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

The server communicates over stdio by default:

```bash
uvx ansible-know-mcp
```

### HTTP Transport

Run as a standalone HTTP server for shared/remote access:

```bash
# HTTP on default port (8080)
ansible-know-mcp --transport http

# Custom host and port
ansible-know-mcp --transport http --host 10.0.0.1 --port 9090

# Via environment variables (useful for containers)
export ANSIBLE_KNOW_TRANSPORT=http
export ANSIBLE_KNOW_PORT=8080
ansible-know-mcp
```

Connect from any MCP client using the streamable HTTP URL:
`http://<host>:8080/mcp`

> **Security**: HTTP mode has no built-in authentication. Deploy behind a
> reverse proxy with authentication/authorization, or use only on trusted
> networks.

### Docker

Build and run from source:

```bash
docker build -t ansible-know-mcp .
docker run -p 7860:7860 ansible-know-mcp
```

Connect from any MCP client using the streamable HTTP URL:
`http://localhost:7860/mcp`

### Remote MCP (Hugging Face Spaces)

A public instance is available as a remote MCP server:

**Claude Code:**
```bash
claude mcp add ansible-know --transport http https://know.ansible.ar/mcp
```

**VS Code / Cursor** (`.vscode/mcp.json`):
```json
{
  "servers": {
    "ansible-know": {
      "type": "http",
      "url": "https://know.ansible.ar/mcp"
    }
  }
}
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

## Deployment

### Hugging Face Spaces

Deploy as a remote MCP server on [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces):

1. Create a new Space with **SDK: Docker**
2. The Space's `README.md` must include YAML frontmatter:
   ```yaml
   ---
   title: Ansible Know MCP
   emoji: "\U0001F4DA"
   sdk: docker
   app_port: 7860
   ---
   ```
3. Push this repository (or set it as the Space's linked repo)
4. The included `Dockerfile` builds and starts the server automatically

> **Note:** Free-tier Spaces sleep after inactivity. MCP clients will see
> connection errors until the Space wakes up (~30-60s cold start). Use
> a paid Space or a keep-alive ping for production use.

**Custom domain** (optional):

1. In the Space settings, go to **Custom domains**
2. Add your domain (e.g., `know.ansible.ar`)
3. Create a CNAME record pointing to `<owner>-<space-name>.hf.space`
4. HF provisions a TLS certificate automatically

**Environment variables** (set in Space settings):

| Variable | Required | Description |
|----------|----------|-------------|
| `ANSIBLE_KNOW_SKILLS_DIR` | No | Defaults to `./skills/` (ephemeral in container) |
| `ANSIBLE_KNOW_NO_PUBLIC_GALAXY` | No | Set to `1` to disable public Galaxy fallback |

### Generic Docker

The `Dockerfile` works with any container platform (Fly.io, Railway, Cloud Run, etc.):

```bash
docker build -t ansible-know-mcp .
docker run -p 8080:7860 ansible-know-mcp
```

Override defaults with environment variables:

```bash
docker run -p 9090:9090 \
  -e ANSIBLE_KNOW_PORT=9090 \
  ansible-know-mcp
```

## Development

```bash
git clone https://github.com/leogallego/ansible-know-mcp.git
cd ansible-know-mcp
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
```

## Acknowledgments

The skill generation approach in this project was inspired by [AnsibleClaw](https://github.com/micytao/AnsibleClaw) by Michael Tao — a skill generation framework that converts Ansible modules into portable AI agent skill packages.

## License

GPL-3.0-or-later
