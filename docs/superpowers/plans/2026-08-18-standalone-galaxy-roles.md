# Standalone Galaxy Role Search and Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `search_standalone_roles` and `get_standalone_role_doc` MCP tools that query Galaxy **v1** standalone roles (`namespace.role`) without changing v3 collection/module/plugin/collection-role paths.

**Architecture:** Dedicated `GalaxyV1Client` in `galaxy_v1.py` (External Access) shares lifespan httpx + `GalaxyServerConfig` auth only. Domain `resolution.py` adds `search_standalone_roles` and `resolve_standalone_role_doc` plus a duplicated `_try_v1_servers` helper. Orchestration in `server.py` validates, injects `_galaxy_v1_factory`, and clears the v1 cache from the existing `clear_cache` tool. Do not import `galaxy_v1` from `galaxy.py`. Do not extend `GalaxyDocClient`.

**Tech Stack:** Python 3.10+, httpx, FastMCP, pytest, asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-18-standalone-galaxy-roles-design.md`  
**Issue:** [#230](https://github.com/leogallego/ansible-know-mcp/issues/230)

## Global Constraints

- Client: separate `GalaxyV1Client` (`src/ansible_know/galaxy_v1.py`), not methods on `GalaxyClient`.
- Transport only: shared lifespan `httpx.AsyncClient` + `GalaxyServerConfig` auth via `from_config`. Not shared: discovery, URL builders, `_discovery_failed`, v3 caches, `GalaxyDocClient`.
- Search query param: `keywords` (never `search` / `keyword`). `order_by=-download_count`, `page_size=10`.
- Exact lookup: `namespace` + `name` only. Never `owner__username` + `name` together.
- README: `GET /api/v1/roles/{id}/content/` `readme_html` + existing `parse_role_readme()`. No GitHub.
- Identifier: `{username}.{name}` (Galaxy legacy namespace, not `github_user`).
- Validator: new 2-part hyphen/mixed-case `validate_standalone_role_name`. Do not reuse `validate_fqcn` / `validate_namespace`.
- `content_type`: `"standalone_role"`. No local ansible-doc. No skills, install, or GitHub fallback.
- `galaxy_v1.py` must not import `galaxy.py`. Import `GalaxyError` from `errors.py` only. Duplicate timeout/size constants or read them from `config.py`.
- List JSON is DRF `{count, results}` — not v3 `{data, meta}`.
- v1 `tags` filter is a single JSON-contains value: send only the first comma-separated segment.
- Search all-fail: raise `GalaxyError` (match `search_galaxy_collections`). Empty hits are success (`count: 0`).
- MCP `clear_cache` (scope `galaxy` or omitted) calls `galaxy_v1.clear_cache()`. `galaxy.clear_cache()` stays v3-only.
- Multi-server get-doc: new `_try_v1_servers`. Do not reuse `_try_galaxy_servers` / do not extend `GalaxyDocClient`.
- Both resolution paths must use `_select_http_client` (skip shared httpx when `validate_certs` is false).
- Tool count 20 → 22. Follow-up issues (GitHub README, skill gen, role install) are out of scope.

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/ansible_know/galaxy_v1.py` | `GalaxyV1Client` + module-level v1 cache |
| Create | `tests/test_galaxy_v1.py` | HTTP-mocked v1 client + isolation vs `GalaxyClient` |
| Modify | `src/ansible_know/validation.py` | `validate_standalone_role_name` |
| Modify | `src/ansible_know/types.py` | TypedDicts + `GalaxyV1ClientFactory` |
| Modify | `src/ansible_know/resolution.py` | `_try_v1_servers`, search, get-doc |
| Modify | `src/ansible_know/server.py` | Two tools, factory, instructions, `clear_cache` |
| Modify | `tests/test_validation.py` | New validator cases |
| Modify | `tests/test_resolution.py` | Standalone search/get-doc |
| Modify | `tests/test_server.py` | Tool wiring + cache-clear coupling |
| Modify | `tests/integration/test_galaxy_api.py` | Opt-in live v1 checks |
| Modify | `CLAUDE.md`, `README.md`, `docs/architecture/service-contracts.md` | Tool tables, layer map |
| Unchanged | `src/ansible_know/galaxy.py` | v3 only — do not import v1 |

---

### Task 1: Foundation — validator + TypedDicts

**Files:**
- Modify: `src/ansible_know/validation.py`
- Modify: `src/ansible_know/types.py`
- Modify: `tests/test_validation.py`

**Interfaces:**
- Consumes: `ValidationError`, `MAX_NAMESPACE_LENGTH` (128)
- Produces: `validate_standalone_role_name(name: str) -> None`; `StandaloneRoleSearchEntry`, `StandaloneRoleSearchResult`, `GetStandaloneRoleDocResult`, `GalaxyV1ClientFactory`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validation.py` (import `validate_standalone_role_name`):

```python
class TestValidateStandaloneRoleName:
    def test_accepts_hyphenated_namespace(self):
        validate_standalone_role_name("ansible-lockdown.rhel9_cis")

    def test_accepts_mixed_case(self):
        validate_standalone_role_name("MindPointGroup.RHEL9_CIS")

    def test_accepts_underscore_role(self):
        validate_standalone_role_name("geerlingguy.elasticsearch_curator")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_standalone_role_name("")

    def test_rejects_one_part(self):
        with pytest.raises(ValidationError):
            validate_standalone_role_name("rhel9_cis")

    def test_rejects_three_part_and_points_at_get_role_doc(self):
        with pytest.raises(ValidationError, match="get_role_doc"):
            validate_standalone_role_name("fedora.linux_system_roles.timesync")

    def test_rejects_path_slash(self):
        with pytest.raises(ValidationError):
            validate_standalone_role_name("foo/bar.role")

    def test_rejects_leading_hyphen_segment(self):
        with pytest.raises(ValidationError):
            validate_standalone_role_name("-bad.role")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_validation.py::TestValidateStandaloneRoleName -v`

Expected: FAIL — `ImportError` / `cannot import name 'validate_standalone_role_name'`

- [ ] **Step 3: Implement validator and types**

In `validation.py`, add `"validate_standalone_role_name"` to `__all__` (alphabetically after `validate_skill_name`). After `validate_namespace`, add:

```python
_STANDALONE_ROLE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def validate_standalone_role_name(name: str) -> None:
    """Validate a Galaxy standalone role identifier ``namespace.role``."""
    if not name or "/" in name or "\\" in name:
        raise ValidationError(
            "Invalid standalone role name: expected format 'namespace.role' "
            "with alphanumeric, hyphen, or underscore segments."
        )
    parts = name.split(".")
    if len(parts) == 3:
        raise ValidationError(
            "Invalid standalone role name: expected 'namespace.role'. "
            "Collection roles use get_role_doc() with a 3-part FQCN."
        )
    if len(parts) != 2:
        raise ValidationError(
            "Invalid standalone role name: expected format 'namespace.role' "
            "with alphanumeric, hyphen, or underscore segments."
        )
    for part in parts:
        if (
            not part
            or len(part) > MAX_NAMESPACE_LENGTH
            or not _STANDALONE_ROLE_SEGMENT_RE.match(part)
        ):
            raise ValidationError(
                "Invalid standalone role name: expected format 'namespace.role' "
                "with alphanumeric, hyphen, or underscore segments."
            )
```

In `types.py`, after `CollectionSearchResult` add:

```python
class StandaloneRoleSearchEntry(TypedDict, total=False):
    """Single standalone role from search_standalone_roles."""

    role_name: str
    description: str
    tags: list[str]
    latest_version: str
    download_count: int
    github_user: str
    github_repo: str
    source: str


class StandaloneRoleSearchResult(TypedDict):
    """Result of search_standalone_roles tool."""

    query: str
    count: int
    roles: list[StandaloneRoleSearchEntry]


class _GetStandaloneRoleDocResultBase(TypedDict):
    role_name: str
    content_type: str
    doc_source: str


class GetStandaloneRoleDocResult(_GetStandaloneRoleDocResultBase, total=False):
    """Result of get_standalone_role_doc. No ``error`` field — failures are ErrorResponse."""

    short_description: str
    entry_points: dict[str, EntryPointInfo]
    dependencies: list[str]
    examples: str
    tags: list[str]
    latest_version: str
    github_user: str
    github_repo: str
    github_branch: str
    download_count: int
    doc_version: str
    doc_warning: str
    doc_source_server: str
```

After `GalaxyClientFactory`, add (do **not** change `GalaxyDocClient`):

```python
class GalaxyV1DocClient(Protocol):
    """v1 standalone-role client. Not a GalaxyDocClient."""

    async def search_roles(
        self, query: str, tags: str | None = None,
    ) -> dict[str, Any]: ...

    async def fetch_standalone_role_doc(
        self, role_name: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def __aenter__(self) -> GalaxyV1DocClient: ...

    async def __aexit__(self, *exc: object) -> None: ...


class GalaxyV1ClientFactory(Protocol):
    """Factory that creates GalaxyV1DocClient instances from config."""

    def __call__(
        self,
        config: GalaxyServerConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> GalaxyV1DocClient: ...
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_validation.py::TestValidateStandaloneRoleName tests/test_validation.py::TestValidateFqcn tests/test_validation.py::TestValidateNamespace -v`

Expected: PASS. Existing FQCN/namespace tests still reject hyphens.

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/validation.py src/ansible_know/types.py tests/test_validation.py
git commit -m "$(cat <<'EOF'
feat: add standalone role name validator and result types

Two-part Galaxy v1 identifiers allow hyphens; keep collection FQCN
validation unchanged.

Assisted-by: Cursor (Grok 4.6)
EOF
)"
```

---

### Task 2: `GalaxyV1Client` — discovery, search, lookup, content, docs

**Files:**
- Create: `src/ansible_know/galaxy_v1.py`
- Create: `tests/test_galaxy_v1.py`

**Interfaces:**
- Consumes: Task 1 types; `GalaxyServerConfig`; `parse_role_readme`; `BoundedCache`; `GalaxyError`
- Produces: `GalaxyV1Client.from_config`, `search_roles`, `fetch_role_by_name`, `fetch_role_content`, `fetch_standalone_role_doc`, `clear_cache`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_galaxy_v1.py`:

```python
"""Tests for ansible_know.galaxy_v1."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ansible_know.errors import GalaxyError
from tests.conftest import SAMPLE_ROLE_README_HTML

SAMPLE_ROLE = {
    "id": 42,
    "username": "ansible-lockdown",
    "name": "rhel9_cis",
    "description": "CIS Benchmark for RHEL 9",
    "github_user": "ansible-lockdown",
    "github_repo": "RHEL9-CIS",
    "github_branch": "devel",
    "download_count": 9000,
    "summary_fields": {
        "tags": ["system", "security"],
        "versions": [{"name": "1.2.3"}],
        "dependencies": [{"namespace": "geerlingguy", "name": "repo"}],
    },
}

SAMPLE_LIST = {"count": 1, "next": None, "previous": None, "results": [SAMPLE_ROLE]}


def _mock_http(json_body, status=200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_body
    mock_resp.content = b"{}"
    mock_resp.headers = {}
    if status >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=MagicMock(status_code=status),
        )
    else:
        mock_resp.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _skip_discovery(client):
    client._api_root = "https://galaxy.ansible.com/api"
    client._v1_path = "v1/"
    return client


class TestSearchRoles:
    @pytest.mark.asyncio
    async def test_uses_keywords_order_by_page_size(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            result = await gc.search_roles("rhel9_cis")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["keywords"] == "rhel9_cis"
        assert params["order_by"] == "-download_count"
        assert params["page_size"] == "10"
        assert "search" not in params
        assert "keyword" not in params
        url = mock_client.get.call_args.args[0]
        assert url.endswith("/api/v1/roles/")
        assert result["roles"][0]["role_name"] == "ansible-lockdown.rhel9_cis"

    @pytest.mark.asyncio
    async def test_tags_sends_first_segment_only(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("cis", tags="system,security")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["tags"] == "system"

    @pytest.mark.asyncio
    async def test_does_not_call_content_during_search(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("rhel9_cis")
        urls = [c.args[0] for c in mock_client.get.call_args_list]
        assert all("/content/" not in u for u in urls)


class TestFetchRoleByName:
    @pytest.mark.asyncio
    async def test_lookup_uses_namespace_and_name_not_owner(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            role = await gc.fetch_role_by_name("ansible-lockdown", "rhel9_cis")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["namespace"] == "ansible-lockdown"
        assert params["name"] == "rhel9_cis"
        assert "owner__username" not in params
        assert role["id"] == 42

    @pytest.mark.asyncio
    async def test_empty_results_raises(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({"count": 0, "results": []})
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            with pytest.raises(GalaxyError, match="not found"):
                await gc.fetch_role_by_name("missing", "role")


class TestFetchStandaloneRoleDoc:
    @pytest.mark.asyncio
    async def test_parses_readme_html(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {
            "readme": "README.md",
            "readme_html": SAMPLE_ROLE_README_HTML,
        }
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, prov = await gc.fetch_standalone_role_doc(
                "ansible-lockdown.rhel9_cis",
            )
        content_url = mock_client.get.call_args_list[1].args[0]
        assert content_url.endswith("/api/v1/roles/42/content/")
        assert meta["content_type"] == "standalone_role"
        assert meta["role_name"] == "ansible-lockdown.rhel9_cis"
        assert prov["doc_source"] == "galaxy_v1_readme"
        assert "main" in meta["entry_points"]
        assert meta["github_branch"] == "devel"
        assert "geerlingguy.repo" in meta["dependencies"] or meta["dependencies"]

    @pytest.mark.asyncio
    async def test_empty_html_is_metadata_success(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {"readme": "README.md", "readme_html": ""}
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, prov = await gc.fetch_standalone_role_doc(
                "ansible-lockdown.rhel9_cis",
            )
        assert prov["doc_source"] == "galaxy_v1_metadata"
        assert meta["short_description"] == "CIS Benchmark for RHEL 9"
        assert "doc_warning" in prov

    @pytest.mark.asyncio
    async def test_hyphenated_name_roundtrip(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {"readme": "README.md", "readme_html": "<p>x</p>"}
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, _ = await gc.fetch_standalone_role_doc("ansible-lockdown.rhel9_cis")
        params = mock_client.get.call_args_list[0].kwargs["params"]
        assert params["namespace"] == "ansible-lockdown"
        assert params["name"] == "rhel9_cis"
        assert meta["role_name"] == "ansible-lockdown.rhel9_cis"


class TestV1Discovery:
    @pytest.mark.asyncio
    async def test_requires_v1_not_v3(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({"available_versions": {"v3": "v3/"}})
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = GalaxyV1Client(base_url="https://hub.example")
            with pytest.raises(GalaxyError, match="v1"):
                await gc.search_roles("cis")

    @pytest.mark.asyncio
    async def test_v1_http_404_raises_galaxy_error(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({}, status=404)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            with pytest.raises(GalaxyError):
                await gc.search_roles("cis")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_galaxy_v1.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'ansible_know.galaxy_v1'`

- [ ] **Step 3: Implement `src/ansible_know/galaxy_v1.py`**

Create the module. Copy transport/auth from `GalaxyClient` in `galaxy.py` (`from_config`, `__aenter__`/`close`/`_get_client`, `_ensure_access_token`, `_resolve_auth_headers`, `_api_get`, `_safe_api_get`) with these substitutions:

- No `enrichment_semaphore`.
- Discovery requires **`v1` only** (`available_versions["v1"]`). Missing v1 → `GalaxyError` (“does not support Galaxy API v1”). Set `self._discovery_failed` on **this** instance only. Do not touch `GalaxyClient`.
- Unsafe v1 path: same charset as v3 (`^[a-zA-Z0-9/_-]+/?$`, no `..`).
- URL builder: `{api_root}/{v1_path}roles/` and `{api_root}/{v1_path}roles/{id}/content/`.
- Duplicate `TIMEOUT_*`, `MAX_GALAXY_RESPONSE_SIZE`, `MAX_DISCOVERY_RESPONSE_SIZE`, and a `_normalize_cache_base_url` helper. Do **not** `import ansible_know.galaxy`.
- Module-level `_v1_cache: BoundedCache` max_size 50, ttl 3600, memory-only.
- `clear_cache()` clears `_v1_cache` only.

Unique methods (do not copy from v3):

```python
def _first_tag(tags: str | None) -> str | None:
    if not tags:
        return None
    return tags.split(",", 1)[0].strip() or None


def _normalize_dependencies(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict):
            ns = item.get("namespace") or item.get("username") or ""
            name = item.get("name") or ""
            if ns and name:
                out.append(f"{ns}.{name}")
            elif name:
                out.append(str(name))
    return out


def _map_search_hit(item: dict) -> dict:
    versions = (item.get("summary_fields") or {}).get("versions") or []
    latest = ""
    if versions and isinstance(versions[0], dict):
        latest = versions[0].get("name") or ""
    tags_raw = (item.get("summary_fields") or {}).get("tags") or []
    tags: list[str] = []
    for t in tags_raw:
        if isinstance(t, str):
            tags.append(t)
        elif isinstance(t, dict) and t.get("name"):
            tags.append(str(t["name"]))
    username = item.get("username") or ""
    name = item.get("name") or ""
    return {
        "role_name": f"{username}.{name}",
        "description": item.get("description") or "",
        "tags": tags,
        "latest_version": latest,
        "download_count": item.get("download_count") or 0,
        "github_user": item.get("github_user") or "",
        "github_repo": item.get("github_repo") or "",
    }


async def search_roles(self, query: str, tags: str | None = None) -> dict:
    await self._discover_api_root()
    cache_key = (*self._cache_identity(), "search", query, tags or "")
    cached = _v1_cache.get(cache_key)
    if cached is not None:
        return cached
    params = {
        "keywords": query,
        "order_by": "-download_count",
        "page_size": "10",
    }
    tag = _first_tag(tags)
    if tag:
        params["tags"] = tag
    data = await self._safe_api_get(self._build_v1_url("roles"), params=params)
    hits = data.get("results") or []
    mapped = [_map_search_hit(item) for item in hits if isinstance(item, dict)]
    result = {"query": query, "count": len(mapped), "roles": mapped}
    _v1_cache.put(cache_key, result)
    return result


async def fetch_role_by_name(self, namespace: str, name: str) -> dict:
    await self._discover_api_root()
    params = {"namespace": namespace, "name": name, "page_size": "1"}
    data = await self._safe_api_get(self._build_v1_url("roles"), params=params)
    results = data.get("results") or []
    if not results:
        raise GalaxyError(
            f"Standalone role '{namespace}.{name}' not found"
        )
    return results[0]


async def fetch_role_content(self, role_id: int) -> dict:
    await self._discover_api_root()
    cache_key = (*self._cache_identity(), "content", str(role_id))
    cached = _v1_cache.get(cache_key)
    if cached is not None:
        return cached
    data = await self._safe_api_get(
        self._build_v1_url("roles", str(role_id), "content"),
    )
    _v1_cache.put(cache_key, data)
    return data


async def fetch_standalone_role_doc(self, role_name: str):
    from ansible_know.readme_parser import parse_role_readme

    namespace, _, name = role_name.partition(".")
    role = await self.fetch_role_by_name(namespace, name)
    content = await self.fetch_role_content(int(role["id"]))
    html = content.get("readme_html") or ""
    parsed = parse_role_readme(html)
    summary = role.get("summary_fields") or {}
    deps = parsed.get("dependencies") or _normalize_dependencies(
        summary.get("dependencies"),
    )
    versions = summary.get("versions") or []
    latest = ""
    if versions and isinstance(versions[0], dict):
        latest = versions[0].get("name") or ""
    tags_raw = summary.get("tags") or []
    tags = [
        t if isinstance(t, str) else str(t.get("name", ""))
        for t in tags_raw
    ]
    tags = [t for t in tags if t]
    short = parsed.get("description") or role.get("description") or ""
    options = []
    for var in parsed.get("variables") or []:
        options.append({
            "name": var["name"],
            "type": var.get("type") or "str",
            "required": bool(var.get("required")),
            "default": var.get("default"),
            "choices": var.get("choices"),
            "description": var.get("description", ""),
            "aliases": var.get("aliases", []),
        })
    metadata = {
        "role_name": role_name,
        "content_type": "standalone_role",
        "short_description": short,
        "entry_points": {
            "main": {"description": short, "options": options},
        },
        "dependencies": deps,
        "examples": parsed.get("examples") or "",
        "tags": tags,
        "latest_version": latest,
        "github_user": role.get("github_user") or "",
        "github_repo": role.get("github_repo") or "",
        "github_branch": role.get("github_branch") or "",
        "download_count": role.get("download_count") or 0,
    }
    has_html = bool(html.strip())
    provenance = {
        "doc_source": "galaxy_v1_readme" if has_html else "galaxy_v1_metadata",
        "doc_version": latest,
    }
    if has_html:
        provenance["doc_warning"] = (
            "Documentation parsed from Galaxy README (best-effort)."
        )
    else:
        provenance["doc_warning"] = (
            "Galaxy stored no README HTML for this standalone role; "
            "returning catalog metadata only."
        )
    if self.server_name:
        provenance["doc_source_server"] = self.server_name
    return metadata, provenance
```

Export `GalaxyV1Client` and `clear_cache` in `__all__`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_galaxy_v1.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy_v1.py tests/test_galaxy_v1.py
git commit -m "$(cat <<'EOF'
feat: add Galaxy v1 client for standalone roles

Search uses keywords plus download-count order; lookup uses namespace
and name so Galaxy download counters are not incremented.

Assisted-by: Cursor (Grok 4.6)
EOF
)"
```

---

### Task 3: Isolation — v1 must not poison v3 discovery

**Files:**
- Modify: `tests/test_galaxy_v1.py`

**Interfaces:**
- Consumes: `GalaxyV1Client`, `GalaxyClient` from Task 2 / existing `galaxy.py`
- Produces: required isolation test

- [ ] **Step 1: Write the failing (or new) isolation test**

Append to `tests/test_galaxy_v1.py`:

```python
class TestV1DoesNotPoisonV3:
    @pytest.mark.asyncio
    async def test_missing_v1_does_not_set_v3_discovery_failed(self):
        from ansible_know.galaxy import GalaxyClient
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.galaxy_v1 import GalaxyV1Client

        config = GalaxyServerConfig(
            name="hub", url="https://hub.example/api",
        )
        v3_only = {"available_versions": {"v3": "v3/"}}
        search_payload = {
            "data": [],
            "meta": {"count": 0},
        }

        def _route(url, **kwargs):
            resp = MagicMock()
            resp.content = b"{}"
            resp.headers = {}
            resp.raise_for_status.return_value = None
            if "collection-versions" in str(url) or "search" in str(url):
                resp.json.return_value = search_payload
            else:
                resp.json.return_value = v3_only
            return resp

        shared = AsyncMock()
        shared.get.side_effect = _route
        v3 = GalaxyClient.from_config(config, http_client=shared)
        v1 = GalaxyV1Client.from_config(config, http_client=shared)
        with pytest.raises(GalaxyError, match="v1"):
            await v1.search_roles("cis")
        assert v3._discovery_failed is False
        assert v3._v3_path is None
        result = await v3.search_collections("net")
        assert result["count"] == 0
        assert v3._discovery_failed is False
        assert v3._v3_path == "v3/"
```

Adjust `_route` if `search_collections` URL matching differs — inspect `GalaxyClient._build_v3_url("plugin", "ansible", "search", "collection-versions")`. Discovery GET hits `config.url` then `{url}/api`. Both should return `v3_only` so v1 fails and v3 succeeds.

- [ ] **Step 2: Run the isolation test**

Run: `pytest tests/test_galaxy_v1.py::TestV1DoesNotPoisonV3 -v`

Expected: PASS after any mock URL tweaks. Must construct **both** clients. Do not “fix” the test by omitting `GalaxyV1Client`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_galaxy_v1.py
git commit -m "$(cat <<'EOF'
test: assert v1 discovery failure does not poison GalaxyClient

Assisted-by: Cursor (Grok 4.6)
EOF
)"
```

---

### Task 4: Resolution — multi-server search and get-doc

**Files:**
- Modify: `src/ansible_know/resolution.py`
- Modify: `tests/test_resolution.py`

**Interfaces:**
- Consumes: `GalaxyV1ClientFactory`, `_select_http_client`
- Produces: `_try_v1_servers`, `search_standalone_roles` (raises `GalaxyError` on all-fail), `resolve_standalone_role_doc` → `GetStandaloneRoleDocResult | ErrorResponse`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resolution.py`. Reuse `GalaxyServerConfig`. Use a tiny fake factory — do **not** patch `GalaxyClient`:

```python
class _FakeV1:
    def __init__(self, search=None, doc=None, error=None):
        self._search = search
        self._doc = doc
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def search_roles(self, query, tags=None):
        if self._error:
            raise self._error
        return self._search

    async def fetch_standalone_role_doc(self, role_name):
        if self._error:
            raise self._error
        return self._doc


def _v1_factory_map(mapping):
    def _factory(config, http_client=None):
        return mapping[config.name]
    return _factory
```

Tests:

```python
class TestSearchStandaloneRoles:
    @pytest.mark.asyncio
    async def test_merges_and_ranks(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_standalone_roles
        s1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        s2 = GalaxyServerConfig(name="hub", url="https://hub.example")
        f1 = _FakeV1(search={"roles": [
            {"role_name": "a.one", "download_count": 10},
        ]})
        f2 = _FakeV1(search={"roles": [
            {"role_name": "b.two", "download_count": 50},
        ]})
        result = await search_standalone_roles(
            "cis", galaxy_servers=[s1, s2],
            v1_client_factory=_v1_factory_map({"galaxy": f1, "hub": f2}),
        )
        assert result["count"] == 2
        assert result["roles"][0]["role_name"] == "b.two"
        assert result["roles"][0]["source"] == "hub"

    @pytest.mark.asyncio
    async def test_dedupes_by_role_name(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_standalone_roles
        s1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        s2 = GalaxyServerConfig(name="hub", url="https://hub.example")
        hit = {"role_name": "a.one", "download_count": 1}
        result = await search_standalone_roles(
            "cis", galaxy_servers=[s1, s2],
            v1_client_factory=_v1_factory_map({
                "galaxy": _FakeV1(search={"roles": [hit]}),
                "hub": _FakeV1(search={"roles": [hit]}),
            }),
        )
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_skips_v1_less_server(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_standalone_roles
        s1 = GalaxyServerConfig(name="hub", url="https://hub.example")
        s2 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        result = await search_standalone_roles(
            "cis", galaxy_servers=[s1, s2],
            v1_client_factory=_v1_factory_map({
                "hub": _FakeV1(error=GalaxyError("does not support Galaxy API v1")),
                "galaxy": _FakeV1(search={"roles": [
                    {"role_name": "a.one", "download_count": 1},
                ]}),
            }),
        )
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_empty_hits_succeed(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_standalone_roles
        s1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        result = await search_standalone_roles(
            "zzzz", galaxy_servers=[s1],
            v1_client_factory=_v1_factory_map({
                "galaxy": _FakeV1(search={"roles": []}),
            }),
        )
        assert result == {"query": "zzzz", "count": 0, "roles": []}

    @pytest.mark.asyncio
    async def test_all_fail_raises(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import search_standalone_roles
        s1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        with pytest.raises(GalaxyError, match="All Galaxy servers failed"):
            await search_standalone_roles(
                "cis", galaxy_servers=[s1],
                v1_client_factory=_v1_factory_map({
                    "galaxy": _FakeV1(error=GalaxyError("timeout")),
                }),
            )


class TestResolveStandaloneRoleDoc:
    @pytest.mark.asyncio
    async def test_first_success_wins(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import resolve_standalone_role_doc
        s1 = GalaxyServerConfig(name="hub", url="https://hub.example")
        s2 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        doc = ({
            "role_name": "ansible-lockdown.rhel9_cis",
            "content_type": "standalone_role",
            "short_description": "CIS",
            "entry_points": {"main": {"description": "CIS", "options": []}},
            "dependencies": [],
            "examples": "",
        }, {"doc_source": "galaxy_v1_readme", "doc_version": "1.0"})
        result = await resolve_standalone_role_doc(
            "ansible-lockdown.rhel9_cis",
            galaxy_servers=[s1, s2],
            v1_client_factory=_v1_factory_map({
                "hub": _FakeV1(error=GalaxyError("does not support Galaxy API v1")),
                "galaxy": _FakeV1(doc=doc),
            }),
        )
        assert result["doc_source"] == "galaxy_v1_readme"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_not_found_is_error_response(self):
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.resolution import resolve_standalone_role_doc
        s1 = GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")
        result = await resolve_standalone_role_doc(
            "missing.role",
            galaxy_servers=[s1],
            v1_client_factory=_v1_factory_map({
                "galaxy": _FakeV1(error=GalaxyError(
                    "Standalone role 'missing.role' not found"
                )),
            }),
        )
        assert result == {"error": "Standalone role 'missing.role' not found"}
        assert "doc_source" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_resolution.py::TestSearchStandaloneRoles tests/test_resolution.py::TestResolveStandaloneRoleDoc -v`

Expected: FAIL — cannot import `search_standalone_roles`

- [ ] **Step 3: Implement resolution**

Update `resolution.py`:

- Add `search_standalone_roles` and `resolve_standalone_role_doc` to `__all__`.
- TYPE_CHECKING imports: `GalaxyV1ClientFactory`, `GetStandaloneRoleDocResult`.
- Duplicate `_try_galaxy_servers` as `_try_v1_servers` with `client_factory: GalaxyV1ClientFactory`. Same body, including `_select_http_client`. Do not change `_try_galaxy_servers`.
- `search_standalone_roles`: copy `search_galaxy_collections` structure (`asyncio.gather`, skip exceptions, merge). Dedupe on `role_name`. Sort by `download_count`. If `not all_roles and errors: raise GalaxyError("All Galaxy servers failed: ...")`. Return `{"query", "count", "roles"}`. Stamp `source` from server name. Factory kwarg name: `v1_client_factory`. If factory is `None`, raise `GalaxyError("No client factory configured for Galaxy search")`.
- `resolve_standalone_role_doc`: if factory is `None`, return `{"error": "No Galaxy client configured for standalone roles"}`. Else `_try_v1_servers` calling `fetch_standalone_role_doc`. Merge provenance onto the metadata dict (`doc_source`, `doc_version`, optional `doc_warning` / `doc_source_server`). On `GalaxyError`, return `{"error": sanitize_error(str(exc))}` — never `doc_source: unavailable`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_resolution.py -v`

Expected: PASS (existing collection/module/role tests plus new class)

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/resolution.py tests/test_resolution.py
git commit -m "$(cat <<'EOF'
feat: resolve standalone Galaxy roles across configured servers

Search merges like collections and raises when every v1 server fails;
get-doc uses a dedicated first-success loop so GalaxyDocClient stays v3.

Assisted-by: Cursor (Grok 4.6)
EOF
)"
```

---

### Task 5: MCP tools, factory, and cache-clear coupling

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `validate_standalone_role_name`, `search_standalone_roles`, `resolve_standalone_role_doc`, `GalaxyV1Client.from_config`
- Produces: tools `search_standalone_roles`, `get_standalone_role_doc`; `_galaxy_v1_factory`; `clear_cache` also calls `galaxy_v1.clear_cache`

- [ ] **Step 1: Write the failing tool tests**

In `tests/test_server.py`, add (mirror `TestSearchCollectionsTool`):

```python
class TestSearchStandaloneRolesTool:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        mock_result = {
            "query": "rhel9_cis",
            "count": 1,
            "roles": [{"role_name": "ansible-lockdown.rhel9_cis", "download_count": 9}],
        }
        with patch(
            "ansible_know.resolution.search_standalone_roles",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            from ansible_know.server import search_standalone_roles
            result = await search_standalone_roles("rhel9_cis")
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_rejects_empty_query(self):
        from ansible_know.server import search_standalone_roles
        result = await search_standalone_roles("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_galaxy_error(self):
        with patch(
            "ansible_know.resolution.search_standalone_roles",
            new_callable=AsyncMock,
            side_effect=GalaxyError("timeout"),
        ):
            from ansible_know.server import search_standalone_roles
            result = await search_standalone_roles("cis")
        assert "error" in result


class TestGetStandaloneRoleDocTool:
    @pytest.mark.asyncio
    async def test_returns_doc(self):
        mock_result = {
            "role_name": "ansible-lockdown.rhel9_cis",
            "content_type": "standalone_role",
            "doc_source": "galaxy_v1_readme",
            "entry_points": {"main": {"description": "", "options": []}},
        }
        with patch(
            "ansible_know.resolution.resolve_standalone_role_doc",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            from ansible_know.server import get_standalone_role_doc
            result = await get_standalone_role_doc("ansible-lockdown.rhel9_cis")
        assert result["content_type"] == "standalone_role"

    @pytest.mark.asyncio
    async def test_rejects_three_part_fqcn(self):
        from ansible_know.server import get_standalone_role_doc
        result = await get_standalone_role_doc(
            "fedora.linux_system_roles.timesync",
        )
        assert "error" in result
        assert "get_role_doc" in result["error"]

    @pytest.mark.asyncio
    async def test_get_role_doc_still_requires_three_part(self):
        from ansible_know.server import get_role_doc
        result = await get_role_doc("ansible-lockdown.rhel9_cis")
        assert "error" in result
```

Update `TestClearCache.test_clear_all` and `test_clear_galaxy_only` to also patch `ansible_know.galaxy_v1.clear_cache` and expect `"galaxy_v1"` in `cleared`. `test_clear_docs_only` must **not** call v1 clear.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_server.py::TestSearchStandaloneRolesTool tests/test_server.py::TestGetStandaloneRoleDocTool tests/test_server.py::TestClearCache -v`

Expected: FAIL — cannot import the new tools / `cleared` list mismatch

- [ ] **Step 3: Wire `server.py`**

1. Module docstring: **22 tools**.
2. Import `GetStandaloneRoleDocResult`, `StandaloneRoleSearchResult`, `validate_standalone_role_name`.
3. FastMCP `instructions`: after collection-role workflow, add standalone search/get-doc (2-part `namespace.role`). Collection roles still `search_collections` / `get_role_doc`.
4. Add `_galaxy_v1_factory(ctx)`:

```python
def _galaxy_v1_factory(ctx: Context | None = None):
    from ansible_know.galaxy_v1 import GalaxyV1Client

    def _factory(config, http_client=None):
        return GalaxyV1Client.from_config(config, http_client=http_client)

    return _factory
```

No enrichment semaphore.

5. Tools (place after `search_collections` / `get_role_doc` as appropriate), `readOnlyHint=True`:

`search_standalone_roles(query, tags=None, ctx=None) -> StandaloneRoleSearchResult | ErrorResponse`

- Docstring: Galaxy **standalone/legacy** roles; `keywords` search; tags is a **single** Galaxy tag (first comma-separated segment is sent). Collection roles → `search_collections`. If public Galaxy is disabled and no server speaks v1, expect a v1-unsupported error.
- `validate_query`; `validate_tags` when set; call `resolution.search_standalone_roles(..., v1_client_factory=_galaxy_v1_factory(ctx), http_client=..., galaxy_servers=state.galaxy_servers)`. Catch `Exception` → `{"error": sanitize_error(...)}`.

`get_standalone_role_doc(role_name, ctx=None) -> GetStandaloneRoleDocResult | ErrorResponse`

- Docstring: 2-part identifier from search; not `get_role_doc`.
- `validate_standalone_role_name`; call `resolution.resolve_standalone_role_doc(..., v1_client_factory=_galaxy_v1_factory(ctx), ...)`.
- Do **not** call `truncate_response` on the dict.

6. `clear_cache`: when `scope in (None, "galaxy")`, also:

```python
from ansible_know import galaxy_v1
galaxy_v1.clear_cache()
cleared.append("galaxy_v1")
```

Do not import `galaxy_v1` from `galaxy.py`. Update the tool docstring: Galaxy scope includes standalone-role (v1) cache.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_server.py::TestSearchStandaloneRolesTool tests/test_server.py::TestGetStandaloneRoleDocTool tests/test_server.py::TestClearCache tests/test_server.py::TestSearchCollectionsTool -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "$(cat <<'EOF'
feat: expose standalone Galaxy role search and docs as MCP tools

Keep collection get_role_doc on 3-part FQCNs; clear the v1 cache from
the existing galaxy cache scope without coupling galaxy.py.

Assisted-by: Cursor (Grok 4.6)
EOF
)"
```

---

### Task 6: Docs, contracts, optional live tests

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `docs/architecture/service-contracts.md`
- Modify: `tests/integration/test_galaxy_api.py`

**Interfaces:**
- Consumes: implemented tools from Task 5
- Produces: accurate tool counts and layer map

- [ ] **Step 1: Update docs**

`CLAUDE.md`: architecture tree add `galaxy_v1.py`; FastMCP line **22 tools**; tool table two rows (read-only).

`README.md` Discovery table, after `get_role_doc`:

| `search_standalone_roles(query, tags?)` | Search Galaxy standalone (legacy v1) roles by keyword |
| `get_standalone_role_doc(role_name)` | Structured docs for a 2-part `namespace.role` from Galaxy README HTML |

Also add those names to the ASCII “What It Does” tool list.

`docs/architecture/service-contracts.md`:

- Layer map: `src/ansible_know/galaxy_v1.py` → **External Access**
- External Access table: `galaxy_v1.py` (`GalaxyV1Client`) consumers = `resolution.py`; `readme_parser.py` consumers = `galaxy.py`, `galaxy_v1.py`
- Orchestration → Domain: add `search_standalone_roles()`, `resolve_standalone_role_doc()`
- “20 tool handlers” → **22**

- [ ] **Step 2: Optional integration tests**

Append to `tests/integration/test_galaxy_api.py`:

```python
class TestRealGalaxyAPIV1Roles:
    @pytest.mark.asyncio
    async def test_search_standalone_roles_rhel9_cis(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        async with GalaxyV1Client() as client:
            result = await client.search_roles("rhel9_cis")
        assert result["count"] >= 1
        names = [r["role_name"] for r in result["roles"]]
        assert any("rhel9_cis" in n for n in names)

    @pytest.mark.asyncio
    async def test_fetch_ansible_lockdown_rhel9_cis(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        async with GalaxyV1Client() as client:
            meta, prov = await client.fetch_standalone_role_doc(
                "ansible-lockdown.rhel9_cis",
            )
        assert meta["role_name"] == "ansible-lockdown.rhel9_cis"
        assert meta["content_type"] == "standalone_role"
        assert prov["doc_source"] in ("galaxy_v1_readme", "galaxy_v1_metadata")
```

- [ ] **Step 3: Run unit suite + lint**

Run:

```bash
pytest tests/ -v
ruff check src/ansible_know/galaxy_v1.py src/ansible_know/validation.py src/ansible_know/types.py src/ansible_know/resolution.py src/ansible_know/server.py tests/test_galaxy_v1.py tests/test_validation.py tests/test_resolution.py tests/test_server.py
```

Expected: all unit tests PASS; ruff clean. Do not require `--run-integration` for merge.

Confirm `galaxy.py` has no `galaxy_v1` import: `rg galaxy_v1 src/ansible_know/galaxy.py` → no matches.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md docs/architecture/service-contracts.md tests/integration/test_galaxy_api.py
git commit -m "$(cat <<'EOF'
docs: document standalone Galaxy role tools and v1 layer

Assisted-by: Cursor (Grok 4.6)
EOF
)"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| `search_standalone_roles` / `get_standalone_role_doc` | 5 |
| `GalaxyV1Client` transport-only share | 2 |
| `keywords` + `order_by=-download_count` + `page_size=10` | 2 |
| `namespace`+`name` lookup, never `owner__username`+`name` | 2 |
| `/content/` + `parse_role_readme` | 2 |
| `{username}.{name}` identity | 2 |
| New hyphen validator | 1 |
| `content_type: standalone_role` | 2, 5 |
| No local ansible-doc / GitHub / skills / install | Global + no tasks |
| DRF `results` list | 2 |
| First-segment `tags` | 2 |
| Empty HTML → `galaxy_v1_metadata` | 2 |
| Search all-fail raises; empty hits succeed | 4 |
| `_try_v1_servers`, not `GalaxyDocClient` | 4 |
| `clear_cache` in server.py, not `galaxy.py` | 5 |
| Isolation: v1 must not set `GalaxyClient._discovery_failed` | 3 |
| `_select_http_client` | 4 |
| CLAUDE.md / README.md / service-contracts 20→22 | 6 |
| `galaxy.py` unchanged (no v1 import) | 6 verify |
| Follow-ups not implemented | Global |

No TBD/TODO placeholders. Type names (`role_name`, `GalaxyV1ClientFactory`, `GetStandaloneRoleDocResult`) are consistent across tasks.
