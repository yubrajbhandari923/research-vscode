"""WP14a: `research chat ...` and the hidden `research telegram ...` aliases through the real launcher; the ops behind the extension."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fakes"))
from fake_telegram import FakeTelegram, TOKEN  # noqa: E402

from research import api, localbin
from research.chat import pairing, telegram
from research.store import Project


@pytest.fixture
def env(repo, tmp_path):
    Project.init(str(repo), name="demo", goal="g").close()
    localbin.install(str(repo))
    fake = FakeTelegram()
    xdg = tmp_path / "xdg-cli"
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "RESEARCH_"))}
    e.update(XDG_CONFIG_HOME=str(xdg), RESEARCH_TELEGRAM_API=fake.url, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    bin_ = localbin.launcher(str(repo))

    def run(*args, extra=None):
        r = subprocess.run([bin_, *args], cwd=str(repo), env={**e, **(extra or {})}, capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
        return r.returncode, r.stdout + r.stderr
    run.fake, run.xdg, run.env = fake, xdg, e
    yield run
    subprocess.run([bin_, "chat", "gateway", "stop"], cwd=str(repo), env=e, capture_output=True)
    fake.stop()


def test_status_pair_and_failures(env):
    code, out = env("chat", "status")
    assert code == 0 and "no token" in out and "no paired users" in out and "gateway: not running" in out
    code, out = env("chat", "setup", "--token", "bad", "--chat-id", "1")
    assert code != 0 and "bot token" in out
    code, out = env("chat", "pair", "ABCD2345")
    assert code != 0 and "wrong or expired" in out
    code, out = env("chat", "pair")
    assert code != 0 and "needs the code" in out
    code, out = env("chat", "setup", "discord")
    assert code != 0 and ("No token entered" in out or "discord.py" in out), "no terminal: asks for a token (or prints the install line without the extra)"
    code, out = env("telegram", "status")
    assert code == 0 and "no token" in out and env("telegram", "--help")[0] == 0
    import re
    assert not re.search(r"^\s+telegram\s", env("--help")[1], re.M), "the old spelling is a hidden alias"


def test_setup_migrates_the_private_chat_and_pairing_is_approved_at_the_terminal(env):
    code, out = env("chat", "setup", "--token", TOKEN, "--chat-id", "777")
    assert code == 0 and "connected" in out and TOKEN not in out, out
    assert any("connected" in m for m in env.fake.sent)
    cfg = json.load(open(os.path.join(env.xdg, "research-os", "telegram.json")))
    assert cfg == {"token": TOKEN, "chat_id": "777"}
    out = env("chat", "status", "--json")[1]
    d = json.loads(out)
    assert d["platforms"]["telegram"]["users"][0]["role"] == "operator" and d["platforms"]["telegram"]["enabled"] and TOKEN not in out
    # a second person messages the bot, gets a code; the owner approves it in the terminal as a viewer
    env.fake.say("hello", chat=888)
    text = env.fake.wait_for("pairing code", 40)
    code_ = text.split("pairing code is ")[1].split(" ")[0]
    assert env("chat", "pair", code_)[0] == 0
    users = json.loads(env("chat", "status", "--json")[1])["platforms"]["telegram"]["users"]
    assert {u["user_id"]: u["role"] for u in users} == {"777": "operator", "888": "viewer"}
    assert env("chat", "pair", code_)[0] != 0, "a code is single use"
    assert env("chat", "unpair", "telegram", "888")[0] == 0
    code, out = env("chat", "remove")
    assert code == 0 and not os.path.exists(os.path.join(env.xdg, "research-os", "telegram.json"))


def test_configuration_is_refused_in_an_agent_environment(env):
    code, out = env("chat", "setup", "--token", TOKEN, "--chat-id", "1", extra={"CLAUDECODE": "1"})
    assert code != 0 and "Only the researcher" in out
    assert env("chat", "pair", "ABCD2345", extra={"RESEARCH_AGENT": "bot"})[0] != 0
    assert not os.path.exists(os.path.join(env.xdg, "research-os", "telegram.json"))


def test_rpc_ops_for_the_extension(repo, tmp_path, monkeypatch):
    Project.init(str(repo), name="demo", goal="g").close()
    s = api.Session(str(repo))
    assert {"chat_status", "chat_setup", "chat_remove", "chat_test", "chat_pair", "chat_users", "chat_gateway", "telegram_status", "telegram_set", "telegram_clear", "telegram_test"} <= set(api.OPS)
    assert api.call("telegram_status", s)["token_set"] is False
    st = api.call("chat_status", s)
    assert st["platforms"]["telegram"]["users"] == [] and st["gateway"]["running"] is False and TOKEN not in json.dumps(st)
    assert api.call("chat_users", s) == []
    with pytest.raises(Exception, match="not available from chat"):
        api.call("chat_setup", s, {"token": TOKEN}, surface="chat")
    with pytest.raises(Exception, match="not available from chat"):
        api.call("chat_pair", s, {"code": "X"}, surface="chat")
    assert [o for n, o in api.OPS.items() if o.mcp and n.startswith(("chat_", "telegram_"))] == [], "agents never see chat configuration"


def test_status_and_doctor_say_what_to_do_next(env):
    code, out = env("chat", "status")
    assert "research chat setup telegram" in out and "research chat doctor" in out
    code, out = env("chat", "doctor")
    assert code == 0 and "Telegram is not set up" in out and "research chat setup discord" in out
    env("chat", "setup", "--token", FakeTelegram and TOKEN, "--chat-id", "1")
    env("chat", "gateway", "stop")
    code, out = env("chat", "status")
    assert "gateway: not running - start it with `research chat gateway start`" in out
    code, out = env("chat", "doctor", "--live")
    assert "Telegram accepts the token" in out and "gateway" in out
    env("chat", "gateway", "start")
    code, out = env("chat", "doctor")
    assert code == 0 and "gateway is running" in out
