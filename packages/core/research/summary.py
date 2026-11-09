"""Plain-language summaries of what happened, for chat. Built only from recorded facts (hypothesis, runs, metric values, evidence checks):
no interpretation, no claim about what the results mean. Interpretation is the synthesis, which a person (or an analyst they launch) writes."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

OK_ARTIFACT = ("ok", "directory", "unhashed")


def _metrics(p, run_ids: List[str], limit: int = 5) -> str:
    if not run_ids:
        return ""
    rows = p.q(f"SELECT run_id, name, value, value_text, unit FROM metrics WHERE run_id IN ({','.join('?' * len(run_ids))}) ORDER BY id", tuple(run_ids))
    last: Dict[Tuple[str, str], str] = {}
    for r in rows:
        v = f"{r['value']:.4g}" if isinstance(r["value"], (int, float)) else str(r["value_text"] or "")
        last[(r["run_id"], r["name"])] = f"{r['name']}={v}{(' ' + r['unit']) if r['unit'] else ''}"
    items = [f"{rid}: {s}" for (rid, _), s in last.items()]
    return "; ".join(items[:limit]) + (f" (and {len(items) - limit} more recorded)" if len(items) > limit else "")


def _unverified(p, exp_id: str) -> List[str]:
    from . import evidence
    out = []
    for a in p.q("SELECT id FROM artifacts WHERE experiment_id=?", (exp_id,)):
        i = evidence.artifact_integrity(p, a["id"])
        if i["status"] not in OK_ARTIFACT:
            out.append(f"{a['id']} {i['status']}")
    return out


def started_text(p, exp_id: str) -> str:
    e = p.get("experiment", exp_id)
    return f"Experiment {exp_id} started: {e['title']}." + (f"\nTesting: {e['hypothesis']}" if e.get("hypothesis") else "")


def experiment_summary(p, exp_id: str) -> Tuple[str, str]:
    """(severity, text): what was done, the recorded results and what the reader should know. `warn` when something needs a look."""
    from .services import experiment_state
    e = p.get("experiment", exp_id)
    st = experiment_state(p, exp_id)
    runs = p.q("SELECT id, status, exit_code FROM runs WHERE experiment_id=? ORDER BY id", (exp_id,))
    failed = [r for r in runs if r["status"] == "failed"]
    done = [r for r in runs if r["status"] == "completed"]
    know: List[str] = []
    if failed:
        know.append(f"{len(failed)} run(s) failed ({', '.join(r['id'] for r in failed[:3])}) and are not yet reviewed" if st["unreviewed_failed"] else f"{len(failed)} run(s) failed")
    bad = _unverified(p, exp_id)
    if bad:
        know.append(f"{len(bad)} artifact(s) could not be verified ({', '.join(bad[:3])}); a finished job does not prove its files exist")
    quiet = [r["id"] for r in done if not p.q1("SELECT 1 AS x FROM metrics WHERE run_id=? LIMIT 1", (r["id"],))]
    if quiet:
        know.append(f"no metrics were logged for {', '.join(quiet[:3])}")
    res = _metrics(p, [r["id"] for r in done])
    lines = [f"Experiment {exp_id} finished: {e['title']} ({e['status'].replace('_', ' ')}).",
             f"Tested: {e['hypothesis']}" if e.get("hypothesis") else "No hypothesis is recorded.",
             f"Done: {len(runs)} run(s), {len(done)} completed, {len(failed)} failed.",
             f"Recorded results: {res}" if res else "Recorded results: none (no metric values logged).",
             "Know this: " + "; ".join(know) + "." if know else "Nothing flagged by the evidence checks."]
    if st["needs_synthesis"] or st["over_run_budget"]:
        lines.append("Next: a synthesis is due. These are recorded values, not an interpretation. Review in the TUI Inbox (research tui, i).")
    return ("warn" if know else "ok"), "\n".join(lines)


def run_summary(p, run_id: str) -> Tuple[str, str]:
    r = p.q1("SELECT id, experiment_id, status, exit_code FROM runs WHERE id=?", (run_id,))
    res = _metrics(p, [run_id], 4)
    ok = r["status"] == "completed" and r["exit_code"] in (0, None)
    return ("ok" if ok else "error"), f"{run_id} {r['status']}" + (f" (exit {r['exit_code']})" if r["exit_code"] is not None else "") + f" in {r['experiment_id']}" + (f": {res}" if res else "")
