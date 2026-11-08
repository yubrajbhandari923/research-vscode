"""WP14b (H.7/H.8): decision requests. Agents ask, humans answer once from any surface; option = prepared action, no model call;
protected options never run from chat. Real supervisor ticks on a blocked task; the planner is the same scripted stand-in as test_intervention."""
import json
import os
import subprocess
import sys

import pytest

from research import api, approval as A, decisions as D, intervene as I, supervisor as SV
from research.util import PolicyBlocked, ResearchError
from test_intervention import WRONG, blocked, start, stub, ticks  # noqa: F401  (stub is a fixture)
from test_supervisor import lproj  # noqa: F401
from test_supervisor import phase

GUIDE = {"kind": "intervention", "instruction": "try again with a hint", "actions": [{"type": "guide", "text": "write out.txt containing the word right"}, {"type": "retry"}]}
FROZEN = {"kind": "intervention", "instruction": "fix the test", "actions": [{"type": "change_frozen_test", "path": "tests/x.py", "reason": "the assertion is wrong, see log"}]}


@pytest.fixture
def halted(lproj, stub):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    tid = phase(lproj, "w")["id"]
    return lproj, tid, stub


def opts(tid, **kw):
    return [{"key": "a", "label": "Retry with a hint", "consequence": "one more attempt", "action": {**GUIDE, "task": tid}},
            {"key": "b", "label": "Allow editing the frozen test", "consequence": "changes a frozen test", "action": {**FROZEN, "task": tid}},
            {"key": "c", "label": "Leave it", **kw}]


def test_validation_computes_protected_and_refuses_unknown_ops(lproj):
    assert D.validate_action({"kind": "op", "op": "plan_control", "params": {"plan": "fx", "action": "pause"}})["protected"] is False
    assert D.validate_action({"kind": "op", "op": "plan_control", "params": {"plan": "fx", "action": "stop"}})["protected"] is True
    assert D.validate_action({"kind": "op", "op": "plan_promote", "params": {"plan": "fx"}})["protected"] is True
    for bad in ({"kind": "op", "op": "supervisor_task", "params": {"task": "T-1", "action": "accept"}}, {"kind": "op", "op": "approval_set_pin", "params": {}},
                {"kind": "shell", "cmd": "rm -rf /"}, {"kind": "intervention", "task": "T-1", "actions": [{"type": "rm"}]}):
        with pytest.raises(ResearchError):
            D.validate_action(bad)
    amend = D.validate_action({"kind": "intervention", "task": "T-1", "actions": [{"type": "amend_task", "fields": {"resources": {"cpus": 64}}}]})
    assert amend["protected"] is True          # nested resources are a resource change, not "implementation"


def test_agent_can_ask_but_not_answer_and_cannot_unprotect(halted):
    p, tid, _ = halted
    p.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    d = api.call("decision_request", api.Session(p.root, p.author), {"question": "Which way?", "subject": tid, "options": [{"key": "1", "label": "Frozen", "protected": False, "action": {**FROZEN, "task": tid}}]}, surface="mcp")
    assert d["created_by"] == "agent:claude" and d["options"][0]["protected"] is True and d["options"][0]["effect"]
    with pytest.raises(PolicyBlocked):
        D.answer(p, d["id"], key="1")
    assert "decision_answer" not in {o.name for o in api.OPS.values() if o.agent} and "decision_request" in {o.mcp for o in api.OPS.values()}
    p.author = {"author_type": "human"}
    assert D.load(p, d["id"])["status"] == "open"


def test_option_runs_its_prepared_action_once_and_closes_everywhere(halted):
    p, tid, stub = halted
    d = D.request(p, "w is blocked: what now?", opts(tid), subject=tid)
    assert D.request(p, "w is blocked: what now?", opts(tid), subject=tid)["id"] == d["id"]      # deduplicated
    r = D.answer(p, d["id"], key="a", via="tui")
    assert not stub.calls, "a prepared action must not call a model"
    assert r["status"] == "answered" and r["answer"]["via"] == "tui" and "INT-001" in r["outcome"]
    it = I.load(p, "INT-001")
    assert it["state"] in ("approved", "implementing", "verifying") and [a["type"] for a in it["actions"]] == ["guide", "retry"]
    again = D.answer(p, d["id"], key="c", via="chat:telegram")                                     # a second surface: same answer back, nothing runs
    assert again["already"] is True and again["answer"]["key"] == "a" and len(I.list_all(p)) == 1
    assert D.list_all(p) == [] and D.list_all(p, "answered")[0]["id"] == d["id"]
    assert "decision_answered" in open(os.path.join(p.rdir, "events.jsonl")).read()


def test_protected_option_never_runs_from_chat_then_needs_the_pin_locally(halted):
    p, tid, _ = halted
    A.set_pin("4321")
    d = D.request(p, "frozen?", opts(tid), subject=tid)
    r = D.answer(p, d["id"], key="b", via="chat:discord")
    assert r["needs_local"] and D.load(p, d["id"])["status"] == "open" and D.load(p, d["id"])["requested_from_chat"]["via"] == "chat:discord" and not I.list_all(p)
    with pytest.raises(PolicyBlocked):
        D.answer(p, d["id"], key="b", note="ok", pin="0000")
    assert D.load(p, d["id"])["status"] == "open"                                                  # a failed attempt reopens it
    r = D.answer(p, d["id"], key="b", note="the assertion really is wrong", pin="4321")
    assert r["status"] == "answered" and [i["state"] for i in I.list_all(p)] == ["cancelled", I.list_all(p)[-1]["state"]] and I.list_all(p)[-1]["state"] in ("approved", "implementing", "verifying", "failed", "resolved")
    assert "4321" not in open(os.path.join(p.rdir, "events.jsonl")).read()


def test_expiry_closes_without_choosing(halted):
    p, tid, _ = halted
    d = D.request(p, "old", opts(tid), subject=tid, expires_hours=-1)
    assert D.list_all(p) == [] and D.load(p, d["id"])["status"] == "expired"
    with pytest.raises(ResearchError, match="expired"):
        D.answer(p, d["id"], key="a")
    d2 = D.request(p, "new", opts(tid), subject=tid)
    assert D.expire(p, d2["id"])[0]["status"] == "expired" and not I.list_all(p)


def test_text_and_key_are_mutually_exclusive(halted):
    p, tid, _ = halted
    with pytest.raises(ResearchError, match="OR free text"):
        D.answer(p, D.request(p, "q2", opts(tid), subject=tid)["id"], key="a", text="both")


def test_diagnosis_options_become_a_request_and_choose_is_an_alias(halted):
    p, tid, stub = halted
    stub.reply = {"summary": "ok", "actions": [{"type": "unblock"}]}
    rec = {"state": "done", "plan": "fx", "tasks": [tid], "fingerprint": "fp1", "summary": "w fails its check", "recommended": 0,
           "options": [{"label": "Retry", "instruction": "retry once", "class": "operational"}, {"label": "Relax check", "instruction": "drop the check", "class": "scientific"}]}
    d = D.from_diagnosis(p, rec)
    assert D.from_diagnosis(p, rec)["id"] == d["id"] and [o["protected"] for o in d["options"]] == [False, True] and d["default"] == "1"
    assert "1. Retry" in D.text_for(d) and "[needs your computer]" in D.text_for(d)
    assert D.choose(p, 2, via="chat:telegram")["needs_local"]
    assert D.choose(p, 1, via="chat:telegram")["status"] == "answered"


def test_cli_lists_and_shows(halted):
    p, tid, _ = halted
    d = D.request(p, "cli question", opts(tid), subject=tid)
    run = lambda *a: subprocess.run([sys.executable, "-m", "research_cli.main", "--root", p.root, *a], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    out = run("decisions", "--json")
    assert out.returncode == 0 and json.loads(out.stdout)[0]["id"] == d["id"]
    shown = run("decide", d["id"])
    assert "Retry with a hint" in shown.stdout and "needs PIN" in shown.stdout
