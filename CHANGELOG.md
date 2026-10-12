# Changelog

## 0.7.0 — 2026-10-12

- **Executable harness hooks** (`harness.hooks`, `research hook …`): preflight, post-implementation, pre-experiment and
  post-experiment commands with timeouts and a results ledger. A hook runs only after a human approves its exact command
  (agents get exit 3; editing revokes approval). `pre_experiment` failures block run creation.
- **Structured task contracts** (`orchestrate contract`, flags on `delegate`) and **`orchestrate verify`**: frozen tests, file
  boundary, approved hooks and task checks, with diagnostics returned to the worker and bounded next steps via `--record`.
  No verifier running is never a pass.
- **Automatic usage accounting**: `--headless` dispatch records provider-reported tokens/cost to `.research/usage.jsonl`;
  `research usage --by strategy|model|task|level`; manual `--usage` kept. Missing values are never zero.
- **Per-task model overrides** (`orchestrate pin`), applied in dispatch and recorded in provenance.
- **Strategy-aware agent files**: `research-<level>.md` generated from the active strategy; managed legacy tier files retired
  to the cache; user-owned files untouched.
- **`research experiment check`**: deterministic validation separated from interpretation; can prepare a synthesis session.
- **Agent Setup page** gains work in flight (overrides, contracts, verification), hooks (approve / run / enable), experiment
  validation and usage by strategy.
- No migration required: all new config keys are optional and default to off/empty.

## 0.6.0 — 2026-10-12

### Named model-allocation strategies (opt-in; legacy projects unchanged)

- **`research strategy list|show|use|validate|route`** and `orchestration.active_strategy`. Four built-ins —
  `sonnet_first`, `opus_first`, `haiku_max`, `sonnet_heavy` — map logical levels (fast/balanced/deep) to
  `agents.profiles`, route tasks deterministically, and define retry limits and escalation. Custom strategies use
  `extends:` and per-key overrides through the existing default ← machine ← project config layers.
- **One resolver** (`strategy.select`) for `research dispatch`, `orchestrate route|dispatch|delegate` and
  `guide refresh`; `--strategy/--level/--profile/--agent-model` override one dispatch. Missing profiles or unavailable
  models fail with a hint instead of substituting another model.
- **Safeguards keyed to roles, not model names**: frozen tests are required whenever the implementer is not the
  test-authoring coordinator level; implementers cannot correct their own frozen tests; verifier dispatch avoids the
  implementer's profile+model and records `independent: false` when it cannot.
- **Provenance**: each dispatch snapshots the resolved policy (`*.policy.json`, task ledger `dispatches`, env vars);
  switching strategies logs an event and rewrites nothing. `orchestrate attempt --usage` records only real usage.
- **`harness:` config** carries project-owned skills, context, an instructions file and verification commands into
  briefs, independent of the strategy. References only; Research OS runs and interprets none of it.
- **Agent setup page** (VS Code: *Research: Open Agent Setup*, or Agent → Agent setup): the active strategy with its
  level → model table and escalation limits, one-click switching, the project harness (missing references flagged),
  skills (import / always-on / update / remove), MCP server registration per client, and managed agent files.
- Fix: replacing a list-valued key (`harness.skills`) in `config.yaml` left the old `- item` lines behind and corrupted
  the file; the surgical editor now replaces the whole block and no longer swallows trailing blank lines.

## 0.5.0 — 2026-10-12

### Layered, sparse, comment-preserving configuration

- **`.research/config.yaml` is now sparse**: it holds only what a project *changes*. The inherited defaults
  follow as a commented reference shortlist, so what is tunable stays discoverable. A real project went from
  89 keys / 106 lines to 11 live lines. Effective settings are unchanged — `load_config` still returns the
  full merged dict, so no consumer, extension view or agent workflow needed touching.
- **Three layers**: built-in defaults ← optional machine file (`~/.config/research-os/config.yaml`, or
  `$RESEARCH_CONFIG`) ← the project file ← per-command flags. Define a cluster once per machine and each
  project only names its own remote slug. The machine layer is opt-in and ignores `project` identity.
- **Edits are surgical.** Writing a key rewrites that key's line and leaves every other byte alone —
  comments, ordering, blank lines, CRLF. `research project set --goal …`, the VS Code settings path and
  `setup agents` all preserve a hand-annotated file now.
- **Block style everywhere.** `yamlio.dump(block=True)` for anything a human edits. The old flow style
  (`project: {name: x}`) turned an inline comment into an **unparseable file**, after which every command
  died with a raw `ParserError` traceback; that is fixed at the source, and a parse failure now explains
  itself and points at `research config check`.
- **New read-only commands**: `research config show [--effective|--sources]`, `research config check`,
  `research config migrate [--apply]`. `--sources` reports which layer supplies each value; `check`
  validates types, HPC profile structure (against the *effective* profile), duplicate keys, and unknown
  keys — which are reported but **never discarded**. `migrate` shows a diff and refuses to write unless the
  effective settings are provably identical.
- **The commented-reference trap is detected.** Editing `#   max_runs_without_synthesis: 2` without
  uncommenting it does nothing; `config check` now names the line, the value in force, and the fix.
- Project upgrades no longer "add missing defaults" (a sparse file needs none). Instead they offer to
  reformat a legacy flow-style file once, refusing if the effective settings would change.

### Extension UI for the 0.3–0.5 features

- The new features were palette-only and effectively undiscoverable. They now have menu homes:
  **Export Full Research Context** on the Overview toolbar and on question/plan/experiment context menus;
  **Reconcile HPC Runs** on the Experiments toolbar and run context menus (hidden unless a cluster is
  configured, via a new `research.hpcEnabled` context key); **Preview Project Upgrade** on the Agent view;
  **Review Managed AGENTS.md Block**, **Check Configuration** and **Show Effective Configuration** on the
  AGENTS.md / config.yaml items.
- New RPC methods `config_check` and `config_show`. All reporting, upgrade, HPC and config logic stays in
  the Python core; the extension only presents it.
- Tests now assert that every declared command is implemented, that each feature has a non-palette menu
  home, and that every `rpc('…')` call in the extension names a method that exists.

### Resource reservations need explicit approval

- Every submission path now discloses what the job would **reserve** — CPUs, GPUs, memory, wall-time,
  partition, account — plus the CPU-/GPU-hours that costs at the wall-time limit. Slurm bills the
  reservation, not the usage.
- `research hpc submit` without `--yes` refuses **with those numbers in the refusal**, so an agent cannot
  ask for approval without showing them. `research run submit` and `research run sweep --mode slurm` gain
  `--dry-run` and print the same block; a sweep totals it across every grid point (a 3-point sweep at
  8 CPUs / 2 GPUs / 12h surfaces 288 CPU-hours and 72 GPU-hours up front).
- The managed `AGENTS.md` block and the `hpc-setup` skill instruct agents to show the user these numbers
  and wait for an explicit yes, never to raise cpus/gpus/mem/time on their own initiative, and not to
  treat `execution.auto_submit` as blanket permission for a new experiment's resources.
- A successful submission records what it reserved, so the approved figure is in the run's provenance.

### Upgrades refresh unmodified first-party skills

- A shipped skill's guidance changes between versions, but `upgrade` only ever *added* missing files, so a
  project kept an older copy forever. It now refreshes a first-party file **only when the project has not
  edited it**, detected by recording the hash of the version we installed: an edited file is left alone,
  and a file with no recorded hash is left alone too (we cannot tell a customization from a stale copy).

### HPC is now self-documenting for agents

- **New `hpc-setup` skill** (`research skill show hpc-setup`), installed by `research init` and added to
  existing projects by `research upgrade`: how to read `doctor`, which config layer a cluster belongs in,
  what each failing check means, and the login-shell/module trap in detail. It also tells an agent what
  *not* to do — invent storage paths, assume `$HOME`, ssh to the cluster by hand, resubmit an uncertain
  job, or put credentials in a Research OS file.
- `doctor` now searches `module avail` when Slurm is missing and names the module in its fix hint, instead
  of only reporting the absence. Every failing check carries an actionable fix (a test enforces this).
- The "No HPC cluster is configured" error names both config layers and points at the skill.
- `research hpc config --example` says where to put the profile, warns that `$HOME` is rarely right for
  data, and documents `login_shell` and what `slurm.setup` is for.
- README has a "When `doctor` says something is wrong" section with the two commands that show the
  non-interactive vs login-shell difference.

### Fixes

- **Remote commands now run through a login shell.** `ssh host cmd` gets a non-interactive shell, which on
  many clusters skips the profile scripts that set up the environment — so `research hpc doctor` reported
  `sbatch=NO` on a cluster where an interactive `ssh` finds it immediately. Found on Duke's DRCC, where a
  `slurm/slurm/21.08.8` module is auto-loaded by the login shell and `sbatch` lives in
  `/cm/shared/apps/slurm/current/bin`, a directory absent from the non-interactive `PATH`. Opt out per
  cluster with `login_shell: false` (and `remote_shell` to choose the shell). When Slurm is still missing,
  `doctor` now looks for a matching module and names it in the fix hint.
- **`research bin install` could not upgrade the project-local CLI**: run from `.research/bin/research`, it
  copied `.research/bin/lib` onto itself, reported success and changed nothing — so an already-fixed bug
  looked unfixed. It now refuses with the commands that do work, and `research upgrade --apply` reports it
  as one failed step instead of aborting.
- **`config check` validated the raw project block**, so a project inheriting its cluster from the machine
  layer was wrongly told it had no `ssh_host`. It validates the effective profile now.
- Tests no longer read the developer's real `~/.config/research-os/config.yaml`; a stray cluster profile
  there used to fail the "no HPC configured" tests.

### Test suite: 9m50s → 2m37s

- The big fixtures were rebuilt per test (~14s × 26 in `test_fullreport` alone). They are now built once
  per session as a template and cloned per test, via `conftest.built_template` / `clone_project`.
  `test_fullreport` alone went 432s → 25s.
- End-to-end scenarios are marked `slow`, so `pytest -m "not slow"` gives a fast inner loop while the
  release build still runs everything. 368 tests pass.

## 0.4.0 — 2026-10-12

### Full deterministic context export (`research report --full`)

- **`research report --full [SCOPE]`** writes one Markdown document holding the complete recorded scientific context —
  every run (all statuses), every metric entry with its full history, every synthesis, superseded findings and decisions,
  reviews, task verification history, notes verbatim, the audit log, and an explicit account of what is missing or was
  never recorded. 14 fixed sections, navigable by id. `--stdout`, `-o FILE`, `--check`, `--json`, `--stamp`.
- **Deterministic by contract**: byte-identical across invocations for a fixed state. Stable ordering, number and null
  spellings, Markdown escaping; metric and event ids are positional, so an export survives `research rebuild` unchanged.
  No wall-clock timestamp unless `--stamp` (which is labelled non-reproducible).
- **Zero side effects**: generating a report logs no event, creates no record and mutates nothing. `--full` opens the
  project read-only, so there is no hand-edit import, no run reconciliation and no Slurm polling during rendering.
- **Zero LLM involvement**: it retrieves, joins, orders and renders records. No model call, no agent, no summarization,
  no inferred conclusion. Preliminary and agent-authored findings are never presented as established facts.
- **`--check`** verifies coverage independently, by parsing record markers out of the rendered Markdown and comparing
  them with the authoritative ids. It separates **exporter failure** (omitted/duplicated record — non-zero exit) from
  **source-data incompleteness** (missing artifact file, unsynthesized experiment — reported, not blamed on the exporter).
- Columns added by a future schema still reach the export, under "Additional recorded fields".
- The existing concise `research report` is unchanged in semantics and output paths.

### Safe upgrades of existing projects (`research upgrade`)

- **`research upgrade --dry-run | --apply | --status`**: creates missing directories, restores missing default skills
  without overwriting customized ones, deep-merges missing config defaults, refreshes `.research/bin/`, runs schema
  migrations, and offers the managed `AGENTS.md` block. Safe to re-run; converges.
- **`--dry-run` mutates nothing** — not even the database: it reads the schema version through a read-only connection
  and never constructs a `Project`, so no migration, index rebuild, cache file or event occurs.
- `--apply` validates preconditions first, backs up the small metadata files it edits, applies steps independently
  (one failure is reported without aborting the rest), refuses an unsupported downgrade, and logs one `upgraded` event.
- Preserved: all records and relationships, custom config values, **unknown config keys**, customized skills,
  hand-edited canonical Markdown, and user-authored `AGENTS.md` content.
- Generated output is kept out of version control: `.research/.gitignore` gains `reports/`, `hpc/bundles/` and
  `hpc/incoming/`. The upgrade **appends** only the missing lines and never rewrites or reorders your own.
- **`research agents-md --preview | --diff | --apply`**: the managed block only. Bytes outside the markers are
  preserved exactly, including CRLF. Missing markers → a block is proposed; duplicated or malformed markers → it
  **refuses** with the manual fix and changes nothing. Marker detection is line-anchored, so prose mentioning a
  marker cannot corrupt the file.

### HPC: portable bundles, SSH/Slurm execution, dataset discovery (opt-in)

- **`research hpc doctor | init | config | clusters | inspect | bundle | submit | status | sync | collect | import |
  cancel | datasets`**. With no `hpc.clusters` configured, nothing changes and every command says so.
- **Portable bundles** (`research hpc bundle EXP-001`) run anywhere with Python 3.8+: `scripts/preflight.sh`,
  `submit.sh`, `collect.sh`. No agent, no network and no Research OS install needed on the cluster. The bundle carries
  the hypothesis, parameters, resources, dataset references, content hashes, and an actual source snapshot including
  uncommitted and untracked files — a commit alone is not a snapshot of what ran. Credential-shaped files, `.git`,
  `.research` and oversized files are never packaged.
- **Result archives** import back onto the *same* run record (`research hpc import`), verifying identity, submission
  token and every checksum before trusting a byte; rejecting absolute paths, `..` traversal and non-regular members.
  Re-importing an identical archive changes nothing. Artifacts over `hpc.collect.max_artifact_bytes` stay on the
  cluster and are registered as references with their remote path and size.
- **SSH + Slurm** (`research hpc submit --cluster NAME`) reuses the existing Slurm backend over an rsync/ssh transport.
  Authentication is entirely OpenSSH's (`~/.ssh/config`); Research OS stores no passwords, keys or tokens.
- **Duplicate submission is prevented structurally**: the run id and submission token exist before anything is
  transferred, the job script writes a remote receipt, and a submission that cannot be confirmed records
  `submission_uncertain` and **refuses to retry**. `research hpc sync` then adopts the job that is already queued.
  A transient SSH failure is never taken as proof that submission failed.
- **`research hpc sync`** reconciles outstanding jobs on demand and at the next session — no daemon. It updates status
  from `squeue`/`sacct` (accounting being unavailable is not treated as failure), imports available results, and lists
  the experiments that now need interpretation through the existing synthesis machinery. It **dispatches no agent**:
  Slurm continues while the laptop is off, agent reasoning does not, and Research OS does not claim otherwise.
- **Datasets have no registry.** The configured `shared_data` directory is the source of truth; each dataset describes
  itself in its own `manifest.json`. A directory without a manifest is neither trusted nor re-downloaded. Verification
  strength is explicit (manifest → inventory → `--deep` checksum), interrupted downloads can never read as complete
  (the manifest is written last), concurrent requests are serialized by an atomic `mkdir` lock, and different versions
  are kept apart. Nothing remote is ever deleted.
- Safety: dry-run previews, resource limits per cluster, remote paths validated and confined to the configured roots,
  `rsync --delete` refused, `BatchMode=yes` with explicit timeouts and argument lists, submission requiring `--yes`,
  and every submission and retrieval logged.
- Exposed through RPC (`report_full`, `upgrade`, `agents_md_state/apply`, `hpc_status`, `hpc_sync`) and MCP
  (`report_full`, `upgrade_preview`, `hpc_doctor`, `hpc_sync`, `hpc_bundle`). No reporting, upgrade or HPC logic is
  reimplemented in TypeScript.

### Fixes found while building the above

- **The full export counted artifacts attached to neither a run nor an experiment but never rendered them.**
  Found by running `--check` against a real project (`pravah`), whose standalone `stage1_*.csv` outputs are
  registered without `--run`/`-e`: coverage verification correctly failed on 5 records. They now appear under
  "Artifacts not attached to a run or experiment" in section 10.

- **`rebuild` was not idempotent**: it replayed `events.jsonl` on top of an already-populated index, so each rebuild
  inflated the audit log (2 → 6 → 11 events) and turned every entity import into a false "duplicate id" conflict. The
  index tables are now cleared before the replay, and imports during a rebuild no longer re-log their own events —
  so `events.jsonl` stops growing on every rebuild too.
- **`_deep_merge` shared nested dictionaries with `DEFAULT_CONFIG`**, so editing one project's `p.config` corrupted the
  process-wide defaults (and therefore other projects opened later). It now deep-copies.
- **The `reviewed` flag on failed runs was lost on rebuild**: `synthesize` set it in SQLite only, without rewriting
  `run.json`. It is now persisted to the canonical file.
- `research hpc` with no action prints its help instead of an argparse error, and a bare subcommand name is no longer
  mistaken for a missing command tail.

### Tests

279 → 348 tests, all offline and deterministic: a fake SSH/Slurm cluster (`tests/python/fakecluster.py`), a 100-run
fixture with long metric histories and every finding status, golden determinism and coverage checks, pre-upgrade
fixtures at schema v1/v2/v3, and the full 17-step acceptance scenario (upgrade → remote run → collect → import →
reconcile → re-export → idempotency → rebuild).

## 0.2.0 — 2026-10-11

### Numerical evidence, artifact integrity and provenance (optional; legacy findings unchanged)

- **Evidence bindings** (`research evidence bind|verify|value|suggest|provenance|artifact|pin|status`, append-only
  `.research/evidence.jsonl`): tie a number in a finding to a recorded metric or a whitelisted calculation over metrics. Values are read from
  the records; ambiguity, NaN, failed runs and incompatible units are explicit. Statuses: verified, mismatch, missing, stale, unverifiable.
- **Review gate**: a finding with bindings can't become `supported` (by anyone) unless every binding verifies, and the reviewer must write
  interpretation notes; independent review is unchanged. Corrections use `--supersedes`.
- **Artifact integrity**: streamed hashes, `modified/missing/unavailable/unhashed` states, append-only pins for large files, historical warnings.
  Runs now record environment lockfile hashes; `evidence provenance` lists what was *not* recorded.
- **Reports** show an evidence table per finding with bindings. The Scientist Guide status shows each finding's numerical state, and the
  `scientist-guide`/`synthesize-experiment` skills tell agents to get numbers from `research evidence`.
- **Literature sources** (`research source`): offline records keeping identity, examination and support separate.
- Limitations are listed in SPEC §22.2.

### Scientist Guide, handoffs and tiered agents (all optional; existing projects keep working)

- **Scientist Guide** (`docs/SCIENTIST_GUIDE.md`, path/model in `config.yaml → guide`): a living scientific narrative, separate
  from README, `research report` and technical docs. Every synthesis makes it *pending* (derived from `syntheses`, tracked in
  `.research/guide.json`); only `research guide done` clears it. `research guide init|status|refresh|done`. `refresh` prepares
  an Opus agent and never marks anything done. Pending shows in Needs Attention once a guide file exists.
- **Skills**: `scientist-guide`, `scientific-handoff` (discussion → Question/Plan/Experiment/Tasks), `engineering-loop`,
  `parameter-tuning`. The AGENTS.md block gained a short "Scientific practice" and "Tiered agents" section.
- **Opus → Sonnet → Haiku orchestration** (`config.yaml → orchestration`, enable with `research setup agents --apply`):
  routing by verifiability (`research orchestrate route`), frozen acceptance tests that block `task done` if changed,
  Haiku dispatch refused without frozen tests, bounded attempts/escalations with failure categories, parent/child delegation,
  and validation-only tuning with a one-shot held-out test (`research tuning`). Verified model IDs: `claude-{opus,sonnet,haiku}-5-5`.
- `research setup agents` writes managed `.claude/agents/research-*.md` (user-owned files are never overwritten), installs
  first-party skills, and offers Ponytail only with `--approve ponytail`.
- Not included: VS Code UI for these features, MCP tools for them, and token/cost recording.

### Project-local CLI

- **`.research/bin/research`** is a copy of the CLI inside each project, so any agent can run it without `pip install`
  or a `PATH` change. That includes the Claude Code extension's Bash tool, which never sees VS Code's terminal shim.
  It is created by `research init`, refreshed by the extension on activation (`research.projectCli`), and refreshed on
  demand by the new `research bin install`. It is gitignored because it is per machine.
- The managed `AGENTS.md` block now tells agents to use it. The extension updates existing blocks once.
- `research mcp install` falls back to the launcher's absolute path when `research` isn't on PATH.
- Commands started by `run exec` through the launcher can `import research` without installing it.

### Research Home redesign

- Home leads with the current focus: what we believe, what blocks us and the next step. It also shows what changed
  since the last checkpoint, a triaged "Needs your judgment" list, a plan stepper with a progress ring, findings and
  failed directions, and key-result charts with evidence thumbnails. Fine controls moved into "⋯" menus and
  collapsible sections.
- Plan, Task, Experiment and Finding pages lead with meaning (goal, result, what we learned) and give one primary
  action per state.
- Sidebar: a compact "Focus" view replaces Overview, tree rows lead with titles, and there is a new
  "Research: Open Item…" quick pick.

### Research OS layer (schema v3, migrated automatically)

- **Verification gates.** Tasks can carry checks (`cmd:…`, `file:…`, `metric:[EXP-…:]name<=x`); `research task verify`
  runs them, and `task done` runs them first, blocking agents on failure. Agents can no longer mark findings supported:
  `research finding review F-… --verdict supported|contradicted|needs_work` by a human or a *different* agent/role does
  (with evidence required). New `reviews.jsonl` records; Home shows failing checks, reviewer disagreements and findings
  awaiting review.
- **Multi-agent coordination.** `task start` claims a task (others are refused, exit 3), `task note` leaves progress
  notes, `task release` hands it back, idle running tasks are flagged stale.
- **Roles, profiles, dispatch.** `config.yaml → agents` maps roles (planner, implementer, verifier, analyst) to agent
  CLIs (claude, codex, gemini, …). `research dispatch` / `research task dispatch` write a focused brief and start the
  agent; authorship becomes `profile/role`. `research agents` lists them.
- **MCP server.** `research mcp` (stdio, standard library only) with 28 tools; `research mcp install` registers it for
  Claude Code, VS Code, Cursor and Gemini, and prints the Codex command.
- **Project skills.** `research skills add <folder|git URL|owner/repo> [--only …] [--always]`, `update`, `remove`,
  `set --always/--applies-to`, `link`. Skills are linked into `.claude/skills` and `.agents/skills` and suggested in
  task briefs. Provenance in `.research/skills.yaml`.
- **Tiered context.** `current.md` has a size budget and lists active plans, attention items and always-on skills;
  `task context` adds checks, skills, progress notes and related findings; `research search` retrieves anything.
- **Run comparison, sweeps, reports.** `research run compare A B` (and a side-by-side page in VS Code),
  `research run sweep EXP --grid …` (checked against the run budget up front), `research report [scope]` (Markdown +
  HTML with evidence plots).
- **Git snapshots.** `research checkpoint --commit` commits `.research/` only (opt-in; never pushes).
- **VS Code:** Dispatch to Agent, Plan with Agent, Review form, Verify, progress notes, Compare Runs page, Search,
  Export Report, Import Skills, Register MCP Server; Home quick actions; task page shows checks, skills, notes, claims.
- **Fixes:** UI actions could be attributed to an agent when VS Code was started from an agent's shell (more identity
  variables are now stripped); `task start` no longer clears the assigned role.
- Tests: 22 new (104 in total); real-VS-Code harness: 71 checks.

### Plans, tasks and Research Home

- **Plans and tasks** (schema v2, migrated automatically): `PLAN-…` objectives with success criteria, and `T-…` tasks with
  type, role, dependencies, goal, inputs, expected outputs, acceptance criteria, verification, result and blockers. A
  task is *ready* (derived) when it is `todo` and all dependencies are `done`. New commands: `research plan create|list|show|update`
  and `research task create|list|show|update|start|done|block|next`, plus matching Python API and RPC methods.
- **Research Home** replaces the Resume page as the VS Code landing page (`Cmd/Ctrl+Alt+R`): current focus, active plan
  with progress, latest insights, needs-your-attention items, key outputs, recent activity. RPC `home`.
- VS Code: new **Plans** sidebar view, Plan and Task detail pages, New Plan / Add Task forms, Start / Complete / Block task actions.
- `AGENTS.md` managed block rewritten as a shorter "Research OS" quick start. The commands it names now exist:
  `research current`, `research task context T-…` (focused task brief) and `research skills show NAME`.
- `context/current.md` gains an "Active plans" section; `research mark` works on plans and tasks; plan pages show activity;
  plans and tasks have edit forms; the Add Task form offers all 8 task types.
- Fixes: New Plan form always failed; `{}` written for empty task lists; Start shown on finished tasks; Complete Task
  ignored Esc; long CLI labels ran into values; `npm run package` failed in uv venvs; the VS Code test harness failed
  to launch current VS Code builds.
- Tests: 42 new plan/task tests (82 in total); the real-VS-Code harness grew to 57 checks.
- Docs: README rewritten as a usage guide; `docs/SPEC.md` now holds the feature inventory, the design rationale (formerly
  `docs/DESIGN.md`, removed) and known issues.

## 0.1.0 — 2026-09-29

First release.

- Python core (stdlib-only, Python ≥ 3.8) and the `research` CLI, covering questions, experiments, runs, metrics, artifacts,
  syntheses, findings, decisions, checkpoints, notes and skills.
- Storage: text files are canonical, SQLite is a rebuildable index. Supports hand edits, rebuild and relocation.
- Run budget for agents (exit code 3), authorship tracking (human / agent / system), managed `AGENTS.md` block,
  and `context/current.md`.
- Local runner (foreground and detached) and SLURM backend (sbatch / squeue / sacct / scancel).
- VS Code extension: Overview, Resume Project, trees, detail pages, forms, terminal shim, Remote SSH support.
- Tests: 40 pytest tests, a clean-install test and a code-server UI end-to-end test.
