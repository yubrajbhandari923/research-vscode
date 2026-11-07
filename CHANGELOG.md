# Changelog

## 3.0.0

**Upgrading**: this release changes how the pieces fit together. Install the CLI first, then the extension. `pip install` the wheel from the GitHub release (or `research self-update` once on 3.x), then install the new VSIX. Project files are untouched. The extension no longer bundles Python, so a VSIX alone is not enough. It shows a warning with the exact command if the CLI is missing or out of date.
- **CLI-first.** The `research` CLI is the product; VS Code is an alternative interface to it. New: `research rpc` (the extension's backend), `research version [--json]`, `research self-update [--check] [--version X] [--major] [--from-file whl]` (downloads from GitHub releases, verifies `SHA256SUMS`, leaves the existing install untouched on any failure; a major bump needs `--major`).
- **TUI**: `research tui` (install with `pip install 'research-panel[tui]'`). A themed dashboard (runs by status, recent runs, pending approvals, findings, doctor) plus Runs, Findings, Plans & Approvals (approve/deny with rationale and masked PIN, through the same core functions as the CLI), Records and Doctor tabs. Responsive banner, light/dark theme, `NO_COLOR` support.
- **Extension is a thin client**: it spawns the installed CLI (`research.cliPath` or `PATH`) and checks `researchCore` ("3.0": same major, core minor at least this) at startup. A mismatch shows a status-bar warning and a prompt to update the CLI (`research self-update`) or the extension; `Research: Show Version Info` lists both. The VSIX shrinks from about 1.3 MB to about 260 KB. Removed: the bundled `python/`, the terminal shim, `research.pythonPath`, `research.terminalCommand`, `research.projectCli`.
- **Fix: opening a subfolder of an initialized project offered to re-initialize it.** The CLI and the extension now find the project the same way, by walking up to the nearest `.research/config.yaml`.

## 2.6.0

**Upgrading**: keep your project files and reinstall the extension. On open it refreshes `.research/bin`, links `./research` into the main checkout, starts the supervisor if Telegram is enabled or a plan is running, and replaces a supervisor still running older code (agents are never touched). New settings default off; existing config files are untouched. From the CLI: `research bin install --force` then `research supervisor restart`.
- **Telegram needs two commands**: `research telegram setup` (also enables alerts and diagnosis, and starts the supervisor, which answers the chat). After a reboot, opening the project in VS Code or `research supervisor start` brings it back.

- **Fix: the approval PIN prompt did not work in the CLI.** (1) `read_pin` opened `/dev/tty` in text `r+` mode, which Python 3.14's `getpass` rejects ("not seekable"); the error was swallowed and reported as "no interactive terminal". It now reads the terminal directly with echo off. (2) With no PIN set, protected actions passed silently; the first protected action in a real terminal now prompts to create one. (3) A PIN that could not be read no longer counts toward the 60 s lockout, and agent-shell refusals name the environment variable and the `env -u` workaround.
- **`./research` in the main checkout**: `research bin install` links the project launcher into the main checkout (git-excluded, never overwrites a file or directory); `research bin link|unlink [--name]`. The launcher now follows symlinks.
- **Escalation after an incomplete deliverable** (`execution.escalation` / `supervisor.escalation`): a missing or empty required output is classified `incomplete_deliverable`, and the next repair attempt can use a stronger model, higher effort or another profile (bounded, fresh session, recorded). Off by default.
- Tests: `test_cli_pin_link_escalation` drives the real launcher through a pseudo-terminal.
- **Plan validation**: `plan yaml validate|diff` warn when a `verifier` task must itself create a deliverable nobody upstream produces (verifiers check work, they do not author it). Advice only; nothing is rewritten.
- **Failure handling**: a missing file is now `incomplete_deliverable` (repair), `missing_prerequisite` (another task owns it: blocked after one attempt, names the owner) or `role_contradiction` (verifier must author it: blocked after one attempt). History, budgets and approvals are untouched.
- **Configuration**: `promotion.test_command` is the one canonical test command (`auto_promotion.test_command` is a legacy alias; both set and different is an error). `auto_promotion.numerical_command|reviewer_profile|require_distinct_reviewer` are in Settings. PIN errors now say incorrect PIN (with attempts left) vs no interactive terminal vs agent-controlled environment.
- **`research supervisor reload` / `restart`** (also in Control Center): validate and apply settings live, or replace the process, without touching running agents, worktrees, budgets or promotion state. Settings are classified live / next launch / restart; an out-of-date supervisor is flagged.
- **Concurrency** is reported (effective value and whether it comes from the project or the plan).
- Fixed: applying a YAML whose only change was the plan `execution:` block did nothing.
- Tests: `test_supervisor_fixes`.
- **Supervisor intervention** (`research supervisor instruct`, `research intervention …`, Instruct / Unblock in the Control Center): steer a task in words; a planning agent proposes classified actions, protected ones (science, resources/retry budget, frozen tests, role changes) need ONE PIN approval, then the supervisor applies them idempotently, re-runs the task and resumes the plan. Evidence and original criteria are preserved (`verification_history`, `originals`, a Decision).
- **Honest plan status**: `health` (progressing / waiting_for_approval / HALTED / completed) instead of an unqualified "running".
- **Debug agent + Telegram** (opt-in): diagnosis of a halt with options (`research supervisor debug`), halt notifications and chat commands (`research telegram setup`), machine-local credentials, settings under Settings → Supervisor.
- Fixed: re-verifying an attempt overwrote its earlier verification result.
- Tests: `test_intervention`.

## 2.5.0

**Opt-in unattended code promotion** (`docs/SUPERVISOR.md` section 14). Off by default; needs the plan to declare `execution.auto_promotion: true` AND a human PIN-gated `research plan autopromote PLAN authorize`.
- Verified, unpromoted commits are tracked per experiment and promoted at experiment boundaries or when a downstream task depends on them: candidate from current `main`, cherry-pick of only new commits, integration tests + numerical checks, independent review by a different reviewer, then supervisor-authorized `git merge --ff-only`. Dependents are blocked until then and start from the integrated `main`.
- Fail-closed: failures, dirty or moved `main`, conflicts and edited tests pause the item (persisted, restart-safe, idempotent); no force, rebase, push or blind retry. Scientific findings are never created or approved.
- Fixed: `promotion.integrate` asked for the PIN (and logged an approval) before checking the candidate was approved.
- New: `research plan autopromote`, `autopromote.py`, `promotion.supervisor_approve/integrate_authorized`, `workspace.ensure_for_experiment(base=)`. Tests: `test_autopromote`.

## 2.4.0

**Agents propose, you approve: governance, a PIN guardrail, provider handoff and YAML setup** (`docs/GOVERNANCE.md`).

- **Fix: `research doctor` crashed (`KeyError: 'text'`)** when a check carried a command-only fix (the HPC check). Root cause: the producer returned `{command}` while the CLI read `fix["text"]`. `doctor.normalize_fix` now gives every consumer one shape (`text`, optional `command`, `safe`); the CLI and the Doctor page tolerate missing fields. Statuses unchanged.
- **Governance** (`governance:` settings): agents cannot create experiments (`propose_only` by default; `human_only` forbids proposals); tasks are `bounded_auto` (allowed types, per-experiment cap), `propose_only` or `human_only`; experiment overrides can only tighten. `supervisor.auto_apply_operational` / `max_auto_tasks` can only tighten governance. Enforced in `create_experiment`, `create_task`, proposal decisions, policy changes and promotion, so CLI, MCP, RPC, plan YAML and the supervisor all obey it.
- **Proposals**: experiment proposals (hypothesis, rationale, method, expected outputs, relation to existing work) and plan proposals share `pending → approved | rejected | revision_requested`; applied exactly once under the supervisor lock with revision and conflict checks; rejections are recorded. New `research proposal experiment|revise`, MCP `experiment_propose`.
- **Approval PIN** (`research approval set-pin|status|verify`, a dialog in VS Code): a local scrypt verifier outside the project; required for approving proposals, changing policy and promoting to `main`; fails closed without a terminal; rate-limited; the audit trail never holds it. Creating your own experiment needs no PIN unless `governance.approval.confirm_human_experiment_creation` is on. Not a defence against a hostile agent (see the docs).
- **Provider handoff**: continue a stopped or failed task with Claude, Codex or Antigravity in the same worktree with a portable context document; refused while the previous process may still run; opt-in bounded automatic fallback (`agents.handoff`). Antigravity (`agy` 1.5.0) now runs headless with `--mode accept-edits` and resumes natively with `--conversation`.
- **YAML setup**: `research init --template|--from FILE [--dry-run|--apply]|--export` and an *Edit YAML* mode in the Initialize Project wizard (validate, preview, load/save), through the same initializer as the guided wizard.
- Fixed: a new project's `policy.md` was stale right after initialization when skill packs were chosen.
- Tests: `test_doctor`, `test_governance`, `test_handoff`, `test_bootstrap`, plus the changed supervisor tests (untyped agent tasks no longer auto-apply; approvals need a reason).

## 2.3.0

**Equipped by default: a curated skills catalog with packs, and project-aware hooks** (`docs/AGENT_TOOLS.md`).

- **Ten first-party skills** (Core Research): `research-context`, `hypothesis-design`, `validation-oracles`, `failure-triage`, `reproducibility-audit`, `uncertainty-analysis`, `literature-evidence`, `scientific-visualization`, `independent-review`, `promote-validated-work`. They use real Research OS commands and do not duplicate existing skills.
- **A catalog of 58 skills** (`research skills catalog`): first-party, **bundled** (31 curated copies from Ponytail, Superpowers, K-Dense scientific-agent-skills, Anthropic skills and Kalaris, MIT or Apache-2.0 only, each with its LICENSE, a SOURCE.yaml with the pinned commit, and an entry in THIRD_PARTY_NOTICES.md) and **external** (Trail of Bits CC-BY-SA-4.0, Vercel without a license, Anthropic's large skill-creator: listed, never shipped, installed from source at a pinned commit on request).
- **Nine skill packs** (`skills.packs`): Core Research (default), Scientific Python, Machine Learning, Physics/Numerical, Medical Imaging, Quantum Computing, HPC, Frontend, Scientific Writing. The wizard has a Skills step; a pack list in the machine config is the default for every new project; the project value wins.
- **Activation, not injection:** each skill has `applies_to` (task type or role) in `.research/skills.yaml`; only matching, assigned or always-on skills reach a brief. `research skills catalog|packs|install|enable|disable|upgrade`; `upgrade` never replaces a skill you changed.
- **Hooks catalog and checks** (`python -m research.checks`): git integrity, secrets scan (values never printed), lint, format, tests, tsc, task scope, frozen-test integrity, experiment configuration (commit, clean tree, seed), numeric invariants (NaN/Inf, declared bounds), artifact integrity, plus the existing syntax and whitespace checks. Nine cheap ones are the default and are approved by the human in the wizard; `research hook recommend|install` is **project-aware** (detects Python, TypeScript, notebooks, scientific/ML/medical/quantum dependencies); costly ones that run project code are never automatic. Approval, timeouts and enforcement are unchanged.
- **Agents → Skills, hooks and MCP** rebuilt: searchable catalog, install, enable/disable, editable activation, packs, update, recommended hooks with approval and test results, where the settings live.
- **Fixes:** advisory hooks no longer count as the "deterministic verifier" a task needs (a syntax check could make a task with no checks pass); skill links were broken when the project path went through a symlink (`/var` on macOS); skill front matter with a colon in the description is quoted so strict YAML readers accept it.
- Tests: catalog integrity and licensing, attribution, activation, packs, update semantics, external install from a pinned commit, symlink regression, every check, project-aware recommendation, the wizard end to end with a small workflow, and the Agents page and wizard step against the real core.
- Not done: skills from sources beyond the seven named; per-pack hooks (hooks are project-aware, not pack-bound); run-time enforcement of draft limits (unchanged).

## 2.2.0

**The Scientist Guide is Quarto-only, created by default, and built for communicating results** (`docs/GUIDE.md`).

- **v1 guide removed.** `docs/SCIENTIST_GUIDE.md`, `research guide done|refresh`, `.research/guide.json`, the `scientist-guide` skill and the `guide.path` setting are gone. An existing Markdown guide is left untouched but no longer read; `research guide init` creates the book.
- **A Quarto book on the main branch, in every new project.** The Initialize wizard has a Guide step (what it is, how citations work, when an agent writes, the limits). The book has Key results, Notes and ideas (yours alone) and References chapters, `references.bib`, and `theme.scss` (a per-project theme). It always lives on the primary checkout, whichever experiment worktree the project is opened from.
- **Publish on every push.** `.github/workflows/publish-guide.yml` builds the scientific edition and deploys it with GitHub Pages (one-time: Settings → Pages → Source: GitHub Actions). Written once; never overwritten or deleted.
- **Important results are logged automatically.** `guide.highlights` (`supported` · `starred` · `all`) plus starred findings; a short entry (claim registered against the finding, its plot, a DRAFT slot) is added to `results.qmd` when a finding is reviewed as supported or starred. Entries are never rewritten. `research guide highlights|star|unstar|log`.
- **Bounded agent drafts.** `research guide draft` prepares (never starts) a session whose brief carries `guide.draft.max_words/max_sections/max_figures`; `guide draft check` compares the files with a baseline and reports overruns, human-owned edits and added figures; `guide draft done` records what was drafted. `guide.draft.trigger` (`result` · `checkpoint` · `manual`) only controls the Home reminder.
- **Citations.** `research guide cite add|list|check` (pasted BibTeX is validated; keys must be new); a cited key missing from `references.bib` is a validation error.
- **Guide page in the Control Center** (Create, important results with Star/Log/Draft, chapters with owner and evidence state, limits, references with an Add dialog, publishing, build result with leak and provenance problems), commands *Open Scientist Guide*, *Draft … with an Agent…*, *Preview … Website*, and RPC/MCP `guide_*` methods.
- **Reports.** `research report` HTML/Markdown: at-a-glance tiles, contents with anchors, status badges, tinted verification cells, sticky contents on wide screens, print styles, figure alt text. `report --full` adds a contents list and an at-a-glance line (still byte-deterministic; coverage verification unchanged). New MCP tool `report`.
- **Agents → Skills, hooks and MCP** (docs/AGENT_TOOLS.md): what each is for, what is loaded, one-click *Link skills now*, *Add starter hooks*, approve/disable/run, MCP command. New projects link the shipped skills into `main/.claude/skills` and `main/.agents/skills` (git-ignored, machine-local) and add and approve two non-blocking starter hooks (Python syntax check, `git diff --check`); `tools: {link_skills, starter_hooks}` in the wizard spec turns either off.
- **Reports in the UI:** Research tab → Reports (write the project report, the complete export, open written files); also on the Guide page. `research report` HTML/Markdown readability as above.
- **Settings shows the Research OS version** (and the core's interpreter) so you can confirm which build is running.
- **Quarto section** on the Guide page (version, path, PDF availability, how to install or update on macOS, Windows and Linux); docs/GUIDE.md#updating-quarto.
- Fixes: a command that navigates the Control Center while it is still loading is no longer lost; Guide status computes validation once and caches the Quarto check.
- Tests: guide core, citations, triggers, draft limits, report HTML escaping, init defaults, and the Guide page and wizard against the real core.
- **Upgrading from 2.1:** `docs/SCIENTIST_GUIDE.md` and `.research/guide.json` are no longer read (nothing is deleted); run `research guide init` to create the book, commit `docs/scientist-guide` and `.github` on main. A 2.1 guide keeps its `_quarto.yml`: `research guide log` adds `results.qmd`, and `research guide chapter add ideas.qmd --owner human` / `references.qmd --owner human` list the new chapters (add `bibliography: references.bib` if missing). For skills: Agents → *Link skills now* in an existing project.
- Known limits: Quarto-dependent tests (real HTML/PDF builds, the Lua filter, leak check on real output) are skipped where Quarto is not installed; draft limits are checked after the fact, not enforced.

## 2.1.0

**Persistent supervisor** (YAML plans, supervisor process, agent reporting, proposals, Plans tab; see docs/SUPERVISOR.md) plus:

- **Environment setup you do once** (docs/SETUP.md): `environment.python` (a uv-created or existing venv) is used by agents, task checks (`{python}`) and `research env check|create|install`; the Initialize wizard creates it with uv or tests an existing one, and collects the cluster's Python, job-environment lines and required packages. `research hpc doctor --compute` (and the wizard's compute-node test) proves the job environment where jobs really run.
- **Codex native resume** (thread id parsed; `codex exec resume`), **parallel agents** across experiments, failed-HPC-job repair, agent-originated proposals, **commit-on-verify and promotion** of supervised work (the supervisor prepares candidates, the researcher reviews).
- Fixes from real runs and stress tests: partial YAML patches no longer drop tasks, run attribution by id, daemon zombie detection, provider limits do not consume attempts, frozen tests inside worktrees, promotion dialog refresh, and the smaller items below.
- Smaller: prompts no longer repeat the task text three times; agents' `patch*.yaml`, caches and venvs are never committed; voided attempts are shown separately; several plans can be expanded at once; supervisor start/stop is a button.

## Earlier work in this release line

- **YAML research plans** (`research plan yaml template|export|validate|diff|apply`): versioned schema, stable keys, revision-aware idempotent reconciliation, safe edits of unstarted work, protection of running/completed work.
- **Supervisor** (`research plan run|pause|resume|stop|status`, `research supervisor …`): a persistent process that schedules ready tasks, launches agents in the right worktree with assembled context, verifies results itself, repairs within limits, waits for HPC runs, recovers after restarts. Only its verification (or a human `accept`) completes a supervised task, by any route, including hand-edited files.
- **Agent reporting** (`research agent progress|artifact|report|problem|propose|finish`, MCP `agent_report`/`agent_propose`/`plan_status`), validated against the owning execution.
- **Plan-change proposals** (operational vs scientific) with approval in the UI.
- **Control Center → Plans** tab and YAML import/open/apply commands; **Settings → Supervisor**.
- Fixes found by real runs: frozen tests inside worktrees, provider usage limits, claim handling. See docs/SUPERVISOR.md.

## 2.0.4

- **Agent reviewers can actually record their verdict.** The headless allowlist now covers `.research/bin/research …` and `cd … && research …`; briefs tell agents to run `research` directly, one command per call. Terminal-run agents (finding reviews) print readable text instead of raw JSON.
- **Policy gates say how to proceed.** The run-budget message lists the exact commands (`run update … --reviewed`, `experiment synthesize`, `--override "reason"`); `hpc submit` accepts `--override`.
- **Promotion → Run tests** asks for the test command when none is configured (and points to `promotion.test_command`).
- `hpc sync` prints the last stderr line of a failed job; `research run logs` reads logs that came home from the cluster.

## 2.0.3

- **First dispatch no longer dead-ends.** When frozen acceptance tests are required, the dialog offers *Have the balanced agent write the tests first* (dispatches the coordinator level, which is exempt from the gate and whose brief says to write and freeze tests), besides *Freeze test files…* and the logged override. Errors can now carry machine-readable `data`.
- **HPC from the UI.** *Research: Run on HPC Cluster…* (palette and Work tab **Run on cluster**): pick experiment, enter the command, create the remote folders if missing, review the reservation, approve, submit; then *Check status and fetch results*. It bundles the experiment's worktree code, like the CLI does from inside the worktree. New RPC `hpc_init`, `hpc_submit`.
- The Initialize wizard's HPC step asks for the **job environment** (module load / venv lines → `slurm.setup`), because compute nodes often lack packages the login node has.

## 2.0.2

Fixes from an end-to-end test on a real Slurm cluster:
- New projects' workspace shows `experiments/` so the code of every experiment worktree is visible; the Work tab's worktree rows have **Open** (adds the folder to the workspace).
- Dispatch's "prepared" step is a QuickPick (Start / Preview brief / Not now) instead of a toast that vanishes; Preview returns to it, so Start is never lost.
- *Add task* on a plan pre-fills type, role and the plan's experiment.
- Implementer briefs state the HPC result contract (`RESEARCH_RUN_DIR`, `metrics.jsonl`, `artifacts.jsonl`).
- `research run logs` finds logs that came home from a cluster (`collected/logs`).

## 2.0.1

- **Dispatch gates are actionable.** "No frozen acceptance tests" and "no worktree" now open a modal with the full message and actions (Freeze test files…, Dispatch without frozen tests… with a logged reason, Create worktree and continue). The gates themselves are unchanged.
- **Agents never silently edit `main/`.** In a shared-ledger project an implementer dispatch needs the experiment's worktree; an experiment's worktree serves all its tasks. New experiment creates a worktree by default.
- **Headless Claude Code can actually work.** `claude -p` is started with `--permission-mode acceptEdits` and `--allowedTools` limited to the `research` CLI/MCP (override per profile with `allowed_tools`, `permission_mode`); the ledger's `research` shim is put on `PATH`. Previously a headless run could exit 0 having done nothing.
- **Task checks run in the task's worktree** (command cwd and `file:` paths), not the ledger folder.
- **HPC (verified on a real Slurm cluster):** fixed the bundle being looked up under `main/` instead of the ledger in shared-ledger projects (upload failed), and commands with quotes/spaces (`-- python -c 'print("x")'`) now reach the cluster shell-quoted. Containerised jobs: a sandbox profile on a remote `hpc submit` is still refused (fail closed); run Research OS on the cluster and use `research run submit` with an apptainer/singularity profile, which was verified end to end.
- Work tab: New plan, Add task, Dispatch; "workspace" wording is now "worktree"; readable effective task spec; new projects do not badge Source Control for ledger records.

## Unreleased

- **Initialize Project** wizard: empty folder → `main/` + `ledger/` + `experiments/` + `.code-workspace`, one git repository, safe first commit, instructive steps, optional HPC discovery/test.
- **Provider adapters** (Claude Code, Codex, Antigravity, custom) with explicit capabilities; `research provider|execution|project-init`; Agents tab; Agent/Model/Mode/Preview/Start dispatch.
- **Fixes**: the execution wrapper and VS Code terminal now start the agent in the experiment worktree; briefs are referenced by absolute path; `running` is reported only for a confirmed live process.

## 2.0.0

**Breaking release.** Project format 2; v1 projects are not upgraded or migrated (see `docs/GUIDE_V2_START_FRESH.md`). Storage schema stays at 3.

- **Format 2:** `.research/format.json` identifies the project format. v1 projects are rejected with clear instructions and left byte-identical; nothing is converted, moved or deleted automatically. `research upgrade`, `config migrate` and the matching RPC/MCP/extension entry points are removed.
- **Control Center** (`Research: Open Control Center`): redesigned Home / Work / Research / Settings on a shared design system (tokens, components, light/dark/high-contrast via VS Code theme variables), populated and empty states, narrow-window layout, guided first-run setup and New experiment, project health.
- **Configuration is files:** Settings shows each setting's effective value, origin layer, exact file and key; Open configuration file; saves are refused when the file changed on disk; unreadable YAML is reported without being rewritten; the UI reloads on window focus. Layers are default → machine → project; `AGENTS.md` documents where configuration lives. See `docs/CONFIGURATION.md`.
- **Project doctor:** `research doctor [--fix ID]`, RPC, read-only MCP tool, and Settings → Project health. Optional capabilities are reported as optional, not as failures.
- **Shared research ledger and experiment worktrees** (`research ledger enable|status|relink`, `research workspace …`), with run provenance pinned to the exact source snapshot.
- **Typed settings registry** (`research config options|explain|set|reset`): validation, inheritance display, YAML diff preview, comment-preserving edits, agent refusal for policy keys, audit events, generated `.research/context/policy.md`.
- **Optional job isolation** (`research sandbox …`, `run exec|submit --sandbox`): profiles, `userns` and `apptainer` runtimes, fail-closed refusal. Remote HPC bundles refuse when a profile applies. Agent sandboxing is not implemented (advisory only); mocked-Apptainer tests are not proof of real isolation.
- **Selective promotion** (`research promote …`): candidate branches with only the chosen commits or paths; tests bound to the candidate head (a dirty candidate worktree cannot be verified); independent review; human approval; fast-forward-only integration. Never forces, rebases, deletes or pushes.
- Fix: a machine-level override no longer makes the generated reference block look hand-edited in `config check`.
- Not included: promotion hunk selection, sandboxed remote bundles, forms for cluster/strategy/hook/profile settings, verification inside Remote SSH, high-contrast and screen-reader testing, Windows.

## 1.1.0 — Scientist Guide v2

- **Quarto Scientist Guide** (`research guide quarto init|prepare|build|preview|doctor`, `guide chapter add`): one set of `.qmd` chapters rendered as a *scientific edition* (no Research OS identifiers) and a *researcher edition* (same prose, exact evidence panels, legacy guide appendix). Opt-in; Quarto is needed only to render. Rendering never executes code.
- **Provenance** (`guide claim|figure|section|validate|coverage`): claims and figures linked to canonical records with a pinned basis; typed claims (observation … proposed) and figures (archived, recreated, derived, conceptual); detects missing, stale, contradicted, superseded, unreviewed and inconsistent evidence; per-section freshness; coverage of experiments and findings not yet in the guide.
- **Independent review** (`guide review request|record|approve|status`): deterministic validation, independent model review and human approval are separate records bound to a fingerprint of text and evidence.
- **Skill levels** (`skills set --level recommended|required|protocol`, `skill verify`, `skills audit`): required skills block managed dispatch when missing, are injected with a recorded hash, and drift is reported by `orchestrate verify`. First-party `scientific-briefing` skill; required for Quarto-guide refresh and review.
- `guide refresh` targets only stale or uncovered sections; RPC `guide_status`/`guide_validate` and MCP `guide_status`/`guide_validate` (read-only); `research upgrade` reports the optional guide and adds the new skill without touching records. Schema stays at 3.
- `guide figure refresh` re-pins a deliberately regenerated figure (history kept); PDF is scientific-edition only and no longer removes the HTML output; the researcher HTML links claims to evidence panels.
- `research dispatch --require-skill NAME` (repeatable) makes a skill mandatory for one dispatch; claim status "established" renamed "traceable" (provenance only).
- Docs: `docs/GUIDE.md`. Known limits: no dedicated VS Code Guide view; independent reviewer routing does not know who authored a chapter.

## 1.0.0 — 2026-10-12

- Crash consistency: project write lock plus undo journal across canonical files, audit log, orchestration state and the index; recovery on open. Concurrency and kill tests.
- Exclusive task execution: single-use launch wrappers holding a per-task lock for the provider's lifetime; execution states prepared/launched/running/completed/failed/unknown.
- One effective task specification (`research task context`) across CLI, RPC, MCP, editor and dispatch briefs; contradictions are surfaced and block dispatch/completion.
- One dispatch preparation path (specification, dependency, frozen-test, preflight) for plain and orchestrated dispatch; the project strategy governs by default, VS Code overrides only on explicit request. Plain dispatch under a strategy that needs frozen tests now requires them or `allow_unfrozen`.
- One verification/completion policy for `task verify`, `task done`, status updates and create-as-done; `passed`/`blocked`/`unverified`; `task verify` exits non-zero on failure.
- Home lists blocked tasks across all active plans; Start replaces Dispatch; charts no longer infer metric direction; findings show numerical evidence coverage separately from review.
- Agent docs state what MCP omits; wrong command examples fixed; a test parses every shipped example.
- Deferred: coordinator continuation, execution state in the panel, Windows verification. See `DESIGN_UX_REVIEW_1_0_0.md`.

### Hardening included in 1.0.0 (previously unreleased)

- Preserve malformed canonical files; abort conflicting or malformed rebuilds transactionally and keep canonical bytes unchanged. Back up the live index without replacing its inode.
- Serialize POSIX JSONL appends, refuse incomplete tails, surface corrupt complete records and fsync shared text writes. Roll back entity-index updates when canonical export fails.
- Close task completion and re-freezing bypasses; reject dependency cycles and serialize claims. Fail closed on corrupt orchestration ledgers and skill manifests.
- Apply preflight hooks across task dispatch paths; block required unapproved hooks and preserve advisory semantics. Require actual zero exit codes in experiment validation.
- Share hook/check subprocess cleanup and bounded output; report headless timeout failure. Fix local runner descriptor cleanup and remote-host cancellation checks.
- Validate RPC/MCP input shapes; fix extension RPC restart races, split UTF-8 decoding, startup timeout, stdin errors and refresh disposal.
- Remove obsolete config-migration helpers, unused constants and duplicate merge/check-process implementations. Remove setuptools from runtime dependencies and repair stale lock metadata.
- Isolate artifact installation tests; copy extension-test workspaces; refuse destructive demo recreation. Add release regression tests and client lifecycle tests.
- Rewrite the README and correct obsolete specification claims. Schema stays at 3.

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
