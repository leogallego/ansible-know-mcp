# ADR 0006: Upstream-First Integration with @ansible/mcp-server (next)

## Status

Proposed

## Date

2026-06-26

## Context

The @ansible/mcp-server (next branch) is the official Ansible DevTools MCP
server, bundled in the VS Code extension. Over time, both servers have built
overlapping capabilities: collection search, module/plugin docs for
uninstalled collections, skill listing, and MCP behavioral annotations.

Maintaining two servers with growing overlap creates confusion for agents
(which server to call?), duplicated maintenance effort, and a weaker pitch
to stakeholders.

The next-mcp server excels at developer workflow (linting, execution,
scaffolding, environment management, task generation). ansible-know-mcp
excels at knowledge retrieval (structured docs, Galaxy fallback, multi-server
support, README parsing) and skill generation.

The question: should ansible-know-mcp continue as a broad knowledge server,
or focus on its unique value and contribute the rest upstream?

## Decision

Adopt an upstream-first strategy:

1. **Contribute** knowledge features to next-mcp where they naturally extend
   existing capabilities. We provide algorithms, patterns, and test cases
   for TypeScript reimplementation — not code drops.

2. **Keep** skill generation as the core value proposition. The generation
   pipeline (structured docs → Jinja2 templates → SKILL.md packages) is
   Python/Jinja2 and doesn't fit in a TypeScript server.

3. **Drop** overlapping tools as upstream absorbs the features.

### Features to upstream

| Priority | Feature | Rationale |
|---|---|---|
| P0 | Multi-server Galaxy via `ansible.cfg` | Enterprise blocker — every enterprise deployment has a private hub |
| P1 | Structured Galaxy docs fallback | Extends existing `get_galaxy_plugin_doc` with structured JSON output |
| P1 | Role README HTML parsing | Most Galaxy roles lack `argument_specs`; extends existing role docs |
| Evaluate | Runtime doc search | Depends on next-mcp team's interest; their Starlight site is hosting, not runtime search |

### Features to keep

- `generate_skill`, `generate_plugin_skill`, `generate_role_skill`,
  `generate_collection_skills` — core generation pipeline
- `get_collection_manifest`, `get_collection_docs` — feed the generation pipeline
- `list_skills`, `get_skill` — serve generated skills via MCP resources

### Tools to drop (once upstream absorbs features)

- `search_modules`, `search_plugins` — next-mcp has better relevance scoring
- `ensure_collection` — next-mcp rebuilds search index after install
- `search_collections` — multi-server moves upstream
- `get_module_doc`, `get_plugin_doc`, `get_role_doc` — docs resolution moves upstream
- `clear_cache` — fewer caches with narrower scope

### Evolution path

```
Phase 1-3: ansible-know-mcp → ansible-skill-mcp (community, Python)
Phase 4:   ansible-skill-mcp → aap-mcp-skills (platform, TypeScript)
```

## Consequences

### Positive

- **Cleaner story**: "We contributed the knowledge infrastructure. What we
  kept is the generation engine that turns knowledge into agent-ready skills."
- **No agent confusion**: one server for knowledge + workflow (next-mcp),
  one server for skill generation (ansible-skill-mcp). Clear boundary.
- **Enterprise gap closed**: multi-server Galaxy support lands in the
  official tooling, not a community add-on.
- **Reduced maintenance**: fewer tools, narrower scope, less surface area.

### Negative

- **Dependency on next-mcp team**: upstream contributions require their
  acceptance and reimplementation capacity. Timeline is theirs, not ours.
- **Transition period**: during Phase 2, both servers have overlapping
  tools. Agent confusion persists until tools are dropped.
- **Standalone degradation**: ansible-skill-mcp without next-mcp loses
  the documentation tools. The remote service model (Layer 3) mitigates
  this — skill generation still works via Galaxy fallback internally.

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
  - Structured Galaxy docs fallback: `galaxy.py:get_docs_blob()`
  - Role README HTML parsing: `readme_parser.py`
- **Phase 2** (tool deprecation): mark overlapping tools as deprecated in
  `server.py`, emit warnings via `ctx.warning()`, remove after one release
- **Phase 3** (rename): `ansible-know-mcp` → `ansible-skill-mcp` — PyPI
  package rename, CLI entrypoint, documentation updates
- **Pitch document**: `docs/research/upstream-integration-proposal-2026-06-26.md`

## Related Decisions

- [ADR-0004](0004-galaxy-fallback-chain.md) — Galaxy fallback and
  multi-server support are the primary upstream contribution candidates
- [ADR-0007](0007-agentskills-spec-compliance.md) — spec compliance is
  required for interoperability with next-mcp's SkillRegistry
- [ADR-0008](0008-three-layer-distribution.md) — three-layer model defines
  how ansible-skill-mcp integrates with next-mcp post-upstream

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-26 | Leonardo Gallego (AI-assisted) | Initial proposal |
