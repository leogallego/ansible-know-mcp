"""Tests for ansible_know.docs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ansible_know.docs import _search_rtd_api, clear_cache, fetch_doc_content, search_docs
from ansible_know.text_utils import clean_rtd_markdown

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
        with patch("ansible_know.docs._search_rtd_api", new_callable=AsyncMock, return_value=[]):
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
        with patch("ansible_know.docs.get_doc_sources", return_value=sources), \
             patch("ansible_know.docs._search_rtd_api", new_callable=AsyncMock, return_value=[]):
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


class TestManifestSizeLimit:
    @pytest.mark.asyncio
    async def test_rejects_large_content_length_url(self):
        from ansible_know.docs import MAX_MANIFEST_SIZE

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"content-length": str(MAX_MANIFEST_SIZE + 1)}
        mock_resp.content = b"{}"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.aclose = AsyncMock()

        sources = {"test": {"url": "https://example.com/manifest.json", "description": "Test"}}
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("test", http_client=mock_client)
        assert results == []

    @pytest.mark.asyncio
    async def test_rejects_large_body_url(self):
        from ansible_know.docs import MAX_MANIFEST_SIZE

        large_body = b"x" * (MAX_MANIFEST_SIZE + 1)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {}
        mock_resp.content = large_body

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.aclose = AsyncMock()

        sources = {"test": {"url": "https://example.com/manifest.json", "description": "Test"}}
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("test", http_client=mock_client)
        assert results == []

    @pytest.mark.asyncio
    async def test_rejects_large_file(self, tmp_path):
        from ansible_know.docs import MAX_MANIFEST_SIZE

        large_file = tmp_path / "large.json"
        large_file.write_bytes(b"x" * (MAX_MANIFEST_SIZE + 1))
        sources = {"test": {"file": str(large_file), "description": "Test"}}
        with patch("ansible_know.docs.get_doc_sources", return_value=sources), \
             patch("ansible_know.docs._search_rtd_api", new_callable=AsyncMock, return_value=[]):
            results = await search_docs("test")
        assert results == []


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


def _mock_url(url_str: str = "https://docs.ansible.com/test"):
    """Create a mock URL object with host attribute."""
    m = MagicMock()
    m.host = "docs.ansible.com"
    m.__str__ = lambda self: url_str
    return m


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
        mock_resp.content = mock_resp.text.encode()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.url = _mock_url("https://docs.ansible.com/projects/ansible/latest/guide.html")

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
        from ansible_know.errors import AnsibleKnowError

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "text/markdown; charset=utf-8",
            "x-markdown-tokens": "5000",
        }
        mock_resp.text = "# Big Page\n\nLots of content."
        mock_resp.content = mock_resp.text.encode()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.url = _mock_url()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(AnsibleKnowError, match="5000"):
            await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/big.html",
                max_tokens=1000,
                http_client=mock_client,
            )

    @pytest.mark.asyncio
    async def test_non_markdown_content_type(self):
        from ansible_know.errors import AnsibleKnowError

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.content = b"<html>not markdown</html>"
        mock_resp.raise_for_status = MagicMock()
        mock_resp.url = _mock_url()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(AnsibleKnowError, match="text/markdown"):
            await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/page.html",
                http_client=mock_client,
            )

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404),
        ))

        with pytest.raises(httpx.HTTPStatusError):
            await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/missing.html",
                http_client=mock_client,
            )


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


class TestTokenizedSearch:
    @pytest.mark.asyncio
    async def test_multi_word_matches_across_fields(self, file_sources):
        """Words from different fields (title + summary) should match."""
        results = await search_docs("precedence rules")
        assert len(results) == 1
        assert results[0]["title"] == "Variable Precedence"

    @pytest.mark.asyncio
    async def test_multi_word_matches_title_and_topic(self, tmp_path):
        manifest = {
            "version": "2.0",
            "base_url": "https://docs.example.com",
            "files": [
                {
                    "path": "rules/no-handler.html",
                    "topic": "rules",
                    "title": "no-handler",
                    "summary": "Use handlers instead of when: result.changed",
                    "audience": "author",
                    "lines": 50,
                },
            ],
        }
        p_file = tmp_path / "tok_manifest.json"
        p_file.write_text(json.dumps(manifest))
        sources = {"lint": {"file": str(p_file), "description": "Lint"}}
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("handler rules")
        assert len(results) == 1
        assert results[0]["title"] == "no-handler"

    @pytest.mark.asyncio
    async def test_single_word_still_works(self, file_sources):
        results = await search_docs("playbook")
        assert len(results) == 1
        assert results[0]["title"] == "Introduction Guide"

    @pytest.mark.asyncio
    async def test_word_order_does_not_matter(self, file_sources):
        r1 = await search_docs("precedence variable")
        r2 = await search_docs("variable precedence")
        assert len(r1) == len(r2) == 1
        assert r1[0]["title"] == r2[0]["title"]


class TestRtdInterleave:
    @pytest.mark.asyncio
    async def test_interleaves_across_sources(self):
        """Results from multiple sources should be interleaved, not concatenated."""
        source_a_hits = [
            {"title": f"A{i}", "path": f"/a{i}.html", "domain": "https://docs.ansible.com", "blocks": []}
            for i in range(5)
        ]
        source_b_hits = [
            {"title": f"B{i}", "path": f"/b{i}.html", "domain": "https://docs.ansible.com", "blocks": []}
            for i in range(5)
        ]

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            if call_count == 0:
                mock_resp.json.return_value = {"results": source_a_hits}
            else:
                mock_resp.json.return_value = {"results": source_b_hits}
            call_count += 1
            return mock_resp

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=mock_get)

        slugs = {"source-a": "slug-a", "source-b": "slug-b"}
        with patch("ansible_know.docs.RTD_PROJECT_SLUGS", slugs):
            results = await _search_rtd_api("test", limit=10, http_client=mock_client)

        sources = [r["source"] for r in results]
        assert "rtd-search:source-a" in sources
        assert "rtd-search:source-b" in sources
        assert results[0]["source"] != results[1]["source"]


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

    @pytest.mark.asyncio
    async def test_no_rtd_fallback_when_filters_cause_empty(self, file_sources):
        """RTD fallback must not fire when manifest had entries but filters narrowed to zero."""
        with patch("ansible_know.docs._search_rtd_api", new_callable=AsyncMock) as mock_rtd:
            mock_rtd.return_value = [{"title": "RTD hit", "summary": "", "topic": [],
                                      "audience": [], "lines": 0, "source": "rtd-search:x", "url": ""}]
            results = await search_docs("", core_only=True, topic="nonexistent_topic")
        mock_rtd.assert_not_called()
        assert results == []

    @pytest.mark.asyncio
    async def test_no_rtd_fallback_when_topic_filter_excludes_all(self, file_sources):
        """Topic filter narrows to zero — RTD must not bypass the filter."""
        with patch("ansible_know.docs._search_rtd_api", new_callable=AsyncMock) as mock_rtd:
            mock_rtd.return_value = [{"title": "RTD hit", "summary": "", "topic": [],
                                      "audience": [], "lines": 0, "source": "rtd-search:x", "url": ""}]
            results = await search_docs("ansible", topic="nonexistent_topic_xyz")
        mock_rtd.assert_not_called()
        assert results == []
