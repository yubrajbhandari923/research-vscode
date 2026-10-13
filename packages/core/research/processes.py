"""Bounded shell checks shared by hooks and task verification.

Commands are trusted project code, not sandboxed. POSIX descendants inherit a
process group so timing out a shell also stops the command it launched.
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time


def stop_process(proc):
    """Stop a process started in its own session, including POSIX descendants."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    proc.wait()


def shell_check(command, *, cwd, env, timeout, output_limit=4000):
    start = time.monotonic()
    code, timed_out = None, False
    # Spool output to disk instead of retaining an unbounded log in memory.
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.Popen(command, shell=True, cwd=cwd, env=env,
                                    stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=os.name == "posix")
            try:
                code = proc.wait(timeout=timeout)
            except BaseException as exc:
                stop_process(proc)
                if not isinstance(exc, subprocess.TimeoutExpired):
                    raise
                timed_out = True
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - output_limit))
            text = output.read().decode("utf-8", errors="replace")
        except OSError as exc:
            text = f"could not start: {exc}"
    return {"ok": code == 0 and not timed_out, "exit_code": code,
            "timed_out": timed_out, "seconds": round(time.monotonic() - start, 2), "output": text}
