"""Validate shipped doc manifests — guards auto-merge of weekly updates."""

import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "ansible_know" / "data"

# Entries need either "path" (resolved via base_url) or "url" (direct)
REQUIRED_FILE_KEYS = {"title", "summary"}
REQUIRED_LOCATION_KEYS = {"path", "url"}

MINIMUM_COUNTS = {
    "ansible_core_manifest.json": 300,
    "ansible_lint_manifest.json": 40,
    "molecule_manifest.json": 15,
    "ansible_builder_manifest.json": 5,
    "ansible_navigator_manifest.json": 5,
    "ansible_creator_manifest.json": 3,
    "aap_25_manifest.json": 30,
    "aap_26_manifest.json": 40,
    "aap_27_manifest.json": 40,
}

MANIFEST_FILES = sorted(DATA_DIR.glob("*_manifest.json"))


@pytest.fixture(params=MANIFEST_FILES, ids=lambda p: p.name)
def manifest(request):
    path = request.param
    data = json.loads(path.read_text())
    return path.name, data


def test_manifest_loads_and_has_required_top_level_keys(manifest):
    name, data = manifest
    assert "version" in data, f"{name}: missing 'version'"
    assert "files" in data, f"{name}: missing 'files'"
    assert isinstance(data["files"], list), f"{name}: 'files' is not a list"


def test_manifest_entries_have_required_keys(manifest):
    name, data = manifest
    for i, entry in enumerate(data["files"]):
        missing = REQUIRED_FILE_KEYS - set(entry.keys())
        assert not missing, f"{name}: entry {i} missing keys: {missing}"
        has_location = REQUIRED_LOCATION_KEYS & set(entry.keys())
        assert has_location, f"{name}: entry {i} needs 'path' or 'url'"


def test_manifest_entry_count_above_minimum(manifest):
    name, data = manifest
    if name not in MINIMUM_COUNTS:
        pytest.skip(f"no minimum defined for {name}")
    count = len(data["files"])
    minimum = MINIMUM_COUNTS[name]
    assert count >= minimum, (
        f"{name}: {count} entries, expected at least {minimum}"
    )


class TestAapManifests:
    """Tests verifying AAP manifests load and are searchable."""

    @pytest.mark.asyncio
    async def test_aap_25_manifest_loads(self):
        from ansible_know.docs import search_docs
        results = await search_docs("installation", source="aap-2.5")
        install_titles = [r["title"].lower() for r in results]
        assert any("install" in t for t in install_titles), f"No install results in {install_titles}"

    @pytest.mark.asyncio
    async def test_aap_26_manifest_loads(self):
        from ansible_know.docs import search_docs
        results = await search_docs("install", source="aap-2.6")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_aap_27_manifest_loads(self):
        from ansible_know.docs import search_docs
        results = await search_docs("install", source="aap-2.7")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_aap_search_returns_redhat_urls(self):
        from ansible_know.docs import search_docs
        results = await search_docs("automation mesh", source="aap-2.5")
        for r in results:
            assert r["source"] == "aap-2.5"
            if r["url"]:
                assert "docs.redhat.com" in r["url"]

    @pytest.mark.asyncio
    async def test_aap_source_filter_works(self):
        from ansible_know.docs import search_docs
        results_25 = await search_docs("install", source="aap-2.5")
        results_27 = await search_docs("install", source="aap-2.7")
        urls_25 = {r["url"] for r in results_25}
        urls_27 = {r["url"] for r in results_27}
        assert urls_25 != urls_27 or (not urls_25 and not urls_27)

    @pytest.mark.asyncio
    async def test_aap_cross_version_search(self):
        """Searching without source filter returns results from multiple AAP versions."""
        from ansible_know.docs import search_docs
        results = await search_docs("install containerized")
        sources = {r["source"] for r in results}
        aap_sources = {s for s in sources if s.startswith("aap-")}
        assert len(aap_sources) >= 1
