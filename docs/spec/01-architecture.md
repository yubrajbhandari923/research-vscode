# 1. Architecture

Research OS is one Python core (standard library only, Python 3.8 or newer) with several thin front ends. All business logic is in the
core; the CLI, the terminal UI, the VS Code extension, the MCP server and the supervisor call the same functions and write the same files.

## Layers

```
 presentation   research_cli/commands/*   research_cli/tui.py     VS Code extension (TypeScript, thin client)
                      │ direct calls              │ api.call                │ JSON-RPC over stdio
                      │                           │                         ▼
 transport            │                           │             research/rpc.py            research/mcp.py (agents)
                      │                           └──────────────────┬─────────────────────────┘
 operations           │                                      research/api/*  (one @op per operation)
                      ▼                                              │
 core services   services, runs, plans, verification, evidence, views, context, report, fullreport, guide*, provenance,
                 agents, providers/, strategy, orchestrate, execution, skills, packs, hooks, checks, supervisor, planspec,
                 intervene, notify, promotion, autopromote, hpc/, workspace, sandbox ...
 config/policy   registry (typed settings and the only defaults), configfile (layers, comment-preserving edits),
                 governance, approval, machine
 store/base      store (Project, transactions, sync), schema, durability, mirrors, util, yamlio, gitinfo
```

Rules the code follows:

1. **An operation is defined once**, as a core function. An `@op` in `research/api/` adapts arguments and shapes the result; the logic stays
   in a core module. `api.OPS` is the registry; `api.call(name, session, params, surface)` binds parameters, runs the op and reports
   `session.changed`.
2. **Governance lives in core only** (`governance`, `approval`, `supervisor`, `verification`). The surface tags on an op (`chat`, `mcp`) are
   presentation filters that fail early with a clearer message; removing a tag never weakens a gate.
3. **"Did this call change data" is a fact of the store.** `Project.tx()` sets `p.dirty` on commit; the outermost owner (CLI `main`,
   `api.call`) then regenerates `context/current.md`. RPC responses carry `"changed": true` so the extension refreshes its tree.
4. **The CLI calls core functions directly** (argparse and printers are CLI specific). RPC, MCP and the TUI go through the op registry.

## Components

| Area | Modules (`packages/core/research/`) |
|---|---|
| Store and index | `store.py` (Project, config, transactions, sync, rebuild, events), `schema.py`, `mirrors.py`, `durability.py` (locks and undo journal), `yamlio.py`, `gitinfo.py`, `workspace.py` (shared ledger, experiment worktrees) |
| Records | `services.py` (questions, experiments, syntheses, findings, decisions, notes), `runs.py`, `plans.py`, `planspec.py` (YAML plans), `verification.py`, `evidence.py`, `provenance.py` |
| Context and reports | `context.py` (resume context, Research Home, checkpoints, `current.md`), `views.py`, `report.py`, `fullreport.py`, `search.py`, `compare.py`, `control.py` |
| Agents | `agents.py` (roles, profiles, dispatch briefs), `providers/` (one adapter module per CLI), `strategy.py`, `orchestrate.py`, `execution.py` (single-use execution records), `usage.py`, `harness.py`, `hooks.py`, `checks.py`, `skills.py`, `packs.py`, `setup_agents.py`, `agentsmd.py`, `taskprompt.py`, `task_spec.py` |
| Supervision | `supervisor.py` (state machine), `supervisord.py` (daemon), `governance.py`, `approval.py`, `intervene.py`, `notify.py` (halt watch, debug agent, Telegram), `autopromote.py`, `promotion.py`, `daemonlib.py`, `machine.py` |
| Execution | `runner.py`, `backends/` (`local`, `slurm`), `hpc/` (cluster profiles, transport, bundles, datasets, results), `sandbox.py`, `isolation.py` |
| Transports | `api/` (the op layer), `rpc.py`, `mcp.py` |
| Configuration | `registry.py`, `configfile.py`, `bootstrap.py`, `initproject.py`, `doctor.py`, `envcheck.py`, `selfupdate.py`, `localbin.py` |
| Scientist Guide | `guide.py`, `guide_quarto.py`, `guide_review.py` |

`packages/cli/research_cli/` holds the argparse CLI (`main.py` assembles the parser and dispatches; `commands/*.py` define the commands;
`output.py` the shared printers) and the Textual TUI (`tui.py`). `packages/vscode-extension/` is the TypeScript client. Both are described in
[interfaces](09-interfaces.md).

## Process model

| Process | Lifetime | Touches SQLite? |
|---|---|---|
| `research` CLI invocation | one command | yes |
| `research.rpc` (one per VS Code window) | the window; the extension restarts it if it dies | yes |
| `research tui` | the session; runs the core in process | yes |
| `research mcp` (started by an agent client) | the client session | yes |
| `research.supervisord` (one per project with a running plan, or when Telegram is enabled) | until `supervisor stop`; reopens the project every pass so settings apply live | yes |
| agent CLI sessions (started by dispatch or the supervisor) | one attempt, in the task's worktree | only through `research` commands |
| `research.runner` (local detached, or inside a SLURM job) | one run | **no**: run-directory files only |
| Python code in a run calling `research.log_metric()` | user code | **no** when `RESEARCH_RUN_DIR` is set |

## Why a Python core and a thin client

- **All business logic is Python**, standard library only. The extension renders data and calls the core; it does not read SQLite or write
  `.research/` files itself (except previews of artifact heads and log tails).
- **One install, several clients.** The core and CLI ship as one wheel (`research-panel`). The VSIX contains no Python and spawns the
  installed `research rpc`; `research tui` is a second UI over the same core; agents use the same `research` command or MCP server.
- **YAML.** The system PyYAML is used when importable; otherwise the vendored PyYAML 6.0.3 under `research/_vendor/yaml`.
- **HPC friendliness.** `python3` exists on every login and compute node; the runner needs only the standard library; SQLite uses a rollback
  journal and compute nodes never open the database ([storage](03-storage-sync.md#sqlite-index)).

The rationale and the alternatives rejected are in [12-rationale-limitations](12-rationale-limitations.md).
