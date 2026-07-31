# RTD Documentation Discovery & Retrieval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Subagent skills:** Each dispatched subagent should use `superpowers:test-driven-development` (tasks follow TDD: write test → verify fail → implement → verify pass → commit) and `superpowers:verification-before-completion` (run tests + lint before claiming done).
>
> **Orchestrator skills:** The main session should use `superpowers:subagent-driven-development` (dispatch + review loop), `superpowers:requesting-code-review` (review each task's output before proceeding), `superpowers:receiving-code-review` (if review feedback requires changes), `superpowers:finishing-a-development-branch` (PR creation), and local `skills/pr-architecture-review` (architecture review before merge).

**Goal:** Replace the forked `leogallego/ansible-documentation:ai-docs` manifest with RTD-native documentation discovery (objects.inv + sitemap manifests) and retrieval (`fetch_doc` tool via Cloudflare markdown endpoint).

**Architecture:** Ship per-project doc manifests as JSON files in the package. `search_docs` loads them from disk (no HTTP at startup). A new `fetch_doc` tool retrieves page content on demand via `Accept: text/markdown` content negotiation. RTD Search API serves as fallback when manifest search returns empty.

**Tech Stack:** Python 3.10+, httpx, sphobjinv, FastMCP, pytest

**Spec:** `docs/superpowers/specs/2026-06-22-rtd-docs-integration-design.md`

## Global Constraints

- Python ≥ 3.10, use `from __future__ import annotations` in every new file
- `typing_extensions.TypedDict` on Python < 3.12 (see `types.py` pattern)
- All tools return `TypedDict | ErrorResponse` — never raise from tool functions
- Tests mock HTTP — no network in unit tests. Integration tests use `@pytest.mark.integration`
- `ruff check src/ tests/` must pass (line-length 120, see `pyproject.toml [tool.ruff]`)
- Use `.venv/bin/pytest` and `.venv/bin/ruff` — never `source activate`
- Never prefix commands with env var assignments (sandbox restriction)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ansible_know/config.py` | Modify | Add `AUDIENCE_MAP`, `CORE_PAGES`, `GUIDE_TOPIC_PREFIXES`, `PROJECT_BASE_URLS`, `RTD_PROJECT_SLUGS`. Change `DEFAULT_DOC_SOURCES` from URL to file paths. |
| `src/ansible_know/docs.py` | Modify | Split `_fetch_manifest` → `_load_manifest_file` / `_fetch_manifest_url` / `_get_manifest`. Add `http_client` param to `search_docs`. Add `_clean_rtd_markdown`, `fetch_doc_content`, `_search_rtd_api`. |
| `src/ansible_know/types.py` | Modify | Add `FetchDocResult` TypedDict. |
| `src/ansible_know/validation.py` | Modify | Add `validate_doc_url()`. |
| `src/ansible_know/server.py` | Modify | Add `fetch_doc` tool. Thread `http_client` to `search_docs`. Update `instructions`. Update `docs://sources` resource. |
| `src/ansible_know/manifest_builder.py` | Create | objects.inv parser, sitemap parser, manifest generator — build-time only. |
| `src/ansible_know/data/` | Create | Directory for shipped manifest JSON files. |
| `scripts/build_docs_manifests.py` | Create | CLI entry point for CI manifest generation. |
| `tests/test_docs.py` | Modify | Add file-based loading tests, `_clean_rtd_markdown` tests, `_search_rtd_api` tests. |
| `tests/test_validation.py` | Modify | Add `validate_doc_url` tests. |
| `tests/test_manifest_builder.py` | Create | Unit tests for manifest builder functions. |
| `pyproject.toml` | Modify | Add `sphobjinv>=2.3` and `defusedxml>=0.7` to dependencies. |

---

### Task 1: Curated config constants and `DEFAULT_DOC_SOURCES` migration

Move curated metadata into `config.py` and switch `DEFAULT_DOC_SOURCES` from fork URL to local file paths. Create the `data/` directory with a hand-crafted seed manifest.

**Files:**
- Modify: `src/ansible_know/config.py`
- Create: `src/ansible_know/data/ansible_core_manifest.json`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `AUDIENCE_MAP: dict[str, str]`, `CORE_PAGES: dict[str, list[str]]`, `GUIDE_TOPIC_PREFIXES: set[str]`, `PROJECT_BASE_URLS: dict[str, str]`, `RTD_PROJECT_SLUGS: dict[str, str]`, `DEFAULT_DOC_SOURCES` (with `"file"` keys), `get_doc_sources() -> dict[str, dict[str, str]]`

- [ ] **Step 1: Create `src/ansible_know/data/` directory and seed manifest**

```bash
mkdir -p src/ansible_know/data
```

Write `src/ansible_know/data/ansible_core_manifest.json` — a hand-crafted seed with ~10 representative entries that exercises all manifest fields. This will be overwritten by the real builder in Task 6.

```json
{
  "version": "2.0",
  "generated": "2026-06-22T00:00:00Z",
  "base_url": "https://docs.ansible.com/projects/ansible/latest",
  "files": [
    {
      "path": "playbook_guide/playbooks_intro.html",
      "topic": "playbook_guide",
      "title": "Ansible playbooks",
      "audience": "author",
      "core": true,
      "summary": "Playbooks are automation blueprints, in a simple, repeatable, re-usable, and sharable format.",
      "lines": 150,
      "tokens": 2855
    },
    {
      "path": "playbook_guide/playbooks_variables.html",
      "topic": "playbook_guide",
      "title": "Using Variables",
      "audience": "author",
      "core": true,
      "summary": "Ansible uses variables to manage differences between systems.",
      "lines": 300,
      "tokens": 5200
    },
    {
      "path": "playbook_guide/playbooks_loops.html",
      "topic": "playbook_guide",
      "title": "Loops",
      "audience": "author",
      "core": true,
      "summary": "Sometimes you want to repeat a task multiple times with different values.",
      "lines": 200,
      "tokens": 3100
    },
    {
      "path": "inventory_guide/intro_inventory.html",
      "topic": "inventory_guide",
      "title": "How to build your inventory",
      "audience": "author",
      "core": true,
      "summary": "Ansible automates tasks on managed nodes or hosts in your infrastructure.",
      "lines": 400,
      "tokens": 7000
    },
    {
      "path": "vault_guide/vault_encrypting_content.html",
      "topic": "vault_guide",
      "title": "Encrypting content with Ansible Vault",
      "audience": "author",
      "core": true,
      "summary": "Ansible Vault encrypts variables and files so you can protect sensitive content.",
      "lines": 180,
      "tokens": 3200
    },
    {
      "path": "reference_appendices/config.html",
      "topic": "reference_appendices",
      "title": "Ansible Configuration Settings",
      "audience": "both",
      "core": true,
      "summary": "Ansible supports several sources for configuring its behavior.",
      "lines": 800,
      "tokens": 15000
    },
    {
      "path": "dev_guide/developing_collections.html",
      "topic": "dev_guide",
      "title": "Developing collections",
      "audience": "developer",
      "core": true,
      "summary": "Collections are a distribution format for Ansible content.",
      "lines": 350,
      "tokens": 6000
    },
    {
      "path": "getting_started/basic_concepts.html",
      "topic": "getting_started",
      "title": "Ansible concepts",
      "audience": "author",
      "core": true,
      "summary": "These concepts are common to all uses of Ansible.",
      "lines": 120,
      "tokens": 2000
    },
    {
      "path": "playbook_guide/playbooks_filters.html",
      "topic": "playbook_guide",
      "title": "Using filters to manipulate data",
      "audience": "author",
      "core": false,
      "summary": "",
      "lines": 0,
      "tokens": 0
    },
    {
      "path": "porting_guides/porting_guide_core_2.18.html",
      "topic": "porting_guides",
      "title": "Porting guide for Ansible-core 2.18",
      "audience": "both",
      "core": false,
      "summary": "",
      "lines": 0,
      "tokens": 0
    }
  ]
}
```

- [ ] **Step 2: Write test for new config constants**

In `tests/test_config.py`, add tests that the new constants exist and have correct types:

```python
class TestDocCurationConfig:
    def test_audience_map_has_entries(self):
        from ansible_know.config import AUDIENCE_MAP
        assert isinstance(AUDIENCE_MAP, dict)
        assert len(AUDIENCE_MAP) == 8
        assert AUDIENCE_MAP["dev_guide"] == "developer"
        assert AUDIENCE_MAP["playbook_guide"] == "author"

    def test_core_pages_has_ansible_key(self):
        from ansible_know.config import CORE_PAGES
        assert "ansible" in CORE_PAGES
        assert len(CORE_PAGES["ansible"]) >= 30
        assert "playbook_guide/playbooks_intro.html" in CORE_PAGES["ansible"]

    def test_core_pages_has_ecosystem_keys(self):
        from ansible_know.config import CORE_PAGES
        for key in ("lint", "navigator", "builder", "creator", "molecule"):
            assert key in CORE_PAGES, f"Missing CORE_PAGES key: {key}"
            assert len(CORE_PAGES[key]) >= 2

    def test_guide_topic_prefixes(self):
        from ansible_know.config import GUIDE_TOPIC_PREFIXES
        assert isinstance(GUIDE_TOPIC_PREFIXES, set)
        assert "playbook_guide" in GUIDE_TOPIC_PREFIXES
        assert "collections" not in GUIDE_TOPIC_PREFIXES

    def test_project_base_urls(self):
        from ansible_know.config import PROJECT_BASE_URLS
        assert PROJECT_BASE_URLS["ansible"] == "https://docs.ansible.com/projects/ansible/latest"
        assert "lint" in PROJECT_BASE_URLS

    def test_rtd_project_slugs_use_source_names(self):
        from ansible_know.config import RTD_PROJECT_SLUGS
        assert RTD_PROJECT_SLUGS["ansible-core"] == "package-doc-builds"
        assert "ansible-lint" in RTD_PROJECT_SLUGS

    def test_default_doc_sources_use_file_keys(self):
        from ansible_know.config import DEFAULT_DOC_SOURCES
        for name, cfg in DEFAULT_DOC_SOURCES.items():
            assert "file" in cfg, f"Source '{name}' missing 'file' key"
            assert "description" in cfg
```

- [ ] **Step 3: Run tests — expect FAIL (constants don't exist yet)**

```bash
.venv/bin/pytest tests/test_config.py::TestDocCurationConfig -v
```

Expected: `ImportError` or `AttributeError` for the new constants.

- [ ] **Step 4: Add constants and update `DEFAULT_DOC_SOURCES` in `config.py`**

Add after the existing `SKILLS_DIR` `__getattr__` function:

```python
AUDIENCE_MAP: dict[str, str] = {
    "dev_guide": "developer",
    "playbook_guide": "author",
    "inventory_guide": "author",
    "getting_started": "author",
    "getting_started_ee": "author",
    "vault_guide": "author",
    "tips_tricks": "author",
    "command_guide": "author",
}

GUIDE_TOPIC_PREFIXES: set[str] = {
    "playbook_guide",
    "inventory_guide",
    "dev_guide",
    "vault_guide",
    "getting_started",
    "getting_started_ee",
    "reference_appendices",
    "porting_guides",
    "collections_guide",
    "command_guide",
    "tips_tricks",
    "network",
    "os_guide",
    "scenario_guides",
    "user_guide",
    "community",
    "roadmap",
}

CORE_PAGES: dict[str, list[str]] = {
    "ansible": [
        "playbook_guide/playbooks_intro.html",
        "playbook_guide/playbooks_variables.html",
        "playbook_guide/playbooks_loops.html",
        "playbook_guide/playbooks_conditionals.html",
        "playbook_guide/playbooks_error_handling.html",
        "playbook_guide/playbooks_reuse_roles.html",
        "playbook_guide/playbooks_handlers.html",
        "playbook_guide/playbooks_blocks.html",
        "playbook_guide/playbooks_filters.html",
        "playbook_guide/playbooks_tests.html",
        "playbook_guide/playbooks_vars_facts.html",
        "playbook_guide/playbooks_tags.html",
        "playbook_guide/playbooks_privilege_escalation.html",
        "inventory_guide/intro_inventory.html",
        "inventory_guide/intro_dynamic_inventory.html",
        "inventory_guide/intro_patterns.html",
        "inventory_guide/connection_details.html",
        "vault_guide/vault_encrypting_content.html",
        "vault_guide/vault_managing_passwords.html",
        "vault_guide/vault_using_encrypted_content.html",
        "reference_appendices/config.html",
        "reference_appendices/playbooks_keywords.html",
        "reference_appendices/special_variables.html",
        "reference_appendices/general_precedence.html",
        "collections_guide/collections_using.html",
        "collections_guide/collections_installing.html",
        "dev_guide/developing_collections.html",
        "dev_guide/developing_modules_general.html",
        "dev_guide/developing_plugins.html",
        "dev_guide/testing.html",
        "dev_guide/developing_collections_structure.html",
        "getting_started/get_started_playbook.html",
        "getting_started/basic_concepts.html",
        "getting_started/get_started_inventory.html",
    ],
    "lint": [
        "",
        "configuring/",
        "rules/",
        "profiles/",
        "usage/",
    ],
    "navigator": [
        "",
        "installation/",
        "settings/",
        "subcommands/",
    ],
    "builder": [
        "",
        "definition/",
        "usage/",
    ],
    "creator": [
        "",
        "content_creation/",
        "ee_scaffolding/",
    ],
    "molecule": [
        "",
        "getting-started-collections/",
        "configuration/",
        "usage/",
    ],
}

PROJECT_BASE_URLS: dict[str, str] = {
    "ansible": "https://docs.ansible.com/projects/ansible/latest",
    "lint": "https://docs.ansible.com/projects/lint",
    "navigator": "https://docs.ansible.com/projects/navigator",
    "builder": "https://docs.ansible.com/projects/builder/en/latest",
    "creator": "https://docs.ansible.com/projects/creator",
    "molecule": "https://docs.ansible.com/projects/molecule",
}

RTD_PROJECT_SLUGS: dict[str, str] = {
    "ansible-core": "package-doc-builds",
    "ansible-lint": "ansible-lint",
    "ansible-navigator": "ansible-navigator",
    "ansible-builder": "ansible-builder",
    "ansible-creator": "ansible-creator",
    "molecule": "molecule",
}
```

Replace the existing `DEFAULT_DOC_SOURCES` (lines 24-29) with:

```python
DEFAULT_DOC_SOURCES: dict[str, dict[str, str]] = {
    "ansible-core": {
        "file": str(_PKG_DIR / "data" / "ansible_core_manifest.json"),
        "description": "Ansible core — playbook guides, inventory, vault, developer guides, reference",
    },
    "ansible-lint": {
        "file": str(_PKG_DIR / "data" / "ansible_lint_manifest.json"),
        "description": "ansible-lint — rules, configuration, profiles",
    },
    "ansible-navigator": {
        "file": str(_PKG_DIR / "data" / "ansible_navigator_manifest.json"),
        "description": "ansible-navigator — settings, subcommands",
    },
    "ansible-builder": {
        "file": str(_PKG_DIR / "data" / "ansible_builder_manifest.json"),
        "description": "ansible-builder — EE definitions, scenarios, usage",
    },
    "ansible-creator": {
        "file": str(_PKG_DIR / "data" / "ansible_creator_manifest.json"),
        "description": "ansible-creator — content creation, EE scaffolding",
    },
    "molecule": {
        "file": str(_PKG_DIR / "data" / "molecule_manifest.json"),
        "description": "molecule — test scenarios, configuration, getting started",
    },
}
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
.venv/bin/pytest tests/test_config.py::TestDocCurationConfig -v
```

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
.venv/bin/pytest tests/ -v --tb=short
```

The existing `test_docs.py` tests will likely FAIL because `search_docs` now looks for a `"file"` key instead of `"url"`. This is expected — Task 2 fixes `docs.py`.

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/config.py src/ansible_know/data/ansible_core_manifest.json tests/test_config.py
git commit -m "feat: add RTD doc curation config and seed manifest

Add AUDIENCE_MAP, CORE_PAGES, GUIDE_TOPIC_PREFIXES, PROJECT_BASE_URLS,
RTD_PROJECT_SLUGS to config.py. Switch DEFAULT_DOC_SOURCES from fork
URL to local file paths. Ship seed ansible_core_manifest.json.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Refactor `docs.py` — file-based manifest loading + `http_client` threading

Split `_fetch_manifest` into file/URL loaders, add `http_client` parameter to `search_docs`, and wire it through from `server.py`.

**Files:**
- Modify: `src/ansible_know/docs.py`
- Modify: `src/ansible_know/server.py` (only the `search_docs` tool function)
- Modify: `tests/test_docs.py`

**Interfaces:**
- Consumes: `config.get_doc_sources()` (now returns dicts with `"file"` key), `BoundedCache`
- Produces: `search_docs(query, source, topic, audience, core_only, http_client) -> list[dict]`, `clear_cache() -> None`

- [ ] **Step 1: Write tests for file-based manifest loading**

Replace the existing `mock_httpx` fixture and tests in `tests/test_docs.py` with tests that exercise both file-based and URL-based loading:

```python
"""Tests for ansible_know.docs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ansible_know.docs import clear_cache, search_docs

MOCK_MANIFEST = {
    "version": "2.0",
    "generated": "2026-01-01T00:00:00Z",
    "base_url": "https://docs.example.com",
    "files": [
        {
            "path": "guide/intro.html",
            "topic": "guide",
            "title": "Introduction Guide",
            "summary": "How to get started with Ansible playbooks",
            "audience": "author",
            "core": True,
            "lines": 500,
        },
        {
            "path": "reference/variables.html",
            "topic": "reference",
            "title": "Variable Precedence",
            "summary": "Understanding Ansible variable precedence rules",
            "audience": "advanced",
            "core": True,
            "lines": 200,
        },
        {
            "path": "guide/galaxy.html",
            "topic": "guide",
            "title": "Galaxy User Guide",
            "summary": "How to use Ansible Galaxy to find and install roles",
            "audience": "beginner",
            "core": False,
            "lines": 300,
        },
    ],
}


@pytest.fixture(autouse=True)
def _clear_manifest_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def manifest_file(tmp_path):
    """Write MOCK_MANIFEST to a temp file and return its path."""
    p = tmp_path / "test_manifest.json"
    p.write_text(json.dumps(MOCK_MANIFEST))
    return str(p)


@pytest.fixture
def file_sources(manifest_file):
    """Patch get_doc_sources to return a file-based source."""
    sources = {
        "test-source": {
            "file": manifest_file,
            "description": "Test source",
        },
    }
    with patch("ansible_know.docs.get_doc_sources", return_value=sources):
        yield


class TestSearchDocsFileLoading:
    @pytest.mark.asyncio
    async def test_search_by_keyword(self, file_sources):
        results = await search_docs("playbook")
        assert len(results) == 1
        assert results[0]["title"] == "Introduction Guide"
        assert results[0]["source"] == "test-source"

    @pytest.mark.asyncio
    async def test_search_returns_url(self, file_sources):
        results = await search_docs("playbook")
        assert results[0]["url"] == "https://docs.example.com/guide/intro.html"

    @pytest.mark.asyncio
    async def test_search_returns_multiple(self, file_sources):
        results = await search_docs("ansible")
        assert len(results) >= 2

    @pytest.mark.asyncio
    async def test_filter_by_topic(self, file_sources):
        results = await search_docs("", topic="reference")
        assert len(results) == 1
        assert results[0]["title"] == "Variable Precedence"

    @pytest.mark.asyncio
    async def test_filter_by_audience(self, file_sources):
        results = await search_docs("", audience="advanced")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_core_only(self, file_sources):
        results = await search_docs("", core_only=True)
        titles = [r["title"] for r in results]
        assert "Galaxy User Guide" not in titles

    @pytest.mark.asyncio
    async def test_no_results(self, file_sources):
        results = await search_docs("nonexistent_xyz_query")
        assert results == []

    @pytest.mark.asyncio
    async def test_caches_after_first_load(self, file_sources, manifest_file):
        await search_docs("playbook")
        # Delete the file — cached version should still work
        Path(manifest_file).unlink()
        results = await search_docs("variable")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self):
        sources = {
            "missing": {
                "file": "/nonexistent/path/manifest.json",
                "description": "Missing",
            },
        }
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_manifest_version_warning(self, tmp_path, caplog):
        manifest = {**MOCK_MANIFEST, "version": "3.0"}
        p = tmp_path / "v3.json"
        p.write_text(json.dumps(manifest))
        sources = {"future": {"file": str(p), "description": "Future"}}
        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("playbook")
        assert len(results) >= 1
        assert any("version" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.venv/bin/pytest tests/test_docs.py::TestSearchDocsFileLoading -v
```

Expected: FAIL because `docs.py` still only supports URL-based loading.

- [ ] **Step 3: Rewrite `docs.py` with split loaders**

Replace the contents of `src/ansible_know/docs.py`:

```python
"""Multi-manifest documentation client.

Manages a registry of documentation manifest sources, loads from local
files (shipped with the package) or HTTP URLs (user overrides), caches
per-source, and provides cross-source search.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from ansible_know.cache import BoundedCache
from ansible_know.config import SEARCH_DOCS_LIMIT, get_doc_sources

logger = logging.getLogger("ansible_know")

__all__ = [
    "clear_cache",
    "search_docs",
]

MAX_MANIFEST_SIZE = 5_000_000  # 5MB
CACHE_TTL_SECONDS = 3600
MANIFEST_VERSION_MAJOR = "2"

_manifest_cache: BoundedCache[str, list[dict[str, Any]]] = BoundedCache(
    max_size=50, ttl=CACHE_TTL_SECONDS,
)


def _postprocess_entries(
    entries: list[dict[str, Any]], source_name: str, base_url: str,
) -> list[dict[str, Any]]:
    """Add _source tag and construct URLs from base_url + path."""
    for entry in entries:
        entry["_source"] = source_name
        if "url" not in entry and "path" in entry and base_url:
            entry["url"] = f"{base_url.rstrip('/')}/{entry['path'].lstrip('/')}"
    return entries


def _load_manifest_file(source_name: str, file_path: str) -> list[dict[str, Any]]:
    """Load manifest from a local JSON file. Returns empty on error."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    try:
        with open(file_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("Manifest file not found for '%s': %s", source_name, file_path)
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load manifest '%s': %s", source_name, exc)
        return []

    version = data.get("version", "1.0") if isinstance(data, dict) else "1.0"
    if not version.startswith(f"{MANIFEST_VERSION_MAJOR}."):
        logger.warning(
            "Manifest '%s' has version %s (expected %s.x) — some fields may be unrecognized",
            source_name, version, MANIFEST_VERSION_MAJOR,
        )

    base_url = data.get("base_url", "") if isinstance(data, dict) else ""
    entries = data if isinstance(data, list) else data.get("files", data.get("documents", data.get("entries", [])))
    entries = _postprocess_entries(entries, source_name, base_url)

    _manifest_cache.put(source_name, entries)
    return entries


async def _fetch_manifest_url(
    source_name: str,
    url: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch manifest from a URL. Returns empty on error."""
    cached = _manifest_cache.get(source_name)
    if cached is not None:
        return cached

    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))
        should_close = True

    try:
        resp = await client.get(url)
        resp.raise_for_status()
        content_length = resp.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = None
            if size is not None and size > MAX_MANIFEST_SIZE:
                raise ValueError(f"Manifest too large: {content_length} bytes (max {MAX_MANIFEST_SIZE})")
        if len(resp.content) > MAX_MANIFEST_SIZE:
            raise ValueError(f"Manifest too large: {len(resp.content)} bytes (max {MAX_MANIFEST_SIZE})")
        data = resp.json()
    finally:
        if should_close:
            await client.aclose()

    base_url = data.get("base_url", "") if isinstance(data, dict) else ""
    entries = data if isinstance(data, list) else data.get("files", data.get("documents", data.get("entries", [])))
    entries = _postprocess_entries(entries, source_name, base_url)

    _manifest_cache.put(source_name, entries)
    return entries


async def _get_manifest(
    source_name: str,
    src_config: dict[str, str],
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Load manifest from file or URL based on source config."""
    if "file" in src_config:
        return _load_manifest_file(source_name, src_config["file"])
    if "url" in src_config:
        return await _fetch_manifest_url(source_name, src_config["url"], http_client)
    logger.warning("Doc source '%s' has neither 'file' nor 'url', skipping", source_name)
    return []


async def search_docs(
    query: str,
    source: str | None = None,
    topic: str | None = None,
    audience: str | None = None,
    core_only: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search documentation manifests for conceptual guides.

    Args:
        query: Search term (matched against title, summary, topics).
        source: Filter to a single source name (e.g. "ansible-core").
        topic: Filter by topic tag.
        audience: Filter by audience tag.
        core_only: If True, only return entries marked as core.
        http_client: Optional shared httpx client for URL-based sources.

    Returns:
        Up to SEARCH_DOCS_LIMIT matching entries with source info.
    """
    sources = get_doc_sources()
    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    for src_name, src_config in sources.items():
        if source and src_name != source:
            continue

        try:
            entries = await _get_manifest(src_name, src_config, http_client)
        except (httpx.HTTPError, ValueError):
            continue

        for entry in entries:
            if core_only and not entry.get("core", False):
                continue

            entry_topics = entry.get("topics", entry.get("topic", []))
            if isinstance(entry_topics, str):
                entry_topics = [entry_topics]
            entry_audience = entry.get("audience", [])
            if isinstance(entry_audience, str):
                entry_audience = [entry_audience]

            if topic and topic.lower() not in [t.lower() for t in entry_topics]:
                continue
            if audience and audience.lower() not in [a.lower() for a in entry_audience]:
                continue

            title = entry.get("title", "").lower()
            summary = entry.get("summary", "").lower()
            topics_str = " ".join(t.lower() for t in entry_topics)
            searchable = f"{title} {summary} {topics_str}"

            if query_lower in searchable:
                result = {
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "topic": entry_topics,
                    "audience": entry_audience,
                    "lines": entry.get("lines", 0),
                    "source": src_name,
                    "url": entry.get("url", ""),
                }
                results.append(result)

            if len(results) >= SEARCH_DOCS_LIMIT:
                break

    return results[:SEARCH_DOCS_LIMIT]


def clear_cache() -> None:
    """Clear the manifest cache."""
    _manifest_cache.clear()
```

- [ ] **Step 4: Update `server.py` search_docs tool to pass `http_client`**

In `src/ansible_know/server.py`, modify the `search_docs` tool function (around line 399) to accept `ctx` and pass `http_client`:

```python
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
```

- [ ] **Step 5: Run file-loading tests — expect PASS**

```bash
.venv/bin/pytest tests/test_docs.py::TestSearchDocsFileLoading -v
```

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --tb=short
```

Fix any remaining failures from old tests that relied on URL-only loading. The old `TestSearchDocs` and `TestManifestSizeLimit` classes may need updating or removal since the mock pattern changed. Keep the size-limit tests but use URL-based source config in the fixture.

- [ ] **Step 7: Lint**

```bash
.venv/bin/ruff check src/ansible_know/docs.py src/ansible_know/server.py tests/test_docs.py
```

- [ ] **Step 8: Commit**

```bash
git add src/ansible_know/docs.py src/ansible_know/server.py tests/test_docs.py
git commit -m "refactor: split manifest loading into file/URL paths

Replace _fetch_manifest with _load_manifest_file, _fetch_manifest_url,
and _get_manifest dispatcher. Add http_client param to search_docs.
Thread lifespan client from server.py.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: `validate_doc_url` and `FetchDocResult` type

Add URL validation and the return type for `fetch_doc`.

**Files:**
- Modify: `src/ansible_know/validation.py`
- Modify: `src/ansible_know/types.py`
- Modify: `tests/test_validation.py`

**Interfaces:**
- Consumes: `ValidationError` from `errors.py`
- Produces: `validate_doc_url(url: str) -> None` (raises `ValidationError`), `FetchDocResult` TypedDict

- [ ] **Step 1: Write tests for `validate_doc_url`**

Add to `tests/test_validation.py`:

```python
from ansible_know.validation import validate_doc_url


class TestValidateDocUrl:
    def test_valid_ansible_core_url(self):
        validate_doc_url("https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html")

    def test_valid_ecosystem_url(self):
        validate_doc_url("https://docs.ansible.com/projects/lint/rules/")

    def test_valid_old_format_url(self):
        validate_doc_url("https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_intro.html")

    def test_rejects_non_ansible_domain(self):
        with pytest.raises(ValidationError):
            validate_doc_url("https://example.com/docs/page.html")

    def test_rejects_http(self):
        with pytest.raises(ValidationError):
            validate_doc_url("http://docs.ansible.com/page.html")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_doc_url("")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValidationError):
            validate_doc_url("docs.ansible.com/page.html")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_doc_url("https://docs.ansible.com/" + "a" * 2000)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.venv/bin/pytest tests/test_validation.py::TestValidateDocUrl -v
```

- [ ] **Step 3: Implement `validate_doc_url` in `validation.py`**

Add to `src/ansible_know/validation.py`:

```python
MAX_URL_LENGTH = 2048

def validate_doc_url(url: str) -> None:
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            f"URL must be non-empty and under {MAX_URL_LENGTH} characters."
        )
    if not url.startswith("https://docs.ansible.com/"):
        raise ValidationError(
            "URL must start with https://docs.ansible.com/"
        )
```

- [ ] **Step 4: Add `FetchDocResult` to `types.py`**

Add to `src/ansible_know/types.py` after `SearchDocsEntry`:

```python
class FetchDocResult(TypedDict):
    """Result of fetch_doc tool."""

    content: str
    title: str
    tokens: int
    source_url: str
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
.venv/bin/pytest tests/test_validation.py::TestValidateDocUrl -v
```

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/validation.py src/ansible_know/types.py tests/test_validation.py
git commit -m "feat: add validate_doc_url and FetchDocResult type

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: `_clean_rtd_markdown` and `fetch_doc_content` in `docs.py`

Add the markdown cleaning function and the HTTP fetch function that `fetch_doc` delegates to.

**Files:**
- Modify: `src/ansible_know/docs.py`
- Modify: `tests/test_docs.py`

**Interfaces:**
- Consumes: `validate_doc_url`, `truncate_response`, `FetchDocResult`, `ErrorResponse`
- Produces: `_clean_rtd_markdown(raw: str) -> tuple[str, str]`, `fetch_doc_content(url: str, max_tokens: int | None, http_client: httpx.AsyncClient | None) -> FetchDocResult | ErrorResponse`

- [ ] **Step 1: Write tests for `_clean_rtd_markdown`**

Add to `tests/test_docs.py`:

```python
from ansible_know.docs import _clean_rtd_markdown


class TestCleanRtdMarkdown:
    def test_strips_breadcrumbs_before_h1(self):
        raw = "[Home](/) > [Guides](/guides)\n\n# My Page Title\n\nContent here."
        content, title = _clean_rtd_markdown(raw)
        assert title == "My Page Title"
        assert content.startswith("# My Page Title")
        assert "Home" not in content

    def test_strips_doctype_artifact(self):
        raw = "<!DOCTYPE html>\n[Nav](/nav)\n\n# Title\n\nBody."
        content, title = _clean_rtd_markdown(raw)
        assert "DOCTYPE" not in content
        assert title == "Title"

    def test_no_h1_keeps_all_content(self):
        raw = "Some content without any heading.\n\nMore content."
        content, title = _clean_rtd_markdown(raw)
        assert content == raw
        assert title == ""

    def test_strips_anchor_from_title(self):
        raw = "# Page Title {#page-title}\n\nBody."
        content, title = _clean_rtd_markdown(raw)
        assert title == "Page Title"

    def test_collapses_excessive_blank_lines(self):
        raw = "# Title\n\n\n\n\n\nContent."
        content, title = _clean_rtd_markdown(raw)
        assert "\n\n\n" not in content
        assert "Content." in content

    def test_h2_before_h1_is_treated_as_nav(self):
        raw = "## Sidebar\n\nNav links\n\n# Main Title\n\nReal content."
        content, title = _clean_rtd_markdown(raw)
        assert title == "Main Title"
        assert "Sidebar" not in content

    def test_empty_input(self):
        content, title = _clean_rtd_markdown("")
        assert content == ""
        assert title == ""

    def test_doctype_on_later_line(self):
        raw = "Nav\n<!DOCTYPE html>\nMore nav\n# Title\n\nBody."
        content, title = _clean_rtd_markdown(raw)
        assert "DOCTYPE" not in content
        assert title == "Title"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.venv/bin/pytest tests/test_docs.py::TestCleanRtdMarkdown -v
```

- [ ] **Step 3: Implement `_clean_rtd_markdown` in `docs.py`**

Add to `src/ansible_know/docs.py`:

```python
import re

_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+html>", re.IGNORECASE)
_H1_RE = re.compile(r"^# (.+?)(?:\s*\{#[\w-]+\})?\s*$", re.MULTILINE)
_EXCESS_BLANKS_RE = re.compile(r"\n{3,}")


def _clean_rtd_markdown(raw: str) -> tuple[str, str]:
    """Clean RTD markdown output and extract title.

    Returns (cleaned_content, title). Title is empty string if no H1 found.
    """
    if not raw:
        return "", ""

    lines = raw.split("\n")
    for i, line in enumerate(lines[:5]):
        if _DOCTYPE_RE.search(line):
            lines[i] = ""
            break

    text = "\n".join(lines)

    match = _H1_RE.search(text)
    if match:
        text = text[match.start():]
        title = match.group(1).strip()
    else:
        title = ""

    text = _EXCESS_BLANKS_RE.sub("\n\n", text)
    return text.strip(), title
```

- [ ] **Step 4: Run cleaning tests — expect PASS**

```bash
.venv/bin/pytest tests/test_docs.py::TestCleanRtdMarkdown -v
```

- [ ] **Step 5: Write tests for `fetch_doc_content`**

Add to `tests/test_docs.py`:

```python
from ansible_know.docs import fetch_doc_content


class TestFetchDocContent:
    @pytest.mark.asyncio
    async def test_returns_cleaned_content(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "text/markdown; charset=utf-8",
            "x-markdown-tokens": "100",
        }
        mock_resp.text = "[Nav](/)\n\n# Test Page\n\nHello world."
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/guide.html",
            http_client=mock_client,
        )
        assert result["title"] == "Test Page"
        assert "Hello world." in result["content"]
        assert result["tokens"] == 100
        assert "Nav" not in result["content"]

    @pytest.mark.asyncio
    async def test_max_tokens_exceeded(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "text/markdown; charset=utf-8",
            "x-markdown-tokens": "5000",
        }
        mock_resp.text = "# Big Page\n\nLots of content."
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/big.html",
            max_tokens=1000,
            http_client=mock_client,
        )
        assert "error" in result
        assert "5000" in result["error"]

    @pytest.mark.asyncio
    async def test_non_markdown_content_type(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/page.html",
            http_client=mock_client,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_http_error_returns_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404),
        ))

        result = await fetch_doc_content(
            "https://docs.ansible.com/projects/ansible/latest/missing.html",
            http_client=mock_client,
        )
        assert "error" in result
```

- [ ] **Step 6: Run tests — expect FAIL**

```bash
.venv/bin/pytest tests/test_docs.py::TestFetchDocContent -v
```

- [ ] **Step 7: Implement `fetch_doc_content` in `docs.py`**

Add to `src/ansible_know/docs.py`:

```python
from ansible_know.types import ErrorResponse, FetchDocResult
from ansible_know.validation import truncate_response


async def fetch_doc_content(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult | ErrorResponse:
    """Fetch a docs.ansible.com page as clean markdown.

    Args:
        url: Full docs.ansible.com URL.
        max_tokens: If set, return error when page exceeds this token count.
        http_client: Optional shared httpx client.

    Returns:
        FetchDocResult on success, ErrorResponse on failure.
    """
    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        should_close = True

    try:
        resp = await client.get(
            url,
            headers={"Accept": "text/markdown"},
            follow_redirects=True,
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to fetch {url}: {exc}"}
    finally:
        if should_close:
            await client.aclose()

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" not in content_type:
        return {"error": f"Expected text/markdown but got {content_type!r} for {url}"}

    tokens_str = resp.headers.get("x-markdown-tokens", "0")
    try:
        tokens = int(tokens_str)
    except ValueError:
        tokens = 0

    if max_tokens is not None and tokens > max_tokens:
        return {
            "error": f"Page has {tokens} tokens (max_tokens={max_tokens}). "
            f"Fetch without max_tokens or increase the limit.",
        }

    content, title = _clean_rtd_markdown(resp.text)
    content = truncate_response(content)

    return {
        "content": content,
        "title": title,
        "tokens": tokens,
        "source_url": str(resp.url),
    }
```

Update the imports at the top of `docs.py` to include `re` and the new types.

- [ ] **Step 8: Run all docs tests — expect PASS**

```bash
.venv/bin/pytest tests/test_docs.py -v
```

- [ ] **Step 9: Commit**

```bash
git add src/ansible_know/docs.py tests/test_docs.py
git commit -m "feat: add _clean_rtd_markdown and fetch_doc_content

Content cleaning strips breadcrumbs, DOCTYPE artifacts, and excess
blank lines. fetch_doc_content handles HTTP fetch with Accept:
text/markdown, token checking, and content cleaning.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: `fetch_doc` MCP tool in `server.py`

Wire the thin tool that validates input and delegates to `docs.fetch_doc_content`.

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `tests/test_server.py` (if tool-level tests exist)

**Interfaces:**
- Consumes: `validate_doc_url`, `_get_http_client`, `docs.fetch_doc_content`
- Produces: `fetch_doc` MCP tool

- [ ] **Step 1: Add `fetch_doc` tool to `server.py`**

Add after the `search_docs` tool function (around line 427). Import `FetchDocResult` and `validate_doc_url` at the top of the file:

```python
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
    except Exception as exc:
        logger.warning("fetch_doc failed: %s", exc)
        return {"error": sanitize_error(str(exc))}
```

Add `FetchDocResult` to the imports from `ansible_know.types` and `validate_doc_url` to the imports from `ansible_know.validation`.

- [ ] **Step 2: Update module docstring tool count**

Change the module docstring from `"13 tools"` to `"14 tools"`.

- [ ] **Step 3: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --tb=short
```

- [ ] **Step 4: Lint**

```bash
.venv/bin/ruff check src/ansible_know/server.py
```

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py
git commit -m "feat: add fetch_doc MCP tool

Thin tool delegates to docs.fetch_doc_content. Validates URL domain,
passes lifespan http_client. Returns cleaned markdown with token count.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: RTD Search API fallback in `search_docs`

Add `_search_rtd_api` with parallel project search and integrate as fallback.

**Files:**
- Modify: `src/ansible_know/docs.py`
- Modify: `tests/test_docs.py`

**Interfaces:**
- Consumes: `RTD_PROJECT_SLUGS` from `config.py`, `httpx.AsyncClient`
- Produces: `_search_rtd_api(query, source, limit, http_client) -> list[dict]` (internal), `search_docs` now falls back to RTD search

- [ ] **Step 1: Write tests for `_search_rtd_api`**

Add to `tests/test_docs.py`:

```python
from ansible_know.docs import _search_rtd_api


class TestSearchRtdApi:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        rtd_response = {
            "count": 1,
            "results": [
                {
                    "title": "Using Variables",
                    "path": "/projects/ansible/latest/playbook_guide/variables.html",
                    "domain": "https://docs.ansible.com",
                    "blocks": [
                        {"type": "section", "content": "Variables let you manage differences. More text here."}
                    ],
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = rtd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        results = await _search_rtd_api("variables", http_client=mock_client)
        assert len(results) == 1
        assert results[0]["title"] == "Using Variables"
        assert results[0]["source"].startswith("rtd-search:")
        assert "docs.ansible.com" in results[0]["url"]

    @pytest.mark.asyncio
    async def test_scoped_to_source(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"count": 0, "results": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await _search_rtd_api("rules", source="ansible-lint", http_client=mock_client)
        call_args = mock_client.get.call_args
        query_param = call_args.kwargs.get("params", {}).get("q", "")
        assert "ansible-lint" in query_param

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        results = await _search_rtd_api("test", http_client=mock_client)
        assert results == []

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        hits = [
            {
                "title": f"Result {i}",
                "path": f"/page{i}.html",
                "domain": "https://docs.ansible.com",
                "blocks": [],
            }
            for i in range(20)
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"count": 20, "results": hits}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        results = await _search_rtd_api("test", limit=5, http_client=mock_client)
        assert len(results) <= 5
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.venv/bin/pytest tests/test_docs.py::TestSearchRtdApi -v
```

- [ ] **Step 3: Implement `_search_rtd_api` in `docs.py`**

Add to `src/ansible_know/docs.py`:

```python
import asyncio

from ansible_know.config import RTD_PROJECT_SLUGS

RTD_SEARCH_URL = "https://app.readthedocs.org/api/v3/search/"
RTD_DOCS_DOMAIN = "https://docs.ansible.com"


async def _search_rtd_api(
    query: str,
    source: str | None = None,
    limit: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search RTD API as fallback when manifest search returns empty."""
    slugs_to_search: list[tuple[str, str]] = []
    if source and source in RTD_PROJECT_SLUGS:
        slugs_to_search = [(source, RTD_PROJECT_SLUGS[source])]
    else:
        slugs_to_search = list(RTD_PROJECT_SLUGS.items())

    client = http_client
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        should_close = True

    async def _search_one(source_name: str, slug: str) -> list[dict[str, Any]]:
        params = {
            "q": f"project:{slug}/latest {query}",
            "page_size": min(limit, 20),
        }
        try:
            resp = await client.get(RTD_SEARCH_URL, params=params, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        hits: list[dict[str, Any]] = []
        for hit in data.get("results", []):
            blocks = hit.get("blocks", [])
            summary = ""
            if blocks:
                raw = blocks[0].get("content", "")
                dot = raw.find(". ")
                summary = (raw[: dot + 1] if dot > 0 else raw[:120]).strip()

            path = hit.get("path", "")
            hits.append({
                "title": hit.get("title", ""),
                "summary": summary,
                "topic": [],
                "audience": [],
                "lines": 0,
                "source": f"rtd-search:{source_name}",
                "url": f"{hit.get('domain', RTD_DOCS_DOMAIN)}{path}",
            })
        return hits

    try:
        all_hits = await asyncio.gather(
            *[_search_one(name, slug) for name, slug in slugs_to_search],
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        for hits in all_hits:
            if isinstance(hits, list):
                results.extend(hits)
    finally:
        if should_close:
            await client.aclose()

    return results[:limit]
```

- [ ] **Step 4: Integrate fallback in `search_docs`**

At the end of `search_docs()`, before the final `return`, add the RTD fallback:

```python
    # RTD Search API fallback when manifest search returns empty
    if not results:
        try:
            rtd_results = await _search_rtd_api(
                query, source=source, http_client=http_client,
            )
            results.extend(rtd_results)
        except Exception:
            pass
    else:
        # Deduplicate: if both manifest and RTD would return same URLs
        pass

    return results[:SEARCH_DOCS_LIMIT]
```

Note: deduplication is only needed when BOTH manifest and RTD return results. Since RTD is only called when manifest is empty, the `else` branch is a no-op for now. It's structured for future use when RTD might supplement (not just fall back).

- [ ] **Step 5: Run RTD tests — expect PASS**

```bash
.venv/bin/pytest tests/test_docs.py::TestSearchRtdApi -v
```

- [ ] **Step 6: Write test for fallback integration**

Add to `tests/test_docs.py`:

```python
class TestSearchDocsFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_rtd_when_manifest_empty(self):
        sources = {
            "test": {"file": "/nonexistent/manifest.json", "description": "Test"},
        }
        rtd_response = {
            "count": 1,
            "results": [
                {
                    "title": "RTD Result",
                    "path": "/projects/ansible/latest/guide.html",
                    "domain": "https://docs.ansible.com",
                    "blocks": [{"content": "Found via RTD search."}],
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = rtd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("ansible_know.docs.get_doc_sources", return_value=sources):
            results = await search_docs("guide", http_client=mock_client)

        assert len(results) >= 1
        assert results[0]["source"].startswith("rtd-search:")
```

- [ ] **Step 7: Run fallback test — expect PASS**

```bash
.venv/bin/pytest tests/test_docs.py::TestSearchDocsFallback -v
```

- [ ] **Step 8: Run full suite + lint**

```bash
.venv/bin/pytest tests/ -v --tb=short
.venv/bin/ruff check src/ansible_know/docs.py tests/test_docs.py
```

- [ ] **Step 9: Commit**

```bash
git add src/ansible_know/docs.py tests/test_docs.py
git commit -m "feat: add RTD Search API fallback to search_docs

Parallel project search via asyncio.gather with 5s per-project timeout.
Falls back when manifest search returns empty. Dedup by URL ready.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Server instructions, `docs://sources` resource update, and CLAUDE.md

Update the MCP server instructions to mention `fetch_doc` and update the `docs://sources` resource to show source type.

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `get_doc_sources()`
- Produces: Updated `instructions` string, updated `docs://sources` resource

- [ ] **Step 1: Update `instructions` string in `server.py`**

Find the `mcp = FastMCP(...)` call (line 142) and update the `instructions` parameter:

```python
    instructions=(
        "Ansible module and role discovery, documentation, and skill generation. "
        "Workflow: (1) search_collections to discover collections on Galaxy, "
        "(2) ensure_collection to install one for this session, "
        "(3) search_modules/get_collection_manifest to find modules and roles, "
        "(4) get_module_doc or get_role_doc for structured docs, "
        "(5) search_docs for conceptual guides, then fetch_doc to retrieve full content, "
        "(6) generate_skill or generate_role_skill to create skill packages. "
        "Resources: server://version for version and upgrade status, "
        "galaxy://installed for session collections, "
        "docs://sources for configured doc sources, "
        "skills://list for generated skills."
    ),
```

- [ ] **Step 2: Update `docs://sources` resource**

Replace the `resource_doc_sources` function:

```python
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
```

- [ ] **Step 3: Update CLAUDE.md**

Update the MCP Tools table to add `fetch_doc`:

```markdown
| `fetch_doc` | read-only | Fetch a docs.ansible.com page as clean Markdown |
```

Update the tool count in the Architecture section if it says "13 tools".

Update the Key Patterns section to add:
```markdown
- `_clean_rtd_markdown` in `docs.py` strips breadcrumbs/artifacts from RTD markdown responses.
- `fetch_doc` uses Cloudflare's `Accept: text/markdown` content negotiation (per-request `follow_redirects=True`).
- RTD Search API (`_search_rtd_api`) serves as fallback when manifest search returns empty.
- Doc manifests shipped as JSON in `src/ansible_know/data/`, loaded from disk (no HTTP at startup).
```

- [ ] **Step 4: Run full suite**

```bash
.venv/bin/pytest tests/ -v --tb=short
.venv/bin/ruff check src/ tests/
```

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/server.py CLAUDE.md
git commit -m "docs: update server instructions and docs://sources for fetch_doc

Add fetch_doc to workflow instructions. Show source type (file/url)
in docs://sources resource. Update CLAUDE.md with new tool and patterns.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Manifest builder and CI script

Build-time module for generating manifests from objects.inv and sitemap. Add `sphobjinv` dependency.

**Files:**
- Modify: `pyproject.toml`
- Create: `src/ansible_know/manifest_builder.py`
- Create: `scripts/build_docs_manifests.py`
- Create: `tests/test_manifest_builder.py`

**Interfaces:**
- Consumes: `AUDIENCE_MAP`, `CORE_PAGES`, `GUIDE_TOPIC_PREFIXES`, `PROJECT_BASE_URLS` from `config.py`, `_clean_rtd_markdown` from `docs.py`
- Produces: `fetch_objects_inv(url) -> list[dict]`, `filter_guide_pages(entries, prefixes) -> list[dict]`, `fetch_sitemap_urls(url, prefix) -> list[str]`, `build_ansible_core_manifest(...) -> dict`, `build_ecosystem_manifest(...) -> dict`

- [ ] **Step 1: Add `sphobjinv` and `defusedxml` to `pyproject.toml`**

Add `"sphobjinv>=2.3"` and `"defusedxml>=0.7"` to the `dependencies` list. `defusedxml` is needed because `manifest_builder.py` parses sitemap XML from an external URL — stdlib `xml.etree.ElementTree` is vulnerable to XXE and billion-laughs attacks.

- [ ] **Step 2: Write unit tests for `filter_guide_pages`**

Create `tests/test_manifest_builder.py`:

```python
"""Tests for ansible_know.manifest_builder."""

from __future__ import annotations

import pytest

from ansible_know.manifest_builder import filter_guide_pages


class TestFilterGuidePages:
    def test_keeps_guide_pages(self):
        entries = [
            {"name": "playbook_guide/playbooks_intro", "display_name": "Intro"},
            {"name": "inventory_guide/intro_inventory", "display_name": "Inventory"},
        ]
        prefixes = {"playbook_guide", "inventory_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 2

    def test_excludes_collections(self):
        entries = [
            {"name": "playbook_guide/intro", "display_name": "Intro"},
            {"name": "collections/ansible/builtin/copy_module", "display_name": "copy"},
        ]
        prefixes = {"playbook_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 1
        assert result[0]["name"] == "playbook_guide/intro"

    def test_excludes_top_level(self):
        entries = [
            {"name": "index", "display_name": "Home"},
            {"name": "playbook_guide/intro", "display_name": "Intro"},
        ]
        prefixes = {"playbook_guide"}
        result = filter_guide_pages(entries, prefixes)
        assert len(result) == 1

    def test_empty_entries(self):
        assert filter_guide_pages([], {"playbook_guide"}) == []
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
.venv/bin/pytest tests/test_manifest_builder.py::TestFilterGuidePages -v
```

- [ ] **Step 4: Create `manifest_builder.py`**

Create `src/ansible_know/manifest_builder.py`:

```python
"""Build documentation manifests from objects.inv and sitemap sources.

This module is used at build time (CI) to generate the JSON manifest
files shipped in src/ansible_know/data/. It is not used at runtime.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import sphobjinv as soi

from ansible_know.config import (
    AUDIENCE_MAP,
    CORE_PAGES,
    GUIDE_TOPIC_PREFIXES,
    PROJECT_BASE_URLS,
)
from ansible_know.docs import _clean_rtd_markdown

logger = logging.getLogger("ansible_know.builder")

MANIFEST_VERSION = "2.0"


def filter_guide_pages(
    entries: list[dict[str, str]],
    topic_prefixes: set[str],
) -> list[dict[str, str]]:
    """Keep only entries whose first path segment matches a guide topic prefix."""
    result = []
    for entry in entries:
        name = entry.get("name", "")
        if "/" not in name:
            continue
        first_segment = name.split("/")[0]
        if first_segment in topic_prefixes:
            result.append(entry)
    return result


async def fetch_objects_inv(url: str) -> list[dict[str, str]]:
    """Download and parse objects.inv, returning std:doc entries."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    inv = soi.Inventory(plaintext=soi.readbytes(url))
    entries = []
    for obj in inv.objects:
        if obj.domain == "std" and obj.role == "doc":
            entries.append({
                "name": obj.name,
                "display_name": obj.dispname if obj.dispname != "-" else obj.name,
                "uri": obj.uri,
            })
    return entries


async def fetch_sitemap_urls(
    sitemap_url: str,
    project_prefix: str,
) -> list[str]:
    """Extract URLs from sitemap XML matching a project prefix."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

    import defusedxml.ElementTree as ET

    root = ET.fromstring(resp.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        if loc.text and project_prefix in loc.text:
            urls.append(loc.text)
    return urls


async def _fetch_page_metadata(
    url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Fetch a page via markdown endpoint and extract metadata."""
    try:
        resp = await client.get(
            url,
            headers={"Accept": "text/markdown"},
            follow_redirects=True,
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return {}

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" not in content_type:
        logger.warning("Non-markdown response for %s: %s", url, content_type)
        return {}

    tokens_str = resp.headers.get("x-markdown-tokens", "0")
    try:
        tokens = int(tokens_str)
    except ValueError:
        tokens = 0

    content, title = _clean_rtd_markdown(resp.text)
    lines = content.count("\n") + 1 if content else 0

    dot = content.find(". ", content.find("\n"))
    summary = ""
    if dot > 0:
        first_para_start = content.find("\n\n")
        if first_para_start > 0:
            first_para = content[first_para_start:].strip()
            dot2 = first_para.find(". ")
            summary = (first_para[: dot2 + 1] if dot2 > 0 else first_para[:200]).strip()

    return {"title": title, "summary": summary, "lines": lines, "tokens": tokens}


async def build_ansible_core_manifest() -> dict[str, Any]:
    """Build the ansible-core manifest from objects.inv."""
    base_url = PROJECT_BASE_URLS["ansible"]
    inv_url = f"{base_url}/objects.inv"

    raw_entries = await fetch_objects_inv(inv_url)
    guide_entries = filter_guide_pages(raw_entries, GUIDE_TOPIC_PREFIXES)

    core_set = set(CORE_PAGES.get("ansible", []))
    files: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for entry in guide_entries:
            name = entry["name"]
            path = f"{name}.html"
            topic = name.split("/")[0]

            file_entry: dict[str, Any] = {
                "path": path,
                "topic": topic,
                "title": entry["display_name"],
                "audience": AUDIENCE_MAP.get(topic, "both"),
                "core": path in core_set,
                "summary": "",
                "lines": 0,
                "tokens": 0,
            }

            if path in core_set:
                url = f"{base_url}/{path}"
                meta = await _fetch_page_metadata(url, client)
                if meta:
                    file_entry.update(meta)

            files.append(file_entry)

    return {
        "version": MANIFEST_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "files": files,
    }


async def build_ecosystem_manifest(
    project_key: str,
    sitemap_url: str = "https://docs.ansible.com/ansible-sitemap.xml",
) -> dict[str, Any]:
    """Build a manifest for an ecosystem project from sitemap + markdown fetch."""
    base_url = PROJECT_BASE_URLS[project_key]
    parsed = urlparse(base_url)
    prefix = parsed.path

    all_urls = await fetch_sitemap_urls(sitemap_url, prefix)
    core_set = set(CORE_PAGES.get(project_key, []))
    files: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for url in all_urls:
            parsed_url = urlparse(url)
            path = parsed_url.path
            if prefix:
                path = path[len(prefix):]
            path = path.lstrip("/")

            meta = await _fetch_page_metadata(url, client)
            title = meta.get("title", "")
            if not title:
                title = path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()

            topic = path.split("/")[0] if "/" in path else "overview"

            files.append({
                "path": path,
                "topic": topic,
                "title": title,
                "audience": "author",
                "core": path in core_set or (path == "" and "" in core_set),
                "summary": meta.get("summary", ""),
                "lines": meta.get("lines", 0),
                "tokens": meta.get("tokens", 0),
            })

    return {
        "version": MANIFEST_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "files": files,
    }


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write a manifest dict to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    logger.info("Wrote manifest: %s (%d entries)", output_path, len(manifest.get("files", [])))
```

- [ ] **Step 5: Run filter tests — expect PASS**

```bash
.venv/bin/pytest tests/test_manifest_builder.py -v
```

- [ ] **Step 6: Create `scripts/build_docs_manifests.py`**

```python
#!/usr/bin/env python3
"""Build all documentation manifests and write to src/ansible_know/data/."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ansible_know.manifest_builder import (
    build_ansible_core_manifest,
    build_ecosystem_manifest,
    write_manifest,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "ansible_know" / "data"

ECOSYSTEM_PROJECTS = ["lint", "navigator", "builder", "creator", "molecule"]

PROJECT_MANIFEST_NAMES = {
    "ansible": "ansible_core_manifest.json",
    "lint": "ansible_lint_manifest.json",
    "navigator": "ansible_navigator_manifest.json",
    "builder": "ansible_builder_manifest.json",
    "creator": "ansible_creator_manifest.json",
    "molecule": "molecule_manifest.json",
}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger("build_manifests")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Building ansible-core manifest from objects.inv...")
    core_manifest = await build_ansible_core_manifest()
    write_manifest(core_manifest, DATA_DIR / PROJECT_MANIFEST_NAMES["ansible"])
    logger.info("ansible-core: %d entries", len(core_manifest["files"]))

    for project in ECOSYSTEM_PROJECTS:
        logger.info("Building %s manifest from sitemap...", project)
        manifest = await build_ecosystem_manifest(project)
        write_manifest(manifest, DATA_DIR / PROJECT_MANIFEST_NAMES[project])
        logger.info("%s: %d entries", project, len(manifest["files"]))

    logger.info("Done. All manifests written to %s", DATA_DIR)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Lint**

```bash
.venv/bin/ruff check src/ansible_know/manifest_builder.py scripts/build_docs_manifests.py tests/test_manifest_builder.py
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/ansible_know/manifest_builder.py scripts/build_docs_manifests.py tests/test_manifest_builder.py
git commit -m "feat: add manifest builder and CI build script

Parse objects.inv (via sphobjinv) for ansible-core, sitemap for
ecosystem projects. Enrich core pages via RTD markdown endpoint.
CLI at scripts/build_docs_manifests.py for CI use.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Generate real manifests, verify, and clean up

Run the builder, verify the output, replace the seed manifest.

**Files:**
- Overwrite: `src/ansible_know/data/ansible_core_manifest.json`
- Create: `src/ansible_know/data/ansible_lint_manifest.json`
- Create: `src/ansible_know/data/ansible_navigator_manifest.json`
- Create: `src/ansible_know/data/ansible_builder_manifest.json`
- Create: `src/ansible_know/data/ansible_creator_manifest.json`
- Create: `src/ansible_know/data/molecule_manifest.json`

**Interfaces:**
- Consumes: `scripts/build_docs_manifests.py`
- Produces: All 6 shipped manifests

- [ ] **Step 1: Run the manifest builder**

```bash
.venv/bin/python scripts/build_docs_manifests.py
```

This hits real endpoints (objects.inv, sitemap, markdown). Expected: ~1-2 minutes.

- [ ] **Step 2: Verify ansible-core manifest entry count**

```bash
.venv/bin/python -c "
import json
m = json.load(open('src/ansible_know/data/ansible_core_manifest.json'))
print(f'Version: {m[\"version\"]}')
print(f'Entries: {len(m[\"files\"])}')
core = [f for f in m['files'] if f.get('core')]
print(f'Core entries: {len(core)}')
print(f'Core with summary: {len([f for f in core if f.get(\"summary\")])}')
"
```

Expected: ~400-500 total entries, ~34 core entries, most core entries with summaries.

- [ ] **Step 3: Verify ecosystem manifests**

```bash
for f in src/ansible_know/data/*.json; do
  echo "$(basename $f): $(python3 -c "import json; print(len(json.load(open('$f'))['files']))")"
done
```

Expected counts roughly: lint ~60, navigator ~8, builder ~16, creator ~5, molecule ~27.

- [ ] **Step 4: Verify search_docs works with real manifests**

```bash
.venv/bin/python -c "
import asyncio
from ansible_know.docs import search_docs, clear_cache
clear_cache()
async def test():
    r = await search_docs('loops')
    print(f'loops: {len(r)} results, first: {r[0][\"title\"] if r else \"none\"}')
    r = await search_docs('rules', source='ansible-lint')
    print(f'lint rules: {len(r)} results')
    r = await search_docs('settings', source='ansible-navigator')
    print(f'nav settings: {len(r)} results')
asyncio.run(test())
"
```

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --tb=short
```

- [ ] **Step 6: Commit all manifests**

```bash
git add src/ansible_know/data/
git commit -m "data: generate all doc manifests from RTD sources

ansible-core from objects.inv, ecosystem projects from sitemap.
Core pages enriched with summaries and token counts.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 10: GitHub Action for weekly manifest refresh

Add a workflow that rebuilds manifests and opens a PR.

**Files:**
- Create: `.github/workflows/update-docs-manifests.yml`

**Interfaces:**
- Consumes: `scripts/build_docs_manifests.py`
- Produces: Weekly PR with updated manifests

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/update-docs-manifests.yml`:

```yaml
name: Update docs manifests

on:
  schedule:
    - cron: '23 4 * * 1'  # Weekly on Monday at 04:23 UTC
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  update-manifests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Build manifests
        run: python scripts/build_docs_manifests.py

      - name: Check for changes
        id: changes
        run: |
          if git diff --quiet src/ansible_know/data/; then
            echo "changed=false" >> "$GITHUB_OUTPUT"
          else
            echo "changed=true" >> "$GITHUB_OUTPUT"
          fi

      - name: Create PR
        if: steps.changes.outputs.changed == 'true'
        uses: peter-evans/create-pull-request@v7
        with:
          branch: auto/update-docs-manifests
          title: 'chore: update docs manifests'
          body: |
            Automated weekly rebuild of documentation manifests from
            docs.ansible.com (objects.inv + sitemap + markdown endpoint).

            Review the diff to verify no unexpected changes from upstream.
          commit-message: 'chore: update docs manifests'
          delete-branch: true
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/update-docs-manifests.yml
git commit -m "ci: add weekly docs manifest refresh workflow

Rebuilds all manifests from RTD sources and opens a PR for review.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```
