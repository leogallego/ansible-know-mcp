"""Integration tests for real Galaxy API calls.

Run with: pytest --run-integration tests/integration/
Requires: network access to galaxy.ansible.com.
"""

import pytest

from ansible_know.errors import GalaxyError
from ansible_know.galaxy import GalaxyClient, clear_cache

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clean_cache():
    clear_cache()
    yield
    clear_cache()


class TestRealGalaxyAPI:
    @pytest.mark.asyncio
    async def test_latest_version_real_collection(self):
        async with GalaxyClient() as client:
            version = await client.latest_version("ansible", "netcommon")
        assert version
        parts = version.split(".")
        assert len(parts) >= 2

    @pytest.mark.asyncio
    async def test_search_collections_real(self):
        async with GalaxyClient() as client:
            result = await client.search_collections("network")
        assert result["count"] > 0
        assert len(result["collections"]) > 0
        first = result["collections"][0]
        assert "namespace" in first
        assert "description" in first

    @pytest.mark.asyncio
    async def test_fetch_module_doc_real(self):
        async with GalaxyClient() as client:
            doc, meta = await client.fetch_module_doc("ansible.netcommon.cli_command")
        assert "ansible.netcommon.cli_command" in doc
        assert meta["doc_source"] == "galaxy"
        assert meta["doc_version"]

    @pytest.mark.asyncio
    async def test_list_collection_modules_real(self):
        async with GalaxyClient() as client:
            modules, meta = await client.list_collection_modules("ansible.netcommon")
        assert len(modules) > 0
        assert meta["source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_nonexistent_collection_raises(self):
        async with GalaxyClient() as client:
            with pytest.raises(GalaxyError):
                await client.latest_version("nonexistent_ns_12345", "fake_col_67890")


class TestRealGalaxyAPIRoles:
    @pytest.mark.asyncio
    async def test_list_collection_roles_real(self):
        async with GalaxyClient() as client:
            roles, meta = await client.list_collection_roles("fedora.linux_system_roles")
        assert len(roles) > 0
        assert meta["source"] == "galaxy"
        assert any("timesync" in fqcn for fqcn in roles)

    @pytest.mark.asyncio
    async def test_fetch_role_doc_real(self):
        async with GalaxyClient() as client:
            role_meta, meta = await client.fetch_role_doc(
                "fedora.linux_system_roles.timesync",
            )
        assert role_meta["role_name"] == "fedora.linux_system_roles.timesync"
        assert meta["doc_source"] == "galaxy"
        assert "main" in role_meta["entry_points"]

    @pytest.mark.asyncio
    async def test_search_collections_includes_role_count(self):
        async with GalaxyClient() as client:
            result = await client.search_collections("linux system roles")
        if result["count"] > 0:
            first = result["collections"][0]
            assert "role_count" in first
