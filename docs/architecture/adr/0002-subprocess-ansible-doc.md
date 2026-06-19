# ADR 0002: Subprocess-Based ansible-doc Integration

## Status

Accepted

## Context

The server needs to extract module and role documentation from installed
Ansible collections. Two approaches are available:

1. **Subprocess `ansible-doc`** — shell out to the `ansible-doc` CLI,
   parse its JSON output. This is the same interface humans use and is
   the only *stable, documented* way to access module documentation.

2. **Ansible Python API** — import Ansible internals directly
   (`ansible.plugins.loader`, `ansible.utils.plugin_docs`). This avoids
   process overhead but depends on undocumented, unstable internal APIs
   that change between ansible-core releases without notice.

3. **Galaxy-only** — fetch all documentation from Galaxy's docs-blob API.
   No local ansible-core dependency, but requires network access and
   cannot document locally-developed modules.

Key constraints:

- The server must work with any ansible-core version (2.14+).
- Users may have custom collections installed locally that are not on Galaxy.
- The server runs as an MCP server (long-lived process), not a CLI tool.
- Multiple tool calls may request different modules concurrently.

## Decision

Use subprocess-based `ansible-doc --json` for local documentation, with
Galaxy docs-blob as a fallback when collections are not installed locally.

The subprocess approach was chosen because:

- **Stability**: `ansible-doc --json` output format is a supported,
  documented interface. Internal Python APIs are explicitly unsupported.
- **Isolation**: each `ansible-doc` call runs in its own process with its
  own Python environment. No risk of polluting the server's process state
  with Ansible's global state (plugin loaders, configuration, etc.).
- **Compatibility**: works with any ansible-core version that supports
  `--json` output (2.14+).
- **Correctness**: `ansible-doc` handles all the complexity of plugin
  resolution, collection paths, and documentation extraction. Reimplementing
  this logic would be error-prone.

## Consequences

### Positive

- No dependency on Ansible's unstable internal Python API.
- Clean process isolation — Ansible's global state cannot leak.
- The `--json` flag provides structured output that is straightforward to parse.
- Easy to test by mocking `_run_ansible_doc()` at a single point.

### Negative

- **Performance**: each `ansible-doc` call spawns a subprocess (~200-500ms).
  For batch operations like `generate_collection_skills`, this is called
  once per module, making it O(n) in subprocess calls.
- **Threading complexity**: subprocess calls are synchronous and must be
  wrapped in `_run_in_executor()` to avoid blocking the async event loop.
  This adds a layer of threading indirection.
- **Environment coupling**: the server must find the `ansible-doc` binary
  and manage `ANSIBLE_COLLECTIONS_PATH` environment variables to include
  session-installed collections.
- **Error parsing**: ansible-doc error messages must be parsed to detect
  missing collections (string matching on `_MISSING_COLLECTION_PATTERNS`)
  rather than catching typed exceptions.

### Mitigations

- Galaxy fallback ensures users get documentation even for collections they
  have not installed locally.
- `_find_ansible_doc()` searches the current Python environment first
  (`sys.executable` sibling), then falls back to `shutil.which()`.
- `collections.py` manages `ANSIBLE_COLLECTIONS_PATH` to include the temp
  install directory.
- Unit tests mock `_run_ansible_doc()` so no real ansible-core is needed.

### Future Considerations

- If subprocess overhead becomes a bottleneck for batch operations, consider
  a single `ansible-doc --list --json <collection>` call to get all modules
  at once, followed by individual `--json` calls only for detailed docs.
- If ansible-core introduces a stable Python documentation API, evaluate
  migrating to it for in-process access.
- The Galaxy fallback already handles the "no local installation" case;
  consider making Galaxy-first the default for documentation (with local
  as fallback) to reduce ansible-core dependency.
