# Getting started

This guide takes you from nothing to a plan running under the supervisor, with an agent doing the work, an HPC cluster behind it if you have one, and Discord telling you what
happened. It uses the terminal; the terminal UI (and the optional VS Code extension) show the same project, so you can switch at any point.

1. [Install](#1-install)
2. [Your first project](#2-your-first-project)
3. [Set up your agents](#3-set-up-your-agents)
4. [Your first experiments](#4-your-first-experiments)
5. [Running on a cluster (HPC)](#5-running-on-a-cluster-hpc)
6. [Watching and steering](#6-watching-and-steering)
7. [From your phone: Discord and Telegram](#7-from-your-phone-discord-and-telegram)
8. [Approvals and the PIN](#8-approvals-and-the-pin)
9. [Usage limits](#9-usage-limits)
10. [Presenting results](#10-presenting-results)
11. [Git and agent permissions](#11-git-and-agent-permissions)
12. [Updating and migrating](#12-updating-and-migrating)
13. [Troubleshooting](#13-troubleshooting)

## 1. Install

You need Python 3.8 or newer. The CLI and terminal UI are on [PyPI](https://pypi.org/project/research-panel/) as `research-panel`.
If you use VS Code, download the extension (`research-panel-X.Y.Z.vsix`) from the
[GitHub releases page](https://github.com/yubrajbhandari923/research-vscode/releases/latest).

**The CLI** (in its own isolated environment, so it never touches your project's packages):

```bash
uv tool install research-panel
research version
```

With pipx instead: `pipx install research-panel`. If `research` is not found afterwards, add the tool directory to
your `PATH` (`uv tool update-shell` or `pipx ensurepath`) and open a new terminal. The same wheel is also attached to every GitHub release
(with `SHA256SUMS`) if you prefer to install from a file; see [other ways to install](#other-ways-to-install) below.

**Optional extras.** The example above already includes the terminal UI:

| Extra | Gives you |
|---|---|
| `[discord]` | the Discord bot (Python 3.10 or newer; Telegram needs no extra) |

Combine them as `research-panel[discord]`. Add one later by installing again with `--force`, for example `uv tool install --force "research-panel[discord]"`, or `pipx inject research-panel discord.py`.

### Other ways to install

**From the release wheel**, to pin an exact file, install without PyPI, or check what you install. Every GitHub release carries the same wheel that is on
PyPI, plus `SHA256SUMS`:

```bash
# download research_panel-X.Y.Z-py3-none-any.whl and SHA256SUMS from the release page, then
grep '\.whl$' SHA256SUMS | shasum -a 256 -c -           # Linux: sha256sum -c -
uv tool install "research-panel @ file:///full/path/to/research_panel-X.Y.Z-py3-none-any.whl"
# or straight from the release URL:
uv tool install "research-panel @ https://github.com/yubrajbhandari923/research-vscode/releases/download/vX.Y.Z/research_panel-X.Y.Z-py3-none-any.whl"
```

Without extras: `uv tool install ./research_panel-X.Y.Z-py3-none-any.whl`, `pipx install ./research_panel-X.Y.Z-py3-none-any.whl`, or `pip install` the file inside a
virtual environment. `research self-update --from-file WHEEL` installs a downloaded wheel (and checks it against a `SHA256SUMS` file if one is next to it).

**Building the wheel yourself** (needs no Node; the extension and tests are described in the repository README under *Development*):

```bash
git clone https://github.com/yubrajbhandari923/research-vscode.git && cd research-vscode
uv build                                        # or: python -m pip install build && python -m build
uv tool install "research-panel @ file://$PWD/dist/research_panel-X.Y.Z-py3-none-any.whl"
```

**The VS Code extension** (needs the CLI above):

```bash
code --install-extension research-panel-X.Y.Z.vsix
```

Or Extensions → `…` → **Install from VSIX…**. If you work over Remote SSH, install the extension on the remote host and the CLI there too.
The extension is a thin client: it runs your installed `research`. It looks at the `research.cliPath` setting first, then your `PATH`, `~/.local/bin`
and pipx's bin directory. If `research` is somewhere else, set **Settings → Research: Cli Path** to the full path (find it with `which research`) and reload the window.

**Versions must match.** The extension needs a CLI with the same major version and a minor version at least as new as the extension asks for
(for example an extension that needs core 3.1 works with CLI 3.1 or 3.2, not 3.0 and not 4.0). When they do not match, the status bar shows a warning
(`Research: version mismatch`, or `CLI not found`). Click it, or run **Research: Show Version Info**, to see what the extension needs, what it found and where.
"CLI is too old" means run `research self-update`; "extension is older than your CLI" means install the newer VSIX from the release. Reload the window afterwards.
`research version --json` shows exactly which CLI you have.

Updating later is covered in [section 12](#12-updating-and-migrating).

## 2. Your first project

```bash
research init sparse-ct --layout ledger --name "Sparse CT" --goal "Does the sampling pattern change reconstruction error?" --agents claude,codex
```

This is a preview: it lists what would be created and changes nothing. Add `--apply` to create it. `--agents` takes any of `claude`, `codex`, `agy`, `opencode`;
the first is the default.

```text
sparse-ct/
├── main/          your code: a git checkout of `main`
├── ledger/        the research records (.research/), on their own branch
├── experiments/   one git worktree per experiment, created on demand
└── Sparse-CT.code-workspace
```

All three are one git repository. Work from `main/`. Nothing is pushed; add a remote and push when you want to.
Initialization also links the research skills for your agents, registers the MCP server with them, approves nine cheap starter checks and creates
your [Scientist Guide](GUIDE.md).

Prefer a single folder? `cd` into an existing git repository and run `research init --goal "..."` (no `--layout`). You can move to the worktree layout later with
`research ledger enable` (preview) and `--apply`.

Check the result:

```bash
cd sparse-ct/main
research doctor
research status
```

`research doctor` is read-only. Each line is **ok**, **optional** (a suggestion, such as setting a PIN), **warning** or **error**, with the command that fixes it.
A fresh project normally shows no errors and a few optional items. Run it any time something seems wrong; `--all` also lists the checks that pass.
Agents should run the project's own launcher; refresh it with `research bin install` after you move the project or upgrade.

Python and cluster environments are described in [SETUP.md](SETUP.md).

## 3. Set up your agents

An agent is a command-line coding tool that does the work you assign: Claude Code (`claude`), Codex (`codex`), Antigravity (`agy`) or OpenCode (`opencode`).
Install and sign in to each one yourself, in its own way; Research OS never handles your provider login.

```bash
research agent setup claude
research agent setup claude --apply
research agent providers list
```

The first command is a preview. `--apply` writes the provider's agent files (for Claude, one agent per model level in `.claude/agents/`) and registers the MCP server.
Do the same for `codex`, `agy` or `opencode`. `research agent providers list` shows which ones are installed on this machine and what each supports.
Details per provider, including OpenCode's `--agent-model provider/model`, are in [PROVIDERS.md](PROVIDERS.md).

**Levels and the strategy.** Work is not all equally hard, so each task gets one of three levels:

| Level | Used for |
|---|---|
| `fast` | checkable, mechanical work (a function with tests, a plot script) |
| `balanced` | planning, coordinating, reviewing |
| `deep` | scientific synthesis and interpretation |

Each level maps to a model for your provider (for Claude: Haiku, Sonnet and Opus). A **strategy** decides which level handles which kind of task.
A new project has none selected, in which case the older tier settings apply. Choose the recommended one:

```bash
research strategy use balanced_first
research strategy show
```

`balanced_first` has the balanced model coordinate, the fast model implement work that has automatic checks, and the deep model do scientific synthesis only.
`research strategy show` prints the model each level resolves to, how tasks are routed, and when a failing task is escalated to the next level.
`research strategy list` shows the others (`deep_first`, `fast_max`, `balanced_heavy`). You can also pick one at setup time with `research agent setup claude --strategy balanced_first --apply`.
Mixing providers is described in [PROVIDERS.md](PROVIDERS.md).

## 4. Your first experiments

A **plan** is a YAML file listing the experiments and the tasks inside them. The supervisor runs the tasks in order, hands each to an agent, checks the result
itself and stops when it needs you. Here is a small plan with four tasks, including an ablation (a repeat with one ingredient changed, to show the effect is real).
Save it as `plans/research.yaml` in `main/`:

```yaml
schema_version: 1
plans:
  - key: sampling
    title: Does the sampling pattern change reconstruction error?
    objective: Compare radial and uniform sampling, and check that the gain is not an artefact of the solver.
    execution:
      mode: bounded_autonomous
      max_parallel_agents: 1
      max_attempts_per_task: 3
      on_failure: diagnose_and_repair
      require_approval_for_scientific_changes: true
    experiments:
      - key: pattern-study
        title: Radial versus uniform sampling
        hypothesis: Radial sampling gives a lower reconstruction error than uniform sampling at the same number of views.
        tasks:
          - key: simulator
            title: Write the simulator
            prompt: |
              Write sim.py. Usage: python sim.py --pattern radial|uniform --views N --seed S.
              It reconstructs a test phantom and writes results/<pattern>-<views>-<seed>.json with {"rmse": ...}.
            checks:
              - "cmd:python3 sim.py --pattern radial --views 30 --seed 0"
              - "file:results/radial-30-0.json"
          - key: compare
            title: Compare radial and uniform
            depends_on: [simulator]
            prompt: |
              For views = 30, run both patterns with seeds 0-4. Register every run with
              `research run exec`, and write results/comparison.png (mean RMSE with error bars).
            expected_artifacts: [results/comparison.png]
          - key: ablation
            title: Ablation, solver iterations
            depends_on: [simulator]
            prompt: |
              Repeat the radial runs with the solver limited to 5, 20 and 100 iterations. Report whether
              the advantage over uniform sampling survives with fewer iterations.
            expected_artifacts: [results/ablation.png]
          - key: analyze
            title: Write up the result
            depends_on: [compare, ablation]
            agent: {role: analyst, profile: claude-deep}
            prompt: |
              Summarise both comparisons, with limitations. Do not claim more than the runs show.
```

Each task has a `prompt` (what the agent is told), optional `depends_on`, and **checks** the supervisor runs itself: `cmd:` runs a command, `file:` expects a file,
and `expected_artifacts` become file checks. A task is only done when its checks pass; the agent saying "finished" is not enough.
`research plan yaml template -o plans/research.yaml` writes a documented starting file instead. The full schema is in [SUPERVISOR.md](SUPERVISOR.md).

Validate, preview, apply:

```bash
research plan yaml validate plans/research.yaml
research plan yaml diff plans/research.yaml
research plan yaml apply plans/research.yaml
```

Validation lists every problem at once. `diff` shows what would be created without changing anything. After `apply` the plan is `PLAN-001`, with tasks `T-001` to `T-004`
and experiment `EXP-001`:

```bash
research plan status PLAN-001
```

Start the supervisor on it:

```bash
research plan run PLAN-001
```

This starts the supervisor process if it is not already running. It launches the agent for each task whose dependencies are done (here `simulator` first, then
`compare` and `ablation`, then `analyze`), runs the checks, retries within the limits you set, and stops with an alert when it cannot continue.
Starting a plan is a human action: it is refused from inside an agent's shell. Running it spends your provider usage, so start with a small plan like this one.

To see what happens, use these (they never change anything):

```bash
research plan status PLAN-001
research supervisor status
research task show T-001
research run list
research experiment show EXP-001
research report
```

`plan status` shows each task's phase and attempts; a halted plan says **HALTED** and why. When a task finishes, its result is on the experiment and its runs.
`research report` writes a summary of everything recorded. Pause with `research plan pause PLAN-001`, continue with `research plan resume PLAN-001`.
If the supervisor is not running (after a reboot, say), `research supervisor start` brings it back; after an upgrade use `research supervisor restart`.

Results are not conclusions. Look at the numbers yourself, then record what you believe with `research experiment synthesize`, `research finding create` and
`research decision create` (see the reference manual's [synthesize and decide](REFERENCE.md#synthesize-decide-and-resume) section). An agent's finding stays preliminary until an independent review.

Steer a task in plain words at any time: `research supervisor instruct T-002 --message "use 60 views instead of 30"` (an assistant agent turns the sentence into actions). Requests that touch science or resources become a proposal
that waits for your PIN (section 8).

## 5. Running on a cluster (HPC)

Skip this if you only run locally. Research OS submits to Slurm over SSH using your own OpenSSH setup (keys, `ControlMaster`, jump hosts live in `~/.ssh/config`; Research OS stores no credentials).

**Once per cluster.** Describe it in the project config, then check it:

```bash
research hpc config --example      # prints a commented hpc.clusters block: ssh alias, approved storage roots, partition/account, python, job setup lines
research hpc doctor                # SSH reachable? directories? sbatch/squeue/sacct? Python on the login node?
research hpc doctor --compute      # also proves the job environment on a compute node (a one-minute, one-CPU srun)
research hpc init                  # creates this project's remote directory under your approved root (never deletes or overwrites)
```

Only the storage roots you name are used (`$HOME` is never assumed). Put what a compute node needs (modules, a venv) in `hpc.clusters.<name>.slurm.setup`. If several projects use one
cluster, each needs its own `hpc.clusters.<name>.project.slug`; a remote directory another project created is refused.

**Per run.** Agents submit through Research OS, never with raw `sbatch`, so every job has a run record before anything is uploaded:

```bash
research hpc submit EXP-001 --dry-run -- python train.py     # what would be uploaded and requested; submits nothing
research hpc submit EXP-001 --yes -- python train.py         # submit (limits from your config are enforced)
research hpc status RUN-0001
research hpc sync                                            # update status and bring finished results home (safe to repeat, also after days offline)
```

A job writes its outputs under `$RESEARCH_RUN_DIR/outputs/`, metrics to `$RESEARCH_RUN_DIR/metrics.jsonl` and lists files in `artifacts.jsonl`. Small files come home; large files stay on the
cluster as references. A dropped connection never resubmits a job: the submission receipt is read back instead. No SSH at all? `research hpc bundle EXP-001` makes a portable archive and
`research hpc import ARCHIVE` brings its results back.

**Under a plan.** A task with `resources: {backend: slurm}` waits while its job runs (the supervisor syncs every `supervisor.hpc_poll_seconds`) and is verified when the run is terminal.
A job that finishes before its results are collected is **not** a failure: verification waits (up to `supervisor.recovery.collection_wait_minutes`), and a task blocked on missing
evidence is re-verified automatically, under the unchanged checks, when the results arrive. Nothing is resubmitted, and no threshold, budget or check is ever changed for you. The
supervisor never submits more than the plan allows (`max_hpc_resubmissions`). Runs imported by an older version: `research hpc reconcile`.

## 6. Watching and steering

**The plan board.**

```bash
research watch            # every plan, live; read-only, safe to leave open in a pane
research plan status      # the same picture once
```

It shows each plan the way its file is organised: experiments, their tasks, progress and budgets. A task that is running shows what its agent did last and
how long ago; one that waits shows what it waits for; one that needs you shows why and the exact command (`research bump T-023`, `research plan sync study`,
`research supervisor approve T-004`). A promotion waiting for review shows whether its reviewer is alive. "Needs you" at the bottom is the whole list.

**What the agents are doing.** `research logs` lists every attempt (running ones first, with their latest step). `research logs T-023` prints the steps of the
latest attempt: what the agent said, each tool call, shortened results, errors in full. `-f` follows a running agent, `--attempt 1` reads an earlier one,
`--full` cuts nothing, and `research logs AP-001` reads a promotion's independent reviewer. This is read from the provider's own live session record, so it
works while the agent runs, not only after it exits.

**Changing a running plan.** Edit `plans/<key>.yaml` and save. Budgets, attempts, prompts, profiles and resources apply on the supervisor's next pass; new or
removed tasks, checks and acceptance wait for `research plan sync` (see the README table). `research bump T-023` is the one-word fix for "attempt budget exhausted".

**Leaving it.** `research background check` says whether the run survives closing your terminal or SSH session; `research background install` makes it a
user service that also survives reboots. `research start PLAN --unattended` applies the overnight settings first.

**Settings** have no screen of their own: `research config show`, `research config set KEY VALUE`, `research config explain KEY`, and
`research profile apply overnight|interactive` for the common bundles.

**Decisions**, **Plans & approvals**, **Worktrees**
(fork, archive, promotion), **Agents**, **Guide & site** (create, build, publish and unpublish experiments), **Settings** (every setting, with its help, default and where it is set;
changes go through the same preview and approval path, PIN included) and **System** (doctor, chat, HPC). Digits `1`-`9` and `g`, `s`, `y` jump to a section; `Ctrl+P` finds any section,
setting or inbox item by typing a few letters. It refreshes by itself, works in a narrow terminal (the navigation folds away and the status bar names your section), and has a light theme (`Ctrl+T`).

**Start with the Inbox (`i`).** It gathers everything that needs a person, from every part of ResearchOS, and keeps three kinds apart:
**Action** (something is waiting for you: a decision, an approval, a blocked task, evidence that changed or is missing), **Recommended follow-up** (a scientific step is due
but nothing is stuck: a synthesis, a finding awaiting independent review, artifacts that cannot be verified), and **Informational** (nothing to do, for example a planned
experiment). Each row says where it comes from, why it matters, whether it blocks execution and the next step. `Enter` or `x` opens the actions for the row; `w` explains what
it means. An experiment that says `needs_review` is a follow-up about interpretation, not a decision request, so Decisions can be empty while the Inbox is not.

For an experiment that is due for **synthesis** the actions follow the division of work: *Check evidence* (automatic), *Prepare analyst draft* (writes a brief; **starts nothing**),
*Launch analyst* (starts a real agent after you confirm; it may cost money), *Record synthesis* (a text box prefilled with the recorded facts: you write and review the
interpretation, nothing is recorded until you save and confirm, and it never closes the experiment) and *Mark completed* (a separate, explicit step). For artifacts the TUI says
whether each file is local, collected, inaccessible (external storage this machine cannot reach), missing or modified, and why: a Slurm job that exited 0 does not prove its files are
here. Nothing is rewritten; *Re-check* and *Sync HPC runs* are offered. The Records tab shows an experiment's lifecycle (`proposed → ready → running → needs_review → completed`) and what drives it.
`?` opens the built-in guide from any screen (the research loop, statuses, who does what, reviews versus approvals, keys, troubleshooting).

| Key | Does |
|---|---|
| `i` | open the Inbox |
| `Enter` or `x` | actions for the selected Inbox row |
| `w` | what does the selected status or item mean? |
| `1`-`9`, `g`, `s`, `y`, or `[` and `]` | jump to a section (on Decisions, the digits pick an option) |
| `Ctrl+P` | find a section, a setting or an inbox item |
| `r` | refresh |
| `/` | filter the rows (an empty filter clears it) |
| `k` | cancel the selected run |
| `a` / `d` | approve or reject the selected proposal (asks for your PIN) |
| `t` | on Decisions: answer in your own words |
| `Ctrl+T` | dark or light theme |
| `?` | the guide (← → move between its sections) |
| `q` | quit |

The optional VS Code extension (`.vsix` on each release) shows the same project in panels; everything in this guide works without it.

**Decisions.** When the system cannot go on without you (a halted task, a diagnosis with alternatives, a question an agent cannot settle) it asks one question, a
*decision request*, and shows it on every surface at once: the terminal, the TUI Decisions tab, VS Code, and chat. Each option says what it will do.

```bash
research decisions
research decide DR-001
research decide DR-001 2
research decide DR-001 "skip T-3 and carry on"
```

Those show the open requests, show one with its options, pick option 2, and answer in free text (the supervisor turns your sentence into actions).
In chat, options are buttons and a reply to the message is a free-text answer. The first answer closes the request everywhere. Options that touch science, resources or promotion are
protected: they run only at your computer, with your PIN. A request that expires picks nothing.

## 7. From your phone: Discord and Telegram

Chat is optional. It sends alerts (plan started and finished, each experiment's results, failures, halts, approvals and questions) and lets you answer and steer with short commands (`/status`,
`/tasks`, `/say`, `/ask`). How much is pushed is `notifications.level`: `low` (plan start/end and failures), `medium` (default: plus each experiment's results and anything waiting for you) or
`high` (plus every run and task).

```bash
research config set notifications.level medium      # or /notify in chat
research chat doctor                                # what is set up, what is missing, the exact fix for each (add --live to ask Discord/Telegram)
```

### Discord: once per machine, then one channel per project

**On the machine, once** (the bot, the server and who may talk to it):

1. Install the extra: `uv tool install "research-panel[discord]"` (Python 3.10+).
2. In the [Discord developer portal](https://discord.com/developers/applications) create an application, add a bot, copy its token. No privileged intents are needed.
3. In your first project run `research chat setup discord`. Paste the token at the hidden prompt; it prints an invite URL (scopes `bot` and `applications.commands`, minimal permissions): open it and
   add the bot to your server.
4. Send the bot a direct message; it replies with a pairing code. Type it at the prompt (or `research chat pair CODE` later). Pair people as `viewer`, `operator` or `approver`.

The token is stored only on this machine (mode 0600). The gateway, one background process for all your projects, starts by itself.

**Per project** (its own channel in the same server): create a channel, copy its id (Discord → Settings → Advanced → Developer Mode, then right-click the channel → Copy Channel ID), and in that project:

```bash
cd ../other-project/main
research chat setup discord --channel 123456789012345678
```

The second and later projects reuse the saved bot (no token prompt) and map themselves to their channel. Alerts and commands in that channel belong to that project; the same bot, pairing and PIN
serve all of them. You can also type `/sethome` in a channel to make it the default for alerts that belong to no particular project. With `notifications.discord.thread_per_task` (default on) each task gets
its own thread inside the project channel. Chat names a project by its folder, or by the folder around `main` (`PRAVAH/main` is `PRAVAH`).

**Telegram** works the same way: create a bot with @BotFather, run `research chat setup telegram`, paste the token, then send the bot any message; that chat is the first paired user.

**What chat can do:** read status, logs and reports; answer decision requests; give the supervisor instructions; pause, resume or hand a task to another agent; ask a read-only agent (`/ask`).
**What it cannot:** approve a protected change, create experiments, change policy, approve promotion or accept unverified work; those happen only at your computer. Only paired people are obeyed.
To approve proposals from your phone, enrol an authenticator (`research approval chat-enroll`, needs your PIN) and enable `governance.approval.chat.enabled`; the PIN itself is never requested through chat.
Commands, levels and troubleshooting: [CHAT.md](CHAT.md).

## 8. Approvals and the PIN

Some actions are protected because they change what counts as scientific truth or what reaches `main`: approving an agent's proposal, changing policy settings, approving or merging a promotion,
and the settings the config marks "needs a human". They always need a human, never an agent, and they ask for your approval PIN.

```bash
research approval set-pin
research approval status
```

`set-pin` asks twice, in a real terminal, with no echo. It is stored as a salted hash in your machine config directory, never in the project. Set it once per machine.

**With no PIN set, protected approvals are refused** ("No approval PIN is set on this machine"). In a real terminal the first protected action offers to create one. In VS Code, over
RPC or anywhere without a terminal it is simply refused until you set one, in a terminal or under **Settings → Approval PIN**. To work without a PIN on purpose, put this in the machine file
(`~/.config/research-os/config.yaml`):

```yaml
governance:
  approval:
    require_pin: false
```

Protected actions then still need a human and a written reason. The PIN is a guard against accidents, not authentication: an agent running as your user could read your files, so keep agents inside worktrees
and review what they do. Forgot the PIN? Delete `approval.json` in `~/.config/research-os/` and set a new one. More in [GOVERNANCE.md](GOVERNANCE.md).

## 9. Usage limits

Providers cap usage per 5 hours or per week. By default a limit stops the task and asks for you. To let the supervisor wait and carry on by itself:

```bash
research config set supervisor.usage_limit.on_limit wait --scope machine --reason "overnight runs"
```

This setting needs a human, so it needs your PIN (or `require_pin: false`, section 8); in VS Code use **Settings**. The values:

| Value | Meaning |
|---|---|
| `halt` (default) | the task stops and waits for you |
| `wait` | tasks on that provider pause until it resets, then relaunch; tasks on other providers keep running |
| `fallback_then_wait` | first try the next provider in `agents.handoff.fallback_order`, otherwise wait |

A limited attempt does not count against a task's attempt budget. The supervisor adds `wait_buffer_seconds` (default 120) after the reset time. A reset further than `max_wait_hours` (default 8, so a
weekly limit) is never waited out unattended: you get a decision request to wait, switch provider or stop. When the reset time cannot be read, it retries after 15, 30, then 60 minutes.
Reset times are read from Claude Code, Codex, OpenCode and Antigravity messages; the formats each one uses are in [PROVIDERS.md](PROVIDERS.md). With chat on, you get one alert when the wait starts and one when it resumes.
Everything else about it is in [SUPERVISOR.md](SUPERVISOR.md).

## 10. Presenting results

`research report` records everything. The **Scientist Guide** is the short story you tell: a [Quarto](https://quarto.org) book in `docs/scientist-guide/` that builds to a website. A new project already has one.

```bash
research guide status
research guide highlights
research guide quarto doctor
research guide quarto build --edition scientific
```

Preview and publish it as a website with `research site`:

```bash
research site status          # what exists, and the exact next commands
research site preview         # live local site, no GitHub needed (over SSH it prints the port-forward line)
research site add EXP-001     # publish a verified experiment's committed report and figures as an editable draft page
research site remove EXP-001  # unpublish (nothing is deleted); previews until you add --apply
```

Research OS never commits or pushes: after you commit the guide and `.github` and `git push`, the generated GitHub Actions workflow deploys the site (once: GitHub → Settings → Pages → Source: GitHub Actions).

Results an independent review marks supported (or that you star with `research guide star F-001`) are logged into it automatically. An agent writes a short draft only when you ask, within word limits you set
(`research guide draft check` reports any overrun). Add references with `research guide cite add --bibtex "..."` and cite them as `[@key]`. Quarto is needed only to build. See [GUIDE.md](GUIDE.md).

## 11. Git and agent permissions

**What is uncommitted, and why it matters.** Experiment worktrees start from the last commit, so a plan, script or setting you have not committed is invisible to agents and not reproducible.

```bash
research git status                # main repo, experiment worktrees, promotions and the ledger, each file with the reason it matters
research git preflight             # the check `plan run`, `plan resume` and promotion run for you (git.preflight: warn by default, block, or off)
research git commit --scaffold     # shows the staged diff of the files Research OS generated (AGENTS.md, CLAUDE.md, .gitignore ...)
research git commit --scaffold --yes
research git ignore                # previews a managed .gitignore block for caches and internal files; --apply writes it
```

A commit needs `--yes` after you have seen the diff, commits exactly the files named and nothing else, refuses secrets and caches, can only be run by a person and never pushes. Verified agent work is
committed on the experiment's own branch automatically (small figures and reports in, bulky outputs and secrets out); moving it to `main` is promotion, a separate human-gated step.

**What a headless agent may run.** The default is edits and the `research` CLI only, never arbitrary Bash. Instead of giving an agent Bash, let it run a task's declared checks through Research OS:
`research agent check T-001` (read-only). To allow one more command on purpose, add it to the profile: `agents.profiles.<name>.allow_commands: ["python scripts/smoke.py *"]`. If an agent is refused a command
the task is blocked as `permission_denied` with the commands and the fix; nothing is resubmitted, and after you change the setting `research supervisor resolve T-001 --unblock` continues with a new attempt
(the failed one stays on record).

## 12. Updating and migrating

```bash
research self-update --check
research self-update
```

`--check` only compares your version with the latest release. `self-update` downloads the newest wheel from the GitHub release, verifies it against the release's `SHA256SUMS`, and reinstalls with the tool that
installed it (uv, pipx or pip). If anything fails, including a checksum mismatch, your existing install is left untouched. A new **major** version can break things, so it is refused unless you add `--major`.
`--version X.Y.Z` installs a specific release, and `--from-file WHEEL` installs a wheel you downloaded (offline), checking a `SHA256SUMS` file beside it. It also restarts supervisors it knows about.

**Existing projects need no migration.** New settings take their defaults and stored plans and runs are read as they are. After an upgrade, in each project:

```bash
research supervisor restart            # a running supervisor keeps the old code until restarted
research agent instructions --diff     # preview the refreshed AGENTS.md block (only the text between the research markers changes)
research agent instructions            # apply it (or: research doctor --fix agents_md.stale)
research bin install --force           # only if agents use the project's launcher and you reinstalled elsewhere
research git status                    # optional: see what is uncommitted; research git ignore --apply adds the managed .gitignore block
```

Chat gateway code is refreshed automatically (`research chat gateway restart` forces it). If you use the VS Code extension, install the `.vsix` from the same release and reload the window.

## 13. Troubleshooting

| Symptom | What to do |
|---|---|
| `research: command not found` | Run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal; in VS Code set `research.cliPath` |
| "version mismatch" in VS Code | Click it. CLI too old: `research self-update`. Extension older: install the newer VSIX. Reload the window |
| "No approval PIN is set" | `research approval set-pin` in a terminal, or `governance.approval.require_pin: false` in the machine config |
| No Telegram or Discord messages | `research chat status`, then `research chat gateway restart` |
| Plan does not move | `research supervisor status`, then `research supervisor start`; `research plan status PLAN-001` says why it halted |
| Not sure the install is healthy | `research doctor` |
| A task says results are not collected yet | Slurm finished but the results are not home: `research hpc sync` (or `research hpc collect RUN`). It is re-verified automatically; nothing is resubmitted |
| Task blocked `permission_denied` | The headless agent was refused a command: `research agent check T-001`, or `agents.profiles.<name>.allow_commands`; then `research supervisor resolve T-001 --unblock` |
| Plan shows 3/3 verified but not completed | An authorized promotion is paused or suspended: `research plan autopromote PLAN-001 status` shows the reviewer's objection; fix it, then `... retry --item AP-001` |
| Second project's alerts land in the wrong Discord channel | `research chat setup discord --channel ID` inside that project; `research chat doctor` |
| `belongs to another project` on HPC | Two projects share a remote directory name: set `hpc.clusters.<name>.project.slug` to something unique in one of them |
| Uncommitted files warning before a plan | `research git status`; commit what matters with `research git commit --paths notes.md --yes` or `--scaffold` |

More causes and fixes are in [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Providers and configuration: [PROVIDERS.md](PROVIDERS.md), [CONFIGURATION.md](CONFIGURATION.md).
