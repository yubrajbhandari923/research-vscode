"""`research chat ...`: chat gateway setup and pairing (Telegram today, Discord later). `research telegram ...` stays as a hidden alias."""
from __future__ import annotations

from typing import Any

from research import approval as AP
from research.util import PolicyBlocked, ResearchError

from . import mut
from ..output import dim, green, out_json, yellow


def _emit(a, obj: Any, lines=None) -> int:
    if a.json:
        out_json(obj)
    elif lines:
        for l in lines(obj):
            print(l)
    return 0


def _human_only(p, what: str) -> None:
    if AP.agent_environment() or (p.author or {}).get("author_type") == "agent":
        raise PolicyBlocked(f"Only the researcher can {what}")


def _status_lines(d):
    t = d["platforms"]["telegram"]
    g = d["gateway"]
    lines = [("telegram: " + ("token saved" if t["token_set"] else "no token") + ", " + ("chat linked" if t["chat_id_set"] else "no chat linked")
              + f", {'enabled' if t['enabled'] else 'disabled'} for this project; stored at {t['stored_at']}")]
    lines += [f"  {u['user_id']}  {u['role']}  {u.get('label') or ''}" for u in t["users"]] or ["  no paired users (message the bot to get a pairing code)"]
    lines.append("gateway: " + (f"running (pid {g['pid']}, {', '.join(g['platforms']) or 'no platform'})" + (" - restart needed (older version)" if g["stale"] else "") if g["running"] else "not running"))
    lines.append("discord: not available yet")
    return lines


def _setup(a, p, platform: str) -> int:
    from research.chat import manage
    if platform != "telegram":
        raise ResearchError(f"{platform} is not available yet; Telegram is")
    token = a.token or AP.read_pin("Bot token (from @BotFather; not shown): ")
    if not token:
        raise ResearchError("No token entered (a terminal is needed, or pass --token)")
    if a.chat_id is None:
        print("Now open your bot in Telegram and send it any message (waiting up to 60 s)…")
    r = manage.setup_telegram(p, token, a.chat_id, discover=a.chat_id is None)
    sup = r["supervisor"]
    return _emit(a, r, lambda d: [green("✓ ") + "Telegram connected; token and chat id are stored only on this machine",
                                  green("✓ ") + "chat gateway " + ("started" if d["gateway"].get("running") else "will start with the supervisor"),
                                  (green("✓ ") + "supervisor " + ("started: it alerts you on halts and the gateway answers your chat" if sup["action"] == "start"
                                                                  else "is running: it alerts you on halts and the gateway answers your chat"))
                                  if not sup.get("error") else yellow("! start it with `research supervisor start`: " + sup["error"])]
                  + ([dim("Your Telegram user was added as operator; others pair with `research chat pair CODE`.")] if d.get("migrated_user") else []))


def cmd_chat(a, p) -> int:
    from research.chat import gateway, manage, pairing
    act, args = a.action, a.args
    if act == "status":
        return _emit(a, manage.status(p), _status_lines)
    if act in ("setup", "pair", "test", "remove", "gateway", "unpair"):
        _human_only(p, "configure chat")
    if act == "setup":
        return _setup(a, p, args[0] if args else "telegram")
    if act == "pair":
        if not args:
            raise ResearchError("pair needs the code the bot sent (research chat pair ABCD2345)")
        u = pairing.approve(args[0], a.role)
        return _emit(a, u, lambda d: [green("✓ ") + f"{d.get('label') or d['user_id']} paired on {d['platform']} as {d['role']}"])
    if act == "unpair":
        if len(args) != 2:
            raise ResearchError("unpair needs PLATFORM and USER_ID")
        return _emit(a, {"removed": pairing.remove_user(args[0], args[1])}, lambda d: [green("✓ ") + "removed" if d["removed"] else yellow("no such user")])
    if act == "test":
        r = manage.test_telegram()
        return _emit(a, r, lambda d: [green("✓ ") + "sent" if d["sent"] else yellow("not configured")])
    if act == "remove":
        manage.remove_telegram(p)
        return _emit(a, {"removed": True}, lambda d: [green("✓ ") + "credentials deleted and Telegram disabled"])
    if act == "gateway":
        sub = args[0] if args else "status"
        if sub == "run":
            import asyncio
            return asyncio.run(gateway.Gateway().run())
        r = gateway.control(sub)
        return _emit(a, r, lambda d: ["gateway " + (f"running (pid {d['pid']})" if d["running"] else "not running")])
    raise ResearchError(f"unknown chat action {act}")


def cmd_telegram(a, p) -> int:
    """Old spelling. `poll` runs one gateway receive pass (debugging)."""
    from research.chat import gateway, telegram
    if a.action == "poll":
        return _emit(a, {"handled": gateway.poll_once(p)}, lambda d: [f"{d['handled']} message(s) handled"])
    if a.action == "status":      # the old output, byte for byte (scripts parse it)
        return _emit(a, telegram.status(p), lambda d: [("token saved" if d["token_set"] else "no token") + ", " + ("chat linked" if d["chat_id_set"] else "no chat linked")
                                                       + f", {'enabled' if d['enabled'] else 'disabled'} for this project; stored at {d['stored_at']}"])
    a.args = []
    return cmd_chat(a, p)


cmd_chat = mut(cmd_chat)
cmd_telegram = mut(cmd_telegram)


def attach(sub, add, group) -> None:
    x = add(sub, "chat", cmd_chat, "chat gateway (opt-in): setup [telegram] | status | pair CODE | unpair PLATFORM USER | test | remove | gateway start|stop|restart|status|run")
    x.add_argument("action", choices=["setup", "status", "pair", "unpair", "test", "remove", "gateway"])
    x.add_argument("args", nargs="*", help="setup: the platform · pair: the code · gateway: start|stop|restart|status|run")
    x.add_argument("--token", help="setup: the bot token (prefer the hidden prompt; arguments are visible in process lists)")
    x.add_argument("--chat-id", dest="chat_id", help="setup: the chat id (default: the first chat that messages the bot)")
    x.add_argument("--role", choices=["viewer", "operator", "approver"], help="pair: the role (default: operator for the first user, viewer after)")
    x = add(sub, "telegram", cmd_telegram, None)
    x.add_argument("action", choices=["setup", "status", "test", "remove", "poll"])
    x.add_argument("--token")
    x.add_argument("--chat-id", dest="chat_id")
    x.set_defaults(role=None, args=[])
