"""Multi-manifest documentation client.

Manages a registry of documentation manifest sources, loads from local
files (shipped with the package) or HTTP URLs (user overrides), caches
per-source, and provides cross-source search.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ansible_know.cache import BoundedCache
from ansible_know.config import SEARCH_DOCS_LIMIT, get_doc_sources

logger = logging.getLogger("ansible_know")

__all__ = [
    "clear_cache",
    "search_docs",
]

MAX_MANIFEST_SIZE = 5_000_000  # 5MB
CACHE_TTL_SECONDS = 3600
MANIFEST_VERSION_MAJOR = "2"

_manifest_cache: BoundedCache[str, list[dict[str, Any]]] = BoundedCache(
    max_size=50, ttl=CACHE_TTL_SECONDS,
)


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

    return results[:SEARCH_DOCS_LIMIT]


def clear_cache() -> None:
    """Clear the manifest cache."""
    _manifest_cache.clear()
