# ADR 0003: Module-Level Mutable State

## Status

Accepted (with known technical debt)

## Context

The server needs to maintain several pieces of mutable state across tool
calls within a single server session:

- **Version check result**: PyPI version info fetched at startup.
- **Galaxy server configuration**: parsed from `ansible.cfg` at startup.
- **Missing collection cache**: namespaces known to be absent locally,
  to skip repeated `ansible-doc` failures.
- **Galaxy API caches**: version lookups and docs-blob responses (TTL-based).
- **Collection install state**: which collections have been installed in the
  session temp directory, and their versions.
- **Documentation manifest cache**: fetched HTTP manifests.

The available patterns for managing this state were:

1. **Module-level globals** — simple, no framework needed. State lives in
   module-scoped variables, accessed by any function in the module.
2. **Dependency injection via class** — encapsulate state in a service class,
   pass instances explicitly.
3. **FastMCP lifespan context** — store state in the lifespan `yield` dict,
   access via `ctx.lifespan_context`.
4. **Application-level DI framework** — use `dependency-injector`, `inject`,
   or similar.

## Decision

Use module-level mutable state, with thread-safe access patterns where
concurrent access is expected.

This was chosen because:

- **Simplicity**: the server is a single-process, single-purpose MCP server.
  There is no need for multiple instances or test isolation beyond mocking.
- **No DI framework overhead**: the project has minimal dependencies. Adding
  a DI framework would increase complexity disproportionately.
- **FastMCP's lifespan context** already handles startup-initialized state
  (HTTP client, Galaxy servers). Module-level state handles runtime-accumulated
  state (caches, install tracking).

Thread safety measures applied:

- `galaxy.py`: `BoundedCache` instances with internal `threading.Lock`,
  LRU eviction, and 1hr TTL for `_version_cache` and `_blob_cache`.
  (Refactored from ad-hoc `OrderedDict`+`Lock`+TTL in PR #60.)
- `docs.py`: `BoundedCache` with internal `threading.Lock`, max 50 entries,
  1hr TTL for `_manifest_cache`. (Fixed from unprotected `dict` in PR #60.)
- `collections.py`: per-collection `threading.Lock` + global `_install_gate`
  serializes `ansible-galaxy` subprocess calls.
- `server.py`: `_missing_collections` uses `set` operations that are atomic
  under CPython's GIL. Documented as a negative cache (PR #60) but still
  lacks explicit synchronization.

## Consequences

### Positive

- Zero-dependency state management. No DI framework to learn or maintain.
- State is colocated with the functions that use it, making the code easy
  to read and follow.
- Thread-safe caches in `galaxy.py` and `collections.py` correctly handle
  the `_run_in_executor()` threading model.

### Negative

- **Testability**: module-level state requires `monkeypatch` or explicit
  `clear_cache()` calls between tests. Existing tests already do this,
  but it is fragile. (`BoundedCache.clear()` makes this cleaner than
  manual dict/lock manipulation.)
- **Thread safety gap**: `server.py` `_missing_collections` relies on
  CPython GIL atomicity, which is an implementation detail, not a
  language guarantee. (The `docs.py` thread-safety gap was fixed in
  PR #60 via `BoundedCache`.)
- **No reset mechanism**: there is no way to reset the server's state
  without restarting the process. This makes it impossible to run multiple
  isolated test scenarios in the same process.
- **State scattering**: mutable state is spread across 4 modules, making
  it hard to reason about the full state of the server at any point in time.
  (`BoundedCache` provides a consistent abstraction but state is still
  scattered across modules.)
- **Singleton coupling**: functions implicitly depend on module-level state,
  making their signatures incomplete — the function's behavior depends on
  hidden global state, not just its arguments.

### Known Technical Debt

1. ~~`docs.py` `_manifest_cache` needs an `asyncio.Lock`.~~ **Fixed in PR #60**
   — now uses `BoundedCache` with internal `threading.Lock`.
2. `server.py` `_missing_collections` should use explicit synchronization.
3. All module-level state should eventually be encapsulated in a
   `ServerState` or `SessionContext` class that is:
   - Created at lifespan start
   - Passed through the lifespan context
   - Accessible to tool handlers via `Context`
   - Resettable for testing

### Partial Mitigation: BoundedCache (PR #60)

PR #60 introduced `cache.py` with a shared `BoundedCache[K, V]` class that
replaces the ad-hoc `OrderedDict` + `threading.Lock` + TTL patterns in
`galaxy.py` (~40 lines removed) and gives `docs.py` thread-safety it
previously lacked. This addresses the most dangerous technical debt item
(V-S1) and standardizes the caching pattern, but the fundamental decision
to use module-level state remains unchanged.

### Future Considerations

- When HTTP/SSE streaming is added, the server may need to handle multiple
  concurrent sessions. Module-level state is per-process, not per-session.
  This would require moving to session-scoped state management.
- Consider a `ServerState` dataclass that holds all caches and runtime state,
  initialized in the lifespan and stored in `ctx.lifespan_context`.
