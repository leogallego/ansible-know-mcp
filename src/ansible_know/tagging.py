"""Tag derivation from module metadata (Foundation layer — no internal dependencies)."""

from __future__ import annotations

from typing import Any

__all__ = ["derive_tags"]


def derive_tags(fqcn: str, params: list[dict[str, Any]]) -> list[str]:
    """Heuristically derive tags from module name segments and parameters.

    Analyzes the module's fully-qualified collection name (FQCN) to extract
    semantic tags based on common naming patterns. Splits the module short name
    on underscores and maps each segment to a category tag when a match is found
    in the predefined tag hints dictionary.

    Args:
        fqcn: Fully-qualified collection name (e.g., "netbox.netbox.ip_address").
        params: List of parameter dictionaries (currently unused, reserved for
            future tag derivation from parameter metadata).

    Returns:
        Sorted list of unique tag strings derived from the module name.
        Returns empty list if no segments match any tag hints.

    Contract:
        Preconditions:
            - `fqcn` must be a string. If not, raises `AttributeError` when
              calling `.split()` (implicit, line 59).
            - `params` must be a list (not validated, but reserved for future use).

        Raises:
            AttributeError: If `fqcn` is not a string (implicit from `.split()`).

        Notes:
            - Always returns a valid list (empty if no matches).
            - Tag matching is case-insensitive via `.lower()` on segments.
            - Multiple segments may map to the same tag — deduplication via `set`.
            - Params parameter is reserved for future enhancement but currently
              ignored.
    """
    parts = fqcn.split(".")
    module_short = parts[-1] if parts else fqcn

    tags: set[str] = set()
    tag_hints = {
        "user": "identity", "group": "identity", "role": "identity",
        "network": "networking", "interface": "networking", "vlan": "networking",
        "firewall": "security", "acl": "security", "cert": "security",
        "file": "files", "copy": "files", "template": "files",
        "package": "packages", "apt": "packages", "yum": "packages", "dnf": "packages",
        "service": "services", "systemd": "services",
        "docker": "containers", "podman": "containers", "container": "containers",
        "ip": "ipam", "prefix": "ipam", "subnet": "ipam", "address": "ipam",
        "device": "dcim", "rack": "dcim", "site": "dcim",
        "vm": "virtualization", "virtual": "virtualization",
        "cloud": "cloud", "ec2": "cloud", "azure": "cloud", "gcp": "cloud",
        "db": "database", "database": "database", "mysql": "database", "postgres": "database",
    }

    for segment in module_short.split("_"):
        segment_lower = segment.lower()
        if segment_lower in tag_hints:
            tags.add(tag_hints[segment_lower])

    return sorted(tags)
