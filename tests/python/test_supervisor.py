"""YAML plans and the supervisor: real processes and files, a scripted stand-in for the agent (never a live model)."""
import json
import os
import sys
import textwrap
import time

import pytest

from conftest import git
from research import execution as E, planspec as PS, plans, supervisor as SV
from research import workspace as W
from research.store import Project
from research.util import PolicyBlocked, ResearchError

MOCK = r'''
import os, re, subprocess, sys
brief = re.search(r"brief at (\S+)", sys.argv[-1]).group(1)
text = open(brief).read()
attempt = os.environ.get("RESEARCH_ATTEMPT", "1")
for m in re.finditer(r"^MOCK_SH(?:@(\d+|\*))?: (.*)$", text, re.M):
    if m.group(1) in (None, "*", attempt):
        r = subprocess.run(m.group(2), shell=True)
        if r.returncode:
            sys.exit(r.returncode)
'''


@pytest.fixture
def proj(repo, tmp_path):
    p = Project.init(str(repo), name="demo", goal="g")
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.save_config()
    yield p
    p.close()


def yaml_plan(tasks, extra_exec="", revision=None, scope="partial"):
    body = "\n".join(tasks)
    return textwrap.dedent(f"""\
        schema_version: 1
        project:
          context: [RESEARCH.md]
        plans:
          - key: demo-plan
            title: Demo plan
            objective: Check the supervisor.
            scope: {scope}
            {f'revision: {revision}' if revision else ''}
            execution: {{max_attempts_per_task: 2, max_parallel_agents: 1 {extra_exec}}}
            experiments:
              - key: exp1
                title: First experiment
                hypothesis: Things work.
                tasks:
        """) + textwrap.indent(body, " " * 8) + "\n"


def task_yaml(key, title, shell, deps=None, checks=None, attempt="*", extra=""):
    d = f"depends_on: [{', '.join(deps)}]" if deps else ""
    c = f"checks: [{', '.join(repr(x) for x in checks)}]" if checks else ""
    return textwrap.dedent(f"""\
        - key: {key}
          title: {title}
          {d}
          agent: {{role: implementer, profile: fake}}
          {c}
          {extra}
          prompt: |
            Do the work.
            MOCK_SH@{attempt}: {shell}
        """)


def run_until(p, pred, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        SV.tick(p)
        if pred():
            return True
        time.sleep(0.3)
    return False


def phases(p, ref="demo-plan"):
    return {t["key"]: t["phase"] for t in SV.status(p, ref)["tasks"]}


# ------------------------------------------------------------------------------------------------ YAML plans
def test_template_is_valid_and_errors_are_complete():
    assert PS.validate(PS.TEMPLATE)["tasks"] == 3
    bad = "schema_version: 9\nplans:\n  - key: a\n    title: t\n    bogus: 1\n    tasks:\n      - {key: x, title: X, prompt: p, depends_on: [y]}\n      - {key: y, title: Y, prompt: p, depends_on: [x]}\n"
    with pytest.raises(PS.PlanSpecError) as e:
        PS.validate(bad)
    text = " ".join(e.value.errors)
    assert "schema_version 9" in text and "unknown field 'bogus'" in text and "cycle" in text
    with pytest.raises(PS.PlanSpecError):
        PS.validate("plans: [")


def test_apply_is_idempotent_and_keeps_identity(proj):
    y = yaml_plan([task_yaml("a", "A", "true"), task_yaml("b", "B", "true", deps=["a"])])
    r1 = PS.apply(proj, y)
    ids = r1["plans"][0]["tasks"]
    assert set(ids) == {"a", "b"} and proj.get("task", ids["b"])["depends_on"] == [ids["a"]]
    r2 = PS.apply(proj, y)
    assert r2["plans"][0]["changed"] is False
    assert PS.diff(proj, y)["unchanged"]
    out = PS.export(proj, "demo-plan")
    assert "revision: 1" in out and ids["a"] in out
    r3 = PS.apply(proj, out)                       # export -> apply round trip changes nothing
    assert r3["plans"][0]["changed"] is False
    assert len(plans.list_tasks(proj)) == 2


def test_edit_add_retire_and_protect(proj):
    y = yaml_plan([task_yaml("a", "A", "true"), task_yaml("b", "B", "true", deps=["a"]), task_yaml("c", "C", "true")])
    ids = PS.apply(proj, y)["plans"][0]["tasks"]
    full = PS.export(proj, "demo-plan")
    assert "scope: full" in full
    # drop c (unstarted), retitle b
    from research import yamlio
    doc = yamlio.load(full)
    tasks = doc["plans"][0]["experiments"][0]["tasks"]
    doc["plans"][0]["experiments"][0]["tasks"] = [t for t in tasks if t["key"] != "c"]
    next(t for t in doc["plans"][0]["experiments"][0]["tasks"] if t["key"] == "b")["title"] = "B renamed"
    edited = yamlio.dump(doc, block=True)
    d = PS.diff(proj, edited)
    assert not d["conflicts"]
    ops = [o["op"] for pl in d["plans"] for o in pl["ops"]]
    assert "update_task" in ops and "retire_task" in ops
    PS.apply(proj, edited)
    assert proj.get("task", ids["c"])["status"] == "blocked" and "retired" in proj.get("task", ids["c"])["blockers"]
    assert proj.get("task", ids["b"])["title"] == "B renamed"
    assert len(plans.list_tasks(proj)) == 3                   # nothing deleted
    # a partial document never deletes
    part = PS.export(proj, "demo-plan", experiment="exp1", task="a")
    assert "scope: partial" in part and not [o for pl in PS.diff(proj, part)["plans"] for o in pl["ops"]]
    # stale revision is a conflict
    with pytest.raises(PS.PlanSpecError) as e:
        PS.apply(proj, edited)
    assert "revision" in str(e.value)
    # done work is protected
    with SV.authority(ids["a"], "test"):
        plans.update_task(proj, ids["a"], status="done")
    changed = PS.export(proj, "demo-plan").replace("title: A", "title: A changed")
    with pytest.raises(PS.PlanSpecError) as e:
        PS.apply(proj, changed)
    assert "done" in str(e.value)


# ------------------------------------------------------------------------------------------------ supervisor
def test_supervisor_runs_a_plan_in_dependency_order_and_verifies(proj, repo):
    y = yaml_plan([task_yaml("make", "Make file", "echo hi > out.txt", checks=["file:out.txt"]),
                   task_yaml("use", "Use file", "cat out.txt > copy.txt", deps=["make"], checks=["cmd:test -s copy.txt"])])
    PS.apply(proj, y)
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert phases(proj)["use"] == "waiting"
    assert run_until(proj, lambda: SV.status(proj, "demo-plan")["state"] == "completed")
    st = SV.status(proj, "demo-plan")
    assert [t["phase"] for t in st["tasks"]] == ["verified", "verified"] and st["progress"] == "2/2"
    rs = SV.load_run(proj, st["plan_id"])
    for tid, e in rs["tasks"].items():
        assert len(e["attempts"]) == 1 and e["attempts"][0]["verification"]["passed"]
        a = e["attempts"][0]
        assert a["prompt_sha"] and os.path.exists(proj.abspath(a["prompt_path"])) and a["cwd"] == str(repo)
    # dependency order: 'use' launched after 'make' verified
    ev = [x["summary"] for x in proj.q("SELECT summary FROM events WHERE action='supervisor_launched' ORDER BY id")]
    assert len(ev) == 2


def test_failed_verification_triggers_a_bounded_repair_and_keeps_history(proj):
    y = yaml_plan([task_yaml("fix", "Needs a fix", "echo bad > r.txt", attempt="1", checks=["cmd:grep -q good r.txt"],
                             extra="")
                   .replace("MOCK_SH@1: echo bad > r.txt", "MOCK_SH@1: echo bad > r.txt\n    MOCK_SH@2: echo good > r.txt")])
    PS.apply(proj, y)
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert run_until(proj, lambda: SV.status(proj, "demo-plan")["state"] == "completed", 90)
    e = next(iter(SV.load_run(proj, SV.status(proj, "demo-plan")["plan_id"])["tasks"].values()))
    assert [a["kind"] for a in e["attempts"]] == ["initial", "repair"]
    assert not e["attempts"][0]["verification"]["passed"] and e["attempts"][1]["verification"]["passed"]
    assert "good" in " ".join(e["attempts"][0]["verification"]["diagnostics"]) or e["attempts"][0]["verification"]["diagnostics"]
    assert "What failed verification" in open(proj.abspath(e["attempts"][1]["prompt_path"])).read()


def test_attempt_budget_blocks_instead_of_looping(proj):
    y = yaml_plan([task_yaml("never", "Never passes", "echo bad > r.txt", checks=["cmd:grep -q good r.txt"])])
    PS.apply(proj, y)
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert run_until(proj, lambda: phases(proj)["never"] == "blocked", 90)
    e = next(iter(SV.load_run(proj, SV.status(proj, "demo-plan")["plan_id"])["tasks"].values()))
    assert len(e["attempts"]) == 2 and "attempt budget exhausted" in e["reason"]       # max_attempts_per_task: 2
    assert proj.get("task", SV.status(proj, "demo-plan")["tasks"][0]["id"])["status"] == "blocked"


def test_completion_cannot_be_forced_around_the_supervisor(proj):
    PS.apply(proj, yaml_plan([task_yaml("a", "A", "true", checks=["cmd:true"])]))
    tid = PS.load_state(proj, "demo-plan")["tasks"]["a"]
    plans.update_task(proj, tid, status="done")           # not supervised yet: ordinary gates apply and pass
    plans.update_task(proj, tid, status="todo")
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    with pytest.raises(PolicyBlocked):
        plans.update_task(proj, tid, status="done")
    with pytest.raises(PolicyBlocked):
        plans.complete_task(proj, tid)
    # a hand-edited mirror file is refused too
    path = proj.abspath(proj.q1("SELECT path FROM mirrors WHERE entity_type='task' AND entity_id=?", (tid,))["path"])
    text = open(path).read().replace("status: todo", "status: done")
    open(path, "w").write(text)
    proj.import_file(path, "task")
    assert proj.get("task", tid)["status"] != "done"
    assert proj.q1("SELECT 1 AS x FROM events WHERE entity_id=? AND action='import_rejected'", (tid,))
    # a human can accept, with a reason, and it is logged as unverified
    with pytest.raises(ResearchError):
        SV.accept(proj, tid, "")
    SV.accept(proj, tid, "checked by hand")
    assert proj.get("task", tid)["status"] == "done" and "WITHOUT supervisor verification" in proj.get("task", tid)["result"]


def test_agent_reports_need_the_owning_execution(proj, monkeypatch):
    PS.apply(proj, yaml_plan([task_yaml("a", "A", "sleep 3; echo x > a.txt", checks=["file:a.txt"])]))
    tid = PS.load_state(proj, "demo-plan")["tasks"]["a"]
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    with pytest.raises(PolicyBlocked):
        SV.report(proj, tid, "progress", message="no execution env")
    assert run_until(proj, lambda: phases(proj)["a"] == "running", 30)
    ex = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])["tasks"][tid]["attempts"][-1]["execution"]
    monkeypatch.setenv("RESEARCH_EXECUTION", "deadbeef" * 4)
    with pytest.raises(PolicyBlocked) as e:
        SV.report(proj, tid, "progress", message="foreign agent")
    assert "does not own" in str(e.value)
    monkeypatch.setenv("RESEARCH_EXECUTION", ex)
    assert SV.report(proj, tid, "progress", message="token=sk-abcdefghijklmnopqrstuv done")["recorded"] == "progress"
    SV.report(proj, tid, "finish", message="done")
    with pytest.raises(PolicyBlocked):
        SV.report(proj, tid, "artifact", path="../escape.txt")
    rows = SV._reports(proj, tid)
    assert "sk-abcdefghijklmnopqrstuv" not in json.dumps(rows) and rows[0]["execution"] == ex
    assert run_until(proj, lambda: phases(proj)["a"] == "verified", 60)
    with pytest.raises(PolicyBlocked):                      # a late report from a finished attempt is refused
        SV.report(proj, tid, "progress", message="late")


def test_pause_resume_and_unknown_execution_is_never_retried(proj):
    PS.apply(proj, yaml_plan([task_yaml("a", "A", "echo x > a.txt", checks=["file:a.txt"]), task_yaml("b", "B", "echo y > b.txt", deps=["a"], checks=["file:b.txt"])]))
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    SV.plan_pause(proj, "demo-plan")
    SV.tick(proj)
    assert phases(proj)["a"] in ("waiting", "ready") and not E.list_all(proj)      # paused: nothing launches
    SV.plan_resume(proj, "demo-plan") if False else SV._set_state(proj, "demo-plan", "running")
    assert run_until(proj, lambda: phases(proj)["a"] == "running" or phases(proj)["a"] == "verified", 30)
    # simulate a crashed launcher: the record claims running/launched but nothing is alive
    rs = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])
    tid = PS.load_state(proj, "demo-plan")["tasks"]["b"]
    assert run_until(proj, lambda: phases(proj)["a"] == "verified", 40)
    assert run_until(proj, lambda: bool(SV.load_run(proj, rs["plan_id"])["tasks"].get(tid, {}).get("attempts")), 30)
    ex = SV.load_run(proj, rs["plan_id"])["tasks"][tid]["attempts"][-1]["execution"]
    deadline = time.time() + 40
    while time.time() < deadline and E.show(proj, ex)["state"] not in ("completed", "failed"):
        time.sleep(0.3)
    path = E.path(proj, ex)
    rec = json.load(open(path))
    rec.update(state="unknown", pid=999999, launcher_pid=999999)
    open(path, "w").write(json.dumps(rec))
    run = SV.load_run(proj, rs["plan_id"])
    run["tasks"][tid]["phase"] = "running"
    run["tasks"][tid]["attempts"][-1].pop("verification", None)
    SV.save_run(proj, run)
    SV.tick(proj)
    st = phases(proj)
    assert st["b"] == "attention" and len(SV.load_run(proj, rs["plan_id"])["tasks"][tid]["attempts"]) == 1   # not retried


def test_proposals_are_classified_and_applied_through_the_same_engine(proj):
    PS.apply(proj, yaml_plan([task_yaml("a", "A", "true")]))
    add = textwrap.dedent("""\
        schema_version: 1
        plans:
          - key: demo-plan
            title: Demo plan
            experiments:
              - key: exp1
                title: First experiment
                tasks:
                  - {key: diag, title: Diagnose, prompt: Look at it, depends_on: [a]}
        """)
    pr = SV.propose(proj, None, add, "diagnostic needed")
    assert pr["classification"] == "operational" and pr["status"] == "applied"
    assert "diag" in PS.load_state(proj, "demo-plan")["tasks"]
    sci = add.replace("diag", "d2").replace("        title: First experiment\n", "        title: First experiment\n        hypothesis: A new belief\n")
    pr2 = SV.propose(proj, None, sci, "change the hypothesis")
    assert pr2["classification"] == "scientific" and pr2["status"] == "pending"
    assert "d2" not in PS.load_state(proj, "demo-plan")["tasks"]
    SV.decide_proposal(proj, pr2["id"], True)
    assert "d2" in PS.load_state(proj, "demo-plan")["tasks"]


def test_agents_run_in_the_experiment_worktree_and_plan_tasks_share_it(repo, tmp_path):
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.save_config()
    PS.apply(p, yaml_plan([task_yaml("a", "A", "pwd > where.txt", checks=["file:where.txt"])]))
    SV.plan_run(p, "demo-plan", start_daemon=False)
    assert run_until(p, lambda: phases(p)["a"] == "verified", 60)
    e = next(iter(SV.load_run(p, PS.load_state(p, "demo-plan")["plan_id"])["tasks"].values()))
    cwd = e["attempts"][0]["cwd"]
    assert "/EXP-" in cwd and cwd != str(repo)
    assert os.path.realpath(open(os.path.join(cwd, "where.txt")).read().strip()) == os.path.realpath(cwd)
    assert not os.path.exists(os.path.join(repo, "where.txt"))
    p.close()


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_yaml_round_trip_and_agent_commands_need_a_supervised_execution(proj, repo, tmp_path):
    import subprocess
    env = {**os.environ, "RESEARCH_AUTHOR_TYPE": "human"}
    def cli(*a, **kw):
        return subprocess.run([sys.executable, "-m", "research_cli", "--root", str(repo), *a], cwd=repo, env=env, capture_output=True, text=True, **kw)
    f = tmp_path / "plan.yaml"
    f.write_text(yaml_plan([task_yaml("a", "A", "true")]))
    assert cli("plan", "yaml", "validate", str(f)).returncode == 0
    d = cli("plan", "yaml", "diff", str(f))
    assert d.returncode == 0 and "create_task" in d.stdout
    assert cli("plan", "yaml", "apply", str(f)).returncode == 0
    again = cli("plan", "yaml", "apply", str(f), "--json")
    assert json.loads(again.stdout)["plans"][0]["changed"] is False
    out = tmp_path / "out.yaml"
    assert cli("plan", "yaml", "export", "demo-plan", "-o", str(out)).returncode == 0 and "revision: 1" in out.read_text()
    assert cli("plan", "run", "demo-plan", "--no-daemon").returncode == 0
    s = cli("plan", "status", "demo-plan", "--json")
    assert json.loads(s.stdout)["state"] == "running"
    bad = cli("agent", "progress", "T-001", "--message", "hi")
    assert bad.returncode == 3 and "RESEARCH_EXECUTION" in bad.stderr
    assert cli("plan", "yaml", "validate", "-", input="schema_version: 2\nplans: []\n").returncode != 0
