# Research OS v2 — end-to-end integration test report

**Date:** 2026-11-02 · **Build under test:** `development` @ 6171030 (2.0.1) · **VS Code:** 1.141.0 (Electron, real UI) ·
**Cluster:** `plp-drcc-login2` (Slurm 21.08, partition `defq`) · **Agents:** Claude Code 2.1.316 (haiku-5-5 implementer, sonnet-5-5 verifier)

The test was run as a first-time researcher: a fresh empty folder, the extension's own wizard, UI wherever a UI exists, the CLI only where
the UI has no equivalent (each case is listed under *Manual / CLI steps*). Every screenshot in this report is a capture of the real
VS Code window driven through Playwright/Electron; none is a mock-up. Bugs were **logged first** (`examples/e2e-diffusion/evidence/observation-log.md`)
and not fixed during the test.

> **Caveats stated up front.** (1) The test operator and the "researcher" are the same entity, so the human review of F-001 was not
> truly independent (recorded in the review text). (2) VS Code's own first-run sign-in/theme dialogs were dismissed by the harness.
> (3) Native OS dialogs (the *Freeze test files…* file picker) cannot be driven by the harness, so that button was **not** exercised live.
> (4) Three bugs found in an earlier session (HPC bundle path in shared-ledger projects, shell-quoting of submitted commands, headless-agent
> permissions) were already fixed in the build under test; they are not re-reported here.

## 1. The experiment

2D diffusion `u_t = D(u_xx+u_yy)` on a 64×64 periodic grid (explicit FTCS, NumPy only), D ∈ {0.05, 0.10, 0.20}, one Slurm job per D, then one
comparison figure. Code, configs, final plot and evidence: [`examples/e2e-diffusion/`](../examples/e2e-diffusion/).

| D | Slurm job | measured growth rate dσ²/dt | 4D | rel. error |
|---|---|---|---|---|
| 0.05 | 203887 (RUN-0010) | 0.199988 | 0.20 | 0.006 % |
| 0.10 | 203888 (RUN-0011) | 0.399976 | 0.40 | 0.006 % |
| 0.20 | 203889 (RUN-0012) | 0.799952 | 0.80 | 0.006 % |

![final plot](screens/e2e/64-final-plot.png)

*Scientific honesty note:* the solver uses `dt = 0.2/(4D)`, so `D·dt` is constant and the three final fields are identical; only the time axis
differs. Panel 2 of the figure is therefore uninformative and the dashed theory lines in panel 1 overlap the data. This is a flaw of my
experiment design (and of the agent-written plot), not of Research OS; the finding was scoped to the growth rate only.

## 2. Step-by-step walkthrough (real screenshots)

### 2.1 Initialize (wizard)
Command palette → *Research: Initialize Project* (an empty folder shows **no** Research entry anywhere else; see UX O-list).
![palette](screens/e2e/02-command-palette.png)
![learn](screens/e2e/03-init-learn.png)
Project name + goal, then Folders / Agents steps, then **Execution → Set up an HPC cluster**:
![project](screens/e2e/04-init-project.png)
![hpc](screens/e2e/08-init-hpc.png)
*Test connection (read-only)* really connected to the cluster and checked sbatch/squeue/sacct/scancel/rsync/python:
![hpc tested](screens/e2e/09-init-hpc-tested.png)
Review lists every change before anything is written:
![review](screens/e2e/10-init-review.png)
Result: `main/` (git, 2 commits), `ledger/` (worktree on `research/ledger`), `experiments/`, `diffusion-study.code-workspace`, Control Center opened automatically.
![after create](screens/e2e/11-after-create.png)

### 2.2 Settings
![settings](screens/e2e/12-settings.png)
![settings hpc](screens/e2e/16-settings-hpc.png)
The HPC page shows `backends.slurm.*` (jobs submitted *from this machine*), not the cluster profile the wizard just wrote.

### 2.3 Experiment, plan, tasks
New experiment dialog (worktree on by default), review, created (question + experiment + task + branch + worktree in one step):
![new exp](screens/e2e/17-new-experiment.png)
![review](screens/e2e/18-new-experiment-review.png)
![created](screens/e2e/19-experiment-created.png)
The auto-created task only contains the hypothesis as its goal; it was completed through the full Edit-task form:
![task](screens/e2e/21-task-detail.png)
![edit](screens/e2e/23-edit-task-form.png)

### 2.4 Dispatch (does it launch agents or only prepare?)
**Verified: Dispatch only prepares.** After the Agent/Mode pickers the execution is `prepared` and `pgrep` shows **0** agent processes until *Start*.
First, the frozen-tests gate blocked the very first dispatch:
![gate](screens/e2e/28-dispatch-result.png)
After a logged human override the prepared toast offers Preview/Start:
![prepared](screens/e2e/30-dispatch-prepared.png)
Preview opens the brief (raw Markdown source):
![brief](screens/e2e/31-brief-preview.png)
Start launches a **real** headless Claude process (pid shown), tracked as an execution:
![starting](screens/e2e/32-agent-starting.png)
![agents](screens/e2e/33-agents-tab-running.png)
First agent run ended `blocked` (no NumPy for the agent's `python3`); the task page says only "environment: needs a human":
![blocked](screens/e2e/34-task-blocked.png)
After Unblock and a human-frozen test, dispatch passes the gate:
![unblocked](screens/e2e/35-unblocked.png)
![frozen ok](screens/e2e/36-dispatch-frozen-ok.png)

### 2.5 Execute on HPC (CLI — there is no UI for this)
Work tab with jobs, Slurm IDs and states (the only HPC *monitoring* surface):
![work jobs](screens/e2e/45-work-with-hpc-jobs.png)
![work after agent](screens/e2e/38-work-after-agent.png)
Evidence (full files in `examples/e2e-diffusion/evidence/`):

```
$ research hpc submit EXP-001 --yes -- python3 diffuse.py --D 0.05 --out outputs/D0.05
✓ RUN-0010 submitted to plp-drcc-login2 as slurm job 203887
  reserved  1 CPU · no GPU · 1G memory · up to 00:05:00 wall-time
$ sacct (12 jobs, see sacct-all-12-jobs.txt)
203887  EXP-001-RUN-0010  COMPLETED 0:0  2026-11-02T16:46:40  00:00:01
203888  EXP-001-RUN-0011  COMPLETED 0:0  2026-11-02T16:46:50  00:00:01
203889  EXP-001-RUN-0012  COMPLETED 0:0  2026-11-02T16:47:00  00:00:01
$ research hpc sync
  ↓ RUN-0010 results imported  5 new metric entries, 2 artifact(s)
```
Resource use: 12 one-second jobs × 1 CPU × 1 GB (+ ≈10 s of client upload/submit each).
It took **four rounds** to get a usable round trip: 3 jobs failed (no NumPy on compute nodes), 3 completed but brought home nothing
(outputs not registered), 3 brought home metrics only, the 4th round brought metrics + files home (see UX O17, O21, O26).

### 2.6 Analyze
`research run exec EXP-001 -- python plot_compare.py …` (RUN-0016, recorded run) → figure above. Experiment page with history and results:
![experiment](screens/e2e/66-experiment-detail.png)
The experiment worktree (code that actually ran) is **not** in the Explorer by default; I added it to the `.code-workspace` by hand (O31):
![explorer](screens/e2e/63-experiment-code-explorer.png)

### 2.7 Review, then integrate into `main`
Finding F-001 (preliminary) → *Ask agent to review* (real Sonnet verifier started in a terminal; no execution record, raw JSON output; its verdict was never written to the ledger):
![finding](screens/e2e/47-finding-detail.png)
![verifier](screens/e2e/50-review-agent-started.png)
Human review form:
![human review](screens/e2e/53-human-review-dialog.png)
Promotion candidate (diff of exactly what main would receive, four gates):
![promo](screens/e2e/57-promotion-open.png)
*Run tests* dead-ends:
![tests](screens/e2e/58-promotion-run-tests.png)
All gates satisfied, then the merge needs an explicit "I have read the patch" confirmation:
![approved](screens/e2e/61-approved.png)
**Provenance check (CLI):** `main` now holds `07ec9b8` whose message ends with `(cherry picked from commit 49e0d3f…)`, the original commit stays on
`exp/EXP-001-…`, `promote show PROMO-001` records tests/evidence/review/approval, and `refs/research/snapshots/RUN-0001…0016` pin the code of every run.
Fast-forward only, no history rewritten.

## 3. Feature-by-feature result

| # | Feature | Result | Notes |
|---|---|---|---|
| 1 | Init wizard (git, worktrees, ledger, config, workspace file, MCP, AGENTS.md) | **PASS** | Clear, reviewable, ~2 min; creates exactly the promised layout |
| 2 | HPC profile in wizard + read-only connection test | **PASS** | Real connection checks; but cluster is "not ready" until CLI `hpc init` (O14) and job environment is not asked (O17) |
| 3 | Settings UI | **PARTIAL** | Good provenance display; cluster profile and test command not editable by form (O3, O34) |
| 4 | New experiment (question+experiment+task+worktree) | **PASS** | Worktree on by default; no fields for method/params/metrics (O4) |
| 5 | Plan/task creation & editing in UI | **PARTIAL** | Works, but *Add task* does not inherit experiment/type/role (O23) |
| 6 | Dispatch pickers, Preview, Start | **PARTIAL** | Prepare-vs-start is honest and correct; Preview kills the flow (O7), toast vanishes (O12) |
| 7 | Dispatch gates (frozen tests, worktree) | **PARTIAL** | Gate is right but first-time path is a dead end (O5); *Freeze test files…* not verifiable here |
| 8 | Real agent launch/tracking (headless) | **PASS** | Real process, pid, state machine, exit code, cost in logs; "completed ≠ verified" respected |
| 9 | Agent outcome visibility | **FAIL** | Blocked/partial explanations live only in execution logs (O11, O24, O33) |
| 10 | HPC submit (CLI) | **PASS** | Upload verified, immutable code snapshot per run, job IDs, token, receipts |
| 11 | HPC UI | **FAIL** | No submit/init/status UI; "SLURM" run mode uses the *local* backend (O15) |
| 12 | HPC monitoring/collection | **PARTIAL** | Status/sync/collect work and Work-tab Jobs list is good; failed-job stderr not retrieved (O18); outputs only come home if registered via an undocumented contract (O21, O26) |
| 13 | Run/experiment history & provenance | **PASS** | Runs, snapshots, synthesis, findings, branch + cherry-pick trailer |
| 14 | Independent review of findings | **PARTIAL** | Human review works; agent reviewer's verdict not recorded, untracked terminal run (O30, O33) |
| 15 | Promotion to `main` (gates, diff, approvals, FF merge) | **PASS** | Best-designed surface; minor: stale dialog (O35), no test-command UI (O34) |
| 16 | Policy gates (unsynthesized / failed-run limits) | **PARTIAL** | Enforced correctly, but fire on local analysis runs and give no command to proceed (O20, O27) |
| 17 | Experiment code visible in the editor | **FAIL** | Worktrees not in the generated workspace (O31, O32) |

## 4. What Research OS automated vs. what I did by hand

Automated by Research OS: project/worktree/branch/ledger creation, run records and code snapshots, brief generation, agent launch & tracking, Slurm
bundle/upload/submit/status/collect, metric import, provenance (snapshot refs, cherry-pick trailer), promotion gates and merge.

Manual / outside the intended UI (all recorded in the observation log):
- Created T-002/T-003 partly via CLI (T-003) and linked experiment/type/role by editing the task (O23).
- Froze the agent-written test by CLI (native file picker not drivable) and rewrote checks to an absolute venv python (O9).
- Provided NumPy: a project-local `python3` shim for agents, and `~/research-os-e2e/pylib` + `slurm.setup` in the cluster profile (O9, O17).
- `research hpc init`, all `hpc submit/sync` calls (O14, O15), `run update --reviewed` ×5, two `experiment synthesize` (policy gates, O20/O27).
- `promote verify --command …` (O34).
- Edited the `.code-workspace` to show the experiment folder (O31); copied collected outputs into `D<value>` folders for the plot script (O28).
- Two failed local runs (RUN-0014/15) were my own shell mistakes; one (RUN-0013) was agent code that could not find D.
- I declined to bypass the "failed runs without review" gate with `--as human` (it is an agent safety policy); I reviewed the failures instead.

## 5. UX evaluation — **5.5 / 10**

*Why not lower:* the mental model is coherent, the wizard and the promotion surface are genuinely good, honesty about state (prepared / running / completed
≠ verified) is excellent, and provenance is real. *Why not higher:* a first-time user cannot complete the headline loop (ask → plan → dispatch → HPC → results
→ plot → promote) without a terminal, undocumented contracts, and three or four recovery steps; several failures are silent or explained only in log files.

### Friction points (IDs refer to the observation log)
1. **First dispatch is a dead end (O5, O7, O8, O12).** Brand-new implementer task → "no frozen acceptance tests; coordinator (balanced)… before fast implements". No action to have the coordinator write them. Preview ends the flow; the Start toast disappears in seconds.
2. **Agent environment (O9, O25).** No project-level interpreter/venv setting; headless agents cannot run Python so they write code unrun; the task ends `blocked: environment: needs a human` with no instructions.
3. **Outcomes not surfaced (O11, O13, O24, O33).** What the agent said/decided is only in `.out` files; task pages show stale banners.
4. **HPC is CLI-only and unforgiving (O14–O19, O21, O22, O26).** No UI to init/submit; job environment never requested; failed-job stderr not fetched; collection contract (`$RESEARCH_RUN_DIR/metrics.jsonl`, `artifacts.jsonl`) documented only inside the remote bundle README, not in the agent brief, Quickstart or UI.
5. **Experiment code invisible (O31, O32).**
6. **Policy gates without exits (O20, O27, O34).** Messages say what is wrong but not the command/button to proceed; local analysis counts as an experimental run.
7. **Smaller:** settings HPC page shows the wrong layer (O3); New-experiment dialog lacks parameters/metrics (O4); *Add task* loses context (O23); dialogs don't refresh after external changes (O35); brief preview is raw Markdown (O6); `hpc status` with no id errors (O16).

### Recommended improvements (highest value first)
1. Make the first dispatch work: a "Dispatch test author first" action (or let the coordinator profile do both), merge Preview/Start into one toast with a persistent Start action, and never lose a prepared execution.
2. Surface every agent outcome on the task/finding page (final report, blocker reason with a suggested action).
3. HPC in the UI: init, submit (Run… → "On cluster"), job list with log tail, "fetch stderr", and a setup step for the job environment (modules/venv) in the wizard.
4. Tell code authors the result contract: put it in every implementer brief and `AGENTS.md`, and register every file under `$RESEARCH_RUN_DIR/outputs` automatically.
5. Show experiment worktrees in the workspace automatically and add *Open in editor* to worktree rows.
6. Give each policy gate an exit (button/command) and don't count analysis runs.

## 6. Final verdict

**Research OS v2 is not yet ready for daily research use by someone who is not already fluent in it, but it is close for a power user who accepts a terminal.**
The architecture is sound and trustworthy where it matters most — provenance, honest run states, gated promotion — and the real round trip
(agents wrote the code, Slurm ran it, results came home, a plot was produced, the code was promoted to `main` with provenance intact) did work. Daily use
is currently blocked by: the first-dispatch dead end, CLI-only HPC with an undocumented result contract, silent environment failures, and agent
outcomes hidden in logs. None of these is a design flaw; each has a small, concrete fix (section 5).

## Appendix — evidence index
`examples/e2e-diffusion/` — `code/` (solver, test, plot), `config/` (ledger config with cluster profile, workspace file), `results/` (figure PNG/PDF,
metrics of RUN-0010..12), `evidence/` (submit logs, `sacct` for all 12 jobs, sync output, full observation log).
Agent cost (tracked executions only): ≈ $0.08 for four haiku sessions; the Sonnet verifier ran untracked (O30), so its cost is unknown.
