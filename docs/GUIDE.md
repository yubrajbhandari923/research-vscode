# The Scientist Guide

`research report` records **everything**: every experiment, run and finding. The Scientist Guide is the opposite: a short,
readable account of the science, in your words, with your citations, for readers who were not in the room. It is a
[Quarto](https://quarto.org) book that lives on the **main** branch (`docs/scientist-guide/`) and publishes as a website on every
push.

```text
Research OS (deterministic)   scaffolds the book · picks the important results · logs a short entry for each · keeps provenance
an agent (only when asked)    writes a SHORT first draft of the main idea, inside limits you set
you                           edit anything · put results in context · add citations · publish
```

Every new project gets the guide (the Initialize wizard has a *Guide* step). For an older project: `research guide init`.

## What is in the folder

| File | Yours to edit? | Notes |
|---|---|---|
| `index.qmd`, `briefing.qmd`, `theory.qmd`, `storyline.qmd`, `open-questions.qmd` | yes | chapters. Agents may draft them only when you start a draft. |
| `results.qmd` | yes | **Key results**: one entry per important result (below). Entries are created once and never rewritten. |
| `ideas.qmd` | yes, only you | **Notes and ideas**: scratch space. Agents never edit it. |
| `references.bib`, `references.qmd` | yes, only you | your bibliography (below). |
| `theme.scss`, `_quarto.yml` | yes | the project's own look and the book settings. Never rewritten. |
| `investigations/*.qmd` | yes | add with `research guide chapter add investigations/x.qmd --title "…"`. |
| `provenance/*.json` | Research OS | claims, figures, sections, reviews, what is logged/drafted. Tracked in git. |
| `_generated/`, `_output/`, `.quarto/` | generated | git-ignored. |

`.github/workflows/publish-guide.yml` (repository root) is written once and is yours to edit.

## Important results: logged for you

A finding is an *important result* under `guide.highlights`:

* `supported` (default): an independent review marked it supported;
* `starred`: only findings you star (`research guide star F-003`, or the Guide page);
* `all`: every finding that is not superseded.

Starred findings always count. A contradicted finding counts only if you star it (you may want to tell that story); a superseded
one never does. When a finding qualifies, Research OS (no agent) adds an entry to `results.qmd`: a heading, one sentence of the
claim registered against the finding, the result's plot (if it has one and `max_figures` allows), and a `DRAFT` comment marking where
your explanation goes. Turn this off with `guide.auto_log: false` and add entries yourself with `research guide log`.

Edit the entry freely. It is not rewritten, and deleting it does not bring it back.

## An agent drafts, within limits, only when you ask

`research guide draft` (Guide page → **Draft with an agent…**, or the command *Draft Scientist Guide Entry with an Agent…*)
prepares a session for the important results that have no draft yet. You preview the brief and press Start; nothing is written
before that. The brief states your limits (Settings → Scientist Guide):

| Setting | Default | Meaning |
|---|---|---|
| `guide.draft.max_words` | 150 | words an agent may add per section / result entry |
| `guide.draft.max_sections` | 3 | chapters or entries one draft may change |
| `guide.draft.max_figures` | 1 | figures per section (0 = text only) |
| `guide.draft.trigger` | `result` | when Home reminds you: `result` (an important result has no draft), `checkpoint` (only after a checkpoint, so drafts are batched), `manual` (never) |

The agent also may not touch human-owned chapters, `ideas.qmd`, `references.bib`, and must not invent citations (it leaves
`<!-- cite: what is needed -->`). Afterwards, `research guide draft check` compares the files with a baseline recorded when the
draft was prepared and reports every overrun; the Guide page shows it. The agent records what it drafted with
`research guide draft done F-003`. The trigger only controls the reminder: an agent never writes into the guide on its own.

## Generated reports: data-driven, never hardcoded

An agent (or a script it writes) that generates a chapter must read the numbers from the run's recorded metrics and the attribution (which run, experiment and artifact) from the records it
used. A typed-in `RUN-0003`, a fixed number, or a sentence about what another run did is stale or false on other data. Research OS checks this with the validators it already has:

* `research guide validate` reports an identifier in scientific prose (`identifier_in_scientific_text`), a cited record that is not in this project (`stale_attribution`, anywhere in the chapter,
  researcher-only blocks included) and a claim that quotes a number different from the metric it cites (`stated_value_mismatch`). Internal identifiers belong in provenance (`research guide claim add`,
  `research guide figure add`) or a researcher-only block, never in the prose a reader sees.
* A **promotion** that adds or changes `.qmd` chapters has a `scientific_content` gate with the same chapter checks: it must pass before approval or integration. A promotion with no Quarto change
  is `not_applicable` and is never held up by it.
* `research site add` refreshes a published experiment's generated files and, if you have not edited the draft page, its commit line and file lists; an edited page is left alone with a warning.
* Agent briefs and the project's AGENTS.md carry these rules.

## Citations and references

`references.bib` exists from the start. Cite a source in any chapter as `[@key]` (several: `[@a2020; @b2021]`; in the text:
`@a2020 showed…`).

```bash
research guide cite add --bibtex '@article{lee2021alpha, author={Lee, Wei}, title={…}, year={2021}}'   # paste from Zotero, a publisher page…
research guide cite add --title "…" --authors "Lee, Wei and Smith, Jane" --year 2021 --doi 10.1000/xyz   # or type the details
research guide cite list        # entries, where each is cited, cited-but-missing keys, unused entries
research guide cite check       # exit 1 when a cited key is not in references.bib
```

A cited key that is missing from `references.bib` is a validation **error** (it would render as `?key`). The Guide page has the same
*Add a reference* dialog. Only add sources you have read: the guide does not check that a citation supports the sentence.

## Look and feel

`theme.scss` is the project's own theme (colours, fonts, rules) layered on Quarto's `cosmo`. Edit it, or change `theme:` in
`_quarto.yml` to any Quarto theme (`[flatly, theme.scss]`). The book's title, author, sidebar and so on are ordinary Quarto settings.

## Build, preview and publish

```bash
research guide quarto doctor                        # is Quarto installed? PDF (Typst) available?
research guide quarto build                         # the website: docs/scientist-guide/_output/scientific/
research guide quarto build --pdf                   # also a PDF (Typst); a failed PDF never removes the HTML
research guide quarto preview                       # live preview in a browser
```

Quarto is only needed to build or preview ([install](https://quarto.org/docs/get-started/)); everything else works without it.
Rendering never runs code (`execute: enabled: false`).

**Publish on every push.** The workflow builds the *scientific edition* and deploys it with GitHub Pages:
commit `docs/scientist-guide` and `.github` on `main`, push, and once on GitHub choose **Settings → Pages → Source: GitHub Actions**.
Every later push that touches the guide updates the site. (`guide.publish.workflow: false`, or the wizard's checkbox, skips the file;
turning it off later never deletes it.) The website never contains Research OS identifiers or evidence panels.

## The research website: `research site`

The website is this guide, served as one Quarto site, plus a page for each experiment you choose to publish. `research site` wraps the guide commands
above (no second system): the same Quarto project, theme, bibliography and the same single GitHub Pages workflow.

```bash
research site init              # preview the files (about, research question, theme, bibliography, .gitignore, Pages workflow)
research site init --apply      # create them; existing files are never overwritten, so it also repairs an older project
research site preview           # live local site with hot reload (http://localhost:4200/); works with no GitHub and no remote
research site build             # static HTML in docs/scientist-guide/_output/scientific/, links and figures checked
research site status            # Quarto, workflow, remote, Pages URL, what is published, and the exact next commands
research site add EXP-001       # publish a verified experiment (see below)
research site remove EXP-001    # unpublish it: previews; add --apply to do it
research site list | sync
```

New projects get all of this when they are created (the guide's init also writes the about and research-question pages from your project record,
with TODOs where the record is empty). **Over SSH or on a cluster login node**, `preview` does not open a browser; it prints the URL and the line to run on
your laptop: `ssh -L 4200:localhost:4200 <user>@<host>`, then open `http://localhost:4200/`.

**Publishing an experiment.** `research site add EXP-001` requires the experiment to be *verified* (every task done and verified by the supervisor; otherwise it
stops and says why, exactly like `research experiment fork`). It reads only what is **committed** at the verified commit (or the commit you name with `--at`, or
`RUN-NNNN`), from `reports/` in the experiment: Quarto chapters (`reports/quarto/chapters/00-smoke.qmd`), figures (png, jpg, svg, gif, webp) and small CSV tables.
They are copied to `docs/scientist-guide/experiments/<title-slug>/` (the slug never contains the experiment id), the chapters join the book, and an editable
`index.qmd` draft is written from the record: question, hypothesis, motivation, method, the recorded steps, limitations, and a **TODO** wherever the record is empty.
Nothing is invented: no results are stated for you and no citation is added. Executable cells in a chapter are shown as code and never run; the website renders
committed results only (`execute: enabled: false`, `--no-execute`, no simulation in the workflow).

Never published, whatever is committed: credentials (`.env`, keys, tokens), logs, notebooks, `ledger/`, `.research/`, agent files, scratch, `hpc/` and `slurm` output, files
over 5 MB (1 MB for tables) and anything whose text looks like a secret. `site add --dry-run` lists what would go and what is excluded and why.

**Research OS only adds.** Running `add` again changes nothing; a generated file you edited is left exactly as it is and reported; the draft page is yours after
it is first written. No command deletes anything. **Unpublishing** (`site remove EXP-001 --apply`) moves the experiment's folder to `_unpublished/<slug>/`
(Quarto never renders `_` folders) and takes its pages out of the book; your edits go with it, and `site add EXP-001` restores it. An experiment you unpublished is not
published again by automatic inclusion. `guide.site.auto_include: true` makes a newly supported finding also publish verified experiments that have committed
reports; it adds only, and never pushes.

**Going live is your decision.** Research OS never creates a repository, commits, pushes or changes GitHub settings. `research site status` prints the next steps:
commit the guide and `.github`, `git push`, and once on GitHub **Settings → Pages → Source: GitHub Actions** (the link is shown when a remote exists). The workflow
(`.github/workflows/publish-guide.yml`, the only one that deploys to Pages, for your default branch, plus manual dispatch) renders what is committed and
deploys it. Links are relative, so a project site at `https://<you>.github.io/<repo>/` works without configuration.

## Updating Quarto

Research OS uses whatever Quarto it finds (`RESEARCH_QUARTO`, `guide.quarto`, `PATH`, `~/.local/bin`, `~/.local/opt/quarto/bin`) and needs **1.4 or newer**.
The Guide page's *Quarto* section shows the version and path in use, and `research guide quarto doctor` prints the same.

| System | Install / update |
|---|---|
| macOS | `brew upgrade --cask quarto` (first time: `brew install --cask quarto`), or the installer from <https://quarto.org/docs/download/> |
| Windows | `winget upgrade --id Posit.Quarto`, or the new installer from the download page |
| Linux | the newest `.deb` / `.rpm` / tarball from the download page (`sudo apt install ./quarto-*.deb`) |

Then press Reload on the Guide page (or run `research guide quarto doctor`). PDF output uses the Typst that ships inside Quarto: nothing else to install.
If a build fails after an update, `quarto check` diagnoses the installation, and `research guide quarto build` prints Quarto's own error.

## Evidence and review (optional rigor)

Two editions are rendered from the same prose. The **scientific edition** (the website) is clean prose. The **researcher edition**
adds, per chapter, an evidence panel: every claim's sources with live state, figures with their data and generator, and the
review status. Provenance is a manifest beside the prose, never inside it.

```bash
research guide claim add "<statement>" --kind observation --section results.qmd --source F-003 --source RUN-0007#loss@last
# mark it in the text:  [statement]{#clm-0001 .claim}
research guide figure add figures/f.svg --fig-kind recreated --section … --label fig-name --title "…" --source RUN-0007#loss
research guide validate        # links resolve; nothing stale, retracted, mismatched or unplaced; no identifiers in scientific prose
research guide section pin results.qmd
research guide review request  # an independent reviewer (verifier role)
research guide review approve results.qmd --by "You"
research guide quarto build --edition researcher
```

| Statement | Produced by | Means |
|---|---|---|
| `guide validate` | deterministic | evidence links resolve and agree. A claim is *traceable*, never "established". |
| `guide review record` | an independent model | judgement against six criteria (evidence fidelity, validity, explanation, uncertainty, history, provenance) |
| `guide review approve` | you | the only thing that labels a chapter *approved*, recorded with your name |

A review or approval is bound to a fingerprint of the chapter text and of every record it cites; when either changes it is reported
`OUTDATED`. The scientific edition refuses to build over provenance errors (for example a retracted claim presented as current)
unless `--allow-invalid`, and a built edition is scanned for leaked identifiers. A successful render is not evidence that the
interpretation is right.

`research guide section set X.qmd --owner human|agent` decides who may rewrite a chapter; for a human-owned one, agents write
suggestions to `_proposals/` (never rendered). Claim kinds: `observation`, `derivation`, `interpretation`, `hypothesis`,
`speculation`, `proposed`. Figure kinds: `archived`, `recreated`, `derived`, `conceptual` (a conceptual figure can never support an
observation). Source refs: `F-003`, `EXP-001/S1`, `F-003/B1`, `RUN-0007#metric@last`, `A-0012`, `D-004`, `Q-002`, `SRC-001`,
`CLM-0003`, `doc:path`.

### Required skills

The `scientific-briefing` skill (the writing rules above, shipped with every project) is **required** for a guide draft: a dispatch
fails if it is missing, and its text is injected and hashed. See `research skills set NAME --level recommended|required|protocol`.
A *direct* Claude Code or Codex session bypasses dispatch and is not gated; use `guide validate` and `guide draft check` on the result.

## Limits

* The structure of the story comes from you (or a draft you accepted). Research OS supplies selection, provenance, limits and checks, not prose.
* The draft limits are checked after the fact, by comparing files; they are written into the brief but an agent can still ignore them. Nothing stops a direct agent session from editing files.
* A word count ignores headings, code, equations and attributes; it is a bound, not a measure of quality.
* No figure is generated by rendering. Recreate a figure with a script that reads recorded values and register it with `--script`/`--input`.
* PDF uses Quarto's bundled Typst, is built for the scientific edition only, and needs a network on first use for some templates.
* Scaffold files written by `guide init` (`filters/edition.lua`, `theme.scss`, the workflow) are never overwritten, so a project created by an earlier build does not receive newer ones automatically.
* A project whose `_quarto.yml` predates 2.2 has no *Key results*, *Notes and ideas* or *References* chapters listed; `guide log` adds `results.qmd` itself, and `research guide chapter add ideas.qmd --owner human` lists the others.
