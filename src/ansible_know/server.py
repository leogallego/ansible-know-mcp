"""Ansible Know MCP Server.

Provides 12 tools, 5 resources, and 4 prompts for module and role discovery,
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
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations

from ansible_know.async_utils import run_in_executor
from ansible_know.errors import AnsibleDocError, ValidationError, collection_hint, maybe_add_hint
from ansible_know.state import LifespanContext, ServerState, SessionManager, SharedState
from ansible_know.types import VersionInfo
from ansible_know.validation import (
    sanitize_error,
    truncate_response,
    validate_fqcn,
    validate_install_path,
    validate_keyword,
    validate_namespace,
    validate_path_containment,
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

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=120.0),
        verify=True,
    ) as client:
        shared.version_info = await _check_pypi_version(client)
        check_task = asyncio.create_task(
            _periodic_version_check(client, shared, sessions)
        )
        try:
            yield LifespanContext(
                http_client=client, shared=shared, sessions=sessions,
            )
        finally:
            check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await check_task

mcp = FastMCP(
    name="Ansible Know",
    version=_VERSION,
    instructions=(
        "Ansible module and role discovery, documentation, and skill generation. "
        "Workflow: (1) search_collections to discover collections on Galaxy, "
        "(2) ensure_collection to install one for this session, "
        "(3) search_modules/get_collection_manifest to find modules and roles, "
        "(4) get_module_doc or get_role_doc for structured docs, "
        "(5) search_docs for conceptual guides, "
        "(6) generate_skill or generate_role_skill to create skill packages."
    ),
    lifespan=app_lifespan,
)


def _galaxy_factory():
    """Lazy import of GalaxyClient.from_config for dependency injection."""
    from ansible_know.galaxy import GalaxyClient
    return GalaxyClient.from_config


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
) -> dict[str, str]:
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
async def get_module_doc(
    module_name: Annotated[str, "Fully-qualified collection name (e.g. 'ansible.builtin.copy')"],
    ctx: Context | None = None,
) -> dict[str, Any]:
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
        from ansible_know import parser, resolution

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        raw_doc, galaxy_meta = await resolution.resolve_module_doc(
            module_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )
        metadata = parser.extract_module_metadata(raw_doc)
        if galaxy_meta:
            metadata.update(galaxy_meta)
        else:
            metadata["doc_source"] = "local"
        return metadata
    except Exception as exc:
        logger.warning("get_module_doc failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        from ansible_know.errors import GalaxyError
        if isinstance(exc.__cause__, GalaxyError):
            return {"error": sanitize_error(str(exc))}
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_role_doc(
    role_name: Annotated[str, "Fully-qualified role name (e.g. 'fedora.linux_system_roles.timesync')"],
    ctx: Context | None = None,
) -> dict[str, Any]:
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
            client_factory=_galaxy_factory(),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )
    except Exception as exc:
        logger.warning("get_role_doc failed: %s", exc)
        ns = ".".join(role_name.split(".")[:2]) if "." in role_name else None
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_docs(
    query: Annotated[str, "Search term to match against documentation titles, summaries, and topics"],
    source: Annotated[str | None, "Filter to a single source (e.g. 'ansible-core')"] = None,
    topic: Annotated[str | None, "Filter by topic tag"] = None,
    audience: Annotated[str | None, "Filter by audience tag"] = None,
    core_only: Annotated[bool, "If true, only return entries marked as core"] = False,
) -> list[dict[str, Any]] | dict[str, str]:
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
            query=query, source=source, topic=topic, audience=audience, core_only=core_only,
        )
    except Exception as exc:
        logger.warning("search_docs failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_collections(
    query: Annotated[str, "Search keyword (e.g., 'netbox', 'cisco ios', 'vmware')"],
    tags: Annotated[str | None, "Optional comma-separated Galaxy tags to filter (e.g., 'networking,cloud')"] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
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
            client_factory=_galaxy_factory(),
        )
    except Exception as exc:
        logger.warning("search_collections failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_collection_manifest(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    ctx: Context | None = None,
) -> dict[str, Any]:
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
        from ansible_know import collection_manifest, parser

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

        if not modules and not roles_raw:
            return {"error": (
                f"No modules or roles found in collection '{collection_namespace}'."
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

        return await run_in_executor(
            collection_manifest.generate_manifest,
            collection_namespace, metadata_list,
            roles_metadata=roles_metadata,
            collection_version=installed_version,
        )
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("get_collection_manifest failed: %s", exc)
        return {"error": maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, readOnlyHint=False, destructiveHint=False))
async def ensure_collection(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[
        str | None,
        "Optional version (e.g. '4.1.0'). If omitted, installs latest and pins the resolved version.",
    ] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
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


def _extract_skill_description(skill_md: Path) -> str:
    """Extract description from a SKILL.md frontmatter."""
    content = skill_md.read_text()
    for line in content.splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip().strip(">-").strip()
    return ""


def _list_skills_sync(
    skills_dir: Path, collection: str | None,
) -> list[dict[str, str]]:
    """Synchronous helper for list_skills — all file I/O happens here."""
    results: list[dict[str, str]] = []
    if not skills_dir.exists():
        return results

    if collection:
        collection_dir = (skills_dir / collection).resolve()
        validate_path_containment(collection_dir, skills_dir)
        if not collection_dir.is_dir():
            return results
        for sub_dir in sorted(collection_dir.iterdir()):
            try:
                skill_md = sub_dir / "SKILL.md"
                if sub_dir.is_dir() and not sub_dir.is_symlink() and skill_md.exists():
                    results.append({
                        "name": f"{collection}.{sub_dir.name}",
                        "description": _extract_skill_description(skill_md),
                        "path": str(sub_dir),
                    })
            except OSError:
                logger.warning("Skipping unreadable skill: %s", sub_dir.name)
                continue
    else:
        for skill_dir in sorted(skills_dir.iterdir()):
            try:
                if not skill_dir.is_dir() or skill_dir.is_symlink():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    results.append({
                        "name": skill_dir.name,
                        "description": _extract_skill_description(skill_md),
                        "path": str(skill_dir),
                    })
            except OSError:
                logger.warning("Skipping unreadable skill: %s", skill_dir.name)
                continue
    return results


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_skills(
    collection: Annotated[
        str | None,
        "Optional collection namespace to list skills within (e.g. 'netbox.netbox'). "
        "Without this, returns collection-level skill entries and standalone skills only.",
    ] = None,
) -> list[dict[str, str]] | dict[str, str]:
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

        return await run_in_executor(_list_skills_sync, SKILLS_DIR, collection)
    except Exception as exc:
        logger.warning("list_skills failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


def _get_skill_sync(skills_dir: Path, skill_name: str) -> str:
    """Read a skill's SKILL.md content from disk.

    Callers MUST validate ``skill_name`` with ``validate_skill_name()`` first.

    Raises:
        FileNotFoundError: If no matching SKILL.md exists.
        ValidationError: If a resolved path escapes ``skills_dir``.
        OSError: On permission or I/O errors reading the file.
    """
    parts = skill_name.split(".")
    if len(parts) >= 3:
        namespace = ".".join(parts[:2])
        short_name = ".".join(parts[2:])
        nested_path = (skills_dir / namespace / short_name / "SKILL.md").resolve()
        validate_path_containment(nested_path, skills_dir)
        if nested_path.exists():
            return truncate_response(nested_path.read_text())

        flat_path = (skills_dir / skill_name / "SKILL.md").resolve()
        validate_path_containment(flat_path, skills_dir)
        if flat_path.exists():
            return truncate_response(flat_path.read_text())
    else:
        skill_path = (skills_dir / skill_name / "SKILL.md").resolve()
        validate_path_containment(skill_path, skills_dir)
        if skill_path.exists():
            return truncate_response(skill_path.read_text())

    raise FileNotFoundError(f"Skill '{skill_name}' not found.")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_skill(
    skill_name: Annotated[
        str,
        "Skill name: a module FQCN (e.g. 'netbox.netbox.netbox_device') or "
        "a collection namespace (e.g. 'netbox.netbox') for the collection-level skill.",
    ],
) -> str | dict[str, str]:
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

        return await run_in_executor(_get_skill_sync, SKILLS_DIR, skill_name)
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
) -> str | dict[str, str]:
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
        from ansible_know import parser, resolution, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        state = await _get_state(ctx)
        http_client = _get_http_client(ctx)
        raw_doc, galaxy_meta = await resolution.resolve_module_doc(
            module_name, http_client=http_client, galaxy_servers=state.galaxy_servers,
            client_factory=_galaxy_factory(),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )
        metadata = parser.extract_module_metadata(raw_doc)
        if galaxy_meta:
            metadata.update(galaxy_meta)

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        fqcn = metadata["module_name"]
        namespace = ".".join(fqcn.split(".")[:2])
        short_name = fqcn.rsplit(".", 1)[-1]
        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        output_dir = base_dir / namespace / short_name

        await run_in_executor(skills.write_module_skill_package, output_dir, metadata)
        logger.info("generate_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_module_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_skill failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        from ansible_know.errors import GalaxyError
        if isinstance(exc.__cause__, GalaxyError):
            return {"error": sanitize_error(str(exc))}
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_role_skill(
    role_name: Annotated[str, "Fully-qualified role name (e.g. 'fedora.linux_system_roles.timesync')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context | None = None,
) -> str | dict[str, str]:
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
            client_factory=_galaxy_factory(),
            missing_collections=state.missing_collections,
            collections_path=state.collection_manager.get_collections_path(),
        )

        if metadata.get("doc_source") == "unavailable":
            return {"error": metadata.get("error", f"No documentation found for role '{role_name}'.")}

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        namespace = ".".join(role_name.split(".")[:2])
        short_name = role_name.rsplit(".", 1)[-1]
        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        output_dir = base_dir / namespace / short_name

        await run_in_executor(skills.write_role_skill_package, output_dir, metadata)
        logger.info("generate_role_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_role_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_role_skill failed: %s", exc)
        ns = ".".join(role_name.split(".")[:2]) if "." in role_name else None
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_collection_skills(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    install_to: Annotated[str | None, "Optional absolute path to install skills to"] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
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
        from ansible_know import collection_manifest, parser, skills
        from ansible_know.config import SKILLS_DIR

        state = await _get_state(ctx)
        cpath = state.collection_manager.get_collections_path()
        modules = await run_in_executor(
            parser.search_modules, "", collection_filter=collection_namespace,
            collections_path=cpath,
        )
        if not modules:
            return {"error": (
                f"No modules found in collection '{collection_namespace}'."
                + collection_hint(collection_namespace)
            )}

        total = len(modules)
        succeeded = 0
        failed = 0
        metadata_list = []

        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR

        installed_version = state.collection_manager.list_installed().get(collection_namespace)

        for i, module_name in enumerate(sorted(modules)):
            if ctx:
                await ctx.report_progress(progress=i, total=total)
            try:
                raw_doc = await run_in_executor(
                    parser.get_module_doc, module_name, collections_path=cpath,
                )
                metadata = parser.extract_module_metadata(raw_doc)
                metadata_list.append(metadata)

                short_name = metadata["module_name"].rsplit(".", 1)[-1]
                output_dir = base_dir / collection_namespace / short_name
                await run_in_executor(skills.write_module_skill_package, output_dir, metadata)
                succeeded += 1
            except Exception as exc:
                logger.warning("Skill generation failed for %s: %s", module_name, exc)
                failed += 1

        manifest = await run_in_executor(
            collection_manifest.generate_manifest,
            collection_namespace, metadata_list, skills_dir=base_dir,
            collection_version=installed_version,
        )

        await run_in_executor(
            skills.write_collection_skill_package,
            base_dir / collection_namespace, collection_namespace,
            metadata_list, installed_version,
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


# --- Resources (read-only data) ---


@mcp.resource("skills://list", name="Available Skills", description="List all generated skill packages")
def resource_skills_list() -> str:
    import json

    from ansible_know.config import SKILLS_DIR

    skills_list: list[str] = []
    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            if not skill_dir.is_dir() or skill_dir.is_symlink():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                skills_list.append(skill_dir.name)
            for sub_dir in sorted(skill_dir.iterdir()):
                if sub_dir.is_dir() and not sub_dir.is_symlink() and (sub_dir / "SKILL.md").exists():
                    skills_list.append(f"{skill_dir.name}.{sub_dir.name}")
    return json.dumps(skills_list, indent=2)


@mcp.resource(
    "skills://{skill_name}",
    name="Skill Content",
    description="Read a generated skill's SKILL.md by FQCN or collection namespace",
)
def resource_skill_content(skill_name: str) -> str:
    from ansible_know.config import SKILLS_DIR

    try:
        validate_skill_name(skill_name)
    except ValidationError as exc:
        return str(exc)

    try:
        return _get_skill_sync(SKILLS_DIR, skill_name)
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
    return json.dumps(
        {name: cfg["description"] for name, cfg in sources.items()},
        indent=2,
    )


# --- Prompts (reusable templates) ---


@mcp.prompt
def review_playbook(playbook_yaml: str) -> str:
    """Review an Ansible playbook against module documentation and best practices."""
    return (
        "Review the following Ansible playbook for correctness, best practices, "
        "and potential issues. Check that modules are used with correct parameters, "
        "FQCNs are used, and the playbook follows idempotency principles.\n\n"
        "Use the search_modules and get_module_doc tools to verify module usage.\n\n"
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
        "5. Use get_module_doc or get_role_doc on relevant content to understand usage"
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
