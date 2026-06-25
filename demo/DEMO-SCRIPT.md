# ansible-know-mcp Demo Script

Recording a live Claude Code session showcasing ansible-know-mcp.

Scenario: Network automation — WAN circuit management with NetBox as source
of truth and Cisco IOS for device configuration. Inspired by the
[NetBox + AAP Solution Guide](https://ansible-tmm.github.io/solution-guides/README-NetBox-AAP-Solution-Guide)
and [NetBox + EDA Config Guide](https://ansible-tmm.github.io/solution-guides/README-NetBox-EDA-Config-Solution-Guide).

## Prerequisites

```bash
# MCP server registered in Claude Code
claude mcp add ansible-know -- uv run --directory /home/lgallego/Claude/ansible-knowledge-mcp ansible-know-mcp

# asciinema installed
sudo dnf install asciinema

# Verify VHS works (optional, for intro clip)
vhs demo/intro.tape
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

### Act 1: Discovery — "I need to automate network circuits with NetBox"

**You type in Claude Code:**

```
I need to automate WAN circuit management using NetBox as source of truth
and push config to Cisco IOS routers. What collections do I need?
```

> **What happens:** Claude calls `search_collections("netbox")` and
> `search_collections("cisco ios")`. Finds `netbox.netbox` and `cisco.ios`,
> shows module counts and descriptions. Highlight: "I didn't go to Galaxy,
> I didn't Google — the AI searched, found the right collections, and told
> me what each one does."

**You type:**

```
Install both netbox.netbox and cisco.ios
```

> **What happens:** Claude calls `ensure_collection` twice. Both install
> into a session-scoped temp directory. Point out: "Session-scoped — nothing
> pollutes your system. When this session ends, they're gone."

### Act 2: Explore — what can these collections do?

**You type:**

```
Show me the manifest for netbox.netbox — what modules does it have for
circuits, devices, and sites?
```

> **What happens:** Claude calls `get_collection_manifest("netbox.netbox")`.
> Shows the full module inventory. Highlight the circuit-related modules:
> `netbox_circuit`, `netbox_circuit_type`, `netbox_circuit_termination`.
> "The AI can see the whole collection's surface area at a glance."

**You type:**

```
What about cisco.ios? Show me the modules for routing and NTP
```

> **What happens:** Claude calls `get_collection_manifest("cisco.ios")`.
> Highlights `ios_config`, `ios_static_routes`, `ios_ntp_global`.
> "Two collections, two ecosystems, same workflow."

### Act 3: Documentation — deep dive on the modules we need

**You type:**

```
Show me the docs for netbox.netbox.netbox_circuit and cisco.ios.ios_static_routes
```

> **What happens:** Claude calls `get_module_doc` for both. Returns
> structured params with types, required flags, choices, and examples.
> For `netbox_circuit`: point out `cid`, `provider`, `circuit_type`, `status`
> with its choices (`planned`, `provisioning`, `active`, `offline`, etc.).
> For `ios_static_routes`: show the nested `address_families` structure.
> "Structured data — the AI sees parameter types and constraints, not prose."

### Act 4: Skill Generation — arm the AI for all of it

**You type:**

```
Generate skills for both collections — netbox.netbox and cisco.ios
```

> **What happens:** Claude calls `generate_collection_skills` twice.
> Shows succeeded/failed/total for each. Highlight the numbers: "60+ modules
> across two collections, all documented in one batch. Every module now has
> a skill package any AI agent can load."

**You type:**

```
List the netbox.netbox skills related to circuits
```

> **What happens:** Claude calls `list_skills(collection="netbox.netbox")`.
> Shows the full list. Point out `netbox_circuit`, `netbox_circuit_type`,
> `netbox_circuit_termination`, `netbox_provider` — all the building blocks
> for the WAN failover scenario.

### Act 5: Execution — write real automation from skills

**You type:**

```
Using the skills we just generated, write me a playbook that does the
following for a WAN circuit failover scenario:

1. Query NetBox for all circuits at site "Bristol" that are offline
2. Find backup circuits between the same pair of sites
3. Update the backup circuit status to active in NetBox
4. Push a static route to the Cisco IOS routers pointing to the new
   circuit's gateway
```

> **What happens:** Claude reads the generated skills and writes a multi-play
> playbook using:
> - `netbox.netbox.nb_lookup` to query circuits
> - `netbox.netbox.netbox_circuit` to update status
> - `cisco.ios.ios_static_routes` to push routing config
>
> Highlight: "It used the correct FQCNs, the right parameter names, proper
> nested structures for ios_static_routes — all because it read the skills
> we generated, not its training data. No hallucinated parameters."

**Optional follow-up:**

```
Now add NTP configuration to the routers using the config context pattern
from NetBox — use cisco.ios.ios_ntp_global with state: replaced
```

> **What happens:** Claude adds a play using `ios_ntp_global` with the
> resource module `state: replaced` pattern. Point out: "It knows the
> resource module pattern because the skill documents it."

### Act 6: Doc Search — conceptual guides

**You type:**

```
Search the Ansible docs for event-driven automation and webhooks
```

> **What happens:** Claude calls `search_docs("event driven automation")`.
> Returns matching guide entries about EDA, rulebooks, webhook sources.
> "Not just module reference — conceptual guides too. The full picture."

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

## Improvisation Ideas

The script above is the golden path. Riff based on audience interest:

- **Arista instead of Cisco:** Swap `cisco.ios` for `arista.eos` — same
  workflow, different vendor. "The AI doesn't care which vendor — it just
  needs the skills."
- **Event-driven deep dive:** After Act 6, ask Claude to write an EDA
  rulebook that watches for NetBox circuit status changes and triggers
  the failover playbook. Show `ansible.eda` collection.
- **Full topology:** Ask Claude to write playbooks for the full setup:
  create the provider, circuit types, circuits, terminations, cables,
  and devices in NetBox — shows multi-module composition.
- **Role docs:** Show `get_role_doc` for a Linux System Role:
  "Show me the docs for fedora.linux_system_roles.timesync"
- **Before/after comparison:** Ask Claude to write a playbook WITHOUT
  generating skills first, then WITH skills — show the quality difference.
  This is the most convincing proof the skills actually help.
- **Dynamic inventory:** Show the `nb_inventory` plugin docs and ask
  Claude to write a NetBox dynamic inventory config with `compose` and
  `group_by` directives for Cisco devices.

## Talking Points

- **No browser tab-switching** — discovery, docs, and skill gen all in the terminal
- **Structured data, not HTML** — AI can reason about params, types, constraints
- **Skills are portable** — any AI coding agent can load them (Claude Code, Copilot, Codex)
- **Multi-vendor, same workflow** — NetBox + Cisco today, NetBox + Arista tomorrow
- **Galaxy fallback** — works even without installing the collection locally
- **Session-scoped installs** — nothing pollutes your system
- **Grounded, not hallucinated** — playbooks use real params from skills, not training data
- **Solution-guide-ready** — the exact modules used in Red Hat's WAN failover and EDA config solution guides
