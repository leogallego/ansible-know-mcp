# Galaxy API Root Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Galaxy client to work with Automation Hub / PAH by adding API root discovery, redirect following, SSO token exchange, and correct URL construction.

**Architecture:** Four fixes applied incrementally: (1) enable `follow_redirects=True` on httpx so standard v3 paths that redirect to Pulp paths work transparently, (2) add SSO token exchange with retry-on-401 for servers with `auth_url` configured, (3) add lazy API root discovery matching ansible-galaxy's `g_connect` pattern — probes the configured URL for `available_versions`, falls back to appending `/api/` if needed, then builds all URLs from the discovered root + v3 path prefix, (4) replace hardcoded paths with dynamic URL building + fix existing tests. Collection endpoints use standard Galaxy v3 paths (`v3/collections/{ns}/{name}/`) and rely on redirects. The search endpoint uses the Pulp-specific path (`v3/plugin/ansible/search/collection-versions/`) since no redirect exists for it.

**Tech Stack:** Python, httpx, pytest, asyncio

**Issue:** https://github.com/leogallego/ansible-know-mcp/issues/154

## Global Constraints

- Python 3.10+ (project minimum)
- All tests mock `_api_get` or httpx — no real network calls in unit tests
- Integration tests in `tests/integration/` for real server validation
- `ruff check` must pass
- Existing test suite must remain green

## Verified API Behavior (from live testing)

All Galaxy-compatible servers return `available_versions: {"v3": "v3/"}` at their API root.

**Collection endpoints** (`{api_root}/v3/collections/{ns}/{name}/`):
- Both public Galaxy and AH return **302 redirect** to the Pulp-specific path
- Following the redirect returns 200 — this is how ansible-galaxy works

**Search endpoint** (`search/collection-versions/`):
- `{api_root}/v3/search/collection-versions/` → **404** on all servers (no redirect)
- `{api_root}/v3/plugin/ansible/search/collection-versions/` → **200** on all servers
- Search MUST use the Pulp-specific path

**Auth for AH:**
- AH requires SSO token exchange: offline token → `auth_url` → access token → `Bearer` header
- Current code sends `Token {token}` which returns 401

---

### Task 1: Enable redirect following on httpx clients

**Files:**
- Modify: `src/ansible_know/galaxy.py:121-130` (`_get_client` method)
- Modify: `src/ansible_know/server.py:123-126` (shared lifespan httpx client)
- Test: `tests/test_galaxy.py`

**Interfaces:**
- Consumes: nothing new
- Produces: all httpx clients (both owned and shared/injected) use `follow_redirects=True`

**IMPORTANT:** There are TWO httpx clients that need this fix:
1. `galaxy.py:_get_client()` — the owned client created when no client is injected
2. `server.py:app_lifespan()` — the shared client injected into all Galaxy operations via `_get_http_client(ctx)` → `resolution._select_http_client()` → `GalaxyClient(http_client=client)`

If only the owned client gets `follow_redirects`, the shared client (used in ALL production MCP tool calls) will still fail on redirects.

**Note:** httpx's `follow_redirects` parameter is typed `bool`. Use `True`, not `"safe"` — httpx has no safe-redirect mode. Since the Galaxy client only uses GET in `_api_get`, `True` is correct (POST is only used in SSO exchange which bypasses `_api_get`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_galaxy.py`:

```python
class TestRedirectFollowing:
    def test_owned_client_follows_redirects(self):
        mock_client = AsyncMock()
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client) as mock_ctor:
            gc = GalaxyClient()
            gc._get_client()
        mock_ctor.assert_called_once()
        assert mock_ctor.call_args[1]["follow_redirects"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestRedirectFollowing -v`
Expected: FAIL — `follow_redirects` not in call kwargs

- [ ] **Step 3: Add `follow_redirects=True` to both clients**

In `src/ansible_know/galaxy.py`, modify `_get_client`:

```python
def _get_client(self) -> httpx.AsyncClient:
    """Get the http client to use for requests."""
    if self._http_client is not None:
        return self._http_client
    if self._owned_client is None:
        self._owned_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=120.0),
            verify=self._verify,
            follow_redirects=True,
        )
    return self._owned_client
```

In `src/ansible_know/server.py`, modify the shared client in `app_lifespan`:

```python
async with httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, read=120.0),
    verify=True,
    follow_redirects=True,
) as client:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestRedirectFollowing -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/galaxy.py src/ansible_know/server.py tests/test_galaxy.py
git commit -m "fix: enable safe redirect following on all httpx clients

Fixes secondary issue from #154 — httpx defaults to not following
redirects, causing HTTP 302 errors from Automation Hub to surface
as GalaxyError instead of being followed.

Applies to BOTH the GalaxyClient's owned client AND the shared
lifespan client in server.py (which is injected into all Galaxy
operations via _get_http_client).

Galaxy NG redirects standard v3/collections/ paths to the Pulp-
specific /v3/plugin/ansible/content/.../collections/index/ paths.
ansible-galaxy follows these redirects automatically; we must too.

Part of #154"
```

---

### Task 2: Add SSO token exchange for Automation Hub auth

**Files:**
- Modify: `src/ansible_know/galaxy.py` (add `_resolve_auth_headers` method, modify `_auth_headers`)
- Modify: `src/ansible_know/galaxy.py` (pass `auth_url` through from config)
- Test: `tests/test_galaxy.py`

**Interfaces:**
- Consumes: `GalaxyServerConfig.auth_url`, `GalaxyServerConfig.token`
- Produces:
  - `async _resolve_auth_headers(self) -> dict[str, str]` — exchanges offline token via SSO if `auth_url` is set, caches the access token, returns auth headers
  - `_api_get` uses resolved auth headers instead of static `_auth_headers()`

When `auth_url` is set (AH/PAH), the configured `token` is an offline/refresh token that must be exchanged via the SSO endpoint for a short-lived access token. The exchange is: `POST auth_url` with `grant_type=refresh_token&client_id={client_id}&refresh_token={token}` → response contains `access_token` used as `Bearer {access_token}`.

`client_id` comes from `GalaxyServerConfig.client_id` (already parsed from ansible.cfg) with fallback to `"cloud-services"` (the default for console.redhat.com). On-premise PAH instances may use a different client_id.

**SSO token expiry handling:** AH access tokens expire (typically 5-15 minutes). For long-running MCP sessions, `_api_get` catches 401 responses when `auth_url` is set, clears the cached `_access_token`, re-exchanges via SSO, and retries the request once. This matches ansible-galaxy's retry-on-401 pattern.

- [ ] **Step 1: Write failing tests for SSO token exchange**

Add to `tests/test_galaxy.py`:

```python
class TestSsoTokenExchange:
    @pytest.mark.asyncio
    async def test_exchanges_offline_token_for_bearer(self):
        """When auth_url is set, token is exchanged via SSO."""
        sso_response = MagicMock()
        sso_response.json.return_value = {"access_token": "sso_access_123"}
        sso_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)

        versions_resp = MagicMock()
        versions_resp.json.return_value = SAMPLE_VERSIONS_RESPONSE
        versions_resp.raise_for_status.return_value = None
        versions_resp.content = b"{}"
        versions_resp.headers = {}
        mock_client.get = AsyncMock(return_value=versions_resp)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="offline_refresh_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        await gc.latest_version("redhat", "insights")

        mock_client.post.assert_called_once()
        post_call = mock_client.post.call_args
        assert "sso.example.com" in post_call[0][0]

        get_call = mock_client.get.call_args
        headers = get_call[1]["headers"]
        assert headers["Authorization"] == "Bearer sso_access_123"

    @pytest.mark.asyncio
    async def test_no_exchange_without_auth_url(self):
        """Without auth_url, uses Token auth as before."""
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        gc = GalaxyClient(
            http_client=mock_client,
            token="plain_token",
        )
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"

        await gc.latest_version("netbox", "netbox")
        headers = mock_client.get.call_args[1]["headers"]
        assert headers["Authorization"] == "Token plain_token"

    @pytest.mark.asyncio
    async def test_caches_access_token(self):
        """SSO exchange only happens once, cached token reused."""
        sso_response = MagicMock()
        sso_response.json.return_value = {"access_token": "cached_token"}
        sso_response.raise_for_status.return_value = None

        versions_resp = MagicMock()
        versions_resp.json.return_value = SAMPLE_VERSIONS_RESPONSE
        versions_resp.raise_for_status.return_value = None
        versions_resp.content = b"{}"
        versions_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)
        mock_client.get = AsyncMock(return_value=versions_resp)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="offline_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        await gc.latest_version("ns1", "col1")
        await gc.latest_version("ns2", "col2")

        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_sso_failure_raises_galaxy_error(self):
        """SSO exchange failure surfaces as GalaxyError."""
        sso_response = MagicMock()
        sso_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401),
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="bad_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        with pytest.raises(GalaxyError, match="SSO.*token"):
            await gc.latest_version("redhat", "insights")

    @pytest.mark.asyncio
    async def test_retries_on_401_with_fresh_token(self):
        """When API returns 401 with auth_url, re-exchanges and retries once."""
        sso_response = MagicMock()
        sso_response.raise_for_status.return_value = None

        # Re-exchange returns a DIFFERENT token than the expired one
        sso_response.json.return_value = {"access_token": "fresh_token_v2"}

        # First API call returns 401, second (after re-exchange) returns 200
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=resp_401,
        )
        resp_401.content = b""

        resp_200 = MagicMock()
        resp_200.json.return_value = SAMPLE_VERSIONS_RESPONSE
        resp_200.raise_for_status.return_value = None
        resp_200.content = b"{}"
        resp_200.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=sso_response)
        mock_client.get = AsyncMock(side_effect=[resp_401, resp_200])

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="offline_token",
            auth_url="https://sso.example.com/token",
        )
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        # Pre-set an expired token to simulate expiry
        gc._access_token = "expired_token_v1"

        version = await gc.latest_version("redhat", "insights")
        assert version == "3.23.0"
        assert mock_client.post.call_count == 1  # re-exchange happened
        assert mock_client.get.call_count == 2   # original + retry

        # Verify the retry used the fresh token, not the expired one
        retry_headers = mock_client.get.call_args_list[1][1]["headers"]
        assert retry_headers["Authorization"] == "Bearer fresh_token_v2"

    @pytest.mark.asyncio
    async def test_no_retry_on_401_without_auth_url(self):
        """Without auth_url, 401 raises immediately (no SSO to retry)."""
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=resp_401,
        )
        resp_401.content = b""

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp_401)

        gc = GalaxyClient(
            http_client=mock_client,
            token="plain_token",
        )
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"

        with pytest.raises(GalaxyError, match="Galaxy API error"):
            await gc.latest_version("netbox", "netbox")
        assert mock_client.get.call_count == 1  # no retry
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestSsoTokenExchange -v`
Expected: FAIL — `auth_url` parameter doesn't exist on GalaxyClient

- [ ] **Step 3: Implement SSO token exchange**

In `src/ansible_know/galaxy.py`:

Add `auth_url` and `client_id` parameters to `__init__` and `from_config`:

```python
def __init__(
    self,
    base_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    token: str | None = None,
    username: str | None = None,
    password: str | None = None,
    verify: bool = True,
    server_name: str | None = None,
    enrichment_semaphore: asyncio.Semaphore | None = None,
    auth_url: str | None = None,
    client_id: str | None = None,
):
    # ... existing init ...
    self._auth_url = auth_url
    self._client_id = client_id
    self._access_token: str | None = None
```

Update `from_config`:

```python
@classmethod
def from_config(cls, config, ...) -> GalaxyClient:
    return cls(
        # ... existing params ...
        auth_url=config.auth_url,
        client_id=config.client_id,
    )
```

Add SSO exchange method:

```python
async def _ensure_access_token(self) -> str:
    """Exchange offline token for access token via SSO, with caching."""
    if self._access_token is not None:
        return self._access_token

    client = self._get_client()
    try:
        resp = await client.post(
            self._auth_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id or "cloud-services",
                "refresh_token": self._token,
            },
            timeout=TIMEOUT_FAST,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as exc:
        raise GalaxyError(
            f"SSO token exchange failed at {self._auth_url}"
        ) from exc
```

Modify `_auth_headers` to be async and handle SSO:

```python
async def _resolve_auth_headers(self) -> dict[str, str]:
    """Build authentication headers, exchanging SSO token if needed."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if self._token and self._auth_url:
        access_token = await self._ensure_access_token()
        headers["Authorization"] = f"Bearer {access_token}"
    elif self._token:
        headers["Authorization"] = f"Token {self._token}"
    return headers
```

Update `_api_get` to use `await self._resolve_auth_headers()` instead of `self._auth_headers()`, and add retry-on-401 for SSO token expiry:

```python
async def _api_get(
    self,
    path: str,
    params: dict[str, str] | None = None,
    timeout: httpx.Timeout = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    url = f"{self._base}{path}"
    client = self._get_client()
    kwargs: dict[str, Any] = {
        "params": params,
        "headers": await self._resolve_auth_headers(),
        "timeout": timeout,
    }
    if not self._token and self._username and self._password:
        kwargs["auth"] = httpx.BasicAuth(self._username, self._password)
    resp = await client.get(url, **kwargs)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401 and self._auth_url:
            self._access_token = None
            kwargs["headers"] = await self._resolve_auth_headers()
            resp = await client.get(url, **kwargs)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as retry_exc:
                raise GalaxyError(
                    f"Galaxy API error (HTTP {retry_exc.response.status_code})"
                ) from retry_exc
        else:
            raise GalaxyError(
                f"Galaxy API error (HTTP {exc.response.status_code})"
            ) from exc
    content_length = resp.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_GALAXY_RESPONSE_SIZE:
                raise GalaxyError("Galaxy API response too large")
        except (ValueError, OverflowError):
            pass
    if len(resp.content) > MAX_GALAXY_RESPONSE_SIZE:
        raise GalaxyError("Galaxy API response too large")
    return resp.json()
```

The sync `_auth_headers` method is no longer called after this change but can be left for now (harmless).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestSsoTokenExchange -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: All pass (existing tests don't set auth_url, so behavior unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "feat: add SSO token exchange for Automation Hub auth

When auth_url is configured (AH/PAH), the token is an offline/refresh
token that must be exchanged via the SSO endpoint for a short-lived
access token sent as Bearer auth. On 401 responses, clears the cached
access token, re-exchanges via SSO, and retries once (handles token
expiry in long-running MCP sessions).

Without auth_url, behavior is unchanged (Token auth).

Part of #154"
```

---

### Task 3: Add API root discovery

**Files:**
- Modify: `src/ansible_know/galaxy.py` (`GalaxyClient.__init__`, new `_discover_api_root`, new `_build_v3_url`)
- Test: `tests/test_galaxy.py`

**Interfaces:**
- Consumes: `self._base` (configured URL), `self._resolve_auth_headers()` from Task 2
- Produces:
  - `self._api_root: str | None` — the discovered API root URL
  - `self._v3_path: str | None` — v3 path prefix from `available_versions` (always `"v3/"` in practice)
  - `async _discover_api_root(self) -> None` — lazy discovery, called once
  - `_build_v3_url(self, *segments) -> str` — joins api_root + v3_path + segments into a full URL

- [ ] **Step 1: Write failing tests for API root discovery**

Add to `tests/test_galaxy.py`:

```python
class TestApiRootDiscovery:
    @pytest.mark.asyncio
    async def test_discovers_v3_from_base_url(self):
        """Base URL returns available_versions directly (AH pattern)."""
        discovery_response = {
            "available_versions": {"v3": "v3/", "pulp-v3": "pulp/api/v3/"},
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = discovery_response
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = b"{}"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        gc = GalaxyClient(
            base_url="https://console.redhat.com/api/automation-hub/content/published",
            http_client=mock_client,
        )
        await gc._discover_api_root()

        assert gc._api_root == "https://console.redhat.com/api/automation-hub/content/published"
        assert gc._v3_path == "v3/"

    @pytest.mark.asyncio
    async def test_falls_back_to_api_suffix(self):
        """When base URL fails, tries appending /api/ (public Galaxy pattern)."""
        not_api_resp = MagicMock()
        not_api_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404),
        )
        not_api_resp.content = b""

        api_resp = MagicMock()
        api_resp.json.return_value = {"available_versions": {"v3": "v3/"}}
        api_resp.raise_for_status.return_value = None
        api_resp.content = b"{}"
        api_resp.headers = {}

        call_urls = []
        mock_client = AsyncMock()
        async def mock_get(url, **kwargs):
            call_urls.append(url)
            if url.endswith("/api"):
                return api_resp
            return not_api_resp
        mock_client.get = mock_get

        gc = GalaxyClient(
            base_url="https://galaxy.ansible.com",
            http_client=mock_client,
        )
        await gc._discover_api_root()

        assert gc._api_root == "https://galaxy.ansible.com/api"
        assert gc._v3_path == "v3/"

    @pytest.mark.asyncio
    async def test_discovery_runs_only_once(self):
        """Concurrent calls don't trigger multiple discoveries."""
        discovery_response = {"available_versions": {"v3": "v3/"}}

        mock_resp = MagicMock()
        mock_resp.json.return_value = discovery_response
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = b"{}"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
        )
        await gc._discover_api_root()
        await gc._discover_api_root()

        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_no_v3(self):
        """Error if server only supports v1."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"available_versions": {"v1": "v1/"}}
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = b"{}"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        gc = GalaxyClient(
            base_url="https://old-galaxy.example.com",
            http_client=mock_client,
        )
        with pytest.raises(GalaxyError, match="v3"):
            await gc._discover_api_root()

    @pytest.mark.asyncio
    async def test_raises_when_no_available_versions(self):
        """Error when neither base URL nor /api/ returns available_versions."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"some_other_key": "value"}
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = b"{}"
        mock_resp.headers = {}

        fail_resp = MagicMock()
        fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404),
        )
        fail_resp.content = b""

        call_count = 0
        mock_client = AsyncMock()
        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_resp
            return fail_resp
        mock_client.get = mock_get

        gc = GalaxyClient(
            base_url="https://not-galaxy.example.com",
            http_client=mock_client,
        )
        with pytest.raises(GalaxyError, match="available_versions"):
            await gc._discover_api_root()

    @pytest.mark.asyncio
    async def test_raises_auth_error_on_401(self):
        """Error message mentions authentication when server returns 401."""
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401),
        )
        resp_401.content = b""

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp_401)

        gc = GalaxyClient(
            base_url="https://hub.example.com",
            http_client=mock_client,
            token="bad_token",
        )
        with pytest.raises(GalaxyError, match="authentication failed"):
            await gc._discover_api_root()


class TestBuildV3Url:
    def test_standard_collection_url(self):
        """Builds correct URL for collection endpoint."""
        gc = GalaxyClient(base_url="https://galaxy.ansible.com")
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"
        url = gc._build_v3_url("collections", "netbox", "netbox", "versions")
        assert url == "https://galaxy.ansible.com/api/v3/collections/netbox/netbox/versions/"

    def test_ah_url_with_long_base(self):
        """AH base URL with path segments builds correct URL."""
        gc = GalaxyClient(base_url="https://console.redhat.com/api/automation-hub/content/published")
        gc._api_root = "https://console.redhat.com/api/automation-hub/content/published"
        gc._v3_path = "v3/"
        url = gc._build_v3_url("collections", "redhat", "insights")
        assert url == "https://console.redhat.com/api/automation-hub/content/published/v3/collections/redhat/insights/"

    def test_always_trailing_slash(self):
        """All URLs end with trailing slash (Galaxy API requirement)."""
        gc = GalaxyClient(base_url="https://hub.example.com")
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"
        url = gc._build_v3_url("collections", "ns", "col")
        assert url.endswith("/")

    def test_strips_extra_slashes(self):
        """Handles api_root or v3_path with trailing slashes."""
        gc = GalaxyClient(base_url="https://hub.example.com/")
        gc._api_root = "https://hub.example.com/"
        gc._v3_path = "v3/"
        url = gc._build_v3_url("collections", "ns", "col")
        assert "///" not in url
        assert url == "https://hub.example.com/v3/collections/ns/col/"

    def test_search_pulp_specific_path(self):
        """Pulp-specific search path builds correctly."""
        gc = GalaxyClient(base_url="https://galaxy.ansible.com")
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"
        url = gc._build_v3_url("plugin", "ansible", "search", "collection-versions")
        assert url == "https://galaxy.ansible.com/api/v3/plugin/ansible/search/collection-versions/"

    def test_fallback_to_base_without_discovery(self):
        """Before discovery, falls back to self._base (may be wrong — caller must discover first)."""
        gc = GalaxyClient(base_url="https://galaxy.ansible.com")
        # _api_root and _v3_path are None (pre-discovery)
        url = gc._build_v3_url("collections", "ns", "col")
        # Missing /api/v3/ — this is expected; callers MUST run _discover_api_root() first
        assert url == "https://galaxy.ansible.com/collections/ns/col/"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestApiRootDiscovery tests/test_galaxy.py::TestBuildV3Url -v`
Expected: FAIL — `_discover_api_root`, `_api_root`, `_v3_path`, `_build_v3_url` don't exist

- [ ] **Step 3: Implement API root discovery and URL builder**

In `src/ansible_know/galaxy.py`, add to `__init__`:

```python
self._api_root: str | None = None
self._v3_path: str | None = None
self._discovery_lock = asyncio.Lock()
```

Add the discovery method:

```python
async def _discover_api_root(self) -> None:
    """Discover API root and v3 path, matching ansible-galaxy's g_connect."""
    if self._api_root is not None:
        return

    async with self._discovery_lock:
        if self._api_root is not None:
            return

        client = self._get_client()
        headers = await self._resolve_auth_headers()
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": TIMEOUT_FAST,
        }
        if not self._token and self._username and self._password:
            kwargs["auth"] = httpx.BasicAuth(self._username, self._password)

        n_url = self._base
        data: dict[str, Any] | None = None
        got_auth_error = False

        try:
            resp = await client.get(n_url, **kwargs)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.debug("Discovery probe %s returned HTTP %s", n_url, exc.response.status_code)
            if exc.response.status_code in (401, 403):
                got_auth_error = True
        except (httpx.RequestError, ValueError):
            pass

        if data is None or "available_versions" not in data:
            if not n_url.rstrip("/").endswith("/api"):
                n_url = n_url.rstrip("/") + "/api"
                try:
                    resp = await client.get(n_url, **kwargs)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    logger.debug("Discovery probe %s returned HTTP %s", n_url, exc.response.status_code)
                    if exc.response.status_code in (401, 403):
                        got_auth_error = True
                    data = None
                except (httpx.RequestError, ValueError):
                    data = None

        if data is None or "available_versions" not in data:
            if got_auth_error:
                raise GalaxyError(
                    f"Galaxy API root discovery failed at {self._base} — "
                    f"authentication failed (HTTP 401/403). "
                    f"Check token/credentials in ansible.cfg."
                )
            raise GalaxyError(
                f"Could not discover Galaxy API root at {self._base} — "
                f"no 'available_versions' found. "
                f"Verify the server URL in ansible.cfg."
            )

        versions = data["available_versions"]
        if "v3" not in versions:
            raise GalaxyError(
                f"Galaxy server {self.server_name or self._base} does not "
                f"support API v3 (available: {', '.join(versions.keys())}). "
                f"Only v3 servers are supported."
            )

        self._api_root = n_url.rstrip("/")
        self._v3_path = versions["v3"]
        logger.info(
            "Discovered Galaxy API root: %s (v3 path: %s)",
            self._api_root, self._v3_path,
        )
```

Add the URL builder:

```python
def _build_v3_url(self, *segments: str) -> str:
    """Build a v3 API URL from path segments using the discovered root."""
    parts = [self._api_root or self._base, self._v3_path or ""]
    parts.extend(segments)
    return "/".join(
        p.strip("/") for p in parts if p and p.strip("/")
    ) + "/"
```

- [ ] **Step 4: Run new discovery tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestApiRootDiscovery tests/test_galaxy.py::TestBuildV3Url -v`
Expected: PASS (new tests only — existing tests may fail until Task 4 updates them)

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "feat: add Galaxy API root discovery

Implements lazy API root discovery matching ansible-galaxy's g_connect
pattern. On first API call, probes the configured URL for
available_versions, falls back to appending /api/ if needed.

Stores the discovered v3 path prefix and uses _build_v3_url() to
construct URLs relative to the discovered root.

Part of #154"
```

---

### Task 4: Replace hardcoded paths with dynamic URL building

**Files:**
- Modify: `src/ansible_know/galaxy.py` (all methods that build API paths)
- Test: `tests/test_galaxy.py`

**Interfaces:**
- Consumes: `_build_v3_url(*segments)` and `_discover_api_root()` from Task 3
- Produces: All existing public methods now use dynamic URL building

Methods to change and their new URL construction:

| Method | New URL construction |
|--------|---------------------|
| `_api_get` | Accept full URLs (starts with `http`), trigger `_discover_api_root()` in `_safe_api_get` |
| `latest_version` | `_build_v3_url("collections", namespace, name, "versions")` |
| `_get_collection_detail` | `_build_v3_url("collections", namespace, name)` |
| `search_collections` | `_build_v3_url("plugin", "ansible", "search", "collection-versions")` (Pulp-specific, no redirect exists) |
| `_fetch_docs_blob` | `_build_v3_url("collections", namespace, name, "versions", version, "docs-blob")` |

**Important:** Collection paths use standard v3 API paths (`collections/{ns}/{name}/`) — NOT `collections/index/{ns}/{name}/`. The server redirects to the Pulp-specific path with `index/`, and `follow_redirects=True` (Task 1) handles it. Search uses the Pulp-specific path directly because no redirect exists for it.

- [ ] **Step 1: Write tests verifying URL construction uses discovery**

Add to `tests/test_galaxy.py`:

```python
class TestDynamicUrlConstruction:
    @pytest.mark.asyncio
    async def test_latest_version_uses_discovered_root(self):
        """URL is built from discovered root, not hardcoded."""
        call_urls = []

        original_api_get = GalaxyClient._api_get

        async def tracking_api_get(self_client, path, params=None, timeout=None):
            call_urls.append(path)
            return SAMPLE_VERSIONS_RESPONSE

        gc = GalaxyClient(base_url="https://hub.example.com/api/galaxy/content/published")
        gc._api_root = "https://hub.example.com/api/galaxy/content/published"
        gc._v3_path = "v3/"

        with patch.object(GalaxyClient, "_api_get", tracking_api_get):
            await gc.latest_version("netbox", "netbox")

        url = call_urls[0]
        assert "/api/v3/plugin/ansible/content/published/" not in url
        assert "hub.example.com" in url
        assert "v3/collections/netbox/netbox/versions/" in url

    @pytest.mark.asyncio
    async def test_search_uses_pulp_specific_path(self):
        """Search endpoint uses Pulp-specific path (no redirect exists for it)."""
        call_urls = []

        async def tracking_api_get(self_client, path, params=None, timeout=None):
            call_urls.append(path)
            return {"meta": {"count": 0}, "links": {}, "data": []}

        gc = GalaxyClient(base_url="https://hub.example.com")
        gc._api_root = "https://hub.example.com"
        gc._v3_path = "v3/"

        with patch.object(GalaxyClient, "_api_get", tracking_api_get):
            await gc.search_collections("test")

        url = call_urls[0]
        assert "plugin/ansible/search/collection-versions" in url

    @pytest.mark.asyncio
    async def test_public_galaxy_url_construction(self):
        """Public Galaxy with discovered /api/ root builds correct URLs."""
        call_urls = []

        async def tracking_api_get(self_client, path, params=None, timeout=None):
            call_urls.append(path)
            return SAMPLE_VERSIONS_RESPONSE

        gc = GalaxyClient(base_url="https://galaxy.ansible.com")
        gc._api_root = "https://galaxy.ansible.com/api"
        gc._v3_path = "v3/"

        with patch.object(GalaxyClient, "_api_get", tracking_api_get):
            await gc.latest_version("ansible", "utils")

        url = call_urls[0]
        assert url.startswith("https://galaxy.ansible.com/api/v3/collections/ansible/utils/versions/")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestDynamicUrlConstruction -v`
Expected: FAIL — methods still use hardcoded paths

- [ ] **Step 3: Keep `_safe_api_get` unchanged (no discovery here)**

`_safe_api_get` stays as-is — it wraps `_api_get` with error handling only. Each public method calls `_discover_api_root()` explicitly before building URLs. This avoids a subtle bug: if discovery ran inside `_safe_api_get`, URLs built by `_build_v3_url()` before `_safe_api_get` would use the wrong base (pre-discovery `_base` instead of post-discovery `_api_root`).

```python
# _safe_api_get stays UNCHANGED from current code — no discovery call here
async def _safe_api_get(
    self,
    path: str,
    params: dict[str, str] | None = None,
    timeout: httpx.Timeout = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """Wrap _api_get with network error handling."""
    try:
        return await self._api_get(path, params=params, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise GalaxyError("Galaxy connection timed out") from exc
    except httpx.RequestError as exc:
        raise GalaxyError(f"Galaxy connection error: {type(exc).__name__}") from exc
```

- [ ] **Step 4: Update `_api_get` to accept full URLs**

```python
async def _api_get(
    self,
    path: str,
    params: dict[str, str] | None = None,
    timeout: httpx.Timeout = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{self._base}{path}"
    client = self._get_client()
    kwargs: dict[str, Any] = {
        "params": params,
        "headers": await self._resolve_auth_headers(),
        "timeout": timeout,
    }
    if not self._token and self._username and self._password:
        kwargs["auth"] = httpx.BasicAuth(self._username, self._password)
    resp = await client.get(url, **kwargs)
    # ... rest unchanged
```

- [ ] **Step 5: Replace hardcoded paths in `latest_version`**

**IMPORTANT:** `_discover_api_root()` MUST run before `_build_v3_url()`. Discovery sets `_api_root` and `_v3_path` which `_build_v3_url` uses as the URL base. Without discovery, `_build_v3_url` falls back to `self._base` which produces wrong URLs for servers where `_base != _api_root` (e.g., public Galaxy: `_base = "https://galaxy.ansible.com"` but `_api_root = "https://galaxy.ansible.com/api"`).

```python
async def latest_version(self, namespace: str, name: str) -> str:
    cache_key = (namespace, name)
    cached = _version_cache.get(cache_key)
    if cached is not None:
        return cached
    await self._discover_api_root()
    url = self._build_v3_url("collections", namespace, name, "versions")
    params = {"limit": "1", "ordering": "-version", "format": "json"}
    data = await self._safe_api_get(url, params=params, timeout=TIMEOUT_FAST)
    versions = data.get("data", [])
    if not versions:
        raise GalaxyError(f"No versions found for {namespace}.{name} on Galaxy.")
    version = versions[0]["version"]
    _version_cache.put(cache_key, version)
    return version
```

- [ ] **Step 6: Replace hardcoded paths in `_get_collection_detail`**

```python
async def _get_collection_detail(self, namespace: str, name: str) -> dict[str, Any]:
    await self._discover_api_root()
    url = self._build_v3_url("collections", namespace, name)
    return await self._safe_api_get(url, timeout=TIMEOUT_FAST)
```

- [ ] **Step 7: Replace hardcoded path in `search_collections`**

Search uses the Pulp-specific path because no redirect exists for it:

```python
async def search_collections(self, query: str, tags: str | None = None) -> dict[str, Any]:
    await self._discover_api_root()
    search_url = self._build_v3_url("plugin", "ansible", "search", "collection-versions")
    search_params: dict[str, str] = {
        "keywords": query,
        "is_highest": "true",
        "limit": "10",
    }
    if tags:
        search_params["tags"] = tags
    data = await self._safe_api_get(search_url, params=search_params)
    # ... rest unchanged
```

- [ ] **Step 8: Replace hardcoded paths in `_fetch_docs_blob`**

```python
async def _fetch_docs_blob(self, namespace: str, name: str, version: str) -> dict[str, Any]:
    cache_key = (namespace, name, version)
    cached = _blob_cache.get(cache_key)
    if cached is not None:
        return cached
    await self._discover_api_root()
    url = self._build_v3_url(
        "collections", namespace, name, "versions", version, "docs-blob",
    )
    params = {"format": "json"}
    data = await self._safe_api_get(url, params=params, timeout=TIMEOUT_SLOW)
    blob = data.get("docs_blob", data)
    _blob_cache.put(cache_key, blob)
    return blob
```

- [ ] **Step 9: Fix existing tests to work with discovery**

Existing tests break because `_discover_api_root()` now runs before API calls. Discovery calls `client.get()` directly (not `_api_get`), so patching `_api_get` alone doesn't prevent discovery from hitting the mock client. Mock responses don't contain `available_versions`, so discovery fails with `GalaxyError`.

**Fix strategy:** Add a helper fixture and pre-set `_api_root`/`_v3_path` on all `GalaxyClient` instances that call public methods (which now trigger discovery).

Add this fixture near the top of `tests/test_galaxy.py`:

```python
def _skip_discovery(gc: GalaxyClient, base: str = "https://galaxy.ansible.com/api") -> GalaxyClient:
    """Pre-set discovery state so tests skip the discovery handshake."""
    gc._api_root = base
    gc._v3_path = "v3/"
    return gc
```

**Affected test classes and methods** (all that create a `GalaxyClient` and call `latest_version`, `search_collections`, `fetch_module_doc`, `fetch_collection_docs`, `list_collection_modules`, `list_collection_roles`, `list_collection_plugins`, `fetch_role_doc`, or `fetch_plugin_doc`):

| Test class | Methods needing `_skip_discovery` |
|------------|----------------------------------|
| `TestLatestVersion` | `test_returns_latest_version`, `test_raises_on_empty_versions`, `test_raises_on_http_error` |
| `TestSearchCollections` | All 4 methods (use `_mock_search_context` which patches `_api_get`, but search now calls `_discover_api_root()` before `_api_get`) |
| `TestDetailEnrichmentFailure` | Both methods |
| `TestFetchModuleDoc` | All 4 methods |
| `TestFetchCollectionDocs` | `test_returns_all_module_docs`, `test_with_explicit_version`, `test_empty_collection_returns_empty_dict`, `test_silences_individual_module_failures` |
| `TestListCollectionModules` | `test_lists_modules_only` |
| `TestSearchCollectionsEdgeCases` | `test_count_data_mismatch`, `test_empty_tags_in_content` |
| `TestModuleWithoutDocStrings` | `test_list_modules_missing_doc_strings` |
| `TestResponseSizeLimit` | All 4 methods |
| `TestCacheHitPaths` | `test_blob_cache_hit_skips_api` (version cache hit in `test_version_cache_hit_skips_api` skips discovery) |
| `TestNetworkErrors` | All 4 methods |
| `TestTimeoutPassthrough` | Both methods |
| `TestHttpClientInjection` | All 3 methods |
| `TestEnrichmentSemaphore` | `test_limits_concurrent_enrichment` |
| `TestGalaxyClientAuth` | `test_token_sent_in_request`, `test_basic_auth_sent_in_request`, `test_token_takes_precedence_over_basic_auth` |
| `TestFindRole` / `TestFetchRoleDoc` / `TestListCollectionRoles` | Methods calling `fetch_role_doc`, `list_collection_roles` |
| `TestFindPlugin` / `TestFetchPluginDoc` / `TestListCollectionPlugins` | Methods calling `fetch_plugin_doc`, `list_collection_plugins` |
| `TestSearchCollectionsRoleCount` | `test_includes_role_count` |

**Tests that do NOT need the fix** (no discovery triggered):
- `TestParseFqcn` — calls `_parse_fqcn` directly
- `TestCacheEviction`, `TestCacheTTL`, `TestConcurrentCacheAccess` — cache-only, no API calls
- `TestTimeoutConstants` — attribute inspection only
- `TestGalaxyClientCleanup` — close/context manager only
- `TestGalaxyClientAuth.test_token_auth_headers`, `test_no_auth_headers`, `test_verify_false_on_owned_client`, `test_server_name_stored`, `test_from_config` — inspect attributes, no API calls
- `TestCacheHitPaths.test_version_cache_hit_skips_api` — cache hit in `latest_version` returns before `_discover_api_root` is reached (cache check is first)

For each affected test, add `_skip_discovery(gc)` or `gc._api_root = ...; gc._v3_path = "v3/"` after creating the `GalaxyClient`. For tests that use `_mock_search_context` (patching `_api_get`), also pre-set discovery state since `search_collections` now calls `_discover_api_root()` before building the URL.

Run: `.venv/bin/pytest tests/test_galaxy.py -v 2>&1 | head -100`
Verify each class above is fixed.

- [ ] **Step 10: Verify all tests pass**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: All pass

- [ ] **Step 11: Run full project test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: All pass

- [ ] **Step 12: Run linter**

Run: `.venv/bin/ruff check src/ tests/`
Expected: No errors

- [ ] **Step 13: Commit**

```bash
git add src/ansible_know/galaxy.py tests/test_galaxy.py
git commit -m "fix: replace hardcoded Galaxy API paths with dynamic URL building

All Galaxy API methods now use _build_v3_url() with the discovered
API root and v3 path prefix. Collection endpoints use standard v3
paths (collections/{ns}/{name}/) and rely on server redirects.
Search uses the Pulp-specific path since no redirect exists for it.

Existing tests pre-set _api_root and _v3_path to skip the discovery
handshake, since they test specific method behavior rather than
the discovery flow.

This fixes URL construction for Automation Hub and PAH where the
base URL already contains path segments that were being duplicated.

Fixes #154"
```

---

### Task 5: Add integration tests

**Files:**
- Create: `tests/integration/test_galaxy_discovery.py`

**Interfaces:**
- Consumes: `GalaxyClient`, `GalaxyServerConfig`, `load_galaxy_servers`
- Produces: Integration tests validating against real Galaxy and optionally AH

- [ ] **Step 1: Write integration tests**

```python
"""Integration tests for Galaxy API root discovery.

Run with: pytest tests/integration/ --run-integration
For AH tests: set AH_TOKEN env var with offline token.
"""

import os

import pytest

from ansible_know.galaxy import GalaxyClient


@pytest.mark.integration
class TestPublicGalaxyDiscovery:
    @pytest.mark.asyncio
    async def test_discovery(self):
        """Discover API root from public Galaxy."""
        async with GalaxyClient(base_url="https://galaxy.ansible.com") as gc:
            await gc._discover_api_root()
            assert gc._api_root is not None
            assert gc._v3_path is not None
            assert "v3" in gc._v3_path

    @pytest.mark.asyncio
    async def test_latest_version(self):
        """Full round-trip: discover + fetch version."""
        async with GalaxyClient(base_url="https://galaxy.ansible.com") as gc:
            version = await gc.latest_version("ansible", "utils")
            assert version

    @pytest.mark.asyncio
    async def test_search_collections(self):
        """Search works via Pulp-specific path."""
        async with GalaxyClient(base_url="https://galaxy.ansible.com") as gc:
            result = await gc.search_collections("netbox")
            assert result["count"] > 0


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("AH_TOKEN"),
    reason="AH_TOKEN not set",
)
class TestAutomationHubDiscovery:
    @pytest.fixture
    async def ah_client(self):
        """Create an AH client with SSO auth."""
        gc = GalaxyClient(
            base_url="https://console.redhat.com/api/automation-hub/content/published",
            token=os.environ["AH_TOKEN"],
            auth_url="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
            server_name="automation_hub",
        )
        yield gc
        await gc.close()

    @pytest.mark.asyncio
    async def test_discovery(self, ah_client):
        await ah_client._discover_api_root()
        assert ah_client._api_root is not None
        assert ah_client._v3_path == "v3/"

    @pytest.mark.asyncio
    async def test_latest_version(self, ah_client):
        version = await ah_client.latest_version("redhat", "insights")
        assert version

    @pytest.mark.asyncio
    async def test_search_collections(self, ah_client):
        result = await ah_client.search_collections("redhat")
        assert result["count"] > 0

    @pytest.mark.asyncio
    async def test_fetch_module_doc(self, ah_client):
        doc, meta = await ah_client.fetch_module_doc("redhat.insights.insights_config")
        assert "redhat.insights.insights_config" in doc
        assert meta["doc_source"] == "galaxy"
```

- [ ] **Step 2: Run integration tests**

Run: `.venv/bin/pytest tests/integration/test_galaxy_discovery.py --run-integration -v`
Expected: Public Galaxy tests PASS; AH tests PASS if AH_TOKEN set

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_galaxy_discovery.py
git commit -m "test: add integration tests for Galaxy API root discovery

Validates discovery, version lookup, search, and doc fetching against
public Galaxy. When AH_TOKEN is set, also validates against Red Hat
Automation Hub with SSO token exchange.

Part of #154"
```

---

### Task 6: Write ansible.cfg for the project and update galaxy_config.py test coverage

**Files:**
- Create: `ansible.cfg` in project root (for development/integration testing)
- Modify: `tests/test_galaxy_config.py` if needed

**Interfaces:**
- Consumes: env vars `ANSIBLE_GALAXY_SERVER_AUTOMATION_HUB_TOKEN`, `ANSIBLE_GALAXY_SERVER_AH_TOKEN`, `AH_TOKEN`
- Produces: Working multi-server Galaxy config for development and CI

- [ ] **Step 1: Create ansible.cfg**

```ini
[galaxy]
server_list = certified, validated, galaxy

[galaxy_server.certified]
url=https://console.redhat.com/api/automation-hub/content/published/
auth_url=https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token

[galaxy_server.validated]
url=https://console.redhat.com/api/automation-hub/content/validated/
auth_url=https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token

[galaxy_server.galaxy]
url=https://galaxy.ansible.com/
```

Tokens are provided via env vars: `ANSIBLE_GALAXY_SERVER_CERTIFIED_TOKEN` and `ANSIBLE_GALAXY_SERVER_VALIDATED_TOKEN`.

- [ ] **Step 2: Add ansible.cfg to .gitignore if it contains tokens, or verify env-var-only approach**

Since this ansible.cfg has no tokens (env vars), it can be committed. Verify it works with `load_galaxy_servers()`.

- [ ] **Step 3: Commit**

```bash
git add ansible.cfg
git commit -m "chore: add ansible.cfg with multi-server Galaxy config

Configures certified (published) and validated AH repos alongside
public Galaxy. Tokens provided via ANSIBLE_GALAXY_SERVER_*_TOKEN
env vars.

Part of #154"
```

---

## Review Notes

Findings from four review rounds with live API testing against public Galaxy and Automation Hub. Items marked "no fix needed" are documented here for implementer awareness.

### Applied fixes (rounds 1-2)

1. **Shared httpx client bypasses `follow_redirects` (Critical)** — `server.py:123` creates a shared `httpx.AsyncClient` without `follow_redirects`. This client is injected into `GalaxyClient` via `_get_http_client(ctx)` → `resolution._select_http_client()` → `GalaxyClient(http_client=client)`. When injected, `_get_client()` returns the injected client, skipping the owned client's `follow_redirects`. **Fix applied:** Task 1 now modifies both `galaxy.py` and `server.py`.

2. **SSO `client_id` was hardcoded to `"cloud-services"` (Minor)** — `GalaxyServerConfig` already has a `client_id` field parsed from ansible.cfg. On-premise PAH may use a different value. **Fix applied:** Task 2 now passes `client_id` through from config with `"cloud-services"` fallback.

### Applied fixes (round 3)

3. **`follow_redirects="safe"` is not a valid httpx value (Moderate)** — httpx's `follow_redirects` parameter is typed `bool`. The string `"safe"` is truthy so it works at runtime, but it's incorrect API usage and would be flagged by type checkers. httpx has no "safe" redirect mode — `True` follows all redirects, which is correct since `_api_get` only uses GET. **Fix applied:** Changed to `follow_redirects=True` in both Task 1 code samples.

4. **URLs built before discovery runs (Critical)** — `latest_version`, `_get_collection_detail`, and `_fetch_docs_blob` called `_build_v3_url()` before `_safe_api_get()`. Discovery ran inside `_safe_api_get`, but the URL was already constructed with pre-discovery `self._base` instead of post-discovery `_api_root`. For public Galaxy: produced `galaxy.ansible.com/collections/...` instead of `galaxy.ansible.com/api/v3/collections/...`. **Fix applied:** Each method now calls `await self._discover_api_root()` before `_build_v3_url()`. Discovery removed from `_safe_api_get`.

5. **Task 5 (fix existing tests) was a separate task with misleading intermediate assertions (Moderate)** — Tasks 3-4 claimed "Expected: All pass" but existing tests break when `_discover_api_root()` is introduced because mock responses don't contain `available_versions`. **Fix applied:** Merged Task 5 into Task 4, fixed intermediate expectations, renumbered subsequent tasks.

6. **SSO access token expiry not handled (Moderate)** — Cached `_access_token` never expired, causing 401s in long-running sessions. **Fix applied:** Added retry-on-401 logic in `_api_get` — clears `_access_token`, re-exchanges via SSO, retries once. Matches ansible-galaxy's pattern.

7. **`_build_v3_url` had no unit tests (Minor)** — Method was only exercised indirectly via higher-level tests. **Fix applied:** Added `TestBuildV3Url` class with edge-case tests for trailing slashes, long AH base URLs, Pulp-specific paths, and pre-discovery fallback behavior.

### Applied fixes (round 4)

8. **SSO retry test used same token on re-exchange (Low — test quality)** — `test_retries_on_401_with_fresh_token` pre-set `_access_token = "token_v1"` and `sso_response.json.side_effect[0]` also returned `"token_v1"`. Re-exchange produced the same token that just failed; test passed only because `resp_200` ignored headers. **Fix applied:** Changed to use distinct token values (`"expired_token_v1"` pre-set, `"fresh_token_v2"` from re-exchange) and added assertion that the retry GET uses the fresh token's `Bearer` header.

9. **Discovery 401 produces misleading error message (Low — UX)** — `_discover_api_root()` caught `HTTPStatusError` (including 401) with bare `pass`, then reported *"no 'available_versions' found"*. For AH servers with bad tokens, the real problem is auth, not a missing API endpoint. **Fix applied:** Added `got_auth_error` tracking in discovery. When both probes fail and either returned 401/403, error message now says *"authentication failed"* instead of *"no 'available_versions' found"*. Added `TestApiRootDiscovery.test_raises_auth_error_on_401` test. HTTP status codes logged at DEBUG in both `except` blocks.

10. **Task 4 Step 9 didn't enumerate affected tests (Moderate — test fix scope)** — Step 9 said "pre-set `_api_root` and `_v3_path`" but didn't list which of the ~20 test classes needed it, risking the implementer missing some. **Fix applied:** Added exhaustive table of affected test classes/methods and a `_skip_discovery()` helper function. Also listed tests that do NOT need the fix (with reasoning).

### No fix needed (reviewed and safe)

11. **`_resolve_auth_headers` + `_discover_api_root` circular dependency concern** — Discovery needs auth headers (AH returns 401 without auth). Auth exchange via SSO needs the httpx client. This is safe because: SSO is a separate service (`sso.redhat.com`) unrelated to the Galaxy API root, the httpx client exists before discovery runs, and `_resolve_auth_headers` doesn't depend on discovery state. No circular dependency.

12. **`GalaxyClientFactory` protocol compatibility** — `types.py:390` defines the protocol. `server.py:175` implements it via `GalaxyClient.from_config(config, ...)`. Since `from_config` reads `auth_url` and `client_id` from `config` (a `GalaxyServerConfig`), and the protocol passes `config` through, the new parameters flow through correctly without protocol changes.

13. **`_build_v3_url` always appends trailing slash** — Galaxy APIs universally require trailing slashes. This matches ansible-galaxy's `_urljoin` behavior which also appends `('',)` to ensure trailing slashes.

14. **`_api_get` dual-purpose interface (full URLs vs relative paths)** — After Task 4, `_api_get` accepts both full URLs (`startswith("http")`) and legacy relative paths. The legacy path is never used after migration. Kept for safety; can be removed in a follow-up cleanup.

15. **`collections/index/` vs `collections/` in paths** — Our current hardcoded paths use `/collections/index/{ns}/{name}/` but ansible-galaxy uses `/collections/{ns}/{name}/`. The `index/` is a Pulp internal path. Standard v3 paths (`collections/{ns}/{name}/`) return 302 redirects to the Pulp path — verified on both public Galaxy and AH. With `follow_redirects=True`, this works transparently. Search has NO redirect and MUST use the Pulp-specific path (`plugin/ansible/search/collection-versions/`).

16. **ansible-core 2.18 vs 2.20+ (devel) `g_connect` logic** — Verified identical. The API root discovery logic in `ansible/galaxy/api.py` is unchanged between 2.18.11 and devel (latest). No forward-compatibility concerns.

### Verified API behavior matrix

| Endpoint | Public Galaxy | AH (published) | AH (validated) |
|----------|--------------|-----------------|-----------------|
| API root discovery | `galaxy.ansible.com/api/` → `v3: "v3/"` | `.../content/published/` → `v3: "v3/"` | `.../content/validated/` → `v3: "v3/"` |
| `v3/collections/{ns}/{name}/` | 302 → Pulp path | 302 → Pulp path | 302 → Pulp path |
| Follow redirect | 200 | 200 | 200 |
| `v3/search/collection-versions/` | 404 | 404 | 404 |
| `v3/plugin/ansible/search/collection-versions/` | 200 | 200 | 200 |
| `Token {offline_token}` auth | N/A (no token) | 401 | 401 |
| SSO exchange → `Bearer {access_token}` | N/A | 200 | 200 |
