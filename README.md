# Ansible Know MCP Server

Module discovery, documentation search, and skill generation for AI agents via the Model Context Protocol.

## What It Does

Ansible Know is the foundational "learn" layer for AI agents working with Ansible. It provides:

- **Galaxy collection discovery** — search 2000+ collections on Ansible Galaxy by keyword, ranked by download count
- **Module discovery** — search and explore Ansible modules across installed collections
- **Module documentation** — get structured parameter specs, examples, and metadata (falls back to Galaxy if not installed locally)
- **Documentation search** — find conceptual guides from Ansible's AI-friendly docs
- **Skill generation** — create ready-to-use skill packages that teach agents how to use specific modules
- **Collection bootstrapping** — auto-install collections from Galaxy for the current session
- **Collection manifests** — get collection-level overviews with per-module summaries
- **Resources** — browse skills and doc sources as MCP resources
- **Prompts** — pre-built templates for playbook review, module explanation, and role generation

Together with [Ansible Devtools MCP](https://github.com/ansible/ansible-dev-tools) (build) and [AAP MCP](https://github.com/ansible/aap-mcp-server) (deploy), this enables the full autonomous cycle: **learn -> build -> deploy**.

```
 Agent's MCP servers:

 +-----------------------+  +-------------------+  +---------------+
 | Ansible Know          |  | Ansible Devtools  |  |   AAP MCP     |
 | (this project)        |  |                   |  |               |
 |                       |  |                   |  |               |
 | search_collections    |  | ansible_lint      |  | controller.*  |
 | search_modules        |  | ansible_navigator |  | eda.*         |
 | get_module_doc        |  | ansible_create_*  |  | gateway.*     |
 | get_role_doc          |  | build_ee          |  | galaxy.*      |
 | get_collection_       |  | zen_of_ansible    |  |               |
 |   manifest            |  | setup_environment |  |               |
 | search_docs           |  | environment_info  |  |               |
 | ensure_collection     |  |                   |  |               |
 | generate_skill        |  |                   |  |               |
 | generate_role_skill   |  |                   |  |               |
 | generate_collection   |  |                   |  |               |
 | list_skills           |  |                   |  |               |
 | get_skill             |  |                   |  |               |
 |                       |  |                   |  |               |
 | LEARN                 |  | BUILD             |  | DEPLOY        |
 +-----------------------+  +-------------------+  +---------------+
```

## Installation

Using `uvx` (recommended):

```bash
uvx ansible-know-mcp
```

Using `pip`:

```bash
pip install ansible-know-mcp
```

Runtime requirement: `ansible-core` must be installed in the same environment (for `ansible-doc`).

## Usage

### With Claude Code

```bash
claude mcp add ansible-know -- uvx ansible-know-mcp
```

To make it available in all projects:

```bash
claude mcp add --scope user ansible-know -- uvx ansible-know-mcp
```

### With VS Code / Cursor

Add to `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "ansible-know": {
      "command": "uvx",
      "args": ["ansible-know-mcp"],
      "type": "stdio"
    }
  }
}
```

### With any MCP client

The server runs over stdio by default:

```bash
uvx ansible-know-mcp
```

### Full stack configuration

```json
{
  "mcpServers": {
    "ansible-know":     { "command": "uvx", "args": ["ansible-know-mcp"] },
    "ansible-devtools": { "command": "ade", "args": ["mcp"] },
    "aap":              { "command": "aap-mcp-server" }
  }
}
```

## Tools

### Discovery (read-only)

| Tool | Description |
|------|-------------|
| `search_collections(query, tags?)` | Search Ansible Galaxy for collections by keyword. Returns results ranked by download count. |
| `search_modules(keyword, namespace?)` | Find modules by keyword in name or description. Returns up to 50 matches. |
| `get_module_doc(module_name)` | Get full structured docs: params, examples, API detection. Falls back to Galaxy if not installed locally. |
| `get_role_doc(role_name)` | Get role documentation with three-tier resolution: local ansible-doc, Galaxy README, or graceful degradation. |
| `search_docs(query, source?, topic?, audience?, core_only?)` | Search documentation manifests for conceptual guides. Returns up to 20 matches. |
| `get_collection_manifest(collection_namespace)` | Get collection-level manifest with per-module and per-role summaries. |

### Collection management

| Tool | Description |
|------|-------------|
| `ensure_collection(collection_namespace, version?)` | Install a collection to a temporary directory for this session. |

### Skill management

| Tool | Description |
|------|-------------|
| `list_skills()` | List all generated skills (read-only). |
| `get_skill(skill_name)` | Read a skill's SKILL.md content (read-only). |
| `generate_skill(module_name, install_to?)` | Generate a skill package for one module. Returns SKILL.md inline. |
| `generate_role_skill(role_name, install_to?)` | Generate a skill package for one role. Returns SKILL.md inline. |
| `generate_collection_skills(collection_namespace, install_to?)` | Batch generate skills for an entire collection. |

## Resources

| URI | Description |
|-----|-------------|
| `skills://list` | List all generated skill packages |
| `skills://{skill_name}` | Read a skill's SKILL.md content by FQCN |
| `galaxy://installed` | List collections installed in this session |
| `server://version` | Installed and latest version info with upgrade status |
| `docs://sources` | List configured documentation manifest sources |

## Prompts

| Prompt | Description |
|--------|-------------|
| `review_playbook(playbook_yaml)` | Review a playbook against module docs and best practices |
| `explain_module(module_name)` | Get a detailed module explanation with usage examples |
| `generate_role(role_purpose, modules)` | Generate a role skeleton using specified modules |
| `find_collection(platform_or_use_case)` | Guide through search, install, and explore workflow |

## Upgrading

The server exposes its version via the MCP protocol. To upgrade to the latest release:

| Installation method | Upgrade command |
|---|---|
| `uvx` | `uvx --upgrade ansible-know-mcp` |
| `pip` | `pip install --upgrade ansible-know-mcp` |
| Claude Code (`claude mcp add`) | `uvx --upgrade ansible-know-mcp` then restart Claude Code |
| VS Code / Cursor | `uvx --upgrade ansible-know-mcp` then reload window |
| Local dev | `git pull && uv sync` |

> **Note:** `uvx` caches the installed version. It does not auto-upgrade on new releases.
> To always run the latest version at the cost of slower startup, register with:
> `claude mcp add ansible-know -- uvx --upgrade ansible-know-mcp`

## Development

```bash
git clone https://github.com/leogallego/ansible-know-mcp.git
cd ansible-know-mcp
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
```

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ANSIBLE_KNOWLEDGE_SKILLS_DIR` | Where to write generated skills | `./skills/` |
| `ANSIBLE_KNOWLEDGE_DOC_SOURCES` | JSON dict of doc manifest sources | Built-in ansible-core source |
| `ANSIBLE_KNOWLEDGE_GALAXY_URL` | Galaxy API base URL | `https://galaxy.ansible.com` |
| `ANSIBLE_KNOW_SKIP_UPDATE_CHECK` | Set to `1` to disable PyPI version check at startup | *(not set)* |

## License

GPL-3.0-or-later
