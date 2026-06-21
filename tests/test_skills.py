"""Tests for ansible_know.skills."""


from ansible_know.parser import extract_module_metadata
from ansible_know.skills import (
    _build_example_args,
    _collection_template_context,
    _extract_example_values,
    _role_template_context,
    module_to_skill_name,
    render_collection_skill,
    render_module_skill,
    render_role_skill,
    render_skill,
    write_collection_skill_package,
    write_module_skill_package,
    write_role_skill_package,
    write_skill_package,
)


class TestModuleToSkillName:
    def test_uses_fqcn(self):
        assert module_to_skill_name("ansible.builtin.package") == "ansible.builtin.package"

    def test_preserves_collection_prefix(self):
        assert module_to_skill_name("netbox.netbox.netbox_device") == "netbox.netbox.netbox_device"


class TestBackwardCompatAliases:
    def test_render_skill_is_render_module_skill(self):
        assert render_skill is render_module_skill

    def test_write_skill_package_is_write_module_skill_package(self):
        assert write_skill_package is write_module_skill_package


class TestExtractExampleValues:
    def test_extracts_key_values(self):
        examples = "- name: Install\n  ansible.builtin.package:\n    name: ntpdate\n    state: present\n"
        values = _extract_example_values(examples)
        assert values["name"] == "ntpdate"
        assert values["state"] == "present"

    def test_skips_complex_values(self):
        examples = "  data: {foo: bar}\n  list_val: [a, b]\n  simple: hello\n"
        values = _extract_example_values(examples)
        assert "data" not in values
        assert "list_val" not in values
        assert values["simple"] == "hello"

    def test_empty_input(self):
        assert _extract_example_values("") == {}


class TestBuildExampleArgs:
    def test_required_params(self):
        params = [
            {"name": "name", "required": True, "type": "str", "choices": None, "default": None},
            {"name": "state", "required": True, "type": "str", "choices": ["present", "absent"], "default": None},
        ]
        result = _build_example_args(params)
        assert "name=<name>" in result
        assert "state=present" in result

    def test_uses_example_values(self):
        params = [
            {"name": "name", "required": True, "type": "str", "choices": None, "default": None},
        ]
        examples = "    name: ntpdate\n    state: present\n"
        result = _build_example_args(params, examples)
        assert "name=ntpdate" in result

    def test_fallback_when_no_required(self):
        params = [
            {"name": "opt1", "required": False, "type": "str", "choices": None, "default": "val1"},
        ]
        result = _build_example_args(params)
        assert "opt1=val1" in result


class TestRenderModuleSkill:
    def test_renders_system_module(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_module_skill(metadata)
        assert "ansible.builtin.package" in content
        assert "Generic OS package manager" in content
        assert "## Parameters" in content
        assert "## When to Use This Skill" in content

    def test_renders_api_module(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_module_skill(metadata)
        assert "connection: local" in content.lower() or "connection:" in content.lower() or "API" in content


class TestWriteModuleSkillPackage:
    def test_writes_full_package(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        output_dir = tmp_path / "ansible.builtin.package"
        write_module_skill_package(output_dir, metadata)

        assert (output_dir / "SKILL.md").exists()
        assert (output_dir / "scripts" / "run.sh").exists()
        assert (output_dir / "scripts" / "check.sh").exists()
        assert (output_dir / "assets" / "playbook.yml").exists()

        skill_content = (output_dir / "SKILL.md").read_text()
        assert "ansible.builtin.package" in skill_content

    def test_scripts_are_executable(self, tmp_path, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        output_dir = tmp_path / "test_skill"
        write_module_skill_package(output_dir, metadata)

        import os
        run_sh = output_dir / "scripts" / "run.sh"
        assert os.access(run_sh, os.X_OK)


class TestRoleTemplateContext:
    def test_builds_context_from_role_metadata(self):
        metadata = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {
                "main": {
                    "description": "Configure time synchronization",
                    "options": [
                        {
                            "name": "timesync_ntp_servers",
                            "type": "list",
                            "required": False,
                            "default": "[]",
                            "description": "List of NTP servers",
                        },
                    ],
                },
            },
            "dependencies": [],
            "examples": "- hosts: all\n  roles:\n    - fedora.linux_system_roles.timesync",
            "doc_source": "galaxy_readme",
        }
        ctx = _role_template_context(metadata)
        assert ctx["role_name"] == "fedora.linux_system_roles.timesync"
        assert ctx["short_description"] == "Configure time synchronization"
        assert len(ctx["entry_points"]) == 1
        assert ctx["dependencies"] == []
        assert "hosts: all" in ctx["examples"]


class TestRenderRoleSkill:
    def test_renders_role_skill(self):
        metadata = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {
                "main": {
                    "description": "Configure time synchronization",
                    "options": [
                        {
                            "name": "timesync_ntp_servers",
                            "type": "list",
                            "required": False,
                            "default": "[]",
                            "description": "List of NTP servers",
                        },
                    ],
                },
            },
            "dependencies": [],
            "examples": "- hosts: all\n  roles:\n    - fedora.linux_system_roles.timesync",
            "doc_source": "local",
        }
        content = render_role_skill(metadata)
        assert "fedora.linux_system_roles.timesync" in content
        assert "Configure time synchronization" in content
        assert "timesync_ntp_servers" in content
        assert "## Variables" in content or "## Entry Points" in content


class TestWriteRoleSkillPackage:
    def test_writes_skill_and_playbook(self, tmp_path):
        metadata = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {
                "main": {
                    "description": "Configure time synchronization",
                    "options": [
                        {
                            "name": "timesync_ntp_servers",
                            "type": "list",
                            "required": False,
                            "default": "[]",
                            "description": "List of NTP servers",
                        },
                    ],
                },
            },
            "dependencies": [],
            "examples": "",
            "doc_source": "galaxy_readme",
        }
        output_dir = tmp_path / "fedora.linux_system_roles.timesync"
        write_role_skill_package(output_dir, metadata)

        assert (output_dir / "SKILL.md").exists()
        assert (output_dir / "assets" / "playbook.yml").exists()
        assert not (output_dir / "scripts").exists()

        skill_content = (output_dir / "SKILL.md").read_text()
        assert "fedora.linux_system_roles.timesync" in skill_content

        playbook_content = (output_dir / "assets" / "playbook.yml").read_text()
        assert "fedora.linux_system_roles.timesync" in playbook_content


class TestCollectionTemplateContext:
    def test_groups_modules_by_tag(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        ctx = _collection_template_context("netbox.netbox", [metadata])

        assert ctx["collection_namespace"] == "netbox.netbox"
        assert ctx["module_count"] == 1
        assert isinstance(ctx["modules_by_tag"], dict)
        found = False
        for modules in ctx["modules_by_tag"].values():
            for m in modules:
                if m["fqcn"] == "netbox.netbox.netbox_device":
                    found = True
        assert found

    def test_all_api_detection(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        ctx = _collection_template_context("netbox.netbox", [metadata])
        assert ctx["all_api"] is True

    def test_all_api_false_for_mixed(self, sample_module_doc, sample_api_module_doc):
        meta1 = extract_module_metadata(sample_module_doc)
        meta2 = extract_module_metadata(sample_api_module_doc)
        ctx = _collection_template_context("mixed.collection", [meta1, meta2])
        assert ctx["all_api"] is False

    def test_common_params_detection(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        ctx = _collection_template_context("netbox.netbox", [metadata])
        common_names = [p["name"] for p in ctx["common_params"]]
        assert "data" in common_names or "netbox_url" in common_names

    def test_empty_metadata_list(self):
        ctx = _collection_template_context("empty.collection", [])
        assert ctx["module_count"] == 0
        assert ctx["all_api"] is False
        assert ctx["common_params"] == []
        assert ctx["modules_by_tag"] == {}

    def test_untagged_modules_go_to_other(self):
        metadata = {
            "module_name": "custom.collection.something_unique",
            "short_description": "A unique module",
            "params": [],
            "examples": "",
            "is_api_module": False,
        }
        ctx = _collection_template_context("custom.collection", [metadata])
        assert "other" in ctx["modules_by_tag"]

    def test_collection_version(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        ctx = _collection_template_context("ansible.builtin", [metadata], collection_version="2.15.0")
        assert ctx["collection_version"] == "2.15.0"


class TestRenderCollectionSkill:
    def test_renders_codex(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata], collection_version="4.1.0")

        assert "netbox.netbox" in content
        assert "Playbook Codex" in content
        assert "Phase 1" in content
        assert "Phase 2" in content
        assert "Phase 5" in content
        assert "v4.1.0" in content

    def test_renders_api_connection_requirements(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])
        assert "connection: local" in content

    def test_renders_without_api_section_for_system_modules(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_collection_skill("ansible.builtin", [metadata])
        assert "connection: local" not in content


class TestWriteCollectionSkillPackage:
    def test_writes_skill_md_only(self, tmp_path, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        output_dir = tmp_path / "netbox.netbox"
        write_collection_skill_package(output_dir, "netbox.netbox", [metadata])

        assert (output_dir / "SKILL.md").exists()
        assert not (output_dir / "scripts").exists()
        assert not (output_dir / "assets").exists()

        content = (output_dir / "SKILL.md").read_text()
        assert "netbox.netbox" in content
        assert "Playbook Codex" in content
