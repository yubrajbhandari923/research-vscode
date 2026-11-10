# Troubleshooting

Start with `research doctor`. It is read-only, checks the project, agents, git workspaces and execution setup, and names the command that fixes each problem.
`research doctor --all` also lists what passes. Steps below assume the [getting started](QUICKSTART.md) setup.

## Installation

| Symptom | Cause and fix |
|---|---|
| `research: command not found` | The tool directory is not on your `PATH`. Run `uv tool update-shell` (uv) or `pipx ensurepath` (pipx) and open a new terminal. `uv tool list` or `pipx list` shows whether it is installed at all |
| The extension says "CLI not found" | It could not find `research` in the `research.cliPath` setting, on `PATH`, in `~/.local/bin` or in pipx's bin directory. Run `which research` in a terminal, put that full path in **Settings → Research: Cli Path**, and reload the window. Over Remote SSH, install the CLI on the remote host |
| "version mismatch" in the status bar | The extension needs the same major version as the CLI and a CLI minor at least what it declares. Click the warning (or run **Research: Show Version Info**) to see both versions. "CLI is too old": `research self-update`. "Extension is older than your CLI": install the newer VSIX from the GitHub release. Reload the window after either |
| `research self-update` fails | The existing install is untouched on any failure. A checksum mismatch means the download was corrupted or incomplete: try again, or download the wheel and `SHA256SUMS` yourself and run `research self-update --from-file WHEEL`. A new major version needs `--major` |
| `research tui` says it needs the extra | Reinstall with `[tui]`: `uv tool install --force "research-panel[tui]"` |
| Discord says `discord.py` is missing | Install the `[discord]` extra the same way (Python 3.10 or newer). `research chat status` prints the exact line for how you installed |
| "… is a Research OS v1 project" | The folder holds an older project. Nothing was changed. See [Recovery](../README.md#recovery) in the README for starting fresh beside it |

## Agents

| Symptom | Cause and fix |
|---|---|
| `research doctor` says the MCP server is not registered | Run `research agent setup PROVIDER --apply` (for example `claude`). `research agent providers mcp claude` checks the registration |
| A provider shows "NOT installed" in `research agent providers list` | Install and sign in to that CLI yourself; Research OS does not manage provider logins |
| A task is waiting and nothing launches | The provider hit a usage limit, or the plan is paused. `research plan status PLAN-001` shows the phase. See usage limits below |
| Work an agent reported as finished is not accepted | Completed is not verified. `research task verify T-001` runs the checks and says which one failed |

## Approvals and the PIN

| Symptom | Cause and fix |
|---|---|
| "No approval PIN is set on this machine" | Protected actions are refused until a PIN exists. In a terminal run `research approval set-pin`. In VS Code use **Settings → Approval PIN**. To run without one on purpose, set `governance.approval.require_pin: false` in `~/.config/research-os/config.yaml`; protected actions then still need a human and a written reason |
| "The PINs did not match, or there is no interactive terminal" | `set-pin` needs a real terminal; it cannot read the PIN from a pipe, a script or an agent's shell. Run it in your own terminal |
| An action is refused as "agent" | Protected actions are human-only. A shell that looks like an agent (Claude Code, Codex and similar) is refused even with `--as human`. Run the command in your own terminal |
| Forgot the PIN | Delete `approval.json` in `~/.config/research-os/` and run `research approval set-pin` |
| Locked after wrong PINs | Five wrong tries lock it for 60 seconds. Wait and retry |
| Chat will not approve | The PIN never goes through chat. Approving from chat needs an enrolled authenticator (`research approval chat-enroll`), `governance.approval.chat.enabled: true` and the `approver` role. Some actions are computer-only whatever you do |

## Supervisor and plans

| Symptom | Cause and fix |
|---|---|
| Plan does not move | `research supervisor status`. If it says not running: `research supervisor start`. After an upgrade or a reboot: `research supervisor restart` |
| Plan says HALTED | `research plan status PLAN-001` names the task and the reason. `research decisions` lists the question waiting for you; answer with `research decide DR-001 …`. To see a diagnosis: `research supervisor debug PLAN-001` |
| Task says "results are not collected and ingested yet" | Slurm finished but the results are not home. The supervisor waits (`supervisor.recovery.collection_wait_minutes`) and re-verifies the same attempt when they arrive; to hurry: `research hpc collect RUN`. Nothing is resubmitted ([SUPERVISOR.md §23](SUPERVISOR.md#23-late-evidence-hpc-results-that-arrive-after-slurm-says-completed)) |
| Task blocked `evidence_pending` | The wait ended before the results came home. Run `research hpc collect RUN` (cluster reachable?); the task is re-verified automatically, no repair attempt is used |
| `research git status` / plan run lists uncommitted files | Experiment worktrees start from the last commit, so uncommitted plans and code are invisible to agents. Review and commit with `research git commit --paths notes.md --yes`, or `--scaffold` for generated files; `git.preflight` chooses warn or block ([SUPERVISOR.md §24](SUPERVISOR.md#24-git-hygiene-what-is-uncommitted-and-the-reviewed-commit)) |
| `research plan yaml validate` lists errors | Every problem is listed at once, with the key it applies to. Unknown fields are errors (keys starting `x_` are kept and ignored). Fix and validate again; `apply` is only safe after a clean validate |
| `apply` reports a revision conflict | The plan changed since the file was written. Run `research plan yaml export PLAN-001 -o plans/research.yaml`, merge your edits, and apply again |
| Checks use the wrong Python | `research env check` shows which Python agents use; `research env create` makes a project environment with uv |

## Chat

| Symptom | Cause and fix |
|---|---|
| Nothing arrives on Telegram or Discord | `research chat doctor` names each problem with its fix (`--live` tests the tokens). `research chat status` shows the token, paired users and whether the gateway runs. Then `research chat gateway restart` and read `~/.config/research-os/gateway/gateway.log` |
| The bot ignores you | Only paired people are obeyed. Message the bot to get a code and pair at your computer with `research chat pair CODE`. `/whoami` shows your role |
| "Several projects use this bot" | Choose one with `/project use NAME` |
| Chat stopped after moving to another machine | The gateway runs where the project lives. Each machine needs its own gateway and bot token |

More in [CHAT.md](CHAT.md).

## Usage limits

| Symptom | Cause and fix |
|---|---|
| A limit stopped the task and waits for me | That is the default (`halt`). To wait automatically, set `supervisor.usage_limit.on_limit` to `wait` (see the [guide](QUICKSTART.md#8-usage-limits)) |
| A weekly limit asks me a question | A reset further than `max_wait_hours` (8) is never waited out unattended. Answer the decision request: wait, switch provider, or stop |

## Records and recovery

| Symptom | Cause and fix |
|---|---|
| You edited a record file by hand | `research sync` imports hand-edited files |
| The index looks wrong or is locked | Stop other clients and retry. `research rebuild` rebuilds the index from the canonical records. Back up `.research/` first |
| "Newer schema refused" | Install a newer release. Do not lower the stored schema version |

If none of this helps, run `research doctor --all` and `research version --json` and include both when you report the problem.
