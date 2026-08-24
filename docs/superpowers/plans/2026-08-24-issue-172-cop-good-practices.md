# CoP Good Practices as a search_docs Source — Implementation Plan

> **For agentic workers:** Implement task-by-task in this worktree (`feat/172-cop-good-practices`). Product design is locked in `docs/superpowers/specs/2026-08-20-cop-good-practices-docs-design.md`. Do not invent a second spec.

**Goal:** Ship Red Hat CoP Automation Good Practices as `search_docs` source `cop-good-practices` (last, after `aap-2.7`) and extend `fetch_doc` with a third allowlisted host (raw GitHub `.adoc` only). No new MCP tool.

**Architecture:** Orchestration (`server.py`) dispatches `fetch_doc` by host. Domain `docs.fetch_cop_content` does a plain GET. Foundation `text_utils.clean_asciidoc` converts AsciiDoc. No `cop_docs.py` External Access module.

**Tech Stack:** Python 3.10+, httpx, FastMCP, pytest, ruff (line-length 120).

**Maps spec names → this repo:** `ansible_know` package; tools `search_docs` / `fetch_doc`; `DEFAULT_DOC_SOURCES` / `get_doc_sources()`; `RTD_PROJECT_SLUGS` / `_search_rtd_api`; `has_filters` / `core_only`; `validate_doc_url` / `ALLOWED_DOC_HOSTS`; `AnsibleKnowError`; `optional_http_client`; `_page_cache`; `estimate_tokens`; `review_playbook`.

## Global Constraints

- Source key stays `cop-good-practices`. Do not rename to `cop-best-practices`.
- Every CoP `summary` contains the exact phrase `best practices`.
- GitHub Pages URL is citation-only (description/README/CLAUDE.md), never a `fetch_doc` host.
- Do not add `source` to `has_filters`. RTD is Sphinx gap-fill only.
- No `fetch_cop` tool, no `build_github_asciidoc_manifest()`, no `cop_docs.py`.
- Hand-written 14-page v2.0 manifest. No new runtime dependencies.
- Commits/PR last line: `Assisted-by: Cursor (Grok 4.6)`.

---

## File map

| File | Change |
|------|--------|
| `src/ansible_know/validation.py` | `ALLOWED_DOC_HOSTS` + `COP_DOC_FILES` + CoP path in `validate_doc_url` |
| `src/ansible_know/text_utils.py` | `clean_asciidoc` |
| `src/ansible_know/docs.py` | `fetch_cop_content`; RTD gate in `search_docs` |
| `src/ansible_know/server.py` | Third `fetch_doc` branch; tool/instructions/`review_playbook` copy |
| `src/ansible_know/config.py` | `DEFAULT_DOC_SOURCES["cop-good-practices"]` last |
| `src/ansible_know/data/cop_good_practices_manifest.json` | New 14-entry v2.0 manifest |
| `tests/test_validation.py` | CoP URL allow/deny |
| `tests/test_text_utils.py` | Converter fixture (new; `test_docs.py` is already large) |
| `tests/test_docs.py` | Fetch mocks + CoP search + RTD gate |
| `tests/test_server.py` | Dispatch |
| `tests/test_config.py` | Source present, after `aap-2.7` |
| `tests/integration/test_cop_docs_live.py` | One live CoP fetch |
| `docs/architecture/service-contracts.md` | `fetch_cop_content`, `clean_asciidoc`, hosts; no `cop_docs.py` |
| `CLAUDE.md`, `README.md` | Source table; GitHub Pages citation only |

---

### Task 1: URL allowlist + COP_DOC_FILES

**Files:** `src/ansible_know/validation.py`, `tests/test_validation.py`

- [ ] Add `COP_DOC_FILES` (14 relative paths) and `raw.githubusercontent.com` to `ALLOWED_DOC_HOSTS`.
- [ ] `validate_doc_url`: https only; empty path rejected; CoP host requires regex `^/redhat-cop/automation-good-practices/(?P<ref>[A-Za-z0-9._-]+)/(?P<file>.+)$` and `file in COP_DOC_FILES`; reject userinfo, fragment, `..`; ignore query string.
- [ ] Error text names all three origins, e.g. `URL must start with https://docs.ansible.com/, https://docs.redhat.com/, or a CoP raw GitHub README.adoc URL.`
- [ ] Export `is_allowed_cop_raw_url(url) -> bool` for Domain redirect re-check (must not treat a redirect to docs.ansible.com as OK).
- [ ] Tests: valid intro + naming; reject other repos, `CONTRIBUTE.adoc`, GitHub Pages, blob URL, HTTP, `..`, unknown section.

### Task 2: `clean_asciidoc` (Foundation)

**Files:** `src/ansible_know/text_utils.py`, `tests/test_text_utils.py`

- [ ] Implement spec §9 (regex/line converter, reuse `_EXCESS_BLANKS_RE`). Empty → `("", "")`.
- [ ] Synthetic fixture only (title, NOTE, collapsible+`====`, `Explanations::`, `[source]` fence, `<<_anchor,label>>`). Do not copy a full CoP README.

### Task 3: Hand-written 14-page manifest

**Files:** `src/ansible_know/data/cop_good_practices_manifest.json`

- [ ] v2.0, explicit `url` per entry (raw GitHub `main`), `audience: "author"`, `core: false`, `lines`/`tokens`: 0.
- [ ] Titles + heading lists from spec §6.1. Every summary includes `best practices`. Intro includes `redhat-cop.github.io/automation-good-practices`. `structures` includes `Zen of Ansible`. Cap 1200 chars.

### Task 4: Register source last in DEFAULT_DOC_SOURCES

**Files:** `src/ansible_know/config.py`, `tests/test_config.py`

- [ ] Append `cop-good-practices` after `aap-2.7` with spec description (GitHub Pages citation only).
- [ ] Assert key present, file exists, insertion order after `aap-2.7`.

### Task 5: `fetch_cop_content` + RTD gate

**Files:** `src/ansible_know/docs.py`, `tests/test_docs.py`

- [ ] `fetch_cop_content` on `__all__`. Cache, GET `Accept: text/plain`, no RTD throttle/CF retry. Redirect must remain CoP raw URL. Non-2xx / oversized / `text/html` → `AnsibleKnowError`. `source_url` is the request URL.
- [ ] `search_docs` RTD gate (do **not** add `source` to `has_filters`):
  1. Unknown `source` → `AnsibleKnowError` listing `get_doc_sources()` keys (include `cop-good-practices`); do not call `_search_rtd_api`.
  2. `topic` / `audience` / `core_only` zero-out → no RTD (unchanged).
  3. Empty + `source in RTD_PROJECT_SLUGS` → `_search_rtd_api(query, source=source)` that slug only.
  4. Empty + known non-RTD (`aap-2.5/2.6/2.7`, `cop-good-practices`) → `[]`, do not call `_search_rtd_api`.
  5. Empty + omitted `source` → `_search_rtd_api` across the six Sphinx slugs only.
- [ ] Search tests with `source="cop-good-practices"`: naming URL; roles heading; zen → structures; `best practices` hits; intro URL. RTD: CoP miss and AAP miss must not call `_search_rtd_api`; `ansible-core` miss may; unknown source errors and must not call RTD.

### Task 6: Orchestration dispatch + copy

**Files:** `src/ansible_know/server.py`, `tests/test_server.py`

- [ ] After `validate_doc_url`: `docs.redhat.com` unchanged; `raw.githubusercontent.com` → `docs.fetch_cop_content`; else `fetch_doc_content`.
- [ ] Update `fetch_doc` / `search_docs` annotations and FastMCP instructions step 6. Tool count stays 22. Do not advertise raw GitHub as a general fetch host.
- [ ] `review_playbook`: one sentence pointing at `search_docs(..., source="cop-good-practices")` then `fetch_doc`. Not a full CoP audit.
- [ ] Tests: CoP URL calls `fetch_cop_content` not RTD/RH; invalid CoP URL returns `{"error": ...}` without HTTP.

### Task 7: Contracts + user docs + live test

**Files:** `docs/architecture/service-contracts.md`, `CLAUDE.md`, `README.md`, `tests/integration/test_cop_docs_live.py`

- [ ] Domain table: add `fetch_cop_content()`. Foundation `text_utils.py`: AsciiDoc→markdown. Hosts narrative. Do not add `cop_docs.py`.
- [ ] CLAUDE.md / README.md: compact source table from spec §10.1; GitHub Pages citation only.
- [ ] Live test: `.../main/naming_conventions/README.adoc` → title `Naming conventions`, contains `Be descriptive`. Skip only on network errors.

### Task 8: Gates

- [ ] `uv run ruff check src/ tests/`
- [ ] `uv run pytest tests/ -v`
- [ ] git-review vs `docs/architecture/service-contracts.md`
- [ ] pep8-review on every changed Python file
- [ ] git-pr: confirm #172 still open via git-closes before `Closes #172`

---

## Plan review (self, vs spec)

| Spec section | Task |
|--------------|------|
| §5 source identity, last after aap-2.7 | 4 |
| §6 14-page manifest + best-practices alias | 3 |
| §7 URL validation | 1 |
| §8 fetch_doc dispatch / fetch_cop_content | 5, 6 |
| §9 clean_asciidoc | 2 |
| §10.1 copy / routing table | 6, 7 |
| §10.5 RTD gate | 5 |
| §11 contracts | 7 |
| §12 docs | 6, 7 |
| §13 tests | 1–7 |
| Non-goals (no new tool, no cop_docs.py, no rename) | Global |

No second product spec. No stacked PRs.
