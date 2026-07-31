# Build Methodology Demo — Design Spec

## Overview

A 15-20 minute presentation script with slides outline showing **how** ansible-know-mcp was built using Claude Code and spec-driven AI-assisted development. The audience is engineering, product management, and company leadership evaluating structured AI adoption.

**This is NOT a product demo.** The existing demo scripts (`demo/DEMO-SCRIPT.md`, `demo/DEMO-SCRIPT-simple.md`) showcase what ansible-know-mcp does. This presentation showcases the **development methodology** that produced it.

## Framing

### What this project IS

- A community proof of concept, unsupported, built by one person
- A proving ground for features that can graduate upstream when validated
- Evidence that spec-driven AI-assisted development produces working software
- A feasibility demonstration that a proper engineering team could productize

### What this project IS NOT

- Enterprise-ready software
- A supported product
- An excuse to skip engineering discipline

### Origin

The project started to solve a real problem: AI agents hallucinate Ansible module parameters. The immediate need was accurate module documentation for Red Hat Summit lightning talks demoing NetBox integration. Once the approach proved viable, development continued to cover broader use cases (Galaxy discovery, role support, plugin support, doc search, skill generation).

### Relationship to the official ansible-mcp-server

The Ansible DevTools team ships an official `ansible-mcp-server` (part of the `ansible` VS Code extension). At Red Hat Summit, conversations with the official team clarified where the two projects sit relative to each other:

- **Originally complementary** — ansible-know-mcp covered gaps the official server didn't address (Galaxy discovery, skill generation, doc search, role/plugin documentation).
- **Then feeding upstream** — ideas explored in this PoC informed discussions about what the official server's next version could include. Met with the DevTools lead architect to discuss direction, identify duplication, evaluate overlaps, and take action.
- **Now parallel with convergence** — both projects are evolving independently, with ongoing tracking of where they overlap and where they diverge. As the official server adds capabilities, ansible-know-mcp's scope adapts accordingly.

This relationship is itself a product of the spec-driven methodology: four documented comparison reports (`docs/research/`) track the official server's evolution across its stable and next branches (June 12, 16, 20, 25), identifying where each project adds value, what overlaps, and what to keep vs. defer. This ongoing ecosystem analysis — built into the development workflow — is part of what makes the PoC more than a weekend hack.

The methodology also produced a structured integration proposal (`docs/research/integration-proposal-2026-06-25.md`) — a stakeholder-targeted report for engineering, product management, and community leadership covering integration roadmap, upstream candidates, risks, and the skill lifecycle pipeline (ansible-know-mcp generates skills, the DevTools MCP server distributes them). This is itself evidence that spec-driven development produces more than just code — it produces the communication artifacts needed to coordinate with other teams.

This is an important part of the presentation: the PoC isn't a competing effort — it's a community proving ground that complements and informs the official tooling. The methodology demo should acknowledge this relationship honestly.

## Audience

Mixed: engineering, product management, and company leadership. Leadership is pushing for AI adoption but wants guardrails against duplication and unmaintainable work.

**Key message**: AI-assisted development produces working, useful software when you apply the same engineering discipline you'd apply to any development work. The methodology creates conditions for quality (specs, tests, reviews) — but validating that quality still requires subject matter expertise. The discipline is the differentiator, not the AI. One person built this proof of concept; a proper team could productize it using the same methodology.

## Format

- Presentation script (markdown) with speaker notes and slide descriptions
- Slides outline with suggested content for each slide (not actual slides — the user builds those)
- No live demo — screenshots and real artifacts from the repo instead (safer for a tight talk)
- All examples are real artifacts from the repository, not fabricated

## Structure: 3 Acts (~15 min)

### Act 1 — The Problem (4-5 min)

**Slide 1: Title**
- "How I Built an MCP Server with Spec-Driven AI Development"
- Subtitle: "A community proof of concept — and a methodology any team can use"

**Slide 2: The problem**
- AI agents hallucinate Ansible module parameters
- Side-by-side: hallucinated playbook vs. correct one
- "I needed accurate module docs for AI agents. Nothing like it existed."

**Slide 3: The origin**
- Red Hat Summit lightning talks — needed NetBox demos that worked
- "I had a real deadline, a real use case, and a real problem to solve"
- Brief mention: once it worked for Summit, kept building for broader use cases

**Slide 4: The ecosystem**
- The official `ansible-mcp-server` exists (Ansible DevTools / VS Code extension) — stable branch shipping, next branch evolving
- At Summit, discussed with the official team where both projects fit
- This PoC is a proving ground — covers gaps (Galaxy discovery, skill generation, doc search), validates features that can graduate upstream
- Four ecosystem comparison reports + a stakeholder integration proposal with upstream candidates, risks, and roadmap
- Concrete integration story: skill lifecycle pipeline — ansible-know-mcp generates skills, DevTools MCP distributes them
- Ongoing: as the official server adds capabilities, ansible-know-mcp's scope adapts accordingly
- "This is a community proving ground, not a fork or a competitor"

**Speaker notes**: Set the tone — this is a community project, built by one person, to solve a real problem. Not a product pitch. Honest about PoC status. Acknowledge the official tooling early so the audience doesn't wonder "why not just use the official one?" The fact that you did four comparison audits (in `docs/research/`) shows this isn't a rogue effort — it's ecosystem-aware development. Use the "learn → create → test → deploy" framing: DevTools' tagline is "build, test, deploy" — this extends it to four steps, adding "learn" as the prerequisite knowledge step and renaming "build" to "create" for clarity. ansible-know-mcp owns "learn", DevTools owns "create + test", and "deploy" is shared — DevTools deploys content (build EEs, sign, publish to Galaxy) while AAP MCP deploys automation to infrastructure (Controller, EDA, Gateway).

### Act 2 — The Build (7-8 min, 3 key moments)

**Slide 5: The methodology overview**
- Diagram: two entry points converge into the same workflow:
  - Path A (feature-driven): brainstorm → spec → plan → issue → worktree → commits (TDD) → PR → review → merge
  - Path B (bug/request-driven): issue → spec → plan → worktree → commits (TDD) → PR → review → merge
  - Both paths share everything from worktree onward. The issue can come before or after the spec — what matters is that spec and plan exist before code.
- "Before writing a line of code, I wrote a spec. Every time."
- Brief: each step uses a Claude Code skill that enforces the discipline

**Moment 1: First spec (~3 min)**

**Slide 6: A real spec**
- Screenshot: `docs/superpowers/specs/2026-06-04-galaxy-collection-discovery.md`
- Highlight: problem statement, design section, API details
- "This isn't AI-generated prose — it's a design document that defines scope and constraints"

**Slide 7: Spec → plan → code**
- Screenshot: the corresponding implementation plan with checkbox steps
- Screenshot: the git log showing the feature branch commits
- "The spec became the plan. The plan became the checkboxes. Each checkbox became a commit."

**Speaker notes**: Key point — the spec is the guardrail. Without it, AI generates plausible but wrong architecture. With it, the AI stays within defined scope. The spec is also documentation that outlives the code.

**Moment 2: The workflow in action (~3 min)**

**Slide 8: Plugin support — a bigger challenge**
- Context: 14 plugin types, 6 architectural layers to modify
- Screenshot: `docs/superpowers/plans/2026-06-23-plugin-support.md` showing the plan scope
- "Same methodology, bigger problem. The spec-driven approach scaled."

**Slide 9: The review cycle**
- Screenshot: the plugin-support PR page showing review comments and fix iterations
- Multiple rounds of review visible — findings addressed in follow-up commits
- "Every PR was reviewed. Code review caught real bugs — not just style nits."

**Speaker notes**: Key point — this looks like normal engineering. That's the point. AI-assisted doesn't mean undisciplined. The review cycle is visible in the git history. A new team member could read the specs and understand every design decision.

**Moment 3: The snowball (~2 min)**

**Slide 10: The specs list**
- Screenshot: `docs/superpowers/specs/` + `docs/specs/` — 10 design specs across two directories
- Screenshot: `docs/superpowers/plans/` — 10 feature implementation plans, plus 12 session-level plans for reviews, releases, and ad-hoc tasks (22 plans total)
- Plus: 4 ecosystem comparison reports + 1 stakeholder integration proposal in `docs/research/`
- Timeline overlay: May 1 → v0.1, June 3 → v0.2, ... June 25 → v0.6

**Slide 11: The numbers**
- 85 issues, 55 PRs, 10 releases, 730+ tests, 10 design specs, 22 plans (10 feature + 12 session-level), 4 ecosystem comparison reports, 1 stakeholder integration proposal
- One person, part-time, community project
- Activity chart showing issue/PR density over time
- "Each feature built on patterns established by the previous one. The methodology compounds."

**Speaker notes**: Don't oversell the timeline. Be honest: "part-time, community project, built across about 8 weeks with periods of intense activity." The numbers are impressive on their own without compression.

### Act 3 — The Takeaway (3 min)

**Slide 12: What this proves**

Three columns:
- **Velocity**: One person produced a working PoC with 17 MCP tools, 730+ tests, CI/CD, packaging
- **Traceability**: Every design decision documented in specs. New contributor can trace any feature from spec → plan → issue → commits → PR → PR review → merge
- **Guardrails**: Code review caught real bugs. TDD prevented regressions. Specs prevented scope creep.

**Slide 13: The PoC vs. the methodology**
- Left side: "The project is a community proof of concept. Unsupported. Not enterprise-ready."
- Right side: "The methodology is ready for a team to adopt today."
- "Imagine what a proper team could do with this approach — both productizing this specific tool AND applying the methodology to their own projects."

**Slide 14: What structured AI adoption looks like**
- Directly addressing leadership's concern:
  - "AI without discipline" → duplication, unmaintainable code, hallucinated architecture
  - "AI with discipline" → specs define scope, reviews catch mistakes, tests prevent regressions, every decision is traceable
- "The guardrails aren't overhead. They're what make the velocity sustainable."

**Slide 15: Thank you / links**
- GitHub repo link
- "Questions?"

## Deliverables

All deliverables live in `demo/spec-driven-mcp-development/`, separate from the existing product demo scripts in `demo/`.

1. `demo/spec-driven-mcp-development/PRESENTATION-SCRIPT.md` — Full speaker script with slide-by-slide notes, what to say, what to show, timing guidance
2. `demo/spec-driven-mcp-development/SLIDES-OUTLINE.md` — Slide-by-slide content outline with suggested text, screenshots to take, and layout notes
3. `demo/spec-driven-mcp-development/SCREENSHOTS.md` — List of specific screenshots to capture from the repo, with exact commands to reproduce them

## Non-goals

- Actual slide deck (user builds slides from the outline)
- Live demo segment (too risky for 15 min; use screenshots instead)
- Product documentation or marketing material
- Comparison with other AI development tools

## Open Questions

None — all clarified during brainstorming.
