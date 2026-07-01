"""Ansible Know MCP Server.

Provides 17 tools, 6 resources, and 5 prompts for module, role, and plugin discovery,
documentation search, Galaxy collection discovery, and skill generation
via the Model Context Protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from importlib.metadata import version as pkg_version
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations

from ansible_know.async_utils import run_in_executor
from ansible_know.errors import AnsibleDocError, AnsibleKnowError, ValidationError, collection_hint, maybe_add_hint
from ansible_know.state import LifespanContext, ServerState, SessionManager, SharedState
from ansible_know.types import (
    ClearCacheResult,
    CollectionDocsResult,
    CollectionSearchResult,
    EnsureCollectionResult,
    ErrorResponse,
    FetchDocResult,
    GenerateCollectionSkillsResult,
    GetModuleDocResult,
    GetPluginDocResult,
    GetRoleDocResult,
    ManifestResult,
    SearchDocsEntry,
    SkillEntry,
    VersionInfo,
)
from ansible_know.validation import (
    extract_collection_fqcn,
    sanitize_error,
    truncate_response,
    validate_doc_url,
    validate_fqcn,
    validate_install_path,
    validate_keyword,
    validate_namespace,
    validate_plugin_type,
    validate_query,
    validate_skill_name,
    validate_tags,
    validate_version,
)

logger = logging.getLogger("ansible_know")

_VERSION = pkg_version("ansible-know-mcp")

_VERSION_CHECK_INTERVAL = 6 * 3600  # 6 hours

# Module-level references for resource functions (no FastMCP Context).
_shared_state: SharedState | None = None
_session_manager: SessionManager | None = None


def _is_stable(v: str) -> bool:
    import re
    return bool(re.fullmatch(r"\d+(\.\d+)*", v.strip()))


def _parse_version(v: str) -> tuple[int, ...]:
    import re
    match = re.match(r"^(\d+(?:\.\d+)*)", v.strip())
    if not match:
        return (0,)
    return tuple(int(x) for x in match.group(1).split("."))


async def _check_pypi_version(client: httpx.AsyncClient) -> VersionInfo | None:
    if os.environ.get("ANSIBLE_KNOW_SKIP_UPDATE_CHECK", "").strip() in ("1", "true", "yes"):
        return None
    try:
        resp = await client.get(
            "https://pypi.org/pypi/ansible-know-mcp/json",
            timeout=httpx.Timeout(3.0),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.debug("PyPI version check failed (non-blocking)")
        return None
    latest = data.get("info", {}).get("version", "")
    if not latest or not _is_stable(latest):
        return None
    outdated = _parse_version(latest) > _parse_version(_VERSION)
    return {
        "installed": _VERSION,
        "latest": latest,
        "outdated": outdated,
        "upgrade_command": "uvx --upgrade ansible-know-mcp",
    }


@lifespan
async def app_lifespan(server):
    global _shared_state, _session_manager
    from ansible_know.collections import CollectionManager
    from ansible_know.galaxy_config import load_galaxy_servers

    galaxy_servers = await run_in_executor(load_galaxy_servers)
    shared = SharedState(galaxy_servers=galaxy_servers)
    sessions = SessionManager(shared, collection_factory=CollectionManager)
    _shared_state = shared
    _session_manager = sessions
    for gs in galaxy_servers:
        auth_type = "token" if gs.token else ("basic" if gs.username else "none")
        logger.info("Galaxy server: %s (%s, auth=%s)", gs.name, gs.url, auth_type)

    from ansible_know.config import USER_AGENT

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=120.0),
        verify=True,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        shared.version_info = await _check_pypi_version(client)
        check_task = asyncio.create_task(
            _periodic_version_check(client, shared, sessions)
        )
        cleanup_task = asyncio.create_task(
            _periodic_session_cleanup(sessions)
        )
        try:
            yield LifespanContext(
                http_client=client, shared=shared, sessions=sessions,
            )
        finally:
            check_task.cancel()
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await check_task
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

mcp = FastMCP(
    name="Ansible Know",
    version=_VERSION,
    instructions=(
        "Ansible module, role, and plugin discovery, documentation, and skill generation. "
        "Workflow: (1) search_collections to discover collections on Galaxy, "
        "(2) ensure_collection to install one for this session, "
        "(3) search_modules/search_plugins/get_collection_manifest to find content, "
        "(4) get_module_doc, get_role_doc, or get_plugin_doc for structured docs, "
        "(5) search_docs for conceptual guides, then fetch_doc to retrieve full content, "
        "(6) generate_skill, generate_role_skill, or generate_plugin_skill for skill packages. "
        "Resources: server://version for version and upgrade status, "
        "galaxy://installed for session collections, "
        "docs://sources for configured doc sources, "
        "skills://list for generated skills."
    ),
    lifespan=app_lifespan,
)


def _galaxy_factory(ctx: Context | None = None):
    """Return a GalaxyClient factory that injects the shared semaphore."""
    from ansible_know.galaxy import GalaxyClient

    semaphore = None
    if ctx is not None:
        shared: SharedState = ctx.lifespan_context["shared"]
        semaphore = shared.enrichment_semaphore

    def _factory(config, http_client=None):
        return GalaxyClient.from_config(
            config, http_client=http_client,
            enrichment_semaphore=semaphore,
        )

    return _factory


async def _get_state(ctx: Context | None) -> ServerState:
    """Return per-session ServerState from ctx, or create ephemeral."""
    if ctx is not None:
        sessions: SessionManager = ctx.lifespan_context["sessions"]
        state = await sessions.get_or_create(ctx.session_id)
        if (
            hasattr(ctx.session, "_exit_stack")
            and ctx.session._exit_stack is not None
            and not getattr(ctx.session, "_ansible_know_cleanup_registered", False)
        ):
            session_id = ctx.session_id

            async def _cleanup_session() -> None:
                await sessions.remove_session(session_id)

            ctx.session._exit_stack.push_async_callback(_cleanup_session)
            ctx.session._ansible_know_cleanup_registered = True
        return state
    from ansible_know.collections import CollectionManager
    return ServerState(collection_manager=CollectionManager())


def _get_shared(ctx: Context | None) -> SharedState:
    """Return process-wide SharedState from ctx or module-level."""
    if ctx is not None:
        return ctx.lifespan_context["shared"]
    if _shared_state is not None:
        return _shared_state
    return SharedState()


def _get_http_client(ctx: Context | None) -> httpx.AsyncClient | None:
    """Extract http_client from the lifespan context."""
    if ctx is None:
        return None
    return ctx.lifespan_context["http_client"]


async def _maybe_warn_upgrade(ctx: Context | None) -> None:
    if ctx is None:
        return
    state = await _get_state(ctx)
    shared = _get_shared(ctx)
    version_info = shared.version_info
    if state.upgrade_warned or not version_info or not version_info.get("outdated"):
        return
    await ctx.warning(
        f"ansible-know-mcp {version_info['installed']} is outdated; "
        f"latest is {version_info['latest']}. "
        f"Upgrade: {version_info['upgrade_command']}"
    )
    state.upgrade_warned = True


async def _periodic_session_cleanup(sessions: SessionManager) -> None:
    """Evict stale sessions periodically."""
    from ansible_know.state import SESSION_CLEANUP_INTERVAL

    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL)
        try:
            await sessions.cleanup_stale_sessions()
        except Exception:
            logger.debug("Session cleanup raised unexpectedly", exc_info=True)


async def _periodic_version_check(
    client: httpx.AsyncClient,
    shared: SharedState,
    sessions: SessionManager,
) -> None:
    """Re-check PyPI for updates periodically (non-blocking)."""
    while True:
        await asyncio.sleep(_VERSION_CHECK_INTERVAL)
        if client.is_closed:
            return
        try:
            new_info = await _check_pypi_version(client)
        except Exception:
            logger.debug("Periodic version check raised unexpectedly", exc_info=True)
            continue
        if new_info is None:
            continue
        old_latest = (
            shared.version_info.get("latest")
            if shared.version_info
            else None
        )
        await sessions.on_version_update(new_info)
        if new_info.get("latest") != old_latest:
            logger.info(
                "Periodic version check: new version %s available",
                new_info.get("latest"),
            )


# --- Discovery tools (read-only) ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_modules(
    keyword: Annotated[str, "Search term to match against module names and descriptions"],
    namespace: Annotated[str | None, "Optional collection namespace filter (e.g. 'community.docker')"] = None,
    ctx: Context | None = None,
) -> dict[str, str] | ErrorResponse:
    """Find Ansible modules by keyword in name or description. Returns up to 50 matches as {fqcn: short_description}.

    Returns: {"module.fqcn": "short description", ...} or {"error": str} on failure.
    """
    logger.info("search_modules keyword=%r namespace=%r", keyword, namespace)
    try:
        validate_keyword(keyword)
        if namespace:
            validate_namespace(namespace)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser
        from ansible_know.config import SEARCH_MODULES_LIMIT

        state = await _get_state(ctx)
        results = await run_in_executor(
            parser.search_modules, keyword, collection_filter=namespace,
            collections_path=state.collection_manager.get_collections_path(),
        )
        if len(results) > SEARCH_MODULES_LIMIT:
            results = dict(list(results.items())[:SEARCH_MODULES_LIMIT])
        return results
    except Exception as exc:
        logger.warning("search_modules failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), namespace)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_plugins(
    keyword: Annotated[str, "Search term to match against plugin names and descriptions"],
    plugin_type: Annotated[
        str | None,
        "Plugin type filter (e.g. 'lookup', 'filter'). If omitted, searches all types.",
    ] = None,
    namespace: Annotated[str | None, "Optional collection namespace filter (e.g. 'netbox.netbox')"] = None,
    ctx: Context | None = None,
) -> dict[str, str] | ErrorResponse:
    """Find Ansible plugins by keyword. Returns up to 50 matches as {fqcn: short_description}.

    Plugin types: lookup, filter, test, connection, become, strategy,
    callback, inventory, cache, cliconf, httpapi, netconf, shell, vars.
    On failure returns {"error": str}.
    """
    logger.info("search_plugins keyword=%r type=%r namespace=%r", keyword, plugin_type, namespace)
    try:
        validate_keyword(keyword)
        if namespace:
            validate_namespace(namespace)
        if plugin_type:
            validate_plugin_type(plugin_type)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser
        from ansible_know.config import PLUGIN_TYPES, SEARCH_MODULES_LIMIT

        state = await _get_state(ctx)
        cpath = state.collection_manager.get_collections_path()

        if plugin_type is not None:
            results = await run_in_executor(
                parser.search_plugins, keyword, plugin_type=plugin_type,
                collection_filter=namespace, collections_path=cpath,
            )
        else:
            # Parallelize across all 14 types
            async def _search_one_type(pt):
                try:
                    return await run_in_executor(
                        parser.search_plugins, keyword, plugin_type=pt,
                        collection_filter=namespace, collections_path=cpath,
                    )
                except (AnsibleDocError, OSError, ValidationError):
                    return None

            type_results = await asyncio.gather(
                *[_search_one_type(pt) for pt in PLUGIN_TYPES]
            )
            results = {}
            error_count = 0
            for r in type_results:
                if r is None:
                    error_count += 1
                else:
                    results.update(r)

            if not results and error_count == len(PLUGIN_TYPES):
                return {"error": "Plugin discovery failed for all plugin types. Check ansible-core installation."}

        if len(results) > SEARCH_MODULES_LIMIT:
            results = dict(list(results.items())[:SEARCH_MODULES_LIMIT])
        return results
    except Exception as exc:
        logger.warning("search_plugins failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), namespace)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_module_doc(
    module_name: Annotated[str, "Fully-qualified collection name (e.g. 'ansible.builtin.copy')"],
    ctx: Context | None = None,
) -> GetModuleDocResult | ErrorResponse:
    """Get full structured documentation for one module.

    Returns: module_name, short_description, params (list with name/type/required/default/choices/description/aliases),
    examples (raw YAML), is_api_module, doc_source ('local' or 'galaxy').
    When doc_source is 'galaxy', also includes doc_version and optionally doc_warning.
    Falls back to Galaxy if collection is not installed locally.
    On failure returns {"error": str}.
    """
    logger.info("get_module_doc module=%r", module_name)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_fqcn(module_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        result = await resolution.resolve_module_doc(
            module_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )
        if "error" in result:
            ns = extract_collection_fqcn(module_name)
            result["error"] = maybe_add_hint(result["error"], ns)
        return result
    except Exception as exc:
        logger.warning("get_module_doc failed: %s", exc)
        ns = extract_collection_fqcn(module_name)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_role_doc(
    role_name: Annotated[str, "Fully-qualified role name (e.g. 'fedora.linux_system_roles.timesync')"],
    ctx: Context | None = None,
) -> GetRoleDocResult | ErrorResponse:
    """Get full structured documentation for one role.

    Returns: role_name, content_type ('role'), short_description,
    doc_source ('local', 'galaxy_readme', or 'unavailable'),
    entry_points (dict of entry point names to {description, options}),
    dependencies (list), examples (str).
    When doc_source is 'galaxy_readme', also includes doc_version and doc_warning.
    Falls back to Galaxy README parsing if local ansible-doc returns empty.
    On validation failure returns {"error": str}.
    """
    logger.info("get_role_doc role=%r", role_name)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_fqcn(role_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        return await resolution.resolve_role_doc(
            role_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )
    except Exception as exc:
        logger.warning("get_role_doc failed: %s", exc)
        ns = extract_collection_fqcn(role_name)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_plugin_doc(
    plugin_name: Annotated[str, "Fully-qualified plugin name (e.g. 'netbox.netbox.nb_lookup')"],
    plugin_type: Annotated[
        str,
        "Plugin type (lookup, filter, test, connection, become, "
        "strategy, callback, inventory, cache, cliconf, httpapi, "
        "netconf, shell, or vars)",
    ],
    ctx: Context | None = None,
) -> GetPluginDocResult | ErrorResponse:
    """Get full structured documentation for one plugin.

    Returns: plugin_name, plugin_type, short_description, params, examples,
    doc_source ('local' or 'galaxy').
    Falls back to Galaxy if collection is not installed locally.
    On failure returns {"error": str}.
    """
    logger.info("get_plugin_doc plugin=%r type=%r", plugin_name, plugin_type)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_fqcn(plugin_name)
        validate_plugin_type(plugin_type)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        return await resolution.resolve_plugin_doc(
            plugin_name, plugin_type,
            http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )
    except Exception as exc:
        logger.warning("get_plugin_doc failed: %s", exc)
        ns = extract_collection_fqcn(plugin_name)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_docs(
    query: Annotated[str, "Search term to match against documentation titles, summaries, and topics"],
    source: Annotated[str | None, "Filter to a single source (e.g. 'ansible-core')"] = None,
    topic: Annotated[str | None, "Filter by topic tag"] = None,
    audience: Annotated[str | None, "Filter by audience tag"] = None,
    core_only: Annotated[bool, "If true, only return entries marked as core"] = False,
    ctx: Context | None = None,
) -> list[SearchDocsEntry] | ErrorResponse:
    """Search documentation manifests for conceptual guides.

    Returns up to 20 matching entries with title, summary, topic, audience, lines, source, and raw URL.
    On failure returns {"error": str}.
    """
    logger.info("search_docs query=%r", query)
    try:
        validate_query(query)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import docs

        return await docs.search_docs(
            query=query, source=source, topic=topic, audience=audience,
            core_only=core_only, http_client=_get_http_client(ctx),
        )
    except Exception as exc:
        logger.warning("search_docs failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def fetch_doc(
    url: Annotated[str, "A docs.ansible.com URL to fetch as markdown"],
    max_tokens: Annotated[
        int | None,
        "If set, return error instead of content when the page exceeds this token count. "
        "Checked after fetching via the x-markdown-tokens response header.",
    ] = None,
    ctx: Context | None = None,
) -> FetchDocResult | ErrorResponse:
    """Fetch a page from docs.ansible.com as clean Markdown.

    Returns documentation content ready for LLM consumption.
    Use search_docs to discover relevant page URLs, or pass a known
    docs.ansible.com URL directly. The url parameter must start with
    https://docs.ansible.com/.
    """
    logger.info("fetch_doc url=%r max_tokens=%r", url, max_tokens)
    try:
        validate_doc_url(url)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import docs

        return await docs.fetch_doc_content(
            url=url, max_tokens=max_tokens, http_client=_get_http_client(ctx),
        )
    except (AnsibleKnowError, ValidationError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("fetch_doc failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_collections(
    query: Annotated[str, "Search keyword (e.g., 'netbox', 'cisco ios', 'vmware')"],
    tags: Annotated[str | None, "Optional comma-separated Galaxy tags to filter (e.g., 'networking,cloud')"] = None,
    ctx: Context | None = None,
) -> CollectionSearchResult | ErrorResponse:
    """Search Ansible Galaxy for collections by keyword.

    Returns non-deprecated collections ranked by download count.
    Use this to discover which collection provides modules for a
    specific platform or use case.

    After finding a collection, use ensure_collection() to install it,
    then get_module_doc() or get_collection_manifest() to explore its modules.

    Returns: {"query": str, "count": int, "collections": [{"namespace": str,
    "description": str, "tags": [str], "latest_version": str, "module_count": int,
    "download_count": int, "deprecated": bool, "signed": bool}, ...]}
    On failure returns {"error": str}.
    """
    logger.info("search_collections query=%r tags=%r", query, tags)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_query(query)
        if tags:
            validate_tags(tags)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        return await resolution.search_galaxy_collections(
            query, tags=tags, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
        )
    except Exception as exc:
        logger.warning("search_collections failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_collection_manifest(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    ctx: Context | None = None,
) -> ManifestResult | ErrorResponse:
    """Get collection-level manifest with per-module summaries.

    Returns cached MANIFEST.json if available, otherwise generates on-demand
    (metadata extraction only, no skill generation).
    On failure returns {"error": str}.
    """
    logger.info("get_collection_manifest namespace=%r", collection_namespace)
    try:
        validate_namespace(collection_namespace)
    except ValidationError as exc:
        return {"error": str(exc)}

    await _maybe_warn_upgrade(ctx)
    try:
        from ansible_know import collection_manifest, parser, resolution

        state = await _get_state(ctx)
        installed_version = state.collection_manager.list_installed().get(collection_namespace)
        cached = await run_in_executor(
            collection_manifest.load_cached_manifest,
            collection_namespace, installed_version=installed_version,
        )
        if cached:
            return cached

        cpath = state.collection_manager.get_collections_path()

        modules = await run_in_executor(
            parser.search_modules, "", collection_filter=collection_namespace,
            collections_path=cpath,
        )

        roles_raw = {}
        try:
            roles_raw = await run_in_executor(
                parser.list_roles, collection_filter=collection_namespace,
                collections_path=cpath,
            )
        except (AnsibleDocError, OSError) as exc:
            logger.warning("list_roles failed for %s: %s", collection_namespace, exc)

        plugin_results = await resolution.discover_collection_plugins(
            collection_namespace, collections_path=cpath,
        )
        plugins_raw: dict[str, dict[str, str]] = {}
        for ptype, type_plugins in plugin_results:
            for pfqcn, pdesc in type_plugins.items():
                plugins_raw[pfqcn] = {"description": pdesc, "plugin_type": ptype}

        if not modules and not roles_raw and not plugins_raw:
            return {"error": (
                f"No modules, roles, or plugins found in collection '{collection_namespace}'."
                + collection_hint(collection_namespace)
            )}

        metadata_list = []
        for module_name in sorted(modules):
            try:
                raw_doc = await run_in_executor(
                    parser.get_module_doc, module_name, collections_path=cpath,
                )
                metadata_list.append(parser.extract_module_metadata(raw_doc))
            except AnsibleDocError:
                continue

        roles_metadata = []
        for role_fqcn, role_data in sorted(roles_raw.items()):
            entry_points = list(role_data.get("entry_points", {}).keys()) or ["main"]
            has_specs = bool(role_data.get("entry_points", {}))
            roles_metadata.append({
                "fqcn": role_fqcn,
                "description": role_data.get("description", ""),
                "has_argument_specs": has_specs,
                "entry_points": entry_points,
            })

        plugins_metadata = []
        for pfqcn, pinfo in sorted(plugins_raw.items()):
            plugins_metadata.append({
                "fqcn": pfqcn,
                "plugin_type": pinfo["plugin_type"],
                "description": pinfo["description"],
                "param_count": 0,
            })

        manifest = await run_in_executor(
            collection_manifest.generate_manifest,
            collection_namespace, metadata_list,
            roles_metadata=roles_metadata,
            plugins_metadata=plugins_metadata,
            collection_version=installed_version,
        )
        await run_in_executor(
            collection_manifest.write_manifest,
            manifest, collection_namespace,
        )
        return manifest
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("get_collection_manifest failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_collection_docs(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[str | None, "Optional version (e.g. '3.23.0'). If omitted, uses latest."] = None,
    ctx: Context | None = None,
) -> CollectionDocsResult | ErrorResponse:
    """Get full module documentation for all modules in a collection from Galaxy.

    Returns all module docs in a single API call without installing the collection.
    Result shape: {"modules": {fqcn: {module_name, short_description, params, examples, is_api_module}, ...},
    "doc_source": "galaxy", "doc_version": str}.
    On failure returns {"error": str}.
    """
    logger.info("get_collection_docs namespace=%r version=%r", collection_namespace, version)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_namespace(collection_namespace)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        return await resolution.resolve_collection_module_docs(
            collection_namespace,
            version=version,
            http_client=http_client,
            galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
        )
    except Exception as exc:
        logger.warning("get_collection_docs failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, readOnlyHint=False, destructiveHint=False))
async def ensure_collection(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[
        str | None,
        "Optional version (e.g. '4.1.0'). If omitted, installs latest and pins the resolved version.",
    ] = None,
    ctx: Context | None = None,
) -> EnsureCollectionResult | ErrorResponse:
    """Install a collection to a temporary directory for this session.

    Installs once and pins the resolved version. Subsequent calls with the
    same namespace skip unless a different version is explicitly requested.
    If a different version is requested than currently installed, the
    collection will be reinstalled.

    Returns dict with keys:
    - namespace: str — the collection namespace
    - version: str — the installed/active version (always set)
    - status: 'installed' (freshly installed or upgraded) or
      'already_installed' (same version already present, no action taken)
    - message: str — human-readable summary including the active version
    On failure returns {"error": str}.
    """
    logger.info("ensure_collection namespace=%r version=%r", collection_namespace, version)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_namespace(collection_namespace)
        if version:
            validate_version(version)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        state = await _get_state(ctx)
        result = await run_in_executor(
            state.collection_manager.ensure_collection, collection_fqcn=collection_namespace, version=version,
        )
        state.clear_missing_namespace(collection_namespace)
        logger.info(
            "ensure_collection result: namespace=%s version=%s status=%s",
            result["namespace"], result["version"], result["status"],
        )
        return result
    except Exception as exc:
        logger.warning("ensure_collection failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


# --- Skill management tools ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_skills(
    collection: Annotated[
        str | None,
        "Optional collection namespace to filter skills (e.g. 'netbox.netbox'). "
        "Without this, returns all skills.",
    ] = None,
) -> list[SkillEntry] | ErrorResponse:
    """List all available generated skills. Returns name, description, path for each.

    Returns: [{"name": str, "description": str, "path": str}, ...] or {"error": str} on failure.
    """
    logger.info("list_skills collection=%r", collection)
    if collection:
        try:
            validate_namespace(collection)
        except ValidationError as exc:
            return {"error": str(exc)}

    try:
        from ansible_know.config import SKILLS_DIR
        from ansible_know.skills import list_skills_sync

        return await run_in_executor(list_skills_sync, SKILLS_DIR, collection)
    except Exception as exc:
        logger.warning("list_skills failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_skill(
    skill_name: Annotated[
        str,
        "Skill name: a module FQCN (e.g. 'netbox.netbox.netbox_device') or "
        "a collection namespace (e.g. 'netbox.netbox') for the collection-level skill.",
    ],
) -> str | ErrorResponse:
    """Read a specific skill's SKILL.md content by name.

    Returns: SKILL.md content as str, or {"error": str} on failure/not found.
    """
    logger.info("get_skill name=%r", skill_name)
    try:
        validate_skill_name(skill_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know.config import SKILLS_DIR
        from ansible_know.skills import get_skill_sync

        return await run_in_executor(get_skill_sync, SKILLS_DIR, skill_name)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("get_skill failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_skill(
    module_name: Annotated[str, "Fully-qualified module name (e.g. 'ansible.builtin.copy')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context | None = None,
) -> str | ErrorResponse:
    """Generate a skill package for one module.

    Writes SKILL.md + scripts + playbook to disk.
    Returns the SKILL.md content as str, or {"error": str} on failure.
    """
    logger.info("generate_skill module=%r install_to=%r", module_name, install_to)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_fqcn(module_name)
        if install_to:
            validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        metadata = await resolution.resolve_module_doc(
            module_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )

        if metadata.get("doc_source") == "unavailable":
            return {"error": metadata.get("error", f"No documentation found for module '{module_name}'.")}

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        fqcn = metadata["module_name"]
        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        collection_dir = skills.collection_skill_name(extract_collection_fqcn(fqcn) or fqcn)
        output_dir = base_dir / collection_dir / skills.fqcn_to_skill_name(fqcn)

        await run_in_executor(skills.write_module_skill_package, output_dir, metadata)
        logger.info("generate_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_module_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_skill failed: %s", exc)
        ns = extract_collection_fqcn(module_name)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_role_skill(
    role_name: Annotated[str, "Fully-qualified role name (e.g. 'fedora.linux_system_roles.timesync')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context | None = None,
) -> str | ErrorResponse:
    """Generate a skill package for one role.

    Writes SKILL.md + assets/playbook.yml to disk (no scripts/).
    Returns the SKILL.md content as str, or {"error": str} on failure.
    """
    logger.info("generate_role_skill role=%r install_to=%r", role_name, install_to)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_fqcn(role_name)
        if install_to:
            validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        metadata = await resolution.resolve_role_doc(
            role_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )

        if metadata.get("doc_source") == "unavailable":
            return {"error": metadata.get("error", f"No documentation found for role '{role_name}'.")}

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        collection_dir = skills.collection_skill_name(extract_collection_fqcn(role_name) or role_name)
        output_dir = base_dir / collection_dir / skills.role_skill_name(role_name)

        await run_in_executor(skills.write_role_skill_package, output_dir, metadata)
        logger.info("generate_role_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_role_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_role_skill failed: %s", exc)
        ns = extract_collection_fqcn(role_name)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_plugin_skill(
    plugin_name: Annotated[str, "Fully-qualified plugin name (e.g. 'netbox.netbox.nb_lookup')"],
    plugin_type: Annotated[
        str,
        "Plugin type (lookup, filter, test, connection, become, "
        "strategy, callback, inventory, cache, cliconf, httpapi, "
        "netconf, shell, or vars)",
    ],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context | None = None,
) -> str | ErrorResponse:
    """Generate a skill package for one plugin.

    Writes SKILL.md to disk (no scripts/ or assets/).
    Returns the SKILL.md content as str, or {"error": str} on failure.
    """
    logger.info("generate_plugin_skill plugin=%r type=%r install_to=%r", plugin_name, plugin_type, install_to)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_fqcn(plugin_name)
        validate_plugin_type(plugin_type)
        if install_to:
            validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import resolution, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        metadata = await resolution.resolve_plugin_doc(
            plugin_name, plugin_type,
            http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(ctx),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )

        if metadata.get("doc_source") == "unavailable":
            return {"error": metadata.get("error", f"No documentation found for plugin '{plugin_name}'.")}

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        collection_dir = skills.collection_skill_name(extract_collection_fqcn(plugin_name) or plugin_name)
        output_dir = base_dir / collection_dir / skills.plugin_skill_name(plugin_name, plugin_type)

        await run_in_executor(skills.write_plugin_skill_package, output_dir, metadata)
        logger.info("generate_plugin_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_plugin_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_plugin_skill failed: %s", exc)
        ns = extract_collection_fqcn(plugin_name)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_collection_skills(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    install_to: Annotated[str | None, "Optional absolute path to install skills to"] = None,
    ctx: Context | None = None,
) -> GenerateCollectionSkillsResult | ErrorResponse:
    """Batch generate skills for an entire collection.

    Generates/updates the collection MANIFEST.json as a byproduct.
    Returns {"succeeded": int, "failed": int, "total": int, "manifest": dict, "collection_skill": str},
    or {"error": str} on failure.
    """
    logger.info("generate_collection_skills namespace=%r install_to=%r", collection_namespace, install_to)
    await _maybe_warn_upgrade(ctx)
    try:
        validate_namespace(collection_namespace)
        if install_to:
            validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import collection_manifest, parser, resolution, skills
        from ansible_know.config import SKILLS_DIR

        state = await _get_state(ctx)
        cpath = state.collection_manager.get_collections_path()

        # Discover modules
        modules = await run_in_executor(
            parser.search_modules, "", collection_filter=collection_namespace,
            collections_path=cpath,
        )

        # Galaxy batch fallback for modules when collection not installed locally
        galaxy_batch_modules: dict[str, Any] = {}
        if not modules:
            batch_result = await resolution.resolve_collection_module_docs(
                collection_namespace,
                http_client=_get_http_client(ctx),
                galaxy_servers=state.galaxy_servers,
                client_factory=_galaxy_factory(ctx),
            )
            if "modules" in batch_result:
                galaxy_batch_modules = batch_result["modules"]

        # Discover roles
        roles_raw = {}
        try:
            roles_raw = await run_in_executor(
                parser.list_roles, collection_filter=collection_namespace,
                collections_path=cpath,
            )
        except (AnsibleDocError, OSError) as exc:
            logger.warning("list_roles failed for %s: %s", collection_namespace, exc)

        plugin_list_results = await resolution.discover_collection_plugins(
            collection_namespace, collections_path=cpath,
        )

        # Combined guard — reject only if ALL content types are empty
        has_plugins = any(plugins for _, plugins in plugin_list_results)
        if not modules and not galaxy_batch_modules and not roles_raw and not has_plugins:
            return {"error": (
                f"No modules, roles, or plugins found in collection '{collection_namespace}'."
                + collection_hint(collection_namespace)
            )}

        plugin_count = sum(len(plugins) for _, plugins in plugin_list_results)
        total = len(modules) + len(galaxy_batch_modules) + len(roles_raw) + plugin_count
        succeeded = 0
        failed = 0
        current = 0
        metadata_list = []

        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        collection_dir_name = skills.collection_skill_name(collection_namespace)

        installed_version = state.collection_manager.list_installed().get(collection_namespace)

        # Generate module skills
        for module_name in sorted(modules):
            if ctx:
                await ctx.report_progress(progress=current, total=total)
            current += 1
            try:
                raw_doc = await run_in_executor(
                    parser.get_module_doc, module_name, collections_path=cpath,
                )
                metadata = parser.extract_module_metadata(raw_doc)
                metadata_list.append(metadata)

                output_dir = base_dir / collection_dir_name / skills.fqcn_to_skill_name(module_name)
                await run_in_executor(skills.write_module_skill_package, output_dir, metadata)
                succeeded += 1
            except Exception as exc:
                logger.warning("Module skill generation failed for %s: %s", module_name, exc)
                failed += 1

        # Generate module skills from Galaxy batch (when not installed locally)
        for module_fqcn, module_meta in sorted(galaxy_batch_modules.items()):
            if ctx:
                await ctx.report_progress(progress=current, total=total)
            current += 1
            try:
                metadata_list.append(module_meta)
                output_dir = base_dir / collection_dir_name / skills.fqcn_to_skill_name(module_fqcn)
                await run_in_executor(skills.write_module_skill_package, output_dir, module_meta)
                succeeded += 1
            except Exception as exc:
                logger.warning("Module skill generation failed for %s: %s", module_fqcn, exc)
                failed += 1

        # Generate role skills
        from ansible_know import resolution

        roles_metadata = []
        for role_fqcn, role_data in sorted(roles_raw.items()):
            if ctx:
                await ctx.report_progress(progress=current, total=total)
            current += 1
            try:
                http_client = _get_http_client(ctx)
                role_meta = await resolution.resolve_role_doc(
                    role_fqcn, http_client=http_client,
                    galaxy_servers=state.galaxy_servers,
                    client_factory=_galaxy_factory(ctx),
                    missing_collections=state.missing_collections,
                    collections_path=cpath,
                )

                if role_meta.get("doc_source") == "unavailable":
                    logger.warning("Role doc unavailable for %s, skipping skill", role_fqcn)
                    failed += 1
                    continue

                entry_points = list(role_data.get("entry_points", {}).keys()) or ["main"]
                has_specs = bool(role_data.get("entry_points", {}))
                roles_metadata.append({
                    "fqcn": role_fqcn,
                    "description": role_data.get("description", ""),
                    "has_argument_specs": has_specs,
                    "entry_points": entry_points,
                })

                output_dir = base_dir / collection_dir_name / skills.role_skill_name(role_fqcn)
                await run_in_executor(
                    skills.write_role_skill_package, output_dir, role_meta,
                )
                succeeded += 1
            except Exception as exc:
                logger.warning("Role skill generation failed for %s: %s", role_fqcn, exc)
                failed += 1

        # Generate plugin skills
        plugins_metadata = []
        for ptype, type_plugins in plugin_list_results:
            for pfqcn in sorted(type_plugins):
                if ctx:
                    await ctx.report_progress(progress=current, total=total)
                current += 1
                try:
                    raw_doc = await run_in_executor(
                        parser.get_plugin_doc, pfqcn, ptype,
                        collections_path=cpath,
                    )
                    meta = parser.extract_plugin_metadata(raw_doc, ptype)
                    plugins_metadata.append({
                        "fqcn": pfqcn,
                        "plugin_type": ptype,
                        "description": meta["short_description"],
                        "param_count": len(meta["params"]),
                    })

                    output_dir = base_dir / collection_dir_name / skills.plugin_skill_name(pfqcn, ptype)
                    await run_in_executor(
                        skills.write_plugin_skill_package, output_dir, meta,
                    )
                    succeeded += 1
                except Exception as exc:
                    logger.warning("Plugin skill generation failed for %s: %s", pfqcn, exc)
                    failed += 1

        manifest = await run_in_executor(
            collection_manifest.generate_manifest,
            collection_namespace, metadata_list,
            roles_metadata=roles_metadata,
            plugins_metadata=plugins_metadata,
            skills_dir=base_dir,
            collection_version=installed_version,
        )
        await run_in_executor(
            collection_manifest.write_manifest,
            manifest, collection_namespace, skills_dir=base_dir,
        )

        await run_in_executor(
            skills.write_collection_skill_package,
            base_dir / collection_dir_name, collection_namespace,
            metadata_list, installed_version, plugins_metadata,
        )

        if ctx:
            await ctx.report_progress(progress=total, total=total)

        logger.info("generate_collection_skills completed: %d/%d succeeded", succeeded, total)
        return {
            "succeeded": succeeded,
            "failed": failed,
            "total": total,
            "manifest": manifest,
            "collection_skill": collection_namespace,
        }
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("generate_collection_skills failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}


_VALID_CACHE_SCOPES = {"galaxy", "docs"}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, readOnlyHint=False, destructiveHint=False))
async def clear_cache(
    scope: Annotated[
        str | None,
        "Cache scope to clear: 'galaxy' (version + docs-blob), 'docs' (doc manifests), "
        "or omit to clear all caches.",
    ] = None,
) -> ClearCacheResult | ErrorResponse:
    """Clear server caches.

    Clears Galaxy version/docs-blob caches, doc manifest/page caches, or both.
    Useful when cached data becomes stale during long-running sessions.
    """
    logger.info("clear_cache scope=%r", scope)
    if scope is not None and scope not in _VALID_CACHE_SCOPES:
        return {"error": f"Invalid scope '{scope}'. Must be 'galaxy', 'docs', or omitted for all."}

    cleared: list[str] = []

    if scope in (None, "galaxy"):
        from ansible_know import galaxy
        galaxy.clear_cache()
        cleared.extend(["galaxy_versions", "galaxy_blobs"])

    if scope in (None, "docs"):
        from ansible_know import docs
        docs.clear_cache()
        cleared.extend(["doc_manifests", "doc_pages"])

    return {"cleared": cleared}


# --- Resources (read-only data) ---


@mcp.resource("skills://list", name="Available Skills", description="List all generated skill packages")
def resource_skills_list() -> str:
    import json

    from ansible_know.config import SKILLS_DIR
    from ansible_know.skills import list_skills_sync

    entries = list_skills_sync(SKILLS_DIR, collection=None)
    return json.dumps([e["name"] for e in entries], indent=2)


@mcp.resource(
    "skills://{skill_name}",
    name="Skill Content",
    description="Read a generated skill's SKILL.md by FQCN or collection namespace",
)
def resource_skill_content(skill_name: str) -> str:
    from ansible_know.config import SKILLS_DIR
    from ansible_know.skills import get_skill_sync

    try:
        validate_skill_name(skill_name)
    except ValidationError as exc:
        return str(exc)

    try:
        return get_skill_sync(SKILLS_DIR, skill_name)
    except FileNotFoundError as exc:
        return str(exc)
    except ValidationError as exc:
        return str(exc)
    except OSError as exc:
        return sanitize_error(str(exc))


@mcp.resource(
    "galaxy://installed",
    name="Installed Collections",
    description="List collections installed across all active sessions",
)
def resource_installed_collections() -> str:
    installed = _session_manager.all_installed_collections if _session_manager else {}
    return json.dumps(installed, indent=2)


@mcp.resource(
    "server://version",
    name="Server Version",
    description="Installed and latest version info with upgrade status",
)
def resource_server_version() -> str:
    version_info = _shared_state.version_info if _shared_state else None
    if version_info:
        return json.dumps(version_info, indent=2)
    return json.dumps(
        {
            "installed": _VERSION,
            "latest": None,
            "outdated": None,
            "upgrade_command": "uvx --upgrade ansible-know-mcp",
        },
        indent=2,
    )


@mcp.resource(
    "galaxy://servers",
    name="Galaxy Servers",
    description=(
        "List configured Galaxy servers from ansible.cfg. "
        "Shows auth type (token/basic/none) for debugging; credentials are never exposed."
    ),
)
def resource_galaxy_servers() -> str:
    from ansible_know.galaxy_config import load_galaxy_servers

    servers = (_shared_state.galaxy_servers if _shared_state else None) or load_galaxy_servers()
    return json.dumps([
        {
            "name": s.name,
            "url": s.url,
            "auth": "token" if s.token else ("basic" if s.username else "none"),
            "validate_certs": s.validate_certs,
        }
        for s in servers
    ], indent=2)


@mcp.resource(
    "docs://sources",
    name="Documentation Sources",
    description="List configured documentation manifest sources",
)
def resource_doc_sources() -> str:
    import json

    from ansible_know.config import get_doc_sources

    sources = get_doc_sources()
    result = {}
    for name, cfg in sources.items():
        entry: dict[str, str] = {"description": cfg.get("description", "")}
        if "file" in cfg:
            entry["type"] = "file"
            entry["path"] = cfg["file"]
        elif "url" in cfg:
            entry["type"] = "url"
            entry["url"] = cfg["url"]
        result[name] = entry
    return json.dumps(result, indent=2)


# --- Prompts (reusable templates) ---


@mcp.prompt
def review_playbook(playbook_yaml: str) -> str:
    """Review an Ansible playbook against module documentation and best practices."""
    return (
        "Review the following Ansible playbook for correctness, best practices, "
        "and potential issues. Check that modules are used with correct parameters, "
        "FQCNs are used, and the playbook follows idempotency principles.\n\n"
        "Use the search_modules, search_plugins, get_module_doc, and get_plugin_doc "
        "tools to verify module and plugin usage.\n\n"
        f"```yaml\n{playbook_yaml}\n```"
    )


@mcp.prompt
def explain_module(module_name: str) -> str:
    """Get a detailed explanation of an Ansible module with usage examples."""
    return (
        f"Explain the Ansible module `{module_name}` in detail. "
        "Use the get_module_doc tool to fetch its full documentation, then provide:\n\n"
        "1. What the module does and when to use it\n"
        "2. Required vs optional parameters with descriptions\n"
        "3. A practical example playbook\n"
        "4. Common pitfalls or gotchas"
    )


@mcp.prompt
def explain_plugin(plugin_name: str, plugin_type: str) -> str:
    """Get a detailed explanation of an Ansible plugin with usage examples."""
    return (
        f"Explain the Ansible {plugin_type} plugin `{plugin_name}` in detail. "
        "Use the get_plugin_doc tool to fetch its full documentation, then provide:\n\n"
        "1. What the plugin does and when to use it instead of a module\n"
        "2. Parameters with descriptions\n"
        "3. A practical usage example (Jinja2 expression, inventory file, or ansible.cfg)\n"
        "4. Common pitfalls or gotchas"
    )


@mcp.prompt
def generate_role(role_purpose: str, modules: str) -> str:
    """Generate an Ansible role skeleton using specified modules."""
    return (
        f"Generate an Ansible role that: {role_purpose}\n\n"
        f"Use the following modules: {modules}\n\n"
        "Use get_module_doc for each module to get correct parameter names. "
        "Follow these conventions:\n"
        "- Use FQCNs for all modules\n"
        "- Prefix all variables with the role name\n"
        "- Put user-facing defaults in defaults/main.yml\n"
        "- Include meta/argument_specs.yml for validation\n"
        "- Ensure idempotency with changed_when on command/shell tasks\n"
        "- Add a README.md with example playbooks"
    )


@mcp.prompt
def find_collection(platform_or_use_case: str) -> str:
    """Guide the agent through discovering, installing, and exploring a collection."""
    return (
        f"Find an Ansible collection for: {platform_or_use_case}\n\n"
        "Follow these steps:\n"
        "1. Use search_collections to find relevant collections on Galaxy\n"
        "2. Pick the best match (prefer high download count, non-deprecated)\n"
        "3. Use ensure_collection to install it for this session\n"
        "4. Use get_collection_manifest to see all available modules and roles\n"
        "5. Use get_module_doc, get_role_doc, or get_plugin_doc on relevant content to understand usage"
    )


def main():
    """Entry point for the MCP server."""
    from ansible_know.cli import parse_args

    config = parse_args()
    kwargs: dict[str, Any] = {}
    if config.transport == "http":
        kwargs.update(host=config.host, port=config.port)
    mcp.run(transport=config.transport, **kwargs)


if __name__ == "__main__":
    main()
