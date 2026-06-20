# HTTP Streamable Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add HTTP streamable transport support so the server can run as a standalone HTTP service accessible by multiple remote clients.

**Architecture:** New `cli.py` Foundation module handles argument parsing with env var fallbacks, returns a frozen `ServerConfig` dataclass. `server.py:main()` delegates to it and passes config to `mcp.run()`. No changes to lifespan, state, or tool handlers — shared `ServerState` across all HTTP clients.

**Tech Stack:** Python stdlib (`argparse`, `dataclasses`, `os`), FastMCP `>=3.2,<4` (already supports `mcp.run(transport="http")`)

## Global Constraints

- Python `>=3.10` (project floor)
- FastMCP `>=3.2,<4` (no new dependencies)
- Foundation layer: zero imports from Domain, External Access, or Orchestration
- All new modules must define `__all__`
- Env var prefix: `ANSIBLE_KNOW_`
- Test runner: `pytest` with `asyncio_mode = "auto"`
- Linter: `ruff check src/ tests/`
- Integration tests: `@pytest.mark.integration`, skipped unless `--run-integration`

---

### Task 1: Create `cli.py` with `ServerConfig` and `parse_args`

**Files:**
- Create: `src/ansible_know/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing (stdlib only)
- Produces:
  - `ServerConfig(transport: str, host: str, port: int)` — frozen dataclass
  - `parse_args(argv: list[str] | None = None) -> ServerConfig` — CLI parser

- [ ] **Step 1: Write failing tests for `parse_args` defaults and CLI flags**

Create `tests/test_cli.py`:

```python
"""Tests for ansible_know.cli."""

from __future__ import annotations

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
        assert config.host == "0.0.0.0"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: `ModuleNotFoundError: No module named 'ansible_know.cli'`

- [ ] **Step 3: Implement `cli.py`**

Create `src/ansible_know/cli.py`:

```python
"""CLI argument parsing for transport configuration.

Foundation-layer module: stdlib only, no imports from Domain,
External Access, or Orchestration.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

__all__ = ["ServerConfig", "parse_args"]

_VALID_TRANSPORTS = ("stdio", "http")
_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8080


@dataclass(frozen=True)
class ServerConfig:
    """Resolved server transport configuration."""

    transport: str
    host: str
    port: int


def _port_in_range(value: str) -> int:
    """Validate port is an integer in 1-65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'")
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(
            f"port must be between 1 and 65535, got {port}"
        )
    return port


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    """Parse CLI arguments with environment variable fallbacks.

    Precedence: CLI flag > environment variable > default.
    """
    parser = argparse.ArgumentParser(
        prog="ansible-know-mcp",
        description="Ansible Know MCP Server",
    )
    parser.add_argument(
        "--transport",
        choices=_VALID_TRANSPORTS,
        default=os.environ.get("ANSIBLE_KNOW_TRANSPORT", "stdio"),
        help="Transport mode (default: stdio, env: ANSIBLE_KNOW_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ANSIBLE_KNOW_HOST", _DEFAULT_HOST),
        help=f"Bind address for HTTP transport (default: {_DEFAULT_HOST}, env: ANSIBLE_KNOW_HOST)",
    )
    parser.add_argument(
        "--port",
        type=_port_in_range,
        default=_port_in_range(os.environ.get("ANSIBLE_KNOW_PORT", str(_DEFAULT_PORT))),
        help=f"Listen port for HTTP transport (default: {_DEFAULT_PORT}, env: ANSIBLE_KNOW_PORT)",
    )

    args = parser.parse_args(argv)
    return ServerConfig(
        transport=args.transport,
        host=args.host,
        port=args.port,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all 17 tests PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check src/ansible_know/cli.py tests/test_cli.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/cli.py tests/test_cli.py
git commit -m "feat: add CLI module for transport configuration (#64)

ServerConfig frozen dataclass and parse_args() with argparse flags
(--transport, --host, --port) and ANSIBLE_KNOW_* env var fallbacks.
Foundation layer: stdlib only, no upper-layer imports.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Wire `main()` to use `parse_args` and update docs

**Files:**
- Modify: `src/ansible_know/server.py:936-938` (`main()` function)
- Modify: `README.md:87-93` (add HTTP transport section after "Any MCP client")
- Modify: `CLAUDE.md` (if architecture table needs update)

**Interfaces:**
- Consumes: `cli.parse_args() -> ServerConfig` (from Task 1)
- Produces: updated `main()` that passes transport config to `mcp.run()`

- [ ] **Step 1: Write a test for `main()` wiring**

Add to `tests/test_cli.py`:

```python
from unittest.mock import patch


class TestMainWiring:
    def test_main_stdio_default(self):
        with patch("ansible_know.server.mcp") as mock_mcp:
            from ansible_know.server import main
            with patch("ansible_know.cli.parse_args") as mock_parse:
                mock_parse.return_value = ServerConfig(transport="stdio", host="0.0.0.0", port=8080)
                with patch("ansible_know.server.parse_args", mock_parse):
                    main()
                mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_main_http_passes_host_port(self):
        with patch("ansible_know.server.mcp") as mock_mcp:
            from ansible_know.server import main
            with patch("ansible_know.cli.parse_args") as mock_parse:
                mock_parse.return_value = ServerConfig(transport="http", host="10.0.0.1", port=9090)
                with patch("ansible_know.server.parse_args", mock_parse):
                    main()
                mock_mcp.run.assert_called_once_with(
                    transport="http", host="10.0.0.1", port=9090,
                )
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py::TestMainWiring -v`
Expected: FAIL — `main()` currently calls `mcp.run()` with no args

- [ ] **Step 3: Update `main()` in `server.py`**

Replace lines 936-938 in `src/ansible_know/server.py`:

```python
def main():
    """Entry point for the MCP server."""
    from ansible_know.cli import parse_args

    config = parse_args()
    kwargs: dict[str, Any] = {}
    if config.transport == "http":
        kwargs.update(host=config.host, port=config.port)
    mcp.run(transport=config.transport, **kwargs)
```

- [ ] **Step 4: Run the wiring tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py::TestMainWiring -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `.venv/bin/pytest tests/ -v --ignore=tests/integration`
Expected: all tests PASS (no regressions — existing tests don't call `main()`)

- [ ] **Step 6: Add HTTP transport section to README.md**

After the "Any MCP client" section (after line 93), add:

```markdown
### HTTP Transport

Run as a standalone HTTP server for shared/remote access:

```bash
# HTTP on default port (8080)
ansible-know-mcp --transport http

# Custom host and port
ansible-know-mcp --transport http --host 10.0.0.1 --port 9090

# Via environment variables (useful for containers)
export ANSIBLE_KNOW_TRANSPORT=http
export ANSIBLE_KNOW_PORT=8080
ansible-know-mcp
```

Connect from any MCP client using the streamable HTTP URL:
`http://<host>:8080/mcp/`

> **Security**: HTTP mode has no built-in authentication. Deploy behind a
> reverse proxy with authentication/authorization, or use only on trusted
> networks.
```

- [ ] **Step 7: Run linter**

Run: `.venv/bin/ruff check src/ansible_know/server.py`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/ansible_know/server.py README.md tests/test_cli.py
git commit -m "feat: wire main() to CLI transport config and add docs (#64)

main() now delegates to cli.parse_args() and passes transport, host,
port to mcp.run(). HTTP transport section added to README with
security warning.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Integration smoke test for HTTP transport

**Files:**
- Create: `tests/integration/test_http_transport.py`

**Interfaces:**
- Consumes: `ansible_know.server.mcp` (the FastMCP server instance)
- Produces: integration test verifying HTTP transport starts and responds

- [ ] **Step 1: Write the integration smoke test**

Create `tests/integration/test_http_transport.py`:

```python
"""Smoke test for HTTP streamable transport.

Verifies the server starts in HTTP mode and responds to an MCP
initialize request. Requires ansible-core installed.

Run with: pytest --run-integration tests/integration/test_http_transport.py -v
"""

from __future__ import annotations

import asyncio
import socket
import time

import httpx
import pytest

pytestmark = pytest.mark.integration


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_http_transport_starts_and_responds():
    """Start the server in HTTP mode and send an MCP initialize request."""
    from ansible_know.server import mcp

    port = _find_free_port()
    server_task = None

    try:
        server_task = asyncio.create_task(
            asyncio.to_thread(mcp.run, transport="http", host="127.0.0.1", port=port)
        )
        await asyncio.sleep(2)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1.0"},
                    },
                },
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                timeout=10.0,
            )
            assert resp.status_code == 200
    finally:
        if server_task and not server_task.done():
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 2: Verify the test is skipped without `--run-integration`**

Run: `.venv/bin/pytest tests/integration/test_http_transport.py -v`
Expected: 1 test SKIPPED (needs `--run-integration`)

- [ ] **Step 3: Run linter**

Run: `.venv/bin/ruff check tests/integration/test_http_transport.py`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_http_transport.py
git commit -m "test: add HTTP transport integration smoke test (#64)

Starts the server in HTTP mode on a free port, sends an MCP
initialize request, verifies 200 response. Skipped unless
--run-integration is passed.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- [x] `cli.py` Foundation module with `ServerConfig` and `parse_args` — Task 1
- [x] `__all__` on `cli.py` — Task 1 Step 3
- [x] `server.py:main()` wiring — Task 2 Step 3
- [x] Env var fallbacks (`ANSIBLE_KNOW_TRANSPORT`, `_HOST`, `_PORT`) — Task 1 Step 3
- [x] CLI flag validation (transport whitelist, port range) — Task 1 Steps 1+3
- [x] Default host `0.0.0.0`, default port `8080` — Task 1 Step 3
- [x] No lifespan/state changes — confirmed, no tasks touch lifespan
- [x] Unit tests for `parse_args` — Task 1 Step 1
- [x] Integration smoke test — Task 3
- [x] README HTTP transport section with security warning — Task 2 Step 6
- [x] No new dependencies — confirmed, no `pyproject.toml` changes

**Placeholder scan:** No TBDs, TODOs, or vague steps found.

**Type consistency:** `ServerConfig` fields (`transport: str`, `host: str`, `port: int`) match between Task 1 definition and Task 2 consumption. `parse_args` signature matches across test imports and `main()` call.
