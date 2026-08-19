# Contributing to Ansible Know MCP Server

Thank you for your interest in contributing! This project is a community proof
of concept and welcomes contributions of all kinds: bug reports, documentation
improvements, new features, and test coverage.

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting Issues

Open an issue at
[github.com/leogallego/ansible-know-mcp/issues](https://github.com/leogallego/ansible-know-mcp/issues).
Include the server version (`uvx ansible-know-mcp --version` or the
`server://version` resource), Python version, and steps to reproduce.

For security vulnerabilities, email **security@ansible.com** instead of opening
a public issue.

## Development Setup

```bash
git clone https://github.com/<your-fork>/ansible-know-mcp.git
cd ansible-know-mcp
uv venv && source .venv/bin/activate
uv sync --extra dev
```

Verify the setup:

```bash
uv run ruff check src/ tests/
uv run pytest tests/ -v
```

**Optional:** install `ansible-core` in the same venv to run integration tests:

```bash
uv pip install ansible-core
uv run pytest tests/ -v --run-integration
```

## Making Changes

1. **Fork** the repository and create a branch from `main`.
2. **Write tests** for your changes. All core changes must include tests.
   Unit tests mock `_run_ansible_doc` so ansible-core is not required.
3. **Run the test suite** before opening a PR:
   ```bash
   uv run ruff check src/ tests/
   uv run pytest tests/ -v --cov=ansible_know
   ```
4. **Open a pull request** against `main`. CI runs lint and tests across
   Python 3.10-3.13 automatically.

## Architecture

The server follows a 5-layer pipeline architecture. See
[`docs/architecture/service-contracts.md`](docs/architecture/service-contracts.md)
for layer boundaries and hard rules. Architecture Decision Records live under
[`docs/architecture/adr/`](docs/architecture/adr/).

Key points for contributors:

- **Data flows downward.** Each layer may depend on layers below it but never
  on layers above.
- **Layer violations are merge blockers.** If your change crosses a layer
  boundary, update the service contracts to document the exception.
- **ADRs are binding.** If your PR contradicts an existing ADR, update the ADR
  as part of the PR and explain the rationale.

## Adding MCP Tools

If you add a new tool, resource, or prompt:

- Include `ToolAnnotations` on tools: set `readOnlyHint`, `idempotentHint`,
  and `destructiveHint` appropriately.
- Update the tool count in the `server.py` module docstring.
- Add the tool to the `CLAUDE.md` MCP Tools table.
- Add the tool to the `README.md` Tools section.
- Write unit tests covering the happy path and error cases.

## Skill Generation

If you modify skill templates (`src/ansible_know/templates/`) or generation
logic (`src/ansible_know/skills.py`):

- Test with at least one module, one role, and one plugin skill.
- Verify the generated `SKILL.md` renders correctly as Markdown.
- The three-layer distribution model (Layer 1 generation, Layer 2 Agent Plugins
  packaging, Layer 3 distribution) is documented in
  [ADR-0008](docs/architecture/adr/0008-three-layer-distribution.md) and
  [ADR-0009](docs/architecture/adr/0009-agent-plugins-distribution.md).

## Code Style

- **Linting:** `ruff` is enforced in CI. Run `uv run ruff check src/ tests/`
  locally.
- **Line length:** 120 characters.
- **Python version:** 3.10+ (no walrus operator in hot paths unless guarded).
- **Async:** all tool handlers are async. Use `run_in_executor()` from
  `async_utils.py` for blocking operations.

## Testing

- **Unit tests** (`tests/`) mock `_run_ansible_doc` and Galaxy API calls.
  No ansible-core or network access required.
- **Integration tests** (`tests/integration/`) hit real ansible-doc and Galaxy
  API. Skipped by default; opt in with `--run-integration`.
- **Coverage:** aim to maintain or improve coverage. Check with
  `uv run pytest --cov=ansible_know --cov-report=term-missing`.

## Community

- [Ansible Forum](https://forum.ansible.com/) (use the `devtools` tag)
- [Ansible Matrix chat](https://matrix.to/#/#devtools:ansible.com)
- [GitHub Discussions](https://github.com/leogallego/ansible-know-mcp/issues)

## License

By contributing, you agree that your contributions will be licensed under the
[GNU General Public License v3.0](LICENSE).
