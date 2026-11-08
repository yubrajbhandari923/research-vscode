"""Decision requests: whenever the system needs a human decision it asks ONE structured question, stored in the project, answerable from any surface.

    {id, status: open|answering|answered|expired, subject: {type, id}, question, options: [{key, label, consequence, effect, action, protected}],
     default, expires, created_by, requested_from_chat, answer: {by, via, key|text, ts, outcome}}

* Agents (and the investigator) may CREATE a request (`request`); only a human answers (`answer`). An agent cannot mark an option unprotected:
  `protected` is computed here from the action, never taken from the creator.
* An option carries a *prepared action* that runs deterministically when chosen (no model call): an `intervention` (the existing action
  vocabulary and classifier, `intervene.py`), an `op` from a small allow-list, an `instruct` (natural-language instruction the existing
  intervention planner turns into actions: what today's diagnosis options are), or nothing (`note`).
* Protected options (science, resources/budget, frozen tests, roles, promotion) never run from chat: the request is flagged
  `requested_from_chat` and waits for the PIN flow at the computer. Locally they need the PIN like any protected action.
* Answering twice, from two surfaces, is safe: the first closes the request, the second gets that answer back (`already: true`).
* Free text goes to the investigator (`investigator.py`) which turns it into an action plan (unprotected parts run, protected parts wait for the PIN,
  ambiguity becomes one clarifying request); with no budget or no agent the request stays open and the reply is a plain-text note.

Storage follows proposals: `.research/supervisor/requests/DR-NNN.json` (no schema entry: plain JSON beside proposals and interventions).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Optional

from . import durability
from .util import PolicyBlocked, ResearchError, now_iso

MAX_OPTIONS, STALE_ANSWERING_S = 8, 600
PROTECTED_OPS = {"plan_promote"}
# op -> (parameter that selects the behaviour, values that are safe without the PIN). Anything else is refused at creation.
SAFE_OPS = {"plan_control": ("action", {"run", "pause", "resume"}), "supervisor_task": ("action", {"approve", "continue", "retry", "unblock", "completed", "failed"})}
PROTECTED_VALUES = {"plan_control": {"stop"}}


# ----------------------------------------------------------------------------------------------- storage
def _dir(p) -> str:
    d = p.path("supervisor", "requests")
    os.makedirs(d, exist_ok=True)
    return d


def _lock(p):
    return durability.lock(os.path.join(_dir(p), ".lock"))


def _file(p, did: str) -> str:
    if not re.fullmatch(r"DR-\d{3,}", did or ""):
        raise ResearchError(f"Bad decision request id {did!r}", hint="`research decisions`")
    return os.path.join(_dir(p), did + ".json")


def load(p, did: str) -> Dict[str, Any]:
    try:
        with open(_file(p, did), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ResearchError(f"No decision request {did}", hint="`research decisions`")


def save(p, d: Dict[str, Any]) -> None:
    from .store import _atomic_write
    d["updated"] = now_iso()
    _atomic_write(_file(p, d["id"]), json.dumps(d, indent=1, sort_keys=True, default=str) + "\n")


def _expired(d: Dict[str, Any]) -> bool:
    try:
        return bool(d.get("expires")) and _dt.datetime.fromisoformat(d["expires"]) < _dt.datetime.now(_dt.timezone.utc)
    except ValueError:
        return False


def list_all(p, status: Optional[str] = "open", task: Optional[str] = None) -> List[Dict[str, Any]]:
    """`status`: open (default; expired ones are closed on the way), answered, expired, or None for all."""
    out = []
    for n in sorted(os.listdir(_dir(p))):
        if n.startswith("DR-") and n.endswith(".json"):
            d = load(p, n[:-5])
            if d["status"] == "open" and _expired(d):
                d = _close_expired(p, d)
            if (status is None or d["status"] == status or (status == "open" and d["status"] == "answering" and not _stale(d))) and (not task or (d["subject"] or {}).get("id") == task):
                out.append(d)
    return out


def _stale(d: Dict[str, Any]) -> bool:
    try:
        return (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(d["answering"]["ts"])).total_seconds() > STALE_ANSWERING_S
    except (KeyError, ValueError, TypeError):
        return True


# ----------------------------------------------------------------------------------------------- validation
def _subject(s: Any) -> Dict[str, Optional[str]]:
    if isinstance(s, dict) and s.get("id"):
        return {"type": str(s.get("type") or _kind_of(str(s["id"]))), "id": str(s["id"])}
    if isinstance(s, str) and s.strip():
        return {"type": _kind_of(s.strip()), "id": s.strip()}
    return {"type": None, "id": None}


def _kind_of(i: str) -> str:
    return "task" if i.startswith("T-") else "proposal" if i.startswith("PROP-") else "run" if i.startswith("RUN-") else "plan"


def validate_action(a: Any) -> Dict[str, Any]:
    """Parse a prepared action; returns it normalized with `protected` (computed, never trusted) and `effect` (what a tap does, one line per step)."""
    from . import api, intervene as I
    if a in (None, {}, "none"):
        return {"action": None, "protected": False, "effect": ["note the answer; nothing changes"]}
    if not isinstance(a, dict):
        raise ResearchError("An option's action must be an object")
    kind = a.get("kind")
    if kind == "intervention":
        task = str(a.get("task") or "")
        if not task:
            raise ResearchError("An intervention action needs the task id")
        acts = I.validate_proposal({"actions": a.get("actions")})["actions"]
        return {"action": {"kind": kind, "task": task, "instruction": str(a.get("instruction") or "")[:2000], "actions": acts},
                "protected": bool(I.protected(acts)), "effect": I.preview(None, {"actions": acts})}
    if kind == "instruct":
        if not a.get("task") or not str(a.get("instruction") or "").strip():
            raise ResearchError("An instruct action needs task and instruction")
        return {"action": {"kind": kind, "task": str(a["task"]), "instruction": str(a["instruction"])[:2000]}, "protected": bool(a.get("protected")),
                "effect": [f"the supervisor plans from: {str(a['instruction'])[:160]} (anything protected still waits for your PIN)"]}
    if kind == "op":
        name, params = str(a.get("op") or ""), a.get("params") or {}
        if name not in api.OPS or not isinstance(params, dict) or not (name in SAFE_OPS or name in PROTECTED_OPS):
            raise ResearchError(f"{name!r} cannot be a prepared action", hint="Allowed ops: " + ", ".join(sorted(set(SAFE_OPS) | PROTECTED_OPS)))
        prot = name in PROTECTED_OPS
        if name in SAFE_OPS:
            key, safe = SAFE_OPS[name]
            v = params.get(key)
            if v in PROTECTED_VALUES.get(name, ()):
                prot = True
            elif v not in safe:
                raise ResearchError(f"{name} {key}={v!r} cannot be a prepared action", hint="Allowed: " + ", ".join(sorted(safe)))
        return {"action": {"kind": kind, "op": name, "params": params}, "protected": prot, "effect": [f"run {name} " + json.dumps(params, sort_keys=True, default=str)[:160]]}
    raise ResearchError(f"Unknown action kind {kind!r}", hint="intervention | instruct | op")


def _options(options: Any) -> List[Dict[str, Any]]:
    if not isinstance(options, list) or not options:
        raise ResearchError("A decision request needs at least one option")
    if len(options) > MAX_OPTIONS:
        raise ResearchError(f"At most {MAX_OPTIONS} options")
    out, seen = [], set()
    for i, o in enumerate(options):
        if not isinstance(o, dict) or not str(o.get("label") or "").strip():
            raise ResearchError("Every option needs a label")
        key = str(o.get("key") or i + 1).strip()
        if key in seen or not re.fullmatch(r"[A-Za-z0-9_-]{1,12}", key):
            raise ResearchError(f"Option key {key!r} is duplicated or invalid")
        seen.add(key)
        v = validate_action(o.get("action"))
        out.append({"key": key, "label": str(o["label"]).strip()[:120], "consequence": str(o.get("consequence") or "")[:400], "effect": v["effect"],
                    "action": v["action"], "protected": v["protected"] or bool(o.get("protected"))})
    return out


# ----------------------------------------------------------------------------------------------- create
def request(p, question: str, options: List[Dict[str, Any]], subject: Any = None, default: Optional[str] = None, expires_hours: float = 72,
            created_by: Optional[str] = None, fingerprint: Optional[str] = None) -> Dict[str, Any]:
    """Ask the human one question. An open request with the same subject and fingerprint (or question) is returned instead of a duplicate."""
    if not str(question or "").strip():
        raise ResearchError("A decision request needs a question")
    subj, opts = _subject(subject), _options(options)
    if default is not None and str(default) not in {o["key"] for o in opts}:
        raise ResearchError("default must be one of the option keys")
    author = p.author or {}
    who = created_by or (f"agent:{author.get('author_name') or 'agent'}" if author.get("author_type") == "agent" else "supervisor")
    fp = fingerprint or question.strip()[:200]
    with _lock(p):
        for d in list_all(p):
            if d["subject"] == subj and d.get("fingerprint") == fp:
                return d
        n = max([int(re.match(r"DR-(\d+)", x).group(1)) for x in os.listdir(_dir(p)) if re.match(r"DR-\d+\.json$", x)] + [0]) + 1
        d = {"id": f"DR-{n:03d}", "status": "open", "created": now_iso(), "created_by": who, "subject": subj, "question": question.strip()[:2000],
             "options": opts, "default": None if default is None else str(default), "fingerprint": fp,
             "expires": (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=float(expires_hours))).isoformat(timespec="seconds") if expires_hours else None,
             "requested_from_chat": None, "answer": None}
        save(p, d)
    p.event("task" if subj["type"] == "task" else "plan", subj["id"], "decision_requested", f"{d['id']} by {who}: {d['question'][:200]}")
    return d


# ----------------------------------------------------------------------------------------------- answer
def _close_expired(p, d: Dict[str, Any]) -> Dict[str, Any]:
    d["status"] = "expired"
    save(p, d)
    p.event("task" if (d["subject"] or {}).get("type") == "task" else "plan", (d["subject"] or {}).get("id"), "decision_expired", f"{d['id']} expired unanswered (nothing was chosen)")
    return d


def expire(p, did: Optional[str] = None) -> List[Dict[str, Any]]:
    """Close one request without choosing anything (or, with no id, every open request past its deadline). Never picks a default."""
    from . import supervisor as SV
    SV.require_human(p, "Closing a decision request")
    out = []
    with _lock(p):
        ds = [load(p, did)] if did else [d for d in list_all(p, None) if d["status"] == "open" and _expired(d)]
        for d in ds:
            if d["status"] == "open":
                out.append(_close_expired(p, d))
    return out


def answer(p, did: str, key: Optional[str] = None, text: Optional[str] = None, via: str = "local", note: Optional[str] = None,
           pin: Optional[str] = None) -> Dict[str, Any]:
    """Answer with an option key or free text (exactly one). Human only; `via` names the surface ("local", "tui", "vscode", "chat:telegram"...).
    Returns the request with `outcome` (what was understood/done, one line); `already: true` when another surface answered first;
    `needs_local: true` when a protected option was chosen from chat (nothing ran; the request stays open for the PIN flow)."""
    from . import approval, supervisor as SV
    SV.require_human(p, "Answering a decision request")
    if approval.agent_environment():
        raise PolicyBlocked("Answering a decision request is a human decision: agents cannot do it" + approval._env_hint())
    if (key is None) == (not (text or "").strip()):
        raise ResearchError("Answer with an option key OR free text")
    chat = str(via).startswith("chat")
    who = (p.author or {}).get("author_name") or os.environ.get("RESEARCH_AUTHOR") or "human"
    with _lock(p):
        d = load(p, did)
        if d["status"] == "open" and _expired(d):
            d = _close_expired(p, d)
        if d["status"] == "expired":
            raise ResearchError(f"{did} expired; nothing was chosen", hint="Instruct the supervisor directly (`research supervisor instruct T-1 --message \"...\"`).")
        if d["status"] == "answered" or (d["status"] == "answering" and not _stale(d)):
            return {**d, "already": True, "outcome": (d.get("answer") or {}).get("outcome") or "being answered from another surface"}
        opt = None
        if key is not None:
            opt = next((o for o in d["options"] if o["key"].lower() == str(key).lower()), None)
            if opt is None:
                raise ResearchError(f"No option {key!r} in {did}", hint="Options: " + ", ".join(o["key"] for o in d["options"]))
            if opt["protected"] and chat:
                d["requested_from_chat"] = {"via": via, "by": who, "key": opt["key"], "ts": now_iso()}
                save(p, d)
                p.event("task" if d["subject"]["type"] == "task" else "plan", d["subject"]["id"], "decision_needs_local",
                        f"{did} option {opt['key']} changes protected things; {who} asked from {via}: it waits for your PIN at the computer")
                return {**d, "needs_local": True, "outcome": f"Option {opt['key']} ({opt['label']}) needs your PIN at the computer: research decide {did} {opt['key']}"}
        d.update(status="answering", answering={"by": who, "via": via, "ts": now_iso()})
        save(p, d)
    try:
        outcome = _run_option(p, d, opt, via, note, pin) if opt else _run_text(p, d, text.strip(), via, who)
        if outcome.get("reopen"):          # nothing was done (no budget, no agent, unusable answer): the plain-text note is the reply, the request stays open
            with _lock(p):
                d.update(status="open", answering=None)
                save(p, d)
            return {**d, "needs_text": True, "outcome": outcome["summary"]}
    except BaseException:
        with _lock(p):
            d.update(status="open", answering=None)
            save(p, d)
        raise
    with _lock(p):
        d.update(status="answered", answering=None, answer={"by": who, "via": via, "key": opt["key"] if opt else None, "text": None if opt else text.strip()[:4000],
                                                              "label": opt["label"] if opt else None, "ts": now_iso(), "outcome": outcome["summary"], "refs": outcome.get("refs") or {}})
        save(p, d)
    p.event("task" if d["subject"]["type"] == "task" else "plan", d["subject"]["id"], "decision_answered",
            f"{did} answered by {who} via {via}: " + (f"option {opt['key']} ({opt['label']})" if opt else "free text") + f" -> {outcome['summary'][:200]}")
    return {**d, "outcome": outcome["summary"]}


def _run_option(p, d, opt, via, note, pin) -> Dict[str, Any]:
    """Run the prepared action deterministically. Protected + locally: the PIN gate (`approval.confirm`, or `intervene.approve` for interventions)."""
    from . import api, approval, intervene as I
    a = opt["action"]
    if a is None:
        return {"summary": f"Noted: {opt['label']}. Nothing was changed."}
    if a["kind"] == "op":
        if opt["protected"]:
            approval.confirm(p, f"Decision {d['id']}: {opt['label']}", note, pin)
        res = api.OPS[a["op"]].fn(p, **a["params"])
        return {"summary": f"{opt['label']}: done ({a['op']})", "refs": {"op": a["op"]}, "result": res if isinstance(res, (dict, list, str, int, float, bool, type(None))) else None}
    if a["kind"] == "intervention":
        it = I.create_planned(p, a["task"], a.get("instruction") or opt["label"], a["actions"], summary=opt["label"], rationale=opt["consequence"], source=f"decision {d['id']}")
        if it["state"] == "proposed" and it["protected"]:
            try:
                it = I.approve(p, it["id"], note or f"decision {d['id']}: {opt['label']}", pin)
            except BaseException:
                I.cancel(p, it["id"])        # a refused PIN leaves nothing half-created: the request reopens and can be answered again
                raise
        return {"summary": f"{it['id']}: {opt['label']} ({it['state']})", "refs": {"intervention": it["id"]}}
    it = I.request(p, a["task"], a["instruction"], source=f"decision {d['id']}")
    return {"summary": _intervention_line(it), "refs": {"intervention": it["id"]}}


def _intervention_line(it: Dict[str, Any]) -> str:
    if it["state"] == "approved":
        return f"{it['id']}: {it['summary']} Applied under your instruction; the supervisor is resuming {it['task']}."
    if it["state"] == "proposed":
        return (f"{it['id']}: {it['summary']} This changes {', '.join(it['protected'])} and waits for your PIN at the computer: "
                f"research intervention approve {it['id']}")
    return f"{it['id']} {it['state']}: {(it.get('history') or [{}])[-1].get('note', '')}"


def _run_text(p, d, text, via, who) -> Dict[str, Any]:
    """Free text goes to the investigator, which classifies what it proposes with the intervention classifier (investigator.py)."""
    from . import investigator
    return investigator.handle_text(p, d, text, via)


def _task_of(p, d) -> str:
    s = d["subject"] or {}
    if s.get("type") == "task":
        return s["id"]
    from . import planspec as PS, supervisor as SV
    if s.get("type") == "plan":
        st = PS._state_for(p, s["id"])
        b = SV.plan_health(p, st, SV.load_run(p, st["plan_id"]) or {}).get("blocking") or []
        if b:
            return b[0]["task"]
    raise ResearchError("A text answer needs a task to act on", hint="Pick an option, or name the task: `research supervisor instruct T-1 --message \"...\"`.")


# ----------------------------------------------------------------------------------------------- the diagnosis options (/options, /choose)
def from_diagnosis(p, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A finished debug record (`notify.run_debug`) as a decision request: each option instructs the supervisor on the first blocked task.
    Scientific and resource options are protected (they never run from chat). None when the record has no options."""
    if rec.get("state") != "done" or not rec.get("options") or not rec.get("tasks"):
        return None
    tid = rec["tasks"][0]
    opts = [{"key": str(i + 1), "label": o["label"], "consequence": (o["instruction"] + (" (recommended)" if rec.get("recommended") == i else ""))[:400],
             "action": {"kind": "instruct", "task": tid, "instruction": o["instruction"], "protected": o.get("class") in ("scientific", "resource")}}
            for i, o in enumerate(rec["options"])]
    default = None if rec.get("recommended") is None else str(rec["recommended"] + 1)
    return request(p, rec.get("summary") or f"Plan {rec.get('plan')} is halted: what should happen?", opts, subject=tid, default=default, created_by="debug agent",
                   fingerprint=rec.get("fingerprint"))


def latest_open(p) -> Optional[Dict[str, Any]]:
    ds = list_all(p)
    return ds[-1] if ds else None


def choose(p, n: Any, via: str = "local", **kw: Any) -> Dict[str, Any]:
    """`/choose N`: option N of the latest open request."""
    d = latest_open(p)
    if not d:
        raise ResearchError("No open decision request", hint="`research decisions`")
    return answer(p, d["id"], key=str(n), via=via, **kw)


def text_for(d: Dict[str, Any]) -> str:
    """Plain text for a chat message or terminal (options with what each does; protected ones marked)."""
    lines = [f"{d['id']}: {d['question']}"]
    for o in d["options"]:
        lines.append(f"{o['key']}. {o['label']}" + (" [needs your computer]" if o["protected"] else "") + (" (default)" if d.get("default") == o["key"] else "")
                     + (f"\n   {o['consequence']}" if o["consequence"] else "") + "".join(f"\n   - {e}" for e in o["effect"][:3]))
    return "\n".join(lines) + f"\n\nAnswer with an option key, or reply in text. (research decide {d['id']} KEY|\"text\")"
