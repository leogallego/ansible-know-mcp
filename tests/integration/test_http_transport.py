"""Smoke test for HTTP streamable transport.

Verifies the server starts in HTTP mode and responds to an MCP
initialize request. Requires ansible-core installed.

Run with: pytest --run-integration tests/integration/test_http_transport.py -v
"""

from __future__ import annotations

import asyncio
import socket
import threading

import httpx
import pytest

pytestmark = pytest.mark.integration


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_http_transport_starts_and_responds():
    """Start the server in HTTP mode and send an MCP initialize request."""
    from ansible_know.server import mcp

    port = _find_free_port()

    server_thread = threading.Thread(
        target=mcp.run,
        kwargs={"transport": "http", "host": "127.0.0.1", "port": port},
        daemon=True,
    )
    server_thread.start()
    await asyncio.sleep(2)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"http://127.0.0.1:{port}/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            },
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=10.0,
        )
        assert resp.status_code == 200
