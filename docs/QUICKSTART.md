# Quick start

1. Create an empty folder (for example `~/Code/sparse-ct`) and open it in VS Code.
2. Run **Research: Initialize Project**. The wizard teaches the model, asks for a name and goal, folder names, agents, optional HPC, and shows exactly what it will do. Nothing is written until you press *Create*.
3. VS Code reopens `sparse-ct.code-workspace`:

```
sparse-ct/
├── main/          git checkout of `main`  (GitHub's root)
├── ledger/        branch research/ledger, holds .research/
├── experiments/   one worktree per experiment (EXP-001/ …), created on demand
└── sparse-ct.code-workspace
```

All three are one git repository. `main/` is the checkout root, so files in it appear at the top level of GitHub's `main`.
Nothing is pushed for you; add a remote in the wizard (or later) and push from `main/`.

4. **Research: New experiment** (question, hypothesis). A worktree is created in `experiments/` (the box is on by default; dispatch to an implementer is refused without one, with a one-click *Create worktree*).
5. Add a plan and tasks with acceptance checks.
6. Open a task → **Dispatch**: choose Agent (Claude Code, Codex, Antigravity), Model, Mode (Interactive or Headless), then *Preview* or *Start*. Routed tasks that a cheaper model implements need **frozen acceptance tests** first: dispatch offers *Freeze test files…* (or a logged *Dispatch without frozen tests…*). Plans, tasks and Dispatch are also on the **Work** tab. The agent runs inside that experiment's worktree. Watch it under the **Agents** tab.
7. When it completes, run **Verify**. Completed is not verified.
8. Want a different agent for the next step? Dispatch the same task with another provider; the history carries over.

CLI equivalents: `research project-init FOLDER --agents claude,codex --apply`, `research dispatch implementer T-001 --profile codex`,
`research execution list`, `research provider list`. Details: `docs/PROVIDERS.md`, `docs/CONFIGURATION.md`.

Existing classic projects keep working. To move one to the worktree layout: `research ledger enable` (dry run), then `--apply`.
