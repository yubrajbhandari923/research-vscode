"""Telegram adapter (Bot API over urllib, no dependencies) and the machine-local credentials file.

The token and chat id live in `<config dir>/telegram.json` (0600, next to the approval PIN), never in the project, prompts or events.
Same file, same `notifications.telegram.enabled` as before the gateway existed: an old setup keeps working unedited.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from ..util import ResearchError
from . import Channel, Incoming, Message, gdir, read_json, txn, write_json

API = "https://api.telegram.org"
CHUNK = 3800                    # Telegram's limit is 4096 characters
MAX_FILE = 20 * 1024 * 1024     # keep uploads small for phones


# ----------------------------------------------------------------------------------------------- credentials (machine-local)
def path() -> str:
    from ..configfile import machine_path
    return os.path.join(os.path.dirname(machine_path() or os.path.expanduser("~/.config/research-os/config.yaml")), "telegram.json")


def credentials() -> Dict[str, Any]:
    try:
        with open(path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    try:
        if os.stat(path()).st_mode & 0o077:
            os.chmod(path(), 0o600)             # a file written by an older version or by hand: tighten, never leave it readable
    except OSError:
        pass
    return d


def set_credentials(token: str, chat_id: Optional[Any] = None) -> None:
    token = (token or "").strip()
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
        raise ResearchError("That does not look like a Telegram bot token (expected 123456:ABC…) ", hint="Create a bot with @BotFather and copy its token.")
    write_json(path(), {"token": token, "chat_id": chat_id})


def clear_credentials() -> None:
    try:
        os.unlink(path())
    except OSError:
        pass


def status(p=None) -> Dict[str, Any]:
    c = credentials()
    en = bool(((p.config.get("notifications") or {}).get("telegram") or {}).get("enabled")) if p is not None else None
    return {"token_set": bool(c.get("token")), "chat_id_set": c.get("chat_id") is not None, "enabled": en,
            "ready": bool(c.get("token") and c.get("chat_id") is not None and en is not False), "stored_at": path()}


def _base() -> str:
    return (os.environ.get("RESEARCH_TELEGRAM_API") or API).rstrip("/")      # override: a proxy, or a local fake in tests


def _call(req: urllib.request.Request, timeout: float) -> Dict[str, Any]:
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


def _api(token: str, method: str, params: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    """The network call for everything but uploads. The token appears only in the URL path, never in logs, errors or events."""
    return _call(urllib.request.Request(f"{_base()}/bot{token}/{method}", data=urllib.parse.urlencode(params).encode(), method="POST"), timeout)


def _upload(token: str, method: str, fields: Dict[str, Any], filename: str, data: bytes, timeout: float = 60.0) -> Dict[str, Any]:
    """multipart/form-data for sendDocument (stdlib has no encoder)."""
    b = uuid.uuid4().hex
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body += f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"{_base()}/bot{token}/{method}", data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    return _call(req, timeout)


def send(text: str, chat_id: Optional[Any] = None) -> bool:
    """One plain message to the stored chat (setup/test and the old API). False when not configured."""
    c = credentials()
    if not c.get("token") or (chat_id or c.get("chat_id")) is None:
        return False
    text = clean(text)
    for i in range(0, len(text), CHUNK):
        _api(c["token"], "sendMessage", {"chat_id": chat_id or c["chat_id"], "text": text[i:i + CHUNK]})
    return True


def discover_chat(token: str, wait: float = 60.0) -> Optional[Any]:
    """Setup helper: the first chat that messages the bot becomes the home chat. Waits at most `wait` seconds."""
    import time
    t0, offset = time.time(), 0
    while time.time() - t0 < wait:
        for u in _api(token, "getUpdates", {"timeout": 0, "offset": offset}, 10).get("result", []):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if (m.get("chat") or {}).get("id") is not None:
                return m["chat"]["id"]
        time.sleep(2)
    return None


# ----------------------------------------------------------------------------------------------- the channel
_BOT_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")


def clean(text: Optional[str]) -> str:
    """Everything outbound: the project's redaction plus anything shaped like a bot token."""
    from ..taskprompt import redact
    return _BOT_TOKEN.sub("[REDACTED]", redact(text) or "")


def _markup(m: Message) -> Optional[str]:
    if not m.buttons:
        return None
    rows = [[{"text": b.label[:40], "callback_data": "a:" + b.token} for b in m.buttons[i:i + 2]] for i in range(0, len(m.buttons), 2)]
    return json.dumps({"inline_keyboard": rows})


class TelegramChannel(Channel):
    platform = "telegram"

    def __init__(self, commands: Optional[List[Any]] = None):
        self.commands = commands or []              # [(name, description)] for setMyCommands
        self._registered = False

    @property
    def creds(self) -> Dict[str, Any]:
        return credentials()

    def ready(self) -> bool:
        c = self.creds
        return bool(c.get("token") and c.get("chat_id") is not None)

    # --- sending (blocking; the async wrappers run these in an executor)
    def send_message(self, m: Message) -> Optional[str]:
        c = self.creds
        chat = m.chat or c.get("chat_id")
        if not c.get("token") or chat is None:
            return None
        text = clean(m.plain()) or "(empty)"
        parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
        ref = None
        for i, part in enumerate(parts):
            params = {"chat_id": chat, "text": part}
            if i == len(parts) - 1 and _markup(m):
                params["reply_markup"] = _markup(m)
            d = _api(c["token"], "sendMessage", params)
            ref = ref or f"{chat}:{(d.get('result') or {}).get('message_id', 0)}"
        for f in m.files[:4]:
            try:
                if os.path.isfile(f) and os.path.getsize(f) <= MAX_FILE:
                    with open(f, "rb") as fh:
                        _upload(c["token"], "sendDocument", {"chat_id": chat}, clean(os.path.basename(f)) or "file", fh.read())
                else:
                    _api(c["token"], "sendMessage", {"chat_id": chat, "text": f"(file too large or missing; it is at {clean(f)} on the machine)"})
            except (ResearchError, OSError):
                pass
        return ref

    def edit_message(self, ref: str, m: Message) -> None:
        c = self.creds
        chat, _, mid = ref.partition(":")
        if not c.get("token") or not mid:
            return
        base = {"chat_id": chat, "message_id": mid}
        markup = _markup(m) or json.dumps({"inline_keyboard": []})
        if m.text or m.title:
            _api(c["token"], "editMessageText", {**base, "text": clean(m.plain())[:CHUNK], "reply_markup": markup})
        else:
            _api(c["token"], "editMessageReplyMarkup", {**base, "reply_markup": markup})

    def delete_message(self, ref: str) -> None:
        chat, _, mid = ref.partition(":")
        if self.creds.get("token") and mid:
            _api(self.creds["token"], "deleteMessage", {"chat_id": chat, "message_id": mid})

    def register_commands(self) -> None:
        c = self.creds
        if c.get("token") and self.commands:
            _api(c["token"], "setMyCommands", {"commands": json.dumps([{"command": n.replace("-", "_"), "description": d[:200]} for n, d in self.commands])})

    # --- receiving
    @staticmethod
    def _offset() -> int:
        d = read_json(gdir("cursors.json"))
        if "telegram_offset" in d:
            return int(d["telegram_offset"])
        return int(credentials().get("offset") or 0)             # an offset written by the pre-gateway version: never replay old updates

    @staticmethod
    def _save_offset(n: int) -> None:
        with txn("cursors"):
            d = read_json(gdir("cursors.json"))
            d["telegram_offset"] = n
            write_json(gdir("cursors.json"), d)

    def poll_once(self, inbox: Any, timeout: int = 0) -> int:
        """One getUpdates pass. The offset is saved BEFORE each update is handled (at-most-once for commands with side effects), so a
        failing send never replays it. Returns the number of updates handled."""
        c = self.creds
        if not c.get("token"):
            return 0
        n = 0
        for u in _api(c["token"], "getUpdates", {"timeout": timeout, "offset": self._offset()}, timeout + 10).get("result", []):
            self._save_offset(u["update_id"] + 1)
            inc = self._incoming(u)
            if inc is None:
                continue
            n += 1
            try:
                reply = inbox.click(inc) if inc.token else inbox.command(inc)
                if inc.token and u.get("callback_query"):
                    _api(c["token"], "answerCallbackQuery", {"callback_query_id": u["callback_query"]["id"], "text": (reply.toast or "")[:190]})
                if reply.edit and inc.ref:
                    self.edit_message(inc.ref, reply.edit)
                for m in reply.messages:
                    self.send_message(m if m.chat else Message(**{**m.__dict__, "chat": inc.chat}))
                for r in reply.delete:
                    self.delete_message(r)
            except (ResearchError, OSError):
                pass            # a failing send must not stop the loop; the offset is already saved
        return n

    @staticmethod
    def _incoming(u: Dict[str, Any]) -> Optional[Incoming]:
        cq = u.get("callback_query")
        m = (cq or {}).get("message") if cq else u.get("message")
        if not m:
            return None
        chat = m.get("chat") or {}
        frm = (cq or {}).get("from") if cq else m.get("from")
        group = chat.get("type") in ("group", "supergroup")
        uid = (frm or {}).get("id")
        if uid is None and not group:
            uid = chat.get("id")            # private chat: Telegram's chat id equals the user id (old clients and fakes omit `from`)
        if uid is None or chat.get("id") is None:
            return None
        name = (frm or {}).get("username") or (frm or {}).get("first_name") or str(uid)
        ref = f"{chat['id']}:{m.get('message_id', 0)}"
        reply_to = (m.get("reply_to_message") or {}).get("message_id")
        base = dict(platform="telegram", user_id=str(uid), user_name=str(name), chat=str(chat["id"]), group=group,
                    is_bot=bool((frm or {}).get("is_bot")), ref=ref)
        if cq:
            data = cq.get("data") or ""
            return Incoming(**base, token=data[2:] if data.startswith("a:") else "invalid") if data else None
        if not m.get("text"):
            return None
        return Incoming(**base, text=m["text"].strip(), reply_to=f"{chat['id']}:{reply_to}" if reply_to else None)

    # --- Channel
    async def run(self, inbox: Any) -> None:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self.register_commands)
        except ResearchError:
            pass
        while True:
            try:
                n = await loop.run_in_executor(None, self.poll_once, inbox, 20)
            except ResearchError:
                n = 0
            except asyncio.CancelledError:
                raise
            if not n:
                await asyncio.sleep(1)

    async def deliver(self, m: Message) -> Optional[str]:
        return await asyncio.get_event_loop().run_in_executor(None, self.send_message, m)

    async def edit(self, ref: str, m: Message) -> None:
        await asyncio.get_event_loop().run_in_executor(None, self.edit_message, ref, m)

    async def delete(self, ref: str) -> None:
        await asyncio.get_event_loop().run_in_executor(None, self.delete_message, ref)
