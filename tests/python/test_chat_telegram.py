"""WP14a: the Telegram adapter against a local fake Bot API, and backward compatibility with a pre-gateway Telegram setup."""
import asyncio
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fakes"))
from fake_telegram import FakeTelegram, TOKEN  # noqa: E402

from research import machine, notify
from research.chat import actions, commands, gateway, gdir, pairing, read_json, telegram
from research.chat import Button, Message
from research.chat.telegram import TelegramChannel
from research.store import Project


@pytest.fixture
def fake(monkeypatch):
    f = FakeTelegram()
    monkeypatch.setenv("RESEARCH_TELEGRAM_API", f.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f
    f.stop()


@pytest.fixture
def old_setup(fake, proj):
    """A Telegram setup written by the pre-gateway version: telegram.json {token, chat_id, offset}, notifications.telegram.enabled, nothing else."""
    path = telegram.path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"token": TOKEN, "chat_id": 777, "offset": 0}, f)
    os.chmod(path, 0o644)
    proj.save_config({"notifications.telegram.enabled": True})
    proj.config = proj.load_config()
    machine.remember(proj.root)
    return proj


def poll(fake):
    ch = TelegramChannel(commands.command_list())
    return ch, ch.poll_once(commands.Inbox("telegram"), 0)


def test_an_old_telegram_config_keeps_working_without_edits(old_setup, fake):
    assert notify.status(old_setup)["ready"] and notify.credentials()["chat_id"] == 777
    assert stat.S_IMODE(os.stat(telegram.path()).st_mode) == 0o600, "a loose token file is tightened on first read"
    assert pairing.migrate_telegram(777)["role"] == "operator", "the private chat id becomes an operator, once"
    assert pairing.migrate_telegram(777) is None
    fake.say("/status")
    fake.say("/help@mybot")                                     # @botname suffix stripped
    fake.say("/status", chat=999)                               # a stranger
    ch, n = poll(fake)
    assert n == 3
    texts = {m["chat"]: m["text"] for m in fake.messages}
    assert "No plan has been run" in fake.messages[0]["text"] and "/status" in fake.messages[1]["text"] and all(m["chat"] == "777" for m in fake.messages[:2])
    assert "pairing code" in fake.messages[2]["text"] and fake.messages[2]["chat"] == "999", "the stranger gets a pairing code, never the status"
    fake.say("loosen the check")                                # plain text is /say (no blocked task here: asks which)
    poll(fake)
    assert "Which task?" in fake.messages[-1]["text"]
    assert notify.poll(old_setup) == 0, "the old `poll` entry point still works and finds nothing new"
    assert notify.send("hello") and fake.sent[-1] == "hello" and TOKEN not in json.dumps(fake.messages)


def test_offset_is_saved_before_handling_so_a_failed_send_never_replays(old_setup, fake):
    pairing.migrate_telegram(777)
    fake.say("/help")
    fake.fail_send = 1
    ch, n = poll(fake)
    assert n == 1 and read_json(gdir("cursors.json"))["telegram_offset"] == 2
    assert fake.sent == [], "the reply failed"
    ch, n = poll(fake)
    assert n == 0 and fake.sent == [], "the update is not replayed"
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(gdir("cursors.json")).st_mode) == 0o600


def test_an_offset_from_the_old_version_is_not_replayed(old_setup, fake):
    pairing.migrate_telegram(777)
    json.dump({"token": TOKEN, "chat_id": 777, "offset": 3}, open(telegram.path(), "w"))
    fake.say("/help")
    fake.say("/help")
    fake.say("/status")
    ch, n = poll(fake)
    assert n == 1 and "No plan" in fake.sent[0]


def test_group_chats_need_a_paired_user_id_not_just_the_chat_id(old_setup, fake):
    json.dump({"token": TOKEN, "chat_id": -1001}, open(telegram.path(), "w"))
    assert pairing.migrate_telegram(-1001) is None, "a group id never becomes an operator"
    fake.say("/status", chat=-1001, user=55, group=True)         # a member of the configured group, not paired
    fake.say("/pair", chat=-1001, user=56, group=True)
    ch, n = poll(fake)
    assert len(fake.messages) == 1 and "pairing code" in fake.messages[0]["text"] and "No plan" not in fake.messages[0]["text"]
    pairing.add_user("telegram", 55, "viewer", "member")
    fake.say("/status", chat=-1001, user=55, group=True)
    fake.say("/pause", chat=-1001, user=55, group=True)
    poll(fake)
    assert "No plan" in fake.messages[-2]["text"] and "operator role" in fake.messages[-1]["text"]


def test_buttons_callbacks_edits_documents_and_commands(old_setup, fake, tmp_path):
    pairing.migrate_telegram(777)
    root = old_setup.root
    ch = TelegramChannel(commands.command_list())
    ch.register_commands()
    names = {c["command"] for c in fake.commands}
    assert {"status", "approve_start", "say", "help"} <= names and all("-" not in n for n in names)
    tok = actions.issue(root, "op", "chat_cmd_status", {}, "viewer")
    doc = tmp_path / "report.md"
    doc.write_text("# r\n")
    ref = asyncio.run(ch.deliver(Message("card", buttons=[Button("Status", tok), Button("Other", "zzz")], files=[str(doc)], chat="777")))
    assert ref == "777:101" and fake.documents[0]["name"] == "report.md"
    kb = fake.messages[-1]["markup"]["inline_keyboard"]
    assert [b["callback_data"] for row in kb for b in row] == ["a:" + tok, "a:zzz"] and all(len(b["callback_data"].encode()) <= 64 for row in kb for b in row)
    fake.click("a:" + tok, message_id=101)
    fake.click("a:" + tok, message_id=101)
    poll(fake)
    assert len(fake.answered) == 2 and fake.answered[0]["text"] == "Done" and "already done" in fake.answered[1]["text"]
    assert fake.edits[0]["method"] == "editMessageText" and "done" in fake.edits[0]["text"] or "status" in fake.edits[0]["text"]
    assert json.loads(fake.edits[0]["reply_markup"]) == {"inline_keyboard": []}, "the clicked card loses its buttons"
    asyncio.run(ch.edit("777:101", Message(text="")))
    assert fake.edits[-1]["method"] == "editMessageReplyMarkup"
    fake.click("a:" + tok, user=4242)
    poll(fake)
    assert "Not authorized" in fake.answered[-1]["text"], "a click is checked against the allow-list too"


def test_long_logs_travel_as_a_file_and_secrets_are_redacted(old_setup, fake):
    pairing.migrate_telegram(777)
    ch = TelegramChannel()
    secret = "sk-ant-api03-" + "A" * 40
    asyncio.run(ch.deliver(Message(f"key is {secret} and token {TOKEN}", chat="777")))
    assert secret not in fake.sent[-1] and TOKEN not in fake.sent[-1]
    big = "x" * 9000
    asyncio.run(ch.deliver(Message(big, chat="777")))
    assert [len(t) for t in fake.sent[-3:]] == [3800, 3800, 1400]


def test_nothing_is_contacted_when_telegram_is_not_configured(fake, proj):
    ch, n = poll(fake)
    assert n == 0 and fake.calls == []
    assert not TelegramChannel().ready() and notify.send("x") is False
