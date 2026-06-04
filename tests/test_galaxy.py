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


def _mock_search_context(mock_api_get_fn):
    """Patch both _api_get and httpx.AsyncClient for search_collections tests.

    search_collections creates a shared httpx.AsyncClient that may fail in
    environments with SOCKS proxies.  Since _api_get is fully mocked, the
    real client is never used — we just need its async-context-manager
    protocol to work.
    """
    dummy_client = _mock_client_get({})
    return (
        patch.object(GalaxyClient, "_api_get", mock_api_get_fn),
        patch("ansible_know.galaxy.httpx.AsyncClient", return_value=dummy_client),
    )


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

        p1, p2 = _mock_search_context(mock_api_get)
        with p1, p2:
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

        p1, p2 = _mock_search_context(mock_api_get)
        with p1, p2:
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

        p1, p2 = _mock_search_context(mock_api_get)
        with p1, p2:
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

        p1, p2 = _mock_search_context(mock_api_get)
        with p1, p2:
            client = GalaxyClient()
            result = await client.search_collections("network", tags="networking")

        search_call = [c for c in call_args if "search/collection-versions" in c["path"]][0]
        assert search_call["params"]["tags"] == "networking"
        assert search_call["params"]["keywords"] == "network"
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
                "namespace_metadata": {"pulp_href": "", "name": "", "company": "", "description": "", "avatar_url": None},
            }],
        }

        async def mock_api_get(self_client, path, params=None, client=None):
            if "search/collection-versions" in path:
                return search_data
            raise GalaxyError("detail request failed")

        p1, p2 = _mock_search_context(mock_api_get)
        with p1, p2:
            client = GalaxyClient()
            result = await client.search_collections("test")

        assert result["collections"][0]["download_count"] == 0


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
        async def mock_api_get(self_client, path, params=None, client=None):
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
        async def mock_api_get(self_client, path, params=None, client=None):
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
        async def mock_api_get(self_client, path, params=None, client=None):
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

        async def mock_api_get(self_client, path, params=None, client=None):
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
        async def mock_api_get(self_client, path, params=None, client=None):
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
