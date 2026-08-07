"""Unit tests for RedHatDocsClient (mocked httpx, no network)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ansible_know.errors import AnsibleKnowError
from ansible_know.redhat_docs import (
    RedHatDocsClient,
    fetch_redhat_doc,
    fetch_redhat_doc_http,
    html_to_markdown,
    parse_mcp_sse,
)
from ansible_know.text_utils import clean_redhat_markdown


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


class TestHtmlToMarkdown:
    """Tests for stdlib HTML→markdown conversion used by HTTP fallback."""

    def test_converts_article_headings_lists_and_code(self):
        html = """
        <html><body>
        <nav>Skip me</nav>
        <article>
          <h1>Install containerized AAP</h1>
          <p>Run the <code>install</code> playbook.</p>
          <h2>Procedure</h2>
          <ol>
            <li>Go to the install directory</li>
            <li>Run:
              <pre><code>ansible-playbook -i inventory install</code></pre>
            </li>
          </ol>
          <p>See <a href="https://docs.redhat.com/en/docs">more docs</a>.</p>
          <button>Copy link</button>
        </article>
        </body></html>
        """
        md = html_to_markdown(html)
        assert "# Install containerized AAP" in md
        assert "## Procedure" in md
        assert "`install`" in md
        assert "1. Go to the install directory" in md
        assert "```" in md
        assert "ansible-playbook -i inventory install" in md
        assert "[more docs](https://docs.redhat.com/en/docs)" in md
        assert "Skip me" not in md
        assert "Copy link" not in md

    def test_empty_input(self):
        assert html_to_markdown("") == ""


def _html_response(
    body: str,
    url: str = "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install-proc_installing_containerized_aap",
    status_code: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        text=body,
        headers={"content-type": "text/html;charset=utf-8"},
        request=httpx.Request("GET", url),
    )


class TestFetchRedhatDocFallback:
    """MCP-first fetch with narrow HTTP fallback on URL validation rejection."""

    AAP25_URL = (
        "https://docs.redhat.com/en/documentation/"
        "red_hat_ansible_automation_platform/2.5/html-single/release_notes"
    )
    AAP27_URL = (
        "https://docs.redhat.com/en/documentation/"
        "red_hat_ansible_automation_platform/2.7/html/"
        "install-proc_installing_containerized_aap"
    )

    @pytest.mark.asyncio
    async def test_mcp_success_does_not_invoke_http_fallback(self):
        """AAP 2.5-style URLs that MCP accepts must not hit HTTP fallback."""
        markdown = "# Release notes\n\nAAP 2.5 content."
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(return_value=markdown)

        with patch(
            "ansible_know.redhat_docs.fetch_redhat_doc_http",
            new_callable=AsyncMock,
        ) as mock_http:
            result = await fetch_redhat_doc(self.AAP25_URL, client=mock_client)

        assert result["title"] == "Release notes"
        assert "AAP 2.5 content" in result["content"]
        mock_client.fetch.assert_awaited_once_with(self.AAP25_URL)
        mock_http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mcp_url_rejection_falls_back_to_http(self):
        """Modular AAP 2.7 URL rejected by MCP should use HTTP→markdown path."""
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            side_effect=AnsibleKnowError(
                "MCP tool redhat_docs_fetch error: "
                "Not a valid Red Hat Documentation link"
            )
        )
        article_html = """
        <article>
          <h1>Install containerized Ansible Automation Platform</h1>
          <p>Run the install playbook after preparing the host.</p>
          <h2>Legal Notice</h2>
          <p>Copyright boilerplate.</p>
          <h2>Procedure</h2>
          <p>Real steps here.</p>
        </article>
        """
        rewritten = (
            "https://docs.redhat.com/en/documentation/"
            "red_hat_ansible_automation_platform/2.7/"
            "install-proc_installing_containerized_aap"
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = AsyncMock(
            return_value=_html_response(article_html, url=rewritten),
        )

        result = await fetch_redhat_doc(
            self.AAP27_URL, client=mock_client, http_client=mock_http,
        )

        assert result["title"] == "Install containerized Ansible Automation Platform"
        assert "Real steps here" in result["content"]
        assert "Legal Notice" not in result["content"]
        assert "Copyright boilerplate" not in result["content"]
        mock_client.fetch.assert_awaited_once_with(self.AAP27_URL)
        mock_http.get.assert_awaited()

    @pytest.mark.asyncio
    async def test_mcp_rejection_content_also_triggers_fallback(self):
        """URL rejection returned as tool text (not JSON-RPC error) still falls back."""
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            return_value="Not a valid Red Hat Documentation link"
        )
        fallback_result = {
            "content": "# Guide\n\nBody.",
            "title": "Guide",
            "tokens": 3,
            "source_url": self.AAP27_URL,
        }
        with patch(
            "ansible_know.redhat_docs.fetch_redhat_doc_http",
            new_callable=AsyncMock,
            return_value=fallback_result,
        ) as mock_http:
            result = await fetch_redhat_doc(self.AAP27_URL, client=mock_client)

        assert result == fallback_result
        mock_http.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_url_mcp_errors_do_not_fallback(self):
        """Transport / generic MCP failures must not silently fall back to HTTP."""
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            side_effect=AnsibleKnowError("MCP fetch failed after 3 retries")
        )

        with patch(
            "ansible_know.redhat_docs.fetch_redhat_doc_http",
            new_callable=AsyncMock,
        ) as mock_http:
            with pytest.raises(AnsibleKnowError, match="MCP fetch failed"):
                await fetch_redhat_doc(self.AAP27_URL, client=mock_client)

        mock_http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generic_invalid_url_error_does_not_fallback(self):
        """Bare 'Invalid URL' must not trigger HTTP fallback (too broad)."""
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(side_effect=AnsibleKnowError("Invalid URL"))

        with patch(
            "ansible_know.redhat_docs.fetch_redhat_doc_http",
            new_callable=AsyncMock,
        ) as mock_http:
            with pytest.raises(AnsibleKnowError, match="Invalid URL"):
                await fetch_redhat_doc(self.AAP27_URL, client=mock_client)

        mock_http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_fallback_rejects_redirect_off_docs_redhat(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = AsyncMock(
            return_value=httpx.Response(
                200,
                text="<html><article><h1>Nope</h1></article></html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", "https://evil.example/doc"),
            )
        )

        with pytest.raises(AnsibleKnowError, match="unexpected domain"):
            await fetch_redhat_doc_http(self.AAP27_URL, http_client=mock_http)

    @pytest.mark.asyncio
    async def test_http_fallback_rejects_soft_404_page(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = AsyncMock(
            return_value=_html_response(
                "<html><main><h1>404: Page not found</h1><p>missing</p></main></html>",
                url=self.AAP27_URL,
                status_code=200,
            )
        )

        with pytest.raises(AnsibleKnowError, match="not-found page"):
            await fetch_redhat_doc_http(self.AAP27_URL, http_client=mock_http)

    @pytest.mark.asyncio
    async def test_http_fallback_rewrites_html_segment_on_404(self):
        """Manifest /html/ URLs that 404 should retry without the segment."""
        article_html = (
            "<article><h1>Install containerized AAP</h1>"
            "<p>Prepared host steps.</p></article>"
        )
        rewritten = (
            "https://docs.redhat.com/en/documentation/"
            "red_hat_ansible_automation_platform/2.7/"
            "install-proc_installing_containerized_aap"
        )
        not_found = _html_response(
            "<html><h1>404: Page not found</h1></html>",
            url=self.AAP27_URL + "/index",
            status_code=404,
        )
        ok = _html_response(article_html, url=rewritten, status_code=200)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = AsyncMock(side_effect=[not_found, ok])

        result = await fetch_redhat_doc_http(self.AAP27_URL, http_client=mock_http)

        assert result["title"] == "Install containerized AAP"
        assert "Prepared host steps" in result["content"]
        assert mock_http.get.await_count == 2
        second_url = mock_http.get.await_args_list[1].args[0]
        assert "/html/" not in second_url
        assert "install-proc_installing_containerized_aap" in second_url


class TestCallToolIsError:
    """MCP tool isError responses should raise AnsibleKnowError."""

    @pytest.mark.asyncio
    async def test_is_error_raises(self):
        error_result = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "result": {
                "isError": True,
                "content": [
                    {"type": "text", "text": "Not a valid Red Hat Documentation link"},
                ],
            },
        }
        sse = httpx.Response(
            200,
            text=f"data: {json.dumps(error_result)}\n\n",
            headers={"content-type": "text/event-stream", "mcp-session-id": "s"},
            request=httpx.Request("POST", "https://docs-mcp.api.redhat.com/mcp"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[
            _make_init_response(),
            _make_notification_response(),
            sse,
        ])
        mock_client.aclose = AsyncMock()

        client = RedHatDocsClient()
        client._client = mock_client

        with pytest.raises(AnsibleKnowError, match="Not a valid Red Hat Documentation link"):
            await client.fetch(
                "https://docs.redhat.com/en/documentation/"
                "red_hat_ansible_automation_platform/2.7/html/"
                "install-proc_installing_containerized_aap"
            )
        await client.close()
