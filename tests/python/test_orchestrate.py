import json
import os

import pytest

from research import orchestrate as O
from research import plans, services as S
from research import setup_agents as SA
from research.util import PolicyBlocked, ResearchError
from research_cli.main import main
import io
import subprocess
import sys
from research import agents
from research import agents as AG
from research import hooks
from research import mcp
from research import plans as P
from research import rpc
from research import runs as R


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
    assert d["tier"] == "haiku" and "claude-haiku-5-5" in d["provider_argv"]
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
    assert d["provider_argv"][0] == "codex"
    assert main(["setup", "agents", "--json"]) == 0


def use(p, name="sonnet_first"):
    p.save_config({"orchestration.active_strategy": name})
    p.config = p.load_config()


def mk(p, plan, **kw):
    return p.get("task", plans.create_task(p, plan["id"], "t", **kw)["id"])


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV = {**os.environ, 'PYTHONPATH': os.path.join(ROOT, 'packages', 'core') + os.pathsep + os.path.join(ROOT, 'packages', 'cli')}


def O_passed(proj, tid):
    return O.verify_work(proj, tid)['passed']


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


# ------------------------------------------------------------------ contracts and verification
def test_contract_in_brief_and_file_boundary(proj, plan, repo):
    use(proj)
    t = mk(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    O.set_contract(proj, t["id"], objective="parse metrics", allowed_files=["src/*.py"], constraints=["no new deps"],
                   expected_outputs=["src/metrics.py"])
    (repo / "test_x.py").write_text("def test_a():\n    assert 1\n")
    O.freeze(proj, t["id"], ["test_x.py"])
    d = O.dispatch_task(proj, t["id"])
    brief = (repo / d["brief"]).read_text()
    assert "Contract" in brief and "src/*.py" in brief and "no new deps" in brief and "orchestrate verify" in brief
    (repo / "src").mkdir()
    (repo / "src" / "metrics.py").write_text("x = 1\n")
    assert O.verify_work(proj, t["id"])["passed"]
    (repo / "other.py").write_text("y = 1\n")
    r = O.verify_work(proj, t["id"])
    assert not r["passed"] and "outside the contract" in r["diagnostics"][0] and "other.py" in r["diagnostics"][0]


# ------------------------------------------------------------------ per-task overrides
def test_task_override_applies_is_recorded_and_clears(proj, plan, repo):
    use(proj)
    a = mk(proj, plan, task_type="implementation")                   # unchecked → balanced by strategy
    b = mk(proj, plan, task_type="implementation")
    with pytest.raises(ResearchError):
        O.set_override(proj, a["id"], level="nope")
    O.set_override(proj, a["id"], level="deep")
    O.set_override(proj, b["id"], profile="claude-fast", model="m-special")
    ra, rb = O.route(proj, proj.get("task", a["id"])), O.route(proj, proj.get("task", b["id"]))
    assert ra["model"] == "claude-opus-5-5" and ra["why"] == "task override" and ra["overrides"]["task_override"] == {"level": "deep"}
    assert rb["profile"] == "claude-fast" and rb["model"] == "m-special"
    d = O.dispatch_task(proj, a["id"], allow_unfrozen="test")
    assert "claude-opus-5-5" in d["provider_argv"] and d["policy"]["overrides"]["task_override"] == {"level": "deep"}
    assert agents.dispatch(proj, "implementer", a["id"], allow_unfrozen="test")["model"] == "claude-opus-5-5"
    assert O.route(proj, proj.get("task", a["id"]), "fast")["level"] == "fast"      # per-dispatch beats per-task
    O.set_override(proj, a["id"], clear=True)
    assert O.route(proj, proj.get("task", a["id"]))["level"] == "balanced" and O.ledger(proj, a["id"]).get("override") is None


# ---- 2/3: one effective task specification and policy across interfaces ----------------------------------
def test_same_obligations_through_every_interface(proj, repo, contract_task):
    O.set_contract(proj, contract_task['id'], objective='g', constraints=['no new deps'], allowed_files=['src/*.py'])
    hooks.add(proj, 'gate', 'post_implementation', 'true', required=True)      # unapproved, required
    core = plans.task_context(proj, contract_task['id'])['effective_spec']
    out = io.StringIO()
    req = {'id': 1, 'method': 'task_context', 'params': {'id': contract_task['id']}}
    rpc.serve(str(repo), io.StringIO(json.dumps(req) + '\n'), out)
    rows = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r for r in rows if r.get('id') == 1][0]['result']['effective_spec'] == core
    out = io.StringIO()
    msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'task_context', 'arguments': {'id': contract_task['id']}}}
    mcp.serve(str(repo), io.StringIO(json.dumps(msg) + '\n'), out)
    text = json.dumps(json.loads(out.getvalue()))
    for needle in ('no new deps', 'src/*.py', 'HUMAN APPROVAL REQUIRED'):
        assert needle in text
    cli = subprocess.run([sys.executable, '-m', 'research_cli', 'task', 'context', contract_task['id'], '--json'], cwd=repo, env=ENV,
                         capture_output=True, text=True)
    assert cli.returncode == 0 and json.loads(cli.stdout)['effective_spec'] == core
    brief = (repo / agents.dispatch(proj, 'implementer', contract_task['id'], allow_unfrozen='x')['brief']).read_text()
    assert 'no new deps' in brief and 'HUMAN APPROVAL REQUIRED' in brief


def test_conflicting_legacy_fields_block_dispatch_and_completion(proj, contract_task):
    O.set_contract(proj, contract_task['id'], objective='something else')
    spec = plans.task_context(proj, contract_task['id'])['effective_spec']
    assert [c['field'] for c in spec['conflicts']] == ['objective']
    with pytest.raises(PolicyBlocked, match='Conflicting'):
        agents.dispatch(proj, 'implementer', contract_task['id'])
    assert not O_passed(proj, contract_task['id'])


def test_dispatch_profiles_roles_and_briefs(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "Repro", objective="match fig 3")
    t = P.create_task(proj, plan["id"], "impl", checks=["file:x.png"], related_experiment_id=e["id"])
    f = S.create_finding(proj, "claim", supports=[runs[0]["id"]])
    proj.config["agents"]["roles"]["verifier"] = "codex"
    proj.config["agents"]["profiles"]["codex"]["model"] = "gpt-5"
    d = AG.dispatch(proj, None, t["id"])
    assert d["role"] == "implementer" and d["profile"] == "claude" and d["provider_argv"][0] == "claude"
    assert d["env"]["RESEARCH_AGENT_ROLE"] == "implementer" and d["brief"] in d["provider_argv"][-1]
    brief = (repo / d["brief"]).read_text()
    assert "IMPLEMENTER" in brief and "Task T-001" in brief and "file: x.png" in brief and "match fig 3" in brief
    v = AG.dispatch(proj, None, f["id"])
    assert v["role"] == "verifier" and v["provider_argv"][0] == "codex" and "-m" in v["provider_argv"] and "gpt-5" in v["provider_argv"]
    vb = (repo / v["brief"]).read_text()
    assert "check a claim against its evidence" in vb and "task verify" not in vb
    g = AG.dispatch(proj, "planner", objective="Reproduce Table 2", profile="gemini")
    assert g["argv"][:2] == ["gemini", "-i"] and "Reproduce Table 2" in (repo / g["brief"]).read_text()
    assert AG.dispatch(proj, None, e["id"])["role"] == "analyst"
    with pytest.raises(ResearchError, match="needs an objective"):
        AG.dispatch(proj, "planner")
    with pytest.raises(ResearchError, match="Unknown agent profile"):
        AG.dispatch(proj, "analyst", e["id"], profile="nope")


def test_role_env_changes_author_name(monkeypatch):
    from research.util import detect_author
    monkeypatch.setenv("RESEARCH_AGENT", "claude")
    monkeypatch.setenv("RESEARCH_AGENT_ROLE", "verifier")
    assert detect_author()["author_name"] == "claude/verifier"


@pytest.fixture
def contract_task(proj):
    plan = plans.create_plan(proj, 'p')
    return plans.create_task(proj, plan['id'], 't', goal='g', acceptance_criteria='a', checks=['cmd:true'])
