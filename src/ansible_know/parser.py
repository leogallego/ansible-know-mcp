"""Ansible module documentation parser.

Wraps `ansible-doc` CLI to extract structured module information
for skill generation and module discovery.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ansible_know.errors import AnsibleDocError, CollectionNotFoundError

_MISSING_COLLECTION_PATTERNS = ("has no attribute", "was not found", "could not be found")


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


def _run_ansible_doc(*args: str) -> str:
    """Execute ansible-doc with the given arguments and return stdout."""
    ansible_doc = _find_ansible_doc()
    cmd = [ansible_doc, *args]

    from ansible_know import collections
    collections_path = collections.get_collections_path()
    env = None
    if collections_path:
        env = os.environ.copy()
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
    except FileNotFoundError:
        raise AnsibleDocError(
            "ansible-doc not found. Install ansible-core: pip install ansible-core"
        )
    except subprocess.TimeoutExpired:
        raise AnsibleDocError(f"ansible-doc timed out: {' '.join(cmd)}")

    if result.returncode != 0:
        msg = f"ansible-doc failed (exit {result.returncode}): {result.stderr.strip()}"
        msg_lower = msg.lower()
        if any(p in msg_lower for p in _MISSING_COLLECTION_PATTERNS):
            raise CollectionNotFoundError(msg)
        raise AnsibleDocError(msg)
    return result.stdout


def get_module_doc(module_name: str) -> dict[str, Any]:
    """Fetch full documentation for a single module.

    Returns the parsed JSON from `ansible-doc <module> --json`.
    The top-level dict is keyed by the fully-qualified module name.
    """
    raw = _run_ansible_doc(module_name, "--json")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse ansible-doc JSON: {exc}")
    return doc


def list_modules(namespace: str | None = None) -> dict[str, str]:
    """List available modules with short descriptions.

    Args:
        namespace: Optional namespace filter (e.g., "community.docker").
                   If None, lists all modules.

    Returns:
        Dict mapping fully-qualified module names to their short descriptions.
    """
    args = ["--list", "--json"]
    if namespace:
        args.append(namespace)
    raw = _run_ansible_doc(*args)
    try:
        modules = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse module list JSON: {exc}")
    return modules


def search_modules(keyword: str, namespace: str | None = None) -> dict[str, str]:
    """Search modules by keyword in name or description.

    Args:
        keyword: Search term (case-insensitive).
        namespace: Optional namespace to restrict the search.

    Returns:
        Filtered dict of matching module names -> descriptions.
    """
    all_modules = list_modules(namespace)
    keyword_lower = keyword.lower()
    return {
        name: desc
        for name, desc in all_modules.items()
        if keyword_lower in name.lower() or keyword_lower in (desc or "").lower()
    }


def extract_params(module_doc: dict[str, Any]) -> list[dict[str, Any]]:
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


def extract_module_metadata(module_doc: dict[str, Any]) -> dict[str, Any]:
    """Extract all metadata needed for skill generation."""
    module_name = _get_module_name(module_doc)
    return {
        "module_name": module_name,
        "short_description": extract_short_description(module_doc),
        "params": extract_params(module_doc),
        "examples": extract_examples(module_doc),
        "is_api_module": is_api_module(module_doc),
    }
