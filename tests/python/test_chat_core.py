"""WP14a: chat core with FakeChannel and a fake clock: pairing, allow-list, roles, router, buttons, alerts, project isolation, op policy."""
import asyncio
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fakes"))
from fake_channel import FakeChannel  # noqa: E402

from research import api, machine
from research.chat import actions, alerts, commands, pairing
from research.store import Project
from research.util import ResearchError


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    for m in (pairing, actions, alerts, commands):
        monkeypatch.setattr(m, "_now", c)
    return c


def enable(p, platform="fake", **cfg):
    """Chat is enabled per platform in the project config; `fake` stands in for Discord/Telegram."""
    p.save_config({f"notifications.{platform}.enabled": True} if platform == "telegram" else {})
    machine.remember(p.root)


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Two initialized projects that both enable the fake platform."""
    roots = []
    for n in ("alpha", "beta"):
        r = tmp_path / n
        r.mkdir()
        Project.init(str(r), name=n, goal="g").close()
        machine.remember(str(r))
        roots.append(str(r))
    monkeypatch.setattr(commands, "enabled_projects", lambda platform: {os.path.basename(r): r for r in roots})
    return roots


@pytest.fixture
def ch(two):
    pairing.add_user("fake", "1", "operator", "owner")
    pairing.add_user("fake", "2", "viewer", "guest")
    return FakeChannel()


# ----------------------------------------------------------------------------------------------- pairing
def test_pairing_code_flow_expiry_limits_and_lockout(clock):
    pairing.add_user("fake", "9", None)
    assert pairing.role_of("fake", "9") == "operator", "the first paired user is the operator"
    assert pairing.add_user("fake", "10")["role"] == "viewer", "later users default to viewer"
    code = pairing.request_code("fake", "20", "bob")
    assert len(code) == 8 and not set(code) & set("01OI")
    assert pairing.request_code("fake", "20") is None, "one live code per user"
    codes = [pairing.request_code("fake", str(u)) for u in (21, 22, 23)]
    assert codes[2] is None, "at most 3 pending per platform"
    u = pairing.approve(code, "operator")
    assert u["user_id"] == "20" and pairing.role_of("fake", "20") == "operator"
    with pytest.raises(ResearchError):
        pairing.approve(code)                           # single use
    c2 = codes[0]
    clock.t += 3601
    with pytest.raises(ResearchError, match="wrong or expired"):
        pairing.approve(c2)                             # expired
    for _ in range(4):
        with pytest.raises(ResearchError):
            pairing.approve("ZZZZZZZZ")
    with pytest.raises(ResearchError, match="Too many"):
        pairing.approve("ZZZZZZZZ")                     # 5 wrong attempts: locked for an hour
    code3 = pairing.request_code("fake", "30")
    with pytest.raises(ResearchError, match="Too many"):
        pairing.approve(code3)
    clock.t += 3601
    assert pairing.approve(pairing.request_code("fake", "31"))["user_id"] == "31"
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(pairing._path()).st_mode) == 0o600


def test_unknown_users_get_a_code_in_private_and_nothing_in_groups(ch, clock):
    r = ch.say("/status", user="77", name="eve")
    t = r.messages[0].text
    assert "pairing code" in t and "research chat pair" in t
    assert ch.say("/status", user="77", group=True).messages == [], "a group member who is not paired gets no reply"
    assert "pairing code" in ch.say("/pair", user="78", group=True).messages[0].text, "but /pair in the group gives that user a code"
    assert "pairing code" in ch.say("hi", user="88").messages[0].text
    assert "Not authorized" in ch.say("hi", user="88").messages[0].text, "while a code is pending: one polite no"
    assert ch.say("hi", user="88").messages == [], "one 'not authorized' per hour"
    assert ch.say("/status", user="1", bot=True).messages == [], "other bots are ignored"


# ----------------------------------------------------------------------------------------------- roles and op policy
def test_roles_op_policy_and_protected_refusals(ch, two):
    for u in ("1", "2"):
        ch.say("/project use alpha", user=u)
    assert "No plan has been run" in ch.say("/status", user="2").messages[0].text, "viewers read"
    r = ch.say("/pause", user="2").messages[0].text
    assert "needs the operator role" in r, "viewers cannot steer"
    assert "Name exactly one plan" in ch.say("/pause", user="1").messages[0].text, "operators reach the op (which then says there is nothing to pause)"
    for cmd in ("/approve PROP-1", "/accept T-1", "/void T-1", "/promote EXP-1", "/restart"):
        assert "needs your computer" in ch.say(cmd, user="1").messages[0].text, cmd
    assert "Unknown command" in ch.say("/nonsense", user="1").messages[0].text
    assert {o.chat for n, o in api.OPS.items() if n.startswith("chat_cmd_")} <= {"read", "decide"}, "no chat op can approve anything"
    forbidden = {"proposal_decide", "intervention_action", "supervisor_task", "plan_control", "supervisor_daemon", "approval_set_pin", "freeze_tests", "plan_promote"}
    assert all(api.OPS[n].chat is None for n in forbidden if n in api.OPS), "protected ops are not exposed to chat"
    with pytest.raises(Exception, match="not available from chat"):
        api.call("proposal_decide", api.Session(two[0]), {"id": "PROP-1", "approve": True}, surface="chat")
    assert "viewer" in ch.say("/whoami", user="2").messages[0].text and "/pause" in ch.say("/help", user="1").messages[0].text
    assert "/pause" not in ch.say("/help", user="2").messages[0].text, "help lists only what the role may do"


def test_a_command_in_one_project_never_touches_the_other(ch, two, monkeypatch):
    a, b = two
    roots = []
    orig = commands.Inbox._run
    monkeypatch.setattr(commands.Inbox, "_run", lambda self, root, op, params, inc: roots.append(root) or orig(self, root, op, params, inc))
    r = ch.say("/status", user="1", chat="X").messages[0].text
    assert "Several projects" in r and not roots, "two projects and no selection: ask, never guess"
    ch.say("/project use alpha", user="1", chat="X")
    ch.say("/project use beta", user="1", chat="Y")
    ch.say("/status", user="1", chat="X")
    ch.say("/status", user="1", chat="Y")
    ch.say("/status", user="1", chat="X")
    assert roots == [a, b, a], "each chat reaches only its own project"
    assert enabled_label(ch, "X") == a and enabled_label(ch, "Y") == b


def enabled_label(ch, chat):
    from research.chat import gdir, read_json
    return read_json(gdir("chats.json"))["selected"][f"fake:{chat}"]


# ----------------------------------------------------------------------------------------------- buttons
def test_buttons_are_single_use_expiring_and_role_checked(ch, two, clock):
    tok = actions.issue(two[0], "op", "chat_cmd_status", {}, "viewer")
    assert actions.peek(tok)["params"] == {} and len(tok) == 12
    ch.say("/project use alpha", user="1")
    r = ch.click(tok, user="1")
    assert r.toast == "Done" and "No plan" in r.messages[0].text and r.edit is not None
    assert "already done by" in ch.click(tok, user="1").toast, "single use"
    tok2 = actions.issue(two[0], "op", "chat_cmd_status", {}, "viewer")
    clock.t += 25 * 3600
    assert "expired" in ch.click(tok2, user="1").toast
    tok3 = actions.issue(two[0], "op", "chat_cmd_pause", {"plan": "x"}, "operator")
    assert "operator role" in ch.click(tok3, user="2").toast
    assert "Not authorized" in ch.click(tok3, user="404").toast
    assert actions.peek(tok3)["used"] is None, "a refused click does not spend the button"
    assert "not valid" in ch.click("nonexistent", user="1").toast


def test_local_button_replies_with_the_command_and_never_runs_anything(ch, two):
    tok = actions.issue(two[0], "local", text="research proposal approve PROP-7")
    r = ch.click(tok, user="1")
    assert "research proposal approve PROP-7" in r.messages[0].text


def test_authenticator_code_flow_binds_to_the_op_and_deletes_the_message(ch, two, monkeypatch):
    """H.9 seen from the chat side: WP14b supplies the op; the router asks for the code, passes it only to the op, deletes the message."""
    seen = []
    monkeypatch.setitem(api.OPS, "fake_approve", api.Op("fake_approve", lambda p, id, code, platform, user_id, role: seen.append((id, code, platform, user_id, role)) or {"text": "Approved " + id},
                                                       True, True, True, True, "approve", None))
    pairing.add_user("fake", "3", "approver", "boss")
    tok = actions.issue(two[0], "ask_code", "fake_approve", {"id": "PROP-7"}, "approver")
    assert "needs the approver role" in ch.click(tok, user="1").toast, "operators cannot approve"
    r = ch.click(tok, user="3", name="boss", ref="c:5")
    assert "6-digit" in r.messages[0].text
    r = ch.say("123456", user="3", name="boss", ref="c:77")
    assert seen == [("PROP-7", "123456", "fake", "3", "approver")] and r.delete == ["c:77"] and "Approved PROP-7" in r.messages[0].text
    assert ch.say("123456", user="3").messages[0].text.startswith("Not done"), "no pending request: a stray code is just a /say"
    assert not seen[1:], "the pending code request is consumed: a stray 6-digit message never reaches the op again"


def test_free_text_reply_to_a_decision_card_goes_to_the_decision_op(ch, two, monkeypatch):
    from research.chat import gdir, write_json
    seen = []
    monkeypatch.setitem(api.OPS, "decision_answer", api.Op("decision_answer", lambda p, id, text, via: seen.append((id, text, via)) or {"text": "understood"},
                                                           True, True, True, True, "decide", None))
    write_json(gdir("refs.json"), {"c:42": {"project": two[0], "label": "alpha", "decision": "DEC-3", "text": "card"}})
    r = ch.say("try lr 1e-4 instead", user="1", reply_to="c:42")
    assert seen == [("DEC-3", "try lr 1e-4 instead", "chat:fake")] and "understood" in r.messages[0].text
    assert ch.say("try lr 1e-4", user="2", reply_to="c:42").messages[0].text.startswith("Answering needs"), "viewers cannot answer"


# ----------------------------------------------------------------------------------------------- alerts
def test_alert_dedupe_cooldown_escalation_and_clear(two, clock):
    with Project(two[0], sync=False) as p:
        out = lambda: alerts.read_from(alerts.outbox_path(p))[0]       # noqa: E731
        assert alerts.raise_(p, "k1", "halt", "error", "plan halted", task="T-1")
        assert alerts.raise_(p, "k1", "halt", "error", "plan halted", task="T-1") is None, "same key inside the cooldown: suppressed"
        clock.t += 31 * 60
        again = alerts.raise_(p, "k1", "halt", "error", "plan halted", task="T-1")
        assert again["text"].startswith("still: "), "a reminder after the cooldown"
        alerts.raise_(p, "k2", "halt", "error", "again", task="T-1")
        third = alerts.raise_(p, "k3", "halt", "error", "again", task="T-1")
        assert "3 halt alerts in a row" in third["text"] and "consider" in third["text"], "escalation at the third"
        assert alerts.clear(p, "k3", "moving again")["clear"] and alerts.clear(p, "k3", "x") is None, "'moving again' once"
        assert alerts.raise_(p, "k4", "task_failed", "error", "x", task="T-9")["text"] == "x", "a new task starts its own count"
        assert alerts.raise_(p, "once", "plan_finished", "ok", "done", once=True) and alerts.raise_(p, "once", "plan_finished", "ok", "done", once=True) is None
        p.save_config({"notifications.alerts.on": ["halt"]})
        p.config = p.load_config()
        assert alerts.raise_(p, "z", "task_failed", "error", "off") is None, "kinds can be switched off"
        recs = out()
        assert [r["key"] for r in recs][:2] == ["k1", "k1"] and all(r["id"] and "project_root" in r for r in recs)


def test_outbox_reader_is_at_least_once_and_survives_rotation(tmp_path):
    f = tmp_path / "o.jsonl"
    f.write_bytes(b'{"id": "a"}\n{"id": "b"}\n{"id": "c"')            # the last line is half written
    recs, off, ino = alerts.read_from(str(f))
    assert [r["id"] for r in recs] == ["a", "b"]
    f.write_bytes(f.read_bytes() + b'}\n')
    recs, off2, _ = alerts.read_from(str(f), off, ino)
    assert [r["id"] for r in recs] == ["c"] and off2 > off
    os.unlink(f)
    f.write_bytes(b'{"id": "z"}\n')                                    # replaced file: read from the start
    assert [r["id"] for r in alerts.read_from(str(f), off2, ino)[0]] == ["z"]


# ----------------------------------------------------------------------------------------------- decisions and approvals with the real WP14b pieces
def test_a_decision_request_becomes_a_card_with_a_button_per_option_and_is_answered_once(ch, two):
    from research import decisions as D, notify
    from research.chat.gateway import Gateway
    a = two[0]
    with Project(a, sync=False, author={"author_type": "agent", "author_name": "investigator", "author_model": None}) as p:
        d = D.request(p, "Which way?", [{"key": "1", "label": "Keep going"}, {"key": "2", "label": "Change the plan", "protected": True}], subject="T-1")
    with Project(a, sync=False) as p:
        notify._scan(p, notify.cfg(p))
        assert notify._scan(p, notify.cfg(p)) is None
    g = Gateway({"fake": ch}, discover=False)
    assert asyncio.run(g.pump_once()) == 1 and len(ch.delivered) == 1, "one card per request, not one per pass"
    m = ch.delivered[0]
    assert [b.label for b in m.buttons] == ["1. Keep going", "2. Change the plan (on my computer)"] and d["id"] in m.text
    ch.say("/project use alpha", user="1")
    r = ch.click(m.buttons[0].token, user="1", name="alice", ref="c:1")
    assert r.toast == "Done" and "Noted" in r.messages[0].text and "answered by alice" in r.edit.text
    with Project(a, sync=False) as p:
        ans = D.load(p, d["id"])
        assert ans["status"] == "answered" and ans["answer"]["via"] == "chat:fake" and ans["answer"]["by"] == "fake:alice"
    assert "Already answered" in ch.click(m.buttons[1].token, user="1").toast, "answering in one place closes it everywhere"


def test_a_protected_option_never_runs_from_chat_and_flags_the_request(ch, two):
    from research import decisions as D
    a = two[0]
    with Project(a, sync=False, author={"author_type": "agent", "author_name": "x", "author_model": None}) as p:
        d = D.request(p, "Spend more?", [{"key": "1", "label": "More budget", "protected": True}], subject="T-2")
    tok = actions.issue(a, "op", "decision_answer", {"id": d["id"], "key": "1"}, "operator")
    r = ch.click(tok, user="1")
    assert "needs your PIN at the computer" in r.toast
    with Project(a, sync=False) as p:
        got = D.load(p, d["id"])
        assert got["status"] == "open" and got["requested_from_chat"]["via"] == "chat:fake", "nothing ran; the request waits for the PIN flow"
    assert actions.peek(tok)["used"] is None, "the button stays usable"


def test_the_approve_button_exists_only_when_chat_approval_is_enrolled_and_allowed(two, monkeypatch):
    from research import approval, supervisor as SV
    pr = {"id": "PROP-1", "kind": "experiment", "status": "pending", "rev": 1, "fields": {}, "rationale": "r"}
    monkeypatch.setattr(SV, "_prop_load", lambda p, pid: pr)
    with Project(two[0], sync=False) as p:
        assert commands.card_actions(p, "proposal", "PROP-1") == [], "not enrolled, not enabled: only the computer"
        monkeypatch.setattr(approval, "chat_status", lambda: {"chat_enrolled": True, "chat_disabled": False})
        from research import governance as G
        monkeypatch.setattr(G, "cfg", lambda p: {"approval": {"chat": {"enabled": True}, "chat_allow": []}})
        (btn,) = commands.card_actions(p, "proposal", "PROP-1")
        assert btn["kind"] == "ask_code" and btn["min_role"] == "approver" and btn["params"] == {"id": "PROP-1", "content_hash": approval.proposal_hash(pr)}
        pr["action"] = "frozen_test_edit"
        assert commands.card_actions(p, "proposal", "PROP-1") == [], "computer-only actions never get the button"
        pr.pop("action")
        monkeypatch.setattr(G, "cfg", lambda p: {"approval": {"chat": {"enabled": True}, "chat_allow": ["promotion"]}})
        assert commands.card_actions(p, "proposal", "PROP-1") == [], "config can only narrow what chat may approve"
    assert api.OPS["chat_approve_proposal"].chat == "approve"


def test_options_and_choose_use_the_decision_store_and_status_takes_a_project(ch, two):
    from research import decisions as D
    a, b = two
    with Project(a, sync=False, author={"author_type": "agent", "author_name": "x", "author_model": None}) as p:
        d = D.request(p, "Which way?", [{"key": "1", "label": "Keep going"}], subject="T-1")
    assert "No plan has been run" in ch.say("/status alpha", user="1", chat="Z").messages[0].text, "an explicit project needs no /project use"
    ch.say("/project use alpha", user="1")
    r = ch.say("/options", user="1").messages[0]
    assert d["id"] in r.text and [x.label for x in r.buttons] == ["1. Keep going"]
    assert "Noted" in ch.say("/choose 1", user="1").messages[0].text
    with Project(a, sync=False) as p:
        assert D.load(p, d["id"])["answer"]["via"] == "chat:fake"
