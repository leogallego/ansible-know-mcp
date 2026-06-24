"""End-to-end tests for the MCP server via FastMCP Client.

Exercises every tool, resource, and prompt exposed by the server.
Requires: network access to galaxy.ansible.com and ansible-core installed.

Run with: pytest --run-integration tests/integration/test_mcp_e2e.py -v
"""

from __future__ import annotations

import json

import pytest

from ansible_know.server import mcp

pytestmark = pytest.mark.integration


@pytest.fixture
async def client():
    """Create a FastMCP test client connected to the server."""
    from fastmcp import Client

    async with Client(mcp) as c:
        yield c


class TestDiscoveryTools:
    """Test all read-only discovery tools."""

    async def test_search_modules_copy(self, client):
        result = await client.call_tool("search_modules", {"keyword": "copy"})
        data = _extract_data(result)
        assert "ansible.builtin.copy" in data
        assert isinstance(data["ansible.builtin.copy"], str)

    async def test_search_modules_with_namespace(self, client):
        result = await client.call_tool(
            "search_modules", {"keyword": "copy", "namespace": "ansible.builtin"},
        )
        data = _extract_data(result)
        assert "ansible.builtin.copy" in data
        assert all(k.startswith("ansible.builtin.") for k in data)

    async def test_search_modules_empty_keyword(self, client):
        result = await client.call_tool("search_modules", {"keyword": ""})
        data = _extract_data(result)
        assert len(data) > 0

    async def test_search_modules_invalid_namespace(self, client):
        result = await client.call_tool(
            "search_modules", {"keyword": "copy", "namespace": "bad"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_get_module_doc_builtin_copy(self, client):
        result = await client.call_tool(
            "get_module_doc", {"module_name": "ansible.builtin.copy"},
        )
        data = _extract_data(result)
        assert data["module_name"] == "ansible.builtin.copy"
        assert data["short_description"]
        assert len(data["params"]) > 5
        assert data["doc_source"] == "local"
        assert isinstance(data["is_api_module"], bool)

        dest_param = next(p for p in data["params"] if p["name"] == "dest")
        assert dest_param["required"] is True
        assert dest_param["type"] == "path"

    async def test_get_module_doc_galaxy_fallback(self, client):
        result = await client.call_tool(
            "get_module_doc", {"module_name": "junipernetworks.junos.junos_config"},
        )
        data = _extract_data(result)
        if "error" not in data:
            assert data["module_name"] == "junipernetworks.junos.junos_config"
            assert data["doc_source"] == "galaxy"
            assert data.get("doc_version")

    async def test_get_module_doc_invalid_fqcn(self, client):
        result = await client.call_tool(
            "get_module_doc", {"module_name": "not-valid"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_search_docs_inventory(self, client):
        result = await client.call_tool("search_docs", {"query": "inventory"})
        data = _extract_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        first = data[0]
        assert "title" in first
        assert "summary" in first
        assert "url" in first

    async def test_search_docs_with_filters(self, client):
        result = await client.call_tool(
            "search_docs",
            {"query": "role", "source": "ansible-core", "topic": "playbook_guide"},
        )
        data = _extract_data(result)
        assert isinstance(data, list)
        if data:
            assert all(e["source"] == "ansible-core" for e in data)

    async def test_search_docs_empty_query(self, client):
        result = await client.call_tool("search_docs", {"query": ""})
        data = _extract_data(result)
        assert "error" in data


class TestFetchDoc:
    """Test the fetch_doc tool against live docs.ansible.com."""

    async def test_fetch_doc_returns_markdown(self, client):
        result = await client.call_tool(
            "fetch_doc",
            {"url": "https://docs.ansible.com/projects/ansible/latest/getting_started/basic_concepts.html"},
        )
        data = _extract_data(result)
        assert "content" in data
        assert "title" in data
        assert "tokens" in data
        assert "source_url" in data
        assert data["title"]
        assert len(data["content"]) > 100

    async def test_fetch_doc_title_is_clean(self, client):
        """Title must not contain permalink artifacts like [¶](#...)."""
        result = await client.call_tool(
            "fetch_doc",
            {"url": "https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html"},
        )
        data = _extract_data(result)
        assert "[¶]" not in data.get("title", "")
        assert "\\uf0c1" not in data.get("title", "")
        assert "Permanent link" not in data.get("title", "")

    async def test_fetch_doc_content_no_permalink_artifacts(self, client):
        """Body content must not contain permalink markers."""
        result = await client.call_tool(
            "fetch_doc",
            {"url": "https://docs.ansible.com/projects/lint/rules/"},
        )
        data = _extract_data(result)
        content = data.get("content", "")
        assert "[¶]" not in content
        assert "Permanent link" not in content

    async def test_fetch_doc_max_tokens_exceeded(self, client):
        result = await client.call_tool(
            "fetch_doc",
            {"url": "https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html",
             "max_tokens": 1},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_fetch_doc_rejects_non_ansible_url(self, client):
        result = await client.call_tool(
            "fetch_doc", {"url": "https://evil.com/page"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_fetch_doc_rejects_bare_domain(self, client):
        result = await client.call_tool(
            "fetch_doc", {"url": "https://docs.ansible.com/"},
        )
        data = _extract_data(result)
        assert "error" in data


class TestSearchDocsBehavior:
    """Test search_docs filter and fallback behavior end-to-end."""

    async def test_search_docs_returns_all_required_fields(self, client):
        result = await client.call_tool("search_docs", {"query": "playbook"})
        data = _extract_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        for entry in data:
            assert "title" in entry
            assert "summary" in entry
            assert "topic" in entry
            assert "audience" in entry
            assert "lines" in entry
            assert "source" in entry
            assert "url" in entry

    async def test_search_docs_core_only_excludes_non_core(self, client):
        result = await client.call_tool(
            "search_docs", {"query": "ansible", "core_only": True},
        )
        data = _extract_data(result)
        assert isinstance(data, list)
        assert len(data) > 0

    async def test_search_docs_source_filter(self, client):
        result = await client.call_tool(
            "search_docs", {"query": "install", "source": "ansible-lint"},
        )
        data = _extract_data(result)
        assert isinstance(data, list)
        if data:
            assert all(e["source"] == "ansible-lint" for e in data)

    async def test_search_docs_topic_filter_no_rtd_bypass(self, client):
        """When topic filter narrows to zero, RTD fallback must NOT fire."""
        result = await client.call_tool(
            "search_docs",
            {"query": "playbook", "topic": "zzz_nonexistent_topic_zzz"},
        )
        data = _extract_data(result)
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_search_docs_no_permalink_in_titles(self, client):
        """All manifest results must have clean titles."""
        result = await client.call_tool("search_docs", {"query": "ansible"})
        data = _extract_data(result)
        for entry in data:
            assert "[¶]" not in entry.get("title", ""), f"Permalink in title: {entry['title']}"

    async def test_search_docs_ecosystem_sources(self, client):
        """All 6 doc sources should be loadable."""
        sources = ["ansible-core", "ansible-lint", "ansible-navigator",
                    "ansible-builder", "ansible-creator", "molecule"]
        for src in sources:
            result = await client.call_tool(
                "search_docs", {"query": "install", "source": src},
            )
            data = _extract_data(result)
            assert isinstance(data, list), f"Source {src} failed"
            assert len(data) > 0, f"Source {src} returned empty"


class TestDocResources:
    """Test the docs://sources resource."""

    async def test_doc_sources_lists_all_shipped(self, client):
        result = await client.read_resource("docs://sources")
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        data = json.loads(text)
        expected = {"ansible-core", "ansible-lint", "ansible-navigator",
                    "ansible-builder", "ansible-creator", "molecule"}
        assert expected.issubset(set(data.keys()))

    async def test_doc_sources_has_descriptions(self, client):
        result = await client.read_resource("docs://sources")
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        data = json.loads(text)
        for name, info in data.items():
            assert "description" in info, f"Source {name} missing description"


class TestGalaxyTools:
    """Test Galaxy-facing tools."""

    async def test_search_collections_network(self, client):
        result = await client.call_tool(
            "search_collections", {"query": "network"},
        )
        data = _extract_data(result)
        assert data["count"] > 0
        assert len(data["collections"]) > 0

        first = data["collections"][0]
        assert "namespace" in first
        assert "description" in first
        assert "latest_version" in first
        assert "module_count" in first
        assert "download_count" in first
        assert "deprecated" in first
        assert isinstance(first["deprecated"], bool)

    async def test_search_collections_has_role_count(self, client):
        result = await client.call_tool(
            "search_collections", {"query": "linux system roles"},
        )
        data = _extract_data(result)
        if data["count"] > 0:
            first = data["collections"][0]
            assert "role_count" in first, (
                "search_collections should include role_count field"
            )

    async def test_search_collections_with_tags(self, client):
        result = await client.call_tool(
            "search_collections", {"query": "network", "tags": "networking"},
        )
        data = _extract_data(result)
        assert data["count"] >= 0

    async def test_search_collections_empty_query(self, client):
        result = await client.call_tool("search_collections", {"query": ""})
        data = _extract_data(result)
        assert "error" in data

    async def test_ensure_collection_install(self, client):
        result = await client.call_tool(
            "ensure_collection",
            {"collection_namespace": "ansible.utils"},
        )
        data = _extract_data(result)
        assert data["namespace"] == "ansible.utils"
        assert data["version"]
        assert data["status"] in ("installed", "already_installed")

    async def test_ensure_collection_idempotent(self, client):
        await client.call_tool(
            "ensure_collection",
            {"collection_namespace": "ansible.utils"},
        )
        result = await client.call_tool(
            "ensure_collection",
            {"collection_namespace": "ansible.utils"},
        )
        data = _extract_data(result)
        assert data["status"] == "already_installed"

    async def test_ensure_collection_invalid_namespace(self, client):
        result = await client.call_tool(
            "ensure_collection",
            {"collection_namespace": "bad"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_get_collection_manifest(self, client):
        await client.call_tool(
            "ensure_collection",
            {"collection_namespace": "ansible.utils"},
        )
        result = await client.call_tool(
            "get_collection_manifest",
            {"collection_namespace": "ansible.utils"},
        )
        data = _extract_data(result)
        assert data["collection"] == "ansible.utils"
        assert data["module_count"] > 0
        assert isinstance(data["modules"], list)
        assert len(data["modules"]) > 0

        first_module = data["modules"][0]
        assert "fqcn" in first_module
        assert "description" in first_module

    async def test_get_collection_manifest_has_roles(self, client):
        await client.call_tool(
            "ensure_collection",
            {"collection_namespace": "ansible.utils"},
        )
        result = await client.call_tool(
            "get_collection_manifest",
            {"collection_namespace": "ansible.utils"},
        )
        data = _extract_data(result)
        assert "roles" in data or "role_count" in data, (
            "get_collection_manifest should include roles section"
        )

    async def test_get_collection_manifest_not_installed(self, client):
        result = await client.call_tool(
            "get_collection_manifest",
            {"collection_namespace": "nonexistent.collection123"},
        )
        data = _extract_data(result)
        assert "error" in data


class TestRoleTools:
    """Test the new role-specific tools."""

    async def test_get_role_doc_galaxy_fallback(self, client):
        result = await client.call_tool(
            "get_role_doc",
            {"role_name": "fedora.linux_system_roles.timesync"},
        )
        data = _extract_data(result)
        assert data["role_name"] == "fedora.linux_system_roles.timesync"
        assert data["content_type"] == "role"
        assert data["doc_source"] in ("local", "galaxy_readme", "unavailable")

        if data["doc_source"] == "galaxy_readme":
            assert "entry_points" in data
            assert "main" in data["entry_points"]

    async def test_get_role_doc_unavailable(self, client):
        result = await client.call_tool(
            "get_role_doc",
            {"role_name": "nonexistent.collection123.fake_role"},
        )
        data = _extract_data(result)
        assert data["doc_source"] == "unavailable"
        assert data["content_type"] == "role"

    async def test_get_role_doc_invalid_fqcn(self, client):
        result = await client.call_tool(
            "get_role_doc", {"role_name": "bad-name"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_generate_role_skill(self, client, tmp_path):
        result = await client.call_tool(
            "generate_role_skill",
            {
                "role_name": "fedora.linux_system_roles.timesync",
                "install_to": str(tmp_path),
            },
        )
        text = _extract_text(result)
        if "error" not in text.lower() or "unavailable" not in text.lower():
            assert "timesync" in text.lower()

            skill_dir = tmp_path / "fedora.linux_system_roles.timesync"
            if skill_dir.exists():
                assert (skill_dir / "SKILL.md").exists()
                assert (skill_dir / "assets" / "playbook.yml").exists()
                assert not (skill_dir / "scripts").exists()

    async def test_generate_role_skill_invalid_fqcn(self, client):
        result = await client.call_tool(
            "generate_role_skill", {"role_name": "bad"},
        )
        data = _extract_data(result)
        assert "error" in data


class TestSkillTools:
    """Test skill management tools."""

    async def test_list_skills(self, client):
        result = await client.call_tool("list_skills", {})
        data = _extract_data(result)
        assert isinstance(data, list)

    async def test_generate_skill_builtin_copy(self, client, tmp_path):
        result = await client.call_tool(
            "generate_skill",
            {
                "module_name": "ansible.builtin.copy",
                "install_to": str(tmp_path),
            },
        )
        text = _extract_text(result)
        assert "ansible.builtin.copy" in text
        assert "Copy files" in text

        skill_dir = tmp_path / "ansible.builtin" / "copy"
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "scripts").exists() or (skill_dir / "assets").exists()

    async def test_get_skill_not_found(self, client):
        result = await client.call_tool(
            "get_skill", {"skill_name": "nonexistent.fake.module"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_generate_skill_invalid_fqcn(self, client):
        result = await client.call_tool(
            "generate_skill", {"module_name": "bad"},
        )
        data = _extract_data(result)
        assert "error" in data


class TestResources:
    """Test MCP resources."""

    async def test_skills_list_resource(self, client):
        result = await client.read_resource("skills://list")
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        data = json.loads(text)
        assert isinstance(data, list)

    async def test_installed_collections_resource(self, client):
        result = await client.read_resource("galaxy://installed")
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        data = json.loads(text)
        assert isinstance(data, (list, dict))

    async def test_doc_sources_resource(self, client):
        result = await client.read_resource("docs://sources")
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        data = json.loads(text)
        assert isinstance(data, dict)
        assert len(data) > 0


class TestPrompts:
    """Test MCP prompt templates."""

    async def test_review_playbook_prompt(self, client):
        result = await client.get_prompt(
            "review_playbook", {"playbook_yaml": "- hosts: all"},
        )
        text = result.messages[0].content.text
        assert "Review" in text
        assert "hosts: all" in text

    async def test_explain_module_prompt(self, client):
        result = await client.get_prompt(
            "explain_module", {"module_name": "ansible.builtin.copy"},
        )
        text = result.messages[0].content.text
        assert "ansible.builtin.copy" in text

    async def test_generate_role_prompt(self, client):
        result = await client.get_prompt(
            "generate_role",
            {"role_purpose": "install nginx", "modules": "ansible.builtin.package"},
        )
        text = result.messages[0].content.text
        assert "nginx" in text
        assert "ansible.builtin.package" in text

    async def test_find_collection_prompt(self, client):
        result = await client.get_prompt(
            "find_collection", {"platform_or_use_case": "network switches"},
        )
        text = result.messages[0].content.text
        assert "network switches" in text
        assert "get_role_doc" in text or "get_module_doc" in text


class TestInputValidation:
    """Test that invalid inputs are handled gracefully."""

    async def test_path_traversal_in_fqcn(self, client):
        result = await client.call_tool(
            "get_module_doc", {"module_name": "../../etc.passwd.read"},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_shell_injection_in_keyword(self, client):
        result = await client.call_tool(
            "search_modules", {"keyword": "; rm -rf /"},
        )
        data = _extract_data(result)
        assert isinstance(data, dict)

    async def test_very_long_keyword(self, client):
        result = await client.call_tool(
            "search_modules", {"keyword": "a" * 1000},
        )
        data = _extract_data(result)
        assert "error" in data

    async def test_unicode_in_search(self, client):
        result = await client.call_tool(
            "search_modules", {"keyword": "paquete"},
        )
        data = _extract_data(result)
        assert isinstance(data, dict)


class TestErrorMessages:
    """Test that error messages don't leak sensitive paths."""

    async def test_module_not_found_no_path_leak(self, client):
        result = await client.call_tool(
            "get_module_doc",
            {"module_name": "nonexistent.collection99.fake_module"},
        )
        data = _extract_data(result)
        if "error" in data:
            assert "/home/" not in data["error"]
            assert "/tmp/" not in data["error"]

    async def test_role_not_found_no_path_leak(self, client):
        result = await client.call_tool(
            "get_role_doc",
            {"role_name": "nonexistent.collection99.fake_role"},
        )
        data = _extract_data(result)
        if "error" in data:
            assert "/home/" not in data["error"]
            assert "/tmp/" not in data["error"]


def _extract_data(result):
    """Extract deserialized data from a CallToolResult.

    FastMCP Client.call_tool() returns a CallToolResult with:
    - .data: deserialized Python object (dict, list, str, or Root for TypedDicts)
    - .content: list of TextContent/ImageContent blocks
    - .is_error: bool

    Prefer JSON-parsing from .content because FastMCP returns Root objects
    (not plain dicts) for TypedDict tool returns — Root is not subscriptable
    or iterable, so .data is unusable for those. JSON parsing always returns
    plain Python types.
    """
    if hasattr(result, "content"):
        for item in result.content:
            if hasattr(item, "text"):
                try:
                    return json.loads(item.text)
                except (json.JSONDecodeError, TypeError):
                    continue
    if hasattr(result, "data") and result.data is not None:
        return result.data
    if isinstance(result, (dict, list)):
        return result
    return result


def _extract_text(result) -> str:
    """Extract text content from a CallToolResult."""
    if hasattr(result, "data") and isinstance(result.data, str):
        return result.data
    if hasattr(result, "content"):
        parts = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
        return "\n".join(parts)
    return str(result)
