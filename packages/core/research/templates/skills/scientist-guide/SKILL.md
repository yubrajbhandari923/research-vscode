---
name: scientist-guide
description: Update the project's Scientist Guide (docs/SCIENTIST_GUIDE.md), a living scientific narrative, after experiments are synthesized. Use when `research guide status` shows pending syntheses.
---

# Maintain the Scientist Guide

The guide is a readable story for a scientist who knows mathematics and physics well but not this project's field.
It is NOT a paper, README, changelog, report or API reference. `research report` stays the structured evidence summary.

## Procedure
1. `research guide status` (pending syntheses and their findings, with statuses) and `research current`.
2. Read the existing guide (`research guide init` creates a bare-bones one if missing; never overwrite it).
3. For each pending synthesis: `research show EXP-…/S…`, its runs, artifacts, findings, decisions. Quote numbers only
   from these records (`research show RUN-…`, metrics), never from memory.
4. Decide what changed in scientific understanding: a confirmed idea, a surprise, a failure, a correction, a new question.
5. Revise the story. Do not append a per-run entry. Weave the development in, and rewrite earlier explanations that
   the new evidence supersedes. Keep important failed approaches and say why they failed. The history stays in Research OS.
6. Get every quantitative value from Research OS, never from earlier prose: `research evidence value RUN-… --metric NAME [--step N]`
   (the recorded value, unit and status) or `research evidence verify F-…`. Quote a number as established only when its
   finding's numbers show `verified` in `research guide status`; otherwise say "preliminary / unverified". If a binding is
   mismatch, missing or stale, explain it as a correction in the story, not a silent fix. Verified numbers do not make an
   interpretation supported: that needs the independent review (`finding.status`).
   Verify every ID and claim against the records. Cite Research OS IDs (EXP-, RUN-, F-, D-) inline.
7. `research guide done <SYN ids>` only after the file is saved. If you could not finish, leave them pending and say why.

## Writing rules
- Narrative, not a fixed template: a short opening, then natural thematic or chronological flow; headings only if they help.
- Open with the motivation, the initial question and the current approach, even with zero experiments.
- Explain WHY an approach was chosen before how it was built. Explain the model and the physical/mathematical intuition.
- Say how each significant experiment was designed and what it actually tested, then what it showed, including limitations.
- Cite with a short evidence note near the paragraph (EXP-…, RUN-…, A-…, F-…, synthesis id), not metadata in every sentence.
- Literature: cite only sources recorded with `research source`; say whether identity, examination and support were each checked.
- Mark status honestly: preliminary (unreviewed), supported, contradicted, superseded. Exploratory observations are not findings.
- Never present synthetic or simulated data as empirical measurement.
- Never invent history, motivation, outcomes, citations or values. Unknown or ambiguous: say so in an "Open questions" passage.
- End with what is unresolved and why the next experiment matters.
- Preserve text outside the `research:guide:begin/end` markers exactly (that is the user's). Edit only inside them.
