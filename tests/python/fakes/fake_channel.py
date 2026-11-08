"""FakeChannel: an in-memory chat adapter implementing `research.chat.Channel`. Drives the router, buttons and alerts without a network."""
from research.chat import Channel, Incoming, Message
from research.chat.commands import Inbox


class FakeChannel(Channel):
    platform = "fake"

    def __init__(self, platform="fake"):
        self.platform = platform
        self.delivered, self.edits, self.deleted, self.ran = [], [], [], False
        self.inbox, self._n = Inbox(platform), 0
        self.fail = 0              # the next N deliveries raise

    def ready(self):
        return True

    async def run(self, inbox):
        self.ran = True

    async def deliver(self, m: Message):
        from research.util import ResearchError
        if self.fail:
            self.fail -= 1
            raise ResearchError("down")
        self._n += 1
        self.delivered.append(m)
        return f"c:{self._n}"

    async def edit(self, ref, m):
        self.edits.append((ref, m))

    async def delete(self, ref):
        self.deleted.append(ref)

    # --- scripting: act as a user
    def say(self, text, user="1", name="u", chat="c1", group=False, bot=False, reply_to=None, ref=None):
        return self.inbox.command(Incoming(self.platform, str(user), name, chat, text, group=group, is_bot=bot, reply_to=reply_to, ref=ref or "c:99"))

    def click(self, token, user="1", name="u", chat="c1", ref="c:1"):
        return self.inbox.click(Incoming(self.platform, str(user), name, chat, token=token, ref=ref))

    def texts(self):
        return [m.plain() for m in self.delivered]
