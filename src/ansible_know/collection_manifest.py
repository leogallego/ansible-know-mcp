"""Collection manifest generation and caching.

Generates MANIFEST.json files per collection with per-module summaries
including parameter counts, required params, API detection, and tags.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ansible_know.config import SKILLS_DIR


def _derive_tags(fqcn: str, params: list[dict[str, Any]]) -> list[str]:
    """Heuristically derive tags from module name segments and parameters."""
    parts = fqcn.split(".")
    module_short = parts[-1] if parts else fqcn

    tags: set[str] = set()
    tag_hints = {
        "user": "identity", "group": "identity", "role": "identity",
        "network": "networking", "interface": "networking", "vlan": "networking",
        "firewall": "security", "acl": "security", "cert": "security",
        "file": "files", "copy": "files", "template": "files",
        "package": "packages", "apt": "packages", "yum": "packages", "dnf": "packages",
        "service": "services", "systemd": "services",
        "docker": "containers", "podman": "containers", "container": "containers",
        "ip": "ipam", "prefix": "ipam", "subnet": "ipam", "address": "ipam",
        "device": "dcim", "rack": "dcim", "site": "dcim",
        "vm": "virtualization", "virtual": "virtualization",
        "cloud": "cloud", "ec2": "cloud", "azure": "cloud", "gcp": "cloud",
        "db": "database", "database": "database", "mysql": "database", "postgres": "database",
    }

    for segment in module_short.split("_"):
        segment_lower = segment.lower()
        if segment_lower in tag_hints:
            tags.add(tag_hints[segment_lower])

    return sorted(tags)


def generate_manifest(
    collection_namespace: str,
    modules_metadata: list[dict[str, Any]],
    roles_metadata: list[dict[str, Any]] | None = None,
    skills_dir: Path | None = None,
    collection_version: str | None = None,
) -> dict[str, Any]:
    """Generate a collection manifest from module and role metadata.

    Args:
        collection_namespace: e.g. "netbox.netbox"
        modules_metadata: list of extract_module_metadata() results
        roles_metadata: list of role metadata dicts (optional)
        skills_dir: where to check for existing skills and write manifest
        collection_version: installed version to store for cache invalidation

    Returns:
        The manifest dict.
    """
    if skills_dir is None:
        skills_dir = SKILLS_DIR

    modules_list = []
    for meta in modules_metadata:
        fqcn = meta["module_name"]
        params = meta["params"]
        required_params = [p["name"] for p in params if p.get("required")]
        skill_dir = skills_dir / fqcn
        has_skill = (skill_dir / "SKILL.md").exists()

        modules_list.append({
            "fqcn": fqcn,
            "description": meta["short_description"],
            "param_count": len(params),
            "required_params": required_params,
            "is_api_module": meta["is_api_module"],
            "has_skill": has_skill,
            "tags": _derive_tags(fqcn, params),
        })

    roles_list = []
    for role_meta in (roles_metadata or []):
        fqcn = role_meta["fqcn"]
        skill_dir = skills_dir / fqcn
        has_skill = (skill_dir / "SKILL.md").exists()

        roles_list.append({
            "fqcn": fqcn,
            "description": role_meta.get("description", ""),
            "has_argument_specs": role_meta.get("has_argument_specs", False),
            "entry_points": role_meta.get("entry_points", ["main"]),
            "has_skill": has_skill,
        })

    manifest = {
        "collection": collection_namespace,
        "collection_version": collection_version,
        "generated": datetime.now(timezone.utc).isoformat(),
        "module_count": len(modules_list),
        "role_count": len(roles_list),
        "modules": modules_list,
        "roles": roles_list,
    }

    manifest_dir = skills_dir / collection_namespace.replace(".", "/")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return manifest


def load_cached_manifest(
    collection_namespace: str,
    skills_dir: Path | None = None,
    installed_version: str | None = None,
) -> dict[str, Any] | None:
    """Load a cached MANIFEST.json if it exists and is still valid.

    When installed_version is provided, the cache is invalidated if the
    stored collection_version doesn't match.
    """
    if skills_dir is None:
        skills_dir = SKILLS_DIR

    manifest_path = skills_dir / collection_namespace.replace(".", "/") / "MANIFEST.json"
    if not manifest_path.exists():
        return None

    manifest = json.loads(manifest_path.read_text())
    if installed_version and manifest.get("collection_version") != installed_version:
        return None
    return manifest
