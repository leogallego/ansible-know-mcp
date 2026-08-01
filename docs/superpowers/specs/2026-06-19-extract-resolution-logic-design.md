# Extract Resolution Logic from server.py

**Date:** 2026-06-19
**Issue:** #66
**Violations fixed:** V-E1 (Error), V-L1 (Error), V-D7 (Warning), V-L2 (Warning)

## Problem

`server.py` (Orchestration layer) contains ~140 lines of business logic that
belong in the Domain layer:

1. `_resolve_module_doc()` and `_resolve_role_doc()` implement the
   local-then-Galaxy fallback resolution strategy (V-D7, V-L2).
2. `_try_galaxy_servers()` and `search_collections` create `GalaxyClient`
   directly, bypassing the Domain layer (V-E1, V-L1).
3. `_missing_collections` negative cache is domain state living in
   Orchestration.

## Solution

Create `src/ansible_know/resolution.py` as a Domain-layer module that owns
all Galaxy fallback and multi-server orchestration logic.

## New Module: `resolution.py`

### Layer

Domain (same level as `parser.py`, `skills.py`, `docs.py`,
`collection_manifest.py`).

### Public API

```python
__all__ = [
    "resolve_module_doc",
    "resolve_role_doc",
    "search_galaxy_collections",
    "clear_missing_namespace",
]
```

| Function | Signature | Description |
|----------|-----------|-------------|
| `resolve_module_doc` | `async (name: str, http_client: AsyncClient \| None, galaxy_servers: list[GalaxyServerConfig] \| None) -> tuple[dict, DocProvenance \| None]` | Local ansible-doc -> Galaxy docs-blob fallback |
| `resolve_role_doc` | `async (name: str, http_client: AsyncClient \| None, galaxy_servers: list[GalaxyServerConfig] \| None) -> dict` | Local ansible-doc -> Galaxy readme -> graceful degradation |
| `search_galaxy_collections` | `async (query: str, tags: str \| None, http_client: AsyncClient \| None, galaxy_servers: list[GalaxyServerConfig] \| None) -> dict` | Concurrent fan-out across Galaxy servers, merge + dedupe by namespace |
| `clear_missing_namespace` | `(namespace: str) -> None` | Remove namespace from negative cache |

**Naming rationale:** `search_galaxy_collections` disambiguates from
`GalaxyClient.search_collections()` (single-server) by describing *what* is
searched rather than *how* (the multi-server fan-out is an implementation
detail). The verb hierarchy across layers is: `get_*` (local/parser),
`fetch_*` (remote/galaxy), `resolve_*`/`search_*` (domain orchestration).

### Internal State

- `_missing_collections: set[str]` -- negative cache, moved as-is from
  server.py. Module-level mutable global (same pattern as other domain
  modules; encapsulation deferred to #68).

### Internal Helpers (not in `__all__`)

- `_try_galaxy_servers(servers, operation, http_client)` -- sequential
  multi-server retry, moved from server.py.
- `_select_http_client(http_client, server)` -- choose shared vs per-server
  client based on cert validation, moved from server.py.

### Dependencies

| Target | Layer | Import Style |
|--------|-------|-------------|
| `parser` | Domain | Lazy (avoid ansible-core at import time) |
| `galaxy` | External Access | Lazy (GalaxyClient) |
| `galaxy_config` | Foundation | Lazy (load_galaxy_servers fallback) |
| `errors` | Foundation | Top-level |
| `validation` | Foundation | Top-level (sanitize_error only) |

All dependencies flow downward per layer rules.

### Behavioral Contract

No behavioral changes -- the fallback logic, error handling, negative cache
semantics, and multi-server iteration are preserved exactly. This is a pure
structural refactor.

## Changes to `server.py`

### Removed (~140 lines)

- `_missing_collections` set and comment block
- `_try_galaxy_servers()` function
- `_resolve_module_doc()` function
- `_resolve_role_doc()` function
- `_select_http_client()` helper
- Direct `GalaxyClient` imports inside `search_collections`

### Added

- Lazy import of `resolution` module (matching existing pattern for `parser`,
  `skills`, etc.)

### Modified Tool Handlers

Each handler becomes thinner, delegating to `resolution.*`:

| Tool | Before | After |
|------|--------|-------|
| `get_module_doc` | `_resolve_module_doc(...)` | `resolution.resolve_module_doc(...)` |
| `get_role_doc` | `_resolve_role_doc(...)` | `resolution.resolve_role_doc(...)` |
| `generate_skill` | `_resolve_module_doc(...)` | `resolution.resolve_module_doc(...)` |
| `generate_role_skill` | `_resolve_role_doc(...)` | `resolution.resolve_role_doc(...)` |
| `search_collections` | ~40 lines inline GalaxyClient | `resolution.search_galaxy_collections(...)` |
| `ensure_collection` | `_missing_collections.discard(ns)` | `resolution.clear_missing_namespace(ns)` |

### Stays in `server.py`

- `_get_lifespan_resources()` -- extracts from `ctx.lifespan_context`,
  Orchestration concern.
- `_maybe_warn_upgrade()` -- upgrade warning logic, Orchestration concern.
- `_run_in_executor()` -- async wrapper, Orchestration concern.
- `_galaxy_servers` module-level variable -- set at lifespan, passed to
  resolution functions via `_get_lifespan_resources()`. Resolution functions
  that receive `galaxy_servers=None` fall back to `load_galaxy_servers()`
  directly (no dependency on server.py's lifespan state).
- All tool handler functions -- validation, error handling, progress
  reporting remain in Orchestration.

## Test Changes

### `tests/test_server.py`

- Update the `_clear_state` fixture to clear `resolution._missing_collections`
  instead of `server._missing_collections`.
- Update `TestLifespanHttpClient` patch target from
  `server._resolve_module_doc` to `resolution.resolve_module_doc`.
- **Move** the following test classes to `tests/test_resolution.py` (they
  directly test the resolution logic being extracted):
  - `TestResolveModuleDoc` -- tests `_resolve_module_doc` directly
  - `TestNegativeCache` -- tests `_missing_collections` cache behavior
- **Update** the following test classes to patch `resolution.*` instead of
  `server.*`:
  - `TestGalaxyDocsFallback` -- tests fallback behavior via tool handlers
    (stays in test_server.py, patches resolution module)
  - `TestSearchCollectionsTool` -- will call `resolution.search_galaxy_collections`
    (stays in test_server.py, patches resolution module)

### New: `tests/test_resolution.py`

Migrated tests from `test_server.py` plus new unit tests for the extracted
functions with mocked `parser` and `galaxy`:

- **From `TestResolveModuleDoc`** (migrated):
  - Local success (no Galaxy call made)
  - Local miss -> Galaxy fallback success
- **New resolution tests**:
  - Both local and Galaxy fail -> error propagation
  - Role resolution: local -> Galaxy readme -> graceful degradation
  - Role resolution: unavailable returns structured fallback dict
- **From `TestNegativeCache`** (migrated):
  - Negative cache hit -> skip local, go straight to Galaxy
  - Negative cache miss on different namespace -> try local first
  - `clear_missing_namespace` removes entry from cache
  - `ensure_collection` clears namespace from cache
- **New `search_galaxy_collections` tests**:
  - Concurrent fan-out across servers
  - Dedup by namespace
  - Sorted by download count
  - Partial server failures with partial success
  - All servers fail -> error

## Documentation Updates

### `docs/architecture/service-contracts.md`

Mark violations as fixed:
- V-E1: ~~Error~~ **Fixed** -- Galaxy access mediated through `resolution.py`
- V-L1: ~~Error~~ **Fixed** -- Orchestration no longer imports External
  Access directly
- V-D7: ~~Warning~~ **Fixed** -- Resolution logic moved to Domain layer
- V-L2: ~~Warning~~ **Fixed** -- Business logic no longer in Orchestration

Update layer diagram and interface tables to include `resolution.py`.

### `docs/architecture/adr/0004-galaxy-fallback-chain.md`

Update the "Negative: Complexity in Orchestration" consequence to note that
the fallback logic now lives in `resolution.py` (Domain layer).

### `CLAUDE.md`

Update the architecture table to include:
```
├── resolution.py          # local-then-Galaxy doc resolution + multi-server search
```

## Scope Boundaries

### In scope
- Move resolution logic to Domain layer
- Move search_collections Galaxy logic to Domain layer
- Move _missing_collections to Domain layer
- Update all callers in server.py
- Update tests
- Update architecture docs

### Out of scope (deferred)
- State encapsulation (#68) -- _missing_collections stays as module global
- GalaxyClientProtocol (#69) -- no interface changes to GalaxyClient
- _transform_to_ansible_doc_format move (#69) -- stays in galaxy.py
- parser.py decoupling from collections.py (#69)
