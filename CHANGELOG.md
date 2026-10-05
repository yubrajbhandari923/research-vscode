# Changelog

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
