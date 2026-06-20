# Session Isolation: Encapsulate Server State

**Issue:** [#68](https://github.com/leogallego/ansible-know-mcp/issues/68)
**Date:** 2026-06-20
**Status:** Draft

## Problem

Mutable state is scattered across 4 modules as module-level globals:

| File | Variables | Risk |
|------|-----------|------|
| `collections.py` | `_tmp_dir`, `_installed`, `_install_locks`, `_locks_lock`, `_install_gate` | Shared across sessions, untestable without monkeypatching |
| `resolution.py` | `_missing_collections` | Stale negative cache persists across sessions |
| `server.py` | `_version_info`, `_galaxy_servers` | Write-once in lifespan, but stored as globals AND in untyped dict |

This makes the server difficult to test, reset, or run multiple sessions.
It blocks #64 (HTTP transport) where a shared server needs clean state boundaries.

## Scope

**In scope (this PR):**
- V-S3: Encapsulate all runtime state in a `ServerState` dataclass
- V-T1: Define a `LifespanContext` TypedDict for the lifespan context
- V-E4: Refactor `collections.py` into a `CollectionManager` class
- V-S2: Move `_missing_collections` out of module scope into `ServerState`

**Out of scope:**
- V-D8: Split `generate_manifest()` generation from persistence (independent refactor)
- Per-session isolation for HTTP transport (future — trivial once `ServerState` exists)
- Moving `galaxy.py`/`docs.py` BoundedCache instances into `ServerState` (process-scoped with TTL, safe to share)

## Design

### `CollectionManager` class in `src/ansible_know/collections.py` (External Access layer)

`CollectionManager` stays in `collections.py` because it runs subprocesses
(`ansible-galaxy`) — that's External Access behavior. The module-level globals
become instance variables with identical logic.

```python
class CollectionManager:
    """Per-process collection install state.

    Encapsulates the temp directory, installed collection tracking,
    and per-collection locks that were previously module-level globals.
    """

    MAX_TRACKED_COLLECTIONS = 100

    def __init__(self) -> None:
        self._tmp_dir: tempfile.TemporaryDirectory | None = None
        self._installed: dict[str, str] = {}
        self._install_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._install_gate = threading.Lock()

    def get_collections_path(self) -> str | None: ...
    def list_installed(self) -> dict[str, str]: ...
    def ensure_collection(self, collection_fqcn: str, version: str | None = None) -> EnsureCollectionResult: ...
```

### New module: `src/ansible_know/state.py` (Foundation layer)

`state.py` holds `ServerState` and `LifespanContext` only. It imports
`CollectionManager` under `TYPE_CHECKING` to avoid a Foundation → External Access
runtime dependency. `collection_manager` is a required field with no default —
the lifespan in `server.py` must construct and pass it explicitly.

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    import httpx
    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import GalaxyServerConfig

__all__ = ["ServerState", "LifespanContext"]


@dataclass
class ServerState:
    """All mutable runtime state for one server process.

    Created once in lifespan, stored in LifespanContext,
    accessed by tool handlers via _get_state(ctx).
    """

    collection_manager: CollectionManager
    missing_collections: set[str] = field(default_factory=set)
    version_info: dict[str, Any] | None = None
    galaxy_servers: list[GalaxyServerConfig] = field(default_factory=list)
    upgrade_warned: bool = False

    def clear_missing_namespace(self, namespace: str) -> None:
        self.missing_collections.discard(namespace)


class LifespanContext(TypedDict):
    """Typed lifespan context replacing the untyped dict."""

    http_client: httpx.AsyncClient
    state: ServerState
```

### Changes to `collections.py`

Module-level globals (`_tmp_dir`, `_installed`, `_install_locks`, `_locks_lock`, `_install_gate`) are deleted.
All logic moves into `CollectionManager` methods with identical behavior (see class definition above).
Helper functions (`_find_ansible_galaxy`, `_parse_version`) stay as module-level private functions (stateless utilities).
`_get_or_create_tmpdir` becomes a private method on `CollectionManager`.

The `_VERSION_PARSE_RE` compiled regex stays at module level (it's a constant).

### Changes to `resolution.py`

The `_missing_collections` module-level set is deleted.

`resolve_module_doc()` and `resolve_role_doc()` gain a `missing_collections: set[str] | None = None` parameter.
When `None`, they behave as if the set is empty (no negative caching — safe default).

`clear_missing_namespace()` is removed from `resolution.py`.
The equivalent is `state.clear_missing_namespace()` called from `server.py`.

`__all__` is updated to remove `clear_missing_namespace`.

### Changes to `server.py`

**Lifespan:**
```python
@lifespan
async def app_lifespan(server):
    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import load_galaxy_servers

    galaxy_servers = await run_in_executor(load_galaxy_servers)
    state = ServerState(
        collection_manager=CollectionManager(),
        galaxy_servers=galaxy_servers,
    )
    # ... log galaxy servers ...

    async with httpx.AsyncClient(...) as client:
        state.version_info = await _check_pypi_version(client)
        yield LifespanContext(http_client=client, state=state)
```

**State access helper (three-tier fallback):**
```python
def _get_state(ctx: Context | None) -> ServerState:
    if ctx is not None:
        return ctx.lifespan_context["state"]
    if _server_state is not None:
        return _server_state
    from ansible_know.collections import CollectionManager
    return ServerState(collection_manager=CollectionManager())

def _get_http_client(ctx: Context | None) -> httpx.AsyncClient | None:
    if ctx is None:
        return None
    return ctx.lifespan_context["http_client"]
```

**Tool handler pattern (before → after):**
```python
# Before:
http_client, galaxy_servers = _get_lifespan_resources(ctx)
raw_doc, meta = await resolution.resolve_module_doc(
    module_name, http_client=http_client, galaxy_servers=galaxy_servers,
    client_factory=_galaxy_factory(),
)

# After:
state = _get_state(ctx)
http_client = _get_http_client(ctx)
raw_doc, meta = await resolution.resolve_module_doc(
    module_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
    client_factory=_galaxy_factory(),
    missing_collections=state.missing_collections,
)
```

**Upgrade warning (before → after):**
```python
# Before:
lc = ctx.lifespan_context
if lc.get("upgrade_warned") or not info ...
lc["upgrade_warned"] = True

# After:
state = _get_state(ctx)
if state.upgrade_warned or not state.version_info ...
state.upgrade_warned = True
```

**Collections access (before → after):**
```python
# Before:
from ansible_know import collections
collections.get_collections_path()
collections.ensure_collection(...)
collections.list_installed()
resolution.clear_missing_namespace(ns)

# After:
state = _get_state(ctx)
state.collection_manager.get_collections_path()
state.collection_manager.ensure_collection(...)
state.collection_manager.list_installed()
state.clear_missing_namespace(ns)
```

### Changes to tests

Tests that monkeypatch `collections._installed`, `collections._tmp_dir`, or `resolution._missing_collections` will instead create fresh instances:

```python
# Before:
monkeypatch.setattr("ansible_know.collections._installed", {"ns.coll": "1.0.0"})

# After:
mgr = CollectionManager()
# or
state = ServerState()
```

### What stays unchanged

- `galaxy.py` — `_version_cache`, `_blob_cache`, `_enrichment_semaphore` stay as module-level BoundedCache instances (process-scoped, TTL-managed, safe to share)
- `docs.py` — `_manifest_cache` stays as module-level BoundedCache
- `parser.py` — no state, no changes
- `cache.py` — no changes
- `config.py` — `SKILLS_DIR` lazy-load stays (it's a constant after first access)
- `galaxy_config.py`, `readme_parser.py`, `async_utils.py`, `errors.py`, `types.py`, `validation.py` — no changes

## Architecture compliance

The new `state.py` module lives in the **Foundation** layer:
- No imports from Orchestration, Domain, or External Access
- Only imports: `typing`, `dataclasses`, `tempfile`, `threading`, stdlib
- Type-checking-only imports for `httpx` and `GalaxyServerConfig`

`CollectionManager` needs helpers from `errors.py` and `validation.py` (both Foundation) — acceptable.

## Migration path to per-session isolation (#64)

Once `ServerState` exists as a single object:
1. HTTP transport creates one `ServerState` per session instead of per process
2. `CollectionManager` can optionally share a temp directory across sessions (install once, share read-only)
3. `missing_collections` becomes naturally per-session
4. Caches in `galaxy.py`/`docs.py` can stay shared (performance optimization)

No design changes needed — just change where `ServerState()` is constructed.
