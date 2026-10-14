"""Named allocation strategies: resolution, isolation, dispatch, escalation, harness independence, provenance."""
import json
import os

import pytest

from research import agents, guide, harness, orchestrate as O, plans, strategy as S
from research import setup_agents as SA
from research.store import Project
from research.util import PolicyBlocked, ResearchError
from research_cli.main import main

BUILTINS = ["sonnet_first", "opus_first", "haiku_max", "sonnet_heavy"]


def use(p, name):
    p.save_config({"orchestration.active_strategy": name})
    p.config["orchestration"]["active_strategy"] = name


def mk(p, plan, title="t", **kw):
    return p.get("task", plans.create_task(p, plan["id"], title, **kw)["id"])


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


@pytest.fixture
def checked(proj, plan):
    return mk(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])


def test_builtins_resolve_to_profiles(proj):
    exp = {"sonnet_first": ("claude-haiku-5-5", "claude-sonnet-5-5"), "sonnet_heavy": ("claude-sonnet-5-5", "claude-sonnet-5-5"),
           "haiku_max": ("claude-haiku-5-5", "claude-sonnet-5-5"), "opus_first": ("claude-haiku-5-5", "claude-sonnet-5-5")}
    t = {"id": "T-1", "task_type": "implementation", "acceptance_criteria": "a", "checks": ["x"]}
    for n in BUILTINS:
        assert S.validate(proj, n)["ok"], n
        assert S.select(proj, "planner", strategy=n)["model"] == ("claude-opus-5-5" if n == "opus_first" else "claude-sonnet-5-5")
        assert S.route_task(proj, t, strategy=n)["model"] == exp[n][0]
        assert S.route_task(proj, {**t, "checks": []}, strategy=n)["model"] == exp[n][1]
    sci = {"id": "T-1", "task_type": "synthesis"}
    assert S.route_task(proj, sci, strategy="sonnet_first")["model"] == "claude-opus-5-5"
    assert S.route_task(proj, sci, strategy="haiku_max")["model"] == "claude-sonnet-5-5"  # deep only on explicit request
    assert S.route_task(proj, sci, strategy="haiku_max", level="deep")["model"] == "claude-opus-5-5"
    mech = {"id": "T-1", "task_type": "mechanical"}
    assert S.route_task(proj, mech, strategy="sonnet_heavy")["level"] == "fast"


def test_unknown_missing_cycles_rejected(proj, plan, checked):
    with pytest.raises(ResearchError, match="Unknown strategy"):
        S.select(proj, "planner", strategy="nope")
    proj.config["orchestration"]["strategies"]["a"] = {"extends": "b"}
    proj.config["orchestration"]["strategies"]["b"] = {"extends": "a"}
    assert "cycle" in S.validate(proj, "a")["errors"][0]
    proj.config["orchestration"]["strategies"]["loop"] = {"extends": "sonnet_first", "escalate": {"fast": "balanced", "balanced": "fast"}}
    assert any("escalation cycle" in e for e in S.validate(proj, "loop")["errors"])
    proj.config["orchestration"]["strategies"]["ghost"] = {"extends": "sonnet_first", "levels": {"fast": "no-such-profile"}}
    assert any("no-such-profile" in e for e in S.validate(proj, "ghost")["errors"])
    with pytest.raises(ResearchError, match="invalid"):
        S.route_task(proj, checked, strategy="ghost")
    use(proj, "sonnet_first")
    proj.config["orchestration"]["available"] = ["claude-sonnet-5-5"]  # fast model unavailable: no silent substitute
    with pytest.raises(ResearchError, match="not in orchestration.available"):
        S.route_task(proj, checked)


def test_inheritance_merges_keys_not_lists(proj, checked):
    proj.config["orchestration"]["strategies"]["mine"] = {"extends": "sonnet_first", "attempts": {"fast": 1},
                                                          "route": {"checked": "balanced"}}
    sd = S.definition(proj, "mine")
    assert sd["attempts"] == {"fast": 1, "balanced": 2} and sd["route"]["planning"] == "balanced"
    assert sd["route"]["checked"] == "balanced" and "extends" not in sd
    assert S.route_task(proj, checked, strategy="mine")["level"] == "balanced"


def test_layers_and_precedence(tmp_path, monkeypatch):
    mach = tmp_path / "machine.yaml"
    mach.write_text("orchestration:\n  strategies:\n    house: {extends: haiku_max, attempts: {fast: 7}}\n"
                    "    sonnet_first: {attempts: {fast: 5}}\n")
    monkeypatch.setenv("RESEARCH_CONFIG", str(mach))
    d = tmp_path / "pr"
    d.mkdir()
    p = Project.init(str(d), name="x")
    try:
        assert S.origin(p, "house") == "global" and S.origin(p, "opus_first") == "built-in"
        assert S.definition(p, "house")["attempts"]["fast"] == 7
        assert S.definition(p, "sonnet_first")["attempts"] == {"fast": 5, "balanced": 2}   # global overrides one key
        p.save_config({"orchestration.strategies.house.attempts.fast": 9})
        p.config = p.load_config()
        assert S.origin(p, "house") == "project" and S.definition(p, "house")["attempts"]["fast"] == 9
        r = S.select(p, "implementer", "checked", strategy="sonnet_first", level="deep", model="m-x")
        assert (r["level"], r["model"], r["overrides"]["model"]) == ("deep", "m-x", "m-x")        # explicit beats strategy
        assert S.select(p, "implementer", "checked", strategy="sonnet_first", profile="codex")["profile"] == "codex"
    finally:
        p.close()


def test_projects_are_isolated(tmp_path):
    ps = []
    for n, s in (("a", "sonnet_first"), ("b", "opus_first")):
        (tmp_path / n).mkdir()
        p = Project.init(str(tmp_path / n), name=n)
        use(p, s)
        ps.append(p)
    a, b = ps
    before = json.dumps(S.show(b), sort_keys=True)
    use(a, "haiku_max")
    a.config = a.load_config()
    b.config = b.load_config()
    assert S.active_name(a) == "haiku_max" and S.active_name(b) == "opus_first"
    assert json.dumps(S.show(b), sort_keys=True) == before
    assert "active_strategy" not in open(b.config_path).read().replace("opus_first", "") or "opus_first" in open(b.config_path).read()
    for p in ps:
        p.close()


def test_legacy_projects_unchanged(proj, checked):
    assert S.active_name(proj) is None
    r = O.route(proj, checked)
    assert r["tier"] == "haiku" and "strategy" not in r
    d = agents.dispatch(proj, "analyst", None, "x")
    assert d["policy"] is None and d["profile"] == "claude" and "RESEARCH_STRATEGY" not in d["env"]


def test_orchestrated_dispatch_snapshot_and_frozen_gate(proj, plan, checked, repo):
    use(proj, "sonnet_first")
    with pytest.raises(PolicyBlocked):
        O.dispatch_task(proj, checked["id"])
    (repo / "test_x.py").write_text("def test_a():\n    assert 1\n")
    O.freeze(proj, checked["id"], ["test_x.py"])
    d = O.dispatch_task(proj, checked["id"])
    assert d["tier"] == "fast" and "claude-haiku-5-5" in d["provider_argv"] and d["profile"] == "claude-fast"
    assert d["env"]["RESEARCH_STRATEGY"] == "sonnet_first" and d["env"]["RESEARCH_AGENT_LEVEL"] == "fast"
    snap = json.load(open(repo / d["brief"].replace(".md", ".policy.json")))
    assert snap["fingerprint"] == S.fingerprint(proj, "sonnet_first") and snap["usage"]["status"] == "unavailable"
    assert snap["task"] == checked["id"] and snap["provider"] == "claude" and snap["revision"] and snap["status"] == "prepared"
    ent = O.ledger(proj, checked["id"])["dispatches"][0]
    assert ent["strategy"] == "sonnet_first" and ent["model"] == "claude-haiku-5-5"
    # a later switch does not touch the recorded dispatch
    use(proj, "sonnet_heavy")
    assert O.ledger(proj, checked["id"])["dispatches"][0]["strategy"] == "sonnet_first"
    # one-dispatch override, no config change
    d2 = O.dispatch_task(proj, checked["id"], strategy="haiku_max")
    assert d2["route"]["strategy"] == "haiku_max" and S.active_name(proj) == "sonnet_heavy"


def test_direct_dispatch_matches_orchestrated_and_explicit_wins(proj, plan, checked, repo):
    use(proj, "sonnet_first")
    d = agents.dispatch(proj, "implementer", checked["id"], allow_unfrozen="test")
    assert d["model"] == "claude-haiku-5-5"
    assert agents.dispatch(proj, "verifier", checked["id"])["model"] == "claude-sonnet-5-5"
    assert agents.dispatch(proj, "implementer", checked["id"], profile="codex", allow_unfrozen="test")["profile"] == "codex"
    assert agents.dispatch(proj, "implementer", checked["id"], model="m-y", allow_unfrozen="test")["model"] == "m-y"
    assert agents.dispatch(proj, "planner", None, "obj")["model"] == "claude-sonnet-5-5"


def test_guide_follows_strategy(proj):
    synth = None
    from research import services as SV
    for n, model in (("sonnet_first", "claude-opus-5-5"), ("opus_first", "claude-opus-5-5")):
        use(proj, n)
        assert S.select(proj, "analyst", "scientific", level=guide._guide_level(proj))["model"] == model
    proj.config["orchestration"]["strategies"]["g"] = {"extends": "sonnet_first", "guide": "balanced"}
    assert S.select(proj, "analyst", "scientific", level=guide._guide_level(proj, "g"), strategy="g")["model"] == "claude-sonnet-5-5"


def test_delegation_uses_strategy(proj, plan):
    use(proj, "sonnet_first")
    par = mk(proj, plan, "parent")
    ch = O.delegate(proj, par["id"], "child", acceptance="a", checks=["cmd:true"])
    assert ch["assigned_role"] == "implementer" and O.route(proj, proj.get("task", ch["id"]))["level"] == "fast"
    ch2 = O.delegate(proj, par["id"], "vague child")  # not checkable → coordinator-level worker, not Haiku
    assert O.route(proj, proj.get("task", ch2["id"]))["level"] == "balanced"


def test_attempts_escalation_and_budget(proj, plan, checked):
    use(proj, "sonnet_first")
    t = checked["id"]
    assert O.record_attempt(proj, t, "fast", "fail", "incorrect_implementation")["action"] == "retry"
    O.record_attempt(proj, t, "fast", "fail", "incorrect_implementation")
    r = O.record_attempt(proj, t, "fast", "fail", "incorrect_implementation", usage={"input_tokens": 100})
    assert (r["action"], r["tier"]) == ("escalate", "balanced")
    assert O.status(proj, t)["usage"] == {"attempts_reporting": 1, "attempts": 3, "input_tokens": 100}
    assert O.record_attempt(proj, t, "fast", "pass")["action"] == "integration_review"
    with pytest.raises(ResearchError):
        O.record_attempt(proj, t, "haiku", "pass")  # legacy tier names are not levels of this strategy
    t2 = mk(proj, plan, acceptance_criteria="a", checks=["cmd:true"])["id"]
    assert O.record_attempt(proj, t2, "balanced", "fail", "scientific_assumption")["tier"] == "deep"
    use(proj, "haiku_max")
    assert O.record_attempt(proj, t2, "balanced", "fail", "scientific_assumption")["action"] == "block"
    assert proj.get("task", t2)["status"] == "blocked"


def test_frozen_tests_still_protected_under_strategy(proj, plan, checked, repo):
    use(proj, "sonnet_first")
    (repo / "t.py").write_text("a\n")
    O.freeze(proj, checked["id"], ["t.py"])
    (repo / "t.py").write_text("b\n")
    with pytest.raises(PolicyBlocked):
        plans.complete_task(proj, checked["id"], result="r")
    assert O.record_attempt(proj, checked["id"], "fast", "pass")["action"] == "restore_tests"
    with pytest.raises(PolicyBlocked):
        O.correct_test(proj, checked["id"], "t.py", "the oracle was wrong because x", "fast")
    assert O.correct_test(proj, checked["id"], "t.py", "the oracle was wrong because x", "balanced")["corrections"] == 1


def test_verifier_independence(proj, plan, repo):
    use(proj, "sonnet_heavy")  # implementer = balanced; default verifier = balanced → must not self-review
    t = mk(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    d = O.dispatch_task(proj, t["id"])
    assert d["route"]["level"] == "balanced" and d["route"]["role"] == "implementer"
    v = agents.dispatch(proj, "verifier", t["id"])
    assert v["policy"]["independent"] is True and v["model"] != "claude-sonnet-5-5"
    forced = agents.dispatch(proj, "verifier", t["id"], level="balanced")
    assert forced["policy"]["independent"] is False
    assert "NOT independent" in S.select(proj, "verifier", level="balanced", led=O.ledger(proj, t["id"]))["warning"]


def test_harness_independent_of_strategy(proj, plan, checked, repo):
    (repo / ".research" / "skills" / "pde").mkdir(parents=True)
    (repo / ".research" / "skills" / "pde" / "SKILL.md").write_text("---\nname: pde\ndescription: d\n---\nuse conservative schemes\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "NOTES.md").write_text("project notes\n")
    for k, v in (("harness.name", "numerical_research"), ("harness.skills", ["pde"]), ("harness.context", ["docs/NOTES.md"]),
                 ("harness.instructions", "docs/NOTES.md"), ("harness.checks", ["pytest tests/oracle -q"])):
        proj.save_config({k: v})
    proj.config = proj.load_config()
    fp = harness.resolve(proj)["fingerprint"]
    before = open(proj.config_path).read()
    seen = []
    for n in ("sonnet_first", "haiku_max"):
        use(proj, n)
        d = agents.dispatch(proj, "implementer", checked["id"], allow_unfrozen="test")
        brief = (repo / d["brief"]).read_text()
        assert "numerical_research" in brief and "research skill show pde" in brief and "pytest tests/oracle -q" in brief
        assert d["policy"]["harness"] == {"name": "numerical_research", "fingerprint": fp}
        seen.append(d["policy"]["strategy"])
    assert seen == ["sonnet_first", "haiku_max"] and harness.resolve(proj)["fingerprint"] == fp
    after = open(proj.config_path).read()
    assert all(line in after for line in before.splitlines() if "active_strategy" not in line and line.strip())
    proj.save_config({"harness.skills": ["pde", "ghost"]})
    proj.config = proj.load_config()
    assert S.active_name(proj) == "haiku_max" and any("ghost" in w for w in harness.warnings(proj))  # strategy untouched


def test_setup_agents_leaves_custom_agents_alone(proj, repo):
    ad = repo / ".claude" / "agents"
    ad.mkdir(parents=True)
    (ad / "pde-expert.md").write_text("custom\n")
    (ad / "research-haiku.md").write_text("user owned, no marker\n")
    SA.setup(proj, apply=True, strategy="sonnet_first")
    use(proj, "opus_first")
    SA.setup(proj, apply=True)
    assert (ad / "pde-expert.md").read_text() == "custom\n" and (ad / "research-haiku.md").read_text() == "user owned, no marker\n"
    assert S.active_name(proj) == "opus_first"


def test_records_unchanged_by_switch(proj, plan, checked):
    snap = lambda: json.dumps([proj.q("SELECT * FROM " + t) for t in ("tasks", "plans", "experiments", "findings", "runs")], default=str)
    before = snap()
    for n in BUILTINS:
        use(proj, n)
    assert snap() == before


def test_cli_strategy(proj, plan, checked, capsys, repo):
    def run(*a):
        capsys.readouterr()
        rc = main(["--json", *a]) if a[0] != "x" else 0
        return rc, json.loads(capsys.readouterr().out)
    rc, out = run("strategy", "list")
    assert rc == 0 and out["active"] is None and {s["name"] for s in out["strategies"]} >= set(BUILTINS)
    rc, out = run("strategy", "use", "haiku_max", "--dry-run")
    assert out["dry_run"] and S.active_name(Project.find(str(repo))) is None
    rc, out = run("strategy", "use", "haiku_max")
    assert out["applied"] and out["previous"] is None
    rc, out = run("strategy", "route", checked["id"])
    assert out["level"] == "fast" and out["strategy"] == "haiku_max"
    rc, out = run("strategy", "route", checked["id"], "--strategy", "sonnet_heavy")
    assert out["level"] == "balanced"
    rc, out = run("strategy", "show")
    assert out["name"] == "haiku_max" and out["validation"]["ok"]
    rc, out = run("strategy", "use", "nope")
    assert rc != 0
    rc, out = run("strategy", "validate")
    assert rc == 0


def test_config_edit_replaces_whole_list_block():
    from research import configfile as CF
    t = "harness:\n  skills:\n  - a\n  - b\n  name: n\n\n# next\nguide:\n  enabled: true\n"
    assert CF.set_keys(t, {"harness.skills": ["c"]}) == "harness:\n  skills: [c]\n  name: n\n\n# next\nguide:\n  enabled: true\n"


def test_agent_setup_rpc_and_strategy_use(proj, repo):
    from research import rpc
    (repo / ".vscode").mkdir()
    (repo / ".vscode" / "mcp.json").write_text('{"servers": {"research": {"command": "r"}, "other": {"command": "npx", "args": ["x"]}}}')
    s = rpc.Server(str(repo))
    d = s.m_agent_setup()
    assert d["strategy"] is None and {x["name"] for x in d["strategies"]} >= set(BUILTINS) and d["harness"]["configured"] is False
    reg = {c["client"]: c["registered"] for c in d["mcp"]["clients"]}
    assert reg["vscode"] and not reg["claude"] and d["mcp"]["others"][0]["name"] == "other"
    with pytest.raises(ResearchError):
        s.m_strategy_use("nope")
    assert s.m_strategy_use("opus_first", dry_run=True)["previous"] is None and s.m_agent_setup()["strategy"] is None
    s.m_strategy_use("opus_first")
    d = s.m_agent_setup()
    assert d["strategy"]["name"] == "opus_first" and any(x["active"] for x in d["strategies"])
