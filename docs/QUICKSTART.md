# Quick start

Install the CLI first (README, Install). Everything below works from a terminal; VS Code and `research tui` are optional interfaces to the same project.

## Terminal only

```bash
research project-init sparse-ct --name "Sparse CT" --goal "What you are trying to find out" --agents claude,codex   # preview
research project-init sparse-ct --name "Sparse CT" --goal "What you are trying to find out" --agents claude,codex --apply
cd sparse-ct/main
research question create "Which sampling pattern recovers the most detail?"
research experiment create "pattern study" -q Q-001 --hypothesis "Radial beats uniform" --planned-runs 3
research task create PLAN-001 "Implement the simulator" --check 'cmd:python -m pytest -q'   # after `research plan create "..."`
research task dispatch T-001 --profile claude                  # prepares a brief and a launch command
research task verify T-001                                      # completed is not verified
research tui                                                    # dashboard, runs, approvals (needs the tui extra)
```

Protected actions (approving proposals, promotion) ask for your approval PIN on the terminal; the first one offers to create it. Each step has a VS Code equivalent below.
`research doctor` checks the project, and `research --help` lists every command.

## With VS Code

> **Before your first run:** set up your Python (and cluster) environment once, see [SETUP.md](SETUP.md).

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
9. **Guide** tab: tell the science. Results an independent review marked *supported* (or that you star) are logged into your Scientist Guide, a Quarto book on `main`; ask an agent for a short draft (within your word limits), add citations in `references.bib`, then push to publish it as a website. Details: `docs/GUIDE.md`.

10. **Plans, the supervisor and approvals** (CLI-first): `research plan yaml validate FILE`, then `research plan run PLAN`; `research supervisor status` shows
    progress and says HALTED when a plan cannot continue. Steer with `research supervisor instruct T-12 "…"`. Protected actions (approving proposals, promotion,
    scientific changes) ask for your approval PIN on the terminal; the first one offers to create it. Optional: `research telegram setup` for alerts and chat commands.
    `./research` is linked in `main/` so you can run it from there. Details: [SUPERVISOR.md](SUPERVISOR.md), [GOVERNANCE.md](GOVERNANCE.md).

Providers and configuration: `docs/PROVIDERS.md`, `docs/CONFIGURATION.md`.

Existing classic projects keep working. To move one to the worktree layout: `research ledger enable` (dry run), then `--apply`.
