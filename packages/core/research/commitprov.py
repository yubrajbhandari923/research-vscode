"""Which commits on an experiment branch belong to which verified task (so automatic promotion neither loses verified work nor takes unverified work).

The supervisor commits verified work itself (`supervisor._commit_verified`) and records that commit on the attempt. But an agent often commits first: the
tree is then clean, the supervisor makes no commit, and the attempt has no `commit`. Those verified commits were invisible to promotion.

This module lists the experiment branch's own commits (`base_rev..HEAD`, minus merges and anything already in main) and gives every one of them exactly one
verdict, with the evidence for it:

    included     tied to a verified task: the supervisor's recorded commit; a supervisor trailer naming a recorded execution; or an agent commit inside the
                 attempt's own record (`head_before..head_after` where the launch recorded it, else the attempt's started..ended window), and no other task
                 claims it
    in_progress  claimed by one task that has not verified yet: left out, not an error
    excluded     a human said so (`research plan autopromote PLAN exclude --commit SHA`)
    orphan       no attempt claims it (a person's commit, or older than the attempt records)
    ambiguous    more than one task claims it (parallel agents in one worktree)

Orphans and ambiguous commits are never promoted and never ignored: they stop the candidate until a human attributes or excludes them, because
promoting around them could omit a verified deliverable or include unreviewed work. Nothing here rewrites history or edits an attempt.
Limits: commit dates and trailers are what the committer wrote, so this is attribution evidence, not proof against a deliberate forgery by an agent that
already has write access to the branch; the reviewer, frozen-test and test gates still apply to everything that is promoted.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from . import workspace as W
from .util import ResearchError, now_iso, parse_iso

SUPERVISOR = "Research OS supervisor"
_TRAILER = re.compile(r"^Research-(Task|Execution): *(\S+) *$", re.M)


def head(path: Optional[str]) -> Optional[str]:
    return (W._git(path, "rev-parse", "--verify", "-q", "HEAD") or None) if path else None


def state(rs: Dict[str, Any]) -> Dict[str, Any]:
    s = rs.setdefault("commit_provenance", {})
    for k in ("attributed", "decisions", "reports"):
        s.setdefault(k, {})
    return s


def branch_commits(path: str, base: str, main: Optional[str]) -> List[Dict[str, Any]]:
    """The experiment branch's own commits, oldest first (cherry-pick order). Merges (main brought into the branch) are not work and are skipped."""
    args = ["log", "--reverse", "--no-merges", "--format=%H%x01%ct%x01%an%x01%s%x01%b%x02", f"{base}..HEAD"]
    if main and W._git(path, "rev-parse", "--verify", "-q", f"refs/heads/{main}"):
        args.append(f"^{main}")
    out = []
    for rec in (W._git(path, *args) or "").split("\x02"):      # not \x1e/\x1f: str.strip() treats those as whitespace and would eat the last record's separators
        f = rec.strip("\n").split("\x01")
        if len(f) < 5 or not f[0].strip():
            continue
        tr = {k.lower(): v for k, v in _TRAILER.findall(f[4])}
        out.append({"sha": f[0].strip(), "ts": float(f[1] or 0), "author": f[2], "subject": f[3], "task": tr.get("task"), "execution": tr.get("execution")})
    return out


def _epoch(ts: Optional[str]) -> Optional[float]:
    d = parse_iso(ts) if ts else None
    return d.timestamp() if d else None


def _verified_attempt(e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    standing = [a for a in e.get("attempts", []) if not a.get("voided")]
    return next((a for a in reversed(standing) if a.get("state") == "verified"), None)


def ws_of(p, tid: str, a: Dict[str, Any]) -> Optional[str]:
    return (a.get("workspace") or {}).get("id") or (W.for_task(p, tid) or {}).get("id")


def classify(p, rs: Dict[str, Any], exp: str, ws: str, main: Optional[str], integrated: Callable[[str], bool]) -> Dict[str, Any]:
    s = W.show(p, ws)
    path, base = s["path"], s.get("base_rev")
    prov = state(rs)
    rep: Dict[str, Any] = {"workspace": ws, "experiment": exp, "base": base, "head": s.get("head"), "included": [], "in_progress": [], "excluded": [],
                           "orphans": [], "ambiguous": []}
    if not s.get("present") or not base:
        return rep
    tasks = {tid: e for tid, e in (rs.get("tasks") or {}).items() if p.exists("task", tid) and p.get("task", tid).get("related_experiment_id") == exp}
    recorded = {a["commit"]: (tid, a) for tid, e in tasks.items() for a in e.get("attempts", []) if a.get("commit") and a.get("state") == "verified" and not a.get("voided")}
    claims = []     # (task, attempt, range-or-None, lo, hi)
    for tid, e in tasks.items():
        for a in e.get("attempts", []):
            if a.get("state") == "launch_failed" or ws_of(p, tid, a) != ws:
                continue
            rng = None
            if a.get("head_before") and a.get("head_after"):
                rng = set((W._git(path, "rev-list", f"{a['head_before']}..{a['head_after']}") or "").split())
            lo = _epoch(a.get("started"))
            hi = _epoch(a.get("ended")) or _epoch((a.get("verification") or {}).get("ts"))
            claims.append((tid, a, rng, lo, hi))
    for c in branch_commits(path, base, main):
        sha = c["sha"]
        if integrated(sha):
            continue
        d, rec = prov["decisions"].get(sha), prov["attributed"].get(sha)
        if d and d["action"] == "exclude":
            rep["excluded"].append(sha)
            continue
        owner, basis = None, None
        if sha in recorded:
            owner, basis = recorded[sha][0], "recorded"
        elif d:                                                      # a human attributed it; the task must still be verified (checked below)
            owner, basis = d["task"], "human"
        elif rec:
            owner, basis = rec["task"], rec["basis"]
        else:
            ex = next((tid for tid, a, *_ in claims if c["execution"] and a.get("execution") == c["execution"] and c["author"] == SUPERVISOR
                       and c["task"] in (None, tid)), None)
            if ex:
                owner, basis = ex, "trailer"
            else:
                hit = {tid: (rng is not None) for tid, a, rng, lo, hi in claims
                       if (rng is not None and sha in rng) or (rng is None and lo is not None and lo <= c["ts"] and (hi is None or c["ts"] <= hi))}
                if c["task"] in hit and len(hit) > 1:
                    hit = {c["task"]: hit[c["task"]]}
                if len(hit) > 1:
                    rep["ambiguous"].append({"sha": sha, "subject": c["subject"], "tasks": sorted(hit)})
                    continue
                if not hit:
                    rep["orphans"].append({"sha": sha, "subject": c["subject"], "author": c["author"]})
                    continue
                (owner, by_range), = hit.items()
                basis = "head_range" if by_range else "attempt_window"
        va = _verified_attempt(tasks[owner]) if owner in tasks else None
        if not va:
            rep["in_progress"].append({"sha": sha, "task": owner})
            continue
        rep["included"].append({"sha": sha, "task": owner, "attempt": va["n"], "basis": basis, "profile": va.get("profile")})
    return rep


def reconcile(p, rs: Dict[str, Any], exp: str, ws: str, main: Optional[str], integrated: Callable[[str], bool], write: bool = True) -> Dict[str, Any]:
    """Classify the branch and (unless `write` is False) record the attribution of every included commit, once. Idempotent: a restart or a retry sees the
    same answer, and an attribution already recorded is never changed."""
    rep = classify(p, rs, exp, ws, main, integrated)
    rep["blocking"] = bool(rep["orphans"] or rep["ambiguous"])
    if write:
        prov = state(rs)
        for x in rep["included"]:
            if x["basis"] != "recorded" and x["sha"] not in prov["attributed"]:
                prov["attributed"][x["sha"]] = {"task": x["task"], "attempt": x["attempt"], "basis": x["basis"], "experiment": exp, "workspace": ws, "at": now_iso()}
        brief = {k: ([i["sha"][:10] if isinstance(i, dict) else i[:10] for i in v] if isinstance(v, list) else v) for k, v in rep.items()
                 if k in ("included", "in_progress", "excluded", "orphans", "ambiguous", "base", "head", "blocking")}
        if {k: v for k, v in (prov["reports"].get(ws) or {}).items() if k != "at"} != brief:
            prov["reports"][ws] = {**brief, "at": now_iso()}
    return rep


def describe(rep: Dict[str, Any]) -> str:
    bits = [f"{x['sha'][:10]} {x['subject'][:60]!r}" + (f" (claimed by {', '.join(x['tasks'])})" if x.get("tasks") else f" (by {x.get('author')})") for x in rep["orphans"] + rep["ambiguous"]]
    return (f"{len(bits)} commit(s) on {rep['experiment']}'s branch cannot be tied to one verified task: " + "; ".join(bits[:6]) + (" …" if len(bits) > 6 else "")
            + ". Promoting around them could omit verified work or include unreviewed work.")


def decide(rs: Dict[str, Any], action: str, commit: str, task: Optional[str], note: Optional[str], by: str) -> Dict[str, Any]:
    """A human's explicit answer for one commit: attribute it to a verified task, or exclude it. Never edits an attempt; recorded with who and why."""
    if action not in ("attribute", "exclude"):
        raise ResearchError("action must be attribute or exclude")
    if not commit:
        raise ResearchError("--commit is required")
    if action == "attribute":
        if not task or task not in (rs.get("tasks") or {}):
            raise ResearchError("--task must name a task of this plan")
        if not _verified_attempt(rs["tasks"][task]):
            raise ResearchError(f"{task} is not verified; a commit can be attributed only to verified work")
    s = state(rs)
    s["decisions"][commit] = {"action": action, "task": task, "note": note, "by": by, "at": now_iso()}
    return s["decisions"][commit]
