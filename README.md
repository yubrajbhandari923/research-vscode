# Research OS

A command-line tool that keeps the reasoning of a computational research project next to its code: what you meant to find out, what was run, what the
results were, what you concluded and what should happen next. Agents (Claude, Codex, Gemini/agy, OpenCode) do planned work under a supervisor that verifies
it with programs, not opinions; you keep every scientific decision. It runs experiments locally, on Slurm clusters over SSH, or as portable bundles, and
talks to you through a terminal UI, Discord or Telegram.

The `research` CLI is the product. The terminal UI (`research tui`), the Python API, the MCP server and the optional VS Code extension are interfaces to the
same project folder. Records are plain text files; SQLite is only an index. Nothing needs a cloud service.

```text
Question → Experiment → Runs → Metrics / Artifacts → Synthesis → Finding → Decision        (what you learned)
Plan → Tasks → Agents → Verification → Promotion to main                                    (the work that was done)
```

The two are kept apart on purpose: a task that verified is not a claim that the science is right.

## Install

```bash
uv tool install "research-panel[tui]"      # or: pipx install "research-panel[tui]"; add ,discord for the Discord bot (Python 3.10+)
research version
```

Update with `uv tool upgrade research-panel` (or `research self-update`). After an upgrade, run `research supervisor restart` in each project: a running
supervisor keeps the old code. Other ways to install (release wheel with checksums, from source) are in [Getting started](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/QUICKSTART.md#1-install).

## Start (5 minutes)

```bash
# 1. a project (the first command previews; --apply creates it)
research init sparse-ct --layout ledger --name "Sparse CT" --goal "What you are trying to find out" --agents claude
research init sparse-ct --layout ledger --name "Sparse CT" --goal "What you are trying to find out" --agents claude --apply
cd sparse-ct/main && research doctor

# 2. an agent and a model strategy (you install and sign in to the agent's own CLI first)
research agent setup claude --apply
research strategy use balanced_first

# 3. a plan: validate, apply, run
research plan yaml template -o plans/research.yaml
research plan yaml validate plans/research.yaml
research plan yaml apply plans/research.yaml
research plan run PLAN-001

# 4. watch and steer
research tui
research plan status PLAN-001
```

`research init --template -o research-os.yaml` writes a fully commented project file with every setting and its default (a copy is in
[examples/](https://github.com/yubrajbhandari923/research-vscode/blob/main/examples/research-os.yaml)); edit it and `research init --from research-os.yaml --apply`.

## What you control

- **Governance.** Changes to a plan's science, resources or checks are proposals that need your approval PIN (`research approval set-pin`). Agents cannot approve, and
  chat never sees the PIN. See [Governance](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/GOVERNANCE.md).
- **Verification.** The supervisor runs each task's declared checks (files, metrics, commands) and only then marks it verified. Verified code is committed on the
  experiment's own branch; promoting it to `main` is a separate, human-gated step (tests, an independent review, your approval).
- **Bounded autonomy.** Attempt, time and HPC-resubmission budgets, an escalation ladder and usage-limit waits keep an unattended run from running away.
- **Headless agents are restrictive by default:** edits and the `research` CLI only. `research agent check TASK` lets an agent run a task's declared checks without
  Bash; `agents.profiles.<name>.allow_commands` allows a specific command on purpose.

## HPC (Slurm over SSH)

One-time, per cluster: describe it, then check it.

```bash
research hpc config --example        # a commented hpc.clusters block: ssh alias, approved storage roots, partition, python, job setup
research hpc doctor                  # SSH, directories, Slurm, Python on the login node (add --compute to prove a compute node)
research hpc init                    # creates this project's remote directory (never deletes or overwrites)
```

Per run: agents submit through Research OS (`research hpc submit EXP-001 --yes -- python train.py`), never with raw `sbatch`. Status and results come home with
`research hpc sync` (the supervisor does this for you while a task waits). Large files stay on the cluster as references.

- **A job that finishes before its results are collected is not a failure.** Verification waits (bounded), and a task blocked on missing evidence is re-verified
  automatically, under the unchanged checks, when the results arrive. Nothing is resubmitted and no check is relaxed.
- **Several projects share one cluster safely:** each has its own remote directory (`project.slug`); a directory another project created is refused.
- Runs imported by an older version: `research hpc reconcile`. Manual workflow without SSH: `research hpc bundle` / `research hpc import`.

## Discord and Telegram

Chat is optional. It is how a running plan reaches your phone: alerts, results, approvals and questions, and short commands (`/status`, `/tasks`, `/say`, `/ask`).

```bash
pip install "research-panel[discord]"            # or install with the extra from the start (Python 3.10+)
research chat setup discord                      # first project on this machine: bot token, server, pairing
research chat doctor                             # what is set up, what is missing, the exact fix for each
research config set notifications.level medium     # low | medium | high: how much is pushed (also /notify in chat)
```

**One bot per machine, one channel per project.** The bot token, the server and who may talk to it (pairing) are set up once per machine. Every further project only
needs its own channel in the same server:

```bash
cd ../other-project/main
research chat setup discord --channel 123456789012345678     # reuses the saved bot; maps this project to that channel
```

Alerts and commands in that channel belong to that project; the same bot answers in all of them. Telegram works the same way (`research chat setup telegram`).
Details, the command list and troubleshooting: [Chat guide](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/CHAT.md).

## Several projects at once

Run as many projects as you like on one machine. Each has its own supervisor process and state; they share the chat gateway (one process), the bot, your approval PIN
and the cluster's Slurm queue. Chat names a project by its folder (`PRAVAH/main` is `PRAVAH`). See
[Several projects on one machine](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/CHAT.md#several-projects-on-one-machine).

## Git, kept clean

`research git status` shows what is uncommitted in your main repository, in each experiment worktree and in the ledger, and why each file matters (experiment worktrees start
from the last commit, so uncommitted plans and code are invisible to agents). Before a plan runs, a preflight says the same. `research git commit --scaffold` shows the
staged diff of the files Research OS generated and commits them only after `--yes`. Research OS never pushes, never rewrites history and never commits arbitrary files on `main`.

## When something is wrong

```bash
research doctor                      # project, agents, environment, launcher; --fix agents_md.stale refreshes AGENTS.md
research plan status PLAN-001        # per task: phase, reason, root cause, what needs you
research supervisor status           # is the supervisor running? `research supervisor restart` after an upgrade
research chat doctor --live          # Discord / Telegram
research hpc doctor                  # cluster
research git status                  # what is uncommitted and why it matters
```

A halted plan says which task needs you and why. Typical causes: a task waiting for results (`research hpc sync`), a permission the headless agent was refused
(`research agent check`, `allow_commands`), a paused promotion with the reviewer's objection (`research plan autopromote PLAN-001 status`), or a decision waiting
for you (`research decisions`). More: [Troubleshooting](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/TROUBLESHOOTING.md).

## Documentation

| | |
|---|---|
| [Getting started](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/QUICKSTART.md) | install, first project, agents, plans, HPC, Discord, approvals, updating, debugging |
| [Reference manual](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/REFERENCE.md) | every feature and command in detail |
| [Specification](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/SPEC.md) | what the system does and why: lifecycles, storage, supervisor, governance (chapters in [docs/spec](https://github.com/yubrajbhandari923/research-vscode/tree/main/docs/spec)) |
| [Supervisor](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/SUPERVISOR.md) | plans, verification, recovery, promotion, permissions, git hygiene |
| [Chat](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/CHAT.md) | Discord and Telegram: setup, commands, levels, several projects |
| [Governance](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/GOVERNANCE.md) | the approval PIN, proposals, what agents can and cannot do |
| [Providers](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/PROVIDERS.md) · [Skills, hooks and MCP](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/AGENT_TOOLS.md) | agent CLIs, model levels, tools |
| [Scientist Guide](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/GUIDE.md) | the Quarto report and research website |
| [Troubleshooting](https://github.com/yubrajbhandari923/research-vscode/blob/main/docs/TROUBLESHOOTING.md) · [Changelog](https://github.com/yubrajbhandari923/research-vscode/blob/main/CHANGELOG.md) | |

An optional VS Code extension (`.vsix` on each release) shows the same project; the CLI and terminal UI are the main interface. To work on Research OS itself:
`python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' build`, then `python -m pytest -q`.
