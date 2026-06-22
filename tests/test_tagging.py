"""Tests for ansible_know.tagging."""

from ansible_know.tagging import derive_tags


class TestDeriveTagsFromTagging:
    def test_import_and_basic_tag(self):
        tags = derive_tags("netbox.netbox.ip_address", [])
        assert "ipam" in tags

    def test_no_matching_tags(self):
        tags = derive_tags("custom.collection.something_unique", [])
        assert tags == []
