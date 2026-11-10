"""Git hygiene through the supervisor: the plan preflight, verified work on the experiment branch (and what stays out of it), and dirty-tree recovery before promotion."""
import os
import subprocess

import pytest

from research import githygiene as GH, planspec as PS, promotion as PR, supervisor as SV
from research.util import PolicyBlocked
from test_supervisor import lproj, phases, run_until, task_yaml, yaml_plan, subprocess_out  # noqa: F401


def write(root, name, text="x\n"):
    path = os.path.join(str(root), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


WORK = ("mkdir -p tests outputs reports __pycache__; echo 'X = 1' > mod.py; echo 'def test_x(): pass' > tests/test_mod.py; echo '# result' > reports/r.md; "
        "echo png > outputs/fig.png; echo big > outputs/data.bin; echo cache > __pycache__/m.pyc; echo TOKEN=1 > .env")


def test_plan_run_reports_uncommitted_files_and_continues_by_default(lproj, repo):
    write(repo, "train.py", "print('edited, not committed')\n")
    write(repo, "plans/mine.yaml", "schema_version: 1\nplans:\n  - key: a\n")
    PS.apply(lproj, yaml_plan([task_yaml("a", "A", "true")]))
    r = SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert r["state"] == "running"
    why = " ".join(i["why"] for i in r["git_preflight"]["issues"])
    assert "will NOT see these edits" in why and any("untracked plan" in i["what"] for i in r["git_preflight"]["issues"])
    assert any(l.startswith("Git preflight (plan)") for l in GH.lines(r["git_preflight"]))


def test_plan_run_and_resume_are_refused_when_git_preflight_is_block(lproj, repo):
    lproj.save_config({"git.preflight": "block"})
    lproj.config = lproj.load_config()
    write(repo, "train.py", "print('edited')\n")
    PS.apply(lproj, yaml_plan([task_yaml("a", "A", "true")]))
    with pytest.raises(PolicyBlocked) as e:
        SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert "uncommitted" in str(e.value) and SV.status(lproj, "demo-plan")["state"] == "not_run"
    GH.commit(lproj, paths=["train.py"], scaffold=True, confirm=True)                       # reviewed and committed (with the scaffolding): now it starts
    assert SV.plan_run(lproj, "demo-plan", start_daemon=False)["state"] == "running"
    SV.plan_pause(lproj, "demo-plan")
    write(repo, "train.py", "print('edited again')\n")
    with pytest.raises(PolicyBlocked):
        SV.plan_resume(lproj, "demo-plan")


def test_verified_work_is_committed_on_the_branch_with_a_safe_selection_and_main_stays_clean(lproj, repo):
    PS.apply(lproj, yaml_plan([task_yaml("a", "Add module", WORK, checks=["file:mod.py"])]))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj)["a"] == "verified", 90)
    st = PS.load_state(lproj, "demo-plan")
    a = SV.load_run(lproj, st["plan_id"])["tasks"][st["tasks"]["a"]]["attempts"][0]
    files = set(subprocess_out(["git", "show", "--name-only", "--format=", a["commit"]], a["cwd"]).split())
    assert files == {"mod.py", "tests/test_mod.py", "reports/r.md", "outputs/fig.png"}          # code, tests, report and the small figure; no data, cache or secret
    skipped = {s["path"]: s["why"] for s in a["commit_report"]["skipped"]}
    assert skipped[".env"] == "secret" and skipped["outputs/data.bin"].startswith("excluded") and skipped["__pycache__/m.pyc"] == "generated"
    assert subprocess_out(["git", "status", "--porcelain", "--untracked-files=no"], str(repo)).strip() == ""      # main untouched
    assert not os.path.exists(os.path.join(repo, "mod.py"))


def test_dirty_tree_recovery_then_promotion_reaches_main(lproj, repo):
    lproj.save_config({"governance.approval.require_pin": False})
    lproj.config = lproj.load_config()
    PS.apply(lproj, yaml_plan([task_yaml("a", "Add module", "echo 'X = 1' > mod.py", checks=["file:mod.py"])]))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj)["a"] == "verified", 90)
    write(repo, "train.py", "print('my unfinished edit')\n")                                   # the researcher edited main meanwhile
    pf = GH.preflight(lproj, "promotion")
    assert not pf["ok"] and "fast-forwards main" in pf["blocking"][0]["why"] and "train.py" in pf["blocking"][0]["files"]
    GH.commit(lproj, paths=["train.py"], message="Finish the edit", confirm=True)             # reviewed, scoped, on main: committing first keeps main fast-forwardable
    assert GH.preflight(lproj, "promotion")["ok"]
    pid = SV.promote_plan(lproj, "demo-plan")["candidates"][0]["id"]
    PR.verify(lproj, pid, "test -f mod.py")
    PR.review(lproj, pid, "approve", "reviewed")
    PR.approve(lproj, pid, "ship it")
    PR.integrate(lproj, pid, confirm=True)
    assert open(os.path.join(repo, "mod.py")).read().strip() == "X = 1"
    assert "my unfinished edit" in open(os.path.join(repo, "train.py")).read()
    assert subprocess_out(["git", "remote"], str(repo)).strip() == ""                          # nothing was pushed
