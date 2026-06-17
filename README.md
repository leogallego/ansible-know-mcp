# Ansible Know MCP Server

Module discovery, documentation search, and skill generation for AI agents via the Model Context Protocol.

## What It Does

Ansible Know is the **learn** layer for AI agents working with Ansible:

- **Galaxy collection discovery** — search 2000+ collections by keyword, ranked by download count
- **Multi-server Galaxy support** — query public Galaxy, private Automation Hub, and AAP Gateway in parallel
- **Module and role documentation** — structured parameter specs, examples, and metadata with Galaxy fallback
- **Documentation search** — find conceptual guides from Ansible's AI-friendly docs
- **Collection management** — auto-install collections and get collection-level overviews
- **Skill generation** — create ready-to-use skill packages that teach agents how to use specific modules and roles
- **Resources and prompts** — browse skills, doc sources, and Galaxy servers; pre-built templates for playbook review, module explanation, and role generation

Together with [Ansible Devtools MCP](https://github.com/ansible/ansible-dev-tools) (build) and [AAP MCP](https://github.com/ansible/aap-mcp-server) (deploy), this enables the full autonomous cycle: **learn -> build -> deploy**.

```
 Agent's MCP servers:

 +----------------------------+  +-------------------+  +--------------+
 | Ansible Know               |  | Ansible Devtools  |  | AAP MCP      |
 | (this project)             |  |                   |  |              |
 |                            |  |                   |  |              |
 | search_collections         |  | ansible_lint      |  | controller.* |
 | search_modules             |  | ansible_navigator |  | eda.*        |
 | get_module_doc             |  | ansible_create_*  |  | gateway.*    |
 | get_role_doc               |  | build_ee          |  | galaxy.*     |
 | get_collection_manifest    |  | zen_of_ansible    |  |              |
 | search_docs                |  | setup_environment |  |              |
 | ensure_collection          |  | environment_info  |  |              |
 | generate_skill             |  |                   |  |              |
 | generate_role_skill        |  |                   |  |              |
 | generate_collection_skills |  |                   |  |              |
 | list_skills                |  |                   |  |              |
 | get_skill                  |  |                   |  |              |
 |                            |  |                   |  |              |
 | LEARN                      |  | BUILD             |  | DEPLOY       |
 +----------------------------+  +-------------------+  +--------------+
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

**Requirement:** `ansible-core` must be installed in the same Python environment (provides `ansible-doc`).

## Usage

### Claude Code

```bash
# Project-scoped
claude mcp add ansible-know -- uvx ansible-know-mcp

# Available in all projects
claude mcp add --scope user ansible-know -- uvx ansible-know-mcp
```

### VS Code / Cursor

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

### Any MCP client

The server communicates over stdio:

```bash
uvx ansible-know-mcp
```

### Full stack

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

### Discovery

| Tool | Description |
|------|-------------|
| `search_collections(query, tags?)` | Search Ansible Galaxy for collections by keyword, ranked by download count |
| `search_modules(keyword, namespace?)` | Find modules by keyword in name or description (up to 50 matches) |
| `get_module_doc(module_name)` | Full structured docs: params, examples, API detection. Falls back to Galaxy if not installed locally |
| `get_role_doc(role_name)` | Role documentation with three-tier resolution: local ansible-doc, Galaxy README, or graceful degradation |
| `search_docs(query, source?, topic?, audience?, core_only?)` | Search documentation manifests for conceptual guides (up to 20 matches) |
| `get_collection_manifest(collection_namespace)` | Collection-level manifest with per-module and per-role summaries |

### Collection management

| Tool | Description |
|------|-------------|
| `ensure_collection(collection_namespace, version?)` | Install a collection to a temporary directory for this session |

### Skills

| Tool | Description |
|------|-------------|
| `list_skills()` | List all generated skills |
| `get_skill(skill_name)` | Read a skill's SKILL.md content |
| `generate_skill(module_name, install_to?)` | Generate a skill package for one module |
| `generate_role_skill(role_name, install_to?)` | Generate a skill package for one role |
| `generate_collection_skills(collection_namespace, install_to?)` | Batch generate skills for an entire collection |

## Resources

| URI | Description |
|-----|-------------|
| `skills://list` | List all generated skill packages |
| `skills://{skill_name}` | Read a skill's SKILL.md content by FQCN |
| `galaxy://installed` | List collections installed in this session |
| `galaxy://servers` | List configured Galaxy servers (names, URLs, auth types — never credentials) |
| `server://version` | Installed and latest version info with upgrade status |
| `docs://sources` | List configured documentation manifest sources |

## Prompts

| Prompt | Description |
|--------|-------------|
| `review_playbook(playbook_yaml)` | Review a playbook against module docs and best practices |
| `explain_module(module_name)` | Detailed module explanation with usage examples |
| `generate_role(role_purpose, modules)` | Generate a role skeleton using specified modules |
| `find_collection(platform_or_use_case)` | Guide through search, install, and explore workflow |

## Multi-server Galaxy Support

Ansible Know reads `[galaxy_server.*]` sections from `ansible.cfg` (resolved in standard order: `ANSIBLE_CONFIG` env, `./ansible.cfg`, `~/.ansible.cfg`, `/etc/ansible/ansible.cfg`):

```ini
# ansible.cfg
[galaxy_server.automation_hub]
url = https://hub.example.com/api/galaxy/
token = my-token

[galaxy_server.public_galaxy]
url = https://galaxy.ansible.com/api/
```

- Token auth, basic auth, and per-server TLS verification
- `search_collections` queries all configured servers in parallel, merging results with source attribution
- `get_module_doc` / `get_role_doc` Galaxy fallback tries servers in priority order
- Public Galaxy is appended as a final fallback; opt out with `ANSIBLE_KNOW_NO_PUBLIC_GALAXY=1`
- Per-server env var overrides: `ANSIBLE_GALAXY_SERVER_{NAME}_{KEY}` (matches ansible-core behavior)
- View configured servers via the `galaxy://servers` resource

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ANSIBLE_KNOW_SKILLS_DIR` | Where to write generated skills | `./skills/` |
| `ANSIBLE_KNOW_DOC_SOURCES` | JSON dict of doc manifest sources | Built-in ansible-core source |
| `ANSIBLE_KNOW_GALAXY_URL` | Galaxy API base URL | `https://galaxy.ansible.com` |
| `ANSIBLE_KNOW_SKIP_UPDATE_CHECK` | Set to `1` to disable PyPI version check at startup | *(not set)* |
| `ANSIBLE_KNOW_NO_PUBLIC_GALAXY` | Set to `1` to suppress auto-appending public Galaxy | *(not set)* |

## Upgrading

| Method | Command |
|--------|---------|
| `uvx` | `uvx --upgrade ansible-know-mcp` |
| `pip` | `pip install --upgrade ansible-know-mcp` |
| Claude Code | `uvx --upgrade ansible-know-mcp` then restart Claude Code |
| VS Code / Cursor | `uvx --upgrade ansible-know-mcp` then reload window |
| Local dev | `git pull && uv sync` |

> **Note:** `uvx` caches the installed version and does not auto-upgrade on new releases.
> To always run the latest version (at the cost of slower startup):
> `claude mcp add ansible-know -- uvx --upgrade ansible-know-mcp`

## Development

```bash
git clone https://github.com/leogallego/ansible-know-mcp.git
cd ansible-know-mcp
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
```

## License

GPL-3.0-or-later
