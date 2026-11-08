# 2. Data model

## Identifiers

| Entity | Prefix | Width | Example |
|---|---|---|---|
| Question | `Q` | 3 | `Q-001` |
| Experiment | `EXP` | 3 | `EXP-012` |
| Run | `RUN` | 4 | `RUN-0044` |
| Finding | `F` | 3 | `F-019` |
| Decision | `D` | 3 | `D-002` |
| Checkpoint | `CP` | 3 | `CP-004` |
| Artifact | `A` | 4 | `A-0107` |
| Plan | `PLAN` | 3 | `PLAN-001` |
| Task | `T` | 3 | `T-042` |
| Synthesis | — | — | `EXP-012/S2` |
| Note | — | — | `note:<file-stem>` |

- IDs are allocated from a per-prefix counter in a write transaction (`BEGIN IMMEDIATE`). The counter never goes below
  max(existing)+1, and it skips any number whose mirror file or run directory already exists on disk.
- **Normalisation:** input is case-insensitive, accepts `-`, `_`, space or no separator, and ignores leading zeros.
  `exp-1`, `EXP1` and `exp_001` all mean `EXP-001`. A bare number is accepted where the type is implied
  (`research experiment show 3`). Passing the wrong prefix for a slot is an error, e.g. `F-1` where an experiment is expected.

## Common fields

Every first-class object has:

| Field | Meaning |
|---|---|
| `author_type` | `human` · `agent` · `system` |
| `author_name` | agent name (e.g. `claude-code`), or `$RESEARCH_AUTHOR` for humans |
| `author_model` | agent model, if known |
| `created_at`, `updated_at` | ISO-8601 with local UTC offset, second precision |

## Entities

### Question

| Field | Type | Notes |
|---|---|---|
| `title` | text | required |
| `description` | text (Markdown) | |
| `status` | enum | `open` · `investigating` · `answered` · `blocked` · `abandoned` |
| `parent_id` | Q id | sub-questions |
| `tags` | list | |

### Experiment

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

### Run

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

### Metric

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

### Artifact

| Field | Notes |
|---|---|
| `run_id`, `experiment_id` | either or both. If a run is given, the experiment is derived from it. |
| `name` | defaults to the basename |
| `type` | inferred from the extension (see Artifact type inference below) or set explicitly |
| `path` | project-relative if inside the project, otherwise absolute. `external = true` when absolute. |
| `description`, `tags` | |
| `size` | bytes; for directories, the sum over up to 20 000 files |
| `mtime` | |
| `hash` | sha256 if size ≤ `artifacts.hash_max_bytes` (default 64 MiB) or `--hash` is given |
| `preview` | JSON; for directories `{files: n}` |

Registering the same `(path, run, experiment)` again **updates** the existing artifact instead of creating a duplicate.

### Synthesis

An interpretation of an experiment's runs, appended to history. Synthesis IDs look like `EXP-001/S1`, `S2`, …

| Field | Notes |
|---|---|
| `what_happened`, `what_worked`, `what_failed`, `interpretation`, `limitations`, `unresolved`, `next_experiment` | at least one of the first four is required |
| `runs_covered` | the list of finished run ids at synthesis time |

Syntheses are **not** promoted to findings automatically.

### Finding

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

### Decision

| Field | Notes |
|---|---|
| `statement` | required; `title` defaults to its first 120 chars |
| `reason` | |
| `status` | `active` · `reversed` · `superseded` |
| `date` | YYYY-MM-DD, default today |
| `superseded_by` | |
| links | `supporting_findings[]` (relation `based_on`), `experiments[]` (relation `related`) |

Creating a decision with `supersedes=D-x` marks D-x as `superseded` and sets its `superseded_by`.

### Checkpoint

A snapshot of understanding.

| Field | Notes |
|---|---|
| `title` | |
| `goal`, `understanding`, `baseline`, `current_problem`, `next_experiment`, `notes` | Markdown |
| `baseline_experiment_id` | |
| `finding_ids`, `failure_ids`, `question_ids`, `experiment_ids` | JSON lists |
| `git_branch`, `git_commit`, `git_dirty` | |

### Note

Notes are file-only (no DB row), stored at `.research/notes/<slug>.md`.

- Front-matter: `title`, `pinned` (bool), `links` (ids), `created`.
- The id is `note:<stem>`.
- Listing order: pinned first, then most recently modified.

### Plan

A research objective decomposed into tasks. Mirror: `.research/plans/PLAN-001.md`.

| Field | Type | Notes |
|---|---|---|
| `title` | text | required |
| `objective`, `success_criteria`, `context` | Markdown | sections |
| `status` | enum | `active` · `completed` · `blocked` · `abandoned` (default `active`) |
| `root_question_id` | Q id | front-matter key `question`; validated to exist |
| `completed_at` | timestamp | set automatically when status becomes `completed` via `update_plan` |
| `skills` | list | project skills for every task in the plan (front-matter) |

Derived on read (`get_plan_with_tasks`): `tasks[]` (creation order), `task_count`, `tasks_done`, `tasks_running`, `tasks_blocked`.

### Task

A unit of work inside a plan. Mirror: `.research/tasks/T-001.md`.

| Field | Type | Notes |
|---|---|---|
| `plan_id` | PLAN id | required; front-matter key `plan`; validated to exist |
| `title` | text | required |
| `task_type` | text | recommended: `research` · `implementation` · `experiment` · `analysis` · `verification` · `synthesis` · `debug` · `data`. The CLI restricts to these; the API and Markdown accept any value. |
| `assigned_role` | text | recommended: `planner` · `implementer` · `verifier` · `analyst` · `human`. Same extensibility rule. |
| `status` | enum | `todo` · `running` · `verify` · `done` · `blocked` (default `todo`) |
| `depends_on` | list of T ids | each validated to exist at create/update time |
| `related_question_id`, `related_experiment_id` | Q / EXP id | front-matter keys `question`, `experiment`; validated |
| `artifacts` | list of A ids | set by `complete_task(artifacts=...)` |
| `goal`, `inputs`, `expected_outputs`, `acceptance_criteria`, `verification`, `result`, `blockers`, `notes` | Markdown | sections |
| `completed_by` | text | free text (agent name or person) |
| `started_at`, `completed_at` | timestamps | set automatically on the transition into `running` / `done` |
| `claimed_by`, `claimed_at` | text, timestamp | who holds the task (set by `start_task`, cleared by `release_task`) — [Coordination between agents](05-agents-governance.md#coordination-between-agents) |
| `skills` | list | project skill names to load for this task (may name skills not installed yet; shown as missing) |
| `checks` | list of check objects | verification checks that gate completion: `{type: command, run}`, `{type: file, path}`, `{type: metric, name, op, value, target?}` — [Verification gates](05-agents-governance.md#verification-gates) |

`completed_by` defaults to the acting author (e.g. `claude/implementer`).

**Ready is derived, never stored.** `is_ready = (status == "todo") and every dependency has status "done"`. A task with
no dependencies is ready as soon as it is `todo`. Updating `depends_on` rejects a dependency cycle; dependencies may point at tasks in other plans.

### Review (verification record)

Append-only records in `reviews.jsonl`, indexed in the `reviews` table. Not mirrored; shown on the target's page and mirror.

| Field | Notes |
|---|---|
| `id` | `<target>/V<n>` for a task check run, `<target>/R<n>` for a finding review |
| `target_id`, `target_type` | the task or finding |
| `kind` | `check` (task checks) · `review` (independent review of a finding) |
| `verdict` | check: `pass` · `fail` — review: `supported` · `contradicted` · `needs_work` |
| `summary` | e.g. `2/3 checks passed: file: missing.png (missing)`, or the reviewer's reasoning |
| `details` | check: per-check results `{check, ok, detail, output (tail), seconds}`; review: `{confidence}` |
| author, `created_at` | |

### Project

The project is not a table. Its metadata lives in `config.yaml → project`: `name`, `description`, `goal`, `status`,
`created_at`, `git_repo` (the origin URL detected at init), `default_backend`.

## Artifact type inference

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

## Relationships (the `links` table)

`links(src_type, src_id, dst_type, dst_id, relation, note)`, with primary key `(src_id, dst_id, relation)`.

| Relation | From → to |
|---|---|
| `supports` / `contradicts` / `related` | finding → experiment/run/artifact/finding |
| `addresses` | finding → question |
| `based_on` | decision → finding |
| `related` | decision → experiment |

Structural parent links (question→question, experiment→question, experiment→experiment, run→experiment,
artifact→run/experiment, plan→question, task→plan/question/experiment, task→task dependencies, task→artifacts) are
columns, not rows in `links`.
