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

from . import durability, planspec as PS, taskprompt
from .store import Project, _atomic_write
from .util import NoFrozenTests, PolicyBlocked, ResearchError, now_iso

DEFAULTS: Dict[str, Any] = {
    "poll_seconds": 5, "max_parallel_agents": 1, "max_attempts_per_task": 3, "max_total_attempts": 20, "max_wall_minutes": 480,
    "attempt_timeout_minutes": 90, "on_failure": "diagnose_and_repair", "enforce_completion": True, "auto_apply_operational": True,
    "max_auto_tasks": 5, "require_approval_for_scientific_changes": True, "session_resume": "auto", "context_max_chars": 24000,
    "hpc_poll_seconds": 30, "max_hpc_resubmissions": 1, "launch_grace_seconds": 120,
}
PHASES = ("waiting", "ready", "awaiting_approval", "running", "waiting_hpc", "verifying", "repair_pending", "attention", "blocked",
          "verified", "retired")
REPAIRABLE = {"incorrect_implementation", "agent_process_failure", "insufficient_test_oracle", "ambiguous_specification"}
NEEDS_HUMAN = {"environment", "resource_limit", "unknown_execution_state"}
CATEGORIES = ("incorrect_implementation", "ambiguous_specification", "insufficient_test_oracle", "environment", "scientific_assumption",
              "resource_limit", "agent_process_failure", "unknown_execution_state")


# ----------------------------------------------------------------------------------------------- config & paths
def cfg(p: Project, plan_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = {**DEFAULTS, **(p.config.get("supervisor") or {})}
    ex = ((plan_state or {}).get("spec") or {}).get("execution") or {}
    for k in ("max_parallel_agents", "max_attempts_per_task", "max_total_attempts", "max_wall_minutes", "on_failure", "max_hpc_resubmissions",
              "require_approval_for_scientific_changes"):
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


def require_human(p: Project, what: str) -> None:
    if (p.author or {}).get("author_type") not in (None, "human") :
        raise PolicyBlocked(f"{what} is a human decision: agents cannot do it", hint="Ask the researcher; supervisor policy is not agent-editable.")


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
    if not (p.config.get("supervisor") or {}).get("enforce_completion", DEFAULTS["enforce_completion"]):
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
                     "profile": flat[key]["agent"].get("profile"), "attempts": len(att), "reason": e.get("reason"),
                     "execution": last and {"id": last["execution"], "state": ex and ex["state"], "alive": ex and ex.get("alive"),
                                            "kind": last["kind"], "verification": last.get("verification"), "outcome": last.get("outcome")},
                     "requires_approval": bool((flat[key].get("execution") or {}).get("requires_approval")) and not (rs.get("approvals") or {}).get(tid),
                     "deferred": key in (st.get("deferred") or {}), "hpc_runs": e.get("hpc_runs") or []})
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["phase"]] = counts.get(r["phase"], 0) + 1
    done = sum(1 for r in rows if r["phase"] == "verified")
    return {"plan": st["plan_key"], "plan_id": st["plan_id"], "revision": st["revision"], "state": rs.get("state", "not_run"), "tasks": rows,
            "counts": counts, "progress": f"{done}/{len(rows)}", "budgets": {**(rs.get("budgets") or {}), "max_total_attempts": c["max_total_attempts"],
                                                                             "max_wall_minutes": c["max_wall_minutes"]},
            "proposals": [x for x in list_proposals(p) if x.get("plan_key") == st["plan_key"] and x["status"] == "pending"],
            "daemon": daemon_status(p), "enforced": {"completion": bool(c["enforce_completion"])}}


# ----------------------------------------------------------------------------------------------- scheduler tick
def _entry(rs: Dict[str, Any], tid: str) -> Dict[str, Any]:
    return rs["tasks"].setdefault(tid, {"phase": "waiting", "attempts": []})


def tick(p: Project, now: Optional[float] = None) -> Dict[str, Any]:
    """One deterministic scheduling pass over every running plan. Safe to call concurrently (state lock) and repeatedly."""
    out = {"plans": 0, "launched": [], "verified": [], "repairs": [], "blocked": [], "events": []}
    with _lock(p):
        for rs in all_runs(p):
            if rs.get("state") != "running":
                continue
            st = PS.load_state(p, rs["plan_key"])
            if not st:
                continue
            out["plans"] += 1
            t = time.time() if now is None else now
            rs["budgets"]["wall_seconds"] = rs["budgets"].get("wall_seconds", 0.0) + max(0.0, min(60.0, t - rs.get("last_tick", t)))
            rs["last_tick"] = t
            c = cfg(p, st)
            _reconcile_attempts(p, st, rs, c, out)
            _poll_hpc(p, st, rs, c, out)
            _schedule(p, st, rs, c, out)
            _apply_deferred(p, st, rs)
            if _plan_finished(p, st, rs):
                rs["state"] = "completed"
                rs["history"].append({"ts": now_iso(), "event": "completed"})
                p.event("plan", st["plan_id"], "supervisor_completed", f"plan {st['plan_key']} finished: every task verified")
            elif c["max_wall_minutes"] and rs["budgets"]["wall_seconds"] > c["max_wall_minutes"] * 60:
                rs["state"] = "paused"
                rs["history"].append({"ts": now_iso(), "event": "paused", "why": "wall-time budget exhausted"})
                out["events"].append(f"{st['plan_key']}: wall-time budget exhausted; paused")
            save_run(p, rs)
    return out


def _plan_finished(p: Project, st: Dict[str, Any], rs: Dict[str, Any]) -> bool:
    live = [k for k in st["tasks"] if k not in (st.get("retired") or {})]
    return bool(live) and all((rs["tasks"].get(st["tasks"][k]) or {}).get("phase") == "verified" for k in live)


def _flat(st: Dict[str, Any], tid: str) -> Dict[str, Any]:
    key = next((k for k, v in st["tasks"].items() if v == tid), None)
    return (st.get("spec_tasks") or {}).get(key or "", {}) | {"_key": key}


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
        deps = task.get("depends_on") or []
        if any(p.get("task", d)["status"] != "done" for d in deps if p.exists("task", d)):
            e["phase"] = "waiting"
            continue
        spec = _flat(st, tid)
        if (spec.get("execution") or {}).get("requires_approval") and not rs["approvals"].get(tid):
            e["phase"] = "awaiting_approval"
            e["reason"] = "waiting for your approval to start (requires_approval)"
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
        except PolicyBlocked as ex:
            _block(p, st, rs, tid, f"policy: {ex}", out)
        except ResearchError as ex:
            e["phase"] = "attention"
            e["reason"] = f"could not launch: {ex}"
            out["events"].append(f"{tid}: could not launch: {ex}")


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
    if kind == "repair" and e["attempts"] and e["attempts"][-1].get("diagnostics_extra"):
        diagnostics = (diagnostics or []) + e["attempts"][-1]["diagnostics_extra"]
    # reserve first: a crash after this point is reconciled from the attempt ledger, never double-launched
    task = p.get("task", tid)
    if task["status"] in ("todo", "blocked"):
        P.update_task(p, tid, status="running", claimed_by="supervisor", claimed_at=now_iso(),
                      started_at=task.get("started_at") or now_iso())
    attempt = {"n": n, "kind": kind, "execution": None, "started": now_iso(), "state": "reserved", "profile": ag.get("profile"),
               "role": role}
    e["attempts"].append(attempt)
    e["phase"] = "running"
    e["reason"] = None
    rs["budgets"]["attempts"] += 1
    save_run(p, rs)
    workspace = None
    try:
        text, sources = taskprompt.assemble(p, tid, st, e, kind, diagnostics, None, c)
        kw: Dict[str, Any] = dict(role=role, target=tid, profile=ag.get("profile"), model=ag.get("model"), instructions=text, headless=True,
                                  create_workspace=(spec.get("execution") or {}).get("workspace", "experiment") == "experiment")
        if ag.get("level"):
            kw["level"] = ag["level"]
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
        workspace = (d.get("allocation") or {}).get("workspace")
        text2 = text
        sid = None
        if kind in ("repair", "resume") and role != "verifier" and c["session_resume"] != "never":
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
        if sid and a.get("role") == role and a.get("profile") == prof and a.get("state") in ("done",):
            cmd = (d.get("provider_argv") or d["argv"])[0]
            kind = pv.provider_of({"command": cmd}, os.path.basename(cmd))
            return sid if pv.native_resume_supported(kind, cmd) else None
    return None


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
                                               "Check it, then `research supervisor resolve " + tid + " --failed|--completed`")
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
    if ex["state"] == "cancelled":
        a["state"] = "cancelled"
        e.update(phase="attention", reason="the agent was cancelled; resume the plan to retry or `supervisor resolve`")
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
    """Computational runs this attempt produced: reported run ids plus runs of the task's experiment created during it."""
    ex = p.get("task", tid).get("related_experiment_id")
    ids = set(a.get("reported_runs") or [])
    rows = []
    for r in p.q("SELECT id, experiment_id, status, backend, slurm_job_id, created_at FROM runs ORDER BY id"):
        if r["id"] in ids or (ex and r["experiment_id"] == ex and r["created_at"] and a.get("started") and r["created_at"] >= a["started"]
                              and r["backend"] in ("slurm", "hpc-bundle")):
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
    diagnostics += [d for d in v.get("diagnostics", [])]
    a["verification"] = {"passed": not diagnostics and v.get("passed", False), "diagnostics": diagnostics, "ts": now_iso(),
                         "checks": f"{v.get('checks_passed', 0)}/{v.get('checks_executed', 0)}", "state": v.get("verification_state")}
    if a["verification"]["passed"]:
        _verified(p, st, rs, tid, e, a, out)
        return
    cat = _categorize(p, tid, a, diagnostics)
    a["category"] = cat
    _after_failure(p, st, rs, tid, e, a, diagnostics, cat, c, out)


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
    return "incorrect_implementation"


def _verified(p, st, rs, tid, e, a, out) -> None:
    from . import plans as P
    summary = next((r.get("summary") for r in reversed(_reports(p, tid)) if r.get("kind") == "report" and r.get("summary")), None)
    with authority(tid, "supervisor verification passed"):
        P.update_task(p, tid, status="verify")
        P.complete_task(p, tid, result=taskprompt.redact(summary) or f"verified by Research OS after attempt {a['n']}", completed_by="supervisor")
    e.update(phase="verified", reason=None)
    a["state"] = "verified"
    out["verified"].append({"task": tid, "attempt": a["n"]})
    _event(p, tid, "supervisor_verified", f"attempt {a['n']} verified: {a['verification'].get('checks')} checks")


def _after_failure(p, st, rs, tid, e, a, diagnostics, cat, c, out) -> None:
    from . import orchestrate as O
    spec = _flat(st, tid)
    max_att = int((spec.get("execution") or {}).get("max_attempts") or c["max_attempts_per_task"])
    n_fail = len(e["attempts"])
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


def retry_task(p: Project, tid: str, note: Optional[str] = None) -> Dict[str, Any]:
    return resolve(p, tid, "retry", note)


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


def propose(p: Project, tid: Optional[str], text: str, rationale: str, source: str = "agent") -> Dict[str, Any]:
    PS.validate(text, p)
    cl = classify_change(p, text)
    doc = PS.normalize(PS.parse(text), PS._known(p))
    plan_key = doc["plans"][0]["key"]
    n = len(list_proposals(p)) + 1
    pr = {"id": f"PROP-{n:04d}", "ts": now_iso(), "task": tid, "plan_key": plan_key, "rationale": taskprompt.redact(rationale), "patch": text,
          "classification": cl["classification"], "operations": cl["operations"], "conflicts": cl["conflicts"], "status": "pending", "source": source}
    st = PS.load_state(p, plan_key)
    c = cfg(p, st)
    auto = (cl["classification"] == "operational" and c["auto_apply_operational"] and not cl["conflicts"]
            and (st or {}).get("auto_tasks", 0) + sum(len(pl["tasks"]) for pl in doc["plans"]) <= int(c["max_auto_tasks"]))
    if cl["classification"] == "scientific" and not c["require_approval_for_scientific_changes"] and not cl["conflicts"]:
        auto = True
    if auto:
        _apply_proposal(p, pr, "auto (policy)")
    else:
        _prop_save(p, pr)
        p.event("plan", (st or {}).get("plan_id"), "plan_proposal", f"{pr['id']} ({cl['classification']}) proposed by {source}: {rationale[:160]}")
    return pr


def _apply_proposal(p: Project, pr: Dict[str, Any], who: str) -> None:
    res = PS.apply(p, pr["patch"], source=f"proposal:{pr['id']} {who}")
    pr.update(status="applied", decided_by=who, decided_at=now_iso(), result=res["plans"])
    st = PS.load_state(p, pr["plan_key"])
    if st and pr["classification"] == "operational":
        st["auto_tasks"] = st.get("auto_tasks", 0) + sum(1 for op in pr.get("operations", []) if op == "create_task")
        PS.save_state(p, st)
    _prop_save(p, pr)


def decide_proposal(p: Project, pid: str, approve: bool, note: Optional[str] = None) -> Dict[str, Any]:
    require_human(p, "Approving or rejecting a plan change")
    pr = _prop_load(p, pid)
    if pr["status"] != "pending":
        raise ResearchError(f"{pid} is already {pr['status']}")
    if approve:
        _apply_proposal(p, pr, "human" + (f": {note}" if note else ""))
    else:
        pr.update(status="rejected", decided_by="human", decided_at=now_iso(), note=note)
        _prop_save(p, pr)
    p.event("plan", None, "plan_proposal_" + pr["status"], f"{pid} {pr['status']}")
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
    age = time.time() - float(d.get("heartbeat", 0))
    return {"running": alive, "pid": d.get("pid"), "started_at": d.get("started_at"), "heartbeat_age_seconds": round(age, 1),
            "note": None if alive else "no such process; state is intact and a new `supervisor start` will reconcile it"}


def daemon_start(p: Project, detach: bool = True, exit_when_idle: bool = False) -> Dict[str, Any]:
    require_human(p, "Starting the supervisor")
    st = daemon_status(p)
    if st["running"]:
        return {**st, "already": True}
    argv = [sys.executable, "-m", "research.supervisor", "run", "--root", p.root] + (["--exit-when-idle"] if exit_when_idle else [])
    if not detach:
        return {"argv": argv}
    log = open(sdir(p, "daemon.log"), "ab")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=os.name == "posix", env=env)
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
    try:
        with durability.lock(lockfile, blocking=False):
            stop = {"now": False}
            signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
            signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
            started = now_iso()
            idle = 0
            while not stop["now"]:
                with _P(root, author=sysauthor, sync=False) as p:
                    _atomic_write(_daemon_path(p), json.dumps({"pid": os.getpid(), "started_at": started, "heartbeat": time.time(), "root": root}) + "\n")
                    if os.path.exists(sdir(p, "stop")):
                        break
                    try:
                        r = tick(p)
                    except Exception as ex:                       # noqa: BLE001 - the daemon must survive a bad tick and say why
                        with open(sdir(p, "daemon.log"), "a") as lf:
                            lf.write(f"{now_iso()} tick error: {type(ex).__name__}: {ex}\n")
                        r = {"plans": 1}
                    poll = float(cfg(p)["poll_seconds"])
                    active = any(x.get("state") in ("running",) for x in all_runs(p))
                idle = 0 if active else idle + 1
                if exit_when_idle and idle >= 2:
                    break
                for _ in range(int(poll * 10)):
                    if stop["now"] or os.path.exists(os.path.join(root, ".research", "supervisor", "stop")):
                        break
                    time.sleep(0.1)
    except ResearchError:
        return 3        # another daemon owns this project
    with _P(root, author=sysauthor, sync=False) as p:
        try:
            os.unlink(_daemon_path(p))
        except OSError:
            pass
    return 0


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="research.supervisor")
    ap.add_argument("action", choices=["run"])
    ap.add_argument("--root", required=True)
    ap.add_argument("--exit-when-idle", action="store_true")
    a = ap.parse_args(argv)
    return run_forever(a.root, a.exit_when_idle)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
