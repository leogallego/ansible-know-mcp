"""Async client for the Red Hat Documentation MCP server.

Uses JSON-RPC over Streamable HTTP to fetch docs.redhat.com pages as
markdown via the ``redhat_docs_fetch`` tool. Sessions are initialized
lazily and re-created on 404 (server-side session expiry).

When the upstream MCP server rejects a URL as invalid (notably AAP 2.6/2.7
modular guide slugs), ``fetch_redhat_doc`` falls back to a direct HTTP
fetch of docs.redhat.com and converts the HTML article to markdown.
"""

from __future__ import annotations

import html
import json
import logging
import re
import uuid
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from ansible_know.config import REDHAT_DOCS_MCP_URL, USER_AGENT
from ansible_know.errors import AnsibleKnowError
from ansible_know.text_utils import clean_redhat_markdown
from ansible_know.types import FetchDocResult
from ansible_know.validation import sanitize_error, truncate_response

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
_MAX_DOC_FETCH_SIZE = 2_000_000  # 2MB — aligned with docs.fetch_doc_content
_RH_DOC_HOST = "docs.redhat.com"

# Narrow trigger for HTTP fallback: upstream URL validator rejections only.
# Do not match generic transport/timeouts — MCP-down should surface as error.
_MCP_URL_REJECTION_RE = re.compile(
    r"not a valid\b.{0,80}\blink"
    r"|invalid\s+(?:red\s+hat\s+)?(?:documentation\s+)?(?:url|link)",
    re.IGNORECASE,
)
_HTML_SEGMENT_RE = re.compile(r"/(html(?:-single)?)/")
_INDEX_SUFFIX_RE = re.compile(r"/index/?$")
_ARTICLE_RE = re.compile(r"<article\b[^>]*>.*?</article>", re.IGNORECASE | re.DOTALL)
_MAIN_RE = re.compile(r"<main\b[^>]*>.*?</main>", re.IGNORECASE | re.DOTALL)


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


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for Red Hat docs.

    The Red Hat MCP server provides no x-markdown-tokens header, unlike
    docs.ansible.com's Cloudflare endpoint. This approximation is
    intentionally rough — used only for max_tokens gating.
    """
    return len(text) // 4


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


def _extract_content_html(raw_html: str) -> str:
    """Prefer ``<article>`` (then ``<main>``) for conversion; else full document."""
    match = _ARTICLE_RE.search(raw_html)
    if match:
        return match.group(0)
    match = _MAIN_RE.search(raw_html)
    if match:
        return match.group(0)
    return raw_html


class _HtmlToMarkdownParser(HTMLParser):
    """Minimal HTML→Markdown converter for Red Hat docs article markup."""

    _SKIP_TAGS = frozenset({
        "script", "style", "nav", "header", "footer", "aside",
        "button", "svg", "noscript", "form", "rh-button", "rh-icon",
        "rh-surface", "rh-breadcrumb", "pf-popover",
    })
    _BLOCK_TAGS = frozenset({
        "p", "div", "section", "li", "tr", "blockquote", "pre",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
        "article", "main", "br", "hr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_pre = False
        self._list_stack: list[str] = []  # "ul" | "ol"
        self._ol_index: list[int] = []
        self._href: str | None = None
        self._pending_href_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in self._SKIP_TAGS:
                self._skip_depth += 1
            return
        if tag in self._SKIP_TAGS:
            self._skip_depth = 1
            return

        attr_map = {k.lower(): (v or "") for k, v in attrs}

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_blank_line()
            self._parts.append("#" * int(tag[1]) + " ")
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "hr":
            self._ensure_blank_line()
            self._parts.append("---\n\n")
        elif tag == "p":
            self._ensure_blank_line()
        elif tag in {"ul", "ol"}:
            self._ensure_blank_line()
            self._list_stack.append(tag)
            self._ol_index.append(0)
        elif tag == "li":
            self._ensure_newline()
            depth = max(len(self._list_stack) - 1, 0)
            indent = "  " * depth
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_index[-1] += 1
                self._parts.append(f"{indent}{self._ol_index[-1]}. ")
            else:
                self._parts.append(f"{indent}- ")
        elif tag == "pre":
            self._ensure_blank_line()
            self._parts.append("```\n")
            self._in_pre = True
        elif tag == "code" and not self._in_pre:
            self._parts.append("`")
        elif tag in {"strong", "b"}:
            self._parts.append("**")
        elif tag in {"em", "i"}:
            self._parts.append("*")
        elif tag == "a":
            href = attr_map.get("href", "")
            if href and not href.startswith("#"):
                self._href = href
                self._pending_href_text = []
        elif tag == "img":
            alt = attr_map.get("alt", "")
            if alt:
                self._parts.append(alt)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in self._SKIP_TAGS:
                self._skip_depth -= 1
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self._parts.append("\n\n")
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            if self._ol_index:
                self._ol_index.pop()
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n")
        elif tag == "pre":
            if not self._parts or not self._parts[-1].endswith("\n"):
                self._parts.append("\n")
            self._parts.append("```\n\n")
            self._in_pre = False
        elif tag == "code" and not self._in_pre:
            self._parts.append("`")
        elif tag in {"strong", "b"}:
            self._parts.append("**")
        elif tag in {"em", "i"}:
            self._parts.append("*")
        elif tag == "a" and self._href is not None:
            label = "".join(self._pending_href_text).strip() or self._href
            self._parts.append(f"[{label}]({self._href})")
            self._href = None
            self._pending_href_text = []
        elif tag in self._BLOCK_TAGS:
            self._ensure_newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._href is not None:
            self._pending_href_text.append(data)
            return
        if self._in_pre:
            self._parts.append(data)
            return
        # Collapse runs of whitespace outside pre/code blocks
        if not data.strip():
            if data and self._parts and not self._parts[-1].endswith(("\n", " ", "\t")):
                self._parts.append(" ")
            return
        collapsed = re.sub(r"[ \t\r\n]+", " ", data)
        self._parts.append(collapsed)

    def _ensure_newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def _ensure_blank_line(self) -> None:
        if not self._parts:
            return
        text = "".join(self._parts)
        if not text.endswith("\n\n"):
            if text.endswith("\n"):
                self._parts.append("\n")
            else:
                self._parts.append("\n\n")

    def get_markdown(self) -> str:
        return "".join(self._parts)


def html_to_markdown(raw_html: str) -> str:
    """Convert Red Hat docs HTML to markdown (stdlib HTMLParser, no extra deps)."""
    if not raw_html:
        return ""
    fragment = _extract_content_html(raw_html)
    parser = _HtmlToMarkdownParser()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        # Best-effort: fall back to stripped text rather than failing the fetch.
        logger.debug("HTML→markdown parse failed; using stripped text fallback", exc_info=True)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", fragment)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return html.unescape(re.sub(r"\s+", " ", text)).strip()
    return parser.get_markdown()


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
        "source_url": source_url,
    }


async def _http_get_redhat_doc(
    client: httpx.AsyncClient,
    url: str,
) -> httpx.Response:
    """GET a docs.redhat.com page, rewriting /html/ paths on 404 when needed."""
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "User-Agent": USER_AGENT,
    }
    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
    if resp.url.host != _RH_DOC_HOST:
        raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")

    if resp.status_code == 404:
        alternate = _alternate_redhat_doc_url(url)
        if alternate and alternate != str(resp.url) and alternate != url:
            logger.info(
                "docs.redhat.com returned 404 for %s; retrying rewritten URL %s",
                url, alternate,
            )
            resp = await client.get(
                alternate, headers=headers, follow_redirects=True, timeout=30.0,
            )
            if resp.url.host != _RH_DOC_HOST:
                raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")

    return resp


async def fetch_redhat_doc_http(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult:
    """Fetch a docs.redhat.com page over HTTP and convert HTML to markdown.

    Used as a workaround when the Red Hat Documentation MCP server rejects
    a URL (AAP 2.6/2.7 modular slugs). Stays on docs.redhat.com only.
    """
    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        should_close = True

    try:
        resp = await _http_get_redhat_doc(client, url)
    finally:
        if should_close:
            await client.aclose()

    if resp.url.host != _RH_DOC_HOST:
        raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")

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

    markdown = html_to_markdown(resp.text)
    if not markdown.strip():
        raise AnsibleKnowError(f"No convertible documentation content found at {url}")

    return _finalize_doc_result(markdown, str(resp.url), max_tokens)


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
