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
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ansible_know.config import PLUGIN_TYPES
from ansible_know.errors import AnsibleDocError, CollectionNotFoundError, is_missing_collection_error
from ansible_know.validation import validate_plugin_type

if TYPE_CHECKING:
    from ansible_know.types import (
        AnsibleDocPayload,
        EntryPointInfo,
        ModuleMetadata,
        ParamDict,
        PluginMetadata,
        RoleMetadata,
    )

logger = logging.getLogger("ansible_know")

# Chunk size for multi-name ansible-doc invocations (avoids huge argv / timeouts).
_ANSIBLE_DOC_BATCH_SIZE = 50

__all__ = [
    "extract_examples",
    "extract_module_metadata",
    "extract_params",
    "extract_plugin_metadata",
    "extract_role_metadata",
    "extract_short_description",
    "get_module_doc",
    "get_module_docs",
    "get_plugin_doc",
    "get_plugin_docs",
    "get_role_doc",
    "is_api_module",
    "list_modules",
    "list_plugins",
    "list_roles",
    "load_module_metadata_batch",
    "load_plugin_metadata_batch",
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


def _batch_timeout(chunk_size: int) -> int:
    """Return a subprocess timeout scaled for multi-name ansible-doc calls."""
    # Single-name calls keep the historic 60s budget; larger chunks get more
    # headroom (capped) because one process documents many plugins.
    return max(60, min(300, 30 + 3 * chunk_size))


def _parse_ansible_doc_json(raw: str, *, kind: str = "ansible-doc") -> AnsibleDocPayload:
    """Parse ansible-doc --json stdout into a dict, or raise AnsibleDocError."""
    try:
        docs = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse {kind} JSON: {exc}") from exc
    if not isinstance(docs, dict):
        raise AnsibleDocError(
            f"Unexpected {kind} JSON type: {type(docs).__name__}"
        )
    return docs


def _fetch_docs_chunk(
    names: Sequence[str],
    *,
    plugin_type: str | None = None,
    collections_path: str | None = None,
) -> AnsibleDocPayload:
    """Run one ansible-doc --json invocation for ``names`` (optionally typed)."""
    if plugin_type is None:
        args: tuple[str, ...] = (*names, "--json")
        kind = "ansible-doc"
    else:
        args = ("-t", plugin_type, *names, "--json")
        kind = "plugin doc"
    raw = _run_ansible_doc(
        *args,
        collections_path=collections_path,
        timeout=_batch_timeout(len(names)),
    )
    return _parse_ansible_doc_json(raw, kind=kind)


def _fetch_docs_batched(
    names: Sequence[str],
    *,
    plugin_type: str | None = None,
    collections_path: str | None = None,
    label: str = "module",
) -> AnsibleDocPayload:
    """Fetch docs for many names with chunking and per-name fallback.

    On multi-name chunk failure, falls back to one ansible-doc call per name
    so earlier successes are kept. Single-name failures re-raise unchanged.
    """
    unique_names = list(dict.fromkeys(names))
    if not unique_names:
        return {}

    merged: AnsibleDocPayload = {}
    last_exc: AnsibleDocError | None = None
    for start in range(0, len(unique_names), _ANSIBLE_DOC_BATCH_SIZE):
        chunk = unique_names[start:start + _ANSIBLE_DOC_BATCH_SIZE]
        try:
            merged.update(_fetch_docs_chunk(
                chunk, plugin_type=plugin_type, collections_path=collections_path,
            ))
        except AnsibleDocError as exc:
            if len(chunk) == 1:
                raise
            logger.warning(
                "Batch %s doc fetch failed for %d names (%s); "
                "falling back per-%s",
                label, len(chunk), exc, label,
            )
            last_exc = exc
            for name in chunk:
                try:
                    merged.update(_fetch_docs_chunk(
                        [name],
                        plugin_type=plugin_type,
                        collections_path=collections_path,
                    ))
                except AnsibleDocError as name_exc:
                    last_exc = name_exc
                    logger.debug(
                        "Per-%s doc fallback failed for %s: %s",
                        label, name, name_exc,
                    )
    if not merged and last_exc is not None:
        raise AnsibleDocError(
            f"Batch {label} doc fetch failed for all chunks. "
            f"Last error: {last_exc}"
        ) from last_exc
    return merged


def _run_ansible_doc(
    *args: str,
    collections_path: str | None = None,
    timeout: int = 60,
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
            timeout=timeout,
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
) -> AnsibleDocPayload:
    """Fetch full documentation for a single module.

    Returns the parsed JSON from `ansible-doc <module> --json`.
    The top-level dict is keyed by the fully-qualified module name.
    """
    return get_module_docs([module_name], collections_path=collections_path)


def get_module_docs(
    module_names: Sequence[str],
    *,
    collections_path: str | None = None,
) -> AnsibleDocPayload:
    """Fetch documentation for many modules in few ansible-doc subprocesses.

    ``ansible-doc`` accepts multiple plugin names per invocation. Names are
    chunked into groups of ``_ANSIBLE_DOC_BATCH_SIZE`` to bound argv size and
    per-call runtime.

    Args:
        module_names: FQCNs to document. Empty sequence returns ``{}`` without
            invoking ansible-doc. Duplicates are removed; first-occurrence
            order is kept when building chunks.
        collections_path: Optional path prepended to ``ANSIBLE_COLLECTIONS_PATH``.

    Returns:
        Parsed ansible-doc ``--json`` object keyed by FQCN. Modules that
        ansible-doc cannot resolve are omitted (warnings go to stderr).

    Contract:
        Preconditions:
            - ``module_names`` may be empty; returns ``{}`` with no subprocess.
        Raises:
            AnsibleDocError: Subprocess/timeout/JSON failure when no names
                succeed after chunking and per-name fallback.
            CollectionNotFoundError: Re-raised when the request dedupes to
                exactly one name and that call reports a missing collection.
        Silences:
            - Unresolved FQCNs omitted from ansible-doc JSON (see Returns).
            - Per-name ``AnsibleDocError`` (including
              ``CollectionNotFoundError``) during multi-name chunk fallback;
              those names are omitted from the result (logged at debug).
            - Partial success: if any name succeeds, returns the merged dict
              without raising even when other names failed.
    """
    return _fetch_docs_batched(
        module_names,
        collections_path=collections_path,
        label="module",
    )


def load_module_metadata_batch(
    module_names: Sequence[str],
    *,
    collections_path: str | None = None,
) -> dict[str, ModuleMetadata]:
    """Batch-fetch module docs and extract skill/manifest metadata.

    Shared by collection manifest and collection skill generation so both
    paths pay O(chunks) ansible-doc calls instead of O(modules).

    Args:
        module_names: Module FQCNs (order preserved in the returned dict).
        collections_path: Optional path prepended to ``ANSIBLE_COLLECTIONS_PATH``.

    Returns:
        Mapping of FQCN → ``ModuleMetadata`` for modules present in the
        ansible-doc response. Missing modules are skipped (logged at debug).

    Contract:
        Preconditions:
            - Same as ``get_module_docs`` for ``module_names``.
        Raises:
            AnsibleDocError: Propagated from ``get_module_docs`` when every
                name fails hard (no docs returned).
        Silences:
            - Names missing from the ansible-doc response (debug log, skip).
            - ``extract_module_metadata`` ``AnsibleDocError`` (warning log, skip).
    """
    docs = get_module_docs(module_names, collections_path=collections_path)
    result: dict[str, ModuleMetadata] = {}
    for name in dict.fromkeys(module_names):
        entry = docs.get(name)
        if entry is None:
            logger.debug("No ansible-doc output for module %s", name)
            continue
        try:
            result[name] = extract_module_metadata({name: entry})
        except AnsibleDocError as exc:
            logger.warning("Failed to extract metadata for %s: %s", name, exc)
    return result


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


def extract_params(module_doc: AnsibleDocPayload) -> list[ParamDict]:
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


def extract_examples(module_doc: AnsibleDocPayload) -> str:
    """Extract example YAML snippets from a module doc."""
    module_name = _get_module_name(module_doc)
    return module_doc[module_name].get("examples", "")


def _get_module_name(module_doc: AnsibleDocPayload) -> str:
    """Return the first key from a module doc dict, or raise on empty."""
    if not module_doc:
        raise AnsibleDocError("Module not found or ansible-doc returned empty output.")
    return next(iter(module_doc))


def extract_short_description(module_doc: AnsibleDocPayload) -> str:
    """Extract the module's one-line description."""
    module_name = _get_module_name(module_doc)
    doc_entry = module_doc[module_name].get("doc", {})
    desc = doc_entry.get("short_description", "")
    return desc.strip() if desc else ""


def is_api_module(module_doc: AnsibleDocPayload) -> bool:
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


def extract_module_metadata(module_doc: AnsibleDocPayload) -> ModuleMetadata:
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
) -> AnsibleDocPayload:
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
) -> AnsibleDocPayload:
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


def extract_role_metadata(role_doc: AnsibleDocPayload) -> RoleMetadata:
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
) -> AnsibleDocPayload:
    """Fetch full documentation for a single plugin.

    Returns the parsed JSON from `ansible-doc -t <type> <plugin> --json`.
    """
    return get_plugin_docs(
        [plugin_name], plugin_type, collections_path=collections_path,
    )


def get_plugin_docs(
    plugin_names: Sequence[str],
    plugin_type: str,
    *,
    collections_path: str | None = None,
) -> AnsibleDocPayload:
    """Fetch documentation for many plugins of one type in few ansible-doc calls.

    Args:
        plugin_names: Plugin FQCNs of ``plugin_type``. Empty returns ``{}``.
            Duplicates are removed; first-occurrence order is kept.
        plugin_type: One of ``PLUGIN_TYPES`` (validated).
        collections_path: Optional path prepended to ``ANSIBLE_COLLECTIONS_PATH``.

    Returns:
        Parsed ansible-doc ``--json`` object keyed by FQCN. Unresolved plugins
        are omitted.

    Contract:
        Preconditions:
            - ``plugin_type`` must be a known plugin type (validated first).
            - ``plugin_names`` may be empty; returns ``{}`` with no subprocess.
        Raises:
            ValidationError: When ``plugin_type`` is not a known plugin type.
            AnsibleDocError: Subprocess/timeout/JSON failure when no names
                succeed after chunking and per-name fallback.
            CollectionNotFoundError: Re-raised when the request dedupes to
                exactly one name and that call reports a missing collection.
        Silences:
            - Unresolved FQCNs omitted from ansible-doc JSON (see Returns).
            - Per-name ``AnsibleDocError`` during multi-name chunk fallback;
              those names are omitted (logged at debug).
            - Partial success returns the merged dict without raising.
    """
    validate_plugin_type(plugin_type)
    return _fetch_docs_batched(
        plugin_names,
        plugin_type=plugin_type,
        collections_path=collections_path,
        label="plugin",
    )


def load_plugin_metadata_batch(
    plugin_names: Sequence[str],
    plugin_type: str,
    *,
    collections_path: str | None = None,
) -> dict[str, PluginMetadata]:
    """Batch-fetch plugin docs and extract skill/manifest metadata.

    Args:
        plugin_names: Plugin FQCNs of ``plugin_type``.
        plugin_type: One of ``PLUGIN_TYPES``.
        collections_path: Optional path prepended to ``ANSIBLE_COLLECTIONS_PATH``.

    Returns:
        Mapping of FQCN → ``PluginMetadata`` for plugins present in the
        ansible-doc response. Missing plugins are skipped (logged at debug).

    Contract:
        Preconditions:
            - Same as ``get_plugin_docs`` for ``plugin_names`` / ``plugin_type``.
        Raises:
            ValidationError: When ``plugin_type`` is not a known plugin type.
            AnsibleDocError: Propagated from ``get_plugin_docs`` when every
                name fails hard (no docs returned).
        Silences:
            - Names missing from the ansible-doc response (debug log, skip).
            - ``extract_plugin_metadata`` ``AnsibleDocError`` (warning log, skip).
    """
    docs = get_plugin_docs(
        plugin_names, plugin_type, collections_path=collections_path,
    )
    result: dict[str, PluginMetadata] = {}
    for name in dict.fromkeys(plugin_names):
        entry = docs.get(name)
        if entry is None:
            logger.debug("No ansible-doc output for plugin %s (%s)", name, plugin_type)
            continue
        try:
            result[name] = extract_plugin_metadata({name: entry}, plugin_type)
        except AnsibleDocError as exc:
            logger.warning(
                "Failed to extract plugin metadata for %s: %s", name, exc,
            )
    return result


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
    plugin_doc: AnsibleDocPayload, plugin_type: str,
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
