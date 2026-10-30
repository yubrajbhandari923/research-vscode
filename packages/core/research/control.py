"""Aggregations for the Control Center (Home / Work / Research). Presentation-free: the frontend only renders these.

Every attention item names the page and target to open, so a click goes straight to the workflow that resolves it."""
from __future__ import annotations

from typing import Any, Dict, List

from . import plans as P, services, workspace as W
from .util import jload


def _attention(p) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(kind, id_, title, detail, page, target=None, severity="info"):
        items.append({"kind": kind, "id": id_, "title": title, "detail": detail, "severity": severity,
                      "open": {"page": page, "target": target or id_}})

    for e in p.list("experiment"):
        if e["status"] in ("completed", "failed", "abandoned", "cancelled"):
            continue
        st = services.experiment_state(p, e["id"])
        if st["over_run_budget"]:
            add("synthesis", e["id"], f"{e['id']} reached its run limit", f"{st['unsynthesized']} finished runs are not yet synthesized; "
                f"further runs are blocked (limit {st['max_runs_without_synthesis']}).", "research", e["id"], "warn")
        elif st["needs_synthesis"]:
            add("synthesis", e["id"], f"{e['id']} needs synthesis", f"{st['unsynthesized']} finished runs have not been interpreted.", "research", e["id"])
        if st["unreviewed_failed"]:
            add("review", e["id"], f"{st['unreviewed_failed']} failed run(s) unreviewed in {e['id']}", e["title"], "research", e["id"], "warn")
    for f in p.list("finding"):
        if f["status"] == "preliminary" and not f.get("reviewed"):
            add("review", f["id"], f"{f['id']} awaits independent review", f["title"], "research", f["id"])
    for pl in P.list_plans(p, status="active"):
        for t in P.list_tasks(p, plan_id=pl["id"]):
            if t["status"] == "blocked":
                add("blocked", t["id"], f"{t['id']} is blocked", t.get("title") or "", "work", t["id"], "warn")
    try:
        from . import hooks
        for h in hooks.listing(p):
            if h.get("enabled") and not h.get("approved"):
                add("approval", h["name"], f"Hook {h['name']} needs your approval", "It cannot run until you approve the exact command.", "settings", "hooks", "warn")
    except Exception:
        pass
    for w in W.list_workspaces(p) if p.location.linked else []:
        if not w["present"]:
            add("workspace", w["id"], f"Workspace {w['id']} directory is missing", w.get("branch") or "", "work", w["id"], "warn")
    try:
        from . import promotion
        for c in promotion.list_candidates(p):
            if c["state"] in ("prepared", "verified", "reviewed", "approved"):
                add("promotion", c["id"], f"Promotion {c['id']} is {c['state']}", c.get("title") or "", "work", c["id"])
    except ImportError:
        pass
    order = {"warn": 0, "info": 1}
    return sorted(items, key=lambda i: order.get(i["severity"], 2))


def home(p) -> Dict[str, Any]:
    from . import context as C
    h = C.research_home(p)
    running = [dict(r) for r in p.q("SELECT id, experiment_id, label, status, started_at, backend FROM runs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 20")]
    findings = [f for f in p.list("finding", order="created_at DESC, id DESC")][:6]
    return {"project": {"name": p.name, "goal": p.config["project"].get("goal"), "layout": W.info(p)["mode"]},
            "focus": h.get("current_focus"), "next_task": h.get("next_task"), "attention": _attention(p), "running": running,
            "learned": [{"id": f["id"], "title": f["title"], "status": f["status"], "confidence": f.get("confidence")} for f in findings],
            "settings_fingerprint": __import__("research.registry", fromlist=["x"]).fingerprint(p)}


def work(p) -> Dict[str, Any]:
    from . import sandbox as SB
    plans = []
    for pl in P.list_plans(p):
        ts = P.list_tasks(p, plan_id=pl["id"])
        plans.append({"id": pl["id"], "title": pl["title"], "status": pl["status"], "tasks": [
            {"id": t["id"], "title": t["title"], "status": t["status"], "assigned_role": t.get("assigned_role"),
             "depends_on": t.get("depends_on") or []} for t in ts]})
    runs = [dict(r) for r in p.q("SELECT id, experiment_id, status, backend, hostname, slurm_job_id, started_at FROM runs ORDER BY id DESC LIMIT 30")]
    wss = W.list_workspaces(p) if p.location.linked else []
    from . import promotion
    return {"plans": plans, "runs": runs, "workspaces": wss, "layout": W.info(p),
            "promotions": promotion.list_candidates(p) if p.location.linked else [],
            "execution": {"profiles": [SB.summary(p, n) for n in SB.profile_names(p)],
                          "default": p.config["sandbox"].get("default_profile"), "applies": bool(p.config["sandbox"].get("enabled")),
                          "agent_boundary": SB.agent_boundary(p)}}


def research(p) -> Dict[str, Any]:
    exps = []
    for e in p.list("experiment", order="id DESC"):
        st = services.experiment_state(p, e["id"])
        exps.append({"id": e["id"], "title": e["title"], "status": e["status"], "question_id": e.get("question_id"),
                     "hypothesis": e.get("hypothesis"), "runs": st["runs"], "active": st["active"], "failed": st["failed"],
                     "unsynthesized": st["unsynthesized"], "needs_synthesis": st["needs_synthesis"]})
    return {"questions": [{"id": q["id"], "title": q["title"], "status": q["status"]} for q in p.list("question")],
            "experiments": exps,
            "findings": [{"id": f["id"], "title": f["title"], "status": f["status"], "kind": f.get("kind"), "confidence": f.get("confidence")}
                         for f in p.list("finding", order="id DESC")],
            "decisions": [{"id": d["id"], "title": d["title"], "status": d.get("status")} for d in p.list("decision", order="id DESC")],
            "checkpoints": [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]} for c in p.list("checkpoint", order="id DESC")[:5]]}
