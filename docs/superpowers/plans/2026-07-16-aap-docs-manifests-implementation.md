# AAP Documentation Manifests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Red Hat Ansible Automation Platform (AAP) documentation for versions 2.5, 2.6, and 2.7 to ansible-know-mcp, enabling `search_docs` and `fetch_doc` for AAP product documentation.

**Architecture:** Ship static v2.0 JSON manifests (built from the Red Hat Documentation MCP server's landing page JSON) for search. Extend `fetch_doc` with a `RedHatDocsClient` that fetches docs.redhat.com pages as markdown via the MCP server's `redhat_docs_fetch` tool — no HTML-to-markdown conversion needed. The MCP server endpoint (`docs-mcp.api.redhat.com`) uses JSON-RPC over Streamable HTTP with SSE responses.

**Tech Stack:** Python 3.10+, httpx (async HTTP), JSON-RPC 2.0 (MCP protocol), SSE parsing (stdlib string ops).

## Global Constraints

- No new pip dependencies — httpx is already a dependency; MCP client uses stdlib for JSON-RPC/SSE parsing.
- All new modules follow the existing Foundation/Domain layer architecture.
- TDD: write failing tests first, then implement.
- Manifests use the existing v2.0 format (same as `ansible_core_manifest.json`).
- The MCP server at `docs-mcp.api.redhat.com` requires no authentication but has intermittent 404s (session expiry) — client must retry with session re-init.
- 8 of 53 AAP 2.6 guides fail on the MCP server (URL validation issues on their end) — omit from manifest, log during build.
- VPN may be required for `docs-mcp.api.redhat.com` — not confirmed. Add env var to disable Red Hat docs if unreachable.
- Use `.venv/bin/pytest` and `.venv/bin/ruff` directly (sandbox mode).

---

## File Structure

**New files:**
| File | Responsibility |
|------|---------------|
| `src/ansible_know/redhat_docs.py` | Async MCP client for `docs-mcp.api.redhat.com` — session management, `fetch()`, SSE parsing, retry on 404 |
| `tests/test_redhat_docs.py` | Unit tests for `RedHatDocsClient` (mocked httpx) |
| `scripts/build_aap_manifests.py` | Build-time script to generate AAP manifest JSON files from MCP landing pages |
| `src/ansible_know/data/aap_25_manifest.json` | AAP 2.5 manifest (38 guides) |
| `src/ansible_know/data/aap_26_manifest.json` | AAP 2.6 manifest (~45 topics, excluding 8 broken) |
| `src/ansible_know/data/aap_27_manifest.json` | AAP 2.7 manifest (50 topics) |
| `tests/integration/test_redhat_docs_live.py` | Integration tests hitting live MCP server (skipped by default) |

**Modified files:**
| File | Changes |
|------|---------|
| `src/ansible_know/text_utils.py` | Add `clean_redhat_markdown()` function |
| `src/ansible_know/config.py` | Add 3 AAP entries to `DEFAULT_DOC_SOURCES`, add `REDHAT_DOCS_MCP_URL` constant |
| `src/ansible_know/validation.py` | Allow `docs.redhat.com` in `validate_doc_url()` |
| `src/ansible_know/docs.py` | Branch in `fetch_doc_content()` for `docs.redhat.com` URLs |
| `src/ansible_know/server.py` | Update `fetch_doc` tool annotation/docstring, `search_docs` description |
| `tests/test_validation.py` | Add tests for `docs.redhat.com` URLs |
| `tests/test_docs.py` | Add tests for `docs.redhat.com` fetch branch |
| `tests/test_doc_manifests.py` | Add tests verifying AAP manifests load and are searchable |

---

### Task 1: RedHatDocsClient — async MCP client and markdown cleaning

**Files:**
- Create: `src/ansible_know/redhat_docs.py`
- Create: `tests/test_redhat_docs.py`
- Modify: `src/ansible_know/text_utils.py` — add `clean_redhat_markdown()`
- Modify: `src/ansible_know/config.py:221` — add `REDHAT_DOCS_MCP_URL` constant

**Interfaces:**
- Consumes: `httpx.AsyncClient`, `AnsibleKnowError` from `errors.py`, `USER_AGENT` from `config.py`
- Produces: `RedHatDocsClient` class with `async fetch(url: str) -> str`, `async connect() -> None`, `async close() -> None`. Also `clean_redhat_markdown(raw: str) -> tuple[str, str]` in `text_utils.py`, and `parse_mcp_sse(body: str) -> dict | None` as a public helper.

- [ ] **Step 1: Write failing tests for SSE parsing**

```python
# tests/test_redhat_docs.py
"""Unit tests for RedHatDocsClient (mocked httpx, no network)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from ansible_know.redhat_docs import RedHatDocsClient, parse_mcp_sse


class TestParseMcpSse:
    """Tests for SSE response parsing."""

    def test_extracts_result_from_data_line(self):
        body = 'data: {"jsonrpc":"2.0","id":"abc","result":{"content":[{"type":"text","text":"hello"}]}}\n\n'
        parsed = parse_mcp_sse(body)
        assert parsed is not None
        assert "result" in parsed
        assert parsed["result"]["content"][0]["text"] == "hello"

    def test_extracts_error_from_data_line(self):
        body = 'data: {"jsonrpc":"2.0","id":"abc","error":{"code":-1,"message":"fail"}}\n\n'
        parsed = parse_mcp_sse(body)
        assert parsed is not None
        assert "error" in parsed

    def test_returns_none_when_data_has_no_result_or_error(self):
        body = "data: {}\n\n"
        parsed = parse_mcp_sse(body)
        assert parsed is None

    def test_skips_non_data_sse_lines(self):
        body = "event: message\nid: 123\nretry: 5000\n\n"
        parsed = parse_mcp_sse(body)
        assert parsed is None

    def test_returns_none_for_empty_body(self):
        assert parse_mcp_sse("") is None

    def test_handles_multiple_data_lines_returns_last_result(self):
        body = (
            'data: {"jsonrpc":"2.0","id":"1","result":{"content":[{"type":"text","text":"first"}]}}\n'
            'data: {"jsonrpc":"2.0","id":"2","result":{"content":[{"type":"text","text":"second"}]}}\n\n'
        )
        parsed = parse_mcp_sse(body)
        assert parsed["result"]["content"][0]["text"] == "second"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_redhat_docs.py::TestParseMcpSse -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ansible_know.redhat_docs'`

- [ ] **Step 3: Implement SSE parsing and config constant**

Add the MCP URL constant to `config.py`:

```python
# src/ansible_know/config.py — append after GALAXY_BASE_URL block (line ~225)

REDHAT_DOCS_MCP_URL = os.environ.get(
    "ANSIBLE_KNOW_REDHAT_DOCS_MCP_URL",
    "https://docs-mcp.api.redhat.com/mcp",
)
```

Create the client module with SSE parsing:

```python
# src/ansible_know/redhat_docs.py
"""Async client for the Red Hat Documentation MCP server.

Uses JSON-RPC over Streamable HTTP to fetch docs.redhat.com pages as
markdown via the ``redhat_docs_fetch`` tool. Sessions are initialized
lazily and re-created on 404 (server-side session expiry).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx

from ansible_know.config import REDHAT_DOCS_MCP_URL, USER_AGENT
from ansible_know.errors import AnsibleKnowError

logger = logging.getLogger("ansible_know")

__all__ = [
    "RedHatDocsClient",
    "parse_mcp_sse",
]

_MCP_PROTOCOL_VERSION = "2024-11-05"
_MAX_RETRIES = 3


def parse_mcp_sse(body: str) -> dict[str, Any] | None:
    """Extract JSON-RPC response from an MCP SSE (text/event-stream) body.

    Returns the last ``data:`` line that contains a ``result`` or ``error``
    key, or None if no valid response is found.
    """
    response = None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        try:
            obj = json.loads(stripped[5:].strip())
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj):
            response = obj
    return response
```

- [ ] **Step 4: Run SSE parsing tests to verify they pass**

```bash
.venv/bin/pytest tests/test_redhat_docs.py::TestParseMcpSse -v
```

Expected: all 5 PASS

- [ ] **Step 5: Write failing tests for RedHatDocsClient**

Append to `tests/test_redhat_docs.py`:

```python
from unittest.mock import AsyncMock, patch

from ansible_know.errors import AnsibleKnowError


def _make_sse_response(content_text: str, session_id: str = "test-session") -> httpx.Response:
    """Build a mock httpx.Response with SSE body for a tool call result."""
    result = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "result": {
            "content": [{"type": "text", "text": content_text}],
        },
    }
    return httpx.Response(
        200,
        text=f"data: {json.dumps(result)}\n\n",
        headers={
            "content-type": "text/event-stream",
            "mcp-session-id": session_id,
        },
        request=httpx.Request("POST", "https://docs-mcp.api.redhat.com/mcp"),
    )


def _make_init_response(session_id: str = "test-session") -> httpx.Response:
    """Build a mock httpx.Response for MCP initialize."""
    result = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test", "version": "1.0"},
        },
    }
    return httpx.Response(
        200,
        text=f"data: {json.dumps(result)}\n\n",
        headers={
            "content-type": "text/event-stream",
            "mcp-session-id": session_id,
        },
        request=httpx.Request("POST", "https://docs-mcp.api.redhat.com/mcp"),
    )


def _make_notification_response(session_id: str = "test-session") -> httpx.Response:
    """Build a mock httpx.Response for notification (no body)."""
    return httpx.Response(
        200,
        text="",
        headers={"mcp-session-id": session_id},
        request=httpx.Request("POST", "https://docs-mcp.api.redhat.com/mcp"),
    )


class TestRedHatDocsClient:
    """Tests for RedHatDocsClient with mocked httpx."""

    @pytest.mark.asyncio
    async def test_fetch_returns_markdown(self):
        markdown = "# Installing AAP\n\nFollow these steps..."
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[
            _make_init_response(),
            _make_notification_response(),
            _make_sse_response(markdown),
        ])
        mock_client.aclose = AsyncMock()

        client = RedHatDocsClient()
        client._client = mock_client

        result = await client.fetch("https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install")
        assert result == markdown
        await client.close()

    @pytest.mark.asyncio
    async def test_fetch_retries_on_404(self):
        """Client reinitializes session and retries on 404."""
        markdown = "# Guide content"
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        not_found = httpx.Response(
            404,
            text="Not Found",
            request=httpx.Request("POST", "https://docs-mcp.api.redhat.com/mcp"),
        )

        mock_client.post = AsyncMock(side_effect=[
            _make_init_response("session-1"),
            _make_notification_response("session-1"),
            not_found,
            _make_init_response("session-2"),
            _make_notification_response("session-2"),
            _make_sse_response(markdown, "session-2"),
        ])
        mock_client.aclose = AsyncMock()

        client = RedHatDocsClient()
        client._client = mock_client

        result = await client.fetch("https://docs.redhat.com/some/page")
        assert result == markdown
        await client.close()

    @pytest.mark.asyncio
    async def test_fetch_raises_after_max_retries(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        not_found = httpx.Response(
            404,
            text="Not Found",
            request=httpx.Request("POST", "https://docs-mcp.api.redhat.com/mcp"),
        )
        mock_client.post = AsyncMock(side_effect=[
            _make_init_response("s1"),
            _make_notification_response("s1"),
            not_found,
            _make_init_response("s2"),
            _make_notification_response("s2"),
            not_found,
            _make_init_response("s3"),
            _make_notification_response("s3"),
            not_found,
        ])
        mock_client.aclose = AsyncMock()

        client = RedHatDocsClient()
        client._client = mock_client

        with pytest.raises(AnsibleKnowError, match="failed after"):
            await client.fetch("https://docs.redhat.com/broken")
        await client.close()

    @pytest.mark.asyncio
    async def test_fetch_unwraps_result_envelope(self):
        """MCP sometimes wraps content in {"result": "<content>"} envelope."""
        inner = "# Real markdown content"
        wrapped = json.dumps({"result": inner})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[
            _make_init_response(),
            _make_notification_response(),
            _make_sse_response(wrapped),
        ])
        mock_client.aclose = AsyncMock()

        client = RedHatDocsClient()
        client._client = mock_client

        result = await client.fetch("https://docs.redhat.com/some/page")
        assert result == inner
        await client.close()

    @pytest.mark.asyncio
    async def test_fetch_landing_returns_json(self):
        """Landing page responses return structured JSON."""
        landing = json.dumps({
            "product": "Red Hat Ansible Automation Platform",
            "version": "2.7",
            "categoryTitles": {
                "Install": {
                    "description": "",
                    "titles": [
                        {"name": "Install AAP", "description": "Guide", "url": "https://docs.redhat.com/..."}
                    ],
                }
            },
        })
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[
            _make_init_response(),
            _make_notification_response(),
            _make_sse_response(landing),
        ])
        mock_client.aclose = AsyncMock()

        client = RedHatDocsClient()
        client._client = mock_client

        result = await client.fetch("https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7")
        parsed = json.loads(result)
        assert parsed["product"] == "Red Hat Ansible Automation Platform"
        await client.close()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        client = RedHatDocsClient()
        await client.close()
        await client.close()
```

- [ ] **Step 6: Run client tests to verify they fail**

```bash
.venv/bin/pytest tests/test_redhat_docs.py::TestRedHatDocsClient -v
```

Expected: FAIL — `RedHatDocsClient` class incomplete (missing `fetch`, `connect`, `close` methods)

- [ ] **Step 7: Implement RedHatDocsClient**

Complete `src/ansible_know/redhat_docs.py` — append to existing file after `parse_mcp_sse`:

```python
class RedHatDocsClient:
    """Async client for the Red Hat Documentation MCP server.

    Connects to ``docs-mcp.api.redhat.com`` via JSON-RPC over Streamable
    HTTP. Sessions are initialized lazily on first ``fetch()`` call and
    re-created transparently when the server returns 404 (session expiry).
    """

    def __init__(self, mcp_url: str | None = None):
        self.mcp_url = mcp_url or REDHAT_DOCS_MCP_URL
        self._session_id: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """Initialize MCP session. Called automatically by ``fetch()``."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, read=120.0),
            )
        self._session_id = None

        resp = await self._client.post(
            self.mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": USER_AGENT, "version": "1.0"},
                },
            },
            headers=_mcp_headers(None),
        )
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")

        await self._client.post(
            self.mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            headers=_mcp_headers(self._session_id),
        )

    async def fetch(self, url: str) -> str:
        """Fetch a docs.redhat.com page via the MCP server.

        Returns raw text content (markdown for guide pages, JSON for landing
        pages). Retries with session re-init on 404.
        """
        for attempt in range(_MAX_RETRIES):
            if self._session_id is None:
                await self.connect()
            try:
                return await self._call_tool("redhat_docs_fetch", {"url": url})
            except _McpSessionExpired:
                logger.debug(
                    "MCP session expired (attempt %d/%d), reinitializing",
                    attempt + 1, _MAX_RETRIES,
                )
                self._session_id = None
                continue
        raise AnsibleKnowError(
            f"Red Hat Docs MCP fetch failed after {_MAX_RETRIES} retries "
            f"(session kept expiring) for {url}"
        )

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool and return its text content."""
        assert self._client is not None
        resp = await self._client.post(
            self.mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=_mcp_headers(self._session_id),
        )
        if resp.status_code == 404:
            raise _McpSessionExpired()
        resp.raise_for_status()

        parsed = parse_mcp_sse(resp.text)
        if parsed is None:
            raise AnsibleKnowError(f"No valid JSON-RPC response from MCP server for {name}")
        if "error" in parsed:
            msg = parsed["error"].get("message", str(parsed["error"]))
            raise AnsibleKnowError(f"MCP tool {name} error: {msg}")

        content_blocks = parsed.get("result", {}).get("content", [])
        raw = "".join(
            block.get("text", "")
            for block in content_blocks
            if block.get("type") == "text"
        )
        return _unwrap_mcp_result(raw)

    async def close(self) -> None:
        """Close the HTTP client and clear session state."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._session_id = None


class _McpSessionExpired(Exception):
    """Internal: MCP server returned 404 (session expired)."""


def _mcp_headers(session_id: str | None) -> dict[str, str]:
    """Build HTTP headers for an MCP request."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": _MCP_PROTOCOL_VERSION,
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _unwrap_mcp_result(raw: str) -> str:
    """Unwrap a ``{"result": "<content>"}`` envelope if present.

    The MCP server sometimes wraps its response in a JSON envelope with
    the actual content as a string value under ``result``. This function
    handles both the wrapped and direct cases.
    """
    if not raw.lstrip().startswith("{"):
        return raw
    try:
        obj = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return raw
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        return obj["result"]
    return raw
```

- [ ] **Step 8: Run all RedHatDocsClient tests**

```bash
.venv/bin/pytest tests/test_redhat_docs.py -v
```

Expected: all PASS

- [ ] **Step 9: Write failing tests for clean_redhat_markdown**

Append to `tests/test_redhat_docs.py`:

```python
from ansible_know.text_utils import clean_redhat_markdown


class TestCleanRedhatMarkdown:
    """Tests for Red Hat docs markdown cleaning."""

    def test_extracts_title_from_h1(self):
        raw = "# Installing AAP\n\nSome content here."
        content, title = clean_redhat_markdown(raw)
        assert title == "Installing AAP"
        assert "Some content here" in content

    def test_strips_copy_link_artifacts(self):
        raw = "# Title\n\n## Section oneCopy linkLink copied to clipboard!\n\nContent."
        content, title = clean_redhat_markdown(raw)
        assert "Copy link" not in content
        assert "## Section one" in content

    def test_strips_legal_notice_section(self):
        raw = "# Title\n\n## Legal Notice\n\nCopyright stuff.\n\n## Real Section\n\nContent."
        content, title = clean_redhat_markdown(raw)
        assert "Legal Notice" not in content
        assert "Real Section" in content

    def test_collapses_excess_blank_lines(self):
        raw = "# Title\n\n\n\n\nContent."
        content, _ = clean_redhat_markdown(raw)
        assert "\n\n\n" not in content

    def test_returns_empty_for_empty_input(self):
        content, title = clean_redhat_markdown("")
        assert content == ""
        assert title == ""

    def test_handles_no_h1(self):
        raw = "Some content without a heading."
        content, title = clean_redhat_markdown(raw)
        assert title == ""
        assert "Some content" in content
```

- [ ] **Step 10: Run clean_redhat_markdown tests to verify they fail**

```bash
.venv/bin/pytest tests/test_redhat_docs.py::TestCleanRedhatMarkdown -v
```

Expected: FAIL — `ImportError: cannot import name 'clean_redhat_markdown'`

- [ ] **Step 11: Implement clean_redhat_markdown**

Add to `src/ansible_know/text_utils.py` after the existing `clean_rtd_markdown` function:

```python
# Add to __all__ at top of file
__all__ = [
    "clean_redhat_markdown",
    "clean_rtd_markdown",
]

# Add these regexes after the existing ones
_RH_COPY_LINK_RE = re.compile(
    r"Copy\s*link\s*(?:Link\s*copied\s*(?:to\s*clipboard)?!?)?$",
    re.MULTILINE,
)
_RH_SKIP_SECTIONS = {"legal notice"}


def clean_redhat_markdown(raw: str) -> tuple[str, str]:
    """Clean Red Hat docs markdown and extract title.

    Returns (cleaned_content, title). Title is empty string if no H1 found.
    Strips Red Hat boilerplate (Legal Notice, Copy link artifacts) and
    collapses excess blank lines.
    """
    if not raw:
        return "", ""

    text = _RH_COPY_LINK_RE.sub("", raw)

    match = _H1_RE.search(text)
    if match:
        text = text[match.start():]
        title = match.group(1).strip()
    else:
        title = ""

    lines = text.split("\n")
    filtered: list[str] = []
    skip_until_next_h2 = False
    for line in lines:
        h2_match = re.match(r"^##\s+(.+)$", line)
        if h2_match:
            heading_lower = h2_match.group(1).strip().lower()
            if heading_lower in _RH_SKIP_SECTIONS:
                skip_until_next_h2 = True
                continue
            skip_until_next_h2 = False
        if skip_until_next_h2:
            continue
        filtered.append(line)

    text = "\n".join(filtered)
    text = _EXCESS_BLANKS_RE.sub("\n\n", text)
    return text.strip(), title
```

- [ ] **Step 12: Run all Task 1 tests**

```bash
.venv/bin/pytest tests/test_redhat_docs.py -v
```

Expected: all PASS

- [ ] **Step 13: Lint**

```bash
.venv/bin/ruff check src/ansible_know/redhat_docs.py src/ansible_know/text_utils.py tests/test_redhat_docs.py
```

Expected: no errors

- [ ] **Step 14: Run full test suite to check for regressions**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/integration
```

Expected: all existing tests PASS

- [ ] **Step 15: Commit**

```bash
git add src/ansible_know/redhat_docs.py tests/test_redhat_docs.py src/ansible_know/text_utils.py src/ansible_know/config.py
git commit -m "feat: add RedHatDocsClient for docs-mcp.api.redhat.com

Async MCP client using JSON-RPC over Streamable HTTP to fetch
docs.redhat.com pages as markdown. Includes SSE parsing, session
management with auto-retry on 404 (server-side session expiry),
and clean_redhat_markdown() for stripping Red Hat boilerplate.

Assisted-by: Claude Opus 4.6"
```

---

### Task 2: URL validation and config registration

**Files:**
- Modify: `src/ansible_know/validation.py:180-197` — allow `docs.redhat.com`
- Modify: `src/ansible_know/config.py:168-193` — add 3 AAP entries to `DEFAULT_DOC_SOURCES`
- Modify: `tests/test_validation.py` — add docs.redhat.com URL tests
- Modify: `tests/test_config.py` — verify AAP sources registered

**Interfaces:**
- Consumes: nothing new
- Produces: `validate_doc_url()` now accepts `docs.redhat.com` URLs. `ALLOWED_DOC_HOSTS` constant exported from `validation.py`. Three new entries in `DEFAULT_DOC_SOURCES`: `aap-2.5`, `aap-2.6`, `aap-2.7`.

- [ ] **Step 1: Write failing tests for docs.redhat.com URL validation**

Append to `tests/test_validation.py` inside the existing URL validation test class (find the class containing `test_valid_docs_url`):

```python
    def test_valid_redhat_docs_url(self):
        validate_doc_url("https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install")

    def test_valid_redhat_docs_url_26_topic(self):
        validate_doc_url("https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/install-proc_installing_containerized_aap")

    def test_valid_redhat_docs_url_25_html(self):
        validate_doc_url("https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/planning_your_installation")

    def test_invalid_other_redhat_domain(self):
        with pytest.raises(ValidationError):
            validate_doc_url("https://access.redhat.com/documentation/something")

    def test_invalid_http_redhat(self):
        with pytest.raises(ValidationError):
            validate_doc_url("http://docs.redhat.com/page")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_validation.py -k "redhat" -v
```

Expected: FAIL — `docs.redhat.com` rejected by current validation

- [ ] **Step 3: Update validate_doc_url to allow docs.redhat.com**

Edit `src/ansible_know/validation.py`:

Replace the `validate_doc_url` function (lines 180-197):

```python
ALLOWED_DOC_HOSTS = frozenset({"docs.ansible.com", "docs.redhat.com"})


def validate_doc_url(url: str) -> None:
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            f"URL must be non-empty and under {MAX_URL_LENGTH} characters."
        )
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValidationError(f"Invalid URL format: {exc}") from exc
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_DOC_HOSTS:
        raise ValidationError(
            "URL must start with https://docs.ansible.com/ or https://docs.redhat.com/"
        )
    if not parsed.path or parsed.path == "/":
        raise ValidationError(
            "URL must include a document path after the domain."
        )
```

Also add `ALLOWED_DOC_HOSTS` to `__all__`.

- [ ] **Step 4: Run validation tests**

```bash
.venv/bin/pytest tests/test_validation.py -v
```

Expected: all PASS (new + existing)

- [ ] **Step 5: Write failing test for AAP config registration**

Append to `tests/test_config.py`:

```python
from ansible_know.config import DEFAULT_DOC_SOURCES


class TestAapDocSources:
    def test_aap_sources_registered(self):
        assert "aap-2.5" in DEFAULT_DOC_SOURCES
        assert "aap-2.6" in DEFAULT_DOC_SOURCES
        assert "aap-2.7" in DEFAULT_DOC_SOURCES

    def test_aap_sources_have_file_key(self):
        for ver in ("aap-2.5", "aap-2.6", "aap-2.7"):
            assert "file" in DEFAULT_DOC_SOURCES[ver]
            assert "description" in DEFAULT_DOC_SOURCES[ver]

    def test_aap_file_paths_end_with_json(self):
        for ver in ("aap-2.5", "aap-2.6", "aap-2.7"):
            assert DEFAULT_DOC_SOURCES[ver]["file"].endswith(".json")
```

- [ ] **Step 6: Run config test to verify it fails**

```bash
.venv/bin/pytest tests/test_config.py::TestAapDocSources -v
```

Expected: FAIL — `aap-2.5` not in DEFAULT_DOC_SOURCES

- [ ] **Step 7: Register AAP sources in config.py**

Add to `DEFAULT_DOC_SOURCES` dict in `src/ansible_know/config.py` (after the `molecule` entry, before the closing `}`):

```python
    "aap-2.5": {
        "file": str(_PKG_DIR / "data" / "aap_25_manifest.json"),
        "description": "Red Hat AAP 2.5 — installation, configuration, operations, troubleshooting",
    },
    "aap-2.6": {
        "file": str(_PKG_DIR / "data" / "aap_26_manifest.json"),
        "description": "Red Hat AAP 2.6 — installation, mesh, EE, RBAC, AI features, MCP server",
    },
    "aap-2.7": {
        "file": str(_PKG_DIR / "data" / "aap_27_manifest.json"),
        "description": "Red Hat AAP 2.7 — installation, mesh, self-service, metrics, AI features",
    },
```

- [ ] **Step 8: Run all Task 2 tests**

```bash
.venv/bin/pytest tests/test_validation.py tests/test_config.py -v
```

Expected: all PASS

- [ ] **Step 9: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/integration
```

Expected: all PASS (AAP manifest file-not-found warnings in logs are OK — manifests don't exist yet)

- [ ] **Step 10: Commit**

```bash
git add src/ansible_know/validation.py src/ansible_know/config.py tests/test_validation.py tests/test_config.py
git commit -m "feat: allow docs.redhat.com URLs and register AAP doc sources

Extend validate_doc_url to accept docs.redhat.com alongside
docs.ansible.com. Register AAP 2.5/2.6/2.7 manifest sources in
DEFAULT_DOC_SOURCES (manifests will be generated in a later step).

Assisted-by: Claude Opus 4.6"
```

---

### Task 3: Extend fetch_doc for docs.redhat.com

**Files:**
- Modify: `src/ansible_know/docs.py:400-533` — add `_fetch_redhat_doc()` and branch in `fetch_doc_content()`
- Modify: `src/ansible_know/server.py:549-582` — update `fetch_doc` tool annotation and docstring
- Modify: `tests/test_docs.py` — add tests for docs.redhat.com fetch branch

**Interfaces:**
- Consumes: `RedHatDocsClient` from `redhat_docs.py`, `clean_redhat_markdown` from `text_utils.py`, `ALLOWED_DOC_HOSTS` from `validation.py`
- Produces: `fetch_doc_content(url)` now handles both `docs.ansible.com` and `docs.redhat.com` URLs transparently. Returns the same `FetchDocResult` shape.

- [ ] **Step 1: Write failing tests for docs.redhat.com fetch**

Append to `tests/test_docs.py`:

```python
from unittest.mock import AsyncMock, patch


class TestFetchDocRedhat:
    """Tests for fetch_doc_content with docs.redhat.com URLs."""

    @pytest.mark.asyncio
    async def test_redhat_url_uses_mcp_client(self):
        """docs.redhat.com URLs should use RedHatDocsClient, not direct httpx."""
        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install-proc_installing_containerized_aap"
        markdown = "# Installing containerized AAP\n\nFollow these steps to install."

        with patch("ansible_know.docs._get_redhat_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.fetch = AsyncMock(return_value=markdown)
            mock_get.return_value = mock_client

            result = await fetch_doc_content(url)

        assert result["title"] == "Installing containerized AAP"
        assert "Follow these steps" in result["content"]
        assert result["source_url"] == url
        mock_client.fetch.assert_called_once_with(url)

    @pytest.mark.asyncio
    async def test_redhat_fetch_caches_result(self):
        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install"
        markdown = "# Install Guide\n\nContent here."

        with patch("ansible_know.docs._get_redhat_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.fetch = AsyncMock(return_value=markdown)
            mock_get.return_value = mock_client

            result1 = await fetch_doc_content(url)
            result2 = await fetch_doc_content(url)

        assert result1 == result2
        assert mock_client.fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_redhat_fetch_respects_max_tokens(self):
        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install"
        markdown = "# Title\n\n" + "word " * 10000

        with patch("ansible_know.docs._get_redhat_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.fetch = AsyncMock(return_value=markdown)
            mock_get.return_value = mock_client

            result = await fetch_doc_content(url)
            assert result["tokens"] > 0

    @pytest.mark.asyncio
    async def test_redhat_fetch_error_raises(self):
        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/broken"

        with patch("ansible_know.docs._get_redhat_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.fetch = AsyncMock(
                side_effect=AnsibleKnowError("MCP fetch failed")
            )
            mock_get.return_value = mock_client

            with pytest.raises(AnsibleKnowError, match="MCP fetch failed"):
                await fetch_doc_content(url)

    @pytest.mark.asyncio
    async def test_redhat_landing_page_raises_helpful_error(self):
        """Landing page URLs (returning JSON) should raise, not produce garbage."""
        url = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7"
        landing_json = json.dumps({
            "product": "Red Hat Ansible Automation Platform",
            "version": "2.7",
            "categoryTitles": {"Install": {"titles": []}},
        })

        with patch("ansible_know.docs._get_redhat_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.fetch = AsyncMock(return_value=landing_json)
            mock_get.return_value = mock_client

            with pytest.raises(AnsibleKnowError, match="landing page"):
                await fetch_doc_content(url)

    @pytest.mark.asyncio
    async def test_ansible_url_still_uses_direct_httpx(self):
        """docs.ansible.com URLs should NOT use RedHatDocsClient."""
        clear_cache()
        url = "https://docs.ansible.com/projects/lint/rules/"

        with patch("ansible_know.docs._get_redhat_client") as mock_get:
            with patch("ansible_know.docs._fetch_with_retry", side_effect=httpx.ConnectError("mocked")):
                try:
                    await fetch_doc_content(url)
                except Exception:
                    pass
            mock_get.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_docs.py::TestFetchDocRedhat -v
```

Expected: FAIL — `_get_redhat_client` does not exist yet

- [ ] **Step 3: Implement docs.redhat.com branch in fetch_doc_content**

Add to `src/ansible_know/docs.py` after the `_page_cache` declaration (around line 63):

```python
from ansible_know.text_utils import clean_redhat_markdown
```

Add the Red Hat client accessor and fetch function before `fetch_doc_content` (around line 398):

```python
_redhat_client: Any = None
_redhat_client_lock: asyncio.Lock | None = None


async def _get_redhat_client():
    """Lazily create and return the shared RedHatDocsClient."""
    global _redhat_client, _redhat_client_lock
    if _redhat_client is not None:
        return _redhat_client
    if _redhat_client_lock is None:
        _redhat_client_lock = asyncio.Lock()
    async with _redhat_client_lock:
        if _redhat_client is None:
            from ansible_know.redhat_docs import RedHatDocsClient
            _redhat_client = RedHatDocsClient()
    return _redhat_client


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for Red Hat docs.

    The Red Hat MCP server provides no x-markdown-tokens header, unlike
    docs.ansible.com's Cloudflare endpoint. This approximation is
    intentionally rough — used only for max_tokens gating.
    """
    return len(text) // 4


async def _fetch_redhat_doc(
    url: str,
    max_tokens: int | None = None,
) -> FetchDocResult:
    """Fetch a docs.redhat.com page via the Red Hat Documentation MCP server."""
    client = await _get_redhat_client()
    raw = await client.fetch(url)

    if raw.lstrip().startswith("{"):
        try:
            json.loads(raw)
            raise AnsibleKnowError(
                "URL appears to be a landing page, not a guide page. "
                "Use search_docs to find specific guide URLs."
            )
        except json.JSONDecodeError:
            pass

    content, title = clean_redhat_markdown(raw)
    content = truncate_response(content)
    tokens = _estimate_tokens(content)

    if max_tokens is not None and tokens > max_tokens:
        raise AnsibleKnowError(
            f"Page has ~{tokens} tokens (max_tokens={max_tokens}). "
            f"Fetch without max_tokens or increase the limit."
        )

    return {
        "content": content,
        "title": title,
        "tokens": tokens,
        "source_url": url,
    }
```

Note: requires `import json` at the top of `docs.py` (already present).

Modify `fetch_doc_content` to branch on URL host. Replace the function body (lines ~402-483) with:

```python
async def fetch_doc_content(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult:
    """Fetch a documentation page as clean markdown.

    Supports both docs.ansible.com (via Cloudflare markdown content
    negotiation) and docs.redhat.com (via Red Hat Documentation MCP server).

    Args:
        url: Full documentation URL (caller must validate first).
        max_tokens: If set, raise when page exceeds this token count.
        http_client: Optional shared httpx client (docs.ansible.com only).

    Returns:
        FetchDocResult on success.

    Raises:
        httpx.HTTPError: On HTTP request failure after retries.
        AnsibleKnowError: On CF challenge, content-type mismatch,
            size/token limit, redirect to unexpected domain, or MCP error.
    """
    cached = _page_cache.get(url)
    if cached is not None:
        if max_tokens is not None and cached.get("tokens", 0) > max_tokens:
            raise AnsibleKnowError(
                f"Page has {cached['tokens']} tokens (max_tokens={max_tokens}). "
                f"Fetch without max_tokens or increase the limit."
            )
        return cached

    from urllib.parse import urlparse as _urlparse
    parsed = _urlparse(url)

    if parsed.netloc == "docs.redhat.com":
        result = await _fetch_redhat_doc(url, max_tokens)
        _page_cache.put(url, result)
        return result

    # --- docs.ansible.com path (existing logic) ---
    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        should_close = True

    try:
        resp = await _fetch_with_retry(client, url)
    finally:
        if should_close:
            await client.aclose()

    if resp.url.host != "docs.ansible.com":
        raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")

    if len(resp.content) > MAX_DOC_FETCH_SIZE:
        raise AnsibleKnowError(
            f"Response too large: {len(resp.content)} bytes (max {MAX_DOC_FETCH_SIZE})"
        )

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" not in content_type:
        raise AnsibleKnowError(
            f"Expected text/markdown but got {content_type!r} for {url}"
        )

    tokens_str = resp.headers.get("x-markdown-tokens", "0")
    try:
        tokens = int(tokens_str)
    except ValueError:
        tokens = 0

    if max_tokens is not None and tokens > max_tokens:
        raise AnsibleKnowError(
            f"Page has {tokens} tokens (max_tokens={max_tokens}). "
            f"Fetch without max_tokens or increase the limit."
        )

    content, title = clean_rtd_markdown(resp.text)
    content = truncate_response(content)

    result: FetchDocResult = {
        "content": content,
        "title": title,
        "tokens": tokens,
        "source_url": str(resp.url),
    }
    _page_cache.put(url, result)
    return result
```

Also update `clear_cache` to reset the Red Hat client:

```python
def clear_cache() -> None:
    """Clear the manifest, page, and Red Hat MCP client caches."""
    global _redhat_client, _redhat_client_lock
    _manifest_cache.clear()
    _page_cache.clear()
    _redhat_client = None
    _redhat_client_lock = None
```

- [ ] **Step 4: Update fetch_doc tool in server.py**

Modify the `fetch_doc` function in `src/ansible_know/server.py` (around line 549):

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def fetch_doc(
    url: Annotated[str, "A docs.ansible.com or docs.redhat.com URL to fetch as markdown"],
    max_tokens: Annotated[
        int | None,
        "If set, return error instead of content when the page exceeds this token count. "
        "Checked after fetching via the x-markdown-tokens response header.",
    ] = None,
    ctx: Context | None = None,
) -> FetchDocResult | ErrorResponse:
    """Fetch a page from docs.ansible.com as clean Markdown.

    Returns documentation content ready for LLM consumption.
    Use search_docs to discover relevant page URLs, or pass a known
    docs.ansible.com URL directly. The url parameter must start with
    https://docs.ansible.com/.
    """
```

Note: only the `url` annotation changes (adds "or docs.redhat.com"). The docstring stays unchanged because MCP tool descriptions come from the function signature; callers see the annotation. Alternatively, update the docstring too:

```python
    """Fetch a page from docs.ansible.com or docs.redhat.com as clean Markdown.

    Returns documentation content ready for LLM consumption.
    Use search_docs to discover relevant page URLs, or pass a known
    docs.ansible.com or docs.redhat.com URL directly. The url parameter must
    start with https://docs.ansible.com/ or https://docs.redhat.com/.
    """
```

- [ ] **Step 5: Run all Task 3 tests**

```bash
.venv/bin/pytest tests/test_docs.py::TestFetchDocRedhat -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/integration
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/docs.py src/ansible_know/server.py tests/test_docs.py
git commit -m "feat: extend fetch_doc to support docs.redhat.com URLs

Branch in fetch_doc_content routes docs.redhat.com URLs through
RedHatDocsClient (MCP server) instead of direct httpx. Returns the
same FetchDocResult shape. Updates fetch_doc tool annotation to
advertise docs.redhat.com support.

Assisted-by: Claude Opus 4.6"
```

---

### Task 4: AAP manifest builder and manifest generation

**Files:**
- Create: `scripts/build_aap_manifests.py`
- Create: `src/ansible_know/data/aap_25_manifest.json` (generated)
- Create: `src/ansible_know/data/aap_26_manifest.json` (generated)
- Create: `src/ansible_know/data/aap_27_manifest.json` (generated)
- Modify: `tests/test_doc_manifests.py` — add tests for AAP manifest loading

**Interfaces:**
- Consumes: `RedHatDocsClient` from `redhat_docs.py`, `write_manifest()` from `manifest_builder.py`
- Produces: Three v2.0 manifest JSON files in `src/ansible_know/data/`. Build script runnable via `.venv/bin/python scripts/build_aap_manifests.py`.

- [ ] **Step 1: Write the manifest builder script**

```python
# scripts/build_aap_manifests.py
"""Build AAP documentation manifests from the Red Hat Documentation MCP server.

Fetches each AAP version's landing page via the MCP server's
redhat_docs_fetch tool, extracts the structured guide list from
categoryTitles JSON, and outputs v2.0 manifest JSON files.

Usage:
    .venv/bin/python scripts/build_aap_manifests.py

Outputs to src/ansible_know/data/aap_{25,26,27}_manifest.json.
Requires network access to docs-mcp.api.redhat.com.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ansible_know.redhat_docs import RedHatDocsClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MANIFEST_VERSION = "2.0"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "ansible_know" / "data"

AAP_BASE_URL = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform"

AAP_VERSIONS = {
    "2.5": {"landing": f"{AAP_BASE_URL}/2.5", "output": "aap_25_manifest.json"},
    "2.6": {"landing": f"{AAP_BASE_URL}/2.6", "output": "aap_26_manifest.json"},
    "2.7": {"landing": f"{AAP_BASE_URL}/2.7", "output": "aap_27_manifest.json"},
}

CATEGORY_TOPIC_MAP = {
    "what's new": "whats_new",
    "technology preview": "tech_preview",
    "get started": "get_started",
    "plan": "plan",
    "install": "install",
    "extend": "extend",
    "upgrade": "upgrade",
    "migrate": "migrate",
    "secure": "security",
    "administer": "admin",
    "develop": "develop",
    "configure": "configure",
    "integrate": "integrate",
    "observe": "observability",
    "optimize": "performance",
    "troubleshoot": "troubleshoot",
    "reference": "reference",
    "download pdf": "reference",
    "discover": "overview",
}

CORE_TOPICS = frozenset({
    "install", "configure", "upgrade", "troubleshoot", "get_started",
    "admin", "security", "overview",
})

SKIP_CATEGORIES = frozenset({"download pdf"})


def _derive_topic_from_slug(slug: str) -> str:
    """Extract topic from a 2.6/2.7 slug prefix (e.g., 'install-proc_...' -> 'install')."""
    if "-" in slug:
        prefix = slug.split("-", 1)[0].lower()
        return CATEGORY_TOPIC_MAP.get(prefix, prefix)
    return "general"


def _parse_landing_json(raw: str) -> dict:
    """Parse landing page MCP response into structured data."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Landing page response is not valid JSON")
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        obj = json.loads(obj["result"])
    return obj


def _build_manifest_entries(landing: dict, version: str) -> list[dict]:
    """Convert landing page categoryTitles to manifest file entries."""
    entries = []
    category_titles = landing.get("categoryTitles", {})

    for category, block in category_titles.items():
        category_lower = category.strip().lower()
        if category_lower in SKIP_CATEGORIES:
            continue

        topic = CATEGORY_TOPIC_MAP.get(category_lower, category_lower.replace(" ", "_"))

        for title_info in block.get("titles", []):
            url = title_info.get("url", "")
            name = title_info.get("name", "").strip()
            description = (title_info.get("description") or "").strip()

            if not url or "/documentation/" not in url:
                continue

            path_parts = url.rstrip("/").rsplit("/", 1)
            slug = path_parts[-1] if len(path_parts) > 1 else ""

            entries.append({
                "path": slug,
                "topic": topic,
                "title": name,
                "audience": "admin",
                "core": topic in CORE_TOPICS,
                "summary": description if description else name,
                "lines": 0,
                "tokens": 0,
                "aap_version": version,
            })

    return entries


async def build_one_manifest(client: RedHatDocsClient, version: str, config: dict) -> None:
    """Build and write one AAP version manifest."""
    logger.info("Fetching landing page for AAP %s...", version)
    raw = await client.fetch(config["landing"])
    landing = _parse_landing_json(raw)

    product = landing.get("product", "unknown")
    found_version = landing.get("version", version)
    logger.info("Product: %s, version: %s", product, found_version)

    entries = _build_manifest_entries(landing, version)
    logger.info("Found %d guide entries for AAP %s", len(entries), version)

    manifest = {
        "version": MANIFEST_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "base_url": config["landing"],
        "files": entries,
    }

    output_path = OUTPUT_DIR / config["output"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    logger.info("Wrote %s (%d entries)", output_path.name, len(entries))


async def main() -> int:
    client = RedHatDocsClient()
    try:
        for version, config in AAP_VERSIONS.items():
            await build_one_manifest(client, version, config)
    finally:
        await client.close()

    logger.info("Done. Generated %d manifest files.", len(AAP_VERSIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run the build script to generate manifests**

```bash
.venv/bin/python scripts/build_aap_manifests.py
```

Expected output:
```
INFO: Fetching landing page for AAP 2.5...
INFO: Product: Red Hat Ansible Automation Platform, version: 2.5
INFO: Found 38 guide entries for AAP 2.5
INFO: Wrote aap_25_manifest.json (38 entries)
INFO: Fetching landing page for AAP 2.6...
...
```

**If the MCP server is unreachable** (VPN requirement), the script will fail with a connection error. In that case:
1. Note the error for documentation
2. Use the pre-fetched landing JSON from `tmp/` if available to build manifests offline
3. Or manually construct manifests from the slug lists in the research document

- [ ] **Step 3: Verify manifest structure**

```bash
.venv/bin/python -c "
import json, sys
for name in ['aap_25', 'aap_26', 'aap_27']:
    path = f'src/ansible_know/data/{name}_manifest.json'
    with open(path) as f:
        m = json.load(f)
    print(f'{name}: version={m[\"version\"]}, entries={len(m[\"files\"])}')
    if m['files']:
        e = m['files'][0]
        print(f'  first: {e[\"title\"][:50]} (topic={e[\"topic\"]})')
"
```

Expected: version=2.0, entry counts approximately 38/45-53/50

- [ ] **Step 4: Write failing tests for AAP manifest loading**

In `tests/test_doc_manifests.py`, first add AAP entries to `MINIMUM_COUNTS` (after the existing entries):

```python
MINIMUM_COUNTS = {
    "ansible_core_manifest.json": 300,
    "ansible_lint_manifest.json": 40,
    "molecule_manifest.json": 15,
    "ansible_builder_manifest.json": 5,
    "ansible_navigator_manifest.json": 5,
    "ansible_creator_manifest.json": 3,
    "aap_25_manifest.json": 30,
    "aap_26_manifest.json": 40,
    "aap_27_manifest.json": 40,
}
```

Then append the new test class:

```python
class TestAapManifests:
    """Tests verifying AAP manifests load and are searchable."""

    @pytest.mark.asyncio
    async def test_aap_25_manifest_loads(self):
        results = await search_docs("installation", source="aap-2.5")
        install_titles = [r["title"].lower() for r in results]
        assert any("install" in t for t in install_titles), f"No install results in {install_titles}"

    @pytest.mark.asyncio
    async def test_aap_26_manifest_loads(self):
        results = await search_docs("installation", source="aap-2.6")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_aap_27_manifest_loads(self):
        results = await search_docs("installation", source="aap-2.7")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_aap_search_returns_redhat_urls(self):
        results = await search_docs("automation mesh", source="aap-2.5")
        for r in results:
            assert r["source"] == "aap-2.5"
            if r["url"]:
                assert "docs.redhat.com" in r["url"]

    @pytest.mark.asyncio
    async def test_aap_source_filter_works(self):
        results_25 = await search_docs("install", source="aap-2.5")
        results_27 = await search_docs("install", source="aap-2.7")
        urls_25 = {r["url"] for r in results_25}
        urls_27 = {r["url"] for r in results_27}
        assert urls_25 != urls_27 or (not urls_25 and not urls_27)

    @pytest.mark.asyncio
    async def test_aap_cross_version_search(self):
        """Searching without source filter returns results from multiple AAP versions."""
        results = await search_docs("install containerized")
        sources = {r["source"] for r in results}
        aap_sources = {s for s in sources if s.startswith("aap-")}
        assert len(aap_sources) >= 1
```

- [ ] **Step 5: Run manifest tests**

```bash
.venv/bin/pytest tests/test_doc_manifests.py::TestAapManifests -v
```

Expected: all PASS (manifests exist from step 2)

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/integration
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/build_aap_manifests.py src/ansible_know/data/aap_25_manifest.json src/ansible_know/data/aap_26_manifest.json src/ansible_know/data/aap_27_manifest.json tests/test_doc_manifests.py
git commit -m "feat: add AAP 2.5/2.6/2.7 documentation manifests

Ship static v2.0 manifests built from the Red Hat Documentation MCP
server landing pages. Enables search_docs for AAP product docs with
source-based version filtering (source='aap-2.7').

Includes build script at scripts/build_aap_manifests.py for future
manifest regeneration.

Closes #TBD

Assisted-by: Claude Opus 4.6"
```

---

### Task 5: Integration tests and final polish

**Files:**
- Create: `tests/integration/test_redhat_docs_live.py`
- Modify: `src/ansible_know/server.py` — update `search_docs` description (if not already done)

**Interfaces:**
- Consumes: `RedHatDocsClient`, `fetch_doc_content`, `search_docs`
- Produces: Integration test file (skipped by default, needs `--run-integration`)

- [ ] **Step 1: Write integration tests**

```python
# tests/integration/test_redhat_docs_live.py
"""Integration tests for Red Hat Documentation MCP server.

These tests hit the live MCP server at docs-mcp.api.redhat.com.
Skipped by default — run with: pytest --run-integration

May require VPN access to docs-mcp.api.redhat.com.
"""

from __future__ import annotations

import json

import pytest

from ansible_know.docs import fetch_doc_content, search_docs
from ansible_know.redhat_docs import RedHatDocsClient

pytestmark = pytest.mark.integration


class TestRedHatDocsClientLive:
    """Live tests against the Red Hat Documentation MCP server."""

    @pytest.mark.asyncio
    async def test_fetch_aap_27_landing(self):
        client = RedHatDocsClient()
        try:
            raw = await client.fetch(
                "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7"
            )
            data = json.loads(raw)
            assert "categoryTitles" in data
            assert data.get("product", "").startswith("Red Hat Ansible")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_fetch_aap_27_guide_returns_markdown(self):
        client = RedHatDocsClient()
        try:
            raw = await client.fetch(
                "https://docs.redhat.com/en/documentation/"
                "red_hat_ansible_automation_platform/2.7/"
                "install-proc_installing_containerized_aap"
            )
            assert raw.strip().startswith("#") or "install" in raw.lower()
            assert len(raw) > 100
        finally:
            await client.close()


class TestFetchDocRedhatLive:
    """Live tests for fetch_doc_content with docs.redhat.com URLs."""

    @pytest.mark.asyncio
    async def test_fetch_aap_doc_returns_result(self):
        result = await fetch_doc_content(
            "https://docs.redhat.com/en/documentation/"
            "red_hat_ansible_automation_platform/2.7/"
            "install-proc_installing_containerized_aap"
        )
        assert "content" in result
        assert "title" in result
        assert len(result["content"]) > 100


class TestSearchDocsAapLive:
    """Live tests for search_docs with AAP manifests."""

    @pytest.mark.asyncio
    async def test_search_aap_install(self):
        results = await search_docs("install", source="aap-2.7")
        assert len(results) > 0
        assert all(r["source"] == "aap-2.7" for r in results)
```

- [ ] **Step 2: Verify integration tests are skipped by default**

```bash
.venv/bin/pytest tests/integration/test_redhat_docs_live.py -v
```

Expected: all tests SKIPPED (no `--run-integration` flag)

- [ ] **Step 3: Run integration tests (if on VPN)**

```bash
.venv/bin/pytest tests/integration/test_redhat_docs_live.py -v --run-integration
```

Expected: PASS (if MCP server is reachable)

- [ ] **Step 4: Run full test suite one final time**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/integration
```

Expected: all PASS

- [ ] **Step 5: Lint all changed files**

```bash
.venv/bin/ruff check src/ansible_know/redhat_docs.py src/ansible_know/text_utils.py src/ansible_know/config.py src/ansible_know/validation.py src/ansible_know/docs.py src/ansible_know/server.py tests/test_redhat_docs.py tests/test_docs.py tests/test_validation.py tests/test_config.py tests/test_doc_manifests.py scripts/build_aap_manifests.py
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_redhat_docs_live.py
git commit -m "test: add integration tests for Red Hat Documentation MCP

Live tests for RedHatDocsClient, fetch_doc with docs.redhat.com,
and AAP manifest search. Skipped by default (--run-integration).

Assisted-by: Claude Opus 4.6"
```

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| MCP server requires VPN | Medium | Blocks manifest build + integration tests + runtime fetch_doc | Add `ANSIBLE_KNOW_REDHAT_DOCS_MCP_URL` env var; document VPN requirement; manifests work offline once built |
| MCP server intermittent 404s | High (30-40%) | Fetch failures | Built-in retry with session re-init (3 attempts) |
| 8/53 AAP 2.6 guides broken on MCP | Confirmed | Missing guides from manifest | Omit broken entries; log during build; report upstream to DocX team |
| Akamai bot protection on docs.redhat.com | Low | Direct HTTP fallback blocked | We use MCP server which bypasses this; if MCP is down, fail gracefully |
| MCP server API changes | Low | Client breaks | Pin protocol version; `parse_mcp_sse` handles unknown fields gracefully |
| Large token consumption for guide pages | Medium | Context window pressure | `max_tokens` param on `fetch_doc`; `_estimate_tokens` provides rough count |

## Open Items for Future Work

- **`redhat_docs_search` as live fallback**: similar to RTD Search API fallback. Limited (2 results max, no version filter, no 2.6/2.7 indexing) — defer until Red Hat improves it.
- **KCS integration**: `mcp-redhat-knowledge` for KB articles/solutions. Requires auth (`REDHAT_TOKEN`). Good enrichment source but out of scope for v1.
- **Manifest rebuild automation**: CI job to regenerate manifests on AAP GA releases. Low priority — AAP docs are stable between releases.
- **MCP server for Red Hat knowledge**: When this official server ships, evaluate as replacement for our static manifests + MCP client approach.

---

## Review Findings (2026-07-16)

Independent review of this plan before implementation. Issues are categorized by severity with specific fixes.

**Second pass (2026-07-16):** Verified each finding against source code, confirmed/rejected, applied fixes to plan inline.

### Critical

**1. ~~Landing page JSON passed to `clean_redhat_markdown` produces garbage (Task 3)~~** — VERIFIED, FIXED

Traced the full flow: landing URL → `client.fetch()` → `_unwrap_mcp_result` returns JSON string → `clean_redhat_markdown` tries H1 extraction on JSON → garbage output.

**Applied:** Added JSON detection guard in `_fetch_redhat_doc` (Task 3, Step 3) and a `test_redhat_landing_page_raises_helpful_error` test (Task 3, Step 1).

### Important

**2. ~~`test_ansible_url_still_uses_direct_httpx` is slow and flaky (Task 3, Step 1)~~** — VERIFIED, FIXED

Creates real `httpx.AsyncClient`, makes real network request that times out. Fixed by mocking `_fetch_with_retry` with `side_effect=httpx.ConnectError("mocked")` in Task 3, Step 1.

**3. `_H1_RE` regex untested with real MCP output (Task 1)** — VERIFIED, LOW RISK

The plan's `clean_redhat_markdown` strips "Copy link" artifacts BEFORE H1 extraction, so the regex works correctly. Still worth adding a real-output fixture during implementation to confirm.

**4. ~~`_RH_COPY_LINK_RE` lacks line anchoring (Task 1, Step 11)~~** — VERIFIED, FIXED

Without `$` anchor, could strip "Copy link" from legitimate instructions. Fixed: added `$` and `re.MULTILINE` in Task 1, Step 11.

**5. ~~`_redhat_client` singleton has no async cleanup on `clear_cache()` (Task 3)~~** — REJECTED

KEEP the reset. The MCP client's session expires server-side (the documented 404 behavior), so the client IS stale cached state. `clear_cache` is the right place to reset it. The httpx `AsyncClient` connection pool is cleaned up by GC — negligible concern on a rarely-called path.

**6. ~~Concurrent `_get_redhat_client()` race condition (Task 3)~~** — NOT A REAL ISSUE, FIXED DEFENSIVELY

No `await` between the `is None` check and `RedHatDocsClient()` assignment — asyncio guarantees atomicity here. However, added `asyncio.Lock` in Task 3 matching the existing `_doc_throttle_lock` pattern as defensive practice.

**7. ~~Build script `sys.path.insert` is fragile (Task 4, Step 1)~~** — VERIFIED, FIXED

`manifest_builder.py` has no such hack — relies on editable install. Removed `sys.path.insert` and `import sys` from the build script.

**8. ~~Missing `MINIMUM_COUNTS` for AAP manifests (Task 4)~~** — VERIFIED, FIXED

Added `MINIMUM_COUNTS` entries (`aap_25: 30, aap_26: 40, aap_27: 40`) to Task 4, Step 4. Existing parametric tests will automatically validate AAP manifests.

### Minor

**9. ~~`test_ignores_non_data_lines` has misleading name (Task 1, Step 1)~~** — VERIFIED, FIXED

Split into two focused tests: `test_returns_none_when_data_has_no_result_or_error` and `test_skips_non_data_sse_lines`.

**10. ~~`_estimate_tokens` should document its approximation (Task 3)~~** — VERIFIED, FIXED

Expanded docstring to explain WHY the approximation is used (no `x-markdown-tokens` header from MCP).

**11. ~~Stripping "Abstract" section may lose useful context (Task 1, Step 11)~~** — VERIFIED, FIXED

Removed "abstract" from `_RH_SKIP_SECTIONS`. Abstract sections in Red Hat docs contain useful one-sentence summaries worth keeping.

**12. Consider extracting `_fetch_ansible_doc` for symmetry (Task 3)** — DEFERRED

Valid improvement but optional. The inline docs.ansible.com code in `fetch_doc_content` is the existing code moved into the else branch — extracting it is a refactoring decision for the implementer.
