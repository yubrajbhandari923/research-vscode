"""Setup and status shared by the CLI (`research chat ...`), the RPC ops and the old `research telegram ...` aliases."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..util import ResearchError
from . import discord_bot, gateway, pairing, telegram


def status(p=None) -> Dict[str, Any]:
    tg = telegram.status(p)
    return {"platforms": {"telegram": dict(tg, users=pairing.users("telegram")), "discord": dict(discord_bot.status(p), users=pairing.users("discord"))}, "gateway": gateway.status()}


def setup_telegram(p, token: str, chat_id: Optional[Any] = None, discover: bool = False, wait: float = 60.0) -> Dict[str, Any]:
    """Store the token (0600, this machine only), enable chat for this project, migrate the chat id into the allow-list, greet, and make
    sure the gateway runs. The one function behind the CLI, the extension and the old `telegram setup`."""
    token = (token or "").strip()
    if chat_id is None and discover:
        chat_id = telegram.discover_chat(token, wait)
        if chat_id is None:
            raise ResearchError("No message reached the bot", hint="Open your bot in Telegram, press Start or send any message, then try again.")
    telegram.set_credentials(token, chat_id)         # validates the token shape
    p.save_config({"notifications.telegram.enabled": True, "notifications.auto_debug": True})
    p.config = p.load_config()
    migrated = pairing.migrate_telegram(chat_id) if chat_id is not None else None
    try:
        telegram.send("Research OS is connected. Send /help for commands.")
    except ResearchError:
        pass
    from . import machine_register
    machine_register(p.root)
    gw = gateway.ensure_running(p)
    from .. import notify
    return {**telegram.status(p), "migrated_user": bool(migrated), "supervisor": notify.ensure_running(p), "gateway": gw}


def remove_telegram(p) -> Dict[str, Any]:
    telegram.clear_credentials()
    p.save_config({"notifications.telegram.enabled": False})
    return {"removed": True}


def test_telegram() -> Dict[str, Any]:
    return {"sent": telegram.send("Research OS test message. Send /help for commands.")}


# ----------------------------------------------------------------------------------------------- Discord
def inspect_discord(token: str) -> Dict[str, Any]:
    """Validate the token (GET /users/@me), list the guilds the bot is in (GET /users/@me/guilds) and build the invite URL. Stores nothing."""
    token = (token or "").strip()
    me = discord_bot.inspect_token(token)
    return {**me, "guilds": discord_bot.guilds(token), "invite_url": discord_bot.invite_url(me["application_id"])}


def setup_discord(p, token: str, guild_id: Optional[str] = None, home_channel_id: Optional[str] = None, channel_id: Optional[str] = None,
                  info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Store the token (0600, this machine only), remember the guild, enable Discord for this project and make sure the gateway runs.
    `guild_id` may be omitted when the bot is in exactly one guild. `channel_id` becomes this project's channel (also the home channel unless one is set)."""
    discord_bot.need_discord()
    info = info or inspect_discord(token)      # a caller that already inspected passes it: every extra call counts against Discord's rate limit
    gs = {g["id"]: g for g in info["guilds"]}
    if guild_id is None and len(gs) == 1:
        guild_id = next(iter(gs))
    if guild_id is None:
        raise ResearchError("The bot is not in a server yet" if not gs else "The bot is in several servers: say which (--guild ID)",
                            hint="Open the invite URL: " + info["invite_url"] if not gs else "Servers: " + ", ".join(f"{g['name']} ({g['id']})" for g in gs.values()))
    if guild_id not in gs:
        raise ResearchError("The bot is not in that server", hint="Open the invite URL: " + info["invite_url"])
    kw: Dict[str, Any] = {"token": token.strip(), "application_id": info["application_id"], "guild_id": guild_id}
    if home_channel_id or channel_id:
        kw["home_channel_id"] = str(home_channel_id or channel_id)
    if channel_id:
        from . import commands
        kw["project_channels"] = {**(discord_bot.credentials().get("project_channels") or {}), commands.project_label(p.root): str(channel_id)}
    discord_bot.save(**kw)
    p.save_config({"notifications.discord.enabled": True})
    p.config = p.load_config()
    from . import machine_register
    machine_register(p.root)
    from .. import notify
    return {**discord_bot.status(p), "bot": info["name"], "guild": gs[guild_id]["name"], "supervisor": notify.ensure_running(p), "gateway": gateway.ensure_running(p)}


def remove_discord(p) -> Dict[str, Any]:
    discord_bot.clear_credentials()
    p.save_config({"notifications.discord.enabled": False})
    return {"removed": True}


def test_discord() -> Dict[str, Any]:
    if not discord_bot.credentials().get("token"):
        return {"sent": False, "reason": "no Discord token: run `research chat setup discord`"}
    home = discord_bot.home_channel()
    if not home:
        return {"sent": False, "reason": "no home channel: type /sethome in the channel that should receive alerts (or `research chat setup discord --channel ID`)"}
    return {"sent": discord_bot.send_rest("Research OS test message. Use /help for commands.", home), "channel": home}


# ----------------------------------------------------------------------------------------------- doctor
def doctor(p, live: bool = False) -> Dict[str, Any]:
    """What is set up, what is missing and the exact command that fixes each thing. Offline by default; `live` asks Telegram/Discord whether the saved
    token still works (one or two requests each)."""
    st = status(p)
    gw = st["gateway"]
    out: List[Dict[str, Any]] = []

    def add(cid: str, level: str, msg: str, fix: Optional[str] = None) -> None:
        out.append({"id": cid, "level": level, "message": msg, "fix": fix})

    configured = []
    t = st["platforms"]["telegram"]
    if not t["token_set"]:
        add("telegram", "info", "Telegram is not set up (optional)", "research chat setup telegram")
    else:
        configured.append("telegram")
        add("telegram.token", "ok", "Telegram token saved on this machine")
        add("telegram.enabled", "ok" if t["enabled"] else "warn", "Telegram is enabled for this project" if t["enabled"] else "Telegram is disabled for this project",
            None if t["enabled"] else "research chat setup telegram")
        add("telegram.chat", "ok" if t["chat_id_set"] else "warn", "a chat is linked" if t["chat_id_set"] else "no chat is linked, so alerts have nowhere to go",
            None if t["chat_id_set"] else "send your bot any message, then run `research chat setup telegram`")
        if not t["chat_id_set"] and not pairing.users("telegram"):
            add("telegram.users", "warn", "nobody is paired", "message the bot to get a code, then `research chat pair CODE`")
        if live:
            try:
                me = telegram._api(telegram.credentials()["token"], "getMe", {})
                add("telegram.live", "ok", "Telegram accepts the token" + (f" (bot @{me['result']['username']})" if isinstance(me.get("result"), dict) and me["result"].get("username") else ""))
            except ResearchError as e:
                add("telegram.live", "error", f"Telegram did not accept the saved token: {e}", "research chat setup telegram")
    d = st["platforms"]["discord"]
    if not d["token_set"]:
        add("discord", "info", "Discord is not set up (optional)", "research chat setup discord")
    else:
        configured.append("discord")
        add("discord.library", "ok" if d["library"] else "error", "discord.py is installed" if d["library"] else "discord.py is not installed, so the gateway cannot start Discord",
            None if d["library"] else d["install"])
        add("discord.enabled", "ok" if d["enabled"] is not False else "warn", "Discord is enabled for this project" if d["enabled"] is not False else "Discord is disabled for this project",
            None if d["enabled"] is not False else "research chat setup discord")
        add("discord.guild", "ok" if d["guild_set"] else "error", "a server is linked" if d["guild_set"] else "no server is linked",
            None if d["guild_set"] else "research chat setup discord   (it prints the invite URL)")
        add("discord.home", "ok" if d["home_channel_set"] else "warn", "a home channel is set" if d["home_channel_set"] else "no home channel, so alerts have nowhere to go",
            None if d["home_channel_set"] else "type /sethome in the channel that should receive alerts, or `research chat setup discord --channel ID`")
        if not pairing.users("discord"):
            add("discord.users", "warn", "nobody is paired", "send the bot a direct message to get a code, then `research chat pair CODE`")
        if live:
            try:
                info = inspect_discord(discord_bot.credentials()["token"])
                add("discord.live", "ok", f"Discord accepts the token (bot {info['name']}, in {len(info['guilds'])} server(s))")
                gid = discord_bot.credentials().get("guild_id")
                if gid and gid not in {g["id"] for g in info["guilds"]}:
                    add("discord.member", "error", "the bot is no longer in the linked server", "re-invite it: " + info["invite_url"])
            except ResearchError as e:
                add("discord.live", "error", f"{e}" + (f" ({e.hint})" if getattr(e, "hint", None) else ""), "research chat doctor --live   (run again)")
    if not configured:
        add("gateway", "info", "no chat platform is set up, so the gateway has nothing to do")
    elif gw["running"] and gw.get("stale"):
        add("gateway", "warn", f"gateway is running (pid {gw['pid']}) but is older than the installed Research OS", "research chat gateway restart")
    elif gw["running"]:
        missing = [x for x in configured if x not in (gw.get("platforms") or [])]
        add("gateway", "warn" if missing else "ok", f"gateway is running (pid {gw['pid']}, {', '.join(gw.get('platforms') or []) or 'no platform'})"
            + (f"; {', '.join(missing)} is configured but not connected" if missing else ""),
            "research chat gateway restart" if missing else None)
    else:
        add("gateway", "error", "the gateway is not running: nothing answers in chat and no alerts are sent",
            "research chat gateway start   (it also starts with `research supervisor start`; `research chat gateway run` runs it in the foreground to see errors)")
    n = {k: sum(1 for c in out if c["level"] == k) for k in ("error", "warn", "info", "ok")}
    return {"checks": out, "counts": n, "ok": not n["error"] and not n["warn"], "live": live}
