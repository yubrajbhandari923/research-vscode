"""`research chat ...`: chat gateway setup and pairing (Telegram and Discord). `research telegram ...` stays as a hidden alias."""
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
    t, dc, g = d["platforms"]["telegram"], d["platforms"]["discord"], d["gateway"]
    lines = [("telegram: " + ("token saved" if t["token_set"] else "no token (set up with `research chat setup telegram`)") + ("" if not t["token_set"] else
              ", " + ("chat linked" if t["chat_id_set"] else "no chat linked (message the bot, then research chat setup telegram)")
              + f", {'enabled' if t['enabled'] else 'disabled'} for this project; stored at {t['stored_at']}"))]
    lines += [f"  {u['user_id']}  {u['role']}  {u.get('label') or ''}" for u in t["users"]] or ["  no paired users (message the bot to get a pairing code, then research chat pair CODE)"]
    lines.append("gateway: " + (f"running (pid {g['pid']}, {', '.join(g['platforms']) or 'no platform'})" + (" - older than the installed version: research chat gateway restart" if g["stale"] else "")
                               if g["running"] else "not running" + (" - start it with `research chat gateway start` (also starts with `research supervisor start`); "
                                                                      "`research chat gateway run` shows errors in the foreground" if t["token_set"] or dc["token_set"] else "")))
    lines.append("discord: " + ("token saved" if dc["token_set"] else "no token (set up with `research chat setup discord`)") + ("" if not dc["token_set"] else
                 (", server linked" if dc["guild_set"] else ", no server (open the invite URL from `research chat setup discord`)")
                 + (", home channel set" if dc["home_channel_set"] else ", no home channel (type /sethome in the alert channel)") + f", {'enabled' if dc['enabled'] else 'disabled'} for this project"
                 + ("" if dc["library"] else f"; discord.py missing: {dc['install']}")))
    if dc["token_set"]:
        lines += [f"  {u['user_id']}  {u['role']}  {u.get('label') or ''}" for u in dc["users"]] or ["  no paired users (DM the bot to get a pairing code, then research chat pair CODE)"]
    lines.append(dim("something wrong? `research chat doctor` checks each step and prints the fix; add --live to test the saved tokens"))
    return lines


def _doctor_lines(d):
    mark = {"ok": green("✓ "), "info": dim("· "), "warn": yellow("! "), "error": yellow("✗ ")}
    out = []
    for c in d["checks"]:
        out.append(mark[c["level"]] + c["message"])
        if c["fix"]:
            out.append(dim("    → " + c["fix"]))
    n = d["counts"]
    out.append(f"{n['error']} error(s), {n['warn']} warning(s)" + ("" if d["live"] else dim("   (offline; --live also tests the saved tokens)")))
    return out


def _setup_discord(a, p) -> int:
    import sys
    from research.chat import discord_bot, manage, pairing
    discord_bot.need_discord()                                  # a clear install line before anything is asked
    print(discord_bot.PORTAL_STEPS)
    token = a.token or AP.read_pin("Bot token (not shown): ")
    if not token:
        raise ResearchError("No token entered (a terminal is needed, or pass --token)")
    discord_bot.NOTICE = lambda m: print(yellow("! ") + m)       # say why we are waiting when Discord rate-limits (429)
    info = manage.inspect_discord(token)                        # GET /users/@me, then /users/@me/guilds
    print(green("✓ ") + f"token is valid for bot {info['name']}\nInvite URL (scopes bot + applications.commands, minimal permissions):\n  {info['invite_url']}")
    gs, guild = info["guilds"], a.guild
    tty = sys.stdin.isatty()
    while not gs and not guild and tty:
        input("Open the URL, add the bot to your server, then press Enter… ")
        gs = discord_bot.guilds(token.strip())
    if not guild and len(gs) > 1:
        for i, g in enumerate(gs, 1):
            print(f"  {i}. {g['name']} ({g['id']})")
        guild = gs[int(input("Which server? number: ") or 0) - 1]["id"] if tty else None
    r = manage.setup_discord(p, token, guild, channel_id=a.channel, info={**info, "guilds": gs})
    print(green("✓ ") + f"Discord connected to {r['guild']}; token stored only on this machine (0600)")
    print(green("✓ ") + "chat gateway " + ("running" if r["gateway"].get("running") or r["gateway"].get("action") in ("none", "start") else "will start with the supervisor"))
    print("Now send the bot a direct message; it replies with a pairing code. Run /sethome in the channel that should receive alerts.")
    code = input("Pairing code (blank to do it later with `research chat pair CODE`): ").strip() if tty else ""
    if code:
        u = pairing.approve(code, a.role)
        print(green("✓ ") + f"{u.get('label') or u['user_id']} paired as {u['role']}")
    return _emit(a, {k: v for k, v in r.items() if k not in ("supervisor", "gateway")}, None)


def _setup(a, p, platform: str) -> int:
    from research.chat import manage
    if platform == "discord":
        return _setup_discord(a, p)
    if platform != "telegram":
        raise ResearchError(f"unknown platform {platform} (telegram or discord)")
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
    if act == "doctor":
        r = manage.doctor(p, live=a.live)
        _emit(a, r, _doctor_lines)
        return 1 if r["counts"]["error"] else 0
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
        r = manage.test_discord() if args[:1] == ["discord"] else manage.test_telegram()
        return _emit(a, r, lambda d: [green("✓ ") + "sent" if d["sent"] else yellow("not configured")])
    if act == "remove":
        dc = args[:1] == ["discord"]
        (manage.remove_discord if dc else manage.remove_telegram)(p)
        return _emit(a, {"removed": True}, lambda d: [green("✓ ") + "credentials deleted and " + ("Discord" if dc else "Telegram") + " disabled"])
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
    x = add(sub, "chat", cmd_chat, "chat gateway (opt-in): setup [telegram|discord] | status | doctor [--live] | pair CODE | unpair PLATFORM USER | test [PLATFORM] | remove [PLATFORM] | gateway start|stop|restart|status|run")
    x.add_argument("action", choices=["setup", "status", "doctor", "pair", "unpair", "test", "remove", "gateway"])
    x.add_argument("args", nargs="*", help="setup: the platform · pair: the code · gateway: start|stop|restart|status|run")
    x.add_argument("--token", help="setup: the bot token (prefer the hidden prompt; arguments are visible in process lists)")
    x.add_argument("--chat-id", dest="chat_id", help="setup: the chat id (default: the first chat that messages the bot)")
    x.add_argument("--role", choices=["viewer", "operator", "approver"], help="pair: the role (default: operator for the first user, viewer after)")
    x.add_argument("--live", action="store_true", help="doctor: also ask Telegram/Discord whether the saved tokens still work")
    x.add_argument("--guild", help="setup discord: the server id (default: the one the bot is in)")
    x.add_argument("--channel", help="setup discord: the channel id for this project's alerts (also the home channel)")
    x = add(sub, "telegram", cmd_telegram, None)
    x.add_argument("action", choices=["setup", "status", "test", "remove", "poll"])
    x.add_argument("--token")
    x.add_argument("--chat-id", dest="chat_id")
    x.set_defaults(role=None, args=[], guild=None, channel=None, live=False)
