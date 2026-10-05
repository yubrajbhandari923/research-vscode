from __future__ import annotations

import os
import signal
import subprocess
from typing import Any, Dict, Optional

from ..runner import read_status
from ..util import ResearchError, hostname
from .base import Backend, runner_argv, runner_env


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


class LocalBackend(Backend):
    name = "local"

    def submit(self, project, run: Dict[str, Any], opts: Dict[str, Any]) -> Dict[str, Any]:
        run_dir = project.abspath(run["run_dir"])
        cwd = project.abspath(run.get("working_dir") or ".")
        argv = runner_argv(run_dir, run["command"], cwd)
        with open(os.devnull, "rb") as devnull:
            proc = subprocess.Popen(argv, cwd=cwd, env=runner_env(), stdin=devnull,
                                    stdout=subprocess.DEVNULL, stderr=open(os.path.join(run_dir, "runner.err"), "ab"),
                                    start_new_session=True)
        return {"pid": proc.pid, "hostname": hostname(), "status": "running"}

    def status(self, project, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = read_status(project.abspath(run["run_dir"])) or {}
        state = st.get("state")
        if state in ("completed", "failed", "cancelled"):
            return st
        pid = st.get("runner_pid") or run.get("pid")
        host = st.get("hostname") or run.get("hostname")
        if host and host != hostname():
            return st or None  # can't check a process on another machine
        if pid and not pid_alive(pid):
            return {**st, "state": "unknown", "note": "runner process disappeared without recording an exit status"}
        return st or None

    def cancel(self, project, run: Dict[str, Any]) -> None:
        st = read_status(project.abspath(run["run_dir"])) or {}
        pid = st.get("runner_pid") or run.get("pid")
        if st.get("hostname") and st["hostname"] != hostname():
            raise ResearchError(f"{run['id']} is running on {st['hostname']}; cancel it there.")
        if not pid or not pid_alive(pid):
            raise ResearchError(f"{run['id']} is not running")
        os.kill(int(pid), signal.SIGTERM)
