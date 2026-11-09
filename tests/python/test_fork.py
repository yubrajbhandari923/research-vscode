"""Experiment forking with pinned code inheritance: real git, real supervisor ticks, a scripted agent. No promotion is configured anywhere."""
import subprocess
import sys
import textwrap

import os

import pytest

from conftest import git
from research import fork as FK, planspec as PS, runs as RUNS, services, supervisor as SV, workspace as W
from research.store import Project
from research.util import ResearchError
from test_autopromote import ap, items  # noqa: F401  (ap: fixture with auto-promotion configured)
from test_supervisor import MOCK, run_until


@pytest.fixture
def fk(repo, tmp_path):
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.save_config()
    yield p
    p.close()


def task(key, shell, deps=(), checks=()):
    d = f"depends_on: [{', '.join(deps)}], " if deps else ""
    c = f"checks: [{', '.join(repr(x) for x in checks)}], " if checks else ""
    return f'{{key: {key}, title: T {key}, {d}{c}agent: {{role: implementer, profile: fake}}, prompt: "Work.\\nMOCK_SH: {shell}"}}'


def plan(exps, key="fk", auto=False):
    body = ""
    for ek, tasks, inh in exps:
        i = f"        inherits: {inh}\n" if inh else ""
        body += f"      - key: {ek}\n        title: Exp {ek}\n        hypothesis: h {ek}\n{i}        tasks:\n" + "".join(f"          - {t}\n" for t in tasks)
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: {key}
            title: Fork
            objective: o
            execution: {{max_parallel_agents: 2, max_attempts_per_task: 1{', auto_promotion: true' if auto else ''}}}
            experiments:
        """) + body


BASE = ("baseline", [task("b", "echo V=1 > base.py", checks=["file:base.py"])], None)
A = ("method-a", [task("a", "test -f base.py && echo V=2 > base.py && echo A > a.py", checks=["file:a.py"])], "{experiment: baseline, revision: verified}")
B = ("method-b", [task("c", "grep -q V=1 base.py && test ! -f a.py && echo B > b.py", checks=["file:b.py"])], "{experiment: baseline}")


def done(p, key="fk"):
    st = PS.load_state(p, key)
    return lambda: all(p.get("task", t)["status"] == "done" for t in st["tasks"].values())


def wt(p, exp):
    return next(W.show(p, r["id"])["path"] for r in W._fold(W._records(p)).values() if r.get("experiment") == exp)


def test_siblings_fork_independently_from_the_pinned_baseline_while_main_moves(fk, repo):
    PS.apply(fk, plan([BASE, A, B]))
    st = PS.load_state(fk, "fk")
    SV.plan_run(fk, "fk", start_daemon=False)
    assert run_until(fk, done(fk), 120)
    e1, e2, e3 = (st["experiments"][k] for k in ("baseline", "method-a", "method-b"))
    pin2, pin3 = FK.inherited(fk, e2), FK.inherited(fk, e3)
    assert pin2["base_rev"] == pin3["base_rev"] and pin2["parent"] == e1
    # EXP-002 changed the baseline file; EXP-003 and EXP-001 still have the original; nothing reached main
    assert (open(f"{wt(fk, e2)}/base.py").read(), open(f"{wt(fk, e3)}/base.py").read(), open(f"{wt(fk, e1)}/base.py").read()) == ("V=2\n", "V=1\n", "V=1\n")
    assert not (repo / "base.py").exists()
    assert subprocess.run(["git", "branch", "--contains", pin2["base_rev"]], cwd=repo, capture_output=True, text=True).stdout.count("main") == 0
    assert FK.inherited(fk, e1) is None


def test_child_waits_for_the_parent_and_survives_a_restart(fk):
    PS.apply(fk, plan([BASE, A]))
    st = PS.load_state(fk, "fk")
    SV.plan_run(fk, "fk", start_daemon=False)
    e2 = st["experiments"]["method-a"]
    t_a = st["tasks"]["a"]
    SV.tick(fk)
    rs = SV.load_run(fk, st["plan_id"])
    assert rs["tasks"][t_a]["phase"] == "waiting" and "baseline" in (rs["tasks"][t_a].get("reason") or "")
    assert FK.inherited(fk, e2) is None
    assert run_until(fk, lambda: FK.inherited(fk, e2) is not None, 90)
    fk.close()   # restart: reopen everything from disk, the pin is in the ledger
    p2 = Project(fk.root)
    assert FK.inherited(p2, e2)["base_rev"]
    assert run_until(p2, done(p2), 120)
    assert W.show(p2, next(r["id"] for r in W._fold(W._records(p2)).values() if r.get("experiment") == e2))["forked_from"]["revision"] == "verified"
    p2.close()


def test_pin_survives_archival_and_is_recorded_on_runs(fk):
    PS.apply(fk, plan([BASE]))
    SV.plan_run(fk, "fk", start_daemon=False)
    assert run_until(fk, done(fk), 90)
    e1 = PS.load_state(fk, "fk")["experiments"]["baseline"]
    out = FK.fork(fk, e1, "Late fork")
    ws1 = next(r["id"] for r in W._fold(W._records(fk)).values() if r.get("experiment") == e1)
    W.archive(fk, ws1, "done")
    top = W.toplevel(fk.root)
    assert subprocess.run(["git", "show", f"{out['pin']}:base.py"], cwd=top, capture_output=True, text=True).stdout == "V=1\n"
    assert W.show(fk, out["workspace"])["forked_from"]["base_rev"] == out["base_rev"]


def test_cli_fork_at_a_commit_and_explicit_sync_only(fk, repo):
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    e = fk.get("experiment", __import__("research.services", fromlist=["x"]).create_experiment(fk, "Parent")["id"])
    out = FK.fork(fk, e["id"], "Child", at=first)
    (repo / "later.py").write_text("x\n")
    git(repo, "add", "later.py")
    git(repo, "commit", "-qm", "main moves")
    ws = W.ensure_for_experiment(fk, out["experiment"])    # never silently follows main
    assert not (open(ws["path"] + "/train.py").read() == "") and not __import__("os").path.exists(ws["path"] + "/later.py")
    r = FK.sync(fk, out["experiment"])
    assert r["synced"] and __import__("os").path.exists(ws["path"] + "/later.py")
    assert FK.sync(fk, out["experiment"])["synced"] is False
    with pytest.raises(ResearchError):
        FK.fork(fk, e["id"], "Bad", at="no-such-rev")
    with pytest.raises(ResearchError, match="not verified"):
        FK.fork(fk, e["id"], "Unverified")


def test_yaml_validation_cycles_unknown_parents_and_pinned_changes(fk):
    cyc = plan([("x", [task("t1", "true")], "{experiment: y}"), ("y", [task("t2", "true")], "{experiment: x}")])
    with pytest.raises(PS.PlanSpecError, match="cycle"):
        PS.normalize(PS.parse(cyc))
    with pytest.raises(PS.PlanSpecError, match="itself"):
        PS.normalize(PS.parse(plan([("x", [task("t1", "true")], "{experiment: x}")])))
    with pytest.raises(PS.PlanSpecError, match="neither"):
        PS.normalize(PS.parse(plan([("x", [task("t1", "true")], "{experiment: nope}")])))
    with pytest.raises(PS.PlanSpecError, match="cycle"):   # parent task depending on the child's task can never start
        PS.normalize(PS.parse(plan([("x", [task("t1", "true", deps=["t2"])], None), ("y", [task("t2", "true")], "{experiment: x}")])))
    PS.apply(fk, plan([BASE, A]))
    changed = PS.diff(fk, plan([BASE, ("method-a", A[1], "{experiment: baseline, revision: HEAD}")]))
    assert not changed["conflicts"]    # not started, nothing pinned yet: still editable
    SV.plan_run(fk, "fk", start_daemon=False)
    assert run_until(fk, done(fk), 120)
    d = PS.diff(fk, plan([BASE, ("method-a", A[1], "{experiment: baseline, revision: HEAD}")]))
    assert any("pinned" in c for c in d["conflicts"])
    assert "inherits" in PS.export(fk, "fk")


def test_existing_experiment_with_work_cannot_start_inheriting(fk):
    own = [task("a", "echo A > a.py", checks=["file:a.py"])]
    PS.apply(fk, plan([BASE, ("method-a", own, None)]))
    SV.plan_run(fk, "fk", start_daemon=False)
    assert run_until(fk, done(fk), 120)
    d = PS.diff(fk, plan([BASE, ("method-a", own, "{experiment: baseline}")]))
    assert any("cannot start inheriting" in c for c in d["conflicts"])


def test_verified_fails_closed_without_a_genuinely_verified_commit(fk):
    PS.apply(fk, plan([BASE, A]))
    st = PS.load_state(fk, "fk")
    e1, tid = st["experiments"]["baseline"], st["tasks"]["b"]
    # a human accepted the task without verification: status done, but no supervisor-verified attempt
    SV.accept(fk, tid, "trust me")
    assert fk.get("task", tid)["status"] == "done"
    with pytest.raises(ResearchError, match="no verified attempt"):
        FK.fork(fk, e1, "Nope")
    SV.plan_run(fk, "fk", start_daemon=False)
    SV.tick(fk)
    assert FK.inherited(fk, st["experiments"]["method-a"]) is None          # YAML children keep waiting, never pin an unverified HEAD
    assert "no verified attempt" in (SV.load_run(fk, st["plan_id"])["tasks"][st["tasks"]["a"]].get("reason") or "")
    assert [r for r in W._records(fk) if r.get("op") == "inherit"] == []
    # an exact commit is still allowed explicitly
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=fk.root, capture_output=True, text=True).stdout.strip()
    assert FK.fork(fk, e1, "Explicit", at=head)["base_rev"] == head


def test_forks_stay_pinned_through_sibling_promotion_sync_archival_restart_and_runs_are_recoverable(ap):
    ap.config["auto_promotion"]["test_command"] = "ls base.py"
    ap.save_config()
    down = ("downstream", [task("d", "test -f a.py && echo D > d.py", deps=["a"], checks=["file:d.py"])], None)   # a plain, non-fork consumer
    PS.apply(ap, plan([BASE, A, B, down], auto=True))
    st = PS.load_state(ap, "fk")
    SV.plan_run(ap, "fk", start_daemon=False)
    SV.autopromote(ap, "fk", "authorize", note="reviewed YAML")
    pid = st["plan_id"]
    ex = st["experiments"]
    assert run_until(ap, lambda: done(ap)() and items(ap, pid) and all(i["status"] == "integrated" for i in items(ap, pid))
                     and len(items(ap, pid)) == 4, 240)
    top = W.toplevel(ap.root)
    g = lambda *a, cwd=top: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout
    pins = {k: FK.inherited(ap, ex[k]) for k in ("method-a", "method-b")}
    wsr = {k: next(r for r in W._fold(W._records(ap)).values() if r.get("experiment") == ex[k]) for k in ex}
    base_tip = g("rev-parse", wsr["baseline"]["branch"]).strip()
    assert pins["method-a"]["base_rev"] == pins["method-b"]["base_rev"] == base_tip
    # promotion worked as before: main has the siblings' and the consumer's code; the plain consumer was synced from main
    assert g("show", "main:base.py") == "V=2\n" and g("show", "main:a.py") and g("show", "main:b.py")
    assert os.path.exists(f"{wt(ap, ex['downstream'])}/a.py")
    # ...but the forks were never synchronized: sibling method-b still has the original baseline, none of method-a's work
    wb = wt(ap, ex["method-b"])
    assert open(f"{wb}/base.py").read() == "V=1\n" and not os.path.exists(f"{wb}/a.py") and not os.path.exists(f"{wb}/d.py")
    rs = SV.load_run(ap, pid)["autopromote"]
    fork_tasks = {st["tasks"]["a"], st["tasks"]["c"]}
    assert not fork_tasks & set(rs.get("launch_bases", {})) and {s["experiment"] for s in rs.get("syncs", [])} <= {ex["downstream"]}
    # an explicit sync of method-a does not touch method-b or any pin
    assert FK.sync(ap, ex["method-a"])["synced"]
    assert open(f"{wb}/base.py").read() == "V=1\n" and not os.path.exists(f"{wb}/a.py")   # b.py is method-b's own file
    assert os.path.exists(f"{wt(ap, ex['method-a'])}/b.py")
    # a run inside the fork records its pin and a recoverable snapshot of the uncommitted tree
    open(f"{wb}/scratch.txt", "w").write("wip\n")
    pw = Project(wb)
    run = RUNS.create_run(pw, ex["method-b"], "echo hi", status="completed", exit_code=0)
    pw.close()
    env = RUNS.get_run(ap, run["id"])["env"]
    env = env if isinstance(env, dict) else __import__("json").loads(env)
    assert env["workspace"]["forked_from"]["base_rev"] == base_tip and env["workspace"]["snapshot"]["differs_from_head"]
    os.remove(f"{wb}/scratch.txt")
    # archive the parent AND method-b: pins and the run snapshot stay recoverable
    W.archive(ap, wsr["baseline"]["id"], "done")
    W.archive(ap, wsr["method-b"]["id"], "done")
    ap.close()
    p2 = Project(top)
    assert {k: FK.inherited(p2, ex[k]) for k in pins} == pins                      # restart: nothing re-resolved, nothing moved
    assert g("show", pins["method-b"]["ref"] + ":base.py") == "V=1\n"
    assert g("show", env["workspace"]["snapshot"]["ref"] + ":scratch.txt") == "wip\n"
    again = FK.fork(p2, ex["method-b"], "Redo from the run", at=run["id"])           # reproduce from the run's recorded source
    assert open(f"{again['path']}/scratch.txt").read() == "wip\n" and open(f"{again['path']}/base.py").read() == "V=1\n"
    assert FK.inherited(p2, ex["method-a"]) == pins["method-a"]
    p2.close()
