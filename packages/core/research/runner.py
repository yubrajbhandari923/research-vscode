"""Run wrapper: executes one command, capturing logs + exit status into a run directory.

    python -m research.runner --run-dir .research/runs/RUN-0003 [--tee] [--cwd DIR] -- "<shell command>"

This module is deliberately independent of the SQLite index so it can run on SLURM
compute nodes: it only writes files inside its run directory.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from typing import Any, Dict, Optional


def _now() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def write_status(run_dir: str, **fields: Any) -> Dict[str, Any]:
    path = os.path.join(run_dir, "status.json")
    cur: Dict[str, Any] = {}
    try:
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    except (OSError, ValueError):
        pass
    cur.update({k: v for k, v in fields.items()})
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return cur


def read_status(run_dir: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(run_dir, "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pump(src, *dsts) -> None:
    for chunk in iter(lambda: src.read1(8192) if hasattr(src, "read1") else src.read(8192), b""):
        for d in dsts:
            try:
                d.write(chunk)
                d.flush()
            except Exception:
                pass


def run(run_dir: str, command: str, cwd: Optional[str] = None, tee: bool = False) -> int:
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    run_id = os.path.basename(run_dir.rstrip("/"))
    shell = shutil.which("bash") or "/bin/sh"
    env = dict(os.environ)
    env.update({"RESEARCH_RUN_ID": run_id, "RESEARCH_RUN_DIR": run_dir, "RESEARCH_IN_JOB": "1"})
    status: Dict[str, Any] = {
        "state": "running", "runner_pid": os.getpid(), "hostname": socket.gethostname(),
        "started_at": _now(), "ended_at": None, "exit_code": None,
    }
    if env.get("SLURM_JOB_ID"):
        status.update({"slurm_job_id": env.get("SLURM_JOB_ID"), "slurm_nodelist": env.get("SLURM_JOB_NODELIST"),
                       "slurm_partition": env.get("SLURM_JOB_PARTITION")})
    out = open(os.path.join(run_dir, "stdout.log"), "ab")
    err = open(os.path.join(run_dir, "stderr.log"), "ab")
    try:
        proc = subprocess.Popen([shell, "-c", command], cwd=cwd or None, env=env,
                                stdout=subprocess.PIPE if tee else out,
                                stderr=subprocess.PIPE if tee else err,
                                start_new_session=True)
    except OSError as e:
        err.write(f"[research.runner] failed to start: {e}\n".encode())
        write_status(run_dir, **{**status, "state": "failed", "exit_code": 127, "ended_at": _now(), "error": str(e)})
        return 127
    status["pid"] = proc.pid
    write_status(run_dir, **status)

    cancelled = {"flag": False}

    def _forward(signum, _frame):
        cancelled["flag"] = True
        try:
            os.killpg(proc.pid, signum)
        except OSError:
            pass

    for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGUSR1", signal.SIGTERM)):
        try:
            signal.signal(s, _forward)
        except (ValueError, OSError):  # not main thread
            pass

    threads = []
    if tee:
        so = getattr(sys.stdout, "buffer", sys.stdout)
        se = getattr(sys.stderr, "buffer", sys.stderr)
        threads = [threading.Thread(target=_pump, args=(proc.stdout, out, so), daemon=True),
                   threading.Thread(target=_pump, args=(proc.stderr, err, se), daemon=True)]
        for t in threads:
            t.start()
    while True:
        try:
            code = proc.wait()
            break
        except KeyboardInterrupt:
            _forward(signal.SIGINT, None)
    for t in threads:
        t.join(timeout=5)
    out.close()
    err.close()
    if cancelled["flag"]:
        state = "cancelled"
    else:
        state = "completed" if code == 0 else "failed"
    write_status(run_dir, state=state, exit_code=code, ended_at=_now())
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.runner")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--cwd")
    ap.add_argument("--tee", action="store_true")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("missing command")
    command = cmd[0] if len(cmd) == 1 else " ".join(cmd)
    return run(a.run_dir, command, a.cwd, a.tee)


if __name__ == "__main__":
    sys.exit(main())
