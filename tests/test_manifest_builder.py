"""Tests for ansible_know.manifest_builder."""

from __future__ import annotations

from ansible_know.manifest_builder import filter_guide_pages


class TestFilterGuidePages:
    def test_keeps_guide_pages(self):
        entries = [
            {"name": "playbook_guide/playbooks_intro", "display_name": "Intro"},
            {"name": "inventory_guide/intro_inventory", "display_name": "Inventory"},
        ]
        prefixes = {"playbook_guide", "inventory_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 2

    def test_excludes_collections(self):
        entries = [
            {"name": "playbook_guide/intro", "display_name": "Intro"},
            {"name": "collections/ansible/builtin/copy_module", "display_name": "copy"},
        ]
        prefixes = {"playbook_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 1
        assert result[0]["name"] == "playbook_guide/intro"

    def test_excludes_top_level(self):
        entries = [
            {"name": "index", "display_name": "Home"},
            {"name": "playbook_guide/intro", "display_name": "Intro"},
        ]
        prefixes = {"playbook_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 1

    def test_empty_entries(self):
        assert filter_guide_pages([], {"playbook_guide"}) == []
