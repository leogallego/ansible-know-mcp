"""Ansible module documentation parser.

Wraps `ansible-doc` CLI to extract structured module information
for skill generation and module discovery.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ansible_know.config import PLUGIN_TYPES
from ansible_know.errors import AnsibleDocError, CollectionNotFoundError, is_missing_collection_error
from ansible_know.validation import validate_plugin_type

if TYPE_CHECKING:
    from ansible_know.types import EntryPointInfo, ModuleMetadata, ParamDict, PluginMetadata, RoleMetadata

logger = logging.getLogger("ansible_know")

__all__ = [
    "extract_examples",
    "extract_module_metadata",
    "extract_params",
    "extract_plugin_metadata",
    "extract_role_metadata",
    "extract_short_description",
    "get_module_doc",
    "get_plugin_doc",
    "get_role_doc",
    "is_api_module",
    "list_modules",
    "list_plugins",
    "list_roles",
    "search_modules",
    "search_plugins",
    "transform_galaxy_to_ansible_doc_format",
]



def _find_ansible_doc() -> str:
    """Locate the ansible-doc binary, preferring the current Python environment."""
    env_bin = Path(sys.executable).parent / "ansible-doc"
    if env_bin.exists():
        return str(env_bin)
    found = shutil.which("ansible-doc")
    if found:
        return found
    raise AnsibleDocError(
        "ansible-doc not found. Install ansible-core: pip install ansible-core"
    )


def _run_ansible_doc(
    *args: str, collections_path: str | None = None,
) -> str:
    """Execute ansible-doc with the given arguments and return stdout."""
    ansible_doc = _find_ansible_doc()
    cmd = [ansible_doc, *args]

    logger.debug("Running: %s (collections_path=%s)", " ".join(cmd), collections_path)
    env = os.environ.copy()
    env.setdefault("ANSIBLE_LOCAL_TMP", tempfile.gettempdir())
    if collections_path:
        existing = env.get("ANSIBLE_COLLECTIONS_PATH", "")
        env["ANSIBLE_COLLECTIONS_PATH"] = (
            f"{collections_path}{os.pathsep}{existing}" if existing else collections_path
        )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except FileNotFoundError as exc:
        logger.debug("ansible-doc binary not found")
        raise AnsibleDocError(
            "ansible-doc not found. Install ansible-core: pip install ansible-core"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        logger.debug("ansible-doc timed out: %s", " ".join(cmd))
        raise AnsibleDocError(f"ansible-doc timed out: {' '.join(cmd)}") from exc

    if result.returncode != 0:
        msg = f"ansible-doc failed (exit {result.returncode}): {result.stderr.strip()}"
        if is_missing_collection_error(msg):
            raise CollectionNotFoundError(msg)
        raise AnsibleDocError(msg)

    if result.stdout.strip() in ("", "{}"):
        if is_missing_collection_error(result.stderr):
            raise CollectionNotFoundError(
                f"ansible-doc returned empty output: {result.stderr.strip()}"
            )

    return result.stdout


def get_module_doc(
    module_name: str, *, collections_path: str | None = None,
) -> dict[str, Any]:
    """Fetch full documentation for a single module.

    Returns the parsed JSON from `ansible-doc <module> --json`.
    The top-level dict is keyed by the fully-qualified module name.
    """
    raw = _run_ansible_doc(module_name, "--json", collections_path=collections_path)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse ansible-doc JSON: {exc}") from exc
    return doc


def list_modules(
    collection_filter: str | None = None, *, collections_path: str | None = None,
) -> dict[str, str]:
    """List available modules with short descriptions.

    Args:
        collection_filter: Optional collection filter passed to ansible-doc
                           (e.g., "community.docker"). If None, lists all modules.
        collections_path: Optional path to prepend to ANSIBLE_COLLECTIONS_PATH.

    Returns:
        Dict mapping fully-qualified module names to their short descriptions.
    """
    args = ["--list", "--json"]
    if collection_filter:
        args.append(collection_filter)
    raw = _run_ansible_doc(*args, collections_path=collections_path)
    try:
        modules = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse module list JSON: {exc}") from exc
    return modules


def search_modules(
    keyword: str,
    collection_filter: str | None = None,
    *,
    collections_path: str | None = None,
) -> dict[str, str]:
    """Search modules by keyword in name or description.

    Args:
        keyword: Search term (case-insensitive).
        collection_filter: Optional collection to restrict the search.
        collections_path: Optional path to prepend to ANSIBLE_COLLECTIONS_PATH.

    Returns:
        Filtered dict of matching module names -> descriptions.
    """
    all_modules = list_modules(collection_filter, collections_path=collections_path)
    keyword_lower = keyword.lower()
    return {
        name: desc
        for name, desc in all_modules.items()
        if keyword_lower in name.lower() or keyword_lower in (desc or "").lower()
    }


def extract_params(module_doc: dict[str, Any]) -> list[ParamDict]:
    """Extract parameter specs from a module doc.

    Returns:
        List of parameter dicts with keys:
        name, type, required, default, choices, description, aliases
    """
    module_name = _get_module_name(module_doc)
    doc_entry = module_doc[module_name].get("doc", {})
    options = doc_entry.get("options", {})

    params = []
    for param_name, spec in options.items():
        description = spec.get("description", [])
        if isinstance(description, list):
            description = " ".join(description)

        params.append({
            "name": param_name,
            "type": spec.get("type", "str"),
            "required": spec.get("required", False),
            "default": spec.get("default"),
            "choices": spec.get("choices"),
            "description": description,
            "aliases": spec.get("aliases", []),
        })

    params.sort(key=lambda p: (not p["required"], p["name"]))
    return params


def extract_examples(module_doc: dict[str, Any]) -> str:
    """Extract example YAML snippets from a module doc."""
    module_name = _get_module_name(module_doc)
    return module_doc[module_name].get("examples", "")


def _get_module_name(module_doc: dict[str, Any]) -> str:
    """Return the first key from a module doc dict, or raise on empty."""
    if not module_doc:
        raise AnsibleDocError("Module not found or ansible-doc returned empty output.")
    return next(iter(module_doc))


def extract_short_description(module_doc: dict[str, Any]) -> str:
    """Extract the module's one-line description."""
    module_name = _get_module_name(module_doc)
    doc_entry = module_doc[module_name].get("doc", {})
    desc = doc_entry.get("short_description", "")
    return desc.strip() if desc else ""


def is_api_module(module_doc: dict[str, Any]) -> bool:
    """Detect whether a module talks to an API rather than managing system state over SSH."""
    module_name = _get_module_name(module_doc)
    doc_entry = module_doc[module_name].get("doc", {})
    options = doc_entry.get("options", {})

    api_param_hints = {"url", "api_url", "api_key", "api_token", "token",
                       "server_url", "host", "hostname", "validate_certs"}
    option_names = {k.lower() for k in options}
    suffix_names = {k.rsplit("_", 1)[-1] for k in option_names}
    if len(option_names & api_param_hints) >= 2 or len(suffix_names & {"url", "token"}) >= 2:
        return True

    examples = module_doc[module_name].get("examples", "")
    if "connection: local" in examples or "connection:" in examples:
        return True

    return False


def extract_module_metadata(module_doc: dict[str, Any]) -> ModuleMetadata:
    """Extract all metadata needed for skill generation."""
    module_name = _get_module_name(module_doc)
    logger.debug("Extracting module metadata for %s", module_name)
    return {
        "module_name": module_name,
        "short_description": extract_short_description(module_doc),
        "params": extract_params(module_doc),
        "examples": extract_examples(module_doc),
        "is_api_module": is_api_module(module_doc),
    }


def transform_galaxy_to_ansible_doc_format(
    fqcn: str, entry: dict[str, Any],
) -> dict[str, Any]:
    """Convert a Galaxy docs-blob content entry to ansible-doc --json format."""
    ds = entry.get("doc_strings", {})
    raw_doc = ds.get("doc", {})

    raw_options = raw_doc.get("options", [])
    if isinstance(raw_options, list):
        options_dict: dict[str, Any] = {}
        for opt in raw_options:
            if not isinstance(opt, dict):
                continue
            opt_copy = dict(opt)
            opt_name = opt_copy.pop("name", None)
            if opt_name:
                options_dict[opt_name] = opt_copy
    else:
        options_dict = raw_options

    doc_section = {
        "short_description": raw_doc.get("short_description", ""),
        "description": raw_doc.get("description", []),
        "options": options_dict,
        "author": raw_doc.get("author", []),
        "notes": raw_doc.get("notes", []),
        "version_added": raw_doc.get("version_added", ""),
    }

    return {
        fqcn: {
            "doc": doc_section,
            "examples": ds.get("examples", ""),
            "return": ds.get("return", []),
            "metadata": ds.get("metadata", {}),
        }
    }


def list_roles(
    collection_filter: str | None = None, *, collections_path: str | None = None,
) -> dict[str, dict[str, Any]]:
    """List available roles with descriptions and entry points.

    Args:
        collection_filter: Optional collection filter passed to ansible-doc
                           (e.g., "fedora.linux_system_roles"). If None, lists all roles.
        collections_path: Optional path to prepend to ANSIBLE_COLLECTIONS_PATH.

    Returns:
        Dict mapping FQCNs to {collection, description, entry_points}.
    """
    args = ["--list", "-t", "role", "--json"]
    if collection_filter:
        args.append(collection_filter)
    raw = _run_ansible_doc(*args, collections_path=collections_path)
    try:
        roles = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse role list JSON: {exc}") from exc
    return roles


def get_role_doc(
    role_name: str, *, collections_path: str | None = None,
) -> dict[str, Any]:
    """Fetch full documentation for a single role.

    Returns parsed JSON from ansible-doc. Returns {} if the role
    lacks argument_specs.yml (same as ansible-doc behavior).
    """
    raw = _run_ansible_doc("-t", "role", role_name, "--json", collections_path=collections_path)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse role doc JSON: {exc}") from exc
    return doc


def extract_role_metadata(role_doc: dict[str, Any]) -> RoleMetadata:
    """Extract metadata from ansible-doc -t role JSON output.

    Returns dict with role_name, short_description, and entry_points.
    Each entry point has description and options list matching the module
    params schema (name, type, required, default, description).
    """
    if not role_doc:
        return {"role_name": "", "short_description": "", "entry_points": {}}

    role_name = next(iter(role_doc))
    logger.debug("Extracting role metadata for %s", role_name)
    role_data = role_doc[role_name]
    raw_entry_points = role_data.get("entry_points", {})

    first_desc = ""
    entry_points: dict[str, EntryPointInfo] = {}

    for ep_name, ep_data in raw_entry_points.items():
        desc = ep_data.get("description", "")
        if isinstance(desc, list):
            desc = " ".join(desc)
        if not first_desc:
            first_desc = desc

        options_raw = ep_data.get("options", {})
        options: list[ParamDict] = []
        for opt_name, opt_spec in options_raw.items():
            opt_desc = opt_spec.get("description", "")
            if isinstance(opt_desc, list):
                opt_desc = " ".join(opt_desc)
            options.append({
                "name": opt_name,
                "type": opt_spec.get("type", "str"),
                "required": opt_spec.get("required", False),
                "default": opt_spec.get("default"),
                "choices": opt_spec.get("choices"),
                "description": opt_desc,
                "aliases": opt_spec.get("aliases", []),
            })
        options.sort(key=lambda o: (not o["required"], o["name"]))

        entry_points[ep_name] = {
            "description": desc,
            "options": options,
        }

    return {
        "role_name": role_name,
        "short_description": first_desc,
        "entry_points": entry_points,
    }


def list_plugins(
    plugin_type: str,
    collection_filter: str | None = None,
    *,
    collections_path: str | None = None,
) -> dict[str, str]:
    """List available plugins of a given type with short descriptions.

    Args:
        plugin_type: One of PLUGIN_TYPES (e.g., "lookup", "filter").
        collection_filter: Optional collection filter (e.g., "netbox.netbox").
        collections_path: Optional path to prepend to ANSIBLE_COLLECTIONS_PATH.

    Returns:
        Dict mapping fully-qualified plugin names to their short descriptions.
    """
    validate_plugin_type(plugin_type)
    args = ["--list", "-t", plugin_type, "--json"]
    if collection_filter:
        args.append(collection_filter)
    raw = _run_ansible_doc(*args, collections_path=collections_path)
    try:
        plugins = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse plugin list JSON: {exc}") from exc
    return plugins


def get_plugin_doc(
    plugin_name: str,
    plugin_type: str,
    *,
    collections_path: str | None = None,
) -> dict[str, Any]:
    """Fetch full documentation for a single plugin.

    Returns the parsed JSON from `ansible-doc -t <type> <plugin> --json`.
    """
    validate_plugin_type(plugin_type)
    raw = _run_ansible_doc(
        "-t", plugin_type, plugin_name, "--json",
        collections_path=collections_path,
    )
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse plugin doc JSON: {exc}") from exc
    return doc


def search_plugins(
    keyword: str,
    plugin_type: str | None = None,
    collection_filter: str | None = None,
    *,
    collections_path: str | None = None,
) -> dict[str, str]:
    """Search plugins by keyword in name or description.

    When plugin_type is None, searches across all plugin types
    sequentially (14 ansible-doc calls). The MCP server tool bypasses
    this path and parallelizes across types via asyncio.gather — this
    all-type codepath exists for direct parser callers (scripts, tests,
    REPL exploration).
    """
    if plugin_type is not None:
        validate_plugin_type(plugin_type)
        all_plugins = list_plugins(
            plugin_type, collection_filter, collections_path=collections_path,
        )
    else:
        all_plugins: dict[str, str] = {}
        errors: list[str] = []
        for ptype in PLUGIN_TYPES:
            try:
                found = list_plugins(
                    ptype, collection_filter, collections_path=collections_path,
                )
                all_plugins.update(found)
            except AnsibleDocError as exc:
                errors.append(str(exc))
                continue

        if not all_plugins and errors:
            raise AnsibleDocError(
                f"Plugin discovery failed for all {len(errors)} types. "
                f"Last error: {errors[-1]}"
            )

    keyword_lower = keyword.lower()
    return {
        name: desc
        for name, desc in all_plugins.items()
        if keyword_lower in name.lower() or keyword_lower in (desc or "").lower()
    }


def extract_plugin_metadata(
    plugin_doc: dict[str, Any], plugin_type: str,
) -> PluginMetadata:
    """Extract all metadata needed for plugin skill generation."""
    plugin_name = _get_module_name(plugin_doc)
    logger.debug("Extracting plugin metadata for %s (type=%s)", plugin_name, plugin_type)
    return {
        "plugin_name": plugin_name,
        "plugin_type": plugin_type,
        "short_description": extract_short_description(plugin_doc),
        "params": extract_params(plugin_doc),
        "examples": extract_examples(plugin_doc),
    }
