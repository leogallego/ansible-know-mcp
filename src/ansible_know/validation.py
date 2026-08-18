"""Input validation, error sanitization, and response truncation utilities."""

from __future__ import annotations

import ipaddress
import re
import warnings
from pathlib import Path
from urllib.parse import urlparse

from ansible_know.errors import ValidationError

__all__ = [
    "ALLOWED_DOC_HOSTS",
    "VALID_MCP_TRANSPORTS",
    "extract_collection_fqcn",
    "extract_namespace",
    "split_collection_fqcn",
    "sanitize_error",
    "truncate_response",
    "validate_doc_url",
    "validate_fqcn",
    "validate_install_path",
    "validate_keyword",
    "validate_lola_module_name",
    "validate_mcp_server_url",
    "validate_mcp_transport",
    "validate_namespace",
    "validate_path_containment",
    "validate_plugin_name",
    "validate_plugin_type",
    "validate_query",
    "validate_skill_name",
    "validate_standalone_role_name",
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
_LOLA_MODULE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
# Agent Plugins §5.5: lowercase alphanumeric, hyphen, period; no "--" / "..".
_PLUGIN_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_SENSITIVE_PREFIXES = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev")
_PATH_RE = re.compile(r"/(?:home|tmp|usr|etc|var|opt)/\S+")
# Allow "ansible-" + full kebab collection name (namespace max is 128).
MAX_LOLA_MODULE_NAME_LENGTH = MAX_NAMESPACE_LENGTH + len("ansible-")
MAX_PLUGIN_NAME_LENGTH = 64


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


_STANDALONE_ROLE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def validate_standalone_role_name(name: str) -> None:
    """Validate a Galaxy standalone role identifier ``namespace.role``."""
    if not name or "/" in name or "\\" in name:
        raise ValidationError(
            "Invalid standalone role name: expected format 'namespace.role' "
            "with alphanumeric, hyphen, or underscore segments."
        )
    parts = name.split(".")
    if len(parts) == 3:
        raise ValidationError(
            "Invalid standalone role name: expected 'namespace.role'. "
            "Collection roles use get_role_doc() with a 3-part FQCN."
        )
    if len(parts) != 2:
        raise ValidationError(
            "Invalid standalone role name: expected format 'namespace.role' "
            "with alphanumeric, hyphen, or underscore segments."
        )
    for part in parts:
        if (
            not part
            or len(part) > MAX_NAMESPACE_LENGTH
            or not _STANDALONE_ROLE_SEGMENT_RE.match(part)
        ):
            raise ValidationError(
                "Invalid standalone role name: expected format 'namespace.role' "
                "with alphanumeric, hyphen, or underscore segments."
            )


def validate_lola_module_name(name: str) -> None:
    """Validate a Lola module directory / market ``name`` field.

    Rejects empty values, path separators, and names that are not safe as a
    single path segment under the packaging output directory.

    Contract:
        Preconditions:
            - ``name`` must be a non-empty single path segment (1–
              ``MAX_LOLA_MODULE_NAME_LENGTH`` chars).
            - Allowed characters: alphanumeric, ``.``, ``_``, ``-``;
              first character alphanumeric. No ``/``, ``\\``, ``.``, or ``..``.
        Raises:
            ValidationError: If any precondition fails (explicit check).
    """
    if (
        not name
        or len(name) > MAX_LOLA_MODULE_NAME_LENGTH
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or not _LOLA_MODULE_NAME_RE.match(name)
    ):
        raise ValidationError(
            "Invalid Lola module name: use 1-"
            f"{MAX_LOLA_MODULE_NAME_LENGTH} alphanumeric characters, "
            "dots, underscores, or hyphens (no path separators)."
        )


def validate_plugin_name(name: str) -> None:
    """Validate an Agent Plugins ``plugin.json`` ``name`` field (§5.5).

    Contract:
        Preconditions:
            - ``name`` length is 1–``MAX_PLUGIN_NAME_LENGTH`` (64).
            - Character set is lowercase ``a-z``, ``0-9``, ``-``, ``.`` only.
            - First and last characters are alphanumeric.
            - No consecutive ``--`` or ``..``.
            - No path separators (``/``, ``\\``).
        Raises:
            ValidationError: If any precondition fails (explicit check).
    """
    if (
        not name
        or len(name) > MAX_PLUGIN_NAME_LENGTH
        or "/" in name
        or "\\" in name
        or "--" in name
        or ".." in name
        or not _PLUGIN_NAME_RE.match(name)
    ):
        raise ValidationError(
            "Invalid plugin name: use 1-"
            f"{MAX_PLUGIN_NAME_LENGTH} lowercase alphanumeric characters, "
            "hyphens, or periods; must start and end alphanumeric; "
            "no '--' or '..' (Agent Plugins §5.5)."
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


def split_collection_fqcn(fqcn: str) -> tuple[str, str]:
    """Split a FQCN into (namespace, collection_name).

    Returns ('namespace', 'collection') from 'namespace.collection.name'.
    For dotless input, returns (fqcn, fqcn) as a safe fallback.
    """
    parts = fqcn.split(".")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return fqcn, fqcn


def extract_namespace(fqcn: str) -> str | None:
    """Deprecated: use extract_collection_fqcn() instead."""
    warnings.warn(
        "extract_namespace() is deprecated, use extract_collection_fqcn()",
        DeprecationWarning,
        stacklevel=2,
    )
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


ALLOWED_DOC_HOSTS = frozenset({"docs.ansible.com", "docs.redhat.com"})
VALID_MCP_TRANSPORTS = frozenset({"stdio", "streamable-http"})


def validate_mcp_transport(transport: str) -> None:
    """Validate an Agent Plugins ``mcp.json`` transport type.

    Contract:
        Preconditions:
            - ``transport`` must be ``stdio`` or ``streamable-http``.
        Raises:
            ValidationError: If the transport is unsupported.
    """
    if transport not in VALID_MCP_TRANSPORTS:
        raise ValidationError(
            "Invalid MCP transport: use 'stdio' or 'streamable-http'."
        )


def validate_mcp_server_url(url: str) -> None:
    """Validate an Agent Plugins remote MCP URL (§7.2.1).

    Absolute HTTP(S) URL, no userinfo or fragment. Non-loopback hosts MUST
    use HTTPS; HTTP is allowed only for ``localhost`` or loopback IPs.

    Contract:
        Preconditions:
            - Non-empty URL under ``MAX_URL_LENGTH``.
            - Scheme ``https``, or ``http`` only for loopback.
            - No username/password/fragment.
        Raises:
            ValidationError: If any precondition fails.
    """
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            f"MCP URL must be non-empty and under {MAX_URL_LENGTH} characters."
        )
    if url != url.strip() or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ValidationError(
            "MCP URL must not contain whitespace or control characters."
        )
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValidationError(f"Invalid MCP URL format: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("MCP URL must use http or https.")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValidationError("MCP URL must not include userinfo or a fragment.")
    host = parsed.hostname
    if not host:
        raise ValidationError("MCP URL must include a host.")
    if parsed.scheme == "http":
        if host == "localhost":
            return
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValidationError(
                    "HTTP MCP URLs are only allowed for localhost or loopback IPs."
                )
        except ValueError as exc:
            raise ValidationError(
                "HTTP MCP URLs are only allowed for localhost or loopback IPs."
            ) from exc


def validate_doc_url(url: str) -> None:
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            f"URL must be non-empty and under {MAX_URL_LENGTH} characters."
        )
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValidationError(f"Invalid URL format: {exc}") from exc
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_DOC_HOSTS:
        raise ValidationError(
            "URL must start with https://docs.ansible.com/ or https://docs.redhat.com/"
        )
    if not parsed.path or parsed.path == "/":
        raise ValidationError(
            "URL must include a document path after the domain."
        )
