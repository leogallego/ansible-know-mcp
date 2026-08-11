# ADR 0008: Three-Layer Skill Distribution Model

## Status

Proposed

## Date

2026-06-26

## Context

Generated skills need to reach developers across different deployment
contexts: individual developers working locally, teams sharing curated
skill sets, and enterprise environments with DevSpaces or portal-based
workflows.

A single distribution mechanism doesn't cover all cases:
- Local filesystem works for individual developers but not for teams
- GitHub repos work for teams but require manual setup
- Remote services work for enterprise but add infrastructure

The question: should we pick one distribution model, or design for all
three as additive layers?

## Decision

Adopt a three-layer distribution model. Each layer is additive — builds
on the previous, uses the same spec-compliant skills throughout.

### Layer 1: Local (now)

ansible-know-mcp runs alongside next-mcp on a developer's machine.
Skills are generated in real time from collections resolved via multiple
endpoints (locally installed, private Automation Hub, public Galaxy).

- Skills land under the project by default via the `SKILLS_DIR` env chain
  (`ANSIBLE_KNOW_SKILLS_DIR` → `ANSIBLE_KNOW_PROJECT_DIR/skills` →
  `CLAUDE_PROJECT_DIR/skills` → `cwd/skills`) — see issue #181
- **Multi-path reads (optional):** `ANSIBLE_KNOW_SKILLS_PATH` (colon-separated)
  lets `list_skills` / `get_skill` / `skills://*` search multiple trees
  (e.g. project + bundled). Writes still use single `SKILLS_DIR` — see #182
- **Host-agent discovery:** `generate_collection_skills` updates a managed
  section in project-root `AGENTS.md` pointing agents at `skills/`.
  This is Layer 1 host discovery, not repository distribution.
  Skipped when skills are written outside `{project_root}/skills`
  (explicit `install_to` or non-project `ANSIBLE_KNOW_SKILLS_DIR`)
- **next-mcp registry:** dual-config — point `ANSIBLE_SKILL_SOURCES`
  (or `ansibleEnvironments.skillSources`) `type: "local"` `url` at the
  same skills directory (`{id, type, url, trust}`). know-mcp and next-mcp
  do not read each other's env vars
- next-mcp `_loadLocalSource` currently scans **1 level**, so nested
  module skills are missed until upstream scan depth is expanded
  (local tracking: #200; upstream issue TBD)

**Value:** Real-time generation from any collection source. No pre-work,
no publishing step. Host agents find skills via `AGENTS.md` even when
next-mcp's local scanner is shallow.

### Layer 2: Repository (next)

Skills are generated once by a platform team, pushed to a GitHub repo,
consumed by multiple developers.

Primary packaging path:
- **Agent Plugins (preferred):** Wrap generated skills with
  ``package_as_plugin`` / Domain ``package_as_agent_plugin`` (#223) into
  an Agent Plugins directory (`plugin.json`, flat `skills/{skill}/SKILL.md`,
  optional `mcp.json` for know-mcp). Any conformant client can discover the
  package. See [ADR-0009](0009-agent-plugins-distribution.md).

Secondary / legacy paths:
- **next-mcp SkillRegistry:** `type: "github"` source. Lola format
  detection can load nested skills **only** when the repo uses Lola
  layout (`{module}/skills/{skill}/SKILL.md`). Raw ansible-know output
  (`skills/{collection}/{module}/SKILL.md`) is **not** Lola layout —
  wrap via Agent Plugins (#223) or deprecated ``package_for_lola`` (#149)
  before relying on GitHub Lola loading. Vercel/generic GitHub loaders
  remain 1-level; Agent Plugins flat `skills/` matches that scan depth.
- **Lola (deprecated):** ``package_for_lola`` (#149) remains for one
  release cycle, then removal. Prefer Agent Plugins.

**Value:** Persistence, sharing, version control, cross-agent reach.

### Layer 3: Remote Service (future)

ansible-know-mcp runs as an HTTP/SSE MCP server deployed by a platform
team. Any MCP client calls it directly for on-demand skill generation.

Target environments:
- Ansible DevSpaces (containerized dev environments)
- Ansible Self-Service Portal (potential integration)
- Enterprise teams with centralized skill curation

**Value:** On-demand generation without local installation. Skills
generated once per collection version, served to all developers.

## Consequences

### Positive

- **Additive, not exclusive**: each layer serves a different use case.
  Teams can use any combination.
- **Same output format**: spec-compliant skills at every layer. No
  format variations or compatibility concerns.
- **Incremental rollout**: Layer 1 works today (host agents + know MCP
  tools). Layer 2 requires publishing/packaging. Layer 3 requires
  infrastructure.

### Negative

- **Layer 1 gap (next-mcp)**: `_loadLocalSource` scans 1 level. Our
  2-level output (collection/skill) is within agentskills.io bounds but
  missed by the current implementation until upstream expands depth.
- **Layer 2 packaging**: Repository consumption needs a wrap step
  (#223 Agent Plugins preferred; #149 Lola deprecated); not automatic
  from raw nested trees.
- **Layer 3 infrastructure**: remote deployment requires hosting,
  authentication, and monitoring. Not trivial for enterprise environments.
- **Scope creep risk**: three layers could expand the project's scope
  beyond skill generation. Mitigated by keeping distribution as a
  consumption concern — we generate spec-compliant skills, consumers
  handle their own distribution mechanics.

### Design constraint

The three-layer model reinforces ADR-0007's decision: one output format,
spec-compliant. If skills needed different formats per layer, the model
would break. Spec compliance makes the layers possible.

## Implementation Notes

- **Layer 1** (local): `get_project_root()` + `SKILLS_DIR` in `config.py`;
  `update_agents_md()` in `skills.py`; wired from `generate_collection_skills`.
  Alignment details:
  [`docs/superpowers/specs/2026-08-02-skill-discoverability-alignment.md`](../../superpowers/specs/2026-08-02-skill-discoverability-alignment.md)
- **Layer 2** (repository): generated skills pushed to a GitHub repo;
  Agent Plugins packaging via MCP tool ``package_as_plugin`` / Domain
  ``package_as_agent_plugin`` (#223 / ADR-0009) — wrap only; does not
  change ``generate_*`` layout (ADR-0007). Deprecated Lola wrap
  ``package_for_lola`` (#149) kept for one release cycle.
- **Layer 3** (remote): FastMCP HTTP/SSE transport (see ADR-0001). Not
  yet implemented — requires hosting, auth, and monitoring infrastructure
  (#71).
- **Scan depth gap**: next-mcp `_loadLocalSource` / content reload assume
  flat `{root}/{name}/SKILL.md`. Tracked in #200; upstream patch against
  ansible/vscode-ansible `next` comes after our side is consistent.
  Draft notes live in `tmp/draft-issue-local-scan-depth.md` (algorithm
  must always scan nested skills even when a collection-level
  `SKILL.md` exists).

## Related Decisions

- [ADR-0001](0001-fastmcp-framework.md) — FastMCP's HTTP/SSE transport
  enables Layer 3
- [ADR-0006](0006-upstream-first-integration.md) — three-layer model
  defines the post-upstream integration path
- [ADR-0007](0007-agentskills-spec-compliance.md) — one output format
  makes the three-layer model possible
- [ADR-0009](0009-agent-plugins-distribution.md) — Agent Plugins as
  primary Layer-2 packaging format

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-26 | Leonardo Gallego (Assisted-by: Claude Opus 4.6) | Initial proposal |
| 2026-08-03 | Leonardo Gallego (Assisted-by: Cursor) | Layer 1: project-scoped skills + AGENTS.md host discovery; correct Lola/GitHub claims; dual-config; scan-depth tracking |
| 2026-08-03 | Leonardo Gallego (Assisted-by: Cursor) | Layer 1: optional `ANSIBLE_KNOW_SKILLS_PATH` multi-path reads (#182) |
| 2026-08-11 | Leonardo Gallego (Assisted-by: Cursor (Grok 4.5)) | Layer 2: Agent Plugins primary (#223); Lola deprecated one cycle |
