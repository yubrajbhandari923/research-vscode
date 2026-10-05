# Research Panel — Design

> Research provenance + research memory + scientific synthesis + agent discipline.
> The test: after a month away, can you open the panel and understand in < 2 minutes
> what was tried, what was learned, what failed, and where to continue?

## 1. Architecture decision: Python core + TypeScript extension (bundled core)

**Chosen:** one Python core (stdlib only, Python ≥ 3.8) that owns *all* business logic,
a thin Python CLI on top of it, and a TypeScript VS Code extension that talks to the
same core over a long‑lived **stdio JSON‑RPC** process.

| | Python core + TS extension | TS-only |
|---|---|---|
| Agents / your scripts calling `research.log_metric()` from Python | native | need a second implementation or subprocess |
| One source of business logic | yes (extension is UI only) | yes, but Python API would be a wrapper around a Node CLI |
| Runs on HPC login + compute nodes | `python3` is always there | Node is often missing on compute nodes |
| Extension complexity | +1 process boundary (JSON-RPC) | simpler |
| SQLite | stdlib `sqlite3` | native module (`better-sqlite3`) → ABI/platform pain inside a VSIX used over Remote SSH |

The one real cost of the Python route is the process boundary. It is kept cheap by:

* **Bundling the Python core inside the VSIX.** The extension runs
  `python3 -m research.rpc` with `PYTHONPATH=<extension>/python`. Nothing has to be
  pip‑installed for the panel to work; `pip install` is only needed if you want the
  `research` command outside VS Code (and the extension also injects a `research` shim
  into every VS Code terminal, so agents running in those terminals get it for free).
* **Zero third-party dependencies.** PyYAML is vendored (pure-Python, MIT) and only used
  if a system PyYAML is not importable.
* **One persistent RPC process** per workspace → no Python start-up cost per click.

### Remote SSH

The extension declares `"extensionKind": ["workspace"]`, so under Remote SSH it runs in the
**remote extension host** on the HPC login node — the same machine that holds the files,
Git, SQLite, the Python env and SLURM. The laptop only renders UI. Webviews load images
through `webview.asWebviewUri`, which VS Code tunnels from the remote host. Nothing in the
extension assumes a local filesystem.

```
Mac (VS Code UI) ── Remote SSH ──▶ HPC login node
                                   ├─ extension host (TS)  ─ stdio JSON-RPC ─▶ python3 -m research.rpc
                                   ├─ repo/.research/  (SQLite index + Markdown/YAML + run dirs)
                                   ├─ sbatch/squeue/sacct/scancel
                                   └─ compute nodes: research.runner writes ONLY to its run dir
```

## 2. Storage model

**Text files are canonical; SQLite is a rebuildable index.**

* High-level objects (questions, experiments, findings, decisions, checkpoints) are Markdown
  files with YAML front-matter under `.research/<kind>/`. You can edit them by hand; edits are
  imported on the next sync (every CLI call, every panel refresh, file watcher).
* Runs live in `.research/runs/RUN-0007/` (`run.json`, `status.json`, `stdout.log`,
  `stderr.log`, `metrics.jsonl`, `artifacts.jsonl`, `git.diff` if dirty).
* `events.jsonl` is an append-only audit log of every change (who/what/when).
* `research.db` is a SQLite index for fast queries and relationships. It is git-ignored
  and rebuilt automatically from the text files if missing (`research rebuild`).

Why: SQLite binaries do not merge in Git; Markdown does. Reconstructability is total.

**HPC filesystem safety:** SQLite uses `journal_mode=DELETE` (WAL is unsafe on NFS/Lustre)
with a busy timeout. Jobs on compute nodes never open SQLite — the runner and
`research.log_metric()` inside a job append to files in the run directory, which are
ingested by the login-node process on the next status check.

## 3. Project folder

```
AGENTS.md                         # created/updated between <!-- research:begin/end --> markers only
.research/
├── config.yaml                   # project name/goal, agent_policy, backend + slurm defaults
├── research.db                   # index (git-ignored, rebuildable)
├── events.jsonl                  # audit log
├── questions/Q-001.md
├── experiments/EXP-001.md
├── findings/F-001.md
├── decisions/D-001.md
├── checkpoints/CP-001.md
├── runs/RUN-0001/{run.json,status.json,stdout.log,stderr.log,metrics.jsonl,artifacts.jsonl}
├── notes/*.md                    # yours; front-matter: title, pinned, links
├── skills/<name>/SKILL.md
├── context/current.md            # regenerated research state for agents
├── prompts/*.md
├── templates/*.md
├── cache/
└── .gitignore
```

## 4. Schema (SQLite index)

```
meta(key PK, value)                               -- schema_version, last_sync
counters(prefix PK, next)                         -- Q, EXP, RUN, F, D, CP, A
questions(id PK, title, description, status, parent_id→questions, tags, author_*, created_at, updated_at)
experiments(id PK, title, question_id→questions, parent_id→experiments,
            hypothesis, motivation, method, expected_outcome, success_criteria, stop_conditions,
            parameters JSON, param_delta JSON, metrics_requested JSON, expected_artifacts JSON,
            planned_runs, is_baseline, status, notes, limitations,
            git_branch, git_commit, git_dirty, tags, author_*, created_at, updated_at, started_at, completed_at)
syntheses(id PK, experiment_id→experiments, what_happened, what_worked, what_failed, interpretation,
          limitations, unresolved, next_experiment, runs_covered, author_*, created_at)
runs(id PK, experiment_id→experiments NOT NULL, label, command, working_dir, parameters JSON, env JSON,
     backend, hostname, pid, slurm_job_id, slurm JSON, run_dir, stdout_path, stderr_path,
     git_branch, git_commit, git_dirty, status, exit_code, reviewed,
     created_at, started_at, ended_at, author_*, notes)
metrics(id PK, run_id→runs?, experiment_id→experiments?, name, value REAL, value_text, step, unit, timestamp)
artifacts(id PK, run_id?, experiment_id?, name, type, path, external, description, size, mtime, hash,
          tags, preview JSON, author_*, created_at)
findings(id PK, title, statement, kind {result|failure}, status {preliminary|supported|contradicted|superseded},
         confidence {low|medium|high}, limitations, contradicting_evidence, superseded_by, tags, author_*, ...)
decisions(id PK, title, statement, reason, status {active|reversed|superseded}, date, superseded_by, author_*, ...)
checkpoints(id PK, title, goal, understanding, baseline, baseline_experiment_id, current_problem,
            next_experiment, notes, finding_ids, failure_ids, question_ids, experiment_ids,
            git_branch, git_commit, git_dirty, author_*, created_at)
links(src_type, src_id, dst_type, dst_id, relation, note)   -- evidence, decision→finding, note→entity
events(id PK, ts, entity_type, entity_id, action, summary, author_*)
```

`author_type ∈ {human, agent, system}`, plus `author_name`, `author_model`.
Author is taken from `--agent`/`RESEARCH_AGENT`, auto-detected for Claude Code
(`CLAUDECODE=1`), otherwise `human`.

Evidence is generic: `links(finding, F-003) —supports→ (run, RUN-0012)`, `—contradicts→`, `—related→`.

## 5. Agent discipline (run budget)

`config.yaml`:

```yaml
agent_policy:
  max_runs_without_synthesis: 5
  max_failed_runs_without_review: 3
  require_experiment_registration: true
  require_synthesis_before_new_experiment: true
  enforce_for_humans: false     # humans get warnings, agents get hard stops
```

* Every run must belong to an experiment (schema-enforced).
* `run create/exec/submit` refuses (exit code 3) when the experiment has ≥ N finished
  unsynthesized runs or ≥ M unreviewed failed runs.
* `experiment create` by an agent refuses while any experiment needs synthesis.
* `--override "reason"` bypasses, and the override is recorded in the audit log.
* The panel shows ⚠ *Needs synthesis · 5 unsynthesized runs* badges.

## 6. Execution backends

`Backend.submit(run) / status(run) / cancel(run)`.

* **local**: `python -m research.runner` detached (`start_new_session`) or foreground (`research run exec`).
* **slurm**: writes `job.sbatch` in the run dir (with your `setup` lines: `module load`, `conda activate`…),
  `sbatch --parsable`; status from `status.json` → `squeue` → `sacct`; `scancel`.

The runner records hostname, start/end, exit code, `SLURM_JOB_ID`, node list, and exports
`RESEARCH_RUN_ID/RESEARCH_RUN_DIR` so `research.log_metric()` in your code attaches to the run.

## 7. What was simplified vs. the brief (deliberately)

* **No separate Project table** — project metadata lives in `config.yaml` (human-editable).
* **Syntheses are a small table, not an entity with its own mirror** — they are rendered inside the experiment file.
* **Notes are pure files**, not DB rows. Pin/link state is their own front-matter.
* **Parameter sweeps** = an experiment with a `parameters` dict of lists + one run per point; no sweep engine.
* **No diff capture beyond `git.diff` for dirty runs** (capped at 2 MB).
* **Preview adapters**: a TS registry with image/text/markdown/json/csv adapters; binary → metadata.

## 8. Requirements the brief didn't mention (and how they're handled)

1. **SQLite on NFS/Lustre** — see §2 (DELETE journal, compute nodes never write the DB).
2. **Concurrent compute-node writes** — per-run files, ingested later.
3. **Git merges / ID collisions across branches** — DB is git-ignored; IDs are counters.
   If two branches both create `EXP-012`, `research rebuild` reports the collision
   (rename one file). Acceptable for single-researcher projects.
4. **Runs that outlive VS Code** — runs are detached; status is reconciled from files, not from a live process handle.
5. **Staleness** — resume view shows *what changed since the last checkpoint* and warns when the checkpoint is old.
6. **Baseline** — an explicit `is_baseline` flag on one experiment (plus the checkpoint's baseline text).
7. **Audit trail** — "never modify history silently": every mutation (including hand-edits picked up by sync) is an event.
