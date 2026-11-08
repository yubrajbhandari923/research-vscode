"""FakeDiscord: an in-memory `research.chat.discord_bot.DiscordTransport`. No network, no discord.py. Records every call in `log`
(in order, so tests can assert 'deferred before the core ran') and builds `Ix` events the way the real transport would."""
import asyncio

from research.chat.discord_bot import Ix, Parts


class Resp:
    """What the bot did to one interaction."""
    def __init__(self):
        self.deferred = None          # ephemeral flag once deferred
        self.followups, self.origin_edits, self.rejected, self.modal, self.choices = [], [], [], None, None


class FakeDiscord:
    def __init__(self):
        self.log, self.sent, self.edits, self.deleted, self.threads = [], {}, [], [], []
        self.fail = []                # exceptions raised by the next send() calls
        self.dispatch, self.slash, self._mid = None, None, 1000
        self.closed = None

    # --- DiscordTransport
    async def start(self, dispatch, slash):
        self.dispatch, self.slash = dispatch, slash
        self.closed = asyncio.Event()
        self.log.append("start")
        await self.closed.wait()

    async def close(self):
        if self.closed:
            self.closed.set()

    async def send(self, channel_id, parts: Parts):
        if self.fail:
            raise self.fail.pop(0)
        self._mid += 1
        self.sent.setdefault(str(channel_id), []).append((str(self._mid), parts))
        self.log.append(("send", str(channel_id)))
        return str(self._mid)

    async def edit(self, channel_id, message_id, parts):
        self.edits.append((str(channel_id), str(message_id), parts))

    async def delete(self, channel_id, message_id):
        self.deleted.append((str(channel_id), str(message_id)))

    async def create_thread(self, channel_id, name):
        self.threads.append((str(channel_id), name))
        return str(5000 + len(self.threads))

    async def defer(self, ix, ephemeral):
        ix.impl.deferred = ephemeral
        self.log.append(("defer", ix.name or ix.custom_id))

    async def followup(self, ix, parts):
        ix.impl.followups.append(parts)
        self.log.append(("followup", ix.name or ix.custom_id))

    async def edit_origin(self, ix, parts):
        ix.impl.origin_edits.append(parts)

    async def reject(self, ix, text):
        ix.impl.rejected.append(text)

    async def send_modal(self, ix, custom_id, title, label, paragraph, max_length):
        assert ix.impl.deferred is None, "a modal must be the first response, not a follow-up"
        ix.impl.modal = {"custom_id": custom_id, "title": title, "label": label, "paragraph": paragraph, "max_length": max_length}

    async def autocomplete(self, ix, choices):
        ix.impl.choices = choices

    # --- scripting: act as a user (all are coroutines returning the Ix, whose `.impl` is the Resp)
    async def do(self, ch, kind, user="1", user_name="u", channel="100", guild="G1", parent=None, **kw):
        ix = Ix(kind, str(user), user_name, str(channel), parent, guild, impl=Resp(), **kw)
        await ch.dispatch(ix)
        return ix

    async def slash_cmd(self, ch, cmd, user="1", where=None, **opts):
        return await self.do(ch, "command", user, name=cmd, options=opts, **(where or {}))

    def texts(self, channel):
        return [(p.embed or {}).get("description") or p.content for _, p in self.sent.get(str(channel), [])]
