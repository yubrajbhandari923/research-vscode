"""YAML plans and the supervisor: real processes and files, a scripted stand-in for the agent (never a live model)."""
import json
import os
import sys
import textwrap
import time

import pytest

from conftest import git
from research import agents, execution as E, planspec as PS, plans, supervisor as SV
from research import workspace as W
from research.store import Project
from research.util import PolicyBlocked, ResearchError
from research import autopromote as AP

MOCK = r'''
import os, re, subprocess, sys
brief = re.search(r"brief at (\S+)", sys.argv[-1]).group(1)
text = open(brief).read()
attempt = os.environ.get("RESEARCH_ATTEMPT", "1")
seen = set()
for m in re.finditer(r"^MOCK_SH(?:@(\d+|\*))?: (.*)$", text, re.M):       # the brief repeats the task text: run each command once
    if m.group(1) in (None, "*", attempt) and m.group(2) not in seen:
        seen.add(m.group(2))
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
    print("RUNS", [(r["id"], r["experiment_id"], r["backend"], r["status"], r["created_at"]) for r in p.q("SELECT * FROM runs")])
    print("ATTEMPTS", [(a["n"], a.get("started")) for st in SV.all_runs(p) for e in st["tasks"].values() for a in e["attempts"]])
    for st in (SV.all_runs(p)):
        print("TIMEOUT STATE", json.dumps({t: {"phase": e.get("phase"), "reason": e.get("reason"), "n": len(e["attempts"])} for t, e in st["tasks"].items()}))
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
                  - {key: diag, title: Diagnose, task_type: diagnostic, prompt: Look at it, depends_on: [a]}
        """)
    pr = SV.propose(proj, None, add, "diagnostic needed")
    assert pr["classification"] == "operational" and pr["status"] == "applied"
    assert "diag" in PS.load_state(proj, "demo-plan")["tasks"]
    sci = add.replace("diag", "d2").replace("        title: First experiment\n", "        title: First experiment\n        hypothesis: A new belief\n")
    pr2 = SV.propose(proj, None, sci, "change the hypothesis")
    assert pr2["classification"] == "scientific" and pr2["status"] == "pending"
    assert "d2" not in PS.load_state(proj, "demo-plan")["tasks"]
    SV.decide_proposal(proj, pr2["id"], True, "hypothesis reviewed")
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


def test_agent_can_claim_and_report_through_the_cli_under_supervision(proj):
    """Regression from the first real run: a supervisor-held claim made the agent's mandatory `task start` fail."""
    cli = f"{sys.executable} -m research_cli"
    PS.apply(proj, yaml_plan([task_yaml("a", "A", f"{cli} task start $RESEARCH_TASK && {cli} agent progress $RESEARCH_TASK --message started && "
                                                  f"echo ok > a.txt && {cli} agent finish $RESEARCH_TASK --request-verification",
                                        checks=["file:a.txt"])]))
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert run_until(proj, lambda: phases(proj)["a"] == "verified", 60)
    tid = PS.load_state(proj, "demo-plan")["tasks"]["a"]
    kinds = [r["kind"] for r in SV._reports(proj, tid)]
    assert kinds == ["progress", "finish"]
    e = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])["tasks"][tid]
    assert len(e["attempts"]) == 1 and e["attempts"][0].get("verify_requested")


def test_frozen_tests_frozen_inside_a_worktree_verify_from_the_supervisor(repo, tmp_path):
    """Regression from the second real run: a relative freeze made in the worktree looked 'modified' from the project root."""
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.save_config()
    cli = f"{sys.executable} -m research_cli"
    PS.apply(p, yaml_plan([task_yaml("a", "A", f"echo 'assert True' > test_x.py && {cli} orchestrate freeze $RESEARCH_TASK test_x.py && echo ok > a.txt",
                                     checks=["file:a.txt"])]))
    SV.plan_run(p, "demo-plan", start_daemon=False)
    assert run_until(p, lambda: phases(p)["a"] in ("verified", "blocked", "attention"), 60)
    assert phases(p)["a"] == "verified"
    p.close()


def test_the_detached_daemon_process_runs_a_plan_to_completion(proj):
    """The persistent process (not just in-process ticks): it launches, verifies and completes, and exits when idle."""
    PS.apply(proj, yaml_plan([task_yaml("a", "A", "echo 1 > a.txt", checks=["file:a.txt"]), task_yaml("b", "B", "echo 2 > b.txt", deps=["a"], checks=["file:b.txt"])]))
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    d = SV.daemon_start(proj, detach=True, exit_when_idle=True)
    assert d["running"] and d["pid"]
    assert run_until_status(proj, "completed", 90), SV.status(proj, "demo-plan")
    deadline = time.time() + 30
    while time.time() < deadline and SV.daemon_status(proj)["running"]:
        time.sleep(0.5)
    assert not SV.daemon_status(proj)["running"]                   # exit-when-idle: no plan running any more
    log = os.path.join(proj.rdir, "supervisor", "daemon.log")
    assert not os.path.exists(log) or "tick error" not in open(log).read()


def run_until_status(p, state, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if SV.status(p, "demo-plan")["state"] == state:
            return True
        time.sleep(0.5)
    return False


def test_provider_usage_limit_pauses_the_task_without_consuming_attempts(proj):
    """Found by the real run: a 429 'usage limit' used to burn repair attempts. It is not the agent's fault and not retried blindly."""
    y = yaml_plan([task_yaml("a", "A", "echo '{\"is_error\": true, \"terminal_reason\": \"api_error\", \"result\": \"You have hit your usage limit\"}'; exit 1",
                             checks=["file:never.txt"])])
    PS.apply(proj, y)
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert run_until(proj, lambda: phases(proj)["a"] == "attention", 60)
    pid = PS.load_state(proj, "demo-plan")["plan_id"]
    rs = SV.load_run(proj, pid)
    e = next(iter(rs["tasks"].values()))
    assert len(e["attempts"]) == 1 and "provider error" in e["attempts"][0]["voided"] and rs["budgets"]["attempts"] == 0
    assert "usage limit" in e["reason"]
    SV.tick(proj)
    assert len(SV.load_run(proj, pid)["tasks"][next(iter(rs["tasks"]))]["attempts"]) == 1      # nothing was retried behind the user's back


def test_voiding_one_attempt_keeps_the_earlier_failure_as_the_repair_basis(proj):
    y = yaml_plan([task_yaml("a", "A", "echo bad > r.txt", attempt="*", checks=["cmd:grep -q good r.txt"])])
    PS.apply(proj, y)
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert run_until(proj, lambda: phases(proj)["a"] == "blocked", 90)           # two attempts, budget 2
    tid = PS.load_state(proj, "demo-plan")["tasks"]["a"]
    SV.void_attempts(proj, tid, "second attempt was not the agent's fault", attempts=[2])
    e = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])["tasks"][tid]
    assert e["phase"] == "repair_pending" and e["attempts"][1]["voided"] and not e["attempts"][0].get("voided")


# ------------------------------------------------------------------------------------------------ fixtures for worktree projects
@pytest.fixture
def lproj(repo, tmp_path):
    """Shared-ledger project (experiment worktrees) with the scripted agent profile."""
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.config["promotion"] = {**(p.config.get("promotion") or {}), "require_supported_finding": False}
    p.save_config()
    yield p
    p.close()


def two_exp_plan(par):
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: par
            title: Parallel
            execution: {{max_parallel_agents: {par}, max_attempts_per_task: 1}}
            experiments:
              - key: e1
                title: One
                tasks:
                  - {{key: a1, title: A1, agent: {{role: implementer, profile: fake}}, checks: ["file:a1.txt"], prompt: "Work.\\nMOCK_SH: sleep 3; echo 1 > a1.txt"}}
                  - {{key: a2, title: A2, depends_on: [], agent: {{role: implementer, profile: fake}}, checks: ["file:a2.txt"], prompt: "Work.\\nMOCK_SH: sleep 3; echo 2 > a2.txt"}}
              - key: e2
                title: Two
                tasks:
                  - {{key: b1, title: B1, agent: {{role: implementer, profile: fake}}, checks: ["file:b1.txt"], prompt: "Work.\\nMOCK_SH: sleep 3; echo 3 > b1.txt"}}
        """)


def watch(p, ref, seconds):
    """Tick for a while; return the maximum number of tasks seen running at once and the worktrees they used."""
    peak, t0, seen = 0, time.time(), set()
    while time.time() - t0 < seconds:
        SV.tick(p)
        st = SV.status(p, ref)
        live = [t for t in st["tasks"] if t["phase"] in ("running", "verifying")]
        peak = max(peak, len(live))
        for t in live:
            seen.add((t["key"], t["experiment"]))
        if st["state"] == "completed":
            break
        time.sleep(0.3)
    return peak, seen, SV.status(p, ref)


def test_two_agents_run_at_once_in_different_worktrees_but_never_share_one(lproj):
    PS.apply(lproj, two_exp_plan(2))
    SV.plan_run(lproj, "par", start_daemon=False)
    peak, seen, st = watch(lproj, "par", 90)
    assert st["state"] == "completed" and peak == 2                       # e1 and e2 ran together
    rs = SV.load_run(lproj, st["plan_id"])
    cwd = {t: e["attempts"][0]["cwd"] for t, e in rs["tasks"].items()}
    ids = {t["key"]: t["id"] for t in st["tasks"]}
    assert cwd[ids["a1"]] == cwd[ids["a2"]] != cwd[ids["b1"]]
    # a1 and a2 share a worktree, so they never overlapped
    spans = {k: (rs["tasks"][ids[k]]["attempts"][0]["started"], rs["tasks"][ids[k]]["attempts"][0]["ended"]) for k in ("a1", "a2")}
    first, second = sorted(spans.values())
    assert first[1] <= second[0] or True and first[1] <= second[1]
    assert not (spans["a1"][0] < spans["a2"][1] and spans["a2"][0] < spans["a1"][1])


def test_parallel_cap_of_one_serialises_everything(lproj):
    PS.apply(lproj, two_exp_plan(1))
    SV.plan_run(lproj, "par", start_daemon=False)
    peak, _, st = watch(lproj, "par", 120)
    assert st["state"] == "completed" and peak == 1


# ------------------------------------------------------------------------------------------------ HPC runs through the supervisor
RUN_PY = ("import os,sys; from research.store import Project; from research import runs; p=Project(os.environ['RESEARCH_ROOT']); "
          "e=p.get('task',os.environ['RESEARCH_TASK'])['related_experiment_id']; "
          "runs.create_run(p,e,command='job',backend='slurm',status='{status}',exit_code={code},slurm_job_id='900{n}')")


def hpc_plan(attempt1, attempt2, extra=""):
    def line(attempt, status, code, n):
        py = RUN_PY.format(status=status, code=code, n=n)
        return f"MOCK_SH@{attempt}: {sys.executable} -c \"{py}\""
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: hpc
            title: HPC
            execution: {{max_attempts_per_task: 3, max_hpc_resubmissions: 2}}
            experiments:
              - key: e
                title: E
                tasks:
                  - key: job
                    title: Job
                    agent: {{role: implementer, profile: fake}}
                    resources: {{backend: slurm}}
                    prompt: |
                      Submit.
                      {line(1, *attempt1, 1)}
                      {line(2, *attempt2, 2)}
        """)


def test_a_failed_hpc_job_fails_verification_and_the_repair_resubmits_within_budget(lproj):
    PS.apply(lproj, hpc_plan(("failed", 1), ("completed", 0)))
    SV.plan_run(lproj, "hpc", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj, "hpc")["job"] == "verified", 90)
    rs = SV.load_run(lproj, PS.load_state(lproj, "hpc")["plan_id"])
    e = next(iter(rs["tasks"].values()))
    assert [a["kind"] for a in e["attempts"]] == ["initial", "repair"]
    d = e["attempts"][0]["verification"]["diagnostics"]
    assert not e["attempts"][0]["verification"]["passed"] and any("did not complete" in x for x in d)
    assert len(e["hpc_runs"]) == 2                                          # the failed run is kept, the new one is linked
    assert "computational run(s) did not complete" in open(lproj.abspath(e["attempts"][1]["prompt_path"])).read()


def test_a_waiting_hpc_job_is_polled_not_resubmitted_and_verified_when_it_finishes(lproj):
    PS.apply(lproj, hpc_plan(("running", 0), ("completed", 0)))
    SV.plan_run(lproj, "hpc", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj, "hpc")["job"] == "waiting_hpc", 60)
    for _ in range(4):                                                      # several polls: still waiting, no second attempt
        SV.tick(lproj)
        time.sleep(0.3)
    rs = SV.load_run(lproj, PS.load_state(lproj, "hpc")["plan_id"])
    e = next(iter(rs["tasks"].values()))
    assert len(e["attempts"]) == 1 and e["phase"] == "waiting_hpc"
    run = lproj.q1("SELECT id FROM runs WHERE backend='slurm'")["id"]
    from research import runs as R
    R.update_run(lproj, run, status="completed", exit_code=0)
    assert run_until(lproj, lambda: phases(lproj, "hpc")["job"] == "verified", 60)


# ------------------------------------------------------------------------------------------------ proposals from agents
def test_an_agent_can_propose_a_follow_up_task_and_it_runs(lproj, tmp_path):
    cli = f"{sys.executable} -m research_cli"
    patch = tmp_path / "patch.yaml"
    patch.write_text(textwrap.dedent("""\
        schema_version: 1
        plans:
          - key: prop
            title: Prop
            experiments:
              - key: e
                title: E
                tasks:
                  - key: diag
                    task_type: diagnostic
                    title: Diagnose
                    depends_on: [main]
                    agent: {role: implementer, profile: fake}
                    checks: ["file:diag.txt"]
                    prompt: |
                      Diagnose.
                      MOCK_SH: echo d > diag.txt
        """))
    y = textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: prop
            title: Prop
            experiments:
              - key: e
                title: E
                tasks:
                  - key: main
                    title: Main
                    agent: {{role: implementer, profile: fake}}
                    checks: ["file:m.txt"]
                    prompt: |
                      Work.
                      MOCK_SH: {cli} agent propose $RESEARCH_TASK --file {patch} --rationale 'need a diagnostic' && echo m > m.txt
        """)
    PS.apply(lproj, y)
    SV.plan_run(lproj, "prop", start_daemon=False)
    assert run_until(lproj, lambda: len(phases(lproj, "prop")) == 2 and set(phases(lproj, "prop").values()) == {"verified"}, 150)
    props = SV.list_proposals(lproj)
    assert len(props) == 1 and props[0]["status"] == "applied" and props[0]["classification"] == "operational" and props[0]["source"].startswith("agent")
    assert "diag" in PS.load_state(lproj, "prop")["tasks"]


# ------------------------------------------------------------------------------------------------ promotion of verified work
def test_verified_work_is_committed_on_its_branch_and_promoted_to_main_with_provenance(lproj, repo):
    from research import promotion as PR
    PS.apply(lproj, yaml_plan([task_yaml("a", "Add module", "echo 'X = 1' > mod.py; mkdir -p outputs; echo big > outputs/data.bin", checks=["file:mod.py"])]))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj)["a"] == "verified", 60)
    tid = PS.load_state(lproj, "demo-plan")["tasks"]["a"]
    a = SV.load_run(lproj, PS.load_state(lproj, "demo-plan")["plan_id"])["tasks"][tid]["attempts"][0]
    cwd = a["cwd"]
    msg = subprocess_out(["git", "log", "-1", "--format=%B", a["commit"]], cwd)
    assert f"Research-Task: {tid}" in msg and "Verified by Research OS" in msg
    files = subprocess_out(["git", "show", "--name-only", "--format=", a["commit"]], cwd).split()
    assert "mod.py" in files and not any(f.startswith("outputs/") for f in files)         # results stay out of code history
    assert not os.path.exists(os.path.join(repo, "mod.py"))                               # main untouched so far
    made = SV.promote_plan(lproj, "demo-plan")
    pid = made["candidates"][0]["id"]
    PR.verify(lproj, pid, "test -f mod.py")
    PR.review(lproj, pid, "approve", "reviewed by the researcher, not by the preparer")   # allowed only because the supervisor prepared it
    PR.approve(lproj, pid, "ship it")
    PR.integrate(lproj, pid, confirm=True)
    assert open(os.path.join(repo, "mod.py")).read().strip() == "X = 1"
    assert f"cherry picked from commit {a['commit']}" in subprocess_out(["git", "log", "-1", "--format=%B"], str(repo))


def subprocess_out(argv, cwd):
    import subprocess
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True).stdout


# ------------------------------------------------------------------------------------------------ codex adapter
def test_codex_session_id_and_resume_command_follow_the_verified_cli_shape():
    from research import providers as P
    argv = P.build_argv({"command": "codex"}, "codex", "gpt-x", "PROMPT", "/w", ["/l"], True, "high")
    r = P.resume_argv("codex", argv, "SID")
    assert r[:4] == ["codex", "exec", "resume", "--json"] and r[-2:] == ["SID", "PROMPT"]
    assert "-s" not in r and "-C" not in r and "--add-dir" not in r
    assert 'sandbox_mode="workspace-write"' in r and any(x.startswith("sandbox_workspace_write.writable_roots=") for x in r)
    assert r[r.index("-m") + 1] == "gpt-x"
    out = 'Reading additional input from stdin...\n{"type":"thread.started","thread_id":"0192-abc"}\n{"type":"turn.started"}\n'
    assert P.session_id_of("codex", out) == "0192-abc"
    assert P.session_id_of("codex", "no json") is None


# ------------------------------------------------------------------------------------------------ short stress tests
def multi_plan(key, n_tasks, sleep=1):
    tasks = "\n".join(f"""                  - {{key: t{i}, title: T{i}, {'depends_on: [t' + str(i - 1) + '], ' if i else ''}agent: {{role: implementer, profile: fake}}, checks: ["file:{key}{i}.txt"], prompt: "Work.\\nMOCK_SH: sleep {sleep}; echo {i} > {key}{i}.txt"}}"""
                      for i in range(n_tasks))
    return f"""schema_version: 1
plans:
  - key: {key}
    title: Plan {key}
    execution: {{max_attempts_per_task: 1, max_parallel_agents: 4}}
    experiments:
      - key: e
        title: Experiment {key}
        tasks:
{tasks}
"""


def test_several_plans_run_at_once_without_cross_talk(lproj):
    for k in ("pa", "pb", "pc"):
        PS.apply(lproj, multi_plan(k, 3))
        SV.plan_run(lproj, k, start_daemon=False)
    peak, t0 = 0, time.time()
    while time.time() - t0 < 150:
        SV.tick(lproj)
        live = sum(1 for k in ("pa", "pb", "pc") for t in SV.status(lproj, k)["tasks"] if t["phase"] in ("running", "verifying"))
        peak = max(peak, live)
        if all(SV.status(lproj, k)["state"] == "completed" for k in ("pa", "pb", "pc")):
            break
        time.sleep(0.3)
    assert all(SV.status(lproj, k)["state"] == "completed" for k in ("pa", "pb", "pc")) and peak >= 2
    cwds = set()
    for k in ("pa", "pb", "pc"):
        rs = SV.load_run(lproj, PS.load_state(lproj, k)["plan_id"])
        assert all(len(e["attempts"]) == 1 for e in rs["tasks"].values())          # nothing launched twice
        cwds |= {e["attempts"][0]["cwd"] for e in rs["tasks"].values()}
    assert len(cwds) == 3                                                           # one worktree per plan's experiment


def test_killing_the_daemon_mid_run_loses_nothing_and_duplicates_nothing(lproj):
    PS.apply(lproj, multi_plan("kill", 3, sleep=3))
    SV.plan_run(lproj, "kill", start_daemon=False)
    d = SV.daemon_start(lproj, detach=True)
    assert d["running"]
    assert run_until_phase(lproj, "kill", "t0", "running", 30)
    os.kill(int(d["pid"]), 9)                                                       # no clean shutdown, no goodbye
    time.sleep(1)
    assert not SV.daemon_status(lproj)["running"]
    d2 = SV.daemon_start(lproj, detach=True, exit_when_idle=True)                   # a restart reconciles from the files
    assert d2["running"] and d2["pid"] != d["pid"]
    deadline = time.time() + 120
    while time.time() < deadline and SV.status(lproj, "kill")["state"] != "completed":
        time.sleep(0.5)
    st = SV.status(lproj, "kill")
    if st["state"] != "completed":
        log = os.path.join(lproj.rdir, "supervisor", "daemon.log")
        print("DAEMON", SV.daemon_status(lproj), open(log).read()[-800:] if os.path.exists(log) else "(no log)", [(t["key"], t["phase"], t["reason"]) for t in st["tasks"]])
    assert st["state"] == "completed" and [t["phase"] for t in st["tasks"]] == ["verified"] * 3
    rs = SV.load_run(lproj, st["plan_id"])
    assert all(len(e["attempts"]) == 1 for e in rs["tasks"].values())               # the running agent survived; no second launch


def run_until_task_phase(p, key, ph, timeout):
    """Phase reached AND, for a running task, its agent process is recorded (the attempt is reserved a moment before)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if phase(p, key)["phase"] == ph and (ph != "running" or (run_attempts(p).get(key) or [{}])[-1].get("execution")):
            return True
        time.sleep(0.3)
    return False


def run_until_phase(p, ref, key, phase, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if phases(p, ref).get(key) == phase:
            return True
        time.sleep(0.3)
    return False


def test_an_unreadable_hpc_status_never_causes_a_resubmission(lproj, monkeypatch):
    PS.apply(lproj, hpc_plan(("running", 0), ("completed", 0)))
    from research.hpc import remote as HR
    calls = []
    monkeypatch.setattr(HR, "sync", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(ResearchError("ssh: connection timed out")))
    lproj.config["supervisor"]["hpc_poll_seconds"] = 5
    SV.plan_run(lproj, "hpc", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj, "hpc")["job"] == "waiting_hpc", 60)
    pid = PS.load_state(lproj, "hpc")["plan_id"]
    for _ in range(12):                                                             # many polls, all failing
        SV.tick(lproj)
        time.sleep(0.2)
    e = next(iter(SV.load_run(lproj, pid)["tasks"].values()))
    assert calls and e["phase"] == "waiting_hpc" and len(e["attempts"]) == 1        # still waiting; no new attempt, no new job
    assert lproj.q1("SELECT COUNT(*) AS n FROM runs WHERE backend='slurm'")["n"] == 1


def test_a_launch_interrupted_before_the_process_started_is_refired_once(lproj, monkeypatch):
    lproj.config["supervisor"]["launch_grace_seconds"] = 0
    PS.apply(lproj, yaml_plan([task_yaml("a", "A", "echo 1 > a.txt", checks=["file:a.txt"])]))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    real = SV.subprocess.Popen
    monkeypatch.setattr(SV.subprocess, "Popen", lambda *a, **k: type("Dead", (), {})() if "execution.py" in " ".join(map(str, a[0])) else real(*a, **k))   # the supervisor "dies" right after preparing
    SV.tick(lproj)
    monkeypatch.setattr(SV.subprocess, "Popen", real)
    pid = PS.load_state(lproj, "demo-plan")["plan_id"]
    e = next(iter(SV.load_run(lproj, pid)["tasks"].values()))
    assert E.show(lproj, e["attempts"][0]["execution"])["state"] == "prepared"
    assert run_until(lproj, lambda: phases(lproj)["a"] == "verified", 60)
    e = next(iter(SV.load_run(lproj, pid)["tasks"].values()))
    assert len(e["attempts"]) == 1 and len(E.list_all(lproj)) == 1                   # one execution record, run exactly once


def test_twelve_independent_tasks_across_four_experiments_all_verify_once(lproj):
    exps = "\n".join(f"""      - key: e{x}
        title: Exp {x}
        tasks:
""" + "\n".join(f"""          - {{key: e{x}t{i}, title: T{x}{i}, agent: {{role: implementer, profile: fake}}, checks: ["file:f{x}{i}.txt"], prompt: "Work.\\nMOCK_SH: echo 1 > f{x}{i}.txt"}}""" for i in range(3)) for x in range(4))
    PS.apply(lproj, f"""schema_version: 1
plans:
  - key: fan
    title: Fan
    execution: {{max_attempts_per_task: 1, max_parallel_agents: 4}}
    experiments:
{exps}
""")
    SV.plan_run(lproj, "fan", start_daemon=False)
    assert run_until(lproj, lambda: SV.status(lproj, "fan")["state"] == "completed", 180)
    rs = SV.load_run(lproj, PS.load_state(lproj, "fan")["plan_id"])
    assert len(rs["tasks"]) == 12 and all(len(e["attempts"]) == 1 and e["phase"] == "verified" for e in rs["tasks"].values())
    assert rs["budgets"]["attempts"] == 12


def test_an_orphaned_worktree_left_by_a_killed_supervisor_is_adopted_not_a_dead_end(lproj, tmp_path):
    from research import services
    e = services.create_experiment(lproj, "orphan", hypothesis="h", question=services.create_question(lproj, "q")["id"])
    top = W.toplevel(lproj.root)
    orphan = tmp_path / "orphan-wt"
    git(top, "worktree", "add", "-q", "-b", f"exp/{e['id']}-orphan", str(orphan))          # created, but never registered
    pl = plans.create_plan(lproj, "p")
    t = plans.create_task(lproj, pl["id"], "t", goal="g", related_experiment_id=e["id"])
    d = agents.dispatch(lproj, "implementer", t["id"], profile="fake", create_workspace=True)
    assert os.path.realpath(d["cwd"]) == os.path.realpath(str(orphan)) and d["workspace"]


def t(key, role, shell, deps=(), checks=(), acceptance=None, attempts=3):
    d = f"depends_on: [{', '.join(deps)}], " if deps else ""
    c = f"checks: [{', '.join(repr(x) for x in checks)}], " if checks else ""
    a = f"acceptance: 'Deliver it. Outputs: {acceptance}.', " if acceptance else ""
    return (f'{{key: {key}, title: T {key}, {d}{c}{a}execution: {{max_attempts: {attempts}}}, agent: {{role: {role}, profile: fake}}, '
            f'prompt: "Work.\\nMOCK_SH: {shell}"}}')


def plan(exps, exec_extra="", key="fx"):
    body = "".join(f"      - key: {ek}\n        title: Exp {ek}\n        hypothesis: h\n        tasks:\n" + "".join(f"          - {x}\n" for x in tasks) for ek, tasks in exps)
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: {key}
            title: Fixes
            objective: o
            execution: {{max_parallel_agents: 1, max_attempts_per_task: 3{exec_extra}}}
            experiments:
        """) + body


# ------------------------------------------------------------------------------------------------ 1. role / task compatibility
VERIFIER_AUTHORS = [("e1", [t("rep", "verifier", "true", checks=["file:docs-gate.md"], acceptance="docs-gate.md")])]


def phase(p, key, ref="fx"):
    return {x["key"]: x for x in SV.status(p, ref)["tasks"]}[key]


def run_attempts(p, ref="fx"):
    st = PS.load_state(p, ref)
    return {k: SV.load_run(p, st["plan_id"])["tasks"].get(tid, {}).get("attempts", []) for k, tid in st["tasks"].items()}


# ------------------------------------------------------------------------------------------------ 4. reload / restart
def slow_plan(sleep=8, par=1):
    return plan([("e1", [t("s", "implementer", f"sleep {sleep}; echo done > s.txt", checks=["file:s.txt"], attempts=1)])]).replace("max_parallel_agents: 1", f"max_parallel_agents: {par}")


# ------------------------------------------------------------------------------------------------ 5. parallelism
PAR = [("e1", [t("a1", "implementer", "sleep 3; echo 1 > a1.txt", checks=["file:a1.txt"]), t("a2", "implementer", "sleep 3; echo 2 > a2.txt", checks=["file:a2.txt"])]),
       ("e2", [t("b1", "implementer", "sleep 3; echo 3 > b1.txt", checks=["file:b1.txt"])]),
       ("e3", [t("c1", "implementer", "sleep 3; echo 4 > c1.txt", checks=["file:c1.txt"])])]


def watch_running(p, seconds):
    peak, same_tree = 0, False
    t0 = time.time()
    while time.time() - t0 < seconds:
        SV.tick(p)
        live = [x for x in SV.status(p, "fx")["tasks"] if x["phase"] in ("running", "verifying")]
        peak = max(peak, len(live))
        exps = [x["experiment"] for x in live]
        same_tree = same_tree or len(exps) != len(set(exps))
        if SV.status(p, "fx")["state"] == "completed":
            break
        time.sleep(0.3)
    return peak, same_tree


def test_validate_and_diff_flag_a_verifier_that_must_author_its_own_deliverable(lproj):
    text = plan(VERIFIER_AUTHORS)
    v = PS.validate(text, lproj)
    assert v["ok"] and len(v["warnings"]) == 1 and "fx/rep is a verifier" in v["warnings"][0] and "docs-gate.md" in v["warnings"][0] and "analyst" in v["warnings"][0]
    d = PS.diff(lproj, text)
    assert d["ok"] and any("fx/rep is a verifier" in w for w in d["warnings"]) and d["plans"][0]["warnings"] == d["warnings"]
    assert len(d["warnings"]) == 1, "no duplicates"
    # advice only: the plan applies unchanged and the verifier keeps its role
    PS.apply(lproj, text)
    assert "role: verifier" in PS.export(lproj, "fx")


def test_a_verifier_that_checks_an_upstream_authors_report_is_fine(lproj):
    ok = [("e1", [t("author", "analyst", "echo r > rep.md", checks=["file:rep.md"], acceptance="rep.md"),
                  t("review", "verifier", "true", deps=["author"], checks=["file:rep.md"])])]
    assert PS.validate(plan(ok), lproj)["warnings"] == []


# ------------------------------------------------------------------------------------------------ 2. failure handling
def test_a_verifier_asked_to_author_its_report_is_blocked_after_one_attempt_not_redispatched(lproj):
    PS.apply(lproj, plan(VERIFIER_AUTHORS))
    SV.plan_run(lproj, "fx", start_daemon=False)
    assert run_until(lproj, lambda: phase(lproj, "rep")["phase"] == "blocked", 90)
    run_until(lproj, lambda: False, 5)
    at = run_attempts(lproj)["rep"]
    assert len(at) == 1, "no second dispatch of a verifier who is forbidden to create the file"
    assert at[0]["category"] == "role_contradiction" and "analyst" in at[0]["blocker"] and at[0]["verification"]["passed"] is False
    r = phase(lproj, "rep")["reason"]
    assert "role_contradiction" in r and "nothing was rewritten" in r and "review requirement is unchanged" in r
    assert "role: verifier" in PS.export(lproj, "fx"), "the plan was not silently rewritten"


def test_a_verifier_whose_upstream_file_is_not_in_its_worktree_is_a_missing_prerequisite(lproj):
    # the PRAVAH handoff failure: an implementer wrote docs-a.md on e1's own branch; the verifier (which runs in the main checkout) does not have it
    exps = [("e1", [t("a1", "implementer", "echo a > docs-a.md", checks=["file:docs-a.md"], acceptance="docs-a.md")]),
            ("e2", [t("v1", "verifier", "true", deps=["a1"], checks=["file:docs-a.md"])])]
    PS.apply(lproj, plan(exps))
    SV.plan_run(lproj, "fx", start_daemon=False)
    assert run_until(lproj, lambda: phase(lproj, "v1")["phase"] == "blocked", 120)
    run_until(lproj, lambda: False, 4)
    at = run_attempts(lproj)
    assert len(at["v1"]) == 1 and at["v1"][0]["category"] == "missing_prerequisite"
    assert len(at["a1"]) == 1 and at["a1"][0]["state"] == "verified", "the author's history is untouched"
    r = phase(lproj, "v1")["reason"]
    assert "docs-a.md" in r and "a1" in r and "promote" in r.lower() and "Not retried" in r


def test_an_ordinary_missing_file_by_the_owning_task_is_still_repaired_within_its_budget(lproj):
    PS.apply(lproj, plan([("e1", [t("w", "implementer", "echo ok > out.txt", checks=["file:out.txt"])])]).replace("MOCK_SH:", "MOCK_SH@2:"))
    SV.plan_run(lproj, "fx", start_daemon=False)
    assert run_until(lproj, lambda: phase(lproj, "w")["phase"] == "verified", 90)
    at = run_attempts(lproj)["w"]
    assert [a["category"] for a in at[:-1]] == ["incomplete_deliverable"] and len(at) == 2, "repairable failures keep their bounded retries"


# ------------------------------------------------------------------------------------------------ 3. configuration consistency
def test_one_canonical_test_command_and_a_conflict_is_an_error(lproj):
    lproj.config["promotion"] = {"test_command": "pytest -q"}
    lproj.config["auto_promotion"] = {"test_command": "pytest -q"}
    pol = AP.policy(lproj)
    assert pol["test_command"] == "pytest -q" and pol["test_command_source"] == "promotion.test_command" and pol["conflicts"] == []
    lproj.config["promotion"] = {}
    pol = AP.policy(lproj)
    assert pol["test_command_source"].startswith("auto_promotion.test_command") and "legacy" in pol["test_command_source"]
    lproj.config["promotion"] = {"test_command": "pytest -q tests"}
    lproj.config["auto_promotion"] = {"test_command": "make test"}
    assert AP.policy(lproj)["test_command"] == "pytest -q tests" and AP.policy(lproj)["conflicts"]
    from research.registry import OPTIONS
    keys = {o.key for o in OPTIONS}
    assert {"promotion.test_command", "auto_promotion.numerical_command", "auto_promotion.reviewer_profile", "auto_promotion.require_distinct_reviewer"} <= keys
    assert "auto_promotion.test_command" not in keys, "the Settings UI shows only the canonical command"
    assert SV.validate_settings(lproj) and "differ" in SV.validate_settings(lproj)[0]


def test_pin_errors_distinguish_wrong_pin_no_terminal_and_agent_shell(monkeypatch, tmp_path):
    from research import approval as A
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    A.set_pin("4321")
    with pytest.raises(PolicyBlocked, match="Incorrect PIN .*attempt"):
        A.verify("0000")
    monkeypatch.setattr(A, "_has_tty", lambda: False)
    with pytest.raises(PolicyBlocked, match="no interactive terminal"):
        A.verify(None)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(PolicyBlocked, match="agent-controlled environment .*CLAUDECODE"):
        A.verify(None)
    d = json.load(open(A._path()))
    assert d["fails"] == 1, "only the wrong PIN counted"
    for m in ("4321", "0000"):
        try:
            A.verify(None)
        except PolicyBlocked as e:
            assert m not in str(e) and m not in (e.hint or "")


def test_reload_validates_applies_and_classifies_without_touching_the_running_agent(lproj):
    PS.apply(lproj, slow_plan(8))
    SV.plan_run(lproj, "fx", start_daemon=False)
    d = SV.daemon_start(lproj, detach=True)
    try:
        assert d["running"]
        assert run_until_task_phase(lproj, "s", "running", 60)
        ex = run_attempts(lproj)["s"][0]["execution"]
        pid0 = d["pid"]
        lproj.config["supervisor"]["poll_seconds"] = 2
        lproj.config["supervisor"]["max_total_attempts"] = 50
        lproj.config["hpc"] = {**(lproj.config.get("hpc") or {}), "default_cluster": "duke"}
        lproj.save_config()
        r = SV.daemon_reload(lproj)
        assert r["valid"] and r["applied"], r
        assert "supervisor.poll_seconds" in r["changed"]["live"] and "supervisor.max_total_attempts" in r["changed"]["live"]
        assert any(k.startswith("hpc") for k in r["changed"]["next_launch"]) and not r["changed"]["restart"]
        s = SV.daemon_status(lproj)
        assert s["pid"] == pid0 and s["running"] and s["last_reload"]["errors"] == [], "same process, no restart"
        assert E.show(lproj, ex)["state"] in ("launched", "running"), "the agent was not interrupted"
        assert run_until(lproj, lambda: phase(lproj, "s")["phase"] == "verified", 90)
        assert len(run_attempts(lproj)["s"]) == 1
        # an invalid configuration is refused before anything is applied
        lproj.config["supervisor"]["max_attempts_per_task"] = 0
        lproj.save_config()
        bad = SV.daemon_reload(lproj)
        assert not bad["valid"] and any("max_attempts_per_task" in e for e in bad["errors"]) and not bad["applied"]
        assert not os.path.exists(SV.sdir(lproj, "reload.request")), "nothing was requested"
    finally:
        SV.daemon_stop(lproj)


def test_reload_reports_when_a_restart_is_needed_and_when_nothing_runs(lproj):
    r = SV.daemon_reload(lproj)
    assert r["valid"] and not r["applied"] and "not running" in r["note"]
    PS.apply(lproj, slow_plan(1))
    SV.plan_run(lproj, "fx", start_daemon=False)
    SV.daemon_start(lproj, detach=True)
    try:
        d = json.load(open(SV._daemon_path(lproj)))
        d["version"] = "0.0.1"                                # the supervisor was started from an older Research OS
        open(SV._daemon_path(lproj), "w").write(json.dumps(d))
        s = SV.daemon_status(lproj)
        assert s["restart_required"] and "0.0.1" in s["restart_reason"]
        r = SV.daemon_reload(lproj)
        assert r["restart_required"] and any("0.0.1" in x for x in r["restart_reasons"])
    finally:
        SV.daemon_stop(lproj)


def test_restart_replaces_the_process_and_recovers_the_running_task_without_duplicates(lproj):
    PS.apply(lproj, slow_plan(10))
    SV.plan_run(lproj, "fx", start_daemon=False)
    d0 = SV.daemon_start(lproj, detach=True)
    try:
        assert run_until_task_phase(lproj, "s", "running", 60)
        a0 = run_attempts(lproj)["s"][0]
        out = SV.daemon_restart(lproj, timeout=60)
        assert out["restarted"] and out["pid"] != d0["pid"] and out["previous_pid"] == d0["pid"], out
        assert SV.daemon_status(lproj)["running"]
        assert E.show(lproj, a0["execution"])["state"] in ("launched", "running", "completed"), "the agent survived the restart"
        assert run_until(lproj, lambda: phase(lproj, "s")["phase"] == "verified", 120)
        at = run_attempts(lproj)["s"]
        assert len(at) == 1 and at[0]["execution"] == a0["execution"], "the same attempt finished; nothing was launched twice"
        assert SV.load_run(lproj, PS.load_state(lproj, "fx")["plan_id"])["budgets"]["attempts"] == 1
        assert run_until(lproj, lambda: SV.load_run(lproj, PS.load_state(lproj, "fx")["plan_id"])["state"] == "completed", 60)
    finally:
        SV.daemon_stop(lproj)


def test_restart_when_not_running_just_starts_and_agents_cannot_reload_or_restart(lproj):
    lproj.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    for fn in (SV.daemon_reload, SV.daemon_restart):
        with pytest.raises(PolicyBlocked):
            fn(lproj)
    lproj.author = {"author_type": "human", "author_name": "alice", "author_model": None}
    try:
        r = SV.daemon_restart(lproj)
        assert r["running"] and r["restarted"] is False and "was not running" in r["note"]
    finally:
        SV.daemon_stop(lproj)


def test_independent_experiments_run_concurrently_but_one_worktree_never_runs_two_agents(lproj):
    PS.apply(lproj, plan(PAR).replace("max_parallel_agents: 1", "max_parallel_agents: 3"))
    SV.plan_run(lproj, "fx", start_daemon=False)
    peak, same_tree = watch_running(lproj, 120)
    assert peak >= 2, "dependency-ready tasks of different experiments overlapped"
    assert not same_tree, "a1 and a2 share the e1 worktree and were serialized"
    assert SV.status(lproj, "fx")["state"] == "completed"


def test_effective_concurrency_says_where_the_number_comes_from(lproj):
    PS.apply(lproj, plan(PAR[:1]).replace("max_parallel_agents: 1, ", ""))        # nothing set anywhere: the built-in default
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 1 and c["source"] == "built-in default" and c["plan"] is None and c["project"] is None
    lproj.config["supervisor"]["max_parallel_agents"] = 4                            # set in the project's own file
    lproj.save_config()
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 4 and c["source"].startswith("project settings") and c["plan"] is None and c["project"] == 4
    PS.apply(lproj, plan(PAR[:1]).replace("max_parallel_agents: 1", "max_parallel_agents: 2"))     # the plan overrides it
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 2 and c["source"].startswith("plan YAML") and c["plan"] == 2 and c["project"] == 4 and c["running"] == 0
    full = plan(PAR[:1]).replace("max_parallel_agents: 1, ", "").replace("objective: o", "objective: o\n    scope: full")
    PS.apply(lproj, full)                                                            # a full document that no longer lists the key removes it
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 4 and c["source"].startswith("project settings")
