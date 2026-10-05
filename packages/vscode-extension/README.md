# Research Panel

Research memory for computational projects: **question → experiment → runs → artifacts/metrics → findings → decisions → checkpoint**.

- **Resume Project** (`Cmd/Ctrl+Alt+R`): one screen that recovers project context in under two minutes.
- Sidebar: Overview, Questions, Experiments (runs nested), Findings, Decisions, Checkpoints, Notes, Artifacts, Agent Context.
- Everything lives in the project's `.research/` folder (Markdown/YAML + a rebuildable SQLite index). There's no cloud and no account.
- Runs locally or on SLURM. Works under Remote SSH because the extension runs on the remote host.
- Adds a `research` CLI to VS Code terminals, so coding agents follow the same protocol (see `AGENTS.md`).

Requires Python ≥ 3.8 on the machine that holds the workspace. The research core is bundled, and nothing needs to be installed with pip.
See the repository README for full documentation.
