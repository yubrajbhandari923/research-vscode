"""Alert outbox with dedupe, cooldown and escalation. Producers (the supervisor's notify step, the gateway watchdog) call
`raise_` / `clear`; the gateway tails `.research/supervisor/outbox.jsonl` and delivers. Records never contain secrets: the gateway
redacts again on the way out."""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .. import registry
from ..store import append_jsonl, _atomic_write

KINDS = ("halt", "task_failed", "approval_needed", "promotion_ready", "plan_finished", "run_failed", "budget", "supervisor_down",
         "decision", "provider_limit")                  # last two: H.7 and H.10 producers (WP14b / later packages)
SUGGEST = {"halt": "continue with another agent, pause the plan, or run /debug", "task_failed": "continue with another agent (/continue T-n PROFILE) or pause the plan"}
_now = time.time                                        # tests replace it


def outbox_path(p) -> str:
    return p.path("supervisor", "outbox.jsonl")


def _state_path(p) -> str:
    return p.path("supervisor", "alerts.json")


def _load(p) -> Dict[str, Any]:
    try:
        with open(_state_path(p), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def cfg(p) -> Dict[str, Any]:
    return registry.section(p, "notifications")["alerts"]


def _append(p, rec: Dict[str, Any]) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(outbox_path(p)), exist_ok=True)
    append_jsonl(outbox_path(p), rec)
    return rec


def raise_(p, key: str, kind: str, severity: str, text: str, actions: Optional[List[Dict[str, Any]]] = None,
           files: Optional[List[str]] = None, task: Optional[str] = None, decision: Optional[str] = None,
           once: bool = False) -> Optional[Dict[str, Any]]:
    """Queue an alert unless the same `key` is inside its cooldown (then a "still: ..." reminder after it) or the kind is switched off.
    `actions`: [{label, op, params, style?, kind?: op|local|ask_code, text?, min_role?}]. Returns the record or None when suppressed."""
    c = cfg(p)
    if c["on"] and kind not in c["on"]:
        return None
    st, now = _load(p), _now()
    e = (st.setdefault("keys", {})).get(key)
    if e and once:
        return None
    sk = f"{kind}:{task or ''}"
    if e and e.get("open"):
        if now - e["ts"] < float(c["cooldown_minutes"]) * 60:
            e["suppressed"] = e.get("suppressed", 0) + 1
            _atomic_write(_state_path(p), json.dumps(st, indent=1) + "\n")
            return None
        text = "still: " + text
    else:
        n = st.setdefault("streak", {}).get(sk, 0) + 1
        st["streak"][sk] = n
        if n >= int(c["escalate_after"]) and kind in SUGGEST:
            text += f"\n{n} {kind.replace('_', ' ')} alerts in a row for {task or 'this plan'}: consider {SUGGEST[kind]}."
    st["keys"][key] = {"ts": now, "open": True, "kind": kind, "task": task}
    _atomic_write(_state_path(p), json.dumps(st, indent=1) + "\n")
    return _append(p, {"id": uuid.uuid4().hex[:12], "ts": now, "key": key, "kind": kind, "severity": severity, "text": text, "actions": actions or [],
                       "files": files or [], "task": task, "decision": decision, "clear": False, "project_root": p.root})


def clear(p, key: str, text: str) -> Optional[Dict[str, Any]]:
    """The condition is over: send "moving again" once, and reset the failure streak for that kind and task."""
    st = _load(p)
    e = (st.get("keys") or {}).get(key)
    if not e or not e.get("open"):
        return None
    e["open"] = False
    (st.setdefault("streak", {})).pop(f"{e['kind']}:{e.get('task') or ''}", None)
    _atomic_write(_state_path(p), json.dumps(st, indent=1) + "\n")
    return _append(p, {"id": uuid.uuid4().hex[:12], "ts": _now(), "key": key, "kind": e["kind"], "severity": "ok", "text": text, "actions": [],
                       "files": [], "task": e.get("task"), "decision": None, "clear": True, "project_root": p.root})


def open_keys(p, prefix: str = "") -> set:
    return {k for k, v in (_load(p).get("keys") or {}).items() if v.get("open") and k.startswith(prefix)}


def read_from(path: str, offset: int = 0, inode: Optional[int] = None) -> Tuple[List[Dict[str, Any]], int, Optional[int]]:
    """New records since (inode, offset): at-least-once. A replaced file (new inode) or a shorter one is read from the start.
    Only whole lines are consumed, so a half-written record is picked up next time."""
    try:
        st = os.stat(path)
    except OSError:
        return [], 0, None
    if inode is not None and (st.st_ino != inode or st.st_size < offset):
        offset = 0
    out: List[Dict[str, Any]] = []
    with open(path, "rb") as f:
        f.seek(offset)
        for line in f:
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            try:
                r = json.loads(line.decode("utf-8"))
                r["_end"] = offset          # where the cursor goes once this record is delivered
                out.append(r)
            except ValueError:
                pass
    return out, offset, st.st_ino
