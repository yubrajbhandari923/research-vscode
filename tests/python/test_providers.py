"""Provider adapters and launch semantics, using mock provider executables (no real agent is ever run)."""
import json
import os
import stat
import subprocess
import sys

import pytest

from research import agents, execution, plans, providers, services
from research import workspace as W
from research.store import Project
from test_workspace import shared  # noqa: F401  (fixture)

MOCK = """#!{py}
import json, os, sys
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, open(os.environ["MOCK_OUT"], "w"))
brief = os.environ["RESEARCH_BRIEF"]
assert os.path.isfile(brief), brief
sys.exit(int(os.environ.get("MOCK_EXIT", "0")))
"""


@pytest.fixture
def mocks(tmp_path, monkeypatch):
    bindir = tmp_path / "mockbin"
    bindir.mkdir()
    for n in ("claude", "codex", "agy"):
        f = bindir / n
        f.write_text(MOCK.format(py=sys.executable))
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "out.json"
    monkeypatch.setenv("MOCK_OUT", str(out))
    return bindir, out


def _task(p):
    e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
    ws = W.create(p, e["id"])
    pl = plans.create_plan(p, "plan", objective="o")
    t = plans.create_task(p, pl["id"], "do it", goal="g")
    W.link_task(p, ws["id"], t["id"])
    return t, ws


def test_argv_per_provider():
    c = providers.build_argv({"command": "claude"}, "claude", "m1", "P", "/w", ["/l"], True)
    assert c == ["claude", "-p", "--output-format", "json", "--add-dir", "/l", "--allowedTools",
                 "Bash(research *),Bash(.research/bin/research *),Bash(./.research/bin/research *),Bash(*/.research/bin/research *),Bash(cd * && research *),Bash(cd * && .research/bin/research *),Bash(cd * && ./.research/bin/research *),mcp__research", "--permission-mode", "acceptEdits", "--model", "m1", "P"]
    assert "--allowedTools" not in providers.build_argv({"command": "claude"}, "claude", "m1", "P", "/w", [], False)   # interactive asks the human
    x = providers.build_argv({"command": "codex"}, "codex", "gpt", "P", "/w", ["/l"], True, "high")
    assert x[:3] == ["codex", "exec", "--json"] and "-C" in x and "/w" in x and "danger-full-access" not in x
    assert ["-m", "gpt"] == x[x.index("-m"):x.index("-m") + 2] and 'model_reasoning_effort="high"' in x
    a = providers.build_argv({"command": "agy"}, "agy", "g", "P", "/w", [], False, "low")
    assert a == ["agy", "--model", "g", "--effort", "low", "-i", "P"]
    with pytest.raises(Exception):
        providers.build_argv({"command": "x"}, "x", None, "P", "/w", [], True)  # custom, no headless_args


@pytest.mark.parametrize("prov", ["claude", "codex", "agy"])
def test_launch_runs_in_the_worktree_and_brief_is_readable(shared, mocks, monkeypatch, prov):  # noqa: F811
    repo, _ = shared
    bindir, out = mocks
    with Project(str(repo)) as p:
        t, ws = _task(p)
        p.config.setdefault("agents", {}).setdefault("profiles", {})[prov] = {"provider": prov, "command": str(bindir / prov)}
        d = agents.dispatch(p, "implementer", t["id"], profile=prov, model="mdl")
        assert d["cwd"] == ws["path"]
        rc = subprocess.run(d["argv"], env={**os.environ, **d["env"]}, cwd=str(repo)).returncode  # wrapper must pick the cwd itself
    assert rc == 0
    seen = json.load(open(out))
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(ws["path"])
    assert "mdl" in seen["argv"] and os.path.isabs(d["env"]["RESEARCH_BRIEF"])
    with Project(str(repo)) as p:
        rec = json.load(open(execution.path(p, d["execution_id"])))
    assert rec["state"] == "completed" and rec["exit_code"] == 0


def test_failed_provider_is_failed_not_completed_and_prepared_is_single_use(shared, mocks, monkeypatch):  # noqa: F811
    repo, _ = shared
    bindir, out = mocks
    monkeypatch.setenv("MOCK_EXIT", "5")
    with Project(str(repo)) as p:
        t, _ws = _task(p)
        p.config.setdefault("agents", {}).setdefault("profiles", {})["codex"] = {"provider": "codex", "command": str(bindir / "codex")}
        d = agents.dispatch(p, "implementer", t["id"], profile="codex")
        env = {**os.environ, **d["env"]}
        assert subprocess.run(d["argv"], env=env).returncode == 5
        again = subprocess.run(d["argv"], env=env, capture_output=True, text=True)
        rec = json.load(open(execution.path(p, d["execution_id"])))
    assert rec["state"] == "failed" and again.returncode == 3 and "already been consumed" in again.stderr


# ---------------------------------------------------------------------------------- lifecycle, concurrency, gates
import time

from research import execution as EX
from research.util import PolicyBlocked, ResearchError

SLOW = MOCK.replace("sys.exit(", "import time; time.sleep(float(os.environ.get('MOCK_SLEEP', '0')))\nsys.exit(")


def _profile(p, bindir, prov, body=None, **kw):
    if body:
        f = bindir / prov
        f.write_text(body.format(py=sys.executable) if "{py}" in body else body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    p.config.setdefault("agents", {}).setdefault("profiles", {})[prov] = {"provider": prov, "command": str(bindir / prov), **kw}


def test_headless_captures_logs_and_a_second_launch_of_the_same_task_is_refused(shared, mocks, monkeypatch):  # noqa: F811
    repo, _ = shared
    bindir, out = mocks
    monkeypatch.setenv("MOCK_SLEEP", "3")
    with Project(str(repo)) as p:
        t, ws = _task(p)
        _profile(p, bindir, "codex", SLOW.replace("sys.exit(", "print('hello-out'); print('oops', file=sys.stderr); sys.exit(", 1))
        d1 = agents.dispatch(p, "implementer", t["id"], profile="codex", headless=True)
        d2 = agents.dispatch(p, "implementer", t["id"], profile="codex", headless=True)
        env = lambda d: {**os.environ, **d["env"]}  # noqa: E731
        first = subprocess.Popen(d1["argv"], env=env(d1))
        for _ in range(50):
            if EX.show(p, d1["execution_id"])["state"] == "running":
                break
            time.sleep(0.1)
        run = EX.show(p, d1["execution_id"])
        assert run["state"] == "running" and run["alive"] and run["mode"] == "headless"
        second = subprocess.run(d2["argv"], env=env(d2), capture_output=True, text=True)
        assert second.returncode != 0 and EX.show(p, d2["execution_id"])["state"] in ("prepared",)
        assert first.wait() == 0
        lg = EX.logs(p, d1["execution_id"])
        assert "hello-out" in lg["out"] and "oops" in lg["err"]
        assert EX.show(p, d1["execution_id"])["state"] == "completed"


def test_stop_marks_cancelled_not_failed(shared, mocks, monkeypatch):  # noqa: F811
    repo, _ = shared
    bindir, _out = mocks
    monkeypatch.setenv("MOCK_SLEEP", "30")
    with Project(str(repo)) as p:
        t, _ = _task(p)
        _profile(p, bindir, "agy", SLOW)
        d = agents.dispatch(p, "implementer", t["id"], profile="agy", headless=True)
        proc = subprocess.Popen(d["argv"], env={**os.environ, **d["env"]})
        for _ in range(50):
            if EX.show(p, d["execution_id"])["state"] == "running":
                break
            time.sleep(0.1)
        EX.stop(p, d["execution_id"])
        proc.wait(timeout=20)
        assert EX.show(p, d["execution_id"])["state"] == "cancelled"


def test_missing_executable_is_a_diagnosed_failure_and_dispatch_names_it(shared, mocks):  # noqa: F811
    repo, _ = shared
    bindir, _ = mocks
    with Project(str(repo)) as p:
        t, _ = _task(p)
        p.config["agents"]["profiles"]["codex"] = {"provider": "codex", "command": str(bindir / "nope-codex")}
        d = agents.dispatch(p, "implementer", t["id"], profile="codex")
        assert "not installed" in d["problem"] and d["available"] is False
        r = subprocess.run(d["argv"], env={**os.environ, **d["env"]}, capture_output=True, text=True)
        assert r.returncode == 127 and "could not start" in r.stderr
        assert EX.show(p, d["execution_id"])["state"] == "failed"


def test_continue_with_another_provider_keeps_records_and_gates_still_apply(shared, mocks, monkeypatch):  # noqa: F811
    repo, _ = shared
    bindir, out = mocks
    with Project(str(repo)) as p:
        t, ws = _task(p)
        plans.update_task(p, t["id"], notes="claude found X") if hasattr(plans, "update_task") else None
        _profile(p, bindir, "claude")
        _profile(p, bindir, "codex")
        d1 = agents.dispatch(p, "implementer", t["id"], profile="claude")
        assert subprocess.run(d1["argv"], env={**os.environ, **d1["env"]}).returncode == 0
        d2 = agents.redispatch(p, d1["execution_id"], profile="codex", model="gpt-x")
        assert d2["allocation"]["provider"] == "codex" and d2["allocation"]["model"] == "gpt-x" and d2["target"] == t["id"]
        assert d2["cwd"] == ws["path"]                      # same worktree, same task, same ledger
        assert d2["execution_id"] != d1["execution_id"]
        # switching provider does not bypass the skill gate
        with pytest.raises(PolicyBlocked):
            agents.dispatch(p, "implementer", t["id"], profile="codex", require_skills=["no-such-skill"])
        # a running/prepared execution cannot be redispatched under another provider
        with pytest.raises(ResearchError):
            agents.redispatch(p, d2["execution_id"], profile="agy")


def test_cli_dispatch_really_launches_in_the_worktree(shared, mocks, monkeypatch):  # noqa: F811
    repo, _ = shared
    bindir, out = mocks
    with Project(str(repo)) as p:
        t, ws = _task(p)
        _profile(p, bindir, "codex")
        p.save_config({"agents.profiles.codex.provider": "codex", "agents.profiles.codex.command": str(bindir / "codex")})
    r = subprocess.run([sys.executable, "-m", "research_cli", "dispatch", "implementer", t["id"], "--profile", "codex", "--agent-model", "m9"],
                       cwd=str(repo), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    seen = json.load(open(out))
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(ws["path"]) and "m9" in seen["argv"] and seen["argv"][0] == "-C" or "-C" in seen["argv"]


def test_rpc_providers_and_prepare_never_runs(shared, mocks):  # noqa: F811
    from research import api
    repo, _ = shared
    bindir, out = mocks
    sess = api.Session(str(repo))
    c = lambda op_, **kw: api.call(op_, sess, kw)
    with Project(str(repo)) as p:
        t, _ws = _task(p)
        p.save_config({"agents.profiles.codex.command": str(bindir / "codex"), "agents.profiles.codex.provider": "codex"})
    pv = c("providers")
    names = {x["name"]: x for x in pv["providers"]}
    assert {"claude", "codex", "agy"} <= set(names) and names["codex"]["installed"] and pv["default"] == "claude"
    out.unlink(missing_ok=True)   # the --version probe above ran the mock; only the dispatch is under test
    d = c("dispatch", role="implementer", target=t["id"], profile="codex", model="m1", effort="high")
    assert d["state"] == "prepared" and d["allocation"]["reasoning_effort"] == "high" and not out.exists()   # prepared, not started
    assert c("executions", task=t["id"])[0]["state"] == "prepared"
    with pytest.raises(ResearchError):
        c("execution_start", id=d["execution_id"])   # interactive executions are started by a terminal, not the server
    h = c("dispatch", role="implementer", target=t["id"], profile="codex", headless=True)
    c("execution_start", id=h["execution_id"])
    for _ in range(80):
        if c("execution_show", id=h["execution_id"])["state"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert c("execution_show", id=h["execution_id"])["state"] == "completed" and out.exists()


def test_cross_provider_strategy_routes_levels_to_different_providers(shared, mocks):  # noqa: F811
    repo, _ = shared
    bindir, _ = mocks
    with Project(str(repo)) as p:
        t, ws = _task(p)
        plans.update_task(p, t["id"], checks=["file:x"]) if hasattr(plans, "update_task") else None
        p.config["agents"]["profiles"]["codex-fast"] = {"provider": "codex", "command": str(bindir / "codex"), "model": "gpt-fast", "effort": "low"}
        p.config["orchestration"]["strategies"]["mixed"] = {"extends": "sonnet_first", "levels": {"fast": "codex-fast"},
                                                           "route": {"checked": "fast", "unchecked": "fast"}}
        with pytest.raises(PolicyBlocked):   # the frozen-acceptance-test gate applies to Codex exactly as it did to Claude
            agents.dispatch(p, "implementer", t["id"], strategy="mixed")
        d = agents.dispatch(p, "implementer", t["id"], strategy="mixed", allow_unfrozen="test")
        al = d["allocation"]
        assert (al["provider"], al["model"], al["strategy"], al["reasoning_effort"]) == ("codex", "gpt-fast", "mixed", "low")
        assert "model_reasoning_effort=\"low\"" in " ".join(d["provider_argv"])
        # the planner still resolves to Claude under the same strategy: routing is per task class, not per project
        assert agents.dispatch(p, "planner", None, objective="x", strategy="mixed")["allocation"]["provider"] == "claude"


def test_models_listed_per_provider_only():
    profs = {"claude-x": {"command": "claude", "model": "sonnet-x"}, "codex-x": {"provider": "codex", "command": "codex", "model": "gpt-x"}}
    assert [m["id"] for m in providers.available_models("codex", "codex-not-installed", profs)["models"]] == ["gpt-x"]
    assert [m["id"] for m in providers.available_models("claude", "claude", profs)["models"]] == ["sonnet-x"]


def test_headless_timeout_is_failure_and_missing_executable_is_controlled(proj):
    from research import agents
    dispatch = {'argv': [sys.executable, '-c', 'import time; time.sleep(5)'], 'env': {},
                'headless': True, 'usage_parser': None, 'policy': {}, 'role': 'implementer',
                'profile': 'test', 'model': None}
    result = agents.run_headless(proj, dispatch, timeout=0.05)
    assert result['timed_out'] and result['exit_code'] == 124
    dispatch['argv'] = ['/definitely/missing/research-agent']
    with pytest.raises(ResearchError, match='Could not start agent'):
        agents.run_headless(proj, dispatch)


def test_detect_reports_resume_from_the_installed_cli_not_the_static_table(monkeypatch):
    # claude's static table says resume False, yet an installed CLI that lists --resume supports it; a static True is not trusted either
    from research import providers
    monkeypatch.setattr(providers.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(providers, "_RESUME_PROBE", {})
    import dataclasses
    for name, answer in (("claude", True), ("codex", False)):                           # adapters are frozen records: swap in modified copies
        monkeypatch.setitem(providers.ADAPTERS, name, dataclasses.replace(providers.ADAPTERS[name], resume_probe=lambda c, a=answer: a))
    assert providers.detect({"command": "claude"}, "claude")["capabilities"]["resume"] is True
    assert providers.detect({"command": "codex"}, "codex")["capabilities"]["resume"] is False
    monkeypatch.setattr(providers.shutil, "which", lambda c: None)                      # not installed: nothing to probe, the table stands
    assert providers.detect({"command": "claude"}, "claude")["capabilities"]["resume"] is False
