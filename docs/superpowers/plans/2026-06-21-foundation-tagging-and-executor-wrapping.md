# Foundation Tagging Module and Executor Wrapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the Domain→Domain cross-import of `derive_tags` (#89) and wrap `get_skill()` blocking I/O in `run_in_executor` (#90).

**Architecture:** Move `derive_tags()` to a new Foundation module `tagging.py`. Extract `get_skill()` file I/O into `_get_skill_sync()` helper wrapped with `run_in_executor()`, matching the existing `_list_skills_sync` pattern.

**Tech Stack:** Python 3.13, FastMCP 3.2, pytest, ruff

## Global Constraints

- Foundation modules must have zero internal dependencies (only stdlib + typing).
- All blocking file I/O in async handlers must go through `run_in_executor()`.
- `validate_path_containment()` calls `Path.resolve()` (blocking I/O) — must be inside executor.
- `validate_skill_name()` is a pure regex check — stays outside executor.
- Sync helper parameter order: `(skills_dir, ...)` to match `_list_skills_sync(skills_dir, collection)`.
- All modules with public functions must define `__all__`.
- **Sandbox mode:** Use `git` CLI for local ops and `MCP GitHub` for API operations (PRs, issues). `gh` CLI is unavailable in sandbox. Load skill `sandbox-git-github` before git/GitHub operations.

## Skills Reference

Subagents must load the relevant skills before starting each task:

| Skill | When to load | Purpose |
|-------|-------------|---------|
| `sandbox-git-github` | Before any git commit or GitHub operation | Git/GitHub in sandboxed environments |
| `skills/pr-architecture-review/SKILL.md` | After both tasks, before PR | Verify changes against architecture contracts |
| `skills/python-contract-docstrings/SKILL.md` | Task 1 Step 3 (creating `tagging.py`) | Verify docstring documents the function contract |
| `skills/python-try-except/SKILL.md` | Task 2 Step 2-3 (restructuring try/except in `get_skill`) | Verify try blocks are scoped correctly |
| `skills/mcp-builder/SKILL.md` | Task 2 Step 3 (modifying MCP tool handler) | MCP server development patterns |

---

### Task 1: Create `tagging.py` Foundation module and migrate `derive_tags`

**Files:**
- Create: `src/ansible_know/tagging.py`
- Modify: `src/ansible_know/collection_manifest.py:5-6,19-20,26-52` (remove function, update imports/`__all__`)
- Modify: `src/ansible_know/skills.py:215` (change import source)
- Modify: `tests/test_collection_manifest.py:5-6` (change import source)
- Modify: `docs/architecture/service-contracts.md:30-32,216-224` (add to Foundation tables, mark stale violations fixed)
- Modify: `skills/pr-architecture-review/SKILL.md:36-41` (add missing Foundation file patterns)
- Modify: `CLAUDE.md` (add `tagging.py` to architecture diagram)

**Interfaces:**
- Produces: `tagging.derive_tags(fqcn: str, params: list[dict[str, Any]]) -> list[str]` — consumed by `collection_manifest.generate_manifest()` and `skills._collection_template_context()`

- [ ] **Step 1: Write the test for `tagging.derive_tags`**

Add a new test file `tests/test_tagging.py`:

```python
"""Tests for ansible_know.tagging."""

from ansible_know.tagging import derive_tags


class TestDeriveTagsFromTagging:
    def test_import_and_basic_tag(self):
        tags = derive_tags("netbox.netbox.ip_address", [])
        assert "ipam" in tags

    def test_no_matching_tags(self):
        tags = derive_tags("custom.collection.something_unique", [])
        assert tags == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tagging.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'ansible_know.tagging'`

- [ ] **Step 3: Create `src/ansible_know/tagging.py`**

> **Skill:** Load `skills/python-contract-docstrings/SKILL.md` to verify the docstring documents the function contract.

```python
"""Tag derivation from module metadata (Foundation layer — no internal dependencies)."""

from __future__ import annotations

from typing import Any

__all__ = ["derive_tags"]


def derive_tags(fqcn: str, params: list[dict[str, Any]]) -> list[str]:
    """Heuristically derive tags from module name segments and parameters."""
    parts = fqcn.split(".")
    module_short = parts[-1] if parts else fqcn

    tags: set[str] = set()
    tag_hints = {
        "user": "identity", "group": "identity", "role": "identity",
        "network": "networking", "interface": "networking", "vlan": "networking",
        "firewall": "security", "acl": "security", "cert": "security",
        "file": "files", "copy": "files", "template": "files",
        "package": "packages", "apt": "packages", "yum": "packages", "dnf": "packages",
        "service": "services", "systemd": "services",
        "docker": "containers", "podman": "containers", "container": "containers",
        "ip": "ipam", "prefix": "ipam", "subnet": "ipam", "address": "ipam",
        "device": "dcim", "rack": "dcim", "site": "dcim",
        "vm": "virtualization", "virtual": "virtualization",
        "cloud": "cloud", "ec2": "cloud", "azure": "cloud", "gcp": "cloud",
        "db": "database", "database": "database", "mysql": "database", "postgres": "database",
    }

    for segment in module_short.split("_"):
        segment_lower = segment.lower()
        if segment_lower in tag_hints:
            tags.add(tag_hints[segment_lower])

    return sorted(tags)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tagging.py -v`

Expected: PASS — both tests green.

- [ ] **Step 5: Update `collection_manifest.py` — remove function, add import**

In `src/ansible_know/collection_manifest.py`:

Remove `"derive_tags"` from `__all__` (line 20), so it becomes:

```python
__all__ = [
    "generate_manifest",
    "load_cached_manifest",
]
```

Remove the entire `derive_tags()` function definition (lines 26–52).

Add the import after the existing `from ansible_know.config import SKILLS_DIR` line (line 14):

```python
from ansible_know.config import SKILLS_DIR
from ansible_know.tagging import derive_tags
```

The function is still called at line 94 inside `generate_manifest()` — the new import provides it.

- [ ] **Step 6: Update `skills.py` — change import source**

In `src/ansible_know/skills.py`, change the lazy import at line 215 inside `_collection_template_context()` from:

```python
    from ansible_know.collection_manifest import derive_tags
```

to:

```python
    from ansible_know.tagging import derive_tags
```

- [ ] **Step 7: Update `tests/test_collection_manifest.py` — change import source**

In `tests/test_collection_manifest.py`, change lines 5–6 from:

```python
from ansible_know.collection_manifest import (
    derive_tags,
    generate_manifest,
    load_cached_manifest,
)
```

to:

```python
from ansible_know.collection_manifest import (
    generate_manifest,
    load_cached_manifest,
)
from ansible_know.tagging import derive_tags
```

- [ ] **Step 8: Update `docs/architecture/service-contracts.md` — add `tagging.py` to Foundation**

In the layer diagram (line 30–32), add `tagging.py` to the Foundation list:

```
│  Foundation        async_utils.py, cache.py, config.py,  │
│                    galaxy_config.py, state.py,            │
│                    tagging.py, validation.py,             │
│                    errors.py, types.py                    │
```

In the Interface Definition table (after line 224), add a row:

```markdown
| `tagging.py` | Tag derivation from module FQCN segments | `tagging.py` |
```

Also in `service-contracts.md`, mark stale `__all__` violations as fixed. Find V-D2 through V-D5 (which flagged missing `__all__` in `parser.py`, `skills.py`, `collection_manifest.py`, `docs.py`) and strike them through or add "(Fixed)" since all four modules now define `__all__`.

- [ ] **Step 9: Update `skills/pr-architecture-review/SKILL.md` — add missing Foundation file patterns**

After line 41 (`types.py` entry), add all Foundation modules currently missing from the table:

```markdown
| `src/ansible_know/async_utils.py` | **Foundation** |
| `src/ansible_know/state.py` | **Foundation** |
| `src/ansible_know/tagging.py` | **Foundation** |
```

- [ ] **Step 10: Update `CLAUDE.md` — add `tagging.py` to architecture diagram**

In the `CLAUDE.md` architecture section under `src/ansible_know/`, add `tagging.py` with its description. Insert it in alphabetical order:

```
├── tagging.py             # Tag derivation from module metadata (Foundation)
```

- [ ] **Step 11: Run full test suite and lint**

Run: `.venv/bin/pytest tests/ -q`

Expected: All tests pass (no behavior change, only import locations moved).

Run: `.venv/bin/ruff check src/ tests/`

Expected: Clean — no lint errors (verify no unused imports from old location).

- [ ] **Step 12: Commit**

> **Skill:** Load `sandbox-git-github` before this step if in sandbox mode.

```bash
git add src/ansible_know/tagging.py tests/test_tagging.py \
  src/ansible_know/collection_manifest.py src/ansible_know/skills.py \
  tests/test_collection_manifest.py \
  docs/architecture/service-contracts.md skills/pr-architecture-review/SKILL.md \
  CLAUDE.md
git commit -m "refactor: move derive_tags to Foundation tagging module (#89)"
```

---

### Task 2: Extract `get_skill()` blocking I/O into `_get_skill_sync`

**Files:**
- Modify: `src/ansible_know/server.py:564,644-689` (add sync helper, refactor handler)
- Test: `tests/test_server.py:324-348` (existing tests verify behavior is preserved)

**Interfaces:**
- Consumes: `run_in_executor` from `async_utils.py`, `validate_skill_name` and `validate_path_containment` from `validation.py`
- Produces: `_get_skill_sync(skills_dir: Path, skill_name: str) -> str | dict[str, str]` — internal only, called by `get_skill()` handler

- [ ] **Step 1: Run existing `get_skill` tests to establish baseline**

Run: `.venv/bin/pytest tests/test_server.py::TestSkillNameValidation tests/test_server.py::TestPathTraversal::test_get_skill_blocks_traversal -v`

Expected: All 3 tests PASS — this is our regression baseline.

- [ ] **Step 2: Add `_get_skill_sync` helper in `server.py`**

> **Skill:** Load `skills/python-try-except/SKILL.md` and `skills/mcp-builder/SKILL.md` before this step.

Add this function between the `list_skills()` handler (ends line 641) and the `get_skill()` handler (line 644), matching the pattern where `_list_skills_sync` sits immediately before `list_skills`:

```python
def _get_skill_sync(
    skills_dir: Path, skill_name: str,
) -> str | dict[str, str]:
    """Synchronous helper for get_skill — all file I/O happens here."""
    parts = skill_name.split(".")
    if len(parts) >= 3:
        namespace = ".".join(parts[:2])
        short_name = ".".join(parts[2:])
        nested_path = (skills_dir / namespace / short_name / "SKILL.md").resolve()
        validate_path_containment(nested_path, skills_dir)
        if nested_path.exists():
            return truncate_response(nested_path.read_text())

        flat_path = (skills_dir / skill_name / "SKILL.md").resolve()
        validate_path_containment(flat_path, skills_dir)
        if flat_path.exists():
            return truncate_response(flat_path.read_text())
    else:
        skill_path = (skills_dir / skill_name / "SKILL.md").resolve()
        validate_path_containment(skill_path, skills_dir)
        if skill_path.exists():
            return truncate_response(skill_path.read_text())

    return {"error": f"Skill '{skill_name}' not found."}
```

- [ ] **Step 3: Refactor `get_skill()` handler to use the sync helper**

Replace the body of `get_skill()` (lines 656–689) with:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_skill(
    skill_name: Annotated[
        str,
        "Skill name: a module FQCN (e.g. 'netbox.netbox.netbox_device') or "
        "a collection namespace (e.g. 'netbox.netbox') for the collection-level skill.",
    ],
) -> str | dict[str, str]:
    """Read a specific skill's SKILL.md content by name.

    Returns: SKILL.md content as str, or {"error": str} on failure/not found.
    """
    logger.info("get_skill name=%r", skill_name)
    try:
        validate_skill_name(skill_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know.config import SKILLS_DIR

        return await run_in_executor(_get_skill_sync, SKILLS_DIR, skill_name)
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("get_skill failed: %s", exc)
        return {"error": sanitize_error(str(exc))}
```

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `.venv/bin/pytest tests/test_server.py::TestSkillNameValidation tests/test_server.py::TestPathTraversal::test_get_skill_blocks_traversal -v`

Expected: All 3 tests PASS — same behavior, now via executor.

- [ ] **Step 5: Run full test suite and lint**

Run: `.venv/bin/pytest tests/ -q`

Expected: All tests pass.

Run: `.venv/bin/ruff check src/ tests/`

Expected: Clean.

- [ ] **Step 6: Commit**

> **Skill:** Load `sandbox-git-github` before this step if in sandbox mode.

```bash
git add src/ansible_know/server.py
git commit -m "refactor: wrap get_skill blocking I/O in run_in_executor (#90)"
```
