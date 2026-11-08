"""Governance: agents propose, humans approve (with a PIN), and every creation path is enforced."""
import os
import textwrap

import pytest

from research import approval as AP, governance as G, plans as P, services, supervisor as SV
from research.store import Project
from research.util import PolicyBlocked, ResearchError


@pytest.fixture
def agent(proj):
    proj.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    return proj


def fields(**kw):
    return {"title": "E", "hypothesis": "h", "rationale": "r", "method": "m", "expected_outputs": "o", "related": "EXP-1", **kw}


def test_agents_cannot_create_experiments_but_humans_can(proj, agent):
    with pytest.raises(PolicyBlocked):
        services.create_experiment(agent, "x")
    agent.author = {"author_type": "human"}
    assert services.create_experiment(agent, "x")["id"]


def test_agent_experiment_proposal_needs_every_field_and_a_pin_to_approve(proj, agent, monkeypatch):
    with pytest.raises(ResearchError):
        SV.propose_experiment(agent, fields(method=""))
    pr = SV.propose_experiment(agent, fields())
    assert pr["status"] == "pending" and not proj.q("SELECT id FROM experiments")
    AP.set_pin("1234")
    with pytest.raises(PolicyBlocked):                      # the agent cannot approve, even claiming to be human via the env marker
        SV.decide_proposal(agent, pr["id"], True, "ok", pin="1234")
    proj.author = {"author_type": "human"}
    with pytest.raises(PolicyBlocked):
        SV.decide_proposal(proj, pr["id"], True, "ok")      # no PIN and no terminal: fails closed
    with pytest.raises(PolicyBlocked):
        SV.decide_proposal(proj, pr["id"], True, "ok", pin="0000")
    with pytest.raises(ResearchError):
        SV.decide_proposal(proj, pr["id"], True, "", pin="1234")   # rationale required
    out = SV.decide_proposal(proj, pr["id"], True, "looks right", pin="1234", rev=1)
    assert out["status"] == "approved" and len(proj.q("SELECT id FROM experiments")) == 1
    with pytest.raises(ResearchError):
        SV.decide_proposal(proj, pr["id"], True, "again", pin="1234")   # exactly once
    assert "1234" not in open(os.path.join(proj.rdir, "events.jsonl")).read()


def test_reject_and_revision_flow_and_stale_revision(proj, agent):
    pr = SV.propose_experiment(agent, fields())
    proj.author = {"author_type": "human"}
    with pytest.raises(ResearchError):
        SV.decide_proposal(proj, pr["id"], "revise", "", pin=None)
    r = SV.decide_proposal(proj, pr["id"], "revise", "tighten the method")
    assert r["status"] == "revision_requested"
    agent.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    r2 = SV.revise_proposal(agent, pr["id"], fields={"method": "better"})
    assert r2["status"] == "pending" and r2["rev"] == 2
    proj.author = {"author_type": "human"}
    with pytest.raises(ResearchError):
        SV.decide_proposal(proj, pr["id"], False, "no", rev=1)     # stale
    assert SV.decide_proposal(proj, pr["id"], False, "no", rev=2)["status"] == "rejected"
    with pytest.raises(ResearchError):
        SV.decide_proposal(proj, pr["id"], True, "late", pin=None)  # rejected work never becomes executable
    assert not proj.q("SELECT id FROM experiments")


def test_task_policies(proj, agent):
    proj.author = {"author_type": "human"}
    exp = services.create_experiment(proj, "e")["id"]
    plan = P.create_plan(proj, "p")["id"]
    proj.author = agent.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    t = lambda **kw: P.create_task(agent, plan, "t", **kw)
    with pytest.raises(PolicyBlocked):
        t(task_type="implementation", related_experiment_id=exp)       # scientific/not an allowed type
    with pytest.raises(PolicyBlocked):
        t(task_type="bugfix")                                          # no experiment
    for _ in range(3):
        t(task_type="bugfix", related_experiment_id=exp)
    with pytest.raises(PolicyBlocked):
        t(task_type="bugfix", related_experiment_id=exp)               # cap of 3
    proj.config.setdefault("governance", {})["tasks"] = {"agent_creation": "propose_only"}
    with pytest.raises(PolicyBlocked):
        t(task_type="bugfix", related_experiment_id=services.create_experiment(
            Project(proj.root, author={"author_type": "human"}), "e2")["id"])
    proj.config["governance"]["tasks"] = {"agent_creation": "human_only"}
    with pytest.raises(PolicyBlocked):
        SV.propose(agent, None, "schema_version: 1\nplans: []\n", "x")


def test_experiment_override_can_only_tighten(proj):
    proj.config["governance"] = {"tasks": {"agent_creation": "propose_only"}, "experiment_overrides": {"EXP-0001": {"task_creation": "bounded_auto"}}}
    assert G.task_policy(proj, "EXP-0001") == "propose_only"
    proj.config["governance"] = {"experiment_overrides": {"EXP-0001": {"task_creation": "human_only"}}}
    assert G.task_policy(proj, "EXP-0001") == "human_only" and G.task_policy(proj, "EXP-0002") == "bounded_auto"


def test_pin_set_change_lockout_and_agent_env(proj, monkeypatch):
    assert not AP.is_set()
    AP.set_pin("abcd")
    with pytest.raises(PolicyBlocked):
        AP.set_pin("new1", old="nope")
    AP.set_pin("new1", old="abcd")
    AP.verify("new1")
    for _ in range(AP.MAX_FAILS):
        with pytest.raises(PolicyBlocked):
            AP.verify("bad")
    with pytest.raises(PolicyBlocked, match="Too many"):
        AP.verify("new1")
    assert "new1" not in open(AP._path()).read()
    monkeypatch.setenv("CLAUDECODE", "1")
    assert AP.read_pin() is None                                       # never prompts inside an agent session
    proj.author = {"author_type": "human"}                             # `--as human` inside an agent session still fails
    with pytest.raises(PolicyBlocked):
        AP.confirm(proj, "x", "why")


def test_human_experiment_creation_needs_no_pin_unless_switched_on(proj):
    AP.set_pin("1234")
    proj.author = {"author_type": "human"}
    G.confirm_human_experiment(proj)                                   # default: no PIN
    proj.config["governance"] = {"approval": {"confirm_human_experiment_creation": True}}
    with pytest.raises(PolicyBlocked):
        G.confirm_human_experiment(proj)
    G.confirm_human_experiment(proj, pin="1234")


def test_policy_settings_and_promotion_need_the_pin(proj):
    from research import registry
    AP.set_pin("1234")
    proj.author = {"author_type": "human"}
    with pytest.raises(PolicyBlocked):
        registry.apply(proj, {"governance.tasks.max_auto_per_experiment": 9})
    assert registry.apply(proj, {"governance.tasks.max_auto_per_experiment": 9}, pin="1234")["changed"]


# ---- WP2: fail-closed approval, agent mirror imports, Telegram poll ----------------------------------------------------
def test_no_pin_no_tty_human_is_refused_and_opt_out_is_explicit(proj):
    proj.author = {"author_type": "human"}
    assert not AP.is_set()
    with pytest.raises(PolicyBlocked, match="No approval PIN"):
        AP.confirm(proj, "x", "why")
    proj.config["governance"] = {"approval": {"require_pin": False}}
    assert AP.confirm(proj, "x", "why")["approved"]


def test_agent_written_experiment_mirror_becomes_a_proposal(proj, repo, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    (repo / ".research" / "experiments" / "sneaky.md").write_text(
        "---\ntitle: Sneaky\nstatus: running\n---\n\n## Hypothesis\n\nh\n\n## Motivation\n\nr\n\n## Method\n\nm\n\n## Expected outcome\n\no\n")
    proj.sync()
    rows = proj.q("SELECT id, status FROM experiments")
    assert [r["status"] for r in rows] == ["proposed"]
    assert proj.q1("SELECT id FROM events WHERE action='import_rejected'")
    assert [pr["source"] for pr in SV.list_proposals(proj)] == ["mirror"]


def test_agent_cannot_set_a_finding_supported_by_editing_its_file(proj, repo, monkeypatch):
    f = services.create_finding(proj, "F", "stmt")
    path = repo / ".research" / "findings" / f"{f['id']}.md"
    prev = proj.get("finding", f["id"])["status"]
    monkeypatch.setenv("CLAUDECODE", "1")
    path.write_text(path.read_text().replace(f"status: {prev}", "status: supported"))
    os.utime(path, (1, 1))
    proj.sync()
    assert proj.get("finding", f["id"])["status"] == prev
    assert proj.q1("SELECT id FROM events WHERE action='import_rejected' AND entity_id=?", (f["id"],))


# the Telegram offset/0600 test moved to test_chat_telegram.py (offset saved before handling, per-gateway cursors, 0600 files)