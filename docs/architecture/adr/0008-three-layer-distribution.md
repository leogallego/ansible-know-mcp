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

ansible-skill-mcp runs alongside next-mcp on a developer's machine.
Skills are generated in real time from collections resolved via multiple
endpoints (locally installed, private Automation Hub, public Galaxy).

- Skills land in `ANSIBLE_KNOW_SKILLS_DIR` (our env var, default `./skills/`)
- next-mcp reads via `type: "local"` source in `ANSIBLE_SKILL_SOURCES`
  (next-mcp's env var — JSON array of `SkillSource` objects with
  `{id, type, url, trust}`)
- Developer gets immediate access via `skill_search`

**Value:** Real-time generation from any collection source. No pre-work,
no publishing step.

### Layer 2: Repository (next)

Skills are generated once by a platform team, pushed to a GitHub repo,
consumed by multiple developers.

Two consumption paths:
- **next-mcp SkillRegistry:** `type: "github"` source. Lola format
  detection scans 2 levels — works today.
- **Lola:** Users wrap skills into a Lola module and distribute to
  40+ agents via `lola install`.

**Value:** Persistence, sharing, version control, cross-agent reach.

### Layer 3: Remote Service (future)

ansible-skill-mcp runs as an HTTP/SSE MCP server deployed by a platform
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
- **Incremental rollout**: Layer 1 works today. Layer 2 requires
  publishing to a repo. Layer 3 requires infrastructure.

### Negative

- **Layer 1 gap**: next-mcp's `_loadLocalSource` scans 1 level. Our
  2-level output (collection/skill) is within spec bounds but missed
  by the current implementation. Proposed patch pending.
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

- **Layer 1** (local): `ANSIBLE_KNOW_SKILLS_DIR` env var (default
  `./skills/`), configured in `config.py`. next-mcp reads via
  `ANSIBLE_SKILL_SOURCES` with `type: "local"` pointing to the same path.
- **Layer 2** (repository): generated skills pushed to a GitHub repo.
  next-mcp reads via `ANSIBLE_SKILL_SOURCES` with `type: "github"`.
  Lola-compatible packaging is a separate user concern.
- **Layer 3** (remote): FastMCP HTTP/SSE transport (see ADR-0001). Not
  yet implemented — requires hosting, auth, and monitoring infrastructure.
- **Scan depth gap**: next-mcp `_loadLocalSource` scans 1 level; our
  2-level output requires a patch (draft at `tmp/draft-issue-local-scan-depth.md`).

## Related Decisions

- [ADR-0001](0001-fastmcp-framework.md) — FastMCP's HTTP/SSE transport
  enables Layer 3
- [ADR-0006](0006-upstream-first-integration.md) — three-layer model
  defines the post-upstream integration path
- [ADR-0007](0007-agentskills-spec-compliance.md) — one output format
  makes the three-layer model possible

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-26 | Leonardo Gallego (AI-assisted) | Initial proposal |
