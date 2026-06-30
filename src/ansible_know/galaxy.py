"""Galaxy v3 API client.

Searches collections, fetches documentation blobs, and resolves versions
from Ansible Galaxy without requiring local collection installation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

from ansible_know.cache import BoundedCache
from ansible_know.config import CACHE_DIR, GALAXY_BASE_URL
from ansible_know.errors import GalaxyError

if TYPE_CHECKING:
    from ansible_know.galaxy_config import GalaxyServerConfig
    from ansible_know.types import DocProvenance, ModuleMetadata

logger = logging.getLogger("ansible_know")

MAX_GALAXY_RESPONSE_SIZE = 5_000_000  # 5MB
CACHE_TTL_SECONDS = 3600

TIMEOUT_FAST = httpx.Timeout(10.0)
TIMEOUT_DEFAULT = httpx.Timeout(10.0, read=30.0)
TIMEOUT_SLOW = httpx.Timeout(10.0, read=60.0)

ENRICHMENT_CONCURRENCY = 5

# Module-level caches shared across all GalaxyClient instances.
# Keys include enough context (namespace, name, version) to avoid
# cross-instance collisions. Thread-safe via BoundedCache.
_version_cache: BoundedCache[tuple[str, str], str] = BoundedCache(
    max_size=500, ttl=CACHE_TTL_SECONDS,
    path=CACHE_DIR / "galaxy-versions.json",
)
_blob_cache: BoundedCache[tuple[str, str, str], dict[str, Any]] = BoundedCache(
    max_size=50, ttl=CACHE_TTL_SECONDS,
    path=CACHE_DIR / "galaxy-blobs.json",
)


def clear_cache() -> None:
    """Clear Galaxy caches (useful for testing)."""
    _version_cache.clear()
    _blob_cache.clear()


def _parse_fqcn(module_name: str) -> tuple[str, str, str]:
    """Split 'namespace.collection.module' into its three parts."""
    parts = module_name.split(".")
    if len(parts) != 3:
        raise GalaxyError(
            f"'{module_name}' is not a fully-qualified collection name "
            f"(expected namespace.collection.module)."
        )
    return parts[0], parts[1], parts[2]


class GalaxyClient:
    """Async client for the Galaxy v3 API."""

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify: bool = True,
        server_name: str | None = None,
        enrichment_semaphore: asyncio.Semaphore | None = None,
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
        self._enrichment_semaphore = enrichment_semaphore or asyncio.Semaphore(
            ENRICHMENT_CONCURRENCY,
        )
        self._auth_url = auth_url
        self._client_id = client_id
        self._access_token: str | None = None
        self._token_lock = asyncio.Lock()
        self._api_root: str | None = None
        self._v3_path: str | None = None
        self._discovery_lock = asyncio.Lock()

    @classmethod
    def from_config(
        cls,
        config: GalaxyServerConfig,
        http_client: httpx.AsyncClient | None = None,
        enrichment_semaphore: asyncio.Semaphore | None = None,
    ) -> GalaxyClient:
        """Create a GalaxyClient from a GalaxyServerConfig."""
        return cls(
            base_url=config.url,
            http_client=http_client,
            token=config.token,
            username=config.username,
            password=config.password,
            verify=config.validate_certs,
            server_name=config.name,
            enrichment_semaphore=enrichment_semaphore,
            auth_url=config.auth_url,
            client_id=config.client_id,
        )

    async def __aenter__(self) -> GalaxyClient:
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
                timeout=httpx.Timeout(10.0, read=120.0),
                verify=self._verify,
                follow_redirects=True,
            )
        return self._owned_client

    async def _ensure_access_token(self) -> str:
        """Exchange offline token for access token via SSO, with caching."""
        if self._access_token is not None:
            return self._access_token
        if self._auth_url is None:
            raise GalaxyError(
                "Cannot exchange SSO token without auth_url"
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
        """Discover API root and v3 path, matching ansible-galaxy's g_connect."""
        if self._api_root is not None:
            return

        async with self._discovery_lock:
            if self._api_root is not None:
                return

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
            if "v3" not in versions:
                raise GalaxyError(
                    f"Galaxy server '{server_label}' does not "
                    f"support API v3 (available: {', '.join(versions.keys())}). "
                    f"Only v3 servers are supported."
                )

            self._api_root = n_url.rstrip("/")
            self._v3_path = versions["v3"]
            logger.info(
                "Discovered Galaxy API root: %s (v3 path: %s)",
                self._api_root, self._v3_path,
            )

    def _build_v3_url(self, *segments: str) -> str:
        """Build a v3 API URL from path segments using the discovered root."""
        parts = [self._api_root or self._base, self._v3_path or ""]
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
        cache_key = (namespace, name)
        cached = _version_cache.get(cache_key)
        if cached is not None:
            return cached
        await self._discover_api_root()
        url = self._build_v3_url("collections", namespace, name, "versions")
        params = {"limit": "1", "ordering": "-version", "format": "json"}
        data = await self._safe_api_get(url, params=params, timeout=TIMEOUT_FAST)
        versions = data.get("data", [])
        if not versions:
            raise GalaxyError(
                f"No versions found for {namespace}.{name} on Galaxy."
            )
        version = versions[0]["version"]
        _version_cache.put(cache_key, version)
        return version

    async def _get_collection_detail(
        self, namespace: str, name: str,
    ) -> dict[str, Any]:
        await self._discover_api_root()
        url = self._build_v3_url("collections", namespace, name)
        return await self._safe_api_get(url, timeout=TIMEOUT_FAST)

    async def search_collections(
        self, query: str, tags: str | None = None,
    ) -> dict[str, Any]:
        await self._discover_api_root()
        search_url = self._build_v3_url("plugin", "ansible", "search", "collection-versions")
        search_params: dict[str, str] = {
            "keywords": query,
            "is_highest": "true",
            "limit": "10",
        }
        if tags:
            search_params["tags"] = tags

        data = await self._safe_api_get(
            search_url, params=search_params,
        )

        candidates = []
        for item in data.get("data", []):
            if item.get("is_deprecated", False):
                continue
            cv = item.get("collection_version", {})
            ns = cv.get("namespace", "")
            name = cv.get("name", "")
            contents = cv.get("contents", [])
            module_count = sum(
                1 for c in contents if c.get("content_type") == "module"
            )
            role_count = sum(
                1 for c in contents if c.get("content_type") == "role"
            )
            from ansible_know.config import PLUGIN_TYPES
            plugin_count = sum(
                1 for c in contents if c.get("content_type") in PLUGIN_TYPES
            )
            tags_list = [t["name"] for t in cv.get("tags", []) if isinstance(t, dict)]
            candidates.append({
                "namespace": f"{ns}.{name}",
                "description": cv.get("description", ""),
                "tags": tags_list,
                "latest_version": cv.get("version", ""),
                "module_count": module_count,
                "role_count": role_count,
                "plugin_count": plugin_count,
                "deprecated": False,
                "signed": item.get("is_signed", False),
                "_ns": ns,
                "_name": name,
            })

        async def _enrich(cand: dict) -> None:
            async with self._enrichment_semaphore:
                try:
                    detail = await self._get_collection_detail(
                        cand["_ns"], cand["_name"],
                    )
                    cand["download_count"] = detail.get("download_count", 0)
                    highest = detail.get("highest_version", {})
                    if isinstance(highest, dict):
                        cand["latest_version"] = highest.get(
                            "version", cand["latest_version"],
                        )
                except GalaxyError:
                    cand["download_count"] = 0

        await asyncio.gather(*[_enrich(c) for c in candidates])

        for cand in candidates:
            cand.pop("_ns", None)
            cand.pop("_name", None)

        candidates.sort(key=lambda c: c.get("download_count", 0), reverse=True)

        return {
            "query": query,
            "count": len(candidates),
            "collections": candidates,
        }

    async def _fetch_docs_blob(
        self, namespace: str, name: str, version: str,
    ) -> dict[str, Any]:
        cache_key = (namespace, name, version)
        cached = _blob_cache.get(cache_key)
        if cached is not None:
            return cached
        await self._discover_api_root()
        url = self._build_v3_url(
            "collections", namespace, name, "versions", version, "docs-blob",
        )
        params = {"format": "json"}
        data = await self._safe_api_get(url, params=params, timeout=TIMEOUT_SLOW)
        blob = data.get("docs_blob", data)
        _blob_cache.put(cache_key, blob)
        return blob

    @staticmethod
    def _find_module(
        blob: dict[str, Any], short_name: str,
    ) -> dict[str, Any] | None:
        for item in blob.get("contents", []):
            if (
                item.get("content_type") == "module"
                and item.get("content_name") == short_name
            ):
                return item
        return None

    @staticmethod
    def _find_role(
        blob: dict[str, Any], short_name: str,
    ) -> dict[str, Any] | None:
        for item in blob.get("contents", []):
            if (
                item.get("content_type") == "role"
                and item.get("content_name") == short_name
            ):
                return item
        return None

    @staticmethod
    def _find_plugin(
        blob: dict[str, Any], short_name: str, plugin_type: str,
    ) -> dict[str, Any] | None:
        for item in blob.get("contents", []):
            if (
                item.get("content_type") == plugin_type
                and item.get("content_name") == short_name
            ):
                return item
        return None

    async def fetch_module_doc(
        self, module_name: str, version: str | None = None,
    ) -> tuple[dict[str, Any], DocProvenance]:
        """Fetch module documentation from Galaxy.

        Returns (module_doc, meta) where module_doc mimics ansible-doc --json
        format and meta contains provenance fields.
        """
        namespace, name, short_module = _parse_fqcn(module_name)
        resolved_version = version or await self.latest_version(namespace, name)
        is_latest = version is None

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        module_entry = self._find_module(blob, short_module)
        if module_entry is None:
            raise GalaxyError(
                f"Module '{short_module}' not found in "
                f"{namespace}.{name} {resolved_version} docs-blob."
            )

        from ansible_know.parser import transform_galaxy_to_ansible_doc_format
        doc = transform_galaxy_to_ansible_doc_format(module_name, module_entry)

        meta: DocProvenance = {
            "doc_source": "galaxy",
            "doc_version": resolved_version,
        }
        if is_latest:
            meta["doc_warning"] = (
                f"Documentation sourced from Galaxy "
                f"({namespace}.{name} {resolved_version}). "
                f"Your installed version may differ."
            )
        if self.server_name:
            meta["doc_source_server"] = self.server_name
        return doc, meta

    async def fetch_role_doc(
        self, role_name: str, version: str | None = None,
    ) -> tuple[dict[str, Any], DocProvenance]:
        """Fetch role documentation from Galaxy docs-blob.

        Parses readme_html via readme_parser.parse_role_readme() and returns
        structured metadata. Returns (role_metadata, meta) where meta
        contains provenance fields.
        """
        from ansible_know.readme_parser import parse_role_readme

        namespace, name, short_role = _parse_fqcn(role_name)
        resolved_version = version or await self.latest_version(namespace, name)
        is_latest = version is None

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        role_entry = self._find_role(blob, short_role)
        if role_entry is None:
            raise GalaxyError(
                f"Role '{short_role}' not found in "
                f"{namespace}.{name} {resolved_version} docs-blob."
            )

        readme_html = role_entry.get("readme_html", "")
        parsed = parse_role_readme(readme_html)

        options: list[dict[str, Any]] = []
        for var in parsed.get("variables", []):
            options.append({
                "name": var["name"],
                "type": var.get("type") or "str",
                "required": bool(var.get("required")),
                "default": var.get("default"),
                "choices": var.get("choices"),
                "description": var.get("description", ""),
                "aliases": var.get("aliases", []),
            })

        role_metadata: dict[str, Any] = {
            "role_name": role_name,
            "short_description": parsed.get("description", ""),
            "entry_points": {
                "main": {
                    "description": parsed.get("description", ""),
                    "options": options,
                },
            },
            "dependencies": parsed.get("dependencies", []),
            "examples": parsed.get("examples", ""),
        }

        meta: DocProvenance = {
            "doc_source": "galaxy",
            "doc_version": resolved_version,
        }
        if is_latest:
            meta["doc_warning"] = (
                "Documentation parsed from Galaxy README (best-effort)."
            )
        if self.server_name:
            meta["doc_source_server"] = self.server_name
        return role_metadata, meta

    async def fetch_plugin_doc(
        self, plugin_name: str, plugin_type: str, version: str | None = None,
    ) -> tuple[dict[str, Any], DocProvenance]:
        """Fetch plugin documentation from Galaxy.

        Returns (plugin_doc, meta) where plugin_doc mimics ansible-doc --json
        format and meta contains provenance fields.
        """
        namespace, name, short_plugin = _parse_fqcn(plugin_name)
        resolved_version = version or await self.latest_version(namespace, name)
        is_latest = version is None

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        plugin_entry = self._find_plugin(blob, short_plugin, plugin_type)
        if plugin_entry is None:
            raise GalaxyError(
                f"Plugin '{short_plugin}' (type={plugin_type}) not found in "
                f"{namespace}.{name} {resolved_version} docs-blob."
            )

        from ansible_know.parser import transform_galaxy_to_ansible_doc_format
        doc = transform_galaxy_to_ansible_doc_format(plugin_name, plugin_entry)

        meta: DocProvenance = {
            "doc_source": "galaxy",
            "doc_version": resolved_version,
        }
        if is_latest:
            meta["doc_warning"] = (
                f"Documentation sourced from Galaxy "
                f"({namespace}.{name} {resolved_version}). "
                f"Your installed version may differ."
            )
        if self.server_name:
            meta["doc_source_server"] = self.server_name
        return doc, meta

    async def fetch_collection_docs(
        self, collection_namespace: str, version: str | None = None,
    ) -> tuple[dict[str, ModuleMetadata], DocProvenance]:
        """Fetch all module docs from a collection in one docs-blob call.

        Extracts every module entry from the blob, returning a dict keyed
        by module FQCN. Transforms each into
        the same ``ModuleMetadata`` shape that ``extract_module_metadata``
        produces from ansible-doc output.

        Contract:
            Preconditions:
                - ``collection_namespace`` must be 'namespace.name' format
                  (two dot-separated segments). Raises ``GalaxyError`` if not.

            Raises:
                GalaxyError: If the namespace is malformed, the collection is
                    not found on Galaxy, or the API request fails.

            Silences:
                - Individual modules whose ``transform_galaxy_to_ansible_doc_format``
                  or ``extract_module_metadata`` raises are logged and skipped.
                  The caller receives docs for the remaining modules with no
                  indication of partial failure (check logs).
        """
        from ansible_know.parser import (
            extract_module_metadata,
            transform_galaxy_to_ansible_doc_format,
        )

        parts = collection_namespace.split(".")
        if len(parts) != 2:
            raise GalaxyError(
                f"'{collection_namespace}' is not a valid collection FQCN "
                f"(expected namespace.name)."
            )
        namespace, name = parts
        resolved_version = version or await self.latest_version(namespace, name)
        is_latest = version is None

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        result: dict[str, ModuleMetadata] = {}
        for item in blob.get("contents", []):
            if item.get("content_type") != "module":
                continue
            short_name = item.get("content_name", "")
            fqcn = f"{collection_namespace}.{short_name}"
            try:
                raw_doc = transform_galaxy_to_ansible_doc_format(fqcn, item)
                result[fqcn] = extract_module_metadata(raw_doc)
            except Exception:
                logger.warning("Skipping module %s: metadata extraction failed", fqcn, exc_info=True)

        meta: DocProvenance = {
            "doc_source": "galaxy",
            "doc_version": resolved_version,
        }
        if is_latest:
            meta["doc_warning"] = (
                f"Documentation sourced from Galaxy "
                f"({namespace}.{name} {resolved_version}). "
                f"Your installed version may differ."
            )
        if self.server_name:
            meta["doc_source_server"] = self.server_name
        return result, meta

    async def list_collection_modules(
        self, collection_fqcn: str, version: str | None = None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """List modules in a collection from the Galaxy docs-blob.

        Returns (modules, meta) where modules is {fqcn: description}.
        """
        parts = collection_fqcn.split(".")
        if len(parts) != 2:
            raise GalaxyError(
                f"'{collection_fqcn}' is not a valid collection FQCN "
                f"(expected namespace.name)."
            )
        namespace, name = parts
        resolved_version = version or await self.latest_version(namespace, name)

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        modules: dict[str, str] = {}
        for item in blob.get("contents", []):
            if item.get("content_type") == "module":
                short = item.get("content_name", "")
                fqcn = f"{collection_fqcn}.{short}"
                desc = item.get("doc_strings", {}).get("doc", {}).get(
                    "short_description", "",
                ) or ""
                modules[fqcn] = desc

        meta = {"source": "galaxy", "version": resolved_version}
        return modules, meta

    async def list_collection_roles(
        self, collection_fqcn: str, version: str | None = None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """List roles in a collection from the Galaxy docs-blob.

        Returns (roles, meta) where roles is {fqcn: description} and
        meta is {"source": "galaxy", "version": str}.
        """
        parts = collection_fqcn.split(".")
        if len(parts) != 2:
            raise GalaxyError(
                f"'{collection_fqcn}' is not a valid collection FQCN "
                f"(expected namespace.name)."
            )
        namespace, name = parts
        resolved_version = version or await self.latest_version(namespace, name)

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        roles: dict[str, str] = {}
        for item in blob.get("contents", []):
            if item.get("content_type") == "role":
                short = item.get("content_name", "")
                fqcn = f"{collection_fqcn}.{short}"
                readme_html = item.get("readme_html", "")
                desc = ""
                if readme_html:
                    from ansible_know.readme_parser import parse_role_readme
                    parsed = parse_role_readme(readme_html)
                    desc = parsed.get("description", "")
                roles[fqcn] = desc

        meta = {"source": "galaxy", "version": resolved_version}
        return roles, meta

    async def list_collection_plugins(
        self, collection_fqcn: str, version: str | None = None,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """List plugins in a collection from the Galaxy docs-blob.

        Returns (plugins, meta) where plugins is
        {fqcn: {"description": str, "plugin_type": str}, ...}.
        Excludes modules and roles.

        Not currently called from the MCP server — manifest generation uses
        local parser.list_plugins instead. This method enables future Galaxy
        fallback for manifest generation when collections are not installed
        locally (parallel to fetch_plugin_doc which IS used for fallback).
        """
        from ansible_know.config import PLUGIN_TYPES

        parts = collection_fqcn.split(".")
        if len(parts) != 2:
            raise GalaxyError(
                f"'{collection_fqcn}' is not a valid collection FQCN "
                f"(expected namespace.name)."
            )
        namespace, name = parts
        resolved_version = version or await self.latest_version(namespace, name)

        blob = await self._fetch_docs_blob(namespace, name, resolved_version)
        plugins: dict[str, dict[str, str]] = {}
        for item in blob.get("contents", []):
            ct = item.get("content_type", "")
            if ct not in PLUGIN_TYPES:
                continue
            short = item.get("content_name", "")
            fqcn = f"{collection_fqcn}.{short}"
            desc = item.get("doc_strings", {}).get("doc", {}).get(
                "short_description", "",
            ) or ""
            plugins[fqcn] = {"description": desc, "plugin_type": ct}

        meta = {"source": "galaxy", "version": resolved_version}
        return plugins, meta
