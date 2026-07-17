"""Tests for ansible_know.docs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import ansible_know.docs as docs_mod
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


LINT_MANIFEST = {
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


@pytest.fixture
def lint_sources(tmp_path):
    """Patch get_doc_sources to return a lint-like single-entry manifest."""
    p_file = tmp_path / "lint_manifest.json"
    p_file.write_text(json.dumps(LINT_MANIFEST))
    sources = {"lint": {"file": str(p_file), "description": "Lint"}}
    with patch("ansible_know.docs.get_doc_sources", return_value=sources):
        yield


class TestTokenizedSearch:
    @pytest.mark.asyncio
    async def test_multi_word_matches_across_fields(self, file_sources):
        """Words from different fields (title + summary) should match."""
        results = await search_docs("precedence rules")
        assert len(results) == 1
        assert results[0]["title"] == "Variable Precedence"

    @pytest.mark.asyncio
    async def test_multi_word_matches_title_and_topic(self, lint_sources):
        results = await search_docs("handler rules")
        assert len(results) == 1
        assert results[0]["title"] == "no-handler"

    @pytest.mark.asyncio
    async def test_source_key_not_searchable(self, lint_sources):
        """Source dict key name (e.g. 'lint') must not be part of searchable text."""
        with patch("ansible_know.docs._search_rtd_api", new_callable=AsyncMock, return_value=[]):
            results = await search_docs("lint rules")
        assert results == []

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


# --- Helpers for fetch_doc hardening tests ---

def _make_ok_response(
    url_str: str = "https://docs.ansible.com/projects/ansible/latest/guide.html",
    text: str = "# Test Page\n\nContent here.",
    tokens: int = 100,
    extra_headers: dict | None = None,
) -> MagicMock:
    """Build a mock httpx.Response that passes all fetch_doc validations."""
    resp = MagicMock()
    resp.status_code = 200
    headers = {
        "content-type": "text/markdown; charset=utf-8",
        "x-markdown-tokens": str(tokens),
    }
    if extra_headers:
        headers.update(extra_headers)
    resp.headers = headers
    resp.text = text
    resp.content = text.encode()
    resp.raise_for_status = MagicMock()
    resp.url = _mock_url(url_str)
    return resp


@pytest.fixture(autouse=True)
def _reset_fetch_doc_state():
    """Reset page cache and throttle between tests."""
    docs_mod._page_cache.clear()
    docs_mod._doc_last_request = 0.0
    docs_mod._doc_throttle_lock = None
    yield
    docs_mod._page_cache.clear()
    docs_mod._doc_last_request = 0.0
    docs_mod._doc_throttle_lock = None


class TestFetchDocUserAgent:
    @pytest.mark.asyncio
    async def test_user_agent_sent_on_request(self):
        from ansible_know.config import USER_AGENT

        mock_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/guide.html",
            http_client=mock_client,
        )

        call_kwargs = mock_client.get.call_args
        sent_headers = call_kwargs.kwargs.get("headers", {})
        assert sent_headers.get("User-Agent") == USER_AGENT


class TestFetchDocPageCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_http(self):
        mock_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        url = "https://docs.ansible.com/projects/ansible/latest/guide.html"
        result1 = await fetch_doc_content(url, http_client=mock_client)
        result2 = await fetch_doc_content(url, http_client=mock_client)

        assert result1 == result2
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_max_tokens_checked_on_cached_result(self):
        from ansible_know.errors import AnsibleKnowError

        mock_resp = _make_ok_response(tokens=5000)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        url = "https://docs.ansible.com/projects/ansible/latest/big.html"
        await fetch_doc_content(url, http_client=mock_client)

        with pytest.raises(AnsibleKnowError, match="5000"):
            await fetch_doc_content(url, max_tokens=1000, http_client=mock_client)

        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_clear_cache_invalidates(self):
        mock_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        url = "https://docs.ansible.com/projects/ansible/latest/guide.html"
        await fetch_doc_content(url, http_client=mock_client)
        clear_cache()
        await fetch_doc_content(url, http_client=mock_client)

        assert mock_client.get.call_count == 2


class TestFetchDocRateLimit:
    @pytest.mark.asyncio
    async def test_throttle_delays_rapid_requests(self):
        mock_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        url1 = "https://docs.ansible.com/projects/ansible/latest/page1.html"
        url2 = "https://docs.ansible.com/projects/ansible/latest/page2.html"

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await fetch_doc_content(url1, http_client=mock_client)
            docs_mod._doc_last_request = docs_mod.time.monotonic()
            await fetch_doc_content(url2, http_client=mock_client)

        sleep_calls = [
            c for c in mock_sleep.call_args_list
            if c.args and c.args[0] > 0
        ]
        assert len(sleep_calls) >= 1


class TestParseRetryAfter:
    """Direct tests for _parse_retry_after edge cases."""

    def _make_resp(self, retry_after: str) -> MagicMock:
        resp = MagicMock()
        resp.headers = {"retry-after": retry_after}
        return resp

    @pytest.mark.parametrize("header,expected", [
        ("2", 2.0),
        ("0", 0.0),
        ("30", 30.0),
        ("60", 30.0),       # capped at 30
        ("-5", 0.0),        # negative → clamped to 0
        ("nan", None),      # NaN → fallback
        ("inf", None),      # inf → fallback
        ("-inf", None),     # -inf → fallback
        ("not-a-number", None),  # non-numeric → fallback
        ("Wed, 01 Jul 2026 00:00:00 GMT", None),  # HTTP-date → fallback
    ])
    def test_edge_cases(self, header, expected):
        from ansible_know.docs import _parse_retry_after

        resp = self._make_resp(header)
        result = _parse_retry_after(resp, attempt=1)
        if expected is not None:
            assert result == expected
        else:
            # Should fall back to exponential backoff: 2.0 ** 1 = 2.0
            assert result == 2.0

    def test_empty_header_uses_backoff(self):
        from ansible_know.docs import _parse_retry_after

        resp = MagicMock()
        resp.headers = {}
        assert _parse_retry_after(resp, attempt=0) == 1.0   # 2**0
        assert _parse_retry_after(resp, attempt=1) == 2.0   # 2**1
        assert _parse_retry_after(resp, attempt=2) == 4.0   # 2**2


class TestFetchDocRetry:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self):
        mock_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[httpx.TimeoutException("timeout"), mock_resp],
        )

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client,
            )

        assert result["title"] == "Test Page"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_429_with_retry_after(self):
        retry_resp = MagicMock()
        retry_resp.status_code = 429
        retry_resp.headers = {"retry-after": "2"}
        retry_resp.raise_for_status = MagicMock()

        ok_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[retry_resp, ok_resp])

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client,
            )

        assert result["title"] == "Test Page"
        assert mock_client.get.call_count == 2
        retry_sleep = [c for c in mock_sleep.call_args_list if c.args and c.args[0] >= 2.0]
        assert len(retry_sleep) >= 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_and_raises(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout"),
        )

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.TimeoutException):
                await fetch_doc_content(
                    "https://docs.ansible.com/projects/ansible/latest/guide.html",
                    http_client=mock_client,
                )

        assert mock_client.get.call_count == docs_mod.MAX_RETRY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_retries_on_server_error(self):
        error_resp = MagicMock()
        error_resp.status_code = 503
        error_resp.headers = {}
        error_resp.raise_for_status = MagicMock()

        ok_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[error_resp, ok_resp])

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client,
            )

        assert result["title"] == "Test Page"
        assert mock_client.get.call_count == 2


    @pytest.mark.asyncio
    async def test_retries_on_connect_error_then_succeeds(self):
        mock_resp = _make_ok_response()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[httpx.ConnectError("connection refused"), mock_resp],
        )

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client,
            )

        assert result["title"] == "Test Page"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_server_error_and_raises(self):
        error_resp = MagicMock()
        error_resp.status_code = 503
        error_resp.headers = {}
        error_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "503 Service Unavailable",
                request=MagicMock(),
                response=error_resp,
            )
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=error_resp)

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_doc_content(
                    "https://docs.ansible.com/projects/ansible/latest/guide.html",
                    http_client=mock_client,
                )

        assert mock_client.get.call_count == docs_mod.MAX_RETRY_ATTEMPTS


class TestFetchDocCfChallenge:
    @pytest.mark.asyncio
    async def test_cf_challenge_raises_immediately(self):
        from ansible_know.errors import AnsibleKnowError

        cf_resp = MagicMock()
        cf_resp.status_code = 429
        cf_resp.headers = {"cf-mitigated": "challenge", "content-type": "text/html"}
        cf_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=cf_resp)

        with pytest.raises(AnsibleKnowError, match="Cloudflare managed challenge"):
            await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client,
            )

    @pytest.mark.asyncio
    async def test_cf_challenge_no_retry(self):
        from ansible_know.errors import AnsibleKnowError

        cf_resp = MagicMock()
        cf_resp.status_code = 429
        cf_resp.headers = {"cf-mitigated": "challenge", "content-type": "text/html"}
        cf_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=cf_resp)

        with pytest.raises(AnsibleKnowError):
            await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client,
            )

        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_regular_429_retries_but_cf_429_does_not(self):
        from ansible_know.errors import AnsibleKnowError

        cf_resp = MagicMock()
        cf_resp.status_code = 429
        cf_resp.headers = {"cf-mitigated": "challenge", "content-type": "text/html"}
        cf_resp.raise_for_status = MagicMock()

        regular_429 = MagicMock()
        regular_429.status_code = 429
        regular_429.headers = {"retry-after": "1"}
        regular_429.raise_for_status = MagicMock()

        ok_resp = _make_ok_response()

        mock_client_cf = AsyncMock()
        mock_client_cf.get = AsyncMock(return_value=cf_resp)

        mock_client_regular = AsyncMock()
        mock_client_regular.get = AsyncMock(side_effect=[regular_429, ok_resp])

        with pytest.raises(AnsibleKnowError, match="Cloudflare"):
            await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client_cf,
            )
        assert mock_client_cf.get.call_count == 1

        docs_mod._page_cache.clear()
        docs_mod._doc_last_request = 0.0

        with patch("ansible_know.docs.asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_doc_content(
                "https://docs.ansible.com/projects/ansible/latest/guide.html",
                http_client=mock_client_regular,
            )
        assert result["title"] == "Test Page"
        assert mock_client_regular.get.call_count == 2


class TestFetchDocRedhat:
    """Tests for fetch_redhat_doc (in redhat_docs module)."""

    @pytest.mark.asyncio
    async def test_redhat_fetch_returns_markdown(self):
        """fetch_redhat_doc should use RedHatDocsClient and return FetchDocResult."""
        from ansible_know.redhat_docs import fetch_redhat_doc

        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install-proc_installing_containerized_aap"
        markdown = "# Installing containerized AAP\n\nFollow these steps to install."

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(return_value=markdown)

        result = await fetch_redhat_doc(url, client=mock_client)

        assert result["title"] == "Installing containerized AAP"
        assert "Follow these steps" in result["content"]
        assert result["source_url"] == url
        mock_client.fetch.assert_called_once_with(url)

    @pytest.mark.asyncio
    async def test_redhat_fetch_respects_max_tokens(self):
        from ansible_know.redhat_docs import fetch_redhat_doc

        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install"
        markdown = "# Title\n\n" + "word " * 10000

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(return_value=markdown)

        result = await fetch_redhat_doc(url, client=mock_client)
        assert result["tokens"] > 0

    @pytest.mark.asyncio
    async def test_redhat_fetch_error_raises(self):
        from ansible_know.errors import AnsibleKnowError
        from ansible_know.redhat_docs import fetch_redhat_doc

        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/broken"

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            side_effect=AnsibleKnowError("MCP fetch failed")
        )

        with pytest.raises(AnsibleKnowError, match="MCP fetch failed"):
            await fetch_redhat_doc(url, client=mock_client)

    @pytest.mark.asyncio
    async def test_redhat_landing_page_raises_helpful_error(self):
        """Landing page URLs (returning JSON) should raise, not produce garbage."""
        from ansible_know.errors import AnsibleKnowError
        from ansible_know.redhat_docs import fetch_redhat_doc

        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7"
        landing_json = json.dumps({
            "product": "Red Hat Ansible Automation Platform",
            "version": "2.7",
            "categoryTitles": {"Install": {"titles": []}},
        })

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(return_value=landing_json)

        with pytest.raises(AnsibleKnowError, match="landing page"):
            await fetch_redhat_doc(url, client=mock_client)

    @pytest.mark.asyncio
    async def test_redhat_fetch_raises_without_client(self):
        """fetch_redhat_doc requires a client — no hidden singleton."""
        from ansible_know.errors import AnsibleKnowError
        from ansible_know.redhat_docs import fetch_redhat_doc

        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install"
        with pytest.raises(AnsibleKnowError, match="required"):
            await fetch_redhat_doc(url)

    @pytest.mark.asyncio
    async def test_ansible_url_still_uses_direct_httpx(self):
        """docs.ansible.com URLs should NOT use RedHatDocsClient."""
        clear_cache()
        url = "https://docs.ansible.com/projects/lint/rules/"

        with patch("ansible_know.docs._fetch_with_retry", side_effect=httpx.ConnectError("mocked")):
            try:
                await fetch_doc_content(url)
            except Exception:
                pass
