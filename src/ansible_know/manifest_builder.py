"""Build documentation manifests from objects.inv and sitemap sources.

This module is used at build time (CI) to generate the JSON manifest
files shipped in src/ansible_know/data/. It is not used at runtime.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ansible_know.config import (
    AUDIENCE_MAP,
    CORE_PAGES,
    GUIDE_TOPIC_PREFIXES,
    PROJECT_BASE_URLS,
)
from ansible_know.docs import fetch_rtd_markdown
from ansible_know.errors import AnsibleKnowError

logger = logging.getLogger("ansible_know.builder")

__all__ = [
    "build_ansible_core_manifest",
    "build_ecosystem_manifest",
    "fetch_objects_inv",
    "fetch_sitemap_urls",
    "filter_guide_pages",
    "write_manifest",
]

MANIFEST_VERSION = "2.0"


def filter_guide_pages(
    entries: list[dict[str, str]],
    topic_prefixes: set[str],
) -> list[dict[str, str]]:
    """Keep only entries whose first path segment matches a guide topic prefix."""
    result = []
    for entry in entries:
        name = entry.get("name", "")
        if "/" not in name:
            continue
        first_segment = name.split("/")[0]
        if first_segment in topic_prefixes:
            result.append(entry)
    return result


async def fetch_objects_inv(url: str) -> list[dict[str, str]]:
    """Download and parse objects.inv, returning std:doc entries."""
    import sphobjinv as soi

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    inv = soi.Inventory(plaintext=soi.decompress(resp.content))
    entries = []
    for obj in inv.objects:
        if obj.domain == "std" and obj.role == "doc":
            entries.append({
                "name": obj.name,
                "display_name": obj.dispname if obj.dispname != "-" else obj.name,
                "uri": obj.uri,
            })
    return entries


async def fetch_sitemap_urls(
    sitemap_url: str,
    project_prefix: str,
) -> list[str]:
    """Extract URLs from sitemap XML matching a project prefix."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

    import defusedxml.ElementTree as ET

    root = ET.fromstring(resp.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        if loc.text:
            loc_path = urlparse(loc.text).path
            if loc_path == project_prefix or loc_path.startswith(project_prefix + "/"):
                urls.append(loc.text)
    return urls


async def _fetch_page_metadata(
    url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Fetch a page via markdown endpoint and extract metadata."""
    try:
        content, title, tokens = await fetch_rtd_markdown(url, client)
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return {}
    except AnsibleKnowError as exc:
        logger.warning("Non-markdown response for %s: %s", url, exc)
        return {}

    lines = content.count("\n") + 1 if content else 0

    summary = ""
    first_para_start = content.find("\n\n")
    if first_para_start >= 0:
        first_para = content[first_para_start:].strip()
    else:
        first_newline = content.find("\n")
        first_para = content[first_newline + 1:].strip() if first_newline >= 0 else content.strip()
    if first_para:
        dot = first_para.find(". ")
        summary = (first_para[: dot + 1] if dot > 0 else first_para[:200]).strip()

    return {"title": title, "summary": summary, "lines": lines, "tokens": tokens}


async def build_ansible_core_manifest() -> dict[str, Any]:
    """Build the ansible-core manifest from objects.inv."""
    base_url = PROJECT_BASE_URLS["ansible"]
    inv_url = f"{base_url}/objects.inv"

    raw_entries = await fetch_objects_inv(inv_url)
    guide_entries = filter_guide_pages(raw_entries, GUIDE_TOPIC_PREFIXES)

    core_set = set(CORE_PAGES.get("ansible", []))
    files: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for entry in guide_entries:
            name = entry["name"]
            path = f"{name}.html"
            topic = name.split("/")[0]

            file_entry: dict[str, Any] = {
                "path": path,
                "topic": topic,
                "title": entry["display_name"],
                "audience": AUDIENCE_MAP.get(topic, "both"),
                "core": path in core_set,
                "summary": "",
                "lines": 0,
                "tokens": 0,
            }

            if path in core_set:
                url = f"{base_url}/{path}"
                meta = await _fetch_page_metadata(url, client)
                if meta:
                    file_entry.update(meta)

            files.append(file_entry)

    return {
        "version": MANIFEST_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "files": files,
    }


async def build_ecosystem_manifest(
    project_key: str,
    sitemap_url: str = "https://docs.ansible.com/ansible-sitemap.xml",
) -> dict[str, Any]:
    """Build a manifest for an ecosystem project from sitemap + markdown fetch."""
    base_url = PROJECT_BASE_URLS[project_key]
    parsed = urlparse(base_url)
    prefix = parsed.path

    all_urls = await fetch_sitemap_urls(sitemap_url, prefix)
    core_set = set(CORE_PAGES.get(project_key, []))
    files: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for url in all_urls:
            parsed_url = urlparse(url)
            path = parsed_url.path
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            path = path.lstrip("/")

            meta = await _fetch_page_metadata(url, client)
            title = meta.get("title", "")
            if not title:
                title = path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()

            topic = path.split("/")[0] if "/" in path else "overview"

            files.append({
                "path": path,
                "topic": topic,
                "title": title,
                "audience": "author",
                "core": path in core_set or (path == "" and "" in core_set),
                "summary": meta.get("summary", ""),
                "lines": meta.get("lines", 0),
                "tokens": meta.get("tokens", 0),
            })

    return {
        "version": MANIFEST_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "files": files,
    }


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write a manifest dict to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    logger.info("Wrote manifest: %s (%d entries)", output_path, len(manifest.get("files", [])))
