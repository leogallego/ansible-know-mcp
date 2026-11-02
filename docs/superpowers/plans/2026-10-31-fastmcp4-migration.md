# FastMCP 4 upgrade + session-state migration Implementation Plan

> **Skills setup — see this session's execution prompt.** This work uses three skill
> sets: (1) **superpowers:test-driven-development** — registered; load via the Skill tool.
> (2) The **pep8-review** PEP 8 review set — the `pep8-review` orchestrator plus its nine
> category sub-skills (`pep8-code-layout`, `pep8-imports`, `pep8-string-quotes`,
> `pep8-whitespace`, `pep8-trailing-commas`, `pep8-comments`, `pep8-naming`,
> `pep8-programming`, `pep8-type-annotations`). (3) Optionally **tighten-types** and
> **contract-docstrings** for this typed codebase. These come from the `ai-skills-python`
> collection; **how to load them is specified in this session's execution prompt** —
> follow that. After each task, apply `pep8-review` to the diff alongside
> `uv run ruff check src/ tests/`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Per-task workflow (every task):**
> 1. Load **superpowers:test-driven-development** (registered — via the `Skill` tool) and follow red → green → refactor (write failing tests first, confirm they fail, implement, confirm they pass). Run the project's full suite (`uv run pytest tests/`) before calling a task done, not just the task's own `-k` subset.
> 2. Before committing a task, run **`uv run ruff check src/ tests/`** and the **pep8-review** skill set (per the execution prompt's skills-setup) on the task's changed files; fix what they flag.
> 3. Commands use **uv** (this repo ships `uv.lock` and `.git-pipeline.yml` already drives `uv run pytest`): `uv sync`, `uv run pytest ...`, `uv run ruff check ...`, `uv run pip-audit`.
>
> **Code-snippet conventions (match the repo; ruff `E,F,I,UP,B,A,C4,T20`, `line-length = 120`, `target-version = py310`):** double-quoted strings, `X | None` unions (not `Optional`), built-in generics, no `print()` in `src/`, no builtin shadowing. The illustrative snippets below follow these; wrap any signature or message that would exceed 120 columns in the real code.

**Goal:** Migrate per-session state off the now-unstable `ctx.session_id` to a server-minted `collection_handle`, remove the invalid `_exit_stack` cleanup, move the upgrade notice off deprecated protocol Logging, and bump to FastMCP 4 (MCP 2026-07-28) — closing CVE-2026-59951 and fixing the latent session-continuity bug together.

**Architecture:** Option B (self-minted handle, hybrid). `SessionManager` stays a string-keyed registry; the key source changes from `ctx.session_id` to a handle that `ensure_collection` mints and returns and consumer tools thread back. Transport is detected **per request, fail-closed** (unknown ⇒ remote ⇒ require a handle); the default handle is a **proven-local**-only convenience. Handle lookup is split: the mint/default path may create a session, but an explicit handle resolves via **get-existing** (a well-formed but never-minted handle errors, it does not create a session). The handle rewrite is framework-agnostic and lands first under the current FastMCP 3; the dependency bump to FastMCP 4 is the final task.

**Tech Stack:** Python ≥3.10, FastMCP 4.1.4 (→ `mcp` ≥2.3 transitive), pytest (unit tests mock `_run_ansible_doc`; no ansible-core needed), `pip-audit`, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-31-fastmcp4-migration-design.md`

## Global Constraints

- Dependency pin: `fastmcp>=4.1.4,<4.2`; explicit floor `mcp>=2.2`.
- Python floor stays `>=3.10` (unchanged; FastMCP 4.1.4 is `requires-python >=3.10`).
- `state.py` is Foundation-layer: no runtime imports from Domain / External Access / Orchestration; `CollectionManager` stays `TYPE_CHECKING`-only. Do not add runtime imports.
- Do not migrate our own code to `httpx2` (deferred to #262); keep classic `httpx`.
- Do not change where `ensure_collection` installs (ephemeral temp dir, docs-only).
- Do not touch `redhat_docs.py` (outbound client; still `mcp-session-id`).
- Resources do **not** gain `collection_handle`; `galaxy://installed` is left as-is (cross-client union leak on HTTP tracked in #263, not fixed here).
- Remote isolation is correct, not best-effort: never let two HTTP clients silently share one workspace; transport detection is **fail-closed**.
- `handle` is a server/session concept: it is `NotRequired` on `EnsureCollectionResult` and populated in `server.py`, never in `collections.py`.
- Order: handle rewrite (Tasks 1–4) under current FastMCP 3, keeping the tree green; dependency bump (Task 5) last.
- **TODO(human) — attribution conflict:** `.git-pipeline.yml` sets `attribution: "Assisted-by: Cursor (Grok 4.5)"`, but this session's harness mandates `Co-Authored-By: Claude Opus 4.8` on commits and `🤖 Generated with Claude Code` on the PR body. Decide which trailer the commits/PR for this work should carry (or both) before pushing. The commit messages below omit the trailer pending that decision.

## Review Focus

- **Fail-closed transport detection** (Task 2, finding B2): the default handle is granted **only** when the call is proven local via a **per-request** FastMCP accessor (e.g. `get_http_request()`), never via a value captured once in `main()` (blind to `mcp.http_app()` ASGI mounts and embedded `mcp.run()`). Any "unknown/ambiguous" transport ⇒ treated as remote ⇒ handle required. Test added in Task 2.
- **Handle authenticity** (Tasks 1–2, finding S1): an explicit, well-formed-but-never-minted handle must resolve via **get-existing** and **error without creating a session** (otherwise a remote caller sprays random valid-hex handles to thrash the LRU cap and evict legitimate workspaces — CWE-400, the CVE's own class). `get_or_create` (create-on-miss) is reserved for the mint path and the stdio default. Tests added in Tasks 1 and 2.
- **`_maybe_warn_upgrade` must never fail a tool** (Tasks 2–3, finding B1 — BLOCKER): it is called at the top of ~13 tools (including `ensure_collection`) *before* their try blocks and today calls `_get_state(ctx)` with no handle. It must become handle-aware (receives the tool's `collection_handle`) and **best-effort** (skip the notice if the handle can't resolve; never raise, never mint), so a missing/unknown handle surfaces as a clean error from the tool's own `_get_state` inside its try — not an unhandled crash, and never crashing the mint entry point. Tests added in Tasks 2 and 3.
- **Malformed / oversized `collection_handle`** (Task 2): rejected by `is_valid_handle` (default sentinel or 32-char lowercase hex) before it becomes a dict key, with a distinct "malformed" error. Test added in Task 2.
- **Three distinct error messages** (Task 2, finding N3): missing-on-remote vs unknown-or-expired vs malformed — each actionable and not misleading on stdio. Tests added in Task 2.
- **Tests must not be green while HTTP is broken** (finding S2): the HTTP round-trip test drives a warning tool (`get_module_doc`), not `search_modules`; isolation + reconnect run against a **real in-process HTTP server + client**, not the in-memory `Client(mcp)`. Added in Task 5.

---

### Task 1: Handle registry primitives + authenticity split in `state.py`

**Files:**
- Modify: `src/ansible_know/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `DEFAULT_HANDLE: str` — module constant, the proven-local default-workspace sentinel, value `"default"` (deliberately not a uuid).
  - `mint_handle() -> str` — returns `uuid.uuid4().hex` (32-char lowercase hex).
  - `is_valid_handle(handle: str) -> bool` — True iff `handle == DEFAULT_HANDLE` or it is 32-char lowercase hex (a minted handle). Length is therefore implicitly capped (format check only — authenticity is enforced by the get-existing split below, not here).
  - `SessionManager.get_or_create(handle: str)` / `remove_session(handle: str)` — unchanged behavior (already string-keyed); only the parameter name and docstrings change from `session_id` to `handle`. `get_or_create` **still creates on miss** — it is now reserved for the mint path (`ensure_collection`) and the stdio default.
  - `SessionManager.get_existing(handle: str) -> ServerState | None` — **new**. Returns the state for an already-registered handle and refreshes its last-accessed time; returns `None` on miss. Never creates. Lockless fast-path read, consistent with `get_or_create`.
  - **No `SharedState.transport` field** — transport is detected per request in Task 2, not captured at startup (finding B2). Do not add it.

- [ ] **Step 1: Write the failing tests** in `tests/test_state.py`

```python
def test_default_handle_is_stable_sentinel():
    from ansible_know.state import DEFAULT_HANDLE
    assert DEFAULT_HANDLE == "default"

def test_mint_handle_unique_hex():
    from ansible_know.state import mint_handle, is_valid_handle
    a, b = mint_handle(), mint_handle()
    assert a != b
    assert len(a) == 32 and all(c in "0123456789abcdef" for c in a)
    assert is_valid_handle(a)

def test_is_valid_handle_rejects_garbage():
    from ansible_know.state import is_valid_handle, DEFAULT_HANDLE
    assert is_valid_handle(DEFAULT_HANDLE)
    assert not is_valid_handle("../etc/passwd")
    assert not is_valid_handle("x" * 500)
    assert not is_valid_handle("not-hex-zzzz")
    assert not is_valid_handle("ABCDEF0123456789ABCDEF0123456789")  # uppercase not minted shape

async def test_get_existing_returns_none_for_unminted_handle():
    # authenticity: a well-formed handle we never created must NOT be materialized
    mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
    assert await mgr.get_existing(mint_handle()) is None
    assert mgr.session_count == 0  # no session created as a side effect

async def test_get_existing_returns_state_after_create():
    mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
    h = mint_handle()
    created = await mgr.get_or_create(h)
    assert await mgr.get_existing(h) is created

async def test_get_or_create_still_creates_on_miss():
    # get_or_create remains the mint/default path and does create
    mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
    state = await mgr.get_or_create(mint_handle())
    assert state.missing_collections == set()
```

> Note for the resyncing session: the old `test_shared_state_transport_defaults_none` is **dropped** (no transport field), and the old `test_get_or_create_unknown_handle_returns_fresh_empty_state` is **replaced** — `get_or_create` still creates on miss, but consumer tools now use `get_existing`, which must return `None` for an unminted handle.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -k "handle or get_existing or sentinel" -v`
Expected: FAIL (`ImportError` / `AttributeError` on new names).

- [ ] **Step 3: Implement the primitives in `src/ansible_know/state.py`**

Add `import uuid`. Add `DEFAULT_HANDLE = "default"`, `mint_handle()`, `is_valid_handle()` near the other module constants. Add `get_existing()` to `SessionManager` (lockless read + last-accessed refresh, `None` on miss). Rename the `session_id` parameter to `handle` in `get_or_create` / `remove_session` and update their docstrings (behavior unchanged — still a string-keyed dict). Keep all TTL/LRU/`cleanup_stale_sessions`/`on_version_update`/`all_installed_collections` logic and the env overrides exactly as-is. Do **not** add a `transport` field.

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS (new tests plus all existing `SessionManager` tests, which are already string-keyed).
Run: `uv run ruff check src/ansible_know/state.py tests/test_state.py` + pep8-review on the diff → clean.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/state.py tests/test_state.py
git commit -m "feat: add handle primitives and get-existing authenticity split to state registry"
```

---

### Task 2: Per-request fail-closed resolver in `server.py`, `_get_state` rewrite, handle-aware best-effort `_maybe_warn_upgrade`, remove `_exit_stack`

**Files:**
- Modify: `src/ansible_know/server.py` (`_get_state` ~221-240, remove `_exit_stack` block ~226-237; add the per-request local-detection helper and resolver; make `_maybe_warn_upgrade` ~259-272 handle-aware + best-effort)
- Modify: `src/ansible_know/errors.py` (add three handle error classes)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `DEFAULT_HANDLE`, `mint_handle`, `is_valid_handle`, `SessionManager.get_existing` (Task 1).
- Produces:
  - In `errors.py`, three distinct subclasses of `AnsibleKnowError` (finding N3):
    - `MissingHandleError` — `"collection_handle is required on remote connections. Call ensure_collection first and pass the returned handle."`
    - `UnknownHandleError` — `"Unknown or expired collection_handle. Call ensure_collection to obtain a fresh handle."`
    - `InvalidHandleError` — `"Malformed collection_handle."`
  - `_is_local_request(ctx: Context | None = None) -> bool` — **per-request, fail-closed** transport detection. Returns `True` only when the call is proven local (stdio / in-memory), i.e. FastMCP reports **no HTTP request in scope**. Uses FastMCP's per-request accessor (e.g. `from fastmcp.server.dependencies import get_http_request`; a returned request ⇒ remote ⇒ `False`; the documented "no HTTP request" signal ⇒ `True`). **Any ambiguity/unexpected error ⇒ return `False` (treat as remote).** Builder verifies the exact accessor and its no-request signal exist and behave identically in FM3 and FM4 (spec "What's left to the builder").
  - `_resolve_handle(ctx, collection_handle, *, mint_if_missing) -> tuple[str, bool]` — returns `(handle, allow_create)`:
    1. `collection_handle is not None`: if not `is_valid_handle(...)` → raise `InvalidHandleError`; else → `(collection_handle, False)` (explicit handle ⇒ must already exist).
    2. elif `_is_local_request(ctx)` → `(DEFAULT_HANDLE, True)`.
    3. elif `mint_if_missing` → `(mint_handle(), True)`.
    4. else → raise `MissingHandleError`.
  - `_get_state(ctx: Context | None, collection_handle: str | None = None, *, mint_if_missing: bool = False) -> ServerState` — resolves `(handle, allow_create)`, then:
    - `allow_create` → `await sessions.get_or_create(handle)`.
    - not `allow_create` → `await sessions.get_existing(handle)`; if `None` → raise `UnknownHandleError`.
    - No more `ctx.session_id` / `_exit_stack`. The `ctx is None` ephemeral path (throwaway `ServerState`) is unchanged.
  - `ensure_collection` (Task 3) obtains the handle string via `_resolve_handle(ctx, collection_handle, mint_if_missing=True)` so the returned `handle` matches the state actually used.
  - `_maybe_warn_upgrade(ctx, collection_handle: str | None = None)` — **handle-aware and best-effort** (finding B1): it resolves state via `_get_state(ctx, collection_handle)` (no `mint_if_missing`) inside a `try`; on **any** handle error (`MissingHandleError` / `UnknownHandleError` / `InvalidHandleError`) it simply **returns without warning** (never raises, never mints). The per-handle `upgrade_warned` de-dup is preserved when a handle resolves.

- [ ] **Step 1: Write the failing tests** in `tests/test_server.py` (replace the `session_id`-based `_get_state` tests ~2313-2377 and delete `test_registers_cleanup_on_session_exit_stack`)

```python
# Patch the per-request detector so tests don't need a live transport.
# _local(True) => proven local (stdio); _local(False) => remote/HTTP.
def _patch_local(monkeypatch, value: bool):
    monkeypatch.setattr("ansible_know.server._is_local_request", lambda ctx=None: value)

async def test_get_state_local_uses_default_handle(monkeypatch):
    _patch_local(monkeypatch, True)
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    s1 = await _get_state(ctx)            # no handle, proven local
    s2 = await _get_state(ctx)
    assert s1 is s2                        # same default workspace

async def test_get_state_distinct_handles_isolated(monkeypatch):
    _patch_local(monkeypatch, False)       # remote
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    h1, h2 = mint_handle(), mint_handle()
    await _get_state(ctx, h1, mint_if_missing=True)   # create via mint path
    await _get_state(ctx, h2, mint_if_missing=True)
    assert await _get_state(ctx, h1) is not await _get_state(ctx, h2)

async def test_get_state_remote_without_handle_raises(monkeypatch):
    _patch_local(monkeypatch, False)
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    with pytest.raises(MissingHandleError):
        await _get_state(ctx)              # consumer path, no handle, remote

async def test_get_state_unknown_transport_requires_handle(monkeypatch):
    # fail-closed: detector cannot prove local -> treated as remote
    _patch_local(monkeypatch, False)
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    with pytest.raises(MissingHandleError):
        await _get_state(ctx)

async def test_get_state_wellformed_unminted_handle_errors_without_creating(monkeypatch):
    # authenticity (S1): explicit valid handle we never minted -> error, no session
    _patch_local(monkeypatch, False)
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    with pytest.raises(UnknownHandleError):
        await _get_state(ctx, mint_handle())   # well-formed, never created
    assert sessions.session_count == 0

async def test_get_state_rejects_malformed_handle(monkeypatch):
    _patch_local(monkeypatch, False)
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    with pytest.raises(InvalidHandleError):
        await _get_state(ctx, "../etc/passwd")

async def test_get_state_mint_if_missing_returns_state(monkeypatch):
    _patch_local(monkeypatch, False)       # remote
    sessions = SessionManager(SharedState(), collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, SharedState()); ctx.lifespan_context["sessions"] = sessions
    assert await _get_state(ctx, None, mint_if_missing=True) is not None  # ensure_collection path

async def test_maybe_warn_upgrade_best_effort_on_remote_without_handle(monkeypatch):
    # B1: the warning helper must NOT raise when the handle can't resolve
    _patch_local(monkeypatch, False)
    shared = SharedState(version_info={"installed": "0.1.0", "latest": "0.2.0",
                                       "outdated": True, "upgrade_command": "x"})
    sessions = SessionManager(shared, collection_factory=CollectionManager)
    ctx = _make_mock_ctx(None, shared); ctx.lifespan_context["sessions"] = sessions
    await _maybe_warn_upgrade(ctx)          # no handle, remote -> silently skips, no raise
```

Also update `_make_mock_ctx` to drop the `mock_ctx.session_id` line (no longer read).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -k "get_state or maybe_warn_upgrade_best_effort" -v`
Expected: FAIL (handle error classes undefined; `_get_state` / `_maybe_warn_upgrade` signature mismatch; `_is_local_request` missing).

- [ ] **Step 3: Implement**

Add `MissingHandleError`, `UnknownHandleError`, `InvalidHandleError` to `errors.py`. Add `_is_local_request(ctx)` (per-request, fail-closed) and `_resolve_handle(...)` per the Interfaces rule. Rewrite `_get_state` to use `_resolve_handle` + `get_or_create`/`get_existing`; delete the `ctx.session._exit_stack` / `_cleanup_session` / `_ansible_know_cleanup_registered` block. Make `_maybe_warn_upgrade` accept `collection_handle=None` and wrap its `_get_state` call in a `try/except` over the three handle errors (return on failure; never mint). Cleanup now relies solely on TTL/LRU + `_periodic_session_cleanup` (already running). Record the fail-closed transport mechanism in code comments. Give `_resolve_handle` / `_get_state` (and later `ensure_collection`) a **contract docstring** documenting the `Raises:` for each of the three handle errors and the "no session created on an unknown explicit handle" guarantee (apply the `contract-docstrings` skill — these are exactly the raise-on-precondition functions it targets).

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest tests/test_server.py -k "get_state or maybe_warn_upgrade" -v`
Expected: PASS.
Run: `uv run ruff check src/ansible_know/server.py src/ansible_know/errors.py tests/test_server.py` + pep8-review on the diff → clean.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py src/ansible_know/errors.py tests/test_server.py
git commit -m "feat: resolve session state by collection_handle (per-request fail-closed, authenticity); drop ctx.session_id and _exit_stack"
```

---

### Task 3: Thread `collection_handle` through stateful tools; `ensure_collection` mints & returns it; warn helper receives the handle

**Files:**
- Modify: `src/ansible_know/server.py` (every tool whose body calls `_get_state`, each tool's `_maybe_warn_upgrade` call, and `ensure_collection`)
- Modify: `src/ansible_know/types.py` (`EnsureCollectionResult`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `_get_state(ctx, collection_handle, mint_if_missing=...)`, `_resolve_handle`, `_maybe_warn_upgrade(ctx, collection_handle)` (Task 2).
- Produces:
  - `EnsureCollectionResult` gains `handle: str` as **`NotRequired`** (finding S3) — `CollectionManager.ensure_collection` in `collections.py` must **not** set it; the `server.py` tool merges it in after resolving. Import `NotRequired` (`typing` on ≥3.11, `typing_extensions` below — mirror the existing `TypedDict` guard in `types.py`).
  - Every stateful tool gains a parameter `collection_handle: Annotated[str | None, "Session handle returned by ensure_collection. Omit on local stdio; required on remote."] = None`, placed before `ctx`, threads it into `_get_state(ctx, collection_handle)`, **and** into its `_maybe_warn_upgrade(ctx, collection_handle)` call where one exists (finding B1).
  - `ensure_collection` resolves the handle with `mint_if_missing=True` (via `_resolve_handle` or by having `_get_state` expose it), uses it for state, and returns it in `result["handle"]`. It also passes `collection_handle` into its own `_maybe_warn_upgrade` call — which, being best-effort, will not crash the mint path if the passed handle is None on remote.
  - The set of tools to edit = every `@mcp.tool` function that today contains `state = await _get_state(ctx)`. Confirm the final list with `grep -n "_get_state(ctx)" src/ansible_know/server.py` (expected: `search_modules`, `search_plugins`, `get_module_doc`, `get_role_doc`, `get_plugin_doc`, `get_collection_manifest`, `get_collection_docs`, `ensure_collection`, `list_skills`, `get_skill`, `generate_skill`, `generate_role_skill`, `generate_plugin_skill`, `generate_collection_skills`). Resources are **excluded** (finding N1).
  - **Handle re-pass semantics (confirm with resyncing session):** an explicit handle passed to **any** tool — including `ensure_collection` — resolves via get-existing; a TTL-expired/unknown handle errors (`UnknownHandleError`) rather than silently re-creating. A remote client whose handle expired calls `ensure_collection` with **no** handle to mint a fresh one. Minting happens only on `handle=None` + remote.

- [ ] **Step 1: Write the failing tests** in `tests/test_server.py`

```python
# stdio_ctx: proven-local (monkeypatched); http_ctx: remote (monkeypatched).
# Use a real SessionManager so handles actually round-trip.

async def test_ensure_collection_returns_default_handle_local(monkeypatch, ...):
    _patch_local(monkeypatch, True)
    result = await ensure_collection("netbox.netbox", ctx=stdio_ctx)
    assert result["handle"] == DEFAULT_HANDLE

async def test_ensure_collection_http_mints_handle(monkeypatch, ...):
    _patch_local(monkeypatch, False)
    result = await ensure_collection("netbox.netbox", ctx=http_ctx)  # no handle in
    from ansible_know.state import is_valid_handle
    assert result["handle"] != DEFAULT_HANDLE and is_valid_handle(result["handle"])

async def test_ensure_collection_http_does_not_crash_in_warn(monkeypatch, ...):
    # B1 regression: version marked outdated so _maybe_warn_upgrade runs its body;
    # ensure_collection must still mint and return, not raise.
    _patch_local(monkeypatch, False)
    http_ctx.lifespan_context["shared"].version_info = {
        "installed": "0.1.0", "latest": "0.2.0", "outdated": True, "upgrade_command": "x"}
    result = await ensure_collection("netbox.netbox", ctx=http_ctx)
    assert "handle" in result and result["status"] in {"installed", "already_installed"}

async def test_warning_tool_roundtrips_handle_on_http(monkeypatch, ...):
    # S2: drive a tool that CALLS _maybe_warn_upgrade (get_module_doc), not search_modules.
    _patch_local(monkeypatch, False)
    h = (await ensure_collection("netbox.netbox", ctx=http_ctx))["handle"]
    out = await get_module_doc("netbox.netbox.device", collection_handle=h, ctx=http_ctx)
    assert "error" not in out

async def test_warning_tool_http_without_handle_errors(monkeypatch, ...):
    _patch_local(monkeypatch, False)
    out = await get_module_doc("netbox.netbox.device", ctx=http_ctx)  # no handle
    assert "error" in out and "ensure_collection" in out["error"]

async def test_consumer_tool_unknown_handle_errors(monkeypatch, ...):
    _patch_local(monkeypatch, False)
    out = await get_module_doc("netbox.netbox.device",
                               collection_handle=mint_handle(), ctx=http_ctx)  # never minted
    assert "error" in out and "ensure_collection" in out["error"]
```

Add `stdio`/`http` ctx fixtures built on `_make_mock_ctx` with a real `SessionManager`; reuse the existing `ensure_collection` / `get_module_doc` scaffolding (mocked `CollectionManager` / `_run_ansible_doc`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -k "handle or roundtrip or does_not_crash" -v`
Expected: FAIL (`handle` KeyError; `collection_handle` is an unexpected kwarg).

- [ ] **Step 3: Implement**

Add `handle: NotRequired[str]` to `EnsureCollectionResult` in `types.py`. Add the `collection_handle` parameter to each tool in the confirmed set, thread it into `_get_state` and into `_maybe_warn_upgrade`. In `ensure_collection`, resolve the handle with `mint_if_missing=True`, use it for state, and merge it into the returned dict (`result["handle"] = handle`) in `server.py` — never in `collections.py`. Update `ensure_collection`'s docstring to document the new `handle` key and when to pass `collection_handle`.

- [ ] **Step 4: Run tests to verify they pass, then the full server suite + lint**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS.
Run: `uv run ruff check src/ansible_know/server.py src/ansible_know/types.py tests/test_server.py` + pep8-review on the diff → clean.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py src/ansible_know/types.py tests/test_server.py
git commit -m "feat: thread collection_handle through tools and warn helper; ensure_collection returns handle"
```

---

### Task 4: Move the upgrade notice off deprecated protocol Logging

**Files:**
- Modify: `src/ansible_know/server.py` (`_maybe_warn_upgrade` ~259-272)
- Test: `tests/test_server.py`

> Scope note: handle-awareness + best-effort behavior already landed in Tasks 2/3 (finding B1). This task is **only** the logging-channel swap.

**Interfaces:**
- Consumes: `_get_state`, `_get_shared` (unchanged).
- Produces: `_maybe_warn_upgrade` no longer calls `ctx.warning`; it emits the notice via `logger.warning(...)` to the `ansible_know` logger (stderr). The per-handle `upgrade_warned` de-dup is preserved (set after emitting, only when a handle resolved). `server://version` resource is unchanged.

- [ ] **Step 1: Write the failing test** in `tests/test_server.py`

```python
async def test_upgrade_notice_uses_stderr_not_ctx_warning(caplog, monkeypatch, ...):
    _patch_local(monkeypatch, True)   # proven local -> default handle resolves
    shared = SharedState(version_info={"installed": "0.1.0", "latest": "0.2.0",
                                       "outdated": True,
                                       "upgrade_command": "pip install -U ansible-know-mcp"})
    ctx = _make_mock_ctx(None, shared)
    ctx.lifespan_context["sessions"] = SessionManager(shared, collection_factory=CollectionManager)
    with caplog.at_level("WARNING", logger="ansible_know"):
        await _maybe_warn_upgrade(ctx)
    assert any("outdated" in r.message for r in caplog.records)
    ctx.warning.assert_not_called()
    # de-dup: second call emits nothing new
    caplog.clear()
    with caplog.at_level("WARNING", logger="ansible_know"):
        await _maybe_warn_upgrade(ctx)
    assert not caplog.records
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "upgrade_notice_uses_stderr" -v`
Expected: FAIL (`ctx.warning` still called).

- [ ] **Step 3: Implement**

Replace `await ctx.warning(...)` with `logger.warning("ansible-know-mcp %s is outdated; latest is %s. Upgrade: %s", version_info["installed"], version_info["latest"], version_info["upgrade_command"])`. Keep the `state.upgrade_warned` guard and set it after logging.

- [ ] **Step 4: Run test to verify it passes, then lint**

Run: `uv run pytest tests/test_server.py -k "upgrade" -v`
Expected: PASS.
Run: `uv run ruff check src/ansible_know/server.py tests/test_server.py` + pep8-review on the diff → clean.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "feat: emit upgrade notice via stderr instead of deprecated protocol logging"
```

---

### Task 5: Bump to FastMCP 4, verify security + protocol + real-HTTP end-to-end

**Files:**
- Modify: `pyproject.toml` (dependency pins), `uv.lock` (relock)
- Modify: `docs/architecture/adr/0001-fastmcp-framework.md` and/or `docs/architecture/service-contracts.md` (only if a transport assumption changed)
- Test: `tests/test_server.py` (add a real in-process HTTP e2e + an in-memory smoke test), existing suite re-run

**Interfaces:**
- Consumes: all prior tasks (the codebase no longer depends on `ctx.session_id`).
- Produces: `fastmcp>=4.1.4,<4.2` and `mcp>=2.2` in `pyproject.toml`; a relocked `uv.lock`; a passing suite under FastMCP 4 including the real-HTTP isolation + reconnect test.

- [ ] **Step 1: Write the failing e2e tests** in `tests/test_server.py`

```python
async def test_protocol_revision_is_2026_07_28():
    # N2: don't hard-code the exact string brittly — assert we left the old SDK behind
    # and are on mcp 2.x / the new revision.
    import mcp.types as t
    assert t.LATEST_PROTOCOL_VERSION != "2025-11-25"
    from importlib.metadata import version
    assert int(version("mcp").split(".")[0]) >= 2

async def test_inmemory_handle_roundtrip_smoke_under_fastmcp4():
    # in-memory Client exercises the proven-local (default-handle) branch only.
    from fastmcp import Client
    from ansible_know.server import mcp
    async with Client(mcp) as client:
        res = await client.call_tool("ensure_collection", {"collection_namespace": "community.general"})
        handle = res.data["handle"]  # == DEFAULT_HANDLE in-memory
        out = await client.call_tool("get_module_doc",
                                     {"module_name": "community.general.ufw", "collection_handle": handle})
        assert "error" not in out.data

@pytest.mark.integration  # needs a real localhost socket
async def test_real_http_two_clients_isolated_and_reconnect():
    # S2: real in-process streamable-HTTP server; two clients must NOT share state,
    # and a reconnected client must resolve the SAME handle.
    import threading
    from fastmcp import Client
    from ansible_know.server import mcp
    # start mcp.run(transport="http") on an ephemeral port in a thread (helper),
    # wait for readiness, then:
    async with Client(http_url) as c1, Client(http_url) as c2:
        h1 = (await c1.call_tool("ensure_collection", {"collection_namespace": "community.general"})).data["handle"]
        h2 = (await c2.call_tool("ensure_collection", {"collection_namespace": "ansible.posix"})).data["handle"]
        assert h1 != h2 and h1 != "default" and h2 != "default"   # isolation + no shared default
        # c2's workspace must not see c1's collection
        with pytest.raises(Exception):
            await c2.call_tool("get_module_doc",
                               {"module_name": "community.general.ufw", "collection_handle": h2})
    # reconnect with the same handle resolves the same state (within TTL)
    async with Client(http_url) as c3:
        out = await c3.call_tool("get_module_doc",
                                 {"module_name": "community.general.ufw", "collection_handle": h1})
        assert "error" not in out.data
```

(Mock `CollectionManager.ensure_collection` / `_run_ansible_doc` as the existing suite does so no network/ansible-core is needed for the in-memory + unit paths. The real-HTTP test is `@pytest.mark.integration`; if the mocks are process-global they apply to the threaded server too — otherwise gate it behind `--run-integration`.)

- [ ] **Step 2: Run to verify it fails on the current stack**

Run: `uv run pytest tests/test_server.py -k "protocol_revision or inmemory_handle" -v`
Expected: FAIL (`LATEST_PROTOCOL_VERSION == "2025-11-25"` / `mcp` major `1` under installed `mcp` 1.27).

- [ ] **Step 3: Bump dependencies and relock**

Edit `pyproject.toml`: `fastmcp>=3.2,<4` → `fastmcp>=4.1.4,<4.2`; add `mcp>=2.2`. Remove any leftover `uv` prerelease constraints from when 4.x was a prerelease (spec Dependency section). Then:

Run: `uv lock && uv sync --extra dev`
Expected: resolves `fastmcp` 4.1.x and `mcp` ≥2.3; on Python 3.10 `httpx2` resolves to ≤2.14.0 alongside our `httpx`.

- [ ] **Step 4: Verify — tests, security, protocol, coexistence, transport kwarg**

Run: `uv run pytest tests/ -v` → Expected: PASS (full unit suite).
Run: `uv run pip-audit` → Expected: no advisories against `mcp` / `starlette` / `pyjwt` / `cryptography` / `python-multipart` / `joserfc`.
Run: `uv run python -c "import httpx, httpx2, ansible_know.server"` → Expected: imports cleanly (httpx/httpx2 coexist).
**Verify `mcp.run(transport="http")` is still accepted in FastMCP 4** (it was the alias in FM 2.x/3.x; `cli.py` passes `transport="http"` from `ServerConfig`). Confirm FM4 did not rename the alias — a quick `uv run ansible-know-mcp --transport http --port 0` smoke (or inspect FastMCP's `run` signature). If renamed, update `main()` / `cli.py` accordingly (note it in the changelog for the resyncing session).
Run: `uv run pytest tests/test_server.py -k "protocol_revision or inmemory_handle" -v` → Expected: PASS.
Run: `uv run pytest tests/ --run-integration -k "real_http or session or handle"` (real-HTTP isolation + reconnect) → Expected: PASS; record the result in the PR.

- [ ] **Step 5: Architecture review + docs + lint**

Run: invoke the `git-review` skill against the branch (per `.git-pipeline.yml`). Update ADR-0001 / service-contracts only if a transport or dependency contract actually changed (note the handle-based session model, the per-request fail-closed transport detection, and the `mcp`≥2.2 floor). Then:

Run: `uv run ruff check src/ tests/`
Expected: clean. (Plus pep8-review on the task diff.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/test_server.py docs/architecture
git commit -m "chore: upgrade to FastMCP 4 (MCP 2026-07-28); close CVE-2026-59951"
```
