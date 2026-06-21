"""Server state and lifespan context types.

Foundation-layer module: no runtime imports from Domain, External Access,
or Orchestration. CollectionManager is imported under TYPE_CHECKING only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    import httpx

    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import GalaxyServerConfig

__all__ = [
    "LifespanContext",
    "ServerState",
    "SessionManager",
    "SharedState",
]

logger = logging.getLogger("ansible_know")


@dataclass
class ServerState:
    """Per-session mutable state.

    Each MCP session gets its own instance via SessionManager.
    Tool handlers access it via ``await _get_state(ctx)``.
    """

    collection_manager: CollectionManager
    missing_collections: set[str] = field(default_factory=set)
    version_info: dict[str, Any] | None = None
    galaxy_servers: list[GalaxyServerConfig] = field(default_factory=list)
    upgrade_warned: bool = False

    def clear_missing_namespace(self, namespace: str) -> None:
        """Remove a namespace from the negative cache."""
        self.missing_collections.discard(namespace)


@dataclass
class SharedState:
    """Process-wide state shared across all sessions.

    Created once at lifespan startup. Read-only except for
    ``version_info`` which is updated by the periodic check.
    """

    galaxy_servers: list[GalaxyServerConfig] = field(default_factory=list)
    version_info: dict[str, Any] | None = None


class SessionManager:
    """Creates and tracks per-session ServerState instances.

    Uses ``asyncio.Lock`` for coroutine-safe access.  Each session
    gets its own ``CollectionManager``, ``missing_collections`` set,
    and ``upgrade_warned`` flag.

    The ``collection_factory`` callable is injected by the caller
    (Orchestration layer) so this Foundation-layer module never
    imports External Access modules at runtime.
    """

    def __init__(
        self,
        shared: SharedState,
        collection_factory: Callable[[], CollectionManager],
    ) -> None:
        self._shared = shared
        self._collection_factory = collection_factory
        self._sessions: dict[str, ServerState] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> ServerState:
        """Return existing session state or create a new one."""
        # Fast path: dict.get() is atomic in single-threaded asyncio
        # (no await between read and lock acquisition).
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing
        async with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ServerState(
                    collection_manager=self._collection_factory(),
                    galaxy_servers=self._shared.galaxy_servers,
                    version_info=self._shared.version_info,
                )
                logger.debug("Created session state for %s", session_id)
            return self._sessions[session_id]

    async def remove_session(self, session_id: str) -> None:
        """Remove a session and clean up its resources."""
        async with self._lock:
            state = self._sessions.pop(session_id, None)
        if state is not None:
            state.collection_manager.cleanup()
            logger.debug("Removed session state for %s", session_id)

    async def on_version_update(self, new_info: dict[str, Any] | None) -> None:
        """Update version info and reset upgrade_warned for all sessions."""
        async with self._lock:
            self._shared.version_info = new_info
            for session in self._sessions.values():
                session.version_info = new_info
                if new_info and new_info.get("outdated"):
                    session.upgrade_warned = False

    @property
    def shared(self) -> SharedState:
        """Read-only access to the process-wide shared state."""
        return self._shared

    @property
    def all_installed_collections(self) -> dict[str, str]:
        """Union of installed collections across all sessions.

        Lockless read: safe in single-threaded asyncio (no await
        between ``list()`` copy and iteration).
        """
        result: dict[str, str] = {}
        for session in list(self._sessions.values()):
            result.update(session.collection_manager.list_installed())
        return result


class LifespanContext(TypedDict):
    """Typed lifespan context for tool and resource access."""

    http_client: httpx.AsyncClient
    shared: SharedState
    sessions: SessionManager
