"""Tests for ansible_know.skills."""

from ansible_know.parser import extract_module_metadata
from ansible_know.skills import (
    _build_example_args,
    _collection_template_context,
    _extract_example_values,
    _find_common_params,
    _role_template_context,
    collection_skill_name,
    fqcn_to_skill_name,
    module_to_skill_name,
    plugin_skill_name,
    render_collection_skill,
    render_module_skill,
    render_plugin_skill,
    render_role_skill,
    render_skill,
    role_skill_name,
    update_agents_md,
    write_collection_skill_package,
    write_module_skill_package,
    write_plugin_skill_package,
    write_role_skill_package,
    write_skill_package,
)


class TestFqcnToSkillName:
    """Test the core FQCN-to-kebab-case conversion."""

    def test_module_short_name_underscores_to_hyphens(self):
        assert fqcn_to_skill_name("netbox.netbox.netbox_device") == "netbox-device"

    def test_module_no_underscores(self):
        assert fqcn_to_skill_name("ansible.builtin.package") == "package"

    def test_module_multiple_underscores(self):
        assert fqcn_to_skill_name("ansible.builtin.apt_key") == "apt-key"

    def test_three_part_fqcn(self):
        assert fqcn_to_skill_name("community.general.redis_info") == "redis-info"


class TestPluginSkillName:
    """Test plugin skill naming: type-prefix + short name."""

    def test_lookup_plugin(self):
        assert plugin_skill_name("netbox.netbox.nb_lookup", "lookup") == "lookup-nb-lookup"

    def test_filter_plugin(self):
        assert plugin_skill_name("ansible.builtin.to_yaml", "filter") == "filter-to-yaml"

    def test_inventory_plugin(self):
        assert plugin_skill_name("netbox.netbox.nb_inventory", "inventory") == "inventory-nb-inventory"

    def test_connection_plugin(self):
        assert plugin_skill_name("ansible.netcommon.network_cli", "connection") == "connection-network-cli"

    def test_callback_plugin(self):
        assert plugin_skill_name("ansible.builtin.default", "callback") == "callback-default"


class TestRoleSkillName:
    """Test role skill naming: short name only, kebab-case."""

    def test_simple_role(self):
        assert role_skill_name("fedora.linux_system_roles.timesync") == "timesync"

    def test_role_with_underscores(self):
        assert role_skill_name("namespace.collection.my_role_name") == "my-role-name"


class TestCollectionSkillName:
    """Test collection-level skill naming: namespace kebab-case."""

    def test_dotted_namespace(self):
        assert collection_skill_name("netbox.netbox") == "netbox-netbox"

    def test_namespace_with_underscores(self):
        assert collection_skill_name("community.general") == "community-general"

    def test_namespace_mixed(self):
        assert collection_skill_name("fedora.linux_system_roles") == "fedora-linux-system-roles"


class TestModuleToSkillName:
    def test_returns_kebab_short_name(self):
        assert module_to_skill_name("ansible.builtin.package") == "package"

    def test_underscores_to_hyphens(self):
        assert module_to_skill_name("netbox.netbox.netbox_device") == "netbox-device"


class TestSpecCompliantFrontmatter:
    """Verify rendered skills contain agentskills.io-compliant frontmatter."""

    def test_module_skill_has_kebab_name(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_module_skill(metadata)
        assert "\nname: package\n" in content

    def test_module_skill_has_metadata_fqcn(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_module_skill(metadata)
        assert "fqcn: ansible.builtin.package" in content

    def test_module_skill_has_metadata_collection(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_module_skill(metadata)
        assert "collection: ansible.builtin" in content

    def test_module_skill_has_metadata_plugin_type(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_module_skill(metadata)
        assert "plugin-type: module" in content

    def test_module_skill_has_compatibility(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_module_skill(metadata)
        assert "compatibility:" in content
        assert "ansible-core" in content

    def test_api_module_skill_has_kebab_name(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_module_skill(metadata)
        assert "\nname: netbox-device\n" in content

    def test_api_module_skill_has_metadata(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_module_skill(metadata)
        assert "fqcn: netbox.netbox.netbox_device" in content
        assert "collection: netbox.netbox" in content
        assert "plugin-type: module" in content

    def test_plugin_skill_has_type_prefix_name(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        content = render_plugin_skill(metadata)
        assert "\nname: lookup-nb-lookup\n" in content

    def test_plugin_skill_has_metadata(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        content = render_plugin_skill(metadata)
        assert "fqcn: netbox.netbox.nb_lookup" in content
        assert "collection: netbox.netbox" in content
        assert "plugin-type: lookup" in content

    def test_plugin_skill_has_compatibility(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        content = render_plugin_skill(metadata)
        assert "compatibility:" in content
        assert "netbox.netbox" in content

    def test_role_skill_has_kebab_name(self):
        metadata = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {},
            "dependencies": [],
            "examples": "",
            "doc_source": "local",
        }
        content = render_role_skill(metadata)
        assert "\nname: timesync\n" in content

    def test_role_skill_has_metadata(self):
        metadata = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {},
            "dependencies": [],
            "examples": "",
            "doc_source": "local",
        }
        content = render_role_skill(metadata)
        assert "fqcn: fedora.linux_system_roles.timesync" in content
        assert "collection: fedora.linux_system_roles" in content
        assert "plugin-type: role" in content

    def test_role_skill_has_compatibility(self):
        metadata = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {},
            "dependencies": [],
            "examples": "",
            "doc_source": "local",
        }
        content = render_role_skill(metadata)
        assert "compatibility:" in content
        assert "fedora.linux_system_roles" in content

    def test_collection_skill_has_kebab_name(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])
        assert "\nname: netbox-netbox\n" in content

    def test_collection_skill_has_metadata(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])
        assert "collection: netbox.netbox" in content
        assert "plugin-type: collection" in content

    def test_collection_skill_has_compatibility(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])
        assert "compatibility:" in content
        assert "netbox.netbox" in content

    def test_module_skill_version_in_metadata(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        metadata["doc_version"] = "2.15.0"
        content = render_module_skill(metadata)
        assert 'version: "2.15.0"' in content

    def test_collection_skill_version_in_metadata(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata], collection_version="4.1.0")
        assert 'version: "4.1.0"' in content


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
        assert ctx["fqcn"] == "fedora.linux_system_roles.timesync"
        assert ctx["namespace"] == "fedora"
        assert ctx["collection_name"] == "linux_system_roles"
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

        assert ctx["fqcn"] == "netbox.netbox"
        assert ctx["namespace"] == "netbox"
        assert ctx["collection_name"] == "netbox"
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


class TestFindCommonParams:
    def test_empty_metadata(self):
        assert _find_common_params([]) == []

    def test_single_module_all_params_common(self):
        meta = {
            "module_name": "ns.col.mod",
            "params": [{"name": "url", "type": "str", "required": True}],
        }
        result = _find_common_params([meta])
        assert len(result) == 1
        assert result[0]["name"] == "url"

    def test_threshold_boundary_at_80_percent(self):
        shared_param = {"name": "token", "type": "str", "required": True}
        unique_param = {"name": "unique", "type": "str", "required": False}
        metas = [
            {"module_name": f"ns.col.mod{i}", "params": [shared_param]}
            for i in range(4)
        ]
        metas.append({"module_name": "ns.col.mod5", "params": [unique_param]})
        result = _find_common_params(metas)
        names = [p["name"] for p in result]
        assert "token" in names
        assert "unique" not in names

    def test_below_threshold_excluded(self):
        metas = [
            {"module_name": "ns.col.mod1", "params": [{"name": "a", "type": "str", "required": True}]},
            {"module_name": "ns.col.mod2", "params": [{"name": "b", "type": "str", "required": True}]},
            {"module_name": "ns.col.mod3", "params": [{"name": "c", "type": "str", "required": True}]},
        ]
        result = _find_common_params(metas)
        assert result == []


class TestRenderCollectionSkill:
    def test_renders_collection_skill(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata], collection_version="4.1.0")

        assert "netbox.netbox" in content
        assert "Playbook Guide" in content
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


class TestCollectionSkillCommonParamsRendering:
    def test_renders_common_params_table(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])

        assert "### Common Parameters" in content
        assert "netbox_url" in content
        assert "netbox_token" in content

    def test_renders_credential_vault_hint(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])

        assert "vault_netbox_url" in content
        assert "vault_netbox_token" in content

    def test_common_params_rendered_for_single_module(self, sample_module_doc):
        metadata = extract_module_metadata(sample_module_doc)
        content = render_collection_skill("ansible.builtin", [metadata])

        assert "### Common Parameters" in content

    def test_below_threshold_param_excluded(self, sample_api_module_doc, sample_module_doc):
        api_meta = extract_module_metadata(sample_api_module_doc)
        sys_meta = extract_module_metadata(sample_module_doc)
        metadata_list = [api_meta, api_meta, api_meta, api_meta, sys_meta]
        content = render_collection_skill("mixed.collection", metadata_list)

        common_section = content.split("### Common Parameters")[1].split("##")[0]
        assert "netbox_url" in common_section
        assert "| `name`" not in common_section

    def test_modules_by_tag_shows_required_params(self, sample_api_module_doc):
        metadata = extract_module_metadata(sample_api_module_doc)
        content = render_collection_skill("netbox.netbox", [metadata])

        assert "Key Params" in content
        assert "`data`" in content


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
        assert "Playbook Guide" in content


class TestRenderPluginSkill:
    def test_renders_lookup_skill(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries and returns elements from NetBox",
            "params": [
                {"name": "api_endpoint", "type": "str", "required": True,
                 "default": None, "choices": None, "description": "NetBox URL", "aliases": []},
                {"name": "token", "type": "str", "required": True,
                 "default": None, "choices": None, "description": "API token", "aliases": []},
            ],
            "examples": "- debug: msg=\"{{ query('netbox.netbox.nb_lookup', 'sites') }}\"",
        }
        result = render_plugin_skill(metadata)
        assert "lookup plugin" in result
        assert "query('netbox.netbox.nb_lookup'" in result
        assert "lookup('netbox.netbox.nb_lookup'" in result
        assert "ansible.builtin.uri" in result  # the "prefer this over uri" guidance

    def test_renders_filter_skill(self):
        metadata = {
            "plugin_name": "ansible.builtin.to_yaml",
            "plugin_type": "filter",
            "short_description": "Convert to YAML",
            "params": [],
            "examples": "",
        }
        result = render_plugin_skill(metadata)
        assert "filter plugin" in result
        assert "ansible.builtin.to_yaml" in result

    def test_renders_inventory_skill(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_inventory",
            "plugin_type": "inventory",
            "short_description": "NetBox inventory source",
            "params": [
                {"name": "api_endpoint", "type": "str", "required": True,
                 "default": None, "choices": None, "description": "NetBox URL", "aliases": []},
            ],
            "examples": "",
        }
        result = render_plugin_skill(metadata)
        assert "inventory plugin" in result
        assert "plugin: netbox.netbox.nb_inventory" in result

    def test_renders_connection_skill(self):
        metadata = {
            "plugin_name": "ansible.netcommon.network_cli",
            "plugin_type": "connection",
            "short_description": "CLI connection to network devices",
            "params": [],
            "examples": "",
        }
        result = render_plugin_skill(metadata)
        assert "connection plugin" in result
        assert "connection: ansible.netcommon.network_cli" in result


class TestWritePluginSkillPackage:
    def test_writes_skill_md(self, tmp_path):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        write_plugin_skill_package(tmp_path / "lookup__nb_lookup", metadata)
        assert (tmp_path / "lookup__nb_lookup" / "SKILL.md").exists()

    def test_no_scripts_directory(self, tmp_path):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        write_plugin_skill_package(tmp_path / "lookup__nb_lookup", metadata)
        assert not (tmp_path / "lookup__nb_lookup" / "scripts").exists()

    def test_no_assets_directory(self, tmp_path):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        write_plugin_skill_package(tmp_path / "lookup__nb_lookup", metadata)
        assert not (tmp_path / "lookup__nb_lookup" / "assets").exists()


class TestCollectionSkillWithPlugins:
    def test_includes_plugins_section(self):
        metadata_list = [{
            "module_name": "netbox.netbox.netbox_device",
            "short_description": "Manage devices",
            "params": [],
            "examples": "",
            "is_api_module": True,
        }]
        plugins = [
            {"fqcn": "netbox.netbox.nb_lookup", "plugin_type": "lookup",
             "description": "Query NetBox", "param_count": 0},
            {"fqcn": "netbox.netbox.nb_inventory", "plugin_type": "inventory",
             "description": "Dynamic inventory", "param_count": 0},
        ]
        result = render_collection_skill(
            "netbox.netbox", metadata_list,
            plugins_metadata=plugins,
        )
        assert "Available Plugins" in result
        assert "nb_lookup" in result
        assert "nb_inventory" in result
        assert "Lookup" in result
        assert "Inventory" in result

    def test_no_plugins_section_when_empty(self):
        metadata_list = [{
            "module_name": "netbox.netbox.netbox_device",
            "short_description": "Manage devices",
            "params": [],
            "examples": "",
            "is_api_module": True,
        }]
        result = render_collection_skill("netbox.netbox", metadata_list)
        assert "Available Plugins" not in result


class TestUpdateAgentsMd:
    def _make_collection_skill(self, skills_dir, name):
        """Create a minimal collection skill directory with SKILL.md."""
        coll_dir = skills_dir / name
        coll_dir.mkdir(parents=True)
        (coll_dir / "SKILL.md").write_text(
            "---\nname: test\ndescription: test collection\n---\n"
        )
        # Add a module-level skill for example path generation
        mod_dir = coll_dir / "some-module"
        mod_dir.mkdir()
        (mod_dir / "SKILL.md").write_text(
            "---\nname: test-mod\ndescription: test module\n---\n"
        )

    def test_create_agents_md(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- ansible-know:skills:start -->" in agents_md
        assert "<!-- ansible-know:skills:end -->" in agents_md
        assert "netbox.netbox" in agents_md

    def test_append_to_existing(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        (tmp_path / "AGENTS.md").write_text("# My Project\n\nExisting content.\n")
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert agents_md.startswith("# My Project")
        assert "Existing content." in agents_md
        assert "<!-- ansible-know:skills:start -->" in agents_md
        assert "netbox.netbox" in agents_md

    def test_replace_between_sentinels(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "ansible-controller")
        existing = (
            "# My Project\n\n"
            "<!-- ansible-know:skills:start -->\n"
            "## Old content\n"
            "<!-- ansible-know:skills:end -->\n\n"
            "## Other section\n"
        )
        (tmp_path / "AGENTS.md").write_text(existing)
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "# My Project" in agents_md
        assert "Old content" not in agents_md
        assert "ansible.controller" in agents_md
        assert "## Other section" in agents_md

    def test_preserves_other_sentinels(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        existing = (
            "<!-- BEGIN ANSIBLE-DEVCONTAINER -->\n"
            "## Devcontainer stuff\n"
            "<!-- END ANSIBLE-DEVCONTAINER -->\n"
        )
        (tmp_path / "AGENTS.md").write_text(existing)
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "BEGIN ANSIBLE-DEVCONTAINER" in agents_md
        assert "Devcontainer stuff" in agents_md
        assert "ansible-know:skills:start" in agents_md

    def test_missing_end_sentinel_appends(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        existing = "# Project\n\n<!-- ansible-know:skills:start -->\nBroken\n"
        (tmp_path / "AGENTS.md").write_text(existing)
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert agents_md.count("ansible-know:skills:start") == 2
        assert "ansible-know:skills:end" in agents_md

    def test_sensitive_path_rejected(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        from pathlib import Path

        import pytest

        from ansible_know.validation import ValidationError
        with pytest.raises(ValidationError):
            update_agents_md(Path("/etc"), skills_dir)

    def test_empty_skills_dir(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "Available collections:" in agents_md

    def test_skips_symlinks(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "real-collection")
        (skills_dir / "symlink-collection").symlink_to(skills_dir / "real-collection")
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "real.collection" in agents_md
        # Should appear once in collections list, not duplicated by symlink
        assert "Available collections: real.collection" in agents_md
        assert "symlink.collection" not in agents_md
