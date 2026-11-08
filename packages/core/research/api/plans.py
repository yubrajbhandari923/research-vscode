"""Plans, tasks, the supervisor, interventions, proposals, governance, executions, dispatch, promotion."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from .. import plans as P
from ..schema import normalize_id
from ..util import ResearchError
from . import op


# tasks -----------------------------------------------------------------------------------------------
@op()
def task_context(p, id: str) -> Dict[str, Any]:
    return P.task_context(p, id)


@op()
def task_brief(p, id: str) -> str:
    """The task context as Markdown (what an agent reads)."""
    from .. import agents as AG
    return AG.task_brief_md(P.task_context(p, id))


@op(mutates=True, mcp="task_release")
def release_task(p, id: str, note: Optional[str] = None) -> Dict[str, Any]:
    return P.release_task(p, id, note)


@op(mutates=True, mcp="task_note")
def task_note(p, id: str, text: str) -> Dict[str, Any]:
    return P.add_task_note(p, id, text)


@op(mutates=True)
def verify_task(p, id: str) -> Dict[str, Any]:
    from .. import verification as VF
    return VF.verify_task(p, id)


@op(mutates=True, mcp="finding_review")
def review_finding(p, id: str, verdict: str, notes: Optional[str] = None, confidence: Optional[str] = None) -> Dict[str, Any]:
    from .. import verification as VF
    return VF.review_finding(p, id, verdict, notes, confidence)


@op(sync=False, mutates=True)
def task_pin(p, id: str, level: Optional[str] = None, profile: Optional[str] = None, clear: bool = False) -> Dict[str, Any]:
    from .. import orchestrate as O
    return O.set_override(p, id, level, profile, None, clear)


@op(mutates=True)
def freeze_tests(p, task: str, paths: Any) -> Dict[str, Any]:
    from .. import orchestrate as O
    return O.freeze(p, task, paths)


# plan yaml -------------------------------------------------------------------------------------------
@op(needs_project=False)
def plan_yaml_template(s) -> Dict[str, Any]:
    from .. import planspec as PS
    return {"text": PS.TEMPLATE}


@op()
def plan_yaml_validate(p, text: str) -> Dict[str, Any]:
    from .. import planspec as PS
    return PS.validate(text, p)


@op()
def plan_yaml_diff(p, text: str) -> Dict[str, Any]:
    from .. import planspec as PS
    return PS.diff(p, text)


@op(mutates=True)
def plan_yaml_apply(p, text: str, dry_run: bool = False) -> Dict[str, Any]:
    from .. import planspec as PS
    return PS.apply(p, text, dry_run=dry_run, source="ui")


@op()
def plan_yaml_export(p, plan: str, experiment: Optional[str] = None, task: Optional[str] = None, partial: bool = False) -> Dict[str, Any]:
    from .. import planspec as PS
    return {"text": PS.export(p, plan, scope="partial" if partial else "full", experiment=experiment, task=task)}


@op(sync=False)
def managed_plans(p) -> List[Dict[str, Any]]:
    """Plans that came through YAML, with their run state: the Control Center's Plans page."""
    from .. import planspec as PS, supervisor as SV
    out = []
    for st in PS.all_states(p):
        try:
            s = SV.status(p, st["plan_key"])
            out.append({"key": st["plan_key"], "id": st["plan_id"], "revision": st["revision"], "title": (st.get("spec") or {}).get("title"),
                        "state": s["state"], "health": s["health"], "progress": s["progress"], "counts": s["counts"], "pending_proposals": len(s["proposals"])})
        except ResearchError:
            continue
    return out


# supervisor ------------------------------------------------------------------------------------------
@op(sync=False, mcp="plan_status")
def supervisor_status(p, plan: str) -> Dict[str, Any]:
    from .. import supervisor as SV
    return SV.status(p, plan)


@op(sync=False, mutates=True)
def supervisor_daemon(p, action: str = "status") -> Dict[str, Any]:
    from .. import supervisor as SV
    if action == "start":
        return SV.daemon_start(p, detach=True)
    if action == "stop":
        return SV.daemon_stop(p)
    if action == "reload":
        return SV.daemon_reload(p)
    if action == "restart":
        return SV.daemon_restart(p)
    return SV.daemon_status(p)


@op(mutates=True)
def plan_control(p, plan: str, action: str, cancel_agents: bool = False) -> Dict[str, Any]:
    from .. import supervisor as SV
    fn = {"run": lambda: SV.plan_run(p, plan), "pause": lambda: SV.plan_pause(p, plan), "resume": lambda: SV.plan_resume(p, plan),
          "stop": lambda: SV.plan_stop(p, plan, cancel_agents)}.get(action)
    if not fn:
        raise ResearchError(f"unknown plan action {action!r}")
    return fn()


@op(mutates=True)
def supervisor_task(p, task: str, action: str, note: Optional[str] = None, reason: Optional[str] = None,
                    attempts: Optional[List[int]] = None) -> Dict[str, Any]:
    from .. import supervisor as SV
    if action == "approve":
        return SV.approve_task(p, task, note)
    if action == "accept":
        return SV.accept(p, task, reason or "")
    if action == "void":
        return SV.void_attempts(p, task, note or reason or "attempts voided by a human", attempts)
    if action == "continue":
        return SV.continue_with(p, task, reason or "", note)
    if action == "wait":       # a provider-limit decision request: wait for the reset however far away it is
        return SV.wait_for_limit(p, task)
    if action in ("failed", "completed", "retry", "unblock"):
        return SV.resolve(p, task, action, note)
    raise ResearchError(f"unknown task action {action!r}")


@op(sync=False)
def provider_status(p, task: Optional[str] = None) -> Dict[str, Any]:
    from .. import supervisor as SV
    return SV.provider_status(p, task)


@op(sync=False)
def proposals(p) -> List[Dict[str, Any]]:
    from .. import supervisor as SV
    return SV.list_proposals(p)


@op(mutates=True)
def proposal_decide(p, id: str, approve: Any, note: Optional[str] = None, pin: Optional[str] = None, rev: Optional[int] = None) -> Dict[str, Any]:
    """approve: true | false | "revise". Approving needs the PIN (when one is set)."""
    from .. import supervisor as SV
    return SV.decide_proposal(p, id, approve, note, pin, rev)


@op(sync=False)
def governance(p) -> Dict[str, Any]:
    from .. import approval, governance as G, supervisor as SV
    return {"policy": G.cfg(p), "pin": approval.status(), "proposals": SV.list_proposals(p)}


@op(mutates=True, needs_project=False)
def approval_set_pin(s, new: str, old: Optional[str] = None) -> Dict[str, Any]:
    from .. import approval
    if approval.agent_environment() and not old and approval.is_set():
        raise ResearchError("Changing the PIN needs the current PIN")
    approval.set_pin(new, old)
    return approval.status()


@op(sync=False)
def attempt_detail(p, task: str, attempt: int = 0) -> Dict[str, Any]:
    """One attempt: its exact prompt, context sources, verification, reports and log tails."""
    from .. import execution as E, planspec as PS, supervisor as SV
    hit = PS.find_task_key(p, task)
    rs = hit and SV.load_run(p, hit[0]["plan_id"])
    atts = ((rs or {}).get("tasks", {}).get(task) or {}).get("attempts") or []
    if not atts:
        return {"task": task, "attempts": []}
    a = atts[attempt - 1] if attempt else atts[-1]
    prompt = None
    if a.get("prompt_path"):
        try:
            prompt = open(p.abspath(a["prompt_path"]), encoding="utf-8").read()
        except OSError:
            prompt = None
    logs = None
    if a.get("execution"):
        try:
            logs = E.logs(p, a["execution"], 6000)
        except (OSError, ResearchError):
            logs = None
    return {"task": task, "attempt": a, "attempts": len(atts), "prompt": prompt, "logs": logs, "reports": SV._reports(p, task, a.get("execution"))[-40:]}


@op(mutates=True)
def plan_promote(p, plan: str, experiment: Optional[str] = None) -> Dict[str, Any]:
    from .. import supervisor as SV
    return SV.promote_plan(p, plan, experiment)


@op(sync=False)
def promotion_hint(p, plan: str) -> Dict[str, Any]:
    from .. import planspec as PS, workspace as W
    st = PS._state_for(p, plan)
    out = []
    for ek, eid in (st.get("experiments") or {}).items():
        try:
            for ws in W.list_workspaces(p):
                if ws.get("experiment") == eid:
                    out.append({"experiment": eid, "workspace": ws["id"], "path": ws.get("path"), "branch": ws.get("branch"), "ahead": ws.get("commits_ahead"),
                                "uncommitted": ws.get("dirty") or ws.get("untracked")})
        except ResearchError:
            continue
    return {"plan": plan, "workspaces": out}


# interventions, halt diagnosis, Telegram ------------------------------------------------------------
@op(sync=False)
def interventions(p, task: Optional[str] = None) -> List[Dict[str, Any]]:
    from .. import intervene as I
    return I.list_all(p, task)


@op(mutates=True)
def intervention_request(p, task: str, message: str, interrupt: bool = False) -> Dict[str, Any]:
    from .. import intervene as I
    return I.request(p, task, message, interrupt=interrupt, source="vscode")


@op(mutates=True)
def intervention_action(p, id: str, action: str, note: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
    from .. import intervene as I
    if action == "approve":
        return I.approve(p, id, note, pin)
    if action == "reject":
        return I.reject(p, id, note)
    if action == "cancel":
        return I.cancel(p, id)
    if action == "plan":
        return I.plan_it(p, id)
    raise ResearchError("action must be approve, reject, cancel or plan")


@op(mutates=True)
def debug_run(p, plan: str) -> Dict[str, Any]:
    from .. import notify
    return notify.run_debug(p, plan)


@op(sync=False)
def debug_latest(p) -> Optional[Dict[str, Any]]:
    from .. import notify
    return notify.latest_debug(p)


# dispatch and executions -----------------------------------------------------------------------------
@op(mutates=True)
def dispatch(p, role: Optional[str] = None, target: Optional[str] = None, objective: Optional[str] = None,
             profile: Optional[str] = None, model: Optional[str] = None, instructions: Optional[str] = None, headless: bool = False,
             effort: Optional[str] = None, strategy: Optional[str] = None, level: Optional[str] = None,
             create_workspace: bool = False, allow_unfrozen: Optional[str] = None) -> Dict[str, Any]:
    """PREPARE only: resolves everything and writes the brief. Nothing runs until `execution_start` / the wrapper is run."""
    from .. import agents as AG
    d = AG.dispatch(p, role, target, objective, profile, model, instructions, headless=headless, effort=effort,
                    strategy=strategy, level=level, create_workspace=create_workspace, allow_unfrozen=allow_unfrozen)
    d["shell"] = None      # never ship a copy-pasteable string built from task text
    return d


@op(sync=False)
def execution_start(p, id: str) -> Dict[str, Any]:
    """Start a prepared headless execution detached from the UI. Returns at once; poll `execution_show` for `running`."""
    import subprocess
    from .. import execution as EX
    r = EX.show(p, id)
    if r["state"] != "prepared" or r.get("mode") != "headless":
        raise ResearchError(f"Only a prepared headless execution can be started here (this one is {r['state']}/{r.get('mode')})")
    subprocess.Popen([sys.executable, os.path.abspath(EX.__file__), p.root, id], cwd=r.get("cwd") or p.root,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"id": id, "state": "starting"}


@op(sync=False)
def executions(p, task: Optional[str] = None) -> List[Dict[str, Any]]:
    from .. import execution as EX
    return EX.list_all(p, task)


@op(sync=False)
def execution_show(p, id: str) -> Dict[str, Any]:
    from .. import execution as EX
    return EX.show(p, id)


@op(sync=False)
def execution_logs(p, id: str) -> Dict[str, Any]:
    from .. import execution as EX
    return EX.logs(p, id)


@op()
def execution_stop(p, id: str) -> Dict[str, Any]:
    from .. import execution as EX
    return EX.stop(p, id)


@op()
def execution_retry(p, id: str, profile: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
    from .. import agents as AG
    d = AG.redispatch(p, id, profile, model)
    d["shell"] = None
    return d


# promotion -------------------------------------------------------------------------------------------
@op(sync=False)
def promotions(p, closed: bool = False) -> List[Dict[str, Any]]:
    from .. import promotion
    return promotion.list_candidates(p, closed)


@op(sync=False)
def promotion_diff(p, id: str) -> Dict[str, Any]:
    from .. import promotion
    return promotion.diff(p, id)


@op()
def promotion_action(p, id: str, action: str, note: Optional[str] = None, confirm: bool = False,
                     verdict: Optional[str] = None, command: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
    """verify | review | approve | integrate | abandon. The core refuses agents and unconfirmed integration."""
    from .. import promotion as PR
    if action == "verify":
        return PR.verify(p, id, command)
    if action == "review":
        return PR.review(p, id, verdict or "comment", note)
    if action == "approve":
        return PR.approve(p, id, note, pin)
    if action == "integrate":
        return PR.integrate(p, id, confirm, pin)
    if action == "abandon":
        return PR.abandon(p, id, note)
    raise ResearchError(f"unknown promotion action {action!r}")
