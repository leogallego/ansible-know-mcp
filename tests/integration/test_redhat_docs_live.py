"""Integration tests for Red Hat Documentation MCP server.

These tests hit the live MCP server at docs-mcp.api.redhat.com.
Skipped by default — run with: pytest --run-integration

May require VPN access to docs-mcp.api.redhat.com.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from ansible_know.docs import search_docs
from ansible_know.errors import AnsibleKnowError
from ansible_know.redhat_docs import RedHatDocsClient, fetch_redhat_doc

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


class TestFetchRedhatDocHttpFallbackLive:
    """Live HTTP fallback for AAP 2.7 modular URLs rejected by RH Docs MCP."""

    @pytest.mark.asyncio
    async def test_aap27_modular_url_http_fallback(self):
        """Simulate MCP URL rejection and fetch via docs.redhat.com HTTP."""
        url = (
            "https://docs.redhat.com/en/documentation/"
            "red_hat_ansible_automation_platform/2.7/html/"
            "install-proc_installing_containerized_aap"
        )
        mock_mcp = AsyncMock()
        mock_mcp.fetch = AsyncMock(
            side_effect=AnsibleKnowError(
                "MCP tool redhat_docs_fetch error: "
                "Not a valid Red Hat Documentation link"
            )
        )

        result = await fetch_redhat_doc(url, client=mock_mcp)
        assert result["title"]
        assert len(result["content"]) > 100
        assert "install" in result["content"].lower() or "install" in result["title"].lower()
