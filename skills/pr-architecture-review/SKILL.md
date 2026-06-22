---
name: pr-architecture-review
description: >-
  Review PR changes against ansible-know-mcp architecture contracts.
  Auto-detects which layers are affected and checks for violations.
---

# PR Architecture Review

Review pull request changes against the documented service contracts for
the Ansible Know MCP Server. This skill auto-detects which architecture
layers are affected by the changed files and runs the relevant checks.

## Prerequisites

Read `docs/architecture/service-contracts.md` and the ADRs in
`docs/architecture/adr/` before using this skill. The contracts define
the layer boundaries and allowed dependencies.

## Step 1: Identify Changed Files and Affected Layers

Run `git diff --name-only <base>..HEAD` (or the PR's file list) and
classify each changed file into its architecture layer:

| File Pattern | Layer |
|-------------|-------|
| Framework config, transport setup | **Transport** |
| `src/ansible_know/server.py` | **Orchestration** |
| `src/ansible_know/parser.py` | **Domain** |
| `src/ansible_know/skills.py` | **Domain** |
| `src/ansible_know/collection_manifest.py` | **Domain** |
| `src/ansible_know/docs.py` | **Domain** |
| `src/ansible_know/galaxy.py` | **External Access** |
| `src/ansible_know/collections.py` | **External Access** |
| `src/ansible_know/readme_parser.py` | **External Access** |
| `src/ansible_know/cache.py` | **Foundation** |
| `src/ansible_know/config.py` | **Foundation** |
| `src/ansible_know/galaxy_config.py` | **Foundation** |
| `src/ansible_know/async_utils.py` | **Foundation** |
| `src/ansible_know/state.py` | **Foundation** |
| `src/ansible_know/tagging.py` | **Foundation** |
| `src/ansible_know/validation.py` | **Foundation** |
| `src/ansible_know/errors.py` | **Foundation** |
| `src/ansible_know/types.py` | **Foundation** |
| `src/ansible_know/templates/*` | **Domain** (templates) |
| `tests/*` | **Test** (check mirrors source layer) |

Files that do not match any pattern (e.g., `README.md`, `pyproject.toml`,
CI configs) do not require architecture review.

## Step 2: Check Layer Dependency Rules

For each changed file, verify that its imports respect the allowed
dependency direction:

```
Transport    → (framework-managed, no application code)
Orchestration → Domain, External Access, Foundation
Domain       → Foundation ONLY
External     → Foundation ONLY
Foundation   → no internal dependencies
```

### What to look for

- [ ] **Domain module importing External Access**: e.g., `parser.py`
  importing from `galaxy.py` or `collections.py`. Domain modules should
  only depend on Foundation (`config`, `errors`, `types`, `validation`).
- [ ] **External Access importing Domain**: e.g., `galaxy.py` importing
  from `parser.py` or `skills.py`. External Access should only depend
  on Foundation.
- [ ] **Foundation importing any upper layer**: Foundation modules must
  have zero dependencies on Orchestration, Domain, or External Access.
- [ ] **Orchestration containing business logic**: `server.py` should
  delegate to Domain modules, not implement resolution strategies,
  data transformations, or caching logic directly.

### Known existing violations (do not worsen)

- `server.py` contains `_resolve_module_doc()` and `_resolve_role_doc()`
  (business logic in Orchestration — V-D7/V-L2).
- `server.py` calls `GalaxyClient` directly (V-E1/V-L1).
- `galaxy.py` imports `readme_parser` (External → Domain — V-L3).
- `parser.py` imports `collections` (Domain → External — V-L4).

**Rule**: do not add new violations. If a PR must cross a layer boundary,
document why and file a follow-up issue.

## Step 3: Check Type Contracts

For changes that modify function signatures or return types:

- [ ] **Typed returns**: functions returning structured data should use
  the `TypedDict` definitions from `types.py` (`ModuleMetadata`,
  `RoleMetadata`, `DocProvenance`), not bare `dict[str, Any]`.
- [ ] **New TypedDicts**: if a function returns a new structured shape,
  add a `TypedDict` to `types.py` rather than using `dict[str, Any]`.
- [ ] **Exception types**: new error conditions should use the existing
  exception hierarchy from `errors.py`. Never raise bare `Exception`
  or `ValueError` — use `AnsibleKnowError` subclasses.
- [ ] **Validation at boundary**: new tool parameters must be validated
  in the Orchestration layer (using `validation.py` functions) before
  being passed to Domain or External Access.

## Step 4: Check Async/Sync Boundary

For changes that add or modify function calls between layers:

- [ ] **Sync functions in async context**: any synchronous function that
  does I/O (subprocess, file system, network) MUST be wrapped in
  `_run_in_executor()` when called from an async handler. Never call
  blocking functions directly from a tool handler.
- [ ] **New subprocess calls**: must use `subprocess.run()` with
  `capture_output=True`, `text=True`, and a `timeout` parameter.
  Never use `subprocess.Popen` or `os.system`.
- [ ] **`asyncio.get_event_loop()`**: do not use. Use
  `asyncio.get_running_loop()` instead (PEP deprecated pattern).

## Step 5: Check State Management

For changes that add or modify module-level state:

- [ ] **New module-level mutable state**: any new `dict`, `set`, `list`,
  or mutable object at module scope MUST have explicit thread-safety
  documentation. If accessed from `_run_in_executor()` threads, it
  MUST use `threading.Lock`. If accessed from async code only, it
  SHOULD use `asyncio.Lock`.
- [ ] **Cache additions**: new caches SHOULD use `BoundedCache` from
  `cache.py` (thread-safe, LRU-bounded, optional TTL). Do not
  introduce new ad-hoc `OrderedDict` + `Lock` patterns — use the
  shared abstraction. If `BoundedCache` does not fit the use case,
  document why.
- [ ] **Global state mutation**: check that `set.add()`, `dict[key] = val`,
  and similar mutations on module-level state are safe under the
  concurrency model being used.

## Step 6: Check Public API Surface

For changes that add, rename, or remove public functions/classes:

- [ ] **`__all__` consistency**: if the module defines `__all__`, verify
  the new function/class is listed (if public) or prefixed with `_`
  (if internal).
- [ ] **Private function access**: no module should call another module's
  `_private()` functions. If access is needed, the function should be
  made public.
- [ ] **MCP tool/resource/prompt changes**: new tools must include
  `ToolAnnotations` with appropriate hints (`readOnlyHint`,
  `idempotentHint`, `destructiveHint`). New resources must have
  descriptive names and descriptions.

## Step 7: PEP 8 and Python Standards

For all Python changes, verify:

- [ ] **Naming**: `snake_case` for functions/variables, `CapWords` for
  classes, `UPPER_CASE` for constants, `_leading_underscore` for
  non-public names (PEP 8 Section 8).
- [ ] **Type annotations**: spaces after colons (`x: int`), spaces around
  arrows (`-> int`), spaces around `=` in annotated defaults
  (`x: int = 0`) (PEP 8 Sections 10-11).
- [ ] **None comparisons**: use `is` / `is not`, never `==` / `!=`
  (PEP 8 Section 9).
- [ ] **Exception handling**: no bare `except:`, use `raise X from Y`
  for chaining, derive from `Exception` not `BaseException`
  (PEP 8 Section 9).
- [ ] **Sequence truthiness**: use `if not seq:` instead of
  `if len(seq) == 0:` (PEP 8 Section 9).

## Step 8: Security Review

For changes that handle external input or subprocess calls:

- [ ] **Input validation**: all tool inputs must be validated before use.
  FQCNs must match `_FQCN_RE`, namespaces `_NAMESPACE_RE`, etc.
- [ ] **Path traversal**: any path derived from user input must be
  validated with `validate_path_containment()` and
  `validate_install_path()`.
- [ ] **Error sanitization**: error messages returned to users must pass
  through `sanitize_error()` to strip filesystem paths.
- [ ] **Subprocess injection**: never construct subprocess commands from
  user input using string interpolation. Use list arguments to
  `subprocess.run()`.
- [ ] **Credential handling**: Galaxy credentials must pass through
  `_sanitize_credential()` to strip control characters.
- [ ] **Response size**: large responses must pass through
  `truncate_response()` to prevent memory exhaustion.

## Reporting

For each finding, report:

1. **Violation ID**: reference from `service-contracts.md` if it matches
   an existing violation, or assign a new one.
2. **Severity**: Error (must fix before merge), Warning (should fix or
   file follow-up issue), Info (note for future improvement).
3. **File and line**: exact location of the violation.
4. **Rule**: which contract, layer rule, or PEP 8 section is violated.
5. **Recommendation**: what to change.

### Severity Guide

| Severity | Criteria | Action |
|----------|----------|--------|
| Error | Thread safety bug, layer skip introducing new coupling, security vulnerability, bare `except:`, `None` equality comparison | Block merge |
| Warning | Missing types at boundary, private function access across modules, missing validation, missing `__all__`, new untyped state | Fix or file issue before merge |
| Info | Loose typing (`dict[str, Any]` where TypedDict exists), minor naming inconsistencies, missing type annotations on internal functions | Note in review, fix opportunistically |
