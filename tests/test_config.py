"""Tests for ansible_know.config."""

import json

from ansible_know.config import DEFAULT_DOC_SOURCES, get_doc_sources


class TestGetDocSources:
    def test_returns_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("ANSIBLE_KNOW_DOC_SOURCES", raising=False)
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_returns_custom_sources(self, monkeypatch):
        custom = {"my-source": {"url": "https://example.com/manifest.json", "description": "test"}}
        monkeypatch.setenv("ANSIBLE_KNOW_DOC_SOURCES", json.dumps(custom))
        result = get_doc_sources()
        assert result == custom

    def test_falls_back_on_invalid_json(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_DOC_SOURCES", "{not valid json")
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_falls_back_on_non_dict_json(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_DOC_SOURCES", '["a list"]')
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_falls_back_on_null_json(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_DOC_SOURCES", "null")
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_empty_string_returns_defaults(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_DOC_SOURCES", "")
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_accepts_empty_dict(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_DOC_SOURCES", "{}")
        result = get_doc_sources()
        assert result == {}


class TestDocCurationConfig:
    def test_audience_map_has_entries(self):
        from ansible_know.config import AUDIENCE_MAP
        assert isinstance(AUDIENCE_MAP, dict)
        assert len(AUDIENCE_MAP) == 9
        assert AUDIENCE_MAP["dev_guide"] == "developer"
        assert AUDIENCE_MAP["playbook_guide"] == "author"

    def test_core_pages_has_ansible_key(self):
        from ansible_know.config import CORE_PAGES
        assert "ansible" in CORE_PAGES
        assert len(CORE_PAGES["ansible"]) >= 30
        assert "playbook_guide/playbooks_intro.html" in CORE_PAGES["ansible"]

    def test_core_pages_has_ecosystem_keys(self):
        from ansible_know.config import CORE_PAGES
        for key in ("lint", "navigator", "builder", "creator", "molecule"):
            assert key in CORE_PAGES, f"Missing CORE_PAGES key: {key}"
            assert len(CORE_PAGES[key]) >= 2

    def test_guide_topic_prefixes(self):
        from ansible_know.config import GUIDE_TOPIC_PREFIXES
        assert isinstance(GUIDE_TOPIC_PREFIXES, set)
        assert "playbook_guide" in GUIDE_TOPIC_PREFIXES
        assert "plugins" in GUIDE_TOPIC_PREFIXES
        assert "collections" not in GUIDE_TOPIC_PREFIXES

    def test_project_base_urls(self):
        from ansible_know.config import PROJECT_BASE_URLS
        assert PROJECT_BASE_URLS["ansible"] == "https://docs.ansible.com/projects/ansible/latest"
        assert "lint" in PROJECT_BASE_URLS

    def test_rtd_project_slugs_use_source_names(self):
        from ansible_know.config import RTD_PROJECT_SLUGS
        assert RTD_PROJECT_SLUGS["ansible-core"] == "package-doc-builds"
        assert "ansible-lint" in RTD_PROJECT_SLUGS

    def test_default_doc_sources_use_file_keys(self):
        from ansible_know.config import DEFAULT_DOC_SOURCES
        for name, cfg in DEFAULT_DOC_SOURCES.items():
            assert "file" in cfg, f"Source '{name}' missing 'file' key"
            assert "description" in cfg


class TestPluginTypeConstants:
    def test_plugin_types_contains_lookup(self):
        from ansible_know.config import PLUGIN_TYPES

        assert "lookup" in PLUGIN_TYPES

    def test_plugin_types_contains_filter(self):
        from ansible_know.config import PLUGIN_TYPES

        assert "filter" in PLUGIN_TYPES

    def test_plugin_types_excludes_module_and_role(self):
        from ansible_know.config import PLUGIN_TYPES

        assert "module" not in PLUGIN_TYPES
        assert "role" not in PLUGIN_TYPES

    def test_plugin_types_has_14_entries(self):
        from ansible_know.config import PLUGIN_TYPES

        assert len(PLUGIN_TYPES) == 14

    def test_jinja2_types(self):
        from ansible_know.config import JINJA2_PLUGIN_TYPES

        assert set(JINJA2_PLUGIN_TYPES) == {"lookup", "filter", "test"}

    def test_playbook_types(self):
        from ansible_know.config import PLAYBOOK_PLUGIN_TYPES

        assert set(PLAYBOOK_PLUGIN_TYPES) == {"connection", "become", "strategy", "callback", "inventory"}

    def test_infra_types(self):
        from ansible_know.config import INFRA_PLUGIN_TYPES

        assert set(INFRA_PLUGIN_TYPES) == {"cache", "cliconf", "httpapi", "netconf", "shell", "vars"}

    def test_categories_cover_all_types(self):
        from ansible_know.config import INFRA_PLUGIN_TYPES, JINJA2_PLUGIN_TYPES, PLAYBOOK_PLUGIN_TYPES, PLUGIN_TYPES

        combined = set(JINJA2_PLUGIN_TYPES) | set(PLAYBOOK_PLUGIN_TYPES) | set(INFRA_PLUGIN_TYPES)
        assert combined == set(PLUGIN_TYPES)


class TestAapDocSources:
    def test_aap_sources_registered(self):
        assert "aap-2.5" in DEFAULT_DOC_SOURCES
        assert "aap-2.6" in DEFAULT_DOC_SOURCES
        assert "aap-2.7" in DEFAULT_DOC_SOURCES

    def test_aap_sources_have_file_key(self):
        for ver in ("aap-2.5", "aap-2.6", "aap-2.7"):
            assert "file" in DEFAULT_DOC_SOURCES[ver]
            assert "description" in DEFAULT_DOC_SOURCES[ver]

    def test_aap_file_paths_end_with_json(self):
        for ver in ("aap-2.5", "aap-2.6", "aap-2.7"):
            assert DEFAULT_DOC_SOURCES[ver]["file"].endswith(".json")


class TestGetProjectRoot:
    def test_ansible_know_project_dir_wins(self, monkeypatch, tmp_path):
        from ansible_know.config import get_project_root

        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/should/not/win")
        assert get_project_root() == tmp_path

    def test_claude_project_dir_fallback(self, monkeypatch, tmp_path):
        from ansible_know.config import get_project_root

        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        assert get_project_root() == tmp_path

    def test_cwd_fallback(self, monkeypatch):
        from pathlib import Path

        from ansible_know.config import get_project_root

        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert get_project_root() == Path.cwd()

    def test_empty_env_var_skipped(self, monkeypatch, tmp_path):
        from ansible_know.config import get_project_root

        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", "")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        assert get_project_root() == tmp_path


class TestSkillsDirResolution:
    def _reload_skills_dir(self, monkeypatch):
        """Force SKILLS_DIR to re-resolve by clearing the cached value."""
        import ansible_know.config as config_mod

        monkeypatch.delattr(config_mod, "SKILLS_DIR", raising=False)
        if "SKILLS_DIR" in config_mod.__dict__:
            del config_mod.__dict__["SKILLS_DIR"]
        return config_mod.SKILLS_DIR

    def test_explicit_skills_dir_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANSIBLE_KNOW_SKILLS_DIR", str(tmp_path / "explicit"))
        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "claude"))
        result = self._reload_skills_dir(monkeypatch)
        assert result == tmp_path / "explicit"

    def test_project_dir_adds_skills_suffix(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_DIR", raising=False)
        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        result = self._reload_skills_dir(monkeypatch)
        assert result == tmp_path / "skills"

    def test_claude_project_dir_adds_skills_suffix(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_DIR", raising=False)
        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        result = self._reload_skills_dir(monkeypatch)
        assert result == tmp_path / "skills"

    def test_cwd_fallback(self, monkeypatch):
        from pathlib import Path

        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_DIR", raising=False)
        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        result = self._reload_skills_dir(monkeypatch)
        assert result == Path.cwd() / "skills"
