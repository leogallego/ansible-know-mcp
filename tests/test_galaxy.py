"""Tests for ansible_know.galaxy."""

import time
from unittest.mock import AsyncMock, MagicMock, patch
from unittest.mock import patch as stdlib_patch

import httpx
import pytest

from ansible_know.errors import GalaxyError
from ansible_know.galaxy import (
    CACHE_TTL_SECONDS,
    MAX_BLOB_CACHE_SIZE,
    MAX_GALAXY_RESPONSE_SIZE,
    MAX_VERSION_CACHE_SIZE,
    TIMEOUT_DEFAULT,
    TIMEOUT_FAST,
    TIMEOUT_SLOW,
    GalaxyClient,
    _get_blob_cache,
    _get_version_cache,
    _put_blob_cache,
    _put_version_cache,
    clear_cache,
)


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
    mock_resp.content = b"{}"

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
            "namespace_metadata": {
                "pulp_href": "", "name": "netbox", "company": "", "description": "", "avatar_url": None,
            },
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
            "namespace_metadata": {
                "pulp_href": "", "name": "deprecated_ns", "company": "", "description": "", "avatar_url": None,
            },
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


def _mock_search_context(mock_api_get_fn):
    """Patch _api_get for search_collections tests.

    Since search_collections now uses the GalaxyClient's _get_client method
    instead of creating its own httpx.AsyncClient, we only need to patch
    _api_get itself.
    """
    return patch.object(GalaxyClient, "_api_get", mock_api_get_fn)


class TestSearchCollections:
    @pytest.mark.asyncio
    async def test_returns_enriched_results(self):
        call_count = 0

        async def mock_api_get(self_client, path, params=None, timeout=None):
            nonlocal call_count
            call_count += 1
            if "search/collection-versions" in path:
                return SAMPLE_SEARCH_RESPONSE
            if "index/netbox/netbox/" in path:
                return SAMPLE_DETAIL_RESPONSE
            return {"download_count": 0, "highest_version": {"version": "1.0.0"}, "deprecated": True}

        with _mock_search_context(mock_api_get):
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
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return SAMPLE_SEARCH_RESPONSE
            if "index/netbox/netbox/" in path:
                return SAMPLE_DETAIL_RESPONSE
            return {"download_count": 50, "highest_version": {"version": "1.0.0"}, "deprecated": True}

        with _mock_search_context(mock_api_get):
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
                    "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
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
                    "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
                },
            ],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            if "high_downloads/col" in path:
                return {"download_count": 5000000, "highest_version": {"version": "2.0.0"}, "deprecated": False}
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}, "deprecated": False}

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("col")

        assert result["collections"][0]["namespace"] == "high_downloads.col"
        assert result["collections"][1]["namespace"] == "low_downloads.col"

    @pytest.mark.asyncio
    async def test_with_tags_filter(self):
        call_args = []

        async def mock_api_get(self_client, path, params=None, timeout=None):
            call_args.append({"path": path, "params": params})
            if "search/collection-versions" in path:
                return {"meta": {"count": 0}, "links": {}, "data": []}
            return {}

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("network", tags="networking")

        search_call = [c for c in call_args if "search/collection-versions" in c["path"]][0]
        assert search_call["params"]["tags"] == "networking"
        assert search_call["params"]["keywords"] == "network"
        assert result["query"] == "network"
        assert result["count"] == 0
        assert result["collections"] == []


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
                "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
            }],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            raise GalaxyError("detail request failed")

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("test")

        assert result["collections"][0]["download_count"] == 0

    @pytest.mark.asyncio
    async def test_all_enrichment_failures_still_returns_results(self):
        """When every detail call fails, results still come back with download_count=0."""
        search_data = {
            "meta": {"count": 2}, "links": {},
            "data": [
                {
                    "collection_version": {
                        "namespace": "ns1", "name": "col1",
                        "version": "1.0.0", "contents": [], "dependencies": {},
                        "description": "First", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
                },
                {
                    "collection_version": {
                        "namespace": "ns2", "name": "col2",
                        "version": "2.0.0", "contents": [], "dependencies": {},
                        "description": "Second", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
                },
            ],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            raise GalaxyError("all detail requests fail")

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("test")

        assert result["count"] == 2
        assert all(c["download_count"] == 0 for c in result["collections"])


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
        async def mock_api_get(self_client, path, params=None, timeout=None):
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
        assert "name" not in options["data"]

        assert meta["doc_source"] == "galaxy"
        assert meta["doc_version"] == "3.23.0"

    @pytest.mark.asyncio
    async def test_with_explicit_version(self):
        async def mock_api_get(self_client, path, params=None, timeout=None):
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
        async def mock_api_get(self_client, path, params=None, timeout=None):
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

        async def mock_api_get(self_client, path, params=None, timeout=None):
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
        async def mock_api_get(self_client, path, params=None, timeout=None):
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
        assert "netbox.netbox.nb_inventory" not in modules
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


class TestResponseSizeLimit:
    @pytest.mark.asyncio
    async def test_rejects_large_content_length_header(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"content-length": str(MAX_GALAXY_RESPONSE_SIZE + 1)}
        mock_resp.content = b"{}"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="too large"):
                await client.latest_version("netbox", "netbox")

    @pytest.mark.asyncio
    async def test_rejects_large_response_body(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {}
        mock_resp.content = b"x" * (MAX_GALAXY_RESPONSE_SIZE + 1)

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="too large"):
                await client.latest_version("netbox", "netbox")

    @pytest.mark.asyncio
    async def test_accepts_response_at_limit(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"content-length": str(MAX_GALAXY_RESPONSE_SIZE)}
        mock_resp.content = b"x" * MAX_GALAXY_RESPONSE_SIZE
        mock_resp.json.return_value = SAMPLE_VERSIONS_RESPONSE

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            version = await client.latest_version("netbox", "netbox")
        assert version == "3.23.0"

    @pytest.mark.asyncio
    async def test_malformed_content_length_falls_through(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"content-length": "not-a-number"}
        mock_resp.content = b"{}"
        mock_resp.json.return_value = SAMPLE_VERSIONS_RESPONSE

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            version = await client.latest_version("netbox", "netbox")
        assert version == "3.23.0"


class TestCacheEviction:
    def test_version_cache_evicts_oldest(self):
        for i in range(MAX_VERSION_CACHE_SIZE + 5):
            _put_version_cache(("ns", f"col{i}"), f"1.0.{i}")
        assert _get_version_cache(("ns", "col0")) is None
        assert _get_version_cache(("ns", "col1")) is None
        assert _get_version_cache(("ns", f"col{MAX_VERSION_CACHE_SIZE + 4}")) == f"1.0.{MAX_VERSION_CACHE_SIZE + 4}"

    def test_blob_cache_evicts_oldest(self):
        for i in range(MAX_BLOB_CACHE_SIZE + 5):
            _put_blob_cache(("ns", f"col{i}", "1.0.0"), {"idx": i})
        assert _get_blob_cache(("ns", "col0", "1.0.0")) is None
        assert _get_blob_cache(("ns", "col1", "1.0.0")) is None
        assert _get_blob_cache(("ns", f"col{MAX_BLOB_CACHE_SIZE + 4}", "1.0.0")) == {"idx": MAX_BLOB_CACHE_SIZE + 4}

    def test_version_cache_stays_at_max_size(self):
        from ansible_know.galaxy import _version_cache, _version_lock
        for i in range(MAX_VERSION_CACHE_SIZE + 10):
            _put_version_cache(("ns", f"c{i}"), f"v{i}")
        with _version_lock:
            assert len(_version_cache) <= MAX_VERSION_CACHE_SIZE


class TestNetworkErrors:
    @pytest.mark.asyncio
    async def test_raises_on_connect_timeout(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectTimeout("connection timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="Galaxy connection"):
                await client.latest_version("netbox", "netbox")

    @pytest.mark.asyncio
    async def test_raises_on_read_timeout(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ReadTimeout("read timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="timed out"):
                await client.latest_version("netbox", "netbox")

    @pytest.mark.asyncio
    async def test_raises_on_connect_error(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="connection error"):
                await client.latest_version("netbox", "netbox")

    @pytest.mark.asyncio
    async def test_raises_on_proxy_error(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ProxyError("proxy failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="connection error"):
                await client.latest_version("netbox", "netbox")


class TestCacheHitPaths:
    @pytest.mark.asyncio
    async def test_version_cache_hit_skips_api(self):
        _put_version_cache(("netbox", "netbox"), "3.23.0")
        mock_client = _mock_client_get({})
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            version = await client.latest_version("netbox", "netbox")
        assert version == "3.23.0"
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_blob_cache_hit_skips_api(self):
        _put_version_cache(("netbox", "netbox"), "3.23.0")
        _put_blob_cache(("netbox", "netbox", "3.23.0"), SAMPLE_DOCS_BLOB["docs_blob"])

        with patch.object(GalaxyClient, "_api_get", side_effect=AssertionError("should not call API")):
            client = GalaxyClient()
            doc, meta = await client.fetch_module_doc("netbox.netbox.netbox_device")

        assert "netbox.netbox.netbox_device" in doc
        assert meta["doc_source"] == "galaxy"


class TestSearchCollectionsEdgeCases:
    @pytest.mark.asyncio
    async def test_count_data_mismatch(self):
        """API reports count=5 but data only has 1 non-deprecated entry."""
        search_data = {
            "meta": {"count": 5},
            "links": {},
            "data": [
                {
                    "collection_version": {
                        "namespace": "ns", "name": "col",
                        "version": "1.0.0", "contents": [], "dependencies": {},
                        "description": "Test", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
                },
            ],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}}

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("test")

        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_empty_tags_in_content(self):
        """Tags that are not dicts should be skipped."""
        search_data = {
            "meta": {"count": 1}, "links": {},
            "data": [{
                "collection_version": {
                    "namespace": "ns", "name": "col",
                    "version": "1.0.0", "contents": [], "dependencies": {},
                    "description": "Test",
                    "tags": ["plain_string", {"name": "valid"}],
                    "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                },
                "is_highest": True, "is_deprecated": False, "is_signed": False,
                "repository": {}, "repository_version": "",
                "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None,
                },
            }],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            return {"download_count": 0, "highest_version": {"version": "1.0.0"}}

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("test")

        assert result["collections"][0]["tags"] == ["valid"]

    @pytest.mark.asyncio
    async def test_whitespace_query_rejected_by_server_tool(self):
        from ansible_know.server import search_collections as search_collections_tool
        result = await search_collections_tool("   ")
        assert "error" in result


class TestModuleWithoutDocStrings:
    @pytest.mark.asyncio
    async def test_list_modules_missing_doc_strings(self):
        blob = {
            "docs_blob": {
                "contents": [
                    {
                        "content_type": "module",
                        "content_name": "no_docs_module",
                    },
                    {
                        "content_type": "module",
                        "content_name": "has_docs",
                        "doc_strings": {
                            "doc": {"short_description": "Has docs"},
                            "examples": "",
                            "return": [],
                            "metadata": {},
                        },
                    },
                ],
            },
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return blob
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            modules, meta = await client.list_collection_modules("test.col")

        assert "test.col.no_docs_module" in modules
        assert modules["test.col.no_docs_module"] == ""
        assert "test.col.has_docs" in modules
        assert modules["test.col.has_docs"] == "Has docs"


class TestTransformEdgeCases:
    def test_missing_doc_strings(self):
        entry = {"content_type": "module", "content_name": "test"}
        result = GalaxyClient._transform_to_ansible_doc_format("ns.col.test", entry)
        doc = result["ns.col.test"]["doc"]
        assert doc["short_description"] == ""
        assert doc["options"] == {}

    def test_missing_short_description(self):
        entry = {
            "doc_strings": {
                "doc": {"description": ["Some desc"], "options": []},
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        result = GalaxyClient._transform_to_ansible_doc_format("ns.col.mod", entry)
        assert result["ns.col.mod"]["doc"]["short_description"] == ""

    def test_options_list_with_non_dict_items(self):
        entry = {
            "doc_strings": {
                "doc": {
                    "short_description": "Test",
                    "options": [
                        {"name": "valid", "type": "str"},
                        "not_a_dict",
                        42,
                    ],
                },
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        result = GalaxyClient._transform_to_ansible_doc_format("ns.col.mod", entry)
        opts = result["ns.col.mod"]["doc"]["options"]
        assert "valid" in opts
        assert len(opts) == 1

    def test_option_without_name_key(self):
        entry = {
            "doc_strings": {
                "doc": {
                    "short_description": "Test",
                    "options": [
                        {"type": "str", "required": True},
                    ],
                },
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        result = GalaxyClient._transform_to_ansible_doc_format("ns.col.mod", entry)
        assert result["ns.col.mod"]["doc"]["options"] == {}


class TestUnicodeQueries:
    @pytest.mark.asyncio
    async def test_unicode_search_collections(self):
        mock_result = {"query": "réseau", "count": 0, "collections": []}
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=mock_result):
            from ansible_know.server import search_collections as search_collections_tool
            result = await search_collections_tool("réseau")
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_unicode_search_docs(self):
        from ansible_know.server import search_docs as search_docs_tool
        with patch("ansible_know.docs.search_docs", return_value=[]):
            result = await search_docs_tool("配置管理")
        assert result == []


class TestCacheTTL:
    def test_version_cache_returns_none_after_ttl(self):
        _put_version_cache(("ns", "col"), "1.0.0")
        assert _get_version_cache(("ns", "col")) == "1.0.0"
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS + 1
            assert _get_version_cache(("ns", "col")) is None

    def test_blob_cache_returns_none_after_ttl(self):
        _put_blob_cache(("ns", "col", "1.0.0"), {"data": "test"})
        assert _get_blob_cache(("ns", "col", "1.0.0")) == {"data": "test"}
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS + 1
            assert _get_blob_cache(("ns", "col", "1.0.0")) is None

    def test_version_cache_returns_value_before_ttl(self):
        _put_version_cache(("ns", "col"), "2.0.0")
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS - 10
            assert _get_version_cache(("ns", "col")) == "2.0.0"

    def test_blob_cache_returns_value_before_ttl(self):
        _put_blob_cache(("ns", "col", "1.0.0"), {"data": "fresh"})
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS - 10
            assert _get_blob_cache(("ns", "col", "1.0.0")) == {"data": "fresh"}


class TestConcurrentCacheAccess:
    def test_concurrent_version_cache_writes(self):
        import threading

        errors = []

        def write_batch(start):
            try:
                for i in range(100):
                    _put_version_cache(("ns", f"col_{start}_{i}"), f"v{i}")
                    _get_version_cache(("ns", f"col_{start}_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_batch, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_blob_cache_writes(self):
        import threading

        errors = []

        def write_batch(start):
            try:
                for i in range(50):
                    _put_blob_cache(("ns", f"col_{start}_{i}", "1.0"), {"v": i})
                    _get_blob_cache(("ns", f"col_{start}_{i}", "1.0"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_batch, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


class TestTimeoutConstants:
    def test_timeout_fast_values(self):
        assert TIMEOUT_FAST.connect == 10.0
        assert TIMEOUT_FAST.read == 10.0
        assert TIMEOUT_FAST.write == 10.0
        assert TIMEOUT_FAST.pool == 10.0

    def test_timeout_default_values(self):
        assert TIMEOUT_DEFAULT.connect == 10.0
        assert TIMEOUT_DEFAULT.read == 30.0
        assert TIMEOUT_DEFAULT.write == 10.0
        assert TIMEOUT_DEFAULT.pool == 10.0

    def test_timeout_slow_values(self):
        assert TIMEOUT_SLOW.connect == 10.0
        assert TIMEOUT_SLOW.read == 60.0
        assert TIMEOUT_SLOW.write == 10.0
        assert TIMEOUT_SLOW.pool == 10.0


class TestTimeoutPassthrough:
    @pytest.mark.asyncio
    async def test_latest_version_uses_fast_timeout(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            await client.latest_version("netbox", "netbox")
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["timeout"] == TIMEOUT_FAST

    @pytest.mark.asyncio
    async def test_fetch_docs_blob_uses_slow_timeout(self):
        _put_version_cache(("netbox", "netbox"), "3.23.0")
        mock_client = _mock_client_get(SAMPLE_DOCS_BLOB)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            await client._fetch_docs_blob("netbox", "netbox", "3.23.0")
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["timeout"] == TIMEOUT_SLOW


class TestHttpClientInjection:
    @pytest.mark.asyncio
    async def test_uses_injected_client(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(http_client=mock_client)
        version = await gc.latest_version("netbox", "netbox")
        assert version == "3.23.0"
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_own_client_when_none(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            gc = GalaxyClient()
            version = await gc.latest_version("netbox", "netbox")
        assert version == "3.23.0"

    @pytest.mark.asyncio
    async def test_reuses_owned_client(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client) as mock_ctor:
            gc = GalaxyClient()
            await gc.latest_version("ns1", "col1")
            await gc.latest_version("ns2", "col2")
        assert mock_ctor.call_count == 1


class TestEnrichmentSemaphore:
    @pytest.mark.asyncio
    async def test_limits_concurrent_enrichment(self):
        import asyncio


        max_concurrent = 0
        current_concurrent = 0

        async def tracking_detail(self_client, namespace, name):
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.01)
            current_concurrent -= 1
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}}

        search_data = {
            "meta": {"count": 8}, "links": {},
            "data": [
                {
                    "collection_version": {
                        "namespace": f"ns{i}", "name": "col",
                        "version": "1.0.0", "contents": [], "dependencies": {},
                        "description": f"Col {i}", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {
                        "pulp_href": "", "name": "", "company": "",
                        "description": "", "avatar_url": None,
                    },
                }
                for i in range(8)
            ],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            with patch.object(GalaxyClient, "_get_collection_detail", tracking_detail):
                client = GalaxyClient()
                await client.search_collections("test")

        assert max_concurrent <= 5, f"Expected max 5 concurrent, got {max_concurrent}"


class TestGalaxyClientCleanup:
    @pytest.mark.asyncio
    async def test_close_closes_owned_client(self):
        mock_client = AsyncMock()
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            gc = GalaxyClient()
            gc._get_client()
            assert gc._owned_client is not None
            await gc.close()
        mock_client.aclose.assert_called_once()
        assert gc._owned_client is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_owned_client(self):
        gc = GalaxyClient()
        await gc.close()
        assert gc._owned_client is None

    @pytest.mark.asyncio
    async def test_close_noop_with_injected_client(self):
        injected = AsyncMock()
        gc = GalaxyClient(http_client=injected)
        await gc.close()
        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_context_manager_closes_owned(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            async with GalaxyClient() as gc:
                await gc.latest_version("netbox", "netbox")
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager_noop_with_injected(self):
        injected = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        async with GalaxyClient(http_client=injected) as gc:
            await gc.latest_version("netbox", "netbox")
        injected.aclose.assert_not_called()
