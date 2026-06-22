"""Shared type definitions for structured data shapes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypedDict

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


class RoleMetadata(TypedDict):
    """Role metadata extracted by parser.extract_role_metadata()."""

    role_name: str
    short_description: str
    entry_points: dict[str, dict[str, Any]]


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

    collection_namespace: str
    collection_version: str | None
    modules_by_tag: dict[str, list[ModuleTagEntry]]
    all_api: bool
    common_params: list[ParamDict]
    module_count: int


class _CollectionInfoBase(TypedDict):
    """Required fields for a collection search result entry."""

    namespace: str
    description: str
    tags: list[str]
    latest_version: str
    module_count: int
    role_count: int
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
