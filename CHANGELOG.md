# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-06-14

### Added

- Multi-server Galaxy support via `ansible.cfg` — reads `[galaxy_server.*]` sections with per-server auth (token, basic auth), env var overrides, and `validate_certs` (#45)
- `galaxy://servers` resource — list configured Galaxy servers with auth type (credentials never exposed)
- Update notifications — server checks PyPI on startup and warns when outdated; `server://version` resource exposes upgrade status (#44)
- `ANSIBLE_KNOW_NO_PUBLIC_GALAXY` env var to suppress automatic public Galaxy fallback
- Integration tests in CI workflow for ansible-doc and Galaxy API (#53)
- `DocProvenance` TypedDict with required/optional field split for Galaxy documentation metadata
- `ModuleMetadata` and `RoleMetadata` TypedDicts for structured parser output
- Credential sanitization — strips CRLF from tokens/passwords to prevent header injection

### Changed

- `search_collections` queries all configured Galaxy servers in parallel and merges results
- `get_module_doc` and `get_role_doc` try Galaxy servers in priority order on fallback
- Renamed internal parameter `namespace` → `collection_filter` in parser functions and `collection_fqcn` in collections for clarity (MCP tool API unchanged) (#54)
- Tightened `except` clauses — removed unnecessary `KeyError`/`OverflowError` catches, added explicit guards (#51, #52)
- GalaxyClient now includes `doc_source_server` in returned metadata automatically

### Fixed

- Blocking file I/O in async lifespan startup — `load_galaxy_servers()` now runs in executor
- `docs.search_docs` crashes on doc sources missing `url` key — now logs warning and skips (#52)
- `_check_pypi_version` exception handling restored to broad `except` to avoid crashing on unexpected PyPI errors (#52)

## [0.3.2] - 2026-06-11

### Changed

- Exposed server version info and upgrade instructions in docs (#41)

## [0.3.1] - 2026-06-11

### Fixed

- Lazy-init enrichment semaphore to avoid event loop mismatch (#39)

## [0.3.0] - 2026-06-11

### Added

- Role support — `get_role_doc`, `generate_role_skill`, role listing in manifests (#38)

## [0.2.0] - 2026-06-10

### Added

- `search_collections` tool — search Galaxy by keyword, ranked by download count
- `ensure_collection` tool — install collections to session-local temp directory
- `galaxy://installed` resource — list session-installed collections
- `find_collection` prompt — guided discover/install/explore workflow
- Galaxy docs fallback — `get_module_doc` uses Galaxy docs-blob when collection not installed locally
- Galaxy v3 API client with version/docs-blob caching, TTL expiry, LRU eviction
- Connection pooling via FastMCP lifespan-managed httpx client
- Enrichment concurrency limiter for Galaxy search results
- Granular httpx timeout profiles (fast/default/slow)
- Negative collection cache — skips ansible-doc for known-missing collections
- MCP tool annotations (`readOnlyHint`, `idempotentHint`, `destructiveHint`)
- Return value documentation in all tool docstrings
- Integration test framework with `--run-integration` opt-in
- 10 integration tests for real ansible-doc and Galaxy API
- Ruff linting and pytest-cov in CI
- 45 validation tests, 7 config tests
- GalaxyClient async context manager for httpx client cleanup

### Fixed

- Galaxy fallback not triggered when ansible-doc returns exit 0 with empty JSON (#37)
- Double-failure hint — no "use ensure_collection" when Galaxy fallback also failed
- Standardized all tool error returns to `{"error": str}`
- Script permissions: world-executable → owner-only
- Docs manifest size limit (5MB) with content-length and body checks
- Malformed content-length headers handled gracefully
- `get_doc_sources()` falls back to defaults on invalid JSON
- Exception chaining (`raise ... from exc`) throughout codebase

### Changed

- `ANSIBLE_COLLECTIONS_PATH` prepends server path (was overriding)

## [0.1.0] - 2025-05-07

### Added

- 8 MCP tools: `search_modules`, `get_module_doc`, `search_docs`, `get_collection_manifest`, `list_skills`, `get_skill`, `generate_skill`, `generate_collection_skills`
- 3 MCP resources: `skills://list`, `skills://{skill_name}`, `docs://sources`
- 3 MCP prompts: `review_playbook`, `explain_module`, `generate_role`
- Documentation search via AI-friendly manifest from ansible-documentation
- Skill package generation with SKILL.md, scripts, and playbooks via Jinja2 templates
- Collection manifest generation with per-module summaries and tagging
- OWASP security hardening: FQCN input validation, path traversal protection, error sanitization, output size limits, audit logging
- 77 tests covering tools, parser, skills, docs, collection manifests, and security

[0.4.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/leogallego/ansible-know-mcp/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/leogallego/ansible-know-mcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leogallego/ansible-know-mcp/releases/tag/v0.1.0
