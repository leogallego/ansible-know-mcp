# Screenshot Reference Guide

This document lists the commands and URLs to reproduce each screenshot in the presentation "Building ansible-know-mcp: Spec-Driven AI-Assisted Development."

Each entry includes:
- **SHOT-XX-description**: Unique identifier
- **Slide**: Which slide it appears on
- **Command/URL**: How to reproduce it
- **Description**: What to show and what to highlight

---

## Slide 2: Hallucinated vs. Correct Playbook

### SHOT-02-hallucinated-playbook
- **Slide:** 2
- **Command:** Display inline code
- **Description:** Show the hallucinated (wrong) playbook snippet with incorrect parameter names. Highlight in red the incorrect parameter names.

**Hallucinated (Incorrect):**
```yaml
---
- name: Create a device in NetBox
  hosts: localhost
  gather_facts: no
  tasks:
    - name: Add device
      netbox.netbox.netbox_device:
        device_name: "{{ inventory_hostname }}"  # WRONG: should be 'name'
        device_role: "router"                    # WRONG: should be 'role'
        device_type: "Juniper MX480"             # WRONG: should be 'type'
        site_name: "DC01"                        # WRONG: should be 'site'
        netbox_url: "{{ netbox_api_url }}"
        netbox_token: "{{ netbox_token }}"
```

### SHOT-02-correct-playbook
- **Slide:** 2
- **Command:** Display inline code
- **Description:** Show the correct playbook snippet with proper parameter names. Highlight in green the correct parameter names.

**Correct:**
```yaml
---
- name: Create a device in NetBox
  hosts: localhost
  gather_facts: no
  tasks:
    - name: Add device
      netbox.netbox.netbox_device:
        name: "{{ inventory_hostname }}"         # CORRECT parameter
        role: "router"                           # CORRECT parameter
        type: "Juniper MX480"                    # CORRECT parameter
        site: "DC01"                             # CORRECT parameter
        url: "{{ netbox_api_url }}"
        token: "{{ netbox_token }}"
```

---

## Slide 6: A Real Spec Example

### SHOT-06-spec-example
- **Slide:** 6
- **Command:**
```bash
head -40 docs/superpowers/specs/2026-06-04-galaxy-collection-discovery.md
```
- **Description:** Shows the opening of a real design spec document. Highlights: problem statement, goal, context table showing what already exists, and the design section heading. Demonstrates structured spec format with clear problem/goal/context/design sections.

---

## Slide 7: Implementation Plan with Checkboxes

### SHOT-07-plan-checkboxes
- **Slide:** 7
- **Command:**
```bash
head -50 docs/superpowers/plans/2026-06-04-galaxy-collection-discovery.md
```
- **Description:** Shows the opening of a real implementation plan. Highlights: the goal statement, architecture section, file structure table with actions, and checkbox-style task breakdown. Demonstrates actionable task tracking with sub-steps.

---

## Slide 7: Git Log for Feature Branch

### SHOT-07-git-history
- **Slide:** 7
- **Command:**
```bash
git log --oneline --all --graph | head -40
```
- **Description:** Shows the branching and merge history of the repository. Highlights feature branches merging into main, commit messages referencing PRs (e.g., "#122", "#134"), and the evolution of the codebase over time.

---

## Slide 8: Plugin Support Plan Scope

### SHOT-08-plugin-plan
- **Slide:** 8
- **Command:**
```bash
head -40 docs/superpowers/plans/2026-06-23-plugin-support.md
```
- **Description:** Shows the scope and implementation strategy for plugin support. Highlights: the goal, architecture overview, new tool additions, and file structure table. Demonstrates how a feature plan maps design decisions to concrete implementation tasks.

---

## Slide 9: Plugin Support PR

### SHOT-09-plugin-pr
- **Slide:** 9
- **Command/URL:**
```
https://github.com/leogallego/ansible-know-mcp/pull/122
```
- **Description:** The merged PR that added plugin support. Highlights: PR title, description linking to design work, the file changes tab showing new modules (search_plugins, generate_plugin_skill), and the merge commit showing successful CI/CD checks.

---

## Slide 10: Specifications List

### SHOT-10-specs-list
- **Slide:** 10
- **Command:**
```bash
ls -1 docs/superpowers/specs/ docs/specs/
```
- **Description:** Directory listing showing all design specifications. Highlights: 10 feature design specs total (dated 2026-06-04 through 2026-06-25), proving systematic specification of each feature before implementation.

---

## Slide 10: Plans List

### SHOT-10-plans-list
- **Slide:** 10
- **Command:**
```bash
ls -1 docs/superpowers/plans/
```
- **Description:** Directory listing of implementation plans. Highlights: 10 feature implementation plans (excluding the presentation plan itself), demonstrating detailed actionable planning for each spec.

---

## Slide 10: Research Reports List

### SHOT-10-research-list
- **Slide:** 10
- **Command:**
```bash
ls -1 docs/research/
```
- **Description:** Directory listing of research and analysis documents. Highlights: ecosystem comparison reports, stakeholder integration proposals, and architectural analysis. Shows evidence of upfront investigation and documented decision-making.

---

## Slide 11: Development Activity Over Time

### SHOT-11-activity-timeline
- **Slide:** 11
- **Command:**
```bash
git log --format="%cd" --date=format:"%Y-%m-%d" | sort | uniq -c | sort -k2
```
- **Description:** Commit histogram showing development activity by date. Highlights: sustained development from May through June, with peaks of 18 commits on 2026-06-22 and 11 on 2026-06-05, demonstrating active iteration and continuous delivery.

---

## Notes

- Commands prefixed with docs paths assume execution from the repository root
- The docs/specs/, docs/research/, and newer docs/superpowers/ files exist in the main worktree
- GitHub URLs point to leogallego/ansible-know-mcp repository
- Presenter should run commands in their checkout to capture live output
- Slide numbers reference the presentation structure defined in SLIDES-OUTLINE.md
