# Project-Scoped Skills with AGENTS.md Discovery — Design Spec

**Date:** 2026-07-17
**Status:** Approved
**Issue:** #181

## Problem

Skill generation tools (`generate_skill`, `generate_role_skill`, `generate_plugin_skill`, `generate_collection_skills`) default to writing skills under the MCP server's own `skills/` directory. For globally installed servers (`uvx ansible-know-mcp`), this means skills pile up in `~/.cache/uv/...` or wherever the server process started — not in the user's project.

Additionally, no AI coding agent auto-discovers skills from a generic `skills/` directory. Each agent has its own path (`.claude/skills/`, `.github/skills/`, etc.), but `AGENTS.md` is read by 30+ agents and can point them all to a single location.

## Goals

1. Skills land in the user's project directory by default, with zero configuration for Claude Code users
2. An AGENTS.md section tells all AI agents where to find the generated skills
3. The solution is agent-agnostic — not tied to any specific AI coding assistant
4. Backward compatible — explicit `install_to` and `ANSIBLE_KNOW_SKILLS_DIR` still work

## Non-Goals

- Multi-path search (searching both project-local and server-local skills directories) — tracked as a follow-up issue
- Writing skills to agent-specific directories (`.claude/skills/`, `.github/skills/`) — AGENTS.md covers all agents universally
- Changes to `list_skills` or `get_skill` signatures

## Design

### 1. Skills Directory Resolution

The `SKILLS_DIR` lazy attribute in `config.py` changes from a single-source default to a priority chain. First non-empty env var wins:

| Priority | Source | Set by | Example |
|----------|--------|--------|---------|
| 1 | `ANSIBLE_KNOW_SKILLS_DIR` | User (explicit override) | `/opt/shared-skills` |
| 2 | `ANSIBLE_KNOW_PROJECT_DIR` + `/skills` | User or client config (agent-agnostic) | `/workspace/my-project/skills` |
| 3 | `CLAUDE_PROJECT_DIR` + `/skills` | Claude Code (auto-injected into MCP servers) | `/home/user/my-project/skills` |
| 4 | `Path.cwd()` + `/skills` | OS (current behavior, fallback) | Depends on how server was launched |

The `__getattr__` lazy pattern in `config.py` walks the chain once at first access and caches the result. Same pattern as today — just more sources.

A new helper `get_project_root()` returns the project root (same chain without the `/skills` suffix), used by AGENTS.md logic.

**Why this order:**
- `ANSIBLE_KNOW_SKILLS_DIR` is explicit — power users who know exactly where they want skills
- `ANSIBLE_KNOW_PROJECT_DIR` is our own agent-agnostic env var — any client can set it
- `CLAUDE_PROJECT_DIR` is auto-injected by Claude Code (v2.1.139+) into every spawned MCP server process — zero config for the primary user base
- `Path.cwd()` is the fallback — works correctly for VS Code (which sets `cwd` to workspace folder) and for project-scoped `.mcp.json` registrations

**Client behavior summary:**

| Client | `cwd` reliability | Auto-injected env var |
|--------|-------------------|----------------------|
| Claude Code CLI | Project dir | `CLAUDE_PROJECT_DIR` |
| Claude Code Desktop | `$HOME` (bug) | `CLAUDE_PROJECT_DIR` |
| VS Code Copilot | Workspace folder (reliable) | None — user sets `ANSIBLE_KNOW_PROJECT_DIR` in `.vscode/mcp.json` env |
| `uvx` launch | `~/.cache/uv/...` (wrong) | `CLAUDE_PROJECT_DIR` (if launched by Claude Code) |
| Devcontainer | `/workspaces/<project>` (reliable) | Depends on client inside container |

### 2. AGENTS.md Management

`generate_collection_skills` writes a managed section to `AGENTS.md` in the project root after generating skills. The project root is resolved from `get_project_root()`.

#### Sentinel markers

The managed section uses `<!-- ansible-know:skills:start -->` and `<!-- ansible-know:skills:end -->` sentinels. These are distinct from other tools' markers (e.g., the devcontainer uses `<!-- BEGIN/END ANSIBLE-DEVCONTAINER -->`). Both can coexist in the same file.

#### Three modes

| Condition | Behavior |
|-----------|----------|
| No AGENTS.md exists | Create it with the managed section (including sentinels) |
| AGENTS.md exists, no sentinels | Append the managed section (with sentinels) at the end |
| AGENTS.md exists, sentinels present | Replace content between sentinels |

All three modes converge to the same state: an AGENTS.md with the managed section wrapped in sentinels.

#### Managed section content

```markdown
<!-- ansible-know:skills:start -->
## Ansible Module Skills

Generated Ansible module documentation skills are in `skills/`.
Before writing tasks for a module, check for a SKILL.md in the
matching collection and module directory
(e.g., `netbox.netbox.netbox_device` → `skills/netbox-netbox/netbox-device/SKILL.md`).

Available collections: ansible.controller, netbox.netbox
<!-- ansible-know:skills:end -->
```

The "Available collections" line and the example path are rebuilt by scanning the skills directory at generation time. The example is picked from the first actual module-level skill found (omitted if only collection-level skills exist). This keeps the section accurate regardless of naming convention changes (e.g., ADR-0007 kebab-case migration, #148).

#### What triggers updates

Only `generate_collection_skills` updates AGENTS.md. Single-module/role/plugin generators (`generate_skill`, `generate_role_skill`, `generate_plugin_skill`) do not — they're typically called by `generate_collection_skills` anyway, and per-module updates would be noisy.

### 3. Implementation

#### New function: `update_agents_md`

Lives in `skills.py` (alongside existing skill I/O functions).

```python
def update_agents_md(project_root: Path, skills_dir: Path) -> None:
```

- Validates `project_root` against `_SENSITIVE_PREFIXES` via `validate_install_path()` — blocks writes to `/etc`, `/usr`, etc.
- Acquires a module-level `threading.Lock` to serialize concurrent writes
- Scans `skills_dir` for collection directories containing `SKILL.md` (skip symlinks and non-directories, following `list_skills_sync` pattern)
- Builds the managed section with the collection list
- Reads `project_root / "AGENTS.md"` (or empty string if missing)
- Applies create/append/replace logic based on sentinel presence. If start sentinel is found but end sentinel is missing, falls back to append mode.
- Writes the result
- Must be added to `skills.__all__`

Called from `generate_collection_skills` in `server.py` after all skills are written. Since this is a sync function doing file I/O called from an async tool handler, it must be wrapped in `run_in_executor()` — same pattern as `write_module_skill_package` and friends.

The call in `server.py` must be wrapped in its own `try/except OSError` so AGENTS.md failures don't mask successful skill generation:

```python
project_root = get_project_root()
if project_root is not None and project_root.is_dir():
    try:
        await run_in_executor(skills.update_agents_md, project_root, base_dir)
    except OSError as exc:
        logger.warning("AGENTS.md update failed: %s", sanitize_error(str(exc)))
```

#### New function: `get_project_root`

Lives in `config.py`.

```python
def get_project_root() -> Path | None:
```

Walks the env var chain (`ANSIBLE_KNOW_PROJECT_DIR` → `CLAUDE_PROJECT_DIR` → `cwd`). Returns `None` if no project root can be determined (defensive — shouldn't happen in practice since `cwd` always exists).

`ANSIBLE_KNOW_SKILLS_DIR` is intentionally excluded from this chain — it points to a skills directory, not a project root. A user who sets `ANSIBLE_KNOW_SKILLS_DIR=/opt/shared/skills` doesn't want AGENTS.md written to `/opt/shared/`.

When `get_project_root()` returns `None`, `generate_collection_skills` skips the AGENTS.md update silently (log a debug message). Skills are still written to `SKILLS_DIR` — only the AGENTS.md step is skipped.

#### Files changed

| File | Change |
|------|--------|
| `config.py` | `__getattr__` for `SKILLS_DIR`: add env var chain. New `get_project_root()` function. |
| `skills.py` | New `update_agents_md(project_root, skills_dir)` function. |
| `server.py` | `generate_collection_skills`: call `update_agents_md` after writing skills. |
| `tests/test_skills.py` | Unit tests for `update_agents_md` (create, append, replace modes). |
| `tests/test_config.py` | Unit tests for env var resolution chain. |

No changes to `list_skills`, `get_skill`, `generate_skill`, `generate_role_skill`, `generate_plugin_skill`, validation, or any resource/prompt.

### 4. Testing

#### Unit tests for env var resolution

Mock `os.environ` to test each priority level:
- All set → `ANSIBLE_KNOW_SKILLS_DIR` wins
- Only `ANSIBLE_KNOW_PROJECT_DIR` → uses that + `/skills`
- Only `CLAUDE_PROJECT_DIR` → uses that + `/skills`
- None set → falls back to `cwd/skills`

#### Unit tests for AGENTS.md

Three test cases with `tmp_path` fixtures:
- **Create mode**: no AGENTS.md → creates with sentinels and collection list
- **Append mode**: existing AGENTS.md without sentinels → appends section with sentinels, preserves existing content
- **Replace mode**: existing AGENTS.md with sentinels → replaces between sentinels, preserves content before/after

#### Integration consideration

The AGENTS.md update runs inside `generate_collection_skills`, which already has integration tests. The new behavior can be verified by checking that AGENTS.md exists after a collection skill generation run.

### 5. Documentation

Update tool descriptions for `generate_skill`, `generate_role_skill`, `generate_plugin_skill`, and `generate_collection_skills` to mention that `install_to` defaults to the project directory (not the server directory).

Update README registration section to note that `ANSIBLE_KNOW_PROJECT_DIR` can be set for non-Claude clients.

Add VS Code example to README:

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

## Alignment addendum

Discoverability across host agents, next-mcp `SkillRegistry`, and Lola is
specified in
[2026-08-02-skill-discoverability-alignment.md](2026-08-02-skill-discoverability-alignment.md).
That addendum supersedes follow-up item 3 below (AGENTS.md is Layer 1 host
discovery, not Layer 2) and defines dual-config with `ANSIBLE_SKILL_SOURCES`.

## Follow-Up Issues

1. **Multi-path search for `list_skills`/`get_skill`** (#182): search both project-local and a secondary path (e.g., pre-baked devcontainer skills). Enables shared + project-specific skill coexistence. Blocked on this design landing first.
2. **`generate_collection_skills` AGENTS.md for single-module generators**: if users frequently call `generate_skill` directly (not via collection batch), consider updating AGENTS.md from those tools too.
3. ~~**Update ADR-0008**: add AGENTS.md as a third Layer 2 consumption path~~ — **Superseded** by the alignment addendum: AGENTS.md is Layer 1; ADR-0008 revision is tracked under #196.
4. **Validate `SKILLS_DIR` against sensitive prefixes**: pre-existing gap — `ANSIBLE_KNOW_SKILLS_DIR` env var is not validated through `validate_install_path()`. Include in #181 implementation (see alignment addendum).

## References

- Research: `CLAUDE_PROJECT_DIR` env var ([Claude Code MCP docs](https://code.claude.com/docs/en/mcp), v2.1.139+)
- Research: VS Code `${workspaceFolder}` ([VS Code MCP config reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration))
- Research: AGENTS.md read by 30+ agents ([AAIF](https://aaif.io))
- Research: agentskills.io SKILL.md format shared by Claude Code, Copilot, Codex CLI, Cursor, Amp, Goose
- Devcontainer multi-harness plan: `/home/lgallego/Claude/claude-code-devcontainer/docs/superpowers/plans/2026-07-17-multi-harness-discovery.md`
- MCP spec 2026-07-28 RC: issue #125 (roots deprecated — env vars are the future-proof approach)
