# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-08-18

### Added

- Agent Plugins packaging via `package_as_plugin` — `plugin.json`, flat `skills/`, optional `mcp.json` (#223, #224)
- Standalone Galaxy role search and docs — `search_standalone_roles`, `get_standalone_role_doc` for 2-part `namespace.role` names (#230, #232)
- Per-collection index pointers in `AGENTS.md` (module count, version, SKILL.md path) (#222, #225)

### Deprecated

- `package_for_lola` — still works, emits a deprecation warning; remove after one release cycle (#223)

### Fixed

- Emit Agent Plugin tarball members at archive root (`plugin.json`, `skills/`) for PAH/Pulp ingest (#226, #228)

### Changed

- Steer agents toward `get_collection_manifest` before `get_collection_docs` (#231)

### Chore

- Migrate architecture review to git-review; contracts as source of truth (#229)
- Bump `actions/setup-python` 6.3.0 → 7.0.0, `astral-sh/setup-uv` 8.2.0 → 9.0.0, `actions/checkout` 7.0.0 → 7.0.1, `pypa/gh-action-pypi-publish` 1.14.0 → 1.14.2

## [0.8.0] - 2026-08-08

### Added

- AAP 2.5/2.6/2.7 documentation manifests and guide discovery (#178, #180, #209)
- HTTP fallback when Red Hat Docs MCP rejects modular AAP URLs (#183, #206)
- Project-scoped skills with `AGENTS.md` discovery/updates (#181, #184)
- Multi-path skill search via `ANSIBLE_KNOW_SKILLS_PATH` (colon-separated) for `list_skills`, `get_skill`, and `skills://*` — first path wins; writes still use single `SKILLS_DIR` (#182, #202)
- RTD Embed API fallback when Cloudflare blocks `docs.ansible.com` markdown (#168, #213)
- Lola-compatible packaging helper for generated skills (#149, #211)

### Changed

- Batch `ansible-doc` for collection manifests and skill generation (fewer subprocesses) (#191, #212, #215)
- Share Foundation HTML→markdown / RTD token helpers; extract optional HTTP client lifecycle and RTD markdown pipeline; inject lifespan `httpx` into `RedHatDocsClient` (#117, #197, #217, #218, #219)
- TypedDict (`AnsibleDocEntry` / `AnsibleDocPayload`) for raw ansible-doc JSON at the parser/Galaxy boundary (#216, #220)
- Refresh architecture docs after v0.7 (#196, #201)

### Fixed

- Set `ANSIBLE_LOCAL_TMP` for subprocesses in restricted environments (#174)
- Partition Galaxy caches by server base URL; keep docs-blob cache memory-only (#190, #199)
- Keep skill/resource path resolution and MCP resource I/O off the event loop (#193, #205, #208, #210)
- Align `test_with_tags` mocks with multi-server Galaxy search (#179, #204)
- Canonicalize AAP 2.6/2.7 manifest URLs at build time (#209)

### Chore

- Docs hygiene, Superpowers plans/specs tracking, stop tracking demo assets (#198)
- Publish workflow marks `alpha`/`beta`/`rc` tags as GitHub prereleases

## [0.7.0] - 2026-06-30

### Added

- Harden `fetch_doc` against Cloudflare challenges and transient failures — User-Agent header, disk-backed page cache (100 entries, 24h TTL), rate limiting (1 req/sec), retry with exponential backoff and `Retry-After` parsing, Cloudflare managed challenge detection (#170)
- Negative cache for Galaxy API discovery failures — avoids repeated slow probes against servers that don't support the v3 API (#166)
- Galaxy API root discovery for Automation Hub and Private Automation Hub support (#155)

### Fixed

- Defense-in-depth hardening from v0.6.0 review — narrowed exception handling, improved input validation (#164)
- Correct template context variable naming for Ansible terminology (#165)
- Replace `peter-evans/create-pull-request` with `gh` CLI in docs manifest workflow (#153)

### Changed

- Deduplicate `DocProvenance` and skill listing logic into shared helpers (#162)
- Update docs manifests (#161)
- Bump `actions/setup-python` 5.6.0 → 6.3.0, `actions/upload-pages-artifact` 4.0.0 → 5.0.0, `astral-sh/setup-uv` 7.6.0 → 8.2.0

## [0.6.1] - 2026-06-27

### Breaking Changes

- Skill naming changed to agentskills.io spec compliance — kebab-case directories and names, added metadata fields (`fqcn`, `collection`, `plugin-type`, `version`, `compatibility`). **All existing generated skills must be regenerated.** (#150)

### Added

- Batch docs-blob fetch for collection documentation via `get_collection_docs` (#135)
- Disk persistence for `BoundedCache` with atomic writes (#142)

### Fixed

- Bypass Galaxy CLI cache in `ensure_collection` to avoid stale installs (#143)
- Improve `search_docs` matching and RTD fallback diversity (#140)
- Restore FQCN output in `skills://list` resource for compatibility with `get_skill` (#150)

### Changed

- Bump `actions/checkout` from v4/v6 to v7.0.0 (#63)

## [0.6.0] - 2026-06-25

### Added

- Plugin discovery, documentation, and skill generation — `search_plugins`, `get_plugin_doc`, `generate_plugin_skill` for all plugin types (lookup, filter, test, connection, become, strategy, callback, inventory, cache, cliconf, httpapi, netconf, shell, vars) (#122)
- RTD-native documentation discovery — replaces ai-docs fork with direct docs.ansible.com integration (#116)

### Changed

- Align `resolve_module_doc` return type with role/plugin documentation pattern (#129)

### Fixed

- Tighten `plugins_metadata` and `roles_metadata` parameter typing (#128)
- Move skill reading functions from server.py to skills.py for better separation of concerns (#130)
- Remove AnsibleClaw references from templates and source code (#115)
- Mention resources in MCP server instructions (#114)

## [0.5.2] - 2026-06-22

### Fixed

- Use `typing_extensions.TypedDict` on Python < 3.12 for compatibility (#112)

## [0.5.1] - 2026-06-22

### Added

- Deploy as remote MCP server on Hugging Face Spaces (#109)
- `clear_cache` MCP tool — clear Galaxy version/docs-blob caches or doc manifests on demand (#110)
- Collection-level skill generation with nested layout (#88)
- Session lifecycle management for HTTP transport (#107)
- Per-session state isolation and periodic PyPI version checks (#87)

### Changed

- Type tool handler returns with TypedDicts (#105)
- Tighten nested types and fix semaphore lifecycle (#106)
- Move `derive_tags` to Foundation module and wrap `get_skill` in executor (#103)
- Add `ManifestResult` and `ParamDict` TypedDicts, split `generate_manifest`, edge-case tests (#104)

## [0.5.0] - 2026-06-20

### Added

- HTTP streamable transport support — MCP clients can use stdio over HTTP (#64, #80)
- Landing page for know.ansible.ar (#72, #75)

### Changed

- Encapsulate server state for session isolation and improved testability (#68, #77)
- Extract resolution logic from server.py to separate module (#66, #73)
- Clean up External Access layer contracts (#69, #74)
- Batch architecture improvements from review (#60, #70)
- Move error-handling helpers from server.py to errors.py (#55)
- Cap fastmcp dependency to <4 with floor at 3.2

### Fixed

- Correct MCP endpoint URL in README — remove trailing slash
- Improve Connect section layout and skip CI for site changes (#76)

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

[0.9.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/leogallego/ansible-know-mcp/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.5.2...v0.6.0
[0.5.2]: https://github.com/leogallego/ansible-know-mcp/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/leogallego/ansible-know-mcp/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/leogallego/ansible-know-mcp/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/leogallego/ansible-know-mcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leogallego/ansible-know-mcp/releases/tag/v0.1.0
