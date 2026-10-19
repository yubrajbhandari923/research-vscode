---
name: scientific-briefing
description: Write or revise the Quarto Scientist Guide (docs/scientist-guide/*.qmd) as an evidence-grounded scientific monograph with claim and figure provenance. Required for every Scientist Guide writing task.
---

# Scientific briefing (Scientist Guide v2)

The guide is a scientific monograph a collaborator could read without knowing Research OS exists. One set of `.qmd`
chapters is rendered twice: the **scientific edition** (clean prose) and the **researcher edition** (the same prose plus
exact Research OS evidence). You write the prose once. Provenance lives beside it, never inside the prose.

## Procedure
1. `research guide validate` and `research guide status`: which sections are stale, invalid, or missing evidence, and which
   experiments or findings the guide has not covered. Work on those sections only. Do not rewrite the whole book.
2. For each affected section read the records (`research show …`, `research evidence value RUN-… --metric NAME`,
   `research evidence verify F-…`). Quote numbers only from these, never from earlier prose or memory.
3. Edit the chapter. Respect ownership: `research guide section` shows `owner`. A `human` section is never edited; write
   your proposed change to `_proposals/<chapter>.md` in the guide directory and say so.
4. Register what you wrote: each substantive statement `research guide claim add "<statement>" --kind … --section … --source …`,
   each figure `research guide figure add …`, and mark them in the text (below).
5. `research guide validate` until there are no errors, then `research guide section pin <chapter>` (records that you
   reconciled it with the current evidence; it does not clear errors).
6. Request the independent review (`research guide review`). Never state that the guide is scientifically approved.

## Writing standards
- Explain the science, not the activity: why the question matters, the model and its assumptions, the mathematics and the
  intuition for it, what was varied, controlled and measured, what happened quantitatively, what it means, what it does not.
- No Research OS identifiers (EXP-, RUN-, F-, D-, CLM-, FIG-) in visible prose, captions or table text. Identifiers go in
  claim registrations and `<!-- comments -->`; the researcher edition adds them from the provenance.
- Keep distinct, and register with the matching `--kind`: observation (measured), derivation (follows mathematically),
  interpretation (what we think it means), hypothesis, speculation, proposed (future experiment). Never present an
  interpretation as an observation, or illustrative data as a measurement.
- Uncertainty is part of the result: state limits, alternative explanations, and what would distinguish them.
- Negative and failed results are results: say what was tried, what it showed, and what it rules out.
- A finding that is contradicted, superseded or retracted is never a current conclusion. Describe the earlier belief as
  history ("we first believed … later evidence showed …") and say why it changed. Unreviewed findings are "preliminary".
- Gaps in the records are reported as gaps ("the procedure for … was not recorded"). Never invent procedures, results,
  motivation, citations or values.
- Literature: cite only sources recorded with `research source` and actually examined; keys go in `references.bib`.

## Quarto conventions
- Mark a claim in the text as `[statement text]{#clm-0007 .claim}` using the id returned by `claim add`.
- Equations: `$$ … $$ {#eq-name}` and refer to them with `@eq-name`. Do not use executable cells; rendering never runs code.
- Figures: `![Caption](figures/name.png){#fig-name}` with a caption that says what is plotted, the axes and units, and the
  uncertainty. Refer to them with `@fig-name`. Register each with `research guide figure add` and its `--fig-kind`:
  `archived` (original experimental figure), `recreated` (re-plotted from recorded measurements), `derived` (new analysis
  of recorded data), `conceptual` (illustration only; it can never support an observation).
- Every substantive figure also needs: a title, labelled axes with units, a caption, a paragraph on what it shows and its
  limits, and its sources or generator inputs. Do not add a figure that only decorates.
- Use `{.content-visible when-profile="researcher"}` blocks only for researcher-only material; the scientific edition must
  read completely without it.

## Review and acceptance
A chapter is acceptable for review when `research guide validate` has no errors, every evidential claim has a source,
every figure has its provenance, and the scientific edition shows no Research OS identifiers. That is a provenance check.
The independent model review and the human researcher's approval are separate steps, recorded separately.
