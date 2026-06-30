"""Integration tests for Galaxy API root discovery.

Run with: pytest tests/integration/ --run-integration
For AH tests: set AH_TOKEN env var with offline token.
"""

import os

import pytest

from ansible_know.galaxy import GalaxyClient


@pytest.mark.integration
class TestPublicGalaxyDiscovery:
    @pytest.mark.asyncio
    async def test_discovery(self):
        """Discover API root from public Galaxy."""
        async with GalaxyClient(base_url="https://galaxy.ansible.com") as gc:
            await gc._discover_api_root()
            assert gc._api_root is not None
            assert gc._v3_path is not None
            assert "v3" in gc._v3_path

    @pytest.mark.asyncio
    async def test_latest_version(self):
        """Full round-trip: discover + fetch version."""
        async with GalaxyClient(base_url="https://galaxy.ansible.com") as gc:
            version = await gc.latest_version("ansible", "utils")
            assert version

    @pytest.mark.asyncio
    async def test_search_collections(self):
        """Search works via Pulp-specific path."""
        async with GalaxyClient(base_url="https://galaxy.ansible.com") as gc:
            result = await gc.search_collections("netbox")
            assert result["count"] > 0


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("AH_TOKEN"),
    reason="AH_TOKEN not set",
)
class TestAutomationHubDiscovery:
    @pytest.fixture
    async def ah_client(self):
        """Create an AH client with SSO auth."""
        gc = GalaxyClient(
            base_url="https://console.redhat.com/api/automation-hub/content/published",
            token=os.environ["AH_TOKEN"],
            auth_url="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
            server_name="automation_hub",
        )
        yield gc
        await gc.close()

    @pytest.mark.asyncio
    async def test_discovery(self, ah_client):
        await ah_client._discover_api_root()
        assert ah_client._api_root is not None
        assert ah_client._v3_path == "v3/"

    @pytest.mark.asyncio
    async def test_latest_version(self, ah_client):
        version = await ah_client.latest_version("redhat", "insights")
        assert version

    @pytest.mark.asyncio
    async def test_search_collections(self, ah_client):
        result = await ah_client.search_collections("redhat")
        assert result["count"] > 0

    @pytest.mark.asyncio
    async def test_fetch_module_doc(self, ah_client):
        doc, meta = await ah_client.fetch_module_doc("redhat.insights.insights_config")
        assert "redhat.insights.insights_config" in doc
        assert meta["doc_source"] == "galaxy"
