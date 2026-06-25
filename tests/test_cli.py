"""Tests for ansible_know.cli."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ansible_know.cli import ServerConfig, parse_args


class TestServerConfig:
    def test_is_frozen(self):
        config = ServerConfig(transport="stdio", host="0.0.0.0", port=8080)
        with pytest.raises(AttributeError):
            config.transport = "http"

    def test_fields(self):
        config = ServerConfig(transport="http", host="127.0.0.1", port=9090)
        assert config.transport == "http"
        assert config.host == "127.0.0.1"
        assert config.port == 9090


class TestParseArgsDefaults:
    def test_defaults(self):
        config = parse_args([])
        assert config.transport == "stdio"
        assert config.host == "127.0.0.1"
        assert config.port == 8080

    def test_transport_http(self):
        config = parse_args(["--transport", "http"])
        assert config.transport == "http"

    def test_host_override(self):
        config = parse_args(["--host", "127.0.0.1"])
        assert config.host == "127.0.0.1"

    def test_port_override(self):
        config = parse_args(["--port", "9090"])
        assert config.port == 9090

    def test_all_flags(self):
        config = parse_args(["--transport", "http", "--host", "10.0.0.1", "--port", "3000"])
        assert config.transport == "http"
        assert config.host == "10.0.0.1"
        assert config.port == 3000


class TestParseArgsEnvVars:
    def test_transport_from_env(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_TRANSPORT", "http")
        config = parse_args([])
        assert config.transport == "http"

    def test_host_from_env(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_HOST", "192.168.1.1")
        config = parse_args([])
        assert config.host == "192.168.1.1"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_PORT", "3000")
        config = parse_args([])
        assert config.port == 3000

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_TRANSPORT", "http")
        config = parse_args(["--transport", "stdio"])
        assert config.transport == "stdio"

    def test_cli_port_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_KNOW_PORT", "3000")
        config = parse_args(["--port", "9090"])
        assert config.port == 9090


class TestParseArgsValidation:
    def test_invalid_transport(self):
        with pytest.raises(SystemExit):
            parse_args(["--transport", "websocket"])

    def test_port_zero(self):
        with pytest.raises(SystemExit):
            parse_args(["--port", "0"])

    def test_port_too_high(self):
        with pytest.raises(SystemExit):
            parse_args(["--port", "99999"])

    def test_port_negative(self):
        with pytest.raises(SystemExit):
            parse_args(["--port", "-1"])

    def test_port_non_numeric(self):
        with pytest.raises(SystemExit):
            parse_args(["--port", "abc"])


class TestMainWiring:
    def test_main_stdio_default(self):
        with patch("ansible_know.server.mcp") as mock_mcp, \
             patch("ansible_know.cli.parse_args") as mock_parse:
            mock_parse.return_value = ServerConfig(transport="stdio", host="0.0.0.0", port=8080)
            from ansible_know.server import main
            main()
            mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_main_http_passes_host_port(self):
        with patch("ansible_know.server.mcp") as mock_mcp, \
             patch("ansible_know.cli.parse_args") as mock_parse:
            mock_parse.return_value = ServerConfig(transport="http", host="10.0.0.1", port=9090)
            from ansible_know.server import main
            main()
            mock_mcp.run.assert_called_once_with(
                transport="http", host="10.0.0.1", port=9090,
            )
