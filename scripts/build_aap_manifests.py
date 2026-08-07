"""Build AAP documentation manifests from the Red Hat Documentation MCP server.

Fetches each AAP version's landing page via the MCP server's
redhat_docs_fetch tool, extracts the structured guide list from
categoryTitles JSON, and outputs v2.0 manifest JSON files.

Manifest ``url`` values are HTTP-canonical for clients (docs.redhat.com):
AAP 2.6/2.7 prefer bare modular slugs (``/{version}/{slug}``) after HTTP
verification; AAP 2.5 keeps MCP ``/html/`` or ``/html-single/`` paths.
The runtime ``fetch_doc`` HTTP fallback / ``/html/`` rewrite remains a
safety net when MCP rejects modular slugs.

Usage:
    .venv/bin/python scripts/build_aap_manifests.py

Outputs to src/ansible_know/data/aap_{25,26,27}_manifest.json.
Requires network access to docs-mcp.api.redhat.com and docs.redhat.com.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ansible_know.config import USER_AGENT
from ansible_know.redhat_docs import (
    RedHatDocsClient,
    _alternate_redhat_doc_url,
    _html_looks_soft_404,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MANIFEST_VERSION = "2.0"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "ansible_know" / "data"

AAP_BASE_URL = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform"

AAP_VERSIONS = {
    "2.5": {"landing": f"{AAP_BASE_URL}/2.5", "output": "aap_25_manifest.json"},
    "2.6": {"landing": f"{AAP_BASE_URL}/2.6", "output": "aap_26_manifest.json"},
    "2.7": {"landing": f"{AAP_BASE_URL}/2.7", "output": "aap_27_manifest.json"},
}

# Versions that serve modular guides at bare ``/{version}/{slug}`` URLs.
_BARE_SLUG_VERSIONS = frozenset({"2.6", "2.7"})

CATEGORY_TOPIC_MAP = {
    "what's new": "whats_new",
    "technology preview": "tech_preview",
    "get started": "get_started",
    "plan": "plan",
    "install": "install",
    "extend": "extend",
    "upgrade": "upgrade",
    "migrate": "migrate",
    "secure": "security",
    "administer": "admin",
    "develop": "develop",
    "configure": "configure",
    "integrate": "integrate",
    "observe": "observability",
    "optimize": "performance",
    "troubleshoot": "troubleshoot",
    "reference": "reference",
    "download pdf": "reference",
    "discover": "overview",
}

CORE_TOPICS = frozenset({
    "install", "configure", "upgrade", "troubleshoot", "get_started",
    "admin", "security", "overview",
})

SKIP_CATEGORIES = frozenset({"download pdf"})

_HTML_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_RH_DOC_HOST = "docs.redhat.com"
_VERIFY_CONCURRENCY = 8
# Significant token length for title≈name overlap (reject mis-redirects).
_TITLE_TOKEN_MIN_LEN = 4
_TITLE_MATCH_RATIO = 0.4


def _parse_landing_json(raw: str) -> dict:
    """Parse landing page MCP response into structured data."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Landing page response is not valid JSON") from None
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        obj = json.loads(obj["result"])
    return obj


def _candidate_urls(url: str, version: str) -> list[str]:
    """Return ordered HTTP URL candidates for a landing-page guide URL.

    2.6/2.7: prefer bare slug (strip one ``/html/`` or ``/html-single/``),
    then the original MCP URL. 2.5: keep the MCP ``/html*`` URL only.
    """
    original = url.strip()
    if version not in _BARE_SLUG_VERSIONS:
        return [original]

    bare = _alternate_redhat_doc_url(original)
    if bare and bare != original:
        return [bare, original]
    return [original]


def _extract_html_title(html: str) -> str:
    """Return the first HTML ``<title>`` text, collapsed whitespace."""
    match = _HTML_TITLE_RE.search(html[:50_000])
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _title_matches_name(title: str, name: str) -> bool:
    """Return True when enough significant name tokens appear in the title.

    Used to reject mis-redirects (seen on some AAP 2.6 ``/html/`` links)
    where HTTP 200 is not enough. Catalog names that simply disagree with
    the page title still soft-accept via the caller.
    """
    if not title or not name:
        return False
    tokens = [
        tok.lower()
        for tok in re.findall(r"[a-zA-Z0-9]+", name)
        if len(tok) >= _TITLE_TOKEN_MIN_LEN
    ]
    if not tokens:
        return True
    title_l = title.lower()
    hits = sum(1 for tok in tokens if tok in title_l)
    return (hits / len(tokens)) >= _TITLE_MATCH_RATIO


def _response_is_usable(resp: httpx.Response) -> bool:
    """True when the response is HTTP 200 on docs.redhat.com and not soft-404."""
    if resp.status_code != 200:
        return False
    if resp.url.host != _RH_DOC_HOST:
        return False
    return not _html_looks_soft_404(resp.text)


async def _verify_url(
    client: httpx.AsyncClient,
    url: str,
    name: str,
) -> tuple[bool, bool]:
    """GET *url*; return ``(usable, title_matches)``."""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=30.0)
    except httpx.HTTPError as exc:
        logger.warning("HTTP verify failed for %s: %s", url, exc)
        return False, False

    if resp.url.host != _RH_DOC_HOST:
        logger.warning(
            "Rejecting redirect off docs.redhat.com for %s -> %s",
            url,
            resp.url,
        )
        return False, False

    if not _response_is_usable(resp):
        return False, False

    title = _extract_html_title(resp.text)
    return True, _title_matches_name(title, name)


async def _resolve_http_canonical_url(
    client: httpx.AsyncClient,
    url: str,
    version: str,
    name: str,
    sem: asyncio.Semaphore,
) -> str:
    """Pick the first verified candidate; prefer title≈name when available."""
    candidates = _candidate_urls(url, version)
    soft_accept: str | None = None

    async with sem:
        for candidate in candidates:
            usable, title_ok = await _verify_url(client, candidate, name)
            if not usable:
                continue
            if title_ok:
                if candidate != url:
                    logger.info(
                        "Canonicalized %s -> %s",
                        url,
                        candidate,
                    )
                return candidate
            if soft_accept is None:
                soft_accept = candidate

    if soft_accept is not None:
        if soft_accept != url:
            logger.info(
                "Canonicalized %s -> %s (title mismatch soft-accept)",
                url,
                soft_accept,
            )
        return soft_accept

    logger.warning("Keeping unverified MCP URL for %s: %s", name or "?", url)
    return url.strip()


async def _canonicalize_entries(
    entries: list[dict],
    version: str,
) -> list[dict]:
    """Resolve each entry URL to an HTTP-canonical form (in place copy)."""
    if version not in _BARE_SLUG_VERSIONS:
        return entries

    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "User-Agent": USER_AGENT,
    }
    sem = asyncio.Semaphore(_VERIFY_CONCURRENCY)
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0)) as client:
        resolved = await asyncio.gather(
            *(
                _resolve_http_canonical_url(
                    client,
                    entry["url"],
                    version,
                    entry.get("title", ""),
                    sem,
                )
                for entry in entries
            )
        )

    out = []
    for entry, url in zip(entries, resolved, strict=True):
        updated = dict(entry)
        updated["url"] = url
        out.append(updated)
    return out


def _build_manifest_entries(landing: dict, version: str) -> list[dict]:
    """Convert landing page categoryTitles to manifest file entries."""
    entries = []
    category_titles = landing.get("categoryTitles", {})

    for category, block in category_titles.items():
        category_lower = category.strip().lower()
        if category_lower in SKIP_CATEGORIES:
            continue

        topic = CATEGORY_TOPIC_MAP.get(category_lower, category_lower.replace(" ", "_"))

        for title_info in block.get("titles", []):
            url = title_info.get("url", "")
            name = title_info.get("name", "").strip()
            description = (title_info.get("description") or "").strip()

            if not url or "/documentation/" not in url:
                continue
            if urlparse(url.strip()).netloc != _RH_DOC_HOST:
                continue

            # URL is refined to HTTP-canonical form in build_one_manifest.
            entries.append({
                "url": url.strip(),
                "topic": topic,
                "title": name,
                "audience": "admin",
                "core": topic in CORE_TOPICS,
                "summary": description if description else name,
                "lines": 0,
                "tokens": 0,
                "aap_version": version,
            })

    return entries


async def build_one_manifest(client: RedHatDocsClient, version: str, config: dict) -> None:
    """Build and write one AAP version manifest."""
    logger.info("Fetching landing page for AAP %s...", version)
    raw = await client.fetch(config["landing"])
    landing = _parse_landing_json(raw)

    product = landing.get("product", "unknown")
    found_version = landing.get("version", version)
    logger.info("Product: %s, version: %s", product, found_version)

    entries = _build_manifest_entries(landing, version)
    logger.info("Found %d guide entries for AAP %s", len(entries), version)

    entries = await _canonicalize_entries(entries, version)
    bare = sum(1 for e in entries if "/html" not in e["url"])
    logger.info(
        "AAP %s URLs: %d bare-slug, %d with /html*",
        version,
        bare,
        len(entries) - bare,
    )

    manifest = {
        "version": MANIFEST_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "base_url": config["landing"],
        "files": entries,
    }

    output_path = OUTPUT_DIR / config["output"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    logger.info("Wrote %s (%d entries)", output_path.name, len(entries))


async def main() -> int:
    client = RedHatDocsClient()
    try:
        for version, config in AAP_VERSIONS.items():
            await build_one_manifest(client, version, config)
    finally:
        await client.close()

    logger.info("Done. Generated %d manifest files.", len(AAP_VERSIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
