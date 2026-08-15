# Spec: Migrate to `git-review` (ai-skills-git)

**Status:** Implemented (2026-08-14)  
**Date:** 2026-08-08  
**Repo:** ansible-know-mcp (`leogallego/ansible-know-mcp`)  
**Depends on:** [ai-skills-git#21](https://github.com/leogallego/ai-skills-git/pull/21) (`git-review` merged)  
**Audience:** Implementation session in this repo only

---

## 1. Problem

This repo ships a fat project skill `skills/pr-architecture-review/SKILL.md`
(~318 lines) that duplicates:

- Review **procedure** now owned by portable `git-review` (ai-skills-git)
- Project **substance** (layer map, dependency rules, known violations,
  security/ADR checklists) that belongs in
  `docs/architecture/service-contracts.md` (+ ADRs / strategy)

`.git-pipeline.yml` still lists `pr-architecture-review` under
`always_load_review_skills`, so pipeline sessions keep loading the duplicate.

## 2. Goals

1. Make **`docs/architecture/service-contracts.md`** the sole SoT for
   enforceable architecture rules (including an explicit **Layer map** table
   currently only in the skill).
2. Wire pipeline to **`git-review`** (+ keep `pep8-review`).
3. Point ADRs / strategy via `.git-pipeline.yml` → `architecture.*`.
4. Remove (or stub-then-delete) `skills/pr-architecture-review/`.
5. Update CLAUDE.md / docs that tell agents to load the old skill.
6. Self-update loop: architecture changes update **contracts**, not a skill.

## 3. Non-goals

- Changing runtime architecture or fixing V-D6 / V-L3
- Porting PEP8 / typing skills into contracts
- Editing ai-skills-git or Ansible Jane in this session
- Replacing Bugbot / security-review / code-reviewer

## 4. Target model

```text
ai-skills-git (installed): git-review
this repo:
  docs/architecture/service-contracts.md   # SoT (+ layer map)
  docs/architecture/adr/                   # via architecture.adr_dir
  docs/architecture/project-strategy.md    # via architecture.strategy
  .git-pipeline.yml:
    always_load_review_skills: [git-review, pep8-review]
    architecture:
      contracts: docs/architecture/service-contracts.md
      adr_dir: docs/architecture/adr/
      strategy: docs/architecture/project-strategy.md
  (no skills/pr-architecture-review/)
```

Prerequisite: `git-review` available via `~/.agents/skills` (or Cursor/Claude
mirrors) from ai-skills-git `./scripts/install-agents.sh`.

---

## 5. Contracts content to absorb from the old skill

Ensure `service-contracts.md` has (add sections if missing; do not leave them
only in the deleted skill):

### 5a. Layer map (path → layer)

Copy/adapt the table from `skills/pr-architecture-review/SKILL.md` Step 1
(`server.py` → Orchestration, Domain modules, External Access, Foundation,
Build-time, CLI, templates, tests). Keep aligned with the Layer Architecture
diagram already in contracts.

### 5b. Dependency direction + known violations

Already partly in contracts (“Dependency Rules Summary”). Ensure V-D6 and
V-L3 (and any current IDs) appear as **Known exceptions / violations** with
severity and “do not worsen” rule.

### 5c. Hard rules (must-fix)

Fold skill Steps 3–6, 8–9 into hard/soft sections as appropriate:

| Topic (from old skill) | Suggested home in contracts |
|------------------------|-----------------------------|
| TypedDict / `types.py` / `AnsibleKnowError` | Type / API surface hard rules |
| Validation at Orchestration boundary | Boundary hard rules |
| `run_in_executor` / subprocess / `get_running_loop` | Async/sync hard rules |
| `SharedState` / `ServerState` / `SessionManager` / `BoundedCache` | State hard rules |
| `__all__` / no cross-module `_private` / MCP `ToolAnnotations` | Public API hard rules |
| `_FQCN_RE`, path containment, `sanitize_error`, credentials, truncate | Security hard rules |
| ADR-0006/0007/0008, tools marked for deprecation | ADR/strategy hard rules (or point to ADRs + strategy file) |

### 5d. Soft rules

PEP 8 naming / None comparisons / bare except can stay **out** of contracts
(covered by `pep8-review`). Keep only architecture-adjacent soft items if any.

### 5e. Companion skills table (optional section)

| When files match | Load skill (if installed) |
|------------------|---------------------------|
| `*.py` generally | `pep8-review` (also always-load) |
| Types / signatures | `tighten-types`, `contract-docstrings` |
| Exception paths | `try-except` |

---

## 6. `.git-pipeline.yml` changes

Replace:

```yaml
always_load_review_skills:
  - pep8-review
  - pr-architecture-review
```

With:

```yaml
always_load_review_skills:
  - git-review
  - pep8-review
architecture:
  contracts: docs/architecture/service-contracts.md
  adr_dir: docs/architecture/adr/
  strategy: docs/architecture/project-strategy.md
```

Keep existing test/lint/forge keys.

---

## 7. Delete / redirect project skill

**Preferred:** delete `skills/pr-architecture-review/` entirely.

**Optional interim:** replace `SKILL.md` with a ≤30-line stub:

- name may stay `pr-architecture-review` **only** if something external still
  references that string; otherwise do not keep the name
- Body: “Deprecated — use `git-review` + `docs/architecture/service-contracts.md`”
- Then delete in a follow-up once search shows no references

Search and update references in:

- `CLAUDE.md` / `AGENTS.md`
- `docs/**` (plans, specs, README)
- Issue/PR templates if any
- Memory / skill indexes

Self-update text that said “update `skills/pr-architecture-review/SKILL.md`”
must become “update `docs/architecture/service-contracts.md` (and ADRs)”.

---

## 8. Implementation checklist

Work only under ansible-know-mcp (use a feature worktree):

- [x] Diff old skill vs `service-contracts.md`; merge missing layer map +
      violations + hard rules into contracts; bump contracts version/date
- [x] Update `.git-pipeline.yml` (`git-review`, `architecture.*`)
- [x] Remove or stub `skills/pr-architecture-review/`
- [x] Grep-replace docs/CLAUDE references
- [x] Sanity: invoke `git-review` on a small branch (or dry-run classify against
      contracts layer map)
- [x] Validate no broken links to deleted skill
- [ ] Commit / PR (user asks)

Suggested branch: `chore/migrate-git-review`  
Suggested commit theme: migrate architecture review to git-review; contracts SoT

---

## 9. Acceptance criteria

1. `always_load_review_skills` includes `git-review`, not `pr-architecture-review`.
2. Layer map + known violations live in `service-contracts.md`.
3. No fat project architecture-review skill remains (or stub only, time-boxed).
4. CLAUDE/docs point agents at `git-review` + contracts.
5. Architecture drift → edit contracts/ADRs only.
6. `pep8-review` still always-loaded for Python quality.

## 10. Handoff blurb

```text
Implement docs/superpowers/specs/2026-08-08-git-review-migration.md in
ansible-know-mcp only. Use a worktree. Move layer map / violations / hard
rules from skills/pr-architecture-review into
docs/architecture/service-contracts.md. Point .git-pipeline.yml at
git-review + architecture.* keys; delete the old skill; update CLAUDE/docs.
Do not edit ai-skills-git or Jane. Do not commit unless I ask.
```

## 11. Implementation notes (2026-08-14)

- Historical `docs/superpowers/plans/*.md` / older specs may still *mention*
  `skills/pr-architecture-review` as a past instruction; left unchanged on
  purpose (audit trail). Live wiring is `.git-pipeline.yml` + this migration.
- CLAUDE.md / AGENTS.md / README had no live references to the old skill.

