# HTTP Streamable Transport Design

**Issue**: [#64](https://github.com/leogallego/ansible-know-mcp/issues/64)
**Date**: 2026-06-20
**Status**: Draft

## Goal

Add HTTP streamable transport to ansible-know-mcp so the server can be
deployed as a standalone HTTP service for shared/remote access by multiple
clients (IDEs, agents, CI pipelines).

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| CLI interface | Dedicated `cli.py` module | Clean separation, room to grow |
| Transport selection | argparse flags + env var fallbacks | Both CLI and container-friendly |
| Session isolation | Shared `ServerState` (phase 1) | Simpler; per-session tracked in #78 |
| Default host | `0.0.0.0` | Remote access is the primary HTTP use case |
| Default port | `8080` | Common non-privileged HTTP port |
| Auth | None (phase 1) | Deployment layer handles access control |
| PyPI check | Once at startup, warn first client | Keep current behavior; periodic tracked in #79 |

## Architecture

### New file: `src/ansible_know/cli.py` (Foundation layer)

No imports from Domain, External Access, or Orchestration. Returns a plain
dataclass with resolved configuration.

```python
@dataclass(frozen=True)
class ServerConfig:
    transport: str   # "stdio" or "http"
    host: str        # only relevant for http
    port: int        # only relevant for http
```

Exports:
- `ServerConfig` — frozen dataclass with validated transport config
- `parse_args(argv: list[str] | None = None) -> ServerConfig` — argparse
  with env var defaults, validates transport and port

### Modified: `server.py:main()`

Changes from `mcp.run()` to:

```python
def main():
    from ansible_know.cli import parse_args
    config = parse_args()
    kwargs: dict[str, Any] = {}
    if config.transport == "http":
        kwargs.update(host=config.host, port=config.port)
    mcp.run(transport=config.transport, **kwargs)
```

### Layer placement

```
Foundation:     cli.py (new) — arg parsing, env vars, validation
Orchestration:  server.py:main() — delegates to cli, calls mcp.run()
Transport:      FastMCP framework — handles HTTP/stdio protocol
```

`cli.py` sits in Foundation because it has zero dependencies on upper layers.
It only uses stdlib (`argparse`, `os`, `dataclasses`, `sys`).

## CLI Interface

### Flags and env vars

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--transport` | `ANSIBLE_KNOW_TRANSPORT` | `stdio` | Transport mode: `stdio` or `http` |
| `--host` | `ANSIBLE_KNOW_HOST` | `0.0.0.0` | Bind address (HTTP only) |
| `--port` | `ANSIBLE_KNOW_PORT` | `8080` | Listen port (HTTP only) |

**Precedence**: CLI flag > env var > default.

### Validation

- `transport` must be `stdio` or `http`. Any other value exits with error.
- `port` must be an integer in range 1-65535. Out-of-range exits with error.
- `host` and `port` are accepted but ignored for stdio transport (no warning).

### Usage examples

```bash
# stdio (default, unchanged from current behavior)
ansible-know-mcp

# HTTP on default host/port
ansible-know-mcp --transport http

# HTTP on custom port
ansible-know-mcp --transport http --port 9090

# Container deployment via env vars
ANSIBLE_KNOW_TRANSPORT=http ANSIBLE_KNOW_PORT=8080 ansible-know-mcp
```

## Lifespan and State

No changes to the lifespan or `ServerState`. The existing lifespan works
identically for both transports:

1. `app_lifespan` creates `CollectionManager`, loads Galaxy servers,
   creates httpx client, checks PyPI
2. Yields `LifespanContext` with `http_client` and `state`
3. All tool handlers access state via `_get_state(ctx)`

For HTTP transport, the lifespan runs once when the server starts and
remains active until shutdown. All connected clients share the same
`ServerState` instance.

`_maybe_warn_upgrade` sets `upgrade_warned = True` on the shared state,
so only the first client to call a tool sees the warning. This is
acceptable for phase 1 (see #79 for periodic re-check).

## Testing

### Unit tests (`tests/test_cli.py`)

- Default values: `parse_args([])` returns `ServerConfig("stdio", "0.0.0.0", 8080)`
- CLI flags override defaults: `parse_args(["--transport", "http", "--port", "9090"])`
- Env var fallback: set `ANSIBLE_KNOW_TRANSPORT=http`, call `parse_args([])`
- CLI takes precedence over env: set env to `http`, pass `--transport stdio`
- Invalid transport: `parse_args(["--transport", "websocket"])` exits with error
- Invalid port: `parse_args(["--port", "0"])` and `--port 99999` exit with error
- Non-numeric port: `parse_args(["--port", "abc"])` exits with error

### Integration test (`tests/integration/test_http_transport.py`)

- Smoke test: start server with `transport="http"`, verify it responds to
  MCP initialize request, shut down cleanly
- Mark with `@pytest.mark.integration` (skipped by default)

## Documentation updates

### README.md

Add "HTTP Transport" section under existing "Registration" section:

```markdown
## HTTP Transport

Run as a standalone HTTP server for shared/remote access:

\`\`\`bash
ansible-know-mcp --transport http --port 8080
\`\`\`

Or via environment variables (useful for containers):

\`\`\`bash
export ANSIBLE_KNOW_TRANSPORT=http
export ANSIBLE_KNOW_PORT=8080
ansible-know-mcp
\`\`\`

Connect from any MCP client using the HTTP streamable transport URL:
`http://<host>:8080/mcp/`
\`\`\`

### pyproject.toml

No dependency changes needed. FastMCP `>=3.2,<4` already supports
`mcp.run(transport="http")`.

## Out of scope

- Authentication / API keys (deploy behind reverse proxy for now)
- TLS termination (handled by reverse proxy)
- Per-session state isolation (tracked in #78)
- Periodic PyPI version re-check (tracked in #79)
- WebSocket transport (deprecated in MCP spec)
- Config file for transport settings
- `stateless_http` mode (FastMCP option for horizontal scaling — future work)

## Follow-up issues

- #78 — Per-session state isolation for HTTP transport
- #79 — Periodic PyPI version check for long-lived HTTP servers
