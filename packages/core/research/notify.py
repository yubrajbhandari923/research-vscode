"""Halt detection, the debug agent, and the supervisor's side of chat (all opt-in).

* A plan is HALTED when it is `running` yet nothing can move: no task is running, verifying, waiting for HPC, ready or repairing, no automatic
  promotion is advancing, and at least one task waits for a human (blocked / attention / awaiting approval). `supervisor.plan_health` computes it;
  `status` reports it instead of an unqualified "running".
* After `notifications.halt_grace_seconds` of the same halt the supervisor can run a DEBUG AGENT (read-only, strongest model by default) that reads the
  blocked tasks and logs and returns a few sentences plus a few concrete options. The options become interventions (`intervene.py`) when chosen.
* Chat: supervisors never talk to Telegram or Discord. `step` writes alerts to the project's outbox (`chat/alerts.py`); the machine's gateway process
  (`chat/gateway.py`) delivers them and obeys commands. Credentials live on THIS machine only (`chat/telegram.py`). Chat can read status, ask for a
  diagnosis, choose an option, instruct, pause or resume; it never approves a protected change without the PIN or an enrolled authenticator.
  The Telegram functions below (`credentials`, `send`, `poll`, ...) are thin shims kept for old callers.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import registry
from .util import PolicyBlocked, ResearchError, now_iso

_threads: Dict[str, threading.Thread] = {}

# Telegram shims: the implementation moved to research.chat
from .chat import telegram as _tg                                  # noqa: E402
from .chat.telegram import clear_credentials, credentials, discover_chat, send, set_credentials, status  # noqa: E402,F401
_path = _tg.path


def chat_platforms(p) -> List[str]:
    """The chat platforms this project pushes to: each one needs its own switch and its own saved token, and never the other's."""
    from .chat import discord_bot
    c, out = cfg(p), []
    if c["telegram"].get("enabled") and credentials().get("token"):
        out.append("telegram")
    if (c.get("discord") or {}).get("enabled") and discord_bot.credentials().get("token"):
        out.append("discord")
    return out


def chat_enabled(p) -> bool:
    return bool(chat_platforms(p))


def ensure_gateway(p) -> Dict[str, Any]:
    """Start (or replace a stale) chat gateway when chat is enabled for this project. Cheap, never raises."""
    if not chat_enabled(p):
        return {"action": "none"}
    from .chat import gateway
    return gateway.ensure_running(p)


def ensure_running(p) -> Dict[str, Any]:
    """One step that keeps the pieces alive without extra switches (called when the extension opens a project and by `chat setup`):
    start the supervisor process if chat is enabled and ready, or a plan is running, and it is not up; replace a running supervisor that is on
    older Research OS code (agents are untouched, the new process reconciles them); make sure the gateway runs. Never raises."""
    from . import supervisor as SV
    try:
        cr = credentials()
        # Telegram needs a linked chat to say anything; Discord's gateway must run before /sethome can be typed, so a token is enough
        wants_chat = bool(("telegram" in chat_platforms(p) and cr.get("chat_id") is not None) or "discord" in chat_platforms(p))
        if wants_chat:
            ensure_gateway(p)
        d = SV.daemon_status(p)
        if d["running"]:
            return {"action": "restart", **SV.daemon_restart(p, timeout=60)} if d.get("restart_required") else {"action": "none"}
        if wants_chat or any(r.get("state") == "running" for r in SV.all_runs(p)):
            return {"action": "start", **SV.daemon_start(p, detach=True)}
    except (ResearchError, PolicyBlocked, OSError) as ex:
        return {"action": "none", "error": str(ex)[:200]}
    return {"action": "none"}


def poll(p=None) -> int:
    """One Telegram receive pass through the gateway's router (debugging; the gateway process does this continuously)."""
    from .chat import gateway
    return gateway.poll_once(p)


# ----------------------------------------------------------------------------------------------- state files
def _dir(p, *parts: str) -> str:
    d = p.path("supervisor", "debug", *parts)
    os.makedirs(d if not parts else os.path.dirname(d), exist_ok=True)
    return d


def _read(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path: str, d: Dict[str, Any]) -> None:
    from .store import _atomic_write
    _atomic_write(path, json.dumps(d, indent=1, sort_keys=True, default=str) + "\n")


def cfg(p) -> Dict[str, Any]:
    return registry.section(p, "notifications")


def latest_debug(p) -> Optional[Dict[str, Any]]:
    return _read(_dir(p, "latest.json"))


# ----------------------------------------------------------------------------------------------- debug agent
DEBUG_PROMPT = """You are a debugging assistant for a research-automation supervisor. A plan has HALTED: nothing can run until a human decides.
Read the blocked tasks below (contract, attempts, diagnostics, log excerpts) and, if you need more, read files and logs in the project (read only; do not
edit anything, do not run experiments). Reply with ONE JSON object only:
{{"summary":"2-4 plain sentences: what happened and the most likely cause","options":[{{"label":"short title","instruction":"a natural-language instruction the supervisor
can plan from","class":"operational|implementation|scientific|resource"}}],"recommended":0}}
Give at most 4 options, ordered most-obvious first; omit options if nothing is clearly right. "recommended" is the index of the best option or null.
Never suggest weakening a scientific check unless the evidence shows the check itself is wrong, and then say so.

HALT:
{halt}

BLOCKED TASKS (JSON):
{context}
"""


def _halt_text(h: Dict[str, Any]) -> str:
    return "; ".join(f"{b['task']} {b['title'][:40]!r} [{b['phase']}] {(b.get('reason') or '')[:160]}" for b in h["blocking"])


def run_debug(p, plan_ref: str, halt: Optional[Dict[str, Any]] = None, tasks: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the debug agent now (CLI/UI/Telegram) or from the halt watcher. Returns the stored record."""
    from . import intervene as I, planspec as PS, supervisor as SV
    st = PS._state_for(p, plan_ref)
    rs = SV.load_run(p, st["plan_id"]) or {}
    halt = halt or SV.plan_health(p, st, rs)
    blocking = [b for b in halt.get("blocking", []) if not tasks or b["task"] in tasks]
    if not blocking:
        raise ResearchError("Nothing is blocked in this plan, so there is nothing to diagnose")
    fp = halt.get("fingerprint") or ",".join(b["task"] for b in blocking)
    rec = {"id": f"DBG-{int(time.time())}", "plan": st["plan_key"], "plan_id": st["plan_id"], "fingerprint": fp, "tasks": [b["task"] for b in blocking],
           "state": "running", "started": now_iso(), "model": I.assistant_cfg(p).get("model") or "default (strongest available)", "options": [], "summary": None}
    _write(_dir(p, "latest.json"), rec)
    try:
        ctx = [I.gather(p, b["task"]) for b in blocking[:4]]
        raw = I._json_from(I.run_assistant(p, f"Diagnose halted plan {st['plan_key']}", DEBUG_PROMPT.format(halt=_halt_text({"blocking": blocking}), context=json.dumps(ctx, default=str, indent=1)[:26000])))
        opts = []
        for o in (raw.get("options") or [])[:4]:
            if isinstance(o, dict) and str(o.get("instruction") or "").strip():
                opts.append({"label": str(o.get("label") or o["instruction"])[:80], "instruction": str(o["instruction"])[:2000],
                             "class": o.get("class") if o.get("class") in ("operational", "implementation", "scientific", "resource") else "implementation"})
        rec.update(state="done", summary=str(raw.get("summary") or "")[:1200], options=opts,
                   recommended=raw.get("recommended") if isinstance(raw.get("recommended"), int) and 0 <= raw["recommended"] < len(opts) else None, finished=now_iso())
    except (ResearchError, ValueError) as ex:
        rec.update(state="failed", error=str(ex)[:400], finished=now_iso())
    _write(_dir(p, "latest.json"), rec)
    _write(_dir(p, rec["fingerprint"].replace("/", "_")[:80] + ".json"), rec)
    return rec


def message_for(rec: Dict[str, Any], halt: Dict[str, Any]) -> str:
    head = f"Plan {halt.get('plan', rec.get('plan'))} is HALTED\n" + "\n".join(f"- {b['task']} {b['title'][:50]}: {(b.get('reason') or b['phase'])[:140]}" for b in halt["blocking"][:5])
    if rec.get("state") == "done" and rec.get("summary"):
        body = "\n\n" + rec["summary"]
        if rec["options"]:
            body += "\n\nOptions:\n" + "\n".join(f"{i + 1}. {o['label']} [{o['class']}]" + (" (recommended)" if rec.get("recommended") == i else "") for i, o in enumerate(rec["options"]))
            body += "\n\nReply /choose N, or /say <your instruction>."
        else:
            body += "\n\nNo obvious fix. Reply /say <your instruction>."
        return head + body
    return head + ("\n\n(The debug agent failed: " + str(rec.get("error"))[:160] + ")" if rec.get("state") == "failed" else "\n\nReply /debug for a diagnosis or /say <instruction>.")


# ----------------------------------------------------------------------------------------------- the watcher (called by the supervisor process every pass)
def _halt_actions(rec: Dict[str, Any], plan_key: str) -> List[Dict[str, Any]]:
    acts: List[Dict[str, Any]] = [{"label": "Diagnose", "op": "chat_cmd_debug"}] if rec.get("state") != "done" else []
    acts += [{"label": f"Option {i + 1}: {o['label']}"[:40], "op": "chat_cmd_choose", "params": {"n": str(i + 1)}} for i, o in enumerate(rec.get("options") or [])]
    return acts + [{"label": "Pause plan", "op": "chat_cmd_pause", "params": {"plan": plan_key}}]


def _alert_halt(p, plan_key: str, fingerprint: str, text: str, rec: Dict[str, Any]) -> None:
    from .chat import alerts, commands
    acts, dec = _halt_actions(rec, plan_key), None
    if rec.get("state") == "done" and rec.get("options"):
        try:                     # H.7: the diagnosis is a decision request, so every surface answers the same object (buttons, text reply, TUI, CLI)
            from . import decisions as D
            d = D.from_diagnosis(p, rec)
            if d:
                dec, acts = d["id"], commands.decision_actions(d) + [{"label": "Pause plan", "op": "chat_cmd_pause", "params": {"plan": plan_key}}]
        except Exception:        # noqa: BLE001 - fall back to the option buttons
            pass
    alerts.raise_(p, f"halt:{plan_key}", "halt", "error", text, actions=acts, decision=dec)


def _scan(p, c: Dict[str, Any]) -> None:
    """Other alert producers, stateless: the alert key dedupes, and a condition that ended is cleared once."""
    from . import intervene as I, planspec as PS, supervisor as SV
    from .chat import alerts, commands
    want = set()
    for pr in SV.list_proposals(p):
        if pr.get("status") == "pending":
            k = f"approval:{pr['id']}"
            want.add(k)
            acts = [{"label": "Reject", "op": "chat_cmd_reject", "params": {"kind": "proposal", "id": pr["id"]}, "style": "danger"},
                    {"label": "Approve on my computer", "text": f"Approve with the PIN on your computer:\n  research proposal approve {pr['id']}\n(or VS Code → Plans → Approvals, or `research tui`)"}]
            alerts.raise_(p, k, "approval_needed", "warn", f"Proposal {pr['id']} needs your approval: {(pr.get('rationale') or '')[:200]}",
                          actions=acts + commands.card_actions(p, "proposal", pr["id"]))
    for it in I.list_all(p):
        if it.get("state") == "proposed":
            k = f"approval:{it['id']}"
            want.add(k)
            alerts.raise_(p, k, "approval_needed", "warn", f"Intervention {it['id']} for {it['task']} needs your approval: {it['instruction'][:160]}",
                          actions=[{"label": "Reject", "op": "chat_cmd_reject", "params": {"kind": "intervention", "id": it["id"]}, "style": "danger"},
                                   {"label": "Approve on my computer", "text": f"Approve with the PIN on your computer:\n  research intervention approve {it['id']}"}],
                          task=it["task"])
    for k in alerts.open_keys(p, "approval:") - want:
        alerts.clear(p, k, f"{k.split(':', 1)[1]} was decided.")
    try:
        from . import decisions as D
        open_ds = [d for d in D.list_all(p) if d.get("created_by") != "debug agent"]       # a diagnosis rides on its halt card
    except Exception:           # noqa: BLE001
        open_ds = []
    for d in open_ds:
        alerts.raise_(p, f"decision:{d['id']}", "decision", "warn", D.text_for(d), actions=commands.decision_actions(d), decision=d["id"],
                      task=(d.get("subject") or {}).get("id"))
    for k in alerts.open_keys(p, "decision:") - {f"decision:{d['id']}" for d in open_ds}:
        alerts.clear(p, k, f"{k.split(':', 1)[1]} was answered or closed.")
    for rs in SV.all_runs(p):
        st = PS.load_state(p, rs["plan_key"])
        if not st:
            continue
        if _recent(rs.get("started_at")):                   # not for a run that began before this feature existed
            alerts.raise_(p, f"plan_started:{rs['plan_key']}:{rs['started_at']}", "plan_started", "info", f"Plan {rs['plan_key']} started.", once=True)
        if SV.plan_health(p, st, rs)["state"] == "completed":
            alerts.raise_(p, f"plan_finished:{rs['plan_key']}", "plan_finished", "ok", f"Plan {rs['plan_key']} finished.", once=True)
        for tid, e in (rs.get("tasks") or {}).items():
            n = len(e.get("attempts") or [])
            if e.get("phase") == "running" and n:
                alerts.raise_(p, f"task_started:{tid}:{n}", "task_started", "info", f"{tid} started (attempt {n}): {p.get('task', tid)['title'][:80]}", task=tid, once=True)
            elif e.get("phase") == "verified":
                alerts.raise_(p, f"task_verified:{tid}", "task_verified", "ok", f"{tid} verified: {p.get('task', tid)['title'][:80]}", task=tid, once=True)
            if e.get("phase") == "failed":
                alerts.raise_(p, f"task_failed:{tid}:{len(e.get('attempts') or [])}", "task_failed", "error",
                              f"{tid} failed after {len(e.get('attempts') or [])} attempt(s): {(e.get('reason') or '')[:200]}", task=tid,
                              actions=[{"label": "Show logs", "op": "chat_cmd_logs", "params": {"task": tid}, "min_role": "viewer"}])


def _recent(ts: Optional[str], hours: float = 24.0) -> bool:
    """True when an ISO timestamp is within `hours` of now (naive timestamps are UTC)."""
    if not ts:
        return False
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() < hours * 3600


def step(p) -> None:
    """Detect new halts, run the debug agent in a background thread, queue chat alerts, keep the gateway alive. Never raises."""
    from . import supervisor as SV
    try:
        c = cfg(p)
        tg = chat_enabled(p)          # any platform: Telegram, Discord or both
        for rs in SV.all_runs(p):
            if rs.get("state") != "running":
                continue
            from . import planspec as PS
            st = PS.load_state(p, rs["plan_key"])
            if not st:
                continue
            h = SV.plan_health(p, st, rs)
            marker = _dir(p, "halt-" + st["plan_key"] + ".json")
            m = _read(marker)
            if h["state"] != "halted":
                if m:
                    os.unlink(marker)
                    if tg and m.get("notified"):
                        from .chat import alerts
                        alerts.clear(p, f"halt:{st['plan_key']}", f"Plan {st['plan_key']} is moving again ({h['state']}).")
                continue
            if not m or m.get("fingerprint") != h["fingerprint"]:
                m = {"fingerprint": h["fingerprint"], "since": time.time(), "notified": False, "debugged": False}
                _write(marker, m)
            if _auto_investigator(p) and not m.get("investigated") and time.time() - m["since"] >= float(c["halt_grace_seconds"]):
                m["investigated"] = True
                _write(marker, m)
                threading.Thread(target=_auto_investigate, args=(p.root, h), daemon=True).start()       # H.5: once per halt, off the supervisor's tick
            if time.time() - m["since"] < float(c["halt_grace_seconds"]) or m.get("notified"):
                continue
            key = st["plan_key"] + h["fingerprint"]
            if c["auto_debug"] and not m.get("debugged"):
                if key in _threads and _threads[key].is_alive():
                    continue
                m["debugged"] = True
                _write(marker, m)
                th = threading.Thread(target=_debug_and_alert, args=(p.root, st["plan_key"], h), daemon=True)
                _threads[key] = th
                th.start()
                continue
            if not c["auto_debug"]:
                m["notified"] = True
                _write(marker, m)
                if tg:
                    _alert_halt(p, st["plan_key"], h["fingerprint"], message_for({"state": "none"}, h), {})
        if tg:
            _scan(p, c)
            ensure_gateway(p)
    except Exception as ex:      # noqa: BLE001 - notifications must never stop scheduling
        try:
            with open(p.path("supervisor", "daemon.log"), "a") as lf:
                lf.write(f"{now_iso()} notify error: {type(ex).__name__}: {str(ex)[:200]}\n")
        except OSError:
            pass


def _auto_investigator(p) -> bool:
    from . import investigator
    return bool(investigator.cfg(p)["auto"])


def _auto_investigate(root: str, halt: Dict[str, Any]) -> None:
    """Worker thread: the investigator once per blocked task of a halted plan (failures the rules could not settle or whose retries ran out; limits and
    approval waits are not blocked tasks). `maybe_auto` applies max_per_plan, budget_usd and once-per-(task, fingerprint)."""
    from .store import Project
    from . import investigator
    try:
        with Project(root, sync=False, author={"author_type": "system", "author_name": "supervisor", "author_model": None}) as p:
            for b in halt["blocking"]:
                if b["phase"] == "blocked":
                    investigator.maybe_auto(p, b["task"], halt.get("fingerprint") or "")
    except Exception:    # noqa: BLE001 - a diagnosis must never break the watcher
        pass


def _debug_and_alert(root: str, plan_key: str, halt: Dict[str, Any]) -> None:
    from .store import Project
    try:
        with Project(root, sync=False, author={"author_type": "system", "author_name": "supervisor", "author_model": None}) as p:
            rec = run_debug(p, plan_key, halt)
            marker = _dir(p, "halt-" + plan_key + ".json")
            m = _read(marker) or {}
            m["notified"] = True
            _write(marker, m)
            if chat_enabled(p):
                _alert_halt(p, plan_key, halt.get("fingerprint", ""), message_for(rec, {**halt, "plan": plan_key}), rec)
    except Exception:    # noqa: BLE001
        pass


def _plans(p) -> List[Any]:
    from . import planspec as PS, supervisor as SV
    out = []
    for rs in SV.all_runs(p):
        st = PS.load_state(p, rs["plan_key"])
        if st:
            out.append((st, rs))
    return out


def _blocked(p) -> List[Dict[str, Any]]:
    from . import supervisor as SV
    return [dict(b, plan=st["plan_key"]) for st, rs in _plans(p) if rs.get("state") in ("running", "paused", "blocked", "attention")
            for b in SV.plan_health(p, st, rs)["blocking"]]


def _plan_pair(p, plan_key):
    from . import planspec as PS, supervisor as SV
    st = PS.load_state(p, plan_key)
    return st, SV.load_run(p, st["plan_id"])
