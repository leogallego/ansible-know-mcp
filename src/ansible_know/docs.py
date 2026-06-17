"""Multi-manifest documentation client.

Manages a registry of documentation manifest sources (e.g., ansible-core ai-docs),
fetches them via HTTP, caches per-source, and provides cross-source search.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ansible_know.cache import BoundedCache
from ansible_know.config import SEARCH_DOCS_LIMIT, get_doc_sources

logger = logging.getLogger("ansible_know")

MAX_MANIFEST_SIZE = 5_000_000  # 5MB

_manifest_cache: BoundedCache[str, list[dict[str, Any]]] = BoundedCache(max_size=50)


async def _fetch_manifest(source_name: str, url: str) -> list[dict[str, Any]]:
    """Fetch and cache a manifest from a URL."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
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

    base_url = data.get("base_url", "") if isinstance(data, dict) else ""
    entries = data if isinstance(data, list) else data.get("files", data.get("documents", data.get("entries", [])))

    for entry in entries:
        entry["_source"] = source_name
        if "url" not in entry and "path" in entry and base_url:
            entry["url"] = f"{base_url.rstrip('/')}/{entry['path'].lstrip('/')}"

    _manifest_cache.put(source_name, entries)
    return entries


async def search_docs(
    query: str,
    source: str | None = None,
    topic: str | None = None,
    audience: str | None = None,
    core_only: bool = False,
) -> list[dict[str, Any]]:
    """Search documentation manifests for conceptual guides.

    Args:
        query: Search term (matched against title, summary, topics).
        source: Filter to a single source name (e.g. "ansible-core").
        topic: Filter by topic tag.
        audience: Filter by audience tag.
        core_only: If True, only return entries marked as core.

    Returns:
        Up to SEARCH_DOCS_LIMIT matching entries with source info.
    """
    sources = get_doc_sources()
    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    for src_name, src_config in sources.items():
        if source and src_name != source:
            continue

        if "url" not in src_config:
            logger.warning("Doc source '%s' missing 'url', skipping", src_name)
            continue

        try:
            entries = await _fetch_manifest(src_name, src_config["url"])
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
    """Clear the manifest cache (useful for testing)."""
    _manifest_cache.clear()

