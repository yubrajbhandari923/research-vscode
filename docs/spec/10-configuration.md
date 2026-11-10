# 10. Configuration

User guide: [CONFIGURATION.md](../CONFIGURATION.md). This chapter is the specification.

## Layers

Configuration is YAML. There is no second settings store: the CLI, the extension's Settings page, MCP and agents all read and write these files.

| Layer | File | Holds |
|---|---|---|
| default | `registry.py` | every default value; `registry.defaults()` builds `store.DEFAULT_CONFIG`; nothing else defines a default |
| machine | `~/.config/research-os/config.yaml` (`$XDG_CONFIG_HOME/research-os/...`, or the file named by `$RESEARCH_CONFIG`) | settings that belong to this computer: cluster hosts and paths, preferred agent CLI |
| project | `.research/config.yaml` | settings that travel with the repository |

Precedence is default, then machine, then project, key by key (`configfile.py`). The project file is sparse: it holds only what the user changed.
Sandbox profile assignment can additionally be set per experiment or task (`sandbox.assign.<experiment|task>.<id>`). The machine-local ledger link
(`<git dir>/research/ledger.json`), jobs, locks and run state are runtime state, not configuration.

**Secrets never live in config or in a project.** The approval PIN verifier (`approval.json`), the Telegram token and chat id (`telegram.json`) and
the known-projects list (`projects.json`) live in the machine directory (the directory of the machine config file), written atomically with mode 0600
through `machine`/`approval`, and are never put in the audit log, prompts, MCP output or git.

## Settings registry

`registry.OPTIONS` describes every user-facing key: type, allowed values, range, category, `help`, `why`, `effect`, `risk`, scopes, files affected and a
dependency. A test checks that each option exists in the defaults with the right type and has help text, and a frozen snapshot test
(`test_config_parity`) fails when a default moves.

| `risk` | Who may change it |
|---|---|
| `normal` | anyone |
| `policy` | a human only; an agent is refused, so an agent cannot loosen its own rules |
| `risky` | a human only, and only with explicit confirmation, because it can discard or expose work |

- `describe` returns effective value, default, source layer and inheritance; `preview` returns the exact YAML diff and writes nothing; `apply`
  validates, refuses `policy`/`risky` keys for agents, requires `confirm` for `risky`, edits only the affected lines (comments and unrelated keys survive),
  writes the machine file for `--scope machine`, records a `config` event with old value, new value and reason, and regenerates `.research/context/policy.md`.
  `reset` removes the override (and an emptied parent block).
- Writers edit line by line, write atomically, refuse to save when the file changed on disk since it was loaded, and never overwrite a file that does not parse.
- Keys without an option still work in YAML; they just have no form (clusters `hpc.clusters.*`, `agents.profiles.*`, `orchestration.strategies.*`,
  `harness.hooks.*`, `sandbox.profiles.*`). `research config check` reports unknown keys, wrong types, duplicate keys and commented-out edits.
- `policy.md` (generated with `current.md` and on every change) lists the effective settings by category with their help text; dispatch briefs point at it and
  record `settings_fingerprint` in the dispatch snapshot, so a later change does not alter a running session's recorded policy.
- CLI: `research config show [--effective|--sources]`, `options`, `explain KEY`, `set KEY VALUE [--scope machine] [--dry-run] [--confirm] [--reason]`, `reset KEY`, `check`.
  RPC: `settings`, `settings_preview`, `settings_apply`, `config`, `config_show`, `config_check`.

## Reference (generated)

<!-- generated:config -->
127 registered settings. Keys not listed here still work in YAML but have no form; run `research config options` for the full help text.

#### Project

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `project.name` | str | `''` |  | The name shown in the panel, reports and generated agent context. |
| `project.goal` | str | `''` |  | One or two sentences describing what the project is trying to learn. It opens every agent brief and checkpoint. |
| `project.default_backend` | enum | `'local'` |  | Where `research run` executes when you do not say: on this machine, or through SLURM. |

#### Research policy

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `agent_policy.max_runs_without_synthesis` | int | `5` | human only | How many finished, unsynthesized runs an agent may accumulate for one experiment. Beyond this limit further runs are blocked (exit code 3) until the results are synthesized or an override with a reason is used. |
| `agent_policy.max_failed_runs_without_review` | int | `3` | human only | How many failed runs may pile up on an experiment before agents must stop and have the failures reviewed. |
| `agent_policy.require_experiment_registration` | bool | `True` | human only | Every run must belong to a registered experiment (question, hypothesis, parameters) before it executes. |
| `agent_policy.require_synthesis_before_new_experiment` | bool | `True` | human only | Agents must synthesize a finished experiment before starting a new one on the same question. |
| `agent_policy.enforce_for_humans` | bool | `False` | human only | By default the run limits above bind agents only. Turn this on to make them bind you as well. |
| `coordination.stale_task_hours` | int | `4` |  | A running task that has had no progress note for this long is flagged stale. |

#### Verification

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `verification.require_review_for_supported` | bool | `True` | human only | A finding cannot be marked supported until an independent review approves it. |
| `verification.independent_reviewer` | bool | `True` | human only | The reviewer may not be the finding's author. |
| `verification.require_evidence_for_supported` | bool | `True` | human only | A supported finding must cite runs, metrics or artifacts. Numbers bound to records must verify. |
| `verification.checks_gate_task_completion` | bool | `True` | human only | `task done` runs the task's checks first; failures block agents from completing it. |
| `verification.check_timeout_seconds` | int | `600` |  | How long one task check may run before it is stopped and counted as failed. |

#### Agents & orchestration

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `agents.default_profile` | str | `'claude'` |  | Which agent profile (claude, codex, agy, opencode, gemini, or one you define) `research agent dispatch` starts when a role names none. |
| `orchestration.enabled` | bool | `False` |  | Route work to cheaper or stronger models by level (fast, balanced, deep) instead of one model for everything. |
| `orchestration.active_strategy` | str | `null` |  | Which named strategy maps task levels to models (for example balanced_first or deep_first). Empty uses the legacy tier routing. |
| `orchestration.limits.haiku_attempts` | int | `3` |  | How many times the fast model may retry a task before work escalates. |
| `orchestration.limits.sonnet_attempts` | int | `2` |  | How many times the balanced model may retry before escalating. |
| `orchestration.limits.escalations` | int | `2` |  | How many times a task may move up to a stronger model. |
| `agents.handoff.enabled` | bool | `False` | human only | When a provider is unavailable or hits its limit, continue the same task with the next provider in the fallback order. Off: the task pauses for you (you can still press Continue with another agent). |
| `agents.handoff.fallback_order` | list | `['claude', 'codex', 'agy', 'opencode']` |  | Providers to try, in order: claude, codex, agy, opencode. Each must have a profile with that provider and be installed. |
| `agents.handoff.on_provider_unavailable` | enum | `'pause'` |  | try_next continues with the next provider; pause stops the task for you. |
| `agents.handoff.on_unknown_execution_state` | enum | `'pause'` |  | Always pause: a task is never relaunched while its previous process might still be running. |
| `ask.profile` | str | `null` |  | The agent profile (claude, codex, agy, opencode or your own) that answers /ask and writes agent summaries. Empty uses the default profile. Only claude and codex can be restricted to read-only, so others are refused unless ask.allow_unenforced is on. In chat: /ai PROFILE MODEL. |
| `ask.model` | str | `null` |  | The model the profile above uses for these questions. Empty uses the profile's own default. |
| `ask.timeout_seconds` | int | `300` |  | How long the agent may work on one question or summary before it is stopped. |
| `ask.allow_unenforced` | bool | `False` | human only | agy, opencode and custom agents cannot be restricted to read-only from here; allowing them means only the written instruction stops them from changing files. |

#### Hooks & skills

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `harness.hooks_timeout_seconds` | int | `300` |  | How long an approved hook command may run before it is stopped. |
| `skills.packs` | list | `['core-research']` |  | Which skill packs are on. Core Research is always on; the others add skills for a field (Scientific Python, Machine Learning, Physics, Medical Imaging, Quantum, HPC, Frontend, Scientific Writing). Put it in the machine file to choose the default for every new project. |
| `skills.disabled` | list | `[]` |  | Skills that stay on disk but are not suggested to agents or linked into .claude/skills and .agents/skills. |
| `harness.require_seed` | bool | `False` |  | The experiment-config hook fails (instead of warning) when a run records no seed among its parameters. |
| `skills.link` | list | `['claude', 'agents']` |  | Which agent CLIs see the project's skills (`claude` → .claude/skills, `agents` → .agents/skills). |

#### Git & workspaces

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `layout.experiments_dir` | str | `null` |  | Where new experiment worktrees are created. Relative paths are under the project folder that holds main/ and ledger/. Empty keeps the legacy <name>-experiments folder beside the checkout. |
| `layout.main_dir` | str | `null` |  | Folder name of the main git checkout (informational; set by Initialize Project). |
| `layout.ledger_dir` | str | `null` |  | Folder name of the shared-ledger worktree (informational; set by Initialize Project). |
| `git.commit_checkpoints` | bool | `False` |  | Creating a checkpoint also commits `.research/` to git (never pushes, and only `.research/`). |
| `git.preflight` | enum | `'warn'` | human only | Before a plan starts or resumes, or promotion is authorized: warn = report uncommitted files and why they matter, then continue; block = refuse until you have reviewed and committed what matters; off = do not check. |
| `promotion.main_branch` | str | `'main'` | human only | The branch that promotions are prepared from and merged into. |
| `promotion.test_command` | str | `null` | human only | The command run in a promotion candidate (for example `pytest -q`). Its result is bound to the candidate's exact commit; without it nothing can be verified or approved. |
| `auto_promotion.numerical_command` | str | `null` | human only | Run after the promotion test command in an automatic-promotion candidate (for example `pytest -q -m oracle`). Optional; recorded as not configured when empty. |
| `auto_promotion.reviewer_profile` | str | `null` | human only | The agent profile that independently reviews automatic-promotion candidates. It must not be a profile that implemented the work (unless require_distinct_reviewer is off). |
| `auto_promotion.require_distinct_reviewer` | bool | `True` | human only | Refuse a reviewer profile that also implemented the work being promoted. |
| `promotion.require_supported_finding` | bool | `True` | human only | A promotion can be approved only when its experiment has at least one supported (reviewed) finding. |

#### Python environment

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `environment.python` | str | `null` |  | The Python used on this machine by agents, task checks and `{python}` in check commands: a venv folder or an interpreter path (relative paths are under the project folder). Empty uses whatever `python3` is on PATH. |
| `environment.packages` | list | `[]` |  | Packages that `research env check` verifies, written as on PyPI (for example numpy, PyYAML, scikit-learn>=1.4); each is checked by its import name (yaml, sklearn). |

#### Supervisor

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `supervisor.poll_seconds` | int | `5` |  | How often the supervisor looks for ready tasks, finished agents and HPC jobs. |
| `supervisor.max_parallel_agents` | int | `1` | human only | How many agents may work at the same time across a plan. Tasks of one experiment never share a worktree concurrently. |
| `supervisor.max_attempts_per_task` | int | `3` | human only | How many agent attempts one task may use (the first try plus repairs) before it is blocked for a human. |
| `supervisor.max_total_attempts` | int | `20` | human only | Total agent attempts a plan run may start. |
| `supervisor.max_wall_minutes` | int | `480` | human only | Minutes a plan may stay in the running state before the supervisor pauses it. |
| `supervisor.attempt_timeout_minutes` | int | `90` |  | A single agent process that runs longer than this is asked to stop. Computation on HPC is not counted. |
| `supervisor.on_failure` | enum | `'diagnose_and_repair'` | human only | diagnose_and_repair dispatches a bounded repair attempt; pause stops the plan for you to look; stop ends the run. |
| `supervisor.enforce_completion` | bool | `True` | human only | While a plan runs, only the supervisor's verification (or a human `supervisor accept`) can mark its tasks done, by any route: CLI, MCP, UI or a hand-edited file. |
| `supervisor.auto_apply_operational` | bool | `True` | human only | Agent proposals that only add new tasks (diagnostics, follow-ups) are applied without asking, up to the limit below. |
| `supervisor.usage_limit.on_limit` | enum | `'halt'` | human only | halt: stop the task for you (today's behaviour). wait: pause only the tasks that use that provider until it resets, then relaunch them by themselves. fallback_then_wait: first try the next provider in agents.handoff.fallback_order (only where strategy, roles and orchestration.available allow it), else wait. |
| `supervisor.usage_limit.wait_buffer_seconds` | int | `120` |  | Seconds added after the provider's stated reset time before the task is relaunched. |
| `supervisor.usage_limit.max_wait_hours` | float | `8` | human only | A reset further away than this (a weekly limit, say) is not waited out silently: the supervisor asks you (wait, switch provider, or stop). |
| `supervisor.usage_limit.unknown_reset_backoff_minutes` | list | `[15, 30, 60]` |  | Minutes between relaunches when the provider did not say when the limit ends; the last value repeats. A refused relaunch costs nothing. |
| `supervisor.usage_limit.short_backoff_minutes` | list | `[1, 2, 5]` |  | Minutes between relaunches after a short-lived rate-limit or overload error with no reset time; the last value repeats. |
| `supervisor.investigator.auto` | bool | `False` | human only | When a task hits a failure the supervisor cannot classify, run the investigator agent once (read-mostly, never approves). Off: it only runs when you ask (`research investigate`, or a free-text answer to a decision request). |
| `supervisor.investigator.max_per_plan` | int | `3` |  | Most investigator runs for one plan; after that the supervisor just asks you in plain text. |
| `supervisor.investigator.budget_usd` | float | `2.0` |  | Total provider-reported cost the investigator may use for this project; runs with no reported cost count toward the per-plan limit only. |
| `supervisor.max_auto_tasks` | int | `5` | human only | Most tasks agents may add to a plan through auto-applied proposals. |
| `supervisor.require_approval_for_scientific_changes` | bool | `True` | human only | Proposals that change hypotheses, methods, checks, acceptance, parameters or existing task definitions always wait for you. |
| `supervisor.session_resume` | enum | `'auto'` |  | auto continues the provider's native conversation for repairs when the installed CLI proves it can; never always starts fresh with a generated hand-off. |
| `supervisor.context_max_chars` | int | `24000` |  | Upper bound for the context the supervisor assembles for an agent attempt (project docs, objectives, prerequisites, history). |
| `supervisor.commit_verified_work` | bool | `True` | human only | When a task verifies, its changes are committed on the experiment's own branch (never main, never pushed) so they can be promoted and traced to the task. |
| `supervisor.commit_exclude` | list | `['outputs/', 'results/', 'patch*.yaml', '*.pyc', '__pycache__/', '.venv/']` |  | Folders (relative to the worktree) the supervisor never commits, such as bulky results. |
| `supervisor.commit_keep` | list | `['*.png', '*.svg', '*.pdf', '*.md', '*.qmd', '*.csv']` |  | File patterns that are still committed with verified work when they sit in an excluded folder (outputs/, results/) and are small, so figures and reports stay with the code that made them. |
| `supervisor.commit_max_file_mb` | int | `5` |  | Files bigger than this are left out of the experiment-branch commit and listed in the attempt record. |
| `supervisor.hpc_poll_seconds` | int | `30` |  | How often waiting computational runs are reconciled with the cluster. Unavailable status never causes a resubmission. |
| `supervisor.recovery.enabled` | bool | `True` | human only | Wait for a finished Slurm job's results to be collected before verifying, and re-verify a failed task unchanged when its missing results arrive. It never relaxes a check, changes the science, raises a budget or submits a job. |
| `supervisor.recovery.collection_wait_minutes` | int | `120` |  | How long a task whose Slurm job finished waits for its results to be collected and ingested before it is verified on what exists. |
| `supervisor.recovery.max_reverifications` | int | `5` |  | Most times one attempt is re-verified automatically because its evidence changed. |
| `supervisor.recovery.max_syncs` | int | `100` |  | Most cluster syncs the recovery step runs for one stopped task while it waits for results. |
| `supervisor.max_hpc_resubmissions` | int | `1` | human only | How many extra runs an agent may submit per task after failures before the task is blocked. |
| `supervisor_assistant.profile` | str | `null` | human only | The agent profile used for the read-only planning agent (interventions) and the debug agent (halts). Empty uses `claude`. |
| `supervisor_assistant.model` | str | `null` | human only | Model for the planning and debug agents. Empty uses the strongest Claude (claude-opus-5-5) when the profile is Claude, otherwise the profile's own model. |
| `intervention.require_approval` | bool | `False` |  | Ask for approval even when an intervention changes nothing protected (unblock, retry, guidance). Protected changes always need approval and the PIN. |
| `notifications.telegram.enabled` | bool | `False` |  | Message you on Telegram when a plan halts and answer simple commands. The bot token and chat id are stored on this machine only (Control Center → Plans → Chat, or `research chat setup`). |
| `notifications.discord.enabled` | bool | `False` |  | Alerts, slash commands and decision buttons in Discord, through the one chat gateway (needs the optional discord extra; `research chat setup discord`). The bot token is stored on this machine only. |
| `notifications.level` | enum | `'medium'` |  | How much the project pushes to your phone (Telegram and Discord): off = nothing automatic (commands still work); low = plan started/finished and failures; medium (default) = plus each experiment starting and finishing with the results it recorded, and anything waiting for you; high = plus every run and task. Change it from chat with /notify. |
| `notifications.summary` | enum | `'facts'` |  | facts = a template filled from recorded facts only (free, instant, no agent). agent = a read-only agent (ask.profile / ask.model) writes a short plain-language summary from the same facts; it is read-only, may cost money, and falls back to the facts if it fails. |
| `notifications.discord.mention` | bool | `True` |  | Mention the paired operators on Discord alerts that need a human (warnings and errors), so Discord sends a push notification even when the server only notifies for @mentions. |
| `notifications.discord.thread_per_task` | bool | `True` |  | Alerts about a task go to a thread named after it, so the conversation is the task's log; the channel gets a one-line pointer for errors and approvals. |
| `notifications.alerts.on` | list | `['halt', 'task_failed', 'approval_needed', 'promotion_ready', 'plan_finished', 'run_failed', 'budget', 'supervisor_down', 'decision', 'provider_limit', 'plan_started', 'experiment_started', 'experiment_finished', 'run_finished', 'task_started', 'task_verified', 'recovery']` |  | Which events reach chat: halt, task_failed, approval_needed, promotion_ready, plan_finished, run_failed, budget, supervisor_down, decision, provider_limit. |
| `notifications.alerts.cooldown_minutes` | int | `30` |  | The same alert is not repeated inside this time; after it, one reminder ('still: ...'). |
| `notifications.alerts.escalate_after` | int | `3` |  | After this many failures in a row for one task the alert suggests a next step (continue with another agent, pause the plan). |
| `notifications.alerts.approval_expiry_hours` | int | `24` |  | Buttons on approval cards disappear after this time. The proposal stays pending; nothing is approved or denied by waiting. |
| `notifications.digest.at` | str | `null` |  | Local time (HH:MM) for a daily summary of what changed. Empty turns the digest off. |
| `notifications.digest.silent_if_unchanged` | bool | `True` |  | Skip the digest when nothing happened since the last one. |
| `notifications.halt_grace_seconds` | int | `120` |  | How long a plan must stay halted before it is reported and diagnosed. |
| `notifications.auto_debug` | bool | `False` |  | Run the debug agent when a plan halts and send its summary and options (Telegram, if enabled, and Control Center). Off (the default) sends only the halt notice; you can still ask for a diagnosis any time. |

#### Governance & approval

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `governance.experiments.agent_creation` | enum | `'propose_only'` | human only | human_only: agents cannot even propose. propose_only: agents submit a proposal; you approve it with your PIN. Agents never create experiments directly. |
| `governance.tasks.agent_creation` | enum | `'bounded_auto'` | human only | human_only, propose_only, or bounded_auto (agents add operational tasks of the allowed types inside their own experiment, up to the limit). |
| `governance.tasks.allowed_auto_types` | list | `['bugfix', 'diagnostic', 'verification']` | human only | Under bounded_auto, agents may add tasks of these types themselves; any other type needs a proposal. |
| `governance.tasks.max_auto_per_experiment` | int | `3` | human only | Under bounded_auto, the most tasks agents may add to one experiment; further tasks need a proposal. |
| `governance.scientific_changes.require_human_approval` | bool | `True` | human only | Changes to hypotheses, methods, checks or acceptance always wait for you. |
| `governance.approval.require_pin` | bool | `True` | human only | Protected actions (approving a proposal, changing policy, promoting to main) ask for your PIN once one is set. |
| `governance.approval.require_rationale` | bool | `True` | human only | Approving a proposal needs a written reason, recorded in the audit trail. |
| `governance.approval.record_rejections` | bool | `True` |  | Rejected and revision-requested proposals are written to the audit trail. |
| `governance.approval.chat.enabled` | bool | `False` | human only | Lets an approver on a paired chat account approve some PIN-protected proposals by sending a 6-digit authenticator (TOTP) code. Also needs `research approval chat-enroll` on this machine. Frozen tests, scientific plan or hypothesis edits, governance/approval settings, the PIN/authenticator and the chat allow-list always stay computer-only. |
| `governance.approval.chat_allow` | list | `[]` | human only | Narrows what chat may approve: experiment_proposal, plan_add_tasks, budget_increase, retry, role_change, promotion, resume, decision_option. Empty means all of these (never the computer-only ones). |
| `governance.approval.confirm_human_experiment_creation` | bool | `False` |  | Ask for the PIN when you create an experiment yourself. Off by default: creating one interactively needs no PIN. |

#### Execution & sandbox

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `sandbox.enabled` | bool | `False` | human only | When on, jobs that name no profile run under the default sandbox profile. Off leaves jobs native unless a run, task, experiment or cluster names a profile. |
| `sandbox.default_profile` | str | `'native'` | human only | The profile used when sandboxing is enabled and a job names none (native, restricted, gpu-research, custom or your own). |
| `sandbox.approved_roots` | list | `[]` | human only | Directories that datasets and extra mounts must live under. Empty means any path that passes the safety checks. |
| `sandbox.agent.mode` | enum | `'none'` | human only | none, or advisory: writes the job boundaries into agent briefs. Neither mode restricts the agent's own shell; Research OS does not sandbox agents. |

#### HPC & SLURM

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `backends.slurm.partition` | str | `null` |  | The SLURM partition (queue) that runs submitted from this machine use unless one is named. |
| `backends.slurm.account` | str | `null` |  | The SLURM account that jobs submitted from this machine are charged to. |
| `backends.slurm.time` | str | `'01:00:00'` |  | The wall-time limit given to SLURM jobs unless a submission sets one, as HH:MM:SS. |
| `hpc.default_cluster` | str | `null` |  | Which configured cluster `research hpc` uses when none is named. |
| `hpc.bundle.max_total_bytes` | int | `268435456` |  | Largest source bundle that may be packaged for a cluster. |

#### Context

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `context.max_chars` | int | `12000` |  | Character budget for the generated agent context (`current.md`), about four characters per token. |
| `resume.stale_checkpoint_days` | int | `14` |  | A checkpoint older than this is flagged in the resume view. |

#### Scientist Guide

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `guide.enabled` | bool | `True` |  | Keep a Quarto book of the science on the main branch (docs/scientist-guide) and flag what needs writing. |
| `guide.highlights` | enum | `'supported'` |  | supported: findings an independent review marked supported. starred: only findings you star. all: every finding that is not superseded. |
| `guide.auto_log` | bool | `True` |  | When a finding becomes an important result, add a short entry (claim, evidence link, one plot) to the Key results chapter. No agent is involved and nothing is ever rewritten. |
| `guide.draft.trigger` | enum | `'result'` |  | manual: never prompt. result: Home asks as soon as an important result has no draft. checkpoint: only after you record a checkpoint, so drafts are batched. |
| `guide.draft.max_words` | int | `150` |  | Upper bound for one draft in one chapter or result entry. A short first draft with the main idea; you add the context. |
| `guide.draft.max_sections` | int | `3` |  | How many chapters or result entries one draft may change. |
| `guide.draft.max_figures` | int | `1` |  | How many figures one draft may add to a section. 0 means the agent writes text only. |
| `guide.publish.workflow` | bool | `True` |  | Keep .github/workflows/publish-guide.yml, which builds the guide's scientific edition and publishes it as a website on every push to main. |
| `guide.site.auto_include` | bool | `False` |  | When a finding becomes supported, publish every verified experiment that has committed report files under reports/ (and was not unpublished) as a draft page on the research website. Adds only; nothing is pushed. |
| `guide.model` | str | `'claude-opus-5-5'` |  | The model profile the guide draft dispatch asks for. |

#### Data & artifacts

| Key | Type | Default | Who | Meaning |
|---|---|---|---|---|
| `artifacts.hash_max_bytes` | int | `67108864` |  | Artifacts at or below this size are content-hashed when registered; larger ones record size only until pinned. |
<!-- /generated:config -->

## Environment variables

| Variable | Read or set by | Meaning |
|---|---|---|
| `RESEARCH_ROOT` | core, CLI | project root override |
| `RESEARCH_CONFIG` | core | path of the machine-level config file |
| `RESEARCH_AUTHOR` | core | human display name |
| `RESEARCH_AUTHOR_TYPE`, `RESEARCH_AGENT`, `RESEARCH_AGENT_MODEL` | core | mark the actor as `human`/`agent`/`system`, the agent name and its model |
| `RESEARCH_AGENT_ROLE` | set by dispatch | role suffix of the agent name (`claude/verifier`) |
| `RESEARCH_BRIEF` | set by dispatch | project-relative path of the session's brief |
| `RESEARCH_STRATEGY`, `RESEARCH_STRATEGY_FINGERPRINT`, `RESEARCH_AGENT_LEVEL` | set by strategy dispatch | the allocation the session started under |
| `RESEARCH_EXECUTION`, `RESEARCH_TASK`, `RESEARCH_ATTEMPT` | set by the supervisor for an agent | the execution, task and attempt; agent reports (`agent_report`) are refused without `RESEARCH_EXECUTION` |
| `RESEARCH_HOOK`, `RESEARCH_HOOK_STAGE`, `RESEARCH_TASK`, `RESEARCH_EXPERIMENT`, `RESEARCH_RUN` | set for hook commands | which hook runs, at which stage, for which task, experiment or run |
| `RESEARCH_PYTHON` | project-local launcher, hooks | interpreter for `.research/bin/research` and the built-in checks |
| `RESEARCH_PROMOTION` | set for the promotion test command | the promotion id |
| `RESEARCH_RUN_ID`, `RESEARCH_RUN_DIR`, `RESEARCH_IN_JOB` | set by the runner | enable in-job logging |
| `RESEARCH_MAX_ARTIFACT_BYTES` | HPC bundle job script | override `hpc.collect.max_artifact_bytes` on the cluster |
| `RESEARCH_QUARTO` | Scientist Guide | path of the `quarto` executable when `guide.quarto` is unset |
| `RESEARCH_TELEGRAM_API` | `notify` | base URL override for the Telegram API (proxy or test double) |
| `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_SANDBOX`, `CODEX_SANDBOX_NETWORK_DISABLED` | core | auto-detect an agent environment |
| `SLURM_JOB_ID`, `SLURM_JOB_NODELIST`, `SLURM_JOB_PARTITION` | runner | recorded in `status.json` |
| `CONDA_DEFAULT_ENV`, `VIRTUAL_ENV`, `USER` | core | stored in `run.env` |
| `NO_COLOR` | CLI, TUI | disable ANSI colour |
