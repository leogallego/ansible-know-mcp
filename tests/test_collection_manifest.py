"""Tests for ansible_know.collection_manifest."""

import json

from ansible_know.collection_manifest import (
    _derive_tags,
    generate_manifest,
    load_cached_manifest,
)
from ansible_know.parser import extract_module_metadata


class TestDeriveTags:
    def test_ip_address_module(self):
        tags = _derive_tags("netbox.netbox.ip_address", [])
        assert "ipam" in tags

    def test_device_module(self):
        tags = _derive_tags("netbox.netbox.device", [])
        assert "dcim" in tags

    def test_docker_module(self):
        tags = _derive_tags("community.docker.docker_container", [])
        assert "containers" in tags

    def test_no_matching_tags(self):
        tags = _derive_tags("custom.collection.something_unique", [])
        assert tags == []

    def test_multiple_tags(self):
        tags = _derive_tags("some.collection.docker_network", [])
        assert "containers" in tags
        assert "networking" in tags


class TestGenerateManifest:
    def test_generates_manifest(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        assert manifest["collection"] == "ansible.builtin"
        assert manifest["module_count"] == 1
        assert len(manifest["modules"]) == 1

        mod = manifest["modules"][0]
        assert mod["fqcn"] == "ansible.builtin.package"
        assert mod["param_count"] == 3
        assert "name" in mod["required_params"]
        assert mod["is_api_module"] is False
        assert mod["has_skill"] is False

    def test_writes_manifest_file(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        manifest_path = tmp_path / "ansible" / "builtin" / "MANIFEST.json"
        assert manifest_path.exists()

        loaded = json.loads(manifest_path.read_text())
        assert loaded["collection"] == "ansible.builtin"

    def test_detects_existing_skills(self, tmp_path, sample_module_doc):
        skill_dir = tmp_path / "ansible.builtin.package"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("test")

        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        assert manifest["modules"][0]["has_skill"] is True

    def test_api_module_detection(self, tmp_path, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        manifest = generate_manifest("netbox.netbox", [metadata], skills_dir=tmp_path)

        assert manifest["modules"][0]["is_api_module"] is True


class TestLoadCachedManifest:
    def test_returns_none_when_not_cached(self, tmp_path):
        assert load_cached_manifest("nonexistent.collection", skills_dir=tmp_path) is None

    def test_returns_cached_manifest(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        cached = load_cached_manifest("ansible.builtin", skills_dir=tmp_path)
        assert cached is not None
        assert cached["collection"] == "ansible.builtin"
