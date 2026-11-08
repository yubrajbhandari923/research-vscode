# Phase cleanup 2: target design, migration plan, opencode and Discord (development branch only)

Dev-only file (matches `docs/PHASE_*` in AGENTS.md). Never merge to `main`.
Written 2026-11-07 against `development` at c123773 (3.0.0, not yet tagged; last tag v2.6.0). Inputs: `PHASE_CLEANUP_1_INVENTORY.md`
(the audit), `PHASE_CLEANUP_R_RESEARCH.md` (external research), `docs/SPEC.md`, the code. Rollback point: branch `backup/pre-cleanup-3.0.0`.

How to use this file: section C is the execution plan. Every work package (WP) names the files it owns, what it depends on and the
commands that must pass. Sections A, B, D and E are the reference the WPs point to. Section F lists the only questions for the owner;
each has a default, so work can start before the owner answers.

### Corrections to the inputs (spot-checked in the code)

| Input claim | What the code says | Consequence |
|---|---|---|
| Research: extras named `research-os[discord]` | The distribution is `research-panel` (`pyproject.toml`) | Extras are `research-panel[tui]`, `research-panel[discord]` |
| Research: `opencode --version` UNVERIFIED | Verified (opencode.ai/docs/cli global flags; computingforgeeks cheat sheet re-checked 2026-10): v2 prints `opencode v2.1.4`, v1 prints `1.19.4` | Version probe = `opencode --version`, major from the first `\d+\.\d+\.\d+` |
| Research: opencode exposes a session env var to child shells | Not available: feature request anomalyco/opencode#9292 (OPENCODE_SESSION_ID) was closed "not planned"; #11065/#12018 are plugin proposals | Ad-hoc opencode sessions cannot be auto-detected as agents; see D.7 |
| Research: `OPENCODE_PERMISSION` env | Not referenced by the 2.x binary (cheat sheet, Oct 2026) | Inject permissions through `OPENCODE_CONFIG_CONTENT` only |
| Inventory: hard-coded ("claude","codex","agy") sites | Confirmed (`rpc.py:888`, `main.py:2864`, both with `dict \| dict`) | WP5 |
| Inventory: `approval.confirm` approves with no PIN, no tty | Confirmed (`approval.py:197-201`: neither branch runs) | WP2 |
| Inventory: `providers.register` has no caller | Confirmed | Deleted in WP5 (replaced by the provider table) |
| Memory note: "re-bundle `packages/vscode-extension/python/` after core edits" | Obsolete in 3.0: `scripts/bundle-python.mjs` is gone; the extension finds the CLI on PATH or `research.cliPath` | UX harness (G) points `research.cliPath` at a dev venv |
| Inventory: test durations | Filled in late by the audit: local full run 25.5 min (1,096 passed, 3 failed), top 40 tests ≈ 380 s, none marked `slow`; the other ~1,050 tests average ≈ 1.1 s | Serial pruning alone cannot reach 3 min: the fast target in WP6 needs markers **and** `pytest-xdist` |
| `supervisord.py` purpose | Exists so only ONE `research.supervisor` module object lives in the daemon (completion grants are module globals) | Do not move `run_forever` or grants into another module (A.8) |

---

## A. Target architecture

### A.1 Layering

```
 presentation   research_cli/commands/*  (argparse + printers)     research_cli/tui.py     VS Code extension (TS)    chat adapters
                       │ direct calls                                   │ api.call               │ JSON-RPC over stdio      │ api.call(surface="chat")
                       │                                                │                        ▼                          │
 transport             │                                  research/rpc.py (stdio loop)   research/mcp.py (MCP stdio)        │
                       │                                                └──────────┬─────────────┘                          │
 operations            │                                         research/api/*  (ONE @op per operation)  ◄─────────────────┘
                       ▼                                                           │
 core services   services, runs, plans, verification, evidence, views, context, report, fullreport, guide*, provenance,
                 agents, providers/, strategy, orchestrate, execution, skills, packs, hooks, checks, supervisor, planspec,
                 intervene, notify (halt watch + debug agent), chat/ (alerts, pairing, gateway), promotion, autopromote, hpc/ ...
 config/policy   registry (owns defaults + types), configfile (layers, comment-preserving edits), governance, approval, machine
 store/base      store (Project, tx, sync), schema, durability, mirrors, util, yamlio, gitinfo
```

Rules (enforced by review and by the contract tests named in WP7):
1. **An operation is defined once**, as a core function. An `@op` in `research/api/` adapts arguments only (no logic beyond renaming,
   defaulting, and shaping the return value; anything longer than ~3 lines belongs in a core module).
2. **Governance lives in core only** (`governance`, `approval`, `supervisor.require_human`, `guard_completion`). Surface tags on ops
   (`chat=`, `mcp=`) are presentation filters that fail early with a clearer message; removing a tag never weakens a gate.
3. **"Did this call change data" is a fact of the store**, not a list per surface: `Project.tx()` sets `p.dirty` on commit. The
   outermost owner (CLI `main`, `api.call`, `Project.close`) regenerates `current.md` once if dirty. RPC responses carry `"changed": true`
   so the extension refreshes its tree. The four MUTATING lists are deleted.
4. The CLI keeps calling core functions directly (argparse UX and printers are CLI-specific). It does **not** go through the op registry;
   routing it there would add indirection for zero sharing, since printers differ from JSON shapes.
5. The TUI and chat call `api.call(...)` in-process; RPC and MCP are transports over the same registry.

### A.2 Module layout after the refactor

Sizes are rough targets after the WPs. Unlisted core modules are unchanged.

**Core `packages/core/research/` (stdlib only)**

| Module | Owns | ~Lines |
|---|---|---|
| `api/__init__.py` | `Op`, `@op`, `OPS`, `call(name, project, params, surface)`, param binding (`inspect.signature(...).bind`), error envelope, `describe()` for the RPC `ready` event | 200 |
| `api/records.py` | create/update/show/list/search/compare/report ops for questions, experiments, runs, metrics, artifacts, findings, decisions, checkpoints, notes, evidence | 300 |
| `api/plans.py` | plans, tasks, plan YAML, supervisor control, proposals, interventions, debug, approvals (`approval_set_pin`) | 280 |
| `api/agents.py` | providers, executions, dispatch, strategy, hooks, skills, packs, tools, usage | 250 |
| `api/guide.py` | guide_* and report ops | 120 |
| `api/project.py` | ping, init_*, init_yaml_*, doctor, settings, config_check, workspaces, sandbox, env, hpc_*, control/home/work/research views | 260 |
| `api/chat.py` | `chat_status`, `chat_setup`, `chat_test`, `chat_remove`, `chat_pairing`; `telegram_*` aliases until 4.0 | 80 |
| `rpc.py` | stdio transport only: framing, `ready` event (`version`, `protocol`, `ops`), per-request author override (non-sticky), error mapping | 120 (from 1,224) |
| `mcp.py` | MCP transport, protocol negotiation, tool table (`Tool(name, description, props, required, op=...)`), installers merged from `providers.mcp_install` | 300 (from 471) |
| `providers/__init__.py` | `Adapter` record, `ADAPTERS`, `KNOWN`, `LABELS`, `build_argv`, `detect`, `readiness`, `table(p)`, `version_info`, resume probe, `session_id_of`, `mcp_install/registered` dispatch | 220 |
| `providers/claude.py`, `codex.py`, `agy.py`, `custom.py` | one adapter each: argv, capabilities, resume shape, session id, MCP install/detect, usage parser name | 60-110 each |
| `providers/opencode.py` | section D | 280 |
| `chat/__init__.py` | `Message`, `Button`, `Channel` protocol, `channels()` | 90 |
| `chat/commands.py` | the generic command router (today `notify.handle/_instruct`), chat surface policy | 380 |
| `chat/alerts.py` | outbox, dedupe/cooldown/escalation, digest builder | 220 |
| `chat/pairing.py` | allow-lists, roles, pairing codes, lockout (0600 machine files) | 160 |
| `chat/gateway.py` | the machine-level gateway daemon (section E.4) | 380 |
| `chat/telegram.py` | stdlib Bot API adapter (send, edit, inline buttons, documents, `getUpdates`) | 260 |
| `chat/discord_bot.py` | discord.py adapter, lazy import (section E) | 480 |
| `notify.py` | halt detection + debug agent only; emits alerts through `chat.alerts` | 220 (from 420) |
| `machine.py` | machine paths (`config_dir()`), known-projects registry (`remember/forget/known_projects`), file writer `write_secret(path, obj)` (0600 via `os.open`) | 90 |
| `daemonlib.py` | generic single-instance lock, detached spawn, heartbeat file, version-stale check; used by supervisor and gateway | 120 |
| `registry.py` | typed settings AND the only defaults; `defaults()` builds `DEFAULT_CONFIG`; `section(p, "notifications")` | ~700 |
| `store.py` | unchanged API; `DEFAULT_CONFIG = registry.defaults()`; `p.dirty`; sync enters `tx()` only when something must be ingested | ~950 |
| `services.py` | + `new_experiment_plan/new_experiment` (moved from `setup.py`) | ~650 |
| `verification.py` | + the single `attention(p)` used by `current.md`, Research Home and Control Center | ~340 |
| **deleted** | `setup.py` (classic wizard), `compat.py` folded into `selfupdate.py` (12 lines, one caller each side) | |

**CLI `packages/cli/research_cli/`**

| Module | Owns | ~Lines |
|---|---|---|
| `main.py` | `main()`: alias rewriting (hidden aliases, `checkpoint` default action, `--` tail), root parser assembled from `COMMANDS`, project open/close, dirty → `current.md`, error printing | 180 |
| `output.py` | colors, `out_json`, `section`, `kv`, `fmt_*`, `_done`, shared printers (`print_list`, `print_generic`) | 250 |
| `commands/project.py` | init (+ `--from`, project-init, ledger), status (+`--full` = old `resume`), current (+`--quiet` = old `context`), show, search, sync, rebuild, config, doctor, log, mark, version, self-update, rpc, tui | 650 |
| `commands/records.py` | question, experiment, run, metric, artifact, finding, decision, checkpoint, note, evidence, source, tuning, and their printers | 950 |
| `commands/plans.py` | plan, task | 420 |
| `commands/supervisor.py` | today's `supervisor_cmds.py` minus telegram: supervisor, proposal, intervention, approval, promote, plan yaml | 560 |
| `commands/agents.py` | agent (report kinds + dispatch, profiles, providers, executions, instructions, setup, usage), strategy, hook, orchestrate, mcp, bin | 620 |
| `commands/skills.py` | skills | 300 |
| `commands/guide.py` | guide (+ `_guide_prov/_guide_review/_guide_quarto` as subcommand functions), report | 480 |
| `commands/hpc.py` | hpc (split `cmd_hpc` CC 106 into one function per action), workspace, sandbox, env | 600 |
| `commands/chat.py` | chat, gateway, hidden `telegram` alias | 220 |
| `tui.py` | unchanged structure; calls `api.call`; adds status bar + chat approval requests (WP16) | 560 |

Each `commands/*.py` exposes `attach(sub, add) -> None` (the existing `supervisor_cmds.attach_*` pattern) and keeps its parser block next
to its functions. `main.COMMANDS = [project, records, plans, supervisor, agents, skills, guide, hpc, chat]`.

**Extension `packages/vscode-extension/src/`**: unchanged structure (thin client). Deleted: `render/home.ts`, `render/resume.ts`,
`render/setup.ts` (after WP9 moves its last controls into the Control Center), `webview/Setup.tsx`, `MUTATING_RPC`. Kept on purpose:
the HTML-string entity pages (`render/detail.ts`, `form.ts`, `ui.ts`); rewriting them in React has no user value now.

### A.3 The op layer (sketch the WP7 agent implements)

```python
# research/api/__init__.py
class Op:
    __slots__ = ("name", "fn", "sync", "needs_init", "needs_project", "chat", "mcp")

OPS: Dict[str, Op] = {}

def op(name=None, *, sync=True, needs_init=True, needs_project=True, chat=None, mcp=None):
    """chat: None (not available in chat) | "read" (viewer+) | "decide" (operator; core still enforces governance).
    mcp: MCP tool name when the op is exposed to agents (descriptions/schemas stay hand-written in mcp.py)."""
    def deco(fn):
        OPS[name or fn.__name__] = Op(name or fn.__name__, fn, sync, needs_init, needs_project, chat, mcp)
        return fn
    return deco

class BadParams(ResearchError): exit_code = 2

def call(name, project, params, surface="rpc"):
    o = OPS.get(name) or raise_(ResearchError(f"unknown operation {name}", code=-32601))
    if surface == "chat" and o.chat is None: raise PolicyBlocked(f"{name} is not available from chat", hint="Use the terminal or VS Code.")
    p = project(sync=o.sync) if o.needs_project else project.root      # project: the long-lived handle factory (RPC/TUI/gateway)
    try: inspect.signature(o.fn).bind(p, **params)                     # only binding errors become "bad parameters" (fixes rpc.py:1206)
    except TypeError as e: raise BadParams(f"bad parameters for {name}: {e}")
    try: return o.fn(p, **params)
    finally:
        if o.needs_project and p.dirty: p.refresh_context()            # current.md once; p.dirty is read by the transport for "changed"
```

RPC methods become module functions with the project as first argument (`def finding_review(p, id, verdict, note=None)`), 1:1 with the
current `m_*` names so the wire protocol does not change. Tests that call `rpc.Server().m_x(...)` (6 files, 24 calls) switch to
`api.call("x", ...)`; tests that speak JSON lines to `serve` are untouched and are the safety net.

### A.4 How `main.py` (4,070 lines) is decomposed

| Lines today | Content | Goes to |
|---|---|---|
| 1-104 | imports, colors, `out_json`, `section`, `kv`, `fmt_*` | `output.py` |
| 105-364 | printers (`print_experiment`, `print_finding`, `print_resume`, `print_status`, `print_plan`, `print_task`, ...) | next to their command in `commands/*`; only `print_list`/`print_generic` stay in `output.py` |
| 367-414, 653-700, 2889-2904 | `cmd_ledger`, `cmd_init_from`, `cmd_init`, `cmd_project_init` | `commands/project.py` (one `init` command; `ledger` and `project-init` become hidden aliases with identical flags) |
| 415-505 | workspace, sandbox | `commands/hpc.py` |
| 505-557 | promote | `commands/supervisor.py` |
| 558-652 | rpc, version, self-update, tui, doctor, `_proj`, `mut`, `_is_readonly`, `_done` | `commands/project.py`; `_proj/_is_readonly` to `main.py`; `mut`/`MUTATING` deleted (A.1 rule 3) |
| 700-760 | status, resume, context, current, show, project set | `commands/project.py` |
| 752-1207 | question .. note | `commands/records.py` |
| 1208-1276, 3001-3166 | skills, agents-md | `commands/skills.py`; agents-md → `agent instructions` |
| 1277-1610 | guide, setup (agents) | `commands/guide.py`; `setup agents` → `agent setup` |
| 1611-2084 | strategy, hook, usage, experiment check, orchestrate, tuning, evidence, source, config, rebuild | `commands/agents.py` (strategy, hook, usage, orchestrate), `commands/records.py` (experiment check, tuning, evidence, source), `commands/project.py` (config, rebuild) |
| 2085-2329 | plan, task, finding review | `commands/plans.py`, review → `records.py` |
| 2330-2425 | search, run compare, run sweep | `project.py`, `records.py` |
| 2426-2761 | hpc (250-line `cmd_hpc`), datasets, slurm opts | `commands/hpc.py`, one function per action, dispatched by a dict |
| 2762-2806 | report | `commands/guide.py` |
| 2807-3000 | agents, execution, provider, dispatch, bin, mcp | `commands/agents.py` |
| 3169-3946 | `build_parser` (776 lines) | split: each module's `attach()` |
| 3947-4005 | `_run_args`, `_dispatch_args`, `_finding_args`, `_checkpoint_args` | with their command module |
| 4006-4070 | `main()` | `main.py`; the `fn is cmd_init` special cases become a `needs_project=False` attribute set by `attach()` (`x.set_defaults(fn=..., project=False)`) |

**CLI surface after regrouping** (old names keep working as hidden aliases until 4.0; `--help` shows only the new ones):

| New | Absorbs (hidden alias) |
|---|---|
| `init [--from FILE] [--layout ledger]` | `project-init`, `ledger`, classic `setup` wizard path |
| `status [--full]` | `resume` (= `status --full`) |
| `current [--quiet]` | `context` (= `current --quiet`, `context --print` = `current`) |
| `agent dispatch\|profiles\|providers\|executions\|instructions\|setup\|usage` (+ existing report kinds) | `dispatch`, `agents`, `provider`, `execution`/`exec`, `agents-md`, `setup agents`, `usage` |
| `chat setup\|status\|test\|pair\|allow\|revoke\|link\|unlink\|remove` | `telegram setup\|status\|test\|remove\|poll` |
| `gateway start\|stop\|status\|run\|logs` | (new) |
| `promote` | `plan autopromote` stays (different object: authorization); docs explain both |

Visible top-level commands drop from 60 to 45. The AGENTS.md managed block, `templates/` and MCP `INSTRUCTIONS` are updated to the new
names in the same WP; hidden aliases keep older agent sessions and user scripts working.

### A.5 Config handling

- `registry.py` is the only place a default exists. `registry.defaults()` returns the nested dict; `store.DEFAULT_CONFIG` is built from it
  at import time. Module `DEFAULTS` in `supervisor`, `notify`, `governance`, `autopromote` (and the per-module `cfg(p)` merges, 7 sites)
  are replaced by `registry.section(p, "supervisor")`, which returns the merged section (cached per `p.config` identity).
- Layers unchanged: built-in defaults <- machine `~/.config/research-os/config.yaml` <- project `.research/config.yaml`; comment-preserving
  edits through `configfile`. No key is renamed or removed (user configs keep working). New keys only (E.6, D.4).
- Secrets never in config: `telegram.json`, `discord.json`, `approval.json` and gateway state live in the machine directory, written only
  through `machine.write_secret` (0600 from creation).
- Acceptance: `test_config_parity` asserts `registry.defaults()` equals a frozen snapshot of today's merged defaults (taken in WP5 before
  the change), so no default moves silently.

### A.6 Error model

One hierarchy (exists): `ResearchError(message, hint, data)` with `exit_code`; `NotInitialized`, `PolicyBlocked` (exit 3), `BudgetExceeded`,
`NoWorkspace`, new `BadParams` (2). Keep `PolicyBlocked` as a subclass of `BudgetExceeded` (tests and agents depend on exit code 3 for both).
One serializer `ResearchError.to_dict() -> {message, hint, code, kind, data}` used by:

| Surface | Shape |
|---|---|
| CLI | text: `✗ message` + dim hint; `--json`: the dict on stderr; exit code = `code` |
| RPC | `{"id", "error": to_dict()}`; non-`ResearchError` exceptions: `code -32603` + short trace (unchanged) |
| MCP | `isError: true` result whose text is `message` + `Hint:` + `code` (unchanged wording) |
| Chat | `Not done: message` + hint, redacted; internal errors print only the exception type |
| TUI | `notify(message, severity="error")` + hint |

`except TypeError` around method calls is removed everywhere; only binding failures are `BadParams`.

### A.7 Version-compatibility contract (extension, TUI, CLI, agents, daemons)

| Party | Rule |
|---|---|
| Core version | SemVer. MAJOR: an op, CLI command or config key removed, or project data changed so older cores cannot read it. MINOR: new ops/keys/commands. PATCH: fixes. Hidden aliases are removed only at a MAJOR. |
| Extension → core | `package.json` `researchCore: "3.N"`: core major must equal 3 and core minor ≥ N (`compat.core_satisfies`, moved into `selfupdate.py`). Checked at activation with `research version --json` (exists). |
| Optional features | The RPC `ready` event adds `ops: [...]`. The extension hides a feature whose op is missing instead of failing (lets a newer extension run on an older 3.x core it accepts). `PROTOCOL` (now 1) changes only if framing changes. |
| TUI | Ships in the same wheel, in-process: always matched. |
| Agents | `.research/bin/research` becomes a 3-line launcher that execs the CLI that wrote it (`sys.executable -m research_cli.main`), refreshed by `init`, `doctor`, `self-update`; no vendored copy, so agents run the same version as the user. MCP registrations point at `research` on PATH or this launcher. |
| Daemons | Supervisor and gateway record `version` in their heartbeat (supervisor does already). `research self-update` and RPC startup call `daemonlib.ensure_current()` for every project in `machine.known_projects()`: a daemon on older code is restarted (agents untouched, as `daemon_restart` does today). |
| Project data | Schema migrations only forward (exists). Opening a project written by a newer major → the existing `_compat` refusal. |
| Self-update extras | `selfupdate._install` must reinstall the extras that are present (`textual`, `discord`): `uv tool install --force 'research-panel[tui,discord] @ file:///…whl'`, same for pipx; plain pip upgrade keeps them. Today `uv tool install --force wheel` silently drops them. |

### A.8 What NOT to change (risk outweighs gain)

- **The 280 lazy imports in `store.py` and friends.** Untangling `store <-> plans/runs/supervisor/...` touches every write path for no user
  value. Only lazy imports made dead by deletions go.
- **Data formats**: mirrors, JSONL, SQLite schema, config keys, `.research/` layout, `telegram.json` location. Additions only.
- **`supervisor.py` scheduler internals** (tick, launch, reconcile, verify, failure handling, grants). Only the generic daemon helpers move to
  `daemonlib.py`; `run_forever` stays in `supervisor.py` and `supervisord.py` stays the `-m` entry (single module object for grants).
- **`hpc/`, `guide_quarto.py`, `provenance.py`, `fullreport.py`, `isolation.py`**: no structural refactor (not audited deeply; well tested).
- **Vendored PyYAML** and stdlib-only core; **argparse** (no click/typer).
- **CLI through the op registry**: no (A.1 rule 4).
- **Generating MCP JSON schemas from type hints**: no; hand-written schemas stay, a contract test checks them against op signatures.
- **Extension HTML entity pages and the `CONTROL_RPC` allow-list**: the allow-list is a webview security boundary; it stays explicit in TS,
  checked by a test against `ops`.
- **Bundled third-party skills in `templates/catalog/`**: untouched.

---

## B. Gap analysis

### B.1 Today vs target, per area

| Area | Today | Target | What must migrate | WP |
|---|---|---|---|---|
| Python 3.8 | 5 `dict \| dict` crash sites; CI red | `{**a, **b}`; AST guard test | 5 expressions; 1 test; CI `fail-fast: false` | 1 |
| Governance | no PIN + no tty = approved; mirror import creates experiments/supported findings as `human` | fail closed unless `approval.require_pin: false`; import of a new experiment by an agent becomes a proposal; agent finding → `supported` refused | `approval.confirm`, `store.import_file`, messages without `env -u` recipe | 2 |
| Operations | 163 `Server.m_*`, 44 MCP tools wrapping them, TUI calling `m_*` | `api/*` registry; RPC/MCP transports; TUI via `api.call` | move each method body; keep names | 7 |
| CLI | `main.py` 4,070 lines, 60 commands | `main.py` 180 + 9 command modules; 45 visible commands + hidden aliases | A.4 | 8 |
| Mutation tracking | 4 lists + TUI skips refresh | `p.dirty` from `tx()`; `changed` in RPC responses | delete `MUTATING` (main, rpc), `Tool.mutating`, `MUTATING_RPC` | 5 (flag), 7, 9 |
| Sync locking | every read takes write lock | `tx()` only when ingesting; one `mirrors` load per sync | `runs.ingest_root_files`, `store.sync` | 5 |
| Config defaults | 3 sources | registry only | `DEFAULT_CONFIG`, 4 module `DEFAULTS`, 7 `cfg()` helpers | 5 |
| Providers | names hard-coded in ~15 places; `register()` unused; 2 MCP installers | `providers/` package with `KNOWN`, `LABELS`, `Adapter`; UI reads labels from RPC `providers`; one MCP installer | 11 sites + `setup.py` list + TS lists | 5 |
| Agent env detection | 4 lists (util, approval, client.ts, conftest) | `util.AGENT_ENV_VARS` (+ RPC `ping` returns it for client.ts) | 4 sites | 5 |
| Attention items | `control._attention` vs `context`/`verification` | `verification.attention(p)` | 2 call sites | 5 |
| Setup wizards | classic Setup + Initialize Project + YAML | Initialize Project + YAML form (`bootstrap`) | delete `setup.py`, `Setup.tsx`, `setup_*` RPC; move `new_experiment*` | 3 |
| Agent setup UI | HTML page `render/setup.ts` + Control Center Agents tab | Control Center only (add strategy switch + task pins there) | 2 controls | 9 |
| Extension refresh | `MUTATING_RPC` list | `changed` flag | `panels.ts` | 9 |
| Launcher | vendored copy of both packages in `.research/bin/lib` | exec launcher to installed CLI | `localbin.py` | 10 |
| Daemon freshness | nothing restarts the supervisor after update/reboot | `ensure_current()` on self-update and RPC startup; gateway auto-start | `selfupdate.py`, `rpc.py` | 10 |
| Telegram | in each supervisor; shared offset; replay; group = anyone | machine gateway; per-update offset save; allow-list by user id; pairing | E | 14 |
| Discord | none | discord.py adapter on the same router | E | 15 |
| opencode | none | full adapter | D | 13 |
| Tests | 1,122 tests; red; organised by release; 6 `slow` marks | green; per-module files; markers by conftest; fast default ≤ 3 min | C.3 | 1, 6, 11 |
| Docs | README/SPEC describe 2.6; SPEC 3,018 lines | README CLI-first; SPEC = index + 12 chapters, generated reference tables | C.4 | 4, 12 |

Inventory findings → WP: 1 → WP1; 2, 3 → WP2; 4 → WP2 (offset, 0600) and WP14 (root fix); 5 → WP4; 6 → WP10 (+ RPC startup hook in WP7);
7, 8 → WP1; 9 → WP5a; 10 → WP7, WP8; 11 → WP3, WP9; 12 → WP3 (+ WP5a for `register`/`retry_task`); 13 → WP5b, WP7, WP9; 14, 15, 16 → WP5b;
17 → WP7; 18 → WP8; 19 → WP7 (`mcp._merge_json` → `store._atomic_write`) and WP5a (`providers.sync_instructions`); 20 → WP6.

### B.2 Deletions (every one: justification and compat consequence)

Python core:

| Item | Why | Compat consequence | Shim? |
|---|---|---|---|
| `plans.add_task_artifact`, `supervisor.retry_task` | zero callers anywhere | none (internal) | no |
| `agentsmd.outside_bytes` + its test | test-only | none | no |
| `providers.register` | no caller; a registered provider would not appear in any list anyway | Python API users (none known) | no; PROVIDERS.md documents adding a module under `providers/` |
| `store.py:827` unused `_pl` import | vulture | none | no |
| RPC `get_plan`, `get_task`, `list_plans`, `list_tasks`, `run_create`, `run_update` | no caller in extension, MCP, CLI or tests | RPC is the private extension protocol; no extension version ever called them | no |
| RPC `agents_md`, `block_task`, `complete_task`, `start_task`, `install_bin`, `next_task`, `guide_chapter_add` | tests only | same; their tests are rewritten against the core functions | no |
| RPC `setup_environment`, `setup_preview`, `setup_apply` + `setup.py` (`environment/_settings/plan/apply`) | duplicate first-run wizard that disagrees with Initialize Project (gemini vs agy) | users of the "classic" button use Initialize Project; projects created by it are ordinary projects | no |
| `rpc.MUTATING`, `main.MUTATING`/`@mut`, `mcp.Tool.mutating` | replaced by `p.dirty` | none externally | no |
| Module `DEFAULTS` in supervisor/notify/governance/autopromote | registry owns defaults | values identical (snapshot test) | no |
| `approval._env_hint` text `env -u CLAUDECODE ...` | hands agents the bypass recipe | message text only | no |
| `notify.discover_chat` ("first chat that messages the bot") | replaced by pairing | existing `telegram.json` keeps working (E.7) | migration, no shim |
| `localbin` vendored `lib/` copy | replaced by exec launcher | `.research/bin/research` path unchanged; `lib/` removed on next refresh | no |
| `compat.py` | 12 lines; folded into `selfupdate.py` | import path `research.compat` used only by selfupdate/tests | no |

CLI flags/commands: none removed in 3.x. `resume`, `context`, `project-init`, `ledger`, `setup agents`, `dispatch`, `agents`, `provider`,
`execution`/`exec`, `agents-md`, `usage`, `telegram` become hidden aliases (A.4). Removal at 4.0 is listed in CHANGELOG "Deprecated".

Extension:

| Item | Why | Compat | Shim? |
|---|---|---|---|
| `panels.openResume`, `renderResume`, `render/home.ts`, `render/resume.ts` | unreachable (no caller) | none; `scripts/preview-entry.ts` loses two preview pages | no |
| `webview/Setup.tsx`, `onClassic` toggle in `App.tsx` | classic wizard deleted | none | no |
| `render/setup.ts` ("Agent setup" HTML page) + RPC `agent_setup` | overlaps Control Center Agents | command `research.agentSetup` opens the Control Center Agents tab instead (keep the command id) | command id kept |
| `Empty` in `webview/common.tsx` | ts-prune | none | no |
| `MUTATING_RPC` | `changed` flag | none | no |
| settings: none removed | | | |

Tests: `test_command_examples` per-example parser rebuild (build once); tests of the deleted RPC methods; `test_setup.py` (classic wizard)
after porting its still-valid cases to `test_initproject.py`.

Docs: `docs/GUIDE_V2_START_FRESH.md` (one paragraph moves to README "Upgrading from 2.x"), `docs/INSTALL_UPDATE.md` (merged into README
Install), SPEC "Stabilization behavior changes", §14G "2.1 to 2.6" history, any v1/migration text. Dev-only scripts on `main`
(`scripts/make_preview_fixture.py`, `packages/vscode-extension/scripts/preview-*`): see F.5.

---

## C. Migration plan

Conventions for every WP: branch `development`; one commit per WP (`cleanup: WPn <title>`); bump nothing until the release WP (WP17
bumps once per release); update the docs that the WP makes wrong in the same commit (AGENTS.md rule). Standard checks, referred to as
**CHECKS**:

```bash
python -m pytest -q                                   # fast default suite after WP6 (all tests before WP6)
python -m pytest -q -m "slow or not slow"             # full suite (CI and release)
npm run typecheck && npm run test:client && npm run test:webview
python -c "import research_cli.main"                  # import smoke
```

### C.1 Waves (packages in one wave can run in parallel; no file is owned by two packages of the same wave)

| Wave | Packages |
|---|---|
| 0 | WP1 |
| 1 | WP2, WP3, WP4, WP6 |
| 2 | WP5, WP10 |
| 3 | WP7, WP8 |
| 4 | WP9, WP11, WP12, WP13 |
| 5 | WP14 |
| 6 | WP15, WP16 |
| 7 | WP17 |

### C.2 Work packages

**WP1 Green baseline (wave 0, mechanical, risk low)**
- Owns: `planspec.py` (L249 only), `supervisor.py` (L409 only), `rpc.py` (L894 + `serve` exception handling), `research_cli/main.py` (L2829, L2863),
  `tests/python/conftest.py`, `test_core.py`, `test_guide.py`, `test_release_1_0.py`, `test_research_os.py`, `test_orchestrate.py`,
  new `tests/python/test_py38_syntax.py`, `.github/workflows/ci.yml`.
- Do: replace the 5 `dict | dict` with `{**a, **b}`; AST test that fails on `BinOp(BitOr)` whose operands are dict displays/comprehensions or
  names bound to them, and on any `str.removeprefix`/`removesuffix`, `functools.cache`, walrus in comprehensions over 3.8 grammar (keep it
  to `dict | dict` plus `ast.parse(feature_version=(3, 8))` per file). In `rpc.serve`, bind params with `inspect.signature(fn).bind(**params)`
  first; only that `TypeError` becomes -32602. Fix `test_synthesis_required_before_new_experiment`: after synthesis, the agent's second
  create runs inside `with governance.approved("test"):` and the first `pytest.raises` asserts `"synthesis" in str(ex.value)` via a human-
  approved path, so it no longer passes for the wrong reason (`PolicyBlocked` subclasses `BudgetExceeded`). Give the guide/release tests a
  human author or a typed task (inventory finding 7). `_clean_env`: set `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, put `packages/cli` launcher on
  PATH for `test_orchestrate`. Add a session-scoped autouse **daemon reaper**: at teardown, for every `daemon.json` under the session tmp
  root, write the `stop` marker and kill the pid if it is a `research.supervisord` whose `--root` is under tmp. CI: `fail-fast: false`.
- Acceptance: CHECKS (full suite, both 3.8 via `uv run --python 3.8 python -m pytest -q` and 3.12) green; `pgrep -f research.supervisord`
  shows nothing after the suite.

**WP2 Governance hardening (wave 1, behaviour change: security tightening, risk medium)**
- Owns: `approval.py`, `governance.py`, `store.py` (`import_file` region L790-858 and the unused `_pl`), `notify.py` (`poll` writer only),
  `tests/python/test_governance.py`, `docs/GOVERNANCE.md` (SPEC text for the same rules is written by WP4 from this design).
- Do: (1) `confirm`: `if ap["require_pin"] and not is_set(): enroll() if interactive() else raise PolicyBlocked("No approval PIN is set on
  this machine", hint="Set one: `research approval set-pin` or VS Code Settings → Approval PIN. To run without a PIN, set approval.require_pin:
  false (machine config).")`. (2) Drop the `env -u` recipe; agent-environment hint says "run it in your own terminal". (3) `import_file`: a NEW
  experiment mirror authored by an agent environment (`detect_author`) goes through `governance.check_experiment_create`; if refused, import as
  `status: proposed` and file a proposal via `supervisor.propose_experiment(..., source="mirror")` with an `import_rejected` event; an agent edit
  setting a finding to `supported` is reverted to its previous status with an event (same pattern as the task-`done` guard L825-835).
  (4) `notify.poll`: save the offset before handling each update and write with `os.open(..., 0o600)`.
- Tests (new): no PIN + no tty + human → PolicyBlocked; `require_pin: false` → approved; agent-written experiment mirror → proposal, not
  experiment; agent finding → supported refused; `poll` with `send` raising does not replay; token file mode stays 0600 after `poll`.
- Compat: users who never set a PIN now get a clear refusal for UI/RPC approvals until they set one (the extension already has PIN entry in
  Settings and Initialize Project). Explicit opt-out remains. See F.1.
- Acceptance: CHECKS; `grep -rn "env -u" packages/core` empty.

**WP3 Dead code and the classic wizard (wave 1, mechanical, risk low)**
- Owns: `rpc.py` (all but WP1's lines, done already), `plans.py`, `agentsmd.py`, `setup.py` (delete), `services.py`, `mcp.py` (no change except
  imports if needed), `tests/python/test_setup.py` (port valid cases to `test_initproject.py`, then delete), `test_initproject.py`, tests that call the
  deleted RPC methods (`test_plans_tasks_interfaces.py`, `test_release_safety.py`, `test_localbin.py` — rewrite against core functions),
  extension `src/panels.ts`, `render/home.ts`, `render/resume.ts`, `webview/Setup.tsx`, `webview/App.tsx`, `webview/common.tsx`,
  `scripts/preview-entry.ts`, `scripts/preview-harness.tsx`, `test/webview.test.cjs` (if it references deleted pages).
- Do: every B.2 core/extension deletion except `render/setup.ts` (WP9), `providers.register` (WP5), `supervisor.retry_task` (WP5), MUTATING
  lists (WP7/WP9). Move `setup.new_experiment_plan/new_experiment` to `services.py`; RPC `new_experiment_preview/new_experiment` call them.
  Remove duplicates in `rpc.MUTATING`. (SPEC §12 method list is updated by WP4 from the B.2 list.)
- Acceptance: CHECKS; `uvx vulture packages/core/research --min-confidence 80 --exclude _vendor,templates` reports nothing new;
  `npx ts-prune -p packages/vscode-extension/tsconfig.json` no unused exports except entry points.

**WP4 Docs facts and README (wave 1, docs only, risk low)**
- Owns: `README.md`, `docs/SPEC.md` (header, §0 counts, §7.2 + §15.6a (no-PIN rule and synthesis gate as decided in WP2/F.1), §7.8, §12
  (remove the B.2 RPC methods), §13A, §13.8, §22), `docs/SUPERVISOR.md` §18,
  `docs/INSTALL_UPDATE.md` (delete), `docs/GUIDE_V2_START_FRESH.md` (delete), `docs/QUICKSTART.md`, `docs/SETUP.md`, `CHANGELOG.md` (3.0.0 entry
  corrections: supervisor no longer starts on project open).
- Do: README Install rewritten CLI-first (`uv tool install` / `pipx install` the release wheel, then the VSIX; `research self-update`); remove
  "VSIX bundles its Python core" and the shim claims; "Upgrading from 2.x" paragraph; QUICKSTART/SETUP gain a CLI-only path.
- Acceptance: `python -m pytest -q tests/python/test_command_examples.py`; `grep -n "2\.6\.0\|bundles its Python" README.md docs/SPEC.md` empty.

**WP6 Test infrastructure (wave 1, risk low)**
- Owns: `tests/python/conftest.py` (after WP1), `pyproject.toml` (`[tool.pytest.ini_options]`, `dev` extra), `tests/python/test_command_examples.py`,
  `.github/workflows/ci.yml` (after WP1), `AGENTS.md` (release step test command).
- Do: markers `slow` (end-to-end subprocess workflows), `daemon` (starts a supervisor or gateway process), `pty` (needs `/dev/tty`), `ext` (needs
  node). Assign them in `pytest_collection_modifyitems` by file and fixture name (no test-file edits, so no ownership clash): `daemon` for tests
  using `daemon_start`/`plan_run(start_daemon=True)` paths and files `test_cli_e2e`, `test_supervisor*`, `test_autopromote`; `pty` for
  `test_cli_pin_link_escalation`, `test_tui` pty cases; `slow` for `test_acceptance`, `test_hpc` bundle executions, and any test over 2 s in
  `--durations=0` of the full run (record the list in conftest), plus `test_guide_quarto` real Quarto/Typst builds and the
  `test_fullreport` byte-identity export (15 s setup). `addopts = -m "not slow and not daemon" -n auto` (`pytest-xdist` added to the `dev` extra
  and dependency group; tests already isolate by `tmp_path` and per-test `XDG_CONFIG_HOME`); CI and release run `-m "slow or not slow"`,
  with daemon tests in a separate `-n 0` step. Fix any xdist-unsafe test (shared ports in fake servers: bind port 0). `test_command_examples`: build the parser
  once (module fixture), add `docs/*.md` to `FILES`, parse examples by fenced code blocks instead of prose `split('…')`.
- Target (baseline: 25.5 min serial full run locally): fast default suite ≤ 3 min wall with `-n auto` on an M-series laptop, ≤ 10 min with
  `-n 0`; full suite ≤ 12 min in CI. If over, move the next offenders to `slow`.
- Acceptance: `time python -m pytest -q` ≤ 180 s; full suite count equals today's count minus deleted tests.

**WP5 Core dedupe (wave 2, refactor, risk medium)** (can be split by the executor into 5a/5b along the owner lists, still in wave 2)
- 5a owns: `providers.py` → `providers/` package (`__init__`, `claude`, `codex`, `agy`, `custom`), `supervisor.py` (L620-655 provider lists,
  `DEFAULTS` → registry, delete `retry_task`), `initproject.py`, `bootstrap.py`,
  `strategy.py` (+ `use(p, name)` extracted from `main.py:1679-1695`/`rpc.py:1105-1116`), `util.py` (`AGENT_ENV_VARS`), `research_cli/main.py`
  and `rpc.py` (only the call sites: provider rows → `providers.table(p)`, strategy switch → `strategy.use`), extension `webview/InitProject.tsx`,
  `webview/Providers.tsx`, `src/client.ts` (env strip list from `ping`), `commands.ts` L1261, `tests/python/test_providers.py`, `test_handoff.py`.
- 5b owns: `store.py` (`DEFAULT_CONFIG`, `p.dirty`, `refresh_context()`, sync ingest-only-when-needed, one `mirrors` load; default agent
  profiles generated from `providers.KNOWN` as `{name: {provider: name, command: name}}`, so a new provider needs no defaults edit), `runs.py`
  (`ingest_root_files`), `registry.py`, `configfile.py`, `notify.py` (`DEFAULTS`), `governance.py` (`DEFAULTS`), `autopromote.py` (`DEFAULTS`),
  `control.py`, `context.py`, `verification.py` (`attention`), `tests/python/test_config*.py`, `test_registry.py`.
- Acceptance: CHECKS; `grep -rnE "\(\"claude\", ?\"codex\"|'claude', ?'codex'" packages/ --include=*.py --include=*.ts --include=*.tsx`
  returns only `providers/__init__.py`; config snapshot test green; a test that 20 concurrent `research status` calls never take
  `.write.lock` when nothing changed (patch `durability.lock` to count).

**WP10 Launcher, machine registry, self-update (wave 2, behaviour change, risk medium)**
- Owns: `localbin.py`, `selfupdate.py` (+ folded `compat.py`), `compat.py` (delete), `machine.py` (new), `daemonlib.py` (new: lock, detached
  spawn, heartbeat, `ensure_current(root)` calling the existing `supervisor.daemon_status/daemon_restart`; WP14 moves the gateway onto it),
  `tests/python/test_localbin.py`,
  `test_selfupdate.py`, extension `src/cli.ts` (import of compat logic unchanged on TS side), `docs/SUPERVISOR.md` §18 (after WP4).
- Do: A.7 launcher; `machine.known_projects` written by `init`, RPC startup and `supervisor start` (callers wired in WP7/WP8; WP10 provides
  the functions and calls them from `localbin.install`); `selfupdate.run` → after install, `ensure_current()` for known projects and extras
  preservation (A.7). Python-level only; no daemon behaviour change beyond restart-on-stale.
- Acceptance: CHECKS; test: launcher execs the interpreter recorded; update with fake installer passes the detected extras.

**WP7 Op layer (wave 3, refactor, risk medium-high)**
- Owns: `research/api/*` (new), `rpc.py`, `mcp.py`, `research_cli/tui.py`, tests calling `Server.m_*` (`test_agent_features.py`,
  `test_providers.py`, `test_localbin.py`, `test_workspace.py`, `test_strategy.py`, `test_tui.py`), new `tests/python/test_ops_contract.py`.
- Do: A.3. Move every `m_*` body into `api/<domain>.py` in file order; delete the `Server` class except a compatibility `Server` with
  `project()` used by `mcp.py`/tests during the move (delete at the end). `ready` gains `ops`. Per-request author override, restored after the
  call (fixes sticky author). Merge `mcp.install` and `providers.mcp_install` into `mcp.install(root, clients)` with per-provider writers
  delegated to adapters. TUI calls `api.call`. RPC startup calls `machine.remember(root)` and `daemonlib.ensure_current(root)` (non-blocking).
- Contract tests: every op name used by the extension (`grep -o "rpc('\w+'\|call('\w+'" src`) and in `CONTROL_RPC` exists in `OPS`; every MCP
  tool's `props` keys ⊆ its op's parameters and `required` ⊆ parameters without defaults; every op with `chat=` set has a chat test; JSON-lines
  golden test: a recorded session of 30 representative requests produces byte-identical results (minus timestamps) before and after.
- Acceptance: CHECKS; `wc -l packages/core/research/rpc.py` < 200.

**WP8 CLI split and regroup (wave 3, refactor, risk medium)**
- Owns: `packages/cli/research_cli/*` (except `tui.py`), `templates/` text that names CLI commands, `agentsmd.py` block text, `mcp.py`
  `INSTRUCTIONS` string only (coordinate: WP7 owns the rest of `mcp.py`; this is one constant — WP8 sends WP7 the new text, WP7 applies it).
- Do: A.4, mechanical moves first (one commit per module is fine inside the WP), then the hidden aliases (`argparse` `aliases=` plus
  `help=argparse.SUPPRESS` subparsers that set the same `fn` with mapped flags). `main()` special cases become `project=False` defaults.
- Acceptance: CHECKS; `test_command_examples` green over README and docs; a new test runs `research <old-name> --help` for every hidden alias
  and asserts exit 0; `wc -l research_cli/main.py` < 250; radon: no function above C (CC > 20) in `research_cli/` (`uvx radon cc -n C`).

**WP9 Extension thinning (wave 4, risk medium)**
- Owns: `packages/vscode-extension/src/*` (except files WP13/14 do not touch; they do not touch the extension), `test/*`.
- Do: delete `MUTATING_RPC`, refresh on `result.changed`; hide features whose op is missing from `ready.ops`; add strategy switch + task pins
  to the Control Center Agents tab, then delete `render/setup.ts` and RPC `agent_setup` (core side: one function in `api/agents.py`, coordinate
  with nobody in wave 4: WP7 is done); `research.agentSetup` command opens Control Center → Agents.
- Acceptance: `npm run typecheck && npm run test:client && npm run test:webview && npm run test:ext`; UX scenarios G.3 #1-#12 pass.

**WP11 Test reorganisation (wave 4, risk low)**
- Owns: all `tests/python/test_*.py` not created by WP13 (WP13 creates only new files).
- Do: merge by module: `test_release_1_0`, `test_release_safety`, `test_research_os`, `test_agent_features`, `test_cli_pin_link_escalation`
  dissolve into `test_hooks.py`, `test_skills.py`, `test_cli.py`, `test_approval.py`, `test_store.py`; `test_supervisor_fixes` → `test_supervisor`;
  `test_guide_review` → `test_guide`. No assertion is dropped (count asserted before/after, minus exact duplicates listed in the commit message).
  Add the missing-coverage tests from the inventory §4.3 not already added (TUI approve with no PIN: refuses with the WP2 hint).
- Acceptance: CHECKS; `pytest --collect-only -q | tail -1` count ≥ before − duplicates.

**WP12 Docs restructure (wave 4, docs, risk low)**
- Owns: `docs/SPEC.md`, new `docs/spec/*.md`, `docs/CONFIGURATION.md`, `docs/AGENT_TOOLS.md`, `docs/SUPERVISOR.md` (not `PROVIDERS.md`: WP13),
  new `tests/python/gen_reference.py` + `test_docs_reference.py`.
- Do: C.4.
- Acceptance: `python -m pytest -q tests/python/test_docs_reference.py tests/python/test_command_examples.py`; every SPEC link resolves
  (simple link checker in the same test).

**WP13 opencode provider (wave 4, feature, risk medium)** — section D. Depends on WP5 (`providers/`), WP7 (MCP installer), WP8 (`agent setup`).
- Owns: `providers/opencode.py` (new), `providers/__init__.py` (register in `ADAPTERS` only), `usage.py`, `execution.py` (watchdog),
  `isolation.py` (`SECRET_DIRS`), `doctor.py` (opencode checks), `research_cli/commands/agents.py` (`agent setup opencode` branch only),
  `tests/python/fakes/opencode` (new), `tests/python/test_opencode.py` (new), `docs/PROVIDERS.md` (rewritten: generated capability table, opencode,
  D.12 verification results). Needs no registry edit (WP5b generates default profiles from `KNOWN`).
- Version bump: minor (feature).

**WP14 Chat core + Telegram port (wave 5, feature + behaviour change, risk high)** — section E.1-E.8. Depends on WP7, WP10.
- Owns: `chat/` package (all but `discord_bot.py`), `notify.py`, `supervisor.py` (one call: `notify.step` stays; gateway ensure in
  `daemon_start`), `api/chat.py`, `research_cli/commands/chat.py`, extension `webview/PlansPage.tsx` (TelegramBox → ChatBox),
  `tests/python/test_chat_*.py` (new), `test_intervention.py` (Telegram parts move to `test_chat_telegram.py`), `test_cli_e2e.py` (FakeTelegram),
  `docs/CHAT.md` (new), registry keys.
- Version bump: minor.

**WP15 Discord adapter (wave 6, feature, risk medium)** — section E.9-E.12. Depends on WP14.
- Owns: `chat/discord_bot.py`, `pyproject.toml` (`discord` extra), `tests/python/test_chat_discord.py`, `tests/python/fakes/fake_discord.py`,
  `docs/CHAT.md` (Discord section).

**WP16 TUI and chat UX (wave 6, feature, risk low)**
- Owns: `research_cli/tui.py`, `tests/python/test_tui.py`.
- Do: status bar (running agents, today's spend from the usage ledger, unattended-mode warning when autopromote is authorized, gateway
  state); Approvals tab sorts proposals with `requested_from_chat` first and shows "requested from Discord/Telegram by <name> at <time>";
  `a` asks for the PIN as today; with no PIN set it offers `set-pin` in a modal instead of failing.

**WP17 UX campaign and release (wave 7)** — section G; fix bugs found (each fix is a small follow-up commit); version bump; CHANGELOG; README;
release per AGENTS.md (only when the owner says so).

### C.3 Test refactor summary

| Action | What |
|---|---|
| Fix | `test_synthesis_required_before_new_experiment` (WP1, root cause: governance blocks agents before the synthesis gate; the test now asserts the gate on the approved-proposal path and checks the message), 4 governance/env-dependent siblings |
| Delete | tests of deleted RPC methods (rewritten against core), `test_setup.py` after porting, per-example parser rebuild |
| Merge | release-named files into per-module files (WP11) |
| Speed | markers via conftest; daemon tests out of the default run; xdist in CI; session-scoped project templates (exist) used by more files |
| Add | py3.8 AST guard; RPC binding; governance (4); Telegram replay/0600/multi-project; ops contract; JSON-lines golden; hidden aliases; opencode (D.11); chat (E.13); daemon reaper |
| Targets | default `pytest` ≤ 3 min local with xdist (from a 25.5 min serial full run); full ≤ 12 min CI; zero leaked daemons |

### C.4 Docs refactor

| Doc | Action |
|---|---|
| `docs/SPEC.md` (3,018 lines) | becomes a ~150-line index: purpose, principles, a map of chapters, the compat contract (A.7), the safety guarantees list. Chapters in `docs/spec/`: `01-architecture.md` (A.1-A.2), `02-data-model.md` (§3), `03-storage-sync.md` (§4-5), `04-lifecycles.md` (§6), `05-agents-governance.md` (§7, 14B), `06-execution.md` (§8, 14, 14C-E), `07-context-reports.md` (§9, 14A), `08-supervisor.md` (§6.8, 14F-G, chat), `09-interfaces.md` (CLI, RPC, MCP, TUI, extension §10-13, 13A), `10-configuration.md`, `11-testing-release.md` (§18-20), `12-rationale-limitations.md` (§21-22). Roadmap (§23) and all version-history text are deleted (CHANGELOG has it). |
| Generated tables | CLI reference (from argparse), RPC/op list (from `OPS`), MCP tool list (from `mcp._tools()`), config reference (from `registry`): `tests/python/gen_reference.py` writes them between `<!-- generated:NAME -->` markers; `test_docs_reference.py` fails when stale and prints the regenerate command. This kills the count drift for good. |
| README | CLI-first (WP4), feature tour, links into `docs/` |
| `docs/PROVIDERS.md` | capability table generated from `providers.ADAPTERS`; "Adding a provider" = add a module under `providers/` + tests (fake binary pattern) |
| `docs/CHAT.md` (new) | Telegram and Discord setup, pairing, roles, what chat can and cannot do, gateway |
| `docs/GOVERNANCE.md` | rewritten in WP2 (one consistent no-PIN rule) |
| `docs/SUPERVISOR.md` | user guide; Telegram sections move to CHAT.md |
| Keep | `QUICKSTART`, `SETUP`, `CONFIGURATION` (links to generated reference), `AGENT_TOOLS`, `GUIDE` |
| Delete | `GUIDE_V2_START_FRESH.md`, `INSTALL_UPDATE.md` (WP4) |

---

## D. Feature design 1: `opencode` provider

Facts used: the research file §2 plus the verifications in the corrections table. Anything still unverified is listed in D.12 and must be
checked against a real install (both a v1.19.x and a v2.x binary) by WP13 before merge; the fake binary encodes what was verified.

### D.1 Adapter record (shape used by every provider after WP5)

```python
Adapter(name="opencode", label="OpenCode", build=build_argv, caps=CAPS, version=version_info,
        resume_probe=resume_supported, resume_argv=resume_argv, session_id=session_id_of, usage_parser="opencode_jsonl",
        final_event=is_final_event, env=extra_env, mcp_install=mcp_install, mcp_registered=mcp_registered,
        secret_dirs=(".local/share/opencode",))
```

### D.2 Capability table (honest values)

| Capability | claude | codex | agy | opencode |
|---|---|---|---|---|
| interactive | yes | yes | yes | yes (`opencode` TUI) |
| headless | `-p --output-format json` | `exec --json` | `-p --output-format json` | `run --format json` |
| model | `--model` | `-m` | `--model` | `-m provider/model` (provider prefix required) |
| effort | no | `-c model_reasoning_effort=` | `--effort` | `--variant` (provider-specific values, passed through) |
| structured_output | json | jsonl | json | jsonl |
| usage | yes | no | no | yes (`step_finish.tokens`, `cost`) |
| resume | probe `--resume` | probe `exec resume` | probe `--conversation` | probe `run --help` for `--session` |
| mcp | `.mcp.json` | `codex mcp add` | `.agents/mcp_config.json` | `opencode.json` `mcp` block (project) + inline per run |
| instructions | CLAUDE.md (`@AGENTS.md`) | AGENTS.md | AGENTS.md | AGENTS.md (CLAUDE.md only if no AGENTS.md) |
| skills | `.claude/skills` | `.agents/skills` | `.agents/skills` | reads `.claude/skills` and `.agents/skills` (name regex `^[a-z0-9]+(-[a-z0-9]+)*$`) |
| agent_files | yes (`setup_agents`) | no | no | no in this release (D.9) |
| watchdog | no | no | no | yes (D.6) |

### D.3 Version probing

`version_info(cmd) -> {"raw", "major", "minor", "patch", "line": "v1"|"v2"}`: run `opencode --version` (15 s timeout); match
`(\d+)\.(\d+)\.(\d+)`; cache by `(realpath(cmd), mtime)`. Unparseable → `major=None`, readiness `problem: "could not read opencode version"`,
headless dispatch refuses (better than guessing a config schema). `major == 1` → doctor warning "OpenCode 1 is end-of-life upstream; run
`opencode upgrade --major`" (still supported). Config generation (D.4) branches on `major`.

### D.4 argv, config injection, model and effort

Headless argv (prompt is short: "Read the brief at …"; the brief itself is attached so the agent never needs to read outside the worktree):

```
opencode run --format json [-m PROVIDER/MODEL] [--variant EFFORT] [--title "T-12 implementer"] -f <abs brief path> "<prompt>"
```

- Working directory: subprocess `cwd` (as for agy). Do not pass `--dir` (avoid v1/v2 differences).
- Model: profile `model` must contain `/`; otherwise `ResearchError("opencode needs provider/model, e.g. anthropic/claude-sonnet-5-5")`.
  Strategy levels keep mapping to profiles; a user who wants opencode in a strategy defines `opencode-strong/-standard/-light` profiles
  (template in PROVIDERS.md). `available_models` for opencode: `opencode models` output (one id per line) when it exits 0 within 30 s, else
  profile models (same pattern as agy).
- Effort: `--variant` verbatim from profile/strategy `effort`.
- Interactive argv: `opencode [-m MODEL]` with cwd = workspace and env from dispatch; the prompt is shown to the user to paste unless the
  installed CLI's `--help` lists `--prompt` (probe), in which case `--prompt PROMPT` is passed.
- Env per run (`extra_env`): `RESEARCH_AGENT=opencode` and the usual dispatch env (inherited by opencode's shell tool, which spawns from the
  process env), plus `OPENCODE_CONFIG_CONTENT` = JSON:
  - `mcp.research = {"type": "local", "command": [<research argv> "mcp"], "enabled": true, "environment": {"RESEARCH_AGENT": "opencode",
    "RESEARCH_AGENT_ROLE": role, "RESEARCH_EXECUTION": id}}`
  - permissions (headless only; interactive sessions keep the user's own config):
    - v2: `"permissions": [{"action": "edit", "resource": "*", "effect": "allow"}, {"action": "shell", "resource": "research *", "effect": "allow"},
      {"action": "shell", "resource": ".research/bin/research *", "effect": "allow"}, {"action": "shell", "resource": "*/.research/bin/research *",
      "effect": "allow"}, {"action": "mcp", "resource": "research_*", "effect": "allow"}]`. No `*` deny rule: in v2 a deny wins within an
      operation, so `shell * deny` would also block `research …`. Everything unmatched is `ask`, which `run` auto-rejects (verified in
      `noninteractive.ts`). Same posture as claude's `allowedTools` + `acceptEdits`.
    - v1: `"permission": {"edit": "allow", "bash": {"*": "ask", "research *": "allow", ".research/bin/research *": "allow",
      "*/.research/bin/research *": "allow"}, "webfetch": "ask"}`; `ask` auto-rejects headless in v1 too (run.ts).
  - Never `--auto`/`--yolo`/`--dangerously-skip-permissions`; a profile `args` containing them is refused by `build_argv` with a policy error.

### D.5 JSON event parsing, session, usage, resume

- `parse_opencode_jsonl(text)` (in `usage.PARSERS`): for each JSON line: `sessionID` (first seen), `type == "text"` → append to
  `result_text` (keep last 4,000 chars), `type == "step_finish"` → sum `part.tokens.{input,output,reasoning,cache.read,cache.write}` and
  `part.cost`, remember `part.reason`; `type == "error"` → `errors.append(message)`. Returns `{session_id, result_text, input_tokens,
  output_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens, cost_usd, final_reason, errors, complete: bool}`. Unknown types
  ignored. Malformed lines ignored (counted).
- `session_id_of("opencode", out)`: first `sessionID` value.
- Resume: `resume_supported` probes `opencode run --help` for `--session`; `resume_argv` inserts `--session ID` right after `run`
  (keeps `--format json`, model, variant, `-f`). Unprobeable → context handoff (existing fallback).
- Usage recording: dispatch picks `usage_parser="opencode_jsonl"` for headless opencode (generalise `agents.py:377` to
  `adapter.usage_parser if headless`). `complete=False` → usage recorded with `"partial": true` (never zero, never dropped).

### D.6 Watchdog (hang after final event, missing final event)

Generic in `execution.launch`, enabled when the adapter defines `final_event`:
- Instead of a blocking `wait()`, poll every 1 s: read new bytes of the `.out` file; feed complete lines to `final_event(line)`
  (opencode: `type == "step_finish"` and `part.reason` in `{"stop", "end_turn", "length"}`; `"tool-calls"` is not final).
- After a final event, wait `profile.exit_grace_seconds` (default 30). Still alive → `processes.stop_process` (process group), record
  `exit_code=0`, `watchdog: "stopped after final event (provider did not exit)"`, event `execution_watchdog`.
- Idle watchdog (generic, opt-in per profile `idle_timeout_seconds`; default 1800 for opencode, unset for others to keep their behaviour):
  no new output for that long → stop, `exit_code=124`, `watchdog: "no output for N s"`. The supervisor treats it as a provider error and its
  existing retry/fallback/handoff applies.
- Exit 0 with no final event → `state=completed`, `provider_warning: "no final event; usage may be partial"`. Verification still decides the
  task outcome (unchanged supervisor logic), so a missing event never marks a task verified by itself.

### D.7 Governance without hooks

ResearchOS uses no provider hooks today (no `PreToolUse`); governance lives in the core and keys off the author:
1. Dispatched sessions (supervisor, `agent dispatch`): `RESEARCH_AGENT=opencode` in the process env → every `research` call from opencode's
   shell is agent-attributed; MCP calls carry the same env through the `environment` block. Protected actions refuse agents in core.
2. PIN-gated actions need a PIN read from `/dev/tty`; after WP2, no PIN + no tty fails closed. An opencode agent does not know the PIN.
3. Ad-hoc opencode sessions started by the user: opencode exposes no env var to child shells (verified: #9292 closed not planned), so plain
   `research` calls look human. Mitigations, decided: (a) `research agent setup opencode` writes the project `opencode.json` MCP entry with
   `environment.RESEARCH_AGENT=opencode`, so MCP use is attributed; (b) `research agent open opencode [--task T-12]` launches opencode with
   the agent env (documented as the supported way to start an interactive opencode session in a research project); (c) doctor warns
   "interactive opencode sessions started outside `research agent open` are recorded as you". This is the same exposure codex had before its
   env detection; it cannot weaken PIN-gated actions (point 2). A plugin-based env injection is rejected: plugins are JS in-process and
   version-specific.
4. Completion of supervised tasks stays guarded by `supervisor.guard_completion` (provider-independent).

### D.8 MCP registration

`mcp_install("opencode", root)`: if `opencode.json` exists or no opencode config exists → JSON-merge `mcp.research` (keep other servers and
keys; atomic write via `store._atomic_write`; same key name in v1 and v2 — verify D.12). If only `opencode.jsonc` exists → do not edit
(stdlib cannot round-trip comments); return `{"where": "opencode.jsonc", "manual": snippet}` and the CLI/UI shows the snippet to paste.
`mcp_registered`: read `opencode.json` or comment-stripped `opencode.jsonc`, check `mcp.research`. `mcp_verify` unchanged (provider-neutral).

### D.9 Instructions, skills, agent files

- `instruction_files`: AGENTS.md row lists opencode. Doctor warns if `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` is set in the environment (then
  `.claude/skills` are invisible to opencode; `.agents/skills` link target covers it — doctor suggests `skills link --target agents`).
- Doctor warns for every linked skill whose directory name fails `^[a-z0-9]+(-[a-z0-9]+)*$` (opencode skips it silently).
- Agent definition files (`.opencode/agents/*.md`): out of scope for this release (capability `agent_files: False`); `setup_agents` stays
  Claude-only with a clear message.

### D.10 Setup wizard, `agent setup`, doctor

- `providers.KNOWN` includes opencode, so Initialize Project, `bootstrap` YAML (`agents.enabled: [opencode]`), the Control Center Agents
  tab and `agent providers` list it with no extra code. Its default profile `opencode: {provider: opencode, command: opencode}` comes from
  the generated defaults (WP5b); no model, so the user's opencode default applies unless set.
- `research agent setup opencode [--model provider/model]`: version probe, MCP install (D.8), AGENTS.md check, skills checks (D.9), writes the
  profile model if given, prints next steps. Same function behind RPC `provider_mcp`.
- Doctor rows: installed + version + line (v1 EOL warning); MCP registered; AGENTS.md present; skills naming; `RESEARCH_AGENT` attribution
  advice (D.7c); `auth: unknown` (no credentials read, consistent with other providers).
- `isolation.SECRET_DIRS` += `.local/share/opencode` (holds `auth.json`).

### D.11 Tests with a fake binary

`tests/python/fakes/opencode` (Python script, `#!/usr/bin/env python3`, put first on PATH by a fixture). Behaviour from env:
`FAKE_OPENCODE_VERSION` (`"opencode v2.1.4"` or `"1.19.4"`), `FAKE_OPENCODE_SCENARIO` in `ok | hang_after_stop | no_final | error |
tool_calls_then_stop | slow_silent`, `FAKE_OPENCODE_LOG` (writes argv + `OPENCODE_CONFIG_CONTENT` + selected env to a file). Supports
`--version`, `run --help` (lists `--session` unless `FAKE_OPENCODE_NO_RESUME`), `models`, `run --format json ...` (emits scripted JSONL with
`sessionID: ses_test1`, tokens and cost).

Tests: argv golden (v1, v2, with/without model/variant, interactive); refusal of `--auto` in profile args; model without `/` refused;
`OPENCODE_CONFIG_CONTENT` shape per major (v2 has no `*` deny; MCP env carries `RESEARCH_AGENT`); usage parse (sums across steps, partial
flag); session id; resume argv and probe fallback; watchdog: `hang_after_stop` stopped within grace (set 1 s) with exit 0 and event;
`no_final` → warning; `slow_silent` with idle timeout 2 s → 124; MCP merge keeps an existing server; `.jsonc` returns snippet, file untouched;
`mcp_registered` true after install; end-to-end supervised plan with one task on the fake → attempt finishes and verification runs; handoff
claude→opencode uses context handoff, opencode→opencode uses native resume; doctor rows; secret dir hidden in sandbox plan.

### D.12 Still to verify against real binaries (WP13 must do this before merge; record results in PROVIDERS.md)

`-f` attaches a file in both majors; `--variant` accepted in v1.19; `mcp` key and `environment` field identical in v2; `--session` present in
v2 `run`; shell tool inherits process env; `--prompt` on the interactive command; JSONL event names in v2 (`noninteractive.ts`); exit codes.

---

## E. Feature design 2: chat gateway, Telegram and Discord

### E.1 Decisions in one place

1. **One gateway process per machine** (not per project) owns every chat connection. Fixes the multi-project offset bug at the root and is
   required for Discord anyway (one bot token = one gateway session; two would double-handle every event).
2. **Supervisors stop talking to chat.** They write alerts to an outbox; the gateway delivers. Commands from chat run in the gateway against
   the project's core through `api.call(surface="chat")`.
3. **Chat may say no to anything and yes only to what is not PIN-protected and does not bypass verification.** No PIN ever enters chat.
4. **Deny by default**: per-platform allow-list of user ids with roles (`viewer`, `operator`); pairing codes approved at the local terminal.
5. **discord.py as an optional extra, lazily imported**; Telegram stays stdlib. Gateway runs with or without the extra.

### E.2 Generic vs platform-specific

| Generic (`chat/commands.py`, `alerts.py`, `pairing.py`, `gateway.py`) | Platform-specific (adapter) |
|---|---|
| command parsing and routing, help text, project selection, role checks, op policy, replies as `Message` | transport (Bot API REST; Discord gateway via discord.py) |
| alert outbox, dedupe/cooldown/escalation, digest | rendering `Message` (Telegram text + inline keyboard; Discord embed + components) |
| button action tokens (single-use, expiring) | message size (Telegram 3,800-char chunks; Discord 2,000 content / 4,096 embed description) |
| redaction (`taskprompt.redact`), `allowed_mentions` none equivalent | threads (Discord threads; Telegram: `[T-12]` prefix, no topics) |
| pairing codes, allow-list, lockout | identity fields (`from.id` vs `interaction.user.id`) |
| status board content | slash command registration (Discord) / BotFather command list (Telegram `setMyCommands`) |

```python
class Message:            # chat/__init__.py
    text: str; title: Optional[str]; fields: List[Tuple[str, str]]; color: str  # "ok"|"warn"|"error"|"info"
    buttons: List[Button]; files: List[str]; thread_key: Optional[str]; ephemeral: bool; project: str
class Button: label: str; token: str; style: str   # token from pairing/actions store, never raw params
class Channel(Protocol):
    platform: str
    def ready(self) -> bool: ...
    async def run(self, inbox: "Inbox") -> None: ...                 # receive loop; calls inbox.command(...) / inbox.click(...)
    async def deliver(self, m: Message) -> Optional[str]: ...       # returns platform message id (for edits)
    async def edit(self, ref: str, m: Message) -> None: ...
```

The gateway runs one asyncio loop; the Telegram adapter's `run` long-polls `getUpdates` in `loop.run_in_executor` (urllib stays sync);
core calls run in a thread pool (`max_workers=4`), so the Discord 3-second interaction deadline is met by deferring first.

### E.3 Outbox and alerts (`chat/alerts.py`)

- Producers call `alerts.raise_(p, key, kind, severity, text, actions=[...], files=[...], task=None)` and `alerts.clear(p, key, text)`.
  Records append to `.research/supervisor/outbox.jsonl` (`id`, `ts`, `key`, `kind`, `severity`, `text`, `actions`, `files`, `task`, `clear`).
- Dedupe at the producer, in `.research/supervisor/alerts.json`: same `key` → suppressed until `notifications.alerts.cooldown_minutes`
  (default 30) has passed, then a reminder "still: …"; a counter per `kind+task` adds "N failures in a row: consider <suggested action>" at
  `notifications.alerts.escalate_after` (default 3; suggestions: continue with another agent / pause plan / diagnose). `clear` sends the
  "moving again" message once.
- Kinds (config `notifications.alerts.on`, default all): `halt` (existing watcher), `task_failed` (after the supervisor's failure handling),
  `approval_needed` (new proposal or protected intervention), `promotion_ready`, `plan_finished`, `run_failed` (tracked runs exit ≠ 0),
  `budget` (run budget or spend threshold reached), `supervisor_down` (raised by the gateway itself: daemon heartbeat older than
  3 × `poll_seconds` while a plan is running).
- Storm control in the gateway: more than 5 alerts for one project within 60 s are folded into one summary message.
- Delivery: the gateway tails each registered project's outbox (cursor = byte offset + inode in `gateway/cursors.json`), at-least-once;
  alert ids remembered (last 2,000) so a restart never re-sends.

### E.4 Process model

- `research gateway run` (foreground) / `start` (detached, via `daemonlib.spawn`) / `stop` / `status` / `logs`. Single instance by
  `daemonlib.lock(machine.config_dir()/gateway/lock)`; heartbeat `gateway/daemon.json` with `pid`, `version`, `platforms`, `projects`.
- Started automatically (cheap pid check, never raises) by: `research chat setup`, `supervisor.daemon_start` when any chat platform is
  enabled for that project, every supervisor tick when chat is enabled and the gateway is not alive, `self-update` (restart if stale).
  This keeps today's "Telegram just works while a plan runs".
- The gateway serves projects listed in `machine.known_projects()` whose config enables a platform (`notifications.telegram.enabled` or
  `notifications.discord.enabled`). It exits when no project enables chat (after 10 minutes idle).
- On HPC: works on a login node (outbound connections only), subject to site rules on long-running processes; docs say so. Projects on
  another machine need their own gateway with its own bot (one bot per machine; F.4).
- Sync core usage: each command opens `Project(root, author=chat_author)` in a worker thread and closes it (no long-lived handles across
  projects; `p.dirty` → `current.md` regenerated by `api.call`).
- Chat author: `{"author_type": "human", "author_name": "<platform>:<user display or id>"}` (events show who acted from where).

### E.5 Security model

| Rule | Implementation |
|---|---|
| Credentials | `telegram.json` (existing path), `discord.json`: token, `application_id`, `guild_id`, `home_channel_id`, `project_channels`; written only by `machine.write_secret` (0600 from creation); RPC returns `token_set` booleans only; the sandbox already hides `~/.config/research-os` |
| Allow-list | `pairing.json` per platform: `[{user_id, role, paired_at, label}]`; checked on every message, command and button click, by user id (never display name), before any core call; other bots ignored; unauthorized → no reply in groups/guilds, one "not authorized" ephemeral/DM reply per hour per user |
| Pairing | an unknown user who DMs the bot gets an 8-char code (alphabet without 0/O/1/I); the owner runs `research chat pair CODE [--role operator]` locally (the local machine is the trust anchor). 1 h expiry, ≤ 3 pending per platform, 1 code per user per 10 min, 5 wrong `pair` attempts → 1 h lockout; codes never logged |
| Roles | `viewer`: read commands, logs, reports, digests. `operator`: plus decisions listed in E.6. First paired user defaults to operator; later ones to viewer unless `--role operator` |
| Buttons | `custom_id`/`callback_data` = `a:<token>` (12 random chars). The token maps (in `gateway/actions.json`) to `{project, op, params, created, expires (24 h), used, alert_id}`; params are never in the button. Click: allow-list + role check on the clicker, op chat policy, single use (then the message is edited to show "done by X" and buttons removed), expired → "this button expired" |
| Redaction | all outbound text and file names through `taskprompt.redact`; logs and diffs sent as attachments, never inline > 1,500 chars; Discord `allowed_mentions` = none; Telegram `parse_mode` off (plain text) |
| PIN | never requested, accepted or shown in chat (no modal, no text). Messages that look like a PIN reply are not special-cased (the bot never asks) |
| Discord app | "Public bot" off; intents: default only (no MESSAGE_CONTENT, no GUILD_MEMBERS); guild-scoped commands in the one configured guild; DMs allowed |
| Telegram groups | a group chat works only for paired user ids inside it; migration in E.7 |

### E.6 What chat can do (op policy) and how approvals work without a PIN

| Class | Examples (ops) | Chat |
|---|---|---|
| Read | status, blocked, task, logs, options, interventions, proposals, usage, report, finding, whoami, digest | viewer+ |
| Steer (human decision, reversible) | `say`/instruct (intervention request; protected content becomes a proposal, unchanged), choose debug option, pause, resume, stop one task's current attempt (`execution_stop`), retry/continue with another agent (`continue_with`), approve a task to start (`approve_task`), run diagnosis | operator |
| Refuse (always safe direction) | reject proposal, reject/cancel intervention, deny promotion | operator |
| Local only: PIN-protected | approve proposal, approve protected intervention, create experiment, change policy, approve/merge promotion, authorize autopromote | never in chat |
| Local only: bypasses verification or rewrites history | `supervisor accept` (complete without verification), `void` attempts, plan stop with cancel agents, supervisor reload/restart | never in chat |

**Approval flow for PIN-protected items (decided).** The alert card for `approval_needed` has two buttons: **Reject** (operator, works in chat)
and **Approve on my computer**. The second does not approve. It (1) marks the proposal `requested_from_chat: {platform, user, ts}`,
(2) replies ephemerally with the exact command (`research proposal approve PROP-7`) and "or VS Code → Plans → Approvals, or `research tui` → Approvals",
(3) raises a local notification: VS Code shows an information message with "Review" (the extension already polls; it reads
`requested_from_chat`), the TUI sorts it first (WP16). The researcher enters the PIN locally.
Why: the PIN is the one thing that proves the human at the machine decided; Discord and Telegram store every message and modal on their
servers, and a hijacked chat account would otherwise equal full approval rights. The two-tier rule keeps the phone useful (most halts
are operational and are resolved with steer actions), while science, resources and code promotion still need the researcher's machine.
Timeout: approval cards older than `notifications.alerts.approval_expiry_hours` (default 24) have their buttons removed; the proposal stays
pending (timeout never auto-approves; there is no auto-deny either, because a proposal is not a blocking prompt).

### E.7 Telegram after the port (backward compatible)

- Same `telegram.json`, same `notifications.telegram.enabled`. Migration on first gateway start: a private chat id (positive id; for private
  chats Telegram's chat id equals the user id) becomes an operator entry; a group/supergroup chat id becomes alerts-only until a member pairs
  (`/pair` in the group gives a code to that user id); the bot posts once how to pair.
- `getUpdates` offset saved before handling each update (at-most-once for commands with side effects).
- Inline keyboards for buttons (`callback_data` = `a:<token>`, ≤ 64 bytes), `answerCallbackQuery`, `editMessageReplyMarkup` to remove buttons.
- `sendDocument` for files (stdlib multipart encoder, ~30 lines), cap 20 MB (Bot API upload limit is larger; keep small for phones).
- Commands registered with `setMyCommands` on start. `@botname` suffix still stripped. Plain text → `/say` (unchanged).
- `research telegram setup|status|test|remove|poll` stay as hidden aliases of `research chat … telegram`; `poll` runs one gateway receive pass
  (debugging). RPC `telegram_*` stay as aliases of `chat_*` until 4.0. The UI box becomes one ChatBox with Telegram and Discord tabs, and
  RPC `chat_setup` calls the same function as the CLI (fixes "UI Connect does not enable notifications").

### E.8 Commands (both platforms; Discord as slash commands with options, Telegram as `/cmd args`)

| Command | Role | Does |
|---|---|---|
| `/status [project]` | viewer | all plans: health label, verified progress, running count, blocked tasks, spend today |
| `/blocked`, `/task T-12`, `/logs T-12 [stderr]` | viewer | as today; logs > 1,500 chars attached as a file (Discord: ephemeral) |
| `/report [scope]` | viewer | `write_report` → attaches `report.md` plus up to 4 plots it references (size cap) |
| `/finding F-3` | viewer | embed with claim, evidence ids, review status, first evidence plot |
| `/usage [today\|week]` | viewer | usage ledger by provider/model; cost when reported; "unavailable" never shown as 0 |
| `/proposals`, `/interventions` | viewer | pending items with Reject / Approve-on-my-computer buttons |
| `/digest` | viewer | the digest now (E.10) |
| `/whoami`, `/help` | anyone paired | role, platform id, projects visible |
| `/project use NAME` | viewer | default project for this chat/DM (Discord project channels imply it) |
| `/say [T-12] text`, `/choose N`, `/debug` | operator | as today |
| `/pause`, `/resume [plan]` | operator | as today (one plan or name it) |
| `/stop T-12` | operator | stop the task's current attempt (supervisor retries/escalates by policy) |
| `/continue T-12 PROFILE` | operator | hand the task to another agent (`continue_with`) |
| `/approve-start T-12` | operator | `approve_task` (start gate), not a proposal approval |
| `/sethome` | operator | this channel/chat receives digests and machine-level alerts |

### E.9 Discord specifics (`chat/discord_bot.py`)

- Library: `discord.py>=2.4,<3` in extra `discord`, imported inside `DiscordChannel.__init__`; missing → `chat status` and `chat setup discord`
  print the install line for the detected installer (`uv tool install --force 'research-panel[discord]'`, `pipx inject research-panel discord.py`,
  `pip install 'research-panel[discord]'`). Marker `python_version >= "3.10"` (current aiohttp needs it; see F.3); Telegram keeps 3.8.
- Setup (`research chat setup discord`): token via `getpass` (never echoed or stored elsewhere); validate with REST `GET /users/@me` (urllib);
  print the OAuth2 invite URL (scopes `bot applications.commands`; permissions: View Channel, Send Messages, Send Messages in Threads,
  Create Public Threads, Embed Links, Attach Files, Read Message History, Manage Threads); wait for the bot to be in exactly one guild
  (`GET /users/@me/guilds`; more than one → ask which); tell the user to DM the bot; pairing code approved in the same terminal prompt; choose or
  create the home channel; `chat link` registers the current project and creates `#<project-name>` (or uses an existing channel id).
- Slash commands: guild-scoped (instant), registered at gateway start from E.8 (with `project` option + autocomplete over linked projects).
  Every interaction: `await interaction.response.defer(ephemeral=...)` first, core call in the thread pool, then `followup.send`.
- Threads: one thread per supervised task in the project channel (`thread_per_task`, default on), created lazily on the task's first alert
  (`T-12 · <title>`, auto-archive 1 week), id stored in `discord.json` `threads[project][task]`. Alerts with `task` go to the thread; the
  project channel gets a one-line pointer for `error`/`approval_needed`. A message in a task thread that @mentions the bot (or a DM that starts
  with `T-12`) becomes `/say T-12 …` (no MESSAGE_CONTENT intent needed for mentions and DMs).
- Status board: one pinned message per project channel, edited at most every 15 s and only when content changed.
- Embeds: color by severity, title, fields (task, phase, attempts, provider/model, tokens/cost), footer with project and time; buttons per alert
  kind: halt → Diagnose, Show logs, Pause plan, Continue with… (select menu of profiles); task_failed → Show logs, Retry with…, Stop;
  approval_needed → Reject, Approve on my computer; promotion_ready → Show diff (attachment), Deny, Approve on my computer.
- "Instruct" button opens a modal with one text input → `/say` (instructions are not secrets).
- Rate limits: discord.py handles 429/buckets; our per-channel send queue ≤ 1 message/s; storm folding (E.3).
- Attachment cap: `min(interaction.attachment_size_limit or 8 MiB, 8 MiB)`; larger files → "too large; at <path> on <host>".

### E.10 Digest and watchdog (included now)

- `notifications.digest.at: "08:00"` (local time, default null = off) and `notifications.digest.silent_if_unchanged: true`. The gateway builds it
  from events since the last digest per project: tasks verified/failed, findings created/reviewed, runs finished/failed, proposals pending,
  spend by provider. Template only, no LLM call, no message when nothing changed. Delivered to the home channel.
- `supervisor_down` watchdog (E.3) and "gateway restarted on new version X" notice to the home channel.

### E.11 Hermes steal-list: now vs later

| # | Idea | Decision | Effort | Why |
|---|---|---|---|---|
| 1 | Pairing + deny-by-default allow-list | now (WP14) | S | prerequisite for safe Discord; fixes Telegram groups |
| 2 | Alert dedupe, cooldown, escalation | now (WP14) | S | unattended runs spam identical alerts |
| 3 | Shared chat adapter | now (WP14) | M | one router, two platforms, one test suite |
| 4 | Discord bot with slash commands, buttons, threads | now (WP15) | M/L | the owner's main ask |
| 5 | Timeout-is-deny, deny beats auto, blocklist | partly: approval cards expire (no auto-approve ever); "approve always" and LLM auto-approval rejected | S | governance stays PIN-based |
| 6 | Scheduled digest, silent unless changed | now (WP14), digest only; general cron later | S | the morning question "what happened overnight" |
| 7 | `/usage`, `/stop`, status bar | now (WP14, WP16) | S | spend visibility and a kill switch from the phone |
| 8 | Busy-input queue (apply now vs next attempt, editable queue) | later | M | interventions already apply at the next attempt; revisit after use |
| 9 | Result files as attachments | now (reports, plots, logs, diffs) | S | readable on a phone |
| 10 | Thread per run/task; per-channel overrides | threads now; overrides later | M | conversation = task log; overrides add config surface for little gain |
| 11 | Env scrubbing for spawned agents | rejected as stated (agents need their API keys); instead: chat tokens never in env, sandbox hides machine dir (exists), redaction on all chat output | S | |
| 12 | opencode adapter | now (WP13) | M | |
| - | Voice, inbound files to agents, 20 platforms, memory files, autonomous skills, cron created by agents | no | | injection surface or out of scope |

### E.12 Config keys (all opt-in; defaults keep today's behaviour)

| Key (project config) | Default | Meaning |
|---|---|---|
| `notifications.telegram.enabled` | false | existing |
| `notifications.discord.enabled` | false | new |
| `notifications.discord.thread_per_task` | true | |
| `notifications.alerts.on` | all kinds | list from E.3 |
| `notifications.alerts.cooldown_minutes` | 30 | |
| `notifications.alerts.escalate_after` | 3 | |
| `notifications.alerts.approval_expiry_hours` | 24 | buttons removed after |
| `notifications.digest.at` | null | `"HH:MM"` local |
| `notifications.digest.silent_if_unchanged` | true | |
| `notifications.halt_grace_seconds`, `notifications.auto_debug` | 120, false | existing |

Machine files (0600): `telegram.json`, `discord.json`, `gateway/{lock,daemon.json,cursors.json,actions.json,pairing.json,gateway.log}`,
`projects.json` (known projects, shared with self-update).

### E.13 Tests with fakes

- `FakeChannel` (in-memory adapter implementing `Channel`): drives the router, buttons and alerts without network. Most chat tests use it.
- Telegram: existing `FakeTelegram` HTTP server (`RESEARCH_TELEGRAM_API`), extended with `callback_query`, `sendDocument`, `setMyCommands`.
- Discord: `fakes/fake_discord.py` replaces the `discord` module for the adapter's surface (Client, app_commands tree, Interaction with
  `response.defer`/`followup.send`, `Thread`, `Embed`, `ui.View/Button`, `File`) via `sys.modules` injection, so tests run without the extra.
  One optional smoke test runs only when `discord` is importable and `RESEARCH_DISCORD_TEST_TOKEN` is set (never in CI).
- Cases: allow-list (unknown user ignored; viewer cannot steer; bot authors ignored); pairing (expiry, 3 pending, lockout, code single use);
  every E.6 refusal row returns the "use your computer" message and leaves state unchanged; Approve-on-my-computer sets `requested_from_chat`
  and does not approve; button single use and expiry; two projects + one bot: a command in project A's channel never touches B; outbox
  at-least-once across gateway restart without duplicates; Telegram offset not replayed after a send failure; dedupe/cooldown/escalation
  timings with a fake clock; storm folding; digest silent when nothing changed; supervisor_down raised from a stale heartbeat; redaction of
  a planted token in logs; attachments over the cap; `discord` missing → setup prints the install line; gateway single instance.

---

## F. Decisions that need the owner (each has a default; work proceeds on the default)

| # | Decision | Recommended default |
|---|---|---|
| F.1 | No PIN set + non-interactive approval (VS Code, TUI-less RPC): fail closed, with `approval.require_pin: false` as the explicit opt-out | **Fail closed** (WP2). Users set a PIN once in Settings; anyone who wants no PIN says so in machine config |
| F.2 | Chat never approves PIN-protected actions (no TOTP or code-in-chat) | **Never in chat**; "Approve on my computer" button (E.6) |
| F.3 | Python floor: core + CLI stay `>=3.8`; Discord extra requires 3.10; TUI follows Textual's own floor | **Yes** (HPC login nodes keep working; chat runs on the laptop) |
| F.4 | One gateway (one bot) per machine; projects on other machines need their own bot | **Yes**; multi-machine relay is out of scope |
| F.5 | Dev tooling on `main`: preview harness scripts and `make_preview_fixture.py` | **Move to dev-only** (remove from `main` at the next merge), and generated-docs code lives in `tests/` so it ships with the test suite |
| F.6 | Release slicing | **3.1.0** = waves 0-4 minus WP13 (cleanup, fixes, regroup with aliases); **3.2.0** = opencode; **3.3.0** = chat gateway + Discord + TUI UX. Each bumps versions per AGENTS.md |

---

## G. UX test plan for the finished product

### G.1 TUI

- **Pilot (automated, in the test suite as `ux` marker, not default):** `async with ResearchTUI(root).run_test(size=(120, 40)) as pilot:`
  press keys, `await pilot.pause()`, assert widget content, and `pilot.app.save_screenshot(path)` (SVG) for each scenario at 80×24, 120×40
  and 200×50, dark and light (`action_theme`), and with `NO_COLOR=1` (`plain()`). Seed projects: an empty initialized project, the
  `benchproj` generator (300 experiments, 900 runs), and a supervised plan with one halted task, one pending proposal with
  `requested_from_chat`, one failed attempt.
- **pty (real binary):** `pty.fork()` + `os.execvp("research", ["research", "tui", "--root", seed])`, write keystrokes, read the screen
  stream, assert start-up < 2 s, clean exit on `q` and Ctrl-C (terminal restored: `stty -a` before/after equal), and the message when
  `textual` is missing (run with `PYTHONPATH` that hides it).
- Review: an agent opens the SVGs and checks truncation, overlapping text, unreadable colors, empty states, focus visibility.

### G.2 VS Code extension (real VS Code)

- Launch: playwright-core `_electron.launch({ executablePath: <.vscode-test/vscode-darwin-arm64-*/…/MacOS/Code>, args:
  ['--extensionDevelopmentPath=packages/vscode-extension', '--user-data-dir=/tmp/rud-x', '--disable-workspace-trust', seedFolder],
  env: <process.env without ELECTRON_RUN_AS_NODE and every ELECTRON_*/VSCODE_* var> })`. playwright-core from the npx cache
  (`~/.npm/_npx/9833c18b2d85bc59/node_modules`) or `npx -y playwright-core@<pinned>`.
- 3.0 specifics: set `research.cliPath` (user settings JSON in `/tmp/rud-x/User/settings.json`) to a dev venv's `research`
  (`uv venv /tmp/rv && uv pip install -e '.[tui]'`); the old `bundle-python` step no longer exists. Rebuild with `npm run build` after TS
  edits. A second run with an older/newer fake CLI (`research version --json` stub) checks the mismatch status bar.
- Traps (from the owner's notes): short `--user-data-dir` (macOS socket path ~103 chars); close
  `.notifications-toasts .codicon-notifications-clear` before clicking; the Control Center is a nested webview frame: poll `w.frames()` for
  visible text; wait for "Project created" (up to 2 min) before `app.close()`.
- Capture each scenario in dark, light and high-contrast themes and at ~900 px width; save PNGs plus the extension output channel log.

### G.3 Scenario checklist (each: TUI, extension, CLI where it applies; chat via fakes plus one real-bot smoke by the owner)

1. Fresh machine: CLI missing → extension shows "CLI not found" with install action; after install, activates.
2. Version mismatch (core 3.0 vs extension needing 3.1) → status bar + message; feature hidden when its op is absent.
3. Initialize Project (no classic wizard): every provider listed from RPC, including opencode; PIN set step.
4. Create question → experiment (human) → run → metric → finding → review; tree refreshes after each action without manual refresh (`changed`).
5. Agent (fake claude/opencode via dispatch) proposes an experiment; Approvals shows it; approve with PIN; no PIN set → clear refusal + set-PIN path.
6. Agent writes an experiment mirror file by hand → becomes a proposal, audit event visible.
7. Supervised plan: run, halt, diagnose, choose option, resume; supervisor restarted after a simulated self-update.
8. Control Center Agents: strategy switch and task pins (after `render/setup.ts` removal); `research.agentSetup` lands there.
9. Settings: change a typed setting, policy key needs PIN.
10. Guide page: build preview with and without Quarto.
11. Large project (benchproj): first paint < 2 s, refresh < 0.5 s, no UI freeze while a write runs.
12. Remote-like: project on a path with spaces and unicode; read-only `.research` (permission error surfaced, not swallowed).
13. TUI: same as 4, 5, 7 via keys; status bar shows running agents, spend, unattended warning, gateway state.
14. Chat (FakeChannel + real Telegram/Discord smoke by the owner): pair, `/status`, halt alert with buttons, `/say`, Reject proposal,
    Approve-on-my-computer → VS Code notification and TUI ordering, `/report` attachment, two linked projects, gateway restart without
    duplicate alerts, digest at a fake time.
15. Hidden aliases: `research resume`, `research telegram status`, `research dispatch --help` still work; `--help` lists only new names.

Bugs found are filed as one line each in a dev-only `docs/PHASE_CLEANUP_3_UX_FINDINGS.md` and fixed in WP17 follow-ups.

---

## Summary

The target keeps the stdlib core and the three interfaces but gives every operation one definition: `research/api/` registers each operation
once; `rpc.py` and `mcp.py` become thin transports; the TUI and chat call the registry in-process; the CLI keeps direct core calls with its
own printers, split from the 4,070-line `main.py` into nine command modules with old names as hidden aliases. "What changed" becomes
`p.dirty` from `Project.tx()`, deleting four mutation lists. Registry owns all defaults; providers become a package with one table.

Seventeen work packages in eight waves: green CI and the Python 3.8 crashes first, then the two governance holes (no-PIN approval,
mirror-file experiments), dead code and the duplicate wizard, docs facts and test markers; then dedupe, launcher/self-update, the op layer and
CLI split; then extension thinning, test merges, SPEC split with generated reference tables; features last.

opencode: full adapter with version probe (v1/v2 config shapes), inline config through `OPENCODE_CONFIG_CONTENT`, JSONL usage/session
parsing, native resume, and a generic watchdog for its hang-after-final-event bug; governance relies on core attribution and the PIN, not hooks.

Chat: one machine-level gateway replaces per-supervisor Telegram polling; supervisors write an outbox; Telegram and Discord (discord.py extra)
share one router with pairing, roles, single-use buttons, threads per task, attachments, dedupe/escalation, digest and a supervisor-down
watchdog. Chat may refuse anything and steer operations, but PIN-protected approvals stay on the researcher's machine.

Owner decisions (defaults in F): fail closed without a PIN, no approvals in chat, Python floors, one bot per machine, dev tooling off `main`,
release slicing 3.1/3.2/3.3.

---

## H. Owner workflow requirements (added by the lead after the owner restated the workflow; binding on WP5b, WP13, WP14-16)

The workflow: (1) per-project setup chooses agents and the delegation/orchestration defaults; (2) a long series of experiments is imported as a YAML plan and run autonomously; (3) results (metrics, plots) come back, the owner interprets them and proposes the next series (ablations or new ideas); (4) shared code that many experiments depend on lands on the main branch; (5) state is reachable remotely from Discord/Telegram; (6) progress is presented with Quarto; (7) an on-demand "intelligence" agent handles situations the deterministic supervisor was not designed for.

Gaps found by checking the code (not in sections A-G):
- **H.1 Strategies are Claude-only.** `strategy._LEVELS = {fast: claude-fast, balanced: claude-balanced, deep: claude-deep}` and the four built-ins (`sonnet_first`, `opus_first`, `haiku_max`, `sonnet_heavy`) are named after Claude models. Requirement: strategies are provider-neutral (`balanced_first`, `deep_first`, `fast_max`, `balanced_heavy`; old names stay as aliases). Each provider ships a **level pack** (`fast/balanced/deep` -> concrete model + effort) in its adapter record: claude, codex, agy, opencode, and `custom`. Setup asks "which provider is your default" and writes `agents.profiles.<provider>-fast|balanced|deep` plus `strategy.levels` for that provider; mixed-provider levels are allowed (e.g. deep = claude, fast = opencode with a cheap model). `research strategy show` / TUI / extension show the resolved model per level and fail loudly if a provider is not installed. Owner of work: WP5a/5b (data model) and WP13 (opencode pack, parity test: every provider has all three levels and passes `strategy.validate` for all built-ins).
- **H.2 Setup is the first-run path.** `research init` (CLI and extension wizard) must end with a working orchestration: default provider, level pack, strategy, roles (planner/implementer/verifier/analyst) all resolved and `doctor` green, with `research setup agents` re-runnable. A parity test runs init for each provider with a fake binary and asserts the same shape. Owner: WP5a + WP8.
- **H.3 Long autonomous plans.** Already present (YAML plan import, supervisor, promotion, budgets). Keep; WP17's UX campaign must include a scenario that imports a 20-task YAML plan with an ablation branch and watches it from the TUI and chat.
- **H.4 Shared core code on main.** Present as promotion/auto-promotion. The UX campaign verifies the path end to end (shared module task -> verified -> promoted to the main branch -> later experiment worktrees start from it); report gaps instead of assuming.
- **H.5 On-demand intelligence agent.** Present partially: a debug/diagnosis agent (`notify.py` /debug, `intervene.py`, escalation after incomplete deliverable). Gaps: (a) it is reachable mainly from Telegram; expose one op `supervisor_investigate(task|plan, question?)` through CLI, TUI, extension and both chat bots; (b) an opt-in trigger: when a task hits an unclassified failure or exhausts deterministic retries, spawn the investigator automatically once, under a budget (`supervisor.investigator.auto: false` default, `max_per_plan`, `budget_usd`), with a read-mostly toolset and a structured outcome (diagnosis, options, proposed actions); anything protected (science, resources, frozen tests, roles) still needs the PIN approval; it cannot approve itself. Everything it does is recorded as events and visible in the TUI/chat. Owner: WP7 (op), WP14 (chat), WP16 (TUI).
- **H.6 Quarto presentation.** Present (Scientist Guide). WP17 UX campaign must render a guide from a real fixture project, and the chat gateway must be able to send the rendered HTML/PDF as an attachment (E.6 attachments).

- **H.7 Decisions with options, everywhere (owner requirement, binding on WP7, WP14, WP15, WP16, WP9).** Whenever the system needs a human decision (a halted task, a diagnosis with alternatives, an ambiguous result, a proposal to approve, "what next after this experiment series", a budget or resource question), it creates a first-class **decision request**: `{id, project, subject (task/plan/proposal/run), question, options: [{key, label, consequence, action (an op + params), protected: bool}], default (optional), expires, created_by (supervisor or investigator agent)}`, stored in the project (not in chat). Agents and the investigator may CREATE a decision request (op `decision_request`, agent-allowed: it only asks) but never answer one. Surfaces:
  - Chat (opt-in, Telegram and Discord): one message per request with a button per option (Telegram inline keyboard, Discord components/select menu when more than 5 options), plus an "Other: reply with text" hint. A free-text reply to that message (or `/say` in the thread) is passed to the supervisor as the instruction (existing intervention path) and recorded as the answer; the supervisor says back what it understood and will do (one line) before acting, and for reversible actions acts immediately. Buttons are single-use and expire; answering in one place (chat, TUI, extension, CLI `research decide <id> <key|text>`) closes it everywhere and edits the chat message to show who answered what.
  - Options whose `protected` flag is true (science, resources/budget, frozen tests, roles, promotion) never execute from chat: the button reads "Approve on my computer" and flags the request for the TUI/extension PIN flow (decision F.2). Unprotected options execute immediately.
  - TUI: a Decisions tab (or the Approvals tab extended) listing open requests with the options as keys `1..9`, `t` for free text; the extension: a Decisions list in the Control Center with option buttons and a text box. A badge on the status bar/header counts open requests.
  - The supervisor sends the request through the opt-in gateway (`notifications.chat.decisions: true`) with dedupe and escalation (re-ping after N hours, "supervisor waiting for you" in the digest), and a configurable `on_timeout` (`wait` default, never auto-choose a protected option).
  - Replaces and generalizes today's `/options`, `/choose N` (kept as aliases) and the diagnosis options in `intervene.py`; the investigator (H.5) produces its options through the same request.
  Tests: FakeChannel drives request -> buttons -> answer; free-text answer path; protected option never executes from chat; double answer from two surfaces is idempotent; expiry; agent can create but not answer.

- **H.8 Two ways to answer a decision, one pipeline (owner refinement of H.7).**
  1. **Pick an option:** the option already carries a *prepared action*, designed when the request was created (by the supervisor for known situations, or by the investigator agent for novel ones): an op + params, or a concrete command/task spec (e.g. "re-run with lr 1e-4", "add ablation EXP-12 without the aux loss", "skip T-7 and continue"). Choosing it runs that action deterministically; no model call at answer time. The investigator must validate the prepared action when creating it (parse it, dry-run it where an op supports `dry_run`, classify it protected/unprotected via the existing `intervene` classifier), and the option shows the exact action text/consequence so you know what a tap does.
  2. **Reply in text** (chat message/thread reply, TUI text box, extension text box, `research decide <id> "text"`): the answer goes to the investigator ("intelligence on demand"), which turns it into a structured **action plan**: new/edited tasks, plan changes as a partial YAML patch, commands to run, or an instruction to a running task. The plan is classified with the same classifier. Unprotected + reversible actions run immediately, and the supervisor replies with a one-line summary of what it understood and created (task ids). Protected parts become ONE proposal needing the PIN approval (F.2: not in chat), and the reply says exactly which part is waiting. If the text is ambiguous the investigator asks ONE clarifying decision request with options instead of guessing. The text, the generated plan, the classification and the outcome are recorded as events and linked to the decision.
  3. The investigator's tools are bounded (read project state, read logs/results, propose; never approve, never edit a scientific plan, never touch frozen tests); budget capped (`supervisor.investigator.*`, H.5); everything auditable. Tests: scripted fake investigator (a fake provider binary returning a canned action plan) covers option-with-prepared-action, free-text -> tasks, ambiguous -> clarifying request, protected part -> proposal, budget exhausted -> falls back to asking the human with a plain text note.

- **H.9 Approving PIN-protected actions from chat (owner decision; supersedes F.2 and the 'Approve on my computer'-only rule in E.6).** The PIN itself never goes through chat. A second, separate, enrolled credential does: an **authenticator (TOTP, RFC 6238, stdlib `hmac`/`hashlib`/`base64`/`time`, 6 digits, 30 s step, SHA-1 for authenticator-app compatibility)**.
  - **Enrollment (computer only, interactive, PIN required):** `research approval chat-enroll` (and a button in VS Code/TUI settings) shows the secret as a QR/`otpauth://` URI in the terminal once, asks for one valid code to confirm, and stores the secret beside the PIN hash with the same protections (machine config dir, mode 0600, never in the project, never logged, never sent through any channel, never readable by agent environments: same agent-detection refusal as the PIN). Re-enroll/revoke: `research approval chat-revoke` (PIN required). Chat approval is **off until enrolled** and until `notifications.chat.approvals: true`.
  - **Flow:** the approval card has Approve and Reject. Approve (button, `approver` role in the chat allow-list, a role above `operator`) -> the bot asks for the current 6-digit code: on Discord via a **modal** (the code is never shown in the channel), on Telegram as a reply, after which the bot deletes the user's message (`deleteMessage`) and edits the card to 'approved by <name> via Discord at <time>'. The code is verified against the machine secret for the step window [-1, 0, +1]; each accepted time-step is recorded and **cannot be reused** (replay protection); wrong codes use the existing PIN failure counter and 60 s lockout (shared across PIN and chat so chat cannot be used to brute-force faster); 5 failures disable chat approval until a local PIN login. The approval is bound to that one proposal id and the exact content hash shown on the card (a changed proposal invalidates the card). The ledger records the approval as method `totp-chat` with platform, user id and role, so a human can tell chat approvals apart from PIN approvals in reports and the TUI.
  - **Scope (owner chose 'everything except the highest-risk'):** chat may approve: new experiment proposals, retry/compute/budget increases, role/profile changes, promotion to the main branch, resumption after a halt, and decision-request options flagged protected (H.7). **Computer-only forever** (the card says 'needs your computer', no Approve button): edits to frozen tests, changes to a scientific plan or hypothesis text, disabling or relaxing governance/approval settings, changing or revoking the PIN/TOTP, and adding chat users to the allow-list. The list lives in code as a constant (`CHAT_FORBIDDEN`), not config, so a config edit by an agent cannot widen it; config can only narrow what chat may approve (`approval.chat_allow` list).
  - **Agents:** cannot call the chat-approval path or read the secret; the code check runs only inside the gateway process on messages from allow-listed human user ids.
  - **Tests (FakeChannel + a fake clock):** valid code approves exactly once; the same code twice is refused (replay); code from the previous/next step accepted, two steps away refused; wrong-code lockout shared with PIN; unenrolled -> no Approve button; forbidden action -> no Approve button; role below `approver` refused; changed proposal content invalidates the card; the code and secret never appear in logs/events/outbox (grep test); agent environment cannot enroll or read; Telegram message deleted after use.
  - **Docs:** `docs/GOVERNANCE.md` and `docs/CHAT.md` gain a plain statement of the threat model: chat approval is as strong as 'your chat account AND your authenticator device'; if you lose the phone, revoke at the computer.
  - **Work:** WP14 owns the chat side and the new `approval.py` additions (`totp.py` new module + enrollment CLI) since WP2 is finished; WP15 adds the Discord modal; WP16 shows 'approved via chat' in the TUI; WP2's tests stay green (the PIN path is unchanged).

- **H.10 Usage-limit wait and auto-resume (owner feature request).** Today (`supervisor._provider_error`, ~L793-830): a provider refusal read from the agent's structured output (Claude `is_error`/`terminal_reason: api_error` + regex `hit your .*limit|rate limit|usage limit|overloaded|429|529|credit balance|quota`) voids the attempt (`category: resource_limit`), refunds the attempt budget, tries `_fallback` to another provider if configured, otherwise stops the task at `attention` until a human resumes. Missing: reading WHEN the limit resets and continuing by itself, for all providers.
  - **Setting (opt-in, machine or project config):** `supervisor.usage_limit.on_limit: halt | wait | fallback_then_wait` (default `halt` = today's behaviour; opting in is the user's decision), `wait_buffer_seconds: 120` (added after the reset time), `max_wait_hours: 8` (a longer wait, e.g. a weekly limit, turns into a decision request per H.7 with options wait / switch provider / stop, never an unattended multi-day wait unless the user raises the cap), `unknown_reset_backoff_minutes: [15, 30, 60]` (when the reset time cannot be parsed: relaunch the voided attempt at these growing intervals, last value repeating), `probe: relaunch` (no extra paid calls: the relaunched attempt IS the probe, and a refusal costs nothing and refunds the budget).
  - **Detection per provider adapter (D.1 record gains `limit_info(out, err, exit_code) -> LimitInfo|None`):** `LimitInfo{kind: usage|rate|overload|credit, reset_at: UTC datetime|None, raw: str}`; reset times parsed with their timezone and stored in UTC; both absolute ('resets 3pm (America/New_York)', ISO/epoch headers) and relative ('try again in 2h 14m', `retry-after` seconds) forms; ambiguous clock times resolve to the next occurrence. claude: structured result messages (the existing path) plus stream-json rate-limit events if present; codex: stderr/JSON error text ('usage limit', 'try again in ...'); opencode: JSON `error` events/`step_finish` error info and provider error bodies (429 + `retry-after`); agy: whatever it prints (treat as UNVERIFIED -> unknown-reset backoff). `overload`/`rate` kinds with no reset time use a short backoff (1, 2, 5 min) rather than the long one. The exact message formats MUST be taken from real outputs/docs (docs/PHASE_CLEANUP_R_LIMITS.md) and stored as fixtures for parser tests; a provider whose format is unverified ships with `reset_at=None` behaviour and is marked as such in `docs/PROVIDERS.md`.
  - **Supervisor behaviour:** a limit marks the provider PROFILE (account) as `limited_until` in project state (`.research/` state, survives daemon restarts); tasks that route to that provider enter phase `waiting_provider` with `resume_at` and are not launched; tasks routed to other providers keep running; with `fallback_then_wait` the existing `_fallback` runs first (only if it does not weaken the strategy/role rules; never swaps in a model not allowed by `orchestration.available`). The daemon schedules its next wakeup at the earliest `resume_at + buffer`; at that time it relaunches the voided attempt with its session resumed where the adapter supports it. Budgets, attempt counts, approvals and governance are untouched (no escalation, nothing protected is approved by waiting). Plan health shows `waiting_for_provider` with the time. Cancel/pause by the human always wins. 
  - **Visibility:** event `supervisor_waiting_provider` / `supervisor_resumed_after_limit`; one chat message at start ('Claude limit reached; resuming about 15:05 (in 1h 42m); other tasks continue') and one at resume (deduped, H.7 decision request if beyond `max_wait_hours`); TUI/extension status chip 'waiting for Claude until 15:05'.
  - **Tests:** parser tests with real fixtures per provider (relative/absolute/timezone/DST edge, missing reset), a fake provider binary that fails with a limit and then succeeds after a fake-clock advance, daemon restart during the wait (state persists, resumes on time), provider A limited while B tasks continue, `max_wait_hours` exceeded -> decision request, `halt` default unchanged.
  - **Work:** parsers + `limit_info` in each adapter belong with the provider work (WP13 for opencode; claude/codex/agy adapter edits in a small WP13a first); supervisor scheduling and the setting are a new **WP18** after WP14 (so the chat/decision pieces exist), owning `supervisor.py` limit region, `registry.py` keys, `docs/SUPERVISOR.md`, tests; wave 6 alongside WP15/WP16.
