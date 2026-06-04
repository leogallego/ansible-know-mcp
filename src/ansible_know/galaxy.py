"""Galaxy v3 API client.

Searches collections, fetches documentation blobs, and resolves versions
from Ansible Galaxy without requiring local collection installation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ansible_know.config import GALAXY_BASE_URL

logger = logging.getLogger("ansible_know")

_COMPONENT_RE_PATTERN = r"^[a-zA-Z0-9_]+$"

MAX_GALAXY_RESPONSE_SIZE = 5_000_000  # 5MB

_version_cache: dict[tuple[str, str], str] = {}
_blob_cache: dict[tuple[str, str, str], dict[str, Any]] = {}


def clear_cache() -> None:
    """Clear Galaxy caches (useful for testing)."""
    _version_cache.clear()
    _blob_cache.clear()


class GalaxyError(Exception):
    """Raised when a Galaxy API request fails."""


def _validate_component(value: str, label: str) -> None:
    """Validate a namespace or name component for safe URL interpolation."""
    if not value or not re.match(_COMPONENT_RE_PATTERN, value):
        raise GalaxyError(f"Invalid {label}: '{value}'")


class GalaxyClient:
    """Async client for the Galaxy v3 API."""

    def __init__(self, base_url: str | None = None):
        self._base = (base_url or GALAXY_BASE_URL).rstrip("/")

    async def _api_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        if client is not None:
            resp = await client.get(
                url, params=params, headers={"Accept": "application/json"},
            )
        else:
            async with httpx.AsyncClient(timeout=30, verify=True) as c:
                resp = await c.get(
                    url, params=params, headers={"Accept": "application/json"},
                )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GalaxyError(
                f"Galaxy API error (HTTP {exc.response.status_code})"
            )
        if len(resp.content) > MAX_GALAXY_RESPONSE_SIZE:
            raise GalaxyError("Galaxy API response too large")
        return resp.json()

    async def _safe_api_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Wrap _api_get with network error handling."""
        try:
            return await self._api_get(path, params=params, client=client)
        except httpx.TimeoutException:
            raise GalaxyError("Galaxy connection timed out")
        except httpx.RequestError as exc:
            raise GalaxyError(f"Galaxy connection error: {type(exc).__name__}")

    async def latest_version(self, namespace: str, name: str) -> str:
        """Resolve the latest version of a collection on Galaxy.

        Args:
            namespace: Collection namespace (e.g. 'netbox').
            name: Collection name (e.g. 'netbox').

        Returns:
            Version string (e.g. '3.23.0').

        Raises:
            GalaxyError: If the collection is not found or the API fails.
        """
        _validate_component(namespace, "namespace")
        _validate_component(name, "name")
        cache_key = (namespace, name)
        if cache_key in _version_cache:
            return _version_cache[cache_key]
        path = (
            f"/api/v3/plugin/ansible/content/published/collections/index/"
            f"{namespace}/{name}/versions/"
        )
        params = {"limit": "1", "ordering": "-version", "format": "json"}
        data = await self._safe_api_get(path, params=params)
        versions = data.get("data", [])
        if not versions:
            raise GalaxyError(
                f"No versions found for {namespace}.{name} on Galaxy."
            )
        version = versions[0]["version"]
        _version_cache[cache_key] = version
        return version
