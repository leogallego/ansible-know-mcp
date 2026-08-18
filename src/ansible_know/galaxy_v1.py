"""Galaxy v1 API client for standalone roles."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from ansible_know.cache import BoundedCache
from ansible_know.config import GALAXY_BASE_URL
from ansible_know.errors import GalaxyError

if TYPE_CHECKING:
    from ansible_know.galaxy_config import GalaxyServerConfig
    from ansible_know.types import DocProvenance

logger = logging.getLogger("ansible_know")

MAX_GALAXY_RESPONSE_SIZE = 5_000_000  # 5MB
CACHE_TTL_SECONDS = 3600

TIMEOUT_FAST = httpx.Timeout(10.0)
TIMEOUT_DEFAULT = httpx.Timeout(10.0, read=30.0)
TIMEOUT_SLOW = httpx.Timeout(10.0, read=120.0)

MAX_DISCOVERY_RESPONSE_SIZE = 100_000  # 100KB — discovery payloads are tiny

_SAFE_V1_PATH_RE = re.compile(r"^[a-zA-Z0-9/_-]+/?$")

_v1_cache: BoundedCache[tuple[str, ...], dict[str, Any]] = BoundedCache(
    max_size=50, ttl=CACHE_TTL_SECONDS,
)


def _normalize_cache_base_url(url: str) -> str:
    """Normalize a Galaxy/Hub URL for cache partitioning."""
    base = url.rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")].rstrip("/")
    return base


def clear_cache() -> None:
    """Clear Galaxy v1 standalone role cache (useful for testing)."""
    _v1_cache.clear()


def _first_tag(tags: str | None) -> str | None:
    if not tags:
        return None
    return tags.split(",", 1)[0].strip() or None


def _normalize_dependencies(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict):
            ns = item.get("namespace") or item.get("username") or ""
            name = item.get("name") or ""
            if ns and name:
                out.append(f"{ns}.{name}")
            elif name:
                out.append(str(name))
    return out


def _map_search_hit(item: dict[str, Any]) -> dict[str, Any]:
    versions = (item.get("summary_fields") or {}).get("versions") or []
    latest = ""
    if versions and isinstance(versions[0], dict):
        latest = versions[0].get("name") or ""
    tags_raw = (item.get("summary_fields") or {}).get("tags") or []
    tags: list[str] = []
    for t in tags_raw:
        if isinstance(t, str):
            tags.append(t)
        elif isinstance(t, dict) and t.get("name"):
            tags.append(str(t["name"]))
    username = item.get("username") or ""
    name = item.get("name") or ""
    return {
        "role_name": f"{username}.{name}",
        "description": item.get("description") or "",
        "tags": tags,
        "latest_version": latest,
        "download_count": item.get("download_count") or 0,
        "github_user": item.get("github_user") or "",
        "github_repo": item.get("github_repo") or "",
    }


class GalaxyV1Client:
    """Async client for the Galaxy v1 API (standalone roles)."""

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify: bool = True,
        server_name: str | None = None,
        auth_url: str | None = None,
        client_id: str | None = None,
    ):
        self._base = (base_url or GALAXY_BASE_URL).rstrip("/")
        self._http_client = http_client
        self._owned_client: httpx.AsyncClient | None = None
        self._token = token
        self._username = username
        self._password = password
        self._verify = verify
        self.server_name = server_name
        self._auth_url = auth_url
        self._client_id = client_id
        self._access_token: str | None = None
        self._token_lock = asyncio.Lock()
        self._api_root: str | None = None
        self._v1_path: str | None = None
        self._discovery_lock = asyncio.Lock()
        self._discovery_failed: bool = False

    def _cache_identity(self) -> tuple[str, str, str]:
        transport_identity = str(
            id(self._http_client if self._http_client is not None else self._owned_client)
        )
        return (
            _normalize_cache_base_url(self._base),
            self.server_name or "",
            transport_identity,
        )

    @classmethod
    def from_config(
        cls,
        config: GalaxyServerConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> GalaxyV1Client:
        """Create a GalaxyV1Client from a GalaxyServerConfig."""
        return cls(
            base_url=config.url,
            http_client=http_client,
            token=config.token,
            username=config.username,
            password=config.password,
            verify=config.validate_certs,
            server_name=config.name,
            auth_url=config.auth_url,
            client_id=config.client_id,
        )

    async def __aenter__(self) -> GalaxyV1Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the owned httpx client, if any."""
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get the http client to use for requests."""
        if self._http_client is not None:
            return self._http_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=TIMEOUT_SLOW,
                verify=self._verify,
                follow_redirects=True,
            )
        return self._owned_client

    async def _ensure_access_token(self) -> str:
        """Exchange offline token for access token via SSO, with caching."""
        if self._access_token is not None:
            return self._access_token
        if self._auth_url is None:
            raise GalaxyError("Cannot exchange SSO token without auth_url")
        if not self._auth_url.startswith("https://"):
            logger.warning(
                "auth_url for server '%s' is not HTTPS — "
                "credentials may be sent in cleartext",
                self.server_name or "default",
            )

        async with self._token_lock:
            if self._access_token is not None:
                return self._access_token

            client = self._get_client()
            try:
                resp = await client.post(
                    self._auth_url,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id or "cloud-services",
                        "refresh_token": self._token,
                    },
                    timeout=TIMEOUT_FAST,
                )
                resp.raise_for_status()
                token = resp.json().get("access_token")
                if not isinstance(token, str) or not token:
                    raise GalaxyError(
                        f"SSO token exchange for server "
                        f"'{self.server_name or 'default'}' returned "
                        f"invalid access_token"
                    )
                self._access_token = token
                return self._access_token
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as exc:
                raise GalaxyError(
                    f"SSO token exchange failed for server "
                    f"'{self.server_name or 'default'}'"
                ) from exc

    async def _resolve_auth_headers(self) -> dict[str, str]:
        """Build authentication headers, exchanging SSO token if needed."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token and self._auth_url:
            access_token = await self._ensure_access_token()
            headers["Authorization"] = f"Bearer {access_token}"
        elif self._token:
            headers["Authorization"] = f"Token {self._token}"
        return headers

    async def _discover_api_root(self) -> None:
        """Discover API root and v1 path."""
        if self._api_root is not None:
            return

        if self._discovery_failed:
            server_label = self.server_name or "default"
            raise GalaxyError(
                f"Galaxy API discovery previously failed for server "
                f"'{server_label}'. Check the server URL and credentials "
                f"in ansible.cfg, then restart the MCP server session "
                f"to retry."
            )

        async with self._discovery_lock:
            if self._api_root is not None:
                return
            if self._discovery_failed:
                server_label = self.server_name or "default"
                raise GalaxyError(
                    f"Galaxy API discovery previously failed for server "
                    f"'{server_label}'. Check the server URL and credentials "
                    f"in ansible.cfg, then restart the MCP server session "
                    f"to retry."
                )

            client = self._get_client()
            headers = await self._resolve_auth_headers()
            kwargs: dict[str, Any] = {
                "headers": headers,
                "timeout": TIMEOUT_FAST,
            }
            if not self._token and self._username and self._password:
                kwargs["auth"] = httpx.BasicAuth(self._username, self._password)

            n_url = self._base
            data: dict[str, Any] | None = None
            got_auth_error = False

            try:
                resp = await client.get(n_url, **kwargs)
                resp.raise_for_status()
                if len(resp.content) > MAX_DISCOVERY_RESPONSE_SIZE:
                    raise GalaxyError("Discovery response too large")
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                logger.debug("Discovery probe %s returned HTTP %s", n_url, exc.response.status_code)
                if exc.response.status_code in (401, 403):
                    got_auth_error = True
            except (httpx.RequestError, ValueError) as exc:
                logger.debug("Discovery probe %s failed: %s", n_url, exc)

            if data is None or "available_versions" not in data:
                if not n_url.rstrip("/").endswith("/api"):
                    n_url = n_url.rstrip("/") + "/api"
                    try:
                        resp = await client.get(n_url, **kwargs)
                        resp.raise_for_status()
                        if len(resp.content) > MAX_DISCOVERY_RESPONSE_SIZE:
                            raise GalaxyError("Discovery response too large")
                        data = resp.json()
                    except httpx.HTTPStatusError as exc:
                        logger.debug("Discovery probe %s returned HTTP %s", n_url, exc.response.status_code)
                        if exc.response.status_code in (401, 403):
                            got_auth_error = True
                        data = None
                    except (httpx.RequestError, ValueError) as exc:
                        logger.debug("Discovery probe %s failed: %s", n_url, exc)
                        data = None

            server_label = self.server_name or "default"
            if data is None or "available_versions" not in data:
                self._discovery_failed = True
                if got_auth_error:
                    raise GalaxyError(
                        f"Galaxy API root discovery failed for server "
                        f"'{server_label}' — authentication failed "
                        f"(HTTP 401/403). "
                        f"Check token/credentials in ansible.cfg."
                    )
                raise GalaxyError(
                    f"Could not discover Galaxy API root for server "
                    f"'{server_label}' — "
                    f"no 'available_versions' found. "
                    f"Verify the server URL in ansible.cfg."
                )

            versions = data["available_versions"]
            if "v1" not in versions:
                self._discovery_failed = True
                raise GalaxyError(
                    f"Galaxy server '{server_label}' does not "
                    f"support Galaxy API v1 (available: {', '.join(versions.keys())})."
                )

            v1_path = versions["v1"]
            if ".." in v1_path or not _SAFE_V1_PATH_RE.match(v1_path):
                self._discovery_failed = True
                raise GalaxyError(
                    f"Galaxy server '{server_label}' returned "
                    f"unsafe v1 path. Verify the server URL in ansible.cfg."
                )

            self._api_root = n_url.rstrip("/")
            self._v1_path = v1_path
            logger.info(
                "Discovered Galaxy API root: %s (v1 path: %s)",
                self._api_root, self._v1_path,
            )

    def _build_v1_url(self, *segments: str) -> str:
        """Build a v1 API URL from path segments using the discovered root."""
        if self._api_root is None:
            logger.warning("_build_v1_url called before _discover_api_root")
        parts = [self._api_root or self._base, self._v1_path or ""]
        parts.extend(segments)
        return "/".join(
            p.strip("/") for p in parts if p and p.strip("/")
        ) + "/"

    async def _api_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        timeout: httpx.Timeout = TIMEOUT_DEFAULT,
    ) -> dict[str, Any]:
        url = path if path.startswith(("http://", "https://")) else f"{self._base}{path}"
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "params": params,
            "headers": await self._resolve_auth_headers(),
            "timeout": timeout,
        }
        if not self._token and self._username and self._password:
            kwargs["auth"] = httpx.BasicAuth(self._username, self._password)
        resp = await client.get(url, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401 and self._auth_url:
                stale = kwargs["headers"].get("Authorization", "")
                cur = f"Bearer {self._access_token}" if self._access_token else ""
                if stale == cur:
                    self._access_token = None
                kwargs["headers"] = await self._resolve_auth_headers()
                resp = await client.get(url, **kwargs)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as retry_exc:
                    raise GalaxyError(
                        f"Galaxy API error (HTTP {retry_exc.response.status_code})"
                    ) from retry_exc
            else:
                raise GalaxyError(
                    f"Galaxy API error (HTTP {exc.response.status_code})"
                ) from exc
        content_length = resp.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_GALAXY_RESPONSE_SIZE:
                    raise GalaxyError("Galaxy API response too large")
            except (ValueError, OverflowError):
                pass
        if len(resp.content) > MAX_GALAXY_RESPONSE_SIZE:
            raise GalaxyError("Galaxy API response too large")
        return resp.json()

    async def _safe_api_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        timeout: httpx.Timeout = TIMEOUT_DEFAULT,
    ) -> dict[str, Any]:
        """Wrap _api_get with network error handling."""
        try:
            return await self._api_get(path, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise GalaxyError("Galaxy connection timed out") from exc
        except httpx.RequestError as exc:
            raise GalaxyError(f"Galaxy connection error: {type(exc).__name__}") from exc

    async def search_roles(self, query: str, tags: str | None = None) -> dict[str, Any]:
        await self._discover_api_root()
        cache_key = (*self._cache_identity(), "search", query, tags or "")
        cached = _v1_cache.get(cache_key)
        if cached is not None:
            return cached
        params = {
            "keywords": query,
            "order_by": "-download_count",
            "page_size": "10",
        }
        tag = _first_tag(tags)
        if tag:
            params["tags"] = tag
        data = await self._safe_api_get(self._build_v1_url("roles"), params=params)
        hits = data.get("results") or []
        mapped = [_map_search_hit(item) for item in hits if isinstance(item, dict)]
        result = {"query": query, "count": len(mapped), "roles": mapped}
        _v1_cache.put(cache_key, result)
        return result

    async def fetch_role_by_name(self, namespace: str, name: str) -> dict[str, Any]:
        await self._discover_api_root()
        params = {"namespace": namespace, "name": name, "page_size": "1"}
        data = await self._safe_api_get(self._build_v1_url("roles"), params=params)
        results = data.get("results") or []
        if not results:
            raise GalaxyError(
                f"Standalone role '{namespace}.{name}' not found"
            )
        return results[0]

    async def fetch_role_content(self, role_id: int) -> dict[str, Any]:
        await self._discover_api_root()
        cache_key = (*self._cache_identity(), "content", str(role_id))
        cached = _v1_cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._safe_api_get(
            self._build_v1_url("roles", str(role_id), "content"),
        )
        _v1_cache.put(cache_key, data)
        return data

    async def fetch_standalone_role_doc(
        self, role_name: str,
    ) -> tuple[dict[str, Any], DocProvenance]:
        from ansible_know.readme_parser import parse_role_readme

        namespace, _, name = role_name.partition(".")
        role = await self.fetch_role_by_name(namespace, name)
        content = await self.fetch_role_content(int(role["id"]))
        html = content.get("readme_html") or ""
        parsed = parse_role_readme(html)
        summary = role.get("summary_fields") or {}
        deps = parsed.get("dependencies") or _normalize_dependencies(
            summary.get("dependencies"),
        )
        versions = summary.get("versions") or []
        latest = ""
        if versions and isinstance(versions[0], dict):
            latest = versions[0].get("name") or ""
        tags_raw = summary.get("tags") or []
        tags = [
            t if isinstance(t, str) else str(t.get("name", ""))
            for t in tags_raw
        ]
        tags = [t for t in tags if t]
        short = parsed.get("description") or role.get("description") or ""
        options = []
        for var in parsed.get("variables") or []:
            options.append({
                "name": var["name"],
                "type": var.get("type") or "str",
                "required": bool(var.get("required")),
                "default": var.get("default"),
                "choices": var.get("choices"),
                "description": var.get("description", ""),
                "aliases": var.get("aliases", []),
            })
        metadata = {
            "role_name": role_name,
            "content_type": "standalone_role",
            "short_description": short,
            "entry_points": {
                "main": {"description": short, "options": options},
            },
            "dependencies": deps,
            "examples": parsed.get("examples") or "",
            "tags": tags,
            "latest_version": latest,
            "github_user": role.get("github_user") or "",
            "github_repo": role.get("github_repo") or "",
            "github_branch": role.get("github_branch") or "",
            "download_count": role.get("download_count") or 0,
        }
        has_html = bool(html.strip())
        provenance: DocProvenance = {
            "doc_source": "galaxy_v1_readme" if has_html else "galaxy_v1_metadata",
            "doc_version": latest,
        }
        if has_html:
            provenance["doc_warning"] = (
                "Documentation parsed from Galaxy README (best-effort)."
            )
        else:
            provenance["doc_warning"] = (
                "Galaxy stored no README HTML for this standalone role; "
                "returning catalog metadata only."
            )
        if self.server_name:
            provenance["doc_source_server"] = self.server_name
        return metadata, provenance


__all__ = ["GalaxyV1Client", "clear_cache"]
