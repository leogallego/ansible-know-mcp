# Galaxy Client Performance Improvements

Resolves: [#16](https://github.com/leogallego/ansible-know-mcp/issues/16)

## Goal

Reduce Galaxy API latency through connection pooling, concurrency control, granular timeouts, smarter caching, and fallback optimization.

## Scope

Five changes to `galaxy.py` and `server.py`. `docs.py` is explicitly out of scope — its manifest cache means the httpx client is created once per server session, not worth optimizing.

---

## 1. Connection Pooling via FastMCP Lifespan

### Problem

`GalaxyClient` creates a new `httpx.AsyncClient` per API call (~100-300ms TCP+TLS overhead). `fetch_module_doc` alone creates 2 throwaway clients per invocation.

### Design

- Add a `@lifespan` function in `server.py` that creates one `httpx.AsyncClient` on server startup and closes it on shutdown via `async with`. The client is yielded as `{"http_client": client}` and available to tools via `ctx.lifespan_context["http_client"]`.

- `GalaxyClient.__init__` accepts an optional `http_client: httpx.AsyncClient | None = None`. When provided, all API calls use it. When `None` (standalone use, testing), `GalaxyClient` creates its own internal client lazily (one per instance, for test isolation).

- Remove the `client: httpx.AsyncClient | None` parameter from `_api_get`, `_safe_api_get`, and `_get_collection_detail`. These methods always use `self._http_client`.

- Remove the `async with httpx.AsyncClient() as shared_client:` context manager in `search_collections` — no longer needed.

- `_resolve_module_doc` accepts an `http_client` parameter, passed from calling tools that have access to `ctx.lifespan_context`.

### Files Changed

- `server.py`: add lifespan, pass http_client to GalaxyClient and `_resolve_module_doc`
- `galaxy.py`: refactor `__init__`, `_api_get`, `_safe_api_get`, `_get_collection_detail`, `search_collections`

---

## 2. Concurrency Semaphore on Enrichment

### Problem

`search_collections` fires unbounded `asyncio.gather` for detail enrichment — up to 10 simultaneous requests that risk Galaxy rate limiting.

### Design

- Add `self._enrichment_semaphore = asyncio.Semaphore(5)` as an instance attribute on `GalaxyClient`.

- The `_enrich` coroutine inside `search_collections` acquires the semaphore before calling `_get_collection_detail`.

- Only enrichment is throttled. Version lookups, docs-blob fetches, and search calls are not affected — they are typically single requests per tool call.

### Files Changed

- `galaxy.py`: add semaphore to `__init__`, wrap `_enrich` in `search_collections`

---

## 3. Granular httpx Timeouts

### Problem

All API calls use a flat `timeout=30`. Version lookups should fail faster (small JSON), docs-blob fetches may need longer (large payloads for big collections).

### Design

Three timeout profiles defined as module-level constants in `galaxy.py`:

| Profile | connect | read | Used by |
|---------|---------|------|---------|
| `TIMEOUT_FAST` | 10s | 10s | `latest_version`, `_get_collection_detail` |
| `TIMEOUT_DEFAULT` | 10s | 30s | `search_collections` search call |
| `TIMEOUT_SLOW` | 10s | 60s | `_fetch_docs_blob` |

Each method passes its timeout to `_api_get`, which passes it through to `self._http_client.get(url, timeout=...)`.

The shared lifespan `httpx.AsyncClient` is created with `timeout=None` (no default) — per-request timeouts take precedence.

### Files Changed

- `galaxy.py`: add constants, add `timeout` parameter to `_api_get`, pass appropriate timeout from each calling method

---

## 4. Cache TTL + Sizing

### Problem

`MAX_BLOB_CACHE_SIZE = 100` could hold 10-50MB. No TTL means stale version/blob data can persist across long sessions.

### Design

- Reduce `MAX_BLOB_CACHE_SIZE` from 100 to 50. Each blob can be 100KB-500KB for large collections. 50 entries is sufficient for a session.

- `MAX_VERSION_CACHE_SIZE` stays at 500 — version strings are negligible in size.

- Add TTL-based eviction with a 1-hour expiry (`CACHE_TTL_SECONDS = 3600`). Each cache entry stores `(value, timestamp)`. On read, if the entry is older than the TTL, discard it and return `None` (cache miss).

- The `_put_*_cache` helpers store `(value, time.monotonic())`. The `_get_*_cache` helpers check the timestamp and evict expired entries.

- `clear_cache()` continues to work for testing. No changes to its interface.

### Files Changed

- `galaxy.py`: modify cache helpers, add `CACHE_TTL_SECONDS` constant, reduce `MAX_BLOB_CACHE_SIZE`

---

## 5. Negative Cache for Fallback Latency

### Problem

`_resolve_module_doc` always tries local `ansible-doc` first, even for collections already known to be missing. Each failed attempt costs ~500ms.

### Design

- Add a module-level `_missing_collections: set[str] = set()` in `server.py`.

- `_resolve_module_doc` extracts the collection namespace (first two segments of the FQCN). If the namespace is in `_missing_collections`, skip `ansible-doc` and go straight to Galaxy.

- When `ansible-doc` raises `CollectionNotFoundError`, add the namespace to `_missing_collections`.

- In `ensure_collection`, after a successful install, call `_missing_collections.discard(namespace)` so subsequent lookups try local again.

- No TTL needed. The set lives in-process and resets when the server restarts — same lifecycle as the temp collection directory managed by `ensure_collection`.

### Files Changed

- `server.py`: add `_missing_collections` set, modify `_resolve_module_doc` and `ensure_collection`

---

## Out of Scope

- **docs.py client reuse**: Manifests cache after first fetch. The httpx client is created once per server session. Not worth changing.
- **HTTP/2**: Would require `httpx[http2]` dependency. Marginal benefit for JSON API calls. Can revisit later.
- **Parallel `ansible-doc` calls**: `get_collection_manifest` calls `ansible-doc` sequentially per module. Parallelizing subprocess calls is a different kind of optimization (CPU-bound, not network-bound).

## Testing Strategy

- Existing tests mock `_api_get` or `httpx.AsyncClient` — both patterns continue to work. `GalaxyClient(http_client=None)` preserves standalone behavior for tests.
- New tests for: semaphore limiting concurrent enrichment, TTL expiry evicting cache entries, negative cache hit/miss/invalidation, timeout constants being passed correctly.
- All 182 existing tests must continue to pass.
