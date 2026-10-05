# Research Panel — Technical Specification

**Version:** 0.1.0 · **Schema version:** 1 · **Status:** MVP, all phases 0–7 implemented

This document says exactly what Research Panel does and how it does it. A competent engineer
should be able to re-implement it from this document alone. It is also the reference for
behaviour questions such as "when does an experiment become `needs_review`?" or "what happens if I
delete `research.db`?".

> The README covers installation and day-to-day use, and [`DESIGN.md`](DESIGN.md) gives the
> design rationale in short form. This document is the full specification.

---

## Table of contents

1. [Purpose and principles](#1-purpose-and-principles)
2. [System architecture](#2-system-architecture)
3. [Data model](#3-data-model)
4. [Storage specification](#4-storage-specification)
5. [Synchronisation, rebuild and hand edits](#5-synchronisation-rebuild-and-hand-edits)
6. [Lifecycles and state machines](#6-lifecycles-and-state-machines)
7. [Agent discipline: policy, authorship, protocol](#7-agent-discipline-policy-authorship-protocol)
8. [Execution: runner, local backend, SLURM backend](#8-execution-runner-local-backend-slurm-backend)
9. [Resume, checkpoints and agent context](#9-resume-checkpoints-and-agent-context)
10. [CLI reference](#10-cli-reference)
11. [Python API reference](#11-python-api-reference)
12. [JSON-RPC protocol (extension ↔ core)](#12-json-rpc-protocol-extension--core)
13. [VS Code extension specification](#13-vs-code-extension-specification)
14. [Remote SSH and HPC behaviour](#14-remote-ssh-and-hpc-behaviour)
15. [Safety guarantees](#15-safety-guarantees)
16. [Configuration reference](#16-configuration-reference)
17. [Environment variables](#17-environment-variables)
18. [Testing and verification](#18-testing-and-verification)
19. [Build, packaging and release](#19-build-packaging-and-release)
20. [Repository layout](#20-repository-layout)
21. [Known limitations and roadmap](#21-known-limitations-and-roadmap)

---

## 1. Purpose and principles

### 1.1 Problem

Computational research produces many files and runs but few conclusions. When an agent (or you) has run 40 jobs and
you come back a month later, the reasoning that links them has usually been lost:

- why each run happened,
- which ones mattered,
- what was concluded,
- what failed and should not be repeated,
- where the evidence is.

### 1.2 What it preserves

```
question → experiment → runs → artifacts / metrics → findings → decisions → checkpoint
```

### 1.3 Design principles

| Principle | Consequence in the implementation |
|---|---|
| **Raw activity is subordinate to understanding** | Runs are collapsed under experiments everywhere. The Resume view leads with checkpoint, findings and failures, not run tables. |
| **Project-local and self-contained** | All state lives in `<project>/.research/`. There is no global database, no server and no account. |
| **Text is canonical, SQLite is an index** | Every fact can be reconstructed from Markdown, YAML and JSON files. `research.db` can be deleted at any time. |
| **Reference, never copy** | Artifacts are stored as paths, sizes, mtimes and hashes. Large data stays where it was produced. |
| **Provenance by default** | Every experiment and run records git branch, commit and dirty state. Dirty runs also store `git.diff`. |
| **Human vs agent is always distinguishable** | Every object carries `author_type` (`human`/`agent`/`system`), plus agent name and model. |
| **Agents are bounded** | A configurable run budget hard-stops agents (exit code 3) until they synthesize. |
| **Domain-agnostic** | Nothing assumes ML, PyTorch or imaging. Metrics are arbitrary name/value pairs, and artifacts can be any file type. |
| **Portable** | Paths are stored relative to the project root whenever possible. A project can be moved, cloned or opened over SSH. |

### 1.4 Anti-goals

Research Panel is deliberately **not** any of the following:

- a W&B or MLflow clone
- a workflow orchestrator
- a cloud SaaS
- a Jupyter or Obsidian replacement
- a chatbot
- a data-versioning system
- a container platform

---

## 2. System architecture

### 2.1 Components

```
┌──────────────────────────── VS Code (UI process: laptop) ──────────────────────────────┐
│  sidebar views · webview panels · forms                                                 │
└──────────────────────────────────────┬──────────────────────────────────────────────────┘
                                       │ VS Code remote protocol (local: in-process)
┌──────────────────────────────────────▼──────── machine that holds the files ─────────────┐
│  Extension host  (TypeScript, extensionKind = "workspace")                                │
│    client.ts ── spawns ──▶ python3 -m research.rpc --root <project>   (one per window)    │
│                 stdio, line-delimited JSON-RPC                                            │
│                                                                                            │
│  Python core  (packages/core/research — bundled inside the VSIX under python/)            │
│    store.py     project, config, SQLite index, mirrors sync, rebuild, events             │
│    services.py  questions, experiments, syntheses, findings, decisions, notes, skills    │
│    runs.py      runs, metrics, artifacts, ingestion, reconciliation                       │
│    context.py   resume context, checkpoints, current.md                                   │
│    views.py     detail aggregations + generated Markdown sections                         │
│    backends/    local.py · slurm.py         runner.py  (job wrapper)                      │
│                                                                                            │
│  CLI  (packages/cli/research_cli)  ── same core, same files                               │
│                                                                                            │
│  <project>/.research/   ← single source of truth                                         │
│  git · sbatch/squeue/sacct/scancel                                                        │
└───────────────────────────────────────────────────────────────────────────────────────────┘
          │ sbatch
┌─────────▼──────── compute node ───────────┐
│ python -m research.runner --run-dir …      │  writes ONLY into .research/runs/RUN-xxxx/
└────────────────────────────────────────────┘
```

### 2.2 Why there is a Python core and a TypeScript UI

- **All business logic is in Python** (standard library only, Python ≥ 3.8; checked with `vermin`, which reports a minimum of 3.7).
- The extension renders data and calls the core. It never reads SQLite or writes `.research/` files itself, except to read previews (artifact heads and log tails).
- **One process boundary.** The extension spawns a single long-lived `research.rpc` process per window, so the Python start-up cost is paid once.
- **Zero install.** The VSIX contains `python/research` and `python/research_cli`, and the extension sets `PYTHONPATH` to that folder.
- **YAML.** The system PyYAML is used if it is importable. Otherwise a vendored pure-Python PyYAML 6.0.3 (MIT) in `research/_vendor/yaml` is used.

### 2.3 Process model

| Process | Lifetime | Touches SQLite? |
|---|---|---|
| `research.rpc` (one per VS Code window) | lifetime of the window; restarted automatically if it dies (up to 2 quick retries) | yes |
| `research` CLI invocation | one command | yes |
| `research.runner` (local detached, or inside a SLURM job) | one run | **no**: run directory files only |
| Python code inside a run calling `research.log_metric()` | user code | **no** when `RESEARCH_RUN_DIR` is set |

---

## 3. Data model

### 3.1 Identifiers

| Entity | Prefix | Width | Example |
|---|---|---|---|
| Question | `Q` | 3 | `Q-001` |
| Experiment | `EXP` | 3 | `EXP-012` |
| Run | `RUN` | 4 | `RUN-0044` |
| Finding | `F` | 3 | `F-019` |
| Decision | `D` | 3 | `D-002` |
| Checkpoint | `CP` | 3 | `CP-004` |
| Artifact | `A` | 4 | `A-0107` |
| Synthesis | — | — | `EXP-012/S2` |
| Note | — | — | `note:<file-stem>` |

- IDs are allocated from a per-prefix counter in a write transaction (`BEGIN IMMEDIATE`). The counter never goes below
  max(existing)+1, and it skips any number whose mirror file or run directory already exists on disk.
- **Normalisation:** input is case-insensitive, accepts `-`, `_`, space or no separator, and ignores leading zeros.
  `exp-1`, `EXP1` and `exp_001` all mean `EXP-001`. A bare number is accepted where the type is implied
  (`research experiment show 3`). Passing the wrong prefix for a slot is an error, e.g. `F-1` where an experiment is expected.

### 3.2 Common fields

Every first-class object has:

| Field | Meaning |
|---|---|
| `author_type` | `human` · `agent` · `system` |
| `author_name` | agent name (e.g. `claude-code`), or `$RESEARCH_AUTHOR` for humans |
| `author_model` | agent model, if known |
| `created_at`, `updated_at` | ISO-8601 with local UTC offset, second precision |

### 3.3 Entities

#### Question

| Field | Type | Notes |
|---|---|---|
| `title` | text | required |
| `description` | text (Markdown) | |
| `status` | enum | `open` · `investigating` · `answered` · `blocked` · `abandoned` |
| `parent_id` | Q id | sub-questions |
| `tags` | list | |

#### Experiment

An experiment is a designed test, not a process execution.

| Field | Type | Notes |
|---|---|---|
| `title` | text | required |
| `question_id` | Q id | |
| `parent_id` | EXP id | "derived from" (variants) |
| `hypothesis`, `motivation`, `method`, `expected_outcome`, `success_criteria`, `stop_conditions` | Markdown | the design, ideally written before running |
| `limitations`, `notes` | Markdown | written after the fact |
| `parameters` | JSON object | scalars or lists (a list means a sweep) |
| `param_delta` | JSON object | `{key: {from, to}}`, set for variants |
| `metrics_requested` | list | metric names to evaluate |
| `expected_artifacts` | list | file names or descriptions |
| `planned_runs` | int | |
| `is_baseline` | bool | at most one experiment is the baseline |
| `status` | enum | `proposed` · `ready` · `running` · `needs_review` · `completed` · `failed` · `abandoned` |
| `git_branch`, `git_commit`, `git_dirty` | | captured at registration |
| `started_at`, `completed_at` | timestamps | |
| `tags` | list | |

#### Run

A run is one execution. It always belongs to an experiment.

| Field | Notes |
|---|---|
| `experiment_id` | required (enforced in schema and API) |
| `label` | short human label, e.g. `alpha=0.5` or `sanity` |
| `command` | shell command string (argv lists are joined with `shlex.quote`) |
| `working_dir` | project-relative path (absolute if outside the project) |
| `parameters` | JSON object |
| `env` | `{python: $CONDA_DEFAULT_ENV or $VIRTUAL_ENV, user}` |
| `backend` | `local` · `slurm` · `manual` (attached) |
| `hostname`, `pid` | |
| `slurm_job_id`, `slurm` | `slurm = {requested:{partition,account,time,cpus,mem,gpus,extra_args}, script, state, nodelist, partition}` |
| `run_dir` | `.research/runs/RUN-xxxx` |
| `stdout_path`, `stderr_path` | default `<run_dir>/stdout.log` and `stderr.log`; attached runs may point anywhere |
| `git_branch`, `git_commit`, `git_dirty` | captured at creation; `git.diff` saved if dirty |
| `status` | `queued` · `running` · `completed` · `failed` · `cancelled` · `unknown` |
| `exit_code`, `started_at`, `ended_at` | `duration_s` is derived |
| `reviewed` | bool. A failed run counts against the failure budget until reviewed (synthesis marks it reviewed). |
| `override_reason` | set if a budget override was used |
| `notes` | |

#### Metric

| Field | Notes |
|---|---|
| `run_id` or `experiment_id` | exactly one owner |
| `name` | arbitrary |
| `value` | REAL. Non-numeric values go to `value_text`. |
| `step` | optional int |
| `unit` | optional |
| `timestamp` | |
| `author_type` | |

A run's "latest" value for a metric is the row with the highest `step` (rows without a step sort first), ties broken by insertion order.

#### Artifact

| Field | Notes |
|---|---|
| `run_id`, `experiment_id` | either or both. If a run is given, the experiment is derived from it. |
| `name` | defaults to the basename |
| `type` | inferred from the extension (see 3.4) or set explicitly |
| `path` | project-relative if inside the project, otherwise absolute. `external = true` when absolute. |
| `description`, `tags` | |
| `size` | bytes; for directories, the sum over up to 20 000 files |
| `mtime` | |
| `hash` | sha256 if size ≤ `artifacts.hash_max_bytes` (default 64 MiB) or `--hash` is given |
| `preview` | JSON; for directories `{files: n}` |

Registering the same `(path, run, experiment)` again **updates** the existing artifact instead of creating a duplicate.

#### Synthesis

An interpretation of an experiment's runs, appended to history. Synthesis IDs look like `EXP-001/S1`, `S2`, …

| Field | Notes |
|---|---|
| `what_happened`, `what_worked`, `what_failed`, `interpretation`, `limitations`, `unresolved`, `next_experiment` | at least one of the first four is required |
| `runs_covered` | the list of finished run ids at synthesis time |

Syntheses are **not** promoted to findings automatically.

#### Finding

| Field | Notes |
|---|---|
| `title` | short, required |
| `statement` | the precise claim (defaults to the title) |
| `kind` | `result` or `failure` (failure means a failed direction: don't repeat) |
| `status` | `preliminary` · `supported` · `contradicted` · `superseded` |
| `confidence` | `low` · `medium` · `high` (optional) |
| `limitations`, `contradicting_evidence` | Markdown |
| `superseded_by` | F id |
| links | `supports[]`, `contradicts[]`, `related[]` → any of EXP/RUN/A/F; `questions[]` → Q (relation `addresses`) |

All linked ids are validated to exist when the finding is created or updated.

#### Decision

| Field | Notes |
|---|---|
| `statement` | required; `title` defaults to its first 120 chars |
| `reason` | |
| `status` | `active` · `reversed` · `superseded` |
| `date` | YYYY-MM-DD, default today |
| `superseded_by` | |
| links | `supporting_findings[]` (relation `based_on`), `experiments[]` (relation `related`) |

Creating a decision with `supersedes=D-x` marks D-x as `superseded` and sets its `superseded_by`.

#### Checkpoint

A snapshot of understanding.

| Field | Notes |
|---|---|
| `title` | |
| `goal`, `understanding`, `baseline`, `current_problem`, `next_experiment`, `notes` | Markdown |
| `baseline_experiment_id` | |
| `finding_ids`, `failure_ids`, `question_ids`, `experiment_ids` | JSON lists |
| `git_branch`, `git_commit`, `git_dirty` | |

#### Note

Notes are file-only (no DB row), stored at `.research/notes/<slug>.md`.

- Front-matter: `title`, `pinned` (bool), `links` (ids), `created`.
- The id is `note:<stem>`.
- Listing order: pinned first, then most recently modified.

#### Project

The project is not a table. Its metadata lives in `config.yaml → project`: `name`, `description`, `goal`, `status`,
`created_at`, `git_repo` (the origin URL detected at init), `default_backend`.

### 3.4 Artifact type inference

| Extensions | Type |
|---|---|
| png jpg jpeg gif svg webp bmp tif tiff | image |
| pdf | pdf |
| csv tsv parquet | table |
| json jsonl | json |
| md | markdown |
| txt yaml yml | text |
| log out err | log |
| npy npz | array |
| nii, nii.gz | nifti |
| h5 hdf5 | hdf5 |
| nc | netcdf |
| pt pth ckpt safetensors | checkpoint |
| pkl | pickle |
| mp4 mov webm | video |
| py sh jl | code |
| ipynb | notebook |
| html | html |
| a directory | directory |
| other extension | binary |
| no extension | file |

### 3.5 Relationships (the `links` table)

`links(src_type, src_id, dst_type, dst_id, relation, note)`, with primary key `(src_id, dst_id, relation)`.

| Relation | From → to |
|---|---|
| `supports` / `contradicts` / `related` | finding → experiment/run/artifact/finding |
| `addresses` | finding → question |
| `based_on` | decision → finding |
| `related` | decision → experiment |

Structural parent links (question→question, experiment→question, experiment→experiment, run→experiment,
artifact→run/experiment) are columns, not rows in `links`.

---

## 4. Storage specification

### 4.1 Project layout

```
<project>/
├── AGENTS.md                         # research block between <!-- research:begin … --> and <!-- research:end -->
└── .research/
    ├── config.yaml                   # project metadata, agent_policy, backends, artifacts, resume
    ├── .gitignore                    # research.db*, cache/, runs/*/stdout.log, runs/*/stderr.log
    ├── research.db                   # SQLite index (derived)
    ├── events.jsonl                  # append-only audit log
    ├── artifacts.jsonl               # append-only artifact records (last record per id wins)
    ├── syntheses.jsonl               # append-only syntheses
    ├── metrics.jsonl                 # experiment-level metrics (run metrics live in run dirs)
    ├── questions/Q-001.md            # mirrors (Markdown + YAML front-matter)
    ├── experiments/EXP-001.md
    ├── findings/F-001.md
    ├── decisions/D-001.md
    ├── checkpoints/CP-001.md
    ├── runs/RUN-0001/
    │   ├── run.json                  # full run record (rewritten by login-node processes)
    │   ├── status.json               # written by the runner (state, pids, host, times, exit code)
    │   ├── stdout.log  stderr.log
    │   ├── metrics.jsonl             # metric records (canonical for run metrics)
    │   ├── artifacts.jsonl           # artifacts registered from inside the job (pending ids)
    │   ├── command.sh                # re-run script (cd relative to project root)
    │   ├── git.diff                  # only if the tree was dirty at launch (≤ 2 MiB, lists untracked files)
    │   ├── job.sbatch  slurm-<id>.out  runner.err
    ├── notes/*.md
    ├── skills/<name>/SKILL.md        # + any scripts/
    ├── prompts/*.md
    ├── templates/*.md
    ├── context/current.md            # regenerated agent summary
    └── cache/                        # research.db backups from `rebuild`
```

### 4.2 What is canonical

| Data | Canonical store | Index |
|---|---|---|
| Questions, experiments, findings, decisions, checkpoints | Markdown mirror files | `questions`, `experiments`, `findings`, `decisions`, `checkpoints` tables |
| Links | mirror front-matter (`supports`, `supporting_findings`, …) | `links` |
| Runs | `runs/RUN-x/run.json` (+ `status.json` for live state) | `runs` |
| Run metrics | `runs/RUN-x/metrics.jsonl` | `metrics` |
| Experiment metrics | `metrics.jsonl` | `metrics` |
| Artifacts | `artifacts.jsonl` | `artifacts` |
| Syntheses | `syntheses.jsonl` | `syntheses` |
| Audit log | `events.jsonl` | `events` |
| Notes, skills, prompts, templates | the files themselves | none |
| Project metadata, policy | `config.yaml` | none |

### 4.3 Mirror file format

```markdown
---
id: EXP-002
title: Locate the stability edge
status: proposed
question: Q-001
derived_from: EXP-001
baseline: false
parameters:
  alpha: [1.2, 1.5]
  steps: 60
param_delta:
  alpha: {from: [0.1, 0.5, 1.0], to: [1.2, 1.5]}
metrics: [final_error, oscillation, runtime]
expected_artifacts: [plot.png]
planned_runs: 3
tags: []
git_branch: master
git_commit: c6c5d8d7…
git_dirty: false
author_type: human
author_name: null
author_model: null
created_at: '2026-09-29T18:21:05+00:00'
updated_at: '2026-09-29T18:21:05+00:00'
started_at: null
completed_at: null
---

# EXP-002 · Locate the stability edge

## Hypothesis

Error is U-shaped in alpha …

## Motivation
…
## Notes

<!-- research:generated — everything below is regenerated; edits here are ignored -->

## Status
…runs table, artifacts, syntheses, findings produced…
```

**Rules:**

- **Front-matter key names.** These map to DB columns. Renamed keys are: `question` → `question_id`, `derived_from` →
  `parent_id`, `metrics` → `metrics_requested`, `baseline` → `is_baseline`, `parent` (questions) → `parent_id`.
  For checkpoints: `important_findings`, `known_failures`, `open_questions`, `active_experiments`, `baseline_experiment`.
- **Sections.** Each long-text field is a `## <Heading>`, with headings matched case-insensitively:

  | Entity | Sections |
  |---|---|
  | Question | Description |
  | Experiment | Hypothesis, Motivation, Method, Expected outcome, Success criteria, Stop conditions, Known limitations, Notes |
  | Finding | Statement, Limitations, Contradicting evidence |
  | Decision | Decision, Reason |
  | Checkpoint | Current goal, Current understanding, Baseline, Current problems / blockers, Next step, Notes |

  An unknown `##` heading inside a known section stays part of that section's text. `###` and deeper are ordinary content.
- **The `# ID · title` H1 is ignored on import.** The title comes from front-matter.
- **Everything after the generated marker is ignored on import** and rewritten on every export.
- **Rendering is deterministic.** A file is only rewritten when its bytes would change, so git diffs stay minimal.

### 4.4 JSONL record schemas

```jsonc
// events.jsonl
{"ts","entity_type","entity_id","action","summary","author_type","author_name"}
// action ∈ created, updated, edited (hand edit), status, baseline, synthesized, superseded, registered,
//          started, submitted, completed, failed, cancelled, unknown, override, restored, conflict,
//          import_error, reconcile_error, initialized

// artifacts.jsonl (one full record per registration/update; last record per id wins)
{"id","run_id","experiment_id","name","type","path","external","description","size","mtime","hash",
 "tags","preview","author_type","author_name","author_model","created_at"}

// syntheses.jsonl
{"id":"EXP-001/S1","experiment_id","what_happened","what_worked","what_failed","interpretation",
 "limitations","unresolved","next_experiment","runs_covered":[…],"created_at", author…}

// metrics.jsonl (run dir or root)
{"name","value"|"text","step"?,"unit"?,"ts","author_type","experiment_id"? (root only)}

// runs/RUN-x/artifacts.jsonl (written inside jobs; no ids yet)
{"path"(absolute),"name","description","type","tags","ts"}
```

### 4.5 `status.json` (runner-owned)

```json
{"state": "running|completed|failed|cancelled", "runner_pid": 123, "pid": 124, "hostname": "node01",
 "started_at": "…", "ended_at": "…", "exit_code": 0,
 "slurm_job_id": "4242", "slurm_nodelist": "node01", "slurm_partition": "gpu"}
```

### 4.6 SQLite index

- **Tables:** `meta`, `counters`, `questions`, `experiments`, `findings`, `decisions`, `checkpoints`, `syntheses`, `runs`,
  `metrics`, `artifacts`, `links`, `events`, `mirrors(path, entity_type, entity_id, hash, mtime, size)`.
- **Indices:** `runs(experiment_id)`, `metrics(run_id)`, `metrics(experiment_id)`, `artifacts(run_id)`,
  `artifacts(experiment_id)`, `links(dst_id)`, `events(ts)`.
- **Connection pragmas:** `journal_mode=DELETE` (WAL is unsafe on NFS and Lustre), `busy_timeout=15000`, `foreign_keys=OFF`
  (integrity is enforced in the services layer). Autocommit, with explicit `BEGIN IMMEDIATE` transactions that nest by depth counting.
- **Migrations:** `schema.MIGRATIONS = {version: [DDL…]}`, applied in order inside a transaction. `meta.schema_version` records
  the current version. A project whose version is **newer** than the tool is refused with an "upgrade" error, never modified.
- `meta.built_at` marks a fully built index. If it is missing, a rebuild runs.

### 4.7 Path portability rules

| Situation | Stored form |
|---|---|
| Path inside the project root | project-relative, `/`-separated (`outputs/run_004/results.npy`) |
| Path outside the project | absolute (`/scratch/user/big.npy`), `external = true` |
| Working directory | relative if inside the project |
| `command.sh` | `cd "$(dirname "$0")/../../.." && cd <relative wd>`, so it works after moving the project |
| `status.json` | contains no paths |
| `job.sbatch` | contains absolute paths. It is a per-submission artifact for one machine and is never read back. |

The test suite asserts that no Markdown, JSON, JSONL or YAML file and no DB row contains the old absolute project path after a move.

---

## 5. Synchronisation, rebuild and hand edits

### 5.1 When sync runs

- At the start of every CLI command (opening a `Project`).
- Before every RPC request except `ping`, `init` and a few read-only helpers.
- On demand: `research sync`, the VS Code **Refresh** command, and the file watcher (which triggers a refresh, which triggers a sync).

### 5.2 Sync algorithm (`Project.sync`)

1. **Mirrors.** For each entity directory and each `*.md` file:
   1. If `mtime` and `size` equal the recorded values, skip it.
   2. Hash the file. If the hash equals the recorded hash, update mtime and size and stop.
   3. Otherwise **import** it:
      - Parse front-matter and sections.
      - Validate statuses. An invalid status is ignored and the previous value kept.
      - Compute the changed fields and changed links, then update the row.
      - Log an `edited` event authored `human`.
      - Re-export to refresh the generated tail.
   4. **Unknown files** (no matching id, e.g. `findings/my-idea.md`) are imported as new entities. They get the next id,
      default status, `kind=result`, and `author_type=human` unless the front-matter says otherwise. The file is then
      rewritten in canonical form *at its existing path*.
   5. **Malformed YAML**: the DB keeps its old values, an `import_error` event is logged, and nothing crashes.
2. **Deleted mirrors.** If an entity row exists but its mirror file is gone, the file is regenerated and a `restored`
   event is logged. Records are never silently dropped.
3. **Root JSONL ingestion.** `artifacts.jsonl`, `syntheses.jsonl` and `metrics.jsonl` are read from the stored byte offset
   (`meta.offset:<file>`). Upserts are idempotent. A file that shrank (e.g. after `git checkout`) is re-read from 0.
4. **Runs.** See below.

### 5.3 Run sync

1. Import unknown run directories (those with a `run.json` but no row), e.g. after `git pull` or a rebuild.
2. For each run whose `metrics.jsonl` or `artifacts.jsonl` grew past its stored offset, ingest the new lines. In-job
   artifacts are given an `A-` id and appended to the root `artifacts.jsonl`.
3. Reconcile every non-terminal run (§8.5). SLURM queries are throttled to once every 20 s unless forced.

### 5.4 Import-before-export

Before any mirror is written, its on-disk hash is compared with the last recorded hash. If they differ, the hand edit
is imported **first**, so a concurrent edit is never overwritten.

### 5.5 Rebuild (`research rebuild`, or automatic when `research.db` is missing)

1. Move the existing DB to `.research/cache/research.db.bak-YYYYmmdd-HHMMSS` (skipped for the automatic first build).
2. In one transaction:
   - import every mirror file
   - import every run directory, re-reading run metrics from offset 0 and restoring the artifact offset from `run.json`
   - ingest the root JSONL files
   - load `events.jsonl`
3. If two files claim the same id, keep the first and log a `conflict` event naming the file to rename.
4. Recompute counters, set `meta.built_at`, then re-export all mirrors. Only files whose bytes change are written.

---

## 6. Lifecycles and state machines

### 6.1 Question

- Statuses are free to move in any direction (`open` · `investigating` · `answered` · `blocked` · `abandoned`).
- **Automatic transition:** registering an experiment under an `open` question sets it to `investigating`.

### 6.2 Experiment

```
proposed ─┐
ready ────┼──(first run queued/running)──▶ running ──(all runs finished, none active)──▶ needs_review
needs_review/completed ──(new run started)──▶ running
proposed/ready ──(a finished run attached)──▶ needs_review
any ──(explicit)──▶ completed | failed | abandoned        (sets completed_at)
any ──(explicit)──▶ running                               (sets started_at if unset)
```

- **Synthesis:** `synthesize(..., mark=completed|failed)` sets that status. Without `mark`, an experiment in
  `running`, `ready` or `proposed` with finished runs and no active runs moves to `needs_review`.
- **UI rule:** marking an experiment completed or failed while it has unsynthesized runs prompts "Synthesize first / Mark anyway".

### 6.3 Experiment state flags (`experiment_state`)

| Flag | Definition |
|---|---|
| `finished` | runs with status ∈ {completed, failed, cancelled, unknown} |
| `active` | runs with status ∈ {queued, running} |
| `unsynthesized` | finished runs not in the union of all syntheses' `runs_covered` |
| `unreviewed_failed` | failed runs with `reviewed = 0` |
| `needs_synthesis` | `unsynthesized > 0` and `active == 0` |
| `over_run_budget` | `unsynthesized ≥ max_runs_without_synthesis` |
| `over_failure_budget` | `unreviewed_failed ≥ max_failed_runs_without_review` |

### 6.4 Run

```
queued ──▶ running ──▶ completed (exit 0) | failed (exit ≠ 0) | cancelled (signal / scancel)
running ──(runner process vanished on this host, no exit recorded)──▶ unknown
queued/running (SLURM) ──(left queue; sacct TIMEOUT/OOM/NODE_FAIL/…)──▶ failed
```

- Reaching a terminal state triggers the experiment's automatic transitions (§6.2).
- Synthesis sets `reviewed = 1` on all failed runs of that experiment.

### 6.5 Finding and decision

- **Finding:** `preliminary ⇄ supported`, `→ contradicted`, `→ superseded` (via supersede, which records `superseded_by`).
- **Decision:** `active → reversed | superseded`.
- History is never rewritten. Superseding keeps the old object visible under "Contradicted & superseded".

---

## 7. Agent discipline: policy, authorship, protocol

### 7.1 Authorship detection (`detect_author`)

Priority order:

1. Explicit arguments: CLI `--agent`, `--model`, `--as`.
2. `RESEARCH_AUTHOR_TYPE`, `RESEARCH_AGENT`, `RESEARCH_AGENT_MODEL`.
3. Auto-detection of known agents:
   - `CLAUDECODE=1` or `CLAUDE_CODE_ENTRYPOINT` → agent `claude-code`
   - `CODEX_SANDBOX…` → agent `codex`
4. Otherwise `human`.

The VS Code extension strips `CLAUDECODE` and `RESEARCH_AGENT` from the RPC process environment, so **actions taken in the UI
are always recorded as human**, even if VS Code itself was launched from an agent's shell. Terminals keep the user's
environment, so an agent running in a VS Code terminal is detected as an agent.

### 7.2 Policy (`config.yaml → agent_policy`)

| Key | Default | Enforcement |
|---|---|---|
| `max_runs_without_synthesis` | 5 | `create_run`/`exec`/`submit`/`attach` raise `BudgetExceeded` when `unsynthesized ≥ N` |
| `max_failed_runs_without_review` | 3 | same, when `unreviewed_failed ≥ M` |
| `require_experiment_registration` | true | structural: a run cannot exist without an experiment |
| `require_synthesis_before_new_experiment` | true | `create_experiment` raises if *any* experiment has unsynthesized runs |
| `enforce_for_humans` | false | if false, humans get warnings (returned in `warnings`, printed in yellow) instead of errors |

- **Exit code 3** (`BudgetExceeded`) lets agents detect the block programmatically. The message names the experiment and the fix.
- **Override:** `--override "reason"` (CLI), `override=` (API), or the "Run anyway" button in the UI. Each override logs an
  `override` event with the reason, and the run stores `override_reason`.
- **Visibility:**
  - ⚠ on experiment tree items
  - a badge on the Experiments view
  - a warning-coloured status bar item
  - a banner on the Resume view and on the experiment page
  - a "Needs synthesis" section in `current.md`

### 7.3 `AGENTS.md`

`research init` (and `research agents-md`) creates `AGENTS.md`, or updates **only** the text between
`<!-- research:begin` and `<!-- research:end -->`. User content outside the markers is preserved byte for byte.
The block covers:

- start-of-session steps (read `current.md`, run `research resume`, set `RESEARCH_AGENT`)
- the 9-step loop (UNDERSTAND → PLAN → REGISTER → IMPLEMENT → SANITY CHECK → RUN → COLLECT EVIDENCE → SYNTHESIZE → STOP/PROPOSE),
  with exact CLI commands
- hard rules:
  - no invisible experiments
  - budgets
  - no unbounded sweeps
  - findings need evidence; failures are findings
  - decisions
  - checkpoints
  - never silently rewrite history
  - never write the human's notes
  - never copy large outputs
- a CLI and Python reference

### 7.4 Default skills (`.research/skills/`)

| Skill | Purpose |
|---|---|
| `run-experiment` | Register → sanity run → bounded runs → artifacts → synthesize → stop. |
| `synthesize-experiment` | Turn runs into a synthesis, promote only reusable findings, decide, mark the experiment, refresh context. |
| `reproduce-paper` | Six-phase reproduction protocol (understand, plan, implement, sanity run, reproduce, synthesize) with a stop rule. |
| `clean-dataset` | Treat cleaning or conversion as an experiment with validation artifacts and exclusion findings. |

Prompts: `resume-session.md`, `synthesize.md`. Templates: `experiment-brief.md`, `note.md`.
`init` never overwrites existing files in these folders.

---

## 8. Execution: runner, local backend, SLURM backend

### 8.1 Run creation (`create_run`)

1. Check the budget (§7.2).
2. Capture git info. Allocate a `RUN-` id. Create the run directory.
3. Insert the row and log a `created` event.
4. If git is dirty, write `git.diff`:
   - `git diff HEAD -- . ':(exclude).research'`, capped at 2 MiB
   - plus a commented list of untracked files
5. Write `command.sh` and `run.json`.
6. Apply experiment transitions (§6.2) and regenerate the experiment mirror.

**Dirty** means tracked changes outside `.research/`, ignoring untracked files (`git status --porcelain --untracked-files=no`).

### 8.2 The runner (`python -m research.runner --run-dir D [--cwd W] [--tee] -- "<cmd>"`)

- Has no SQLite and no project imports beyond the standard library, so it is safe on compute nodes.
- Runs `bash -c "<cmd>"` (or `/bin/sh` if bash is missing) in a **new session** (process group). The environment adds
  `RESEARCH_RUN_ID`, `RESEARCH_RUN_DIR` and `RESEARCH_IN_JOB=1`.
- **Before start**, writes `status.json`: `state=running`, `runner_pid`, child `pid`, `hostname`, `started_at`, and the SLURM
  env vars if present.
- stdout and stderr are appended to `stdout.log` and `stderr.log`. With `--tee` they are also streamed to the terminal by
  pump threads.
- SIGTERM, SIGINT and SIGUSR1 are forwarded to the child's process group, and the final state becomes `cancelled`.
- **On exit**, writes `state` (`completed` if the exit code is 0, `failed` otherwise, `cancelled` if signalled), `exit_code`
  and `ended_at`. If the command cannot be started, `exit_code=127` and `state=failed`.
- All JSON writes are atomic (temp file + `os.replace`).

### 8.3 Local backend

| Mode | Behaviour |
|---|---|
| **Foreground** (`research run exec`) | The runner is called in-process with `tee=True`, then reconciled. The CLI's exit status is 0 for completed, otherwise the run's exit code. |
| **Detached** (`--detach`, VS Code "Background") | `Popen([python, -m, research.runner, …], start_new_session=True)` with `PYTHONPATH` pointing at the core. stdin is `/dev/null`, runner errors go to `runner.err`. The run survives the terminal, VS Code and SSH disconnects. |
| **Status** | If `status.json` is terminal, use it. Otherwise, if the run was on **this** host and its pid is dead, the state is `unknown` ("runner process disappeared…"). Runs on another host are left alone. |
| **Cancel** | `SIGTERM` to the runner pid, which forwards it to the job. Refused if the run is on another host. |

### 8.4 SLURM backend

**Submission** (`research run submit` / `run_start(backend=slurm)`):

1. Merge `config.backends.slurm` with per-call options (per-call wins; empty values ignored).
2. Write `job.sbatch`:
   ```bash
   #!/bin/bash
   #SBATCH --job-name=RUN-0007
   #SBATCH --output=<abs run_dir>/slurm-%j.out
   #SBATCH --partition=…  --account=…  --time=…  --cpus-per-task=…  --mem=…  --gres=gpu:N
   #SBATCH <each extra_args entry>
   cd '<abs working dir>'
   <setup lines, verbatim>
   export PYTHONPATH='<core parent>'"${PYTHONPATH:+:$PYTHONPATH}"
   exec '<python>' -m research.runner --run-dir '<abs run dir>' --cwd '<abs wd>' -- '<command>'
   ```
   - `gpus: 1` becomes `--gres=gpu:1`, and `gpus: "a100:2"` becomes `--gres=a100:2`.
   - `python` is `backends.slurm.python` if set, otherwise the submitting interpreter (`sys.executable`). This assumes a
     shared filesystem, which is standard on HPC.
3. Run `sbatch --parsable job.sbatch`. The job id is the text before any `;cluster` suffix.
4. Store `slurm_job_id` and `slurm.requested`, set status `queued`, log a `submitted` event.
5. If submission fails (e.g. `sbatch` is missing), the run is marked `failed` with a note and the error is raised with a hint.

**Status reconciliation**, first source that answers wins:

1. `status.json` is terminal: use it (exact exit code, host, times).
2. `squeue -h -j ID -o "%T|%N|%S"` returns a line: map the state and record the nodelist.
3. `sacct -n -P -j ID -o JobID,State,ExitCode,Start,End,NodeList` (the line whose JobID matches exactly): map the state,
   exit code and times. This covers jobs killed before the runner could write, such as TIMEOUT or out-of-memory.
4. Otherwise the job left the queue with no record: `unknown`.

**State mapping:**

| SLURM states | Run status |
|---|---|
| PENDING, CONFIGURING, REQUEUED, RESIZING, SUSPENDED | queued |
| RUNNING, COMPLETING, STAGE_OUT | running |
| COMPLETED | completed |
| FAILED, TIMEOUT, NODE_FAIL, OUT_OF_MEMORY, BOOT_FAIL, DEADLINE, PREEMPTED | failed |
| CANCELLED (incl. "CANCELLED by uid"), REVOKED | cancelled |
| anything else | unknown |

**Cancel:** `scancel ID`.

### 8.5 Reconciliation (`reconcile_run`)

1. Ingest new metrics and artifacts from the run directory.
2. Ask the backend (or `status.json` for manual runs).
3. Merge changes: `status`, `exit_code`, `started_at`, `ended_at`, `hostname`, `slurm_job_id`, and `slurm.{nodelist, state, partition}`.
4. A terminal status without an end time gets `ended_at = now`.
5. Log a system event, for example `RUN-0004 completed (exit 0)`.
6. Rewrite `run.json` and apply the experiment transitions.

The extension polls `run_refresh` every `research.pollIntervalSeconds` (default 10 s) **only while active runs exist**.

### 8.6 Metrics and artifacts from inside a run

`research.log_metric(name, value, step=None, unit=None)` and `research.register_artifact(path, …)` called **with no
explicit run or experiment** and with `RESEARCH_RUN_DIR` set:

- append to `<run_dir>/metrics.jsonl` or `artifacts.jsonl` (artifact paths are made absolute)
- touch no database
- are ingested by the next sync or status check on a machine that has the index

Outside a run, they write through the project, which appends to JSONL and then ingests immediately.

---

## 9. Resume, checkpoints and agent context

### 9.1 Resume context (`resume_context`): field-by-field

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
| `needs_synthesis` | experiments flagged by §6.3 or with status `needs_review` |
| `active_runs` | runs that are queued or running |
| `decisions` | active decisions, newest date first |
| `blockers` | in order: the checkpoint's `current_problem` (first); blocked questions; experiments failed since the checkpoint; experiments over the failure budget; the 5 most recent unreviewed failed or unknown runs |
| `suggested_files` | de-duplicated, in order: the latest checkpoint file; pinned notes; artifacts cited as evidence by up to 6 supported findings; up to 2 baseline artifacts (images first, then tables); mirror files of up to 3 experiments needing synthesis; `context/current.md`. Each entry has `exists`. |
| `counts` | totals per entity |
| `recent_events` | last 15 events |
| `policy` | agent_policy |

### 9.2 Checkpoint draft (`checkpoint_draft`)

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

### 9.3 `.research/context/current.md`

- Regenerated after every mutating CLI command and every mutating RPC call, and on `research context`.
- It is only rewritten if its content changed, ignoring the timestamp line, so git noise is avoided.
- Sections:
  - Goal
  - Git
  - Latest checkpoint (understanding, problem, next step, STALE flag)
  - Current baseline
  - Active and open questions
  - Established findings (with evidence ids)
  - Failed directions
  - Current experiments (with unsynthesized counts)
  - ⚠ Needs synthesis
  - Active decisions
  - Blockers
  - Agent policy
- Agents read this file at session start instead of the full history (progressive disclosure).

---

## 10. CLI reference

**Global flags** (before or after the subcommand):

| Flag | Meaning |
|---|---|
| `--json` | machine-readable output |
| `--root DIR` | project root |
| `--agent NAME`, `--model M` | act as this agent |
| `--as human\|agent\|system` | author type |
| `--version` | print version |

- **Project discovery:** `--root`, else `$RESEARCH_ROOT`, else walk up from the cwd to the nearest `.research/config.yaml`.
- **Exit codes:** `0` ok · `1` error · `2` not initialized · `3` blocked by run budget or policy · `4` not found · `130` interrupted.
- **Output:** colour only when stdout is a TTY and `NO_COLOR` is unset. Errors go to stderr. With `--json`, errors are
  printed as `{"error","hint","code"}` on stderr.
- **Command separator:** everything after the first `--` is the command to run, passed verbatim and quoted with `shlex`.
- **`K=V` parsing:** integers, floats (`3e-4`), `true`/`false`, `null`/`none`, JSON lists or objects (`[0.1,0.5]`), otherwise text.

| Command | Purpose / key options |
|---|---|
| `research init [PATH] [--name] [--goal] [--description] [--no-agents-md]` | create `.research/` and the AGENTS.md block; idempotent |
| `research status` | compact status: counts, active runs, needs-synthesis, current experiments, latest checkpoint |
| `research resume` | the full "where was I" view (§9.1) |
| `research context [--print]` | regenerate `current.md` |
| `research show ID` | any object, including `note:…` |
| `research mark ID STATUS [--reason]` | set status of a question, experiment, finding, decision or run |
| `research log [--limit 30]` | audit log |
| `research sync` | §5.2 |
| `research rebuild` | §5.5 |
| `research agents-md` | refresh the AGENTS.md block |
| `research project set [--name] [--goal] [--description] [--status]` | edit project metadata in config.yaml |
| `research question create TITLE [-d] [--parent] [--status] [--tags…]` | aliases `q`, `new` |
| `research question list [--status]` · `update ID …` · `show ID` | |
| `research experiment create TITLE [-q Q] [--hypothesis] [--motivation] [--method] [--expected] [--success] [--stop] [--param K=V]… [--metric M]… [--expect-artifact F]… [--planned-runs N] [--parent EXP] [--status proposed\|ready] [--tags…] [--notes] [--override R]` | aliases `exp`, `e` |
| `research experiment list [--status] [-q]` · `show ID` · `update ID …` | |
| `research experiment variant ID [--param K=V]… [--title] [--motivation] [--hypothesis] [--override]` | alias `duplicate`; records the delta |
| `research experiment start\|ready\|review\|complete\|fail\|abandon ID [--reason]` | status shortcuts |
| `research experiment baseline [ID] [--unset]` | |
| `research experiment synthesize ID [--happened] [--worked] [--failed] [--interpretation] [--limitations] [--unresolved] [--next] [--complete\|--fail]` | alias `synth` |
| `research experiment run EXP … -- CMD` | same as `run exec` |
| `research run exec EXP [--param]… [--label] [--cwd] [--notes] [--override] [--detach] -- CMD` | local; foreground streams output |
| `research run submit EXP [-p PART] [-A ACCT] [-t TIME] [-c CPUS] [--mem] [-G GPUS] [--sbatch-arg ARG]… … -- CMD` | SLURM |
| `research run attach EXP [--command] [--status] [--exit-code] [--stdout] [--stderr] [--started] [--ended] [--slurm-job] [--param]… [--label] -- [CMD]` | alias `add`; register a run you already did |
| `research run list [-e EXP] [--status] [--limit]` · `status ID` (alias `show`; reconciles) | |
| `research run update ID [--status] [--exit-code] [--notes] [--label] [--param] [--reviewed]` · `cancel ID` · `sync` | |
| `research run logs ID [--stderr] [--tail N] [-f]` | `-f` follows until the run ends |
| `research metric add NAME VALUE (--run R \| -e EXP) [--step] [--unit]` | alias `log` |
| `research artifact add PATH… [--run\|-e] [--name] [--type] [-d] [--tags…] [--hash] [--allow-missing]` | alias `register` |
| `research artifact list [--run\|-e]` | |
| `research finding create TITLE [-s STATEMENT] [--kind result\|failure] [--status] [--confidence] [--supports ID…] [--contradicts ID…] [--related F…] [--questions Q…] [--limitations] [--contradicting TEXT] [--tags…]` | alias `f` |
| `research finding list [--status] [--kind]` · `update ID … [--add-supports ID…]` · `supersede ID [--by F] [--reason]` · `show ID` | |
| `research decision create STATEMENT [--reason] [--title] [--findings F…] [--experiments E…] [--date] [--supersedes D]` | alias `d` |
| `research decision list [--status]` · `update ID …` | |
| `research checkpoint [create] [--title] [--goal] [--understanding] [--baseline] [--baseline-experiment] [--findings…] [--failures…] [--questions…] [--experiments…] [--problem] [--next] [--notes] [--no-draft]` | a bare `research checkpoint` creates one |
| `research checkpoint list` · `draft` · `show [ID]` (default: latest) | |
| `research note new TITLE [--body] [--link ID…] [--pin]` · `list` · `pin ID [--unpin]` · `link ID TARGET… [--remove]` | |
| `research skills list` · `new NAME [-d]` · `duplicate SRC NAME` | |

After every **mutating** command, `current.md` is regenerated.

---

## 11. Python API reference

`import research`. Every function accepts `project=Project(...)`. Otherwise it opens the project found from the cwd or
`$RESEARCH_ROOT`, syncs it, and closes it again.

| Function | Notes |
|---|---|
| `init(root=".", name=None, goal=None) -> Project` | |
| `open_project(root=None) -> Project` | context manager (`with research.open_project() as p:`) |
| `get_resume_context()` / `get_project_context()` | §9.1 dict |
| `regenerate_context() -> path` | |
| `show(id)` | detail dict for any id |
| `list_questions(status=None)` · `create_question(title, description=None, parent=None, status="open", tags=None)` · `update_question(id, **fields)` | |
| `create_experiment(title, question=None, hypothesis=None, motivation=None, method=None, expected_outcome=None, success_criteria=None, stop_conditions=None, parameters=None, metrics=None, expected_artifacts=None, planned_runs=None, parent=None, status="proposed", tags=None, notes=None, override=None)` | |
| `create_variant(source, params=None, title=None, motivation=None, hypothesis=None, override=None, **extra)` | |
| `get_experiment(id)` (full detail) · `list_experiments(status=None, question=None)` · `update_experiment(id, **fields)` | |
| `set_status(id, status, reason=None)` · `set_baseline(id or None)` | |
| `synthesize(exp, what_happened=…, what_worked=…, what_failed=…, interpretation=…, limitations=…, unresolved=…, next_experiment=…, mark=None)` | |
| `create_run(experiment, command=None, parameters=None, label=None, working_dir=None, backend="manual", status="queued", notes=None, override=None, stdout_path=None, stderr_path=None, exit_code=None, started_at=None, ended_at=None, host=None, slurm_job_id=None)` | registers only; does not execute |
| `attach_run(experiment, command=None, status="completed", **kw)` · `update_run(id, status=None, exit_code=None, notes=None, label=None, parameters=None, reviewed=None)` | |
| `exec_run(experiment, command, detach=False, parameters=None, label=None, working_dir=None, notes=None, override=None, tee=True)` · `submit_run(experiment, command, **slurm_opts)` · `cancel_run(id)` | |
| `get_run(id)` · `list_runs(experiment=None, status=None, limit=None)` | |
| `log_metric(name, value, step=None, unit=None, run=None, experiment=None)` | in-job mode in §8.6 |
| `register_artifact(path, run=None, experiment=None, name=None, type=None, description=None, tags=None, hash=None, allow_missing=False)` | |
| `list_artifacts(run=None, experiment=None)` | |
| `create_finding(title, statement=None, kind="result", status="preliminary", confidence=None, supports=None, contradicts=None, related=None, questions=None, limitations=None, contradicting_evidence=None, tags=None)` | |
| `update_finding(id, supports=None, contradicts=None, related=None, questions=None, add_supports=None, **fields)` · `supersede_finding(id, by=None, reason=None)` · `list_findings(status=None, kind=None)` | |
| `create_decision(statement, reason=None, title=None, supporting_findings=None, experiments=None, date=None, status="active", tags=None, supersedes=None)` · `list_decisions(status=None)` | |
| `create_checkpoint(**fields, use_draft=True)` · `checkpoint_draft()` · `list_checkpoints()` | |
| `create_note(title, body="", links=None, pinned=False)` · `list_notes()` · `list_skills()` | |

**Exceptions:** `ResearchError` (`.exit_code`, `.hint`), and its subclasses `NotInitialized` (2), `BudgetExceeded` (3)
and `NotFound` (4).

---

## 12. JSON-RPC protocol (extension ↔ core)

**Transport:** the child process's stdin and stdout, one JSON object per line, UTF-8.

```
→ {"id": 7, "method": "show", "params": {"id": "EXP-001"}}
← {"id": 7, "result": {...}}
← {"id": 7, "error": {"message": "...", "hint": "...", "code": 4, "kind": "NotFound"}}
```

- **Handshake:** on start the server emits `{"event":"ready","version","root","initialized"}`.
- **Error codes:** core exit codes (1–4); `-32700` invalid JSON; `-32601` unknown method; `-32602` bad params; `-32603`
  internal error (includes a `trace`, which is logged to the "Research" output channel).
- Every method except `ping` and `init` fails with code 2 if the project is not initialized.
- Every method except `ping`, `init`, `index`, `config`, `update_project`, the note and skill helpers, `sync`, `rebuild`,
  `events`, `agents_md` and `run_refresh` performs a full sync (§5.2) first.
- After a **mutating** method (`create`, `update`, `set_status`, `set_baseline`, `synthesize`, `supersede_finding`, `run_*`,
  `register_artifact`, `log_metric`, `init`, `update_project`, `sync`, `rebuild`), `current.md` is regenerated.

| Method | Params → result |
|---|---|
| `ping` | → `{version, root, initialized, python, pid}` |
| `init` | `{name?, goal?}` → `{root, name}` |
| `resume` | → resume context (§9.1) |
| `tree` | → everything the sidebar needs in one round-trip: project, questions, experiments (with `state`, `runs[]` each with `artifacts[]`, experiment-level `artifacts[]`), findings, decisions, checkpoints, notes, artifacts, skills, `agent{context,prompts,templates}`, `needs_synthesis[]` |
| `index` | → `{items:[{id,type,title,status,…}], statuses}` (used by pickers) |
| `show` | `{id}` → detail view for any type |
| `config` | → merged config |
| `update_project` | `{name?, goal?, description?, status?}` |
| `create` | `{type: question\|experiment\|variant\|finding\|decision\|checkpoint\|note\|skill, …fields}` → created object |
| `update` | `{type: question\|experiment\|finding\|decision, id, …fields}` |
| `set_status` | `{id, status, reason?}` |
| `set_baseline` | `{id \| null}` |
| `synthesize` | `{id, what_happened…, mark?}` |
| `supersede_finding` | `{id, by?, reason?}` |
| `duplicate_skill` | `{src, name}` |
| `pin_note` | `{id, pinned}` |
| `link_note` | `{id, targets, remove?}` |
| `checkpoint_draft` | → §9.2 |
| `experiment_state` | `{id}` → §6.3 |
| `check_run_budget` | `{experiment}` → `{ok, messages[], state}` (does not raise) |
| `run_attach` | `{experiment, …}` |
| `run_start` | `{experiment, command, backend: local\|slurm, parameters?, label?, working_dir?, override?, slurm?:{…}}`: local is detached |
| `run_create` | register only |
| `run_cancel` | `{id}` |
| `run_update` | `{id, …}` |
| `run_refresh` | `{id?}`: force-reconcile one run, or all runs with SLURM throttling off |
| `register_artifact` | `{path, run?, experiment?, …}` |
| `log_metric` | `{name, value, run?, experiment?, step?, unit?}` |
| `write_context`, `agents_md`, `sync`, `rebuild` | maintenance |
| `events` | `{limit}` |

A params object may carry `author: {agent?, model?, author_type?}` to override authorship for that request.

---

## 13. VS Code extension specification

### 13.1 Manifest essentials

| Key | Value |
|---|---|
| id | `research-local.research-panel` |
| engine | `^1.85.0` |
| `extensionKind` | `["workspace"]` |
| `main` | `dist/extension.js` (esbuild bundle) |
| activation | `workspaceContains:.research/config.yaml`, `onStartupFinished` |
| when-clause contexts | `research.ready` (core started) and `research.initialized` (project exists) gate the views, welcome content and palette entries |

### 13.2 Activation sequence

1. **Choose the project root:**
   - the `research.projectRoot` setting (absolute, or relative to the first folder), else
   - the first `file:` workspace folder containing `.research/config.yaml`, else
   - the first `file:` folder.
   With no folder open, only a stub `research.init` command is registered.
2. **Detect Python:** `research.pythonPath`, otherwise `python3`, then `python` (on Windows `py`, `python`, `python3`).
   Each candidate is checked to be ≥ 3.8, and the resolved `sys.executable` is used.
3. **Locate the core:** `<ext>/python` (packaged), or `<repo>/packages/core` + `packages/cli` (dev).
4. **Install the terminal shim** (§13.8). Create the client, model, panels, views, status bar, commands and watchers.
5. **Initial refresh:** `ping`, then (if initialized) `tree`, `resume` and `index` in parallel. On error, show a
   notification with **Show log** and **Set Python path** actions.

### 13.3 Model and refresh

- `Model.refresh()` fetches `tree`, `resume` and `index` and fires `onChange`. All views and panels re-render from it.
  Calls are coalesced: one refresh in flight at a time, with a single queued follow-up.
- **Refresh triggers:**
  - a file watcher on `.research/**` (ignoring `research.db*`, `cache/`, temp files and `events.jsonl`), debounced 400 ms
  - a watcher on `AGENTS.md` and `config.yaml`
  - window focus
  - after every UI mutation
  - the Refresh command
- **Polling:** while `resume.active_runs` is non-empty, `run_refresh` is called every `pollIntervalSeconds`.
  Polling stops automatically when no runs are active.

### 13.4 Views (activity bar container "Research")

| View | Type | Content and behaviour |
|---|---|---|
| **Overview** | webview | Goal (or "Set a project goal"); **Resume Project** button; stats (questions, experiments, findings); banners for needs-synthesis and active runs; latest checkpoint card (problem 🔥 → next step →); **Create Checkpoint**; active questions (5), current experiments (6), latest findings (4), current decisions (3); an empty-state call to action. `initialSize: 3`. |
| **Questions** | tree | Top-level questions sorted investigating → open → blocked → answered → abandoned. Children are sub-questions, then that question's experiments (full experiment nodes). Investigating questions start expanded. |
| **Experiments** | tree | ≤ 5 experiments: a flat list (active first). More than 5: groups **In progress** (expanded), **Completed**, **Failed & abandoned** (collapsed). Each experiment's children: a "Needs synthesis" item (if flagged; clicking it opens the synthesis form), a **Runs** group (collapsed unless a run is active; newest first; each run's children are its artifacts), and experiment-level artifacts. If there are no runs, a "No runs yet — Run…" item. View badge = needs-synthesis count. |
| **Findings** | tree | Groups: **Established** (supported results), **Preliminary**, **Failed directions** (failure kind, not superseded), **Contradicted & superseded** (collapsed). Descriptions show confidence, evidence count and agent name. |
| **Decisions** | tree | All decisions; non-active ones show their status. |
| **Checkpoints** | tree | Newest first; the latest is marked "latest" and coloured green. |
| **Notes** | tree | Pinned first; clicking opens the Markdown file; inline pin/unpin. |
| **Artifacts** | tree | All artifacts, newest first; missing files flagged red. |
| **Agent Context** | tree | `AGENTS.md`, `config.yaml`, **Skills** (description as label suffix; multi-file skills expand), Project context, Prompts, Templates. |

**Icons and colours** (codicons with chart theme colours):

| Entity | Status → icon |
|---|---|
| Experiment | proposed: grey beaker · ready: blue beaker · running: spinning sync (yellow) · needs_review: orange bell · completed: green ✓ · failed: red ⊗ · abandoned: grey ⊘ |
| Run | queued: clock · running: spin · completed: ✓ · failed: ✗ · cancelled: ⊘ · unknown: red ? |
| Finding | supported: green verified · preliminary: yellow lightbulb · failure kind: red ✗ · contradicted: orange warning · superseded: grey history |
| Question | open: blue ? · investigating: yellow eye · answered: green ✓ · blocked: red · abandoned: grey |
| Decision | active: green law · reversed / superseded: grey |

Tooltips are Markdown: hypothesis, parameters, run counts, statement and limitations.

### 13.5 Webview panels

One panel per object; re-opening reveals the existing panel. Alt/Cmd/Ctrl-click opens beside the current editor.

| Panel | Sections |
|---|---|
| **Resume** (`Cmd/Ctrl+Alt+R`, status bar click) | Hero (eyebrow name, goal, last activity + author, git, toolbar: Create Checkpoint · New Question · New Experiment · New Finding · Open current.md); banners (no checkpoint / stale or >25 changes / needs synthesis with "Synthesize EXP-x" / runs in flight); **Where we left off** (Current understanding + baseline, Current problem, Next step, "since this checkpoint" line); **What we believe** · **What failed — don't repeat**; **Current baseline** + **Decisions in force** · **Open questions**; **Experiments in progress** · **Needs attention**; **Look at these first** · **Recent activity**; stat tiles that focus the matching views. |
| **Experiment** | Breadcrumb (question, "derived from"); title, status, baseline, author, tags; meta (runs vs planned, created, closed, git); toolbar (Run… · Attach run · Synthesize · Finding · Variant · Status ▾ · More ▾); budget or synthesis banner; design card + sidebar (parameters with delta strikethrough, delta card, metrics, expected artifacts, variants); **Runs table** (status dot, id, label, failure exit pill, dirty and agent markers, one column per parameter and metric, inline bars, lowest value highlighted, duration, backend/job, hover actions for stdout, stderr and cancel); **metric charts** (one horizontal bar chart per metric when ≥ 2 numeric values, labelled by the varying parameters); **artifact gallery** (thumbnails for images, type icons otherwise); **Syntheses** (newest first; agent ones tinted purple and labelled "agent-generated synthesis"); findings produced; decisions & linked notes; limitations and notes; activity timeline. |
| **Finding** | Kind, status, confidence meter and author pills; superseded banner; statement as a large quote (colour by state); supporting evidence list (with a warning banner if empty); "evidence at a glance" image gallery; contradicting evidence; limitations (with a prompt if empty); experiments; decisions based on this; related findings; history. Toolbar: Edit · Decision from this · Status ▾ · More ▾. |
| **Question** | Description, sub-questions, experiments (run and synthesis counts), "What we learned" findings, linked notes, history. |
| **Decision** | Statement quote, Why, based-on findings, experiments, history. |
| **Checkpoint** | Goal, understanding and baseline; problems; next step; notes; important findings, known failures, open questions, active experiments (baseline starred). |
| **Run** | Status and exit pills, timings, host and job, git; toolbar (stdout · stderr · Run dir · Terminal here · Add metric · Register artifact · Cancel · Refresh); command with a copy button; working dir and `git.diff` link; parameters; latest metrics; SLURM block; artifacts; stdout and stderr tails (last ~6 KB each); citing findings. |
| **Artifact** | Type, missing and external pills; path; description; **preview**; metadata (path, absolute path, size, mtime, sha256 prefix, registered); citing findings. |

**Preview adapters** (`PREVIEW_ADAPTERS` in `panels.ts`; first match wins):

| Adapter | Behaviour |
|---|---|
| image | rendered through `asWebviewUri`, with a cache-busting mtime query |
| CSV / TSV | quote-aware parser, first 200 rows from the first 256 KB |
| JSON | pretty-printed if ≤ 400 KB, else raw head |
| Markdown | rendered |
| text / log / code | first 200 KB |
| NPY | header (dtype and shape) |
| directory | first 300 entries |
| anything else | metadata + Open / Reveal / Copy path |

**Rendering details:**

- **Markdown** in all text fields: paragraphs, lists, code fences, inline code, bold and italic, and links. Every research id
  (`EXP-001`, `RUN-0004`, …) in any text is auto-linked and clickable.
- **Live updates:** a visible panel re-renders through `postMessage` (scroll position preserved). A hidden panel is fully
  re-rendered when it becomes visible again.

### 13.6 Forms

Rendered from a schema (`render/form.ts`) and driven by `media/ui.js`.

| Field type | Notes |
|---|---|
| `text`, `textarea` | textareas auto-size |
| `number`, `select` | |
| `seg` | segmented control with semantic colours |
| `check` | |
| `list` | comma-separated |
| `entity`, `entities` | searchable picker; filtered by type; keyboard ↑ ↓ Enter, Backspace removes a chip; free-typed ids accepted |
| `kv` | parameter editor; values are parsed like the CLI; rows that differ from the base are highlighted with "was …" |
| `hidden` | |

Required-field validation, `Cmd/Ctrl+Enter` to submit, an inline error line (core error message + hint), and a busy spinner.

| Form | Prefill / special behaviour |
|---|---|
| New question | optional parent |
| Register experiment | groups What / Design / Parameters & evidence; optional question or parent prefill |
| Edit experiment / question / finding / decision | an emptied field **clears** the value |
| Variant | the parent's parameters as base; only changed keys are sent; the delta is recorded |
| Synthesize | runs listed as placeholder, hypothesis shown; mark completed/failed; optional "create a finding next" |
| Run | prefilled from the last run's command, params and working dir (or the experiment's params, taking the first element of list values); mode Terminal / Background / SLURM; SLURM resource group (prefilled from config) shown only in SLURM mode. A budget pre-check before opening offers Synthesize or Run anyway (logged override). |
| Attach run | outcome, exit code, label, params, log paths, SLURM job id, notes |
| New finding | kind, status, confidence; evidence pickers (supports, contradicts, questions, related); scope |
| New decision | statement, reason, based-on findings, experiments; supersede mode |
| Create checkpoint | prefilled from `checkpoint_draft`; groups Understanding / State / Pointers |

**Terminal mode:** opens a terminal named `▶ EXP-x · label` with the shim on `PATH` and sends
`research run exec EXP-x --param … --label … [--override …] --cwd <wd> -- <command>`. The run streams live there.

### 13.7 Commands

All commands are under the category **Research**.

| Command | Purpose |
|---|---|
| Initialize Project | name + goal prompts → `init` |
| Resume Project | `Cmd/Ctrl+Alt+R` |
| Refresh | reconcile runs + refresh |
| Create Research Checkpoint | form |
| New Question / New Experiment / New Finding / New Decision / New Note / New Skill | |
| Create Variant · Run… · Synthesize Experiment · Mark Experiment Complete / Failed | |
| Attach Existing Run · Cancel Run · Open Run Log (scrolls to the end) · Refresh Run Status | |
| Register as Research Artifact | also in the Explorer and editor-tab context menus; multi-select supported; asks for target run/experiment and a description |
| Add Metric · Change Status… · Set as Baseline · Edit… · Supersede Finding | |
| Open Details · Open Markdown File · Open File · Reveal in Explorer · Copy ID · Copy Path · Open Terminal Here | |
| Pin / Unpin / Link Note · Duplicate Skill | |
| Regenerate Agent Context · Open AGENTS.md · Open Project Config · Set Project Goal | |
| Rebuild Index from Files | modal confirmation |
| Show Log | the "Research" output channel |

Item-only commands are hidden from the palette. Inline tree actions: Run ▶ and Synthesize ✦ on experiments; logs and
cancel on runs; pin on notes.

### 13.8 Terminal shim

- **Location:** `<globalStorage>/bin/research`, which is on the extension host machine (the remote host under SSH).
- **Contents:**
  ```sh
  #!/bin/sh
  PYTHONPATH=<ext>/python${PYTHONPATH:+:$PYTHONPATH} exec <python> -m research_cli "$@"
  ```
- **PATH:** prepended to every integrated terminal through `environmentVariableCollection` (disable with
  `research.terminalCommand: false`). Not installed on Windows.

### 13.9 Status bar

- **Text:** `$(beaker) <project>`, then `$(sync~spin) N` while runs are active, then `$(warning) N` for experiments needing
  synthesis. The background turns warning-coloured when anything needs synthesis.
- **Tooltip:** goal, counts, latest checkpoint.
- **Click:** opens Resume.

### 13.10 Webview security

- **CSP:**
  ```
  default-src 'none'; img-src <cspSource> https: data:; style-src <cspSource> 'unsafe-inline';
  font-src <cspSource>; script-src 'nonce-…'
  ```
- **`localResourceRoots`:** extension `media/`, the project root, and the parent directories of external artifacts
  (added dynamically per panel when they are rendered).
- No remote scripts or fonts are loaded. Codicons are bundled.
- All interpolated text is HTML-escaped. JSON embedded for pickers escapes `<`.

### 13.11 Settings

| Setting | Default | Meaning |
|---|---|---|
| `research.pythonPath` | `""` | Python ≥ 3.8 on the workspace host. Empty means auto-detect. Changing it prompts a reload. |
| `research.terminalCommand` | `true` | Add `research` to terminal `PATH`. |
| `research.pollIntervalSeconds` | `10` (min 3) | Status refresh interval while runs are active. |
| `research.defaultRunMode` | `terminal` | `terminal` · `background` · `slurm`. |
| `research.projectRoot` | `""` | Override root detection. |

### 13.12 Design system (`media/ui.css`)

- **Colours:** built only on VS Code theme variables (`--vscode-*`), so it follows every light, dark and high-contrast theme.
  - surfaces: `color-mix` elevations of editor background and foreground
  - semantic colours: `--vscode-charts-{green,yellow,red,orange,blue,purple}`
  - pill text: mixed 78 % with the foreground for contrast
- **Components:**
  - pills, id chips, author badges (human outline / agent purple)
  - confidence meter (3 bars)
  - cards (tone = left border, tint = background)
  - list items, buttons (primary / secondary / ghost / icon)
  - dropdown menus, banners, stat tiles, tables (sticky headers, hover actions), gallery thumbnails (checkerboard backdrop)
  - timeline, SVG bar charts, synthesis blocks, forms (groups, rows, segmented controls, pickers, kv rows, sticky action bar)
  - toast
- **Layout:** pages max 1120 px (narrow pages 820 px); `.grid2` reflows at 340 px columns; the split layout collapses
  below 860 px; the sidebar variant is compact.

---

## 14. Remote SSH and HPC behaviour

- `extensionKind: workspace` means the extension host, and therefore the RPC process, git, SQLite, the file watchers,
  `sbatch` and all artifact reads, run on the **remote** machine. The laptop only renders. Images reach webviews through
  VS Code's resource tunnelling (`asWebviewUri`).
- The extension must be installed on the remote side once (`code --install-extension …` from a Remote terminal, or
  "Install in SSH: host").
- **SQLite on NFS/Lustre** is handled by the rollback journal, busy timeout and short write transactions. Compute nodes
  never open the DB (§2.3, §8.6), so concurrent jobs cannot corrupt or lock it.
- **Disconnects:** detached and SLURM runs keep running. Status is reconstructed from files and SLURM accounting on the
  next connection.
- **Verified** in code-server (VS Code 1.117 server/browser split, the same architecture as Remote SSH), with the project
  moved to a new path and the index deleted (§18).

---

## 15. Safety guarantees

1. **Never deletes or moves user data.** Artifacts are references. `rebuild` backs up the DB before replacing it.
   No command deletes runs, artifacts or files.
2. **Never commits** to git, and never modifies the repo except `AGENTS.md` (managed block only) and `.research/`.
3. **Never silently rewrites history.**
   - Every mutation, including hand edits picked up by sync, is an event in `events.jsonl`.
   - Superseding is the only way to "replace" a finding or decision.
   - Missing mirror files are regenerated and logged.
4. **Never overwrites a hand edit** (import-before-export, §5.4).
5. **Malformed input never corrupts the index**: a bad file is skipped and logged.
6. **Version safety:** an older tool refuses a newer schema.
7. **Atomic writes** for every generated file (temp file + `os.replace`).

---

## 16. Configuration reference (`.research/config.yaml`)

```yaml
project:
  name: alpha-sweep demo
  description: ""
  goal: "Understand how the step size alpha trades convergence speed against stability…"
  status: active
  created_at: "2026-09-29T18:21:00+00:00"
  git_repo: git@github.com:you/repo.git      # detected at init
  default_backend: local
agent_policy:
  max_runs_without_synthesis: 5
  max_failed_runs_without_review: 3
  require_experiment_registration: true
  require_synthesis_before_new_experiment: true
  enforce_for_humans: false
backends:
  slurm:
    partition: null
    account: null
    time: "01:00:00"
    cpus: null
    mem: null
    gpus: null            # 1 → --gres=gpu:1 ; "a100:2" → --gres=a100:2
    extra_args: []        # each becomes an "#SBATCH <arg>" line
    setup: []             # shell lines before the command (module load …, conda activate …)
    # python: /path/to/python   # optional; default = submitting interpreter
artifacts:
  hash_max_bytes: 67108864
resume:
  stale_checkpoint_days: 14
```

Missing keys fall back to these defaults (deep merge), so old configs keep working.
`research project set` and the UI's "Set Project Goal" rewrite the file, keeping a short header comment.

---

## 17. Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `RESEARCH_ROOT` | core | project root override |
| `RESEARCH_AGENT`, `RESEARCH_AGENT_MODEL` | core | mark the actor as an agent |
| `RESEARCH_AUTHOR_TYPE` | core | `human` / `agent` / `system` |
| `RESEARCH_AUTHOR` | core | human display name |
| `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` | core | auto-detect Claude Code |
| `CODEX_SANDBOX`, `CODEX_SANDBOX_NETWORK_DISABLED` | core | auto-detect Codex |
| `RESEARCH_RUN_ID`, `RESEARCH_RUN_DIR`, `RESEARCH_IN_JOB` | set by the runner | enable in-job logging (§8.6) |
| `SLURM_JOB_ID`, `SLURM_JOB_NODELIST`, `SLURM_JOB_PARTITION` | runner | recorded in `status.json` |
| `CONDA_DEFAULT_ENV`, `VIRTUAL_ENV`, `USER` | core | stored in `run.env` |
| `NO_COLOR` | CLI | disable ANSI colour |

---

## 18. Testing and verification

| Suite | Command | What it proves |
|---|---|---|
| Core + CLI + RPC + SLURM (pytest, 40 tests) | `python -m pytest -q` | init structure and AGENTS.md preservation; migrations and version refusal; journal mode; id normalisation; question hierarchy and editing; experiment git capture and question auto-transition; variants and delta; run → artifact → metric chain; artifact de-dup; external paths; finding evidence, failure kind, decision links, supersession; authorship detection; run budget (agent block, override logged, human warnings, failure budget, synthesis-before-new-experiment); local foreground, detached and failed runs with logs, in-job metrics and artifacts; dirty-tree `git.diff`; hand-edit import, malformed-file safety, dropped-in file import, deleted-mirror restore; **full rebuild from text files**; **relocation with no absolute-path leakage**; checkpoint draft and resume; notes; skills; SLURM script generation, submission, completion through a simulated job, TIMEOUT via sacct, RUNNING via squeue, cancel, missing sbatch; RPC round-trip and uninitialized handling; CLI end-to-end flow; CLI exit code 3 for agents; not-initialized exit code 2 |
| TypeScript | `npm run typecheck` | the extension compiles under `strict` |
| Clean install | `bash scripts/clean_install_test.sh` | fresh venv + wheel → full protocol (question, experiment, 2 runs, artifact, synthesis, finding, decision, checkpoint) → copy to a new path, delete the index → resume reconstructs exact counts → artifact paths relative and resolving → no build-machine paths in the project, installed package or VSIX |
| UI end-to-end (Playwright + code-server) | `python scripts/e2e_code_server.py <project> <dir>` | VSIX installed into a real VS Code server; sidebar trees and overview render; New Question form writes `Q-003`; question panel opens; Run form → terminal → `RUN-0004` completes; experiment panel shows the new run (live refresh). 8/8 checks. |
| Real VS Code harness | `RESEARCH_TEST_WORKSPACE=<demo> npm run test:ext` | downloads VS Code, installs the VSIX, runs ~30 checks in the extension host (state reconstruction, every panel command, every view, shim, watcher, authorship). Needs access to `update.code.visualstudio.com`. |
| Visual review | `scripts/preview-entry.ts` + themes | renders every webview outside VS Code with dark and light theme variables for screenshots (`docs/screens/`) |

---

## 19. Build, packaging and release

```bash
pip install -e ".[dev]"        # editable CLI + pytest
npm install                    # extension dev deps (root postinstall)
npm run build                  # esbuild → packages/vscode-extension/dist/extension.js (+ copies codicons)
npm test                       # pytest + typecheck
npm run package                # scripts/package.sh → dist/
```

**`scripts/package.sh`:**

1. Builds the extension in production mode.
2. Runs `scripts/bundle-python.mjs`, which copies `packages/core/research` and `packages/cli/research_cli` into
   `packages/vscode-extension/python/`.
3. Runs `vsce package --no-dependencies` → `dist/research-panel-X.Y.Z.vsix` (~225 KB).
4. Runs `python -m build` → wheel + sdist in `dist/`. If `build` is missing, it falls back to `pip wheel`.

**Versioning:** bump `version` in `pyproject.toml`, `packages/vscode-extension/package.json` and `research/__init__.py`
together. Schema changes add a new entry to `schema.MIGRATIONS` and increment `SCHEMA_VERSION`. Never edit existing entries.

**Release checklist:**

1. `npm test`
2. `npm run package`
3. `bash scripts/clean_install_test.sh`
4. optional UI E2E
5. tag
6. attach `dist/*` to a GitHub release

---

## 20. Repository layout

```
research-vscode/
├── README.md                       # user guide
├── LICENSE                         # MIT
├── package.json                    # root scripts: build / test / package / demo
├── pyproject.toml                  # Python distribution "research-panel" → `research` entry point
├── docs/
│   ├── SPEC.md                     # this document
│   ├── DESIGN.md                   # condensed design rationale
│   └── screens/                    # UI screenshots
├── packages/
│   ├── core/research/
│   │   ├── __init__.py             # public Python API
│   │   ├── schema.py               # entity specs, ids, DDL, migrations
│   │   ├── store.py                # Project: config, index, mirrors, sync, rebuild, events, AGENTS.md
│   │   ├── mirrors.py              # Markdown render/parse
│   │   ├── services.py             # questions, experiments, budget, syntheses, findings, decisions, notes, skills
│   │   ├── runs.py                 # runs, metrics, artifacts, ingestion, reconciliation
│   │   ├── context.py              # resume, checkpoint draft/create, current.md
│   │   ├── views.py                # detail aggregations, generated mirror sections
│   │   ├── rpc.py                  # JSON-RPC server
│   │   ├── runner.py               # job wrapper
│   │   ├── gitinfo.py  yamlio.py  util.py
│   │   ├── backends/ (base, local, slurm)
│   │   ├── templates/ (AGENTS.block.md, skills/, prompts/, templates/)
│   │   └── _vendor/yaml/           # PyYAML 6.0.3, MIT, fallback only
│   ├── cli/research_cli/           # argparse CLI (main.py, __main__.py)
│   └── vscode-extension/
│       ├── package.json            # manifest (views, commands, menus, settings)
│       ├── src/                    # extension.ts, client.ts, model.ts, trees.ts, panels.ts, commands.ts, shim.ts, util.ts
│       │   └── render/             # ui.ts (components), resume.ts, detail.ts, form.ts
│       ├── media/                  # ui.css, ui.js, research.svg, icon.png (codicons copied at build)
│       ├── scripts/                # bundle-python.mjs, preview-entry.ts, theme-*.css
│       └── test/                   # real-VS-Code harness (run.mjs, harness/)
├── examples/alpha-sweep/           # sim.py, plot.py (stdlib only), run_demo.sh
├── tests/python/                   # pytest suites
├── scripts/                        # package.sh, clean_install_test.sh, e2e_code_server.py
└── dist/                           # built VSIX, wheel, sdist
```

---

## 21. Known limitations and roadmap

**Current limitations:**

- **ID collisions across git branches.** Counters are local. Two branches that create the same id produce a `conflict`
  event on rebuild, and one file must be renamed by hand. A future option is branch-safe ids (e.g. a short hash suffix).
- **Hand edits to `run.json` and the JSONL files are not re-imported** (they are append-only records). Edit runs through
  `research run update`. A hand-edited `run.json` is only read when the run directory is unknown to the index
  (fresh clone or rebuild).
- **Windows:** the core and CLI work. The terminal shim and SLURM backend are POSIX-only.
- **SLURM verified against mocked `sbatch`/`squeue`/`sacct`** in tests. Site-specific accounting quirks (e.g. sacct
  disabled) degrade to the `unknown` state, never to a crash.
- **Preview formats:** NIfTI, HDF5, NetCDF and NPZ show metadata only.
- **Real-VS-Code harness:** not runnable in environments that block the VS Code download. The code-server E2E covers the
  same runtime.

**Roadmap** (only once real use shows a need):

- preview adapters for NIfTI slices, HDF5 trees and NPZ keys
- a parameter-sweep helper (`research run sweep EXP --grid alpha=…`) that respects `planned_runs`
- diff view between two runs (parameters, metrics, `git.diff`)
- an optional MCP server exposing the RPC methods directly to agents
- per-question timelines and an export of a project summary as a report
