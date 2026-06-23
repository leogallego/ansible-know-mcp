"""Tests for ansible_know.docs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ansible_know.docs import clear_cache, search_docs

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
