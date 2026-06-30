"""Shared type definitions for structured data shapes."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Protocol

if sys.version_info >= (3, 12):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict

if TYPE_CHECKING:
    import httpx

    from ansible_know.galaxy_config import GalaxyServerConfig


class ParamDict(TypedDict):
    """Single parameter extracted from a module's ansible-doc options."""

    name: str
    type: str
    required: bool
    default: Any
    choices: Any
    description: str
    aliases: list[str]


class ModuleMetadata(TypedDict):
    """Module metadata extracted by parser.extract_module_metadata()."""

    module_name: str
    short_description: str
    params: list[ParamDict]
    examples: str
    is_api_module: bool


class EntryPointInfo(TypedDict):
    """Single role entry point with description and options list."""

    description: str
    options: list[ParamDict]


class RoleMetadata(TypedDict):
    """Role metadata extracted by parser.extract_role_metadata()."""

    role_name: str
    short_description: str
    entry_points: dict[str, EntryPointInfo]


class PluginMetadata(TypedDict):
    """Plugin metadata extracted by parser.extract_plugin_metadata()."""

    plugin_name: str
    plugin_type: str
    short_description: str
    params: list[ParamDict]
    examples: str


class RoleManifestInput(TypedDict):
    """Input shape for role metadata passed to manifest/skill generation.

    Distinct from ManifestRoleEntry which adds has_skill (computed internally).
    """

    fqcn: str
    description: str
    has_argument_specs: bool
    entry_points: list[str]


class PluginManifestInput(TypedDict):
    """Input shape for plugin metadata passed to manifest/skill generation.

    Distinct from ManifestPluginEntry which adds has_skill (computed internally).
    """

    fqcn: str
    plugin_type: str
    description: str
    param_count: int


class ManifestPluginEntry(PluginManifestInput):
    """Single plugin entry in a collection manifest.

    Extends PluginManifestInput with has_skill (computed internally).
    """

    has_skill: bool


class _DocProvenanceBase(TypedDict):
    doc_source: str
    doc_version: str


class DocProvenance(_DocProvenanceBase, total=False):
    """Provenance metadata for documentation sourced from Galaxy."""

    doc_warning: str
    doc_source_server: str


class VersionInfo(TypedDict):
    """Version check result from PyPI."""

    installed: str
    latest: str
    outdated: bool
    upgrade_command: str


class ErrorResponse(TypedDict):
    """Standard error shape returned by all tools on failure."""

    error: str


class EnsureCollectionResult(TypedDict):
    """Result of ensure_collection tool."""

    namespace: str
    version: str
    status: str
    message: str


class SkillEntry(TypedDict):
    """Single entry in list_skills output."""

    name: str
    description: str
    path: str


class ModuleTagEntry(TypedDict):
    """Single module entry within a tag group for collection skill rendering."""

    fqcn: str
    short_name: str
    short_description: str
    required_params: list[ParamDict]
    is_api_module: bool


class CollectionSkillContext(TypedDict):
    """Template context for collection-level skill rendering."""

    fqcn: str
    spec_name: str
    collection_version: str | None
    modules_by_tag: dict[str, list[ModuleTagEntry]]
    all_api: bool
    common_params: list[ParamDict]
    module_count: int
    plugins_by_type: dict[str, list[dict[str, str]]]
    namespace: str
    collection_name: str
    plugin_type: str


class ManifestModuleEntry(TypedDict):
    """Single module entry in a collection manifest."""

    fqcn: str
    description: str
    param_count: int
    required_params: list[str]
    is_api_module: bool
    has_skill: bool
    tags: list[str]


class ManifestRoleEntry(RoleManifestInput):
    """Single role entry in a collection manifest.

    Extends RoleManifestInput with has_skill (computed internally).
    """

    has_skill: bool


class ManifestResult(TypedDict):
    """Result of get_collection_manifest / generate_manifest."""

    collection: str
    collection_version: str | None
    generated: str
    module_count: int
    role_count: int
    plugin_count: int
    has_collection_skill: bool
    modules: list[ManifestModuleEntry]
    roles: list[ManifestRoleEntry]
    plugins: list[ManifestPluginEntry]


class _CollectionInfoBase(TypedDict):
    """Required fields for a collection search result entry."""

    namespace: str
    description: str
    tags: list[str]
    latest_version: str
    module_count: int
    role_count: int
    plugin_count: int
    deprecated: bool
    signed: bool


class CollectionInfo(_CollectionInfoBase, total=False):
    """Single collection entry from search_collections.

    Optional fields are populated during enrichment (download_count)
    or added by server.py (source).
    """

    download_count: int
    source: str


class CollectionSearchResult(TypedDict):
    """Result of search_collections tool."""

    query: str
    count: int
    collections: list[CollectionInfo]


class _GetModuleDocResultBase(ModuleMetadata):
    """Required fields for get_module_doc tool return."""

    content_type: str
    doc_source: str


class GetModuleDocResult(_GetModuleDocResultBase, total=False):
    """Full result of get_module_doc tool.

    Extends ModuleMetadata with provenance. When doc_source is 'galaxy',
    includes doc_version and optionally doc_warning/doc_source_server.
    """

    doc_version: str
    doc_warning: str
    doc_source_server: str


class _GetRoleDocResultBase(TypedDict):
    """Required fields for get_role_doc tool return."""

    role_name: str
    content_type: str
    doc_source: str


class GetRoleDocResult(_GetRoleDocResultBase, total=False):
    """Full result of get_role_doc tool.

    Combines role metadata with provenance. Optional fields depend
    on doc_source value ('local', 'galaxy_readme', or 'unavailable').
    """

    short_description: str
    entry_points: dict[str, EntryPointInfo]
    dependencies: list[str]
    examples: str
    doc_version: str
    doc_warning: str
    doc_source_server: str
    error: str


class _GetPluginDocResultBase(PluginMetadata):
    """Required fields for get_plugin_doc tool return."""

    content_type: str
    doc_source: str


class GetPluginDocResult(_GetPluginDocResultBase, total=False):
    """Full result of get_plugin_doc tool.

    Extends PluginMetadata with provenance. When doc_source is 'galaxy',
    includes doc_version and optionally doc_warning/doc_source_server.
    """

    doc_version: str
    doc_warning: str
    doc_source_server: str


class SearchDocsEntry(TypedDict):
    """Single entry from search_docs results."""

    title: str
    summary: str
    topic: list[str]
    audience: list[str]
    lines: int
    source: str
    url: str


class FetchDocResult(TypedDict):
    """Result of fetch_doc tool."""

    content: str
    title: str
    tokens: int
    source_url: str


class GenerateCollectionSkillsResult(TypedDict):
    """Result of generate_collection_skills tool."""

    succeeded: int
    failed: int
    total: int
    manifest: dict[str, Any]
    collection_skill: str


class ClearCacheResult(TypedDict):
    """Result of clear_cache tool."""

    cleared: list[str]


class _CollectionDocsResultBase(TypedDict):
    """Required fields for batch collection docs result."""

    modules: dict[str, ModuleMetadata]
    doc_source: str


class CollectionDocsResult(_CollectionDocsResultBase, total=False):
    """Result of resolve_collection_module_docs / get_collection_docs.

    When doc_source is 'galaxy', includes doc_version and optionally
    doc_warning/doc_source_server.
    """

    doc_version: str
    doc_warning: str
    doc_source_server: str


class GalaxyDocClient(Protocol):
    """Structural interface for Galaxy documentation clients.

    GalaxyClient satisfies this protocol without inheriting from it.
    Defined here (Foundation) so Domain modules can depend on the
    protocol rather than importing the concrete External Access class.
    """

    async def fetch_module_doc(
        self, module_name: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def fetch_role_doc(
        self, role_name: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def fetch_plugin_doc(
        self, plugin_name: str, plugin_type: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def fetch_collection_docs(
        self, collection_namespace: str, version: str | None = None,
    ) -> tuple[dict[str, ModuleMetadata], DocProvenance]: ...

    async def search_collections(
        self, query: str, tags: str | None = None,
    ) -> dict[str, Any]: ...

    async def __aenter__(self) -> GalaxyDocClient: ...

    async def __aexit__(self, *exc: object) -> None: ...


class GalaxyClientFactory(Protocol):
    """Factory that creates GalaxyDocClient instances from config."""

    def __call__(
        self,
        config: GalaxyServerConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> GalaxyDocClient: ...
