"""Tests for ansible_know.config."""

import json

from ansible_know.config import DEFAULT_DOC_SOURCES, get_doc_sources


class TestGetDocSources:
    def test_returns_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", raising=False)
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_returns_custom_sources(self, monkeypatch):
        custom = {"my-source": {"url": "https://example.com/manifest.json", "description": "test"}}
        monkeypatch.setenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", json.dumps(custom))
        result = get_doc_sources()
        assert result == custom

    def test_falls_back_on_invalid_json(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", "{not valid json")
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_falls_back_on_non_dict_json(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", '["a list"]')
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_falls_back_on_null_json(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", "null")
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_empty_string_returns_defaults(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", "")
        result = get_doc_sources()
        assert result == DEFAULT_DOC_SOURCES

    def test_accepts_empty_dict(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOWLEDGE_DOC_SOURCES", "{}")
        result = get_doc_sources()
        assert result == {}
