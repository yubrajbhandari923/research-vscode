"""Verification gates: deterministic task checks and independent reviews of findings.

Records (task check runs and finding reviews) are append-only lines in .research/reviews.jsonl, indexed in the
`reviews` table. Ids: `T-005/V1` (check run), `F-003/R1` (review).

Gates (enforced for agents, warnings for humans unless agent_policy.enforce_for_humans):
  * `task done` on a task with checks runs them first; a failure blocks completion.
  * Agents can't mark a finding `supported` directly. A review with verdict `supported` by an independent reviewer
    (a human, or an agent whose name/role differs from the finding's author) is the only way, and it needs evidence.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from .schema import REVIEW_VERDICTS, normalize_id, type_of
from .store import Project, append_jsonl
from .util import NotFound, PolicyBlocked, ResearchError, as_list, jload, now_iso

_METRIC_RE = re.compile(r"^\s*(?:(?P<target>(?:RUN|EXP)[-_]?\d+)\s*:\s*)?(?P<name>[\w.\-/]+)\s*(?P<op><=|>=|==|!=|<|>)\s*(?P<value>[-+0-9.eE]+)\s*$", re.I)
_OPS = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, ">": lambda a, b: a > b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _policy(p: Project) -> Dict[str, Any]:
    return p.config.get("verification") or {}


def _enforced(p: Project) -> bool:
    from .services import _is_enforced
    return _is_enforced(p)


# =============================================================================== checks

def parse_check(spec: Any) -> Dict[str, Any]:
    """Normalise a check.

    Strings:  "cmd:pytest -q" (or any string without a known prefix) · "file:outputs/plot.png" ·
              "metric:rmse<=0.05" (the task's related experiment) · "metric:EXP-002:rmse<=0.05" · "metric:RUN-0007:acc>=0.9"
    Dicts are validated and returned as-is.
    """
    if isinstance(spec, dict):
        c = dict(spec)
    else:
        s = str(spec).strip()
        kind, _, rest = s.partition(":")
        kind = kind.strip().lower()
        if kind in ("cmd", "command", "run") and rest:
            c = {"type": "command", "run": rest.strip()}
        elif kind in ("file", "exists") and rest:
            c = {"type": "file", "path": rest.strip()}
        elif kind == "metric" and rest:
            m = _METRIC_RE.match(rest)
            if not m:
                raise ResearchError(f"Bad metric check {s!r}", hint="Use metric:[EXP-…|RUN-…:]name<=value (ops: <= >= < > == !=)")
            c = {"type": "metric", "name": m.group("name"), "op": m.group("op"), "value": float(m.group("value"))}
            if m.group("target"):
                c["target"] = normalize_id(m.group("target"))
        else:
            c = {"type": "command", "run": s}
    t = c.get("type")
    if t == "command" and not c.get("run"):
        raise ResearchError("A command check needs `run`")
    if t == "file" and not c.get("path"):
        raise ResearchError("A file check needs `path`")
    if t == "metric" and (not c.get("name") or c.get("op") not in _OPS or c.get("value") is None):
        raise ResearchError("A metric check needs name, op and value")
    if t not in ("command", "file", "metric"):
        raise ResearchError(f"Unknown check type {t!r} (command, file, metric)")
    return c


def describe_check(c: Dict[str, Any]) -> str:
    if c["type"] == "command":
        return f"cmd: {c['run']}"
    if c["type"] == "file":
        return f"file: {c['path']}"
    return f"metric: {(c.get('target') + ':') if c.get('target') else ''}{c['name']} {c['op']} {c['value']:g}"


def _metric_value(p: Project, c: Dict[str, Any], task: Dict[str, Any]) -> (Optional[float], str):
    from .runs import summary_metrics
    target = c.get("target") or task.get("related_experiment_id")
    if not target:
        return None, "no target: give metric:EXP-…:name… or link the task to an experiment"
    if target.startswith("RUN-"):
        m = summary_metrics(p, target).get(c["name"])
        return (m["value"] if m else None), target
    for r in p.q("SELECT id FROM runs WHERE experiment_id=? AND status='completed' ORDER BY id DESC", (target,)):
        m = summary_metrics(p, r["id"]).get(c["name"])
        if m and m["value"] is not None:
            return m["value"], f"{target} (latest completed run {r['id']})"
    m = p.q1("SELECT value FROM metrics WHERE experiment_id=? AND run_id IS NULL AND name=? ORDER BY id DESC LIMIT 1",
             (target, c["name"]))
    return (m["value"] if m else None), target


def run_check(p: Project, c: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    out: Dict[str, Any] = {"check": describe_check(c), "type": c["type"]}
    if c["type"] == "command":
        timeout = float(_policy(p).get("check_timeout_seconds") or 600)
        try:
            r = subprocess.run(c["run"], shell=True, cwd=p.root, capture_output=True, text=True, timeout=timeout,
                               env={**os.environ, "RESEARCH_ROOT": p.root})
            out.update(ok=r.returncode == 0, detail=f"exit {r.returncode}",
                       output=((r.stdout or "") + (r.stderr or ""))[-2000:])
        except subprocess.TimeoutExpired:
            out.update(ok=False, detail=f"timed out after {timeout:g}s")
    elif c["type"] == "file":
        ap = p.abspath(c["path"])
        ok = bool(ap and os.path.exists(ap))
        if ok and os.path.isfile(ap) and os.path.getsize(ap) == 0:
            out.update(ok=False, detail="exists but is empty")
        else:
            out.update(ok=ok, detail="exists" if ok else "missing")
    else:
        val, src = _metric_value(p, c, task)
        if val is None:
            out.update(ok=False, detail=f"{c['name']} not found ({src})")
        else:
            ok = _OPS[c["op"]](float(val), float(c["value"]))
            out.update(ok=ok, detail=f"{c['name']} = {val:.6g} from {src} (needs {c['op']} {c['value']:g})", value=val)
    out["seconds"] = round(time.time() - t0, 2)
    return out


# =============================================================================== records

def _next_record_id(p: Project, target: str, letter: str) -> str:
    n = (p.q1("SELECT COUNT(*) AS n FROM reviews WHERE target_id=?", (target,)) or {"n": 0})["n"] + 1
    rid = f"{target}/{letter}{n}"
    while p.q1("SELECT 1 AS x FROM reviews WHERE id=?", (rid,)):
        n += 1
        rid = f"{target}/{letter}{n}"
    return rid


def record(p: Project, target: str, kind: str, verdict: str, summary: str, details: Any = None) -> Dict[str, Any]:
    if verdict not in REVIEW_VERDICTS[kind]:
        raise ResearchError(f"verdict must be one of {', '.join(REVIEW_VERDICTS[kind])}")
    rec = {"id": _next_record_id(p, target, "V" if kind == "check" else "R"), "target_id": target,
           "target_type": type_of(target), "kind": kind, "verdict": verdict, "summary": summary,
           "details": details, "created_at": now_iso(), **p.author}
    append_jsonl(p.path("reviews.jsonl"), rec)
    from .runs import ingest_root_files
    ingest_root_files(p)
    p.event(rec["target_type"], target, "verified" if kind == "check" else "reviewed",
            f"{rec['id']} {verdict}: {summary}"[:300])
    return get_record(p, rec["id"])


def get_record(p: Project, rid: str) -> Dict[str, Any]:
    r = p.q1("SELECT * FROM reviews WHERE id=?", (rid,))
    if not r:
        raise NotFound(f"Review {rid} not found")
    r["details"] = jload(r.get("details"), None)
    return r


def list_records(p: Project, target: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = p.q("SELECT id FROM reviews WHERE target_id=?" + (" AND kind=?" if kind else "") + " ORDER BY created_at DESC, rowid DESC",
               (target, kind) if kind else (target,))
    return [get_record(p, r["id"]) for r in rows]


def latest(p: Project, target: str, kind: str) -> Optional[Dict[str, Any]]:
    rows = list_records(p, target, kind)
    return rows[0] if rows else None


# =============================================================================== tasks

def verify_task(p: Project, task_id: str, transition: bool = True) -> Dict[str, Any]:
    """Run a task's checks and record the result. A task in `verify` moves to done (pass) or back to running (fail)."""
    from . import plans as P
    tid = normalize_id(task_id, "T")
    task = p.get("task", tid)
    checks = task.get("checks") or []
    if not checks:
        raise ResearchError(f"{tid} has no checks", hint=f"Add some: research task update {tid} --check \"cmd:pytest -q\" "
                            "--check \"file:outputs/plot.png\" --check \"metric:EXP-001:rmse<=0.05\"")
    results = [run_check(p, parse_check(c), task) for c in checks]
    passed = all(r["ok"] for r in results)
    failed = [r for r in results if not r["ok"]]
    summary = f"{len(results) - len(failed)}/{len(results)} checks passed" + (
        "" if passed else ": " + "; ".join(f"{r['check']} ({r['detail']})" for r in failed))
    rec = record(p, tid, "check", "pass" if passed else "fail", summary, results)
    if transition and task["status"] == "verify":
        if passed:
            P.update_task(p, tid, status="done", completed_by=P.author_label(p))
        else:
            P.update_task(p, tid, status="running")
    return {"task": p.get("task", tid), "passed": passed, "results": results, "record": rec}


def gate_task_completion(p: Project, task_id: str) -> List[str]:
    """Called by complete_task. Runs checks; blocks agents on failure, warns humans."""
    task = p.get("task", task_id)
    if not task.get("checks") or not _policy(p).get("checks_gate_task_completion", True):
        return []
    res = verify_task(p, task_id, transition=False)
    if res["passed"]:
        return []
    msg = f"{task_id} can't be completed: {res['record']['summary']}"
    if _enforced(p):
        raise PolicyBlocked(msg, hint=f"Fix the failures and run `research task done {task_id}` again "
                            f"(`research task verify {task_id}` re-runs the checks only).")
    return [msg]


# =============================================================================== findings

def check_finding_status(p: Project, status: Optional[str], via_review: bool = False) -> None:
    """Gate: agents can't set a finding to `supported` except through an independent review."""
    if status != "supported" or via_review or not _policy(p).get("require_review_for_supported", True):
        return
    if _enforced(p):
        raise PolicyBlocked(
            "Agents can't mark a finding `supported` directly; it stays preliminary until an independent review.",
            hint="Create it as preliminary. A human or a different agent/role reviews it: "
                 "`research finding review F-… --verdict supported --notes \"…\"` (or `research dispatch verifier F-…`).")


def review_finding(p: Project, finding_id: str, verdict: str, notes: Optional[str] = None,
                   confidence: Optional[str] = None) -> Dict[str, Any]:
    """Record an independent review. supported → supported · contradicted → contradicted · needs_work → preliminary."""
    fid = normalize_id(finding_id, "F")
    f = p.get("finding", fid)
    verdict = (verdict or "").strip().lower().replace("-", "_").replace(" ", "_")
    if verdict not in REVIEW_VERDICTS["review"]:
        raise ResearchError(f"verdict must be one of {', '.join(REVIEW_VERDICTS['review'])}")
    me = p.author
    pol = _policy(p)
    if (pol.get("independent_reviewer", True) and me.get("author_type") == "agent"
            and f.get("author_type") == "agent" and (me.get("author_name") or "") == (f.get("author_name") or "")):
        raise PolicyBlocked(f"{fid} was written by {f.get('author_name')}; it needs an independent reviewer.",
                            hint="Ask a human, or dispatch a different agent or role: `research dispatch verifier "
                                 f"{fid}` (roles count as different reviewers, e.g. claude/implementer vs claude/verifier).")
    if verdict == "supported" and pol.get("require_evidence_for_supported", True) and not f["links"].get("supports"):
        raise PolicyBlocked(f"{fid} has no supporting evidence, so it can't be marked supported.",
                            hint=f"Link evidence first: research finding update {fid} --add-supports EXP-… RUN-… A-…")
    rec = record(p, fid, "review", verdict, notes or verdict, {"confidence": confidence} if confidence else None)
    new_status = {"supported": "supported", "contradicted": "contradicted", "needs_work": "preliminary"}[verdict]
    changes: Dict[str, Any] = {"status": new_status}
    if confidence:
        changes["confidence"] = confidence
    if verdict == "contradicted" and notes:
        changes["contradicting_evidence"] = ((f.get("contradicting_evidence") or "") + f"\n\n{rec['id']}: {notes}").strip()
    p.update_entity("finding", fid, changes, action="reviewed",
                    summary=f"{fid} reviewed by {me.get('author_name') or me.get('author_type')}: {verdict}")
    return {"finding": p.get("finding", fid), "review": rec}


# =============================================================================== attention

def attention_items(p: Project, limit: int = 8) -> List[Dict[str, Any]]:
    """Verification items for Research Home and current.md."""
    items: List[Dict[str, Any]] = []
    for t in p.q("SELECT id, title, status FROM tasks WHERE status != 'done'"):
        last = latest(p, t["id"], "check")
        if last and last["verdict"] == "fail":
            items.append({"kind": "verification_failed", "severity": "high", "id": t["id"], "title": t["title"],
                          "message": last["summary"]})
    for f in p.q("SELECT id, title FROM findings WHERE status != 'superseded'"):
        last = latest(p, f["id"], "review")
        if last and last["verdict"] in ("contradicted", "needs_work"):
            items.append({"kind": "reviewer_disagrees", "severity": "high", "id": f["id"], "title": f["title"],
                          "message": f"{last['author_name'] or last['author_type']}: {last['verdict'].replace('_', ' ')}"
                                     f" — {last['summary']}"[:240]})
    pending = p.q("SELECT id, title FROM findings WHERE status='preliminary' AND kind='result' AND author_type='agent' "
                  "AND id NOT IN (SELECT target_id FROM reviews WHERE kind='review') ORDER BY id DESC")
    for f in pending[:3]:
        items.append({"kind": "awaiting_review", "severity": "low", "id": f["id"], "title": f["title"],
                      "message": "Agent finding awaiting an independent review"})
    return items[:limit]
