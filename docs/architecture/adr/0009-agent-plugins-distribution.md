# ADR 0009: Agent Plugins as Layer-2 Distribution Format

## Status

Accepted

## Date

2026-08-11

## Context

Layer 2 repository distribution (ADR-0008) previously relied on Lola-specific
packaging (`package_for_lola` / `lola-market.yml`, #149). That wrap works for
the Lola ecosystem but is not a portable, multi-client standard.

The [Agent Plugins specification v1.0.0](https://agent-plugins.org/specification)
defines a vendor-neutral plugin directory with:

- Required `plugin.json` (closed schema, `$schema` + `name`)
- Optional flat `skills/` discovery (one level of `SKILL.md` children)
- Optional `mcp.json` for bundled MCP servers (stdio / streamable-http / sse)

Our generated skills already conform to the Agent Skills specification
(ADR-0007 / #148). The remaining gap is packaging for portable discovery —
including shipping know-mcp itself beside pre-generated skills.

## Decision

Adopt Agent Plugins as the **primary** Layer-2 packaging format.

1. Add Domain function `package_as_agent_plugin()` and MCP tool
   `package_as_plugin` that wrap already-generated nested skills into:

   ```text
   {plugin}/
   ├── plugin.json
   ├── mcp.json                 # optional (stdio or streamable-http)
   └── skills/{skill}/SKILL.md  # flat; no recursive nesting
   {plugin}-{version}.tar.gz    # optional Pulp/AAP artifact (default on)
   ```

2. Keep `generate_*` output nested (`skills/{collection}/{module}/`) —
   flatten **only** at packaging time (same wrap pattern as #149).

3. Deprecate `package_for_lola` for one release cycle (still functional with
   warning). Remove in a later release after consumers migrate.

4. Default plugin name is `ansible-{collection-kebab}-agentplugin` (distinct
   from collection package names; signals Agent Plugins format). Names that
   violate Agent Plugins §5.5 (including the 64-character limit) fail closed —
   callers must supply an explicit `plugin_name`.

5. Artifact format is **`.tar.gz`** (not ZIP) for consistency with Ansible
   collections and Pulp. Filename is `{plugin-name}-{version}.tar.gz`.
   Archives include only allowlisted members (`plugin.json`, `mcp.json`,
   `skills/`); symlinks are omitted.

6. `mcp.json` supports `stdio` (default `uvx ansible-know-mcp`) and
   `streamable-http` (requires `mcp_url` for AAP-hosted know-mcp).

7. `plugin.json` `keywords` include `ansible`, `automation`, namespace,
   collection kebab, collection FQCN, and packaged skill names (capped).

8. `write_plugin_json=False` is an intentional escape hatch for staging
   trees that are **not** claimed as Agent Plugins–conformant packages.
   Defaults write a valid `plugin.json`.

### Explicitly out of scope for know-mcp packaging

- **JFrog path-based identity** (`name/version/name-version.zip`): that layout
  is a **registry storage convention**. know-mcp produces a portable plugin
  directory + tarball; PAH/Pulp/Artifactory decide how to place the artifact
  under their repository path scheme when uploading.
- **Multi-harness manifest directories** (`.claude-plugin/`, `.cursor-plugin/`,
  …): Agent Plugins v1 portable discovery uses root `plugin.json`. Harness-
  specific sidecars are client/marketplace extensions and can be added later
  without changing the core wrap.

## Consequences

### Positive

- Portable distribution across Agent Plugins–conformant clients.
- Single directory can ship MCP tools **and** pre-generated skills.
- Flat packaged `skills/` aligns with shallow scanners (related: #200).
- No change to ADR-0007 generate-time layout.
- `.tar.gz` artifact aligns with Pulp/AAP content pipelines.
- Enterprise `mcp.json` can point at AAP-hosted streamable-http know-mcp.

### Negative

- Another packaging tool on the public MCP surface (20 tools).
- Default names longer than 64 characters need an explicit override.
- Lola consumers must migrate within one release cycle.

### Neutral

- `AGENTS.md` host discovery for flat vs nested trees remains Layer 1 and is
  tracked separately (#222).
- Registry path layout and multi-harness sidecars remain hosting concerns.

## Related Decisions

- [ADR-0007](0007-agentskills-spec-compliance.md) — skill content format
- [ADR-0008](0008-three-layer-distribution.md) — three-layer model; Layer 2
  now prefers Agent Plugins

## References

- [Agent Plugins Specification v1.0.0](https://agent-plugins.org/specification)
- [Plugin manifest schema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json)
- [MCP config schema](https://agent-plugins.org/schemas/1.0.0/mcp.schema.json)
- Issue [#223](https://github.com/leogallego/ansible-know-mcp/issues/223)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-08-11 | Leonardo Gallego (Assisted-by: Cursor (Grok 4.5)) | Initial acceptance |
| 2026-08-11 | Leonardo Gallego (Assisted-by: Cursor (Grok 4.5)) | tar.gz artifact, streamable-http mcp.json, richer keywords; clarify registry/harness out of scope |
| 2026-08-11 | Leonardo Gallego (Assisted-by: Cursor (Grok 4.5)) | Default plugin name suffix `-agentplugin` to distinguish from collections |
