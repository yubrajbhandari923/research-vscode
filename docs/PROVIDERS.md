# Agents, providers and dispatch

Research OS owns the plan, tasks, ledger, worktrees and verification. A **provider** (Claude Code, OpenAI Codex, Google
Antigravity, or your own) only does the work. Records live in the shared ledger, never in an agent, so you can hand any task
to a different provider and nothing is lost.

## Choosing and changing the provider

| Where | How |
|---|---|
| Wizard | Initialize Project → Agents: enable any of the three, pick the default (Claude Code by default). |
| UI | Control Center → **Agents** → *Make default*. |
| YAML | `agents.default_profile: codex` in `.research/config.yaml` (the UI edits the same key). |
| One dispatch | Task → Dispatch → Agent / Model / Mode, or `research dispatch implementer T-001 --profile codex --agent-model gpt-5 --effort high`. |
| CLI | `research provider list`, `research provider set-default codex`. |

Changing the default affects only future dispatches; a running session keeps what it was launched with. Research OS never
substitutes another provider or model when the one you asked for is missing: dispatch reports the problem instead.

## Model allocation across providers

Levels `fast` / `balanced` / `deep` map to **profiles** (`agents.profiles.*`), and a profile is a provider + model (+ effort).
The default `sonnet_first` strategy maps them to Claude models. Mix providers by extending a strategy:

```yaml
agents:
  profiles:
    codex-checked: {provider: codex, command: codex, model: gpt-5, effort: medium}
orchestration:
  active_strategy: mixed
  strategies:
    mixed:
      extends: sonnet_first          # planning stays on Claude/Sonnet, deep synthesis on Claude/Opus
      levels: {fast: codex-checked}  # checked implementation goes to Codex
```

Every dispatch returns an `allocation` (task class, strategy, level, provider, profile, model, effort, mode, workspace,
permissions, verification policy). What you *requested* and what the provider *reported* (usage, from headless output) are stored separately.
Existing gates (required skills, frozen acceptance tests, task checks, independent review) run before launch and on completion,
whatever the provider.

## What every agent receives

1. `AGENTS.md` in `main/`: one set of rules (a managed block). Codex and Antigravity read it directly; `CLAUDE.md` is created as `@AGENTS.md`, so Claude reads the same text. Nothing is duplicated per provider.
2. A **task brief** (Markdown) generated from canonical records: goal, question, experiment hypothesis/parameters/criteria, plan, dependencies, checks, prior runs/findings, policies, required skills, working directory and branch, and how to run, record and finish. Its **absolute path** is in the prompt and in `RESEARCH_BRIEF`.
3. The **Research MCP server** for live state: `research provider mcp NAME --apply` registers it (`.mcp.json` for Claude, `.agents/mcp_config.json` for Antigravity, `codex mcp add` for Codex at user level), then **starts it from the experiment worktree and lists its tools**. Registration is not reported as working until that succeeds.

## Prepare, start, running, completed, verified

| State | Meaning |
|---|---|
| **prepared** | Everything resolved and the brief written. Nothing executed. (Preview.) |
| **launched / running** | The launcher holds the task lock and recorded a live process id. A command typed into a terminal is not "running". |
| **completed** | The provider process exited 0. It does **not** mean the task or experiment is right. |
| **failed / cancelled / unknown** | Non-zero exit (or could not start), stopped by you, or the launcher disappeared without an outcome. |
| **verified** | The task's checks and review policy passed (`research task verify`). Only this counts scientifically. |

`research execution list|show|logs|stop|retry`. A task can only be launched once at a time (a kernel lock); a prepared execution is single use.
Headless Claude Code runs are started with `--permission-mode acceptEdits` and `--allowedTools` limited to the `research` CLI and MCP server, because nobody can answer a prompt; arbitrary shell commands are not pre-approved. Override per profile with `allowed_tools: [...]` and `permission_mode`. The ledger's `.research/bin` is put on `PATH`. Exit 0 means the process ended, not that the task passed: run the task's checks (they run in the experiment worktree).

Headless runs capture stdout/stderr to `.research/orchestration/executions/<id>.out|.err` and the exit status.
`retry --profile codex` continues the same task with another provider in the same worktree.

## Continuing a task with another provider

A stopped or failed task can be continued by another provider in the same worktree; see [GOVERNANCE.md](GOVERNANCE.md#3-claude-codex-and-antigravity). Antigravity runs headless (`agy -p --output-format json --mode accept-edits`) and resumes with `--conversation`.

## Adding a provider

Constrained CLI route (no code): add a profile with `command`, `args`, `model_flag`, `prompt_flag` and, for headless, `headless_args`
(and optionally `usage_parser`). It uses provider `custom`; unsupported modes are refused rather than guessed.
Code route: see `examples/custom-provider/example_provider.py`; `providers.register(name, build, capabilities)` where `build(prof, model,
effort, prompt, cwd, extra_dirs, headless)` returns an argument **list**. Capabilities declared: interactive, headless, model, effort,
structured_output, usage, resume, mcp, instructions. Credentials never go in Research OS config; the CLI keeps its own sign-in.

## HPC

Agent-dispatched work runs locally in the experiment worktree and produces registered runs. To run an experiment on a cluster, use the
HPC commands (`research hpc doctor|submit|sync`): they ship only that experiment's source snapshot, never the ledger or datasets.
