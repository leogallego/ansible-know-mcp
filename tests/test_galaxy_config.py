"""Tests for ansible_know.galaxy_config."""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from ansible_know.galaxy_config import (
    GalaxyServerConfig,
    _sanitize_credential,
    find_ansible_cfg,
    load_galaxy_servers,
)


class TestFindAnsibleCfg:
    def test_env_var_overrides_all(self, tmp_path):
        cfg = tmp_path / "custom.cfg"
        cfg.write_text("[galaxy]\n")
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            assert find_ansible_cfg() == cfg

    def test_env_var_nonexistent_returns_none(self):
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": "/nonexistent/ansible.cfg"}):
            assert find_ansible_cfg() is None

    def test_cwd_ansible_cfg(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[galaxy]\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANSIBLE_CONFIG", None)
            assert find_ansible_cfg() == cfg

    def test_no_cfg_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANSIBLE_CONFIG", None)
            with patch("ansible_know.galaxy_config.Path.home", return_value=tmp_path / "fakehome"):
                result = find_ansible_cfg()
                assert result is None or result == Path("/etc/ansible/ansible.cfg")


class TestLoadGalaxyServers:
    def test_no_ansible_cfg_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANSIBLE_CONFIG", None)
            with patch("ansible_know.galaxy_config.find_ansible_cfg", return_value=None):
                servers = load_galaxy_servers()
        assert len(servers) >= 1
        assert servers[0].url == "https://galaxy.ansible.com"

    def test_parses_server_list(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub, public_galaxy

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
            token = secret123
            validate_certs = false

            [galaxy_server.public_galaxy]
            url = https://galaxy.ansible.com/
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert len(servers) == 2
        assert servers[0].name == "my_hub"
        assert servers[0].url == "https://hub.example.com/api/galaxy"
        assert servers[0].token == "secret123"
        assert servers[0].validate_certs is False
        assert servers[1].name == "public_galaxy"
        assert servers[1].url == "https://galaxy.ansible.com"
        assert servers[1].token is None
        assert servers[1].validate_certs is True

    def test_env_var_override_token(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
            token = original_token
        """))
        with patch.dict(os.environ, {
            "ANSIBLE_CONFIG": str(cfg),
            "ANSIBLE_GALAXY_SERVER_MY_HUB_TOKEN": "override_token",
        }):
            servers = load_galaxy_servers()

        assert servers[0].token == "override_token"

    def test_env_var_override_url(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
        """))
        with patch.dict(os.environ, {
            "ANSIBLE_CONFIG": str(cfg),
            "ANSIBLE_GALAXY_SERVER_MY_HUB_URL": "https://override.example.com/api/galaxy/",
        }):
            servers = load_galaxy_servers()

        assert servers[0].url == "https://override.example.com/api/galaxy"

    def test_skips_server_without_url(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = bad_server, good_server

            [galaxy_server.bad_server]
            token = no_url_here

            [galaxy_server.good_server]
            url = https://galaxy.ansible.com/
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        names = [s.name for s in servers]
        assert "bad_server" not in names
        assert "good_server" in names

    def test_public_galaxy_appended_if_missing(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = private_hub

            [galaxy_server.private_hub]
            url = https://hub.internal.com/api/galaxy/
            token = secret
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert len(servers) == 2
        assert servers[0].name == "private_hub"
        assert servers[1].name == "public_galaxy"
        assert servers[1].url == "https://galaxy.ansible.com"

    def test_public_galaxy_not_duplicated(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = galaxy

            [galaxy_server.galaxy]
            url = https://galaxy.ansible.com/
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert len(servers) == 1
        assert servers[0].url == "https://galaxy.ansible.com"

    def test_basic_auth_fields(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
            username = admin
            password = secretpass
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert servers[0].username == "admin"
        assert servers[0].password == "secretpass"
        assert servers[0].token is None

    def test_custom_galaxy_url_fallback(self, tmp_path, monkeypatch):
        with patch("ansible_know.galaxy_config.find_ansible_cfg", return_value=None):
            with patch("ansible_know.galaxy_config.GALAXY_BASE_URL", "https://custom.galaxy.example.com"):
                servers = load_galaxy_servers()

        assert servers[0].url == "https://custom.galaxy.example.com"
        assert servers[-1].url == "https://galaxy.ansible.com"

    def test_timeout_parsing(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
            timeout = 120
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert servers[0].timeout == 120


    def test_validate_certs_off(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
            validate_certs = off
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert servers[0].validate_certs is False

    def test_validate_certs_on(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
            validate_certs = on
        """))
        with patch.dict(os.environ, {"ANSIBLE_CONFIG": str(cfg)}):
            servers = load_galaxy_servers()

        assert servers[0].validate_certs is True

    def test_crlf_stripped_from_token(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
        """))
        with patch.dict(os.environ, {
            "ANSIBLE_CONFIG": str(cfg),
            "ANSIBLE_GALAXY_SERVER_MY_HUB_TOKEN": "secret\r\nX-Evil: true",
        }):
            servers = load_galaxy_servers()

        assert servers[0].token == "secretX-Evil: true"
        assert "\r" not in servers[0].token
        assert "\n" not in servers[0].token

    def test_crlf_stripped_from_username(self, tmp_path):
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(textwrap.dedent("""\
            [galaxy]
            server_list = my_hub

            [galaxy_server.my_hub]
            url = https://hub.example.com/api/galaxy/
        """))
        with patch.dict(os.environ, {
            "ANSIBLE_CONFIG": str(cfg),
            "ANSIBLE_GALAXY_SERVER_MY_HUB_USERNAME": "admin\r\n",
        }):
            servers = load_galaxy_servers()

        assert servers[0].username == "admin"


class TestSanitizeCredential:
    def test_none_returns_none(self):
        assert _sanitize_credential(None) is None

    def test_clean_value_unchanged(self):
        assert _sanitize_credential("my_token_123") == "my_token_123"

    def test_strips_crlf(self):
        assert _sanitize_credential("secret\r\nX-Evil: true") == "secretX-Evil: true"

    def test_strips_lone_cr(self):
        assert _sanitize_credential("secret\rvalue") == "secretvalue"

    def test_strips_lone_lf(self):
        assert _sanitize_credential("secret\nvalue") == "secretvalue"

    def test_empty_after_strip_returns_none(self):
        assert _sanitize_credential("\r\n") is None

    def test_whitespace_stripped(self):
        assert _sanitize_credential("  token  ") == "token"


class TestGalaxyServerConfig:
    def test_frozen_dataclass(self):
        config = GalaxyServerConfig(name="test", url="https://example.com")
        with pytest.raises(AttributeError):
            config.name = "changed"

    def test_defaults(self):
        config = GalaxyServerConfig(name="test", url="https://example.com")
        assert config.token is None
        assert config.username is None
        assert config.password is None
        assert config.auth_url is None
        assert config.client_id is None
        assert config.validate_certs is True
        assert config.timeout == 60
