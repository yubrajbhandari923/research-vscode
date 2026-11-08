# 3. Storage and synchronisation

## Project layout

```
<project>/
├── AGENTS.md                         # research block between <!-- research:begin … --> and <!-- research:end -->
└── .research/
    ├── config.yaml                   # project metadata, agent_policy, backends, artifacts, resume
    ├── format.json                   # project format (store.PROJECT_FORMAT), checked on open
    ├── .gitignore                    # research.db*, cache/, locks/, transaction files, bin/, reports/, hpc/bundles/, run logs
    ├── research.db                   # SQLite index (derived)
    ├── events.jsonl                  # append-only audit log
    ├── artifacts.jsonl               # append-only artifact records (last record per id wins)
    ├── syntheses.jsonl               # append-only syntheses
    ├── metrics.jsonl                 # experiment-level metrics (run metrics live in run dirs)
    ├── reviews.jsonl                 # append-only task check runs and finding reviews
    ├── skills.yaml                   # where imported skills came from + project settings (always, applies_to)
    ├── evidence.jsonl  integrity.jsonl  sources.jsonl   # append-only: number bindings, artifact pins, literature records
    ├── usage.jsonl                   # append-only model usage (only what a provider/caller reported)
    ├── guide.json                    # syntheses already reflected in the Scientist Guide
    ├── hooks/results.jsonl           # every harness hook execution (append-only)
    ├── orchestration/                # T-001.json per-task ledger (frozen tests, attempts, contract, override, dispatches); tuning-EXP-001.json
    ├── hpc/                          # bundles/ (ignored), incoming/, cluster state; see [HPC execution](06-execution.md#hpc-execution)
    ├── supervisor/                   # plan runtime state, daemon.json / daemon.lock / daemon.log, proposals, prompts/, promotions/AP-n.json, interventions/INT-n.json (SUPERVISOR.md)
    ├── promotions.jsonl              # append-only promotion candidates and gates ([Selective promotion into main](08-supervisor.md#selective-promotion-into-main))
    ├── orchestration/executions/     # per-execution output; execution records and locks (.research/locks/) for supervised and dispatched tasks
    ├── bin/                          # project-local CLI launcher (per machine; git-ignored)
    ├── questions/Q-001.md            # mirrors (Markdown + YAML front-matter)
    ├── experiments/EXP-001.md
    ├── findings/F-001.md
    ├── decisions/D-001.md
    ├── checkpoints/CP-001.md
    ├── plans/PLAN-001.md
    ├── tasks/T-001.md
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
    ├── reports/                      # `research report` output (project.md/.html, Q-001.md, …)
    └── cache/                        # research.db backups, dispatch/ briefs + *.policy.json snapshots, removed-skills/, removed-agents/
```

Machine-level files (never in the project, git or the audit log): `~/.config/research-os/config.yaml` (optional machine config), `approval.json` (PIN verifier, scrypt), `telegram.json` (bot token and chat id) and `projects.json` (known projects), the secret files with mode 0600, all in the directory of the machine config file (`$RESEARCH_CONFIG` when set).

Outside `.research/`, the tool writes only when asked: `AGENTS.md` (managed block), per-skill symlinks in
`.claude/skills/` and `.agents/skills/` (config `skills.link`), and MCP registrations from `research mcp install`
(`.mcp.json`, `.vscode/mcp.json`, `.cursor/mcp.json`, `.gemini/settings.json`), and, from `research agent setup --apply`, managed `.claude/agents/research-*.md`. Supervised work happens in the experiment worktrees, and `./research` is linked in the main checkout (git-excluded).

## What is canonical

| Data | Canonical store | Index |
|---|---|---|
| Questions, experiments, findings, decisions, checkpoints, plans, tasks | Markdown mirror files | `questions`, `experiments`, `findings`, `decisions`, `checkpoints`, `plans`, `tasks` tables |
| Links | mirror front-matter (`supports`, `supporting_findings`, …) | `links` |
| Runs | `runs/RUN-x/run.json` (+ `status.json` for live state) | `runs` |
| Run metrics | `runs/RUN-x/metrics.jsonl` | `metrics` |
| Experiment metrics | `metrics.jsonl` | `metrics` |
| Artifacts | `artifacts.jsonl` | `artifacts` |
| Syntheses | `syntheses.jsonl` | `syntheses` |
| Task check runs, finding reviews | `reviews.jsonl` | `reviews` |
| Skill provenance and settings | `skills.yaml` | none |
| Audit log | `events.jsonl` | `events` |
| Notes, skills, prompts, templates | the files themselves | none |
| Project metadata, policy | `config.yaml` | none |

## Mirror file format

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
  For plans: `question` → `root_question_id`. For tasks: `plan` → `plan_id`, `question` → `related_question_id`,
  `experiment` → `related_experiment_id`.
- **Sections.** Each long-text field is a `## <Heading>`, with headings matched case-insensitively:

  | Entity | Sections |
  |---|---|
  | Question | Description |
  | Experiment | Hypothesis, Motivation, Method, Expected outcome, Success criteria, Stop conditions, Known limitations, Notes |
  | Finding | Statement, Limitations, Contradicting evidence |
  | Decision | Decision, Reason |
  | Checkpoint | Current goal, Current understanding, Baseline, Current problems / blockers, Next step, Notes |
  | Plan | Objective, Success Criteria, Context |
  | Task | Goal, Inputs, Expected Outputs, Acceptance Criteria, Verification, Result, Blockers, Notes |

  Generated tails: a plan lists its tasks with status icons (✓ done, ⏳ running, ⛔ blocked, 🔍 verify, ○ todo) and
  dependencies. A task shows **Ready to start** or its status, its dependencies, and its plan, question, experiment and artifacts.

  An unknown `##` heading inside a known section stays part of that section's text. `###` and deeper are ordinary content.
- **The `# ID · title` H1 is ignored on import.** The title comes from front-matter.
- **Everything after the generated marker is ignored on import** and rewritten on every export.
- **Rendering is deterministic.** A file is only rewritten when its bytes would change, so git diffs stay minimal.

## JSONL record schemas

```jsonc
// events.jsonl
{"ts","entity_type","entity_id","action","summary","author_type","author_name"}
// action is an open set of short verbs (features add their own, e.g. config, hook_run). Common values: created, updated, edited (hand edit), status, baseline, synthesized, superseded, registered,
//          started, submitted, completed, failed, cancelled, unknown, override, restored, conflict,
//          import_error, reconcile_error, initialized, progress (task note), verified (check run),
//          reviewed, dispatched, imported/removed (skill), swept, report, committed (snapshot)

// artifacts.jsonl (one full record per registration/update; last record per id wins)
{"id","run_id","experiment_id","name","type","path","external","description","size","mtime","hash",
 "tags","preview","author_type","author_name","author_model","created_at"}

// syntheses.jsonl
{"id":"EXP-001/S1","experiment_id","what_happened","what_worked","what_failed","interpretation",
 "limitations","unresolved","next_experiment","runs_covered":[…],"created_at", author…}

// reviews.jsonl
{"id":"T-005/V1"|"F-003/R1","target_id","target_type","kind":"check"|"review","verdict","summary","details",
 "created_at", author…}

// metrics.jsonl (run dir or root)
{"name","value"|"text","step"?,"unit"?,"ts","author_type","experiment_id"? (root only)}

// runs/RUN-x/artifacts.jsonl (written inside jobs; no ids yet)
{"path"(absolute),"name","description","type","tags","ts"}
```

## `status.json` (runner-owned)

```json
{"state": "running|completed|failed|cancelled", "runner_pid": 123, "pid": 124, "hostname": "node01",
 "started_at": "…", "ended_at": "…", "exit_code": 0,
 "slurm_job_id": "4242", "slurm_nodelist": "node01", "slurm_partition": "gpu"}
```

## SQLite index

- **Tables:** `meta`, `counters`, `questions`, `experiments`, `findings`, `decisions`, `checkpoints`, `plans`, `tasks`,
  `syntheses`, `reviews`, `runs`, `metrics`, `artifacts`, `links`, `events`, `mirrors(path, entity_type, entity_id, hash, mtime, size)`.
- **Indices:** `runs(experiment_id)`, `metrics(run_id)`, `metrics(experiment_id)`, `artifacts(run_id)`,
  `artifacts(experiment_id)`, `links(dst_id)`, `events(ts)`, `tasks(plan_id)`, `tasks(status)`, `plans(status)`, `reviews(target_id)`.
- **Schema:** the index schema is version 3 (`schema.SCHEMA_VERSION`), built from `ENTITIES`/DDL. The DDL declares foreign keys on `plans` and `tasks`, but `foreign_keys=OFF`, so
  integrity is enforced by `plans.py`. Migration steps can be callables; column additions check `PRAGMA table_info` first, because a
  fresh database already gets every column from `ENTITIES`.
- **Connection pragmas:** `journal_mode=DELETE` (WAL is unsafe on NFS and Lustre), `busy_timeout=15000`, `foreign_keys=OFF`
  (integrity is enforced in the services layer). Autocommit, with explicit `BEGIN IMMEDIATE` transactions that nest by depth counting.
- **Migrations:** `schema.MIGRATIONS = {version: [DDL…]}`, applied in order inside a transaction. `meta.schema_version` records
  the current version. A project whose version is **newer** than the tool is refused with an error asking for a newer release, never modified.
- `meta.built_at` marks a fully built index. If it is missing, a rebuild runs.

## Path portability rules

| Situation | Stored form |
|---|---|
| Path inside the project root | project-relative, `/`-separated (`outputs/run_004/results.npy`) |
| Path outside the project | absolute (`/scratch/user/big.npy`), `external = true` |
| Working directory | relative if inside the project |
| `command.sh` | `cd "$(dirname "$0")/../../.." && cd <relative wd>`, so it works after moving the project |
| `status.json` | contains no paths |
| `job.sbatch` | contains absolute paths. It is a per-submission artifact for one machine and is never read back. |

The test suite asserts that no Markdown, JSON, JSONL or YAML file and no DB row contains the old absolute project path after a move.

## Crash consistency

`durability.py`: `Project.tx()` takes the project write lock, journals each canonical file (full prior content, or prior length for appends) before the first write, commits SQLite, then
marks the journal committed. Opening a project recovers: an uncommitted journal is rolled back and the index rebuilt; a committed one is dropped. A recovery failure leaves
`.transaction.json` in place. This makes a process crash safe; it is not a defence against raw edits to canonical files or against unreliable network filesystems.

## Synchronisation, rebuild and hand edits

### When sync runs

- At the start of every CLI command (opening a `Project`).
- Before every RPC or MCP call whose op syncs (every op except those declared `sync=False`, such as `ping`, `init` and the config helpers).
- On demand: `research sync`, the VS Code **Refresh** command, and the file watcher (which triggers a refresh, which triggers a sync).

### Sync algorithm (`Project.sync`)

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

### Run sync

1. Import unknown run directories (those with a `run.json` but no row), e.g. after `git pull` or a rebuild.
2. For each run whose `metrics.jsonl` or `artifacts.jsonl` grew past its stored offset, ingest the new lines. In-job
   artifacts are given an `A-` id and appended to the root `artifacts.jsonl`.
3. Reconcile every non-terminal run ([Reconciliation](06-execution.md#reconciliation-reconcile_run)). SLURM queries are throttled to once every 20 s unless forced.

### Import-before-export

Before any mirror is written, its on-disk hash is compared with the last recorded hash. If they differ, the hand edit
is imported **first**. Malformed edits block export and preserve the file. This is not a filesystem lock against an editor writing between the hash check and replacement; concurrent external editing still requires coordination.

### Rebuild (`research rebuild`, or automatic when `research.db` is missing)

1. Back up the readable live DB with SQLite backup into a unique `.research/cache/research.db.bak-*` file (skipped for the automatic first build). Keep the live database inode in place.
2. In one transaction:
   - import every mirror file
   - import every run directory, re-reading run metrics from offset 0 and restoring the artifact offset from `run.json`
   - ingest the root JSONL files
   - load `events.jsonl`
3. Duplicate IDs, malformed mirrors/run records and malformed complete JSONL records abort the transaction. A readable existing index remains intact. No conflicting file is discarded.
4. Advance counters and set `meta.built_at`. Rebuild does not rewrite canonical mirrors. Normal sync may refresh generated tails.

Incomplete trailing JSONL lines are not ingested; appending refuses an incomplete tail until repaired. POSIX appenders use an advisory file lock, flush and fsync. A corrupt index that cannot be opened requires stopping clients, backing up the project, and moving the index aside before reopening (`research rebuild` backs up a readable index; a missing one is simply rebuilt).
