# 7. Context, checkpoints and reports

## Resume context (`resume_context`): field-by-field

| Field | Computation |
|---|---|
| `project` | name, goal, description, status, root, created_at |
| `git` | current branch, commit, dirty, remote |
| `last_activity` | newest event, excluding `restored`, `import_error` and `reconcile_error`, plus its age in days |
| `latest_checkpoint` | newest checkpoint, with resolved cards, `age_days`, and `stale = age > resume.stale_checkpoint_days (14)` |
| `since_checkpoint` | event count, run count, and new finding, experiment and decision ids created after the checkpoint |
| `baseline` | the experiment with `is_baseline = 1` (most recently updated), with parameters |
| `active_questions` / `open_questions` / `blocked_questions` | questions by status, with experiment counts |
| `established_findings` | `kind=result` and status ∈ {supported, preliminary}; supported first, then most recently updated |
| `failed_directions` | `kind=failure` and not superseded, **or** status = contradicted |
| `current_experiments` | status ∈ {running, needs_review, ready, proposed}, in that order, with state flags |
| `needs_synthesis` | experiments flagged by [Experiment state flags](04-lifecycles.md#experiment-state-flags-experiment_state) or with status `needs_review` |
| `active_runs` | runs that are queued or running |
| `decisions` | active decisions, newest date first |
| `blockers` | in order: the checkpoint's `current_problem` (first); blocked questions; experiments failed since the checkpoint; experiments over the failure budget; the 5 most recent unreviewed failed or unknown runs |
| `suggested_files` | de-duplicated, in order: the latest checkpoint file; pinned notes; artifacts cited as evidence by up to 6 supported findings; up to 2 baseline artifacts (images first, then tables); mirror files of up to 3 experiments needing synthesis; `context/current.md`. Each entry has `exists`. |
| `counts` | totals per entity |
| `recent_events` | last 15 events |
| `policy` | agent_policy |

## Checkpoint draft (`checkpoint_draft`)

| Field | Pre-filled from |
|---|---|
| `title` | `Checkpoint — YYYY-MM-DD` |
| `goal` | project goal |
| `understanding` | bullet list of up to 8 established findings (`- title (F-id, status)`) |
| `baseline` | `EXP-id — title (k=v, …)` |
| `finding_ids` / `failure_ids` | up to 10 each |
| `question_ids` | active, open and blocked questions |
| `experiment_ids` | current experiments |
| `current_problem` | bullet list of blockers |
| `next_experiment` | the first `proposed` experiment, otherwise the most recent synthesis's `next_experiment` |
| git | branch, commit, dirty |

`create_checkpoint(...)` uses any explicitly passed field and fills the rest from the draft
(`use_draft=False` / `--no-draft` disables filling). The VS Code form shows the draft for editing and saves with
`use_draft=False`.

**Git snapshot (opt-in).** `create_checkpoint(commit=True)` (`research checkpoint create --commit`, the form's checkbox, the MCP
`checkpoint_create` tool), or every checkpoint when `git.commit_checkpoints: true`, then runs `gitinfo.snapshot`:
`git add -- .research` and `git commit --only -m "research: CP-00N <title>" -- .research`. Only `.research/` is committed;
anything else you have staged stays staged and uncommitted; nothing is pushed. The `committed` event is logged *before*
the commit, so it is included, and `current.md` ignores the HEAD line when deciding whether to rewrite itself, so the
tree is clean right after a snapshot. Returns `snapshot_commit` (or `null` if there was nothing to commit). The draft
carries `commit_default` (the config value) for the form.

## `.research/context/current.md`

- Regenerated after every mutating CLI command, RPC call and MCP tool, and on `research current --quiet` / `research current`
  (which also prints it).
- It is only rewritten if its content changed, ignoring the timestamp/token line and the `Git:` HEAD line, so git noise is avoided.
- **Budget:** `context.max_chars` (default 12 000 ≈ 3 k tokens). Rendering tries four list-length steps
  (findings/failures/questions/experiments/decisions/blockers: 15/12/20/15/10/10 → 8/6/10/8/5/6 → 5/3/5/5/3/4 →
  3/2/3/3/2/3) and keeps the first that fits; truncated lists end with "… N more (`research … list`)". The header states
  the size (`~N tokens`) and points to the next tiers (`research show`, `research search`, `research task context`).
- Sections:
  - Goal
  - Git
  - Latest checkpoint (understanding, problem, next step, STALE flag)
  - Current baseline
  - Active and open questions
  - Established findings (with evidence ids)
  - Failed directions
  - Current experiments (with unsynthesized counts)
  - Active plans (see below)
  - Needs attention (verification): failed checks, reviewer disagreements, findings awaiting review
  - ⚠ Needs synthesis
  - Active decisions
  - Blockers
  - Project skills: always-on skills with their descriptions, plus a count and how to load the rest
  - Agent policy (incl. the review and checks rules)
- Agents read this file at session start instead of the full history (progressive disclosure).
- **Active plans** section (after Current experiments): up to 3 newest active plans, each with `done/total`, the first line of its
  objective, the next ready task (with a `research task context` hint), and its running (with who claimed it and a
  `STALE, idle Nh` flag), awaiting-verification and blocked tasks (blocked ones with the first line of their blockers).
  `_none_` when there is no active plan.

## Research Home (`research_home`, RPC `home`)

The aggregation behind Research Home, the landing page of the extension. It returns **everything in `resume_context`** plus:

| Field | Computation |
|---|---|
| `active_plan` | the newest plan with status `active`, with its tasks and counts (`get_plan_with_tasks`), or `null` |
| `tasks` | that plan's tasks in creation order |
| `current_task` | the last `running` task in the plan (with dependencies resolved) |
| `next_task` | `get_next_ready_task(active_plan)` |
| `blocked_tasks` | tasks in the plan with status `blocked` |
| `current_focus` | **From the plan, if one is active:** `question` = objective (or title), `understanding` = latest checkpoint's understanding, `next_step` = next ready task's title, `blocker` = first blocked task's `blockers`. **Otherwise, from the latest checkpoint:** goal (or project goal), understanding, `next_experiment`, `current_problem`. `null` if neither exists. Includes `source` (`plan` / `checkpoint`) and `source_id`. |
| `attention_items` | ordered: blocked tasks (`task_blocked`, high); tasks in `verify` (`verification_needed`, medium); experiments over the run or failure budget (`synthesis_required`, high); stale running tasks (`stale_task`, medium, [Coordination between agents](05-agents-governance.md#coordination-between-agents)); failed checks (`verification_failed`, high), reviewer disagreements (`reviewer_disagrees`, high) and agent findings awaiting review (`awaiting_review`, low) ([Verification gates](05-agents-governance.md#verification-gates)); unreviewed failed runs from `blockers` (`failed_run`, medium). Each item has `kind`, `severity`, `id`, `title`, `message`. |
| `key_outputs` | ≤ 6 image or table artifacts: up to 2 cited as evidence by each of the first 5 established findings, then up to 3 of the baseline experiment's newest. Each has `reason`. |
| `findings_by_status` | `supported` / `preliminary` (from the first 6 established findings, with `evidence_count`), and `failed` / `contradicted` (from the first 4 failed directions) |

The CLI has no `home` command. `research status --full` prints the underlying resume context.

## Task context

`task_context`, `research task context`, RPC `task_context`.

A bounded brief for working on one task, so an agent doesn't need the whole history:

| Field | Content |
|---|---|
| `task` | id, title, status, type, role, goal, inputs, expected outputs, acceptance criteria, verification, result, blockers, notes, artifacts, depends_on |
| `is_ready` | [Task](04-lifecycles.md#task) |
| `plan` | id, title, status, objective, success criteria, context |
| `dependencies` | id, title, status and **result** of each dependency |
| `question` | related question: id, title, status, description |
| `experiment` | related experiment: id, title, status, hypothesis, parameters, success criteria, stop conditions, baseline flag, run `state` ([Experiment state flags](04-lifecycles.md#experiment-state-flags-experiment_state)), latest synthesis (interpretation, next) |
| `claimed_by` | who holds the task |
| `checks`, `last_verification` | the checks as text, and the latest check run (id, verdict, summary) |
| `progress` | the 5 latest progress notes |
| `skills` | `skills_for(task, plan)`: name, description, path, why, missing ([Project skills](05-agents-governance.md#project-skills)) |
| `related_findings` | up to 6 findings linked to the related experiment, its runs, or the related question |
| `policy` | agent_policy |

`blockers` is shown only while the task is blocked (the text stays on the task as history). The CLI renders it as
sections and ends with the commands to finish (`task done`) or report a blocker (`task block`); `agents.task_brief_md`
renders the same data as Markdown for dispatch briefs, the MCP `task_context` tool and the extension's *Show Agent Brief*.

## Search (`research search`, RPC `search`, MCP `research_search`)

Keyword retrieval, the on-demand tier below `task context`. Every term must appear (case-insensitive substring) in the
record's title, id or body; score = 3 × title hits + body hits, ties newest first; default limit 20.
Sources: every mirrored entity (title + its Markdown sections), syntheses, runs (label, command, notes, parameters),
artifacts (name, path, description), reviews (summary), notes (file text) and skills (name, description).
`--type` (repeatable) limits the record types. Each hit has `id`, `type`, `title`, `status`, `snippet`, `score`.

## Reports (`research report`, RPC `report`)

`research report [SCOPE] [--format md|html|both] [-o FILE]` writes `.research/reports/<scope or project>.{md,html}`.
SCOPE is a question, plan or experiment id; none means the whole project.

| Part | Content |
|---|---|
| Header | goal, generation time, git branch @ commit |
| Where things stand | (project) latest checkpoint's understanding, open problem, next step, baseline |
| What we learned | findings in scope (supported first): statement, status/confidence, author, review status ("reviewed by …: supported" / "not yet independently reviewed"), evidence ids, limitations, and up to 3 evidence plots (artifact evidence, else the supporting runs' images) |
| Failed directions and contradicted claims | failure-kind and contradicted findings |
| Decisions in force | (project) active decisions with their findings |
| Questions and experiments | per question: description, then each experiment: hypothesis, success criteria, runs table (parameters × metrics, ≤ 20 rows), latest synthesis (what happened, interpretation, what failed, next), up to 4 plots |
| Plans | progress and each task with its result |
| Open questions | (project) |

Images are linked relative to the report file (never copied). HTML is self-contained apart from those images, readable in
light and dark mode. A `report` event is logged.

## Run comparison (`research run compare A B`, RPC `compare_runs`, MCP `runs_compare`)

`compare_runs(a, b)` returns: `parameters` (union, with `same`), `metrics` (latest value per name on each side, `delta` =
b − a and `rel` = delta / |a| for numbers), `meta` (status, exit code, duration, backend, host, SLURM job, branch, commit,
uncommitted changes, command, working dir, label, experiment, start), `same_commit`, `code_diff` (`git diff --stat` and
patch ≤ 200 KB between the two commits, excluding `.research/`, when they differ), `uncommitted` (each run's `git.diff`),
`artifacts` matched by name (`same_hash` when both hashes match), and `changed_parameters`. The CLI prints the
differences; the extension shows a side-by-side page.

## Full deterministic context export (`research report --full`)

**Module:** `research/fullreport.py`. **Export schema:** 1. Independent of `report.py`, whose concise
summary keeps its existing semantics and output paths unchanged.

### What it is

One Markdown document holding the complete recorded scientific context of a project, question, experiment
or plan, suitable for pasting into an LLM. It **retrieves, joins, validates, orders and renders** records
and nothing else: no model call, no agent dispatch, no summarization, no inferred conclusion, no
natural-language rewriting. `fullreport.py` imports no HTTP client and no agent module, and a test asserts
that rendering performs no subprocess and opens no socket.

Defaults to `.research/reports/<scope>-full.md` (`project-full.md` when unscoped). Not subject to
`context.max_chars`, top-N finding selection, or run-table truncation — a large export is expected, and the
document reports its own size and record counts.

### Section hierarchy

Fourteen `##` sections in a fixed order, from `fullreport.SECTIONS` (the renderer emits them and the
coverage checker requires them, so a renamed or dropped section fails verification):

```
1. Export Metadata                     8. Decisions and Supersession History
2. Project Definition and Configuration  9. Checkpoints
3. Current Research State             10. Research Notes
4. Questions and Hypotheses           11. Open Work and Unresolved Problems
5. Plans and Tasks                    12. Audit / Event History
6. Experiments                        13. Data Integrity and Missing Evidence
     Experimental Design              14. Coverage Manifest
     Implementation and Provenance
     Runs · Metrics · Artifacts
     Syntheses
     Linked Findings and Decisions
7. Findings and Reviews
```

Coverage per category: questions, plans, tasks, experiments, runs, metric entries, artifacts, syntheses,
findings, reviews, decisions, checkpoints, notes, literature sources, audit events.

Every record is emitted with a marker comment `<!-- research:record <type> <id> -->`.

### Determinism contract

For a fixed canonical state and fixed options, the output is **byte-identical** across invocations.

| Requirement | How |
|---|---|
| Stable ordering | explicit `ORDER BY id`; events by append position; dicts rendered `sorted()`; documented tie-breakers |
| Stable numbers | `_num`: ints bare, floats via `repr` (shortest round-trip), `x.0` for integral floats, explicit `NaN`/`Infinity` |
| Stable nulls | `_val` renders `None`, `""`, `[]`, `{}` as `—`, always |
| Stable escaping | `_cell` escapes `\` and `|` and flattens newlines to `<br>`, so no value can break a table |
| No random ids | metric records are `RUN-0001#<n>` by position in the run's ordered history, **not** the SQLite rowid; events are `E<n>` by append position — both survive `research rebuild` |
| No wall-clock time | no timestamp unless `--stamp`, which is explicitly labelled non-reproducible |
| Portable paths | project-relative wherever the file is inside the project |
| No filesystem iteration order | every `listdir` is `sorted()` |
| No self-inclusion | the output lives in `.research/reports/`, which is not a scanned record directory |

**Side effects:** none. Generating a report logs **no event**, creates no record, mutates no finding. The CLI
opens the project with `sync=False` for `--full`, so there is no hand-edit import, no run reconciliation and
no background Slurm polling during rendering. `--check` therefore works against a **read-only snapshot**; if
you have hand-edited canonical Markdown, run `research sync` first. An export is only guaranteed repeatable
for the same logical snapshot.

### Completeness verification (`--check`)

`coverage(snapshot, text)` is an **independent** verification path: it parses the record markers out of the
rendered Markdown and compares them with the authoritative id lists from `record_ids(snapshot)` — it does
not trust a counter incremented during printing. It reports, per category, expected vs emitted, plus any
missing, duplicated or unexpected ids, and validates the document's section structure and table rows.

Two categories, never conflated:

- **Exporter failure** — a valid canonical record was omitted, duplicated or rendered unexpectedly. Fails
  verification; `--check` exits non-zero.
- **Source-data incompleteness** — a registered artifact's file is absent, an external dataset is
  unavailable, an experiment was never synthesized, a design field was never filled in. Reported accurately
  in section 13 and in `source_incompleteness`, and **not** treated as an exporter bug.

Fields added by a future schema are preserved generically: entity rows are read straight from SQLite, so a
column this renderer has no section for still reaches the export under "Additional recorded fields".

Nothing claims completeness for unrecorded science or external files; section 13 states explicitly that
artifact contents, logs, external datasets and unwritten reasoning are inherently unavailable.
