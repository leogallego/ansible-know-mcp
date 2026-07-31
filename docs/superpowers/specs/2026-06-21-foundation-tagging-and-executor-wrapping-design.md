# Foundation Tagging Module and Executor Wrapping

**Issues**: [#89](https://github.com/leogallego/ansible-know-mcp/issues/89), [#90](https://github.com/leogallego/ansible-know-mcp/issues/90)
**Date**: 2026-06-21
**Status**: Draft

## Problem

Two architectural issues identified during PR #88 review:

1. `skills.py` imports `derive_tags()` from `collection_manifest.py` — a Domain→Domain cross-import that violates the layer dependency rule (Domain → Foundation ONLY).
2. `get_skill()` in `server.py` calls `Path.resolve()`, `.exists()`, and `.read_text()` directly in an async handler without `run_in_executor()`, blocking the event loop.

## Scope

| Issue | What changes | What does NOT change |
|-------|-------------|---------------------|
| #89 | `derive_tags()` moves to new Foundation module | Function signature, behavior, callers' semantics |
| #90 | `get_skill()` blocking I/O extracted to sync helper | Tool API, return types, validation logic |

### Out of scope

- `list_skills()` — already fixed in PR #88 (`_list_skills_sync` + `run_in_executor`).
- `resource_skills_list()` / `resource_skill_content()` — sync handlers; FastMCP 3.2.4 wraps them via `call_sync_fn_in_threadpool()` automatically.
- `generate_manifest()` separation of concerns (#93) — separate PR.
- TypedDict tightening for `params` (#96, #101) — separate PR.

## Design

### Change 1: New Foundation module `tagging.py`

Create `src/ansible_know/tagging.py`:

```python
"""Tag derivation from module metadata (Foundation layer — no internal dependencies)."""

from __future__ import annotations

from typing import Any

__all__ = ["derive_tags"]


def derive_tags(fqcn: str, params: list[dict[str, Any]]) -> list[str]:
    """Heuristically derive tags from module name segments and parameters."""
    # exact current implementation, moved as-is
```

- Zero internal dependencies — only stdlib and `typing`.
- `params` is accepted but currently unused by the function body (only `fqcn` segments are checked). Keeping the parameter preserves the signature for future use (e.g., deriving tags from parameter names).

Update `collection_manifest.py`:
- Remove `derive_tags` from `__all__`.
- Remove the `derive_tags()` function definition entirely.
- Add top-level import `from ansible_know.tagging import derive_tags` (used internally by `generate_manifest()`).
- No re-export: `derive_tags` has only 2 internal callers and no external consumers, so a backward-compat shim is unnecessary.

Update `skills.py`:
- Change lazy import at line 215 from `from ansible_know.collection_manifest import derive_tags` to `from ansible_know.tagging import derive_tags`. This can become a top-level import since `tagging.py` is Foundation (no heavy dependencies).

Update `tests/test_collection_manifest.py`:
- Change `from ansible_know.collection_manifest import derive_tags` to `from ansible_know.tagging import derive_tags`.

### Change 2: Extract `get_skill()` blocking I/O

Create `_get_skill_sync()` in `server.py`:

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

Update `get_skill()` handler:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_skill(skill_name: ...) -> str | dict[str, str]:
    # ... docstring ...
    try:
        validate_skill_name(skill_name)  # pure string check — stays outside executor
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

Key decisions from architecture review:
- `validate_skill_name()` stays outside executor (pure regex check, no I/O).
- `validate_path_containment()` goes inside `_get_skill_sync()` because it calls `Path.resolve()` which is blocking I/O.
- `ValidationError` from inside the executor propagates up and is caught by the handler's existing `except ValidationError` block.

### Change 3: Documentation updates

Update `docs/architecture/service-contracts.md`:
- Add `tagging.py` to the Foundation layer table.
- Add `tagging.py` to the Foundation Interface Definition section.

Update `skills/pr-architecture-review/SKILL.md`:
- Add `tagging.py` to the Foundation file pattern table.

## Architecture Review Results

Reviewed against `service-contracts.md` and `skills/pr-architecture-review/SKILL.md`. No Error-level findings.

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| F4 | Warning | `_get_skill_sync` return type could use `ErrorResponse` TypedDict | Accepted — use `str \| dict[str, str]` for consistency with `_list_skills_sync` pattern; tightening is #92 scope |
| F6 | Warning | `validate_path_containment()` must be inside executor | Addressed — included in `_get_skill_sync` |
| F8 | Warning | `__all__` changes needed for both modules | Addressed — spec includes both |
| F13 | Warning | Path construction/validation/reads must be co-located | Addressed — all inside `_get_skill_sync` |
| R-F3 | Warning | Re-export ambiguity in `collection_manifest.py` | Addressed — no re-export, clean removal, both callers import from `tagging` directly |
| R-F4 | Warning | `tests/test_collection_manifest.py` not listed as affected | Addressed — added to Change 1 affected files |
| R-F6 | Info | Parameter order inconsistency with `_list_skills_sync` | Addressed — aligned to `(skills_dir, skill_name)` to match sibling function |

## Test Plan

- Existing `test_get_skill` tests cover the functional behavior — no new test cases needed for the extraction refactor.
- Existing `test_collection_manifest` tests cover `derive_tags` behavior — no change in behavior, only import location.
- Add one unit test for `_get_skill_sync` directly (mirrors `_list_skills_sync` test pattern).
- Add one unit test importing `derive_tags` from `tagging` to verify the module works.
- Verify `ruff check` passes (import ordering, unused imports).
- Verify full test suite passes (`pytest tests/ -q`).
