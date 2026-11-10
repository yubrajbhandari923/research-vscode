"""The on-demand investigator: a bounded agent for situations the deterministic supervisor was not designed for (H.5, H.8).

It reads one task's state (contract, attempts, diagnostics, logs, plan), optionally the researcher's free-text instruction or question, and returns
ONE structured outcome that this module validates and classifies with the existing intervention classifier (`intervene.py`):

* actions        -> unprotected ones run now as an intervention (approved under the researcher's own authority) and the reply says in one line what was
                    understood and created; protected ones become ONE separate intervention that waits for the PIN (`research intervention approve`).
                    Resume-type actions (unblock/retry/reverify) travel with the protected part so they run AFTER an approved amendment.
* clarify        -> it is ambiguous: ONE clarifying decision request with options (each a prepared action), never a guess.
* nothing usable -> a plain-text note asking the human, request left open.

Bounds: it never approves anything (this module never calls an approve function), its text is parsed against a closed vocabulary, scientific plans and
frozen tests can only change through the protected path, and spend is capped by `supervisor.investigator.{max_per_plan, budget_usd}`. Everything is an event
and is logged with its classification and outcome in `.research/supervisor/investigator.json`.

Automatic trigger (`supervisor.investigator.auto`): `maybe_auto` is the opt-in entry point; `notify.step` calls it from the halt watcher's worker thread
(`_auto_investigate`), never from the supervisor tick (a model call must never run under the supervisor lock).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import durability, registry
from .util import ResearchError, now_iso

RESUME = ("unblock", "retry", "reverify")
SAFE_AUTO = ("reverify", "note", "guide")      # what an automatic diagnosis may do by itself; anything else (retry, new tasks, edits) waits for a person

PROMPT = """You are the investigator for a research-automation supervisor. A researcher needs help with ONE task the deterministic rules cannot settle.
Read the task context below (and, if you need more, project files and logs: read only; do not edit anything, do not run experiments, do not approve anything).
Reply with ONE JSON object only:
{{"understood":"one sentence: what the researcher wants / what is wrong","summary":"one sentence: what you propose","rationale":"why",
  "actions":[...], "clarify":null}}
Allowed action types (anything else is rejected):
  {{"type":"unblock"}} | {{"type":"retry"}} | {{"type":"reverify"}} | {{"type":"guide","text":"what the next attempt must do differently"}} | {{"type":"note","text":"..."}}
  {{"type":"add_task","task":{{"key":"...","title":"...","prompt":"...","depends_on":[...],"agent":{{"role":"implementer"}},"checks":["file:..."]}}}}
  {{"type":"amend_task","fields":{{...}}}}  {{"type":"set_resources","fields":{{...}}}}  {{"type":"set_max_attempts","value":4}}  {{"type":"change_frozen_test","path":"...","reason":"..."}}
The last four and any change to acceptance/checks/inputs/datasets/dependencies/agent are PROTECTED: the researcher must approve them with a PIN. Never weaken a check
to make a task pass unless the researcher explicitly asked for that. Prefer the fewest changes. Do not invent results.
If the request is ambiguous, do NOT guess: set "actions" to [] and ask ONE question:
  "clarify":{{"question":"...","options":[{{"label":"short","consequence":"what happens","actions":[...]}}]}}   (2 to 4 options)

{ask}

TASK CONTEXT (JSON):
{context}
"""


# ----------------------------------------------------------------------------------------------- budget
def cfg(p) -> Dict[str, Any]:
    return registry.section(p, "supervisor")["investigator"]


def _path(p) -> str:
    return p.path("supervisor", "investigator.json")


def _load(p) -> Dict[str, Any]:
    try:
        with open(_path(p), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"runs": []}


def usage(p, plan_id: Optional[str] = None) -> Dict[str, Any]:
    runs = _load(p)["runs"]
    mine = [r for r in runs if not plan_id or r.get("plan_id") == plan_id]
    return {"runs": len(mine), "total_runs": len(runs), "spent_usd": round(sum(r.get("cost_usd") or 0 for r in runs), 4)}


def exhausted(p, plan_id: Optional[str]) -> Optional[str]:
    """Why the investigator may not run now (None = it may)."""
    c, u = cfg(p), usage(p, plan_id)
    if u["runs"] >= int(c["max_per_plan"]):
        return f"the investigator already ran {u['runs']} time(s) for this plan (supervisor.investigator.max_per_plan = {c['max_per_plan']})"
    if u["spent_usd"] >= float(c["budget_usd"]):
        return f"the investigator budget is used up (${u['spent_usd']} of ${c['budget_usd']}, supervisor.investigator.budget_usd)"
    return None


def _record(p, entry: Dict[str, Any]) -> None:
    from .store import _atomic_write
    with durability.lock(_path(p) + ".lock"):
        d = _load(p)
        d["runs"].append(entry)
        _atomic_write(_path(p), json.dumps(d, indent=1, sort_keys=True, default=str) + "\n")


# ----------------------------------------------------------------------------------------------- run
def _resolve_task(p, task: Optional[str], plan: Optional[str]) -> str:
    if task:
        return task
    if plan:
        from . import planspec as PS, supervisor as SV
        st = PS._state_for(p, plan)
        b = SV.plan_health(p, st, SV.load_run(p, st["plan_id"]) or {}).get("blocking") or []
        if b:
            return b[0]["task"]
        raise ResearchError(f"Nothing is blocked in plan {plan}", hint="Name a task: `research investigate T-12 \"question\"`.")
    raise ResearchError("Name a task or a plan to investigate")


def _plain(task: str, why: str) -> Dict[str, Any]:
    return {"status": "fallback", "task": task, "interventions": [], "decision": None, "cost_usd": 0,
            "summary": f"I cannot investigate now: {why}. Tell the supervisor what to do in plain words: research supervisor instruct {task} \"...\" (or reply in chat with /say {task} ...)."}


def investigate(p, task: Optional[str] = None, plan: Optional[str] = None, question: Optional[str] = None, text: Optional[str] = None,
                decision: Optional[str] = None, source: str = "investigator", fingerprint: Optional[str] = None, safe_only: bool = False) -> Dict[str, Any]:
    """Run the investigator once for `task` (or the first blocked task of `plan`). `question` = what to find out; `text` = the researcher's instruction
    (a free-text decision answer). Human-initiated only. Returns {status: done|clarify|fallback|failed, summary, interventions, decision, cost_usd}."""
    from . import decisions as D, intervene as I, supervisor as SV, taskprompt
    SV.require_human(p, "Running the investigator")
    tid = _resolve_task(p, task, plan)
    ctx = I.gather(p, tid)
    why = exhausted(p, ctx["plan_id"])
    if why:
        p.event("task", tid, "investigator_skipped", why)
        return _plain(tid, why)
    ask = (f"RESEARCHER INSTRUCTION:\n{text}" if text else "") + (f"\nQUESTION:\n{question}" if question else "") or "No instruction: diagnose the task and propose the fewest actions that unblock it."
    entry = {"ts": now_iso(), "plan_id": ctx["plan_id"], "task": tid, "decision": decision, "fingerprint": fingerprint, "text": taskprompt.redact(text or question or ""), "cost_usd": None}
    p.event("task", tid, "investigator_started", f"{(text or question or 'diagnose')[:160]}")
    try:
        r = I.run_assistant_full(p, f"Investigate {tid}", PROMPT.format(ask=ask, context=json.dumps(ctx, default=str, indent=1)[:24000]))
    except ResearchError as ex:
        p.event("task", tid, "investigator_failed", str(ex)[:300])
        return _plain(tid, f"the investigator agent did not run ({str(ex)[:160]})")
    u = r.get("usage")
    entry["cost_usd"] = u.get("cost_usd") if isinstance(u, dict) else None
    try:
        out = _apply(p, tid, ctx, I._json_from(r["result"]), text or question or "investigate", decision, source, entry, safe_only)
    except (ResearchError, ValueError) as ex:
        out = {"status": "failed", "task": tid, "interventions": [], "decision": None, "summary": f"The investigator's answer could not be used ({str(ex)[:200]}). Nothing was changed."}
        entry["error"] = str(ex)[:300]
    out["cost_usd"] = entry["cost_usd"]
    entry.update(status=out["status"], outcome=out["summary"])
    _record(p, entry)
    p.event("task", tid, "investigator_outcome", f"{out['status']}: {out['summary'][:300]}")
    return out


def _apply(p, tid: str, ctx: Dict[str, Any], raw: Any, instruction: str, decision: Optional[str], source: str, entry: Dict[str, Any], safe_only: bool = False) -> Dict[str, Any]:
    from . import decisions as D, intervene as I
    if not isinstance(raw, dict):
        raise ResearchError("not a JSON object")
    understood = str(raw.get("understood") or raw.get("summary") or "")[:300]
    clar = raw.get("clarify")
    if clar and not raw.get("actions"):
        opts = [{"key": str(i + 1), "label": o.get("label"), "consequence": o.get("consequence") or "",
                 "action": {"kind": "intervention", "task": tid, "instruction": str(o.get("label")), "actions": o.get("actions")}} for i, o in enumerate(clar.get("options") or []) if isinstance(o, dict)]
        d = D.request(p, str(clar.get("question") or "Which did you mean?"), opts, subject=tid, created_by="investigator")
        entry["clarifying_decision"] = d["id"]
        return {"status": "clarify", "task": tid, "interventions": [], "decision": d["id"],
                "summary": f"I was not sure what you meant ({understood or 'ambiguous'}), so I asked: {d['question']} Answer with research decide {d['id']} (options: " + ", ".join(o["key"] for o in d["options"]) + ")."}
    prop = I.validate_proposal({"summary": raw.get("summary") or understood, "rationale": raw.get("rationale"), "actions": raw.get("actions")})
    acts = prop["actions"]
    entry["plan"] = acts
    entry["classes"] = sorted({I.classify_action(a) for a in acts})
    prot = [a for a in acts if I.classify_action(a) in ("scientific", "resource", "frozen_test") or (safe_only and a["type"] not in SAFE_AUTO)]
    go = [a for a in acts if a not in prot]
    if prot:                       # resume-type actions wait with the protected part so they run after an approved amendment
        held = [a for a in go if a["type"] in RESUME]
        go, prot = [a for a in go if a not in held], prot + held
    made: List[Dict[str, Any]] = []
    src = f"{source} {decision}" if decision else source
    if go:
        made.append(I.create_planned(p, tid, instruction, go, prop["summary"], prop["rationale"], source=src))
    if prot:
        made.append(I.create_planned(p, tid, instruction, prot, prop["summary"], prop["rationale"], source=src, allow_open=bool(go), hold=safe_only))
    entry["interventions"] = [i["id"] for i in made]
    created = [f"{a['task']['key']}" for a in go if a["type"] == "add_task"]
    parts = [f"Understood: {understood or prop['summary']}."]
    for it in made:
        if it["state"] == "proposed":
            parts.append(f"Waiting for your PIN ({', '.join(it['protected'])}): {it['id']} ({'; '.join(x.split('] ', 1)[-1] for x in it['preview'])[:200]}). Approve at the computer: research intervention approve {it['id']}")
        else:
            parts.append(f"Doing now: {it['id']} ({'; '.join(x.split('] ', 1)[-1] for x in it['preview'])[:200]})" + (f"; adding tasks {', '.join(created)}" if created else "") + ".")
    return {"status": "done", "task": tid, "interventions": [i["id"] for i in made], "decision": None, "summary": " ".join(parts)}


def handle_text(p, d: Dict[str, Any], text: str, via: str) -> Dict[str, Any]:
    """A free-text answer to decision request `d` (called by `decisions.answer`): the investigator turns it into an action plan.
    `reopen: True` in the result keeps the request open (budget used up, agent unavailable, unusable answer): the human is told in plain text."""
    from . import decisions as D
    out = investigate(p, task=D._task_of(p, d), text=text, decision=d["id"])
    return {"summary": out["summary"], "reopen": out["status"] in ("fallback", "failed"), "refs": {"interventions": out["interventions"], "clarifying_decision": out["decision"]}}


def maybe_auto(p, task: str, fingerprint: str) -> Optional[Dict[str, Any]]:
    """Opt-in automatic trigger for an unclassified failure: runs the investigator once per (task, fingerprint) when `supervisor.investigator.auto` is true.
    Call it from the halt watcher's worker thread, never from the supervisor tick."""
    if not cfg(p)["auto"]:
        return None
    if any(r.get("task") == task and r.get("fingerprint") == fingerprint for r in _load(p)["runs"]):
        return None
    from . import supervisor as SV
    p.event("task", task, "investigator_auto", "supervisor.investigator.auto: investigating a failure the rules could not settle")
    # `auto: true` is the researcher's standing opt-in for this one read-mostly run; protected parts of the outcome still wait for the PIN and nothing here approves.
    with SV.human_grant("supervisor.investigator.auto"):
        return investigate(p, task=task, fingerprint=fingerprint, safe_only=True,
                           question="This task failed and the rules could not settle why. Use `evidence` (run records, numerical metrics, logs, artifact paths), the checks in `attempts` "
                                    "and `recovery`. If the cause is results that were late, answer with a single reverify. Never propose relaxing a threshold, changing a scientific "
                                    "assumption, raising a budget or submitting a job. Diagnose it and propose the fewest actions.")
