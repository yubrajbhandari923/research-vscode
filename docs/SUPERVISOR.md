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

## 10b. Escalate after an incomplete deliverable (opt-in)

When an attempt ends with required outputs missing (a `file:` check "missing" or "exists but is empty", or a required metric never recorded), the
supervisor classifies it `incomplete_deliverable`. By default the repair runs as planned. To spend more on exactly this case:

```yaml
execution:
  escalation: {model: claude-opus-5-5, effort: high}   # any of model / effort / profile; optional on: [...], max_escalated_attempts: 1
```
(or `supervisor.escalation` in `.research/config.yaml` for every plan). Only the next repair attempt is escalated (default one attempt, always
inside `max_attempts` and the plan budgets), it starts a fresh session (a different model cannot resume the old one), and the attempt records
`escalated`. `effort` is honoured by Codex and Antigravity; Claude escalates by `model`. A wrong result (a command check failed while the files exist)
is not escalated. A human's "Continue with another agent" choice still wins over `escalation.profile`.

## 10c. Failures that repeating cannot fix

A missing file is classified before anything is re-dispatched:

| Category | Meaning | What happens |
|---|---|---|
| `incomplete_deliverable` | this task owns the file and did not write it | bounded repair (optionally escalated, 10b) |
| `missing_prerequisite` | another task owns the file (it is upstream) and it is not where this task runs | **blocked after one attempt** with the owner task, whether it is done, and "promote/integrate it first" |
| `role_contradiction` | a **verifier** must itself create the file; verifiers check work and are told not to author it | **blocked after one attempt**: change the role to analyst/implementer, or move the file check to the task that writes it |

Blocked tasks keep their full attempt history, budgets and approvals; nothing is rewritten and no review requirement is weakened. Resolve with
`research supervisor resolve TASK --unblock` after fixing the plan or the upstream task. `research plan yaml validate|diff` warns about the
role contradiction before anything runs. Note: only **implementer** tasks get an experiment worktree; analyst and verifier tasks run in the main
checkout, so an implementer's files on its branch are not visible to them until the work is integrated into `main`.

## 11. Security and limits (read this)

* **Enforced**: agents cannot complete supervised tasks by CLI, MCP, RPC or hand-edited task files; agent reports are accepted only from
  the execution that owns the active attempt; policy settings are not agent-editable; headless Claude is limited to `research` commands
  and file edits (no arbitrary shell).
* **Advisory only**: `allowed_files`, "do not edit tests", "do not push" are instructions in the prompt. Without a sandbox profile an
  agent that can run Python can do anything the OS user can. Use `sandbox` profiles for real isolation.
* **Identity is self-declared** (`--as`, environment). It stops accidents and cooperative agents, not an adversarial process. Do not
  rely on human-only gates against hostile code.
* No secrets belong in YAML. Prompts and reports are redacted for common credential patterns, which is best effort.
* Nothing is merged, pushed or deleted automatically. Promotion to `main` stays in the existing review workflow, unless a human has
  authorized **automatic code promotion** for that plan (section 14), which can only fast-forward local `main`.

## 12. Compatibility

Manual Research OS workflows are unchanged when no plan is running. Supervision applies only while a plan run is `running`, `paused`,
`blocked` or `attention`; `supervisor.enforce_completion: false` turns the guards off. New settings are under **Settings → Supervisor**.

## 13. Settings

`supervisor.poll_seconds`, `max_parallel_agents`, `max_attempts_per_task`, `max_total_attempts`, `max_wall_minutes`,
`attempt_timeout_minutes`, `on_failure`, `enforce_completion`, `auto_apply_operational`, `max_auto_tasks`,
`require_approval_for_scientific_changes`, `session_resume`, `context_max_chars`, `hpc_poll_seconds`, `max_hpc_resubmissions`.
All live in `.research/config.yaml`; a plan's `execution:` block overrides them for that plan.

## 14. Automatic code promotion (opt-in, unattended)

For overnight plans where experiment 2 needs the code experiment 1 wrote. **Off by default; manual mode is unchanged.**

**Two keys, both required, per plan:**
1. The plan YAML declares intent: `execution: {auto_promotion: true}` (reviewable text; it enables nothing by itself).
2. A human authorizes this run: `research plan autopromote PLAN authorize --note "why"` (approval PIN). The authorization is stored in the
   run state with a fingerprint of the verification policy. If `promotion.test_command`, `auto_promotion.numerical_command`, `reviewer_profile`,
   `protected_test_globs` or `promotion.main_branch` change afterwards the plan becomes **suspended** (fail closed) until re-authorized.
   Agents cannot authorize, and cannot enable it through a proposal (changing `execution` is a scientific-class change that needs you).

**Settings** (`.research/config.yaml`):
```yaml
promotion:
  test_command: pytest -q tests/integration      # canonical and required (Settings → Git & workspaces); `auto_promotion.test_command` is a legacy alias,
                                                 # honoured only if this is empty, and a conflict if both differ
auto_promotion:
  numerical_command: pytest -q tests/numerical   # optional, run after the tests; "not_configured" is recorded when absent
  reviewer_profile: codex                        # required: a configured agent profile that did NOT implement the work
  require_distinct_reviewer: true
  review_timeout_minutes: 120
  max_file_mb: 5
  protected_test_globs: ["tests/*", "*/tests/*", "test_*.py", "*/test_*.py", "*_test.py", "conftest.py", "*/conftest.py"]
```

**What happens** (one transition per supervisor tick, one promotion at a time, in order):
1. *Track.* Each verified task already commits its work on its experiment branch (`supervisor.commit_verified_work`). Those commits are
   "pending" until they are in `main`. Already-integrated commits (ledger or ancestry) and no-op tasks (no commit exists) are never listed.
2. *Trigger.* At an **experiment boundary** (every task of the experiment verified), or earlier when a **downstream task depends on that
   code** (any transitive `depends_on` into another experiment). No promotion per task, so tests run once per boundary.
3. *Prepare.* A candidate from the **current** `main` with only the pending commits cherry-picked (`promote/PROMO-n-…`). Identical to
   `main` -> recorded as a no-op. Guards: promoted work may not modify/delete existing tests (new tests are fine), may not add files over
   `max_file_mb`, never touches `.research/`. Datasets and results stay out of `main` (`supervisor.commit_exclude`).
4. *Verify.* `test_command`, then `numerical_command`, in the candidate. Failure pauses the item; commands are never rewritten.
5. *Independent review.* A fresh headless verifier on `reviewer_profile` is started and records `research promote review`. The preparer
   (supervisor) and any implementing profile are refused. A human may review instead (`research promote review PROMO-n --verdict approve`).
6. *Authorized integration.* The supervisor records a code-approval as `system/supervisor` (never as a person; the record names the
   authorizing human and `mode: supervisor_authorized`) and runs `git merge --ff-only` into local `main`. Requires `main` to be exactly
   the revision the candidate was tested on and its worktree to have no tracked changes.
7. *Unblock.* Dependents wait while the upstream item is open. Their worktree is created from the integrated `main` SHA. An existing
   worktree with no work of its own is fast-forwarded; one with its own (already promoted) commits gets a recorded merge of `main` into
   the **experiment branch** (aborted and blocked on conflict). Nothing is ever rebased.

**Code, not science.** Evidence for these candidates is informational: a negative or undecided hypothesis can promote valid code, a
positive one is not "approved" by it. No finding or claim is created, changed or approved. Results/figures stay in the experiment
worktree and ledger; `.research/supervisor/promotions/AP-n.json` records source/candidate/main SHAs, the worktree path and its figures so
the Quarto guide keeps resolving them.

**Failure = pause, not retry.** Test/numerical failure, requested changes, a moved or dirty `main`, a refused merge, a missing reviewer or
an unexpected error pauses that item with the error persisted; later promotions queue behind it and dependents keep waiting. Nothing is
retried blindly. A human fixes the cause and runs `research plan autopromote PLAN retry --item AP-001`, which abandons the old candidate
(kept on disk) and prepares a new one from the current `main`, re-tested. `research plan autopromote PLAN status` shows everything;
`revoke` returns to manual mode. State is persisted per item (`source`, `candidate_head`, `base_main`, `main_after`, tests, numerical,
review, error, history); a restart adopts the open candidate (never a duplicate) and never integrates twice.

Never done automatically: push, force, reset, rebase, delete branches/worktrees/untracked files, edit hypotheses, plans or tests.

Limits: the PIN/authorization are accident guards, not defence against a hostile process with your OS user; the plan run waits for the
final promotions before it reports `completed`; tests run inside the tick (a very long `test_command` delays other scheduling; keep
`promotion.test_timeout_seconds` sensible); a live reviewer-agent launch needs a working headless profile (`research doctor`).

## 15. The `research` command from the main checkout

`research bin install` (also run by init and `ledger enable`) writes the launcher into the ledger and also links it into the
repository's main checkout: `./research …` works from `main/` with no long path and no PATH change. The link is absolute (machine-specific), listed in
`.git/info/exclude` (never committed), never replaces an existing file or directory (installing still succeeds; use `research bin link --name rs`),
and is not created inside experiment worktrees. `research bin unlink [--name rs]` removes only its own link. To use `research` anywhere, put it on
your PATH yourself, for example `ln -s "$PWD/research" ~/.local/bin/research` (it resolves the project from the directory you run it in).

## 16. Reload, restart and parallelism

* `research supervisor reload` validates the project settings, then has the running supervisor apply them at once. The supervisor already re-reads settings
  on every scheduling pass; reload makes that immediate, refuses invalid values (nothing applied), and reports each changed key as **live**
  (`supervisor`, `governance`, `promotion`, `auto_promotion`, `verification`, `coordination`), **next launch** (`agents`, `environment`, `hpc`: running
  agents/jobs keep what they started with) or **restart** (anything else, for example `layout`). It also flags when the supervisor runs an older Research OS
  than the one installed. Agents, worktrees, plan state, budgets and promotion state are not touched.
* `research supervisor restart [--timeout S]` replaces the process with the installed Research OS. Agents and HPC jobs are separate processes and keep running; the new
  supervisor reconciles every recorded execution before scheduling, so nothing is launched or submitted twice. If the old supervisor is in a long pass
  (for example running promotion tests) it finishes it, then starts its successor. Both are human-only and are buttons in **Control Center → Plans**
  (the restart button is highlighted when the running supervisor is older than the installed version).
* Concurrency: `supervisor.max_parallel_agents` (project) is overridden by a plan's `execution.max_parallel_agents`. Control Center and `plan status --json`
  (`concurrency`) show the effective number, where it comes from, and how many agents run or wait. Tasks in the same experiment worktree never run together.
* Editing only a plan's `execution:` block and applying it now counts as a change (it used to be reported as "no changes" and ignored).

## 17. Steering a task in words (supervisor intervention)

`research supervisor instruct T-12` (type the instruction, finish with an empty line) or `--message "…"`; in Control Center → Plans → **Instruct / Unblock** on any
unfinished task. A planning agent (read-only; `supervisor_assistant.profile/model`, strongest Claude by default) reads the task's contract, attempts,
diagnostics, logs, evidence and plan, and returns a small set of actions. Nothing it returns is trusted: only a closed vocabulary with the right shapes is accepted.

| Class | Actions | Needs approval? |
|---|---|---|
| operational | unblock, retry, reverify (re-check the latest output under the current contract), note | no |
| implementation | guide (text injected into the next brief), prompt/title edits, add a task | no (the instruction is yours) |
| scientific | acceptance, checks, expected artifacts, inputs, datasets, dependencies, **agent role** | **yes** |
| resource | cpus/mem/time/partition/account/gpus, **retry budget** (`max_attempts`) | **yes** |
| frozen test | authorize a change to a frozen test | **yes** (recorded; re-freezing stays `research orchestrate correct-test`; the intervention never edits the file) |

You see the preview first. If anything is protected, **one** approval covers the whole proposal: `research intervention approve INT-001 --note "why"` (PIN) or the
dialog. Agents and chat cannot approve. `intervention.require_approval: true` asks even for unprotected ones.

Lifecycle, persisted in `.research/supervisor/interventions/INT-n.json`: `requested → proposed → approved → implementing → verifying → resolved` (or `rejected`,
`failed`, `cancelled`). The supervisor tick applies each action once (flagged before the next, so a restart never repeats one) through the existing APIs: plan YAML
reconciliation, `resolve`, `void`. The task then runs a normal supervised attempt with your instruction in its brief, and the intervention is `resolved` only when
the supervisor verifies it. Downstream tasks resume by themselves.

Running agents: by default the instruction is queued until the attempt ends (if the attempt succeeds meanwhile, nothing is changed). `--interrupt` stops the agent
safely and keeps its attempt as evidence (voided = not counted); refused while computational jobs are pending (no repeated submissions). One open intervention per task.

Evidence is never rewritten: failed attempts and their diagnostics stay; a re-verification keeps the earlier result in `verification_history`; an amended contract
keeps its `originals`, and a Decision records before/after, your instruction, the rationale and who approved it.

`research intervention list|show|approve|reject|cancel|plan`.

## 18. Halts, the debug agent and Telegram

**Status tells the truth.** A plan whose run state is `running` but where nothing is running, ready, repairing or promoting is reported **HALTED (needs you)**
(`WAITING FOR YOUR APPROVAL` if only start approvals are pending), in `plan status`, `supervisor status --json` (`health`) and Control Center.

**Debug agent** (read-only, `supervisor_assistant.*`, strongest Claude by default): `research supervisor debug [PLAN]` or the **Diagnose** button reads the blocked tasks
and logs and returns a few sentences plus up to four options. `research supervisor debug --choose N` (or *Use this option*) turns one into an intervention (§17); or write your own.
`notifications.auto_debug: true` runs it automatically after `notifications.halt_grace_seconds` (default 120) of the same halt. Off by default.

**Telegram** (opt-in): `research telegram setup` (hidden token prompt; then message your bot once) or Control Center → Plans → *Telegram alerts*; enable with
`notifications.telegram.enabled`. The token and chat id live in `~/.config/research-os/telegram.json` (0600), never in the project, git, prompts or events; only that chat is obeyed.
You get one message per halt (what is blocked, the diagnosis and options) and one when the plan moves again. Commands: `/status /blocked /task T-12 /logs T-12 /debug /options /choose N
/say [T-12] <text> /interventions /pause /resume /help`; plain text goes to the only blocked task. Chat can request and apply **unprotected** changes; protected ones are
proposed and wait for your PIN in VS Code or the terminal.

**Starting after a reboot or an upgrade.** The supervisor (and with it Telegram alerts) is a separate process: it keeps running when VS Code closes, and opening a project in VS Code does not start or restart it.
After a reboot run `research supervisor start`; after `research self-update` run `research supervisor restart` so it runs the installed version (agents and HPC jobs are untouched). Agents started through `.research/bin/research`
keep using the copy installed there until `research bin install --force`.
