# ansible-know-mcp: Project Strategy and Evolution

**Last updated:** 2026-08-03
**Status:** Active

---

## Project Status

**ansible-know-mcp** is a community MCP server for Ansible knowledge retrieval and AI skill generation.

| Dimension | Current (v0.7.0) |
|---|---|
| Language | Python (FastMCP) |
| License | GPL-3.0-or-later |
| Distribution | PyPI (`uvx ansible-know-mcp`) |
| Tools | 18 |
| Resources | 6 |
| Prompts | 5 |
| Tests | 974 collected (unit + integration; integration skipped by default) |
| Modules | 23 Python source modules |

### Core capabilities

| Capability | Purpose |
|---|---|
| Module/plugin/role documentation | Structured docs with Galaxy fallback, multi-server support, README parsing |
| Collection discovery | Concurrent multi-server search via `ansible.cfg`, enriched manifests |
| Skill generation | AI-ready SKILL.md packages from module, plugin, role, and collection metadata |
| Documentation search | Upstream doc manifests (ansible-core, lint, navigator, builder, creator, molecule) + RTD fallback |

---

## Strategic Direction

### Upstream-first integration with @ansible/mcp-server (next)

Rather than maintain two servers that increasingly overlap, contribute knowledge features upstream and focus on what's unique.

**Contribute upstream:**
- P0: Multi-server Galaxy via `ansible.cfg` (enterprise blocker)
- P1: Structured Galaxy docs fallback (extends existing `get_galaxy_plugin_doc`)
- P1: Role README HTML parsing (extends existing role docs)
- Evaluate: Runtime doc search (depends on next-mcp team's interest)

**Keep:**
- Skill generation pipeline (all 4 generation tools + supporting tools)
- Skill sharing as team service (remote HTTP/SSE deployment)

**Drop** (once upstream absorbs the features):
- `search_modules`, `search_plugins` → next-mcp's `search_ansible_plugins` has better scoring
- `ensure_collection` → next-mcp's `install_ansible_collection` rebuilds search index
- `search_collections` → next-mcp covers Galaxy + GitHub orgs
- `get_module_doc`, `get_plugin_doc`, `get_role_doc` → next-mcp with upstream Galaxy fallback + README parsing
- `clear_cache` → fewer caches to manage

See [ADR-0006](adr/0006-upstream-first-integration.md) for the full decision record.

### Evolution path

```
Phase 1-3 (now → mid-term):
  ansible-know-mcp → ansible-skill-mcp
  Python, community project, focused on skill generation + distribution

Phase 4 (future):
  ansible-skill-mcp → aap-mcp-skills
  TypeScript, platform component alongside aap-mcp-server
```

### Three-layer distribution model

Each layer is additive — same spec-compliant skills at every layer.

| Layer | When | How | Value |
|---|---|---|---|
| **Local** | Now | Paired with next-mcp, shared directory | Real-time generation from locally installed, private hub, and Galaxy collections |
| **Repository** | Next | Generated skills pushed to GitHub repo | Persistence, sharing, version control, Lola distribution to 40+ agents |
| **Remote service** | Future | HTTP/SSE MCP server | On-demand generation for DevSpaces, Ansible Self-Service Portal, enterprise teams |

See [ADR-0008](adr/0008-three-layer-distribution.md) for the full decision record.

---

## Architecture Decisions

All significant decisions are recorded in [docs/architecture/adr/](adr/). Decisions from the strategy discussion (2026-06-26):

| ADR | Title | Status | Summary |
|---|---|---|---|
| [0006](adr/0006-upstream-first-integration.md) | Upstream-First Integration with @ansible/mcp-server (next) | Proposed | Contribute knowledge features upstream, keep skill generation |
| [0007](adr/0007-agentskills-spec-compliance.md) | agentskills.io Specification Compliance | Accepted | One output format, spec-compliant; naming and metadata conventions (#148) |
| [0008](adr/0008-three-layer-distribution.md) | Three-Layer Skill Distribution Model | Proposed | Local → Repository → Remote; Layer 1 AGENTS.md + dual-config (#181/#184) |

Prior decisions:

| ADR | Title | Status |
|---|---|---|
| [0001](adr/0001-fastmcp-framework.md) | FastMCP as MCP Server Framework | Accepted |
| [0002](adr/0002-subprocess-ansible-doc.md) | Subprocess-Based ansible-doc Integration | Accepted |
| [0003](adr/0003-module-level-state.md) | Module-Level Mutable State | Accepted (with known debt) |
| [0004](adr/0004-galaxy-fallback-chain.md) | Galaxy Fallback Chain with Multi-Server Support | Accepted |
| [0005](adr/0005-jinja2-skill-generation.md) | Jinja2-Based Skill Package Generation | Accepted |

---

## Key Relationships

### @ansible/mcp-server (next branch)

The Ansible DevTools MCP server, bundled in the VS Code extension. Our primary integration partner.

- **Local copy:** clone of `github.com/ansible/vscode-ansible` (next branch)
- **Repo:** `github.com/ansible/vscode-ansible`
- **Version:** 0.0.1 (pre-release)
- **Relationship:** We upstream knowledge features; they distribute skills via the SkillRegistry
- **Integration point:** Shared project `skills/` directory (Layer 1 dual-config + `AGENTS.md`; see ADR-0008 / #181) or GitHub source after Lola packaging (#149)
- **Gap:** `_loadLocalSource` scans 1 level; tracked in [#200](https://github.com/leogallego/ansible-know-mcp/issues/200) (upstream later)
- **Pitch document:** [upstream-integration-proposal-2026-06-26.md](../research/upstream-integration-proposal-2026-06-26.md)

### aap-mcp-server

The AAP platform MCP server for Controller, EDA, and Gateway operations.

- **Repo:** `github.com/ansible/aap-mcp-server`
- **Relationship:** Future home for `aap-mcp-skills` (Phase 4, TypeScript rewrite)
- **No current integration** — the platform MCP ecosystem is the long-term direction

### Lola

Cross-agent AI skill package manager by Red Hat Product Security.

- **Repo:** `github.com/LobsterTrap/lola`
- **Marketplace:** `github.com/RedHatProductSecurity/lola-market`
- **Relationship:** Distribution channel. Users wrap our spec-compliant skills into Lola modules for cross-agent installation (40+ agents).
- **Not a generation format** — Lola packaging is a user's distribution step, not our output format.

### agentskills.io

The standard specification for AI agent skills.

- **Spec:** `agentskills.io/specification`
- **Validator:** `github.com/agentskills/agentskills/tree/main/skills-ref`
- **Our compliance:** #148 closed; ADR-0007 Accepted. Nested kebab layout + metadata shipped.
- **See:** [ADR-0007](adr/0007-agentskills-spec-compliance.md)

---

## Open Items

1. ~~**Finalize issue #148**~~ — **Done** (closed; ADR-0007 Accepted)
2. ~~**Draft next-mcp scan depth proposal**~~ — **Tracked** as [#200](https://github.com/leogallego/ansible-know-mcp/issues/200)
3. ~~**#182** — multi-path `list_skills` / `get_skill`~~ (done: `ANSIBLE_KNOW_SKILLS_PATH`)
4. **#149** — Lola packaging helper (Layer 2)
5. **#189 / #125** — FastMCP 4 / MCP 2026-07-28 session migration (blocked on stable release)

---

## Supporting Documents

| Document | Purpose |
|---|---|
| [upstream-integration-proposal-2026-06-26.md](../research/upstream-integration-proposal-2026-06-26.md) | External pitch to next-mcp team |
| [comparison-2026-06-26.md](../research/comparison-2026-06-26.md) | Technical feature comparison |
| [skills-distribution-strategy-2026-06-26.md](../research/skills-distribution-strategy-2026-06-26.md) | Skills distribution technical details |
| [service-contracts.md](service-contracts.md) | MCP server contracts and invariants |
