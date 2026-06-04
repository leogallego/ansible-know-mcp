"""Ansible Know MCP Server.

Provides 8 tools for module discovery, documentation search,
and skill generation via the Model Context Protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

logger = logging.getLogger("ansible_know")

MAX_RESPONSE_SIZE = 500_000  # 500KB
MAX_KEYWORD_LENGTH = 200
MAX_QUERY_LENGTH = 500
MAX_NAMESPACE_LENGTH = 128
MAX_VERSION_LENGTH = 64

_FQCN_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")
_NAMESPACE_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_SENSITIVE_PREFIXES = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev")
_PATH_RE = re.compile(r"/(?:home|tmp|usr|etc|var|opt)/\S+")

mcp = FastMCP(
    name="Ansible Know",
    instructions=(
        "Ansible module discovery, documentation, and skill generation. "
        "Use search_modules to find modules, get_module_doc for details, "
        "search_docs for conceptual guides, and generate_skill to create "
        "ready-to-use skill packages."
    ),
)


class ValidationError(Exception):
    """Raised when tool input fails validation."""


def _validate_fqcn(name: str) -> None:
    if not name or not _FQCN_RE.match(name):
        raise ValidationError(
            f"Invalid module name: expected format 'namespace.collection.module' "
            f"with alphanumeric/underscore segments."
        )


def _validate_namespace(ns: str) -> None:
    if not ns or len(ns) > MAX_NAMESPACE_LENGTH or not _NAMESPACE_RE.match(ns):
        raise ValidationError(
            f"Invalid collection namespace: expected format 'namespace.collection' "
            f"with alphanumeric/underscore segments."
        )


def _validate_keyword(keyword: str) -> None:
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise ValidationError(
            f"Keyword too long: {len(keyword)} chars (max {MAX_KEYWORD_LENGTH})."
        )


def _validate_version(version: str) -> None:
    if not version or len(version) > MAX_VERSION_LENGTH or not _VERSION_RE.match(version):
        raise ValidationError(
            f"Invalid version format: use alphanumeric characters, dots, dashes only."
        )


def _validate_query(query: str) -> None:
    if not query or not query.strip():
        raise ValidationError("Query must not be empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValidationError(
            f"Query too long: {len(query)} chars (max {MAX_QUERY_LENGTH})."
        )


_TAGS_RE = re.compile(r"^[a-zA-Z0-9_,-]+$")
MAX_TAGS_LENGTH = 500


def _validate_tags(tags: str) -> None:
    if len(tags) > MAX_TAGS_LENGTH:
        raise ValidationError(
            f"Tags too long: {len(tags)} chars (max {MAX_TAGS_LENGTH})."
        )
    if not _TAGS_RE.match(tags):
        raise ValidationError(
            "Invalid tags: use alphanumeric characters, hyphens, underscores, and commas only."
        )


def _validate_install_path(path_str: str) -> Path:
    resolved = Path(path_str).resolve()
    for prefix in _SENSITIVE_PREFIXES:
        if str(resolved).startswith(prefix):
            raise ValidationError(
                f"Install path not allowed: cannot write to system directories."
            )
    return resolved


def _validate_path_containment(child: Path, parent: Path) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        raise ValidationError("Path escapes the allowed directory.")


def _sanitize_error(msg: str) -> str:
    return _PATH_RE.sub("<path>", str(msg))


def _truncate_response(text: str) -> str:
    if len(text) > MAX_RESPONSE_SIZE:
        return text[:MAX_RESPONSE_SIZE] + "\n\n[Truncated — response exceeded size limit]"
    return text


def _run_in_executor(func, *args, **kwargs):
    """Run a blocking function in the default executor."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


_MISSING_COLLECTION_PATTERNS = ("has no attribute", "was not found", "could not be found")


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


async def _resolve_module_doc(module_name: str) -> tuple[dict, dict | None]:
    """Try local ansible-doc, fall back to Galaxy if the collection is missing.

    Returns (raw_doc, galaxy_meta_or_none). Raises on non-missing-collection
    errors and when both local and Galaxy lookups fail.
    """
    from ansible_know import parser

    try:
        raw_doc = await _run_in_executor(parser.get_module_doc, module_name)
        return raw_doc, None
    except Exception as local_exc:
        if not _is_missing_collection_error(str(local_exc)):
            raise

        logger.info("Local lookup failed, trying Galaxy: %s", local_exc)
        try:
            from ansible_know.galaxy import GalaxyClient

            client = GalaxyClient()
            galaxy_doc, galaxy_meta = await client.fetch_module_doc(module_name)
            return galaxy_doc, galaxy_meta
        except Exception as galaxy_exc:
            logger.warning("Galaxy fallback also failed: %s", galaxy_exc)
            raise local_exc from galaxy_exc


# --- Discovery tools (read-only) ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_modules(
    keyword: Annotated[str, "Search term to match against module names and descriptions"],
    namespace: Annotated[str | None, "Optional collection namespace filter (e.g. 'community.docker')"] = None,
) -> dict[str, str]:
    """Find Ansible modules by keyword in name or description. Returns up to 50 matches as {fqcn: short_description}."""
    logger.info("search_modules keyword=%r namespace=%r", keyword, namespace)
    try:
        _validate_keyword(keyword)
        if namespace:
            _validate_namespace(namespace)
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
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), namespace)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_module_doc(
    module_name: Annotated[str, "Fully-qualified collection name (e.g. 'ansible.builtin.copy')"],
) -> dict[str, Any]:
    """Get full structured documentation for one module.

    Returns: module_name, short_description, params (list with name/type/required/default/choices/description/aliases),
    examples (raw YAML), is_api_module.
    """
    logger.info("get_module_doc module=%r", module_name)
    try:
        _validate_fqcn(module_name)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser

        raw_doc, galaxy_meta = await _resolve_module_doc(module_name)
        metadata = parser.extract_module_metadata(raw_doc)
        if galaxy_meta:
            metadata.update(galaxy_meta)
        else:
            metadata["doc_source"] = "local"
        return metadata
    except Exception as exc:
        logger.warning("get_module_doc failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), ns)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_docs(
    query: Annotated[str, "Search term to match against documentation titles, summaries, and topics"],
    source: Annotated[str | None, "Filter to a single source (e.g. 'ansible-core')"] = None,
    topic: Annotated[str | None, "Filter by topic tag"] = None,
    audience: Annotated[str | None, "Filter by audience tag"] = None,
    core_only: Annotated[bool, "If true, only return entries marked as core"] = False,
) -> list[dict[str, Any]]:
    """Search documentation manifests for conceptual guides.

    Returns up to 20 matching entries with title, summary, topic, audience, lines, source, and raw URL.
    """
    logger.info("search_docs query=%r", query)
    try:
        _validate_query(query)
    except ValidationError as exc:
        return [{"error": str(exc)}]

    try:
        from ansible_know import docs

        return await docs.search_docs(
            query=query, source=source, topic=topic, audience=audience, core_only=core_only,
        )
    except Exception as exc:
        logger.warning("search_docs failed: %s", exc)
        return [{"error": _sanitize_error(str(exc))}]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_collections(
    query: Annotated[str, "Search keyword (e.g., 'netbox', 'cisco ios', 'vmware')"],
    tags: Annotated[str | None, "Optional comma-separated Galaxy tags to filter (e.g., 'networking,cloud')"] = None,
) -> dict[str, Any]:
    """Search Ansible Galaxy for collections by keyword.

    Returns collections ranked by download count, with module counts
    and descriptions. Use this to discover which collection provides
    modules for a specific platform or use case.
    """
    logger.info("search_collections query=%r tags=%r", query, tags)
    try:
        _validate_query(query)
        if tags:
            _validate_tags(tags)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know.galaxy import GalaxyClient

        client = GalaxyClient()
        return await client.search_collections(query, tags=tags)
    except Exception as exc:
        logger.warning("search_collections failed: %s", exc)
        return {"error": _sanitize_error(str(exc))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_collection_manifest(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
) -> dict[str, Any]:
    """Get collection-level manifest with per-module summaries.

    Returns cached MANIFEST.json if available, otherwise generates on-demand
    (metadata extraction only, no skill generation).
    """
    logger.info("get_collection_manifest namespace=%r", collection_namespace)
    try:
        _validate_namespace(collection_namespace)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser, collection_manifest

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
            except parser.AnsibleDocError:
                continue

        return collection_manifest.generate_manifest(collection_namespace, metadata_list)
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("get_collection_manifest failed: %s", exc)
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), collection_namespace)}


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, readOnlyHint=False))
async def ensure_collection(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[str | None, "Optional version (e.g. '4.1.0'). If omitted, installs latest and pins the resolved version."] = None,
) -> dict[str, Any]:
    """Install a collection to a temporary directory for this session.

    Installs once and pins the resolved version. Subsequent calls with the
    same namespace skip unless a different version is explicitly requested.

    Returns dict with keys: namespace, version, status, message.
    - status: 'installed' (freshly installed) or 'already_installed' (version matched).
    - message: human-readable summary including the active version.
    """
    logger.info("ensure_collection namespace=%r version=%r", collection_namespace, version)
    try:
        _validate_namespace(collection_namespace)
        if version:
            _validate_version(version)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import collections

        result = await _run_in_executor(collections.ensure_collection, collection_namespace, version)
        logger.info(
            "ensure_collection result: namespace=%s version=%s status=%s",
            result["namespace"], result["version"], result["status"],
        )
        return result
    except Exception as exc:
        logger.warning("ensure_collection failed: %s", exc)
        return {"error": _sanitize_error(str(exc))}


# --- Skill management tools ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_skills() -> list[dict[str, str]]:
    """List all available generated skills. Returns name, description, path for each."""
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
        return [{"error": _sanitize_error(str(exc))}]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_skill(
    skill_name: Annotated[str, "Skill name (usually the module FQCN)"],
) -> str:
    """Read a specific skill's SKILL.md content by name."""
    logger.info("get_skill name=%r", skill_name)
    try:
        _validate_fqcn(skill_name)
    except ValidationError as exc:
        return str(exc)

    try:
        from ansible_know.config import SKILLS_DIR

        skill_path = (SKILLS_DIR / skill_name / "SKILL.md").resolve()
        _validate_path_containment(skill_path, SKILLS_DIR)
        if not skill_path.exists():
            return f"Skill '{skill_name}' not found."
        return _truncate_response(skill_path.read_text())
    except ValidationError as exc:
        return str(exc)
    except Exception as exc:
        logger.warning("get_skill failed: %s", exc)
        return _sanitize_error(str(exc))


@mcp.tool
async def generate_skill(
    module_name: Annotated[str, "Fully-qualified module name (e.g. 'ansible.builtin.copy')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context = None,
) -> str:
    """Generate a skill package for one module.

    Writes SKILL.md + scripts + playbook to disk.
    Returns the SKILL.md content inline so the agent can use it immediately.
    """
    logger.info("generate_skill module=%r install_to=%r", module_name, install_to)
    try:
        _validate_fqcn(module_name)
        if install_to:
            _validate_install_path(install_to)
    except ValidationError as exc:
        return str(exc)

    try:
        from ansible_know import parser, skills
        from ansible_know.config import SKILLS_DIR

        if ctx:
            await ctx.report_progress(progress=0, total=100)

        raw_doc, _ = await _resolve_module_doc(module_name)
        metadata = parser.extract_module_metadata(raw_doc)

        if ctx:
            await ctx.report_progress(progress=50, total=100)

        skill_name = skills._module_to_skill_name(metadata["module_name"])
        base_dir = _validate_install_path(install_to) if install_to else SKILLS_DIR
        output_dir = base_dir / skill_name

        await _run_in_executor(skills.write_skill_package, output_dir, metadata)
        logger.info("generate_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return _truncate_response(skills.render_skill(metadata))
    except ValidationError as exc:
        return str(exc)
    except Exception as exc:
        logger.warning("generate_skill failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        return _maybe_add_hint(_sanitize_error(str(exc)), ns)


@mcp.tool
async def generate_collection_skills(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    install_to: Annotated[str | None, "Optional absolute path to install skills to"] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Batch generate skills for an entire collection.

    Generates/updates the collection MANIFEST.json as a byproduct.
    Returns summary (succeeded/failed counts) + manifest content.
    """
    logger.info("generate_collection_skills namespace=%r install_to=%r", collection_namespace, install_to)
    try:
        _validate_namespace(collection_namespace)
        if install_to:
            _validate_install_path(install_to)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import parser, skills, collection_manifest
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

        base_dir = _validate_install_path(install_to) if install_to else SKILLS_DIR

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
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), collection_namespace)}


# --- Resources (read-only data) ---


@mcp.resource("skills://list", name="Available Skills", description="List all generated skill packages")
def resource_skills_list() -> str:
    from ansible_know.config import SKILLS_DIR
    import json

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
        _validate_fqcn(skill_name)
    except ValidationError as exc:
        return str(exc)

    skill_path = (SKILLS_DIR / skill_name / "SKILL.md").resolve()
    try:
        _validate_path_containment(skill_path, SKILLS_DIR)
    except ValidationError as exc:
        return str(exc)

    if not skill_path.exists():
        return f"Skill '{skill_name}' not found."
    return _truncate_response(skill_path.read_text())


@mcp.resource(
    "docs://sources",
    name="Documentation Sources",
    description="List configured documentation manifest sources",
)
def resource_doc_sources() -> str:
    from ansible_know.config import get_doc_sources
    import json

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


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
