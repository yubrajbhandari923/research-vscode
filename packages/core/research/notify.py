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

from . import recovery as RC, registry
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


def _as_halt(h: Dict[str, Any]) -> Dict[str, Any]:
    """A paused plan with failed tasks is diagnosed and reported like a halted one."""
    if not h.get("blocking") and h.get("stuck"):
        return {**h, "blocking": h["stuck"], "paused": True, "fingerprint": "paused|" + h.get("stuck_fingerprint", "")}
    return h


def run_debug(p, plan_ref: str, halt: Optional[Dict[str, Any]] = None, tasks: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the debug agent now (CLI/UI/Telegram) or from the halt watcher. Returns the stored record."""
    from . import intervene as I, planspec as PS, supervisor as SV
    st = PS._state_for(p, plan_ref)
    rs = SV.load_run(p, st["plan_id"]) or {}
    halt = halt or SV.plan_health(p, st, rs)
    halt = _as_halt(halt)
    blocking = [b for b in halt.get("blocking", []) if not tasks or b["task"] in tasks]
    if not blocking:
        raise ResearchError("Nothing is blocked in this plan, so there is nothing to diagnose")
    fp = halt.get("fingerprint") or ",".join(b["task"] for b in blocking)
    promos = [b for b in blocking if b.get("promotion")]
    work = [b for b in blocking if not b.get("promotion")]       # a paused promotion is not a task: it has no attempts, logs or contract to read
    rec = {"id": f"DBG-{int(time.time())}", "plan": st["plan_key"], "plan_id": st["plan_id"], "fingerprint": fp, "tasks": [b["task"] for b in (work or blocking)],
           "state": "running", "started": now_iso(), "model": I.assistant_cfg(p).get("model") or "default (strongest available)", "options": [], "summary": None,
           "promotion_only": not work}
    _write(_dir(p, "latest.json"), rec)
    try:
        extra = "\n\n".join(promotion_diagnosis(st, rs, b) for b in promos)
        if work:
            ctx = [I.gather(p, b["task"]) for b in work[:4]]
            raw = I._json_from(I.run_assistant(p, f"Diagnose halted plan {st['plan_key']}", DEBUG_PROMPT.format(halt=_halt_text({"blocking": work}), context=json.dumps(ctx, default=str, indent=1)[:26000])))
        else:
            raw = {"summary": extra}                                 # recorded facts and exact commands: no agent is needed, and none can invent a cause
            extra = ""
        opts = []
        for o in (raw.get("options") or [])[:4]:
            if isinstance(o, dict) and str(o.get("instruction") or "").strip():
                opts.append({"label": str(o.get("label") or o["instruction"])[:80], "instruction": str(o["instruction"])[:2000],
                             "class": o.get("class") if o.get("class") in ("operational", "implementation", "scientific", "resource") else "implementation"})
        rec.update(state="done", summary=(str(raw.get("summary") or "") + ("\n\n" + extra if extra else ""))[:1800], options=opts,
                   recommended=raw.get("recommended") if isinstance(raw.get("recommended"), int) and 0 <= raw["recommended"] < len(opts) else None, finished=now_iso())
    except (ResearchError, ValueError) as ex:
        rec.update(state="failed", error=str(ex)[:400], finished=now_iso())
    _write(_dir(p, "latest.json"), rec)
    _write(_dir(p, rec["fingerprint"].replace("/", "_")[:80] + ".json"), rec)
    return rec


def promotion_diagnosis(st: Dict[str, Any], rs: Dict[str, Any], b: Dict[str, Any]) -> str:
    """What stopped an automatic promotion, from its own record, and the exact next step. Every task is verified and none is re-run."""
    from . import commitprov as CP
    item = next((i for i in ((rs.get("autopromote") or {}).get("items") or []) if i["id"] == b["task"]), {})
    k, out = st["plan_key"], [f"Promotion {b['task']} ({item.get('experiment')}) is paused: {(item.get('error') or b.get('reason') or 'no reason recorded')[:500]}"]
    for o in (item.get("objections") or [])[-2:]:
        out.append(f"Earlier objection (attempt {o.get('attempt')}): {str(o.get('why'))[:200]}")
    cov = (CP.state(rs)["reports"] or {}).get(item.get("workspace"))
    if cov and cov.get("blocking"):
        out.append(f"{len(cov['orphans']) + len(cov['ambiguous'])} commit(s) on the experiment branch are not tied to a verified task. See them with "
                   f"`research plan autopromote {k} coverage`; then `attribute --commit SHA --task T-…` or `exclude --commit SHA`.")
    if item.get("omits"):
        out.append(f"The candidate omits {len(item['omits'])} verified commit(s) recorded after it was prepared; retry prepares a new one with them.")
    out.append(f"Nothing is merged and no task is re-run. After fixing the cause: `research plan autopromote {k} retry --item {b['task']}`.")
    return "\n".join(out)


def message_for(rec: Dict[str, Any], halt: Dict[str, Any]) -> str:
    head = f"Plan {halt.get('plan', rec.get('plan'))} is {'PAUSED with failed tasks' if halt.get('paused') else 'HALTED'}\n" + "\n".join(
        f"- {b['task']} {b['title'][:50]}: {(b.get('reason') or b['phase'])[:140]}" + "".join("\n    " + x for x in RC.line(b.get("recovery")).splitlines()) for b in halt["blocking"][:5])
    if rec.get("state") == "done" and rec.get("summary"):
        body = "\n\n" + rec["summary"]
        if rec["options"]:
            body += "\n\nOptions:\n" + "\n".join(f"{i + 1}. {o['label']} [{o['class']}]" + (" (recommended)" if rec.get("recommended") == i else "") for i, o in enumerate(rec["options"]))
            body += "\n\nReply /choose N, or /say <your instruction>."
        elif rec.get("promotion_only"):
            body += "\n\nThe fix is on your side; the commands above do it. Tasks are not re-run."
        else:
            body += "\n\nNo obvious fix. Reply /say <your instruction>."
        return head + body
    return head + ("\n\n(The debug agent failed: " + str(rec.get("error"))[:160] + ")" if rec.get("state") == "failed" else "\n\nReply /debug for a diagnosis or /say <instruction>.")


# ----------------------------------------------------------------------------------------------- the watcher (called by the supervisor process every pass)
def _halt_actions(rec: Dict[str, Any], plan_key: str) -> List[Dict[str, Any]]:
    acts: List[Dict[str, Any]] = [{"label": "Diagnose", "op": "chat_cmd_debug"}] if rec.get("state") != "done" else []
    acts += [{"label": f"Option {i + 1}: {o['label']}"[:40], "op": "chat_cmd_choose", "params": {"n": str(i + 1)}} for i, o in enumerate(rec.get("options") or [])]
    return acts + [{"label": "Pause plan", "op": "chat_cmd_pause", "params": {"plan": plan_key}}]


def _alert_halt(p, plan_key: str, fingerprint: str, text: str, rec: Dict[str, Any], force: bool = False) -> None:
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
    alerts.raise_(p, f"halt:{plan_key}", "halt", "error", text, actions=acts, decision=dec, force=force)


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


def _experiment_alerts(p) -> None:
    """Experiment started / finished (with the recorded results) and, at the highest level, every run. Only recent events, so an upgrade does not replay history."""
    from . import summary as S
    from .chat import alerts
    for e in p.q("SELECT id, title, status, started_at, updated_at FROM experiments WHERE status IN ('running','needs_review','completed','failed')"):
        if e["status"] == "running" and _recent(e["started_at"]):
            alerts.raise_(p, f"experiment_started:{e['id']}", "experiment_started", "info", S.started_text(p, e["id"]), once=True)
        elif e["status"] != "running" and _recent(e["updated_at"]) and alerts.allowed(p, "experiment_finished"):
            n = p.q1("SELECT COUNT(*) AS n FROM runs WHERE experiment_id=? AND status IN ('completed','failed','cancelled')", (e["id"],))["n"]
            if n:
                sev, text = S.experiment_summary(p, e["id"])
                key = f"experiment_finished:{e['id']}:{n}"
                if (p.config.get("notifications") or {}).get("summary") == "agent" and not (alerts._load(p).get("keys") or {}).get(key):
                    if key not in _threads or not _threads[key].is_alive():
                        _threads[key] = threading.Thread(target=_agent_summary_and_alert, args=(p.root, e["id"], key, sev, text), daemon=True)
                        _threads[key].start()
                    continue
                alerts.raise_(p, key, "experiment_finished", sev, text, once=True)
    if alerts.allowed(p, "run_finished"):
        for r in p.q("SELECT id, ended_at FROM runs WHERE status IN ('completed','failed','cancelled') ORDER BY ended_at DESC LIMIT 30"):
            if _recent(r["ended_at"]):
                sev, text = S.run_summary(p, r["id"])
                alerts.raise_(p, f"run_finished:{r['id']}", "run_finished", sev, text, once=True)


def _agent_summary_and_alert(root: str, exp_id: str, key: str, sev: str, facts: str) -> None:
    """notifications.summary: agent. A read-only agent writes the short summary from the recorded facts, off the supervisor's tick; the facts follow it,
    and if the agent cannot run the facts alone are sent (saying why). Never blocks scheduling, never writes anything but the alert."""
    from . import ask as ASK
    from .chat import alerts
    from .store import Project
    try:
        with Project(root, sync=False, author={"author_type": "system", "author_name": "supervisor", "author_model": None}) as p:
            try:
                r = ASK.run(p, p.get("experiment", exp_id)["title"], facts=facts, style="summary")
                text = f"{r['answer']}\n\n--- recorded facts ---\n{facts}{ASK.footer(r)}"
            except (ResearchError, PolicyBlocked) as ex:
                text = f"{facts}\n\n(agent summary unavailable: {str(ex)[:160]})"
            alerts.raise_(p, key, "experiment_finished", sev, text, once=True)
    except Exception:      # noqa: BLE001
        pass


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
            if rs.get("state") not in ("running", "paused"):
                continue
            from . import planspec as PS
            st = PS.load_state(p, rs["plan_key"])
            if not st:
                continue
            h = SV.plan_health(p, st, rs)
            if rs["state"] == "paused" and h.get("stuck"):      # a paused plan with failed tasks is diagnosed and reported like a halted one
                h = {**h, "state": "halted", "paused": True, "blocking": h["stuck"], "fingerprint": "paused|" + h["stuck_fingerprint"]}
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
            _experiment_alerts(p)
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
                if b["phase"] in ("blocked", "attention") and not b.get("promotion"):
                    investigator.maybe_auto(p, b["task"], halt.get("fingerprint") or "")
    except Exception:    # noqa: BLE001 - a diagnosis must never break the watcher
        pass


def _debug_and_alert(root: str, plan_key: str, halt: Dict[str, Any]) -> None:
    from .store import Project
    try:
        with Project(root, sync=False, author={"author_type": "system", "author_name": "supervisor", "author_model": None}) as p:
            halt = _as_halt(halt)
            try:
                rec = run_debug(p, plan_key, halt)
            except Exception as ex:    # noqa: BLE001 - an unexpected failure is still an answer to "diagnose": say what happened
                rec = {"state": "failed", "error": f"{type(ex).__name__}: {ex}", "plan": plan_key, "options": []}
                _log(p, f"diagnosis of {plan_key} failed: {rec['error'][:300]}")
            marker = _dir(p, "halt-" + plan_key + ".json")
            m = _read(marker) or {}
            m["notified"] = True
            _write(marker, m)
            if chat_enabled(p):
                _alert_halt(p, plan_key, halt.get("fingerprint", ""), message_for(rec, {**halt, "plan": plan_key}), rec, force=True)
    except Exception as ex:    # noqa: BLE001
        try:
            with open(os.path.join(root, ".research", "supervisor", "daemon.log"), "a") as lf:
                lf.write(f"{now_iso()} diagnosis of {plan_key} failed: {type(ex).__name__}: {str(ex)[:200]}\n")
        except OSError:
            pass


def _log(p, line: str) -> None:
    try:
        with open(p.path("supervisor", "daemon.log"), "a") as lf:
            lf.write(f"{now_iso()} {line}\n")
    except OSError:
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
    out = []
    for st, rs in _plans(p):
        if rs.get("state") in ("running", "paused", "blocked", "attention"):
            h = SV.plan_health(p, st, rs)
            out += [dict(b, plan=st["plan_key"]) for b in (h["blocking"] or h.get("stuck") or [])]
    return out


def _plan_pair(p, plan_key):
    from . import planspec as PS, supervisor as SV
    st = PS.load_state(p, plan_key)
    return st, SV.load_run(p, st["plan_id"])
