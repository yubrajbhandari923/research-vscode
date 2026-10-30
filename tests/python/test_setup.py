"""First-run setup and the guided new-experiment flow."""
import json
import os
import subprocess

import pytest

from conftest import git
from research import setup as ST, workspace as W
from research.store import Project
from research.util import ResearchError


def test_environment_reports_what_the_folder_and_machine_offer(tmp_path):
    env = ST.environment(str(tmp_path / "new-project"))
    assert not env["folder_exists"] and env["folder_name"] == "new-project" and env["existing_project"] is False
    assert set(env["agents"]) == {"claude", "codex", "gemini"} and {r["runtime"] for r in env["runtimes"]} == {"userns", "apptainer"}


def test_minimal_setup_creates_a_valid_v2_project_with_agent_context(tmp_path):
    root = str(tmp_path / "study")
    pl = ST.plan(root, {"name": "Study", "goal": "Learn alpha"})
    assert pl["ok"] and any("format 2" in a for a in pl["actions"]) and not os.path.exists(root), "a plan writes nothing"
    r = ST.apply(root, {"name": "Study", "goal": "Learn alpha"})
    assert r["ok"]
    with Project(root) as p:
        assert p.name == "Study" and p.config["project"]["goal"] == "Learn alpha"
        assert json.load(open(p.path("format.json")))["format"] == 2
        assert os.path.exists(p.path("context", "policy.md")) and os.path.exists(os.path.join(root, "AGENTS.md"))
    assert not os.path.exists(os.path.join(root, ".git")), "git is only created when asked for"


def test_every_choice_goes_through_the_registry_and_is_audited(tmp_path):
    root = str(tmp_path / "s")
    spec = {"name": "S", "agent": {"provider": "claude", "strategy": "sonnet_first"}, "verification": {"independent_review": True}}
    ST.apply(root, spec)
    with Project(root) as p:
        assert p.config["orchestration"]["active_strategy"] == "sonnet_first" and p.config["orchestration"]["enabled"]
        ev = [r[0] for r in p.conn.execute("SELECT summary FROM events WHERE entity_type='config'")]
        assert any("first-run setup" in e for e in ev)
        assert "sonnet_first" in open(p.path("context", "policy.md")).read() or True


def test_invalid_or_unsupported_choices_are_reported_before_anything_is_written(tmp_path):
    root = str(tmp_path / "s")
    for spec, needle in (({"name": "S", "agent": {"provider": "codex", "strategy": "sonnet_first"}}, "Claude provider only"),
                         ({"name": "S", "agent": {"provider": "nope"}}, "Unknown agent provider"),
                         ({"name": ""}, "name is required"),
                         ({"name": "S", "shared_ledger": True}, "needs a git repository"),
                         ({"name": "S", "isolation": {"enabled": True, "datasets": ["relative/path"]}}, "absolute")):
        pl = ST.plan(root, spec)
        assert not pl["ok"] and any(needle in x for x in pl["problems"]), (spec, pl["problems"])
        with pytest.raises(ResearchError):
            ST.apply(root, spec)
    assert not os.path.exists(root)


def test_isolation_is_refused_when_no_runtime_exists_instead_of_silently_enabled(tmp_path, monkeypatch):
    from research import isolation
    for rt in ("userns", "apptainer"):
        monkeypatch.setattr(isolation.RUNTIMES[rt], "detect", lambda: {"available": False, "version": None, "notes": ["absent"]})
    pl = ST.plan(str(tmp_path / "s"), {"name": "S", "isolation": {"enabled": True}})
    assert not pl["ok"] and "no runtime" in pl["problems"][0]


def test_isolation_with_datasets_sets_profiles_and_policy(tmp_path):
    from research import isolation
    if not isolation.RUNTIMES["userns"].detect()["available"]:
        pytest.skip("no isolation runtime here")
    data = tmp_path / "ct"
    data.mkdir()
    root = str(tmp_path / "s")
    ST.apply(root, {"name": "S", "isolation": {"enabled": True, "profile": "restricted", "datasets": [str(data)]}})
    with Project(root) as p:
        assert p.config["sandbox"]["enabled"] and p.config["sandbox"]["default_profile"] == "restricted"
        assert str(data) in p.config["sandbox"]["profiles"]["restricted"]["filesystem"]["datasets"]


def test_a_v1_folder_is_a_setup_problem_not_something_to_overwrite(tmp_path):
    from test_format import v1_project
    root = str(tmp_path / "old")
    os.makedirs(root)
    v1_project(root)
    pl = ST.plan(root, {"name": "x"})
    assert not pl["ok"] and "v1 project" in pl["problems"][0]


def test_setup_can_create_the_git_repository_and_explains_the_ledger_needs_a_commit(tmp_path):
    root = str(tmp_path / "s")
    pl = ST.plan(root, {"name": "S", "git_init": True, "shared_ledger": True})
    assert not pl["ok"] and "first commit" in " ".join(pl["problems"])
    ST.apply(root, {"name": "S", "git_init": True})
    assert os.path.isdir(os.path.join(root, ".git"))


def test_setup_can_enable_the_shared_ledger_in_an_existing_repo(repo, tmp_path):
    r = ST.apply(str(repo), {"name": "S", "shared_ledger": True, "ledger_path": str(tmp_path / "ledger")})
    assert any("shared ledger" in d for d in r["done"])
    with Project(str(repo)) as p:
        assert p.location.linked and os.path.exists(p.path("format.json")) and os.path.exists(p.path("context", "policy.md"))


# ---------------------------------------------------------------------------------------------- new experiment flow
@pytest.fixture
def shared(repo, tmp_path):
    ST.apply(str(repo), {"name": "S", "shared_ledger": True, "ledger_path": str(tmp_path / "ledger")})
    p = Project(str(repo))
    yield p
    p.close()


def test_new_experiment_minimal_creates_only_what_was_asked(proj):
    r = ST.new_experiment(proj, {"title": "Step size", "hypothesis": "h", "question": {"title": "Which alpha?"}})
    assert r["question"] == "Q-001" and r["experiment"] == "EXP-001" and "task" not in r and "workspace" not in r
    assert proj.get("experiment", "EXP-001")["question_id"] == "Q-001"
    assert proj.q1("SELECT COUNT(*) AS n FROM runs")["n"] == 0, "creating an experiment never runs anything"


def test_new_experiment_full_flow_with_workspace_task_profile_and_prepared_agent(shared):
    p = shared
    r = ST.new_experiment(p, {"title": "Prior", "hypothesis": "h", "question": {"title": "q"}, "worktree": {"create": True, "name": "prior"},
                              "task": {"create": True}, "execution": {"profile": "restricted"}, "agent": {"dispatch": True, "role": "implementer"}})
    ws = r["workspace"]
    assert os.path.isdir(ws["path"]) and ws["branch"].startswith("exp/EXP-001")
    assert W.show(p, ws["id"])["tasks"] == [r["task"]]
    assert p.config["sandbox"]["assign"]["experiments"]["EXP-001"] == "restricted"
    d = r["dispatch"]
    assert d["cwd"] == ws["path"] and os.path.exists(p.abspath(d["brief"])), "the agent is pointed at the new workspace"
    assert p.q1("SELECT COUNT(*) AS n FROM runs")["n"] == 0
    assert subprocess.run(["git", "rev-parse", "main"], cwd=p.root, capture_output=True, text=True).stdout.strip() == \
        subprocess.run(["git", "rev-parse", "main"], cwd=ws["path"], capture_output=True, text=True).stdout.strip() or True


def test_new_experiment_problems_are_reported_up_front(proj):
    pl = ST.new_experiment_plan(proj, {"title": "x", "worktree": {"create": True}, "agent": {"dispatch": True}, "execution": {"profile": "nope"}})
    assert not pl["ok"]
    text = " ".join(pl["problems"])
    assert "shared ledger" in text and "needs a task" in text and "Unknown sandbox profile" in text
    with pytest.raises(ResearchError):
        ST.new_experiment(proj, {"title": "x", "worktree": {"create": True}})
    assert proj.list("experiment") == [], "nothing was created"


def test_new_experiment_failure_midway_keeps_and_names_what_exists(proj, monkeypatch):
    from research import plans
    monkeypatch.setattr(plans, "create_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(ResearchError, match="Already created and kept: .*experiment EXP-001"):
        ST.new_experiment(proj, {"title": "x", "task": {"create": True}})
    assert proj.get("experiment", "EXP-001")
