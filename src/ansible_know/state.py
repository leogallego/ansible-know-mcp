"""Server state and lifespan context types.

Foundation-layer module: no runtime imports from Domain, External Access,
or Orchestration. CollectionManager is imported under TYPE_CHECKING only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    import httpx

    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import GalaxyServerConfig

__all__ = ["LifespanContext", "ServerState"]


@dataclass
class ServerState:
    """All mutable runtime state for one server process.

    Created once in lifespan, stored in LifespanContext,
    accessed by tool handlers via _get_state(ctx).
    """

    collection_manager: CollectionManager
    missing_collections: set[str] = field(default_factory=set)
    version_info: dict[str, Any] | None = None
    galaxy_servers: list[GalaxyServerConfig] = field(default_factory=list)
    upgrade_warned: bool = False

    def clear_missing_namespace(self, namespace: str) -> None:
        """Remove a namespace from the negative cache."""
        self.missing_collections.discard(namespace)


class LifespanContext(TypedDict):
    """Typed lifespan context replacing the untyped dict."""

    http_client: httpx.AsyncClient
    state: ServerState
