# Batch Collection Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch all module documentation for a collection in a single Galaxy API call, eliminating N redundant lookups.

**Architecture:** New `GalaxyClient.fetch_collection_docs()` method (External Access) extracts all module docs from a single docs-blob fetch. New `resolve_collection_module_docs()` in resolution.py (Domain/Orchestration) wraps the Galaxy call with multi-server fallback. New `get_collection_docs` MCP tool exposes the batch fetch. `generate_collection_skills` integrates the batch path when collections aren't installed locally.

**Tech Stack:** Python 3.10+, httpx, FastMCP, pytest

## Global Constraints

- Use `TypedDict` from `types.py` for all structured return types — never bare `dict[str, Any]`.
- Galaxy method = External Access layer; resolution function = Domain via Orchestration delegation.
- New methods need contract-style docstrings (preconditions, raises, silences).
- `_fetch_docs_blob` already caches — reuse, don't add new caching.
- Non-breaking: new method and tool, no changes to existing signatures.
- Sandbox mode: use `.venv/bin/` paths, no env var prefixes, no `source activate`.

---

### Task 1: `GalaxyClient.fetch_collection_docs()` method

**Files:**
- Modify: `src/ansible_know/galaxy.py` — add `fetch_collection_docs` method after `fetch_plugin_doc`
- Test: `tests/test_galaxy.py` — add `TestFetchCollectionDocs` class

**Interfaces:**
- Consumes: `_fetch_docs_blob()`, `_find_module()` (both existing on GalaxyClient), `transform_galaxy_to_ansible_doc_format` from `parser.py`, `extract_module_metadata` from `parser.py`
- Produces: `fetch_collection_docs(collection_namespace: str, version: str | None = None) -> tuple[dict[str, ModuleMetadata], DocProvenance]` — dict keyed by FQCN with `ModuleMetadata` values, plus provenance metadata. Used by Task 2 (`resolve_collection_module_docs`) and Task 3 (`get_collection_docs` tool).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_galaxy.py`:

```python
class TestFetchCollectionDocs:
    @pytest.mark.asyncio
    async def test_returns_all_module_docs(self):
        """Batch extracts docs for every module in the blob."""
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            docs, meta = await client.fetch_collection_docs("netbox.netbox")

        assert "netbox.netbox.netbox_device" in docs
        assert "netbox.netbox.netbox_site" in docs
        assert len(docs) == 2  # only modules, not inventory plugin
        assert docs["netbox.netbox.netbox_device"]["module_name"] == "netbox.netbox.netbox_device"
        assert docs["netbox.netbox.netbox_device"]["short_description"] == "Create, update or delete devices"
        assert meta["doc_source"] == "galaxy"
        assert meta["doc_version"] == "3.23.0"

    @pytest.mark.asyncio
    async def test_with_explicit_version(self):
        """Explicit version skips latest_version lookup and omits warning."""
        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "docs-blob" in path:
                assert "3.20.0" in path
                return SAMPLE_DOCS_BLOB
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            docs, meta = await client.fetch_collection_docs(
                "netbox.netbox", version="3.20.0",
            )

        assert meta["doc_version"] == "3.20.0"
        assert "doc_warning" not in meta

    @pytest.mark.asyncio
    async def test_empty_collection_returns_empty_dict(self):
        """Collection with no modules returns empty dict, not error."""
        empty_blob = {"docs_blob": {"contents": [
            {"content_type": "inventory", "content_name": "nb_inventory",
             "doc_strings": {"doc": {"short_description": "Inv plugin"}, "examples": "", "return": [], "metadata": {}}},
        ]}}

        async def mock_api_get(self_client, path, params=None, timeout=None):
            if "versions/" in path and "docs-blob" not in path:
                return SAMPLE_VERSIONS_RESPONSE
            if "docs-blob" in path:
                return empty_blob
            return {}

        with patch.object(GalaxyClient, "_api_get", mock_api_get):
            client = GalaxyClient()
            docs, meta = await client.fetch_collection_docs("netbox.netbox")

        assert docs == {}
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_rejects_invalid_namespace(self):
        """Namespace must be namespace.name format."""
        client = GalaxyClient()
        with pytest.raises(GalaxyError, match="not a valid collection FQCN"):
            await client.fetch_collection_docs("just_one_part")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestFetchCollectionDocs -v`
Expected: FAIL — `GalaxyClient` has no attribute `fetch_collection_docs`

- [ ] **Step 3: Implement `fetch_collection_docs` in `galaxy.py`**

Add after `fetch_plugin_doc` (around line 491):

```python
async def fetch_collection_docs(
    self, collection_namespace: str, version: str | None = None,
) -> tuple[dict[str, ModuleMetadata], DocProvenance]:
    """Fetch all module docs from a collection in one docs-blob call.

    Extracts every module entry from the blob and transforms each into
    the same ``ModuleMetadata`` shape that ``extract_module_metadata``
    produces from ansible-doc output.

    Contract:
        Preconditions:
            - ``collection_namespace`` must be 'namespace.name' format
              (two dot-separated segments). Raises ``GalaxyError`` if not.

        Raises:
            GalaxyError: If the namespace is malformed, the collection is
                not found on Galaxy, or the API request fails.

        Silences:
            - Individual modules whose ``transform_galaxy_to_ansible_doc_format``
              or ``extract_module_metadata`` raises are logged and skipped.
              The caller receives docs for the remaining modules with no
              indication of partial failure (check logs).
    """
    from ansible_know.parser import (
        extract_module_metadata,
        transform_galaxy_to_ansible_doc_format,
    )

    parts = collection_namespace.split(".")
    if len(parts) != 2:
        raise GalaxyError(
            f"'{collection_namespace}' is not a valid collection FQCN "
            f"(expected namespace.name)."
        )
    namespace, name = parts
    resolved_version = version or await self.latest_version(namespace, name)
    is_latest = version is None

    blob = await self._fetch_docs_blob(namespace, name, resolved_version)
    result: dict[str, ModuleMetadata] = {}
    for item in blob.get("contents", []):
        if item.get("content_type") != "module":
            continue
        short_name = item.get("content_name", "")
        fqcn = f"{collection_namespace}.{short_name}"
        try:
            raw_doc = transform_galaxy_to_ansible_doc_format(fqcn, item)
            result[fqcn] = extract_module_metadata(raw_doc)
        except Exception:
            logger.warning("Skipping module %s: metadata extraction failed", fqcn)

    meta: DocProvenance = {
        "doc_source": "galaxy",
        "doc_version": resolved_version,
    }
    if is_latest:
        meta["doc_warning"] = (
            f"Documentation sourced from Galaxy "
            f"({namespace}.{name} {resolved_version}). "
            f"Your installed version may differ."
        )
    if self.server_name:
        meta["doc_source_server"] = self.server_name
    return result, meta
```

Add the import for `ModuleMetadata` under `TYPE_CHECKING` at the top of `galaxy.py`:

```python
if TYPE_CHECKING:
    from ansible_know.galaxy_config import GalaxyServerConfig
    from ansible_know.types import DocProvenance, ModuleMetadata
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestFetchCollectionDocs -v`
Expected: 4 passed

- [ ] **Step 5: Run full test suite and lint**

Run: `.venv/bin/ruff check src/ansible_know/galaxy.py && .venv/bin/pytest tests/test_galaxy.py -v`
Expected: All pass, no lint errors

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "feat(galaxy): add fetch_collection_docs batch method

Fetches docs-blob once and extracts all module docs in a single pass.
Returns dict[str, ModuleMetadata] keyed by FQCN.

Refs #85

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: `resolve_collection_module_docs()` in resolution.py

**Files:**
- Modify: `src/ansible_know/resolution.py` — add `resolve_collection_module_docs` function
- Modify: `src/ansible_know/types.py` — add `CollectionDocsResult` TypedDict and update `GalaxyDocClient` protocol
- Test: `tests/test_resolution.py` — add `TestResolveCollectionModuleDocs` class

**Interfaces:**
- Consumes: `_try_galaxy_servers()` (existing in resolution.py), `GalaxyClient.fetch_collection_docs()` (from Task 1)
- Produces: `resolve_collection_module_docs(collection_namespace, version?, http_client?, galaxy_servers?, client_factory?) -> CollectionDocsResult` — returns `{"modules": dict[str, ModuleMetadata], "doc_source": str, "doc_version": str}`. Used by Task 3 (`get_collection_docs` tool) and Task 4 (`generate_collection_skills` integration).

- [ ] **Step 1: Add `CollectionDocsResult` TypedDict to `types.py`**

Add after `GenerateCollectionSkillsResult` (around line 303):

```python
class _CollectionDocsResultBase(TypedDict):
    """Required fields for batch collection docs result."""

    modules: dict[str, ModuleMetadata]
    doc_source: str


class CollectionDocsResult(_CollectionDocsResultBase, total=False):
    """Result of resolve_collection_module_docs / get_collection_docs.

    When doc_source is 'galaxy', includes doc_version and optionally
    doc_warning/doc_source_server.
    """

    doc_version: str
    doc_warning: str
    doc_source_server: str
```

Update `GalaxyDocClient` protocol to include `fetch_collection_docs`:

```python
class GalaxyDocClient(Protocol):
    # ... existing methods ...

    async def fetch_collection_docs(
        self, collection_namespace: str, version: str | None = None,
    ) -> tuple[dict[str, ModuleMetadata], DocProvenance]: ...
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_resolution.py`:

```python
class TestResolveCollectionModuleDocs:
    @pytest.mark.asyncio
    async def test_returns_galaxy_docs(self, missing):
        """Batch fetch via Galaxy returns all module docs."""
        from ansible_know.resolution import resolve_collection_module_docs

        sample_modules = {
            "netbox.netbox.netbox_device": {
                "module_name": "netbox.netbox.netbox_device",
                "short_description": "Create, update or delete devices",
                "params": [],
                "examples": "",
                "is_api_module": True,
            },
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_collection_docs",
            new_callable=AsyncMock,
            return_value=(sample_modules, galaxy_meta),
        ):
            result = await resolve_collection_module_docs(
                "netbox.netbox",
                galaxy_servers=SERVERS,
                client_factory=FACTORY,
                missing_collections=missing,
            )

        assert result["doc_source"] == "galaxy"
        assert "netbox.netbox.netbox_device" in result["modules"]
        assert result["doc_version"] == "3.23.0"

    @pytest.mark.asyncio
    async def test_galaxy_failure_returns_error(self, missing):
        """When Galaxy fails, returns error response."""
        from ansible_know.resolution import resolve_collection_module_docs

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_collection_docs",
            new_callable=AsyncMock,
            side_effect=GalaxyError("Connection failed"),
        ):
            result = await resolve_collection_module_docs(
                "netbox.netbox",
                galaxy_servers=SERVERS,
                client_factory=FACTORY,
                missing_collections=missing,
            )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_factory_returns_error(self, missing):
        """Without client_factory, returns error immediately."""
        from ansible_know.resolution import resolve_collection_module_docs

        result = await resolve_collection_module_docs(
            "netbox.netbox",
            missing_collections=missing,
        )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_passes_version_through(self, missing):
        """Explicit version is forwarded to Galaxy client."""
        from ansible_know.resolution import resolve_collection_module_docs

        with patch(
            "ansible_know.galaxy.GalaxyClient.fetch_collection_docs",
            new_callable=AsyncMock,
            return_value=({}, {"doc_source": "galaxy", "doc_version": "3.20.0"}),
        ) as mock_fetch:
            await resolve_collection_module_docs(
                "netbox.netbox",
                version="3.20.0",
                galaxy_servers=SERVERS,
                client_factory=FACTORY,
                missing_collections=missing,
            )

        mock_fetch.assert_called_once_with("netbox.netbox", version="3.20.0")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resolution.py::TestResolveCollectionModuleDocs -v`
Expected: FAIL — `resolve_collection_module_docs` not found

- [ ] **Step 4: Implement `resolve_collection_module_docs` in `resolution.py`**

Add after `resolve_role_doc` (around line 331). Add to `__all__`:

```python
async def resolve_collection_module_docs(
    collection_namespace: str,
    version: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
) -> CollectionDocsResult | ErrorResponse:
    """Batch-fetch all module docs for a collection from Galaxy.

    Delegates to ``GalaxyClient.fetch_collection_docs`` via the
    multi-server fallback chain. Does NOT try local ansible-doc —
    the caller decides whether to use this (Galaxy-only) path or
    the existing per-module local path.

    Contract:
        Preconditions:
            - ``client_factory`` must be provided. If ``None``, returns
              an ``ErrorResponse`` immediately (no exception raised).

        Raises:
            Nothing — errors are returned as ``ErrorResponse`` dicts.

        Silences:
            - ``GalaxyError`` from all servers: caught and returned as
              ``{"error": str}`` after sanitization. Individual server
              failures are logged at INFO level by ``_try_galaxy_servers``.
    """
    from ansible_know.errors import GalaxyError

    if client_factory is None:
        return {"error": "No Galaxy client configured for collection docs"}

    servers = _get_servers(galaxy_servers)

    async def _fetch(client):
        return await client.fetch_collection_docs(
            collection_namespace, version=version,
        )

    try:
        modules, galaxy_meta = await _try_galaxy_servers(
            servers, _fetch, client_factory, http_client,
        )
        result: CollectionDocsResult = {
            "modules": modules,
            "doc_source": "galaxy",
        }
        if "doc_version" in galaxy_meta:
            result["doc_version"] = galaxy_meta["doc_version"]
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as exc:
        return {"error": sanitize_error(str(exc))}
```

Add `CollectionDocsResult` to the imports from `types.py` in `resolution.py` (inside `TYPE_CHECKING`):

```python
from ansible_know.types import (
    CollectionDocsResult,
    DocProvenance,
    ErrorResponse,
    GalaxyClientFactory,
    GetPluginDocResult,
    GetRoleDocResult,
)
```

Add `"resolve_collection_module_docs"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolution.py::TestResolveCollectionModuleDocs -v`
Expected: 4 passed

- [ ] **Step 6: Run full suite and lint**

Run: `.venv/bin/ruff check src/ansible_know/resolution.py src/ansible_know/types.py && .venv/bin/pytest tests/test_resolution.py -v`
Expected: All pass, no lint errors

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/resolution.py src/ansible_know/types.py tests/test_resolution.py
git commit -m "feat(resolution): add resolve_collection_module_docs batch function

Wraps GalaxyClient.fetch_collection_docs with multi-server fallback.
Adds CollectionDocsResult TypedDict for the batch return type.

Refs #85

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: `get_collection_docs` MCP tool

**Files:**
- Modify: `src/ansible_know/server.py` — add `get_collection_docs` tool function
- Test: `tests/test_server.py` — add `TestGetCollectionDocsTool` class

**Interfaces:**
- Consumes: `resolve_collection_module_docs()` from Task 2, `validate_namespace()` from `validation.py`, `_get_state()`, `_get_http_client()`, `_galaxy_factory()` (all existing in server.py)
- Produces: `get_collection_docs(collection_namespace, version?) -> CollectionDocsResult | ErrorResponse` MCP tool. Returns `{"modules": {fqcn: ModuleMetadata, ...}, "doc_source": "galaxy", "doc_version": str}`. Not consumed by other tasks — user-facing tool.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
class TestGetCollectionDocsTool:
    @pytest.mark.asyncio
    async def test_returns_batch_docs(self, mock_ansible_doc):
        from ansible_know.server import get_collection_docs

        sample_result = {
            "modules": {
                "netbox.netbox.netbox_device": {
                    "module_name": "netbox.netbox.netbox_device",
                    "short_description": "Create, update or delete devices",
                    "params": [],
                    "examples": "",
                    "is_api_module": True,
                },
            },
            "doc_source": "galaxy",
            "doc_version": "3.23.0",
        }

        with patch(
            "ansible_know.resolution.resolve_collection_module_docs",
            new_callable=AsyncMock,
            return_value=sample_result,
        ):
            result = await get_collection_docs("netbox.netbox")

        assert "modules" in result
        assert "netbox.netbox.netbox_device" in result["modules"]
        assert result["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_rejects_invalid_namespace(self):
        from ansible_know.server import get_collection_docs

        result = await get_collection_docs("invalid")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_fqcn_with_three_parts(self):
        from ansible_know.server import get_collection_docs

        result = await get_collection_docs("a.b.c")
        assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py::TestGetCollectionDocsTool -v`
Expected: FAIL — `get_collection_docs` not found

- [ ] **Step 3: Implement `get_collection_docs` tool in `server.py`**

Add after `get_collection_manifest` (around line 730). Place it near the other `get_*` tools:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_collection_docs(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[str | None, "Optional version (e.g. '3.23.0'). If omitted, uses latest."] = None,
    ctx: Context | None = None,
) -> CollectionDocsResult | ErrorResponse:
    """Get full module documentation for all modules in a collection from Galaxy.

    Returns all module docs in a single API call without installing the collection.
    Result shape: {"modules": {fqcn: {module_name, short_description, params, examples, is_api_module}, ...},
    "doc_source": "galaxy", "doc_version": str}.
    On failure returns {"error": str}.
    """
    logger.info("get_collection_docs namespace=%r version=%r", collection_namespace, version)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_namespace(collection_namespace)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        return await resolution.resolve_collection_module_docs(
            collection_namespace,
            version=version,
            http_client=http_client,
            galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
        )
    except Exception as exc:
        logger.warning("get_collection_docs failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}
```

Add `CollectionDocsResult` to the imports from `types.py` at the top of `server.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_server.py::TestGetCollectionDocsTool -v`
Expected: 3 passed

- [ ] **Step 5: Run full suite and lint**

Run: `.venv/bin/ruff check src/ansible_know/server.py && .venv/bin/pytest tests/test_server.py -v`
Expected: All pass, no lint errors

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "feat(server): add get_collection_docs MCP tool

Returns all module documentation for a collection from Galaxy in a
single API call without requiring local installation.

Refs #85

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Integrate batch fetch into `generate_collection_skills`

**Files:**
- Modify: `src/ansible_know/server.py:1154-1346` — update `generate_collection_skills` to use batch path for Galaxy fallback
- Test: `tests/test_server.py` — add test for Galaxy batch path in `TestGenerateCollectionSkillsTool`

**Interfaces:**
- Consumes: `resolve_collection_module_docs()` from Task 2, `GalaxyClient.list_collection_modules()` (existing in galaxy.py), existing `generate_collection_skills` function
- Produces: Updated `generate_collection_skills` that uses batch fetch when `parser.search_modules` returns empty (collection not installed locally). Same return type as before.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py` inside `TestGenerateCollectionSkillsTool`:

```python
    @pytest.mark.asyncio
    async def test_galaxy_batch_fallback(self, tmp_path, mock_ansible_doc, monkeypatch):
        """When search_modules returns empty, falls back to Galaxy batch fetch."""
        # search_modules returns empty (collection not installed)
        # list_roles returns empty
        # 14 plugin types return empty
        responses = [
            json.dumps({}),  # search_modules — empty
            json.dumps({}),  # list_roles — empty
        ]
        responses.extend([json.dumps({})] * 14)  # 14 plugin types

        mock_ansible_doc.side_effect = responses
        monkeypatch.setattr("ansible_know.config.SKILLS_DIR", tmp_path)

        sample_batch = {
            "netbox.netbox.netbox_device": {
                "module_name": "netbox.netbox.netbox_device",
                "short_description": "Create, update or delete devices",
                "params": [{"name": "data", "type": "dict", "required": True,
                            "default": None, "choices": None,
                            "description": "Device data", "aliases": []}],
                "examples": "- name: Create\n  netbox.netbox.netbox_device:\n    data: {}\n",
                "is_api_module": True,
            },
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "3.23.0"}

        with patch(
            "ansible_know.resolution.resolve_collection_module_docs",
            new_callable=AsyncMock,
            return_value={
                "modules": sample_batch,
                "doc_source": "galaxy",
                "doc_version": "3.23.0",
            },
        ):
            from ansible_know.server import generate_collection_skills
            result = await generate_collection_skills(
                "netbox.netbox", install_to=str(tmp_path),
            )

        assert result["total"] == 1
        assert result["succeeded"] == 1
        assert (tmp_path / "netbox.netbox" / "netbox_device" / "SKILL.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_server.py::TestGenerateCollectionSkillsTool::test_galaxy_batch_fallback -v`
Expected: FAIL — current code returns `{"error": "No modules..."}` when search_modules is empty

- [ ] **Step 3: Modify `generate_collection_skills` to add Galaxy batch fallback**

In `server.py`, after the module discovery block (around line 1183), add the Galaxy batch fallback. The key change: when `modules` is empty, try `resolve_collection_module_docs` to get module docs from Galaxy:

```python
        # Discover modules (local)
        modules = await run_in_executor(
            parser.search_modules, "", collection_filter=collection_namespace,
            collections_path=cpath,
        )

        # Galaxy batch fallback for modules when collection not installed locally
        galaxy_batch_modules: dict[str, Any] = {}
        if not modules:
            batch_result = await resolution.resolve_collection_module_docs(
                collection_namespace,
                http_client=_get_http_client(ctx),
                galaxy_servers=state.galaxy_servers,
                client_factory=_galaxy_factory(ctx),
                missing_collections=state.missing_collections,
            )
            if "modules" in batch_result:
                galaxy_batch_modules = batch_result["modules"]
```

Then update the "combined guard" to also check `galaxy_batch_modules`:

```python
        if not modules and not galaxy_batch_modules and not roles_raw and not has_plugins:
            return {"error": (
                f"No modules, roles, or plugins found in collection '{collection_namespace}'."
                + collection_hint(collection_namespace)
            )}
```

Update the total count:

```python
        total = len(modules) + len(galaxy_batch_modules) + len(roles_raw) + plugin_count
```

After the existing module skills loop, add the Galaxy batch module skills loop:

```python
        # Generate module skills from Galaxy batch (when not installed locally)
        for module_fqcn, module_meta in sorted(galaxy_batch_modules.items()):
            if ctx:
                await ctx.report_progress(progress=current, total=total)
            current += 1
            try:
                metadata_list.append(module_meta)
                short_name = module_fqcn.rsplit(".", 1)[-1]
                output_dir = base_dir / collection_namespace / short_name
                await run_in_executor(skills.write_module_skill_package, output_dir, module_meta)
                succeeded += 1
            except Exception as exc:
                logger.warning("Module skill generation failed for %s: %s", module_fqcn, exc)
                failed += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_server.py::TestGenerateCollectionSkillsTool -v`
Expected: Both existing and new test pass

- [ ] **Step 5: Run full suite and lint**

Run: `.venv/bin/ruff check src/ansible_know/server.py && .venv/bin/pytest tests/ -v`
Expected: All pass, no lint errors

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "feat(server): integrate Galaxy batch fetch into generate_collection_skills

When a collection is not installed locally, falls back to batch
Galaxy docs-blob fetch instead of failing. Eliminates N individual
fetch_module_doc calls.

Refs #85

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Update documentation and close issue

**Files:**
- Modify: `CLAUDE.md` — add `get_collection_docs` to MCP Tools table
- No code changes

**Interfaces:**
- Consumes: nothing new
- Produces: Updated docs

- [ ] **Step 1: Update CLAUDE.md MCP Tools table**

Add after `get_collection_manifest` row:

```markdown
| `get_collection_docs` | read-only | Get all module docs for a collection from Galaxy |
```

- [ ] **Step 2: Run full test suite one final time**

Run: `.venv/bin/ruff check src/ tests/ && .venv/bin/pytest tests/ -v`
Expected: All pass, no lint errors

- [ ] **Step 3: Commit and prepare for PR**

```bash
git add CLAUDE.md
git commit -m "docs: add get_collection_docs to MCP tools table

Closes #85

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```
