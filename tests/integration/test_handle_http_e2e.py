"""Real-HTTP end-to-end test for handle-based session isolation under FastMCP 4.

Starts the server in streamable-HTTP transport mode on an ephemeral localhost
port (reusing the pattern from ``test_http_transport.py``), then drives it
with real ``fastmcp.Client`` connections to prove:

- S2: two independent HTTP clients each get their own handle (``ensure_collection``
  returns distinct, non-"default" handles) and do NOT share one workspace.
- A client reconnecting with a previously-minted handle resolves the SAME
  per-handle session state (within the TTL).

No real network or ``ansible-core`` is required: ``CollectionManager.ensure_collection``
/ ``get_collections_path`` and ``parser._run_ansible_doc`` are patched at the
class/module level before the server thread starts, so the in-process HTTP
server thread (started from the *same* process) observes the same patches.
Galaxy fallback is also short-circuited (``GalaxyClient.fetch_module_doc``
raises) so a miss resolves to a clean ``{"error": ...}`` dict instead of a
real network call.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from unittest.mock import patch

import pytest

from ansible_know.errors import CollectionNotFoundError, GalaxyError

pytestmark = pytest.mark.integration


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _extract_data(result):
    """Extract deserialized dict data from a FastMCP ``CallToolResult``.

    ``.data`` can be a non-subscriptable ``Root`` object for TypedDict tool
    returns, so JSON-parse ``.content`` instead (mirrors
    ``tests/integration/test_mcp_e2e.py::_extract_data``).
    """
    if hasattr(result, "content"):
        for item in result.content:
            if hasattr(item, "text"):
                try:
                    return json.loads(item.text)
                except (json.JSONDecodeError, TypeError):
                    continue
    if hasattr(result, "data") and result.data is not None:
        return result.data
    return result


def _fake_ensure_collection(self, collection_fqcn, version=None):
    """Stand-in for ``CollectionManager.ensure_collection`` — no subprocess.

    Tracks the "installed" namespace on the *instance* (one per handle, since
    ``SessionManager`` creates one ``CollectionManager`` per ``ServerState``),
    which is what makes the isolation assertion below faithful: a handle that
    never ensured a namespace has no record of it.
    """
    installed_version = version or "1.0.0"
    self._installed[collection_fqcn] = installed_version
    return {
        "namespace": collection_fqcn,
        "version": installed_version,
        "status": "installed",
        "message": f"Installed {collection_fqcn} v{installed_version} (latest).",
    }


def _fake_get_collections_path(self):
    """Encode the instance's installed namespaces as a fake 'path' for inspection."""
    return "|".join(sorted(self._installed)) if self._installed else None


def _fake_run_ansible_doc(*args, collections_path=None, timeout=60):
    """Stand-in for ``parser._run_ansible_doc`` — succeeds only if the
    module's two-part collection namespace is present in ``collections_path``
    (as encoded by ``_fake_get_collections_path`` above), mirroring real
    local-doc resolution semantics without a real ``ansible-doc`` binary.
    """
    module_name = args[0]
    parts = module_name.split(".")
    collection_fqcn = ".".join(parts[:2]) if len(parts) >= 3 else None
    installed = (collections_path or "").split("|") if collections_path else []
    if not collection_fqcn or collection_fqcn not in installed:
        raise CollectionNotFoundError(f"ansible-doc failed: collection {collection_fqcn!r} not installed")
    return json.dumps(
        {
            module_name: {
                "doc": {
                    "module": module_name,
                    "short_description": "Fake module for e2e isolation test",
                    "description": ["Fake module for e2e isolation test."],
                    "options": {},
                },
                "examples": "",
            }
        }
    )


@pytest.fixture
def _patched_server_state():
    """Process-global patches so the threaded HTTP server honors them too."""
    with (
        patch(
            "ansible_know.collections.CollectionManager.ensure_collection",
            _fake_ensure_collection,
        ),
        patch(
            "ansible_know.collections.CollectionManager.get_collections_path",
            _fake_get_collections_path,
        ),
        patch(
            "ansible_know.parser._run_ansible_doc",
            side_effect=_fake_run_ansible_doc,
        ),
        patch(
            "ansible_know.galaxy.GalaxyClient.fetch_module_doc",
            side_effect=GalaxyError("galaxy unavailable in test"),
        ),
    ):
        yield


async def test_real_http_two_clients_isolated_and_reconnect(_patched_server_state):
    """S2: real in-process streamable-HTTP server; two clients must NOT share
    state, and a reconnected client must resolve the SAME handle."""
    from fastmcp import Client

    from ansible_know.server import mcp

    port = _find_free_port()
    server_thread = threading.Thread(
        target=mcp.run,
        kwargs={"transport": "http", "host": "127.0.0.1", "port": port},
        daemon=True,
    )
    server_thread.start()
    await asyncio.sleep(2)

    http_url = f"http://127.0.0.1:{port}/mcp/"

    async with Client(http_url) as c1, Client(http_url) as c2:
        h1_res = await c1.call_tool("ensure_collection", {"collection_namespace": "community.general"})
        h1 = _extract_data(h1_res)["handle"]
        h2_res = await c2.call_tool("ensure_collection", {"collection_namespace": "ansible.posix"})
        h2 = _extract_data(h2_res)["handle"]

        assert h1 != h2
        assert h1 != "default"
        assert h2 != "default"

        # c2's workspace never ensured community.general — it must not see it.
        miss_res = await c2.call_tool(
            "get_module_doc",
            {"module_name": "community.general.ufw", "collection_handle": h2},
        )
        miss_data = _extract_data(miss_res)
        assert "error" in miss_data

        # c1's own workspace resolves fine.
        hit_res = await c1.call_tool(
            "get_module_doc",
            {"module_name": "community.general.ufw", "collection_handle": h1},
        )
        hit_data = _extract_data(hit_res)
        assert "error" not in hit_data

    # Reconnect with the same handle resolves the SAME state (within TTL).
    async with Client(http_url) as c3:
        out_res = await c3.call_tool(
            "get_module_doc",
            {"module_name": "community.general.ufw", "collection_handle": h1},
        )
        out_data = _extract_data(out_res)
        assert "error" not in out_data
