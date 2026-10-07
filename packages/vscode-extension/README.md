# Research Panel

Research memory for computational projects: **question → experiment → runs → artifacts/metrics → findings → decisions → checkpoint**,
plus optional **plans → tasks** for tracking the work itself.

- **Research Home** (`Cmd/Ctrl+Alt+R`): one page that answers "what are we trying to understand, what do we believe, what
  failed, what needs my attention, and what's next?"
- Sidebar: Focus, Plans (tasks nested), Findings, Experiments (runs nested), Questions, Decisions, Checkpoints, Notes,
  Artifacts, Agent Context.
- Everything lives in the project's `.research/` folder (Markdown/YAML + a rebuildable SQLite index). There's no cloud and no account.
- Runs locally (foreground or detached) or on SLURM. Works under Remote SSH because the extension runs on the remote host.
- Built for working with AI agents (Claude Code, Codex, Gemini, Copilot…): **dispatch** a task to any agent CLI, an
  **MCP server** with the research tools, task **checks** that gate completion, independent **reviews** before an agent's
  finding counts as supported, task claims and progress notes so several agents can share one queue, and project
  **skills** (import from a folder or GitHub) loaded where they're relevant.
- Compare two runs side by side, sweep parameters within the run budget, export a report with the evidence plots.
- Gives every project its own CLI at `.research/bin/research`, which any agent can run without installing anything,
  including the Claude Code extension's Bash tool. Plain `research` is also on `PATH` in VS Code terminals. Agents follow
  the same protocol (see `AGENTS.md`) and get a run budget that forces them to synthesize before running more.

Requires Python ≥ 3.8 on the machine that holds the workspace. The research core is bundled, and nothing needs to be installed with pip.
See the repository README for the usage guide and `docs/SPEC.md` for the full feature list and design.
