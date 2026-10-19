# Scientist Guide v2 (Research OS 1.1)

Research OS records what happened. The Scientist Guide explains the science that emerged. v2 publishes it with
[Quarto](https://quarto.org) as a scientific monograph that stays tied to the canonical records.

## Before and after

| | 1.0 | 1.1 |
|---|---|---|
| Guide | one Markdown file, `docs/SCIENTIST_GUIDE.md` | a Quarto book in `docs/scientist-guide/` (the Markdown guide is kept, untouched) |
| Freshness | which syntheses were incorporated | per **section**: changed findings, finding status and supersession, reviews, metrics, artifacts, documents |
| Evidence | IDs cited inline in prose | a provenance manifest links each claim and figure to records; the prose has no IDs |
| Output | read the Markdown | **scientific edition** (clean prose) and **researcher edition** (same prose plus exact evidence) |
| Review | none | deterministic validation, independent model review and human approval, recorded separately |
| Writing rules | `scientist-guide` skill, advisory | `scientific-briefing` skill, **required** at dispatch, injected and hashed |

Quarto is needed only to render or preview. Every other Research OS operation, and `guide validate`, works without it.
Adopting the v2 guide is opt-in; an upgrade never creates it.

## Layers

1. **Scientific monograph** (`index`, `briefing`, `theory`, `storyline`, `open-questions`): reads without knowing Research OS exists.
2. **Investigations** (`investigations/*.qmd`): motivation, formulation, method, results, figures, interpretation, limits.
3. **Evidence** (`provenance/*.json`, `reviews.json`): claims, figures, section dependencies and reviews, linked to canonical records.
   Metric values are never copied; they are re-read from the records every time.

Files in `docs/scientist-guide/` (the directory is `guide.dir` in `.research/config.yaml`):

| File | Owner | Tracked in git |
|---|---|---|
| `*.qmd`, `references.bib`, `_quarto*.yml`, `filters/` | human or agent (per section) | yes |
| `provenance/claims.json`, `figures.json`, `sections.json`, `reviews.json` | Research OS (edited through `research guide …`) | yes |
| `_proposals/*.md` | agents: suggested changes to human-owned sections (never rendered) | yes |
| `_generated/`, `_output/`, `.quarto/` | generated | no |

## Tutorial

```bash
research upgrade --dry-run && research upgrade --apply     # existing project: adds the scientific-briefing skill only
research guide quarto doctor                                # is Quarto available? PDF (Typst)?
research guide quarto init                                  # create the book next to any existing guide; never overwrites
research guide coverage                                     # experiments and findings the guide does not cover yet
research guide refresh --run                                # an agent writes/updates only stale or uncovered sections
research guide validate                                     # missing, stale, contradicted, superseded, inconsistent evidence
research guide review request                               # prepare an independent reviewer session (verifier role)
research guide review approve investigations/x.qmd --by "You"   # your sign-off; outdated as soon as text or evidence changes
research guide quarto build --edition both                  # docs/scientist-guide/_output/{scientific,researcher}/
research guide quarto preview --edition researcher
```

Registering what the prose says (agents do this under the `scientific-briefing` skill):

```bash
research guide chapter add investigations/sensitivity.qmd --title "…"
research guide claim add "<statement>" --kind observation --section investigations/sensitivity.qmd --source F-003 --source RUN-0007#loss@last
research guide figure add figures/f.svg --fig-kind recreated --section … --label fig-name --title "…" --source RUN-0007#loss --script scripts/make.py
# mark the claim in the text as  [statement]{#clm-0001 .claim}
research guide section pin investigations/sensitivity.qmd   # after reconciling the text with the current evidence
research guide figure refresh FIG-001 --notes "re-plotted"  # after regenerating a figure on purpose (the old hash is kept in its history)
```

Claim kinds: `observation`, `derivation`, `interpretation`, `hypothesis`, `speculation`, `proposed`. Figure kinds: `archived`,
`recreated`, `derived`, `conceptual` (a conceptual figure can never support an observation).
A claim may cite `challenges:` evidence, and a claim flagged `--historical` may cite retracted evidence as history.
Source refs: `F-003`, `EXP-001/S1`, `F-003/B1`, `RUN-0007#metric@last`, `A-0012`, `D-004`, `Q-002`, `SRC-001`, `CLM-0003`, `doc:path`.

## What is checked, and by whom

| Statement | Produced by | Means |
|---|---|---|
| `guide validate` | deterministic | (a claim whose evidence resolves and agrees is labelled *traceable*, never "established") links resolve; nothing stale, retracted, mismatched or unplaced; no identifier in scientific prose; no executable cells |
| `guide review record` | an independent model | judgement against six criteria (evidence fidelity, validity, explanation, uncertainty, history, provenance) |
| `guide review approve` | you | the only thing that labels a chapter *approved*; recorded with your name |

A review or approval is bound to a fingerprint of the chapter text and the state of every record it cites. When either
changes it is reported `OUTDATED`. A successful render is not evidence that the interpretation is correct.
The scientific edition refuses to build over provenance errors (for example a retracted claim presented as current) unless
`--allow-invalid`; a built edition is scanned for leaked identifiers.

## Human edits

Each chapter has an owner (`research guide section set X.qmd --owner human|agent`). Refresh briefs tell agents to leave
human-owned sections alone and write suggestions to `_proposals/`. Routine refreshes only touch stale or uncovered
sections, never regenerate the book, and `quarto init` / `chapter add` never overwrite a file.

## Required skills

`research skills set NAME --level recommended|required|protocol [--check 'research …']`.

* **recommended** (default): discoverable, never blocks.
* **required**: managed dispatch (`research dispatch`, orchestrate, `guide refresh`) fails if it is missing; its text is
  injected into the brief and its content hash recorded; `orchestrate verify` reports a skill edited after dispatch.
* **protocol**: required, plus `research …` commands that must exit 0 (`research skill verify NAME`).

`guide refresh` on a project that adopted the Quarto guide, and `guide review request`, require `scientific-briefing`.
A *direct* Claude Code or Codex session bypasses dispatch and is not gated: it only sees linked skills and `AGENTS.md`.
`research skills audit` lists exactly what such a session can discover. Subjective compliance cannot be proven by injection;
use `guide validate` and `skill verify` on the result.

## Limits

* The structure of the storyline comes from the agent that writes it; Research OS supplies coverage, freshness and
  validation, not prose.
* No figure is generated by rendering. Quarto never executes code (`execute.enabled: false`, `--no-execute`). Recreate a
  figure with a script that reads recorded values and register it with `--script`/`--input`.
* PDF uses Quarto's bundled Typst and is built for the scientific edition only (`--pdf`); it is rendered into its own directory and copied beside the HTML, so a failed or unavailable PDF never removes the HTML. The researcher edition's evidence panels are per-chapter web pages and have no PDF form.
* Scaffold files written by `guide quarto init` (for example `filters/edition.lua`) are never overwritten, so a guide created by an earlier build does not receive a newer filter automatically; copy it by hand if release notes say it changed.
* The VS Code extension has no dedicated Guide view in 1.1; RPC `guide_status`/`guide_validate` and the MCP tools of the same
  names are read-only.
