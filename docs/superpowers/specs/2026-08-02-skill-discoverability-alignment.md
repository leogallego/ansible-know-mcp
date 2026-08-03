# Skill Discoverability Alignment — Addendum

**Date:** 2026-08-02
**Status:** Draft (ready for issue updates, then implementation)
**Parent design:** [2026-07-17-project-scoped-skills-design.md](2026-07-17-project-scoped-skills-design.md) (issue #181)
**Related ADRs:** [ADR-0007](../../architecture/adr/0007-agentskills-spec-compliance.md), [ADR-0008](../../architecture/adr/0008-three-layer-distribution.md)

## Purpose

Correct and unify how generated skills are **discovered** across:

1. Host AI agents (Claude Code, Cursor, Copilot, etc.) via filesystem + `AGENTS.md`
2. next-mcp `SkillRegistry` via `ANSIBLE_SKILL_SOURCES` (local / github)
3. Cross-agent packaging via Lola (#149)

This is an **addendum**, not a redesign of skill generation or layout.
Output format remains agentskills.io-compliant nested collection grouping
(ADR-0007 / closed #148).

## Problem restatement

Three discoverability gaps are often conflated:

| Gap | Symptom | Fixed by |
|-----|---------|----------|
| **A. Wrong write location** | Skills land under `uvx` cache / `$HOME`, not the project | #181 env chain |
| **B. Host agents don’t know where to look** | Generic `skills/` is not auto-loaded by most agents | #181 `AGENTS.md` managed section |
| **C. next-mcp misses nested skills** | `_loadLocalSource` scans 1 level; module skills invisible | Upstream vscode-ansible patch (deferred) |

#181 addresses **A** and **B** only. Claiming #181 “integrates with next-mcp”
without **C** overstates Layer 1 readiness.

## Discovery paths (corrected)

```text
ansible-know-mcp                         Consumers
generate_* ──► SKILLS_DIR
                 │
                 ├─► know MCP: list_skills / get_skill / skills://*
                 │
                 ├─► Host agents: read AGENTS.md → open skills/**/SKILL.md
                 │     (#181 — Layer 1 host discovery)
                 │
                 ├─► next-mcp SkillRegistry: type "local" url = same dir
                 │     (today: collection-level only; nested needs upstream)
                 │
                 └─► Lola wrap (#149) → lola install / GitHub Lola source
                       (Layer 2 distribution — packaging step)
```

### Layer classification (corrects ADR-0008 follow-up wording)

| Mechanism | Layer | Role |
|-----------|-------|------|
| Shared project `skills/` + know MCP tools | Layer 1 | Generate + serve locally |
| `AGENTS.md` managed section (#181) | Layer 1 | Host-agent pointer into project skills |
| next-mcp `ANSIBLE_SKILL_SOURCES` local | Layer 1 | Registry/`skill_*` over same directory |
| GitHub skill repo + next-mcp github source | Layer 2 | Team-shared consumption |
| Lola module packaging (#149) | Layer 2 | Cross-agent install / marketplace |
| Remote HTTP MCP (#71) | Layer 3 | On-demand generation service |

**Correction:** Do **not** classify `AGENTS.md` as a Layer 2 consumption path.
The #181 design follow-up item that said so is superseded by this addendum.

**Correction:** next-mcp GitHub **Lola** loading does **not** consume our raw
`skills/{collection}/{module}/SKILL.md` tree. Lola expects
`{module}/skills/{skill}/SKILL.md` (or marketplace layout). Layer 2 via Lola
requires #149 (or equivalent wrap). Vercel/generic GitHub loaders are also
1-level and miss nested module skills without packaging or an upstream depth fix.

## Dual-config contract (our side)

When users run ansible-know-mcp **and** next-mcp against the same project,
both must resolve to the **same skills directory**.

### Env vars

| Variable | Owner | Meaning |
|----------|-------|---------|
| `ANSIBLE_KNOW_SKILLS_DIR` | know-mcp | Explicit skills directory (wins) |
| `ANSIBLE_KNOW_PROJECT_DIR` | know-mcp | Project root → `{root}/skills` |
| `CLAUDE_PROJECT_DIR` | Claude Code → know-mcp | Auto-injected project root → `{root}/skills` |
| `ANSIBLE_SKILL_SOURCES` | next-mcp | JSON array of `SkillSource`: `{id, type, url, trust}` |

know-mcp never reads `ANSIBLE_SKILL_SOURCES`. next-mcp never reads
`ANSIBLE_KNOW_*`. Alignment is **operator configuration**, documented by us.

### Recommended VS Code wiring

Prefer the extension setting for next-mcp sources when available
(`ansibleEnvironments.skillSources`); for raw MCP `env`, use the same
absolute/workspace path in both places:

```json
{
  "servers": {
    "ansible-know": {
      "command": "uvx",
      "args": ["ansible-know-mcp"],
      "env": {
        "ANSIBLE_KNOW_PROJECT_DIR": "${workspaceFolder}"
      }
    }
  }
}
```

```json
{
  "ansibleEnvironments.skillSources": [
    {
      "id": "know-generated",
      "type": "local",
      "url": "${workspaceFolder}/skills",
      "trust": "community"
    }
  ]
}
```

Notes:

- next-mcp’s loader expects **`url`** (directory path), plus **`id`** and **`trust`**.
  Docs/examples that use `path` or `repo` alone do not match the TypeScript
  `SkillSource` interface — call that out in README; do not invent a parallel schema.
- Until upstream expands local scan depth, next-mcp will index **collection-level**
  `skills/{collection}/SKILL.md` and miss nested module/plugin/role skills.
  Host agents following `AGENTS.md` can still open nested `SKILL.md` files.
- Claude Code: `CLAUDE_PROJECT_DIR` makes know-mcp zero-config; next-mcp still
  needs an explicit local source if `skill_*` tools should see generated skills.

### What `AGENTS.md` must and must not claim

Managed section (#181) **should**:

- Point at the project-relative `skills/` tree
- Give a concrete nested example path
- List available collection directories when known

Managed section **must not**:

- Claim that next-mcp `SkillRegistry` reads `AGENTS.md` (it does not)
- Claim that all nested skills appear in `skill_list` without the upstream fix
- Write beside `ANSIBLE_KNOW_SKILLS_DIR` when that path is a shared non-project
  directory (`get_project_root()` excludes that var — keep that rule)

## Non-goals (this addendum / #181 wave)

- Changing nested output layout to flat `skills/{name}/SKILL.md` for next-mcp
- Implementing multi-path `list_skills` (#182) in the same change as #181
- Automating Lola packaging (#149)
- Patching vscode-ansible `_loadLocalSource` (upstream phase)
- Loading `.agents/skills` or agent-specific skill dirs (rejected by next-mcp ADR-014 for registry; out of scope for us too)

## Issue map and ownership

| Issue | Action after this addendum | Phase |
|-------|----------------------------|-------|
| **#181** | Implement; body/comments: dual-config, A+B only, link this doc; harden `SKILLS_DIR` validation | Our side — now |
| **#182** | Comment: blocked on #181; know-only multi-path; not next-mcp | Our side — after #181 |
| **#149** | Comment: required for Layer 2 Lola/GitHub Lola; not a substitute for local scan depth | Our side — later |
| **#196** | Remaining v0.7 docs drift only (service-contracts counts, ADR-0003, ADR-0007 Accepted, July 31 comparison, strategy table). ADR-0008 / discoverability owned by #181 | After #181 |
| **#200** | next-mcp local scan depth tracking; draft at `tmp/draft-issue-local-scan-depth.md` (fix algorithm before filing upstream) | After #181; then upstream |

### Upstream draft fix (do not file until our wave lands)

`tmp/draft-issue-local-scan-depth.md` must be corrected before posting:

1. **Always** discover nested `{collection}/{skill}/SKILL.md` even when
   `{collection}/SKILL.md` exists (collection-level skill is normal for us).
2. Persist a relative content path (or `contentUrl`) so
   `_fetchSkillContent` does not assume `join(url, skill.name, 'SKILL.md')`.
3. Optionally mirror depth-2 for `_loadVercelSource` if raw GitHub trees
   without Lola wrapping are in scope.

Filing venue: `ansible/vscode-ansible` (next branch / SkillRegistry).

## Implementation sequence (our side first)

1. **Land this addendum** (this file) — source of truth for discoverability narrative.
2. **Update issue bodies/comments** (#181, #182, #149, #196) to link here and drop superseded Layer-2/`AGENTS.md` wording.
3. **Implement #181** per parent design + dual-config README/tool-description notes from this addendum; include `SKILLS_DIR` sensitive-prefix validation.
4. **Docs/ADR catch-up** (#196 slice) in the same PR or an immediate follow-up.
5. **#182** only if multi-path is still needed after #181.
6. **#149** when Layer 2 / marketplace work starts.
7. **Upstream phase:** fix draft → file vscode-ansible issue → link from ADR-0008.

## Edits implied in sibling docs (for #196 / #181 PR)

Do not rewrite the full skills-distribution strategy. Minimal corrections:

| Doc | Change |
|-----|--------|
| #181 design follow-up #3 | Superseded: `AGENTS.md` is Layer 1 host discovery, not Layer 2 |
| ADR-0008 | Add Layer 1 `AGENTS.md` path; fix “Lola works today” for raw output; link deferred upstream + this addendum |
| ADR-0007 | Promote toward Accepted (#148 closed); keep consumer scan-depth caveat |
| `docs/research/skills-distribution-strategy-2026-06-26.md` | Same Lola/Layer corrections in “Already works” / open items |
| `docs/research/2026-07-31-know-vs-next-mcp-comparison.md` | Replace flat `skills/ansible.builtin.copy/` example with nested kebab layout |
| `docs/architecture/project-strategy.md` | Mark #148 done; note scan-depth proposal deferred to upstream phase |

## Success criteria

- [ ] #181 ships: skills default into the project; `AGENTS.md` managed section works (create/append/replace).
- [ ] README documents dual-config for VS Code / Claude Code without promising nested `skill_list` until upstream.
- [ ] ADR-0007/0008 and strategy docs no longer claim Lola/GitHub consume raw nested trees.
- [ ] Issues #181/#182/#149/#196 link this addendum and agree on sequencing.
- [ ] Upstream issue remains unfiled until the draft algorithm is fixed and our side narrative is consistent.

## References

- Parent design: [2026-07-17-project-scoped-skills-design.md](2026-07-17-project-scoped-skills-design.md)
- Plan: [../plans/2026-07-17-project-scoped-skills.md](../plans/2026-07-17-project-scoped-skills.md)
- Distribution strategy: [../../research/skills-distribution-strategy-2026-06-26.md](../../research/skills-distribution-strategy-2026-06-26.md)
- Know vs next comparison: [../../research/2026-07-31-know-vs-next-mcp-comparison.md](../../research/2026-07-31-know-vs-next-mcp-comparison.md)
- Upstream draft (local): `tmp/draft-issue-local-scan-depth.md`
- next-mcp `SkillRegistry`: `packages/services/src/SkillRegistry.ts` in ansible/vscode-ansible (`next`)
- agentskills.io client guide (scan depth 4–6)
