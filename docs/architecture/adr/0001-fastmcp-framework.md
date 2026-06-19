# ADR 0001: FastMCP as MCP Server Framework

## Status

Accepted

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
