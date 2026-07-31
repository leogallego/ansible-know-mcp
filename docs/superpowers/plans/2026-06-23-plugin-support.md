# Plugin Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add discovery, documentation, and skill generation for all Ansible plugin types (lookup, filter, inventory, connection, callback, become, cache, cliconf, httpapi, netconf, shell, vars, strategy, test) so AI sessions use idiomatic plugins instead of falling back to raw modules.

**Architecture:** Plugins share the same `ansible-doc -t <type>` interface as modules and roles. The Galaxy docs-blob already contains plugin entries with `content_type` values matching these types — the current code just filters them out. We add a unified plugin abstraction across all six layers (parser, galaxy, resolution, manifest, skills/templates, server) following the same patterns used for roles. Plugin skills have different templates than module skills: lookup plugins show `{{ query(...) }}` usage, filter plugins show `{{ value | filter_name() }}`, etc.

**Tech Stack:** Python 3.10+, FastMCP, Jinja2, httpx, pytest

**Tracking issue:** #121
**Related issues:** #119 (resolve_module_doc return type alignment — out of scope, tracked as follow-up)

## Setup

Use a git worktree for isolation. Before starting any task:

```bash
# Create worktree (or use /using-git-worktrees skill)
git worktree add .claude/worktrees/feature-plugin-support -b feature/plugin-support main
cd .claude/worktrees/feature-plugin-support

# Set up venv in the worktree
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
.venv/bin/pytest tests/ -v --tb=short  # baseline — must pass before any changes
```

## Global Constraints

- All plugin types supported by `ansible-doc -t`: `become`, `cache`, `callback`, `cliconf`, `connection`, `httpapi`, `inventory`, `lookup`, `netconf`, `shell`, `vars`, `strategy`, `test`, `filter`
- Module and role are NOT plugins in this context — they already have their own paths
- FQCN validation regex already permits plugin names (same `namespace.collection.name` format)
- Galaxy docs-blob `content_type` values for plugins match the `ansible-doc -t` type names (verified against Galaxy API: `ansible.netcommon` returns `become`, `cache`, `cliconf`, `connection`, `filter`, `httpapi`, `netconf`; `community.general` returns `become`, `cache`, `callback`, `connection`, `filter`, `inventory`, `lookup`, `test`). Galaxy also returns `action`, `doc_fragments`, `module_utils` — these are NOT `ansible-doc -t` types and are correctly skipped by the `if ct not in PLUGIN_TYPES` check.
- Tests mock `_run_ansible_doc` — no real ansible-core needed for unit tests
- All new tools follow existing naming and annotation patterns (read-only hints, idempotent hints)
- Plugin skill templates produce usage examples appropriate to the plugin type, not playbook tasks
- Plugin skills use `{plugin_type}__{short_name}` directory names (e.g., `lookup__nb_lookup/SKILL.md`) to avoid collisions with module skills that share the same short name
- Use `ValidationError` (from `errors.py`) for input validation, never bare `ValueError` — server tool handlers only catch `ValidationError`
- Discovery loops across 14 plugin types MUST use `asyncio.gather()` for parallelism, not sequential `await` in a for-loop

## Plugin Type Categories

For template purposes, plugins fall into three usage pattern groups:

1. **Jinja2 plugins** — used inline in templates/playbooks:
   - `lookup` → `{{ query('ns.col.name', ...) }}` or `{{ lookup('ns.col.name', ...) }}`
   - `filter` → `{{ value | ns.col.name(...) }}`
   - `test` → `{{ value is ns.col.name(...) }}`

2. **Playbook-level plugins** — declared in play/task directives:
   - `connection` → `connection: ns.col.name`
   - `become` → `become_method: ns.col.name`
   - `strategy` → `strategy: ns.col.name`
   - `callback` → configured in `ansible.cfg` or env var
   - `inventory` → `-i` flag or `ansible.cfg` plugin path

3. **Infrastructure plugins** — rarely user-facing, configured in ansible.cfg:
   - `cache`, `cliconf`, `httpapi`, `netconf`, `shell`, `vars`

---

### Task 1: Plugin Type Constants and TypedDict Definitions

**Files:**
- Modify: `src/ansible_know/config.py` (add plugin type constant)
- Modify: `src/ansible_know/types.py` (add PluginMetadata, ManifestPluginEntry, etc.)
- Test: `tests/test_config.py` (verify constant), `tests/test_types.py` (new — verify TypedDict instantiation)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `PLUGIN_TYPES: tuple[str, ...]` — the 14 ansible-doc plugin type strings
  - `JINJA2_PLUGIN_TYPES: tuple[str, ...]` — `("lookup", "filter", "test")`
  - `PLAYBOOK_PLUGIN_TYPES: tuple[str, ...]` — `("connection", "become", "strategy", "callback", "inventory")`
  - `INFRA_PLUGIN_TYPES: tuple[str, ...]` — `("cache", "cliconf", "httpapi", "netconf", "shell", "vars")`
  - `PluginMetadata(TypedDict)` with keys: `plugin_name: str`, `plugin_type: str`, `short_description: str`, `params: list[ParamDict]`, `examples: str`
  - `ManifestPluginEntry(TypedDict)` with keys: `fqcn: str`, `plugin_type: str`, `description: str`, `param_count: int`, `has_skill: bool`
  - `GetPluginDocResult(TypedDict)` extending `PluginMetadata` with `doc_source: str` and optional `doc_version`, `doc_warning`, `doc_source_server`

**Skills:** Load before starting this task:
- `python-tighten-types` — review TypedDict definitions for type tightness
- `pep8-type-annotations` — correct annotation spacing and style
- `pep8-naming` — UPPER_CASE for constants, CapWords for TypedDicts

- [ ] **Step 1: Write the failing test for plugin type constants**

```python
# tests/test_config.py — add at end of file

from ansible_know.config import PLUGIN_TYPES, JINJA2_PLUGIN_TYPES, PLAYBOOK_PLUGIN_TYPES, INFRA_PLUGIN_TYPES


class TestPluginTypeConstants:
    def test_plugin_types_contains_lookup(self):
        assert "lookup" in PLUGIN_TYPES

    def test_plugin_types_contains_filter(self):
        assert "filter" in PLUGIN_TYPES

    def test_plugin_types_excludes_module_and_role(self):
        assert "module" not in PLUGIN_TYPES
        assert "role" not in PLUGIN_TYPES

    def test_plugin_types_has_14_entries(self):
        assert len(PLUGIN_TYPES) == 14

    def test_jinja2_types(self):
        assert set(JINJA2_PLUGIN_TYPES) == {"lookup", "filter", "test"}

    def test_playbook_types(self):
        assert set(PLAYBOOK_PLUGIN_TYPES) == {"connection", "become", "strategy", "callback", "inventory"}

    def test_infra_types(self):
        assert set(INFRA_PLUGIN_TYPES) == {"cache", "cliconf", "httpapi", "netconf", "shell", "vars"}

    def test_categories_cover_all_types(self):
        combined = set(JINJA2_PLUGIN_TYPES) | set(PLAYBOOK_PLUGIN_TYPES) | set(INFRA_PLUGIN_TYPES)
        assert combined == set(PLUGIN_TYPES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py::TestPluginTypeConstants -v`
Expected: FAIL with `ImportError: cannot import name 'PLUGIN_TYPES'`

- [ ] **Step 3: Add constants to config.py**

```python
# src/ansible_know/config.py — add after existing constants

PLUGIN_TYPES: tuple[str, ...] = (
    "become", "cache", "callback", "cliconf", "connection",
    "filter", "httpapi", "inventory", "lookup", "netconf",
    "shell", "strategy", "test", "vars",
)

JINJA2_PLUGIN_TYPES: tuple[str, ...] = ("filter", "lookup", "test")

PLAYBOOK_PLUGIN_TYPES: tuple[str, ...] = (
    "become", "callback", "connection", "inventory", "strategy",
)

INFRA_PLUGIN_TYPES: tuple[str, ...] = (
    "cache", "cliconf", "httpapi", "netconf", "shell", "vars",
)
```

- [ ] **Step 4: Run config tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py::TestPluginTypeConstants -v`
Expected: PASS

- [ ] **Step 5: Add TypedDict definitions to types.py**

Add after `RoleMetadata`:

```python
class PluginMetadata(TypedDict):
    """Plugin metadata extracted by parser.extract_plugin_metadata()."""

    plugin_name: str
    plugin_type: str
    short_description: str
    params: list[ParamDict]
    examples: str


class ManifestPluginEntry(TypedDict):
    """Single plugin entry in a collection manifest."""

    fqcn: str
    plugin_type: str
    description: str
    param_count: int
    has_skill: bool
```

Add after `GetRoleDocResult`:

```python
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
```

Update `ManifestResult` to include plugins:

```python
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
```

> **Existing test impact:** Adding `plugin_count` and `plugins` to `ManifestResult`
> means any existing test that constructs or asserts on a manifest dict without these
> fields will fail. Grep for `ManifestResult`, `generate_manifest`, and `"module_count"`
> across `tests/test_collection_manifest.py`, `tests/test_server.py`, and
> `tests/test_skills.py`. Add `"plugin_count": 0, "plugins": []` to each fixture.
> The `load_cached_manifest` backfill (Task 5 Step 3b) handles cached JSON on disk,
> but test dicts constructed in-memory need manual updates. Same applies to
> `_CollectionInfoBase` below — existing `search_collections` test fixtures need
> `"plugin_count": 0`.

Update `CollectionInfo` (`_CollectionInfoBase`) to include `plugin_count`:

```python
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
```

Add `fetch_plugin_doc` to the `GalaxyDocClient` protocol:

```python
class GalaxyDocClient(Protocol):
    async def fetch_module_doc(
        self, module_name: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def fetch_role_doc(
        self, role_name: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def fetch_plugin_doc(
        self, plugin_name: str, plugin_type: str,
    ) -> tuple[dict[str, Any], DocProvenance]: ...

    async def search_collections(
        self, query: str, tags: str | None = None,
    ) -> dict[str, Any]: ...

    async def __aenter__(self) -> GalaxyDocClient: ...

    async def __aexit__(self, *exc: object) -> None: ...
```

- [ ] **Step 6: Write type instantiation smoke test**

```python
# tests/test_types.py (new file)

from ansible_know.types import ManifestPluginEntry, PluginMetadata


class TestPluginTypes:
    def test_plugin_metadata_instantiation(self):
        meta: PluginMetadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox via its API",
            "params": [],
            "examples": "",
        }
        assert meta["plugin_type"] == "lookup"

    def test_manifest_plugin_entry(self):
        entry: ManifestPluginEntry = {
            "fqcn": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "description": "Queries NetBox via its API",
            "param_count": 3,
            "has_skill": False,
        }
        assert entry["plugin_type"] == "lookup"
```

- [ ] **Step 7: Run all tests and verify no regressions**

Run: `.venv/bin/pytest tests/test_config.py tests/test_types.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/ansible_know/config.py src/ansible_know/types.py tests/test_config.py tests/test_types.py
git commit -m "feat: add plugin type constants and TypedDict definitions

Add PLUGIN_TYPES, JINJA2_PLUGIN_TYPES, PLAYBOOK_PLUGIN_TYPES, and
INFRA_PLUGIN_TYPES constants. Add PluginMetadata, ManifestPluginEntry,
and GetPluginDocResult TypedDicts. Extend ManifestResult and
CollectionInfo with plugin_count. Add fetch_plugin_doc to
GalaxyDocClient protocol.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Parser Plugin Functions

**Files:**
- Modify: `src/ansible_know/parser.py` (add `list_plugins`, `get_plugin_doc`, `search_plugins`, `extract_plugin_metadata`)
- Modify: `tests/conftest.py` (add `SAMPLE_PLUGIN_DOC`, `SAMPLE_PLUGIN_LIST` fixtures)
- Modify: `tests/test_parser.py` (add tests for new functions)

**Interfaces:**
- Consumes: `PLUGIN_TYPES` from `config.py`, `PluginMetadata` from `types.py`
- Produces:
  - `list_plugins(plugin_type: str, collection_filter: str | None = None, *, collections_path: str | None = None) -> dict[str, str]`
  - `get_plugin_doc(plugin_name: str, plugin_type: str, *, collections_path: str | None = None) -> dict[str, Any]`
  - `search_plugins(keyword: str, plugin_type: str | None = None, collection_filter: str | None = None, *, collections_path: str | None = None) -> dict[str, str]`
  - `extract_plugin_metadata(plugin_doc: dict[str, Any], plugin_type: str) -> PluginMetadata`

**Skills:** Load before starting this task:
- `python-contract-docstrings` — new public functions need contract docstrings
- `python-try-except` — audit try/except in search_plugins all-types fallback
- `pep8-naming` — snake_case for functions, verify `__all__` completeness

- [ ] **Step 1: Add test fixtures to conftest.py**

```python
# tests/conftest.py — add after SAMPLE_ROLE_README_HTML_CODEBLOCK_VARS

SAMPLE_PLUGIN_DOC = {
    "netbox.netbox.nb_lookup": {
        "doc": {
            "name": "nb_lookup",
            "short_description": "Queries and returns elements from NetBox",
            "description": [
                "Queries NetBox via its API to return virtually any information",
                "capable of being stored in NetBox.",
            ],
            "options": {
                "api_endpoint": {
                    "description": ["The URL to the NetBox instance"],
                    "type": "str",
                    "required": True,
                },
                "token": {
                    "description": ["The API token for NetBox"],
                    "type": "str",
                    "required": True,
                },
                "api_filter": {
                    "description": ["The api_filter to use"],
                    "type": "str",
                    "required": False,
                },
            },
        },
        "examples": (
            "- name: Obtain list of sites from NetBox\n"
            "  debug:\n"
            "    msg: \"{{ query('netbox.netbox.nb_lookup', 'sites', api_endpoint='http://localhost', token='mytoken') }}\"\n"
        ),
    },
}

SAMPLE_PLUGIN_LIST = {
    "netbox.netbox.nb_lookup": "Queries and returns elements from NetBox",
    "ansible.builtin.env": "Read the value of environment variables",
    "ansible.builtin.file": "Return file contents",
    "community.general.bitwarden": "Retrieve secrets from Bitwarden",
}
```

Add fixtures:

```python
@pytest.fixture
def sample_plugin_doc():
    return SAMPLE_PLUGIN_DOC

@pytest.fixture
def sample_plugin_doc_json():
    return json.dumps(SAMPLE_PLUGIN_DOC)

@pytest.fixture
def sample_plugin_list():
    return SAMPLE_PLUGIN_LIST

@pytest.fixture
def sample_plugin_list_json():
    return json.dumps(SAMPLE_PLUGIN_LIST)
```

- [ ] **Step 2: Write failing tests for parser plugin functions**

```python
# tests/test_parser.py — add at end

from ansible_know.errors import ValidationError
from ansible_know.parser import (
    extract_plugin_metadata,
    get_plugin_doc,
    list_plugins,
    search_plugins,
)


class TestListPlugins:
    def test_returns_plugin_dict(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json) as mock:
            result = list_plugins("lookup")
        assert "netbox.netbox.nb_lookup" in result
        mock.assert_called_once_with("--list", "-t", "lookup", "--json", collections_path=None)

    def test_passes_collection_filter(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json) as mock:
            list_plugins("lookup", collection_filter="netbox.netbox")
        mock.assert_called_once_with(
            "--list", "-t", "lookup", "--json", "netbox.netbox",
            collections_path=None,
        )

    def test_rejects_invalid_plugin_type(self):
        with pytest.raises(ValidationError, match="Invalid plugin type"):
            list_plugins("bogus")


class TestGetPluginDoc:
    def test_returns_parsed_json(self, sample_plugin_doc, sample_plugin_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_doc_json):
            result = get_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        assert result == sample_plugin_doc

    def test_passes_type_flag(self, sample_plugin_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_doc_json) as mock:
            get_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        mock.assert_called_once_with(
            "-t", "lookup", "netbox.netbox.nb_lookup", "--json",
            collections_path=None,
        )

    def test_rejects_invalid_plugin_type(self):
        with pytest.raises(ValidationError, match="Invalid plugin type"):
            get_plugin_doc("foo.bar.baz", "bogus")


class TestSearchPlugins:
    def test_filters_by_keyword(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json):
            result = search_plugins("netbox", plugin_type="lookup")
        assert "netbox.netbox.nb_lookup" in result
        assert len(result) == 1

    def test_search_all_types(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json):
            result = search_plugins("env")
        assert "ansible.builtin.env" in result

    def test_case_insensitive(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json):
            result = search_plugins("NETBOX", plugin_type="lookup")
        assert "netbox.netbox.nb_lookup" in result


class TestExtractPluginMetadata:
    def test_extracts_name_and_type(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        assert meta["plugin_name"] == "netbox.netbox.nb_lookup"
        assert meta["plugin_type"] == "lookup"

    def test_extracts_short_description(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        assert meta["short_description"] == "Queries and returns elements from NetBox"

    def test_extracts_params(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        names = [p["name"] for p in meta["params"]]
        assert "api_endpoint" in names
        assert "token" in names

    def test_extracts_examples(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        assert "nb_lookup" in meta["examples"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_parser.py::TestListPlugins tests/test_parser.py::TestGetPluginDoc tests/test_parser.py::TestSearchPlugins tests/test_parser.py::TestExtractPluginMetadata -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Implement parser plugin functions**

Add to `src/ansible_know/parser.py`:

```python
# At the top of the file, add to imports:
from ansible_know.config import PLUGIN_TYPES
from ansible_know.errors import AnsibleDocError, CollectionNotFoundError, ValidationError, is_missing_collection_error

# Replace the existing __all__ list with:
__all__ = [
    "extract_examples",
    "extract_module_metadata",
    "extract_params",
    "extract_plugin_metadata",
    "extract_role_metadata",
    "extract_short_description",
    "get_module_doc",
    "get_plugin_doc",
    "get_role_doc",
    "is_api_module",
    "list_modules",
    "list_plugins",
    "list_roles",
    "search_modules",
    "search_plugins",
    "transform_galaxy_to_ansible_doc_format",
]


def _validate_plugin_type(plugin_type: str) -> None:
    """Raise ValidationError if plugin_type is not a recognized ansible-doc type."""
    if plugin_type not in PLUGIN_TYPES:
        raise ValidationError(
            f"Invalid plugin type '{plugin_type}'. "
            f"Valid types: {', '.join(sorted(PLUGIN_TYPES))}"
        )


def list_plugins(
    plugin_type: str,
    collection_filter: str | None = None,
    *,
    collections_path: str | None = None,
) -> dict[str, str]:
    """List available plugins of a given type with short descriptions.

    Args:
        plugin_type: One of PLUGIN_TYPES (e.g., "lookup", "filter").
        collection_filter: Optional collection filter (e.g., "netbox.netbox").
        collections_path: Optional path to prepend to ANSIBLE_COLLECTIONS_PATH.

    Returns:
        Dict mapping fully-qualified plugin names to their short descriptions.
    """
    _validate_plugin_type(plugin_type)
    args = ["--list", "-t", plugin_type, "--json"]
    if collection_filter:
        args.append(collection_filter)
    raw = _run_ansible_doc(*args, collections_path=collections_path)
    try:
        plugins = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse plugin list JSON: {exc}") from exc
    return plugins


def get_plugin_doc(
    plugin_name: str,
    plugin_type: str,
    *,
    collections_path: str | None = None,
) -> dict[str, Any]:
    """Fetch full documentation for a single plugin.

    Returns the parsed JSON from `ansible-doc -t <type> <plugin> --json`.
    """
    _validate_plugin_type(plugin_type)
    raw = _run_ansible_doc(
        "-t", plugin_type, plugin_name, "--json",
        collections_path=collections_path,
    )
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnsibleDocError(f"Failed to parse plugin doc JSON: {exc}") from exc
    return doc


def search_plugins(
    keyword: str,
    plugin_type: str | None = None,
    collection_filter: str | None = None,
    *,
    collections_path: str | None = None,
) -> dict[str, str]:
    """Search plugins by keyword in name or description.

    When plugin_type is None, searches across all plugin types
    sequentially (14 ansible-doc calls). The MCP server tool bypasses
    this path and parallelizes across types via asyncio.gather — this
    all-type codepath exists for direct parser callers (scripts, tests,
    REPL exploration).
    """
    if plugin_type is not None:
        _validate_plugin_type(plugin_type)
        all_plugins = list_plugins(
            plugin_type, collection_filter, collections_path=collections_path,
        )
    else:
        all_plugins: dict[str, str] = {}
        errors: list[str] = []
        for ptype in PLUGIN_TYPES:
            try:
                found = list_plugins(
                    ptype, collection_filter, collections_path=collections_path,
                )
                all_plugins.update(found)
            except AnsibleDocError as exc:
                errors.append(str(exc))
                continue

        if not all_plugins and errors:
            raise AnsibleDocError(
                f"Plugin discovery failed for all {len(errors)} types. "
                f"Last error: {errors[-1]}"
            )

    keyword_lower = keyword.lower()
    return {
        name: desc
        for name, desc in all_plugins.items()
        if keyword_lower in name.lower() or keyword_lower in (desc or "").lower()
    }


def extract_plugin_metadata(
    plugin_doc: dict[str, Any], plugin_type: str,
) -> PluginMetadata:
    """Extract all metadata needed for plugin skill generation."""
    plugin_name = _get_module_name(plugin_doc)
    logger.debug("Extracting plugin metadata for %s (type=%s)", plugin_name, plugin_type)
    return {
        "plugin_name": plugin_name,
        "plugin_type": plugin_type,
        "short_description": extract_short_description(plugin_doc),
        "params": extract_params(plugin_doc),
        "examples": extract_examples(plugin_doc),
    }
```

Update the `TYPE_CHECKING` imports to add `PluginMetadata`:

```python
if TYPE_CHECKING:
    from ansible_know.types import EntryPointInfo, ModuleMetadata, ParamDict, PluginMetadata, RoleMetadata
```

Note: `ValidationError` is imported at runtime (not TYPE_CHECKING) since `_validate_plugin_type` raises it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_parser.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/parser.py tests/conftest.py tests/test_parser.py
git commit -m "feat: add parser functions for plugin discovery and documentation

Add list_plugins(), get_plugin_doc(), search_plugins(), and
extract_plugin_metadata() to parser.py. All pass -t <type> to
ansible-doc. search_plugins() can search one type or all 14 types.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Galaxy Client Plugin Support

**Files:**
- Modify: `src/ansible_know/galaxy.py` (add `_find_plugin`, `fetch_plugin_doc`, `list_collection_plugins`; update `search_collections` to count plugins)
- Modify: `tests/conftest.py` (add `SAMPLE_DOCS_BLOB_WITH_PLUGINS` fixture)
- Modify: `tests/test_galaxy.py` (add tests for plugin methods)

**Interfaces:**
- Consumes: `PLUGIN_TYPES` from `config.py`, `DocProvenance` from `types.py`
- Produces:
  - `GalaxyClient._find_plugin(blob, short_name, plugin_type) -> dict | None`
  - `GalaxyClient.fetch_plugin_doc(plugin_name, plugin_type, version=None) -> tuple[dict, DocProvenance]`
  - `GalaxyClient.list_collection_plugins(collection_fqcn, version=None) -> tuple[dict[str, dict[str, str]], dict]` — returns `{fqcn: {"description": str, "plugin_type": str}, ...}`

**Skills:** Load before starting this task:
- `python-contract-docstrings` — docstrings for new GalaxyClient methods
- `python-tighten-types` — return type annotations on new methods
- `pep8-type-annotations` — annotation style for async method signatures

- [ ] **Step 1: Add SAMPLE_DOCS_BLOB_WITH_PLUGINS fixture to conftest.py**

```python
# tests/conftest.py — add after SAMPLE_DOCS_BLOB_WITH_ROLES

SAMPLE_DOCS_BLOB_WITH_PLUGINS = {
    "docs_blob": {
        "contents": [
            {
                "content_type": "module",
                "content_name": "netbox_device",
                "doc_strings": {
                    "doc": {
                        "short_description": "Create, update or delete devices",
                        "options": [],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "lookup",
                "content_name": "nb_lookup",
                "doc_strings": {
                    "doc": {
                        "short_description": "Queries and returns elements from NetBox",
                        "description": ["Queries NetBox via its API."],
                        "options": [
                            {"name": "api_endpoint", "type": "str", "required": True,
                             "description": ["The URL to the NetBox instance"]},
                            {"name": "token", "type": "str", "required": True,
                             "description": ["The API token"]},
                        ],
                    },
                    "examples": "- debug: msg=\"{{ query('netbox.netbox.nb_lookup', 'sites') }}\"",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "filter",
                "content_name": "nb_filter",
                "doc_strings": {
                    "doc": {
                        "short_description": "Filter NetBox data",
                        "description": ["Filters NetBox query results."],
                        "options": [],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "inventory",
                "content_name": "nb_inventory",
                "doc_strings": {
                    "doc": {
                        "short_description": "NetBox inventory source",
                        "description": ["Dynamic inventory from NetBox."],
                        "options": [
                            {"name": "api_endpoint", "type": "str", "required": True,
                             "description": ["The URL to the NetBox instance"]},
                        ],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
        ],
    },
}
```

Add fixture:

```python
@pytest.fixture
def sample_docs_blob_with_plugins():
    return SAMPLE_DOCS_BLOB_WITH_PLUGINS
```

- [ ] **Step 2: Write failing tests for Galaxy plugin methods**

```python
# tests/test_galaxy.py — add at end (within the file's existing test structure)

class TestFindPlugin:
    def test_finds_lookup_plugin(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "nb_lookup", "lookup")
        assert result is not None
        assert result["content_name"] == "nb_lookup"

    def test_finds_filter_plugin(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "nb_filter", "filter")
        assert result is not None

    def test_returns_none_for_missing(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "nonexistent", "lookup")
        assert result is None

    def test_does_not_match_module_as_plugin(self, sample_docs_blob_with_plugins):
        blob = sample_docs_blob_with_plugins["docs_blob"]
        result = GalaxyClient._find_plugin(blob, "netbox_device", "lookup")
        assert result is None


class TestFetchPluginDoc:
    @pytest.mark.asyncio
    async def test_fetches_lookup_doc(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                doc, meta = await client.fetch_plugin_doc(
                    "netbox.netbox.nb_lookup", "lookup",
                )
        assert "netbox.netbox.nb_lookup" in doc
        assert meta["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_raises_on_missing_plugin(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                with pytest.raises(GalaxyError, match="not found"):
                    await client.fetch_plugin_doc(
                        "netbox.netbox.nonexistent", "lookup",
                    )


class TestListCollectionPlugins:
    @pytest.mark.asyncio
    async def test_lists_plugins(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                plugins, meta = await client.list_collection_plugins("netbox.netbox")
        assert "netbox.netbox.nb_lookup" in plugins
        assert plugins["netbox.netbox.nb_lookup"]["plugin_type"] == "lookup"
        assert "netbox.netbox.nb_filter" in plugins
        assert "netbox.netbox.nb_inventory" in plugins
        assert len(plugins) == 3

    @pytest.mark.asyncio
    async def test_excludes_modules_and_roles(self, sample_docs_blob_with_plugins):
        client = GalaxyClient(base_url="https://galaxy.example.com")
        with patch.object(client, "latest_version", return_value="1.0.0"):
            with patch.object(client, "_fetch_docs_blob",
                              return_value=sample_docs_blob_with_plugins["docs_blob"]):
                plugins, _ = await client.list_collection_plugins("netbox.netbox")
        fqcns = list(plugins.keys())
        assert not any("netbox_device" in f for f in fqcns)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_galaxy.py::TestFindPlugin tests/test_galaxy.py::TestFetchPluginDoc tests/test_galaxy.py::TestListCollectionPlugins -v`
Expected: FAIL

- [ ] **Step 4: Implement Galaxy plugin methods**

In `src/ansible_know/galaxy.py`, add to `GalaxyClient`:

```python
@staticmethod
def _find_plugin(
    blob: dict[str, Any], short_name: str, plugin_type: str,
) -> dict[str, Any] | None:
    for item in blob.get("contents", []):
        if (
            item.get("content_type") == plugin_type
            and item.get("content_name") == short_name
        ):
            return item
    return None

async def fetch_plugin_doc(
    self, plugin_name: str, plugin_type: str, version: str | None = None,
) -> tuple[dict[str, Any], DocProvenance]:
    """Fetch plugin documentation from Galaxy.

    Returns (plugin_doc, meta) where plugin_doc mimics ansible-doc --json
    format and meta contains provenance fields.
    """
    namespace, name, short_plugin = _parse_fqcn(plugin_name)
    resolved_version = version or await self.latest_version(namespace, name)
    is_latest = version is None

    blob = await self._fetch_docs_blob(namespace, name, resolved_version)
    plugin_entry = self._find_plugin(blob, short_plugin, plugin_type)
    if plugin_entry is None:
        raise GalaxyError(
            f"Plugin '{short_plugin}' (type={plugin_type}) not found in "
            f"{namespace}.{name} {resolved_version} docs-blob."
        )

    from ansible_know.parser import transform_galaxy_to_ansible_doc_format
    doc = transform_galaxy_to_ansible_doc_format(plugin_name, plugin_entry)

    meta: DocProvenance = {
        "doc_source": "galaxy",
        "doc_version": resolved_version,
    }
    if is_latest:
        meta["doc_warning"] = (
            f"Documentation sourced from Galaxy "
            f"({namespace}.{name} {resolved_version}). "
            f"Your installed version may differ."
        )
    if self.server_name:
        meta["doc_source_server"] = self.server_name
    return doc, meta

async def list_collection_plugins(
    self, collection_fqcn: str, version: str | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """List plugins in a collection from the Galaxy docs-blob.

    Returns (plugins, meta) where plugins is
    {fqcn: {"description": str, "plugin_type": str}, ...}.
    Excludes modules and roles.

    Not currently called from the MCP server — manifest generation uses
    local parser.list_plugins instead. This method enables future Galaxy
    fallback for manifest generation when collections are not installed
    locally (parallel to fetch_plugin_doc which IS used for fallback).
    """
    from ansible_know.config import PLUGIN_TYPES

    parts = collection_fqcn.split(".")
    if len(parts) != 2:
        raise GalaxyError(
            f"'{collection_fqcn}' is not a valid collection FQCN "
            f"(expected namespace.name)."
        )
    namespace, name = parts
    resolved_version = version or await self.latest_version(namespace, name)

    blob = await self._fetch_docs_blob(namespace, name, resolved_version)
    plugins: dict[str, dict[str, str]] = {}
    for item in blob.get("contents", []):
        ct = item.get("content_type", "")
        if ct not in PLUGIN_TYPES:
            continue
        short = item.get("content_name", "")
        fqcn = f"{collection_fqcn}.{short}"
        desc = item.get("doc_strings", {}).get("doc", {}).get(
            "short_description", "",
        ) or ""
        plugins[fqcn] = {"description": desc, "plugin_type": ct}

    meta = {"source": "galaxy", "version": resolved_version}
    return plugins, meta
```

Update `search_collections` to count plugins — add after the `role_count` line in the loop body:

```python
# At the top of search_collections loop body, after role_count:
from ansible_know.config import PLUGIN_TYPES as _PLUGIN_TYPES
plugin_count = sum(
    1 for c in contents if c.get("content_type") in _PLUGIN_TYPES
)
```

And add `"plugin_count": plugin_count` to the candidate dict.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`
Expected: all PASS

- [ ] **Step 6: Verify existing Galaxy tests still pass (search_collections may need fixture updates)**

Run: `.venv/bin/pytest tests/test_galaxy.py -v`

If `search_collections` tests fail because fixtures don't include `plugin_count`, update the relevant test assertions to expect `plugin_count: 0` in results that had no plugins in their fixture data.

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/galaxy.py tests/conftest.py tests/test_galaxy.py
git commit -m "feat: add Galaxy client plugin discovery and documentation

Add _find_plugin(), fetch_plugin_doc(), and list_collection_plugins()
to GalaxyClient. Update search_collections to include plugin_count in
results. Reuses transform_galaxy_to_ansible_doc_format for consistent
output format.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Resolution Layer Plugin Support

**Files:**
- Modify: `src/ansible_know/resolution.py` (add `resolve_plugin_doc`)
- Modify: `tests/test_resolution.py` (add tests for `resolve_plugin_doc`)

**Interfaces:**
- Consumes:
  - `parser.get_plugin_doc(name, type, collections_path=...)` from Task 2
  - `parser.extract_plugin_metadata(doc, type)` from Task 2
  - `GalaxyClient.fetch_plugin_doc(name, type)` from Task 3
- Produces:
  - `resolve_plugin_doc(plugin_name, plugin_type, http_client=None, galaxy_servers=None, client_factory=None, missing_collections=None, collections_path=None) -> dict[str, Any]`

**Skills:** Load before starting this task:
- `python-contract-docstrings` — contract docstring for resolve_plugin_doc
- `python-try-except` — audit the layered try/except for CollectionNotFoundError vs AnsibleDocError vs GalaxyError
- `pep8-type-annotations` — annotation style on the long parameter list

- [ ] **Step 1: Write failing tests**

```python
# tests/test_resolution.py — add at end

class TestResolvePluginDoc:
    @pytest.mark.asyncio
    async def test_returns_local_doc(self):
        mock_doc = {
            "netbox.netbox.nb_lookup": {
                "doc": {
                    "short_description": "Queries NetBox",
                    "options": {},
                },
                "examples": "",
            },
        }
        with patch("ansible_know.parser.get_plugin_doc", return_value=mock_doc):
            with patch("ansible_know.parser.extract_plugin_metadata", return_value={
                "plugin_name": "netbox.netbox.nb_lookup",
                "plugin_type": "lookup",
                "short_description": "Queries NetBox",
                "params": [],
                "examples": "",
            }):
                result = await resolve_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        assert result["doc_source"] == "local"
        assert result["plugin_type"] == "lookup"
        assert result["plugin_name"] == "netbox.netbox.nb_lookup"

    @pytest.mark.asyncio
    async def test_falls_back_to_galaxy(self):
        galaxy_doc = {
            "netbox.netbox.nb_lookup": {
                "doc": {"short_description": "Queries NetBox", "options": {}},
                "examples": "",
            },
        }
        galaxy_meta = {"doc_source": "galaxy", "doc_version": "1.0.0"}

        mock_client = AsyncMock()
        mock_client.fetch_plugin_doc = AsyncMock(return_value=(galaxy_doc, galaxy_meta))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        def factory(config, http_client=None):
            return mock_client

        with patch("ansible_know.parser.get_plugin_doc", side_effect=CollectionNotFoundError("not found")):
            with patch("ansible_know.parser.extract_plugin_metadata", return_value={
                "plugin_name": "netbox.netbox.nb_lookup",
                "plugin_type": "lookup",
                "short_description": "Queries NetBox",
                "params": [],
                "examples": "",
            }):
                from ansible_know.galaxy_config import GalaxyServerConfig
                servers = [GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")]
                result = await resolve_plugin_doc(
                    "netbox.netbox.nb_lookup", "lookup",
                    galaxy_servers=servers,
                    client_factory=factory,
                )
        assert result["doc_source"] == "galaxy"

    @pytest.mark.asyncio
    async def test_unavailable_when_no_client(self):
        with patch("ansible_know.parser.get_plugin_doc", side_effect=CollectionNotFoundError("not found")):
            result = await resolve_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        assert result["doc_source"] == "unavailable"
```

Add required imports at the top of test_resolution.py:

```python
from ansible_know.resolution import resolve_plugin_doc
from ansible_know.errors import CollectionNotFoundError
from unittest.mock import AsyncMock
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resolution.py::TestResolvePluginDoc -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement resolve_plugin_doc**

Add to `src/ansible_know/resolution.py`:

Replace the `__all__` list with:

```python
__all__ = [
    "resolve_module_doc",
    "resolve_plugin_doc",
    "resolve_role_doc",
    "search_galaxy_collections",
]
```

```python
async def resolve_plugin_doc(
    plugin_name: str,
    plugin_type: str,
    http_client: httpx.AsyncClient | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    client_factory: GalaxyClientFactory | None = None,
    missing_collections: set[str] | None = None,
    collections_path: str | None = None,
) -> dict[str, Any]:
    """Try local ansible-doc -t <type>, fall back to Galaxy.

    Returns the complete tool response dict including doc_source and plugin_type.
    """
    from ansible_know import parser
    from ansible_know.errors import CollectionNotFoundError, GalaxyError

    servers = _get_servers(galaxy_servers)
    namespace = ".".join(plugin_name.split(".")[:2]) if "." in plugin_name else None
    cpath = collections_path

    local_doc: dict[str, Any] = {}

    if not (namespace and missing_collections is not None and namespace in missing_collections):
        try:
            local_doc = await run_in_executor(
                parser.get_plugin_doc, plugin_name, plugin_type,
                collections_path=cpath,
            )
        except CollectionNotFoundError:
            if namespace and missing_collections is not None:
                missing_collections.add(namespace)
            local_doc = {}
        except AnsibleDocError as exc:
            logger.warning("Local plugin doc failed for %s: %s", plugin_name, exc)
            local_doc = {}

    if local_doc:
        metadata = parser.extract_plugin_metadata(local_doc, plugin_type)
        metadata["content_type"] = "plugin"
        metadata["doc_source"] = "local"
        return metadata

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolution.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/resolution.py tests/test_resolution.py
git commit -m "feat: add resolve_plugin_doc with local-then-Galaxy fallback

Follows the same pattern as resolve_role_doc: try local ansible-doc
first, fall back to Galaxy docs-blob, degrade gracefully.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Collection Manifest Plugin Support

**Files:**
- Modify: `src/ansible_know/collection_manifest.py` (add plugin entries to manifest)
- Modify: `tests/test_collection_manifest.py` (test plugin entries)

**Interfaces:**
- Consumes: `ManifestPluginEntry` from `types.py`, `SKILLS_DIR` from `config.py`
- Produces: Updated `generate_manifest()` that accepts `plugins_metadata` and includes `plugins` and `plugin_count` in output

**Skills:** Load before starting this task:
- `pep8-type-annotations` — annotation for the new `plugins_metadata` parameter
- `pep8-naming` — variable naming consistency with existing module/role patterns

- [ ] **Step 1: Write failing tests**

```python
# tests/test_collection_manifest.py — add at end

class TestManifestPluginEntries:
    def test_includes_plugins_in_manifest(self, tmp_path):
        plugins_metadata = [
            {
                "fqcn": "netbox.netbox.nb_lookup",
                "plugin_type": "lookup",
                "description": "Queries NetBox",
                "param_count": 3,
            },
            {
                "fqcn": "netbox.netbox.nb_inventory",
                "plugin_type": "inventory",
                "description": "NetBox dynamic inventory",
                "param_count": 5,
            },
        ]
        manifest = generate_manifest(
            "netbox.netbox", [], plugins_metadata=plugins_metadata,
            skills_dir=tmp_path,
        )
        assert manifest["plugin_count"] == 2
        assert len(manifest["plugins"]) == 2
        lookup = next(p for p in manifest["plugins"] if p["fqcn"] == "netbox.netbox.nb_lookup")
        assert lookup["plugin_type"] == "lookup"
        assert lookup["has_skill"] is False

    def test_empty_plugins_defaults(self, tmp_path):
        manifest = generate_manifest("test.test", [], skills_dir=tmp_path)
        assert manifest["plugin_count"] == 0
        assert manifest["plugins"] == []

    def test_plugin_skill_detection(self, tmp_path):
        skill_dir = tmp_path / "netbox.netbox" / "lookup__nb_lookup"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n")

        plugins_metadata = [
            {
                "fqcn": "netbox.netbox.nb_lookup",
                "plugin_type": "lookup",
                "description": "Queries NetBox",
                "param_count": 3,
            },
        ]
        manifest = generate_manifest(
            "netbox.netbox", [], plugins_metadata=plugins_metadata,
            skills_dir=tmp_path,
        )
        assert manifest["plugins"][0]["has_skill"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_collection_manifest.py::TestManifestPluginEntries -v`
Expected: FAIL

- [ ] **Step 3: Update generate_manifest to accept plugins**

```python
# src/ansible_know/collection_manifest.py — update generate_manifest signature and body:

def generate_manifest(
    collection_namespace: str,
    modules_metadata: list[ModuleMetadata],
    roles_metadata: list[dict[str, Any]] | None = None,
    plugins_metadata: list[dict[str, Any]] | None = None,
    skills_dir: Path | None = None,
    collection_version: str | None = None,
) -> ManifestResult:
    # ... existing module and role code unchanged ...

    plugins_list = []
    for plugin_meta in (plugins_metadata or []):
        fqcn = plugin_meta["fqcn"]
        ptype = plugin_meta.get("plugin_type", "")
        short_name = fqcn.rsplit(".", 1)[-1]
        has_skill = (collection_dir / f"{ptype}__{short_name}" / "SKILL.md").exists()

        plugins_list.append({
            "fqcn": fqcn,
            "plugin_type": ptype,
            "description": plugin_meta.get("description", ""),
            "param_count": plugin_meta.get("param_count", 0),
            "has_skill": has_skill,
        })

    # Update manifest dict to include:
    manifest = {
        # ... existing fields ...
        "plugin_count": len(plugins_list),
        # ... existing fields ...
        "plugins": plugins_list,
    }
```

- [ ] **Step 3b: Add backwards-compat backfill in load_cached_manifest**

```python
# src/ansible_know/collection_manifest.py — update load_cached_manifest, after json.loads:

    manifest = json.loads(manifest_path.read_text())
    if installed_version and manifest.get("collection_version") != installed_version:
        return None
    # Backfill fields added in later versions
    manifest.setdefault("plugin_count", 0)
    manifest.setdefault("plugins", [])
    return manifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_collection_manifest.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/collection_manifest.py tests/test_collection_manifest.py
git commit -m "feat: add plugin entries to collection manifest

generate_manifest() now accepts plugins_metadata and includes plugins
list and plugin_count in the manifest output.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Plugin Skill Template and Rendering

**Files:**
- Create: `src/ansible_know/templates/PLUGIN_SKILL.md.j2`
- Modify: `src/ansible_know/skills.py` (add `render_plugin_skill`, `write_plugin_skill_package`, `_plugin_template_context`)
- Modify: `tests/test_skills.py` (add tests for plugin rendering)

**Interfaces:**
- Consumes: `PluginMetadata` from `types.py`, `JINJA2_PLUGIN_TYPES`, `PLAYBOOK_PLUGIN_TYPES` from `config.py`
- Produces:
  - `render_plugin_skill(metadata: dict[str, Any]) -> str`
  - `write_plugin_skill_package(output_dir: Path, metadata: dict[str, Any]) -> None`

**Skills:** Load before starting this task:
- `python-contract-docstrings` — docstrings for render/write functions
- `pep8-naming` — function and variable naming

- [ ] **Step 1: Create the PLUGIN_SKILL.md.j2 template**

```jinja2
{# src/ansible_know/templates/PLUGIN_SKILL.md.j2 #}
---
name: {{ plugin_name }}
description: >-
  {{ short_description }}
  Use when you need the {{ skill_name | replace('_', ' ') }} {{ plugin_type }} plugin in Ansible.
---

# {{ plugin_name }} ({{ plugin_type }} plugin)

{{ short_description }}

## When to Use This Skill

{% if plugin_type == "lookup" %}
Use the `{{ plugin_name }}` lookup plugin to **retrieve data** from external sources during playbook execution. Lookup plugins run on the **control node** and return data that can be used in variables, templates, and task arguments.

Prefer this over `ansible.builtin.uri` + `register` + JSON parsing when a dedicated lookup exists — it's more concise, handles pagination and auth, and integrates with Jinja2 naturally.
{% elif plugin_type == "filter" %}
Use the `{{ plugin_name }}` filter plugin to **transform data** inline within Jinja2 expressions. Filters are chainable and run on the control node during template rendering.
{% elif plugin_type == "test" %}
Use the `{{ plugin_name }}` test plugin in Jinja2 `{% raw %}{% if %}{% endraw %}` and `when:` conditionals. Tests return boolean values and are used with `is` syntax.
{% elif plugin_type == "connection" %}
Use the `{{ plugin_name }}` connection plugin to define **how Ansible connects** to managed hosts. Set it at play level or in inventory.
{% elif plugin_type == "become" %}
Use the `{{ plugin_name }}` become plugin to define **how Ansible escalates privileges** on managed hosts.
{% elif plugin_type == "strategy" %}
Use the `{{ plugin_name }}` strategy plugin to control **how Ansible executes tasks** across hosts (linear, free, etc.).
{% elif plugin_type == "callback" %}
Use the `{{ plugin_name }}` callback plugin to **customize output** or trigger actions on playbook events. Configure in `ansible.cfg`.
{% elif plugin_type == "inventory" %}
Use the `{{ plugin_name }}` inventory plugin to **dynamically discover hosts** from an external source. Configure as a YAML inventory source file.
{% elif plugin_type == "cache" %}
Use the `{{ plugin_name }}` cache plugin to **persist gathered facts** between playbook runs. Configure in `ansible.cfg`.
{% else %}
Use the `{{ plugin_name }}` {{ plugin_type }} plugin when the standard Ansible behavior needs to be extended or customized for your environment.
{% endif %}

{% if params %}
## Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
{% for p in params -%}
| `{{ p.name }}` | {{ p.type }} | {{ "yes" if p.required else "no" }} | {{ p.default if p.default is not none else "-" }} | {{ p.description }} |
{% endfor %}
{% if params | selectattr("choices") | list %}
### Parameter Choices

{% for p in params %}{% if p.choices %}
- **{{ p.name }}**: {{ p.choices | join(", ") }}
{% endif %}{% endfor %}
{% endif %}
{% endif %}

## Usage

{% if plugin_type == "lookup" %}
### In a playbook task (query form — recommended)

```yaml
- name: Get data via {{ skill_name }}
  ansible.builtin.debug:
    msg: "{{ '{{' }} query('{{ plugin_name }}'{% if params %}, {% for p in params %}{% if p.required %}{{ p.name }}='<{{ p.name }}>'{% if not loop.last %}, {% endif %}{% endif %}{% endfor %}{% endif %}) {{ '}}' }}"
```

### In a variable definition

```yaml
vars:
  my_data: "{{ '{{' }} lookup('{{ plugin_name }}'{% if params %}, {% for p in params %}{% if p.required %}{{ p.name }}='<{{ p.name }}>'{% if not loop.last %}, {% endif %}{% endif %}{% endfor %}{% endif %}) {{ '}}' }}"
```

### In a loop

```yaml
- name: Process each item from {{ skill_name }}
  ansible.builtin.debug:
    msg: "{{ '{{' }} item {{ '}}' }}"
  loop: "{{ '{{' }} query('{{ plugin_name }}'{% if params %}, {% for p in params %}{% if p.required %}{{ p.name }}='<{{ p.name }}>'{% if not loop.last %}, {% endif %}{% endif %}{% endfor %}{% endif %}) {{ '}}' }}"
```
{% elif plugin_type == "filter" %}
### Inline in Jinja2

```yaml
- name: Transform data with {{ skill_name }}
  ansible.builtin.debug:
    msg: "{{ '{{' }} my_data | {{ plugin_name }}({% for p in params %}{% if p.required %}<{{ p.name }}>{% if not loop.last %}, {% endif %}{% endif %}{% endfor %}) {{ '}}' }}"
```
{% elif plugin_type == "test" %}
### In a when conditional

```yaml
- name: Check condition with {{ skill_name }}
  ansible.builtin.debug:
    msg: "Condition met"
  when: my_value is {{ plugin_name }}({% for p in params %}{% if p.required %}<{{ p.name }}>{% if not loop.last %}, {% endif %}{% endif %}{% endfor %})
```
{% elif plugin_type == "connection" %}
### In a play

```yaml
- hosts: target_hosts
  connection: {{ plugin_name }}
  tasks:
    - name: Run task via {{ skill_name }}
      ansible.builtin.command: hostname
```
{% elif plugin_type == "become" %}
### In a play

```yaml
- hosts: target_hosts
  become: true
  become_method: {{ plugin_name }}
  tasks:
    - name: Run privileged task
      ansible.builtin.command: whoami
```
{% elif plugin_type == "callback" %}
### In ansible.cfg

```ini
[defaults]
{% if "stdout" in skill_name %}
stdout_callback = {{ plugin_name }}
{% else %}
callbacks_enabled = {{ plugin_name }}
{% endif %}
```

### Via environment variable

```bash
{% if "stdout" in skill_name %}
ANSIBLE_STDOUT_CALLBACK={{ plugin_name }} ansible-playbook playbook.yml
{% else %}
ANSIBLE_CALLBACKS_ENABLED={{ plugin_name }} ansible-playbook playbook.yml
{% endif %}
```
{% elif plugin_type == "inventory" %}
### Inventory source file (e.g. `my_inventory.{{ skill_name }}.yml`)

```yaml
plugin: {{ plugin_name }}
{% for p in params %}{% if p.required %}
{{ p.name }}: <{{ p.name }}>
{% endif %}{% endfor %}
```

### Usage

```bash
ansible-playbook -i my_inventory.{{ skill_name }}.yml playbook.yml
```
{% elif plugin_type == "strategy" %}
### In a play

```yaml
- hosts: all
  strategy: {{ plugin_name }}
  tasks:
    - name: Run with {{ skill_name }} strategy
      ansible.builtin.debug:
        msg: "Hello"
```
{% elif plugin_type == "cache" %}
### In ansible.cfg

```ini
[defaults]
gathering = smart
fact_caching = {{ plugin_name }}
fact_caching_timeout = 3600
```
{% else %}
Refer to Ansible documentation for configuration details for this {{ plugin_type }} plugin.
{% endif %}

{% if examples %}
### Examples from Ansible Documentation

```yaml
{{ examples }}
```
{% endif %}

## Safety

{% if plugin_type in ("lookup", "inventory") %}
- **Credentials**: This plugin communicates with an external service. Keep tokens and URLs out of version control — use environment variables or Ansible vault.
{% endif %}
- **Check mode**: Plugin behavior during `--check` depends on the specific plugin implementation
- **FQCN**: Always use the fully qualified name `{{ plugin_name }}` to avoid ambiguity
```

- [ ] **Step 2: Write failing tests for plugin skill rendering**

```python
# tests/test_skills.py — add at end

from ansible_know.skills import render_plugin_skill, write_plugin_skill_package


class TestRenderPluginSkill:
    def test_renders_lookup_skill(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries and returns elements from NetBox",
            "params": [
                {"name": "api_endpoint", "type": "str", "required": True,
                 "default": None, "choices": None, "description": "NetBox URL", "aliases": []},
                {"name": "token", "type": "str", "required": True,
                 "default": None, "choices": None, "description": "API token", "aliases": []},
            ],
            "examples": "- debug: msg=\"{{ query('netbox.netbox.nb_lookup', 'sites') }}\"",
        }
        result = render_plugin_skill(metadata)
        assert "lookup plugin" in result
        assert "query('netbox.netbox.nb_lookup'" in result
        assert "lookup('netbox.netbox.nb_lookup'" in result
        assert "ansible.builtin.uri" in result  # the "prefer this over uri" guidance

    def test_renders_filter_skill(self):
        metadata = {
            "plugin_name": "ansible.builtin.to_yaml",
            "plugin_type": "filter",
            "short_description": "Convert to YAML",
            "params": [],
            "examples": "",
        }
        result = render_plugin_skill(metadata)
        assert "filter plugin" in result
        assert "ansible.builtin.to_yaml" in result

    def test_renders_inventory_skill(self):
        metadata = {
            "plugin_name": "netbox.netbox.nb_inventory",
            "plugin_type": "inventory",
            "short_description": "NetBox inventory source",
            "params": [
                {"name": "api_endpoint", "type": "str", "required": True,
                 "default": None, "choices": None, "description": "NetBox URL", "aliases": []},
            ],
            "examples": "",
        }
        result = render_plugin_skill(metadata)
        assert "inventory plugin" in result
        assert "plugin: netbox.netbox.nb_inventory" in result

    def test_renders_connection_skill(self):
        metadata = {
            "plugin_name": "ansible.netcommon.network_cli",
            "plugin_type": "connection",
            "short_description": "CLI connection to network devices",
            "params": [],
            "examples": "",
        }
        result = render_plugin_skill(metadata)
        assert "connection plugin" in result
        assert "connection: ansible.netcommon.network_cli" in result


class TestWritePluginSkillPackage:
    def test_writes_skill_md(self, tmp_path):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        write_plugin_skill_package(tmp_path / "lookup__nb_lookup", metadata)
        assert (tmp_path / "lookup__nb_lookup" / "SKILL.md").exists()

    def test_no_scripts_directory(self, tmp_path):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        write_plugin_skill_package(tmp_path / "lookup__nb_lookup", metadata)
        assert not (tmp_path / "lookup__nb_lookup" / "scripts").exists()

    def test_no_assets_directory(self, tmp_path):
        metadata = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
        }
        write_plugin_skill_package(tmp_path / "lookup__nb_lookup", metadata)
        assert not (tmp_path / "lookup__nb_lookup" / "assets").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_skills.py::TestRenderPluginSkill tests/test_skills.py::TestWritePluginSkillPackage -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Add plugin rendering functions to skills.py**

```python
# src/ansible_know/skills.py — add after write_role_skill_package

def _plugin_template_context(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build template context from plugin metadata."""
    plugin_name = metadata["plugin_name"]
    return {
        "plugin_name": plugin_name,
        "plugin_type": metadata["plugin_type"],
        "skill_name": plugin_name.rsplit(".", 1)[-1],
        "short_description": metadata.get("short_description", ""),
        "params": metadata.get("params", []),
        "examples": metadata.get("examples", "").strip(),
    }


def render_plugin_skill(metadata: dict[str, Any]) -> str:
    """Render the PLUGIN_SKILL.md.j2 template with plugin metadata."""
    logger.debug("Rendering plugin skill for %s", metadata.get("plugin_name", "?"))
    env = _get_template_env()
    template = env.get_template("PLUGIN_SKILL.md.j2")
    return template.render(**_plugin_template_context(metadata))


def write_plugin_skill_package(output_dir: Path, metadata: dict[str, Any]) -> None:
    """Write the plugin skill package: SKILL.md only (no scripts/ or assets/)."""
    logger.debug(
        "Writing plugin skill package to %s for %s",
        output_dir, metadata.get("plugin_name", "?"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    content = render_plugin_skill(metadata)
    (output_dir / "SKILL.md").write_text(content)
```

Add to `__all__`:

```python
"render_plugin_skill",
"write_plugin_skill_package",
```

- [ ] **Step 4b: Prepare collection skill functions for plugins_metadata parameter**

Add `plugins_metadata` as a no-op parameter to `render_collection_skill` and
`write_collection_skill_package` now, so Task 7 can pass it without breaking.
Task 8 wires the template to actually use it.

```python
# src/ansible_know/skills.py — update existing signatures:

def render_collection_skill(
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[dict[str, Any]] | None = None,
) -> str:
    env = _get_template_env()
    template = env.get_template("COLLECTION_SKILL.md.j2")
    ctx = _collection_template_context(namespace, metadata_list, collection_version)
    return template.render(**ctx)


def write_collection_skill_package(
    output_dir: Path,
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[dict[str, Any]] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    content = render_collection_skill(
        namespace, metadata_list, collection_version, plugins_metadata,
    )
    (output_dir / "SKILL.md").write_text(content)
```

Note: `plugins_metadata` is accepted but not yet passed to `_collection_template_context`
or the template. Task 8 adds the template section and wires the context builder.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skills.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/ansible_know/templates/PLUGIN_SKILL.md.j2 src/ansible_know/skills.py tests/test_skills.py
git commit -m "feat: add plugin skill template and rendering functions

Add PLUGIN_SKILL.md.j2 with type-specific usage examples for lookup,
filter, test, connection, become, strategy, callback, inventory, cache,
and infrastructure plugins. Add render_plugin_skill() and
write_plugin_skill_package() to skills.py.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Server Tools for Plugin Discovery and Documentation

**Files:**
- Modify: `src/ansible_know/server.py` (add `search_plugins`, `get_plugin_doc`, `generate_plugin_skill` tools; update `get_collection_manifest` and `generate_collection_skills` to include plugins)
- Modify: `tests/test_server.py` (add tests for new tools)

**Interfaces:**
- Consumes:
  - `parser.search_plugins()`, `parser.list_plugins()`, `parser.extract_plugin_metadata()` from Task 2
  - `resolution.resolve_plugin_doc()` from Task 4
  - `skills.render_plugin_skill()`, `skills.write_plugin_skill_package()` from Task 6
- Produces:
  - `search_plugins(keyword, plugin_type=None, namespace=None, ctx=None)` — MCP tool
  - `get_plugin_doc(plugin_name, plugin_type, ctx=None)` — MCP tool
  - `generate_plugin_skill(plugin_name, plugin_type, install_to=None, ctx=None)` — MCP tool

**Skills:** Load before starting this task:
- `mcp-builder` — new MCP tools must follow FastMCP patterns (ToolAnnotations, Annotated params)
- `python-try-except` — audit try/except in tool handlers and asyncio.gather error handling
- `python-contract-docstrings` — tool docstrings serve as MCP tool descriptions
- `pep8-type-annotations` — Annotated[] parameter style for tool signatures

- [ ] **Step 1: Write failing tests for server plugin tools**

```python
# tests/test_server.py — add at end (follow existing test patterns)

class TestSearchPlugins:
    @pytest.mark.asyncio
    async def test_returns_matching_plugins(self):
        mock_results = {
            "netbox.netbox.nb_lookup": "Queries and returns elements from NetBox",
        }
        with patch("ansible_know.parser.search_plugins", return_value=mock_results):
            result = await search_plugins("netbox", plugin_type="lookup")
        assert "netbox.netbox.nb_lookup" in result

    @pytest.mark.asyncio
    async def test_validates_plugin_type(self):
        result = await search_plugins("test", plugin_type="bogus_type")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_keyword_returns_all(self):
        mock_results = {"ansible.builtin.env": "Read env vars"}
        with patch("ansible_know.parser.search_plugins", return_value=mock_results):
            result = await search_plugins("", plugin_type="lookup")
        assert "ansible.builtin.env" in result


class TestGetPluginDoc:
    @pytest.mark.asyncio
    async def test_returns_plugin_metadata(self):
        mock_result = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
            "doc_source": "local",
            "content_type": "plugin",
        }
        with patch("ansible_know.resolution.resolve_plugin_doc", return_value=mock_result):
            result = await get_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        assert result["plugin_name"] == "netbox.netbox.nb_lookup"
        assert result["plugin_type"] == "lookup"

    @pytest.mark.asyncio
    async def test_validates_fqcn(self):
        result = await get_plugin_doc("invalid", "lookup")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_validates_plugin_type(self):
        result = await get_plugin_doc("ns.col.name", "bogus")
        assert "error" in result


class TestGeneratePluginSkill:
    @pytest.mark.asyncio
    async def test_returns_skill_content(self, tmp_path):
        mock_result = {
            "plugin_name": "netbox.netbox.nb_lookup",
            "plugin_type": "lookup",
            "short_description": "Queries NetBox",
            "params": [],
            "examples": "",
            "doc_source": "local",
            "content_type": "plugin",
        }
        with patch("ansible_know.resolution.resolve_plugin_doc", return_value=mock_result):
            with patch("ansible_know.skills.write_plugin_skill_package"):
                result = await generate_plugin_skill(
                    "netbox.netbox.nb_lookup", "lookup",
                )
        assert isinstance(result, str)
        assert "nb_lookup" in result
```

Add required imports at the top of test_server.py:

```python
from ansible_know.server import search_plugins, get_plugin_doc, generate_plugin_skill
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py::TestSearchPlugins tests/test_server.py::TestGetPluginDoc tests/test_server.py::TestGeneratePluginSkill -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement server plugin tools**

Add to `src/ansible_know/server.py` after the `search_modules` tool:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_plugins(
    keyword: Annotated[str, "Search term to match against plugin names and descriptions"],
    plugin_type: Annotated[str | None, "Plugin type filter (e.g. 'lookup', 'filter', 'inventory'). If omitted, searches all types."] = None,
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
            from ansible_know.config import PLUGIN_TYPES
            if plugin_type not in PLUGIN_TYPES:
                return {"error": f"Invalid plugin type '{plugin_type}'. Valid: {', '.join(sorted(PLUGIN_TYPES))}"}
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
```

Add `get_plugin_doc` tool after the `get_role_doc` tool:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_plugin_doc(
    plugin_name: Annotated[str, "Fully-qualified plugin name (e.g. 'netbox.netbox.nb_lookup')"],
    plugin_type: Annotated[str, "Plugin type: lookup, filter, test, connection, become, strategy, callback, inventory, cache, cliconf, httpapi, netconf, shell, or vars"],
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
        from ansible_know.config import PLUGIN_TYPES
        if plugin_type not in PLUGIN_TYPES:
            return {"error": f"Invalid plugin type '{plugin_type}'. Valid: {', '.join(sorted(PLUGIN_TYPES))}"}
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
        ns = ".".join(plugin_name.split(".")[:2]) if "." in plugin_name else None
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}
```

Add `generate_plugin_skill` tool after `generate_role_skill`:

```python
@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_plugin_skill(
    plugin_name: Annotated[str, "Fully-qualified plugin name (e.g. 'netbox.netbox.nb_lookup')"],
    plugin_type: Annotated[str, "Plugin type: lookup, filter, test, connection, become, strategy, callback, inventory, cache, cliconf, httpapi, netconf, shell, or vars"],
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
        from ansible_know.config import PLUGIN_TYPES
        if plugin_type not in PLUGIN_TYPES:
            return {"error": f"Invalid plugin type '{plugin_type}'. Valid: {', '.join(sorted(PLUGIN_TYPES))}"}
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

        namespace = ".".join(plugin_name.split(".")[:2])
        short_name = plugin_name.rsplit(".", 1)[-1]
        base_dir = validate_install_path(install_to) if install_to else SKILLS_DIR
        output_dir = base_dir / namespace / f"{plugin_type}__{short_name}"

        await run_in_executor(skills.write_plugin_skill_package, output_dir, metadata)
        logger.info("generate_plugin_skill wrote to %s", output_dir)

        if ctx:
            await ctx.report_progress(progress=100, total=100)

        return truncate_response(skills.render_plugin_skill(metadata))
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("generate_plugin_skill failed: %s", exc)
        ns = ".".join(plugin_name.split(".")[:2]) if "." in plugin_name else None
        return {"error": maybe_add_hint(sanitize_error(str(exc)), ns)}
```

Add `GetPluginDocResult` to the imports from `ansible_know.types`.

- [ ] **Step 4: Update get_collection_manifest to include plugins**

In the `get_collection_manifest` tool function, after the roles discovery block, add:

```python
# Discover plugins across all types (parallel — 14 types via asyncio.gather)
from ansible_know.config import PLUGIN_TYPES

async def _list_one_plugin_type(ptype):
    try:
        return ptype, await run_in_executor(
            parser.list_plugins, ptype,
            collection_filter=collection_namespace,
            collections_path=cpath,
        )
    except (AnsibleDocError, OSError, ValidationError):
        return ptype, {}

plugin_results = await asyncio.gather(
    *[_list_one_plugin_type(pt) for pt in PLUGIN_TYPES]
)
plugins_raw: dict[str, dict[str, str]] = {}
for ptype, type_plugins in plugin_results:
    for pfqcn, pdesc in type_plugins.items():
        plugins_raw[pfqcn] = {"description": pdesc, "plugin_type": ptype}
```

Then update the `generate_manifest` call to pass `plugins_metadata`:

```python
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
```

Also update the "no content found" check:

```python
if not modules and not roles_raw and not plugins_raw:
    return {"error": ...}
```

- [ ] **Step 5: Update generate_collection_skills to include roles and plugins**

The existing `generate_collection_skills` only generates module skills — roles are
silently skipped even though `generate_role_skill`, `parser.list_roles`, and
`skills.write_role_skill_package` all exist. Since we're already adding plugin
discovery here, add role discovery too — the pattern is identical and the code is
already in place.

Restructure `generate_collection_skills` to:
1. Discover modules, roles, AND plugins up front (roles + plugins in parallel)
2. Check the combined guard
3. Generate skills for all three content types
4. Build manifest with all three

First, move discovery before the guard. Replace the existing modules-only check:

```python
# Before (existing):
if not modules:
    return {"error": ...}

# After — discover all content types, then check:
```

Add role and plugin discovery after module discovery, before the guard:

```python
# Discover roles
roles_raw = {}
try:
    roles_raw = await run_in_executor(
        parser.list_roles, collection_filter=collection_namespace,
        collections_path=cpath,
    )
except (AnsibleDocError, OSError) as exc:
    logger.warning("list_roles failed for %s: %s", collection_namespace, exc)

# Discover plugins across all types (parallel)
from ansible_know.config import PLUGIN_TYPES

async def _list_plugin_type(ptype):
    try:
        return ptype, await run_in_executor(
            parser.list_plugins, ptype,
            collection_filter=collection_namespace,
            collections_path=cpath,
        )
    except (AnsibleDocError, OSError, ValidationError):
        return ptype, {}

plugin_list_results = await asyncio.gather(
    *[_list_plugin_type(pt) for pt in PLUGIN_TYPES]
)

# Combined guard — reject only if ALL content types are empty
has_plugins = any(plugins for _, plugins in plugin_list_results)
if not modules and not roles_raw and not has_plugins:
    return {"error": (
        f"No modules, roles, or plugins found in collection '{collection_namespace}'."
        + collection_hint(collection_namespace)
    )}
```

After the existing module skill generation loop, add role skill generation:

```python
# Generate role skills
from ansible_know import resolution

roles_metadata = []
for role_fqcn, role_data in sorted(roles_raw.items()):
    total += 1
    try:
        state = await _get_state(ctx)
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

        short_name = role_fqcn.rsplit(".", 1)[-1]
        output_dir = base_dir / collection_namespace / short_name
        await run_in_executor(
            skills.write_role_skill_package, output_dir, role_meta,
        )
        succeeded += 1
    except Exception as exc:
        logger.warning("Role skill generation failed for %s: %s", role_fqcn, exc)
        failed += 1
```

Then add plugin skill generation:

```python
# Generate plugin skills
plugins_metadata = []
for ptype, type_plugins in plugin_list_results:
    for pfqcn in sorted(type_plugins):
        total += 1
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

            short_name = pfqcn.rsplit(".", 1)[-1]
            output_dir = base_dir / collection_namespace / f"{ptype}__{short_name}"
            await run_in_executor(
                skills.write_plugin_skill_package, output_dir, meta,
            )
            succeeded += 1
        except Exception as exc:
            logger.warning("Plugin skill generation failed for %s: %s", pfqcn, exc)
            failed += 1
```

Pass all three content types to `generate_manifest()` and `write_collection_skill_package()`:

```python
manifest = await run_in_executor(
    collection_manifest.generate_manifest,
    collection_namespace, metadata_list,
    roles_metadata=roles_metadata,
    plugins_metadata=plugins_metadata,
    skills_dir=base_dir,
    collection_version=installed_version,
)
```

```python
await run_in_executor(
    skills.write_collection_skill_package,
    base_dir / collection_namespace, collection_namespace,
    metadata_list, installed_version, plugins_metadata,
)
```

- [ ] **Step 6: Update `_get_skill_sync` and `_list_skills_sync` for plugin skill paths**

Plugin skills use `{plugin_type}__{short_name}` directories (e.g., `lookup__nb_lookup`).
The existing skill lookup and listing functions don't know about this convention.

Update `_get_skill_sync` in `server.py` to try the `{type}__{name}` pattern:

```python
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

        # Module/role skill (direct short_name)
        nested_path = (skills_dir / namespace / short_name / "SKILL.md").resolve()
        validate_path_containment(nested_path, skills_dir)
        if nested_path.exists():
            return truncate_response(nested_path.read_text())

        # Plugin skill ({type}__{short_name} convention)
        from ansible_know.config import PLUGIN_TYPES
        for ptype in PLUGIN_TYPES:
            plugin_path = (skills_dir / namespace / f"{ptype}__{short_name}" / "SKILL.md").resolve()
            validate_path_containment(plugin_path, skills_dir)
            if plugin_path.exists():
                return truncate_response(plugin_path.read_text())

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
```

Update `_list_skills_sync` to strip the `{type}__` prefix for user-facing names:

```python
import re

_PLUGIN_SKILL_DIR_RE = re.compile(r"^([a-z]+)__(.+)$")

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
                    dir_name = sub_dir.name
                    # Strip {type}__ prefix for plugin skills
                    from ansible_know.config import PLUGIN_TYPES
                    match = _PLUGIN_SKILL_DIR_RE.match(dir_name)
                    if match and match.group(1) in PLUGIN_TYPES:
                        display_name = f"{collection}.{match.group(2)}"
                    else:
                        display_name = f"{collection}.{dir_name}"
                    results.append({
                        "name": display_name,
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
```

Add tests for the plugin skill lookup:

```python
# tests/test_server.py — add to existing skill tests

class TestGetPluginSkill:
    @pytest.mark.asyncio
    async def test_finds_plugin_skill_by_fqcn(self, tmp_path):
        skill_dir = tmp_path / "netbox.netbox" / "lookup__nb_lookup"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: netbox.netbox.nb_lookup\n---\nlookup skill")
        with patch("ansible_know.config.SKILLS_DIR", tmp_path):
            result = await get_skill("netbox.netbox.nb_lookup")
        assert "lookup skill" in result

    @pytest.mark.asyncio
    async def test_module_skill_takes_precedence(self, tmp_path):
        # If both a module and plugin skill exist, module (direct name) wins
        mod_dir = tmp_path / "netbox.netbox" / "nb_lookup"
        mod_dir.mkdir(parents=True)
        (mod_dir / "SKILL.md").write_text("module skill")
        plugin_dir = tmp_path / "netbox.netbox" / "lookup__nb_lookup"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "SKILL.md").write_text("plugin skill")
        with patch("ansible_know.config.SKILLS_DIR", tmp_path):
            result = await get_skill("netbox.netbox.nb_lookup")
        assert "module skill" in result


class TestListPluginSkills:
    @pytest.mark.asyncio
    async def test_strips_type_prefix_from_name(self, tmp_path):
        skill_dir = tmp_path / "netbox.netbox" / "lookup__nb_lookup"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n")
        with patch("ansible_know.config.SKILLS_DIR", tmp_path):
            result = await list_skills(collection="netbox.netbox")
        names = [s["name"] for s in result]
        assert "netbox.netbox.nb_lookup" in names
        assert "netbox.netbox.lookup__nb_lookup" not in names
```

- [ ] **Step 6b: Update `resource_skills_list` to strip plugin directory prefixes**

The `skills://list` resource function must strip `{type}__` prefixes consistently
with `_list_skills_sync`. Without this, `skills://list` returns raw directory names
like `netbox.netbox.lookup__nb_lookup` while `list_skills` returns `netbox.netbox.nb_lookup`.

```python
# server.py — update resource_skills_list():

@mcp.resource("skills://list", name="Available Skills", description="List all generated skill packages")
def resource_skills_list() -> str:
    import json

    from ansible_know.config import PLUGIN_TYPES, SKILLS_DIR

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
                    dir_name = sub_dir.name
                    match = _PLUGIN_SKILL_DIR_RE.match(dir_name)
                    if match and match.group(1) in PLUGIN_TYPES:
                        skills_list.append(f"{skill_dir.name}.{match.group(2)}")
                    else:
                        skills_list.append(f"{skill_dir.name}.{dir_name}")
    return json.dumps(skills_list, indent=2)
```

Add a test for this:

```python
# tests/test_server.py — add to TestListPluginSkills:

class TestResourceSkillsListPlugins:
    def test_strips_type_prefix_in_resource(self, tmp_path):
        skill_dir = tmp_path / "netbox.netbox" / "lookup__nb_lookup"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n")
        with patch("ansible_know.config.SKILLS_DIR", tmp_path):
            result = json.loads(resource_skills_list())
        assert "netbox.netbox.nb_lookup" in result
        assert "netbox.netbox.lookup__nb_lookup" not in result
```

- [ ] **Step 7: Update server.py module docstring and MCP instructions**

Update the module docstring to say "16 tools, 5 prompts" instead of "13 tools, 4 prompts" and update the `instructions` string in the `FastMCP` constructor to mention plugins:

```python
"""Ansible Know MCP Server.

Provides 16 tools, 5 resources, and 5 prompts for module, role, and plugin discovery,
documentation search, Galaxy collection discovery, and skill generation
via the Model Context Protocol.
"""
```

```python
instructions=(
    "Ansible module, role, and plugin discovery, documentation, and skill generation. "
    "Workflow: (1) search_collections to discover collections on Galaxy, "
    "(2) ensure_collection to install one for this session, "
    "(3) search_modules/search_plugins/get_collection_manifest to find content, "
    "(4) get_module_doc, get_role_doc, or get_plugin_doc for structured docs, "
    "(5) search_docs for conceptual guides, "
    "(6) generate_skill, generate_role_skill, or generate_plugin_skill for skill packages. "
    "Resources: server://version for version and upgrade status, "
    "galaxy://installed for session collections, "
    "docs://sources for configured doc sources, "
    "skills://list for generated skills."
),
```

- [ ] **Step 8: Run all tests to verify no regressions**

Run: `.venv/bin/pytest tests/ -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "feat: add plugin tools, role+plugin batch generation

Three new MCP tools: search_plugins, get_plugin_doc,
generate_plugin_skill. Updated get_collection_manifest to discover
plugins across all 14 types. Updated generate_collection_skills to
generate role AND plugin skills (previously only generated module
skills). Updated server instructions to mention plugins.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Update CLAUDE.md, Collection Skill Template, and MCP Instructions

**Files:**
- Modify: `CLAUDE.md` (update tool count, add plugin tools to table)
- Modify: `src/ansible_know/templates/COLLECTION_SKILL.md.j2` (add plugin section to collection-level skills)
- Modify: `src/ansible_know/skills.py` (update `_collection_template_context` to accept plugins)

**Interfaces:**
- Consumes: `PluginMetadata` from `types.py`
- Produces: Updated CLAUDE.md documentation, updated collection skill template with plugin listing

**Skills:** Load before starting this task:
- `pep8-type-annotations` — CollectionSkillContext TypedDict update
- `pep8-naming` — template variable naming

- [ ] **Step 1: Update CLAUDE.md**

Add three new rows to the MCP Tools table:

```markdown
| `search_plugins` | read-only | Find plugins by keyword (lookup, filter, inventory, etc.) |
| `get_plugin_doc` | read-only | Get full plugin documentation |
| `generate_plugin_skill` | idempotent write | Generate a skill package for one plugin |
```

Add one row to the MCP Prompts table:

```markdown
| `explain_plugin` | Detailed plugin explanation with usage examples |
```

Update the architecture comment at the top of CLAUDE.md from:
```
├── server.py              # FastMCP server: 13 tools, 4 resources, 4 prompts (entrypoint)
```
to:
```
├── server.py              # FastMCP server: 16 tools, 5 resources, 5 prompts (entrypoint)
```

Update `ManifestResult` in the docs to include `plugin_count` and `plugins`.

- [ ] **Step 1b: Update MCP prompts to mention plugin tools**

Update `review_playbook`:
```python
"Use the search_modules, search_plugins, get_module_doc, and get_plugin_doc "
"tools to verify module and plugin usage.\n\n"
```

Update `find_collection` step 5:
```python
"5. Use get_module_doc, get_role_doc, or get_plugin_doc on relevant content to understand usage"
```

Add a new `explain_plugin` prompt:
```python
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
```

- [ ] **Step 2: Update COLLECTION_SKILL.md.j2 to list plugins**

After the module selection section (`## Phase 2 — Select Modules`), add a plugin section when plugins are present:

```jinja2
{% if plugins_by_type %}
## Available Plugins

These plugins provide data retrieval, transformation, and infrastructure integration.
**Use plugins instead of raw modules** when a plugin exists for your use case —
they are more concise and integrate naturally with Jinja2.

{% for ptype, plugins in plugins_by_type | dictsort %}
### {{ ptype | capitalize }} Plugins

| Plugin | Purpose |
|--------|---------|
{% for p in plugins -%}
| `{{ p.fqcn }}` | {{ p.short_description }} |
{% endfor %}

{% endfor %}
{% endif %}
```

- [ ] **Step 3: Wire _collection_template_context to pass plugins to the template**

The `plugins_metadata` parameter was already added to `render_collection_skill` and
`write_collection_skill_package` in Task 6 Step 4b as a no-op. Now wire it through
to the template context.

```python
# src/ansible_know/skills.py — update _collection_template_context to accept and use plugins:

def _collection_template_context(
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[dict[str, Any]] | None = None,
) -> CollectionSkillContext:
    # ... existing code ...

    # After the modules_by_tag section, add:
    plugins_by_type: dict[str, list[dict[str, str]]] = {}
    for pmeta in (plugins_metadata or []):
        ptype = pmeta.get("plugin_type", "other")
        plugins_by_type.setdefault(ptype, []).append({
            "fqcn": pmeta["fqcn"],
            "short_description": pmeta.get("description", ""),
        })

    # Add to return dict:
    # "plugins_by_type": plugins_by_type,
```

Update `render_collection_skill` to pass `plugins_metadata` through to the context:

```python
def render_collection_skill(
    namespace: str,
    metadata_list: list[ModuleMetadata],
    collection_version: str | None = None,
    plugins_metadata: list[dict[str, Any]] | None = None,
) -> str:
    env = _get_template_env()
    template = env.get_template("COLLECTION_SKILL.md.j2")
    ctx = _collection_template_context(
        namespace, metadata_list, collection_version, plugins_metadata,
    )
    return template.render(**ctx)
```

`write_collection_skill_package` already passes `plugins_metadata` to
`render_collection_skill` (added in Task 6 Step 4b), so no change needed there.

Update `CollectionSkillContext` TypedDict in `types.py` to include `plugins_by_type`:

```python
class CollectionSkillContext(TypedDict):
    collection_namespace: str
    collection_version: str | None
    modules_by_tag: dict[str, list[ModuleTagEntry]]
    all_api: bool
    common_params: list[ParamDict]
    module_count: int
    plugins_by_type: dict[str, list[dict[str, str]]]
```

Note: `generate_collection_skills` in `server.py` already passes `plugins_metadata`
to `write_collection_skill_package` (done in Task 7 Step 5).

- [ ] **Step 4: Write tests for collection skill with plugins**

```python
# tests/test_skills.py — add:

class TestCollectionSkillWithPlugins:
    def test_includes_plugins_section(self):
        metadata_list = [{
            "module_name": "netbox.netbox.netbox_device",
            "short_description": "Manage devices",
            "params": [],
            "examples": "",
            "is_api_module": True,
        }]
        plugins = [
            {"fqcn": "netbox.netbox.nb_lookup", "plugin_type": "lookup",
             "description": "Query NetBox"},
            {"fqcn": "netbox.netbox.nb_inventory", "plugin_type": "inventory",
             "description": "Dynamic inventory"},
        ]
        result = render_collection_skill(
            "netbox.netbox", metadata_list,
            plugins_metadata=plugins,
        )
        assert "Available Plugins" in result
        assert "nb_lookup" in result
        assert "nb_inventory" in result
        assert "Lookup" in result
        assert "Inventory" in result

    def test_no_plugins_section_when_empty(self):
        metadata_list = [{
            "module_name": "netbox.netbox.netbox_device",
            "short_description": "Manage devices",
            "params": [],
            "examples": "",
            "is_api_module": True,
        }]
        result = render_collection_skill("netbox.netbox", metadata_list)
        assert "Available Plugins" not in result
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md src/ansible_know/templates/COLLECTION_SKILL.md.j2 src/ansible_know/skills.py src/ansible_know/types.py src/ansible_know/server.py tests/test_skills.py
git commit -m "feat: add plugin listing to collection skills, update documentation

Collection-level skills now list available plugins by type with
'use plugins instead of raw modules' guidance. Updated CLAUDE.md
tool table to include the three new plugin tools.

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Full Integration Verification

**Files:**
- No new files — verification only
- Optionally: `tests/integration/test_plugin_discovery.py` (if integration test patterns exist)

**Interfaces:**
- Consumes: all previous tasks
- Produces: verified working system

**Skills:** Load before starting this task:
- `skills/pr-architecture-review` — final architecture review before PR, validates layer dependencies and service contracts

- [ ] **Step 1: Run full unit test suite**

Run: `.venv/bin/pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 2: Run linter**

Run: `.venv/bin/ruff check src/ tests/`
Expected: no errors

- [ ] **Step 3: Run type checker if available**

Run: `.venv/bin/pyright src/ansible_know/ 2>/dev/null || echo "pyright not installed, skipping"`

- [ ] **Step 4: Add integration test for Galaxy content_type verification**

Add to `tests/integration/test_galaxy_api.py`:

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_galaxy_plugin_content_types():
    """Verify Galaxy content_type values match our PLUGIN_TYPES constant."""
    from ansible_know.config import PLUGIN_TYPES
    from ansible_know.galaxy import GalaxyClient

    async with GalaxyClient() as client:
        version = await client.latest_version("community", "general")
        blob = await client._fetch_docs_blob("community", "general", version)
        plugin_types = {
            c["content_type"] for c in blob.get("contents", [])
            if c.get("content_type") not in ("module", "role", "action",
                                               "doc_fragments", "module_utils")
        }
        assert plugin_types, "No plugins found in community.general"
        unknown = plugin_types - set(PLUGIN_TYPES)
        assert not unknown, f"Galaxy returned unknown content_types: {unknown}"
```

- [ ] **Step 5: Verify template renders for each plugin type category**

Write a quick smoke test (can be temporary):

```python
# Run in Python REPL or as a script
from ansible_know.skills import render_plugin_skill

for ptype in ("lookup", "filter", "test", "connection", "become",
              "strategy", "callback", "inventory", "cache", "vars"):
    result = render_plugin_skill({
        "plugin_name": f"test.col.test_{ptype}",
        "plugin_type": ptype,
        "short_description": f"Test {ptype} plugin",
        "params": [{"name": "param1", "type": "str", "required": True,
                     "default": None, "choices": None,
                     "description": "Test param", "aliases": []}],
        "examples": "",
    })
    assert f"{ptype} plugin" in result, f"Missing type label for {ptype}"
    assert "test.col.test_" in result
    print(f"  {ptype}: OK ({len(result)} chars)")
```

- [ ] **Step 5: Commit any fixes needed**

If any fixes were needed during verification, commit them as a single fix commit.

- [ ] **Step 6: Final commit summary**

Verify the branch has clean commits and no leftover debug code:

```bash
git log --oneline | head -10
git diff HEAD
```

### Related issues

- **#119** — `resolve_module_doc` returns `tuple[dict, DocProvenance | None]`, inconsistent
  with the `resolve_role_doc` / `resolve_plugin_doc` pattern that returns a complete
  tool response dict. The module path forces the server to do 11 lines of assembly
  that belongs in the resolution layer. Out of scope for this plan but should be
  addressed as follow-up tech debt.
