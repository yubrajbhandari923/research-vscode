# Agents, providers and dispatch

Research OS owns the plan, tasks, ledger, worktrees and verification. A **provider** (Claude Code, OpenAI Codex, Google
Antigravity, OpenCode, or your own) only does the work. Records live in the shared ledger, never in an agent, so you can hand any task
to a different provider and nothing is lost.

## Choosing and changing the provider

| Where | How |
|---|---|
| Wizard | Initialize Project → Agents: enable any of the four, pick the default (Claude Code by default). |
| UI | Control Center → **Agents** → *Make default*. |
| YAML | `agents.default_profile: codex` in `.research/config.yaml` (the UI edits the same key). |
| One dispatch | Task → Dispatch → Agent / Model / Mode, or `research agent dispatch implementer T-001 --profile codex --agent-model gpt-5 --effort high`. |
| CLI | `research agent providers list`, `research agent providers set-default codex`. |

Changing the default affects only future dispatches; a running session keeps what it was launched with. Research OS never
substitutes another provider or model when the one you asked for is missing: dispatch reports the problem instead.

## Model allocation across providers

Levels `fast` / `balanced` / `deep` map to **profiles** (`agents.profiles.*`), and a profile is a provider + model (+ effort).
The default `balanced_first` strategy maps them to the default provider's level pack (below). Mix providers by extending a strategy:

```yaml
agents:
  profiles:
    codex-checked: {provider: codex, command: codex, model: gpt-5, effort: medium}
orchestration:
  active_strategy: mixed
  strategies:
    mixed:
      extends: balanced_first        # planning stays on Claude/Sonnet, deep synthesis on Claude/Opus
      levels: {fast: codex-checked}  # checked implementation goes to Codex
```

Every dispatch returns an `allocation` (task class, strategy, level, provider, profile, model, effort, mode, workspace,
permissions, verification policy). What you *requested* and what the provider *reported* (usage, from headless output) are stored separately.
Existing gates (required skills, frozen acceptance tests, task checks, independent review) run before launch and on completion,
whatever the provider.

## What every agent receives

1. `AGENTS.md` in `main/`: one set of rules (a managed block). Codex, Antigravity and OpenCode read it directly; `CLAUDE.md` is created as `@AGENTS.md`, so Claude reads the same text. Nothing is duplicated per provider.
2. A **task brief** (Markdown) generated from canonical records: goal, question, experiment hypothesis/parameters/criteria, plan, dependencies, checks, prior runs/findings, policies, required skills, working directory and branch, and how to run, record and finish. Its **absolute path** is in the prompt and in `RESEARCH_BRIEF`.
3. The **Research MCP server** for live state: `research agent providers mcp NAME --apply` registers it (`.mcp.json` for Claude, `.agents/mcp_config.json` for Antigravity, the `mcp` block of `opencode.json` for OpenCode, `codex mcp add` for Codex at user level), then **starts it from the experiment worktree and lists its tools**. Registration is not reported as working until that succeeds.

## Prepare, start, running, completed, verified

| State | Meaning |
|---|---|
| **prepared** | Everything resolved and the brief written. Nothing executed. (Preview.) |
| **launched / running** | The launcher holds the task lock and recorded a live process id. A command typed into a terminal is not "running". |
| **completed** | The provider process exited 0. It does **not** mean the task or experiment is right. |
| **failed / cancelled / unknown** | Non-zero exit (or could not start), stopped by you, or the launcher disappeared without an outcome. |
| **verified** | The task's checks and review policy passed (`research task verify`). Only this counts scientifically. |

`research agent executions list|show|logs|stop|retry`. A task can only be launched once at a time (a kernel lock); a prepared execution is single use.
Headless Claude Code runs are started with `--permission-mode acceptEdits` and `--allowedTools` limited to the `research` CLI and MCP server, because nobody can answer a prompt; arbitrary shell commands are not pre-approved. Override per profile with `allowed_tools: [...]` and `permission_mode`. The ledger's `.research/bin` is put on `PATH`. Exit 0 means the process ended, not that the task passed: run the task's checks (they run in the experiment worktree).

Headless runs capture stdout/stderr to `.research/orchestration/executions/<id>.out|.err` and the exit status.
`retry --profile codex` continues the same task with another provider in the same worktree.

## Continuing a task with another provider

A stopped or failed task can be continued by another provider in the same worktree; see [GOVERNANCE.md](GOVERNANCE.md#continuing-a-task-with-another-agent). Antigravity runs headless (`agy -p --output-format json --mode accept-edits`) and resumes with `--conversation`.

## Capabilities

What each adapter honestly supports. Generated from `providers.ADAPTERS` (`python tests/python/gen_reference.py`; a test fails when it drifts), so
nothing here is assumed from a provider's name.

<!-- generated:capabilities -->
| Capability | claude | codex | agy | opencode | custom |
|---|---|---|---|---|---|
| `interactive` | yes | yes | yes | yes | yes |
| `headless` | yes | yes | yes | yes | `if headless_args` |
| `model` | yes | yes | yes | yes | `if model_flag` |
| `effort` | no | yes | yes | yes | no |
| `structured_output` | `json` | `jsonl` | `json` | `jsonl` | - |
| `usage` | yes | no | no | yes | `if usage_parser` |
| `resume` | no | yes | yes | yes | no |
| `mcp` | `.mcp.json` | `codex mcp add` | `.agents/mcp_config.json` | `opencode.json` | - |
| `instructions` | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md` | - |
| `watchdog` | no | no | no | yes | no |
| `limit_info` (usage-limit parser) | yes | yes | yes | yes | no |
| `version_info` | no | no | no | yes | no |
<!-- /generated:capabilities -->

`watchdog` = Research OS stops a run that hangs after its final event or goes silent (OpenCode only; the others exit on their own).
`custom` is the fallback for any CLI described entirely by a profile.

### Level packs

`fast` / `balanced` / `deep` resolve to these models per provider (profiles `<provider>-fast|balanced|deep`; override any with
`agents.profiles.<name>.model`). Models marked UNVERIFIED in the adapter source were not checked against an account: codex and agy pin no
model (the CLI's own default is used) and differ by reasoning effort only; OpenCode's `anthropic/*` ids and `--variant high` are the
documented defaults, not tested ones. Run `opencode models` and pin what your account can use.

<!-- generated:levels -->
| Provider | fast | balanced | deep |
|---|---|---|---|
| `claude` | `claude-haiku-5-5` | `claude-sonnet-5-5` | `claude-opus-5-5` |
| `codex` | `CLI default` (low) | `CLI default` (medium) | `CLI default` (high) |
| `agy` | `CLI default` (low) | `CLI default` (medium) | `CLI default` (high) |
| `opencode` | `anthropic/claude-haiku-5-5` | `anthropic/claude-sonnet-5-5` | `anthropic/claude-opus-5-5` (high) |
| `custom` | `CLI default` | `CLI default` | `CLI default` |
<!-- /generated:levels -->

## Setting up each provider

`research agent setup PROVIDER [--apply] [--agent-model X]` is the same for every provider and idempotent: it checks the CLI, writes the
AGENTS.md block, registers the Research MCP server with the provider's own mechanism, links the first-party skills, enables orchestration,
and checks routing offline (no model call). `research init` runs the same steps for the providers you enable. `research doctor` then
reports, per provider: version, MCP registration, credential file presence (existence only, never read) and provider-specific warnings.

| Provider | Install check | MCP registration | Notes |
|---|---|---|---|
| Claude Code | `claude --version` | `.mcp.json` | Also writes managed `.claude/agents/research-*.md`. Headless: `-p --output-format json`, edits and the research CLI pre-approved only. |
| OpenAI Codex | `codex --version` | `codex mcp add` (user config) | Headless `codex exec --json -s workspace-write`. No usage report. |
| Antigravity | `agy --version` | `.agents/mcp_config.json` | Headless `--mode accept-edits --output-format json -p`. |
| OpenCode | `opencode --version` | `opencode.json` `mcp` block | See below. |

None of them gets provider hooks: governance is the core's agent detection (`RESEARCH_AGENT`) plus the approval PIN, identical for all.

### OpenCode

- **Versions.** OpenCode 1 and 2 differ in config schema, so the adapter reads `opencode --version` (cached) and generates the matching inline
  config (`OPENCODE_CONFIG_CONTENT`): v1 `permission` object, v2 `permissions` rule array. v1 rejects the v2 key, so a wrong guess would fail the run;
  an unreadable version refuses headless dispatch. v1 is end-of-life upstream; doctor suggests `opencode upgrade --major`. If your v2 binary has a
  different name (v1's error message mentions `opencode2`), set `agents.profiles.opencode.command`.
- **Models.** `provider/model` is required (`anthropic/claude-sonnet-5-5`); `effort` becomes `--variant`. `agent setup opencode --agent-model provider/model` writes the profile model.
- **Permissions.** Headless runs allow edits, the `research` CLI and the research MCP tools; every other request is `ask`, which `opencode run`
  auto-rejects. `--auto`, `--yolo` and `--dangerously-skip-permissions` in profile `args` are refused. Interactive sessions keep your own permissions.
- **Hangs.** OpenCode 1 can print its final event and never exit, or never print one. The launcher stops the process group `exit_grace_seconds`
  (default 30) after a final `step_finish` (counted as exit 0), stops a silent run after `idle_timeout_seconds` (default 1800, exit 124), and records
  `provider_warning` when a run exits 0 without a final event (usage is then flagged `partial`). Verification still decides the task.
- **Attribution.** opencode passes no env var to its shell, so an interactive session you start yourself is recorded as you; dispatched runs and MCP calls carry
  `RESEARCH_AGENT`. PIN-gated actions stay human-only either way.
- **Secrets.** `~/.local/share/opencode` (it holds `auth.json`) is never mountable into a sandbox.

## Usage limits

Each adapter has `limit_info(stdout, stderr, exit_code)` returning kind (`usage`, `rate`, `overload`, `credit`) and `reset_at` (UTC, or unknown).
It is exposed as `providers.limit_info(provider, out, err, exit_code)` and tested against the messages in `tests/python/fixtures/limits/`
(label REAL = quoted from an issue, doc or source file; REALISTIC = built from verified field names). **The supervisor does not use it yet**:
today a limit voids the attempt and halts the task (`supervisor.usage_limit.on_limit: halt`); waiting and auto-resume is a later package.

| Provider | Signal parsed | Reset forms | Status |
|---|---|---|---|
| Claude Code | `result` with `is_error` / `terminal_reason: api_error` (never `subtype`); stream-json `rate_limit_event` `rejected`; banner text on a failed run | epoch seconds (`resetsAt`), `resets 3pm (America/New_York)`, `resets 11am (UTC)`, host-local `resets 9:20pm`, `regain access on 2026-09-01 at 00:00 UTC`, `retry-after` | Detection from real issues; UNVERIFIED: exact `-p --output-format json` output at a real 5-hour limit |
| Codex | `turn.failed` / `error` message, failed-run stderr | `Try again at 3:41 PM.` and `Feb 23rd, 2026 9:01 PM.` (host-local, no zone), `try again after 60.6 seconds`; `Try again later.` = unknown | From source; UNVERIFIED: `resets_at` epoch in `exec --json`, JSON form of the per-minute 429 |
| OpenCode | `error` events (`retryAfterMs`, error type), `retry` events with epoch-ms `nextRetryAt` waiting 5+ minutes, failed-run stderr | relative ms, epoch ms, `retry after 30s` | REALISTIC from source, not a live limit; the 5-minute threshold is a guess |
| Antigravity | stdout `result` event with `status: ERROR`, stderr `AGY_ERROR: {...}`, json-mode error | relative `Resets in 2h39m52s` | One real issue (antigravity-cli #1018); UNVERIFIED: json-mode shape, other units. A timeout with zero tokens is not treated as a limit |

Reset times without a zone are host-local; a clock time resolves to its next occurrence (a reset up to five minutes ago counts as now);
DST shifts are honoured; an unknown zone name gives an unknown reset rather than a guess. Plain text only counts for a failed run.

## Verification results

Checked 2026-11-08 against real binaries, with no model call and no login (`--help`, `--version`, `models`, `debug config`):

| Item | Result |
|---|---|
| claude 2.1.319 flags the adapter uses: `-p`, `--output-format`, `--add-dir`, `--allowedTools`, `--permission-mode`, `--model`, `--resume` | VERIFIED present in `claude --help` |
| codex-cli 0.163.0: `exec`, `--json`, `-s`, `-C`, `--add-dir`, `-m`, `-c`, `exec resume [--json] [-m] [-c] ID PROMPT`, `mcp add`, `mcp list` | VERIFIED present |
| agy 1.5.3: `--add-dir`, `--model`, `--effort` (low\|medium\|high\|xhigh\|max), `--mode accept-edits`, `--output-format json`, `-p`, `-i`, `--conversation`, `models` | VERIFIED present (earlier notes said 1.5.0) |
| opencode install | VERIFIED: npm package `opencode-ai`, `latest` = 1.19.5; installed into a scratch prefix only. No 2.x exists under that package (`latest-0`, `latest-1`, `next`, `beta`, `dev` tags only), so v2 could not be installed |
| `opencode --version` output | VERIFIED v1: prints `1.19.5`. v2 (`opencode v2.1.4`): UNVERIFIED |
| D.12 `-f` attaches a file | v1: flag VERIFIED in `run --help`; it is an array option that swallows a following positional (VERIFIED by parsing), so the prompt goes first. That the content reaches the model: UNVERIFIED. v2: UNVERIFIED |
| D.12 `--variant` accepted | v1.19.5 flag VERIFIED; which values each model provider accepts: UNVERIFIED |
| D.12 `mcp` key and `environment` field | v1 VERIFIED (`opencode debug config` with `OPENCODE_CONFIG_CONTENT` echoes both). v2: UNVERIFIED |
| D.12 `permission` vs `permissions` | v1 VERIFIED: accepts `permission`, rejects `permissions` ("V2 permissions are not supported by OpenCode V1"). v2 schema: UNVERIFIED (design D.4 and upstream docs) |
| D.12 `--session` on `run` | v1 VERIFIED (`-s, --session`). v2: UNVERIFIED |
| D.12 `--prompt` on the interactive command | VERIFIED on 1.19.5 (`opencode --help`) |
| D.12 JSONL event names | v1 VERIFIED from one real run with a keyless free model (cost 0): `step_start`, `tool_use`, `step_finish` (`part.reason: tool-calls`, `part.tokens {total,input,output,reasoning,cache{write,read}}`, `part.cost`), `sessionID` on every line. The `text` event, the `error` event and v2 names: UNVERIFIED |
| D.12 shell tool inherits the process env | UNVERIFIED |
| D.12 exit codes | UNVERIFIED |
| `opencode models` | VERIFIED offline: one `provider/model` per line |
| Config files read | `OPENCODE_CONFIG_CONTENT` VERIFIED to be read; data dir `~/.local/share/opencode` VERIFIED (`debug paths`) |

The fake binary (`tests/python/fakes/opencode`) encodes only what is marked VERIFIED for v1; its v2 behaviour follows the design notes.

## Adding a provider

1. **No code**: add a profile with `command`, `args`, `model_flag`, `prompt_flag` and, for headless, `headless_args` (and optionally `usage_parser`).
   It uses provider `custom`; unsupported modes are refused rather than guessed.
2. **Runtime, from a script**: `providers.register(name, build, capabilities)`; see `examples/custom-provider/example_provider.py`. The provider gets an
   empty level pack and is not in `providers.KNOWN` (no default profiles, wizard entry or fallback slot).
3. **First-class (a PR)**: write `packages/core/research/providers/<name>.py` exporting `ADAPTER = Adapter(...)` and add the module to `_MODULES` in
   `providers/__init__.py`. Default profiles, the provider table, fallback order, setup wizard, `agent setup`, doctor and this page's tables derive from it.
   Fill in, honestly: `build` (an argv list, never a shell string), `caps`, `levels`, `profile`; and where the CLI supports them `usage_parser`,
   `resume_probe`/`resume_argv`/`session_id`, `mcp_install`/`mcp_registered`, `version_info`, `final_event` (enables the watchdog), `env`, `checks`
   (doctor rows) and `limit_info`. Then add: a fake binary in `tests/python/fakes/`, tests (argv golden, usage, resume, MCP), limit fixtures in
   `tests/python/fixtures/limits/`, and regenerate the tables. `tests/python/test_agent_setup_parity.py` runs every `KNOWN` provider through `init` and
   `agent setup` and requires the same shape and a green doctor.

Credentials never go in Research OS config; the CLI keeps its own sign-in.

## HPC

Agent-dispatched work runs locally in the experiment worktree and produces registered runs. To run an experiment on a cluster, use the
HPC commands (`research hpc doctor|submit|sync`): they ship only that experiment's source snapshot, never the ledger or datasets.
