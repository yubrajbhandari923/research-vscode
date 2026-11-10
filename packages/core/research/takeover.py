"""Human takeover and deterministic task controls (`research task intervene T-ID`).

Simple human decisions should not need YAML edits or an AI-generated intervention plan. This module is the menu behind `research task intervene`, the TUI and the
RPC op: it reports where a task stands (state, why, attempts used of allowed, the agent, the worktree) and offers a small closed set of controls, each previewed
before it is applied. No model is involved.

    attempts N / plan-budget N   an intervention (set_max_attempts / set_plan_budget): recorded, preflighted, the PIN approves it (resource change)
    retry / reverify / profile   the supervisor's existing human actions (attempt history untouched); refused while the attempt budget is exhausted
    pause                        pauses the plan (running agents are not stopped)
    logs / worktree              read-only
    takeover / release           the task is never launched, repaired or escalated while a person works in its worktree; `release` returns it to the
                                 supervisor through its NORMAL verification (a new attempt is not created; the person's changes are what is verified)

Nothing here resets, voids or deletes an attempt, relaxes a check, or stops an agent unless `--stop-agent` is given. Scientific-contract, frozen-test and resource
changes keep requiring human approval; they still go through `intervene`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import intervene as I, planspec as PS, supervisor as SV
from .util import ResearchError, now_iso

RUNNING = ("running", "verifying", "waiting_hpc")
ACTIONS = ("attempts", "plan-budget", "retry", "reverify", "profile", "pause", "logs", "worktree", "takeover", "release")


def _ctx(p, tid: str):
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan", hint="Only tasks of a supervised plan have an intervention menu.")
    st, key = hit
    rs = SV.load_run(p, st["plan_id"])
    if not rs:
        raise ResearchError(f"{st['plan_key']} has not been run yet", hint=f"`research plan run {st['plan_id']}`")
    return st, key, rs, (rs.get("tasks") or {}).get(tid) or {}


def _agent(p, e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from . import execution as E
    a = (e.get("attempts") or [None])[-1]
    if not a:
        return None
    row = {"attempt": a["n"], "profile": e.get("provider_override") or a.get("profile"), "model": a.get("model"), "execution": a.get("execution"), "state": a.get("state"), "running": False}
    if a.get("execution"):
        try:
            ex = E.show(p, a["execution"])
            row.update(execution_state=ex["state"], running=ex["state"] in ("prepared", "launched", "running") or bool(ex.get("alive")))
        except ResearchError:
            pass
    return row


def menu(p, tid: str) -> Dict[str, Any]:
    """Where the task stands and what can be done about it. Read-only: opening the menu never changes anything or stops anything."""
    from . import workspace as W
    st, key, rs, e = _ctx(p, tid)
    c = SV.cfg(p, st)
    spec = SV._flat(st, tid)
    live = [a for a in e.get("attempts", []) if not a.get("voided")]
    allowed = int((spec.get("execution") or {}).get("max_attempts") or c["max_attempts_per_task"])
    plan_used, plan_max = int((rs.get("budgets") or {}).get("attempts") or 0), int(c["max_total_attempts"])
    ws = W.for_task(p, tid)
    agent = _agent(p, e)
    running = e.get("phase") in RUNNING or bool(agent and agent["running"])
    exhausted = len(live) >= allowed or plan_used >= plan_max
    tk = e.get("takeover")
    ivs = [{"id": i["id"], "state": i["state"], "instruction": i["instruction"][:80]} for i in I.list_all(p, tid, open_only=True)]
    base = {"task": tid, "key": key, "title": p.get("task", tid)["title"], "plan": st["plan_key"], "plan_state": rs.get("state"), "phase": e.get("phase"),
            "reason": e.get("reason"), "attempts": {"used": len(live), "allowed": allowed, "voided": len(e.get("attempts", [])) - len(live), "exhausted": len(live) >= allowed},
            "plan_budget": {"used": plan_used, "max": plan_max, "exhausted": plan_used >= plan_max}, "agent": agent, "running": running,
            "takeover": tk, "worktree": ws["path"] if ws else None, "interventions": ivs, "verified": e.get("phase") == "verified"}

    def a(id_, label, ok=True, why=None, pin=False, arg=None):
        return {"id": id_, "label": label, "available": bool(ok), "why_not": None if ok else why, "needs_pin": pin, "arg": arg}
    done = base["verified"]
    free = not running and not tk and not done
    ready_for_attempt = free and not exhausted
    base["actions"] = [
        a("attempts", f"Set this task's attempt budget (now {allowed})", not done, "the task is verified", True, "N"),
        a("plan-budget", f"Set the plan's total attempt budget (now {plan_max}, {plan_used} used)", True, None, True, "N"),
        a("retry", "Retry now (a new attempt that repairs the last failure)", ready_for_attempt and bool(live),
          "an agent is running" if running else "a human has taken over" if tk else "the task is verified" if done else "no attempt has failed yet" if not live
          else "the attempt budget is exhausted: raise it first (attempts / plan-budget)"),
        a("reverify", "Re-run the checks on the latest output (no new attempt, no new run)", free and bool(live), "an agent is running" if running else "a human has taken over" if tk else "nothing to re-check"),
        a("profile", "Hand the task to another agent profile (next attempt)", ready_for_attempt, "an agent is running" if running else "a human has taken over" if tk else "the attempt budget is exhausted: raise it first", False, "PROFILE"),
        a("pause", "Pause the plan (running agents keep running)", rs.get("state") == "running", f"the plan is {rs.get('state')}"),
        a("logs", "Show the latest agent log", bool(agent and agent.get("execution")), "no attempt has run"),
        a("worktree", "Show the task's worktree", bool(ws), "the task has no experiment worktree on this machine"),
        a("takeover", "Take over: suspend scheduling for this task so you can work in its worktree", not tk and not done, "already taken over" if tk else "the task is verified"),
        a("release", "Release: hand the task back; the supervisor verifies it normally", bool(tk), "no takeover is active"),
    ]
    return base


def preview(p, tid: str, action: str, value: Any = None, stop_agent: bool = False) -> Dict[str, Any]:
    """What the action would do, including the numbers before and after and whether anything running is affected. Changes nothing."""
    m = menu(p, tid)
    act = next((x for x in m["actions"] if x["id"] == action), None)
    if not act:
        raise ResearchError(f"Unknown action {action!r}; choose from: {', '.join(ACTIONS)}")
    lines: List[str] = []
    run_note = (f"the running agent ({m['agent']['profile']}, attempt {m['agent']['attempt']}) is NOT affected" if m["running"] else "no agent is running")
    if not act["available"]:
        return {"action": action, "available": False, "why_not": act["why_not"], "lines": [f"not available: {act['why_not']}"], "affects_running": False, "needs_pin": False}
    if action in ("attempts", "plan-budget"):
        n = _positive(value, "a whole number of attempts, e.g. `attempts 12`")
        cur = m["attempts"]["allowed"] if action == "attempts" else m["plan_budget"]["max"]
        used = m["attempts"]["used"] if action == "attempts" else m["plan_budget"]["used"]
        if action == "plan-budget" and n < used:
            return {"action": action, "available": False, "why_not": f"{used} attempts are already used; the budget cannot be lower", "lines": [], "affects_running": False, "needs_pin": False}
        lines = [f"{'task attempt budget' if action == 'attempts' else 'plan total attempt budget'}: {cur} -> {n} ({used} used, {max(n - used, 0)} left afterwards)",
                 "attempt history is kept exactly as it is: nothing is reset or voided", f"{run_note}; the new number applies to the next decision",
                 "a resource change: recorded as a decision and approved with your PIN"]
        if action == "attempts" and n > m["plan_budget"]["max"]:
            lines.append(f"warning: more than the plan's total budget ({m['plan_budget']['max']}); raise it too (plan-budget {n + m['plan_budget']['used']})")
        if m["plan_state"] != "running":
            lines.append(f"the plan is {m['plan_state']}: nothing starts until `research plan resume {m['plan']}`")
    elif action == "retry":
        lines = [f"a new attempt (#{m['attempts']['used'] + 1} of {m['attempts']['allowed']}) repairs the last failure in the same worktree", "earlier attempts and their diagnostics stay on record"]
    elif action == "reverify":
        lines = ["the declared checks run again on the latest output under the current contract", "no agent starts and no new run or HPC job is created"]
    elif action == "profile":
        if not value:
            raise ResearchError("name the agent profile, e.g. `profile codex`")
        lines = [f"the next attempt (#{m['attempts']['used'] + 1} of {m['attempts']['allowed']}) goes to profile {value!r} in the same worktree, with a portable context document",
                 "the previous agent's session is not reused"]
    elif action == "pause":
        lines = [f"plan {m['plan']} stops scheduling new work", run_note + "; use `research plan stop --cancel-agents` to stop agents"]
    elif action == "takeover":
        lines = [f"{tid} will not be launched, repaired or escalated until you release it", "your worktree edits stay yours; nothing is committed on your behalf until release"]
        lines.append(f"a running agent keeps running and finishes normally ({run_note})" if m["running"] and not stop_agent else
                     "the running agent will be stopped (its attempt is kept as evidence, not voided)" if m["running"] else "no agent is running")
    elif action == "release":
        lines = ["the supervisor verifies the task's declared checks against the worktree as you left it (normal verification; no new attempt)",
                 "if they pass, your changes are committed on the experiment branch like any verified work and promotion rules apply",
                 "if they fail, the task is repaired or blocked under the usual budgets"]
    else:
        lines = ["read-only"]
    return {"action": action, "available": True, "lines": lines, "affects_running": bool(m["running"] and action == "takeover" and stop_agent),
            "needs_pin": act["needs_pin"], "running": m["running"]}


def _positive(v: Any, what: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ResearchError(f"needs {what}")
    if n < 1:
        raise ResearchError(f"needs {what}")
    return n


def _tick_interventions(p, tid: str) -> None:
    """Apply what was just approved now instead of waiting for the next supervisor pass; the state lock serializes it with a running supervisor."""
    st, _, _, _ = _ctx(p, tid)
    with SV._lock(p):
        rs = SV.load_run(p, st["plan_id"])
        out: Dict[str, Any] = {"events": []}
        I.step(p, st, rs, out)
        SV.save_run(p, rs)


def run(p, tid: str, action: str, value: Any = None, note: Optional[str] = None, pin: Optional[str] = None, stop_agent: bool = False) -> Dict[str, Any]:
    """Apply one control. Human only. Previewed by `preview`; refused when the preview says it is not available."""
    SV.require_human(p, f"Changing {tid}")
    pv = preview(p, tid, action, value, stop_agent)
    if not pv["available"]:
        raise ResearchError(f"{action} is not available for {tid}: {pv['why_not']}")
    if action in ("logs", "worktree"):
        return {"task": tid, "action": action, **_read(p, tid, action)}
    if action in ("attempts", "plan-budget"):
        n = _positive(value, "a whole number of attempts")
        act = ({"type": "set_max_attempts", "value": n} if action == "attempts" else {"type": "set_plan_budget", "field": "max_total_attempts", "value": n})
        it = I.create_planned(p, tid, f"human: {pv['lines'][0]}", [act], summary=pv["lines"][0], rationale=note or "set directly by the researcher with `research task intervene`",
                              source="takeover", allow_open=True)
        if it["state"] == "proposed":
            it = I.approve(p, it["id"], note or "set directly by the researcher", pin)
        _tick_interventions(p, tid)
        return {"task": tid, "action": action, "intervention": I.load(p, it["id"])["id"], "state": I.load(p, it["id"])["state"]}
    if action == "takeover":
        return start_takeover(p, tid, stop_agent, note)
    if action == "release":
        return release(p, tid, note)
    if action == "pause":
        SV.plan_pause(p, menu(p, tid)["plan"])
        return {"task": tid, "action": action}
    if action == "profile":
        SV.continue_with(p, tid, str(value), note)
        return {"task": tid, "action": action, "profile": value}
    SV.resolve(p, tid, "retry" if action == "retry" else "completed", note or f"human: {action} via `research task intervene`")
    return {"task": tid, "action": action}


def _read(p, tid: str, action: str) -> Dict[str, Any]:
    from . import execution as E
    m = menu(p, tid)
    if action == "worktree":
        return {"worktree": m["worktree"]}
    lg = E.logs(p, m["agent"]["execution"], 6000)
    return {"execution": m["agent"]["execution"], "out": (lg.get("out") or "")[-3000:], "err": (lg.get("err") or "")[-1500:]}


def start_takeover(p, tid: str, stop_agent: bool = False, note: Optional[str] = None) -> Dict[str, Any]:
    """Suspend scheduling for this task. A running agent is left alone (it finishes normally) unless `stop_agent`; its attempt is then kept as evidence."""
    from . import execution as E
    SV.require_human(p, "Taking over a task")
    st, key, _, _ = _ctx(p, tid)
    with SV._lock(p):
        rs = SV.load_run(p, st["plan_id"])
        e = SV._entry(rs, tid)
        if e.get("takeover"):
            return {"task": tid, "action": "takeover", "already": True, "worktree": (menu(p, tid) or {}).get("worktree")}
        running = e.get("phase") in RUNNING
        a = (e.get("attempts") or [None])[-1]
        if running and stop_agent:
            if e.get("phase") == "waiting_hpc" or (e.get("hpc_runs") and e.get("phase") != "verified"):
                raise ResearchError("Cannot stop the agent: computational jobs are pending; they would be resubmitted. Let them finish, then take over.")
            if a and a.get("execution"):
                try:
                    E.stop(p, a["execution"])
                except ResearchError:
                    pass
        e["takeover"] = {"by": (p.author or {}).get("author_name") or "human", "since": now_iso(), "note": note, "from_phase": e.get("phase"), "from_reason": e.get("reason"),
                         "agent_running": running and not stop_agent}
        if not (running and not stop_agent):
            e.update(phase="takeover", reason="a human has taken over; `research task intervene " + tid + " release` returns it to the supervisor")
        SV.save_run(p, rs)
    p.event("task", tid, "takeover_started", f"{tid}: human takeover" + (f" ({note})" if note else "") + ("; the running agent was left running" if running and not stop_agent else ""))
    m = menu(p, tid)
    return {"task": tid, "action": "takeover", "worktree": m["worktree"], "agent_still_running": bool(running and not stop_agent), "phase": m["phase"]}


def release(p, tid: str, note: Optional[str] = None) -> Dict[str, Any]:
    """Return the task to the supervisor through its normal verification of the latest attempt against the worktree as the person left it."""
    SV.require_human(p, "Releasing a task")
    st, key, _, _ = _ctx(p, tid)
    with SV._lock(p):
        rs = SV.load_run(p, st["plan_id"])
        e = SV._entry(rs, tid)
        tk = e.pop("takeover", None)
        if not tk:
            raise ResearchError(f"{tid} is not under takeover")
        a = (e.get("attempts") or [None])[-1]
        if e.get("phase") in RUNNING:
            pass                                          # the agent that was left running is still the supervisor's to finish
        elif a:
            a["takeover"] = {**tk, "released": now_iso(), "note_release": note}
            a.update(outcome="completed", state="done", note=note or "released after human takeover")
            e.update(phase="verifying", reason=None)
        else:
            e.update(phase="waiting", reason=None)
        if rs["state"] in ("attention", "blocked"):
            rs["state"] = "running"
        SV.save_run(p, rs)
    p.event("task", tid, "takeover_released", f"{tid}: returned to the supervisor for normal verification" + (f" ({note})" if note else ""))
    return {"task": tid, "action": "release", "phase": menu(p, tid)["phase"]}


def select(p, plan: Optional[str], stuck: bool = False, todo: bool = False) -> List[str]:
    """Task ids of a plan by state: `stuck` = blocked / needs attention / taken over; `todo` = not started yet (waiting, ready or never scheduled)."""
    if plan:
        st = PS._state_for(p, plan)
    else:
        runs = [s for s in PS.all_states(p) if SV.load_run(p, s["plan_id"])]
        if len(runs) != 1:
            raise ResearchError("name the plan with --plan" + (f" ({', '.join(s['plan_key'] for s in runs)})" if runs else ""))
        st = runs[0]
    rs = SV.load_run(p, st["plan_id"]) or {}
    out = []
    for key, tid in sorted(st["tasks"].items(), key=lambda kv: kv[1]):
        if key in (st.get("retired") or {}) or not p.exists("task", tid):
            continue
        ph = ((rs.get("tasks") or {}).get(tid) or {}).get("phase")
        if ph == "verified" or p.get("task", tid)["status"] == "done":
            continue
        if (stuck and ph in ("blocked", "attention", "takeover")) or (todo and ph in (None, "waiting", "ready", "awaiting_approval")):
            out.append(tid)
    return out


def set_attempts(p, tids: List[str], n: int, note: Optional[str] = None, pin: Optional[str] = None, apply: bool = False) -> List[Dict[str, Any]]:
    """Change the attempt budget of several tasks at once (stuck ones, or ones that have not started). Each is its own recorded, PIN-approved intervention; without
    `apply` nothing changes and the rows show current -> new. Attempt history is never touched."""
    rows = []
    for tid in tids:
        m = menu(p, tid)
        row = {"task": tid, "title": m["title"], "phase": m["phase"], "used": m["attempts"]["used"], "from": m["attempts"]["allowed"], "to": n, "running": m["running"]}
        pv = preview(p, tid, "attempts", n)
        row["ok"] = pv["available"]
        row["why_not"] = pv.get("why_not")
        if apply and pv["available"]:
            r = run(p, tid, "attempts", n, note, pin)
            row.update(intervention=r["intervention"], state=r["state"])
        rows.append(row)
    return rows
