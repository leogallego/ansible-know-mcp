# Session Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encapsulate all module-level mutable server state into a `ServerState` dataclass and `CollectionManager` class so state is testable, resettable, and ready for future per-session isolation.

**Architecture:** New `state.py` (Foundation) holds `ServerState` and `LifespanContext`. `CollectionManager` class replaces module-level globals in `collections.py` (External Access). `resolution.py` receives `missing_collections` as a parameter. `server.py` creates `ServerState` in lifespan, passes it through typed context.

**Tech Stack:** Python 3.11+, dataclasses, TypedDict, pytest, FastMCP

## Skills

**Load at session start (before any task):**
- `superpowers:test-driven-development` — all tasks follow TDD (write failing test → implement → verify)
- `superpowers:verification-before-completion` — run full suite before claiming any task is done
- `pep8-naming` — naming conventions for new classes, methods, module-level names
- `pep8-type-annotations` — type annotation formatting for TypedDict, dataclass fields

**Load per task:**
- Tasks 1–2 (new classes): local skill `skills/python-contract-docstrings` (read SKILL.md, apply to `CollectionManager` and `ServerState` — document contracts, input invariants, errors)
- Tasks 1–2 (new types): local skill `skills/python-tighten-types` (read SKILL.md, apply to `state.py` and `collections.py` — verify annotations are tight, no loose `dict[str, Any]` where TypedDict exists)
- Task 5 (final verification): `superpowers:verification-before-completion` + local skill `skills/python-pre-mortem` (read SKILL.md, apply to the new state wiring — spot fragile assumptions, implicit coupling)
- Task 6 (PR): `superpowers:requesting-code-review`, `pep8-review`, and local skill `skills/pr-architecture-review` (read SKILL.md, apply its 8-step checklist against the final diff)

## Global Constraints

- Layer dependency: Foundation has zero runtime imports from upper layers
- `CollectionManager` imports under `TYPE_CHECKING` only in `state.py`
- All new modules must define `__all__`
- Thread safety patterns preserved identically
- Existing test behavior must not change (438 passing, 57 skipped)
- Run tests with `.venv/bin/pytest tests/ -v --tb=short`
- Run lint with `.venv/bin/ruff check src/ tests/`
- **Sandbox mode:** auto-allow, auto-edit. Never prefix commands with env var assignments (e.g. `FOO=bar command`) — use `export` on a separate line or pass via other means. Use `.venv/bin/` prefixed binaries directly, never `source activate`. These patterns trigger unnecessary permission prompts in sandboxed environments.

---

### Task 1: Create `CollectionManager` class in `collections.py`

**Files:**
- Modify: `src/ansible_know/collections.py`
- Modify: `tests/test_collections.py`

**Interfaces:**
- Produces: `CollectionManager` class with methods `ensure_collection(collection_fqcn: str, version: str | None = None) -> EnsureCollectionResult`, `get_collections_path() -> str | None`, `list_installed() -> dict[str, str]`

- [ ] **Step 1: Write tests that use `CollectionManager` directly**

Replace the `reset_collections_state` fixture and update all test imports in `tests/test_collections.py`. Every test that called `ensure_collection()` as a module function now calls `mgr.ensure_collection()` on a fresh `CollectionManager` instance.

New fixture:

```python
@pytest.fixture
def mgr():
    """Create a fresh CollectionManager for each test."""
    from ansible_know.collections import CollectionManager
    manager = CollectionManager()
    yield manager
    if manager._tmp_dir is not None:
        try:
            manager._tmp_dir.cleanup()
        except Exception:
            pass
```

Remove the `autouse=True` `reset_collections_state` fixture entirely.

Update imports at the top of the file — remove `ensure_collection`, `get_collections_path`, `list_installed` imports. Add `from ansible_know.collections import CollectionManager`.

Update every test class method signature to accept `mgr` and call methods on it:

- `TestGetCollectionsPath`: call `mgr.get_collections_path()` and `mgr.ensure_collection()`
- `TestListInstalled`: call `mgr.list_installed()` and `mgr.ensure_collection()`
- `TestEnsureCollectionInstalls`: call `mgr.ensure_collection()` and `mgr.list_installed()`
- `TestEnsureCollectionErrors`: call `mgr.ensure_collection()`
- `TestVersionParsing`: call `mgr.ensure_collection()`. The `test_fallback_to_manifest` test that previously set `col._tmp_dir` directly now sets `mgr._tmp_dir` instead.
- `TestConcurrentInstall`: all threads must share the same `mgr` instance (pass via `target=mgr.ensure_collection`)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_collections.py -v --tb=short`
Expected: FAIL — `CollectionManager` class does not exist yet

- [ ] **Step 3: Implement `CollectionManager` class**

In `src/ansible_know/collections.py`:

1. Keep module-level constants: `_VERSION_PARSE_RE`, `logger`
2. Keep module-level stateless helpers: `_find_ansible_galaxy()`, `_parse_version()`
3. Create `CollectionManager` class:
   - Move `MAX_TRACKED_COLLECTIONS = 100` to class attribute
   - `__init__` creates instance variables: `self._tmp_dir`, `self._installed`, `self._install_locks`, `self._locks_lock`, `self._install_gate`
   - `_get_or_create_tmpdir(self)` — same logic, uses `self._locks_lock` and `self._tmp_dir`
   - `ensure_collection(self, ...)` — same logic, uses `self._locks_lock`, `self._install_locks`, `self._installed`, `self._install_gate`, `self._get_or_create_tmpdir()`
   - `get_collections_path(self)` — same logic, uses `self._locks_lock` and `self._tmp_dir`
   - `list_installed(self)` — same logic, uses `self._locks_lock` and `self._installed`
4. Delete old module-level globals: `_tmp_dir`, `_installed`, `_install_locks`, `_locks_lock`, `_install_gate`
5. Delete old module-level functions: `_get_or_create_tmpdir()`, `ensure_collection()`, `get_collections_path()`, `list_installed()`
6. Add `__all__ = ["CollectionManager"]`

The method bodies are identical to the current functions — just replace module globals with `self._` attributes. `_find_ansible_galaxy()` and `_parse_version()` stay as module-level functions called from within the methods.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_collections.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Run full suite to check for breakage**

Run: `.venv/bin/pytest tests/ -v --tb=short`
Expected: Failures in `test_server.py` and `test_resolution.py` (they still use old module-level functions). `test_collections.py` passes.

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/collections.py tests/test_collections.py
git commit -m "refactor: extract CollectionManager class from collections globals

Addresses V-E4: module-level mutable state in collections.py is now
encapsulated in a CollectionManager class with identical thread-safety.

Part of #68.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Create `state.py` with `ServerState` and `LifespanContext`

**Files:**
- Create: `src/ansible_know/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `CollectionManager` from Task 1 (type-check only)
- Produces: `ServerState` dataclass with fields `collection_manager: CollectionManager`, `missing_collections: set[str]`, `version_info: dict[str, Any] | None`, `galaxy_servers: list[GalaxyServerConfig]`, `upgrade_warned: bool`, method `clear_missing_namespace(namespace: str) -> None`. `LifespanContext` TypedDict with keys `http_client: httpx.AsyncClient`, `state: ServerState`.

- [ ] **Step 1: Write tests for `ServerState` and `LifespanContext`**

Create `tests/test_state.py`:

```python
"""Tests for ansible_know.state."""

from ansible_know.collections import CollectionManager
from ansible_know.state import LifespanContext, ServerState


class TestServerState:
    def test_create_with_required_fields(self):
        mgr = CollectionManager()
        state = ServerState(collection_manager=mgr)
        assert state.collection_manager is mgr
        assert state.missing_collections == set()
        assert state.version_info is None
        assert state.galaxy_servers == []
        assert state.upgrade_warned is False

    def test_clear_missing_namespace(self):
        mgr = CollectionManager()
        state = ServerState(collection_manager=mgr)
        state.missing_collections.add("netbox.netbox")
        state.clear_missing_namespace("netbox.netbox")
        assert "netbox.netbox" not in state.missing_collections

    def test_clear_missing_namespace_absent_is_noop(self):
        mgr = CollectionManager()
        state = ServerState(collection_manager=mgr)
        state.clear_missing_namespace("nonexistent.ns")
        assert state.missing_collections == set()

    def test_independent_instances(self):
        mgr1 = CollectionManager()
        mgr2 = CollectionManager()
        state1 = ServerState(collection_manager=mgr1)
        state2 = ServerState(collection_manager=mgr2)
        state1.missing_collections.add("netbox.netbox")
        assert "netbox.netbox" not in state2.missing_collections


class TestLifespanContext:
    def test_is_typed_dict(self):
        assert "http_client" in LifespanContext.__annotations__
        assert "state" in LifespanContext.__annotations__
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state.py -v --tb=short`
Expected: FAIL — `ansible_know.state` module does not exist

- [ ] **Step 3: Implement `state.py`**

Create `src/ansible_know/state.py`:

```python
"""Server state and lifespan context types.

Foundation-layer module: no runtime imports from Domain, External Access,
or Orchestration. CollectionManager is imported under TYPE_CHECKING only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    import httpx

    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import GalaxyServerConfig

__all__ = ["LifespanContext", "ServerState"]


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
        """Remove a namespace from the negative cache."""
        self.missing_collections.discard(namespace)


class LifespanContext(TypedDict):
    """Typed lifespan context replacing the untyped dict."""

    http_client: httpx.AsyncClient
    state: ServerState
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Lint check**

Run: `.venv/bin/ruff check src/ansible_know/state.py tests/test_state.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/state.py tests/test_state.py
git commit -m "feat: add ServerState and LifespanContext types

Addresses V-S3 and V-T1: typed lifespan context and centralized
state dataclass for all mutable runtime state.

Part of #68.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Update `resolution.py` to accept `missing_collections` as parameter

**Files:**
- Modify: `src/ansible_know/resolution.py`
- Modify: `tests/test_resolution.py`

**Interfaces:**
- Consumes: None (standalone change)
- Produces: `resolve_module_doc(..., missing_collections: set[str] | None = None)`, `resolve_role_doc(..., missing_collections: set[str] | None = None)`. `clear_missing_namespace()` removed. `__all__` updated.

- [ ] **Step 1: Update tests to pass `missing_collections` explicitly**

In `tests/test_resolution.py`:

Replace the `reset_negative_cache` autouse fixture with a simple fixture that provides a fresh set:

```python
@pytest.fixture
def missing():
    """Provide a fresh missing-collections set for each test."""
    return set()
```

Update `TestResolveModuleDoc` — add `missing` param to each test and pass `missing_collections=missing` to `resolve_module_doc()`:

```python
async def test_local_success_no_galaxy(self, mock_ansible_doc, missing):
    mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
    from ansible_know.resolution import resolve_module_doc
    raw_doc, galaxy_meta = await resolve_module_doc(
        "ansible.builtin.package", missing_collections=missing,
    )
    assert "ansible.builtin.package" in raw_doc
    assert galaxy_meta is None
```

Same pattern for all other tests. For `TestNegativeCache`:

- `test_skips_local_on_cache_hit`: pre-populate `missing.add("netbox.netbox")` then pass `missing_collections=missing`
- `test_populates_cache_on_collection_not_found`: pass `missing_collections=missing`, then assert `"netbox.netbox" in missing`
- `test_does_not_cache_non_collection_errors`: pass `missing_collections=missing`, assert `"ansible.builtin" not in missing`
- `test_clear_missing_namespace`: test `ServerState.clear_missing_namespace()` instead:

```python
def test_clear_missing_namespace(self):
    from ansible_know.collections import CollectionManager
    from ansible_know.state import ServerState
    state = ServerState(collection_manager=CollectionManager())
    state.missing_collections.add("netbox.netbox")
    state.clear_missing_namespace("netbox.netbox")
    assert "netbox.netbox" not in state.missing_collections
```

- `test_role_skips_local_on_cache_hit`: pre-populate `missing.add("some.col")` then pass `missing_collections=missing`

Update `TestResolveRoleDoc` — add `missing` param and pass `missing_collections=missing`.

`TestSearchGalaxyCollections` — no changes needed (doesn't use `missing_collections`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resolution.py -v --tb=short`
Expected: FAIL — `resolve_module_doc()` and `resolve_role_doc()` don't accept `missing_collections` parameter yet

- [ ] **Step 3: Update `resolution.py`**

1. Delete `_missing_collections: set[str] = set()` (line 43)
2. Delete the comment block above it (lines 34-42)
3. Delete `clear_missing_namespace()` function (lines 91-93)
4. Update `__all__` — remove `"clear_missing_namespace"`
5. Add `missing_collections: set[str] | None = None` parameter to `resolve_module_doc()` after `client_factory`
6. Add `missing_collections: set[str] | None = None` parameter to `resolve_role_doc()` after `client_factory`
7. In `resolve_module_doc()`: replace `_missing_collections` with `missing_collections` (guarded by `if missing_collections is not None`):
   - Line 117: `if namespace and missing_collections is not None and namespace in missing_collections:`
   - Line 139: `if namespace and missing_collections is not None: missing_collections.add(namespace)`
8. In `resolve_role_doc()`: same replacements:
   - Line 172: `if not (namespace and missing_collections is not None and namespace in missing_collections):`
   - Line 179: `if namespace and missing_collections is not None: missing_collections.add(namespace)`

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolution.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/resolution.py tests/test_resolution.py
git commit -m "refactor: pass missing_collections as parameter to resolution functions

Addresses V-S2: _missing_collections module-level set removed.
Resolution functions now receive the set as a parameter, making
the negative cache testable and session-scoped.

Part of #68.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Update `server.py` to use `ServerState` and `LifespanContext`

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `ServerState`, `LifespanContext` from Task 2; `CollectionManager` from Task 1; updated `resolve_module_doc`/`resolve_role_doc` signatures from Task 3

- [ ] **Step 1: Update `test_server.py` fixtures and tests**

Remove the `reset_negative_cache_global` autouse fixture (lines 17-23). It's no longer needed because `resolution.py` has no module-level `_missing_collections`.

Update `TestMaybeWarnUpgrade` tests (lines 758-831). These currently mock `ctx.lifespan_context` as a dict with keys `"version_info"`, `"upgrade_warned"`. Change them to use `ServerState`:

```python
class TestMaybeWarnUpgrade:
    @pytest.mark.asyncio
    async def test_warns_when_outdated(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.server import _maybe_warn_upgrade
        from ansible_know.state import ServerState

        state = ServerState(
            collection_manager=CollectionManager(),
            version_info={
                "installed": "0.3.2", "latest": "0.4.0",
                "outdated": True, "upgrade_command": "uvx --upgrade ansible-know-mcp",
            },
        )
        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"state": state, "http_client": None}
        mock_ctx.warning = AsyncMock()

        await _maybe_warn_upgrade(mock_ctx)
        mock_ctx.warning.assert_called_once()
        assert "outdated" in mock_ctx.warning.call_args[0][0]
        assert state.upgrade_warned is True
```

Apply same pattern to `test_warns_only_once`, `test_no_warn_when_current`, `test_no_warn_when_check_failed`. For `test_no_warn_when_no_ctx` — no change needed.

Update `TestServerVersionResource` (lines 833-861). Replace `srv._version_info` manipulation with `ServerState`:

```python
class TestServerVersionResource:
    def test_returns_installed_version_without_pypi_check(self):
        from ansible_know.collections import CollectionManager
        from ansible_know.state import ServerState
        import ansible_know.server as srv
        state = ServerState(collection_manager=CollectionManager())
        old = srv._server_state
        try:
            srv._server_state = state
            result = json.loads(srv.resource_server_version())
            assert result["installed"] == srv._VERSION
            assert result["latest"] is None
            assert result["outdated"] is None
        finally:
            srv._server_state = old
```

Wait — the resource functions access `_version_info` as a module global. After refactoring, they'll need access to `ServerState`. Since resource functions don't receive `ctx`, they'll need a module-level reference. The simplest approach: keep a `_server_state: ServerState | None = None` module global in server.py, set in lifespan. Resources read from it. This is the same pattern as the current `_version_info` global — just consolidated into one object.

Update `TestEnsureCollectionTool` (lines 300-345). Remove the `col._installed = {}; col._tmp_dir = None` lines — the tool now accesses `state.collection_manager`, so mock that path instead. The simplest approach: these tests call `ensure_collection()` as a tool function. The tool calls `_get_state(ctx)` which returns a `ServerState` with a fresh `CollectionManager` when `ctx is None`. So the tests can remain mostly as-is — just remove the monkeypatching of old globals.

Update `TestLifespanHttpClient` (lines 931-1031). The `mock_ctx.lifespan_context` dicts change from `{"http_client": ..., "galaxy_servers": ...}` to `{"http_client": ..., "state": ServerState(collection_manager=CollectionManager(), galaxy_servers=[...])}`.

Update `TestGetRoleDocTool.test_cached_missing_collection_skips_local` (line 1090). Replace `resolution._missing_collections.add("some.col")` with setting up a `ServerState` with pre-populated `missing_collections` and patching `_get_state` to return it.

Update `TestResourceFunctions.test_resource_installed_collections_*` — these currently patch `collections.list_installed`. After refactoring, `resource_installed_collections()` calls `_server_state.collection_manager.list_installed()`, so the patch target changes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -v --tb=short`
Expected: Many failures — server.py not yet updated

- [ ] **Step 3: Update `server.py`**

**Imports:** Add at top:
```python
from ansible_know.state import LifespanContext, ServerState
```

**Module globals:** Replace `_version_info` and `_galaxy_servers` with:
```python
# Module-level reference for resource functions that don't receive FastMCP
# Context. Set once in lifespan; consolidates the former _version_info and
# _galaxy_servers globals into a single typed object.
_server_state: ServerState | None = None
```

**Lifespan:** Replace `app_lifespan`:
```python
@lifespan
async def app_lifespan(server):
    global _server_state
    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import load_galaxy_servers

    galaxy_servers = await run_in_executor(load_galaxy_servers)
    state = ServerState(
        collection_manager=CollectionManager(),
        galaxy_servers=galaxy_servers,
    )
    _server_state = state
    for gs in galaxy_servers:
        auth_type = "token" if gs.token else ("basic" if gs.username else "none")
        logger.info("Galaxy server: %s (%s, auth=%s)", gs.name, gs.url, auth_type)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=120.0),
        verify=True,
    ) as client:
        state.version_info = await _check_pypi_version(client)
        yield LifespanContext(http_client=client, state=state)
```

**Replace `_get_lifespan_resources` and add new helpers:**
```python
def _get_state(ctx: Context | None) -> ServerState:
    """Return ServerState from ctx, fall back to module-level, or create ephemeral."""
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

The three-tier fallback: (1) ctx-based is primary (tool handlers in production),
(2) `_server_state` for resource functions and edge cases where lifespan has run
but no ctx is available, (3) ephemeral instance only in pure unit tests with no
lifespan.

Delete `_get_lifespan_resources()`.

**Replace `_maybe_warn_upgrade`:**
```python
async def _maybe_warn_upgrade(ctx: Context | None) -> None:
    if ctx is None:
        return
    state = _get_state(ctx)
    if state.upgrade_warned or not state.version_info or not state.version_info.get("outdated"):
        return
    info = state.version_info
    await ctx.warning(
        f"ansible-know-mcp {info['installed']} is outdated; "
        f"latest is {info['latest']}. "
        f"Upgrade: {info['upgrade_command']}"
    )
    state.upgrade_warned = True
```

**Update all tool handlers.** For each tool that uses state:

`search_modules`: replace `collections.get_collections_path()` with `_get_state(None).collection_manager.get_collections_path()` — but this tool has no `ctx` param. Add `ctx: Context | None = None` and use `_get_state(ctx)`:
```python
state = _get_state(ctx)
results = await run_in_executor(
    parser.search_modules, keyword, collection_filter=namespace,
    collections_path=state.collection_manager.get_collections_path(),
)
```

`get_module_doc`: replace `_get_lifespan_resources(ctx)` with:
```python
state = _get_state(ctx)
http_client = _get_http_client(ctx)
raw_doc, galaxy_meta = await resolution.resolve_module_doc(
    module_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
    client_factory=_galaxy_factory(),
    missing_collections=state.missing_collections,
)
```

Same pattern for `get_role_doc`, `search_collections`, `generate_skill`, `generate_role_skill`.

`get_collection_manifest`: replace `collections.list_installed()` and `collections.get_collections_path()` with `state.collection_manager.list_installed()` and `state.collection_manager.get_collections_path()`.

`ensure_collection`: replace `collections.ensure_collection(...)` with `state.collection_manager.ensure_collection(...)`. Replace `resolution.clear_missing_namespace(ns)` with `state.clear_missing_namespace(ns)`.

`generate_collection_skills`: replace `collections.get_collections_path()` with `state.collection_manager.get_collections_path()`.

`resource_installed_collections`: replace `collections.list_installed()` with `_server_state.collection_manager.list_installed() if _server_state else {}`.

`resource_server_version`: replace `_version_info` with `_server_state.version_info if _server_state else None`.

`resource_galaxy_servers`: if it references `_galaxy_servers`, replace with `_server_state.galaxy_servers if _server_state else []`.

Remove `import ansible_know.collections as collections` from lazy imports where it was used for module-level function calls. The tool handlers now access collections via `state.collection_manager`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_server.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ -v --tb=short`
Expected: All 438 tests PASS, 57 skipped

- [ ] **Step 6: Lint check**

Run: `.venv/bin/ruff check src/ansible_know/server.py`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "refactor: use ServerState and LifespanContext in server.py

Replaces untyped lifespan dict with LifespanContext TypedDict.
All tool handlers access state via _get_state(ctx) helper.
Module-level globals _version_info and _galaxy_servers replaced
by single _server_state reference for resource functions.

Closes #68.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Final verification and cleanup

**Files:**
- Possibly modify: any file with remaining references to old patterns

- [ ] **Step 1: Search for remaining references to old patterns**

```bash
grep -rn "_missing_collections\|_get_lifespan_resources\|_version_info\|_galaxy_servers" src/ tests/
grep -rn "from ansible_know.collections import ensure_collection\|from ansible_know.collections import get_collections_path\|from ansible_know.collections import list_installed" .
grep -rn "collections\.ensure_collection\|collections\.get_collections_path\|collections\.list_installed" src/ tests/
```

Expected: No hits for old module-level function imports. No hits for `_missing_collections`,
`_get_lifespan_resources`. `_version_info` only in `state.py` type annotation context.
`_server_state` in `server.py` only.

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Run lint**

Run: `.venv/bin/ruff check src/ tests/`
Expected: No errors

- [ ] **Step 4: Verify architecture compliance**

Check layer dependencies in `state.py`:
```bash
grep -n "^from ansible_know\|^import ansible_know" src/ansible_know/state.py
```
Expected: No runtime imports from upper layers. Only `TYPE_CHECKING` imports.

Check `collections.py` has no module-level mutable state:
```bash
grep -n "^_tmp_dir\|^_installed\|^_install_locks\|^_locks_lock\|^_install_gate" src/ansible_know/collections.py
```
Expected: No hits.

- [ ] **Step 5: Commit any cleanup**

If any stray references were found and fixed, commit them:
```bash
git add -A
git commit -m "refactor: clean up remaining references to old state patterns

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: PR architecture review and submission

**Skills:** Load `superpowers:requesting-code-review`. Read local skill `skills/pr-architecture-review/SKILL.md` and apply its full checklist.

- [ ] **Step 1: Run PR architecture review**

Read `skills/pr-architecture-review/SKILL.md` and apply every step against the full diff:

```bash
git diff main...HEAD
```

Check all 8 steps from the skill:
1. Identify changed files and affected layers
2. Check layer dependency rules (no new violations)
3. Check type contracts (TypedDict usage, exception types)
4. Check async/sync boundary (run_in_executor wrapping)
5. Check state management (thread safety, BoundedCache usage)
6. Check public API surface (__all__, ToolAnnotations)
7. PEP 8 and Python standards
8. Security review (input validation, path traversal, error sanitization)

Fix any findings in-place. Commit fixes if needed.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin worktree-issue-68-session-isolation
```

Open PR against `main` with:
- Title: `refactor: encapsulate server state for session isolation (#68)`
- Body: summary of changes, violations addressed (V-S3, V-T1, V-E4, V-S2), test results
- Reference: Closes #68

- [ ] **Step 3: Post review summary on PR**

Post a comment on the PR with:
- Architecture review results (findings, what was fixed, what was deferred)
- Test verification results (test count, pass/fail)
- Violations addressed vs remaining
