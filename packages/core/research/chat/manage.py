"""Setup and status shared by the CLI (`research chat ...`), the RPC ops and the old `research telegram ...` aliases."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..util import ResearchError
from . import gateway, pairing, telegram


def status(p=None) -> Dict[str, Any]:
    tg = telegram.status(p)
    return {"platforms": {"telegram": dict(tg, users=pairing.users("telegram"))}, "gateway": gateway.status(),
            "discord": {"available": False, "note": "Discord arrives with the optional discord extra"}}


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
