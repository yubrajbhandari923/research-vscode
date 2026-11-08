"""CLI behaviour that spans modules: new commands, exit codes, hooks/usage/check."""
import json
import os
import subprocess
import sys

import pytest
from research import plans
from research import runs as R
from research import services as S
from research_cli.main import main


def use(p, name="sonnet_first"):
    p.save_config({"orchestration.active_strategy": name})
    p.config = p.load_config()


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


def mk(p, plan, **kw):
    return p.get("task", plans.create_task(p, plan["id"], "t", **kw)["id"])


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV = {**os.environ, 'PYTHONPATH': os.path.join(ROOT, 'packages', 'core') + os.pathsep + os.path.join(ROOT, 'packages', 'cli')}


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


def _skill_repo(tmp_path):
    root = tmp_path / "ponytail"
    for d, name, desc in (("skills/ponytail", "ponytail", "Replace fifty lines with one."),
                          (".openclaw/skills/ponytail", "ponytail", "hidden copy"),
                          ("skills/nv-segment-ct", "nv-segment-ct", "Segment CT volumes."),
                          ("docs/examples/demo", "demo-skill", "an example elsewhere")):
        (root / d).mkdir(parents=True)
        (root / d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    (root / "skills/nv-segment-ct/scripts").mkdir()
    (root / "skills/nv-segment-ct/scripts/run.py").write_text("print(1)\n")
    return root


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


def test_cli_exit_code_reflects_failed_verification(proj, repo):
    plan = plans.create_plan(proj, 'p')
    t = plans.create_task(proj, plan['id'], 'x', checks=['cmd:false'])
    run = subprocess.run([sys.executable, '-m', 'research_cli', 'task', 'verify', t['id'], '--json'], cwd=repo, env=ENV,
                         capture_output=True, text=True)
    assert run.returncode != 0 and json.loads(run.stdout)['passed'] is False


def test_cli_new_commands(proj, repo, tmp_path, capsys):
    q, e, runs = _exp_with_runs(proj, repo)
    src = _skill_repo(tmp_path)
    assert main(["skills", "add", str(src), "--list"]) == 0
    assert main(["skills", "add", str(src), "--only", "ponytail", "--always"]) == 0
    assert main(["plan", "create", "P", "--skill", "ponytail"]) == 0
    assert main(["task", "create", "PLAN-001", "t", "--check", "file:out_0.5.png", "--check", "metric:EXP-001:err<=0.01",
                 "-e", "EXP-001"]) == 0
    assert main(["task", "start", "T-001"]) == 0
    assert main(["task", "note", "T-001", "half", "done"]) == 0
    assert main(["task", "verify", "T-001"]) == 0
    assert main(["task", "done", "T-001", "-r", "ok"]) == 0
    assert main(["finding", "create", "c", "--supports", runs[0]["id"]]) == 0
    assert main(["--agent", "codex", "finding", "review", "F-001", "--verdict", "supported", "--notes", "fine"]) == 0
    assert main(["search", "alpha"]) == 0
    assert main(["run", "compare", runs[0]["id"], runs[1]["id"]]) == 0
    assert main(["run", "sweep", "EXP-001", "--dry-run", "--", "python", "sim.py", "{alpha}"]) == 0
    assert main(["report", "--format", "md"]) == 0
    assert main(["dispatch", "verifier", "F-001", "--print"]) == 0
    assert main(["task", "dispatch", "T-001", "--print", "--profile", "codex"]) == 0
    assert main(["agents"]) == 0
    assert main(["mcp", "install", "--client", "claude"]) == 0
    assert main(["checkpoint", "--commit"]) == 0
    out = capsys.readouterr().out
    for s in ("ponytail", "passed", "T-001", "Δ", "alpha=0.1", "report:", "RESEARCH_AGENT_ROLE=verifier",
              "codex", "claude: .mcp.json", "committed"):
        assert s in out, s
    assert (repo / ".research/reports/project.md").exists()
