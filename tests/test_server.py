"""Tests for ansible_know.server MCP tools."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.conftest import (
    SAMPLE_MODULE_DOC,
    SAMPLE_MODULE_LIST,
    SAMPLE_ROLE_DOC,
    SAMPLE_ROLE_LIST,
)


def _make_mock_ctx(state, shared, http_client=None):
    """Build a mock FastMCP Context with session-based state."""
    mock_ctx = MagicMock()
    sessions = MagicMock()
    sessions.get_or_create = AsyncMock(return_value=state)
    mock_ctx.lifespan_context = {
        "shared": shared,
        "sessions": sessions,
        "http_client": http_client,
    }
    mock_ctx.session_id = "test-session"
    mock_ctx.warning = AsyncMock()
    return mock_ctx


@pytest.fixture
def mock_ansible_doc():
    with patch("ansible_know.parser._run_ansible_doc") as mock:
        yield mock


@pytest.fixture
def mock_doc_fetch():
    """Mock httpx for docs search tests."""
    mock_manifest = [
        {
            "title": "Playbook Guide",
            "summary": "How to write playbooks",
            "topics": ["playbooks"],
            "audience": ["beginner"],
            "core": True,
            "lines": 100,
            "url": "https://example.com/playbook.md",
        }
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_manifest
    mock_resp.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("ansible_know.docs.httpx.AsyncClient", return_value=mock_client):
        from ansible_know.docs import clear_cache
        clear_cache()
        yield
        clear_cache()


class TestSearchModulesTool:
    @pytest.mark.asyncio
    async def test_search_modules(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_LIST)
        from ansible_know.server import search_modules
        result = await search_modules("redis")
        assert "community.general.redis" in result
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search_with_namespace(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_LIST)
        from ansible_know.server import search_modules
        result = await search_modules("package", namespace="ansible.builtin")
        assert "ansible.builtin.package" in result


class TestGetModuleDocTool:
    @pytest.mark.asyncio
    async def test_get_module_doc(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.package")
        assert result["module_name"] == "ansible.builtin.package"
        assert result["short_description"] == "Generic OS package manager"
        assert len(result["params"]) == 3
        assert result["is_api_module"] is False


class TestSearchDocsTool:
    @pytest.mark.asyncio
    async def test_search_docs(self, mock_doc_fetch):
        from ansible_know.server import search_docs
        results = await search_docs("playbook")
        assert len(results) == 1
        assert results[0]["title"] == "Playbook Guide"


class TestGetCollectionManifestTool:
    @pytest.mark.asyncio
    async def test_returns_error_for_empty_collection(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps({})
        from ansible_know.server import get_collection_manifest
        result = await get_collection_manifest("nonexistent.collection")
        assert "error" in result


class TestListSkillsTool:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import list_skills
        result = await list_skills()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_top_level_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        skill_dir = tmp_path / "netbox.netbox"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: netbox.netbox\ndescription: Collection guide\n---\n")
        from ansible_know.server import list_skills
        result = await list_skills()
        assert len(result) == 1
        assert result[0]["name"] == "netbox.netbox"
        assert result[0]["description"] == "Collection guide"

    @pytest.mark.asyncio
    async def test_returns_collection_skills_with_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        mod_dir = tmp_path / "netbox.netbox" / "netbox_device"
        mod_dir.mkdir(parents=True)
        (mod_dir / "SKILL.md").write_text("---\nname: netbox.netbox.netbox_device\ndescription: Device\n---\n")
        from ansible_know.server import list_skills
        result = await list_skills(collection="netbox.netbox")
        assert len(result) == 1
        assert result[0]["name"] == "netbox.netbox.netbox_device"
        assert result[0]["description"] == "Device"

    @pytest.mark.asyncio
    async def test_returns_empty_for_nonexistent_collection(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import list_skills
        result = await list_skills(collection="nonexistent.collection")
        assert result == []

    @pytest.mark.asyncio
    async def test_rejects_invalid_collection_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import list_skills
        result = await list_skills(collection="bad!")
        assert "error" in result


class TestGetSkillTool:
    @pytest.mark.asyncio
    async def test_returns_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import get_skill
        result = await get_skill("ansible.builtin.nonexistent")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reads_nested_skill(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        nested = tmp_path / "netbox.netbox" / "netbox_device"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("nested skill content")
        from ansible_know.server import get_skill
        result = await get_skill("netbox.netbox.netbox_device")
        assert result == "nested skill content"

    @pytest.mark.asyncio
    async def test_reads_collection_skill_by_namespace(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        coll_dir = tmp_path / "netbox.netbox"
        coll_dir.mkdir(parents=True)
        (coll_dir / "SKILL.md").write_text("collection skill content")
        from ansible_know.server import get_skill
        result = await get_skill("netbox.netbox")
        assert result == "collection skill content"

    @pytest.mark.asyncio
    async def test_falls_back_to_flat_layout(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        flat = tmp_path / "ansible.builtin.copy"
        flat.mkdir(parents=True)
        (flat / "SKILL.md").write_text("flat skill content")
        from ansible_know.server import get_skill
        result = await get_skill("ansible.builtin.copy")
        assert result == "flat skill content"


class TestGenerateSkillTool:
    @pytest.mark.asyncio
    async def test_generates_skill(self, tmp_path, mock_ansible_doc, monkeypatch):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import generate_skill
        result = await generate_skill("ansible.builtin.package")
        assert "ansible.builtin.package" in result
        assert (tmp_path / "ansible.builtin" / "package" / "SKILL.md").exists()


class TestGenerateCollectionSkillsTool:
    @pytest.mark.asyncio
    async def test_generates_collection_skills(self, tmp_path, mock_ansible_doc, monkeypatch):
        mock_ansible_doc.side_effect = [
            json.dumps(SAMPLE_MODULE_LIST),
            json.dumps(SAMPLE_MODULE_DOC),
            json.dumps(SAMPLE_MODULE_DOC),
            json.dumps(SAMPLE_MODULE_DOC),
            json.dumps(SAMPLE_MODULE_DOC),
        ]
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import generate_collection_skills
        result = await generate_collection_skills("ansible.builtin", install_to=str(tmp_path))
        assert result["total"] == 4
        assert result["succeeded"] + result["failed"] == 4
        assert result["collection_skill"] == "ansible.builtin"
        assert (tmp_path / "ansible.builtin" / "SKILL.md").exists()


class TestFQCNValidation:
    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        from ansible_know.server import get_module_doc
        result = await get_module_doc("../../etc/passwd")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_shell_metacharacters(self):
        from ansible_know.server import get_module_doc
        result = await get_module_doc("; rm -rf /")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_empty_string(self):
        from ansible_know.server import get_module_doc
        result = await get_module_doc("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_single_segment(self):
        from ansible_know.server import get_module_doc
        result = await get_module_doc("copy")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_two_segments(self):
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_accepts_valid_fqcn(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.package")
        assert "error" not in result
        assert result["module_name"] == "ansible.builtin.package"

    @pytest.mark.asyncio
    async def test_rejects_dashes_in_fqcn(self):
        from ansible_know.server import get_module_doc
        result = await get_module_doc("my-namespace.my-collection.my-module")
        assert "error" in result


class TestNamespaceValidation:
    @pytest.mark.asyncio
    async def test_rejects_invalid_namespace(self):
        from ansible_know.server import get_collection_manifest
        result = await get_collection_manifest("../etc")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_three_segments(self):
        from ansible_know.server import get_collection_manifest
        result = await get_collection_manifest("a.b.c")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_accepts_valid_namespace(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps({})
        from ansible_know.server import get_collection_manifest
        with patch("ansible_know.collection_manifest.load_cached_manifest", return_value=None):
            result = await get_collection_manifest("ansible.builtin")
        assert "error" in result  # empty collection, but validation passed


class TestKeywordValidation:
    @pytest.mark.asyncio
    async def test_rejects_long_keyword(self):
        from ansible_know.server import search_modules
        result = await search_modules("a" * 201)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_accepts_normal_keyword(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_LIST)
        from ansible_know.server import search_modules
        result = await search_modules("copy")
        assert "error" not in result


class TestQueryValidation:
    @pytest.mark.asyncio
    async def test_rejects_long_query(self):
        from ansible_know.server import search_docs
        result = await search_docs("a" * 501)
        assert "error" in result


class TestSkillNameValidation:
    @pytest.mark.asyncio
    async def test_get_skill_accepts_namespace(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        coll_dir = tmp_path / "netbox.netbox"
        coll_dir.mkdir()
        (coll_dir / "SKILL.md").write_text("collection skill")
        from ansible_know.server import get_skill
        result = await get_skill("netbox.netbox")
        assert result == "collection skill"

    @pytest.mark.asyncio
    async def test_get_skill_rejects_single_segment(self):
        from ansible_know.server import get_skill
        result = await get_skill("copy")
        assert "error" in result


class TestGetSkillSync:
    def test_returns_content_for_namespace_skill(self, tmp_path):
        from ansible_know.server import _get_skill_sync

        coll_dir = tmp_path / "netbox.netbox"
        coll_dir.mkdir()
        (coll_dir / "SKILL.md").write_text("collection skill content")
        assert _get_skill_sync(tmp_path, "netbox.netbox") == "collection skill content"

    def test_returns_content_for_nested_module_skill(self, tmp_path):
        from ansible_know.server import _get_skill_sync

        mod_dir = tmp_path / "netbox.netbox" / "netbox_device"
        mod_dir.mkdir(parents=True)
        (mod_dir / "SKILL.md").write_text("module skill content")
        assert _get_skill_sync(tmp_path, "netbox.netbox.netbox_device") == "module skill content"

    def test_falls_back_to_flat_layout(self, tmp_path):
        from ansible_know.server import _get_skill_sync

        flat_dir = tmp_path / "netbox.netbox.netbox_device"
        flat_dir.mkdir()
        (flat_dir / "SKILL.md").write_text("flat skill content")
        assert _get_skill_sync(tmp_path, "netbox.netbox.netbox_device") == "flat skill content"

    def test_raises_for_missing_skill(self, tmp_path):
        from ansible_know.server import _get_skill_sync

        with pytest.raises(FileNotFoundError, match="not found"):
            _get_skill_sync(tmp_path, "no.such.module")


class TestExtractSkillDescription:
    def test_extracts_simple_description(self, tmp_path):
        from ansible_know.server import _extract_skill_description

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\ndescription: A test skill\n---\n")
        assert _extract_skill_description(skill_md) == "A test skill"

    def test_returns_empty_when_no_description(self, tmp_path):
        from ansible_know.server import _extract_skill_description

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\n---\nSome content\n")
        assert _extract_skill_description(skill_md) == ""

    def test_returns_empty_for_empty_file(self, tmp_path):
        from ansible_know.server import _extract_skill_description

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("")
        assert _extract_skill_description(skill_md) == ""

    def test_strips_folded_scalar_marker(self, tmp_path):
        from ansible_know.server import _extract_skill_description

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\ndescription: >-\n  Multiline\n---\n")
        assert _extract_skill_description(skill_md) == ""

    def test_description_with_colon_in_value(self, tmp_path):
        from ansible_know.server import _extract_skill_description

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\ndescription: Manage devices: create and update\n---\n")
        assert _extract_skill_description(skill_md) == "Manage devices: create and update"

    def test_description_bare_colon_no_value(self, tmp_path):
        from ansible_know.server import _extract_skill_description

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\ndescription:\n---\n")
        assert _extract_skill_description(skill_md) == ""


class TestPathTraversal:
    @pytest.mark.asyncio
    async def test_get_skill_blocks_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import get_skill
        result = await get_skill("../../etc/passwd")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_generate_skill_blocks_etc(self):
        from ansible_know.server import generate_skill
        result = await generate_skill("ansible.builtin.copy", install_to="/etc/evil")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_generate_skill_blocks_usr(self):
        from ansible_know.server import generate_skill
        result = await generate_skill("ansible.builtin.copy", install_to="/usr/local/evil")
        assert "error" in result


class TestErrorSanitization:
    @pytest.mark.asyncio
    async def test_error_strips_paths(self):
        from ansible_know.validation import sanitize_error
        msg = "Failed at /home/user/.ansible/tmp/something: permission denied"
        sanitized = sanitize_error(msg)
        assert "/home/user" not in sanitized
        assert "<path>" in sanitized

    @pytest.mark.asyncio
    async def test_error_preserves_message(self):
        from ansible_know.validation import sanitize_error
        msg = "Module not found"
        assert sanitize_error(msg) == msg


class TestOutputTruncation:
    @pytest.mark.asyncio
    async def test_truncates_large_response(self):
        from ansible_know.validation import MAX_RESPONSE_SIZE, truncate_response
        large = "x" * (MAX_RESPONSE_SIZE + 100)
        result = truncate_response(large)
        assert len(result) < len(large)
        assert "Truncated" in result

    @pytest.mark.asyncio
    async def test_preserves_small_response(self):
        from ansible_know.validation import truncate_response
        small = "hello world"
        assert truncate_response(small) == small


class TestEnsureCollectionTool:
    @pytest.mark.asyncio
    async def test_installs_collection(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        mock_result = MagicMock()
        mock_result.stdout = galaxy_stdout
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=mock_result):
                from ansible_know.server import ensure_collection
                result = await ensure_collection("netbox.netbox")
        assert result["status"] == "installed"
        assert result["namespace"] == "netbox.netbox"

    @pytest.mark.asyncio
    async def test_invalid_namespace(self):
        from ansible_know.server import ensure_collection
        result = await ensure_collection("../etc")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_version(self):
        from ansible_know.server import ensure_collection
        result = await ensure_collection("netbox.netbox", version="; rm -rf /")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_valid_version_format(self):
        galaxy_stdout = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        mock_result = MagicMock()
        mock_result.stdout = galaxy_stdout
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=mock_result):
                from ansible_know.server import ensure_collection
                result = await ensure_collection("netbox.netbox", version="3.9.0")
        assert result["status"] == "installed"
        assert result["version"] == "3.9.0"


class TestMissingCollectionHints:
    @pytest.mark.asyncio
    async def test_get_module_doc_hint_suppressed_on_double_failure(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError, GalaxyError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )
        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found on Galaxy"),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("netbox.netbox.netbox_device")
        assert "error" in result
        assert "ensure_collection" not in result["error"]

    @pytest.mark.asyncio
    async def test_get_module_doc_hint_on_local_only_failure(self, mock_ansible_doc):
        from ansible_know.errors import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox was not found"
        )
        from ansible_know.server import get_module_doc
        result = await get_module_doc("netbox.netbox.netbox_device")
        assert "ensure_collection" in result["error"]

    @pytest.mark.asyncio
    async def test_search_modules_hint(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox was not found"
        )
        from ansible_know.server import search_modules
        result = await search_modules("device", namespace="netbox.netbox")
        assert "ensure_collection" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_skill_hint_suppressed_on_double_failure(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError, GalaxyError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device could not be found"
        )
        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found on Galaxy"),
        ):
            from ansible_know.server import generate_skill
            result = await generate_skill("netbox.netbox.netbox_device")
        assert "error" in result
        assert "ensure_collection" not in result["error"]

    @pytest.mark.asyncio
    async def test_no_hint_for_unrelated_errors(self, mock_ansible_doc):
        from ansible_know.errors import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("Some unrelated error")
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.copy")
        assert "ensure_collection" not in result.get("error", "")


class TestIsMissingCollectionError:
    def test_has_no_attribute(self):
        from ansible_know.errors import is_missing_collection_error
        assert is_missing_collection_error("netbox.netbox has no attribute") is True

    def test_was_not_found(self):
        from ansible_know.errors import is_missing_collection_error
        assert is_missing_collection_error("module was not found") is True

    def test_could_not_be_found(self):
        from ansible_know.errors import is_missing_collection_error
        assert is_missing_collection_error("could not be found in Galaxy") is True

    def test_unrelated_error(self):
        from ansible_know.errors import is_missing_collection_error
        assert is_missing_collection_error("ansible-doc timed out") is False

    def test_empty_string(self):
        from ansible_know.errors import is_missing_collection_error
        assert is_missing_collection_error("") is False

    def test_case_insensitive(self):
        from ansible_know.errors import is_missing_collection_error
        assert is_missing_collection_error("HAS NO ATTRIBUTE") is True


class TestGalaxyDocsFallback:
    @pytest.mark.asyncio
    async def test_get_module_doc_local_includes_doc_source(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.package")
        assert result["doc_source"] == "local"

    @pytest.mark.asyncio
    async def test_get_module_doc_falls_back_to_galaxy(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Create, update or delete devices",
                    "description": ["Manages devices."],
                    "options": {"data": {"type": "dict", "required": True}},
                    "author": [],
                    "notes": [],
                    "version_added": "0.1.0",
                },
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("netbox.netbox.netbox_device")

        assert result["module_name"] == "netbox.netbox.netbox_device"
        assert result["doc_source"] == "galaxy"
        assert result["doc_version"] == "3.23.0"
        assert result["short_description"] == "Create, update or delete devices"

    @pytest.mark.asyncio
    async def test_get_module_doc_no_fallback_for_non_missing_errors(self, mock_ansible_doc):
        from ansible_know.errors import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=AssertionError("Galaxy fallback should not fire"),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("ansible.builtin.copy")
        assert "error" in result
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_get_module_doc_returns_error_when_both_fail(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError, GalaxyError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): some.col.mod was not found"
        )

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("Module 'mod' not found in some.col docs-blob."),
        ):
            from ansible_know.server import get_module_doc
            result = await get_module_doc("some.col.mod")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_generate_skill_falls_back_to_galaxy(self, mock_ansible_doc, tmp_path, monkeypatch):
        from ansible_know.errors import CollectionNotFoundError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Create, update or delete devices",
                    "description": ["Manages devices."],
                    "options": {"data": {"type": "dict", "required": True}},
                    "author": [],
                    "notes": [],
                    "version_added": "0.1.0",
                },
                "examples": "- name: Create device\n  netbox.netbox.netbox_device:\n    data:\n      name: Test\n",
                "return": [],
                "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            from ansible_know.server import generate_skill
            result = await generate_skill("netbox.netbox.netbox_device")

        assert "netbox.netbox.netbox_device" in result
        assert (tmp_path / "netbox.netbox" / "netbox_device" / "SKILL.md").exists()


class TestResourceFunctions:
    def test_resource_skills_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import resource_skills_list
        result = json.loads(resource_skills_list())
        assert result == []

    def test_resource_skills_list_with_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        skill_dir = tmp_path / "ansible.builtin"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Codex skill")
        from ansible_know.server import resource_skills_list
        result = json.loads(resource_skills_list())
        assert "ansible.builtin" in result

    def test_resource_skill_content_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import resource_skill_content
        result = resource_skill_content("ansible.builtin.copy")
        assert "not found" in result.lower()

    def test_resource_skill_content_reads_nested(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        nested = tmp_path / "ansible.builtin" / "copy"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("nested content")
        from ansible_know.server import resource_skill_content
        result = resource_skill_content("ansible.builtin.copy")
        assert result == "nested content"

    def test_resource_skill_content_reads_collection_skill(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        coll_dir = tmp_path / "netbox.netbox"
        coll_dir.mkdir()
        (coll_dir / "SKILL.md").write_text("collection skill content")
        from ansible_know.server import resource_skill_content
        result = resource_skill_content("netbox.netbox")
        assert result == "collection skill content"

    def test_resource_skills_list_with_nested_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        coll_dir = tmp_path / "netbox.netbox"
        coll_dir.mkdir()
        (coll_dir / "SKILL.md").write_text("# Collection skill")
        mod_dir = coll_dir / "netbox_device"
        mod_dir.mkdir()
        (mod_dir / "SKILL.md").write_text("# Device skill")
        from ansible_know.server import resource_skills_list
        result = json.loads(resource_skills_list())
        assert "netbox.netbox" in result
        assert "netbox.netbox.netbox_device" in result

    def test_resource_skill_content_flat_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        flat = tmp_path / "ansible.builtin.copy"
        flat.mkdir(parents=True)
        (flat / "SKILL.md").write_text("flat content")
        from ansible_know.server import resource_skill_content
        result = resource_skill_content("ansible.builtin.copy")
        assert result == "flat content"

    def test_resource_skill_content_invalid_fqcn(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import resource_skill_content
        result = resource_skill_content("../../etc/passwd")
        assert "invalid" in result.lower() or "expected" in result.lower()

    def test_resource_installed_collections_empty(self):
        import ansible_know.server as srv
        from ansible_know.collections import CollectionManager
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState()
        sessions = SessionManager(shared, collection_factory=CollectionManager)
        old = srv._session_manager
        try:
            srv._session_manager = sessions
            result = json.loads(srv.resource_installed_collections())
        finally:
            srv._session_manager = old
        assert result == {}

    def test_resource_installed_collections_with_data(self):
        import ansible_know.server as srv

        mock_manager = MagicMock()
        mock_manager.all_installed_collections = {"netbox.netbox": "4.1.0", "ansible.utils": "5.0.0"}
        old = srv._session_manager
        try:
            srv._session_manager = mock_manager
            result = json.loads(srv.resource_installed_collections())
        finally:
            srv._session_manager = old
        assert result == {"netbox.netbox": "4.1.0", "ansible.utils": "5.0.0"}

    def test_resource_doc_sources(self):
        from ansible_know.server import resource_doc_sources
        result = json.loads(resource_doc_sources())
        assert isinstance(result, dict)
        assert len(result) >= 1


class TestPromptFunctions:
    def test_review_playbook_prompt(self):
        from ansible_know.server import review_playbook
        result = review_playbook("- hosts: all\n  tasks: []")
        assert "Review" in result
        assert "- hosts: all" in result

    def test_explain_module_prompt(self):
        from ansible_know.server import explain_module
        result = explain_module("ansible.builtin.copy")
        assert "ansible.builtin.copy" in result
        assert "get_module_doc" in result

    def test_generate_role_prompt(self):
        from ansible_know.server import generate_role
        result = generate_role("Install nginx", "ansible.builtin.package, ansible.builtin.service")
        assert "Install nginx" in result
        assert "ansible.builtin.package" in result

    def test_find_collection_prompt(self):
        from ansible_know.server import find_collection
        result = find_collection("NetBox DCIM")
        assert "NetBox DCIM" in result
        assert "search_collections" in result
        assert "ensure_collection" in result


class TestParseVersion:
    def test_simple_version(self):
        from ansible_know.server import _parse_version
        assert _parse_version("0.3.2") == (0, 3, 2)

    def test_major_version(self):
        from ansible_know.server import _parse_version
        assert _parse_version("1.0.0") == (1, 0, 0)

    def test_invalid_version(self):
        from ansible_know.server import _parse_version
        assert _parse_version("abc") == (0,)

    def test_empty_string(self):
        from ansible_know.server import _parse_version
        assert _parse_version("") == (0,)

    def test_dev_version(self):
        from ansible_know.server import _parse_version
        assert _parse_version("0.4.0.dev0") == (0, 4, 0)

    def test_prerelease_versions(self):
        from ansible_know.server import _parse_version
        assert _parse_version("0.4.0a1") == (0, 4, 0)
        assert _parse_version("0.4.0rc1") == (0, 4, 0)
        assert _parse_version("0.4.0.post1") == (0, 4, 0)

    def test_comparison(self):
        from ansible_know.server import _parse_version
        assert _parse_version("0.4.0") > _parse_version("0.3.2")
        assert _parse_version("0.3.2") == _parse_version("0.3.2")
        assert not (_parse_version("0.3.2") > _parse_version("0.4.0"))

    def test_dev_not_outdated_by_older_stable(self):
        from ansible_know.server import _parse_version
        assert not (_parse_version("0.3.2") > _parse_version("0.4.0.dev0"))


class TestIsStable:
    def test_stable_versions(self):
        from ansible_know.server import _is_stable
        assert _is_stable("0.3.2") is True
        assert _is_stable("1.0.0") is True

    def test_prerelease_versions(self):
        from ansible_know.server import _is_stable
        assert _is_stable("0.4.0.dev0") is False
        assert _is_stable("0.4.0a1") is False
        assert _is_stable("0.4.0rc1") is False
        assert _is_stable("0.4.0.post1") is False

    def test_empty(self):
        from ansible_know.server import _is_stable
        assert _is_stable("") is False


class TestCheckPypiVersion:
    @pytest.mark.asyncio
    async def test_returns_version_info_when_outdated(self):
        from ansible_know.server import _check_pypi_version

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "99.0.0"}}
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        result = await _check_pypi_version(mock_client)
        assert result is not None
        assert result["latest"] == "99.0.0"
        assert result["outdated"] is True
        assert "upgrade_command" in result

    @pytest.mark.asyncio
    async def test_returns_not_outdated_when_current(self):
        from ansible_know.server import _VERSION, _check_pypi_version

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": _VERSION}}
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        result = await _check_pypi_version(mock_client)
        assert result is not None
        assert result["outdated"] is False

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        from ansible_know.server import _check_pypi_version

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("no network")

        result = await _check_pypi_version(mock_client)
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    async def test_returns_none_when_skip_env_set(self, monkeypatch, value):
        from ansible_know.server import _check_pypi_version

        monkeypatch.setenv("ANSIBLE_KNOW_SKIP_UPDATE_CHECK", value)
        mock_client = AsyncMock()
        result = await _check_pypi_version(mock_client)
        assert result is None
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_version(self):
        from ansible_know.server import _check_pypi_version

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": ""}}
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        result = await _check_pypi_version(mock_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_prerelease_from_pypi(self):
        from ansible_know.server import _check_pypi_version

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "0.4.0rc1"}}
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        result = await _check_pypi_version(mock_client)
        assert result is None


class TestMaybeWarnUpgrade:
    @pytest.mark.asyncio
    async def test_warns_when_outdated(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _maybe_warn_upgrade
        from ansible_know.state import ServerState, SharedState

        version_info = {
            "installed": "0.3.2", "latest": "0.4.0",
            "outdated": True, "upgrade_command": "uvx --upgrade ansible-know-mcp",
        }
        state = ServerState(collection_manager=CollectionManager())
        shared = SharedState(version_info=version_info)
        mock_ctx = _make_mock_ctx(state, shared)

        await _maybe_warn_upgrade(mock_ctx)
        mock_ctx.warning.assert_called_once()
        assert "outdated" in mock_ctx.warning.call_args[0][0]
        assert state.upgrade_warned is True

    @pytest.mark.asyncio
    async def test_warns_only_once(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _maybe_warn_upgrade
        from ansible_know.state import ServerState, SharedState

        version_info = {
            "installed": "0.3.2", "latest": "0.4.0",
            "outdated": True, "upgrade_command": "uvx --upgrade ansible-know-mcp",
        }
        state = ServerState(collection_manager=CollectionManager())
        shared = SharedState(version_info=version_info)
        mock_ctx = _make_mock_ctx(state, shared)

        await _maybe_warn_upgrade(mock_ctx)
        await _maybe_warn_upgrade(mock_ctx)
        mock_ctx.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_warn_when_current(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _maybe_warn_upgrade
        from ansible_know.state import ServerState, SharedState

        version_info = {
            "installed": "0.3.2", "latest": "0.3.2",
            "outdated": False, "upgrade_command": "uvx --upgrade ansible-know-mcp",
        }
        state = ServerState(collection_manager=CollectionManager())
        shared = SharedState(version_info=version_info)
        mock_ctx = _make_mock_ctx(state, shared)

        await _maybe_warn_upgrade(mock_ctx)
        mock_ctx.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_warn_when_check_failed(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _maybe_warn_upgrade
        from ansible_know.state import ServerState, SharedState

        state = ServerState(collection_manager=CollectionManager())
        shared = SharedState(version_info=None)
        mock_ctx = _make_mock_ctx(state, shared)

        await _maybe_warn_upgrade(mock_ctx)
        mock_ctx.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_warn_when_no_ctx(self):
        from ansible_know.server import _maybe_warn_upgrade
        await _maybe_warn_upgrade(None)


class TestServerVersionResource:
    def test_returns_installed_version_without_pypi_check(self):
        import ansible_know.server as srv
        from ansible_know.state import SharedState

        shared = SharedState()
        old = srv._shared_state
        try:
            srv._shared_state = shared
            result = json.loads(srv.resource_server_version())
            assert result["installed"] == srv._VERSION
            assert result["latest"] is None
            assert result["outdated"] is None
        finally:
            srv._shared_state = old

    def test_returns_pypi_info_when_available(self):
        import ansible_know.server as srv
        from ansible_know.state import SharedState

        shared = SharedState(
            version_info={
                "installed": "0.3.2",
                "latest": "0.4.0",
                "outdated": True,
                "upgrade_command": "uvx --upgrade ansible-know-mcp",
            },
        )
        old = srv._shared_state
        try:
            srv._shared_state = shared
            result = json.loads(srv.resource_server_version())
            assert result["installed"] == "0.3.2"
            assert result["latest"] == "0.4.0"
            assert result["outdated"] is True
        finally:
            srv._shared_state = old


class TestSearchCollectionsTool:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        mock_result = {
            "query": "netbox",
            "count": 1,
            "collections": [
                {
                    "namespace": "netbox.netbox",
                    "description": "Ansible modules for NetBox",
                    "tags": ["dcim", "ipam"],
                    "download_count": 11999959,
                    "latest_version": "3.23.0",
                    "module_count": 88,
                    "deprecated": False,
                    "signed": False,
                }
            ],
        }
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=mock_result):
            from ansible_know.server import search_collections
            result = await search_collections("netbox")
        assert result["count"] == 1
        assert result["collections"][0]["namespace"] == "netbox.netbox"

    @pytest.mark.asyncio
    async def test_with_tags(self):
        mock_result = {"query": "network", "count": 0, "collections": []}
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=mock_result) as mock_search:
            from ansible_know.server import search_collections
            result = await search_collections("network", tags="networking,cloud")
        mock_search.assert_called_once_with("network", tags="networking,cloud")
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_rejects_empty_query(self):
        from ansible_know.server import search_collections
        result = await search_collections("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_long_query(self):
        from ansible_know.server import search_collections
        result = await search_collections("a" * 501)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_invalid_tags(self):
        from ansible_know.server import search_collections
        result = await search_collections("netbox", tags="valid,tags&inject=bad")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_long_tags(self):
        from ansible_know.server import search_collections
        result = await search_collections("netbox", tags="a" * 501)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_galaxy_error(self):
        from ansible_know.errors import GalaxyError
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", side_effect=GalaxyError("timeout")):
            from ansible_know.server import search_collections
            result = await search_collections("netbox")
        assert "error" in result


class TestLifespanHttpClient:
    @pytest.mark.asyncio
    async def test_get_module_doc_passes_lifespan_http_client(self, mock_ansible_doc):
        from ansible_know.collections import CollectionManager
        from ansible_know.state import ServerState, SharedState

        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        mock_client = AsyncMock()
        state = ServerState(collection_manager=CollectionManager())
        shared = SharedState()
        mock_ctx = _make_mock_ctx(state, shared, http_client=mock_client)

        with patch("ansible_know.resolution.resolve_module_doc") as mock_resolve:
            mock_resolve.return_value = (SAMPLE_MODULE_DOC, None)
            from ansible_know.server import get_module_doc
            await get_module_doc("ansible.builtin.package", ctx=mock_ctx)

        args, kwargs = mock_resolve.call_args
        assert args == ("ansible.builtin.package",)
        assert kwargs["http_client"] is mock_client
        assert kwargs["galaxy_servers"] == []
        assert kwargs["client_factory"] is not None

    @pytest.mark.asyncio
    async def test_search_collections_passes_lifespan_http_client(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.state import ServerState, SharedState

        mock_client = AsyncMock()
        state = ServerState(collection_manager=CollectionManager())
        shared = SharedState()
        mock_ctx = _make_mock_ctx(state, shared, http_client=mock_client)

        mock_result = {"query": "test", "count": 0, "collections": []}
        with patch("ansible_know.galaxy.GalaxyClient.from_config") as mock_from_config:
            mock_gc = AsyncMock()
            mock_gc.search_collections.return_value = mock_result
            mock_gc.__aenter__ = AsyncMock(return_value=mock_gc)
            mock_gc.__aexit__ = AsyncMock(return_value=False)
            mock_from_config.return_value = mock_gc
            from ansible_know.server import search_collections
            result = await search_collections("test", ctx=mock_ctx)

        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_validate_certs_false_skips_shared_client(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.state import ServerState, SharedState

        mock_client = AsyncMock()
        server = GalaxyServerConfig(
            name="private_hub",
            url="https://hub.internal.com/api/galaxy",
            token="secret",
            validate_certs=False,
        )
        state = ServerState(
            collection_manager=CollectionManager(),
            galaxy_servers=[server],
        )
        shared = SharedState()
        mock_ctx = _make_mock_ctx(state, shared, http_client=mock_client)

        mock_result = {"query": "test", "count": 0, "collections": []}
        with patch("ansible_know.galaxy.GalaxyClient.from_config") as mock_from_config:
            mock_gc = AsyncMock()
            mock_gc.search_collections.return_value = mock_result
            mock_gc.__aenter__ = AsyncMock(return_value=mock_gc)
            mock_gc.__aexit__ = AsyncMock(return_value=False)
            mock_from_config.return_value = mock_gc
            from ansible_know.server import search_collections
            await search_collections("test", ctx=mock_ctx)

        mock_from_config.assert_called_once()
        call_kwargs = mock_from_config.call_args
        assert call_kwargs[0][0] == server
        assert call_kwargs[1]["http_client"] is None

    @pytest.mark.asyncio
    async def test_validate_certs_true_uses_shared_client(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.state import ServerState, SharedState

        mock_client = AsyncMock()
        server = GalaxyServerConfig(
            name="public_galaxy",
            url="https://galaxy.ansible.com",
            validate_certs=True,
        )
        state = ServerState(
            collection_manager=CollectionManager(),
            galaxy_servers=[server],
        )
        shared = SharedState()
        mock_ctx = _make_mock_ctx(state, shared, http_client=mock_client)

        mock_result = {"query": "test", "count": 0, "collections": []}
        with patch("ansible_know.galaxy.GalaxyClient.from_config") as mock_from_config:
            mock_gc = AsyncMock()
            mock_gc.search_collections.return_value = mock_result
            mock_gc.__aenter__ = AsyncMock(return_value=mock_gc)
            mock_gc.__aexit__ = AsyncMock(return_value=False)
            mock_from_config.return_value = mock_gc
            from ansible_know.server import search_collections
            await search_collections("test", ctx=mock_ctx)

        mock_from_config.assert_called_once()
        call_kwargs = mock_from_config.call_args
        assert call_kwargs[0][0] == server
        assert call_kwargs[1]["http_client"] is mock_client

    @pytest.mark.asyncio
    async def test_tools_work_without_ctx(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.package")
        assert result["doc_source"] == "local"


class TestGetRoleDocTool:
    @pytest.mark.asyncio
    async def test_local_resolution(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_ROLE_DOC)
        from ansible_know.server import get_role_doc
        result = await get_role_doc("fedora.linux_system_roles.gfs2")
        assert result["role_name"] == "fedora.linux_system_roles.gfs2"
        assert result["content_type"] == "role"
        assert result["doc_source"] == "local"
        assert "main" in result["entry_points"]

    @pytest.mark.asyncio
    async def test_galaxy_fallback_on_empty_doc(self, mock_ansible_doc):
        mock_ansible_doc.return_value = "{}"
        galaxy_role_meta = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {
                "main": {"description": "Configure time sync", "options": []},
            },
            "dependencies": [],
            "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.121.0", "doc_warning": "parsed from README"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            from ansible_know.server import get_role_doc
            result = await get_role_doc("fedora.linux_system_roles.timesync")

        assert result["doc_source"] == "galaxy_readme"
        assert result["content_type"] == "role"

    @pytest.mark.asyncio
    async def test_graceful_degradation(self, mock_ansible_doc):
        mock_ansible_doc.return_value = "{}"
        from ansible_know.errors import GalaxyError

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            side_effect=GalaxyError("not found"),
        ):
            from ansible_know.server import get_role_doc
            result = await get_role_doc("some.col.missing_role")

        assert result["doc_source"] == "unavailable"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_validation_error(self):
        from ansible_know.server import get_role_doc
        result = await get_role_doc("invalid")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cached_missing_collection_skips_local(self, mock_ansible_doc):
        from ansible_know.collections import CollectionManager
        from ansible_know.state import ServerState, SharedState

        state = ServerState(collection_manager=CollectionManager())
        state.missing_collections.add("some.col")
        shared = SharedState()
        mock_ctx = _make_mock_ctx(state, shared)

        galaxy_role_meta = {
            "role_name": "some.col.role",
            "short_description": "A role",
            "entry_points": {"main": {"description": "", "options": []}},
            "dependencies": [],
            "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.0.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            from ansible_know.server import get_role_doc
            result = await get_role_doc("some.col.role", ctx=mock_ctx)

        mock_ansible_doc.assert_not_called()
        assert result["doc_source"] == "galaxy_readme"

        assert "ansible.builtin" not in state.missing_collections


class TestGenerateRoleSkillTool:
    @pytest.mark.asyncio
    async def test_generates_role_skill(self, tmp_path, mock_ansible_doc, monkeypatch):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_ROLE_DOC)
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)
        from ansible_know.server import generate_role_skill
        result = await generate_role_skill("fedora.linux_system_roles.gfs2")
        assert "fedora.linux_system_roles.gfs2" in result
        assert (tmp_path / "fedora.linux_system_roles" / "gfs2" / "SKILL.md").exists()
        assert (tmp_path / "fedora.linux_system_roles" / "gfs2" / "assets" / "playbook.yml").exists()
        assert not (tmp_path / "fedora.linux_system_roles" / "gfs2" / "scripts").exists()

    @pytest.mark.asyncio
    async def test_validation_error(self):
        from ansible_know.server import generate_role_skill
        result = await generate_role_skill("invalid")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_galaxy_fallback(self, tmp_path, mock_ansible_doc, monkeypatch):
        mock_ansible_doc.return_value = "{}"
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)

        galaxy_role_meta = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {
                "main": {
                    "description": "Configure time sync",
                    "options": [
                        {
                            "name": "timesync_ntp_servers",
                            "type": "list",
                            "required": False,
                            "default": "[]",
                            "description": "NTP servers",
                        }
                    ],
                },
            },
            "dependencies": [],
            "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.121.0", "doc_warning": "parsed from README"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            from ansible_know.server import generate_role_skill
            result = await generate_role_skill("fedora.linux_system_roles.timesync")

        assert "fedora.linux_system_roles.timesync" in result
        assert (tmp_path / "fedora.linux_system_roles" / "timesync" / "SKILL.md").exists()


class TestGetCollectionManifestWithRoles:
    @pytest.mark.asyncio
    async def test_manifest_includes_roles(self, mock_ansible_doc):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "-t" in args and "role" in args and "--list" in args:
                return json.dumps(SAMPLE_ROLE_LIST)
            if "--list" in args:
                return json.dumps(SAMPLE_MODULE_LIST)
            return json.dumps(SAMPLE_MODULE_DOC)

        mock_ansible_doc.side_effect = side_effect

        with patch("ansible_know.collection_manifest.load_cached_manifest", return_value=None):
            from ansible_know.server import get_collection_manifest
            result = await get_collection_manifest("fedora.linux_system_roles")

        assert "roles" in result
        assert "role_count" in result

    @pytest.mark.asyncio
    async def test_search_collections_has_role_count(self):
        mock_result = {
            "query": "linux",
            "count": 1,
            "collections": [
                {
                    "namespace": "fedora.linux_system_roles",
                    "description": "Linux system roles",
                    "tags": [],
                    "download_count": 2600000,
                    "latest_version": "1.121.0",
                    "module_count": 27,
                    "role_count": 43,
                    "deprecated": False,
                    "signed": False,
                }
            ],
        }
        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=mock_result):
            from ansible_know.server import search_collections
            result = await search_collections("linux")
        assert result["collections"][0]["role_count"] == 43


class TestPeriodicVersionCheck:
    @pytest.mark.asyncio
    async def test_updates_version_on_new_release(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _periodic_version_check
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState(version_info={"installed": "0.3.0", "latest": "0.3.0", "outdated": False})
        sessions = SessionManager(shared, collection_factory=CollectionManager)
        new_info = {"installed": "0.3.0", "latest": "0.4.0", "outdated": True}

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        mock_client = MagicMock()
        mock_client.is_closed = False
        with patch("ansible_know.server._check_pypi_version", return_value=new_info):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_version_check(mock_client, shared, sessions)

        assert shared.version_info["latest"] == "0.4.0"

    @pytest.mark.asyncio
    async def test_skips_when_check_returns_none(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _periodic_version_check
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState(version_info={"installed": "0.3.0", "latest": "0.3.0", "outdated": False})
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        mock_client = MagicMock()
        mock_client.is_closed = False
        with patch("ansible_know.server._check_pypi_version", return_value=None):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_version_check(mock_client, shared, sessions)

        # version_info unchanged when check returns None
        assert shared.version_info["latest"] == "0.3.0"

    @pytest.mark.asyncio
    async def test_no_update_when_same_version(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _periodic_version_check
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState(version_info={"installed": "0.3.0", "latest": "0.3.0", "outdated": False})
        sessions = SessionManager(shared, collection_factory=CollectionManager)
        same_info = {"installed": "0.3.0", "latest": "0.3.0", "outdated": False}

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        mock_client = MagicMock()
        mock_client.is_closed = False
        with patch("ansible_know.server._check_pypi_version", return_value=same_info):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_version_check(mock_client, shared, sessions)

        # Same version: on_version_update is called but upgrade_warned not reset
        assert shared.version_info["latest"] == "0.3.0"

    @pytest.mark.asyncio
    async def test_exits_when_client_closed(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _periodic_version_check
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState(version_info={"installed": "0.3.0", "latest": "0.3.0", "outdated": False})
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        mock_client = MagicMock()
        mock_client.is_closed = True

        async def fake_sleep(_):
            pass

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await _periodic_version_check(mock_client, shared, sessions)

        assert shared.version_info["latest"] == "0.3.0"

    @pytest.mark.asyncio
    async def test_survives_unexpected_exception(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _periodic_version_check
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState(version_info={"installed": "0.3.0", "latest": "0.3.0", "outdated": False})
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError

        mock_client = MagicMock()
        mock_client.is_closed = False
        with patch("ansible_know.server._check_pypi_version", side_effect=RuntimeError("boom")):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_version_check(mock_client, shared, sessions)

        # Loop survived the exception and ran again (call_count > 2)
        assert call_count > 2
        assert shared.version_info["latest"] == "0.3.0"


class TestGetStateSessionIsolation:
    @pytest.mark.asyncio
    async def test_different_sessions_get_different_state(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _get_state
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState()
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        ctx_a = MagicMock()
        ctx_a.lifespan_context = {"shared": shared, "sessions": sessions, "http_client": None}
        ctx_a.session_id = "session-a"

        ctx_b = MagicMock()
        ctx_b.lifespan_context = {"shared": shared, "sessions": sessions, "http_client": None}
        ctx_b.session_id = "session-b"

        state_a = await _get_state(ctx_a)
        state_b = await _get_state(ctx_b)
        assert state_a is not state_b

    @pytest.mark.asyncio
    async def test_same_session_gets_same_state(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _get_state
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState()
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        ctx = MagicMock()
        ctx.lifespan_context = {"shared": shared, "sessions": sessions, "http_client": None}
        ctx.session_id = "session-x"

        state_1 = await _get_state(ctx)
        state_2 = await _get_state(ctx)
        assert state_1 is state_2

    @pytest.mark.asyncio
    async def test_none_ctx_returns_ephemeral(self):
        from ansible_know.server import _get_state

        state = await _get_state(None)
        assert state is not None
        assert state.collection_manager is not None
        # Each call returns a new ephemeral instance
        state_2 = await _get_state(None)
        assert state is not state_2

    @pytest.mark.asyncio
    async def test_registers_cleanup_on_session_exit_stack(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _get_state
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState()
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        mock_exit_stack = MagicMock()
        ctx = MagicMock()
        ctx.lifespan_context = {"shared": shared, "sessions": sessions, "http_client": None}
        ctx.session_id = "session-cleanup"
        ctx.session._exit_stack = mock_exit_stack
        ctx.session._ansible_know_cleanup_registered = False

        await _get_state(ctx)

        mock_exit_stack.push_async_callback.assert_called_once()
        assert ctx.session._ansible_know_cleanup_registered is True

    @pytest.mark.asyncio
    async def test_cleanup_not_registered_twice(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _get_state
        from ansible_know.state import SessionManager, SharedState

        shared = SharedState()
        sessions = SessionManager(shared, collection_factory=CollectionManager)

        mock_exit_stack = MagicMock()
        ctx = MagicMock()
        ctx.lifespan_context = {"shared": shared, "sessions": sessions, "http_client": None}
        ctx.session_id = "session-once"
        ctx.session._exit_stack = mock_exit_stack
        ctx.session._ansible_know_cleanup_registered = False

        await _get_state(ctx)
        await _get_state(ctx)

        mock_exit_stack.push_async_callback.assert_called_once()


class TestClearCache:
    """Tests for the clear_cache tool."""

    @pytest.mark.asyncio
    async def test_clear_all(self):
        from ansible_know.server import clear_cache

        with patch("ansible_know.galaxy.clear_cache") as mock_galaxy, \
             patch("ansible_know.docs.clear_cache") as mock_docs:
            result = await clear_cache()

        assert result == {"cleared": ["galaxy_versions", "galaxy_blobs", "doc_manifests"]}
        mock_galaxy.assert_called_once()
        mock_docs.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_galaxy_only(self):
        from ansible_know.server import clear_cache

        with patch("ansible_know.galaxy.clear_cache") as mock_galaxy, \
             patch("ansible_know.docs.clear_cache") as mock_docs:
            result = await clear_cache(scope="galaxy")

        assert result == {"cleared": ["galaxy_versions", "galaxy_blobs"]}
        mock_galaxy.assert_called_once()
        mock_docs.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_docs_only(self):
        from ansible_know.server import clear_cache

        with patch("ansible_know.galaxy.clear_cache") as mock_galaxy, \
             patch("ansible_know.docs.clear_cache") as mock_docs:
            result = await clear_cache(scope="docs")

        assert result == {"cleared": ["doc_manifests"]}
        mock_galaxy.assert_not_called()
        mock_docs.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_scope(self):
        from ansible_know.server import clear_cache

        result = await clear_cache(scope="invalid")

        assert "error" in result
        assert "Invalid scope" in result["error"]
