# The Research OS supervisor

You write (or have an AI write) a plan in YAML, import it, press **Run plan**, and Research OS starts agents task by task,
checks their work itself, repairs failures within limits, watches HPC jobs, and stops for you when it must. It never needs an LLM to
decide what to run next.

If you have never used Research OS, read [QUICKSTART.md](QUICKSTART.md) first. **Set up your Python and cluster environments once: [SETUP.md](SETUP.md).**

## 1. Who does what

| Research OS (deterministic code) | Agents (Claude Code, Codex, …) |
|---|---|
| Owns plans, task order, ownership, attempts, budgets | Reason, write code, debug, analyse |
| Starts agents in the right worktree with assembled context | Report progress through `research agent …` |
| Verifies results (checks, files, runs) and is the **only** thing that can mark a supervised task done | Cannot mark their own work verified |
| Waits for HPC jobs, collects results, never resubmits on its own | May submit jobs through Research OS HPC integration |
| Retries within limits; stops for scientific or environment problems | Propose plan changes; cannot apply scientific ones |

## 2. Quick start (CLI)

```bash
research plan yaml template -o plans/research.yaml     # or ask a planning AI to write one in this schema
research plan yaml validate plans/research.yaml
research plan yaml diff plans/research.yaml            # preview, nothing changes
research plan yaml apply plans/research.yaml
research plan run PLAN-001                             # starts the supervisor process if needed
research plan status PLAN-001                          # phases, attempts, budgets
research supervisor status
research plan pause PLAN-001 / resume / stop
```

Starting, pausing, approving and accepting are **human decisions**: agents are refused. (Identity is the `--as`/environment declaration;
see Security.) In VS Code use **Plans** in the Control Center, or the commands *Import Plan YAML…*, *Open Plan as YAML*,
*Apply Plan YAML from Editor…*, *Run Plan (Supervisor)…*.

## 3. YAML schema (version 1)

```yaml
schema_version: 1                # required; newer versions are rejected, never guessed at
project:
  context: [RESEARCH.md, AGENTS.md]   # files drawn into every prompt (selected and size-limited)
plans:
  - key: diffusion               # stable, yours; maps to PLAN-NNN in the ledger
    id: PLAN-001                 # optional: adopt an existing plan
    title: …
    objective: …
    success_criteria: …
    scope: partial               # partial (default): never deletes. full: unstarted tasks missing from the file are retired
    revision: 3                  # written by export; applying a file based on an older revision is a conflict
    execution:
      mode: bounded_autonomous
      max_parallel_agents: 1
      max_attempts_per_task: 3
      max_total_attempts: 20
      max_wall_minutes: 480
      max_hpc_resubmissions: 1
      on_failure: diagnose_and_repair      # or pause, stop
      require_approval_for_scientific_changes: true
    experiments:
      - key: sweep
        id: EXP-001              # optional: adopt an existing experiment
        title: …
        hypothesis: …            # also: method, success_criteria, parameters, metrics, expected_artifacts, planned_runs, question
        tasks:
          - key: implement       # unique within the plan; dependencies refer to keys (or existing T- ids)
            title: …
            prompt: |            # the agent's task text (required)
              …
            depends_on: [other-key]
            agent: {role: implementer, profile: claude-balanced, model: null, session_group: main, level: null}
            checks: ["cmd:pytest -q", "file:out.txt", "metric:EXP-001:rmse<=0.05"]
            expected_artifacts: [results/figure.png]   # relative paths; become file: checks
            acceptance: …
            inputs: …
            allowed_files: [src/**]                    # advisory unless a sandbox profile enforces it
            resources: {backend: slurm, cpus: 1, mem: 1G, time: "00:05:00", partition: defq}
            execution: {max_attempts: 3, requires_approval: false, workspace: experiment, timeout_minutes: 90}
    tasks: []                    # plan-level tasks (no experiment)
```

* Unknown fields are errors (keys beginning `x_` are kept and ignored). Every validation problem is listed at once.
* The YAML is *desired work*. Runtime history (attempts, runs, results, status) lives in the ledger and is never rewritten by apply.

### Editing safely

`export` writes the plan with its current revision; edit and `apply`:

| You do | Result |
|---|---|
| Change an unstarted task | Updated in place |
| Change a **running** task | Queued: the active attempt keeps its snapshot; the next attempt uses the new text |
| Change a **done** task | Refused (add a new task instead) |
| Remove an unstarted task from a `scope: full` file | Retired (kept in the ledger, ignored by the supervisor) |
| Remove a done or running task | Refused |
| Apply a file based on an older revision | Conflict; re-export, merge, apply again |
| Apply a partial export (`--experiment`, `--task`) | Only what it names; nothing missing is deleted |

Apply saves its key map after every entity, so an interrupted apply is finished by running it again.

## 4. How a task moves (state machine)

Task status keeps its Research OS v2 values (`todo`, `running`, `verify`, `done`, `blocked`). The supervisor adds a **phase** per task:

```
waiting ─► ready ─► (awaiting_approval) ─► running ─► verifying ─► verified
                                              │           │
                                              │           ├─► waiting_hpc (computational runs still queued/running) ─► verifying
                                              │           └─► repair_pending ─► running (repair attempt)  … up to the attempt limit ─► blocked
                                              └─► attention (cancelled, unknown process state, provider refused, scientific assumption)
```

Each **attempt** reuses the execution record lifecycle `prepared → launched → running → completed | failed | cancelled | unknown`.
`completed` only means the agent process exited 0; the task is verified only when Research OS's checks pass.

Readiness is derived (all dependencies done, not retired, policy allows). One task has at most one active attempt; one worktree has at
most one active coding agent; `max_parallel_agents` caps the rest.

## 5. Verification

After an attempt exits, the supervisor runs, in the task's workspace: frozen-test integrity, approved post-implementation hooks, the task's
`checks` (commands, files, metrics) and any `expected_artifacts`. If the task produced Slurm runs, it waits for them (reconciling status
and collecting results) before verifying. No verifier means "unverified", never "passed". A scientifically negative result is not an
engineering failure: checks decide, not the sign of the result.

## 6. Repair and limits

| Failure | What happens |
|---|---|
| Checks fail (incorrect implementation, ambiguous spec, weak oracle) | Repair attempt with the diagnostics, up to `max_attempts_per_task` and `max_total_attempts` |
| Environment (missing module, permissions) or resource limit | Task blocked for a human: not repairable by retrying |
| Agent process crashed | Counts as an attempt; repaired within limits |
| Provider refusal (usage limit, overload, 429) | Task goes to *attention*; the attempt is kept as evidence but **not counted**; nothing is retried automatically |
| Unknown process state | *Attention*; never retried automatically; you settle it (`supervisor resolve`, UI **Retry**) |
| Scientific assumption reported by the agent | *Attention* and a proposal; nothing continues |

Repairs never edit acceptance tests, raise budgets, change hypotheses or resubmit HPC jobs on their own. Wall-time exhaustion pauses the plan.
You can void attempts that were not the agent's fault (UI: Details → *Void this attempt*, or **Retry fresh**).

## 7. Sessions and context

* Each attempt gets a prompt assembled from: project context files, plan/experiment objectives, the task prompt, acceptance and checks,
  results of finished dependencies, earlier attempts and their diagnostics, progress notes, workspace, resources and reporting
  instructions. Sections are capped (`supervisor.context_max_chars`) and tagged with their source. The exact text and a hash are stored
  per attempt (`.research/supervisor/prompts/`) and shown in the UI. Credentials are redacted.
* **Native resumption**: a repair continues the provider's own session only if the installed CLI proves it (`claude --help` lists
  `--resume`; the session id is read from the agent's JSON output) and the role and profile match. **Codex** works the same way: the
  `thread_id` from `codex exec --json` is stored and a repair runs `codex exec resume --json … <id> <prompt>` (verified against
  codex-cli 0.163; resume takes no `-s/-C/--add-dir`, so the sandbox and writable roots are passed with `-c`). **Context hand-off**
  (a fresh agent with the persisted history) is used when native resume is unavailable, and always works. Verifier tasks never resume an
  implementer's session. Codex's workspace-write sandbox has no network, so a Codex task cannot itself run `research hpc submit`.
* `session_group` marks tasks meant to share a session; the supervisor only continues a session for repairs of the same task.

## 8. HPC

Agents submit through Research OS (`research hpc submit …`, on PATH, inside the experiment worktree). The supervisor links runs to the
attempt by reported run ids and by experiment + time window, waits for them (polling every `supervisor.hpc_poll_seconds`), retrieves
logs and outputs, and verifies. An agent that merely submits jobs does not complete the task. Unavailable status is never treated as
failure and the supervisor itself never submits. **Result contract** (also in every implementer brief): write outputs under
`$RESEARCH_RUN_DIR/outputs/`, metrics to `$RESEARCH_RUN_DIR/metrics.jsonl`, and register each file in `$RESEARCH_RUN_DIR/artifacts.jsonl`,
otherwise they stay on the cluster. Job environment (modules, venv) goes in `hpc.clusters.<name>.slurm.setup`.

### Parallel agents and promotion

`max_parallel_agents` (plan or project; the cap applies per plan, so several plans can each use their own cap) lets agents of *different* experiments work at the same time, including different providers;
tasks of one experiment never share a worktree concurrently. When a task verifies, its changes are committed on the experiment branch
(never `main`, never pushed; `supervisor.commit_verified_work`; `commit_exclude` keeps results, `patch*.yaml`, caches and venvs out). **Plans → Prepare promotion from verified work**
(or `research plan promote PLAN-001`) turns those commits into promotion candidates *prepared by the supervisor*, so you can review them
yourself; tests, review, approval and the explicit merge confirmation then follow the existing Work-tab flow.

## 9. Plan changes proposed by agents

`research agent propose T-001 --file patch.yaml --rationale …` (or MCP `agent_propose`) stores a patch in the same schema. It is
**operational** if it only adds new tasks (applied automatically up to `supervisor.max_auto_tasks`, if enabled); anything that edits
existing definitions, hypotheses, checks, parameters or experiments is **scientific** and waits for you in Plans → *Proposed plan changes*.
Approved changes go through the same `apply` engine, so revisions and history are preserved.

Since 2.4 an operational proposal auto-applies only if `governance.tasks` allows it (type allow-list, per-experiment cap), `supervisor.auto_apply_operational` is on and `max_auto_tasks` holds; approving anything else needs your reason and PIN. Agents can also propose experiments. See [GOVERNANCE.md](GOVERNANCE.md).

## 10. Recovery and troubleshooting

* State is files in `.research/supervisor/`; the SQLite index is rebuildable. Closing VS Code does not stop the supervisor process.
* After a crash or reboot: `research supervisor start` reconciles. A launch that never started is re-fired (idempotent); a running
  agent is left alone; unknown states go to *attention*.
* A stale heartbeat proves nothing; Research OS checks the process.
* `research plan status`, the Plans page, and `.research/supervisor/daemon.log` explain why a task is waiting or blocked.
* Do not edit the plan runtime state by hand; use `supervisor resolve|approve|accept|void` or the UI.

## 11. Security and limits (read this)

* **Enforced**: agents cannot complete supervised tasks by CLI, MCP, RPC or hand-edited task files; agent reports are accepted only from
  the execution that owns the active attempt; policy settings are not agent-editable; headless Claude is limited to `research` commands
  and file edits (no arbitrary shell).
* **Advisory only**: `allowed_files`, "do not edit tests", "do not push" are instructions in the prompt. Without a sandbox profile an
  agent that can run Python can do anything the OS user can. Use `sandbox` profiles for real isolation.
* **Identity is self-declared** (`--as`, environment). It stops accidents and cooperative agents, not an adversarial process. Do not
  rely on human-only gates against hostile code.
* No secrets belong in YAML. Prompts and reports are redacted for common credential patterns, which is best effort.
* Nothing is merged, pushed or deleted automatically. Promotion to `main` stays in the existing review workflow.

## 12. Compatibility

Manual Research OS workflows are unchanged when no plan is running. Supervision applies only while a plan run is `running`, `paused`,
`blocked` or `attention`; `supervisor.enforce_completion: false` turns the guards off. New settings are under **Settings → Supervisor**.

## 13. Settings

`supervisor.poll_seconds`, `max_parallel_agents`, `max_attempts_per_task`, `max_total_attempts`, `max_wall_minutes`,
`attempt_timeout_minutes`, `on_failure`, `enforce_completion`, `auto_apply_operational`, `max_auto_tasks`,
`require_approval_for_scientific_changes`, `session_resume`, `context_max_chars`, `hpc_poll_seconds`, `max_hpc_resubmissions`.
All live in `.research/config.yaml`; a plan's `execution:` block overrides them for that plan.
