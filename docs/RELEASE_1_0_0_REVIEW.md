# Independent review for Research OS 1.0.0

Review date: 2026-10-12 (America/New_York). Baseline: `main`, `b715c63`.
The initial working tree and untracked-file report were clean. No pre-existing changes were
removed. No research directory was deleted, no external release was published, and no tag was pushed.

> **Superseded by the final implementation pass (see `TEST_VERIFICATION_1_0_0.md`, last section, and `DESIGN_UX_REVIEW_1_0_0.md` dispositions).** The three P1 blockers below (crash consistency, execution ownership, completion integrity) were addressed and covered by multi-process and kill tests; the software is now a 1.0.0 release candidate (not published). The text below is the original 0.7.0 assessment, kept as history.

**Release verdict: NOT READY.** These changes are a substantial stabilization pass, not a
certification that the entire requested production review is complete. Software remains **0.7.0**;
schema remains **3**. Passing tests and installable artifacts do not close the blockers below.

## Initial architecture and assessment

The repository is a Python core/CLI and a TypeScript VS Code extension. It already implements most
of the intended scientific and operational workflows. The baseline contained 432 Python tests,
not merely documentation claiming that count. The default shell Python was 3.14.7 without pytest;
the repository environment provided Python 3.10.18. Node was 22.23.2, npm 10.9.8, uv 0.8.19.
The root, Python and extension package versions were 0.7.0; `uv.lock` still described the project
as 0.1.0. Runtime metadata unnecessarily required setuptools despite the standalone core design.

| Boundary | Implementation and state ownership |
|---|---|
| VS Code → Python | `src/extension.ts`, `model.ts`, `client.ts`, `panels.ts`, renderers and webview assets; persistent line-delimited RPC child, tree cache, watchers and polling |
| CLI / MCP / RPC → core | `research_cli/main.py`, `mcp.py`, `rpc.py`; argument/protocol handling and dispatch to shared services; MCP exposes a subset |
| Canonical storage → index | `store.py`, `schema.py`, `mirrors.py`, `yamlio.py`; Markdown, run directories, JSON/JSONL; rebuildable SQLite with rollback journal and explicit write transactions |
| Scientific lifecycle | `services.py`, `runs.py`, `evidence.py`, `context.py`, `views.py`, `guide.py`, reports; explicit synthesis, finding/review and checkpoint steps |
| Operational lifecycle | `plans.py`, `agents.py`, `strategy.py`, `orchestrate.py`, `verification.py`; task claims/checks, provider profiles, strategy resolution, contracts, frozen hashes, attempts and dispatch briefs |
| Project harness | `harness.py`, `hooks.py`, `skills.py`, `setup_agents.py`, templates; referenced context, managed instructions, approved shell hooks and imported skills |
| Execution | `runner.py`, `backends/`, `hpc/`; subprocesses, per-run files, remote transfers and result ingestion; compute nodes do not write SQLite |
| Installation/upgrades | `localbin.py`, `upgrade.py`, `configfile.py`, build scripts and manifests; generated per-machine CLI, sparse configuration, backed-up managed updates |

Coupling is concentrated in the store/services/runs/views relationships, managed through local
imports. CLI `main.py` and several rendering/report modules remain large. The review preserved the
architecture instead of attempting a broad rewrite. Default configuration and strategy merging,
subprocess checks, recovery behavior and protocol boundaries offered concrete consolidation targets.
Historical architecture documents describe proposals and are now labelled accordingly.

Canonical state is not limited to indexed tables: orchestration, evidence, usage, guide, hook and
skill records also live in text files. Losing an index must not be used as a reason to remove them.
Generated bundled Python, extension JavaScript, codicons and package artifacts are not authoritative
source. Existing legacy routing and user-visible commands remain supported.

## Significant findings and implemented changes

| Severity | Finding | Change and evidence |
|---|---|---|
| P0 | Export could overwrite a malformed hand-edited mirror after import failed | Preserve mirror mapping and bytes; export/update refuses the malformed input. Regression asserts exact bytes and indexed value |
| P1 | Rebuild silently omitted malformed files; duplicate-ID event was suppressed during replay | Rebuild raises on malformed mirrors/runs/complete JSONL and duplicate IDs; rollback retains the prior index. Sync also refuses duplicate IDs |
| P1 | Rebuild moved the live database, leaving other connections attached to the old inode | SQLite backup into a unique file, then rebuild the same live DB in a transaction; canonical files are not re-exported |
| P1 | Canonical export failure could leave an entity/run mutation committed only to SQLite | Export entities and run metadata before their index transaction commits; failure-injection tests verify rollback. This does **not** solve all multi-file crash windows |
| P1 | JSONL corruption disappeared silently; partial tails could be joined to a subsequent record | Streaming complete-record parsing with explicit errors, POSIX append locking, flush/fsync, and refusal to append over incomplete tails |
| P1 | Re-freezing changed tests silently replaced their approved hashes | Re-freezing changed hashes is refused; corrections retain the explicit correction path |
| P1 | Malformed task ledgers silently reset frozen tests and attempt history | Fail closed with a repair diagnostic; preserve the source file |
| P1 | `task update --status done` and generic `mark` bypassed `task done` checks | Central public update gate; verification validates frozen integrity before executing checks |
| P1 | Task claim read and write were separate operations | One SQLite write transaction around the claim; two independent connections are tested for a single winning owner |
| P1 | Required unapproved hooks could leave a gate successful; advisory failures behaved differently across paths | Required approval blocks the gate, disabled hooks remain skipped, advisory failures do not block an otherwise verified result; no verifier is not a pass |
| P1 | Preflight hooks only applied on one orchestration dispatch path | Shared agent dispatcher applies task preflight to plain and routed preparation |
| P1 | Command-check timeout killed a shell without reliably stopping descendants | Shared process-group cleanup for hooks/checks/headless agents; hook/check output spooled rather than accumulated in memory |
| P1 | Headless timeout could produce CLI exit zero | Timeout is failure (124), missing executable becomes a controlled ResearchError |
| P1 | Experiment validation accepted a completed label with missing/nonzero exit code | Require a recorded zero exit code; no runs or blocked required hooks cannot produce all-passed |
| P1 | JSON scalar/list messages could terminate RPC/MCP servers | Request-shape validation and continued handling of subsequent requests, including malformed author parameters |
| P1 | Late exit from an old RPC child could clear the replacement child and reject new requests | Process-identity isolation, per-process UTF-8 decoder/buffer, startup timeout, stdin failure handling; Node lifecycle regressions |
| P2 | Dependency cycles were accepted | Updates reject self and indirect cycles before mutation |
| P2 | Refresh completion could re-arm polling after model disposal | Disposal flag and event-emitter cleanup |
| P2 | Mutable/obsolete implementation duplication | Shared deep merge; shared hook/check subprocess runner; removed unused config migration helper, config header, mirror constant, and redundant JSONL expression/offset variable |
| P2 | Malformed skill manifest could be treated as empty and overwritten | Report parse/shape failure instead of discarding provenance |
| P2 | Local subprocess handles leaked; cancellation could ignore stored remote host | Close runner stderr handle; close log files on startup failure; consult recorded host before cancellation |
| P1 | Demo script unconditionally deleted its destination | Refuse an existing destination |
| P2 | Extension test harness claimed isolation but used the supplied project directly | Copy workspace to the test temp directory; rebuild its index; use argument arrays for shim tests; support an installed VS Code executable |
| P2 | Clean-install check could inherit development paths/configuration; a VSIX scan failure could be masked | Isolate imports/config, offline dependency-free wheel installation, explicit scan failure |
| P2 | README/SPEC claimed no known bugs and contradicted implemented orchestration/rebuild | Rewrite README, correct recovery/trust/verification behavior in SPEC, label historical proposals, add this evidence record |

New regression coverage lives in `tests/python/test_release_safety.py` and
`packages/vscode-extension/test/client.test.cjs`. Existing configuration, strategy, orchestration,
HPC bundle, scientific-evidence, migration and integration suites remain in place.

## Remaining release blockers

These are unresolved engineering issues, not external-environment excuses:

1. **P1 — Crash consistency across canonical files and audit/index state.** Individual replacement
   and append durability improved, but there is no transaction spanning Markdown/run JSON, JSONL,
   related entities and SQLite. For example, `store.insert_entity` appends an audit event before
   canonical export; an injected export failure rolls back the index but leaves the event in the
   canonical log. Larger operations can publish only part of their state. A recovery protocol and
   kill/failure tests across these boundaries remain required. Directory fsync and network-filesystem
   semantics have not been established either.
2. **P1 — Concurrent orchestration and duplicate execution.** Task claims now serialize different
   actors, but dispatch preparation/launch is not an exclusive execution lease. Identical actor
   labels can reclaim a task; generic dispatch and terminal launches do not reserve it. Orchestration
   JSON ledgers still use read/modify/replace without concurrency control, so simultaneous attempts,
   pins or dispatches can lose updates. See `agents.dispatch`, CLI launch paths and
   `orchestrate.ledger/_save`. Concurrent budget checks also precede run creation. A common launch
   ownership/recovery mechanism and multi-process regressions remain required.
3. **P1 — Full completion-contract integration remains incomplete.** Frozen hashes and declared task
   checks gate public updates, but `task done` does not automatically run the entire
   `orchestrate verify` contract/file-boundary/post-implementation pipeline. Creating a task already
   in `done`, or changing its check definition, is not an immutable approval mechanism. Roles/files
   are explicitly trusted, but ordinary supported entry points still need a single documented
   completion policy. The working-tree file boundary also cannot reliably attribute edits to files
   that were already dirty at dispatch.
4. **P2 release gate — Review and platform breadth.** This pass traced the major implementation
   boundaries and added targeted regressions; it did not independently exhaust every command,
   renderer, scientific transition, malformed configuration shape, interrupted migration and
   portability case requested in the assignment. The local Python 3.8/Windows, live provider,
   browser UI and real cluster gaps below remain. The existing CI matrix is configuration, not
   evidence of a run performed in this session.

Corrupt SQLite recovery still requires a stopped-client backup and moving the unreadable index
aside; the CLI opens the project before `rebuild`. Automatic destructive repair was deliberately
not added. Cross-branch sequential IDs remain; conflicts are now explicit and non-destructive,
not automatically renumbered. Resolve the canonical IDs and all references together.

## Security and scientific integrity

Research OS is **not sandboxed**. Agent names/roles and human authorship are self-declared. A process
with write access can edit canonical files, configuration and approval state. Task checks, approved
hooks, agent CLIs and experiment commands execute with the user's permissions. Hook approval is
retained, bound to the configured command/stage/timeout, and not granted automatically.

Frozen hashes detect file modification through supported verification paths; they do not provide
OS-enforced file immutability. Findings require the existing independent review conventions, and
numerical bindings establish numerical consistency only. No scientific data was altered to satisfy
tests. No live HPC deletion, dataset download, paid model run, credential inspection or third-party
skill installation was performed. HPC archive/path/transport behavior is primarily offline-tested.

## Verification record

See [TEST_VERIFICATION_1_0_0.md](TEST_VERIFICATION_1_0_0.md) for commands, counts, artifacts and limitations.
The initial failure of bare `npm test` was an environment failure (no pytest in shell Python), not a
passing baseline. The baseline with the repository environment passed 432 tests. The final full run passed 485 Python tests plus TypeScript checking and 3 client tests; the final run/storage subset passed 93 tests. Real installed-VSIX validation passed 73/73 checks. Packaging and isolated clean installation passed. Actual installed
VS Code validation is distinguished from Node mocks, fake-cluster tests and mocked provider output.

A small local performance probe with 100 questions measured median open/sync/list at 0.0085 seconds,
rebuild at 0.160 seconds, and CLI help process startup at 0.082 seconds (five samples for medians,
Python 3.10.18 on this macOS host). This is a small-project probe, not a large-project or network-FS
benchmark. Streaming JSONL and bounded hook/check output reduce avoidable memory growth without
introducing cache invalidation machinery.

## Preserved design decisions

- Scientific and operational lifecycles remain separate and connected by references.
- Plain-text project records remain canonical; SQLite remains an index.
- No new cloud, provider coupling, runtime framework or runtime dependency was introduced.
- Existing CLI commands, configuration layers, schema and legacy routing remain supported.
- Human hook approval, independent-review conventions and bounded escalation remain explicit.
- Historical proposals are labelled, not erased; useful compatibility code was not deleted merely
  because it originated in an older version.

The code, documentation and local artifacts are reviewable stabilization work. They are not yet a
release I would approve as research-data-safe 1.0.0. Close the P1 issues, complete the missing review
and validation, then update all package versions together and rerun artifact-level verification.
