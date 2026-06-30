# ADR 0004: Galaxy Fallback Chain with Multi-Server Support

## Status

Accepted

## Date

2026-06-19

## Context

The server needs to provide module and role documentation to AI agents. Users
may or may not have the relevant Ansible collection installed locally.
Additionally, users may operate multiple Galaxy-compatible servers:

- **Public Ansible Galaxy** (`galaxy.ansible.com`)
- **Private Automation Hub** (on-premise, authenticated)
- **Red Hat AAP Gateway** (SSO-authenticated, routing to Hub)

The documentation resolution strategy must handle:

1. Collections installed locally (available via `ansible-doc`).
2. Collections not installed locally but available on Galaxy.
3. Collections available on private Galaxy servers but not public Galaxy.
4. Multiple Galaxy servers with different authentication methods.
5. Graceful degradation when neither local nor Galaxy docs are available.

## Decision

Implement a three-tier resolution chain for documentation:

```
Local ansible-doc  →  Galaxy docs-blob API  →  Graceful degradation
```

With multi-server Galaxy support:

- Parse `ansible.cfg` `[galaxy]` `server_list` and `[galaxy_server.*]`
  sections for server configuration.
- Support per-server authentication: token, basic auth, or none.
- Support `ANSIBLE_GALAXY_SERVER_{NAME}_{KEY}` environment variable overrides.
- Try each configured server in priority order (as listed in `server_list`).
- For collection search, query all servers concurrently and merge results.
- Always include public Galaxy as a fallback unless explicitly disabled
  via `ANSIBLE_KNOW_NO_PUBLIC_GALAXY=1`.
- Cache the set of namespaces known to be missing locally
  (`_missing_collections`) to skip repeated `ansible-doc` failures.

## Consequences

### Positive

- **Zero-config for common case**: public Galaxy is always available as
  a fallback, so users get docs even without local installation.
- **Enterprise-ready**: private Automation Hub and AAP Gateway support
  means the server works in air-gapped or restricted environments.
- **Transparent to the consumer**: tool handlers get documentation without
  knowing the source. The `doc_source` field in the response indicates
  provenance (`local`, `galaxy`, `galaxy_readme`, `unavailable`).
- **Priority-ordered**: `server_list` order is respected, so users can
  prioritize their private Hub over public Galaxy.
- **Concurrent search**: `search_collections` queries all servers in
  parallel (`asyncio.gather`) and merges results by namespace.

### Negative

- ~~**Complexity in Orchestration**: the fallback logic (`_resolve_module_doc`,
  `_resolve_role_doc`) is currently in `server.py`, adding ~100 lines of
  business logic to the Orchestration layer. This should be in a Domain
  module (see V-D7, V-L2 in service-contracts.md).~~
  **Fixed (PR #66):** fallback logic now lives in `resolution.py` (Domain layer).
- **Inconsistent fallback strategies**: module docs use Galaxy docs-blob
  (structured API data), while role docs fall back to Galaxy
  `readme_html` parsing (best-effort HTML scraping). The quality of
  Galaxy-sourced role docs is inherently lower.
- **Cache invalidation**: `_missing_collections` is never cleared except
  by `ensure_collection()`. If a user installs a collection externally
  (outside the server), the cache will incorrectly skip local lookup.
- **Error handling complexity**: each Galaxy server can fail independently.
  The `_try_galaxy_servers` function must track the last error to re-raise
  if all servers fail, while `search_collections` must merge partial
  results with partial failures.

### Provenance Tracking

When documentation comes from Galaxy rather than local `ansible-doc`, the
response includes provenance metadata:

| Field | Description |
|-------|-------------|
| `doc_source` | `"local"`, `"galaxy"`, `"galaxy_readme"`, or `"unavailable"` |
| `doc_version` | The collection version from which docs were fetched |
| `doc_warning` | Warning that local version may differ |
| `doc_source_server` | Which Galaxy server provided the docs |

This allows AI agents to inform users when documentation may not match
their installed version.

### Future Considerations

- ~~Move fallback logic to a domain-level `resolution.py` module.~~
  **Done (PR #66):** `resolution.py` implements the fallback chain.
- Add a `_missing_collections` TTL or manual invalidation mechanism.
- Consider making Galaxy-first the default (cheaper than spawning
  `ansible-doc` subprocesses) with local as fallback for custom modules.
- ~~Support `auth_url` + `client_id` for SSO token refresh (AAP Gateway).~~
  **Done (PR #155):** `galaxy.py` exchanges offline tokens via SSO
  `auth_url`, with `client_id` support and retry-on-401 for token expiry.

## Implementation Notes

- `resolution.py` — `resolve_module_doc()`, `resolve_role_doc()`:
  local → Galaxy → graceful degradation
- `galaxy.py` — Galaxy v3 API client: version lookup, docs-blob fetch,
  collection search, format conversion, SSO token exchange
  (`_ensure_access_token()`), lazy API root discovery
  (`_discover_api_root()`, matching ansible-galaxy's `g_connect`),
  dynamic URL construction (`_build_v3_url()`)
- `readme_parser.py` — Galaxy role README HTML parsing (4 variable
  documentation patterns)
- `galaxy_config.py` — Galaxy server configuration parsed from `ansible.cfg`
  (`load_galaxy_servers()`, `GalaxyServerConfig` dataclass)
- `server.py:lifespan()` — Galaxy server initialization at startup

## Related Decisions

- [ADR-0002](0002-subprocess-ansible-doc.md) — local `ansible-doc` is the
  first tier in the fallback chain
- [ADR-0003](0003-module-level-state.md) — Galaxy caches use `BoundedCache`
- [ADR-0006](0006-upstream-first-integration.md) — multi-server Galaxy
  support is a P0 candidate for upstream contribution

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-19 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Initial decision |
| 2026-06-19 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Updated: resolution logic moved to resolution.py (PR #66) |
| 2026-06-26 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Added Implementation Notes, Related Decisions, Revision History |
| 2026-06-30 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Marked auth_url/client_id SSO support as implemented (PR #155), marked resolution.py move as done (PR #66) |
