"""Deterministic research supervisor: schedules plan tasks, launches agents, verifies, repairs within bounds.

No LLM decides anything here. Scheduling, ownership, retries, approvals and every transition to "verified" are plain code
over durable files in `.research/supervisor/`:

    plans/<PLAN-ID>.json     run state: plan state, per-task phase and attempt ledger, budgets
    specs/<plan-key>.json    desired plan (see planspec.py)
    reports/<T-ID>.jsonl     structured reports from agents (attributed to an execution)
    proposals/<n>.json       plan-change proposals
    daemon.json              pid / heartbeat of the persistent process (informational; a stale heartbeat proves nothing)

Execution attempts reuse `execution.py` records (prepared -> launched -> running -> completed/failed/cancelled/unknown).
An attempt that exits 0 is *not* a passed task: `verify_attempt` decides, and only it (or an explicit human `accept`) may
complete a supervised task.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from typing import Any, Dict, Iterator, List, Optional

from . import autopromote as AP, durability, planspec as PS, registry, taskprompt
from .store import Project, _atomic_write
from .util import NoFrozenTests, PolicyBlocked, ResearchError, now_iso

PHASES = ("waiting", "ready", "awaiting_approval", "waiting_provider", "running", "waiting_hpc", "verifying", "repair_pending", "attention", "blocked",
          "verified", "retired")
REPAIRABLE = {"incomplete_deliverable", "incorrect_implementation", "agent_process_failure", "insufficient_test_oracle", "ambiguous_specification"}
NEEDS_HUMAN = {"environment", "resource_limit", "unknown_execution_state"}
CATEGORIES = ("missing_prerequisite", "role_contradiction", "incomplete_deliverable", "incorrect_implementation", "ambiguous_specification", "insufficient_test_oracle", "environment", "scientific_assumption",
              "resource_limit", "agent_process_failure", "unknown_execution_state")


# ----------------------------------------------------------------------------------------------- config & paths
def cfg(p: Project, plan_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = registry.section(p, "supervisor")
    ex = ((plan_state or {}).get("spec") or {}).get("execution") or {}
    for k in ("max_parallel_agents", "max_attempts_per_task", "max_total_attempts", "max_wall_minutes", "on_failure", "max_hpc_resubmissions",
              "require_approval_for_scientific_changes", "escalation"):
        if k in ex:
            c[k] = ex[k]
    return c


def sdir(p: Project, *parts: str) -> str:
    d = p.path("supervisor", *parts)
    os.makedirs(os.path.dirname(d) if parts else d, exist_ok=True)
    return d


def _lock(p: Project):
    return durability.lock(sdir(p, "state.lock"))


def _rs_path(p: Project, plan_id: str) -> str:
    return sdir(p, "plans", plan_id + ".json")


def load_run(p: Project, plan_id: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_rs_path(p, plan_id), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_run(p: Project, rs: Dict[str, Any]) -> None:
    rs["updated_at"] = now_iso()
    _atomic_write(_rs_path(p, rs["plan_id"]), json.dumps(rs, indent=1, sort_keys=True, default=str) + "\n")


def all_runs(p: Project) -> List[Dict[str, Any]]:
    d = sdir(p, "plans", "x")
    d = os.path.dirname(d)
    return [json.load(open(os.path.join(d, n), encoding="utf-8")) for n in sorted(os.listdir(d)) if n.endswith(".json")]


def _event(p: Project, tid: Optional[str], action: str, summary: str) -> None:
    p.event("task" if tid else "plan", tid, action, taskprompt.redact(summary) or "")


_HUMAN_GRANTS: List[str] = []   # in-process only: pushed while an intervention a human already approved is being applied


@contextlib.contextmanager
def human_grant(reason: str) -> Iterator[None]:
    _HUMAN_GRANTS.append(reason)
    try:
        yield
    finally:
        _HUMAN_GRANTS.pop()


def require_human(p: Project, what: str) -> None:
    if _HUMAN_GRANTS:
        return
    if (p.author or {}).get("author_type") not in (None, "human") :
        from . import approval
        raise PolicyBlocked(f"{what} is a human decision: agents cannot do it", hint="Ask the researcher; supervisor policy is not agent-editable." + approval._env_hint())


# ----------------------------------------------------------------------------------------------- completion authority
_AUTH: Dict[str, str] = {}


@contextlib.contextmanager
def authority(tid: str, reason: str) -> Iterator[None]:
    """In-process grant for the supervisor's verification transition (or a human `accept`). Not reachable from an agent process."""
    _AUTH[tid] = reason
    try:
        yield
    finally:
        _AUTH.pop(tid, None)


def is_supervised(p: Project, tid: str) -> bool:
    if not (p.config.get("supervisor") or {}).get("enforce_completion", registry.default("supervisor.enforce_completion")):
        return False
    hit = PS.find_task_key(p, tid)
    if not hit:
        return False
    rs = load_run(p, hit[0].get("plan_id") or "")
    return bool(rs and rs.get("state") in ("running", "paused", "blocked", "attention"))


def guard_completion(p: Project, tid: str) -> None:
    """Called by every path that can set a task to done (CLI/RPC/MCP update, direct file import). Fail closed."""
    if tid in _AUTH or not is_supervised(p, tid):
        return
    raise PolicyBlocked(f"{tid} is supervised: only the supervisor's verification (or a human `research supervisor accept {tid} --reason …`) "
                        "can complete it",
                        hint="Finish with `research agent finish --request-verification`; the supervisor verifies after you exit.")


# ----------------------------------------------------------------------------------------------- plan control
def _plan_state(p: Project, ref: str) -> Dict[str, Any]:
    return PS._state_for(p, ref)


def plan_run(p: Project, ref: str, start_daemon: bool = True) -> Dict[str, Any]:
    require_human(p, "Starting a plan run")
    st = _plan_state(p, ref)
    with _lock(p):
        rs = load_run(p, st["plan_id"])
        if rs and rs["state"] in ("running",):
            return status(p, ref)
        rs = rs or {"plan_id": st["plan_id"], "plan_key": st["plan_key"], "tasks": {}, "budgets": {"attempts": 0, "wall_seconds": 0.0},
                    "created_at": now_iso(), "approvals": {}, "history": []}
        rs.update(state="running", started_at=rs.get("started_at") or now_iso(), last_tick=time.time())
        rs["history"].append({"ts": now_iso(), "event": "run"})
        save_run(p, rs)
    p.event("plan", st["plan_id"], "supervisor_run", f"plan {st['plan_key']} run requested")
    out = status(p, ref)
    if start_daemon:
        out["daemon"] = daemon_start(p, detach=True)
    return out


def autopromote(p: Project, ref: str, action: str, note: Optional[str] = None, pin: Optional[str] = None, item: Optional[str] = None) -> Dict[str, Any]:
    """Human-only control of unattended code promotion: authorize | revoke | retry | status. Authorization needs the approval PIN."""
    st = _plan_state(p, ref)
    if action != "status":
        require_human(p, f"autopromote {action}")
    rs = load_run(p, st["plan_id"])
    if not rs:
        raise ResearchError(f"{st['plan_key']} has not been run yet", hint=f"`research plan run {st['plan_id']}`")
    if action == "status":
        return AP.summary(p, st, rs)
    if action == "authorize":
        AP.check_ready(p, st)
        from . import approval            # prompts for the PIN BEFORE the state lock is taken
        approval.confirm(p, f"Authorize unattended code promotion for plan {st['plan_key']}", note, pin)
    with _lock(p):
        rs = load_run(p, st["plan_id"])
        if action == "authorize":
            out = AP.authorize(p, st, rs, note)
        elif action == "revoke":
            out = AP.revoke(p, st, rs)
        elif action == "retry":
            out = AP.retry(p, st, rs, item or "")
        else:
            raise ResearchError("action must be authorize, revoke, retry or status")
        save_run(p, rs)
    p.event("plan", st["plan_id"], "autopromote_" + action, f"plan {st['plan_key']}: automatic promotion {action}" + (f" ({note})" if note else ""))
    return out


def _set_state(p: Project, ref: str, state: str, only_from: Optional[tuple] = None) -> Dict[str, Any]:
    require_human(p, f"Changing a plan run to {state}")
    st = _plan_state(p, ref)
    with _lock(p):
        rs = load_run(p, st["plan_id"])
        if not rs:
            raise ResearchError(f"{st['plan_key']} has not been run yet", hint=f"`research plan run {st['plan_id']}`")
        if only_from and rs["state"] not in only_from:
            raise ResearchError(f"Plan is {rs['state']}; cannot {state}")
        rs["state"] = state
        rs["history"].append({"ts": now_iso(), "event": state})
        save_run(p, rs)
    p.event("plan", st["plan_id"], "supervisor_" + state, f"plan {st['plan_key']} {state}")
    return status(p, ref)


def plan_pause(p: Project, ref: str) -> Dict[str, Any]:
    return _set_state(p, ref, "paused", ("running",))


def plan_resume(p: Project, ref: str) -> Dict[str, Any]:
    out = _set_state(p, ref, "running", ("paused", "blocked", "attention"))
    out["daemon"] = daemon_start(p, detach=True)
    return out


def plan_stop(p: Project, ref: str, cancel_agents: bool = False) -> Dict[str, Any]:
    from . import execution as E
    out = _set_state(p, ref, "stopped")
    if cancel_agents:
        rs = load_run(p, _plan_state(p, ref)["plan_id"]) or {}
        for t, e in (rs.get("tasks") or {}).items():
            a = (e.get("attempts") or [])[-1:] or [None]
            if a[0] and e.get("phase") == "running":
                try:
                    E.stop(p, a[0]["execution"])
                except ResearchError:
                    pass
    return out


# ----------------------------------------------------------------------------------------------- status
def _topo(st: Dict[str, Any]) -> List[str]:
    deps = {t["key"]: [d for d in t["depends_on"] if d in {x["key"] for x in st["spec"].get("tasks_flat", [])}] for t in st["spec"].get("tasks_flat", [])}
    order, seen = [], set()
    def visit(k: str) -> None:
        if k in seen:
            return
        seen.add(k)
        for d in deps.get(k, []):
            visit(d)
        order.append(k)
    for k in deps:
        visit(k)
    return order


ACTIVE_PHASES = ("running", "verifying", "waiting_hpc", "ready", "repair_pending")
HUMAN_PHASES = ("blocked", "attention", "awaiting_approval")


def plan_health(p: Project, st: Dict[str, Any], rs: Dict[str, Any]) -> Dict[str, Any]:
    """What the plan is really doing. `state` stays the run state (running/paused/...); `health` says whether anything is actually moving:
    progressing | waiting_for_approval | halted | completed | paused | not_run. Halted = running, nothing active, something waits for a human."""
    if not rs:
        return {"state": "not_run", "label": "not run", "blocking": [], "fingerprint": ""}
    tasks = rs.get("tasks") or {}
    live = [k for k in st["tasks"] if k not in (st.get("retired") or {})]
    rows = []
    for k in live:
        tid = st["tasks"][k]
        if not p.exists("task", tid):
            continue
        e = tasks.get(tid) or {}
        ph = e.get("phase") or ("verified" if p.get("task", tid)["status"] == "done" else "waiting")
        rows.append((k, tid, ph, e.get("reason")))
    unfinished = [r for r in rows if r[2] != "verified"]
    if rs.get("state") == "completed" or (rows and not unfinished):
        return {"state": "completed", "label": "completed", "blocking": [], "fingerprint": ""}
    if rs.get("state") != "running":
        return {"state": "paused" if rs.get("state") == "paused" else rs.get("state", "stopped"), "label": rs.get("state", "stopped"), "blocking": [], "fingerprint": ""}
    moving = [r for r in unfinished if r[2] in ACTIVE_PHASES]
    ap = (rs.get("autopromote") or {}).get("items") or []
    promo = any(i["status"] not in ("integrated", "noop", "cancelled", "paused") for i in ap)
    iv = False
    try:
        from . import intervene as IV
        iv = any(i["plan_id"] == st["plan_id"] and i["state"] in ("approved", "implementing", "verifying") for i in IV.list_all(p, open_only=True))
    except (ResearchError, OSError):
        pass
    blocking = [{"task": tid, "key": k, "title": p.get("task", tid)["title"], "phase": ph, "reason": reason} for k, tid, ph, reason in unfinished if ph in HUMAN_PHASES]
    paused_promos = [{"task": i["id"], "key": i["id"], "title": f"promotion of {i['experiment']}", "phase": "blocked", "reason": i.get("error")} for i in ap if i["status"] == "paused"]
    wp = waiting_provider(rs)
    wpx = {"waiting_provider": wp} if wp else {}
    if moving or promo or iv:
        return {"state": "progressing", "label": "running", "blocking": blocking, "fingerprint": "", "moving": len(moving), **wpx}
    only_approval = blocking and all(b["phase"] == "awaiting_approval" for b in blocking) and not paused_promos
    stuck = blocking + paused_promos
    if not stuck and wp:
        return {"state": "waiting_for_provider", "label": f"waiting for {wp['provider']} until {_hhmm(wp['until_ts'])}", "blocking": [], "fingerprint": "", **wpx}
    if not stuck:
        return {"state": "idle", "label": "running (idle: waiting on dependencies)", "blocking": [], "fingerprint": ""}
    fp = "|".join(sorted(f"{b['task']}:{b['phase']}:{(b.get('reason') or '')[:60]}" for b in stuck))
    return {"state": "waiting_for_approval" if only_approval else "halted", "label": "WAITING FOR YOUR APPROVAL" if only_approval else "HALTED (needs you)",
            "blocking": stuck, "fingerprint": fp, **wpx}


def concurrency(p: Project, st: Dict[str, Any], rs: Dict[str, Any]) -> Dict[str, Any]:
    """How many agents may run at once and where that number comes from. Existing machinery (cfg, _schedule, the workspace lease) does the
    work; this only reports it. Tasks that share a worktree are always serialized by the lease, whatever the number."""
    # the effective config always carries the default, so "set by the project" means present in the project's own (sparse) config file
    proj = ((getattr(p, "raw_config", None) or {}).get("supervisor") or {}).get("max_parallel_agents")
    plan = ((st.get("spec") or {}).get("execution") or {}).get("max_parallel_agents")
    eff = int(cfg(p, st)["max_parallel_agents"])
    src = "plan YAML (execution.max_parallel_agents)" if plan else "project settings (supervisor.max_parallel_agents)" if proj else "built-in default"
    tasks = (rs or {}).get("tasks") or {}
    return {"effective": eff, "source": src, "plan": plan, "project": proj, "default": registry.default("supervisor.max_parallel_agents"),
            "running": len(_active(rs)) if rs else 0, "ready_waiting": sum(1 for e in tasks.values() if e.get("phase") == "ready"),
            "note": "Tasks in the same experiment worktree never run at the same time."}


def status(p: Project, ref: str) -> Dict[str, Any]:
    from . import execution as E
    st = _plan_state(p, ref)
    rs = load_run(p, st["plan_id"]) or {}
    c = cfg(p, st)
    rows = []
    flat = {t["key"]: t for t in st["spec"].get("tasks_flat", [])}
    for key in _topo(st):
        tid = st["tasks"].get(key)
        if not tid or not p.exists("task", tid):
            continue
        t = p.get("task", tid)
        e = (rs.get("tasks") or {}).get(tid) or {}
        att = e.get("attempts") or []
        last = att[-1] if att else None
        ex = None
        if last:
            try:
                ex = E.show(p, last["execution"])
            except (ResearchError, OSError):
                ex = None
        phase = "retired" if key in (st.get("retired") or {}) else e.get("phase") or ("verified" if t["status"] == "done" else "waiting")
        rows.append({"key": key, "id": tid, "title": t["title"], "experiment": flat[key].get("experiment"), "status": t["status"], "phase": phase,
                     "depends_on": [st["tasks"].get(d, d) for d in flat[key]["depends_on"]], "role": flat[key]["agent"].get("role"),
                     "profile": flat[key]["agent"].get("profile"), "attempts": len([x for x in att if not x.get("voided")]), "voided_attempts": len([x for x in att if x.get("voided")]), "reason": e.get("reason"),
                     "execution": last and {"id": last["execution"], "state": ex and ex["state"], "alive": ex and ex.get("alive"),
                                            "kind": last["kind"], "verification": last.get("verification"), "outcome": last.get("outcome")},
                     "requires_approval": bool((flat[key].get("execution") or {}).get("requires_approval")) and not (rs.get("approvals") or {}).get(tid),
                     "deferred": key in (st.get("deferred") or {}), "hpc_runs": e.get("hpc_runs") or [], "resume_at": e.get("resume_at_iso")})
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["phase"]] = counts.get(r["phase"], 0) + 1
    done = sum(1 for r in rows if r["phase"] == "verified")
    return {"plan": st["plan_key"], "plan_id": st["plan_id"], "revision": st["revision"], "state": rs.get("state", "not_run"), "tasks": rows,
            "counts": counts, "progress": f"{done}/{len(rows)}", "budgets": {**(rs.get("budgets") or {}), "max_total_attempts": c["max_total_attempts"],
                                                                             "max_wall_minutes": c["max_wall_minutes"]},
            "proposals": [x for x in list_proposals(p) if x.get("plan_key") == st["plan_key"] and x["status"] == "pending"],
            "auto_promotion": AP.summary(p, st, rs) if rs else AP.mode(p, st, {}),
            "concurrency": concurrency(p, st, rs), "health": plan_health(p, st, rs), "waiting_provider": waiting_provider(rs),
            "daemon": daemon_status(p), "enforced": {"completion": bool(c["enforce_completion"])}}


# ----------------------------------------------------------------------------------------------- scheduler tick
def _entry(rs: Dict[str, Any], tid: str) -> Dict[str, Any]:
    return rs["tasks"].setdefault(tid, {"phase": "waiting", "attempts": []})


def tick(p: Project, now: Optional[float] = None) -> Dict[str, Any]:
    """One deterministic scheduling pass over every running plan. Safe to call concurrently (state lock) and repeatedly."""
    out = {"plans": 0, "launched": [], "verified": [], "repairs": [], "blocked": [], "events": []}
    busy = False
    with _lock(p):
        for rs in all_runs(p):
            if rs.get("state") != "running":
                continue
            st = PS.load_state(p, rs["plan_key"])
            if not st:
                continue
            out["plans"] += 1
            t = time.time() if now is None else now
            if not (waiting_provider(rs) and not _active(rs)):      # time spent waiting for a provider limit to reset is not the plan's working time
                rs["budgets"]["wall_seconds"] = rs["budgets"].get("wall_seconds", 0.0) + max(0.0, min(60.0, t - rs.get("last_tick", t)))
            rs["last_tick"] = t
            c = cfg(p, st)
            _limits_step(p)
            _reconcile_attempts(p, st, rs, c, out)
            _poll_hpc(p, st, rs, c, out)
            AP.step(p, st, rs, c, out)
            from . import intervene as IV
            save_run(p, rs)          # interventions edit the run state through the public APIs; persist what this pass already did first
            IV.step(p, st, rs, out)
            rs = load_run(p, rs["plan_id"]) or rs     # an intervention may have resolved/voided through the same state file
            _schedule(p, st, rs, c, out)
            _apply_deferred(p, st, rs)
            wp = waiting_provider(rs)
            if any(x.get("phase") in ACTIVE_PHASES for x in rs["tasks"].values()):
                busy = True
            elif wp:
                out["next_wake"] = min(out.get("next_wake") or wp["until_ts"], wp["until_ts"])      # the daemon sleeps until then (no busy polling)
            if _plan_finished(p, st, rs) and AP.settled(p, st, rs):
                rs["state"] = "completed"
                rs["history"].append({"ts": now_iso(), "event": "completed"})
                p.event("plan", st["plan_id"], "supervisor_completed", f"plan {st['plan_key']} finished: every task verified")
            elif c["max_wall_minutes"] and rs["budgets"]["wall_seconds"] > c["max_wall_minutes"] * 60:
                rs["state"] = "paused"
                rs["history"].append({"ts": now_iso(), "event": "paused", "why": "wall-time budget exhausted"})
                out["events"].append(f"{st['plan_key']}: wall-time budget exhausted; paused")
            save_run(p, rs)
    if busy:
        out.pop("next_wake", None)
    return out


def _plan_finished(p: Project, st: Dict[str, Any], rs: Dict[str, Any]) -> bool:
    live = [k for k in st["tasks"] if k not in (st.get("retired") or {})]
    return bool(live) and all((rs["tasks"].get(st["tasks"][k]) or {}).get("phase") == "verified" for k in live)


def _flat(st: Dict[str, Any], tid: str) -> Dict[str, Any]:
    key = next((k for k, v in st["tasks"].items() if v == tid), None)
    return {**(st.get("spec_tasks") or {}).get(key or "", {}), "_key": key}


def _active(rs: Dict[str, Any]) -> List[str]:
    return [t for t, e in rs["tasks"].items() if e.get("phase") in ("running", "verifying", "waiting_hpc")]


def _schedule(p: Project, st: Dict[str, Any], rs: Dict[str, Any], c: Dict[str, Any], out: Dict[str, Any]) -> None:
    retired = st.get("retired") or {}
    for key in _topo(st):
        tid = st["tasks"].get(key)
        if not tid or key in retired or not p.exists("task", tid):
            continue
        e = _entry(rs, tid)
        task = p.get("task", tid)
        if task["status"] == "done":
            e["phase"] = "verified" if e.get("phase") != "verified" else "verified"
            continue
        if e["phase"] in ("running", "verifying", "waiting_hpc", "attention", "blocked", "awaiting_approval") and e["phase"] != "awaiting_approval":
            continue
        if e["phase"] == "waiting_provider" and not _resume_after_limit(p, tid, e, c):
            continue
        deps = task.get("depends_on") or []
        if any(p.get("task", d)["status"] != "done" for d in deps if p.exists("task", d)):
            e["phase"] = "waiting"
            continue
        spec = _flat(st, tid)
        if (spec.get("execution") or {}).get("requires_approval") and not rs["approvals"].get(tid):
            e["phase"] = "awaiting_approval"
            e["reason"] = "waiting for your approval to start (requires_approval)"
            continue
        wait = AP.gate(p, st, rs, tid)       # opt-in automatic promotion only; manual mode returns None
        if wait:
            e["phase"], e["reason"] = "waiting", wait
            continue
        if len(_active(rs)) >= int(c["max_parallel_agents"]):
            e["phase"] = "ready"
            continue
        if _lease_conflict(p, st, rs, tid):
            e["phase"] = "ready"
            e["reason"] = "waiting: another agent holds this workspace"
            continue
        kind = "repair" if e["phase"] == "repair_pending" else "initial"
        if rs["budgets"]["attempts"] >= int(c["max_total_attempts"]):
            _block(p, st, rs, tid, "plan attempt budget exhausted (max_total_attempts)", out)
            continue
        try:
            _launch(p, st, rs, tid, kind, c, out)
        except _Limited as ex:       # the provider this task routes to is waiting for a reset: nothing was launched, nothing was counted
            _wait_or_switch(p, st, rs, tid, e, None, ex.profile, ex.until, "provider limit still active", c, out)
        except PolicyBlocked as ex:
            _block(p, st, rs, tid, f"policy: {ex}", out)
        except ResearchError as ex:
            e["phase"] = "attention"
            e["reason"] = f"could not launch: {ex}"
            out["events"].append(f"{tid}: could not launch: {ex}")
            if "not available" in str(ex) or "not on PATH" in str(ex):
                _fallback(p, rs, tid, e, None, f"provider unavailable: {ex}", out)


def _lease_key(p: Project, st: Dict[str, Any], tid: str) -> str:
    spec = _flat(st, tid)
    task = p.get("task", tid)
    if (spec.get("execution") or {}).get("workspace") == "main" or not task.get("related_experiment_id"):
        return "main"
    return task["related_experiment_id"]


def _lease_conflict(p: Project, st: Dict[str, Any], rs: Dict[str, Any], tid: str) -> bool:
    mine = _lease_key(p, st, tid)
    for other in _active(rs):
        if other != tid and _lease_key(p, st, other) == mine:
            return True
    return False


def _block(p: Project, st: Dict[str, Any], rs: Dict[str, Any], tid: str, why: str, out: Dict[str, Any]) -> None:
    from . import plans as P
    e = _entry(rs, tid)
    e.update(phase="blocked", reason=why)
    try:
        P.block_task(p, tid, why)
    except ResearchError:
        pass
    out["blocked"].append({"task": tid, "reason": why})
    _event(p, tid, "supervisor_blocked", why)


# ----------------------------------------------------------------------------------------------- launching an attempt
def _providers():
    from . import providers
    return providers


def _launch(p: Project, st: Dict[str, Any], rs: Dict[str, Any], tid: str, kind: str, c: Dict[str, Any], out: Dict[str, Any]) -> None:
    from . import agents, execution as E, plans as P
    e = _entry(rs, tid)
    spec = _flat(st, tid)
    ag = spec.get("agent") or {}
    role = ag.get("role") or "implementer"
    n = len(e["attempts"]) + 1
    diagnostics = (e["attempts"][-1].get("verification") or {}).get("diagnostics") if e["attempts"] and kind == "repair" else None
    if kind == "repair":      # the latest attempt that actually stands (voided ones are evidence, not instructions)
        standing = [x for x in e["attempts"] if not x.get("voided")]
        diagnostics = ((standing[-1].get("verification") or {}).get("diagnostics")) if standing else None
    esc = escalation_for(c, e, kind)
    # reserve first: a crash after this point is reconciled from the attempt ledger, never double-launched
    task = p.get("task", tid)
    if task["status"] in ("todo", "blocked"):
        # The supervisor's own ledger is the exclusive reservation. The task's `claimed_by` stays free so the agent's mandatory
        # `research task start` succeeds (found by the first real run: a supervisor-held claim made every agent stop).
        P.update_task(p, tid, status="running", claimed_by=None, claimed_at=None, started_at=task.get("started_at") or now_iso())
    elif task["status"] == "running" and task.get("claimed_by"):
        P.update_task(p, tid, claimed_by=None, claimed_at=None)
    exp_id = task.get("related_experiment_id")
    attempt = {"n": n, "kind": kind, "execution": None, "started": now_iso(), "state": "reserved", "profile": e.get("provider_override") or (esc or {}).get("profile") or ag.get("profile"),
               "escalated": esc,
               "role": role,   # runs that already exist belong to earlier attempts; second-resolution timestamps cannot tell them apart
               "runs_before": [r["id"] for r in p.q("SELECT id FROM runs WHERE experiment_id=?", (exp_id,))] if exp_id else []}
    e["attempts"].append(attempt)
    e["phase"] = "running"
    e["reason"] = None
    rs["budgets"]["attempts"] += 1
    save_run(p, rs)
    workspace = None
    try:
        text, sources = taskprompt.assemble(p, tid, st, e, kind, diagnostics, None, c)
        kw: Dict[str, Any] = dict(role=role, target=tid, profile=e.get("provider_override") or (esc or {}).get("profile") or ag.get("profile"),
                                  model=(esc or {}).get("model") or (None if (esc or {}).get("profile") else ag.get("model")), instructions=text, headless=True,
                                  create_workspace=(spec.get("execution") or {}).get("workspace", "experiment") == "experiment")
        if ag.get("level") and not (esc or {}).get("model"):
            kw["level"] = ag["level"]
        if (esc or {}).get("effort"):
            kw["effort"] = esc["effort"]
        try:
            d = agents.dispatch(p, **kw)
        except NoFrozenTests as ex:       # the sanctioned way out: let the coordinator level write the tests first
            lvl = (ex.data or {}).get("coordinator_level")
            if not lvl:
                raise
            _event(p, tid, "supervisor_note", f"routed level requires frozen tests; using coordinator level {lvl} for this attempt")
            d = agents.dispatch(p, **{**kw, "level": lvl})
        if not d.get("available"):
            raise ResearchError(d.get("problem") or "provider not available")
        if c["usage_limit"]["on_limit"] != "halt" and limited_until(p, d.get("profile")):
            raise _Limited(d.get("profile"), limited_until(p, d.get("profile")))
        workspace = (d.get("allocation") or {}).get("workspace")
        text2 = text
        sid = None
        after_limit = e.pop("limit_relaunch", None)       # the previous attempt was refused by a provider limit: continue its session where possible
        if (kind in ("repair", "resume") or after_limit) and role != "verifier" and c["session_resume"] != "never" and not esc:   # a different model cannot resume the old session
            sid = _resumable_session(p, e, spec, role, d)
        if sid:
            attempt["resumed_session"] = sid
        _amend_execution(p, d["execution_id"], tid, n, sid, d)
        attempt.update(execution=d["execution_id"], state="prepared", cwd=d.get("cwd"), brief=d.get("brief"), model=d.get("model"),
                       profile=d.get("profile"), prompt_sha=taskprompt.fingerprint(text), context_sources=sources,
                       settings_fingerprint=((d.get("policy") or {}).get("settings_fingerprint")), workspace=workspace,
                       session_mode="native" if sid else ("handoff" if n > 1 else "new"), tier=(d.get("policy") or {}).get("level"))
        _persist_prompt(p, tid, n, text2, attempt)
        save_run(p, rs)
        # detached wrapper: it survives this process; the state lock and execution lock keep it single-owner
        subprocess.Popen(d["argv"], cwd=d.get("cwd") or p.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=os.name == "posix", env={**os.environ, **(d.get("env") or {})})
        attempt["state"] = "launched"
        attempt["launched"] = now_iso()
        out["launched"].append({"task": tid, "attempt": n, "execution": d["execution_id"], "kind": kind})
        if esc:
            _event(p, tid, "supervisor_escalated", f"attempt {n} escalated after an incomplete deliverable: " + ", ".join(f"{k}={v}" for k, v in esc.items()))
        _event(p, tid, "supervisor_launched", f"attempt {n} ({kind}) execution {d['execution_id']} via {d.get('profile')}/{d.get('model')}")
    except BaseException:
        attempt["state"] = "launch_failed"
        e["phase"] = "waiting"
        rs["budgets"]["attempts"] -= 1            # nothing ran: the attempt does not count against the budget
        e["attempts"].pop()
        if task["status"] != "done":
            try:
                P.update_task(p, tid, status="todo", claimed_by=None, claimed_at=None)
            except ResearchError:
                pass
        raise


def _persist_prompt(p: Project, tid: str, n: int, text: str, attempt: Dict[str, Any]) -> None:
    path = sdir(p, "prompts", f"{tid}-attempt{n}.md")
    _atomic_write(path, text.rstrip() + "\n")
    attempt["prompt_path"] = p.rel(path)


def _amend_execution(p: Project, execution_id: str, tid: str, n: int, sid: Optional[str], d: Dict[str, Any]) -> None:
    from . import execution as E
    path = E.path(p, execution_id)
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    rec["env"] = {**rec.get("env", {}), "RESEARCH_EXECUTION": execution_id, "RESEARCH_TASK": tid, "RESEARCH_ATTEMPT": str(n)}
    pv = _providers()
    if sid:
        kind = pv.provider_of({"command": rec["argv"][0]}, os.path.basename(rec["argv"][0]))
        rec["argv"] = pv.resume_argv(kind, rec["argv"], sid) or rec["argv"]
    rec["supervised"] = True
    _atomic_write(path, json.dumps(rec, sort_keys=True) + "\n")


def _resumable_session(p: Project, e: Dict[str, Any], spec: Dict[str, Any], role: str, d: Dict[str, Any]) -> Optional[str]:
    """Native continuation only when the *installed* provider proves it supports it, in the same role and session group."""
    pv = _providers()
    group = (spec.get("agent") or {}).get("session_group")
    prof = d.get("profile")
    for a in reversed(e["attempts"][:-1]):
        sid = a.get("session_id")
        if sid and a.get("role") == role and a.get("profile") == prof and (a.get("state") == "done" or a.get("limit")):
            cmd = (d.get("provider_argv") or d["argv"])[0]
            kind = pv.provider_of({"command": cmd}, os.path.basename(cmd))
            return sid if pv.native_resume_supported(kind, cmd) else None
    return None


# ----------------------------------------------------------------------------------------------- provider handoff
def _provider_profiles(p: Project) -> Dict[str, str]:
    """provider kind -> first installed profile name using it (the profile a fallback to that provider launches)."""
    pv = _providers()
    out: Dict[str, str] = {}
    profs = (p.config.get("agents") or {}).get("profiles") or {}
    for name, prof in profs.items():
        try:
            d = pv.detect(prof, name)
        except ResearchError:
            continue
        if d["installed"] and d["provider"] in pv.KNOWN and d["provider"] not in out and name == d["provider"]:
            out[d["provider"]] = name
    return out


def provider_status(p: Project, tid: Optional[str] = None) -> Dict[str, Any]:
    """Which providers can take a task over now, the current one, and the handoff history (for the panel and `supervisor continue`)."""
    pv = _providers()
    profs = (p.config.get("agents") or {}).get("profiles") or {}
    rows = []
    for kind in pv.KNOWN:
        name = kind
        prof = profs.get(name) or {"command": kind}
        d = pv.detect(prof, name)
        rows.append({"provider": kind, "profile": name, "installed": d["installed"], "headless": d["capabilities"]["headless"],
                     "resume": d["capabilities"]["resume"]})
    res = {"providers": rows, "handoff": {**((p.config.get("agents") or {}).get("handoff") or {})}}
    if tid:
        hit = PS.find_task_key(p, tid)
        rs = load_run(p, hit[0]["plan_id"]) if hit else None
        e = ((rs or {}).get("tasks") or {}).get(tid) or {}
        last = (e.get("attempts") or [{}])[-1]
        res.update(current=e.get("provider_override") or last.get("profile"), handoffs=e.get("handoffs", []), phase=e.get("phase"),
                   attempts=[{"n": a["n"], "profile": a.get("profile"), "state": a.get("state"), "session_mode": a.get("session_mode")} for a in e.get("attempts", [])])
    return res


def _switch(p: Project, e: Dict[str, Any], tid: str, to_profile: str, why: str, by: str) -> None:
    a = (e["attempts"] or [{}])[-1]
    e.setdefault("handoffs", []).append({"ts": now_iso(), "after_attempt": len(e["attempts"]), "from": a.get("profile"), "to": to_profile, "reason": why[:300], "by": by})
    e["provider_override"] = to_profile
    failed_verification = bool((a.get("verification") or {}).get("passed") is False and not a.get("voided"))
    e.update(phase="repair_pending" if failed_verification else "waiting", reason=None)
    _event(p, tid, "supervisor_handoff", f"{a.get('profile')} → {to_profile} ({by}): {why[:160]}")


def _fallback(p: Project, rs: Dict[str, Any], tid: str, e: Dict[str, Any], a: Optional[Dict[str, Any]], why: str, out: Dict[str, Any], ok=None) -> bool:
    """Opt-in automatic fallback to the next provider in agents.handoff.fallback_order. Bounded: each provider at most once per task and
    only while the supervisor's own attempt limits allow. Never used when the previous attempt's state is unknown."""
    h = (p.config.get("agents") or {}).get("handoff") or {}
    if not (h.get("enabled") and h.get("on_provider_unavailable") == "try_next"):
        return False
    if a and (a.get("state") == "unknown" or a.get("category") == "unknown_execution_state"):
        return False
    avail = _provider_profiles(p)
    tried = {x.get("profile") for x in e.get("attempts", [])} | {hh["to"] for hh in e.get("handoffs", [])}
    cur = e.get("provider_override") or (e["attempts"][-1].get("profile") if e.get("attempts") else None)
    for kind in h.get("fallback_order") or []:
        name = avail.get(kind)
        if name and name not in tried and name != cur and (ok is None or ok(name)):
            _switch(p, e, tid, name, why, "policy (fallback_order)")
            if rs["state"] in ("attention", "blocked"):
                rs["state"] = "running"
            return True
    return False


def continue_with(p: Project, tid: str, profile: str, note: Optional[str] = None) -> Dict[str, Any]:
    """Human action: continue a stopped or failed task with another agent, in the same worktree, as the next attempt of the same task.
    The new provider gets a portable context document (never the old provider's session id). Refused while the previous process may still run."""
    from . import execution as E
    require_human(p, "Continuing a task with another agent")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    prof = ((p.config.get("agents") or {}).get("profiles") or {}).get(profile)
    if not prof:
        raise ResearchError(f"No agent profile {profile!r}", hint="`research agent providers list` shows the profiles.")
    d = _providers().detect(prof, profile)
    if not d["installed"]:
        raise ResearchError(f"`{d['command']}` is not installed on this machine")
    with _lock(p):
        rs = load_run(p, hit[0]["plan_id"])
        e = _entry(rs, tid)
        if e.get("phase") in ("running", "verifying", "waiting_hpc", "verified", "retired"):
            raise ResearchError(f"{tid} is {e['phase']}; only a stopped, failed or waiting task can change agent")
        a = e["attempts"][-1] if e["attempts"] else None
        if a and a.get("execution"):
            ex = E.show(p, a["execution"])
            if ex["state"] in ("prepared", "launched", "running") or ex.get("alive") or a.get("state") == "unknown" or ex["state"] == "unknown":
                raise ResearchError(f"The previous agent process for {tid} may still be running (state {ex['state']})",
                                    hint="Stop it, or settle it with `research supervisor resolve` first; nothing was changed.")
        _switch(p, e, tid, profile, note or e.get("reason") or "continued by the researcher", "human")
        if rs["state"] in ("attention", "blocked"):
            rs["state"] = "running"
        save_run(p, rs)
    return {"task": tid, "profile": profile, "handoffs": e["handoffs"], "plan_state": rs["state"],
            "note": "the plan is paused: resume it to start the next attempt" if rs["state"] == "paused" else None}


# ----------------------------------------------------------------------------------------------- reconciliation of attempts
def _reconcile_attempts(p: Project, st: Dict[str, Any], rs: Dict[str, Any], c: Dict[str, Any], out: Dict[str, Any]) -> None:
    from . import execution as E
    for tid, e in rs["tasks"].items():
        if e.get("phase") not in ("running", "verifying"):
            continue
        if not e["attempts"]:
            e["phase"] = "waiting"
            continue
        a = e["attempts"][-1]
        if not a.get("execution"):
            # reserved but never prepared (supervisor died between reservation and dispatch): nothing ran, safe to redo
            e["attempts"].pop()
            rs["budgets"]["attempts"] = max(0, rs["budgets"]["attempts"] - 1)
            e["phase"] = "repair_pending" if e["attempts"] else "waiting"
            continue
        try:
            ex = E.show(p, a["execution"])
        except (OSError, ResearchError):
            e.update(phase="attention", reason="execution record missing; resolve with `research supervisor resolve`")
            continue
        s = ex["state"]
        if s == "prepared":
            if a.get("state") == "launched" and _age(a.get("launched")) > c["launch_grace_seconds"]:
                try:   # launch is idempotent (single-use record + lock); only re-fire when nothing has started
                    subprocess.Popen([sys.executable, E.__file__, p.root, a["execution"]], cwd=a.get("cwd") or p.root, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name == "posix")
                    a["launched"] = now_iso()
                    out["events"].append(f"{tid}: re-fired an unstarted launch")
                except OSError as exn:
                    e.update(phase="attention", reason=f"launch failed: {exn}")
            continue
        if s in ("launched", "running"):
            tmo = float((_flat(st, tid).get("execution") or {}).get("timeout_minutes") or c["attempt_timeout_minutes"]) * 60
            if tmo and _age(a.get("started")) > tmo and not a.get("timeout_signalled"):
                a["timeout_signalled"] = now_iso()
                try:
                    E.stop(p, a["execution"])
                except ResearchError:
                    pass
                _event(p, tid, "supervisor_timeout", f"attempt {a['n']} exceeded {tmo / 60:.0f} minutes; asked to stop")
            continue
        if s == "unknown":
            if ex.get("alive"):
                continue
            a["state"] = "unknown"
            e.update(phase="attention", reason="the launcher exited without recording an outcome; the agent may still be running. "
                                               "Check it, then settle it with Retry in the Plans page or the supervisor resolve command")
            a["category"] = "unknown_execution_state"
            _event(p, tid, "supervisor_unknown", f"attempt {a['n']} state unknown; not retried automatically")
            out["blocked"].append({"task": tid, "reason": "unknown execution state"})
            continue
        _finish_attempt(p, st, rs, tid, e, a, ex, c, out)


def _age(ts: Optional[str]) -> float:
    if not ts:
        return 0.0
    from .util import parse_iso
    d = parse_iso(ts)
    import datetime as _dt
    return (_dt.datetime.now().astimezone() - d).total_seconds() if d else 0.0


def _finish_attempt(p, st, rs, tid, e, a, ex, c, out) -> None:
    from . import execution as E
    a["ended"] = now_iso()
    a["outcome"] = ex["state"]
    a["exit_code"] = ex.get("exit_code")
    a["usage"] = ex.get("reported")
    sid = _session_id(p, a, ex)
    if sid:
        a["session_id"] = sid
    wait_mode = c["usage_limit"]["on_limit"] != "halt"
    # a limit can show on any finished attempt (exit 0 too, stdout or stderr); `halt` keeps today's scope: failed runs only. A human cancel is never second-guessed,
    # but an attempt the supervisor itself stopped for its timeout may have been sleeping inside a provider's retry loop.
    info = _limit_of(p, a, ex) if (wait_mode or ex["state"] == "failed") and (ex["state"] != "cancelled" or a.get("timeout_signalled")) else None
    if info is None and wait_mode and ex["state"] in ("completed", "failed"):
        _limit_clear_streak(p, a.get("profile"))
    if ex["state"] == "cancelled" and not info:
        a["state"] = "cancelled"
        e.update(phase="attention", reason="the agent was cancelled; resume the plan to retry or `supervisor resolve`")
        return
    if info:       # not the agent's fault and not repairable by trying again now: void the attempt, keep it as evidence, count nothing
        pe = info.raw[:200] or info.kind
        a.update(state="failed", category="resource_limit", voided="provider error: " + pe, limit={"kind": info.kind, "reset_at": info.reset_at.isoformat() if info.reset_at else None})
        rs["budgets"]["attempts"] = max(0, rs["budgets"]["attempts"] - 1)
        _event(p, tid, "supervisor_provider_error", pe)
        if wait_mode:
            _on_limit(p, st, rs, tid, e, a, info, c, out)
            return
        e.update(phase="attention", reason=f"the provider refused the request ({pe}). Nothing was retried; resume the plan or press Retry when it is available again")
        if _fallback(p, rs, tid, e, a, "provider error: " + pe, out):
            return
        out["blocked"].append({"task": tid, "reason": e["reason"]})
        return
    if ex["state"] == "failed":
        a["state"] = "failed"
        logs = E.logs(p, a["execution"], 1500)
        tail = taskprompt.redact((logs.get("err") or logs.get("out") or "")[-900:]) or ""
        code = ex.get("exit_code")
        cat = "environment" if code in (127, 126) or re.search(r"ModuleNotFoundError|command not found|No such file or directory: '", tail) else "agent_process_failure"
        a["category"] = cat
        diag = [f"the agent process exited with code {code}" + (f": {tail.strip().splitlines()[-1]}" if tail.strip() else "")]
        a["verification"] = {"passed": False, "diagnostics": diag, "ts": now_iso(), "skipped": "agent process failed"}
        _after_failure(p, st, rs, tid, e, a, diag, cat, c, out)
        return
    a["state"] = "done"
    e["phase"] = "verifying"
    _verify_attempt(p, st, rs, tid, e, a, c, out)


_PROVIDER_ERR = re.compile(r"hit your .*limit|rate.?limit|usage limit|overloaded|\b429\b|\b529\b|credit balance|quota", re.I)


def _provider_error(p: Project, a: Dict[str, Any]) -> Optional[str]:
    """A provider-side refusal (usage limit, overload, quota), read from the agent's own structured output; never from the task's work."""
    from . import execution as E
    try:
        text = (E.logs(p, a["execution"], 200000).get("out")) or ""
        for line in reversed(text.strip().splitlines()):
            d = json.loads(line)
            if isinstance(d, dict) and (d.get("is_error") or d.get("terminal_reason") == "api_error"):
                msg = str(d.get("result") or "")
                if d.get("terminal_reason") == "api_error" or _PROVIDER_ERR.search(msg):
                    return msg[:200] or "api error"
            break
    except (OSError, ValueError, ResearchError):
        pass
    return None


# ----------------------------------------------------------------------------------------------- provider usage limits (H.10)
# `limits.json` (beside the run state, so it survives daemon restarts): {key: {until, provider, kind, raw, streak, alerted}} where `key` is the account
# a limit applies to (profile `account`, else the provider kind: every profile of one provider shares one login) and `until` is the epoch time it may be
# used again (reset + buffer). A limit only ever pauses the tasks that route to that account; the others keep running.
class _Limited(Exception):
    def __init__(self, profile: Optional[str], until: float):
        super().__init__(f"{profile} is limited until {until}")
        self.profile, self.until = profile, until


def _now_ts() -> float:
    from .providers import limits as L
    return L.now().timestamp()           # the one clock (tests set a fake one)


def _hhmm(until: Any) -> str:
    import datetime as _dt
    d = _dt.datetime.fromtimestamp(float(until)).astimezone()
    return d.strftime("%H:%M") if abs(float(until) - _now_ts()) < 20 * 3600 else d.strftime("%a %H:%M")


def _in_text(secs: float) -> str:
    m = max(1, int(round(secs / 60)))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def _lim_path(p: Project) -> str:
    return sdir(p, "limits.json")


def _lim_load(p: Project) -> Dict[str, Any]:
    try:
        with open(_lim_path(p), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _lim_save(p: Project, d: Dict[str, Any]) -> None:
    _atomic_write(_lim_path(p), json.dumps(d, indent=1, sort_keys=True) + "\n")


def _limit_key(p: Project, profile: Optional[str]) -> str:
    prof = ((p.config.get("agents") or {}).get("profiles") or {}).get(profile or "") or {}
    try:
        return str(prof.get("account") or _providers().provider_of(prof, profile or ""))
    except ResearchError:
        return str(profile)


def limited_until(p: Project, profile: Optional[str]) -> Optional[float]:
    """Epoch time the account behind `profile` may be used again, None when it is not limited now."""
    u = (_lim_load(p).get(_limit_key(p, profile)) or {}).get("until")
    return float(u) if u and float(u) > _now_ts() else None


def limits_status(p: Project) -> List[Dict[str, Any]]:
    now = _now_ts()
    return [{"account": k, "provider": v.get("provider"), "kind": v.get("kind"), "until": v["until"], "limited": v["until"] > now}
            for k, v in sorted(_lim_load(p).items()) if v.get("until")]


def _limit_clear_streak(p: Project, profile: Optional[str]) -> None:
    d, k = _lim_load(p), _limit_key(p, profile)
    if (d.get(k) or {}).get("streak"):
        d[k]["streak"] = 0
        _lim_save(p, d)


def waiting_provider(rs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the plan status shows as a chip: the earliest provider wait, `{provider, until (ISO), until_ts, tasks}`; None when nothing waits."""
    w = [e for e in (rs.get("tasks") or {}).values() if e.get("phase") == "waiting_provider" and e.get("resume_at")]
    if not w:
        return None
    first = min(w, key=lambda e: e["resume_at"])
    return {"provider": (first.get("waiting_provider") or {}).get("provider") or "provider", "until": first["resume_at_iso"], "until_ts": first["resume_at"], "tasks": len(w)}


def _claude_projects_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def _claude_transcript_error(a: Dict[str, Any]) -> str:
    """Claude's weekly limit can exit 1 with nothing on stdout; the message then exists only in the session transcript
    (~/.claude/projects/<cwd with / as ->/<session>.jsonl, an assistant entry with isApiErrorMessage). Newest transcript since the attempt began."""
    import glob
    d = os.path.join(_claude_projects_dir(), re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(a.get("cwd") or "")))
    try:
        newest = max(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime)
        with open(newest, encoding="utf-8", errors="replace") as f:
            for line in reversed(f.read().splitlines()[-40:]):
                m = json.loads(line)
                if m.get("isApiErrorMessage"):
                    c = (m.get("message") or {}).get("content")
                    return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in c or [] if isinstance(x, dict))
    except (ValueError, OSError):
        pass
    return ""


def _limit_of(p: Project, a: Dict[str, Any], ex: Dict[str, Any]):
    """Is this finished attempt a provider refusal (usage/rate/overload/credit)? Reads the attempt's provider through its adapter (`providers.limit_info`) over
    stdout AND stderr whatever the exit code; falls back to the structured-output check this module always had (`_provider_error`, any provider)."""
    from . import execution as E
    from .providers import limits as L
    pv, name = _providers(), a.get("profile") or ""
    logs = E.logs(p, a["execution"], 200000)
    out, err, code = logs.get("out") or "", logs.get("err") or "", ex.get("exit_code")
    try:
        kind = pv.provider_of(((p.config.get("agents") or {}).get("profiles") or {}).get(name) or {}, name)
    except ResearchError:
        kind = "custom"
    if kind == "claude" and code not in (0, None) and not out.strip() and not err.strip():
        out = _claude_transcript_error(a)
    info = pv.limit_info(kind, out, err, code)
    if info is None and ex["state"] == "failed":
        pe = _provider_error(p, a)
        if pe:
            info = L.info(pe) or pv.LimitInfo("usage", None, pe)
    return info


def _plan_wait(p: Project, key: str, provider: str, info, uc: Dict[str, Any]) -> float:
    """Record the limit on its account and return the time (epoch) the attempt may be relaunched."""
    d, now = _lim_load(p), _now_ts()
    rec = d.get(key) or {}
    ts = info.reset_at.timestamp() if info.reset_at else None
    if info.kind == "credit" and not ts:     # waiting does not add credit: always ask the human (wait / switch / stop)
        until = now + 3600.0 * float(uc["max_wait_hours"]) + 1
    elif ts and ts > now:
        until = ts + float(uc["wait_buffer_seconds"])
    else:                          # no usable reset time: the relaunch is the probe, at growing intervals (short ones for rate/overload)
        steps = [float(x) for x in uc["short_backoff_minutes" if info.kind in ("rate", "overload") else "unknown_reset_backoff_minutes"]] or [15.0]
        until = now + 60 * steps[min(int(rec.get("streak") or 0), len(steps) - 1)]
        rec["streak"] = int(rec.get("streak") or 0) + 1
    prev = float(rec.get("until") or 0)
    if prev <= now:
        rec.pop("alerted", None)      # a new episode
        prev = 0.0
    rec.update(until=max(until, prev), provider=provider, kind=info.kind, raw=info.raw[:200])
    d[key] = rec
    _lim_save(p, d)
    return rec["until"]


def _on_limit(p, st, rs, tid, e, a, info, c, out) -> None:
    """The attempt was refused by a limit (`wait` / `fallback_then_wait`). The attempt is already void and refunded."""
    uc = c["usage_limit"]
    profile = a.get("profile")
    key, provider = _limit_key(p, profile), _provider_kind(p, profile)
    until = _plan_wait(p, key, provider, info, uc)
    e["limit_pending"] = {"profile": profile, "provider": provider, "until": until}
    _wait_or_switch(p, st, rs, tid, e, a, profile, until, info.raw[:160], c, out)


def _provider_kind(p: Project, profile: Optional[str]) -> str:
    try:
        return _providers().provider_of(((p.config.get("agents") or {}).get("profiles") or {}).get(profile or "") or {}, profile or "")
    except ResearchError:
        return str(profile)


def _wait_or_switch(p, st, rs, tid, e, a, profile, until, why, c, out) -> None:
    """fallback_then_wait: another provider first, if every rule allows it. Else wait for the reset; a reset beyond max_wait_hours becomes a decision request."""
    uc = c["usage_limit"]
    provider = _provider_kind(p, profile)
    if uc["on_limit"] == "fallback_then_wait":
        from . import orchestrate as O, strategy as S
        avail = O.as_list(O.cfg(p).get("available"))
        level = (_flat(st, tid).get("agent") or {}).get("level")
        profs = (p.config.get("agents") or {}).get("profiles") or {}

        def ok(name: str) -> bool:      # never weaken the strategy, never a model orchestration.available does not list, never another limited account
            m = (profs.get(name) or {}).get("model")
            return not limited_until(p, name) and not (S.active_name(p) and level) and not (avail and m and m not in avail)
        if _fallback(p, rs, tid, e, a, f"{provider} limit: {why}", out, ok=ok):
            return
    if (until - _now_ts()) / 3600.0 > float(uc["max_wait_hours"]) and not e.get("limit_wait_ok"):
        _ask_about_limit(p, st, rs, tid, e, profile, provider, until, c, out)
        return
    _enter_wait(p, tid, e, provider, profile, until)
    _alert_limit(p, _limit_key(p, profile), provider, until)


def _enter_wait(p: Project, tid: str, e: Dict[str, Any], provider: str, profile: Optional[str], until: float) -> None:
    import datetime as _dt
    iso = _dt.datetime.fromtimestamp(until, _dt.timezone.utc).isoformat(timespec="seconds")
    last = (e.get("attempts") or [{}])[-1]
    e.update(phase="waiting_provider", resume_at=until, resume_at_iso=iso, waiting_provider={"provider": provider, "profile": profile, "until": iso},
             reason=f"waiting for the {provider} limit to reset; resuming about {_hhmm(until)} (in {_in_text(until - _now_ts())})",
             limit_relaunch=bool(last.get("limit")) or None)
    e["limit_kind"] = "repair" if (last.get("kind") == "repair") else "initial"
    e.pop("limit_pending", None)
    _event(p, tid, "supervisor_waiting_provider", f"{provider} limit: {e['reason']}")


def _ask_about_limit(p, st, rs, tid, e, profile, provider, until, c, out) -> None:
    from . import decisions as D
    hours = (until - _now_ts()) / 3600.0
    opts = [{"key": "wait", "label": f"Wait for {provider} (until about {_hhmm(until)})", "consequence": f"The task resumes by itself after the reset, about {hours:.0f} hours from now; other tasks continue.",
             "action": {"kind": "op", "op": "supervisor_task", "params": {"task": tid, "action": "wait"}}}]
    alt = next((n for k, n in _provider_profiles(p).items() if not limited_until(p, n) and n != profile and k != provider), None)
    if alt:
        opts.append({"key": "switch", "label": f"Switch this task to {alt}", "consequence": "The next attempt uses another provider (portable context, not the old session).",
                     "action": {"kind": "op", "op": "supervisor_task", "params": {"task": tid, "action": "continue", "reason": alt}}})
    opts.append({"key": "stop", "label": "Stop: pause the plan", "consequence": "Nothing runs until you resume the plan.",
                 "action": {"kind": "op", "op": "plan_control", "params": {"plan": st["plan_key"], "action": "pause"}}})
    credit = (_lim_load(p).get(_limit_key(p, profile)) or {}).get("kind") == "credit"
    q = (f"The {provider} account is out of credit; waiting does not add any. What now for {tid}?" if credit else
         f"The {provider} limit resets in about {hours:.0f} hours (more than the {c['usage_limit']['max_wait_hours']:g} hours the supervisor waits unattended). What now for {tid}?")
    d = D.request(p, q,
                  opts, subject=tid, created_by="supervisor", fingerprint=f"limit:{_limit_key(p, profile)}:{int(until // 3600)}")
    e.update(phase="attention", reason=(f"the {provider} account is out of credit" if credit else f"the {provider} limit resets about {_hhmm(until)}, longer than max_wait_hours") + f": answer decision {d['id']} (wait / switch / stop)")
    e["limit_pending"] = {"profile": profile, "provider": provider, "until": until}
    out["blocked"].append({"task": tid, "reason": e["reason"]})


def wait_for_limit(p: Project, tid: str) -> Dict[str, Any]:
    """Human choice 'wait' on a limit decision request: wait for the reset however far away it is."""
    require_human(p, "Waiting out a provider limit")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    with _lock(p):
        rs = load_run(p, hit[0]["plan_id"])
        e = _entry(rs, tid)
        lp = e.get("limit_pending")
        if not lp:
            raise ResearchError(f"{tid} is not waiting on a provider limit decision")
        until = limited_until(p, lp["profile"]) or lp["until"]
        e["limit_wait_ok"] = True
        _enter_wait(p, tid, e, lp["provider"], lp["profile"], until)
        if rs["state"] in ("attention", "blocked"):
            rs["state"] = "running"
        save_run(p, rs)
    return {"task": tid, "phase": e["phase"], "resume_at": e["resume_at_iso"]}


def _resume_after_limit(p: Project, tid: str, e: Dict[str, Any], c: Dict[str, Any]) -> bool:
    """True when a waiting task may launch again now. A limit another task extended (or a wait that is not over) keeps it waiting."""
    now = _now_ts()
    prof = (e.get("waiting_provider") or {}).get("profile")
    until = max(float(e.get("resume_at") or 0), limited_until(p, prof) or 0)
    if until > now:
        if until > float(e.get("resume_at") or 0):
            _enter_wait(p, tid, e, (e.get("waiting_provider") or {}).get("provider") or "provider", prof, until)
        return False
    e.update(phase="repair_pending" if e.get("limit_kind") == "repair" else "waiting", reason=None)
    for k in ("resume_at", "resume_at_iso", "waiting_provider"):
        e.pop(k, None)
    _event(p, tid, "supervisor_resumed_after_limit", f"the provider limit has reset; relaunching {tid}")
    return True


def _limits_step(p: Project) -> None:
    """Once per limit episode, when its reset has passed: the single 'resuming' alert (clears the one raised at the start)."""
    d, now, dirty = _lim_load(p), _now_ts(), False
    for k, v in d.items():
        if v.get("alerted") and float(v.get("until") or 0) <= now:
            try:
                from .chat import alerts
                alerts.clear(p, f"provider_limit:{k}", f"{(v.get('provider') or k).capitalize()} limit reset; resuming the waiting tasks.")
            except Exception:       # noqa: BLE001 - an alert must never stop scheduling
                pass
            v.pop("alerted", None)
            dirty = True
    if dirty:
        _lim_save(p, d)


def _alert_limit(p: Project, key: str, provider: str, until: float) -> None:
    """One chat message per limit episode (the alert outbox dedupes and cools down on top of this)."""
    d = _lim_load(p)
    if (d.get(key) or {}).get("alerted"):
        return
    try:
        from . import notify
        if not notify.chat_enabled(p):
            return
        from .chat import alerts
        alerts.raise_(p, f"provider_limit:{key}", "provider_limit", "warn",
                      f"{provider.capitalize()} limit reached; resuming about {_hhmm(until)} (in {_in_text(until - _now_ts())}); other tasks continue")
        d.setdefault(key, {})["alerted"] = True
        _lim_save(p, d)
    except Exception:       # noqa: BLE001
        pass


def _session_id(p: Project, a: Dict[str, Any], ex: Dict[str, Any]) -> Optional[str]:
    from . import execution as E
    try:
        text = (E.logs(p, a["execution"], 200000).get("out")) or ""
        pv = _providers()
        cmd = (ex.get("argv") or [""])[0]
        return pv.session_id_of(pv.provider_of({"command": cmd}, os.path.basename(cmd)), text)
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------- verification
def _task_runs(p: Project, st: Dict[str, Any], tid: str, a: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Computational runs this attempt produced: reported run ids plus new HPC runs of the task's experiment (not present at launch)."""
    ex = p.get("task", tid).get("related_experiment_id")
    ids = set(a.get("reported_runs") or [])
    before = set(a.get("runs_before") or [])
    rows = []
    for r in p.q("SELECT id, experiment_id, status, backend, slurm_job_id, created_at FROM runs ORDER BY id"):
        if r["id"] in ids or (ex and r["experiment_id"] == ex and r["id"] not in before and r["backend"] in ("slurm", "hpc-bundle")):
            rows.append(r)
    return rows


def _verify_attempt(p, st, rs, tid, e, a, c, out) -> None:
    from . import orchestrate as O, plans as P
    from .schema import RUN_TERMINAL
    runs = _task_runs(p, st, tid, a)
    spec = _flat(st, tid)
    hpc = spec.get("resources", {}).get("backend") == "slurm" or any(r["backend"] in ("slurm", "hpc-bundle") for r in runs)
    e["hpc_runs"] = sorted({*e.get("hpc_runs", []), *[r["id"] for r in runs]})
    pending = [r for r in runs if r["status"] not in RUN_TERMINAL]
    if pending:
        e["phase"] = "waiting_hpc"
        e["reason"] = "waiting for computational run(s) " + ", ".join(r["id"] for r in pending)
        a["hpc_wait_since"] = a.get("hpc_wait_since") or now_iso()
        return
    diagnostics: List[str] = []
    extra: List[str] = []
    if hpc and not runs:
        diagnostics.append("the task requires computation (resources.backend=slurm) but no computational run was recorded for it; the agent "
                           "must submit through Research OS (`research hpc submit …`) so results are tracked")
    failed = [r for r in runs if r["status"] in ("failed", "cancelled")]
    if failed:
        diagnostics.append("computational run(s) did not complete: " + ", ".join(f"{r['id']} [{r['status']}]" for r in failed)
                           + "; inspect `research run logs <RUN> --stderr`")
    if failed and len(e["hpc_runs"]) > int(c["max_hpc_resubmissions"]) + int((spec.get("resources") or {}).get("planned_runs", 0) or 0) + len(runs) - len(failed):
        extra.append("computational resubmission budget exhausted")
    try:
        v = O.verify_work(p, tid)
    except ResearchError as ex:
        v = {"passed": False, "diagnostics": [str(ex)], "verification_state": "unverified"}
    vd = list(v.get("diagnostics", []))
    if runs and not failed:    # completed computational runs are themselves a deterministic verifier (found by the scripted-HPC test)
        vd = [d for d in vd if not d.startswith("no deterministic verifier ran")]
        if not vd and not v.get("passed"):
            v = {**v, "passed": True, "verification_state": "passed"}
    diagnostics += vd
    if a.get("verification"):       # a re-verification (after a contract amendment) never overwrites the earlier result: it is kept as evidence
        a.setdefault("verification_history", []).append(a["verification"])
    a["verification"] = {"passed": not diagnostics and v.get("passed", False), "diagnostics": diagnostics, "ts": now_iso(),
                         "checks": f"{v.get('checks_passed', 0)}/{v.get('checks_executed', 0)}", "state": v.get("verification_state")}
    if a["verification"]["passed"]:
        _verified(p, st, rs, tid, e, a, out)
        return
    cat = _categorize(p, tid, a, diagnostics)
    if cat == "incomplete_deliverable":
        cat, why = _refine_missing(p, st, tid, spec, diagnostics)
        if why:
            a["blocker"] = why
    a["category"] = cat
    _after_failure(p, st, rs, tid, e, a, diagnostics, cat, c, out)


# a required output is absent or empty (file check: "missing" / "exists but is empty") or a required metric was never recorded
_INCOMPLETE = re.compile(r"check file: .+? failed \((?:missing|exists but is empty)\)|check metric:.+? failed \(.* not found")


def _refine_missing(p: Project, st: Dict[str, Any], tid: str, spec: Dict[str, Any], diagnostics: List[str]) -> tuple:
    """A missing file is one of three different problems, and only the third is fixed by running the same task again:
    * role_contradiction   a verifier must itself create the file; verifiers check work and are told not to author it, so repeating cannot pass
    * missing_prerequisite another task owns the file and it is not in this task's worktree/outputs (not produced, or not integrated here)
    * incomplete_deliverable  this task owns the file and did not write it: a repair (or an escalation) can fix that"""
    missing = sorted({m.group(1) for d in diagnostics for m in re.finditer(r"check file: (.+?) failed \((?:missing|exists but is empty)\)", d)})
    if not missing:
        return "incomplete_deliverable", None
    flat = st.get("spec_tasks") or {}
    me = spec.get("_key")
    role = (spec.get("agent") or {}).get("role")
    owners = {}
    for k, t in flat.items():
        if k != me:
            for path in PS.deliverables(t):
                owners.setdefault(path, []).append(k)
    upstream = PS.authored_by_other(flat, me) if me in flat else set()
    foreign = [(m, owners[m]) for m in missing if m in owners and m in upstream]
    if foreign:
        lines = []
        for path, ks in foreign:
            who = ", ".join(f"{k} ({st['tasks'].get(k, k)}, {'done' if st['tasks'].get(k) and p.exists('task', st['tasks'][k]) and p.get('task', st['tasks'][k])['status'] == 'done' else 'not done'})" for k in ks)
            lines.append(f"{path} is produced by {who}")
        return "missing_prerequisite", ("an upstream deliverable is missing: " + "; ".join(lines) + ". Check that the producing task actually wrote it at that path, and that its work "
                                        "is in this task's worktree (promote/integrate the upstream experiment first). Not retried: this task cannot create it. "
                                        "Fix the upstream task, then `research supervisor resolve TASK --unblock`.")
    if role == "verifier":
        return "role_contradiction", (f"verifier task {me} must create {', '.join(missing)} itself, but verifiers check work and do not author it, so repeating cannot pass. "
                                     "Change the task's role to analyst/implementer (or move those file checks to the task that writes them) in the plan YAML; "
                                     "nothing was rewritten and the review requirement is unchanged. Then `research supervisor resolve TASK --unblock`.")
    return "incomplete_deliverable", None


def escalation_for(c: Dict[str, Any], e: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    """Opt-in: after an attempt failed because deliverables were missing, the repair attempt may run on a more capable model / higher effort /
    another profile. Bounded (`max_escalated_attempts`, default 1) and inside the normal attempt budgets. None = run as planned."""
    esc = c.get("escalation") or {}
    if kind != "repair" or not any(esc.get(k) for k in ("model", "effort", "profile")):
        return None
    standing = [x for x in e.get("attempts", []) if not x.get("voided")]
    if not standing or standing[-1].get("category") not in (esc.get("on") or ["incomplete_deliverable"]):
        return None
    if len([x for x in standing if x.get("escalated")]) >= int(esc.get("max_escalated_attempts", 1)):
        return None
    return {k: esc[k] for k in ("model", "effort", "profile") if esc.get(k)}


def _categorize(p: Project, tid: str, a: Dict[str, Any], diagnostics: List[str]) -> str:
    reported = [r for r in _reports(p, tid, a.get("execution")) if r.get("kind") == "problem"]
    if reported and reported[-1].get("category") in CATEGORIES:
        return reported[-1]["category"]
    text = " ".join(diagnostics)
    if re.search(r"ModuleNotFoundError|command not found|Permission denied|No space left", text):
        return "environment"
    if re.search(r"OOM|out of memory|TIMEOUT|time limit|MemoryError", text, re.I):
        return "resource_limit"
    if "frozen acceptance tests were modified" in text:
        return "insufficient_test_oracle"
    if _INCOMPLETE.search(text):
        return "incomplete_deliverable"
    return "incorrect_implementation"


def _verified(p, st, rs, tid, e, a, out) -> None:
    from . import plans as P
    summary = next((r.get("summary") for r in reversed(_reports(p, tid)) if r.get("kind") == "report" and r.get("summary")), None)
    with authority(tid, "supervisor verification passed"):
        P.update_task(p, tid, status="verify")
        P.complete_task(p, tid, result=taskprompt.redact(summary) or f"verified by Research OS after attempt {a['n']}", completed_by="supervisor")
    e.update(phase="verified", reason=None)
    a["state"] = "verified"
    try:
        sha = _commit_verified(p, tid, a, spec_title=p.get("task", tid)["title"], plan_key=st["plan_key"], c=cfg(p, st))
        if sha:
            a["commit"] = sha
    except ResearchError as ex:
        e["commit_note"] = f"verified, but the work could not be committed: {ex}"
    out["verified"].append({"task": tid, "attempt": a["n"]})
    _event(p, tid, "supervisor_verified", f"attempt {a['n']} verified: {a['verification'].get('checks')} checks")


def _commit_verified(p: Project, tid: str, a: Dict[str, Any], spec_title: str, plan_key: str, c: Dict[str, Any]) -> Optional[str]:
    """Commit the verified work on the experiment's own branch (never on main, never pushed), so it is promotable and traceable.
    Only experiment worktrees are touched; `supervisor.commit_exclude` keeps bulky results out of code history."""
    if not c.get("commit_verified_work"):
        return None
    from . import workspace as W
    ws = W.for_task(p, tid)
    if not ws:
        return None
    path = ws["path"]
    excl = [(f":(exclude,glob)**/{x.rstrip('/')}/**" if x.endswith("/") else f":(exclude,glob)**/{x}") for x in (c.get("commit_exclude") or [])]
    env = {**os.environ, "GIT_AUTHOR_NAME": "Research OS supervisor", "GIT_AUTHOR_EMAIL": "supervisor@research-os.local",
           "GIT_COMMITTER_NAME": "Research OS supervisor", "GIT_COMMITTER_EMAIL": "supervisor@research-os.local"}
    def git(*args):
        r = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env)
        if r.returncode:
            raise ResearchError("git " + " ".join(args[:2]) + ": " + (r.stderr or r.stdout).strip()[:200])
        return r.stdout.strip()
    git("add", "-A", "--", ".", *excl)
    if not git("diff", "--cached", "--name-only"):
        return None
    msg = (f"{tid} {spec_title}\n\nVerified by Research OS (plan {plan_key}, attempt {a['n']}).\n\n"
           f"Research-Task: {tid}\nResearch-Plan: {plan_key}\nResearch-Execution: {a.get('execution')}\n")
    git("commit", "-q", "-m", msg)
    return git("rev-parse", "HEAD")


def promote_plan(p: Project, plan_ref: str, experiment: Optional[str] = None, title: Optional[str] = None) -> Dict[str, Any]:
    """Prepare promotion candidates from the commits the supervisor made for verified tasks. Nothing merges: the candidate then goes
    through the existing tests / evidence / review / approval / integrate gates."""
    from . import promotion as PR, workspace as W
    require_human(p, "Preparing a promotion")
    st = _plan_state(p, plan_ref)
    rs = load_run(p, st["plan_id"]) or {}
    by_ws: Dict[str, List[str]] = {}
    for tid, e in (rs.get("tasks") or {}).items():
        exp = p.get("task", tid).get("related_experiment_id")
        if experiment and exp != experiment and (st["experiments"].get(experiment) != exp):
            continue
        for a in e.get("attempts", []):
            if a.get("commit") and a.get("state") == "verified":
                ws = W.for_task(p, tid)
                if ws:
                    by_ws.setdefault(ws["id"], []).append(a["commit"])
    if not by_ws:
        raise ResearchError("No verified, committed work to promote yet",
                            hint="Tasks are committed on their experiment branch when they verify (supervisor.commit_verified_work).")
    human = p.author
    p.author = {"author_type": "system", "author_name": "supervisor", "author_model": None}   # the supervisor prepares; the researcher reviews
    try:
        made = [PR.prepare(p, wsid, commits=shas, title=title or f"{st['plan_key']} verified work",
                           reason=f"{len(shas)} commit(s) verified by the supervisor (plan {st['plan_key']})") for wsid, shas in by_ws.items()]
    finally:
        p.author = human
    return {"plan": st["plan_key"], "candidates": [{"id": m.get("id") or m.get("promotion"), "workspace": w, "commits": by_ws[w]}
                                                    for m, w in zip(made, by_ws)]}


def _after_failure(p, st, rs, tid, e, a, diagnostics, cat, c, out) -> None:
    from . import orchestrate as O
    spec = _flat(st, tid)
    max_att = int((spec.get("execution") or {}).get("max_attempts") or c["max_attempts_per_task"])
    n_fail = len([x for x in e["attempts"] if not x.get("voided")])
    try:   # keep the established attempt ledger and escalation rules in step
        tier = a.get("tier") or (a.get("profile") or "agent")
        O.record_attempt(p, tid, tier, "fail", cat if cat in O.CATEGORIES else "incorrect_implementation", note=(diagnostics or [""])[0][:200])
    except ResearchError:
        pass
    if cat == "scientific_assumption":
        e.update(phase="attention", reason="a scientific assumption looks wrong: review the proposal or problem report before anything continues")
        _event(p, tid, "supervisor_attention", e["reason"])
        out["blocked"].append({"task": tid, "reason": e["reason"]})
        return
    if cat in ("missing_prerequisite", "role_contradiction"):   # more attempts of the same task cannot help: stop with the actionable reason
        _block(p, st, rs, tid, f"{cat}: {a.get('blocker')}", out)
        return
    if cat in NEEDS_HUMAN:
        _block(p, st, rs, tid, f"{cat}: {(diagnostics or ['needs a human'])[0][:300]}", out)
        return
    if c["on_failure"] == "pause":
        rs["state"] = "paused"
        e.update(phase="attention", reason="on_failure=pause: " + (diagnostics or [""])[0][:200])
        return
    if c["on_failure"] == "stop":
        rs["state"] = "stopped"
        e.update(phase="blocked", reason="on_failure=stop")
        return
    if n_fail >= max_att:
        _block(p, st, rs, tid, f"attempt budget exhausted after {n_fail} attempt(s); last problem: {(diagnostics or ['unknown'])[0][:300]}", out)
        return
    if rs["budgets"]["attempts"] >= int(c["max_total_attempts"]):
        _block(p, st, rs, tid, "plan attempt budget exhausted (max_total_attempts)", out)
        return
    e.update(phase="repair_pending", reason=f"repairing after attempt {a['n']}: {(diagnostics or [''])[0][:200]}")
    out["repairs"].append({"task": tid, "after_attempt": a["n"], "category": cat})
    _event(p, tid, "supervisor_repair", f"attempt {a['n']} failed [{cat}]; repair dispatch queued")


# ----------------------------------------------------------------------------------------------- HPC polling
def _poll_hpc(p: Project, st: Dict[str, Any], rs: Dict[str, Any], c: Dict[str, Any], out: Dict[str, Any]) -> None:
    waiting = [t for t, e in rs["tasks"].items() if e.get("phase") == "waiting_hpc"]
    if not waiting:
        return
    if time.time() - rs.get("last_hpc_poll", 0) >= float(c["hpc_poll_seconds"]):
        rs["last_hpc_poll"] = time.time()
        try:    # read-only reconciliation: status and result collection; it never submits anything
            from .hpc import remote as HR
            HR.sync(p, cluster_name=None)
        except ResearchError as ex:
            out["events"].append(f"hpc sync unavailable: {ex}")   # unavailable status is NOT a failure: nothing is resubmitted
        except Exception as ex:                                    # noqa: BLE001 (defensive: a poll must never kill the supervisor)
            out["events"].append(f"hpc sync error: {ex}")
    for tid in waiting:
        e = rs["tasks"][tid]
        a = e["attempts"][-1]
        e["phase"] = "verifying"
        _verify_attempt(p, st, rs, tid, e, a, c, out)


def _apply_deferred(p: Project, st: Dict[str, Any], rs: Dict[str, Any]) -> None:
    """Spec changes requested while a task was running take effect once it is no longer in flight."""
    from . import plans as P
    stt = PS.load_state(p, st["plan_key"])
    if not stt or not stt.get("deferred"):
        return
    changed = False
    for key, d in list(stt["deferred"].items()):
        tid = stt["tasks"].get(key)
        if tid and (rs["tasks"].get(tid) or {}).get("phase") not in ("running", "verifying", "waiting_hpc"):
            task = next((t for t in stt["spec"].get("tasks_flat", []) if t["key"] == key), None)
            if task and p.get("task", tid)["status"] != "done":
                P._update_task(p, tid, **PS._task_kwargs(p, stt, task, tid))
            stt["deferred"].pop(key)
            changed = True
    if changed:
        PS.save_state(p, stt)


# ----------------------------------------------------------------------------------------------- human controls
def approve_task(p: Project, tid: str, note: Optional[str] = None) -> Dict[str, Any]:
    require_human(p, "Approving a task to start")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    with _lock(p):
        rs = load_run(p, hit[0]["plan_id"])
        if not rs:
            raise ResearchError("the plan has not been run")
        rs["approvals"][tid] = {"ts": now_iso(), "note": note}
        _entry(rs, tid)["phase"] = "waiting"
        save_run(p, rs)
    p.event("task", tid, "supervisor_approved", note or "approved to start")
    return {"task": tid, "approved": True}


def resolve(p: Project, tid: str, outcome: str, note: Optional[str] = None) -> Dict[str, Any]:
    """A human settles an attempt the supervisor could not classify: failed (retry allowed), completed (verify it), or retry now."""
    require_human(p, "Resolving an unknown attempt")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    with _lock(p):
        rs = load_run(p, hit[0]["plan_id"])
        e = _entry(rs, tid)
        a = e["attempts"][-1] if e["attempts"] else None
        if outcome == "failed" and a:
            a.update(outcome="failed", state="failed", category=a.get("category") or "agent_process_failure", note=note)
            e["phase"] = "repair_pending"
        elif outcome == "completed" and a:
            a.update(outcome="completed", state="done", note=note)
            e["phase"] = "verifying"
        elif outcome == "retry":
            e["phase"] = "repair_pending" if e["attempts"] else "waiting"
        elif outcome == "unblock":
            e["phase"] = "waiting"
            from . import plans as P
            P.update_task(p, tid, status="todo")
        else:
            raise ResearchError("outcome must be failed, completed, retry or unblock")
        e["reason"] = None
        if rs["state"] in ("attention", "blocked"):
            rs["state"] = "running"
        save_run(p, rs)
    p.event("task", tid, "supervisor_resolved", f"{outcome}: {note or ''}")
    return {"task": tid, "outcome": outcome}


def accept(p: Project, tid: str, reason: str) -> Dict[str, Any]:
    """Human override: complete a supervised task without supervisor verification. Always logged as such."""
    require_human(p, "Accepting a task without verification")
    if not reason or not reason.strip():
        raise ResearchError("accepting without verification needs --reason (it is recorded)")
    from . import plans as P
    hit = PS.find_task_key(p, tid)
    with authority(tid, "human accept: " + reason):
        P.update_task(p, tid, status="verify")
        P.complete_task(p, tid, result=f"accepted by a human WITHOUT supervisor verification: {reason}", completed_by="human")
    if hit:
        with _lock(p):
            rs = load_run(p, hit[0]["plan_id"])
            if rs:
                e = _entry(rs, tid)
                e.update(phase="verified", reason=None, accepted_without_verification=reason)
                save_run(p, rs)
    p.event("task", tid, "supervisor_accepted", "accepted without verification: " + reason)
    return {"task": tid, "accepted": True}


def void_attempts(p: Project, tid: str, reason: str, attempts: Optional[List[int]] = None) -> Dict[str, Any]:
    """A human declares attempts invalid because of a defect outside the agent's work (Research OS, the provider, the environment).
    They stay in the ledger as evidence but stop counting against the task's budget. Default: every attempt that did not verify.
    If an earlier attempt still stands as a failed verification, the next dispatch is a repair carrying its diagnostics."""
    require_human(p, "Voiding attempts")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    with _lock(p):
        rs = load_run(p, hit[0]["plan_id"])
        e = _entry(rs, tid)
        n = 0
        for a in e["attempts"]:
            if (attempts is None or a["n"] in attempts) and not a.get("voided") and a.get("state") != "verified":
                a["voided"] = reason
                n += 1
                rs["budgets"]["attempts"] = max(0, rs["budgets"]["attempts"] - 1)
        standing = [a for a in e["attempts"] if not a.get("voided")]
        failed_verification = bool(standing and standing[-1].get("verification") and not standing[-1]["verification"].get("passed"))
        e.update(phase="repair_pending" if failed_verification else "waiting", reason=None)
        if rs["state"] in ("blocked", "attention"):
            rs["state"] = "running"
        save_run(p, rs)
    from . import plans as P
    if p.get("task", tid)["status"] == "blocked":
        P.update_task(p, tid, status="todo", blockers=None)
    p.event("task", tid, "supervisor_voided", f"{n} attempt(s) voided: {reason}")
    return {"task": tid, "voided": n}


# ----------------------------------------------------------------------------------------------- agent reporting
def _reports_path(p: Project, tid: str) -> str:
    return sdir(p, "reports", tid + ".jsonl")


def _reports(p: Project, tid: str, execution: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        rows = [json.loads(l) for l in open(_reports_path(p, tid), encoding="utf-8") if l.strip()]
    except OSError:
        return []
    return [r for r in rows if not execution or r.get("execution") == execution]


def _active_attempt(p: Project, tid: str) -> Dict[str, Any]:
    execution = os.environ.get("RESEARCH_EXECUTION")
    if not execution:
        raise PolicyBlocked("agent reports must come from a supervised execution (RESEARCH_EXECUTION is not set)",
                            hint="Only an agent launched by the Research OS supervisor can report on a task.")
    hit = PS.find_task_key(p, tid)
    rs = hit and load_run(p, hit[0]["plan_id"])
    e = rs and rs["tasks"].get(tid)
    if not e or not e["attempts"]:
        raise PolicyBlocked(f"{tid} has no active supervised attempt")
    a = e["attempts"][-1]
    if a.get("execution") != execution:
        raise PolicyBlocked(f"this execution ({execution[:8]}) does not own {tid}; the active attempt belongs to {str(a.get('execution'))[:8]}",
                            hint="A stale or foreign agent cannot report on this task.")
    if e.get("phase") not in ("running", "verifying", "waiting_hpc"):
        raise PolicyBlocked(f"{tid} is {e.get('phase')}: late reports from a finished attempt are refused")
    return a


def report(p: Project, tid: str, kind: str, **fields: Any) -> Dict[str, Any]:
    """Structured agent report. kind: progress | milestone | artifact | metric | run | hpc_job | code | problem | report | finish | propose."""
    a = _active_attempt(p, tid)
    rec = {"ts": now_iso(), "task": tid, "execution": a["execution"], "attempt": a["n"], "kind": kind, "by": (p.author or {}).get("author_name") or "agent"}
    for k, v in fields.items():
        if v is not None:
            rec[k] = taskprompt.redact(v) if isinstance(v, str) else v
    if kind == "artifact":
        path = fields.get("path") or ""
        if os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
            raise PolicyBlocked("artifact paths must be relative to the workspace and stay inside it")
        base = a.get("cwd") or p.root
        rec["exists"] = os.path.exists(os.path.join(base, path))
    if kind == "problem" and fields.get("category") not in CATEGORIES:
        raise ResearchError(f"problem needs --category, one of: {', '.join(CATEGORIES)}")
    with _lock(p):
        with open(_reports_path(p, tid), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        hit = PS.find_task_key(p, tid)
        rs = load_run(p, hit[0]["plan_id"])
        att = rs["tasks"][tid]["attempts"][-1]
        if kind == "run" and fields.get("run"):
            att.setdefault("reported_runs", []).append(fields["run"])
        if kind == "finish":
            att["verify_requested"] = now_iso()
        save_run(p, rs)
    p.event("task", tid, "agent_" + kind, (rec.get("message") or rec.get("summary") or rec.get("path") or rec.get("run") or kind)[:300])
    return {"recorded": kind, "task": tid, "attempt": a["n"]}


# ----------------------------------------------------------------------------------------------- plan-change proposals
def _prop_dir(p: Project) -> str:
    d = p.path("supervisor", "proposals")
    os.makedirs(d, exist_ok=True)
    return d


def list_proposals(p: Project) -> List[Dict[str, Any]]:
    d = _prop_dir(p)
    return [json.load(open(os.path.join(d, n), encoding="utf-8")) for n in sorted(os.listdir(d)) if n.endswith(".json")]


def _prop_load(p: Project, pid: str) -> Dict[str, Any]:
    try:
        return json.load(open(os.path.join(_prop_dir(p), pid + ".json"), encoding="utf-8"))
    except OSError:
        raise ResearchError(f"No proposal {pid}")


def _prop_save(p: Project, pr: Dict[str, Any]) -> None:
    _atomic_write(os.path.join(_prop_dir(p), pr["id"] + ".json"), json.dumps(pr, indent=1, sort_keys=True) + "\n")


def classify_change(p: Project, text: str) -> Dict[str, Any]:
    """Operational = only adds new tasks; anything that changes existing definitions, experiments, hypotheses or checks is scientific."""
    d = PS.diff(p, text)
    kinds = {op["op"] for pl in d["plans"] for op in pl["ops"]}
    sci = sorted(k for k in kinds if k not in ("create_task",))
    return {"classification": "operational" if kinds and not sci else "scientific", "operations": sorted(kinds), "conflicts": d["conflicts"]}


def _auto_ok(p: Project, c: Dict[str, Any], st: Optional[Dict[str, Any]], doc: Dict[str, Any], cl: Dict[str, Any]) -> bool:
    """Auto-apply needs governance (type allow-list, cap) AND the legacy supervisor switches; the stricter always wins."""
    from . import governance as G
    g = G.cfg(p)["tasks"]
    tasks = [t for pl in doc["plans"] for t in pl["tasks"]]
    n = len(tasks)
    return (cl["classification"] == "operational" and not cl["conflicts"] and G.auto_apply_allowed(p) and c["auto_apply_operational"]
            and all((t.get("task_type") or "") in g["allowed_auto_types"] for t in tasks)
            and n <= int(g["max_auto_per_experiment"])
            and (st or {}).get("auto_tasks", 0) + n <= int(c["max_auto_tasks"]))


def propose(p: Project, tid: Optional[str], text: str, rationale: str, source: str = "agent") -> Dict[str, Any]:
    from . import governance as G
    if G.is_agent(p) and G.cfg(p)["tasks"]["agent_creation"] == "human_only":
        raise PolicyBlocked("Policy is human_only: agents may not propose plan changes", hint="Tell the researcher what you found instead.")
    PS.validate(text, p)
    cl = classify_change(p, text)
    doc = PS.normalize(PS.parse(text), PS._known(p))
    plan_key = doc["plans"][0]["key"]
    pr = {"id": _next_prop_id(p), "kind": "plan", "rev": 1, "ts": now_iso(), "task": tid, "plan_key": plan_key, "rationale": taskprompt.redact(rationale), "patch": text,
          "classification": cl["classification"], "operations": cl["operations"], "conflicts": cl["conflicts"], "status": "pending", "source": source}
    st = PS.load_state(p, plan_key)
    c = cfg(p, st)
    auto = _auto_ok(p, c, st, doc, cl)
    if cl["classification"] == "scientific" and not c["require_approval_for_scientific_changes"] and not G.cfg(p)["scientific_changes"]["require_human_approval"] and not cl["conflicts"]:
        auto = True
    if auto:
        _apply_proposal(p, pr, "auto (policy)")
    else:
        _prop_save(p, pr)
        p.event("plan", (st or {}).get("plan_id"), "plan_proposal", f"{pr['id']} ({cl['classification']}) proposed by {source}: {rationale[:160]}")
    return pr


def _next_prop_id(p: Project) -> str:
    return f"PROP-{len(list_proposals(p)) + 1:04d}"


EXP_FIELDS = ("title", "hypothesis", "rationale", "method", "expected_outputs", "related")


def propose_experiment(p: Project, fields: Dict[str, Any], source: str = "agent") -> Dict[str, Any]:
    """An agent's experiment proposal. It always waits for a human: nothing is created until approved with the PIN."""
    from . import governance as G
    if G.is_agent(p) and G.cfg(p)["experiments"]["agent_creation"] == "human_only":
        raise PolicyBlocked("Policy is human_only: agents may not propose experiments")
    miss = [k for k in EXP_FIELDS if not str(fields.get(k) or "").strip()]
    if miss:
        raise ResearchError("An experiment proposal needs: " + ", ".join(miss), hint="Give hypothesis, rationale, method, expected outputs and how it relates to existing work.")
    pr = {"id": _next_prop_id(p), "kind": "experiment", "rev": 1, "ts": now_iso(), "task": None, "plan_key": None,
          "fields": {k: taskprompt.redact(str(fields[k])) for k in EXP_FIELDS}, "rationale": taskprompt.redact(str(fields["rationale"])),
          "classification": "scientific", "operations": ["create_experiment"], "conflicts": [], "status": "pending", "source": source}
    _prop_save(p, pr)
    p.event("experiment", None, "experiment_proposal", f"{pr['id']} proposed by {source}: {fields['title'][:120]}")
    return pr


def _apply_proposal(p: Project, pr: Dict[str, Any], who: str, status: str = "applied") -> None:
    from . import governance as G, services
    with G.approved(pr["id"]):
        if pr.get("kind") == "experiment":
            f = pr["fields"]
            res = [services.create_experiment(p, f["title"], hypothesis=f["hypothesis"], motivation=f["rationale"], method=f["method"],
                                              expected_outcome=f["expected_outputs"], notes="Relation to existing work: " + f["related"])["id"]]
        else:
            res = PS.apply(p, pr["patch"], source=f"proposal:{pr['id']} {who}")["plans"]
    pr.update(status=status, decided_by=who, decided_at=now_iso(), result=res)
    if pr.get("kind") != "experiment":
        st = PS.load_state(p, pr["plan_key"])
        if st and pr["classification"] == "operational":
            st["auto_tasks"] = st.get("auto_tasks", 0) + sum(1 for op in pr.get("operations", []) if op == "create_task")
            PS.save_state(p, st)
    _prop_save(p, pr)


def decide_proposal(p: Project, pid: str, approve: Any, note: Optional[str] = None, pin: Optional[str] = None, rev: Optional[int] = None) -> Dict[str, Any]:
    """Decide one proposal: approve (bool True / "approve"), reject, or "revise" (ask the agent to change it).
    Approving needs the PIN; it is applied exactly once, under the supervisor lock, and only if nothing changed since `rev`."""
    from . import approval, governance as G
    action = "approve" if approve is True else "reject" if approve is False else str(approve)
    if action not in ("approve", "reject", "revise"):
        raise ResearchError(f"Unknown decision {action!r}: approve, reject or revise")
    require_human(p, "Approving or rejecting a plan change")
    with _lock(p):
        pr = _prop_load(p, pid)
        if pr["status"] != "pending":
            raise ResearchError(f"{pid} is already {pr['status']}")
        if rev is not None and int(rev) != pr.get("rev", 1):
            raise ResearchError(f"{pid} changed since you opened it (now revision {pr.get('rev', 1)}): review it again")
        if action == "approve":
            approval.confirm(p, f"Approve {pid}", note, pin)
            if pr.get("kind") != "experiment":
                cl = classify_change(p, pr["patch"])
                if cl["conflicts"]:
                    raise ResearchError(f"{pid} no longer applies cleanly: " + "; ".join(cl["conflicts"]), hint="Ask the agent to revise it.")
            _apply_proposal(p, pr, "human" + (f": {note}" if note else ""), status="approved")
        else:
            if action == "revise" and not (note or "").strip():
                raise ResearchError("Say what should change (--note)")
            pr.update(status="rejected" if action == "reject" else "revision_requested", decided_by="human", decided_at=now_iso(), note=note)
            if G.cfg(p)["approval"]["record_rejections"]:
                p.event("plan", None, "proposal_" + pr["status"], f"{pid}: {(note or '')[:300]}")
            _prop_save(p, pr)
    p.event("plan", None, "plan_proposal_" + pr["status"], f"{pid} {pr['status']}")
    return pr


def revise_proposal(p: Project, pid: str, text: Optional[str] = None, fields: Optional[Dict[str, Any]] = None, rationale: Optional[str] = None) -> Dict[str, Any]:
    """The agent's answer to a revision request: same proposal, new content, back to pending (revision + 1)."""
    with _lock(p):
        pr = _prop_load(p, pid)
        if pr["status"] != "revision_requested":
            raise ResearchError(f"{pid} is {pr['status']}; only a proposal with a revision request can be revised")
        if pr.get("kind") == "experiment":
            pr["fields"].update({k: v for k, v in (fields or {}).items() if k in EXP_FIELDS})
        else:
            PS.validate(text, p)
            cl = classify_change(p, text)
            pr.update(patch=text, classification=cl["classification"], operations=cl["operations"], conflicts=cl["conflicts"])
        pr.update(status="pending", rev=pr.get("rev", 1) + 1, rationale=taskprompt.redact(rationale or pr["rationale"]))
        _prop_save(p, pr)
    return pr


# ----------------------------------------------------------------------------------------------- daemon
def _daemon_path(p: Project) -> str:
    return sdir(p, "daemon.json")


def daemon_status(p: Project) -> Dict[str, Any]:
    try:
        d = json.load(open(_daemon_path(p), encoding="utf-8"))
    except OSError:
        return {"running": False}
    alive = False
    try:
        os.kill(int(d["pid"]), 0)
        alive = True
    except (ProcessLookupError, KeyError, ValueError):
        alive = False
    except PermissionError:
        alive = True
    if alive and os.name == "posix":      # a killed child that nobody has reaped yet is a zombie, not a running supervisor
        try:
            stat = subprocess.run(["ps", "-o", "stat=", "-p", str(d["pid"])], capture_output=True, text=True, timeout=5).stdout.strip()
            alive = bool(stat) and not stat.startswith("Z")
        except (OSError, subprocess.SubprocessError):
            pass
    age = time.time() - float(d.get("heartbeat", 0))
    from . import __version__
    stale = alive and bool(d.get("version")) and d["version"] != __version__
    return {"running": alive, "pid": d.get("pid"), "started_at": d.get("started_at"), "heartbeat_age_seconds": round(age, 1),
            "version": d.get("version"), "installed_version": __version__, "restart_required": stale,
            "restart_reason": f"the supervisor runs Research OS {d.get('version')}; {__version__} is installed" if stale else None,
            "last_reload": d.get("last_reload"),
            "note": None if alive else "no such process; state is intact and a new `supervisor start` will reconcile it"}


# Settings the next scheduling pass reads from disk (the daemon reopens the project every pass), settings that only affect agents/jobs launched
# afterwards (a running agent keeps what it started with), and what genuinely needs a new process.
LIVE_KEYS = ("supervisor", "governance", "promotion", "auto_promotion", "verification", "coordination")
NEXT_LAUNCH_KEYS = ("agents", "environment", "hpc")


def _flat_cfg(d: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (d or {}).items():
        out.update(_flat_cfg(v, f"{prefix}{k}.") if isinstance(v, dict) and v else {f"{prefix}{k}": v})
    return out


def _snapshot(p: Project) -> Dict[str, Any]:
    return _flat_cfg({k: v for k, v in p.config.items() if k in LIVE_KEYS + NEXT_LAUNCH_KEYS + ("layout",)})


def _classify(keys: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {"live": [], "next_launch": [], "restart": []}
    for k in sorted(keys):
        top = k.split(".")[0]
        out["live" if top in LIVE_KEYS else "next_launch" if top in NEXT_LAUNCH_KEYS else "restart"].append(k)
    return out


def validate_settings(p: Project) -> List[str]:
    """Everything the next pass would choke on, found before it does."""
    from . import governance as G
    errs: List[str] = []
    try:
        c = cfg(p)
        for k in ("poll_seconds", "max_parallel_agents", "max_attempts_per_task", "max_total_attempts"):
            if float(c[k]) <= 0:
                errs.append(f"supervisor.{k} must be positive (got {c[k]!r})")
        if c.get("escalation") and not isinstance(c["escalation"], dict):
            errs.append("supervisor.escalation must be a mapping")
        if c["on_failure"] not in PS.ON_FAILURE:
            errs.append(f"supervisor.on_failure must be one of {', '.join(PS.ON_FAILURE)}")
    except (ValueError, TypeError, KeyError) as ex:
        errs.append(f"supervisor settings are malformed: {ex}")
    try:
        G.cfg(p)
    except ResearchError as ex:
        errs.append(str(ex))
    errs += AP.policy(p)["conflicts"]
    return errs


def daemon_reload(p: Project, wait: float = 15.0) -> Dict[str, Any]:
    """Validate the project settings and have the running supervisor apply them now, without touching agents, worktrees, plan state, budgets or
    promotion state (the supervisor already re-reads settings every pass; this makes it immediate, proves it, and says what still needs a restart)."""
    require_human(p, "Reloading the supervisor")
    errs = validate_settings(p)
    if errs:
        return {"valid": False, "errors": errs, "applied": False, "note": "Nothing was applied; fix these in the settings and reload again."}
    d = daemon_status(p)
    try:
        prev = json.load(open(_daemon_path(p), encoding="utf-8")).get("settings") or {}
    except (OSError, ValueError):
        prev = {}
    now = _snapshot(p)
    changed = _classify([k for k in set(prev) | set(now) if prev.get(k) != now.get(k)])
    out = {"valid": True, "errors": [], "changed": changed, "daemon": d,
           "restart_required": bool(d.get("restart_required") or changed["restart"]),
           "restart_reasons": ([d["restart_reason"]] if d.get("restart_reason") else []) + [f"{k} needs a new supervisor process" for k in changed["restart"]]}
    if not d["running"]:
        return {**out, "applied": False, "note": "The supervisor is not running; these settings apply when it starts (`research supervisor start`)."}
    marker = sdir(p, "reload.request")
    _atomic_write(marker, now_iso() + "\n")
    t0 = time.time()
    while time.time() - t0 < wait and os.path.exists(marker):
        time.sleep(0.2)
    done = not os.path.exists(marker)
    return {**out, "applied": done, "daemon": daemon_status(p),
            "note": ("Applied at the next scheduling pass; running agents were not touched." if done else
                     "Requested; the supervisor is in the middle of a long pass and will apply it when that finishes.")}


def _spawn(root: str, exit_when_idle: bool = False) -> subprocess.Popen:
    argv = [sys.executable, "-m", "research.supervisord", "run", "--root", root] + (["--exit-when-idle"] if exit_when_idle else [])
    with Project(root, sync=False) as p:
        log = open(sdir(p, "daemon.log"), "ab")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.environ.get("PYTHONPATH", "")])}
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=os.name == "posix", env=env)


def daemon_restart(p: Project, timeout: float = 120.0) -> Dict[str, Any]:
    """Gracefully replace the supervisor process with one running the Research OS that is installed now.
    Agents and HPC jobs are separate processes and keep running; the old supervisor finishes its current pass, then the new one reconciles every
    recorded execution before scheduling (nothing is launched or submitted twice). If the old pass outlives `timeout`, the old supervisor starts
    its successor itself when it exits."""
    require_human(p, "Restarting the supervisor")
    s = daemon_status(p)
    before = s.get("pid")
    if not s["running"]:
        return {**daemon_start(p, detach=True), "restarted": False, "note": "It was not running, so it was started."}
    pending = sdir(p, "restart.pending")
    _atomic_write(pending, now_iso() + "\n")
    _atomic_write(sdir(p, "stop"), now_iso() + "\n")
    try:
        os.kill(int(s["pid"]), signal.SIGTERM)
    except ProcessLookupError:
        pass
    t0, gone_at = time.time(), None
    while time.time() - t0 < timeout:
        time.sleep(0.2)
        n = daemon_status(p)
        if n["running"] and n.get("pid") != before:
            return {**n, "restarted": True, "previous_pid": before, "note": "Restarted on Research OS " + str(n.get("version"))}
        if not n["running"]:
            gone_at = gone_at or time.time()
            if time.time() - gone_at > 20:          # the old process is gone and handed nothing over
                break
    try:
        os.unlink(sdir(p, "stop"))
    except OSError:
        pass
    if daemon_status(p)["running"] and daemon_status(p).get("pid") == before:
        return {**daemon_status(p), "restarted": False, "previous_pid": before,
                "note": "The supervisor is finishing a long pass; it will start its successor when that ends. Nothing is lost."}
    try:
        os.unlink(pending)
    except OSError:
        pass
    n = daemon_start(p, detach=True)
    return {**n, "restarted": bool(n.get("running")), "previous_pid": before, "note": "Restarted (the old process exited without a successor, so one was started)."}


def daemon_start(p: Project, detach: bool = True, exit_when_idle: bool = False) -> Dict[str, Any]:
    require_human(p, "Starting the supervisor")
    st = daemon_status(p)
    if st["running"]:
        return {**st, "already": True}
    argv = [sys.executable, "-m", "research.supervisord", "run", "--root", p.root] + (["--exit-when-idle"] if exit_when_idle else [])
    if not detach:
        return {"argv": argv}
    log = open(sdir(p, "daemon.log"), "ab")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=os.name == "posix", env=env)
    from . import notify
    notify.ensure_gateway(p)       # the machine's chat gateway delivers this supervisor's alerts (no-op when chat is off)
    for _ in range(50):
        time.sleep(0.1)
        s = daemon_status(p)
        if s["running"] and s.get("pid") == proc.pid:
            return s
    return {"running": False, "note": "daemon did not report in; see .research/supervisor/daemon.log"}


def daemon_stop(p: Project, cancel_agents: bool = False) -> Dict[str, Any]:
    require_human(p, "Stopping the supervisor")
    s = daemon_status(p)
    if not s["running"]:
        return {"running": False, "note": "not running"}
    _atomic_write(sdir(p, "stop"), now_iso() + "\n")
    try:
        os.kill(int(s["pid"]), signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(50):
        time.sleep(0.1)
        if not daemon_status(p)["running"]:
            break
    try:
        os.unlink(sdir(p, "stop"))
    except OSError:
        pass
    return daemon_status(p)


def run_forever(root: str, exit_when_idle: bool = False) -> int:
    """The persistent process. One per project (kernel lock); work state lives in files, so any restart resumes."""
    from .store import Project as _P
    sysauthor = {"author_type": "system", "author_name": "supervisor", "author_model": None}
    with _P(root, author=sysauthor, sync=False) as p0:
        lockfile = sdir(p0, "daemon.lock")
        sup_dir = os.path.dirname(lockfile)       # where the stop / reload markers live (the ledger's, in shared-ledger mode)
    try:
        with durability.lock(lockfile, blocking=False):
            stop = {"now": False}
            signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
            signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
            started = now_iso()
            idle = 0
            while not stop["now"]:
                with _P(root, author=sysauthor, sync=False) as p:
                    from . import __version__
                    reloaded = None
                    if os.path.exists(sdir(p, "reload.request")):
                        reloaded = {"at": now_iso(), "errors": validate_settings(p)}
                        try:
                            os.unlink(sdir(p, "reload.request"))
                        except OSError:
                            pass
                    keep = {}
                    try:
                        keep = {"last_reload": (json.load(open(_daemon_path(p), encoding="utf-8")) or {}).get("last_reload")}
                    except (OSError, ValueError):
                        pass
                    _atomic_write(_daemon_path(p), json.dumps({"pid": os.getpid(), "started_at": started, "heartbeat": time.time(), "root": root, "version": __version__,
                                                               "settings": _snapshot(p), **keep, **({"last_reload": reloaded} if reloaded else {})}) + "\n")
                    if os.path.exists(sdir(p, "stop")):
                        break
                    try:
                        r = tick(p)
                    except Exception as ex:                       # noqa: BLE001 - the daemon must survive a bad tick and say why
                        with open(sdir(p, "daemon.log"), "a") as lf:
                            lf.write(f"{now_iso()} tick error: {type(ex).__name__}: {ex}\n")
                        r = {"plans": 1}
                    from . import notify
                    notify.step(p)
                    poll = float(cfg(p)["poll_seconds"])
                    if r.get("next_wake") and not r.get("launched"):      # only provider waits are pending: sleep to the earliest reset (capped so stop/heartbeat stay responsive)
                        poll = max(poll, min(60.0, r["next_wake"] - time.time()))
                    active = any(x.get("state") in ("running",) for x in all_runs(p))
                idle = 0 if active else idle + 1
                if exit_when_idle and idle >= 2:
                    break
                for _ in range(int(poll * 10)):
                    if stop["now"] or os.path.exists(os.path.join(sup_dir, "stop")) or os.path.exists(os.path.join(sup_dir, "reload.request")):
                        break
                    time.sleep(0.1)
    except ResearchError:
        return 3        # another daemon owns this project
    with _P(root, author=sysauthor, sync=False) as p:
        try:
            os.unlink(_daemon_path(p))
        except OSError:
            pass
        pending = sdir(p, "restart.pending")
        if os.path.exists(pending):          # a restart was requested while this pass was running: hand over to a fresh process
            try:
                os.unlink(pending)
                os.unlink(sdir(p, "stop"))
            except OSError:
                pass
            _spawn(root, exit_when_idle)
    return 0


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="research.supervisor")
    ap.add_argument("action", choices=["run"])
    ap.add_argument("--root", required=True)
    ap.add_argument("--exit-when-idle", action="store_true")
    a = ap.parse_args(argv)
    return run_forever(a.root, a.exit_when_idle)

