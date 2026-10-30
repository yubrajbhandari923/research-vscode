"""Typed configuration registry: validation, inheritance, surgical edits, approval rules, agent context."""
import json
import os
import subprocess
import sys

import pytest

from research import configfile, registry as R
from research.store import DEFAULT_CONFIG, Project
from research.util import ResearchError


def cli(cwd, *args, env=None):
    return subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def test_every_option_matches_a_real_default_and_has_real_help():
    for o in R.OPTIONS:
        present, d = configfile._get(DEFAULT_CONFIG, o.key)
        assert present, f"{o.key} is not in DEFAULT_CONFIG"
        if d is not None:
            kind = {"bool": bool, "int": int, "float": (int, float), "str": str, "path": str, "enum": str, "list": list}[o.type]
            assert isinstance(d, kind), f"{o.key} default {d!r} is not {o.type}"
        assert len(o.help) > 40 and o.help.lower().rstrip(".") != o.label.lower(), f"{o.key}: help must say more than the label"
        assert o.why and o.effect, f"{o.key} must say why it exists and what changing it does"
        assert o.category in dict(R.CATEGORIES)


@pytest.mark.parametrize("key,val,ok,norm", [
    ("agent_policy.max_runs_without_synthesis", "7", True, 7),
    ("agent_policy.max_runs_without_synthesis", "0", False, None),
    ("agent_policy.max_runs_without_synthesis", "2.5", False, None),
    ("agent_policy.max_runs_without_synthesis", "many", False, None),
    ("agent_policy.enforce_for_humans", "yes", True, True),
    ("agent_policy.enforce_for_humans", "maybe", False, None),
    ("project.default_backend", "slurm", True, "slurm"),
    ("project.default_backend", "cloud", False, None),
    ("skills.link", "claude,agents", True, ["claude", "agents"]),
    ("skills.link", "bogus", False, None),
    ("orchestration.active_strategy", "", True, None),
    ("sandbox.agent.mode", "none", True, "none"),          # a literal enum value, not null
    ("project.goal", "", True, ""),
    ("agent_policy.max_runs_without_synthesis", "", False, None),
    ("backends.slurm.time", "04:00:00", True, "04:00:00"),
    ("backends.slurm.time", "soon", False, None),
])
def test_validation(key, val, ok, norm):
    good, v, err = R.validate(key, val)
    assert good is ok and (v == norm if ok else bool(err))


def test_unknown_key_is_refused():
    with pytest.raises(ResearchError):
        R.get("nope.nothing")


def test_describe_shows_default_value_source_and_machine_inheritance(proj, tmp_path, monkeypatch):
    d = {x["key"]: x for x in R.describe(proj)["options"]}
    x = d["agent_policy.max_runs_without_synthesis"]
    assert x["value"] == x["default"] == 5 and x["source"] == "default" and x["inherited"]
    mc = tmp_path / "machine.yaml"
    mc.write_text("agent_policy:\n  max_runs_without_synthesis: 9\n")
    monkeypatch.setenv("RESEARCH_CONFIG", str(mc))
    proj.config = proj.load_config()
    x = {y["key"]: y for y in R.describe(proj)["options"]}["agent_policy.max_runs_without_synthesis"]
    assert x["value"] == 9 and x["source"] == "machine" and x["inherited"]


def test_apply_edits_one_line_keeps_comments_audits_and_regenerates_policy(proj):
    path = proj.config_path
    with open(path, "a") as f:
        f.write("\n# my note about limits\nagent_policy:\n  max_runs_without_synthesis: 5   # tuned for CT\n  enforce_for_humans: false\n")
    proj.config = proj.load_config()
    before = open(path).read()
    pv = R.preview(proj, {"agent_policy.max_runs_without_synthesis": "8"})
    assert open(path).read() == before and "+  max_runs_without_synthesis: 8" in pv["diff"]
    # a human changes it
    r = R.apply(proj, {"agent_policy.max_runs_without_synthesis": "8"}, reason="longer sweeps")
    after = open(path).read()
    assert "# my note about limits" in after and "# tuned for CT" in after and "enforce_for_humans: false" in after
    assert after.count("\n") == before.count("\n") and "max_runs_without_synthesis: 8" in after
    assert proj.config["agent_policy"]["max_runs_without_synthesis"] == 8
    ev = proj.conn.execute("SELECT summary FROM events WHERE entity_type='config' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "5" in ev and "8" in ev and "longer sweeps" in ev
    policy = open(proj.path("context", "policy.md")).read()
    assert "Maximum runs without synthesis" in policy and "8" in policy and r["fingerprint"] in policy


def test_reset_removes_only_the_override(proj):
    R.apply(proj, {"agent_policy.max_runs_without_synthesis": "8"})
    R.apply(proj, {"verification.check_timeout_seconds": "30"})
    R.reset(proj, ["agent_policy.max_runs_without_synthesis"])
    assert proj.config["agent_policy"]["max_runs_without_synthesis"] == 5
    assert proj.config["verification"]["check_timeout_seconds"] == 30
    assert not any("max_runs" in l for l in open(proj.config_path).read().splitlines() if not l.startswith("#"))
    d = {x["key"]: x for x in R.describe(proj)["options"]}
    assert d["agent_policy.max_runs_without_synthesis"]["source"] == "default"


def test_invalid_change_writes_nothing(proj):
    before = open(proj.config_path).read()
    with pytest.raises(ResearchError):
        R.apply(proj, {"agent_policy.max_runs_without_synthesis": "-3"})
    assert open(proj.config_path).read() == before


def test_agents_cannot_change_policy_but_can_change_ordinary_settings(repo):
    from research.util import detect_author
    p = Project.init(str(repo), name="d")
    p.author = detect_author("claude", None, None)
    assert p.author["author_type"] == "agent"
    with pytest.raises(ResearchError, match="Agents cannot change project policy"):
        R.apply(p, {"agent_policy.max_runs_without_synthesis": "99"})
    assert R.preview(p, {"agent_policy.max_runs_without_synthesis": "99"})["blocked_for_agents"]
    R.apply(p, {"context.max_chars": "20000"})
    assert p.config["context"]["max_chars"] == 20000
    p.close()


def test_risky_options_need_explicit_confirmation(proj):
    R.register(R.Option("context.max_chars", "int", "context", "Context size budget", "x" * 50, why="w", effect="e", risk="risky", min_=1))
    try:
        with pytest.raises(ResearchError, match="Confirmation required"):
            R.apply(proj, {"context.max_chars": "5000"})
        R.apply(proj, {"context.max_chars": "5000"}, confirm=True)
        assert proj.config["context"]["max_chars"] == 5000
    finally:
        import importlib
        importlib.reload(R)


def test_machine_scope_writes_the_machine_file(proj, tmp_path, monkeypatch):
    mc = tmp_path / "cfg" / "machine.yaml"
    monkeypatch.setenv("RESEARCH_CONFIG", str(mc))
    R.apply(proj, {"backends.slurm.partition": "gpu"}, scope="machine")
    assert "partition: gpu" in mc.read_text()
    d = {x["key"]: x for x in R.describe(proj)["options"]}["backends.slurm.partition"]
    assert d["value"] == "gpu" and d["source"] == "machine"


def test_dispatch_brief_carries_policy_pointer_and_fingerprint_changes(proj):
    from research import agents, plans
    fp = R.fingerprint(proj)
    pl = plans.create_plan(proj, "p", objective="o")
    t = plans.create_task(proj, pl["id"], "t", goal="g")
    d = agents.dispatch(proj, "implementer", t["id"])
    assert "policy.md" in open(proj.abspath(d["brief"])).read()
    R.apply(proj, {"agent_policy.max_runs_without_synthesis": "9"})
    assert R.fingerprint(proj) != fp
    # the earlier dispatch's recorded policy snapshot is untouched
    snap = json.load(open(proj.abspath(d["brief"])[:-3] + ".policy.json")) if d.get("policy") else {}
    assert snap.get("settings_fingerprint", fp) == fp


def test_agents_md_points_at_the_policy_file(proj):
    proj.write_agents_md()
    assert ".research/context/policy.md" in open(os.path.join(proj.root, "AGENTS.md")).read()


def test_cli_set_explain_reset_and_dry_run(repo):
    Project.init(str(repo), name="d").close()
    r = cli(repo, "config", "set", "agent_policy.max_runs_without_synthesis", "6", "--dry-run")
    live = lambda: [l for l in open(repo / ".research" / "config.yaml").read().splitlines() if l.strip() and not l.startswith("#")]
    assert r.returncode == 0 and "6" in r.stdout and not any("max_runs" in l for l in live())
    assert cli(repo, "config", "set", "agent_policy.max_runs_without_synthesis", "6").returncode == 0
    x = json.loads(cli(repo, "config", "explain", "agent_policy.max_runs_without_synthesis", "--json").stdout)
    assert x["value"] == 6 and x["source"] == "project" and x["default"] == 5 and x["approval"]
    bad = cli(repo, "config", "set", "agent_policy.max_runs_without_synthesis", "zero")
    assert bad.returncode != 0
    assert cli(repo, "config", "reset", "agent_policy.max_runs_without_synthesis").returncode == 0
    x = json.loads(cli(repo, "config", "explain", "agent_policy.max_runs_without_synthesis", "--json").stdout)
    assert x["value"] == 5 and x["source"] == "default"
    # an agent environment is refused
    r = cli(repo, "config", "set", "agent_policy.max_runs_without_synthesis", "99", env={"RESEARCH_AGENT": "claude"})
    assert r.returncode == 3 or "Agents cannot" in (r.stderr + r.stdout)


def test_no_registered_option_reports_a_problem_with_its_own_default(proj):
    bad = [(x["key"], x["errors"]) for x in R.describe(proj)["options"] if x["errors"]]
    assert not bad, bad


def test_profile_and_strategy_settings_offer_the_names_that_exist(proj):
    d = {x["key"]: x for x in R.describe(proj)["options"]}
    assert d["sandbox.default_profile"]["type"] == "enum" and "restricted" in d["sandbox.default_profile"]["choices"]
    assert d["orchestration.active_strategy"]["type"] == "enum" and d["orchestration.active_strategy"]["choices"]
