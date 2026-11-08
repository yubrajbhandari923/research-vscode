"""Line-delimited JSON-RPC over stdio, used by the VS Code extension: a thin transport over `research.api`.

    python -m research.rpc --root /path/to/project

Request:  {"id": 1, "method": "resume", "params": {...}}
Response: {"id": 1, "result": ...}  |  {"id": 1, "error": {"message", "hint", "code"}}
A response whose call wrote to the database also carries "changed": true. The first line is the `ready` event; its `ops` lists every
method this core serves, so a newer extension can hide a feature whose op is missing.

The extension spawns one process per workspace folder. It runs wherever the extension host runs: under Remote SSH that is the
remote machine, next to the files. Every method is defined once in `research/api/`.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from typing import Any, Dict

from . import __version__
from . import api
from .api.records import PROTOCOL
from .store import find_root
from .util import ResearchError


def _on_open(root: str) -> None:
    """Opening a project: remember it, start the supervisor if Telegram is enabled or a plan is running, and replace a daemon running
    older code. Background, best effort: never delays or breaks the RPC session."""
    try:
        from . import daemonlib, machine, notify
        from .store import Project
        machine.remember(root)
        if os.path.isdir(os.path.join(root, ".research")):
            with Project(root, sync=False) as p:
                notify.ensure_running(p)
            daemonlib.ensure_current(root)
    except Exception:
        pass


def _err(rid: Any, message: str, code: int) -> Dict[str, Any]:
    return {"id": rid, "error": {"message": message, "code": code}}


def serve(root: str, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    session = api.Session(root)

    def send(obj: Dict[str, Any]) -> None:
        stdout.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")
        stdout.flush()

    send({"event": "ready", "version": __version__, "protocol": PROTOCOL, "root": session.root, "initialized": session.initialized(),
          "ops": sorted(api.OPS)})
    threading.Thread(target=_on_open, args=(session.root,), daemon=True).start()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send(_err(None, "invalid JSON", -32700))
            continue
        if not isinstance(req, dict) or not isinstance(req.get("method"), str):
            send(_err(None, "invalid request", -32600))
            continue
        rid, method, params = req.get("id"), req["method"], req.get("params", {})
        if not isinstance(params, dict):
            send(_err(rid, "params must be an object", -32602))
            continue
        if method not in api.OPS:
            send(_err(rid, f"unknown method {method}", -32601))
            continue
        had_author, author = "author" in params, params.pop("author", None)   # optional per-request override from the UI
        if had_author and not isinstance(author, dict):
            send(_err(rid, f"bad parameters for {method}: author must be an object", -32602))
            continue
        try:
            result = api.call(method, session, params, author=author)
            resp = {"id": rid, "result": result}
            if session.changed:
                resp["changed"] = True
            send(resp)
        except api.BadParams as e:
            send(_err(rid, str(e), e.rpc_code))
        except ResearchError as e:
            send({"id": rid, "error": {"message": str(e), "hint": e.hint, "code": e.exit_code, "data": e.data, "kind": type(e).__name__}})
        except Exception as e:  # pragma: no cover
            send({"id": rid, "error": {"message": f"{type(e).__name__}: {e}", "code": -32603, "trace": traceback.format_exc(limit=6)}})


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m research.rpc")
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    serve(a.root or find_root() or os.getcwd())
    return 0


if __name__ == "__main__":
    sys.exit(main())
