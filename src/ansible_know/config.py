"""Paths, constants, and environment variable defaults."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

TEMPLATE_DIR = _PKG_DIR / "templates"

SKILLS_DIR = Path(os.environ.get(
    "ANSIBLE_KNOW_SKILLS_DIR",
    Path.cwd() / "skills",
))

DEFAULT_DOC_SOURCES: dict[str, dict[str, str]] = {
    "ansible-core": {
        "url": "https://raw.githubusercontent.com/leogallego/ansible-documentation/ai-docs/manifest.json",
        "description": "Ansible core documentation — playbook guides, developer guides, reference",
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
