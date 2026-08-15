# Service Contracts

Enforceable architecture rules for the Ansible Know MCP Server. Reviewed by
`git-review` (ai-skills-git). **Hard rules** must be fixed before merge.
**Soft guidelines** are advisory (PEP 8 / naming stay with `pep8-review`).

This document is the sole source of truth for layer boundaries, allowed
dependencies, and known exceptions. Do not duplicate these rules into a
project review skill — update **this file** (and ADRs / strategy) when
architecture drifts.

Based on the architecture review in `docs/research/architecture-report.md`
(2026-06-16); updated through Agent Plugins packaging (2026-08-11) and
git-review migration (2026-08-14).

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
│                    readme_parser.py, redhat_docs.py      │
├─────────────────────────────────────────────────────────┤
│  Foundation        async_utils.py, cache.py, config.py,  │
│                    galaxy_config.py, state.py,            │
│                    tagging.py, text_utils.py,             │
│                    validation.py, errors.py, types.py     │
└─────────────────────────────────────────────────────────┘
```

**Data flows downward.** Each layer may depend on layers below it but never
on layers above. The Transport layer is owned by the FastMCP framework; the
Orchestration layer is the application's primary entry point.

### Layer map (path → layer)

Classify changed files with this table for `git-review`. Files that do not
match (e.g. `README.md`, `pyproject.toml`, CI) do not require architecture
review unless they change contracts/ADRs.

| Path / pattern | Layer |
|----------------|-------|
| Framework config, transport setup | **Transport** |
| `src/ansible_know/server.py` | **Orchestration** |
| `src/ansible_know/parser.py` | **Domain** |
| `src/ansible_know/skills.py` | **Domain** |
| `src/ansible_know/collection_manifest.py` | **Domain** |
| `src/ansible_know/docs.py` | **Domain** |
| `src/ansible_know/resolution.py` | **Domain** |
| `src/ansible_know/templates/*` | **Domain** (templates) |
| `src/ansible_know/galaxy.py` | **External Access** |
| `src/ansible_know/collections.py` | **External Access** |
| `src/ansible_know/readme_parser.py` | **External Access** |
| `src/ansible_know/redhat_docs.py` | **External Access** |
| `src/ansible_know/cache.py` | **Foundation** |
| `src/ansible_know/config.py` | **Foundation** |
| `src/ansible_know/galaxy_config.py` | **Foundation** |
| `src/ansible_know/async_utils.py` | **Foundation** |
| `src/ansible_know/state.py` | **Foundation** |
| `src/ansible_know/tagging.py` | **Foundation** |
| `src/ansible_know/text_utils.py` | **Foundation** |
| `src/ansible_know/validation.py` | **Foundation** |
| `src/ansible_know/errors.py` | **Foundation** |
| `src/ansible_know/types.py` | **Foundation** |
| `src/ansible_know/manifest_builder.py` | **Build-time** (not runtime) |
| `src/ansible_know/cli.py` | **CLI** (entrypoint) |
| `tests/*` | **Test** (mirror the source layer under test) |

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
| 20 tool handlers | `server.py` | `@mcp.tool(annotations=ToolAnnotations(...))` |
| 6 resource handlers | `server.py` | `@mcp.resource(uri, ...)` |
| 5 prompt handlers | `server.py` | `@mcp.prompt` |
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
| ~~V-T2~~ | ~~Warning~~ | ~~`_run_in_executor()` uses deprecated `asyncio.get_event_loop()`. Should use `asyncio.get_running_loop()` — the function is only called from async context.~~ **Fixed** — `async_utils.py` uses `asyncio.get_running_loop()`. `_run_in_executor` was moved from `server.py` to `async_utils.py` in PR #89. | ~~`server.py:126-129`~~ → `async_utils.py` |
| ~~V-T3~~ | ~~Info~~ | ~~Tool return types are `dict[str, Any]` rather than typed dicts.~~ **Fixed in PR #105** — all tool handlers (18 as of v0.7) use specific TypedDicts (`GetModuleDocResult`, `GetRoleDocResult`, `CollectionSearchResult`, etc.) with `| ErrorResponse` unions. Safe because `from __future__ import annotations` makes annotations strings at runtime — FastMCP never sees the TypedDict classes. Note: PR #60 originally reverted TypedDict annotations to `dict[str, Any]` over FastMCP wrapping concerns, but that concern is moot with stringified annotations. | ~~All tool handlers~~ → `types.py`, `server.py` |

---

## Layer 2: Orchestration → Domain

### Contract

The Orchestration layer (`server.py`) calls Domain functions to perform
business logic. Domain modules are imported lazily to avoid loading
`ansible-core` at startup.

### Interface Definition

| Domain Module | Functions Called | File |
|---------------|----------------|------|
| `parser` | `search_modules()`, `get_module_doc()`, `get_module_docs()`, `load_module_metadata_batch()`, `get_plugin_doc()`, `get_plugin_docs()`, `load_plugin_metadata_batch()`, `get_role_doc()`, `list_roles()`, `extract_module_metadata()`, `extract_role_metadata()` | `parser.py` |
| `skills` | `render_skill()`, `write_skill_package()`, `render_role_skill()`, `write_role_skill_package()`, `package_as_agent_plugin()`, `package_collection_for_lola()` (deprecated), `list_skills_sync()`, `get_skill_sync()`, `collection_skill_name()` / `fqcn_to_skill_name()` / related naming helpers | `skills.py` |
| `collection_manifest` | `generate_manifest()`, `load_cached_manifest()` | `collection_manifest.py` |
| `docs` | `search_docs()`, `fetch_doc_content()` | `docs.py` |
| `collections` | `ensure_collection()`, `list_installed()` | `collections.py` |
| `resolution` | `resolve_module_doc()`, `resolve_role_doc()`, `search_galaxy_collections()`, `clear_missing_namespace()` | `resolution.py` |

### Types Crossing This Boundary

| Direction | Type | Definition |
|-----------|------|------------|
| Down | `str` (module_name, keyword, namespace) | Primitives, validated by Orchestration |
| Up | `ModuleMetadata` | `types.py:8-15` — TypedDict |
| Up | `RoleMetadata` | `types.py:18-23` — TypedDict |
| Up | `EnsureCollectionResult` | `types.py` — TypedDict (added in PR #60) |
| Up | `PackageAsPluginResult` | `types.py` — TypedDict (added in #223) |
| Up | `PackageForLolaResult` | `types.py` — TypedDict (added in #149; deprecated wrap) |
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
| ~~V-D1~~ | ~~Warning~~ | ~~`server.py` calls `skills._module_to_skill_name()` — a private function (leading underscore). This crosses the public API boundary.~~ **Fixed** — renamed to `module_to_skill_name()` (public, in `skills.__all__`). `server.py` no longer calls it; namespace/short_name extraction is inlined at the call site. | ~~`server.py:753`, `server.py:880`~~ |
| ~~V-D2~~ | ~~Warning~~ | ~~`parser.py` does not define `__all__`. All module-level functions are implicitly public, including internal helpers like `_get_module_name()` and `_run_ansible_doc()`.~~ **Fixed** — `parser.py` now defines `__all__`. | ~~`parser.py`~~ |
| ~~V-D3~~ | ~~Warning~~ | ~~`skills.py` does not define `__all__`.~~ **Fixed** — `skills.py` now defines `__all__`. | ~~`skills.py`~~ |
| ~~V-D4~~ | ~~Warning~~ | ~~`collection_manifest.py` does not define `__all__`.~~ **Fixed** — `collection_manifest.py` now defines `__all__`. | ~~`collection_manifest.py`~~ |
| ~~V-D5~~ | ~~Warning~~ | ~~`docs.py` does not define `__all__`.~~ **Fixed** — `docs.py` now defines `__all__`. | ~~`docs.py`~~ |
| ~~V-D6~~ | ~~Info~~ | ~~Several Domain functions return `dict[str, Any]` where TypedDicts exist.~~ **Resolved across PRs #60, #87, #104, #105, #106** — tool handlers and key Domain returns use TypedDicts (`ParamDict`, `EntryPointInfo`, `EnsureCollectionResult`, etc.). Residual untyped internals may remain; not tracked as an open contract violation. | ~~`parser.py`, `server.py`~~ → `types.py` |
| ~~V-D7~~ | ~~Warning~~ | ~~`_resolve_module_doc()` and `_resolve_role_doc()` in `server.py` contain significant business logic (Galaxy fallback, missing-collection caching). This logic belongs in the Domain layer, not Orchestration. The Orchestration layer should delegate to a domain-level resolution function.~~ **Fixed in PR #66** — Resolution logic moved to `resolution.py`. | ~~`server.py:195-301`~~ |
| ~~V-D8~~ | ~~Warning~~ | ~~`collection_manifest.generate_manifest()` writes to disk as a side effect.~~ **Fixed** — Split into `generate_manifest()` (pure computation) and `write_manifest()` (I/O). Callers in `server.py` call both, wrapping `write_manifest` in `run_in_executor`. | ~~`collection_manifest.py:113-117`~~ |

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
| `redhat_docs.py` (`RedHatDocsClient`) | `server.py` (via `SharedState`) | `redhat_docs.py` |

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
- `RedHatDocsClient` methods are **async**. It manages its own MCP session
  lifecycle (lazy connect, auto-reconnect on 404 expiry). The client
  instance lives in `SharedState` (created at lifespan, closed at shutdown).
  Prefer injecting the lifespan ``httpx.AsyncClient`` (shared transport);
  ``close()`` only closes an owned fallback client. It is a transport
  client, not a data cache — `clear_cache` does not touch it.

### Violations

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| ~~V-E1~~ | ~~Error~~ | ~~`server.py` calls `GalaxyClient` directly in `search_collections()` and `_try_galaxy_servers()`, bypassing the Domain layer entirely. Galaxy access should be mediated through a domain-level service.~~ **Fixed in PR #66** — Galaxy access is now mediated through `resolution.py`. | ~~`server.py:168-192`, `server.py:480-486`~~ |
| ~~V-E2~~ | ~~Warning~~ | ~~`GalaxyClient` has no abstract base class or Protocol.~~ **Fixed in PR #66:** `GalaxyDocClient` Protocol and `GalaxyClientFactory` Protocol defined in `types.py`. `resolution.py` depends on the Protocol, not the concrete class. `GalaxyClient` satisfies the Protocol structurally. | ~~`galaxy.py:111`~~ → `types.py` |
| ~~V-E3~~ | ~~Warning~~ | ~~`GalaxyClient._transform_to_ansible_doc_format()` is a static method that converts Galaxy format to ansible-doc format. This is a data transformation that belongs in the Domain layer (parser), not the External Access layer.~~ **Fixed in PR #69** — Moved to `parser.transform_galaxy_to_ansible_doc_format()`. `galaxy.py` lazy-imports it. | ~~`galaxy.py:381-417`~~ → `parser.py` |
| ~~V-E4~~ | ~~Warning~~ | ~~`collections.py` module-level mutable state (`_tmp_dir`, `_installed`, `_install_locks`) makes the module impossible to test in isolation without monkeypatching globals. Should be encapsulated in a class.~~ **Fixed in PR #87** — state encapsulated in `CollectionManager` class, created per-session via `SessionManager`. | ~~`collections.py:26-29`~~ → `collections.py:CollectionManager` |
| ~~V-E5~~ | ~~Info~~ | ~~`galaxy.py` uses `asyncio.Semaphore` for enrichment throttling but creates it lazily with event-loop detection (`_get_enrichment_semaphore`). This is fragile if the event loop changes.~~ **Fixed in PR #106** — semaphore moved to `SharedState` (created once at lifespan). `GalaxyClient` accepts it as constructor parameter. `_galaxy_factory()` closure injects it from context. | ~~`galaxy.py:40-46`~~ → `state.py`, `galaxy.py`, `server.py` |

---

## Layer 4: External Access → Foundation

### Contract

All layers depend on Foundation modules for configuration, validation,
error types, and shared type definitions. Foundation modules have no
dependencies on upper layers.

### Interface Definition

| Foundation Module | Purpose | File |
|-------------------|---------|------|
| `async_utils.py` | `run_in_executor` — blocking-to-async bridge; `optional_http_client` — shared-or-owned httpx lifecycle | `async_utils.py` |
| `cache.py` | Thread-safe bounded LRU cache with TTL | `cache.py` |
| `config.py` | Paths, constants, env var defaults, doc sources | `config.py` |
| `galaxy_config.py` | Galaxy server config from `ansible.cfg` | `galaxy_config.py` |
| `state.py` | Session state management (collection install tracking) | `state.py` |
| `tagging.py` | Tag derivation from module FQCN segments | `tagging.py` |
| `text_utils.py` | RTD/RH markdown cleaning, HTML→markdown, token estimate | `text_utils.py` |
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
| `PackageAsPluginResult` | `TypedDict` | `types.py` (added in #223) |
| `AgentPluginManifest` | `TypedDict` | `types.py` (added in #223) |
| `AgentMcpStdioServer` | `TypedDict` | `types.py` (added in #223) |
| `AgentMcpHttpServer` | `TypedDict` | `types.py` (added in #223) |
| `AgentMcpServer` | union alias | `types.py` (added in #223) |
| `AgentMcpConfig` | `TypedDict` | `types.py` (added in #223) |
| `PackageForLolaResult` | `TypedDict` | `types.py` (added in #149; deprecated wrap) |

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
| ~~V-F1~~ | ~~Warning~~ | ~~`config.py` evaluates `Path.cwd()` at import time for `SKILLS_DIR`. This captures the working directory at the moment the module is first imported, which may differ from the intended runtime directory. Should be a function or lazy property.~~ **Fixed** — `SKILLS_DIR` now uses `__getattr__` module-level lazy evaluation with caching. | ~~`config.py:14-17`~~ → `config.py:__getattr__` |
| ~~V-F2~~ | ~~Info~~ | ~~`types.py` `TypedDict` definitions use `dict[str, Any]` for nested structures (e.g., `params: list[dict[str, Any]]`). More specific nested types would improve type safety.~~ **Fixed in PRs #104, #106** — `params` tightened to `list[ParamDict]` (PR #104), `entry_points` tightened to `dict[str, EntryPointInfo]` (PR #106). | ~~`types.py:14`, `types.py:22`~~ → `types.py` |
| V-F3 | Info | `galaxy_config.py` `GalaxyServerConfig` is a frozen dataclass, which is correct for an immutable configuration value object. No issues. | `galaxy_config.py:24-35` |

---

## Cross-Cutting Concerns

### State Management

State is split into process-wide and per-session layers (PR #87):

- **`SharedState`** (process-wide): `galaxy_servers`, `version_info`,
  `enrichment_semaphore`, `redhat_client`. Created once in lifespan.
  `version_info` updated by periodic PyPI check. `redhat_client` is a
  transport client (not a data cache) — shares the lifespan httpx client
  when injected, manages MCP session lifecycle, and is closed at lifespan
  shutdown (without closing the shared httpx client), not by `clear_cache`.
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

### Anti-patterns (do not introduce)

- **Domain → External Access** — e.g. `parser.py` importing `galaxy` /
  `collections`. Domain depends on Foundation only.
- **External Access → Domain** — e.g. `galaxy.py` importing `parser` /
  `skills` for non-transformer use. (V-L3: lazy import of pure transformers
  is the accepted exception — do not expand.)
- **Foundation → any upper layer** — zero deps on Orchestration / Domain /
  External Access.
- **Orchestration business logic** — `server.py` must not implement
  resolution strategies, data transforms, or caching; delegate to Domain.

Do not add new layer-boundary crossings. If a PR must cross a boundary,
document why and file a follow-up issue.

### Violations of Dependency Rules

| ID | Severity | Description |
|----|----------|-------------|
| ~~V-L1~~ | ~~Error~~ | ~~Orchestration (`server.py`) imports and calls External Access (`galaxy.py:GalaxyClient`) directly, bypassing the Domain layer.~~ **Fixed in PR #66** — Orchestration delegates to `resolution.py` (Domain). | 
| ~~V-L2~~ | ~~Warning~~ | ~~Orchestration (`server.py`) contains domain logic in `_resolve_module_doc()` and `_resolve_role_doc()` — these are domain-level resolution strategies, not orchestration.~~ **Fixed in PR #66** — Moved to `resolution.py`. |
| V-L3 | Info | External Access (`galaxy.py`) lazy-imports Domain modules (`readme_parser.py` for `fetch_role_doc()`, `parser.py` for `transform_galaxy_to_ansible_doc_format()` in `fetch_module_doc()`). Both are pure data transformers — acceptable cross-layer calls that avoid duplicating domain logic. |
| ~~V-L4~~ | ~~Warning~~ | ~~Domain (`parser.py`) imports External Access (`collections.py`) at the top level to get the collections path.~~ **Fixed in PR #69** — `parser.py` accepts `collections_path` as a parameter; callers (server.py, resolution.py) inject it from `collections.get_collections_path()`. |

---

## Hard rules

Must-fix before merge (`git-review` Errors). Layer narrative and per-boundary
detail live in the sections above; this list is the enforceable checklist.

### Types / API surface

- Structured returns use `TypedDict`s from `types.py` (`ModuleMetadata`,
  `RoleMetadata`, `DocProvenance`, `ParamDict`, `EntryPointInfo`, tool result
  types) — not bare `dict[str, Any]` at public boundaries.
- New structured shapes get a `TypedDict` in `types.py`.
- Errors use `errors.py` hierarchy (`AnsibleKnowError` subclasses). Do not
  raise bare `Exception` / `ValueError` from tool or Domain paths.
- Tool inputs are validated in Orchestration (`validation.py`) before Domain
  or External Access calls.

### Async / sync

- Blocking I/O (subprocess, filesystem, sync network) called from async
  handlers MUST go through `run_in_executor()` / `_run_in_executor()`.
- Subprocess: `subprocess.run(..., capture_output=True, text=True, timeout=…)`.
  No `Popen` / `os.system` for tool paths.
- Use `asyncio.get_running_loop()` — never deprecated `get_event_loop()`.

### State

- Process-wide state → `SharedState` (lifespan). Per-session → `ServerState`
  via `SessionManager` (`state.py`).
- New caches SHOULD use `BoundedCache` (`cache.py`). No ad-hoc
  `OrderedDict` + `Lock` patterns.
- Collection install state → `CollectionManager` (per-session). No module-level
  collection state.
- State touched from executor threads MUST use `threading.Lock`. Async-only
  shared mutation SHOULD use `asyncio.Lock` when concurrent coroutines mutate.

### Public API / MCP surface

- Modules with `__all__` must list new public symbols (or use `_` prefix for
  internal).
- No cross-module calls to another module's `_private()` helpers — promote to
  public if shared.
- New MCP tools include `ToolAnnotations` (`readOnlyHint` / `idempotentHint` /
  `destructiveHint` as appropriate). Resources need clear names/descriptions.

### Security / boundary

- Validate tool inputs (`_FQCN_RE`, `_NAMESPACE_RE`, etc.) before use.
- Paths from user input: `validate_path_containment()` /
  `validate_install_path()`.
- User-facing errors: `sanitize_error()` (no filesystem paths).
- Subprocess args as lists — no string-interpolated shells from user input.
- Galaxy credentials: `_sanitize_credential()` (strip control chars).
- Large payloads: `truncate_response()` where applicable.

### ADR / strategy

- ADR-0006 (upstream-first): do not add knowledge tools that overlap next-mcp;
  focus on skill generation and sharing.
- ADR-0007: generated skills kebab-case; `metadata.fqcn` / `collection` /
  `plugin-type` / `compatibility`; validate with `agentskills validate`.
- ADR-0008 / ADR-0009: three-layer distribution; Layer 2 prefers Agent Plugins
  (`package_as_plugin`); `package_for_lola` is deprecated.
- PRs that contradict an ADR must update the ADR or document the deviation.
- Tools marked for upstream deprecation (search_modules, search_plugins,
  search_collections, ensure_collection, get_module_doc, get_plugin_doc,
  get_role_doc, clear_cache): maintain only — no new features.

---

## Soft guidelines

- PEP 8 naming, None comparisons, bare `except`, sequence truthiness →
  `pep8-review` (always-loaded). Do not duplicate here.
- Prefer fixing residual untyped internals opportunistically (Info), not as
  merge blockers when TypedDicts already exist at the MCP boundary.
- Architecture drift in a PR (new modules, layer moves, ADR edits) → update
  **this file** and ADRs before merge; do not revive a project review skill.

---

## Severity calibration

Used by `git-review` when classifying findings. PEP 8 / naming / `None`
comparisons / bare `except` → `pep8-review` (not listed here).

| Severity | When (this repo) | Action |
|----------|------------------|--------|
| Error | New layer-boundary violation; thread-safety bug on shared/executor state; security-boundary miss (validation, path containment, `sanitize_error`, credential sanitize, subprocess list-args); ADR contradiction without ADR update | Block merge |
| Warning | Missing TypedDict / validation / `__all__` at a public boundary; cross-module `_private` use; new ad-hoc cache/state instead of `BoundedCache` / `SharedState`/`ServerState`; new features on upstream-deprecated tools; layer map or this file stale vs the diff | Fix or file follow-up before merge |
| Info | Residual internal `dict[str, Any]` where TypedDicts exist; minor naming; missing annotations on non-boundary helpers; accepted exceptions (e.g. V-L3) unchanged | Note; fix opportunistically |

---

## Known exceptions

Open / accepted exceptions appear in the violation tables above. Do not worsen
them. Current open dependency exception:

| ID | Severity | Summary | Status |
|----|----------|---------|--------|
| V-L3 | Info | `galaxy.py` lazy-imports Domain transformers | Accepted — do not expand |

Resolved historical IDs (V-T*, V-D*, V-E*, V-S*, V-L1/L2/L4, V-D6, …) remain
struck through in the layer sections for audit trail.

---

## Companion skills

| When files match | Load skill (if installed) |
|------------------|---------------------------|
| Architecture / layer / ADR review | `git-review` (always-load via `.git-pipeline.yml`) |
| `*.py` generally | `pep8-review` (always-load) |
| Types / signatures | `tighten-types`, `contract-docstrings` |
| Exception paths | `try-except` |

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
4. ~~**V-T2**: Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`.~~
   **Fixed** — `async_utils.py` uses `asyncio.get_running_loop()`.
5. ~~**V-D1**: Make `_module_to_skill_name()` public (rename to
   `module_to_skill_name()`).~~ **Fixed** — renamed and added to `skills.__all__`.
6. ~~**V-D2–V-D5**: Add `__all__` to all modules.~~ **Fixed** — all modules now define `__all__`.
7. ~~**V-D7 / V-L2**: Move `_resolve_module_doc()` and `_resolve_role_doc()` to a
   domain-level resolution module.~~ **Fixed in PR #66**.
8. ~~**V-D8**: Split `generate_manifest()` into generation and persistence.~~ **Fixed** — `generate_manifest()` is pure; `write_manifest()` handles I/O.
9. ~~**V-E2**: Define a `GalaxyClientProtocol` for the Galaxy client interface.~~
   **Fixed in PR #66** — `GalaxyDocClient` Protocol in `types.py`.
10. ~~**V-E3**: Move `_transform_to_ansible_doc_format()` to `parser.py`.~~
    **Fixed in PR #69** — now `parser.transform_galaxy_to_ansible_doc_format()`.
11. ~~**V-E4**: Encapsulate `collections.py` state in a `CollectionManager` class.~~
    **Fixed in PR #87** — `CollectionManager` class, created per-session.
12. ~~**V-S2**: Use `asyncio.Lock` or explicit synchronization for
    `_missing_collections`.~~ **Mitigated in PR #87** — now per-session.
13. ~~**V-S3**: Design a `ServerState` or session context that encapsulates all
    mutable state.~~ **Partially resolved in PR #87** — `SharedState` +
    `SessionManager` with per-session `ServerState`.
14. ~~**V-F1**: Make `SKILLS_DIR` lazy (function or descriptor).~~
    **Fixed** — uses `__getattr__` lazy evaluation with caching.

### Priority 3 (Info-level, PEP 8 suggestions)

15. ~~**V-D6 / V-F2 / V-T3**: Tighten return types from `dict[str, Any]` to
    specific `TypedDict`s where definitions exist.~~ **Resolved across PRs
    #60, #87, #104, #105, #106** — all tool handlers now use specific TypedDicts
    (`GetModuleDocResult`, `GetRoleDocResult`, `CollectionSearchResult`, etc.).
    `ParamDict` and `EntryPointInfo` replace nested `dict[str, Any]`.
    PR #60 initially reverted TypedDict annotations over FastMCP wrapping
    concerns; PR #105 re-added them safely using `from __future__ import
    annotations` (annotations are strings at runtime, invisible to FastMCP).
16. Add `__all__` exports to all public modules.
