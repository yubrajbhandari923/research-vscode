"""The plan board: one picture of every plan, built from the plan file's structure and the supervisor's state. `research plan status` and `research watch`
render it; chat and the RPC op read the same data, so "what needs me" has one definition.

For every item that is not moving the board says why, who moves next (the supervisor or you), and the exact command when it is you."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import archive as AR, planfile as PF, planspec as PS, supervisor as SV
from .store import Project
from .util import ResearchError

MARK = {"verified": "done", "running": "run", "verifying": "run", "waiting_hpc": "run", "repair_pending": "run", "ready": "wait", "waiting": "wait",
        "waiting_provider": "wait", "awaiting_approval": "you", "blocked": "you", "attention": "you", "takeover": "you", "retired": "off"}


def next_steps(plan: str, t: Dict[str, Any]) -> List[Dict[str, str]]:
    """What a person can do about a task that waits for them: the first entry is the usual answer."""
    tid, ph = t["id"], t["phase"]
    if ph == "awaiting_approval":
        return [{"cmd": f"research supervisor approve {tid}", "what": "let it start"}]
    if ph == "takeover":
        return [{"cmd": f"research task intervene {tid} release", "what": "hand it back; your work is verified with the task's checks"}]
    if ph not in ("blocked", "attention"):
        return []
    if t.get("block_kind") == "task_budget":
        return [{"cmd": f"research bump {tid}", "what": "3 more attempts, retried now"},
                {"cmd": f"research why {tid}", "what": "what keeps failing"}]
    if t.get("block_kind") == "plan_budget":
        return [{"cmd": f"research plan set {plan} execution.max_total_attempts=N", "what": "raise the plan's attempt budget; blocked tasks continue by themselves"}]
    return [{"cmd": f"research why {tid}", "what": "diagnosis with numbered ways forward"},
            {"cmd": f"research tell {tid} \"...\"", "what": "say what to do in words"},
            {"cmd": f"research retry {tid}", "what": "try again as it is"}]


def plan(p: Project, ref: str) -> Dict[str, Any]:
    from . import autopromote as AP
    s = SV.status(p, ref)
    st = PS._state_for(p, ref)
    rs = SV.load_run(p, st["plan_id"]) or {}
    try:
        s["file"] = PF.info(p, st["plan_key"])
    except (ResearchError, OSError):
        s["file"] = None
    exps = {e["key"]: {"key": e["key"], "id": st["experiments"].get(e["key"]), "title": e.get("title") or e["key"], "tasks": [],
                       "archived": AR.is_archived(p, st["experiments"].get(e["key"]))} for e in st["spec"].get("experiments", [])}
    loose = {"key": None, "id": None, "title": "Plan tasks", "tasks": [], "archived": False}
    need: List[Dict[str, Any]] = []
    for t in s["tasks"]:
        if t.get("reason"):       # one readable line; the "how to continue" tail of a budget stop is shown as a command below instead
            t["reason"] = " ".join(str(t["reason"]).split()).split(" More attempts: `")[0].split(" Raise it: `")[0]
        t["mark"] = MARK.get(t["phase"], "wait")
        t["next"] = next_steps(st["plan_key"], t)
        (exps.get(t.get("experiment")) or loose)["tasks"].append(t)
        if t["mark"] == "you":
            need.append({"ref": t["id"], "title": t["title"], "why": t.get("reason") or t["phase"], "next": t["next"]})
    items = {i["id"]: i for i in ((rs.get("autopromote") or {}).get("items") or [])}
    promos = []
    for it in (s.get("auto_promotion") or {}).get("items") or []:
        full = items.get(it["id"]) or it
        row = {"id": it["id"], "experiment": it.get("experiment"), "status": it["status"], "error": it.get("error"), "reviewer": AP.reviewer_status(p, full) if it["status"] == "awaiting_review" else None, "next": []}
        if it["status"] in ("paused", "suspended"):
            row["next"] = [{"cmd": f"research why {it['id']}", "what": "which gate stopped it"},
                           {"cmd": f"research plan autopromote {st['plan_key']} retry --item {it['id']}", "what": "prepare a new candidate from the same verified work"}]
            need.append({"ref": it["id"], "title": f"promotion of {it.get('experiment')}", "why": it.get("error") or it["status"], "next": row["next"]})
        promos.append(row)
    f = s.get("file") or {}
    if f.get("problem"):
        need.append({"ref": f["path"], "title": "the plan file cannot be applied", "why": "; ".join(f["problem"][:3]), "next": [{"cmd": f"research plan edit {st['plan_key']}", "what": "fix it; the plan runs on its last good version meanwhile"}]})
    elif f.get("held"):
        need.append({"ref": f["path"], "title": "plan-file changes wait for you", "why": "; ".join(f["held"][:4]), "next": [{"cmd": f"research plan sync {st['plan_key']}", "what": "apply them"}]})
    for pr in s.get("proposals") or []:
        need.append({"ref": pr["id"], "title": "a proposal waits for approval", "why": (pr.get("rationale") or "")[:200], "next": [{"cmd": f"research proposal show {pr['id']}", "what": "read it, then approve or reject"}]})
    s.update(experiments=[e for e in list(exps.values()) + [loose] if e["tasks"]], promotions=promos, needs_you=need, archived=bool(st.get("archived")))
    return s


def data(p: Project, ref: Optional[str] = None, include_archived: bool = False) -> Dict[str, Any]:
    if ref:
        plans = [plan(p, ref)]
    else:
        plans = []
        for st in sorted(PS.all_states(p), key=lambda s: s.get("created_at") or ""):
            if st.get("archived") and not include_archived:
                continue
            try:
                plans.append(plan(p, st["plan_key"]))
            except (ResearchError, OSError, KeyError):
                continue
    hidden = 0 if include_archived or ref else len([s for s in PS.all_states(p) if s.get("archived")])
    return {"plans": plans, "archived_hidden": hidden, "daemon": SV.daemon_status(p), "limits": SV.limits_status(p),
            "needs_you": [{**n, "plan": pl["plan"]} for pl in plans for n in pl["needs_you"]]}
