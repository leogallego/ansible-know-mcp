"""Skill rendering and package writing.

Extracted from ansibleclawed cli.py — generates SKILL.md skill packages
from Ansible module metadata.
"""

from __future__ import annotations

import logging
import stat
from pathlib import Path
from typing import Any

from ansible_know.config import TEMPLATE_DIR

logger = logging.getLogger("ansible_know")


def _get_template_env():
    """Create a Jinja2 environment pointed at the templates directory."""
    from jinja2 import Environment, FileSystemLoader

    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _module_to_skill_name(module_name: str) -> str:
    """Convert a module FQCN to a skill directory name."""
    return module_name


def _template_context(metadata: dict[str, Any]) -> dict[str, Any]:
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


def render_skill(metadata: dict[str, Any]) -> str:
    """Render the SKILL.md template with the given module metadata."""
    logger.debug("Rendering skill for module %s", metadata.get("module_name", "?"))
    env = _get_template_env()
    template = env.get_template("SKILL.md.j2")
    return template.render(**_template_context(metadata))


def write_skill_package(output_dir: Path, metadata: dict[str, Any]) -> None:
    """Write the full skill package: SKILL.md + scripts + assets."""
    logger.debug("Writing skill package to %s for %s", output_dir, metadata.get("module_name", "?"))
    env = _get_template_env()
    ctx = _template_context(metadata)

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


def _build_example_args(params: list[dict[str, Any]], examples_yaml: str = "") -> str:
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
