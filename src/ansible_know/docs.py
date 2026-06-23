"""Multi-manifest documentation client.

Manages a registry of documentation manifest sources, loads from local
files (shipped with the package) or HTTP URLs (user overrides), caches
per-source, and provides cross-source search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from ansible_know.cache import BoundedCache
from ansible_know.config import RTD_PROJECT_SLUGS, SEARCH_DOCS_LIMIT, get_doc_sources
from ansible_know.errors import ValidationError
from ansible_know.types import ErrorResponse, FetchDocResult
from ansible_know.validation import sanitize_error, truncate_response, validate_doc_url

logger = logging.getLogger("ansible_know")

__all__ = [
    "clean_rtd_markdown",
    "clear_cache",
    "fetch_doc_content",
    "search_docs",
]

MAX_MANIFEST_SIZE = 5_000_000  # 5MB
CACHE_TTL_SECONDS = 3600
MANIFEST_VERSION_MAJOR = "2"
RTD_SEARCH_URL = "https://app.readthedocs.org/api/v3/search/"
RTD_DOCS_DOMAIN = "https://docs.ansible.com"

_manifest_cache: BoundedCache[str, list[dict[str, Any]]] = BoundedCache(
    max_size=50, ttl=CACHE_TTL_SECONDS,
)

_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+html>", re.IGNORECASE)
_H1_RE = re.compile(r"^# (.+?)(?:\s*\{#[\w-]+\})?\s*$", re.MULTILINE)
_EXCESS_BLANKS_RE = re.compile(r"\n{3,}")


def _postprocess_entries(
    entries: list[dict[str, Any]], source_name: str, base_url: str,
) -> list[dict[str, Any]]:
    """Add _source tag and construct URLs from base_url + path."""
    for entry in entries:
        entry["_source"] = source_name
        if "url" not in entry and "path" in entry and base_url:
            entry["url"] = f"{base_url.rstrip('/')}/{entry['path'].lstrip('/')}"
    return entries


def _load_manifest_file(source_name: str, file_path: str) -> list[dict[str, Any]]:
    """Load manifest from a local JSON file. Returns empty on error."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    try:
        with open(file_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("Manifest file not found for '%s': %s", source_name, file_path)
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load manifest '%s': %s", source_name, exc)
        return []

    version = data.get("version", "1.0") if isinstance(data, dict) else "1.0"
    if not version.startswith(f"{MANIFEST_VERSION_MAJOR}."):
        logger.warning(
            "Manifest '%s' has version %s (expected %s.x) — some fields may be unrecognized",
            source_name, version, MANIFEST_VERSION_MAJOR,
        )

    base_url = data.get("base_url", "") if isinstance(data, dict) else ""
    entries = data if isinstance(data, list) else data.get("files", data.get("documents", data.get("entries", [])))
    entries = _postprocess_entries(entries, source_name, base_url)

    _manifest_cache.put(source_name, entries)
    return entries


async def _fetch_manifest_url(
    source_name: str,
    url: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch manifest from a URL. Returns empty on error."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))
        should_close = True

    try:
        resp = await client.get(url)
        resp.raise_for_status()
        content_length = resp.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = None
            if size is not None and size > MAX_MANIFEST_SIZE:
                raise ValueError(f"Manifest too large: {content_length} bytes (max {MAX_MANIFEST_SIZE})")
        if len(resp.content) > MAX_MANIFEST_SIZE:
            raise ValueError(f"Manifest too large: {len(resp.content)} bytes (max {MAX_MANIFEST_SIZE})")
        data = resp.json()
    finally:
        if should_close:
            await client.aclose()

    version = data.get("version", "1.0") if isinstance(data, dict) else "1.0"
    if not version.startswith(f"{MANIFEST_VERSION_MAJOR}."):
        logger.warning(
            "Manifest '%s' has version %s (expected %s.x) — some fields may be unrecognized",
            source_name, version, MANIFEST_VERSION_MAJOR,
        )

    base_url = data.get("base_url", "") if isinstance(data, dict) else ""
    entries = data if isinstance(data, list) else data.get("files", data.get("documents", data.get("entries", [])))
    entries = _postprocess_entries(entries, source_name, base_url)

    _manifest_cache.put(source_name, entries)
    return entries


async def _get_manifest(
    source_name: str,
    src_config: dict[str, str],
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Load manifest from file or URL based on source config."""
    if "file" in src_config:
        return _load_manifest_file(source_name, src_config["file"])
    if "url" in src_config:
        return await _fetch_manifest_url(source_name, src_config["url"], http_client)
    logger.warning("Doc source '%s' has neither 'file' nor 'url', skipping", source_name)
    return []


async def _search_rtd_api(
    query: str,
    source: str | None = None,
    limit: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search RTD API as fallback when manifest search returns empty."""
    slugs_to_search: list[tuple[str, str]] = []
    if source and source in RTD_PROJECT_SLUGS:
        slugs_to_search = [(source, RTD_PROJECT_SLUGS[source])]
    else:
        slugs_to_search = list(RTD_PROJECT_SLUGS.items())

    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        should_close = True

    async def _search_one(source_name: str, slug: str) -> list[dict[str, Any]]:
        params = {
            "q": f"project:{slug}/latest {query}",
            "page_size": min(limit, 20),
        }
        try:
            resp = await client.get(RTD_SEARCH_URL, params=params, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        hits: list[dict[str, Any]] = []
        for hit in data.get("results", []):
            blocks = hit.get("blocks", [])
            summary = ""
            if blocks:
                raw = blocks[0].get("content", "")
                dot = raw.find(". ")
                summary = (raw[: dot + 1] if dot > 0 else raw[:120]).strip()

            path = hit.get("path", "")
            hits.append({
                "title": hit.get("title", ""),
                "summary": summary,
                "topic": [],
                "audience": [],
                "lines": 0,
                "source": f"rtd-search:{source_name}",
                "url": f"{hit.get('domain', RTD_DOCS_DOMAIN)}{path}",
            })
        return hits

    try:
        all_hits = await asyncio.gather(
            *[_search_one(name, slug) for name, slug in slugs_to_search],
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        for hits in all_hits:
            if isinstance(hits, list):
                results.extend(hits)
    finally:
        if should_close:
            await client.aclose()

    return results[:limit]


async def search_docs(
    query: str,
    source: str | None = None,
    topic: str | None = None,
    audience: str | None = None,
    core_only: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search documentation manifests for conceptual guides.

    Args:
        query: Search term (matched against title, summary, topics).
        source: Filter to a single source name (e.g. "ansible-core").
        topic: Filter by topic tag.
        audience: Filter by audience tag.
        core_only: If True, only return entries marked as core.
        http_client: Optional shared httpx client for URL-based sources.

    Returns:
        Up to SEARCH_DOCS_LIMIT matching entries with source info.
    """
    sources = get_doc_sources()
    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    for src_name, src_config in sources.items():
        if source and src_name != source:
            continue

        try:
            entries = await _get_manifest(src_name, src_config, http_client)
        except (httpx.HTTPError, ValueError):
            continue

        for entry in entries:
            if core_only and not entry.get("core", False):
                continue

            entry_topics = entry.get("topics", entry.get("topic", []))
            if isinstance(entry_topics, str):
                entry_topics = [entry_topics]
            entry_audience = entry.get("audience", [])
            if isinstance(entry_audience, str):
                entry_audience = [entry_audience]

            if topic and topic.lower() not in [t.lower() for t in entry_topics]:
                continue
            if audience and audience.lower() not in [a.lower() for a in entry_audience]:
                continue

            title = entry.get("title", "").lower()
            summary = entry.get("summary", "").lower()
            topics_str = " ".join(t.lower() for t in entry_topics)
            searchable = f"{title} {summary} {topics_str}"

            if query_lower in searchable:
                result = {
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "topic": entry_topics,
                    "audience": entry_audience,
                    "lines": entry.get("lines", 0),
                    "source": src_name,
                    "url": entry.get("url", ""),
                }
                results.append(result)

            if len(results) >= SEARCH_DOCS_LIMIT:
                break

    # RTD Search API fallback when manifest search returns empty
    if not results:
        try:
            rtd_results = await _search_rtd_api(
                query, source=source, http_client=http_client,
            )
            results.extend(rtd_results)
        except Exception:
            pass
    else:
        # Deduplicate: if both manifest and RTD would return same URLs
        pass

    return results[:SEARCH_DOCS_LIMIT]


def clear_cache() -> None:
    """Clear the manifest cache."""
    _manifest_cache.clear()


def clean_rtd_markdown(raw: str) -> tuple[str, str]:
    """Clean RTD markdown output and extract title.

    Returns (cleaned_content, title). Title is empty string if no H1 found.
    """
    if not raw:
        return "", ""

    lines = raw.split("\n")
    for i, line in enumerate(lines[:5]):
        if _DOCTYPE_RE.search(line):
            lines[i] = ""
            break

    text = "\n".join(lines)

    match = _H1_RE.search(text)
    if match:
        text = text[match.start():]
        title = match.group(1).strip()
    else:
        title = ""

    text = _EXCESS_BLANKS_RE.sub("\n\n", text)
    return text.strip(), title


async def fetch_doc_content(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult | ErrorResponse:
    """Fetch a docs.ansible.com page as clean markdown.

    Args:
        url: Full docs.ansible.com URL.
        max_tokens: If set, return error when page exceeds this token count.
        http_client: Optional shared httpx client.

    Returns:
        FetchDocResult on success, ErrorResponse on failure.
    """
    try:
        validate_doc_url(url)
    except ValidationError as exc:
        return {"error": str(exc)}

    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        should_close = True

    try:
        resp = await client.get(
            url,
            headers={"Accept": "text/markdown"},
            follow_redirects=True,
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"error": sanitize_error(f"Failed to fetch {url}: {exc}")}
    finally:
        if should_close:
            await client.aclose()

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" not in content_type:
        return {"error": sanitize_error(f"Expected text/markdown but got {content_type!r} for {url}")}

    tokens_str = resp.headers.get("x-markdown-tokens", "0")
    try:
        tokens = int(tokens_str)
    except ValueError:
        tokens = 0

    if max_tokens is not None and tokens > max_tokens:
        return {
            "error": f"Page has {tokens} tokens (max_tokens={max_tokens}). "
            f"Fetch without max_tokens or increase the limit.",
        }

    content, title = clean_rtd_markdown(resp.text)
    content = truncate_response(content)

    return {
        "content": content,
        "title": title,
        "tokens": tokens,
        "source_url": str(resp.url),
    }
