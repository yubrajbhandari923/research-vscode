---
name: scientific-briefing
description: Write a short first draft of an important result in the Quarto Scientist Guide (docs/scientist-guide/*.qmd) within the researcher's limits. Required for every Scientist Guide writing task.
---

# Scientific briefing: a short draft the researcher will finish

The Scientist Guide is the researcher's own scientific writing, rendered as a book (and a website). You are not writing it:
you give the researcher a **short first draft of the main idea**. They will rewrite it, add citations and put the result in
its scientific context. `research report` already covers every experiment and run; the guide holds only what matters.

## Limits
Your brief states them (`guide.draft.*`): words per section, sections per draft, figures per section. They are checked by
`research guide draft check` after you finish; going over is a failure, not a style choice. Shorter is fine. Never touch
human-owned chapters (`research guide section` shows the owner; `ideas.qmd` and `references.qmd` are always human) or
`references.bib`. If you think a human-owned chapter should change, write it to `_proposals/<chapter>.md` and say so.

## Procedure
1. `research guide status` and the result entries named in your brief (`<!-- research:result F-… -->` in `results.qmd`).
2. Read the records behind each result: `research show F-…`, its runs and artifacts, and the numbers with
   `research evidence value RUN-… --metric NAME` or `research evidence verify F-…`. Quote numbers only from there.
3. Replace the `DRAFT` comment of the entry with 2–5 plain sentences: what was done, what was found, what it means, and the
   main limitation. Keep the marked claim `[…]{#clm-… .claim}` as it is, or reword it and keep its marker.
4. If a figure helps and the limit allows, keep the one logged with the entry and write its caption: what is plotted, axes,
   units, uncertainty. Do not add figures that only decorate.
5. `research guide validate` (no errors), then `research guide draft done F-…` and `research guide draft check`.
6. Do not claim the text is scientifically approved. Review and approval are separate steps (`research guide review`).

## Writing standards
- Explain the science, not the activity. Say what was varied, controlled and measured, what happened, what it does not show.
- No Research OS identifiers (EXP-, RUN-, F-, D-, CLM-, FIG-) in visible prose, captions or tables. They live in the provenance
  and in `<!-- comments -->`; the researcher edition adds them back.
- Keep observation, derivation, interpretation, hypothesis, speculation and proposed work distinct
  (`research guide claim add "…" --kind … --section … --source …` registers a claim). Never present an interpretation as an
  observation, or illustrative data as a measurement.
- A finding that is contradicted, superseded or retracted is never a current conclusion: describe it as history. An unreviewed
  finding is "preliminary". Negative and failed results are results.
- Citations: never invent one. If a source would help, leave `<!-- cite: what is needed -->`; the researcher adds the entry to
  `references.bib` and cites it as `[@key]`. Cite only sources recorded with `research source`.
- Gaps in the records are reported as gaps. Never invent procedures, results, motivation or values.

## Quarto conventions
- Equations: `$$ … $$ {#eq-name}`, referenced `@eq-name`. Figures: `![Caption](figures/name.png){#fig-name fig-alt="…"}`, registered
  with `research guide figure add` (`--fig-kind archived|recreated|derived|conceptual`; conceptual never supports an observation).
- No executable cells: rendering never runs code.
- `{.content-visible when-profile="researcher"}` blocks are only for researcher-only material; the scientific edition must read
  completely without them. A claim that was retracted must not stay in the scientific edition (`validate` reports it).
