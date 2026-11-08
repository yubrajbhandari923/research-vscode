"""Generic helpers for long-running per-project processes (the supervisor now, the chat gateway later):
exclusive lock, detached spawn, heartbeat file, and `ensure_current(root)`.

`supervisor.run_forever` and `supervisord.py` stay where they are; this module holds only what is not scheduler-specific.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from .durability import lock  # noqa: F401  (re-exported: `daemonlib.lock(path)` is the one-instance lock)


def pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError, OSError):
        return False


def spawn_detached(argv: List[str], log_path: str, env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
    """Start `argv` in its own session with stdout/stderr appended to `log_path`; it outlives the caller."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as log:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                                start_new_session=os.name == "posix")


def write_heartbeat(path: str, **fields: Any) -> None:
    from . import __version__
    from .store import _atomic_write
    _atomic_write(path, json.dumps({"pid": os.getpid(), "version": __version__, "heartbeat": time.time(), **fields}) + "\n")


def read_heartbeat(path: str) -> Dict[str, Any]:
    """The heartbeat plus `alive` and `stale` (running older code than this install). Missing or corrupt file: not alive."""
    from . import __version__
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {"alive": False, "stale": False}
    if not isinstance(d, dict):
        return {"alive": False, "stale": False}
    alive = pid_alive(d.get("pid"))
    return {**d, "alive": alive, "stale": alive and bool(d.get("version")) and d["version"] != __version__}


def ensure_current(root: str, timeout: float = 60.0) -> Dict[str, Any]:
    """Restart the project's supervisor if it runs older code than the installed Research OS (agents are untouched, as
    `supervisor.daemon_restart` guarantees). Does nothing when it is not running or is current. Never raises."""
    from . import supervisor as SV
    from .store import Project
    try:
        with Project(root, sync=False) as p:
            if SV.daemon_status(p).get("restart_required"):
                return {"action": "restart", **SV.daemon_restart(p, timeout=timeout)}
    except Exception as ex:
        return {"action": "none", "error": str(ex)[:200]}
    return {"action": "none"}
