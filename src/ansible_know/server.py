"""Ansible Know MCP Server.

Provides 10 tools, 4 resources, and 4 prompts for module discovery,
documentation search, Galaxy collection discovery, and skill generation
via the Model Context Protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations

from ansible_know.errors import AnsibleDocError, ValidationError
from ansible_know.validation import (
    sanitize_error,
    truncate_response,
    validate_fqcn,
    validate_install_path,
    validate_keyword,
    validate_namespace,
    validate_path_containment,
    validate_query,
    validate_tags,
    validate_version,
)

logger = logging.getLogger("ansible_know")


@lifespan
async def app_lifespan(server):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=120.0),
        verify=True,
    ) as client:
        yield {"http_client": client}


mcp = FastMCP(
    name="Ansible Know",
    instructions=(
        "Ansible module discovery, documentation, and skill generation. "
        "Workflow: (1) search_collections to discover collections on Galaxy, "
        "(2) ensure_collection to install one for this session, "
        "(3) search_modules to find modules in installed collections, "
        "(4) get_module_doc for structured docs (falls back to Galaxy if not installed), "
        "(5) search_docs for conceptual guides, "
        "(6) generate_skill to create ready-to-use skill packages."
    ),
    lifespan=app_lifespan,
)


def _run_in_executor(func, *args, **kwargs):
    """Run a blocking function in the default executor."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


_MISSING_COLLECTION_PATTERNS = ("has no attribute", "was not found", "could not be found")
_missing_collections: set[str] = set()


def _collection_hint(namespace: str) -> str:
    return (
        f" Collection '{namespace}' not installed locally. "
        f"Use ensure_collection('{namespace}') to install it from Ansible Galaxy "
        f"(latest version, or specify version='X.Y.Z')."
    )


def _is_missing_collection_error(error_msg: str) -> bool:
    """Check if an error message indicates a missing/not-found collection or module."""
    msg_lower = error_msg.lower()
    return any(p in msg_lower for p in _MISSING_COLLECTION_PATTERNS)


def _maybe_add_hint(error_msg: str, namespace: str | None) -> str:
    if namespace and _is_missing_collection_error(error_msg):
        return error_msg + _collection_hint(namespace)
    return error_msg


async def _resolve_module_doc(
    module_name: str, http_client: httpx.AsyncClient | None = None,
) -> tuple[dict, dict | None]:
    """Try local ansible-doc, fall back to Galaxy if the collection is missing.

    Returns (raw_doc, galaxy_meta_or_none). Raises on non-missing-collection
    errors and when both local and Galaxy lookups fail.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    namespace = ".".join(module_name.split(".")[:2]) if "." in module_name else None

    if namespace and namespace in _missing_collections:
        try:
            from ansible_know.galaxy import GalaxyClient
            async with GalaxyClient(http_client=http_client) as client:
                galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            raise CollectionNotFoundError(
                f"Collection '{namespace}' not installed locally"
            ) from galaxy_exc

    try:
        raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
        return raw_doc, None
    except CollectionNotFoundError as local_exc:
        if namespace:
            _missing_collections.add(namespace)
        logger.info("Collection not installed, trying Galaxy: %s", local_exc)
        try:
            from ansible_know.galaxy import GalaxyClient

            async with GalaxyClient(http_client=http_client) as client:
                galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
            return galaxy_doc, galaxy_meta
        except GalaxyError as galaxy_exc:
            logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
            raise local_exc from galaxy_exc


# --- Discovery tools (read-only) ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_modules(
    keyword: Annotated[str, "Search term to match against module names and descriptions"],
    namespace: Annotated[str | None, "Optional collection namespace filter (e.g. 'community.docker')"] = None,
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

        results = await _run_in_executor(parser.search_modules, keyword, namespace)
        if len(results) > SEARCH_MODULES_LIMIT:
            results = dict(list(results.items())[:SEARCH_MODULES_LIMIT])
        return results
    except Exception as exc:
        logger.warning("search_modules failed: %s", exc)
        return {"error": _maybe_add_hint(sanitize_error(str(exc)), namespace)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_module_doc(
    module_name: Annotated[str, "Fully-qualified collection name (e.g. 'ansible.builtin.copy')"],
    ctx: Context = None,
) -> dict[str, Any]:
    """Get full structured documentation for one module.

    Returns: module_name, short_description, params (list with name/type/required/default/choices/description/aliases),
    examples (raw YAML), is_api_module, doc_source ('local' or 'galaxy').
    When doc_source is 'galaxy', also includes doc_version and optionally doc_warning.
    Falls back to Galaxy if collection is not installed locally.
    On failure returns {"error": str}.
    """
    logger.info("get_module_doc module=%r", module_name)
    try:
        validate_fqcn(module_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser

        http_client = ctx.lifespan_context.get("http_client") if ctx else None
        raw_doc, galaxy_meta = await _resolve_module_doc(module_name, http_client=http_client)
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
        return {"error": _maybe_add_hint(sanitize_error(str(exc)), ns)}


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
    ctx: Context = None,
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
    try:
        validate_query(query)
        if tags:
            validate_tags(tags)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know.galaxy import GalaxyClient

        http_client = ctx.lifespan_context.get("http_client") if ctx else None
        async with GalaxyClient(http_client=http_client) as client:
            return await client.search_collections(query, tags=tags)
    except Exception as exc:
        logger.warning("search_collections failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_collection_manifest(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
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

    try:
        from ansible_know import collection_manifest, parser

        cached = collection_manifest.load_cached_manifest(collection_namespace)
        if cached:
            return cached

        modules = await _run_in_executor(parser.search_modules, "", collection_namespace)
        if not modules:
            return {"error": (
                f"No modules found in collection '{collection_namespace}'."
                + _collection_hint(collection_namespace)
            )}

        metadata_list = []
        for module_name in sorted(modules):
            try:
                raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
                metadata_list.append(parser.extract_module_metadata(raw_doc))
            except AnsibleDocError:
                continue

        return collection_manifest.generate_manifest(collection_namespace, metadata_list)
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("get_collection_manifest failed: %s", exc)
        return {"error": _maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, readOnlyHint=False, destructiveHint=False))
async def ensure_collection(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[
        str | None,
        "Optional version (e.g. '4.1.0'). If omitted, installs latest and pins the resolved version.",
    ] = None,
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
    try:
        validate_namespace(collection_namespace)
        if version:
            validate_version(version)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import collections

        result = await _run_in_executor(collections.ensure_collection, collection_namespace, version)
        _missing_collections.discard(collection_namespace)
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
async def list_skills() -> list[dict[str, str]] | dict[str, str]:
    """List all available generated skills. Returns name, description, path for each.

    Returns: [{"name": str, "description": str, "path": str}, ...] or {"error": str} on failure.
    """
    logger.info("list_skills")
    try:
        from ansible_know.config import SKILLS_DIR

        results = []
        if not SKILLS_DIR.exists():
            return results

        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text()
                description = ""
                for line in content.splitlines():
                    if line.startswith("description:"):
                        description = line.partition(":")[2].strip().strip(">-").strip()
                        break
                results.append({
                    "name": skill_dir.name,
                    "description": description,
                    "path": str(skill_dir),
                })

        return results
    except Exception as exc:
        logger.warning("list_skills failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_skill(
    skill_name: Annotated[str, "Skill name (usually the module FQCN)"],
) -> str | dict[str, str]:
    """Read a specific skill's SKILL.md content by name.

    Returns: SKILL.md content as str, or {"error": str} on failure/not found.
    """
    logger.info("get_skill name=%r", skill_name)
    try:
        validate_fqcn(skill_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know.config import SKILLS_DIR

        skill_path = (SKILLS_DIR / skill_name / "SKILL.md").resolve()
        validate_path_containment(skill_path, SKILLS_DIR)
        if not skill_path.exists():
            return {"error": f"Skill '{skill_name}' not found."}
        return truncate_response(skill_path.read_text())
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("get_skill failed: %s", exc)
        return {"error": sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_skill(
    module_name: Annotated[str, "Fully-qualified module name (e.g. 'ansible.builtin.copy')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context = None,
) -> str | dict[str, str]:
    """Generate a skill package for one module.

    Writes SKILL.md + scripts + playbook to disk.
    Returns the SKILL.md content as str, or {"error": str} on failure.
    """
    logger.info("generate_skill module=%r install_to=%r", module_name, install_to)
    try:
        validate_fqcn(module_name)
        if install_to:
            validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        http_client = ctx.lifespan_context.get("http_client") if ctx else None
        raw_doc, galaxy_meta = await _resolve_module_doc(module_name, http_client=http_client)
        metadata = parser.extract_module_metadata(raw_doc)
        if galaxy_meta:
            metadata.update(galaxy_meta)

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        skill_name = skills._module_to_skill_name(metadata["module_name"])
        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        output_dir = base_dir / skill_name

        await _run_in_executor(skills.write_skill_package, output_dir, metadata)
        logger.info("generate_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_skill failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        from ansible_know.errors import GalaxyError
        if isinstance(exc.__cause__, GalaxyError):
            return {"error": sanitize_error(str(exc))}
        return {"error": _maybe_add_hint(sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_collection_skills(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    install_to: Annotated[str | None, "Optional absolute path to install skills to"] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Batch generate skills for an entire collection.

    Generates/updates the collection MANIFEST.json as a byproduct.
    Returns {"succeeded": int, "failed": int, "total": int, "manifest": dict},
    or {"error": str} on failure.
    """
    logger.info("generate_collection_skills namespace=%r install_to=%r", collection_namespace, install_to)
    try:
        validate_namespace(collection_namespace)
        if install_to:
            validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import collection_manifest, parser, skills
        from ansible_know.config import SKILLS_DIR

        modules = await _run_in_executor(parser.search_modules, "", collection_namespace)
        if not modules:
            return {"error": (
                f"No modules found in collection '{collection_namespace}'."
                + _collection_hint(collection_namespace)
            )}

        total = len(modules)
        succeeded = 0
        failed = 0
        metadata_list = []

        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR

        for i, module_name in enumerate(sorted(modules)):
            if ctx:
                await ctx.report_progress(progress=i, total=total)
            try:
                raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
                metadata = parser.extract_module_metadata(raw_doc)
                metadata_list.append(metadata)

                skill_name = skills._module_to_skill_name(metadata["module_name"])
                output_dir = base_dir / skill_name
                await _run_in_executor(skills.write_skill_package, output_dir, metadata)
                succeeded += 1
            except Exception:
                failed += 1

        manifest = collection_manifest.generate_manifest(
            collection_namespace, metadata_list, skills_dir=base_dir,
        )

        if ctx:
            await ctx.report_progress(progress=total, total=total)

        logger.info("generate_collection_skills completed: %d/%d succeeded", succeeded, total)
        return {
            "succeeded": succeeded,
            "failed": failed,
            "total": total,
            "manifest": manifest,
        }
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("generate_collection_skills failed: %s", exc)
        return {"error": _maybe_add_hint(sanitize_error(str(exc)), collection_namespace)}


# --- Resources (read-only data) ---


@mcp.resource("skills://list", name="Available Skills", description="List all generated skill packages")
def resource_skills_list() -> str:
    import json

    from ansible_know.config import SKILLS_DIR

    skills = []
    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                skills.append(skill_dir.name)
    return json.dumps(skills, indent=2)


@mcp.resource(
    "skills://{skill_name}",
    name="Skill Content",
    description="Read a generated skill's SKILL.md by FQCN",
)
def resource_skill_content(skill_name: str) -> str:
    from ansible_know.config import SKILLS_DIR

    try:
        validate_fqcn(skill_name)
    except ValidationError as exc:
        return str(exc)

    skill_path = (SKILLS_DIR / skill_name / "SKILL.md").resolve()
    try:
        validate_path_containment(skill_path, SKILLS_DIR)
    except ValidationError as exc:
        return str(exc)

    if not skill_path.exists():
        return f"Skill '{skill_name}' not found."
    return truncate_response(skill_path.read_text())


@mcp.resource(
    "galaxy://installed",
    name="Installed Collections",
    description="List collections installed in this session via ensure_collection",
)
def resource_installed_collections() -> str:
    from ansible_know import collections

    return json.dumps(collections.list_installed(), indent=2)


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
        "4. Use get_collection_manifest to see all available modules\n"
        "5. Use get_module_doc on the most relevant modules to understand their usage"
    )


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
