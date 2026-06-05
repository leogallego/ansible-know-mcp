# Galaxy Client Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Galaxy API latency through connection pooling, concurrency control, granular timeouts, smarter caching, and fallback optimization.

**Architecture:** Refactor `GalaxyClient` to accept an optional shared `httpx.AsyncClient` created via FastMCP's `@lifespan` decorator in `server.py`. Add concurrency limiting, per-operation timeouts, TTL-based cache expiry, and a negative cache for missing collections.

**Tech Stack:** FastMCP lifespan, httpx.AsyncClient, asyncio.Semaphore, time.monotonic

**Spec:** `docs/superpowers/specs/2026-06-05-galaxy-performance.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ansible_know/galaxy.py` | Modify | Connection pooling refactor, semaphore, timeouts, cache TTL |
| `src/ansible_know/server.py` | Modify | Lifespan, negative cache, pass http_client to GalaxyClient |
| `tests/test_galaxy.py` | Modify | Update mock signatures, add TTL/semaphore/timeout tests |
| `tests/test_server.py` | Modify | Add negative cache tests, update Galaxy fallback tests |

---

### Task 1: Cache TTL + Sizing (galaxy.py)

**Files:**
- Modify: `src/ansible_know/galaxy.py:1-56` (constants, cache helpers)
- Test: `tests/test_galaxy.py`

This is the most isolated change — pure data layer, no API signature changes.

- [ ] **Step 1: Write failing tests for TTL expiry**

Add to `tests/test_galaxy.py`:

```python
import time
from unittest.mock import patch as stdlib_patch

from ansible_know.galaxy import (
    CACHE_TTL_SECONDS,
    _get_version_cache,
    _put_version_cache,
    _get_blob_cache,
    _put_blob_cache,
)


class TestCacheTTL:
    def test_version_cache_returns_none_after_ttl(self):
        _put_version_cache(("ns", "col"), "1.0.0")
        assert _get_version_cache(("ns", "col")) == "1.0.0"
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS + 1
            assert _get_version_cache(("ns", "col")) is None

    def test_blob_cache_returns_none_after_ttl(self):
        _put_blob_cache(("ns", "col", "1.0.0"), {"data": "test"})
        assert _get_blob_cache(("ns", "col", "1.0.0")) == {"data": "test"}
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS + 1
            assert _get_blob_cache(("ns", "col", "1.0.0")) is None

    def test_version_cache_returns_value_before_ttl(self):
        _put_version_cache(("ns", "col"), "2.0.0")
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS - 10
            assert _get_version_cache(("ns", "col")) == "2.0.0"

    def test_blob_cache_returns_value_before_ttl(self):
        _put_blob_cache(("ns", "col", "1.0.0"), {"data": "fresh"})
        with stdlib_patch("ansible_know.galaxy.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + CACHE_TTL_SECONDS - 10
            assert _get_blob_cache(("ns", "col", "1.0.0")) == {"data": "fresh"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestCacheTTL -v`
Expected: FAIL — `CACHE_TTL_SECONDS` not importable, and cache helpers don't check timestamps.

- [ ] **Step 3: Implement cache TTL**

In `src/ansible_know/galaxy.py`:

1. Add `import time` to the imports.
2. Add constant `CACHE_TTL_SECONDS = 3600` after `MAX_BLOB_CACHE_SIZE`.
3. Change `MAX_BLOB_CACHE_SIZE` from `100` to `50`.
4. Change `_put_version_cache` to store `(value, time.monotonic())` as the cache value.
5. Change `_get_version_cache` to unpack `(value, timestamp)`, check `time.monotonic() - timestamp > CACHE_TTL_SECONDS`, and return `None` if expired (also delete the expired entry).
6. Same changes for `_put_blob_cache` and `_get_blob_cache`.

The cache entry type changes from `str` to `tuple[str, float]` for version cache, and from `dict` to `tuple[dict, float]` for blob cache. Update the type annotations on the `OrderedDict` declarations accordingly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: ALL pass, including existing eviction tests and the new TTL tests.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "feat(galaxy): add cache TTL and reduce blob cache size

Cache entries now expire after 1 hour (CACHE_TTL_SECONDS=3600).
MAX_BLOB_CACHE_SIZE reduced from 100 to 50 to limit memory.
Resolves part of #16.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Granular httpx Timeouts (galaxy.py)

**Files:**
- Modify: `src/ansible_know/galaxy.py:81-168` (_api_get, calling methods)
- Test: `tests/test_galaxy.py`

- [ ] **Step 1: Write failing tests for timeout constants**

Add to `tests/test_galaxy.py`:

```python
from ansible_know.galaxy import TIMEOUT_FAST, TIMEOUT_DEFAULT, TIMEOUT_SLOW


class TestTimeoutConstants:
    def test_timeout_fast_values(self):
        assert TIMEOUT_FAST.connect == 10.0
        assert TIMEOUT_FAST.read == 10.0

    def test_timeout_default_values(self):
        assert TIMEOUT_DEFAULT.connect == 10.0
        assert TIMEOUT_DEFAULT.read == 30.0

    def test_timeout_slow_values(self):
        assert TIMEOUT_SLOW.connect == 10.0
        assert TIMEOUT_SLOW.read == 60.0


class TestTimeoutPassthrough:
    @pytest.mark.asyncio
    async def test_latest_version_uses_fast_timeout(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            await client.latest_version("netbox", "netbox")
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["timeout"] == TIMEOUT_FAST

    @pytest.mark.asyncio
    async def test_fetch_docs_blob_uses_slow_timeout(self):
        _put_version_cache(("netbox", "netbox"), "3.23.0")
        mock_client = _mock_client_get(SAMPLE_DOCS_BLOB)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            await client._fetch_docs_blob("netbox", "netbox", "3.23.0")
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["timeout"] == TIMEOUT_SLOW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestTimeoutConstants tests/test_galaxy.py::TestTimeoutPassthrough -v`
Expected: FAIL — constants don't exist, `_api_get` doesn't pass timeout.

- [ ] **Step 3: Implement granular timeouts**

In `src/ansible_know/galaxy.py`:

1. Add three timeout constants after the cache constants:

```python
TIMEOUT_FAST = httpx.Timeout(connect=10.0, read=10.0)
TIMEOUT_DEFAULT = httpx.Timeout(connect=10.0, read=30.0)
TIMEOUT_SLOW = httpx.Timeout(connect=10.0, read=60.0)
```

2. Add `timeout: httpx.Timeout = TIMEOUT_DEFAULT` parameter to `_api_get`. Pass it to the `client.get()` call as `timeout=timeout`. Both the `client is not None` and `async with` branches must pass it.

3. Add `timeout: httpx.Timeout = TIMEOUT_DEFAULT` parameter to `_safe_api_get`. Forward it to `_api_get(..., timeout=timeout)`.

4. In `latest_version`: pass `timeout=TIMEOUT_FAST` to `_safe_api_get`.
5. In `_get_collection_detail`: pass `timeout=TIMEOUT_FAST` to `_safe_api_get`.
6. In `search_collections`: the search call uses `timeout=TIMEOUT_DEFAULT` (the default, no change needed).
7. In `_fetch_docs_blob`: pass `timeout=TIMEOUT_SLOW` to `_safe_api_get`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "feat(galaxy): add granular httpx timeout profiles

TIMEOUT_FAST (10s) for version lookups, TIMEOUT_DEFAULT (30s) for
search, TIMEOUT_SLOW (60s) for docs-blob fetches.
Resolves part of #16.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Connection Pooling via Lifespan (galaxy.py + server.py)

**Files:**
- Modify: `src/ansible_know/galaxy.py:75-237` (GalaxyClient.__init__, _api_get, _safe_api_get, _get_collection_detail, search_collections)
- Modify: `src/ansible_know/server.py:1-100` (add lifespan, update _resolve_module_doc, update tool handlers)
- Test: `tests/test_galaxy.py`
- Test: `tests/test_server.py`

This is the largest task — refactors GalaxyClient to accept a shared httpx client and wires FastMCP lifespan.

- [ ] **Step 1: Write failing tests for GalaxyClient http_client injection**

Add to `tests/test_galaxy.py`:

```python
class TestHttpClientInjection:
    @pytest.mark.asyncio
    async def test_uses_injected_client(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(http_client=mock_client)
        version = await gc.latest_version("netbox", "netbox")
        assert version == "3.23.0"
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_own_client_when_none(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            gc = GalaxyClient()
            version = await gc.latest_version("netbox", "netbox")
        assert version == "3.23.0"

    @pytest.mark.asyncio
    async def test_reuses_owned_client(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client) as mock_ctor:
            gc = GalaxyClient()
            await gc.latest_version("ns1", "col1")
            await gc.latest_version("ns2", "col2")
        assert mock_ctor.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestHttpClientInjection -v`
Expected: FAIL — `GalaxyClient.__init__` doesn't accept `http_client`.

- [ ] **Step 3: Refactor GalaxyClient for connection pooling**

In `src/ansible_know/galaxy.py`:

1. Change `__init__` signature to `(self, base_url=None, http_client=None)`:
   - Store `self._http_client = http_client` (the injected shared client).
   - Add `self._owned_client: httpx.AsyncClient | None = None` (lazily created for standalone use).

2. Add `_get_client` method:

```python
def _get_client(self) -> httpx.AsyncClient:
    if self._http_client is not None:
        return self._http_client
    if self._owned_client is None:
        self._owned_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0),
            verify=True,
        )
    return self._owned_client
```

3. Refactor `_api_get`: remove the `client` parameter. Always use `self._get_client()`. Remove the `if client is not None` / `else async with` branching. The method body becomes:

```python
async def _api_get(self, path, params=None, timeout=TIMEOUT_DEFAULT):
    url = f"{self._base}{path}"
    client = self._get_client()
    resp = await client.get(
        url, params=params, headers={"Accept": "application/json"},
        timeout=timeout,
    )
    # ... rest unchanged (raise_for_status, size checks, json)
```

4. Refactor `_safe_api_get`: remove `client` parameter. Forward to `_api_get(path, params=params, timeout=timeout)`.

5. Refactor `_get_collection_detail`: remove `client` parameter. Call `self._safe_api_get(path, timeout=TIMEOUT_FAST)` (no `client=` arg).

6. Refactor `search_collections`: remove `async with httpx.AsyncClient(...) as shared_client:` context manager. Remove `client=shared_client` from `_safe_api_get` and `_get_collection_detail` calls. The method body de-indents one level.

- [ ] **Step 4: Update existing test mock signatures**

All existing `mock_api_get` functions in `tests/test_galaxy.py` have signature `async def mock_api_get(self_client, path, params=None, client=None)`. Remove the `client=None` parameter from all of them:

- `TestSearchCollections.test_returns_enriched_results`
- `TestSearchCollections.test_filters_deprecated`
- `TestSearchCollections.test_sorts_by_download_count`
- `TestSearchCollections.test_with_tags_filter`
- `TestDetailEnrichmentFailure.test_enrichment_failure_sets_zero_downloads`
- `TestDetailEnrichmentFailure.test_all_enrichment_failures_still_returns_results`
- `TestSearchCollectionsEdgeCases.test_count_data_mismatch`
- `TestSearchCollectionsEdgeCases.test_empty_tags_in_content`

Also update `_mock_search_context`: the `httpx.AsyncClient` patch is no longer needed since `search_collections` no longer creates one. But tests that mock `_api_get` via `patch.object` still work. Remove the `httpx.AsyncClient` patch from `_mock_search_context` — just return the `patch.object` alone. Update all call sites that do `p1, p2 = _mock_search_context(...)` / `with p1, p2:` to use a single context manager.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: ALL pass.

- [ ] **Step 6: Wire FastMCP lifespan in server.py**

In `src/ansible_know/server.py`:

1. Add imports:

```python
import httpx
from fastmcp.server.lifespan import lifespan
```

2. Add lifespan function before the `mcp = FastMCP(...)` declaration:

```python
@lifespan
async def app_lifespan(server):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0),
        verify=True,
    ) as client:
        yield {"http_client": client}
```

3. Register lifespan with FastMCP:

```python
mcp = FastMCP(
    name="Ansible Know",
    instructions=(...),
    lifespan=app_lifespan,
)
```

4. Update `_resolve_module_doc` signature to `(module_name, http_client=None)`. Pass `http_client` to `GalaxyClient(http_client=http_client)`.

5. Update `get_module_doc` tool: add `ctx: Context = None` parameter. Extract http_client:

```python
http_client = ctx.lifespan_context.get("http_client") if ctx else None
raw_doc, galaxy_meta = await _resolve_module_doc(module_name, http_client=http_client)
```

6. Update `search_collections` tool: add `ctx: Context = None` parameter. Extract http_client and pass to `GalaxyClient(http_client=http_client)`.

7. Update `generate_skill` tool: extract http_client from ctx and pass to `_resolve_module_doc(module_name, http_client=http_client)`.

8. Update `generate_collection_skills` tool: no changes needed — it uses `parser.get_module_doc` (local ansible-doc), not `_resolve_module_doc`.

- [ ] **Step 7: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: ALL 182+ tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/ansible_know/galaxy.py src/ansible_know/server.py tests/test_galaxy.py tests/test_server.py
git commit -m "feat: connection pooling via FastMCP lifespan

Share a single httpx.AsyncClient across all tool calls via
FastMCP's @lifespan decorator. GalaxyClient accepts http_client
parameter; falls back to lazy-created owned client for standalone
use and testing.
Resolves part of #16.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Concurrency Semaphore on Enrichment (galaxy.py)

**Files:**
- Modify: `src/ansible_know/galaxy.py:211-225` (search_collections._enrich)
- Test: `tests/test_galaxy.py`

- [ ] **Step 1: Write failing test for semaphore limiting**

Add to `tests/test_galaxy.py`:

```python
class TestEnrichmentSemaphore:
    @pytest.mark.asyncio
    async def test_limits_concurrent_enrichment(self):
        from ansible_know.galaxy import _enrichment_semaphore

        max_concurrent = 0
        current_concurrent = 0

        original_enrich_detail = GalaxyClient._get_collection_detail

        async def tracking_detail(self_client, namespace, name):
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.01)
            current_concurrent -= 1
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}}

        search_data = {
            "meta": {"count": 8}, "links": {},
            "data": [
                {
                    "collection_version": {
                        "namespace": f"ns{i}", "name": "col",
                        "version": "1.0.0", "contents": [], "dependencies": {},
                        "description": f"Col {i}", "tags": [],
                        "pulp_href": "", "requires_ansible": "", "pulp_created": "",
                    },
                    "is_highest": True, "is_deprecated": False, "is_signed": False,
                    "repository": {}, "repository_version": "",
                    "namespace_metadata": {
                        "pulp_href": "", "name": "", "company": "",
                        "description": "", "avatar_url": None,
                    },
                }
                for i in range(8)
            ],
        }

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "search/collection-versions" in path:
                return search_data
            return {"download_count": 100, "highest_version": {"version": "1.0.0"}}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            with patch.object(GalaxyClient, "_get_collection_detail", tracking_detail):
                client = GalaxyClient()
                await client.search_collections("test")

        assert max_concurrent <= 5, f"Expected max 5 concurrent, got {max_concurrent}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestEnrichmentSemaphore -v`
Expected: FAIL — `_enrichment_semaphore` not importable, and without it max_concurrent may exceed 5.

- [ ] **Step 3: Implement concurrency semaphore**

In `src/ansible_know/galaxy.py`:

1. Add module-level semaphore after the cache constants:

```python
_enrichment_semaphore = asyncio.Semaphore(5)
```

2. Wrap the `_enrich` coroutine inside `search_collections`:

```python
async def _enrich(cand: dict) -> None:
    async with _enrichment_semaphore:
        try:
            detail = await self._get_collection_detail(
                cand["_ns"], cand["_name"],
            )
            cand["download_count"] = detail.get("download_count", 0)
            highest = detail.get("highest_version", {})
            if isinstance(highest, dict):
                cand["latest_version"] = highest.get(
                    "version", cand["latest_version"],
                )
        except GalaxyError:
            cand["download_count"] = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "feat(galaxy): limit enrichment concurrency with semaphore

Module-level asyncio.Semaphore(5) throttles parallel detail
requests in search_collections to avoid Galaxy rate limiting.
Resolves part of #16.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Negative Cache for Fallback Latency (server.py)

**Files:**
- Modify: `src/ansible_know/server.py:54-100` (_resolve_module_doc, ensure_collection)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing tests for negative cache**

Add to `tests/test_server.py`:

```python
class TestNegativeCache:
    @pytest.fixture(autouse=True)
    def reset_negative_cache(self):
        from ansible_know import server
        server._missing_collections.clear()
        yield
        server._missing_collections.clear()

    @pytest.mark.asyncio
    async def test_skips_ansible_doc_on_cache_hit(self, mock_ansible_doc):
        from ansible_know.errors import GalaxyError
        from ansible_know import server
        server._missing_collections.add("netbox.netbox")

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
            from ansible_know.server import get_module_doc
            result = await get_module_doc("netbox.netbox.netbox_device")

        mock_ansible_doc.assert_not_called()
        assert result["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_populates_cache_on_collection_not_found(self, mock_ansible_doc):
        from ansible_know.errors import CollectionNotFoundError, GalaxyError
        from ansible_know import server

        mock_ansible_doc.side_effect = CollectionNotFoundError(
            "netbox.netbox has no attribute"
        )

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("not found"),
        ):
            from ansible_know.server import get_module_doc
            await get_module_doc("netbox.netbox.netbox_device")

        assert "netbox.netbox" in server._missing_collections

    @pytest.mark.asyncio
    async def test_ensure_collection_clears_negative_cache(self):
        from ansible_know import server
        server._missing_collections.add("netbox.netbox")

        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        mock_result = MagicMock()
        mock_result.stdout = galaxy_stdout
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=mock_result):
                import ansible_know.collections as col
                col._installed = {}
                col._tmp_dir = None
                from ansible_know.server import ensure_collection
                await ensure_collection("netbox.netbox")

        assert "netbox.netbox" not in server._missing_collections

    @pytest.mark.asyncio
    async def test_does_not_cache_non_collection_errors(self, mock_ansible_doc):
        from ansible_know.errors import AnsibleDocError
        from ansible_know import server

        mock_ansible_doc.side_effect = AnsibleDocError("ansible-doc timed out")

        from ansible_know.server import get_module_doc
        await get_module_doc("ansible.builtin.copy")

        assert "ansible.builtin" not in server._missing_collections
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py::TestNegativeCache -v`
Expected: FAIL — `_missing_collections` doesn't exist in server module.

- [ ] **Step 3: Implement negative cache**

In `src/ansible_know/server.py`:

1. Add module-level set after the `_MISSING_COLLECTION_PATTERNS` line:

```python
_missing_collections: set[str] = set()
```

2. Update `_resolve_module_doc` to check negative cache before calling ansible-doc:

```python
async def _resolve_module_doc(module_name: str, http_client=None) -> tuple[dict, dict | None]:
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    namespace = ".".join(module_name.split(".")[:2]) if "." in module_name else None

    if namespace and namespace in _missing_collections:
        try:
            from ansible_know.galaxy import GalaxyClient
            client = GalaxyClient(http_client=http_client)
            galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
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
            from ansible_know.galaxy import GalaxyClient
            client = GalaxyClient(http_client=http_client)
            galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
            raise local_exc from galaxy_exc
```

3. Update `ensure_collection` to clear the negative cache after successful install. Add after the `result = await _run_in_executor(...)` line:

```python
_missing_collections.discard(collection_namespace)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "feat(server): negative cache skips ansible-doc for missing collections

Module-level _missing_collections set tracks namespaces that
failed ansible-doc lookup. Subsequent lookups skip straight to
Galaxy, saving ~500ms per call. ensure_collection clears entries
on successful install.
Resolves part of #16.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Final Integration Verification

**Files:**
- All modified files from Tasks 1-5

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: ALL tests pass (182+ existing + new tests from this plan).

- [ ] **Step 2: Verify no regressions with pyright**

Run: `.venv/bin/pyright src/ansible_know/galaxy.py src/ansible_know/server.py`
Expected: No errors.

- [ ] **Step 3: Verify the import chain works**

Run: `.venv/bin/python -c "from ansible_know.server import mcp; print(mcp.settings.name)"`
Expected: Prints "Ansible Know" without errors.

- [ ] **Step 4: Final commit if any fixups needed**

If any fixes were needed in steps 1-3, commit them:

```bash
git add -A
git commit -m "fix: integration fixups for Galaxy performance changes

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```
