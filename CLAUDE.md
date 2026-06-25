# CLAUDE.md

## Project

Ansible Know MCP Server — module and role discovery, documentation search, and skill generation for AI agents via the Model Context Protocol.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Runtime requirement: `ansible-core` must be installed in the same environment (for `ansible-doc`).

## Architecture

```
src/ansible_know/
├── server.py              # FastMCP server: 17 tools, 6 resources, 5 prompts (entrypoint)
├── parser.py              # ansible-doc wrapper — module discovery and metadata extraction
├── resolution.py          # local-then-Galaxy doc resolution + multi-server search
├── readme_parser.py       # Parse Galaxy role README HTML into structured data
├── skills.py              # skill rendering + package writing (Jinja2)
├── async_utils.py         # run_in_executor — blocking-to-async bridge (Foundation)
├── config.py              # paths, constants, doc source registry
├── collection_manifest.py # collection-level MANIFEST.json generation/caching
├── docs.py                # multi-manifest documentation client (httpx)
├── text_utils.py          # RTD markdown cleaning (Foundation)
├── galaxy.py              # Galaxy v3 API client — search, docs-blob, format conversion
├── tagging.py             # Tag derivation from module metadata (Foundation)
├── manifest_builder.py    # Build-time: generate doc manifests from objects.inv/sitemap
├── data/                  # Shipped JSON doc manifests (ansible-core, lint, navigator, etc.)
└── templates/             # Jinja2 templates for skill packages
```

## MCP Tools

| Tool | Type | Description |
|------|------|-------------|
| `search_modules` | read-only | Find modules by keyword |
| `search_plugins` | read-only | Find plugins by keyword (lookup, filter, inventory, etc.) |
| `get_module_doc` | read-only | Get full module documentation |
| `get_plugin_doc` | read-only | Get full plugin documentation |
| `get_role_doc` | read-only | Get full role documentation (local or Galaxy README) |
| `search_docs` | read-only | Search conceptual doc manifests |
| `fetch_doc` | read-only | Fetch a docs.ansible.com page as clean Markdown |
| `search_collections` | read-only | Search Galaxy for collections by keyword |
| `get_collection_manifest` | read-only | Get collection-level module and role summary |
| `ensure_collection` | idempotent write | Install a collection for this session |
| `list_skills` | read-only | List generated skills |
| `get_skill` | read-only | Read a skill's content |
| `generate_skill` | idempotent write | Generate a skill package for one module |
| `generate_plugin_skill` | idempotent write | Generate a skill package for one plugin |
| `generate_role_skill` | idempotent write | Generate a skill package for one role |
| `generate_collection_skills` | idempotent write | Batch generate skills for a collection |
| `clear_cache` | idempotent write | Clear Galaxy and/or doc manifest caches |

## MCP Resources

| URI | Description |
|-----|-------------|
| `skills://list` | List all generated skill packages |
| `skills://{skill_name}` | Read a skill's SKILL.md by FQCN |
| `galaxy://installed` | List collections installed in this session |
| `galaxy://servers` | List configured Galaxy servers with auth type |
| `server://version` | Installed/latest version info with upgrade status |
| `docs://sources` | List configured doc manifest sources |

## MCP Prompts

| Prompt | Description |
|--------|-------------|
| `review_playbook` | Review a playbook against module docs |
| `explain_module` | Detailed module explanation with examples |
| `explain_plugin` | Detailed plugin explanation with usage examples |
| `generate_role` | Generate a role skeleton using specified modules |
| `find_collection` | Guide through search, install, and explore workflow |

## Key Patterns

- All `parser.py` functions call `subprocess.run()`. The server wraps them via `asyncio.run_in_executor()`.
- Tool functions use lazy imports for `parser` and `skills` to avoid importing ansible-core at startup.
- All inputs are validated (FQCN format, path traversal, length limits) before processing.
- Error messages are sanitized to strip filesystem paths.
- `docs.py` fetches manifests via httpx, caches per-source in a dict.
- `galaxy.py` provides async Galaxy v3 API client with version/docs-blob caching and format conversion.
- `get_module_doc` falls back to Galaxy docs-blob when local collection is missing.
- Lifespan checks PyPI for newer versions (non-blocking, 3s timeout, `ANSIBLE_KNOW_SKIP_UPDATE_CHECK=1` to suppress).
- First tool call emits a single `ctx.warning()` if outdated; `server://version` resource exposes the cached result.
- Tests mock `_run_ansible_doc` — no real `ansible-doc` needed.
- `readme_parser.py` parses Galaxy role README HTML using stdlib `html.parser`. Handles four variable documentation patterns: tables, heading-per-variable, code-block-per-variable, and graceful degradation.
- `get_role_doc` uses three-tier resolution: local ansible-doc → Galaxy readme_html → graceful degradation.
- `text_utils.clean_rtd_markdown` (Foundation) strips breadcrumbs/artifacts from RTD markdown responses.
- `fetch_doc_content` in `docs.py` uses Cloudflare's `Accept: text/markdown` content negotiation, raises `AnsibleKnowError` on content-type mismatch, size/token limits, or redirect to unexpected domain.
- RTD Search API (`_search_rtd_api`) serves as fallback when manifest search returns empty (only when no filters caused the empty result).
- Doc manifests shipped as JSON in `src/ansible_know/data/`, loaded from disk (no HTTP at startup).
- `manifest_builder.py` generates manifests at build time from objects.inv and sitemap sources (requires `[build]` optional deps: `sphobjinv`, `defusedxml`).

## Testing

```bash
pytest tests/ -v                          # unit tests only (mocked, no ansible-core needed)
pytest tests/ --run-integration           # include integration tests (needs ansible-core + network)
ruff check src/ tests/                    # lint
pytest --cov=ansible_know                 # coverage report
```

Unit tests mock `_run_ansible_doc` — no ansible-core needed. Integration tests (`tests/integration/`) hit real ansible-doc and Galaxy API, skipped by default.

## Registration

Development (from project root):
```bash
claude mcp add ansible-know -- uv run --directory . ansible-know-mcp
```

Global (after pip install or via uvx):
```bash
claude mcp add --scope user ansible-know -- uvx ansible-know-mcp
```
