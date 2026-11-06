# Research Panel — Features, Design and Technical Specification

**Version:** 2.6.0 · **Project format:** 2 · **Schema version:** 3

This is the single reference for **what Research Panel does, why it is designed that way, and exactly how it works**.
A competent engineer should be able to re-implement it from this document alone. It also answers behaviour questions
such as "when does an experiment become `needs_review`?", "when is a task *ready*?" or "what happens if I delete `research.db`?".

> To learn to *use* the system, start with the [README](../README.md). This document replaces the former `DESIGN.md`,
> whose rationale is now [§21](#21-design-rationale). Where another document disagrees with this file, this file describes what is actually built.

---

## Architecture notes

- **Crash consistency.** `durability.py`: `Project.tx()` takes the project write lock, journals each canonical file (full prior content, or prior length for appends) before the first write, commits SQLite, then marks the journal committed. Opening a project recovers: an uncommitted journal is rolled back and the index rebuilt; a committed one is dropped. Recovery failures leave `.transaction.json` in place. Process-crash safe, not a defence against raw edits or unreliable network filesystems.
- **Execution ownership.** `execution.py`: dispatch for a task writes a single-use execution record and returns a wrapper command. The wrapper takes `.research/locks/<task>.execution` non-blocking and passes the descriptor to the provider, so the lock outlives a killed wrapper while the provider runs. States: prepared, launched, running, completed, failed, unknown.
- **Effective task specification.** `task_spec.py` projects task fields and the contract with sources and conflicts; storage is unchanged.
- **Completion policy.** `orchestrate.verify_work` is the single verifier used by `verify_task`, `gate_task_completion`, `task done`, status updates and create-as-done. `verification_state` is `passed`, `blocked` or `unverified`; humans may complete administratively with an audit event.
- **Not implemented.** A UI view of execution records and Windows lock/process-tree verification. Coordinator continuation is provided since 2.1 by the persistent supervisor ([§14G](#14g-supervisor-governance-promotion-and-notifications-21-to-26)).

## Stabilization behavior changes

- Every public task status-update path applies the completion gate. Task verification checks frozen-test integrity before running commands. Task dependency updates reject cycles; claims perform their read and write under one SQLite write transaction.
- Re-freezing a changed acceptance file is refused; use the authorized correction path with a recorded reason. Malformed orchestration ledgers fail closed instead of resetting tests and attempt counts.
- All task dispatch preparation paths run preflight hooks through the shared agent dispatcher. Enabled required hooks without approval block the gate. Disabled hooks are skipped; advisory failures cannot alone prove verification success. Experiment checks require a zero exit code, registered runs and no blocked required hooks before reporting all passed.
- Hooks and task checks share bounded-output subprocess execution. POSIX timeout/interruption cleanup kills the process group. Headless agent timeouts return failure (124). Windows descendant cleanup is not equivalent to a POSIX process group and remains unverified.
- RPC/MCP validate malformed request shapes and keep serving subsequent requests. The extension client decodes split UTF-8 correctly, bounds startup waiting, handles stdin errors and isolates late exits from an older process after restart.
- The Python wheel has no runtime dependency on setuptools. The clean-install harness isolates Python import paths and machine configuration and installs offline. The extension-host harness copies its workspace; the demo refuses to erase an existing directory.


## Table of contents

0. [Feature inventory](#0-feature-inventory)
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
14A. [Full deterministic context export](#14a-full-deterministic-context-export-research-report---full)
14B. [The managed AGENTS.md block](#14b-the-managed-agentsmd-block)
14C. [HPC execution](#14c-hpc-execution)
14D. Shared ledger and experiment workspaces · 14E. Typed settings and job isolation · 14F. Selective promotion into main
14G. [Supervisor, governance, promotion and notifications](#14g-supervisor-governance-promotion-and-notifications-21-to-26)
13A. Control Center (React)
15. [Safety guarantees](#15-safety-guarantees)
16. [Configuration reference](#16-configuration-reference)
17. [Environment variables](#17-environment-variables)
18. [Testing and verification](#18-testing-and-verification)
19. [Build, packaging and release](#19-build-packaging-and-release)
20. [Repository layout](#20-repository-layout)
21. [Design rationale](#21-design-rationale)
22. [Limitations](#22-limitations)
23. [Roadmap](#23-roadmap)

---

## 0. Feature inventory

Everything below is implemented and covered by tests unless marked otherwise. Section numbers point to the full specification.

### Research memory (core)

| Feature | Summary | § |
|---|---|---|
| Questions | Hierarchical research questions; `open` questions auto-move to `investigating` when an experiment is registered under them | 3.3, 6.1 |
| Experiments | Designed tests: hypothesis, motivation, method, expected outcome, success criteria, stop conditions, parameters, metrics, expected artifacts, planned runs | 3.3, 6.2 |
| Variants | Derive an experiment with changed parameters; the delta (`from → to`) is recorded | 3.3 |
| Baseline | Exactly one experiment can be flagged as the current baseline | 3.3 |
| Runs | One execution, always owned by an experiment; captures command, cwd, params, env, host, pid, SLURM job, logs, exit code, timing, git branch/commit/dirty, `git.diff` | 3.3, 8 |
| Metrics | Arbitrary name → number/text on a run or experiment, with step and unit | 3.3 |
| Artifacts | References (never copies) to files and directories, with inferred type, size, mtime and sha256 | 3.3, 3.4 |
| Syntheses | Interpretations of an experiment's runs; mark failed runs reviewed; never auto-promoted to findings | 3.3 |
| Findings | Reusable conclusions with `supports` / `contradicts` / `related` evidence, confidence, limitations; `result` or `failure` kind; supersession | 3.3, 6.5 |
| Decisions | Deliberate choices based on findings; supersession | 3.3, 6.5 |
| Checkpoints | Snapshots of understanding, auto-drafted from current state, with git state | 3.3, 9.2 |
| Notes | Human-only Markdown notes, pinnable and linkable | 3.3 |
| **Plans** | Research objectives with success criteria and context, optionally rooted in a question | 3.3, 6.6 |
| **Tasks** | Units of work in a plan: type, role, dependencies, goal, inputs, expected outputs, acceptance criteria, verification, result, blockers; derived *ready* state; "next ready task" | 3.3, 6.7 |
| **Reviews & checks** | Verification records: task check runs (`T-005/V1`) and independent finding reviews (`F-003/R1`) | 3.3, 7.5 |
| Authorship | Every object and event records `human` / `agent` / `system`, plus agent name (with role, e.g. `claude/verifier`) and model | 7.1 |
| Audit log | Every mutation, including hand edits, is an event in `events.jsonl` | 4.4 |

### Storage and safety

| Feature | Summary | § |
|---|---|---|
| Text-canonical storage | Markdown + YAML mirrors, JSONL records and run directories are the truth; SQLite is a rebuildable index | 4 |
| Hand editing | Edit any mirror file or drop in a new one; imported on next sync; generated tail regenerated | 5 |
| Import-before-export | A pending hand edit is never overwritten | 5.4 |
| Rebuild | `research rebuild` / missing DB → full reconstruction from text, with backup | 5.5 |
| Portability | Project-relative paths; projects can be moved, cloned or opened over SSH | 4.7 |
| Schema migrations | Versioned and forward-only; an older tool refuses a newer project | 4.6 |
| Layered configuration | Built-in defaults ← optional machine file (`~/.config/research-os/config.yaml`, `$RESEARCH_CONFIG`) ← sparse project file; comment-preserving surgical edits; `config show\|check\|migrate` | 16 |
| Managed AGENTS.md block, full export | managed AGENTS.md block; full deterministic export (`report --full`) | 14A, 14B |
| HPC-safe SQLite | Rollback journal (no WAL), busy timeout; compute nodes never open the DB | 2.3, 14 |

### Execution

| Feature | Summary | § |
|---|---|---|
| Local foreground runs | `research run exec` streams output and records everything | 8.3 |
| Local detached runs | Survive terminal, VS Code and SSH disconnects | 8.3 |
| SLURM backend | `sbatch` script generation with config defaults and setup lines; status via run dir → `squeue` → `sacct`; `scancel` | 8.4 |
| Attached runs | Register a run you executed yourself | 8.1 |
| In-job logging | `research.log_metric()` / `register_artifact()` inside a job write only to the run dir | 8.6 |
| Run budget | Agents are blocked (exit code 3) after N unsynthesized or M unreviewed failed runs; logged overrides | 7.2 |
| **HPC (SSH + Slurm, bundles)** | Cluster profiles, approved paths, one-shot submission with identity, `hpc sync`, portable bundles, dataset discovery from the filesystem | 14C |
| **Parameter sweeps** | `research run sweep`: one run per grid point, checked against the budget and `planned_runs` before anything starts | 8.7 |

### Agent support

| Feature | Summary | § |
|---|---|---|
| `AGENTS.md` managed block | Created/refreshed between markers; user text preserved | 7.3 |
| `context/current.md` / `research current` | Compact, auto-regenerated state summary for agents, including the active plans' work queue | 9.3 |
| Task context | `research task context T-…`: a bounded brief for one task (plan, dependency results, question, experiment state, rules) | 9.5 |
| Default skills, prompts, templates | `run-experiment`, `synthesize-experiment`, `reproduce-paper`, `clean-dataset` | 7.4 |
| Agent auto-detection | Claude Code and Codex detected from env; `RESEARCH_AGENT` / `--agent` for others | 7.1 |
| `--json` everywhere | Every CLI command; structured errors with exit codes | 10 |
| **Verification gates** | Task checks (command, file, metric threshold) gate `task done`; agents can't mark findings supported — only an independent review can | 7.5 |
| **Multi-agent coordination** | Task claims (no two agents on one task), progress notes, release/hand-off, stale-task detection | 7.6 |
| **Roles, profiles, dispatch** | Roles (planner, implementer, verifier, analyst) map to agent CLIs (claude, codex, gemini, …); dispatch writes a brief and starts the agent in a terminal | 7.7 |
| **MCP server** | `research mcp`: 44 typed tools for any MCP client; `research mcp install` registers it for Claude Code, VS Code, Cursor, Gemini (and prints the Codex command) | 7.8 |
| **Project skills** | Import skills from a folder or git repo (e.g. ponytail, NVIDIA medical-AI skills), update, remove, always-on / applies-to settings, exposed in `.claude/skills` and `.agents/skills`, suggested per task | 7.9 |
| **Allocation strategies** | Named, switchable model-allocation policies (`sonnet_first`, `opus_first`, `haiku_max`, `sonnet_heavy`, custom): levels → profiles, deterministic routing, retries, escalation; one resolver for every dispatch path; per-dispatch snapshot for provenance | 7.7, 10.x |
| **Tiered orchestration** | Routing by verifiability, frozen acceptance tests, bounded attempts, parent/child delegation, per-task model pins, task contracts and `orchestrate verify` | 10.x |
| **Project harness and hooks** | `harness:` references (skills, context, instructions, checks) in every brief; executable hooks at preflight / post-implementation / pre- and post-experiment, run only after human approval, with timeouts and a results ledger | 10.x, 16 |
| **Usage accounting** | `--headless` runs record provider-reported tokens/cost to `.research/usage.jsonl`; `research usage`; a missing value is never zero | 10.x |
| **Experiment check** | `research experiment check`: deterministic validation of runs, metrics, artifacts and hooks, separated from what needs human interpretation; can prepare a synthesis session | 10.x |
| **Scientist Guide** | Quarto book on main (`docs/scientist-guide/`); important results logged automatically; agent drafts bounded by `guide.draft.*` and only on request; `references.bib`; GitHub Pages workflow | 10.x, `docs/GUIDE.md` |
| **Numerical evidence** | Findings bind numbers to recorded metrics; a failing binding blocks `supported`; artifact integrity, run provenance, offline literature records | 10.x |
| **Tiered context** | AGENTS.md (bootstrap) → `research current` (bounded state) → `research task context` (one task) → `research search` / `show` (retrieval) | 9.3–9.6 |

| **Persistent supervisor** | Deterministic daemon that runs YAML plans: dispatch, verification, bounded repair, escalation, honest plan health (HALTED), live reload/restart | 14G |
| **Governance and approval PIN** | Agents propose, humans approve; local scrypt PIN, prompted on the terminal | 14G |
| **Unattended code promotion** | Opt-in, two-key, fast-forward-only into local `main`, independent review, never approves science | 14F, 14G |
| **Supervisor intervention** | `supervisor instruct`: steer a task in words; classified actions, one PIN for protected ones | 14G |
| **Debug agent and Telegram** | Opt-in diagnosis of a halt, alerts and chat commands | 14G |

### Interfaces

| Feature | Summary | § |
|---|---|---|
| CLI | `research …`: 56 top-level commands (plus aliases); `research --help` lists them | 10 |
| Python API | `import research` | 11 |
| JSON-RPC | stdio line protocol between extension and core | 12 |
| VS Code: sidebar | Overview webview + 9 trees (Plans, Questions, Experiments, Findings, Decisions, Checkpoints, Notes, Artifacts, Agent Context) | 13.4 |
| VS Code: Research Home | Landing page: focus, active plan, insights, attention items, key outputs, activity (`Cmd/Ctrl+Alt+R`) | 13.5, 9.4 |
| VS Code: detail pages | Experiment, Finding, Question, Decision, Checkpoint, Run, Artifact, Plan, Task | 13.5 |
| VS Code: artifact previews | Image, CSV/TSV, JSON, Markdown, text/log/code, NPY header, directory | 13.5 |
| VS Code: forms | Schema-driven create/edit forms with entity pickers, parameter editors, run modes | 13.6 |
| VS Code: terminal shim | `research` on `PATH` in every integrated terminal | 13.8 |
| Project-local CLI | `.research/bin/research`: a vendored copy of the CLI any agent can run without install or `PATH` (`research bin install`, refreshed by `init` and the extension) | 13.8 |
| VS Code: live refresh | File watcher + run polling while runs are active | 13.3 |
| VS Code: Remote SSH | Runs on the workspace host; works on HPC login nodes | 14 |
| VS Code: run comparison | Side-by-side page: metric deltas, parameter changes, code diff between commits, outputs | 13.5, 9.8 |
| VS Code: agents | Dispatch to agent, plan with agent, review form, verify, progress notes, skill import, MCP registration | 13.7 |
| VS Code: Agent setup | One page for the active strategy and its routing, tasks in flight (model pins, contracts), harness hooks (approve, run, enable), experiment validation, usage by strategy, skills, MCP servers and managed agent files | 13.5 |
| Reports | `research report`: Markdown + HTML summary of findings with evidence plots, experiments, decisions | 9.7 |
| Search | `research search` / VS Code search box across every record | 9.6 |
| Git snapshots | `research checkpoint --commit` commits `.research/` only (opt-in, never pushes) | 9.2 |
| Theme-native design | Built only on VS Code theme variables; light, dark and high contrast | 13.12 |

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
question → experiment → runs → artifacts / metrics → synthesis → findings → decisions → checkpoint
                 ▲
   plan → tasks ─┘   (optional operational layer: objectives broken into tracked work)
```

Experiments remain the *scientific* unit, a designed test. Plans and tasks are the *operational* unit: who does what, in
which order, and how to tell when it is done. A task may reference the question and experiment it serves.

### 1.3 Design principles

| Principle | Consequence in the implementation |
|---|---|
| **Raw activity is subordinate to understanding** | Runs are collapsed under experiments everywhere. Research Home leads with focus, plan, findings and attention items, not run tables. |
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
- a general workflow orchestrator (the supervisor runs research plans with verification and approval gates; it is not an arbitrary DAG engine)
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
│    plans.py     plans, tasks, readiness, claims, progress notes, stale tasks, task context │
│    verification.py  task checks, finding reviews, the gates                                │
│    skills.py    import/update/remove skills, links for agents, matching to tasks          │
│    agents.py    roles, profiles, dispatch briefs, headless runs   mcp.py  MCP server + install │
│    strategy.py  named allocation strategies, the single dispatch resolver, agent-file docs │
│    orchestrate.py  routing, frozen tests, attempts, contracts, verify, pins, tuning        │
│    harness.py · hooks.py  project references and approved executable hooks                │
│    usage.py     usage ledger        expcheck.py  experiment check                          │
│    plans.py · planspec.py   plans, tasks, YAML plan schema                                 │
│    supervisor.py · supervisord.py  deterministic plan supervisor and its daemon (no LLM)   │
│    autopromote.py · promotion.py  gated code promotion into main                           │
│    governance.py · approval.py · intervene.py · notify.py  proposals, PIN, steering, Telegram │
│    guide.py · evidence.py · setup_agents.py · fullreport.py · agentsmd.py     │
│    configfile.py layered sparse config   localbin.py  .research/bin    yamlio.py           │
│    search.py · compare.py · report.py                 gitinfo.py (incl. opt-in snapshot)  │
│    context.py   resume context, Research Home aggregation, checkpoints, current.md        │
│    views.py     detail aggregations + generated Markdown sections                         │
│    backends/    local.py · slurm.py         runner.py  (job wrapper)                      │
│    hpc/         config · transport · remote · bundle · datasets · results                  │
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
| `research.supervisord` (one per project with a running plan, or when Telegram is enabled) | until `supervisor stop`; reopens the project each pass, so settings apply live | yes |
| agent CLI sessions (Claude Code, Codex, Antigravity) started by dispatch or the supervisor | one attempt, in the task's worktree | only through `research` commands |
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
| Plan | `PLAN` | 3 | `PLAN-001` |
| Task | `T` | 3 | `T-042` |
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

#### Plan

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

#### Task

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
| `artifacts` | list of A ids | set by `complete_task(artifacts=…)` / `add_task_artifact` |
| `goal`, `inputs`, `expected_outputs`, `acceptance_criteria`, `verification`, `result`, `blockers`, `notes` | Markdown | sections |
| `completed_by` | text | free text (agent name or person) |
| `started_at`, `completed_at` | timestamps | set automatically on the transition into `running` / `done` |
| `claimed_by`, `claimed_at` | text, timestamp | who holds the task (set by `start_task`, cleared by `release_task`) — §7.6 |
| `skills` | list | project skill names to load for this task (may name skills not installed yet; shown as missing) |
| `checks` | list of check objects | verification checks that gate completion: `{type: command, run}`, `{type: file, path}`, `{type: metric, name, op, value, target?}` — §7.5 |

`completed_by` defaults to the acting author (e.g. `claude/implementer`).

**Ready is derived, never stored.** `is_ready = (status == "todo") and every dependency has status "done"`. A task with
no dependencies is ready as soon as it is `todo`. Dependencies are not checked for cycles and may point at tasks in other plans.

#### Review (verification record)

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
artifact→run/experiment, plan→question, task→plan/question/experiment, task→task dependencies, task→artifacts) are
columns, not rows in `links`.

---

## 4. Storage specification

### 4.1 Project layout

```
<project>/
├── AGENTS.md                         # research block between <!-- research:begin … --> and <!-- research:end -->
└── .research/
    ├── config.yaml                   # project metadata, agent_policy, backends, artifacts, resume
    ├── .gitignore                    # research.db*, cache/, bin/, reports/, hpc/bundles/, run logs
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
    ├── hpc/                          # bundles/ (ignored), incoming/, cluster state; see §14C
    ├── supervisor/                   # plan runtime state, daemon.json / daemon.lock / daemon.log, proposals, prompts/, promotions/AP-n.json, interventions/INT-n.json (SUPERVISOR.md)
    ├── promotions.jsonl              # append-only promotion candidates and gates (§14F)
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

Machine-level files (never in the project, git or the audit log): `~/.config/research-os/config.yaml` (optional machine config), `approval.json` (PIN verifier, scrypt) and
`telegram.json` (bot token and chat id, mode 0600), all in the directory of `$RESEARCH_CONFIG` when set.

Outside `.research/`, the tool writes only when asked: `AGENTS.md` (managed block), per-skill symlinks in
`.claude/skills/` and `.agents/skills/` (config `skills.link`), and MCP registrations from `research mcp install`
(`.mcp.json`, `.vscode/mcp.json`, `.cursor/mcp.json`, `.gemini/settings.json`), and, from `research setup agents --apply`, managed `.claude/agents/research-*.md`. Supervised work happens in the experiment worktrees, and `./research` is linked in the main checkout (git-excluded).

### 4.2 What is canonical

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

### 4.4 JSONL record schemas

```jsonc
// events.jsonl
{"ts","entity_type","entity_id","action","summary","author_type","author_name"}
// action ∈ created, updated, edited (hand edit), status, baseline, synthesized, superseded, registered,
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

### 4.5 `status.json` (runner-owned)

```json
{"state": "running|completed|failed|cancelled", "runner_pid": 123, "pid": 124, "hostname": "node01",
 "started_at": "…", "ended_at": "…", "exit_code": 0,
 "slurm_job_id": "4242", "slurm_nodelist": "node01", "slurm_partition": "gpu"}
```

### 4.6 SQLite index

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
is imported **first**. Malformed edits block export and preserve the file. This is not a filesystem lock against an editor writing between the hash check and replacement; concurrent external editing still requires coordination.

### 5.5 Rebuild (`research rebuild`, or automatic when `research.db` is missing)

1. Back up the readable live DB with SQLite backup into a unique `.research/cache/research.db.bak-*` file (skipped for the automatic first build). Keep the live database inode in place.
2. In one transaction:
   - import every mirror file
   - import every run directory, re-reading run metrics from offset 0 and restoring the artifact offset from `run.json`
   - ingest the root JSONL files
   - load `events.jsonl`
3. Duplicate IDs, malformed mirrors/run records and malformed complete JSONL records abort the transaction. A readable existing index remains intact. No conflicting file is discarded.
4. Advance counters and set `meta.built_at`. Rebuild does not rewrite canonical mirrors. Normal sync may refresh generated tails.

Incomplete trailing JSONL lines are not ingested; appending refuses an incomplete tail until repaired. POSIX appenders use an advisory file lock, flush and fsync. SQLite cannot transact atomically with all canonical files: multi-file mutations and audit-log appends still have crash windows. The review records this remaining release blocker. A corrupt index that cannot be opened requires stopping clients, backing up the project, and moving the index aside before reopening; the current rebuild command cannot open it.

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
  For agents, `→ supported` only happens through an independent review (§7.5); a review also drives
  `needs_work → preliminary` and `contradicted → contradicted`.
- **Decision:** `active → reversed | superseded`.
- History is never rewritten. Superseding keeps the old object visible under "Contradicted & superseded".

### 6.6 Plan

- Statuses move freely: `active` · `completed` · `blocked` · `abandoned` (`research plan update --status`, `research mark`, or the UI).
- Setting `completed` through `update_plan` stamps `completed_at` if it is not given.
- Plan status is **not** derived from task status. Completing every task does not change the plan's `status`; a supervised run's completion is tracked separately (§6.8).
- The newest `active` plan (by `created_at`) is the one shown on Research Home (§9.4).

### 6.7 Task

```
todo ──(start_task / status=running)──▶ running ──(complete_task / status=done)──▶ done
running ──(block_task)──▶ blocked          any ──(explicit)──▶ verify | todo | …
todo + all depends_on done  ⇒  "ready"  (derived; never stored)
```

- Any status can be set explicitly. Starting a non-ready task is allowed but returns a warning ("waiting on T-…").
  The UI shows the **Start** button on the task page only when the task is ready.
- **Claims (§7.6):** `start_task` records `claimed_by` / `claimed_at`; starting a task another actor holds raises
  `PolicyBlocked` (exit 3) unless `force`. `release_task` returns it to `todo` and clears the claim.
- **Completion gate (§7.5):** `complete_task` first refuses if the task's frozen acceptance tests were modified (`orchestrate`), then runs the task's checks; failures block agents and warn humans.
- **`verify` with checks:** `research task verify` moves a `verify` task to `done` when all checks pass, or back to
  `running` when any fails.
- Entering `running` stamps `started_at`, and entering `done` stamps `completed_at` (unless the caller passes them).
- `verify` means "work finished, awaiting verification". It surfaces as an attention item on Research Home. Outside supervised plans nothing
  moves tasks into `verify` automatically; a verifier agent is dispatched to it with `research dispatch verifier T-…`.
- **Next ready task** (`get_next_ready_task(plan_id=None)`): walks tasks in creation order (optionally within one plan)
  and returns the first `todo` task whose dependencies are all `done`, with resolved `dependencies[]` and `is_ready`.
  Returns `None` if there is none.

### 6.8 Supervised plans and tasks

A plan in the supervisor's care has a **run state** (`running` · `paused` · `stopped` · `completed`) and a **health** derived from it, which is what status commands and the
Plans page show: `progressing` (something is moving), `waiting_for_approval`, **`halted`** (running, nothing moving, something waits for a human), `completed`, `paused`, `not_run`.
Health never claims "running" for a plan that cannot continue; it names the blocking tasks and why.

Each supervised task has a **phase** kept in the supervisor's state (not the task's `status`): `waiting` · `ready` · `awaiting_approval` · `running` · `waiting_hpc` · `verifying` ·
`repair_pending` · `attention` · `blocked` · `verified` · `retired` (full list: `supervisor.PHASES`). `attention` and `blocked` need a human (`supervisor resolve|approve|accept`, or
`supervisor instruct` to steer in words, §14G). Attempts, budgets (`max_attempts_per_task`, `max_total_attempts`, `max_wall_minutes`, `attempt_timeout_minutes`) and failure categories
(`missing_prerequisite`, `role_contradiction`, `incomplete_deliverable`, ...) are recorded per attempt; verification results are kept in `verification_history`, never overwritten.
When all of a plan's tasks are verified the plan completes and its run state becomes `completed` (the plan's own `status` field is still not derived; §6.6).

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

**Roles:** if `RESEARCH_AGENT_ROLE` is set (dispatch sets it), the agent name becomes `<name>/<role>`, e.g.
`claude/verifier`. Roles of one CLI therefore count as different identities, which is what lets one subscription act as
implementer and independent reviewer (§7.5). **MCP:** when the server is started by a client and the environment names no
agent, the client's `clientInfo.name` from `initialize` becomes the agent name (e.g. `claude-code`).

The VS Code extension strips every agent-identity variable (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_SANDBOX*`,
`RESEARCH_AGENT`, `RESEARCH_AGENT_ROLE`, `RESEARCH_AGENT_MODEL`, `RESEARCH_AUTHOR_TYPE`) from the RPC process environment,
so **actions taken in the UI are always recorded as human**, even if VS Code itself was launched from an agent's shell. Terminals keep the user's
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
  - a "Needs Your Attention" item on Research Home and a banner on the experiment page
  - a "Needs synthesis" section in `current.md`

### 7.3 `AGENTS.md`

`research init` (and `research agents-md`) creates `AGENTS.md`, or updates **only** the text between
`<!-- research:begin` and `<!-- research:end -->`. User content outside the markers is preserved byte for byte.
The block's source is `packages/core/research/templates/AGENTS.block.md`. The current ("Research OS") version covers (sections `Running the CLI`, `Quick start`, `Core rules`, `Scientific practice`, `Remote execution`, `Context hand-off`, `Tiered agents`, `Recording work`, `Task workflow`):

- **Quick start (the context tiers):** `research current` → `research task next` / `research task context T-…` →
  `research search` / `research show` → `research --help`. Notes that every tool is also an MCP tool.
- **Core rules:**
  - every run belongs to an experiment
  - claim before working (`task start`); leave progress notes (`task note`)
  - register artifacts and findings as you work
  - synthesize after `max_runs_without_synthesis` runs
  - findings stay preliminary until an independent review; never review your own
  - never rewrite history; supersede instead
  - large outputs stay where they were produced; register their paths
  - exit code 3 means a policy blocked you: read the hint, don't work around it
- **Recording work:** `research run exec`, in-code `log_metric` / `register_artifact`, `finding create --supports`, `checkpoint create`
- **Task workflow:** `research task start` → work + notes → `research task complete --result` (runs the checks)
- **Scientific practice:** read context in layers, keep intent / implementation / observation / interpretation distinct, bind every number
  to a run metric (`research evidence bind … && verify`), never tune on held-out results.
- **Remote execution (only when `hpc.clusters` is configured):** `research hpc doctor` first, never `ssh` by hand, never submit twice,
  `hpc sync` before proposing more runs, bundles for human-run jobs.
- **Tiered agents:** `research strategy show`, task contracts (`research task context`), `orchestrate verify`, and hooks (`research hook list`).

Every command the block names exists and is covered by a test that parses each `research …` line in the template
(`test_agents_md_only_references_real_commands`):

| Command in the block | What it does |
|---|---|
| `research current` | regenerates and prints `context/current.md` (§9.3) |
| `research task context T-…` | focused brief for one task (§9.5) |
| `research skill show <name>` | prints a skill's `SKILL.md` (`skill` is an alias of `skills`) |
| `research task next` / `start` / `note` / `complete` | task lifecycle (`complete` is an alias of `done`) |
| `research search "…"` | retrieval over all records (§9.6) |

The step-by-step protocol now lives in the `run-experiment` and `reproduce-paper` skills.

### 7.4 Default skills (`.research/skills/`)

| Skill | Purpose |
|---|---|
| `run-experiment` | Register → sanity run → bounded runs → artifacts → synthesize → stop. |
| `synthesize-experiment` | Turn runs into a synthesis, promote only reusable findings, decide, mark the experiment, refresh context. |
| `reproduce-paper` | Six-phase reproduction protocol (understand, plan, implement, sanity run, reproduce, synthesize) with a stop rule. |
| `clean-dataset` | Treat cleaning or conversion as an experiment with validation artifacts and exclusion findings. |

Prompts: `resume-session.md`, `synthesize.md`. Templates: `experiment-brief.md`, `note.md`.
`init` never overwrites existing files in these folders.

### 7.5 Verification gates

Two gates keep agent output from silently becoming "established truth". Both raise `PolicyBlocked` (exit code 3, a
subclass of `BudgetExceeded`) for agents, and only warn humans unless `agent_policy.enforce_for_humans` is true.

**Task checks gate completion.** A task's `checks` are deterministic tests, run in the project root:

| Check | String form | Passes when |
|---|---|---|
| command | `cmd:pytest -q` (or any string without a prefix) | exit code 0 within `verification.check_timeout_seconds` (600); the last 2 KB of output is recorded |
| file | `file:outputs/fig3.png` | the path exists and, if it is a file, is not empty |
| metric | `metric:rmse<=0.05` · `metric:EXP-002:rmse<=0.05` · `metric:RUN-0007:acc>=0.9` | the value satisfies the operator (`<= >= < > == !=`). The target defaults to the task's related experiment; for an experiment, the latest completed run that logged the metric is used, then experiment-level metrics |

- `research task verify T-…` runs them and appends a `check` record (`T-…/V<n>`, verdict `pass` / `fail`, per-check
  details). A task in `verify` moves to `done` on pass, or back to `running` on fail.
- `research task done T-…` (`complete_task`) runs the checks first when `verification.checks_gate_task_completion` is
  true (default). A failure blocks agents with the failing checks in the message; humans get a warning and the task completes.
- Tasks without checks complete as before.

**Findings need an independent review to become `supported`.** With `verification.require_review_for_supported` (default
true), an agent cannot create a finding as `supported`, nor move one to `supported` through `update`, `mark`/`set_status`
or the RPC. The only route is a review:

```
research finding review F-003 --verdict supported|contradicted|needs_work --notes "<reasoning>" [--confidence …]
```

| Rule | Detail |
|---|---|
| Independence (`verification.independent_reviewer`) | an agent may not review a finding whose author is an agent with the same `author_name` (role included, so `claude/implementer` ≠ `claude/verifier`) |
| Evidence (`verification.require_evidence_for_supported`) | a `supported` verdict needs at least one `supports` link |
| Effects | `supported` → status supported (and confidence if given) · `needs_work` → preliminary · `contradicted` → contradicted, with the reasoning appended to *Contradicting evidence* |
| Record | a `review` record `F-…/R<n>`; events `reviewed` |

Humans can always set `supported` directly (the UI is always human, §7.1). Known gap by design: a hand edit of a finding's
mirror file is imported as a human edit (§22.1).

**Attention items** (`verification.attention_items`, shown on Research Home and in `current.md`):
`verification_failed` (latest check run of an unfinished task failed), `reviewer_disagrees` (latest review is
`contradicted` or `needs_work`), `awaiting_review` (up to 3 preliminary agent findings with no review yet).

### 7.6 Coordination between agents

Several agents (and you) work from one task queue without stepping on each other.

| Mechanism | Behaviour |
|---|---|
| **Claim** | `research task start T-…` sets `status=running`, `claimed_by=<author>`, `claimed_at`. Starting a task someone else holds raises `PolicyBlocked` ("already claimed by claude/implementer since …"); `--force` takes it over. `task next` never returns running (claimed) tasks. |
| **Progress notes** | `research task note T-… "…"` appends a `progress` event. `task context`, the task page and the dispatch brief show the latest notes: the hand-off trail for whoever continues. |
| **Release** | `research task release T-… [--note "…"]` returns the task to `todo`, clears the claim, and records the note. |
| **Stale tasks** | a running task with no activity (update, note, check, claim) for `coordination.stale_task_hours` (default 4) is listed by `stale_tasks()`, flagged `STALE` in `current.md`, shown as a `stale_task` attention item on Research Home and as a banner on the task page. |

### 7.7 Roles, profiles and dispatch

Dispatch prepares the work and starts an agent session that you watch in a terminal; nothing calls model APIs directly (agents are provider CLIs). Unattended execution exists only under the supervisor (§14G), which dispatches the same way and is bound by the same gates.

```yaml
agents:
  default_profile: claude
  roles: {planner: null, implementer: null, verifier: null, analyst: null}   # null → default_profile (set e.g. implementer: codex)
  profiles:
    claude: {command: claude, args: [], model: null, model_flag: --model, prompt_flag: null}
    codex:  {command: codex,  args: [], model: null, model_flag: -m,      prompt_flag: null}
    agy:    {command: agy,    args: [], model: null}                          # Antigravity (adapter: headless --mode accept-edits, native resume)
    gemini: {command: gemini, args: [], model: null, model_flag: -m,      prompt_flag: -i}
    # used by the built-in allocation strategies (levels fast / balanced / deep); swap a model here, not in routing
    claude-fast:     {command: claude, args: [], model: claude-haiku-5-5,  model_flag: --model, headless_args: [-p, --output-format, json], usage_parser: claude_json}
    claude-balanced: {command: claude, args: [], model: claude-sonnet-5-5, model_flag: --model, headless_args: [-p, --output-format, json], usage_parser: claude_json}
    claude-deep:     {command: claude, args: [], model: claude-opus-5-5,   model_flag: --model, headless_args: [-p, --output-format, json], usage_parser: claude_json}
```

- **Profiles** are agent CLIs. Add one for any tool that takes a prompt argument. `model` is optional; switching models
  or providers (e.g. when one runs out of credit) is a one-line change, and research state is unaffected.
- **Roles:** `planner` (turn an objective into a plan with checks; don't implement), `implementer` (one task: claim,
  load skills, run experiments, notes, findings, `task done`), `verifier` (run checks / review a finding against its
  evidence; separate guide for tasks and findings), `analyst` (synthesize an experiment, propose findings).
- **Default role from the target:** finding → verifier · experiment → analyst · plan → planner · task → verifier if
  in `verify` or of type `verification`, else its `assigned_role` if it is a role, else implementer · no target → planner.
- **`dispatch(role, target, objective, profile, model, instructions, strategy, level, policy, headless)`** writes a brief to
  `.research/cache/dispatch/<target>-<role>-<timestamp>.md` (role guide, objective, extra instructions, the task brief
  (§9.5) or target summary, then `current.md`) and returns `argv`, `env`, `brief`, `available` (command on PATH) and a
  copy-pasteable `shell` line. It logs a `dispatched` event; it never starts anything itself.
- **argv** = `command + args + [model_flag, model] + [prompt_flag] + [prompt]`, where the prompt tells the agent to read
  the brief. **env** = `RESEARCH_AGENT=<profile>`, `RESEARCH_AGENT_ROLE=<role>`, `RESEARCH_ROOT`, `RESEARCH_BRIEF`,
  `RESEARCH_AGENT_MODEL` (if any): everything the session records is attributed to `<profile>/<role>`.
- **CLI:** `research dispatch ROLE [TARGET] [--objective] [--profile] [--agent-model] [--instructions] [--print]` runs the
  agent in the current terminal (or prints the command); `research task dispatch T-…` picks the role from the task;
  `research agents` lists profiles, roles and availability. **VS Code:** *Dispatch to Agent…* picks role and profile,
  then opens a terminal named `🤖 profile/role · target` with the env set.
- **Providers and execution records.** `providers.py` is the only place that knows each CLI's flags (`claude`, `codex`, `agy`, `custom`) and declares capabilities
  (interactive, headless, model, effort, structured output, usage, resume, MCP, instructions file) so no caller assumes parity. A task dispatch writes a single-use
  **execution record** (`execution.py`) and a wrapper that takes `.research/locks/<task>.execution`; `research execution list|show|logs|stop|retry` operate on it. A stopped
  or failed task can continue with another provider in the same worktree from a portable context document (`supervisor continue`, `agents.handoff`; refused while the
  previous process may still run). Details: [PROVIDERS.md](PROVIDERS.md).
- **Allocation strategies (opt-in).** `orchestration.active_strategy` (or `--strategy NAME` for one dispatch) switches profile choice
  from the `roles` map to `strategy.select()`, the single resolver used by `research dispatch`, `orchestrate route|dispatch|delegate`
  and `guide refresh`. A strategy (`orchestration.strategies.<name>`, merged along `extends:`; dicts merge key by key, lists and scalars
  replace) maps logical **levels** to profiles (`levels`), task **classes** (planning · scientific · verification · checked · unchecked ·
  mechanical) to levels (`route`), and defines `coordinator`, `guide`, `attempts`, `escalate` (level → level | `human`),
  `max_escalations`, `on_scientific_assumption`. Built-ins: `sonnet_first`, `opus_first`, `haiku_max`, `sonnet_heavy` (README table).
  Precedence, highest first: `--profile`/`--agent-model` > `--level` > per-task pin (`orchestrate pin`; implementer role only) >
  `--strategy` for the call > project file > machine file > built-in. A missing profile, an invalid strategy, or a level whose model
  is not in `orchestration.available` fails with a hint; no other model is substituted. `strategy validate` rejects unknown levels,
  missing routes and escalation or `extends` cycles. Task class comes from metadata only (type, role, checks + acceptance criteria).
  Verifier dispatch avoids the profile+model that implemented the task and records `independent: false` when it cannot.
- **Snapshot.** A strategy dispatch writes `<brief>.policy.json` (strategy, origin, fingerprint of the resolved policy and profiles,
  class, level, profile, provider, model, reason, overrides, independence, harness fingerprint, git revision, status), appends a copy
  to the task ledger `dispatches[]`, sets `RESEARCH_STRATEGY`, `RESEARCH_STRATEGY_FINGERPRINT`, `RESEARCH_AGENT_LEVEL`, and logs the
  strategy in the `dispatched` event. History never re-reads the current config; running sessions keep what they started with.
- **Harness in briefs.** `harness:` skills, context files, instructions file and verification commands are appended to every brief
  (missing ones flagged). **Headless:** `headless=True` adds the profile's `headless_args`; `run_headless()` runs the session and
  records provider-reported usage via the profile's `usage_parser` (`claude_json`); profiles without `headless_args` are refused.

### 7.8 MCP server

`research mcp` serves the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0), standard library only.

- **Protocol:** `initialize` (echoes a supported `protocolVersion`: 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05;
  otherwise answers 2025-06-18), `ping`, `tools/list`, `tools/call`; `resources/list` / `prompts/list` return empty lists;
  notifications get no response; JSON-RPC batches are accepted. Server `instructions` summarise the protocol for the agent.
- **Execution:** the server reuses the RPC server's project handling (reload config, sync, rebuild if the index vanished).
  Each call runs with stdout redirected to stderr (stdout belongs to the protocol) and regenerates `current.md` after
  mutating tools. Results are text (JSON for structured data). `ResearchError`s, including policy blocks, come back as
  `isError: true` results with the message, hint and exit code; unknown tools are JSON-RPC errors.
- **Tools (44):** `research_current`, `research_search`, `research_show`; `task_next`, `task_context`, `task_start`,
  `task_note`, `task_done`, `task_block`, `task_release`, `task_verify`, `task_create`, `plan_create`;
  `question_create`, `experiment_create`, `run_start` (always detached; poll `run_status`), `run_status`, `run_logs`,
  `runs_compare`, `metric_log`, `artifact_register`, `experiment_synthesize`; `finding_create` (always preliminary),
  `finding_review`; `checkpoint_create`; `skills_list`, `skill_show`; `dispatch_prepare` (follows the active strategy); `report_full`; `doctor`; `hpc_doctor`, `hpc_sync`, `hpc_bundle`. Supervised-agent and governance tools: `agent_report` (progress, artifacts, metrics, runs, HPC jobs, problems, finish), `agent_propose` (a plan change as a partial YAML patch), `plan_status`, `experiment_propose` (agents may not create experiments; a human approves with the PIN). Scientist Guide: `guide_highlights`, `guide_draft_check`. There are no MCP tools yet for strategies, hooks, contracts, usage or experiment check (CLI/RPC only). Read-only tools carry
  `annotations.readOnlyHint`.
- **Install:** `research mcp install [--client claude|vscode|cursor|gemini|codex]…` (default claude + vscode) merges a
  `research` entry (`command: research, args: [mcp]`) into `.mcp.json` (`mcpServers`), `.vscode/mcp.json` (`servers`),
  `.cursor/mcp.json`, `.gemini/settings.json`, keeping other servers; for Codex (per-user config) it returns
  `codex mcp add research -- research mcp`. When `research` is not on PATH, `command` is the absolute path of the
  project-local launcher `.research/bin/research` (§13.8), created if missing.

### 7.9 Project skills

Skills are Agent Skills folders (`SKILL.md` with `name` and `description` front-matter, optional `scripts/`,
`references/`, `assets/`) in `.research/skills/<name>/`. Imported skills are copied verbatim; their provenance and project
settings live in `.research/skills.yaml`, never in the SKILL.md.

| Operation | Behaviour |
|---|---|
| `skills add SOURCE [--only N…] [--list] [--force] [--always] [--applies-to T…] [--no-link]` | SOURCE is a folder, a git URL (`https://…`, `git@…`, `file://…`), `owner/repo`, or a GitHub `…/tree/<ref>/<path>` URL (shallow clone of that ref). **Discovery:** the folder itself if it has SKILL.md; otherwise every SKILL.md folder below it, skipping hidden folders (`.git`, `.claude-plugin`, `.openclaw`, …) and `node_modules`; duplicates by name prefer the copy under a `skills/` folder (so ponytail's `skills/ponytail/` wins over its agent-specific copies). `--list` previews without importing. Existing skills are refused unless `--force`, which moves the old copy to `.research/cache/removed-skills/`. Records `source`, `subpath`, `ref` (commit for git sources), `added_at`. |
| `skills update [NAME…]` | re-import from the recorded source (all imported skills by default) |
| `skills remove NAME` | moves the folder to `.research/cache/removed-skills/`, drops its links and manifest entry |
| `skills set NAME [--always/--no-always] [--applies-to T…]` | `always`: listed in `current.md` under *Project skills* for every session (e.g. a coding-style skill like ponytail). `applies_to`: task types/roles it is suggested for |
| `skills link [--target claude|agents|DIR]` | per-skill relative symlinks `.claude/skills/<name>` (Claude Code) and `.agents/skills/<name>` (Codex and other Agent Skills tools) → `.research/skills/<name>`. Entries that aren't ours are never touched; our links to removed skills are deleted. Falls back to copying where symlinks aren't allowed. Runs automatically after add/update/remove for the targets in `skills.link`. |
| `skills show NAME` · `skills list` | print SKILL.md · list with source, always-on and applies-to |

**Matching to tasks** (`skills_for`): skills assigned to the task, assigned to its plan, whose `applies_to` contains the
task's type or role, and always-on skills, each with a reason. `task context`, the task page and dispatch briefs list them
("load with `research skill show NAME`"); assigned skills that aren't installed are flagged.

---

## 8. Execution: runner, local backend, SLURM backend

### 8.1 Run creation (`create_run`)

1. Check the budget (§7.2). If the run is about to execute (`status=queued`, backend other than `manual`), run the approved
   `pre_experiment` harness hooks; a failing required hook raises `PolicyBlocked` (exit 3) before anything is written.
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

### 8.7 Parameter sweeps (`research run sweep`, `sweep_run`)

```
research run sweep EXP-001 [--grid lr=1e-3,1e-4 --grid bs=32,64] [--mode local|detach|slurm] [--dry-run] [--override R] -- python train.py --lr {lr} --bs {bs}
```

- **Points:** the Cartesian product of the `--grid` axes (values parsed like `--param`), or, without `--grid`, of the
  experiment's list-valued parameters. Scalar experiment parameters are added to every point.
- **Command:** `{name}` placeholders are replaced by the point's values; each run gets `parameters` = the point and
  `label` = `name=value, …` over the swept axes.
- **Checked up front:** the sweep is refused (agents; warning for humans) if it would exceed the runs left before
  synthesis is required, or take the experiment past `planned_runs`. Because the whole sweep is checked before the first
  run, it never stops halfway through. `--override "reason"` is logged once for the sweep.
- **Modes:** `local` runs the points one after another in the foreground; `detach` starts them all in the background;
  `slurm` submits each (`--partition/--time/--gpus/…` as for `run submit`). A `swept` event records the sweep.

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

**Git snapshot (opt-in).** `create_checkpoint(commit=True)` (`research checkpoint --commit`, the form's checkbox, the MCP
`checkpoint_create` tool), or every checkpoint when `git.commit_checkpoints: true`, then runs `gitinfo.snapshot`:
`git add -- .research` and `git commit --only -m "research: CP-00N <title>" -- .research`. Only `.research/` is committed;
anything else you have staged stays staged and uncommitted; nothing is pushed. The `committed` event is logged *before*
the commit, so it is included, and `current.md` ignores the HEAD line when deciding whether to rewrite itself, so the
tree is clean right after a snapshot. Returns `snapshot_commit` (or `null` if there was nothing to commit). The draft
carries `commit_default` (the config value) for the form.

### 9.3 `.research/context/current.md`

- Regenerated after every mutating CLI command, RPC call and MCP tool, and on `research context` / `research current`
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

### 9.4 Research Home (`research_home`, RPC `home`)

The aggregation behind the VS Code landing page. It returns **everything in `resume_context`** plus:

| Field | Computation |
|---|---|
| `active_plan` | the newest plan with status `active`, with its tasks and counts (`get_plan_with_tasks`), or `null` |
| `tasks` | that plan's tasks in creation order |
| `current_task` | the last `running` task in the plan (with dependencies resolved) |
| `next_task` | `get_next_ready_task(active_plan)` |
| `blocked_tasks` | tasks in the plan with status `blocked` |
| `current_focus` | **From the plan, if one is active:** `question` = objective (or title), `understanding` = latest checkpoint's understanding, `next_step` = next ready task's title, `blocker` = first blocked task's `blockers`. **Otherwise, from the latest checkpoint:** goal (or project goal), understanding, `next_experiment`, `current_problem`. `null` if neither exists. Includes `source` (`plan` / `checkpoint`) and `source_id`. |
| `attention_items` | ordered: blocked tasks (`task_blocked`, high); tasks in `verify` (`verification_needed`, medium); experiments over the run or failure budget (`synthesis_required`, high); stale running tasks (`stale_task`, medium, §7.6); failed checks (`verification_failed`, high), reviewer disagreements (`reviewer_disagrees`, high) and agent findings awaiting review (`awaiting_review`, low) (§7.5); unreviewed failed runs from `blockers` (`failed_run`, medium). Each item has `kind`, `severity`, `id`, `title`, `message`. The page shows up to 8. |
| `key_outputs` | ≤ 6 image or table artifacts: up to 2 cited as evidence by each of the first 5 established findings, then up to 3 of the baseline experiment's newest. Each has `reason`. |
| `findings_by_status` | `supported` / `preliminary` (from the first 6 established findings, with `evidence_count`), and `failed` / `contradicted` (from the first 4 failed directions) |

The CLI has no `home` command. `research resume` prints the underlying resume context.

### 9.5 Task context (`task_context`, `research task context`, RPC `task_context`)

A bounded brief for working on one task, so an agent doesn't need the whole history:

| Field | Content |
|---|---|
| `task` | id, title, status, type, role, goal, inputs, expected outputs, acceptance criteria, verification, result, blockers, notes, artifacts, depends_on |
| `is_ready` | §6.7 |
| `plan` | id, title, status, objective, success criteria, context |
| `dependencies` | id, title, status and **result** of each dependency |
| `question` | related question: id, title, status, description |
| `experiment` | related experiment: id, title, status, hypothesis, parameters, success criteria, stop conditions, baseline flag, run `state` (§6.3), latest synthesis (interpretation, next) |
| `claimed_by` | who holds the task |
| `checks`, `last_verification` | the checks as text, and the latest check run (id, verdict, summary) |
| `progress` | the 5 latest progress notes |
| `skills` | `skills_for(task, plan)`: name, description, path, why, missing (§7.9) |
| `related_findings` | up to 6 findings linked to the related experiment, its runs, or the related question |
| `policy` | agent_policy |

`blockers` is shown only while the task is blocked (the text stays on the task as history). The CLI renders it as
sections and ends with the commands to finish (`task done`) or report a blocker (`task block`); `agents.task_brief_md`
renders the same data as Markdown for dispatch briefs, the MCP `task_context` tool and VS Code's *Show Agent Brief*.

### 9.6 Search (`research search`, RPC `search`, MCP `research_search`)

Keyword retrieval, the on-demand tier below `task context`. Every term must appear (case-insensitive substring) in the
record's title, id or body; score = 3 × title hits + body hits, ties newest first; default limit 20.
Sources: every mirrored entity (title + its Markdown sections), syntheses, runs (label, command, notes, parameters),
artifacts (name, path, description), reviews (summary), notes (file text) and skills (name, description).
`--type` (repeatable) limits the record types. Each hit has `id`, `type`, `title`, `status`, `snippet`, `score`.

### 9.7 Reports (`research report`, RPC `report`)

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

### 9.8 Run comparison (`research run compare A B`, RPC `compare_runs`, MCP `runs_compare`)

`compare_runs(a, b)` returns: `parameters` (union, with `same`), `metrics` (latest value per name on each side, `delta` =
b − a and `rel` = delta / |a| for numbers), `meta` (status, exit code, duration, backend, host, SLURM job, branch, commit,
uncommitted changes, command, working dir, label, experiment, start), `same_commit`, `code_diff` (`git diff --stat` and
patch ≤ 200 KB between the two commits, excluding `.research/`, when they differ), `uncommitted` (each run's `git.diff`),
`artifacts` matched by name (`same_hash` when both hashes match), and `changed_parameters`. The CLI prints the
differences; VS Code shows the side-by-side page (§13.5).

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
| `research current` | regenerate and print `current.md` (`--json` → `{path, markdown}`) |
| `research show ID` | any object, including `note:…` |
| `research mark ID STATUS [--reason]` | set status of a question, experiment, finding, decision, run, plan or task. For a task marked `blocked`, `--reason` is stored as its `blockers`. |
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
| `research skills list` · `show NAME` · `new NAME [-d]` · `duplicate SRC NAME` | alias `skill`; `show` prints `SKILL.md` (matches the front-matter name or folder name; exit 4 if unknown) |
| `research plan create TITLE [-o OBJECTIVE] [-q Q] [--success] [--context] [--status]` | aliases `plans`, `p`; subcommand alias `new` |
| `research plan list [--status]` | shows `done/total tasks` per plan |
| `research plan show ID` | plan, tasks, ✓ ready markers, dependency arrows |
| `research plan update ID [--title] [-o] [-q] [--success] [--context] [--status]` | |
| `research task create PLAN TITLE [-g GOAL] [--type T] [--role R] [--status] [--depends T…] [--inputs] [--outputs] [--acceptance] [--verification] [-q Q] [-e EXP] [--notes] [--check C]… [--skill S]…` | aliases `tasks`, `t`; `--type` and `--role` limited to the recommended values |
| `research task list [-p PLAN] [--status]` · `show ID` | `show` includes resolved dependencies and readiness |
| `research task context ID` | focused working brief for an agent (§9.5) |
| `research task update ID [--title] [-g] [--type] [--role] [--status] [--depends…] [--inputs] [--outputs] [--acceptance] [--verification] [-q] [-e] [--result] [--blockers] [--notes] [--check C]… [--skill S]…` | only the flags you pass are changed; `--check`/`--skill` replace the lists (`--check ''` clears) |
| `research task start ID [--role R] [--force]` | claim + `running`; refuses a task someone else holds unless `--force` (§7.6) |
| `research task note ID TEXT…` | progress note |
| `research task release ID [--note]` | back to `todo`, claim cleared |
| `research task verify ID` | run the checks, record `T-…/V<n>`; exit 1 if any fail (§7.5) |
| `research task dispatch ID [--role] [--profile] [--agent-model] [--objective] [--instructions] [--print]` | hand the task to an agent CLI (§7.7) |
| `research task done ID [-r RESULT] [--by WHO] [--artifacts A…]` | alias `complete`; runs the checks first (blocks agents on failure); → `done` |
| `research task block ID REASON` | → `blocked`, stores `blockers` |
| `research task next [-p PLAN]` | first ready task, or "No tasks ready" (`null` with `--json`) |
| `research plan create/update … [--skill S]…` | skills for every task in the plan |
| `research finding review ID --verdict supported\|contradicted\|needs_work [--notes] [--confidence]` | independent review (§7.5) |
| `research run compare A B` | alias `diff`; §9.8 |
| `research run sweep EXP [--grid K=V1,V2]… [--mode local\|detach\|slurm] [--dry-run] [--override R] [slurm opts] -- CMD {K}` | §8.7 |
| `research search QUERY… [--type T]… [--limit N]` | §9.6 |
| `research report [SCOPE] [--format md\|html\|both] [-o FILE]` | §9.7 |
| `research checkpoint … --commit` | also commit `.research/` (§9.2) |
| `research dispatch ROLE [TARGET] [--objective] [--profile] [--agent-model] [--instructions] [--print]` | §7.7 |
| `research agents` | profiles, roles, availability |
| `research mcp [serve]` · `research mcp install [--client …]…` | §7.8 |
| `research bin [install|status] [--force]` | §13.8 |
| `research skills add SOURCE [--only N…] [--list] [--force] [--always] [--applies-to T…] [--no-link]` | alias `import`; §7.9 |
| `research skills update [NAME…]` · `remove NAME` (alias `rm`) · `set NAME [--always\|--no-always] [--applies-to T…]` · `link [--target …]` | §7.9 |

After every **mutating** command, `current.md` is regenerated. (`note` commands and `skills list/show/new/duplicate/link`
are not counted as mutating.)

### 10.x Scientist Guide, orchestration, tuning and evidence (optional layer)

| Command | Purpose |
|---|---|
| `guide init [--dry-run]\|status\|highlights\|star F-…\|unstar F-…\|log [F-…]\|cite list\|check\|add\|draft [F-…] [--run]\|draft done [F-…]\|draft check\|publish` | Scientist Guide (see `docs/GUIDE.md`). `init` scaffolds the book on the main checkout (never overwrites) and the publish workflow. Important results = `guide.highlights` (supported · starred · all) plus every starred finding (`highlight` tag); `log` adds one entry per result to `results.qmd` (never rewritten; a deleted entry stays deleted). `draft` prepares a bounded agent session (limits in the brief, baseline in `provenance/draft.json`) and never runs it without `--run`; `draft check` reports overruns; `draft done` records what was drafted. `cite add` appends validated BibTeX; a cited key missing from `references.bib` is a validation error. `claim\|figure\|section\|validate\|coverage\|quarto …\|chapter add\|review …` are the provenance, build and review commands. |
| `setup agents [--provider claude] [--preset scientific] [--apply] [--approve ponytail] [--probe] [--strategy NAME]` | Dry run by default. Writes managed `.claude/agents/research-*.md` (per strategy level when a strategy is active; managed legacy tier files are moved to `.research/cache/removed-agents/`), copies first-party skills, enables `orchestration`. Never installs MCP servers, plugins or hooks. |
| `orchestrate route T-… [--tier LEVEL] [--strategy S] [--profile P] [--agent-model M] [--available M…]` | Without a strategy (tier mode): tier/model by verifiability, with fallback (never silently below Opus for Opus work). With a strategy: the resolver's decision (level, profile, model, reason, overrides). |
| `orchestrate freeze T-… FILES` / `correct-test T-… FILE --reason … --tier L` | Hash the acceptance tests; `task done` is blocked if they differ. A correction needs a logged reason and is refused for any level that implemented (or is the strategy's checked-work level for) the task, and for legacy `haiku`. |
| `orchestrate dispatch T-… [--run] [--headless] [--allow-unfrozen REASON] [--strategy S] [--tier L] [--profile P] [--agent-model M]` | Runs approved `preflight` hooks (a required failure blocks), then prepares/starts the routed session. Frozen tests are required for any implementer that is not the strategy's coordinator level (legacy: Haiku). The brief carries the task contract and a file-boundary baseline. `--headless` runs non-interactively and records provider-reported usage. |
| `orchestrate attempt T-… --tier L --outcome pass\|fail [--category C] [--note N] [--usage JSON]` | Bounded retries from the strategy's `attempts` / `escalate` / `max_escalations` (tier mode: `limits`). Returns retry / escalate / block / integration_review / restore_tests. `--usage` is the manual usage fallback. |
| `orchestrate delegate T-parent "title" [--tier L] [contract flags]` | Child task; parent gains it in `depends_on`. Under a strategy the level defaults to the strategy's routing of the child as specified (checkable → worker, otherwise coordinator level). |
| `hook list\|add\|enable\|disable\|approve\|revoke\|run\|results` | Project harness hooks (`harness.hooks`, `hooks_approved`); stages preflight / post_implementation / pre_experiment / post_experiment; human approval required. |
| `orchestrate contract\|verify\|pin T-…` | Delegation contract in the task ledger; deterministic verification with diagnostics (`--record` logs an attempt); per-task model pin (`--level/--profile/--agent-model/--clear`). `dispatch --headless` records provider-reported usage. |
| `plan …` / `task …` | Plans and tasks; `plan yaml validate\|diff\|apply`, `plan run`, `plan autopromote PLAN authorize\|revoke\|retry\|status` (see SUPERVISOR.md). |
| `supervisor start\|status\|stop\|reload\|restart\|tick\|approve\|accept\|resolve\|continue\|providers` | The persistent supervisor; `reload`/`restart` never touch running agents. Status shows plan health (progressing / waiting_for_approval / HALTED / completed) and concurrency. |
| `supervisor instruct T-… "text"` / `debug [PLAN]` / `intervention list\|show\|approve\|reject\|cancel\|plan` | Steer a task in words (classified actions, one PIN for protected ones); diagnose a halt. |
| `telegram setup\|status\|test\|remove\|poll` | Opt-in alerts and chat commands; credentials in `~/.config/research-os/telegram.json` (0600). |
| `proposal list\|show\|approve\|reject\|revise\|experiment` / `approval status\|set-pin\|verify` | Governance (GOVERNANCE.md): agents propose, a human approves with the PIN; a protected CLI action prompts on the terminal. |
| `bin install\|link\|unlink [--name]` | Project launcher `.research/bin/research`, linked as `./research` in the main checkout (git-excluded, never overwrites). |
| `usage [--by …]` / `usage import FILE` | Summaries of `.research/usage.jsonl` (missing is never zero). |
| `experiment check EXP-… [--request-synthesis]` | Deterministic validation of runs/artifacts/hooks; lists what needs interpretation; prepares (never runs) synthesis. |
| `strategy list\|show [NAME]\|use NAME [--dry-run]\|validate [NAME]\|route T-… [--strategy] [--level]` | Named allocation strategies (`orchestration.strategies`, `active_strategy`); see README "Allocation strategies". `orchestrate`/`dispatch`/`guide refresh` take `--strategy`; with an active strategy `--tier` means a strategy level. |
| `tuning declare\|trial\|select\|test EXP-…` | Frozen plan; design/validation trials within a budget; one-shot held-out test after selection. |

| `evidence bind F-… --run RUN-… --metric NAME [--step N\|--select first\|last\|min\|max] [--claim X] [--unit U] [--factor K] [--rel-tol T] [--partition design\|validation\|test] [--artifact A-…] [--supersedes F-…/B1]` | Bind a number to a recorded metric. Without `--claim` the value is **read** from the evidence. With `--derive OP --input RUN:metric[@step]…` it binds a whitelisted calculation (`mean sum min max std difference abs_difference ratio relative_improvement relative_change`); no free-form expressions. |
| `evidence verify F-…` / `value RUN-… --metric NAME` | Re-read live records: `verified \| mismatch \| missing \| stale \| unverifiable` per binding. `value` prints one resolved metric. |
| `evidence suggest F-…` / `status` | Unbound numbers in a statement; project overview incl. historical integrity warnings. |
| `evidence provenance RUN-…` | Commit, dirty state, seeds, partitions, lockfile hashes, units, and explicit limitations. |
| `evidence artifact A-… [--deep]` / `pin A-…` | Integrity vs the registered hash (`ok \| modified \| missing \| unavailable \| unhashed \| possibly_modified \| directory`); `pin` appends a first-seen hash for unhashed large files. Registered metadata is never rewritten. |
| `source add --doi\|--arxiv\|--url` / `mark SRC-… [--identity-verified] [--examined --locator …] [--finding F-… --verdict supports\|partially_supports\|does_not_support]` / `list` | Offline literature records with identity, examination and per-finding support kept separate. |

Review rule: for a finding with bindings, a `supported` review or status change is refused unless **every** binding verifies (humans included), and the reviewer must supply `--notes` on the interpretation; independent review is unchanged. Findings without bindings behave exactly as before. `research report` adds a per-finding evidence table (value, source or calculation, artifact, partition, verification) only for findings that have bindings.

State: `.research/hooks/results.jsonl`, `.research/usage.jsonl`, `.research/guide.json`, `.research/orchestration/<T-id>.json`, `.research/orchestration/tuning-<EXP>.json`, append-only `.research/evidence.jsonl`, `integrity.jsonl`, `sources.jsonl`.

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
| `create_plan(title, objective=None, status="active", root_question_id=None, success_criteria=None, context=None)` | |
| `update_plan(id, **fields)` · `list_plans(status=None)` · `get_plan(id)` | `get_plan` adds `tasks[]` and counts |
| `create_task(plan_id, title, goal=None, task_type=None, status="todo", assigned_role=None, depends_on=None, inputs=None, expected_outputs=None, acceptance_criteria=None, verification=None, related_question_id=None, related_experiment_id=None, notes=None)` | |
| `update_task(id, **fields)` · `list_tasks(plan_id=None, status=None)` · `get_task(id)` | `get_task` adds `dependencies[]` and `is_ready` |
| `start_task(id, assigned_role=None)` · `complete_task(id, result=None, completed_by=None, artifacts=None)` · `block_task(id, blockers)` | |
| `get_next_ready_task(plan_id=None)` | §6.7 |
| `release_task(id, note=None)` · `add_task_note(id, text)` | §7.6 |
| `verify_task(id, transition=True)` · `review_finding(id, verdict, notes=None, confidence=None)` | §7.5 |
| `search(query, types=None, limit=20)` · `compare_runs(a, b)` · `write_report(scope=None, fmt="md", out=None)` | §9.6–9.8 |
| `sweep_run(experiment, command, grid=None, mode="local", override=None, dry_run=False, working_dir=None, tee=True, **slurm_opts)` | §8.7 |
| `dispatch(role=None, target=None, objective=None, profile=None, model=None, instructions=None)` | §7.7 |
| `list_skills_full()` · `add_skills(source, only=None, force=False, always=None, applies_to=None, link=True, list_only=False)` · `update_skills(names=None)` · `remove_skill(name)` · `link_skills(targets=None)` | §7.9 |
| `task_context(id)` | §9.5 |

**Exceptions:** `ResearchError` (`.exit_code`, `.hint`), and its subclasses `NotInitialized` (2), `BudgetExceeded` (3),
`PolicyBlocked` (3, a `BudgetExceeded`: verification and claim gates) and `NotFound` (4).

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
- Every method except `ping`, `init`, `index`, `config`, `config_show`, `config_check`, `update_project`, the note and skill helpers, `sync`, `rebuild`,
  `events`, `agents`, `agents_md*`, `install_bin`, `mcp_install`, `agent_setup`, `strategy_use`, `setup_agents`, `hook_*`, `task_pin` and `run_refresh` performs a full sync (§5.2) first.
- After a **mutating** method (`create`, `update`, `set_status`, `set_baseline`, `synthesize`, `supersede_finding`, `run_*`,
  `register_artifact`, `log_metric`, `init`, `update_project`, `sync`, `rebuild`, `start_task`, `complete_task`,
  `block_task`, `release_task`, `task_note`, `verify_task`, `review_finding`, `report`, `dispatch`, `skills_add`,
  `skills_remove`, `skills_update`, `skills_set`, `strategy_use`, `setup_agents`, `hook_approve`, `hook_toggle`, `hook_run`, `task_pin`, `experiment_check`), `current.md` is regenerated.
- Params are passed to the core function as keyword arguments. An unknown key fails with `-32602` ("bad parameters").

| Method | Params → result |
|---|---|
| `ping` | → `{version, root, initialized, python, pid}` |
| `init` | `{name?, goal?}` → `{root, name}` |
| `resume` | → resume context (§9.1) |
| `home` | → Research Home aggregation (§9.4) |
| `tree` | → everything the sidebar needs in one round-trip (skills include `source`, `always`, `applies_to`): project, questions, experiments (with `state`, `runs[]` each with `artifacts[]`, experiment-level `artifacts[]`), findings, decisions, checkpoints, notes, artifacts, skills, `agent{context,prompts,templates}`, `needs_synthesis[]`, **`plans[]` (each with `tasks[]`)** |
| `index` | → `{items:[{id,type,title,status,…}], statuses}` (used by pickers; includes plans and tasks) |
| `show` | `{id}` → detail view for any type (plan → `get_plan_with_tasks`; task → task with `dependencies`, `is_ready`, `plan`/`question`/`experiment` briefs, `artifact_cards`, `events`) |
| `config` | → merged config |
| `update_project` | `{name?, goal?, description?, status?}` |
| `create` | `{type: question\|experiment\|variant\|finding\|decision\|checkpoint\|note\|skill\|plan\|task, …fields}` → created object |
| `update` | `{type: question\|experiment\|finding\|decision\|plan\|task, id, …fields}` |
| `list_plans` | `{status?}` |
| `list_tasks` | `{plan_id?, status?}` |
| `get_plan` / `get_task` | `{id}` |
| `next_task` | `{plan_id?}` → next ready task or `null` |
| `task_context` | `{id}` → §9.5 |
| `start_task` | `{id, assigned_role?, force?}` |
| `complete_task` | `{id, result?, completed_by?, artifacts?}` |
| `block_task` | `{id, blockers}` |
| `release_task` · `task_note` · `verify_task` · `task_brief` | `{id, note?}` · `{id, text}` · `{id}` · `{id}` → Markdown brief |
| `review_finding` | `{id, verdict, notes?, confidence?}` |
| `search` · `compare_runs` · `report` | `{query, type?, limit?}` · `{a, b}` · `{scope?, fmt?}` |
| `agents` · `dispatch` · `mcp_install` | — · `{role?, target?, objective?, profile?, model?, instructions?}` (follows the active strategy) · `{clients?}` |
| `agent_setup` | → everything the Agent setup page renders: `strategy` (null = tier mode) with resolved levels and validation, `strategies[]`, `harness` + warnings, `hooks[]` (approval, last result), `tasks[]` (route, pin, contract, checks, frozen, attempts), `experiments[]` (state + last validation), `usage` (by strategy), `skills[]`, `mcp` (per client + other servers), `orchestration` (limits, managed agent files), `profiles[]` |
| `strategy_use` | `{name, dry_run?}` → `{previous, active, warnings}`; refuses an invalid strategy |
| `setup_agents` | `{apply?, strategy?}` → the `research setup agents` report |
| `hook_approve` · `hook_toggle` · `hook_run` | `{names?}` (human author only) · `{name, enabled}` · `{name}` |
| `task_pin` | `{id, level?, profile?, clear?}` → `{task, override}` |
| `experiment_check` | `{id, request_synthesis?}` → deterministic result, `needs_human[]`, `synthesis` |
| `config_show` · `config_check` | `{effective?, sources?}` · — (read-only; no sync) |
| `agents_md_state` · `agents_md_apply` | managed-block preview / apply |
| `hpc_status` · `hpc_sync` | `{}` · `{cluster?}` |
| `report_full` | `{scope?, check?}` → full deterministic export |
| `skills_add` · `skills_remove` · `skills_update` · `skills_set` · `skills_link` | `{source, only?, force?, always?, applies_to?, list_only?}` · `{name}` · `{names?}` · `{name, always?, applies_to?}` · — |
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
| `plan_yaml_template` · `plan_yaml_validate` · `plan_yaml_diff` · `plan_yaml_apply` · `plan_yaml_export` | `{}` · `{text}` · `{text}` · `{text, dry_run?}` · `{plan, experiment?, task?, partial?}` — YAML plans (SUPERVISOR.md) |
| `plan_control` · `plan_promote` · `promotion_hint` | `{plan, action: run\|pause\|resume\|stop, cancel_agents?}` · `{plan, experiment?}` · `{plan}` |
| `supervisor_status` · `supervisor_daemon` · `supervisor_task` | `{plan}` (phases, attempts, budgets, `health`, concurrency) · `{action: status\|start\|stop\|reload\|restart}` · `{task, action: approve\|accept\|resolve\|…, note?, reason?, attempts?}` |
| `attempt_detail` · `provider_status` · `provider_default` · `provider_mcp` | `{task, attempt?}` · `{task?}` · `{name}` · `{name, apply?}` |
| `executions` · `execution_show` · `execution_logs` · `execution_start` · `execution_stop` · `execution_retry` | execution records (§7.7): `{task?}` · `{id}` · `{id}` · `{id}` · `{id}` · `{id, profile?, model?}` |
| `freeze_tests` | `{task, paths}` |
| `interventions` · `intervention_request` · `intervention_action` | `{task?}` · `{task, message, interrupt?}` · `{id, action: approve\|reject\|cancel, note?, pin?}` (SUPERVISOR.md §17) |
| `debug_run` · `debug_latest` | `{plan}` diagnose a halt · latest diagnosis |
| `telegram_status` · `telegram_set` · `telegram_test` · `telegram_clear` | `{}` · `{token, chat_id?, discover?}` · `{}` · `{}` (the token is never returned) |
| `approval_set_pin` · `proposal_decide` | `{new, old?}` · `{id, approve, note?, pin?, rev?}` (GOVERNANCE.md) |
| `init_preview` · `init_apply` · `init_environment` · `new_experiment_preview` | Initialize Project wizard: `{spec}` · `{spec}` · `{}` · `{spec}` |
| `init_yaml_template` · `init_yaml_check` · `init_yaml_apply` · `init_yaml_export` | YAML initialization mode: `{}` · `{text}` · `{text}` · `{}` |
| `setup_preview` · `setup_apply` · `setup_environment` · `env_check` · `env_probe` | agent and Python environment setup: `{spec}` · `{spec}` · `{}` · `{packages?, python?}` · `{python, packages?}` |
| `skill_install` · `skill_toggle` · `skills_upgrade` · `pack_set` | `{names, force?}` · `{name, enabled}` · `{}` · `{id, enabled?, optional?}` |
| `hook_install` · `hook_install_starters` | `{names?, approve?}` · `{approve?}` |
| `hpc_discover` · `hpc_init` · `hpc_test` · `hpc_submit` | HPC (§14C): `{}` · `{cluster?, dry_run?}` · `{ssh_host, workspace_root?, python?, …}` · `{experiment, command, cluster?, dry_run?, approve?, resources?}` |
| `workspace_info` | `{}` — shared-ledger and workspace state (§14D) |
| `guide_status` · `guide_init` · `guide_highlight` · `guide_log` · `guide_draft` · `guide_draft_done` · `guide_validate` · `guide_build` · `guide_preview` · `guide_chapter_add` · `guide_cite_list` · `guide_cite_add` | Scientist Guide (GUIDE.md): the CLI `guide …` operations as RPC |

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
| **Plans** | tree | ≤ 3 plans: a flat list. More than 3: groups **Active** (expanded), **Completed**, **Blocked & abandoned** (collapsed). Plan description: `done/total · N running · N blocked`; the icon turns orange if any task is blocked; active plans start expanded. Children are tasks (creation order), with description = non-trivial status · role · `deps: …`. Tree context is `task` for `todo` tasks and `task.<status>` otherwise. Inline: **Add Task** on plans, **Start** on `todo` tasks, **Complete** on `running` and `verify` tasks; context menu: Change Status, Edit, Open Markdown, Copy ID, Block (running tasks). `initialSize: 2`. Empty when there are no plans (no welcome content). |
| **Overview** (view title "Focus") | webview | Goal (or "Set a project goal"); **Resume Project** button (opens Research Home); stats (questions, experiments, findings); banners for needs-synthesis and active runs; latest checkpoint card (problem 🔥 → next step →); **Create Checkpoint**; active questions (5), current experiments (6), latest findings (4), current decisions (3); an empty-state call to action. `initialSize: 3`. |
| **Questions** | tree | Top-level questions sorted investigating → open → blocked → answered → abandoned. Children are sub-questions, then that question's experiments (full experiment nodes). Investigating questions start expanded. |
| **Experiments** | tree | ≤ 5 experiments: a flat list (active first). More than 5: groups **In progress** (expanded), **Completed**, **Failed & abandoned** (collapsed). Each experiment's children: a "Needs synthesis" item (if flagged; clicking it opens the synthesis form), a **Runs** group (collapsed unless a run is active; newest first; each run's children are its artifacts), and experiment-level artifacts. If there are no runs, a "No runs yet — Run…" item. View badge = needs-synthesis count. |
| **Findings** | tree | Groups: **Established** (supported results), **Preliminary**, **Failed directions** (failure kind, not superseded), **Contradicted & superseded** (collapsed). Descriptions show confidence, evidence count and agent name. |
| **Decisions** | tree | All decisions; non-active ones show their status. |
| **Checkpoints** | tree | Newest first; the latest is marked "latest" and coloured green. |
| **Notes** | tree | Pinned first; clicking opens the Markdown file; inline pin/unpin. |
| **Artifacts** | tree | All artifacts, newest first; missing files flagged red. |
| **Agent Context** | tree | **Agent setup** (opens the page, §13.5), `AGENTS.md`, `config.yaml`, **Skills** (description as label suffix, `★ always on` prefix and star icon for always-on skills, source/applies-to in the tooltip; multi-file skills expand), Project context, Prompts, Templates. Title actions: **Open Agent Setup**, **Import Skills…**, **Register MCP Server…**. Skill context menu: Toggle Always-On, Update from Source (imported skills), Remove, Duplicate. |

**Icons and colours** (codicons with chart theme colours):

| Entity | Status → icon |
|---|---|
| Experiment | proposed: grey beaker · ready: blue beaker · running: spinning sync (yellow) · needs_review: orange bell · completed: green ✓ · failed: red ⊗ · abandoned: grey ⊘ |
| Run | queued: clock · running: spin · completed: ✓ · failed: ✗ · cancelled: ⊘ · unknown: red ? |
| Finding | supported: green verified · preliminary: yellow lightbulb · failure kind: red ✗ · contradicted: orange warning · superseded: grey history |
| Question | open: blue ? · investigating: yellow eye · answered: green ✓ · blocked: red · abandoned: grey |
| Decision | active: green law · reversed / superseded: grey |
| Plan | active: blue list-ordered · completed: green ✓ · blocked: red error · abandoned: grey ⊘ |
| Task | todo: grey circle · running: spinning sync (yellow) · verify: blue eye · done: green ✓ · blocked: red error |

Tooltips are Markdown: hypothesis, parameters, run counts, statement and limitations.

### 13.5 Webview panels

One panel per object; re-opening reveals the existing panel. Alt/Cmd/Ctrl-click opens beside the current editor.

| Panel | Sections |
|---|---|
| **Research Home** (`Cmd/Ctrl+Alt+R`, status-bar click, Overview's **Resume Project**; panel title "Research Home · &lt;project&gt;") | Rendered by `render/home.ts` from the `home` RPC (§9.4). **Header:** project name, goal (or a "Set goal" link), "Updated … ago [by agent]", branch @ commit with an *uncommitted* tag. **Current Focus** card: question/objective, current understanding, Next, Blocker (or "No blockers"). **Active Plan** card: title, `done / total`, progress bar, up to 10 tasks (status icon, title, role; running highlighted, done struck), "+N more", Open plan; or an empty state with **Create Plan**. **Two-column grid:** *Latest Insights* (≤ 3 supported, ≤ 2 preliminary, ≤ 2 failed directions, with confidence and evidence count) · *Needs Your Attention* with a count (≤ 5 items; otherwise "No intervention needed"). **Key Outputs** thumbnail grid. **Recent Activity** (8 events). **Quick actions:** Create Checkpoint · New Question · New Experiment · Add Task (if a plan is active) · Dispatch <next task> · Plan with agent… · Search · Report. The plan card also names the next ready task, and running tasks show who claimed them. |
| **Resume** (fallback) | The resume page (`render/resume.ts`), used only if the `home` request fails. Hero; banners (no checkpoint / stale or >25 changes / needs synthesis / runs in flight); Where we left off; What we believe · What failed; Current baseline + Decisions · Open questions; Experiments in progress · Needs attention; Look at these first · Recent activity; stat tiles. |
| **Plan** | Header (Plan · root question link; title; status and author pills; created/completed); toolbar (**Add Task** · **Dispatch T-…** (next ready task) · **Refine with planner…** · **Report** · Edit · Status ▾ (completed / blocked / abandon) · More ▾ (Open Markdown, Copy ID)); progress bar with done / running / blocked / remaining; Objective + Success Criteria card and Context card; **Tasks** list (status icon, id, title, goal excerpt, type, role, dependency links, agent marker, status pill) or an empty state; Activity timeline. |
| **Task** | Breadcrumb (plan › Task); title; pills (status, **ready** if ready, type, author); meta (created, started, completed, role); toolbar (**Dispatch to agent…** on todo tasks · **Verify with agent…** in `verify` · **Start** when ready · **Verify** (run checks) when it has checks · **Note** · **Complete** when running or in `verify` · **Block** when running · Edit (form) · More: Release, Show Agent Brief · Status ▾ (done / running / blocked / verify / reset to todo) · More ▾); meta shows "claimed by …"; green "Ready to start" banner with a Start button, or a red "Blocked: …" banner; orange "Stale" banner with Release; red "Checks failing" banner with Re-run; Goal; Dependencies with status; Inputs · Expected Outputs; Acceptance Criteria · Verification; **Checks** (with the last run's verdict) · **Skills to load** (why each applies; missing ones flagged); **Progress notes**; **Verification history**; Result (green tint); Related (plan, question, experiment) · Artifacts; Notes; Activity timeline. |
| **Agent setup** (*Research: Open Agent Setup*; Agent sidebar section) | `render/setup.ts` from the `agent_setup` RPC. Sections in reading order: **Model strategy** (active strategy, origin, fingerprint, work → level → model table, retry/escalation limits, validation problems, other strategies with Use, Change strategy…) · **Work in flight** (open tasks with routed level/model, pins, checks, frozen tests, contract summary; Pin a model… / Use strategy default) · **Project harness** (references with missing ones flagged; **Hooks**: stage, approval state, last result, Run now, Enable/Disable, Review and approve…) · **Experiment validation** (state, synthesis due, last check, Check) · **Model usage by strategy** (n/m reported, never zero) · **Skills** (import, always-on, update, remove) · **MCP servers** (per-client registration; other servers read-only) · **Agent files** (managed state, Set up agents…). All changes go through native quick-picks and modal confirmations; the page itself only links. |
| **Compare runs** | Header `RUN-a vs RUN-b` with *same commit / different commits* and *N parameters changed* pills, Swap button; **Metrics** table with Δ and % (green when lower, orange when higher); **Parameters** and **Run details** tables (rows that differ are highlighted); code changes between the commits (stat, full patch on demand); each run's uncommitted `git.diff`; **Outputs side by side** (images next to each other, matched by name, *identical* when hashes match). Opened from a run page, a run's context menu, an experiment's More menu, or the palette. |
| **Experiment** | Breadcrumb (question, "derived from"); title, status, baseline, author, tags; meta (runs vs planned, created, closed, git); toolbar (Run… · Attach run · Synthesize · Finding · Variant · Status ▾ · More ▾); budget or synthesis banner; design card + sidebar (parameters with delta strikethrough, delta card, metrics, expected artifacts, variants); **Runs table** (status dot, id, label, failure exit pill, dirty and agent markers, one column per parameter and metric, inline bars, lowest value highlighted, duration, backend/job, hover actions for stdout, stderr and cancel); **metric charts** (one horizontal bar chart per metric when ≥ 2 numeric values, labelled by the varying parameters); **artifact gallery** (thumbnails for images, type icons otherwise); **Syntheses** (newest first; agent ones tinted purple and labelled "agent-generated synthesis"); findings produced; decisions & linked notes; limitations and notes; activity timeline. |
| **Finding** | Kind, status, confidence meter and author pills; superseded banner; "awaiting an independent review" banner (agent findings with no review) or "Reviewer …: needs work / contradicted" banner; **Reviews** section (verdict icon, id, reviewer, time, reasoning); statement as a large quote (colour by state); supporting evidence list (with a warning banner if empty); "evidence at a glance" image gallery; contradicting evidence; limitations (with a prompt if empty); experiments; decisions based on this; related findings; reviews; history. Toolbar: **Review…** · **Ask agent to review** (dispatch verifier) · Edit · Decision from this · Status ▾ · More ▾. |
| **Question** | Description, sub-questions, experiments (run and synthesis counts), "What we learned" findings, linked notes, history. |
| **Decision** | Statement quote, Why, based-on findings, experiments, history. |
| **Checkpoint** | Goal, understanding and baseline; problems; next step; notes; important findings, known failures, open questions, active experiments (baseline starred). |
| **Run** | Status and exit pills, timings, host and job, git; toolbar (stdout · stderr · Run dir · Terminal here · **Compare with…** · Add metric · Register artifact · Cancel · Refresh); command with a copy button; working dir and `git.diff` link; parameters; latest metrics; SLURM block; artifacts; stdout and stderr tails (last ~6 KB each); citing findings. |
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
| Create plan | groups What (title*, objective, success criteria) / Context (addresses question → `root_question_id`, additional context); saved as `active` |
| Edit plan | same fields + status seg; clearing the question unlinks it |
| Add task | opened with a plan id, or after a quick-pick of active plans (offers **Create Plan** if none); groups Task definition (title*, goal, type seg: research / implementation / experiment / analysis / verification / synthesis / debug / data, role seg) / Workflow (dependencies picker filtered to tasks, inputs, expected outputs, related question, related experiment) / Quality (acceptance criteria, verification approach); saved as `todo`. Unset type, role and links are sent as `null`. |
| Edit task | same fields + status seg, result, blockers, notes; emptied text fields clear, unset type/role/links/dependencies are cleared |
| Checks and skills (task create/edit) | *Checks*: one per line in the same form the core prints (`cmd: …`, `file: …`, `metric: [EXP-…:]name <= value`); *Skills to load*: comma-separated names |
| Review finding | statement and evidence ids in the subtitle; verdict seg (supported / needs work / contradicted)*, reasoning*, confidence |
| Create checkpoint (extended) | checkbox *Also commit .research/ to git*, defaulting to `git.commit_checkpoints` |

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
| New Plan | form; palette + Plans view title bar |
| Add Task | form; palette, plan inline/context action, plan and Home toolbars |
| Start Task · Complete Task · Block Task | item-only. Start sets `running`; Complete asks for an optional result, then `done` (Esc cancels); Block asks for a reason, then `blocked` (Esc cancels) |
| Change Status… on plans and tasks | uses `update` (not `set_status`) under the hood |
| Dispatch to Agent… | role pick (suggested role first; asks for a target or, for the planner, an objective), profile pick (configured profile first, unavailable ones flagged), then a terminal `🤖 profile/role · target` with the dispatch env running the agent. Inline on todo tasks; context menu on tasks, plans, findings, experiments; buttons on task, plan, finding, experiment pages and Home |
| Plan Objective with Agent… | dispatch the planner with an objective (Plans view title, Home) |
| Search Research… | live quick-pick over `search` (Overview title bar, Home) |
| Export Research Report… | scope pick (project / question / plan / experiment), writes md + html, opens the Markdown preview, offers *Open HTML in browser* |
| Verify Task · Add Progress Note… · Release Task · Show Agent Brief | task items and page |
| Review Finding… | review form (finding items and page) |
| Compare Runs… | picks two runs (optionally within an experiment) → compare page |
| Import Skills… · Remove Skill · Update Skill from Source · Toggle Always-On · Expose Skills to Agents | folder picker or git URL / owner/repo → preview → multi-select → on-demand vs always-on; replace prompt on clashes |
| Register MCP Server for Agents… | multi-select clients; offers to copy the Codex command |
| Open Agent Setup | the Agent setup page (§13.5); also the first node of Agent Context and its title bar |
| Choose Model Strategy… | quick-pick of strategies (origin, active, validity), then a modal confirm; `strategy_use`. Also the page's *Use* buttons |
| Set Up Claude Code Agents… | previews the managed agent files, modal confirm, then `setup_agents --apply` |
| Pin Model for Task… · Use Strategy Default Model for Task | pick a task and a strategy level (`task_pin`); item/row actions on the page |
| Review and Approve Harness Hooks… | modal listing every unapproved command, stage and timeout; only this action (or `research hook approve`) can approve; item-only: Run Harness Hook · Enable or Disable Harness Hook |
| Check Experiment (Deterministic Validation) | `experiment_check`; offers *Prepare synthesis* when one is due |
| Open Project Config | item-only on the page; the palette entry opens `config.yaml` |
| Edit… on plans and tasks | opens the edit form (§13.6) |
| Regenerate Agent Context · Open AGENTS.md · Open Project Config · Set Project Goal | |
| Rebuild Index from Files | modal confirmation |
| Show Log | the "Research" output channel |
| Open Research Home · Open Settings · Open Item… | Home page; the Control Center's Settings tab; quick-pick over `index` |
| Mark Experiment Failed · Pin Note · Unpin Note · Link Note to… | item actions |
| Regenerate Agent Context (current.md) · Export Full Research Context (deterministic)… · Review Managed AGENTS.md Block… | `write_context`; `report_full`; `agents_md_state` / `agents_md_apply` |
| Reconcile HPC Runs… · Run on HPC Cluster… | `hpc_sync`; `hpc_submit` (preview first) |
| Verify Task (Run Checks) · Expose Skills to Agents | `verify_task`; `skills_link` |
| Check Configuration · Show Effective Configuration… | `config_check`; `config_show` |
| Import Plan YAML… · New Plan YAML from Template · Apply Plan YAML from Editor… · Open Plan as YAML | `plan_yaml_*` (validate, diff, then apply) |
| Run Plan (Supervisor)… | `plan_control` run; starts the supervisor if needed |
| Open Scientist Guide · Draft Scientist Guide Entry with an Agent… · Preview Scientist Guide Website | `guide_*` |

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

**Project-local CLI (`.research/bin/`).** Shells that don't inherit VS Code's terminal environment (the Claude Code
extension's Bash tool, other agent sandboxes, cron) can't see the shim. So every project also carries its own launcher:

- **Layout:** `.research/bin/research` (POSIX `sh`), `.research/bin/research.cmd` (Windows) and `.research/bin/lib/`,
  which holds a copy of the `research` and `research_cli` packages plus a `.stamp` (version and source hash).
- **Launcher:** finds Python from `$RESEARCH_PYTHON`, then the interpreter that wrote it, then `python3`, then `python`.
  It runs `python -m research_cli` with `lib/` prepended to `PYTHONPATH`, so commands started by `run exec` can also
  `import research`.
- **Refresh:** `research.localbin.install(root)` copies only when the stamp differs. It is called by
  `Project.init`, `research bin install [--force]` and the RPC method `install_bin`. The extension calls `install_bin`
  once per activation (setting `research.projectCli`, default `true`). The first time, if the managed `AGENTS.md`
  block predates the launcher, it is refreshed.
- **`./research` in the main checkout:** `install` (and `research bin link|unlink [--name]`) also links the launcher into the main checkout as `./research` so it can be run from
  there without the long path. The link is git-excluded, never overwrites a file or directory (best effort), and the launcher follows symlinks. `install_bin` also starts the
  supervisor when Telegram is enabled or a plan is running, and replaces a supervisor still running older code (agents are untouched).
- **Git:** the launcher is per machine, so `bin/` is listed in `.research/.gitignore`, and existing projects get the
  line added. A laptop and its Remote SSH host each keep their own copy.

### 13.9 Status bar

- **Text:** `$(beaker) <project>`, then `$(sync~spin) N` while runs are active, then `$(warning) N` for experiments needing
  synthesis. The background turns warning-coloured when anything needs synthesis.
- **Tooltip:** goal, counts, latest checkpoint.
- **Click:** opens Research Home.

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
| `research.projectCli` | `true` | Keep the project-local launcher `.research/bin/research` (gitignored, refreshed on open; §13.8). |
| `research.openControlCenterOnStartup` | `true` | Open the Control Center when a window with a project opens. |

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

## 14A. Full deterministic context export (`research report --full`)

**Module:** `research/fullreport.py`. **Export schema:** 1. Independent of `report.py`, whose concise
summary keeps its existing semantics and output paths unchanged.

### 14A.1 What it is

One Markdown document holding the complete recorded scientific context of a project, question, experiment
or plan, suitable for pasting into an LLM. It **retrieves, joins, validates, orders and renders** records
and nothing else: no model call, no agent dispatch, no summarization, no inferred conclusion, no
natural-language rewriting. `fullreport.py` imports no HTTP client and no agent module, and a test asserts
that rendering performs no subprocess and opens no socket.

Defaults to `.research/reports/<scope>-full.md` (`project-full.md` when unscoped). Not subject to
`context.max_chars`, top-N finding selection, or run-table truncation — a large export is expected, and the
document reports its own size and record counts.

### 14A.2 Section hierarchy

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

### 14A.3 Determinism contract

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

### 14A.4 Completeness verification (`--check`)

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

---

## 14B. The managed AGENTS.md block

**Module:** `research/agentsmd.py`. A `.research/` of another project format is rejected, never converted. Only the Research OS-owned region of `AGENTS.md` is managed.


One owned region between `<!-- research:begin … -->` and `<!-- research:end -->`. `locate()` matches whole
marker **lines**, not bare substrings, so prose that mentions a marker — in the block itself or in your own
notes — cannot be mistaken for one.

| Command | Behaviour |
|---|---|
| `--preview` | prints the block this version generates; touches nothing |
| `--diff` | unified diff of the file as it is vs. as it would be |
| `--apply` | rewrites only the managed region (the default action) |

Bytes outside the region are preserved exactly, including CRLF line endings and a missing trailing newline
(the file is read and written with `newline=""`). Markers absent → propose adding a block, leaving existing
content alone. Markers duplicated, reversed or malformed → **refuse** with the manual fix, change nothing,
and change nothing else. No three-way merge, no inference about which instructions to
remove: a deliberately human-reviewed workflow.

---

## 14C. HPC execution

**Package:** `research/hpc/` — `config.py` (cluster profiles), `transport.py` (ssh/rsync), `bundle.py`
(portable packages), `results.py` (import), `datasets.py` (discovery), `remote.py` (doctor/submit/sync).
Entirely **opt-in**: with no `hpc.clusters` configured, every command says so and nothing else changes.

### 14C.1 Identity, and why submission is never retried

```
laptop: run id + submission token allocated  →  immutable bundle  →  rsync to the cluster
     →  sbatch (writes a receipt carrying run id + token + job id as its first action)
     →  [days pass; the laptop may be off]  →  research hpc sync  →  results imported onto the SAME run
```

The dangerous failure is `sbatch` succeeding and the connection dying before the job id is read. Research OS
therefore: allocates the run id and token **before** anything is transferred; has the job script write a
remote receipt; and, when it cannot confirm a job id, records `submission_uncertain` and **refuses to
retry**. `research hpc sync` then reads the receipt (and `squeue`/`sacct`) and adopts the job that is already
running. A transient SSH failure is never taken as proof that submission failed. Tested explicitly: one job
on the cluster, one run record, no duplicate.

### 14C.2 State ownership

The laptop holds the only authoritative research history. The cluster holds execution state only, described
by small manifests in its own directories, reusing the existing run-directory conventions
(`run.json`, `status.json`, `metrics.jsonl`, `artifacts.jsonl`):

```
<projects>/<slug>/
  code/EXP-001/RUN-0007/     immutable code snapshot + .bundle-token
  runs/RUN-0007/             submission.json, status.json, metrics.jsonl, logs
  results/RUN-0007/
  metadata/project.json
```

Information flows one way (execution package out, results in). There are never two mutable `.research/`
databases, and no remote database or registry of any kind.

### 14C.3 Bundles

A bundle carries project/experiment/run identity, the hypothesis and intended test, the source commit **and
dirty-state provenance**, an actual source snapshot (uncommitted and untracked files included — a commit
alone is not a snapshot of what ran), runtime config, resource requirements, expected inputs/outputs,
environment setup, dataset references, run commands and parameters, expected metric/artifact schema, and
SHA-256 hashes of every packaged file.

It ships `research_runner` as a standalone top-level module — not `research.runner`, whose package `__init__`
would pull in SQLite and YAML. No agent, no network and no Research OS install are needed on the cluster.
Never packaged: credential-shaped files (`id_*`, `*.pem`, `.env`, `.netrc`, …, refused outright), `.git`,
`.research`, caches, and files over `hpc.bundle.max_file_bytes` unless named in `hpc.bundle.include`.

Results archives carry `manifest.json`, `status.json`, `metrics.jsonl`, `artifacts.json`, `logs/`,
`outputs/` and `checksums.json`. Import validates identity (run id, experiment, submission token), verifies
every checksum before trusting a byte, rejects absolute paths, `..` traversal and non-regular members, and
bounds member count and size. Re-importing an identical archive adds no metric, artifact or event: JSONL is
merged by content hash and artifacts are keyed on (path, run). Artifacts over
`hpc.collect.max_artifact_bytes` stay on the cluster and are registered as `remote-reference` artifacts with
their remote path and size.

### 14C.4 Datasets: no registry

The configured `shared_data` directory **is** the source of truth. A dataset is a directory under it,
described by a `manifest.json` inside that same directory. There is no `datasets.json`, no central index and
no registry service, so a new session discovers what exists by looking rather than by remembering.

- a directory without a manifest is **neither trusted nor re-downloaded** — it is inspected, then `adopt`ed
- `status` ∈ `downloading | complete | incomplete | corrupt | unknown`; the manifest is written **last**, so
  an interrupted transfer can never read as complete
- verification strength is explicit: `manifest` (status word only) → `inventory` (expected files exist,
  sizes match) → `checksum` (`--deep`, recomputed remotely). Terabytes are not rehashed every session.
- concurrency uses an atomic `mkdir` lock inside the dataset directory plus a staging directory — no
  distributed locking
- versions are distinguished by manifest `version`; a different version never overwrites an existing copy
- `auto_delete` is fixed at false: **Research OS never deletes remote datasets, code or results**

### 14C.5 Resource approval

Slurm bills what a job **reserves**, not what it uses, and a large request also waits longer in the
queue. Every submission path therefore discloses the reservation before anything is submitted:

- `research hpc submit --dry-run`, `research run submit --dry-run` and `research run sweep --mode slurm
  --dry-run` print CPUs, GPUs, memory, wall-time, partition and account, plus the CPU-/GPU-hours the
  reservation costs if the job runs to its limit. A sweep also totals that across every grid point.
- `research hpc submit` without `--yes` **refuses, and the refusal text carries the numbers**, so an
  approval cannot be sought without them.
- `reservation()` is the single place that computes this, so the CLI, the refusal and the recorded
  submission all state the same thing. A successful submission records what it reserved.
- The managed `AGENTS.md` block and the `hpc-setup` skill instruct agents to show the user these numbers
  and wait for an explicit yes, never to raise `cpus`/`gpus`/`mem`/`time` on their own initiative, and not
  to treat `execution.auto_submit` as blanket permission for a new experiment's resources.
- The cluster's `limits` remain a hard backstop that refuses an over-large request before submission —
  that is a safety net, not the approval.

### 14C.6 Safety

Dry-run preview on `submit` and `init`; resource requests bounded by the cluster's `limits`; cluster chosen
explicitly (or a single configured default); every remote path validated as absolute, metacharacter-free and
traversal-free, and checked to lie **inside** the configured roots; `rsync --delete` refused; `BatchMode=yes`
with explicit timeouts and argument lists (never string interpolation of user data); read probes retried
once on a transient error, `sbatch` never; submission requires `--yes` or `execution.auto_submit`; every
submission and retrieval logged as an event; and the original experiment/run relationship preserved exactly.

### 14C.7 Long-running work and continuation

Reconciliation is **on demand** (`research hpc sync`) and on the next session — there is no daemon and none
is required. Pending follow-up work survives process termination because it lives in the canonical run and
experiment records, not in memory. `sync` reports the experiments that now need interpretation through the
existing synthesis/task machinery and **dispatches no agent**: Slurm jobs and pre-submitted dependencies
continue while the laptop is off, but agent reasoning cannot, and Research OS does not pretend otherwise.

Checkpoint/restart is not reinvented: declare `restart_command` and `checkpoint_dir` parameters on the
experiment and the bundle carries them in `manifest.json` for your own script to use.

---

## 14D. Shared ledger and experiment workspaces (opt-in)

By default (classic mode) `.research/` sits beside the code. `research ledger enable` moves a git project to
**shared-ledger mode**: one ledger lives in its own worktree on branch `research/ledger`, and every source worktree of the
repository reaches it. Implemented in `workspace.py`.

| Concept | Where it lives | Portable? |
|---|---|---|
| Project identity | `<ledger>/.research/project.json` (`project_id`) | yes |
| Ledger root | `<git common dir>/research/ledger.json` | no (machine-local, never tracked) |
| Workspace records (`WS-001` …) | `<ledger>/.research/workspaces.jsonl`, append-only, folded on read; no paths | yes |
| Workspace → directory | `<git common dir>/research/workspaces.json` | no |
| A worktree's workspace id | `<worktree git dir>/research-workspace` (survives moving the directory) | no |
| Source repository identity | root commit hash of the repository | yes |

`Project` now separates **`root`** (the source tree: git, run working directories, `AGENTS.md`, guide) from
**`ledger_root`** / **`rdir`** (where `.research/` lives). They are equal in classic mode. Paths inside the ledger are stored as
`.research/...`; all other stored paths stay relative to the source root. Discovery is automatic from the CLI, RPC and MCP:
`find_root` walks up for `.research/config.yaml` (classic wins), otherwise asks git (`--git-common-dir`, which also covers `.git`
being a file in a linked worktree) for the link.

Invariants: one ledger, one SQLite index, one counter per prefix, so ids cannot collide; all worktrees use the existing write
lock, journal and index; history survives deleting any experiment branch (the ledger is not on it); `research rebuild` is unchanged.

**Enabling** (`research ledger enable`, dry run by default; `--apply` performs it): refuses when `.research/` files are tracked by
git, when a transaction journal is unfinished, or when the target exists. It creates the ledger branch and worktree, copies
canonical records (not the index, locks or `bin/`), rebuilds the index there and compares per-table record counts with the
original, then links and renames the original to `.research.pre-ledger`. Any failure before the link removes what was created.
Nothing is committed. To undo: remove the ledger worktree, delete `ledger.json`, rename the backup back.

**Workspaces** (`research workspace create|attach|list|show|link-task|assign|archive|open`; RPC `workspaces`, `workspace_create`,
`workspace_archive`; MCP `workspace_list`). `create EXP-041` makes branch `exp/EXP-041-<title>` and a worktree
(`<repo>-experiments/EXP-041`) from `HEAD` or `--base`; it never touches an existing branch. `archive` removes only the
directory: it refuses while runs are queued/running or while tracked changes or untracked files exist, pins the head as
`refs/research/archive/WS-n`, never uses `--force`, never deletes the branch. `--dry-run` shows the impact.

**Run provenance.** In shared mode each run records `env.workspace`: workspace id, branch, head, base revision, experiment, and a
`snapshot` of the exact working tree (tracked + untracked, not ignored) as a commit pinned by `refs/research/snapshots/<RUN>`.
It is built with a temporary index; the real index, HEAD and files are untouched.

**Not implemented yet:** a workspace view in the extension (selective promotion is built, §14F), workspace data in dispatch
briefs, snapshot export into HPC bundles, and concurrent-process tests beyond the id-uniqueness test. Windows is unverified.

---

## 14E. Typed settings and optional job isolation

### Settings registry (`registry.py`)
The YAML layers (defaults < machine file < project file) remain canonical. `registry.OPTIONS` adds, for each user-facing key: type,
allowed values, range, category, `help`, `why`, `effect`, risk (`normal` | `policy` | `risky`), scopes, files affected, and a
dependency. Defaults are read from `DEFAULT_CONFIG`, never repeated; a test checks that every option exists there with the right type and
has real help. `describe` returns effective value, default, source layer and inheritance; `preview` returns the exact YAML diff and
writes nothing; `apply` validates, refuses `policy`/`risky` keys for agents, requires `confirm` for `risky`, edits only the affected lines
(comments and unrelated keys survive), writes the machine file for `--scope machine`, records a `config` event with old value, new value
and reason, then regenerates `.research/context/policy.md`. `reset` removes the override (and an emptied parent block).
CLI: `research config options|explain|set|reset [--dry-run] [--scope machine] [--confirm] [--reason]`; RPC `settings`,
`settings_preview`, `settings_apply`. Keys without an option still work in YAML; they just have no form.

Agent context: `policy.md` (generated, regenerated with `current.md` and on every change) lists effective settings by category with their
help text and a "Verified now" section of observed facts. `AGENTS.md`'s managed block tells agents to read it and that they cannot change
policy. Dispatch briefs point at it and record `settings_fingerprint` in the dispatch policy snapshot, so a later change does not alter a
running session's recorded policy.

### Job isolation (`isolation.py`, `sandbox.py`) — optional; native stays the default
A **profile** (`sandbox.profiles.<name>`) chooses a runtime, image, filesystem access (source `read_only|read_write|hidden`, read-only
datasets, extra read-only/read-write mounts, temporary scratch; home and ledger are always hidden), network `enabled|disabled`, GPU, an
environment allowlist, and `enforcement`. Built-ins: `native`, `restricted`, `gpu-research`, `custom`. Precedence, highest first:
run (`--sandbox`) > task > experiment > cluster > project default (only while `sandbox.enabled`) > native. A restrictive profile is
mandatory: an agent cannot choose `native` over it, a human needs `--sandbox-override-reason`, and the override is logged and stored
in the run.

Each requested restriction has a state: `verified` (a canary under the same runtime and mount layout observed it holding),
`enforceable` (runtime present, not probed), or `unavailable`. If any requested restriction is unavailable, or the self-test fails, the
job is **refused before a run record exists** (`PolicyBlocked`, exit 3). The runner repeats the check at job start (compute nodes
included) and exits 126 with `sandbox_refused`; there is no fallback to native.

Runtimes: `native`; `userns` (unprivileged user+mount+pid(+net) namespaces and `pivot_root`; kernel-dependent, **not** a defence
against a hostile program that also exploits the kernel; the job sees itself as uid 0 and `/usr`, `/bin`, `/lib*` and a few `/etc` files
read-only); `apptainer` (`--containall --cleanenv --no-home --no-mount bind-paths`, explicit `--bind`, `--network none`, `--nv`).
Docker and Podman are not implemented; `register_runtime` is the extension point.

Mounts: host paths must be absolute, free of `..`, existing, and are resolved with `realpath` before binding. Refused: `/`, the home
directory, credential directories (`.ssh`, `.aws`, `.gnupg`, `.config/gcloud`, `.kube`, …, even through symlinks), the research ledger or
any parent of it, and anything outside `sandbox.approved_roots` when set. Job layout: `/workspace` (source), `/data/<name>` (datasets,
read-only), `/outputs` (`<run>/outputs`), `/run` (the run directory: logs, `metrics.jsonl`, `status.json`), `/tmp` (tmpfs). The job writes
results through run-directory files; it has no access to the ledger or index.

Provenance: `run.env.sandbox` stores the profile, scope chain, override, runtime and version, image identity (size, mtime, sha256 up to
1 GiB), resolved mounts, per-property state, and the full policy. `sandbox.json` in the run directory is what the runner reads.

CLI: `research sandbox list|show|doctor|verify|preview`; `research run exec|submit --sandbox P --sandbox-dataset PATH`.

**Agents are not sandboxed.** Job isolation does not restrict an agent's shell or SSH session. `sandbox.agent.mode: advisory` only writes
boundaries into the brief; `enforced` is unsupported and not accepted.

**Verified here:** `userns` on WSL2 (kernel 6.18, util-linux 2.39) with disposable fixtures: read-only source, read-only dataset, writes
only to outputs, protected files and fake credentials invisible, ledger invisible, clean environment, no network interfaces, isolated pid
namespace. **Not verified:** Apptainer or any site SLURM container plugin (not installed here; its argv construction and refusal
paths are tested with a fake executable only), GPU passthrough, behaviour on other kernels, and root/administrator-configured mounts.
**Not implemented:** sandboxing of remote bundle execution. `hpc submit` refuses (exit 3) when a profile applies to the cluster, rather
than run unrestricted.

---

## 14F. Selective promotion into main

`promotion.py`; records in `.research/promotions.jsonl` (append-only, folded on read, no machine paths). Requires shared-ledger mode.
Scientific success, technical correctness, reusability, integration-readiness and promotion are reported as **separate** values
(`classification`; `null` means not known); an unsuccessful experiment stays fully documented and simply never becomes integration-ready.

1. `research promote changes WS-n` lists the workspace's commits and files (uncommitted work is flagged: it cannot be promoted).
2. `prepare WS-n --commit SHA… | --path P…` creates `promote/PROMO-n-…` in its own worktree (`<repo>-promotions/PROMO-n`) from the **current**
   main and applies only the selection (`cherry-pick -x`, or `checkout <workspace head> -- paths` committed once). Paths must be relative,
   free of `..`, outside `.research`, and committed. The workspace and main are untouched. A failed preparation removes only the branch and
   worktree it just created.
3. `diff` is exactly `main...candidate`. `verify` runs `promotion.test_command` in the candidate and binds the result to its head commit.
4. Gates: tests passed for the **current** head; scientific evidence (the experiment has a supported finding; `promotion.require_supported_finding`);
   implementation `review` by someone other than the preparer (agents may review) and only after passing tests; `approve` by a **human** only,
   bound to the head commit: any later commit makes the approval `stale`.
5. `integrate --confirm` (human only) runs `git merge --ff-only` in main's worktree. It is refused when a gate is open, main's worktree has
   uncommitted changes, or main moved since preparation (prepare again from the new main). It never forces, rebases, deletes branches or pushes;
   it records the integration commit, main's previous tip and the evidence. Nothing in this system merges into main otherwise.

Surfaces: CLI `research promote …`; RPC `promotions`, `promotion_diff`, `promotion_action`; the Work page's Promotion candidates dialog
(gates, patch, actions; Merge stays disabled until approved and the patch is acknowledged). Settings: `promotion.main_branch`,
`promotion.test_command`, `promotion.test_timeout_seconds`, `promotion.require_supported_finding`.
Not implemented: hunk-level selection, running the candidate's tests inside a sandbox profile, non-fast-forward integration strategies,
automatic cleanup of promotion worktrees (they are kept), push or pull-request creation.

---

## 14G. Supervisor, governance, promotion and notifications (2.1 to 2.6)

Normative detail lives in [SUPERVISOR.md](SUPERVISOR.md) and [GOVERNANCE.md](GOVERNANCE.md); this is the map.

- **Supervisor** (`supervisor.py`, daemon `research.supervisord`): a deterministic process, no LLM. It reopens the project every pass, dispatches
  agents, verifies deliverables (`file:`/`cmd:`/`metric:` checks), repairs within budgets, and applies settings live (`daemon.json`,
  `reload.request`). Failures are classified (`missing_prerequisite`, `role_contradiction`, `incomplete_deliverable`); an incomplete deliverable
  may escalate to a stronger model or effort (`execution.escalation`, opt-in). Plan `health` reports HALTED instead of "running".
- **Plans as YAML** (`planspec.py`): partial or full scope, validated with role/task warnings, applied as reviewed diffs; scientific changes need approval.
- **Governance and the approval PIN** (`approval.py`): agents propose, humans approve; the PIN is a local scrypt verifier outside the project, read from the terminal with echo off.
- **Unattended promotion** (`autopromote.py`, opt-in): needs `execution.auto_promotion: true` and a PIN-authorized `plan autopromote authorize`; verified commits reach local `main` only by `git merge --ff-only` after independent review; fails closed; never approves science.
- **Intervention** (`intervene.py`): `supervisor instruct` produces classified actions from a closed vocabulary; protected classes (scientific, resource, frozen test, role) need one PIN approval; applied idempotently with the evidence preserved.
- **Debug agent and Telegram** (`notify.py`, opt-in): diagnosis of a halt, halt alerts and chat commands from the configured chat only. Settings: `auto_promotion.*`, `supervisor_assistant.*`, `intervention.*`, `notifications.*` (sparse per project, merged over defaults).
- **RPC:** `managed_plans` (with `health`), supervisor reload/restart, intervention, debug and Telegram methods used by the Plans page.

---

## 13A. Control Center (React)

A reusable React/TypeScript frontend (`packages/vscode-extension/src/webview/`, bundle `media/app.js`, styles `media/app.css` on top of the
VS Code theme variables). It talks only to a `Host` interface (`call`, `openEntity`, `openDoc`, `getState`, `setState`); `vscodeHost` implements it
with webview messages, and a browser host could implement it over HTTP without touching the pages. The extension relays an allow-listed set of RPC
methods (`CONTROL_RPC` in `panels.ts`: control, settings, workspaces, promotion, plans and supervisor, intervention, Telegram, governance, providers, executions, guide, tools, init/setup, doctor, reports); everything else is refused, and mutating ones re-sync the tree. Command: **Research: Open Control Center**
(`research.openControlCenter`; it also opens on startup unless `research.openControlCenterOnStartup` is off); the Home, detail and Agent-setup panels remain.

Seven tabs: Home, Work, Research, **Plans**, **Guide**, **Agents** and Settings (plus Doctor and the Initialize Project and New Experiment wizards). **Home**: attention items (synthesis due, unreviewed failures, findings awaiting review, blocked tasks, unapproved hooks, missing workspaces,
promotions in flight), each opening its workflow; next task; runs in progress; recent findings. **Work**: plans/tasks, workspaces (create, archive with
an impact preview), promotion candidates, execution profiles with the agent-boundary statement, recent runs. **Research**: experiments with run and
unsynthesized counts, findings, questions, decisions. **Plans**: managed YAML plans with validate, diff and apply, run/pause/resume/stop, plan health (progressing / waiting for approval / HALTED / completed) with concurrency, per-task phases and attempts, approvals and proposals with the PIN dialog, **Instruct** and **Unblock** (supervisor intervention), a halt panel with the debug agent's diagnosis and options (including continuing with another agent), supervisor Reload/Restart, and the Telegram box. **Guide**: key results, citations, draft, build and preview of the Scientist Guide. **Agents**: providers and models, execution sessions, skills, packs and hooks. **Settings**: every registry option by category or search, with a `?` explaining what it does,
why, what changes, default, current value and source, files affected and whether a human is required; typed controls; inherited/default/project/machine
badges; per-setting reset; a diff-and-reason review dialog before any write.

Help (`?`): opens on hover or keyboard focus, stays open when clicked (also touch), closes on Escape or outside click, and opens below the icon so the control
stays visible. Accessibility: tablist with roving tabindex and arrow keys, labelled controls, `role=switch`, live regions for loading and errors, focus-trapped
dialogs that restore focus, VS Code theme variables (light, dark, high contrast).

Aggregations are produced by the core (`control.py`, RPC `control`), not by the UI. **Verified:** interaction tests in jsdom (`npm run test:webview`: help behaviour,
validation, diff review, save, reset, search, tab navigation, promotion gates), type-checking and bundling of every page, and screenshots in light and dark themes
(`docs/screens/control-center-v2/`, produced by the preview harness under `packages/vscode-extension/scripts/`). **Not verified:** the Plans-page supervisor, intervention and
Telegram controls inside a running VS Code window (they are type-checked and bundled; the screenshots predate them), Remote SSH, high-contrast themes, and screen-reader output.

---

## 15. Safety guarantees

1. **Never deletes or moves user data.** Artifacts are references. `rebuild` backs up the DB before replacing it.
   Research records and artifact references are retained. Skill removal moves managed skills to backup; arbitrary user commands and hooks can modify files with the user’s permissions.
2. **Never pushes**, and commits only in opt-in or supervised cases: `checkpoint --commit` (or `git.commit_checkpoints: true`)
   commits `.research/` and nothing else and leaves other staged changes alone; the supervisor commits an agent's verified work in that task's worktree (`supervisor.commit_verified_work`, excluding `supervisor.commit_exclude`); unattended promotion (opt-in, two keys) fast-forwards local `main` and nothing else. No force, rebase or push, ever. Managed writes outside `.research/` include the `AGENTS.md` block, skill links, generated agent definitions, the configured Scientist Guide and explicitly installed MCP entries. Runs, checks and hooks can write anywhere permitted by the operating system.
2a. **Removed skills are kept** in `.research/cache/removed-skills/` (remove, `--force` re-import, update).
3. **Never silently rewrites history.**
   - Every mutation, including hand edits picked up by sync, is an event in `events.jsonl`.
   - Superseding is the only way to "replace" a finding or decision.
   - Missing mirror files are regenerated and logged.
4. **Import-before-export** protects detected hand edits (§5.4); there is no lock against concurrent external editors.
5. **Malformed records:** normal mirror sync logs parse errors and retains the indexed value; export refuses to overwrite malformed files. Rebuild fails and rolls back on malformed records or duplicate IDs. Complete malformed JSONL lines are errors, not silently skipped.
6. **Version safety:** an older tool refuses a newer schema.
6a. **Agents propose, humans approve.** Agents cannot create experiments (proposals only), cannot approve proposals, change policy or promote to `main`, and cannot approve science; those need a human and, by default, the approval PIN (a local scrypt verifier outside the project, never in the audit trail, rate-limited, failing closed with no terminal). The supervisor acts through in-process grants and never impersonates a human. This is a guard rail for honest agents, not a boundary against a hostile process running as the same OS user (GOVERNANCE.md).
7. **Atomic replacement** is used for managed text writes. The shared writer uses a unique sibling, flush and fsync before replacement. This is not an atomic transaction across multiple files or a guarantee of network-filesystem durability.

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
hpc:                          # §14C — entirely opt-in; `clusters: {}` means HPC is off
  default_cluster: null       # required when several clusters are configured
  clusters: {}                # name → profile; `research hpc config --example` prints a full one
    # duke:
    #   transport: ssh        # ssh | local (local = the existing native SLURM backend)
    #   ssh_host: plp-drcc-login2        # an alias from ~/.ssh/config; NEVER a password/key/token
    #   paths:
    #     workspace_root: /APPROVED/HPC/WORKSPACE    # you must supply approved paths; $HOME is never assumed
    #     shared_data: /APPROVED/HPC/DATA            # datasets shared between projects (may be read-only)
    #     projects: /APPROVED/HPC/WORKSPACE/projects # default: <workspace_root>/projects
    #     scratch: null                              # optional faster filesystem for job scratch
    #   transfer: {method: rsync}                    # rsync | scp (scp is the fallback)
    #   datasets: {reuse_existing: true, verify_before_reuse: true,
    #              auto_delete: false,               # fixed intent: nothing remote is ever deleted
    #              require_download_approval: true}
    #   execution: {use_slurm: true, auto_submit: false,
    #               preserve_code: true, preserve_results: true}
    #   slurm: {partition: null, account: null, time: "04:00:00", cpus: 4, mem: 16G,
    #           gpus: 1, extra_args: [], setup: ["module load python/3.11"]}
    #   limits: {max_cpus: 64, max_gpus: 8, max_time_hours: 72, max_mem_gb: 512}
    #   python: null          # remote interpreter for the runner; default python3
    #   project: {slug: pravah}    # remote directory name; default = a slug of the project name
  bundle:
    exclude: [".git", ".research", "__pycache__", "*.pyc", ".venv", "venv", "node_modules",
              ".mypy_cache", ".pytest_cache", ".DS_Store", "*.egg-info", ".ipynb_checkpoints"]
    max_file_bytes: 8388608       # a larger source file must be named in `include`
    max_total_bytes: 268435456    # datasets belong on the cluster, not in a bundle
    include: []                   # extra paths to package anyway
  collect:
    max_artifact_bytes: 33554432  # bigger outputs stay remote and are referenced
resume:
  stale_checkpoint_days: 14
agents:                       # §7.7  (profiles claude-fast / claude-balanced / claude-deep are also built in, see §7.7)
  default_profile: claude
  roles: {planner: null, implementer: null, verifier: null, analyst: null}     # null → default_profile
  profiles:
    claude: {command: claude, args: [], model: null, model_flag: --model, prompt_flag: null}
    codex:  {command: codex,  args: [], model: null, model_flag: -m,      prompt_flag: null}
    gemini: {command: gemini, args: [], model: null, model_flag: -m,      prompt_flag: -i}
verification:                 # §7.5
  require_review_for_supported: true
  independent_reviewer: true
  require_evidence_for_supported: true
  checks_gate_task_completion: true
  check_timeout_seconds: 600
coordination:
  stale_task_hours: 4         # §7.6; 0 disables
context:
  max_chars: 12000            # current.md budget (§9.3)
skills:
  link: [claude, agents]      # .claude/skills and .agents/skills; [] disables (§7.9)
git:
  commit_checkpoints: false   # true → every checkpoint commits .research/ (§9.2)
guide: {enabled: true, dir: docs/scientist-guide, profile: null, model: claude-opus-5-5, auto_log: true, highlights: supported,
         draft: {trigger: result, max_words: 150, max_sections: 3, max_figures: 1}, publish: {workflow: true}}
orchestration:                # opt-in via `research setup agents --apply`
  enabled: false
  tiers: {opus: {model: claude-opus-5-5, fallback: [opus]}, sonnet: {...}, haiku: {...}}
  available: []               # [] = assume configured models exist
  limits: {haiku_attempts: 3, sonnet_attempts: 2, escalations: 2, max_turns: null, tuning_evaluations: 50}
  active_strategy: null       # null = the tiers above (tier mode); else sonnet_first | opus_first | haiku_max | sonnet_heavy | custom
  strategies:                 # built-ins shown by `research strategy show`; override one key at a time or add your own
    sonnet_first: {description: …, levels: {fast: claude-fast, balanced: claude-balanced, deep: claude-deep}, coordinator: balanced,
                   guide: deep, route: {planning: balanced, scientific: deep, verification: balanced, checked: fast, unchecked: balanced, mechanical: fast},
                   attempts: {fast: 3, balanced: 2}, escalate: {fast: balanced, balanced: human, deep: human},
                   on_scientific_assumption: deep, max_escalations: 2}
    # opus_first · haiku_max · sonnet_heavy: same keys, different routes/limits (README "Allocation strategies")
    # mine: {extends: sonnet_first, attempts: {fast: 2}}     # custom: inherit and change only what differs
harness:                      # project-owned references; independent of the strategy
  name: null
  skills: []                  # .research/skills names
  context: []                 # files to read
  instructions: null          # an instructions file
  checks: []                  # informational verification commands shown in briefs
  hooks: {}                   # name → {stage, command, timeout?, required?, enabled?, description?}  (stages: preflight | post_implementation | pre_experiment | post_experiment)
  hooks_approved: {}          # name → hash of stage+command+timeout; written only by a human (`research hook approve`)
  hooks_timeout_seconds: 300
```

Further sections, shown with their built-in defaults (a project file holds only what you change). Meaning of each is in `research config options`
and in SUPERVISOR.md (§13 to 18), GOVERNANCE.md and §14D to 14F; `harness.require_seed` is also a setting. The Telegram token and chat id are not config: they live
in `~/.config/research-os/telegram.json` (mode 0600).

```yaml
layout:
  main_dir: null
  ledger_dir: null
  experiments_dir: null
  workspace_file: null
agents:
  handoff:
    enabled: false
    fallback_order:
    - claude
    - codex
    - agy
    on_provider_unavailable: pause
    on_unknown_execution_state: pause
skills:
  packs:
  - core-research
  disabled: []
environment:
  python: null
  packages: []
supervisor:
  poll_seconds: 5
  max_parallel_agents: 1
  max_attempts_per_task: 3
  max_total_attempts: 20
  max_wall_minutes: 480
  attempt_timeout_minutes: 90
  on_failure: diagnose_and_repair
  enforce_completion: true
  auto_apply_operational: true
  max_auto_tasks: 5
  require_approval_for_scientific_changes: true
  session_resume: auto
  context_max_chars: 24000
  commit_verified_work: true
  commit_exclude:
  - outputs/
  - results/
  - patch*.yaml
  - '*.pyc'
  - __pycache__/
  - .venv/
  hpc_poll_seconds: 30
  max_hpc_resubmissions: 1
governance:
  experiments:
    agent_creation: propose_only
  tasks:
    agent_creation: bounded_auto
    allowed_auto_types:
    - bugfix
    - diagnostic
    - verification
    max_auto_per_experiment: 3
  scientific_changes:
    require_human_approval: true
  approval:
    require_pin: true
    require_rationale: true
    record_rejections: true
    confirm_human_experiment_creation: false
  experiment_overrides: {}
promotion:
  main_branch: main
  test_command: null
  test_timeout_seconds: 1800
  require_supported_finding: true
auto_promotion:
  numerical_command: null
  reviewer_profile: null
  require_distinct_reviewer: true
supervisor_assistant:
  profile: null
  model: null
  timeout_seconds: 900
intervention:
  require_approval: false
notifications:
  halt_grace_seconds: 120
  auto_debug: false
  telegram:
    enabled: false
sandbox:
  enabled: false
  default_profile: native
  approved_roots: []
  agent:
    mode: none
  assign:
    experiments: {}
    tasks: {}
  profiles:
    native:
      runtime: native
    restricted:
      runtime: auto
      network:
        mode: disabled
    gpu-research:
      runtime: auto
      gpu: true
      network:
        mode: disabled
    custom:
      runtime: auto
```

Missing keys fall back to these defaults (deep merge), so a sparse project file keeps working as defaults grow. Effective settings are layered: built-in defaults ← optional machine file (`$RESEARCH_CONFIG`, else `$XDG_CONFIG_HOME/research-os/config.yaml`; `project` identity is ignored there) ← the project's sparse `config.yaml` ← per-command flags. `research config show [--effective|--sources]`, `config check` and `config migrate [--apply]` inspect it; edits rewrite only the key's own lines (comments preserved).
`research project set` and the UI's "Set Project Goal" rewrite the file, keeping a short header comment.

---

## 17. Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `RESEARCH_ROOT` | core | project root override |
| `RESEARCH_AGENT`, `RESEARCH_AGENT_MODEL` | core | mark the actor as an agent |
| `RESEARCH_AGENT_ROLE` | core | role suffix for the agent name (`claude/verifier`); set by dispatch |
| `RESEARCH_BRIEF` | set by dispatch | project-relative path of the session's brief |
| `RESEARCH_AUTHOR_TYPE` | core | `human` / `agent` / `system` |
| `RESEARCH_CONFIG` | core | path of the machine-level config file (default `$XDG_CONFIG_HOME/research-os/config.yaml`) |
| `RESEARCH_STRATEGY`, `RESEARCH_STRATEGY_FINGERPRINT`, `RESEARCH_AGENT_LEVEL` | set by strategy dispatch | the allocation the session started under |
| `RESEARCH_HOOK`, `RESEARCH_HOOK_STAGE`, `RESEARCH_TASK`, `RESEARCH_EXPERIMENT`, `RESEARCH_RUN` | set for hook commands | which hook is running, at which stage, for which task / experiment / run |
| `RESEARCH_PYTHON` | project-local launcher | interpreter for `.research/bin/research` |
| `RESEARCH_AUTHOR` | core | human display name |
| `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` | core | auto-detect Claude Code |
| `CODEX_SANDBOX`, `CODEX_SANDBOX_NETWORK_DISABLED` | core | auto-detect Codex |
| `RESEARCH_RUN_ID`, `RESEARCH_RUN_DIR`, `RESEARCH_IN_JOB` | set by the runner | enable in-job logging (§8.6) |
| `SLURM_JOB_ID`, `SLURM_JOB_NODELIST`, `SLURM_JOB_PARTITION` | runner | recorded in `status.json` |
| `CONDA_DEFAULT_ENV`, `VIRTUAL_ENV`, `USER` | core | stored in `run.env` |
| `RESEARCH_TELEGRAM_API` | notify | base URL override for the Telegram API (proxy or test double) |
| `NO_COLOR` | CLI | disable ANSI colour |

---

## 18. Testing and verification

| Suite | Command | What it proves |
|---|---|---|
| Python suite (pytest, 1104 tests collected at 2.6.0 (`python -m pytest -q --collect-only`); three tests fail on an unmodified 2.5.0 as well) | `python -m pytest -q` | all offline: no SSH, no real Slurm, no model, no network. Per file: |
| ↳ `test_core.py` (29) · `test_slurm_rpc_cli.py` (11) | | init and AGENTS.md preservation; migrations; rollback journal; ids; hierarchy; experiments and git capture; runs, artifacts, findings, decisions; authorship; run budget; local foreground/detached runs; hand-edit import, rebuild, relocation; checkpoints; SLURM script/submit/completion/TIMEOUT/cancel against mocked `sbatch`/`squeue`/`sacct`; RPC round trip; CLI end to end and exit codes |
| ↳ `test_plans_tasks.py` (32) · `test_plans_tasks_interfaces.py` (19) · `test_research_os.py` (22) | | plans, tasks, readiness, claims, notes, stale tasks; exact form payloads over RPC; every command in the AGENTS.md template parses; checks gate, finding gate and role-based independence; skills import/update/remove; search; compare; sweeps; reports; checkpoint `--commit`; dispatch; MCP protocol and install |
| ↳ `test_evidence.py` (16) · `test_guide.py` (7) · `test_orchestrate.py` (11) | | numerical bindings, mismatch blocking `supported`, artifact integrity, provenance, sources; Scientist Guide freshness and AGENTS block; tier-mode routing, frozen tests, attempts, delegation, tuning |
| ↳ `test_strategy.py` (19) | | the four strategies and custom ones: resolution, layering and precedence, cycles, project isolation, one resolver for direct/orchestrated/delegated/guide dispatch, frozen tests and independence under strategies, harness independence, managed-file boundaries, the `agent_setup` RPC, list-block config edits |
| ↳ `test_agent_features.py` (15) | | hooks (human-only approval, edit revokes, timeout, advisory, preflight/pre-experiment gates), contracts and `orchestrate verify`, usage parsing and summaries (missing ≠ zero), headless runs with a mock agent, task pins, strategy-aware agent files, `experiment check`, CLI and RPC |
| ↳ `test_autopromote.py` (14) · `test_supervisor_fixes.py` (13) · `test_intervention.py` (26) | | fast-forward-only unattended promotion and its fail-closed cases; failure classification, plan health, reload/restart; intervention state machine, debug agent, Telegram against a local fake server |
| ↳ `test_cli_pin_link_escalation.py` (12) · `test_cli_e2e.py` (8) | | the real launcher driven through a pseudo-terminal: PIN prompt, `./research` link, escalation; end-to-end CLI flows |
| ↳ `test_config.py` (74) · `test_localbin.py` (8) | | layered sparse configuration, surgical comment-preserving edits, check/migrate; the project-local launcher |
| ↳ `test_fullreport.py` (37) | | managed AGENTS.md block; deterministic, side-effect-free, coverage-verified full export |
| ↳ `test_hpc.py` (89) · `test_acceptance.py` (2) | | bundles, SSH/Slurm against a fake cluster, datasets, result import, reconciliation; the 17-step remote run → import → export acceptance scenario |
| TypeScript | `npm run typecheck` | the extension compiles under `strict` |
| Clean install | `bash scripts/clean_install_test.sh` | fresh venv + wheel → full protocol (question, experiment, 2 runs, artifact, synthesis, finding, decision, checkpoint) → copy to a new path, delete the index → resume reconstructs exact counts → artifact paths relative and resolving → no build-machine paths in the project, installed package or VSIX |
| Webview and client tests | `npm run test:webview` · `npm run test:client` | the React Control Center pages and the extension client |
| Real VS Code harness | `RESEARCH_TEST_WORKSPACE=<demo> npm run test:ext` | downloads VS Code, installs the VSIX, runs the checks in `test/harness/suite/index.js` in the extension host (state reconstruction, the project-local CLI, every panel command, every view, shim, watcher, authorship, the plan/task flow through the real form handlers: New Plan, Add Task, Start, Change Status, Edit task/plan, plan activity, Research Home, Plans tree data, `current.md`; and the Research OS layer: task form with checks + skills, Verify command, agent brief, awaiting-review attention item, Review form, compare panel, skill import + `.claude/skills` link, search, report, dispatch). Needs access to `update.code.visualstudio.com`. When launched from a terminal inside VS Code, run it with a clean environment (`env -i HOME=$HOME PATH=… npm run test:ext`), otherwise the inherited `VSCODE_*` variables hijack the test instance. Last run (73/73 on VS Code 1.141.0) predates strategies, hooks and the Agent setup page; those are covered by the Python suite and the typecheck, and the page was reviewed as screenshots rendered from the real `render/setup.ts`, not inside VS Code. |
| Real MCP client | `claude -p "…" --mcp-config .mcp.json --strict-mcp-config --allowedTools mcp__research__research_current` | Claude Code connects to `research mcp` and calls a tool (checked manually on 2026-10-07; not part of CI) |
| Visual review | `packages/vscode-extension/scripts/preview-*` + themes | renders every webview outside VS Code with dark and light theme variables for screenshots (`docs/screens/`) |

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
3. Runs `vsce package --no-dependencies` → `dist/research-panel-X.Y.Z.vsix`.
4. Runs `python -m build` → wheel + sdist in `dist/`. If `build` is missing, it falls back to `pip wheel` / `uv build`; in an environment with neither setuptools nor `build` the sdist step fails after the VSIX and wheel have been written.

**Versioning:** bump `version` in `pyproject.toml`, root `package.json`, `packages/vscode-extension/package.json` (and the two version lines at the top of its `package-lock.json`) and `research/__init__.py` together, rename the CHANGELOG heading, rebuild and reinstall the VSIX (the extension refreshes each project's `.research/bin/research` on activation). Schema changes add a new entry to `schema.MIGRATIONS` and increment `SCHEMA_VERSION`. Never edit existing entries.

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
├── CHANGELOG.md
├── docs/
│   ├── SPEC.md                     # this document (features + design + spec)
│   ├── SUPERVISOR.md               # plans, supervisor, promotion, intervention, Telegram
│   ├── GOVERNANCE.md               # proposals, approval PIN, who may do what
│   ├── CONFIGURATION.md            # layers, files, precedence, editing
│   └── screens/                    # UI screenshots
├── packages/
│   ├── core/research/
│   │   ├── __init__.py             # public Python API, __version__
│   │   ├── schema.py  store.py  mirrors.py  durability.py  # entity specs and DDL; Project (config, index, sync, rebuild, events); Markdown mirrors; crash journal
│   │   ├── services.py  runs.py  views.py  context.py  # questions/experiments/findings/decisions; runs, metrics, artifacts; detail views; resume, Home, current.md
│   │   ├── plans.py  planspec.py   # plans, tasks, readiness, claims; YAML plan schema, validation, diff, apply
│   │   ├── supervisor.py  supervisord.py  # deterministic plan supervisor (state machine); the daemon process
│   │   ├── autopromote.py  promotion.py  # unattended fast-forward promotion; promotion candidates and gates
│   │   ├── intervene.py  notify.py  # supervisor intervention; debug agent, Telegram
│   │   ├── governance.py  approval.py  # who may create what; approval PIN
│   │   ├── verification.py  task_spec.py  taskprompt.py  # task checks, finding reviews, gates; effective task spec; per-attempt prompt
│   │   ├── agents.py  providers.py  execution.py  processes.py  # roles, profiles, dispatch; provider adapters; execution records; process control
│   │   ├── strategy.py  orchestrate.py  harness.py  hooks.py  checks.py  # allocation strategies; routing, frozen tests, contracts; hook catalog and checks
│   │   ├── skills.py  packs.py  setup_agents.py  # skill import/update, packs; `setup agents`
│   │   ├── guide.py  guide_quarto.py  guide_review.py  evidence.py  provenance.py  # Scientist Guide; numerical evidence
│   │   ├── workspace.py  sandbox.py  isolation.py  # shared ledger and experiment worktrees; job isolation
│   │   ├── initproject.py  bootstrap.py  setup.py  envcheck.py  doctor.py  # Initialize Project, YAML init, environment setup, doctor
│   │   ├── configfile.py  registry.py  # layered sparse configuration; settings registry
│   │   ├── agentsmd.py  fullreport.py  localbin.py  report.py  search.py  compare.py  usage.py  expcheck.py
│   │   ├── mcp.py  rpc.py          # MCP server and install; JSON-RPC server
│   │   ├── runner.py  control.py  gitinfo.py  yamlio.py  util.py
│   │   ├── backends/ (base, local, slurm)
│   │   ├── hpc/ (config, transport, remote, bundle, datasets, results)
│   │   ├── templates/ (AGENTS.block.md, catalog/, catalog.yaml, packs.yaml, skills/, claude-agents/, prompts/, templates/)
│   │   └── _vendor/yaml/           # PyYAML, MIT, fallback only
│   ├── cli/research_cli/           # argparse CLI (main.py, supervisor_cmds.py, __main__.py)
│   └── vscode-extension/
│       ├── package.json            # manifest (views, commands, menus, settings)
│       ├── src/                    # extension.ts, client.ts, model.ts, trees.ts, panels.ts, commands.ts, shim.ts, util.ts
│       │   ├── render/             # ui.ts, home.ts (Research Home), resume.ts (Overview + fallback Resume), detail.ts, form.ts, setup.ts
│       │   └── webview/            # React Control Center: App, pages (Home, Research, Work), PlansPage, SettingsPage, GuidePage, Providers, Setup, InitProject, Doctor, ...
│       ├── media/                  # ui.css, ui.js, research.svg, icon.png (codicons copied at build)
│       ├── scripts/                # bundle-python.mjs, theme-*.css, preview harness
│       └── test/                   # client/webview tests, real-VS-Code harness (run.mjs, harness/)
├── examples/                       # alpha-sweep, auto-promotion-demo, custom-provider
├── tests/python/                   # pytest suites
├── scripts/                        # package.sh, clean_install_test.sh, make_preview_fixture.py
└── dist/                           # built VSIX, wheel, sdist
```

---

## 21. Design rationale

> The test the design is held to: after a month away, can you open the panel and understand in under two minutes what
> was tried, what was learned, what failed, and where to continue?

### 21.1 Python core + TypeScript extension (bundled core)

**Chosen:** one Python core (stdlib only, Python ≥ 3.8) that owns *all* business logic, a thin Python CLI on top of it,
and a TypeScript extension that talks to the same core over a long-lived stdio JSON-RPC process.

| | Python core + TS extension (chosen) | TS-only |
|---|---|---|
| Agents and user scripts calling `research.log_metric()` from Python | native | needs a second implementation, or a subprocess |
| One source of business logic | yes (the extension is UI only) | yes, but the Python API becomes a wrapper around a Node CLI |
| Runs on HPC login and compute nodes | `python3` is always there | Node is often missing on compute nodes |
| Extension complexity | +1 process boundary | simpler |
| SQLite | stdlib `sqlite3` | native module (`better-sqlite3`), with ABI and platform problems inside a VSIX used over Remote SSH |

The process boundary is kept cheap by bundling the core inside the VSIX (`PYTHONPATH=<ext>/python`, nothing to
pip-install), having zero third-party dependencies (PyYAML vendored as a fallback), and using one persistent RPC process
per window, so Python start-up is paid once.

### 21.2 Text is canonical, SQLite is an index

SQLite binaries don't merge in git, and Markdown does. Making the text files canonical gives total reconstructability
(`rebuild`), human-editable records, readable diffs and inspectable branch merges (counter-ID conflicts require manual resolution). The index exists only for fast queries and
relationships. On HPC filesystems the DB uses `journal_mode=DELETE` (WAL needs shared memory, which is unsafe on NFS
and Lustre), and compute nodes write only to per-run files that the login node ingests later.

### 21.3 Understanding over activity

Runs are deliberately subordinate. They are collapsed under experiments in every view. Research Home leads with focus,
plan, findings and attention items, not run tables. Syntheses are required (and budget-enforced for agents) before
more runs, and findings are never auto-generated from syntheses. A human or agent has to decide what is reusable.

### 21.4 Bounded agents

Agents are good at running jobs and bad at stopping. The run budget (§7.2), the synthesis-before-new-experiment rule
and authorship tracking give a human reviewer a bounded, attributable trail. The budget is a hard stop for agents, a
warning for humans, and always overridable with a logged reason.

### 21.5 Plans and tasks as a separate layer

Experiments answer "what did we test and what did it show?". Plans and tasks answer "what work is being done, by whom,
in what order, and how do we know it is finished?". Keeping them separate stops operational steps ("implement the data
loader") from polluting the scientific record, while `related_experiment_id` / `related_question_id` keep the two layers
connected. *Ready* is derived rather than stored, so it can never go stale when a dependency changes.

### 21.6 Gates instead of trust, dispatch instead of autonomy

- **Verification is a gate, not a suggestion.** Agents produce plausible claims quickly; a finding only becomes
  `supported` after someone else looked at the evidence. The gate is enforced like the run budget (exit code 3 with a
  hint), so an agent gets a clear, machine-readable "no" rather than a policy it can ignore.
- **Roles make one subscription usable as two reviewers.** Identity is `name/role`, so `claude/implementer` and
  `claude/verifier` are different reviewers. This is trust-based (an agent could fake its role), which is acceptable for
  a single researcher's project; humans remain the final authority and are never blocked.
- **Dispatch first, supervision on top.** Dispatch prepares a bounded brief and starts the agent CLI you already use,
  in a terminal you can watch. Swapping providers when one runs out of credit is a config change. The supervisor (§14G) adds unattended execution of a *reviewed plan*:
  it is deterministic code (no LLM), agents never launch agents, attempts and wall time are bounded, every outcome is verified, it stops and asks for scientific, resource and
  frozen-test changes, and it halts honestly instead of looping. Autonomous science (agents approving findings, creating experiments or promoting code unreviewed) is deliberately left out.
- **MCP in the standard library.** The protocol is small enough (newline-delimited JSON-RPC) that an SDK would add a
  dependency for little gain, and the core must stay installable on HPC nodes.
- **Skills are copied, not referenced.** An imported skill lives in `.research/skills/` and is committed with the
  project, so a clone has exactly the skills its history was produced with; provenance (`source`, `ref`) makes `update`
  possible.

### 21.7 Deliberate simplifications

- **No Project table:** project metadata lives in `config.yaml`, where it is human-editable.
- **Syntheses are records, not mirrored entities.** They render inside the experiment file.
- **Notes are pure files**, with pin and link state in their own front-matter.
- **Bounded sweeps:** the CLI expands a Cartesian parameter grid and creates one run per point; this is not a general scheduling engine.
- **No diff capture beyond `git.diff`** for dirty runs (capped at 2 MiB).
- **Preview adapters** are a small TypeScript registry, and unknown binaries show metadata.
- **No unattended scheduler:** routing, dispatch preparation, explicit execution, verification and escalation exist; automatic plan progression does not.

### 21.8 Requirements not in the original brief, and how they are handled

1. **SQLite on NFS/Lustre:** DELETE journal, busy timeout, compute nodes never write the DB.
2. **Concurrent compute-node writes:** per-run files, ingested later.
3. **Git merges and ID collisions across branches:** the DB is git-ignored and IDs are counters. Duplicate IDs stop sync/rebuild with a diagnostic; resolve IDs and references before retrying.
4. **Runs that outlive VS Code:** detached processes; status reconciled from files, not from a live process handle.
5. **Staleness:** the resume data reports what changed since the last checkpoint and flags old checkpoints.
6. **Baseline:** an explicit `is_baseline` flag on one experiment, plus the checkpoint's baseline text.
7. **Audit trail:** every mutation, including hand edits picked up by sync, is an event.

---

## 22. Limitations

### 22.1 Limitations by design

#### Scientific provenance, Scientist Guide and orchestration layers (added after the items below)

- **Evidence verification checks numbers, not science.** Verified bindings prove a number matches a recorded metric (or a whitelisted calculation over metrics). They do not show the metric is the right one, the experiment is valid, or the interpretation is correct. Free-form prose is not parsed; only explicitly bound numbers are checked (`evidence suggest` lists unbound numbers in legacy findings but cannot bind them for you).
- **Claims are compared after rounding to the stated decimals (or `--rel-tol`, capped at 5%).** `12.8` matches a recorded 12.84. Use `--rel-tol` for exact comparison. An integer claim such as `12` matches anything within ±0.5.
- **Only runs/metrics recorded in Research OS are verifiable.** A number computed outside a registered run, or in an artifact whose contents are not parsed, can only be cross-checked by its artifact hash (not by reading the file's numbers).
- **Artifact integrity needs a stored hash.** Files larger than `artifacts.hash_max_bytes` have no registration hash: they report `unhashed`/`possibly_modified` (size only) until pinned with `research evidence pin`, which hashes from that moment, not from the original creation. Directories are not content-hashed. External or unmounted storage reports `unavailable`.
- **Run provenance is only as good as what was recorded.** Lockfile hashes are recorded for runs created after this change; older runs say so. Seeds and split identities are read from run `parameters` (keys containing seed/split/partition/fold/dataset). Missing metadata is reported as a limitation, never inferred. A clean git commit does not prove reproducibility.
- **Leakage protection is structural, not semantic.** Held-out test values are refused when the experiment has a `research tuning` plan with no frozen selection or recorded test. Without that structured metadata, the check can only warn. It cannot detect leakage you did not declare.
- **The numerical gate also applies to humans.** Unlike other review gates, `supported` is refused for anyone when a bound number fails. Correct by superseding the binding or finding; a history of earlier bindings (including loose ones created before the tolerance cap) is preserved.
- **Literature sources are offline records.** `research source` validates identifier format only. It never resolves a DOI or fetches a paper; identity, examination and per-finding support are three separate human/agent assertions.
- **Scientist Guide prose is not machine-verified.** The guide status shows whether each finding's numbers verify, but nothing checks that the narrative quotes them correctly. Draft limits are checked after the fact by comparing files, not enforced. Review it like any scientific text. Writing depends on an agent actually being run; `guide draft` only prepares it.
- **Orchestration is a scheduler only under the supervisor.** Without a running plan it is launched by people or sessions. Native Claude Code subagents cannot spawn subagents, so Sonnet→Haiku runs as a separate `claude` process (`research orchestrate dispatch … --run`). Plain `research dispatch` does not route by tier unless an allocation strategy is active, in which case it uses the same resolver as `orchestrate`. The shipped model IDs are configuration defaults, not a current provider-availability guarantee. This review did not exercise a live provider or generated subagent model frontmatter.
- **Usage is captured only for headless runs** (`--headless`) and manual records; interactive sessions expose none. Hooks execute shell commands only after human approval (see README, Hooks and task contracts).
- **No MCP tools yet for the guide, strategies, orchestration, hooks, usage, tuning, evidence or sources** (CLI and RPC only), and no VS Code UI for tuning, evidence or sources. Strategies, hooks, task pins, usage, experiment validation, skills and MCP registration have the Agent setup page (§13.5); contracts are shown there but are edited with `research orchestrate contract`; strategy definitions, models, limits and hook commands are edited in `config.yaml` / `research hook add`.
- **Usage is recorded only when it is exposed.** `--headless` runs record what the provider's JSON reports (parsed defensively; the `claude -p --output-format json` shape was taken from documentation and not exercised against a live session); `usage import` and `orchestrate attempt --usage` take what you supply. Interactive sessions expose nothing, so none is recorded for them. Cost is provider-reported (no price table), so it can be partial; summaries show `reported/records`. Headless Claude cannot answer permission prompts; put the flags you trust in the profile's `headless_args`.
- **Hooks execute shell commands.** They run only after a human approves the exact command (hash of stage + command + timeout; agents get exit 3), with a timeout and the project root as working directory, but they are not sandboxed: approve only what you would run yourself. `post_experiment` hooks run in `research experiment check`, not automatically when a run finishes, and there is no hook stage for HPC bundle import.
- **Per-task pins need an active strategy and apply to implementer dispatch only** (verifier dispatch keeps its independence rule). The file boundary in `orchestrate verify` compares `git status` against the baseline taken at dispatch, so it only sees a git repository and cannot attribute changes made by other processes in between.
- **Generated agent files follow the strategy only when you re-run `research setup agents --apply`;** a strategy change marks them `update`. Managed tier-mode files are moved to `.research/cache/removed-agents/`, user-owned files are never touched. The subagent `model:` frontmatter was not exercised in a live session.
- **Ponytail import was not run against the live repository.** `research setup agents --approve ponytail` uses the existing skill importer, which is tested with a local fixture.

#### Supervisor, promotion and notifications (2.1 to 2.6)

- **The supervisor is not a security boundary.** It and the approval PIN guard against honest mistakes and unsupervised agents; a hostile process running as the same OS user can read project files (GOVERNANCE.md).
- **Unattended promotion is local only:** fast-forward into local `main`; it never pushes, rebases or forces, and pauses on any conflict, dirty or moved `main` or edited test.
- **Analyst and verifier tasks run in the main checkout;** only implementer tasks get a worktree.
- **Frozen-test changes are approval-gated and recorded, never edited or re-frozen automatically.**
- **Not exercised against live services in the test suite:** real Claude/Codex planning and debug agents and the real Telegram API (scripted agents and a local fake server are used). The Plans page controls are type-checked and bundled; the screenshots predate them.
- **Python 3.14:** `getpass` cannot read the PIN from `/dev/tty`; the CLI reads the terminal directly with echo off. With no terminal (CI, agent shells) protected actions fail closed.

#### Core

- **ID collisions across git branches (classic mode).** Counters are local to each `.research/`. Two branches can create the same ID; shared-ledger mode (§14D) removes this. Sync/rebuild now refuses duplicates. Resolve each front-matter ID and its references; renaming a file alone is insufficient. A future option is branch-safe ids (e.g. a short hash suffix).
- **Hand edits to `run.json` and the JSONL files are not re-imported** (they are append-only records). Edit runs through
  `research run update`. A hand-edited `run.json` is only read when the run directory is unknown to the index
  (fresh clone or rebuild).
- **Windows:** the core and CLI work. The terminal shim and SLURM backend are POSIX-only.
- **SLURM verified against mocked `sbatch`/`squeue`/`sacct`** in tests. Site-specific accounting quirks (e.g. sacct
  disabled) degrade to the `unknown` state, never to a crash.
- **Preview formats:** NIfTI, HDF5, NetCDF and NPZ show metadata only.
- **Real-VS-Code harness:** not runnable in environments that block the VS Code download. The code-server E2E covers the
  same runtime.
- **Plans and tasks are scheduled only by the supervisor** (§14G): updates reject dependency cycles; outside a supervised plan there is no automatic
  completion or move to `verify`, and starting a non-ready task only warns.
- **Plans and tasks are not part of checkpoints** (draft or stored fields).
- **The finding gate trusts identities and files.** Roles are self-declared environment variables, and a hand edit of a
  finding's Markdown is imported as a human edit, so a determined agent could bypass the gate. It is a guard rail for
  honest agents, not a security boundary.
- **MCP registrations written without `research` on PATH point at `.research/bin/research` by absolute path.** If the
  project moves, re-run `research mcp install`.
- **Skill updates keep a backup copy each time** in `.research/cache/removed-skills/` (ignored by git); prune it by hand.
- **Checks run arbitrary commands** from the task definition in the project root, like `run exec`; only add checks you'd run yourself.

---

## 23. Roadmap

Built from the earlier list: tiered context, verification gates, roles/profiles with dispatch, the MCP server, project skills,
run comparison, sweeps, reports, git snapshots, the Scientist Guide, numerical evidence, HPC, layered configuration,
allocation strategies, hooks, task contracts, usage accounting, per-task pins, strategy-aware agent files, `experiment check`, the persistent supervisor, governance with the approval PIN, unattended fast-forward promotion, supervisor intervention, the debug agent and Telegram.
Remaining ideas, only once real use shows a need:

- **Model-driven verification inside the gate**, e.g. automatically dispatching a verifier when a finding is created
- **MCP tools for strategies, hooks, contracts and experiment check** (so agents need not shell out)
- **Running `experiment check` automatically** when a run finishes, and hook stages for HPC bundle import
- **Live verification of headless usage capture** against a real `claude -p` session, and further `usage_parser`s for other CLIs
- **Fully autonomous science** (deliberately not built; see §21.6). The supervisor automates execution, never scientific approval
- **Data-format viewers** (NIfTI slices, HDF5 trees, NPZ keys) as a *separate* extension rather than in this one
- per-question timelines
- branch-safe ids
