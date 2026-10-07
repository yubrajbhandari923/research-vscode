---
name: research-os-ui
description: Design, implement, and critique the Research OS VS Code extension UI. Use whenever modifying Research Home, Plans, Tasks, Findings, Experiments, navigation, visual hierarchy, interaction design, CSS, webviews, tree views, or other human-facing Research OS surfaces.
---

# Research OS UI/UX

You are designing a professional computational-research environment inside VS Code.

This is not a website, analytics dashboard, project-management app, or generic AI agent interface.

The product's primary user is a researcher who wants to think at the level of:

- research questions
- hypotheses
- scientific understanding
- evidence
- decisions
- next directions

Agents handle much of the implementation, experimentation, execution, plotting, and bookkeeping.

The UI exists to make that delegated work understandable and steerable.

## Product test

When the researcher opens a project after being away for two weeks, within approximately 20 seconds they should understand:

1. What are we trying to understand?
2. What do we currently believe?
3. What changed?
4. What is being worked on?
5. What failed?
6. What is blocked?
7. What requires my judgment?
8. What happens next?
9. Which results or plots should I inspect?

Every major UI element should help answer one of those questions.

If it does not, question why it is prominent.

---

# Core interaction hierarchy

Optimize the product around:

UNDERSTAND → STEER → INSPECT

not:

CREATE RECORD → EDIT FIELDS → MANAGE DATABASE

Manual CRUD remains available, but is secondary.

The default experience should surface research understanding, not storage entities.

---

# Research information hierarchy

Prefer this conceptual hierarchy:

Research objective / current question
→ current understanding
→ active plan
→ current task
→ findings and failed directions
→ decisions
→ important outputs
→ experiments
→ runs
→ raw logs and provenance

Do not visually flatten these into equally important database entities.

Runs are evidence and execution detail.

Findings and decisions are knowledge.

Tasks are current work.

The research objective and current understanding are the highest-level human concepts.

---

# Research Home

Research Home is the primary human surface.

It should prioritize:

## Current Focus

Show:

- current research objective/question
- concise current understanding
- current blocker
- next meaningful step

This should be visually dominant.

## Active Plan

Show task progress compactly.

Prefer:

✓ completed  
● active  
○ upcoming  
! blocked  
? awaiting verification

Use restrained semantic styling.

Do not build a large DAG canvas unless the actual workflow proves one necessary.

Dependency-aware lists and indentation are preferable initially.

## Latest Insights

Surface a small number of high-value:

- supported findings
- preliminary findings
- failed directions
- meaningful changes in understanding

A finding statement matters more than its ID.

## Needs My Attention

This is not a notification center.

Only surface things requiring meaningful human thought or intervention:

- agent/verifier disagreement
- blocked research task
- major unexpected result
- important reproduction discrepancy
- run budget reached
- consequential proposed decision
- request for conceptual choice

If nothing needs attention, say so clearly.

## Key Outputs

Surface the most relevant:

- plots
- figures
- tables
- comparison images
- result artifacts

A researcher should not need to navigate Experiment → Run → Artifact to rediscover an important figure.

## Recent Activity

Keep activity compact and secondary.

Do not let an event feed dominate the page.

---

# Progressive disclosure

Always design in layers.

Level 1:
research meaning

Level 2:
work status and evidence

Level 3:
metadata and provenance

Level 4:
raw technical detail

For example:

Full validation experiment

Result:
Entropy regularization reduced mixed-material voxels on 18/20 subjects.

2 runs · 3 outputs

[Details]

Only after expansion/detail navigation should the user see:

- exact run IDs
- timestamps
- agent/model
- command
- git commit
- parameters
- raw logs

Minimal does not mean deleting information.

It means showing information at the appropriate depth.

---

# VS Code-native constraint

This is an IDE extension.

Respect current VS Code UX conventions.

Prefer native VS Code surfaces where they are adequate:

- Tree Views
- Quick Picks
- Command Palette
- toolbar actions
- context menus
- notifications
- status bar

Use webviews when custom presentation genuinely adds value.

Do not recreate native VS Code functionality inside a webview.

Keep the number of sidebar views small.

All custom UI must respect VS Code theme variables.

Never assume only dark mode.

Test at least:

- common dark theme
- common light theme
- high-contrast-friendly behavior
- narrow editor width
- normal editor width
- split editor

Do not use the deprecated `@vscode/webview-ui-toolkit`.

---

# Visual character

Target:

calm scientific instrument
+
refined developer tool
+
dense research notebook

The product should feel precise, intelligent, restrained, and serious.

Avoid both extremes:

- boring database administration
- flashy consumer SaaS dashboard

The visual identity should come primarily from:

- excellent hierarchy
- spacing
- typography
- density
- alignment
- subtle surfaces
- thoughtful interactions

not decorative effects.

---

# Typography

Prefer VS Code/system typography rather than importing decorative web fonts.

This is an IDE tool.

Use:

- font weight
- size
- opacity
- spacing
- line height
- monospace strategically

to establish hierarchy.

IDs such as:

T-014
EXP-008
RUN-0042
F-012

are operational references.

Do not visually lead with them.

Prefer:

Full validation comparison
    T-014

rather than:

T-014 — Full validation comparison

unless ID lookup is specifically important.

---

# Density

Research software contains substantial information.

Do not solve complexity merely by adding giant whitespace.

Use controlled density.

Related information should remain visually grouped.

The interface should support scanning.

Avoid huge cards with little content.

Avoid excessive vertical padding.

Avoid making the researcher scroll through decorative whitespace to find the next scientific statement.

---

# Color

Use VS Code theme variables.

Most UI should remain neutral.

Use semantic color sparingly:

success/support → restrained positive accent
preliminary/attention → restrained warning accent
failure/blocked → restrained error accent
active/running → VS Code accent

Never communicate status through color alone.

Do not create rainbow dashboards.

Avoid gradients unless there is an unusually strong functional/design justification.

No default purple AI aesthetic.

---

# Cards and containers

Do not turn every piece of information into a rounded card.

Prefer:

- headings
- spacing
- grouping
- subtle dividers
- background shifts

Use containers when a semantic grouping genuinely benefits from one.

Avoid:

three equal cards
four KPI cards
bento-grid-for-everything
nested rounded rectangles

These are generic AI UI failure modes.

---

# Motion

Motion is secondary in an IDE research tool.

Use it only when it clarifies:

- expansion/collapse
- state transition
- loading
- task completion
- navigation

Keep it subtle and fast.

Respect `prefers-reduced-motion`.

Never add decorative page-load choreography merely to make the extension appear sophisticated.

---

# Interaction design

Every action should have an obvious outcome.

Prefer labels based on researcher intent:

Create experiment
Start task
Open result
Verify finding
Create checkpoint

rather than implementation terminology.

Maintain keyboard usability.

Interactive rows need clear hover/focus states.

Important actions should be discoverable without cluttering every row with buttons.

Use context menus and secondary actions appropriately.

---

# Human versus agent

The unit of human understanding is the research work, not the agent process.

Prefer:

Full validation comparison
Running · implementer

rather than:

Claude Agent #2 — Running

Agent identity should be secondary provenance.

When useful, display subtly:

Claude Code · 23 min ago

Detailed agent/model information belongs deeper in the hierarchy.

---

# Empty states

Empty states should teach the model.

Bad:

No findings.

Better:

No findings yet.

Findings capture reusable conclusions supported by experiments and other evidence. Agents can create preliminary findings after analysis.

Provide an appropriate action only when useful.

---

# Error and blocked states

Explain:

- what happened
- why it matters
- what can be done next

Avoid vague:

Something went wrong.

Prefer:

Validation task is blocked because the calibration file referenced by the experiment is missing.

Locate file
Open task

---

# Research-specific anti-patterns

Reject designs that:

- emphasize entity counts
- make Runs more prominent than Findings
- treat Research OS like Jira
- show agent activity instead of research activity
- expose every metadata field by default
- make CRUD forms the landing experience
- create a giant workflow graph
- use a Kanban board without a demonstrated need
- hide important scientific outputs deep in navigation
- bury failed directions
- make IDs visually dominant
- duplicate information across multiple panels
- show notifications that do not require intervention
- use color as the only state signal
- optimize for empty demo data rather than real dense projects

---

# Design workflow

For substantial UI changes:

1. Inspect the existing implementation and screenshots.
2. Understand the research task the surface supports.
3. State the intended information hierarchy before coding.
4. Reuse existing UI architecture where sensible.
5. Implement the smallest coherent design.
6. Build/package the extension.
7. Launch the real extension or existing code-server test environment.
8. Capture screenshots.
9. Critique the screenshots visually.
10. Test realistic dense data, not only empty state.
11. Test narrow and normal widths.
12. Test dark and light themes.
13. Run accessibility/UX review.
14. Fix obvious hierarchy, spacing, density, contrast, and interaction problems.
15. Only then consider the UI finished.

Do not consider “it compiles” a successful design review.

---

# Screenshot review rubric

When viewing the rendered UI, explicitly evaluate:

## Hierarchy
Where does the eye go first?
Is that the most important research information?

## Cognitive load
Can the page be understood without reading every element?

## Density
Is information compact but legible?

## Grouping
Are related concepts visibly related?

## Research semantics
Does the page foreground understanding, evidence, and decisions?

## Action clarity
Is the next useful action apparent?

## Noise
What could be removed or demoted?

## Visual craft
Are alignment, spacing, typography, borders, and states consistent?

## VS Code fit
Does this feel like a high-quality VS Code extension rather than a webpage embedded inside VS Code?

## Accessibility
Keyboard, focus, semantic markup, contrast, non-color cues.

Do at least one revision after screenshot review for substantial redesigns.

---

# Final principle

A beautiful Research OS is not the one with the most visual effects.

It is the one where complex research becomes calm and legible.

Make the researcher feel:

"I understand exactly where this project stands."

Then allow them to drill into arbitrary technical depth when they choose.