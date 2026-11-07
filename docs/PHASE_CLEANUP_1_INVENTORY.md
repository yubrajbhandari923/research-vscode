# Phase cleanup 1: feature inventory and code audit (development branch only)

Dev-only file (matches `docs/PHASE_*` in AGENTS.md). Never merge to `main`.
Audited: `development` at c123773 (3.0.0), 2026-11-07. Read-only; nothing in the code was changed.

**What this repo does:** Research OS ("Research Panel") is a project-local research memory. A Python core keeps questions,
experiments, runs, findings, decisions, plans and tasks as Markdown/JSONL text with a SQLite index. A `research` CLI, a JSON-RPC
server (for a thin VS Code extension), an MCP server (for agent CLIs), a Textual TUI and a persistent "supervisor" daemon (runs YAML
plans by launching Claude/Codex/Antigravity CLIs, verifying, repairing, and optionally talking to Telegram) all sit on that core.

**Load assumed:** one researcher per machine, a handful of projects, 1-5 agent processes per project running concurrently, some
projects on HPC login nodes (NFS, old system Pythons). Hundreds to a few thousand records per project. Not multi-tenant.

**How measured:** `wc -l`, AST scripts (caller grep for every top-level def and every RPC method), `uvx vulture` (60% and 80%),
`uvx radon cc`, `uvx vermin`, `npx ts-prune`, `python -X importtime`, `/usr/bin/time`, a synthetic 300-experiment / 900-run /
300-finding project (`benchproj`) to time sync and RPC refresh, the full pytest run with `--durations=40`, and the logs of the last
GitHub Actions run (`gh api .../jobs/128048525874/logs`).

---

## 1. Feature inventory

Legend for "SPEC vs code": **ok** = agree; **drift** = SPEC/README says something the code no longer does (or the reverse).

### 1.1 Project, store, records

| Feature | Where | SPEC vs code |
|---|---|---|
| Questions, experiments (variants, baseline), runs, metrics, artifacts, syntheses, findings, decisions, checkpoints, notes | `services.py` 612, `runs.py` 791, `context.py` 613, `views.py` 407, `schema.py` 374 | ok |
| Text-canonical storage, mirrors, hand-edit import, rebuild, events.jsonl | `store.py` 979 (`sync` L859, `import_file` L790, `rebuild_index` L902), `mirrors.py`, `durability.py` | ok, but hand-edit import bypasses governance for experiments too (finding 3) |
| Crash journal + write lock (`Project.tx`) | `store.py:498`, `durability.py` | ok |
| Layered config (defaults <- machine <- project), comment-preserving edits, typed registry | `configfile.py` 577, `registry.py` 671, defaults in `store.py:31-151` plus per-module `DEFAULTS` | ok; three sources of defaults (finding 15) |
| Shared ledger + experiment worktrees | `workspace.py` 616, `initproject.py` 391 | ok |
| Run budget, synthesis-before-new-experiment gate | `services.check_new_experiment_allowed`, `runs.create_run` | **drift**: agents can no longer reach the synthesis gate (governance blocks first, `services.py:169`); SPEC §7.2 still describes it as the agent gate |
| Search, compare, report, full report, current.md | `search.py`, `compare.py`, `report.py` 409, `fullreport.py` 1044, `context.py` | ok |
| Evidence bindings, artifact integrity, sources, tuning | `evidence.py` 488, `provenance.py` 713, CLI `evidence/source/tuning` | ok |

### 1.2 Plans, supervisor, governance

| Feature | Where | SPEC vs code |
|---|---|---|
| Plans/tasks, readiness, claims, notes, stale tasks, task context | `plans.py` 567, `task_spec.py` | ok |
| Verification gates (task checks, independent finding review) | `verification.py` 313, `processes.py` (shell=True by design) | ok |
| YAML plans (validate/diff/apply/export) | `planspec.py` 732 | **broken on Python 3.8** (`planspec.py:249` uses `dict | dict`) |
| Persistent supervisor daemon, phases, repair, escalation, health/HALTED, reload/restart | `supervisor.py` 1739, `supervisord.py` | broken on 3.8 (`supervisor.py:409`) |
| Governance: agents propose, humans approve | `governance.py` 98, `supervisor.propose_experiment/decide_proposal` | ok in code paths; bypassed by mirror files (finding 3) |
| Approval PIN (scrypt, lockout, tty prompt) | `approval.py` 202 | **drift**: SPEC §15.6a and GOVERNANCE.md say "fails closed with no terminal"; with no PIN set a non-interactive human-looking process is approved silently (finding 2) |
| Intervention (`supervisor instruct`) | `intervene.py` 480 | ok |
| Unattended promotion to local `main` | `autopromote.py` 437, `promotion.py` 441 | ok (3.8-broken via supervisor) |
| Provider handoff / continue | `supervisor.continue_with`, `agents.redispatch`, `execution.py` | ok |

### 1.3 Agents and providers

| Feature | Where | SPEC vs code |
|---|---|---|
| Roles/profiles/dispatch briefs, headless runs, usage ledger | `agents.py` 454, `usage.py` 94 | ok; usage parser exists only for `claude_json` |
| Provider adapters claude/codex/agy/custom, capabilities, resume | `providers.py` 332 | ok; `register()` (L246) has no caller in the tree; providers listed again in ~12 other places (section 6a) |
| Allocation strategies, orchestration, pins, contracts, frozen tests | `strategy.py` 353, `orchestrate.py` 672 | ok |
| Harness hooks (approved shell commands at stages) + check catalog | `hooks.py` 294, `checks.py` 319, `harness.py` | ok |
| Skills import/link/packs/catalog (58 skills, 9 packs) | `skills.py` 532, `packs.py` 317, `templates/catalog/` | ok |
| `research setup agents` (Claude subagent files) | `setup_agents.py` 186 | Claude-only (`setup_agents.py:110` refuses other providers) |
| MCP server (44 tools) + install | `mcp.py` 471 | **drift**: SPEC §7.8 tool list omits `workspace_list`, `guide_status`, `guide_validate`, `guide_draft_done`, `report`; SPEC §22 says "no MCP tools for the guide" while 5 exist |
| AGENTS.md managed block, CLAUDE.md `@AGENTS.md` | `agentsmd.py` 180, `providers.sync_instructions` | ok |
| Gemini profile | `store.py:82`, `setup.py:16` | **drift**: README/PROVIDERS name Claude, Codex, Antigravity; the "classic" setup wizard offers gemini but not agy |

### 1.4 Runs, execution, HPC

| Feature | Where | SPEC vs code |
|---|---|---|
| Local foreground/detached runs, runner, attach, sweeps | `runs.py`, `runner.py`, `backends/local.py` | ok |
| SLURM backend (sbatch/squeue/sacct/scancel) | `backends/slurm.py` 136 | ok (mocked in tests) |
| HPC over SSH: doctor/init/submit/sync/collect/bundle/datasets | `hpc/` 2858 lines | ok; remote shell built with `shlex.quote` throughout (checked `transport.py`, `remote.py`) |
| Job isolation (sandbox profiles, unshare/apptainer plans) | `isolation.py` 504, `sandbox.py` 178 | ok; real Apptainer untested |

### 1.5 Guide, notify, interfaces

| Feature | Where | SPEC vs code |
|---|---|---|
| Scientist Guide (Quarto book, highlights, drafts, citations, provenance, review) | `guide.py` 530, `guide_quarto.py` 770, `guide_review.py` 159, `provenance.py` 713 | ok |
| Halt detection, debug agent, Telegram | `notify.py` 420, `supervisor_cmds.cmd_telegram`, RPC `telegram_*`, `PlansPage.tsx:284` | **drift**: CHANGELOG 2.6 / SUPERVISOR.md say opening the project in VS Code restarts the supervisor; 3.0 removed that call (finding 6). Multi-project and retry bugs (finding 4) |
| CLI (60 top-level commands, 79 incl. aliases) | `research_cli/main.py` 4070, `supervisor_cmds.py` 523 | **drift**: SPEC §0 says 56 |
| JSON-RPC (163 methods) | `rpc.py` 1224 | ok; 16 methods have no extension caller (section 3, finding 13) |
| TUI (6 tabs) | `research_cli/tui.py` 473, in-process `rpc.Server` | ok |
| Self-update + version check | `selfupdate.py` 101, `compat.py`, `extension.ts` | ok |
| VS Code extension: 10 views, 90 commands, Control Center (React), entity pages (HTML strings) | `packages/vscode-extension/src` 7158 lines | **drift**: SPEC §13A says the Home panel "remains"; it is unreachable (`panels.openResume` has no caller) |
| Python >= 3.8 (pyproject, SPEC §2.2) | everywhere | **broken**: 5 `dict | dict` sites; CI on 3.8 fails 86 tests (finding 1) |
| Version labels | SPEC header "2.6.0", README "Version 2.6.0" | **drift**: code is 3.0.0 (finding 5) |

---

## 2. Architecture as it is

### 2.1 Size

| Area | Lines |
|---|---|
| Core `packages/core/research` (no vendor/templates) | 28,064 in 71 modules |
| Vendored PyYAML (`_vendor/yaml`) | 5,809 |
| Templates (skills catalog, agents, prompts; 181 files) | incl. 2,305 lines of bundled Python scripts |
| CLI `packages/cli/research_cli` | 5,072 (`main.py` 4,070, `supervisor_cmds.py` 523, `tui.py` 473) |
| Extension TS/TSX | 7,158 (+ `media/ui.js` 246, CSS 888) |
| Python tests | 13,284 lines, 1,122 tests in 46 files |
| Docs | SPEC 3,018; README 410; CHANGELOG 550; 10 other guides 1,085 |

### 2.2 Module map (core, by layer)

| Layer | Modules (LOC) | Responsibility |
|---|---|---|
| Base | `util` 210, `schema` 374, `yamlio` 72, `durability` 203, `mirrors` 121, `gitinfo` 91 | helpers, entity schema + SQLite migrations, YAML, locks + journal, mirror render/parse, git |
| Store | `store` 979, `configfile` 577, `registry` 671, `workspace` 616 | Project handle, sync/rebuild, config layers + typed settings, worktrees/ledger |
| Records | `services` 612, `runs` 791, `plans` 567, `verification` 313, `evidence` 488, `views` 407, `context` 613, `search` 77, `compare` 69, `report` 409, `fullreport` 1044 | business logic and read models |
| Agents | `agents` 454, `providers` 332, `strategy` 353, `orchestrate` 672, `execution` 190, `taskprompt` 145, `usage` 94, `skills` 532, `packs` 317, `hooks` 294, `checks` 319, `harness` 53, `setup_agents` 186, `agentsmd` 180, `localbin` 244 | dispatch and agent equipment |
| Automation | `supervisor` 1739, `planspec` 732, `intervene` 480, `notify` 420, `autopromote` 437, `promotion` 441, `governance` 98, `approval` 202 | daemon, YAML plans, steering, alerts, promotion, gates |
| Guide | `guide` 530, `guide_quarto` 770, `guide_review` 159, `provenance` 713 | Scientist Guide |
| Execution | `runner` 194, `backends/*` 259, `hpc/*` 2858, `isolation` 504, `sandbox` 178, `processes` 49, `envcheck` 115 | where commands run |
| Setup | `initproject` 391, `setup` 229, `bootstrap` 221, `doctor` 309, `selfupdate` 101, `compat` 12 | first run, YAML init, doctor, update |
| Surfaces | `rpc` 1224, `mcp` 471, `control` 144 | extension RPC, MCP, Control Center aggregations |

**Layering is leaky.** 461 intra-package import edges; 280 of them are function-level (lazy) imports used to dodge cycles. `store`
(the lowest stateful layer) imports 19 modules lazily, including `supervisor`, `plans`, `orchestrate`, `verification`
(`store.py:827`, task-completion guard inside `import_file`), `runs` (`store.py:895`, inside `sync`), `context`, `packs`, `localbin`.
Mutual dependencies: `store <-> {plans, runs, supervisor, orchestrate, verification, views, workspace, context, configfile, packs, agentsmd}`,
`plans <-> {orchestrate, services, supervisor, verification}`, `supervisor <-> {autopromote, intervene, notify, plans}`.
Highest fan-in: `util` 56, `store` 42, `schema` 23, `services` 20. Highest fan-out: `rpc` 49, `agents` 21, `supervisor` 20, `mcp` 19.

### 2.3 Surfaces and how they share code

| Surface | Entry | Project handling | Who regenerates `current.md` after a write |
|---|---|---|---|
| CLI | `main.main` (`main.py:4006`), argparse tree in `build_parser` (776 lines, `main.py:3169`) | `Project(sync=True)` per command | `@mut` decorator set `MUTATING` (`main.py:627`) |
| RPC | `rpc.serve` (`rpc.py:1155`), `Server.m_*` (163 methods) | one long-lived `Project`, full `sync()` per call except ~60 listed methods | `rpc.MUTATING` set (`rpc.py:1147`; contains duplicates) |
| MCP | `mcp.serve`, `_tools()` 44 `Tool`s | reuses `rpc.Server.project()` | `Tool.mutating` flag |
| TUI | `tui.ResearchTUI`, calls `rpc.Server.m_tree/m_proposals/m_show/m_proposal_decide/m_run_cancel` in-process | via `rpc.Server` | via `rpc.Server` (no `MUTATING` handling: TUI writes skip `current.md` refresh) |
| Extension | `client.ts` spawns `research rpc --root`; `panels.ts` relays an allow-list `CONTROL_RPC` (91 methods) | n/a | `MUTATING_RPC` in `panels.ts:13` only re-syncs the tree |

What is duplicated between surfaces (verified by reading both sides):
- **Strategy switch**: validate + `save_config` + `strategy_changed` event in `main.py:1679-1695` and `rpc.py:1105-1116`.
- **Provider list rows**: `main.py:2862-2864` and `rpc.py:886-892` (same filter `name in ("claude","codex","agy") or prof.get("provider")`, both with `dict | dict`).
- **"Which calls mutate"**: four lists (CLI `@mut`, `rpc.MUTATING`, `Tool.mutating`, `panels.ts MUTATING_RPC`).
- **Telegram setup**: CLI `cmd_telegram setup` (`supervisor_cmds.py:317-333`) enables `notifications.telegram.enabled` + `auto_debug` and starts the
  supervisor; RPC `m_telegram_set` (`rpc.py:715`) only stores credentials. Same feature, two behaviours.
- **MCP registration**: `mcp.install` (clients claude/vscode/cursor/gemini/codex) vs `providers.mcp_install` (claude/agy/codex); two status readers
  (`mcp.status`, `providers.mcp_registered`).
- **Attention items**: `context.research_home` + `verification.attention_items` (Research Home, `current.md`) vs `control._attention` (Control Center)
  with different rules (e.g. Control Center lists every unreviewed preliminary finding; `verification` lists up to 3 agent findings).
- **Agent environment detection**: `util.detect_author` (CLAUDECODE, CLAUDE_CODE_ENTRYPOINT, CODEX_SANDBOX*), `approval._AGENT_ENV`
  (adds RESEARCH_AGENT, RESEARCH_EXECUTION), `client.ts:47` strip list, `tests/python/conftest.py:14`.
- **Setup wizards**: `setup.py` ("classic" Setup, providers claude/codex/gemini), `initproject.py` (Initialize Project, claude/codex/agy),
  `bootstrap.py` (YAML form of the same), `setup_agents.py` (Claude agent files). Two UIs (`Setup.tsx`, `InitProject.tsx`, toggled in `App.tsx:37-38`).
- **Two extension UI stacks**: HTML-string pages (`render/*.ts` 1,745 lines + `media/ui.js` 246 + `ui.css` 519) for sidebar, entity pages,
  compare, forms and "Agent setup"; React Control Center (`webview/*.tsx` ~2,400 + `app.css` 369). "Agent setup" (`render/setup.ts`, RPC
  `agent_setup`) overlaps the Control Center Agents tab (`Providers.tsx`, `ToolsSection.tsx`, RPC `providers`/`tools`).

### 2.4 Measured cost

| Measure | Result |
|---|---|
| `python -X importtime -c 'import research_cli.main'` | 49 ms total (`research` 43 ms; `research.context` 38 ms cumulative) |
| `research --help` wall time | 0.08-0.11 s (bare `python -c pass` 0.02 s) |
| Open + full sync, 1,500 records (benchproj) | 64-77 ms; open without sync 3 ms |
| `research status` / `current` on benchproj | 0.22 s / 0.26 s |
| RPC on benchproj: `tree` / `resume` / `home` / `index` | 212 / 111 / 135 / 2 ms; one extension refresh (tree+home+index) ~0.35 s |
| Writes | ~15 ms per created record (fsync + journal) |

Startup and sync are fine at the assumed load. The one structural cost: every sync takes the project write lock and a
`BEGIN IMMEDIATE` because `ingest_root_files` always opens `p.tx()` (`runs.py:620`), so every read command serializes behind writers.

---

## 3. Problems, ranked by value of fixing

### Must fix

1. **The product is broken on Python 3.8 and CI has been red for every recent push** (`planspec.py:249`, `supervisor.py:409`, `rpc.py:894`, `main.py:2829`, `main.py:2863`)
   - **What this is:** pyproject says `requires-python >=3.8`, SPEC §2.2 promises 3.8, CI tests 3.8 and 3.12. HPC login nodes often have 3.8.
   - **Problem:** five `dict | dict` expressions (a 3.9 feature; `vermin` cannot see it, so the SPEC's "checked with vermin" is no proof). On 3.8, plan YAML,
     the supervisor, autopromote, handoff, `provider list` and the Control Center Agents page raise `TypeError`. Last CI run on `development`
     (run 42263286188): `86 failed, 966 passed, 16 errors` on 3.8; 3.12 was cancelled by fail-fast, so nobody sees 3.12's own failures either.
     `rpc.serve` reports this TypeError to the UI as "bad parameters" (`rpc.py:1206`), which hides it.
   - **Fix:** replace each with `{**a, **b}`. Add `fail-fast: false` to the CI matrix. Narrow `except TypeError` in `rpc.serve` to errors raised by the call binding
     (check with `inspect.signature(fn).bind(**params)` before calling).
   - **If we skip it:** every 3.8 user gets crashes in the supervisor; CI stays red, so real regressions are invisible.

2. **With no PIN set, a protected action is approved without any human** (`approval.py:197-201`)
   - **What this is:** `confirm()` gates proposal approval, policy changes and promotion to `main`.
   - **Problem:** if `require_pin` is on, no PIN exists, and the process is not interactive, neither branch runs and the action is approved. Reproduced:
     agent proposes an experiment, then a non-tty process with the agent variables unset runs `decide_proposal(..., True, ...)` and the experiment
     is created (`approved 1 experiments`). The refusal message hands agents the recipe: `_env_hint()` (`approval.py:104-107`) prints
     `env -u CLAUDECODE -u ... research ...`. New installs have no PIN, so this is the default state. GOVERNANCE.md §2 says both "fails closed" and
     "non-interactive runs keep the old behaviour".
   - **Fix:** `if ap["require_pin"] and not is_set(): enroll() if interactive() else raise PolicyBlocked("No approval PIN is set", hint="research approval set-pin")`.
     Drop the `env -u` recipe from messages shown in agent environments (say "run it in your own terminal"). Add one test: no PIN + no tty + human author -> PolicyBlocked.
   - **If we skip it:** "agents propose, humans approve" holds only for users who happened to set a PIN.

3. **An agent can create experiments (and supported findings) by writing a Markdown file** (`store.py:790-858`, `import_file`)
   - **What this is:** sync imports new or edited mirror files as **human** edits (`hand` author, `store.py:818`).
   - **Problem:** reproduced with `CLAUDECODE=1`: writing `.research/experiments/EXP-050-sneaky.md` with `status: ready` and running any command creates
     EXP-050 authored `human`, status `ready`, bypassing `governance.check_experiment_create`. SPEC §22 admits this for findings only. Agents edit Markdown
     files by habit, so honest agents can trip it. The task-`done` case is already guarded (`store.py:825-835`), so the pattern exists.
   - **Fix:** in `import_file`, for a *new* experiment file run `governance.check_experiment_create` with the detected author and, if refused, import it as
     `status: proposed` with an `import_rejected` event (or convert to a proposal); apply the same guard used for task `done` to a finding moving to
     `supported` (`verification.review` gate). One test per case.
   - **If we skip it:** governance has a one-file bypass; the audit log records the agent's experiment as the researcher's.

4. **Telegram misbehaves with two projects, re-runs commands after a network error, and briefly writes the bot token world-readable** (`notify.py:297-321`, `notify.py:224-267`)
   - **What this is:** each project's supervisor calls `notify.poll(p)` every pass; credentials and the `getUpdates` offset live in one machine-wide `telegram.json`.
   - **Problem:** (a) two projects with Telegram enabled poll the same bot: each reads the shared offset, both handle the same message (a `/pause` or `/say` runs in
     both projects, replies come twice), or one consumes messages meant for the other. (b) The offset is saved only after the loop; if `send()` raises
     (Telegram unreachable) the exception leaves `poll` before the save, so the next pass re-executes the same `/say` (a duplicate intervention) or `/resume`.
     (c) `poll` rewrites the file with `open(path + ".tmp", "w")` then `chmod` (`notify.py:317-319`), so on a shared login node the token is readable under umask 022
     between those calls; `set_credentials` already does it right with `os.open(..., 0o600)`. (d) Allow-list is chat id only and the chat is "the first chat that
     messages the bot" (`discover_chat`); in a group, any member commands the supervisor. (e) UI Connect (`rpc.py:715`) does not enable notifications; CLI setup does.
   - **Fix:** one poller per machine is the root fix; smallest fix: store `offset` per project (`.research/supervisor/telegram-offset`), and route by project name
     (`/status` lists all; commands take `PROJECT T-12`), or refuse to enable Telegram in a second project. Save the offset before handling each message. Reuse the
     0600 writer. Record `from.id` at setup and require it. Make `m_telegram_set` call the same function as the CLI.
   - **If we skip it:** commands run twice or in the wrong project; a phone command can pause the wrong plan.

5. **README and SPEC describe 2.6, not the 3.0 being released** (`README.md:8`, `README.md:96-110`, `README.md:407`, `docs/SPEC.md:3`)
   - **What this is:** the user-facing install story.
   - **Problem:** README says "Version 2.6.0", "the VSIX bundles its Python core; no separate pip install needed", "VS Code terminals receive a `research` shim on PATH".
     In 3.0 the VSIX has no Python and the CLI is required; a user following README gets an extension that only shows "CLI missing". SPEC header still says 2.6.0;
     SPEC §0 says 56 commands (60 exist); SPEC §13A says the Home panel remains (it is unreachable, finding 12).
   - **Fix:** rewrite README "Install" to CLI-first (pip/pipx/uv tool install the wheel, then the VSIX); bump headers; fix the counts. Docs only.
   - **If we skip it:** the 3.0 release ships with wrong install instructions on `main`.

6. **3.0 silently dropped "opening the project starts the supervisor" and leaves agents on stale code after self-update** (`extension.ts`, removed block in c123773; `rpc.py:354-367`; `localbin.py`)
   - **What this is:** in 2.6 the extension called RPC `install_bin` on open, which refreshed `.research/bin/lib` (the vendored CLI copy agents use) and ran
     `notify.ensure_running` (start the supervisor for Telegram, replace one running old code).
   - **Problem:** c123773 removed that call ("the extension does not call install_bin", SPEC §13.8). Now after a reboot Telegram stays dead until someone runs
     `research supervisor start`, and after `research self-update` both the supervisor and `.research/bin/research` keep running the old version (agents and MCP
     registrations that point at the launcher use the old code). CHANGELOG 2.6 and SUPERVISOR.md still promise the old behaviour. Nothing refreshes the launcher
     except `init`, `bin install`, `mcp install` and `doctor` (grep of `localbin.install` callers).
   - **Fix:** make `.research/bin/research` a 3-line launcher that execs the installed CLI it was written by (record the interpreter path), instead of a vendored copy
     of both packages. Call `notify.ensure_running` from `research self-update` (for known projects) and from `rpc` startup, or document the manual step.
   - **If we skip it:** version skew between the CLI the user runs and the CLI agents run; the Telegram bot silently stops after reboots.

### Should fix

7. **One pre-existing failing test, and four more broken by the governance change** (`tests/python/test_core.py:211`, `test_guide.py`, `test_release_1_0.py`, `test_research_os.py`, `test_orchestrate.py`)
   - **What this is:** `test_synthesis_required_before_new_experiment` and siblings.
   - **Problem / root cause:** 2.4 added `governance.check_experiment_create` as the first line of `services.create_experiment` (`services.py:169`), and the default
     `experiments.agent_creation` is `propose_only` (`governance.py:18`); the only values are `human_only | propose_only`, so an agent can never create an experiment
     directly. The test's first `pytest.raises(BudgetExceeded)` passes for the wrong reason (`PolicyBlocked` subclasses `BudgetExceeded`), then
     `create_experiment(proj, "E2")` as an agent after synthesis raises `PolicyBlocked: Agents may not create experiments`. The synthesis gate for agents is now
     reachable only inside `governance.approved(...)` (applying an approved proposal). Same cause: `test_guide::test_a_supported_review_logs_automatically...`;
     task-type governance breaks `test_release_1_0::test_required_unapproved_hook_blocks_every_completion_path` ("untyped" agent task). Environment-dependent:
     `test_research_os::test_checkpoint_commit_only_research` (no git identity in CI), `test_orchestrate::test_cli_and_existing_dispatch_unchanged` (exit 127).
   - **Fix:** wrap the agent's second `create_experiment` in `with governance.approved("test"):` and assert the message mentions synthesis; give the guide/release tests a
     human author or a typed task; set `GIT_AUTHOR_*`/`GIT_COMMITTER_*` in the autouse `_clean_env` fixture. Update SPEC §7.2 to say the synthesis gate applies to
     humans and to approved proposals.
   - **If we skip it:** the suite can never go green, so every later failure hides in the noise.

8. **Tests leak supervisor daemons that run for days** (`supervisor.plan_resume` `supervisor.py:218-221`, `notify.ensure_running`)
   - **What this is:** `plan_resume` always starts a detached daemon; Telegram `/resume` and `ensure_running` do too.
   - **Problem:** on this machine two `research.supervisord` processes from pytest temp projects (`pytest-66/test_telegram_commands_repor...`, and a scratch project)
     have been alive 32 hours with ~12 CPU-minutes each. CI's cleanup step killed 7 orphan python/sh/sleep processes. A daemon with Telegram enabled never exits when idle.
   - **Fix:** an autouse fixture that sets `stop` markers / kills daemons whose `--root` is under `tmp_path` at teardown; pass `start_daemon=False` where tests only need state.
   - **If we skip it:** developer machines accumulate pollers; flaky CI timing.

9. **Provider knowledge is hard-coded in ~15 places, so adding `opencode` touches many files** (see section 6a for the list)
   - **What this is:** `providers.py` claims to be "the only place that knows each agent CLI".
   - **Problem:** `("claude","codex","agy")` is repeated in `supervisor.py:630`, `supervisor.py:640`, `rpc.py:888`, `main.py:2864`, `initproject.py:27`,
     `store.py:76-81`, `supervisor_cmds.py:213`, `bootstrap.py:39-45`, `registry.py:191`, `InitProject.tsx:21`, `Providers.tsx:6`; `setup.py:16` has a different set
     (gemini, no agy). `providers.register()` exists but a registered provider would not appear in any of these lists.
   - **Fix:** `providers.KNOWN = tuple(k for k in ADAPTERS if k != "custom")` plus a `LABELS` dict, used everywhere; the UI reads labels from RPC `providers`.
     Delete `setup.py`'s `PROVIDERS` (see finding 11).
   - **If we skip it:** each new provider is a scavenger hunt and parity bugs (like gemini vs agy) multiply.

10. **`main.py` (4,070 lines) and `rpc.py` (1,224 lines, one class) are god-files** (`main.py:3169` `build_parser` 776 lines; `cmd_hpc` 250 lines, CC 106)
    - **What this is:** every CLI command, its printer and its argparse definition live in one file; every RPC method is a method of one class.
    - **Problem:** radon: `cmd_hpc` F(106), `cmd_guide` F(79), `_guide_prov` F(69), `cmd_strategy` F(50), `cmd_config` F(47). A change to one command scrolls through
      four distant places (impl, printer, parser block, `main()` special cases at `main.py:4031-4042`).
    - **Fix (split by job, following the existing `supervisor_cmds.py` pattern with an `attach_*(sub, add)` function per module):**
      `cli/output.py` (L25-104 helpers + printers L105-364), `cli/records_cmds.py` (question/experiment/run/metric/artifact/finding/decision/checkpoint/note,
      ~L618-1240), `cli/skills_cmds.py` (L3001-3166 + L1208-1276), `cli/guide_cmds.py` (L1277-1610), `cli/orchestration_cmds.py` (strategy, hook, usage, orchestrate,
      evidence, config: L1611-2084), `cli/plans_cmds.py` (L2085-2328), `cli/hpc_cmds.py` (L2425-2805), `cli/agents_cmds.py` (L2806-3000); each owns its parser block.
      For RPC: group `Server` methods into mixins per domain (records, plans, supervisor, guide, agents, setup) in the same order; behaviour unchanged.
    - **If we skip it:** merge conflicts and missed sibling edits keep producing the surface drift listed in 2.3.

11. **Two first-run wizards and two Home/Agents UIs** (`setup.py` + `Setup.tsx`; `initproject.py` + `InitProject.tsx`; `render/setup.ts` vs `Providers.tsx`/`ToolsSection.tsx`)
    - **What this is:** "classic" setup (`App.tsx:37`, `onClassic`) and Initialize Project do the same job with different provider lists and code.
    - **Problem:** every setup change must land twice; they already disagree (gemini vs agy).
    - **Fix:** keep Initialize Project (+ `bootstrap.py` YAML form, which already shares its initializer); delete `setup.plan/apply/_environment`, `Setup.tsx`, the
      `onClassic` toggle and RPC `setup_preview/setup_apply/setup_environment` after moving `new_experiment_plan/new_experiment` (still used) into `services.py`.
      Retire the HTML "Agent setup" page in favour of the Control Center Agents tab once its remaining controls (task pins, strategy switch) exist there.
    - **If we skip it:** two code paths to keep correct for the most user-visible flow.

12. **Dead code, verified by caller grep** (safe to delete)
    - **What this is:** code with no caller in `packages/`, `tests/`, `scripts/`, docs examples or extension.
    - **Problem / list:**
      - `packages/core/research/store.py:827` unused import `_pl` (vulture 90%).
      - `plans.add_task_artifact` (`plans.py:557`), `supervisor.retry_task` (`supervisor.py:1239`): zero callers anywhere.
      - `agentsmd.outside_bytes` (`agentsmd.py:179`): tests only. `supervisor.revise_proposal` is used by tests and docs only; check before deleting.
      - RPC methods with no caller in extension, tests, MCP or CLI: `get_plan`, `get_task`, `list_plans`, `list_tasks`, `run_create`, `run_update` (`rpc.py`). Tests-only:
        `agents_md`, `block_task`, `complete_task`, `start_task`, `install_bin`, `next_task`, `guide_chapter_add`.
      - Extension: `panels.openResume` (`panels.ts:98`) has no caller, so `renderResume` (`panels.ts:266-280`), `renderHome` (`render/home.ts:1-309`), `renderResume` and
        `renderOverview` (`render/resume.ts`, used only by `scripts/preview-entry.ts`) are unreachable in the product; `Empty` in `webview/common.tsx:102` (ts-prune).
      - `rpc.MUTATING` duplicates (`hook_approve`, `hook_toggle`, `hook_run` listed twice).
    - **Fix:** delete them (about 600 lines); update SPEC §12 and the preview harness.
    - **If we skip it:** more surface to keep in sync for no user value.

13. **Four "which calls mutate" lists and per-surface `current.md` refresh** (`main.py:627`, `rpc.py:1147`, `mcp.py Tool.mutating`, `panels.ts:13`)
    - **What this is:** after a write, `current.md` (the agent context file) must be regenerated.
    - **Problem:** each surface keeps its own list; the TUI writes through `rpc.Server` methods directly and skips the refresh entirely; a new mutating RPC
      that is forgotten in `MUTATING` leaves agents reading stale state.
    - **Fix:** mark the context dirty in `Project.tx()` on commit and regenerate once in `Project.close()` (or at the end of the outermost tx). Delete the
      three Python lists; keep `MUTATING_RPC` in TS only for the tree refresh.
    - **If we skip it:** stale agent context after some UI actions.

14. **Every read takes the project write lock** (`runs.py:620`, `store.py:859-899`)
    - **What this is:** `Project.sync()` runs on every CLI command and almost every RPC call.
    - **Problem:** `ingest_root_files` always enters `p.tx()` (write lock + `BEGIN IMMEDIATE` + journal) even when no file grew; `sync` also issues one
      `SELECT ... FROM mirrors WHERE path=?` per file. With several agents and a supervisor polling, reads queue behind writes (bounded by SQLite busy timeout).
      At 1,500 records it is 64-77 ms, so this is about contention, not speed.
    - **Fix:** check offsets/sizes first and enter `tx()` only when there is something to ingest; load the `mirrors` table once into a dict per sync.
    - **If we skip it:** "database is locked" style waits on NFS when many agents run.

15. **Three sources of configuration defaults** (`store.DEFAULT_CONFIG` `store.py:31-151`, module `DEFAULTS` in `supervisor.py:32`, `notify.py:27`, `governance.py:17`, `autopromote.py:36`, plus `registry.py` option defaults)
    - **What this is:** each feature merges its own defaults (`cfg(p)` in 7 modules) on top of the store defaults.
    - **Problem:** the same key has up to three defaults (e.g. `notifications` in `store.py:137` and `notify.DEFAULTS`); `test_config_parity` keeps some in sync,
      not all. `deep_merge` runs on every `cfg()` call.
    - **Fix:** let `registry.py` own defaults and generate `DEFAULT_CONFIG` from it; delete module `DEFAULTS`.
    - **If we skip it:** a default changed in one place silently differs elsewhere.

16. **Attention items computed twice with different rules** (`control.py:12-55` vs `context.py:104-160` + `verification.attention_items`)
    - **Problem:** Control Center Home and `current.md`/Research Home disagree on what "needs attention" (all unreviewed preliminary findings vs up to 3 agent ones;
      hooks/workspaces/promotions only in one).
    - **Fix:** one `attention(p)` in `verification.py` (or `control.py`) used by both; it already returns `open` targets for the UI.

17. **RPC author override is sticky** (`rpc.py:1190-1196`)
    - **Problem:** a request with `author: {...}` sets `srv.author` for all later requests in that process, so one agent-attributed call makes later UI clicks
      agent-attributed. No current caller sends `author` (grep of `src/`), so it is latent.
    - **Fix:** apply the override for that request only (save and restore `srv.p.author`), or delete the feature since nothing uses it.

18. **CLI surface sprawl** (60 top-level commands)
    - **Problem:** overlapping verbs: `agents`/`agent`/`dispatch`/`provider`/`execution`; `init`/`project-init`/`ledger`/`setup`; `status`/`resume`/`context`/`current`;
      `promote` vs `plan autopromote`; `execution` has alias `exec` while `run exec` means "run a command".
    - **Fix:** in the refactor, group under nouns (`research agent dispatch|list|providers|executions`), keep old names as hidden aliases for one major version.

### Nice to have

19. **Non-atomic writes outside the shared writer** (`mcp._merge_json` `mcp.py:399-412`, `providers.sync_instructions`, `notify.poll`): use `store._atomic_write`.
20. **`test_command_examples` builds the full 776-line parser 266 times and never scans `docs/*.md`** (`tests/python/test_command_examples.py:12`): build once
    (module-level), add `docs/*.md` to `FILES` so SPEC/SUPERVISOR examples are checked.

Smaller items left out: about 15 (style-level duplication in printers, `fmt_*` helpers, CSS duplication between `ui.css` and `app.css`).

---

## 4. Tests

### 4.1 Inventory

1,122 Python tests in 46 files (13,284 lines), plus extension tests: `webview.test.cjs` 25 (jsdom against the real core), `client.test.cjs` 5,
`util.test.cjs` 2, VS Code harness suite 7. Six tests carry `@pytest.mark.slow` (`test_hpc.py` 4, `test_acceptance.py` 2).

| File | Tests | Lines | Notes |
|---|---|---|---|
| test_command_examples | 266 | 42 | parametrized doc-example parsing (finding 20) |
| test_hpc | 90 | 1060 | fake cluster (`fakecluster.py`); 4 slow |
| test_config | 70 | 588 | |
| test_release_safety | 53 | 327 | "regressions from the release review": mixed topics |
| test_fullreport | 37 | 433 | |
| test_plans_tasks | 32 | 395 | |
| test_supervisor / _fixes | 30 / 13 | 796 / 286 | start real daemons |
| test_registry / test_packs | 30 / 30 | 188 / 512 | |
| test_core | 29 | 395 | 1 failing (finding 7) |
| test_provenance / test_intervention | 27 / 26 | 295 / 467 | intervention covers Telegram via a stubbed `_api` |
| test_guide / _quarto / _review | 24 / 19 / 13 | 426 / 320 / 175 | |
| test_sandbox / test_research_os / test_strategy | 23 / 22 / 19 | 320 / 483 / 312 | |
| test_plans_tasks_interfaces / test_evidence / test_agent_features | 18 / 16 / 16 | 316 / 226 / 278 | |
| test_workspace / test_autopromote / test_setup / test_release_1_0 / test_providers | 15 / 14 / 13 / 13 / 13 | | |
| test_promotion / test_skill_enforcement / test_cli_pin_link_escalation | 12 / 12 / 12 | | pty-driven CLI |
| test_orchestrate / test_slurm_rpc_cli / test_initproject / test_tui | 11 / 11 / 11 / 9 | | |
| test_doctor / test_localbin / test_governance / test_cli_e2e / test_bootstrap | 9 / 8 / 8 / 8 / 8 | | cli_e2e: real daemon + fake Telegram HTTP server |
| test_selfupdate / test_config_parity / test_format / test_report_readable / test_handoff / test_environment / test_acceptance | 7 / 6 / 5 / 4 / 4 / 4 / 2 | | |

Runtime: CI's last 3.8 run took 1,047 s (17.5 min) for the suite. Local full run (Python 3.10, M-series Mac) with `--durations=40`: **3 failed, 1,096 passed, 23 skipped in 1,529 s (25.5 min)**; the 3 local failures are the governance ones in finding 7 (the git-identity and exit-127 failures are CI-only). Slowest: `test_cli_e2e` daemon+Telegram 21 s; `test_autopromote` 16, 16, 12, 8, 7 s; `test_fullreport::test_repeated_export_is_byte_identical` 15 s in *setup*; `test_supervisor` 15, 14, 13, 11, 10 s; `test_supervisor_fixes` 12, 10, 10 s; `test_guide_quarto` 11, 11, 10, 7, 7 s (real Quarto/Typst builds). The top 40 tests take about 380 s; none of them is marked `slow`.

### 4.2 Problems

- **Red suite** (finding 7). Locally 3 fail; in CI (3.8) 86 fail + 16 errors and **3.8 red** (finding 1): CI has failed on every push shown by `gh run list` (8 of 8).
- **Organised by release, not by feature:** `test_release_1_0`, `test_release_safety`, `test_research_os`, `test_agent_features`, `test_supervisor_fixes`,
  `test_cli_pin_link_escalation` mix unrelated modules. Merge into per-module files during the split (e.g. hooks tests from `test_release_safety`,
  `test_release_1_0` and `test_agent_features` into `test_hooks.py`).
- **Leaked processes** (finding 8); **environment-dependent** tests (git identity, PATH for `research`, `/dev/tty` for pty tests).
- **Brittle by design:** `test_command_examples` depends on exact prose formatting (`split('…')`, `split('<')`).
- **Slow-marker coverage:** only 6 tests are `slow`, while the CLI/daemon/pty tests (`test_cli_e2e`, `test_cli_pin_link_escalation`, `test_supervisor*`, `test_autopromote`)
  dominate runtime; mark them so `-m "not slow"` is a real inner loop.

### 4.3 Missing coverage (one good test each)

- `approval.confirm` with no PIN, no tty, human author (finding 2).
- Mirror import of a new experiment file / a finding set to `supported` by an agent (finding 3).
- Telegram: two projects polling one bot; `send` failing inside `poll` (offset must not replay); token file mode after `poll` (finding 4).
- Python 3.8 syntax guard: a test that greps the AST for `BinOp(BitOr)` between dict expressions, since vermin misses it (finding 1).
- `rpc.serve` must not turn a TypeError raised inside a method into "bad parameters" (finding 1).
- TUI approve with no PIN set (it calls `decide(note, None)` while Textual owns the terminal; `tui.py:452-458`).

### 4.4 Delete / merge / add

- Delete: tests for RPC methods removed in finding 12; `test_command_examples` parser rebuild (keep the test, build once).
- Merge: `test_supervisor_fixes` into `test_supervisor`; `test_release_*`, `test_research_os`, `test_agent_features` by module; `test_guide_review` into `test_guide`.
- Add: the six tests in 4.3; a daemon-reaper fixture.

---

## 5. Docs

User-facing docs live on `main`; internal ones are dev-only (AGENTS.md).

| Doc | State | Action |
|---|---|---|
| `README.md` | stale for 3.0 (version, install, shim, "bundled Python") | rewrite Install + First run CLI-first (finding 5) |
| `docs/SPEC.md` (3,018 lines) | header 2.6.0; counts; §7.2 synthesis gate; §7.8 vs §22 MCP guide tools contradict; §13A Home panel; §2.1 module map misses `control`, `registry`, `workspace`, `isolation`, `sandbox`, `doctor`, `execution`, `processes`, `selfupdate`, `compat`, `initproject`, `bootstrap`, `setup`, `packs`, `checks` | fix facts now; later split: SPEC keeps data model, storage, protocol; move user how-to to the guides |
| `docs/GOVERNANCE.md` | §2 contradicts itself on no-PIN behaviour; title says "(2.4)" | fix with finding 2 |
| `docs/SUPERVISOR.md` §18, `CHANGELOG.md` 2.6 | promise supervisor restart on project open (gone in 3.0) | fix with finding 6 |
| `docs/PROVIDERS.md` | "Adding a provider: `providers.register`" is misleading (finding 9) | rewrite after finding 9 |
| `docs/GUIDE_V2_START_FRESH.md` | v1->v2 note; SPEC header says "remove v1/migration content" (commit 0559945) | keep one paragraph in README, delete the file |
| `docs/INSTALL_UPDATE.md` (16 lines) | correct for 3.0 | merge into README Install |
| `docs/QUICKSTART.md`, `docs/SETUP.md` | VS Code-first; fine | add a CLI-only path |
| `docs/CONFIGURATION.md`, `docs/AGENT_TOOLS.md`, `docs/GUIDE.md` | current | keep |
| `scripts/make_preview_fixture.py`, `packages/vscode-extension/scripts/preview-*` | screenshot tooling, on `main` today | dev-only per AGENTS.md ("scratch scripts"); remove from `main` at the next merge, or state the exception in AGENTS.md |
| `docs/PHASE_CLEANUP_R_RESEARCH.md`, this file | dev-only | never merge |

---

## 6. Integration points for the two new features

### 6a. `opencode` as a fourth provider (full parity with claude/codex/agy)

What "a provider" touches today, file by file. Items marked (H) are hard-coded lists that should become `providers.KNOWN` first (finding 9).

| Concern | Where | What opencode needs |
|---|---|---|
| argv builder | `providers.py:30-89` `ADAPTERS` | `_opencode(prof, model, effort, prompt, cwd, dirs, headless)`: interactive vs non-interactive (`opencode run ...`), `--model provider/model`, JSON output flag, cwd via `cwd=` (like agy). Headless permissions must stay as narrow as claude's (`providers.py:50-53`): only `research` CLI + MCP, never "allow all" |
| capabilities | `providers.py:18-27` `CAPABILITIES` | honest values for interactive/headless/model/effort/structured_output/usage/resume/mcp/instructions |
| resume | `providers.py:260-310` `native_resume_supported`, `resume_argv`; `session_id_of` L313 | the help-probe argv and flag, the resume argv shape, the session-id key in JSON output; otherwise the context handoff is used automatically |
| usage | `usage.py:51` `PARSERS`; default chosen in `agents.py:377` (`claude_json` only) | an `opencode_json` parser if its JSON reports tokens/cost; else usage stays "unavailable" (never zero) |
| MCP registration | `providers.mcp_install` L170, `mcp_registered` L215; separately `mcp.install`/`CLIENTS`/`status` (`mcp.py:415-471`) | write the `research` server into opencode's project config file (merge, keep other servers); detect it for `mcp_registered`. Merge the two MCP installers while doing this |
| instructions | `providers.instruction_files` L145, `sync_instructions` L153; `agentsmd.py` | opencode reads `AGENTS.md`, so likely nothing; add it to the `providers` list in `instruction_files` |
| skills | `skills.LINK_TARGETS` (`skills.py:39`), `skills.link` config default `store.py:98`, `skills.required_for` discoverability checks (`skills.py:515-527`, `main.py:3066`) | add a link target if opencode reads skills from its own folder; the "discoverable" flags are claude/agents-specific booleans and need generalising |
| agent files | `setup_agents.py` (Claude only, `setup_agents.py:110`) | optional: opencode agent definitions per strategy level; or state "not supported" in capabilities |
| agent detection (security) | `util.detect_author` L172-197; `approval._AGENT_ENV` L24; `client.ts:47`; `conftest.py:14` | the env variable opencode sets in its shell, in all four lists (better: one list in `util`). Without it an ad-hoc opencode session records as `human` and gets PIN prompts |
| secrets isolation | `isolation.SECRET_DIRS` (`isolation.py:46`) | opencode's credential directory |
| defaults (H) | `store.py:76-82` profiles + `handoff.fallback_order` | an `opencode` profile with `provider: opencode`; add to fallback order docs |
| init wizard (H) | `initproject.py:27` `PROVIDER_PROFILES`, L188-191 action texts, L356-362 MCP install loop; `bootstrap.py:39-45` template | add the entry; MCP install via the new installer |
| supervisor (H) | `supervisor.py:630`, `supervisor.py:640` (`_provider_profiles`, `provider_status`) | use `providers.KNOWN` |
| CLI/RPC rows (H) | `main.py:2864`, `rpc.py:888`, `supervisor_cmds.py:213`, `registry.py:191` texts | use `providers.KNOWN` |
| UI (H) | `InitProject.tsx:19-21`, `Providers.tsx:6`, `commands.ts:1261` (MCP client pick) | read labels from RPC |
| strategies | `strategy.py:32` `_LEVELS` maps built-in levels to `claude-*` profiles | nothing required; document an `opencode-*` profile in a custom strategy (PROVIDERS.md pattern) |
| tests | `test_providers.py` (32 provider-name references), `test_handoff.py` | argv golden tests, resume argv, session id parse, MCP file merge, handoff to/from opencode |

Full parity therefore = adapter + capabilities + resume + usage parser + MCP install/detect + agent-env detection + init/defaults/UI entries + tests.
Do finding 9 first; it turns the 11 hard-coded sites into one.

### 6b. A Discord bot next to Telegram

Telegram end to end:

| Piece | Where | Telegram-specific? |
|---|---|---|
| Credentials file `telegram.json` (token, chat_id, offset), 0600, machine-wide | `notify._path/credentials/set_credentials/clear_credentials` L32-62 | yes (layout generic) |
| HTTP call | `notify._api` L72-85 (`RESEARCH_TELEGRAM_API` override for tests) | yes |
| Send with chunking (3,800 chars) + redaction | `notify.send` L88-96 (`taskprompt.redact`) | chunk size yes; redaction generic |
| Setup: discover chat = first chat that messages the bot | `notify.discover_chat` L99-109; CLI `supervisor_cmds.cmd_telegram` L308-345; RPC `m_telegram_set` `rpc.py:715` | yes |
| Inbound: `getUpdates` polling with offset, chat-id allow-list | `notify.poll` L297-321 | yes |
| Command router `/status /blocked /task /logs /debug /options /choose /say /interventions /pause /resume /help`, plain text = `/say`; acts as human "telegram"; protected changes only proposed | `notify.handle` L340-387, `_instruct` L396-420 | **generic** (only `cmd.split("@")` is Telegram bot-name syntax) |
| Halt watcher: halt markers, grace, auto-debug thread, "moving again" message | `notify.step` L224-273, `_debug_and_send` L276, `message_for` L210 | generic, but calls `send()`/`poll()` directly |
| Debug agent | `notify.run_debug` L180, `intervene.run_assistant` | generic |
| Process lifecycle | `notify.ensure_running` L112 (start supervisor if chat ready), `supervisor.run_forever` L1702 calls `notify.step(p)` each pass | `wants_chat` checks Telegram only |
| Config keys | `notifications.{halt_grace_seconds, auto_debug, telegram.enabled}` (`store.py:137`, `notify.DEFAULTS`, `registry.py:223-227`) | `telegram.enabled` |
| CLI | `research telegram setup|status|test|remove|poll` (`supervisor_cmds.py:478`) | yes |
| RPC | `telegram_status|set|test|clear` (`rpc.py:711-731`), allow-listed in `panels.ts:12-13` | yes |
| UI | `TelegramBox` (`PlansPage.tsx:284-305`) | yes |
| Tests | `test_intervention.py:273-467` (stubbed `_api`), `test_cli_e2e.py:29-68` (`FakeTelegram` HTTP server) | yes |
| Security model | token never in project/git/events; only the configured chat obeyed; chat cannot approve protected changes (they become proposals needing the PIN) | model generic; weaknesses in finding 4 |

Smallest shape for Discord: split `notify.py` into the generic part (step/handle/run_debug/message_for) and a **channel** interface of four
functions per transport: `configured() -> bool`, `send(text)`, `poll(handler) -> int`, `setup(...)`. `step()` loops over enabled channels instead of
calling Telegram directly; `ensure_running.wants_chat` becomes "any channel ready". Config `notifications.discord.enabled`; credentials
`discord.json` (0600, written with the existing `os.open(..., 0o600)` pattern); CLI `research discord setup|status|test|remove|poll` (or one
`research notify <channel> ...` command); RPC `discord_*` or generic `notify_*`; a generic UI box. Discord-specific facts the design must handle:
messages are limited to 2,000 characters (chunk size per channel); bot commands use a different prefix convention; channels are multi-user, so the
allow-list must be **user id plus channel id**, not channel id alone (fix Telegram the same way, finding 4); receiving messages needs either the
Gateway (a websocket, which the stdlib does not provide) or REST polling of the channel's messages with the Message Content intent enabled for the
bot. Do finding 4 first so Discord does not inherit the multi-project offset and replay bugs.

---

Verdict: not healthy for release. Fix first: 1 (Python 3.8 crashes, red CI), 2 and 3 (governance bypasses), 7 (red suite), then 5 and 6 before tagging 3.0.

Lean: about -600 lines of dead code now (finding 12), -1,000 more by retiring the classic setup and the HTML Home/Agent-setup pages (finding 11); 0 dependencies to remove (the core is stdlib only; vendored PyYAML is a deliberate fallback).

Not checked: `hpc/` internals beyond quoting and caller graph; `guide_quarto.py`/`provenance.py` logic; `isolation.py` runtimes; `fullreport.py` determinism;
the 31 bundled third-party skills under `templates/catalog`; Windows paths; the real VS Code harness (`npm run test:ext`) and `npm run test:webview` were not run;
opencode/Discord external facts were not researched (another agent covers that).
