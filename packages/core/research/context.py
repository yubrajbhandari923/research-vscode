"""Resume view, checkpoints, and the agent-readable .research/context/current.md."""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Dict, List, Optional

from . import gitinfo
from .schema import normalize_id
from .services import baseline, experiment_state, experiments_needing_synthesis, list_notes, list_syntheses
from .store import Project, _atomic_write
from .util import as_list, now_iso, parse_iso
from .views import _brief, checkpoint_detail


def _age_days(ts: Optional[str]) -> Optional[float]:
    d = parse_iso(ts)
    if not d:
        return None
    return (_dt.datetime.now().astimezone() - d).total_seconds() / 86400.0


def _findings(p: Project, where: str, args: tuple = (), limit: int = 50) -> List[Dict[str, Any]]:
    rows = p.list("finding", where, args, order="CASE status WHEN 'supported' THEN 0 WHEN 'preliminary' THEN 1 ELSE 2 END, updated_at DESC")
    out = []
    for f in rows[:limit]:
        d = {k: f.get(k) for k in ("id", "title", "statement", "status", "confidence", "kind", "author_type",
                                   "author_name", "updated_at", "limitations")}
        d["evidence"] = f["links"].get("supports", [])
        out.append(d)
    return out


def resume_context(p: Project) -> Dict[str, Any]:
    cfg = p.config["project"]
    g = gitinfo.info(p.root)
    last = p.q1("SELECT * FROM events WHERE action NOT IN ('restored','import_error','reconcile_error') ORDER BY id DESC LIMIT 1")
    cp_row = p.q1("SELECT id FROM checkpoints ORDER BY created_at DESC, id DESC LIMIT 1")
    cp = checkpoint_detail(p, cp_row["id"]) if cp_row else None
    stale_days = float(p.config.get("resume", {}).get("stale_checkpoint_days") or 14)
    since: Dict[str, Any] = {"events": 0, "runs": 0, "findings": [], "experiments": [], "decisions": []}
    if cp:
        cp["age_days"] = _age_days(cp["created_at"])
        cp["stale"] = cp["age_days"] is not None and cp["age_days"] > stale_days
        ts = cp["created_at"]
        since["events"] = p.q1("SELECT COUNT(*) AS n FROM events WHERE ts > ?", (ts,))["n"]
        since["runs"] = p.q1("SELECT COUNT(*) AS n FROM runs WHERE created_at > ?", (ts,))["n"]
        since["findings"] = [r["id"] for r in p.q("SELECT id FROM findings WHERE created_at > ? ORDER BY id", (ts,))]
        since["experiments"] = [r["id"] for r in p.q("SELECT id FROM experiments WHERE created_at > ? ORDER BY id", (ts,))]
        since["decisions"] = [r["id"] for r in p.q("SELECT id FROM decisions WHERE created_at > ? ORDER BY id", (ts,))]
    base = baseline(p)
    if base:
        base = {**_brief(p, base["id"]), "parameters": base.get("parameters"), "question_id": base.get("question_id")}

    def qcards(status: str) -> List[Dict[str, Any]]:
        out = []
        for q in p.list("question", "status=?", (status,), order="updated_at DESC"):
            n_exp = p.q1("SELECT COUNT(*) AS n FROM experiments WHERE question_id=?", (q["id"],))["n"]
            out.append({"id": q["id"], "title": q["title"], "status": q["status"], "experiments": n_exp,
                        "parent_id": q.get("parent_id"), "updated_at": q["updated_at"]})
        return out

    current = []
    for e in p.list("experiment", "status IN ('running','ready','proposed','needs_review')",
                    order="CASE status WHEN 'running' THEN 0 WHEN 'needs_review' THEN 1 WHEN 'ready' THEN 2 ELSE 3 END, updated_at DESC"):
        current.append({"id": e["id"], "title": e["title"], "status": e["status"], "question_id": e.get("question_id"),
                        "is_baseline": e.get("is_baseline"), "state": experiment_state(p, e["id"]),
                        "parameters": e.get("parameters"), "updated_at": e["updated_at"],
                        "author_type": e.get("author_type")})
    needs = [{"id": e["id"], "title": e["title"], "status": e["status"], "state": e["state"]}
             for e in experiments_needing_synthesis(p)]

    blockers: List[Dict[str, Any]] = []
    for q in p.list("question", "status='blocked'"):
        blockers.append({"kind": "question", "id": q["id"], "text": q["title"]})
    for e in p.list("experiment", "status='failed' AND (completed_at IS NULL OR completed_at > ?)",
                    ((cp or {}).get("created_at") or "",)):
        blockers.append({"kind": "experiment", "id": e["id"], "text": f"{e['title']} failed"})
    for n in needs:
        if n["state"]["over_failure_budget"]:
            blockers.append({"kind": "budget", "id": n["id"], "text": f"{n['state']['unreviewed_failed']} failed runs need review"})
    for r in p.q("SELECT id, experiment_id, ended_at FROM runs WHERE status IN ('failed','unknown') AND reviewed=0 ORDER BY id DESC LIMIT 5"):
        blockers.append({"kind": "run", "id": r["id"], "text": f"{r['id']} ({r['experiment_id']}) failed — not yet reviewed"})
    if cp and cp.get("current_problem"):
        blockers.insert(0, {"kind": "checkpoint", "id": cp["id"], "text": cp["current_problem"]})

    # suggested files
    suggested: List[Dict[str, Any]] = []
    seen = set()

    def add(path: Optional[str], reason: str, ref: Optional[str] = None, kind: str = "file") -> None:
        if not path or path in seen:
            return
        seen.add(path)
        ap = p.abspath(path)
        suggested.append({"path": path, "abspath": ap, "exists": bool(ap and os.path.exists(ap)),
                          "reason": reason, "ref": ref, "kind": kind})

    if cp:
        add(cp["path"], "Latest checkpoint", cp["id"], "checkpoint")
    for n in list_notes(p):
        if n["pinned"]:
            add(n["path"], "Pinned note", n["id"], "note")
    for f in p.list("finding", "status='supported'", order="updated_at DESC")[:6]:
        for aid in f["links"].get("supports", []):
            if aid.startswith("A-"):
                a = p.q1("SELECT path, name FROM artifacts WHERE id=?", (aid,))
                if a:
                    add(a["path"], f"Evidence for {f['id']}", aid, "artifact")
    if base:
        for a in p.q("SELECT id, path FROM artifacts WHERE experiment_id=? ORDER BY CASE type WHEN 'image' THEN 0 "
                     "WHEN 'table' THEN 1 ELSE 2 END, id DESC LIMIT 2", (base["id"],)):
            add(a["path"], f"Baseline {base['id']} artifact", a["id"], "artifact")
    for n in needs[:3]:
        add(p.rel(p.mirror_path("experiment", n["id"])), f"{n['id']} needs synthesis", n["id"], "experiment")
    add(".research/context/current.md", "Agent context summary", None, "context")

    counts = {t: p.q1(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in
              ("questions", "experiments", "runs", "findings", "decisions", "checkpoints", "artifacts")}
    active_runs = p.q("SELECT id, experiment_id, status, backend, slurm_job_id, started_at, label FROM runs "
                      "WHERE status IN ('queued','running') ORDER BY id")
    return {
        "project": {"name": p.name, "goal": cfg.get("goal"), "description": cfg.get("description"),
                    "status": cfg.get("status"), "root": p.root, "created_at": cfg.get("created_at")},
        "git": g,
        "last_activity": last,
        "last_activity_age_days": _age_days(last["ts"]) if last else None,
        "latest_checkpoint": cp,
        "since_checkpoint": since,
        "baseline": base,
        "active_questions": qcards("investigating"),
        "open_questions": qcards("open"),
        "blocked_questions": qcards("blocked"),
        "established_findings": _findings(p, "kind='result' AND status IN ('supported','preliminary')"),
        "failed_directions": _findings(p, "(kind='failure' AND status NOT IN ('superseded')) OR status='contradicted'"),
        "current_experiments": current,
        "needs_synthesis": needs,
        "active_runs": active_runs,
        "decisions": [{"id": d["id"], "title": d["title"], "statement": d["statement"], "date": d["date"],
                       "findings": d["links"].get("supporting_findings", []), "author_type": d["author_type"]}
                      for d in p.list("decision", "status='active'", order="date DESC, id DESC")],
        "blockers": blockers,
        "suggested_files": suggested,
        "counts": counts,
        "recent_events": p.q("SELECT * FROM events ORDER BY id DESC LIMIT 15"),
        "policy": p.policy,
        "generated_at": now_iso(),
    }


# =============================================================================== checkpoints

def checkpoint_draft(p: Project) -> Dict[str, Any]:
    """Pre-filled checkpoint content from current state (the human/agent edits before saving)."""
    r = resume_context(p)
    g = r["git"]
    est = [f for f in r["established_findings"]]
    understanding = "\n".join(f"- {f['title']} ({f['id']}, {f['status']})" for f in est[:8]) or ""
    base = r["baseline"]
    baseline_txt = ""
    if base:
        params = ", ".join(f"{k}={v}" for k, v in (base.get("parameters") or {}).items())
        baseline_txt = f"{base['id']} — {base['title']}" + (f" ({params})" if params else "")
    problems = [b["text"] for b in r["blockers"] if b["kind"] != "checkpoint"]
    nxt = ""
    for e in r["current_experiments"]:
        if e["status"] == "proposed":
            nxt = f"{e['id']} — {e['title']}"
            break
    if not nxt:
        syn = p.q1("SELECT next_experiment FROM syntheses WHERE next_experiment IS NOT NULL AND next_experiment != '' "
                   "ORDER BY created_at DESC LIMIT 1")
        nxt = syn["next_experiment"] if syn else ""
    return {
        "title": f"Checkpoint — {now_iso()[:10]}",
        "goal": r["project"]["goal"] or "",
        "understanding": understanding,
        "baseline": baseline_txt,
        "baseline_experiment_id": base["id"] if base else None,
        "finding_ids": [f["id"] for f in est[:10]],
        "failure_ids": [f["id"] for f in r["failed_directions"][:10]],
        "question_ids": [q["id"] for q in r["active_questions"] + r["open_questions"] + r["blocked_questions"]],
        "experiment_ids": [e["id"] for e in r["current_experiments"]],
        "current_problem": "\n".join(f"- {x}" for x in problems),
        "next_experiment": nxt,
        "git_branch": g.get("branch"), "git_commit": g.get("commit"), "git_dirty": g.get("dirty"),
    }


def create_checkpoint(p: Project, title: Optional[str] = None, goal: Optional[str] = None,
                      understanding: Optional[str] = None, baseline: Optional[str] = None,
                      baseline_experiment: Optional[str] = None, findings: Any = None, failures: Any = None,
                      questions: Any = None, experiments: Any = None, current_problem: Optional[str] = None,
                      next_experiment: Optional[str] = None, notes: Optional[str] = None,
                      use_draft: bool = True) -> Dict[str, Any]:
    d = checkpoint_draft(p) if use_draft else {}
    g = gitinfo.info(p.root)

    def pick(v, key):
        return v if v not in (None, "") else d.get(key)

    vals = {
        "title": pick(title, "title") or f"Checkpoint — {now_iso()[:10]}",
        "goal": pick(goal, "goal"), "understanding": pick(understanding, "understanding"),
        "baseline": pick(baseline, "baseline"),
        "baseline_experiment_id": normalize_id(baseline_experiment, "EXP") if baseline_experiment else d.get("baseline_experiment_id"),
        "finding_ids": [normalize_id(x, "F") for x in as_list(findings)] if findings is not None else d.get("finding_ids", []),
        "failure_ids": [normalize_id(x, "F") for x in as_list(failures)] if failures is not None else d.get("failure_ids", []),
        "question_ids": [normalize_id(x, "Q") for x in as_list(questions)] if questions is not None else d.get("question_ids", []),
        "experiment_ids": [normalize_id(x, "EXP") for x in as_list(experiments)] if experiments is not None else d.get("experiment_ids", []),
        "current_problem": pick(current_problem, "current_problem"),
        "next_experiment": pick(next_experiment, "next_experiment"), "notes": notes,
        "git_branch": g.get("branch"), "git_commit": g.get("commit"), "git_dirty": g.get("dirty"),
    }
    c = p.insert_entity("checkpoint", vals, summary=f"Checkpoint {vals['title']}")
    write_current_md(p)
    return checkpoint_detail(p, c["id"])


# =============================================================================== current.md

def _line(items: List[str], empty: str = "_none_") -> List[str]:
    return items if items else [empty]


def render_current_md(p: Project, r: Optional[Dict[str, Any]] = None) -> str:
    r = r or resume_context(p)
    pr = r["project"]
    g = r["git"]
    cp = r["latest_checkpoint"]
    L = [f"# Research context — {pr['name']}",
         "",
         "> Generated from .research/ by `research context`. Do not edit; it is overwritten.",
         f"> Generated {r['generated_at']}. Details: `research show <ID>`, `research resume`.",
         "",
         "## Goal", "", pr.get("goal") or "_(not set — `research project set --goal …`)_", ""]
    if g.get("branch"):
        L += [f"Git: `{g['branch']}` @ `{(g.get('commit') or '')[:10]}`" + (" (uncommitted changes)" if g.get("dirty") else ""), ""]
    L += ["## Latest checkpoint", ""]
    if cp:
        L += [f"**{cp['id']}** {cp['title']} ({cp['created_at'][:10]}"
              + (", STALE" if cp.get("stale") else "") + ")"]
        for key, label in (("understanding", "Understanding"), ("current_problem", "Current problem"),
                           ("next_experiment", "Next step")):
            if cp.get(key):
                L += [f"- **{label}:** " + cp[key].replace("\n", "\n  ")]
    else:
        L += ["_No checkpoint yet._"]
    b = r["baseline"]
    L += ["", "## Current baseline", "",
          (f"{b['id']} — {b['title']}" + (f" · params: {b['parameters']}" if b.get("parameters") else "")) if b else "_none set_"]
    L += ["", "## Active & open questions", ""]
    L += _line([f"- {q['id']} [{q['status']}] {q['title']}" for q in r["active_questions"] + r["open_questions"] + r["blocked_questions"]])
    L += ["", "## Established findings (what we believe)", ""]
    L += _line([f"- {f['id']} [{f['status']}{', ' + f['confidence'] if f.get('confidence') else ''}] {f['title']}"
                + (f" — evidence: {', '.join(f['evidence'][:4])}" if f.get("evidence") else "")
                for f in r["established_findings"][:15]])
    L += ["", "## Failed directions (do not repeat without a new reason)", ""]
    L += _line([f"- {f['id']} [{f['status']}] {f['title']}" for f in r["failed_directions"][:12]])
    L += ["", "## Current experiments", ""]
    L += _line([f"- {e['id']} [{e['status']}] {e['title']} — runs {e['state']['runs']}"
                + (f", ⚠ {e['state']['unsynthesized']} unsynthesized" if e["state"]["unsynthesized"] else "")
                for e in r["current_experiments"]])
    if r["needs_synthesis"]:
        L += ["", "## ⚠ Needs synthesis before new experiments", ""]
        L += [f"- {e['id']} {e['title']} ({e['state']['unsynthesized']} unsynthesized runs)" for e in r["needs_synthesis"]]
    L += ["", "## Active decisions", ""]
    L += _line([f"- {d['id']} ({d['date']}) {d['statement']}" for d in r["decisions"][:10]])
    L += ["", "## Blockers", ""]
    L += _line([f"- [{x['kind']}] {x['id']}: {x['text']}" for x in r["blockers"][:10]])
    pol = r["policy"]
    L += ["", "## Agent policy", "",
          f"- max runs without synthesis: {pol.get('max_runs_without_synthesis')}",
          f"- max failed runs without review: {pol.get('max_failed_runs_without_review')}",
          f"- synthesis required before new experiment: {pol.get('require_synthesis_before_new_experiment')}",
          "- Protocol: see AGENTS.md", ""]
    return "\n".join(L)


def write_current_md(p: Project) -> str:
    path = p.path("context", "current.md")
    text = render_current_md(p)
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    # avoid churn: ignore the timestamp line when comparing
    strip = lambda s: "\n".join(l for l in (s or "").splitlines() if not l.startswith("> Generated "))
    if strip(old) != strip(text):
        _atomic_write(path, text)
    return path
