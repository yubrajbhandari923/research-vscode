"""`research experiment check`: deterministic validation of an experiment's runs, kept apart from interpretation.

Verified by programs: runs reached a terminal state and completed with exit 0, metrics were logged, registered artifacts
still exist and match their hashes, and the project's approved post_experiment hooks pass. Everything that needs
judgement — failed runs nobody has reviewed, a synthesis that is due, unapproved hooks — is listed under `needs_human`.
The result is written to the event log; synthesis is only *requested* (a prepared analyst dispatch), never run.
HPC runs are read from the registry; `research hpc sync` is what refreshes them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import normalize_id
from .util import now_iso


def check(p, exp_id: str, request_synthesis: bool = False) -> Dict[str, Any]:
    from . import agents, evidence, hooks
    from .services import experiment_state
    exp_id = normalize_id(exp_id, "EXP")
    exp = p.get("experiment", exp_id)
    st = experiment_state(p, exp_id)
    runs = p.q("SELECT id, status, exit_code, backend FROM runs WHERE experiment_id=? ORDER BY id", (exp_id,))
    scope_ids = set(st["unsynthesized_ids"]) | {r["id"] for r in runs if r["status"] in ("queued", "running")}
    scope = [r for r in runs if r["id"] in scope_ids] or runs[-1:]
    validated: List[str] = []
    failed: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    human: List[str] = []

    def ok(msg: str) -> None:
        validated.append(msg)

    def bad(run: str, msg: str) -> None:
        failed.append({"run": run, "problem": msg})
    if not runs:
        human.append("no runs registered yet")
    for r in scope:
        rid = r["id"]
        if r["status"] in ("queued", "running"):
            pending.append({"run": rid, "state": r["status"], "next": "`research hpc sync`" if r["backend"] in ("slurm", "hpc-bundle") else "wait or `research run refresh`"})
            continue
        if r["status"] != "completed":
            bad(rid, f"run {r['status']}" + (f" (exit {r['exit_code']})" if r["exit_code"] is not None else ""))
            continue
        if r["exit_code"] != 0:
            bad(rid, f"completed status without a verified zero exit code ({r['exit_code']})")
            continue
        ok(f"{rid} completed" + (f" (exit {r['exit_code']})" if r["exit_code"] is not None else ""))
        n = p.q1("SELECT COUNT(*) AS n FROM metrics WHERE run_id=?", (rid,))["n"]
        (ok(f"{rid} logged {n} metric value(s)") if n else bad(rid, "completed but logged no metrics"))
        for a in p.q("SELECT id FROM artifacts WHERE run_id=?", (rid,)):
            i = evidence.artifact_integrity(p, a["id"])
            (ok(f"{a['id']} {i['status']}") if i["status"] in ("ok", "directory") else bad(rid, f"artifact {a['id']} {i['status']}: {i['detail']}"))
    hr = hooks.run(p, "post_experiment", experiment=exp_id, run_id=(scope[-1]["id"] if scope else None))
    for h in hr["ran"]:
        message = f"hook {h['hook']} failed: {h['output'].strip()[-300:]}"
        if h["ok"]:
            ok(f"hook {h['hook']} passed")
        elif h["required"]:
            bad(scope[-1]["id"] if scope else "-", message)
        else:
            human.append(message)
    human += [f"hook {s['hook']} skipped: {s['reason']}" for s in hr["skipped"]]
    if st["unreviewed_failed"]:
        human.append(f"{st['unreviewed_failed']} failed run(s) have not been reviewed (are they dead ends or bugs?)")
    due = bool(st["needs_synthesis"] or st["over_run_budget"])
    if due:
        human.append("synthesis is due: " + ("run budget reached" if st["over_run_budget"] else f"{st['unsynthesized']} finished run(s) not yet synthesized"))
    out: Dict[str, Any] = {"experiment": exp_id, "checked_at": now_iso(), "runs_checked": [r["id"] for r in scope],
                           "deterministic": {"validated": validated, "failed": failed, "pending": pending,
                                             "all_passed": not failed and not pending and not hr["blocked"] and bool(scope) and bool(validated)},
                           "needs_human": human, "synthesis": {"due": due, "dispatch": None}}
    if request_synthesis and due and not pending:   # interpretation stays with the analyst role; this only prepares it
        d = agents.dispatch(p, "analyst", exp_id)
        out["synthesis"]["dispatch"] = {"profile": d["profile"], "model": d["model"], "brief": d["brief"], "shell": d["shell"]}
    p.event("experiment", exp_id, "validated", f"{len(validated)} checks passed, {len(failed)} failed, {len(pending)} pending; "
            f"{len(human)} need a human" + ("; synthesis due" if due else ""))
    return out
