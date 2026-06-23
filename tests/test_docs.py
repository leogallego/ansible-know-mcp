"""Tests for ansible_know.docs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ansible_know.docs import _search_rtd_api, clean_rtd_markdown, clear_cache, fetch_doc_content, search_docs

MOCK_MANIFEST = {
    "version": "2.0",
    "generated": "2026-01-01T00:00:00Z",
    "base_url": "https://docs.example.com",
    "files": [
        {
            "path": "guide/intro.html",
            "topic": "guide",
            "title": "Introduction Guide",
            "summary": "How to get started with Ansible playbooks",
            "audience": "author",
            "core": True,
            "lines": 500,
        },
        {
            "path": "reference/variables.html",
            "topic": "reference",
            "title": "Variable Precedence",
            "summary": "Understanding Ansible variable precedence rules",
            "audience": "advanced",
            "core": True,
            "lines": 200,
        },
        {
            "path": "guide/galaxy.html",
            "topic": "guide",
            "title": "Galaxy User Guide",
            "summary": "How to use Ansible Galaxy to find and install roles",
            "audience": "beginner",
            "core": False,
            "lines": 300,
        },
    ],
}


@pytest.fixture(autouse=True)
def _clear_manifest_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def manifest_file(tmp_path):
    """Write MOCK_MANIFEST to a temp file and return its path."""
    p = tmp_path / "test_manifest.json"
    p.write_text(json.dumps(MOCK_MANIFEST))
    return str(p)


@pytest.fixture
def file_sources(manifest_file):
    """Patch get_doc_sources to return a file-based source."""
    sources = {
        "test-source": {
            "file": manifest_file,
            "description": "Test source",
        },
    }
    with patch("ansible_know.docs.get_doc_sources", return_value=sources):
        yield


class TestSearchDocsFileLoading:
    @pytest.mark.asyncio
    async def test_search_by_keyword(self, file_sources):
        results = await search_docs("playbook")
        assert len(results) == 1
        assert results[0]["title"] == "Introduction Guide"
        assert results[0]["source"] == "test-source"

    @pytest.mark.asyncio
    async def test_search_returns_url(self, file_sources):
        results = await search_docs("playbook")
        assert results[0]["url"] == "https://docs.example.com/guide/intro.html"

    @pytest.mark.asyncio
    async def test_search_returns_multiple(self, file_sources):
        results = await search_docs("ansible")
        assert len(results) >= 2

    @pytest.mark.asyncio
    async def test_filter_by_topic(self, file_sources):
        results = await search_docs("", topic="reference")
        assert len(results) == 1
        assert results[0]["title"] == "Variable Precedence"

    @pytest.mark.asyncio
    async def test_filter_by_audience(self, file_sources):
        results = await search_docs("", audience="advanced")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_core_only(self, file_sources):
        results = await search_docs("", core_only=True)
        titles = [r["title"] for r in results]
        assert "Galaxy User Guide" not in titles

    @pytest.mark.asyncio
    async def test_no_results(self, file_sources):
        results = await search_docs("nonexistent_xyz_query")
        assert results == []

    @pytest.mark.asyncio
    async def test_caches_after_first_load(self, file_sources, manifest_file):
        await search_docs("playbook")
        # Delete the file — cached version should still work
        Path(manifest_file).unlink()
        results = await search_docs("variable")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self):
        sources = {
            "missing": {
                "file": "/nonexistent/path/manifest.json",
                "description": "Missing",
            },
        }
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_manifest_version_warning(self, tmp_path, caplog):
        manifest = {**MOCK_MANIFEST, "version": "3.0"}
        p = tmp_path / "v3.json"
        p.write_text(json.dumps(manifest))
        sources = {"future": {"file": str(p), "description": "Future"}}
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("playbook")
        assert len(results) >= 1
        assert any("version" in r.message.lower() for r in caplog.records)


class TestCleanRtdMarkdown:
    def test_strips_breadcrumbs_before_h1(self):
        raw = "[Home](/) > [Guides](/guides)\n\n# My Page Title\n\nContent here."
        content, title = clean_rtd_markdown(raw)
        assert title == "My Page Title"
        assert content.startswith("# My Page Title")
        assert "Home" not in content

    def test_strips_doctype_artifact(self):
        raw = "<!DOCTYPE html>\n[Nav](/nav)\n\n# Title\n\nBody."
        content, title = clean_rtd_markdown(raw)
        assert "DOCTYPE" not in content
        assert title == "Title"

    def test_no_h1_keeps_all_content(self):
        raw = "Some content without any heading.\n\nMore content."
        content, title = clean_rtd_markdown(raw)
        assert content == raw
        assert title == ""

    def test_strips_anchor_from_title(self):
        raw = "# Page Title {#page-title}\n\nBody."
        content, title = clean_rtd_markdown(raw)
        assert title == "Page Title"

    def test_collapses_excessive_blank_lines(self):
        raw = "# Title\n\n\n\n\n\nContent."
        content, title = clean_rtd_markdown(raw)
        assert "\n\n\n" not in content
        assert "Content." in content

    def test_h2_before_h1_is_treated_as_nav(self):
        raw = "## Sidebar\n\nNav links\n\n# Main Title\n\nReal content."
        content, title = clean_rtd_markdown(raw)
        assert title == "Main Title"
        assert "Sidebar" not in content

    def test_empty_input(self):
        content, title = clean_rtd_markdown("")
        assert content == ""
        assert title == ""

    def test_doctype_on_later_line(self):
        raw = "Nav\n<!DOCTYPE html>\nMore nav\n# Title\n\nBody."
        content, title = clean_rtd_markdown(raw)
        assert "DOCTYPE" not in content
        assert title == "Title"


class TestFetchDocContent:
    @pytest.mark.asyncio
    async def test_returns_cleaned_content(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "text/markdown; charset=utf-8",
            "x-markdown-tokens": "100",
        }
        mock_resp.text = "[Nav](/)\n\n# Test Page\n\nHello world."
        mock_resp.raise_for_status = MagicMock()
        mock_resp.url = "https://docs.ansible.com/projects/ansible/latest/guide.html"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/guide.html",
            http_client=mock_client,
        )
        assert result["title"] == "Test Page"
        assert "Hello world." in result["content"]
        assert result["tokens"] == 100
        assert "Nav" not in result["content"]

    @pytest.mark.asyncio
    async def test_max_tokens_exceeded(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "text/markdown; charset=utf-8",
            "x-markdown-tokens": "5000",
        }
        mock_resp.text = "# Big Page\n\nLots of content."
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/big.html",
            max_tokens=1000,
            http_client=mock_client,
        )
        assert "error" in result
        assert "5000" in result["error"]

    @pytest.mark.asyncio
    async def test_non_markdown_content_type(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/page.html",
            http_client=mock_client,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_http_error_returns_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404),
        ))

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/missing.html",
            http_client=mock_client,
        )
        assert "error" in result


class TestSearchRtdApi:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        rtd_response = {
            "count": 1,
            "results": [
                {
                    "title": "Using Variables",
                    "path": "/projects/ansible/latest/playbook_guide/variables.html",
                    "domain": "https://docs.ansible.com",
                    "blocks": [
                        {"type": "section", "content": "Variables let you manage differences. More text here."}
                    ],
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = rtd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        results = await _search_rtd_api("variables", source="ansible-core", http_client=mock_client)
        assert len(results) == 1
        assert results[0]["title"] == "Using Variables"
        assert results[0]["source"].startswith("rtd-search:")
        assert "docs.ansible.com" in results[0]["url"]

    @pytest.mark.asyncio
    async def test_scoped_to_source(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"count": 0, "results": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await _search_rtd_api("rules", source="ansible-lint", http_client=mock_client)
        call_args = mock_client.get.call_args
        query_param = call_args.kwargs.get("params", {}).get("q", "")
        assert "ansible-lint" in query_param

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        results = await _search_rtd_api("test", http_client=mock_client)
        assert results == []

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        hits = [
            {
                "title": f"Result {i}",
                "path": f"/page{i}.html",
                "domain": "https://docs.ansible.com",
                "blocks": [],
            }
            for i in range(20)
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"count": 20, "results": hits}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        results = await _search_rtd_api("test", limit=5, http_client=mock_client)
        assert len(results) <= 5


class TestSearchDocsFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_rtd_when_manifest_empty(self):
        sources = {
            "test": {"file": "/nonexistent/manifest.json", "description": "Test"},
        }
        rtd_response = {
            "count": 1,
            "results": [
                {
                    "title": "RTD Result",
                    "path": "/projects/ansible/latest/guide.html",
                    "domain": "https://docs.ansible.com",
                    "blocks": [{"content": "Found via RTD search."}],
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = rtd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("guide", http_client=mock_client)

        assert len(results) >= 1
        assert results[0]["source"].startswith("rtd-search:")
