# 8. Supervisor, promotion, intervention and notifications

User guides: [SUPERVISOR.md](../SUPERVISOR.md) (running plans) and [GOVERNANCE.md](../GOVERNANCE.md) (who may do what). This chapter is the specification of the
runtime. Approval and governance rules are summarised in [agents and governance](05-agents-governance.md#governance-and-approvals).

## The supervisor

`supervisor.py` is a deterministic state machine over durable files in `.research/supervisor/`; no model decides what to run next. The persistent process is
`python -m research.supervisord run --root DIR` (`research supervisor start`). It reopens the project on every pass, so settings apply live, and it records a
heartbeat in `daemon.json` (informational: a stale heartbeat proves nothing, the process is checked). `daemonlib.py` holds the generic single-instance lock, detached
spawn, heartbeat and version-stale check (`ensure_current`) that long-running per-project processes share.

| Path under `.research/supervisor/` | Content |
|---|---|
| `plans/<PLAN-ID>.json` | run state: plan state, per-task phase and attempt ledger, budgets |
| `specs/<plan-key>.json` | the desired plan ([planspec](#plans-as-yaml)) |
| `reports/<T-ID>.jsonl` | structured reports from agents, each attributed to an execution |
| `proposals/<n>.json` | plan-change proposals |
| `interventions/INT-n.json`, `promotions/AP-n.json`, `prompts/`, `daemon.json`, `daemon.log`, `state.lock` | intervention records, automatic-promotion items, the exact prompt of each attempt, process state |

**Plan run state** is `running`, `paused`, `stopped` or `completed`. **Health** is derived from it and is what status commands and the Plans page show:
`progressing`, `idle` (running, waiting on dependencies), `waiting_for_approval`, `halted` (running, nothing moving, something waits for a human), `completed`, `paused`, `not_run`. Health never claims
"running" for a plan that cannot continue; it names the blocking tasks and why. A plan completes when all its tasks are verified (the plan's own `status`
field is not derived, [lifecycles](04-lifecycles.md#plan)).

**Task phases** are kept in the supervisor's state, not in the task's `status`: `waiting`, `ready`, `awaiting_approval`, `running`, `waiting_hpc`, `verifying`,
`repair_pending`, `attention`, `blocked`, `verified`, `retired` (`supervisor.PHASES`). `attention` and `blocked` need a human (`supervisor resolve|approve|accept`, or
`supervisor instruct`). One task has at most one active attempt; one worktree has at most one active coding agent; `max_parallel_agents` caps the rest.

**Attempts.** Each attempt reuses the execution-record lifecycle ([agents](05-agents-governance.md#roles-profiles-providers-and-dispatch)). An attempt that exits 0 is not a passed
task: `verify_attempt` decides, and only it (or an explicit human `accept`) may complete a supervised task. Verification runs, in the task's workspace, frozen-test integrity,
approved post-implementation hooks, the task's checks and its expected artifacts; if the task produced Slurm runs it waits for them first. No verifier means "unverified",
never "passed".

**Failure categories** are recorded per attempt (`supervisor.CATEGORIES`): `missing_prerequisite`, `role_contradiction`, `incomplete_deliverable`, `incorrect_implementation`,
`ambiguous_specification`, `insufficient_test_oracle`, `environment`, `scientific_assumption`, `resource_limit`, `agent_process_failure`, `unknown_execution_state`.
Repairable categories are retried within `max_attempts_per_task`, `max_total_attempts` and `max_wall_minutes`; `environment`, `resource_limit` and unknown execution state need a human;
`missing_prerequisite` and `role_contradiction` block after one attempt. An `incomplete_deliverable` may escalate the next repair attempt to a stronger model or effort
(`execution.escalation`, opt-in). A provider refusal read from the agent's structured output (usage limit, overload, quota) voids the attempt (not counted, kept as evidence) and moves the
task to `attention`, unless `agents.handoff` opts into trying the next provider in `fallback_order`. Repairs never edit acceptance tests, raise budgets, change hypotheses or
resubmit HPC jobs. Verification results are kept in `verification_history` and never overwritten.

**HPC.** The supervisor never submits. It links runs to an attempt by reported run ids and by experiment and time window, polls (`supervisor.hpc_poll_seconds`), collects results and verifies.

**Commits.** When a task verifies, the supervisor commits its changes on the experiment branch in that task's worktree (`supervisor.commit_verified_work`, excluding `supervisor.commit_exclude`); it never touches `main` and never pushes.

**Settings and lifecycle.** `supervisor.*` settings live in the registry; a plan's `execution:` block overrides them for that plan. `supervisor reload` validates and applies settings at once and
reports each changed key as live, next launch or restart; `supervisor restart` replaces the process (agents and HPC jobs keep running and every recorded execution is reconciled before scheduling,
so nothing is launched or submitted twice). Opening a project over RPC records it in `projects.json` and starts or refreshes the supervisor when a plan is running or Telegram is enabled;
`research self-update` restarts the daemon of every known project.

## Plans as YAML

`planspec.py` defines the plan schema (`schema_version: 1`; a newer version is rejected, never guessed at). A file holds `project.context`, plans with `execution` limits, experiments and tasks
(`key`, `prompt`, `depends_on`, `agent`, `checks`, `expected_artifacts`, `allowed_files`, `resources`, `execution`). Unknown fields are errors (keys beginning `x_` are kept and ignored) and every problem
is listed at once. The YAML is desired work; runtime history is never rewritten by `apply`. `export` writes the plan with its revision; applying a file based on an older revision is a conflict; a
`partial` scope never deletes, a `full` scope retires unstarted tasks missing from the file; changing a running task queues the change for the next attempt; changing a done task is refused. Scientific
changes need approval. Agents propose changes as a partial YAML patch (`agent_propose`); a patch that only adds tasks is operational and may auto-apply within `governance.tasks` and
`supervisor.max_auto_tasks`, anything else waits for a human.

## Governance, approvals and the PIN

See [agents and governance](05-agents-governance.md#governance-and-approvals). The supervisor acts through in-process grants and never impersonates a human.

## Selective promotion into main

`promotion.py`; records in `.research/promotions.jsonl` (append-only, folded on read, no machine paths). Requires shared-ledger mode. Scientific success, technical correctness, reusability,
integration-readiness and promotion are reported as separate values (`classification`; `null` means not known); an unsuccessful experiment stays fully documented and simply never becomes
integration-ready.

1. `research promote changes WS-n` lists the workspace's commits and files (uncommitted work is flagged: it cannot be promoted).
2. `prepare WS-n --commit SHA... | --path P...` creates `promote/PROMO-n-...` in its own worktree from the **current** main and applies only the selection (`cherry-pick -x`, or `checkout <workspace head> -- paths`
   committed once). Paths must be relative, free of `..`, outside `.research`, and committed. The workspace and main are untouched. A failed preparation removes only what it just created.
3. `diff` is exactly `main...candidate`. `verify` runs `promotion.test_command` in the candidate and binds the result to its head commit.
4. Gates: tests passed for the **current** head; scientific evidence (a supported finding for the experiment, `promotion.require_supported_finding`); an implementation `review` by someone other
   than the preparer (agents may review) and only after passing tests; `approve` by a **human** only, bound to the head commit, so any later commit makes the approval stale.
5. `integrate --confirm` (human only) runs `git merge --ff-only` in main's worktree. It is refused when a gate is open, main's worktree has uncommitted changes, or main moved since preparation. It
   never forces, rebases, deletes branches or pushes, and records the integration commit, main's previous tip and the evidence. Nothing in this system merges into main otherwise.

Not implemented: hunk-level selection, running the candidate's tests inside a sandbox profile, non-fast-forward strategies, automatic cleanup of promotion worktrees, push or pull-request creation.

### Unattended promotion

`autopromote.py`, opt-in, per plan, two keys: the plan YAML declares `execution.auto_promotion: true` (intent only) and a human authorizes the run with
`research plan autopromote PLAN authorize` (approval PIN). The authorization stores a fingerprint of the verification policy (`promotion.test_command`, `auto_promotion.numerical_command`,
`reviewer_profile`, `protected_test_globs`, `promotion.main_branch`); a change suspends the plan (fail closed) until re-authorized. At an experiment boundary, or earlier when a downstream task
depends on the code, one candidate at a time is prepared from the current main (pending commits only, never modifying existing tests, never `.research/`), tested, independently reviewed by a fresh
headless verifier on `reviewer_profile` (never the implementing profile; a human may review instead), then integrated by `git merge --ff-only` recorded as `system/supervisor` with the authorizing human
named. Any failure, a moved or dirty main, a refused merge or a missing reviewer pauses that item; nothing is retried blindly (`autopromote retry --item AP-n` prepares a new candidate). It promotes code,
never science, and never pushes, forces, resets, rebases or deletes anything.

## Supervisor intervention

`intervene.py`. `research supervisor instruct T-12` sends a read-only planning agent (`supervisor_assistant.profile/model`) the task's contract, attempts, diagnostics, logs and plan; it returns actions from a
closed vocabulary and nothing else is accepted.

| Class | Actions | Needs approval |
|---|---|---|
| operational | unblock, retry, reverify, note | no |
| implementation | guide, prompt or title edits, add a task | no |
| scientific | acceptance, checks, expected artifacts, inputs, datasets, dependencies, agent role | yes |
| resource | cpus, mem, time, partition, account, gpus, retry budget | yes |
| frozen test | authorize a change to a frozen test (the intervention never edits the file) | yes |

One approval (`research intervention approve INT-n`, PIN) covers a whole proposal; agents cannot approve. Lifecycle, persisted in `interventions/INT-n.json`: `requested`, `proposed`, `approved`,
`implementing`, `verifying`, `resolved` (or `rejected`, `failed`, `cancelled`). The supervisor applies each action once through the existing APIs; evidence is never rewritten.

## Notifications, the debug agent and Telegram

`notify.py` detects a halt and runs the opt-in debug agent (`supervisor_assistant.*`; `research supervisor debug [PLAN]`), which reads blocked tasks and logs and returns a few sentences plus up to four
options; `--choose N` turns one into an intervention. `notifications.auto_debug` runs it after `notifications.halt_grace_seconds` of the same halt. Telegram (opt-in, `notifications.telegram.*`) sends one message per halt
and one when the plan moves again, and accepts commands from the one configured chat; the token and chat id are machine-level (`telegram.json`, mode 0600) and the update offset is saved before each message is
handled. Chat can request and apply unprotected changes; protected ones are proposed and wait for the PIN at the computer. The Telegram code and its config keys are the notification surface today; any other
channel is built on the same halt events.
