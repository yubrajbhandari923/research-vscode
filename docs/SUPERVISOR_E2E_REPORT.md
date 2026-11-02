# Supervisor end-to-end test report

Real agents (Claude Code 2.1.316, `claude-sonnet-5-5`), a real Slurm cluster (`plp-drcc-login2`, partition `defq`), the real VS Code
extension (VS Code 1.141, driven by Playwright/Electron). All screenshots are captures of that window. Evidence files:
[`examples/supervisor-e2e/`](../examples/supervisor-e2e/) (plans, solver, figure, Slurm `sacct`, status timelines).
How the system works: [SUPERVISOR.md](SUPERVISOR.md).

**Read this first.** The headline run succeeded, but it did *not* succeed on the first try: the real agents exposed four defects in my
own supervisor (listed in §3). I fixed them and continued; the final state is real, the path to it was not clean. Where I had to
intervene by hand, it is stated.

## 1. What was run

Fresh project created with the Initialize backend (`research project-init … --apply`: `main/`, `ledger/`, `experiments/`, cluster profile
with a job-environment line). A scientific context file (`RESEARCH.md`), then three YAML plans imported **through the VS Code UI**
(open YAML → *Apply Plan YAML from Editor…* → reviewed preview → Apply):

| Plan | Purpose | Result |
|---|---|---|
| `diffusion` (3 tasks) | Implement a 2D diffusion solver, run D = 0.05/0.10/0.20 on Slurm, compare and plot | 3/3 verified |
| `repair-demo` (1 task) | Fix a seeded bug in `smooth.py` by reading | 1/1 verified on attempt 1 (no repair needed) |
| `repair-hidden` (1 task) | Spec is ambiguous; a held-out oracle the agent cannot see | attempt 1 failed verification → repair → verified |

![YAML preview](screens/supervisor/03-yaml-preview-dialog.png)
![tasks before run](screens/supervisor/06-plan-tasks-before-run.png)

Pressing **Run plan** in the Plans tab started the supervisor process and the first agent:
![run started](screens/supervisor/07-run-started.png)

## 2. Results

Diffusion plan (final):

| Task | Attempts | Evidence |
|---|---|---|
| T-001 implement | 3 (2 voided, see §3) | `test_diffuse.py` passes under supervisor verification (3/3 checks) |
| T-002 HPC | 1 | Slurm jobs **203905, 203906, 203907** COMPLETED on node004 (`evidence/sacct.txt`); runs RUN-0004..6 linked to the attempt; metric check `variance_growth_rate>=0.1` passed on the *collected* metrics |
| T-003 analyse | 1 | `outputs/figure/diffusion_comparison.png` produced via `research run exec`; file check passed |

![final figure](../examples/supervisor-e2e/diffusion_comparison.png)
![all plans complete](screens/supervisor/18-plans-all-complete.png)

Measured growth rates agree with 4D (the T-001 test asserts within 1%; the figure shows the dashed 4D line on the data).
Cost of every agent session in the project including failed ones: **about US$2.4** (list price as reported by the CLI). Wall time of
the diffusion plan, excluding my debugging pauses: roughly 5 minutes of agent time.

Repair (`repair-hidden`): attempt 1 failed the held-out check, and the UI shows exactly what the next agent receives:
![attempt 1 failed](screens/supervisor/19-attempt1-failed-verification.png)

The repair attempts then used **native session resume** (`claude -p --resume <id>`; the session id was read from the agent's JSON output,
and the installed CLI's `--help` was probed first). **Honest caveat:** attempts 2 and 3 were killed by a *provider usage-limit error (429)*
at the end of their work; the agent had already written the corrected `smooth3.py` (reflection padding) before that. I voided those two
attempts from the UI (they stay in the ledger) and attempt 4 resumed the same session; the agent reported the file was already correct
and the supervisor's verification passed. So the failure detection, diagnostics hand-off, native resume and re-verification are real; the
code fix itself happened inside a voided attempt.
![attempt 4](screens/supervisor/20-attempt4-repair-native-resume.png)

Plan-change proposal approved in the UI (applies through the same engine, revision 1 → 2). The patch was **injected by me** to
exercise the flow; no agent proposed it:
![proposal](screens/supervisor/22-proposal-awaiting-approval.png)
![applied](screens/supervisor/23-proposal-applied.png)

Settings (typed registry, help text, origin, defaults):
![settings](screens/supervisor/24-settings-supervisor.png)

## 3. Defects found by the real runs (all fixed, each with a regression test)

1. **Supervisor held the task claim, so every agent's mandatory `research task start` failed** and the agent correctly stopped.
   Attempts 1–2 were wasted. Fix: the supervisor's ledger is the reservation; the task claim stays free. (Mock tests could not show this.)
   ![blocked by defect](screens/supervisor/08-blocked-by-defect.png)
2. **Frozen tests frozen inside a worktree looked "modified" from the project root**, blocking verification of good work. Fix:
   frozen paths resolve in the task's workspace.
3. **The daemon, started as `python -m research.supervisor`, held a second copy of the module**, so the supervisor's completion grant was
   invisible to the guard and every tick raised. Fix: `research.supervisord` entry module + a detached-daemon regression test.
   The T-002 attempt of the diffusion plan was launched by one **manual `tick` from a debug script** while the daemon was failing;
   T-003 and everything afterwards ran from the repaired daemon.
4. **A provider usage limit consumed repair attempts.** Now: the attempt is kept as evidence, not counted, the task goes to *attention*
   and nothing is retried blindly.

Also fixed along the way: UI table overflow from very long reasons; missing **Re-verify** and **Void this attempt** actions.

![t001 verified, t002 running](screens/supervisor/12-t001-verified-t002-running.png)

## 4. Status matrix

| Area | Status | Evidence / note |
|---|---|---|
| YAML schema, validate/diff/apply/export, idempotence, round trip | PASS | unit tests; applied via UI |
| Retire unstarted, protect done/running, stale revision, partial scope | PASS | unit tests; not exercised in the real runs |
| Interrupted apply recovers | PASS (observed) | a CLI apply stopped by a policy gate resumed in the UI without duplicates |
| Dependency ordering, parallel cap, one active attempt per task/workspace | PASS | tests + real run (sequential) |
| Parallel agents > 1 | NOT TESTED | only logic-tested with mocks |
| Verification only by supervisor; bypass via CLI/update/file edit | PASS | tests (CLI update, `complete_task`, hand-edited mirror) |
| Same bypass via MCP/RPC | PARTIAL | same core function; not separately attacked |
| Agent reports validated against active execution | PASS | tests; used by real agents (progress/artifact/report/finish/problem) |
| Daemon survives UI close, restart reconciles | PASS | daemon outlived VS Code restarts during the run |
| Unknown execution state never retried | PASS | test only |
| Claude dispatch in the experiment worktree | PASS | real run; cwd check |
| Codex dispatch / resume | NOT TESTED | probed `codex exec resume`; no run; session id not parsed (hand-off fallback) |
| Native Claude session resume | PASS | real (see §2) |
| Context hand-off | PARTIAL | used (attempt 3 `session: handoff`); not compared against resume |
| Bounded repair with diagnostics | PASS (real) | caveat in §2 |
| Provider-limit handling | PASS | found live, fixed, unit-tested; recovery after reset verified live |
| Scientific change needs approval; operational auto-applies | PASS | unit tests; approval live (injected patch) |
| Agent-proposed change end to end | NOT TESTED | no real agent proposed anything |
| Real Slurm submit by agent, wait, collect, verify | PASS | jobs 203905–7 |
| Mock Slurm state transitions / failed job / unknown job handling | PARTIAL | existing HPC tests; supervisor path for a *failed* job only reasoned, not run |
| Duplicate-submission protection | PARTIAL | by design (supervisor never submits; waits for non-terminal runs); no adversarial test |
| Promotion candidate from supervised work | NOT TESTED | the Plans page shows worktree/commit state and links to the existing promotion flow; no agent commits happened |
| VS Code: import/preview/apply, run/pause/resume/stop, status refresh, details, approvals, settings | PASS (live) | screenshots; pause/resume/stop only unit/RPC-tested |
| Security: sandbox enforcement | NOT IMPLEMENTED | restrictions are advisory unless a sandbox profile is used (SUPERVISOR.md §11) |

## 5. UX assessment (first-time researcher)  **6 / 10**

Good: one place (Plans) shows what is waiting, running, verifying, blocked; every attempt's exact prompt, verification diagnostics and
agent reports are one click away; actions (Re-verify, Retry fresh, Void) match real situations; YAML preview before apply is clear.

Friction:
* The plan author must know the environment: my checks used an absolute venv Python path (no project interpreter setting).
* The task table shows "agent running — process completed (not confirmed)" for a moment after exit; confusing.
* The *Attempts* column counts voided attempts while the budget does not.
* Only one plan's task list is open at a time, and the supervisor process has a tiny *start/stop* text link.
* Native file dialogs and VS Code's own sign-in/theme dialogs interrupt scripted use (a harness limit, not a product defect).
* Failure reasons are long strings; the table now truncates and Details has the rest.
* An agent that cannot run Python (headless allowlist) writes untested code unless the plan tells it to use `research run exec`.
* Humans are identified by self-declaration, so "human-only" is not authentication.

## 6. Verdict

**Experimental use only.** The supervisor core, YAML round trip, verification gates, repair, native resume and the Slurm round trip
work against real systems, and four real defects were found and fixed along the way, which is precisely why it is not yet "daily use":
it has had a handful of real runs, one provider, one cluster, sequential tasks, no failed-HPC-job run and no agent-originated proposal.
Implementation is broad and tested (17 supervisor tests, webview tests); integration confidence is limited to what is in §2 and §4.
