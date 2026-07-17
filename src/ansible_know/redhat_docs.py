"""Async client for the Red Hat Documentation MCP server.

Uses JSON-RPC over Streamable HTTP to fetch docs.redhat.com pages as
markdown via the ``redhat_docs_fetch`` tool. Sessions are initialized
lazily and re-created on 404 (server-side session expiry).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx

from ansible_know.config import REDHAT_DOCS_MCP_URL, USER_AGENT
from ansible_know.errors import AnsibleKnowError
from ansible_know.text_utils import clean_redhat_markdown
from ansible_know.types import FetchDocResult
from ansible_know.validation import sanitize_error, truncate_response

logger = logging.getLogger("ansible_know")

__all__ = [
    "RedHatDocsClient",
    "clear_redhat_client",
    "fetch_redhat_doc",
    "parse_mcp_sse",
]

_MCP_PROTOCOL_VERSION = "2024-11-05"
_MAX_RETRIES = 3
_MAX_RESPONSE_SIZE = 2_000_000  # 2MB


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
        if self._client is None:
            raise AnsibleKnowError("RedHatDocsClient not connected")
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

        if len(resp.content) > _MAX_RESPONSE_SIZE:
            raise AnsibleKnowError(
                f"MCP response too large: {len(resp.content)} bytes "
                f"(max {_MAX_RESPONSE_SIZE})"
            )

        parsed = parse_mcp_sse(resp.text)
        if parsed is None:
            raise AnsibleKnowError(f"No valid JSON-RPC response from MCP server for {name}")
        if "error" in parsed:
            msg = parsed["error"].get("message", str(parsed["error"]))
            raise AnsibleKnowError(f"MCP tool {name} error: {sanitize_error(msg)}")

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


_redhat_client: RedHatDocsClient | None = None
_redhat_client_lock: asyncio.Lock | None = None


async def _get_redhat_client() -> RedHatDocsClient:
    """Lazily create and return the shared RedHatDocsClient."""
    global _redhat_client, _redhat_client_lock
    if _redhat_client is not None:
        return _redhat_client
    if _redhat_client_lock is None:
        _redhat_client_lock = asyncio.Lock()
    async with _redhat_client_lock:
        if _redhat_client is None:
            _redhat_client = RedHatDocsClient()
    return _redhat_client


def clear_redhat_client() -> None:
    """Reset the shared RedHatDocsClient singleton."""
    global _redhat_client, _redhat_client_lock
    _redhat_client = None
    _redhat_client_lock = None


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for Red Hat docs.

    The Red Hat MCP server provides no x-markdown-tokens header, unlike
    docs.ansible.com's Cloudflare endpoint. This approximation is
    intentionally rough — used only for max_tokens gating.
    """
    return len(text) // 4


async def fetch_redhat_doc(
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
