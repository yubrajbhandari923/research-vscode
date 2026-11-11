"""Bounded, deterministic recovery for supervised tasks whose evidence is late or whose failure has a recognisable cause.

A Slurm job passes through four stages that used to be one: Slurm finished -> the results archive was collected -> metrics and artifacts were
ingested -> the supervisor may verify. Verifying before the last two judged a task on evidence that had not arrived yet ("metric not found").

* `uncollected` / `waiting`   a completed cluster run whose results are not collected yet holds verification (`supervisor.recovery.collection_wait_minutes`).
* `evidence_fp` / `contract_fp`  fingerprints stored with every failed verification. When the evidence changes and the contract does not, `step` re-verifies
                              the SAME attempt (the earlier result moves to `verification_history`); nothing is rewritten and no agent or job starts.
* `diagnose`                  a deterministic root cause (collection, late metric, numerical threshold, failed job) with what the supervisor decided and
                              what only a person may do. An LLM is used only for what this cannot settle (`investigator`, opt-in).
Never done here: relaxing a threshold, changing a scientific assumption or check, raising a budget, submitting a job. `step` only syncs/collects (read-only
on the cluster), reconciles collected files and re-runs the unchanged checks, each bounded by `supervisor.recovery.*` and written to the audit log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .util import ResearchError, now_iso

TRACKED_DONE = ("collected", "cancelled")            # hpc states that are not "waiting for collection"
EVIDENCE_PENDING = "evidence_pending"                # category: the evidence was not there yet; repeating the task cannot create it
_NUMERIC = re.compile(r"check metric:.+? failed \((\S+) = (\S+) from .*\(needs (\S+) (\S+)\)")
_MISSING_METRIC = re.compile(r"check metric:.+? failed \((\S+) not found")
_MISSING_FILE = re.compile(r"check file: (.+?) failed \((?:missing|exists but is empty)\)")


def cfg(c: Dict[str, Any]) -> Dict[str, Any]:
    return (c or {}).get("recovery") or {}


# ----------------------------------------------------------------------------------------------- stages
def _hpc(run: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((run.get("slurm") or {}).get("hpc")) or {})


def stage(run: Dict[str, Any]) -> str:
    """slurm_pending | ended | collection_pending | collected | ready. `ready` = completed and nothing is left to bring home."""
    from .schema import RUN_TERMINAL
    if run["status"] not in RUN_TERMINAL:
        return "slurm_pending"
    if run["status"] != "completed":
        return "ended"
    s = _hpc(run).get("state")
    if run.get("backend") in ("slurm", "hpc-bundle") and s and s not in TRACKED_DONE:
        return "collection_pending"
    return "collected" if s == "collected" else "ready"


def uncollected(p, runs: List[Dict[str, Any]]) -> List[str]:
    from .runs import get_run
    return [r["id"] for r in (get_run(p, x["id"]) for x in runs) if stage(r) == "collection_pending"]


def waiting(a: Dict[str, Any], ids: List[str], c: Dict[str, Any]) -> bool:
    """True while completed runs are still being collected and the wait is inside its bound. Past the bound the attempt is verified on what exists and a
    miss is classed `evidence_pending` (a person is told; it is re-verified automatically if the results arrive)."""
    if not cfg(c).get("enabled", True):
        return False
    since = a.setdefault("collect_wait_since", now_iso())
    limit = float(cfg(c).get("collection_wait_minutes", 120)) * 60
    from . import supervisor as SV
    if SV._age(since) < limit:
        return True
    a["collection_overdue"] = sorted(ids)
    return False


def wait_text(ids: List[str]) -> str:
    return (f"Slurm finished {', '.join(ids)} but its results are not collected and ingested yet; verification waits for them (it is not a failure). "
            "Collection runs automatically; to do it now: research hpc collect RUN")


# ----------------------------------------------------------------------------------------------- fingerprints
def evidence_runs(p, tid: str) -> List[Dict[str, Any]]:
    from .runs import get_run
    ex = p.get("task", tid).get("related_experiment_id")
    return [get_run(p, r["id"]) for r in p.q("SELECT id FROM runs WHERE experiment_id=? ORDER BY id", (ex,))] if ex else []


def evidence_fp(p, tid: str) -> str:
    """What the task's checks can see: every run of its experiment (status, collection state, metric count) and its artifacts."""
    from .runs import list_artifacts, metrics_for_run
    rows: List[Any] = []
    for r in evidence_runs(p, tid):
        arts = sorted((a["id"], str(a.get("hash") or a.get("size") or "")) for a in list_artifacts(p, run=r["id"]))
        rows.append([r["id"], r["status"], _hpc(r).get("state"), len(metrics_for_run(p, r["id"])), arts])
    ex = p.get("task", tid).get("related_experiment_id")
    if ex:
        rows.append(["exp", (p.q1("SELECT COUNT(*) AS n FROM metrics WHERE experiment_id=? AND run_id IS NULL", (ex,)) or {}).get("n")])
    return hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]


def contract_fp(p, tid: str) -> str:
    t = p.get("task", tid)
    keys = ("checks", "acceptance", "expected_artifacts", "inputs", "datasets", "depends_on", "related_experiment_id")
    return hashlib.sha1(json.dumps({k: t.get(k) for k in keys}, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------------------------- diagnosis
def evidence(p, tid: str) -> Dict[str, Any]:
    """Facts for a person or the investigator: runs with their stage, numerical metrics, artifact paths and log tails. Redacted, size-capped."""
    from . import taskprompt
    from .runs import list_artifacts, summary_metrics
    runs = []
    for r in evidence_runs(p, tid)[-8:]:
        d = p.abspath(p.rel(p.path("runs", r["id"])))
        tail = ""
        for name in ("stderr.log", "stdout.log"):
            try:
                with open(os.path.join(d, "collected", "logs", name), encoding="utf-8", errors="replace") as f:
                    tail += f"[{name}] " + f.read()[-700:] + "\n"
            except OSError:
                pass
        runs.append({"run": r["id"], "backend": r.get("backend"), "status": r["status"], "exit_code": r.get("exit_code"), "stage": stage(r),
                     "hpc_state": _hpc(r).get("state"), "slurm_job_id": r.get("slurm_job_id"),
                     "metrics": {k: m.get("value") for k, m in list(summary_metrics(p, r["id"]).items())[:40]},
                     "artifacts": [a["path"] for a in list_artifacts(p, run=r["id"])[:20]], "log_tail": taskprompt.redact(tail) or ""})
    return {"runs": runs, "evidence_fingerprint": evidence_fp(p, tid)}


def diagnose(p, tid: str, a: Dict[str, Any], diagnostics: List[str]) -> Dict[str, Any]:
    """The deterministic root cause. {cause, text, needs_human[]}. Causes: collection_pending | late_metric | numerical | job_failed | missing_file | other."""
    text = " ".join(diagnostics)
    runs = evidence_runs(p, tid)
    if a.get("collection_overdue"):
        ids = ", ".join(a["collection_overdue"])
        return {"cause": "collection_pending", "text": f"Slurm finished {ids}, but its results were not collected within the wait, so the checks had nothing to read.",
                "needs_human": [f"get the results home: `research hpc collect {a['collection_overdue'][0]}` (the cluster may be unreachable). The task is re-verified automatically when they arrive."]}
    m = _NUMERIC.search(text)
    if m:
        return {"cause": "numerical", "text": f"{m.group(1)} = {m.group(2)} does not satisfy the required {m.group(3)} {m.group(4)}. The evidence is present; the result misses the criterion.",
                "needs_human": ["a scientific decision: Research OS never relaxes a threshold. Re-run with a changed method, or amend the criterion through an approved (PIN) amendment."]}
    if re.search(r"computational run\(s\) did not complete", text):
        bad = [f"{r['id']} [{r['status']}]" for r in runs if r["status"] in ("failed", "cancelled")]
        return {"cause": "job_failed", "text": "A computational run did not complete: " + (", ".join(bad) or "see the run logs") + ".", "needs_human": []}
    mm = _MISSING_METRIC.search(text)
    if mm:
        have = sorted({k for r in runs for k in _metric_names(p, r["id"])})
        return {"cause": "late_metric", "text": f"The required metric {mm.group(1)!r} is not recorded for the experiment (recorded: {', '.join(have[:10]) or 'none'}). "
                                                "If a run is still being collected or ingested it arrives later; otherwise the job did not log it.",
                "needs_human": []}
    mf = _MISSING_FILE.search(text)
    if mf:
        return {"cause": "missing_file", "text": f"The required file {mf.group(1)} is absent or empty.", "needs_human": []}
    return {"cause": "other", "text": (diagnostics or ["verification failed"])[0][:240], "needs_human": []}


def _metric_names(p, run_id: str) -> List[str]:
    from .runs import summary_metrics
    return list(summary_metrics(p, run_id))


def record(p, e: Dict[str, Any], a: Dict[str, Any], tid: str, diagnostics: List[str]) -> None:
    """Called when a verification failed: store the fingerprints the automatic re-verification compares, and the root cause."""
    v = a.get("verification") or {}
    v["evidence_fp"], v["contract_fp"] = evidence_fp(p, tid), contract_fp(p, tid)
    d = diagnose(p, tid, a, diagnostics)
    e["recovery"] = {"ts": now_iso(), "attempt": a.get("n"), "cause": d["cause"], "root_cause": d["text"], "needs_human": d["needs_human"],
                     "decision": None, "reverifications": a.get("reverifications", 0)}


def decide(e: Dict[str, Any]) -> None:
    """After the failure was routed, say in plain words what the supervisor decided (shown in the TUI and chat)."""
    r = e.get("recovery")
    if not r:
        return
    ph = e.get("phase")
    if r["cause"] == "missing_file" and ph == "repair_pending":
        r["decision"] = "a repair attempt is queued in the same worktree: the task did not write a file it owns, and no run can deliver it"
    elif r["cause"] in ("collection_pending", "late_metric", "missing_file") and ph in ("blocked", "attention", "repair_pending"):
        r["decision"] = ("waiting: the same attempt is re-verified automatically, unchanged, as soon as new results arrive; " +
                         ("a repair attempt is queued meanwhile" if ph == "repair_pending" else "no new attempt or job is started"))
    elif r["cause"] == "numerical":
        r["decision"] = "no automatic recovery: the evidence is complete and the criterion is not met"
    elif ph == "repair_pending":
        r["decision"] = "a repair attempt is queued (bounded by the attempt budgets)"
    elif ph in ("blocked", "attention"):
        r["decision"] = "stopped: waiting for you"
    else:
        r["decision"] = ph


def line(r: Optional[Dict[str, Any]]) -> str:
    """One readable block for chat/TUI: cause, decision, what needs a person."""
    if not r:
        return ""
    out = [f"Cause: {r.get('root_cause')}"]
    if r.get("decision"):
        out.append(f"Decision: {r['decision']}")
    if r.get("needs_human"):
        out.append("Needs you: " + " ".join(r["needs_human"]))
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- audit
def log(p, rs: Dict[str, Any], tid: str, action: str, detail: str) -> None:
    from . import supervisor as SV
    rs.setdefault("recovery_log", []).append({"ts": now_iso(), "task": tid, "action": action, "detail": detail[:300]})
    del rs["recovery_log"][:-200]
    SV._event(p, tid, "supervisor_recovery", f"{action}: {detail[:200]}")


# ----------------------------------------------------------------------------------------------- the step
def step(p, st: Dict[str, Any], rs: Dict[str, Any], c: Dict[str, Any], out: Dict[str, Any]) -> None:
    """For tasks stopped on a failed verification: refresh the evidence (collect, reconcile; read-only on the cluster and bounded) and, when it changed while
    the contract did not, re-verify the same attempt. Runs for running AND paused plans. Never starts an agent or a job."""
    rc = cfg(c)
    if not rc.get("enabled", True):
        return
    for tid, e in list(rs["tasks"].items()):
        if e.get("phase") not in ("blocked", "attention", "repair_pending"):
            continue
        standing = [x for x in e.get("attempts", []) if not x.get("voided")]
        a = standing[-1] if standing else None
        v = (a or {}).get("verification")
        if not a or not v or v.get("passed") or not v.get("evidence_fp") or not p.exists("task", tid):
            continue
        if int(a.get("reverifications", 0)) >= int(rc.get("max_reverifications", 5)):
            continue
        fp = evidence_fp(p, tid)
        if fp == v["evidence_fp"]:
            _refresh(p, rs, c, tid, e)
            fp = evidence_fp(p, tid)
            if fp == v["evidence_fp"]:
                continue
        if contract_fp(p, tid) != v.get("contract_fp"):
            continue             # the contract was amended: that path (intervention/resolve) decides, never this one
        _reverify(p, st, rs, c, tid, e, a, out)


def _refresh(p, rs: Dict[str, Any], c: Dict[str, Any], tid: str, e: Dict[str, Any]) -> None:
    """Safe operational actions only: bring results home and point artifacts at collected copies. Rate-limited and capped per task."""
    r = e.get("recovery") or {}
    if r.get("cause") not in ("collection_pending", "late_metric", "missing_file"):
        return
    rc = cfg(c)
    if time.time() - float(rs.get("last_recovery_sync", 0)) < float(c.get("hpc_poll_seconds", 30)):
        return
    if int(e.get("recovery_syncs", 0)) >= int(rc.get("max_syncs", 100)):
        return
    rs["last_recovery_sync"] = time.time()
    e["recovery_syncs"] = int(e.get("recovery_syncs", 0)) + 1
    if any(stage(x) == "collection_pending" for x in evidence_runs(p, tid)):
        try:
            from .hpc import remote as HR
            HR.sync(p, cluster_name=None)               # status + collection only: it never submits
        except Exception as ex:                          # noqa: BLE001 (unavailable is not a failure)
            log(p, rs, tid, "sync_unavailable", str(ex)[:160])
    try:
        from .runs import reconcile_collected
        for x in evidence_runs(p, tid):
            if stage(x) == "collected":
                reconcile_collected(p, x["id"])
    except (ResearchError, OSError):
        pass


def _reverify(p, st, rs, c, tid, e, a, out) -> None:
    from . import plans as P, supervisor as SV
    n = int(a.get("reverifications", 0)) + 1
    a["reverifications"] = n
    log(p, rs, tid, "reverify", f"attempt {a['n']}: new evidence under the unchanged contract (re-verification {n})")
    if p.get("task", tid)["status"] == "blocked":
        P.update_task(p, tid, status="todo", blockers=None)
    e.update(phase="verifying", reason=None)
    SV._verify_attempt(p, st, rs, tid, e, a, c, out)       # moves the earlier result into verification_history
    if e.get("phase") == "verified":
        log(p, rs, tid, "recovered", f"attempt {a['n']} verified after re-verification {n}; earlier result kept in verification_history")
        e["recovery"] = {**(e.get("recovery") or {}), "ts": now_iso(), "decision": f"recovered automatically: re-verified attempt {a['n']} once the evidence arrived", "needs_human": []}
        _resume_paused(rs, tid)
        _alert(p, st, tid, a)


def _resume_paused(rs: Dict[str, Any], tid: str) -> None:
    """A plan paused only because this task failed continues when the task recovers. A pause the person chose is never undone."""
    if rs.get("state") == "paused" and rs.get("paused_for") == tid:
        rs.update(state="running", paused_for=None)
        rs["history"].append({"ts": now_iso(), "event": "resumed", "why": f"{tid} recovered after its evidence arrived"})


def _alert(p, st, tid: str, a: Dict[str, Any]) -> None:
    try:
        from . import notify
        if notify.chat_enabled(p):
            from .chat import alerts
            alerts.raise_(p, f"recovery:{tid}:{a['n']}:{a.get('reverifications')}", "recovery", "info",
                          f"{tid} recovered automatically in plan {st['plan_key']}: its results arrived after Slurm finished; attempt {a['n']} was re-verified under the "
                          "unchanged checks and passed. No job was resubmitted.", task=tid, once=True)
    except Exception:       # noqa: BLE001 - an alert must never break recovery
        pass
