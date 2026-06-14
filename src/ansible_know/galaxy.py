"""Galaxy v3 API client.

Searches collections, fetches documentation blobs, and resolves versions
from Ansible Galaxy without requiring local collection installation.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import httpx

from ansible_know.config import GALAXY_BASE_URL
from ansible_know.errors import GalaxyError

if TYPE_CHECKING:
    from ansible_know.galaxy_config import GalaxyServerConfig

logger = logging.getLogger("ansible_know")

MAX_GALAXY_RESPONSE_SIZE = 5_000_000  # 5MB
MAX_VERSION_CACHE_SIZE = 500
MAX_BLOB_CACHE_SIZE = 50
CACHE_TTL_SECONDS = 3600

TIMEOUT_FAST = httpx.Timeout(10.0)
TIMEOUT_DEFAULT = httpx.Timeout(10.0, read=30.0)
TIMEOUT_SLOW = httpx.Timeout(10.0, read=60.0)

_enrichment_semaphore: asyncio.Semaphore | None = None
_enrichment_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_enrichment_semaphore() -> asyncio.Semaphore:
    global _enrichment_semaphore, _enrichment_semaphore_loop
    loop = asyncio.get_running_loop()
    if _enrichment_semaphore is None or _enrichment_semaphore_loop is not loop:
        _enrichment_semaphore = asyncio.Semaphore(5)
        _enrichment_semaphore_loop = loop
    return _enrichment_semaphore

_version_cache: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()
_version_lock = threading.Lock()
_blob_cache: OrderedDict[tuple[str, str, str], tuple[dict[str, Any], float]] = OrderedDict()
_blob_lock = threading.Lock()


def _get_version_cache(key: tuple[str, str]) -> str | None:
    with _version_lock:
        cached = _version_cache.get(key)
        if cached is None:
            return None
        value, timestamp = cached
        if time.monotonic() - timestamp > CACHE_TTL_SECONDS:
            del _version_cache[key]
            return None
        return value


def _put_version_cache(key: tuple[str, str], value: str) -> None:
    with _version_lock:
        _version_cache[key] = (value, time.monotonic())
        while len(_version_cache) > MAX_VERSION_CACHE_SIZE:
            _version_cache.popitem(last=False)


def _get_blob_cache(key: tuple[str, str, str]) -> dict[str, Any] | None:
    with _blob_lock:
        cached = _blob_cache.get(key)
        if cached is None:
            return None
        value, timestamp = cached
        if time.monotonic() - timestamp > CACHE_TTL_SECONDS:
            del _blob_cache[key]
            return None
        return value


def _put_blob_cache(key: tuple[str, str, str], value: dict[str, Any]) -> None:
    with _blob_lock:
        _blob_cache[key] = (value, time.monotonic())
        while len(_blob_cache) > MAX_BLOB_CACHE_SIZE:
            _blob_cache.popitem(last=False)


def clear_cache() -> None:
    """Clear Galaxy caches (useful for testing)."""
    with _version_lock:
        _version_cache.clear()
    with _blob_lock:
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
    ):
        self._base = (base_url or GALAXY_BASE_URL).rstrip("/")
        self._http_client = http_client
        self._owned_client: httpx.AsyncClient | None = None
        self._token = token
        self._username = username
        self._password = password
        self._verify = verify
        self.server_name = server_name

    @classmethod
    def from_config(
        cls, config: GalaxyServerConfig, http_client: httpx.AsyncClient | None = None,
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
            )
        return self._owned_client

    def _auth_headers(self) -> dict[str, str]:
        """Build authentication headers based on configured credentials."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Token {self._token}"
        return headers

    async def _api_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        timeout: httpx.Timeout = TIMEOUT_DEFAULT,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "params": params,
            "headers": self._auth_headers(),
            "timeout": timeout,
        }
        if not self._token and self._username and self._password:
            kwargs["auth"] = httpx.BasicAuth(self._username, self._password)
        resp = await client.get(url, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
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
        cached = _get_version_cache(cache_key)
        if cached is not None:
            return cached
        path = (
            f"/api/v3/plugin/ansible/content/published/collections/index/"
            f"{namespace}/{name}/versions/"
        )
        params = {"limit": "1", "ordering": "-version", "format": "json"}
        data = await self._safe_api_get(path, params=params, timeout=TIMEOUT_FAST)
        versions = data.get("data", [])
        if not versions:
            raise GalaxyError(
                f"No versions found for {namespace}.{name} on Galaxy."
            )
        version = versions[0]["version"]
        _put_version_cache(cache_key, version)
        return version

    async def _get_collection_detail(
        self, namespace: str, name: str,
    ) -> dict[str, Any]:
        path = (
            f"/api/v3/plugin/ansible/content/published/collections/index/"
            f"{namespace}/{name}/"
        )
        return await self._safe_api_get(path, timeout=TIMEOUT_FAST)

    async def search_collections(
        self, query: str, tags: str | None = None,
    ) -> dict[str, Any]:
        search_path = "/api/v3/plugin/ansible/search/collection-versions/"
        search_params: dict[str, str] = {
            "keywords": query,
            "is_highest": "true",
            "limit": "10",
        }
        if tags:
            search_params["tags"] = tags

        data = await self._safe_api_get(
            search_path, params=search_params,
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
            tags_list = [t["name"] for t in cv.get("tags", []) if isinstance(t, dict)]
            candidates.append({
                "namespace": f"{ns}.{name}",
                "description": cv.get("description", ""),
                "tags": tags_list,
                "latest_version": cv.get("version", ""),
                "module_count": module_count,
                "role_count": role_count,
                "deprecated": False,
                "signed": item.get("is_signed", False),
                "_ns": ns,
                "_name": name,
            })

        async def _enrich(cand: dict) -> None:
            async with _get_enrichment_semaphore():
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
        cached = _get_blob_cache(cache_key)
        if cached is not None:
            return cached
        path = (
            f"/api/v3/plugin/ansible/content/published/collections/index/"
            f"{namespace}/{name}/versions/{version}/docs-blob/"
        )
        params = {"format": "json"}
        data = await self._safe_api_get(path, params=params, timeout=TIMEOUT_SLOW)
        blob = data.get("docs_blob", data)
        _put_blob_cache(cache_key, blob)
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
    def _transform_to_ansible_doc_format(
        fqcn: str, entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert a Galaxy docs-blob content entry to ansible-doc --json format."""
        ds = entry.get("doc_strings", {})
        raw_doc = ds.get("doc", {})

        raw_options = raw_doc.get("options", [])
        if isinstance(raw_options, list):
            options_dict: dict[str, Any] = {}
            for opt in raw_options:
                if not isinstance(opt, dict):
                    continue
                opt_copy = dict(opt)
                opt_name = opt_copy.pop("name", None)
                if opt_name:
                    options_dict[opt_name] = opt_copy
        else:
            options_dict = raw_options

        doc_section = {
            "short_description": raw_doc.get("short_description", ""),
            "description": raw_doc.get("description", []),
            "options": options_dict,
            "author": raw_doc.get("author", []),
            "notes": raw_doc.get("notes", []),
            "version_added": raw_doc.get("version_added", ""),
        }

        return {
            fqcn: {
                "doc": doc_section,
                "examples": ds.get("examples", ""),
                "return": ds.get("return", []),
                "metadata": ds.get("metadata", {}),
            }
        }

    async def fetch_module_doc(
        self, module_name: str, version: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
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

        doc = self._transform_to_ansible_doc_format(module_name, module_entry)

        meta: dict[str, str] = {
            "doc_source": "galaxy",
            "doc_version": resolved_version,
        }
        if is_latest:
            meta["doc_warning"] = (
                f"Documentation sourced from Galaxy "
                f"({namespace}.{name} {resolved_version}). "
                f"Your installed version may differ."
            )
        return doc, meta

    async def fetch_role_doc(
        self, role_name: str, version: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
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
                "type": var.get("type"),
                "required": var.get("required"),
                "default": var.get("default"),
                "description": var.get("description", ""),
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

        meta: dict[str, str] = {
            "doc_source": "galaxy",
            "doc_version": resolved_version,
        }
        if is_latest:
            meta["doc_warning"] = (
                "Documentation parsed from Galaxy README (best-effort)."
            )
        return role_metadata, meta

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
