"""Tests for ansible_know.galaxy."""

import time
from unittest.mock import AsyncMock, MagicMock, patch
from unittest.mock import patch as stdlib_patch

import httpx
import pytest

from ansible_know.errors import GalaxyError
from ansible_know.galaxy import (
    CACHE_TTL_SECONDS,
    MAX_GALAXY_RESPONSE_SIZE,
    TIMEOUT_DEFAULT,
    TIMEOUT_FAST,
    TIMEOUT_SLOW,
    GalaxyClient,
    _blob_cache,
    _version_cache,
    clear_cache,
)
from tests.conftest import SAMPLE_DOCS_BLOB_WITH_ROLES


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


class TestFetchCollectionDocs:
    @pytest.mark.asyncio
    async def test_returns_all_module_docs(self):
        """Batch extracts docs for every module in the blob."""
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            docs, meta = await client.fetch_collection_docs("netbox.netbox")

        assert "netbox.netbox.netbox_device" in docs
        assert "netbox.netbox.netbox_site" in docs
        assert len(docs) == 2  # only modules, not inventory plugin
        assert docs["netbox.netbox.netbox_device"]["module_name"] == "netbox.netbox.netbox_device"
        assert docs["netbox.netbox.netbox_device"]["short_description"] == "Create, update or delete devices"
        assert meta["doc_source"] == "galaxy"
        assert meta["doc_version"] == "3.23.0"

    @pytest.mark.asyncio
    async def test_with_explicit_version(self):
        """Explicit version skips latest_version lookup and omits warning."""
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "docs-blob" in path:
                assert "3.20.0" in path
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            docs, meta = await client.fetch_collection_docs(
                "netbox.netbox", version="3.20.0",
            )

        assert meta["doc_version"] == "3.20.0"
        assert "doc_warning" not in meta

    @pytest.mark.asyncio
    async def test_empty_collection_returns_empty_dict(self):
        """Collection with no modules returns empty dict, not error."""
        empty_blob = {"docs_blob": {"contents": [
            {"content_type": "inventory", "content_name": "nb_inventory",
             "doc_strings": {"doc": {"short_description": "Inv plugin"}, "examples": "", "return": [], "metadata": {}}},
        ]}}

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return empty_blob
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            docs, meta = await client.fetch_collection_docs("netbox.netbox")

        assert docs == {}
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_rejects_invalid_namespace(self):
        """Namespace must be namespace.name format."""
        client = GalaxyClient()
        with pytest.raises(GalaxyError, match="not a valid collection FQCN"):
            await client.fetch_collection_docs("just_one_part")

    @pytest.mark.asyncio
    async def test_silences_individual_module_failures(self, caplog):
        """Individual module extraction failures are logged and skipped."""
        blob_with_bad_module = {
            "docs_blob": {
                "contents": [
                    {
                        "content_type": "module",
                        "content_name": "good_module",
                        "doc_strings": {
                            "doc": {
                                "short_description": "A good module",
                                "description": [],
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
                        "content_type": "module",
                        "content_name": "bad_module",
                        "doc_strings": None,  # Will cause extraction to fail
                    },
                ],
            },
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return blob_with_bad_module
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            import logging
            with caplog.at_level(logging.WARNING, logger="ansible_know"):
                docs, meta = await client.fetch_collection_docs("netbox.netbox")

        assert "netbox.netbox.good_module" in docs
        assert "netbox.netbox.bad_module" not in docs
        assert len(docs) == 1
        assert any("bad_module" in r.message and "metadata extraction failed" in r.message for r in caplog.records)


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
        max_size = _version_cache.max_size
        for i in range(max_size + 5):
            _version_cache.put(("ns", f"col{i}"), f"1.0.{i}")
        assert _version_cache.get(("ns", "col0")) is None
        assert _version_cache.get(("ns", "col1")) is None
        assert _version_cache.get(("ns", f"col{max_size + 4}")) == f"1.0.{max_size + 4}"

    def test_blob_cache_evicts_oldest(self):
        max_size = _blob_cache.max_size
        for i in range(max_size + 5):
            _blob_cache.put(("ns", f"col{i}", "1.0.0"), {"idx": i})
        assert _blob_cache.get(("ns", "col0", "1.0.0")) is None
        assert _blob_cache.get(("ns", "col1", "1.0.0")) is None
        assert _blob_cache.get(("ns", f"col{max_size + 4}", "1.0.0")) == {"idx": max_size + 4}

    def test_version_cache_stays_at_max_size(self):
        max_size = _version_cache.max_size
        for i in range(max_size + 10):
            _version_cache.put(("ns", f"c{i}"), f"v{i}")
        assert len(_version_cache) <= max_size


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
        _version_cache.put(("netbox", "netbox"), "3.23.0")
        mock_client = _mock_client_get({})
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            version = await client.latest_version("netbox", "netbox")
        assert version == "3.23.0"
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_blob_cache_hit_skips_api(self):
        _version_cache.put(("netbox", "netbox"), "3.23.0")
        _blob_cache.put(("netbox", "netbox", "3.23.0"), SAMPLE_DOCS_BLOB["docs_blob"])

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
        _version_cache.put(("ns", "col"), "1.0.0")
        assert _version_cache.get(("ns", "col")) == "1.0.0"
        with stdlib_patch("ansible_know.cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS + 1
            assert _version_cache.get(("ns", "col")) is None

    def test_blob_cache_returns_none_after_ttl(self):
        _blob_cache.put(("ns", "col", "1.0.0"), {"data": "test"})
        assert _blob_cache.get(("ns", "col", "1.0.0")) == {"data": "test"}
        with stdlib_patch("ansible_know.cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS + 1
            assert _blob_cache.get(("ns", "col", "1.0.0")) is None

    def test_version_cache_returns_value_before_ttl(self):
        _version_cache.put(("ns", "col"), "2.0.0")
        with stdlib_patch("ansible_know.cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS - 10
            assert _version_cache.get(("ns", "col")) == "2.0.0"

    def test_blob_cache_returns_value_before_ttl(self):
        _blob_cache.put(("ns", "col", "1.0.0"), {"data": "fresh"})
        with stdlib_patch("ansible_know.cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS - 10
            assert _blob_cache.get(("ns", "col", "1.0.0")) == {"data": "fresh"}


class TestConcurrentCacheAccess:
    def test_concurrent_version_cache_writes(self):
        import threading

        errors = []

        def write_batch(start):
            try:
                for i in range(100):
                    _version_cache.put(("ns", f"col_{start}_{i}"), f"v{i}")
                    _version_cache.get(("ns", f"col_{start}_{i}"))
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
                    _blob_cache.put(("ns", f"col_{start}_{i}", "1.0"), {"v": i})
                    _blob_cache.get(("ns", f"col_{start}_{i}", "1.0"))
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
        _version_cache.put(("netbox", "netbox"), "3.23.0")
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


class TestFindRole:
    def test_finds_role_by_name(self):
        blob = SAMPLE_DOCS_BLOB_WITH_ROLES["docs_blob"]
        result = GalaxyClient._find_role(blob, "timesync")
        assert result is not None
        assert result["content_name"] == "timesync"
        assert result["content_type"] == "role"

    def test_returns_none_for_missing_role(self):
        blob = SAMPLE_DOCS_BLOB_WITH_ROLES["docs_blob"]
        result = GalaxyClient._find_role(blob, "nonexistent")
        assert result is None

    def test_does_not_match_modules(self):
        blob = SAMPLE_DOCS_BLOB_WITH_ROLES["docs_blob"]
        result = GalaxyClient._find_role(blob, "some_module")
        assert result is None


class TestListCollectionRoles:
    @pytest.mark.asyncio
    async def test_lists_roles_only(self):
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB_WITH_ROLES
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            roles, meta = await client.list_collection_roles("fedora.linux_system_roles")

        assert "fedora.linux_system_roles.timesync" in roles
        assert "fedora.linux_system_roles.network" in roles
        assert "fedora.linux_system_roles.some_module" not in roles
        assert len(roles) == 2
        assert meta["source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_raises_for_invalid_namespace(self):
        client = GalaxyClient()
        with pytest.raises(GalaxyError, match="not a valid collection"):
            await client.list_collection_roles("just_one_part")


class TestFetchRoleDoc:
    @pytest.mark.asyncio
    async def test_returns_structured_metadata(self):
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB_WITH_ROLES
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            role_meta, meta = await client.fetch_role_doc(
                "fedora.linux_system_roles.timesync",
            )

        assert role_meta["role_name"] == "fedora.linux_system_roles.timesync"
        assert "Configure time synchronization" in role_meta["short_description"]
        assert "main" in role_meta["entry_points"]
        options = role_meta["entry_points"]["main"]["options"]
        names = [o["name"] for o in options]
        assert "timesync_ntp_servers" in names

        assert meta["doc_source"] == "galaxy"
        assert meta["doc_version"] == "3.23.0"

    @pytest.mark.asyncio
    async def test_raises_for_missing_role(self):
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB_WITH_ROLES
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="not found"):
                await client.fetch_role_doc("fedora.linux_system_roles.nonexistent")

    @pytest.mark.asyncio
    async def test_handles_empty_readme_html(self):
        blob = {
            "docs_blob": {
                "contents": [
                    {
                        "content_type": "role",
                        "content_name": "empty_role",
                        "doc_strings": {},
                        "readme_html": "",
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
            role_meta, meta = await client.fetch_role_doc("some.col.empty_role")

        assert role_meta["role_name"] == "some.col.empty_role"
        assert role_meta["entry_points"]["main"]["options"] == []


class TestSearchCollectionsRoleCount:
    @pytest.mark.asyncio
    async def test_includes_role_count(self):
        search_with_roles = {
            "meta": {"count": 1}, "links": {},
            "data": [{
                "collection_version": {
                    "namespace": "fedora", "name": "linux_system_roles",
                    "version": "1.121.0",
                    "contents": [
                        {"content_name": "timesync", "content_type": "role"},
                        {"content_name": "network", "content_type": "role"},
                        {"content_name": "some_module", "content_type": "module"},
                    ],
                    "dependencies": {},
                    "description": "Linux system roles",
                    "tags": [],
                    "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                },
                "is_highest": True, "is_deprecated": False, "is_signed": False,
                "repository": {}, "repository_version": "",
                "namespace_metadata": {
                    "pulp_href": "", "name": "", "company": "",
                    "description": "", "avatar_url": None,
                },
            }],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_with_roles
            return {"download_count": 2600000, "highest_version": {"version": "1.121.0"}}

        with _mock_search_context(mock_api_get):
            client = GalaxyClient()
            result = await client.search_collections("linux system roles")

        col = result["collections"][0]
        assert col["role_count"] == 2
        assert col["module_count"] == 1


class TestGalaxyClientAuth:
    def test_token_auth_headers(self):
        gc = GalaxyClient(token="my_secret_token")
        headers = gc._auth_headers()
        assert headers["Authorization"] == "Token my_secret_token"
        assert headers["Accept"] == "application/json"

    def test_no_auth_headers(self):
        gc = GalaxyClient()
        headers = gc._auth_headers()
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/json"

    @pytest.mark.asyncio
    async def test_token_sent_in_request(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(http_client=mock_client, token="test_token")
        await gc.latest_version("netbox", "netbox")
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Token test_token"

    @pytest.mark.asyncio
    async def test_basic_auth_sent_in_request(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(http_client=mock_client, username="admin", password="secret")
        await gc.latest_version("netbox", "netbox")
        call_kwargs = mock_client.get.call_args[1]
        auth = call_kwargs.get("auth")
        assert auth is not None
        assert isinstance(auth, httpx.BasicAuth)

    def test_verify_false_on_owned_client(self):
        mock_client = AsyncMock()
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client) as mock_ctor:
            gc = GalaxyClient(verify=False)
            gc._get_client()
        mock_ctor.assert_called_once()
        assert mock_ctor.call_args[1]["verify"] is False

    def test_server_name_stored(self):
        gc = GalaxyClient(server_name="my_hub")
        assert gc.server_name == "my_hub"

    @pytest.mark.asyncio
    async def test_token_takes_precedence_over_basic_auth(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(
            http_client=mock_client,
            token="my_token",
            username="admin",
            password="secret",
        )
        await gc.latest_version("netbox", "netbox")
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Token my_token"
        assert "auth" not in call_kwargs

    def test_from_config(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        config = GalaxyServerConfig(
            name="test_hub",
            url="https://hub.example.com/api/galaxy",
            token="tok123",
            validate_certs=False,
        )
        gc = GalaxyClient.from_config(config)
        assert gc._base == "https://hub.example.com/api/galaxy"
        assert gc._token == "tok123"
        assert gc._verify is False
        assert gc.server_name == "test_hub"


class TestSsoTokenExchange:
    @pytest.mark.asyncio
    async def test_exchanges_offline_token_for_bearer(self):
        """When auth_url is set, token is exchanged via SSO."""
        sso_response = MagicMock()
        sso_response.json.return_value = {"access_token": "sso_access_123"}
        sso_response.raise_for_status.return_value = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_VERSIONS_RESPONSE
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = b"{}"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)
        mock_client.get = AsyncMock(return_value=mock_resp)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="offline_refresh_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        await gc.latest_version("redhat", "insights")

        mock_client.post.assert_called_once()
        post_call = mock_client.post.call_args
        assert "sso.example.com" in post_call[0][0]

        get_call = mock_client.get.call_args
        headers = get_call[1]["headers"]
        assert headers["Authorization"] == "Bearer sso_access_123"

    @pytest.mark.asyncio
    async def test_no_exchange_without_auth_url(self):
        """Without auth_url, uses Token auth as before."""
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(
            http_client=mock_client,
            token="plain_token",
        )
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"

        await gc.latest_version("netbox", "netbox")
        headers = mock_client.get.call_args[1]["headers"]
        assert headers["Authorization"] == "Token plain_token"

    @pytest.mark.asyncio
    async def test_caches_access_token(self):
        """SSO exchange only happens once, cached token reused."""
        sso_response = MagicMock()
        sso_response.json.return_value = {"access_token": "cached_token"}
        sso_response.raise_for_status.return_value = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_VERSIONS_RESPONSE
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = b"{}"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)
        mock_client.get = AsyncMock(return_value=mock_resp)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="offline_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        await gc.latest_version("ns1", "col1")
        await gc.latest_version("ns2", "col2")

        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_sso_failure_raises_galaxy_error(self):
        """SSO exchange failure surfaces as GalaxyError."""
        sso_response = MagicMock()
        sso_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401),
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="bad_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        with pytest.raises(GalaxyError, match="SSO.*token"):
            await gc.latest_version("redhat", "insights")

    @pytest.mark.asyncio
    async def test_retries_on_401_with_fresh_token(self):
        """When API returns 401 with auth_url, re-exchanges and retries once."""
        sso_response = MagicMock()
        sso_response.raise_for_status.return_value = None
        sso_response.json.return_value = {"access_token": "fresh_token_v2"}

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=resp_401,
        )
        resp_401.content = b""

        resp_200 = MagicMock()
        resp_200.json.return_value = SAMPLE_VERSIONS_RESPONSE
        resp_200.raise_for_status.return_value = None
        resp_200.content = b"{}"
        resp_200.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)
        mock_client.get = AsyncMock(side_effect=[resp_401, resp_200])

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="offline_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"
        gc._access_token = "expired_token_v1"

        version = await gc.latest_version("redhat", "insights")
        assert version == "3.23.0"
        assert mock_client.post.call_count == 1
        assert mock_client.get.call_count == 2

        retry_headers = mock_client.get.call_args_list[1][1]["headers"]
        assert retry_headers["Authorization"] == "Bearer fresh_token_v2"

    @pytest.mark.asyncio
    async def test_no_retry_on_401_without_auth_url(self):
        """Without auth_url, 401 raises immediately (no SSO to retry)."""
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=resp_401,
        )
        resp_401.content = b""

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp_401)

        gc = GalaxyClient(
            http_client=mock_client,
            token="plain_token",
        )
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"

        with pytest.raises(GalaxyError, match="Galaxy API error"):
            await gc.latest_version("netbox", "netbox")
        assert mock_client.get.call_count == 1


class TestRedirectFollowing:
    def test_owned_client_follows_redirects(self):
        mock_client = AsyncMock()
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client) as mock_ctor:
            gc = GalaxyClient()
            gc._get_client()
        mock_ctor.assert_called_once()
        assert mock_ctor.call_args[1]["follow_redirects"] is True


class TestFindPlugin:
    def test_finds_lookup_plugin(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "nb_lookup", "lookup")
        assert result is not None
        assert result["content_name"] == "nb_lookup"

    def test_finds_filter_plugin(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "nb_filter", "filter")
        assert result is not None

    def test_returns_none_for_missing(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "nonexistent", "lookup")
        assert result is None

    def test_does_not_match_module_as_plugin(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "netbox_device", "lookup")
        assert result is None


class TestFetchPluginDoc:
    @pytest.mark.asyncio
    async def test_fetches_lookup_doc(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                doc, meta = await client.fetch_plugin_doc(
                    "netbox.netbox.nb_lookup", "lookup",
                )
        assert "netbox.netbox.nb_lookup" in doc
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_raises_on_missing_plugin(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                with pytest.raises(GalaxyError, match="not found"):
                    await client.fetch_plugin_doc(
                        "netbox.netbox.nonexistent", "lookup",
                    )


class TestListCollectionPlugins:
    @pytest.mark.asyncio
    async def test_lists_plugins(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                plugins, meta = await client.list_collection_plugins("netbox.netbox")
        assert "netbox.netbox.nb_lookup" in plugins
        assert plugins["netbox.netbox.nb_lookup"]["plugin_type"] == "lookup"
        assert "netbox.netbox.nb_filter" in plugins
        assert "netbox.netbox.nb_inventory" in plugins
        assert len(plugins) == 3

    @pytest.mark.asyncio
    async def test_excludes_modules_and_roles(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                plugins, _ = await client.list_collection_plugins("netbox.netbox")
        fqcns = list(plugins.keys())
        assert not any("netbox_device" in f for f in fqcns)
