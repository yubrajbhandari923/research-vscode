"""WP15: the Discord adapter through a fake transport (no network, no discord.py): pairing, slash commands, threads, decision buttons and
select menu, authenticator modal, role checks, chunking, attachments, rate limits, allow-lists, setup. One test checks the real discord.py
API surface when the extra is installed."""
import asyncio
import inspect
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fakes"))
from fake_discord import FakeDiscord  # noqa: E402

from research import api, machine
from research.chat import actions, alerts, commands, discord_bot as D, gateway, manage, pairing
from research.chat.commands import Inbox
from research.chat.gateway import Gateway
from research.store import Project
from research.util import ResearchError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Two projects enabling Discord; alpha has channel 100, home channel 200; user 1 operator, 2 viewer, 3 approver."""
    roots = []
    for n in ("alpha", "beta"):
        r = tmp_path / n
        r.mkdir()
        Project.init(str(r), name=n, goal="g").close()
        machine.remember(str(r))
        roots.append(str(r))
    monkeypatch.setattr(commands, "enabled_projects", lambda platform: {os.path.basename(r): r for r in roots})
    D.save(token="x" * 10, guild_id="G1", application_id="42", home_channel_id="200", project_channels={"alpha": "100"})
    for uid, role in (("1", "operator"), ("2", "viewer"), ("3", "approver")):
        pairing.add_user("discord", uid, role, "u" + uid)
    fake = FakeDiscord()
    ch = D.DiscordChannel(fake, gap=0)
    ch.inbox = Inbox("discord")
    fake.roots = roots
    return fake, ch


def decision(root, n_options, protected_last=False):
    from research import decisions as D_
    opts = [{"key": str(i + 1), "label": f"Option {i + 1}"} for i in range(n_options)]
    with Project(root, sync=False, author={"author_type": "agent", "author_name": "inv", "author_model": None}) as p:
        return D_.request(p, "Which way?", opts, subject="T-1")


# ----------------------------------------------------------------------------------------------- slash commands, pairing, allow-lists
def test_slash_payload_has_typed_options_and_matches_the_router():
    names = {c["name"] for c in D.slash_payload()}
    assert names <= set(commands.COMMANDS) | set(commands.LOCAL), "every slash command is one the router knows"
    assert {"status", "say", "approve-start", "continue", "choose"} <= names and "pair" not in names
    by = {c["name"]: c for c in D.slash_payload()}
    say = {o["name"]: o for o in by["say"]["options"]}
    assert say["text"]["required"] and say["task"]["autocomplete"] and by["say"]["options"][0]["name"] == "text", "required options come first"
    assert [c["value"] for c in {o["name"]: o for o in by["usage"]["options"]}["period"]["choices"]] == ["today", "week"]
    assert {o["name"]: o for o in by["choose"]["options"]}["n"]["type"] == 4
    assert all(len(c["description"]) <= 100 and c["description"] for c in by.values())


def test_defer_comes_first_and_the_project_channel_implies_the_project(env, monkeypatch):
    fake, ch = env
    seen = []
    real = ch.inbox.command

    def slow(inc):
        seen.append(inc.text)
        fake.log.append("core")
        return real(inc)
    monkeypatch.setattr(ch.inbox, "command", slow)
    ix = run(fake.slash_cmd(ch, "status", where={"channel": "100"}))
    assert seen == ["/status alpha"] and fake.log.index(("defer", "status")) < fake.log.index("core")
    assert ix.impl.deferred is True and ix.impl.followups and ix.impl.followups[0].ephemeral
    ix = run(fake.slash_cmd(ch, "status", project="beta", where={"channel": "100"}))
    assert seen[-1] == "/status beta"
    run(fake.slash_cmd(ch, "say", text="try lr 1e-4", task="T-9", where={"channel": "100"}))
    assert seen[-1] == "/say alpha T-9 try lr 1e-4", "the router's argument order, project first"
    run(fake.slash_cmd(ch, "whoami", where={"channel": "100"}))
    assert seen[-1] == "/whoami"


def test_run_registers_the_slash_commands_and_dispatches(env):
    fake, ch = env

    async def go():
        t = asyncio.ensure_future(ch.run(ch.inbox))
        await asyncio.sleep(0.05)
        assert fake.slash and fake.slash[0]["type"] == 1
        ix = await fake.slash_cmd(ch, "whoami")
        await ch.close()
        await t
        return ix
    ix = run(go())
    assert "operator on discord" in ix.impl.followups[0].embed["description"]


def test_a_stranger_gets_a_pairing_code_in_a_dm_only_and_the_owner_approves_it_locally(env):
    fake, ch = env
    run(fake.do(ch, "message", "77", "eve", channel="dm9", guild=None, text="hello"))
    (txt,) = fake.texts("dm9")
    code = txt.split("code is ")[1].split(" ")[0]
    assert f"research chat pair {code}" in txt and pairing.role_of("discord", "77") is None
    run(fake.do(ch, "message", "78", "mallory", text="hi", guild="G1", channel="100"))
    assert fake.texts("100") == [], "a stranger in a server gets nothing"
    ix = run(fake.slash_cmd(ch, "status", user="78"))
    assert "Not authorized" in ix.impl.followups[0].content and not pairing._load().get("discord", {}).get("last_code", {}).get("78"), "no code in a server"
    pairing.approve(code, "viewer")
    ix = run(fake.slash_cmd(ch, "whoami", user="77"))
    assert "viewer on discord" in ix.impl.followups[0].embed["description"]
    run(fake.do(ch, "message", "9", "bot", text="x", guild=None, channel="dm1", is_bot=True))
    assert fake.texts("dm1") == [], "bots are ignored"


def test_guild_and_channel_allow_lists(env):
    fake, ch = env
    ix = run(fake.slash_cmd(ch, "whoami", where={"guild": "OTHER"}))
    assert ix.impl.rejected and not ix.impl.followups, "another server never reaches the router"
    D.save(allowed_channels=["300"])
    assert run(fake.slash_cmd(ch, "whoami", where={"channel": "301"})).impl.rejected
    assert run(fake.slash_cmd(ch, "whoami", where={"channel": "300"})).impl.followups
    assert run(fake.slash_cmd(ch, "whoami", where={"channel": "100"})).impl.followups, "a project channel is always allowed"
    assert run(fake.slash_cmd(ch, "whoami", where={"channel": "dm5", "guild": None})).impl.followups, "DMs are allowed"


def test_viewers_cannot_steer_and_see_the_role_message(env):
    fake, ch = env
    ix = run(fake.slash_cmd(ch, "say", user="2", text="stop it", task="T-1"))
    assert "needs the operator role" in ix.impl.followups[0].embed["description"]


def test_autocomplete_lists_projects_filtered_and_nothing_for_strangers(env):
    fake, ch = env
    ix = run(fake.do(ch, "autocomplete", name="status", focused="project", text="al"))
    assert ix.impl.choices == [("alpha", "alpha")]
    assert run(fake.do(ch, "autocomplete", "99", name="status", focused="project", text="")).impl.choices == []


# ----------------------------------------------------------------------------------------------- threads, embeds, chunking, files, rate limits
def test_alerts_use_one_thread_per_task_embeds_and_a_pointer(env):
    fake, ch = env
    a = fake.roots[0]
    with Project(a, sync=False) as p:
        alerts.raise_(p, "k1", "task_failed", "error", "T-12 failed twice\nsecond line", task="T-12",
                      actions=[{"label": "Show logs", "op": "chat_cmd_logs", "params": {"task": "T-12"}}])
        alerts.raise_(p, "k2", "halt", "warn", "T-12 halted", task="T-12")
    g = Gateway({"discord": ch}, discover=False)
    assert run(g.pump_once()) == 2
    assert len(fake.threads) == 1 and fake.threads[0][0] == "100", "one thread for the task, in the project channel"
    tid = str(5001)
    assert D.credentials()["threads"]["alpha"]["T-12"] == tid
    assert len(fake.sent[tid]) == 2, "both alerts in the thread"
    e = fake.sent[tid][0][1].embed
    assert e["color"] == D.COLORS["error"] and e["footer"] == {"text": "alpha"} and "timestamp" in e
    assert {"name": "Task", "value": "T-12", "inline": True} in e["fields"]
    labels = [b[0] for b in fake.sent[tid][0][1].buttons]
    assert labels == ["Show logs", "Instruct"]
    assert len(fake.sent["100"]) == 1 and f"<#{tid}>" in fake.sent["100"][0][1].content, "a one-line pointer for the error only"
    from research.chat import gdir, read_json
    assert f"{tid}:" in "".join(read_json(gdir("refs.json")))


def test_thread_per_task_can_be_switched_off_and_falls_back_without_permission(env):
    fake, ch = env
    with Project(fake.roots[0], sync=False) as p:
        p.save_config({"notifications.discord.thread_per_task": False})
        alerts.raise_(p, "k", "halt", "warn", "halted", task="T-1")
    run(Gateway({"discord": ch}, discover=False).pump_once())
    assert not fake.threads and len(fake.sent["100"]) == 1

    async def boom(channel_id, name):
        raise ResearchError("Discord refused the request (HTTP 403, code 50013)")
    fake.create_thread = boom
    run(ch.deliver(commands.Message("again", thread_key="T-2", project="alpha")))
    assert len(fake.sent["100"]) == 2, "no thread permission: the alert still arrives in the channel"


def test_text_chunking_4096_embed_and_2000_content():
    assert D.split("a" * 10, 4) == ["aaaa", "aaaa", "aa"] and D.split("short", 100) == ["short"]
    assert all(len(c) <= 10 for c in D.split("line one\nline two\nline three\n", 10))
    parts = D.render(commands.Message("x" * (4096 + 2000 + 5), title="Big"))
    assert len(parts[0].embed["description"]) == 4096 and parts[0].embed["title"] == "Big"
    assert [len(p.content) for p in parts[1:]] == [2000, 5] and all(p.embed is None for p in parts[1:])
    assert len(D.render(commands.Message("y" * 3000))) == 1


def test_secrets_are_redacted_and_files_respect_the_size_limit(tmp_path):
    tok = "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ" + "." + "GaBcDe" + "." + "abcdefghijklmnopqrstuvwxyz0123456789"
    small, big = tmp_path / "report.md", tmp_path / "plot.png"
    small.write_text("ok")
    big.write_bytes(b"0" * 5000)
    (p,) = D.render(commands.Message(f"token {tok}", files=[str(small), str(big), str(tmp_path / "gone.txt")]), limit=1000)
    assert tok not in p.embed["description"] and "[REDACTED]" in p.embed["description"]
    assert p.files == [str(small)] and "plot.png is too large or missing" in p.embed["description"] and "gone.txt" in p.embed["description"]
    assert D.render(commands.Message("x", files=[str(big)]))[0].files == [str(big)], "under the default 8 MiB cap it is attached"


def test_a_rate_limited_send_is_retried_and_a_persistent_one_is_left_for_the_outbox(env, monkeypatch):
    fake, ch = env
    slept = []

    async def nap(s):
        slept.append(s)
    monkeypatch.setattr(D.asyncio, "sleep", nap)
    fake.fail = [D.RateLimited(2.5)]
    ref = run(ch.deliver(commands.Message("hello")))
    assert ref.startswith("200:") and slept == [2.5] and fake.texts("200") == ["hello"]
    fake.fail = [D.RateLimited(1)] * 4
    with pytest.raises(ResearchError):
        run(ch.deliver(commands.Message("never")))
    with Project(fake.roots[0], sync=False) as p:
        alerts.raise_(p, "k", "halt", "error", "halted")
    fake.fail = [D.RateLimited(1)] * 4
    g = Gateway({"discord": ch}, discover=False)
    assert run(g.pump_once()) == 0
    assert run(g.pump_once()) == 1, "the outbox cursor stayed before the record: it is delivered on the next pass"


def test_sends_to_one_channel_are_paced(env, monkeypatch):
    fake, ch = env
    ch.gap = 1.0
    slept = []

    async def nap(s):
        slept.append(s)
    monkeypatch.setattr(D.asyncio, "sleep", nap)
    run(ch.deliver(commands.Message("a")))
    run(ch.deliver(commands.Message("b")))
    assert slept and 0 < slept[0] <= 1.0


# ----------------------------------------------------------------------------------------------- decisions and approvals
def test_decision_with_two_options_uses_buttons_and_answers_once(env):
    fake, ch = env
    d = decision(fake.roots[0], 2)
    with Project(fake.roots[0], sync=False) as p:
        from research import notify
        notify._scan(p, notify.cfg(p))
    run(Gateway({"discord": ch}, discover=False).pump_once())
    (mid, parts), = fake.sent["5001"]            # the decision belongs to T-1: its thread
    assert "T-1" in fake.sent["100"][0][1].content and [b[0] for b in parts.buttons[:2]] == ["1. Option 1", "2. Option 2"] and not parts.options and d["id"] in parts.embed["description"]
    cid = parts.buttons[0][1]
    mid = mid
    assert cid.startswith("a:") and len(cid) == 14 and "DEC" not in cid, "the button carries only a token"
    ix = run(fake.do(ch, "button", "1", "alice", channel="100", custom_id=cid, message_ref=f"5001:{mid}"))
    assert ix.impl.deferred is False and ix.impl.origin_edits and ix.impl.origin_edits[0].buttons == [] and "alice" in ix.impl.origin_edits[0].embed["description"]
    again = run(fake.do(ch, "button", "1", "alice", channel="100", custom_id=cid, message_ref=f"5001:{mid}"))
    assert "already done" in again.impl.followups[0].content
    with Project(fake.roots[0], sync=False) as p:
        from research import decisions as D_
        assert D_.load(p, d["id"])["answer"]["via"] == "chat:discord"


def test_decision_with_more_than_five_options_uses_a_select_menu(env):
    fake, ch = env
    d = decision(fake.roots[0], 7)
    with Project(fake.roots[0], sync=False) as p:
        from research import notify
        notify._scan(p, notify.cfg(p))
    run(Gateway({"discord": ch}, discover=False).pump_once())
    (mid, parts), = fake.sent["5001"]
    assert len(parts.options) == 7 and [b[0] for b in parts.buttons] == ["Instruct"] and parts.options[0][0] == "1. Option 1"
    ix = run(fake.do(ch, "select", "1", "alice", channel="100", custom_id="s:menu", values=[parts.options[2][1]], message_ref=f"5001:{mid}"))
    assert ix.impl.origin_edits and ix.impl.origin_edits[0].buttons == []
    with Project(fake.roots[0], sync=False) as p:
        from research import decisions as D_
        assert D_.load(p, d["id"])["answer"]["key"] == "3"


def test_a_free_text_reply_to_a_decision_card_is_the_answer_not_a_say(env, monkeypatch):
    fake, ch = env
    D.save(threads={"alpha": {"T-1": "5001"}})
    from research.chat import gdir, write_json
    write_json(gdir("refs.json"), {"5001:77": {"project": fake.roots[0], "label": "alpha", "decision": "DEC-3", "text": "card"}})
    seen = []
    ch.inbox = type("I", (), {"command": lambda self, inc: seen.append(inc.text) or commands.Reply()})()
    run(fake.do(ch, "message", "1", channel="5001", parent="100", text="try something else", reply_to="5001:77"))
    run(fake.do(ch, "message", "1", channel="5001", parent="100", text="please slow down"))
    run(fake.do(ch, "message", "1", channel="dm3", guild=None, text="T-4 look at the loss"))
    assert seen == ["try something else", "/say alpha T-1 please slow down", "T-4 look at the loss"]


def test_instruct_button_opens_a_modal_and_submits_a_say(env):
    fake, ch = env
    ix = run(fake.do(ch, "button", "1", custom_id="i:T-1|alpha"))
    assert ix.impl.modal["custom_id"] == "m:T-1|alpha" and ix.impl.modal["paragraph"] and ix.impl.deferred is None
    assert run(fake.do(ch, "button", "2", custom_id="i:T-1|alpha")).impl.rejected, "viewers cannot instruct"
    seen = []
    ch.inbox = type("I", (), {"command": lambda self, inc: seen.append(inc.text) or commands.Reply([commands.Message("ok")])})()
    sub = run(fake.do(ch, "modal", "1", custom_id="m:T-1|alpha", text="use a smaller lr"))
    assert seen == ["/say alpha T-1 use a smaller lr"] and sub.impl.deferred is True


def approve_op(monkeypatch, good="123456"):
    seen = []

    def fn(p, id, code, platform, user_id, role):
        seen.append(code)
        if code != good:
            raise ResearchError("wrong or already used code")
        return {"text": "Approved " + id}
    monkeypatch.setitem(api.OPS, "fake_approve", api.Op("fake_approve", fn, True, True, True, True, "approve", None))
    return seen


def test_authenticator_code_goes_through_an_ephemeral_modal_with_wrong_and_replayed_codes(env, monkeypatch):
    fake, ch = env
    seen = approve_op(monkeypatch)
    tok = actions.issue(fake.roots[0], "ask_code", "fake_approve", {"id": "PROP-7"}, "approver")
    cid = "a:" + tok
    assert run(fake.do(ch, "button", "1", custom_id=cid)).impl.rejected, "an operator cannot approve"
    ix = run(fake.do(ch, "button", "3", "boss", custom_id=cid, message_ref="100:900"))
    m = ix.impl.modal
    assert m["custom_id"] == "c:" + tok and m["max_length"] == 6 and "not your PIN" in m["label"] and ix.impl.deferred is None
    assert actions.peek(tok)["used"] is None, "opening the modal consumes nothing"
    bad = run(fake.do(ch, "modal", "3", "boss", custom_id=m["custom_id"], text="12ab56", message_ref="100:900"))
    assert not seen and "not a 6-digit" in bad.impl.followups[0].content, "a non-code is refused before it reaches anything"
    wrong = run(fake.do(ch, "modal", "3", "boss", custom_id=m["custom_id"], text="000000", message_ref="100:900"))
    assert seen == ["000000"] and "wrong or already used" in wrong.impl.followups[0].content and wrong.impl.deferred is True
    assert actions.peek(tok)["used"] is None, "after a wrong code the Approve button works again"
    ok = run(fake.do(ch, "modal", "3", "boss", custom_id=m["custom_id"], text="123456", message_ref="100:900"))
    assert "Approved PROP-7" in ok.impl.followups[0].content and ok.impl.followups[0].ephemeral
    assert fake.edits and fake.edits[0][:2] == ("100", "900") and "approved by boss via discord" in fake.edits[0][2].embed["description"]
    assert fake.edits[0][2].buttons == []
    replay = run(fake.do(ch, "button", "3", "boss", custom_id=cid, message_ref="100:900"))
    assert replay.impl.modal is None and "already done" in replay.impl.followups[0].content, "the card is single use after approval"
    shown = repr([(c, p) for c, v in fake.sent.items() for _, p in v]) + repr(fake.edits)
    assert "000000" not in shown and "123456" not in shown, "the code is never displayed anywhere"


def test_forbidden_actions_never_get_an_approve_button(env, monkeypatch):
    from research import approval, governance as G, supervisor as SV
    fake, ch = env
    pr = {"id": "PROP-1", "kind": "experiment", "status": "pending", "rev": 1, "fields": {}, "rationale": "r", "action": "frozen_test_edit"}
    monkeypatch.setattr(SV, "_prop_load", lambda p, pid: pr)
    monkeypatch.setattr(approval, "chat_status", lambda: {"chat_enrolled": True, "chat_disabled": False})
    monkeypatch.setattr(G, "cfg", lambda p: {"approval": {"chat": {"enabled": True}, "chat_allow": []}})
    local = {"label": "Approve on my computer", "text": "research proposal approve PROP-1"}
    with Project(fake.roots[0], sync=False) as p:
        acts = commands.card_actions(p, "proposal", "PROP-1") + [local]
        alerts.raise_(p, "k", "approval_needed", "warn", "PROP-1 needs you", actions=acts)
        pr.pop("action")
        pr["action"] = "promotion"
        alerts.raise_(p, "k2", "approval_needed", "warn", "PROP-2 needs you", actions=commands.card_actions(p, "proposal", "PROP-1") + [local])
    run(Gateway({"discord": ch}, discover=False).pump_once())
    first, second = [p for _, p in fake.sent["100"][:2]]
    assert [b[0] for b in first.buttons] == ["Approve on my computer"]
    assert [b[0] for b in second.buttons] == ["Approve (authenticator code)", "Approve on my computer"]
    ix = run(fake.do(ch, "button", "1", custom_id=first.buttons[0][1], message_ref="100:1"))
    cards = [f for f in ix.impl.followups if f.embed]
    assert "research proposal approve PROP-1" in cards[0].embed["description"] and cards[0].ephemeral


def test_a_viewer_clicking_an_operator_button_is_told_and_the_button_stays(env):
    fake, ch = env
    tok = actions.issue(fake.roots[0], "op", "chat_cmd_pause", {}, "operator")
    ix = run(fake.do(ch, "button", "2", custom_id="a:" + tok))
    assert "needs the operator role" in ix.impl.followups[0].content and ix.impl.followups[0].ephemeral
    assert actions.peek(tok)["used"] is None
    assert "Not authorized" in run(fake.do(ch, "button", "55", custom_id="a:" + tok)).impl.followups[0].content


# ----------------------------------------------------------------------------------------------- credentials and setup
def test_credentials_are_0600_never_in_status_and_discord_tokens_are_redacted(env):
    assert stat.S_IMODE(os.stat(D.path()).st_mode) == 0o600
    with Project(env[0].roots[0], sync=False) as p:
        st = manage.status(p)
    assert "xxxxxxxxxx" not in repr(st) and st["platforms"]["discord"]["token_set"] and st["platforms"]["discord"]["users"]


def test_invite_url_asks_for_minimal_scopes_and_permissions():
    u = D.invite_url("42")
    assert "client_id=42" in u and "scope=bot%20applications.commands" in u
    perms = int(u.split("permissions=")[1])
    for bit in (10, 11, 14, 15, 16, 35, 38):
        assert perms & (1 << bit)
    assert not perms & (1 << 3), "no administrator"


def test_setup_validates_the_token_lists_guilds_and_stores_it(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    Project.init(str(root), name="proj", goal="g").close()
    calls = []
    guilds = [[]]

    def rest(token, method, route, body=None):
        calls.append(route)
        if route == "/users/@me":
            if token == "bad":
                raise ResearchError("Discord rejected the token")
            return {"id": "42", "username": "ResearchBot", "bot": True}
        return guilds[0]
    monkeypatch.setattr(D, "_rest", rest)
    monkeypatch.setattr(D, "available", lambda: True)
    monkeypatch.setattr(manage.gateway, "ensure_running", lambda p=None: {"action": "none"})
    from research import notify
    monkeypatch.setattr(notify, "ensure_running", lambda p: {"action": "none"})
    with Project(str(root)) as p:
        with pytest.raises(ResearchError, match="rejected"):
            manage.setup_discord(p, "bad")
        with pytest.raises(ResearchError, match="not in a server") as ei:
            manage.setup_discord(p, "good-token")
        assert "oauth2/authorize" in ei.value.hint
        guilds[0] = [{"id": "G1", "name": "Lab"}, {"id": "G2", "name": "Other"}]
        with pytest.raises(ResearchError, match="several servers"):
            manage.setup_discord(p, "good-token")
        guilds[0] = [{"id": "G1", "name": "Lab"}]
        r = manage.setup_discord(p, "good-token", channel_id="555")
        assert r["guild"] == "Lab" and r["enabled"] and "good-token" not in repr(r)
        c = D.credentials()
        assert c["token"] == "good-token" and c["guild_id"] == "G1" and c["home_channel_id"] == "555" and c["project_channels"] == {"proj": "555"}
        assert stat.S_IMODE(os.stat(D.path()).st_mode) == 0o600
        assert "/users/@me/guilds" in calls
        manage.remove_discord(p)
        assert not D.credentials() and not os.path.exists(D.path())


def test_without_the_extra_setup_prints_the_install_line(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "available", lambda: False)
    r = tmp_path / "p"
    r.mkdir()
    Project.init(str(r), name="p", goal="g").close()
    with Project(str(r)) as p:
        with pytest.raises(ResearchError) as ei:
            manage.setup_discord(p, "t")
        assert "research-panel[discord]" in ei.value.hint or "discord.py" in ei.value.hint
        assert D.status(p)["install"] and not D.status(p)["library"]
    with pytest.raises(ResearchError):
        D.DiscordChannel()


# ----------------------------------------------------------------------------------------------- the real library, when installed
def test_real_transport_uses_only_discord_py_attributes_that_exist():
    d = pytest.importorskip("discord")
    for name in ("Client", "Intents", "AllowedMentions", "Embed", "File", "SelectOption", "ButtonStyle", "TextStyle", "ChannelType", "InteractionType", "LoginFailure", "HTTPException"):
        assert hasattr(d, name), name
    for name in ("View", "Button", "Select", "Modal", "TextInput"):
        assert hasattr(d.ui, name), name
    assert hasattr(d.app_commands, "Choice")
    for m in ("application_command", "autocomplete", "component", "modal_submit"):
        assert hasattr(d.InteractionType, m), m
    for m in ("primary", "danger", "secondary"):
        assert hasattr(d.ButtonStyle, m)
    assert hasattr(d.TextStyle, "short") and hasattr(d.TextStyle, "paragraph") and hasattr(d.ChannelType, "public_thread")
    assert hasattr(d.Intents.default(), "message_content") and d.Intents.default().message_content is False, "no privileged intent"
    assert hasattr(d.AllowedMentions, "none") and hasattr(d.Embed, "from_dict")

    def params(fn):
        return set(inspect.signature(fn).parameters)
    assert {"application_id", "guild_id", "payload"} <= params(d.http.HTTPClient.bulk_upsert_guild_commands)
    assert {"content", "embed", "view", "files"} <= params(d.abc.Messageable.send)
    assert {"content", "embeds", "view"} <= params(d.PartialMessage.edit)
    assert hasattr(d.PartialMessage, "delete") and hasattr(d.Client, "get_partial_messageable") and hasattr(d.Client, "fetch_channel")
    assert {"name", "type", "auto_archive_duration"} <= params(d.TextChannel.create_thread)
    assert {"ephemeral", "thinking"} <= params(d.InteractionResponse.defer)
    for fn in ("send_message", "send_modal", "autocomplete", "defer"):
        assert hasattr(d.InteractionResponse, fn), fn
    assert "ephemeral" in params(d.Webhook.send) and hasattr(d.Interaction, "edit_original_response")
    assert {"title", "custom_id", "timeout"} <= params(d.ui.Modal.__init__)
    assert {"label", "custom_id", "style", "required", "min_length", "max_length"} <= params(d.ui.TextInput.__init__)
    assert {"label", "custom_id", "style"} <= params(d.ui.Button.__init__) and {"custom_id", "placeholder", "options"} <= params(d.ui.Select.__init__)
    assert hasattr(d.User, "display_name") and hasattr(d.ClientUser, "mentioned_in")
    t = D.RealTransport("tok", "42", "1")      # constructing it must not need a network

    async def build():
        return t._view(D.Parts(buttons=[("a", "a:x", "primary")], options=[("o", "v")])), t._kw(D.Parts(embed={"description": "x", "color": 1, "fields": []}), edit=True)
    v, kw = asyncio.run(build())
    assert len(v.children) == 2 and kw["view"] is None and len(kw["embeds"]) == 1


# ----------------------------------------------------------------------------------------------- HTTP 429 during setup, and `chat doctor`
class _Resp:
    def __init__(self, body): self._b = json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _http_429(headers=None, body=None):
    import email.message
    import io
    import urllib.error
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://discord.com/api/v10/x", 429, "Too Many Requests", h, io.BytesIO(json.dumps(body or {}).encode()))


@pytest.fixture
def wire(monkeypatch):
    """Scripted Discord REST: a list of outcomes per route (an exception is raised, anything else returned); sleeps are recorded, not slept."""
    script, sleeps, notices = {}, [], []

    def urlopen(req, timeout=0):
        route = req.full_url.split("/api/v10")[1]
        out = script[route].pop(0) if len(script[route]) > 1 else script[route][0]
        if isinstance(out, Exception):
            raise out
        return _Resp(out)
    monkeypatch.setattr(D.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(D, "_sleep", sleeps.append)
    monkeypatch.setattr(D, "NOTICE", notices.append)
    monkeypatch.setattr(D, "available", lambda: True)
    monkeypatch.setattr(manage.gateway, "ensure_running", lambda p=None: {"action": "none"})
    from research import notify
    monkeypatch.setattr(notify, "ensure_running", lambda p: {"action": "none"})
    script["sleeps"], script["notices"] = sleeps, notices
    return script


ME = {"id": "42", "username": "ResearchBot", "bot": True}


def test_429_with_retry_after_header_waits_then_succeeds(wire):
    wire["/users/@me/guilds"] = [_http_429({"Retry-After": "1.5"}), [{"id": "G1", "name": "Lab"}]]
    assert D.guilds("tok") == [{"id": "G1", "name": "Lab"}]
    assert wire["sleeps"] == [1.5] and "retrying in 1.5 s" in wire["notices"][0] and "/users/@me/guilds" in wire["notices"][0]


def test_429_reads_retry_after_from_the_json_body_and_retries_more_than_once(wire):
    wire["/users/@me/guilds"] = [_http_429(body={"retry_after": 0.5}), _http_429(body={"retry_after": 2}), []]
    assert D.guilds("tok") == [] and wire["sleeps"] == [0.5, 2.0]


def test_429_asking_for_a_long_wait_fails_clearly_without_sleeping(wire):
    wire["/users/@me/guilds"] = [_http_429({"Retry-After": "90"})]
    with pytest.raises(ResearchError, match=r"rate-limiting this bot.*90 s") as ei:
        D.guilds("tok")
    assert "Nothing was saved" in ei.value.hint and wire["sleeps"] == []


def test_429_is_bounded_when_discord_keeps_limiting(wire):
    wire["/users/@me/guilds"] = [_http_429({"Retry-After": "1"})]
    with pytest.raises(ResearchError, match=r"still limited after 4 tries"):
        D.guilds("tok")
    assert wire["sleeps"] == [1.0, 1.0, 1.0]


def test_setup_recovers_from_429_and_stores_only_complete_credentials(tmp_path, wire):
    root = tmp_path / "proj"
    root.mkdir()
    Project.init(str(root), name="proj", goal="g").close()
    wire["/users/@me"] = [ME]
    wire["/users/@me/guilds"] = [_http_429({"Retry-After": "2"}), [{"id": "G1", "name": "Lab"}]]
    with Project(str(root)) as p:
        r = manage.setup_discord(p, "good-token")
        assert r["guild"] == "Lab" and D.credentials()["token"] == "good-token" and D.credentials()["guild_id"] == "G1"
        assert wire["sleeps"] == [2.0]
        manage.remove_discord(p)
    # a limit that does not clear leaves nothing behind: no token, no half-linked server, Discord stays disabled
    wire["/users/@me/guilds"] = [_http_429({"Retry-After": "120"})]
    with Project(str(root)) as p:
        with pytest.raises(ResearchError, match="rate-limiting"):
            manage.setup_discord(p, "good-token")
        assert not D.credentials() and not os.path.exists(D.path()) and D.status(p)["enabled"] is not True


def test_setup_with_an_inspection_makes_no_second_round_trip(tmp_path, wire):
    root = tmp_path / "proj"
    root.mkdir()
    Project.init(str(root), name="proj", goal="g").close()
    wire["/users/@me"] = [ME]
    wire["/users/@me/guilds"] = [[{"id": "G1", "name": "Lab"}]]
    calls = []
    real = D._rest_once
    D._rest_once = lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1]
    try:
        with Project(str(root)) as p:
            info = manage.inspect_discord("good-token")
            manage.setup_discord(p, "good-token", info=info)
    finally:
        D._rest_once = real
    assert calls.count("/users/@me/guilds") == 1


def test_chat_doctor_names_each_problem_and_its_fix(tmp_path, wire, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    Project.init(str(root), name="proj", goal="g").close()
    monkeypatch.setattr(manage.gateway, "status", lambda: {"running": False, "pid": None, "platforms": [], "stale": False})
    with Project(str(root)) as p:
        d = manage.doctor(p)
        assert d["ok"] and {c["id"] for c in d["checks"]} >= {"telegram", "discord", "gateway"} and d["counts"]["error"] == 0
        D.save(token="t", application_id="42")                     # a half-finished Discord setup
        d = {c["id"]: c for c in manage.doctor(p)["checks"]}
        assert d["discord.guild"]["level"] == "error" and "research chat setup discord" in d["discord.guild"]["fix"]
        assert d["gateway"]["level"] == "error" and "research chat gateway start" in d["gateway"]["fix"]
        assert d["discord.home"]["level"] == "warn" and "/sethome" in d["discord.home"]["fix"]
        # --live reports Discord's own answer, including a rate limit, and the offline run never touches the network
        wire["/users/@me"] = [_http_429({"Retry-After": "60"})]
        live = {c["id"]: c for c in manage.doctor(p, live=True)["checks"]}
        assert live["discord.live"]["level"] == "error" and "rate-limiting" in live["discord.live"]["message"] and "Wait that long" in live["discord.live"]["message"]
        wire["/users/@me"] = [ME]
        wire["/users/@me/guilds"] = [[{"id": "OTHER", "name": "Elsewhere"}]]
        D.save(guild_id="G1")
        live = {c["id"]: c for c in manage.doctor(p, live=True)["checks"]}
        assert live["discord.live"]["level"] == "ok" and live["discord.member"]["level"] == "error" and "oauth2/authorize" in live["discord.member"]["fix"]


# ----------------------------------------------------------------------------------------------- /sethome, /say, /tasks, /health
def _posts(monkeypatch):
    posts = []
    monkeypatch.setattr(D, "_rest", lambda token, method, route, body=None: posts.append((method, route, (body or {}).get("content"))) or {})
    return posts


def test_sethome_is_saved_in_discord_json_and_status_doctor_test_and_alerts_agree(env, monkeypatch):
    from research.chat import Message, read_json, write_json
    fake, ch = env
    before = D.credentials()
    ix = run(fake.slash_cmd(ch, "sethome", where={"channel": "300"}))
    assert "now receives digests" in ix.impl.followups[0].embed["description"] and "research chat test discord" in ix.impl.followups[0].embed["description"]
    c = D.credentials()
    assert c["home_channel_id"] == "300"
    assert {k: c[k] for k in ("token", "guild_id", "application_id", "project_channels")} == {k: before[k] for k in ("token", "guild_id", "application_id", "project_channels")}, \
        "/sethome merges: credentials and project channel mappings stay"
    assert not (read_json(gateway.gdir("chats.json")).get("home") or {}).get("discord"), "no second copy"
    with Project(fake.roots[0]) as p:
        assert D.status(p)["home_channel_set"] and manage.status(p)["platforms"]["discord"]["home_channel_set"]
        assert {c["id"]: c["level"] for c in manage.doctor(p)["checks"]}["discord.home"] == "ok"
    posts = _posts(monkeypatch)
    assert manage.test_discord() == {"sent": True, "channel": "300"} and posts[0][:2] == ("POST", "/channels/300/messages")
    run(ch.deliver(Message("an alert", chat=None)))                       # no project channel, no explicit chat: the home channel
    assert any("an alert" in (t or "") for t in fake.texts("300"))
    # a gateway restart: new channel and gateway objects read the same file
    fake2 = FakeDiscord()
    ch2 = D.DiscordChannel(fake2, gap=0)
    assert gateway.Gateway()._home("discord", Message("x")).chat == "300"
    run(ch2.deliver(Message("after restart")))
    assert any("after restart" in (t or "") for t in fake2.texts("300"))
    # a home set by an older version lived only in gateway/chats.json: it is adopted, not lost
    c = D.credentials()
    c.pop("home_channel_id")
    write_json(D.path(), c)
    write_json(gateway.gdir("chats.json"), {"home": {"discord": "400"}})
    assert D.home_channel() == "400" and D.credentials()["home_channel_id"] == "400"


def test_test_discord_says_what_is_missing(env):
    from research.chat import write_json
    fake, ch = env
    c = D.credentials()
    c.pop("home_channel_id")
    write_json(D.path(), c)
    r = manage.test_discord()
    assert r["sent"] is False and "/sethome" in r["reason"]
    D.clear_credentials()
    assert "research chat setup discord" in manage.test_discord()["reason"]


def test_say_without_a_task_picks_the_one_that_needs_you_or_lists_the_choices(env, monkeypatch):
    from research import intervene
    from research.api import chat as CHAT
    fake, ch = env
    sent = []
    monkeypatch.setattr(intervene, "request", lambda p, task, text, source="": sent.append((task, text)) or {"state": "approved", "summary": "s", "id": "I-1"})
    t = lambda tid, human: {"task": tid, "title": "Train model", "phase": "blocked" if human else "running", "plan": "main", "attempts": 2, "reason": None, "human": human}
    with Project(fake.roots[0]) as p:
        monkeypatch.setattr(CHAT, "_live", lambda p_: [t("T-3", True), t("T-5", False)])
        CHAT._instruct(p, None, "lower the lr", "discord")
        assert sent == [("T-3", "lower the lr")], "the task that needs a human wins"
        monkeypatch.setattr(CHAT, "_live", lambda p_: [t("T-5", False)])
        CHAT._instruct(p, None, "keep going", "discord")
        assert sent[-1] == ("T-5", "keep going"), "the only running task is the default"
        monkeypatch.setattr(CHAT, "_live", lambda p_: [t("T-3", True), t("T-4", True), t("T-5", False)])
        with pytest.raises(ResearchError) as ei:
            CHAT._instruct(p, None, "x", "discord")
        msg = str(ei.value)
        assert "Needs you:" in msg and "T-3 Train model [blocked, attempt 2]" in msg and "T-4" in msg and "Running: T-5" in msg and "/say T-3 <instruction>" in msg
        monkeypatch.setattr(CHAT, "_live", lambda p_: [])
        with pytest.raises(ResearchError, match="No task is running or waiting"):
            CHAT._instruct(p, None, "x", "discord")
        monkeypatch.setattr(CHAT, "_live", lambda p_: [t("T-3", True), t("T-5", False)])
        r = CHAT.chat_cmd_tasks(p)
        assert "Needs you:" in r["text"] and "/say T-3" in r["text"] and "Running:" in r["text"] and "/stop T-5" in r["text"]


def test_slash_commands_in_a_task_thread_mean_that_task(env, monkeypatch):
    fake, ch = env
    D.save(threads={"alpha": {"T-9": "555"}})
    seen = []
    real = ch.inbox.command
    monkeypatch.setattr(ch.inbox, "command", lambda inc: seen.append(inc.text) or real(inc))
    run(fake.slash_cmd(ch, "say", text="lower the lr", where={"channel": "555"}))
    assert seen[-1] == "/say alpha T-9 lower the lr"
    run(fake.slash_cmd(ch, "stop", where={"channel": "555"}))
    assert seen[-1] == "/stop alpha T-9"
    run(fake.slash_cmd(ch, "say", text="other", task="T-1", where={"channel": "555"}))
    assert seen[-1] == "/say alpha T-1 other", "an explicit task still wins"


def test_health_command_and_tasks_are_registered(env):
    assert "tasks" in commands.COMMANDS and "health" in commands.LOCAL
    assert {"tasks", "health"} <= {c["name"] for c in D.slash_payload()}
    fake, ch = env
    ix = run(fake.slash_cmd(ch, "health", where={"channel": "100"}))          # a project channel implies the project
    d = ix.impl.followups[0].embed["description"]
    assert d.startswith("Chat health for") and "gateway" in d


# ----------------------------------------------------------------------------------------------- push notifications: either platform, levels, mentions
PLAN_YAML = """\
schema_version: 1
plans:
  - key: x
    title: X
    objective: o
    experiments:
      - key: e
        title: E
        hypothesis: h
        tasks:
          - {key: t, title: T, agent: {role: implementer, profile: fake}, prompt: "p"}
"""


def _enable(p, telegram=False, discord=False, **more):
    p.save_config({"notifications.telegram.enabled": telegram, "notifications.discord.enabled": discord, **more})
    p.config = p.load_config()


def _outbox(p):
    return alerts.read_from(alerts.outbox_path(p))[0]


def test_chat_platforms_are_independent_discord_only_telegram_only_both_neither(env):
    from research import notify
    from research.chat import telegram
    fake, ch = env                                                     # the fixture saved Discord credentials only
    with Project(fake.roots[0], sync=False) as p:
        def got(tg, dc):
            _enable(p, tg, dc)
            return notify.chat_platforms(p), notify.chat_enabled(p)
        assert got(False, False) == ([], False)                         # neither
        assert got(False, True) == (["discord"], True), "Discord alone: no Telegram credentials are needed"
        assert got(True, False) == ([], False), "Telegram enabled but no Telegram token: nothing to push to"
        telegram.set_credentials("123456:" + "A" * 35, 777)
        assert got(True, False) == (["telegram"], True)                  # Telegram only: unchanged behaviour
        assert got(True, True) == (["telegram", "discord"], True)        # both
        assert got(False, False) == ([], False), "credentials without the project switch push nothing"
        D.clear_credentials()
        assert got(False, True) == ([], False), "Discord enabled but no token"


def test_discord_only_plan_alerts_reach_the_gateway_once_and_survive_restarts(env, monkeypatch):
    import sys
    from research import notify, planspec as PS, supervisor as SV
    fake, ch = env
    gateways = []
    monkeypatch.setattr(notify, "ensure_gateway", lambda p: gateways.append(p.root) or {"action": "none"})
    root = fake.roots[0]
    with Project(root, sync=False) as p:
        _enable(p, discord=True)
        p.config["agents"]["profiles"]["fake"] = {"command": sys.executable}
        PS.apply(p, PLAN_YAML)
        pid = PS.load_state(p, "x")["plan_id"]
        SV.plan_run(p, "x", start_daemon=False)
        rs = SV.load_run(p, pid)
        rs["state"] = "completed"
        SV.save_run(p, rs)
        notify.step(p)                                                   # the supervisor's pass: no Telegram anywhere
        kinds = [r["kind"] for r in _outbox(p)]
        assert kinds.count("plan_finished") == 1 and kinds.count("plan_started") == 1 and gateways == [root], "the gateway is kept alive for Discord too"
        notify.step(p)
        assert len(_outbox(p)) == len(kinds), "no duplicate on the next pass"
    with Project(root, sync=False) as p2:                                # supervisor restart: state is on disk
        notify.step(p2)
        assert len(_outbox(p2)) == len(kinds)
    g = Gateway({"discord": ch}, discover=False)
    assert run(g.pump_once()) == len(kinds)
    assert any("finished" in (t or "") for t in fake.texts("100") + fake.texts("200"))
    assert run(Gateway({"discord": ch}, discover=False).pump_once()) == 0, "a restarted gateway does not deliver them again"
    with Project(root, sync=False) as p3:
        notify.step(p3)
        assert len(_outbox(p3)) == len(kinds)


def test_notification_level_filters_kinds_and_never_replays_what_it_skipped(env):
    fake, ch = env
    with Project(fake.roots[0], sync=False) as p:
        n = iter(range(1000))

        def sends(kind, once=False, key=None):
            return alerts.raise_(p, key or f"{kind}:{next(n)}", kind, "info", "x", once=once) is not None
        _enable(p, discord=True, **{"notifications.level": "low"})
        assert all([sends("halt"), sends("task_failed"), sends("run_failed"), sends("plan_started", True, "ps"), sends("plan_finished", True, "pf")])
        assert not any([sends("approval_needed"), sends("decision"), sends("provider_limit"), sends("task_verified", True, "tv1"), sends("task_started")])
        _enable(p, discord=True, **{"notifications.level": "medium"})
        assert all([sends("approval_needed"), sends("decision"), sends("promotion_ready")]) and not sends("task_started")
        _enable(p, discord=True, **{"notifications.level": "high"})
        assert sends("task_started") and sends("task_verified", True, "tv2")
        assert not sends("task_verified", True, "tv1"), "an event skipped at a lower level is not replayed when you raise it"
        _enable(p, discord=True, **{"notifications.level": "off"})
        assert not any([sends("halt"), sends("plan_finished"), sends("plan_started")])
    from research import registry
    assert registry.default("notifications.level") == "medium", "default: everything that was sent before levels existed"


def test_discord_alerts_that_need_a_human_mention_the_operators_so_discord_pushes(env):
    fake, ch = env
    root = fake.roots[0]
    with Project(root, sync=False) as p:
        _enable(p, discord=True)
        alerts.raise_(p, "k1", "halt", "error", "halted")
        alerts.raise_(p, "k2", "plan_finished", "ok", "all done", once=True)
        alerts.raise_(p, "k3", "approval_needed", "warn", "needs you")
    run(Gateway({"discord": ch}, discover=False).pump_once())
    by = {(p_.embed or {}).get("description"): p_ for _, p_ in fake.sent["100"]}

    assert by["[alpha] halted"].mention == ["1", "3"] and by["[alpha] needs you"].mention == ["1", "3"], "operator and approver, not the viewer"
    assert by["[alpha] all done"].mention == [], "good news does not ping"
    with Project(root, sync=False) as p:
        _enable(p, discord=True, **{"notifications.discord.mention": False})
        alerts.raise_(p, "k4", "halt", "error", "halted again")
    run(Gateway({"discord": ch}, discover=False).pump_once())
    assert [p_ for _, p_ in fake.sent["100"] if (p_.embed or {}).get("description") == "[alpha] halted again"][0].mention == []


def test_telegram_alerts_are_never_sent_silently(monkeypatch):
    from research.chat import telegram
    sent = []
    monkeypatch.setattr(telegram, "_api", lambda token, method, params, timeout=15.0: sent.append((method, params)) or {"result": {"message_id": 1}})
    telegram.set_credentials("123456:" + "A" * 35, 777)
    telegram.TelegramChannel().send_message(commands.Message("Plan x finished.", chat="777", color="ok"))
    assert sent and sent[0][0] == "sendMessage" and "disable_notification" not in sent[0][1], "Telegram pushes unless a message asks to be silent"
