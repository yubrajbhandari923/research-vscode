import json
import os

import pytest

from research import orchestrate as O
from research import plans, services as S
from research import setup_agents as SA
from research.util import PolicyBlocked, ResearchError
from research_cli.main import main


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


def task(proj, plan, title="t", **kw):
    return plans.create_task(proj, plan["id"], title, **kw)


def test_routing_by_verifiability_not_label(proj, plan):
    t_ok = task(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    t_vague = task(proj, plan, task_type="implementation")
    t_syn = task(proj, plan, task_type="synthesis", acceptance_criteria="a", checks=["cmd:true"])
    t_ver = task(proj, plan, task_type="verification")
    t_exp = task(proj, plan, task_type="experiment", acceptance_criteria="a", checks=["cmd:true"])
    tiers = [O.route(proj, proj.get("task", t["id"]))["tier"] for t in (t_ok, t_vague, t_syn, t_ver, t_exp)]
    assert tiers == ["haiku", "sonnet", "opus", "sonnet", "haiku"]
    assert O.route(proj, proj.get("task", t_ok["id"]))["model"] == "claude-haiku-5-5"


def test_fallback_when_model_unavailable(proj, plan):
    t = proj.get("task", task(proj, plan)["id"])
    r = O.route(proj, t, "haiku", available=["claude-sonnet-5-5"])
    assert r["tier"] == "sonnet" and r["degraded"]
    r = O.route(proj, t, "haiku", available=["haiku"])  # alias fallback
    assert r["model"] == "haiku" and not r["degraded"]
    r = O.route(proj, t, "opus", available=["claude-sonnet-5-5"])  # never silently down
    assert r["model"] is None and r["degraded"]


def test_parent_child_delegation_and_single_claim(proj, plan):
    par = task(proj, plan, "parent")
    ch = O.delegate(proj, par["id"], "child", "haiku", acceptance="a", checks=["cmd:true"])
    assert ch["id"] in proj.get("task", par["id"])["depends_on"]
    assert O.ledger(proj, ch["id"])["parent"] == par["id"] and O.ledger(proj, par["id"])["children"] == [ch["id"]]
    plans.start_task(proj, ch["id"])
    proj.author["author_name"] = "other"
    with pytest.raises(PolicyBlocked):
        plans.start_task(proj, ch["id"])


def test_haiku_requires_frozen_tests_first(proj, plan, repo):
    t = task(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    with pytest.raises(PolicyBlocked):
        O.dispatch_task(proj, t["id"])
    (repo / "test_x.py").write_text("def test_a():\n    assert 1\n")
    O.freeze(proj, t["id"], ["test_x.py"])
    d = O.dispatch_task(proj, t["id"])
    assert d["tier"] == "haiku" and "claude-haiku-5-5" in d["argv"]
    brief = (repo / d["brief"]).read_text()
    assert "Never edit, delete or weaken the frozen" in brief and "research skill show ponytail" not in brief  # not installed
    assert "Optional skill ponytail is not installed" in brief


def test_ponytail_loaded_when_installed(proj, plan, repo):
    for n in ("ponytail", "ponytail-review"):
        d = repo / "src" / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {n}\ndescription: x\n---\nbody\n")
    from research.skills import add_skills
    add_skills(proj, str(repo / "src"))
    t = task(proj, plan, task_type="implementation")
    d = O.dispatch_task(proj, t["id"], "sonnet")
    b = (repo / d["brief"]).read_text()
    assert "research skill show ponytail`" in b and "research skill show ponytail-review" in b and "SONNET" in b


def test_frozen_tests_protected_and_correction_rules(proj, plan, repo):
    t = task(proj, plan)
    f = repo / "test_x.py"
    f.write_text("def test_a():\n    assert 1\n")
    O.freeze(proj, t["id"], ["test_x.py"])
    f.write_text("def test_a():\n    pass\n")  # weakened
    with pytest.raises(PolicyBlocked):
        plans.complete_task(proj, t["id"])
    assert O.record_attempt(proj, t["id"], "haiku", "pass")["action"] == "restore_tests"
    with pytest.raises(PolicyBlocked):
        O.correct_test(proj, t["id"], "test_x.py", "it was wrong, really", "haiku")
    with pytest.raises(ResearchError):
        O.correct_test(proj, t["id"], "test_x.py", "x", "sonnet")
    O.correct_test(proj, t["id"], "test_x.py", "oracle asserted wrong sign convention", "sonnet")
    assert not O.tampered(proj, t["id"]) and len(O.ledger(proj, t["id"])["corrections"]) == 1
    plans.complete_task(proj, t["id"])


def test_bounded_retries_and_escalation(proj, plan):
    t = task(proj, plan)["id"]
    steps = [O.record_attempt(proj, t, "haiku", "fail", "incorrect_implementation")["action"] for _ in range(3)]
    assert steps == ["retry", "retry", "escalate"]
    assert O.record_attempt(proj, t, "sonnet", "fail", "incorrect_implementation")["action"] == "retry"
    r = O.record_attempt(proj, t, "sonnet", "fail", "incorrect_implementation")
    assert (r["action"], r["tier"]) == ("escalate", "opus")
    r = O.record_attempt(proj, t, "opus", "fail", "ambiguous_specification")  # third escalation > budget
    assert r["action"] == "block" and proj.get("task", t)["status"] == "blocked"
    with pytest.raises(ResearchError):
        O.record_attempt(proj, t, "haiku", "fail")  # category required


def test_scientific_and_environment_routing(proj, plan):
    t = task(proj, plan)["id"]
    assert O.record_attempt(proj, t, "haiku", "fail", "scientific_assumption")["tier"] == "opus"
    t2 = task(proj, plan)["id"]
    assert O.record_attempt(proj, t2, "haiku", "fail", "environment")["action"] == "block"


def test_tuning_validation_only_and_oneshot_test(proj):
    e = S.create_experiment(proj, "tune")["id"]
    O.tuning_declare(proj, e, "min rmse", {"lr": "1e-4:1e-1"}, "random seed 0", "min validation rmse", 0, budget=2)
    with pytest.raises(PolicyBlocked):
        O.tuning_test(proj, e, 0.1)  # locked
    with pytest.raises(PolicyBlocked):
        O.tuning_trial(proj, e, {"lr": 0.1}, "test", 0.1)
    with pytest.raises(ResearchError):
        O.tuning_select(proj, e, {"lr": 0.1}, "r")  # no validation trials
    O.tuning_trial(proj, e, {"lr": 0.1}, "validation", 0.3)
    O.tuning_trial(proj, e, {"lr": 0.01}, "design", 0.2)
    with pytest.raises(PolicyBlocked):
        O.tuning_trial(proj, e, {"lr": 0.001}, "validation", 0.1)  # budget
    O.tuning_select(proj, e, {"lr": 0.1}, "best validation")
    with pytest.raises(PolicyBlocked):
        O.tuning_trial(proj, e, {"lr": 0.5}, "validation", 0.1)
    O.tuning_test(proj, e, 0.35)
    with pytest.raises(PolicyBlocked):
        O.tuning_test(proj, e, 0.2)
    with pytest.raises(ResearchError):
        O.tuning_declare(proj, e, "o", {"a": 1}, "s", "c")  # frozen plan


def test_setup_idempotent_preserves_user_files(proj, repo):
    mine = repo / ".claude" / "agents"
    mine.mkdir(parents=True)
    (mine / "research-haiku.md").write_text("my own agent\n")
    dry = SA.setup(proj)
    assert not (mine / "research-opus.md").exists() and dry["applied"] is False
    r1 = SA.setup(proj, apply=True)
    assert (mine / "research-haiku.md").read_text() == "my own agent\n"
    assert "claude-opus-5-5" in (mine / "research-opus.md").read_text()
    assert any("user-owned" in s for s in r1["skipped"]) and any("ponytail" in s and "not approved" in s for s in r1["skipped"])
    assert proj.config["orchestration"]["enabled"]
    before = (mine / "research-sonnet.md").read_text()
    r2 = SA.setup(proj, apply=True)
    assert (mine / "research-sonnet.md").read_text() == before and all(f["state"] != "create" for f in r2["agent_files"])
    assert (repo / ".research" / "skills" / "engineering-loop" / "SKILL.md").exists()


def test_cli_and_existing_dispatch_unchanged(proj, repo, capsys):
    assert main(["dispatch", "planner", "--objective", "x", "--profile", "codex", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["argv"][0] == "codex"
    assert main(["setup", "agents", "--json"]) == 0
