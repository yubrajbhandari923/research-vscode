"""WP14b (H.9): TOTP (RFC 6238) and chat approval. A fake clock (`at`) drives every time-dependent case; no chat platform is involved."""
import base64
import json
import os

import pytest

from research import approval as AP, supervisor as SV, totp
from research.util import PolicyBlocked, ResearchError

RFC = base64.b32encode(b"12345678901234567890").decode()
T0 = 1_700_000_000.0


def test_rfc6238_vectors_and_window():
    assert totp.code(RFC, 59) == "287082" and totp.code(RFC, 1111111109) == "081804"
    c = totp.code(RFC, T0)
    assert totp.verify(RFC, c, T0 + 30) and totp.verify(RFC, c, T0 - 30) and totp.verify(RFC, c, T0 + 60) is None
    assert totp.verify(RFC, c, T0, after_step=totp.step_of(T0)) is None   # used steps never match
    assert totp.uri(RFC, "me").startswith("otpauth://totp/") and f"secret={RFC}" in totp.uri(RFC, "me")


@pytest.fixture
def chat(proj):
    AP.set_pin("1234")
    shown = {}
    AP.chat_enroll("1234", lambda: totp.code(shown["s"], T0), lambda uri, s: shown.update(s=s), at=T0)
    proj.config["governance"] = {"approval": {"chat": {"enabled": True}}}
    proj.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    prs = [SV.propose_experiment(proj, {"title": "E", "hypothesis": "h", "rationale": "r", "method": "m", "expected_outputs": "o", "related": "EXP-1"}) for _ in range(3)]
    proj.author = {"author_type": "human"}
    return proj, shown["s"], prs


def go(p, secret, pr, at=T0, code=None, **kw):
    pr = SV._prop_load(p, pr["id"])
    return AP.confirm_chat(p, pr, kw.pop("h", AP.proposal_hash(pr)), code or totp.code(secret, at), "telegram", "42", at=at, **kw)


def test_valid_code_once_replay_refused_adjacent_steps_ok_two_steps_refused(chat):
    p, s, (a, b, c) = chat
    assert go(p, s, a)["method"] == "totp-chat"
    with pytest.raises(PolicyBlocked, match="already used"):
        go(p, s, b)                                   # same code again
    assert go(p, s, b, at=T0 + 30)                    # next step
    with pytest.raises(PolicyBlocked):
        go(p, s, c, at=T0 + 30, code=totp.code(s, T0 + 150))   # five steps away
    assert go(p, s, c, at=T0 + 60, code=totp.code(s, T0 + 90))  # one step ahead of the clock


def test_two_steps_away_refused_on_a_fresh_secret(chat):
    p, s, (a, *_) = chat
    with pytest.raises(PolicyBlocked):
        go(p, s, a, code=totp.code(s, T0 + 60))


def test_lockout_is_shared_with_the_pin_and_disables_chat_until_a_pin_login(chat):
    p, s, (a, *_) = chat
    for _ in range(2):
        with pytest.raises(PolicyBlocked):
            AP.verify("0000")
    for _ in range(3):
        with pytest.raises(PolicyBlocked, match="Incorrect"):
            go(p, s, a, code="000000")
    with pytest.raises(PolicyBlocked, match="disabled"):
        go(p, s, a)                                   # disabled even with the right code
    with pytest.raises(PolicyBlocked, match="Too many wrong"):
        AP.verify("1234")                             # the shared 60 s lock also holds the PIN
    d = AP._load()
    d["locked_until"] = 0
    AP._save(d)
    AP.verify("1234")                                 # local PIN login
    assert go(p, s, a)["approved"]


def test_unenrolled_off_forbidden_stale_role_and_allow_list(chat):
    p, s, (a, b, c) = chat
    with pytest.raises(PolicyBlocked, match="approver role"):
        go(p, s, a, role="operator")
    pr = SV._prop_load(p, a["id"])
    with pytest.raises(PolicyBlocked, match="changed since"):
        go(p, s, a, h="deadbeef")
    p.config["governance"]["approval"]["chat_allow"] = ["retry"]
    with pytest.raises(PolicyBlocked, match="chat_allow"):
        go(p, s, a)
    p.config["governance"]["approval"]["chat_allow"] = []
    plan_edit = {**pr, "kind": "plan", "operations": ["create_task", "update_task"], "id": "PROP-9"}
    with pytest.raises(PolicyBlocked, match="needs your computer"):
        AP.confirm_chat(p, plan_edit, AP.proposal_hash(plan_edit), totp.code(s, T0), "telegram", "42", at=T0)
    for tag in AP.CHAT_FORBIDDEN:
        with pytest.raises(PolicyBlocked, match="needs your computer"):
            claimed = {**pr, "action": tag}
            AP.confirm_chat(p, claimed, AP.proposal_hash(claimed), totp.code(s, T0), "telegram", "42", at=T0)
    p.config["governance"]["approval"]["chat"]["enabled"] = False
    with pytest.raises(PolicyBlocked, match="off"):
        go(p, s, a)
    p.config["governance"]["approval"]["chat"]["enabled"] = True
    AP.chat_revoke("1234")
    with pytest.raises(PolicyBlocked, match="No authenticator"):
        go(p, s, a)


def test_changed_proposal_invalidates_the_card(chat):
    p, s, (a, *_) = chat
    h = AP.proposal_hash(SV._prop_load(p, a["id"]))
    pr = SV._prop_load(p, a["id"])
    pr["status"] = "revision_requested"
    SV._prop_save(p, pr)
    pr.update(status="pending", rev=2)
    SV._prop_save(p, pr)
    with pytest.raises(PolicyBlocked, match="changed since"):
        AP.approve_proposal_from_chat(p, a["id"], h, totp.code(s, T0), "telegram", "42", at=T0)


def test_approve_from_chat_applies_once_and_records_totp_chat(chat):
    p, s, (a, *_) = chat
    h = AP.proposal_hash(SV._prop_load(p, a["id"]))
    out = AP.approve_proposal_from_chat(p, a["id"], h, totp.code(s, T0), "discord", "7", at=T0)
    assert out["status"] == "approved" and out["approved_via"]["method"] == "totp-chat" and len(p.q("SELECT id FROM experiments")) == 1
    with pytest.raises(PolicyBlocked, match="already approved"):
        AP.approve_proposal_from_chat(p, a["id"], h, totp.code(s, T0 + 30), "discord", "7", at=T0 + 30)
    ev = open(os.path.join(p.rdir, "events.jsonl")).read()
    assert "method=totp-chat platform=discord user=7 role=approver" in ev


def test_secret_and_codes_never_in_logs_events_or_project(chat):
    p, s, (a, *_) = chat
    code = totp.code(s, T0)
    go(p, s, a)
    for root, _, files in os.walk(p.root):
        for f in files:
            try:
                t = open(os.path.join(root, f), errors="ignore").read()
            except OSError:
                continue
            assert s not in t and f'"{code}"' not in t and f" {code} " not in t, os.path.join(root, f)
    assert "secret" not in json.dumps(AP.status())


def test_agent_environment_cannot_enroll_or_read(chat, monkeypatch):
    p, s, (a, *_) = chat
    monkeypatch.setenv("RESEARCH_AGENT", "claude")
    with pytest.raises(PolicyBlocked):
        AP.chat_enroll("1234", lambda: "", lambda *x: None)
    with pytest.raises(PolicyBlocked):
        AP.chat_revoke("1234")
    with pytest.raises(PolicyBlocked):
        AP._totp_secret()
    with pytest.raises(PolicyBlocked):
        go(p, s, a)


def test_wrong_enrollment_code_stores_nothing(proj):
    AP.set_pin("1234")
    with pytest.raises(PolicyBlocked):
        AP.chat_enroll("1234", lambda: "000000", lambda *x: None, at=T0)
    assert not AP.chat_status()["chat_enrolled"]
    with pytest.raises(PolicyBlocked):
        AP.chat_enroll("9999", lambda: "", lambda *x: None)   # PIN required


def test_changing_the_pin_keeps_the_authenticator(chat):
    AP.set_pin("5678", "1234")
    assert AP.chat_status()["chat_enrolled"]
