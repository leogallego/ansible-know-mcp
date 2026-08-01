# Build Methodology Demo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a 15-20 minute presentation script with slides outline and screenshots list showing how ansible-know-mcp was built using spec-driven AI-assisted development.

**Architecture:** Three markdown deliverables in `demo/spec-driven-mcp-development/`. The presentation script is the primary artifact (what the speaker reads). The slides outline describes what goes on each slide. The screenshots list provides exact commands to reproduce every screenshot referenced in the other two documents.

**Tech Stack:** Markdown, git (for log/graph commands), GitHub (for PR/issue counts)

**Spec:** `docs/superpowers/specs/2026-06-25-build-methodology-demo-design.md`

## Global Constraints

- All deliverables live in `demo/spec-driven-mcp-development/` — separate from existing product demo scripts in `demo/`
- All examples must be real artifacts from the repository, not fabricated
- Framing: community PoC / proving ground, NOT enterprise-ready product
- Claims must be honest: "working, useful" not "high-quality"; "conditions for quality" with SME caveat
- Numbers must match verified counts: 85 issues, 55 PRs, 10 releases, 730+ tests, 10 design specs, 22 plans, 4 ecosystem comparison reports, 1 stakeholder integration proposal
- 15 slides total, 3 acts: Act 1 (4-5 min), Act 2 (7-8 min), Act 3 (3 min)

## File Structure

```
demo/spec-driven-mcp-development/
├── PRESENTATION-SCRIPT.md    # Speaker script with slide-by-slide notes, timing, what to say
├── SLIDES-OUTLINE.md         # Per-slide content: text, layout, suggested visuals
└── SCREENSHOTS.md            # Exact commands to capture every screenshot referenced
```

---

### Task 1: Create the screenshots list

**Why first:** The other two documents reference screenshots. Defining them first ensures consistent naming and that every referenced screenshot has a reproducible command.

**Files:**
- Create: `demo/spec-driven-mcp-development/SCREENSHOTS.md`

**Produces:** Screenshot identifiers and commands referenced by Tasks 2 and 3.

- [ ] **Step 1: Gather the exact commands for each screenshot**

Run these commands to verify they produce useful output, then document them:

```bash
# Slide 2: hallucinated vs correct playbook — needs a fabricated example
# (spec says "side-by-side: hallucinated playbook vs. correct one")

# Slide 6: A real spec
head -40 docs/superpowers/specs/2026-06-04-galaxy-collection-discovery.md

# Slide 7: Implementation plan with checkboxes
head -50 docs/superpowers/plans/2026-06-04-galaxy-collection-discovery.md

# Slide 7: Git log for the feature branch
git log --oneline --all --graph | grep -A5 -B5 "Galaxy collection discovery"

# Slide 8: Plugin support plan scope
head -40 docs/superpowers/plans/2026-06-23-plugin-support.md

# Slide 9: Plugin support PR page — needs GitHub URL
# PR #122: https://github.com/leogallego/ansible-know-mcp/pull/122

# Slide 10: Specs list
ls -1 docs/superpowers/specs/ docs/specs/

# Slide 10: Plans list
ls -1 docs/superpowers/plans/

# Slide 10: Research reports
ls -1 docs/research/

# Slide 11: Issue/PR activity over time
git log --format="%cd" --date=format:"%Y-%m-%d" | sort | uniq -c | sort -k2
```

- [ ] **Step 2: Write `SCREENSHOTS.md`**

The document must include for each screenshot:
- A unique identifier (e.g., `SHOT-02-hallucinated-playbook`)
- Which slide it belongs to
- The exact command or URL to reproduce it
- A brief description of what to capture and what to highlight
- For the Slide 2 hallucinated vs correct playbook: write two short playbook snippets inline — one with plausible but wrong parameter names (e.g., `netbox_device` with `device_name` instead of `name`, `device_role` instead of `role`), one with the correct parameters from the actual module docs. These are illustrative examples, not fabricated claims.

- [ ] **Step 3: Verify all commands produce output**

Run each command from Step 1 and confirm it produces useful, non-empty output. Fix any that fail (e.g., grep patterns that don't match).

- [ ] **Step 4: Commit**

```bash
git add demo/spec-driven-mcp-development/SCREENSHOTS.md
git commit -m "docs: add screenshots list for build methodology demo"
```

---

### Task 2: Create the slides outline

**Files:**
- Create: `demo/spec-driven-mcp-development/SLIDES-OUTLINE.md`

**Consumes:** Screenshot identifiers from Task 1.

**Produces:** Slide-by-slide structure referenced by Task 3.

- [ ] **Step 1: Write `SLIDES-OUTLINE.md`**

For each of the 15 slides, include:
- Slide number and title
- Act and timing context
- Suggested text / bullet points for the slide itself (what the audience reads)
- Visual elements: which screenshot(s) to include, suggested layout (full-bleed image, split-screen, bullet list, diagram)
- For Slide 5 (methodology diagram): draw the two-path workflow as ASCII art:
  ```
  Feature-driven:  brainstorm → spec → plan → issue ──┐
  Bug-driven:                          issue → spec → plan ──┤
                                                              ├→ worktree → commits (TDD) → PR → review → merge
  ```
- For Slide 12 (what this proves): three-column layout with Velocity / Traceability / Guardrails
- For Slide 13 (PoC vs methodology): two-column layout, left vs right
- For Slide 14 (structured AI adoption): two-row contrast, "without discipline" vs "with discipline"
- Keep slide text concise — max 5 bullet points per slide, max 10 words per bullet. The speaker script has the detail; slides are visual anchors.

- [ ] **Step 2: Cross-check against spec**

Read `docs/superpowers/specs/2026-06-25-build-methodology-demo-design.md` and verify every slide in the spec has a corresponding entry in the outline. Check:
- All 15 slides present
- Act timing matches (Act 1: 4-5 min, Act 2: 7-8 min, Act 3: 3 min)
- Numbers match global constraints
- Screenshot references match identifiers from Task 1

- [ ] **Step 3: Commit**

```bash
git add demo/spec-driven-mcp-development/SLIDES-OUTLINE.md
git commit -m "docs: add slides outline for build methodology demo"
```

---

### Task 3: Create the presentation script

**Files:**
- Create: `demo/spec-driven-mcp-development/PRESENTATION-SCRIPT.md`

**Consumes:** Slide structure from Task 2, screenshot identifiers from Task 1.

- [ ] **Step 1: Write `PRESENTATION-SCRIPT.md`**

Structure as a slide-by-slide speaker script. For each slide:

```markdown
---

## Slide N: [Title] — [Act X, ~Nm]

**[Show: SHOT-XX-description]**

[What to say — written as natural speech, not bullet points. 
First person. Conversational tone. Community contributor energy.
Include transition phrases between slides.]

**Key point:** [The one thing the audience should remember from this slide]

---
```

Content guidelines per act:

**Act 1 (Slides 1-4):** Set the tone. Be honest about PoC status upfront. The hallucinated playbook comparison should be visceral — "this is what happens without grounded docs." The ecosystem slide should be brief — plant the seed, don't dwell. Use the "learn → create → test → deploy" framing: this extends DevTools' "build, test, deploy" to four steps, adding "learn" as the prerequisite and renaming "build" to "create." ansible-know-mcp owns "learn", DevTools owns "create + test", and "deploy" is shared — DevTools deploys content (build EEs, sign, publish to Galaxy) while AAP MCP deploys automation to infrastructure (Controller, EDA, Gateway).

**Act 2 (Slides 5-11):** This is the meat. Walk through real artifacts. When referencing a spec or plan, quote a specific line or section — "look at line 14, where the spec defines the Galaxy API endpoint we're going to use." When showing the PR review, call out a specific finding — "the reviewer caught X, which would have caused Y." The snowball section should feel like acceleration — the specs list grows, the velocity increases.

**Act 3 (Slides 12-15):** Land the message. The PoC vs methodology split should be the emotional peak — honest about limitations, confident about the approach. The structured AI adoption slide directly addresses the leadership audience. End clean.

Timing guidance: write approximate word counts for each slide's script. At ~150 words/minute speaking pace:
- Act 1 slides: ~150-200 words each (1-1.5 min per slide)
- Act 2 slides: ~150-250 words each (1-2 min per slide)
- Act 3 slides: ~100-150 words each (under 1 min per slide)

- [ ] **Step 2: Read the full script aloud (mentally)**

Check for:
- Natural speech flow — no jargon pileups, no sentence-starting with "So,"
- Transitions between slides — each slide should flow from the previous one
- Timing — total word count should be 2000-3000 words (15-20 min at 150 wpm)
- Claims — nothing that contradicts the spec's honesty guardrails
- References — every screenshot/slide reference matches Tasks 1 and 2

- [ ] **Step 3: Cross-check against spec**

Final verification against the design spec:
- All framing points covered (PoC, proving ground, not enterprise-ready)
- Ecosystem relationship described honestly (complementary → feeding upstream → parallel with convergence)
- Numbers match (85 issues, 55 PRs, etc.)
- Key message present: "discipline is the differentiator, not the AI"
- SME caveat included: "conditions for quality... validating that quality still requires subject matter expertise"

- [ ] **Step 4: Commit**

```bash
git add demo/spec-driven-mcp-development/PRESENTATION-SCRIPT.md
git commit -m "docs: add presentation script for build methodology demo"
```

---

### Task 4: Final review and cross-document consistency

**Files:**
- Modify: all three files if needed

- [ ] **Step 1: Verify cross-references**

Check that:
- Every screenshot referenced in SLIDES-OUTLINE.md and PRESENTATION-SCRIPT.md exists in SCREENSHOTS.md
- Slide numbers are consistent across all three documents
- No screenshot is defined but never referenced (dead screenshots)

- [ ] **Step 2: Verify the directory exists and all files are present**

```bash
ls -la demo/spec-driven-mcp-development/
```

Expected: three .md files.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add demo/spec-driven-mcp-development/
git commit -m "docs: cross-document consistency fixes for build methodology demo"
```
