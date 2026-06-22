# Service Contracts

This document defines the formal contracts between architecture layers in the
Ansible Know MCP Server. It describes how each layer *should* interact and
flags violations found in the current codebase.

Based on the architecture review in `docs/research/architecture-report.md`
(2026-06-16) and updated after PR #60 (2026-06-17).

## Layer Architecture

The server follows a 5-layer pipeline architecture adapted for MCP:

```
┌─────────────────────────────────────────────────────────┐
│  Transport         FastMCP framework (MCP protocol)     │
│                    Handled by fastmcp library            │
├─────────────────────────────────────────────────────────┤
│  Orchestration     server.py                            │
│                    Tool handlers, validation, fallback   │
│                    logic, lifespan management            │
├─────────────────────────────────────────────────────────┤
│  Domain            parser.py, skills.py,                │
│                    collection_manifest.py, docs.py,      │
│                    resolution.py                         │
├─────────────────────────────────────────────────────────┤
│  External Access   galaxy.py, collections.py,           │
│                    readme_parser.py                      │
├─────────────────────────────────────────────────────────┤
│  Foundation        async_utils.py, cache.py, config.py,  │
│                    galaxy_config.py, state.py,            │
│                    validation.py, errors.py, types.py     │
└─────────────────────────────────────────────────────────┘
```

**Data flows downward.** Each layer may depend on layers below it but never
on layers above. The Transport layer is owned by the FastMCP framework; the
Orchestration layer is the application's primary entry point.

---

## Layer 1: Transport → Orchestration

### Contract

The Transport layer (FastMCP) calls into Orchestration (`server.py`) via
decorated tool, resource, and prompt handler functions. FastMCP manages:

- MCP protocol framing (stdio currently; HTTP/SSE in the future)
- Tool/resource/prompt registration via decorators
- `Context` object injection for progress reporting and warnings
- Lifespan management (startup/shutdown via `@lifespan`)
- Input deserialization and output serialization (JSON)

### Interface Definition

| Element | File | Registration |
|---------|------|--------------|
| 12 tool handlers | `server.py` | `@mcp.tool(annotations=ToolAnnotations(...))` |
| 6 resource handlers | `server.py` | `@mcp.resource(uri, ...)` |
| 4 prompt handlers | `server.py` | `@mcp.prompt` |
| Lifespan hook | `server.py` | `@lifespan` decorator on `app_lifespan()` |

### Types Crossing This Boundary

| Direction | Type | Source |
|-----------|------|--------|
| In (from Transport) | `Context` | `fastmcp.Context` |
| In (from Transport) | Tool parameters | `Annotated[str, ...]`, `Annotated[str \| None, ...]` |
| Out (to Transport) | Tool results | `dict[str, Any]`, `str`, `list[dict]` |
| Shared (lifespan) | `httpx.AsyncClient` | Via `LifespanContext["http_client"]` |
| Shared (lifespan) | `SharedState` | Via `LifespanContext["shared"]` — process-wide galaxy servers + version info |
| Shared (lifespan) | `SessionManager` | Via `LifespanContext["sessions"]` — per-session state factory |

### Concurrency Contract

- Tool handlers are `async` functions running on the FastMCP event loop.
- Blocking work (subprocess calls, file I/O) MUST be dispatched via
  `_run_in_executor()` to avoid blocking the event loop.
- The lifespan context is a `LifespanContext` TypedDict shared across all
  concurrent tool calls. Per-session state is obtained via
  `await sessions.get_or_create(ctx.session_id)`.

### Violations

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| ~~V-T1~~ | ~~Warning~~ | ~~Lifespan context is an untyped `dict[str, Any]` accessed by string keys.~~ **Fixed in PR #87** — `LifespanContext` TypedDict with typed keys (`http_client`, `shared`, `sessions`). | ~~`server.py:103-108`~~ → `state.py` |
| V-T2 | Warning | `_run_in_executor()` uses deprecated `asyncio.get_event_loop()`. Should use `asyncio.get_running_loop()` — the function is only called from async context. | `server.py:126-129` |
| V-T3 | Info | Tool return types are `dict[str, Any]` rather than typed dicts. FastMCP serializes to JSON regardless, but typed returns would improve static analysis within the server code. | All tool handlers |

---

## Layer 2: Orchestration → Domain

### Contract

The Orchestration layer (`server.py`) calls Domain functions to perform
business logic. Domain modules are imported lazily to avoid loading
`ansible-core` at startup.

### Interface Definition

| Domain Module | Functions Called | File |
|---------------|----------------|------|
| `parser` | `search_modules()`, `get_module_doc()`, `get_role_doc()`, `list_roles()`, `extract_module_metadata()`, `extract_role_metadata()` | `parser.py` |
| `skills` | `render_skill()`, `write_skill_package()`, `render_role_skill()`, `write_role_skill_package()`, `_module_to_skill_name()` | `skills.py` |
| `collection_manifest` | `generate_manifest()`, `load_cached_manifest()` | `collection_manifest.py` |
| `docs` | `search_docs()` | `docs.py` |
| `collections` | `ensure_collection()`, `list_installed()` | `collections.py` |
| `resolution` | `resolve_module_doc()`, `resolve_role_doc()`, `search_galaxy_collections()`, `clear_missing_namespace()` | `resolution.py` |

### Types Crossing This Boundary

| Direction | Type | Definition |
|-----------|------|------------|
| Down | `str` (module_name, keyword, namespace) | Primitives, validated by Orchestration |
| Up | `ModuleMetadata` | `types.py:8-15` — TypedDict |
| Up | `RoleMetadata` | `types.py:18-23` — TypedDict |
| Up | `EnsureCollectionResult` | `types.py:43-49` — TypedDict (added in PR #60) |
| Up | `dict[str, str]` | Search results (module FQCN → description) |
| Up | `dict[str, Any]` | Raw ansible-doc JSON, manifest dicts |
| Up | Exceptions | `AnsibleDocError`, `CollectionNotFoundError`, `CollectionInstallError` |

### Threading Contract

- `parser.py` functions are **synchronous** (subprocess calls). The
  Orchestration layer MUST wrap them in `_run_in_executor()`.
- `collections.py` functions are **synchronous** and internally thread-safe
  (per-collection locks + a global install gate).
- `docs.py` `search_docs()` is **async** and can be awaited directly.
- `skills.py` functions are **synchronous** (file I/O + Jinja2 rendering).
  MUST be wrapped in `_run_in_executor()`.
- `collection_manifest.py` functions are **synchronous** (file I/O + JSON).
  MUST be wrapped in `_run_in_executor()`.

### Violations

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| V-D1 | Warning | `server.py` calls `skills._module_to_skill_name()` — a private function (leading underscore). This crosses the public API boundary. Should be exposed as a public function or the skill name derivation should be the caller's responsibility. | `server.py:753`, `server.py:880` |
| V-D2 | Warning | `parser.py` does not define `__all__`. All module-level functions are implicitly public, including internal helpers like `_get_module_name()` and `_run_ansible_doc()`. | `parser.py` |
| V-D3 | Warning | `skills.py` does not define `__all__`. | `skills.py` |
| V-D4 | Warning | `collection_manifest.py` does not define `__all__`. | `collection_manifest.py` |
| V-D5 | Warning | `docs.py` does not define `__all__`. | `docs.py` |
| V-D6 | Info | Several Domain functions return `dict[str, Any]` where TypedDicts exist. Partially addressed in PR #60 (`EnsureCollectionResult` now typed on `collections.ensure_collection()`, plus `ErrorResponse`, `SkillEntry`, `CollectionSearchResult` TypedDicts added) but not all return sites use them yet. | `parser.py:214-223`, `server.py:195-240` |
| ~~V-D7~~ | ~~Warning~~ | ~~`_resolve_module_doc()` and `_resolve_role_doc()` in `server.py` contain significant business logic (Galaxy fallback, missing-collection caching). This logic belongs in the Domain layer, not Orchestration. The Orchestration layer should delegate to a domain-level resolution function.~~ **Fixed in PR #66** — Resolution logic moved to `resolution.py`. | ~~`server.py:195-301`~~ |
| V-D8 | Warning | `collection_manifest.generate_manifest()` writes to disk as a side effect. A Domain function that both generates and persists violates separation of concerns — generation and persistence should be separate operations. | `collection_manifest.py:113-117` |

---

## Layer 3: Domain → External Access

### Contract

Domain modules call External Access modules to interact with systems outside
the process boundary: the Galaxy REST API, the `ansible-doc` CLI, the
`ansible-galaxy` CLI, and Galaxy README HTML.

### Interface Definition

| External Access Module | Consumers | File |
|------------------------|-----------|------|
| `galaxy.py` (`GalaxyClient`) | `resolution.py` (via `GalaxyDocClient` Protocol) | `galaxy.py` |
| `collections.py` | `server.py`, `resolution.py` | `collections.py` |
| `readme_parser.py` | `galaxy.py` | `readme_parser.py` |

`GalaxyClient` is the primary class in the codebase. It acts as an async HTTP
client wrapper for the Galaxy v3 REST API. (`BoundedCache` in the Foundation
layer and `_ReadmeParser` in `readme_parser.py` are the other classes.)

### Types Crossing This Boundary

| Direction | Type | Definition |
|-----------|------|------------|
| Down | `str` (FQCN, namespace, version) | Primitives |
| Down | `GalaxyServerConfig` | `galaxy_config.py:24-35` — frozen dataclass |
| Down | `httpx.AsyncClient` (optional) | Shared lifespan client |
| Up | `dict[str, Any]` | ansible-doc JSON, Galaxy API responses |
| Up | `DocProvenance` | `types.py:26-34` — TypedDict |
| Up | `tuple[dict, DocProvenance]` | `GalaxyClient.fetch_module_doc()`, `fetch_role_doc()` |
| Up | Exceptions | `GalaxyError`, `AnsibleDocError`, `CollectionNotFoundError`, `CollectionInstallError` |

### Concurrency Contract

- `GalaxyClient` methods are **async**. They use the shared `httpx.AsyncClient`
  when the server validates certs, or create an owned client otherwise.
- `GalaxyClient` uses module-level `BoundedCache` instances
  (`_version_cache`, `_blob_cache`) which are internally thread-safe
  (lock-protected, LRU-bounded, TTL-expiring).
- `collections.py` uses per-collection locks and a global `_install_gate`
  to serialize `ansible-galaxy` subprocess calls.

### Violations

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| ~~V-E1~~ | ~~Error~~ | ~~`server.py` calls `GalaxyClient` directly in `search_collections()` and `_try_galaxy_servers()`, bypassing the Domain layer entirely. Galaxy access should be mediated through a domain-level service.~~ **Fixed in PR #66** — Galaxy access is now mediated through `resolution.py`. | ~~`server.py:168-192`, `server.py:480-486`~~ |
| ~~V-E2~~ | ~~Warning~~ | ~~`GalaxyClient` has no abstract base class or Protocol.~~ **Fixed in PR #66:** `GalaxyDocClient` Protocol and `GalaxyClientFactory` Protocol defined in `types.py`. `resolution.py` depends on the Protocol, not the concrete class. `GalaxyClient` satisfies the Protocol structurally. | ~~`galaxy.py:111`~~ → `types.py` |
| ~~V-E3~~ | ~~Warning~~ | ~~`GalaxyClient._transform_to_ansible_doc_format()` is a static method that converts Galaxy format to ansible-doc format. This is a data transformation that belongs in the Domain layer (parser), not the External Access layer.~~ **Fixed in PR #69** — Moved to `parser.transform_galaxy_to_ansible_doc_format()`. `galaxy.py` lazy-imports it. | ~~`galaxy.py:381-417`~~ → `parser.py` |
| V-E4 | Warning | `collections.py` module-level mutable state (`_tmp_dir`, `_installed`, `_install_locks`) makes the module impossible to test in isolation without monkeypatching globals. Should be encapsulated in a class. | `collections.py:26-29` |
| V-E5 | Info | `galaxy.py` uses `asyncio.Semaphore` for enrichment throttling but creates it lazily with event-loop detection (`_get_enrichment_semaphore`). This is fragile if the event loop changes. | `galaxy.py:40-46` |

---

## Layer 4: External Access → Foundation

### Contract

All layers depend on Foundation modules for configuration, validation,
error types, and shared type definitions. Foundation modules have no
dependencies on upper layers.

### Interface Definition

| Foundation Module | Purpose | File |
|-------------------|---------|------|
| `async_utils.py` | `run_in_executor` — blocking-to-async bridge | `async_utils.py` |
| `cache.py` | Thread-safe bounded LRU cache with TTL | `cache.py` |
| `config.py` | Paths, constants, env var defaults, doc sources | `config.py` |
| `galaxy_config.py` | Galaxy server config from `ansible.cfg` | `galaxy_config.py` |
| `validation.py` | Input validation, error sanitization, response truncation | `validation.py` |
| `errors.py` | Exception hierarchy and error helpers | `errors.py` |
| `types.py` | `TypedDict` definitions, `GalaxyDocClient` Protocol | `types.py` |

### Types Exported

| Type | Kind | File |
|------|------|------|
| `BoundedCache[K, V]` | Generic class | `cache.py:19` |
| `ModuleMetadata` | `TypedDict` | `types.py:8-15` |
| `RoleMetadata` | `TypedDict` | `types.py:18-23` |
| `DocProvenance` | `TypedDict` (partial) | `types.py:26-35` |
| `VersionInfo` | `TypedDict` | `types.py:43-49` |
| `ErrorResponse` | `TypedDict` | `types.py:51-53` |
| `EnsureCollectionResult` | `TypedDict` | `types.py:43-49` |
| `SkillEntry` | `TypedDict` | `types.py:52-57` |
| `CollectionInfo` | `TypedDict` (partial) | `types.py:74-82` |
| `CollectionSearchResult` | `TypedDict` | `types.py:85-90` |
| `GalaxyDocClient` | `Protocol` | `types.py:94-120` |
| `GalaxyClientFactory` | `Protocol` | `types.py:123-131` |
| `ServerState` | `@dataclass` | `state.py` |
| `SharedState` | `@dataclass` | `state.py` |
| `SessionManager` | class | `state.py` |
| `LifespanContext` | `TypedDict` | `state.py` |
| `GalaxyServerConfig` | `@dataclass(frozen=True)` | `galaxy_config.py:24-35` |
| `AnsibleKnowError` | Exception base | `errors.py:6-7` |
| `AnsibleDocError` | Exception | `errors.py:10-11` |
| `CollectionNotFoundError` | Exception | `errors.py:14-15` |
| `GalaxyError` | Exception | `errors.py:18-19` |
| `CollectionInstallError` | Exception | `errors.py:22-23` |
| `ValidationError` | Exception | `errors.py:26-27` |

### Error Hierarchy

```
AnsibleKnowError
├── AnsibleDocError
│   └── CollectionNotFoundError
├── GalaxyError
├── CollectionInstallError
└── ValidationError
```

### Violations

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| V-F1 | Warning | `config.py` evaluates `Path.cwd()` at import time for `SKILLS_DIR`. This captures the working directory at the moment the module is first imported, which may differ from the intended runtime directory. Should be a function or lazy property. | `config.py:14-17` |
| V-F2 | Info | `types.py` `TypedDict` definitions use `dict[str, Any]` for nested structures (e.g., `params: list[dict[str, Any]]`). More specific nested types would improve type safety. | `types.py:14`, `types.py:22` |
| V-F3 | Info | `galaxy_config.py` `GalaxyServerConfig` is a frozen dataclass, which is correct for an immutable configuration value object. No issues. | `galaxy_config.py:24-35` |

---

## Cross-Cutting Concerns

### State Management

State is split into process-wide and per-session layers (PR #87):

- **`SharedState`** (process-wide): `galaxy_servers`, `version_info`. Created
  once in lifespan. `version_info` updated by periodic PyPI check.
- **`ServerState`** (per-session): `collection_manager`, `missing_collections`,
  `upgrade_warned`. Created lazily by `SessionManager.get_or_create()`.
- **`SessionManager`**: manages session lifecycle with `asyncio.Lock`.
  Accepts a `collection_factory` callable to avoid Foundation→External Access
  imports. Provides `remove_session()` for cleanup.

Module-level references `_shared_state` and `_session_manager` in `server.py`
are write-once at lifespan startup for resource handlers (which don't receive
`Context`).

Additional module-level caches:

| Module | State Variables | Thread Safety |
|--------|----------------|---------------|
| `galaxy.py` | `_version_cache`, `_blob_cache` | Thread-safe via `BoundedCache` (internal `threading.Lock`, LRU eviction, 1hr TTL) |
| `docs.py` | `_manifest_cache` | Thread-safe via `BoundedCache` (max 50 entries, 1hr TTL) — **fixed in PR #60** |

#### Violations

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| ~~V-S1~~ | ~~Error~~ | ~~`docs.py` `_manifest_cache` is not thread-safe.~~ **Fixed in PR #60** — now uses `BoundedCache` with thread-safe locking and bounded size. | `docs.py:20` |
| ~~V-S2~~ | ~~Warning~~ | ~~`server.py` `_missing_collections` is a module-level `set` mutated from async tool handlers without synchronization.~~ **Mitigated in PR #87** — `missing_collections` is now per-session in `ServerState`. Async-only access within a session is safe (no preemption at `set.add()`/`set.discard()`). | ~~`server.py:165-169`~~ → `state.py:ServerState` |
| ~~V-S3~~ | ~~Warning~~ | ~~Module-level mutable state makes the server difficult to test, reset, or run multiple instances.~~ **Partially resolved in PR #87** — State split into `SharedState` (process-wide) and `ServerState` (per-session) via `SessionManager`. `collections.py` still uses per-instance state (`CollectionManager` class) but is now created per-session. | ~~Multiple modules~~ → `state.py`, `server.py` |

### Validation

Input validation is centralized in `validation.py` and called from the
Orchestration layer before any Domain or External Access calls. This is
correct — validation belongs at the system boundary.

The validation functions raise `ValidationError` on invalid input, which
tool handlers catch and convert to `{"error": str}` responses.

### Error Handling Pattern

Tool handlers follow a consistent pattern:
1. Validate input → return `{"error": str}` on `ValidationError`
2. Call domain/external functions in `try`/`except`
3. Catch specific exceptions, sanitize with `sanitize_error()`, return `{"error": str}`
4. Never raise exceptions to the Transport layer

This is a correct MCP pattern — tools should return error dicts, not raise.

---

## Dependency Rules Summary

### Allowed Dependencies

```
Transport    → (framework-managed, no application code)
Orchestration → Domain, External Access, Foundation
Domain       → Foundation (only)
External     → Foundation (only)
Foundation   → (no internal dependencies)
```

### Violations of Dependency Rules

| ID | Severity | Description |
|----|----------|-------------|
| ~~V-L1~~ | ~~Error~~ | ~~Orchestration (`server.py`) imports and calls External Access (`galaxy.py:GalaxyClient`) directly, bypassing the Domain layer.~~ **Fixed in PR #66** — Orchestration delegates to `resolution.py` (Domain). | 
| ~~V-L2~~ | ~~Warning~~ | ~~Orchestration (`server.py`) contains domain logic in `_resolve_module_doc()` and `_resolve_role_doc()` — these are domain-level resolution strategies, not orchestration.~~ **Fixed in PR #66** — Moved to `resolution.py`. |
| V-L3 | Info | External Access (`galaxy.py`) lazy-imports Domain modules (`readme_parser.py` for `fetch_role_doc()`, `parser.py` for `transform_galaxy_to_ansible_doc_format()` in `fetch_module_doc()`). Both are pure data transformers — acceptable cross-layer calls that avoid duplicating domain logic. |
| ~~V-L4~~ | ~~Warning~~ | ~~Domain (`parser.py`) imports External Access (`collections.py`) at the top level to get the collections path.~~ **Fixed in PR #69** — `parser.py` accepts `collections_path` as a parameter; callers (server.py, resolution.py) inject it from `collections.get_collections_path()`. |

---

## Recommended Remediations

### Priority 1 (Error-level violations)

1. ~~**V-S1**: Add `asyncio.Lock` to `docs.py` `_manifest_cache` access.~~
   **Fixed in PR #60** — `docs.py` now uses `BoundedCache`.
2. ~~**V-E1 / V-L1**: Extract Galaxy client orchestration from `server.py` into
   a domain-level service.~~ **Fixed in PR #66** — `resolution.py` now owns
   all Galaxy fallback and multi-server search logic.

### Priority 2 (Warning-level violations)

3. ~~**V-T1**: Define a `LifespanContext` TypedDict or dataclass for the lifespan
   context dict.~~ **Fixed in PR #87** — `LifespanContext` TypedDict in `state.py`.
4. **V-T2**: Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`.
5. **V-D1**: Make `_module_to_skill_name()` public (rename to
   `module_to_skill_name()`).
6. **V-D2–V-D5**: Add `__all__` to all modules.
7. ~~**V-D7 / V-L2**: Move `_resolve_module_doc()` and `_resolve_role_doc()` to a
   domain-level resolution module.~~ **Fixed in PR #66**.
8. **V-D8**: Split `generate_manifest()` into generation and persistence.
9. ~~**V-E2**: Define a `GalaxyClientProtocol` for the Galaxy client interface.~~
   **Fixed in PR #66** — `GalaxyDocClient` Protocol in `types.py`.
10. ~~**V-E3**: Move `_transform_to_ansible_doc_format()` to `parser.py`.~~
    **Fixed in PR #69** — now `parser.transform_galaxy_to_ansible_doc_format()`.
11. **V-E4**: Encapsulate `collections.py` state in a `CollectionManager` class.
12. ~~**V-S2**: Use `asyncio.Lock` or explicit synchronization for
    `_missing_collections`.~~ **Mitigated in PR #87** — now per-session.
13. ~~**V-S3**: Design a `ServerState` or session context that encapsulates all
    mutable state.~~ **Partially resolved in PR #87** — `SharedState` +
    `SessionManager` with per-session `ServerState`.
14. **V-F1**: Make `SKILLS_DIR` lazy (function or descriptor).

### Priority 3 (Info-level, PEP 8 suggestions)

15. **V-D6 / V-F2 / V-T3**: Tighten return types from `dict[str, Any]` to
    specific `TypedDict`s where definitions exist. (Partially addressed in
    PR #60 — `EnsureCollectionResult`, `ErrorResponse`, `SkillEntry`,
    `CollectionInfo`, `CollectionSearchResult` TypedDicts added to `types.py`.
    Further tightened in PR #87 — `VersionInfo` TypedDict for version check
    results. Remaining tool handlers still return untyped dicts.)
16. Add `__all__` exports to all public modules.
