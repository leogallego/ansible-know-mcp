# Galaxy Collection Discovery & Remote Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable agents to discover collections on Galaxy by keyword and fetch module docs from the Galaxy API without local installation.

**Architecture:** A new `galaxy.py` module provides an async httpx client for the Galaxy v3 API with three capabilities: keyword search, collection detail enrichment, and docs-blob fetching with format conversion. `server.py` gains a `search_collections` tool and a docs-blob fallback in `get_module_doc` (and other tools). The Galaxy client is ported from the upstream `ansibleclaw` project, adapted from stdlib urllib to async httpx.

**Tech Stack:** Python 3.10+, httpx (async HTTP), FastMCP, pytest + pytest-asyncio

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/ansible_know/galaxy.py` | **Create** | Galaxy v3 API client — search, detail, versions, docs-blob, format conversion |
| `tests/test_galaxy.py` | **Create** | Unit tests for Galaxy client (all API calls mocked) |
| `src/ansible_know/config.py` | **Modify** | Add `GALAXY_BASE_URL` constant |
| `src/ansible_know/server.py` | **Modify** | Add `search_collections` tool, add Galaxy docs-blob fallback to `get_module_doc` and other tools |
| `tests/test_server.py` | **Modify** | Tests for new tool and fallback behavior |

---

## Task 1: Galaxy API Client — Core HTTP and Version Resolution

**Files:**
- Create: `src/ansible_know/galaxy.py`
- Create: `tests/test_galaxy.py`
- Modify: `src/ansible_know/config.py`

This task builds the foundation: the `GalaxyClient` class with its HTTP helper and `latest_version()` method.

- [ ] **Step 1: Add GALAXY_BASE_URL to config**

In `src/ansible_know/config.py`, add after the `SEARCH_DOCS_LIMIT` line:

```python
GALAXY_BASE_URL = os.environ.get(
    "ANSIBLE_KNOWLEDGE_GALAXY_URL",
    "https://galaxy.ansible.com",
)
```

- [ ] **Step 2: Write failing tests for GalaxyClient core**

Create `tests/test_galaxy.py`:

```python
"""Tests for ansible_know.galaxy."""

from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from ansible_know.galaxy import GalaxyClient, GalaxyError, clear_cache


@pytest.fixture(autouse=True)
def reset_galaxy_cache():
    """Clear Galaxy caches between tests."""
    clear_cache()
    yield
    clear_cache()


SAMPLE_VERSIONS_RESPONSE = {
    "meta": {"count": 42},
    "links": {"first": None, "previous": None, "next": None, "last": None},
    "data": [
        {
            "version": "3.23.0",
            "href": "/api/v3/.../versions/3.23.0/",
            "created_at": "2026-05-07T13:31:02.008964Z",
            "updated_at": "2026-05-07T13:31:02.008964Z",
            "requires_ansible": ">=2.15.0",
            "marks": [],
        }
    ],
}


def _mock_client_get(response_json):
    """Create a mock httpx.AsyncClient whose .get() returns response_json."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestLatestVersion:
    @pytest.mark.asyncio
    async def test_returns_latest_version(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            version = await client.latest_version("netbox", "netbox")
        assert version == "3.23.0"
        call_url = mock_client.get.call_args[0][0]
        assert "netbox/netbox/versions/" in call_url
        call_params = mock_client.get.call_args[1].get("params", {})
        assert call_params.get("ordering") == "-version"

    @pytest.mark.asyncio
    async def test_raises_on_empty_versions(self):
        empty_response = {"meta": {"count": 0}, "links": {}, "data": []}
        mock_client = _mock_client_get(empty_response)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="No versions found"):
                await client.latest_version("nonexistent", "collection")

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        import httpx as real_httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = real_httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        mock_resp.content = b""
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="Galaxy API error"):
                await client.latest_version("bad", "collection")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_galaxy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ansible_know.galaxy'`

- [ ] **Step 4: Implement GalaxyClient core**

Create `src/ansible_know/galaxy.py`:

```python
"""Galaxy v3 API client.

Searches collections, fetches documentation blobs, and resolves versions
from Ansible Galaxy without requiring local collection installation.
"""

from __future__ import annotations

import asyncio
import logging
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
    import re
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_galaxy.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py src/ansible_know/config.py
git commit -m "Add Galaxy API client with version resolution

Assisted-by: Claude <noreply@anthropic.com>"
```

---

## Task 2: Galaxy Client — Collection Search and Detail Enrichment

**Files:**
- Modify: `src/ansible_know/galaxy.py`
- Modify: `tests/test_galaxy.py`

This task adds `search_collections()` which does keyword search + download count enrichment.

- [ ] **Step 1: Write failing tests for search and detail**

Append to `tests/test_galaxy.py`:

```python
SAMPLE_SEARCH_RESPONSE = {
    "meta": {"count": 2},
    "links": {"first": None, "previous": None, "next": None, "last": None},
    "data": [
        {
            "collection_version": {
                "pulp_href": "/api/v3/...",
                "namespace": "netbox",
                "name": "netbox",
                "version": "3.23.0",
                "requires_ansible": ">=2.15.0",
                "pulp_created": "2026-05-07T13:31:02Z",
                "contents": [
                    {"name": "netbox_device", "description": "Manage devices", "content_type": "module"},
                    {"name": "netbox_site", "description": "Manage sites", "content_type": "module"},
                    {"name": "nb_inventory", "description": "Inventory plugin", "content_type": "inventory"},
                ],
                "dependencies": {},
                "description": "Ansible modules for NetBox",
                "tags": [{"name": "dcim"}, {"name": "ipam"}],
            },
            "is_highest": True,
            "is_deprecated": False,
            "is_signed": False,
            "repository": {},
            "repository_version": "",
            "namespace_metadata": {"pulp_href": "", "name": "netbox", "company": "", "description": "", "avatar_url": None},
        },
        {
            "collection_version": {
                "pulp_href": "/api/v3/...",
                "namespace": "deprecated_ns",
                "name": "old_netbox",
                "version": "1.0.0",
                "requires_ansible": ">=2.9",
                "pulp_created": "2020-01-01T00:00:00Z",
                "contents": [],
                "dependencies": {},
                "description": "Deprecated netbox modules",
                "tags": [],
            },
            "is_highest": True,
            "is_deprecated": True,
            "is_signed": False,
            "repository": {},
            "repository_version": "",
            "namespace_metadata": {"pulp_href": "", "name": "deprecated_ns", "company": "", "description": "", "avatar_url": None},
        },
    ],
}

SAMPLE_DETAIL_RESPONSE = {
    "href": "/api/v3/.../netbox/netbox/",
    "namespace": "netbox",
    "name": "netbox",
    "deprecated": False,
    "versions_url": "/api/v3/.../versions/",
    "highest_version": {"href": "/api/v3/.../3.23.0/", "version": "3.23.0"},
    "created_at": "2023-05-08T23:15:50Z",
    "updated_at": "2026-05-07T13:31:02Z",
    "download_count": 11999959,
}


class TestSearchCollections:
    @pytest.mark.asyncio
    async def test_returns_enriched_results(self):
        call_count = 0

        async def mock_api_get(self_client, path, params=None, client=None):
            nonlocal call_count
            call_count += 1
            if "search/collection-versions" in path:
                return SAMPLE_SEARCH_RESPONSE
            if "index/netbox/netbox/" in path:
                return SAMPLE_DETAIL_RESPONSE
            return {"download_count": 0, "highest_version": {"version": "1.0.0"}, "deprecated": True}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("netbox")

        assert result["query"] == "netbox"
        assert result["count"] >= 1
        collections = result["collections"]
        first = collections[0]
        assert first["namespace"] == "netbox.netbox"
        assert first["download_count"] == 11999959
        assert first["latest_version"] == "3.23.0"
        assert first["module_count"] == 2  # only content_type=module counted
        assert "dcim" in first["tags"]

    @pytest.mark.asyncio
    async def test_filters_deprecated(self):
        async def mock_api_get(self_client, path, params=None, client=None):
            if "search/collection-versions" in path:
                return SAMPLE_SEARCH_RESPONSE
            if "index/netbox/netbox/" in path:
                return SAMPLE_DETAIL_RESPONSE
            return {"download_count": 50, "highest_version": {"version": "1.0.0"}, "deprecated": True}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("netbox")

        namespaces = [c["namespace"] for c in result["collections"]]
        assert "deprecated_ns.old_netbox" not in namespaces

    @pytest.mark.asyncio
    async def test_sorts_by_download_count(self):
        search_data = {
            "meta": {"count": 2},
            "links": {},
            "data": [
                {
                    "collection_version": {
                        "namespace": "low_downloads", "name": "col",
                        "version": "1.0.0", "contents": [], "dependencies": {},
                        "description": "Low", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {"pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None},
                },
                {
                    "collection_version": {
                        "namespace": "high_downloads", "name": "col",
                        "version": "2.0.0", "contents": [], "dependencies": {},
                        "description": "High", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {"pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None},
                },
            ],
        }

        async def mock_api_get(self_client, path, params=None, client=None):
            if "search/collection-versions" in path:
                return search_data
            if "high_downloads/col" in path:
                return {"download_count": 5000000, "highest_version": {"version": "2.0.0"}, "deprecated": False}
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}, "deprecated": False}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("col")

        assert result["collections"][0]["namespace"] == "high_downloads.col"
        assert result["collections"][1]["namespace"] == "low_downloads.col"

    @pytest.mark.asyncio
    async def test_with_tags_filter(self):
        call_args = []

        async def mock_api_get(self_client, path, params=None, client=None):
            call_args.append({"path": path, "params": params})
            if "search/collection-versions" in path:
                return {"meta": {"count": 0}, "links": {}, "data": []}
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("network", tags="networking")

        search_call = [c for c in call_args if "search/collection-versions" in c["path"]][0]
        assert search_call["params"]["tags"] == "networking"
        assert search_call["params"]["keywords"] == "network"
        assert result["count"] == 0
        assert result["collections"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_galaxy.py::TestSearchCollections -v`
Expected: FAIL — `AttributeError: 'GalaxyClient' object has no attribute 'search_collections'`

- [ ] **Step 3: Implement search_collections**

Add to `src/ansible_know/galaxy.py`, inside the `GalaxyClient` class, after `latest_version()`:

```python
    async def _get_collection_detail(
        self, namespace: str, name: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        _validate_component(namespace, "namespace")
        _validate_component(name, "name")
        path = (
            f"/api/v3/plugin/ansible/content/published/collections/index/"
            f"{namespace}/{name}/"
        )
        return await self._safe_api_get(path, client=client)

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

        async with httpx.AsyncClient(timeout=30, verify=True) as shared_client:
            data = await self._safe_api_get(
                search_path, params=search_params, client=shared_client,
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
                tags_list = [t["name"] for t in cv.get("tags", []) if isinstance(t, dict)]
                candidates.append({
                    "namespace": f"{ns}.{name}",
                    "description": cv.get("description", ""),
                    "tags": tags_list,
                    "latest_version": cv.get("version", ""),
                    "module_count": module_count,
                    "deprecated": False,
                    "signed": item.get("is_signed", False),
                    "_ns": ns,
                    "_name": name,
                })

            async def _enrich(cand: dict) -> None:
                try:
                    detail = await self._get_collection_detail(
                        cand["_ns"], cand["_name"], client=shared_client,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_galaxy.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "Add collection search with download count enrichment

Assisted-by: Claude <noreply@anthropic.com>"
```

---

## Task 3: Galaxy Client — Docs-Blob Fetching and Format Conversion

**Files:**
- Modify: `src/ansible_know/galaxy.py`
- Modify: `tests/test_galaxy.py`

This task adds `fetch_module_doc()` and `list_collection_modules()` which fetch the docs-blob from Galaxy and convert Galaxy's option-list format to ansible-doc's option-dict format.

- [ ] **Step 1: Write failing tests for docs-blob**

Append to `tests/test_galaxy.py`:

```python
SAMPLE_DOCS_BLOB = {
    "docs_blob": {
        "contents": [
            {
                "content_type": "module",
                "content_name": "netbox_device",
                "doc_strings": {
                    "doc": {
                        "short_description": "Create, update or delete devices",
                        "description": ["Manages devices in NetBox."],
                        "options": [
                            {
                                "name": "data",
                                "description": ["Device data"],
                                "type": "dict",
                                "required": True,
                            },
                            {
                                "name": "state",
                                "description": ["Object state"],
                                "type": "str",
                                "required": False,
                                "default": "present",
                                "choices": ["present", "absent"],
                            },
                        ],
                        "author": ["Author Name"],
                        "notes": [],
                        "version_added": "0.1.0",
                    },
                    "examples": "- name: Create device\n  netbox.netbox.netbox_device:\n    data:\n      name: Test\n",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "module",
                "content_name": "netbox_site",
                "doc_strings": {
                    "doc": {
                        "short_description": "Create, update or delete sites",
                        "description": ["Manages sites in NetBox."],
                        "options": [],
                        "author": [],
                        "notes": [],
                        "version_added": "0.1.0",
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "inventory",
                "content_name": "nb_inventory",
                "doc_strings": {
                    "doc": {"short_description": "Inventory plugin"},
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
        ],
    },
}


class TestFetchModuleDoc:
    @pytest.mark.asyncio
    async def test_returns_ansible_doc_format(self):
        call_paths = []

        async def mock_api_get(self_client, path):
            call_paths.append(path)
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            doc, meta = await client.fetch_module_doc("netbox.netbox.netbox_device")

        assert "netbox.netbox.netbox_device" in doc
        module_doc = doc["netbox.netbox.netbox_device"]
        assert module_doc["doc"]["short_description"] == "Create, update or delete devices"
        options = module_doc["doc"]["options"]
        assert isinstance(options, dict)
        assert "data" in options
        assert "state" in options
        assert options["data"]["required"] is True
        assert "name" not in options["data"]  # "name" key removed from each option

        assert meta["doc_source"] == "galaxy"
        assert meta["doc_version"] == "3.23.0"

    @pytest.mark.asyncio
    async def test_with_explicit_version(self):
        async def mock_api_get(self_client, path):
            if "docs-blob" in path:
                assert "3.20.0" in path
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            doc, meta = await client.fetch_module_doc(
                "netbox.netbox.netbox_device", version="3.20.0",
            )

        assert meta["doc_version"] == "3.20.0"
        assert "doc_warning" not in meta

    @pytest.mark.asyncio
    async def test_raises_for_missing_module(self):
        async def mock_api_get(self_client, path):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="not found"):
                await client.fetch_module_doc("netbox.netbox.nonexistent_module")

    @pytest.mark.asyncio
    async def test_handles_options_already_as_dict(self):
        blob_with_dict_options = {
            "docs_blob": {
                "contents": [
                    {
                        "content_type": "module",
                        "content_name": "some_module",
                        "doc_strings": {
                            "doc": {
                                "short_description": "A module",
                                "description": [],
                                "options": {
                                    "param1": {"type": "str", "required": True},
                                },
                                "author": [],
                                "notes": [],
                                "version_added": "1.0.0",
                            },
                            "examples": "",
                            "return": [],
                            "metadata": {},
                        },
                    }
                ],
            },
        }

        async def mock_api_get(self_client, path):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return blob_with_dict_options
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            doc, meta = await client.fetch_module_doc("test.col.some_module")

        options = doc["test.col.some_module"]["doc"]["options"]
        assert isinstance(options, dict)
        assert "param1" in options


class TestListCollectionModules:
    @pytest.mark.asyncio
    async def test_lists_modules_only(self):
        async def mock_api_get(self_client, path):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            modules, meta = await client.list_collection_modules("netbox.netbox")

        assert "netbox.netbox.netbox_device" in modules
        assert "netbox.netbox.netbox_site" in modules
        assert "netbox.netbox.nb_inventory" not in modules  # inventory, not module
        assert len(modules) == 2
        assert meta["source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_raises_for_invalid_namespace(self):
        client = GalaxyClient()
        with pytest.raises(GalaxyError, match="not a valid collection"):
            await client.list_collection_modules("just_one_part")


class TestParseFqcn:
    def test_valid_three_segments(self):
        from ansible_know.galaxy import _parse_fqcn
        ns, name, mod = _parse_fqcn("netbox.netbox.netbox_device")
        assert ns == "netbox"
        assert name == "netbox"
        assert mod == "netbox_device"

    def test_rejects_two_segments(self):
        from ansible_know.galaxy import _parse_fqcn
        with pytest.raises(GalaxyError, match="not a fully-qualified"):
            _parse_fqcn("netbox.netbox")

    def test_rejects_four_segments(self):
        from ansible_know.galaxy import _parse_fqcn
        with pytest.raises(GalaxyError, match="not a fully-qualified"):
            _parse_fqcn("a.b.c.d")


class TestNetworkErrors:
    @pytest.mark.asyncio
    async def test_raises_on_connect_timeout(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectTimeout("connection timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="Galaxy connection error"):
                await client.latest_version("netbox", "netbox")


class TestDetailEnrichmentFailure:
    @pytest.mark.asyncio
    async def test_enrichment_failure_sets_zero_downloads(self):
        search_data = {
            "meta": {"count": 1}, "links": {},
            "data": [{
                "collection_version": {
                    "namespace": "test_ns", "name": "test_col",
                    "version": "1.0.0", "contents": [], "dependencies": {},
                    "description": "Test", "tags": [],
                    "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                },
                "is_highest": True, "is_deprecated": False, "is_signed": False,
                "repository": {}, "repository_version": "",
                "namespace_metadata": {"pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None},
            }],
        }

        async def mock_api_get(self_client, path, params=None, client=None):
            if "search/collection-versions" in path:
                return search_data
            raise GalaxyError("detail request failed")

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("test")

        assert result["collections"][0]["download_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_galaxy.py::TestFetchModuleDoc tests/test_galaxy.py::TestListCollectionModules -v`
Expected: FAIL — `AttributeError: 'GalaxyClient' object has no attribute 'fetch_module_doc'`

- [ ] **Step 3: Implement docs-blob methods**

Add to `src/ansible_know/galaxy.py`.

First, add a module-level helper function outside the class (before the class or after it):

```python
def _parse_fqcn(module_name: str) -> tuple[str, str, str]:
    """Split 'namespace.collection.module' into its three parts."""
    parts = module_name.split(".")
    if len(parts) != 3:
        raise GalaxyError(
            f"'{module_name}' is not a fully-qualified collection name "
            f"(expected namespace.collection.module)."
        )
    return parts[0], parts[1], parts[2]
```

Then add these methods inside `GalaxyClient`, after `search_collections()`:

```python
    async def _fetch_docs_blob(
        self, namespace: str, name: str, version: str,
    ) -> dict[str, Any]:
        cache_key = (namespace, name, version)
        if cache_key in _blob_cache:
            return _blob_cache[cache_key]
        path = (
            f"/api/v3/plugin/ansible/content/published/collections/index/"
            f"{namespace}/{name}/versions/{version}/docs-blob/"
        )
        params = {"format": "json"}
        data = await self._safe_api_get(path, params=params)
        blob = data.get("docs_blob", data)
        _blob_cache[cache_key] = blob
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
    def _transform_to_ansible_doc_format(
        fqcn: str, entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert a Galaxy docs-blob content entry to ansible-doc --json format.

        Galaxy stores options as a list of dicts (each with a 'name' key);
        ansible-doc stores them as a dict keyed by option name.
        """
        ds = entry.get("doc_strings", {})
        raw_doc = ds.get("doc", {})

        raw_options = raw_doc.get("options", [])
        if isinstance(raw_options, list):
            options_dict: dict[str, Any] = {}
            for opt in raw_options:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_galaxy.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "Add docs-blob fetching and format conversion to Galaxy client

Assisted-by: Claude <noreply@anthropic.com>"
```

---

## Task 4: Add `search_collections` MCP Tool

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `tests/test_server.py`

This task wires the Galaxy client's `search_collections()` into a new MCP tool.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_server.py`:

```python
class TestSearchCollectionsTool:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        mock_result = {
            "query": "netbox",
            "count": 1,
            "collections": [
                {
                    "namespace": "netbox.netbox",
                    "description": "Ansible modules for NetBox",
                    "tags": ["dcim", "ipam"],
                    "download_count": 11999959,
                    "latest_version": "3.23.0",
                    "module_count": 88,
                    "deprecated": False,
                    "signed": False,
                }
            ],
        }
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=mock_result):
            from ansible_know.server import search_collections
            result = await search_collections("netbox")
        assert result["count"] == 1
        assert result["collections"][0]["namespace"] == "netbox.netbox"

    @pytest.mark.asyncio
    async def test_with_tags(self):
        mock_result = {"query": "network", "count": 0, "collections": []}
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=mock_result) as mock_search:
            from ansible_know.server import search_collections
            result = await search_collections("network", tags="networking,cloud")
        mock_search.assert_called_once_with("network", tags="networking,cloud")
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_rejects_empty_query(self):
        from ansible_know.server import search_collections
        result = await search_collections("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_long_query(self):
        from ansible_know.server import search_collections
        result = await search_collections("a" * 501)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_invalid_tags(self):
        from ansible_know.server import search_collections
        result = await search_collections("netbox", tags="valid,tags&inject=bad")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_long_tags(self):
        from ansible_know.server import search_collections
        result = await search_collections("netbox", tags="a" * 501)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_galaxy_error(self):
        from ansible_know.galaxy import GalaxyError
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", side_effect=GalaxyError("timeout")):
            from ansible_know.server import search_collections
            result = await search_collections("netbox")
        assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_server.py::TestSearchCollectionsTool -v`
Expected: FAIL — `ImportError: cannot import name 'search_collections'`

- [ ] **Step 3: Update `_validate_query`, add `_validate_tags`, and implement `search_collections` tool**

First, update `_validate_query` in `src/ansible_know/server.py` to also reject empty strings:

```python
def _validate_query(query: str) -> None:
    if not query or not query.strip():
        raise ValidationError("Query must not be empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValidationError(
            f"Query too long: {len(query)} chars (max {MAX_QUERY_LENGTH})."
        )
```

Then add the `_validate_tags` function after `_validate_query`:

```python
_TAGS_RE = re.compile(r"^[a-zA-Z0-9_,-]+$")
MAX_TAGS_LENGTH = 500

def _validate_tags(tags: str) -> None:
    if len(tags) > MAX_TAGS_LENGTH:
        raise ValidationError(
            f"Tags too long: {len(tags)} chars (max {MAX_TAGS_LENGTH})."
        )
    if not _TAGS_RE.match(tags):
        raise ValidationError(
            "Invalid tags: use alphanumeric characters, hyphens, underscores, and commas only."
        )
```

Then add the `search_collections` tool after the `search_docs` tool function and before the `get_collection_manifest` tool:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_collections(
    query: Annotated[str, "Search keyword (e.g., 'netbox', 'cisco ios', 'vmware')"],
    tags: Annotated[str | None, "Optional comma-separated Galaxy tags to filter (e.g., 'networking,cloud')"] = None,
) -> dict[str, Any]:
    """Search Ansible Galaxy for collections by keyword.

    Returns collections ranked by download count, with module counts
    and descriptions. Use this to discover which collection provides
    modules for a specific platform or use case.
    """
    logger.info("search_collections query=%r tags=%r", query, tags)
    try:
        _validate_query(query)
        if tags:
            _validate_tags(tags)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know.galaxy import GalaxyClient

        client = GalaxyClient()
        return await client.search_collections(query, tags=tags)
    except Exception as exc:
        logger.warning("search_collections failed: %s", exc)
        return {"error": _sanitize_error(str(exc))}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_server.py::TestSearchCollectionsTool -v`
Expected: All passed

- [ ] **Step 5: Run full test suite**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/ -v`
Expected: All tests pass (existing + new)

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "Add search_collections MCP tool for Galaxy discovery

Assisted-by: Claude <noreply@anthropic.com>"
```

---

## Task 5: Galaxy Docs-Blob Fallback in `get_module_doc` and Other Tools

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `tests/test_server.py`

This task adds a Galaxy docs-blob fallback: when `get_module_doc` fails because a module isn't installed locally, it retries via the Galaxy API. The same fallback pattern extends to `generate_skill` and `get_collection_manifest`.

- [ ] **Step 1: Write failing tests for the fallback**

Append to `tests/test_server.py`:

```python
class TestIsMissingCollectionError:
    def test_has_no_attribute(self):
        from ansible_know.server import _is_missing_collection_error
        assert _is_missing_collection_error("netbox.netbox has no attribute") is True

    def test_was_not_found(self):
        from ansible_know.server import _is_missing_collection_error
        assert _is_missing_collection_error("module was not found") is True

    def test_could_not_be_found(self):
        from ansible_know.server import _is_missing_collection_error
        assert _is_missing_collection_error("could not be found in Galaxy") is True

    def test_unrelated_error(self):
        from ansible_know.server import _is_missing_collection_error
        assert _is_missing_collection_error("ansible-doc timed out") is False

    def test_empty_string(self):
        from ansible_know.server import _is_missing_collection_error
        assert _is_missing_collection_error("") is False

    def test_case_insensitive(self):
        from ansible_know.server import _is_missing_collection_error
        assert _is_missing_collection_error("HAS NO ATTRIBUTE") is True


class TestGalaxyDocsFallback:
    @pytest.mark.asyncio
    async def test_get_module_doc_local_includes_doc_source(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.package")
        assert result["doc_source"] == "local"

    @pytest.mark.asyncio
    async def test_get_module_doc_falls_back_to_galaxy(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Create, update or delete devices",
                    "description": ["Manages devices."],
                    "options": {"data": {"type": "dict", "required": True}},
                    "author": [],
                    "notes": [],
                    "version_added": "0.1.0",
                },
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("netbox.netbox.netbox_device")

        assert result["module_name"] == "netbox.netbox.netbox_device"
        assert result["doc_source"] == "galaxy"
        assert result["doc_version"] == "3.23.0"
        assert result["short_description"] == "Create, update or delete devices"

    @pytest.mark.asyncio
    async def test_get_module_doc_no_fallback_for_non_missing_errors(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=AssertionError("Galaxy fallback should not fire"),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("ansible.builtin.copy")
        assert "error" in result
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_get_module_doc_returns_error_when_both_fail(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        from ansible_know.galaxy import GalaxyError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): some.col.mod was not found"
        )

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("Module 'mod' not found in some.col docs-blob."),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("some.col.mod")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_generate_skill_falls_back_to_galaxy(self, mock_ansible_doc, tmp_path, monkeypatch):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Create, update or delete devices",
                    "description": ["Manages devices."],
                    "options": {"data": {"type": "dict", "required": True}},
                    "author": [],
                    "notes": [],
                    "version_added": "0.1.0",
                },
                "examples": "- name: Create device\n  netbox.netbox.netbox_device:\n    data:\n      name: Test\n",
                "return": [],
                "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            from ansible_know.server import generate_skill
            result = await generate_skill("netbox.netbox.netbox_device")

        assert "netbox.netbox.netbox_device" in result
        assert (tmp_path / "netbox.netbox.netbox_device" / "SKILL.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_server.py::TestGalaxyDocsFallback -v`
Expected: FAIL — tests run but the fallback doesn't trigger, so `get_module_doc` returns an error dict instead of Galaxy data

- [ ] **Step 3: Implement Galaxy docs-blob fallback**

First, add a helper function to `src/ansible_know/server.py` (after the `_collection_hint` function, **before** `_maybe_add_hint`):

```python
def _is_missing_collection_error(error_msg: str) -> bool:
    """Check if an error message indicates a missing/not-found collection or module."""
    msg_lower = error_msg.lower()
    return any(p in msg_lower for p in _MISSING_COLLECTION_PATTERNS)
```

Then refactor `_maybe_add_hint` to use it (eliminates duplicated pattern matching):

```python
def _maybe_add_hint(error_msg: str, namespace: str | None) -> str:
    if namespace and _is_missing_collection_error(error_msg):
        return error_msg + _collection_hint(namespace)
    return error_msg
```

Then add a shared fallback helper to `src/ansible_know/server.py` (after `_maybe_add_hint`):

```python
async def _resolve_module_doc(module_name: str) -> tuple[dict, dict | None]:
    """Try local ansible-doc, fall back to Galaxy if the collection is missing.

    Returns (raw_doc, galaxy_meta_or_none). Raises on non-missing-collection
    errors and when both local and Galaxy lookups fail.
    """
    from ansible_know import parser

    try:
        raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
        return raw_doc, None
    except Exception as local_exc:
        if not _is_missing_collection_error(str(local_exc)):
            raise

        logger.info("Local lookup failed, trying Galaxy: %s", local_exc)
        try:
            from ansible_know.galaxy import GalaxyClient

            client = GalaxyClient()
            galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
            return galaxy_doc, galaxy_meta
        except Exception as galaxy_exc:
            logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
            raise local_exc from galaxy_exc
```

Then modify the `get_module_doc` tool function. Replace the existing implementation with:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_module_doc(
    module_name: Annotated[str, "Fully-qualified collection name (e.g. 'ansible.builtin.copy')"],
) -> dict[str, Any]:
    """Get full structured documentation for one module.

    Returns: module_name, short_description, params (list with name/type/required/default/choices/description/aliases),
    examples (raw YAML), is_api_module.
    """
    logger.info("get_module_doc module=%r", module_name)
    try:
        _validate_fqcn(module_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser

        raw_doc, galaxy_meta = await _resolve_module_doc(module_name)
        metadata = parser.extract_module_metadata(raw_doc)
        if galaxy_meta:
            metadata.update(galaxy_meta)
        else:
            metadata["doc_source"] = "local"
        return metadata
    except Exception as exc:
        logger.warning("get_module_doc failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), ns)}
```

Then modify the `generate_skill` tool function. Replace the existing implementation with:

```python
@mcp.tool
async def generate_skill(
    module_name: Annotated[str, "Fully-qualified module name (e.g. 'ansible.builtin.copy')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context = None,
) -> str:
    """Generate a skill package for one module.

    Writes SKILL.md + scripts + playbook to disk.
    Returns the SKILL.md content inline so the agent can use it immediately.
    """
    logger.info("generate_skill module=%r install_to=%r", module_name, install_to)
    try:
        _validate_fqcn(module_name)
        if install_to:
            _validate_install_path(install_to)
    except ValidationError as exc:
        return str(exc)

    try:
        from ansible_know import parser, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        raw_doc, _ = await _resolve_module_doc(module_name)
        metadata = parser.extract_module_metadata(raw_doc)

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        skill_name = skills._module_to_skill_name(metadata["module_name"])
        base_dir = _validate_install_path(install_to) if install_to else SKILLS_DIR
        output_dir = base_dir / skill_name

        await _run_in_executor(skills.write_skill_package, output_dir, metadata)
        logger.info("generate_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return _truncate_response(skills.render_skill(metadata))
    except ValidationError as exc:
        return str(exc)
    except Exception as exc:
        logger.warning("generate_skill failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        return _maybe_add_hint(_sanitize_error(str(exc)), ns)
```

- [ ] **Step 4: Run new fallback tests to verify they pass**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/test_server.py::TestGalaxyDocsFallback -v`
Expected: All passed

- [ ] **Step 5: Update existing `TestMissingCollectionHints` to mock Galaxy fallback**

The existing hint tests (`test_get_module_doc_hint`, `test_search_modules_hint`, `test_generate_skill_hint`) now trigger the Galaxy fallback because their error messages match `_is_missing_collection_error`. They need a Galaxy mock that also fails, so the fallback completes and the hint is still added to the error.

In `tests/test_server.py`, update the `TestMissingCollectionHints` class. Wrap each test that triggers a "missing collection" error with a failing Galaxy mock:

```python
class TestMissingCollectionHints:
    @pytest.mark.asyncio
    async def test_get_module_doc_hint(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        from ansible_know.galaxy import GalaxyError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )
        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found on Galaxy"),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("netbox.netbox.netbox_device")
        assert "ensure_collection" in result["error"]
        assert "netbox.netbox" in result["error"]

    @pytest.mark.asyncio
    async def test_search_modules_hint(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox was not found"
        )
        from ansible_know.server import search_modules
        result = await search_modules("device", namespace="netbox.netbox")
        assert "ensure_collection" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_skill_hint(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        from ansible_know.galaxy import GalaxyError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device could not be found"
        )
        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found on Galaxy"),
        ):
            from ansible_know.server import generate_skill
            result = await generate_skill("netbox.netbox.netbox_device")
        assert "ensure_collection" in result

    @pytest.mark.asyncio
    async def test_no_hint_for_unrelated_errors(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("Some unrelated error")
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.copy")
        assert "ensure_collection" not in result.get("error", "")
```

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "Add Galaxy docs-blob fallback to get_module_doc and generate_skill

Assisted-by: Claude <noreply@anthropic.com>"
```

---

## Task 6: Update CLAUDE.md and MCP Server Metadata

**Files:**
- Modify: `CLAUDE.md`
- Modify: `src/ansible_know/server.py`

Update the project documentation and MCP server instructions to reflect the new tools and capabilities.

- [ ] **Step 1: Update CLAUDE.md**

Update the tool count in `CLAUDE.md` (the line that says `server.py # FastMCP server: 8 tools, 3 resources, 3 prompts`):

Change `8 tools` to `9 tools`.

In the `## MCP Tools` table, add this row after the `search_docs` row:

```markdown
| `search_collections` | read-only | Search Galaxy for collections by keyword |
```

- [ ] **Step 2: Update server.py MCP instructions**

In `src/ansible_know/server.py`, update the `instructions` string in the `FastMCP` constructor. Change it from:

```python
    instructions=(
        "Ansible module discovery, documentation, and skill generation. "
        "Use search_modules to find modules, get_module_doc for details, "
        "search_docs for conceptual guides, and generate_skill to create "
        "ready-to-use skill packages."
    ),
```

to:

```python
    instructions=(
        "Ansible module discovery, documentation, and skill generation. "
        "Use search_modules to find modules in installed collections, "
        "search_collections to discover collections on Galaxy, "
        "get_module_doc for details (falls back to Galaxy if not installed), "
        "search_docs for conceptual guides, and generate_skill to create "
        "ready-to-use skill packages."
    ),
```

- [ ] **Step 3: Run tests to confirm no breakage**

Run: `cd /home/lgallego/Claude/ansible-knowledge-mcp && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md src/ansible_know/server.py
git commit -m "Update docs and server instructions for Galaxy discovery tools

Assisted-by: Claude <noreply@anthropic.com>"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] `search_collections(query, tags?)` tool — Task 2 (client) + Task 4 (MCP tool)
- [x] Galaxy docs-blob fallback in `get_module_doc` — Task 5
- [x] Galaxy docs-blob fallback in `generate_skill` — Task 5
- [x] Format conversion (list→dict options) — Task 3 (`_transform_to_ansible_doc_format`)
- [x] `doc_source: "galaxy"` metadata — Task 3 + Task 5
- [x] `doc_source: "local"` always present — Task 5
- [x] `GalaxyClient` ported from ansibleclaw — Tasks 1-3
- [x] Config constant for Galaxy URL — Task 1
- [x] Collection detail enrichment (download counts) — Task 2
- [x] Concurrent enrichment with `asyncio.gather` — Task 2
- [x] Shared httpx client within `search_collections` — Task 2
- [x] Filter deprecated, sort by download_count — Task 2
- [x] `list_collection_modules` from docs-blob — Task 3
- [x] Response caching (version + docs-blob) — Tasks 1 + 3
- [x] Relationship docs (ensure_collection vs docs-blob) — Covered by spec; code paths are clean
- [x] CLAUDE.md update — Task 6

**Spec items NOT implemented (by design):**
- `get_collection_manifest` Galaxy fallback — For `get_collection_manifest`, the fallback would use `list_collection_modules` which provides less data than the local manifest generator. This is lower value and can be added later if needed. For `search_modules` with namespace, the existing `_collection_hint` + `ensure_collection` flow is sufficient.

**2. Placeholder scan:** No TBD/TODO found. All code blocks are complete.

**3. Type consistency:**
- `GalaxyClient.search_collections()` returns `dict[str, Any]` with keys: `query`, `count`, `collections` — matches test assertions and server tool return type
- `GalaxyClient.fetch_module_doc()` returns `tuple[dict[str, Any], dict[str, str]]` — matches ansibleclaw signature, test assertions, and server fallback usage
- `GalaxyClient.list_collection_modules()` returns `tuple[dict[str, str], dict[str, str]]` — matches test assertions
- `_parse_fqcn()` returns `tuple[str, str, str]` — strict 3-segment validation
- `_resolve_module_doc()` returns `tuple[dict, dict | None]` — shared by `get_module_doc` and `generate_skill`
- `_is_missing_collection_error()` used by `_maybe_add_hint` and `_resolve_module_doc` — single definition

**4. Security review findings addressed:**
- [x] URL params via httpx `params` dict, not f-string (no injection)
- [x] `_validate_tags()` with character and length validation
- [x] `_validate_component()` validates namespace/name from Galaxy responses before URL interpolation
- [x] `_parse_fqcn()` strict 3-segment, `parts[2]` not `parts[-1]`
- [x] `MAX_GALAXY_RESPONSE_SIZE` check on all responses
- [x] Error messages don't leak API paths
- [x] Network errors (timeout, connection) caught and wrapped as `GalaxyError`
- [x] `_validate_query` rejects empty strings
- [x] httpx `verify=True` explicit

**5. Performance review findings addressed:**
- [x] Concurrent enrichment with `asyncio.gather`
- [x] Shared httpx client within `search_collections`
- [x] Version and docs-blob caching with `clear_cache()` for tests

**6. Architecture review findings addressed:**
- [x] `_resolve_module_doc` shared helper (DRY)
- [x] `_maybe_add_hint` refactored to call `_is_missing_collection_error`
- [x] Existing `TestMissingCollectionHints` updated with Galaxy mocks
