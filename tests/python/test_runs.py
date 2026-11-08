"""Runs: search, compare, sweep, checkpoint."""
import subprocess
import sys

import pytest
from conftest import as_agent
from research import compare as CMP
from research import context as C
from research import runs as R
from research import services as S
from research.search import search
from research.util import BudgetExceeded
from conftest import git


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


def test_search_and_current_budget(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    S.create_finding(proj, "Entropy regularisation reduces mixing", statement="Mixing drops 40% with entropy", supports=[runs[0]["id"]])
    S.synthesize(proj, e["id"], interpretation="entropy helps a lot")
    hits = search(proj, "entropy")
    assert hits[0]["id"] == "F-001" and {h["type"] for h in hits} >= {"finding", "synthesis"}
    assert search(proj, "entropy mixing", types=["finding"])[0]["snippet"]
    assert search(proj, "nonexistentword") == []
    for i in range(40):
        S.create_finding(proj, f"finding number {i} " + "x" * 120, supports=[runs[0]["id"]])
    proj.config["context"]["max_chars"] = 4000
    md = C.render_current_md(proj)
    assert len(md) <= 4000 and "more (`research finding list`)" in md and "{TOKENS}" not in md


def test_compare_runs(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    c = CMP.compare_runs(proj, runs[0]["id"], runs[1]["id"])
    assert c["changed_parameters"] == ["alpha"]
    err = next(m for m in c["metrics"] if m["name"] == "err")
    assert err["a"] == pytest.approx(0.4) and err["b"] == 0 and err["delta"] == pytest.approx(-0.4)
    assert {x["name"] for x in c["artifacts"]} == {"out_0.1.png", "out_0.5.png"} and c["same_commit"]


def test_sweep_budget_and_runs(proj, repo, monkeypatch):
    q = S.create_question(proj, "q")
    e = S.create_experiment(proj, "grid", question=q["id"], parameters={"a": [1, 2, 3], "fixed": 7}, planned_runs=4)
    (repo / "p.py").write_text("import sys; print(sys.argv[1:])\n")
    dry = R.sweep_run(proj, e["id"], f"{sys.executable} p.py {{a}} {{fixed}}", dry_run=True)
    assert [x["command"].split()[-2:] for x in dry["points"]] == [["1", "7"], ["2", "7"], ["3", "7"]]
    assert dry["points"][0]["label"] == "a=1" and dry["points"][0]["parameters"] == {"fixed": 7, "a": 1}
    as_agent(proj, "claude", None, monkeypatch)
    with pytest.raises(BudgetExceeded, match="planned: 4"):
        R.sweep_run(proj, e["id"], "true", grid={"a": [1, 2, 3, 4, 5]})
    with pytest.raises(BudgetExceeded, match="run budget"):
        proj.config["agent_policy"]["max_runs_without_synthesis"] = 2
        R.sweep_run(proj, e["id"], "true", dry_run=True)
    proj.config["agent_policy"]["max_runs_without_synthesis"] = 5
    res = R.sweep_run(proj, e["id"], f"{sys.executable} p.py {{a}}", tee=False)
    assert [r["status"] for r in res["runs"]] == ["completed"] * 3
    assert [r["label"] for r in res["runs"]] == ["a=1", "a=2", "a=3"]


def test_checkpoint_commit_only_research(proj, repo):
    (repo / "staged.txt").write_text("keep me staged\n")
    git(repo, "add", "staged.txt")
    c = C.create_checkpoint(proj, current_problem="none", commit=True)
    assert c["snapshot_commit"]
    files = subprocess.run(["git", "show", "--name-only", "--pretty=", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert files.strip() and all(f.startswith(".research/") for f in files.split())
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert "A  staged.txt" in status and ".research" not in status  # nothing of ours left dirty
    C.write_current_md(proj)  # HEAD changed; regenerating must not dirty current.md
    assert ".research" not in subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert C.create_checkpoint(proj, commit=False)["snapshot_commit"] is None
