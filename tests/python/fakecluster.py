"""A fake cluster for the HPC tests: a local directory plus a fake `ssh`/`rsync`/Slurm.

`research.hpc.transport.set_runner` swaps the real subprocess call for `FakeCluster.run`, which
interprets the argv it is handed:

  * `ssh HOST <shell>`  → runs the shell snippet with /bin/sh inside the fake cluster's root, with a
    PATH containing fake `sbatch`/`squeue`/`sacct`/`scancel` scripts
  * `rsync … src host:dst` / `scp …` → a local copy

So the tests exercise the real quoting, the real shell snippets, the real sbatch scripts and the real
runner — everything except the network. No HPC account, no credentials, fully offline.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from typing import Any, Dict, List, Optional

SBATCH = r"""#!/bin/sh
# fake sbatch: records the job, then runs it synchronously unless RESEARCH_FAKE_SLURM_DEFER=1
set -e
STATE="$FAKE_SLURM_STATE"
mkdir -p "$STATE"
SCRIPT=""
for a in "$@"; do case "$a" in --parsable) ;; -*) ;; *) SCRIPT="$a";; esac; done
[ -n "$SCRIPT" ] || { echo "fake sbatch: no script" >&2; exit 1; }
if [ -f "$STATE/fail" ]; then echo "fake sbatch: submission refused" >&2; exit 1; fi
N=$(cat "$STATE/next" 2>/dev/null || echo 1000)
echo $((N + 1)) > "$STATE/next"
JOB=$N
printf '%s\n' "$SCRIPT" > "$STATE/job-$JOB.script"
if [ -f "$STATE/defer" ]; then
  printf 'PENDING\n' > "$STATE/job-$JOB.state"
else
  printf 'RUNNING\n' > "$STATE/job-$JOB.state"
  if sh "$SCRIPT" > "$STATE/job-$JOB.out" 2> "$STATE/job-$JOB.err"; then
    printf 'COMPLETED\n' > "$STATE/job-$JOB.state"; printf '0\n' > "$STATE/job-$JOB.exit"
  else
    code=$?
    printf 'FAILED\n' > "$STATE/job-$JOB.state"; printf '%s\n' "$code" > "$STATE/job-$JOB.exit"
  fi
fi
printf '%s\n' "$JOB"
"""

SQUEUE = r"""#!/bin/sh
# fake squeue: only pending/running jobs are "in the queue"
STATE="$FAKE_SLURM_STATE"
JOB=""
while [ $# -gt 0 ]; do case "$1" in -j) shift; JOB="$1";; esac; shift; done
[ -n "$JOB" ] || exit 0
S=$(cat "$STATE/job-$JOB.state" 2>/dev/null || echo "")
case "$S" in PENDING|RUNNING) printf '%s|node001|batch\n' "$S";; *) exit 0;; esac
"""

SACCT = r"""#!/bin/sh
# fake sacct: terminal jobs only; `touch $STATE/noacct` to simulate accounting being down
STATE="$FAKE_SLURM_STATE"
[ -f "$STATE/noacct" ] && exit 0
JOB=""
while [ $# -gt 0 ]; do case "$1" in -j) shift; JOB="$1";; esac; shift; done
[ -n "$JOB" ] || exit 0
S=$(cat "$STATE/job-$JOB.state" 2>/dev/null || echo "")
[ -n "$S" ] || exit 0
E=$(cat "$STATE/job-$JOB.exit" 2>/dev/null || echo 0)
printf '%s|%s|%s:0|2026-01-01T00:00:00|2026-01-01T00:10:00|node001\n' "$JOB" "$S" "$E"
"""

SCANCEL = r"""#!/bin/sh
STATE="$FAKE_SLURM_STATE"
for JOB in "$@"; do
  [ -f "$STATE/job-$JOB.state" ] || { echo "fake scancel: no such job $JOB" >&2; exit 1; }
  printf 'CANCELLED\n' > "$STATE/job-$JOB.state"
done
"""


class FakeCluster:
    """A cluster that lives in a temporary directory."""

    def __init__(self, root: str, host: str = "fake-login"):
        self.root = os.path.abspath(root)
        self.host = host
        self.state = os.path.join(self.root, ".slurm")
        self.bin = os.path.join(self.root, ".bin")
        # The "remote" filesystem. These are real absolute paths, so Research OS's own path
        # validation, quoting and shell snippets all run unmodified.
        self.workspace = os.path.join(self.root, "workspace")
        self.shared = os.path.join(self.root, "shared")
        self.calls: List[List[str]] = []
        self.fail_next_ssh = 0          # drop this many ssh calls (simulated disconnects)
        self.offline = False
        self.drop_matching: Optional[str] = None   # drop ssh calls whose command contains this
        # The dangerous case: the command RUNS on the cluster, but the reply never reaches the laptop.
        # Research OS must not conclude "submission failed" from a lost reply.
        self.lose_reply_matching: Optional[str] = None
        # Mimic a real cluster (Duke's DRCC) where a profile script puts Slurm on PATH: a plain
        # `ssh host sbatch` fails, while an interactive ssh or `bash -lc` finds it.
        self.slurm_needs_login_shell = False
        for d in (self.root, self.state, self.bin, self.workspace, self.shared):
            os.makedirs(d, exist_ok=True)
        for name, body in (("sbatch", SBATCH), ("squeue", SQUEUE), ("sacct", SACCT), ("scancel", SCANCEL)):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # ------------------------------------------------------------------ remote filesystem
    def path(self, remote: str) -> str:
        """Remote paths are configured to live under the fake root already, so this is identity."""
        return remote

    def mkdirs(self, *remote: str) -> None:
        for r in remote:
            os.makedirs(self.path(r), exist_ok=True)

    def write(self, remote: str, text: str) -> None:
        path = self.path(remote)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, remote: str) -> Optional[str]:
        try:
            with open(self.path(remote), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def exists(self, remote: str) -> bool:
        return os.path.exists(self.path(remote))

    def defer_jobs(self, on: bool = True) -> None:
        """Make sbatch queue jobs instead of running them (for 'still queued' tests)."""
        self._flag("defer", on)

    def break_accounting(self, on: bool = True) -> None:
        self._flag("noacct", on)

    def refuse_sbatch(self, on: bool = True) -> None:
        self._flag("fail", on)

    def _flag(self, name: str, on: bool) -> None:
        f = os.path.join(self.state, name)
        if on:
            open(f, "w").close()
        elif os.path.exists(f):
            os.remove(f)

    def run_job(self, job: str) -> None:
        """Run a deferred job now (simulates Slurm getting round to it)."""
        script = os.path.join(self.state, f"job-{job}.script")
        with open(script, encoding="utf-8") as f:
            path = f.read().strip()
        r = subprocess.run(["sh", path], capture_output=True, text=True, env=self._env())
        state = "COMPLETED" if r.returncode == 0 else "FAILED"
        with open(os.path.join(self.state, f"job-{job}.state"), "w") as f:
            f.write(state + "\n")
        with open(os.path.join(self.state, f"job-{job}.exit"), "w") as f:
            f.write(f"{r.returncode}\n")

    def jobs(self) -> Dict[str, str]:
        out = {}
        for fn in sorted(os.listdir(self.state)):
            if fn.startswith("job-") and fn.endswith(".state"):
                job = fn[4:-6]
                with open(os.path.join(self.state, fn), encoding="utf-8") as f:
                    out[job] = f.read().strip()
        return out

    # ------------------------------------------------------------------ the fake transport
    def _env(self) -> Dict[str, str]:
        return {**os.environ, "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
                "FAKE_SLURM_STATE": self.state,
                # the compute node must not see the laptop's PYTHONPATH: the bundle sets its own
                "PYTHONPATH": ""}

    def run(self, argv: List[str], timeout: float) -> Any:
        from research.hpc.transport import Result
        self.calls.append(list(argv))
        prog = os.path.basename(argv[0])
        if self.offline and prog in ("ssh", "rsync", "scp"):
            return Result(argv, 255, "", "ssh: connect to host fake-login port 22: Connection timed out")
        if prog == "ssh":
            return self._ssh(argv, timeout)
        if prog in ("rsync", "scp"):
            return self._copy(argv)
        return Result(argv, 127, "", f"fake cluster: unknown program {prog}")

    def _ssh(self, argv: List[str], timeout: float) -> Any:
        from research.hpc.transport import Result
        # argv = ["ssh", *opts, host, command]
        if argv[-2] != self.host and self.host not in argv:
            return Result(argv, 255, "", f"ssh: Could not resolve hostname {argv[-2]}")
        cmd = argv[-1]
        if self.fail_next_ssh > 0:
            self.fail_next_ssh -= 1
            return Result(argv, 255, "", "client_loop: send disconnect: Broken pipe")
        if self.drop_matching and self.drop_matching in cmd:
            return Result(argv, 255, "", "client_loop: send disconnect: Broken pipe")
        env = self._env()
        if self.slurm_needs_login_shell and "-lc" not in cmd:
            # not a login shell → the profile never ran → Slurm is not on PATH
            env["PATH"] = os.pathsep.join(
                x for x in env["PATH"].split(os.pathsep) if os.path.realpath(x) != os.path.realpath(self.bin))
        r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True,
                           cwd=self.root, env=env, timeout=timeout)
        if self.lose_reply_matching and self.lose_reply_matching in cmd:
            # the work happened; only the answer was lost
            return Result(argv, 255, "", "client_loop: send disconnect: Broken pipe")
        return Result(argv, r.returncode, r.stdout, r.stderr)

    def _copy(self, argv: List[str]) -> Any:
        from research.hpc.transport import Result
        src, dst = argv[-2], argv[-1]
        if ":" in dst:                                   # upload
            _, _, remote = dst.partition(":")
            target = self.path(remote)
            os.makedirs(target, exist_ok=True)
            local = src.rstrip("/")
            if os.path.isdir(local):
                for name in sorted(os.listdir(local)):
                    s = os.path.join(local, name)
                    d = os.path.join(target, name)
                    if os.path.isdir(s):
                        shutil.rmtree(d, ignore_errors=True)
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)
            else:
                shutil.copy2(local, target)
            return Result(argv, 0, "", "")
        _, _, remote = src.partition(":")                # download
        source = self.path(remote)
        if not os.path.exists(source):
            return Result(argv, 23, "", f"rsync: link_stat \"{remote}\" failed: No such file or directory")
        os.makedirs(dst, exist_ok=True)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(dst, os.path.basename(source.rstrip("/"))),
                            dirs_exist_ok=True)
        else:
            shutil.copy2(source, dst)
        return Result(argv, 0, "", "")


def install(cluster: FakeCluster):
    """Point the transport at this fake cluster. Returns a callable that restores the real one."""
    from research.hpc import transport
    transport.set_runner(cluster.run)
    return lambda: transport.set_runner(None)


def cluster_config(cluster: FakeCluster, *, slug: str = "demo", **over: Any) -> Dict[str, Any]:
    """A config.yaml `hpc` block pointing at the fake cluster."""
    cfg: Dict[str, Any] = {
        "default_cluster": "fake",
        "clusters": {
            "fake": {
                "transport": "ssh",
                "ssh_host": cluster.host,
                "paths": {
                    "workspace_root": cluster.workspace,
                    "shared_data": cluster.shared,
                    "projects": os.path.join(cluster.workspace, "projects"),
                },
                "transfer": {"method": "rsync"},
                "execution": {"use_slurm": True, "auto_submit": False},
                "slurm": {"time": "00:10:00", "cpus": 1, "mem": "1G", "partition": None,
                          "account": None, "gpus": None, "extra_args": [], "setup": []},
                "python": sys.executable,
                "project": {"slug": slug},
            }
        },
    }
    for k, v in over.items():
        cfg["clusters"]["fake"][k] = v
    return cfg
