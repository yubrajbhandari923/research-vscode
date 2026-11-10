"""`research task intervene`: deterministic human controls and Human Takeover. Real supervisor ticks, git and a scripted agent; no model is involved."""
import argparse
import os
import subprocess

import pytest

from research import approval as A, intervene as I, planspec as PS, supervisor as SV, takeover as TK, workspace as W
from research.util import ResearchError
from test_intervention import WRONG, blocked, start, ticks  # noqa: F401
from test_supervisor import lproj, phase, plan, run_attempts, t  # noqa: F401  (lproj is a fixture)

PIN = "4321"


@pytest.fixture
def stuck(lproj):
    A.set_pin(PIN)
    start(lproj, WRONG)                                           # one attempt allowed: it fails and the task blocks, budget exhausted
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    return lproj, phase(lproj, "w")["id"]


def snapshot(p):
    return {k: [(a["n"], a.get("execution"), a.get("state"), a.get("voided")) for a in v] for k, v in run_attempts(p).items()}


def action(m, id_):
    return next(x for x in m["actions"] if x["id"] == id_)


# ------------------------------------------------------------------------------------------------ exhausted budget
def test_the_menu_shows_state_attempts_agent_and_why_retry_is_not_offered(stuck):
    p, tid = stuck
    before = snapshot(p)
    m = TK.menu(p, tid)
    assert m["phase"] == "blocked" and m["attempts"] == {"used": 1, "allowed": 1, "voided": 0, "exhausted": True} and m["agent"]["profile"] == "fake"
    assert m["plan_budget"]["used"] == 1 and m["worktree"] and os.path.isdir(m["worktree"]) and m["reason"]
    r = action(m, "retry")
    assert not r["available"] and "attempt budget is exhausted" in r["why_not"]
    assert action(m, "attempts")["available"] and action(m, "attempts")["needs_pin"] and action(m, "takeover")["available"] and not action(m, "release")["available"]
    assert snapshot(p) == before and phase(p, "w")["phase"] == "blocked", "opening the menu changes nothing"
    with pytest.raises(ResearchError, match="not available"):
        TK.run(p, tid, "retry")


def test_raising_the_budget_past_ten_is_previewed_recorded_pin_approved_and_keeps_every_attempt(stuck):
    p, tid = stuck
    before = snapshot(p)
    pv = TK.preview(p, tid, "attempts", 12)
    assert pv["available"] and pv["needs_pin"] and any("1 -> 12" in l and "11 left" in l for l in pv["lines"]) and any("nothing is reset or voided" in l for l in pv["lines"])
    assert TK.menu(p, tid)["attempts"]["allowed"] == 1, "a preview applies nothing"
    with pytest.raises(Exception):
        TK.run(p, tid, "attempts", 12, pin="0000")                # a resource change still needs the PIN
    assert TK.menu(p, tid)["attempts"]["allowed"] == 1
    r = TK.run(p, tid, "attempts", 12, pin=PIN)
    assert r["state"] == "resolved"
    m = TK.menu(p, tid)
    assert m["attempts"]["allowed"] == 12 and not m["attempts"]["exhausted"] and snapshot(p) == before, "attempt history is exactly as it was"
    assert action(m, "retry")["available"]
    it = I.load(p, r["intervention"])
    assert it["actions"] == [{"type": "set_max_attempts", "value": 12, "why": ""}] and it["protected"] == ["resource"] and it["approval"]["via"].startswith("human approval with PIN")
    assert any("budget" in x["statement"] or "amended" in x["statement"].lower() for x in p.list("decision"))
    TK.run(p, tid, "retry")
    assert ticks(p, lambda: len(run_attempts(p)["w"]) == 2, 60), "the extra attempt really starts"


def test_the_plan_budget_can_be_raised_and_must_be_a_positive_number(stuck):
    p, tid = stuck
    with pytest.raises(ResearchError, match="positive|whole number"):
        TK.preview(p, tid, "plan-budget", 0)
    assert TK.preview(p, tid, "plan-budget", 1)["available"], "equal to what is used is allowed"
    raised = TK.run(p, tid, "plan-budget", 400, pin=PIN)
    assert raised["state"] == "resolved" and TK.menu(p, tid)["plan_budget"]["max"] == 400


# ------------------------------------------------------------------------------------------------ running agents
SLOW = [("e1", [t("s", "implementer", "sleep 5; echo ok > s.txt", checks=["file:s.txt"], attempts=2)])]


def test_a_running_agent_is_never_stopped_by_the_menu_a_budget_change_or_takeover(lproj):
    A.set_pin(PIN)
    start(lproj, SLOW)
    assert ticks(lproj, lambda: phase(lproj, "s")["phase"] == "running" and (run_attempts(lproj)["s"][-1].get("execution")), 30)
    tid = phase(lproj, "s")["id"]
    m = TK.menu(lproj, tid)
    assert m["running"] and m["agent"]["running"] and not action(m, "retry")["available"] and "agent is running" in action(m, "retry")["why_not"]
    pv = TK.preview(lproj, tid, "attempts", 5)
    assert any("is NOT affected" in l for l in pv["lines"])
    TK.run(lproj, tid, "attempts", 5, pin=PIN)                    # applied now, while the agent runs
    assert phase(lproj, "s")["phase"] == "running" and TK.menu(lproj, tid)["attempts"]["allowed"] == 5
    r = TK.run(lproj, tid, "takeover")
    assert r["agent_still_running"] is True and phase(lproj, "s")["phase"] == "running", "taking over does not terminate the agent"
    assert ticks(lproj, lambda: phase(lproj, "s")["phase"] == "verified", 60), "it finishes and is verified normally"
    assert len(run_attempts(lproj)["s"]) == 1 and TK.menu(lproj, tid)["takeover"] is None


# ------------------------------------------------------------------------------------------------ takeover and release
def test_takeover_suspends_scheduling_then_release_verifies_the_persons_work_normally(stuck):
    p, tid = stuck
    before = snapshot(p)
    r = TK.run(p, tid, "takeover", note="fixing the output by hand")
    assert r["worktree"] and not r["agent_still_running"]
    assert phase(p, "w")["phase"] == "takeover"
    h = SV.status(p, "fx")["health"]
    assert h["state"] == "halted" and h["blocking"][0]["phase"] == "takeover", "the plan says it is waiting for a person"
    ticks(p, lambda: False, 4)
    assert snapshot(p) == before, "nothing is launched, repaired or escalated while a person works"
    assert not action(TK.menu(p, tid), "retry")["available"] and "taken over" in action(TK.menu(p, tid), "retry")["why_not"]
    wt = r["worktree"]
    open(os.path.join(wt, "out.txt"), "w").write("right\n")      # the person's own change satisfies the declared checks (file + content)
    TK.run(p, tid, "release", note="done by hand")
    assert phase(p, "w")["phase"] == "verifying"
    assert ticks(p, lambda: phase(p, "w")["phase"] == "verified", 60)
    after = snapshot(p)
    assert [x[:2] for x in after["w"]] == [x[:2] for x in before["w"]], "no new attempt, no new execution: the existing attempt was re-verified"
    a = run_attempts(p)["w"][0]
    assert a["takeover"]["by"] and a["takeover"]["released"] and a["verification"]["passed"] is True and a["verification_history"][0]["passed"] is False, "the failed result stays as evidence"
    assert a.get("commit") and "out.txt" in subprocess.run(["git", "show", "--stat", "--format=", a["commit"]], cwd=wt, capture_output=True, text=True).stdout, \
        "the person's change is committed on the experiment branch as verified work, through the normal commit path"
    assert TK.menu(p, tid)["takeover"] is None


def test_release_returns_to_normal_budgets_when_the_persons_work_still_fails(stuck):
    p, tid = stuck
    TK.run(p, tid, "takeover")
    TK.run(p, tid, "release")                                    # nothing was fixed
    assert ticks(p, lambda: phase(p, "w")["phase"] == "blocked", 60), "the supervisor's normal rules apply again (budget exhausted: blocked)"
    assert len(run_attempts(p)["w"]) == 1


def test_a_second_takeover_is_refused_and_release_without_takeover_is_refused(stuck):
    p, tid = stuck
    TK.run(p, tid, "takeover")
    with pytest.raises(ResearchError, match="already taken over"):
        TK.run(p, tid, "takeover")                                # the menu does not offer it, and it is refused
    assert TK.start_takeover(p, tid)["already"] is True           # called directly it is idempotent
    TK.release(p, tid)
    with pytest.raises(ResearchError, match="not under takeover"):
        TK.release(p, tid)


# ------------------------------------------------------------------------------------------------ paused plans, agents, the CLI
def test_a_paused_plan_says_so_and_takeover_works_on_it(stuck):
    p, tid = stuck
    SV.plan_pause(p, "fx")
    m = TK.menu(p, tid)
    assert m["plan_state"] == "paused" and not action(m, "pause")["available"] and "paused" in action(m, "pause")["why_not"]
    assert any("nothing starts until" in l for l in TK.preview(p, tid, "attempts", 3)["lines"])
    assert TK.run(p, tid, "takeover")["worktree"] and TK.menu(p, tid)["takeover"]
    TK.release(p, tid)


def test_agents_cannot_use_the_controls(stuck):
    p, tid = stuck
    human, p.author = p.author, {"author_type": "agent", "author_name": "claude", "author_model": None}
    try:
        for act in ("takeover", "attempts"):
            with pytest.raises(Exception):
                TK.run(p, tid, act, 3, pin=PIN)
    finally:
        p.author = human


def test_quick_cli_budget_for_stuck_and_not_started_tasks(lproj, capsys):
    from research_cli.commands import plans as CP
    A.set_pin(PIN)
    start(lproj, [("e1", [t("w", "implementer", "echo wrong > out.txt", checks=["file:out.txt", "cmd:grep -q right out.txt"], attempts=1),
                          t("later", "implementer", "echo ok > later.txt", deps=["w"], checks=["file:later.txt"], attempts=2)])])
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stuck_ids, todo_ids = TK.select(lproj, "fx", stuck=True), TK.select(lproj, "fx", todo=True)
    assert stuck_ids == [phase(lproj, "w")["id"]] and todo_ids == [phase(lproj, "later")["id"]]
    ns = lambda **k: argparse.Namespace(**{"ids": [], "stuck": False, "todo": False, "plan": "fx", "yes": False, "note": None, "json": False, "max_attempts": 12, **k})
    CP.cmd_task_budget(ns(stuck=True, todo=True), lproj)
    out = capsys.readouterr().out
    assert "budget 1 -> 12" in out and "budget 2 -> 12" in out and "preview only" in out
    assert TK.menu(lproj, stuck_ids[0])["attempts"]["allowed"] == 1, "no --yes: nothing changed"
    rows = TK.set_attempts(lproj, stuck_ids + todo_ids, 12, "future runs need more room", PIN, apply=True)
    assert [r["state"] for r in rows] == ["resolved", "resolved"]
    assert [TK.menu(lproj, i)["attempts"]["allowed"] for i in stuck_ids + todo_ids] == [12, 12]
    with pytest.raises(ResearchError, match="name tasks"):
        CP.cmd_task_budget(ns(), lproj)


def test_cli_menu_prints_everything_the_researcher_needs(stuck, capsys):
    from research_cli.commands import plans as CP
    p, tid = stuck
    CP.cmd_task_intervene(argparse.Namespace(id=tid, action=None, value=None, yes=False, note=None, stop_agent=False, json=False), p)
    out = capsys.readouterr().out
    assert tid in out and "blocked" in out and "attempts: 1 of 1" in out and "EXHAUSTED" in out and "agent: fake" in out and "worktree:" in out and "takeover" in out and "(PIN)" in out
    CP.cmd_task_intervene(argparse.Namespace(id=tid, action="attempts", value="12", yes=False, note=None, stop_agent=False, json=False), p)
    out = capsys.readouterr().out
    assert "1 -> 12" in out and "--yes" in out and TK.menu(p, tid)["attempts"]["allowed"] == 1
