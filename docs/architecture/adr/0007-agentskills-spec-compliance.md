# ADR 0007: agentskills.io Specification Compliance

## Status

Proposed

## Date

2026-06-26

## Context

The [agentskills.io specification](https://agentskills.io/specification) is
the emerging standard for AI agent skills, supported by 40+ coding agents
including Claude Code, Cursor, GitHub Copilot, VS Code, Gemini CLI, OpenAI
Codex, Junie (JetBrains), and others.

Our generated SKILL.md packages do not comply with the spec:

- `name` field uses Ansible FQCNs with dots and underscores
  (`netbox.netbox.netbox_device`) — the spec requires lowercase + hyphens only
- Directory names don't match the `name` field
- Missing metadata fields (`compatibility`, `metadata.fqcn`, etc.)

Non-compliance blocks interoperability with the agent ecosystem and with
distribution tools like [Lola](https://github.com/LobsterTrap/lola) and
the [vscode-ansible skill registry](https://github.com/ansible/vscode-ansible).

The [client implementation guide](https://agentskills.io/client-implementation/adding-skills-support)
recommends 4-6 levels of scan depth. Our nested output (collection/skill/SKILL.md
= 2 levels) is within spec bounds. Consumers that scan only 1 level (next-mcp's
`_loadLocalSource`, Lola's per-module scan) are below the spec recommendation.

## Decision

### One output format, spec-compliant

No layout options or output modes. Every generated skill is a valid
agentskills.io skill. The collection grouping directory is organizational
structure, not a format — the spec doesn't constrain parent directories.

### Naming conventions

**Skill names** use kebab-case derived from the short module/plugin/role
name. The collection directory provides the namespace — short names are
unambiguous within their collection.

**Modules** get no type prefix (they're the default):

```
netbox-netbox/
  netbox-device/SKILL.md       name: netbox-device
  netbox-site/SKILL.md         name: netbox-site
```

**Plugins** get their type as prefix to prevent collision (a module and a
plugin can share a name within a collection):

```
netbox-netbox/
  lookup-nb-lookup/SKILL.md    name: lookup-nb-lookup
  filter-some-filter/SKILL.md  name: filter-some-filter
```

**Roles** get no prefix (roles cannot collide with modules in a collection):

```
fedora-linux-system-roles/
  timesync/SKILL.md            name: timesync
```

**Collection-level skills** use the collection namespace as the name:

```
netbox-netbox/
  SKILL.md                     name: netbox-netbox (matches parent dir)
```

### Metadata fields

Every generated skill includes:

```yaml
---
name: netbox-device
description: >-
  Manage devices within NetBox.
  Use when managing netbox device resources via API with Ansible.
compatibility: Requires ansible-core and the netbox.netbox collection
metadata:
  fqcn: netbox.netbox.netbox_device
  collection: netbox.netbox
  plugin-type: module
  version: "4.6.0"
---
```

- `name`: kebab-case, matches parent directory, max 64 chars
- `description`: what + when, max 1024 chars
- `compatibility`: runtime requirements
- `metadata.fqcn`: original fully-qualified collection name (for programmatic use)
- `metadata.collection`: collection namespace
- `metadata.plugin-type`: `module`, `lookup`, `filter`, `test`, `connection`,
  `become`, `strategy`, `callback`, `inventory`, `cache`, `cliconf`,
  `httpapi`, `netconf`, `shell`, `vars`, or `role`
- `metadata.version`: collection version when known

### Nesting is spec-compliant

Our output structure (collection/skill/SKILL.md = 2 levels) is within the
spec's recommended 4-6 level scan depth. The 1-level limitation in some
consumers (next-mcp `_loadLocalSource`, Lola per-module scan) is a consumer
gap, not a spec violation on our side.

We propose patching next-mcp's `_loadLocalSource` to scan 2+ levels,
citing the spec. This benefits all skill producers that use namespace grouping.

### Lola packaging is a user concern

Lola is a distribution channel, not a generation format. Our spec-compliant
output can be wrapped into a Lola module by the user as a packaging step.
No special output mode needed.

## Consequences

### Positive

- **40+ agent compatibility**: any agent following the spec can discover
  and load our skills.
- **Lola distribution**: spec-compliant skills can be packaged into Lola
  modules for cross-agent installation.
- **next-mcp registry**: skills work with GitHub sources (Lola format,
  2-level scan) today, and with local sources once scan depth is expanded.
- **Validation**: generated skills can be validated with `skills-ref validate`.
- **One format**: no configuration, no layout modes, no confusion.

### Negative

- **Breaking change**: existing generated skills use FQCN names and
  underscore directories. Regeneration required.
- **Plugin type prefix verbosity**: `lookup-nb-lookup` is longer than
  `nb_lookup`. Trade-off for collision avoidance.
- **Consumer limitations**: 1-level scanners miss module-level skills until
  they expand their scan depth. Mitigated by the proposed next-mcp patch
  and by the GitHub source path (which already scans 2 levels).

## Implementation Notes

Issue [#148](https://github.com/leogallego/ansible-know-mcp/issues/148)
tracks the implementation. Changes required:

- **Templates**: `SKILL.md.j2`, `PLUGIN_SKILL.md.j2`, `ROLE_SKILL.md.j2`,
  `COLLECTION_SKILL.md.j2` — add `compatibility`, `metadata` block
- **Code**: `skills.py` — FQCN-to-kebab-case name generation, plugin type
  prefix logic, directory naming, metadata context building
- **Validation**: frontmatter validation against spec constraints (name
  format, length, required fields)
- **Tests**: update all skill generation tests for new naming/metadata
- **Validator**: `pip install skills-ref`, CLI `agentskills validate` —
  confirmed passing with proposed format

## Related Decisions

- [ADR-0005](0005-jinja2-skill-generation.md) — templates must be updated
  to produce spec-compliant frontmatter
- [ADR-0006](0006-upstream-first-integration.md) — spec compliance is
  required for interoperability with next-mcp's SkillRegistry
- [ADR-0008](0008-three-layer-distribution.md) — one output format makes
  the three-layer model possible

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-26 | Leonardo Gallego (AI-assisted) | Initial proposal |
