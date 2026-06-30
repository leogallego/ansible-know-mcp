"""Validate shipped doc manifests — guards auto-merge of weekly updates."""

import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "ansible_know" / "data"

REQUIRED_FILE_KEYS = {"path", "title", "summary"}

MINIMUM_COUNTS = {
    "ansible_core_manifest.json": 300,
    "ansible_lint_manifest.json": 40,
    "molecule_manifest.json": 15,
    "ansible_builder_manifest.json": 5,
    "ansible_navigator_manifest.json": 5,
    "ansible_creator_manifest.json": 3,
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


def test_manifest_entry_count_above_minimum(manifest):
    name, data = manifest
    if name not in MINIMUM_COUNTS:
        pytest.skip(f"no minimum defined for {name}")
    count = len(data["files"])
    minimum = MINIMUM_COUNTS[name]
    assert count >= minimum, (
        f"{name}: {count} entries, expected at least {minimum}"
    )
