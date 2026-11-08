# 12. Design rationale and limitations

## Rationale

> The test the design is held to: after a month away, can you open the panel and understand in under two minutes what
> was tried, what was learned, what failed, and where to continue?

### Python core as the backend, thin VS Code client, TUI as a second UI

**Chosen:** one Python core (stdlib only, Python ≥ 3.8) that owns *all* business logic, a thin Python CLI on top of it,
a TypeScript extension that talks to the installed CLI over a long-lived stdio JSON-RPC process (`research rpc`), and a Textual TUI over the same core.

| | Python core + TS extension (chosen) | TS-only |
|---|---|---|
| Agents and user scripts calling `research.log_metric()` from Python | native | needs a second implementation, or a subprocess |
| One source of business logic | yes (the extension is UI only) | yes, but the Python API becomes a wrapper around a Node CLI |
| Runs on HPC login and compute nodes | `python3` is always there | Node is often missing on compute nodes |
| Extension complexity | +1 process boundary | simpler |
| SQLite | stdlib `sqlite3` | native module (`better-sqlite3`), with ABI and platform problems inside a VSIX used over Remote SSH |

The process boundary is kept cheap by installing the core once as a wheel (the VSIX stays small and cannot drift from the CLI agents use; versions are checked at startup, [VS Code extension](09-interfaces.md#vs-code-extension)), having zero third-party dependencies (PyYAML vendored as a fallback), and using one persistent RPC process
per window, so Python start-up is paid once.

### Text is canonical, SQLite is an index

SQLite binaries don't merge in git, and Markdown does. Making the text files canonical gives total reconstructability
(`rebuild`), human-editable records, readable diffs and inspectable branch merges (counter-ID conflicts require manual resolution). The index exists only for fast queries and
relationships. On HPC filesystems the DB uses `journal_mode=DELETE` (WAL needs shared memory, which is unsafe on NFS
and Lustre), and compute nodes write only to per-run files that the login node ingests later.

### Understanding over activity

Runs are deliberately subordinate. They are collapsed under experiments in every view. Research Home leads with focus,
plan, findings and attention items, not run tables. Syntheses are required (and budget-enforced for agents) before
more runs, and findings are never auto-generated from syntheses. A human or agent has to decide what is reusable.

### Bounded agents

Agents are good at running jobs and bad at stopping. The run budget ([Policy](05-agents-governance.md#policy-configyaml--agent_policy)), the synthesis-before-new-experiment rule
and authorship tracking give a human reviewer a bounded, attributable trail. The budget is a hard stop for agents, a
warning for humans, and always overridable with a logged reason.

### Plans and tasks as a separate layer

Experiments answer "what did we test and what did it show?". Plans and tasks answer "what work is being done, by whom,
in what order, and how do we know it is finished?". Keeping them separate stops operational steps ("implement the data
loader") from polluting the scientific record, while `related_experiment_id` / `related_question_id` keep the two layers
connected. *Ready* is derived rather than stored, so it can never go stale when a dependency changes.

### Gates instead of trust, dispatch instead of autonomy

- **Verification is a gate, not a suggestion.** Agents produce plausible claims quickly; a finding only becomes
  `supported` after someone else looked at the evidence. The gate is enforced like the run budget (exit code 3 with a
  hint), so an agent gets a clear, machine-readable "no" rather than a policy it can ignore.
- **Roles make one subscription usable as two reviewers.** Identity is `name/role`, so `claude/implementer` and
  `claude/verifier` are different reviewers. This is trust-based (an agent could fake its role), which is acceptable for
  a single researcher's project; humans remain the final authority and are never blocked.
- **Dispatch first, supervision on top.** Dispatch prepares a bounded brief and starts the agent CLI you already use,
  in a terminal you can watch. Swapping providers when one runs out of credit is a config change. The supervisor ([The supervisor](08-supervisor.md#the-supervisor)) adds unattended execution of a *reviewed plan*:
  it is deterministic code (no LLM), agents never launch agents, attempts and wall time are bounded, every outcome is verified, it stops and asks for scientific, resource and
  frozen-test changes, and it halts honestly instead of looping. Autonomous science (agents approving findings, creating experiments or promoting code unreviewed) is deliberately left out.
- **MCP in the standard library.** The protocol is small enough (newline-delimited JSON-RPC) that an SDK would add a
  dependency for little gain, and the core must stay installable on HPC nodes.
- **Skills are copied, not referenced.** An imported skill lives in `.research/skills/` and is committed with the
  project, so a clone has exactly the skills its history was produced with; provenance (`source`, `ref`) makes `update`
  possible.

### Deliberate simplifications

- **No Project table:** project metadata lives in `config.yaml`, where it is human-editable.
- **Syntheses are records, not mirrored entities.** They render inside the experiment file.
- **Notes are pure files**, with pin and link state in their own front-matter.
- **Bounded sweeps:** the CLI expands a Cartesian parameter grid and creates one run per point; this is not a general scheduling engine.
- **No diff capture beyond `git.diff`** for dirty runs (capped at 2 MiB).
- **Preview adapters** are a small TypeScript registry, and unknown binaries show metadata.
- **Plans are scheduled only by the supervisor,** a deterministic state machine; there is no general DAG engine.

### Requirements not in the original brief, and how they are handled

1. **SQLite on NFS/Lustre:** DELETE journal, busy timeout, compute nodes never write the DB.
2. **Concurrent compute-node writes:** per-run files, ingested later.
3. **Git merges and ID collisions across branches:** the DB is git-ignored and IDs are counters. Duplicate IDs stop sync/rebuild with a diagnostic; resolve IDs and references before retrying.
4. **Runs that outlive VS Code:** detached processes; status reconciled from files, not from a live process handle.
5. **Staleness:** the resume data reports what changed since the last checkpoint and flags old checkpoints.
6. **Baseline:** an explicit `is_baseline` flag on one experiment, plus the checkpoint's baseline text.
7. **Audit trail:** every mutation, including hand edits picked up by sync, is an event.

## Limitations

What the system deliberately does not do, and where its guarantees stop.

### Science and evidence

- **Evidence verification checks numbers, not science.** A verified binding proves a number matches a recorded metric (or a whitelisted calculation over metrics). It does not show the metric is the
  right one, the experiment is valid, or the interpretation is correct. Free-form prose is not parsed; only explicitly bound numbers are checked (`evidence suggest` lists unbound numbers but cannot bind them).
- **Claims are compared after rounding to the stated decimals** (or `--rel-tol`, capped at 5%): `12.8` matches a recorded 12.84; an integer claim such as `12` matches anything within 0.5.
- **Only runs and metrics recorded in Research OS are verifiable.** A number computed outside a registered run, or in an artifact whose contents are not parsed, can only be cross-checked by the artifact hash.
- **Artifact integrity needs a stored hash.** Files larger than `artifacts.hash_max_bytes` have no registration hash and report `unhashed` or `possibly_modified` (size only) until pinned with `research evidence pin`;
  directories are not content-hashed; unreachable external storage reports `unavailable`.
- **Run provenance is only as good as what was recorded.** Seeds and split identities are read from run `parameters` (keys containing seed, split, partition, fold, dataset). Missing metadata is reported as a
  limitation, never inferred. A clean git commit does not prove reproducibility.
- **Leakage protection is structural, not semantic.** Held-out test values are refused when the experiment has a `research tuning` plan with no frozen selection or recorded test; without that metadata the check can
  only warn, and it cannot detect leakage you did not declare. The numerical gate applies to humans too: `supported` is refused for anyone when a bound number fails.
- **Literature sources are offline records.** `research source` validates identifier format only; it never resolves a DOI or fetches a paper.
- **Scientist Guide prose is not machine-verified.** The guide status shows whether each finding's numbers verify; nothing checks that the narrative quotes them correctly, and draft limits are checked after the fact by
  comparing files. Writing needs an agent to actually run; `guide draft` only prepares it.

### Agents and orchestration

- **The finding gate and role identity trust the environment.** Roles and agent names are self-declared through environment variables, and a hand edit of a finding's Markdown is imported as a human edit, so a
  determined agent could bypass the gate. The gates, the approval PIN and the supervisor are guard rails for honest agents, not a boundary against a hostile process running as the same OS user.
- **Orchestration schedules only under the supervisor.** Without a running plan, dispatch is launched by people or sessions. A native subagent cannot spawn subagents, so tiered work runs as separate provider
  processes (`research orchestrate dispatch ... --run`). Plain `research agent dispatch` routes by strategy only when one is active. Shipped model names in the level packs are configuration defaults, not a guarantee
  that a provider currently offers them.
- **Usage is recorded only when the provider exposes it:** `--headless` runs record what the provider's JSON reports (parsed defensively); interactive sessions expose nothing. Cost is provider-reported with no price
  table, so it can be partial; summaries show `reported/records`. Headless Claude cannot answer permission prompts; put the flags you trust in the profile's headless arguments.
- **Hooks execute shell commands.** They run only after a human approves the exact command (hash of stage, command and timeout; agents get exit 3), with a timeout and the project root as working directory, but they are
  not sandboxed. `post_experiment` hooks run in `research experiment check`, not automatically when a run finishes, and there is no hook stage for HPC bundle import. Checks run arbitrary commands from the task
  definition, like `run exec`.
- **Per-task pins need an active strategy and apply to implementer dispatch only.** The file boundary in `orchestrate verify` compares `git status` against the baseline taken at dispatch, so it only sees a git
  repository and cannot attribute changes made by other processes in between.
- **Generated agent files follow the strategy only when `research agent setup --apply` is re-run;** a strategy change marks them `update`. Managed tier-mode files move to `.research/cache/removed-agents/`;
  user-owned files are never touched.
- **MCP covers the core loop only.** Strategies, orchestration, hooks, usage, tuning, evidence and sources are CLI and RPC only; MCP registrations written without `research` on `PATH` point at the absolute path of
  `.research/bin/research`, so re-run `research mcp install` if the project moves.
- **Skills:** updates keep a backup copy each time in `.research/cache/removed-skills/` (ignored by git); prune it by hand. Third-party skill text is not reviewed for scientific correctness.

### Supervisor, promotion and notifications

- **The supervisor is not a security boundary.** It and the approval PIN guard against honest mistakes and unsupervised agents.
- **Unattended promotion is local only:** a fast-forward into local `main`; it never pushes, rebases or forces, and pauses on any conflict, dirty or moved `main` or edited test.
- **Analyst and verifier tasks run in the main checkout;** only implementer tasks get a worktree, so an implementer's files on its branch are not visible to them until the work is integrated.
- **Frozen-test changes are approval-gated and recorded,** never edited or re-frozen automatically.
- **Live services are not exercised by the test suite:** real provider planning and debug agents and the real Telegram API are replaced by scripted agents and a local fake server.
- **Python 3.14:** `getpass` cannot read the PIN from `/dev/tty`, so the CLI reads the terminal directly with echo off. With no terminal (CI, agent shells) protected actions fail closed.

### Core

- **ID collisions across git branches (classic mode).** Counters are local to each `.research/`; two branches can create the same ID. Shared-ledger mode removes this. Sync and rebuild refuse duplicates; resolve each
  front-matter ID and its references (renaming a file alone is insufficient).
- **Hand edits to `run.json` and the JSONL files are not re-imported** (they are append-only records). Edit runs through `research run update`. A hand-edited `run.json` is read only when the run directory is unknown
  to the index (fresh clone or rebuild).
- **Plans and tasks are scheduled only by the supervisor:** updates reject dependency cycles; outside a supervised plan there is no automatic completion or move to `verify`, and starting a non-ready task only warns.
  Plans and tasks are not part of checkpoints.
- **Windows and WSL2 are unverified.** Nothing in the test suite runs on them. The SLURM backend, the runner's process-group handling and the sandbox runtimes assume POSIX.
- **SLURM** behaviour is tested against mocked `sbatch`, `squeue` and `sacct`. Site quirks (for example `sacct` disabled) degrade to the `unknown` state, never to a crash.
- **Preview formats:** NIfTI, HDF5, NetCDF and NPZ show metadata only.
- **Sandbox:** the `userns` runtime is kernel dependent and not a defence against a hostile program that also exploits the kernel; Apptainer is tested only with a fake executable and GPU passthrough is not tested; Docker and Podman are
  not implemented.
