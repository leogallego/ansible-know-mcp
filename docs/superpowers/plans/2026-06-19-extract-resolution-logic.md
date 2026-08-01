# Extract Resolution Logic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move ~140 lines of Galaxy fallback resolution logic from `server.py` (Orchestration) into a new `resolution.py` (Domain), fixing architecture violations V-E1, V-L1, V-D7, V-L2.

**Architecture:** Create `src/ansible_know/resolution.py` as a Domain-layer module with 4 public functions and 2 internal helpers. Update `server.py` tool handlers to delegate to it. Migrate and expand tests. Update architecture docs.

**Tech Stack:** Python 3.10+, FastMCP, pytest, asyncio, httpx

**Spec:** `docs/superpowers/specs/2026-06-19-extract-resolution-logic-design.md`

## Global Constraints

- Pure structural refactor -- no behavioral changes to fallback logic, error handling, or caching.
- All lazy imports preserved (parser, galaxy, galaxy_config) to avoid loading ansible-core at startup.
- `_missing_collections` stays as module-level mutable global (state encapsulation deferred to #68).
- Tests must remain mockable without ansible-core -- mock `_run_ansible_doc`, not real `ansible-doc`.
- Baseline: 114 tests pass in `tests/test_server.py` before any changes.

## Skills to Load

Before each task, load the listed skills. There are two types:

**Invocable skills** (use the `Skill` tool):
- `pep8-imports`, `pep8-naming`, `pep8-type-annotations`, `pep8-programming`, `pep8-review`
- `superpowers:test-driven-development`, `superpowers:verification-before-completion`

**File-based skills** (use the `Read` tool on the SKILL.md path):
- `skills/python-contract-docstrings/SKILL.md` — function contract docstrings
- `skills/python-try-except/SKILL.md` — try/except scope audit
- `skills/python-tighten-types/SKILL.md` — tighten type annotations
- `skills/python-concept-analysis/SKILL.md` — naming consistency
- `skills/pr-architecture-review/SKILL.md` — architecture review checklist
- `skills/mcp-builder/reference/python_mcp_server.md` — FastMCP patterns

### Per-task skill loading

- **Task 1 (resolution.py):**
  - Read: `skills/python-contract-docstrings/SKILL.md`, `skills/python-try-except/SKILL.md`, `skills/python-tighten-types/SKILL.md`
  - Invoke: `pep8-imports`, `pep8-naming`, `pep8-type-annotations`
- **Task 2 (tests):**
  - No skills needed (tests follow existing patterns in `tests/test_server.py`)
- **Task 3 (server.py):**
  - Read: `skills/pr-architecture-review/SKILL.md`
  - Invoke: `pep8-imports`
- **Task 4 (docs):** none
- **Task 5 (verify):**
  - Read: `skills/pr-architecture-review/SKILL.md`
  - Invoke: `pep8-review` (full PEP 8 audit on `src/ansible_know/resolution.py`), `superpowers:verification-before-completion`

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/ansible_know/resolution.py` | Domain-layer doc resolution + Galaxy multi-server search |
| Create | `tests/test_resolution.py` | Unit tests for resolution module |
| Modify | `src/ansible_know/server.py` | Remove extracted code, delegate to resolution |
| Modify | `tests/test_server.py` | Update patches/fixture, remove migrated test classes |
| Modify | `docs/architecture/service-contracts.md` | Mark violations fixed, add resolution.py to tables |
| Modify | `docs/architecture/adr/0004-galaxy-fallback-chain.md` | Update consequence note |
| Modify | `CLAUDE.md` | Add resolution.py to architecture table |

---

### Task 1: Create `resolution.py` with extracted logic

**Files:**
- Create: `src/ansible_know/resolution.py`

**Interfaces:**
- Consumes: `parser.get_module_doc(name) -> dict`, `parser.get_role_doc(name) -> dict`, `parser.extract_role_metadata(doc) -> dict`, `GalaxyClient.from_config(server, http_client=) -> GalaxyClient`, `GalaxyClient.fetch_module_doc(name) -> tuple[dict, dict]`, `GalaxyClient.fetch_role_doc(name) -> tuple[dict, dict]`, `GalaxyClient.search_collections(query, tags=) -> dict`, `load_galaxy_servers() -> list[GalaxyServerConfig]`
- Produces: `resolve_module_doc(name, http_client, galaxy_servers) -> tuple[dict, DocProvenance | None]`, `resolve_role_doc(name, http_client, galaxy_servers) -> dict`, `search_galaxy_collections(query, tags, http_client, galaxy_servers) -> dict`, `clear_missing_namespace(namespace) -> None`

- [ ] **Step 1: Create `src/ansible_know/resolution.py` with full implementation**

```python
"""Document resolution with local-then-Galaxy fallback.

Owns the resolution strategy for module and role documentation:
local ansible-doc first, then Galaxy docs-blob API, then graceful
degradation. Also provides multi-server Galaxy collection search.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from ansible_know.galaxy_config import GalaxyServerConfig
    from ansible_know.types import DocProvenance

from ansible_know.errors import AnsibleDocError
from ansible_know.validation import sanitize_error

logger = logging.getLogger("ansible_know")

__all__ = [
    "resolve_module_doc",
    "resolve_role_doc",
    "search_galaxy_collections",
    "clear_missing_namespace",
]

# Negative cache: namespaces that failed local ansible-doc lookup.
# Skips retrying local resolution for known-missing collections,
# going straight to Galaxy fallback. Cleared per-namespace on
# successful ensure_collection().
#
# Thread safety: only mutated from the asyncio event loop thread
# (add/discard in coroutines); executor callbacks (parser calls)
# never touch it. Under CPython, set.add/discard/`in` are also
# GIL-atomic, so even an accidental cross-thread read is safe.
_missing_collections: set[str] = set()


def _run_in_executor(func, *args, **kwargs):
    """Run a blocking function in the default executor."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


def _select_http_client(
    http_client: httpx.AsyncClient | None,
    server: GalaxyServerConfig,
) -> httpx.AsyncClient | None:
    """Use shared client only when server validates certs."""
    return http_client if server.validate_certs else None


async def _try_galaxy_servers(
    servers: list[GalaxyServerConfig],
    operation: Callable[..., Awaitable[Any]],
    http_client: httpx.AsyncClient | None = None,
) -> Any:
    """Try an operation across multiple Galaxy servers in priority order.

    Returns the first successful result. Raises the last GalaxyError if all fail.
    """
    from ansible_know.errors import GalaxyError
    from ansible_know.galaxy import GalaxyClient

    last_exc: GalaxyError | None = None
    for server in servers:
        try:
            async with GalaxyClient.from_config(
                server, http_client=_select_http_client(http_client, server),
            ) as client:
                return await operation(client)
        except GalaxyError as exc:
            logger.info("Galaxy server '%s' failed: %s", server.name, exc)
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise GalaxyError("No Galaxy servers configured")


def _get_servers(
    galaxy_servers: list[GalaxyServerConfig] | None,
) -> list[GalaxyServerConfig]:
    """Return explicit servers or fall back to ansible.cfg discovery."""
    if galaxy_servers is not None:
        return galaxy_servers
    from ansible_know.galaxy_config import load_galaxy_servers
    return load_galaxy_servers()


def clear_missing_namespace(namespace: str) -> None:
    """Remove a namespace from the negative cache."""
    _missing_collections.discard(namespace)


async def resolve_module_doc(
    module_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
) -> tuple[dict[str, Any], DocProvenance | None]:
    """Try local ansible-doc, fall back to Galaxy if the collection is missing.

    Returns (raw_doc, galaxy_meta_or_none). Raises on non-missing-collection
    errors and when both local and Galaxy lookups fail.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    namespace = ".".join(module_name.split(".")[:2]) if "." in module_name else None

    async def _fetch_from_galaxy(client):
        return await client.fetch_module_doc(module_name)

    if namespace and namespace in _missing_collections:
        try:
            galaxy_doc, galaxy_meta = await _try_galaxy_servers(
                servers, _fetch_from_galaxy, http_client,
            )
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            raise CollectionNotFoundError(
                f"Collection '{namespace}' not installed locally"
            ) from galaxy_exc

    try:
        raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
        return raw_doc, None
    except CollectionNotFoundError as local_exc:
        if namespace:
            _missing_collections.add(namespace)
        logger.info("Collection not installed, trying Galaxy: %s", local_exc)
        try:
            galaxy_doc, galaxy_meta = await _try_galaxy_servers(
                servers, _fetch_from_galaxy, http_client,
            )
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
            raise local_exc from galaxy_exc


async def resolve_role_doc(
    role_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
) -> dict[str, Any]:
    """Try local ansible-doc -t role, fall back to Galaxy readme_html.

    Returns the complete tool response dict including doc_source and content_type.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    namespace = ".".join(role_name.split(".")[:2]) if "." in role_name else None

    local_doc: dict[str, Any] = {}

    if not (namespace and namespace in _missing_collections):
        try:
            local_doc = await _run_in_executor(parser.get_role_doc, role_name)
        except CollectionNotFoundError:
            if namespace:
                _missing_collections.add(namespace)
            local_doc = {}
        except AnsibleDocError as exc:
            logger.warning("Local role doc failed for %s: %s", role_name, exc)
            local_doc = {}

    if local_doc:
        metadata = parser.extract_role_metadata(local_doc)
        metadata["content_type"] = "role"
        metadata["doc_source"] = "local"
        return metadata

    try:
        async def _fetch(client):
            return await client.fetch_role_doc(role_name)
        galaxy_role_meta, galaxy_meta = await _try_galaxy_servers(
            servers, _fetch, http_client,
        )

        result = dict(galaxy_role_meta)
        result["content_type"] = "role"
        result["doc_source"] = "galaxy_readme"
        result["doc_version"] = galaxy_meta.get("doc_version", "")
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as galaxy_exc:
        return {
            "role_name": role_name,
            "content_type": "role",
            "doc_source": "unavailable",
            "error": sanitize_error(str(galaxy_exc)),
            "entry_points": {},
        }


async def search_galaxy_collections(
    query: str,
    tags: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
) -> dict[str, Any]:
    """Search all configured Galaxy servers concurrently, merge and dedupe results."""
    from ansible_know.galaxy import GalaxyClient

    servers = _get_servers(galaxy_servers)

    async def _query_server(server):
        async with GalaxyClient.from_config(
            server, http_client=_select_http_client(http_client, server),
        ) as client:
            result = await client.search_collections(query, tags=tags)
        return server.name, result

    tasks = [_query_server(s) for s in servers]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    all_collections: list[dict[str, Any]] = []
    seen_namespaces: set[str] = set()
    errors: list[str] = []

    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, Exception):
            logger.info(
                "search_collections on '%s' failed: %s",
                servers[i].name, outcome,
            )
            errors.append(f"{servers[i].name}: {outcome}")
            continue
        server_name, result = outcome
        for coll in result.get("collections", []):
            ns = coll.get("namespace", "")
            if ns not in seen_namespaces:
                coll["source"] = server_name
                all_collections.append(coll)
                seen_namespaces.add(ns)

    if not all_collections and errors:
        from ansible_know.errors import GalaxyError
        raise GalaxyError(f"All Galaxy servers failed: {'; '.join(errors)}")

    all_collections.sort(key=lambda c: c.get("download_count", 0), reverse=True)

    return {
        "query": query,
        "count": len(all_collections),
        "collections": all_collections,
    }
```

Note: `search_galaxy_collections` raises `GalaxyError` on total failure instead of returning `{"error": ...}` -- the tool handler in server.py catches it and converts to the error dict. This keeps domain logic free of MCP response formatting.

- [ ] **Step 2: Verify the file compiles**

Run: `python -c "import ast; ast.parse(open('src/ansible_know/resolution.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/ansible_know/resolution.py
git commit -m "refactor: create resolution.py domain module (#66)

Extract Galaxy fallback resolution logic into a dedicated Domain-layer
module. This commit adds the module; server.py is updated in a later
commit.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Create `tests/test_resolution.py`

**Files:**
- Create: `tests/test_resolution.py`

**Interfaces:**
- Consumes: `resolution.resolve_module_doc`, `resolution.resolve_role_doc`, `resolution.search_galaxy_collections`, `resolution.clear_missing_namespace`, `resolution._missing_collections`
- Produces: test coverage for the resolution module

- [ ] **Step 1: Create `tests/test_resolution.py` with migrated + new tests**

```python
"""Tests for ansible_know.resolution module."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import SAMPLE_MODULE_DOC, SAMPLE_ROLE_DOC


@pytest.fixture(autouse=True)
def reset_negative_cache():
    """Clear the negative cache before each test."""
    from ansible_know import resolution
    resolution._missing_collections.clear()
    yield
    resolution._missing_collections.clear()


@pytest.fixture
def mock_ansible_doc():
    with patch("ansible_know.parser._run_ansible_doc") as mock:
        yield mock


class TestResolveModuleDoc:
    """Tests migrated from test_server.py::TestResolveModuleDoc + new cases."""

    @pytest.mark.asyncio
    async def test_local_success_no_galaxy(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_MODULE_DOC)
        from ansible_know.resolution import resolve_module_doc
        raw_doc, galaxy_meta = await resolve_module_doc("ansible.builtin.package")
        assert "ansible.builtin.package" in raw_doc
        assert galaxy_meta is None

    @pytest.mark.asyncio
    async def test_non_missing_collection_error_not_retried(self, mock_ansible_doc):
        from ansible_know.errors import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=AssertionError("Galaxy should not be called"),
        ):
            from ansible_know.resolution import resolve_module_doc
            with pytest.raises(AnsibleDocError, match="timed out"):
                await resolve_module_doc("ansible.builtin.copy")

    @pytest.mark.asyncio
    async def test_galaxy_fallback_on_missing_collection(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Create, update or delete devices",
                    "description": ["Manages devices."],
                    "options": {"data": {"type": "dict", "required": True}},
                    "author": [], "notes": [], "version_added": "0.1.0",
                },
                "examples": "", "return": [], "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            from ansible_know.resolution import resolve_module_doc
            raw_doc, meta = await resolve_module_doc("netbox.netbox.netbox_device")

        assert raw_doc == galaxy_doc
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_both_fail_raises_local_error(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError, GalaxyError
        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "ansible-doc failed (exit 1): some.col.mod was not found"
        )

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("Module 'mod' not found in docs-blob"),
        ):
            from ansible_know.resolution import resolve_module_doc
            with pytest.raises(CollectionNotFoundError, match="was not found"):
                await resolve_module_doc("some.col.mod")


class TestResolveRoleDoc:

    @pytest.mark.asyncio
    async def test_local_success(self, mock_ansible_doc):
        mock_ansible_doc.return_value = json.dumps(SAMPLE_ROLE_DOC)
        from ansible_know.resolution import resolve_role_doc
        result = await resolve_role_doc("fedora.linux_system_roles.gfs2")
        assert result["content_type"] == "role"
        assert result["doc_source"] == "local"

    @pytest.mark.asyncio
    async def test_galaxy_fallback_on_empty_doc(self, mock_ansible_doc):
        mock_ansible_doc.return_value = "{}"
        galaxy_role_meta = {
            "role_name": "fedora.linux_system_roles.timesync",
            "short_description": "Configure time synchronization",
            "entry_points": {"main": {"description": "Configure time sync", "options": []}},
            "dependencies": [], "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.121.0", "doc_warning": "parsed from README"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            from ansible_know.resolution import resolve_role_doc
            result = await resolve_role_doc("fedora.linux_system_roles.timesync")

        assert result["doc_source"] == "galaxy_readme"
        assert result["content_type"] == "role"

    @pytest.mark.asyncio
    async def test_graceful_degradation(self, mock_ansible_doc):
        mock_ansible_doc.return_value = "{}"
        from ansible_know.errors import GalaxyError

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            side_effect=GalaxyError("not found"),
        ):
            from ansible_know.resolution import resolve_role_doc
            result = await resolve_role_doc("some.col.missing_role")

        assert result["doc_source"] == "unavailable"
        assert "error" in result


class TestNegativeCache:
    """Tests migrated from test_server.py::TestNegativeCache."""

    @pytest.mark.asyncio
    async def test_skips_local_on_cache_hit(self, mock_ansible_doc):
        from ansible_know import resolution
        resolution._missing_collections.add("netbox.netbox")

        galaxy_doc = {
            "netbox.netbox.netbox_device": {
                "doc": {
                    "short_description": "Manage devices",
                    "description": [], "options": {},
                    "author": [], "notes": [], "version_added": "0.1.0",
                },
                "examples": "", "return": [], "metadata": {},
            }
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            return_value=(galaxy_doc, galaxy_meta),
        ):
            raw_doc, meta = await resolution.resolve_module_doc("netbox.netbox.netbox_device")

        mock_ansible_doc.assert_not_called()
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_populates_cache_on_collection_not_found(self, mock_ansible_doc):
        from ansible_know import resolution
        from ansible_know.errors import CollectionNotFoundError, GalaxyError

        mock_ansible_doc.side_effect = CollectionNotFoundError("netbox.netbox has no attribute")

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found"),
        ):
            with pytest.raises(CollectionNotFoundError):
                await resolution.resolve_module_doc("netbox.netbox.netbox_device")

        assert "netbox.netbox" in resolution._missing_collections

    @pytest.mark.asyncio
    async def test_does_not_cache_non_collection_errors(self, mock_ansible_doc):
        from ansible_know import resolution
        from ansible_know.errors import AnsibleDocError

        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        with pytest.raises(AnsibleDocError):
            await resolution.resolve_module_doc("ansible.builtin.copy")

        assert "ansible.builtin" not in resolution._missing_collections

    def test_clear_missing_namespace(self):
        from ansible_know import resolution
        resolution._missing_collections.add("netbox.netbox")
        resolution.clear_missing_namespace("netbox.netbox")
        assert "netbox.netbox" not in resolution._missing_collections

    @pytest.mark.asyncio
    async def test_role_skips_local_on_cache_hit(self, mock_ansible_doc):
        from ansible_know import resolution
        resolution._missing_collections.add("some.col")

        galaxy_role_meta = {
            "role_name": "some.col.role",
            "short_description": "A role",
            "entry_points": {"main": {"description": "", "options": []}},
            "dependencies": [], "examples": "",
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.0.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_role_doc",
            return_value=(galaxy_role_meta, galaxy_meta),
        ):
            result = await resolution.resolve_role_doc("some.col.role")

        mock_ansible_doc.assert_not_called()
        assert result["doc_source"] == "galaxy_readme"


class TestSearchGalaxyCollections:

    @pytest.mark.asyncio
    async def test_merges_results_from_servers(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_galaxy_collections

        server1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        server2 = GalaxyServerConfig(name="hub", url="https://hub.internal.com")

        result1 = {"query": "net", "count": 1, "collections": [
            {"namespace": "netbox.netbox", "download_count": 100},
        ]}
        result2 = {"query": "net", "count": 1, "collections": [
            {"namespace": "cisco.nxos", "download_count": 200},
        ]}

        with patch("ansible_know.galaxy.GalaxyClient.search_collections") as mock_search:
            call_count = 0
            async def side_effect(query, tags=None):
                nonlocal call_count
                call_count += 1
                return result1 if call_count == 1 else result2
            mock_search.side_effect = side_effect
            result = await search_galaxy_collections(
                "net", galaxy_servers=[server1, server2],
            )

        assert result["count"] == 2
        assert result["collections"][0]["namespace"] == "cisco.nxos"
        assert result["collections"][1]["namespace"] == "netbox.netbox"

    @pytest.mark.asyncio
    async def test_deduplicates_by_namespace(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_galaxy_collections

        server1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        server2 = GalaxyServerConfig(name="hub", url="https://hub.internal.com")

        dup_result = {"query": "net", "count": 1, "collections": [
            {"namespace": "netbox.netbox", "download_count": 100},
        ]}

        with patch("ansible_know.galaxy.GalaxyClient.search_collections", return_value=dup_result):
            result = await search_galaxy_collections(
                "net", galaxy_servers=[server1, server2],
            )

        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_partial_server_failure(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.errors import GalaxyError
        from ansible_know.resolution import search_galaxy_collections

        server1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        server2 = GalaxyServerConfig(name="hub", url="https://hub.internal.com")

        good_result = {"query": "net", "count": 1, "collections": [
            {"namespace": "netbox.netbox", "download_count": 100},
        ]}

        with patch("ansible_know.galaxy.GalaxyClient.search_collections") as mock_search:
            call_count = 0
            async def side_effect(query, tags=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise GalaxyError("timeout")
                return good_result
            mock_search.side_effect = side_effect
            result = await search_galaxy_collections(
                "net", galaxy_servers=[server1, server2],
            )

        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_all_servers_fail_raises(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.errors import GalaxyError
        from ansible_know.resolution import search_galaxy_collections

        server = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")

        with patch("ansible_know.galaxy.GalaxyClient.search_collections",
                   side_effect=GalaxyError("timeout")):
            with pytest.raises(GalaxyError, match="All Galaxy servers failed"):
                await search_galaxy_collections("net", galaxy_servers=[server])
```

- [ ] **Step 2: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolution.py -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_resolution.py
git commit -m "test: add resolution module tests (#66)

Unit tests for resolve_module_doc, resolve_role_doc,
search_galaxy_collections, and negative cache behavior. Includes
migrated tests from test_server.py and new coverage.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Update `server.py` and `tests/test_server.py`

**Files:**
- Modify: `src/ansible_know/server.py:142-311` (remove extracted code), `server.py:453-531` (simplify search_collections), `server.py:646` (clear_missing_namespace)
- Modify: `tests/test_server.py:17-23` (fixture), `tests/test_server.py:544-572` (remove TestResolveModuleDoc), `tests/test_server.py:961-976` (update patch), `tests/test_server.py:1061-1218` (remove TestNegativeCache, update TestGetRoleDocTool)

**Interfaces:**
- Consumes: `resolution.resolve_module_doc`, `resolution.resolve_role_doc`, `resolution.search_galaxy_collections`, `resolution.clear_missing_namespace`
- Produces: updated tool handlers that delegate to resolution module

- [ ] **Step 1: Remove extracted code from `server.py`**

Remove these sections from `server.py`:

1. Remove `_select_http_client()` (lines 142-147)
2. Remove `_missing_collections` set and its comment block (lines 165-174)
3. Remove `_try_galaxy_servers()` (lines 177-201)
4. Remove `_resolve_module_doc()` (lines 204-249)
5. Remove `_resolve_role_doc()` (lines 252-311)

- [ ] **Step 2: Update `get_module_doc` tool handler**

Replace the try block body in `get_module_doc` (the `from ansible_know import parser` block through the return) with:

```python
    try:
        from ansible_know import parser, resolution

        http_client, galaxy_servers = _get_lifespan_resources(ctx)
        raw_doc, galaxy_meta = await resolution.resolve_module_doc(
            module_name, http_client=http_client, galaxy_servers=galaxy_servers,
        )
        metadata = parser.extract_module_metadata(raw_doc)
        if galaxy_meta:
            metadata.update(galaxy_meta)
        else:
            metadata["doc_source"] = "local"
        return metadata
    except Exception as exc:
        logger.warning("get_module_doc failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        from ansible_know.errors import GalaxyError
        if isinstance(exc.__cause__, GalaxyError):
            return {"error": sanitize_error(str(exc))}
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}
```

- [ ] **Step 3: Update `get_role_doc` tool handler**

Replace the try block body with:

```python
    try:
        from ansible_know import resolution

        http_client, galaxy_servers = _get_lifespan_resources(ctx)
        return await resolution.resolve_role_doc(
            role_name, http_client=http_client, galaxy_servers=galaxy_servers,
        )
    except Exception as exc:
        logger.warning("get_role_doc failed: %s", exc)
        ns = ".".join(role_name.split(".")[:2]) if "." in role_name else None
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}
```

- [ ] **Step 4: Update `generate_skill` tool handler**

Replace the resolution call with:

```python
        from ansible_know import parser, resolution, skills

        # ... (progress reporting stays)
        http_client, galaxy_servers = _get_lifespan_resources(ctx)
        raw_doc, galaxy_meta = await resolution.resolve_module_doc(
            module_name, http_client=http_client, galaxy_servers=galaxy_servers,
        )
```

- [ ] **Step 5: Update `generate_role_skill` tool handler**

Replace the resolution call with:

```python
        from ansible_know import resolution, skills

        # ... (progress reporting stays)
        http_client, galaxy_servers = _get_lifespan_resources(ctx)
        metadata = await resolution.resolve_role_doc(
            role_name, http_client=http_client, galaxy_servers=galaxy_servers,
        )
```

- [ ] **Step 6: Replace `search_collections` tool handler body**

Replace the entire try block (lines 482-531) with:

```python
    try:
        from ansible_know import resolution

        http_client, galaxy_servers = _get_lifespan_resources(ctx)
        return await resolution.search_galaxy_collections(
            query, tags=tags, http_client=http_client, galaxy_servers=galaxy_servers,
        )
    except Exception as exc:
        logger.warning("search_collections failed: %s", exc)
        return {"error": sanitize_error(str(exc))}
```

- [ ] **Step 7: Update `ensure_collection` tool handler**

Replace `_missing_collections.discard(collection_namespace)` (line 646) with:

```python
        from ansible_know import resolution
        resolution.clear_missing_namespace(collection_namespace)
```

Note: the `from ansible_know import resolution` can be at the top of the try block alongside the existing `from ansible_know import collections`.

- [ ] **Step 8: Clean up unused imports in `server.py`**

Remove from the top-level imports:
- `from collections.abc import Awaitable, Callable` (no longer needed -- `_try_galaxy_servers` moved)
- `from functools import partial` (no longer needed -- `_run_in_executor` moved)

The `sanitize_error` import in validation stays (still used in `search_collections` error handler).

Also remove the `TYPE_CHECKING` imports that are no longer needed:
- `from ansible_know.galaxy_config import GalaxyServerConfig` (no longer used in server.py signatures)
- `from ansible_know.types import DocProvenance` (no longer used in server.py signatures)

Keep the `_run_in_executor` function in server.py -- it's still used by `get_collection_manifest`, `generate_collection_skills`, and other tool handlers that call parser/skills directly.

Wait -- `_run_in_executor` is still used in server.py (e.g., `get_collection_manifest` line 560-561). Keep it. But remove `from functools import partial` only if `_run_in_executor` is moved. Since `_run_in_executor` stays, keep `partial` too.

Correction: `_run_in_executor` stays in server.py. `partial` stays. Only remove `Awaitable, Callable` from `collections.abc` import, and the TYPE_CHECKING imports for `GalaxyServerConfig` and `DocProvenance`.

- [ ] **Step 9: Update `tests/test_server.py`**

9a. Update the `reset_negative_cache_global` fixture (lines 17-23):

```python
@pytest.fixture(autouse=True)
def reset_negative_cache_global():
    """Clear the negative cache before each test to prevent test pollution."""
    from ansible_know import resolution
    resolution._missing_collections.clear()
    yield
    resolution._missing_collections.clear()
```

9b. Remove `TestResolveModuleDoc` class (lines 544-572) -- migrated to test_resolution.py.

9c. Remove `TestNegativeCache` class (lines 1061-1125) -- migrated to test_resolution.py.

9d. Update `TestLifespanHttpClient.test_get_module_doc_passes_lifespan_http_client` (line 969): change patch target from `"ansible_know.server._resolve_module_doc"` to `"ansible_know.resolution.resolve_module_doc"`.

9e. Update `TestGetRoleDocTool.test_cached_missing_collection_skips_local` (line 1194-1217): change `server._missing_collections` references to `resolution._missing_collections`:

```python
    @pytest.mark.asyncio
    async def test_cached_missing_collection_skips_local(self, mock_ansible_doc):
        from ansible_know import resolution
        resolution._missing_collections.add("some.col")
        # ... rest stays the same, but change the final assert:
        assert "ansible.builtin" not in resolution._missing_collections
```

- [ ] **Step 10: Run all tests**

Run: `.venv/bin/pytest tests/test_server.py tests/test_resolution.py -v`
Expected: all tests PASS. The count in test_server.py will be lower (migrated tests removed), test_resolution.py adds its own.

- [ ] **Step 11: Run the full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 12: Run linter**

Run: `.venv/bin/ruff check src/ansible_know/resolution.py src/ansible_know/server.py tests/test_resolution.py tests/test_server.py`
Expected: no errors

- [ ] **Step 13: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py tests/test_resolution.py
git commit -m "refactor: delegate server.py resolution to domain module (#66)

Remove _resolve_module_doc, _resolve_role_doc, _try_galaxy_servers,
_select_http_client, and _missing_collections from server.py. Tool
handlers now delegate to resolution.resolve_module_doc(),
resolution.resolve_role_doc(), resolution.search_galaxy_collections(),
and resolution.clear_missing_namespace().

Fixes: V-E1 (server.py calling GalaxyClient directly)
Fixes: V-L1 (Orchestration importing External Access)
Fixes: V-D7 (business logic in Orchestration)
Fixes: V-L2 (domain logic in Orchestration)

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Update architecture documentation

**Files:**
- Modify: `docs/architecture/service-contracts.md`
- Modify: `docs/architecture/adr/0004-galaxy-fallback-chain.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: none (documentation only)
- Produces: updated architecture docs reflecting the refactor

- [ ] **Step 1: Update `docs/architecture/service-contracts.md`**

1a. Add `resolution.py` to the Layer Architecture diagram (line 23):

```
│  Domain            parser.py, skills.py,                │
│                    collection_manifest.py, docs.py,      │
│                    resolution.py                         │
```

1b. Add `resolution` to the Orchestration -> Domain interface table (after line 104):

```markdown
| `resolution` | `resolve_module_doc()`, `resolve_role_doc()`, `search_galaxy_collections()`, `clear_missing_namespace()` | `resolution.py` |
```

1c. Mark V-E1 as fixed (line 192):

```markdown
| ~~V-E1~~ | ~~Error~~ | ~~`server.py` calls `GalaxyClient` directly in `search_collections()` and `_try_galaxy_servers()`, bypassing the Domain layer entirely. Galaxy access should be mediated through a domain-level service.~~ **Fixed in PR #66** — Galaxy access is now mediated through `resolution.py`. | ~~`server.py:168-192`, `server.py:480-486`~~ |
```

1d. Mark V-D7 as fixed (line 141):

```markdown
| ~~V-D7~~ | ~~Warning~~ | ~~`_resolve_module_doc()` and `_resolve_role_doc()` in `server.py` contain significant business logic (Galaxy fallback, missing-collection caching). This logic belongs in the Domain layer, not Orchestration.~~ **Fixed in PR #66** — Resolution logic moved to `resolution.py`. | ~~`server.py:195-301`~~ |
```

1e. Mark V-L1 as fixed (line 321):

```markdown
| ~~V-L1~~ | ~~Error~~ | ~~Orchestration (`server.py`) imports and calls External Access (`galaxy.py:GalaxyClient`) directly, bypassing the Domain layer.~~ **Fixed in PR #66** — Orchestration delegates to `resolution.py` (Domain). |
```

1f. Mark V-L2 as fixed (line 322):

```markdown
| ~~V-L2~~ | ~~Warning~~ | ~~Orchestration (`server.py`) contains domain logic in `_resolve_module_doc()` and `_resolve_role_doc()` — these are domain-level resolution strategies, not orchestration.~~ **Fixed in PR #66** — Moved to `resolution.py`. |
```

1g. Update Priority 1 remediation section (line 334):

```markdown
2. ~~**V-E1 / V-L1**: Extract Galaxy client orchestration from `server.py` into
   a domain-level service.~~ **Fixed in PR #66** — `resolution.py` now owns
   all Galaxy fallback and multi-server search logic.
```

1h. Update Priority 2 items 7 (line 346):

```markdown
7. ~~**V-D7 / V-L2**: Move `_resolve_module_doc()` and `_resolve_role_doc()` to a
   domain-level resolution module.~~ **Fixed in PR #66**.
```

- [ ] **Step 2: Update `docs/architecture/adr/0004-galaxy-fallback-chain.md`**

Update the "Negative" consequence about Orchestration complexity (lines 64-67):

```markdown
- ~~**Complexity in Orchestration**: the fallback logic (`_resolve_module_doc`,
  `_resolve_role_doc`) is currently in `server.py`, adding ~100 lines of
  business logic to the Orchestration layer. This should be in a Domain
  module (see V-D7, V-L2 in service-contracts.md).~~
  **Fixed (PR #66):** fallback logic now lives in `resolution.py` (Domain layer).
```

- [ ] **Step 3: Update `CLAUDE.md`**

Add `resolution.py` to the Architecture section table:

```
├── resolution.py          # local-then-Galaxy doc resolution + multi-server search
```

Add after the `parser.py` line in the architecture listing.

- [ ] **Step 4: Commit**

```bash
git add docs/architecture/service-contracts.md docs/architecture/adr/0004-galaxy-fallback-chain.md CLAUDE.md
git commit -m "docs: mark architecture violations fixed (#66)

Update service-contracts.md to mark V-E1, V-L1, V-D7, V-L2 as fixed.
Add resolution.py to architecture diagrams and interface tables.
Update ADR 0004 consequence note. Add resolution.py to CLAUDE.md.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Final verification and PR

**Files:**
- All files from Tasks 1-4

**Interfaces:**
- Consumes: all previous task outputs
- Produces: verified, passing codebase ready for PR

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 2: Run linter on all changed files**

Run: `.venv/bin/ruff check src/ansible_know/resolution.py src/ansible_know/server.py tests/test_resolution.py tests/test_server.py`
Expected: no errors

- [ ] **Step 3: Verify no behavioral changes**

Run: `.venv/bin/pytest tests/ -v --tb=short`
Expected: same test behavior -- all pass, no new warnings.

- [ ] **Step 4: Verify architecture compliance**

Load `skills/pr-architecture-review/SKILL.md` and run the checklist against the diff:
- `resolution.py` imports only Foundation + Domain peers (lazy) + External Access (lazy) ✓
- `server.py` no longer imports `GalaxyClient` or `galaxy_config` directly ✓
- No new module-level mutable state introduced ✓
- `__all__` defined in `resolution.py` ✓

- [ ] **Step 5: Create PR**

```bash
git push -u origin HEAD
gh pr create --title "refactor: extract resolution logic from server.py (#66)" \
  --body "$(cat <<'EOF'
## Summary

- Create `resolution.py` domain module with `resolve_module_doc()`, `resolve_role_doc()`, `search_galaxy_collections()`, and `clear_missing_namespace()`
- Remove ~140 lines of business logic from `server.py` (Orchestration) to `resolution.py` (Domain)
- Migrate `TestResolveModuleDoc` and `TestNegativeCache` to `test_resolution.py`, add new coverage
- Mark architecture violations V-E1, V-L1, V-D7, V-L2 as fixed

## Architecture violations fixed

| ID | Severity | Description |
|----|----------|-------------|
| V-E1 | Error | server.py no longer calls GalaxyClient directly |
| V-L1 | Error | Orchestration no longer imports External Access |
| V-D7 | Warning | Resolution logic moved to Domain layer |
| V-L2 | Warning | Business logic removed from Orchestration |

## Test plan

- [ ] `pytest tests/test_resolution.py -v` — new resolution module tests pass
- [ ] `pytest tests/test_server.py -v` — existing server tests pass with updated patches
- [ ] `pytest tests/ -v` — full suite passes
- [ ] `ruff check src/ tests/` — no lint errors
- [ ] Architecture review: `resolution.py` respects layer dependency rules

Closes #66

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```
