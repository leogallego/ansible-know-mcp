# Project-Scoped Skills with AGENTS.md Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated skills land in the user's project directory by default and write an AGENTS.md section so all AI agents can discover them.

**Architecture:** Two new functions — `get_project_root()` in Foundation (`config.py`) and `update_agents_md()` in Domain (`skills.py`). Orchestration (`server.py`) calls both after collection skill generation. No new tools, no signature changes.

**Tech Stack:** Python 3.10+, pytest, pathlib, threading

**Spec:** `docs/superpowers/specs/2026-07-17-project-scoped-skills-design.md`
**Issue:** #181

## Global Constraints

- Layer rules: Foundation has no internal dependencies. Domain depends only on Foundation. Orchestration calls Domain and Foundation.
- Sync I/O in async handlers must use `run_in_executor()`.
- Paths from env vars must be validated against `_SENSITIVE_PREFIXES` before writing.
- Error messages must pass through `sanitize_error()`.
- All public functions in modules with `__all__` must be listed.
- Tests use `.venv/bin/pytest` (never bare `pytest`).
- No env var assignments inline with commands.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ansible_know/config.py` | Modify | Env var chain for `SKILLS_DIR`, new `get_project_root()` |
| `src/ansible_know/skills.py` | Modify | New `update_agents_md()`, add to `__all__` |
| `src/ansible_know/server.py` | Modify | Call `update_agents_md` from `generate_collection_skills` |
| `tests/test_config.py` | Modify | Tests for env var resolution and `get_project_root()` |
| `tests/test_skills.py` | Modify | Tests for AGENTS.md create/append/replace |

---

### Task 1: Env var resolution chain and `get_project_root()`

Changes `SKILLS_DIR` default resolution in `config.py` and adds `get_project_root()`. Foundation layer — no internal dependencies.

**Files:**
- Modify: `src/ansible_know/config.py:38-46`
- Modify: `tests/test_config.py` (add new test class)

**Interfaces:**
- Consumes: nothing
- Produces: `get_project_root() -> Path | None` (used by Task 3)

- [ ] **Step 1: Write failing tests for `get_project_root()`**

Add to `tests/test_config.py`:

```python
from ansible_know.config import get_project_root


class TestGetProjectRoot:
    def test_ansible_know_project_dir_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/should/not/win")
        assert get_project_root() == tmp_path

    def test_claude_project_dir_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        assert get_project_root() == tmp_path

    def test_cwd_fallback(self, monkeypatch):
        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        from pathlib import Path
        assert get_project_root() == Path.cwd()

    def test_empty_env_var_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", "")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        assert get_project_root() == tmp_path
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_config.py::TestGetProjectRoot -v
```

Expected: FAIL — `ImportError: cannot import name 'get_project_root'`

- [ ] **Step 3: Write failing tests for `SKILLS_DIR` env var chain**

Add to `tests/test_config.py`:

```python
from pathlib import Path
import importlib
import ansible_know.config as config_mod


class TestSkillsDirResolution:
    def _reload_skills_dir(self, monkeypatch):
        """Force SKILLS_DIR to re-resolve by clearing the cached value."""
        monkeypatch.delattr(config_mod, "SKILLS_DIR", raising=False)
        if "SKILLS_DIR" in config_mod.__dict__:
            del config_mod.__dict__["SKILLS_DIR"]
        return config_mod.SKILLS_DIR

    def test_explicit_skills_dir_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANSIBLE_KNOW_SKILLS_DIR", str(tmp_path / "explicit"))
        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "claude"))
        result = self._reload_skills_dir(monkeypatch)
        assert result == tmp_path / "explicit"

    def test_project_dir_adds_skills_suffix(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_DIR", raising=False)
        monkeypatch.setenv("ANSIBLE_KNOW_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        result = self._reload_skills_dir(monkeypatch)
        assert result == tmp_path / "skills"

    def test_claude_project_dir_adds_skills_suffix(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_DIR", raising=False)
        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        result = self._reload_skills_dir(monkeypatch)
        assert result == tmp_path / "skills"

    def test_cwd_fallback(self, monkeypatch):
        monkeypatch.delenv("ANSIBLE_KNOW_SKILLS_DIR", raising=False)
        monkeypatch.delenv("ANSIBLE_KNOW_PROJECT_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        result = self._reload_skills_dir(monkeypatch)
        assert result == Path.cwd() / "skills"
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_config.py::TestSkillsDirResolution -v
```

Expected: FAIL — some tests pass (current `ANSIBLE_KNOW_SKILLS_DIR` already works), others fail (new env vars not read yet)

- [ ] **Step 5: Implement `get_project_root()` and update `SKILLS_DIR` resolution**

In `src/ansible_know/config.py`, replace the existing `__getattr__` function (lines 38-46) with:

```python
def get_project_root() -> Path | None:
    """Return the project root from env vars, or cwd as fallback."""
    for var in ("ANSIBLE_KNOW_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(value)
    return Path.cwd()


def __getattr__(name: str):
    if name == "SKILLS_DIR":
        explicit = os.environ.get("ANSIBLE_KNOW_SKILLS_DIR", "").strip()
        if explicit:
            value = Path(explicit)
        else:
            root = get_project_root()
            value = root / "skills" if root else Path.cwd() / "skills"
        globals()["SKILLS_DIR"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_config.py::TestGetProjectRoot tests/test_config.py::TestSkillsDirResolution -v
```

Expected: all PASS

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
.venv/bin/pytest tests/ -x -q
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/ansible_know/config.py tests/test_config.py
git commit -m "feat: add env var chain for SKILLS_DIR and get_project_root()

SKILLS_DIR now resolves via priority chain:
ANSIBLE_KNOW_SKILLS_DIR → ANSIBLE_KNOW_PROJECT_DIR/skills →
CLAUDE_PROJECT_DIR/skills → cwd/skills.

New get_project_root() returns the project root from the same chain
(without /skills suffix), used by AGENTS.md logic in the next commit.

Closes #181 (partial)

Assisted-by: Claude Opus 4.6"
```

---

### Task 2: `update_agents_md()` function

Adds the AGENTS.md management function to `skills.py`. Domain layer — depends only on Foundation (`validation.py`, `pathlib`).

**Files:**
- Modify: `src/ansible_know/skills.py` (add function + `__all__` entry)
- Modify: `tests/test_skills.py` (add new test class)

**Interfaces:**
- Consumes: `skill_dir_to_collection_fqcn(kebab_dir: str) -> str` (same module, line 107)
- Produces: `update_agents_md(project_root: Path, skills_dir: Path) -> None` (used by Task 3)

- [ ] **Step 1: Write failing tests for AGENTS.md create/append/replace**

Add to `tests/test_skills.py`:

```python
from ansible_know.skills import update_agents_md


class TestUpdateAgentsMd:
    def _make_collection_skill(self, skills_dir, name):
        """Create a minimal collection skill directory with SKILL.md."""
        coll_dir = skills_dir / name
        coll_dir.mkdir(parents=True)
        (coll_dir / "SKILL.md").write_text(
            "---\nname: test\ndescription: test collection\n---\n"
        )
        # Add a module-level skill for example path generation
        mod_dir = coll_dir / "some-module"
        mod_dir.mkdir()
        (mod_dir / "SKILL.md").write_text(
            "---\nname: test-mod\ndescription: test module\n---\n"
        )

    def test_create_agents_md(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- ansible-know:skills:start -->" in agents_md
        assert "<!-- ansible-know:skills:end -->" in agents_md
        assert "netbox.netbox" in agents_md

    def test_append_to_existing(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        (tmp_path / "AGENTS.md").write_text("# My Project\n\nExisting content.\n")
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert agents_md.startswith("# My Project")
        assert "Existing content." in agents_md
        assert "<!-- ansible-know:skills:start -->" in agents_md
        assert "netbox.netbox" in agents_md

    def test_replace_between_sentinels(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "ansible-controller")
        existing = (
            "# My Project\n\n"
            "<!-- ansible-know:skills:start -->\n"
            "## Old content\n"
            "<!-- ansible-know:skills:end -->\n\n"
            "## Other section\n"
        )
        (tmp_path / "AGENTS.md").write_text(existing)
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "# My Project" in agents_md
        assert "Old content" not in agents_md
        assert "ansible.controller" in agents_md
        assert "## Other section" in agents_md

    def test_preserves_other_sentinels(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        existing = (
            "<!-- BEGIN ANSIBLE-DEVCONTAINER -->\n"
            "## Devcontainer stuff\n"
            "<!-- END ANSIBLE-DEVCONTAINER -->\n"
        )
        (tmp_path / "AGENTS.md").write_text(existing)
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "BEGIN ANSIBLE-DEVCONTAINER" in agents_md
        assert "Devcontainer stuff" in agents_md
        assert "ansible-know:skills:start" in agents_md

    def test_missing_end_sentinel_appends(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        existing = "# Project\n\n<!-- ansible-know:skills:start -->\nBroken\n"
        (tmp_path / "AGENTS.md").write_text(existing)
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert agents_md.count("ansible-know:skills:start") == 2
        assert "ansible-know:skills:end" in agents_md

    def test_sensitive_path_rejected(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "netbox-netbox")
        from pathlib import Path
        from ansible_know.validation import ValidationError
        import pytest
        with pytest.raises(ValidationError):
            update_agents_md(Path("/etc"), skills_dir)

    def test_empty_skills_dir(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "Available collections:" in agents_md

    def test_skips_symlinks(self, tmp_path):
        skills_dir = tmp_path / "skills"
        self._make_collection_skill(skills_dir, "real-collection")
        (skills_dir / "symlink-collection").symlink_to(skills_dir / "real-collection")
        update_agents_md(tmp_path, skills_dir)
        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "real.collection" in agents_md
        assert agents_md.count("real.collection") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_skills.py::TestUpdateAgentsMd -v
```

Expected: FAIL — `ImportError: cannot import name 'update_agents_md'`

- [ ] **Step 3: Implement `update_agents_md`**

Add to `src/ansible_know/skills.py`, after the existing `get_skill_sync` function (around line 325). Also add the import and lock at the top of the file:

Add `import threading` to the imports section (after `import stat` on line 13):

```python
import threading
```

Add the lock and sentinels after `PLUGIN_SKILL_DIR_RE` (line 37):

```python
_AGENTS_MD_START = "<!-- ansible-know:skills:start -->"
_AGENTS_MD_END = "<!-- ansible-know:skills:end -->"
_agents_md_lock = threading.Lock()
```

Add `"update_agents_md"` to the `__all__` list.

Add the function after `get_skill_sync`:

```python
def update_agents_md(project_root: Path, skills_dir: Path) -> None:
    """Write or update the managed AGENTS.md section listing generated skills."""
    validate_install_path(str(project_root))

    collections = []
    example_path = ""
    if skills_dir.exists():
        for entry in sorted(skills_dir.iterdir()):
            try:
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if (entry / "SKILL.md").exists():
                    fqcn = skill_dir_to_collection_fqcn(entry.name)
                    collections.append(fqcn)
                    if not example_path:
                        for sub in sorted(entry.iterdir()):
                            if sub.is_dir() and not sub.is_symlink() and (sub / "SKILL.md").exists():
                                example_path = f"{fqcn}.{skill_dir_to_short_fqcn(sub.name)}"
                                example_dir = f"skills/{entry.name}/{sub.name}/SKILL.md"
                                break
            except OSError:
                continue

    example_line = ""
    if example_path:
        example_line = f"\n(e.g., `{example_path}` → `{example_dir}`)."
    else:
        example_line = "."

    section = (
        f"{_AGENTS_MD_START}\n"
        f"## Ansible Module Skills\n"
        f"\n"
        f"Generated Ansible module documentation skills are in `skills/`.\n"
        f"Before writing tasks for a module, check for a SKILL.md in the\n"
        f"matching collection and module directory{example_line}\n"
        f"\n"
        f"Available collections: {', '.join(collections)}\n"
        f"{_AGENTS_MD_END}\n"
    )

    agents_md_path = project_root / "AGENTS.md"

    with _agents_md_lock:
        if not agents_md_path.exists():
            agents_md_path.write_text(section)
            return

        content = agents_md_path.read_text()

        if _AGENTS_MD_START in content and _AGENTS_MD_END in content:
            start_idx = content.index(_AGENTS_MD_START)
            end_idx = content.index(_AGENTS_MD_END) + len(_AGENTS_MD_END)
            if content[end_idx:end_idx + 1] == "\n":
                end_idx += 1
            content = content[:start_idx] + section + content[end_idx:]
            agents_md_path.write_text(content)
            return

        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + section
        agents_md_path.write_text(content)
```

- [ ] **Step 4: Add `validate_install_path` to the imports in `skills.py`**

In `src/ansible_know/skills.py`, update the import from `validation` (line 19-23) to include `validate_install_path`:

```python
from ansible_know.validation import (
    split_collection_fqcn,
    truncate_response,
    validate_install_path,
    validate_path_containment,
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_skills.py::TestUpdateAgentsMd -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -x -q
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/skills.py tests/test_skills.py
git commit -m "feat: add update_agents_md for cross-agent skill discovery

New function writes a managed section to AGENTS.md with sentinel
markers (<!-- ansible-know:skills:start/end -->). Three modes:
create if missing, append with sentinels, replace between sentinels.

Validates project_root against sensitive prefixes. Uses threading.Lock
to serialize concurrent writes. Skips symlinks during scanning.

Closes #181 (partial)

Assisted-by: Claude Opus 4.6"
```

---

### Task 3: Wire `update_agents_md` into `generate_collection_skills`

Calls `update_agents_md` from the Orchestration layer after skill generation completes. Wraps in `try/except OSError` so failures don't mask successful skill results.

**Files:**
- Modify: `src/ansible_know/server.py:1296-1306`

**Interfaces:**
- Consumes: `get_project_root() -> Path | None` from `config.py` (Task 1), `update_agents_md(project_root, skills_dir)` from `skills.py` (Task 2)
- Produces: no new interfaces — modifies existing `generate_collection_skills` behavior

- [ ] **Step 1: Add the `update_agents_md` call to `generate_collection_skills`**

In `src/ansible_know/server.py`, after the `write_collection_skill_package` call (after line 1294) and before the progress report (line 1296), insert:

```python
        from ansible_know.config import get_project_root

        project_root = get_project_root()
        if project_root is not None and project_root.is_dir():
            try:
                await run_in_executor(skills.update_agents_md, project_root, base_dir)
            except OSError as exc:
                logger.warning("AGENTS.md update failed: %s", sanitize_error(str(exc)))
```

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/pytest tests/ -x -q
```

Expected: all PASS (existing tests should not break — the new code only runs when `project_root` is a real directory)

- [ ] **Step 3: Lint check**

```bash
.venv/bin/ruff check src/ansible_know/server.py src/ansible_know/skills.py src/ansible_know/config.py
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add src/ansible_know/server.py
git commit -m "feat: call update_agents_md from generate_collection_skills

After writing all skill packages, updates AGENTS.md in the project
root with a managed section listing available collections. Wrapped
in try/except OSError so AGENTS.md failures never mask successful
skill generation results.

Closes #181

Assisted-by: Claude Opus 4.6"
```

---

### Task 4: Documentation updates

Updates tool descriptions and README with the new env var and VS Code example.

**Files:**
- Modify: `src/ansible_know/server.py` (tool description strings)
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing
- Produces: nothing (docs only)

- [ ] **Step 1: Update `generate_collection_skills` docstring**

In `src/ansible_know/server.py`, update the `generate_collection_skills` function docstring to mention AGENTS.md:

Current (around line 1098):
```python
    """Batch generate skills for an entire collection.

    Generates/updates the collection MANIFEST.json as a byproduct.
    Returns {"succeeded": int, "failed": int, "total": int, "manifest": dict, "collection_skill": str},
    or {"error": str} on failure."""
```

Change to:
```python
    """Batch generate skills for an entire collection.

    Generates/updates the collection MANIFEST.json as a byproduct.
    Updates AGENTS.md in the project root with a managed section
    listing available collections for cross-agent discovery.
    Returns {"succeeded": int, "failed": int, "total": int, "manifest": dict, "collection_skill": str},
    or {"error": str} on failure."""
```

- [ ] **Step 2: Update `install_to` parameter descriptions**

In `src/ansible_know/server.py`, update the `install_to` parameter annotation on `generate_skill` (line 890), `generate_role_skill` (line 952), `generate_plugin_skill` (line 1019), and `generate_collection_skills` (line 1082) from:

```python
"Optional absolute path to install the skill to"
```

to:

```python
"Optional absolute path to install the skill to. Defaults to the project directory (via ANSIBLE_KNOW_PROJECT_DIR or CLAUDE_PROJECT_DIR env vars, then cwd)."
```

- [ ] **Step 3: Add VS Code configuration example to README**

In `README.md`, after the existing "Global (after pip install or via uvx)" registration section, add:

```markdown
VS Code Copilot (`.vscode/mcp.json`):
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

- [ ] **Step 4: Lint and verify**

```bash
.venv/bin/ruff check src/ansible_know/server.py
```

Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py README.md
git commit -m "docs: document project-scoped skills and AGENTS.md

Update install_to parameter descriptions to mention project directory
default. Add AGENTS.md note to generate_collection_skills docstring.
Add VS Code MCP configuration example to README.

Assisted-by: Claude Opus 4.6"
```
