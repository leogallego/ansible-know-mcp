"""Tests for Agent Plugins packaging helper (issue #223)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from ansible_know.errors import ValidationError
from ansible_know.skills import (
    MCP_SCHEMA,
    PLUGIN_SCHEMA,
    default_plugin_name,
    package_as_agent_plugin,
)
from ansible_know.validation import (
    validate_mcp_server_url,
    validate_mcp_transport,
    validate_plugin_name,
)


def _write_skill_tree(skills_root: Path, collection_kebab: str) -> Path:
    """Create a minimal generated-skills tree for packaging tests."""
    collection_dir = skills_root / collection_kebab
    collection_dir.mkdir(parents=True)
    (collection_dir / "SKILL.md").write_text(
        "---\nname: netbox-netbox\ndescription: Collection overview\n---\n# Collection\n"
    )
    (collection_dir / "MANIFEST.json").write_text(
        json.dumps({
            "collection": "netbox.netbox",
            "collection_version": "3.2.0",
            "module_count": 1,
        })
    )
    module_dir = collection_dir / "netbox-device"
    module_dir.mkdir()
    (module_dir / "SKILL.md").write_text(
        "---\nname: netbox-device\ndescription: Manage devices\n---\n# Device\n"
    )
    scripts = module_dir / "scripts"
    scripts.mkdir()
    (scripts / "run.sh").write_text("#!/bin/sh\necho hi\n")
    plugin_dir = collection_dir / "lookup-nb-lookup"
    plugin_dir.mkdir()
    (plugin_dir / "SKILL.md").write_text(
        "---\nname: lookup-nb-lookup\ndescription: Lookup helper\n---\n# Lookup\n"
    )
    return collection_dir


class TestValidatePluginName:
    def test_accepts_kebab_name(self) -> None:
        validate_plugin_name("ansible-netbox-netbox-agentplugin")

    def test_accepts_single_char(self) -> None:
        validate_plugin_name("a")

    def test_accepts_period(self) -> None:
        validate_plugin_name("acme.tools")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("My-Plugin")

    def test_rejects_leading_hyphen(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("-start")

    def test_rejects_double_hyphen(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("has--double")

    def test_rejects_double_period(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("too.many..dots")

    def test_rejects_length_65(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("a" * 65)

    def test_accepts_length_64(self) -> None:
        validate_plugin_name("a" * 64)

    def test_rejects_underscore(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("bad_name")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            validate_plugin_name("")


class TestDefaultPluginName:
    def test_prefixes_ansible(self) -> None:
        assert default_plugin_name("netbox.netbox") == "ansible-netbox-netbox-agentplugin"

    def test_overlong_fails_closed(self) -> None:
        # ansible- (8) + 60-char ns + "-" + 1 = already past 64 when long enough
        long_ns = "n" * 40
        long_coll = "c" * 40
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            default_plugin_name(f"{long_ns}.{long_coll}")


class TestValidateMcpTransportAndUrl:
    def test_accepts_stdio_and_streamable_http(self) -> None:
        validate_mcp_transport("stdio")
        validate_mcp_transport("streamable-http")

    def test_rejects_unknown_transport(self) -> None:
        with pytest.raises(ValidationError, match="Invalid MCP transport"):
            validate_mcp_transport("sse")

    def test_accepts_https_url(self) -> None:
        validate_mcp_server_url("https://aap.example.com/mcp/skills/")

    def test_accepts_localhost_http(self) -> None:
        validate_mcp_server_url("http://localhost:8080/mcp")

    def test_rejects_plain_http_remote(self) -> None:
        with pytest.raises(ValidationError, match="loopback"):
            validate_mcp_server_url("http://aap.example.com/mcp")

    def test_rejects_userinfo(self) -> None:
        with pytest.raises(ValidationError, match="userinfo|fragment"):
            validate_mcp_server_url("https://user:pass@aap.example.com/mcp")


class TestPackageAsAgentPlugin:
    def test_wraps_skills_into_plugin_layout(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_as_agent_plugin(skills, "netbox.netbox", out)

        plugin_dir = Path(result["plugin_dir"])
        assert result["plugin_name"] == "ansible-netbox-netbox-agentplugin"
        assert result["skill_count"] == 3
        assert set(result["skills"]) == {
            "netbox-netbox",
            "netbox-device",
            "lookup-nb-lookup",
        }
        assert (plugin_dir / "skills" / "netbox-device" / "SKILL.md").is_file()
        assert (plugin_dir / "skills" / "netbox-device" / "scripts" / "run.sh").is_file()
        assert (plugin_dir / "skills" / "netbox-netbox" / "SKILL.md").is_file()
        # Flat layout — no nested collection/module under skills/
        assert not (plugin_dir / "skills" / "netbox-netbox" / "netbox-device").exists()
        assert not (plugin_dir / "MANIFEST.json").exists()

        plugin_json = json.loads((plugin_dir / "plugin.json").read_text())
        assert plugin_json["$schema"] == PLUGIN_SCHEMA
        assert plugin_json["name"] == "ansible-netbox-netbox-agentplugin"
        assert plugin_json["version"] == "3.2.0"
        assert plugin_json["description"] == "Collection overview"
        assert "ansible" in plugin_json["keywords"]
        assert "automation" in plugin_json["keywords"]
        assert "netbox.netbox" in plugin_json["keywords"]
        assert "netbox-device" in plugin_json["keywords"]
        assert set(plugin_json.keys()) <= {
            "$schema", "name", "version", "description", "author",
            "homepage", "repository", "license", "keywords", "extensions",
        }

        mcp_json = json.loads((plugin_dir / "mcp.json").read_text())
        assert mcp_json["$schema"] == MCP_SCHEMA
        assert mcp_json["mcpServers"]["ansible-know"]["type"] == "stdio"
        assert mcp_json["mcpServers"]["ansible-know"]["command"] == "uvx"
        assert mcp_json["mcpServers"]["ansible-know"]["args"] == ["ansible-know-mcp"]
        assert result["plugin_json"] == str(plugin_dir / "plugin.json")
        assert result["mcp_json"] == str(plugin_dir / "mcp.json")

        archive = Path(result["archive"] or "")
        assert archive.name == "ansible-netbox-netbox-agentplugin-3.2.0.tar.gz"
        assert archive.is_file()
        with tarfile.open(archive, "r:gz") as tf:
            names = tf.getnames()
        assert "ansible-netbox-netbox-agentplugin/plugin.json" in names
        assert "ansible-netbox-netbox-agentplugin/skills/netbox-device/SKILL.md" in names

    def test_skips_plugin_json_when_disabled(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_as_agent_plugin(
            skills, "netbox.netbox", out, write_plugin_json=False,
        )
        assert result["plugin_json"] is None
        assert not (Path(result["plugin_dir"]) / "plugin.json").exists()
        assert (Path(result["plugin_dir"]) / "mcp.json").is_file()

    def test_skips_mcp_json_when_disabled(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_as_agent_plugin(
            skills, "netbox.netbox", out, include_mcp_config=False,
        )
        assert result["mcp_json"] is None
        assert not (Path(result["plugin_dir"]) / "mcp.json").exists()
        assert (Path(result["plugin_dir"]) / "plugin.json").is_file()

    def test_streamable_http_mcp_config(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")
        url = "https://aap.example.com/mcp/skills/"

        result = package_as_agent_plugin(
            skills,
            "netbox.netbox",
            out,
            mcp_transport="streamable-http",
            mcp_url=url,
            write_tarball=False,
        )
        mcp_json = json.loads(Path(result["mcp_json"] or "").read_text())
        assert mcp_json["mcpServers"]["ansible-know"] == {
            "type": "streamable-http",
            "url": url,
        }
        assert result["archive"] is None

    def test_streamable_http_requires_url(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill_tree(skills, "netbox-netbox")
        with pytest.raises(ValidationError, match="mcp_url is required"):
            package_as_agent_plugin(
                skills,
                "netbox.netbox",
                tmp_path / "out",
                mcp_transport="streamable-http",
            )

    def test_skips_tarball_when_disabled(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")
        result = package_as_agent_plugin(
            skills, "netbox.netbox", out, write_tarball=False,
        )
        assert result["archive"] is None
        assert not list(out.glob("*.tar.gz"))

    def test_removes_stale_manifests_when_disabled(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        first = package_as_agent_plugin(skills, "netbox.netbox", out)
        plugin_dir = Path(first["plugin_dir"])
        assert (plugin_dir / "plugin.json").is_file()
        assert (plugin_dir / "mcp.json").is_file()
        assert Path(first["archive"] or "").is_file()

        second = package_as_agent_plugin(
            skills,
            "netbox.netbox",
            out,
            write_plugin_json=False,
            include_mcp_config=False,
            write_tarball=False,
        )
        assert second["plugin_json"] is None
        assert second["mcp_json"] is None
        assert second["archive"] is None
        assert not (plugin_dir / "plugin.json").exists()
        assert not (plugin_dir / "mcp.json").exists()
        assert not list(out.glob("*.tar.gz"))

    def test_custom_plugin_name(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill_tree(skills, "netbox-netbox")
        result = package_as_agent_plugin(
            skills, "netbox.netbox", tmp_path / "out", plugin_name="netbox.skills",
        )
        assert result["plugin_name"] == "netbox.skills"
        assert Path(result["plugin_dir"]).name == "netbox.skills"

    def test_invalid_plugin_name(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill_tree(skills, "netbox-netbox")
        with pytest.raises(ValidationError, match="Invalid plugin name"):
            package_as_agent_plugin(
                skills, "netbox.netbox", tmp_path / "out", plugin_name="Bad_Name",
            )

    def test_missing_skills_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No generated skills"):
            package_as_agent_plugin(tmp_path, "netbox.netbox", tmp_path / "out")

    def test_idempotent_replace_skills(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")
        first = package_as_agent_plugin(skills, "netbox.netbox", out)
        stale = Path(first["plugin_dir"]) / "skills" / "stale-skill"
        stale.mkdir()
        (stale / "SKILL.md").write_text("---\nname: stale\ndescription: x\n---\n")

        second = package_as_agent_plugin(skills, "netbox.netbox", out)
        assert "stale-skill" not in second["skills"]
        assert not stale.exists()


class TestPackageAsPluginTool:
    @pytest.mark.asyncio
    async def test_package_as_plugin_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")
        monkeypatch.setenv("ANSIBLE_KNOW_SKILLS_DIR", str(skills))

        from ansible_know.server import package_as_plugin

        result = await package_as_plugin(
            collection="netbox.netbox",
            output_dir=str(out),
            source_dir=str(skills),
        )
        assert "error" not in result
        assert result["skill_count"] == 3
        assert Path(result["plugin_dir"]).is_dir()

    @pytest.mark.asyncio
    async def test_package_as_plugin_validation_error(self) -> None:
        from ansible_know.server import package_as_plugin

        result = await package_as_plugin(
            collection="not a namespace",
            output_dir="/tmp/out",
        )
        assert "error" in result
