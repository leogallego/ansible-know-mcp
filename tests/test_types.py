"""Tests for ansible_know.types."""

from ansible_know.types import ManifestPluginEntry, PluginMetadata


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
