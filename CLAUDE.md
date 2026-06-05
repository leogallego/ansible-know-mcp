# CLAUDE.md

## Project

Ansible Know MCP Server — module discovery, documentation search, and skill generation for AI agents via the Model Context Protocol.

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
├── server.py              # FastMCP server: 10 tools, 3 resources, 3 prompts (entrypoint)
├── parser.py              # ansible-doc wrapper — module discovery and metadata extraction
├── skills.py              # skill rendering + package writing (Jinja2)
├── config.py              # paths, constants, doc source registry
├── collection_manifest.py # collection-level MANIFEST.json generation/caching
├── docs.py                # multi-manifest documentation client (httpx)
├── galaxy.py              # Galaxy v3 API client — search, docs-blob, format conversion
└── templates/             # Jinja2 templates for skill packages
```

## MCP Tools

| Tool | Type | Description |
|------|------|-------------|
| `search_modules` | read-only | Find modules by keyword |
| `get_module_doc` | read-only | Get full module documentation |
| `search_docs` | read-only | Search conceptual doc manifests |
| `search_collections` | read-only | Search Galaxy for collections by keyword |
| `get_collection_manifest` | read-only | Get collection-level module summary |
| `ensure_collection` | idempotent write | Install a collection for this session |
| `list_skills` | read-only | List generated skills |
| `get_skill` | read-only | Read a skill's content |
| `generate_skill` | write | Generate a skill package for one module |
| `generate_collection_skills` | write | Batch generate skills for a collection |

## MCP Resources

| URI | Description |
|-----|-------------|
| `skills://list` | List all generated skill packages |
| `skills://{skill_name}` | Read a skill's SKILL.md by FQCN |
| `docs://sources` | List configured doc manifest sources |

## MCP Prompts

| Prompt | Description |
|--------|-------------|
| `review_playbook` | Review a playbook against module docs |
| `explain_module` | Detailed module explanation with examples |
| `generate_role` | Generate a role skeleton using specified modules |

## Key Patterns

- All `parser.py` functions call `subprocess.run()`. The server wraps them via `asyncio.run_in_executor()`.
- Tool functions use lazy imports for `parser` and `skills` to avoid importing ansible-core at startup.
- All inputs are validated (FQCN format, path traversal, length limits) before processing.
- Error messages are sanitized to strip filesystem paths.
- `docs.py` fetches manifests via httpx, caches per-source in a dict.
- `galaxy.py` provides async Galaxy v3 API client with version/docs-blob caching and format conversion.
- `get_module_doc` falls back to Galaxy docs-blob when local collection is missing.
- Tests mock `_run_ansible_doc` — no real `ansible-doc` needed.

## Testing

```bash
pytest tests/ -v
```

All tests use `unittest.mock.patch` to mock `_run_ansible_doc`. No ansible-core installation is needed to run the test suite.

## Registration

Development (from project root):
```bash
claude mcp add ansible-know -- uv run --directory . ansible-know-mcp
```

Global (after pip install or via uvx):
```bash
claude mcp add --scope user ansible-know -- uvx ansible-know-mcp
```
