# ADR 0005: Jinja2-Based Skill Package Generation

## Status

Accepted

## Date

2026-06-19

## Context

The server's primary output artifact is a "skill package" — a directory
containing structured documentation and runnable scripts that AI agents
can load to learn how to use a specific Ansible module or role. Each skill
package contains:

```
<module.fqcn>/
├── SKILL.md           # Structured guide for AI agents
├── scripts/
│   ├── run.sh         # Execute the module via ansible-playbook
│   └── check.sh       # Dry-run (check mode) execution
└── assets/
    └── playbook.yml   # Ready-to-use playbook
```

Role skill packages omit `scripts/` (roles are used within playbooks, not
run directly):

```
<role.fqcn>/
├── SKILL.md           # Structured guide for AI agents
└── assets/
    └── playbook.yml   # Ready-to-use playbook
```

The content of each file is derived from module/role metadata (parameters,
examples, descriptions) and must follow consistent formatting.

Approaches considered:

1. **Jinja2 templates** — define `.j2` templates for each output file,
   render with metadata context.
2. **Python string formatting** — build file contents with f-strings or
   `str.format()`.
3. **Code generation library** — use a dedicated code generation tool.
4. **YAML/Markdown builders** — use structured builders for each file type.

## Decision

Use Jinja2 templates stored in `src/ansible_know/templates/` for all
skill package file generation.

Templates:
- `SKILL.md.j2` — module skill documentation
- `ROLE_SKILL.md.j2` — role skill documentation
- `run.sh.j2` — module execution script
- `check.sh.j2` — module check-mode script
- `playbook.yml.j2` — module playbook
- `role_playbook.yml.j2` — role playbook

## Consequences

### Positive

- **Separation of content and logic**: templates define the output format;
  Python code handles metadata extraction and context building. Template
  authors do not need Python knowledge; Python developers do not need to
  understand the output format details.
- **Maintainability**: changing the skill package format (adding sections,
  adjusting formatting) requires editing templates, not Python code.
- **Consistency**: all skill packages are generated from the same templates,
  ensuring uniform structure across modules and roles.
- **Jinja2 is already an Ansible ecosystem tool**: users familiar with
  Ansible templating can understand and customize the templates.

### Negative

- **Jinja2 dependency**: adds `jinja2` as a runtime dependency. However,
  Jinja2 is already an indirect dependency via `ansible-core`, so the
  incremental cost is zero in practice.
- **Template debugging**: Jinja2 template errors produce tracebacks that
  reference template line numbers, not Python line numbers. This can be
  confusing when debugging rendering failures.
- **Limited type safety**: template context is a `dict[str, Any]` — typos
  in variable names are silent template errors, not Python type errors.
- **Two context builders**: `_template_context()` for modules and
  `_role_template_context()` for roles are separate functions that could
  drift if not maintained together.

### Design Details

The rendering pipeline:

```
Raw ansible-doc JSON
  → parser.extract_module_metadata()  → ModuleMetadata TypedDict
  → skills._template_context()        → dict[str, Any] (template vars)
  → Jinja2 template.render()          → str (file contents)
  → skills.write_skill_package()      → files on disk
```

The `_template_context()` function enriches metadata with derived values:
- `skill_name`: short module name (last segment of FQCN)
- `example_args`: representative CLI arguments from parameters/examples
- `examples_contain_play`: whether example YAML includes a full play

Scripts are written with executable permissions (`chmod +x`) via
`stat.S_IXUSR`.

### Future Considerations

- If the output format becomes complex enough to require conditional
  logic beyond Jinja2's capabilities, consider a two-phase approach:
  template rendering for structure, then post-processing for validation.
- Consider adding template validation tests that render with sample
  metadata and verify the output structure.
- If custom skill formats are needed (e.g., for different AI agent
  frameworks), support template overrides via environment variable or
  configuration.

## Implementation Notes

- `skills.py` — context builders, package writers, skill listing/reading
- `templates/SKILL.md.j2` — module skill template
- `templates/PLUGIN_SKILL.md.j2` — plugin skill template
- `templates/ROLE_SKILL.md.j2` — role skill template
- `templates/COLLECTION_SKILL.md.j2` — collection-level skill template
- `templates/run.sh.j2`, `check.sh.j2` — execution scripts
- `templates/playbook.yml.j2`, `role_playbook.yml.j2` — playbook assets

## Related Decisions

- [ADR-0002](0002-subprocess-ansible-doc.md) — metadata extraction feeds
  the template context
- [ADR-0007](0007-agentskills-spec-compliance.md) — templates must produce
  agentskills.io-compliant frontmatter (issue #148)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-19 | Leonardo Gallego (AI-assisted) | Initial decision |
| 2026-06-26 | Leonardo Gallego (AI-assisted) | Added Implementation Notes, Related Decisions, Revision History |
