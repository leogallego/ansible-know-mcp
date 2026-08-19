# ADR 0006: Upstream-First Integration with @ansible/mcp-server (next)

## Status

Amended (2026-08-19)

## Date

2026-06-26

## Context

The @ansible/mcp-server (next branch) is the official Ansible DevTools MCP
server, bundled in the VS Code extension. Over time, both servers have built
overlapping capabilities: collection search, module/plugin docs for
uninstalled collections, skill listing, and MCP behavioral annotations.

The next-mcp server excels at developer workflow (linting, execution,
scaffolding, environment management, task generation). ansible-know-mcp
excels at knowledge retrieval (structured docs, Galaxy fallback, multi-server
support, README parsing, standalone Galaxy roles) and skill generation.

The overlap is limited to 7 tools out of 22 (v0.9.0). Running both servers
simultaneously works well — agents route questions naturally based on tool
descriptions.

## Decision

Adopt an upstream-when-ready strategy:

1. **Contribute** knowledge features to next-mcp where they naturally extend
   existing capabilities. We provide algorithms, patterns, and test cases
   for TypeScript reimplementation — not code drops.

2. **Keep** ansible-know-mcp as a knowledge-and-skills server. The knowledge
   tools (documentation, search, Galaxy resolution) feed the generation
   pipeline internally — removing them would create a fragile cross-server
   dependency for the project's core function.

3. **Deprecate** overlapping tools only after upstream proves it absorbed
   the features. Do not preemptively drop tools or gate scope on upstream's
   acceptance timeline.

4. **No rename.** The name "know" covers both knowledge retrieval and
   knowledge packaging (skills). The rename churn (PyPI package, CLI
   entrypoint, MCP configs, documentation) is not justified.

### Features to upstream

| Priority | Feature | Rationale |
|---|---|---|
| P0 | Multi-server Galaxy via `ansible.cfg` | Enterprise blocker — every enterprise deployment has a private hub |
| P1 | Structured Galaxy docs fallback | Extends existing `get_galaxy_plugin_doc` with structured JSON output |
| P1 | Role README HTML parsing | Most Galaxy roles lack `argument_specs`; extends existing role docs |
| P2 | Galaxy v1 standalone role support | Thousands of Galaxy roles exist only as standalone; next-mcp has no v1 API |
| Evaluate | Runtime doc search | Depends on next-mcp team's interest |

### Features to keep

- `generate_skill`, `generate_plugin_skill`, `generate_role_skill`,
  `generate_collection_skills` — core generation pipeline
- `get_collection_manifest`, `get_collection_docs` — feed the generation pipeline
- `list_skills`, `get_skill` — serve generated skills via MCP resources
- `package_as_plugin` — Agent Plugins packaging (Layer-2 distribution)
- `search_standalone_roles`, `get_standalone_role_doc` — Galaxy v1 standalone roles
- `search_docs`, `fetch_doc` — documentation search and retrieval
- All 5 MCP prompts and 6 MCP resources

### Tools to deprecate (only after upstream absorbs the features)

- `search_modules`, `search_plugins` — next-mcp has better relevance scoring
- `ensure_collection` — next-mcp rebuilds search index after install
- `search_collections` — next-mcp covers Galaxy + GitHub orgs
- `get_module_doc`, `get_plugin_doc`, `get_role_doc` — kept internally for
  generation pipeline; marked deprecated in public API only after upstream
  absorbs the feature
- `clear_cache` — fewer caches with narrower scope

### Evolution path

```
Phase 1 (now):  ansible-know-mcp in ansible-community org
                Full 22-tool server, upstream contributions when accepted

Phase 2:        Upstream absorbs P0/P1 features
                Deprecate overlapping public tools, keep internal resolution
```

## Consequences

### Positive

- **Self-contained generation pipeline**: knowledge tools feed generation
  tools internally — no fragile cross-server dependency for core function.
- **No forced narrowing**: project scope grows based on community need
  (e.g., standalone Galaxy roles in v0.9.0), not upstream dependency.
- **Enterprise gap closed**: multi-server Galaxy support contributed to the
  official tooling once upstream accepts it.
- **No rename churn**: PyPI package, CLI entrypoint, MCP configs, and
  documentation all stay stable.

### Negative

- **Overlap persists longer**: agents see similar tools from both servers
  until upstream absorbs features. Mitigated by clear tool descriptions
  that enable natural routing.
- **Broader maintenance surface**: 22 tools instead of ~10 if we had
  narrowed. Mitigated by the tools being well-tested and stable.

### ADR compliance with next-mcp

All contributions must align with next-mcp's ADRs:
- ADR-018: Behavioral annotations, structured JSON errors
- ADR-014: agentskills.io-compatible SKILL.md frontmatter
- ADR-013: Galaxy fallback complements SCM-based docs
- ADR-019: Python tooling works within tiered env model

## Implementation Notes

- **Phase 1** (upstream contributions): provide algorithms, patterns, and
  test cases for TypeScript reimplementation in next-mcp. Key features:
  - Multi-server Galaxy via `ansible.cfg`: `galaxy.py`, `config.py`
  - Structured Galaxy docs fallback: `galaxy.py:GalaxyClient._fetch_docs_blob()`
  - Role README HTML parsing: `readme_parser.py`
  - Galaxy v1 standalone roles: `galaxy_v1.py`
- **Phase 2** (tool deprecation): mark overlapping tools as deprecated in
  `server.py`, emit warnings via `ctx.warning()`. Deprecation begins only
  after the upstream feature ships and proves stable in production use.
  Removal follows after minimum one release cycle with the deprecation
  warning active.
- **Pitch document**: `docs/research/upstream-integration-proposal-2026-06-26.md`
- **Latest comparison**: `docs/research/2026-08-19-know-vs-next-mcp-comparison.md`

## Related Decisions

- [ADR-0004](0004-galaxy-fallback-chain.md) — Galaxy fallback and
  multi-server support are the primary upstream contribution candidates
- [ADR-0007](0007-agentskills-spec-compliance.md) — spec compliance is
  required for interoperability with next-mcp's SkillRegistry
- [ADR-0008](0008-three-layer-distribution.md) — three-layer model defines
  how ansible-know-mcp integrates with next-mcp
- [ADR-0009](0009-agent-plugins-distribution.md) — Agent Plugins packaging
  for Layer-2 distribution

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-26 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Initial proposal |
| 2026-08-19 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Amended: drop rename, upstream-when-ready, add P2 Galaxy v1, keep knowledge tools |
