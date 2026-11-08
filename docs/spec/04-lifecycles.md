# 4. Lifecycles and state machines

The lifecycle of supervised plans and tasks (phases, health, attempts) is in [supervisor](08-supervisor.md#the-supervisor).

## Question

- Statuses are free to move in any direction (`open` · `investigating` · `answered` · `blocked` · `abandoned`).
- **Automatic transition:** registering an experiment under an `open` question sets it to `investigating`.

## Experiment

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

## Experiment state flags (`experiment_state`)

| Flag | Definition |
|---|---|
| `finished` | runs with status ∈ {completed, failed, cancelled, unknown} |
| `active` | runs with status ∈ {queued, running} |
| `unsynthesized` | finished runs not in the union of all syntheses' `runs_covered` |
| `unreviewed_failed` | failed runs with `reviewed = 0` |
| `needs_synthesis` | `unsynthesized > 0` and `active == 0` |
| `over_run_budget` | `unsynthesized ≥ max_runs_without_synthesis` |
| `over_failure_budget` | `unreviewed_failed ≥ max_failed_runs_without_review` |

## Run

```
queued ──▶ running ──▶ completed (exit 0) | failed (exit ≠ 0) | cancelled (signal / scancel)
running ──(runner process vanished on this host, no exit recorded)──▶ unknown
queued/running (SLURM) ──(left queue; sacct TIMEOUT/OOM/NODE_FAIL/…)──▶ failed
```

- Reaching a terminal state triggers the experiment's automatic transitions ([Experiment](#experiment)).
- Synthesis sets `reviewed = 1` on all failed runs of that experiment.

## Finding and decision

- **Finding:** `preliminary ⇄ supported`, `→ contradicted`, `→ superseded` (via supersede, which records `superseded_by`).
  For agents, `→ supported` only happens through an independent review ([Verification gates](05-agents-governance.md#verification-gates)); a review also drives
  `needs_work → preliminary` and `contradicted → contradicted`.
- **Decision:** `active → reversed | superseded`.
- History is never rewritten. Superseding keeps the old object visible under "Contradicted & superseded".

## Plan

- Statuses move freely: `active` · `completed` · `blocked` · `abandoned` (`research plan update --status`, `research mark`, or the UI).
- Setting `completed` through `update_plan` stamps `completed_at` if it is not given.
- Plan status is **not** derived from task status. Completing every task does not change the plan's `status`; a supervised run's completion is tracked separately ([The supervisor](08-supervisor.md#the-supervisor)).
- The newest `active` plan (by `created_at`) is the one shown on Research Home ([Research Home](07-context-reports.md#research-home-research_home-rpc-home)).

## Task

```
todo ──(start_task / status=running)──▶ running ──(complete_task / status=done)──▶ done
running ──(block_task)──▶ blocked          any ──(explicit)──▶ verify | todo | …
todo + all depends_on done  ⇒  "ready"  (derived; never stored)
```

- Any status can be set explicitly. Starting a non-ready task is allowed but returns a warning ("waiting on T-…").
- **Claims ([Coordination between agents](05-agents-governance.md#coordination-between-agents)):** `start_task` records `claimed_by` / `claimed_at`; starting a task another actor holds raises
  `PolicyBlocked` (exit 3) unless `force`. `release_task` returns it to `todo` and clears the claim.
- **Completion gate ([Verification gates](05-agents-governance.md#verification-gates)):** `complete_task` first refuses if the task's frozen acceptance tests were modified (`orchestrate`), then runs the task's checks; failures block agents and warn humans.
- **`verify` with checks:** `research task verify` moves a `verify` task to `done` when all checks pass, or back to
  `running` when any fails.
- Entering `running` stamps `started_at`, and entering `done` stamps `completed_at` (unless the caller passes them).
- `verify` means "work finished, awaiting verification". It surfaces as an attention item on Research Home. Outside supervised plans nothing
  moves tasks into `verify` automatically; a verifier agent is dispatched to it with `research agent dispatch verifier T-…`.
- **Next ready task** (`get_next_ready_task(plan_id=None)`): walks tasks in creation order (optionally within one plan)
  and returns the first `todo` task whose dependencies are all `done`, with resolved `dependencies[]` and `is_ready`.
  Returns `None` if there is none.
