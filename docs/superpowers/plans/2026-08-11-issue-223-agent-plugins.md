# Plan: Agent Plugins packaging (#223)

**Issue:** [#223](https://github.com/leogallego/ansible-know-mcp/issues/223)  
**Scope:** medium  
**Branch:** `feat/223-agent-plugins` from `origin/main`  
**Design decision:** Option (b) — keep nested `generate_*` layout; flatten only in packaging (mirror `package_collection_for_lola`).

## Assessment summary

```text
Assessment:
  codebase_still_matches: true
  scope: medium
  files_to_modify: [
    src/ansible_know/skills.py,
    src/ansible_know/server.py,
    src/ansible_know/types.py,
    src/ansible_know/validation.py,
    docs/architecture/adr/0008-three-layer-distribution.md,
    docs/architecture/adr/0009-agent-plugins-distribution.md (new),
    docs/architecture/adr/README.md,
    docs/architecture/service-contracts.md,
    tests/test_agent_plugin_packaging.py (new),
    README.md,
    CLAUDE.md
  ]
  skills_needed: [pep8-review, pr-architecture-review, contract-docstrings, git-review]
  risks: [public MCP API growth, plugin name constraints (64 chars), deprecate without breaking Lola]
  blockers_found: []
  out_of_scope: [#222 AGENTS.md enrichment, #200 scan depth, #148/#149 already done]
  brief: Add Agent Plugins Layer-2 packaging (plugin.json + flat skills/ + optional mcp.json); deprecate Lola packaging for one release cycle.
```

## Acceptance criteria (from issue)

1. `package_as_agent_plugin()` Domain function writes a conformant plugin directory.
2. MCP tool `package_as_plugin` exposes it.
3. `package_for_lola` remains functional but deprecated (warning + docs).
4. ADR-0008 Layer 2 updated; ADR-0009 written.
5. Tests cover packaging, name validation, mcp.json optional write, flatten layout.
6. `generate_*` layout unchanged (ADR-0007).

## File-by-file changes

### 1. `src/ansible_know/validation.py`

Add Foundation validators:

- `validate_plugin_name(name: str)` — Agent Plugins §5.5:
  - length 1–64
  - charset `[a-z0-9.-]`
  - start/end alphanumeric
  - no `--` or `..`
- Export in `__all__`.

Reuse path validators already present (`validate_install_path`, `validate_path_containment`).

### 2. `src/ansible_know/types.py`

Add TypedDicts:

```python
class PackageAsPluginResult(TypedDict):
    collection: str
    plugin_name: str
    plugin_dir: str
    skill_count: int
    skills: list[str]
    plugin_json: str | None
    mcp_json: str | None

class AgentPluginManifest(TypedDict, total=False):
    # required keys always set at write time; total=False allows optional metadata
    ...

class AgentPluginManifestRequired(TypedDict):
    schema: Required via key "$schema"  # use TypedDict with quoted keys as elsewhere
```

Prefer a concrete closed-schema TypedDict matching Lola's `LolaMarketYml` pattern:

```python
class AgentPluginManifest(TypedDict):
    """Shape written to plugin.json (closed schema — only these keys)."""
    # use NotRequired for optional fields if on 3.11+ / typing_extensions
```

Keys allowed: `$schema`, `name`, `version`, `description`, `keywords` (and omit author/homepage/repository/license unless we have real values — do not invent).

Also optional `AgentMcpConfig` TypedDict for mcp.json top-level shape if helpful.

Keep `PackageForLolaResult` unchanged.

### 3. `src/ansible_know/skills.py` (Domain)

Add beside existing Lola packaging helpers; export both in `__all__`:

- `default_plugin_name(collection_fqcn) -> str`  
  Default: `ansible-{collection-kebab}` (same as Lola). **Fail closed:** if the default fails `validate_plugin_name` (e.g. >64 chars), raise `ValidationError` requiring an explicit `plugin_name`. No silent truncation.
- `package_as_agent_plugin(skills_dirs, collection_fqcn, output_dir, *, plugin_name=None, include_mcp_config=True, write_plugin_json=True) -> PackageAsPluginResult`

Contract (mirror Lola):

- Preconditions: callers MUST `validate_namespace(collection_fqcn)` first; Domain validates `output_dir` via `validate_install_path`, validates `plugin_name` via `validate_plugin_name`, and `validate_path_containment` on all writes.
- Raises: `FileNotFoundError` (no skills), `ValidationError` (name/path), `OSError` (I/O).
- Silences: unreadable nested dirs / MANIFEST (log + skip / defaults), same as Lola.

Behavior (reuse Lola planning/copy helpers where possible):

1. Resolve collection skills dir via `resolve_collection_skills_dir`.
2. Plan packable skills (same rules as Lola: collection-level SKILL.md + nested dirs with SKILL.md; skip symlink / collision / unreadable).
3. Create `{output_dir}/{plugin_name}/`.
4. Replace `{plugin}/skills/` and copy trees to **flat** `skills/{skill}/SKILL.md` (+ supporting files).
5. If `write_plugin_json`: write closed-schema `plugin.json` via `AgentPluginManifest`:
   - `$schema`: `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`
   - `name`, `version` (from MANIFEST or `"0.0.0"`), `description`, `keywords` (`ansible`, namespace, collection kebab).
6. If `include_mcp_config`: write `mcp.json`:
   ```json
   {
     "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
     "mcpServers": {
       "ansible-know": {
         "type": "stdio",
         "command": "uvx",
         "args": ["ansible-know-mcp"]
       }
     }
   }
   ```
   (`command` is a bare executable token — no placeholders; matches §7.2.1.)
7. If flags false: omit corresponding files; if re-packaging and flag false for a file that exists, unlink it (same pattern as Lola `write_market_yml=False`).

Contract docstring per `contract-docstrings` skill.

Do **not** change `generate_*` or nested source layout.

### 4. `src/ansible_know/server.py` (Orchestration)

- New tool `package_as_plugin` (idempotent write, destructiveHint=True like Lola):
  - params: `collection`, `output_dir`, `source_dir?`, `plugin_name?`, `include_mcp_config=True`, `write_plugin_json=True`
  - Parity with `package_for_lola`: `validate_namespace`, `validate_install_path` (output + optional source), `validate_plugin_name` when provided; lazy-import Domain; `run_in_executor`; catch `FileNotFoundError` / `ValidationError` / `Exception` → `{"error"}` with `sanitize_error` where appropriate.
- Deprecate `package_for_lola`:
  - docstring note: prefer `package_as_plugin`
  - on each call: `logger.warning("package_for_lola is deprecated; use package_as_plugin")`
  - optional `warnings.warn(..., DeprecationWarning, stacklevel=2)` for programmatic callers

Update tool count comments / instructions strings if they hardcode “19 tools”.

### 5. Docs / ADRs

- **ADR-0009** (new, Proposed/Accepted): adopt Agent Plugins as primary Layer-2 packaging format; cite spec URLs; decide option (b); keep Lola one-cycle deprecation.
- **ADR-0008**: Layer 2 section — Agent Plugins primary; Lola secondary/deprecated; note flat `skills/` resolves next-mcp 1-level scan for packaged plugins.
- **ADR README**: index ADR-0009.
- **service-contracts.md**: add `package_as_agent_plugin()` to skills Domain table; add `PackageAsPluginResult` boundary type; bump tool count if documented.
- **README.md** / **CLAUDE.md**: document `package_as_plugin`; mark `package_for_lola` deprecated.
- **project-strategy.md**: one-line Layer-2 note — Agent Plugins primary; Lola deprecated one cycle.

### 6. Tests — `tests/test_agent_plugin_packaging.py`

Mirror `tests/test_lola_packaging.py` structure:

- `validate_plugin_name` accept/reject cases (`My-Plugin`, `--`, `..`, length 65, leading `-`)
- default name for `netbox.netbox` → `ansible-netbox-netbox`
- default name for overlong collection FQCN → `ValidationError` (fail closed; no truncation)
- packaging writes flat `skills/{name}/SKILL.md` (including collection overview skill)
- copies supporting files (`scripts/`)
- writes valid `plugin.json` / optional skip
- writes / skips `mcp.json`
- replaces existing `skills/` idempotently
- missing skills → FileNotFoundError / tool error
- MCP tool happy path + validation error
- deprecation: calling `package_for_lola` still works (existing tests remain)

Share `_write_skill_tree` helper (import from lola test module **or** extract tiny shared fixture — prefer duplicate minimal helper in new file to avoid cross-test coupling).

## Test / lint commands

```bash
uv run pytest tests/test_agent_plugin_packaging.py tests/test_lola_packaging.py -v
uv run ruff check src/ tests/test_agent_plugin_packaging.py
uv run pytest tests/ -v
```

## Migration / compat

- No migration of on-disk generated skills.
- `package_for_lola` kept one release cycle with deprecation warning.
- New tool is additive (tool count 19 → 20).

## Out of scope (do not implement)

- #222 AGENTS.md enrichment for flat layout
- Changing `generate_*` nesting
- Removing Lola packaging
- Client extension namespaces / marketplace publishing

## Implementation order

1. validation + types  
2. Domain `package_as_agent_plugin` + unit tests (TDD)  
3. MCP tool + deprecation  
4. ADRs / contracts / README / CLAUDE  
5. Full test + ruff  
6. PR with `Closes #223`
