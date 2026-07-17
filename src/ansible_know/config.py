"""Paths, constants, and environment variable defaults."""

from __future__ import annotations

import json
import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

TEMPLATE_DIR = _PKG_DIR / "templates"


def _get_cache_dir() -> Path:
    """Return the disk cache directory, respecting XDG and env overrides."""
    explicit = os.environ.get("ANSIBLE_KNOW_CACHE_DIR")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ansible-know"


CACHE_DIR = _get_cache_dir()

try:
    USER_AGENT = (
        f"ansible-know-mcp/{pkg_version('ansible-know-mcp')}"
        " (+https://github.com/leogallego/ansible-know-mcp)"
    )
except PackageNotFoundError:
    USER_AGENT = "ansible-know-mcp/unknown (+https://github.com/leogallego/ansible-know-mcp)"


def get_project_root() -> Path:
    """Return the project root from env vars, or cwd as fallback."""
    for var in ("ANSIBLE_KNOW_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(value)
    return Path.cwd()


def __getattr__(name: str):
    if name == "SKILLS_DIR":
        explicit = os.environ.get("ANSIBLE_KNOW_SKILLS_DIR", "").strip()
        if explicit:
            value = Path(explicit)
        else:
            root = get_project_root()
            value = root / "skills"
        globals()["SKILLS_DIR"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


AUDIENCE_MAP: dict[str, str] = {
    "dev_guide": "developer",
    "playbook_guide": "author",
    "inventory_guide": "author",
    "getting_started": "author",
    "getting_started_ee": "author",
    "vault_guide": "author",
    "plugins": "both",
    "tips_tricks": "author",
    "command_guide": "author",
}

GUIDE_TOPIC_PREFIXES: set[str] = {
    "playbook_guide",
    "inventory_guide",
    "dev_guide",
    "vault_guide",
    "getting_started",
    "getting_started_ee",
    "reference_appendices",
    "porting_guides",
    "collections_guide",
    "command_guide",
    "tips_tricks",
    "network",
    "os_guide",
    "plugins",
    "scenario_guides",
    "user_guide",
    "community",
    "roadmap",
}

CORE_PAGES: dict[str, list[str]] = {
    "ansible": [
        "playbook_guide/playbooks_intro.html",
        "playbook_guide/playbooks_variables.html",
        "playbook_guide/playbooks_loops.html",
        "playbook_guide/playbooks_conditionals.html",
        "playbook_guide/playbooks_error_handling.html",
        "playbook_guide/playbooks_reuse_roles.html",
        "playbook_guide/playbooks_handlers.html",
        "playbook_guide/playbooks_blocks.html",
        "playbook_guide/playbooks_filters.html",
        "playbook_guide/playbooks_tests.html",
        "playbook_guide/playbooks_vars_facts.html",
        "playbook_guide/playbooks_tags.html",
        "playbook_guide/playbooks_privilege_escalation.html",
        "inventory_guide/intro_inventory.html",
        "inventory_guide/intro_dynamic_inventory.html",
        "inventory_guide/intro_patterns.html",
        "inventory_guide/connection_details.html",
        "vault_guide/vault_encrypting_content.html",
        "vault_guide/vault_managing_passwords.html",
        "vault_guide/vault_using_encrypted_content.html",
        "reference_appendices/config.html",
        "reference_appendices/playbooks_keywords.html",
        "reference_appendices/special_variables.html",
        "reference_appendices/general_precedence.html",
        "collections_guide/collections_using.html",
        "collections_guide/collections_installing.html",
        "dev_guide/developing_collections.html",
        "dev_guide/developing_modules_general.html",
        "dev_guide/developing_plugins.html",
        "dev_guide/testing.html",
        "dev_guide/developing_collections_structure.html",
        "getting_started/get_started_playbook.html",
        "getting_started/basic_concepts.html",
        "getting_started/get_started_inventory.html",
    ],
    "lint": [
        "",
        "configuring/",
        "rules/",
        "profiles/",
        "usage/",
    ],
    "navigator": [
        "",
        "installation/",
        "settings/",
        "subcommands/",
    ],
    "builder": [
        "",
        "definition/",
        "usage/",
    ],
    "creator": [
        "",
        "content_creation/",
        "ee_scaffolding/",
    ],
    "molecule": [
        "",
        "getting-started-collections/",
        "configuration/",
        "usage/",
    ],
}

PROJECT_BASE_URLS: dict[str, str] = {
    "ansible": "https://docs.ansible.com/projects/ansible/latest",
    "lint": "https://docs.ansible.com/projects/lint",
    "navigator": "https://docs.ansible.com/projects/navigator",
    "builder": "https://docs.ansible.com/projects/builder/en/latest",
    "creator": "https://docs.ansible.com/projects/creator",
    "molecule": "https://docs.ansible.com/projects/molecule",
}

RTD_PROJECT_SLUGS: dict[str, str] = {
    "ansible-core": "package-doc-builds",
    "ansible-lint": "ansible-lint",
    "ansible-navigator": "ansible-navigator",
    "ansible-builder": "ansible-builder",
    "ansible-creator": "ansible-creator",
    "molecule": "molecule",
}

DEFAULT_DOC_SOURCES: dict[str, dict[str, str]] = {
    "ansible-core": {
        "file": str(_PKG_DIR / "data" / "ansible_core_manifest.json"),
        "description": "Ansible core — playbook guides, inventory, vault, developer guides, reference",
    },
    "ansible-lint": {
        "file": str(_PKG_DIR / "data" / "ansible_lint_manifest.json"),
        "description": "ansible-lint — rules, configuration, profiles",
    },
    "ansible-navigator": {
        "file": str(_PKG_DIR / "data" / "ansible_navigator_manifest.json"),
        "description": "ansible-navigator — settings, subcommands",
    },
    "ansible-builder": {
        "file": str(_PKG_DIR / "data" / "ansible_builder_manifest.json"),
        "description": "ansible-builder — EE definitions, scenarios, usage",
    },
    "ansible-creator": {
        "file": str(_PKG_DIR / "data" / "ansible_creator_manifest.json"),
        "description": "ansible-creator — content creation, EE scaffolding",
    },
    "molecule": {
        "file": str(_PKG_DIR / "data" / "molecule_manifest.json"),
        "description": "molecule — test scenarios, configuration, getting started",
    },
    "aap-2.5": {
        "file": str(_PKG_DIR / "data" / "aap_25_manifest.json"),
        "description": "Red Hat AAP 2.5 — installation, configuration, operations, troubleshooting",
    },
    "aap-2.6": {
        "file": str(_PKG_DIR / "data" / "aap_26_manifest.json"),
        "description": "Red Hat AAP 2.6 — installation, mesh, EE, RBAC, AI features, MCP server",
    },
    "aap-2.7": {
        "file": str(_PKG_DIR / "data" / "aap_27_manifest.json"),
        "description": "Red Hat AAP 2.7 — installation, mesh, self-service, metrics, AI features",
    },
}

def get_doc_sources() -> dict[str, dict[str, str]]:
    """Return configured documentation manifest sources.

    Override defaults via ANSIBLE_KNOW_DOC_SOURCES env var (JSON).
    Falls back to defaults on invalid JSON or malformed structure.
    """
    env_val = os.environ.get("ANSIBLE_KNOW_DOC_SOURCES")
    if env_val:
        try:
            parsed = json.loads(env_val)
        except (json.JSONDecodeError, ValueError):
            logging.getLogger("ansible_know").warning(
                "Invalid JSON in ANSIBLE_KNOW_DOC_SOURCES, using defaults"
            )
            return DEFAULT_DOC_SOURCES
        if not isinstance(parsed, dict):
            logging.getLogger("ansible_know").warning(
                "ANSIBLE_KNOW_DOC_SOURCES must be a JSON object, using defaults"
            )
            return DEFAULT_DOC_SOURCES
        return parsed
    return DEFAULT_DOC_SOURCES

SEARCH_MODULES_LIMIT = 50
SEARCH_DOCS_LIMIT = 20

GALAXY_BASE_URL = os.environ.get(
    "ANSIBLE_KNOW_GALAXY_URL",
    "https://galaxy.ansible.com",
)

REDHAT_DOCS_MCP_URL = os.environ.get(
    "ANSIBLE_KNOW_REDHAT_DOCS_MCP_URL",
    "https://docs-mcp.api.redhat.com/mcp",
)

PLUGIN_TYPES: tuple[str, ...] = (
    "become", "cache", "callback", "cliconf", "connection",
    "filter", "httpapi", "inventory", "lookup", "netconf",
    "shell", "strategy", "test", "vars",
)

JINJA2_PLUGIN_TYPES: tuple[str, ...] = ("filter", "lookup", "test")

PLAYBOOK_PLUGIN_TYPES: tuple[str, ...] = (
    "become", "callback", "connection", "inventory", "strategy",
)

INFRA_PLUGIN_TYPES: tuple[str, ...] = (
    "cache", "cliconf", "httpapi", "netconf", "shell", "vars",
)
