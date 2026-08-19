# ansible-know-mcp: Project Strategy and Evolution

**Last updated:** 2026-08-19
**Status:** Active

---

## Project Status

**ansible-know-mcp** is a community MCP server for Ansible knowledge retrieval and AI skill generation.

| Dimension | Current (v0.9.0) |
|---|---|
| Language | Python (FastMCP) |
| License | GPL-3.0-or-later |
| Distribution | PyPI (`uvx ansible-know-mcp`) |
| Tools | 22 |
| Resources | 6 |
| Prompts | 5 |
| Modules | 25 Python source modules |

### Core capabilities

| Capability | Purpose |
|---|---|
| Module/plugin/role documentation | Structured docs with Galaxy fallback, multi-server support, README parsing |
| Standalone Galaxy role documentation | Galaxy v1 API search and README fetch for 2-part roles not in collections |
| Collection discovery | Concurrent multi-server search via `ansible.cfg`, enriched manifests |
| Skill generation | AI-ready SKILL.md packages from module, plugin, role, and collection metadata |
| Skill packaging | Agent Plugins tarballs for Layer-2 distribution |
| Documentation search | Upstream doc manifests (ansible-core, lint, navigator, builder, creator, molecule, AAP 2.5/2.6/2.7) + RTD fallback |

---

## Strategic Direction

### Upstream-when-ready integration with @ansible/mcp-server (next)

Contribute knowledge features upstream when accepted, but do not gate scope
or identity on upstream's acceptance timeline. The project remains a
knowledge-and-skills server.

**Contribute upstream:**
- P0: Multi-server Galaxy via `ansible.cfg` (enterprise blocker)
- P1: Structured Galaxy docs fallback (extends existing `get_galaxy_plugin_doc`)
- P1: Role README HTML parsing (extends existing role docs)
- P2: Galaxy v1 standalone role support (broadens Galaxy coverage)
- Evaluate: Runtime doc search (depends on next-mcp team's interest)

**Keep:**
- Skill generation pipeline (all 4 generation tools + supporting tools)
- Documentation tools (`search_docs`, `fetch_doc`, role docs, standalone role docs)
- Agent Plugins packaging (`package_as_plugin`)
- Skill sharing as team service (remote HTTP/SSE deployment)

**Deprecate** (only after upstream absorbs and proves stable):
- `search_modules`, `search_plugins` → next-mcp's `search_ansible_plugins` has better scoring
- `ensure_collection` → next-mcp's `install_ansible_collection` rebuilds search index
- `search_collections` → next-mcp covers Galaxy + GitHub orgs
- `get_module_doc`, `get_plugin_doc`, `get_role_doc` → kept internally for generation pipeline
- `clear_cache` → fewer caches to manage

See [ADR-0006](adr/0006-upstream-first-integration.md) for the full decision record.

### Evolution path

```
Phase 1 (now):
  ansible-know-mcp in ansible-community org
  Full 22-tool server, upstream contributions when accepted

Phase 2:
  Upstream absorbs P0/P1 features
  Deprecate overlapping public tools, keep internal resolution
```

### Three-layer distribution model

Each layer is additive — same spec-compliant skills at every layer.

| Layer | When | How | Value |
|---|---|---|---|
| **Local** | Now | Paired with next-mcp, shared directory | Real-time generation from locally installed, private hub, and Galaxy collections |
| **Repository** | Now | Agent Plugins tarball via GitHub/registry source | Persistence, sharing, version control, distribution to 68+ agents |
| **Remote service** | Future | HTTP/SSE MCP server | On-demand generation for DevSpaces, Ansible Self-Service Portal, enterprise teams |

See [ADR-0008](adr/0008-three-layer-distribution.md) for the full decision record.

---

## Architecture Decisions

All significant decisions are recorded in [docs/architecture/adr/](adr/).

| ADR | Title | Status | Summary |
|---|---|---|---|
| [0006](adr/0006-upstream-first-integration.md) | Upstream-First Integration with @ansible/mcp-server (next) | Amended | Upstream when ready, no rename, keep knowledge tools |
| [0007](adr/0007-agentskills-spec-compliance.md) | agentskills.io Specification Compliance | Accepted | One output format, spec-compliant; naming and metadata conventions (#148) |
| [0008](adr/0008-three-layer-distribution.md) | Three-Layer Skill Distribution Model | Proposed | Local → Repository → Remote; Layer 1 AGENTS.md + dual-config (#181/#184) |
| [0009](adr/0009-agent-plugins-distribution.md) | Agent Plugins Distribution | Accepted | Agent Plugins packaging for Layer-2 (#223) |

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
- **Relationship:** We upstream knowledge features when accepted; they distribute skills via the SkillRegistry
- **Integration point:** Shared project `skills/` directory (Layer 1 dual-config + `AGENTS.md`; see ADR-0008 / #181) or Agent Plugins packaging (#223 / ADR-0009)
- **Registry pipeline:** know-mcp generates → `package_as_plugin` → publish to HTTP index → next-mcp `SkillRegistry._loadRegistrySource()` consumes
- **Gap:** `_loadLocalSource` scans 1 level; tracked in [#200](https://github.com/leogallego/ansible-know-mcp/issues/200)
- **Pitch document:** [upstream-integration-proposal-2026-06-26.md](../research/upstream-integration-proposal-2026-06-26.md)
- **Latest comparison:** [2026-08-19-know-vs-next-mcp-comparison.md](../research/2026-08-19-know-vs-next-mcp-comparison.md)

### aap-mcp-server

The AAP platform MCP server for Controller, EDA, and Gateway operations.

- **Repo:** `github.com/ansible/aap-mcp-server`
- **Relationship:** Potential future integration
- **No current integration**

### Agent Plugins

Open packaging format for bundling Agent Skills and MCP servers.

- **Spec:** [agent-plugins.org/specification](https://agent-plugins.org/specification)
- **Relationship:** Primary Layer-2 distribution wrap via MCP tool
  ``package_as_plugin`` (#223 / ADR-0009). Ships flat `skills/` plus optional
  `mcp.json` for know-mcp.
- **Not a generation format** — packaging is a post-generate wrap step; nested
  `generate_*` layout is unchanged (ADR-0007).

### Lola

Cross-agent AI skill package manager by Red Hat Product Security.

- **Repo:** `github.com/LobsterTrap/lola`
- **Marketplace:** `github.com/RedHatProductSecurity/lola-market`
- **Relationship:** Legacy distribution channel. ``package_for_lola`` (#149)
  is deprecated in favor of Agent Plugins (#223).
- **Not a generation format** — Lola packaging is a post-generate wrap step, not a `generate_*` output mode.

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
4. ~~**#149** — Lola packaging helper (Layer 2)~~ — **Done** (deprecated; prefer [#223](https://github.com/leogallego/ansible-know-mcp/issues/223) / [ADR-0009](adr/0009-agent-plugins-distribution.md))
5. **#189 / #125** — FastMCP 4 / MCP 2026-07-28 session migration (blocked on stable release)
6. **#233–#237** — Standalone role hardening (HTTPS, malformed payloads, GitHub README fallback, skill generation, role install)
7. **#192** — Split server.py (lower barrier for community contributors)

---

## Supporting Documents

| Document | Purpose |
|---|---|
| [upstream-integration-proposal-2026-06-26.md](../research/upstream-integration-proposal-2026-06-26.md) | External pitch to next-mcp team |
| [2026-08-19-know-vs-next-mcp-comparison.md](../research/2026-08-19-know-vs-next-mcp-comparison.md) | Latest feature comparison with next-mcp |
| [comparison-2026-06-26.md](../research/comparison-2026-06-26.md) | Technical feature comparison (historical) |
| [skills-distribution-strategy-2026-06-26.md](../research/skills-distribution-strategy-2026-06-26.md) | Skills distribution technical details |
| [service-contracts.md](service-contracts.md) | MCP server contracts and invariants |
