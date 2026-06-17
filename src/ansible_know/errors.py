"""Unified exception hierarchy and error helpers for ansible-know."""

from __future__ import annotations


class AnsibleKnowError(Exception):
    """Base exception for all ansible-know errors."""


class AnsibleDocError(AnsibleKnowError):
    """Raised when ansible-doc fails or returns unexpected output."""


class CollectionNotFoundError(AnsibleDocError):
    """Module or collection not found locally — triggers Galaxy fallback."""


class GalaxyError(AnsibleKnowError):
    """Raised when a Galaxy API request fails."""


class CollectionInstallError(AnsibleKnowError):
    """Raised when ansible-galaxy collection install fails."""


class ValidationError(AnsibleKnowError):
    """Raised when tool input fails validation."""


_MISSING_COLLECTION_PATTERNS = ("has no attribute", "was not found", "could not be found")


def collection_hint(namespace: str) -> str:
    return (
        f" Collection '{namespace}' not installed locally. "
        f"Use ensure_collection('{namespace}') to install it from Ansible Galaxy "
        f"(latest version, or specify version='X.Y.Z')."
    )


def is_missing_collection_error(error_msg: str) -> bool:
    """Check if an error message indicates a missing/not-found collection or module."""
    msg_lower = error_msg.lower()
    return any(p in msg_lower for p in _MISSING_COLLECTION_PATTERNS)


def maybe_add_hint(error_msg: str, namespace: str | None) -> str:
    if namespace and is_missing_collection_error(error_msg):
        return error_msg + collection_hint(namespace)
    return error_msg
