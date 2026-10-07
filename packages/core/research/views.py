"""Read-side aggregations: detail views (for UI/CLI) and generated mirror tails."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import schema
from .runs import get_artifact, list_artifacts, list_runs, summary_metrics
from .schema import normalize_id
from .services import experiment_state, list_syntheses, notes_linked_to
from .store import Project
from .util import NotFound, ResearchError


def _brief(p: Project, id_: str) -> Dict[str, Any]:
    """Tiny {id,type,title,status} card for any id; never raises."""
    try:
        t = schema.type_of(id_)
    except ResearchError:
        return {"id": id_, "type": "unknown", "title": id_, "missing": True}
    try:
        if t in schema.ENTITIES:
            r = p.q1(f"SELECT * FROM {schema.ENTITIES[t]['table']} WHERE id=?", (id_,))
            if not r:
                raise NotFound(id_)
            out = {"id": id_, "type": t, "title": r.get("title") or r.get("statement") or id_, "status": r.get("status")}
            if t == "finding":
                out.update(kind=r.get("kind"), confidence=r.get("confidence"))
            return out
        if t == "run":
            r = p.q1("SELECT id,label,status,experiment_id,command,exit_code FROM runs WHERE id=?", (id_,))
            if not r:
                raise NotFound(id_)
            return {"id": id_, "type": t, "title": r["label"] or (r["command"] or "")[:80] or id_,
                    "status": r["status"], "experiment_id": r["experiment_id"], "exit_code": r["exit_code"]}
        if t == "artifact":
            a = get_artifact(p, id_)
            return {"id": id_, "type": t, "title": a["name"], "status": None, "artifact_type": a["type"],
                    "path": a["path"], "abspath": a["abspath"], "exists": a["exists"], "run_id": a["run_id"],
                    "experiment_id": a["experiment_id"]}
        if t == "note":
            from .services import list_notes
            n = next((n for n in list_notes(p) if n["id"] == id_), None)
            if not n:
                raise NotFound(id_)
            return {"id": id_, "type": t, "title": n["title"], "path": n["path"]}
    except NotFound:
        return {"id": id_, "type": t, "title": id_, "missing": True}
    return {"id": id_, "type": t, "title": id_}


def run_view(p: Project, run: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(run)
    r["metrics"] = {k: {"value": v["value"], "text": v["value_text"], "step": v["step"], "unit": v["unit"]}
                    for k, v in summary_metrics(p, run["id"]).items()}
    r["artifacts"] = list_artifacts(p, run=run["id"])
    rd = p.abspath(run["run_dir"])
    r["has_diff"] = bool(rd and os.path.exists(os.path.join(rd, "git.diff")))
    for k in ("stdout_path", "stderr_path"):
        ap = p.abspath(run.get(k))
        r[k + "_abs"] = ap
        r[k + "_exists"] = bool(ap and os.path.exists(ap))
    r["run_dir_abs"] = rd
    return r


def experiment_detail(p: Project, exp_id: str) -> Dict[str, Any]:
    exp_id = normalize_id(exp_id, "EXP")
    e = p.get("experiment", exp_id)
    e["state"] = experiment_state(p, exp_id)
    e["question"] = _brief(p, e["question_id"]) if e.get("question_id") else None
    e["parent"] = _brief(p, e["parent_id"]) if e.get("parent_id") else None
    e["children"] = [_brief(p, r["id"]) for r in p.q("SELECT id FROM experiments WHERE parent_id=? ORDER BY id", (exp_id,))]
    runs = [run_view(p, r) for r in sorted(list_runs(p, experiment=exp_id), key=lambda r: r["id"])]
    e["runs"] = runs
    names: List[str] = []
    for n in (e.get("metrics_requested") or []):
        if n not in names:
            names.append(n)
    for r in runs:
        for n in r["metrics"]:
            if n not in names:
                names.append(n)
    e["metric_names"] = names
    e["artifacts"] = list_artifacts(p, experiment=exp_id)
    e["syntheses"] = list_syntheses(p, exp_id)
    fids = {l["src_id"] for l in p.q(
        "SELECT src_id FROM links WHERE src_type='finding' AND (dst_id=? OR dst_id IN (SELECT id FROM runs WHERE experiment_id=?) "
        "OR dst_id IN (SELECT id FROM artifacts WHERE experiment_id=?))", (exp_id, exp_id, exp_id))}
    e["findings"] = [_brief(p, f) for f in sorted(fids)]
    e["decisions"] = [_brief(p, l["src_id"]) for l in p.links_to(exp_id) if l["src_type"] == "decision"]
    e["linked_notes"] = notes_linked_to(p, exp_id)
    param_keys: List[str] = []
    for r in runs:
        for k in (r.get("parameters") or {}):
            if k not in param_keys:
                param_keys.append(k)
    e["run_param_names"] = param_keys
    e["events"] = p.q("SELECT * FROM events WHERE entity_id=? OR entity_id IN (SELECT id FROM runs WHERE experiment_id=?) "
                      "ORDER BY id DESC LIMIT 30", (exp_id, exp_id))
    return e


def finding_detail(p: Project, fid: str) -> Dict[str, Any]:
    fid = normalize_id(fid, "F")
    f = p.get("finding", fid)
    L = f["links"]
    f["evidence"] = {k: [_brief(p, i) for i in L.get(k, [])] for k in ("supports", "contradicts", "related")}
    f["questions"] = [_brief(p, i) for i in L.get("questions", [])]
    exps = set()
    arts = []
    for b in f["evidence"]["supports"] + f["evidence"]["contradicts"]:
        if b["type"] == "experiment":
            exps.add(b["id"])
        elif b["type"] == "run" and b.get("experiment_id"):
            exps.add(b["experiment_id"])
        elif b["type"] == "artifact":
            arts.append(get_artifact(p, b["id"]) if not b.get("missing") else b)
            if b.get("experiment_id"):
                exps.add(b["experiment_id"])
    f["experiments"] = [_brief(p, i) for i in sorted(exps)]
    f["artifacts"] = arts
    f["decisions"] = [_brief(p, l["src_id"]) for l in p.links_to(fid) if l["src_type"] == "decision"]
    f["referenced_by"] = [_brief(p, l["src_id"]) for l in p.links_to(fid) if l["src_type"] == "finding"]
    f["superseded_by_card"] = _brief(p, f["superseded_by"]) if f.get("superseded_by") else None
    f["supersedes"] = [_brief(p, r["id"]) for r in p.q("SELECT id FROM findings WHERE superseded_by=?", (fid,))]
    f["linked_notes"] = notes_linked_to(p, fid)
    f["events"] = p.q("SELECT * FROM events WHERE entity_id=? ORDER BY id DESC LIMIT 20", (fid,))
    return f


def question_detail(p: Project, qid: str) -> Dict[str, Any]:
    qid = normalize_id(qid, "Q")
    q = p.get("question", qid)
    q["parent"] = _brief(p, q["parent_id"]) if q.get("parent_id") else None
    q["children"] = [_brief(p, r["id"]) for r in p.q("SELECT id FROM questions WHERE parent_id=? ORDER BY id", (qid,))]
    exps = p.q("SELECT id FROM experiments WHERE question_id=? ORDER BY id", (qid,))
    q["experiments"] = []
    for r in exps:
        b = _brief(p, r["id"])
        b["state"] = experiment_state(p, r["id"])
        q["experiments"].append(b)
    fids = {l["src_id"] for l in p.links_to(qid) if l["src_type"] == "finding"}
    exp_ids = [r["id"] for r in exps]
    for e in exp_ids:
        fids |= {l["src_id"] for l in p.q(
            "SELECT src_id FROM links WHERE src_type='finding' AND (dst_id=? OR dst_id IN (SELECT id FROM runs WHERE experiment_id=?))",
            (e, e))}
    q["findings"] = [_brief(p, f) for f in sorted(fids)]
    q["linked_notes"] = notes_linked_to(p, qid)
    q["events"] = p.q("SELECT * FROM events WHERE entity_id=? ORDER BY id DESC LIMIT 20", (qid,))
    return q


def decision_detail(p: Project, did: str) -> Dict[str, Any]:
    did = normalize_id(did, "D")
    d = p.get("decision", did)
    d["findings"] = [_brief(p, i) for i in d["links"].get("supporting_findings", [])]
    d["experiments"] = [_brief(p, i) for i in d["links"].get("experiments", [])]
    d["superseded_by_card"] = _brief(p, d["superseded_by"]) if d.get("superseded_by") else None
    d["linked_notes"] = notes_linked_to(p, did)
    d["events"] = p.q("SELECT * FROM events WHERE entity_id=? ORDER BY id DESC LIMIT 20", (did,))
    return d


def checkpoint_detail(p: Project, cid: str) -> Dict[str, Any]:
    cid = normalize_id(cid, "CP")
    c = p.get("checkpoint", cid)
    c["cards"] = {
        "findings": [_brief(p, i) for i in c.get("finding_ids") or []],
        "failures": [_brief(p, i) for i in c.get("failure_ids") or []],
        "questions": [_brief(p, i) for i in c.get("question_ids") or []],
        "experiments": [_brief(p, i) for i in c.get("experiment_ids") or []],
        "baseline": _brief(p, c["baseline_experiment_id"]) if c.get("baseline_experiment_id") else None,
    }
    c["path"] = p.rel(p.mirror_path("checkpoint", cid))
    return c


def run_detail(p: Project, run_id: str) -> Dict[str, Any]:
    from .runs import get_run, metrics_for_run
    r = run_view(p, get_run(p, run_id))
    r["experiment"] = _brief(p, r["experiment_id"])
    r["metric_series"] = metrics_for_run(p, r["id"])
    r["findings"] = [_brief(p, l["src_id"]) for l in p.links_to(r["id"]) if l["src_type"] == "finding"]
    return r


def artifact_detail(p: Project, aid: str) -> Dict[str, Any]:
    a = get_artifact(p, aid)
    a["findings"] = [_brief(p, l["src_id"]) for l in p.links_to(a["id"]) if l["src_type"] == "finding"]
    a["run"] = _brief(p, a["run_id"]) if a.get("run_id") else None
    a["experiment"] = _brief(p, a["experiment_id"]) if a.get("experiment_id") else None
    return a


def plan_detail(p: Project, plan_id: str) -> Dict[str, Any]:
    """Get detailed plan view with tasks and statistics."""
    from . import plans as _plans
    return _plans.get_plan_with_tasks(p, plan_id)


def task_detail(p: Project, task_id: str) -> Dict[str, Any]:
    """Get detailed task view with dependencies and related entities."""
    from . import plans as _plans
    task = _plans.get_task_with_deps(p, task_id)

    # Add related entity details
    task["plan"] = _brief(p, task["plan_id"]) if task.get("plan_id") else None
    task["question"] = _brief(p, task["related_question_id"]) if task.get("related_question_id") else None
    task["experiment"] = _brief(p, task["related_experiment_id"]) if task.get("related_experiment_id") else None

    # Resolve artifacts
    arts = task.get("artifacts") or []
    task["artifact_cards"] = [_brief(p, a) for a in arts]

    task["events"] = p.q("SELECT * FROM events WHERE entity_id=? ORDER BY id DESC LIMIT 20", (task["id"],))
    return task


def show(p: Project, id_: str) -> Dict[str, Any]:
    t = schema.type_of(id_)
    if t == "note":
        from .services import list_notes
        n = next((n for n in list_notes(p) if n["id"] == id_), None)
        if not n:
            raise NotFound(f"Note {id_} not found")
        return n
    id_ = normalize_id(id_)
    return {"experiment": experiment_detail, "finding": finding_detail, "question": question_detail,
            "decision": decision_detail, "checkpoint": checkpoint_detail, "run": run_detail,
            "artifact": artifact_detail, "plan": plan_detail, "task": task_detail}[t](p, id_)


# =============================================================================== mirror tails

def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _md_cell(s: Any) -> str:
    return _fmt(s).replace("|", "\\|").replace("\n", " ")


def mirror_tail(p: Project, etype: str, id_: str) -> str:
    try:
        if etype == "experiment":
            return _experiment_tail(p, id_)
        if etype == "finding":
            f = finding_detail(p, id_)
            L = ["## Evidence (resolved)", ""]
            for k in ("supports", "contradicts", "related"):
                for b in f["evidence"][k]:
                    L.append(f"- *{k}* **{b['id']}** {b['title']}" + (" ⚠ missing" if b.get("missing") else ""))
            if f["decisions"]:
                L += ["", "## Decisions based on this finding", ""] + [f"- **{d['id']}** {d['title']} ({d['status']})" for d in f["decisions"]]
            L += ["", f"_Author: {f.get('author_type')}" + (f" ({f['author_name']})" if f.get("author_name") else "") + "_"]
            return "\n".join(L)
        if etype == "question":
            q = question_detail(p, id_)
            L = []
            if q["experiments"]:
                L += ["## Experiments", ""] + [f"- **{e['id']}** {e['title']} — {e['status']}" for e in q["experiments"]]
            if q["findings"]:
                L += ["", "## Findings", ""] + [f"- **{f['id']}** {f['title']} ({f['status']})" for f in q["findings"]]
            return "\n".join(L)
        if etype in ("decision", "checkpoint"):
            r = p.get(etype, id_)
            by = r.get("author_type") + (f" ({r['author_name']})" if r.get("author_name") else "")
            extra = ""
            if etype == "checkpoint":
                extra = f"Git: `{r.get('git_branch') or '—'}` @ `{(r.get('git_commit') or '—')[:10]}`" + (" (dirty)" if r.get("git_dirty") else "") + "  \n"
            return extra + f"_Author: {by} · created {r.get('created_at')}_"
        if etype == "plan":
            return _plan_tail(p, id_)
        if etype == "task":
            return _task_tail(p, id_)
    except Exception as e:  # never break writes because of a view bug
        return f"_(could not render generated section: {e})_"
    return ""


def _plan_tail(p: Project, plan_id: str) -> str:
    """Generate tail for plan mirror showing task summary."""
    plan = plan_detail(p, plan_id)
    L = [f"## Tasks ({plan['tasks_done']}/{plan['task_count']} done)", ""]

    if plan.get("tasks"):
        for t in plan["tasks"]:
            status_icon = {
                "done": "✓", "running": "⏳", "blocked": "⛔",
                "verify": "🔍", "todo": "○"
            }.get(t["status"], "○")

            deps = t.get("depends_on") or []
            dep_str = f" (deps: {', '.join(deps)})" if deps else ""
            L.append(f"- {status_icon} **{t['id']}** {t['title']} — {t['status']}{dep_str}")

        if plan["tasks_blocked"]:
            L.append(f"\n⚠ {plan['tasks_blocked']} task(s) blocked")
    else:
        L.append("_(no tasks yet)_")

    L.append(f"\n_Author: {plan.get('author_type')}" +
             (f" ({plan['author_name']})" if plan.get("author_name") else "") + "_")
    return "\n".join(L)


def _task_tail(p: Project, task_id: str) -> str:
    """Generate tail for task mirror showing status and related entities."""
    task = task_detail(p, task_id)
    L = ["## Status", ""]

    # Ready status (derived)
    if task.get("is_ready"):
        L.append("**Ready to start** — all dependencies satisfied")
    else:
        L.append(f"**{task['status']}**")

    # Dependencies
    deps = task.get("dependencies") or []
    if deps:
        L.append("\n### Dependencies")
        for d in deps:
            status_icon = "✓" if d["status"] == "done" else "○"
            L.append(f"- {status_icon} **{d['id']}** {d['title']} ({d['status']})")

    # Related entities
    if task.get("plan"):
        L.append(f"\n**Plan:** {task['plan']['id']} {task['plan']['title']}")
    if task.get("question"):
        L.append(f"**Question:** {task['question']['id']} {task['question']['title']}")
    if task.get("experiment"):
        L.append(f"**Experiment:** {task['experiment']['id']} {task['experiment']['title']}")

    # Artifacts
    if task.get("artifact_cards"):
        L.append("\n### Artifacts")
        for a in task["artifact_cards"]:
            L.append(f"- **{a['id']}** {a['title']}")

    L.append(f"\n_Author: {task.get('author_type')}" +
             (f" ({task['author_name']})" if task.get("author_name") else "") + "_")
    return "\n".join(L)


def _experiment_tail(p: Project, exp_id: str) -> str:
    e = experiment_detail(p, exp_id)
    st = e["state"]
    L = [f"## Status", "",
         f"**{e['status']}** · {st['runs']} runs ({st['completed']} completed, {st['failed']} failed, {st['active']} active)"
         + (f" · ⚠ {st['unsynthesized']} unsynthesized" if st["unsynthesized"] else ""),
         f"Git at registration: `{e.get('git_branch') or '—'}` @ `{(e.get('git_commit') or '—')[:10]}`"
         + (" (dirty)" if e.get("git_dirty") else "")]
    if e.get("param_delta"):
        L.append("Delta vs parent: " + ", ".join(f"`{k}`: {d.get('from')} → {d.get('to')}" for k, d in e["param_delta"].items()))
    if e["runs"]:
        pn, mn = e["run_param_names"], e["metric_names"]
        hdr = ["Run", "Status", "Exit"] + pn + mn + ["Label"]
        L += ["", "## Runs", "", "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
        for r in e["runs"]:
            row = [r["id"], r["status"], _fmt(r.get("exit_code"))]
            row += [_md_cell((r.get("parameters") or {}).get(k)) for k in pn]
            row += [_md_cell((r["metrics"].get(k) or {}).get("value", (r["metrics"].get(k) or {}).get("text"))) for k in mn]
            row += [_md_cell(r.get("label") or "")]
            L.append("| " + " | ".join(row) + " |")
    if e["artifacts"]:
        L += ["", "## Artifacts", ""] + [f"- **{a['id']}** `{a['path']}` ({a['type']})" + (f" — {a['description']}" if a.get("description") else "")
                                         for a in e["artifacts"]]
    for s in e["syntheses"]:
        L += ["", f"## Synthesis {s['id'].split('/')[-1]} — {s['created_at'][:10]} ({s['author_type']}"
              + (f": {s['author_name']}" if s.get("author_name") else "") + ")", ""]
        for key, label in (("what_happened", "What happened"), ("what_worked", "What worked"), ("what_failed", "What did not work"),
                           ("interpretation", "Interpretation"), ("limitations", "Limitations"),
                           ("unresolved", "Unresolved"), ("next_experiment", "Recommended next")):
            if s.get(key):
                L.append(f"- **{label}:** {s[key]}")
    if e["findings"]:
        L += ["", "## Findings produced", ""] + [f"- **{f['id']}** {f['title']} ({f['status']})" for f in e["findings"]]
    return "\n".join(L)
