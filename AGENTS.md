# Agent instructions — research-vscode

<!-- research:begin — managed by `research init`; edit outside these markers freely -->
## Research OS

This repo uses Research OS for research memory and task coordination. Every tool below is also available as an
MCP tool if the `research` MCP server is configured (`research mcp install`).

### Running the CLI (no install needed)
Use `.research/bin/research` from the project root (e.g. `.research/bin/research current`). It is a project-local
copy of the CLI, kept up to date by the VS Code extension and by `research bin install`; it needs only Python 3.
`research` works too where it is on PATH (VS Code terminals, or after `pip install`). The examples below write
`research` for short. Don't `pip install` anything just to get the CLI. If `.research/bin/research` is missing, ask
the human to open the project in VS Code with the Research Panel extension, or to run `research bin install`.

### Quick start
1. Get current state: `research current`
2. Get your task: `research task next`, then `research task context T-001` (focused brief: goal, checks, deps, skills)
3. Look things up instead of reading history: `research search "…"`, `research show <ID>`
4. Discover tools: `research --help`

### Core rules
- All runs must belong to an experiment
- Claim before working (`research task start T-001`); leave progress notes as you go (`research task note T-001 "…"`)
- Register artifacts and findings as you work
- After `max_runs_without_synthesis` runs, you must synthesize
- Findings stay preliminary until an independent review; never review your own
- Never rewrite history; supersede findings/decisions instead
- Large outputs stay where produced; just register paths
- Exit code 3 means a policy blocked you: read the hint, don't work around it

### Scientific practice
- Read context in layers (`research current` → `task context` → `show <ID>`) and stop when you have enough. Search prior
  findings and docs before repeating work.
- Keep intent, implementation, observation, interpretation and supported conclusions distinct. Label findings
  preliminary, supported, contradicted or superseded.
- Approved experiment specs and evaluation criteria are binding. Never tune on held-out test results; never loosen a
  check or tolerance just to pass. Quote numbers from run/metric records, not memory.
- Bind every number in a finding to its record (`research evidence bind F-… --run RUN-… --metric NAME`, then `research evidence verify F-…`).
  A mismatch blocks `supported`; correct it by superseding the binding, never by explanation. Verified numbers are not a reviewed interpretation.
- Preserve the user's files, data, notebooks and unsaved work. Project-specific numerical/scientific rules belong in the
  user-authored part of this file, outside the markers.
- Unfamiliar with the science? Read `docs/SCIENTIST_GUIDE.md` (`research skill show scientist-guide`). After synthesizing
  an experiment, refresh it or leave it marked pending: `research guide status`. Hand-offs: `research skill show scientific-handoff`.

### Remote execution (optional; only when `hpc.clusters` is configured)
- `research hpc doctor` first: it says whether SSH/Slurm works and which modes are usable. Never `ssh` by hand —
  the `research hpc` commands enforce the approved paths, resource limits and provenance.
- `research hpc submit EXP-001 --cluster NAME --dry-run`, then `--yes`. Submission is expensive: never submit twice.
- If a submission is reported uncertain, run `research hpc sync` — it adopts a job that is already queued. Do not resubmit.
- No direct execution available, or a human will run it? `research hpc bundle EXP-001` → they run it → `research hpc import <archive>`.
- `research hpc sync` before proposing more runs: it reconciles finished jobs, imports results and lists what now
  needs interpretation. Register the complete outcome (metrics, artifacts, failures) before drawing conclusions.
- Datasets live on the cluster; the filesystem is the source of truth (`research hpc datasets list|check`). Never
  re-download something that verifies, and never assume an undescribed directory is valid.
- Slurm keeps running while the laptop is off; agents do not. Nothing is interpreted until someone runs sync.

### Context hand-off and upgrades
- A complete hand-off (new session, another model, a human): `research report --full --check`. It is a deterministic
  dump of the records — **not** a synthesis. It interprets nothing; don't quote it as if it concluded something.
- Before migrating a project: `research upgrade --dry-run`, and `research agents-md --diff` to review the managed
  block. Everything outside the two marker comments below is the project's — never rewrite it.

### Tiered agents (optional)
If `orchestration.enabled`: Opus = science/synthesis/guide, Sonnet = contracts + tests + review, Haiku = frozen-test
implementation. `research orchestrate route T-001`; protocol: `research skill show engineering-loop`. Never edit frozen tests.

### Recording work
```bash
# Run with provenance
research run exec EXP-001 -- python train.py

# Inside code
import research
research.log_metric("rmse", 0.12)
research.register_artifact("plot.png", description="loss curve")

# Findings
research finding create "..." --supports EXP-001 RUN-001

# Checkpoint when done
research checkpoint create
```

### Task workflow (if using plans)
```bash
research task start T-001          # claim + mark running
# ... do the work, `research task note T-001 "..."` along the way ...
research task complete T-001 --result "summary of what happened"   # runs the task's checks first
```

For skills: `research skill show <name>`. For details: `research show <ID>`.
<!-- research:end -->

## Release rule (project-specific)

Every time a feature is added, bump the version in `pyproject.toml`, `package.json`, `packages/vscode-extension/package.json`
(and the two `package-lock.json` headers) and `packages/core/research/__init__.py`, rename the CHANGELOG "Unreleased" heading
to the new version, rebuild (`npm run package`), and reinstall the VSIX. Without a new version number VS Code cannot tell
the builds apart and can keep the old extension.
