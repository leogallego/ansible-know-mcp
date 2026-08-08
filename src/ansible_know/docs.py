"""Multi-manifest documentation client.

Manages a registry of documentation manifest sources, loads from local
files (shipped with the package) or HTTP URLs (user overrides), caches
per-source, and provides cross-source search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from itertools import zip_longest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from ansible_know.async_utils import optional_http_client, run_in_executor
from ansible_know.cache import BoundedCache
from ansible_know.config import (
    CACHE_DIR,
    RTD_PROJECT_SLUGS,
    SEARCH_DOCS_LIMIT,
    USER_AGENT,
    get_doc_sources,
    get_rtd_api_token,
)
from ansible_know.errors import AnsibleKnowError
from ansible_know.text_utils import clean_rtd_markdown, estimate_tokens, html_to_markdown
from ansible_know.types import FetchDocResult, SearchDocsEntry
from ansible_know.validation import sanitize_error, truncate_response

logger = logging.getLogger("ansible_know")

__all__ = [
    "clear_cache",
    "fetch_doc_content",
    "fetch_rtd_markdown",
    "parse_rtd_markdown_response",
    "search_docs",
]

MAX_MANIFEST_SIZE = 5_000_000  # 5MB
CACHE_TTL_SECONDS = 3600
MANIFEST_VERSION_MAJOR = "2"
RTD_SEARCH_URL = "https://app.readthedocs.org/api/v3/search/"
RTD_EMBED_URL = "https://app.readthedocs.org/api/v3/embed/"
RTD_DOCS_DOMAIN = "https://docs.ansible.com"
RTD_EMBED_DOCS_HOST = "ansible.readthedocs.io"
CF_CHALLENGE_MARKER = "Cloudflare managed challenge"

_manifest_cache: BoundedCache[str, list[dict[str, Any]]] = BoundedCache(
    max_size=50, ttl=CACHE_TTL_SECONDS,
    path=CACHE_DIR / "doc-manifests.json",
)

PAGE_CACHE_TTL = 86400
PAGE_CACHE_MAX = 100
DOC_RATE_LIMIT_INTERVAL = 1.0
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_page_cache: BoundedCache[str, FetchDocResult] = BoundedCache(
    max_size=PAGE_CACHE_MAX, ttl=PAGE_CACHE_TTL,
    path=CACHE_DIR / "doc-pages.json",
)

_doc_throttle_lock: asyncio.Lock | None = None
_doc_last_request: float = 0.0


async def _throttle_doc_request() -> None:
    """Enforce minimum interval between docs.ansible.com requests."""
    global _doc_last_request, _doc_throttle_lock
    if _doc_throttle_lock is None:
        _doc_throttle_lock = asyncio.Lock()
    async with _doc_throttle_lock:
        now = time.monotonic()
        elapsed = now - _doc_last_request
        if _doc_last_request > 0 and elapsed < DOC_RATE_LIMIT_INTERVAL:
            await asyncio.sleep(DOC_RATE_LIMIT_INTERVAL - elapsed)
        _doc_last_request = time.monotonic()


def _is_cf_challenge(resp: httpx.Response) -> bool:
    """Detect Cloudflare managed challenge responses."""
    return "challenge" in resp.headers.get("cf-mitigated", "").lower()


def _parse_retry_after(resp: httpx.Response, attempt: int) -> float:
    """Extract retry delay from Retry-After header or use exponential backoff."""
    header = resp.headers.get("retry-after", "")
    if header:
        try:
            delay = float(header)
            if math.isfinite(delay):
                return max(0.0, min(delay, 30.0))
        except ValueError:
            pass
    return min(RETRY_BACKOFF_BASE ** attempt, 30.0)


def _postprocess_entries(
    entries: list[dict[str, Any]], source_name: str, base_url: str,
) -> list[dict[str, Any]]:
    """Add _source tag, construct URLs, and precompute lowercase fields."""
    for entry in entries:
        entry["_source"] = source_name
        if "url" not in entry and "path" in entry and base_url:
            entry["url"] = f"{base_url.rstrip('/')}/{entry['path'].strip().lstrip('/')}"
        topics = entry.get("topics", entry.get("topic", []))
        if isinstance(topics, str):
            topics = [topics]
        entry["_topics"] = topics
        aud = entry.get("audience", [])
        if isinstance(aud, str):
            aud = [aud]
        entry["_audience"] = aud
        entry["_searchable"] = "{} {} {}".format(
            entry.get("title", "").lower(),
            entry.get("summary", "").lower(),
            " ".join(t.lower() for t in topics),
        )
    return entries


def _check_manifest_version(data: Any, source_name: str) -> None:
    """Log warning if manifest version is unrecognized."""
    version = data.get("version", "1.0") if isinstance(data, dict) else "1.0"
    if not version.startswith(f"{MANIFEST_VERSION_MAJOR}."):
        logger.warning(
            "Manifest '%s' has version %s (expected %s.x) — some fields may be unrecognized",
            source_name, version, MANIFEST_VERSION_MAJOR,
        )


def _extract_manifest_entries(data: Any) -> tuple[list[dict[str, Any]], str]:
    """Extract entries list and base_url from manifest data."""
    base_url = data.get("base_url", "") if isinstance(data, dict) else ""
    entries = data if isinstance(data, list) else data.get("files", data.get("documents", data.get("entries", [])))
    return entries, base_url


def _load_manifest_file(source_name: str, file_path: str) -> list[dict[str, Any]]:
    """Load manifest from a local JSON file. Returns empty on error."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    try:
        file_size = Path(file_path).stat().st_size
        if file_size > MAX_MANIFEST_SIZE:
            logger.warning(
                "Manifest file too large for '%s': %d bytes (max %d)",
                source_name, file_size, MAX_MANIFEST_SIZE,
            )
            return []
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("Manifest file not found for '%s': %s", source_name, file_path)
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load manifest '%s': %s", source_name, exc)
        return []

    _check_manifest_version(data, source_name)
    entries, base_url = _extract_manifest_entries(data)
    entries = _postprocess_entries(entries, source_name, base_url)

    _manifest_cache.put(source_name, entries)
    return entries


async def _fetch_manifest_url(
    source_name: str,
    url: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch manifest from a URL. Raises on HTTP/size errors."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    async with optional_http_client(
        http_client, timeout=httpx.Timeout(10.0, read=30.0),
    ) as client:
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

    _check_manifest_version(data, source_name)
    entries, base_url = _extract_manifest_entries(data)
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
        return await run_in_executor(
            _load_manifest_file, source_name, src_config["file"],
        )
    if "url" in src_config:
        return await _fetch_manifest_url(source_name, src_config["url"], http_client)
    logger.warning("Doc source '%s' has neither 'file' nor 'url', skipping", source_name)
    return []


async def _search_rtd_api(
    query: str,
    source: str | None = None,
    limit: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> list[SearchDocsEntry]:
    """Search RTD API as fallback when manifest search returns empty."""
    if source and source in RTD_PROJECT_SLUGS:
        slugs_to_search = [(source, RTD_PROJECT_SLUGS[source])]
    elif source:
        return []
    else:
        slugs_to_search = list(RTD_PROJECT_SLUGS.items())

    async with optional_http_client(http_client, timeout=10.0) as client:

        async def _search_one(source_name: str, slug: str) -> list[SearchDocsEntry]:
            params = {
                "q": f"project:{slug}/latest {query}",
                "page_size": min(limit, 20),
            }
            headers = _rtd_api_headers()
            try:
                resp = await client.get(
                    RTD_SEARCH_URL,
                    params=params,
                    headers=headers,
                    timeout=5.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("RTD search for %s failed: %s", slug, exc)
                return []

            hits: list[SearchDocsEntry] = []
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
                    "url": f"{RTD_DOCS_DOMAIN}{path}",
                })
            return hits

        all_hits = await asyncio.gather(
            *[_search_one(name, slug) for name, slug in slugs_to_search],
            return_exceptions=True,
        )
        per_source: list[list[SearchDocsEntry]] = []
        for hits in all_hits:
            if isinstance(hits, list):
                per_source.append(hits)
            elif isinstance(hits, BaseException):
                logger.debug("RTD search failed for one project: %s", hits)
        results: list[SearchDocsEntry] = [
            hit for group in zip_longest(*per_source)
            for hit in group if hit is not None
        ]

    return results[:limit]


async def search_docs(
    query: str,
    source: str | None = None,
    topic: str | None = None,
    audience: str | None = None,
    core_only: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> list[SearchDocsEntry]:
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
    query_words = query_lower.split()
    results: list[SearchDocsEntry] = []
    has_filters = bool(topic or audience or core_only)
    manifest_had_entries = False

    for src_name, src_config in sources.items():
        if source and src_name != source:
            continue

        try:
            entries = await _get_manifest(src_name, src_config, http_client)
        except (httpx.HTTPError, ValueError):
            continue

        if entries:
            manifest_had_entries = True

        for entry in entries:
            if core_only and not entry.get("core", False):
                continue

            entry_topics = entry.get("_topics", entry.get("topics", entry.get("topic", [])))
            if isinstance(entry_topics, str):
                entry_topics = [entry_topics]
            entry_audience = entry.get("_audience", entry.get("audience", []))
            if isinstance(entry_audience, str):
                entry_audience = [entry_audience]

            if topic and topic.lower() not in [t.lower() for t in entry_topics]:
                continue
            if audience and audience.lower() not in [a.lower() for a in entry_audience]:
                continue

            searchable = entry.get("_searchable", "")
            if not searchable:
                searchable = "{} {} {}".format(
                    entry.get("title", "").lower(),
                    entry.get("summary", "").lower(),
                    " ".join(t.lower() for t in entry_topics),
                )

            if all(w in searchable for w in query_words):
                results.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "topic": entry_topics,
                    "audience": entry_audience,
                    "lines": entry.get("lines", 0),
                    "source": src_name,
                    "url": entry.get("url", ""),
                })

            if len(results) >= SEARCH_DOCS_LIMIT:
                break

    if not results and not (has_filters and manifest_had_entries):
        try:
            rtd_results = await _search_rtd_api(
                query, source=source, http_client=http_client,
            )
            results.extend(rtd_results)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.debug("RTD fallback search failed: %s", exc)

    return results[:SEARCH_DOCS_LIMIT]


def clear_cache() -> None:
    """Clear the manifest and page data caches.

    Only clears actual data caches (stale-able content with TTL).
    Does NOT touch transport clients like RedHatDocsClient — that is
    not a cache, it manages its own MCP session lifecycle (auto-reconnect
    on 404 expiry) and lives in SharedState.
    """
    _manifest_cache.clear()
    _page_cache.clear()


MAX_DOC_FETCH_SIZE = 2_000_000  # 2MB


def _map_docs_url_to_rtd(url: str) -> str:
    """Rewrite docs.ansible.com URLs to ansible.readthedocs.io for Embed API.

    Paths are identical across the custom domain and the RTD project domain.
    Host is hard-coded after validation — never taken from redirect targets.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "docs.ansible.com":
        raise AnsibleKnowError(
            "RTD Embed fallback only supports https://docs.ansible.com/ URLs"
        )
    return urlunparse(parsed._replace(netloc=RTD_EMBED_DOCS_HOST))


def _rtd_api_headers() -> dict[str, str]:
    """Build RTD API headers (Embed/Search); optional token increases rate limits."""
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    token = get_rtd_api_token()
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


def _is_embed_fallback_error(
    exc: AnsibleKnowError | httpx.HTTPStatusError,
) -> bool:
    """Return True for CF challenge or persistent HTTP 429 from docs.ansible.com."""
    if isinstance(exc, AnsibleKnowError) and CF_CHALLENGE_MARKER in str(exc):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return response is not None and response.status_code == 429
    return False


async def _fetch_via_rtd_embed(
    client: httpx.AsyncClient,
    url: str,
    max_tokens: int | None = None,
) -> FetchDocResult:
    """Fetch page content via RTD Embed API (bypasses docs.ansible.com Cloudflare)."""
    rtd_url = _map_docs_url_to_rtd(url)
    try:
        resp = await client.get(
            RTD_EMBED_URL,
            params={"url": rtd_url},
            headers=_rtd_api_headers(),
            follow_redirects=False,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise AnsibleKnowError(
            f"RTD Embed API request failed: {sanitize_error(str(exc))}"
        ) from exc

    if resp.url.host != "app.readthedocs.org":
        raise AnsibleKnowError(
            f"RTD Embed redirected to unexpected domain: {resp.url.host}"
        )

    if len(resp.content) > MAX_DOC_FETCH_SIZE:
        raise AnsibleKnowError(
            f"RTD Embed response too large: {len(resp.content)} bytes "
            f"(max {MAX_DOC_FETCH_SIZE})"
        )

    if resp.status_code >= 400:
        detail = ""
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            detail = f": {sanitize_error(str(payload['error']))}"
        raise AnsibleKnowError(
            f"RTD Embed API returned HTTP {resp.status_code}{detail}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise AnsibleKnowError("RTD Embed API returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise AnsibleKnowError("RTD Embed API returned unexpected JSON shape")

    if data.get("error"):
        raise AnsibleKnowError(
            f"RTD Embed API error: {sanitize_error(str(data['error']))}"
        )

    html_content = data.get("content")
    if not isinstance(html_content, str) or not html_content.strip():
        raise AnsibleKnowError("RTD Embed API returned empty content")

    html_bytes = len(html_content.encode("utf-8"))
    if html_bytes > MAX_DOC_FETCH_SIZE:
        raise AnsibleKnowError(
            f"RTD Embed content too large: {html_bytes} bytes "
            f"(max {MAX_DOC_FETCH_SIZE})"
        )

    markdown = html_to_markdown(html_content)
    content, title = clean_rtd_markdown(markdown)
    # Estimate before truncate so max_tokens matches primary-path gating intent.
    tokens = estimate_tokens(content)
    if max_tokens is not None and tokens > max_tokens:
        raise AnsibleKnowError(
            f"Page has {tokens} tokens (max_tokens={max_tokens}). "
            f"Fetch without max_tokens or increase the limit."
        )
    content = truncate_response(content)

    result: FetchDocResult = {
        "content": content,
        "title": title,
        "tokens": tokens,
        "source_url": url,
    }
    return result


def parse_rtd_markdown_response(
    resp: httpx.Response,
    *,
    url: str | None = None,
) -> tuple[str, str, int]:
    """Parse a Cloudflare ``text/markdown`` response.

    Returns:
        ``(content, title, tokens)`` after ``clean_rtd_markdown``.

    Raises:
        AnsibleKnowError: When ``Content-Type`` is not markdown.
    """
    content_type = resp.headers.get("content-type", "")
    if "text/markdown" not in content_type:
        target = url or str(resp.url)
        raise AnsibleKnowError(
            f"Expected text/markdown but got {content_type!r} for {target}"
        )

    tokens_str = resp.headers.get("x-markdown-tokens", "0")
    try:
        tokens = int(tokens_str)
    except ValueError:
        tokens = 0

    content, title = clean_rtd_markdown(resp.text)
    return content, title, tokens


async def fetch_rtd_markdown(
    url: str,
    client: httpx.AsyncClient,
) -> tuple[str, str, int]:
    """Fetch a docs page via Cloudflare markdown content negotiation.

    Shared pipeline for runtime ``fetch_doc_content`` callers that do not
    need retry/Embed fallback, and for build-time
    ``manifest_builder._fetch_page_metadata``.

    Returns:
        ``(content, title, tokens)``.
    """
    resp = await client.get(
        url,
        headers={"Accept": "text/markdown"},
        follow_redirects=True,
        timeout=30.0,
    )
    resp.raise_for_status()
    return parse_rtd_markdown_response(resp, url=url)


async def fetch_doc_content(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult:
    """Fetch a docs.ansible.com page as clean markdown.

    Uses Cloudflare's ``Accept: text/markdown`` content negotiation.
    On Cloudflare managed challenge or persistent HTTP 429, falls back to
    the Read the Docs Embed API (different domain, bypasses the CF zone).

    Args:
        url: Full docs.ansible.com URL (caller must validate first).
        max_tokens: If set, raise when page exceeds this token count.
            Primary path uses Cloudflare ``x-markdown-tokens``; Embed
            fallback estimates ``len(content) // 4`` (no token header).
        http_client: Optional shared httpx client.

    Returns:
        FetchDocResult on success.

    Raises:
        httpx.HTTPError: On HTTP request failure after retries (non-fallback).
        AnsibleKnowError: On CF+Embed failure, content-type mismatch,
            size/token limit, or redirect to unexpected domain.
    """
    cached = _page_cache.get(url)
    if cached is not None:
        if max_tokens is not None and cached.get("tokens", 0) > max_tokens:
            raise AnsibleKnowError(
                f"Page has {cached['tokens']} tokens (max_tokens={max_tokens}). "
                f"Fetch without max_tokens or increase the limit."
            )
        return cached

    async with optional_http_client(
        http_client, timeout=httpx.Timeout(30.0),
    ) as client:
        try:
            resp = await _fetch_with_retry(client, url)
        except (AnsibleKnowError, httpx.HTTPStatusError) as primary_exc:
            if not _is_embed_fallback_error(primary_exc):
                raise
            logger.info(
                "docs.ansible.com blocked (%s); trying RTD Embed fallback",
                type(primary_exc).__name__,
            )
            try:
                result = await _fetch_via_rtd_embed(
                    client, url, max_tokens=max_tokens,
                )
            except AnsibleKnowError as embed_exc:
                raise AnsibleKnowError(
                    f"{sanitize_error(str(primary_exc))} "
                    f"RTD Embed fallback also failed: "
                    f"{sanitize_error(str(embed_exc))}"
                ) from embed_exc
            _page_cache.put(url, result)
            return result

        if resp.url.host != "docs.ansible.com":
            raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")

        if len(resp.content) > MAX_DOC_FETCH_SIZE:
            raise AnsibleKnowError(
                f"Response too large: {len(resp.content)} bytes "
                f"(max {MAX_DOC_FETCH_SIZE})"
            )

        content, title, tokens = parse_rtd_markdown_response(resp, url=url)

        if max_tokens is not None and tokens > max_tokens:
            raise AnsibleKnowError(
                f"Page has {tokens} tokens (max_tokens={max_tokens}). "
                f"Fetch without max_tokens or increase the limit."
            )

        content = truncate_response(content)

        result: FetchDocResult = {
            "content": content,
            "title": title,
            "tokens": tokens,
            "source_url": str(resp.url),
        }
        _page_cache.put(url, result)
        return result


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str,
) -> httpx.Response:
    """Fetch a URL with rate limiting, retry, and CF challenge detection."""
    for attempt in range(MAX_RETRY_ATTEMPTS):
        await _throttle_doc_request()

        try:
            resp = await client.get(
                url,
                headers={"Accept": "text/markdown", "User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=30.0,
            )
        except httpx.TransportError as exc:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                delay = min(RETRY_BACKOFF_BASE ** attempt, 30.0)
                logger.debug(
                    "fetch_doc attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1, MAX_RETRY_ATTEMPTS, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
                continue
            raise

        if _is_cf_challenge(resp):
            raise AnsibleKnowError(
                "docs.ansible.com returned a Cloudflare managed challenge "
                "(bot detection). This is transient — try again later. "
                "Use search_docs for local results that don't require network access."
            )

        if resp.status_code in RETRYABLE_STATUS_CODES:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                delay = _parse_retry_after(resp, attempt)
                logger.debug(
                    "fetch_doc attempt %d/%d got HTTP %d, retrying in %.1fs",
                    attempt + 1, MAX_RETRY_ATTEMPTS, resp.status_code, delay,
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()

        resp.raise_for_status()
        return resp

    raise RuntimeError("unreachable")
