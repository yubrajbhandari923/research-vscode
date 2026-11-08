"""Hooks, checks, verification gates, the frozen oracle and completion paths."""
import os
import shlex
import sys
import time

import pytest
from conftest import as_agent, as_human
from research import agents
from research import expcheck
from research import hooks
from research import orchestrate
from research import orchestrate as O
from research import plans
from research import plans as P
from research import runs
from research import runs as R
from research import services
from research import services as S
from research import verification
from research import verification as V
from research.util import PolicyBlocked
from research.util import ResearchError


def approve(p):
    p.author["author_type"] = "human"
    hooks.approve(p)


def use(p, name="sonnet_first"):
    p.save_config({"orchestration.active_strategy": name})
    p.config = p.load_config()


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


def mk(p, plan, **kw):
    return p.get("task", plans.create_task(p, plan["id"], "t", **kw)["id"])


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


# ------------------------------------------------------------------ hooks
def test_hooks_need_human_approval_and_edits_revoke_it(proj):
    hooks.add(proj, "ok", "post_implementation", "echo hi")
    assert hooks.run(proj, "post_implementation")["skipped"][0]["reason"].startswith("not approved")
    proj.author["author_type"] = "agent"
    with pytest.raises(PolicyBlocked):
        hooks.approve(proj)
    approve(proj)
    r = hooks.run(proj, "post_implementation")
    assert r["ok"] and r["ran"][0]["output"].strip() == "hi" and hooks.results(proj, "ok")[0]["ok"]
    hooks.add(proj, "ok", "post_implementation", "echo changed")      # any edit revokes approval
    assert not hooks.is_approved(proj, "ok") and not hooks.run(proj, "post_implementation")["ran"]
    approve(proj)
    hooks.set_enabled(proj, "ok", False)
    assert hooks.run(proj, "post_implementation")["skipped"][0]["reason"] == "disabled"


def test_hook_timeout_failure_and_advisory(proj):
    hooks.add(proj, "slow", "preflight", f"{sys.executable} -c 'import time; time.sleep(30)'", timeout=1)
    hooks.add(proj, "soft", "preflight", "exit 3", required=False)
    approve(proj)
    r = hooks.run(proj, "preflight")
    slow = next(x for x in r["ran"] if x["hook"] == "slow")
    assert slow["timed_out"] and slow["seconds"] < 10 and not r["ok"]
    with pytest.raises(PolicyBlocked, match="slow"):
        hooks.gate(proj, "preflight")
    hooks.set_enabled(proj, "slow", False)
    approve(proj)
    assert hooks.run(proj, "preflight")["ok"]          # an advisory failure does not block


def test_preflight_blocks_dispatch_and_pre_experiment_blocks_runs(proj, plan, repo):
    use(proj)
    t = mk(proj, plan, task_type="implementation")
    hooks.add(proj, "env", "preflight", "exit 1")
    hooks.add(proj, "data", "pre_experiment", "exit 1")
    approve(proj)
    with pytest.raises(PolicyBlocked, match="env"):
        O.dispatch_task(proj, t["id"], "balanced")
    exp = services.create_experiment(proj, "e", hypothesis="h")
    with pytest.raises(PolicyBlocked, match="data"):
        runs.exec_run(proj, exp["id"], "true")
    assert proj.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0   # nothing was registered


def test_verify_returns_diagnostics_and_bounded_next_step(proj, plan, repo):
    use(proj)
    t = mk(proj, plan, task_type="implementation", acceptance_criteria="a", checks=[f"cmd:{sys.executable} -c \"print('E: boom'); raise SystemExit(2)\""])
    r = O.verify_work(proj, t["id"], "fast", record=True)
    assert not r["passed"] and "E: boom" in r["diagnostics"][0] and r["next"]["action"] == "retry"
    for _ in range(2):
        r = O.verify_work(proj, t["id"], "fast", record=True)
    assert r["next"]["action"] == "escalate" and r["next"]["tier"] == "balanced"


def test_no_verifier_is_not_a_pass_and_hooks_count(proj, plan):
    use(proj)
    t = mk(proj, plan, task_type="implementation")
    r = O.verify_work(proj, t["id"])
    assert not r["passed"] and "not a pass" in r["diagnostics"][0]
    hooks.add(proj, "oracle", "post_implementation", "true")
    approve(proj)
    assert O.verify_work(proj, t["id"])["passed"]


def test_frozen_tests_still_enforced_by_verify(proj, plan, repo):
    use(proj)
    t = mk(proj, plan, acceptance_criteria="a", checks=["cmd:true"])
    (repo / "t.py").write_text("a\n")
    O.freeze(proj, t["id"], ["t.py"])
    (repo / "t.py").write_text("b\n")
    assert "frozen" in O.verify_work(proj, t["id"])["diagnostics"][0]


# ------------------------------------------------------------------ experiment check
def test_experiment_check_separates_verification_from_interpretation(proj, repo):
    e = services.create_experiment(proj, "e", hypothesis="h")
    r1 = runs.exec_run(proj, e["id"], f"{sys.executable} -c \"print(1)\"")
    out = expcheck.check(proj, e["id"])
    assert r1["status"] == "completed"
    assert any("logged no metrics" in f["problem"] for f in out["deterministic"]["failed"]) and not out["deterministic"]["all_passed"]
    assert any("synthesis" in h for h in out["needs_human"]) or out["synthesis"]["due"]
    hooks.add(proj, "outputs", "post_experiment", "true")
    out = expcheck.check(proj, e["id"])
    assert any("hook outputs" in h for h in out["needs_human"])             # unapproved: reported, never counted as passed
    approve(proj)
    out = expcheck.check(proj, e["id"], request_synthesis=True)
    assert "hook outputs passed" in out["deterministic"]["validated"]
    assert out["synthesis"]["due"] and out["synthesis"]["dispatch"]["brief"]
    assert proj.q("SELECT COUNT(*) AS n FROM syntheses")[0]["n"] == 0       # requested, never written
    assert any(ev["action"] == "validated" for ev in proj.q("SELECT action FROM events"))


def test_unfrozen_gate_offers_the_coordinator_and_that_dispatch_is_allowed(proj, plan, repo):
    from research.util import NoFrozenTests
    use(proj)
    a = mk(proj, plan, task_type="implementation")
    O.set_override(proj, a["id"], level="fast")
    with pytest.raises(NoFrozenTests) as e:
        agents.dispatch(proj, "implementer", a["id"])
    assert e.value.data == {"coordinator_level": "balanced"} and "coordinator" in e.value.hint
    d = agents.dispatch(proj, "implementer", a["id"], level="balanced")      # the offered way out: no override, no bypass
    assert d["model"] == "claude-sonnet-5-5"


def test_required_unapproved_hook_blocks_every_completion_path(proj):
    hooks.add(proj, 'gate', 'post_implementation', 'true', required=True)
    plan = plans.create_plan(proj, 'p')
    # Agents may only auto-create typed tasks inside their experiment: give the agent one, so the refusals below come from the hook.
    from research.util import detect_author
    from research import services as S
    eid = S.create_experiment(proj, 'E')['id']
    proj.author = detect_author(agent='claude')
    kw = dict(checks=['cmd:true'], task_type='verification', related_experiment_id=eid)
    with pytest.raises(PolicyBlocked) as ex:
        plans.create_task(proj, plan['id'], 'born done', status='done', **kw)
    assert 'hook' in str(ex.value).lower() or 'approv' in str(ex.value).lower()
    t = plans.create_task(proj, plan['id'], 'x', **kw)
    plans.start_task(proj, t['id'])
    for finish in (lambda: plans.update_task(proj, t['id'], status='done'), lambda: plans.complete_task(proj, t['id']),
                   lambda: verification.verify_task(proj, t['id'])):
        try:
            res = finish()
        except PolicyBlocked:
            continue
        assert res.get('passed') is False          # verification reports; it never confers completion
    assert proj.get('task', t['id'])['status'] != 'done'


def test_no_verifier_is_unverified_not_passed(proj):
    plan = plans.create_plan(proj, 'p')
    t = plans.create_task(proj, plan['id'], 'x')
    r = O.verify_work(proj, t['id'])
    assert not r['passed'] and r['verification_state'] == 'unverified'


def test_unapproved_required_hook_blocks_gate(proj):
    hooks.add(proj, 'required', 'preflight', 'true')
    with pytest.raises(PolicyBlocked, match='required'):
        hooks.gate(proj, 'preflight')
    assert not hooks.run(proj, 'preflight')['ok']
    hooks.set_enabled(proj, 'required', False)
    assert hooks.gate(proj, 'preflight')['ok']


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), 'bad'])
def test_hook_timeout_validation_before_mutation(proj, timeout):
    with pytest.raises(ResearchError, match='timeout'):
        hooks.add(proj, 'invalid', 'preflight', 'true', timeout=timeout)
    assert 'invalid' not in hooks.specs(proj)


def test_advisory_hook_is_not_a_required_check(proj):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task', checks=['cmd:true'])
    hooks.add(proj, 'advisory', 'post_implementation', 'exit 1', required=False)
    hooks.approve(proj)
    assert orchestrate.verify_work(proj, task['id'])['passed']
    plans.update_task(proj, task['id'], checks=[])
    assert not orchestrate.verify_work(proj, task['id'])['passed']


@pytest.mark.parametrize('exit_code', [None, 1])
def test_completed_label_does_not_prove_success(proj, exit_code):
    exp = services.create_experiment(proj, 'experiment')
    run = runs.attach_run(proj, exp['id'])
    runs.update_run(proj, run['id'], exit_code=exit_code)
    runs.log_metric(proj, 'loss', 1, run=run['id'])
    result = expcheck.check(proj, exp['id'])
    assert not result['deterministic']['all_passed']
    assert result['deterministic']['failed']


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process group cleanup')
def test_check_timeout_stops_descendants(proj, repo):
    from research.verification import run_check
    marker = repo / 'escaped'
    script = "import time; from pathlib import Path; time.sleep(0.8); Path('escaped').write_text('bad')"
    cmd = shlex.quote(sys.executable) + ' -c ' + shlex.quote(script)
    proj.config['verification']['check_timeout_seconds'] = 0.1
    result = run_check(proj, {'type': 'command', 'run': cmd}, {})
    assert not result['ok'] and 'timed out' in result['detail']
    time.sleep(1)
    assert not marker.exists()


def test_freeze_cannot_accept_a_changed_oracle(proj, repo):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'implement')
    path = repo / 'acceptance.py'
    path.write_text('assert result == 42\n')
    orchestrate.freeze(proj, task['id'], ['acceptance.py'])
    before = orchestrate.ledger(proj, task['id'])['frozen']
    path.write_text('assert True\n')
    with pytest.raises(PolicyBlocked, match='already frozen'):
        orchestrate.freeze(proj, task['id'], ['acceptance.py'])
    assert orchestrate.ledger(proj, task['id'])['frozen'] == before
    assert orchestrate.tampered(proj, task['id']) == ['acceptance.py']


@pytest.mark.parametrize('entry', ['not json', '[]', '{"frozen": []}'])
def test_corrupt_ledger_never_resets_frozen_tests(proj, repo, entry):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task')
    directory = repo / '.research/orchestration'
    directory.mkdir(exist_ok=True)
    path = directory / (task['id'] + '.json')
    path.write_text(entry)
    with pytest.raises(ResearchError, match='ledger'):
        orchestrate.assert_frozen_intact(proj, task['id'])
    assert path.read_text() == entry


@pytest.mark.parametrize('entrypoint', ['update', 'mark'])
def test_all_public_completion_paths_enforce_checks(proj, entrypoint):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task', checks=['cmd:exit 1'])
    proj.author = {'author_type': 'agent', 'author_name': 'implementer'}
    with pytest.raises(PolicyBlocked):
        if entrypoint == 'update':
            plans.update_task(proj, task['id'], status='done')
        else:
            services.set_status(proj, task['id'], 'done')
    assert proj.get('task', task['id'])['status'] == 'todo'


def test_preflight_applies_to_plain_dispatch(proj):
    from research import agents
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task')
    hooks.add(proj, 'required', 'preflight', 'true')
    with pytest.raises(PolicyBlocked):
        agents.dispatch(proj, 'implementer', task['id'])
    hooks.approve(proj)
    result = agents.dispatch(proj, 'implementer', task['id'])
    assert result['preflight']['ran'] == ['required']


def test_parse_check_forms():
    assert V.parse_check("cmd:pytest -q") == {"type": "command", "run": "pytest -q"}
    assert V.parse_check("make test") == {"type": "command", "run": "make test"}
    assert V.parse_check("file: out/a.png") == {"type": "file", "path": "out/a.png"}
    assert V.parse_check("metric:EXP-2:rmse<=0.05") == {"type": "metric", "name": "rmse", "op": "<=", "value": 0.05, "target": "EXP-002"}
    m = V.parse_check("metric: acc >= 0.9")
    assert m["op"] == ">=" and m["value"] == 0.9 and "target" not in m
    for c in ("cmd:x", "file:y", "metric:RUN-0001:loss<1"):
        assert V.parse_check(V.describe_check(V.parse_check(c))) == V.parse_check(c)  # round trip
    with pytest.raises(ResearchError):
        V.parse_check("metric:rmse~0.1")


def test_checks_gate_completion_for_agents_warn_humans(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "P")
    t = P.create_task(proj, plan["id"], "impl", related_experiment_id=e["id"],
                      checks=["file:out_0.5.png", "metric:err<=0.01", "file:missing.png", f"cmd:{sys.executable} -c 'exit(0)'"])
    as_agent(proj, "claude", "implementer", monkeypatch)
    P.start_task(proj, t["id"])
    with pytest.raises(PolicyBlocked, match="3/4 checks passed: file: missing.png"):
        P.complete_task(proj, t["id"])
    assert proj.get("task", t["id"])["status"] == "running"
    last = V.latest(proj, t["id"], "check")
    assert last["verdict"] == "fail" and len(last["details"]) == 4
    assert any(a["kind"] == "verification_failed" for a in V.attention_items(proj))
    as_human(proj)
    done = P.complete_task(proj, t["id"])  # humans: warning, not a block
    assert done["status"] == "done" and "can't be completed" in done["warnings"][0]


def test_verify_moves_verify_tasks(proj, repo):
    _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "P")
    t = P.create_task(proj, plan["id"], "impl", checks=["file:out_0.5.png"], status="verify")
    res = V.verify_task(proj, t["id"])
    assert res["passed"] and res["task"]["status"] == "done" and res["record"]["id"] == f"{t['id']}/V1"
    t2 = P.create_task(proj, plan["id"], "impl2", checks=["file:nope.png"], status="verify")
    assert V.verify_task(proj, t2["id"])["task"]["status"] == "running"
    bare = V.verify_task(proj, P.create_task(proj, plan["id"], "x")["id"])
    assert not bare["passed"] and bare["verification_state"] == "unverified"
