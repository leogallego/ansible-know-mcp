"""Input validation, error sanitization, and response truncation utilities."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from ansible_know.errors import ValidationError

__all__ = [
    "extract_collection_fqcn",
    "extract_namespace",
    "sanitize_error",
    "truncate_response",
    "validate_doc_url",
    "validate_fqcn",
    "validate_install_path",
    "validate_keyword",
    "validate_namespace",
    "validate_path_containment",
    "validate_plugin_type",
    "validate_query",
    "validate_skill_name",
    "validate_tags",
    "validate_version",
]

MAX_RESPONSE_SIZE = 500_000  # 500KB
MAX_KEYWORD_LENGTH = 200
MAX_QUERY_LENGTH = 500
MAX_NAMESPACE_LENGTH = 128
MAX_VERSION_LENGTH = 64
MAX_TAGS_LENGTH = 500
MAX_URL_LENGTH = 2048

_FQCN_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")
_NAMESPACE_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)?$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_TAGS_RE = re.compile(r"^[a-zA-Z0-9_,-]+$")
_SENSITIVE_PREFIXES = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev")
_PATH_RE = re.compile(r"/(?:home|tmp|usr|etc|var|opt)/\S+")


def validate_fqcn(name: str) -> None:
    if not name or not _FQCN_RE.match(name):
        raise ValidationError(
            "Invalid module name: expected format 'namespace.collection.module' "
            "with alphanumeric/underscore segments."
        )


MAX_SKILL_NAME_LENGTH = MAX_NAMESPACE_LENGTH * 2


def validate_skill_name(name: str) -> None:
    if not name or len(name) > MAX_SKILL_NAME_LENGTH or not _SKILL_NAME_RE.match(name):
        raise ValidationError(
            "Invalid skill name: expected a collection namespace (e.g. 'netbox.netbox') "
            "or a fully-qualified module name (e.g. 'netbox.netbox.netbox_device')."
        )


def validate_namespace(ns: str) -> None:
    if not ns or len(ns) > MAX_NAMESPACE_LENGTH or not _NAMESPACE_RE.match(ns):
        raise ValidationError(
            "Invalid collection namespace: expected format 'namespace.collection' "
            "with alphanumeric/underscore segments."
        )


def validate_keyword(keyword: str) -> None:
    if not keyword or not keyword.strip():
        raise ValidationError("Keyword must not be empty.")
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise ValidationError(
            f"Keyword too long: {len(keyword)} chars (max {MAX_KEYWORD_LENGTH})."
        )


def validate_version(version: str) -> None:
    if not version or len(version) > MAX_VERSION_LENGTH or not _VERSION_RE.match(version):
        raise ValidationError(
            "Invalid version format: use alphanumeric characters, dots, dashes only."
        )


def validate_query(query: str) -> None:
    if not query or not query.strip():
        raise ValidationError("Query must not be empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValidationError(
            f"Query too long: {len(query)} chars (max {MAX_QUERY_LENGTH})."
        )


def validate_tags(tags: str) -> None:
    if len(tags) > MAX_TAGS_LENGTH:
        raise ValidationError(
            f"Tags too long: {len(tags)} chars (max {MAX_TAGS_LENGTH})."
        )
    if not _TAGS_RE.match(tags):
        raise ValidationError(
            "Invalid tags: use alphanumeric characters, hyphens, underscores, and commas only."
        )


def validate_install_path(path_str: str) -> Path:
    resolved = Path(path_str).resolve()
    for prefix in _SENSITIVE_PREFIXES:
        if str(resolved).startswith(prefix):
            raise ValidationError(
                "Install path not allowed: cannot write to system directories."
            )
    return resolved


def validate_path_containment(child: Path, parent: Path) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValidationError("Path escapes the allowed directory.") from exc


def extract_collection_fqcn(fqcn: str) -> str | None:
    """Extract the 2-part collection FQCN from a fully-qualified name.

    Returns 'namespace.collection' from 'namespace.collection.name',
    or None if the name has no dots.
    """
    return ".".join(fqcn.split(".")[:2]) if "." in fqcn else None


def extract_namespace(fqcn: str) -> str | None:
    """Deprecated: use extract_collection_fqcn() instead."""
    return extract_collection_fqcn(fqcn)


def sanitize_error(msg: str) -> str:
    return _PATH_RE.sub("<path>", str(msg))


def truncate_response(text: str) -> str:
    if len(text) > MAX_RESPONSE_SIZE:
        return text[:MAX_RESPONSE_SIZE] + "\n\n[Truncated — response exceeded size limit]"
    return text


def validate_plugin_type(plugin_type: str) -> None:
    """Raise ValidationError if plugin_type is not a recognized ansible-doc type."""
    from ansible_know.config import PLUGIN_TYPES

    if plugin_type not in PLUGIN_TYPES:
        raise ValidationError(
            f"Invalid plugin type '{plugin_type}'. "
            f"Valid types: {', '.join(sorted(PLUGIN_TYPES))}"
        )


def validate_doc_url(url: str) -> None:
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            f"URL must be non-empty and under {MAX_URL_LENGTH} characters."
        )
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValidationError(f"Invalid URL format: {exc}") from exc
    if parsed.scheme != "https" or parsed.netloc != "docs.ansible.com":
        raise ValidationError(
            "URL must start with https://docs.ansible.com/"
        )
    if not parsed.path or parsed.path == "/":
        raise ValidationError(
            "URL must include a document path after https://docs.ansible.com/"
        )
