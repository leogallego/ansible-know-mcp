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
    from ansible_know.types import (
        CollectionDocsResult,
        ErrorResponse,
        GalaxyClientFactory,
        GalaxyV1ClientFactory,
        GetModuleDocResult,
        GetPluginDocResult,
        GetRoleDocResult,
        GetStandaloneRoleDocResult,
        StandaloneRoleSearchResult,
    )

from ansible_know.async_utils import run_in_executor
from ansible_know.errors import AnsibleDocError
from ansible_know.validation import extract_collection_fqcn, sanitize_error, validate_plugin_type

logger = logging.getLogger("ansible_know")

__all__ = [
    "discover_collection_plugins",
    "resolve_collection_module_docs",
    "resolve_module_doc",
    "resolve_plugin_doc",
    "resolve_role_doc",
    "resolve_standalone_role_doc",
    "search_standalone_roles",
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


async def _try_v1_servers(
    servers: list[GalaxyServerConfig],
    operation: Callable[..., Awaitable[Any]],
    client_factory: GalaxyV1ClientFactory,
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
    if galaxy_servers:
        return galaxy_servers
    from ansible_know.galaxy_config import load_galaxy_servers
    return load_galaxy_servers()


async def discover_collection_plugins(
    collection_namespace: str,
    collections_path: str | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Discover all plugins in a collection across all 14 plugin types.

    Runs ``parser.list_plugins`` for each type in parallel via
    ``asyncio.gather``. Individual type failures are logged and
    silently return empty results.

    Returns a list of ``(plugin_type, {fqcn: description})`` tuples.
    """
    from ansible_know import parser
    from ansible_know.config import PLUGIN_TYPES
    from ansible_know.errors import ValidationError

    async def _list_one_type(ptype: str) -> tuple[str, dict[str, str]]:
        try:
            return ptype, await run_in_executor(
                parser.list_plugins, ptype,
                collection_filter=collection_namespace,
                collections_path=collections_path,
            )
        except (AnsibleDocError, OSError, ValidationError):
            return ptype, {}

    return list(await asyncio.gather(
        *[_list_one_type(pt) for pt in PLUGIN_TYPES]
    ))


async def resolve_module_doc(
    module_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
    collections_path: str | None = None,
) -> GetModuleDocResult | ErrorResponse:
    """Try local ansible-doc, fall back to Galaxy if the collection is missing.

    Returns the complete tool response dict including doc_source and content_type.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    collection_fqcn = extract_collection_fqcn(module_name)
    cpath = collections_path

    local_doc: dict[str, Any] = {}
    local_error: str | None = None

    if not (collection_fqcn and missing_collections is not None and collection_fqcn in missing_collections):
        try:
            local_doc = await run_in_executor(
                parser.get_module_doc, module_name, collections_path=cpath,
            )
        except CollectionNotFoundError as exc:
            if collection_fqcn and missing_collections is not None:
                missing_collections.add(collection_fqcn)
            local_error = str(exc)
            local_doc = {}
        except AnsibleDocError as exc:
            logger.warning("Local module doc failed for %s: %s", module_name, exc)
            local_error = str(exc)
            local_doc = {}

    if local_doc:
        result = dict(parser.extract_module_metadata(local_doc))
        result["content_type"] = "module"
        result["doc_source"] = "local"
        return result

    if client_factory is None:
        return {
            "module_name": module_name,
            "content_type": "module",
            "doc_source": "unavailable",
            "error": sanitize_error(
                local_error or f"Collection '{collection_fqcn}' not installed locally"
                if collection_fqcn else local_error or "Module not found"
            ),
            "params": [],
        }

    async def _fetch_from_galaxy(client):
        return await client.fetch_module_doc(module_name)

    try:
        galaxy_doc, galaxy_meta = await _try_galaxy_servers(
            servers, _fetch_from_galaxy, client_factory, http_client,
        )
        metadata = parser.extract_module_metadata(galaxy_doc)
        result = dict(metadata)
        result["content_type"] = "module"
        result["doc_source"] = "galaxy"
        result["doc_version"] = galaxy_meta.get("doc_version", "")
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as galaxy_exc:
        logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
        return {
            "module_name": module_name,
            "content_type": "module",
            "doc_source": "unavailable",
            "error": sanitize_error(str(galaxy_exc)),
            "params": [],
        }


async def resolve_plugin_doc(
    plugin_name: str,
    plugin_type: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
    collections_path: str | None = None,
) -> GetPluginDocResult | ErrorResponse:
    """Try local ansible-doc -t <type>, fall back to Galaxy.

    Returns the complete tool response dict including doc_source and plugin_type.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    validate_plugin_type(plugin_type)

    servers = _get_servers(galaxy_servers)
    collection_fqcn = extract_collection_fqcn(plugin_name)
    cpath = collections_path

    local_doc: dict[str, Any] = {}

    if not (collection_fqcn and missing_collections is not None and collection_fqcn in missing_collections):
        try:
            local_doc = await run_in_executor(
                parser.get_plugin_doc, plugin_name, plugin_type,
                collections_path=cpath,
            )
        except CollectionNotFoundError:
            if collection_fqcn and missing_collections is not None:
                missing_collections.add(collection_fqcn)
            local_doc = {}
        except AnsibleDocError as exc:
            logger.warning("Local plugin doc failed for %s: %s", plugin_name, exc)
            local_doc = {}

    if local_doc:
        result = dict(parser.extract_plugin_metadata(local_doc, plugin_type))
        result["content_type"] = "plugin"
        result["doc_source"] = "local"
        return result

    if client_factory is None:
        return {
            "plugin_name": plugin_name,
            "plugin_type": plugin_type,
            "content_type": "plugin",
            "doc_source": "unavailable",
            "error": "No Galaxy client configured for fallback",
            "params": [],
        }

    try:
        async def _fetch(client):
            return await client.fetch_plugin_doc(plugin_name, plugin_type)
        galaxy_doc, galaxy_meta = await _try_galaxy_servers(
            servers, _fetch, client_factory, http_client,
        )
        metadata = parser.extract_plugin_metadata(galaxy_doc, plugin_type)
        result = dict(metadata)
        result["content_type"] = "plugin"
        result["doc_source"] = "galaxy"
        result["doc_version"] = galaxy_meta.get("doc_version", "")
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as galaxy_exc:
        return {
            "plugin_name": plugin_name,
            "plugin_type": plugin_type,
            "content_type": "plugin",
            "doc_source": "unavailable",
            "error": sanitize_error(str(galaxy_exc)),
            "params": [],
        }


async def resolve_role_doc(
    role_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
    collections_path: str | None = None,
) -> GetRoleDocResult | ErrorResponse:
    """Try local ansible-doc -t role, fall back to Galaxy readme_html.

    Returns the complete tool response dict including doc_source and content_type.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    collection_fqcn = extract_collection_fqcn(role_name)
    cpath = collections_path

    local_doc: dict[str, Any] = {}

    if not (collection_fqcn and missing_collections is not None and collection_fqcn in missing_collections):
        try:
            local_doc = await run_in_executor(
                parser.get_role_doc, role_name, collections_path=cpath,
            )
        except CollectionNotFoundError:
            if collection_fqcn and missing_collections is not None:
                missing_collections.add(collection_fqcn)
            local_doc = {}
        except AnsibleDocError as exc:
            logger.warning("Local role doc failed for %s: %s", role_name, exc)
            local_doc = {}

    if local_doc:
        result = dict(parser.extract_role_metadata(local_doc))
        result["content_type"] = "role"
        result["doc_source"] = "local"
        return result

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


async def resolve_collection_module_docs(
    collection_namespace: str,
    version: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
) -> CollectionDocsResult | ErrorResponse:
    """Batch-fetch all module docs for a collection from Galaxy.

    Delegates to ``GalaxyClient.fetch_collection_docs`` via the
    multi-server fallback chain. Does NOT try local ansible-doc —
    the caller decides whether to use this (Galaxy-only) path or
    the existing per-module local path.

    Contract:
        Preconditions:
            - ``client_factory`` must be provided. If ``None``, returns
              an ``ErrorResponse`` immediately (no exception raised).

        Raises:
            Nothing — errors are returned as ``ErrorResponse`` dicts.

        Silences:
            - ``GalaxyError`` from all servers: caught and returned as
              ``{"error": str}`` after sanitization. Individual server
              failures are logged at INFO level by ``_try_galaxy_servers``.
    """
    from ansible_know.errors import GalaxyError

    if client_factory is None:
        return {"error": "No Galaxy client configured for collection docs"}

    servers = _get_servers(galaxy_servers)

    async def _fetch(client):
        return await client.fetch_collection_docs(
            collection_namespace, version=version,
        )

    try:
        modules, galaxy_meta = await _try_galaxy_servers(
            servers, _fetch, client_factory, http_client,
        )
        result: CollectionDocsResult = {
            "modules": modules,
            "doc_source": "galaxy",
        }
        if "doc_version" in galaxy_meta:
            result["doc_version"] = galaxy_meta["doc_version"]
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as exc:
        return {"error": sanitize_error(str(exc))}


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


async def search_standalone_roles(
    query: str,
    tags: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    v1_client_factory: GalaxyV1ClientFactory | None = None,
) -> StandaloneRoleSearchResult:
    """Search configured Galaxy servers concurrently for standalone roles.

    A successful v1 response with zero roles is success, even if other
    servers lack v1 (typical Automation Hub) or time out. Raises
    GalaxyError only when no server returns a v1 search response.
    """
    from ansible_know.errors import GalaxyError

    if v1_client_factory is None:
        raise GalaxyError("No client factory configured for Galaxy search")

    servers = _get_servers(galaxy_servers)

    async def _query_server(server):
        async with v1_client_factory(
            server, http_client=_select_http_client(http_client, server),
        ) as client:
            result = await client.search_roles(query, tags=tags)
        return server.name, result

    tasks = [_query_server(s) for s in servers]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    all_roles: list[dict[str, Any]] = []
    seen_role_names: set[str] = set()
    errors: list[str] = []
    v1_ok = 0

    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, Exception):
            logger.info(
                "search_roles on '%s' failed: %s",
                servers[i].name, outcome,
            )
            errors.append(f"{servers[i].name}: {outcome}")
            continue
        v1_ok += 1
        server_name, result = outcome
        for role in result.get("roles", []):
            role_name = role.get("role_name", "")
            if role_name not in seen_role_names:
                role["source"] = server_name
                all_roles.append(role)
                seen_role_names.add(role_name)

    if v1_ok == 0:
        raise GalaxyError(
            f"All Galaxy servers failed: {'; '.join(errors)}"
            if errors else "No Galaxy servers configured"
        )

    all_roles.sort(key=lambda r: r.get("download_count", 0), reverse=True)

    return {
        "query": query,
        "count": len(all_roles),
        "roles": all_roles,
    }


async def resolve_standalone_role_doc(
    role_name: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    v1_client_factory: GalaxyV1ClientFactory | None = None,
) -> GetStandaloneRoleDocResult | ErrorResponse:
    """Resolve standalone role docs from configured Galaxy v1 servers."""
    from ansible_know.errors import GalaxyError

    if v1_client_factory is None:
        return {"error": "No Galaxy client configured for standalone roles"}

    servers = _get_servers(galaxy_servers)

    async def _fetch(client):
        return await client.fetch_standalone_role_doc(role_name)

    try:
        role_doc, galaxy_meta = await _try_v1_servers(
            servers, _fetch, v1_client_factory, http_client,
        )
        result = dict(role_doc)
        result["doc_source"] = galaxy_meta.get("doc_source", "")
        result["doc_version"] = galaxy_meta.get("doc_version", "")
        if "doc_warning" in galaxy_meta:
            result["doc_warning"] = galaxy_meta["doc_warning"]
        if "doc_source_server" in galaxy_meta:
            result["doc_source_server"] = galaxy_meta["doc_source_server"]
        return result
    except GalaxyError as exc:
        return {"error": sanitize_error(str(exc))}
