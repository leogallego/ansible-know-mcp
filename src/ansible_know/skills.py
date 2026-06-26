"""Skill rendering and package writing.

Skill generation approach inspired by AnsibleClaw (https://github.com/micytao/AnsibleClaw).
Generates SKILL.md skill packages from Ansible module, role, and collection metadata.
"""

from __future__ import annotations

import functools
import logging
import re
import stat
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ansible_know.config import TEMPLATE_DIR
from ansible_know.tagging import derive_tags
from ansible_know.validation import truncate_response, validate_path_containment

if TYPE_CHECKING:
    from ansible_know.types import (
        CollectionSkillContext,
        ModuleMetadata,
        ModuleTagEntry,
        ParamDict,
        PluginManifestInput,
        SkillEntry,
    )

logger = logging.getLogger("ansible_know")

PLUGIN_SKILL_DIR_RE = re.compile(r"^([a-z]+)__(.+)$")

__all__ = [
    "PLUGIN_SKILL_DIR_RE",
    "extract_skill_description",
    "get_skill_sync",
    "list_skills_sync",
    "module_to_skill_name",
    "render_collection_skill",
    "render_module_skill",
    "render_plugin_skill",
    "render_role_skill",
    "render_skill",
    "write_collection_skill_package",
    "write_module_skill_package",
    "write_plugin_skill_package",
    "write_role_skill_package",
    "write_skill_package",
]


@functools.lru_cache(maxsize=1)
def _get_template_env():
    """Return the cached Jinja2 environment, creating it on first call."""
    from jinja2 import Environment, FileSystemLoader

    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def module_to_skill_name(module_name: str) -> str:
    """Convert a module FQCN to a skill directory name."""
    return module_name


def _module_template_context(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build the shared template context from module metadata."""
    params = metadata["params"]
    example_args = _build_example_args(params, metadata.get("examples", ""))
    return {
        "module_name": metadata["module_name"],
        "skill_name": metadata["module_name"].rsplit(".", 1)[-1],
        "short_description": metadata["short_description"],
        "params": params,
        "examples": metadata["examples"].strip() if metadata["examples"] else "",
        "example_args": example_args,
        "is_api_module": metadata.get("is_api_module", False),
        "examples_contain_play": _examples_contain_play(metadata.get("examples", "")),
    }


def _examples_contain_play(examples: str) -> bool:
    """Check if examples YAML already defines a full play."""
    if not examples:
        return False
    return "hosts:" in examples and "tasks:" in examples


def render_module_skill(metadata: dict[str, Any]) -> str:
    """Render the SKILL.md template with the given module metadata."""
    logger.debug("Rendering skill for module %s", metadata.get("module_name", "?"))
    env = _get_template_env()
    template = env.get_template("SKILL.md.j2")
    return template.render(**_module_template_context(metadata))


render_skill = render_module_skill


def write_module_skill_package(output_dir: Path, metadata: dict[str, Any]) -> None:
    """Write the full skill package: SKILL.md + scripts + assets."""
    logger.debug("Writing skill package to %s for %s", output_dir, metadata.get("module_name", "?"))
    env = _get_template_env()
    ctx = _module_template_context(metadata)

    output_dir.mkdir(parents=True, exist_ok=True)

    skill_template = env.get_template("SKILL.md.j2")
    (output_dir / "SKILL.md").write_text(skill_template.render(**ctx))

    scripts_dir = output_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)

    for script_name in ("run.sh", "check.sh"):
        template = env.get_template(f"{script_name}.j2")
        script_path = scripts_dir / script_name
        script_path.write_text(template.render(**ctx))
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    assets_dir = output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    playbook_template = env.get_template("playbook.yml.j2")
    (assets_dir / "playbook.yml").write_text(playbook_template.render(**ctx))


write_skill_package = write_module_skill_package


# --- Skill reading / listing ---


def extract_skill_description(skill_md: Path) -> str:
    """Extract description from a SKILL.md frontmatter."""
    content = skill_md.read_text()
    for line in content.splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip().strip(">-").strip()
    return ""


def list_skills_sync(
    skills_dir: Path, collection: str | None,
) -> list[SkillEntry]:
    """List generated skills from *skills_dir*.

    When *collection* is given, returns module/role/plugin skills within that
    collection directory.  Otherwise returns top-level (collection-level and
    standalone) skill entries.
    """
    results: list[dict[str, str]] = []
    if not skills_dir.exists():
        return results

    if collection:
        collection_dir = (skills_dir / collection).resolve()
        validate_path_containment(collection_dir, skills_dir)
        if not collection_dir.is_dir():
            return results
        for sub_dir in sorted(collection_dir.iterdir()):
            try:
                skill_md = sub_dir / "SKILL.md"
                if sub_dir.is_dir() and not sub_dir.is_symlink() and skill_md.exists():
                    dir_name = sub_dir.name
                    from ansible_know.config import PLUGIN_TYPES
                    match = PLUGIN_SKILL_DIR_RE.match(dir_name)
                    if match and match.group(1) in PLUGIN_TYPES:
                        display_name = f"{collection}.{match.group(2)}"
                    else:
                        display_name = f"{collection}.{dir_name}"
                    results.append({
                        "name": display_name,
                        "description": extract_skill_description(skill_md),
                        "path": str(sub_dir),
                    })
            except OSError:
                logger.warning("Skipping unreadable skill: %s", sub_dir.name)
                continue
    else:
        for skill_dir in sorted(skills_dir.iterdir()):
            try:
                if not skill_dir.is_dir() or skill_dir.is_symlink():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    results.append({
                        "name": skill_dir.name,
                        "description": extract_skill_description(skill_md),
                        "path": str(skill_dir),
                    })
            except OSError:
                logger.warning("Skipping unreadable skill: %s", skill_dir.name)
                continue
    return results


def get_skill_sync(skills_dir: Path, skill_name: str) -> str:
    """Read a skill's SKILL.md content from disk.

    Callers MUST validate *skill_name* with ``validate_skill_name()`` first.

    Raises:
        FileNotFoundError: If no matching SKILL.md exists.
        ValidationError: If a resolved path escapes *skills_dir*.
        OSError: On permission or I/O errors reading the file.
    """
    parts = skill_name.split(".")
    if len(parts) >= 3:
        namespace = ".".join(parts[:2])
        short_name = ".".join(parts[2:])

        nested_path = (skills_dir / namespace / short_name / "SKILL.md").resolve()
        validate_path_containment(nested_path, skills_dir)
        if nested_path.exists():
            return truncate_response(nested_path.read_text())

        from ansible_know.config import PLUGIN_TYPES
        for ptype in PLUGIN_TYPES:
            plugin_path = (skills_dir / namespace / f"{ptype}__{short_name}" / "SKILL.md").resolve()
            validate_path_containment(plugin_path, skills_dir)
            if plugin_path.exists():
                return truncate_response(plugin_path.read_text())

        flat_path = (skills_dir / skill_name / "SKILL.md").resolve()
        validate_path_containment(flat_path, skills_dir)
        if flat_path.exists():
            return truncate_response(flat_path.read_text())
    else:
        skill_path = (skills_dir / skill_name / "SKILL.md").resolve()
        validate_path_containment(skill_path, skills_dir)
        if skill_path.exists():
            return truncate_response(skill_path.read_text())

    raise FileNotFoundError(f"Skill '{skill_name}' not found.")


def _build_example_args(params: list[ParamDict], examples_yaml: str = "") -> str:
    """Build a representative example args string from parameters."""
    concrete = _extract_example_values(examples_yaml)

    parts: list[str] = []
    for p in params:
        if p["required"]:
            name = p["name"]
            if name in concrete:
                parts.append(f"{name}={concrete[name]}")
            elif p["choices"]:
                parts.append(f"{name}={p['choices'][0]}")
            elif p["type"] == "bool":
                parts.append(f"{name}=true")
            else:
                parts.append(f"{name}=<{name}>")
    if not parts:
        for p in params[:2]:
            name = p["name"]
            if name in concrete:
                parts.append(f"{name}={concrete[name]}")
            elif p["default"] is not None:
                parts.append(f"{name}={p['default']}")
            elif p["choices"]:
                parts.append(f"{name}={p['choices'][0]}")
            else:
                parts.append(f"{name}=<{name}>")
    return " ".join(parts) if parts else "name=<value>"


def _extract_example_values(examples_yaml: str) -> dict[str, str]:
    """Pull concrete parameter values from the first YAML example block."""
    values: dict[str, str] = {}
    if not examples_yaml:
        return values
    for line in examples_yaml.splitlines():
        line = line.strip()
        if line.startswith("- name:") or line.startswith("#") or not line:
            continue
        if ":" in line and not line.endswith(":"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and not val.startswith("{") and not val.startswith("["):
                values.setdefault(key, val)
    return values


def _role_template_context(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build template context from role metadata."""
    role_name = metadata["role_name"]
    return {
        "role_name": role_name,
        "skill_name": role_name.rsplit(".", 1)[-1],
        "short_description": metadata.get("short_description", ""),
        "entry_points": metadata.get("entry_points", {}),
        "dependencies": metadata.get("dependencies", []),
        "examples": metadata.get("examples", "").strip(),
        "doc_source": metadata.get("doc_source", ""),
    }


def render_role_skill(metadata: dict[str, Any]) -> str:
    """Render the ROLE_SKILL.md.j2 template with role metadata."""
    logger.debug("Rendering role skill for %s", metadata.get("role_name", "?"))
    env = _get_template_env()
    template = env.get_template("ROLE_SKILL.md.j2")
    return template.render(**_role_template_context(metadata))


def write_role_skill_package(output_dir: Path, metadata: dict[str, Any]) -> None:
    """Write the role skill package: SKILL.md + assets/playbook.yml (no scripts/)."""
    logger.debug("Writing role skill package to %s for %s", output_dir, metadata.get("role_name", "?"))
    env = _get_template_env()
    ctx = _role_template_context(metadata)

    output_dir.mkdir(parents=True, exist_ok=True)

    skill_template = env.get_template("ROLE_SKILL.md.j2")
    (output_dir / "SKILL.md").write_text(skill_template.render(**ctx))

    assets_dir = output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    playbook_template = env.get_template("role_playbook.yml.j2")
    (assets_dir / "playbook.yml").write_text(playbook_template.render(**ctx))


# --- Plugin-level skill ---


def _plugin_template_context(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build template context from plugin metadata."""
    plugin_name = metadata["plugin_name"]
    return {
        "plugin_name": plugin_name,
        "plugin_type": metadata["plugin_type"],
        "skill_name": plugin_name.rsplit(".", 1)[-1],
        "short_description": metadata.get("short_description", ""),
        "params": metadata.get("params", []),
        "examples": metadata.get("examples", "").strip(),
    }


def render_plugin_skill(metadata: dict[str, Any]) -> str:
    """Render the PLUGIN_SKILL.md.j2 template with plugin metadata."""
    logger.debug("Rendering plugin skill for %s", metadata.get("plugin_name", "?"))
    env = _get_template_env()
    template = env.get_template("PLUGIN_SKILL.md.j2")
    return template.render(**_plugin_template_context(metadata))


def write_plugin_skill_package(output_dir: Path, metadata: dict[str, Any]) -> None:
    """Write the plugin skill package: SKILL.md only (no scripts/ or assets/)."""
    logger.debug(
        "Writing plugin skill package to %s for %s",
        output_dir, metadata.get("plugin_name", "?"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    content = render_plugin_skill(metadata)
    (output_dir / "SKILL.md").write_text(content)


# --- Collection-level skill ---


def _collection_template_context(
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
) -> CollectionSkillContext:
    """Build template context for a collection-level skill."""
    modules_by_tag: dict[str, list[ModuleTagEntry]] = {}
    for meta in metadata_list:
        fqcn = meta["module_name"]
        short_name = fqcn.rsplit(".", 1)[-1]
        params = meta["params"]
        required_params = [p for p in params if p["required"]]
        tags = derive_tags(fqcn, params)

        entry: ModuleTagEntry = {
            "fqcn": fqcn,
            "short_name": short_name,
            "short_description": meta["short_description"],
            "required_params": required_params,
            "is_api_module": meta.get("is_api_module", False),
        }

        if not tags:
            modules_by_tag.setdefault("other", []).append(entry)
        else:
            for tag in tags:
                modules_by_tag.setdefault(tag, []).append(entry)

    all_api = bool(metadata_list) and all(
        m.get("is_api_module", False) for m in metadata_list
    )

    common_params = _find_common_params(metadata_list)

    plugins_by_type: dict[str, list[dict[str, str]]] = {}
    for pmeta in (plugins_metadata or []):
        ptype = pmeta["plugin_type"]
        plugins_by_type.setdefault(ptype, []).append({
            "fqcn": pmeta["fqcn"],
            "short_description": pmeta["description"],
        })

    return {
        "collection_namespace": namespace,
        "collection_version": collection_version,
        "modules_by_tag": modules_by_tag,
        "all_api": all_api,
        "common_params": common_params,
        "module_count": len(metadata_list),
        "plugins_by_type": plugins_by_type,
    }


def _find_common_params(metadata_list: list[ModuleMetadata]) -> list[ParamDict]:
    """Find parameters shared by >80% of modules in a collection."""
    if not metadata_list:
        return []

    threshold = len(metadata_list) * 0.8
    param_counts: Counter[str] = Counter()
    param_info: dict[str, ParamDict] = {}

    for meta in metadata_list:
        for p in meta["params"]:
            name = p["name"]
            param_counts[name] += 1
            if name not in param_info:
                param_info[name] = p

    return [
        param_info[name]
        for name, count in param_counts.most_common()
        if count >= threshold
    ]


def render_collection_skill(
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
) -> str:
    """Render the COLLECTION_SKILL.md.j2 template for a collection-level skill."""
    logger.debug("Rendering collection skill for %s", namespace)
    env = _get_template_env()
    template = env.get_template("COLLECTION_SKILL.md.j2")
    ctx = _collection_template_context(
        namespace, metadata_list, collection_version, plugins_metadata,
    )
    return template.render(**ctx)


def write_collection_skill_package(
    output_dir: Path,
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
) -> None:
    """Write the collection-level skill package: SKILL.md only (no scripts/assets)."""
    logger.debug("Writing collection skill package to %s for %s", output_dir, namespace)
    output_dir.mkdir(parents=True, exist_ok=True)

    content = render_collection_skill(
        namespace, metadata_list, collection_version, plugins_metadata,
    )
    (output_dir / "SKILL.md").write_text(content)
