"""Chat ops. Two families:

* `chat_cmd_*`: the commands a paired user can run from Telegram or Discord (E.8). `chat="read"` = viewer and up, `"decide"` = operator.
  They return `{"text", "files"?, "actions"?}`; the router renders them. Anything PIN-protected has NO op here: chat refuses it.
* `chat_status`, `chat_setup`, `chat_pair`, ... and the `telegram_*` aliases: configuration, for the CLI and the extension (never chat).
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, List, Optional

from ..util import ResearchError
from . import op


def _plans(p):
    from .. import notify
    return notify._plans(p)


def _text(t: str, **kw: Any) -> Dict[str, Any]:
    return {"text": t, **kw}


# ----------------------------------------------------------------------------------------------- read (viewer)
@op(sync=False, chat="read")
def chat_cmd_status(p) -> Dict[str, Any]:
    from .. import supervisor as SV
    rows = []
    for st, rs in _plans(p):
        h = SV.plan_health(p, st, rs)
        s = SV.status(p, st["plan_key"])
        rows.append(f"{st['plan_key']}: {h['label']} · {s['progress']} verified · {s['concurrency']['running']} running"
                    + (f" · blocked: {', '.join(b['task'] for b in h['blocking'])}" if h["blocking"] else ""))
    return _text("\n".join(rows) or "No plan has been run.")


@op(sync=False, chat="read")
def chat_cmd_blocked(p) -> Dict[str, Any]:
    from .. import notify
    b = notify._blocked(p)
    return _text("\n".join(f"{x['task']} {x['title'][:40]} [{x['phase']}]\n  {(x.get('reason') or '')[:240]}" for x in b) or "Nothing is blocked.")


@op(sync=False, chat="read")
def chat_cmd_task(p, task: str) -> Dict[str, Any]:
    from .. import intervene as I
    g = I.gather(p, task)
    return _text(f"{g['task']} {g['title']}\nphase {g['phase']} · {len(g['attempts'])} attempt(s)\n{(g['reason'] or '')[:400]}")


@op(sync=False, chat="read")
def chat_cmd_logs(p, task: str, stream: str = "") -> Dict[str, Any]:
    """The last log lines; more than 1,500 characters travel as an attachment, never inline."""
    from .. import intervene as I
    logs = I.gather(p, task)["logs"] or ""
    if stream == "stderr":
        logs = "\n".join(l for l in logs.splitlines() if "stderr" in l.lower()) or logs
    if len(logs) <= 1500:
        return _text(logs or "(no log yet)")
    out = p.path("supervisor", "chat", f"{task}.log")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(logs)
    return _text(f"{task}: last {len(logs)} characters attached.\n" + logs[-300:], files=[out])


@op(chat="read")
def chat_cmd_report(p, scope: str = "") -> Dict[str, Any]:
    from .. import report
    r = report.write_report(p, scope or None, "md")
    files = [x for x in r["paths"] if os.path.isfile(x)][:1]
    if files:
        base = os.path.dirname(files[0])
        with open(files[0], encoding="utf-8", errors="replace") as f:
            for ref in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", f.read())[:4]:
                q = os.path.normpath(os.path.join(base, ref))
                if os.path.isfile(q) and os.path.getsize(q) < 5 * 1024 * 1024:
                    files.append(q)
    return _text(f"{r.get('title') or 'Report'}: {len(files)} file(s) attached.", files=files)


@op(sync=False, chat="read")
def chat_cmd_finding(p, id: str) -> Dict[str, Any]:
    f = p.get("finding", id)
    ev = f.get("evidence") or f.get("evidence_ids") or []
    return _text(f"{f['id']} {f.get('title') or ''}\n{(f.get('statement') or f.get('claim') or '')[:600]}\nstatus: {f.get('status')} · evidence: {', '.join(map(str, ev)) or 'none'}")


@op(sync=False, chat="read")
def chat_cmd_usage(p, period: str = "today") -> Dict[str, Any]:
    from .. import usage
    from datetime import datetime, timedelta, timezone
    days = {"today": 1, "week": 7}.get(period)
    if days is None:
        raise ResearchError("usage period: today or week")
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    rows: Dict[str, Dict[str, Any]] = {}
    for r in usage.records(p):
        if str(r.get("ts") or r.get("at") or "") < cut:
            continue
        g = rows.setdefault(f"{r.get('provider') or 'unknown'}/{r.get('model') or 'unknown'}", {"n": 0, "cost": None, "tokens": None})
        g["n"] += 1
        if isinstance(r.get("cost_usd"), (int, float)):
            g["cost"] = (g["cost"] or 0) + r["cost_usd"]
        t = (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) if isinstance(r.get("input_tokens"), (int, float)) else None
        if t is not None:
            g["tokens"] = (g["tokens"] or 0) + t
    if not rows:
        return _text(f"No usage recorded ({period}).")
    return _text("\n".join(f"{k}: {g['n']} run(s), cost " + (f"${g['cost']:.2f}" if g["cost"] is not None else "unavailable")
                           + ", tokens " + (str(g["tokens"]) if g["tokens"] is not None else "unavailable") for k, g in sorted(rows.items())))


@op(sync=False, chat="read")
def chat_cmd_proposals(p) -> Dict[str, Any]:
    from .. import supervisor as SV
    from ..chat import commands as C
    pend = [x for x in SV.list_proposals(p) if x.get("status") == "pending"]
    acts: List[Dict[str, Any]] = []
    for x in pend[:4]:
        acts.append({"label": f"Reject {x['id']}", "op": "chat_cmd_reject", "params": {"kind": "proposal", "id": x["id"]}, "style": "danger"})
        acts += C.card_actions(p, "proposal", x["id"])
    return _text("\n".join(f"{x['id']}: {(x.get('rationale') or '')[:140]}" for x in pend) + ("\n\nApproving needs your computer (PIN)." if pend else "") or "No proposals waiting.",
                 actions=acts)


@op(sync=False, chat="read")
def chat_cmd_interventions(p) -> Dict[str, Any]:
    from .. import intervene as I
    items = I.list_all(p)[-8:]
    acts = [{"label": f"Reject {i['id']}", "op": "chat_cmd_reject", "params": {"kind": "intervention", "id": i["id"]}, "style": "danger"} for i in items if i["state"] == "proposed"][:4]
    return _text("\n".join(f"{i['id']} {i['task']} {i['state']}: {i['instruction'][:70]}" for i in items) or "None.", actions=acts)


@op(sync=False, chat="read")
def chat_cmd_decisions(p) -> Dict[str, Any]:
    from .. import decisions as D
    from ..chat import commands as C
    ds = D.list_all(p)
    if not ds:
        return _text("No open decision requests.")
    return _text("\n\n".join(D.text_for(d) for d in ds[:3]), actions=[a for d in ds[:3] for a in C.decision_actions(d)][:8])


@op(mutates=True, chat="approve")
def chat_approve_proposal(p, id: str, content_hash: str, code: str, platform: str, user_id: str, role: str = "approver") -> Dict[str, Any]:
    """H.9: approve one proposal from chat with an authenticator code (never the PIN). `approval.confirm_chat` enforces every rule: enrolled,
    enabled, approver role, an allowed action class, the card's content hash, a fresh unused code, the shared failure counter."""
    from .. import approval
    approval.approve_proposal_from_chat(p, id, content_hash, code, platform, user_id, role)
    return _text(f"{id} approved via {platform} (authenticator).")


@op(sync=False, chat="read")
def chat_cmd_digest(p) -> Dict[str, Any]:
    import time
    from ..chat import digest
    return _text(digest.build(p, time.time() - 86400) or "Nothing changed in the last 24 hours.")


@op(sync=False, chat="read")
def chat_cmd_options(p) -> Dict[str, Any]:
    from .. import decisions as D, notify
    from ..chat import commands as C
    d = D.latest_open(p)
    if d:                      # the decision store first; the old diagnosis record is the fallback
        return _text(D.text_for(d), actions=C.decision_actions(d))
    d = notify.latest_debug(p)
    return _text(notify.message_for(d, {"plan": d["plan"], "blocking": [{"task": t, "title": "", "phase": "blocked"} for t in d["tasks"]]}) if d else "No diagnosis yet. Try /debug.")


# ----------------------------------------------------------------------------------------------- steer and refuse (operator)
def _live(p) -> List[Dict[str, Any]]:
    """Tasks a plan is working on or waiting on a human for: id, title, phase, plan, attempts, reason."""
    from .. import supervisor as SV
    out = []
    for st, rs in _plans(p):
        for tid, e in (rs.get("tasks") or {}).items():
            if e.get("phase") in SV.ACTIVE_PHASES + SV.HUMAN_PHASES and p.exists("task", tid):
                out.append({"task": tid, "title": p.get("task", tid)["title"], "phase": e["phase"], "plan": st["plan_key"],
                            "attempts": len(e.get("attempts") or []), "reason": e.get("reason"), "human": e["phase"] in SV.HUMAN_PHASES})
    return out


def _task_line(t: Dict[str, Any]) -> str:
    return f"{t['task']} {t['title'][:50]} [{t['phase']}" + (f", attempt {t['attempts']}" if t["attempts"] else "") + f"] ({t['plan']})"


def _instruct(p, task: Optional[str], text: str, via: str) -> Dict[str, Any]:
    from .. import intervene as I
    if task is None:
        live = _live(p)
        needs = [t for t in live if t["human"]]
        pick = needs if needs else live          # one task needs you: that one; else the only running one
        if len(pick) != 1:
            ex = (pick or live or [{"task": "T-12"}])[0]["task"]
            raise ResearchError("Which task? " + ("Needs you: " + "; ".join(_task_line(t) for t in needs) + ". " if needs else "")
                                + ("Running: " + "; ".join(_task_line(t) for t in live if not t["human"]) + ". " if any(not t["human"] for t in live) else "")
                                + ("No task is running or waiting. " if not live else "")
                                + f"Say it as /say {ex} <instruction>, or type in that task's thread.")
        task = pick[0]["task"]
    it = I.request(p, task, text, source=via)
    if it["state"] == "approved":
        return _text(f"{it['id']}: {it['summary']}\nApplied under your instruction; the supervisor is resuming {task}.")
    if it["state"] == "proposed":
        return _text(f"{it['id']}: {it['summary']}\nThis changes " + ", ".join(it["protected"]) + " and needs your approval with the PIN:\n"
                     f"  research intervention approve {it['id']}\n(or in VS Code → Plans → {task} → Instruct).")
    return _text(f"{it['id']} {it['state']}: {(it.get('history') or [{}])[-1].get('note', '')}")


@op(sync=False, chat="read")
def chat_cmd_tasks(p) -> Dict[str, Any]:
    live = _live(p)
    if not live:
        return _text("No task is running or waiting for you. /status shows the plans.")
    lines = []
    needs = [t for t in live if t["human"]]
    if needs:
        lines.append("Needs you:")
        lines += [f"  {_task_line(t)}" + (f"\n      {str(t['reason'])[:200]}" if t.get("reason") else "") + f"\n      /say {t['task']} <instruction> · /logs {t['task']}" for t in needs]
    run = [t for t in live if not t["human"]]
    if run:
        lines.append("Running:")
        lines += [f"  {_task_line(t)}\n      /task {t['task']} · /logs {t['task']} · /stop {t['task']}" for t in run]
    return _text("\n".join(lines))


@op(mutates=True, chat="decide")
def chat_cmd_say(p, text: str, task: Optional[str] = None, via: str = "chat") -> Dict[str, Any]:
    m = re.match(r"(T-\d+)\s+(.*)", text, re.S)
    if m and task is None:
        task, text = m.group(1), m.group(2)
    return _instruct(p, task, text, via)


@op(mutates=True, chat="decide")
def chat_cmd_choose(p, n: str, via: str = "chat") -> Dict[str, Any]:
    from .. import decisions as D, notify
    if D.latest_open(p):
        r = D.choose(p, n, via=f"chat:{via}")
        return _text(r.get("outcome") or "Done.")
    d = notify.latest_debug(p)
    if not d or not str(n).isdigit() or not 1 <= int(n) <= len(d.get("options") or []):
        raise ResearchError("Choose a number from /options.")
    return _instruct(p, d["tasks"][0], d["options"][int(n) - 1]["instruction"], via)


@op(sync=False, chat="decide")
def chat_cmd_debug(p) -> Dict[str, Any]:
    from .. import notify, supervisor as SV
    plan = next((st["plan_key"] for st, rs in _plans(p) if SV.plan_health(p, st, rs)["blocking"]), None)
    if not plan:
        return _text("Nothing is blocked.")
    threading.Thread(target=notify._debug_and_alert, args=(p.root, plan, SV.plan_health(p, *notify._plan_pair(p, plan))), daemon=True).start()
    return _text("Diagnosing; I will message you when it is done.")


def _plan_to_change(p, cmd: str, plan: Optional[str]) -> str:
    states = ("running",) if cmd == "pause" else ("paused", "blocked", "attention")
    cand = [st["plan_key"] for st, rs in _plans(p) if rs.get("state") in states]
    if plan:
        return plan
    if len(cand) != 1:
        raise ResearchError("Name exactly one plan state to change: " + (", ".join(cand) or "none applies"))
    return cand[0]


@op(mutates=True, chat="decide")
def chat_cmd_pause(p, plan: Optional[str] = None) -> Dict[str, Any]:
    from .. import supervisor as SV
    k = _plan_to_change(p, "pause", plan)
    SV.plan_pause(p, k)
    return _text(f"{k} paused.")


@op(mutates=True, chat="decide")
def chat_cmd_resume(p, plan: Optional[str] = None) -> Dict[str, Any]:
    from .. import supervisor as SV
    k = _plan_to_change(p, "resume", plan)
    SV.plan_resume(p, k)
    return _text(f"{k} resumed.")


@op(mutates=True, chat="decide")
def chat_cmd_stop(p, task: str) -> Dict[str, Any]:
    """Stop the task's current attempt; the supervisor retries or escalates by policy. Stopping the plan or cancelling agents is local only."""
    from .. import execution as EX
    run = [e for e in EX.list_all(p, task) if e["state"] == "running" and e.get("alive")]
    if not run:
        raise ResearchError(f"{task} has no running attempt")
    EX.stop(p, run[0]["id"])
    return _text(f"Stop requested for {task}; the supervisor decides what happens next.")


@op(mutates=True, chat="decide")
def chat_cmd_continue(p, task: str, profile: str) -> Dict[str, Any]:
    from .. import supervisor as SV
    SV.continue_with(p, task, profile, None)
    return _text(f"{task} handed to {profile}.")


@op(mutates=True, chat="decide")
def chat_cmd_approve_start(p, task: str) -> Dict[str, Any]:
    from .. import supervisor as SV
    SV.approve_task(p, task, "approved to start from chat")
    return _text(f"{task} may start.")


@op(mutates=True, chat="decide")
def chat_cmd_reject(p, kind: str, id: str) -> Dict[str, Any]:
    """The always-safe direction: reject a proposal or an intervention. Approving is never an op of this family."""
    from .. import intervene as I, supervisor as SV
    if kind == "proposal":
        SV.decide_proposal(p, id, False, "rejected from chat", None, None)
    elif kind == "intervention":
        I.reject(p, id, "rejected from chat")
    else:
        raise ResearchError("reject a proposal or an intervention")
    return _text(f"{id} rejected.")


# ----------------------------------------------------------------------------------------------- configuration (CLI, extension; never chat)
@op(sync=False)
def chat_status(p) -> Dict[str, Any]:
    from ..chat import manage
    return manage.status(p)


@op(sync=False, mutates=True)
def chat_setup(p, platform: str = "telegram", token: str = "", chat_id: Optional[str] = None, discover: bool = False, guild_id: Optional[str] = None) -> Dict[str, Any]:
    from ..chat import manage
    if platform == "discord":
        return manage.setup_discord(p, token, guild_id, channel_id=chat_id)
    if platform != "telegram":
        raise ResearchError(f"unknown platform {platform} (telegram or discord)")
    return manage.setup_telegram(p, token, chat_id, discover, 45)


@op(sync=False, mutates=True)
def chat_remove(p, platform: str = "telegram") -> Dict[str, Any]:
    from ..chat import manage
    return manage.remove_discord(p) if platform == "discord" else manage.remove_telegram(p)


@op(mutates=True, needs_project=False)
def chat_test(s, platform: str = "telegram") -> Dict[str, Any]:
    from ..chat import manage
    return manage.test_discord() if platform == "discord" else manage.test_telegram()


@op(needs_project=False, mutates=True)
def chat_pair(s, code: str, role: Optional[str] = None) -> Dict[str, Any]:
    from ..chat import pairing
    return pairing.approve(code, role)


@op(needs_project=False)
def chat_users(s, platform: Optional[str] = None) -> List[Dict[str, Any]]:
    from ..chat import pairing
    return pairing.users(platform)


@op(needs_project=False, mutates=True)
def chat_gateway(s, action: str = "status") -> Dict[str, Any]:
    from ..chat import gateway
    return gateway.control(action)


# `telegram_*` stay as aliases until 4.0 (the extension and old scripts call them)
@op(sync=False)
def telegram_status(p) -> Dict[str, Any]:
    from ..chat import telegram
    return telegram.status(p)


@op(sync=False, mutates=True)
def telegram_set(p, token: str, chat_id: Optional[str] = None, discover: bool = False) -> Dict[str, Any]:
    from ..chat import manage, telegram
    manage.setup_telegram(p, token, chat_id, discover, 45)
    return telegram.status(p)


@op(sync=False, mutates=True)
def telegram_clear(p) -> Dict[str, Any]:
    from ..chat import manage, telegram
    manage.remove_telegram(p)
    return telegram.status(p)


@op(mutates=True, needs_project=False)
def telegram_test(s) -> Dict[str, Any]:
    from ..chat import manage
    return manage.test_telegram()
