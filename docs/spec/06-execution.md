# 6. Execution: runs, SLURM, HPC, workspaces and isolation

## Run creation (`create_run`)

1. Check the budget ([Policy](05-agents-governance.md#policy-configyaml--agent_policy)). If the run is about to execute (`status=queued`, backend other than `manual`), run the approved
   `pre_experiment` harness hooks; a failing required hook raises `PolicyBlocked` (exit 3) before anything is written.
2. Capture git info. Allocate a `RUN-` id. Create the run directory.
3. Insert the row and log a `created` event.
4. If git is dirty, write `git.diff`:
   - `git diff HEAD -- . ':(exclude).research'`, capped at 2 MiB
   - plus a commented list of untracked files
5. Write `command.sh` and `run.json`.
6. Apply experiment transitions ([Experiment](04-lifecycles.md#experiment)) and regenerate the experiment mirror.

**Dirty** means tracked changes outside `.research/`, ignoring untracked files (`git status --porcelain --untracked-files=no`).

## The runner (`python -m research.runner --run-dir D [--cwd W] [--tee] -- "<cmd>"`)

- Has no SQLite and imports only the standard library, except that a sandboxed run imports `research.isolation` (below). Safe on compute nodes.
- Runs `bash -c "<cmd>"` (or `/bin/sh` if bash is missing) in a **new session** (process group). The environment adds
  `RESEARCH_RUN_ID`, `RESEARCH_RUN_DIR` and `RESEARCH_IN_JOB=1`.
- **Before start**, writes `status.json`: `state=running`, `runner_pid`, child `pid`, `hostname`, `started_at`, and the SLURM
  env vars if present.
- stdout and stderr are appended to `stdout.log` and `stderr.log`. With `--tee` they are also streamed to the terminal by
  pump threads.
- SIGTERM, SIGINT and SIGUSR1 received by the runner are forwarded to the child's process group, and the final state becomes `cancelled`.
- **On exit**, writes `state` (`completed` if the exit code is 0, `failed` otherwise, `cancelled` if the runner forwarded a signal), `exit_code`
  and `ended_at`. If the command cannot be started, `exit_code=127` and `state=failed`; a sandbox that refuses to start gives `exit_code=126`.
- All JSON writes are atomic (temp file + `os.replace`).

## Local backend

| Mode | Behaviour |
|---|---|
| **Foreground** (`research run exec`) | The runner is called in-process with `tee=True`, then reconciled. The CLI's exit status is 0 for completed, otherwise the run's exit code. |
| **Detached** (`--detach`, VS Code "Background") | `Popen([python, -m, research.runner, …], start_new_session=True)` with `PYTHONPATH` pointing at the core. stdin is `/dev/null`, runner errors go to `runner.err`. The run survives the terminal, VS Code and SSH disconnects. |
| **Status** | If `status.json` is terminal, use it. Otherwise, if the run was on **this** host and its pid is dead, the state is `unknown` ("runner process disappeared…"). Runs on another host are left alone. |
| **Cancel** | `SIGTERM` to the runner pid, which forwards it to the job. Refused if the run is on another host. |

## SLURM backend

**Submission** (`research run submit` / `run_start(backend=slurm)`):

1. Merge `config.backends.slurm` with per-call options (per-call wins; empty values ignored).
2. Write `job.sbatch`:
   ```bash
   #!/bin/bash
   #SBATCH --job-name=RUN-0007
   #SBATCH --output=<abs run_dir>/slurm-%j.out
   #SBATCH --partition=…  --account=…  --time=…  --cpus-per-task=…  --mem=…  --gres=gpu:N
   #SBATCH <each extra_args entry>
   cd '<abs working dir>'
   <setup lines, verbatim>
   export PYTHONPATH='<core parent>'"${PYTHONPATH:+:$PYTHONPATH}"
   exec '<python>' -m research.runner --run-dir '<abs run dir>' --cwd '<abs wd>' -- '<command>'
   ```
   - `gpus: 1` becomes `--gres=gpu:1`, and `gpus: "a100:2"` becomes `--gres=a100:2`.
   - `python` is `backends.slurm.python` if set, otherwise the submitting interpreter (`sys.executable`). This assumes a
     shared filesystem, which is standard on HPC.
3. Run `sbatch --parsable job.sbatch`. The job id is the text before any `;cluster` suffix.
4. Store `slurm_job_id` and `slurm.requested`, set status `queued`, log a `submitted` event.
5. If submission fails (e.g. `sbatch` is missing), the run is marked `failed` with a note and the error is raised with a hint.

**Status reconciliation**, first source that answers wins:

1. `status.json` is terminal: use it (exact exit code, host, times).
2. `squeue -h -j ID -o "%T|%N|%S"` returns a line: map the state and record the nodelist.
3. `sacct -n -P -j ID -o JobID,State,ExitCode,Start,End,NodeList` (the line whose JobID matches exactly): map the state,
   exit code and times. This covers jobs killed before the runner could write, such as TIMEOUT or out-of-memory.
4. Otherwise the job left the queue with no record: `unknown`.

**State mapping:**

| SLURM states | Run status |
|---|---|
| PENDING, CONFIGURING, REQUEUED, RESIZING, SUSPENDED | queued |
| RUNNING, COMPLETING, STAGE_OUT | running |
| COMPLETED | completed |
| FAILED, TIMEOUT, NODE_FAIL, OUT_OF_MEMORY, BOOT_FAIL, DEADLINE, PREEMPTED | failed |
| CANCELLED (incl. "CANCELLED by uid"), REVOKED | cancelled |
| anything else | unknown |

**Cancel:** `scancel ID`.

## Reconciliation (`reconcile_run`)

1. Ingest new metrics and artifacts from the run directory.
2. Ask the backend (or `status.json` for manual runs).
3. Merge changes: `status`, `exit_code`, `started_at`, `ended_at`, `hostname`, `slurm_job_id`, and `slurm.{nodelist, state, partition}`.
4. A terminal status without an end time gets `ended_at = now`.
5. Log a system event, for example `RUN-0004 completed (exit 0)`.
6. Rewrite `run.json` and apply the experiment transitions.

The extension polls `run_refresh` every `research.pollIntervalSeconds` (default 10 s) **only while active runs exist**.

## Metrics and artifacts from inside a run

`research.log_metric(name, value, step=None, unit=None)` and `research.register_artifact(path, …)` called **with no
explicit run or experiment** and with `RESEARCH_RUN_DIR` set:

- append to `<run_dir>/metrics.jsonl` or `artifacts.jsonl` (artifact paths are made absolute)
- touch no database
- are ingested by the next sync or status check on a machine that has the index

Outside a run, they write through the project, which appends to JSONL and then ingests immediately.

## Parameter sweeps (`research run sweep`, `sweep_run`)

```
research run sweep EXP-001 [--grid lr=1e-3,1e-4 --grid bs=32,64] [--mode local|detach|slurm] [--dry-run] [--override R] -- python train.py --lr {lr} --bs {bs}
```

- **Points:** the Cartesian product of the `--grid` axes (values parsed like `--param`), or, without `--grid`, of the
  experiment's list-valued parameters. Scalar experiment parameters are added to every point.
- **Command:** `{name}` placeholders are replaced by the point's values; each run gets `parameters` = the point and
  `label` = `name=value, …` over the swept axes.
- **Checked up front:** the sweep is refused (agents; warning for humans) if it would exceed the runs left before
  synthesis is required, or take the experiment past `planned_runs`. Because the whole sweep is checked before the first
  run, it never stops halfway through. `--override "reason"` is logged once for the sweep.
- **Modes:** `local` runs the points one after another in the foreground; `detach` starts them all in the background;
  `slurm` submits each (`--partition/--time/--gpus/…` as for `run submit`). A `swept` event records the sweep.

## Remote SSH and HPC behaviour

- `extensionKind: workspace` means the extension host, and therefore the RPC process, git, SQLite, the file watchers,
  `sbatch` and all artifact reads, run on the **remote** machine. The laptop only renders. Images reach webviews through
  VS Code's resource tunnelling (`asWebviewUri`).
- The extension must be installed on the remote side once (`code --install-extension …` from a Remote terminal, or
  "Install in SSH: host").
- **SQLite on NFS/Lustre** is handled by the rollback journal, busy timeout and short write transactions. Compute nodes
  never open the DB ([Process model](01-architecture.md#process-model), [Metrics and artifacts from inside a run](#metrics-and-artifacts-from-inside-a-run)), so concurrent jobs cannot corrupt or lock it.
- **Disconnects:** detached and SLURM runs keep running. Status is reconstructed from files and SLURM accounting on the
  next connection.

## HPC execution

**Package:** `research/hpc/` — `config.py` (cluster profiles), `transport.py` (ssh/rsync), `bundle.py`
(portable packages), `results.py` (import), `datasets.py` (discovery), `remote.py` (doctor/submit/sync).
Entirely **opt-in**: with no `hpc.clusters` configured, every command says so and nothing else changes.

### Identity, and why submission is never retried

```
laptop: run id + submission token allocated  →  immutable bundle  →  rsync to the cluster
     →  sbatch (writes a receipt carrying run id + token + job id as its first action)
     →  [days pass; the laptop may be off]  →  research hpc sync  →  results imported onto the SAME run
```

The dangerous failure is `sbatch` succeeding and the connection dying before the job id is read. Research OS
therefore: allocates the run id and token **before** anything is transferred; has the job script write a
remote receipt; and, when it cannot confirm a job id, records `submission_uncertain` and **refuses to
retry**. `research hpc sync` then reads the receipt (and `squeue`/`sacct`) and adopts the job that is already
running. A transient SSH failure is never taken as proof that submission failed. Tested explicitly: one job
on the cluster, one run record, no duplicate.

### State ownership

The laptop holds the only authoritative research history. The cluster holds execution state only, described
by small manifests in its own directories, reusing the existing run-directory conventions
(`run.json`, `status.json`, `metrics.jsonl`, `artifacts.jsonl`):

```
<projects>/<slug>/
  code/EXP-001/RUN-0007/     immutable code snapshot + .bundle-token
  runs/RUN-0007/             submission.json, status.json, metrics.jsonl, logs
  results/RUN-0007/
  metadata/project.json
```

Information flows one way (execution package out, results in). There are never two mutable `.research/`
databases, and no remote database or registry of any kind.

### Bundles

A bundle carries project/experiment/run identity, the hypothesis and intended test, the source commit **and
dirty-state provenance**, an actual source snapshot (uncommitted and untracked files included — a commit
alone is not a snapshot of what ran), runtime config, resource requirements, expected inputs/outputs,
environment setup, dataset references, run commands and parameters, expected metric/artifact schema, and
SHA-256 hashes of every packaged file.

It ships `research_runner` as a standalone top-level module — not `research.runner`, whose package `__init__`
would pull in SQLite and YAML. No agent, no network and no Research OS install are needed on the cluster.
Never packaged: credential-shaped files (`id_*`, `*.pem`, `.env`, `.netrc`, …, refused outright), `.git`,
`.research`, caches, and files over `hpc.bundle.max_file_bytes` unless named in `hpc.bundle.include`.

Results archives carry `manifest.json`, `status.json`, `metrics.jsonl`, `artifacts.json`, `logs/`,
`outputs/` and `checksums.json`. Import validates identity (run id, experiment, submission token), verifies
every checksum before trusting a byte, rejects absolute paths, `..` traversal and non-regular members, and
bounds member count and size. Re-importing an identical archive adds no metric, artifact or event: JSONL is
merged by content hash and artifacts are keyed on (path, run). Artifacts over
`hpc.collect.max_artifact_bytes` stay on the cluster and are registered as `remote-reference` artifacts with
their remote path and size.

### Datasets: no registry

The configured `shared_data` directory **is** the source of truth. A dataset is a directory under it,
described by a `manifest.json` inside that same directory. There is no `datasets.json`, no central index and
no registry service, so a new session discovers what exists by looking rather than by remembering.

- a directory without a manifest is **neither trusted nor re-downloaded** — it is inspected, then `adopt`ed
- `status` ∈ `downloading | complete | incomplete | corrupt | unknown`; the manifest is written **last**, so
  an interrupted transfer can never read as complete
- verification strength is explicit: `manifest` (status word only) → `inventory` (expected files exist,
  sizes match) → `checksum` (`--deep`, recomputed remotely). Terabytes are not rehashed every session.
- concurrency uses an atomic `mkdir` lock inside the dataset directory plus a staging directory — no
  distributed locking
- versions are distinguished by manifest `version`; a different version never overwrites an existing copy
- `auto_delete` is fixed at false: **Research OS never deletes remote datasets, code or results**

### Resource approval

Slurm bills what a job **reserves**, not what it uses, and a large request also waits longer in the
queue. Every submission path therefore discloses the reservation before anything is submitted:

- `research hpc submit --dry-run`, `research run submit --dry-run` and `research run sweep --mode slurm
  --dry-run` print CPUs, GPUs, memory, wall-time, partition and account, plus the CPU-/GPU-hours the
  reservation costs if the job runs to its limit. A sweep also totals that across every grid point.
- `research hpc submit` without `--yes` **refuses, and the refusal text carries the numbers**, so an
  approval cannot be sought without them.
- `reservation()` is the single place that computes this, so the CLI, the refusal and the recorded
  submission all state the same thing. A successful submission records what it reserved.
- The managed `AGENTS.md` block and the `hpc-setup` skill instruct agents to show the user these numbers
  and wait for an explicit yes, never to raise `cpus`/`gpus`/`mem`/`time` on their own initiative, and not
  to treat the cluster's `execution.auto_submit` as blanket permission for a new experiment's resources.
- The cluster's `limits` remain a hard backstop that refuses an over-large request before submission —
  that is a safety net, not the approval.

### Safety

Dry-run preview on `submit` and `init`; resource requests bounded by the cluster's `limits`; cluster chosen
explicitly (or a single configured default); every remote path validated as absolute, metacharacter-free and
traversal-free, and checked to lie **inside** the configured roots; `rsync --delete` refused; `BatchMode=yes`
with explicit timeouts and argument lists (never string interpolation of user data); read probes retried
once on a transient error, `sbatch` never; submission requires `--yes` or the cluster profile's `execution.auto_submit: true`; every
submission and retrieval logged as an event; and the original experiment/run relationship preserved exactly.

### Long-running work and continuation

Reconciliation is **on demand** (`research hpc sync`) and on the next session — there is no daemon and none
is required. Pending follow-up work survives process termination because it lives in the canonical run and
experiment records, not in memory. `sync` reports the experiments that now need interpretation through the
existing synthesis/task machinery and **dispatches no agent**: Slurm jobs and pre-submitted dependencies
continue while the laptop is off, but agent reasoning cannot, and Research OS does not pretend otherwise.

Checkpoint/restart is not reinvented: declare `restart_command` and `checkpoint_dir` parameters on the
experiment and the bundle carries them in `manifest.json` for your own script to use.

## Shared ledger and experiment workspaces

Opt-in. By default (classic mode) `.research/` sits beside the code. `research ledger enable` moves a git project to
**shared-ledger mode**: one ledger lives in its own worktree on branch `research/ledger`, and every source worktree of the
repository reaches it. Implemented in `workspace.py`.

| Concept | Where it lives | Portable? |
|---|---|---|
| Project identity | `<ledger>/.research/project.json` (`project_id`) | yes |
| Ledger root | `<git common dir>/research/ledger.json` | no (machine-local, never tracked) |
| Workspace records (`WS-001` …) | `<ledger>/.research/workspaces.jsonl`, append-only, folded on read; no paths | yes |
| Workspace → directory | `<git common dir>/research/workspaces.json` | no |
| A worktree's workspace id | `<worktree git dir>/research-workspace` (survives moving the directory) | no |
| Source repository identity | root commit hash of the repository | yes |

`Project` separates **`root`** (the source tree: git, run working directories, `AGENTS.md`, guide) from
**`ledger_root`** / **`rdir`** (where `.research/` lives). They are equal in classic mode. Paths inside the ledger are stored as
`.research/...`; all other stored paths stay relative to the source root. Discovery is automatic from the CLI, RPC and MCP:
`find_root` walks up for `.research/config.yaml` (classic wins), otherwise asks git (`--git-common-dir`, which also covers `.git`
being a file in a linked worktree) for the link.

Invariants: one ledger, one SQLite index, one counter per prefix, so ids cannot collide; all worktrees use the existing write
lock, journal and index; history survives deleting any experiment branch (the ledger is not on it); `research rebuild` is unchanged.

**Enabling** (`research ledger enable`, dry run by default; `--apply` performs it): refuses when `.research/` files are tracked by
git, when a transaction journal is unfinished, or when the target exists. It creates the ledger branch and worktree, copies
canonical records (not the index, locks or `bin/`), rebuilds the index there and compares per-table record counts with the
original, then links and renames the original to `.research.pre-ledger`. Any failure before the link removes what was created.
Nothing is committed. To undo: remove the ledger worktree, delete `ledger.json`, rename the backup back.

**Workspaces** (`research workspace create|attach|list|show|link-task|assign|archive|open`; RPC `workspaces`, `workspace_create`,
`workspace_archive`; MCP `workspace_list`). `create EXP-041` makes branch `exp/EXP-041-<title>` and a worktree
(under `layout.experiments_dir`, by default `<repo>-experiments/EXP-041` beside the checkout) from `HEAD` or `--base`; it never touches an existing branch. `archive` removes only the
directory: it refuses while runs are queued/running or while tracked changes or untracked files exist, pins the head as
`refs/research/archive/WS-n`, never uses `--force`, never deletes the branch. `--dry-run` shows the impact.

**Run provenance.** In shared mode each run records `env.workspace`: workspace id, branch, head, base revision, experiment, and a
`snapshot` of the exact working tree (tracked + untracked, not ignored) as a commit pinned by `refs/research/snapshots/<RUN>`.
It is built with a temporary index; the real index, HEAD and files are untouched.

Windows is unverified for shared-ledger mode.

## Job isolation

Optional; native stays the default (`isolation.py`, `sandbox.py`).

A **profile** (`sandbox.profiles.<name>`) chooses a runtime, image, filesystem access (source `read_only|read_write|hidden`, read-only
datasets, extra read-only/read-write mounts, temporary scratch; home and ledger are always hidden), network `enabled|disabled`, GPU, an
environment allowlist, and `enforcement`. Built-ins: `native`, `restricted`, `gpu-research`, `custom`. Precedence, highest first:
run (`--sandbox`) > task > experiment > cluster > project default (only while `sandbox.enabled`) > native. A restrictive profile is
mandatory: an agent cannot choose `native` over it, a human needs `--sandbox-override-reason`, and the override is logged and stored
in the run.

Each requested restriction has a state: `verified` (a canary under the same runtime and mount layout observed it holding),
`enforceable` (runtime present, not probed), or `unavailable`. If any requested restriction is unavailable, or the self-test fails, the
job is **refused before a run record exists** (`PolicyBlocked`, exit 3). The runner repeats the check at job start (compute nodes
included) and exits 126 with `sandbox_refused`; there is no fallback to native.

Runtimes: `native`; `userns` (unprivileged user+mount+pid(+net) namespaces and `pivot_root`; kernel-dependent, **not** a defence
against a hostile program that also exploits the kernel; the job sees itself as uid 0 and `/usr`, `/bin`, `/lib*` and a few `/etc` files
read-only); `apptainer` (`--containall --cleanenv --no-home --no-mount bind-paths`, explicit `--bind`, `--network none`, `--nv`).
Docker and Podman are not implemented; `register_runtime` is the extension point.

Mounts: host paths must be absolute, free of `..`, existing, and are resolved with `realpath` before binding. Refused: `/`, the home
directory, credential directories (`.ssh`, `.aws`, `.gnupg`, `.config/gcloud`, `.kube`, …, even through symlinks), the research ledger or
any parent of it, and anything outside `sandbox.approved_roots` when set. Job layout: `/workspace` (source), `/data/<name>` (datasets,
read-only), `/outputs` (`<run>/outputs`), `/run` (the run directory: logs, `metrics.jsonl`, `status.json`), `/tmp` (tmpfs). The job writes
results through run-directory files; it has no access to the ledger or index.

Provenance: `run.env.sandbox` stores the profile, scope chain, override, runtime and version, image identity (size, mtime, sha256 up to
1 GiB), resolved mounts, per-property state, and the full policy. `sandbox.json` in the run directory is what the runner reads.

CLI: `research sandbox list|show|doctor|verify|preview`; `research run exec|submit --sandbox P --sandbox-dataset PATH`.

**Agents are not sandboxed.** Job isolation does not restrict an agent's shell or SSH session. `sandbox.agent.mode: advisory` only writes
boundaries into the brief; `enforced` is unsupported and not accepted.

**Tests.** `tests/python/test_sandbox.py` exercises real `userns` enforcement (read-only source and dataset, writes only to outputs, protected files and fake credentials invisible, ledger invisible, clean environment, no network, isolated pid namespace) on a disposable fixture, and is skipped where unprivileged user namespaces are unavailable. Apptainer argv construction and refusal paths are tested with a fake executable only. **Unverified:** Apptainer or any site SLURM container plugin, GPU passthrough, behaviour on particular kernels or platforms (including WSL2 and Windows), and root/administrator-configured mounts. **Not implemented:** sandboxing of remote bundle execution. `hpc submit` refuses (exit 3) when a profile applies to the cluster, rather
than run unrestricted.
