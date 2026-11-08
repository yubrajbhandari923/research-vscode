"""WP14b (H.5/H.8): the investigator, driven through the real provider machinery by a scripted fake binary (tests/python/fakes/investigator).
No model is called. A blocked task comes from real supervisor ticks."""
import json
import os
import sys

import pytest

from research import api, decisions as D, intervene as I, investigator as INV
from research.util import PolicyBlocked, ResearchError
from test_intervention import WRONG, blocked, start, ticks
from test_supervisor import lproj  # noqa: F401
from test_supervisor import phase

FAKE = os.path.join(os.path.dirname(__file__), "fakes", "investigator")
ADD = {"type": "add_task", "task": {"key": "extra", "title": "Extra diagnostic", "prompt": "print the environment", "agent": {"role": "implementer"}, "checks": ["file:env.txt"]}}


@pytest.fixture
def inv(lproj, tmp_path, monkeypatch):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    lproj.config["agents"]["profiles"]["inv"] = {"command": sys.executable, "args": [FAKE], "headless_args": ["--headless"], "usage_parser": "claude_json"}
    lproj.config["supervisor_assistant"] = {"profile": "inv"}
    log = tmp_path / "prompts.log"
    monkeypatch.setenv("FAKE_INVESTIGATOR_LOG", str(log))

    class H:
        p, tid = lproj, phase(lproj, "w")["id"]

        @staticmethod
        def reply(obj, cost=0.25):
            f = tmp_path / "reply.json"
            f.write_text(obj if isinstance(obj, str) else json.dumps(obj))
            monkeypatch.setenv("FAKE_INVESTIGATOR_REPLY", str(f))
            monkeypatch.setenv("FAKE_INVESTIGATOR_COST", str(cost))

        @staticmethod
        def runs():
            return open(log).read().count("=====") if os.path.exists(log) else 0
    return H


def dr(h, *opts):
    return D.request(h.p, "w is blocked: what now?", list(opts) or [{"key": "1", "label": "Leave it"}], subject=h.tid)


def test_free_text_becomes_tasks_runs_now_and_says_what_it_understood(inv):
    inv.reply({"understood": "You want a diagnostic of the environment first.", "summary": "Add a diagnostic task and retry with a hint.", "actions": [ADD, {"type": "guide", "text": "check env.txt"}, {"type": "retry"}]})
    d = dr(inv)
    r = D.answer(inv.p, d["id"], text="first dump the environment, then try again")
    assert r["status"] == "answered" and "Understood: You want a diagnostic" in r["outcome"] and "adding tasks extra" in r["outcome"] and "Doing now: INT-001" in r["outcome"]
    it = I.load(inv.p, "INT-001")
    assert it["state"] in ("approved", "implementing", "verifying") and {a["type"] for a in it["actions"]} == {"add_task", "guide", "retry"}
    assert INV.usage(inv.p) == {"runs": 1, "total_runs": 1, "spent_usd": 0.25} and inv.runs() == 1
    log = INV._load(inv.p)["runs"][0]
    assert log["decision"] == d["id"] and log["classes"] == ["implementation", "operational"] and log["interventions"] == ["INT-001"] and log["text"].startswith("first dump")


def test_protected_part_becomes_one_proposal_for_the_pin_and_resume_waits_with_it(inv):
    inv.reply({"understood": "The content check is wrong.", "summary": "Relax the check.", "actions": [{"type": "guide", "text": "just write out.txt"}, {"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}, {"type": "retry"}]})
    out = INV.investigate(inv.p, task=inv.tid, text="drop the content check")
    assert out["status"] == "done" and out["interventions"] == ["INT-001", "INT-002"]
    first, second = I.load(inv.p, "INT-001"), I.load(inv.p, "INT-002")
    assert [a["type"] for a in first["actions"]] == ["guide"] and first["state"] in ("approved", "implementing", "verifying")
    assert second["state"] == "proposed" and second["protected"] == ["scientific"] and [a["type"] for a in second["actions"]] == ["amend_task", "retry"]
    assert "Waiting for your PIN (scientific): INT-002" in out["summary"] and "research intervention approve INT-002" in out["summary"]
    assert "approval" not in second, "the investigator never approves"


def test_ambiguous_text_asks_one_clarifying_request_with_prepared_actions(inv):
    inv.reply({"understood": "unclear", "actions": [], "clarify": {"question": "Retry once, or skip this task?", "options": [
        {"label": "Retry once", "consequence": "one more attempt", "actions": [{"type": "retry"}]},
        {"label": "Relax the check", "consequence": "changes acceptance", "actions": [{"type": "amend_task", "fields": {"acceptance": "x"}}]}]}})
    d = dr(inv)
    r = D.answer(inv.p, d["id"], text="make it work")
    new = [x for x in D.list_all(inv.p) if x["created_by"] == "investigator"]
    assert len(new) == 1 and new[0]["question"] == "Retry once, or skip this task?" and [o["protected"] for o in new[0]["options"]] == [False, True]
    assert new[0]["id"] in r["outcome"] and not I.list_all(inv.p)
    inv.reply("{}")
    D.answer(inv.p, new[0]["id"], key="1")                                   # the prepared option runs with no further model call
    assert inv.runs() == 1 and I.load(inv.p, "INT-001")["actions"] == [{"type": "retry", "why": ""}]


def test_budget_exhausted_falls_back_to_a_plain_text_ask(inv):
    inv.p.config["supervisor"] = {**(inv.p.config.get("supervisor") or {}), "investigator": {"budget_usd": 0.2, "max_per_plan": 5}}
    inv.reply({"understood": "u", "summary": "s", "actions": [{"type": "note", "text": "noted"}]})
    assert INV.investigate(inv.p, task=inv.tid, question="why?")["status"] == "done"
    d = dr(inv)
    r = D.answer(inv.p, d["id"], text="try something else")
    assert r["needs_text"] and r["status"] == "open" and "budget is used up" in r["outcome"] and f"research supervisor instruct {inv.tid}" in r["outcome"]
    assert inv.runs() == 1 and D.load(inv.p, d["id"])["status"] == "open"           # no second agent run
    inv.p.config["supervisor"]["investigator"] = {"budget_usd": 50, "max_per_plan": 1}
    assert INV.investigate(inv.p, task=inv.tid)["status"] == "fallback" and "max_per_plan" in INV.investigate(inv.p, task=inv.tid)["summary"]


def test_unusable_answer_changes_nothing_and_unavailable_agent_is_plain_text(inv, monkeypatch):
    inv.reply("I think you should just rerun it.")
    out = INV.investigate(inv.p, task=inv.tid, question="why?")
    assert out["status"] == "failed" and not I.list_all(inv.p) and INV.usage(inv.p)["runs"] == 1
    inv.reply({"actions": [{"type": "rm -rf"}]})
    assert INV.investigate(inv.p, task=inv.tid)["status"] == "failed" and not I.list_all(inv.p)
    inv.p.config["supervisor_assistant"] = {"profile": "nonexistent"}
    out = INV.investigate(inv.p, task=inv.tid)
    assert out["status"] == "fallback" and "did not run" in out["summary"]


def test_agents_cannot_run_it_and_chat_policy(inv):
    inv.reply({"actions": [{"type": "note", "text": "x"}]})
    assert api.OPS["supervisor_investigate"].chat == "decide" and not api.OPS["supervisor_investigate"].agent
    inv.p.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    with pytest.raises(PolicyBlocked):
        INV.investigate(inv.p, task=inv.tid)
    assert inv.runs() == 0


def test_the_prompt_carries_the_task_and_the_instruction_and_auto_is_opt_in(inv):
    inv.reply({"understood": "u", "summary": "s", "actions": [{"type": "note", "text": "n"}]})
    assert INV.maybe_auto(inv.p, inv.tid, "fp") is None and inv.runs() == 0              # supervisor.investigator.auto defaults to false
    inv.p.config["supervisor"] = {**(inv.p.config.get("supervisor") or {}), "investigator": {"auto": True, "max_per_plan": 3, "budget_usd": 5}}
    assert INV.maybe_auto(inv.p, inv.tid, "fp")["status"] == "done"
    assert INV.maybe_auto(inv.p, inv.tid, "fp") is None and inv.runs() == 1             # once per task and fingerprint
    import re
    brief = open(re.search(r"brief at (\S+\.md)", open(os.environ["FAKE_INVESTIGATOR_LOG"]).read()).group(1)).read()
    assert inv.tid in brief and "diagnose it" in brief.lower() and "never approve" in brief.lower().replace("do not approve", "never approve")
