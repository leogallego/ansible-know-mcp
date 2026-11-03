# ADR 0001: FastMCP as MCP Server Framework

## Status

Accepted

## Date

2026-06-19

## Context

The server needs to expose tools, resources, and prompts via the Model Context
Protocol (MCP). At the time of implementation, the available options were:

1. **Raw `mcp` library** (`modelcontextprotocol/python-sdk`) — the reference
   Python SDK. Provides low-level protocol handling but requires manual
   registration of tools, resources, and prompts with boilerplate.

2. **FastMCP** (`jlowin/fastmcp`) — a higher-level framework built on top of
   the `mcp` library. Provides decorator-based registration (`@mcp.tool`,
   `@mcp.resource`, `@mcp.prompt`), automatic input validation via type
   annotations, `Context` injection for progress/warnings, and lifespan
   management.

3. **Custom implementation** — building directly on JSON-RPC transport.
   Maximum control but significant protocol-level work.

The server needed to support 12 tools, 6 resources, and 4 prompts with
consistent input validation, progress reporting, and lifecycle management.

## Decision

Use FastMCP as the MCP server framework.

FastMCP was chosen because:

- **Decorator-based registration** eliminates boilerplate for 22 handlers.
- **`Annotated` type hints** provide both MCP schema generation and
  documentation in a single declaration.
- **`ToolAnnotations`** support (`readOnlyHint`, `idempotentHint`,
  `destructiveHint`) enables MCP-aware clients to make safety decisions.
- **Lifespan management** via `@lifespan` provides clean startup/shutdown
  for the shared `httpx.AsyncClient` and Galaxy server configuration.
- **`Context` injection** supports progress reporting and upgrade warnings.
- **Transport abstraction** — FastMCP handles stdio now and can support
  HTTP/SSE streaming later without application code changes.

## Consequences

### Positive

- Minimal boilerplate: each tool is a decorated async function with typed
  parameters. Adding a new tool requires ~20 lines, not ~100.
- Transport-agnostic: switching from stdio to HTTP/SSE streaming requires
  only configuration changes, not code changes.
- Built-in input schema generation from Python type annotations.
- Active community and upstream maintenance.

### Negative

- **Framework coupling**: the entire Orchestration layer depends on FastMCP
  decorators and the `Context` type. Switching frameworks would require
  rewriting all 22 handler registrations.
- **Lifespan context is untyped**: FastMCP's lifespan `yield` passes an
  untyped dict, which the application accesses by string keys. This is a
  source of potential runtime errors (see V-T1 in service-contracts.md).
- **Opaque transport**: the application has no control over connection
  management, backpressure, or protocol-level details. If future MCP
  features require protocol-level access, FastMCP may not expose them.
- **Implicit async model**: FastMCP runs tool handlers on its event loop.
  Blocking calls must be explicitly dispatched to executors — the framework
  does not detect or warn about blocking calls.

### Future Considerations

- If HTTP/SSE streaming is added, verify that FastMCP's transport layer
  handles connection lifecycle, keepalive, and backpressure correctly.
- If the server needs to run behind a reverse proxy or load balancer,
  verify FastMCP's HTTP transport supports health checks and graceful
  shutdown.
- Consider defining a `LifespanContext` TypedDict to type the lifespan
  dict and prevent key typos.

## Implementation Notes

- `server.py` — all tool/resource/prompt registrations via FastMCP decorators
- `server.py:lifespan()` — startup/shutdown: httpx client, Galaxy server
  config, PyPI version check
- `server.py:Context` — injected into tool handlers for progress/warnings
- Transport: stdio via `mcp.run()`, HTTP/SSE supported by FastMCP but
  not yet configured

## Related Decisions

- [ADR-0003](0003-module-level-state.md) — state management choices
  influenced by FastMCP's lifespan context model
- [ADR-0008](0008-three-layer-distribution.md) — Layer 3 (remote service)
  depends on FastMCP's HTTP/SSE transport

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-19 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Initial decision |
| 2026-06-26 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Added Implementation Notes, Related Decisions, Revision History |

## Update (2026-11-03): FastMCP 4 / MCP 2026-07-28 migration

Bumped to `fastmcp>=4.1.4,<4.2` with an explicit `mcp>=2.2` floor (guard for
CVE-2026-59951; the transitive resolve is mcp >= 2.3). This is our adoption of
the MCP **2026-07-28** stateless protocol revision.

Consequence for application state: 2026-07-28 returns a fresh `ctx.session_id`
per call, so it is no longer a stable cross-call key. Per-session state is now
keyed on a **server-minted `collection_handle`** that `ensure_collection`
returns and stateful tools thread back. Transport is detected **per request and
fail-closed** via `fastmcp.server.dependencies.get_http_request` (a raised
`RuntimeError` / no HTTP request in scope ⇒ proven-local; a returned request or
any other error ⇒ treated as remote): a proven-local call with no handle gets a
process-wide `DEFAULT_HANDLE`; a remote or unknown-transport call requires an
explicit handle. Explicit handles resolve via `get_existing` (no create-on-miss,
closing a CWE-400 LRU-thrash vector). The private `ctx.session._exit_stack`
cleanup hook was removed; cleanup relies on the existing TTL/LRU + periodic sweep.

Transport note: FastMCP 4's canonical streamable-HTTP mount path is `/mcp` (no
trailing slash); `/mcp/` now 307-redirects. Separately, the "outdated version"
notice moved off the deprecated protocol Logging channel (`ctx.warning`) to
stderr via `logger.warning`.
