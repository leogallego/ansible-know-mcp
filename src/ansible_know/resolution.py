"""Document resolution with local-then-Galaxy fallback.

Owns the resolution strategy for module and role documentation:
local ansible-doc first, then Galaxy docs-blob API, then graceful
degradation. Also provides multi-server Galaxy collection search.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from ansible_know.galaxy_config import GalaxyServerConfig
    from ansible_know.types import DocProvenance, GalaxyClientFactory

from ansible_know.async_utils import run_in_executor
from ansible_know.errors import AnsibleDocError
from ansible_know.validation import sanitize_error

logger = logging.getLogger("ansible_know")

__all__ = [
    "resolve_module_doc",
    "resolve_role_doc",
    "search_galaxy_collections",
]



def _select_http_client(
    http_client: httpx.AsyncClient | None,
    server: GalaxyServerConfig,
) -> httpx.AsyncClient | None:
    """Use shared client only when server validates certs."""
    return http_client if server.validate_certs else None


async def _try_galaxy_servers(
    servers: list[GalaxyServerConfig],
    operation: Callable[..., Awaitable[Any]],
    client_factory: GalaxyClientFactory,
    http_client: httpx.AsyncClient | None = None,
) -> Any:
    """Try an operation across multiple Galaxy servers in priority order.

    Returns the first successful result. Raises the last GalaxyError if all fail.
    """
    from ansible_know.errors import GalaxyError

    last_exc: GalaxyError | None = None
    for server in servers:
        try:
            async with client_factory(
                server, http_client=_select_http_client(http_client, server),
            ) as client:
                return await operation(client)
        except GalaxyError as exc:
            logger.info("Galaxy server '%s' failed: %s", server.name, exc)
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise GalaxyError("No Galaxy servers configured")


def _get_servers(
    galaxy_servers: list[GalaxyServerConfig] | None,
) -> list[GalaxyServerConfig]:
    """Return explicit servers or fall back to ansible.cfg discovery."""
    if galaxy_servers is not None:
        return galaxy_servers
    from ansible_know.galaxy_config import load_galaxy_servers
    return load_galaxy_servers()


async def resolve_module_doc(
    module_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
    collections_path: str | None = None,
) -> tuple[dict[str, Any], DocProvenance | None]:
    """Try local ansible-doc, fall back to Galaxy if the collection is missing.

    Returns (raw_doc, galaxy_meta_or_none). Raises on non-missing-collection
    errors and when both local and Galaxy lookups fail.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    namespace = ".".join(module_name.split(".")[:2]) if "." in module_name else None
    cpath = collections_path

    async def _fetch_from_galaxy(client):
        return await client.fetch_module_doc(module_name)

    if namespace and missing_collections is not None and namespace in missing_collections:
        if client_factory is None:
            raise CollectionNotFoundError(
                f"Collection '{namespace}' not installed locally"
            )
        try:
            galaxy_doc, galaxy_meta = await _try_galaxy_servers(
                servers, _fetch_from_galaxy, client_factory, http_client,
            )
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            raise CollectionNotFoundError(
                f"Collection '{namespace}' not installed locally"
            ) from galaxy_exc

    try:
        raw_doc = await run_in_executor(
            parser.get_module_doc, module_name, collections_path=cpath,
        )
        return raw_doc, None
    except CollectionNotFoundError as local_exc:
        if namespace and missing_collections is not None:
            missing_collections.add(namespace)
        if client_factory is None:
            raise
        logger.info("Collection not installed, trying Galaxy: %s", local_exc)
        try:
            galaxy_doc, galaxy_meta = await _try_galaxy_servers(
                servers, _fetch_from_galaxy, client_factory, http_client,
            )
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
            raise local_exc from galaxy_exc


async def resolve_role_doc(
    role_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
    collections_path: str | None = None,
) -> dict[str, Any]:
    """Try local ansible-doc -t role, fall back to Galaxy readme_html.

    Returns the complete tool response dict including doc_source and content_type.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    namespace = ".".join(role_name.split(".")[:2]) if "." in role_name else None
    cpath = collections_path

    local_doc: dict[str, Any] = {}

    if not (namespace and missing_collections is not None and namespace in missing_collections):
        try:
            local_doc = await run_in_executor(
                parser.get_role_doc, role_name, collections_path=cpath,
            )
        except CollectionNotFoundError:
            if namespace and missing_collections is not None:
                missing_collections.add(namespace)
            local_doc = {}
        except AnsibleDocError as exc:
            logger.warning("Local role doc failed for %s: %s", role_name, exc)
            local_doc = {}

    if local_doc:
        metadata = parser.extract_role_metadata(local_doc)
        metadata["content_type"] = "role"
        metadata["doc_source"] = "local"
        return metadata

    if client_factory is None:
        return {
            "role_name": role_name,
            "content_type": "role",
            "doc_source": "unavailable",
            "error": "No Galaxy client configured for fallback",
            "entry_points": {},
        }

    try:
        async def _fetch(client):
            return await client.fetch_role_doc(role_name)
        galaxy_role_meta, galaxy_meta = await _try_galaxy_servers(
            servers, _fetch, client_factory, http_client,
        )

        result = dict(galaxy_role_meta)
        result["content_type"] = "role"
        result["doc_source"] = "galaxy_readme"
        result["doc_version"] = galaxy_meta.get("doc_version", "")
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as galaxy_exc:
        return {
            "role_name": role_name,
            "content_type": "role",
            "doc_source": "unavailable",
            "error": sanitize_error(str(galaxy_exc)),
            "entry_points": {},
        }


async def search_galaxy_collections(
    query: str,
    tags: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
) -> dict[str, Any]:
    """Search all configured Galaxy servers concurrently, merge and dedupe results."""
    from ansible_know.errors import GalaxyError

    if client_factory is None:
        raise GalaxyError("No client factory configured for Galaxy search")

    servers = _get_servers(galaxy_servers)

    async def _query_server(server):
        async with client_factory(
            server, http_client=_select_http_client(http_client, server),
        ) as client:
            result = await client.search_collections(query, tags=tags)
        return server.name, result

    tasks = [_query_server(s) for s in servers]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    all_collections: list[dict[str, Any]] = []
    seen_namespaces: set[str] = set()
    errors: list[str] = []

    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, Exception):
            logger.info(
                "search_collections on '%s' failed: %s",
                servers[i].name, outcome,
            )
            errors.append(f"{servers[i].name}: {outcome}")
            continue
        server_name, result = outcome
        for coll in result.get("collections", []):
            ns = coll.get("namespace", "")
            if ns not in seen_namespaces:
                coll["source"] = server_name
                all_collections.append(coll)
                seen_namespaces.add(ns)

    if not all_collections and errors:
        raise GalaxyError(f"All Galaxy servers failed: {'; '.join(errors)}")

    all_collections.sort(key=lambda c: c.get("download_count", 0), reverse=True)

    return {
        "query": query,
        "count": len(all_collections),
        "collections": all_collections,
    }
