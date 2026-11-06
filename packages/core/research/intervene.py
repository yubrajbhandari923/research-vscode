"""Supervisor intervention: a researcher steers a stuck or misdirected task in natural language (opt-in; nothing changes unless you ask).

    requested -> proposed -> approved -> implementing -> verifying -> resolved        (+ rejected | failed | cancelled)

1. `request`  gathers the task's context (contract, attempts, diagnostics, logs, evidence, plan) and asks a planning agent to turn the
              instruction into a small set of concrete actions (JSON, validated; anything else fails closed).
2. `proposed` every action is classified: operational (unblock/retry/reverify), implementation (guidance, prompt edit, new task) are applied
              under the researcher's own authority; scientific (acceptance, checks, expected artifacts, inputs, datasets, dependencies),
              resource (cpus/mem/time/partition/retry budget) and frozen-test changes are PROTECTED and need ONE consolidated human approval (PIN).
3. `approved` the researcher's decision is recorded (who, why, when); protected actions can never be approved by an agent or over chat.
4. `implementing`/`verifying` the supervisor (tick) applies the actions idempotently through the existing APIs (plan YAML reconciliation, resolve,
              void) and the normal supervised attempt runs with the researcher's instruction in its brief. Nothing is verified by the intervention
              itself: the task is resolved only when the supervisor verifies it, against the (possibly amended) contract.

Original evidence is never touched: failed attempts, their diagnostics and verification results stay in the run state; an amended contract
records its before/after, the rationale, the instruction and who approved it (also as a Decision).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from . import planspec as PS
from .util import PolicyBlocked, ResearchError, now_iso

OPEN = ("requested", "proposed", "approved", "implementing", "verifying")
TERMINAL = ("resolved", "rejected", "failed", "cancelled")
SCIENTIFIC_FIELDS = {"acceptance", "checks", "expected_artifacts", "datasets", "inputs", "depends_on", "agent"}   # agent: a role change can weaken independent review
IMPLEMENTATION_FIELDS = {"prompt", "title", "description", "notes"}
RESOURCE_FIELDS = {"cpus", "mem", "time", "partition", "account", "gpus", "backend", "cluster"}
ACTION_TYPES = ("unblock", "retry", "reverify", "guide", "amend_task", "add_task", "set_resources", "set_max_attempts", "change_frozen_test", "note")
MAX_ACTIONS = 8


# ----------------------------------------------------------------------------------------------- storage
def _dir(p) -> str:
    d = p.path("supervisor", "interventions")
    os.makedirs(d, exist_ok=True)
    return d


def _file(p, iid: str) -> str:
    if not re.fullmatch(r"INT-\d{3,}", iid or ""):
        raise ResearchError(f"Bad intervention id {iid!r}")
    return os.path.join(_dir(p), iid + ".json")


def load(p, iid: str) -> Dict[str, Any]:
    try:
        with open(_file(p, iid), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ResearchError(f"No intervention {iid}", hint="`research intervention list`")


def save(p, it: Dict[str, Any]) -> None:
    from .store import _atomic_write
    it["updated"] = now_iso()
    _atomic_write(_file(p, it["id"]), json.dumps(it, indent=1, sort_keys=True, default=str) + "\n")


def list_all(p, task: Optional[str] = None, open_only: bool = False) -> List[Dict[str, Any]]:
    out = []
    for n in sorted(os.listdir(_dir(p))):
        if n.endswith(".json"):
            it = load(p, n[:-5])
            if (not task or it["task"] == task) and (not open_only or it["state"] in OPEN):
                out.append(it)
    return out


def _set(it: Dict[str, Any], state: str, **kw: Any) -> None:
    it.update(kw, state=state)
    it["history"].append({"ts": now_iso(), "state": state, **({"note": kw["error"]} if kw.get("error") else {})})


# ----------------------------------------------------------------------------------------------- classification
def classify_action(a: Dict[str, Any]) -> str:
    """operational | implementation | scientific | resource | frozen_test. Unknown or odd shapes are rejected earlier, never guessed here."""
    t = a["type"]
    if t in ("unblock", "retry", "reverify", "note"):
        return "operational"
    if t in ("guide", "add_task"):
        return "implementation"
    if t == "set_resources" or t == "set_max_attempts":
        return "resource"
    if t == "change_frozen_test":
        return "frozen_test"
    f = set(a.get("fields") or {})
    if f & SCIENTIFIC_FIELDS:
        return "scientific"
    if f & RESOURCE_FIELDS:
        return "resource"
    return "implementation"


def protected(actions: List[Dict[str, Any]]) -> List[str]:
    return sorted({classify_action(a) for a in actions} & {"scientific", "resource", "frozen_test"})


def validate_proposal(raw: Any) -> Dict[str, Any]:
    """The planning agent's output is untrusted text: accept only a small closed vocabulary with the right shapes."""
    if not isinstance(raw, dict) or not isinstance(raw.get("actions"), list) or not raw["actions"]:
        raise ResearchError("The planning agent returned no actions")
    if len(raw["actions"]) > MAX_ACTIONS:
        raise ResearchError(f"The planning agent proposed more than {MAX_ACTIONS} actions")
    acts = []
    for a in raw["actions"]:
        if not isinstance(a, dict) or a.get("type") not in ACTION_TYPES:
            raise ResearchError(f"Unknown action {a.get('type') if isinstance(a, dict) else a!r}; allowed: {', '.join(ACTION_TYPES)}")
        t, ok = a["type"], {"type": a["type"]}
        if t in ("guide", "note"):
            if not isinstance(a.get("text"), str) or not a["text"].strip():
                raise ResearchError(f"{t} needs text")
            ok["text"] = a["text"].strip()[:4000]
        elif t == "amend_task":
            f = a.get("fields")
            if not isinstance(f, dict) or not f or set(f) - (SCIENTIFIC_FIELDS | IMPLEMENTATION_FIELDS | {"execution", "resources"}):
                raise ResearchError("amend_task needs fields from: " + ", ".join(sorted(SCIENTIFIC_FIELDS | IMPLEMENTATION_FIELDS)))
            ok["fields"] = f
        elif t == "add_task":
            tk = a.get("task")
            if not isinstance(tk, dict) or not tk.get("key") or not tk.get("title") or not (tk.get("prompt") or tk.get("description")):
                raise ResearchError("add_task needs task.key, task.title and task.prompt")
            ok["task"] = tk
        elif t == "set_resources":
            f = a.get("fields")
            if not isinstance(f, dict) or not f or set(f) - RESOURCE_FIELDS:
                raise ResearchError("set_resources fields must be from: " + ", ".join(sorted(RESOURCE_FIELDS)))
            ok["fields"] = f
        elif t == "set_max_attempts":
            if not isinstance(a.get("value"), int) or isinstance(a["value"], bool) or not 1 <= a["value"] <= 10:
                raise ResearchError("set_max_attempts needs an integer between 1 and 10")
            ok["value"] = a["value"]
        elif t == "change_frozen_test":
            if not isinstance(a.get("path"), str) or not isinstance(a.get("reason"), str) or len(a["reason"].strip()) < 10:
                raise ResearchError("change_frozen_test needs path and a real reason")
            ok.update(path=a["path"], reason=a["reason"].strip())
        acts.append({**ok, "why": str(a.get("why") or "")[:500]})
    return {"summary": str(raw.get("summary") or "")[:1000], "rationale": str(raw.get("rationale") or "")[:2000], "actions": acts}


# ----------------------------------------------------------------------------------------------- context & planning agent
def gather(p, tid: str) -> Dict[str, Any]:
    """Everything the planning agent (and the human reading the preview) needs, redacted and size-capped."""
    from . import execution as E, planspec as PSM, supervisor as SV, taskprompt
    hit = PSM.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    st, key = hit
    rs = SV.load_run(p, st["plan_id"]) or {}
    e = (rs.get("tasks") or {}).get(tid) or {}
    spec = (st.get("spec_tasks") or {}).get(key, {})
    attempts = []
    for a in e.get("attempts", []):
        row = {k: a.get(k) for k in ("n", "kind", "state", "category", "blocker", "profile", "model", "voided", "escalated")}
        row["diagnostics"] = ((a.get("verification") or {}).get("diagnostics") or [])[:6]
        attempts.append(row)
    logs = ""
    last = (e.get("attempts") or [None])[-1]
    if last and last.get("execution"):
        try:
            lg = E.logs(p, last["execution"], 6000)
            logs = ((lg.get("out") or "")[-2500:] + "\n--- stderr ---\n" + (lg.get("err") or "")[-1500:])
        except (ResearchError, OSError):
            pass
    task = p.get("task", tid)
    eid = task.get("related_experiment_id")
    findings = [{"id": f["id"], "title": f["title"], "status": f["status"]} for f in p.list("finding")
                if eid and eid in ((f.get("links") or {}).get("supports", []) + (f.get("supports") or []))][:10]
    return {"task": tid, "key": key, "title": task["title"], "status": task["status"], "phase": e.get("phase"), "reason": e.get("reason"),
            "plan": st["plan_key"], "plan_id": st["plan_id"], "plan_objective": (st.get("spec") or {}).get("objective"), "experiment": eid,
            "contract": {k: spec.get(k) for k in ("title", "prompt", "acceptance", "checks", "expected_artifacts", "inputs", "datasets", "depends_on",
                                                    "resources", "execution", "agent", "allowed_files")},
            "attempts": attempts, "logs": taskprompt.redact(logs) or "", "findings": findings,
            "running": e.get("phase") in ("running", "verifying", "waiting_hpc")}


PROMPT = """You are the supervisor's intervention planner for a research project. A researcher gave an instruction about one task that is stuck or
misdirected. Turn it into a SMALL set of concrete actions. Do not run commands, do not edit files, do not call tools. Reply with ONE JSON object only.

Allowed action types (anything else is rejected):
  {{"type":"unblock"}} | {{"type":"retry"}} | {{"type":"reverify"}}     (resume: put LAST; reverify re-checks the latest output against the current contract)
  {{"type":"guide","text":"what the next attempt must do differently"}}
  {{"type":"amend_task","fields":{{"prompt":...}} }}   (prompt/title/description/notes = implementation; acceptance/checks/expected_artifacts/inputs/datasets/depends_on = SCIENTIFIC)
  {{"type":"add_task","task":{{"key":"...","title":"...","prompt":"...","depends_on":[...],"agent":{{"role":"implementer"}},"checks":["file:..."]}}}}
  {{"type":"set_resources","fields":{{"cpus":16,"mem":"32G","time":"04:00:00"}}}}   (protected)
  {{"type":"set_max_attempts","value":4}}   (protected)
  {{"type":"change_frozen_test","path":"tests/x.py","reason":"what was wrong and why"}}   (protected)
  {{"type":"note","text":"..."}}
Every action may carry "why". Never weaken a check to make a task pass unless the researcher explicitly asked to amend the acceptance criteria; if they did, say so
in "rationale". Prefer the fewest changes. Do not invent results.
Output shape: {{"summary":"one sentence","rationale":"why these actions follow from the instruction","actions":[...]}}

RESEARCHER INSTRUCTION:
{instruction}

TASK CONTEXT (JSON):
{context}
"""


def _json_from(text: str) -> Any:
    text = text or ""
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except ValueError:
            pass
    raise ResearchError("The agent's reply was not a JSON object")


def assistant_cfg(p) -> Dict[str, Any]:
    c = {"profile": None, "model": None, "timeout_seconds": 900, **((p.config.get("supervisor_assistant") or {}))}
    return c


def run_assistant(p, objective: str, prompt: str) -> str:
    """One headless, read-only agent turn for planning/debugging. Default model is the strongest Claude when the profile is Claude; any
    other provider uses its own configured model. Tests replace this function."""
    from . import agents, providers as PV
    c = assistant_cfg(p)
    profile = c["profile"] or "claude"
    name, prof = agents.profile_for(p, "planner", profile)
    model = c["model"] or (prof.get("model") or ("claude-opus-5-5" if PV.provider_of(prof, name) == "claude" else None))
    d = agents.dispatch(p, role="planner", objective=objective, instructions=prompt, profile=profile, model=model, headless=True)
    if not d.get("available"):
        raise ResearchError(d.get("problem") or "the assistant agent is not available")
    r = agents.run_headless(p, d, timeout=float(c["timeout_seconds"]))
    if r["timed_out"] or r["exit_code"] not in (0, None):
        raise ResearchError(f"the assistant agent failed (exit {r['exit_code']}{', timed out' if r['timed_out'] else ''}): {(r.get('stderr') or '')[-200:]}")
    return r["result"]


def preview(p, it: Dict[str, Any]) -> List[str]:
    """Human-readable consequences, one line per action."""
    out = []
    for a in it.get("actions") or []:
        k = classify_action(a)
        t = a["type"]
        s = {"unblock": "resume the task (new attempt)", "retry": "queue another attempt", "reverify": "re-run the checks on the latest output under the current contract",
             "guide": "next attempt is told: " + a.get("text", "")[:160], "note": "note: " + a.get("text", "")[:160],
             "amend_task": "amend " + ", ".join(sorted(a.get("fields", {}))) + " of the task contract (applies to the next attempt)",
             "add_task": f"add task {a.get('task', {}).get('key')} to the plan", "set_resources": "set resources " + json.dumps(a.get("fields")),
             "set_max_attempts": f"allow {a.get('value')} attempts for this task", "change_frozen_test": f"authorize a change to frozen test {a.get('path')}"}[t]
        out.append(f"[{k}{'*' if k in ('scientific', 'resource', 'frozen_test') else ''}] {s}")
    return out


# ----------------------------------------------------------------------------------------------- lifecycle
def request(p, tid: str, message: str, interrupt: bool = False, source: str = "human", plan_now: bool = True) -> Dict[str, Any]:
    """Create the intervention (persisted first, so a crash leaves a visible `requested` record) and, by default, plan it now."""
    from . import supervisor as SV
    SV.require_human(p, "Instructing the supervisor")
    if not (message or "").strip():
        raise ResearchError("An instruction needs text")
    ctx = gather(p, tid)
    if any(i["state"] in OPEN for i in list_all(p, tid)):
        raise ResearchError(f"{tid} already has an open intervention; finish, reject or cancel it first", hint="`research intervention list`")
    n = max([int(i["id"][4:]) for i in list_all(p)] + [0]) + 1
    who = (p.author or {}).get("author_name") or os.environ.get("RESEARCH_AUTHOR") or "human"
    it = {"id": f"INT-{n:03d}", "task": ctx["task"], "key": ctx["key"], "plan": ctx["plan"], "plan_id": ctx["plan_id"], "instruction": message.strip(),
          "source": source, "requested_by": who, "interrupt": bool(interrupt), "state": "requested", "actions": [], "applied": {}, "originals": {},
          "created": now_iso(), "history": [{"ts": now_iso(), "state": "requested"}], "protected": []}
    save(p, it)
    p.event("task", ctx["task"], "intervention_requested", f"{it['id']}: {message.strip()[:200]}")
    return plan_it(p, it["id"]) if plan_now else it


def plan_it(p, iid: str) -> Dict[str, Any]:
    """(Re)run the planning agent for a `requested` (or failed-to-plan) intervention."""
    it = load(p, iid)
    if it["state"] not in ("requested", "failed"):
        raise ResearchError(f"{iid} is {it['state']}; it can only be planned while requested")
    ctx = gather(p, it["task"])
    try:
        raw = _json_from(run_assistant(p, f"Plan intervention {iid} for {it['task']}", PROMPT.format(instruction=it["instruction"], context=json.dumps(ctx, default=str, indent=1)[:24000])))
        prop = validate_proposal(raw)
    except (ResearchError, ValueError) as ex:
        _set(it, "failed", error=f"could not plan: {ex}")
        save(p, it)
        return it
    prot = protected(prop["actions"])
    _set(it, "proposed", summary=prop["summary"], rationale=prop["rationale"], actions=prop["actions"], protected=prot, preview=preview(p, {"actions": prop["actions"]}))
    save(p, it)
    p.event("task", it["task"], "intervention_proposed", f"{iid}: {prop['summary'][:200]}" + (f" [needs approval: {', '.join(prot)}]" if prot else ""))
    if not prot:       # nothing protected: the researcher's own instruction is the authority (opt-out: intervention.require_approval)
        if not (p.config.get("intervention") or {}).get("require_approval", False):
            return _approve(p, it, by=it["requested_by"], via="policy: no protected change", note=None)
    return it


def _approve(p, it: Dict[str, Any], by: str, via: str, note: Optional[str]) -> Dict[str, Any]:
    _set(it, "approved", approval={"by": by, "via": via, "note": note, "ts": now_iso(), "protected": it["protected"]})
    save(p, it)
    p.event("task", it["task"], "intervention_approved", f"{it['id']} approved by {by} ({via})")
    return it


def approve(p, iid: str, note: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
    """ONE approval for everything in the proposal. Protected changes need a human, a rationale and the PIN; agents and chat cannot."""
    from . import approval, supervisor as SV
    SV.require_human(p, "Approving an intervention")
    it = load(p, iid)
    if it["state"] != "proposed":
        raise ResearchError(f"{iid} is {it['state']}, not waiting for approval")
    if it["protected"]:
        approval.confirm(p, f"Approve intervention {iid} ({', '.join(it['protected'])}): {it.get('summary', '')[:120]}", note, pin)
    by = (p.author or {}).get("author_name") or os.environ.get("RESEARCH_AUTHOR") or "human"
    return _approve(p, it, by=by, via="human approval" + (" with PIN" if it["protected"] else ""), note=note)


def reject(p, iid: str, reason: Optional[str] = None) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Rejecting an intervention")
    it = load(p, iid)
    if it["state"] not in ("requested", "proposed", "approved"):
        raise ResearchError(f"{iid} is {it['state']}; only a not-yet-applied intervention can be rejected")
    _set(it, "rejected", rejection={"reason": reason, "by": (p.author or {}).get("author_name"), "ts": now_iso()})
    save(p, it)
    p.event("task", it["task"], "intervention_rejected", f"{iid} rejected" + (f": {reason}" if reason else ""))
    return it


def cancel(p, iid: str) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Cancelling an intervention")
    it = load(p, iid)
    if it["state"] not in OPEN or it.get("applied"):
        raise ResearchError(f"{iid} is {it['state']} and has applied changes; those stay recorded and are not undone")
    _set(it, "cancelled")
    save(p, it)
    return it


# ----------------------------------------------------------------------------------------------- applying (called by the supervisor tick)
def guidance(p, tid: str) -> Optional[str]:
    """Text injected into the task's next brief: the researcher's instruction and the approved guidance, from approved-or-later interventions."""
    parts = []
    for it in list_all(p, tid):
        if it["state"] in ("approved", "implementing", "verifying", "resolved") and it.get("approval"):
            g = [a["text"] for a in it["actions"] if a["type"] in ("guide", "note")]
            amended = [a for a in it["actions"] if a["type"] in ("amend_task", "change_frozen_test")]
            parts.append(f"{it['id']} (from {it['requested_by']}): {it['instruction']}" + ("".join(f"\n- {x}" for x in g))
                         + ("".join(f"\n- authorized: modify frozen test {a['path']} because {a['reason']}" for a in amended if a["type"] == "change_frozen_test")))
    return "\n\n".join(parts) or None


def step(p, st: Dict[str, Any], rs: Dict[str, Any], out: Dict[str, Any]) -> None:
    """Advance approved interventions of this plan by at most one transition each. Idempotent: every applied action is flagged before the next."""
    from . import supervisor as SV
    for it in list_all(p, open_only=True):
        if it["plan_id"] != st["plan_id"] or it["state"] in ("requested", "proposed"):
            continue
        try:
            _advance(p, st, rs, it, out, SV)
        except (PolicyBlocked, ResearchError) as ex:
            _set(it, "failed", error=str(ex))
            save(p, it)
            out["events"].append(f"intervention {it['id']} failed: {ex}")


def _advance(p, st, rs, it, out, SV) -> None:
    tid = it["task"]
    e = (rs.get("tasks") or {}).get(tid) or {}
    live = e.get("phase") in ("running", "verifying", "waiting_hpc")
    if it["state"] == "approved":
        if live and not it["interrupt"]:
            it["queued"] = True                 # applied when the current attempt ends
            save(p, it)
            return
        if live and it["interrupt"]:
            _interrupt(p, st, rs, it, e, SV)
        _set(it, "implementing", queued=False)
        save(p, it)
    if it["state"] == "implementing":
        if live:
            return
        if p.get("task", tid)["status"] == "done" and e.get("phase") == "verified":      # the running attempt succeeded while this was queued
            _set(it, "resolved", note="the task was verified before the instruction was applied; nothing was changed")
            save(p, it)
            return
        with SV.human_grant(f"intervention {it['id']} approved by {it['approval']['by']}"):
            for i, a in enumerate(it["actions"]):
                if it["applied"].get(str(i)):
                    continue
                _apply(p, st, rs, it, i, a, SV)
                it["applied"][str(i)] = now_iso()
                save(p, it)                                    # flagged before the next action: a restart never repeats one
        rs2 = SV.load_run(p, st["plan_id"]) or rs
        it["attempts_at_resume"] = len([x for x in ((rs2.get("tasks") or {}).get(tid) or {}).get("attempts", []) if not x.get("voided")])
        _set(it, "verifying")
        save(p, it)
        return
    if it["state"] == "verifying":
        task = p.get("task", tid)
        n = len([x for x in e.get("attempts", []) if not x.get("voided")])
        if task["status"] == "done" and e.get("phase") == "verified":
            _set(it, "resolved")
            save(p, it)
            p.event("task", tid, "intervention_resolved", f"{it['id']} resolved: the supervisor verified {tid}")
        elif e.get("phase") in ("blocked", "attention") and n > it.get("attempts_at_resume", 0):
            _set(it, "failed", error=f"the task did not recover after the intervention ({e.get('phase')}: {(e.get('reason') or '')[:200]})")
            save(p, it)


def _interrupt(p, st, rs, it, e, SV) -> None:
    """Stop the running agent safely and keep its attempt as evidence (voided = not counted). Refused while computational jobs are pending."""
    from . import execution as E
    if e.get("phase") == "waiting_hpc" or e.get("hpc_runs") and e.get("phase") != "verified":
        raise ResearchError("Cannot interrupt: computational jobs are pending; they would be resubmitted. Let them finish, or instruct without --interrupt.")
    a = (e.get("attempts") or [None])[-1]
    if a and a.get("execution"):
        try:
            E.stop(p, a["execution"])
        except ResearchError:
            pass
        with SV.human_grant(f"intervention {it['id']}"):
            SV.void_attempts(p, it["task"], f"interrupted by {it['id']}: {it['instruction'][:80]}", [a["n"]])


def _apply(p, st, rs, it, i, a, SV) -> None:
    tid, key, t = it["task"], it["key"], a["type"]
    if t in ("guide", "note"):
        return                                       # carried by guidance() into the next brief
    if t in ("amend_task", "set_resources", "set_max_attempts", "add_task"):
        _apply_plan_patch(p, st, it, a)
        return
    if t == "change_frozen_test":
        p.event("task", tid, "intervention_frozen_test", f"{it['id']}: authorized change to {a['path']}: {a['reason']}")
        return                                       # authorization is recorded and given to the agent; re-freezing stays `research orchestrate correct-test`
    outcome = {"unblock": "unblock", "retry": "retry", "reverify": "completed"}[t]
    SV.resolve(p, tid, outcome, f"{it['id']}: {it['instruction'][:120]}")


def _apply_plan_patch(p, st, it, a) -> None:
    from . import services, yamlio
    key, tid = it["key"], it["task"]
    doc = PS.parse(PS.export(p, st["plan_key"], task=key))
    pl = doc["plans"][0]
    exp = next((e for e in pl.get("experiments", []) if any(x.get("key") == key for x in e.get("tasks", []))), None)
    task = next((x for e in pl.get("experiments", []) for x in e.get("tasks", []) if x.get("key") == key), None) or \
        next((x for x in pl.get("tasks", []) if x.get("key") == key), None)
    if task is None:
        raise ResearchError(f"{key} is not in the plan spec")
    before = {k: task.get(k) for k in ("prompt", "title", "acceptance", "checks", "expected_artifacts", "inputs", "datasets", "depends_on", "resources", "execution")}
    t = a["type"]
    if t == "amend_task":
        task.update(a["fields"])
    elif t == "set_resources":
        task["resources"] = {**(task.get("resources") or {}), **a["fields"]}
    elif t == "set_max_attempts":
        task["execution"] = {**(task.get("execution") or {}), "max_attempts": a["value"]}
    elif t == "add_task":
        new = dict(a["task"])
        (exp["tasks"] if exp else pl.setdefault("tasks", [])).append(new)
    text = yamlio.dump(doc, block=True)
    if t != "add_task":
        it["originals"].setdefault(key, before)                       # first change wins: the original contract is preserved verbatim
    PS.apply(p, text, source=f"intervention {it['id']}", author_note=f"{it['id']} approved by {it['approval']['by']}: {it['instruction'][:120]}")
    if classify_action(a) in ("scientific", "resource"):
        after = {k: task.get(k) for k in before}
        services.create_decision(
            p, f"{it['id']}: amended the contract of {tid} ({key})",
            reason=f"Instruction: {it['instruction']}\nRationale: {it.get('rationale') or a.get('why')}\nApproved by {it['approval']['by']} ({it['approval']['via']}).\n"
                   f"Before: {json.dumps({k: v for k, v in before.items() if v != after.get(k)}, default=str)}\nAfter: {json.dumps({k: v for k, v in after.items() if v != before.get(k)}, default=str)}\n"
                   "Earlier attempts, their diagnostics and failed checks are unchanged.", title=f"Amended acceptance/resources of {tid}", experiments=[p.get("task", tid).get("related_experiment_id")] if p.get("task", tid).get("related_experiment_id") else None)
        it.setdefault("amendments", []).append({"action": i_summary(a), "before": before, "after": after, "ts": now_iso()})


def i_summary(a: Dict[str, Any]) -> str:
    return a["type"] + (":" + ",".join(sorted(a.get("fields", {}))) if a.get("fields") else "")
