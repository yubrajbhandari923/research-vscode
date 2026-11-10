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
         "decision", "provider_limit",                 # these two: H.7 and H.10 producers (WP14b / later packages)
         "plan_started", "experiment_started", "experiment_finished", "run_finished", "task_started", "task_verified", "answer", "recovery")
# `notifications.level` is a preset over the kinds: each kind has the lowest level that sends it. An unlisted kind counts as "medium".
#   low     the plan starts or ends, plus failures (a halt is a failure: nothing can move)
#   medium  (default) plus each experiment starting and FINISHING with its recorded results, and what waits for you (approvals, decisions...)
#   high    plus every run finishing, and each task starting or being verified
LEVELS = {"off": 0, "low": 1, "medium": 2, "high": 3}
LEVEL_HELP = {"off": "nothing automatic (commands still work)",
              "low": "plan started / finished, and failures",
              "medium": "plus each experiment started and finished (with the results it recorded) and anything waiting for you",
              "high": "plus every run and every task"}
KIND_LEVEL = {"halt": 1, "task_failed": 1, "run_failed": 1, "budget": 1, "supervisor_down": 1, "plan_started": 1, "plan_finished": 1,
              "approval_needed": 2, "recovery": 2, "decision": 2, "promotion_ready": 2, "provider_limit": 2, "experiment_started": 2, "experiment_finished": 2,
              "run_finished": 3, "task_started": 3, "task_verified": 3,
              "answer": 0}      # a reply to something you asked for (/ask): never filtered by the level or the kind list
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


def level(p) -> str:
    v = str(registry.section(p, "notifications").get("level") or "medium").lower()
    return v if v in LEVELS else "medium"


def allowed(p, kind: str) -> bool:
    return KIND_LEVEL.get(kind, 2) <= LEVELS[level(p)]


def raise_(p, key: str, kind: str, severity: str, text: str, actions: Optional[List[Dict[str, Any]]] = None,
           files: Optional[List[str]] = None, task: Optional[str] = None, decision: Optional[str] = None,
           once: bool = False, chat: Optional[str] = None, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Queue an alert unless the same `key` is inside its cooldown (then a "still: ..." reminder after it), the kind is switched off
    (`notifications.alerts.on`) or `notifications.level` is below the kind's level.
    `actions`: [{label, op, params, style?, kind?: op|local|ask_code, text?, min_role?}]. Returns the record or None when suppressed."""
    c = cfg(p)
    if c["on"] and kind not in c["on"] and kind != "answer":
        return None
    st, now = _load(p), _now()
    if not allowed(p, kind):
        if once and key not in (st.setdefault("keys", {})):      # remember it, so raising the level later does not replay old events
            st["keys"][key] = {"ts": now, "open": False, "kind": kind, "task": task, "level_skipped": True}
            _atomic_write(_state_path(p), json.dumps(st, indent=1) + "\n")
        return None
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
                       "files": files or [], "task": task, "decision": decision, "clear": False, "project_root": p.root,
                       **({"chat": chat, "platform": platform} if chat else {})})


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
