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
ACTION_TYPES = ("unblock", "retry", "reverify", "guide", "amend_task", "add_task", "set_resources", "set_max_attempts", "set_plan_budget", "change_frozen_test", "note")
PLAN_BUDGETS = ("max_total_attempts", "max_attempts_per_task")
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
    if t in ("set_resources", "set_max_attempts", "set_plan_budget"):
        return "resource"
    if t == "change_frozen_test":
        return "frozen_test"
    f = set(a.get("fields") or {})
    if f & SCIENTIFIC_FIELDS:
        return "scientific"
    if f & (RESOURCE_FIELDS | {"execution", "resources"}):   # nested resource/execution blocks are resource changes too
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
            ag = tk.get("agent")
            if ag is not None and (not isinstance(ag, dict) or ag.get("role", "implementer") not in PS.ROLES):
                raise ResearchError(f"add_task {tk.get('key')}: agent.role must be one of {', '.join(PS.ROLES)} (got {(ag or {}).get('role') if isinstance(ag, dict) else ag!r})")
            ok["task"] = tk
        elif t == "set_resources":
            f = a.get("fields")
            if not isinstance(f, dict) or not f or set(f) - RESOURCE_FIELDS:
                raise ResearchError("set_resources fields must be from: " + ", ".join(sorted(RESOURCE_FIELDS)))
            ok["fields"] = f
        elif t == "set_max_attempts":
            if not isinstance(a.get("value"), int) or isinstance(a["value"], bool) or a["value"] < 1:
                raise ResearchError("set_max_attempts needs a positive integer")     # the ceiling is the plan's own max_total_attempts, checked in preflight()
            ok["value"] = a["value"]
        elif t == "set_plan_budget":
            if a.get("field") not in PLAN_BUDGETS or not isinstance(a.get("value"), int) or isinstance(a["value"], bool) or a["value"] < 1:
                raise ResearchError("set_plan_budget needs field (" + ", ".join(PLAN_BUDGETS) + ") and a positive integer value")
            ok.update(field=a["field"], value=a["value"])
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
            "recovery": e.get("recovery"), "evidence": _evidence(p, tid), "verification_history": [(a.get("verification_history") or []) for a in e.get("attempts", [])][-1:],
            "running": e.get("phase") in ("running", "verifying", "waiting_hpc")}


def _evidence(p, tid: str) -> Dict[str, Any]:
    from . import recovery
    try:
        return recovery.evidence(p, tid)
    except (ResearchError, OSError, KeyError):
        return {}


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


class ProviderLimited(ResearchError):
    """The assistant's provider refused because of a usage/rate/overload limit. `until` is when it may be used again (epoch); `waitable` is False for a
    credit balance, which waiting does not fix."""

    def __init__(self, message: str, until: float, provider: str, waitable: bool = True):
        super().__init__(message)
        self.until, self.provider, self.waitable = until, provider, waitable


def _hhmm(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts).astimezone().strftime("%a %H:%M")


def run_assistant(p, objective: str, prompt: str) -> str:
    """One headless, read-only agent turn for planning/debugging. Default model is the strongest Claude when the profile is Claude; any
    other provider uses its own configured model. Tests replace this function."""
    return run_assistant_full(p, objective, prompt)["result"]


def run_assistant_full(p, objective: str, prompt: str) -> Dict[str, Any]:
    """`run_assistant` with the session's result dict (`result`, `usage`: the provider-reported usage record or a string saying it was unavailable)."""
    from . import agents, providers as PV
    c = assistant_cfg(p)
    profile = c["profile"] or "claude"
    name, prof = agents.profile_for(p, "planner", profile)
    model = c["model"] or (prof.get("model") or ("claude-opus-5-5" if PV.provider_of(prof, name) == "claude" else None))
    from . import supervisor as SV
    kind = PV.provider_of(prof, name)
    known = SV.limited_until(p, name)
    if known:       # the account is already known to be limited: do not spend a call to find out again
        raise ProviderLimited(f"{kind} is at its usage limit until about {_hhmm(known)}", known, kind)
    d = agents.dispatch(p, role="planner", objective=objective, instructions=prompt, profile=profile, model=model, headless=True)
    if not d.get("available"):
        raise ResearchError(d.get("problem") or "the assistant agent is not available")
    r = agents.run_headless(p, d, timeout=float(c["timeout_seconds"]))
    if not r["timed_out"] and r["exit_code"] not in (0, None) :
        info = PV.limit_info(kind, r.get("raw_stdout") or "", r.get("raw_stderr") or r.get("stderr") or "", r["exit_code"])
        if info:
            until = SV._plan_wait(p, SV._limit_key(p, name), kind, info, SV.cfg(p)["usage_limit"])
            waitable = info.kind != "credit" and until - SV._now_ts() <= float(SV.cfg(p)["usage_limit"]["max_wait_hours"]) * 3600
            raise ProviderLimited(f"{kind} refused ({info.kind} limit); it may be used again at about {_hhmm(until)}", until, kind, waitable)
    if r["timed_out"] or r["exit_code"] not in (0, None):
        raise ResearchError(f"the assistant agent failed (exit {r['exit_code']}{', timed out' if r['timed_out'] else ''}): {(r.get('stderr') or '')[-200:]}")
    return r


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
             "set_max_attempts": f"allow {a.get('value')} attempts for this task",
             "set_plan_budget": f"set the plan's {a.get('field')} to {a.get('value')}", "change_frozen_test": f"authorize a change to frozen test {a.get('path')}"}[t]
        out.append(f"[{k}{'*' if k in ('scientific', 'resource', 'frozen_test') else ''}] {s}")
    return out


# ----------------------------------------------------------------------------------------------- lifecycle
def _create(p, tid: str, message: str, interrupt: bool, source: str, allow_open: bool = False) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Instructing the supervisor")
    if not (message or "").strip():
        raise ResearchError("An instruction needs text")
    ctx = gather(p, tid)
    if not allow_open and any(i["state"] in OPEN for i in list_all(p, tid)):
        raise ResearchError(f"{tid} already has an open intervention; finish, reject or cancel it first", hint="`research intervention list`")
    n = max([int(i["id"][4:]) for i in list_all(p)] + [0]) + 1
    who = (p.author or {}).get("author_name") or os.environ.get("RESEARCH_AUTHOR") or "human"
    it = {"id": f"INT-{n:03d}", "task": ctx["task"], "key": ctx["key"], "plan": ctx["plan"], "plan_id": ctx["plan_id"], "instruction": message.strip(),
          "source": source, "requested_by": who, "interrupt": bool(interrupt), "state": "requested", "actions": [], "applied": {}, "originals": {},
          "created": now_iso(), "history": [{"ts": now_iso(), "state": "requested"}], "protected": []}
    save(p, it)
    p.event("task", ctx["task"], "intervention_requested", f"{it['id']}: {message.strip()[:200]}")
    return it


def request(p, tid: str, message: str, interrupt: bool = False, source: str = "human", plan_now: bool = True) -> Dict[str, Any]:
    """Create the intervention (persisted first, so a crash leaves a visible `requested` record) and, by default, plan it now."""
    it = _create(p, tid, message, interrupt, source)
    return plan_it(p, it["id"]) if plan_now else it


def create_planned(p, tid: str, instruction: str, raw_actions: Any, summary: str = "", rationale: str = "", source: str = "decision", allow_open: bool = False, hold: bool = False) -> Dict[str, Any]:
    """An intervention whose actions are already known (a decision option's prepared action, the investigator's plan): no planning-agent call.
    Same validation, classification and approval rules as a planned one: unprotected actions are approved under the researcher's own authority,
    protected ones wait in `proposed` for ONE approval with the PIN (`research intervention approve`)."""
    prop = validate_proposal({"summary": summary, "rationale": rationale, "actions": raw_actions})   # raises before anything is stored
    preflight(p, tid, prop["actions"])                                                              # the whole proposal against the complete plan schema, also before anything is stored
    return _propose(p, _create(p, tid, instruction, False, source, allow_open), prop, hold)   # hold: wait for a person even though nothing is protected


def plan_it(p, iid: str) -> Dict[str, Any]:
    """(Re)run the planning agent for a `requested` (or failed-to-plan) intervention."""
    it = load(p, iid)
    if it["state"] not in ("requested", "failed"):
        raise ResearchError(f"{iid} is {it['state']}; it can only be planned while requested")
    ctx = gather(p, it["task"])
    try:
        raw = _json_from(run_assistant(p, f"Plan intervention {iid} for {it['task']}", PROMPT.format(instruction=it["instruction"], context=json.dumps(ctx, default=str, indent=1)[:24000])))
        prop = validate_proposal(raw)
        preflight(p, it["task"], prop["actions"])
    except (ResearchError, ValueError) as ex:
        _set(it, "failed", error=f"could not plan: {ex}")
        save(p, it)
        return it
    return _propose(p, it, prop)


def _propose(p, it: Dict[str, Any], prop: Dict[str, Any], hold: bool = False) -> Dict[str, Any]:
    iid = it["id"]
    prot = protected(prop["actions"]) or (["held: beyond what an automatic diagnosis may do"] if hold else [])
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


# ----------------------------------------------------------------------------------------------- a failed intervention: diagnose, retry, correct
def diagnose(p, iid: str) -> Dict[str, Any]:
    """Where a failed or partly applied intervention stands, deterministically: which actions ran (and stay applied), which did not, whether the rest could be
    applied now, and the exact next step. Read-only."""
    it = load(p, iid)
    rows, applied = [], it.get("applied") or {}
    for i, a in enumerate(it.get("actions") or []):
        rows.append({"n": i + 1, "type": a["type"], "summary": preview(p, {"actions": [a]})[0], "applied": applied.get(str(i)), "class": classify_action(a)})
    pending = [r for r in rows if not r["applied"]]
    problem = None
    try:
        preflight(p, it["task"], it["actions"], applied)
    except ResearchError as ex:
        problem = str(ex)
    if it["state"] != "failed":
        nxt = f"{iid} is {it['state']}; nothing to recover"
    elif not it.get("approval"):
        nxt = f"`research intervention plan {iid}` plans it again (it never reached approval)"
    elif problem:
        nxt = (f"fix the action that cannot apply: `research intervention edit {iid} N --json '{{...}}'` (replaces one NOT-yet-applied action and asks for approval again), "
               f"then approve it; the {len(rows) - len(pending)} applied action(s) are never repeated")
    else:
        nxt = f"`research intervention retry {iid}` applies the {len(pending)} remaining action(s) under the original approval; applied ones are skipped"
    return {"id": iid, "task": it["task"], "state": it["state"], "error": it.get("error"), "actions": rows, "applied": len(rows) - len(pending), "pending": len(pending),
            "applicable_now": problem is None, "problem": problem, "next": nxt, "protected": it.get("protected")}


def retry(p, iid: str, note: Optional[str] = None) -> Dict[str, Any]:
    """A human re-opens a failed intervention that WAS approved, under that same approval (it covers exactly these actions). Applied actions stay flagged and are
    skipped; the rest are preflighted again before anything runs. Nothing is voided or reset."""
    from . import supervisor as SV
    SV.require_human(p, "Retrying an intervention")
    it = load(p, iid)
    if it["state"] != "failed" or not it.get("approval"):
        raise ResearchError(f"{iid} is {it['state']}" + ("" if it.get("approval") else " and was never approved") + "; only a failed, approved intervention can be retried",
                            hint=f"`research intervention diagnose {iid}`")
    d = diagnose(p, iid)
    if not d["applicable_now"]:
        raise ResearchError(f"{iid} would fail again: {d['problem']}", hint=d["next"])
    it.setdefault("retries", []).append({"by": (p.author or {}).get("author_name") or "human", "ts": now_iso(), "note": note, "applied_before": d["applied"]})
    _set(it, "approved", error=None)
    save(p, it)
    p.event("task", it["task"], "intervention_retried", f"{iid} retried: {d['pending']} action(s) remain, {d['applied']} already applied are skipped")
    return it


def edit_action(p, iid: str, index: int, raw: Dict[str, Any], note: Optional[str] = None) -> Dict[str, Any]:
    """A human corrects ONE not-yet-applied action of a failed (or proposed) intervention. The new action is validated and the whole remainder preflighted; then
    the intervention goes back to `proposed`: the earlier approval covered different text, so a protected change needs the PIN again. Applied actions are untouched."""
    from . import supervisor as SV
    SV.require_human(p, "Editing an intervention")
    it = load(p, iid)
    if it["state"] not in ("failed", "proposed"):
        raise ResearchError(f"{iid} is {it['state']}; only a failed or proposed intervention can be corrected")
    if not 1 <= index <= len(it["actions"]):
        raise ResearchError(f"{iid} has {len(it['actions'])} actions")
    if (it.get("applied") or {}).get(str(index - 1)):
        raise ResearchError(f"action {index} was already applied; it cannot be edited (history is never rewritten)")
    new = validate_proposal({"actions": [raw]})["actions"][0]
    acts = list(it["actions"])
    acts[index - 1] = new
    preflight(p, it["task"], acts, it.get("applied"))
    prot = protected(acts)
    it["history"].append({"ts": now_iso(), "state": "edited", "note": f"action {index} corrected by {(p.author or {}).get('author_name') or 'human'}" + (f": {note}" if note else "")})
    it.pop("approval", None)
    it.update(actions=acts, protected=prot, preview=preview(p, {"actions": acts}))
    _set(it, "proposed", error=None)
    save(p, it)
    p.event("task", it["task"], "intervention_edited", f"{iid}: action {index} corrected" + (f" [needs approval: {', '.join(prot)}]" if prot else ""))
    if not prot and not (p.config.get("intervention") or {}).get("require_approval", False):
        return _approve(p, it, by=it["requested_by"], via="policy: no protected change", note=None)
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
            n, k = len(it.get("applied") or {}), len(it.get("actions") or [])
            why = str(ex) + (f" ({n} of {k} actions had already been applied and stay applied: `research intervention diagnose {it['id']}`)" if n else "")
            _set(it, "failed", error=why)
            save(p, it)
            out["events"].append(f"intervention {it['id']} failed: {why}")


BUDGET_ONLY = ("set_max_attempts", "set_plan_budget")


def budget_only(it: Dict[str, Any]) -> bool:
    """A budget change does not touch the task's work: it applies at once even while an agent runs, and there is no attempt to wait for."""
    return bool(it.get("actions")) and all(a["type"] in BUDGET_ONLY for a in it["actions"])


def _advance(p, st, rs, it, out, SV) -> None:
    tid = it["task"]
    e = (rs.get("tasks") or {}).get(tid) or {}
    live = e.get("phase") in ("running", "verifying", "waiting_hpc") and not budget_only(it)
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
        preflight(p, tid, it["actions"], it["applied"])        # every remaining action is checked before the first one runs
        with SV.human_grant(f"intervention {it['id']} approved by {it['approval']['by']}"):
            for i, a in enumerate(it["actions"]):
                if it["applied"].get(str(i)):
                    continue
                _apply(p, st, rs, it, i, a, SV)
                it["applied"][str(i)] = now_iso()
                save(p, it)                                    # flagged before the next action: a restart never repeats one
        if budget_only(it):
            _set(it, "resolved", note="budget applied; attempt history unchanged")
            save(p, it)
            p.event("task", tid, "intervention_resolved", f"{it['id']} resolved: budget changed")
            return
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
    if t in PATCHING:
        _apply_plan_patch(p, st, it, a)
        return
    if t == "change_frozen_test":
        p.event("task", tid, "intervention_frozen_test", f"{it['id']}: authorized change to {a['path']}: {a['reason']}")
        return                                       # authorization is recorded and given to the agent; re-freezing stays `research orchestrate correct-test`
    outcome = {"unblock": "unblock", "retry": "retry", "reverify": "completed"}[t]
    SV.resolve(p, tid, outcome, f"{it['id']}: {it['instruction'][:120]}")


def _locate(pl: Dict[str, Any], key: str):
    for e in pl.get("experiments", []):
        for x in e.get("tasks", []):
            if x.get("key") == key:
                return e, x
    return None, next((x for x in pl.get("tasks", []) if x.get("key") == key), None)


def _present(pl: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    return next((x for e in pl.get("experiments", []) for x in e.get("tasks", []) if x.get("key") == key), None) or \
        next((x for x in pl.get("tasks", []) if x.get("key") == key), None)


def _same(existing: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """The task already in the plan is the one this action would add: every field the action names has the same value (defaults the spec fills in are not compared)."""
    return all(existing.get(k) == v or (k == "agent" and all((existing.get("agent") or {}).get(x) == y for x, y in (v or {}).items())) for k, v in new.items())


def _lookup(p, st: Dict[str, Any]):
    """A task already in the plan under a key (the patch works on a one-task export, which cannot see its siblings)."""
    def find(k: str) -> Optional[Dict[str, Any]]:
        if k not in (st.get("tasks") or {}):
            return None
        return _present(PS.parse(PS.export(p, st["plan_key"], task=k))["plans"][0], k)
    return find


def _patch(pl: Dict[str, Any], key: str, a: Dict[str, Any], find=None) -> str:
    """Apply one plan-changing action to the in-memory plan. Returns "changed" or "present" (an add_task whose task already exists unchanged: applied before)."""
    t = a["type"]
    if t == "set_plan_budget":
        pl["execution"] = {**(pl.get("execution") or {}), a["field"]: a["value"]}
        return "changed"
    exp, task = _locate(pl, key)
    if task is None:
        raise ResearchError(f"{key} is not in the plan spec")
    if t == "amend_task":
        task.update(a["fields"])
    elif t == "set_resources":
        task["resources"] = {**(task.get("resources") or {}), **a["fields"]}
    elif t == "set_max_attempts":
        task["execution"] = {**(task.get("execution") or {}), "max_attempts": a["value"]}
    elif t == "add_task":
        new = dict(a["task"])
        have = _present(pl, new["key"]) or (find(new["key"]) if find else None)
        if have is not None:
            if _same(have, new):
                return "present"
            raise ResearchError(f"add_task {new['key']}: a different task with that key already exists in the plan")
        (exp["tasks"] if exp else pl.setdefault("tasks", [])).append(new)
    return "changed"


PATCHING = ("amend_task", "set_resources", "set_max_attempts", "set_plan_budget", "add_task")


def preflight(p, tid: str, actions: List[Dict[str, Any]], applied: Optional[Dict[str, Any]] = None) -> None:
    """Validate the WHOLE remaining intervention against the complete plan schema before any action runs, so a bad later action can never leave earlier
    ones applied. The plan is patched in memory, action by action in order, and normalized exactly as `plan yaml apply` would; every problem is listed."""
    from . import supervisor as SV
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a managed plan")
    st, key = hit
    todo = [(i, a) for i, a in enumerate(actions) if not (applied or {}).get(str(i))]
    patches = [(i, a) for i, a in todo if a["type"] in PATCHING]
    find = _lookup(p, st)
    errs: List[str] = []
    rs = SV.load_run(p, st["plan_id"]) or {}
    budget, used = int(SV.cfg(p, st)["max_total_attempts"]), int(((rs.get("budgets") or {}).get("attempts")) or 0)
    doc = None
    if patches:
        doc = PS.parse(PS.export(p, st["plan_key"], task=key))
    for i, a in patches:
        try:
            if a["type"] == "set_plan_budget" and a["field"] == "max_total_attempts":
                if a["value"] < used:
                    raise ResearchError(f"max_total_attempts {a['value']} is below the {used} attempts already used")
                budget = a["value"]
            if a["type"] == "set_max_attempts" and a["value"] > budget:
                raise ResearchError(f"{a['value']} attempts for one task exceeds the plan's max_total_attempts ({budget}); raise the plan budget first (set_plan_budget)")
            if _patch(doc["plans"][0], key, a, find) == "present":
                continue
        except ResearchError as ex:
            errs.append(f"action {i + 1} ({a['type']}): {ex}")
    if doc is not None and not errs:
        try:
            PS.normalize(doc, PS._known(p))
        except PS.PlanSpecError as ex:
            errs += [f"the plan would be invalid: {m}" for m in (getattr(ex, "errors", None) or [str(ex)])]
        except ResearchError as ex:
            errs.append(f"the plan would be invalid: {ex}")
    if errs:
        raise ResearchError("The intervention cannot be applied, and nothing was changed: " + "; ".join(errs[:6]))


def _apply_plan_patch(p, st, it, a) -> None:
    from . import services, yamlio
    key, tid = it["key"], it["task"]
    doc = PS.parse(PS.export(p, st["plan_key"], task=key))
    pl = doc["plans"][0]
    _, task = _locate(pl, key)
    if task is None:
        raise ResearchError(f"{key} is not in the plan spec")
    before = {k: task.get(k) for k in ("prompt", "title", "acceptance", "checks", "expected_artifacts", "inputs", "datasets", "depends_on", "resources", "execution")}
    plan_before = dict(pl.get("execution") or {})
    t = a["type"]
    if _patch(pl, key, a, _lookup(p, st)) == "present":
        return                                                          # already in the plan from an earlier, interrupted pass: never added twice
    text = yamlio.dump(doc, block=True)
    if t not in ("add_task", "set_plan_budget"):
        it["originals"].setdefault(key, before)                       # first change wins: the original contract is preserved verbatim
    PS.apply(p, text, source=f"intervention {it['id']}", author_note=f"{it['id']} approved by {it['approval']['by']}: {it['instruction'][:120]}")
    if classify_action(a) in ("scientific", "resource"):
        after = {k: task.get(k) for k in before}
        what = {"budget": {"before": plan_before, "after": pl.get("execution")}} if t == "set_plan_budget" else {}
        services.create_decision(
            p, f"{it['id']}: amended the contract of {tid} ({key})" if not what else f"{it['id']}: changed the plan's attempt budget",
            reason=f"Instruction: {it['instruction']}\nRationale: {it.get('rationale') or a.get('why')}\nApproved by {it['approval']['by']} ({it['approval']['via']}).\n"
                   f"Before: {json.dumps(what['budget']['before'] if what else {k: v for k, v in before.items() if v != after.get(k)}, default=str)}\n"
                   f"After: {json.dumps(what['budget']['after'] if what else {k: v for k, v in after.items() if v != before.get(k)}, default=str)}\n"
                   "Earlier attempts, their diagnostics and failed checks are unchanged.", title=f"Amended acceptance/resources of {tid}", experiments=[p.get("task", tid).get("related_experiment_id")] if p.get("task", tid).get("related_experiment_id") else None)
        it.setdefault("amendments", []).append({"action": i_summary(a), "before": before, "after": after, "ts": now_iso()})


def i_summary(a: Dict[str, Any]) -> str:
    return a["type"] + (":" + ",".join(sorted(a.get("fields", {}))) if a.get("fields") else "")
