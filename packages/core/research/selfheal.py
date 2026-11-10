"""Self-healing: the debug agent's diagnosis acts, within bounds, for failures that are engineering problems and not decisions.

Off unless the researcher turns on `supervisor_assistant.self_heal` ("Automatically diagnose and repair recoverable failures"). It lives in the supervisor's own tick, next to
automatic promotion, so there is no extra service and nothing to start by hand; every step is persisted in the run state (`rs["selfheal"]`) before it is taken, so a
restart or a crash resumes it and nothing is done twice.

A paused automatic promotion is diagnosed from its own record (deterministic: no agent can invent a cause). Each gate has a recipe:

    scientific content (an executable cell, an identifier in prose, ...)   repair the SOURCE; check = `research heal check-source FILES`
    tests failed                                                            repair the code; check = the promotion test command (never edited)
    reviewer requested changes                                              address the objection; check = the promotion test command
    anything else (frozen/protected tests, numerical oracle, large files, unattributed commits, policy suspended, unknown) -> asks you, with the reason

A recipe adds ONE repair task to the experiment (an ordinary supervised task: its own worktree branch, the plan's existing agent profile and attempt budget, the
supervisor's verification, its commit attributed to it by `commitprov`). It is recorded as created by the supervisor under the researcher's standing opt-in, never as a
person. When the task verifies, the paused promotion is retried through the same path a human's retry takes: a NEW candidate from the current main, the promotion tests,
and a fresh independent review all run again, then the fast-forward. Nothing is bypassed and the candidate itself is never edited.

Bounds: `max_repairs` per failure, `max_repairs_per_plan`; the same failure after a repair (same fingerprint) stops it; a repair task that fails or exhausts its budget counts as
a failed repair. Anything it stops on stays paused, the plan is HALTED, and the usual alert tells you why.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

from . import planspec as PS, promotion as PR, registry
from .util import ResearchError, now_iso

HARD_RULES = ("Do NOT edit, delete, skip or weaken any test, oracle, threshold or acceptance criterion. Do NOT change a hypothesis, a dataset, a result or any evidence, and do not invent "
              "or alter numbers. Do NOT change settings, approvals, permissions or governance, and do not push anything. Fix the underlying source with the smallest change, commit nothing else, "
              "and run the check below until it passes.")
UNSAFE = ("promoted work may not modify or delete an existing test", "ledger files are never promoted", "exceeds auto_promotion.max_file_mb", "numerical checks failed",
          "cannot be tied to one verified task", "verification policy changed", "standing authorization", "no independent review arrived", "reviewer profile")


def cfg(p) -> Dict[str, Any]:
    return registry.section(p, "supervisor_assistant")


def enabled(p) -> bool:
    return bool(cfg(p).get("self_heal"))


def authorize_item(rs: Dict[str, Any], item_id: str, job: str) -> None:
    """A job (a human's delegated request) authorizes self-healing for ONE promotion, even with the project-wide setting off. Recorded in the run state."""
    S = _state(rs)
    S.setdefault("jobs", {})[item_id] = job


def _allowed(p, rs, item_id: str) -> bool:
    return enabled(p) or item_id in ((rs.get("selfheal") or {}).get("jobs") or {})


def _state(rs: Dict[str, Any]) -> Dict[str, Any]:
    return rs.setdefault("selfheal", {"cases": {}, "repairs": 0})


def _research() -> str:
    return "research" if shutil.which("research") else f"{sys.executable} -m research_cli.main"


def _fp(kind: str, parts: List[str]) -> str:
    return hashlib.sha256((kind + "|" + "|".join(sorted(parts))).encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------------------------- deterministic diagnosis
def diagnose_promotion(p, st, rs, item: Dict[str, Any]) -> Dict[str, Any]:
    """What stopped this promotion and whether a repair task can fix it. Reads the candidate's own gates; changes nothing."""
    err = str(item.get("error") or "")
    low = err.lower()
    if any(u in low for u in (x.lower() for x in UNSAFE)):
        return {"recoverable": False, "class": "needs_you", "why": "this is a protected, scientific or permission matter, not an engineering fault: " + err[:200], "fingerprint": _fp("needs_you", [err[:80]])}
    if not item.get("promotion"):
        return {"recoverable": False, "class": "no_candidate", "why": "no candidate was prepared, so there is nothing to diagnose: " + err[:200], "fingerprint": _fp("no_candidate", [err[:80]])}
    c = PR.show(p, item["promotion"])
    g = c["gates"]
    sci = g["scientific_content"]
    if sci["state"] == "failed":
        issues = sorted(sci.get("issues") or [sci.get("detail") or ""])
        return {"recoverable": True, "class": "scientific_content", "files": sci.get("files") or [], "issues": issues, "fingerprint": _fp("sc", issues),
                "summary": "scientific-content provenance failed: " + "; ".join(issues)[:300]}
    if g["tests"]["state"] == "failed":
        out = (g["tests"].get("output") or "")[-1500:]
        return {"recoverable": True, "class": "tests_failed", "output": out, "fingerprint": _fp("tf", [out[-300:]]), "summary": "the promotion tests failed:\n" + out[-600:]}
    if g["review"]["state"] == "changes_requested":
        note = str(g["review"].get("note") or "")
        return {"recoverable": True, "class": "review_changes", "note": note, "fingerprint": _fp("rc", [note]), "summary": "the independent reviewer asked for changes: " + note[:300]}
    return {"recoverable": False, "class": "unknown", "why": "no deterministic recipe fits: " + err[:200] + " (use /debug for the agent's diagnosis)", "fingerprint": _fp("unknown", [err[:80]])}


def _check_for(p, dx: Dict[str, Any]) -> Optional[str]:
    """The deterministic check that proves the repair: the failing gate's own question, or the promotion test command (never edited here)."""
    if dx["class"] == "scientific_content":
        return f"{_research()} heal check-source " + " ".join(dx["files"])
    from . import autopromote as AP
    pol = AP.policy(p)
    return pol["test_command"] or None


def _prompt(p, item: Dict[str, Any], dx: Dict[str, Any], check: str, n: int) -> str:
    what = {"scientific_content": "The promotion's scientific-content gate failed. Fix the offending report source (for example turn an executable code cell into a plain code block, move "
                                  "internal identifiers out of scientific prose); do not touch the numbers or claims.",
            "tests_failed": "The promotion's integration tests failed. Fix the code so they pass; the tests are frozen and must not be edited.",
            "review_changes": "The independent reviewer asked for changes. Make exactly the change they ask for, within the hard rules."}[dx["class"]]
    extra = str(cfg(p).get("repair_instructions") or "").strip()
    return (f"SELF-HEALING REPAIR {n} for the failed promotion {item['id']} ({item['experiment']}).\n\n{what}\n\nWHAT FAILED:\n{dx['summary']}\n\n"
            f"HARD RULES: {HARD_RULES}\n\nCHECK (must pass): {check}\n" + (f"\n{extra}\n" if extra else ""))


# ----------------------------------------------------------------------------------------------- the repair task
def _add_task(p, st, item: Dict[str, Any], key: str, title: str, prompt: str, check: str, profile: Optional[str]) -> str:
    """Add the repair task to the experiment's plan, idempotently (a task already there unchanged is not added twice). Same schema preflight an intervention gets."""
    from . import intervene as I, supervisor as SV, yamlio
    if key in ((PS.load_state(p, st["plan_key"]) or st).get("tasks") or {}):
        return "present"                                       # added on an earlier pass (or before a restart): never compared again, never added twice
    src_tid = (item.get("sources") or [{}])[0].get("task")
    hit = PS.find_task_key(p, src_tid) if src_tid else None
    if not hit:
        raise ResearchError("the experiment's tasks are not in a managed plan")
    st, src_key = hit
    task = {"key": key, "title": title, "prompt": prompt, "checks": [f"cmd:{check}"], "execution": {"max_attempts": 2},
            "agent": {"role": "implementer", **({"profile": profile} if profile else {})}}
    act = {"type": "add_task", "task": task}
    I.preflight(p, src_tid, [act])
    doc = PS.parse(PS.export(p, st["plan_key"], task=src_key))
    if I._patch(doc["plans"][0], src_key, act, I._lookup(p, st)) == "present":
        return "present"
    with SV.human_grant("self-healing: the researcher's standing opt-in (supervisor_assistant.self_heal)"):
        PS.apply(p, yamlio.dump(doc, block=True), source="selfheal", author_note=f"self-healing repair task {key} for {item['id']} (created by the supervisor, not a person)")
    return "added"


# ----------------------------------------------------------------------------------------------- the lifecycle (one bounded step per tick)
def step(p, st, rs, c, out) -> None:
    from . import autopromote as AP
    if rs.get("state") != "running" or not (enabled(p) or (rs.get("selfheal") or {}).get("jobs")):
        return
    S = _state(rs)
    for item in (AP._state(rs)["items"] or []):
        live = (S["cases"].get(f"promotion:{item['id']}") or {}).get("phase") in ("repairing", "retrying", "diagnose")
        if (item["status"] in AP.TERMINAL and not live) or not _allowed(p, rs, item["id"]):
            continue                                           # an integrated item still needs its case closed (recovered) once
        cid = f"promotion:{item['id']}"
        case = S["cases"].get(cid)
        try:
            _drive(p, st, rs, item, cid, case, S, out)
        except ResearchError as ex:
            _escalate(p, item, S["cases"].setdefault(cid, {"id": cid, "attempt": 0, "history": []}), f"self-healing hit a problem: {ex}", out)


def _drive(p, st, rs, item, cid, case, S, out) -> None:
    from . import autopromote as AP
    mx, cap = int(cfg(p)["max_repairs"]), int(cfg(p)["max_repairs_per_plan"])
    if case is None:
        if item["status"] != "paused" or item.get("transient") or AP.retrying(p, item):
            return                                             # only a stopped, non-transient promotion is a failure; transient ones have their own bounded retry
        case = S["cases"][cid] = {"id": cid, "kind": "promotion", "item": item["id"], "attempt": 0, "history": [], "phase": "diagnose", "opened": now_iso()}
    if case["phase"] in ("recovered", "escalated"):
        return
    if case["phase"] == "diagnose":
        if item["status"] != "paused":
            return
        dx = diagnose_promotion(p, st, rs, item)
        if not dx["recoverable"]:
            return _escalate(p, item, case, dx["why"], out)
        if any(h["fingerprint"] == dx["fingerprint"] and h.get("outcome") == "failed_again" for h in case["history"]):
            return _escalate(p, item, case, "the same failure came back after a repair that verified and was rebuilt: stopping instead of looping", out)
        if case["attempt"] >= mx:
            return _escalate(p, item, case, f"{mx} repair attempt(s) did not fix it", out)
        if S["repairs"] >= cap:
            return _escalate(p, item, case, f"the plan's repair budget ({cap}) is used", out)
        check = _check_for(p, dx)
        if not check:
            return _escalate(p, item, case, "no promotion test command is configured, so a repair could not be verified", out)
        n = case["attempt"] + 1
        profile = next((x.get("profile") for x in (item.get("sources") or []) if x.get("profile")), None)
        case.update(phase="repairing", attempt=n, task_key=f"heal-{item['id'].lower()}-{n}", dx=dx, item_attempt=item.get("attempt", 1), check=check, profile=profile)
        S["repairs"] += 1
        case["history"].append({"attempt": n, "fingerprint": dx["fingerprint"], "class": dx["class"], "task_key": case["task_key"], "opened": now_iso()})
        item["healing"] = True
        out["events"].append(f"self-healing {item['id']}: {dx['class']}; repair task {case['task_key']} (attempt {n} of {mx})")
        return                                                 # persisted before anything is created: a crash here resumes at `repairing`
    if case["phase"] == "repairing":
        item["healing"] = True
        _add_task(p, st, item, case["task_key"], f"Repair {item['id']}: {case['dx']['class'].replace('_', ' ')}", _prompt(p, item, case["dx"], case["check"], case["attempt"]), case["check"], case.get("profile"))
        tid = (PS.load_state(p, st["plan_key"]) or st)["tasks"].get(case["task_key"])
        phase = ((rs.get("tasks") or {}).get(tid) or {}).get("phase") if tid else None
        case["task"] = tid
        if phase == "verified":
            if item["status"] == "paused" and item.get("attempt", 1) == case["item_attempt"]:    # idempotent: a retry already taken moved the attempt on
                AP.retry(p, st, rs, item["id"])
            case["phase"] = "retrying"
            out["events"].append(f"self-healing {item['id']}: repair {case['task_key']} verified; candidate rebuilt and re-checked")
        elif phase in ("blocked", "attention"):
            case["history"][-1]["outcome"] = "repair_failed"
            case["phase"] = "diagnose"                         # the next pass diagnoses again: another repair if the bounds allow, else it asks you
            out["events"].append(f"self-healing {item['id']}: repair {case['task_key']} did not verify")
        return
    if case["phase"] == "retrying":
        item["healing"] = True
        if item["status"] == "integrated":
            _recovered(p, st, item, case, out)
        elif item["status"] == "paused" and not item.get("transient"):
            case["history"][-1]["outcome"] = "failed_again"
            case["phase"] = "diagnose"
        elif item["status"] in ("noop", "cancelled"):
            case["phase"] = "escalated"
            item.pop("healing", None)


def _escalate(p, item, case, why: str, out) -> None:
    case.update(phase="escalated", reason=why, closed=now_iso())
    item.pop("healing", None)
    item["error"] = f"{item.get('error')} [self-healing stopped: {why}]"
    out["events"].append(f"self-healing {item['id']} stopped: {why}")


def _recovered(p, st, item, case, out) -> None:
    case.update(phase="recovered", closed=now_iso())
    item.pop("healing", None)
    out["events"].append(f"self-healing {item['id']}: recovered by {case.get('task_key')}")
    try:
        from . import notify
        if notify.chat_enabled(p):
            from .chat import alerts
            h = case["history"][-1]
            alerts.raise_(p, f"selfheal:{st['plan_key']}:{case['id']}:{case['attempt']}", "recovery", "ok",
                          f"Recovered by itself: {item['id']} ({item['experiment']}) had stopped ({h['class'].replace('_', ' ')}). Repair task {case.get('task_key')} was verified by the supervisor, "
                          f"the candidate was rebuilt, re-tested and independently re-reviewed, and it is in {PR._main_branch(p)} at {str(item.get('main_after') or '')[:8]}. No scientific claim was approved.", once=True)
    except Exception:   # noqa: BLE001
        pass


def status(rs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"id": c["id"], "phase": c.get("phase"), "attempt": c.get("attempt"), "class": ((c.get("history") or [{}])[-1]).get("class"), "task": c.get("task_key"), "reason": c.get("reason")}
            for c in (rs.get("selfheal") or {}).get("cases", {}).values()]


def retry_allowed(p, tid: str) -> bool:
    """May the automatic diagnosis restart this TASK? Only with self-healing on and while the task's own attempt budget (and the plan's) has room; an exhausted budget asks you."""
    from . import jobs as JB, supervisor as SV
    hit = PS.find_task_key(p, tid)
    if not hit:
        return False
    st = hit[0]
    if not (enabled(p) or JB.authorizes(p, st["plan_key"])):
        return False
    rs = SV.load_run(p, st["plan_id"]) or {}
    c = SV.cfg(p, st)
    e = (rs.get("tasks") or {}).get(tid) or {}
    used = len([a for a in e.get("attempts", []) if not a.get("voided")])
    allowed = int((SV._flat(st, tid).get("execution") or {}).get("max_attempts") or c["max_attempts_per_task"])
    return used < allowed and int((rs.get("budgets") or {}).get("attempts") or 0) < int(c["max_total_attempts"])
