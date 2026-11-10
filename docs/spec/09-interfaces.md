# 9. Interfaces: CLI, Python API, RPC, MCP, TUI, extension

Every interface reaches the same core ([architecture](01-architecture.md)). The tables below marked *generated* are written from the code by
`python tests/python/gen_reference.py`; do not edit them by hand (a test fails when they are stale).

## Command-line interface

**Global flags** (accepted before or after the subcommand): `--json` (machine-readable output), `--root DIR`, `--agent NAME`, `--model M`,
`--as human|agent|system`, `--version`.

- **Project discovery:** `--root`, else `$RESEARCH_ROOT`, else walk up from the working directory to the nearest `.research/config.yaml`
  (in shared-ledger mode git is asked for the ledger link, [execution](06-execution.md#shared-ledger-and-experiment-workspaces)).
- **Exit codes:** `0` ok, `1` error, `2` not initialized or bad parameters, `3` blocked by run budget, policy, approval or sandbox, `4` not found,
  `130` interrupted. With `--json`, errors are printed on stderr as `{"error","hint","code"}`.
- **Output:** colour only when stdout is a TTY and `NO_COLOR` is unset. Errors go to stderr.
- **Command separator:** everything after the first `--` is the command to run, passed verbatim (`research run exec EXP-001 -- python train.py`).
- **`KEY=VALUE` parsing** (`--param`, `config set`): integers, floats (`3e-4`), `true`/`false`, `null`/`none`, JSON lists and objects, else text.
- **Aliases.** Some commands have short visible aliases (`q`, `exp`, `r`, `cp`, ...). Older command names that were regrouped stay as hidden
  aliases that run the new command; they are not listed by `--help` and are removed only at a major version ([compatibility](../SPEC.md#version-compatibility-contract)).
- A command that changes data regenerates `.research/context/current.md` afterwards; `research current` prints it.

<!-- generated:cli -->
| Command | Aliases | Summary |
|---|---|---|
| `research init` |  | initialize .research/ in a project (and AGENTS.md); or --from research-os.yaml for a whole project |
| `research status` |  | compact project status; --full: latest checkpoint + current state |
| `research current` |  | print the current research state for agents (regenerates current.md); --quiet only regenerates it |
| `research search` |  | search everything recorded: research search "dataset prep" |
| `research ledger` |  | shared research ledger for git worktrees: status \| enable [--apply] \| relink |
| `research version` |  | print the core version and RPC protocol (--json for the extension) |
| `research self-update` |  | install the newest GitHub release (sha256-verified) |
| `research tui` |  | terminal UI (needs the tui extra) |
| `research doctor` |  | inspect the project: records, workspaces, runs, agents, sandbox, context (read-only) |
| `research show` |  | show any object by id (Q-…, EXP-…, RUN-…, F-…, D-…, CP-…, A-…, note:…) |
| `research mark` |  | set status of any object: research mark Q-001 answered |
| `research sync` |  | import hand-edited files, ingest job outputs, refresh run status |
| `research config` |  | configuration: show [--effective\|--sources] \| check \| options \| explain \| set \| reset |
| `research log` |  | audit log of recent changes |
| `research project` |  | project metadata |
| `research tuning` |  | declare \| trial \| select \| test (held-out, once) |
| `research evidence` |  | numerical evidence: bind \| verify \| value \| suggest \| provenance \| artifact \| pin \| status |
| `research source` |  | literature sources: add \| mark \| list (offline; identity, examination and support are separate) |
| `research question` | `q` | research questions |
| `research experiment` | `exp`, `e` | experiments (designed tests; own their runs) |
| `research run` | `r` | runs (single executions; always belong to an experiment) |
| `research metric` | `m` | metrics |
| `research artifact` | `a` | artifacts (referenced by path, never copied) |
| `research finding` | `f` | findings (interpreted, reusable results) |
| `research decision` | `d` | decisions (deliberate choices based on findings) |
| `research checkpoint` | `cp` | checkpoints (snapshots of project understanding) |
| `research note` | `notes` | your project notes (.research/notes/) |
| `research plan` | `plans`, `p` | research plans (decomposed into tasks) |
| `research task` | `tasks`, `t` | tasks within plans (units of work) |
| `research intervention` |  | supervisor interventions: list \| show \| approve \| reject \| cancel \| plan |
| `research env` |  | the project's Python environment: check \| create (uv) \| install |
| `research supervisor` |  | the persistent supervisor: start \| status \| stop \| instruct T-12 (steer a task in words) \| debug [PLAN] (diagnose a halt) \| reload (apply settings, agents untouched) \| restart (new process, agents untouched) \| tick \| approve \| accept \| resolve \| continue (hand a task to another agent) \| providers |
| `research proposal` | `proposals` | proposals: list \| show \| approve \| reject \| revise (ask for changes) \| experiment (agents: propose one) |
| `research approval` |  | the approval PIN: status \| set-pin (create or change) \| verify; chat-enroll \| chat-revoke \| chat-status (authenticator for chat approval) |
| `research promote` | `promotion` | select experiment code for main: list \| changes \| prepare \| show \| diff \| verify \| review \| approve \| integrate \| abandon |
| `research decisions` |  | open decision requests (what the supervisor or an agent is waiting for you to decide) |
| `research investigate` |  | run the bounded investigator agent on a task or a halted plan (budget-capped; it never approves; protected changes wait for your PIN) |
| `research decide` |  | answer a decision request: decide DR-001 [KEY \| "free text"] (no answer: show it) |
| `research chat` |  | chat gateway (opt-in): setup [telegram\|discord] \| status \| doctor [--live] \| pair CODE \| unpair PLATFORM USER \| test [PLATFORM] \| remove [PLATFORM] \| gateway start\|stop\|restart\|status\|run |
| `research agent` |  | agent sessions: dispatch \| profiles \| providers \| executions \| instructions \| setup \| usage; and structured reports from a supervised agent (progress \| milestone \| … \| finish) |
| `research mcp` |  | MCP server for agents (stdio); `research mcp install` registers it |
| `research orchestrate` |  | route \| dispatch \| freeze \| attempt \| correct-test \| delegate \| status \| contract \| verify \| pin |
| `research strategy` |  | model-allocation strategies: list \| show [NAME] \| use NAME \| validate [NAME] \| route T-… |
| `research hook` |  | project harness hooks: list \| add \| enable \| disable \| approve \| revoke \| run \| results \| recommend \| install |
| `research skills` | `skill` | agent skills (.research/skills/) |
| `research report` |  | write a readable report (project, or one Q-/PLAN-/EXP-) to .research/reports/ |
| `research guide` |  | Scientist Guide (Quarto book on main): init \| status \| highlights \| star F-… \| log \| cite list\|add\|check \| draft [done\|check] \| publish \| claim \| figure \| section \| validate \| coverage \| quarto … \| chapter add \| review … |
| `research site` |  | research website (Quarto, GitHub Pages): init \| preview \| build \| status \| add EXP \| remove EXP \| list \| sync |
| `research workspace` | `ws` | experiment worktrees: info \| create \| attach \| list \| show \| link-task \| assign \| archive |
| `research sandbox` |  | optional job isolation: list \| show NAME \| doctor \| verify NAME \| preview EXP |
| `research hpc` |  | remote execution: doctor \| init \| submit \| status \| sync \| collect \| import \| bundle \| datasets \| inspect \| cancel \| clusters \| config |
| `research git` |  | git hygiene: status \| preflight \| ignore \| commit (reviewed, scoped, never pushes) |

**`research project`** actions: `set` (set name/goal/description)

**`research question`** actions: `create` (create a question); `list` (list questions); `update` (edit a question); `show` (show a question)

**`research experiment`** actions: `create` (register an experiment BEFORE running it); `list` (list experiments); `show` (experiment detail: runs, metrics, artifacts, synthesis); `fork` (new experiment whose worktree starts from another experiment's pinned code (no promotion needed)); `sync` (explicitly merge main (or an EXP-/commit) into a forked experiment's branch); `update` (edit experiment fields); `variant` (derive a new experiment with changed parameters); `start` (mark experiment running); `ready` (mark experiment ready); `review` (mark experiment needs_review); `complete` (mark experiment completed); `fail` (mark experiment failed); `abandon` (mark experiment abandoned); `baseline` (mark as the current baseline); `check` (validate runs deterministically, list what needs interpretation, optionally prepare synthesis); `synthesize` (record a synthesis of the experiment's runs); `run` (execute a run of this experiment (same as `run exec`))

**`research run`** actions: `exec` (run a command locally, tracked: research run exec EXP-001 -- python x.py); `submit` (submit via SLURM: research run submit EXP-001 --time 1:00:00 -- python x.py); `attach` (register a run you already executed yourself); `list` (list runs); `status` (show run status (refreshes from backend)); `update` (update a run (status, exit code, notes, reviewed)); `cancel` (cancel a running/queued run); `logs` (print run logs); `sync` (refresh status of all active runs); `compare` (side by side: parameters, metrics, code, artifacts of two runs); `sweep` (one run per grid point: research run sweep EXP-1 --grid lr=1e-3,1e-4 -- python t.py --lr {lr})

**`research metric`** actions: `add` (log a metric value)

**`research artifact`** actions: `add` (register one or more files/dirs); `list` (list artifacts)

**`research finding`** actions: `create` (record a finding with evidence); `list` (list findings); `update` (edit a finding / its evidence); `review` (independent review: --verdict supported|contradicted|needs_work); `supersede` (mark superseded (optionally by a newer finding)); `show` (show a finding)

**`research decision`** actions: `create` (record a decision); `list` (list decisions); `update` (edit a decision)

**`research checkpoint`** actions: `create` (create a checkpoint (pre-filled from current state)); `list` (list checkpoints); `draft` (show the auto-generated draft without saving); `show` (show a checkpoint (default: latest))

**`research note`** actions: `new` (create a note); `list` (list notes); `pin` (pin/unpin a note); `link` (link a note to questions/experiments/findings)

**`research plan`** actions: `create` (create a plan); `list` (list plans); `show` (show plan with all tasks); `update` (update a plan); `yaml` (YAML plans: template | export | validate | diff | apply); `promote` (prepare promotion candidates from verified, committed work (nothing merges)); `autopromote` (unattended code promotion into main (off by default): status | authorize (human, PIN; allowed before `plan run`, starts nothing) | revoke | retry); `run` (let the supervisor execute a plan); `pause` (pause scheduling for a plan); `resume` (resume a paused or blocked plan); `stop` (stop a plan run (history is kept)); `status` (supervised status: phases, attempts, budgets)

**`research task`** actions: `create` (create a task); `list` (list tasks); `show` (show task detail); `context` (focused context for working on a task: plan, deps, question, experiment, rules); `update` (update a task); `start` (claim a task and mark it running); `release` (give a claimed task back (→ todo) with a hand-off note); `note` (add a progress note: research task note T-003 "loader done, tests next"); `verify` (run the task's checks and record the result); `dispatch` (hand this task to an agent CLI (role from the task)); `done` (mark task as done (completed)); `block` (mark task as blocked); `next` (show next ready task (dependencies satisfied))

**`research agent`** actions: `dispatch` (hand work to an agent CLI: research agent dispatch verifier F-003); `executions` (launched agent sessions: list | show | logs | stop | retry (completed ≠ verified)); `providers` (agent providers: list | set-default NAME | mcp NAME [--apply]); `profiles` (agent profiles and roles (config.yaml → agents)); `usage` (model usage: `agent usage [--by strategy|model|task|level]` · `agent usage import FILE`); `instructions` (create/refresh the research block in AGENTS.md); `setup` (set up agent files for a provider: research agent setup PROVIDER --preset scientific [--apply]); `progress` (free-text progress); `milestone` (a named milestone); `artifact` (an output file (workspace-relative)); `metric` (a metric value); `run` (a computational run id); `hpc-job` (an HPC job id); `code` (a commit / changed files); `problem` (a problem with a failure category); `report` (summary of what was done and found); `finish` (request verification); `propose` (propose a plan change (YAML patch))

**`research skills`** actions: `list` (list skills); `show` (print a skill's SKILL.md); `add` (import skill(s) from a folder or git repo (URL, owner/repo, …/tree/main/skills/x)); `remove` (remove a skill (moved to .research/cache/removed-skills)); `update` (re-import skills from their recorded source); `set` (project settings for a skill); `verify` (run a protocol skill's machine-checkable requirements); `audit` (which skills gate managed dispatch, and what direct Claude Code/Codex sessions can see); `link` ((re)create .claude/skills and .agents/skills links); `catalog` (the skills catalog: first-party, bundled (attributed copies) and install-from-source entries); `packs` (skill packs: list | enable ID [--optional] | disable ID); `install` (install catalog skill(s) (bundled copy, or fetched from the source at the pinned commit)); `enable` (turn a skill on (it stays on disk either way)); `disable` (turn a skill off: not suggested, not linked; files kept); `upgrade` (bring bundled and first-party skills to this release's version (never replaces a skill you changed)); `new` (create a skill); `duplicate` (copy a skill)

**`research site`** actions: `init` (preview the website files; --apply creates them (never overwrites; also repairs)); `status` (quarto, workflow, remote, Pages URL, published experiments and the exact next commands); `preview` (live local website with hot reload (no GitHub needed)); `build` (render the static site locally and check its links (never runs code)); `add` (publish a verified experiment's committed report and figures as a draft page you can edit); `remove` (unpublish an experiment: move it out of the site (nothing is deleted); previews unless --apply); `list` (experiments on the website and their state); `sync` (publish verified experiments that have committed reports and are not on the site yet (adds only))

**`research git`** actions: `status` (main repo, experiment worktrees, promotions and ledger: what is uncommitted and why it matters); `preflight` (the check run before a plan starts/resumes or promotion is authorized (exit 2 when blocked)); `ignore` (preview (default) or --apply a managed .gitignore block for generated and internal files); `commit` (show the staged diff of the named files; --yes commits exactly those files on main (humans only, never pushes))

Hidden top-level aliases (kept so older scripts and agent sessions keep working; not listed by `--help`): `agents`, `agents-md`, `bin`, `context`, `dispatch`, `exec`, `execution`, `project-init`, `provider`, `rebuild`, `resume`, `rpc`, `setup`, `telegram`, `usage`.
<!-- /generated:cli -->

## Python API

`import research` exposes the functions in `research.__all__`. Each accepts `project=Project(...)`; without it the function opens the project
found from the working directory or `$RESEARCH_ROOT`, syncs, and closes it. Groups: initialisation (`init`, `open_project`), context
(`get_resume_context`, `regenerate_context`, `show`, `search`), records (`create_*`, `update_*`, `list_*`, `get_*` for questions, experiments,
runs, artifacts, findings, decisions, checkpoints, notes, plans and tasks), lifecycle (`set_status`, `synthesize`, `set_baseline`,
`supersede_finding`, `start_task`, `complete_task`, `block_task`, `release_task`, `verify_task`, `review_finding`), execution (`exec_run`,
`submit_run`, `cancel_run`, `sweep_run`, `attach_run`), reporting (`write_report`, `compare_runs`, `task_context`) and agents (`dispatch`, skills
functions). `research.log_metric(...)` and `research.register_artifact(...)` are safe to call inside a job: with `RESEARCH_RUN_DIR` set they write
only run-directory files ([execution](06-execution.md#metrics-and-artifacts-from-inside-a-run)). Errors are `ResearchError` subclasses
(`NotFound`, `NotInitialized`, `IncompatibleProject`, `BudgetExceeded`, `PolicyBlocked`), each with `exit_code`, an optional `hint` and `data`.

## The operation layer and JSON-RPC

`research/api/` defines each operation once with `@op(...)`. An op is `(project, **params)`; flags say whether it needs a project, whether it
syncs first, whether it mutates, whether an agent may call it over MCP (`mcp="tool_name"`), and whether chat may call it (`chat="read"|"decide"`).
A parameter named `dry_run` is the preview convention. `api.call(name, session, params, surface)` is the single entry used by RPC, MCP and the TUI.

**RPC transport** (`python -m research.rpc --root DIR`, started by the extension as `research rpc`): one JSON object per line on stdin and stdout.

```
-> {"id": 7, "method": "show", "params": {"id": "EXP-001"}}
<- {"id": 7, "result": {...}}                     (plus "changed": true when the call wrote to the database)
<- {"id": 7, "error": {"message": "...", "hint": "...", "code": 4, "kind": "NotFound", "data": null}}
```

- **Handshake:** the first line is `{"event":"ready","version","protocol","root","initialized","ops":[...]}`. `protocol` is the framing version (1);
  `ops` lists every method this core serves, so a newer extension hides a feature whose op is missing instead of failing.
- **Errors:** core exit codes (1 to 4) for `ResearchError`; `-32700` invalid JSON, `-32600` invalid request, `-32601` unknown method,
  `-32602` bad parameters, `-32603` internal error (with a short `trace`). An op other than `ping` and `init` fails with code 2 on an uninitialized project.
- **Sync:** an op with `sync=True` (the default) syncs the project first; read-only helpers and config ops opt out.
- **Author:** `params.author` (`{agent, model, author_type}`) overrides the author for that one request and is not sticky. The extension strips
  every agent-identity variable from the RPC process environment, so UI actions are always recorded as human
  ([agents](05-agents-governance.md#authorship-detection)).
- **On open** the server remembers the project (`machine.remember`), starts the supervisor when a plan is running or Telegram is enabled,
  and replaces a daemon running older code (best effort, in the background).

<!-- generated:ops -->
225 operations.

| Op (RPC method) | Parameters | Mutates | MCP tool | Chat |
|---|---|---|---|---|
| `agent_propose` | task, yaml, rationale | yes | `agent_propose` |  |
| `agent_report` | task, kind | yes | `agent_report` |  |
| `agent_run_start` | experiment, command, parameters, label, cwd | yes | `run_start` |  |
| `agent_setup` |  |  |  |  |
| `agent_task_context` | id |  | `task_context` |  |
| `agents` |  |  |  |  |
| `agents_md_apply` |  |  |  |  |
| `agents_md_state` |  |  |  |  |
| `approval_set_pin` | new, old | yes |  |  |
| `artifact_register` | path, run, experiment, description | yes | `artifact_register` |  |
| `attempt_detail` | task, attempt |  |  |  |
| `chat_approve_proposal` | id, content_hash, code, platform, user_id, role | yes |  | approve |
| `chat_cmd_approve_start` | task | yes |  | decide |
| `chat_cmd_blocked` |  |  |  | read |
| `chat_cmd_choose` | n, via | yes |  | decide |
| `chat_cmd_continue` | task, profile | yes |  | decide |
| `chat_cmd_debug` |  |  |  | decide |
| `chat_cmd_decisions` |  |  |  | read |
| `chat_cmd_digest` |  |  |  | read |
| `chat_cmd_finding` | id |  |  | read |
| `chat_cmd_interventions` |  |  |  | read |
| `chat_cmd_logs` | task, stream |  |  | read |
| `chat_cmd_options` |  |  |  | read |
| `chat_cmd_pause` | plan | yes |  | decide |
| `chat_cmd_proposals` |  |  |  | read |
| `chat_cmd_reject` | kind, id | yes |  | decide |
| `chat_cmd_report` | scope |  |  | read |
| `chat_cmd_resume` | plan | yes |  | decide |
| `chat_cmd_say` | text, task, via | yes |  | decide |
| `chat_cmd_status` |  |  |  | read |
| `chat_cmd_stop` | task | yes |  | decide |
| `chat_cmd_task` | task |  |  | read |
| `chat_cmd_tasks` |  |  |  | read |
| `chat_cmd_usage` | period |  |  | read |
| `chat_gateway` | action | yes |  |  |
| `chat_pair` | code, role | yes |  |  |
| `chat_remove` | platform | yes |  |  |
| `chat_setup` | platform, token, chat_id, discover, guild_id | yes |  |  |
| `chat_status` |  |  |  |  |
| `chat_test` | platform | yes |  |  |
| `chat_users` | platform |  |  |  |
| `check_run_budget` | experiment |  |  |  |
| `checkpoint_create` | understanding, problem, next, commit | yes | `checkpoint_create` |  |
| `checkpoint_draft` |  |  |  |  |
| `compare_runs` | a, b |  | `runs_compare` |  |
| `config` |  |  |  |  |
| `config_check` |  |  |  |  |
| `config_show` | effective, sources |  |  |  |
| `control` | page |  |  |  |
| `create` | type | yes |  |  |
| `debug_latest` |  |  |  |  |
| `debug_run` | plan | yes |  |  |
| `decision_answer` | id, key, text, via, note, pin | yes |  | decide |
| `decision_expire` | id | yes |  |  |
| `decision_list` | status, task |  |  | read |
| `decision_request` | question, options, subject, default, expires_hours | yes | `decision_request` |  |
| `decision_show` | id |  |  | read |
| `dispatch` | role, target, objective, profile, model, instructions, headless, effort, strategy, level, create_workspace, allow_unfrozen | yes |  |  |
| `dispatch_prepare` | role, target, objective, profile | yes | `dispatch_prepare` |  |
| `doctor` | fix |  | `doctor` |  |
| `duplicate_skill` | src, name |  |  |  |
| `env_check` | packages, python |  |  |  |
| `env_probe` | python, packages |  |  |  |
| `events` | limit |  |  |  |
| `execution_logs` | id |  |  |  |
| `execution_retry` | id, profile, model |  |  |  |
| `execution_show` | id |  |  |  |
| `execution_start` | id |  |  |  |
| `execution_stop` | id |  |  |  |
| `executions` | task |  |  |  |
| `experiment_check` | id, request_synthesis | yes |  |  |
| `experiment_create` | title | yes | `experiment_create` |  |
| `experiment_fork` | id, title, at |  |  |  |
| `experiment_propose` |  | yes | `experiment_propose` |  |
| `experiment_state` | id |  |  |  |
| `experiment_sync` | id, source |  |  |  |
| `experiment_synthesize` | id | yes | `experiment_synthesize` |  |
| `finding_create` | title, statement, kind, confidence, supports, contradicts, questions, limitations | yes | `finding_create` |  |
| `freeze_tests` | task, paths | yes |  |  |
| `git_commit` | paths, scaffold, message, confirm | yes |  |  |
| `git_commit_preview` | paths, scaffold, message |  |  |  |
| `git_health` |  |  |  |  |
| `git_ignore` | apply, patterns | yes |  |  |
| `git_preflight` | purpose |  |  |  |
| `governance` |  |  |  |  |
| `guide_build` | edition, pdf, allow_invalid | yes |  |  |
| `guide_cite_add` | bibtex, key, title, authors, year, doi, journal, url | yes |  |  |
| `guide_cite_list` |  |  |  |  |
| `guide_draft` | results, section, profile, model | yes |  |  |
| `guide_draft_check` |  |  | `guide_draft_check` |  |
| `guide_draft_done` | ids | yes | `guide_draft_done` |  |
| `guide_highlight` | id, on | yes |  |  |
| `guide_highlights` |  |  | `guide_highlights` |  |
| `guide_init` | workflow | yes |  |  |
| `guide_log` | ids | yes |  |  |
| `guide_preview` | edition |  |  |  |
| `guide_status` |  |  | `guide_status` |  |
| `guide_validate` |  |  | `guide_validate` |  |
| `home` |  |  |  |  |
| `hook_approve` | names | yes |  |  |
| `hook_install` | names, approve | yes |  |  |
| `hook_install_starters` | approve | yes |  |  |
| `hook_run` | name | yes |  |  |
| `hook_toggle` | name, enabled | yes |  |  |
| `hpc_bundle` | experiment, command | yes | `hpc_bundle` |  |
| `hpc_discover` |  |  |  |  |
| `hpc_doctor` | cluster |  | `hpc_doctor` |  |
| `hpc_init` | cluster, dry_run | yes |  |  |
| `hpc_reconcile` | run | yes |  |  |
| `hpc_status` |  |  |  |  |
| `hpc_submit` | experiment, command, cluster, dry_run, approve, resources | yes |  |  |
| `hpc_sync` | cluster | yes | `hpc_sync` |  |
| `hpc_test` | ssh_host, workspace_root, python, required_packages, setup, partition, compute |  |  |  |
| `index` |  |  |  |  |
| `init` | name, goal | yes |  |  |
| `init_apply` | spec |  |  |  |
| `init_environment` |  |  |  |  |
| `init_preview` | spec |  |  |  |
| `init_yaml_apply` | text | yes |  |  |
| `init_yaml_check` | text |  |  |  |
| `init_yaml_export` |  |  |  |  |
| `init_yaml_template` |  |  |  |  |
| `intervention_action` | id, action, note, pin | yes |  |  |
| `intervention_request` | task, message, interrupt | yes |  |  |
| `interventions` | task |  |  |  |
| `link_note` | id, targets, remove |  |  |  |
| `log_metric` | name, value | yes |  |  |
| `managed_plans` |  |  |  |  |
| `mcp_install` | clients |  |  |  |
| `metric_log` | name, value, run, experiment, step, unit | yes | `metric_log` |  |
| `new_experiment` | spec, pin |  |  |  |
| `new_experiment_preview` | spec |  |  |  |
| `pack_set` | id, enabled, optional | yes |  |  |
| `pin_note` | id, pinned |  |  |  |
| `ping` |  |  |  |  |
| `plan_control` | plan, action, cancel_agents | yes |  |  |
| `plan_create` | title, objective, success_criteria, question, skills | yes | `plan_create` |  |
| `plan_promote` | plan, experiment | yes |  |  |
| `plan_yaml_apply` | text, dry_run | yes |  |  |
| `plan_yaml_diff` | text |  |  |  |
| `plan_yaml_export` | plan, experiment, task, partial |  |  |  |
| `plan_yaml_template` |  |  |  |  |
| `plan_yaml_validate` | text |  |  |  |
| `promotion_action` | id, action, note, confirm, verdict, command, pin |  |  |  |
| `promotion_diff` | id |  |  |  |
| `promotion_hint` | plan |  |  |  |
| `promotions` | closed |  |  |  |
| `proposal_decide` | id, approve, note, pin, rev | yes |  |  |
| `proposals` |  |  |  |  |
| `provider_default` | name |  |  |  |
| `provider_mcp` | name, apply |  |  |  |
| `provider_status` | task |  |  |  |
| `providers` |  |  |  |  |
| `question_create` | title, description, parent | yes | `question_create` |  |
| `rebuild` |  | yes |  |  |
| `register_artifact` | path | yes |  |  |
| `release_task` | id, note | yes | `task_release` |  |
| `report` | scope, fmt | yes | `report` |  |
| `report_full` | scope, check |  | `report_full` |  |
| `reports` |  |  |  |  |
| `research_current` |  |  | `research_current` |  |
| `research_search` | query, type, limit |  | `research_search` |  |
| `resume` |  |  |  |  |
| `review_finding` | id, verdict, notes, confidence | yes | `finding_review` |  |
| `run_attach` | experiment | yes |  |  |
| `run_cancel` | id | yes |  |  |
| `run_logs` | id, stream, tail |  | `run_logs` |  |
| `run_refresh` | id |  |  |  |
| `run_start` | experiment, command, backend | yes |  |  |
| `run_status` | id |  | `run_status` |  |
| `sandbox_preview` | experiment, task, profile, datasets |  |  |  |
| `search` | query, type, limit |  |  |  |
| `set_baseline` | id | yes |  |  |
| `set_status` | id, status, reason | yes |  |  |
| `settings` | category, search |  |  |  |
| `settings_apply` | changes, scope, reason, confirm, remove, expected, pin |  |  |  |
| `settings_preview` | changes, scope, remove, expected |  |  |  |
| `setup_agents` | apply, strategy | yes |  |  |
| `show` | id |  | `research_show` |  |
| `site_add` | experiment, at, dry_run | yes |  |  |
| `site_init` | apply | yes |  |  |
| `site_remove` | experiment, apply | yes |  |  |
| `site_status` |  |  |  |  |
| `skill_install` | names, force | yes |  |  |
| `skill_show` | name |  | `skill_show` |  |
| `skill_toggle` | name, enabled | yes |  |  |
| `skills_add` | source, only, force, always, applies_to, list_only | yes |  |  |
| `skills_link` |  | yes |  |  |
| `skills_list` |  |  | `skills_list` |  |
| `skills_remove` | name | yes |  |  |
| `skills_set` | name, always, applies_to, level, checks | yes |  |  |
| `skills_update` | names | yes |  |  |
| `skills_upgrade` |  | yes |  |  |
| `strategy_use` | name, dry_run | yes |  |  |
| `supersede_finding` | id, by, reason | yes |  |  |
| `supervisor_daemon` | action | yes |  |  |
| `supervisor_investigate` | task, plan, question | yes |  | decide |
| `supervisor_status` | plan |  | `plan_status` |  |
| `supervisor_task` | task, action, note, reason, attempts | yes |  |  |
| `sync` |  | yes |  |  |
| `synthesize` | id | yes |  |  |
| `task_block` | id, reason | yes | `task_block` |  |
| `task_brief` | id |  |  |  |
| `task_context` | id |  |  |  |
| `task_create` | plan, title, goal, type, role, depends_on, acceptance_criteria, checks, skills, experiment, question, inputs, expected_outputs | yes | `task_create` |  |
| `task_done` | id, result, artifacts | yes | `task_done` |  |
| `task_next` | plan |  | `task_next` |  |
| `task_note` | id, text | yes | `task_note` |  |
| `task_pin` | id, level, profile, clear | yes |  |  |
| `task_start` | id, force | yes | `task_start` |  |
| `task_verify` | id | yes | `task_verify` |  |
| `telegram_clear` |  | yes |  |  |
| `telegram_set` | token, chat_id, discover | yes |  |  |
| `telegram_status` |  |  |  |  |
| `telegram_test` |  | yes |  |  |
| `tools` |  |  |  |  |
| `tree` |  |  |  |  |
| `update` | type, id | yes |  |  |
| `update_project` |  | yes |  |  |
| `verify_task` | id | yes |  |  |
| `workspace_archive` | id, dry_run, reason |  |  |  |
| `workspace_create` | experiment, name, base |  |  |  |
| `workspace_info` |  |  |  |  |
| `workspaces` | archived |  | `workspace_list` |  |
| `write_context` |  |  |  |  |
<!-- /generated:ops -->

## MCP server

`research mcp` serves the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0), standard library only.

- **Protocol:** `initialize` echoes a supported `protocolVersion` (`2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`; otherwise `2025-06-18`),
  `ping`, `tools/list`, `tools/call`; `resources/list` and `prompts/list` return empty lists; notifications get no reply; JSON-RPC batches are accepted.
  The server `instructions` summarise the protocol for the agent. When the environment names no agent, the client's `clientInfo.name` becomes the author.
- **Execution:** each tool is an op called with `surface="mcp"` through the same `api.call` as RPC, with stdout redirected to stderr (stdout belongs to
  the protocol). Results are text (JSON for structured data). A `ResearchError`, including a policy block, returns an `isError: true` result with the message,
  hint and exit code; bad arguments return `isError: true`; an unknown tool is a JSON-RPC error. Read-only tools carry `annotations.readOnlyHint`.
- **Install:** `research mcp install [--client claude|vscode|cursor|gemini|codex]` merges a `research` entry (`command: research, args: [mcp]`) into the
  client's project config (`.mcp.json`, `.vscode/mcp.json`, `.cursor/mcp.json`, `.gemini/settings.json`), keeping other servers;
  for Codex (per-user config) it returns `codex mcp add research -- research mcp`. When `research` is not on `PATH` the command is the absolute path of the
  project-local launcher `.research/bin/research`.
- Strategies, hooks, orchestration, usage and evidence are CLI and RPC only; MCP covers the core loop and the supervised-agent reporting tools.

<!-- generated:mcp -->
45 tools.

| Tool | Purpose | Mutates | Required arguments |
|---|---|---|---|
| `research_current` | Project state for agents: goal, checkpoint, beliefs, failures, experiments, active plans and their next tasks, attention items, skills, policy. |  |  |
| `research_search` | Keyword search over questions, experiments, runs, findings, decisions, plans, tasks, syntheses, notes and skills. |  | `query` |
| `research_show` | Full detail of any id (Q-, EXP-, RUN-, F-, D-, CP-, A-, PLAN-, T-, note:…). |  | `id` |
| `task_next` | The next ready task (todo, dependencies done, unclaimed), optionally within one plan. |  |  |
| `task_context` | Focused brief for one task: goal, acceptance criteria, checks, plan objective, dependency results, related experiment state, skills to load, progress notes, rules. |  | `id` |
| `agent_report` | Report on your supervised task: kind progress\|milestone\|artifact\|metric\|run\|hpc_job\|code\|problem\|report\|finish. | yes | `task`, `kind` |
| `agent_propose` | Propose a plan change as a YAML patch (same schema as plan YAML, scope partial). | yes | `task`, `yaml`, `rationale` |
| `decision_request` | Ask the researcher a decision with options. | yes | `question`, `options` |
| `plan_status` | Supervised status of a plan: task phases, attempts, budgets, pending proposals. |  | `plan` |
| `task_start` | Claim a task and mark it running. | yes | `id` |
| `task_note` | Append a progress note to a task (the hand-off trail for whoever continues). | yes | `id`, `text` |
| `task_done` | Complete a task. | yes | `id` |
| `task_block` | Mark a task blocked, with the reason. | yes | `id`, `reason` |
| `task_release` | Give a claimed task back (status todo) with an optional hand-off note. | yes | `id` |
| `task_verify` | Run a task's checks (commands, files, metric thresholds) and record the result. | yes | `id` |
| `task_create` | Add a task to a plan. | yes | `plan`, `title` |
| `plan_create` | Create a plan (a research objective that tasks break down). | yes | `title` |
| `question_create` | Create a research question. | yes | `title` |
| `experiment_create` | Register an experiment BEFORE running it (humans only; agents use experiment_propose): hypothesis, parameters (lists = sweep), metrics, planned runs, success criteria, stop conditions. | yes | `title` |
| `experiment_propose` | You may NOT create experiments. | yes | `title`, `hypothesis`, `rationale`, `method`, `expected_outputs`, `related` |
| `run_start` | Start a tracked run of an experiment in the background (returns the run id; poll run_status). | yes | `experiment`, `command` |
| `run_status` | Status, exit code, metrics and artifacts of a run (refreshes from the backend). |  | `id` |
| `run_logs` | Tail of a run's stdout or stderr. |  | `id` |
| `runs_compare` | Side by side: parameter and metric differences, code changes, artifacts of two runs. |  | `a`, `b` |
| `metric_log` | Log a metric on a run or an experiment. | yes | `name`, `value` |
| `artifact_register` | Register a file or folder (by reference, never copied) on a run or experiment. | yes | `path` |
| `experiment_synthesize` | Interpret an experiment's finished runs. | yes | `id` |
| `finding_create` | Record a reusable result with evidence ids (EXP-/RUN-/A-/F-). | yes | `title` |
| `finding_review` | Independent review of a finding: verdict supported \| contradicted \| needs_work, with reasoning. | yes | `id`, `verdict` |
| `checkpoint_create` | Snapshot the project's understanding (pre-filled from current state). | yes |  |
| `skills_list` | Project skills (name, description, always-on, applies_to). |  |  |
| `skill_show` | The full SKILL.md of a project skill. |  | `name` |
| `dispatch_prepare` | Prepare a brief for another agent role (planner\|implementer\|verifier\|analyst) on a target id or objective; returns the command a human can run. | yes |  |
| `report_full` | Deterministic, exhaustive Markdown export of everything recorded (optionally for one Q-/EXP-/PLAN-), with coverage verification. |  |  |
| `report` | Write the readable project report (goal, findings with evidence plots, failed directions, decisions, runs and syntheses per question) to .research/reports/<scope>.md and/or .html and return the paths. | yes |  |
| `guide_status` | Scientist Guide (Quarto book on main) state: important results and whether each has a draft, per-chapter freshness (fresh\|stale\|invalid), citations, the last draft's limit check, review/approval label per chapter. |  |  |
| `guide_highlights` | The important results (findings) that belong in the Scientist Guide, with numbers status and whether each is logged/drafted. |  |  |
| `guide_draft_check` | After writing a guide draft: did you stay within guide.draft.* (words per section, sections, figures) and out of human-owned chapters? Read-only; fix every problem it lists. |  |  |
| `guide_draft_done` | Record that the given important results (F- ids) now have a draft, then return the limit check. | yes |  |
| `guide_validate` | Validate the Scientist Guide's claim/figure provenance against the live records (missing, stale, contradicted, superseded, inconsistent). |  |  |
| `doctor` | Project health: records, shared ledger, workspaces, runs, agents, sandbox, context. |  |  |
| `workspace_list` | Experiment workspaces (git worktrees) sharing this project's one research ledger: branch, base revision, dirty state, linked tasks, active runs. |  |  |
| `hpc_doctor` | Whether remote execution is available on a configured cluster, and which modes are usable. |  |  |
| `hpc_sync` | Reconcile outstanding remote runs: adopt uncertain submissions, update Slurm status, import available results, and list experiments now needing interpretation. | yes |  |
| `hpc_bundle` | Build a portable execution bundle for an experiment, for manual execution on a cluster (no agent or connection needed there). | yes | `experiment` |
<!-- /generated:mcp -->

## Terminal UI (`research tui`)

A Textual dashboard over the same core (`pip install 'research-panel[tui]'`; without Textual it prints the install hint). It runs in process and calls
ops through `api.call(..., surface="tui")`. Tabs: **Overview**, **Runs**, **Findings**, **Plans & Approvals**, **Records**, **Doctor**. Keys: `1` to `6`
switch tab, `r` refresh, `/` filter tables (empty clears), `k` cancel the selected run, `a` / `d` approve or reject the selected proposal, `ctrl+t`
dark or light theme, `?` help, `q` quit. Protected actions ask for confirmation and, where the approval gate needs it, for the PIN. With `NO_COLOR` set or
`TERM=dumb` it falls back to plain output.

## VS Code extension

A thin client (`packages/vscode-extension/`): it renders what the core returns and sends user actions back as ops.

| Item | Value |
|---|---|
| id, engine | `research-local.research-panel`, VS Code `^1.85.0` |
| `extensionKind` | `["workspace"]`: the extension host, the RPC process, git, SQLite and artifact reads run on the machine that holds the files (the remote host under Remote SSH); the laptop only renders |
| activation | `workspaceContains:.research/config.yaml`, `onStartupFinished` |
| entry | `dist/extension.js` (esbuild bundle); no Python is bundled |
| settings | `research.cliPath`, `research.pollIntervalSeconds` (10), `research.defaultRunMode` (`terminal`), `research.projectRoot`, `research.openControlCenterOnStartup` (true) |
| core compatibility | `package.json` `researchCore: "3.0"` ([contract](../SPEC.md#version-compatibility-contract)) |

**Startup.** Choose the project root (`research.projectRoot`, else the first workspace folder with `.research/config.yaml` in it or a parent), find the
CLI (`research.cliPath`, then `PATH`, `~/.local/bin`, pipx), check the core version against `researchCore`, spawn `research rpc --root <root>`, then
refresh. A dead RPC process is restarted up to twice in quick succession. With a mismatch the status bar shows a warning and *Research: Show Version Info*
explains whether to run `research self-update` or install a newer VSIX.

**Structure.** `client.ts` (the RPC client and the `ready.ops` feature check), `model.ts` (refresh and coalescing), `trees.ts` (the Plans, Questions, Experiments,
Findings, Decisions, Checkpoints, Notes, Artifacts and Agent Context views), `panels.ts` (webview panels), `commands.ts`, `cli.ts` (locating and version-checking the
installed CLI), `render/` (HTML-string entity pages and forms), `webview/` (the React Control Center). The Control Center talks only to a `Host` interface;
the extension relays an allow-listed set of RPC methods (`CONTROL_RPC` in `panels.ts`) and refuses the rest. Aggregations are produced by the core
(`control.py`, ops `control`, `home`, `resume`), not by the UI. Settings pages are generated from the settings registry, so a new registered key appears
without UI work ([configuration](10-configuration.md)).

**Refresh.** A watcher on `.research/**` (ignoring `research.db*`, `cache/`, temp files and `events.jsonl`, debounced), window focus, every UI mutation
(`"changed": true`) and the Refresh command trigger a refresh. While runs are active the extension calls `run_refresh` every `pollIntervalSeconds`.

**Webview security.** Content-Security-Policy `default-src 'none'` with a per-page script nonce, no remote scripts or fonts, `localResourceRoots` limited to the
extension `media/` folder, the project root and the folders of external artifacts, all interpolated text HTML-escaped, and a design built only on VS Code theme variables.

**CLI and launcher.** The extension runs the user-installed `research` (it writes no shim). Shells that lack it on `PATH` (agent sandboxes, cron) use the project-local
launcher `.research/bin/research`, a short script that runs the CLI that wrote it (`python -m research_cli.main`) so agents run the same version as the user. `init`,
`research bin install`, `doctor` and `self-update` refresh it; it is git-ignored.

**Self-update.** `research self-update` downloads the newest GitHub release wheel, verifies it against `SHA256SUMS`, installs it with the interpreter that runs the CLI
(pipx and `uv tool` installs reinstall with `--force`), and leaves the existing install untouched on any failure. `--check` reports only, `--version X.Y.Z`, `--major`
(required for a breaking major) and `--from-file WHEEL` (offline). `research version [--json]` prints the core version and RPC protocol.

**Remote SSH and HPC.** Detached and SLURM runs keep running when the connection drops; status is rebuilt from files and SLURM accounting on the next connection. The
extension must be installed on the remote side once.
