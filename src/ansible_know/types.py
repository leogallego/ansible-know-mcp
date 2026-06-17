"""Shared type definitions for structured data shapes."""

from __future__ import annotations

from typing import Any, TypedDict


class ModuleMetadata(TypedDict):
    """Module metadata extracted by parser.extract_module_metadata()."""

    module_name: str
    short_description: str
    params: list[dict[str, Any]]
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
