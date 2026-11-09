# Getting started

This guide takes you from nothing to a plan running under the supervisor, with an agent doing the work and you steering.
It uses the terminal. VS Code and the terminal UI show the same project, so you can switch at any point.

1. [Install](#1-install)
2. [Your first project](#2-your-first-project)
3. [Set up your agents](#3-set-up-your-agents)
4. [Your first experiments](#4-your-first-experiments)
5. [Watching and steering](#5-watching-and-steering)
6. [From your phone](#6-from-your-phone)
7. [Approvals and the PIN](#7-approvals-and-the-pin)
8. [Usage limits](#8-usage-limits)
9. [Presenting results](#9-presenting-results)
10. [Updating](#10-updating)
11. [Troubleshooting](#11-troubleshooting)

## 1. Install

You need Python 3.8 or newer. The CLI and terminal UI are on [PyPI](https://pypi.org/project/research-panel/) as `research-panel`.
If you use VS Code, download the extension (`research-panel-X.Y.Z.vsix`) from the
[GitHub releases page](https://github.com/yubrajbhandari923/research-vscode/releases/latest).

**The CLI** (in its own isolated environment, so it never touches your project's packages):

```bash
uv tool install "research-panel[tui]"
research version
```

With pipx instead: `pipx install "research-panel[tui]"`. If `research` is not found afterwards, add the tool directory to
your `PATH` (`uv tool update-shell` or `pipx ensurepath`) and open a new terminal. The same wheel is also attached to every GitHub release
(with `SHA256SUMS`) if you prefer to install from a file; see [other ways to install](#other-ways-to-install) below.

**Optional extras.** The example above already includes the terminal UI:

| Extra | Gives you |
|---|---|
| `[tui]` | the terminal UI, `research tui` |
| `[discord]` | the Discord bot (Python 3.10 or newer; Telegram needs no extra) |

Combine them as `research-panel[tui,discord]`. Add one later by installing again with `--force`, for example `uv tool install --force "research-panel[tui,discord]"`, or `pipx inject research-panel discord.py`.

### Other ways to install

**From the release wheel**, to pin an exact file, install without PyPI, or check what you install. Every GitHub release carries the same wheel that is on
PyPI, plus `SHA256SUMS`:

```bash
# download research_panel-X.Y.Z-py3-none-any.whl and SHA256SUMS from the release page, then
grep '\.whl$' SHA256SUMS | shasum -a 256 -c -           # Linux: sha256sum -c -
uv tool install "research-panel[tui] @ file:///full/path/to/research_panel-X.Y.Z-py3-none-any.whl"
# or straight from the release URL:
uv tool install "research-panel[tui] @ https://github.com/yubrajbhandari923/research-vscode/releases/download/vX.Y.Z/research_panel-X.Y.Z-py3-none-any.whl"
```

Without extras: `uv tool install ./research_panel-X.Y.Z-py3-none-any.whl`, `pipx install ./research_panel-X.Y.Z-py3-none-any.whl`, or `pip install` the file inside a
virtual environment. `research self-update --from-file WHEEL` installs a downloaded wheel (and checks it against a `SHA256SUMS` file if one is next to it).

**Building the wheel yourself** (needs no Node; the extension and tests are described in the repository README under *Development*):

```bash
git clone https://github.com/yubrajbhandari923/research-vscode.git && cd research-vscode
uv build                                        # or: python -m pip install build && python -m build
uv tool install "research-panel[tui] @ file://$PWD/dist/research_panel-X.Y.Z-py3-none-any.whl"
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

Updating later is covered in [section 10](#10-updating).

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

In VS Code: open an empty folder and run **Research: Initialize Project**. The wizard asks the same questions and shows what it will do before it writes anything.
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
`research decision create` (see the README's [synthesize and decide](../README.md#synthesize-decide-and-resume) section). An agent's finding stays preliminary until an independent review.

Steer a task in plain words at any time: `research supervisor instruct T-002 --message "use 60 views instead of 30"` (an assistant agent turns the sentence into actions). Requests that touch science or resources become a proposal
that waits for your PIN (section 7).

## 5. Watching and steering

**The terminal UI.** Install the `[tui]` extra (section 1), then:

```bash
research tui
```

It has eight tabs: Overview, Runs, Findings, Plans & Approvals, Decisions, Records, Doctor and **Inbox**. It refreshes by itself.

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
| `1`-`8`, or `[` and `]` | switch tab (on Decisions, the digits pick an option) |
| `r` | refresh |
| `/` | filter the rows (an empty filter clears it) |
| `k` | cancel the selected run |
| `a` / `d` | approve or reject the selected proposal (asks for your PIN) |
| `t` | on Decisions: answer in your own words |
| `Ctrl+T` | dark or light theme |
| `?` | the guide (← → move between its sections) |
| `q` | quit |

**The VS Code Control Center.** Run **Research: Open Control Center** (or `Ctrl/Cmd+Alt+R` for Home). Home lists what needs you; **Plans** shows phases, attempts,
approvals and proposals; **Research** has experiments, findings and decisions; **Settings** shows every configurable value and where it comes from.

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

## 6. From your phone

Chat is optional and off until you set it up. It sends alerts (halts, failures, decisions, finished plans) to Telegram or Discord and lets you answer and steer. The full setup is in
[CHAT.md](CHAT.md); in short:

**Telegram.** Create a bot with @BotFather and copy its token. Run `research chat setup`, paste the token at the hidden prompt, then send your bot any message. That chat becomes
the first paired user.

**Discord.** Install the `[discord]` extra, create an application in the Discord developer portal, copy the bot token, then run `research chat setup discord`. It prints an invite URL;
open it and add the bot to your server. Send the bot a direct message to get a pairing code and type it at the prompt.

```bash
research chat status
research chat test
research chat pair CODE
```

`chat status` shows the token, the paired people and whether the gateway is running; `chat test` sends a message; `chat pair CODE` approves someone else who messaged the bot
(you do this at your computer, with a role of `viewer`, `operator` or `approver`).

**What chat can do.** Read status, logs and reports; answer decision requests; give the supervisor instructions; pause, resume, stop one attempt, hand a task to another agent.
**What it cannot:** approve a protected change, create experiments, change policy, approve promotion, accept unverified work. Those are only ever done at your computer. Only paired people are obeyed.

**Approving from chat (optional).** If you want to approve proposals from your phone, enrol an authenticator app on the computer
(`research approval chat-enroll`, which needs your PIN) and enable `governance.approval.chat.enabled`. Approving then needs the 6-digit code from the app. **The PIN itself is never
requested through chat, in any form.** Details are in [GOVERNANCE.md](GOVERNANCE.md).

## 7. Approvals and the PIN

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

## 8. Usage limits

Providers cap usage per 5 hours or per week. By default a limit stops the task and asks for you. To let the supervisor wait and carry on by itself:

```bash
research config set supervisor.usage_limit.on_limit wait --scope machine --reason "overnight runs"
```

This setting needs a human, so it needs your PIN (or `require_pin: false`, section 7); in VS Code use **Settings**. The values:

| Value | Meaning |
|---|---|
| `halt` (default) | the task stops and waits for you |
| `wait` | tasks on that provider pause until it resets, then relaunch; tasks on other providers keep running |
| `fallback_then_wait` | first try the next provider in `agents.handoff.fallback_order`, otherwise wait |

A limited attempt does not count against a task's attempt budget. The supervisor adds `wait_buffer_seconds` (default 120) after the reset time. A reset further than `max_wait_hours` (default 8, so a
weekly limit) is never waited out unattended: you get a decision request to wait, switch provider or stop. When the reset time cannot be read, it retries after 15, 30, then 60 minutes.
Reset times are read from Claude Code, Codex, OpenCode and Antigravity messages; the formats each one uses are in [PROVIDERS.md](PROVIDERS.md). With chat on, you get one alert when the wait starts and one when it resumes.
Everything else about it is in [SUPERVISOR.md](SUPERVISOR.md).

## 9. Presenting results

`research report` records everything. The **Scientist Guide** is the short story you tell: a [Quarto](https://quarto.org) book in `docs/scientist-guide/` that builds to a website. A new project already has one.

```bash
research guide status
research guide highlights
research guide quarto doctor
research guide quarto build --edition scientific
```

Results an independent review marks supported (or that you star with `research guide star F-001`) are logged into it automatically. An agent writes a short draft only when you ask, within word limits you set
(`research guide draft check` reports any overrun). Add references with `research guide cite add --bibtex "..."` and cite them as `[@key]`. Quarto is needed only to build. See [GUIDE.md](GUIDE.md).

## 10. Updating

```bash
research self-update --check
research self-update
```

`--check` only compares your version with the latest release. `self-update` downloads the newest wheel from the GitHub release, verifies it against the release's `SHA256SUMS`, and reinstalls with the tool that
installed it (uv, pipx or pip). If anything fails, including a checksum mismatch, your existing install is left untouched. A new **major** version can break things, so it is refused unless you add `--major`.
`--version X.Y.Z` installs a specific release, and `--from-file WHEEL` installs a wheel you downloaded (offline), checking a `SHA256SUMS` file beside it. It also restarts supervisors it knows about.

Then update the VS Code extension: download `research-panel-X.Y.Z.vsix` from the same release, reinstall it (`code --install-extension research-panel-X.Y.Z.vsix`) and reload the window. Run `research bin install --force`
if agents use the project's launcher, and `research supervisor restart` for a running supervisor.

## 11. Troubleshooting

| Symptom | What to do |
|---|---|
| `research: command not found` | Run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal; in VS Code set `research.cliPath` |
| "version mismatch" in VS Code | Click it. CLI too old: `research self-update`. Extension older: install the newer VSIX. Reload the window |
| "No approval PIN is set" | `research approval set-pin` in a terminal, or `governance.approval.require_pin: false` in the machine config |
| No Telegram or Discord messages | `research chat status`, then `research chat gateway restart` |
| Plan does not move | `research supervisor status`, then `research supervisor start`; `research plan status PLAN-001` says why it halted |
| Not sure the install is healthy | `research doctor` |

More causes and fixes are in [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Providers and configuration: [PROVIDERS.md](PROVIDERS.md), [CONFIGURATION.md](CONFIGURATION.md).
