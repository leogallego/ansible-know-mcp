"""Async client for the Red Hat Documentation MCP server.

Uses JSON-RPC over Streamable HTTP to fetch docs.redhat.com pages as
markdown via the ``redhat_docs_fetch`` tool. Sessions are initialized
lazily and re-created on 404 (server-side session expiry).

When the upstream MCP server rejects a URL as invalid (notably AAP 2.6/2.7
modular guide slugs), ``fetch_redhat_doc`` falls back to a direct HTTP
fetch of docs.redhat.com and converts the HTML article to markdown.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from ansible_know.config import REDHAT_DOCS_MCP_URL, USER_AGENT
from ansible_know.errors import AnsibleKnowError, ValidationError
from ansible_know.text_utils import (
    clean_redhat_markdown,
    estimate_tokens,
    html_to_markdown,
)
from ansible_know.types import FetchDocResult
from ansible_know.validation import sanitize_error, truncate_response, validate_doc_url

logger = logging.getLogger("ansible_know")

__all__ = [
    "RedHatDocsClient",
    "fetch_redhat_doc",
    "fetch_redhat_doc_http",
    "html_to_markdown",
    "parse_mcp_sse",
]

_MCP_PROTOCOL_VERSION = "2024-11-05"
_MAX_RETRIES = 3
_MAX_RESPONSE_SIZE = 2_000_000  # 2MB
# HTML pages are larger than Cloudflare markdown; modular AAP guides are ~1MB,
# but some html-single books exceed 2MB. Cap below manifests (5MB).
_MAX_DOC_FETCH_SIZE = 5_000_000
_RH_DOC_HOST = "docs.redhat.com"

# Narrow trigger for HTTP fallback: upstream URL validator rejections only.
# Do not match generic "Invalid URL" / unrelated "not a valid … link" text.
_MCP_URL_REJECTION_RE = re.compile(
    r"not a valid(?:\s+red\s+hat)?\s+documentation\s+link\b"
    r"|invalid\s+(?:red\s+hat\s+)?documentation\s+(?:url|link)\b",
    re.IGNORECASE,
)
_HTML_SEGMENT_RE = re.compile(r"/(html(?:-single)?)/")
_INDEX_SUFFIX_RE = re.compile(r"/index/?$")
# Soft-404 title/H1 only — must not match troubleshooting prose mentioning 404.
_SOFT_404_TITLE_RE = re.compile(
    r"^#\s*404\b.{0,40}\bpage not found\b",
    re.IGNORECASE,
)
_HTML_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_HTML_SOFT_404_H1_RE = re.compile(
    r"<h1[^>]*>\s*404\b.{0,40}page\s+not\s+found",
    re.IGNORECASE,
)


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

        result = parsed.get("result", {})
        content_blocks = result.get("content", [])
        raw = "".join(
            block.get("text", "")
            for block in content_blocks
            if block.get("type") == "text"
        )
        if result.get("isError"):
            raise AnsibleKnowError(
                f"MCP tool {name} error: {sanitize_error(raw or 'unknown error')}"
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


def _is_mcp_url_rejection(exc: BaseException) -> bool:
    """Return True when *exc* is an upstream RH Docs MCP URL-validation error."""
    if not isinstance(exc, AnsibleKnowError):
        return False
    return bool(_MCP_URL_REJECTION_RE.search(str(exc)))


def _is_url_rejection_message(text: str) -> bool:
    """Return True when MCP returned a short URL-rejection string as content."""
    stripped = text.strip()
    if not stripped or len(stripped) > 300:
        return False
    return bool(_MCP_URL_REJECTION_RE.search(stripped))


def _is_soft_404_markdown(markdown: str) -> bool:
    """Return True when the first markdown H1 is a not-found title.

    Ignores leading non-heading noise (e.g. decorative image alt text that
    RH soft-404 pages emit before ``# 404: Page not found``).
    """
    match = re.search(r"^#\s+.+$", markdown, re.MULTILINE)
    if match is None:
        return False
    return bool(_SOFT_404_TITLE_RE.match(match.group(0)))


def _html_looks_soft_404(html: str) -> bool:
    """Cheap pre-convert check for RH SPA / soft not-found pages.

    Title must *start* with ``Page not found`` (RH soft-404 shape), not merely
    mention those words in a troubleshooting guide title.
    """
    head = html[:50_000]
    title = _HTML_TITLE_RE.search(head)
    if title and re.match(r"\s*page\s+not\s+found\b", title.group(1), re.IGNORECASE):
        return True
    return bool(_HTML_SOFT_404_H1_RE.search(head))


def _alternate_redhat_doc_url(url: str) -> str | None:
    """Return a rewritten docs.redhat.com URL without /html/ or /html-single/.

    AAP 2.6/2.7 modular guides are served at ``/{version}/{slug}``. Manifest
    entries often still include a ``/html/`` segment that 404s for 2.7 (2.6
    usually redirects). Returns None when no rewrite applies.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != _RH_DOC_HOST:
        return None
    path = _HTML_SEGMENT_RE.sub("/", parsed.path, count=1)
    path = _INDEX_SUFFIX_RE.sub("", path)
    if path == parsed.path:
        return None
    return urlunparse(parsed._replace(path=path or "/"))


def _finalize_doc_result(
    raw_markdown: str,
    source_url: str,
    max_tokens: int | None,
) -> FetchDocResult:
    """Clean markdown, gate tokens, and build FetchDocResult."""
    if raw_markdown.lstrip().startswith("{"):
        try:
            json.loads(raw_markdown)
            raise AnsibleKnowError(
                "URL appears to be a landing page, not a guide page. "
                "Use search_docs to find specific guide URLs."
            )
        except json.JSONDecodeError:
            pass

    content, title = clean_redhat_markdown(raw_markdown)
    content = truncate_response(content)
    tokens = estimate_tokens(content)

    if max_tokens is not None and tokens > max_tokens:
        raise AnsibleKnowError(
            f"Page has ~{tokens} tokens (max_tokens={max_tokens}). "
            f"Fetch without max_tokens or increase the limit."
        )

    return {
        "content": content,
        "title": title,
        "tokens": tokens,
        "source_url": source_url,
    }


def _assert_redhat_host(resp: httpx.Response) -> None:
    if resp.url.host != _RH_DOC_HOST:
        raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")


async def _http_get_once(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
    _assert_redhat_host(resp)
    return resp


async def _http_get_redhat_doc(
    client: httpx.AsyncClient,
    url: str,
) -> httpx.Response:
    """GET a docs.redhat.com page, rewriting /html/ on status or soft 404."""
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "User-Agent": USER_AGENT,
    }
    resp = await _http_get_once(client, url, headers)

    needs_rewrite = resp.status_code == 404 or (
        resp.status_code == 200 and _html_looks_soft_404(resp.text)
    )
    if not needs_rewrite:
        return resp

    alternate = _alternate_redhat_doc_url(url)
    if not alternate or alternate == url or alternate == str(resp.url):
        return resp

    logger.info(
        "docs.redhat.com returned not-found for %s; retrying rewritten URL %s",
        url, alternate,
    )
    return await _http_get_once(client, alternate, headers)


def _validate_redhat_http_response(resp: httpx.Response, url: str) -> None:
    _assert_redhat_host(resp)

    if len(resp.content) > _MAX_DOC_FETCH_SIZE:
        raise AnsibleKnowError(
            f"Response too large: {len(resp.content)} bytes (max {_MAX_DOC_FETCH_SIZE})"
        )

    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AnsibleKnowError(
            f"docs.redhat.com returned HTTP {exc.response.status_code} for {url}"
        ) from exc

    content_type = resp.headers.get("content-type", "")
    body_start = resp.text.lstrip()[:64].lower()
    looks_like_html = body_start.startswith("<!doctype") or body_start.startswith("<html")
    if (
        "text/html" not in content_type
        and "application/xhtml" not in content_type
        and not looks_like_html
    ):
        raise AnsibleKnowError(
            f"Expected HTML from docs.redhat.com but got {content_type!r} for {url}"
        )


async def fetch_redhat_doc_http(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult:
    """Fetch a docs.redhat.com page over HTTP and convert HTML to markdown.

    Used as a workaround when the Red Hat Documentation MCP server rejects
    a URL (AAP 2.6/2.7 modular slugs). Stays on docs.redhat.com only.
    """
    try:
        validate_doc_url(url)
    except ValidationError as exc:
        raise AnsibleKnowError(str(exc)) from exc
    if urlparse(url).netloc != _RH_DOC_HOST:
        raise AnsibleKnowError(
            "URL must start with https://docs.redhat.com/ for HTTP fallback"
        )

    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        should_close = True

    try:
        resp = await _http_get_redhat_doc(client, url)
        _validate_redhat_http_response(resp, url)

        markdown = html_to_markdown(resp.text)
        if not markdown.strip():
            raise AnsibleKnowError(f"No convertible documentation content found at {url}")
        # Prefer HTML title/H1 check (survives leading img alts); markdown is backup.
        if _html_looks_soft_404(resp.text) or _is_soft_404_markdown(markdown):
            raise AnsibleKnowError(
                f"docs.redhat.com returned a not-found page for {url}"
            )

        return _finalize_doc_result(markdown, str(resp.url), max_tokens)
    finally:
        if should_close:
            await client.aclose()


async def fetch_redhat_doc(
    url: str,
    max_tokens: int | None = None,
    client: RedHatDocsClient | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult:
    """Fetch a docs.redhat.com page via MCP, with HTTP fallback on URL rejection.

    Prefers the Red Hat Documentation MCP server (works for AAP 2.5 and other
    traditional guide URLs). When MCP rejects the URL as invalid — the known
    upstream failure for AAP 2.6/2.7 modular slugs — falls back to a direct
    HTTP fetch + HTML→markdown conversion.

    The caller (Orchestration layer) owns client lifecycles — MCP client via
    SharedState, optional shared httpx.AsyncClient from lifespan.
    """
    if client is None:
        raise AnsibleKnowError("RedHatDocsClient is required — pass via SharedState")

    try:
        raw = await client.fetch(url)
    except AnsibleKnowError as exc:
        if not _is_mcp_url_rejection(exc):
            raise
        logger.warning(
            "Red Hat Docs MCP rejected URL (upstream URL validation); "
            "falling back to direct HTTP fetch: %s",
            url,
        )
        return await fetch_redhat_doc_http(
            url, max_tokens=max_tokens, http_client=http_client,
        )

    if _is_url_rejection_message(raw):
        logger.warning(
            "Red Hat Docs MCP returned URL-rejection content; "
            "falling back to direct HTTP fetch: %s",
            url,
        )
        return await fetch_redhat_doc_http(
            url, max_tokens=max_tokens, http_client=http_client,
        )

    return _finalize_doc_result(raw, url, max_tokens)


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
