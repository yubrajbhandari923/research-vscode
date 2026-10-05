"""Entity-level business logic: questions, experiments, syntheses, findings, decisions, notes, skills."""
from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List, Optional

from . import gitinfo, schema, yamlio
from .schema import CONFIDENCE, FINDING_KINDS, RUN_TERMINAL, STATUSES, normalize_id
from .store import Project, append_jsonl
from .util import BudgetExceeded, NotFound, ResearchError, as_list, jload, now_iso, slugify


def _check_status(etype: str, status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    s = str(status).strip().lower().replace("-", "_").replace(" ", "_")
    if s not in STATUSES[etype]:
        raise ResearchError(f"Invalid {etype} status {status!r}. Use one of: {', '.join(STATUSES[etype])}")
    return s


def _ids(p: Project, values: Any, expect: Optional[str] = None, must_exist: bool = True) -> List[str]:
    out = []
    for v in as_list(values):
        i = normalize_id(str(v), expect)
        if must_exist:
            _require(p, i)
        out.append(i)
    return out


def _require(p: Project, id_: str) -> None:
    t = schema.type_of(id_)
    if t == "note":
        if not os.path.exists(note_path(p, id_)):
            raise NotFound(f"Note {id_} not found")
        return
    table = {"run": "runs", "artifact": "artifacts"}.get(t) or schema.ENTITIES[t]["table"]
    if not p.q1(f"SELECT 1 AS x FROM {table} WHERE id=?", (id_,)):
        raise NotFound(f"{t.capitalize()} {id_} not found")


def _is_enforced(p: Project) -> bool:
    return p.author.get("author_type") == "agent" or bool(p.policy.get("enforce_for_humans"))


# =============================================================================== questions

def create_question(p: Project, title: str, description: Optional[str] = None, parent: Optional[str] = None,
                    status: str = "open", tags: Any = None) -> Dict[str, Any]:
    if not title or not title.strip():
        raise ResearchError("A question needs a title")
    parent_id = _ids(p, parent, "Q")[0] if parent else None
    return p.insert_entity("question", {
        "title": title.strip(), "description": description, "parent_id": parent_id,
        "status": _check_status("question", status), "tags": as_list(tags)})


def update_question(p: Project, qid: str, **fields: Any) -> Dict[str, Any]:
    qid = normalize_id(qid, "Q")
    ch = {k: v for k, v in fields.items() if v is not None}
    if "status" in ch:
        ch["status"] = _check_status("question", ch["status"])
    if "parent" in ch:
        ch["parent_id"] = _ids(p, ch.pop("parent"), "Q")[0] if ch.get("parent") else None
    if "tags" in ch:
        ch["tags"] = as_list(ch["tags"])
    return p.update_entity("question", qid, ch)


# =============================================================================== experiments

def experiment_state(p: Project, exp_id: str) -> Dict[str, Any]:
    """Run counts + synthesis/budget flags for one experiment."""
    runs = p.q("SELECT id,status,reviewed,ended_at,created_at FROM runs WHERE experiment_id=? ORDER BY id", (exp_id,))
    last = p.q1("SELECT created_at, runs_covered FROM syntheses WHERE experiment_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (exp_id,))
    covered = set()
    for s in p.q("SELECT runs_covered FROM syntheses WHERE experiment_id=?", (exp_id,)):
        covered.update(as_list(jload(s["runs_covered"], [])))
    finished = [r for r in runs if r["status"] in RUN_TERMINAL]
    active = [r for r in runs if r["status"] in ("queued", "running")]
    unsynth = [r for r in finished if r["id"] not in covered]
    unrev_failed = [r for r in runs if r["status"] == "failed" and not r["reviewed"]]
    pol = p.policy
    max_u = int(pol.get("max_runs_without_synthesis") or 0)
    max_f = int(pol.get("max_failed_runs_without_review") or 0)
    return {
        "runs": len(runs), "active": len(active), "finished": len(finished),
        "completed": sum(1 for r in runs if r["status"] == "completed"),
        "failed": sum(1 for r in runs if r["status"] == "failed"),
        "unsynthesized": len(unsynth), "unsynthesized_ids": [r["id"] for r in unsynth],
        "unreviewed_failed": len(unrev_failed),
        "needs_synthesis": len(unsynth) > 0 and not active,
        "over_run_budget": bool(max_u) and len(unsynth) >= max_u,
        "over_failure_budget": bool(max_f) and len(unrev_failed) >= max_f,
        "last_synthesis_at": last["created_at"] if last else None,
        "max_runs_without_synthesis": max_u, "max_failed_runs_without_review": max_f,
    }


def experiments_needing_synthesis(p: Project) -> List[Dict[str, Any]]:
    out = []
    for e in p.q("SELECT id,title,status FROM experiments WHERE status NOT IN ('abandoned') ORDER BY id"):
        st = experiment_state(p, e["id"])
        if st["needs_synthesis"] or st["over_run_budget"] or st["over_failure_budget"] or e["status"] == "needs_review":
            out.append({**e, "state": st})
    return out


def check_new_experiment_allowed(p: Project, override: Optional[str] = None) -> List[str]:
    """Agent policy: synthesize before starting new experiments. Returns warnings (humans)."""
    if not p.policy.get("require_synthesis_before_new_experiment"):
        return []
    pending = [e for e in experiments_needing_synthesis(p) if e["state"]["unsynthesized"] > 0]
    if not pending:
        return []
    msg = ("Synthesis required before starting a new experiment: "
           + ", ".join(f"{e['id']} ({e['state']['unsynthesized']} unsynthesized runs)" for e in pending))
    if _is_enforced(p) and not override:
        raise BudgetExceeded(msg, hint="Run `research experiment synthesize <EXP>` first "
                             "(or ask the human; --override \"reason\" is logged).")
    if override:
        p.event("policy", None, "override", f"Override new-experiment rule: {override}")
    return [msg]


def check_run_budget(p: Project, exp_id: str, override: Optional[str] = None) -> List[str]:
    st = experiment_state(p, exp_id)
    problems = []
    if st["over_run_budget"]:
        problems.append(f"{exp_id} has {st['unsynthesized']} unsynthesized runs "
                        f"(limit {st['max_runs_without_synthesis']}). Synthesize before running more.")
    if st["over_failure_budget"]:
        problems.append(f"{exp_id} has {st['unreviewed_failed']} failed runs without review "
                        f"(limit {st['max_failed_runs_without_review']}). Review/synthesize before retrying.")
    if not problems:
        return []
    if _is_enforced(p) and not override:
        raise BudgetExceeded(" ".join(problems), hint=f"research experiment synthesize {exp_id} …")
    if override:
        p.event("experiment", exp_id, "override", f"Run budget override: {override}")
    return problems


def create_experiment(p: Project, title: str, question: Optional[str] = None, hypothesis: Optional[str] = None,
                      motivation: Optional[str] = None, method: Optional[str] = None,
                      expected_outcome: Optional[str] = None, success_criteria: Optional[str] = None,
                      stop_conditions: Optional[str] = None, parameters: Optional[Dict[str, Any]] = None,
                      metrics: Any = None, expected_artifacts: Any = None, planned_runs: Optional[int] = None,
                      parent: Optional[str] = None, status: str = "proposed", tags: Any = None,
                      notes: Optional[str] = None, override: Optional[str] = None,
                      _param_delta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not title or not title.strip():
        raise ResearchError("An experiment needs a title")
    warnings = check_new_experiment_allowed(p, override)
    qid = _ids(p, question, "Q")[0] if question else None
    pid = _ids(p, parent, "EXP")[0] if parent else None
    g = gitinfo.info(p.root)
    e = p.insert_entity("experiment", {
        "title": title.strip(), "question_id": qid, "parent_id": pid, "hypothesis": hypothesis,
        "motivation": motivation, "method": method, "expected_outcome": expected_outcome,
        "success_criteria": success_criteria, "stop_conditions": stop_conditions,
        "parameters": parameters or {}, "param_delta": _param_delta or None,
        "metrics_requested": as_list(metrics), "expected_artifacts": as_list(expected_artifacts),
        "planned_runs": planned_runs, "status": _check_status("experiment", status), "is_baseline": False,
        "tags": as_list(tags), "notes": notes,
        "git_branch": g["branch"], "git_commit": g["commit"], "git_dirty": g["dirty"],
    })
    if qid:
        q = p.get("question", qid)
        if q["status"] == "open":
            p.update_entity("question", qid, {"status": "investigating"}, summary=f"{qid} → investigating ({e['id']} registered)")
    if warnings:
        e["warnings"] = warnings
    return e


def create_variant(p: Project, source: str, params: Optional[Dict[str, Any]] = None, title: Optional[str] = None,
                   motivation: Optional[str] = None, hypothesis: Optional[str] = None,
                   override: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    src = p.get("experiment", normalize_id(source, "EXP"))
    params = params or {}
    base = dict(src.get("parameters") or {})
    delta = {k: {"from": base.get(k), "to": v} for k, v in params.items() if base.get(k) != v}
    new_params = {**base, **params}
    if not title:
        dstr = ", ".join(f"{k}={v}" for k, v in params.items())
        title = f"{src['title']} [{dstr}]" if dstr else f"{src['title']} (variant)"
    fields = dict(
        question=src.get("question_id"), hypothesis=hypothesis or src.get("hypothesis"),
        motivation=motivation or (f"Variant of {src['id']}: " + ", ".join(
            f"{k} {d['from']} → {d['to']}" for k, d in delta.items()) if delta else f"Variant of {src['id']}"),
        method=src.get("method"), expected_outcome=src.get("expected_outcome"),
        success_criteria=src.get("success_criteria"), stop_conditions=src.get("stop_conditions"),
        metrics=src.get("metrics_requested"), expected_artifacts=src.get("expected_artifacts"),
        planned_runs=src.get("planned_runs"), tags=src.get("tags"),
    )
    fields.update({k: v for k, v in extra.items() if v is not None})
    return create_experiment(p, title, parameters=new_params, parent=src["id"], override=override,
                             _param_delta=delta, **fields)


def update_experiment(p: Project, exp_id: str, **fields: Any) -> Dict[str, Any]:
    exp_id = normalize_id(exp_id, "EXP")
    ch: Dict[str, Any] = {}
    alias = {"question": "question_id", "parent": "parent_id", "metrics": "metrics_requested",
             "expected": "expected_outcome", "success": "success_criteria", "stop": "stop_conditions"}
    for k, v in fields.items():
        if v is None:
            continue
        k = alias.get(k, k)
        if k == "question_id":
            v = _ids(p, v, "Q")[0] if v else None
        if k == "parent_id":
            v = _ids(p, v, "EXP")[0] if v else None
        if k in ("metrics_requested", "expected_artifacts", "tags"):
            v = as_list(v)
        if k == "status":
            return set_experiment_status(p, exp_id, v)
        ch[k] = v
    return p.update_entity("experiment", exp_id, ch)


def set_experiment_status(p: Project, exp_id: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    exp_id = normalize_id(exp_id, "EXP")
    status = _check_status("experiment", status)
    cur = p.get("experiment", exp_id)
    ch: Dict[str, Any] = {"status": status}
    if status == "running" and not cur.get("started_at"):
        ch["started_at"] = now_iso()
    if status in ("completed", "failed", "abandoned"):
        ch["completed_at"] = now_iso()
    summ = f"{exp_id}: {cur['status']} → {status}" + (f" ({reason})" if reason else "")
    return p.update_entity("experiment", exp_id, ch, action="status", summary=summ)


def set_baseline(p: Project, exp_id: Optional[str]) -> Optional[Dict[str, Any]]:
    exp_id = normalize_id(exp_id, "EXP") if exp_id else None
    for r in p.q("SELECT id FROM experiments WHERE is_baseline=1"):
        if r["id"] != exp_id:
            p.update_entity("experiment", r["id"], {"is_baseline": False}, action="baseline",
                            summary=f"{r['id']} is no longer the baseline")
    if exp_id:
        return p.update_entity("experiment", exp_id, {"is_baseline": True}, action="baseline",
                               summary=f"{exp_id} set as baseline")
    return None


def baseline(p: Project) -> Optional[Dict[str, Any]]:
    r = p.q1("SELECT id FROM experiments WHERE is_baseline=1 ORDER BY updated_at DESC LIMIT 1")
    return p.get("experiment", r["id"]) if r else None


def synthesize(p: Project, exp_id: str, what_happened: Optional[str] = None, what_worked: Optional[str] = None,
               what_failed: Optional[str] = None, interpretation: Optional[str] = None,
               limitations: Optional[str] = None, unresolved: Optional[str] = None,
               next_experiment: Optional[str] = None, mark: Optional[str] = None) -> Dict[str, Any]:
    exp_id = normalize_id(exp_id, "EXP")
    exp = p.get("experiment", exp_id)
    if not any([what_happened, what_worked, what_failed, interpretation]):
        raise ResearchError("A synthesis needs at least one of: what happened / worked / failed / interpretation")
    st = experiment_state(p, exp_id)
    n = (p.q1("SELECT COUNT(*) AS n FROM syntheses WHERE experiment_id=?", (exp_id,)) or {"n": 0})["n"] + 1
    sid = f"{exp_id}/S{n}"
    while p.q1("SELECT 1 AS x FROM syntheses WHERE id=?", (sid,)):
        n += 1
        sid = f"{exp_id}/S{n}"
    finished = [r["id"] for r in p.q("SELECT id FROM runs WHERE experiment_id=? AND status IN ('completed','failed','cancelled','unknown')", (exp_id,))]
    rec = {"id": sid, "experiment_id": exp_id, "what_happened": what_happened, "what_worked": what_worked,
           "what_failed": what_failed, "interpretation": interpretation, "limitations": limitations,
           "unresolved": unresolved, "next_experiment": next_experiment, "runs_covered": finished,
           "created_at": now_iso(), **p.author}
    append_jsonl(p.path("syntheses.jsonl"), rec)
    from .runs import ingest_root_files
    with p.tx():
        ingest_root_files(p)
        p.conn.execute("UPDATE runs SET reviewed=1 WHERE experiment_id=? AND status='failed'", (exp_id,))
        p.event("experiment", exp_id, "synthesized",
                f"{exp_id} synthesized ({len(finished)} runs covered, {st['unsynthesized']} new)")
    if mark:
        set_experiment_status(p, exp_id, mark)
    elif exp["status"] in ("running", "ready", "proposed") and st["active"] == 0 and finished:
        set_experiment_status(p, exp_id, "needs_review", "synthesized; awaiting completion")
    else:
        p.export("experiment", exp_id)
    return get_synthesis(p, sid)


def get_synthesis(p: Project, sid: str) -> Dict[str, Any]:
    r = p.q1("SELECT * FROM syntheses WHERE id=?", (sid,))
    if not r:
        raise NotFound(f"Synthesis {sid} not found")
    r["runs_covered"] = as_list(jload(r["runs_covered"], []))
    return r


def list_syntheses(p: Project, exp_id: str) -> List[Dict[str, Any]]:
    out = []
    for r in p.q("SELECT id FROM syntheses WHERE experiment_id=? ORDER BY created_at, id", (exp_id,)):
        out.append(get_synthesis(p, r["id"]))
    return out


# =============================================================================== findings

def create_finding(p: Project, title: str, statement: Optional[str] = None, kind: str = "result",
                   status: str = "preliminary", confidence: Optional[str] = None, supports: Any = None,
                   contradicts: Any = None, related: Any = None, questions: Any = None,
                   limitations: Optional[str] = None, contradicting_evidence: Optional[str] = None,
                   tags: Any = None) -> Dict[str, Any]:
    if not title or not title.strip():
        raise ResearchError("A finding needs a short title")
    if kind not in FINDING_KINDS:
        raise ResearchError(f"kind must be one of {FINDING_KINDS}")
    if confidence and confidence not in CONFIDENCE:
        raise ResearchError(f"confidence must be one of {CONFIDENCE}")
    links = {"supports": _ids(p, supports), "contradicts": _ids(p, contradicts),
             "related": _ids(p, related), "questions": _ids(p, questions, "Q")}
    return p.insert_entity("finding", {
        "title": title.strip(), "statement": statement or title.strip(), "kind": kind,
        "status": _check_status("finding", status), "confidence": confidence,
        "limitations": limitations, "contradicting_evidence": contradicting_evidence, "tags": as_list(tags),
    }, links)


def update_finding(p: Project, fid: str, supports: Any = None, contradicts: Any = None, related: Any = None,
                   questions: Any = None, add_supports: Any = None, **fields: Any) -> Dict[str, Any]:
    fid = normalize_id(fid, "F")
    cur = p.get("finding", fid)
    ch = {k: v for k, v in fields.items() if v is not None}
    if "status" in ch:
        ch["status"] = _check_status("finding", ch["status"])
    if ch.get("confidence") and ch["confidence"] not in CONFIDENCE:
        raise ResearchError(f"confidence must be one of {CONFIDENCE}")
    if "tags" in ch:
        ch["tags"] = as_list(ch["tags"])
    links = dict(cur["links"])
    changed = False
    for key, val in (("supports", supports), ("contradicts", contradicts), ("related", related), ("questions", questions)):
        if val is not None:
            links[key] = _ids(p, val, "Q" if key == "questions" else None)
            changed = True
    if add_supports:
        links["supports"] = list(dict.fromkeys(links["supports"] + _ids(p, add_supports)))
        changed = True
    return p.update_entity("finding", fid, ch, links=links if changed else None)


def supersede_finding(p: Project, fid: str, by: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    fid = normalize_id(fid, "F")
    by_id = _ids(p, by, "F")[0] if by else None
    return p.update_entity("finding", fid, {"status": "superseded", "superseded_by": by_id}, action="superseded",
                           summary=f"{fid} superseded" + (f" by {by_id}" if by_id else "") + (f": {reason}" if reason else ""))


# =============================================================================== decisions

def create_decision(p: Project, statement: str, reason: Optional[str] = None, title: Optional[str] = None,
                    supporting_findings: Any = None, experiments: Any = None, date: Optional[str] = None,
                    status: str = "active", tags: Any = None, supersedes: Optional[str] = None) -> Dict[str, Any]:
    if not statement or not statement.strip():
        raise ResearchError("A decision needs a statement")
    d = p.insert_entity("decision", {
        "title": (title or statement).strip()[:120], "statement": statement.strip(), "reason": reason,
        "status": _check_status("decision", status), "date": date or now_iso()[:10], "tags": as_list(tags),
    }, {"supporting_findings": _ids(p, supporting_findings, "F"), "experiments": _ids(p, experiments, "EXP")})
    if supersedes:
        old = normalize_id(supersedes, "D")
        p.update_entity("decision", old, {"status": "superseded", "superseded_by": d["id"]}, action="superseded",
                        summary=f"{old} superseded by {d['id']}")
    return d


def update_decision(p: Project, did: str, supporting_findings: Any = None, experiments: Any = None,
                    **fields: Any) -> Dict[str, Any]:
    did = normalize_id(did, "D")
    cur = p.get("decision", did)
    ch = {k: v for k, v in fields.items() if v is not None}
    if "status" in ch:
        ch["status"] = _check_status("decision", ch["status"])
    links = dict(cur["links"])
    changed = False
    if supporting_findings is not None:
        links["supporting_findings"] = _ids(p, supporting_findings, "F")
        changed = True
    if experiments is not None:
        links["experiments"] = _ids(p, experiments, "EXP")
        changed = True
    return p.update_entity("decision", did, ch, links=links if changed else None)


# =============================================================================== generic status

def set_status(p: Project, id_: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    t = schema.type_of(id_)
    id_ = normalize_id(id_)
    if t == "experiment":
        return set_experiment_status(p, id_, status, reason)
    if t in ("question", "finding", "decision"):
        s = _check_status(t, status)
        cur = p.get(t, id_)
        return p.update_entity(t, id_, {"status": s}, action="status",
                               summary=f"{id_}: {cur['status']} → {s}" + (f" ({reason})" if reason else ""))
    if t == "run":
        from .runs import set_run_status
        return set_run_status(p, id_, status, reason)
    raise ResearchError(f"{t} has no status")


# =============================================================================== notes (file-canonical)

def note_path(p: Project, note_id: str) -> str:
    stem = note_id[5:] if note_id.startswith("note:") else note_id
    stem = stem[:-3] if stem.endswith(".md") else stem
    return p.path("notes", f"{stem}.md")


def _read_note(p: Project, path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        fm, body = yamlio.split_front_matter(text)
    except Exception:
        fm, body = {}, text
    stem = os.path.splitext(os.path.basename(path))[0]
    title = fm.get("title")
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.M)
        title = m.group(1).strip() if m else stem
    st = os.stat(path)
    first = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")), "")
    return {"id": f"note:{stem}", "type": "note", "title": str(title), "pinned": bool(fm.get("pinned")),
            "links": [str(x) for x in as_list(fm.get("links"))], "path": p.rel(path),
            "created_at": fm.get("created") or None, "modified": st.st_mtime, "excerpt": first[:200]}


def list_notes(p: Project) -> List[Dict[str, Any]]:
    d = p.path("notes")
    out = [_read_note(p, os.path.join(d, fn)) for fn in os.listdir(d) if fn.endswith(".md")] if os.path.isdir(d) else []
    out.sort(key=lambda n: (not n["pinned"], -n["modified"]))
    return out


def create_note(p: Project, title: str, body: str = "", links: Any = None, pinned: bool = False) -> Dict[str, Any]:
    stem = slugify(title)
    path = p.path("notes", f"{stem}.md")
    i = 2
    while os.path.exists(path):
        path = p.path("notes", f"{stem}-{i}.md")
        i += 1
    fm = {"title": title, "pinned": bool(pinned), "links": _ids(p, links), "created": now_iso()}
    with open(path, "w", encoding="utf-8") as f:
        f.write(yamlio.join_front_matter(fm, f"# {title}\n\n{body}".rstrip() + "\n"))
    n = _read_note(p, path)
    p.event("note", n["id"], "created", f"Note '{title}'")
    return n


def _edit_note_fm(p: Project, note_id: str, fn) -> Dict[str, Any]:
    path = note_path(p, note_id)
    if not os.path.exists(path):
        raise NotFound(f"Note {note_id} not found")
    with open(path, encoding="utf-8") as f:
        fm, body = yamlio.split_front_matter(f.read())
    fn(fm)
    with open(path, "w", encoding="utf-8") as f:
        f.write(yamlio.join_front_matter(fm, body))
    return _read_note(p, path)


def pin_note(p: Project, note_id: str, pinned: bool = True) -> Dict[str, Any]:
    return _edit_note_fm(p, note_id, lambda fm: fm.__setitem__("pinned", bool(pinned)))


def link_note(p: Project, note_id: str, targets: Any, remove: bool = False) -> Dict[str, Any]:
    ids = _ids(p, targets)

    def f(fm):
        cur = [str(x) for x in as_list(fm.get("links"))]
        fm["links"] = [x for x in cur if x not in ids] if remove else list(dict.fromkeys(cur + ids))
    return _edit_note_fm(p, note_id, f)


def notes_linked_to(p: Project, id_: str) -> List[Dict[str, Any]]:
    return [n for n in list_notes(p) if id_ in n["links"]]


# =============================================================================== agent context

AGENT_DIRS = {"skills": "skills", "context": "context", "prompts": "prompts", "templates": "templates"}


def list_skills(p: Project) -> List[Dict[str, Any]]:
    d = p.path("skills")
    out = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        sd = os.path.join(d, name)
        sk = os.path.join(sd, "SKILL.md")
        if os.path.isdir(sd) and os.path.isfile(sk):
            try:
                with open(sk, encoding="utf-8") as f:
                    fm, body = yamlio.split_front_matter(f.read())
            except Exception:
                fm, body = {}, ""
            files = []
            for root, _dirs, fns in os.walk(sd):
                for fn in fns:
                    files.append(p.rel(os.path.join(root, fn)))
            out.append({"name": str(fm.get("name") or name), "dir": name,
                        "description": str(fm.get("description") or "").strip(),
                        "path": p.rel(sk), "files": sorted(files)})
        elif name.endswith(".md") and os.path.isfile(sd):
            out.append({"name": name[:-3], "dir": None, "description": "", "path": p.rel(sd), "files": [p.rel(sd)]})
    return out


def create_skill(p: Project, name: str, description: str = "", body: str = "") -> Dict[str, Any]:
    slug = slugify(name)
    d = p.path("skills", slug)
    if os.path.exists(d):
        raise ResearchError(f"Skill '{slug}' already exists")
    os.makedirs(d)
    text = yamlio.join_front_matter({"name": slug, "description": description or f"TODO: when to use {slug}"},
                                    body or f"# {name}\n\n1. …\n")
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(text)
    p.event("skill", slug, "created", f"Skill '{slug}'")
    return next(s for s in list_skills(p) if s["dir"] == slug)


def duplicate_skill(p: Project, src: str, new_name: str) -> Dict[str, Any]:
    s = p.path("skills", src)
    if not os.path.isdir(s):
        raise NotFound(f"Skill '{src}' not found")
    slug = slugify(new_name)
    d = p.path("skills", slug)
    if os.path.exists(d):
        raise ResearchError(f"Skill '{slug}' already exists")
    shutil.copytree(s, d)
    sk = os.path.join(d, "SKILL.md")
    if os.path.exists(sk):
        with open(sk, encoding="utf-8") as f:
            fm, body = yamlio.split_front_matter(f.read())
        fm["name"] = slug
        with open(sk, "w", encoding="utf-8") as f:
            f.write(yamlio.join_front_matter(fm, body))
    p.event("skill", slug, "created", f"Skill '{slug}' duplicated from '{src}'")
    return next(x for x in list_skills(p) if x["dir"] == slug)


def list_agent_files(p: Project, kind: str) -> List[Dict[str, Any]]:
    if kind not in AGENT_DIRS:
        raise ResearchError(f"Unknown agent context kind {kind!r}")
    d = p.path(AGENT_DIRS[kind])
    out = []
    for root, _dirs, fns in os.walk(d):
        for fn in sorted(fns):
            if fn.startswith("."):
                continue
            path = os.path.join(root, fn)
            out.append({"name": os.path.relpath(path, d), "path": p.rel(path), "size": os.path.getsize(path)})
    return sorted(out, key=lambda x: x["name"])
