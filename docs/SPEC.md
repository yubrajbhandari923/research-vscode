# Research OS: specification

**Project format 2 · index schema 3.** This is the reference for what Research OS does, why it is designed that way, and how it behaves. A competent engineer should be able to answer
"when does an experiment become `needs_review`?", "when is a task ready?" or "what happens if I delete `research.db`?" from it. To learn to *use* the system start with the
[README](../README.md) and the guides in this folder. Where another document disagrees with this specification, this one describes the built behaviour. Release history is in the
[CHANGELOG](../CHANGELOG.md).

## Purpose

Computational research produces many files and runs but few conclusions. After 40 jobs and a month away, the reasoning that links them is usually lost: why each run happened, which
mattered, what was concluded, what failed and should not be repeated, and where the evidence is. Research OS keeps that chain next to the code:

```
question -> experiment -> runs -> artifacts / metrics -> synthesis -> findings -> decisions -> checkpoint
                 ^
   plan -> tasks +   (optional operational layer: objectives broken into tracked work, run by people, agents or the supervisor)
```

Experiments are the scientific unit, a designed test. Plans and tasks are the operational unit: who does what, in which order, and how to tell it is done.

## Principles

| Principle | Consequence in the implementation |
|---|---|
| Raw activity is subordinate to understanding | Runs are collapsed under experiments everywhere; Research Home leads with focus, plan, findings and attention items, not run tables |
| Project-local and self-contained | All state lives in `<project>/.research/`; no global database, no server, no account |
| Text is canonical, SQLite is an index | Every fact can be rebuilt from Markdown, YAML and JSON files; `research.db` can be deleted at any time |
| Reference, never copy | Artifacts are stored as paths, sizes, mtimes and hashes; large data stays where it was produced |
| Provenance by default | Experiments and runs record git branch, commit and dirty state; dirty runs also store `git.diff` |
| Human and agent are always distinguishable | Every object carries `author_type` (`human`, `agent`, `system`), plus agent name and model |
| Agents are bounded and propose, humans approve | Run budgets, verification gates and an approval PIN stop agents from silently establishing truth |
| One core, thin clients | The CLI, TUI, extension, MCP server and supervisor call the same operations and write the same files |
| Domain-agnostic and portable | Metrics are arbitrary name and value pairs; stored paths are project-relative; a project can be moved, cloned or opened over SSH |

Not goals: a W&B or MLflow clone; a general workflow engine (the supervisor runs research plans with verification and approval gates, not arbitrary DAGs); a cloud service; a Jupyter or Obsidian
replacement; a chatbot; a data-versioning system; a container platform.

## Chapters

| Chapter | Covers |
|---|---|
| [1. Architecture](spec/01-architecture.md) | layers, modules, the op layer, process model, why a Python core |
| [2. Data model](spec/02-data-model.md) | identifiers, entities and fields, artifact types, relationships |
| [3. Storage and synchronisation](spec/03-storage-sync.md) | project layout, canonical files, mirrors, SQLite index, sync, rebuild, crash safety |
| [4. Lifecycles](spec/04-lifecycles.md) | state machines of questions, experiments, runs, findings, decisions, plans, tasks |
| [5. Agents and governance](spec/05-agents-governance.md) | authorship, policy, verification gates, coordination, providers, dispatch and strategies, skills, the managed `AGENTS.md` block, approvals |
| [6. Execution](spec/06-execution.md) | runner, local and SLURM backends, sweeps, HPC bundles, shared ledger and workspaces, job isolation |
| [7. Context and reports](spec/07-context-reports.md) | resume context, checkpoints, `current.md`, Research Home, task context, search, reports, full export |
| [8. Supervisor](spec/08-supervisor.md) | plan runtime, phases and health, YAML plans, promotion, intervention, notifications |
| [9. Interfaces](spec/09-interfaces.md) | CLI, Python API, RPC and the op layer, MCP, TUI, VS Code extension (with generated reference tables) |
| [10. Configuration](spec/10-configuration.md) | layers, settings registry, generated settings reference, environment variables |
| [11. Testing and release](spec/11-testing-release.md) | test suites, build, versioning, release checklist, repository layout |
| [12. Rationale and limitations](spec/12-rationale-limitations.md) | design decisions and what the system deliberately does not do |

User guides: [QUICKSTART](QUICKSTART.md), [SETUP](SETUP.md), [CONFIGURATION](CONFIGURATION.md), [GOVERNANCE](GOVERNANCE.md), [SUPERVISOR](SUPERVISOR.md), [PROVIDERS](PROVIDERS.md),
[AGENT_TOOLS](AGENT_TOOLS.md), [GUIDE](GUIDE.md).

## Version compatibility contract

| Party | Rule |
|---|---|
| Core version | SemVer. MAJOR: an op, CLI command or config key removed, or project data changed so older cores cannot read it. MINOR: new ops, keys or commands. PATCH: fixes. Hidden CLI aliases are removed only at a MAJOR. |
| Extension to core | `researchCore: "M.N"` in the extension manifest: the core's major must equal M and its minor must be at least N (`selfupdate.core_satisfies`). Checked at activation with `research version --json`; a mismatch shows a warning and the install hint. |
| Optional features | The RPC `ready` event lists `ops`. The extension hides a feature whose op is missing instead of failing, so a newer extension can run on an older core it accepts. `protocol` (currently 1) changes only if the framing changes. |
| TUI | Ships in the same wheel and runs in process: always matched. |
| Agents | `.research/bin/research` is a short launcher that execs the CLI that wrote it, refreshed by `init`, `doctor`, `research bin install` and `self-update`; no vendored copy, so agents run the version the user installed. MCP registrations point at `research` on `PATH` or at this launcher. |
| Daemons | The supervisor records its `version` in its heartbeat. RPC startup (for the opened project) and `research self-update` (for every project in `projects.json`) call `daemonlib.ensure_current`: a daemon running older code is restarted; agents and HPC jobs are untouched. |
| Project data | Schema migrations only move forward (`schema.MIGRATIONS`). A project written by a newer format or schema is refused, never modified. |
| Self-update | `research self-update` reinstalls the extras that are present (`tui`) so an update does not drop them. |

## Safety guarantees

1. **Never deletes or moves user data.** Artifacts are references; `rebuild` backs up the index first; removed skills are kept in `.research/cache/removed-skills/`. User commands, hooks and runs can modify files
   with the user's permissions.
2. **Never pushes.** Commits happen only in opt-in or supervised cases: `checkpoint create --commit` (or `git.commit_checkpoints`) commits `.research/` and nothing else; the supervisor commits an agent's verified work
   on that task's experiment branch; unattended promotion (two keys) fast-forwards local `main`. No force, rebase or push, ever. Managed writes outside `.research/` are the `AGENTS.md` block, skill links, generated
   agent files, the configured Scientist Guide and explicitly installed MCP entries.
3. **Never silently rewrites history.** Every mutation, including hand edits picked up by sync, is an event in `events.jsonl`; superseding is the only way to replace a finding or decision; missing mirror files are
   regenerated and logged.
4. **Import-before-export** protects detected hand edits; there is no lock against a concurrent external editor.
5. **Malformed records are errors, not silent skips.** Sync logs parse errors and keeps the indexed value; export refuses to overwrite a malformed file; rebuild aborts and rolls back on malformed records or duplicate IDs.
6. **Version safety:** an older tool refuses a newer project.
7. **Agents propose, humans approve.** Agents cannot create experiments (proposals only), approve proposals, change policy, promote to `main` or approve science; those need a human and the approval PIN. With no PIN a
   non-interactive approval fails closed. The supervisor acts through in-process grants and never impersonates a human. This is a guard rail for honest agents, not a boundary against a hostile process running as the
   same OS user ([GOVERNANCE.md](GOVERNANCE.md)).
8. **Findings need independent review.** An agent cannot make a finding `supported`; a verifier with a different identity must review it, and a failing bound number blocks `supported` for everyone.
9. **Secrets stay out of the project.** The PIN verifier, chat tokens and the known-projects list live in the machine config directory with mode 0600 and never reach git, events, prompts or MCP output.
10. **Atomic writes and crash recovery.** Managed text is replaced atomically (unique sibling, flush, fsync, rename); canonical-file transactions are journaled and rolled back on recovery. Neither is a guarantee on unreliable
    network filesystems.
