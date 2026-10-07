# Changelog

## Unreleased

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
