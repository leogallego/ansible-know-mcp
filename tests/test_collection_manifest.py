"""Tests for ansible_know.collection_manifest."""

import json

from ansible_know.collection_manifest import (
    generate_manifest,
    load_cached_manifest,
    write_manifest,
)
from ansible_know.parser import extract_module_metadata
from ansible_know.tagging import derive_tags


class TestDeriveTags:
    def test_ip_address_module(self):
        tags = derive_tags("netbox.netbox.ip_address", [])
        assert "ipam" in tags

    def test_device_module(self):
        tags = derive_tags("netbox.netbox.device", [])
        assert "dcim" in tags

    def test_docker_module(self):
        tags = derive_tags("community.docker.docker_container", [])
        assert "containers" in tags

    def test_no_matching_tags(self):
        tags = derive_tags("custom.collection.something_unique", [])
        assert tags == []

    def test_multiple_tags(self):
        tags = derive_tags("some.collection.docker_network", [])
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

    def test_generate_does_not_write_file(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        manifest_path = tmp_path / "ansible.builtin" / "MANIFEST.json"
        assert not manifest_path.exists()

    def test_writes_manifest_file(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)
        write_manifest(manifest, "ansible.builtin", skills_dir=tmp_path)

        manifest_path = tmp_path / "ansible-builtin" / "MANIFEST.json"
        assert manifest_path.exists()

        loaded = json.loads(manifest_path.read_text())
        assert loaded["collection"] == "ansible.builtin"

    def test_detects_existing_skills(self, tmp_path, sample_module_doc):
        skill_dir = tmp_path / "ansible-builtin" / "package"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("test")

        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        assert manifest["modules"][0]["has_skill"] is True

    def test_api_module_detection(self, tmp_path, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        manifest = generate_manifest("netbox.netbox", [metadata], skills_dir=tmp_path)

        assert manifest["modules"][0]["is_api_module"] is True

    def test_has_collection_skill_false_by_default(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        assert manifest["has_collection_skill"] is False

    def test_has_collection_skill_true_when_exists(self, tmp_path, sample_module_doc):
        skill_dir = tmp_path / "ansible-builtin"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("collection skill content")

        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)

        assert manifest["has_collection_skill"] is True


class TestGenerateManifestWithRoles:
    def test_includes_roles_section(self, tmp_path, sample_module_doc):
        from ansible_know.parser import extract_module_metadata

        module_meta = extract_module_metadata(sample_module_doc)
        roles_metadata = [
            {
                "fqcn": "ansible.builtin.test_role",
                "description": "A test role",
                "has_argument_specs": False,
                "entry_points": ["main"],
            },
        ]
        manifest = generate_manifest(
            "ansible.builtin", [module_meta],
            roles_metadata=roles_metadata, skills_dir=tmp_path,
        )

        assert "roles" in manifest
        assert "role_count" in manifest
        assert manifest["role_count"] == 1
        assert manifest["roles"][0]["fqcn"] == "ansible.builtin.test_role"
        assert manifest["roles"][0]["has_argument_specs"] is False

    def test_empty_roles_list(self, tmp_path, sample_module_doc):
        from ansible_know.parser import extract_module_metadata

        module_meta = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest(
            "ansible.builtin", [module_meta],
            roles_metadata=[], skills_dir=tmp_path,
        )

        assert manifest["role_count"] == 0
        assert manifest["roles"] == []

    def test_no_roles_metadata_defaults_empty(self, tmp_path, sample_module_doc):
        from ansible_know.parser import extract_module_metadata

        module_meta = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest(
            "ansible.builtin", [module_meta], skills_dir=tmp_path,
        )

        assert manifest["role_count"] == 0
        assert manifest["roles"] == []

    def test_role_has_skill_detection(self, tmp_path):
        skill_dir = tmp_path / "ansible-builtin" / "test-role"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("test")

        roles_metadata = [
            {
                "fqcn": "ansible.builtin.test_role",
                "description": "A test role",
                "has_argument_specs": True,
                "entry_points": ["main", "configure"],
            },
        ]
        manifest = generate_manifest(
            "ansible.builtin", [],
            roles_metadata=roles_metadata, skills_dir=tmp_path,
        )

        assert manifest["roles"][0]["has_skill"] is True
        assert manifest["roles"][0]["has_argument_specs"] is True
        assert manifest["roles"][0]["entry_points"] == ["main", "configure"]


class TestLoadCachedManifest:
    def test_returns_none_when_not_cached(self, tmp_path):
        assert load_cached_manifest("nonexistent.collection", skills_dir=tmp_path) is None

    def test_returns_cached_manifest(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        manifest = generate_manifest("ansible.builtin", [metadata], skills_dir=tmp_path)
        write_manifest(manifest, "ansible.builtin", skills_dir=tmp_path)

        cached = load_cached_manifest("ansible.builtin", skills_dir=tmp_path)
        assert cached is not None
        assert cached["collection"] == "ansible.builtin"


class TestManifestPluginEntries:
    def test_includes_plugins_in_manifest(self, tmp_path):
        plugins_metadata = [
            {
                "fqcn": "netbox.netbox.nb_lookup",
                "plugin_type": "lookup",
                "description": "Queries NetBox",
                "param_count": 3,
            },
            {
                "fqcn": "netbox.netbox.nb_inventory",
                "plugin_type": "inventory",
                "description": "NetBox dynamic inventory",
                "param_count": 5,
            },
        ]
        manifest = generate_manifest(
            "netbox.netbox", [], plugins_metadata=plugins_metadata,
            skills_dir=tmp_path,
        )
        assert manifest["plugin_count"] == 2
        assert len(manifest["plugins"]) == 2
        lookup = next(p for p in manifest["plugins"] if p["fqcn"] == "netbox.netbox.nb_lookup")
        assert lookup["plugin_type"] == "lookup"
        assert lookup["has_skill"] is False

    def test_empty_plugins_defaults(self, tmp_path):
        manifest = generate_manifest("test.test", [], skills_dir=tmp_path)
        assert manifest["plugin_count"] == 0
        assert manifest["plugins"] == []

    def test_plugin_skill_detection(self, tmp_path):
        skill_dir = tmp_path / "netbox-netbox" / "lookup-nb-lookup"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n")

        plugins_metadata = [
            {
                "fqcn": "netbox.netbox.nb_lookup",
                "plugin_type": "lookup",
                "description": "Queries NetBox",
                "param_count": 3,
            },
        ]
        manifest = generate_manifest(
            "netbox.netbox", [], plugins_metadata=plugins_metadata,
            skills_dir=tmp_path,
        )
        assert manifest["plugins"][0]["has_skill"] is True
