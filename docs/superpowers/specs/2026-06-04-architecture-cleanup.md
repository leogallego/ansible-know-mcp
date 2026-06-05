# Architecture Cleanup — Exception Hierarchy, Validation Extract, DRY Sanitize

> GitHub issue: #15

## Goal

Internal refactoring to improve maintainability and correctness without changing behavior. Clean up exception handling, extract validation into a dedicated module, eliminate duplicated code, and consolidate validation to a single boundary.

## Current State

**Exceptions** — four independent exception classes across three modules:
- `ValidationError` in `server.py`
- `AnsibleDocError` in `parser.py`
- `GalaxyError` in `galaxy.py`
- `CollectionInstallError` in `collections.py`

The Galaxy fallback in `_resolve_module_doc()` catches bare `Exception` and uses string-matching (`_is_missing_collection_error`) to decide whether to attempt Galaxy lookup.

**Validation** — seven `_validate_*` functions plus regex constants and length limits all live in `server.py` (lines 28–121). This is ~95 lines of validation mixed into the server module.

**`_sanitize_error`** — identical function exists in `server.py:123` and `collections.py:35`, both using the same `_PATH_RE` pattern.

**FQCN validation** — `server.py` validates full FQCNs with `_FQCN_RE` at the tool boundary. `galaxy.py` validates individual namespace/name components with `_validate_component()` internally. Both do validation, but at different granularities.

## Design

### 1. New file: `src/ansible_know/errors.py`

```python
class AnsibleKnowError(Exception):
    """Base exception for all ansible-know errors."""

class AnsibleDocError(AnsibleKnowError):
    """ansible-doc CLI failures."""

class CollectionNotFoundError(AnsibleDocError):
    """Module/collection not found — triggers Galaxy fallback."""

class GalaxyError(AnsibleKnowError):
    """Galaxy API failures."""

class CollectionInstallError(AnsibleKnowError):
    """ansible-galaxy install failures."""

class ValidationError(AnsibleKnowError):
    """Input validation failures."""
```

- `parser.py` raises `CollectionNotFoundError` instead of `AnsibleDocError` when the error message matches missing-collection patterns.
- `_resolve_module_doc()` catches `CollectionNotFoundError` explicitly instead of string-matching.
- All other code catches `AnsibleKnowError` or specific subtypes — no bare `Exception`.

### 2. New file: `src/ansible_know/validation.py`

Move from `server.py`:
- All `_validate_*` functions → public `validate_*` functions
- All regex constants (`_FQCN_RE`, `_NAMESPACE_RE`, `_VERSION_RE`, `_TAGS_RE`, `_SENSITIVE_PREFIXES`)
- All length constants (`MAX_KEYWORD_LENGTH`, `MAX_QUERY_LENGTH`, etc.)
- `_sanitize_error()` → `sanitize_error()` (shared utility, DRYs item 4)
- `_truncate_response()` → `truncate_response()`

`server.py` and `collections.py` import from `validation.py`.

### 3. Consolidate FQCN validation

- Remove `_validate_component()` from `galaxy.py`.
- Galaxy methods trust their inputs (they're only called from `server.py` tools which already validate).
- `_parse_fqcn()` stays in `galaxy.py` (it's format parsing, not security validation).

### 4. Updated `_resolve_module_doc` fallback

```python
async def _resolve_module_doc(module_name: str) -> tuple[dict, dict | None]:
    try:
        raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
        return raw_doc, None
    except CollectionNotFoundError:
        # Explicit type — no string matching needed
        logger.info("Collection not installed, trying Galaxy for %s", module_name)
        try:
            client = GalaxyClient()
            galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            raise CollectionNotFoundError(...) from galaxy_exc
```

## Non-goals

- No new features
- No API changes (tool signatures, return shapes unchanged)
- No test behavior changes (tests may need import path updates)
