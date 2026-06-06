"""Tests for ansible_know.docs."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ansible_know.docs import MAX_MANIFEST_SIZE, clear_cache, search_docs

MOCK_MANIFEST = [
    {
        "title": "Ansible Playbook Guide",
        "summary": "How to write and run Ansible playbooks",
        "topics": ["playbooks", "getting-started"],
        "audience": ["beginner", "developer"],
        "core": True,
        "lines": 500,
        "url": "https://example.com/docs/playbook-guide.md",
    },
    {
        "title": "Variable Precedence",
        "summary": "Understanding Ansible variable precedence rules",
        "topics": ["variables", "reference"],
        "audience": ["advanced"],
        "core": True,
        "lines": 200,
        "url": "https://example.com/docs/variable-precedence.md",
    },
    {
        "title": "Galaxy User Guide",
        "summary": "How to use Ansible Galaxy to find and install roles",
        "topics": ["galaxy", "roles"],
        "audience": ["beginner"],
        "core": False,
        "lines": 300,
        "url": "https://example.com/docs/galaxy-guide.md",
    },
]


@pytest.fixture(autouse=True)
def _clear_manifest_cache():
    clear_cache()
    yield
    clear_cache()


def _mock_httpx_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_MANIFEST
    mock_resp.raise_for_status.return_value = None
    return mock_resp


@pytest.fixture
def mock_httpx():
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_httpx_response()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch("ansible_know.docs.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


class TestSearchDocs:
    @pytest.mark.asyncio
    async def test_search_by_keyword(self, mock_httpx):
        results = await search_docs("playbook")
        assert len(results) == 1
        assert results[0]["title"] == "Ansible Playbook Guide"
        assert results[0]["source"] == "ansible-core"

    @pytest.mark.asyncio
    async def test_search_returns_multiple(self, mock_httpx):
        results = await search_docs("ansible")
        assert len(results) >= 2

    @pytest.mark.asyncio
    async def test_filter_by_topic(self, mock_httpx):
        results = await search_docs("", topic="variables")
        assert len(results) == 1
        assert results[0]["title"] == "Variable Precedence"

    @pytest.mark.asyncio
    async def test_filter_by_audience(self, mock_httpx):
        results = await search_docs("", audience="advanced")
        assert len(results) == 1
        assert results[0]["title"] == "Variable Precedence"

    @pytest.mark.asyncio
    async def test_core_only(self, mock_httpx):
        results = await search_docs("", core_only=True)
        for r in results:
            assert r["title"] != "Galaxy User Guide"

    @pytest.mark.asyncio
    async def test_result_fields(self, mock_httpx):
        results = await search_docs("playbook")
        r = results[0]
        assert "title" in r
        assert "summary" in r
        assert "topic" in r
        assert "audience" in r
        assert "lines" in r
        assert "source" in r
        assert "url" in r

    @pytest.mark.asyncio
    async def test_caches_manifest(self, mock_httpx):
        await search_docs("playbook")
        await search_docs("variable")
        assert mock_httpx.get.call_count == 1

    @pytest.mark.asyncio
    async def test_no_results(self, mock_httpx):
        results = await search_docs("nonexistent_xyz_query")
        assert results == []


class TestManifestSizeLimit:
    @pytest.mark.asyncio
    async def test_rejects_large_content_length(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"content-length": str(MAX_MANIFEST_SIZE + 1)}
        mock_resp.content = b"{}"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.docs.httpx.AsyncClient", return_value=mock_client):
            results = await search_docs("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_rejects_large_body(self):
        large_body = b"x" * (MAX_MANIFEST_SIZE + 1)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {}
        mock_resp.content = large_body

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ansible_know.docs.httpx.AsyncClient", return_value=mock_client):
            results = await search_docs("test")
        assert results == []
