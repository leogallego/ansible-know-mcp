# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for the
Ansible Know MCP Server. Each ADR documents a significant architecture
decision with its context, rationale, and consequences.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-fastmcp-framework.md) | FastMCP as MCP Server Framework | Accepted |
| [0002](0002-subprocess-ansible-doc.md) | Subprocess-Based ansible-doc Integration | Accepted |
| [0003](0003-module-level-state.md) | Module-Level Mutable State | Accepted (with known debt) |
| [0004](0004-galaxy-fallback-chain.md) | Galaxy Fallback Chain with Multi-Server Support | Accepted |
| [0005](0005-jinja2-skill-generation.md) | Jinja2-Based Skill Package Generation | Accepted |
| [0006](0006-upstream-first-integration.md) | Upstream-First Integration with next-mcp | Proposed |
| [0007](0007-agentskills-spec-compliance.md) | agentskills.io Specification Compliance | Proposed |
| [0008](0008-three-layer-distribution.md) | Three-Layer Skill Distribution Model | Proposed |

## Format

Each ADR follows the format:

- **Title**: short descriptive name
- **Status**: Proposed, Accepted, Deprecated, Superseded
- **Context**: what forces led to this decision
- **Decision**: what was decided and why
- **Consequences**: positive and negative impacts, future considerations

## Cross-References

ADRs reference violations documented in
[service-contracts.md](../service-contracts.md) where applicable.
