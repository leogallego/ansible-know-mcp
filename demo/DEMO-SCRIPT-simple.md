# ansible-know-mcp Demo Script (Simple)

A shorter, single-collection demo. Good for quick introductions or
audiences not focused on networking.

## Prerequisites

```bash
# MCP server registered in Claude Code
claude mcp add ansible-know -- uv run --directory /home/lgallego/Claude/ansible-knowledge-mcp ansible-know-mcp

# asciinema installed
sudo dnf install asciinema
```

## Recording

```bash
# Start recording
asciinema rec demo/raw-session.cast --title "ansible-know-mcp demo" --cols 120 --rows 35

# Inside the recording, launch Claude Code:
claude

# Run through the script below, then exit Claude and stop recording with: exit
```

## Demo Flow

### Act 1: Discovery (search Galaxy, explore a collection)

**You type in Claude Code:**

```
Search Galaxy for collections related to NetBox
```

> **What happens:** Claude calls `search_collections("netbox")`, shows results
> ranked by downloads. Highlight that it found `netbox.netbox` with module count
> and description — no browser needed.

**You type:**

```
Install netbox.netbox and show me what modules it has
```

> **What happens:** Claude calls `ensure_collection("netbox.netbox")` then
> `get_collection_manifest("netbox.netbox")`. Shows a summary of every module
> with one-line descriptions. Point out: "This is the collection's full inventory
> — modules, roles, descriptions — all structured, not a wall of HTML."

### Act 2: Documentation (get module docs)

**You type:**

```
Show me the docs for netbox.netbox.netbox_device
```

> **What happens:** Claude calls `get_module_doc("netbox.netbox.netbox_device")`.
> Returns structured params with types, required flags, defaults, choices, and
> examples. Highlight: "Structured data, not a man page — the AI can reason
> about parameter names, types, and constraints."

### Act 3: Skill Generation (the payoff)

**You type:**

```
Generate skills for the entire netbox.netbox collection
```

> **What happens:** Claude calls `generate_collection_skills("netbox.netbox")`.
> Batch-generates SKILL.md packages for every module in the collection.
> Shows progress (succeeded/failed/total) and the collection-level skill.
> Highlight: "One call — every module in the collection now has a skill
> package that any AI agent can load."

**You type:**

```
Show me what skills we generated
```

> **What happens:** Claude calls `list_skills(collection="netbox.netbox")`.
> Lists all generated skills with names and descriptions. Point out the
> breadth: devices, interfaces, IP addresses, sites, VLANs — the full API
> surface, documented and ready.

**You type:**

```
Show me the skill for netbox_device
```

> **What happens:** Claude calls `get_skill("netbox.netbox.netbox_device")`.
> Displays the SKILL.md content. Walk through sections briefly: parameter
> reference, usage patterns, ready-to-use playbook example.

### Act 4: Execution (using the skills)

**You type:**

```
Using the netbox.netbox skills, write me a playbook that creates a device
called "web-server-01" at site "DC1" with device type "PowerEdge R640"
and role "server"
```

> **What happens:** Claude loads the generated skill and writes a playbook
> with correct module FQCN, proper parameter names, required fields, and
> Ansible best practices. Highlight: "It's not hallucinating parameters —
> it's reading the skill we just generated. The module name, the parameter
> types, the required fields — all grounded in real documentation."

> **Bonus point:** If the playbook uses `netbox_url` and `netbox_token`,
> note that the skill taught it those are required connection params.

### Act 5: Doc Search (conceptual guides)

**You type:**

```
Search the docs for how to write custom filters
```

> **What happens:** Claude calls `search_docs("custom filters")`. Returns
> matching guide entries with titles, summaries, and source links. Highlight:
> "Not just module reference — conceptual guides too."

### Closing

Exit Claude Code (`/exit` or Ctrl+D), then stop asciinema (`exit`).

## Post-Recording

```bash
# Clean up timing (compress long API waits, trim dead air)
python demo/cast-editor.py demo/raw-session.cast demo/ansible-know-demo.cast

# Preview
asciinema play demo/ansible-know-demo.cast

# Upload (optional)
asciinema upload demo/ansible-know-demo.cast
```

## Talking Points

- **No browser tab-switching** — discovery, docs, and skill gen all in the terminal
- **Structured data, not HTML** — AI can reason about params, types, constraints
- **Skills are portable** — any AI coding agent can load them
- **Galaxy fallback** — works even without installing the collection locally
- **Session-scoped installs** — nothing pollutes your system
- **Grounded, not hallucinated** — playbooks use real params because the AI reads skills, not training data
