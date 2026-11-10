"""Jobs: a request in words is delegated, planned by a coordinator, carried out by the EXISTING supervisor (plans, worktrees, verification, promotion, self-healing), repaired
within bounds and only called done after independent verification. The coordinator agent is the one stand-in (`intervene.run_assistant`); everything else is real."""
import json
import os
import threading

import pytest

from research import intervene as I, jobs as JB, planspec as PS, registry, supervisor as SV, workspace as W
from research.api import chat as CH
from research.chat import alerts, commands
from research.util import PolicyBlocked, ResearchError
from test_auto_approve import begin, enable, paused_or
from test_autopromote import ap, head, install_reviewer, items, run_to, task  # noqa: F401  (fixtures and helpers)
from test_selfheal import CELL, EXP, FIX, case
from test_supervisor import run_until
from research import notify


class Coordinator:
    """Stands in for the coordinator agent: `reply` is what the model returns for the next request."""

    def __init__(self, monkeypatch, reply):
        self.reply, self.calls = reply, []
        monkeypatch.setattr(I, "run_assistant", self.run)

    def run(self, p, objective, prompt):
        self.calls.append(prompt)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def job_until(p, jid, states, timeout=240):
    assert run_until(p, lambda: JB.load(p, jid)["state"] in states, timeout), json.dumps(JB.load(p, jid), indent=1, default=str)[:3000]
    return JB.load(p, jid)


@pytest.fixture
def jp(ap, monkeypatch):
    ap.config["agents"]["default_profile"] = "fake"
    ap.save_config()
    monkeypatch.setattr(notify, "chat_enabled", lambda p: True)
    return ap


def sent(p, kind):
    return [json.loads(l) for l in open(alerts.outbox_path(p)) if f'"{kind}"' in l] if os.path.exists(alerts.outbox_path(p)) else []


STEPS = {"summary": "two files and a check", "acceptance": ["a.txt and b.txt exist"],
         "steps": [{"type": "task", "key": "mk-a", "title": "Make a", "prompt": "Work.\nMOCK_SH: echo a > a.txt", "checks": ["file:a.txt"]},
                   {"type": "task", "key": "mk-b", "title": "Make b", "prompt": "Work.\nMOCK_SH: echo b > b.txt", "depends_on": ["mk-a"], "checks": ["file:b.txt"]}]}


# ------------------------------------------------------------------------------------------------ the AP-002 scenario, in words
def test_a_natural_language_job_unblocks_a_paused_promotion_repairs_the_source_and_resumes(jp, repo, monkeypatch):
    enable(jp)
    registry.apply(jp, {"supervisor_assistant.repair_instructions": FIX})            # project convention for repairs; self_heal itself stays OFF
    pid = begin(jp, EXP)
    run_to(jp, pid, lambda rs: paused_or(jp, pid, "paused"), 90)
    assert "scientific content provenance" in items(jp, pid)[0]["error"] and not os.path.exists(os.path.join(repo, "chapter.qmd"))
    Coordinator(monkeypatch, {"summary": "repair AP-001", "acceptance": ["AP-001 is in main and the plan moves again"], "steps": [{"type": "heal_promotion", "plan": "ap", "item": "AP-001"}]})
    j = JB.create(jp, "The promotion AP-001 is stuck on an executable cell in the report. Fix the source, rerun the gates, and get it merged.", source="cli")
    assert j["state"] == "planning" and j["requested_by"] and j["budget"]["max_steps"] == 8
    j = job_until(jp, j["id"], ("done", "blocked"), 300)
    assert j["state"] == "done" and j["progress"] == "1/1" and j["steps"][0]["status"] == "done", j.get("blocked")
    assert j["steps"][0]["evidence"]["repair_task"] == "heal-ap-001-1" and j["steps"][0]["evidence"]["review"] == "reviewer", "done only with evidence: integrated, repaired by a task, independently reviewed"
    it = items(jp, pid)[0]
    assert it["status"] == "integrated" and "```text" in open(os.path.join(repo, "chapter.qmd")).read() and "{python}" not in open(os.path.join(repo, "chapter.qmd")).read()
    assert SV.load_run(jp, pid)["selfheal"]["jobs"] == {"AP-001": j["id"]}, "authorized by the job, not by the project-wide setting"
    assert SV.status(jp, "ap")["health"]["state"] == "completed", "downstream scheduling is free again"
    assert j["checkpoints"][0]["state"] == "planning" and "integrated" in j["checkpoints"][-1]["note"]
    assert any("is done" in a["text"] for a in sent(jp, "job")) and sent(jp, "job")[0]["text"].startswith(j["id"] + " started")


# ------------------------------------------------------------------------------------------------ a multi-step job through the existing supervisor
def test_a_task_job_runs_as_a_managed_plan_and_is_done_only_after_independent_verification(jp, repo, monkeypatch):
    Coordinator(monkeypatch, STEPS)
    j = JB.create(jp, "make a.txt then b.txt", source="cli")
    j = job_until(jp, j["id"], ("done", "blocked"))
    assert j["state"] == "done" and j["progress"] == "3/3" and [s["key"] for s in j["steps"]] == ["mk-a", "mk-b", "verify"]
    st = PS.load_state(jp, j["plan_key"])
    rs = SV.load_run(jp, st["plan_id"])
    ver = rs["tasks"][st["tasks"]["verify"]]
    assert ver["phase"] == "verified" and j["result"]["verified_by"] == st["tasks"]["verify"], "the last step is an independent verifier task"
    assert st["spec_tasks"]["verify"]["agent"]["role"] == "verifier" and st["spec_tasks"]["mk-a"]["agent"]["role"] == "implementer"
    t = jp.get("task", st["tasks"]["mk-a"])
    assert t["status"] == "done" and t.get("completed_by") == "supervisor", "verified by the supervisor like any task; nothing parallel"
    assert os.path.exists(os.path.join(W.list_workspaces(jp)[-1]["path"], "b.txt"))
    assert JB.logs(jp, j["id"])["steps"][0]["attempts"][0]["verified"] is True


# ------------------------------------------------------------------------------------------------ persistence, restart, concurrency
def test_a_restart_between_planning_and_materializing_resumes_from_the_checkpoint(jp, monkeypatch):
    co = Coordinator(monkeypatch, STEPS)
    j = JB.create(jp, "make a.txt then b.txt")
    j = job_until(jp, j["id"], ("running", "done"), 60)
    assert j["steps"] and any("coordinator planned 2 step" in c["note"] for c in j["checkpoints"])
    n_plans = len(PS.all_states(jp))
    from research.store import Project
    p2 = Project(jp.root)                                              # the daemon restarted: a new process, same files
    try:
        assert JB.load(p2, j["id"])["steps"] == j["steps"]
        JB.step(p2)
        JB.step(p2)
        assert len(PS.all_states(p2)) == n_plans and len(co.calls) == 1, "nothing is planned or created twice"
        assert job_until(p2, j["id"], ("done",), 240)["state"] == "done"
    finally:
        p2.close()
    # crash window: steps checkpointed, plan not yet created
    j2 = JB.create(jp, "again")
    rec = JB.load(jp, j2["id"])
    rec.update(steps=[dict(s, status="pending") for s in STEPS["steps"] if s["key"] == "mk-a"], acceptance=["x"], planner_started=None)
    rec["steps"][0].update(type="task", checks=["file:a.txt"], depends_on=[], role="implementer")
    JB.save(jp, rec, "planned (test: crash before materializing)")
    JB.step(jp)
    assert JB.load(jp, j2["id"])["state"] == "running" and JB.load(jp, j2["id"])["plan_key"]


def test_two_jobs_at_once_get_distinct_ids_and_both_complete(jp, monkeypatch):
    Coordinator(monkeypatch, {"summary": "one file", "acceptance": ["f"], "steps": [{"type": "task", "key": "mk", "title": "Make", "prompt": "Work.\nMOCK_SH: echo x > f.txt", "checks": ["file:f.txt"]}]})
    made = []
    ts = [threading.Thread(target=lambda: made.append(JB.create(jp, "make f.txt")["id"])) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(made) == ["JOB-001", "JOB-002", "JOB-003", "JOB-004"], "ids are allocated under a lock"
    assert run_until(jp, lambda: all(JB.load(jp, i)["state"] in ("done", "blocked") for i in made), 400)
    assert {JB.load(jp, i)["state"] for i in made} == {"done"} and len({JB.load(jp, i)["plan_key"] for i in made}) == 4, "each job is its own plan: no collisions"


# ------------------------------------------------------------------------------------------------ authorization boundaries
def test_only_a_human_delegates_and_the_coordinator_cannot_reach_protected_things(jp, monkeypatch):
    human, jp.author = jp.author, {"author_type": "agent", "author_name": "claude", "author_model": None}
    with pytest.raises(PolicyBlocked):
        JB.create(jp, "do it")
    jp.author = human
    for bad in ({"steps": [{"type": "amend_task", "fields": {"acceptance": "weaker"}}]}, {"steps": [{"type": "change_frozen_test", "path": "tests/x.py"}]},
                {"steps": [{"type": "task", "key": "k", "title": "t", "prompt": "p", "role": "reviewer-bot"}]}, {"steps": [{"type": "task", "key": "k", "title": "t", "prompt": "p", "depends_on": ["ghost"]}]}):
        with pytest.raises(ResearchError):
            JB.validate_plan(bad, 8)
    Coordinator(monkeypatch, {"summary": "needs a scientific decision", "steps": [], "needs_human": {"reason": "this would lower the acceptance threshold of T-004", "decision": "Do you accept a threshold of 0.8 instead of 0.9?"}})
    j = JB.create(jp, "make the failing test pass by loosening the threshold")
    j = job_until(jp, j["id"], ("blocked",), 60)
    assert "threshold" in j["blocked"]["reason"] and j["blocked"]["decision"].startswith("Do you accept") and not PS.all_states(jp), "nothing was created; the precise decision is stated"
    assert any("Smallest decision needed" in a["text"] for a in sent(jp, "halt"))


def test_a_job_with_nothing_checkable_is_blocked_not_declared_done(jp, monkeypatch):
    Coordinator(monkeypatch, {"summary": "vague", "acceptance": ["it works"], "steps": [{"type": "task", "key": "t", "title": "T", "prompt": "Work.\nMOCK_SH: true"}]})
    j = job_until(jp, JB.create(jp, "make it work")["id"], ("blocked", "done"), 60)
    assert j["state"] == "blocked" and "deterministic check" in j["blocked"]["reason"] and not PS.all_states(jp)


def test_an_unusable_coordinator_is_bounded_not_a_loop(jp, monkeypatch):
    co = Coordinator(monkeypatch, "not json at all")
    j = JB.create(jp, "anything")
    j = job_until(jp, j["id"], ("blocked",), 90)
    assert len(co.calls) == j["budget"]["max_planning_attempts"] and "could not produce a valid plan" in j["blocked"]["reason"]


# ------------------------------------------------------------------------------------------------ exhausted retries, cancel, never a false success
def test_a_step_that_cannot_pass_ends_blocked_with_the_precise_reason_and_never_claims_success(jp, monkeypatch):
    Coordinator(monkeypatch, {"summary": "impossible", "acceptance": ["z.txt exists"],
                              "steps": [{"type": "task", "key": "mk", "title": "Make z", "prompt": "Work.\nMOCK_SH: echo nothing", "checks": ["file:z.txt"]}]})
    j = JB.create(jp, "make z.txt")
    j = job_until(jp, j["id"], ("blocked", "done"), 300)
    assert j["state"] == "blocked" and "step mk" in j["blocked"]["reason"] and "attempt budget" in j["blocked"]["reason"] and j["blocked"]["decision"]
    assert j["steps"][-1]["status"] != "done" and j["result"] is None, "the verifier never ran, so success was never claimed"
    assert any("Smallest decision" in a["text"] for a in sent(jp, "halt"))


def test_cancel_stops_the_plan_and_keeps_the_history(jp, monkeypatch):
    Coordinator(monkeypatch, {"summary": "slow", "acceptance": ["s"], "steps": [{"type": "task", "key": "slow", "title": "Slow", "prompt": "Work.\nMOCK_SH: sleep 30; echo s > s.txt", "checks": ["file:s.txt"]}]})
    j = JB.create(jp, "slow thing")
    j = job_until(jp, j["id"], ("running",), 60)
    run_until(jp, lambda: False, 3)
    c = JB.cancel(jp, j["id"])
    assert c["state"] == "cancelled" and SV.status(jp, j["plan_key"])["state"] == "stopped" and JB.load(jp, j["id"])["checkpoints"][-1]["state"] == "cancelled"
    with pytest.raises(ResearchError, match="only a running job"):
        JB.cancel(jp, j["id"])


# ------------------------------------------------------------------------------------------------ /do in chat
def test_do_and_jobs_are_chat_commands_for_operators_only(jp, monkeypatch):
    assert commands.COMMANDS["do"][0] == "chat_cmd_do"
    assert commands.COMMANDS["jobs"][0] == "chat_cmd_jobs"
    Coordinator(monkeypatch, STEPS)
    r = CH.chat_cmd_do(jp, "make a.txt then b.txt", via="discord")
    assert "JOB-001 started" in str(r) and JB.load(jp, "JOB-001")["source"] == "discord"
    assert "JOB-001" in str(CH.chat_cmd_jobs(jp))
