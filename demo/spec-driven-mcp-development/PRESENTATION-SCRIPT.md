# Presentation Script — Build Methodology Demo

**Total duration:** ~14 minutes (2031 words at ~150 wpm)

**Audience:** Engineering, product management, and company leadership evaluating structured AI adoption.

**Format:** 15 slides, 3 acts. Screenshots and real artifacts from the repo — no live demo.

---

## Slide 1: Title — Act 1, ~30s

**[Show: Title slide — clean layout]**

Good morning. I'm here to talk about how I built an MCP server using spec-driven AI-assisted development. This is a community proof of concept — unsupported, built by one person, part-time. But the methodology I used to build it? That's something any team can adopt today.

**Key point:** This is NOT a product demo — it's a development methodology demo.

---

## Slide 2: The Problem — Act 1, ~1m

**[Show: SHOT-02-hallucinated-playbook and SHOT-02-correct-playbook side by side]**

AI agents hallucinate Ansible module parameters. Look at the left side — this is what an AI agent generates without grounded documentation. The parameter names look plausible: `device_name`, `device_role`, `device_type`, `site_name`. They're wrong. Every single one.

The right side shows the correct parameters: `name`, `role`, `type`, `site`. Four simple parameters, four hallucinations. This playbook won't run.

Here's what makes this visceral: I discovered this ten minutes before a demo. The agent had generated what looked like perfect Ansible — clean YAML, proper indentation, convincing parameter names. I trusted it. I ran it. It failed immediately. Not a subtle error — a hard crash on the first task. The module didn't recognize a single parameter. I had to rewrite the playbook by hand, reading module documentation in a separate browser tab, cross-referencing every parameter.

That's when I realized the core problem: AI agents are incredibly good at generating plausible Ansible syntax, but without grounded module documentation, they're guessing. And plausible guesses break production automation. I needed accurate module documentation for AI agents to consume, and nothing like it existed. So I built it.

**Key point:** AI hallucinations break automation — grounded documentation prevents them.

---

## Slide 3: The Origin — Act 1, ~1m

**[Show: Timeline graphic or Red Hat Summit context]**

This started with a real deadline. I was giving lightning talks at Red Hat Summit, demoing NetBox integration with Ansible. I needed playbooks that worked, and I needed AI agents to help me write them. The problem was urgent and concrete: Summit was three weeks away, I had four demo scenarios to build, and every AI-generated playbook was breaking on parameter hallucinations.

That immediate need drove the first version. I built the minimum viable tool: parse ansible-doc output, expose it via MCP, let AI agents query accurate module parameters. It worked. The demos ran. The lightning talks went well.

But here's what happened next: I kept using it. After Summit, I started building more complex automation. The tool that solved my demo problem was solving a broader problem — AI agents need grounded knowledge to generate working Ansible. Over the next eight weeks, I expanded it to cover Galaxy discovery, role support, plugin support, doc search, and skill generation. The initial solve-my-demo-crisis tool became a community proof of concept for AI-assisted Ansible development.

**Key point:** Real problem, real deadline, real use case — this wasn't a weekend hack.

---

## Slide 4: The Ecosystem — Act 1, ~2m

**[Show: Four-phase workflow diagram: learn → create → test → deploy]**

The official ansible-mcp-server exists. It's part of the Ansible DevTools VS Code extension — a supported, production tool. At Red Hat Summit, I met with the official team to discuss where both projects fit.

This PoC is a proving ground. It covers gaps the official server doesn't address yet: Galaxy discovery, skill generation, doc search. But it's not a competitor — it's ecosystem-aware development. I've written four comparison reports tracking the official server's evolution across its stable and next branches, identifying where each project adds value and where they overlap.

Here's the concrete integration story: I'm building a skill lifecycle pipeline. ansible-know-mcp generates skills for Ansible modules, and the DevTools MCP server distributes them to AI agents. This follows the four-phase workflow you see here: learn, create, test, deploy. ansible-know-mcp owns "learn" — the knowledge layer. DevTools owns "create and test" — writing and validating Ansible content. "Deploy" is shared: DevTools deploys content like execution environments to Galaxy; the AAP MCP server deploys automation to infrastructure.

This is a community proving ground, not a fork or a competitor.

**Key point:** Ecosystem-aware development produces collaboration, not duplication.

---

## Slide 5: The Methodology Overview — Act 2, ~1m

**[Show: Two-path workflow diagram converging into one flow]**

Before I wrote a single line of code, I wrote a spec. Every time. No exceptions.

The diagram shows two entry points. Feature-driven work starts with brainstorming, then a spec, then a plan, then an issue. Bug-driven work starts with the issue, then adds a spec and a plan. Both paths converge at the same point: worktree, commits using test-driven development, pull request, review, merge.

The issue can come before or after the spec. What matters is that the spec and plan exist before the code. Claude Code skills enforce this discipline — they won't let me skip steps. The guardrails aren't overhead. They're what make the velocity sustainable.

**Key point:** Discipline is the differentiator, not the AI.

---

## Slide 6: A Real Spec — Act 2, Moment 1, ~1.5m

**[Show: SHOT-06-spec-example]**

This is a real design spec for Galaxy collection discovery. Look at the structure: problem statement, goal, context table showing what already exists, and the design section defining scope and constraints.

This isn't AI-generated prose. It's a structured engineering document. Line 14 shows the problem: "AI agents need to discover Ansible collections on Galaxy without knowing collection names upfront." Line 22 shows the goal: "Add search_collections tool that queries Galaxy API v3." The context table at line 28 shows what tools already existed and what was missing.

The spec defines the API endpoint we're going to use, the response structure we expect, and the error cases we'll handle. It's explicit, concrete, and falsifiable. This document became the contract between me and the AI agent implementing the feature.

**Key point:** Specs prevent hallucinated architecture — they define constraints before code.

---

## Slide 7: Spec → Plan → Code — Act 2, Moment 1, ~1.5m

**[Show: SHOT-07-plan-checkboxes and SHOT-07-git-history]**

The spec became the plan. Look at the top panel: the implementation plan breaks the spec into actionable steps. Each checkbox is a concrete task: "Add search_collections tool to server.py", "Add Galaxy search client to galaxy.py", "Write unit tests for search filtering."

The bottom panel shows the git history. Each checkbox became a commit. Look at the branch merges: feature branches for Galaxy discovery, plugin support, role documentation. Each one traces back to a spec, a plan, and a set of checkboxes.

This is traceability. A new contributor can pick any feature, read the spec to understand why we built it, read the plan to see how we broke it down, and read the commit history to see what we actually implemented. Every design decision is documented.

**Key point:** Traceability from design to merge — no archeology required.

---

## Slide 8: Plugin Support — Act 2, Moment 2, ~1.5m

**[Show: SHOT-08-plugin-plan]**

Plugin support was a bigger challenge. Ansible has 14 plugin types — lookup, filter, test, connection, inventory, and nine others. Adding support meant modifying six architectural layers: the parser, the Galaxy client, the skill generator, the server tools, the tests, and the documentation.

Without a spec, I would have started coding. I would have added lookup plugin support, shipped it, then realized I needed filter plugins too. Then test plugins. Each one would have been a separate architectural decision, probably inconsistent with the previous ones. By the fifth plugin type, I'd be refactoring earlier implementations to match new patterns.

Instead, I wrote the spec first. Look at the plan. Line 8 shows the scope: "Support all 14 plugin types with same workflow as modules." That single scope statement prevented eight weeks of rework. Line 22 shows the architecture overview: which files need changes, what new functions to add. Line 45 shows the file structure table: parser.py gets `list_plugins` and `get_plugin_doc`, skills.py gets `generate_plugin_skill`, server.py gets two new tools.

Same methodology, bigger problem. The spec-driven approach scaled. The plan became checkboxes, the checkboxes became commits, the commits became a pull request. One feature, one consistent implementation, fourteen plugin types.

**Key point:** The methodology scales — bigger problems need better structure, not less.

---

## Slide 9: The Review Cycle — Act 2, Moment 2, ~1.5m

**[Show: SHOT-09-plugin-pr]**

Every pull request was reviewed. This is the plugin support PR. Look at the review comments tab — multiple rounds of feedback visible. The reviewer caught a bug where plugin types weren't validated against the supported list. That would have caused runtime errors on bad input. Code review caught it before merge.

This looks like normal engineering. That's the point. AI-assisted doesn't mean undisciplined. The review cycle is visible in the git history. The findings are documented in the PR comments. The fixes are in follow-up commits.

A new team member could read this PR and understand the entire review process: what was found, what was fixed, what was deferred, and why.

**Key point:** Code review catches bugs AI misses — humans validate, AI accelerates.

---

## Slide 10: The Specs List — Act 2, Moment 3, ~1m

**[Show: SHOT-10-specs-list, SHOT-10-plans-list, SHOT-10-research-list]**

This is what the methodology produces. Ten design specs across two directories — `docs/specs/` for early work, `docs/superpowers/specs/` for later features. Each one documents a design decision before implementation.

Twenty-two implementation plans: ten for features, twelve for session-level work like releases, reviews, and ad-hoc tasks. Each plan breaks a spec into checkboxes. Each checkbox becomes a commit.

Four ecosystem comparison reports in `docs/research/` — tracking the official ansible-mcp-server's evolution, identifying overlaps, documenting where each project adds value. Plus one stakeholder integration proposal with upstream candidates, risks, and roadmap.

Here's what makes this compound velocity: each spec built on patterns from previous specs. The Galaxy discovery spec established the search-then-fetch pattern. The plugin support spec reused that pattern for fourteen plugin types. The role documentation spec reused it again for role discovery. By the time I wrote the tenth spec, I wasn't inventing new patterns — I was applying proven ones. The methodology creates reusable templates. Write once, apply many times. That's how one person ships ten features in eight weeks.

This is documented decision-making, not just code. The repository is a knowledge base — a new contributor can read the specs and understand not just what the code does, but why it exists and how the design evolved.

**Key point:** Specs outlive code — they're the documentation future contributors will need.

---

## Slide 11: The Numbers — Act 2, Moment 3, ~1m

**[Show: SHOT-11-activity-timeline]**

Eighty-five issues. Fifty-five pull requests. Ten releases from v0.1 to v0.6. Seven hundred thirty tests. Ten design specs, twenty-two plans, four ecosystem reports, one integration proposal.

One person. Part-time. Community project. Built across eight weeks with periods of intense activity.

Look at the activity chart. June 22 shows eighteen commits — that was plugin support landing. June 5 shows eleven — role documentation and Galaxy fallback integration. These peaks aren't crunch time or death marches. They're what happens when the spec is solid and the implementation becomes straightforward. Most of those commits are small, focused changes: add one function, write its tests, update the docs, merge.

Each feature built on patterns established by the previous one. The methodology compounds. By week eight, I wasn't figuring out how to structure a feature — I was following the template from previous features. That's why velocity increases over time instead of burning out.

**Key point:** Velocity through discipline — the guardrails enable speed, not slow it.

---

## Slide 12: What This Proves — Act 3, ~1m

**[Show: Three-column layout — Velocity / Traceability / Guardrails]**

What does this prove?

Velocity: one person produced a working proof of concept with seventeen MCP tools, seven hundred thirty tests, CI/CD, packaging, and ten releases.

Traceability: every design decision is documented. Specs define scope, plans break specs into tasks, commits implement tasks, PRs bundle commits, reviews catch mistakes. A new contributor can trace any feature from spec to merge.

Guardrails: code review caught real bugs that AI missed. Test-driven development prevented regressions. Specs prevented scope creep. The methodology creates conditions for quality — but validating that quality still requires subject matter expertise.

**Key point:** Discipline is the differentiator, not the AI.

---

## Slide 13: PoC vs. Methodology — Act 3, ~1m

**[Show: Two-column contrast — The Project vs. The Methodology]**

The project is a community proof of concept. Unsupported. Not enterprise-ready. I built it to prove this approach works.

But here's what I want leadership to understand: the methodology is ready for a team to adopt today. Specs before code. Plans before commits. Reviews before merge. Tests before ship. These are engineering practices, not AI tricks.

I'm one person, part-time, working alone. I don't have code review from teammates — I'm using Claude Code's review skill as a substitute. I don't have QA — I'm writing my own tests. I don't have product management — I'm writing my own specs. Despite those constraints, this methodology produced a working proof of concept with CI/CD, packaging, releases, and seven hundred thirty tests.

Now imagine what a proper team could do with this approach. A team with real code reviewers, real QA, real product managers. They could productize this specific tool — turn the PoC into a supported product with SLAs, enterprise features, and professional support. They could apply the methodology to their own projects — build new tools, new features, new systems with the same velocity and traceability.

The PoC proves the methodology works under the hardest constraints: one person, no formal team structure, community effort. The methodology is the real deliverable. That's what I'm here to demonstrate.

**Key point:** The methodology outlives the PoC — it applies to any project, any team.

---

## Slide 14: Structured AI Adoption — Act 3, ~1m

**[Show: Two-row contrast — Without Discipline vs. With Discipline]**

This directly addresses the concern I hear from leadership: AI without discipline produces duplication, unmaintainable code, and hallucinated architecture.

AI with discipline produces specs that define scope, reviews that catch mistakes, tests that prevent regressions, and traceability for every decision.

The guardrails aren't overhead. They're what make the velocity sustainable. Without specs, AI generates plausible but wrong designs. Without reviews, bugs ship. Without tests, regressions accumulate. Without traceability, new contributors can't understand why things work the way they do.

The discipline is the differentiator, not the AI. The AI accelerates, but humans validate.

**Key point:** Guardrails enable velocity — they're not obstacles, they're foundations.

---

## Slide 15: Thank You — Act 3, ~30s

**[Show: Clean closing slide with GitHub link]**

Thank you. The repository is on GitHub at leogallego/ansible-know-mcp. All the specs, plans, PRs, and code are there. Questions?

**Key point:** The work is transparent — everything is documented, everything is traceable.

---

## Script Metadata

**Total word count:** 2031 words (13.5 min at 150 wpm)

**Act 1 word count:** 579 words (3.9 min at 150 wpm)

**Act 2 word count:** 1031 words (6.9 min at 150 wpm)

**Act 3 word count:** 421 words (2.8 min at 150 wpm)

**Numbers verified:**
- 85 issues ✓
- 55 PRs ✓
- 10 releases ✓
- 730+ tests ✓
- 10 design specs ✓
- 22 plans (10 feature + 12 session-level) ✓
- 4 ecosystem comparison reports ✓
- 1 stakeholder integration proposal ✓

**Key messages present:**
- "Discipline is the differentiator, not the AI" ✓
- SME caveat: "conditions for quality... validating that quality still requires subject matter expertise" ✓
- PoC framing throughout ✓
- "One person, part-time, community project" ✓
- "Guardrails aren't overhead" ✓

**Screenshot references match SCREENSHOTS.md:** All 11 SHOT-XX identifiers verified ✓

**Honesty guardrails:**
- "working, useful" not "high-quality" ✓
- "community proof of concept, unsupported" ✓
- "not enterprise-ready" ✓
- "proper team could productize it" ✓
