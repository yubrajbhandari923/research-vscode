# 13. Operations: recovery, settlement, permissions, git hygiene, several projects

This chapter specifies behaviour added in 3.5.x. It refines [chapter 8](08-supervisor.md) (supervisor) and [chapter 6](06-execution.md) (execution). Where they differ, this chapter describes the built behaviour.
The user-level summaries are in [SUPERVISOR.md](../SUPERVISOR.md) §23-25 and [CHAT.md](../CHAT.md).

## 13.1 Evidence stages of a cluster run

A Slurm run passes four stages that earlier versions treated as one. `recovery.stage(run)` returns one of:

| Stage | Meaning |
|---|---|
| `slurm_pending` | the run is not terminal |
| `ended` | terminal and not `completed` (failed, cancelled, unknown) |
| `collection_pending` | `completed`, the run was submitted through `research hpc`, and `slurm.hpc.state` is not `collected` |
| `collected` / `ready` | results are home and ingested (or the run has nothing to collect) |

**Verification gate.** `supervisor._verify_attempt` does not judge an attempt whose task runs include a `collection_pending` run while the wait is inside its bound
(`supervisor.recovery.collection_wait_minutes`, default 120; the clock starts at the first observation, stored as `attempt.collect_wait_since`). The task stays `waiting_hpc` with a reason and a
`recovery` record (cause `collection_pending`). With `supervisor.recovery.enabled: false` the gate is off. Past the bound the attempt is verified on what exists; a missing-evidence failure
is classed `evidence_pending`: not an implementation failure (no ledger failure is recorded, no repair attempt starts), the task is `blocked` with the command that fixes it.

## 13.2 Automatic re-verification

Every failed verification stores, in `attempt.verification`, `evidence_fp` (hash of the experiment's runs: status, collection state, metric count, artifact ids/hashes, experiment-level metric count) and
`contract_fp` (hash of the task's checks, acceptance, expected artifacts, inputs, datasets, dependencies, experiment link). `recovery.step` runs each tick for **running and paused** plans over tasks in
`blocked`, `attention` or `repair_pending` whose latest standing attempt failed with an `evidence_fp`:

1. If the evidence is unchanged and the cause is evidence-shaped (`collection_pending`, `late_metric`, `missing_file`) it performs only read-only operations: `hpc sync` (collection) and `reconcile_collected`,
   rate-limited by `hpc_poll_seconds` and capped at `supervisor.recovery.max_syncs` per task.
2. If the evidence changed and `contract_fp` did not, it re-verifies the **same attempt**: the earlier result is appended to `attempt.verification_history`, `attempt.reverifications` increments (cap
   `max_reverifications`, default 5), and the normal `_verify_attempt` runs. A changed contract is never re-verified here: amendments go through interventions.
3. On success the task is `verified`; a plan paused only because of that task (`run_state.paused_for`) resumes; an alert of kind `recovery` is raised.

Audit: `run_state.recovery_log` (last 200 entries: reverify, recovered, sync_unavailable), the `supervisor_recovery` events, and `task.recovery` (cause, root cause, decision, what needs a person).
Never done: relaxing a threshold, changing a scientific check or assumption, raising a budget, submitting a job, launching an agent.

**Root causes** (`recovery.diagnose`, deterministic): `collection_pending`, `late_metric`, `missing_file`, `job_failed`, `numerical` (complete evidence that misses the criterion: "no automatic recovery"), `other`.
**Investigator.** With `supervisor.investigator.auto`, diagnosis also runs for `attention` tasks and for paused plans (via `plan_health().stuck`). Its context includes `recovery.evidence()` (runs with stage,
numeric metrics, artifact paths, log tails). In this automatic mode only `reverify`, `note` and `guide` apply by themselves (`investigator.SAFE_AUTO`); other actions become interventions held for a person.

## 13.3 Plan completion and promotion settlement

`plan_health().state == completed` requires every live task `verified` **and** `autopromote.unresolved(plan) == []`. `unresolved` is empty in manual mode (nothing was promised). In `active` or `suspended` mode it lists
open items (queued, running, paused, with the gate's or reviewer's actual objection in `why`) and verified commits not yet integrated. `settled == not unresolved`. Consequences:

* all tasks verified with a paused item: the run state stays `running`, health is `halted` (blocking row with `promotion: true`), the alert watcher notifies; `plan status` prints the objection;
* a `suspended` authorization (verification policy changed) with unpromoted commits no longer completes the plan (fail closed and visible);
* a run state stored as `completed` by an earlier version with an unresolved promotion is reported `promotion_unresolved` and reopened (`running`, history event `reopened`) on the next tick;
* `autopromote retry` re-queues only the item (its objection is kept in `item.objections`); tasks, attempts and runs are untouched, so no agent restarts and no HPC job is created.

## 13.4 Headless permissions

The default headless Claude allow-list is `providers.claude.DEFAULT_TOOLS` (edits, the `research` CLI, the research MCP server). A profile may replace it (`allowed_tools`) or extend it with exactly named commands
(`allow_commands`, each rendered `Bash(<pattern>)`). `research agent check TASK` runs the task's declared checks (`verification._task_checks(transition=False, log=False)`): read-only, nothing recorded.

When an attempt's structured result lists `permission_denials`, the attempt is `permission_denied` (not `agent_process_failure`, not missing evidence): `verification.skipped` is set, the ledger failure is not
recorded, the task is blocked with the commands and the two remedies, and `task.recovery.cause == permission_denied`. Recovery is a human act (`resolve --unblock`): the failed attempt is unchanged, the next attempt
counts against the normal budget, `max_hpc_resubmissions` and every check are untouched. Before launch, `_permission_conflicts` compares commands in the task's prompt/acceptance (backticks or `$ ` lines) with the profile's
allow-list (Claude only) and records `task.permission_notes`, an event and a brief section.

## 13.5 Git hygiene

`githygiene.py` keeps three places apart: the **main repository** (human-owned), **experiment worktrees** (the supervisor commits verified work on the experiment branch) and the **ledger** (reported only).
All git calls are non-interactive (`GIT_TERMINAL_PROMPT=0`, no editor, pager or credential helper, timeouts).

* `classify(path)` yields `kind` (secret, runtime, generated, output, scaffold, website, plan, figure, report, doc, source, other), `matters` and a reason. Secrets, runtime state and caches are never committable here.
* `preflight(purpose)` runs before `plan run`, `plan resume`, `plan autopromote authorize` and `promote approve`. `git.preflight`: `warn` (default; report and continue), `block` (refuse on warnings; promotion is also refused while
  main has tracked changes), `off`.
* `commit(paths | scaffold, confirm)` is human-only, shows a staged diff built in a throwaway index, needs `confirm`, commits exactly the named files (`git commit --only`) and never pushes.
* `ignore_plan` / `ignore_apply` maintain one managed `.gitignore` block; a pattern that would hide an important file is refused; tracked generated files are reported, never untracked.
* `select_for_commit` picks what verified work puts on the experiment branch: source, tests, reports and small figures in (`commit_keep`, `commit_max_file_mb`), secrets, caches, compiled files, bulky outputs and
  `commit_exclude` paths out; the attempt records `commit_report`. Commits use the supervisor identity without signing.

## 13.6 Scientific content in promotions

A promotion candidate that adds or changes `.qmd` files has a `scientific_content` gate (`not_applicable` otherwise). It applies `guide_quarto.text_issues` to each changed chapter at the candidate head:
identifiers in scientific prose, **stale attribution** (a cited RUN-/EXP-/A-/F- record that does not exist in this project, researcher-only blocks included), executable cells, figure captions. A failed gate blocks
approval and integration. `provenance.validate` additionally reports `stated_value_mismatch` when a claim quotes a number next to the metric it cites that differs from the live value. The identifier validator is unchanged.

## 13.7 Several projects on one machine

Per project: the supervisor process, state, outbox, alert state and promotion records. Per machine: the chat gateway (one process; its cursors and card references are keyed by platform and project root), the bot token
and server, pairing, the approval PIN, and the machine project registry.

* **Labels.** `commands.project_label(root)` is the folder name, or the parent folder for generic names (`main`, `master`, `code`, `src`, `repo`, `project`, `checkout`). `enabled_projects` orders by path and suffixes `-2` on a
  clash, so a label never moves between projects. `canonical_label` maps a key stored by an older version (the bare folder name) to the one project that had it; an ambiguous key is never guessed.
* **Discord.** `research chat setup discord` stores the token once; later projects reuse it and add `project_channels[label] = channel` (`--channel ID`). Threads per task are keyed by label.
* **HPC.** A project's remote directory is `<projects>/<slug>`; `metadata/project.json` carries a `project_id` (hash of name and creation time). `hpc.remote.check_owner` refuses a directory whose id (or, for an older
  directory without one, name) belongs to another project. Collection of one run is serialized by a per-run lock; a second collector finds the run `collected` and imports nothing (an explicit `research hpc collect` forces it).

## 13.8 Promotion coverage

Automatic promotion promotes the verified commits of an experiment branch, whoever made them, and nothing else. Every commit of `base_rev..HEAD` (merges and anything already in main excluded) has exactly one verdict: *included*, *in progress*, *excluded*, *orphan* or *ambiguous* (table in `docs/SUPERVISOR.md` §26). Evidence, strongest first: a human decision; the attempt's recorded commit; a supervisor trailer naming a recorded execution; the attempt's `head_before..head_after`; its started..ended window (ledgers written before heads were recorded). A commit claimed by two tasks, or by none, stops the candidate before it is prepared; a human attributes it to a verified task or excludes it. Attributions are recorded once in the run state with their basis and are idempotent across restarts and retries; attempts are never edited and history is never rewritten. Integration stays fast-forward only and every other gate is unchanged.

A diagnosis requested from chat (Diagnose, `/debug`) is an answer, not an alert: it is delivered regardless of the halt card's cooldown. A paused promotion is diagnosed from its own record without an agent; a failure of the diagnosis is itself reported.

## 13.9 Interventions and human takeover

An intervention is validated as a whole before any action runs: the remaining actions are applied in memory to the complete plan and normalized against the plan schema; `agent.role` is one of planner, implementer, verifier, analyst, human; the attempt budget is bounded by the plan's `max_total_attempts`, not a fixed constant. Applied actions are flagged; `add_task` is idempotent. A failed intervention is recovered with `diagnose`, `edit` (one not-yet-applied action; returns to `proposed`, so a protected change needs the PIN again) and `retry` (under its own approval). Human controls (`research task intervene`) are deterministic wrappers over existing operations; budget changes are recorded interventions; takeover is a flag on the task's run entry that the scheduler honours (phase `takeover`), released through normal verification of the latest attempt with `takeover` provenance on it. Nothing resets or voids an attempt; scientific, frozen-test and resource changes keep requiring human approval.

A halt diagnosis or investigation refused by a usage limit is `waiting_provider` with a resume time on the account's limit record (the one clock the supervisor's task waits use), and re-runs after it; a credit balance is a failure.

