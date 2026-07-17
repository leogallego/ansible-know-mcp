"""Unit tests for RedHatDocsClient (mocked httpx, no network)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from ansible_know.errors import AnsibleKnowError
from ansible_know.redhat_docs import RedHatDocsClient, parse_mcp_sse
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
