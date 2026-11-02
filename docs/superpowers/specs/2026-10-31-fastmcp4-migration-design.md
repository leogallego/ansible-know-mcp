# FastMCP 4 upgrade + session-state migration — design

Date: 2026-10-31
Issue: #189 (actionable upgrade). Context: #125 (spec tracking), #261 (deferred
metadata follow-ups), #262 (deferred httpx → httpx2 evaluation), #263 (galaxy://installed
cross-client leak on HTTP, pre-existing).
Research: `docs/research/2026-10-23-fastmcp4-mcp-spec-189-125-research.md`.

---

## Intent and why

We depend on `fastmcp>=3.2,<4` (locked 3.2.4), which pulls `mcp` 1.27.0. Two things
force a move to FastMCP 4:

1. **Security — we ship a known-vulnerable dependency today.** `mcp` 1.27.0 is in
   range for **CVE-2026-59951** (GHSA-84m7-p3x7-pcfv), a published DoS
   (unbounded session accumulation) fixed in `mcp` 2.2.0. FastMCP 4.0.3+ pulls
   `mcp` 2.2.0 transitively. Our default `uvx`/stdio exposure doesn't trigger this
   particular CVE (it hits stateful Streamable HTTP), but the HTTP transport
   (`package_as_plugin` streamable-http option) is exposed, and the dependency is
   flagged on PyPI regardless.

2. **Correctness — a naive pin bump silently breaks us.** Our per-session state is
   keyed on `ctx.session_id`. In FastMCP 4, `ctx.session_id` returns a **new UUID on
   every tool call** on default stateless (2026-07-28) connections, across stdio,
   streamable-HTTP, and in-memory transports (PrefectHQ/fastmcp #5134; docs corrected
   in #5862). The moment anyone bumps the pin, every tool call spins up a brand-new
   `ServerState` + `CollectionManager` (new temp dir), `missing_collections` /
   `upgrade_warned` never persist, and the `_exit_stack` cleanup hook no longer fires.
   Session continuity is gone.

Because of (2), **the pin bump and the session rewrite cannot be shipped separately** —
a pin bump alone is a silent regression. They land together.

FastMCP 4 is GA (4.0.0 on 2026-08-31; current stable **4.1.4**, 2026-10-26), so #189's
original blocker ("don't ship users on 4.0.0b1") is resolved.

### Relationship to the MCP 2026-07-28 spec revision

This upgrade **is** our adoption of the MCP **2026-07-28** protocol revision — they are one
piece of work, not two. FastMCP 4 is the reference Python server framework; it rides on the
MCP Python SDK (`mcp`) **2.x**, and `mcp` 2.x is the implementation of the 2026-07-28
revision (`mcp` 2.0.0 GA'd on 2026-07-28, the same day as the spec). There is no way to
adopt 2026-07-28 in Python without `mcp` 2.x, and FastMCP 4 is what pulls it
(4.1.4 → `fastmcp-slim` → `mcp>=2.3.0`).

We are **not** on 2026-07-28 today: confirmed 2026-10-31 that our installed `mcp` 1.27.0
reports `LATEST_PROTOCOL_VERSION = 2025-11-25` (negotiating `2025-03-26` by default) — the
2026-07-28 revision isn't even known to our current SDK.

The spec revision is the **root cause** of this migration, not a side effect. Its headline
is a **stateless protocol** (SEP-2567 — `Mcp-Session-Id` removed). That is precisely why
`ctx.session_id` stops being a stable cross-call key under FastMCP 4, which is why our
`SessionManager` breaks and the session rewrite is required. Tracking: **#125** (spec
impact/migration tracker) and **#189** (this actionable upgrade); full RC-vs-final spec
deltas live in the research doc.

## Goals

- Upgrade to `fastmcp>=4.1.4,<4.2` (settled; see Constraints) — which is also our adoption
  of the MCP 2026-07-28 protocol revision (see above).
- Replace `ctx.session_id`-keyed session lookup with a **server-minted
  `collection_handle`** that survives across tool calls, correct for **both** stdio and
  remote/HTTP deployments.
- Remove the now-invalid private `_exit_stack` cleanup hook; rely on the existing
  TTL/LRU + periodic cleanup we already own.
- Move the "you're on an outdated version" notice off the **deprecated** protocol
  Logging channel (`ctx.warning`) to stderr and/or the existing `server://version`
  resource.
- Keep unit tests mock-based and green; update any test that constructs a `ctx` with
  `session_id` or asserts on `get_or_create(session_id)`; run integration + an MCP e2e
  against FastMCP 4.
- Clear the flagged transitive advisory stack (verify with `pip-audit` after the bump).

## Non-goals

- **No change to where `ensure_collection` installs.** It installs into an ephemeral
  `tempfile.TemporaryDirectory(prefix="ansible_know_")` purely so `ansible-doc` can
  introspect collections for discovery/docs. That it is *not* discoverable by the
  developer's Ansible runtime is intended — this is a knowledge tool, not a project
  bootstrapper. Any "install into the real project" idea is a separate issue.
- **Deferred metadata/polish (tracked in #261):** `openWorldHint`, `outputSchema` from
  TypedDicts, enabling actual caching on static resources, and the Tasks-extension
  evaluation. The caching-hints carve-out was investigated pre-implementation and **does
  not apply** — FastMCP 4 auto-populates the required `ttlMs`/`cacheScope` fields with safe
  defaults and never errors without them, so nothing is pulled into this spec (see Caching
  hints below).
- **No change to `redhat_docs.py`.** It is an outbound client to a server we don't
  control (`docs-mcp.api.redhat.com`) that still speaks `mcp-session-id`. Leave until
  upstream moves.
- No OAuth / CIMD / multi-tenant auth (only relevant if #71 remote multi-tenant moves
  forward). The `UserSession` API is explicitly *not* adopted — it requires auth we
  don't have in the default deployment.

## Anti-goals (what makes this a failure even if it "works")

- Shipping a version where two remote clients share one collection workspace. Remote
  isolation must be correct, not best-effort.
- A green unit suite that passes only because tests still assert the old
  `session_id` model — tests must exercise the handle model.
- Bundling optional polish into this PR and thereby delaying/muddying the critical
  security + correctness fix.

## Constraints

- **Must work remotely as well as locally.** Remote (streamable-HTTP) is a secondary but
  real target (#71) and must not regress. This is the load-bearing constraint on the
  handle design: since FastMCP 4 gives a fresh `session_id` per request even over HTTP,
  the server cannot distinguish remote clients from protocol state. A single
  per-process default workspace would make all remote clients collide — the exact
  unbounded-accumulation shape the CVE warns about. Therefore the handle must be
  **minted by us and threaded explicitly**; any per-process default is a stdio-only
  convenience that remote never relies on.
- Our per-session object is a **live, non-JSON-serializable** `CollectionManager`
  (wraps a temp dir + subprocess installs). It cannot live in FastMCP's JSON session
  store; we keep it in our own registry keyed by the handle.
- Python floor stays `>=3.10`. Confirmed (2026-10-31): every FastMCP 4.x including 4.1.4
  declares `requires-python >=3.10` — compatible, no change, no re-lock risk.
- Set an explicit `mcp>=2.2` floor so a re-lock cannot slip back onto a pre-fix `mcp`
  (transitive via `fastmcp-slim` 4.1.4 already resolves to ≥2.3.0).
- FastMCP version ceiling settled (2026-10-31): **`fastmcp>=4.1.4,<4.2`** — latest stable,
  no 4.2 GA / no 5.x yet; `<4.2` guards against between-minor breaking changes (idle
  expiry, `MultiAuth` scoping) and the `4.2.0a1` alpha. Revisit when 4.2 reaches GA.

## The decided design

### Session model: Option B — self-minted `collection_handle` (hybrid)

Chosen over FastMCP's native `SessionId`/`SessionProvider` and over `UserSession`
because (a) our per-session object isn't JSON-serializable so it can't use the native
store anyway, (b) we already own all the TTL/LRU/periodic-cleanup machinery, and (c)
the FastMCP session docs explicitly bless "skip SessionProvider, take your own id as an
argument" for app-minted ids. `UserSession` is unusable without auth.

Behavior:

- `ensure_collection` **mints and returns an opaque `collection_handle`** (uuid4) in
  its result.
- Stateful tools accept an **optional** `collection_handle: str | None = None`
  argument and resolve their `ServerState` from it.
- **stdio convenience:** when no handle is supplied **over stdio**, resolve to a single
  per-process **default handle**. Safe because stdio is one-client-per-process. Keeps
  existing local tool signatures working with no new argument the agent must thread.
- **remote correctness:** on remote/HTTP there is **no default handle**. A stateful tool
  called without a `collection_handle` **fails fast with a clear error** instructing the
  agent to call `ensure_collection` and thread the returned handle. Remote isolation is
  therefore guaranteed, not best-effort.

**Decided (transport-gated, fail-closed — revised 2026-10-31 after review finding B2):**
the default handle is available **only when the call is proven local**. Detection is
**per-request**, not a value captured once at startup: a module global set only in
`main()` is blind to `mcp.http_app()` ASGI mounting and embedded `mcp.run()`, which would
leave the transport "unknown" and silently share one workspace across remote clients — the
central anti-goal. Instead the resolver asks FastMCP per request whether an HTTP request is
in scope (e.g. `fastmcp`'s `get_http_request()` dependency, which returns a request under
HTTP and raises/returns nothing under stdio/in-memory — builder verifies the exact
accessor exists and behaves identically in FM3 and FM4).

Rule: **if the call cannot be proven local, require an explicit handle (fail closed).**
Only a proven-local (stdio/in-memory) call with no handle gets the `DEFAULT_HANDLE`. Any
"unknown transport" case is treated as remote. Record the mechanism in code comments + an
ADR note.

### Which tools get `collection_handle`

The "stateful" tools are those that today call `_get_state(ctx)` and depend on the
per-session `CollectionManager` / `missing_collections`. The builder enumerates them
from the current `_get_state` callers rather than from a hardcoded list here; at minimum
this includes `ensure_collection`, `search_modules`, `search_plugins`, `get_module_doc`,
`get_plugin_doc`, `get_role_doc`, `get_collection_manifest`, `get_collection_docs`, and the
skill generators. Read-only tools that never touch session state do not gain the argument.

**Resources do NOT gain `collection_handle`** (corrected 2026-10-31 after review finding
N1): MCP resources cannot take tool arguments, so `galaxy://installed` cannot accept a
handle. It reads `SessionManager.all_installed_collections` (the union across all handles).
Leave it as-is for this migration. Note that on a multi-client HTTP deployment this union
leaks every client's installed collections across clients — a pre-existing isolation gap
not introduced by this work; tracked separately in **#263** rather than fixed here, since
per-client resource scoping is a larger change (resources can't take a handle argument).

### `state.py` — `SessionManager` becomes a handle registry

- `get_or_create(handle)` / `remove_session(handle)` keyed on a server-minted token
  instead of `session_id`. The internal dicts, lock, and fast-path stay as-is.
- TTL eviction, LRU eviction, `cleanup_stale_sessions`, `on_version_update`,
  `all_installed_collections`, `DEFAULT_SESSION_TTL` / `DEFAULT_MAX_SESSIONS` /
  `SESSION_CLEANUP_INTERVAL`, and env overrides (`ANSIBLE_KNOW_SESSION_TTL`,
  `ANSIBLE_KNOW_MAX_SESSIONS`) all stay. This is the key reason Option B is cheap: the
  lifecycle machinery does **not** depend on protocol sessions, only on the key.
- Add a way to mint a new handle and a well-known default handle constant.
- **Handle authenticity (added 2026-10-31 after review finding S1):** separate "get or
  create" from "get existing". `get_or_create(handle)` (create-on-miss) is used **only** by
  the mint path (`ensure_collection`) and for the stdio `DEFAULT_HANDLE`. A consumer tool
  that is handed an explicit handle must resolve via a **get-existing** path that returns
  `None` on miss — the resolver then raises a clear "unknown or expired handle — call
  ensure_collection" error instead of silently materializing a fresh empty `ServerState`.
  Rationale: `is_valid_handle` is only a *format* check (32-char hex); without this, a
  remote caller can spray well-formed random handles through a consumer tool, each creating
  a session and thrashing the LRU cap (`DEFAULT_MAX_SESSIONS`) to evict legitimate clients'
  workspaces — a CWE-400 availability attack, the same class as CVE-2026-59951, on the very
  transport this migration hardens. (The LRU bounds memory, so this is eviction-thrash, not
  unbounded growth — but it is still a real DoS and cheap to close.) It also gives the
  genuine TTL-eviction case a clean message instead of a silently-empty workspace.

### `server.py`

- `_get_state(ctx)` stops reading `ctx.session_id`. It resolves the handle from the
  explicit `collection_handle` tool argument (per the fail-closed transport rule above),
  then calls get-or-create (mint/default path) or get-existing (consumer path) per the
  handle-authenticity rule. `_get_state` grows `collection_handle` and a `mint_if_missing`
  flag (or an equivalent resolver helper — builder's call).
- **Delete** the `ctx.session._exit_stack` / `_cleanup_session` /
  `_ansible_know_cleanup_registered` block (currently ~lines 226-237). It depends on a
  stable per-connection `ctx.session` that no longer exists. Cleanup is handled by
  TTL/LRU + `_periodic_session_cleanup`, which already run.
- `ensure_collection` returns the handle in its result. **The handle is populated in the
  `server.py` tool, not in `collections.py`** (revised 2026-10-31 after review finding S3):
  `CollectionManager.ensure_collection` (Foundation/Domain) has no concept of a session
  handle, so `handle` must be `NotRequired` on `EnsureCollectionResult` and merged in by the
  tool after resolving — never set inside `collections.py`.
- The ephemeral no-ctx path in `_get_state` (returns a throwaway `ServerState`) stays as
  a fallback.

### Upgrade-notice logging move

- `_maybe_warn_upgrade` currently calls `await ctx.warning(...)`. Protocol Logging is
  deprecated (SEP-2577, 12-month runway — still functional, not removed). Move the
  notice to **stderr** (plain `logger.warning` to the existing `ansible_know` logger,
  which already goes to stderr) and keep the `server://version` resource as the
  structured source of truth. Drop the `ctx.warning` call. Preserve the
  once-per-session `upgrade_warned` de-dup behavior (now per handle).
- **`_maybe_warn_upgrade` must be handle-aware and best-effort (added 2026-10-31 after
  review finding B1 — this is a BLOCKER, implement with Tasks 2/3, not deferred to the
  logging task):** today `_maybe_warn_upgrade(ctx)` calls `_get_state(ctx)` with no handle,
  unconditionally, and is invoked at the **top of ~13 tools including `ensure_collection`**,
  *before* their try blocks. Once `_get_state` can raise on a missing/invalid handle, this
  would raise an **unhandled** error on every remote warning-tool call — and crash
  `ensure_collection` (the mint entry point) before it ever mints, killing the entire HTTP
  story. Fix: (a) thread the tool's `collection_handle` into `_maybe_warn_upgrade` so the
  per-handle `upgrade_warned` de-dup works when a valid handle is present; (b) the upgrade
  notice must **never** be the thing that fails a tool — if the handle can't be resolved,
  **skip the notice** (do not raise, do not mint). The clean "missing/unknown handle" error
  must originate from the tool's own `_get_state` inside its try block, not from the warning
  helper.

### Caching hints (carve-out — RESOLVED, nothing to do)

Researched and settled pre-implementation (2026-10-31). **FastMCP 4 auto-populates the
now-REQUIRED `ttlMs`/`cacheScope` (`CacheableResult`) fields on every cacheable result**
(`tools/list`, `prompts/list`, `resources/list`, `resources/read`,
`resources/templates/list`). Without any configuration it fills the conservative default
`ttlMs=0, cacheScope="private"` ("don't reuse"); it does **not** error or warn when the
server leaves them unset (verified on the wire with FastMCP 4.1.3 — SEP-2549 support
added in 4.1.0). Opting into real caching is done via `FastMCP(cache_ttl=..., cache_scope=...)`
constructor args or per-handler overrides (per-field precedence via `apply_cache_hint`).

**Conclusion:** the carve-out condition is **not** triggered — no caching-hint work enters
this PR. Enabling actual caching on static resources (`docs://sources`, `server://version`)
is pure optional polish and stays **deferred in #261**.

### HTTP idle expiry (researched 2026-10-31 — no conflict)

FastMCP 4.1.0 made HTTP sessions "expire after the MCP SDK's 30-minute idle default
instead of never." This is a **transport-layer** connection timeout (and part of the same
memory-accumulation mitigation family as CVE-2026-59951), distinct from both FastMCP's
application-level Session State and our handle registry. After this migration our
collection state is keyed on our **own** `collection_handle` in our **own** registry,
governed by our **own** TTL (`DEFAULT_SESSION_TTL` = 4h, `ANSIBLE_KNOW_SESSION_TTL`), and
no longer touches `ctx.session` / `ctx.session_id`. Therefore the 30-min idle expiry
**cannot drop our collection state**: an idled-out HTTP client reconnects, passes the same
`collection_handle`, and resolves the same `ServerState` (within our TTL). The handle-based
design is deliberately robust to transport churn — exactly what remote/HTTP needs. No code
change or pin adjustment required; the only test implication is an HTTP e2e that exercises
reconnect + same-handle round-trip (see Tests).

### Dependency / packaging (versions settled 2026-10-31 from PyPI)

Facts as of 2026-10-31: latest stable **FastMCP 4.1.4** (2026-10-26). There is **no 4.2
GA** (only `4.2.0a1` alpha, 2026-10-27) and **no 5.x**. Every 4.x release declares
`requires-python >=3.10`, matching our floor — no re-lock risk, no floor change. FastMCP 4
is a meta-package: `fastmcp` → `fastmcp-slim[client,server]` → **`mcp>=2.3.0,<3.0.0`**, so
the CVE-2026-59951 fix (`mcp` 2.2.0) is already exceeded transitively. `fastmcp-slim`
4.1.4 also pulls the remediated `starlette>=1.0.1`, `pyjwt>=2.12.0`, `joserfc>=1.5.0`,
`python-multipart>=0.0.26`, `certifi>=2026.10.19`.

- `pyproject.toml`: `fastmcp>=3.2,<4` → **`fastmcp>=4.1.4,<4.2`** — pin to the latest
  stable minor line (user preference: prefer latest, pin to it). `<4.2` excludes the
  `4.2.0a1` alpha and any future 4.2 minor that could carry breaking changes (4.1.x
  already introduced breaking changes between minors — HTTP idle expiry, `MultiAuth`
  client-ID scoping). Revisit the ceiling when 4.2 reaches GA.
- Add an explicit **`mcp>=2.2`** floor as a documented CVE guard (transitive already
  resolves to ≥2.3.0; the explicit floor prevents a future re-lock from slipping back).
- FastMCP 4 uses **`httpx2`** internally (Pydantic's next-gen HTTP client;
  `fastmcp-slim` → `httpx2>=2.5.0`). Our own client code (`docs.py`, `galaxy.py`,
  `galaxy_v1.py`, `redhat_docs.py`, lifespan) keeps classic **`httpx`**. These are
  distinct packages and coexist with no conflict — on Python 3.10, pip resolves httpx2 to
  ≤2.14.0 (2.15.0+ requires 3.11), so FastMCP 4 installs cleanly on our `>=3.10` floor.
  **This spec does not migrate our code to httpx2** — that is deferred to **#262** (it
  carries a Python-floor decision and a breaking API migration, orthogonal to this work).
  Implementation only needs a smoke-check: confirm the lock resolves and our
  `httpx.AsyncClient` usage still imports/runs alongside `httpx2`.
- Resolve any leftover `uv` prerelease constraints from when 4.x was a prerelease — no
  longer needed now that 4.x is stable.
- After the bump, run `pip-audit` and confirm the flagged transitive stack
  (starlette / pyjwt / cryptography / python-multipart / joserfc) clears.

### Tests

- Unit tests mock `_run_ansible_doc`, not FastMCP internals, so most are unaffected.
- Update any test that builds a `ctx` with `session_id` or asserts on
  `get_or_create(session_id)` to the handle model.
- Add tests for: handle minting + round-trip (`ensure_collection` → handle → stateful
  tool resolves same state), stdio default-handle fallback, **remote-without-handle fails
  fast with the clear error** (transport-gated), **remote consumer tool with a well-formed
  but never-minted handle gets an "unknown or expired handle" error and does NOT create a
  session** (authenticity, finding S1), two distinct handles get isolated `ServerState`
  (the remote-isolation guarantee), and TTL/LRU still evict by handle.
- **Tests must not pass while HTTP is broken (finding S2):** the HTTP-with-valid-handle
  round-trip test must drive a tool that calls `_maybe_warn_upgrade` (e.g. `get_module_doc`),
  not only `search_modules` (which has no warn call and would mask finding B1). The e2e must
  run against a **real in-process HTTP server + client** (not the in-memory `Client(mcp)`,
  whose transport is never HTTP and so exercises only the default-handle branch), and must
  assert **two concurrent clients stay isolated** and a **reconnected client resolves the
  same handle**. The in-memory `Client(mcp)` check may remain as an additional smoke test.
- Run integration (`--run-integration`) + the HTTP e2e above against FastMCP 4.
- Optionally wire the `@modelcontextprotocol/conformance` suite as a non-blocking CI job
  (not a prerequisite — FastMCP 4 is already conformance-tested upstream).

## Architecture / contracts

`state.py` is Foundation-layer (no runtime imports from Domain / External Access /
Orchestration; `CollectionManager` under `TYPE_CHECKING` only). The handle-registry
change must preserve that — do not introduce new runtime imports. Run the `git-review`
skill against the branch per `.git-pipeline.yml`; update
`docs/architecture/service-contracts.md` / ADR-0001 if any transport assumption changes.

## What's left to the builder

- Exact shape of the handle resolver in `server.py` (extra param on `_get_state` vs a
  helper). (Transport-gating is decided above — per-request, fail-closed — not open.)
- Verify the exact FastMCP per-request HTTP accessor (`get_http_request` or equivalent) and
  that it behaves identically in FM3 and FM4. Distinct error messages (finding N3): a
  **"collection_handle is required on remote connections"** message for the fail-closed
  missing-handle case, vs an **"unknown or expired handle — call ensure_collection"**
  message for a well-formed handle that doesn't resolve, vs an **"invalid handle"** message
  for a malformed value (don't tell a stdio user it's "remote-only").
- Confirm the `httpx` / `httpx2` coexistence on the actual lock (see Dependency section).
- Enumerating the exact set of tools that gain `collection_handle` from the current
  `_get_state` callers.
- Follow the plan produced next (superpowers:writing-plans) for sequencing and TDD.

(Settled pre-implementation and no longer open: FastMCP version + ceiling
(`fastmcp>=4.1.4,<4.2`), Python floor (`>=3.10`, compatible), the 30-min HTTP idle-expiry
interaction (no conflict), and the caching-hints carve-out (auto-handled, deferred).)

## Acceptance

- `fastmcp>=4.1.4,<4.2` + `mcp>=2.2` (transitive ≥2.3) installed; `pip-audit` clean.
- Session continuity works across multiple tool calls under FastMCP 4 on **both** stdio
  and streamable-HTTP; two handles stay isolated; a reconnected HTTP client resolves the
  same state via its handle.
- A well-formed but never-minted handle on a remote consumer tool returns an error and
  creates **no** session (finding S1); `_maybe_warn_upgrade` never fails a tool and never
  crashes `ensure_collection` on remote (finding B1); the transport default is fail-closed
  (finding B2).
- `_exit_stack` block removed; no reliance on `ctx.session_id` for cross-call state.
- Upgrade notice no longer uses `ctx.warning`; `server://version` still correct.
- Unit + integration + MCP e2e green against FastMCP 4, including the real-HTTP two-client
  isolation + reconnect test (finding S2).
