"""Tests for ansible_know.resolution module."""

import json
from unittest.mock import patch

import pytest

from tests.conftest import SAMPLE_MODULE_DOC, SAMPLE_ROLE_DOC


@pytest.fixture(autouse=True)
def reset_negative_cache():
    """Clear the negative cache before each test."""
    from ansible_know import resolution
    resolution._missing_collections.clear()
    yield
    resolution._missing_collections.clear()


@pytest.fixture
def mock_ansible_doc():
    with patch("ansible_know.parser._run_ansible_doc") as mock:
        yield mock


class TestResolveModuleDoc:
    """Tests migrated from test_server.py::TestResolveModuleDoc + new cases."""

    @pytest.mark.asyncio
    async def test_local_success_no_galaxy(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.resolution import resolve_module_doc
        raw_doc, galaxy_meta = await resolve_module_doc("ansible.builtin.package")
        assert "ansible.builtin.package" in raw_doc
        assert galaxy_meta is None

    @pytest.mark.asyncio
    async def test_non_missing_collection_error_not_retried(self, mock_ansible_doc):
        from ansible_know.errors import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=AssertionError("Galaxy should not be called"),
        ):
            from ansible_know.resolution import resolve_module_doc
            with pytest.raises(AnsibleDocError, match="timed out"):
                await resolve_module_doc("ansible.builtin.copy")

    @pytest.mark.asyncio
    async def test_galaxy_fallback_on_missing_collection(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Create, update or delete devices",
                    "description": ["Manages devices."],
                    "options": {"data": {"type": "dict", "required": True}},
                    "author": [], "notes": [], "version_added": "0.1.0",
                },
                "examples": "", "return": [], "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            from ansible_know.resolution import resolve_module_doc
            raw_doc, meta = await resolve_module_doc("netbox.netbox.netbox_device")

        assert raw_doc == galaxy_doc
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_both_fail_raises_local_error(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError, GalaxyError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): some.col.mod was not found"
        )

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("Module 'mod' not found in docs-blob"),
        ):
            from ansible_know.resolution import resolve_module_doc
            with pytest.raises(CollectionNotFoundError, match="was not found"):
                await resolve_module_doc("some.col.mod")


class TestResolveRoleDoc:

    @pytest.mark.asyncio
    async def test_local_success(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_ROLE_DOC)
        from ansible_know.resolution import resolve_role_doc
        result = await resolve_role_doc("fedora.linux_system_roles.gfs2")
        assert result["content_type"] == "role"
        assert result["doc_source"] == "local"

    @pytest.mark.asyncio
    async def test_galaxy_fallback_on_empty_doc(self, mock_ansible_doc):
        mock_ansible_doc.return_value = "{}"
        galaxy_role_meta = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {"main": {"description": "Configure time sync", "options": []}},
            "dependencies": [], "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.121.0", "doc_warning": "parsed from README"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            from ansible_know.resolution import resolve_role_doc
            result = await resolve_role_doc("fedora.linux_system_roles.timesync")

        assert result["doc_source"] == "galaxy_readme"
        assert result["content_type"] == "role"

    @pytest.mark.asyncio
    async def test_graceful_degradation(self, mock_ansible_doc):
        mock_ansible_doc.return_value = "{}"
        from ansible_know.errors import GalaxyError

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            side_effect=GalaxyError("not found"),
        ):
            from ansible_know.resolution import resolve_role_doc
            result = await resolve_role_doc("some.col.missing_role")

        assert result["doc_source"] == "unavailable"
        assert "error" in result


class TestNegativeCache:
    """Tests migrated from test_server.py::TestNegativeCache."""

    @pytest.mark.asyncio
    async def test_skips_local_on_cache_hit(self, mock_ansible_doc):
        from ansible_know import resolution
        resolution._missing_collections.add("netbox.netbox")

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Manage devices",
                    "description": [], "options": {},
                    "author": [], "notes": [], "version_added": "0.1.0",
                },
                "examples": "", "return": [], "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            raw_doc, meta = await resolution.resolve_module_doc("netbox.netbox.netbox_device")

        mock_ansible_doc.assert_not_called()
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_populates_cache_on_collection_not_found(self, mock_ansible_doc):
        from ansible_know import resolution
        from ansible_know.errors import CollectionNotFoundError, GalaxyError

        mock_ansible_doc.side_effect = CollectionNotFoundError("netbox.netbox has no attribute")

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found"),
        ):
            with pytest.raises(CollectionNotFoundError):
                await resolution.resolve_module_doc("netbox.netbox.netbox_device")

        assert "netbox.netbox" in resolution._missing_collections

    @pytest.mark.asyncio
    async def test_does_not_cache_non_collection_errors(self, mock_ansible_doc):
        from ansible_know import resolution
        from ansible_know.errors import AnsibleDocError

        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        with pytest.raises(AnsibleDocError):
            await resolution.resolve_module_doc("ansible.builtin.copy")

        assert "ansible.builtin" not in resolution._missing_collections

    def test_clear_missing_namespace(self):
        from ansible_know import resolution
        resolution._missing_collections.add("netbox.netbox")
        resolution.clear_missing_namespace("netbox.netbox")
        assert "netbox.netbox" not in resolution._missing_collections

    @pytest.mark.asyncio
    async def test_role_skips_local_on_cache_hit(self, mock_ansible_doc):
        from ansible_know import resolution
        resolution._missing_collections.add("some.col")

        galaxy_role_meta = {
            "role_name": "some.col.role",
            "short_description": "A role",
            "entry_points": {"main": {"description": "", "options": []}},
            "dependencies": [], "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.0.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            result = await resolution.resolve_role_doc("some.col.role")

        mock_ansible_doc.assert_not_called()
        assert result["doc_source"] == "galaxy_readme"


class TestSearchGalaxyCollections:

    @pytest.mark.asyncio
    async def test_merges_results_from_servers(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_galaxy_collections

        server1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        server2 = GalaxyServerConfig(name="hub", url="https://hub.internal.com")

        result1 = {"query": "net", "count": 1, "collections": [
            {"namespace": "netbox.netbox", "download_count": 100},
        ]}
        result2 = {"query": "net", "count": 1, "collections": [
            {"namespace": "cisco.nxos", "download_count": 200},
        ]}

        with patch("ansible_know.galaxy.GalaxyClient.search_collections") as mock_search:
            call_count = 0
            async def side_effect(query, tags=None):
                nonlocal call_count
                call_count += 1
                return result1 if call_count == 1 else result2
            mock_search.side_effect = side_effect
            result = await search_galaxy_collections(
                "net", galaxy_servers=[server1, server2],
            )

        assert result["count"] == 2
        assert result["collections"][0]["namespace"] == "cisco.nxos"
        assert result["collections"][1]["namespace"] == "netbox.netbox"

    @pytest.mark.asyncio
    async def test_deduplicates_by_namespace(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_galaxy_collections

        server1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        server2 = GalaxyServerConfig(name="hub", url="https://hub.internal.com")

        dup_result = {"query": "net", "count": 1, "collections": [
            {"namespace": "netbox.netbox", "download_count": 100},
        ]}

        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=dup_result):
            result = await search_galaxy_collections(
                "net", galaxy_servers=[server1, server2],
            )

        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_partial_server_failure(self):
        from ansible_know.errors import GalaxyError
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_galaxy_collections

        server1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        server2 = GalaxyServerConfig(name="hub", url="https://hub.internal.com")

        good_result = {"query": "net", "count": 1, "collections": [
            {"namespace": "netbox.netbox", "download_count": 100},
        ]}

        with patch("ansible_know.galaxy.GalaxyClient.search_collections") as mock_search:
            call_count = 0
            async def side_effect(query, tags=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise GalaxyError("timeout")
                return good_result
            mock_search.side_effect = side_effect
            result = await search_galaxy_collections(
                "net", galaxy_servers=[server1, server2],
            )

        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_all_servers_fail_raises(self):
        from ansible_know.errors import GalaxyError
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_galaxy_collections

        server = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")

        with patch("ansible_know.galaxy.GalaxyClient.search_collections",
                   side_effect=GalaxyError("timeout")):
            with pytest.raises(GalaxyError, match="All Galaxy servers failed"):
                await search_galaxy_collections("net", galaxy_servers=[server])
