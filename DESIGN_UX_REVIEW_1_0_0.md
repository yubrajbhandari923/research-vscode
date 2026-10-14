# Research OS — independent architecture, UX and workflow review

Review date: 2026-10-12. Reviewed: the current working tree on `main`, based on `b715c63`, including the existing, uncommitted stabilization changes. Software version 0.7.0; storage schema 3.

This is a design assessment and implementation proposal, not a release certification. The only repository file created for this review is this report. No recommendations were implemented. Existing source, configuration and documentation were left unchanged. Workflow mutations were confined to a newly created disposable project; no paid provider call, remote job, migration, deployment or third-party installation was performed.

Final scope check: content hashes of all 186 pre-existing tracked/untracked files captured at the start of this review were unchanged. All local links in this report resolved.

## 1. Executive assessment

**Research OS has an appropriate foundation, but its operational design does not yet reliably deliver the intended experience.** Keep the scientific record model, local text storage, rebuildable index, Python core, thin extension, and explicit separation between computation and scientific conclusions. A rewrite would discard useful work without addressing the principal problem.

The principal problem is that **the user or agent must know which implementation path supplies which guarantees**. A strategy can select one model while the normal UI flow explicitly overrides it. A delegation contract can exist without appearing in the advertised task context. A task can complete while the broader verifier reports a required hook is unapproved. Research Home can report no need for judgment while a task in another active plan is blocked. These are architectural and interaction problems even when individual functions behave as their tests expect.

The system currently works best as **research memory plus a collection of agent utilities**. It is less successful at translating an approved scientific objective into a coherent, recoverable workflow. The existing SPEC deliberately chooses “dispatch instead of autonomy” (§21.6), while the desired experience asks agents to continue bounded work and recover routine failures. That mismatch needs an explicit product decision: support bounded continuation by an already running agent session, while retaining human control over scientific scope, approvals and costly execution. It does not require a background scheduler.

The best next investment is a small set of common policies and projections, not more features:

1. One effective task contract and one completion policy, consumed by every supported interface.
2. One task-dispatch preparation path; strategy defaults visible and used unless deliberately overridden.
3. Project-wide exception visibility and honest distinctions among prepared, running, checked, reviewed and complete.
4. Accurate capability discovery and instructions, with focused context that avoids repeated reads.
5. A small human-facing workflow above the existing records: objective → proposed work → progress/exceptions → evidence review → decision/checkpoint.

**Design verdict: changes are essential before claiming the intended 1.0.0 experience.** The separate engineering review's unresolved durability and concurrency blockers also remain; this review neither fixes nor waives them.

## 2. Scope, method and evidence

### What was inspected

- [README](README.md), [SPEC](docs/SPEC.md), [engineering review](docs/RELEASE_1_0_0_REVIEW.md), and [verification record](docs/TEST_VERIFICATION_1_0_0.md).
- Core boundaries: `store`, `schema`, `configfile`, `plans`, `services`, `runs`, `context`, `views`, `agents`, `strategy`, `orchestrate`, `verification`, `harness`, `hooks`, `skills`, `setup_agents`, `guide`, `evidence`, `usage`, RPC/MCP, local/SLURM execution and HPC configuration/transport workflows.
- CLI parsing and dispatch; extension manifest, commands, model, panels, trees, Home/setup/detail/form renderers and webview event handling.
- Managed instructions, role prompts, generated-agent logic and first-party skills, particularly engineering, tuning, HPC and scientific handoff.
- Configuration, strategy, orchestration, interface, acceptance and extension-harness tests. Tests were used to identify the intended contracts and coverage boundaries, not as proof of usability.

Evidence labels used below:

- **Observed:** exercised against the current implementation in a disposable project or actual renderer/parser/protocol.
- **Traced:** followed through source and interface registration; no human interaction study or live provider execution implied.
- **Hypothesis:** a design judgment requiring validation with representative researchers.

The disposable project was created under the OS temporary directory, named `research-design-review-n3r88tz5`. It contains `journey.json`, protocol output, context measurements and rendered HTML. These are session-local evidence, not release artifacts. Core command reproductions are included below so the conclusions do not depend on keeping that directory.

### Executed review probes

| ID | Probe and evidence | Result and meaning |
|---|---|---|
| O1 | CLI initialization, configuration check, question/plan/experiment/task creation | Succeeded with no machine configuration. Fresh project had no active strategy. No provider was launched. |
| O2 | Set contract objective/constraint markers; request CLI context, RPC task context/brief, and MCP `task_context` | The constraint marker was absent from all these context paths. A normal dispatch brief also omitted it. Source includes contracts in the strategy-specific orchestration brief. |
| O3 | `strategy route T-001`; generic dispatch; generic dispatch with explicit `--profile claude`; equivalent RPC call used by UI | Default route selected `fast / claude-fast / claude-haiku-5-5`. Explicit `claude` changed the profile to `claude`, model to null, while the policy still named level `fast`. Model strings were read from configuration, not checked with a provider. |
| O4 | Same checked implementation task, no frozen tests: generic dispatch versus `orchestrate dispatch` | Generic preparation succeeded; orchestration preparation returned policy exit 3. Shared model selection is not shared dispatch policy. |
| O5 | Required unapproved `post_implementation` hook; orchestration verify; ordinary verify and completion as `design-probe` agent | Orchestration returned `passed:false`; ordinary task verify passed its file check; task done succeeded. The orchestration command itself returned exit 0 for the negative verification result. |
| O6 | Actual foreground toy run logging error 0.25; experiment check; synthesis; agent finding; different-agent review; decision; checkpoint; resume/current | Entire record flow succeeded. This verifies workflow mechanics only. After synthesis, `current` still printed “Needs synthesis before new experiments” with **0 unsynthesized runs**, because the experiment remained `needs_review`. |
| O7 | Two active plans, blocked task outside the selected plan; core Home aggregation and real Home HTML renderer | The other plan's blocker appeared in `current` but not Home's attention list. Home rendered “Nothing needs your judgment.” The fixture explicitly ordered plan timestamps to make the selection deterministic. |
| O8 | Actual MCP subprocess: initialize, initialized notification, tools/list, task_context | Exit 0; **33 tools**. Contract omission reproduced through the protocol. No decision-create, evidence, strategy or orchestration tools were registered. |
| O9 | Custom profile passed to `configfile.check` without saving anything | Legitimate `agents.profiles.lab-worker.command/model` appeared as unknown keys, though the overall result was `ok:true`. Extensible configuration is not described consistently by diagnostics. |
| O10 | Actual Home renderer with two synthetic temperature observations | Rendered “higher is better” and “best highlighted” without any declared scientific objective. This was a renderer fixture, not an experiment demonstrating that warmer is preferable. |
| O11 | Parse the shipped parameter-tuning skill's `research orchestrate tuning declare …` | Exit 2: `tuning` is not an orchestration action. The corresponding top-level `research tuning declare …` parsed successfully. |
| O12 | `hpc doctor` in an unconfigured project; `upgrade --dry-run` | HPC returned a useful configuration hint and contacted no cluster. Upgrade preview succeeded; even this new project reported a missing reports directory. No upgrade was applied. |

The initial renderer probe needed two corrections in the review harness: resolve esbuild from the extension's dependencies, and provide both observations and labels expected by the chart input. Those probe errors are not attributed to Research OS.

### Limits of this review

UI interaction findings are source traces and executions of the actual rendering functions, plus equivalent RPC calls. I did not conduct a new interactive VS Code, browser, screen-reader or theme study. Prior installed-VSIX evidence (73 checks) is in the verification record and is not relabelled as a new usability test. No live provider/model availability, HPC behavior or external-service compatibility was assessed.

The test process started before this read-only assignment completed successfully: **485 Python tests, TypeScript checking, and 3 Node tests**, with Python duration 234.64 seconds. This is supporting functional evidence from the previous verification task, not evidence that the design problems below are solved. No additional full suite was needed for this report-only change.

## 3. Architecture critique

### Keep the scientific and operational models separate

Question, experiment, run, synthesis, finding, decision and checkpoint answer different scientific questions. Plan and task answer different operational questions. Their number is not itself evidence of overengineering. Collapsing them into generic “work items” would erase distinctions the product exists to preserve.

The problem is the amount of record management exposed to the user. Let one guided review interaction produce several explicit linked records, with a preview of what will be written. Keep synthesis, finding support and decisions separately attributable. Never turn successful execution into scientific support automatically.

Preserve optional plans for research that does not need agent delegation. Do not require a question, plan, task and contract ceremony before every exploratory observation or manual run.

Sources: [schema](packages/core/research/schema.py), [services](packages/core/research/services.py), [plans](packages/core/research/plans.py), [context](packages/core/research/context.py).

### Keep profiles, allocation policy and project guidance as separate axes

These abstractions justify themselves when defined narrowly:

- **Profile:** how to invoke a provider/executable and which model to request.
- **Strategy:** which configured capability level handles a kind of work, and the bounded escalation policy.
- **Project guidance:** conventions, relevant skills, context and reusable validation.

Their independence allows changing models without rewriting scientific conventions. Keep that separation. However, three parallel vocabularies—roles, legacy vendor tiers and strategy levels—are too visible. The ordinary user should see the selected agent/model and why it was selected; the advanced view can show class, level, profile and override provenance.

The four named strategies are small data definitions; removing them saves little maintenance. Preserve their identifiers for compatibility, provide human-readable allocation descriptions, and present one recommended starting choice. Make the reduced scientific depth in `haiku_max` explicit when selected. “Available on PATH” must remain distinct from “model access verified.”

Sources: [strategy](packages/core/research/strategy.py) `select`, `requires_frozen`, `next_step`; [agents](packages/core/research/agents.py) `profile_for`, `dispatch`; [setup renderer](packages/vscode-extension/src/render/setup.ts).

### Merge policy application, not necessarily files or commands

`agents.dispatch` and `orchestrate.dispatch_task` are not interchangeable. The latter adds frozen-test requirements, strategy protocols, baseline tracking and contract rendering. The former is used by the UI, RPC and MCP. Shared routing hides this deeper difference (O2–O4).

Introduce one task-specific preparation service that resolves the effective task, checks applicable dispatch prerequisites, builds the brief and snapshots the resolved policy. Both public dispatch families can call it. Non-task planning and analysis dispatches can keep their role-specific branches. Existing CLI spellings can remain aliases/adapters.

Likewise, use one completion evaluator for checks, applicable approved hooks, frozen integrity, declared file boundaries and required review. Distinguish a check execution result from permission to complete. Ordinary tasks without executable checks need an explicit “completed, not automatically verified” interpretation; inventing a pass would be worse than the current simplicity.

Do not cache a passing check for arbitrary reuse: reuse requires an unchanged check definition, contract, relevant inputs and revision. A first implementation may rerun checks rather than introduce a fragile invalidation system.

Sources: [orchestrate](packages/core/research/orchestrate.py) `_dispatch_strategy`, `verify_work`; [verification](packages/core/research/verification.py) `verify_task`, `gate_task_completion`; [plans](packages/core/research/plans.py) `update_task`, `complete_task`.

### Resolve duplicated task intent before changing storage

Tasks already contain goal, inputs, expected outputs, acceptance criteria and verification text. Their sidecar contract repeats objective, context, expected outputs and verification, adding allowed files and constraints. The two representations can disagree, and `task_context` reads only one.

First introduce an **effective task specification**: a single read model with explicit field ownership, source attribution and conflict diagnostics. Include it in CLI/RPC/MCP context, all task briefs and the task page. Do not silently pick one of two contradictory objectives. Prefer the task's scientific objective as authoritative and use the execution contract for constraints and a dispatch-time snapshot; require an explicit reconciliation for legacy conflicts.

A later, separately tested migration can remove redundant stored fields. Moving everything immediately into a new table or entity is unnecessary. Keep old files readable and preserve historical snapshots.

### Preserve text storage; clarify the authority of each sidecar

Markdown records, run files, orchestration ledgers, verification JSONL, guide state, evidence bindings and usage logs have accumulated separate state models. Not all of this is duplication: evidence bindings and provider usage are different records. The cost comes from multiple interpretations of “attempt,” “dispatched,” “verified” and “done.”

Use the task record for task status; a dispatch/session record for invocation lifecycle; verification records for check/review evidence; derived summaries for Home. Avoid adding another queue/status database. Distinguish `prepared`, `started`, `exited` and `unknown` where process observation permits; do not claim terminal startup proves execution.

The engineering review's cross-file recovery and execution-ownership problems are prerequisites for greater autonomy. A design facade does not solve storage atomicity. Keep that work in the stabilization track.

### Retain both local/SLURM and portable remote execution

Executing on a workspace host and transferring a bundle to a separate cluster are materially different deployment modes. They deserve separate implementations. They do not need separate scientific concepts of a run.

Present “Where should this experiment run?” with configured locations, resolved resource requests, storage roots and approval requirements. Keep authentication in OpenSSH; keep compute nodes away from SQLite; retain uncertain-submission recovery and explicit resource confirmation. Share resource-resolution/display code where demonstrated duplication exists. Do not replace the bundle path with a remote service.

Sources: [SLURM backend](packages/core/research/backends/slurm.py), [cluster configuration](packages/core/research/hpc/config.py), [remote workflow](packages/core/research/hpc/remote.py), [HPC setup skill](packages/core/research/templates/skills/hpc-setup/SKILL.md).

## 4. Human-facing journey analysis

The manifest contributes **79 commands and 10 views**. The CLI has **42 top-level commands/groups**, excluding aliases. These are inventory counts, not a usability score. The issue is which choices a scientist must encounter to complete a common journey.

| Journey | Current behavior and friction | Proposed experience |
|---|---|---|
| Create a project | Initialization is short: name and scientific goal in UI. But model policy, provider readiness, agent-file setup and MCP registration are separate concepts. Fresh projects begin without the strategy described as preferred for new projects. **Observed/traced.** | Keep init short. Offer an optional “Use agents” setup that selects an installed profile/provider and previews the default allocation. Advanced harness/HPC setup remains optional. Do not infer credentials or install anything. |
| Resume after days | Home has a good scientific reading order, evidence links and checkpoint delta. Focus is the newest active plan; its task subset drives some attention items. A blocker in another active plan was absent (O7). **Observed.** | Separate “current focus” from project-wide exceptions. Show why an item needs action, who can act and one relevant next step. Allow focus selection without changing which blockers are visible. |
| Give an objective to agents | “Plan with agent” asks for objective, then profile; the planner prompt creates records and explicitly stops. Follow-on task dispatch is another interaction. **Traced.** | Show the proposed plan and boundaries for approval. Continue authorized tasks through an existing coordinator session. State clearly when no agent is running and what action starts it. |
| Dispatch a ready task | Targeted UI dispatch asks for role and profile, even with an active strategy. The profile becomes an explicit override (O3). **Observed/traced.** | One normal “Start” action with a compact resolved-model explanation. “Change agent/model” is an optional override, not a mandatory picker. |
| Follow work | Task state, run state, attempt ledger, dispatch snapshot, verification history and terminal process are separate. A prepared brief generates a `dispatched` event. UI reports dispatched after sending terminal text. **Traced.** | Show task outcome, last known execution state and next expected event. Keep detailed attempts/logs expandable. Display “prepared” until launch is observed; detached/disconnected uncertainty must remain visible. |
| Configure validation | Task checks execute; harness checks are instructions; hooks execute only on particular paths. Setup calls harness checks “Verification.” Stage labels imply more automatic behavior than the actual trigger paths. **Observed/traced.** | One validation explanation: task acceptance checks, reusable approved checks, and advisory instructions. Show the exact trigger and approval state. Put task verification on the task page and experiment validation on the experiment page. |
| Finish implementation | Ordinary verify, orchestration verify and done have different scopes (O5). Verify-then-done can rerun task checks. **Observed/traced.** | “Submit for verification” / “Complete” invoke the same evaluator and show what passed, what did not run and what still needs independent judgment. |
| Interpret experimental results | Toy run/check/synthesis worked, but a completed synthesis still appeared as needing synthesis with zero unsynthesized runs (O6). Plans require separate completion. **Observed.** | Distinguish “results need interpretation,” “interpretation recorded,” and “experiment needs a final disposition.” Suggest explicit closure; do not automatically close an inconclusive experiment or plan. |
| Review a finding | Findings have evidence links and independent-review commands. Numerical bindings are separate CLI operations; finding pages do not expose that status. A supported finding with no bindings is allowed (O6). **Observed/traced.** | Show two separate facts: scientific review status and numerical evidence coverage. Missing bindings are “not checked,” not verified. Offer contextual binding/inspection for quantitative claims. |
| Make a decision and close a session | CLI/UI support decision and checkpoint records; the MCP tool set omits decision creation. **Observed/traced.** | A review panel drafts linked finding/decision/checkpoint records for confirmation. Preserve individual records and allow CLI fallback where an interface is incomplete. |
| Handle failure | Error codes and hints are often useful; HPC-without-configuration is a good example (O12). All orchestration `environment`/`resource_limit` categories resolve to a human block, even though some environment issues are routine. **Traced.** | Distinguish a bounded, safe local repair from an authority request. Agents can inspect logs, locate an existing executable or retry a transient read; they cannot silently install code, increase resources or change scientific criteria. |
| Update/move a project | Dry-run, managed-file diffs and project CLI regeneration are sound. Users encounter separate tool version, schema, generated instructions, shim and MCP registration concerns. **Observed/traced.** | One readiness/status page explains each outdated component and its repair, with a concrete preview. Preserve existing backup and approval semantics. |

The new-task form exposes **13 fields in three visible groups** before edit-only fields, although only the title is required. Keep the structure for expert edits; default creation should emphasize intent, expected result and acceptance, with dependencies, assignment, links and execution details disclosed when relevant. This recommendation is a hypothesis to test with researchers, not a measured completion-time improvement.

Home already uses disclosure for completed tasks, routine attention and secondary actions. Retain that work. The setup page combines eight concerns: model policy, work in flight, harness/hooks, experiment validation, usage, skills, MCP and generated agent files. Move scientific work and validation into their record pages; keep setup focused on reusable configuration. Do not build a replacement dashboard framework.

## 5. Agent-facing experience

### What works

Structured records, stable IDs, explicit exit-code hints, task claims, progress notes, focused dependency results, search/show, copied skills and run provenance are good agent affordances. The CLI is usable without a cloud service. Routing is deterministic and configurable. Optional third-party skills are not installed merely by selecting a strategy.

### Where agents must guess or repeat work

1. **The advertised task context is incomplete.** It omits sidecar contract constraints (O2). Agents should not need to discover an extra ledger or remember a special dispatch command to recover their obligations after interruption.
2. **Instructions overstate interface coverage.** The managed block says every listed tool is available through MCP; the actual registry has 33 tools and excludes several named workflows. The top-level instruction should explain the CLI fallback. Do not attempt blanket parity with all 42 CLI groups.
3. **Shipped instructions can select nonexistent commands.** The tuning skill uses `research orchestrate tuning …`; the implemented family is `research tuning …` (O11). The existing managed-block command test does not establish correctness of all skill examples.
4. **Provider/tier language leaks into policy.** The engineering-loop skill says Haiku must load `ponytail` and only Opus updates the guide, while strategies allow other level/profile mappings and optional skill installation. The runtime orchestration prompt has a missing-skill fallback; the static skill does not express the same rule consistently.
5. **Completion and escalation require procedural memory.** A worker must know whether to use ordinary verify, orchestration verify, record an attempt, invoke another model, mark verify, or mark done. `next_step` returns advice; it does not enact the handoff. This should be explicit, and the continuation result should carry the exact next supported action.
6. **Project tools are only partly discoverable.** Setup can list third-party MCP registrations, but that proves configuration presence, not accessibility in the current agent session. Report the distinction. Do not invent a second plugin/tool registry.
7. **Project customization is supported, but permissions are scattered.** Agents can create local skills and propose hooks. Hook approval remains human-owned. A task brief should distinguish permitted project-local edits, proposals awaiting approval, and changes to Research OS infrastructure that are out of scope.

Sources: [managed instructions](packages/core/research/templates/AGENTS.block.md), [engineering loop](packages/core/research/templates/skills/engineering-loop/SKILL.md), [tuning skill](packages/core/research/templates/skills/parameter-tuning/SKILL.md), [skills matching](packages/core/research/skills.py), [MCP registry](packages/core/research/mcp.py).

### Context and token measurements

On the small disposable project after the toy workflow:

| Text | Characters |
|---|---:|
| Generated managed `AGENTS.md` | 6,729 |
| `research current` | 1,794 |
| Task brief | 1,072 |
| Normal task dispatch brief | 4,324 |
| All 33 MCP tool descriptions/schemas serialized by the probe | 11,925 |

The dispatch brief contained the current-context text and task brief information, while startup instructions also direct the agent to read current/task context. Reading all four would consume about **13,919 characters**, with roughly **2,866 characters** of avoidable repeated current/task context. Dividing by four gives only a rough token estimate; no provider tokenizer or client prompt-caching behavior was measured. MCP clients may load schemas selectively, so 11,925 characters is not a claimed per-turn cost.

Shorten the always-read block to the protocol, safety boundaries and retrieval map. Move optional HPC and specialist procedures into skills that are already present. A dispatch brief should identify the context and specification it includes, plus when to refresh them, rather than instructing an immediate reread. Never trim acceptance criteria, constraints or required approvals to satisfy a token budget.

`context.max_chars` controls list reduction in `current`, not the full session payload; it is not a hard total-context guarantee. Keep full report exports for explicit archival/handoff needs. The default interrupted-task recovery should be focused context and recent progress, not a complete project dump.

## 6. Configuration and customization audit

The existing hierarchy is good: **built-in defaults ← machine configuration ← project overrides**, with command-specific overrides. Sparse, comment-preserving editing and `config show --sources` are worth keeping. The experience around that hierarchy needs improvement.

| Concern | Actual ownership/precedence | Assessment and proposal |
|---|---|---|
| Project identity and scientific intent | Project configuration; machine `project` values discarded | Correct. Keep project identity local and explicit. |
| Provider executable/model profiles | Defaults, machine and project; explicit dispatch flags win | Keep the layers. Show the effective model and source. Machine executable paths may reasonably differ; record the resolved invocation in provenance. |
| Allocation strategy | Active name plus merged strategy definitions and inheritance | Useful advanced capability. Default picker should describe allocation behavior; show inheritance and field origins on demand. |
| Role mapping, legacy tiers, active strategy | Multiple alternative resolvers; explicit profile can bypass strategy/pins | Retain compatibility through an internal adapter, not parallel user-facing setup flows. Clarify what `orchestration.enabled` controls; selecting a strategy works even without that flag. |
| Task pins | Sidecar ledger; implementer-only; below explicit dispatch overrides | Keep exceptional pins visible on the task. Normal UI dispatch should not unintentionally override them. |
| Harness instructions/context/skills | Project references; all are carried into general dispatch guidance | Keep references. Merge/deduplicate task/plan/harness skill suggestions with their reason, and expose missing files before launch. |
| Validation | Task checks, harness checks, approved hooks, verification policy | Distinct execution and guidance concepts are currently named too similarly. Provide one effective validation plan with executable versus informational labels. |
| Hook authorization | Command/stage/timeout digest under `harness.hooks_approved` | Keep explicit approval. Treat approval metadata as managed operational state, not an ordinary preference. UI should allow approving selected hooks; current action approves the whole unapproved set after one modal. Do not strengthen authority or move approvals silently. |
| Local SLURM versus remote cluster profiles | `backends.slurm` and `hpc.clusters.<name>.slurm`, then flags | These deployment modes justify separate configuration. A shared resolved resource display would reduce confusion without a disruptive migration. |
| Editor settings | Six `research.*` settings for interpreter, root, terminal/shim, polling and run mode | Appropriate separation for editor behavior. Do not duplicate scientific or model policy in VS Code settings. |
| Generated instructions | Managed AGENTS block, copied skills, provider agent files | They are projections with different refresh paths. A strategy change leaves provider files stale until setup is rerun. Show freshness and a managed diff near the change. |
| Validation/schema | Entity schema, defaults and specialized validators; no standalone comprehensive config schema found | General checking infers some types from defaults and has special cases for extensible maps. Valid custom profiles can be labelled unknown (O9). Use a small explicit field/extension map shared by diagnostics and focused editors; avoid a schema framework unless needed. |

Do not auto-detect and execute a repository's arbitrary test commands. Detect candidate commands for a proposal, show their working directory, timeout and scope, then use the existing approval mechanism when execution is being authorized. Do not infer cluster storage roots, provider credentials, model capability or scientific acceptance thresholds.

## 7. Feature integration matrix

**Yes** means an explicit implemented path; **partial** describes the stated subset. It does not mean live-provider verification. UI entries are source-traced unless marked observed above. The matrix deliberately distinguishes RPC from CLI: statements that all optional modules are “CLI/RPC” are too broad.

| Capability | Core | CLI | MCP | RPC | VS Code / instructions | Integration judgment |
|---|---|---|---|---|---|---|
| Init, goal, resume/current | Yes | Yes | Current; no init | Init/home/resume | Home, init, context guidance | Good core experience; Home/current attention semantics differ |
| Questions/experiments/runs | Yes | Broad | Create, local start/status/logs, metrics/artifacts | Broad local/SLURM operations | Forms, details, run actions; skills | Strong local path; remote lifecycle remains specialized |
| Synthesis/findings/review | Yes | Yes | Yes | Yes | Forms/review, evidence links | Useful separation; numerical verification not visible in finding UI |
| Decisions/checkpoints | Yes | Yes | Checkpoint; no decision-create | Generic create for both | Both available | MCP-only scientific loop cannot record its decision directly |
| Task creation/claims/progress | Yes | Yes | Yes | Yes | Forms/actions/brief | Good basic coordination; not an execution lease |
| Task checks/completion | Yes | Yes | Ordinary verify/done | Ordinary verify/complete | Ordinary checks/complete | Not integrated with full orchestration completion policy |
| Structured contract/frozen tests | Yes, sidecar | Orchestrate commands | No edit/freeze tools; context omits contract | Setup returns contract data; no full contract/freeze API | Setup shows counts/file count; task form edits separate fields | Incomplete at the point of execution/recovery |
| Strategy/pins | Yes | List/show/use/validate/route/pin | No management; dispatch indirectly uses active policy | Setup/use/pin | Setup and pin commands | Selection implemented; normal UI dispatch supplies an overriding profile |
| Dispatch | Generic and orchestrated | Both, with different launch defaults | Generic prepare only | Generic prepare | Opens terminal through generic prepare | Shared routing, divergent safeguards/briefs; prepared versus launched blurred |
| Retry/escalation/delegation | Yes | Orchestrate attempts/delegate/status | No | Setup summaries, not full operations | Protocol guidance; no continuation controller | Advisory next-step policy, not an automated scheduler |
| Harness/skills/hooks | Yes | Broad skill/hook controls; harness YAML | Skills list/show; no hook or harness management | Setup, skill operations, hook approve/toggle/run | Setup, import/create, approval; YAML for advanced references | Useful but executable/informational and stage distinctions need clarity |
| Experiment validation | Yes | `experiment check` | No dedicated tool | `experiment_check` | Setup Check action | Human/agent validation belongs with experiment, not only agent setup |
| Numerical evidence/literature/tuning | Yes | Dedicated families | No | No dedicated handlers | No dedicated controls; skills/CLI | Specialized CLI is acceptable; finding evidence status must be surfaced |
| Scientist Guide | Yes | Init/status/refresh/done | No | No dedicated guide operations | File/skill access; Home may flag pending | Optional narrative; prepared refresh is not execution or verified prose |
| Usage | Provider-reported ledger | Headless/import/summary | No | Setup summary | Setup usage table | Honest missing-value handling; not a universal agent cost monitor |
| Remote HPC | Yes | Config/doctor/submit/sync/bundle/import/datasets | Doctor/sync/bundle subset | Status/sync subset | Opt-in sync/status, terminal/config paths | Keep specialized controls; do not imply full UI/MCP parity |
| Upgrade/recovery | Yes | Preview/apply/rebuild/bin | Upgrade preview | Upgrade/rebuild/managed-file operations | Preview/config/update commands | Useful capabilities, fragmented readiness and recovery presentation |

Sources: [CLI parser](packages/cli/research_cli/main.py), [MCP tool registry](packages/core/research/mcp.py), [RPC handlers](packages/core/research/rpc.py), [extension commands](packages/vscode-extension/src/commands.ts), [manifest](packages/vscode-extension/package.json).

## 8. Alternatives and recommended implementation approaches

Effort and risk are relative engineering judgments, not delivery commitments. “Small” means localized changes; “medium” means coordinated core/interface/test changes; “large” means new lifecycle or migration work. Every recommendation below is proposed, not implemented.

| Decision | Alternative A | Alternative B | Recommendation and tradeoff |
|---|---|---|---|
| Task versus contract duplication | Immediately migrate all contract data into task records | Add one effective specification reader, conflict diagnostics and shared projection first | **B.** Immediate user benefit with smaller migration risk. Remove redundant storage later only when historical cases are understood. A reduces long-term duplication but is too disruptive as the first step. |
| Divergent dispatch/completion | Add separate fixes to every CLI/RPC/UI path | Make public paths call a common task preparation/completion service | **B.** More initial integration work, fewer policy discrepancies and less future testing duplication. Keep old commands as adapters. |
| Strategy choice | Ask for profile on every dispatch | Use resolved policy by default; offer explicit advanced override | **B.** Fewer choices and correct pin behavior. Preview model/profile/reason so defaults remain inspectable. |
| Legacy routing | Remove it and force config migration | Resolve legacy configuration into the same internal routing result | **B** for 1.0.0. Preserves projects while removing duplicated behavior. Future retirement needs usage evidence and a migration path. |
| Agent autonomy | Add an always-running multi-agent scheduler | Let an already authorized coordinator session continue bounded work using common services | **B**, after reliability prerequisites. Less process/state complexity, offline-friendly and easier to stop. It does not promise execution while no agent is running. |
| Tool discoverability | Generate an MCP tool for every CLI action | Add concise capability discovery, honest fallback instructions, and selected missing lifecycle tools | **B.** Smaller schema/context cost and lower authority surface. Decision creation and effective task context are more valuable than exhaustive administrative parity. |
| Configuration editor | Build a general form for the entire config tree | Focused model/location/check controls plus effective-value provenance and YAML escape hatch | **B.** Less maintenance and fewer misleading controls. Use shared field metadata for validation where practical. |
| Scientific review UX | Merge synthesis, finding and decision into one record | One guided interaction with separate previewed linked records | **B.** Reduces navigation while preserving meaning and provenance. Keep manual individual actions available. |
| Home focus | Make every active plan a full dashboard column | One selected focus plus project-wide exceptions and a plan switcher | **B.** Keeps the page readable without hiding operational problems. |
| Performance/context | Add persistent context caches or a vector store | Measure payload/query costs, reuse projections within a request, avoid duplicate prompt content | **B.** Lower correctness risk and no new dependency. Persistent caching requires a demonstrated bottleneck. |
| Metric direction | Continue inferring “better” from metric names | Neutral display unless direction/target is explicitly declared | **B.** A small change removes unsupported scientific interpretation. Optional direction metadata can be added compatibly later. |

### Essential before 1.0.0

| ID | Improvement and concrete approach | User impact | Effort / regression risk | Maintenance and acceptance evidence |
|---|---|---|---|---|
| E1 | **Unify effective task contract and completion policy.** Keep existing storage initially; expose constraints and file boundaries everywhere. Route public completion/status transitions through one evaluator. Report deterministic pass separately from independent review and manual completion. | Critical: agents know their obligations; “done” has a consistent meaning | Medium / high | Fewer competing policies. Cross-interface scenarios must return identical obligations and completion decisions; required unapproved hooks must not disappear from a completion result. Test contradictory legacy fields without discarding either value. |
| E2 | **Use one task preparation path and preserve strategy defaults.** Add a default route choice to UI, make override optional, apply frozen prerequisites and contract rendering across generic/orchestrated CLI, RPC and MCP. Show exact profile/model and override source. | Critical: users can rely on chosen allocation and delegation boundaries | Medium / medium-high | Less duplicate dispatch code. Test UI-equivalent request, direct CLI and MCP with the same task/pin; compare both routing and safeguards, not just the model string. |
| E3 | **Make state and attention honest.** Aggregate blockers across plans; separate focus from exceptions. Separate synthesis due from awaiting disposition. Describe dispatch preparation accurately and use nonzero CLI status for failed verification, with compatibility notes. | High: prevents false reassurance and unnecessary work | Medium / medium | Shared summaries reduce drift. Test two active plans, zero-unsynthesized `needs_review`, disconnected execution, failed verify and no-agent-running state. |
| E4 | **Correct agent capability and procedure contracts.** Fix tuning examples, MCP parity claim, vendor-tier absolutes and unconditional optional-skill requirements. Publish a compact supported-capability/fallback map. | High: avoids predictable failed calls and unnecessary human assistance | Small-medium / low | Validate all shipped skill command examples and important workflows, not just the AGENTS block. Generated provider files must reference the effective strategy or be explicitly marked stale. |
| E5 | **Remove unsupported scientific cues.** Default charts to neutral direction. Show numerical status/coverage alongside finding review status; explicitly distinguish `no_bindings` from verified. | High: protects scientific interpretation | Small-medium / low-medium | Test unknown metric direction, target-valued metrics, supported-but-unbound findings, partial bindings and failed bindings. Do not reject all prose containing digits or claim automatic scientific validity. |

E1/E2 depend on the engineering stabilization work for safe concurrent mutations and launch ownership. Greater automation must wait until those blockers are closed. Honest labels and instruction fixes can proceed independently.

### High-value improvements worth considering for 1.0.0

| ID | Improvement and implementation approach | User impact | Effort / risk | Maintenance/validation |
|---|---|---|---|---|
| H1 | **Focused onboarding and configuration provenance.** Goal first; optional provider/allocation setup; executable detection without paid probing; effective values and source; custom-profile diagnostics; selected-hook approval. | High | Medium / medium | Reuse config resolution; no second settings store. Verify two projects with different machine/project overrides and a relocated project. |
| H2 | **Reduce normal task-form and setup complexity.** Intent/result/acceptance first; advanced execution details disclosed. Move validation and task contract inspection to task/experiment pages. | High | Medium / low-medium | Reuse render components. Researcher walkthrough must find next action without opening config or orchestration help. Preserve expert fields. |
| H3 | **Focused agent context.** One effective task brief with included-context provenance; shorter always-read instructions; deduplicated task/plan/harness skills; bounded optional detail. | Medium-high | Medium / medium | Measure characters and retrieval calls on small and large fixtures. Assert no constraints, approvals or required checks disappear when context is reduced. |
| H4 | **A common research review action.** Summarize runs, validation and uncertainty; draft synthesis/findings/decision/checkpoint with explicit separate saves. Add selected missing lifecycle MCP capability, especially decision creation. | High | Medium / medium | Reuse existing record operations; test partial save/recovery and preserve review independence. Avoid all-or-nothing multi-record claims without storage support. |
| H5 | **Unified readiness and recovery presentation.** Explain missing executable, stale generated files, moved registration, pending approval and recoverable run states with targeted actions. | Medium-high | Medium / medium | Derive status from existing sources; do not add another readiness ledger. Never repair scientific records or remote paths automatically. |

### Future improvements

- **F1 — Bounded coordinator continuation:** an opt-in, session-scoped loop through ready work, checks and bounded repair/escalation. Large effort and high risk until leases, interruption recovery and common gates are dependable. Persist the reason for stopping; do not introduce a daemon merely to make the UI say “autonomous.”
- **F2 — Provenance-bound verification reuse:** avoid repeated expensive checks only when exact checked inputs, definitions and revision can be identified. Medium/high risk; rerunning remains the safe default.
- **F3 — Larger-project optimization:** instrument Home/RPC payload size, scan/query counts, refresh latency and context growth before optimizing. Batch related reads within a refresh; retain a simple rebuildable index.
- **F4 — Usability and accessibility validation:** keyboard-only navigation, focus after live refresh, screen-reader announcements, theme/contrast checks, constrained window sizes and researcher task studies. Source includes useful labels, keyboard delegation and disclosure-state restoration; these do not establish accessibility in practice.
- **F5 — Simplified canonical task storage:** migrate redundant contract fields only after the effective-specification approach resolves precedence and historical conflicts. Medium/large effort, high migration risk, potentially lower long-term maintenance.

### Features or abstractions worth removing or retiring

These are specific retirement candidates, not authorization to delete compatibility or user data:

1. Parallel legacy and strategy policy implementations: retain old inputs through an adapter, then remove duplicated internal decisions after parity tests.
2. Mandatory profile selection on routine UI dispatch. Keep optional overrides.
3. Vendor names as universal roles in project-independent instructions. Keep provider profiles and legacy aliases.
4. Duplicate authoritative objective/output/verification fields. Reconcile first; retain historical snapshots and compatibility reads.
5. “Verification” labels for informational harness commands. Preserve the guidance capability under an accurate name.
6. Unconditional references to optional third-party skills. Keep approved import and explicit selection.
7. Task/experiment operational summaries duplicated in Agent setup when their record pages can own them.
8. Historical implementation/fix narratives inside the normative SPEC. Preserve them in historical docs/changelog; make the specification describe current contracts.

Do **not** remove the four strategy presets, task aliases, portable bundles, scientific record types, full reports, optional guide, or expert YAML merely because their origin is older development. Each has a distinct use or compatibility value.

## 9. Proposed sequence and target experience

### Implementation sequence after approval

1. Agree on the effective-task and completion semantics, including manual/unverified completion, independent review and legacy conflicts. Write cross-interface acceptance scenarios before editing individual frontends.
2. Finish storage/concurrency prerequisites in the stabilization track. Build the shared task projections and policy services; retain public command compatibility.
3. Connect normal dispatch/completion paths, correct Home attention and scientific labels, and repair agent instructions. These are the essential design release gates.
4. Add focused setup, context deduplication and review affordances as time permits. Measure task success and choices required rather than counting new controls.
5. Re-run release verification against the resulting artifacts. Pilot with researchers on an ordinary local experiment and a configured HPC workflow; live integration remains a separate verification task.

### Simplest practical user workflow

1. **Start:** open a project and describe what needs to be understood. If agents are wanted, choose a provider once and inspect the allocation recommendation. No harness design is required to begin.
2. **Plan:** discuss scientific assumptions with the reasoning model. Research OS presents proposed experiments, acceptance criteria, permitted work and stopping boundaries. The human changes or approves the scientific direction.
3. **Continue:** start the next authorized task. The selected model and reason are visible, with overrides available. The agent receives one complete specification, relevant skills and current evidence. It can perform safe local work, run approved validation and record progress without asking for each administrative step.
4. **Handle exceptions:** Home shows project-wide blockers, approval requests, disputed findings and execution uncertainty. Routine diagnostics stay with the agent. Changed scientific criteria, new external capabilities, resource reservations and ambiguous scientific decisions return to the human.
5. **Review:** completed runs appear with execution outcome, deterministic checks, numerical coverage and provisional interpretation clearly separated. An independent review evaluates support; the researcher chooses the resulting decision.
6. **Close/resume:** checkpoint the understanding and unresolved question. On return, see what changed, what is established versus provisional, what needs judgment and what authorized action comes next.

Most infrastructure should be invisible on a normal day, but never hidden at the point where it changes authority, cost, scientific meaning or recoverability.

## 10. Explicit non-recommendations

- No cloud control plane, always-on agent fleet, generic workflow engine, message bus or distributed queue.
- No replacement of text canonical storage with an authoritative database; no UUID migration solely for architectural neatness.
- No conversion of scientific records into generic tickets, and no automatic promotion of synthesis or passing tests into supported findings.
- No LLM-based routing classifier when explicit task classes and deterministic rules suffice.
- No unrestricted “run arbitrary CLI” MCP tool to claim parity. Expose deliberate capabilities and honest fallback paths.
- No automatic third-party skill/plugin installation, hook approval, credential discovery, permission expansion, HPC resource increase or remote resubmission.
- No claim that role names, frozen hashes or declared allowed files form a sandbox or authenticated review boundary.
- No new frontend framework to reorganize the current pages. Existing pure renderers and shared form components are sufficient for the proposed changes.
- No vector database, persistent prompt cache or event-sourcing rewrite without demonstrated retrieval, performance or recovery needs.
- No mandatory full-history context dump or Scientist Guide refresh for every engineering task.
- No blanket removal of old commands, config keys or historical files. Compatibility adapters can reduce internal complexity while preserving supported projects.

## Appendix: compact reproductions and evaluation criteria

Run only in a new disposable project with machine configuration isolated. The dispatch probes below use `--json`, which prepares commands without launching a provider. They describe observed current behavior, not recommended scientific procedure.

```bash
research init --goal "Inspect workflow behavior" --json
research plan create "Review fixture" --json
research task create PLAN-001 "Fixture task" \
  --acceptance "Expected file exists" --check 'file:result.txt' --json
# Create a small result.txt fixture in the disposable project.
research orchestrate contract T-001 --constraint CONTRACT-CONSTRAINT-MARKER --json
research task context T-001
research strategy use sonnet_first --json
research strategy route T-001 --json
research dispatch implementer T-001 --json
research dispatch implementer T-001 --profile claude --json
research orchestrate dispatch T-001 --json
research hook add review-check --stage post_implementation --command true --json
research orchestrate verify T-001 --json
research task done T-001 --agent design-probe --json
```

Expected current observations: context omits the constraint; the explicit profile changes the resolved model; only orchestrated dispatch refuses missing frozen tests; full verification reports the unapproved hook while ordinary completion succeeds. The fixture check proves file presence, not numerical correctness.

For O7, create two active plans, make one task blocked with a distinctive reason, and deterministically select the other plan by its creation timestamp. Compare `context.research_home(p)["attention_items"]`, `context.render_current_md(p)` and `renderHome` output. This tests visibility independently of actual agent execution.

For O10, pass two labelled temperature observations to the existing `renderHome` result-chart input. The implementation's name regex selects “higher is better”; no scientific direction field is consulted.

Success criteria for the proposed changes:

- A researcher can identify current intent, project-wide exceptions and the next authorized action from Home without opening orchestration/configuration documentation.
- A task recovered through CLI, RPC or MCP carries the same constraints and completion obligations as its dispatch brief.
- The same task and overrides resolve to the same model **and safeguards** across supported entry points.
- Completion distinguishes executed checks, skipped checks, independent review and manual disposition.
- No UI implies numerical verification, scientific support, successful launch or background continuation without recorded evidence.
- Agents can discover unsupported interface operations and the exact fallback without guessing; all first-party procedure examples match the parser and meaningful workflow tests.
- Project-local skills and proposed checks can evolve without modifying Research OS infrastructure or weakening approval boundaries.

The architecture can reach this target through focused consolidation. Its scientific foundations should remain; the competing operational paths and misleading projections should not.

## Implementation dispositions (1.0.0 work)

Status values: **implemented** (code and a test), **modified** (done differently than proposed), **deferred**, **rejected**.
Evidence for each "implemented" row is in `tests/python/test_release_1_0.py`, `test_command_examples.py` and the existing suites.

| Recommendation | Disposition | Rationale |
|---|---|---|
| Unified effective task specification | **implemented** (`task_spec.py`) | Read-only projection over task fields and the orchestration contract; storage unchanged. Contradictions are listed with their sources and block dispatch and completion until reconciled. Present in `task_context`, `task_detail`, dispatch briefs and resume/MCP briefs. |
| Shared preparation for plain and orchestrated dispatch | **implemented** (`orchestrate.preparation`, called from `agents.dispatch`) | Plain `research dispatch`, orchestrated dispatch, RPC (VS Code) and MCP now share the specification, frozen-test, dependency and preflight checks. Behaviour change: plain dispatch under a strategy that requires frozen tests now refuses without them (or `allow_unfrozen`, logged). |
| Strategy governs normal execution; VS Code must not override it | **implemented** | The Start action calls `dispatch` with no profile; an optional "Advanced model override" passes an explicit profile. The notice shows model, strategy and reason. |
| Exclusive launch ownership | **implemented** (`execution.py`, `durability.lock`) | Prepared commands are single-use wrappers that take a non-blocking per-task kernel lock held by the provider process, so a killed wrapper does not release it while the provider lives. Tested with real processes. Limits: advisory locks; a provider that daemonizes away from the inherited descriptor escapes it; network filesystems with broken `flock` are unsupported. |
| Crash-consistent mutation | **modified** | Instead of a write-ahead log or database, each mutation runs under a project lock with an undo journal (fsynced before each canonical write); recovery rolls back an uncommitted transaction and rebuilds the index. Kill tests cover mid-transaction and after-SQLite-commit. Not covered: raw edits outside the protocol, power loss on filesystems that ignore fsync. |
| One verification and completion policy | **implemented** | `verify_task`, `task done`, status updates and create-as-done all call `orchestrate.verify_work`. States: passed / blocked / unverified; no verifier is never a pass. Agents are blocked; humans are warned and an audit event (`completion_override` / `administrative_completion`) is written. CLI `verify` exits non-zero on failure. |
| Distinguish "implemented / checked / reviewed / scientifically supported" | **modified** | The verification result carries executed/passed counts, state and an explicit note that findings need separate independent review. Findings show numerical evidence coverage in a card separate from the review list. A dedicated "manual completion" field was not added: the audit event is the record. |
| Home: project-wide exceptions separate from focus | **implemented** | `blocked_tasks` now spans every active plan; Home renders a "Needs attention (project-wide)" section. |
| Home: next authorized action, human vs routine | **modified** | The Start button is on the next-task row and project-wide blockers are marked as needing human judgment. A full human-vs-agent queue was not built. |
| Execution states | **modified** | Core records prepared/launched/running/failed/completed/unknown per execution. The VS Code message now says the command was sent and execution is unconfirmed; the panel does not yet read the execution record, so it cannot show "running". |
| Neutral metric display | **implemented** | Charts no longer infer direction from metric names and do not highlight a "best" point unless a direction is declared. |
| Agent-facing instruction fixes | **implemented** | Managed block and MCP instructions state exactly what MCP omits and to use the CLI; wrong examples fixed (`research tuning`, `run status`, `run submit`); a parser test validates every shipped example. |
| Custom provider profile validation | **implemented** | `agents.profiles.<name>.*` keys are no longer reported as unknown. |
| Configuration provenance | **deferred (already present)** | `research config show --sources` already exists; no new UI. |
| Bounded coordinator continuation | **deferred** | Not implemented and not claimed. Smallest safe design: a session-scoped `research orchestrate continue` that loops claim → dispatch → verify only while `verify_work` passes, the attempt limit and budget hold, and no task has an unapproved hook, changed acceptance criteria or unresolved conflict; every other state exits with the reason. It depends on the ownership lock above, which now exists. Until then dispatch, retry and escalation decisions remain manual. |
| Retire legacy tier routing | **rejected** | Legacy `orchestration.tiers` remain supported; `route()` already delegates to one strategy resolver when a strategy is active. |
| Frontend framework | **rejected** | Existing string renderers were extended. |
