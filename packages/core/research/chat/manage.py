"""Setup and status shared by the CLI (`research chat ...`), the RPC ops and the old `research telegram ...` aliases."""
from __future__ import annotations

from typing import Any, Dict, Optional

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


def setup_discord(p, token: str, guild_id: Optional[str] = None, home_channel_id: Optional[str] = None, channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Store the token (0600, this machine only), remember the guild, enable Discord for this project and make sure the gateway runs.
    `guild_id` may be omitted when the bot is in exactly one guild. `channel_id` becomes this project's channel (also the home channel unless one is set)."""
    discord_bot.need_discord()
    info = inspect_discord(token)
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
        import os
        kw["project_channels"] = {**(discord_bot.credentials().get("project_channels") or {}), os.path.basename(p.root.rstrip("/")): str(channel_id)}
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
    return {"sent": discord_bot.send_rest("Research OS test message. Use /help for commands.")}
