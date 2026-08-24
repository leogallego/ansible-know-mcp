# Spec: CoP Good Practices as a Doc Source

**Status:** Proposed
**Date:** 2026-08-20
**Issue:** [#172](https://github.com/leogallego/ansible-know-mcp/issues/172)
**Repo:** ansible-know-mcp (`leogallego/ansible-know-mcp`)
**Audience:** Implementation session in this repo only

> **For agentic workers:** Use superpowers:writing-plans to create an
> implementation plan from this spec. Do not implement from this document
> alone.

Supersedes the July recommendation in
`docs/research/federated-doc-sources-2026-07-01.md` (Approach B+ /
`fetch_cop`). That write-up assumed `fetch_doc` was RTD-only. Since #177,
`fetch_doc` already dispatches `docs.ansible.com` vs `docs.redhat.com`.
This spec extends that dispatcher; it does **not** add a new MCP tool.

---

## 1. Problem

Agents using `search_docs` / `fetch_doc` cannot discover or retrieve Red Hat
Communities of Practice (CoP) Automation Good Practices. That content is
AsciiDoc on GitHub (`redhat-cop/automation-good-practices`): no Sphinx
sitemap, no `objects.inv`, no `Accept: text/markdown`.

A **review** path already exists outside this server: the
`ansible-good-practices` skill (`claude-ansible-skills`, v2.2.1) bundles all
13 section `.adoc` files and falls back to

```text
https://raw.githubusercontent.com/redhat-cop/automation-good-practices/{ref}/{section}/README.adoc
```

That skill is an auditor (load rules, check files). It does not make CoP
searchable through this MCP server’s documentation tools. #172 is still
open; there is no CoP source in `DEFAULT_DOC_SOURCES`, no AsciiDoc cleaner,
and `ALLOWED_DOC_HOSTS` is only `docs.ansible.com` and `docs.redhat.com`.

---

## 2. Goals

1. Ship CoP as a first-class `search_docs` source named `cop-good-practices`.
2. Retrieve a CoP page through **existing** `fetch_doc` when the URL is an
   allowlisted raw GitHub CoP `.adoc` file.
3. Convert the CoP AsciiDoc subset to markdown suitable for LLM consumption.
4. Reuse the skill’s **contract** (13 section directories, URL template,
   parse notes) and **extend search** beyond the skill: index the root intro
   page, and put guideline headings in each summary so filtered search can
   hit buried rules. Do not import the skill plugin or ship `.adoc` snapshots.
5. Keep the tool surface unchanged (still `search_docs` + `fetch_doc`). Make
   **which source to pass** obvious in tool copy so CoP does not compete with
   ansible-core / AAP in unfiltered results.
6. Keep the upstream name **good practices** (GPA). Do **not** rename the
   source key or titles to “best practices”. Add **best practices** as a
   search alias so `search_docs("best practices", source="cop-good-practices")`
   matches (every query word must appear in title+summary+topic).

---

## 3. Non-goals

| Follow-up | Why deferred |
|-----------|----------------|
| `fetch_cop` MCP tool | `fetch_doc` is already the multi-host dispatcher |
| General `ANSIBLE_KNOW_DOC_SOURCES` fetch for arbitrary domains | CoP prefix allowlist only; no user-defined fetch hosts |
| Full Asciidoctor / `asciidoc` CLI | CoP subset is small; no new runtime dependency |
| CoP review / lint tool | Lives in the `ansible-good-practices` skill |
| GitHub Pages HTML fetch (`redhat-cop.github.io`) | Single concatenated HTML book; 403s for some clients; cite in description only |
| `CONTRIBUTE.adoc` | CoP-author process, not Ansible content practices; easy follow-up |
| Nested example YAML/inventory dirs | Samples pulled in via `include::`; not guideline pages |
| `_style/render.adoc` | Asciidoctor chrome |
| One manifest entry per `==` / `===` guideline (~80 rows) | Same fetch URL repeated; headings go in the section **summary** instead |
| `github.com` blob/tree URLs | Fetch only `raw.githubusercontent.com` |
| Bundling `.adoc` files in the wheel | MCP already fetches docs over the network; live `main` is fresher than the skill snapshot |
| Weekly CI refresh of CoP content | Manifest is 14 static entries; regenerate when pages are added |
| Changing the skill plugin | Different repo; keep the split (skill = review, MCP = discovery) |
| Interleaving / re-ranking `search_docs` across sources | Out of scope; `source=` is the reliable CoP path (see §10) |
| Renaming GPA / the source key to `cop-best-practices` | CoP explicitly chose *good* not *best*; alias search terms instead (§6) |

Also out of scope:

- Allowlisting all of `raw.githubusercontent.com`
- Parameterizing `content_type` / `allowed_domains` on every doc source
- Wiring CoP into `generate_skill` templates
- Fetching or inlining `include::` example files
- New MCP tools (`fetch_cop`, `search_cop`, review tools)
- New ADR (existing `search_docs` / `fetch_doc` keep-list in ADR-0006 covers this)

---

## 4. Architecture

Third host on the existing `fetch_doc` branch in `server.py`. Search stays
source-agnostic (manifest JSON). Conversion is Foundation; HTTP GET is
Domain (same layer as RTD `fetch_doc_content`). No new External Access
module — CoP is a plain GET of `text/plain`, not an MCP session.

```text
search_docs(query, source="cop-good-practices")   # required for reliable CoP hits
  → docs.search_docs
      → DEFAULT_DOC_SOURCES["cop-good-practices"]
      → data/cop_good_practices_manifest.json
      → SearchDocsEntry.url = allowlisted raw.githubusercontent.com .adoc

fetch_doc(url)
  → validate_doc_url(url)          # Foundation: host + CoP path prefix
  → server.py dispatch:
       docs.redhat.com            → redhat_docs.fetch_redhat_doc
       docs.ansible.com           → docs.fetch_doc_content
       raw.githubusercontent.com  → docs.fetch_cop_content
                                      → GET raw AsciiDoc
                                      → text_utils.clean_asciidoc
                                      → FetchDocResult
```

**Skill relationship (not a runtime dependency):**

| Concern | Skill (`ansible-good-practices`) | This MCP change |
|---------|----------------------------------|-----------------|
| Job | Review / audit Ansible files | Discover + fetch CoP while authoring |
| Content | Bundled `references/*.adoc` (13 sections) | Live GET of those 13 paths **plus** root `README.adoc` |
| Conversion | Agent reads AsciiDoc in-place | `clean_asciidoc()` → markdown |
| Network | Optional GitHub fallback | Required for `fetch_doc` |

Copy these from `claude-skills-ansible/scripts/update-cop-references.sh`:
section list and URL template. Do not add that repo as a build dependency.

---

## 5. Source identity

### 5.1 `DEFAULT_DOC_SOURCES` entry

In `config.py`:

```python
"cop-good-practices": {
    "file": str(_PKG_DIR / "data" / "cop_good_practices_manifest.json"),
    "description": (
        "Red Hat CoP Automation Good Practices (GPA; people often search "
        "'best practices'): role design, naming, inventories, CaC, testing, "
        "Git workflow — not ansible-core HOWTO and not AAP product manuals. "
        "Published site: https://redhat-cop.github.io/automation-good-practices/ "
        "(citation only; fetch uses raw GitHub AsciiDoc)."
    ),
},
```

Append this entry **last** in `DEFAULT_DOC_SOURCES` (after `aap-2.7`). Search
scans sources in dict order and stops at 20 hits, so CoP must not sit in
front of ansible-core. See §10.

`docs://sources` already iterates `get_doc_sources()` and will list this
source with `type: "file"`. No resource API change.

### 5.2 Indexed pages (frozen, 14)

The CoP repo has no further practice chapters beyond the skill’s 13
directories. MCP still indexes **one extra page** the skill omitted for
context: the root intro.

Define once as `COP_DOC_FILES: frozenset[str]` in `validation.py`
(relative paths after `/{ref}/`). Tests import that constant. Do not
duplicate the list in `config.py` or `docs.py`.

```text
README.adoc                         # topic: introduction
aap_configuration/README.adoc
cicd_and_promotion/README.adoc
coding_style/README.adoc
collections/README.adoc
git_workflow/README.adoc
inventories/README.adoc
naming_conventions/README.adoc
playbooks/README.adoc
plugins/README.adoc
roles/README.adoc
security/README.adoc
structures/README.adoc
testing/README.adoc
```

The 13 directories match
`claude-skills-ansible/scripts/update-cop-references.sh`. Root
`README.adoc` is MCP-only (intro, four-part map, Zen of Ansible, published
URL). It `include::`s the 13 sections; `clean_asciidoc` strips includes, so
fetching the intro does **not** dump the whole book.

### 5.3 Fetch URL shape

Canonical URLs (what search returns and `fetch_doc` accepts):

```text
https://raw.githubusercontent.com/redhat-cop/automation-good-practices/{ref}/README.adoc
https://raw.githubusercontent.com/redhat-cop/automation-good-practices/{ref}/{section}/README.adoc
```

- `{ref}` for shipped search results is always `main`.
- Path after `/{ref}/` must be exactly one of `COP_DOC_FILES`.
- No query string required; if present, ignore it for matching. Reject
  userinfo, fragments, and `..`.

Human site `https://redhat-cop.github.io/automation-good-practices/` is
**citation only** — put it in the source description (`docs://sources`,
README, CLAUDE.md). It is **not** a `fetch_doc` URL (one HTML book, not
per-section pages; `/naming_conventions/` 404s).

---

## 6. Manifest (v2.0)

Ship `src/ansible_know/data/cop_good_practices_manifest.json`.

`docs._postprocess_entries()` already keeps an explicit `url` when present
and only builds `base_url + path` when `url` is missing. CoP entries **must**
set `url` to the raw GitHub fetch URL so search results are fetchable.

```json
{
  "version": "2.0",
  "generated": "<ISO-8601 UTC at write time>",
  "base_url": "https://raw.githubusercontent.com/redhat-cop/automation-good-practices/main",
  "files": [
    {
      "path": "naming_conventions/README.adoc",
      "url": "https://raw.githubusercontent.com/redhat-cop/automation-good-practices/main/naming_conventions/README.adoc",
      "topic": "naming_conventions",
      "title": "Naming conventions",
      "audience": "author",
      "core": false,
      "summary": "<lead paragraph>. Guidelines: <heading>; <heading>; ...",
      "lines": 0,
      "tokens": 0
    }
  ]
}
```

Rules:

- One file object per indexed page (**14 total**). Do not add one row per
  guideline heading.
- `topic` is `introduction` for the root file, otherwise the section
  directory name (string is fine; `_postprocess_entries` wraps it).
- `audience` is `"author"` (same as most ecosystem manifests).
- `core` is `false` for every CoP page.
- `title` is the AsciiDoc document title (`= ...` on line 1). Use §6.1.
- `summary` is: (1) the lead paragraph after the title, `NOTE:` / xrefs
  stripped to plain text, then (2) a `Guidelines:` clause listing every
  `==` heading, semicolon-separated. For `roles/README.adoc` only, list
  `===` headings instead (`==` is just “Role design considerations” /
  “References”). Cap the whole summary at **1200 characters** (search
  returns summaries inline; do not ship 2k-char blobs). If a heading list
  would exceed the cap, drop generic headings first and **keep** distinctive
  ones (e.g. roles must still include “Don't use host group names”).
- Extra search hooks (not extra pages; not a rename):
  - **Every** CoP `summary` MUST contain the exact phrase `best practices`
    (users type that more than “good practices”; matcher requires every
    query word). Keep official titles as *good practices* / GPA.
  - **structures** summary must also include `Zen of Ansible`.
  - Intro summary must also include
    `redhat-cop.github.io/automation-good-practices`.
- `search_docs` matches title + summary + topic, **not** the page body.
  Heading lists are how queries like “Don't use host group names” or
  “trunk-based development” hit CoP. `best practices` hits via the alias
  phrase, not by renaming the source.
- `lines` / `tokens` may be `0`. Do not fetch pages at package build time
  just to fill those fields.

Hand-write the JSON. Do **not** add `build_github_asciidoc_manifest()` or a
CI job in this change. If CoP adds a page later, update `COP_DOC_FILES`,
the JSON, and tests in a follow-up.

### 6.1 Titles and guideline headings

Lead titles from current CoP / skill snapshots. Heading lists are required
in that page’s `summary` (see §6 rules). Skip `== References`.

| `path` | Title | Headings in summary |
|--------|--------|---------------------|
| `README.adoc` | Good Practices for Ansible - GPA | Introduction; Where to get and maintain this document |
| `structures/README.adoc` | Automation structures | Guiding principles for Automation Good Practices; Define which structure to use for which purpose |
| `naming_conventions/README.adoc` | Naming conventions | Be descriptive, consistent, and concise in all names; Use standard naming patterns for repositories; Name playbook files with verb-noun pattern; Follow standard Git branch and commit naming conventions; Name AAP resources using lowercase with underscores and organization prefixes |
| `roles/README.adoc` | Roles Good Practices for Ansible | Basic design; Role Structure; Role Distribution; Naming parameters; Providers; Distributions and Versions; Package roles in an Ansible collection to simplify distribution and consumption; Check Mode; Idempotency; Supporting multiple distributions and versions; Platform specific variables; Platform specific tasks; Supporting multiple providers; Generating files from templates; Vars vs Defaults; Documentation conventions; Create a meaningful README file for every role; Don't use host group names or at least make them a parameter; Prefix task names in sub-tasks files of roles; Argument Validation |
| `collections/README.adoc` | Collections good practices | Collection Structure should be at the type or landscape level; Create implicit collection variables and reference them in your roles' defaults variables; Include a README file in each collection; Include a license file in a collection root directory |
| `playbooks/README.adoc` | Playbooks good practices | Keep your playbooks as simple as possible; Use either the tasks or roles section in playbooks, not both; Use tags cautiously either for roles or for complete purposes; Use the verbosity parameter with debug statements |
| `inventories/README.adoc` | Inventories and Variables Good Practices for Ansible | Identify your Single Source(s) of Truth and use it/them in your inventory; Differentiate clearly between "As-Is" and "To-Be" information; Define your inventory as structured directory instead of single file; Rely on your inventory to loop over hosts, don't create lists of hosts; Restrict your usage of variable types; Prefer inventory variables over extra vars to describe the desired state |
| `plugins/README.adoc` | Plugins good practices | Python Guidelines; Write documentation for all plugin types; Use sphinx (reST) formatted docstrings in Python code; Use Python type hints to document variable types; The use of unittest is discouraged, use pytest instead; Formatting of manually maintained plugin argspecs; Keep plugin entry files to a minimal size; Plugins should be initially developed using the ansible plugin builder; Use clear error/info messages |
| `coding_style/README.adoc` | Coding Style Good Practices for Ansible | Naming things; YAML and Jinja2 Syntax; Ansible Guidelines; Wrap longer lines of code |
| `aap_configuration/README.adoc` | AAP Configuration as Code | Manage all AAP configuration declaratively using Git and the infra.aap_configuration collection; Use the dispatch role for simplified and ordered configuration application; Structure CaC repositories with environment-specific group_vars; Organize AAP variable files by coupling: shared resources in type files, tightly coupled resources in JT bundles; Handle secrets in CaC using vault or environment variable lookups; Always pin the infra.aap_configuration collection to an exact version; Test CaC playbooks for idempotency and use check mode before applying to production; Apply CaC through CI/CD pipelines, not manually |
| `git_workflow/README.adoc` | Git workflow and versioning | Use trunk-based development with a single main branch; Keep feature branches short-lived; Choose a versioning strategy that fits your release cadence; Use immutable Git tags for releases; Promote the same tag across all environments; Never use "latest" or branch names in production references; Synchronize versions across all components; Protect the main branch with required reviews and CI checks; Document breaking changes prominently |
| `testing/README.adoc` | Testing | Test as early as possible in the development cycle (shift-left); Use ansible-lint with the production profile; Use Molecule to test roles in isolated container environments; Test on multiple platforms when roles support them; Write unit tests for custom modules, plugins, and filters; Always verify idempotency during testing; Use descriptive test names that explain the expected behavior |
| `cicd_and_promotion/README.adoc` | CI/CD and promotion | Use pre-commit hooks to enforce quality before code enters the repository; Run the same checks in CI that run in pre-commit; Design CI pipelines for fast feedback; Separate testing (CI) from building and releasing (CD); Implement quality gates at each promotion stage; Validate PR quality with automated checks; Use release manifests to track what is deployed where; Always have a documented rollback plan |
| `security/README.adoc` | Security | Never store secrets in Git repositories; Enforce secret detection with pre-commit hooks and CI scanning; Enable push protection on your Git hosting platform; Apply least-privilege access control; Use AAP credential objects instead of embedding credentials in playbooks; Implement regular secret rotation; Scan container images and generate SBOMs for every release; Use Git as the audit trail for all configuration changes; Implement network segmentation between environments; Conduct regular security reviews and access audits |

---

## 7. URL validation

Extend `validate_doc_url()` in `validation.py`. Keep a single function;
callers already go through it before `fetch_doc` dispatch.

### 7.1 Hosts

```python
ALLOWED_DOC_HOSTS = frozenset({
    "docs.ansible.com",
    "docs.redhat.com",
    "raw.githubusercontent.com",
})
```

`https` only. Empty path / `/` still rejected for all hosts.

### 7.2 CoP path prefix (host-specific)

When `netloc == "raw.githubusercontent.com"`, require:

```text
^/redhat-cop/automation-good-practices/(?P<ref>[A-Za-z0-9._-]+)/(?P<file>.+)$
```

Then `file` must be in `COP_DOC_FILES` (exact match, including
`README.adoc` for the intro). The ref token is `[A-Za-z0-9._-]+` so `main`
and tags like `1.2.3` work; slashes in refs are rejected (no `feature/foo`).

Reject:

- Any other GitHub org/repo (`/ansible/ansible/...`)
- `CONTRIBUTE.adoc`, `_style/...`, example dirs, `LICENSE`
- `redhat-cop.github.io` (wrong host)
- `github.com`, `githubusercontent.com`, `raw.github.com`
- HTTP, credentials, fragments
- Paths containing `..`

### 7.3 Error copy

The `ValidationError` message must name all three allowed origins, e.g.
`URL must start with https://docs.ansible.com/, https://docs.redhat.com/, or a CoP raw GitHub README.adoc URL.`

Do not mention `raw.githubusercontent.com` as a general-purpose fetch host
in tool descriptions — say CoP good-practices GitHub raw URLs.

---

## 8. `fetch_doc` dispatch

### 8.1 Orchestration (`server.py`)

After `validate_doc_url(url)`:

| `netloc` | Call |
|----------|------|
| `docs.redhat.com` | `redhat_docs.fetch_redhat_doc(...)` (unchanged) |
| `raw.githubusercontent.com` | `docs.fetch_cop_content(...)` |
| else (`docs.ansible.com`) | `docs.fetch_doc_content(...)` (unchanged) |

Update the `fetch_doc` parameter annotation, docstring, and server
instructions so they mention CoP **raw GitHub URLs from search_docs hits**,
not `raw.githubusercontent.com` as a general fetch host. Tool count stays
at 22.

Update `search_docs` `source` annotation and docstring with the §10.1 table
(compact). FastMCP `instructions` workflow step 6 becomes: search_docs for
conceptual guides **with the matching source** (ansible-core HOWTO,
ansible-lint rules, aap-2.x product, cop-good-practices for CoP), then
fetch_doc.

This is the same Orchestration dispatch pattern introduced for AAP (#177).
Do not move Red Hat routing in this change.

### 8.2 Domain (`docs.fetch_cop_content`)

New public function; add to `docs.__all__`.

```python
async def fetch_cop_content(
    url: str,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchDocResult:
```

Behavior:

1. `_page_cache.get(url)` — same 24h page cache as RTD. Honor `max_tokens`
   against cached `tokens` the same way `fetch_doc_content` does.
2. GET via `optional_http_client` (shared client or owned 30s timeout).
   Header: `Accept: text/plain`. `follow_redirects=True`.
3. After response: final URL host must remain `raw.githubusercontent.com`
   and the path must still pass the CoP regex. Otherwise
   `AnsibleKnowError("Redirect to unexpected domain: ...")`.
4. Status not 2xx → `AnsibleKnowError` with sanitized message (GitHub 404
   for a bad section/ref).
5. Body larger than `MAX_DOC_FETCH_SIZE` (2MB, already in `docs.py`) →
   `AnsibleKnowError`.
6. Content-type, if present, must be `text/plain` or start with `text/`.
   Reject `text/html` (GitHub error pages). Missing content-type is allowed.
7. Decode UTF-8 (`resp.text`). `clean_asciidoc(raw)` → `(content, title)`.
8. `tokens = estimate_tokens(content)` (no `x-markdown-tokens` header).
9. If `max_tokens` is set and `tokens > max_tokens`, raise `AnsibleKnowError`
   with the same wording style as `fetch_doc_content`.
10. Build `FetchDocResult`: `content`, `title`, `tokens`, `source_url=url`
    (the request URL, not a post-redirect URL, so it matches the manifest).
11. `_page_cache.put(url, result)` and return.

Do **not** use `_throttle_doc_request`, RTD Embed fallback, or Cloudflare
retry logic. Those are docs.ansible.com-specific.

Do not add a `cop_docs.py` External Access module.

---

## 9. `clean_asciidoc` (Foundation)

Add to `text_utils.py` and `__all__`:

```python
def clean_asciidoc(raw: str) -> tuple[str, str]:
    """Convert the CoP AsciiDoc subset to markdown.

    Returns (cleaned_markdown, title). Title is empty if no document
    title (``= ...``) is found.
    """
```

Regex/line converter only. No `asciidoc` package. Empty input returns
`("", "")`. Collapse 3+ blank lines to 2 (reuse `_EXCESS_BLANKS_RE`).

This matches the skill’s “AsciiDoc parsing notes”, not a general Asciidoctor.

### 9.1 Convert

| AsciiDoc | Markdown |
|----------|----------|
| `= Title` (document title, first match) | `# Title`; also the returned `title` |
| `== Heading` | `## Heading` |
| `=== Heading` | `### Heading` |
| `==== Heading` used as a real heading (not a collapsible delimiter — see §9.2) | `#### Heading` |
| `Explanations::` / `Rationale::` / `Examples::` at line start | `**Explanations**` / `**Rationale**` / `**Examples**` |
| `NOTE:`, `TIP:`, `CAUTION:`, `WARNING:`, `IMPORTANT:` (line start, optional space) | `> **Note:**` (and Tip / Caution / Warning / Important) plus the rest of the line |
| `[source,yaml]` / `[source,json]` / `[source]` immediately followed by `----` fence | Opening ` ```yaml ` / ` ```json ` / ` ``` `; closing `----` → ` ``` ` |
| `link:URL[label]` | `[label](URL)` |
| `<<id,label>>` | `label` |
| `<<id>>` (no label) | drop the xref (empty string) |
| `` `code` `` | leave as-is |
| `*bold*` / `_italic_` | leave as-is (LLMs tolerate AsciiDoc emphasis) |

### 9.2 Strip

Drop entire lines (do not keep a blank placeholder beyond normal newline
collapse):

- `[%collapsible]`
- `include::...`
- `image::...`
- A line that is exactly `+` (AsciiDoc list continuation)
- `ifdef::...` / `ifndef::...` / `endif::...`

Collapsible **delimiters**: after `[%collapsible]`, CoP wraps the body in a
line that is exactly `====`. Drop those delimiter-only `====` lines. Do not
treat delimiter `====` as a heading.

### 9.3 Do not implement

- Nested includes, attributes (`:toc:`), tables, callouts, `ifdef` content
  gating (directives stripped, inner lines kept)
- Converting `* ` lists (already markdown-compatible enough)
- Fetching or inlining `include::` targets. Guideline **prose** stays; YAML
  samples living in sibling dirs (`roles/dont_use_groups/`, inventory
  examples) are omitted. That is acceptable: the rule text is in the
  README; agents that need a sample still see the Examples:: blocks that
  are inline.

### 9.4 Fixture

Unit tests must use a **minimal synthetic snippet** that contains: document
title, one `NOTE:`, one `[%collapsible]` + `====` pair, `Explanations::`,
`[source]` code fence, and a `<<_anchor,label>>` xref. Do not copy a full
CoP `README.adoc` into the test tree.

---

## 10. Agent routing, search, and efficiency

No new MCP tools. CoP is another **source** on the existing pair
`search_docs` → `fetch_doc`.

### 10.1 When to search which source

`search_docs` already takes `source`. That is the routing mechanism. Put
this table in the `search_docs` docstring, the FastMCP `instructions`
string (workflow step 6), CLAUDE.md, and README. Keep it short.

| Need | `source=` | Then |
|------|-----------|------|
| Official HOWTO (playbooks, vault, inventory syntax, module usage concepts) | `ansible-core` (or omit to mix official docs first) | `fetch_doc` on docs.ansible.com |
| ansible-lint rule / profile | `ansible-lint` | `fetch_doc` |
| Navigator / builder / creator / molecule | matching source name | `fetch_doc` |
| AAP product (install, mesh, RBAC, AI features) | `aap-2.5` / `aap-2.6` / `aap-2.7` | `fetch_doc` on docs.redhat.com |
| **CoP opinionated practices** (role design, naming, CaC, Git, testing *process*; users often say **best practices**) | **`cop-good-practices`** (do not invent `cop-best-practices`) | `fetch_doc` on the raw GitHub URL from the hit |
| Unsure which corpus | omit `source` | Official + AAP fills the 20-hit window first; CoP may be absent — if the question is CoP-shaped, retry with `source="cop-good-practices"` |

CoP vs the **skill**: MCP search/fetch is for *reading a practice while
authoring*. Auditing a tree against all rules stays in
`ansible-good-practices`. `review_playbook` may mention CoP with
`source="cop-good-practices"`; it must not imply a full CoP audit.

Module parameter questions stay on `get_module_doc` / `search_modules`,
never CoP.

### 10.2 Why unfiltered search is not the CoP path

`search_docs` walks `DEFAULT_DOC_SOURCES` in insertion order and **stops at
20 hits** (`SEARCH_DOCS_LIMIT`). Matching is substring (`all(word in
title+summary+topic)`), not ranked relevance.

Consequences this spec **does not try to fix**:

- ansible-core (first) often fills the window alone.
- CoP is **last**, so unfiltered queries usually never reach it. That is
  intentional: heading-rich CoP summaries contain common words (`roles`,
  `testing`, `security`, `ansible`) and would otherwise crowd official docs.
- Do **not** interleave sources, boost CoP, or expand the 20-hit cap in
  this PR.

Reliable CoP discovery is always `source="cop-good-practices"`. Acceptance
tests must pass that parameter (they already do).

### 10.3 Fetch cost

`fetch_doc` returns the **whole section** (roles is ~34KB / ~8k tokens),
not one guideline. That is cheaper and simpler than 80 fetch URLs. Agents
that only needed one heading still get the sibling rules in the same page,
which is useful. Callers may pass `max_tokens` to refuse oversized pages.

Reuse `_page_cache` (24h). Do not throttle CoP like docs.ansible.com.

### 10.4 Search payload

Returned `summary` is capped at 1200 characters (§6). Heading lists exist so
a **filtered** CoP search can match “Don't use host group names” without
indexing bodies. They are not a reason to dump 14×2k-char strings into
mixed search.

Hand-written headings can drift from live AsciiDoc. Fetch is live; search
metadata is a snapshot. Acceptable for v1; do not add a builder in this PR.

### 10.5 RTD fallback (backend-aware)

RTD Search API is a **Sphinx / docs.ansible.com gap-fill**, not a global
empty-search handler. That was correct when every `search_docs` source was
an RTD project. It is wrong now that sources include AAP (`docs.redhat.com`)
and CoP (raw GitHub). `source=` means “which corpus,” not “which RTD slug.”

Do **not** add `source` to the existing `has_filters` (topic / audience /
`core_only`) and suppress RTD for every scoped search. That would regress
`source="ansible-core"` (and lint / navigator / builder / creator /
molecule): a manifest miss must still query that project’s RTD slug.

Replace the gate in `docs.search_docs` (`src/ansible_know/docs.py`) with:

1. If `source` is set and is **not** a key in `get_doc_sources()` → raise
   (or return the same error shape the tool already uses) listing valid
   keys, including `cop-good-practices`. Do **not** call `_search_rtd_api`.
2. If `topic`, `audience`, or `core_only` narrowed the corpus to zero → do
   **not** call RTD (unchanged).
3. If manifests returned nothing and `source` is in `RTD_PROJECT_SLUGS` →
   call `_search_rtd_api(query, source=source)` for **that slug only**
   (keep ansible-core / lint gap-fill).
4. If manifests returned nothing and `source` is a **known non-RTD**
   corpus (`aap-2.5` / `aap-2.6` / `aap-2.7`, `cop-good-practices`) →
   return `[]`. Do **not** call `_search_rtd_api`.
5. If manifests returned nothing and `source` is omitted → call
   `_search_rtd_api` across the six Sphinx slugs only (current unfiltered
   fallback). Never treat AAP or CoP as RTD projects.

`_search_rtd_api` already returns `[]` when `source` is set and not in
`RTD_PROJECT_SLUGS`. That silent no-op is not enough: `search_docs` must
not invoke RTD for non-RTD corpora, and must not treat an unknown `source`
as “empty, try RTD.”

This does **not** put CoP into unfiltered 20-hit results. CoP discovery
still requires `source="cop-good-practices"` (§10.2).

---

## 11. Layer / contract updates

Update `docs/architecture/service-contracts.md` in the same PR:

- Layer 2 Domain table: `docs` functions include `fetch_cop_content()`.
- Layer 4 `text_utils.py` purpose: add AsciiDoc→markdown (`clean_asciidoc`).
- `ALLOWED_DOC_HOSTS` narrative wherever doc URL hosts are listed (validation
  / fetch_doc). Do not add `cop_docs.py` to External Access.

`server.py` calling `docs.fetch_cop_content` is allowed Domain use, same as
`docs.fetch_doc_content`. No new exception ID.

ADR-0006: no change. `search_docs` / `fetch_doc` remain the knowledge-retrieval
tools to keep.

---

## 12. Docs and copy

Update in the same PR:

- `CLAUDE.md` — `fetch_doc` / `search_docs` rows plus the §10.1 source
  table (or a two-line version of it). GitHub Pages is citation only.
- `README.md` — same source table; CoP in the doc-sources list; `fetch_doc`
  URL sentence; published site link for humans.
- `fetch_doc` tool description (annotation + docstring).
- `search_docs` `source` parameter text: examples must include
  `cop-good-practices`, `ansible-core`, and `aap-2.6`. Note that
  “best practices” / GPA questions use `source="cop-good-practices"`.
- FastMCP `instructions` workflow step 6: source-qualified search_docs
  (see §8.1).
- `review_playbook` prompt: one sentence — CoP rules via
  `search_docs(..., source="cop-good-practices")` then `fetch_doc`. Do not
  turn that prompt into a full CoP audit (the skill owns that).

Issue #172 closes when this lands (`Closes #172`).

---

## 13. Testing

### 13.1 Unit (no network)

| Area | File | Cases |
|------|------|--------|
| URL allow/deny | `tests/test_validation.py` | Valid intro `.../main/README.adoc` and section `.../main/naming_conventions/README.adoc`; reject other GitHub repos, `CONTRIBUTE.adoc`, GitHub Pages HTML, `github.com` blob, HTTP, path `..`, unknown section |
| Converter | `tests/test_docs.py` or `tests/test_text_utils.py` (new file only if `test_docs.py` is already awkward) | Fixture in §9.4: title extracted; collapsible markers gone; `Explanations` bold; fence becomes markdown; xref label kept; empty input |
| Fetch | `tests/test_docs.py` | Mock httpx: happy path → `FetchDocResult`; cache hit skips GET; 404 → `AnsibleKnowError`; HTML content-type → error; redirect off prefix → error; `max_tokens` exceeded |
| Dispatch | `tests/test_server.py` | `fetch_doc` with a CoP URL calls `fetch_cop_content`, not RTD/RH; invalid CoP URL returns `{"error": ...}` without HTTP |
| Config | `tests/test_config.py` | `DEFAULT_DOC_SOURCES` contains `cop-good-practices` with a `file` that exists; key order has it after `aap-2.7` |
| Search | `tests/test_docs.py` | With `source="cop-good-practices"`: a guideline heading in `summary` matches that page; `best practices` returns at least one CoP hit (alias present on every summary); returned `url` is the explicit raw URL; intro `README.adoc` is fetchable. Unfiltered search of a core HOWTO query is not required to include CoP. RTD gate (§10.5): CoP miss and AAP miss must **not** call `_search_rtd_api`; `source="ansible-core"` miss with no other filters **may** call it with that source; unknown `source` errors and must not call RTD. |

Do not call real GitHub in unit tests.

### 13.2 Integration (`--run-integration`)

One test: `fetch_cop_content` (or `fetch_doc`) for
`.../main/naming_conventions/README.adoc` returns non-empty markdown whose
title is `Naming conventions` and which contains `Be descriptive` (a stable
`==` heading). Skip/xfail only on network errors, not on converter misses.

---

## 14. Security

- Fetch is read-only GET.
- Allowlist is **prefix + `COP_DOC_FILES`**, not the GitHub raw host.
- Redirects re-validated.
- Size cap 2MB; CoP sections are tens of KB.
- `sanitize_error` on exception paths (no filesystem leaks; GitHub URLs in
  errors are acceptable as they are the user-supplied URL).
- No credentials. Do not send Galaxy/RTD tokens to GitHub.

---

## 15. Acceptance

1. `search_docs("naming conventions", source="cop-good-practices")` returns
   the naming section with a `raw.githubusercontent.com` URL.
2. `search_docs("Don't use host group names", source="cop-good-practices")`
   returns the **roles** page (heading lives in that summary, not the title).
3. `search_docs("zen of ansible", source="cop-good-practices")` returns
   `structures`.
4. `search_docs("best practices", source="cop-good-practices")` returns CoP
   pages (not empty). Source key stays `cop-good-practices`; there is no
   `cop-best-practices` source.
5. `fetch_doc` on a CoP raw URL returns markdown (not AsciiDoc
   `[%collapsible]`, not HTML).
6. `fetch_doc` on `https://raw.githubusercontent.com/octocat/Hello-World/master/README`
   or `https://redhat-cop.github.io/automation-good-practices/` returns a
   validation error.
7. `fetch_doc` on existing ansible.com / redhat.com URLs is unchanged
   (existing unit tests still pass).
8. No new MCP tool. `docs://sources` lists `cop-good-practices`. `search_docs`
   docstring / `source` annotation names `cop-good-practices` next to
   `ansible-core` and `aap-2.6`.
9. Unfiltered `search_docs("playbook vault")` still returns official docs
   in the 20-hit window (CoP last; must not crowd ansible-core). Covered by
   existing unit tests plus one assertion that default source order lists
   `cop-good-practices` after `aap-2.7`.
10. `ruff check src/ tests/` and `pytest tests/ -v` pass.
11. `search_docs("no-such-guideline-xyz", source="cop-good-practices")`
    returns empty and does **not** call the RTD Search API. Same for
    `source="aap-2.7"` on a miss. `source="ansible-core"` on a manifest
    miss may still fall back to that project’s RTD slug.
12. `search_docs("anything", source="not-a-real-source")` returns an error
    that lists valid source keys (including `cop-good-practices`), not RTD
    hits.

---

## 16. File map

| File | Change |
|------|--------|
| `src/ansible_know/validation.py` | `ALLOWED_DOC_HOSTS`, `COP_DOC_FILES`, CoP path check in `validate_doc_url` |
| `src/ansible_know/text_utils.py` | `clean_asciidoc` |
| `src/ansible_know/docs.py` | `fetch_cop_content`; backend-aware RTD gate in `search_docs` (§10.5) |
| `src/ansible_know/server.py` | Third `fetch_doc` branch; copy; `review_playbook` sentence |
| `src/ansible_know/config.py` | `DEFAULT_DOC_SOURCES` entry |
| `src/ansible_know/data/cop_good_practices_manifest.json` | New 14-entry v2.0 manifest (intro + 13 sections, heading-rich summaries) |
| `tests/test_validation.py` | CoP URL cases |
| `tests/test_docs.py` (and/or `test_text_utils.py`) | Cleaner + fetch mocks + search |
| `tests/test_server.py` | Dispatch |
| `tests/test_config.py` | Source present |
| `tests/integration/` | One live CoP fetch |
| `docs/architecture/service-contracts.md` | `fetch_cop_content`, `clean_asciidoc` |
| `CLAUDE.md`, `README.md` | User-facing source/fetch copy |

No new runtime dependencies. No `manifest_builder.py` change in this PR.

---

## 17. Open questions (resolved in this spec)

| Question | Decision |
|----------|----------|
| New tool vs generalize `fetch_doc` | Generalize dispatch only; no `fetch_cop` |
| URL vs section name on fetch | URL, same as every other `fetch_doc` call |
| Ship `.adoc` vs live fetch | Live fetch; manifest is search metadata only |
| GitHub Pages vs raw GitHub | Cite Pages in description; fetch raw `.adoc` |
| Index beyond the skill’s 13 sections | Yes: root intro. No: CONTRIBUTE, examples, `_style` |
| One search row per guideline | No; put `==` / `===` headings in each page summary (1200-char cap) |
| Unfiltered search must surface CoP | No; require `source="cop-good-practices"`; CoP last in `DEFAULT_DOC_SOURCES` |
| Interleave / re-rank search_docs | Out of scope |
| User-extensible fetch hosts | Out of scope |
| Manifest builder / weekly CI | Out of scope; hand-written JSON |
| Rename source/titles to “best practices” | No. Keep GPA / `cop-good-practices`. Alias `best practices` in every summary |
| `source=` always suppresses RTD | No. RTD only for `RTD_PROJECT_SLUGS` (ansible-core gap-fill). AAP/CoP miss → `[]`. Unknown source → error. |

---

## References

- Issue: https://github.com/leogallego/ansible-know-mcp/issues/172
- CoP repo: https://github.com/redhat-cop/automation-good-practices
- Skill contract: `claude-skills-ansible/scripts/update-cop-references.sh`
  and `ansible-good-practices` v2.2.1 `SKILL.md` AsciiDoc notes
- Prior research (superseded): `docs/research/federated-doc-sources-2026-07-01.md`
- Related shipped work: #177 AAP docs (`docs.redhat.com` `fetch_doc` branch)
