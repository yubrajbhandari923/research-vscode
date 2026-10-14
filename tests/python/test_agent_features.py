"""Hooks, task contracts, usage accounting, per-task overrides, strategy-aware agent files, experiment check."""
import json
import os
import sys

import pytest

from research import agents, expcheck, hooks, orchestrate as O, plans, runs, services, strategy as S, usage as U
from research import setup_agents as SA
from research.util import PolicyBlocked, ResearchError


def use(p, name="sonnet_first"):
    p.save_config({"orchestration.active_strategy": name})
    p.config = p.load_config()


def approve(p):
    p.author["author_type"] = "human"
    hooks.approve(p)


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


def mk(p, plan, **kw):
    return p.get("task", plans.create_task(p, plan["id"], "t", **kw)["id"])


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


# ------------------------------------------------------------------ usage
CLAUDE_JSON = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.0123, "duration_ms": 900, "session_id": "s1",
                          "usage": {"input_tokens": 100, "output_tokens": 40, "cache_read_input_tokens": 500}})


def test_usage_parse_never_invents(proj):
    r = U.parse_claude_json(CLAUDE_JSON)
    assert r["input_tokens"] == 100 and r["cache_read_tokens"] == 500 and "cache_write_tokens" not in r and r["cost_usd"] == 0.0123
    assert U.parse_claude_json("not json") == {}
    assert U.record(proj, "x", {}, task="T-1") is None and U.records(proj) == []


def test_usage_summary_counts_reported_not_zero(proj):
    U.record(proj, "claude_json", {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5}, task="T-1", strategy="a", level="fast")
    U.record(proj, "manual", {"input_tokens": 300}, task="T-2", strategy="a", level="fast")
    U.record(proj, "manual", {"input_tokens": 50, "cost_usd": 1.0}, task="T-3", strategy="b", level="deep")
    g = U.summary(proj, "strategy")["groups"]
    assert g["a"]["input_tokens"] == {"total": 400, "reported": 2} and g["a"]["cost_usd"] == {"total": 0.5, "reported": 1}
    assert g["a"]["cache_read_tokens"] == {"total": None, "reported": 0} and g["a"]["mean_per_task"]["cache_read_tokens"] is None
    assert g["b"]["tasks"] == 1 and U.summary(proj, "level")["groups"]["deep"]["records"] == 1


def test_headless_run_records_usage_and_manual_fallback(proj, plan, repo):
    script = repo / "fake_claude.py"
    script.write_text(f"import sys\nprint({CLAUDE_JSON!r})\n")
    proj.config["agents"]["profiles"]["mock"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--json"],
                                                 "usage_parser": "claude_json", "model": "m-mock", "model_flag": "--model"}
    d = agents.dispatch(proj, "analyst", None, "x", profile="mock", headless=True)
    assert d["argv"][:3] == [sys.executable, str(script), "--json"]
    h = agents.run_headless(proj, d)
    assert h["exit_code"] == 0 and h["result"] == "done" and h["usage"]["input_tokens"] == 100 and h["usage"]["model"] == "m-mock"
    proj.config["agents"]["profiles"]["plain"] = {"command": sys.executable, "model": None}
    with pytest.raises(ResearchError, match="headless_args"):
        agents.dispatch(proj, "analyst", None, "x", profile="plain", headless=True)
    use(proj)
    t = mk(proj, plan, acceptance_criteria="a", checks=["cmd:true"])
    O.record_attempt(proj, t["id"], "balanced", "fail", "incorrect_implementation", usage={"input_tokens": 7})
    assert U.summary(proj, "level")["groups"]["balanced"]["input_tokens"]["total"] == 7
    assert O.status(proj, t["id"])["usage"]["input_tokens"] == 7        # the manual ledger copy is preserved


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


# ------------------------------------------------------------------ agent files
def test_strategy_aware_agent_files(proj, repo):
    ad = repo / ".claude" / "agents"
    ad.mkdir(parents=True)
    (ad / "research-haiku.md").write_text("<!-- research:managed -->\nlegacy\n")
    (ad / "research-deep.md").write_text("mine, no marker\n")
    (ad / "my-agent.md").write_text("custom\n")
    SA.setup(proj, apply=True, strategy="sonnet_first")
    fast = (ad / "research-fast.md").read_text()
    assert "model: claude-haiku-5-5" in fast and "WORKER" in fast and "orchestrate verify" in fast and "sonnet_first" in fast
    bal = (ad / "research-balanced.md").read_text()
    assert "COORDINATOR" in bal and "orchestrate contract" in bal and "research hook list" in bal and "← you" in bal
    assert "escalat" in bal.lower() and "claude-opus-5-5" in bal
    assert (ad / "research-deep.md").read_text() == "mine, no marker\n" and (ad / "my-agent.md").read_text() == "custom\n"
    assert not (ad / "research-haiku.md").exists() and (repo / ".research" / "cache" / "removed-agents" / "research-haiku.md").exists()
    before = (ad / "research-fast.md").read_text()
    use(proj, "haiku_max")
    plan = SA.plan_agent_files(proj)
    assert any(f["tier"] == "fast" and f["state"] == "update" for f in plan)       # attempts differ → stale, will refresh
    SA.setup(proj, apply=True)
    assert (ad / "research-fast.md").read_text() != before and "haiku_max" in (ad / "research-fast.md").read_text()


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


def test_cli_hooks_usage_pin_and_check(proj, plan, repo, capsys):
    from research_cli.main import main

    def run(*a):
        capsys.readouterr()
        rc = main(list(a))
        return rc, capsys.readouterr().out
    assert run("--as", "agent", "--agent", "claude", "hook", "add", "t1", "--stage", "post_implementation", "--command", "true")[0] == 0
    assert run("--as", "agent", "--agent", "claude", "hook", "approve")[0] == 3          # policy block: agents cannot approve
    assert run("--as", "human", "hook", "approve")[0] == 0
    rc, out = run("--json", "hook", "run", "--stage", "post_implementation")
    assert rc == 0 and json.loads(out)["ran"][0]["ok"]
    use(proj)
    t = mk(proj, plan, task_type="implementation")
    assert run("orchestrate", "pin", t["id"], "--level", "deep")[0] == 0
    assert json.loads(run("--json", "orchestrate", "route", t["id"])[1])["level"] == "deep"
    assert run("orchestrate", "pin", t["id"], "--clear")[0] == 0
    assert run("--json", "orchestrate", "contract", t["id"], "--objective", "o", "--allowed-file", "a.py")[0] == 0
    assert json.loads(run("--json", "orchestrate", "contract", t["id"])[1])["contract"]["allowed_files"] == ["a.py"]
    assert run("orchestrate", "verify", t["id"])[0] == 1       # no verifier ran: not a pass
    rc, out = run("--json", "usage")
    assert rc == 0 and json.loads(out)["groups"] == {}


def test_rpc_agent_setup_has_new_sections(proj, plan, repo):
    from research import rpc
    use(proj)
    t = mk(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    e = services.create_experiment(proj, "e", hypothesis="h")
    s = rpc.Server(str(repo))
    s.m_task_pin(t["id"], level="deep")
    hooks.add(proj, "h1", "preflight", "true")
    d = s.m_agent_setup()
    row = next(x for x in d["tasks"] if x["id"] == t["id"])
    assert row["override"] == {"level": "deep"} and row["route"]["level"] == "deep" and row["checks"] == 1
    assert d["hooks"][0]["name"] == "h1" and not d["hooks"][0]["approved"] and d["usage"]["groups"] == {}
    assert d["experiments"][0]["id"] == e["id"]
    assert s.m_hook_toggle("h1", False)["enabled"] is False
    assert s.m_task_pin(t["id"], clear=True)["override"] is None
