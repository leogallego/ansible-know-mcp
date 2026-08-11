"""Skill rendering and package writing.

Skill generation approach inspired by AnsibleClaw (https://github.com/micytao/AnsibleClaw).
Generates SKILL.md skill packages from Ansible module, role, and collection metadata.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import shutil
import stat
import tarfile
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ansible_know.config import TEMPLATE_DIR
from ansible_know.errors import ValidationError
from ansible_know.tagging import derive_tags
from ansible_know.validation import (
    split_collection_fqcn,
    truncate_response,
    validate_install_path,
    validate_lola_module_name,
    validate_mcp_server_url,
    validate_mcp_transport,
    validate_path_containment,
    validate_plugin_name,
)

if TYPE_CHECKING:
    from ansible_know.types import (
        AgentMcpConfig,
        AgentMcpHttpServer,
        AgentMcpServer,
        AgentMcpStdioServer,
        AgentPluginManifest,
        CollectionSkillContext,
        LolaMarketYml,
        ModuleMetadata,
        ModuleTagEntry,
        PackageAsPluginResult,
        PackageForLolaResult,
        ParamDict,
        PluginManifestInput,
        SkillEntry,
    )

logger = logging.getLogger("ansible_know")

PLUGIN_SKILL_DIR_RE = re.compile(r"^([a-z]+)-(.+)$")
_AGENTS_MD_START = "<!-- ansible-know:skills:start -->"
_AGENTS_MD_END = "<!-- ansible-know:skills:end -->"
_agents_md_lock = threading.Lock()

__all__ = [
    "PLUGIN_SKILL_DIR_RE",
    "collection_skill_name",
    "default_lola_module_name",
    "default_plugin_name",
    "extract_skill_description",
    "fqcn_to_skill_name",
    "get_skill_sync",
    "list_skills_sync",
    "module_to_skill_name",
    "package_as_agent_plugin",
    "package_collection_for_lola",
    "plugin_skill_name",
    "resolve_collection_skills_dir",
    "skill_dir_to_collection_fqcn",
    "skill_dir_to_short_fqcn",
    "render_collection_skill",
    "render_module_skill",
    "render_plugin_skill",
    "render_role_skill",
    "render_skill",
    "role_skill_name",
    "update_agents_md",
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


def _to_kebab(name: str) -> str:
    """Convert underscores and dots to hyphens for spec-compliant naming."""
    return name.replace("_", "-").replace(".", "-")


def fqcn_to_skill_name(fqcn: str) -> str:
    """Convert a module/role FQCN to a spec-compliant kebab-case skill name.

    Takes the short name (after last dot) and converts underscores to hyphens.
    """
    short = fqcn.rsplit(".", 1)[-1]
    return _to_kebab(short)


def plugin_skill_name(fqcn: str, plugin_type: str) -> str:
    """Convert a plugin FQCN to a spec-compliant skill name with type prefix."""
    short = fqcn.rsplit(".", 1)[-1]
    return f"{plugin_type}-{_to_kebab(short)}"


def role_skill_name(fqcn: str) -> str:
    """Convert a role FQCN to a spec-compliant kebab-case skill name."""
    return fqcn_to_skill_name(fqcn)


def collection_skill_name(collection_fqcn: str) -> str:
    """Convert a collection FQCN to a spec-compliant kebab-case skill name."""
    return _to_kebab(collection_fqcn)


def skill_dir_to_collection_fqcn(kebab_dir: str) -> str:
    """Reverse a kebab collection directory name to a collection FQCN.

    Splits on the first hyphen: namespace becomes the dot separator,
    remaining hyphens revert to underscores.
    ``netbox-netbox`` → ``netbox.netbox``
    ``fedora-linux-system-roles`` → ``fedora.linux_system_roles``
    """
    parts = kebab_dir.split("-", 1)
    if len(parts) == 2:
        return parts[0] + "." + parts[1].replace("-", "_")
    return parts[0]


def skill_dir_to_short_fqcn(kebab_dir: str) -> str:
    """Reverse a kebab skill directory name to the original short name.

    ``netbox-device`` → ``netbox_device``
    """
    return kebab_dir.replace("-", "_")


def module_to_skill_name(module_name: str) -> str:
    """Convert a module FQCN to a skill directory name."""
    return fqcn_to_skill_name(module_name)



def _module_template_context(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build the shared template context from module metadata."""
    fqcn = metadata["module_name"]
    params = metadata["params"]
    example_args = _build_example_args(params, metadata.get("examples", ""))
    ns, coll = split_collection_fqcn(fqcn)
    return {
        "spec_name": fqcn_to_skill_name(fqcn),
        "skill_name": fqcn.rsplit(".", 1)[-1],
        "short_description": metadata["short_description"],
        "params": params,
        "examples": metadata["examples"].strip() if metadata["examples"] else "",
        "example_args": example_args,
        "is_api_module": metadata.get("is_api_module", False),
        "examples_contain_play": _examples_contain_play(metadata.get("examples", "")),
        "fqcn": fqcn,
        "namespace": ns,
        "collection_name": coll,
        "plugin_type": "module",
        "doc_version": metadata.get("doc_version"),
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


def _list_nested_skills(
    collection_dir: Path,
    collection_fqcn: str,
) -> list[SkillEntry]:
    """List module/role/plugin skills within a collection directory."""
    from ansible_know.config import PLUGIN_TYPES

    results: list[SkillEntry] = []
    for sub_dir in sorted(collection_dir.iterdir()):
        try:
            skill_md = sub_dir / "SKILL.md"
            if sub_dir.is_dir() and not sub_dir.is_symlink() and skill_md.exists():
                dir_name = sub_dir.name
                match = PLUGIN_SKILL_DIR_RE.match(dir_name)
                if match and match.group(1) in PLUGIN_TYPES:
                    display_name = f"{collection_fqcn}.{skill_dir_to_short_fqcn(match.group(2))}"
                else:
                    display_name = f"{collection_fqcn}.{skill_dir_to_short_fqcn(dir_name)}"
                results.append({
                    "name": display_name,
                    "description": extract_skill_description(skill_md),
                    "path": str(sub_dir),
                })
        except OSError:
            logger.warning("Skipping unreadable skill: %s", sub_dir.name)
            continue
    return results


def _normalize_skills_dirs(skills_dirs: Path | Sequence[Path]) -> list[Path]:
    """Normalize a single Path or a sequence of Paths to a list."""
    if isinstance(skills_dirs, Path):
        return [skills_dirs]
    return list(skills_dirs)


def _list_skills_one_dir(
    skills_dir: Path, collection: str | None,
) -> list[SkillEntry]:
    """List skills from a single directory (no multi-path merge)."""
    results: list[SkillEntry] = []
    if not skills_dir.is_dir():
        return results

    if collection:
        collection_dir_name = collection_skill_name(collection)
        collection_dir = (skills_dir / collection_dir_name).resolve()
        validate_path_containment(collection_dir, skills_dir)
        if not collection_dir.is_dir():
            return results
        return _list_nested_skills(collection_dir, collection)

    for skill_dir in sorted(skills_dir.iterdir()):
        try:
            if not skill_dir.is_dir() or skill_dir.is_symlink():
                continue
            collection_fqcn = skill_dir_to_collection_fqcn(skill_dir.name)
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                results.append({
                    "name": collection_fqcn,
                    "description": extract_skill_description(skill_md),
                    "path": str(skill_dir),
                })
            results.extend(_list_nested_skills(skill_dir, collection_fqcn))
        except OSError:
            logger.warning("Skipping unreadable skill: %s", skill_dir.name)
            continue
    return results


def list_skills_sync(
    skills_dirs: Path | Sequence[Path],
    collection: str | None,
) -> list[SkillEntry]:
    """List generated skills from one or more skills directories.

    When *collection* is given, returns module/role/plugin skills within that
    collection directory.  Otherwise returns all skills: collection-level
    entries followed by their nested module/role/plugin skills.

    With multiple directories, results are merged in path order and deduplicated
    by skill name (first path wins).
    """
    merged: list[SkillEntry] = []
    seen: set[str] = set()
    for skills_dir in _normalize_skills_dirs(skills_dirs):
        for entry in _list_skills_one_dir(skills_dir, collection):
            name = entry["name"]
            if name in seen:
                continue
            seen.add(name)
            merged.append(entry)
    return merged


def _get_skill_one_dir(skills_dir: Path, skill_name: str) -> str | None:
    """Read a skill from *skills_dir*, or return None if missing."""
    parts = skill_name.split(".")
    if len(parts) >= 3:
        namespace = ".".join(parts[:2])
        short_name = ".".join(parts[2:])
        collection_dir_name = collection_skill_name(namespace)
        kebab_short = _to_kebab(short_name)

        nested_path = (skills_dir / collection_dir_name / kebab_short / "SKILL.md").resolve()
        validate_path_containment(nested_path, skills_dir)
        if nested_path.exists():
            return truncate_response(nested_path.read_text())

        from ansible_know.config import PLUGIN_TYPES
        for ptype in PLUGIN_TYPES:
            plugin_path = (
                skills_dir / collection_dir_name / f"{ptype}-{kebab_short}" / "SKILL.md"
            ).resolve()
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

    return None


def get_skill_sync(skills_dirs: Path | Sequence[Path], skill_name: str) -> str:
    """Read a skill's SKILL.md content from disk.

    Callers MUST validate *skill_name* with ``validate_skill_name()`` first.

    With multiple directories, searches in order and returns the first match.

    Raises:
        FileNotFoundError: If no matching SKILL.md exists in any directory.
        ValidationError: If a resolved path escapes its skills directory.
        OSError: On permission or I/O errors reading the file.
    """
    for skills_dir in _normalize_skills_dirs(skills_dirs):
        content = _get_skill_one_dir(skills_dir, skill_name)
        if content is not None:
            return content

    raise FileNotFoundError(f"Skill '{skill_name}' not found.")


def update_agents_md(project_root: Path, skills_dir: Path) -> None:
    """Write or update the managed AGENTS.md section listing generated skills."""
    validate_install_path(str(project_root))

    collections = []
    example_path = ""
    example_dir = ""
    if skills_dir.exists():
        for entry in sorted(skills_dir.iterdir()):
            try:
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if (entry / "SKILL.md").exists():
                    fqcn = skill_dir_to_collection_fqcn(entry.name)
                    collections.append(fqcn)
                    if not example_path:
                        for sub in sorted(entry.iterdir()):
                            if sub.is_dir() and not sub.is_symlink() and (sub / "SKILL.md").exists():
                                example_path = f"{fqcn}.{skill_dir_to_short_fqcn(sub.name)}"
                                example_dir = f"skills/{entry.name}/{sub.name}/SKILL.md"
                                break
            except OSError:
                continue

    example_line = ""
    if example_path:
        example_line = f"\n(e.g., `{example_path}` → `{example_dir}`)."
    else:
        example_line = "."

    section = (
        f"{_AGENTS_MD_START}\n"
        f"## Ansible Module Skills\n"
        f"\n"
        f"Generated Ansible module documentation skills are in `skills/`.\n"
        f"Before writing tasks for a module, check for a SKILL.md in the\n"
        f"matching collection and module directory{example_line}\n"
        f"\n"
        f"Available collections: {', '.join(collections)}\n"
        f"{_AGENTS_MD_END}\n"
    )

    agents_md_path = project_root / "AGENTS.md"

    with _agents_md_lock:
        if not agents_md_path.exists():
            agents_md_path.write_text(section)
            return

        content = agents_md_path.read_text()

        if _AGENTS_MD_START in content and _AGENTS_MD_END in content:
            start_idx = content.index(_AGENTS_MD_START)
            end_idx = content.index(_AGENTS_MD_END) + len(_AGENTS_MD_END)
            if content[end_idx:end_idx + 1] == "\n":
                end_idx += 1
            content = content[:start_idx] + section + content[end_idx:]
            agents_md_path.write_text(content)
            return

        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + section
        agents_md_path.write_text(content)


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
    fqcn = metadata["role_name"]
    ns, coll = split_collection_fqcn(fqcn)
    return {
        "spec_name": role_skill_name(fqcn),
        "skill_name": fqcn.rsplit(".", 1)[-1],
        "short_description": metadata.get("short_description", ""),
        "entry_points": metadata.get("entry_points", {}),
        "dependencies": metadata.get("dependencies", []),
        "examples": metadata.get("examples", "").strip(),
        "doc_source": metadata.get("doc_source", ""),
        "fqcn": fqcn,
        "namespace": ns,
        "collection_name": coll,
        "plugin_type": "role",
        "doc_version": metadata.get("doc_version"),
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
    fqcn = metadata["plugin_name"]
    ptype = metadata["plugin_type"]
    ns, coll = split_collection_fqcn(fqcn)
    return {
        "plugin_type": ptype,
        "spec_name": plugin_skill_name(fqcn, ptype),
        "skill_name": fqcn.rsplit(".", 1)[-1],
        "short_description": metadata.get("short_description", ""),
        "params": metadata.get("params", []),
        "examples": metadata.get("examples", "").strip(),
        "fqcn": fqcn,
        "namespace": ns,
        "collection_name": coll,
        "doc_version": metadata.get("doc_version"),
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
    collection_fqcn: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
) -> CollectionSkillContext:
    """Build template context for a collection-level skill."""
    ns, coll = split_collection_fqcn(collection_fqcn)

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
        "fqcn": collection_fqcn,
        "spec_name": collection_skill_name(collection_fqcn),
        "collection_version": collection_version,
        "modules_by_tag": modules_by_tag,
        "all_api": all_api,
        "common_params": common_params,
        "module_count": len(metadata_list),
        "plugins_by_type": plugins_by_type,
        "namespace": ns,
        "collection_name": coll,
        "plugin_type": "collection",
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
    collection_fqcn: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
) -> str:
    """Render the COLLECTION_SKILL.md.j2 template for a collection-level skill."""
    logger.debug("Rendering collection skill for %s", collection_fqcn)
    env = _get_template_env()
    template = env.get_template("COLLECTION_SKILL.md.j2")
    ctx = _collection_template_context(
        collection_fqcn, metadata_list, collection_version, plugins_metadata,
    )
    return template.render(**ctx)


def write_collection_skill_package(
    output_dir: Path,
    collection_fqcn: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[PluginManifestInput] | None = None,
) -> None:
    """Write the collection-level skill package: SKILL.md only (no scripts/assets)."""
    logger.debug("Writing collection skill package to %s for %s", output_dir, collection_fqcn)
    output_dir.mkdir(parents=True, exist_ok=True)

    content = render_collection_skill(
        collection_fqcn, metadata_list, collection_version, plugins_metadata,
    )
    (output_dir / "SKILL.md").write_text(content)


# --- Lola packaging (Layer 2 distribution wrap; does not change generate_* layout) ---


def default_lola_module_name(collection_fqcn: str) -> str:
    """Return the default Lola module directory name for a collection FQCN."""
    return f"ansible-{collection_skill_name(collection_fqcn)}"


def resolve_collection_skills_dir(
    skills_dirs: Path | Sequence[Path],
    collection_fqcn: str,
) -> Path:
    """Locate a generated collection skills directory across skills roots.

    Callers MUST validate *collection_fqcn* with ``validate_namespace()`` first.

    Raises:
        FileNotFoundError: If no matching collection directory exists.
        ValidationError: If a resolved path escapes its skills root.
    """
    collection_dir_name = collection_skill_name(collection_fqcn)
    for skills_dir in _normalize_skills_dirs(skills_dirs):
        if not skills_dir.is_dir():
            continue
        collection_dir = (skills_dir / collection_dir_name).resolve()
        validate_path_containment(collection_dir, skills_dir)
        if collection_dir.is_dir():
            return collection_dir
    raise FileNotFoundError(
        f"No generated skills found for collection '{collection_fqcn}'."
    )


def _ignore_symlinks(directory: str, names: list[str]) -> list[str]:
    """Exclude symlink entries so copytree never follows link targets."""
    base = Path(directory)
    return [name for name in names if (base / name).is_symlink()]


def _copy_skill_tree(src: Path, dest: Path, package_root: Path) -> None:
    """Copy one skill directory into a packaging root, replacing any prior copy."""
    validate_path_containment(dest.resolve(), package_root)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=False, ignore=_ignore_symlinks)


def _read_collection_version(collection_dir: Path) -> str | None:
    """Read ``collection_version`` from MANIFEST.json when present."""
    manifest_path = collection_dir / "MANIFEST.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable MANIFEST.json at %s: %s", manifest_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("collection_version")
    return version if isinstance(version, str) and version else None


def _write_lola_market_yml(
    module_root: Path,
    *,
    module_name: str,
    collection_fqcn: str,
    description: str,
    version: str | None,
    skill_count: int,
) -> Path:
    """Write optional marketplace metadata beside the Lola module skills tree."""
    namespace, collection_name = split_collection_fqcn(collection_fqcn)
    payload: LolaMarketYml = {
        "name": module_name,
        "description": description
        or f"Ansible skills for the {collection_fqcn} collection",
        "version": version or "0.0.0",
        "collection": collection_fqcn,
        "skill_count": skill_count,
        "tags": sorted({"ansible", namespace, collection_name.replace("_", "-")}),
    }
    market_path = module_root / "lola-market.yml"
    validate_path_containment(market_path.resolve(), module_root)
    market_path.write_text(
        yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
    )
    return market_path


def package_collection_for_lola(
    skills_dirs: Path | Sequence[Path],
    collection_fqcn: str,
    output_dir: Path,
    *,
    write_market_yml: bool = True,
    module_name: str | None = None,
) -> PackageForLolaResult:
    """Wrap generated collection skills into a Lola module directory.

    Source layout (unchanged)::

        {skills_dir}/{collection-kebab}/{skill}/SKILL.md

    Target Lola layout::

        {output_dir}/{module_name}/skills/{skill}/SKILL.md
        {output_dir}/{module_name}/lola-market.yml   # optional

    Nested skill directories that contain ``SKILL.md`` are copied with supporting
    files (symlink members are skipped). A collection-level ``SKILL.md`` is
    packaged as ``skills/{collection-kebab}/SKILL.md``. ``MANIFEST.json`` is
    not copied. Existing ``skills/`` under the module root is replaced.

    Contract:
        Preconditions:
            - Callers MUST validate *collection_fqcn* with
              ``validate_namespace()`` first.
            - *output_dir* must be an allowed install path (validated here via
              ``validate_install_path``).
            - When *module_name* is not ``None``, it must already be a valid
              Lola module name (or it is validated here). Empty string is
              invalid — pass ``None`` for the default name.
        Raises:
            FileNotFoundError: If the collection has no generated skills, or
                none of those skills contain a ``SKILL.md``.
            ValidationError: On path escape or invalid module name.
            OSError: On filesystem permission or I/O errors during mkdir,
                copy, or market-yml write (not silenced).
        Silences:
            - Unreadable nested skill dirs (``OSError`` while iterating /
              copying): logged and skipped; ``skill_count`` may omit them.
            - Unreadable ``MANIFEST.json`` or collection skill description:
              logged; version/description fall back to defaults.
    """
    resolved_output = validate_install_path(str(output_dir))
    if module_name is None:
        lola_name = default_lola_module_name(collection_fqcn)
    else:
        lola_name = module_name
    validate_lola_module_name(lola_name)

    collection_dir = resolve_collection_skills_dir(skills_dirs, collection_fqcn)
    collection_dir_name = collection_dir.name
    collection_skill_md = collection_dir / "SKILL.md"

    # Plan packable skills before mutating any existing Lola module output.
    has_collection_skill, nested_skill_dirs = _plan_packable_skill_dirs(collection_dir)
    if not has_collection_skill and not nested_skill_dirs:
        raise FileNotFoundError(
            f"Collection '{collection_fqcn}' has no SKILL.md packages to wrap."
        )

    module_root = (resolved_output / lola_name).resolve()
    validate_path_containment(module_root, resolved_output)
    module_root.mkdir(parents=True, exist_ok=True)

    skills_out = module_root / "skills"
    if skills_out.exists():
        validate_path_containment(skills_out.resolve(), module_root)
        shutil.rmtree(skills_out)
    skills_out.mkdir(parents=True)
    validate_path_containment(skills_out.resolve(), module_root)

    packaged: list[str] = []
    if has_collection_skill:
        dest = skills_out / collection_dir_name
        dest.mkdir(parents=True, exist_ok=True)
        validate_path_containment(dest.resolve(), module_root)
        shutil.copy2(collection_skill_md, dest / "SKILL.md")
        packaged.append(collection_dir_name)

    for entry in nested_skill_dirs:
        try:
            dest = skills_out / entry.name
            _copy_skill_tree(entry, dest, module_root)
            packaged.append(entry.name)
        except OSError as exc:
            logger.warning("Skipping unreadable skill dir %s: %s", entry.name, exc)
            continue

    if not packaged:
        raise FileNotFoundError(
            f"Collection '{collection_fqcn}' has no SKILL.md packages to wrap."
        )

    description = ""
    if collection_skill_md.is_file():
        try:
            description = extract_skill_description(collection_skill_md)
        except OSError as exc:
            logger.warning("Could not read collection skill description: %s", exc)

    market_path = module_root / "lola-market.yml"
    market_yml_path: str | None = None
    if write_market_yml:
        market_yml_path = str(
            _write_lola_market_yml(
                module_root,
                module_name=lola_name,
                collection_fqcn=collection_fqcn,
                description=description,
                version=_read_collection_version(collection_dir),
                skill_count=len(packaged),
            )
        )
    elif market_path.is_file():
        validate_path_containment(market_path.resolve(), module_root)
        market_path.unlink()

    logger.info(
        "Packaged %d skills for %s into Lola module %s",
        len(packaged),
        collection_fqcn,
        module_root,
    )
    return {
        "collection": collection_fqcn,
        "module_name": lola_name,
        "module_dir": str(module_root),
        "skill_count": len(packaged),
        "skills": packaged,
        "market_yml": market_yml_path,
    }


# --- Agent Plugins packaging (Layer 2; flatten only at package time) ---

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_ARCHIVE_VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
MAX_PLUGIN_KEYWORDS = 64


def default_plugin_name(collection_fqcn: str) -> str:
    """Return the default Agent Plugins ``name`` for a collection FQCN.

    Uses ``ansible-{collection-kebab}-agentplugin`` so artifacts are distinct
    from Galaxy/Pulp collection names and clearly target the Agent Plugins
    format. Fails closed if that value violates Agent Plugins §5.5 (e.g.
    longer than 64 characters) — callers must pass an explicit ``plugin_name``.

    Contract:
        Preconditions:
            - Callers SHOULD validate *collection_fqcn* with
              ``validate_namespace()`` first.
        Raises:
            ValidationError: If the default name fails ``validate_plugin_name``.
    """
    name = f"ansible-{collection_skill_name(collection_fqcn)}-agentplugin"
    validate_plugin_name(name)
    return name


def _plan_packable_skill_dirs(collection_dir: Path) -> tuple[bool, list[Path]]:
    """Plan collection-level + nested skill dirs for packaging wraps."""
    collection_dir_name = collection_dir.name
    collection_skill_md = collection_dir / "SKILL.md"
    has_collection_skill = collection_skill_md.is_file()

    nested_skill_dirs: list[Path] = []
    for entry in sorted(collection_dir.iterdir()):
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
            if has_collection_skill and entry.name == collection_dir_name:
                logger.warning(
                    "Skipping nested skill %r: name collides with collection skill",
                    entry.name,
                )
                continue
            if (entry / "SKILL.md").is_file():
                nested_skill_dirs.append(entry)
        except OSError as exc:
            logger.warning("Skipping unreadable skill dir %s: %s", entry.name, exc)
            continue
    return has_collection_skill, nested_skill_dirs


def _archive_version(version: str | None) -> str:
    """Return a filesystem-safe version segment for ``{name}-{version}.tar.gz``."""
    if version and _ARCHIVE_VERSION_RE.match(version):
        return version
    return "0.0.0"


def _build_plugin_keywords(
    collection_fqcn: str,
    skill_names: Sequence[str],
) -> list[str]:
    """Build registry-oriented keywords for ``plugin.json``.

    Includes ``ansible`` / ``automation``, namespace, collection kebab, collection
    FQCN, and packaged skill directory names (capped for manifest size).
    """
    namespace, collection_name = split_collection_fqcn(collection_fqcn)
    keywords: set[str] = {
        "ansible",
        "automation",
        namespace.lower(),
        collection_name.replace("_", "-").lower(),
        collection_fqcn.lower(),
    }
    for skill in skill_names:
        keywords.add(skill.lower())
    return sorted(keywords)[:MAX_PLUGIN_KEYWORDS]


_PLUGIN_ROOT_ENTRIES = frozenset({"plugin.json", "mcp.json", "skills"})


def _is_direct_child(path: Path, parent: Path) -> bool:
    """Return True if *path* is a direct child of *parent* (no symlink follow)."""
    try:
        return path.parent.resolve() == parent.resolve()
    except OSError:
        return False


def _unlink_symlink_or_file(path: Path, parent: Path) -> None:
    """Unlink a direct-child file or symlink without following the target."""
    if not _is_direct_child(path, parent):
        raise ValidationError("Path escapes the allowed directory.")
    if path.is_symlink() or path.is_file():
        path.unlink()


def _prepare_regular_file(path: Path, parent: Path) -> Path:
    """Ensure *path* is a writable regular file path under *parent*.

    Unlinks an existing symlink first so later writes cannot redirect through
    an in-tree link target.
    """
    if not _is_direct_child(path, parent):
        raise ValidationError("Path escapes the allowed directory.")
    if path.is_symlink():
        path.unlink()
    elif path.exists() and path.is_dir():
        raise ValidationError(f"Expected a file path, found a directory: {path.name}")
    return path


def _write_plugin_json(
    plugin_root: Path,
    *,
    plugin_name: str,
    collection_fqcn: str,
    description: str,
    version: str | None,
    skill_names: Sequence[str],
) -> Path:
    """Write closed-schema ``plugin.json`` under the plugin root."""
    payload: AgentPluginManifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": plugin_name,
        "version": version or "0.0.0",
        "description": description
        or f"Ansible skills for the {collection_fqcn} collection",
        "keywords": _build_plugin_keywords(collection_fqcn, skill_names),
    }
    plugin_json_path = _prepare_regular_file(plugin_root / "plugin.json", plugin_root)
    plugin_json_path.write_text(json.dumps(payload, indent=2) + "\n")
    return plugin_json_path


def _write_mcp_json(
    plugin_root: Path,
    *,
    mcp_transport: str = "stdio",
    mcp_url: str | None = None,
) -> Path:
    """Write Agent Plugins ``mcp.json`` for know-mcp (stdio or streamable-http)."""
    validate_mcp_transport(mcp_transport)
    server: AgentMcpServer
    if mcp_transport == "stdio":
        stdio_server: AgentMcpStdioServer = {
            "type": "stdio",
            "command": "uvx",
            "args": ["ansible-know-mcp"],
        }
        server = stdio_server
    else:
        if not mcp_url:
            raise ValidationError(
                "mcp_url is required when mcp_transport is 'streamable-http'."
            )
        validate_mcp_server_url(mcp_url)
        http_server: AgentMcpHttpServer = {
            "type": "streamable-http",
            "url": mcp_url,
        }
        server = http_server
    payload: AgentMcpConfig = {
        "$schema": MCP_SCHEMA,
        "mcpServers": {
            "ansible-know": server,
        },
    }
    mcp_json_path = _prepare_regular_file(plugin_root / "mcp.json", plugin_root)
    mcp_json_path.write_text(json.dumps(payload, indent=2) + "\n")
    return mcp_json_path


def _tar_safe_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Omit symlink/hardlink members and path-escape attempts from archives."""
    if tarinfo.issym() or tarinfo.islnk():
        return None
    if ".." in Path(tarinfo.name).parts:
        return None
    return tarinfo


def _scrub_plugin_root(plugin_root: Path) -> None:
    """Remove unexpected top-level entries before archiving a plugin root.

    Containment checks the directory entry under *plugin_root* without
    following symlink targets (absolute links must still be removable).
    Allowlisted names that are symlinks are also removed so later writes
    cannot redirect through them.
    """
    root = plugin_root.resolve()
    for entry in list(plugin_root.iterdir()):
        try:
            if entry.parent.resolve() != root:
                logger.warning(
                    "Skipping unexpected non-child entry under plugin root: %s",
                    entry.name,
                )
                continue
            if entry.name in _PLUGIN_ROOT_ENTRIES:
                if entry.is_symlink():
                    entry.unlink()
                continue
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
        except OSError as exc:
            logger.warning("Could not scrub plugin root entry %s: %s", entry.name, exc)


def _remove_plugin_archives(output_dir: Path, plugin_name: str) -> None:
    """Remove ``{plugin_name}-*.tar.gz`` files and symlinks under *output_dir*."""
    out = output_dir.resolve()
    for archive in sorted(output_dir.glob(f"{plugin_name}-*.tar.gz")):
        try:
            if not _is_direct_child(archive, out):
                continue
            # Unlink the directory entry itself (including symlinks) so writes
            # cannot follow an in-tree redirect into another output_dir file.
            if archive.is_symlink() or archive.is_file():
                archive.unlink()
        except (OSError, ValidationError) as exc:
            logger.warning("Could not remove stale archive %s: %s", archive.name, exc)


def _write_plugin_tarball(
    plugin_root: Path,
    output_dir: Path,
    *,
    plugin_name: str,
    version: str | None,
) -> Path:
    """Write ``{plugin_name}-{version}.tar.gz`` beside the plugin directory.

    Archives only allowlisted members (``plugin.json``, ``mcp.json``,
    ``skills/``) and filters out symlink/hardlink members. The archive path
    is opened as a literal child of *output_dir* after removing any prior
    file or symlink with that name.
    """
    _scrub_plugin_root(plugin_root)
    _remove_plugin_archives(output_dir, plugin_name)
    archive_name = f"{plugin_name}-{_archive_version(version)}.tar.gz"
    archive_path = _prepare_regular_file(output_dir / archive_name, output_dir)
    with tarfile.open(archive_path, "w:gz") as archive:
        for member_name in sorted(_PLUGIN_ROOT_ENTRIES):
            src = plugin_root / member_name
            if not src.exists() or src.is_symlink():
                continue
            archive.add(
                src,
                arcname=f"{plugin_name}/{member_name}",
                filter=_tar_safe_filter,
            )
    return archive_path


def package_as_agent_plugin(
    skills_dirs: Path | Sequence[Path],
    collection_fqcn: str,
    output_dir: Path,
    *,
    plugin_name: str | None = None,
    include_mcp_config: bool = True,
    write_plugin_json: bool = True,
    write_tarball: bool = True,
    mcp_transport: str = "stdio",
    mcp_url: str | None = None,
) -> PackageAsPluginResult:
    """Wrap generated collection skills into an Agent Plugins directory.

    Source layout (unchanged)::

        {skills_dir}/{collection-kebab}/{skill}/SKILL.md

    Target Agent Plugins layout::

        {output_dir}/{plugin_name}/plugin.json
        {output_dir}/{plugin_name}/mcp.json          # optional
        {output_dir}/{plugin_name}/skills/{skill}/SKILL.md
        {output_dir}/{plugin_name}-{version}.tar.gz  # optional artifact

    Nested skill directories that contain ``SKILL.md`` are copied with supporting
    files (symlink members are skipped). A collection-level ``SKILL.md`` is
    packaged as ``skills/{collection-kebab}/SKILL.md``. ``MANIFEST.json`` is
    not copied. Existing ``skills/`` under the plugin root is replaced.

    Contract:
        Preconditions:
            - Callers MUST validate *collection_fqcn* with
              ``validate_namespace()`` first.
            - *output_dir* must be an allowed install path (validated here via
              ``validate_install_path``).
            - When *plugin_name* is not ``None``, it must already satisfy
              Agent Plugins §5.5 (or it is validated here). Empty string is
              invalid — pass ``None`` for the default name.
            - When *include_mcp_config* is true, *mcp_transport* must be
              ``stdio`` or ``streamable-http``; *mcp_url* is required for
              ``streamable-http``.
            - *write_tarball* requires *write_plugin_json* (distribution
              artifacts must include required ``plugin.json``).
        Raises:
            FileNotFoundError: If the collection has no generated skills, or
                none of those skills contain a ``SKILL.md``.
            ValidationError: On path escape, invalid plugin name, invalid MCP
                transport/URL, or ``write_tarball`` without ``write_plugin_json``
                (including when the default name exceeds §5.5).
            OSError: On filesystem permission or I/O errors during mkdir,
                copy, or manifest write (not silenced).
        Silences:
            - Unreadable nested skill dirs (``OSError`` while iterating /
              copying): logged and skipped; ``skill_count`` may omit them.
            - Unreadable ``MANIFEST.json`` or collection skill description:
              logged; version/description fall back to defaults.
    """
    resolved_output = validate_install_path(str(output_dir))
    if plugin_name is None:
        name = default_plugin_name(collection_fqcn)
    else:
        name = plugin_name
        validate_plugin_name(name)
    if write_tarball and not write_plugin_json:
        raise ValidationError(
            "write_tarball requires write_plugin_json=True "
            "(Agent Plugins packages must include plugin.json)."
        )
    if include_mcp_config:
        validate_mcp_transport(mcp_transport)
        if mcp_transport == "streamable-http":
            if not mcp_url:
                raise ValidationError(
                    "mcp_url is required when mcp_transport is 'streamable-http'."
                )
            validate_mcp_server_url(mcp_url)

    collection_dir = resolve_collection_skills_dir(skills_dirs, collection_fqcn)
    collection_dir_name = collection_dir.name
    collection_skill_md = collection_dir / "SKILL.md"

    has_collection_skill, nested_skill_dirs = _plan_packable_skill_dirs(collection_dir)
    if not has_collection_skill and not nested_skill_dirs:
        raise FileNotFoundError(
            f"Collection '{collection_fqcn}' has no SKILL.md packages to wrap."
        )

    plugin_root = (resolved_output / name).resolve()
    validate_path_containment(plugin_root, resolved_output)
    plugin_root.mkdir(parents=True, exist_ok=True)

    skills_out = plugin_root / "skills"
    if skills_out.is_symlink():
        _unlink_symlink_or_file(skills_out, plugin_root)
    elif skills_out.exists():
        validate_path_containment(skills_out.resolve(), plugin_root)
        shutil.rmtree(skills_out)
    skills_out.mkdir(parents=True)
    validate_path_containment(skills_out.resolve(), plugin_root)

    packaged: list[str] = []
    if has_collection_skill:
        dest = skills_out / collection_dir_name
        dest.mkdir(parents=True, exist_ok=True)
        validate_path_containment(dest.resolve(), plugin_root)
        shutil.copy2(collection_skill_md, dest / "SKILL.md")
        packaged.append(collection_dir_name)

    for entry in nested_skill_dirs:
        try:
            dest = skills_out / entry.name
            _copy_skill_tree(entry, dest, plugin_root)
            packaged.append(entry.name)
        except OSError as exc:
            logger.warning("Skipping unreadable skill dir %s: %s", entry.name, exc)
            continue

    if not packaged:
        raise FileNotFoundError(
            f"Collection '{collection_fqcn}' has no SKILL.md packages to wrap."
        )

    description = ""
    if collection_skill_md.is_file():
        try:
            description = extract_skill_description(collection_skill_md)
        except OSError as exc:
            logger.warning("Could not read collection skill description: %s", exc)

    version = _read_collection_version(collection_dir)

    plugin_json_path: str | None = None
    plugin_json_file = plugin_root / "plugin.json"
    if write_plugin_json:
        plugin_json_path = str(
            _write_plugin_json(
                plugin_root,
                plugin_name=name,
                collection_fqcn=collection_fqcn,
                description=description,
                version=version,
                skill_names=packaged,
            )
        )
    elif plugin_json_file.is_symlink() or plugin_json_file.exists():
        _unlink_symlink_or_file(plugin_json_file, plugin_root)

    mcp_json_path: str | None = None
    mcp_json_file = plugin_root / "mcp.json"
    if include_mcp_config:
        mcp_json_path = str(
            _write_mcp_json(
                plugin_root,
                mcp_transport=mcp_transport,
                mcp_url=mcp_url,
            )
        )
    elif mcp_json_file.is_symlink() or mcp_json_file.exists():
        _unlink_symlink_or_file(mcp_json_file, plugin_root)

    archive_path: str | None = None
    if write_tarball:
        archive_path = str(
            _write_plugin_tarball(
                plugin_root,
                resolved_output,
                plugin_name=name,
                version=version,
            )
        )
    else:
        _remove_plugin_archives(resolved_output, name)
        _scrub_plugin_root(plugin_root)

    logger.info(
        "Packaged %d skills for %s into Agent Plugin %s",
        len(packaged),
        collection_fqcn,
        plugin_root,
    )
    return {
        "collection": collection_fqcn,
        "plugin_name": name,
        "plugin_dir": str(plugin_root),
        "skill_count": len(packaged),
        "skills": packaged,
        "plugin_json": plugin_json_path,
        "mcp_json": mcp_json_path,
        "archive": archive_path,
    }
