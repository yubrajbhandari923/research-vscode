"""Archiving: take plans and experiments you no longer care about out of sight without deleting anything.

An archived plan or experiment keeps every record, run, artifact, finding, worktree and commit. It only stops appearing in the default lists, the plan
board and the things-that-need-you list, and the supervisor stops syncing its plan file. `research archive --list` shows what is archived and
`research unarchive ID` brings it back exactly as it was. Archiving a plan archives its experiments with it (and unarchiving restores those, not ones
archived separately). A plan that is running or paused must be stopped first: archiving never stops work by surprise.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from . import planspec as PS
from .store import Project, _atomic_write
from .schema import normalize_id
from .util import ResearchError, now_iso


def _path(p: Project) -> str:
    return p.path("archive.json")


def load(p: Project) -> Dict[str, Dict[str, Any]]:
    try:
        with open(_path(p), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(p: Project, d: Dict[str, Dict[str, Any]]) -> None:
    _atomic_write(_path(p), json.dumps(d, indent=1, sort_keys=True) + "\n")


def ids(p: Project) -> set:
    return set(load(p))


def is_archived(p: Project, ident: Optional[str]) -> bool:
    return bool(ident) and ident in load(p)


def visible(p: Project, rows: Iterable[Dict[str, Any]], include: bool = False, key: str = "id") -> List[Dict[str, Any]]:
    """`rows` without the archived ones (all of them with `include`, each marked `archived`)."""
    arch = load(p)
    out = []
    for r in rows:
        a = r.get(key) in arch
        if a and not include:
            continue
        out.append({**r, "archived": True} if a else r)
    return out


def _resolve(p: Project, ref: str) -> Dict[str, Any]:
    up = ref.upper()
    if up.startswith("EXP-") or up.startswith("E-"):
        eid = normalize_id(ref, "EXP")
        e = p.get("experiment", eid)
        return {"kind": "experiment", "id": eid, "title": e["title"]}
    st = None
    try:
        st = PS._state_for(p, ref)
    except ResearchError:
        st = None
    pid = (st or {}).get("plan_id") or (normalize_id(ref, "PLAN") if up.startswith("PLAN-") else None)
    if not pid or not p.exists("plan", pid):
        raise ResearchError(f"{ref} is not a plan or an experiment", hint="Use a plan key or id (PLAN-002) or an experiment id (EXP-004).")
    return {"kind": "plan", "id": pid, "title": p.get("plan", pid)["title"], "state": st}


def archive(p: Project, ref: str, reason: Optional[str] = None) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Archiving")
    t = _resolve(p, ref)
    d = load(p)
    if t["id"] in d:
        return {"id": t["id"], "kind": t["kind"], "archived": True, "changed": False, "also": []}
    also: List[str] = []
    rec = {"kind": t["kind"], "title": t["title"], "at": now_iso(), "reason": reason, "by": (p.author or {}).get("author_name")}
    if t["kind"] == "plan":
        st = t.get("state")
        rs = SV.load_run(p, t["id"]) if st else None
        if rs and rs.get("state") in ("running", "paused"):
            raise ResearchError(f"plan {st['plan_key']} is {rs['state']}; archiving never stops work",
                                hint=f"Stop it first (`research plan stop {st['plan_key']}`), then archive it.")
        if st:
            for eid in (st.get("experiments") or {}).values():
                if eid and eid not in d and p.exists("experiment", eid):
                    d[eid] = {"kind": "experiment", "title": p.get("experiment", eid)["title"], "at": rec["at"], "reason": reason, "via": t["id"], "by": rec["by"]}
                    also.append(eid)
            st = PS.load_state(p, st["plan_key"])
            st["archived"] = rec["at"]
            PS.save_state(p, st)
            rec["plan_key"] = st["plan_key"]
    d[t["id"]] = rec
    _save(p, d)
    p.event(t["kind"], t["id"], "archived", f"{t['id']} archived" + (f": {reason}" if reason else "") + (f" (with {', '.join(also)})" if also else ""))
    return {"id": t["id"], "kind": t["kind"], "archived": True, "changed": True, "also": also}


def unarchive(p: Project, ref: str) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Unarchiving")
    t = _resolve(p, ref)
    d = load(p)
    if t["id"] not in d:
        return {"id": t["id"], "kind": t["kind"], "archived": False, "changed": False, "also": []}
    d.pop(t["id"])
    also = [k for k, v in d.items() if v.get("via") == t["id"]]
    for k in also:
        d.pop(k)
    if t["kind"] == "plan" and t.get("state"):
        st = PS.load_state(p, t["state"]["plan_key"])
        st.pop("archived", None)
        PS.save_state(p, st)
    _save(p, d)
    p.event(t["kind"], t["id"], "unarchived", f"{t['id']} is back" + (f" (with {', '.join(also)})" if also else ""))
    return {"id": t["id"], "kind": t["kind"], "archived": False, "changed": True, "also": also}


def listing(p: Project) -> List[Dict[str, Any]]:
    return [{"id": k, **v} for k, v in sorted(load(p).items(), key=lambda kv: kv[1].get("at") or "")]
