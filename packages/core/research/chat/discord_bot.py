"""Discord adapter: the same `Channel` as Telegram, on discord.py (optional extra `research-panel[discord]`, imported lazily).

Outbound gateway connection only (no public URL). Everything platform-specific is behind the thin `DiscordTransport` protocol: this
module's logic (rendering, chunking, threads, modals, pacing, allow-lists) never touches discord.py, so tests run it against a fake
transport, and `RealTransport` is the only code that does. Interaction data stays raw (`interaction.data`), so slash commands are
registered from the table below as plain JSON with typed options. No MESSAGE_CONTENT intent: only mentions, DMs and slash commands.

The approval PIN is never requested, accepted or shown. The authenticator code (H.9) is typed into an ephemeral modal (6 digits, validated
here before it goes anywhere) and reaches only the op, through the router's unchanged ask_code flow.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple


from ..util import ResearchError
from . import Channel, Incoming, Message, Reply, actions, commands, gdir, pairing, read_json, txn, write_json
from .telegram import clean as _clean_tg

REST = "https://discord.com/api/v10"
EMBED_MAX, CONTENT_MAX, FIELD_MAX = 4096, 2000, 1024
MAX_FILE = 8 * 1024 * 1024
THREAD_ARCHIVE = 10080                   # one week, in minutes
COLORS = {"ok": 0x2ECC71, "warn": 0xF1C40F, "error": 0xE74C3C, "info": 0x3498DB}
# VIEW_CHANNEL, SEND_MESSAGES, EMBED_LINKS, ATTACH_FILES, READ_MESSAGE_HISTORY, CREATE_PUBLIC_THREADS, SEND_MESSAGES_IN_THREADS
PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 14) | (1 << 15) | (1 << 16) | (1 << 35) | (1 << 38)
_DISCORD_TOKEN = re.compile(r"[MNO][A-Za-z\d_-]{22,}\.[\w-]{6}\.[\w-]{25,}")
_now = time.time


class RateLimited(Exception):
    def __init__(self, retry_after: float = 1.0):
        super().__init__("rate limited")
        self.retry_after = retry_after


# ----------------------------------------------------------------------------------------------- credentials (machine-local, 0600)
def path() -> str:
    from ..configfile import machine_path
    return os.path.join(os.path.dirname(machine_path() or os.path.expanduser("~/.config/research-os/config.yaml")), "discord.json")


def credentials() -> Dict[str, Any]:
    d = read_json(path())
    try:
        if os.stat(path()).st_mode & 0o077:
            os.chmod(path(), 0o600)
    except OSError:
        pass
    return d if isinstance(d, dict) else {}


def save(**kw: Any) -> Dict[str, Any]:
    with txn("discord"):
        d = credentials()
        d.update(kw)
        write_json(path(), d)          # atomic, 0600 from creation; the token is never logged or returned
        return d


def clear_credentials() -> None:
    try:
        os.unlink(path())
    except OSError:
        pass


def available() -> bool:
    try:
        import discord  # noqa: F401
        return True
    except ImportError:
        return False


def install_hint() -> str:
    import sys
    pre = sys.prefix.replace("\\", "/")
    if "/uv/tools/" in pre:
        return "uv tool install --force 'research-panel[discord]'"
    if "/pipx/" in pre:
        return "pipx inject research-panel discord.py"
    return "pip install 'research-panel[discord]'"


def need_discord() -> None:
    if not available():
        raise ResearchError("Discord needs the optional discord.py package (Python 3.10 or newer)", hint="Install it with: " + install_hint())


def status(p=None) -> Dict[str, Any]:
    c = credentials()
    en = bool(commands.registry.section(p, "notifications")["discord"].get("enabled")) if p is not None else None
    return {"token_set": bool(c.get("token")), "guild_set": bool(c.get("guild_id")), "home_channel_set": bool(c.get("home_channel_id")), "enabled": en,
            "library": available(), "install": None if available() else install_hint(), "stored_at": path(),
            "ready": bool(c.get("token") and c.get("guild_id") and available() and en is not False)}


# ----------------------------------------------------------------------------------------------- REST (setup and `test`; urllib)
def _rest(token: str, method: str, route: str, body: Optional[Dict[str, Any]] = None) -> Any:
    req = urllib.request.Request(REST + route, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bot " + token, "Content-Type": "application/json", "User-Agent": "DiscordBot (research-os, 1)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as ex:
        raise ResearchError("Discord rejected the token" if ex.code == 401 else f"Discord refused the request (HTTP {ex.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise ResearchError(f"Discord is unreachable: {type(ex).__name__}") from None


def inspect_token(token: str) -> Dict[str, Any]:
    """`GET /users/@me` validates the token; for a bot the user id is the application id."""
    me = _rest(token.strip(), "GET", "/users/@me")
    if not isinstance(me, dict) or not me.get("bot", True) or not me.get("id"):
        raise ResearchError("That token is not a bot token", hint="Use the Bot tab of your application in the Discord developer portal.")
    return {"application_id": str(me["id"]), "name": me.get("username", "")}


def guilds(token: str) -> List[Dict[str, str]]:
    return [{"id": str(g["id"]), "name": g.get("name", "")} for g in (_rest(token, "GET", "/users/@me/guilds") or [])]


def invite_url(application_id: str) -> str:
    return "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode({"client_id": application_id, "scope": "bot applications.commands",
                                                                           "permissions": PERMISSIONS}, quote_via=urllib.parse.quote)


PORTAL_STEPS = """In the Discord developer portal (https://discord.com/developers/applications):
  1. New Application, name it, then open the Bot tab.
  2. Reset Token and copy it (shown once; paste it here, it is not echoed).
  3. Turn OFF "Public Bot". Leave every Privileged Gateway Intent OFF (no Message Content needed).
  4. Open the invite URL below, choose your server, Authorize. The bot asks only for: send messages, embed links, attach files,
     create public threads, send messages in threads, read message history."""


def send_rest(text: str, channel_id: Optional[str] = None) -> bool:
    """One plain message to the home channel (setup test). False when not configured."""
    c = credentials()
    ch = channel_id or c.get("home_channel_id")
    if not c.get("token") or not ch:
        return False
    _rest(c["token"], "POST", f"/channels/{ch}/messages", {"content": clean(text)[:CONTENT_MAX], "allowed_mentions": {"parse": []}})
    return True


# ----------------------------------------------------------------------------------------------- slash command table (E.8)
# command -> options: (name, description, "str"|"int", required, choices tuple | autocomplete kind | None)
_PROJ = ("project", "Project (default: this channel's)", "str", False, "project")
SLASH: Dict[str, List[Tuple[str, str, str, bool, Any]]] = {
    "status": [_PROJ], "blocked": [_PROJ],
    "task": [("task", "Task id, e.g. T-12", "str", True, "task")],
    "logs": [("task", "Task id, e.g. T-12", "str", True, "task"), ("stream", "stdout or stderr", "str", False, ("stdout", "stderr"))],
    "report": [("scope", "What to report on", "str", False, None)],
    "finding": [("id", "Finding id, e.g. F-3", "str", True, None)],
    "usage": [("period", "today or week", "str", False, ("today", "week"))],
    "proposals": [], "interventions": [], "digest": [], "decisions": [], "options": [], "debug": [], "whoami": [], "help": [], "sethome": [],
    "project": [("action", "use or list", "str", True, ("use", "list")), ("name", "Project name", "str", False, "project")],
    "say": [("text", "Instruction for the supervisor", "str", True, None), ("task", "Task id (optional)", "str", False, "task")],
    "choose": [("n", "Option number", "int", True, None)],
    "pause": [("plan", "Plan", "str", False, "plan")], "resume": [("plan", "Plan", "str", False, "plan")],
    "stop": [("task", "Task whose current attempt to stop", "str", True, "task")],
    "continue": [("task", "Task id", "str", True, "task"), ("profile", "Agent profile to hand it to", "str", True, None)],
    "approve-start": [("task", "Task id", "str", True, "task")],
}
ORDER = {"say": ("task", "text")}        # argument order the router expects when it differs from the option order


def slash_payload() -> List[Dict[str, Any]]:
    desc = dict(commands.command_list())
    out = []
    for name, opts in SLASH.items():
        o = []
        for n, d, t, req, extra in opts:
            x: Dict[str, Any] = {"type": 4 if t == "int" else 3, "name": n, "description": d[:100], "required": req}
            if isinstance(extra, tuple):
                x["choices"] = [{"name": c, "value": c} for c in extra]
            elif extra:
                x["autocomplete"] = True
            o.append(x)
        o.sort(key=lambda x: not x["required"])      # Discord wants required options first
        out.append({"name": name, "type": 1, "description": (desc.get(name) or name)[:100], "options": o})
    return out


# ----------------------------------------------------------------------------------------------- rendering and chunking
def clean(text: Optional[str]) -> str:
    return _DISCORD_TOKEN.sub("[REDACTED]", _clean_tg(text))


def split(text: str, n: int) -> List[str]:
    """Pieces of at most n characters, cut at a line break when one falls in the last fifth."""
    out = []
    while len(text) > n:
        cut = text.rfind("\n", int(n * 0.8), n)
        cut = cut + 1 if cut > 0 else n
        out.append(text[:cut])
        text = text[cut:]
    return out + [text]


@dataclass
class Parts:
    """One Discord message, transport-neutral. In an edit, None means 'keep' and an empty `buttons` list means 'remove the components'."""
    content: Optional[str] = None
    embed: Optional[Dict[str, Any]] = None
    buttons: List[Tuple[str, str, str]] = field(default_factory=list)          # (label, custom_id, style)
    options: List[Tuple[str, str]] = field(default_factory=list)               # select menu (label, value)
    files: List[str] = field(default_factory=list)
    ephemeral: bool = False


def render(m: Message, limit: int = MAX_FILE, at: Optional[float] = None) -> List[Parts]:
    """A `Message` as an embed (color by severity, fields, project and time footer) plus continuation messages for overflow."""
    body = split(clean(m.text) or "(empty)", EMBED_MAX)
    first, rest = body[0], "".join(body[1:])
    files, notes = [], []
    for f in m.files[:4]:
        try:
            ok = os.path.isfile(f) and os.path.getsize(f) <= min(limit or MAX_FILE, MAX_FILE)
        except OSError:
            ok = False
        (files if ok else notes).append(f)
    if notes:
        first = split(first + "".join(f"\n({clean(os.path.basename(f))} is too large or missing; it is at {clean(f)} on the machine)" for f in notes), EMBED_MAX)[0]
    fields = [(k, v) for k, v in m.fields]
    if m.thread_key and not any(k.lower() == "task" for k, _ in fields):
        fields.insert(0, ("Task", m.thread_key))
    embed: Dict[str, Any] = {"description": first, "color": COLORS.get(m.color, COLORS["info"]),
                             "fields": [{"name": clean(k)[:256] or "-", "value": clean(v)[:FIELD_MAX] or "-", "inline": True} for k, v in fields[:25]],
                             "timestamp": datetime.fromtimestamp(at if at is not None else _now(), timezone.utc).isoformat()}
    if m.title:
        embed["title"] = clean(m.title)[:256]
    if m.project:
        embed["footer"] = {"text": m.project[:2048]}
    buttons = [(b.label[:80], "a:" + b.token, b.style) for b in m.buttons]
    options: List[Tuple[str, str]] = []
    if len(buttons) > 5:                                    # H.7: a select menu past five options
        options, buttons = [(b.label[:100], b.token) for b in m.buttons[:25]], []
    if m.thread_key and m.buttons:
        buttons.append(("Instruct", f"i:{m.thread_key}|{m.project}"[:100], "primary"))
    parts = [Parts(embed=embed, buttons=buttons, options=options, files=files, ephemeral=m.ephemeral)]
    parts += [Parts(content=c, ephemeral=m.ephemeral) for c in split(rest, CONTENT_MAX)] if rest else []
    return parts


# ----------------------------------------------------------------------------------------------- the transport seam
@dataclass
class Ix:
    """One event from Discord: a slash command, autocomplete, button, select, modal submit or a message (mention or DM)."""
    kind: str                       # command | autocomplete | button | select | modal | message
    user_id: str
    user_name: str = ""
    channel_id: str = ""
    parent_id: Optional[str] = None  # the channel a thread lives in
    guild_id: Optional[str] = None
    name: str = ""                  # command name
    options: Dict[str, Any] = field(default_factory=dict)
    focused: str = ""               # autocomplete: the option being typed
    custom_id: str = ""
    values: List[str] = field(default_factory=list)
    text: str = ""                  # message content, modal input, or the autocomplete prefix
    message_ref: Optional[str] = None
    reply_to: Optional[str] = None
    is_bot: bool = False
    attachment_limit: int = MAX_FILE
    impl: Any = None                # the transport's own interaction object

    @property
    def dm(self) -> bool:
        return self.guild_id is None


class DiscordTransport(Protocol):
    """Everything the adapter needs from discord.py. `RealTransport` implements it; tests use FakeDiscord."""
    async def start(self, dispatch: Callable[[Ix], Awaitable[None]], slash: List[Dict[str, Any]]) -> None: ...
    async def close(self) -> None: ...
    async def send(self, channel_id: str, parts: Parts) -> str: ...
    async def edit(self, channel_id: str, message_id: str, parts: Parts) -> None: ...
    async def delete(self, channel_id: str, message_id: str) -> None: ...
    async def create_thread(self, channel_id: str, name: str) -> str: ...
    async def defer(self, ix: Ix, ephemeral: bool) -> None: ...
    async def followup(self, ix: Ix, parts: Parts) -> None: ...
    async def edit_origin(self, ix: Ix, parts: Parts) -> None: ...
    async def reject(self, ix: Ix, text: str) -> None: ...
    async def send_modal(self, ix: Ix, custom_id: str, title: str, label: str, paragraph: bool, max_length: int) -> None: ...
    async def autocomplete(self, ix: Ix, choices: List[Tuple[str, str]]) -> None: ...


# ----------------------------------------------------------------------------------------------- the channel
def _safe(s: str, n: int = 100) -> str:
    return re.sub(r"\s+", " ", s).strip()[:n]


class DiscordChannel(Channel):
    platform = "discord"

    def __init__(self, transport: Optional[DiscordTransport] = None, gap: float = 1.0):
        if transport is None:
            need_discord()
            c = credentials()
            transport = RealTransport(c["token"], c.get("application_id", ""), c.get("guild_id", ""))
        self.t, self.gap, self.inbox = transport, gap, None
        self._last: Dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=4)      # core calls are blocking; the 3 s deadline is met by deferring first

    def ready(self) -> bool:
        c = credentials()
        return bool(c.get("token") and c.get("guild_id"))

    async def run(self, inbox: Any) -> None:
        self.inbox = inbox
        await self.t.start(self.dispatch, slash_payload())

    async def close(self) -> None:
        await self.t.close()

    async def _call(self, fn: Callable, *a: Any) -> Any:
        return await asyncio.get_event_loop().run_in_executor(self._pool, fn, *a)

    # --- sending with pacing and rate-limit retry
    async def _send(self, channel: str, parts: Parts) -> str:
        for attempt in range(4):
            wait = self.gap - (time.monotonic() - self._last.get(channel, -1e9))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[channel] = time.monotonic()
            try:
                return await self.t.send(channel, parts)
            except RateLimited as ex:
                if attempt == 3:
                    raise ResearchError("Discord rate limit; will retry") from None
                await asyncio.sleep(min(ex.retry_after, 30))
        raise ResearchError("Discord rate limit")                  # unreachable

    def _cfg_threads(self, label: str, task: str) -> Optional[str]:
        return ((credentials().get("threads") or {}).get(label) or {}).get(task)

    def _label_channel(self, label: str) -> Optional[str]:
        return (credentials().get("project_channels") or {}).get(label)

    def _thread_per_task(self, label: str) -> bool:
        root = commands.enabled_projects("discord").get(label)
        if not root:
            return True
        try:
            from ..store import Project
            with Project(root, sync=False) as p:
                return bool(commands.registry.section(p, "notifications")["discord"].get("thread_per_task", True))
        except Exception:      # noqa: BLE001
            return True

    async def deliver(self, m: Message) -> Optional[str]:
        c = credentials()
        chan = self._label_channel(m.project) or m.chat or c.get("home_channel_id")
        if not chan:
            raise ResearchError("Discord has no home channel", hint="Run /sethome in the channel that should receive alerts.")
        chan, first = str(chan), render(m)
        target = chan
        if m.thread_key and await self._call(self._thread_per_task, m.project):
            try:
                target = await self._thread(m.project, m.thread_key, chan, m.title or "")
            except ResearchError:
                target = chan                                     # no permission for threads: post in the channel
        ref = None
        for i, part in enumerate(first):
            mid = await self._send(target, part)
            ref = ref or f"{target}:{mid}"
        if target != chan and (m.color == "error" or m.buttons):    # a one-line pointer in the project channel
            try:
                await self._send(chan, Parts(content=clean(f"{m.thread_key}: {_safe(m.text.splitlines()[0] if m.text else '', 150)} -> <#{target}>")[:CONTENT_MAX]))
            except ResearchError:
                pass
        return ref

    async def _thread(self, label: str, task: str, channel: str, title: str) -> str:
        have = self._cfg_threads(label, task)
        if have:
            return str(have)
        tid = await self.t.create_thread(channel, _safe(f"{task} · {title}" if title else task))
        with txn("discord"):
            d = credentials()
            d.setdefault("threads", {}).setdefault(label, {})[task] = tid
            write_json(path(), d)
        return tid

    async def edit(self, ref: str, m: Message) -> None:
        chan, _, mid = ref.partition(":")
        if not mid:
            return
        if m.text or m.title:
            await self.t.edit(chan, mid, render(m)[0])
        else:
            await self.t.edit(chan, mid, Parts(buttons=[]))          # expired: remove the components only

    async def delete(self, ref: str) -> None:
        chan, _, mid = ref.partition(":")
        if mid:
            await self.t.delete(chan, mid)

    # --- receiving
    def _where(self, ix: Ix) -> Tuple[Optional[str], Optional[str]]:
        """(project label, task) implied by the channel: a project channel, or a task thread inside one."""
        c = credentials()
        for label, per in (c.get("threads") or {}).items():
            for task, tid in per.items():
                if str(tid) == ix.channel_id:
                    return label, task
        for label, cid in (c.get("project_channels") or {}).items():
            if str(cid) in (ix.channel_id, ix.parent_id):
                return label, None
        return None, None

    def _allowed(self, ix: Ix) -> bool:
        if ix.dm:
            return True
        c = credentials()
        if not c.get("guild_id") or str(c["guild_id"]) != ix.guild_id:
            return False
        allow = [str(x) for x in c.get("allowed_channels") or []]
        return not allow or ix.channel_id in allow or ix.parent_id in allow or bool(self._where(ix)[0])

    def _inc(self, ix: Ix, text: str = "", token: Optional[str] = None, ref: Optional[str] = None) -> Incoming:
        return Incoming("discord", ix.user_id, ix.user_name, ix.channel_id, text, token=token, group=not ix.dm, is_bot=ix.is_bot,
                        reply_to=ix.reply_to, ref=ref or ix.message_ref)

    async def dispatch(self, ix: Ix) -> None:
        if ix.is_bot:
            return
        if not self._allowed(ix):
            if ix.kind != "message" and ix.kind != "autocomplete":
                await self.t.reject(ix, "This bot is not available in this server or channel.")
            return
        try:
            await {"command": self._command, "autocomplete": self._autocomplete, "button": self._component, "select": self._component,
                   "modal": self._modal, "message": self._message}[ix.kind](ix)
        except (ResearchError, OSError):
            pass                                       # a failing send must not stop the receive loop; the router already answered

    def _text(self, ix: Ix) -> str:
        """The slash command as the router's text. A project channel or task thread implies the project (as its first word)."""
        label, _ = self._where(ix)
        o = ix.options
        order = ORDER.get(ix.name) or [x[0] for x in SLASH.get(ix.name, [])]
        pre = ((o.get("project") if ix.name in ("status", "blocked") else None) or label) if ix.name in commands.COMMANDS else None
        args = [str(o[k]) for k in order if k != "project" and o.get(k) not in (None, "")]
        return " ".join(["/" + ix.name] + ([pre] if pre else []) + args)

    async def _command(self, ix: Ix) -> None:
        await self.t.defer(ix, True)                    # 3 s deadline: acknowledge first, answer in a follow-up
        reply = await self._call(self.inbox.command, self._inc(ix, self._text(ix)))
        await self._emit(ix, reply, "Not authorized here. Send me a direct message to get a pairing code.")

    async def _emit(self, ix: Ix, reply: Reply, empty: str = "Done.") -> None:
        sent = False
        for m in reply.messages:
            for part in render(m, ix.attachment_limit):
                part.ephemeral = True if ix.kind == "command" else m.ephemeral
                await self.t.followup(ix, part)
                sent = True
        if not sent:
            await self.t.followup(ix, Parts(content=empty, ephemeral=True))

    async def _autocomplete(self, ix: Ix) -> None:
        if pairing.role_of("discord", ix.user_id) is None:
            return await self.t.autocomplete(ix, [])
        kind = next((o[4] for o in SLASH.get(ix.name, []) if o[0] == ix.focused), None)
        names = await self._call(self._names, kind, ix.options.get("project") or self._where(ix)[0])
        want = ix.text.lower()
        await self.t.autocomplete(ix, [(n, n) for n in names if want in n.lower()][:25])

    @staticmethod
    def _names(kind: Any, label: Optional[str]) -> List[str]:
        try:
            projs = commands.enabled_projects("discord")
            if kind == "project":
                return list(projs)
            root = projs.get(label or "") or (next(iter(projs.values())) if len(projs) == 1 else None)
            if not root or kind not in ("task", "plan"):
                return []
            from .. import supervisor as SV
            from ..store import Project
            with Project(root, sync=False) as p:
                runs = SV.all_runs(p)
            return sorted({r["plan_key"] for r in runs}) if kind == "plan" else sorted({t for r in runs for t in r.get("tasks", {})})
        except Exception:      # noqa: BLE001 - autocomplete is a convenience
            return []

    async def _component(self, ix: Ix) -> None:
        role = pairing.role_of("discord", ix.user_id)
        cid = ix.custom_id
        if cid.startswith("i:"):                         # Instruct: a modal with one text input -> /say
            if not pairing.at_least(role, "operator"):
                return await self.t.reject(ix, "Instructing needs the operator role.")
            return await self.t.send_modal(ix, "m:" + cid[2:], "Instruct the supervisor", "Instruction", True, 1500)
        token = ix.values[0] if ix.kind == "select" and ix.values else cid[2:] if cid.startswith("a:") else ""
        rec = actions.peek(token)
        if rec and rec.get("kind") == "ask_code" and not rec.get("used"):
            if not pairing.at_least(role, rec["min_role"]):
                return await self.t.reject(ix, f"This needs the {rec['min_role']} role.")
            # the modal must be the first response (no defer): the token is only looked at here and consumed on submit
            return await self.t.send_modal(ix, "c:" + token, "Approve with your authenticator", "6-digit authenticator code (not your PIN)", False, 6)
        await self.t.defer(ix, False)
        reply = await self._call(self.inbox.click, self._inc(ix, token=token, ref=ix.message_ref))
        if reply.edit:
            e = render(reply.edit)[0]
            await self.t.edit_origin(ix, Parts(content=None, embed=e.embed, buttons=[]))
        if reply.toast:
            await self.t.followup(ix, Parts(content=reply.toast[:CONTENT_MAX], ephemeral=True))
        for m in reply.messages:
            for part in render(m, ix.attachment_limit):
                await self.t.followup(ix, part)

    async def _modal(self, ix: Ix) -> None:
        await self.t.defer(ix, True)
        kind, _, arg = ix.custom_id.partition(":")
        if kind == "c":
            return await self._code(ix, arg)
        task, _, label = arg.partition("|")
        text = ix.text.strip()
        if not text:
            return await self.t.followup(ix, Parts(content="Nothing to send.", ephemeral=True))
        reply = await self._call(self.inbox.command, self._inc(ix, f"/say {label + ' ' if label else ''}{task} {text}"))
        await self._emit(ix, reply)

    async def _code(self, ix: Ix, token: str) -> None:
        """Authenticator code from the modal. Never echoed, logged or sent anywhere but the op (through the router's ask_code flow)."""
        code = ix.text.strip()
        if not re.fullmatch(r"\d{6}", code):
            return await self.t.followup(ix, Parts(content="That is not a 6-digit authenticator code. Open the button again to retry.", ephemeral=True))
        r1 = await self._call(self.inbox.click, self._inc(ix, token=token, ref=ix.message_ref))
        if r1.toast != "Waiting for your code":            # wrong role, used, expired
            return await self.t.followup(ix, Parts(content=(r1.toast or "Not done.")[:CONTENT_MAX], ephemeral=True))
        reply = await self._call(self.inbox.command, self._inc(ix, code, ref=None))
        bad = bool(reply.messages) and reply.messages[0].color == "error"
        if bad:
            actions.release(token)                         # a wrong or replayed code: the Approve button works again
        elif reply.edit and ix.message_ref:
            e = render(reply.edit)[0]
            try:
                await self.t.edit(*ix.message_ref.split(":", 1), Parts(content=None, embed=e.embed, buttons=[]))
            except ResearchError:
                pass
        for m in reply.messages:
            await self.t.followup(ix, Parts(content=clean(m.text)[:CONTENT_MAX], ephemeral=True))

    async def _message(self, ix: Ix) -> None:
        text = ix.text.strip()
        if not text:
            return
        from .commands import _refs
        label, task = self._where(ix)
        if label and not (ix.reply_to and (_refs().get(ix.reply_to) or {}).get("decision")):
            text = f"/say {label} {task + ' ' if task else ''}{text}"       # a task thread / project channel: the mention is an instruction
        reply = await self._call(self.inbox.command, self._inc(ix, text))
        for m in reply.messages:
            for part in render(m, ix.attachment_limit):
                await self._send(ix.channel_id, part)


# ----------------------------------------------------------------------------------------------- discord.py (the only code that imports it)
class RealTransport:
    def __init__(self, token: str, application_id: str, guild_id: str):
        import discord
        self.d, self.token, self.app, self.guild = discord, token, application_id, guild_id
        self.client = discord.Client(intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none())   # no message_content

    # -- helpers
    def _wrap(self, ex: Exception) -> Exception:
        d = self.d
        if isinstance(ex, d.HTTPException):
            if ex.status == 429:
                return RateLimited(float(getattr(ex, "retry_after", 1.0) or 1.0))
            return ResearchError(f"Discord refused the request (HTTP {ex.status}, code {getattr(ex, 'code', 0)})")
        return ex

    def _view(self, parts: Parts):
        if not parts.buttons and not parts.options:
            return None
        from . import actions as _a
        d = self.d
        v = d.ui.View(timeout=float(_a.TTL))
        style = {"primary": d.ButtonStyle.primary, "danger": d.ButtonStyle.danger}
        for label, cid, st in parts.buttons[:25]:
            v.add_item(d.ui.Button(label=label, custom_id=cid, style=style.get(st, d.ButtonStyle.secondary)))
        if parts.options:
            v.add_item(d.ui.Select(custom_id="s:menu", placeholder="Choose an option",
                                   options=[d.SelectOption(label=l[:100], value=val) for l, val in parts.options[:25]]))
        return v

    def _kw(self, parts: Parts, edit: bool = False) -> Dict[str, Any]:
        d, kw = self.d, {}
        if parts.content is not None:
            kw["content"] = parts.content
        if parts.embed is not None:
            kw["embed" if not edit else "embeds"] = d.Embed.from_dict(parts.embed) if not edit else [d.Embed.from_dict(parts.embed)]
        if edit:
            kw["view"] = self._view(parts)                   # None clears the components
        elif parts.buttons or parts.options:
            kw["view"] = self._view(parts)
        if parts.files and not edit:
            kw["files"] = [d.File(f, filename=clean(os.path.basename(f)) or "file") for f in parts.files]
        return kw

    async def _do(self, coro: Any) -> Any:
        try:
            return await coro
        except Exception as ex:      # noqa: BLE001
            w = self._wrap(ex)
            if w is ex:
                raise
            raise w from None

    # -- DiscordTransport
    async def start(self, dispatch, slash) -> None:
        d, client = self.d, self.client
        registered = []

        @client.event
        async def on_ready():
            if registered:
                return
            registered.append(1)
            app = client.application_id or int(self.app or 0)
            try:
                if self.guild:
                    await client.http.bulk_upsert_guild_commands(app, int(self.guild), slash)   # guild-scoped: instant
            except Exception:      # noqa: BLE001 - commands stay as last registered; the bot still answers DMs and mentions
                pass

        @client.event
        async def on_interaction(i):
            ix = self._ix(i)
            if ix is not None:
                await dispatch(ix)

        @client.event
        async def on_message(m):
            if m.author.bot or client.user is None:
                return
            if m.guild is not None and not client.user.mentioned_in(m):
                return
            text = re.sub(rf"<@!?{client.user.id}>", "", m.content or "").strip()
            ch = m.channel
            await dispatch(Ix("message", str(m.author.id), m.author.display_name, str(ch.id), str(getattr(ch, "parent_id", None) or "") or None,
                              str(m.guild.id) if m.guild else None, text=text,
                              reply_to=f"{ch.id}:{m.reference.message_id}" if m.reference and m.reference.message_id else None, impl=m))
        try:
            await client.start(self.token)
        except d.LoginFailure:
            raise ResearchError("Discord rejected the bot token", hint="Run `research chat setup discord` again.") from None

    def _ix(self, i) -> Optional[Ix]:
        d, data = self.d, i.data or {}
        T = d.InteractionType
        base = dict(user_id=str(i.user.id), user_name=i.user.display_name, is_bot=bool(i.user.bot), channel_id=str(i.channel_id or ""),
                    parent_id=str(getattr(i.channel, "parent_id", None) or "") or None, guild_id=str(i.guild_id) if i.guild_id else None, impl=i,
                    attachment_limit=int(getattr(i, "attachment_size_limit", 0) or MAX_FILE),
                    message_ref=f"{i.channel_id}:{i.message.id}" if getattr(i, "message", None) else None)
        if i.type is T.application_command:
            return Ix("command", name=data.get("name", ""), options={o["name"]: o.get("value") for o in data.get("options", [])}, **base)
        if i.type is T.autocomplete:
            opts = {o["name"]: o.get("value") for o in data.get("options", [])}
            foc = next((o for o in data.get("options", []) if o.get("focused")), {})
            return Ix("autocomplete", name=data.get("name", ""), options=opts, focused=foc.get("name", ""), text=str(foc.get("value") or ""), **base)
        if i.type is T.component:
            return Ix("select" if data.get("component_type") == 3 else "button", custom_id=data.get("custom_id", ""), values=list(data.get("values") or []), **base)
        if i.type is T.modal_submit:
            val = next((c.get("value", "") for row in data.get("components", []) for c in row.get("components", [])), "")
            return Ix("modal", custom_id=data.get("custom_id", ""), text=val, **base)
        return None

    async def close(self) -> None:
        await self.client.close()

    def _chan(self, channel_id: str):
        return self.client.get_partial_messageable(int(channel_id))

    async def send(self, channel_id, parts) -> str:
        m = await self._do(self._chan(channel_id).send(**self._kw(parts)))
        return str(m.id)

    async def edit(self, channel_id, message_id, parts) -> None:
        await self._do(self._chan(channel_id).get_partial_message(int(message_id)).edit(**self._kw(parts, edit=True)))

    async def delete(self, channel_id, message_id) -> None:
        await self._do(self._chan(channel_id).get_partial_message(int(message_id)).delete())

    async def create_thread(self, channel_id, name) -> str:
        ch = await self._do(self.client.fetch_channel(int(channel_id)))
        t = await self._do(ch.create_thread(name=name[:100], type=self.d.ChannelType.public_thread, auto_archive_duration=THREAD_ARCHIVE))
        return str(t.id)

    async def defer(self, ix, ephemeral) -> None:
        await self._do(ix.impl.response.defer(ephemeral=ephemeral, thinking=ix.kind not in ("button", "select")))

    async def followup(self, ix, parts) -> None:
        kw = self._kw(parts)
        if "embed" not in kw and "content" not in kw:
            kw["content"] = "Done."
        await self._do(ix.impl.followup.send(ephemeral=parts.ephemeral, **kw))

    async def edit_origin(self, ix, parts) -> None:
        await self._do(ix.impl.edit_original_response(**self._kw(parts, edit=True)))

    async def reject(self, ix, text) -> None:
        await self._do(ix.impl.response.send_message(text, ephemeral=True))

    async def send_modal(self, ix, custom_id, title, label, paragraph, max_length) -> None:
        d = self.d
        modal = d.ui.Modal(title=title[:45], custom_id=custom_id[:100], timeout=None)
        modal.add_item(d.ui.TextInput(label=label[:45], custom_id="v", style=d.TextStyle.paragraph if paragraph else d.TextStyle.short,
                                      required=True, min_length=max_length if not paragraph else None, max_length=max_length))
        await self._do(ix.impl.response.send_modal(modal))

    async def autocomplete(self, ix, choices) -> None:
        await self._do(ix.impl.response.autocomplete([self.d.app_commands.Choice(name=n[:100], value=v[:100]) for n, v in choices]))
