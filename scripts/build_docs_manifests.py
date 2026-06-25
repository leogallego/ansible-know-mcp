#!/usr/bin/env python3
"""Build all documentation manifests and write to src/ansible_know/data/."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ansible_know.manifest_builder import (
    build_ansible_core_manifest,
    build_ecosystem_manifest,
    write_manifest,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "ansible_know" / "data"

ECOSYSTEM_PROJECTS = ["lint", "navigator", "builder", "creator", "molecule"]

PROJECT_MANIFEST_NAMES = {
    "ansible": "ansible_core_manifest.json",
    "lint": "ansible_lint_manifest.json",
    "navigator": "ansible_navigator_manifest.json",
    "builder": "ansible_builder_manifest.json",
    "creator": "ansible_creator_manifest.json",
    "molecule": "molecule_manifest.json",
}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger("build_manifests")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Building ansible-core manifest from objects.inv...")
    core_manifest = await build_ansible_core_manifest()
    write_manifest(core_manifest, DATA_DIR / PROJECT_MANIFEST_NAMES["ansible"])
    logger.info("ansible-core: %d entries", len(core_manifest["files"]))

    for project in ECOSYSTEM_PROJECTS:
        logger.info("Building %s manifest from sitemap...", project)
        manifest = await build_ecosystem_manifest(project)
        write_manifest(manifest, DATA_DIR / PROJECT_MANIFEST_NAMES[project])
        logger.info("%s: %d entries", project, len(manifest["files"]))

    logger.info("Done. All manifests written to %s", DATA_DIR)


if __name__ == "__main__":
    asyncio.run(main())
