"""Halt detection, the debug agent, and the Telegram channel (all opt-in).

* A plan is HALTED when it is `running` yet nothing can move: no task is running, verifying, waiting for HPC, ready or repairing, no automatic
  promotion is advancing, and at least one task waits for a human (blocked / attention / awaiting approval). `supervisor.plan_health` computes it;
  `status` reports it instead of an unqualified "running".
* After `notifications.halt_grace_seconds` of the same halt the supervisor can run a DEBUG AGENT (read-only, strongest model by default) that reads the
  blocked tasks and logs and returns a few sentences plus a few concrete options. The options become interventions (`intervene.py`) when chosen.
* Telegram: the bot token and chat id are stored on THIS machine only (`telegram.json`, 0600, next to the approval PIN), never in the project, git,
  prompts or events. Only messages from the configured chat are obeyed. Chat can read status, ask for a diagnosis, choose an option, give a text
  instruction, pause or resume. It can never approve a protected change: that needs the PIN at a terminal or in VS Code.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .util import PolicyBlocked, ResearchError, now_iso

API = "https://api.telegram.org"
DEFAULTS = {"halt_grace_seconds": 120, "auto_debug": False, "telegram": {"enabled": False}}
_threads: Dict[str, threading.Thread] = {}


# ----------------------------------------------------------------------------------------------- credentials (machine-local)
def _path() -> str:
    from .configfile import machine_path
    return os.path.join(os.path.dirname(machine_path() or os.path.expanduser("~/.config/research-os/config.yaml")), "telegram.json")


def credentials() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_credentials(token: str, chat_id: Optional[Any] = None) -> None:
    token = (token or "").strip()
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
        raise ResearchError("That does not look like a Telegram bot token (expected 123456:ABC…) ", hint="Create a bot with @BotFather and copy its token.")
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"token": token, "chat_id": chat_id, "offset": 0}, f)
    os.replace(tmp, path)


def _save(c: Dict[str, Any]) -> None:
    path = _path()
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(c, f)
    os.replace(path + ".tmp", path)


def clear_credentials() -> None:
    try:
        os.unlink(_path())
    except OSError:
        pass


def status(p=None) -> Dict[str, Any]:
    c = credentials()
    en = bool(((p.config.get("notifications") or {}).get("telegram") or {}).get("enabled")) if p is not None else None
    return {"token_set": bool(c.get("token")), "chat_id_set": c.get("chat_id") is not None, "enabled": en,
            "ready": bool(c.get("token") and c.get("chat_id") is not None and en is not False), "stored_at": _path()}


def _api(token: str, method: str, params: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    """The only network call. Tests replace it. The token appears only in the URL path, never in logs, errors or events."""
    base = os.environ.get("RESEARCH_TELEGRAM_API") or API       # override: a proxy, or a local fake in tests
    req = urllib.request.Request(f"{base.rstrip('/')}/bot{token}/{method}", data=urllib.parse.urlencode(params).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as ex:
        raise ResearchError(f"Telegram refused the request (HTTP {ex.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise ResearchError(f"Telegram is unreachable: {type(ex).__name__}") from None
    if not d.get("ok"):
        raise ResearchError("Telegram error: " + str(d.get("description", "unknown"))[:120])
    return d


def send(text: str, chat_id: Optional[Any] = None) -> bool:
    c = credentials()
    if not c.get("token") or (chat_id or c.get("chat_id")) is None:
        return False
    from .taskprompt import redact
    text = redact(text) or ""
    for i in range(0, len(text), 3800):             # Telegram's limit is 4096 characters
        _api(c["token"], "sendMessage", {"chat_id": chat_id or c["chat_id"], "text": text[i:i + 3800]})
    return True


def discover_chat(token: str, wait: float = 60.0) -> Optional[Any]:
    """Setup helper: the first chat that messages the bot becomes the authorized chat."""
    t0, offset = time.time(), 0
    while time.time() - t0 < wait:
        for u in _api(token, "getUpdates", {"timeout": 0, "offset": offset}, 10).get("result", []):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if (m.get("chat") or {}).get("id") is not None:
                return m["chat"]["id"]
        time.sleep(2)
    return None


def ensure_running(p) -> Dict[str, Any]:
    """One step that keeps the pieces alive without extra switches (called when the extension opens a project and by `telegram setup`):
    start the supervisor process if Telegram is enabled and ready, or a plan is running, and it is not up; replace a running supervisor that is on
    older Research OS code (agents are untouched, the new process reconciles them). Never raises."""
    from . import supervisor as SV
    try:
        c, cr = cfg(p), credentials()
        wants_chat = bool(c["telegram"].get("enabled") and cr.get("token") and cr.get("chat_id") is not None)
        d = SV.daemon_status(p)
        if d["running"]:
            return {"action": "restart", **SV.daemon_restart(p, timeout=60)} if d.get("restart_required") else {"action": "none"}
        if wants_chat or any(r.get("state") == "running" for r in SV.all_runs(p)):
            return {"action": "start", **SV.daemon_start(p, detach=True)}
    except (ResearchError, PolicyBlocked, OSError) as ex:
        return {"action": "none", "error": str(ex)[:200]}
    return {"action": "none"}


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
    n = p.config.get("notifications") or {}
    return {**DEFAULTS, **n, "telegram": {**DEFAULTS["telegram"], **(n.get("telegram") or {})}}


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
def step(p) -> None:
    """Detect new halts, run the debug agent in a background thread, send Telegram messages, and obey Telegram commands. Never raises."""
    from . import supervisor as SV
    try:
        c = cfg(p)
        tg = c["telegram"].get("enabled") and credentials().get("token")
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
                        send(f"Plan {st['plan_key']} is moving again ({h['state']}).")
                continue
            if not m or m.get("fingerprint") != h["fingerprint"]:
                m = {"fingerprint": h["fingerprint"], "since": time.time(), "notified": False, "debugged": False}
                _write(marker, m)
            if time.time() - m["since"] < float(c["halt_grace_seconds"]) or m.get("notified"):
                continue
            key = st["plan_key"] + h["fingerprint"]
            if c["auto_debug"] and not m.get("debugged"):
                if key in _threads and _threads[key].is_alive():
                    continue
                m["debugged"] = True
                _write(marker, m)
                th = threading.Thread(target=_debug_and_send, args=(p.root, st["plan_key"], h, bool(tg)), daemon=True)
                _threads[key] = th
                th.start()
                continue
            if not c["auto_debug"]:
                m["notified"] = True
                _write(marker, m)
                if tg:
                    send(message_for({"state": "none"}, h))
        if tg:
            poll(p)
    except Exception as ex:      # noqa: BLE001 - notifications must never stop scheduling
        try:
            with open(p.path("supervisor", "daemon.log"), "a") as lf:
                lf.write(f"{now_iso()} notify error: {type(ex).__name__}: {str(ex)[:200]}\n")
        except OSError:
            pass


def _debug_and_send(root: str, plan_key: str, halt: Dict[str, Any], tg: bool) -> None:
    from .store import Project
    try:
        with Project(root, sync=False, author={"author_type": "system", "author_name": "supervisor", "author_model": None}) as p:
            rec = run_debug(p, plan_key, halt)
            marker = _dir(p, "halt-" + plan_key + ".json")
            m = _read(marker) or {}
            m["notified"] = True
            _write(marker, m)
            if tg:
                send(message_for(rec, {**halt, "plan": plan_key}))
    except Exception:    # noqa: BLE001
        pass


# ----------------------------------------------------------------------------------------------- Telegram commands
HELP = ("/status  all plans\n/blocked  what is stuck and why\n/task T-12  one task\n/logs T-12  last log lines\n/debug  diagnose the halt now\n"
        "/options  show the latest diagnosis\n/choose N  pick an option\n/say <text>  or /say T-12 <text>  instruct the supervisor\n"
        "/interventions  list\n/pause  /resume  the running plan\n/help\nProtected changes (science, resources, frozen tests) are approved with the PIN in VS Code or the terminal, never here.")


def poll(p) -> int:
    """Handle new messages from the authorized chat. Everything from any other chat is ignored."""
    c = credentials()
    if not c.get("token") or c.get("chat_id") is None:
        return 0
    n = 0
    for u in _api(c["token"], "getUpdates", {"timeout": 0, "offset": int(c.get("offset") or 0)}, 10).get("result", []):
        c["offset"] = u["update_id"] + 1
        _save(c)    # before handling, so a failing send() never replays the update
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(c["chat_id"]) or not m.get("text"):
            continue
        n += 1
        try:
            reply = handle(p, m["text"].strip())
        except (ResearchError, PolicyBlocked) as ex:
            reply = "Not done: " + str(ex)[:600]
        except Exception as ex:   # noqa: BLE001
            reply = f"Error: {type(ex).__name__}"
        send(reply, c["chat_id"])
    return n


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


def handle(p, text: str) -> str:
    from . import intervene as I, supervisor as SV
    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower().split("@")[0]
    rest = rest.strip()
    human = p.author
    p.author = {"author_type": "human", "author_name": "telegram", "author_model": None}      # the authorized chat is the researcher; recorded as such
    try:
        if cmd in ("/start", "/help"):
            return HELP
        if cmd == "/status":
            rows = []
            for st, rs in _plans(p):
                h = SV.plan_health(p, st, rs)
                s = SV.status(p, st["plan_key"])
                rows.append(f"{st['plan_key']}: {h['label']} · {s['progress']} verified · {s['concurrency']['running']} running"
                            + (f" · blocked: {', '.join(b['task'] for b in h['blocking'])}" if h["blocking"] else ""))
            return "\n".join(rows) or "No plan has been run."
        if cmd == "/blocked":
            b = _blocked(p)
            return "\n".join(f"{x['task']} {x['title'][:40]} [{x['phase']}]\n  {(x.get('reason') or '')[:240]}" for x in b) or "Nothing is blocked."
        if cmd == "/task":
            g = I.gather(p, rest)
            return f"{g['task']} {g['title']}\nphase {g['phase']} · {len(g['attempts'])} attempt(s)\n{(g['reason'] or '')[:400]}"
        if cmd == "/logs":
            return (I.gather(p, rest)["logs"] or "(no log yet)")[-1500:]
        if cmd == "/debug":
            plan = next((st["plan_key"] for st, rs in _plans(p) if SV.plan_health(p, st, rs)["blocking"]), None)
            if not plan:
                return "Nothing is blocked."
            threading.Thread(target=_debug_and_send, args=(p.root, plan, SV.plan_health(p, *_plan_pair(p, plan)), True), daemon=True).start()
            return "Diagnosing; I will message you when it is done."
        if cmd == "/options":
            d = latest_debug(p)
            return message_for(d, {"plan": d["plan"], "blocking": [{"task": t, "title": "", "phase": "blocked"} for t in d["tasks"]]}) if d else "No diagnosis yet. Try /debug."
        if cmd in ("/choose", "/say") or not cmd.startswith("/"):
            return _instruct(p, cmd, rest, text)
        if cmd in ("/pause", "/resume"):
            running = [st["plan_key"] for st, rs in _plans(p) if rs.get("state") in (("running",) if cmd == "/pause" else ("paused", "blocked", "attention"))]
            if len(running) != 1:
                return "Name exactly one plan state to change: " + (", ".join(running) or "none applies")
            (SV.plan_pause if cmd == "/pause" else SV.plan_resume)(p, running[0])
            return f"{running[0]} {'paused' if cmd == '/pause' else 'resumed'}."
        if cmd == "/interventions":
            return "\n".join(f"{i['id']} {i['task']} {i['state']}: {i['instruction'][:70]}" for i in I.list_all(p)[-8:]) or "None."
        return "Unknown command. /help"
    finally:
        p.author = human


def _plan_pair(p, plan_key):
    from . import planspec as PS, supervisor as SV
    st = PS.load_state(p, plan_key)
    return st, SV.load_run(p, st["plan_id"])


def _instruct(p, cmd: str, rest: str, text: str) -> str:
    from . import intervene as I
    if cmd == "/choose":
        d = latest_debug(p)
        if not d or not rest.isdigit() or not 1 <= int(rest) <= len(d.get("options") or []):
            return "Choose a number from /options."
        opt, tid = d["options"][int(rest) - 1], d["tasks"][0]
        msg = opt["instruction"]
    else:
        body = rest if cmd == "/say" else text
        m = re.match(r"(T-\d+)\s+(.*)", body, re.S)
        blocked = _blocked(p)
        if m:
            tid, msg = m.group(1), m.group(2)
        elif len(blocked) == 1:
            tid, msg = blocked[0]["task"], body
        else:
            return "Which task? Use /say T-12 <instruction>. Blocked: " + (", ".join(b["task"] for b in blocked) or "none")
    it = I.request(p, tid, msg, source="telegram")
    if it["state"] == "approved":
        return f"{it['id']}: {it['summary']}\nApplied under your instruction; the supervisor is resuming {tid}."
    if it["state"] == "proposed":
        return (f"{it['id']}: {it['summary']}\nThis changes " + ", ".join(it["protected"]) + " and needs your approval with the PIN:\n"
                f"  research intervention approve {it['id']}\n(or in VS Code → Plans → {tid} → Instruct).")
    return f"{it['id']} {it['state']}: {(it.get('history') or [{}])[-1].get('note', '')}"
