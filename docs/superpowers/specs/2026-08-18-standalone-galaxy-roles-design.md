# Spec: Standalone Galaxy Role Search and Documentation

**Status:** Proposed  
**Date:** 2026-08-18  
**Issue:** [#230](https://github.com/leogallego/ansible-know-mcp/issues/230)  
**Repo:** ansible-know-mcp (`leogallego/ansible-know-mcp`)  
**Audience:** Implementation session in this repo only

> **For agentic workers:** Use superpowers:writing-plans to create an
> implementation plan from this spec. Do not implement from this document
> alone.

---

## 1. Problem

Galaxy **standalone roles** (legacy v1 roles such as
`ansible-lockdown.rhel9_cis`) are not discoverable through this MCP server.

Existing role tools only cover roles **inside collections**:

| Tool | Identifier | API |
|------|------------|-----|
| `search_collections` | collection FQCN | Galaxy **v3** |
| `get_role_doc` | 3-part FQCN `ns.collection.role` | local ansible-doc → v3 docs-blob `readme_html` |
| `get_collection_manifest` | `ns.collection` | v3 contents |

Standalone roles live on the Galaxy **v1** API. They have no docs-blob, no
3-part FQCN, and were explicitly out of scope in
`docs/superpowers/specs/2026-06-11-role-support-design.md`.

The June spec assumed READMEs existed only on GitHub and would need a GitHub
API. That is outdated: galaxy-ng stores rendered README HTML at import time
and serves it at `GET /api/v1/roles/{id}/content/`.

---

## 2. Goals

1. Add `search_standalone_roles` — keyword search of Galaxy standalone roles.
2. Add `get_standalone_role_doc` — structured docs for one standalone role.
3. Keep Galaxy **v3** collection/module/plugin/collection-role paths unchanged.
   A v1 404, 410, or removal must not fail v3 discovery or tools.
4. Reuse `parse_role_readme()` on Galaxy-stored HTML (same as collection
   `get_role_doc` Galaxy fallback).

---

## 3. Non-goals (follow-up issues)

Create separate issues after this one lands; do **not** implement here:

| Follow-up | Why deferred |
|-----------|----------------|
| GitHub README fallback when `readme_html` is empty | Extra host allowlist; v1 `/content/` is the Galaxy UI path |
| `generate_standalone_role_skill` | Needs 2-part layout + template copy; ~25–30% extra on top of this spec |
| `ansible-galaxy role install` / ensure-role | Write path; different from `ensure_collection` |

Also out of scope:

- Extending `search_collections` or `get_role_doc` to accept 2-part names
- Local `ansible-doc -t role` for standalone roles
- Skill listing / AGENTS.md / Lola packaging for standalone roles
- Changing `GalaxyDocClient` / v3 discovery to require v1

---

## 4. Architecture

**Approach B:** a dedicated `GalaxyV1Client` that shares **transport only**
with `GalaxyClient`.

```text
server.py
  search_standalone_roles / get_standalone_role_doc
    → resolution.search_standalone_roles
    → resolution.resolve_standalone_role_doc
        → GalaxyV1Client   (v1 only)
            → shared httpx.AsyncClient + GalaxyServerConfig
            → _select_http_client (skip shared client when validate_certs is false)

GalaxyClient               (v3 only — unchanged)
  via GalaxyDocClient Protocol
```

**Shared (transport):** the lifespan `httpx.AsyncClient`, TLS verify flag, and
credentials on `GalaxyServerConfig` (token / basic / SSO).
`GalaxyV1Client.from_config(config, http_client=...)` mirrors
`GalaxyClient.from_config`.

**Not shared:** API-root discovery, URL builders, `_discovery_failed`,
v3 caches, `GalaxyDocClient` Protocol.

Prefer a new module `src/ansible_know/galaxy_v1.py` (External Access layer)
so deleting v1 later is one file plus the two tools and two resolution
functions. Do not add v1 methods to `GalaxyClient`.

`readme_parser.py` gains a second consumer (`galaxy_v1.py`) in addition to
`galaxy.py`. No parser API change.

---

## 5. Verified galaxy-ng v1 surface

Source: [ansible/galaxy_ng](https://github.com/ansible/galaxy_ng)
`galaxy_ng/app/api/v1/` (urls, filtersets, viewsets/roles, serializers).
Live-checked against `galaxy.ansible.com`.

### 5.1 Routes

`/api/v1/roles/` and `/api/v1/search/roles/` are the **same** list view
(`LegacyRolesViewSet.list`). Use `/api/v1/roles/` only.

| Operation | Method | Path |
|-----------|--------|------|
| Search / list | GET | `/api/v1/roles/` |
| Exact lookup | GET | `/api/v1/roles/?namespace={ns}&name={name}` |
| README | GET | `/api/v1/roles/{id}/content/` |
| Versions (unused here) | GET | `/api/v1/roles/{id}/versions/` |

Pagination: `page_size` default 10, max 1000 (`LegacyRolesSetPagination`).
List JSON is DRF: `{count, next, previous, results: [...]}` — not v3
`{data, meta}`. Parse `results`.

### 5.2 Filters that work

From `LegacyRoleFilter` — **not** `search=` or `keyword=` (those are ignored
and return the full catalog, ~37k roles).

| Param | Behavior |
|-------|----------|
| `keywords` | Case-sensitive `contains` on namespace name, role name, **or** description. Use this for search. |
| `autocomplete` | Same contains logic as `keywords`. Do not use (redundant). |
| `namespace` | Exact, case-insensitive (`__iexact`) |
| `name` | Exact (model field) |
| `tags` / `tag` | JSON list contains **one** tag string |
| `github_user` | Namespace name |
| `owner__username` | Namespace `__iexact` |
| `order_by` | `name`, `created`, `modified`, `download_count` (prefix `-` for desc) |

**Download-count side effect:** if **both** `owner__username` and `name` are
present, the list view increments the role's download counter (CLI install
detection). Exact lookup for docs **must** use `namespace` + `name`, which
the Galaxy UI uses and which does not increment counts.

### 5.3 Identifiers

Galaxy listing identity is `{username}.{name}` where `username` is the
**legacy namespace** (`obj.namespace.name`), not `github_user`.

The same GitHub repo can appear twice (e.g. `MindPointGroup.rhel9_cis` and
`ansible-lockdown.rhel9_cis`). Tools treat those as distinct roles.

Namespaces and role names may contain **hyphens** and mixed case
(`ansible-lockdown`, `elasticsearch-curator`). Existing `validate_fqcn` /
`validate_namespace` reject hyphens — do not reuse them.

### 5.4 Content payload

`GET /api/v1/roles/{id}/content/` →
`{"readme": "README.md", "readme_html": "<h1>..."}` from
`full_metadata` (`LegacyRoleContentSerializer`). This is the Galaxy UI
Documentation tab. No GitHub request.

List/detail payloads include `id`, `username`, `name`, `description`,
`github_user`, `github_repo`, `github_branch`, `download_count`,
`summary_fields.tags`, `summary_fields.versions`,
`summary_fields.dependencies`.

---

## 6. Client: `GalaxyV1Client`

File: `src/ansible_know/galaxy_v1.py`

### 6.1 Construction

```python
class GalaxyV1Client:
    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify: bool = True,
        server_name: str | None = None,
        auth_url: str | None = None,
        client_id: str | None = None,
    ) -> None: ...

    @classmethod
    def from_config(
        cls,
        config: GalaxyServerConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> GalaxyV1Client: ...
```

Context manager `aclose` of an **owned** client only (same as `GalaxyClient`).
When `http_client` is injected, do not close it.

Auth header construction may copy the SSO/token/basic pattern from
`GalaxyClient` (small duplication is acceptable; v1 will not grow). Do not
import or call `GalaxyClient._discover_api_root`.

### 6.2 Independent v1 discovery

Probe `base_url`, then `base_url + "/api"` if needed, for
`available_versions`. **Require `v1` only.** Do not inspect or require `v3`.
Do not set any flag on `GalaxyClient`.

- Missing `v1` → this client raises `GalaxyError` (“server does not support
  Galaxy API v1”). Resolution skips that server.
- Unsafe `v1` path (same rules as v3: no `..`, charset `[a-zA-Z0-9/_-]`) →
  `GalaxyError` for this client only.

URL builder: `{api_root}/{v1_path}roles/` and
`{api_root}/{v1_path}roles/{id}/content/`.

Do **not** import `galaxy.py` from `galaxy_v1.py` (circular import:
`galaxy.clear_cache` will lazy-import `galaxy_v1.clear_cache`). Duplicate
timeout and size-limit constants in `galaxy_v1.py`, or read them from
`config.py`. Import `GalaxyError` from `errors.py` only.

### 6.3 Methods

```python
async def search_roles(
    self, query: str, tags: str | None = None,
) -> dict[str, Any]:
    """GET roles/?keywords=&order_by=-download_count&page_size=10."""

async def fetch_role_by_name(
    self, namespace: str, name: str,
) -> dict[str, Any]:
    """GET roles/?namespace=&name=. Return the first result or raise."""

async def fetch_role_content(self, role_id: int) -> dict[str, Any]:
    """GET roles/{id}/content/. Return {readme, readme_html}."""

async def fetch_standalone_role_doc(
    self, role_name: str,
) -> tuple[dict[str, Any], DocProvenance]:
    """Split role_name, lookup, fetch content, parse_role_readme."""
```

`search_roles` params:

- `keywords` = query
- `order_by` = `-download_count`
- `page_size` = `10` (match v3 collection search `limit=10`)
- if `tags` is set, pass **one** `tags` value (see §7)

`fetch_standalone_role_doc` maps parsed README to the same metadata keys as
`GalaxyClient.fetch_role_doc` (`role_name`, `short_description`,
`entry_points.main.options`, `dependencies`, `examples`), plus v1 fields
`tags`, `latest_version`, `github_user`, `github_repo`, `github_branch`,
`download_count`.

Provenance:

- `doc_source`: `"galaxy_v1_readme"` when `readme_html` is non-empty
- `doc_source`: `"galaxy_v1_metadata"` when HTML is empty (metadata only;
  warning that README was missing; **no GitHub fetch**)
- `doc_version`: latest `summary_fields.versions[0].name` when present
- `doc_source_server`: `server_name` when set
- `doc_warning`: best-effort README parse note when source is readme

Empty HTML is success, not an exception.

### 6.4 Cache

A **separate** `BoundedCache` for v1 search/content, keyed by
`(normalized_base_url, server_name, ...)` like v3. Do not write into
`_version_cache` / `_blob_cache`. TTL 3600s. Memory-only is fine (search
payloads are small; HTML can be large — cap cache entries, e.g. max_size 50).

**Operator switch:** the MCP `clear_cache` tool in `server.py` already
clears `galaxy` and `docs` as two independent modules. When `scope` is
`galaxy` or omitted, also call `galaxy_v1.clear_cache()`. Do **not**
import `galaxy_v1` from `galaxy.py` — that would be the only v3→v1
coupling. `galaxy.clear_cache()` stays v3-only. Add a `galaxy_v1` entry
to the tool's `cleared` list (e.g. `"galaxy_v1"`).

---

## 7. Tools

### 7.1 `search_standalone_roles`

Read-only. Parameters:

- `query: str` — `validate_query`
- `tags: str | None` — `validate_tags` when set

v1 `tags` is a **single** JSON-contains filter, not comma-AND. If the caller
passes comma-separated tags, send only the **first** segment to v1. Document
that in the tool docstring.

Resolution: query all configured Galaxy servers concurrently (same gather
pattern as `search_galaxy_collections`). Skip servers that raise (no v1,
404, timeout) — typical Automation Hub / certified / validated endpoints.
Do **not** restrict search to `galaxy.ansible.com` by URL; private
galaxy-ng may expose v1. Dedupe on `{username}.{name}`. Rank by
`download_count` descending (that ranking is the relevance pass — do not
permute letter-case variants).

Send ``keywords`` as **repeated** query params (Galaxy ``getlist`` AND):
tokenize on whitespace / hyphen / underscore, lowercase, drop stopwords
(``role``, ``manage``, ``want``, ``ansible``, …). ``win openssh`` becomes
``keywords=win&keywords=openssh``, which matches ``win_openssh``. A single
phrase ``keywords=win openssh`` does **not** match.

If the AND returns zero hits and more than one token remains, retry with
the **longest** token only (so ``windows openssh`` still finds
``win_openssh`` via ``openssh``).

Empty merged results are **success** (`count: 0`) whenever **at least one**
server returned a v1 response. A v1 200 with zero roles plus Hub
`does not support Galaxy API v1` errors is still success — not
`All Galaxy servers failed`. Raise `GalaxyError` only when **no** server
returns a v1 search response. Do not copy `search_galaxy_collections`'
`if not hits and errors` condition; that treats Hub-only failures plus
an empty public-Galaxy hit list as total failure.

Result TypedDict `StandaloneRoleSearchResult`:

```python
{
    "query": str,
    "count": int,
    "roles": [
        {
            "role_name": str,         # username.name (same key as get-doc)
            "description": str,
            "tags": list[str],
            "latest_version": str,
            "download_count": int,
            "github_user": str,
            "github_repo": str,
            "source": str,            # optional, server name
        },
        ...
    ],
}
```

Do not fetch `/content/` during search.

### 7.2 `get_standalone_role_doc`

Read-only. Parameter:

- `role_name: str` — `validate_standalone_role_name` (new)

Split into `(namespace, name)` on the **first** dot (exactly two segments).
Lowercase both segments. Keep namespace hyphens (`ansible-lockdown` is not
`ansible_lockdown`). For the role name, try the lowercase spelling first,
then the hyphen/underscore swap (`ssh-hardening` hits immediately;
`RHEL9-CIS` misses as `rhel9-cis` then hits as `rhel9_cis`). Lookup via
`namespace` + `name`, then `/content/`, then parse.

Result TypedDict `GetStandaloneRoleDocResult` — same optional fields as
`GetRoleDocResult` plus standalone extras:

| Field | Notes |
|-------|--------|
| `role_name` | Galaxy canonical `{username}.{name}` after lookup, not the raw caller spelling |
| `content_type` | `"standalone_role"` (not `"role"`) |
| `doc_source` | `galaxy_v1_readme` or `galaxy_v1_metadata` on success. Do not return a success payload with `unavailable` — that case is `ErrorResponse`. |
| `short_description`, `entry_points`, `dependencies`, `examples` | From parser when HTML present. If HTML is empty, `short_description` is the v1 `description` field; `dependencies` fall back to `summary_fields.dependencies`. When HTML is present, parser dependencies win; if the parser returns none, still fall back to `summary_fields.dependencies`. |
| `tags`, `latest_version`, `github_user`, `github_repo`, `github_branch` | From list payload |
| `doc_version`, `doc_warning`, `doc_source_server` | Provenance |
| `error` | Not used on the success TypedDict. Not-found and all-servers-failed are `ErrorResponse` (`{"error": str}`) only. |

Never return raw `readme_html`. Do not call `truncate_response` on the
result dict (it takes `str`; only skill-render tools use it). Structured
doc tools return the TypedDict as-is.

No local ansible-doc attempt.

### 7.3 Validation

New `validate_standalone_role_name(name: str) -> None` in `validation.py`.

- Exactly two segments separated by `.`
- Each segment: `[A-Za-z0-9][A-Za-z0-9_-]*` (hyphen + mixed case accepted; lookup canonicalizes)
- Length cap: reuse `MAX_NAMESPACE_LENGTH` per segment (128)
- Reject 3-part FQCNs with an error that points at `get_role_doc`
- Reject empty / path characters

Do not change `validate_fqcn` or `validate_namespace`.

### 7.4 Agent-facing copy

Server instructions and tool descriptions must say:

- Collection roles → `search_collections` / `get_role_doc` (3-part FQCN)
- Standalone Galaxy roles → these two tools (2-part `namespace.role`)

Update `CLAUDE.md` and `README.md` tool tables (this branch has **20** tools → **22**) and `server.py` module docstring / FastMCP `instructions` (add standalone search/get-doc to the workflow). Update
`docs/architecture/service-contracts.md` External Access table:
`galaxy_v1.py` → `resolution.py`; `readme_parser.py` consumers include
`galaxy_v1.py`. Layer map: `galaxy_v1.py` → External Access.

---

## 8. Resolution

`resolution.py` (Domain):

```python
async def search_standalone_roles(
    query: str,
    tags: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    v1_client_factory: GalaxyV1ClientFactory | None = None,
) -> dict[str, Any]: ...  # raises GalaxyError if every server fails

async def resolve_standalone_role_doc(
    role_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    v1_client_factory: GalaxyV1ClientFactory | None = None,
) -> GetStandaloneRoleDocResult | ErrorResponse: ...
```

`GalaxyV1ClientFactory` is a Protocol in `types.py` parallel to
`GalaxyClientFactory`, returning a context manager with
`search_roles` and `fetch_standalone_role_doc`. **Do not** extend
`GalaxyDocClient`.

Do **not** reuse `_try_galaxy_servers` as currently typed
(`GalaxyClientFactory` → `GalaxyDocClient`). Duplicate a small
`_try_v1_servers` with the same first-success / last-`GalaxyError` loop
and `GalaxyV1ClientFactory`. Do not genericize the v3 helper.

`server.py` injects a `_galaxy_v1_factory(ctx)` closure analogous to
`_galaxy_factory` (no enrichment semaphore). Orchestration: validate →
call resolution → return.

Try servers in configured order for get-doc (first success wins). Search
queries all servers and merges; **raises** `GalaxyError` only when every
server fails to return a v1 response. Empty hits from a v1-capable
server are success even if v1-less siblings error. Both paths must use
`_select_http_client` (do not inject the shared httpx client when
`validate_certs` is false).

Lookup: lowercase both segments. Try role-name as stored, then hyphen
swap / underscore swap. Search tokenizes into repeated ``keywords``
params; empty AND falls back to the longest token. Search does not
globally replace hyphens in free text (``ansible-lockdown`` as a
keyword must remain hyphenated until tokenized).

If public Galaxy is disabled (`ANSIBLE_KNOW_NO_PUBLIC_GALAXY=1`) and no
configured server speaks v1, the tools return the “does not support
standalone roles” error. That is expected — standalone roles are a public
Galaxy catalog.

---

## 9. Errors

| Case | Tool result |
|------|-------------|
| Validation failure | `{"error": str}` |
| No v1 on any configured server | `{"error": "... does not support standalone roles (Galaxy v1)"}` |
| Role not found | `{"error": "Standalone role '{name}' not found"}` |
| HTTP/timeout / no-v1 on a server (search) | Skip that server; a v1 200 with zero roles is success; raise `GalaxyError` only if **no** server returned v1 |
| HTTP/timeout on get-doc after all servers | `{"error": ...}` sanitized |
| Empty README HTML | Success with `doc_source: galaxy_v1_metadata`, v1 description/tags, and `doc_warning` |

v1 exceptions must not set `GalaxyClient._discovery_failed`.

---

## 10. Testing

Unit tests mock HTTP (no live Galaxy). Integration tests stay opt-in
(`--run-integration`).

| File | Coverage |
|------|----------|
| `tests/test_galaxy_v1.py` | Repeated ``keywords`` AND tokens; stopwords dropped; empty AND falls back to longest token; lookup tries hyphen form then underscore; hyphenated names like ``ssh-hardening`` hit first; namespace underscores are not rewritten to hyphens; ``tags`` first-segment only; lookup uses ``namespace``+``name`` **not** ``owner__username``; content parse → entry_points; empty HTML → ``galaxy_v1_metadata``; 404 on v1 → ``GalaxyError``; discovery requires v1 and does **not** require v3 |
| `tests/test_validation.py` | Accept hyphens/mixed case; reject 1-part, 3-part, empty, `/` |
| `tests/test_resolution.py` | Multi-server: v1-less server skipped, v1 server used; all-fail **raises** `GalaxyError`; search dedupe; empty hits succeed **including when v1-less siblings error** (the Hub+empty combination; testing skip-with-hits and empty-alone separately is not enough) |
| `tests/test_server.py` | Both tools success + validation error; `get_role_doc` still requires 3-part FQCN; `clear_cache(scope=galaxy)` also calls `galaxy_v1.clear_cache` |
| `tests/test_galaxy.py` | Existing v3 tests still pass (no v1 import) |
| `tests/integration/test_galaxy_api.py` | Optional live `keywords=rhel9_cis` and `get ansible-lockdown.rhel9_cis` |

**Isolation test (required):** construct both clients from the same config +
httpx mock. A v1 404 / missing `v1` in `available_versions` must not set
`GalaxyClient._discovery_failed` and must not change `_v3_path`. v3
`search_collections` / `fetch_module_doc` on that same `GalaxyClient`
instance must still succeed. A tautological “v3 works in a test that never
constructs V1Client” is not sufficient.

---

## 11. Files

| Path | Change |
|------|--------|
| `src/ansible_know/galaxy_v1.py` | **Create** — `GalaxyV1Client` |
| `src/ansible_know/validation.py` | Add `validate_standalone_role_name` |
| `src/ansible_know/types.py` | Search/doc TypedDicts + `GalaxyV1ClientFactory` Protocol |
| `src/ansible_know/resolution.py` | `search_standalone_roles`, `resolve_standalone_role_doc` |
| `src/ansible_know/server.py` | Two tools, factory, instructions, `clear_cache` also calls `galaxy_v1.clear_cache` |
| `src/ansible_know/galaxy.py` | **Unchanged** (v3-only; do not import v1) |
| `tests/test_galaxy_v1.py` | **Create** |
| `tests/test_validation.py` | New validator cases |
| `tests/test_resolution.py` | **Modify** — standalone search/get-doc resolution |
| `tests/test_server.py` | Tool wiring + cache-clear coupling |
| `CLAUDE.md` | Tool table |
| `docs/architecture/service-contracts.md` | Layer map + External Access row |
| `README.md` | Tool table (required — it lists tools) |

No template or `skills.py` changes in this issue.

---

## 12. Agent workflow (after implementation)

```text
search_standalone_roles("rhel9_cis")
  → {roles: [{role_name: "ansible-lockdown.rhel9_cis", download_count: ..., ...}]}

get_standalone_role_doc("ansible-lockdown.rhel9_cis")
  → {content_type: "standalone_role", doc_source: "galaxy_v1_readme",
     entry_points: {main: {options: [...]}}, ...}
```

Collection roles remain:

```text
search_collections("timesync") → get_role_doc("fedora.linux_system_roles.timesync")
```

---

## 13. Follow-up issues (file after merge, do not implement)

1. **GitHub README fallback** — if `readme_html` is empty, fetch
   `raw.githubusercontent.com/{github_user}/{github_repo}/{github_branch}/README.md`
   with host allowlist. Not required for the Galaxy UI path.
2. **`generate_standalone_role_skill`** — reuse `write_role_skill_package`
   once get-doc returns `GetRoleDocResult`-shaped metadata; 2-part directory
   layout and template compatibility line (no collection).
3. **Standalone role install** — `ansible-galaxy role install`; separate from
   `ensure_collection`.

---

## 14. Design decisions (locked)

| Decision | Choice |
|----------|--------|
| Client | Separate `GalaxyV1Client` (`galaxy_v1.py`), not methods on `GalaxyClient` |
| Transport | Shared lifespan httpx + `GalaxyServerConfig` auth |
| Search query param | `keywords` (not `search` / `keyword`) |
| Exact lookup | `namespace` + `name` (not `owner__username` + `name`) |
| README | v1 `/content/` `readme_html` + `parse_role_readme` |
| Identifier | `{username}.{name}` (Galaxy namespace) |
| Validator | New 2-part hyphen-safe validator |
| `content_type` | `standalone_role` |
| Local ansible-doc | No |
| GitHub / skills / install | Follow-up issues |
| v3 `clear_cache` | Stays v3-only; MCP tool also clears v1 |
| Multi-server get-doc | New `_try_v1_servers`; do not extend `GalaxyDocClient` |
| Search all-fail | Raise `GalaxyError` (match collection search) |
