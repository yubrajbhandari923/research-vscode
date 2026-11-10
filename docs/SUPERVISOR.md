# The Research OS supervisor

You write (or have an AI write) a plan in YAML, import it, press **Run plan**, and Research OS starts agents task by task,
checks their work itself, repairs failures within limits, watches HPC jobs, and stops for you when it must. It never needs an LLM to
decide what to run next.

If you have never used Research OS, read [QUICKSTART.md](QUICKSTART.md) first. **Set up your Python and cluster environments once: [SETUP.md](SETUP.md).** The runtime is specified in [spec/08-supervisor.md](spec/08-supervisor.md).

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

Task status keeps its values (`todo`, `running`, `verify`, `done`, `blocked`). The supervisor adds a **phase** per task:

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
| Provider refusal (usage limit, overload, 429) | Task goes to *attention*; the attempt is kept as evidence but **not counted**; nothing is retried automatically, unless `agents.handoff` is enabled with `on_provider_unavailable: try_next`, which continues with the next provider in `fallback_order`, or you opt in to waiting for the reset (§21) |
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
  `thread_id` from `codex exec --json` is stored and a repair runs `codex exec resume --json … <id> <prompt>` (the adapter
  passes the sandbox and writable roots with `-c`, because `codex exec resume` takes no `-s/-C/--add-dir`). **Context hand-off**
  (a fresh agent with the persisted history) is used when native resume is unavailable, and always works. Verifier tasks never resume an
  implementer's session. The Codex adapter runs with `-s workspace-write`; whether a Codex task can reach the cluster for `research hpc submit` depends on your Codex sandbox settings.
* `session_group` marks tasks meant to share a session; the supervisor only continues a session for repairs of the same task.

## 8. HPC

Agents submit through Research OS (`research hpc submit …`, on PATH, inside the experiment worktree). The supervisor links runs to the
attempt by reported run ids and by experiment + time window, waits for them (polling every `supervisor.hpc_poll_seconds`), retrieves
logs and outputs, and verifies. An agent that merely submits jobs does not complete the task. Unavailable status is never treated as
failure and the supervisor itself never submits. **Result contract** (also in every implementer brief): write outputs under
`$RESEARCH_RUN_DIR/outputs/`, metrics to `$RESEARCH_RUN_DIR/metrics.jsonl`, and register each file in `$RESEARCH_RUN_DIR/artifacts.jsonl`,
otherwise they stay on the cluster. Job environment (modules, venv) goes in `hpc.clusters.<name>.slurm.setup`.

### Forking an experiment from another's verified code

Downstream experiments normally start from whatever `main` is. To branch from another experiment's code instead, with no promotion at all:

```yaml
experiments:
  - key: baseline
    title: Baseline
    tasks: [...]
  - key: method-a
    title: Method A
    inherits: {experiment: baseline, revision: verified}   # a key of this plan or an EXP- id; revision: verified (default) | RUN-… run | git commit
    tasks: [...]
  - key: method-b
    title: Method B
    inherits: {experiment: baseline}
    tasks: [...]
```

- The supervisor holds `method-a` and `method-b` until every live task of `baseline` is verified (the status shows why they wait, and a blocked parent
  keeps them waiting until you resolve it). Then it **pins** the parent's last verified commit once: one `inherit` record in `workspaces.jsonl` and the ref
  `refs/research/forks/EXP-00N`, which keeps the commit alive after the parent's worktree is archived. Each child gets its **own worktree and branch** from
  that commit, so `method-a` editing a baseline file never reaches `method-b`.
- `verified` fails closed. Every live task of the parent needs a *supervisor-verified* attempt and the pin is the tip of those attempts' commits. A task a
  human accepted without verification, an uncommitted result (`supervisor.commit_verified_work` off) or a verified run that changed no files gives nothing to
  pin: the fork is refused (or the YAML children keep waiting, with the reason in the status) and you name a commit with `--at <commit>` or `--at RUN-NNNN`.
  A worktree HEAD or base revision is never substituted.
- Forking from the UI: **Work → Experiment worktrees → Fork an experiment…** (same rules as the CLI). Creating the new experiment follows the usual
  governance (for example synthesis-before-new-experiment); the supervisor itself never creates experiments when it pins a YAML fork, so unattended YAML forks
  need no `--override`.
- A fork never follows `main`: automatic promotion's "start from the integrated main" step skips it, and nothing else merges into it. To bring code in
  yourself: `research experiment sync EXP-002 [--from main|EXP-001|<commit>]` (a recorded merge; refused with uncommitted changes or active runs; a
  conflict is aborted and nothing changes).
- Promotion is separate and optional: forking works with `auto_promotion` off. Promoting a fork promotes only its own commits, so promote the parent first.
- A pin never changes. Editing `inherits` of a pinned experiment, or adding it to an experiment that already has work or a worktree, is a conflict in
  `plan yaml diff/apply` (add a new experiment instead). `inherits` counts as a scientific change, so an agent's patch containing it needs your approval.
  Self-inheritance, unknown parents and cycles (including a parent task that depends on a child's task) fail `plan yaml validate`.
- By hand: `research experiment fork EXP-001 --title "Method A" [--at verified|RUN-…|<commit>]` creates the experiment (parent set), pins and creates the worktree.
- Provenance: every run records `workspace.forked_from` (parent, revision, pinned commit) next to its own `head` and source snapshot; `research workspace show`
  and `plan status` list the pin.

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

An operational proposal auto-applies only if `governance.tasks` allows it (type allow-list, per-experiment cap), `supervisor.auto_apply_operational` is on and `max_auto_tasks` holds; approving anything else needs your reason and PIN. Agents can also propose experiments. See [GOVERNANCE.md](GOVERNANCE.md).

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
`require_approval_for_scientific_changes`, `session_resume`, `context_max_chars`, `hpc_poll_seconds`, `max_hpc_resubmissions`, `launch_grace_seconds`, `escalation`,
`commit_verified_work`, `commit_exclude`. The generated [settings reference](spec/10-configuration.md#reference-generated) lists defaults and help for each.
All live in `.research/config.yaml`; a plan's `execution:` block overrides them for that plan.

## 14. Automatic code promotion (opt-in, unattended)

For overnight plans where experiment 2 needs the code experiment 1 wrote. **Off by default; manual mode is unchanged.**

**Two keys, both required, per plan:**
1. The plan YAML declares intent: `execution: {auto_promotion: true}` (reviewable text; it enables nothing by itself).
2. A human authorizes this run: `research plan autopromote PLAN authorize --note "why"` (approval PIN). The authorization is stored in the
   run state with a fingerprint of the verification policy. If `promotion.test_command`, `auto_promotion.numerical_command`, `reviewer_profile`,
   `protected_test_globs` or `promotion.main_branch` change afterwards the plan becomes **suspended** (fail closed) until re-authorized.
   Agents cannot authorize, and cannot enable it through a proposal (changing `execution` is a scientific-class change that needs you).
   You can authorize **before** `plan run` (the plan only has to be applied and declare the flag): nothing starts, no agent launches and no
   run state is created; the authorization (and its fingerprint) is kept beside the plan and becomes part of the run when you run it. A policy
   change between the two leaves the plan suspended, exactly as after a run began. `research plan autopromote PLAN status|revoke` work before
   the run too; `retry` needs a run.

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
* Editing only a plan's `execution:` block and applying it counts as a change.

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

## 18. Halts, the debug agent and chat

**Status tells the truth.** A plan whose run state is `running` but where nothing is running, ready, repairing or promoting is reported **HALTED (needs you)**
(`WAITING FOR YOUR APPROVAL` if only start approvals are pending), in `plan status`, `supervisor status --json` (`health`) and Control Center.

**Debug agent** (read-only, `supervisor_assistant.*`, strongest Claude by default): `research supervisor debug [PLAN]` or the **Diagnose** button reads the blocked tasks
and logs and returns a few sentences plus up to four options. `research supervisor debug --choose N` (or *Use this option*) turns one into an intervention (§17); or write your own.
`notifications.auto_debug: true` runs it automatically after `notifications.halt_grace_seconds` (default 120) of the same halt. Off by default.

**Chat** (opt-in, Telegram): alerts, buttons and commands from your phone, run by one gateway process per machine; setup, pairing, roles, the threat model and the command list are in
[CHAT.md](CHAT.md). Supervisors only write alerts to `.research/supervisor/outbox.jsonl`; the gateway delivers them. The old `research telegram` command still works as an alias of `research chat`.

**Starting after a reboot or an upgrade.** The supervisor (and with it chat alerts) is a separate process: it keeps running when VS Code closes. Opening a project in VS Code
(or any RPC client) records it in `~/.config/research-os/projects.json` and, when Telegram is enabled or a plan is running, starts the supervisor if it is down, or replaces one running older code
(agents and HPC jobs are untouched). `research self-update` does the same restart for every known project after installing. After a reboot with VS Code closed, run `research supervisor start`.
`.research/bin/research` is a short launcher that execs the Python recorded when it was written, so agents always run the installed version; `research bin install` refreshes it and removes any old vendored `lib/` copy.

## 19. Decision requests

Whenever the system needs you (a halted task, a diagnosis with alternatives, a question an agent cannot settle) it asks **one** structured question, stored in
`.research/supervisor/requests/DR-n.json`, and shows it on every surface: `research decisions`, `research decide DR-001`, chat (buttons), VS Code, the TUI.

```bash
research decisions                    # open requests
research decide DR-001                # show the question, the options and exactly what each does
research decide DR-001 2              # pick option 2: its prepared action runs, no model call
research decide DR-001 "skip T-7 and carry on"    # free text: the supervisor plans it (see below)
```

Each option is `{key, label, consequence, action, protected}`. The **action** is prepared when the request is created: an `intervention` (the actions and classes of §17),
an `op` from a short allow-list (`plan_control` run/pause/resume, `supervisor_task` approve/continue/retry/unblock/completed/failed; `plan_promote` and `plan_control stop` are protected),
an `instruct` (a sentence the §17 planner turns into actions: what the diagnosis options are), or nothing. `protected` is **computed**, never taken from whoever created the
request: science, resources, frozen tests, agent roles and promotion are protected. A protected option never runs from chat: the request is flagged for your computer and you
choose it there with the PIN. Everything else runs the moment it is chosen.

* **Agents may ask, never answer.** The MCP tool `decision_request` creates a request; `decision_answer` is human only (and refuses agent shells).
* **Answer once, anywhere.** The first answer closes the request on every surface; a second one gets the first answer back (`already`). A refused PIN reopens it.
* **Expiry** (`expires_hours`, default 72) closes a request without choosing anything: it never picks a default and never chooses a protected option.
* **Chat approval of protected options** uses the authenticator flow in GOVERNANCE.md, not this command.
* `/options` and `/choose N` are aliases: the debug agent's options are stored as a request (`decisions.from_diagnosis`) and `/choose N` answers option N of the latest open one.

## 20. The investigator (on demand)

For situations the rules were not designed for, ask a bounded agent: `research investigate [T-12|PLAN] ["question or instruction"]` (op `supervisor_investigate`, a chat operator action,
not an agent tool; and every **free-text answer** to a decision request, §19). It reads the task (contract, attempts, diagnostics, logs, plan), then returns one
structured outcome that Research OS validates against the closed action vocabulary of §17 and classifies with the same classifier:

* **Unprotected** actions (guide, note, add a task, unblock/retry when nothing protected is pending) run **immediately** as an intervention under your authority.
  The reply says in one line what it understood and what it started: `Understood: … Doing now: INT-001 (…); adding tasks extra.`
* **Protected** actions (acceptance, checks, inputs, datasets, dependencies, agent roles, resources, retry budget, frozen tests) become **one** separate intervention that waits
  for your PIN (`research intervention approve INT-002`). A resume (retry/unblock) travels with it so it runs after an approved amendment. The reply names the part that is waiting.
* **Ambiguous** requests become **one clarifying decision request** with options (each a prepared action), never a guess.
* It **never approves** (nothing in the module calls an approval), never edits a scientific plan or a frozen test (those only change through the protected path), and uses the
  planner profile of `supervisor_assistant.*`. Each run is an event (`investigator_started/outcome/skipped/failed`) and is logged with its text, plan, classes and outcome in
  `.research/supervisor/investigator.json`.

| Setting | Default | Meaning |
|---|---|---|
| `supervisor.investigator.auto` | `false` | also run once per task failure the rules cannot classify (§22) |
| `supervisor.investigator.max_per_plan` | `3` | most runs for one plan |
| `supervisor.investigator.budget_usd` | `2.0` | total provider-reported cost; a run with no reported cost counts toward `max_per_plan` only |

When the budget or run limit is used up (or the agent cannot start), nothing is called and the reply is a plain-text note telling you how to instruct the supervisor yourself;
a decision request you answered in text stays open.

## 21. Usage limits: wait and resume by itself (opt-in)

A 5-hour or weekly limit ends on its own, so nobody needs to be there to press Retry. Off by default (`halt` is the behaviour described in §6); opt in with
`supervisor.usage_limit.on_limit`:

| Setting | Default | Meaning |
|---|---|---|
| `supervisor.usage_limit.on_limit` | `halt` | `halt`: the task stops at *attention* (today). `wait`: only the tasks that use that provider pause until it resets, then relaunch by themselves. `fallback_then_wait`: first the next provider of `agents.handoff.fallback_order`, else wait |
| `supervisor.usage_limit.wait_buffer_seconds` | `120` | added after the provider's stated reset time |
| `supervisor.usage_limit.max_wait_hours` | `8` | a reset further away (a weekly limit) is not waited out unattended: a decision request asks you to wait, switch provider, or stop |
| `supervisor.usage_limit.unknown_reset_backoff_minutes` | `[15, 30, 60]` | relaunch intervals when the reset time is unknown; the last repeats |
| `supervisor.usage_limit.short_backoff_minutes` | `[1, 2, 5]` | the same for rate and overload errors with no reset time |

**Detection.** Every finished attempt, failed or exit 0, has its stdout **and** stderr read through the provider's adapter (`providers.limit_info`: Claude, Codex, opencode,
agy; formats and which are real or realistic in `docs/PROVIDERS.md`), then through the structured-output check `halt` always used. Claude's weekly limit can exit 1 with nothing
on stdout; the supervisor then reads the last API-error message of the session transcript (`~/.claude/projects/...`) when there is one. A run the supervisor stopped for its own
timeout is read too (a provider may sleep inside its own retry loop, for example Claude with `CLAUDE_CODE_RETRY_WATCHDOG=1`; the attempt timeout, 90 minutes by default, is what ends that sleep,
and the limit text is read from what it printed so far; a provider that printed nothing before the timeout is treated as an ordinary failure, which is the known gap).

**What happens.** The attempt is voided and refunded exactly as before: it does not count against `max_attempts_per_task` or `max_total_attempts`; approvals and governance are untouched.
The limit is stored per account (profile `account`, else the provider; all profiles of one provider share one login) in `.research/supervisor/limits.json`, so it survives a daemon restart.
Tasks routed to that account enter phase `waiting_provider` with `resume_at` and are not launched; tasks on other providers keep running. When the time comes the voided attempt is relaunched
with its session resumed where the adapter supports it. The daemon sleeps until the earliest `resume_at` (at most a minute at a time) instead of polling. Time spent waiting does not count
toward `max_wall_minutes`. Pausing or stopping the plan always wins: a paused plan relaunches nothing, and Resume continues the wait. A reset that is not yet known keeps relaunching
at the backoff intervals; a refused relaunch costs nothing.

`fallback_then_wait` never weakens a rule: a fallback is skipped when a strategy level routes the task, when the other profile's model is not in `orchestration.available`, or when that account is
limited too; the task then waits. A credit-balance refusal (no reset time) is never waited out blindly: it asks you.

**Visibility.** Events `supervisor_waiting_provider` and `supervisor_resumed_after_limit`. With chat on, one alert when the wait starts (`Claude limit reached; resuming about 15:05 (in 1h 42m);
other tasks continue`) and one when it resumes. Plan health becomes `waiting_for_provider` and the plan status (`supervisor_status`, `research plan status`) carries
`waiting_provider: {provider, until, until_ts, tasks}` (ISO UTC time, epoch seconds, number of waiting tasks; `null` when nothing waits) for a status chip, and each task row has `resume_at`.
A wait beyond `max_wait_hours` is a decision request (options `wait`, `switch`, `stop`; none is protected).

## 22. The investigator, automatically (opt-in)

With `supervisor.investigator.auto: true`, the halt watcher (its own worker thread beside the debug agent, never the supervisor tick) runs the investigator **once** per blocked
task when a plan halts because a task hit a failure the rules could not classify or used up its deterministic retries (provider limits and approval waits do not count). It is capped by
`supervisor.investigator.max_per_plan` and `budget_usd`; beyond them nothing is called and the halt alert stays as it is. The trigger and its outcome are events (`investigator_started`,
`investigator_outcome`, `investigator_skipped`) and a line in `.research/supervisor/investigator.json`. Everything in §20 still holds: unprotected actions run, protected ones wait for the PIN
approval, and the investigator never approves anything.

## 23. Late evidence: HPC results that arrive after Slurm says "completed"

A Slurm job passes through four stages that used to look like one: **Slurm finished** → **results collected** (the archive is home) → **ingested** (metrics and artifacts are records) →
**verified**. `research plan status` shows the stage for a task that is waiting (`recovery` in `--json`, three lines in text: cause, decision, what needs you).

* **Verification waits for collection.** A completed cluster run whose results are not collected is *not* a failure and is not verified: the task stays `waiting_hpc`
  (reason: "Slurm finished RUN-… but its results are not collected and ingested yet") for at most `supervisor.recovery.collection_wait_minutes` (120). The existing poll
  (`hpc_poll_seconds`) collects the results; nothing is resubmitted.
* **Past the wait** the attempt is verified on what exists. A miss is classed `evidence_pending`, not an implementation failure: no repair attempt starts (an agent cannot create results
  that are still on the cluster), the task is blocked with the reason and the command that fixes it (`research hpc collect RUN`).
* **The same attempt is re-verified automatically.** Every failed verification stores a fingerprint of its evidence (runs, collection state, metric and artifact counts) and of the task's
  contract. When the evidence changes and the contract does not, the supervisor re-runs the *unchanged* checks on the *same* attempt: it works for blocked, attention and repair-pending tasks,
  and for **paused plans too** (a plan paused only because that task failed continues when it recovers; a pause you chose is never undone). The earlier result moves to
  `verification_history`, every step is in the plan's `recovery_log` and the events (`supervisor_recovery`), and a `recovery` alert is sent to chat. At most `max_reverifications` (5) per
  attempt; syncs are rate-limited and capped (`max_syncs`).
* **A root cause is recorded for every failed verification**, deterministically: `collection_pending`, `late_metric`, `missing_file`, `job_failed`, or `numerical` (the evidence is complete and the
  criterion is not met: *no automatic recovery*, a scientific decision). It is shown in the TUI Inbox, `/tasks`, `plan status` and the halt alert.
* **Diagnosis in words, for halted *and paused* plans.** With `supervisor.investigator.auto: true` the investigator (§22) now also runs for tasks in `attention` and for paused plans with failed
  tasks. It receives the run records, numerical metrics, log tails, artifact paths, the task checks, the root cause and previous attempts, and in this automatic mode it may only apply
  `reverify`, `note` and `guide` by itself; `retry`, `unblock`, new tasks and edits wait for you, and anything protected waits for the PIN, as before.
* **Never automatic:** relaxing a threshold, changing a scientific assumption or check, raising an HPC budget, submitting a job. Recovery only syncs/collects (read-only on the cluster),
  reconciles collected files and re-runs the unchanged checks. Switch it off with `supervisor.recovery.enabled: false` (then a task is verified the moment its run is terminal).

## 24. Git hygiene: what is uncommitted, and the reviewed commit

Three places stay apart. The **main repository** is yours: Research OS never commits an arbitrary file there. **Experiment worktrees**: the supervisor commits verified work on the
experiment's own branch. The **ledger** (`.research/`, the shared ledger) is Research OS's own and is only reported.

* `research git status` (and the TUI **Git health** page, key `h`) lists tracked changes, untracked files, ignored files that look important, every worktree (modified, untracked, unpromoted commits),
  pending promotions and the ledger, each file with *what it is and why it matters* (a plan, source, a report, scaffolding, a secret, a cache).
* **Preflight.** `plan run`, `plan resume`, `plan autopromote authorize` and `promote approve` run the same check and print what matters ("experiment worktrees start from the last commit, so agents will NOT
  see these edits"). `git.preflight`: `warn` (default, report and continue), `block` (refuse until reviewed and committed; promotion is also refused while main has uncommitted tracked changes), `off`.
* **Reviewed commit.** `research git commit --paths notes.md` or `research git commit --scaffold` (AGENTS.md, CLAUDE.md, `.gitignore`, website sources: what Research OS generated) shows the staged diff, then needs `--yes`
  (or `y` at a terminal / in the TUI). It commits exactly those files (`git commit --only`): anything else you staged or modified stays as it is. Secrets, caches and runtime state are refused, agents cannot run it,
  nothing is pushed. After `init`, `agent setup` and `site init --apply`, uncommitted scaffolding is pointed out with the exact command.
* **Ignores.** `research git ignore` previews and `--apply` writes one managed block in `.gitignore` for generated and internal files (`.research/`, caches, `.venv/`, `__pycache__/`, build output).
  A pattern that would hide source, a plan, a report or documentation is refused and named; lines you wrote are never touched; tracked generated files stay tracked (untracking is yours).
* **Verified work** is committed on the experiment branch with a safe selection: source, tests, reports and small figures go in (`supervisor.commit_keep`, `commit_max_file_mb`, even from `outputs/`);
  bulky outputs, `commit_exclude` paths, caches, compiled files and secrets do not. The attempt records what was committed and skipped (`commit_report`).
* **Headless.** Every git call is non-interactive (no prompts, editors, pager or credential helper) with a timeout; a hook, identity or signing problem is an error with the fix, never a hang. Supervisor commits
  use the supervisor's identity without signing.

## 25. Headless permissions: what an agent may run, and what happens when it is refused

A headless Claude agent (nobody can answer a prompt) may edit files and run the `research` CLI; arbitrary Bash is refused. That default stays.

* **Run the declared checks without Bash:** `research agent check T-001` runs the task's own checks (read-only: no state change, nothing recorded). The brief says so.
* **Allow a command on purpose:** `agents.profiles.<name>.allow_commands: ["python scripts/smoke.py *"]` adds exactly those commands (one pattern each) to the profile's allow-list; nothing else widens.
* **A refusal is a permission failure.** The provider's own result lists the denied commands; the attempt is `permission_denied`, not missing metrics or an environment problem. The task is blocked with the commands, the two ways out above and
  the command to continue (`research supervisor resolve T-001 --unblock`). Nothing was submitted by that attempt; it stays in the ledger exactly as it was; the next attempt counts against the normal budget; checks, budgets and
  `max_hpc_resubmissions` are untouched, so an HPC job already submitted is never duplicated.
* **Before launch** the supervisor compares commands the task text tells the agent to run (in backticks or after `$ `) with the profile's allow-list (Claude only, where it is known). A conflict is an event
  (`supervisor_permission_conflict`), a `permission_notes` field in `plan status --json`, and a "Permissions (read first)" section in the agent's brief.

## 26. Promotion coverage: which commits belong to which verified task

The supervisor commits verified work itself and records that commit on the attempt. An agent often commits first; then the tree is clean, no supervisor commit exists and the attempt records none. So that automatic promotion neither loses that work nor takes unverified work, every commit of the experiment branch gets one verdict (`research plan autopromote PLAN coverage`):

| Verdict | Meaning | Effect |
|---|---|---|
| included | the supervisor's recorded commit; a supervisor trailer naming a recorded execution; an agent commit inside the attempt's `head_before..head_after` (older ledgers: its started..ended window) and claimed by no other task | promoted with the task's other commits, in branch order |
| in progress | claimed by a task that has not verified | left out; not an error |
| excluded | a human ran `exclude --commit SHA` | never promoted |
| orphan | no attempt claims it (a person's commit, or older than the attempt records) | stops the candidate until decided |
| ambiguous | two tasks claim it (parallel agents in one worktree) | stops the candidate until decided |

Decide a stopped commit with `research plan autopromote PLAN attribute --commit SHA --task T-003 --note "why"` (the task must be verified) or `exclude --commit SHA`, then `retry --item AP-001`. Attributions are written once to the run state (`commit_provenance`) with their basis; attempts are never edited and history is never rewritten. Dates and trailers are what the committer wrote: this is attribution evidence, and the reviewer, frozen-test and test gates still apply to everything promoted. A paused item whose candidate predates some attributed commits says so in `plan status`; `retry` prepares a new candidate from the same verified work.
