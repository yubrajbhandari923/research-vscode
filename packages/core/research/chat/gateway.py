"""The chat gateway: ONE process per machine that owns every chat connection (python -m research.chat.gateway run).

It tails each enabled project's alert outbox and delivers it (at-least-once, ids remembered so a restart never re-sends), long-polls
each platform and hands messages to the one router, raises `supervisor_down`, sends the morning digest, removes expired buttons, and
exits when no project enables chat. Lock, spawn and heartbeat come from `daemonlib`; state is under `<config dir>/gateway/`.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

from .. import daemonlib, registry
from ..util import PolicyBlocked, ResearchError
from . import Channel, Message, gdir, read_json, txn, write_json
from . import alerts, commands, digest

IDLE_EXIT = 600
STORM_N, STORM_WINDOW = 5, 60
MAX_REFS, MAX_SENT = 500, 2000


def _hb() -> str:
    return gdir("daemon.json")


def status() -> Dict[str, Any]:
    d = daemonlib.read_heartbeat(_hb())
    return {"running": bool(d.get("alive")), "pid": d.get("pid"), "version": d.get("version"), "stale": bool(d.get("stale")),
            "platforms": d.get("platforms") or [], "projects": d.get("projects") or [], "log": gdir("gateway.log")}


def start(wait: float = 6.0) -> Dict[str, Any]:
    argv = [sys.executable, "-m", "research.chat.gateway", "run"]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), os.environ.get("PYTHONPATH", "")])}
    proc = daemonlib.spawn_detached(argv, gdir("gateway.log"), env)
    t0 = time.time()
    while time.time() - t0 < wait:
        d = daemonlib.read_heartbeat(_hb())
        if d.get("alive") and d.get("pid") == proc.pid:
            break
        time.sleep(0.1)
    return status()


def stop(wait: float = 10.0) -> Dict[str, Any]:
    d = daemonlib.read_heartbeat(_hb())
    if d.get("alive"):
        try:
            os.kill(int(d["pid"]), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        t0 = time.time()
        while time.time() - t0 < wait and daemonlib.pid_alive(d["pid"]):
            time.sleep(0.1)
    return status()


def ensure_running(p=None) -> Dict[str, Any]:
    """Cheap and safe: start the gateway when it is not running, replace one on older code. Never raises. A second spawn that loses the
    lock exits at once, so a race costs nothing."""
    try:
        d = daemonlib.read_heartbeat(_hb())
        if d.get("alive") and not d.get("stale"):
            return {"action": "none"}
        if d.get("alive"):
            stop()
        return {"action": "start", **start()}
    except Exception as ex:      # noqa: BLE001
        return {"action": "none", "error": str(ex)[:200]}


def control(action: str) -> Dict[str, Any]:
    if action == "status":
        return status()
    if action == "start":
        return status() if status()["running"] else start()
    if action == "stop":
        return stop()
    if action == "restart":
        stop()
        return start()
    raise ResearchError("gateway action: start | stop | restart | status")


def make_channels() -> Dict[str, Channel]:
    from .telegram import TelegramChannel
    t = TelegramChannel(commands.command_list())
    out: Dict[str, Channel] = {"telegram": t} if t.ready() and commands.enabled_projects("telegram") else {}
    from . import discord_bot
    if discord_bot.credentials().get("token") and discord_bot.available() and commands.enabled_projects("discord"):
        try:
            out["discord"] = discord_bot.DiscordChannel()
        except Exception:    # noqa: BLE001 - Discord failing to start must not take Telegram down
            pass
    return out


class Gateway:
    def __init__(self, channels: Optional[Dict[str, Channel]] = None, now: Callable[[], float] = time.time, discover: bool = True, idle_exit: float = IDLE_EXIT):
        self.channels: Dict[str, Channel] = channels if channels is not None else {}
        self.discover, self.now, self.idle_exit = discover, now, idle_exit
        self.inboxes: Dict[str, commands.Inbox] = {}
        self.tasks: Dict[str, Any] = {}
        self.window: Dict[str, Deque[float]] = {}
        self.fails: Dict[str, int] = {}
        self._proj_cache: Dict[str, Any] = {}

    # --- projects (cached 15 s: opening every project's config each tick is wasteful)
    def projects(self, platform: str) -> Dict[str, str]:
        c = self._proj_cache.get(platform)
        if not c or self.now() - c[0] > 15:
            c = (self.now(), commands.enabled_projects(platform))
            self._proj_cache[platform] = c
        return c[1]

    def inbox(self, platform: str) -> commands.Inbox:
        return self.inboxes.setdefault(platform, commands.Inbox(platform))

    # --- delivery
    def _home(self, platform: str, m: Message) -> Message:
        if platform == "discord":
            from . import discord_bot
            h = discord_bot.home_channel()
        else:
            h = (read_json(gdir("chats.json")).get("home") or {}).get(platform)
        if h and not m.chat:
            m.chat = h
        return m

    async def send(self, platform: str, m: Message, root: str = "", label: str = "", alert: Optional[Dict[str, Any]] = None) -> Optional[str]:
        ch = self.channels[platform]
        ref = await ch.deliver(self._home(platform, m))
        if ref:
            exp = float(registry.defaults()["notifications"]["alerts"]["approval_expiry_hours"]) * 3600
            if alert and alert.get("kind") in ("approval_needed", "decision") and root:
                from ..store import Project
                try:
                    with Project(root, sync=False) as p:
                        exp = float(alerts.cfg(p)["approval_expiry_hours"]) * 3600
                except Exception:    # noqa: BLE001
                    pass
            with txn("refs"):
                d = read_json(gdir("refs.json"))
                d[ref] = {"project": root, "label": label, "alert_id": (alert or {}).get("id"), "decision": (alert or {}).get("decision"),
                          "text": m.plain()[:1500], "ts": self.now(), "platform": platform, "buttons": bool(m.buttons),
                          "expires": self.now() + exp if m.buttons else None}
                if len(d) > MAX_REFS:
                    d = dict(sorted(d.items(), key=lambda kv: kv[1].get("ts", 0))[-MAX_REFS:])
                write_json(gdir("refs.json"), d)
        return ref

    def render(self, root: str, label: str, multi: bool, r: Dict[str, Any]) -> Message:
        color = {"error": "error", "warn": "warn", "ok": "ok"}.get(r.get("severity"), "info")
        from ..taskprompt import redact
        return Message((f"[{label}] " if multi else "") + (redact(r.get("text")) or ""), color=color, files=list(r.get("files") or []),
                       buttons=commands.buttons_for(root, r.get("actions") or [], r.get("id")), thread_key=r.get("task"), project=label)

    async def pump_once(self) -> int:
        """Deliver new outbox records of every enabled project on every platform. Returns the number of messages sent."""
        sent = 0
        for platform in list(self.channels):
            projs = self.projects(platform)
            for label, root in projs.items():
                sent += await self._pump_project(platform, label, root, len(projs) > 1)
        return sent

    async def _pump_project(self, platform: str, label: str, root: str, multi: bool) -> int:
        from ..store import Project
        path = os.path.join(root, ".research", "supervisor", "outbox.jsonl")
        try:
            with Project(root, sync=False) as p:
                path = alerts.outbox_path(p)
        except Exception:    # noqa: BLE001
            pass
        with txn("cursors"):
            cur = read_json(gdir("cursors.json"))
        c = (cur.get("outbox") or {}).get(f"{platform}|{root}") or {}
        recs, end, ino = alerts.read_from(path, int(c.get("offset") or 0), c.get("inode"))
        seen = set((cur.get("sent") or {}).get(platform) or [])
        fresh = [r for r in recs if r["id"] not in seen]
        n, done_to, ok_ids = 0, c.get("offset") or 0, []
        w = self.window.setdefault(f"{platform}|{root}", deque())
        while w and self.now() - w[0] > STORM_WINDOW:
            w.popleft()
        try:
            if len(w) + len(fresh) > STORM_N and len(fresh) > 1:      # storm control: one summary for the burst
                kinds = ", ".join(sorted({r["kind"] for r in fresh}))
                await self.send(platform, Message(f"{'[' + label + '] ' if multi else ''}{len(fresh)} alerts in the last minute ({kinds}). Latest: {fresh[-1]['text'][:300]}", color="warn", project=label), root, label)
                n += 1
                ok_ids += [r["id"] for r in fresh]
                done_to = fresh[-1]["_end"]
                w.append(self.now())
            else:
                for r in fresh:
                    try:
                        await self.send(platform, self.render(root, label, multi, r), root, label, r)
                    except (ResearchError, OSError):
                        self.fails[r["id"]] = self.fails.get(r["id"], 0) + 1
                        if self.fails[r["id"]] < 5:
                            raise            # keep the cursor before this record; it is retried next tick
                    n += 1
                    w.append(self.now())
                    ok_ids.append(r["id"])
                    done_to = r["_end"]
            done_to = max(done_to, recs[-1]["_end"]) if recs and not fresh else done_to
        except (ResearchError, OSError):
            pass
        with txn("cursors"):
            cur = read_json(gdir("cursors.json"))
            cur.setdefault("outbox", {})[f"{platform}|{root}"] = {"offset": done_to, "inode": ino}
            lst = ((cur.setdefault("sent", {})).get(platform) or []) + ok_ids
            cur["sent"][platform] = lst[-MAX_SENT:]
            write_json(gdir("cursors.json"), cur)
        return n

    # --- watchdog, digest, button expiry
    async def watch_once(self) -> None:
        from ..store import Project
        from .. import supervisor as SV
        roots: Dict[str, str] = {}
        for platform in self.channels:
            roots.update({r: l for l, r in self.projects(platform).items()})
        for root in roots:
            try:
                with Project(root, sync=False) as p:
                    running = any(r.get("state") == "running" for r in SV.all_runs(p))
                    d = SV.daemon_status(p)
                    poll = float(registry.section(p, "supervisor")["poll_seconds"])
                    if running and "heartbeat_age_seconds" in d and (not d["running"] or d["heartbeat_age_seconds"] > 3 * poll):
                        alerts.raise_(p, "supervisor_down", "supervisor_down", "error",
                                      "The supervisor is not running while a plan is running. Start it with `research supervisor start`; nothing is lost.")
                    elif d.get("running"):
                        alerts.clear(p, "supervisor_down", "The supervisor is running again.")
                    await self._digest(p, root, roots[root])
            except Exception:    # noqa: BLE001 - a bad project must not stop the watchdog for the others
                continue
        await self._expire()

    async def _digest(self, p, root: str, label: str) -> None:
        c = registry.section(p, "notifications")["digest"]
        at = c.get("at")
        if not at:
            return
        t = datetime.fromtimestamp(self.now())
        try:
            hh, mm = [int(x) for x in str(at).split(":")]
        except ValueError:
            return
        today = t.strftime("%Y-%m-%d")
        if (t.hour, t.minute) < (hh, mm):
            return
        with txn("chats"):
            d = read_json(gdir("chats.json"))
            last = (d.get("digest") or {}).get(root) or {}
            if last.get("date") == today:
                return
            d.setdefault("digest", {})[root] = {"date": today, "ts": self.now()}
            write_json(gdir("chats.json"), d)
        text = digest.build(p, float(last.get("ts") or self.now() - 86400))
        if text is None and c.get("silent_if_unchanged", True):
            return
        for platform in self.channels:
            if root in self.projects(platform).values():
                await self.send(platform, Message(text or "Nothing changed since the last digest.", title=f"Digest · {label}", project=label), root, label)

    async def _expire(self) -> None:
        with txn("refs"):
            d = read_json(gdir("refs.json"))
        due = [(k, v) for k, v in d.items() if v.get("buttons") and v.get("expires") and v["expires"] < self.now()]
        for ref, v in due:
            try:
                if v.get("platform") in self.channels:
                    await self.channels[v["platform"]].edit(ref, Message(text=""))      # remove the buttons only; the proposal stays pending
            except (ResearchError, OSError):
                pass
            v["buttons"] = False
        if due:
            with txn("refs"):
                cur = read_json(gdir("refs.json"))
                cur.update({k: v for k, v in due})
                write_json(gdir("refs.json"), cur)

    # --- channels
    def sync_channels(self) -> None:
        """Start a platform when it becomes ready, stop it when its credentials go away. Telegram migration runs on first start."""
        if not self.discover:
            return
        want = make_channels()
        for name in list(self.channels):
            if name not in want:
                t = self.tasks.pop(name, None)
                if t:
                    t.cancel()
                self.channels.pop(name)
        for name, ch in want.items():
            if name not in self.channels:
                self.channels[name] = ch
                if name == "telegram":
                    from . import pairing, telegram
                    pairing.migrate_telegram(telegram.credentials().get("chat_id"))
                self.tasks[name] = asyncio.ensure_future(self._guard(ch))

    async def _guard(self, ch: Channel) -> None:
        while True:
            try:
                await ch.run(self.inbox(ch.platform))
            except asyncio.CancelledError:
                raise
            except Exception:    # noqa: BLE001 - one platform failing must not stop the gateway; retry shortly
                await asyncio.sleep(5)

    async def _group_notice(self) -> None:
        """E.7: a group set as the Telegram home chat receives alerts only; say once how members pair."""
        if "telegram" not in self.channels or not self.discover:
            return
        from . import telegram
        cid = str(telegram.credentials().get("chat_id") or "")
        if not cid.startswith("-"):
            return
        with txn("chats"):
            d = read_json(gdir("chats.json"))
            if d.get("group_notice") == cid:
                return
            d["group_notice"] = cid
            write_json(gdir("chats.json"), d)
        try:
            await self.send("telegram", Message("This group receives alerts. To give commands, type /pair here and approve the code on the computer: research chat pair CODE."))
        except (ResearchError, OSError):
            pass

    async def run(self, tick: float = 2.0) -> int:
        try:
            with daemonlib.lock(gdir("lock"), blocking=False):
                return await self._loop(tick)
        except ResearchError:
            return 3            # another gateway owns this machine

    async def _loop(self, tick: float) -> int:
        from .. import __version__
        stop = asyncio.Event()
        try:
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(signal.SIGTERM, stop.set)
            loop.add_signal_handler(signal.SIGINT, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
        prev = daemonlib.read_heartbeat(_hb())
        notice = bool(prev.get("version") and prev.get("version") != __version__)
        idle_since, last_watch = self.now(), 0.0
        while not stop.is_set():
            self.sync_channels()
            if notice and self.channels:
                for platform in self.channels:
                    try:
                        await self.send(platform, Message(f"Chat gateway restarted on Research OS {__version__}."))
                    except (ResearchError, OSError):
                        pass
                notice = False
            await self._group_notice()
            await self.pump_once()
            if self.now() - last_watch > 30:
                await self.watch_once()
                last_watch = self.now()
            daemonlib.write_heartbeat(_hb(), platforms=sorted(self.channels), projects=sorted({r for pl in self.channels for r in self.projects(pl).values()}))
            if self.channels:
                idle_since = self.now()
            elif not self.projects("telegram") and not self.projects("discord") and self.now() - idle_since > self.idle_exit:
                break
            try:
                await asyncio.wait_for(stop.wait(), tick)
            except asyncio.TimeoutError:
                pass
        for t in self.tasks.values():
            t.cancel()
        try:
            os.unlink(_hb())
        except OSError:
            pass
        return 0


def poll_once(p=None) -> int:
    """One receive pass for Telegram on the current thread (the old `telegram poll`, for debugging). Returns updates handled."""
    from . import pairing, telegram
    ch = telegram.TelegramChannel(commands.command_list())
    if not ch.ready():
        return 0
    pairing.migrate_telegram(telegram.credentials().get("chat_id"))
    return ch.poll_once(commands.Inbox("telegram"), 0)


def main(argv: List[str]) -> int:
    if argv[:1] != ["run"]:
        print("usage: python -m research.chat.gateway run", file=sys.stderr)
        return 2
    return asyncio.run(Gateway().run())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
