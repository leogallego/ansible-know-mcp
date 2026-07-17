"""Build AAP documentation manifests from the Red Hat Documentation MCP server.

Fetches each AAP version's landing page via the MCP server's
redhat_docs_fetch tool, extracts the structured guide list from
categoryTitles JSON, and outputs v2.0 manifest JSON files.

Usage:
    .venv/bin/python scripts/build_aap_manifests.py

Outputs to src/ansible_know/data/aap_{25,26,27}_manifest.json.
Requires network access to docs-mcp.api.redhat.com.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ansible_know.redhat_docs import RedHatDocsClient

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


def _derive_topic_from_slug(slug: str) -> str:
    """Extract topic from a 2.6/2.7 slug prefix (e.g., 'install-proc_...' -> 'install')."""
    if "-" in slug:
        prefix = slug.split("-", 1)[0].lower()
        return CATEGORY_TOPIC_MAP.get(prefix, prefix)
    return "general"


def _parse_landing_json(raw: str) -> dict:
    """Parse landing page MCP response into structured data."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Landing page response is not valid JSON") from None
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        obj = json.loads(obj["result"])
    return obj


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

            path_parts = url.rstrip("/").rsplit("/", 1)
            slug = path_parts[-1] if len(path_parts) > 1 else ""

            entries.append({
                "path": slug,
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
