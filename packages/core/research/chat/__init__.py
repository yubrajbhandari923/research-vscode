"""Chat gateway: platform-neutral core (Message/Button/Channel, router, pairing, alerts, gateway process) and the adapters.

One gateway process per machine owns every chat connection; supervisors only write alerts to an outbox. Telegram (stdlib) lives in
`telegram.py`; a later `discord_bot.py` implements the same `Channel` (discord.py as an optional extra). Machine-level state is 0600
under `<config dir>/gateway/`. Nothing here ever handles the approval PIN.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ROLES = ("viewer", "operator", "approver")          # ascending; `approver` may answer H.9 code prompts


def gdir(*parts: str) -> str:
    from .. import machine
    return os.path.join(os.path.dirname(machine.path()), "gateway", *parts)


def machine_register(root: str) -> None:
    from .. import machine
    machine.remember(root)


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path: str, d: Any) -> None:
    """Atomic, mode 0600 from creation (tokens, allow-lists and action tokens live here)."""
    from .. import durability
    durability.replace_bytes(path, (json.dumps(d, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8"), 0o600)


def txn(name: str):
    """Exclusive lock for one machine-level state file family (`gateway/<name>.lock`)."""
    from .. import durability
    os.makedirs(gdir(), exist_ok=True)
    return durability.lock(gdir(name + ".lock"))


@dataclass
class Button:
    label: str
    token: str                    # from actions.issue; never raw parameters
    style: str = "default"        # default | primary | danger


@dataclass
class Message:
    text: str = ""
    title: Optional[str] = None
    fields: List[Tuple[str, str]] = field(default_factory=list)
    color: str = "info"           # ok | warn | error | info
    buttons: List[Button] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    thread_key: Optional[str] = None
    ephemeral: bool = False
    project: str = ""
    chat: Optional[str] = None    # destination override (reply to where the command came from); None = the platform's home
    direct: bool = False          # True: `chat` is where the asker is (an /ask answer), so it wins over the project channel and the home channel

    def plain(self) -> str:
        """Plain-text rendering (Telegram; also what tests assert on)."""
        head = (self.title + "\n") if self.title else ""
        body = "\n".join(f"{k}: {v}" for k, v in self.fields)
        return (head + self.text + (("\n" + body) if body else "")).strip()


@dataclass
class Incoming:
    """One user action, platform-neutral. `token` is set for a button click, `text` for a message or command."""
    platform: str
    user_id: str
    user_name: str = ""
    chat: str = ""
    text: str = ""
    token: Optional[str] = None
    group: bool = False
    is_bot: bool = False
    reply_to: Optional[str] = None      # ref of the bot message this answers
    ref: Optional[str] = None           # ref of this message (or of the clicked card)


@dataclass
class Reply:
    messages: List[Message] = field(default_factory=list)
    toast: Optional[str] = None         # short answer to a click (answerCallbackQuery / ephemeral)
    edit: Optional[Message] = None      # replaces the clicked card (buttons removed)
    delete: List[str] = field(default_factory=list)   # refs to delete (a typed authenticator code)


class Channel:
    """Adapter interface. Telegram implements it with urllib; Discord (WP15) with discord.py. `inbox` is a `commands.Inbox`
    (blocking calls: adapters on an event loop run them in an executor)."""
    platform = ""

    def ready(self) -> bool:
        raise NotImplementedError

    async def run(self, inbox: Any) -> None:
        raise NotImplementedError

    async def deliver(self, m: Message) -> Optional[str]:
        raise NotImplementedError

    async def edit(self, ref: str, m: Message) -> None:
        raise NotImplementedError

    async def delete(self, ref: str) -> None:
        return None

    async def close(self) -> None:
        return None
