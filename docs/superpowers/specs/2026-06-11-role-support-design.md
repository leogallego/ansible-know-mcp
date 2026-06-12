# Role Support Design Spec

> **For agentic workers:** Use superpowers:writing-plans to create an
> implementation plan from this spec.

**Goal:** Extend ansible-know MCP server to discover, document, and generate
skills for Ansible roles — not just modules.

**Architecture:** Enrich existing tools with role awareness + add two new
tools + a new HTML parser module. Three-tier doc resolution: local ansible-doc
→ Galaxy readme_html fallback → graceful degradation.

**Tech Stack:** Python 3.10+, html.parser (stdlib), existing Jinja2 templates,
existing Galaxy client.

---

## Problem

Many Ansible collections are role-based (fedora.linux_system_roles,
redhat.rhel_system_roles, debops). The MCP server only understands modules —
agents cannot discover, document, or generate skills for roles.

Real-world data from fedora.linux_system_roles v1.121.0:
- 43 roles, 27 modules in the same collection
- Only 1/43 roles has `meta/argument_specs.yml` (structured docs)
- Galaxy docs-blob has `doc_strings: {}` (empty) for roles — docs come from
  `readme_html` only

This means a structured-only approach would cover ~2% of real-world roles.

---

## Design Decisions

1. **Enrich existing tools** rather than creating parallel tool sets. The LLM
   discovers content type from search results (`role_count` vs `module_count`)
   and uses the same workflow regardless.

2. **Three-tier doc resolution** for roles, mirroring the module pattern:
   - Local `ansible-doc -t role` (when `argument_specs.yml` exists)
   - Galaxy `readme_html` parsing (covers 95%+ of roles)
   - Graceful degradation (FQCN + status only)

3. **Galaxy readme_html** over raw README.md from source repos. The HTML is
   already in the docs-blob (zero extra API calls). Avoids GitHub/GitLab API
   dependency and auth complexity.

4. **Role-specific skill templates** — roles use playbooks, not ad-hoc
   commands. No `run.sh`/`check.sh` scripts.

5. **All entry points** documented, not just `main`. Some roles define
   `configure`, `start`, etc. in their argument specs.

---

## Existing Tool Modifications

### `search_collections` — add `role_count`

The Galaxy docs-blob `contents` array already has `content_type: "role"`
entries alongside modules.

**Change in `galaxy.py` `search_collections()`:**

Add `role_count` to each result dict:

```python
role_count = sum(
    1 for c in contents if c.get("content_type") == "role"
)
```

Result shape adds one field:

```python
{
    "namespace": "fedora.linux_system_roles",
    "module_count": 27,
    "role_count": 43,  # NEW
    ...
}
```

**No extra API call needed:** The Galaxy search endpoint already includes
`contents` in each result (see `collection_version.contents` in the search
response). The existing code counts modules from this array — we add an
identical count for roles.

**Files:** `src/ansible_know/galaxy.py` (search_collections method),
`tests/test_galaxy.py`, `tests/test_server.py`.

### `get_collection_manifest` — add `roles` section

Currently returns `{collection, generated, module_count, modules: [...]}`.

**Change:** Add role discovery via `ansible-doc --list -t role --json
<namespace>` for locally installed collections. For Galaxy-only (no local
install), extract roles from docs-blob `contents`.

New manifest shape:

```python
{
    "collection": "fedora.linux_system_roles",
    "generated": "...",
    "module_count": 27,
    "role_count": 43,       # NEW
    "modules": [...],
    "roles": [              # NEW
        {
            "fqcn": "fedora.linux_system_roles.timesync",
            "description": "Configure time synchronization",
            "has_argument_specs": false,
            "entry_points": ["main"],
            "has_skill": false,
            "tags": ["services"]
        }
    ]
}
```

The `has_argument_specs` flag tells the LLM whether `get_role_doc` will return
structured options or README-parsed data.

**Schema note:** Manifest `entry_points` is a list of entry point names
(summary). `get_role_doc` returns full `entry_points` dict with nested
options (detail). Collections with zero roles return `role_count: 0,
roles: []`.

**Backward compatibility:** New fields (`role_count`, `roles`) are additive.
Consumers built for pre-v0.3.0 manifests ignore them — module-only
functionality is unchanged.

**Files:** `src/ansible_know/parser.py` (new functions),
`src/ansible_know/collection_manifest.py` (generate_manifest),
`src/ansible_know/server.py` (get_collection_manifest tool),
`tests/test_parser.py`, `tests/test_collection_manifest.py`,
`tests/test_server.py`.

---

## New Tool: `get_role_doc`

### Signature

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_role_doc(
    role_name: Annotated[str, "Fully-qualified role name (e.g. 'fedora.linux_system_roles.timesync')"],
    ctx: Context = None,
) -> dict[str, Any]:
```

### Three-tier resolution

1. **Local ansible-doc:** `ansible-doc -t role <fqcn> --json`. If the role has
   `argument_specs.yml`, returns structured entry points with options (same
   format as `ansible-doc` output). If it returns `{}`, fall through.

2. **Galaxy readme_html fallback:** Fetch docs-blob via existing
   `GalaxyClient._fetch_docs_blob()`. Find the role entry by `content_name`.
   Parse `readme_html` using `readme_parser.parse_role_readme()` to extract
   description, variables, examples, and dependencies.

3. **Graceful degradation:** If both fail, return
   `{"role_name": fqcn, "doc_source": "unavailable", "error": "..."}`.

### Return shape

```python
{
    "role_name": "fedora.linux_system_roles.timesync",
    "content_type": "role",
    "short_description": "Configure time synchronization",
    "doc_source": "local",  # "local" | "galaxy_readme" | "unavailable"
    "entry_points": {
        "main": {
            "description": "...",
            "options": [
                {
                    "name": "timesync_ntp_servers",
                    "type": "list",
                    "required": false,
                    "default": null,
                    "description": "List of NTP servers"
                }
            ]
        }
    },
    "dependencies": ["some.collection.other_role"],
    "examples": "- hosts: all\n  roles:\n    - fedora.linux_system_roles.timesync",
    # When doc_source is "galaxy_readme":
    "doc_version": "1.121.0",
    "doc_warning": "Documentation parsed from Galaxy README (best-effort)."
}
```

When `doc_source` is `"galaxy_readme"`, the `entry_points` will have a
synthetic `"main"` entry with variables extracted from the README. If
README parsing yields no variables, entry_points is
`{"main": {"description": "<from README>", "options": []}}`. Roles
always have at least a synthetic `"main"` entry point.

### Validation

Uses existing `validate_fqcn()` — same three-segment format as modules.

**Files:** `src/ansible_know/server.py` (new tool function),
`src/ansible_know/parser.py` (new functions),
`src/ansible_know/galaxy.py` (new methods),
`src/ansible_know/readme_parser.py` (new module),
`tests/test_server.py`, `tests/test_parser.py`, `tests/test_galaxy.py`,
`tests/test_readme_parser.py`.

---

## New Tool: `generate_role_skill`

### Signature

```python
@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def generate_role_skill(
    role_name: Annotated[str, "Fully-qualified role name (e.g. 'fedora.linux_system_roles.timesync')"],
    install_to: Annotated[str | None, "Optional absolute path to install the skill to"] = None,
    ctx: Context = None,
) -> str | dict[str, str]:
```

### Behavior

Uses the same doc resolution as `get_role_doc`. Renders role-specific Jinja2
templates.

### Output structure

```
skills/fedora.linux_system_roles.timesync/
├── SKILL.md          # From ROLE_SKILL.md.j2
└── assets/
    └── playbook.yml  # From role_playbook.yml.j2
```

No `scripts/` directory — roles are invoked via playbooks, not ad-hoc
commands.

### Role skill templates

Two new Jinja2 templates in `src/ansible_know/templates/`:

**`ROLE_SKILL.md.j2`** — Role-oriented SKILL.md:
- Role name, description, doc source
- Entry points with their descriptions
- Variables/defaults table (from argument_specs or README parsing)
- Dependencies list
- Example playbook showing `roles:` usage
- Safety notes (check mode, idempotency)
- Uses FQCN for role references per Ansible good practices

**`role_playbook.yml.j2`** — Example playbook:
- Uses FQCN for role name
- Includes role variables as commented-out defaults
- Follows Ansible CoP: 2-space indent, `true`/`false` booleans, imperative
  task names, no mixed `roles:` + `tasks:` sections

### Skills module changes

New functions in `src/ansible_know/skills.py`:

- `render_role_skill(metadata)` — renders ROLE_SKILL.md.j2
- `write_role_skill_package(output_dir, metadata)` — writes SKILL.md + assets
- `_role_template_context(metadata)` — builds template context from role
  metadata (entry points, variables, dependencies, examples)

**Files:** `src/ansible_know/skills.py`,
`src/ansible_know/templates/ROLE_SKILL.md.j2`,
`src/ansible_know/templates/role_playbook.yml.j2`,
`src/ansible_know/server.py`,
`tests/test_skills.py`, `tests/test_server.py`.

---

## New Module: `readme_parser.py`

Single-purpose module for extracting structured data from Galaxy role README
HTML. Uses Python's built-in `html.parser.HTMLParser` — no external
dependencies.

### Public API

```python
def parse_role_readme(html: str) -> dict[str, Any]:
    """Parse role README HTML into structured data.

    Returns dict with keys:
    - description (str): first paragraph(s) before first heading
    - variables (list[dict]): [{name, type, required, default, description}]
    - examples (str): YAML code blocks concatenated
    - dependencies (list[str]): role FQCNs from Dependencies section

    Best-effort parsing. Never raises on malformed input — returns empty
    fields for sections that cannot be parsed.
    """
```

### Parsing strategy

1. **Description:** Text content between `<h1>` and the first `<h2>`/`<h3>`.
   Strip HTML tags, join paragraphs.

2. **Variables:** From `<table>` elements. Role READMEs commonly use tables
   with columns like Name, Default, Description. Parse `<th>` for column
   headers, `<td>` for values. Map columns to the variable dict schema
   (name, type, required, default, description) by header text matching.

3. **Examples:** `<pre><code>` blocks containing YAML. Detect by presence of
   `---`, `hosts:`, `roles:`, or `tasks:` keywords. Concatenate with `\n\n`
   separator.

4. **Dependencies:** Text under headings containing "Dependencies" or
   "Requirements". Extract role names (FQCN-like patterns) from the content.

### Error handling

- Never raises on malformed HTML — returns empty/partial results
- Uses `html.parser.HTMLParser` which handles malformed HTML gracefully
- Size limit: hard truncate HTML input at 1MB (`html[:1_000_000]`).
  Truncation may produce malformed HTML — the parser handles this
  gracefully via best-effort extraction. No warning added to output.
- Type annotations on all functions, `from __future__ import annotations`
- Exception handling follows try-except skill: only catch specific exceptions
  from external state (HTML parsing), never catch broad `Exception`

### Design constraints

- No external dependencies — stdlib only
- No regex for HTML parsing — use the HTMLParser state machine
- Stateless: no caching, no side effects
- Pure functions: input HTML → output dict

**Files:** `src/ansible_know/readme_parser.py`,
`tests/test_readme_parser.py`.

---

## Parser Layer Changes

New functions in `src/ansible_know/parser.py`:

### `list_roles(namespace=None)`

Wraps `ansible-doc --list -t role --json [namespace]`.

```python
def list_roles(namespace: str | None = None) -> dict[str, dict[str, Any]]:
    """List available roles with descriptions and entry points.

    Returns dict mapping FQCNs to {collection, description, entry_points}.
    """
```

Uses existing `_run_ansible_doc()` — just passes `-t role` flag.

### `get_role_doc(role_name)`

Wraps `ansible-doc -t role <fqcn> --json`.

```python
def get_role_doc(role_name: str) -> dict[str, Any]:
    """Fetch full documentation for a single role.

    Returns parsed JSON from ansible-doc. Returns {} if the role
    lacks argument_specs.yml (same as ansible-doc behavior).
    """
```

**Empty `{}` handling:** `ansible-doc -t role` returns `{}` with exit 0 and
empty stderr when the role exists but has no `argument_specs.yml`. This is
distinct from a missing collection (exit 0, `{}`, stderr contains
"was not found"). The existing `_run_ansible_doc` already handles missing
collections. For the undocumented-role case (`{}` with clean stderr), the
server layer treats this as "fall through to Galaxy" — not an error.

### `extract_role_metadata(role_doc)`

Extracts structured metadata from ansible-doc role JSON.

```python
def extract_role_metadata(role_doc: dict[str, Any]) -> dict[str, Any]:
    """Extract metadata from ansible-doc -t role JSON output.

    Returns:
        {
            "role_name": str,
            "short_description": str,
            "entry_points": {
                "main": {
                    "description": str,
                    "options": [
                        {"name", "type", "required", "default", "description"}
                    ]
                }
            }
        }
    """
```

The entry_points structure nests options under each entry point name. Each
entry point's options use the same schema as module params (name, type,
required, default, choices, description, aliases) for consistency.

**Files:** `src/ansible_know/parser.py`, `tests/test_parser.py`.

---

## Galaxy Layer Changes

New methods in `src/ansible_know/galaxy.py` `GalaxyClient`:

### `_find_role(blob, short_name)` (static method)

Like `_find_module` but filters `content_type == "role"`.

```python
@staticmethod
def _find_role(
    blob: dict[str, Any], short_name: str,
) -> dict[str, Any] | None:
```

### `fetch_role_doc(role_name, version=None)`

Fetches docs-blob, finds the role entry, parses readme_html internally,
and returns structured metadata matching the `extract_role_metadata()` shape.
This mirrors `fetch_module_doc` which transforms docs-blob data to
ansible-doc format internally via `_transform_to_ansible_doc_format`.

```python
async def fetch_role_doc(
    self, role_name: str, version: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch role documentation from Galaxy docs-blob.

    Parses readme_html via readme_parser.parse_role_readme() and returns
    structured metadata in the same shape as extract_role_metadata().
    Returns (role_metadata, meta) where meta contains provenance fields.
    Raises GalaxyError if the role is not found in the blob.
    """
```

Returns structured role metadata + provenance dict. Transformation lives
in `galaxy.py`, keeping `server.py` thin — same pattern as modules.

### `list_collection_roles(collection_fqcn, version=None)`

Like `list_collection_modules` but filters `content_type == "role"`.

```python
async def list_collection_roles(
    self, collection_fqcn: str, version: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """List roles in a collection from the Galaxy docs-blob.

    Returns (roles, meta) where roles is {fqcn: description}.
    """
```

**Files:** `src/ansible_know/galaxy.py`, `tests/test_galaxy.py`.

---

## Server Layer Changes

### New tools

- `get_role_doc` — signature and resolution described above
- `generate_role_skill` — signature and behavior described above

### Modified tools

- `get_collection_manifest` — calls `parser.list_roles(namespace)` alongside
  `parser.search_modules()` for locally installed collections. Merges role
  metadata into the manifest.
- `search_collections` — already handled by galaxy.py changes (role_count
  field)

### MCP metadata updates

- `mcp` instructions string updated to mention roles
- CLAUDE.md tool table updated with new tools
- Tool count in server docstring updated

### Resource updates

No new resources needed — `skills://list` and `skills://{skill_name}` already
work for role skills since they use the same SKILLS_DIR.

### Prompt updates

- `find_collection` prompt updated to mention roles in the workflow

**Files:** `src/ansible_know/server.py`, `CLAUDE.md`.

---

## Doc resolution flow

```
get_role_doc("fedora.linux_system_roles.timesync")
│
├── Is namespace in _missing_collections cache?
│   ├── YES → skip local, go to Galaxy
│   └── NO → try local ansible-doc
│
├── Local: ansible-doc -t role timesync --json
│   ├── Returns structured JSON → extract_role_metadata() → done (doc_source: "local")
│   ├── Returns {} → role lacks argument_specs → fall through to Galaxy
│   └── Raises CollectionNotFoundError → add to _missing_collections → fall through
│
├── Galaxy: fetch_role_doc("fedora.linux_system_roles.timesync")
│   ├── _fetch_docs_blob() → _find_role(blob, "timesync")
│   ├── Found → return readme_html
│   │   └── parse_role_readme(html) → synthetic metadata → done (doc_source: "galaxy_readme")
│   └── Not found → raise GalaxyError
│
└── Both failed → return {"role_name": ..., "doc_source": "unavailable", "error": ...}
```

---

## Validation

Uses existing validators:
- `validate_fqcn()` — same three-segment format for roles and modules
- `validate_namespace()` — for collection namespace in manifest
- `validate_install_path()` — for generate_role_skill install_to param
- `validate_path_containment()` — for skill path security

No new validators needed.

---

## Testing Strategy

All new code follows TDD — tests written first, watched to fail, then
minimal implementation.

### Unit tests (mocked, no ansible-core needed)

**`tests/test_parser.py`** — new test classes:
- `TestListRoles` — mock `_run_ansible_doc`, verify `--list -t role` args
  passed, parse JSON output
- `TestGetRoleDoc` — mock `_run_ansible_doc`, verify `-t role` args,
  handle `{}` return
- `TestExtractRoleMetadata` — entry point extraction, options parsing,
  description extraction, multiple entry points

**`tests/test_readme_parser.py`** — new file:
- `TestParseRoleReadme` — HTML with tables, code blocks, headings
- `TestParseEmptyReadme` — empty string, minimal HTML
- `TestParseReadmeNoTables` — README without variable tables (returns
  empty variables list)
- `TestParseReadmeMultipleTables` — merge and deduplicate
- `TestParseReadmeMalformedHtml` — graceful degradation, never raises
- `TestParseReadmeCodeBlocks` — YAML detection in pre/code blocks
- `TestParseReadmeDependencies` — extract from Dependencies heading
- `TestParseReadmeSizeLimit` — input truncation at 1MB

**`tests/test_galaxy.py`** — new test classes:
- `TestFindRole` — `_find_role` static method filtering
- `TestFetchRoleDoc` — readme_html extraction from blob
- `TestListCollectionRoles` — content_type filtering, FQCN construction

**`tests/test_skills.py`** — new tests:
- Role skill template rendering
- Role template context building
- Role playbook template rendering
- Write role skill package (SKILL.md + assets, no scripts/)

**`tests/test_server.py`** — new tests:
- `get_role_doc` tool — local resolution, Galaxy fallback, graceful
  degradation, validation errors
- `get_role_doc` — cached missing collection skips ansible-doc
- `get_role_doc` — Galaxy readme parsing returns empty fields
- `get_role_doc` — role in docs-blob but readme_html is empty string
- `generate_role_skill` tool — success path, install_to param
- `get_collection_manifest` — roles section in manifest
- `get_collection_manifest` — collection with zero roles returns empty list
- `search_collections` — role_count in results

**`tests/test_collection_manifest.py`** — new tests:
- Role entries in manifest generation
- Mixed module + role manifest

### Integration tests (opt-in, `--run-integration`)

**`tests/integration/test_ansible_doc.py`** — new tests:
- `test_list_roles_returns_dict` — real `ansible-doc --list -t role`
- `test_get_role_doc_with_argument_specs` — real role with specs (if
  available)
- `test_get_role_doc_without_argument_specs` — returns `{}`

**`tests/integration/test_galaxy_api.py`** — new tests:
- `test_fetch_role_doc_returns_html` — real Galaxy docs-blob for a role
- `test_list_collection_roles` — real Galaxy role listing

### Fixtures (conftest.py)

New fixtures:

```python
SAMPLE_ROLE_DOC = {
    "fedora.linux_system_roles.gfs2": {
        "collection": "fedora.linux_system_roles",
        "entry_points": {
            "main": {
                "description": "The gfs2 role.",
                "options": {
                    "gfs2_cluster_name": {
                        "description": "The name of the cluster.",
                        "required": True,
                        "type": "str"
                    },
                    "gfs2_enable_repos": {
                        "description": "Whether to enable required repos.",
                        "required": False,
                        "type": "bool"
                    }
                }
            }
        }
    }
}

SAMPLE_ROLE_LIST = {
    "fedora.linux_system_roles.timesync": {
        "collection": "fedora.linux_system_roles",
        "description": "UNDOCUMENTED",
        "entry_points": {}
    },
    "fedora.linux_system_roles.gfs2": {
        "collection": "fedora.linux_system_roles",
        "description": "The gfs2 role.",
        "entry_points": {"main": {}}
    }
}

SAMPLE_ROLE_README_HTML = """
<h1>Timesync Role</h1>
<p>Configure time synchronization using NTP or PTP.</p>
<h2>Role Variables</h2>
<table>
<thead><tr><th>Variable</th><th>Default</th><th>Description</th></tr></thead>
<tbody>
<tr><td>timesync_ntp_servers</td><td>[]</td><td>List of NTP servers</td></tr>
</tbody>
</table>
<h2>Example Playbook</h2>
<pre><code>- hosts: all
  roles:
    - fedora.linux_system_roles.timesync
</code></pre>
<h2>Dependencies</h2>
<p>None.</p>
"""
```

---

## File Map

### New files

| File | Purpose |
|------|---------|
| `src/ansible_know/readme_parser.py` | HTML → structured role data |
| `src/ansible_know/templates/ROLE_SKILL.md.j2` | Role skill template |
| `src/ansible_know/templates/role_playbook.yml.j2` | Role example playbook |
| `tests/test_readme_parser.py` | readme_parser unit tests |

### Modified files

| File | Changes |
|------|---------|
| `src/ansible_know/parser.py` | Add `list_roles()`, `get_role_doc()`, `extract_role_metadata()` |
| `src/ansible_know/galaxy.py` | Add `_find_role()`, `fetch_role_doc()`, `list_collection_roles()`, `role_count` in `search_collections()` |
| `src/ansible_know/skills.py` | Add `render_role_skill()`, `write_role_skill_package()`, `_role_template_context()` |
| `src/ansible_know/collection_manifest.py` | Add roles to `generate_manifest()` |
| `src/ansible_know/server.py` | Add `get_role_doc` and `generate_role_skill` tools, update `get_collection_manifest`, update instructions |
| `tests/conftest.py` | Add role fixtures |
| `tests/test_parser.py` | Add role test classes |
| `tests/test_galaxy.py` | Add role test classes |
| `tests/test_skills.py` | Add role skill tests |
| `tests/test_server.py` | Add role tool tests |
| `tests/test_collection_manifest.py` | Add role manifest tests |
| `tests/integration/test_ansible_doc.py` | Add role integration tests |
| `tests/integration/test_galaxy_api.py` | Add role integration tests |
| `CLAUDE.md` | Update tool table |

---

## Not in Scope

- Role execution or testing
- Role scaffolding (handled by ansible-scaffold-role skill)
- Plugin types other than module and role (inventory, callback, filter, etc.)
- Full HTML-to-markdown conversion — we extract structured fields only
- README.md from source repos (we use Galaxy readme_html)
- Galaxy legacy roles (v1 API) — only collection roles
- Name collision handling — a collection can have a module and role with
  the same short name (e.g., `namespace.collection.timesync`). They appear
  as separate entries in the manifest (`modules` vs `roles` sections) and
  are accessed via separate tools (`get_module_doc` vs `get_role_doc`).
  The `content_type` field in responses disambiguates.

---

## Agent Workflow

After implementation, the LLM workflow for role-based collections:

```
search_collections("timesync")
  → {module_count: 27, role_count: 43, ...}

get_collection_manifest("fedora.linux_system_roles")
  → {modules: [...], roles: [{fqcn: "...timesync", has_argument_specs: false}]}

get_role_doc("fedora.linux_system_roles.timesync")
  → {role_name, doc_source: "galaxy_readme", entry_points: {main: {options: [...]}}, ...}

generate_role_skill("fedora.linux_system_roles.timesync")
  → SKILL.md content with role usage instructions
```

The same `search_collections` → explore → document → skill-generate flow
works for both module-based and role-based collections.

---

## Version

This feature targets v0.3.0.
