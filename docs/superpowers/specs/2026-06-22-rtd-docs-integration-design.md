# Spec: Replace ai-docs fork with RTD-native documentation discovery and retrieval

## Context

ansible-know-mcp currently has a `search_docs` tool that fetches a custom `manifest.json`
from a fork of ansible-documentation (`leogallego/ansible-documentation:ai-docs`). That
fork runs a Python script (`build_ai_docs.py`) that converts RST to Markdown via pandoc
and generates a manifest with per-page metadata (title, summary, topic, audience, lines,
core flag).

The Ansible documentation team has rejected the custom ai-docs branch approach in favor
of using Read the Docs native capabilities:

- [ansible/ansible-documentation#3680](https://github.com/ansible/ansible-documentation/issues/3680) —
  closed by oraNod with: "Since RTD hosting provides markdown for agents I think it makes
  sense to use that tooling instead of custom stuff."
- [ansible-community/ai-forge#29](https://github.com/ansible-community/ai-forge/pull/29) —
  the endorsed approach: a skill that uses `curl -H "Accept: text/markdown"` against
  docs.ansible.com. About to be merged into the ai-forge repo.
- [ansible/ansible-documentation#3056](https://github.com/ansible/ansible-documentation/pull/3056) —
  oraNod's draft llms.txt PR (not yet merged, but signals direction).

This spec defines how to drop the fork dependency and replace it with RTD-native
endpoints, while preserving and improving discovery and retrieval.

### What the fork currently provides

The `build_ai_docs.py` script in `~/Claude/ansible-documentation` on the
`feat/ai-docs-pipeline` branch does the following:

1. Discovers RST files under `docs/docsite/rst/` (excluding top-level, index files,
   images, shared_snippets)
2. Preprocesses RST: resolves `.. include::` directives, strips Sphinx-only directives
   (`toctree`, `versionadded`, `versionchanged`, `deprecated`, `seealso`, `contents`,
   `meta`, `raw`, `only`), converts Sphinx roles (`:ref:`, `:doc:`, `:mod:`, `:func:`,
   `:ansplugin:`, `:ansopt:`, etc.) to plain text or inline code
3. Converts to GFM Markdown via `pandoc -f rst -t gfm --wrap=none`
4. Post-processes: strips `<span id="">`, `<div>` blocks, collapses blank lines
5. Generates `manifest.json` with per-file metadata:
   - `path` — relative file path
   - `topic` — parent directory name
   - `title` — first H1 heading
   - `audience` — from a hardcoded 8-entry `AUDIENCE_MAP`
   - `lines` — line count
   - `core` — from a curated `ai-docs-core.yml` list
   - `summary` — first sentence after the title

The manifest currently has ~454 files. The ai-docs branch also hosts the converted
markdown files themselves for direct fetching.

---

## Goals

1. Drop dependency on the `leogallego/ansible-documentation:ai-docs` fork entirely
2. Build per-project manifests from RTD-native sources (objects.inv for ansible-core,
   sitemap for ecosystem tools) for discovery
3. Add a `fetch_doc` tool that retrieves documentation content via RTD's markdown endpoint
4. Keep `search_docs` working with the same interface and return type
5. Expand documentation coverage to ecosystem tools (lint, navigator, builder, creator,
   molecule) as separate doc sources
6. Ship curated metadata (audience, core flags) as config in ansible-know
7. Add RTD Search API as a supplementary search backend (multi-project)

## Non-goals

- Pre-converting RST to Markdown (RTD does this at request time via Cloudflare)
- Bundling documentation content in the package (fetch on demand)
- Replacing RTD Search API with custom full-text search
- Supporting non-docs.ansible.com documentation sites in this phase (keep the
  multi-source architecture in `get_doc_sources()` for future use)
- Including collection module/plugin documentation pages in the manifest (see
  "Avoiding conflicting sources" below)

---

## RTD endpoints — verified working on docs.ansible.com (June 2026)

### 1. Markdown content negotiation (Cloudflare "Markdown for Agents")

Any docs.ansible.com URL returns clean markdown with the `Accept: text/markdown` header.
This is powered by Cloudflare's edge conversion, not RTD itself — it works on any
Cloudflare-fronted site. Zero configuration required.

```bash
curl -s -m 30 \
  "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_intro.html" \
  -H "Accept: text/markdown"
```

Verified response headers:
- `content-type: text/markdown; charset=utf-8`
- `x-markdown-tokens: 2855` — token count for the markdown output
- `x-original-tokens: 10304` — token count for the original HTML
- `content-signal: ai-train=yes, search=yes, ai-input=yes`
- `vary: accept`

Key characteristics:
- ~72% token reduction vs HTML (2,855 vs 10,304 tokens for playbooks_intro)
- ~3.6x size reduction (11,460 bytes vs 41,259 bytes)
- Includes navigation breadcrumbs/sidebar links as markdown at the top of the response
  (before the first `# ` heading) — should be stripped for clean content
- Line 1 may contain a `<!DOCTYPE html>` text artifact — strip it
- No rate limits documented
- No authentication required

**URL redirect:** `/ansible/latest/...` redirects (301) to `/projects/ansible/latest/...`.
The markdown endpoint only works on GET, not HEAD — Cloudflare returns `text/html` for
HEAD requests. This means `x-markdown-tokens` is only available after fetching the full
content, NOT via a pre-flight HEAD check. The `fetch_doc` implementation must follow
redirects and must use GET.

**Canonical base URL:** Use `https://docs.ansible.com/projects/ansible/latest` (the
redirect destination) to avoid a redirect hop on every fetch. The `/ansible/latest/`
prefix still works but adds latency.

**Difference from the fork approach:** The fork pre-converts RST with custom Sphinx
directive stripping and role conversion. The RTD markdown endpoint converts rendered HTML
to markdown, so Sphinx directives are already resolved — but the conversion quality
depends on Cloudflare's engine rather than our controlled pandoc pipeline. Tables,
complex code blocks, and nested RST constructs may convert differently. Test with a
sample of pages before relying on it.

### 2. RTD Search API

```
GET https://app.readthedocs.org/api/v3/search/?q=project:package-doc-builds/latest+<keywords>
```

**Critical details:**
- Project slug is `package-doc-builds`, NOT `ansible` — this is not obvious
- The docs-domain endpoint (`https://docs.ansible.com/_/api/v3/search/`) returns 0
  results — ONLY `app.readthedocs.org` works
- No authentication required for public projects
- Default page size: 50 results; paginated with `count`, `next`, `previous`

Returns per result:
```json
{
  "title": "Using Variables",
  "path": "/projects/ansible/latest/playbook_guide/playbooks_variables.html",
  "domain": "https://docs.ansible.com",
  "project": {"slug": "package-doc-builds", "alias": null},
  "version": {"slug": "latest"},
  "blocks": [
    {
      "type": "section",
      "id": "section-anchor",
      "title": "Section heading",
      "content": "Full section text...",
      "highlights": {"title": ["<span>match</span>"], "content": ["..."]}
    }
  ]
}
```

The `blocks` array provides section-level content with full text — useful for extracting
summaries or answering questions without fetching the full page.

### 3. Sphinx objects.inv

```
https://docs.ansible.com/projects/ansible/latest/objects.inv
```

Binary format: 4-line text header followed by zlib-compressed entries. Parse with the
`sphobjinv` Python library (BSD licensed, pure Python).

Contains 340,804 entries total:
- `std:doc`: **11,165 entries** — document page paths with display names (titles)
- `std:label`: 327,826 entries — anchors/cross-references
- `std:envvar`: 812 entries
- `std:cmdoption`: 619 entries
- `std:term`: 86 entries
- Python API entries: ~80 (modules, classes, methods, exceptions)

Each `std:doc` entry maps a document name to a URL path with a display title. Example
entry format: `name domain:type priority url display_name`.

The 11,165 `std:doc` entries are a superset of the fork's ~454 files — they include all
collection documentation pages as well, not just ansible-core guides. This is both a
benefit (more coverage) and a challenge (need filtering to avoid noise).

**Important:** `sphobjinv` is not currently a dependency of ansible-know. Add it as a
required dependency in `pyproject.toml`:

```toml
dependencies = [
    ...
    "sphobjinv>=2.3",
]
```

Making it required (not optional) eliminates conditional imports and any need for a
custom objects.inv parser. `sphobjinv` is pure Python, BSD-licensed, with no heavy
transitive dependencies — acceptable as a runtime dep even though only
`manifest_builder.py` uses it directly.

### 4. Sitemap

```
https://docs.ansible.com/ansible-sitemap.xml
```

550+ bare URLs across all 23 ecosystem projects, no metadata. Referenced from `robots.txt`.
For ansible-core, less useful than objects.inv (no titles or metadata). However, this is
the **primary discovery source for ecosystem projects** (lint, navigator, builder, etc.)
where sitemap + markdown fetch is simpler than objects.inv parsing for small page counts.
Filter by URL prefix to extract per-project page lists.

Verified project URL counts from sitemap:
- ansible: 9,998 | lint: 63 | runner: 48 | rulebook: 48 | awx-operator: 44
- galaxy-ng: 31 | molecule: 27 | awx: 26 | sign: 25 | sdk: 18
- builder: 16 | dev-tools: 12 | navigator: 8 | creator: 5

### 5. llms.txt

**Not available.** Returns 404 at:
- `https://docs.ansible.com/llms.txt`
- `https://docs.ansible.com/ansible/latest/llms.txt`
- `https://docs.ansible.com/ansible/latest/llms-full.txt`

RTD infrastructure supports it via `sphinx-llm` or `sphinx-llms-txt` extensions, but the
Ansible docs team hasn't enabled it. oraNod has a draft PR (#3056) exploring it. This is
the best long-term replacement for a curated manifest but requires upstream action.

---

## What we lose by dropping the fork, and how to replace each feature

| Feature | Fork provides | Replacement | Gap |
|---------|--------------|-------------|-----|
| Page listing with titles | manifest.json (~454 ansible-core files) | objects.inv `std:doc` (filtered ~400-500) + sitemap for ecosystem tools | None — equivalent or better coverage |
| Topic categorization | `topic` field from directory name | Parse from URL path structure | None — same derivation |
| Audience tagging | `AUDIENCE_MAP` (8 entries) | Ship same map as config in ansible-know | None — copy the 8 entries |
| Core page enrichment | `ai-docs-core.yml` (20 entries, 17 built) | Per-project `CORE_PAGES` (~35 ansible-core + ~18 ecosystem) | Improvement — more pages, all 20 previously-failing ones work via RTD |
| Summaries | First sentence extraction from converted markdown | Fetch core pages via markdown endpoint on manifest build; RTD Search API `blocks[].content` for non-core | Slightly lower quality for non-core pages |
| Line/token counts | Line count of converted markdown | `x-markdown-tokens` header + line count during manifest build for core pages | Available at discovery time for core only; at fetch time for all |
| Pre-converted markdown content | Hosted on ai-docs branch | RTD markdown endpoint (`Accept: text/markdown`) | Conversion quality differs (Cloudflare vs custom pandoc); test before relying |
| Ecosystem tool docs | Not covered at all | Separate manifests for lint, navigator, builder, creator, molecule | Improvement — entirely new coverage |
| Offline content access | Bundled core files in ansible-docs skill | Not replaced in this phase (non-goal) | True gap — ansible-docs skill concern |

---

## Avoiding conflicting sources

ansible-know already provides structured module and role documentation via two tools:

- `get_module_doc` — returns typed parameters, defaults, choices, examples, aliases from
  `ansible-doc` or Galaxy docs-blob fallback
- `get_role_doc` — returns entry points, options, dependencies, examples from
  `ansible-doc` or Galaxy README fallback

These cover **all modules and roles** in any installed collection, plus Galaxy fallback
for collections not installed locally. They return structured data (typed parameters with
defaults/choices/descriptions), not prose markdown.

The Ansible documentation site (docs.ansible.com) also hosts rendered module/plugin
documentation pages for every module in the Ansible community package. The objects.inv
`std:doc` entries include ~10,700+ collection documentation pages (out of 11,165 total).

**These MUST be excluded from the docs manifest.** Including them would mean:

1. **Duplicate sources** — an agent asking "how do I use ansible.builtin.copy" could get
   answers from `get_module_doc` (structured, authoritative) OR `search_docs` +
   `fetch_doc` (prose markdown from HTML conversion). The structured source is better for
   programmatic use; the prose version adds confusion.
2. **Inferior data** — `get_module_doc` returns typed param objects. The markdown endpoint
   returns rendered HTML-to-markdown with navigation chrome. The structured data is
   strictly more useful for code generation and review.
3. **Manifest noise** — 10,700+ collection doc entries would drown out the ~450 conceptual
   guide pages that are the manifest's actual value.

**Filtering strategy:** When building the manifest from objects.inv, include ONLY pages
whose topic (first path segment) matches the conceptual guide directories:

```python
GUIDE_TOPIC_PREFIXES = {
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
```

Exclude everything under `collections/` (module/plugin docs) and any top-level pages.
This should reduce the ~11,165 entries down to ~400-500, matching the fork's ~454 count.

**The `fetch_doc` tool itself does NOT need this restriction.** An agent should be able to
fetch any docs.ansible.com URL via `fetch_doc` if it has a specific URL — the filtering
only applies to what appears in the discovery manifest. The tool validates the URL domain,
not whether it's a "guide" page.

**Server instructions should clarify the separation:**
- `get_module_doc` / `get_role_doc` → for module and role documentation (structured)
- `search_docs` + `fetch_doc` → for conceptual guides, playbook patterns, porting guides,
  reference appendices (prose)

This keeps each tool's purpose clear and avoids agents choosing the wrong path.

---

## Architecture

### Manifest generation

Build one manifest per project and ship them with the package. A GitHub Action in the
ansible-know repo regenerates them periodically. Manifests are discovery indexes only —
content is fetched on demand via the markdown endpoint.

**ansible-core manifest** (the largest, uses objects.inv):

Inputs:
1. `objects.inv` from `https://docs.ansible.com/projects/ansible/latest/objects.inv`
2. Curated config: `AUDIENCE_MAP`, `CORE_PAGES["ansible"]`, `GUIDE_TOPIC_PREFIXES`

Process:
1. Download and parse `objects.inv` — extract all `std:doc` entries
2. Filter to guide pages only using `GUIDE_TOPIC_PREFIXES` (exclude `collections/`
   which contains module/plugin docs already served by `get_module_doc`/`get_role_doc`)
3. For each entry, derive:
   - `path`: from objects.inv entry, converted to `.html` URL path
   - `title`: from objects.inv display name
   - `topic`: first path segment (e.g. `playbook_guide`, `vault_guide`)
   - `audience`: lookup from `AUDIENCE_MAP`; default `"both"`
   - `core`: lookup from `CORE_PAGES["ansible"]` set
4. For core pages only, fetch via markdown endpoint and extract:
   - `summary`: first sentence after the H1 heading
   - `lines`: line count of the markdown
   - `tokens`: from `x-markdown-tokens` response header
5. Output `ansible_core_manifest.json`

Filtering reduces ~11,165 `std:doc` entries to ~400-500 guide pages (matching the fork's
~454 count). Validate during implementation.

**Ecosystem project manifests** (smaller, use sitemap):

Ecosystem projects (lint, navigator, builder, creator, molecule) may have objects.inv
(RTD auto-generates it for Sphinx projects), but their page counts are small enough
(5-60 pages) that the simpler sitemap + markdown-fetch approach is sufficient. Their
manifests are built from the sitemap URLs + markdown endpoint:

Process:
1. Extract project URLs from `https://docs.ansible.com/ansible-sitemap.xml`
2. Filter to the project's URL prefix
3. Fetch ALL pages via markdown endpoint to extract title, summary, lines, tokens.
   These manifests are small (5-60 pages each), so fetching every page at build time
   is feasible in CI — no need for the core/non-core enrichment split used for
   ansible-core (where only core pages are fetched). On fetch failure for a page, fall
   back to a URL-path-derived title (e.g. `configuring/` → `"Configuring"`) with empty
   summary and zero lines/tokens.
   **Note:** HEAD requests return `text/html`, not markdown — they cannot be used for
   title extraction from the markdown endpoint. Always use GET.
4. For each entry, set `core: true` if the page path is in
   `CORE_PAGES[project_key]`, else `core: false`. This uses the same lookup as
   ansible-core but every page still gets full enrichment regardless of core status.
5. Output `{project}_manifest.json`

The ansible-lint manifest would have ~60 entries, molecule ~27, etc.

**Curated config** (verified against actual source files):

```python
# From build_ai_docs.py AUDIENCE_MAP — 8 entries, rarely changes
AUDIENCE_MAP = {
    "dev_guide": "developer",
    "playbook_guide": "author",
    "inventory_guide": "author",
    "getting_started": "author",
    "getting_started_ee": "author",
    "vault_guide": "author",
    "tips_tricks": "author",
    "command_guide": "author",
}
```

Core files list — the pages worth enriching with summaries during manifest build. These
are **evergreen pages** that are useful regardless of Ansible version. Porting guides
are excluded (version-specific, go stale, RTD search covers them).

The fork's old `ai-docs-core.yml` had 20 entries but was limited to ansible-core guides
only. This revised list expands to cover **ecosystem tools** (lint, navigator, builder,
creator, molecule) which are separate RTD projects but part of the daily Ansible workflow.

```python
CORE_PAGES = {
    # --- ansible-core guides (base_url: /projects/ansible/latest) ---

    # Playbook authoring — the pages everyone needs
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
        # Inventory
        "inventory_guide/intro_inventory.html",
        "inventory_guide/intro_dynamic_inventory.html",
        "inventory_guide/intro_patterns.html",
        "inventory_guide/connection_details.html",
        # Vault
        "vault_guide/vault_encrypting_content.html",
        "vault_guide/vault_managing_passwords.html",
        "vault_guide/vault_using_encrypted_content.html",
        # Reference — the lookup tables people reach for
        "reference_appendices/config.html",
        "reference_appendices/playbooks_keywords.html",
        "reference_appendices/special_variables.html",
        "reference_appendices/general_precedence.html",
        # Collections
        "collections_guide/collections_using.html",
        "collections_guide/collections_installing.html",
        # Developer guide essentials
        "dev_guide/developing_collections.html",
        "dev_guide/developing_modules_general.html",
        "dev_guide/developing_plugins.html",
        "dev_guide/testing.html",
        "dev_guide/developing_collections_structure.html",
        # Getting started
        "getting_started/get_started_playbook.html",
        "getting_started/basic_concepts.html",
        "getting_started/get_started_inventory.html",
    ],

    # --- Ecosystem tools (separate RTD projects, different base URLs) ---

    # ansible-lint (base_url: /projects/lint)
    "lint": [
        "",                     # homepage/overview (URL: /projects/lint/)
        "configuring/",
        "rules/",
        "profiles/",
        "usage/",
    ],

    # ansible-navigator (base_url: /projects/navigator)
    "navigator": [
        "",                     # homepage/overview
        "installation/",
        "settings/",
        "subcommands/",
    ],

    # ansible-builder (base_url: /projects/builder/en/latest)
    "builder": [
        "",                     # homepage/overview
        "definition/",
        "usage/",
    ],

    # ansible-creator (base_url: /projects/creator)
    "creator": [
        "",                     # homepage/overview
        "content_creation/",
        "ee_scaffolding/",
    ],

    # molecule (base_url: /projects/molecule)
    "molecule": [
        "",                     # homepage/overview
        "getting-started-collections/",
        "configuration/",
        "usage/",
    ],
}
```

The ecosystem project base URLs differ from ansible-core — some use `/en/latest/`, others
just `/`. The manifest builder needs a per-project URL config:

```python
# Builder uses short project keys internally. These map short keys to
# canonical base URLs for manifest generation.
PROJECT_BASE_URLS = {
    "ansible": "https://docs.ansible.com/projects/ansible/latest",
    "lint": "https://docs.ansible.com/projects/lint",
    "navigator": "https://docs.ansible.com/projects/navigator",
    "builder": "https://docs.ansible.com/projects/builder/en/latest",
    "creator": "https://docs.ansible.com/projects/creator",
    "molecule": "https://docs.ansible.com/projects/molecule",
}

# Map from DEFAULT_DOC_SOURCES keys (user-facing source names) to RTD
# Search API project slugs. This is the canonical dict — used by
# _search_rtd_api in docs.py at runtime. The builder doesn't need it.
RTD_PROJECT_SLUGS = {
    "ansible-core": "package-doc-builds",
    "ansible-lint": "ansible-lint",
    "ansible-navigator": "ansible-navigator",
    "ansible-builder": "ansible-builder",
    "ansible-creator": "ansible-creator",
    "molecule": "molecule",
}
```

Note: `RTD_PROJECT_SLUGS` keys match `DEFAULT_DOC_SOURCES` keys (source names), NOT the
short builder keys in `PROJECT_BASE_URLS` / `CORE_PAGES`. The builder uses short keys
internally; `RTD_PROJECT_SLUGS` is only used at runtime by `_search_rtd_api` in `docs.py`,
where the `source` parameter comes from `search_docs(source="ansible-lint")` etc.

The `ansible-docs` skill in claude-skills-ansible has a different set of bundled core
files (18 files: includes `playbooks_vars_facts.md`, `YAMLSyntax.md`, porting guides for
2.20 and 2.21, but missing several from the canonical list). This is a separate concern —
the ansible-docs skill will be updated to use `fetch_doc` in a later PR.

The core files concept changes meaning in this new architecture: in the fork, "core"
meant "bundled locally for zero-latency access". With on-demand fetching via `fetch_doc`,
no files are bundled. "Core" now only means two things:
1. **Enriched in the manifest** — at manifest build time, core pages are fetched via the
   markdown endpoint to extract summaries, line counts, and token counts. Non-core pages
   only get title and topic from objects.inv.
2. **Filterable via `core_only` parameter** — the existing `search_docs` tool already
   supports a `core_only: bool` parameter that filters to core entries only.

The list size is no longer constrained by bundling/context limits. However, each entry
means one HTTP fetch at manifest build time (in CI). The current list has ~35 ansible-core
pages + ~18 ecosystem pages = ~53 fetches total, which completes in under a minute in CI.

**Output format** — one manifest per project, each matching the format `search_docs`
already consumes. Each project is a separate source in `get_doc_sources()`, so the
existing `source` filter parameter works unchanged.

**Manifest version handling:** The `"version"` field uses semver-major for breaking
changes. The loader (`_get_manifest`) should check `version.startswith("2.")` and log a
warning (not error) if the major version is higher than expected. This provides forward
compatibility — a manifest from a newer builder still loads, the warning alerts that
some fields may be unrecognized. The current fork manifest has no version field; the
loader should treat missing version as `"1.0"` (legacy format).

Example manifest for ansible-core (`ansible_core_manifest.json`):

```json
{
  "version": "2.0",
  "generated": "2026-06-22T12:00:00Z",
  "base_url": "https://docs.ansible.com/projects/ansible/latest",
  "files": [
    {
      "path": "playbook_guide/playbooks_intro.html",
      "topic": "playbook_guide",
      "title": "Ansible playbooks",
      "audience": "author",
      "core": true,
      "summary": "Playbooks are automation blueprints...",
      "lines": 123,
      "tokens": 2855
    }
  ]
}
```

Example manifest for ansible-lint (`ansible_lint_manifest.json`):

```json
{
  "version": "2.0",
  "generated": "2026-06-22T12:00:00Z",
  "base_url": "https://docs.ansible.com/projects/lint",
  "files": [
    {
      "path": "configuring/",
      "topic": "configuration",
      "title": "Configuring ansible-lint",
      "audience": "author",
      "core": true,
      "summary": "Configure ansible-lint using a .ansible-lint file...",
      "lines": 95,
      "tokens": 800
    }
  ]
}
```

Each manifest has its own `base_url` matching the project's canonical URL pattern. The
`url` field in `search_docs` results is constructed the same way as today (line 59 of
`docs.py`: `base_url + "/" + path`).

**Important:** Use `/projects/ansible/latest` for ansible-core, NOT `/ansible/latest`.
The latter redirects (301) to the former. Using the canonical URL avoids a redirect hop
on every `fetch_doc` call. Each ecosystem project has its own URL pattern — see
`PROJECT_BASE_URLS` above.

**Separate sources enable:**
- `search_docs(query="rules", source="ansible-lint")` — scoped search
- Adding/removing projects without affecting others
- Users adding custom doc sources via `ANSIBLE_KNOW_DOC_SOURCES` env var
- Independent refresh schedules per project in CI
- The `docs://sources` resource becomes a useful registry of available docs

**`DEFAULT_DOC_SOURCES` in `config.py`:**

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

Note the key change from `"url"` to `"file"` — `_fetch_manifest` in `docs.py` needs to
support loading from a local file path, not just HTTP URLs. The env var override
(`ANSIBLE_KNOW_DOC_SOURCES`) should support both `"url"` and `"file"` keys so users can
point at remote or local manifests.

**`data/` directory setup:** The `src/ansible_know/data/` directory does not exist yet.
Create it and ensure shipped manifests are included in the wheel:

1. Create `src/ansible_know/data/` directory
2. Hatch already includes the entire `src/ansible_know` package tree (see
   `[tool.hatch.build.targets.wheel] packages = ["src/ansible_know"]`), so `.json`
   files under `data/` are included automatically — no `pyproject.toml` change needed
   for inclusion. Verify with `hatch build && unzip -l dist/*.whl | grep data/` after
   generating the first manifest.

### Manifest refresh strategy

**Recommended: Ship in package + optional runtime refresh.**

- GitHub Action runs weekly (or on release), regenerates all manifests, opens a PR with
  updated files at `src/ansible_know/data/*.json` (PR-based, not direct commit to main —
  provides a review checkpoint if manifests change dramatically due to upstream
  restructuring)
- `docs.py` loads shipped manifests as the default sources (file paths, not URLs)
- Cache loaded manifests in `BoundedCache` (already used by `docs.py`) with TTL
- If network is unavailable, shipped manifests work offline

This avoids the current approach of fetching the manifest from a remote URL on every
cache miss (line 38 of `docs.py`), while still allowing freshness via periodic CI
rebuilds. Runtime refresh from objects.inv is possible but adds complexity — start
without it and add later if staleness becomes a problem.

### Changes to search_docs

Current `search_docs` in `docs.py` (lines 65-137):
- Iterates over sources from `get_doc_sources()`
- For each source, calls `_fetch_manifest(source_name, url)` to get/cache entries
- Filters by topic, audience, core_only
- Matches query against `title`, `summary`, `topics` fields (substring match)
- Returns up to `SEARCH_DOCS_LIMIT` (20) results as `list[SearchDocsEntry]`

The `SearchDocsEntry` TypedDict (from `types.py`) has:
```python
class SearchDocsEntry(TypedDict):
    title: str
    summary: str
    topic: list[str]
    audience: list[str]
    lines: int
    source: str
    url: str
```

**Note on `tokens` field:** Manifests include a `tokens` field for core pages (from
`x-markdown-tokens`). This is NOT surfaced in `SearchDocsEntry` — it's manifest-only
metadata used during skill generation and manifest building. Agents get token info when
they call `fetch_doc` (returned in `FetchDocResult.tokens`). Adding it to search results
would clutter the output for most use cases. If needed later, extend the TypedDict.

Changes needed:

1. **Split `_fetch_manifest` into two loaders.** The current function creates a throwaway
   `httpx.AsyncClient` per call. With shipped manifests, the common path is `json.load()`
   — no HTTP at all. Refactor into:
   - `_load_manifest_file(source_name: str, file_path: str) -> list[dict]` — synchronous
     JSON load from a local file (wrapped in `run_in_executor` if needed, but JSON load
     of ~500 entries is fast enough to call directly). On `FileNotFoundError`, log a
     warning and return empty list — this handles the case where not all manifests exist
     yet (Phase 1a ships only ansible-core initially) or a bad package install.
   - `_fetch_manifest_url(source_name: str, url: str, http_client: ...) -> list[dict]` —
     async HTTP fetch (the current behavior, for user-provided URL overrides)
   - `_get_manifest(source_name: str, src_config: dict, http_client: ...) -> list[dict]`
     — dispatches based on `"file"` vs `"url"` key in config

   Both paths cache to `_manifest_cache` identically. The post-processing (adding
   `_source`, constructing URLs from `base_url + path`) stays shared.

2. Update `DEFAULT_DOC_SOURCES` in `config.py` to point at the shipped manifest files
3. Keep the URL-based loading path for backward compatibility (users can still override
   via `ANSIBLE_KNOW_DOC_SOURCES` env var with either `"file"` or `"url"` keys)
4. **Thread `http_client` through `search_docs`.** Change the signature to accept an
   optional `http_client: httpx.AsyncClient | None = None` parameter. The server.py
   tool function passes the lifespan client via `_get_http_client(ctx)`. This is needed
   for both URL-based manifest loading and the RTD Search API fallback. Callers passing
   `None` get the old behavior (short-lived client created internally).
5. Add RTD Search API fallback (see below)

### New tool: fetch_doc

Add a new MCP tool to `server.py` that retrieves documentation content from
docs.ansible.com via the markdown endpoint.

**Tool signature:**

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
```

**Return type** — new TypedDict in `types.py`:

```python
class FetchDocResult(TypedDict):
    content: str       # cleaned markdown content
    title: str         # extracted from first H1
    tokens: int        # from x-markdown-tokens header
    source_url: str    # the fetched URL
```

**Behavior:**
1. Validate URL starts with `https://docs.ansible.com/`
2. Use the lifespan `http_client` from context (same pattern as other tools — see
   `_get_http_client(ctx)` in server.py line 211) rather than creating a new
   `httpx.AsyncClient`
3. Send GET with `Accept: text/markdown` header, 30-second timeout, follow redirects.
   **Important:** httpx defaults to `follow_redirects=False`. The lifespan client
   (server.py line 119) does NOT set `follow_redirects=True`. Either:
   (a) add `follow_redirects=True` to the lifespan client constructor, or
   (b) pass it per-request: `await client.get(url, follow_redirects=True)`.
   Option (b) is safer — it avoids changing behavior for other tools that use the
   same client. The `/ansible/latest/` → `/projects/ansible/latest/` redirect is a
   301 and httpx will not follow it without this flag.
4. Check `content-type` header — if not `text/markdown`, return error
5. Parse `x-markdown-tokens` header for token count (this header is only available on
   GET responses, NOT HEAD — Cloudflare only converts on GET)
6. If `max_tokens` is set and token count exceeds it, return error with the token count
   (so the agent can decide what to do). Note: the content is already fetched at this
   point — this is a post-fetch check, not a pre-fetch optimization. An alternative is
   to use streaming and check the header before reading the body, but that adds
   complexity for marginal benefit.
7. Clean the content via `_clean_rtd_markdown(raw: str) -> tuple[str, str]` in `docs.py`
   (returns `(cleaned_content, title)`). Lives in `docs.py` alongside the search and RTD
   code — not in `server.py`. The `fetch_doc` tool in `server.py` delegates to a
   `fetch_doc_content()` function in `docs.py` that handles the HTTP call, cleaning, and
   result construction. This function needs its own unit tests covering edge cases.
   Cleaning steps:
   - Strip any leading `<!DOCTYPE html>` text artifact (always on line 1 when present,
     but match anywhere in the first 5 lines to be safe)
   - Find the first `# ` heading line (ATX H1). Everything before it is navigation
     breadcrumbs/sidebar — strip it.
   - If no `# ` heading exists (possible for some ecosystem tool pages), keep all
     content and set title to empty string. The caller can fall back to the URL path
     for a title.
   - Extract the title text from the first `# ` heading (strip the `# ` prefix and
     any trailing `{#anchor}` markup)
   - Collapse 3+ consecutive blank lines into 2
8. Return `FetchDocResult`

**MCP tool description:**

```
Fetch a page from docs.ansible.com as clean Markdown.

Returns documentation content ready for LLM consumption.
Use search_docs to discover relevant page URLs, or pass a known
docs.ansible.com URL directly. The url parameter must start with
https://docs.ansible.com/.
```

**Important implementation notes:**
- Reuse the existing lifespan `http_client` — do NOT create a new `httpx.AsyncClient`
  per call. The server already manages a shared client in `app_lifespan` (server.py
  line 119) with `timeout=httpx.Timeout(10.0, read=120.0)`. For `fetch_doc`, override
  the timeout per-request to 30 seconds since doc pages can be large.
- When `ctx` is `None` (tests, direct calls), `_get_http_client(ctx)` returns `None`.
  The `fetch_doc_content()` function in `docs.py` must handle this by creating a
  short-lived `httpx.AsyncClient` internally, same pattern as `_search_rtd_api`.
- Add input validation in `validation.py` — a `validate_doc_url(url)` function that
  checks the URL starts with `https://docs.ansible.com/` and is a valid URL format.
- Use `truncate_response()` (from `validation.py`) on the content before returning, same
  as `get_skill` does (server.py line 812), to prevent oversized responses.

### RTD Search API integration

Add as a fallback in `search_docs` when manifest-based search returns zero results.
Search across all known RTD project slugs.

**Implementation in `docs.py`:**

```python
import asyncio

from ansible_know.config import RTD_PROJECT_SLUGS

RTD_SEARCH_URL = "https://app.readthedocs.org/api/v3/search/"
RTD_DOCS_DOMAIN = "https://docs.ansible.com"

# RTD_PROJECT_SLUGS is imported from config.py — single source of truth.
# Keys are source names ("ansible-core", "ansible-lint", ...) matching
# DEFAULT_DOC_SOURCES. Values are RTD project slugs.

async def _search_rtd_api(
    query: str,
    source: str | None = None,
    limit: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search RTD API as fallback when manifest search returns empty.

    If source is specified and maps to a known slug, search only that project.
    Otherwise search all known projects.
    """
    slugs_to_search: list[tuple[str, str]] = []
    if source and source in RTD_PROJECT_SLUGS:
        slugs_to_search = [(source, RTD_PROJECT_SLUGS[source])]
    else:
        slugs_to_search = list(RTD_PROJECT_SLUGS.items())

    results: list[dict[str, Any]] = []
    client = http_client or httpx.AsyncClient(timeout=10.0)

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

        hits = []
        for hit in data.get("results", []):
            blocks = hit.get("blocks", [])
            summary = ""
            if blocks:
                raw = blocks[0].get("content", "")
                dot = raw.find(". ")
                summary = (raw[:dot + 1] if dot > 0 else raw[:120]).strip()

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
        # Search all projects in parallel (5s timeout per project)
        all_hits = await asyncio.gather(
            *[_search_one(name, slug) for name, slug in slugs_to_search],
            return_exceptions=True,
        )
        for hits in all_hits:
            if isinstance(hits, list):
                results.extend(hits)
    finally:
        if http_client is None:
            await client.aclose()

    return results[:limit]
```

**Integration point:** In `search_docs()` (line 133 of `docs.py`), after the manifest
search loop, if `results` is empty, call `_search_rtd_api(query, source=source)` and
extend results. Pass the `source` parameter through so scoped searches stay scoped.

Note: `_search_rtd_api` receives the `http_client` parameter threaded through from
`search_docs()`, which in turn receives it from `server.py` via `_get_http_client(ctx)`.
When `http_client` is `None` (e.g. direct calls in tests), `_search_rtd_api` creates a
short-lived client internally.

### Server instructions update

The MCP server `instructions` string (server.py line 143) is what appears in the agent's
system prompt. Update it to mention `fetch_doc` and the recommended workflow:

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

---

## Implementation plan

### Phase 1a: Curated config + local file loading in docs.py

This sub-phase makes `search_docs` work with shipped manifest files and can be tested
with a hand-crafted manifest before the builder exists.

1. Ship curated config in `config.py`:
   - `AUDIENCE_MAP` — 8 entries (verified above)
   - `CORE_PAGES` — per-project core page lists (see above)
   - `GUIDE_TOPIC_PREFIXES` — set of topic directory names for objects.inv filtering
   - `PROJECT_BASE_URLS` — canonical base URL per project
   - `RTD_PROJECT_SLUGS` — RTD search API slug per project
2. Create `src/ansible_know/data/` directory
3. Refactor `_fetch_manifest` in `docs.py` into `_load_manifest_file()`,
   `_fetch_manifest_url()`, and `_get_manifest()` dispatcher (see "Changes to
   search_docs" section above)
4. Update `search_docs` signature in `docs.py` to accept optional
   `http_client: httpx.AsyncClient | None = None` parameter. Update `server.py`'s
   `search_docs` tool function to pass `http_client=_get_http_client(ctx)` through
   to `docs.search_docs()` (requires adding `ctx: Context | None = None` to the tool
   function signature if not already present)
5. Update `DEFAULT_DOC_SOURCES` in `config.py` to the multi-source layout with
   `"file"` keys pointing at `src/ansible_know/data/*.json`
6. Create a hand-crafted test manifest with ~10 entries to validate the file loading
   path and verify `search_docs` still works. Ship this as the initial
   `ansible_core_manifest.json` — it will be overwritten by the builder in Phase 1b.
7. Update existing tests in `test_docs.py` to cover file-based loading

### Phase 1b: Manifest builder + CI

1. Add `sphobjinv>=2.3` as a required dependency in `pyproject.toml`. Pure Python,
   BSD-licensed, no heavy transitive deps. Used by `manifest_builder.py` to parse
   objects.inv for ansible-core manifest; ecosystem projects use sitemap instead.
2. Create `manifest_builder.py`:
   - `fetch_objects_inv(url: str) -> list[dict]` — download, decompress, parse, return
     `std:doc` entries
   - `filter_guide_pages(entries, topic_prefixes) -> list[dict]` — keep only conceptual
     guide pages, exclude collection module/plugin docs
   - `fetch_sitemap_urls(sitemap_url, project_prefix) -> list[str]` — extract project
     URLs from sitemap XML
   - `build_manifest(entries, audience_map, core_pages, base_url) -> dict` — produce
     manifest dict
   - `enrich_pages(manifest, urls_to_enrich) -> dict` — fetch pages via markdown
     endpoint, extract summaries, line counts, token counts
   - `build_ecosystem_manifest(sitemap_url, project, base_url, core_pages) -> dict` —
     build a manifest for an ecosystem project from sitemap + markdown fetches
3. Create `scripts/build_docs_manifests.py` CLI entry point that builds all manifests
   and writes them to `src/ansible_know/data/`:
   - `ansible_core_manifest.json` (from objects.inv, ~400-500 entries)
   - `ansible_lint_manifest.json` (from sitemap, ~60 entries)
   - `ansible_navigator_manifest.json` (from sitemap, ~8 entries)
   - `ansible_builder_manifest.json` (from sitemap, ~16 entries)
   - `ansible_creator_manifest.json` (from sitemap, ~5 entries)
   - `molecule_manifest.json` (from sitemap, ~27 entries)
4. Add GitHub Action: weekly schedule, runs the build script, opens a PR with updated
   manifests (PR-based, not direct commit — review checkpoint for upstream changes)

### Phase 1c: Generate real manifests and verify

1. Run `scripts/build_docs_manifests.py` to generate all manifests
2. Verify objects.inv `std:doc` filtered entries produce ~400-500 guide pages (matching
   the fork's ~454 count)
3. Verify `search_docs` returns equivalent results for ansible-core queries AND now
   returns results for ecosystem tool queries
4. Commit generated manifests to the repo

### Phase 2: fetch_doc tool

1. Add `FetchDocResult` TypedDict to `types.py`
2. Add `validate_doc_url()` to `validation.py`
3. Add `_clean_rtd_markdown(raw: str) -> tuple[str, str]` to `docs.py` with unit tests
   in `test_docs.py` covering: normal page with H1, page with `<!DOCTYPE html>` artifact,
   page with no H1, page with `{#anchor}` in heading, excessive blank lines
4. Add `fetch_doc_content()` async function to `docs.py` — handles HTTP fetch with
   `Accept: text/markdown`, redirect following, content-type check, token extraction,
   cleaning via `_clean_rtd_markdown`, and `truncate_response()`. Accepts optional
   `http_client` (creates short-lived client when `None`)
5. Add thin `fetch_doc` tool to `server.py` that validates URL, extracts lifespan
   client, and delegates to `docs.fetch_doc_content()`
6. Test with a representative sample of docs.ansible.com pages (integration tests):
   - Verify markdown quality (tables, code blocks, nested lists)
   - Verify breadcrumb stripping works
   - Verify `x-markdown-tokens` header is present
   - Test with non-existent pages (should return clean error)
7. Register tool with proper description and `readOnlyHint=True`

### Phase 3: RTD Search API fallback

1. Add `_search_rtd_api()` to `docs.py` with:
   - `RTD_SEARCH_URL` constant
   - Import `RTD_PROJECT_SLUGS` from `config.py` (single source of truth)
   - `import asyncio` for `asyncio.gather()`
2. Add fallback call in `search_docs()` when manifest search returns empty
3. Pass `source` parameter through so scoped searches stay scoped to one project
4. Results from RTD search should be appended after manifest results, not replace them
5. Deduplicate by URL: before extending with RTD results, collect `seen_urls = {r["url"]
   for r in results}` from manifest hits, then filter RTD results with
   `[r for r in rtd_results if r["url"] not in seen_urls]`

### Phase 4: Server instructions and docs://sources update

1. Update `instructions` string in `mcp = FastMCP(...)` (server.py line 142)
2. Update `docs://sources` resource to show: source name, type (`"file"` or `"url"`),
   description, and file path or URL per source. This makes the resource a useful
   registry for agents to understand available documentation coverage.
3. Update CHANGELOG
4. Update tool count in `server.py` module docstring (currently "13 tools")

---

## Testing considerations

- **Markdown endpoint quality — ansible-core:** Fetch these pages and verify usable:
  - `playbook_guide/playbooks_intro.html` — basic prose
  - `reference_appendices/playbooks_keywords.html` — large tables
  - `dev_guide/developing_modules_general.html` — code blocks and nested lists
  - `porting_guides/porting_guide_core_2.19.html` — inline code and directives
  - A collection module page (e.g. `collections/ansible/builtin/copy_module.html`) —
    verify it works even though filtered from manifest
- **Markdown endpoint quality — ecosystem:** Verify on at least one page per project:
  - `https://docs.ansible.com/projects/lint/rules/` — ansible-lint rule index
  - `https://docs.ansible.com/projects/navigator/settings/` — navigator config
  - `https://docs.ansible.com/projects/builder/en/latest/definition/` — EE definition
  - `https://docs.ansible.com/projects/molecule/configuration/` — molecule config
- **objects.inv filtering:** Count `std:doc` entries after filtering by
  `GUIDE_TOPIC_PREFIXES` — should be ~400-500, matching the fork's ~454
- **RTD Search API — multi-project:** Test with queries across projects:
  - `project:package-doc-builds "role defaults variables"` — ansible-core results
  - `project:ansible-lint "no-changed-when"` — lint rule results
  - `project:ansible-navigator "settings"` — navigator results
  - `project:molecule "scenario"` — molecule results
  - `"nonexistent_feature_xyz"` — empty across all projects
- **search_docs integration:** Verify:
  - `search_docs(query="loops")` — returns ansible-core playbook guide hits
  - `search_docs(query="rules", source="ansible-lint")` — returns lint hits only
  - `search_docs(query="xyznonexistent")` — falls back to RTD search, returns empty
  - `search_docs(query="settings", source="ansible-navigator")` — scoped to navigator

---

## Migration checklist

- [ ] Verify objects.inv `std:doc` filtered entries cover the fork's ~454 guide pages
- [ ] Verify all `CORE_PAGES["ansible"]` URLs return valid markdown via RTD endpoint
- [ ] Verify all ecosystem project core page URLs return valid markdown
- [ ] Verify `GUIDE_TOPIC_PREFIXES` filtering produces ~400-500 entries (not 11K)
- [ ] Test markdown endpoint quality on sample pages (see testing section)
- [ ] Test RTD Search API with representative queries across multiple project slugs
- [ ] Remove fork URL from `config.py` `DEFAULT_DOC_SOURCES` (line 24-29)
- [ ] Ship all manifests in package (`src/ansible_know/data/*.json`)
- [ ] Verify `search_docs` returns results for both ansible-core and ecosystem queries
- [ ] Verify `search_docs(source="ansible-lint")` scoping works
- [ ] Verify `docs://sources` resource lists all new sources
- [ ] Update `ansible-docs` skill in claude-skills-ansible to use `fetch_doc` instead of
      bundled core files (separate PR, after ansible-know releases with `fetch_doc`)

---

## Reference: current code in ansible-know

| File | What to change |
|------|---------------|
| `src/ansible_know/config.py` | `DEFAULT_DOC_SOURCES` (line 24) — change from fork URL to local file paths (one per project). Add `AUDIENCE_MAP`, `CORE_PAGES`, `GUIDE_TOPIC_PREFIXES`, `PROJECT_BASE_URLS`, `RTD_PROJECT_SLUGS`. |
| `src/ansible_know/docs.py` | Replace `_fetch_manifest()` (line 32) with `_load_manifest_file()`, `_fetch_manifest_url()`, and `_get_manifest()` dispatcher. Add optional `http_client` param to `search_docs()` (line 65). Add `_search_rtd_api()` with parallel project search via `asyncio.gather()` (requires `import asyncio`). Add `_clean_rtd_markdown()` and `fetch_doc_content()` for the `fetch_doc` tool. Import `RTD_PROJECT_SLUGS` from `config.py`. |
| `src/ansible_know/server.py` | Add thin `fetch_doc` tool (~line 427, after `search_docs`) delegating to `docs.fetch_doc_content()`. Update `instructions` string (line 143). Pass `http_client=_get_http_client(ctx)` to `docs.search_docs()`. |
| `src/ansible_know/types.py` | Add `FetchDocResult` TypedDict. |
| `src/ansible_know/validation.py` | Add `validate_doc_url()`. |
| `src/ansible_know/cache.py` | No changes — `BoundedCache` already used by docs.py. |
| `pyproject.toml` | Add `sphobjinv>=2.3` and `defusedxml>=0.7` to required dependencies. |
| NEW: `src/ansible_know/manifest_builder.py` | objects.inv parser, sitemap parser, manifest generator. |
| NEW: `src/ansible_know/data/ansible_core_manifest.json` | Shipped ansible-core manifest. |
| NEW: `src/ansible_know/data/ansible_lint_manifest.json` | Shipped ansible-lint manifest. |
| NEW: `src/ansible_know/data/ansible_navigator_manifest.json` | Shipped ansible-navigator manifest. |
| NEW: `src/ansible_know/data/ansible_builder_manifest.json` | Shipped ansible-builder manifest. |
| NEW: `src/ansible_know/data/ansible_creator_manifest.json` | Shipped ansible-creator manifest. |
| NEW: `src/ansible_know/data/molecule_manifest.json` | Shipped molecule manifest. |
| NEW: `scripts/build_docs_manifests.py` | CLI for CI manifest generation (all projects). |
| NEW: `.github/workflows/update-docs-manifests.yml` | Weekly manifest rebuild. |

## Reference: related upstream issues and PRs

- [ansible/ansible-documentation#3680](https://github.com/ansible/ansible-documentation/issues/3680) —
  AI-optimized docs build proposal (closed: "use RTD markdown instead of custom stuff")
- [ansible/ansible-documentation#3056](https://github.com/ansible/ansible-documentation/pull/3056) —
  oraNod's draft llms.txt PR (not merged, signals future direction)
- [ansible-community/ai-forge#29](https://github.com/ansible-community/ai-forge/pull/29) —
  endorsed `Accept: text/markdown` skill for Lola (about to merge)
- [ansible/vscode-ansible#2798](https://github.com/ansible/vscode-ansible/issues/2798) —
  feat(mcp): add module discovery and documentation tools via ansible-doc
- [ansible/vscode-ansible#2797](https://github.com/ansible/vscode-ansible/issues/2797) —
  discussion: consider standalone repository for ansible-mcp-server

## Reference: the ansible-docs skill (this repo)

The `ansible-docs` skill at `ansible-docs/skills/ansible-docs/SKILL.md` in
claude-skills-ansible currently:
- Loads a `manifest.json` from the plugin's root directory
- Scores manifest entries by keyword relevance
- Loads core files from a bundled `core/` directory (18 markdown files)
- Fetches non-core files from the fork's ai-docs branch via `WebFetch`
- Has a 5,000-line context budget

After ansible-know ships `fetch_doc`, this skill can be simplified to:
1. Use `search_docs` (MCP tool) for discovery
2. Use `fetch_doc` (MCP tool) for content retrieval
3. Remove bundled core files and manifest.json
4. Keep the skill's Q&A and code review response modes

This is a separate PR in the claude-skills-ansible repo and should happen after
ansible-know releases with the new tools.
