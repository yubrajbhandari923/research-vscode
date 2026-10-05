from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

CORE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def runner_argv(run_dir: str, command: str, cwd: Optional[str], tee: bool = False,
                python: Optional[str] = None) -> List[str]:
    argv = [python or sys.executable, "-m", "research.runner", "--run-dir", run_dir]
    if cwd:
        argv += ["--cwd", cwd]
    if tee:
        argv.append("--tee")
    return argv + ["--", command]


def runner_env() -> Dict[str, str]:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    if CORE_PARENT not in pp.split(os.pathsep):
        env["PYTHONPATH"] = CORE_PARENT + (os.pathsep + pp if pp else "")
    return env


class Backend:
    """submit() starts a run; status() reports backend-side state; cancel() stops it.

    status() returns a dict with at least {"state": queued|running|completed|failed|cancelled|unknown}
    and optionally exit_code/started_at/ended_at/hostname — or None if the backend has no opinion.
    """

    name = "base"

    def submit(self, project, run: Dict[str, Any], opts: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def status(self, project, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:  # pragma: no cover
        return None

    def cancel(self, project, run: Dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError
