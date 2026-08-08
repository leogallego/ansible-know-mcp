"""Tests for ansible_know.types."""

from ansible_know.parser import transform_galaxy_to_ansible_doc_format
from ansible_know.types import AnsibleDocEntry, AnsibleDocPayload, ManifestPluginEntry, PluginMetadata


class TestAnsibleDocEntry:
    def test_entry_optional_keys(self):
        entry: AnsibleDocEntry = {
            "doc": {"short_description": "Test module"},
            "examples": "- name: example\n  module: test",
            "return": [],
            "metadata": {"status": ["preview"]},
        }
        assert entry["doc"]["short_description"] == "Test module"

    def test_transform_produces_ansible_doc_payload(self):
        galaxy_entry = {
            "doc_strings": {
                "doc": {
                    "short_description": "Galaxy module",
                    "options": [{"name": "foo", "type": "str"}],
                },
                "examples": "example yaml",
                "return": [{"name": "bar"}],
                "metadata": {"version_added": "1.0.0"},
            }
        }
        payload: AnsibleDocPayload = transform_galaxy_to_ansible_doc_format(
            "ns.col.mod", galaxy_entry,
        )
        assert "ns.col.mod" in payload
        mod = payload["ns.col.mod"]
        assert mod["doc"]["short_description"] == "Galaxy module"
        assert mod["examples"] == "example yaml"
        assert mod["return"] == [{"name": "bar"}]
        assert mod["metadata"] == {"version_added": "1.0.0"}


class TestPluginTypes:
    def test_plugin_metadata_instantiation(self):
        meta: PluginMetadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox via its API",
            "params": [],
            "examples": "",
        }
        assert meta["plugin_type"] == "lookup"

    def test_manifest_plugin_entry(self):
        entry: ManifestPluginEntry = {
            "fqcn": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "description": "Queries NetBox via its API",
            "param_count": 3,
            "has_skill": False,
        }
        assert entry["plugin_type"] == "lookup"
