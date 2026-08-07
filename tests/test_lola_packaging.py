"""Tests for Lola packaging helper (issue #149)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from ansible_know.errors import ValidationError
from ansible_know.skills import (
    default_lola_module_name,
    package_collection_for_lola,
    resolve_collection_skills_dir,
)
from ansible_know.validation import validate_lola_module_name


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


class TestValidateLolaModuleName:
    def test_accepts_kebab_name(self) -> None:
        validate_lola_module_name("ansible-netbox-netbox")

    def test_rejects_path_separator(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Lola module name"):
            validate_lola_module_name("../escape")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Lola module name"):
            validate_lola_module_name("")

    def test_accepts_ansible_prefix_at_namespace_max(self) -> None:
        # ansible- (8) + 128-char kebab collection name
        name = "ansible-" + ("a" * 60) + "-" + ("b" * 67)
        assert len(name) == 136
        validate_lola_module_name(name)


class TestDefaultLolaModuleName:
    def test_prefixes_ansible(self) -> None:
        assert default_lola_module_name("netbox.netbox") == "ansible-netbox-netbox"


class TestResolveCollectionSkillsDir:
    def test_finds_collection_dir(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill_tree(skills, "netbox-netbox")
        found = resolve_collection_skills_dir(skills, "netbox.netbox")
        assert found == (skills / "netbox-netbox").resolve()

    def test_searches_multiple_roots(self, tmp_path: Path) -> None:
        first = tmp_path / "a"
        second = tmp_path / "b"
        first.mkdir()
        _write_skill_tree(second, "netbox-netbox")
        found = resolve_collection_skills_dir([first, second], "netbox.netbox")
        assert found == (second / "netbox-netbox").resolve()

    def test_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No generated skills"):
            resolve_collection_skills_dir(tmp_path, "netbox.netbox")


class TestPackageCollectionForLola:
    def test_wraps_skills_into_lola_layout(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_collection_for_lola(skills, "netbox.netbox", out)

        module_dir = Path(result["module_dir"])
        assert result["module_name"] == "ansible-netbox-netbox"
        assert result["skill_count"] == 3
        assert sorted(result["skills"]) == [
            "lookup-nb-lookup",
            "netbox-device",
            "netbox-netbox",
        ]
        assert (module_dir / "skills" / "netbox-device" / "SKILL.md").is_file()
        assert (module_dir / "skills" / "netbox-device" / "scripts" / "run.sh").is_file()
        assert (module_dir / "skills" / "lookup-nb-lookup" / "SKILL.md").is_file()
        assert (module_dir / "skills" / "netbox-netbox" / "SKILL.md").is_file()
        assert not (module_dir / "skills" / "MANIFEST.json").exists()
        assert not (module_dir / "skills" / "SKILL.md").exists()

    def test_writes_market_yml_from_metadata(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_collection_for_lola(skills, "netbox.netbox", out)

        market_path = Path(result["market_yml"] or "")
        assert market_path.is_file()
        data = yaml.safe_load(market_path.read_text())
        assert data["name"] == "ansible-netbox-netbox"
        assert data["collection"] == "netbox.netbox"
        assert data["version"] == "3.2.0"
        assert data["skill_count"] == 3
        assert data["description"] == "Collection overview"
        assert "ansible" in data["tags"]

    def test_skips_market_yml_when_disabled(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_collection_for_lola(
            skills, "netbox.netbox", out, write_market_yml=False,
        )

        assert result["market_yml"] is None
        assert not (Path(result["module_dir"]) / "lola-market.yml").exists()

    def test_custom_module_name(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        result = package_collection_for_lola(
            skills, "netbox.netbox", out, module_name="my-netbox-mod",
        )

        assert result["module_name"] == "my-netbox-mod"
        assert Path(result["module_dir"]).name == "my-netbox-mod"

    def test_idempotent_overwrite(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        collection_dir = _write_skill_tree(skills, "netbox-netbox")

        first = package_collection_for_lola(skills, "netbox.netbox", out)
        module_dir = Path(first["module_dir"])
        stale = module_dir / "skills" / "netbox-device" / "stale.txt"
        stale.write_text("old")
        assert (module_dir / "lola-market.yml").is_file()

        # Remove a source skill and disable market yml — orphans must go away.
        shutil.rmtree(collection_dir / "lookup-nb-lookup")
        second = package_collection_for_lola(
            skills, "netbox.netbox", out, write_market_yml=False,
        )

        assert second["skill_count"] == 2
        assert sorted(second["skills"]) == ["netbox-device", "netbox-netbox"]
        assert not stale.exists()
        assert not (module_dir / "skills" / "lookup-nb-lookup").exists()
        assert second["market_yml"] is None
        assert not (module_dir / "lola-market.yml").exists()

    def test_rejects_output_path_escape_via_module_name(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        with pytest.raises(ValidationError, match="Invalid Lola module name"):
            package_collection_for_lola(
                skills, "netbox.netbox", out, module_name="../escape",
            )

    def test_empty_collection_raises(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        empty = skills / "netbox-netbox"
        empty.mkdir(parents=True)
        (empty / "MANIFEST.json").write_text("{}")

        with pytest.raises(FileNotFoundError, match="no SKILL.md"):
            package_collection_for_lola(skills, "netbox.netbox", tmp_path / "out")

    def test_skips_nested_symlinks(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        collection_dir = _write_skill_tree(skills, "netbox-netbox")
        secret = tmp_path / "secret.txt"
        secret.write_text("do-not-copy")
        link = collection_dir / "netbox-device" / "leak"
        link.symlink_to(secret)

        result = package_collection_for_lola(skills, "netbox.netbox", tmp_path / "out")
        packaged = (
            Path(result["module_dir"]) / "skills" / "netbox-device" / "leak"
        )
        assert not packaged.exists()

    def test_rejects_empty_module_name(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill_tree(skills, "netbox-netbox")
        with pytest.raises(ValidationError, match="Invalid Lola module name"):
            package_collection_for_lola(
                skills, "netbox.netbox", tmp_path / "out", module_name="",
            )

    def test_failed_repackage_preserves_existing_skills(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        collection_dir = _write_skill_tree(skills, "netbox-netbox")
        first = package_collection_for_lola(skills, "netbox.netbox", tmp_path / "out")
        module_skills = Path(first["module_dir"]) / "skills"
        assert (module_skills / "netbox-device" / "SKILL.md").is_file()

        # Empty the source of all SKILL.md packages, then re-package.
        (collection_dir / "SKILL.md").unlink()
        shutil.rmtree(collection_dir / "netbox-device")
        shutil.rmtree(collection_dir / "lookup-nb-lookup")

        with pytest.raises(FileNotFoundError, match="no SKILL.md"):
            package_collection_for_lola(skills, "netbox.netbox", tmp_path / "out")

        assert (module_skills / "netbox-device" / "SKILL.md").is_file()

    def test_skips_nested_dir_colliding_with_collection_name(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        collection_dir = _write_skill_tree(skills, "netbox-netbox")
        collide = collection_dir / "netbox-netbox"
        collide.mkdir()
        (collide / "SKILL.md").write_text(
            "---\nname: collide\ndescription: should be skipped\n---\n"
        )

        result = package_collection_for_lola(skills, "netbox.netbox", tmp_path / "out")
        assert result["skills"].count("netbox-netbox") == 1
        overview = (
            Path(result["module_dir"]) / "skills" / "netbox-netbox" / "SKILL.md"
        ).read_text()
        assert "Collection overview" in overview
        assert "should be skipped" not in overview


@pytest.mark.asyncio
class TestPackageForLolaTool:
    async def test_package_for_lola_tool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")
        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_PATH", raising=False)
        monkeypatch.setenv("ANSIBLE_KNOW_SKILLS_DIR", str(skills))
        import ansible_know.config as config_mod

        monkeypatch.delattr(config_mod, "SKILLS_DIR", raising=False)
        config_mod.__dict__.pop("SKILLS_DIR", None)

        from ansible_know.server import package_for_lola

        result = await package_for_lola(
            collection="netbox.netbox",
            output_dir=str(out),
        )

        assert "error" not in result
        assert result["skill_count"] == 3
        assert Path(result["module_dir"]).is_dir()

    async def test_package_for_lola_source_dir_override(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        out = tmp_path / "out"
        _write_skill_tree(skills, "netbox-netbox")

        from ansible_know.server import package_for_lola

        result = await package_for_lola(
            collection="netbox.netbox",
            output_dir=str(out),
            source_dir=str(skills),
            write_market_yml=False,
        )

        assert result["market_yml"] is None
        assert result["skill_count"] == 3

    async def test_package_for_lola_validation_error(self) -> None:
        from ansible_know.server import package_for_lola

        result = await package_for_lola(
            collection="not a namespace",
            output_dir="/tmp/out",
        )
        assert "error" in result
