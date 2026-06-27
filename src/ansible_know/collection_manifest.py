"""Collection manifest generation and caching.

Generates MANIFEST.json files per collection with per-module summaries
including parameter counts, required params, API detection, and tags.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ansible_know.config import SKILLS_DIR
from ansible_know.errors import ValidationError
from ansible_know.tagging import derive_tags
from ansible_know.validation import validate_path_containment

if TYPE_CHECKING:
    from ansible_know.types import ManifestResult, ModuleMetadata, PluginManifestInput, RoleManifestInput

__all__ = [
    "generate_manifest",
    "load_cached_manifest",
    "write_manifest",
]


def generate_manifest(
    collection_namespace: str,
    modules_metadata: list[ModuleMetadata],
    roles_metadata: list[RoleManifestInput] | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
    skills_dir: Path | None = None,
    collection_version: str | None = None,
) -> ManifestResult:
    """Generate a collection manifest from module, role, and plugin metadata.

    Args:
        collection_namespace: e.g. "netbox.netbox"
        modules_metadata: list of extract_module_metadata() results
        roles_metadata: list of role metadata dicts (optional)
        plugins_metadata: list of plugin metadata dicts (optional)
        skills_dir: where to check for existing skill packages
        collection_version: installed version to store for cache invalidation

    Returns:
        The manifest dict.
    """
    from ansible_know.skills import (  # noqa: I001
        collection_skill_name,
        fqcn_to_skill_name,
        plugin_skill_name as _plugin_skill_name,
        role_skill_name as _role_skill_name,
    )

    if skills_dir is None:
        skills_dir = SKILLS_DIR

    collection_dir = skills_dir / collection_skill_name(collection_namespace)

    modules_list = []
    for meta in modules_metadata:
        fqcn = meta["module_name"]
        params = meta["params"]
        required_params = [p["name"] for p in params if p.get("required")]
        has_skill = (collection_dir / fqcn_to_skill_name(fqcn) / "SKILL.md").exists()

        modules_list.append({
            "fqcn": fqcn,
            "description": meta["short_description"],
            "param_count": len(params),
            "required_params": required_params,
            "is_api_module": meta["is_api_module"],
            "has_skill": has_skill,
            "tags": derive_tags(fqcn, params),
        })

    roles_list = []
    for role_meta in (roles_metadata or []):
        fqcn = role_meta["fqcn"]
        has_skill = (collection_dir / _role_skill_name(fqcn) / "SKILL.md").exists()

        roles_list.append({
            "fqcn": fqcn,
            "description": role_meta["description"],
            "has_argument_specs": role_meta["has_argument_specs"],
            "entry_points": role_meta["entry_points"],
            "has_skill": has_skill,
        })

    plugins_list = []
    for plugin_meta in (plugins_metadata or []):
        fqcn = plugin_meta["fqcn"]
        ptype = plugin_meta["plugin_type"]
        skill_path = (collection_dir / _plugin_skill_name(fqcn, ptype) / "SKILL.md").resolve()
        try:
            validate_path_containment(skill_path, skills_dir)
            has_skill = skill_path.exists()
        except (ValidationError, ValueError):
            has_skill = False

        plugins_list.append({
            "fqcn": fqcn,
            "plugin_type": ptype,
            "description": plugin_meta["description"],
            "param_count": plugin_meta["param_count"],
            "has_skill": has_skill,
        })

    has_collection_skill = (collection_dir / "SKILL.md").exists()

    manifest = {
        "collection": collection_namespace,
        "collection_version": collection_version,
        "generated": datetime.now(timezone.utc).isoformat(),
        "module_count": len(modules_list),
        "role_count": len(roles_list),
        "plugin_count": len(plugins_list),
        "has_collection_skill": has_collection_skill,
        "modules": modules_list,
        "roles": roles_list,
        "plugins": plugins_list,
    }

    return manifest


def write_manifest(
    manifest: dict[str, Any],
    collection_namespace: str,
    skills_dir: Path | None = None,
) -> None:
    """Persist a manifest dict to MANIFEST.json on disk.

    Creates the collection directory if it does not exist.
    Defaults to ``SKILLS_DIR`` when ``skills_dir`` is not provided.

    Raises:
        ValidationError: If the resolved path escapes ``skills_dir``.
        OSError: On permission or I/O errors.
    """
    from ansible_know.skills import collection_skill_name

    if skills_dir is None:
        skills_dir = SKILLS_DIR

    collection_dir = (skills_dir / collection_skill_name(collection_namespace)).resolve()
    validate_path_containment(collection_dir, skills_dir)
    collection_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = collection_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))


def load_cached_manifest(
    collection_namespace: str,
    skills_dir: Path | None = None,
    installed_version: str | None = None,
) -> ManifestResult | None:
    """Load a cached MANIFEST.json if it exists and is still valid.

    When installed_version is provided, the cache is invalidated if the
    stored collection_version doesn't match.

    Backfills plugin_count and plugins fields for cached manifests
    created before plugin support was added.
    """
    from ansible_know.skills import collection_skill_name

    if skills_dir is None:
        skills_dir = SKILLS_DIR

    manifest_path = skills_dir / collection_skill_name(collection_namespace) / "MANIFEST.json"
    if not manifest_path.exists():
        return None

    manifest = json.loads(manifest_path.read_text())
    if installed_version and manifest.get("collection_version") != installed_version:
        return None

    if "plugin_count" not in manifest:
        manifest["plugin_count"] = 0
    if "plugins" not in manifest:
        manifest["plugins"] = []

    return manifest
