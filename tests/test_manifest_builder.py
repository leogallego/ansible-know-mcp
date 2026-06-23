"""Tests for ansible_know.manifest_builder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ansible_know.manifest_builder import _fetch_page_metadata, fetch_sitemap_urls, filter_guide_pages


class TestFilterGuidePages:
    def test_keeps_guide_pages(self):
        entries = [
            {"name": "playbook_guide/playbooks_intro", "display_name": "Intro"},
            {"name": "inventory_guide/intro_inventory", "display_name": "Inventory"},
        ]
        prefixes = {"playbook_guide", "inventory_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 2

    def test_excludes_collections(self):
        entries = [
            {"name": "playbook_guide/intro", "display_name": "Intro"},
            {"name": "collections/ansible/builtin/copy_module", "display_name": "copy"},
        ]
        prefixes = {"playbook_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 1
        assert result[0]["name"] == "playbook_guide/intro"

    def test_excludes_top_level(self):
        entries = [
            {"name": "index", "display_name": "Home"},
            {"name": "playbook_guide/intro", "display_name": "Intro"},
        ]
        prefixes = {"playbook_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 1

    def test_empty_entries(self):
        assert filter_guide_pages([], {"playbook_guide"}) == []


pytest.importorskip("defusedxml", reason="defusedxml is a [build] optional dependency")


class TestFetchSitemapUrlsBoundary:
    @pytest.mark.asyncio
    async def test_rejects_partial_prefix_match(self):
        """URLs where prefix is a substring but not a path-prefix must be excluded."""
        sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://docs.ansible.com/projects/lint/rules/</loc></url>
          <url><loc>https://docs.ansible.com/projects/lint-extra/page</loc></url>
          <url><loc>https://docs.ansible.com/projects/linter/config</loc></url>
        </urlset>"""

        mock_resp = MagicMock()
        mock_resp.text = sitemap_xml
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.manifest_builder.httpx.AsyncClient", return_value=mock_client):
            urls = await fetch_sitemap_urls(
                "https://docs.ansible.com/ansible-sitemap.xml",
                "/projects/lint",
            )

        assert len(urls) == 1
        assert "/lint/rules/" in urls[0]


class TestFetchPageMetadataSummary:
    @pytest.mark.asyncio
    async def test_single_line_content_extracts_summary(self):
        """Single-line markdown must still extract a summary."""
        markdown = "# Title\nIntroduction paragraph. More text follows here."
        mock_resp = MagicMock()
        mock_resp.text = markdown
        mock_resp.headers = {"content-type": "text/markdown", "x-markdown-tokens": "50"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_page_metadata("https://example.com/page", mock_client)
        assert result["summary"] != ""
        assert "Introduction" in result["summary"]

    @pytest.mark.asyncio
    async def test_multi_paragraph_extracts_first_sentence(self):
        """Multi-paragraph content extracts from the first body paragraph."""
        markdown = "# Title\n\nFirst paragraph sentence. Second sentence.\n\nAnother paragraph."
        mock_resp = MagicMock()
        mock_resp.text = markdown
        mock_resp.headers = {"content-type": "text/markdown", "x-markdown-tokens": "50"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_page_metadata("https://example.com/page", mock_client)
        assert "First paragraph sentence." in result["summary"]

    @pytest.mark.asyncio
    async def test_no_period_uses_truncation(self):
        """Content without a period-space uses first 200 chars as summary."""
        markdown = "# Title\n\nA long paragraph without any sentence endings"
        mock_resp = MagicMock()
        mock_resp.text = markdown
        mock_resp.headers = {"content-type": "text/markdown", "x-markdown-tokens": "50"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_page_metadata("https://example.com/page", mock_client)
        assert "long paragraph" in result["summary"]
