"""Jobs: delegate a request in words and let Research OS carry it out, with a persistent record you can inspect (`research job run`, `/do` in chat).

There is no second execution system. A job is a durable record (objective, target, steps, progress, dependencies, budget, attempts, acceptance criteria, checkpoints) over the
machinery that already exists:

    coordinator   one read-only planning agent (the same one interventions and diagnosis use: `intervene.run_assistant`) turns the request into a SMALL closed set of steps
    task step     becomes a task of a managed plan `job-NNN` (an isolated experiment worktree, the plan's agent profiles, the supervisor's verification, attempts and budgets,
                  promotion, usage-limit waits, recovery), so everything the supervisor already guarantees applies
    heal step     "unblock this paused promotion": authorizes the existing self-healing case machinery (selfheal.py) for that ONE promotion: a repair task fixes the source,
                  the candidate is rebuilt, re-tested and independently re-reviewed, integration is retried, dependents resume
    verify step   always last: a task with role `verifier` that independently checks the acceptance criteria. A job is `done` only after it verified.

The coordinator cannot reach protected things: it may only produce those step types, with roles planner/implementer/verifier/analyst/human, so it cannot edit acceptance
criteria, frozen tests, hypotheses, resources, approvals or governance. A request that needs one of those becomes `blocked` with the precise reason and the smallest human
decision. Changes to already verified work are NEW tasks (new attempts, new commits attributed to them); historical verification is never rewritten.

Persistence: every transition is a checkpoint written atomically before the next action, so a daemon restart resumes where it was. Bounded: steps, repairs and coordinator
attempts are capped; exhausted means `blocked`, never a loop. Authority: creating a job is a human act (CLI, or a chat operator); the job inherits only that.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import durability, planspec as PS, registry, yamlio
from .store import _atomic_write
from .util import ResearchError, now_iso

ACTIVE = ("planning", "running")
STEP_ROLES = PS.ROLES
_threads: Dict[str, threading.Thread] = {}

PROMPT = """You are the coordinator for a research-automation supervisor. A researcher delegated ONE request. Interpret it, look at the project state below, and decompose it into
a SMALL set of executable steps. Reply with ONE JSON object only. Do not run commands or edit files.
{{"summary":"one sentence","acceptance":["a concrete, checkable end state", "..."],
  "steps":[ {{"type":"heal_promotion","plan":"<plan key>","item":"AP-002"}}   (unblock a PAUSED automatic promotion: its source is repaired and the gates rerun)
          | {{"type":"task","key":"short-key","title":"...","prompt":"what the agent must do","role":"implementer|analyst|verifier|planner|human","depends_on":["key"],"checks":["file:path","cmd:command"]}} ],
  "needs_human":null}}
Rules: at most {max_steps} steps. Never change scientific acceptance criteria, thresholds, hypotheses, frozen tests, datasets, approvals, permissions or governance, and never invent results;
if the request needs one of those, reply with "steps":[] and "needs_human":{{"reason":"precisely why","decision":"the smallest decision the researcher must make"}}.
Give every task checks that prove it. The supervisor appends an independent verification step itself.

REQUEST:
{request}

PROJECT STATE (JSON):
{context}
"""


# ----------------------------------------------------------------------------------------------- storage
def _dir(p) -> str:
    d = p.path("supervisor", "jobs")
    os.makedirs(d, exist_ok=True)
    return d


def _file(p, jid: str) -> str:
    return os.path.join(_dir(p), f"{jid}.json")


def load(p, jid: str) -> Dict[str, Any]:
    try:
        with open(_file(p, jid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise ResearchError(f"No job {jid}", hint="`research job list`")


def save(p, j: Dict[str, Any], note: Optional[str] = None) -> None:
    j["updated"] = now_iso()
    if note:
        j["checkpoints"].append({"ts": j["updated"], "state": j["state"], "note": note})   # a checkpoint is written with every transition, before the next action
    _atomic_write(_file(p, j["id"]), json.dumps(j, indent=1, sort_keys=True, default=str) + "\n")


def list_all(p, active_only: bool = False) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(os.listdir(_dir(p))):
        if f.startswith("JOB-") and f.endswith(".json"):
            try:
                j = load(p, f[:-5])
            except ResearchError:
                continue
            if not active_only or j["state"] in ACTIVE:
                out.append(j)
    return out


def create(p, request: str, source: str = "cli", budget: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """A human delegates a request. Safe to call from several processes: the id is allocated under a lock."""
    from . import supervisor as SV
    SV.require_human(p, "Delegating a job")
    if not (request or "").strip():
        raise ResearchError("A job needs a request in words")
    with durability.lock(os.path.join(_dir(p), ".lock")):
        n = max([int(j["id"][4:]) for j in list_all(p)] + [0]) + 1
        by = (p.author or {}).get("author_name") or os.environ.get("RESEARCH_AUTHOR") or "human"
        c = registry.section(p, "supervisor_assistant")
        j = {"id": f"JOB-{n:03d}", "objective": request.strip(), "source": source, "requested_by": by, "created": now_iso(), "state": "planning", "target": None, "plan_key": None,
             "steps": [], "acceptance": [], "progress": "0/0", "dependencies": {}, "attempts": 0, "history": [], "checkpoints": [], "result": None, "blocked": None,
             "budget": {"max_steps": 8, "max_repairs": int(c.get("max_repairs") or 2), "max_planning_attempts": 2, "repairs": 0, **(budget or {})}}
        save(p, j, f"delegated by {by} via {source}")
    _alert(p, j, "job", f"{j['id']} started: {j['objective'][:160]}")
    return j


# ----------------------------------------------------------------------------------------------- the coordinator
def _context(p) -> Dict[str, Any]:
    from . import autopromote as AP, supervisor as SV
    plans = []
    for rs in SV.all_runs(p):
        st = PS.load_state(p, rs["plan_key"])
        if not st:
            continue
        h = SV.plan_health(p, st, rs)
        plans.append({"plan": rs["plan_key"], "state": rs.get("state"), "health": h.get("state"), "blocking": [{"task": b["task"], "phase": b["phase"], "reason": str(b.get("reason"))[:200]} for b in h.get("blocking", [])][:6],
                      "promotions": [{"id": i["id"], "experiment": i["experiment"], "status": i["status"], "error": str(i.get("error"))[:300]} for i in (rs.get("autopromote") or {}).get("items", []) if i["status"] not in AP.TERMINAL]})
    return {"plans": plans}


def validate_plan(raw: Any, max_steps: int) -> Dict[str, Any]:
    """The coordinator's output is untrusted text: only the closed vocabulary below is accepted, so it cannot touch criteria, tests, approvals or governance."""
    if not isinstance(raw, dict):
        raise ResearchError("the coordinator returned no plan")
    nh = raw.get("needs_human")
    if nh:
        return {"summary": str(raw.get("summary") or "")[:400], "acceptance": [], "steps": [], "needs_human": {"reason": str(nh.get("reason") or "a decision is needed")[:500], "decision": str(nh.get("decision") or "")[:300]}}
    steps, keys = [], set()
    for s in raw.get("steps") or []:
        if not isinstance(s, dict):
            raise ResearchError("a step is not an object")
        if s.get("type") == "heal_promotion":
            if not s.get("plan") or not s.get("item"):
                raise ResearchError("heal_promotion needs plan and item")
            steps.append({"type": "heal_promotion", "plan": str(s["plan"]), "item": str(s["item"]), "status": "pending"})
        elif s.get("type") == "task":
            k = str(s.get("key") or "").strip()
            if not k or k in keys or not s.get("title") or not s.get("prompt"):
                raise ResearchError("a task step needs a unique key, a title and a prompt")
            if (s.get("role") or "implementer") not in STEP_ROLES:
                raise ResearchError(f"task {k}: role must be one of {', '.join(STEP_ROLES)}")
            keys.add(k)
            steps.append({"type": "task", "key": k, "title": str(s["title"])[:120], "prompt": str(s["prompt"])[:6000], "role": s.get("role") or "implementer",
                          "depends_on": [d for d in (s.get("depends_on") or []) if isinstance(d, str)], "checks": [c for c in (s.get("checks") or []) if isinstance(c, str)], "status": "pending"})
        else:
            raise ResearchError(f"unknown step type {s.get('type')!r}; allowed: task, heal_promotion")
    bad = [d for s in steps if s["type"] == "task" for d in s["depends_on"] if d not in keys]
    if bad:
        raise ResearchError("unknown dependency " + ", ".join(bad))
    if not steps or len(steps) > max_steps:
        raise ResearchError(f"the plan has {len(steps)} steps; between 1 and {max_steps} are allowed")
    return {"summary": str(raw.get("summary") or "")[:400], "acceptance": [str(a)[:300] for a in (raw.get("acceptance") or [])][:8], "steps": steps, "needs_human": None}


def _plan_worker(root: str, jid: str) -> None:
    from .store import Project
    from . import intervene as I
    try:
        with Project(root, sync=False, author={"author_type": "system", "author_name": "supervisor", "author_model": None}) as p:
            j = load(p, jid)
            try:
                raw = I._json_from(I.run_assistant(p, f"Coordinate {jid}", PROMPT.format(max_steps=j["budget"]["max_steps"], request=j["objective"], context=json.dumps(_context(p), default=str, indent=1)[:20000])))
                plan = validate_plan(raw, j["budget"]["max_steps"])
                err = None
            except I.ProviderLimited as ex:       # a usage limit is a wait, not a failure: the same planning runs again after the reset
                j = load(p, jid)
                j["wait_until"] = ex.until if ex.waitable else None
                j["planning_error"] = str(ex) if ex.waitable else None
                if ex.waitable:
                    j["planner_started"] = None
                    save(p, j, f"coordinator waits for the provider limit: {ex}")
                    return
                err = str(ex)
            except (ResearchError, ValueError) as ex:
                err = str(ex)
            j = load(p, jid)
            if j["state"] != "planning":
                return
            j["planner_started"] = None
            if err:
                j["planning_error"] = err
                save(p, j, f"coordinator failed: {err[:200]}")
                return
            if plan["needs_human"]:
                return _block(p, j, plan["needs_human"]["reason"], plan["needs_human"]["decision"])
            j.update(steps=plan["steps"], acceptance=plan["acceptance"], summary=plan["summary"], planning_error=None)
            save(p, j, f"coordinator planned {len(plan['steps'])} step(s)")
            _materialize(p, j)
    except Exception:   # noqa: BLE001 - a coordinator thread must never take the supervisor down; the job keeps its checkpoint and is re-planned within its budget
        pass


def _materialize(p, j: Dict[str, Any]) -> None:
    """Steps become the existing machinery: task steps a managed plan (isolated experiment worktree); heal steps authorize the self-healing case for that promotion."""
    from . import supervisor as SV
    tasks = [s for s in j["steps"] if s["type"] == "task"]
    heals = [s for s in j["steps"] if s["type"] == "heal_promotion"]
    for s in heals:                                        # recorded before anything runs: the supervisor's tick drives it
        j["target"] = {"kind": "promotion", "plan": s["plan"], "item": s["item"]}
    if tasks:
        checks = list(dict.fromkeys(c for t in tasks for c in t["checks"]))
        if not checks:                                     # success must be checkable: a verifier with nothing to run proves nothing
            return _block(p, j, "none of the planned steps has a deterministic check, so completion could not be independently verified",
                          "Say how the result can be checked (a file, a command, a metric), or approve a verifier that only reports")
        key = f"job-{j['id'][4:].lower()}"
        j["plan_key"] = key
        ver = {"key": "verify", "title": f"Independently verify {j['id']}", "role": "verifier", "depends_on": [t["key"] for t in tasks], "status": "pending", "type": "task", "checks": checks,
               "prompt": "Independently verify that the job is complete. Acceptance criteria:\n" + "\n".join(f"- {a}" for a in j["acceptance"]) + "\nInspect the results yourself; do not trust earlier agents. "
                         "Report precisely what you verified. Do not edit files."}
        spec = [{"key": t["key"], "title": t["title"], "prompt": t["prompt"], "depends_on": t["depends_on"], **({"checks": t["checks"]} if t["checks"] else {}),
                 "agent": {"role": t["role"]}, "execution": {"max_attempts": 2}} for t in tasks + [ver]]
        doc = {"schema_version": 1, "plans": [{"key": key, "title": f"Job {j['id']}: {j['objective'][:60]}", "objective": j["objective"], "execution": {"max_attempts_per_task": 2},
                                              "experiments": [{"key": "job", "title": f"{j['id']} (engineering job)", "hypothesis": "An engineering job; no scientific claim is made or tested.", "tasks": spec}]}]}
        j["steps"].append(ver)
        PS.apply(p, yamlio.dump(doc, block=True), source="job", author_note=f"{j['id']} (delegated by {j['requested_by']}; created by the coordinator)")
        SV.plan_run(p, key, start_daemon=False)
    j["state"] = "running"
    save(p, j, "materialized: " + (f"plan {j['plan_key']}" if tasks else "") + (" + " if tasks and heals else "") + (f"authorizing the repair of {heals[0]['item']}" if heals else ""))


# ----------------------------------------------------------------------------------------------- the lifecycle (called from the supervisor tick)
def step(p, out: Optional[Dict[str, Any]] = None) -> None:
    """One bounded pass over active jobs. Never raises; every decision is a checkpoint."""
    for j in list_all(p, active_only=True):
        try:
            _advance(p, j, out)
        except Exception as ex:   # noqa: BLE001
            j = load(p, j["id"])
            j["history"].append({"ts": now_iso(), "error": f"{type(ex).__name__}: {ex}"[:300]})
            save(p, j)


def _advance(p, j: Dict[str, Any], out) -> None:
    if j["state"] == "planning":
        t = _threads.get(j["id"])
        if (t and t.is_alive()) or float(j.get("wait_until") or 0) > time.time():
            return
        if j.get("planning_error") and j["attempts"] >= j["budget"]["max_planning_attempts"]:
            return _block(p, j, f"the coordinator could not produce a valid plan after {j['attempts']} attempts: {j['planning_error']}", "Rephrase the request, or split it into smaller ones")
        if j.get("steps"):
            return _materialize(p, j)                      # a restart between planning and materialising: the plan is already checkpointed
        if not j.get("planner_started") or time.time() - float(j["planner_started"]) > 1200:
            j["attempts"] += 1 if not j.get("wait_until") else 0
            j["planner_started"], j["wait_until"] = time.time(), None
            save(p, j, f"coordinator attempt {j['attempts']}")
            th = threading.Thread(target=_plan_worker, args=(p.root, j["id"]), daemon=True)
            _threads[j["id"]] = th
            th.start()
        return
    from . import selfheal as SH, supervisor as SV
    steps = j["steps"]
    for s in steps:
        if s["type"] == "heal_promotion" and s["status"] != "done":
            _drive_heal(p, j, s)
            j = load(p, j["id"])
            if j["state"] != "running":
                return
    st = PS.load_state(p, j["plan_key"]) if j.get("plan_key") else None
    rs = SV.load_run(p, st["plan_id"]) if st else None
    done = 0
    if rs:
        for s in steps:
            if s["type"] == "task":
                tid = st["tasks"].get(s["key"])
                ph = ((rs.get("tasks") or {}).get(tid) or {}).get("phase")
                s["status"], s["task"] = ("done" if ph == "verified" else "blocked" if ph in ("blocked", "attention") else "running" if ph in ("running", "verifying", "waiting_hpc", "repair_pending") else "pending"), tid
    done = sum(1 for s in steps if s["status"] == "done")
    j["progress"] = f"{done}/{len(steps)}"
    if done == len(steps):
        j.update(state="done", result={"verified_by": next((s.get("task") for s in steps if s.get("key") == "verify"), None), "at": now_iso()})
        save(p, j, "every step done and independently verified")
        return _alert(p, j, "job", f"{j['id']} is done and was independently verified: {j.get('summary') or j['objective'][:120]}")
    blocked = [s for s in steps if s["status"] == "blocked"]
    if blocked and rs:
        h = SV.plan_health(p, st, rs)
        if h["state"] == "halted":
            b = next((x for x in h["blocking"] if x["task"] == blocked[0].get("task")), h["blocking"][0] if h["blocking"] else {})
            return _block(p, j, f"step {blocked[0].get('key')} ({blocked[0].get('task')}) stopped and could not be repaired within its budget: {str(b.get('reason'))[:300]}",
                          "Decide whether to raise its attempt budget (research task intervene), change the approach, or cancel the job")
    save(p, j)


def _drive_heal(p, j: Dict[str, Any], s: Dict[str, Any]) -> None:
    """Authorize the existing self-healing case for THIS promotion, then watch it: recovered (and integrated) is done; escalated is the precise blocker."""
    from . import planspec as PSM, selfheal as SH, supervisor as SV
    st = PSM.load_state(p, s["plan"])
    if not st:
        return _block(p, j, f"no plan {s['plan']!r}", "Name an existing plan")
    with SV._lock(p):
        rs = SV.load_run(p, st["plan_id"]) or {}
        item = next((i for i in (rs.get("autopromote") or {}).get("items", []) if i["id"] == s["item"]), None)
        if not item:
            return _block(p, j, f"{s['plan']} has no promotion {s['item']}", "Name an existing automatic promotion")
        SH.authorize_item(rs, s["item"], j["id"])
        SV.save_run(p, rs)
        case = ((rs.get("selfheal") or {}).get("cases") or {}).get(f"promotion:{s['item']}") or {}
        if item["status"] == "integrated" and case.get("phase") in (None, "recovered"):
            h = SV.plan_health(p, st, rs)
            if h["state"] in ("progressing", "completed", "idle", "waiting_for_provider"):
                s["status"] = "done"
                s["evidence"] = {"integrated": item.get("main_after"), "repair_task": case.get("task_key"), "review": (item.get("review") or {}).get("by")}
        elif case.get("phase") == "escalated":
            j = load(p, j["id"])
            return _block(p, j, f"{s['item']} could not be repaired automatically: {case.get('reason')}", "Decide the protected or scientific matter named above (or fix it yourself), then `research plan autopromote " + s["plan"] + f" retry --item {s['item']}`")
    j2 = load(p, j["id"])
    for x in j2["steps"]:
        if x.get("type") == "heal_promotion" and x["item"] == s["item"]:
            x.update(status=s["status"], **({"evidence": s["evidence"]} if s.get("evidence") else {}))
    j2["progress"] = f"{sum(1 for x in j2['steps'] if x['status'] == 'done')}/{len(j2['steps'])}"
    save(p, j2, f"{s['item']}: recovered and integrated" if s["status"] == "done" else None)
    if s["status"] == "done" and all(x["status"] == "done" for x in j2["steps"]):
        j2.update(state="done", result={"promotion": s["item"], "evidence": s.get("evidence"), "at": now_iso()})
        save(p, j2, f"{s['item']} integrated and the plan is moving again")
        _alert(p, j2, "job", f"{j2['id']} is done: {s['item']} was repaired, re-verified and integrated; downstream work is moving again.")


def _block(p, j: Dict[str, Any], reason: str, decision: str) -> None:
    j.update(state="blocked", blocked={"reason": reason, "decision": decision, "at": now_iso()})
    save(p, j, "blocked: " + reason[:200])
    _alert(p, j, "halt", f"{j['id']} is blocked: {reason}\nSmallest decision needed from you: {decision}")


def cancel(p, jid: str) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Cancelling a job")
    j = load(p, jid)
    if j["state"] not in ACTIVE:
        raise ResearchError(f"{jid} is {j['state']}; only a running job can be cancelled")
    j["state"] = "cancelled"
    save(p, j, f"cancelled by {(p.author or {}).get('author_name') or 'human'}")
    if j.get("plan_key") and PS.load_state(p, j["plan_key"]):
        try:
            SV.plan_stop(p, j["plan_key"], cancel_agents=True)
        except ResearchError:
            pass
    return j


def authorizes(p, plan_key: str) -> bool:
    """Does an active job own this plan? (Its tasks may be retried by the automatic diagnosis within their own budgets.)"""
    return any(j["state"] in ACTIVE and j.get("plan_key") == plan_key for j in list_all(p, True))


def _alert(p, j: Dict[str, Any], kind: str, text: str) -> None:
    try:
        from . import notify
        if notify.chat_enabled(p):
            from .chat import alerts
            alerts.raise_(p, f"job:{j['id']}:{j['state']}", kind, "error" if kind == "halt" else "ok", text, once=True)
    except Exception:   # noqa: BLE001
        pass


def logs(p, jid: str, tail: int = 1500) -> Dict[str, Any]:
    """Everything that happened: the job's checkpoints and, per task step, its attempts and the latest agent output."""
    from . import execution as E, supervisor as SV
    j = load(p, jid)
    rows = []
    st = PS.load_state(p, j["plan_key"]) if j.get("plan_key") else None
    rs = SV.load_run(p, st["plan_id"]) if st else None
    for s in j["steps"]:
        row = {"step": s.get("key") or s.get("item"), "type": s["type"], "status": s["status"], "task": s.get("task")}
        e = ((rs or {}).get("tasks") or {}).get(s.get("task") or "") or {}
        row["attempts"] = [{"n": a["n"], "state": a.get("state"), "profile": a.get("profile"), "verified": (a.get("verification") or {}).get("passed")} for a in e.get("attempts", [])]
        if e.get("attempts") and e["attempts"][-1].get("execution"):
            try:
                lg = E.logs(p, e["attempts"][-1]["execution"], tail * 2)
                row["output"] = (lg.get("out") or "")[-tail:]
            except ResearchError:
                pass
        rows.append(row)
    return {"job": j["id"], "state": j["state"], "checkpoints": j["checkpoints"], "history": j["history"], "steps": rows}
