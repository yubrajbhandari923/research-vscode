"""Single-use, expiring button tokens. The button carries only `a:<token>`; the record (project, op, params) stays on the machine.

`gateway/actions.json`: {token: {kind, project, op, params, min_role, created, expires, used, alert_id, text}}.
kind: "op" (call an op through `api.call(surface="chat")`), "local" (reply with a command to run at the computer), "ask_code"
(H.9: the clicker must type an authenticator code; the op receives it as `code`).
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Optional

from ..util import ResearchError
from . import gdir, read_json, txn, write_json

TTL = 24 * 3600
_now = time.time


def _path() -> str:
    return gdir("actions.json")


def issue(project: str, kind: str = "op", op: str = "", params: Optional[Dict[str, Any]] = None, min_role: str = "operator",
          alert_id: Optional[str] = None, text: str = "", ttl: float = TTL) -> str:
    with txn("actions"):
        d = read_json(_path())
        now = _now()
        d = {k: v for k, v in d.items() if v["expires"] > now - 3600}          # keep a spent token briefly so "already used" is accurate
        tok = secrets.token_urlsafe(9)[:12]
        d[tok] = {"kind": kind, "project": project, "op": op, "params": params or {}, "min_role": min_role, "created": now,
                  "expires": now + ttl, "used": None, "alert_id": alert_id, "text": text}
        write_json(_path(), d)
        return tok


def peek(token: str) -> Optional[Dict[str, Any]]:
    return read_json(_path()).get(token)


def take(token: str, by: str = "") -> Dict[str, Any]:
    """Mark used and return the record; raises with the user-facing reason when unknown, expired or already used."""
    with txn("actions"):
        d = read_json(_path())
        r = d.get(token)
        if not r:
            raise ResearchError("this button is not valid any more")
        if r["used"]:
            raise ResearchError(f"already done by {r['used']['by']}")
        if r["expires"] < _now():
            raise ResearchError("this button expired")
        r["used"] = {"by": by, "at": _now()}
        write_json(_path(), d)
        return r


def release(token: str) -> None:
    """Give a token back (the action failed before doing anything, e.g. a wrong authenticator code: the user may retry)."""
    with txn("actions"):
        d = read_json(_path())
        if token in d:
            d[token]["used"] = None
            write_json(_path(), d)
