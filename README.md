# Research OS

Project-local research memory for computational science. Research OS connects a VS Code extension,
a `research` CLI, a Python API and an MCP server to the same `.research/` directory.
It preserves what you intended, what was implemented, what experiments showed, what you learned,
and what should happen next.

**Release status:** 1.0.0 release candidate (built locally; not published, tagged or pushed). See the
[verification record](docs/TEST_VERIFICATION_1_0_0.md) for what was and was not tested and the
[engineering review](docs/RELEASE_1_0_0_REVIEW.md) for trust assumptions. The storage schema remains version 3.

Two related lifecycles keep scientific conclusions separate from completed work:

```text
Question → Experiment → Runs → Metrics / Artifacts → Synthesis → Findings → Decisions → Checkpoint
Plan → Tasks → Agent delegation → Implementation → Verification → Completion
```

Research OS is local and provider-independent. Canonical records are text files; SQLite provides
an index. It supports local runs, Remote SSH, SLURM and portable HPC bundles. It does not replace
scientific notebooks, validate scientific truth, supply an execution sandbox, or autonomously
schedule your research. There is no required cloud service.

## Install

The core requires Python 3.8 or newer. Python runtime dependencies are vendored or in the standard
library. Node is needed to build the extension, not to use the Python CLI.

```bash
# From artifacts built from this checkout:
python3 -m pip install dist/research_panel-1.0.0-py3-none-any.whl
code --install-extension dist/research-panel-1.0.0.vsix
```

The VSIX bundles its Python core, so the panel does not require a separate pip installation.
Install the extension on the workspace host when using VS Code Remote SSH. Each initialized
project also gets `.research/bin/research`, a generated machine-local CLI; regenerate it with
`research bin install` after changing machines or moving the project. VS Code terminals receive
a `research` shim on PATH.

To build from a clone:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]' build
npm install
npm test
npm run package
bash scripts/clean_install_test.sh
```

Activate the environment before `npm test`: it invokes `python3` from PATH. Builds go into `dist/`;
nothing is published automatically. [Build and release details](docs/SPEC.md#19-build-packaging-and-release).

## Start a research project

```bash
cd my-project
research init --goal "Measure how step size affects convergence"
research question create "Which step size converges reliably?"
research experiment create "step-size study" -q Q-001 \
  --hypothesis "An intermediate step size converges fastest" \
  --param 'alpha=[0.1,0.5,1.0]' --metric final_error --planned-runs 3 \
  --stop "Three runs, then synthesize"
```

Initialization writes `.research/`, a managed block in `AGENTS.md`, and the project-local CLI.
It preserves text outside the managed block. Default skills and templates live inside `.research/`.

Register an experiment before executing its runs. With your own `sim.py`:

```bash
research run exec EXP-001 --param alpha=0.1 -- python sim.py --alpha 0.1
research run exec EXP-001 --param alpha=0.5 -- python sim.py --alpha 0.5
research run exec EXP-001 --param alpha=1.0 --detach -- python sim.py --alpha 1.0
research run logs RUN-0001 --tail 20
research experiment show EXP-001
```

Inside a registered run, the Python API attaches records to the current run:

```python
import research

research.log_metric("final_error", 0.002)
research.register_artifact("outputs/curve.png", description="Convergence curve")
```

Logs, parameters, command, timing, exit state and available git metadata are recorded. Artifacts
are references, not backup copies. Detached runs survive the initiating CLI; reconciliation reads
the runner's status files. A disappeared process without a recorded outcome becomes `unknown`.

For an executable example, use a **new** destination:

```bash
bash examples/alpha-sweep/run_demo.sh /tmp/my-research-demo
```

The demo refuses an existing destination. It must never erase a research directory to start over.

## Synthesize, decide and resume

After examining actual results, record the interpretation explicitly:

```bash
research experiment synthesize EXP-001 \
  --happened "Describe the completed runs and their measured results" \
  --interpretation "Explain what the evidence supports and what it does not" \
  --next "Describe the next discriminating experiment"
research finding create "A conclusion supported by the observed results" \
  --supports EXP-001 RUN-0001 --confidence medium --limitations "Single seed"
research finding create "A direction that failed" --kind failure --supports RUN-0003
research decision create "The next research decision" --findings F-001
research checkpoint --problem "Remaining uncertainty" --next "Next experiment"
research resume
research current
```

These are templates for your observations, not conclusions to copy without evidence. A synthesis
does not automatically create a finding. Agent findings remain preliminary until reviewed by an
independent actor or role. Failed directions remain discoverable. Superseding records preserves
historical context.

`research evidence` binds numerical claims to recorded metrics or supported calculations.
Numerical consistency is distinct from scientific validity: software cannot establish that the
experiment, metric or interpretation is appropriate. `research guide refresh` prepares a brief
for maintaining the Scientist Guide; it does not run a model or write a verified narrative.

In VS Code, open **Research Home** with `Ctrl/Cmd+Alt+R`. Questions, experiments, findings,
decisions, checkpoints and plans also have sidebar views. Experiment pages expose runs, logs,
metrics and artifacts. The CLI remains available for interfaces without a dedicated UI.

## Plans, tasks and agents

```bash
research plan create "Implement and validate the next experiment"
research task create PLAN-001 "Implement the simulator" --check 'cmd:python -m pytest -q'
research task next
research task context T-001
research task start T-001
research task verify T-001
research task done T-001 --result "Implementation and validation summary"
```

Tasks can carry dependencies, acceptance criteria, file or metric checks, and progress notes.
Dependency cycles introduced by task updates are rejected. Claims are serialized in SQLite;
another actor's claim requires an explicit takeover. Starting a task with unmet dependencies
still warns. Plans do not complete automatically.

Completion checks apply to `task done`, task status updates, `mark` and creating a task already `done`; all
of them run the same verification (`orchestrate.verify_work`): frozen-test integrity, a consistent task
specification, file boundaries, approved hooks and task checks. The result is *passed*, *blocked* (a check failed,
a required hook is unapproved, or the specification conflicts) or *unverified* (nothing deterministic ran). A task with
no verifier can still be completed administratively, and that is recorded as such; it is never reported as verified.
`research task verify` exits non-zero unless the result is *passed*. Passing checks never make a finding
scientifically supported; that needs the separate independent review. Failed checks block agents;
humans receive warnings and an audit event unless `agent_policy.enforce_for_humans` is enabled. Frozen-test integrity
blocks completion independently. Roles are self-declared conventions, not authenticated identities.
Hand-edited files and direct Python internals are outside the CLI policy boundary.

```bash
research dispatch planner --objective "Design the next experiment"
research task dispatch T-001
research dispatch verifier F-001
```

`research task context T-001` (also the MCP `task_context` tool, the RPC call behind the editor, and the start of every
dispatch brief) prints one *effective task specification*: objective, inputs, constraints, permitted files, expected
outputs, acceptance criteria, checks, frozen tests and hooks needing approval, each with its source. If the task record and
its orchestration contract disagree, both values are shown as a conflict and dispatch and completion are blocked until
someone reconciles them; nothing is silently overridden.

Dispatch prepares a brief and, for tasks, a single-use launch command. The active strategy chooses the model by
default; `--profile`/`--agent-model` are explicit overrides (task pin, then strategy, is the order below them). Every
task dispatch path (plain, routed, orchestrated, editor, MCP) applies the same specification, dependency,
frozen-test and preflight checks. Launching the command takes a per-task lock held for the provider's lifetime, so
two processes cannot run the same task; a second launch exits with code 3. Execution records under
`.research/orchestration/executions/` distinguish *prepared*, *launched*, *running*, *completed*, *failed* and
*unknown* (the launcher was interrupted and the provider's fate is unobserved). A launcher killed outright (SIGKILL) cannot update its record, which then stays *running*; the lock, not the record, is the authority on whether the task is still being executed. Preparing or sending a command to a
terminal is not evidence that an agent ran. Missing provider executables, credentials
or model access must be resolved in that provider's environment. No third-party skill is installed
merely by selecting a strategy.

### Model allocation and contracts

```bash
research strategy list
research strategy use sonnet_first
research strategy validate
research orchestrate --help
```

Built-in strategies map task classes to configured model profiles:

| Strategy | Allocation intent |
|---|---|
| `sonnet_first` | Balanced coordinator, fast checked implementation, deep scientific reasoning |
| `opus_first` | Deep planning, balanced coordination, fast checked implementation |
| `haiku_max` | More fast implementation; balanced scientific routing is an explicit strategy choice |
| `sonnet_heavy` | Balanced implementation, fast mechanical work, deep synthesis |

Custom strategies can extend a built-in. Profile model identifiers are configuration, not evidence
of provider availability. Per-dispatch overrides take precedence over task pins, then strategy
routing. Task pins apply to implementer dispatches; verifier routing retains its independence rule.
Without an active strategy, legacy tier configuration remains supported for existing projects.

Contracts persist objective, context, outputs, allowed files, constraints and verification guidance.
Use `research orchestrate contract --help` for fields. `research orchestrate freeze` records acceptance
file hashes; changing an existing hash requires `correct-test` and a reason. Re-freezing does not
silently accept changed tests. `orchestrate verify` checks integrity, file boundaries, task checks
and hooks; missing verification is not success. Retries and escalations are bounded by the chosen
strategy, but Research OS does not supply an unattended scheduler.

Agents should recover context through `AGENTS.md → research current → research task context → search/show`.
Project agents may propose skills and hooks; this does not authorize modifying Research OS itself.

## Configuration, skills and hooks

Configuration precedence is **built-in defaults ← machine file ← project file**. The machine file
is normally `~/.config/research-os/config.yaml`; project overrides live in `.research/config.yaml`.
Sparse files inherit missing values. Use `research config check` for diagnostics. Supported edits
preserve comments; converting legacy flow-style YAML may require a backed-up migration.

The project harness references instructions, skills, focused context and checks. Harness `checks`
are guidance in briefs; executable automatic checks belong in task checks or approved hooks.

- **Skills** are instructions and supporting files, imported into `.research/skills/` with provenance.
  Use `research skills list`, `show`, `add`, `update` and `link`. Removed/replaced skills are backed up
  in `.research/cache/removed-skills/`; user-owned agent-directory entries are preserved.
- **Tools** are callable CLI, RPC or MCP operations. MCP covers a subset of the CLI.
- **Hooks** are shell commands at `preflight`, `post_implementation`, `pre_experiment` or
  `post_experiment`. They require explicit human approval.

```bash
research hook add tests --stage post_implementation --command 'python -m pytest -q'
research hook list
research hook approve tests
research hook run --stage post_implementation
```

Approval binds the command, stage and effective timeout. Relevant changes invalidate it. An enabled,
required, unapproved hook blocks its gate; disabled hooks are skipped. Advisory failures remain
visible without blocking an otherwise successful verifier. A skipped hook never counts as a pass.
`post_experiment` runs through `research experiment check`, not automatically when a run exits.
Hook and check commands execute with the user's filesystem permissions. Approve only trusted code.

## MCP and HPC

```bash
research mcp install
research mcp serve
```

The stdio MCP server exposes registered tools with schemas and structured errors. It does not expose
every feature: strategy, orchestration, hooks, evidence, tuning and guide workflows use CLI/RPC.
Re-run installation after moving a project if a client registration contains its old absolute path.
See [MCP details](docs/SPEC.md#78-mcp-server) and `research mcp --help`.

For SLURM on the workspace host, use `research run submit --help`. Remote HPC profiles specify SSH
aliases, approved storage roots and resources:

```bash
research hpc config --example
research hpc doctor
research hpc --help
```

Configure and inspect a profile before submission. SSH credentials stay in OpenSSH configuration.
Compute-node jobs write per-run files, never the project SQLite index. Portable bundles support
separate execution and result import; remote sync reconciles outcomes. Offline tests simulate
clusters and execute portable bundles locally; they do not establish compatibility with every
site's SLURM accounting, filesystem or scheduler policy. [HPC specification](docs/SPEC.md#14c-hpc-execution).

## Recovery and upgrades

Back up the complete `.research/` directory and referenced artifacts. Run logs are gitignored by
default, so committing metadata alone does not back up logs or external datasets.

```bash
research upgrade --dry-run
research upgrade --apply
research config check
research rebuild
```

Every mutation runs under a project write lock (`.research/.write.lock`) with an undo journal
(`.research/.transaction.json`). If a process dies mid-operation, the next open rolls the canonical files back and
rebuilds the index, so a half-finished operation is discarded as a whole; an unreadable or unsafe journal is preserved and
reported instead. This protects against process crashes and concurrent supported writers. It does not protect against
hand edits made during a write, filesystems whose `fsync`/`flock` are unreliable (some network mounts), or power loss on
hardware that acknowledges writes it has not persisted.

Rebuild backs up a readable index, imports canonical records transactionally and preserves canonical
file bytes. Duplicate IDs and malformed canonical records stop recovery rather than silently dropping
records. Resolve duplicate IDs **and their references**; renaming a filename alone does not change a
front-matter ID. Incomplete JSONL tails are not ingested and block further appends until repaired.

For an unreadable/corrupt index, stop clients and back up `.research/` first. Move `research.db` to a
safe backup location, then reopen the project to reconstruct it. Do not discard canonical files or
SQLite journal files blindly. The current `rebuild` CLI still requires an index it can open.

| Symptom | Next action |
|---|---|
| CLI missing | Use `.research/bin/research`, activate the installed environment, or `research bin install` |
| Python fails to start in VS Code | Check Output → Research and `research.pythonPath` |
| Run budget blocks an agent | Review failures and synthesize before launching more runs |
| Required hook blocks work | Inspect its exact command, then have a human approve or deliberately disable it |
| Malformed Markdown or JSONL | Preserve the file, repair the reported record, then retry |
| Task ledger is corrupt | Restore/repair it; do not reset frozen tests or attempt history |
| Index locked | Stop competing clients and retry; do not delete an active database |
| Newer schema refused | Upgrade the tool; do not lower the stored schema version |

[Upgrade details](docs/UPGRADING.md) · [Technical specification](docs/SPEC.md) ·
[Release review and verification](docs/RELEASE_1_0_0_REVIEW.md) · [Changelog](CHANGELOG.md).

## Development and verification

`npm test` runs Python tests, TypeScript checking and Node RPC-client tests. `npm run build` bundles
the extension. `npm run package` produces the VSIX, wheel and (when a build frontend is available)
source distribution. `bash scripts/clean_install_test.sh` installs the wheel offline in a fresh
environment and verifies relocation/rebuild without the developer's PYTHONPATH or machine config.

`RESEARCH_TEST_WORKSPACE=<demo> npm run test:ext` installs the VSIX into an isolated VS Code profile
and tests a copied workspace. Set `RESEARCH_TEST_VSCODE_EXECUTABLE` to a compatible installed VS Code
executable to avoid downloading it. `scripts/e2e_code_server.py` requires code-server, Playwright and
a running browser-accessible workspace; it is separate from the extension-host harness.

Authoritative source lives under `packages/core`, `packages/cli` and `packages/vscode-extension/src`
(with webview assets in `media`). Bundled Python, extension `dist`, codicons, test downloads and root
`dist/` are generated. Historical design proposals in `docs/` are labelled as such; they are not
implemented-capability specifications.
