# Galaxy Collection Discovery & Remote Docs

## Problem

ansible-know currently only works with **locally installed** collections. An agent asking about `netbox.netbox.netbox_device` gets nothing unless the user has already run `ansible-galaxy collection install netbox.netbox`. With 2000+ collections on Galaxy, requiring pre-installation is a dead end for discovery.

## Goal

An agent goes from a natural language question ("manage NetBox devices") to accurate module documentation without the user installing anything. Two capabilities are missing:

1. **Collection discovery** — find the right collection by keyword (Galaxy is the registry)
2. **Remote module docs** — fetch documentation from Galaxy's API without local installation

## Context: What Already Exists

| Component | Status | What it does |
|-----------|--------|-------------|
| `search_modules(keyword, namespace)` | Shipped | Searches modules in **installed** collections |
| `get_module_doc(fqcn)` | Shipped | Full module docs for **installed** collections |
| `generate_skill(fqcn)` | Shipped | SKILL.md generation for **installed** collections |
| `ensure_collection(namespace)` | Shipped | Auto-installs collections to temp dir via `ansible-galaxy` |
| `GalaxyDocProvider` (ansibleclaw) | Exists in separate project | Fetches module docs from Galaxy API — no install needed |

The `ensure_collection` bootstrapping plan is complete. This spec adds two new capabilities on top of that foundation.

## Design

### New Tool 1: `search_collections(query, tags?)`

Discovers collections on Galaxy by keyword. Two-step API strategy:

**Step 1 — Keyword search** (finds candidates):
```
GET https://galaxy.ansible.com/api/v3/plugin/ansible/search/collection-versions/
    ?keywords={query}&is_highest=true&limit=10
    [&tags={tag}]          # optional tag filter
    [&order_by=name]       # only name/namespace/version/pulp_created supported
```

Returns per result: `namespace`, `name`, `description`, `tags[]`, `contents[]` (module list with names and types), `is_deprecated`, `is_signed`.

**Does NOT return:** download count, popularity, or relevance score.

**Step 2 — Enrich with download counts** (ranks results):
```
GET https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/{namespace}/{name}/
```

Returns: `download_count` (e.g., 12M for netbox.netbox). One call per candidate.

Only enrich the top results from Step 1 (cap at 10). Both endpoints are unauthenticated.

**Ranking:** Filter out deprecated collections, then sort by `download_count` descending. This surfaces `ansible.network` (millions of downloads) over personal projects with 50 downloads for broad queries like "network".

**Tool signature:**
```python
@mcp.tool()
async def search_collections(
    query: Annotated[str, "Search keyword (e.g., 'netbox', 'cisco ios', 'vmware')"],
    tags: Annotated[str | None, "Optional comma-separated Galaxy tags to filter (e.g., 'networking,cloud')"] = None,
) -> dict[str, Any]:
    """Search Ansible Galaxy for collections by keyword.

    Returns collections ranked by download count, with module counts
    and descriptions. Use this to discover which collection provides
    modules for a specific platform or use case.
    """
```

**Return schema:**
```json
{
  "query": "netbox",
  "count": 3,
  "collections": [
    {
      "namespace": "netbox.netbox",
      "description": "Ansible modules for NetBox",
      "tags": ["dcim", "ipam", "networking"],
      "download_count": 11999242,
      "latest_version": "3.23.0",
      "module_count": 88,
      "deprecated": false,
      "signed": false
    }
  ]
}
```

### New Tool 2: Galaxy docs-blob fallback in `get_module_doc`

When `get_module_doc(fqcn)` fails because a module isn't installed locally, fall back to Galaxy's docs-blob API instead of just returning an error.

**Galaxy docs-blob endpoint:**
```
GET /api/v3/plugin/ansible/content/published/collections/index/{ns}/{name}/versions/{version}/docs-blob/
```

Returns the complete documentation for every module in the collection — descriptions, parameters (with types, required, defaults, choices), examples, return values, author info.

**Format conversion required:** Galaxy stores module options as a list of dicts (each with a `name` key); ansible-doc stores them as a dict keyed by option name. This conversion is already implemented in ansibleclaw's `GalaxyDocProvider._transform_to_ansible_doc_format()`.

**Fallback chain for `get_module_doc`:**
1. Try local `ansible-doc --json` (current behavior)
2. If not found → try Galaxy docs-blob (new)
3. Return result with `doc_source: "galaxy"` metadata so the agent knows provenance

**Same fallback applies to:** `generate_skill`, `get_collection_manifest`, `search_modules` (when namespace is specified but collection not installed).

### Code to Port from ansibleclaw

The `GalaxyDocProvider` class in `~/Claude/ansibleclaw/src/ansibleclaw/core/galaxy.py` (~200 lines, stdlib only) provides:

- `_api_get(path)` — HTTP GET with JSON parsing, SSL context, error handling
- `_latest_version(ns, name)` — resolves latest version via versions endpoint
- `_fetch_docs_blob(ns, name, version)` — fetches the full docs-blob
- `_find_module(blob, short_name)` — extracts a single module from the blob
- `_transform_to_ansible_doc_format(fqcn, entry)` — converts Galaxy option format (list) to ansible-doc format (dict)
- `list_collection_modules(fqcn, version?)` — lists all modules in a collection from docs-blob
- `fetch_module_doc(fqcn, version?)` — full module doc in ansible-doc format

Port this into a new `src/ansible_know/galaxy.py` module, adapting:
- Replace `ansibleclaw.config.GALAXY_URL` with ansible-know's own config
- Use async-compatible HTTP (or run in executor like `ensure_collection` does)
- Add logging consistent with ansible-know's `logger` pattern

### Relationship to `ensure_collection`

`ensure_collection` (the bootstrapping plan) and Galaxy docs-blob serve **different purposes**:

| Need | Solution |
|------|----------|
| Answer "how do I use module X?" | Galaxy docs-blob (no install) |
| Review code that uses module X | Galaxy docs-blob (no install) |
| Generate a SKILL.md for module X | Galaxy docs-blob (no install) |
| **Run** a playbook that uses module X | `ensure_collection` (needs local install) |
| User explicitly asks to install a collection | `ensure_collection` |
| `search_modules` across all modules in a collection | Either — docs-blob has module list too |

The docs-blob path handles the majority of agent interactions (documentation queries). `ensure_collection` remains necessary for execution and for any case where `ansible-doc` provides richer data than the docs-blob.

## Galaxy API Reference

**Endpoints used (all unauthenticated GET):**

| Endpoint | Purpose | Returns |
|----------|---------|---------|
| `/api/v3/plugin/ansible/search/collection-versions/?keywords=X&is_highest=true` | Keyword search | namespace, name, description, tags, contents[], is_deprecated |
| `/api/v3/plugin/ansible/content/published/collections/index/{ns}/{name}/` | Collection detail | download_count, latest version, deprecated status |
| `/api/v3/plugin/ansible/content/published/collections/index/{ns}/{name}/versions/{ver}/docs-blob/` | Full module docs | Complete documentation blob for all modules in collection |
| `/api/v3/plugin/ansible/content/published/collections/index/{ns}/{name}/versions/?limit=1&ordering=-version` | Latest version | Most recent published version string |

**Valid `order_by` values for search endpoint:** `name`, `-name`, `namespace`, `-namespace`, `version`, `-version`, `pulp_created`, `-pulp_created`. No download_count or relevance ordering available on this endpoint.

**Search endpoint filters:** `keywords`, `tags` (comma-separated), `is_highest` (bool), `is_deprecated` (bool), `is_signed` (bool), `repository_name`, `namespace`, `name`, `version`.

## Agent Workflow (end to end)

```
User: "I need to create a device in NetBox"

1. search_collections("netbox")
   → [{namespace: "netbox.netbox", download_count: 12M, module_count: 88, ...}]

2. get_module_doc("netbox.netbox.netbox_device")
   → local ansible-doc fails (not installed)
   → falls back to Galaxy docs-blob
   → returns full module docs with doc_source: "galaxy"

3. Agent answers grounded in module docs, citing parameters, examples, caveats
```

If the user later needs to **run** the module:
```
4. ensure_collection("netbox.netbox")
   → installs to temp dir

5. Now ansible-doc, search_modules, etc. all work locally too
```

## New Files

| File | Action |
|------|--------|
| `src/ansible_know/galaxy.py` | Create — Galaxy API client (ported from ansibleclaw, ~200 lines) |
| `tests/test_galaxy.py` | Create — Unit tests for Galaxy client |
| `src/ansible_know/server.py` | Modify — Add `search_collections` tool, add docs-blob fallback to `get_module_doc` |
| `tests/test_server.py` | Modify — Add tests for new tool and fallback behavior |

## Build Sequence

1. Port `GalaxyDocProvider` into `src/ansible_know/galaxy.py`
2. Add `search_collections` tool to `server.py`
3. Add Galaxy docs-blob fallback to `get_module_doc` (and other tools)
4. Tests for all of the above

The collection bootstrapping plan is complete (PR #11, merged), so all prerequisite `server.py` changes are in place.
