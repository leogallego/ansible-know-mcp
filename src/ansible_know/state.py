"""Server state and lifespan context types.

Foundation-layer module: no runtime imports from Domain, External Access,
or Orchestration. CollectionManager is imported under TYPE_CHECKING only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import httpx

    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import GalaxyServerConfig
    from ansible_know.types import VersionInfo

__all__ = [
    "LifespanContext",
    "ServerState",
    "SessionManager",
    "SharedState",
]

logger = logging.getLogger("ansible_know")

DEFAULT_SESSION_TTL = 4 * 3600  # 4 hours
DEFAULT_MAX_SESSIONS = 100
SESSION_CLEANUP_INTERVAL = 300  # 5 minutes


def _get_session_ttl() -> int:
    raw = os.environ.get("ANSIBLE_KNOW_SESSION_TTL", "")
    if raw.strip():
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return DEFAULT_SESSION_TTL


def _get_max_sessions() -> int:
    raw = os.environ.get("ANSIBLE_KNOW_MAX_SESSIONS", "")
    if raw.strip():
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_MAX_SESSIONS


@dataclass
class ServerState:
    """Per-session mutable state.

    Each MCP session gets its own instance via SessionManager.
    Tool handlers access it via ``await _get_state(ctx)``.
    """

    collection_manager: CollectionManager
    missing_collections: set[str] = field(default_factory=set)
    galaxy_servers: list[GalaxyServerConfig] = field(default_factory=list)
    upgrade_warned: bool = False

    def clear_missing_namespace(self, namespace: str) -> None:
        """Remove a namespace from the negative cache."""
        self.missing_collections.discard(namespace)


@dataclass
class SharedState:
    """Process-wide state shared across all sessions.

    Created once at lifespan startup. ``galaxy_servers`` is write-once:
    set at lifespan startup, never mutated thereafter. Per-session
    ``ServerState`` references the same list object for zero-copy reads.
    ``version_info`` is updated by the periodic check via
    ``SessionManager.on_version_update()``.
    """

    galaxy_servers: list[GalaxyServerConfig] = field(default_factory=list)
    version_info: VersionInfo | None = None
    enrichment_semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(5))


class SessionManager:
    """Creates and tracks per-session ServerState instances.

    Uses ``asyncio.Lock`` for coroutine-safe access.  Each session
    gets its own ``CollectionManager``, ``missing_collections`` set,
    and ``upgrade_warned`` flag.

    Lifecycle management:
    - **TTL eviction**: sessions inactive longer than ``session_ttl``
      seconds are cleaned up (env: ``ANSIBLE_KNOW_SESSION_TTL``).
    - **Count limit**: at most ``max_sessions`` concurrent sessions;
      LRU eviction removes the least-recently-accessed session when
      the limit is reached (env: ``ANSIBLE_KNOW_MAX_SESSIONS``).
    - **Periodic cleanup**: call ``cleanup_stale_sessions()`` from a
      background task to evict expired sessions.

    The ``collection_factory`` callable is injected by the caller
    (Orchestration layer) so this Foundation-layer module never
    imports External Access modules at runtime.
    """

    def __init__(
        self,
        shared: SharedState,
        collection_factory: Callable[[], CollectionManager],
        session_ttl: int | None = None,
        max_sessions: int | None = None,
    ) -> None:
        self._shared = shared
        self._collection_factory = collection_factory
        self._sessions: dict[str, ServerState] = {}
        self._last_accessed: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._session_ttl = session_ttl if session_ttl is not None else _get_session_ttl()
        self._max_sessions = max_sessions if max_sessions is not None else _get_max_sessions()

    @property
    def session_ttl(self) -> int:
        return self._session_ttl

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    async def get_or_create(self, session_id: str) -> ServerState:
        """Return existing session state or create a new one.

        Updates last-accessed time. Evicts LRU session if count limit
        is reached when creating a new session.
        """
        now = time.monotonic()
        existing = self._sessions.get(session_id)
        if existing is not None:
            self._last_accessed[session_id] = now
            return existing
        async with self._lock:
            if session_id in self._sessions:
                self._last_accessed[session_id] = now
                return self._sessions[session_id]
            if len(self._sessions) >= self._max_sessions:
                await self._evict_lru_locked()
            self._sessions[session_id] = ServerState(
                collection_manager=self._collection_factory(),
                galaxy_servers=self._shared.galaxy_servers,
            )
            self._last_accessed[session_id] = now
            logger.debug("Created session state for %s", session_id)
            return self._sessions[session_id]

    async def _evict_lru_locked(self) -> None:
        """Evict the least-recently-accessed session. Must hold self._lock."""
        if not self._last_accessed:
            return
        lru_id = min(self._last_accessed, key=self._last_accessed.__getitem__)
        state = self._sessions.pop(lru_id, None)
        self._last_accessed.pop(lru_id, None)
        if state is not None:
            state.collection_manager.cleanup()
            logger.info("Evicted LRU session %s (max_sessions=%d)", lru_id, self._max_sessions)

    async def remove_session(self, session_id: str) -> None:
        """Remove a session and clean up its resources."""
        async with self._lock:
            state = self._sessions.pop(session_id, None)
            self._last_accessed.pop(session_id, None)
        if state is not None:
            state.collection_manager.cleanup()
            logger.debug("Removed session state for %s", session_id)

    async def cleanup_stale_sessions(self) -> int:
        """Evict sessions that have exceeded the TTL.

        Returns the number of sessions evicted.
        """
        now = time.monotonic()
        cutoff = now - self._session_ttl
        to_evict: list[str] = []

        async with self._lock:
            for sid, last in list(self._last_accessed.items()):
                if last < cutoff:
                    to_evict.append(sid)
            evicted_states: list[ServerState] = []
            for sid in to_evict:
                state = self._sessions.pop(sid, None)
                self._last_accessed.pop(sid, None)
                if state is not None:
                    evicted_states.append(state)

        for state in evicted_states:
            state.collection_manager.cleanup()

        if to_evict:
            logger.info("Evicted %d stale sessions (ttl=%ds)", len(to_evict), self._session_ttl)
        return len(to_evict)

    async def on_version_update(self, new_info: VersionInfo | None) -> None:
        """Update version info and reset upgrade_warned for all sessions."""
        async with self._lock:
            self._shared.version_info = new_info
            if new_info and new_info.get("outdated"):
                for session in self._sessions.values():
                    session.upgrade_warned = False

    @property
    def session_count(self) -> int:
        return len(self._sessions)

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
