# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/leogallego/ansible-know-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leogallego/ansible-know-mcp/releases/tag/v0.1.0
