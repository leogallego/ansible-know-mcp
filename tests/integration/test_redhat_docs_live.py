"""Integration tests for Red Hat Documentation MCP server.

These tests hit the live MCP server at docs-mcp.api.redhat.com.
Skipped by default — run with: pytest --run-integration

May require VPN access to docs-mcp.api.redhat.com.
"""

from __future__ import annotations

import json

import pytest

from ansible_know.docs import search_docs
from ansible_know.redhat_docs import RedHatDocsClient

pytestmark = pytest.mark.integration


class TestRedHatDocsClientLive:
    """Live tests against the Red Hat Documentation MCP server.

    Note: The MCP server has known issues with many guide-level URLs
    (documented 8/53 failures for AAP 2.6). Landing pages work reliably.
    """

    @pytest.mark.asyncio
    async def test_fetch_aap_27_landing(self):
        """Test fetching AAP 2.7 landing page returns valid JSON."""
        client = RedHatDocsClient()
        try:
            raw = await client.fetch(
                "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7"
            )
            data = json.loads(raw)
            assert "categoryTitles" in data
            assert data.get("product", "").startswith("Red Hat Ansible")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_fetch_aap_26_landing(self):
        """Test fetching AAP 2.6 landing page returns valid JSON."""
        client = RedHatDocsClient()
        try:
            raw = await client.fetch(
                "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6"
            )
            data = json.loads(raw)
            assert "categoryTitles" in data
            assert data.get("product", "").startswith("Red Hat Ansible")
        finally:
            await client.close()


class TestSearchDocsAapLive:
    """Live tests for search_docs with AAP manifests."""

    @pytest.mark.asyncio
    async def test_search_aap_install(self):
        results = await search_docs("install", source="aap-2.7")
        assert len(results) > 0
        assert all(r["source"] == "aap-2.7" for r in results)
