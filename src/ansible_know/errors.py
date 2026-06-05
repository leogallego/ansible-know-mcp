"""Unified exception hierarchy for ansible-know."""

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
