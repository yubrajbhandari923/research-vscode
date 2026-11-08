# 5. Agents, discipline and governance

## Authorship detection

`detect_author` resolves who is acting. Priority order:

1. Explicit arguments: CLI `--agent`, `--model`, `--as`.
2. `RESEARCH_AUTHOR_TYPE`, `RESEARCH_AGENT`, `RESEARCH_AGENT_MODEL`.
3. Auto-detection of known agents:
   - `CLAUDECODE=1` or `CLAUDE_CODE_ENTRYPOINT` → agent `claude-code`
   - `CODEX_SANDBOX…` → agent `codex`
4. Otherwise `human`.

**Roles:** if `RESEARCH_AGENT_ROLE` is set (dispatch sets it), the agent name becomes `<name>/<role>`, e.g.
`claude/verifier`. Roles of one CLI therefore count as different identities, which is what lets one subscription act as
implementer and independent reviewer ([Verification gates](#verification-gates)). **MCP:** when the server is started by a client and the environment names no
agent, the client's `clientInfo.name` from `initialize` becomes the agent name (e.g. `claude-code`).

The VS Code extension strips every agent-identity variable (`util.AGENT_ENV_STRIP`: `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_SANDBOX*`,
`RESEARCH_AGENT`, `RESEARCH_EXECUTION`, `RESEARCH_AGENT_ROLE`, `RESEARCH_AGENT_MODEL`, `RESEARCH_AUTHOR_TYPE`) from the RPC process environment,
so **actions taken in the UI are always recorded as human**, even if VS Code itself was launched from an agent's shell. Terminals keep the user's
environment, so an agent running in a VS Code terminal is detected as an agent.

## Policy (`config.yaml → agent_policy`)

| Key | Default | Enforcement |
|---|---|---|
| `max_runs_without_synthesis` | 5 | `create_run`/`exec`/`submit`/`attach` raise `BudgetExceeded` when `unsynthesized ≥ N` |
| `max_failed_runs_without_review` | 3 | same, when `unreviewed_failed ≥ M` |
| `require_experiment_registration` | true | structural: a run cannot exist without an experiment |
| `require_synthesis_before_new_experiment` | true | `create_experiment` raises if *any* experiment has unsynthesized runs. It applies to experiments a human creates and to approved experiment proposals; an agent's own `create_experiment` is refused earlier by governance (`propose_only`, [The supervisor](08-supervisor.md#the-supervisor)) |
| `enforce_for_humans` | false | if false, humans get warnings (returned in `warnings`, printed in yellow) instead of errors |

- **Exit code 3** (`BudgetExceeded`) lets agents detect the block programmatically. The message names the experiment and the fix.
- **Override:** `--override "reason"` (CLI), `override=` (API), or the "Run anyway" button in the UI. Each override logs an
  `override` event with the reason, and the run stores `override_reason`.
- **Visibility:** an experiment over a budget is flagged in `current.md` ("Needs synthesis") and as an attention item on Research Home, the Control Center and the TUI.

## `AGENTS.md`

`research init` (and `research agent instructions`) creates `AGENTS.md`, or updates **only** the text between
`<!-- research:begin` and `<!-- research:end -->`. User content outside the markers is preserved byte for byte.
The block's source is `packages/core/research/templates/AGENTS.block.md`. It covers (sections `Running the CLI`, `Quick start`, `Core rules`, `Scientific practice`, `Remote execution`, `Context hand-off`, `Tiered agents`, `Recording work`, `Task workflow`):

- **Quick start (the context tiers):** `research current` → `research task next` / `research task context T-…` →
  `research search` / `research show` → `research --help`. Notes that every tool is also an MCP tool.
- **Core rules:**
  - every run belongs to an experiment
  - claim before working (`task start`); leave progress notes (`task note`)
  - register artifacts and findings as you work
  - synthesize after `max_runs_without_synthesis` runs
  - findings stay preliminary until an independent review; never review your own
  - never rewrite history; supersede instead
  - large outputs stay where they were produced; register their paths
  - exit code 3 means a policy blocked you: read the hint, don't work around it
- **Recording work:** `research run exec`, in-code `log_metric` / `register_artifact`, `finding create --supports`, `checkpoint create`
- **Task workflow:** `research task start` → work + notes → `research task complete --result` (runs the checks)
- **Scientific practice:** read context in layers, keep intent / implementation / observation / interpretation distinct, bind every number
  to a run metric (`research evidence bind … && verify`), never tune on held-out results.
- **Remote execution (only when `hpc.clusters` is configured):** `research hpc doctor` first, never `ssh` by hand, never submit twice,
  `hpc sync` before proposing more runs, bundles for human-run jobs.
- **Tiered agents:** `research strategy show`, task contracts (`research task context`), `orchestrate verify`, and hooks (`research hook list`).

Every command the block names exists and is covered by a test that parses each `research …` line in the template
(`test_agents_md_only_references_real_commands`):

| Command in the block | What it does |
|---|---|
| `research current` | regenerates and prints `context/current.md` ([`.research/context/current.md`](07-context-reports.md#researchcontextcurrentmd)) |
| `research task context T-…` | focused brief for one task ([Task context](07-context-reports.md#task-context)) |
| `research skill show <name>` | prints a skill's `SKILL.md` (`skill` is an alias of `skills`) |
| `research task next` / `start` / `note` / `complete` | task lifecycle (`complete` is an alias of `done`) |
| `research search "…"` | retrieval over all records ([Search](07-context-reports.md#search-research-search-rpc-search-mcp-research_search)) |

The step-by-step protocol lives in the `run-experiment` and `reproduce-paper` skills.

## Default skills (`.research/skills/`)

`research init` installs the Core Research pack (and any packs the user chose) into `.research/skills/` and links them for Claude Code and Codex; `research skills catalog` lists the catalog
(first-party, bundled and external skills) and `skills packs` the packs. Prompts (`resume-session.md`, `synthesize.md`) and templates (`experiment-brief.md`, `note.md`) are copied beside them. `init` never
overwrites existing files in these folders. The catalog, packs and hooks are described in [AGENT_TOOLS.md](../AGENT_TOOLS.md).

## Verification gates

Two gates keep agent output from silently becoming "established truth". Both raise `PolicyBlocked` (exit code 3, a
subclass of `BudgetExceeded`) for agents, and only warn humans unless `agent_policy.enforce_for_humans` is true.

**Task checks gate completion.** A task's `checks` are deterministic tests, run in the project root:

| Check | String form | Passes when |
|---|---|---|
| command | `cmd:pytest -q` (or any string without a prefix) | exit code 0 within `verification.check_timeout_seconds` (600); the last 2 KB of output is recorded |
| file | `file:outputs/fig3.png` | the path exists and, if it is a file, is not empty |
| metric | `metric:rmse<=0.05` · `metric:EXP-002:rmse<=0.05` · `metric:RUN-0007:acc>=0.9` | the value satisfies the operator (`<= >= < > == !=`). The target defaults to the task's related experiment; for an experiment, the latest completed run that logged the metric is used, then experiment-level metrics |

- `research task verify T-…` runs them and appends a `check` record (`T-…/V<n>`, verdict `pass` / `fail`, per-check
  details). A task in `verify` moves to `done` on pass, or back to `running` on fail.
- `research task done T-…` (`complete_task`) runs the checks first when `verification.checks_gate_task_completion` is
  true (default). A failure blocks agents with the failing checks in the message; humans get a warning and the task completes.
- Tasks without checks complete as before.

**Findings need an independent review to become `supported`.** With `verification.require_review_for_supported` (default
true), an agent cannot create a finding as `supported`, nor move one to `supported` through `update`, `mark`/`set_status`
or the RPC. The only route is a review:

```
research finding review F-003 --verdict supported|contradicted|needs_work --notes "<reasoning>" [--confidence …]
```

| Rule | Detail |
|---|---|
| Independence (`verification.independent_reviewer`) | an agent may not review a finding whose author is an agent with the same `author_name` (role included, so `claude/implementer` ≠ `claude/verifier`) |
| Evidence (`verification.require_evidence_for_supported`) | a `supported` verdict needs at least one `supports` link |
| Effects | `supported` → status supported (and confidence if given) · `needs_work` → preliminary · `contradicted` → contradicted, with the reasoning appended to *Contradicting evidence* |
| Record | a `review` record `F-…/R<n>`; events `reviewed` |

Humans can always set `supported` directly (the UI is always human, [Authorship detection](#authorship-detection)). Known gap by design: a hand edit of a finding's
mirror file is imported as a human edit ([Limitations](12-rationale-limitations.md#limitations)).

**Attention items** (`verification.attention_items`, shown on Research Home and in `current.md`):
`verification_failed` (latest check run of an unfinished task failed), `reviewer_disagrees` (latest review is
`contradicted` or `needs_work`), `awaiting_review` (up to 3 preliminary agent findings with no review yet).

## Coordination between agents

Several agents (and you) work from one task queue without stepping on each other.

| Mechanism | Behaviour |
|---|---|
| **Claim** | `research task start T-…` sets `status=running`, `claimed_by=<author>`, `claimed_at`. Starting a task someone else holds raises `PolicyBlocked` ("already claimed by claude/implementer since …"); `--force` takes it over. `task next` never returns running (claimed) tasks. |
| **Progress notes** | `research task note T-… "…"` appends a `progress` event. `task context`, the task page and the dispatch brief show the latest notes: the hand-off trail for whoever continues. |
| **Release** | `research task release T-… [--note "…"]` returns the task to `todo`, clears the claim, and records the note. |
| **Stale tasks** | a running task with no activity (update, note, check, claim) for `coordination.stale_task_hours` (default 4) is listed by `stale_tasks()`, flagged `STALE` in `current.md`, shown as a `stale_task` attention item on Research Home and as a banner on the task page. |

## Roles, profiles, providers and dispatch

Dispatch prepares the work and starts an agent session that you watch in a terminal; nothing calls model APIs directly (agents are provider CLIs).
Unattended execution exists only under the supervisor ([supervisor](08-supervisor.md)), which dispatches the same way and is bound by the same gates.

```yaml
agents:
  default_profile: claude
  roles: {planner: null, implementer: null, verifier: null, analyst: null}   # null -> default_profile
  profiles:                                                                   # generated from the provider adapters; yours are merged over them
    claude: {command: claude, ...}
    claude-fast:     {command: claude, model: ..., ...}     # <provider>-fast | -balanced | -deep: the level pack of each provider
    claude-balanced: {...}
    claude-deep:     {...}
```

- **Providers** (`providers/`): one adapter module per agent CLI, registered in `providers.ADAPTERS`, plus a generic `custom` adapter. An adapter is the only
  place that knows its CLI: argv construction (model, effort, prompt, headless flags), declared **capabilities** (interactive, headless, model, effort,
  structured output, usage, resume, MCP file, instructions file) so no caller assumes parity, session-id extraction and resume, MCP registration, and its
  **level pack** (`fast`, `balanced`, `deep` mapped to a concrete model and effort). `providers.default_profiles()` turns the packs into the
  `<provider>-fast|balanced|deep` profiles. See [PROVIDERS.md](../PROVIDERS.md) for the capability table and how to add a provider.
- **Profiles** are an executable plus optional model, effort and flags. Switching a model or provider is a one-line config change and research state is unaffected.
  A missing provider, profile or model fails with a hint; no other model is silently substituted.
- **Roles:** `planner` (turn an objective into a plan with checks; do not implement), `implementer` (one task: claim, load skills, run experiments, notes, findings,
  `task done`), `verifier` (run checks or review a finding against its evidence), `analyst` (synthesize an experiment, propose findings).
- **Default role from the target:** finding, verifier; experiment, analyst; plan, planner; task, verifier if it is in `verify` or of type `verification`, else its
  `assigned_role` when that is a role, else implementer; no target, planner.
- **`dispatch(role, target, objective, profile, model, instructions, strategy, level, effort, policy, headless, ...)`** writes a brief to
  `.research/cache/dispatch/<target>-<role>-<timestamp>.md` (role guide, objective, extra instructions, the task brief ([task context](07-context-reports.md#task-context)),
  the harness references, then `current.md`) and returns `argv`, `env`, `brief`, `available` and a copy-pasteable `shell` line. It logs a `dispatched` event and starts
  nothing itself. `env` carries `RESEARCH_AGENT=<profile>`, `RESEARCH_AGENT_ROLE=<role>`, `RESEARCH_ROOT`, `RESEARCH_BRIEF` (and `RESEARCH_AGENT_MODEL`), so everything
  the session records is attributed to `<profile>/<role>`.
- **CLI:** `research agent dispatch ROLE [TARGET]` runs the agent in the current terminal (or prints the command with `--print`); `research task dispatch T-...`
  picks the role from the task; `research agent profiles` and `research agent providers list` show profiles, roles and readiness.
- **Execution records.** A task dispatch writes a single-use **execution record** (`execution.py`) and a wrapper that takes `.research/locks/<task>.execution`
  non-blocking and passes the descriptor to the provider, so the lock outlives a killed wrapper while the provider runs. Execution states: prepared, launched,
  running, completed, failed, unknown. `research agent executions list|show|logs|stop|retry` operate on them; completed is not verified. A stopped or failed task can
  continue with another provider in the same worktree from a portable context document (`supervisor continue`); it is refused while the previous process may still run.

### Allocation strategies

`orchestration.active_strategy` (or `--strategy NAME` for one dispatch) switches profile choice from the `roles` map to `strategy.select()`, the single resolver used by
`research agent dispatch`, `orchestrate route|dispatch|delegate` and `guide refresh`. `research init` and `agent setup` write `orchestration.enabled: true` and an active
strategy; with none set, the older tier routing applies unchanged.

A strategy (`orchestration.strategies.<name>`, merged along `extends:`; dicts merge key by key, lists and scalars replace) maps logical **levels** (`fast`, `balanced`,
`deep`) to profiles (`levels`), task **classes** (`planning`, `scientific`, `verification`, `checked`, `unchecked`, `mechanical`) to levels (`route`), and defines
`coordinator`, `guide`, `attempts`, `escalate` (level to level or `human`), `max_escalations` and `on_scientific_assumption`. Strategies are provider neutral. The built-ins
are `balanced_first` (balanced model coordinates, fast model implements checkable work, deep model only for scientific synthesis), `deep_first`, `fast_max` and
`balanced_heavy`; the older names `sonnet_first`, `opus_first`, `haiku_max` and `sonnet_heavy` are accepted everywhere as aliases. Levels may point at different providers.

- **Precedence**, highest first: `--profile` / `--agent-model`, then `--level`, then a per-task pin (`orchestrate pin`; implementer role only), then `--strategy` for the call,
  then project file, machine file, built-in. A missing profile, an invalid strategy, or a level whose model is not in `orchestration.available` fails with a hint.
  `strategy validate` rejects unknown levels, missing routes and `extends` or escalation cycles. Task class comes from metadata only (type, role, checks and acceptance criteria).
- Verifier dispatch avoids the profile and model that implemented the task and records `independent: false` when it cannot.
- **Snapshot.** A strategy dispatch writes `<brief>.policy.json` (strategy, origin, fingerprint of the resolved policy and profiles, class, level, profile, provider, model,
  reason, overrides, independence, harness fingerprint, git revision, status), appends a copy to the task ledger `dispatches[]`, sets `RESEARCH_STRATEGY`,
  `RESEARCH_STRATEGY_FINGERPRINT` and `RESEARCH_AGENT_LEVEL`, and logs the strategy in the `dispatched` event. History never re-reads the current config.
- **Harness in briefs.** `harness:` skills, context files, instructions file and verification commands are appended to every brief (missing ones flagged).
  **Headless:** `headless=True` adds the profile's headless arguments; `run_headless()` runs the session and records provider-reported usage through the usage parser
  (`.research/usage.jsonl`); a provider without headless support is refused; a missing value is never recorded as zero.

## Project skills

Skills are Agent Skills folders (`SKILL.md` with `name` and `description` front-matter, optional `scripts/`,
`references/`, `assets/`) in `.research/skills/<name>/`. Imported skills are copied verbatim; their provenance and project
settings live in `.research/skills.yaml`, never in the SKILL.md.

| Operation | Behaviour |
|---|---|
| `skills add SOURCE [--only N…] [--list] [--force] [--always] [--applies-to T…] [--no-link]` | SOURCE is a folder, a git URL (`https://…`, `git@…`, `file://…`), `owner/repo`, or a GitHub `…/tree/<ref>/<path>` URL (shallow clone of that ref). **Discovery:** the folder itself if it has SKILL.md; otherwise every SKILL.md folder below it, skipping hidden folders (`.git`, `.claude-plugin`, `.openclaw`, …) and `node_modules`; duplicates by name prefer the copy under a `skills/` folder (so ponytail's `skills/ponytail/` wins over its agent-specific copies). `--list` previews without importing. Existing skills are refused unless `--force`, which moves the old copy to `.research/cache/removed-skills/`. Records `source`, `subpath`, `ref` (commit for git sources), `added_at`. |
| `skills update [NAME…]` | re-import from the recorded source (all imported skills by default) |
| `skills remove NAME` | moves the folder to `.research/cache/removed-skills/`, drops its links and manifest entry |
| `skills set NAME [--always/--no-always] [--applies-to T…]` | `always`: listed in `current.md` under *Project skills* for every session (e.g. a coding-style skill like ponytail). `applies_to`: task types/roles it is suggested for |
| `skills link [--target claude|agents|DIR]` | per-skill relative symlinks `.claude/skills/<name>` (Claude Code) and `.agents/skills/<name>` (Codex and other Agent Skills tools) → `.research/skills/<name>`. Entries that aren't ours are never touched; our links to removed skills are deleted. Falls back to copying where symlinks aren't allowed. Runs automatically after add/update/remove for the targets in `skills.link`. |
| `skills show NAME` · `skills list` | print SKILL.md · list with source, always-on and applies-to |

**Matching to tasks** (`skills_for`): skills assigned to the task, assigned to its plan, whose `applies_to` contains the
task's type or role, and always-on skills, each with a reason. `task context`, the task page and dispatch briefs list them
("load with `research skill show NAME`"); assigned skills that aren't installed are flagged.

## The managed AGENTS.md block

**Module:** `research/agentsmd.py`. A `.research/` of another project format is rejected, never converted. Only the Research OS-owned region of `AGENTS.md` is managed.

One owned region between `<!-- research:begin … -->` and `<!-- research:end -->`. `locate()` matches whole
marker **lines**, not bare substrings, so prose that mentions a marker — in the block itself or in your own
notes — cannot be mistaken for one.

| Command | Behaviour |
|---|---|
| `--preview` | prints the block this version generates; touches nothing |
| `--diff` | unified diff of the file as it is vs. as it would be |
| `--apply` | rewrites only the managed region (the default action) |

Bytes outside the region are preserved exactly, including CRLF line endings and a missing trailing newline
(the file is read and written with `newline=""`). Markers absent → propose adding a block, leaving existing
content alone. Markers duplicated, reversed or malformed → **refuse** with the manual fix, change nothing,
and change nothing else. No three-way merge, no inference about which instructions to
remove: a deliberately human-reviewed workflow.

## Governance and approvals

Normative user-level detail: [GOVERNANCE.md](../GOVERNANCE.md). Summary of what the code enforces:

- **Agents propose, humans approve.** An agent can never create an experiment: with `governance.experiments.agent_creation: propose_only` it submits a proposal, with `human_only` it cannot
  even propose. Task creation is `bounded_auto` (an allowed type inside its own experiment, up to a cap), `propose_only` or `human_only`. Scientific changes always wait for a human.
  `governance` is the ceiling; older `supervisor.auto_apply_operational` and `supervisor.max_auto_tasks` can only tighten it, and experiment overrides can only be stricter.
- **Proposal states:** `pending`, `approved` (applied exactly once, under the supervisor lock), `rejected`, `revision_requested` (the agent revises, which returns it to `pending`), and `applied`
  for auto-applied operational proposals. Approval checks the revision reviewed and re-checks conflicts. Every decision is in the audit trail (actor, time, action, reason; never the PIN).
- **Where it is enforced:** in the functions every path ends in (`services.create_experiment`, `plans.create_task`, `supervisor.decide_proposal`, `registry.apply` for policy keys,
  `promotion.approve` and `promotion.integrate`), not in buttons or briefs. A proposal being applied runs inside an in-process grant agents cannot reach. A hand-edited mirror in which an agent
  writes a new experiment is imported as `proposed` with a proposal; an agent edit that sets a finding to `supported` is reverted with an event.
- **The approval PIN** (`approval.py`) is a local guard against accidental autonomous changes. Protected actions are approving an agent's proposal, changing policy settings, approving or integrating
  a promotion, authorizing unattended promotion and approving an intervention. The verifier is a salted scrypt hash in `approval.json` in the machine config directory (mode 0600), never in the
  project, git, events, prompts or MCP output; five wrong tries lock it for 60 seconds, and a PIN that could not be read (no terminal) is refused without counting as a wrong attempt.
- **Fail closed.** With no PIN set, a protected action in a real terminal first offers to create one; anywhere else (VS Code, RPC, CI, an agent shell) it is refused with a hint. A process that looks
  like an agent (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_SANDBOX*`, `RESEARCH_AGENT`, `RESEARCH_EXECUTION`) is refused and PIN prompts are disabled, whatever `--as` claims. The explicit opt-out is
  `governance.approval.require_pin: false`; protected actions then still need a human and a rationale.
- **Limits, plainly.** This is a guard rail for honest agents, not authentication. An agent running as the same OS user can read project files and machine files or edit the code. Use job isolation
  and worktrees to limit what agents can touch ([execution](06-execution.md#job-isolation)).
