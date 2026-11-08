"""Project, records, search, reports: the day-to-day record operations."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from .. import __version__
from .. import context as C
from .. import plans as P
from .. import runs as R
from .. import services as S
from .. import skills as SK
from .. import views as V
from ..schema import ENTITIES, STATUSES, normalize_id
from ..store import Project
from ..util import AGENT_ENV_STRIP, ResearchError, human_size
from . import op

PROTOCOL = 1


@op(needs_init=False, needs_project=False)
def ping(s) -> Dict[str, Any]:
    ok = s.initialized() and not s.compat()
    return {"version": __version__, "protocol": PROTOCOL, "root": s.root, "initialized": s.initialized(), "incompatible": s.compat(),
            "name": s.project(sync=False).name if ok else os.path.basename(s.root), "python": sys.executable, "pid": os.getpid(),
            "agent_env": list(AGENT_ENV_STRIP)}


@op(needs_init=False, needs_project=False, mutates=True)
def init(s, name: Optional[str] = None, goal: Optional[str] = None) -> Dict[str, Any]:
    s.p = Project.init(s.root, name=name, goal=goal)
    s.p.author = s.author
    return {"root": s.root, "name": s.p.name}


@op(sync=False)
def control(p, page: str) -> Dict[str, Any]:
    from .. import control as CT
    fn = {"home": CT.home, "work": CT.work, "research": CT.research}.get(page)
    if not fn:
        raise ResearchError(f"unknown control page {page!r}")
    return fn(p)


@op()
def resume(p) -> Dict[str, Any]:
    return C.resume_context(p)


@op()
def home(p) -> Dict[str, Any]:
    """Research Home aggregation - primary human landing page data."""
    return C.research_home(p)


@op()
def tree(p) -> Dict[str, Any]:
    """Everything the sidebar trees need, in one round-trip."""
    qs = p.list("question", order="id")
    exps = p.list("experiment", order="id DESC")
    runs = p.q("SELECT id, experiment_id, status, label, command, exit_code, backend, slurm_job_id, started_at, "
               "ended_at, parameters, author_type, author_name FROM runs ORDER BY id")
    runs_by: Dict[str, list] = {}
    for r in runs:
        r["parameters"] = json.loads(r["parameters"] or "{}")
        runs_by.setdefault(r["experiment_id"], []).append(r)
    arts = R.list_artifacts(p)
    arts_by_run: Dict[str, list] = {}
    arts_by_exp: Dict[str, list] = {}
    for a in arts:
        a["size_h"] = human_size(a.get("size"))
        if a.get("run_id"):
            arts_by_run.setdefault(a["run_id"], []).append(a)
        elif a.get("experiment_id"):
            arts_by_exp.setdefault(a["experiment_id"], []).append(a)
    for e in exps:
        e["state"] = S.experiment_state(p, e["id"])
        e["runs"] = runs_by.get(e["id"], [])
        for r in e["runs"]:
            r["artifacts"] = arts_by_run.get(r["id"], [])
        e["artifacts"] = arts_by_exp.get(e["id"], [])
    plans = P.list_plans(p)
    for plan in plans:
        plan["tasks"] = P.list_tasks(p, plan_id=plan["id"])
    return {
        "project": {"name": p.name, "goal": p.config["project"].get("goal")},
        "questions": qs, "experiments": exps,
        "findings": p.list("finding", order="updated_at DESC"),
        "decisions": p.list("decision", order="date DESC, id DESC"),
        "checkpoints": p.list("checkpoint", order="created_at DESC, id DESC"),
        "notes": S.list_notes(p), "artifacts": arts,
        "skills": SK.list_skills(p),
        "agent": {k: S.list_agent_files(p, k) for k in ("context", "prompts", "templates")},
        "needs_synthesis": [e["id"] for e in S.experiments_needing_synthesis(p)],
        "plans": plans,
    }


@op(sync=False)
def index(p) -> Dict[str, Any]:
    """Id/title list for pickers."""
    out = []
    for et, spec in ENTITIES.items():
        cols = "id, title, status" if et in STATUSES else "id, title, NULL AS status"
        for r in p.q(f"SELECT {cols} FROM {spec['table']} ORDER BY id"):
            out.append({"id": r["id"], "type": et, "title": r["title"], "status": r["status"]})
    for r in p.q("SELECT id, label, command, status, experiment_id FROM runs ORDER BY id"):
        out.append({"id": r["id"], "type": "run", "title": r["label"] or (r["command"] or "")[:60],
                    "status": r["status"], "experiment_id": r["experiment_id"]})
    for r in p.q("SELECT id, name, path FROM artifacts ORDER BY id"):
        out.append({"id": r["id"], "type": "artifact", "title": r["name"], "path": r["path"]})
    return {"items": out, "statuses": STATUSES}


@op(mcp="research_show")
def show(p, id: str) -> Dict[str, Any]:
    return V.show(p, id if id.startswith("note:") else normalize_id(id))


@op(sync=False)
def config(p) -> Dict[str, Any]:
    return p.load_config()


@op(sync=False, mutates=True)
def update_project(p, **kw: Any) -> Dict[str, Any]:
    return p.update_project(**kw)


@op(mutates=True)
def create(p, type: str, **kw: Any) -> Dict[str, Any]:
    fn = {"question": S.create_question, "experiment": S.create_experiment, "variant": S.create_variant,
          "finding": S.create_finding, "decision": S.create_decision, "checkpoint": C.create_checkpoint,
          "note": S.create_note, "skill": S.create_skill, "plan": P.create_plan, "task": P.create_task}
    if type not in fn:
        raise ResearchError(f"cannot create {type!r}")
    pin = kw.pop("pin", None)
    if type == "experiment":
        from .. import governance
        governance.confirm_human_experiment(p, pin)
    return fn[type](p, **kw)


@op(mutates=True)
def update(p, type: str, id: str, **kw: Any) -> Dict[str, Any]:
    fn = {"question": S.update_question, "experiment": S.update_experiment, "finding": S.update_finding,
          "decision": S.update_decision, "plan": P.update_plan, "task": P.update_task}.get(type)
    if not fn:
        raise ResearchError(f"cannot update {type!r}")
    return fn(p, id, **kw)


@op(mutates=True)
def set_status(p, id: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    return S.set_status(p, id, status, reason)


@op(mutates=True)
def set_baseline(p, id: Optional[str] = None) -> Any:
    return S.set_baseline(p, id)


@op(mutates=True)
def synthesize(p, id: str, **kw: Any) -> Dict[str, Any]:
    return S.synthesize(p, id, **kw)


@op(mutates=True)
def supersede_finding(p, id: str, by: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    return S.supersede_finding(p, id, by, reason)


@op(sync=False)
def duplicate_skill(p, src: str, name: str) -> Dict[str, Any]:
    return S.duplicate_skill(p, src, name)


@op(sync=False)
def pin_note(p, id: str, pinned: bool = True) -> Dict[str, Any]:
    return S.pin_note(p, id, pinned)


@op(sync=False)
def link_note(p, id: str, targets: Any, remove: bool = False) -> Dict[str, Any]:
    return S.link_note(p, id, targets, remove)


@op()
def checkpoint_draft(p) -> Dict[str, Any]:
    return C.checkpoint_draft(p)


@op()
def experiment_state(p, id: str) -> Dict[str, Any]:
    return S.experiment_state(p, normalize_id(id, "EXP"))


@op()
def check_run_budget(p, experiment: str) -> Dict[str, Any]:
    st = S.experiment_state(p, normalize_id(experiment, "EXP"))
    msgs = []
    if st["over_run_budget"]:
        msgs.append(f"{st['unsynthesized']} unsynthesized runs (limit {st['max_runs_without_synthesis']})")
    if st["over_failure_budget"]:
        msgs.append(f"{st['unreviewed_failed']} failed runs without review (limit {st['max_failed_runs_without_review']})")
    return {"ok": not msgs, "messages": msgs, "state": st}


@op()
def write_context(p) -> Dict[str, Any]:
    return {"path": C.write_current_md(p)}


@op(sync=False, mutates=True)
def sync(p) -> Dict[str, Any]:
    return p.sync()


@op(sync=False, mutates=True)
def rebuild(p) -> Dict[str, Any]:
    return p.rebuild_index(backup=True)


@op(sync=False)
def events(p, limit: int = 50) -> Any:
    return p.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),))


@op()
def search(p, query: str, type: Any = None, limit: int = 30) -> Any:
    from ..search import search as _search
    return _search(p, query, type, limit)


@op(mcp="runs_compare")
def compare_runs(p, a: str, b: str) -> Dict[str, Any]:
    from .. import compare as CMP
    return CMP.compare_runs(p, a, b)


@op(mutates=True, mcp="report")
def report(p, scope: Optional[str] = None, fmt: str = "both") -> Dict[str, Any]:
    from .. import report as RP
    return RP.write_report(p, scope or None, fmt)


@op(sync=False)
def reports(p) -> Dict[str, Any]:
    """Reports already written under .research/reports (newest first), with absolute paths for the editor. Read-only."""
    d = p.path("reports")
    rows = []
    for f in (os.listdir(d) if os.path.isdir(d) else []):
        if f.endswith((".md", ".html")):
            st = os.stat(os.path.join(d, f))
            rows.append({"name": f, "abs": os.path.join(d, f), "bytes": st.st_size, "modified": st.st_mtime, "full": f.endswith("-full.md")})
    return {"dir": d, "reports": sorted(rows, key=lambda r: -r["modified"])}


@op(needs_project=False, mcp="report_full")
def report_full(s, scope: Optional[str] = None, check: bool = True) -> Dict[str, Any]:
    """Deterministic full context export. Read-only: opened without sync so nothing is mutated."""
    from .. import fullreport as FR
    from . import ro
    p = ro(s)
    try:
        return FR.write(p, scope, check=check)
    finally:
        p.close()


@op(sync=False)
def new_experiment_preview(p, spec: Dict[str, Any]) -> Dict[str, Any]:
    return S.new_experiment_plan(p, spec)


@op()
def new_experiment(p, spec: Dict[str, Any], pin: Optional[str] = None) -> Dict[str, Any]:
    from .. import governance
    governance.confirm_human_experiment(p, pin)     # only asks when the optional setting is on
    return S.new_experiment(p, spec)


@op(mutates=True)
def experiment_check(p, id: str, request_synthesis: bool = False) -> Dict[str, Any]:
    from .. import expcheck
    return expcheck.check(p, id, request_synthesis)
