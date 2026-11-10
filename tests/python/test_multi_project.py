"""Several projects on one machine, one cluster and one chat gateway at the same time: nothing one project does may reach another. Real supervisors in threads,
a shared fake cluster, the real gateway with a fake channel."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time

import pytest

import fakecluster as FC
from conftest import git
from research import machine, planspec as PS, runs as R, services, supervisor as SV
from research.chat import alerts, commands, gateway
from research.chat.gateway import Gateway
from research.hpc import remote as RM
from research.store import Project
from research.util import ResearchError
from test_chat_core import Clock, FakeChannel, clock  # noqa: F401
from test_hpc import TRAIN
from test_supervisor import MOCK, run_until, task_yaml, yaml_plan


def init_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "train.py").write_text(TRAIN)
    git(path, "add", ".")
    git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def cluster(tmp_path):
    cl = FC.FakeCluster(str(tmp_path / "cluster"))
    restore = FC.install(cl)
    yield cl
    restore()


def hpc_project(tmp_path, cluster, name, slug):
    root = init_repo(tmp_path / name)
    p = Project.init(str(root), name=name, goal="g")
    p.config["hpc"] = FC.cluster_config(cluster, slug=slug)
    p.save_config()
    p.config = p.load_config()
    services.create_experiment(p, "train", hypothesis="h", parameters={"command": f"{sys.executable} train.py"}, metrics=["rmse"])
    return p


# ------------------------------------------------------------------------------------------------ one cluster, two projects
def test_two_projects_on_one_cluster_keep_their_runs_apart_under_concurrent_sync(tmp_path, cluster):
    a, b = hpc_project(tmp_path, cluster, "alpha", "alpha"), hpc_project(tmp_path, cluster, "beta", "beta")
    try:
        for p in (a, b):
            RM.init_workspace(p)
        ra, rb = RM.submit(a, "EXP-001", approve=True), RM.submit(b, "EXP-001", approve=True)
        assert ra["run"] == rb["run"] == "RUN-0001"                        # both projects number from 1: only the remote project directory tells them apart
        pa, pb = [(R.get_run(p, "RUN-0001")["slurm"]["hpc"]["remote_code"]) for p in (a, b)]
        assert pa != pb and "/alpha/" in pa and "/beta/" in pb
        errs = []

        def work(root):
            try:
                with Project(root) as q:                                    # one handle per thread (in real life: one process per project)
                    for _ in range(3):
                        RM.sync(q)
            except Exception as ex:      # noqa: BLE001
                import traceback
                errs.append(traceback.format_exc())
        ts = [threading.Thread(target=work, args=(p.root,)) for p in (a, b, a, b)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errs, "\n".join(errs)
        for p, other in ((a, b), (b, a)):
            run = R.get_run(p, "RUN-0001")
            assert run["status"] == "completed" and run["slurm"]["hpc"]["state"] == "collected"
            assert len(R.metrics_for_run(p, "RUN-0001")) == 3 and p.q1("SELECT COUNT(*) AS n FROM runs")["n"] == 1
            assert "slurm_job_id" in run and run["slurm_job_id"] != R.get_run(other, "RUN-0001")["slurm_job_id"]
    finally:
        a.close()
        b.close()


def test_a_project_never_reuses_another_projects_remote_directory(tmp_path, cluster):
    a = hpc_project(tmp_path, cluster, "alpha", "shared")
    b = hpc_project(tmp_path, cluster, "beta", "shared")             # the same slug by mistake
    try:
        RM.init_workspace(a)
        RM.submit(a, "EXP-001", approve=True)
        with pytest.raises(ResearchError) as e:
            RM.init_workspace(b)
        assert "belongs to another project ('alpha')" in str(e.value) and "project.slug" in getattr(e.value, "hint", "")
        with pytest.raises(ResearchError):
            RM.submit(b, "EXP-001", approve=True)
        assert b.q1("SELECT COUNT(*) AS n FROM runs")["n"] == 0        # nothing was created, nothing overwritten
        b.config["hpc"]["clusters"]["fake"]["project"]["slug"] = "beta"
        b.save_config()
        b.config = b.load_config()
        RM.init_workspace(b)
        assert RM.submit(b, "EXP-001", approve=True)["state"] == "submitted"
        again = RM.submit(a, "EXP-001", approve=True)                   # the owner is unaffected
        assert again["state"] == "submitted"
    finally:
        a.close()
        b.close()


# ------------------------------------------------------------------------------------------------ chat: labels and channels
def test_projects_created_by_init_get_distinct_stable_chat_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    roots = []
    for n in ("pravah", "sparse-ct"):
        root = tmp_path / n / "main"                                        # `research init` layout: every project's checkout is called main
        root.mkdir(parents=True)
        Project.init(str(root), name=n, goal="g").close()
        p = Project(str(root))
        p.save_config({"notifications.discord.enabled": True})
        p.close()
        roots.append(str(root))
    for order in (roots, roots[::-1], roots):                               # whichever project was touched last, the labels do not move
        for r in order:
            machine.remember(r)
            time.sleep(0.01)
        assert commands.enabled_projects("discord") == {"pravah": roots[0], "sparse-ct": roots[1]}
    assert commands.project_label("/x/proj") == "proj" and commands.project_label("/x/proj/main") == "proj"
    assert commands.canonical_label("main") == "main"                      # an old bare key that two projects share stays ambiguous: never guessed
    assert commands.canonical_label("pravah") == "pravah"


def test_an_old_label_maps_to_the_only_project_that_had_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "only" / "main"
    root.mkdir(parents=True)
    Project.init(str(root), name="only", goal="g").close()
    p = Project(str(root))
    p.save_config({"notifications.discord.enabled": True})
    p.close()
    machine.remember(str(root))
    assert commands.enabled_projects("discord") == {"only": str(root)}
    assert commands.canonical_label("main") == "only"                      # channels saved as "main" by an older version still reach this project


def run(coro):
    return asyncio.run(coro)


def test_concurrent_alerts_in_two_projects_reach_only_their_own_channel(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    roots = {}
    for n in ("alpha", "beta"):
        r = tmp_path / n
        r.mkdir()
        Project.init(str(r), name=n, goal="g").close()
        machine.remember(str(r))
        roots[n] = str(r)
    monkeypatch.setattr(commands, "enabled_projects", lambda platform: dict(roots))
    N = 25

    def raise_many(name):
        with Project(roots[name], sync=False) as p:
            for i in range(N):
                alerts.raise_(p, f"k{name}{i}", "task_failed", "error", f"{name} failure {i}", task=f"T-{i}")
    ts = [threading.Thread(target=raise_many, args=(n,)) for n in roots]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ch = FakeChannel()
    g = Gateway({"fake": ch}, now=clock, discover=False)
    for _ in range(4):
        run(g.pump_once())
        clock.t += 120                                                      # beyond the storm window
    texts = [m.plain() for m in ch.delivered]
    assert texts and not any("alpha" in t and "[beta]" in t or "beta" in t and "[alpha]" in t for t in texts)
    for name, other in (("alpha", "beta"), ("beta", "alpha")):
        mine = [m for m in ch.delivered if m.project == name]
        assert mine and all(other not in m.plain().replace(f"[{name}]", "") for m in mine)
    assert len(json.loads(open(gateway.gdir("cursors.json")).read())["outbox"]) == 2        # one cursor per project and platform, not shared


# ------------------------------------------------------------------------------------------------ two supervisors at once
def agent_project(tmp_path, name, script):
    root = init_repo(tmp_path / name)
    p = Project.init(str(root), name=name, goal="g")
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.save_config()
    p.config = p.load_config()
    return p


def test_two_projects_run_supervised_plans_at_the_same_time_without_touching_each_other(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    ps = {n: agent_project(tmp_path, n, script) for n in ("alpha", "beta")}
    try:
        for n, p in ps.items():
            y = yaml_plan([task_yaml("a", "A", f"echo {n} > a_{n}.txt", checks=[f"file:a_{n}.txt"]),
                           task_yaml("b", "B", f"test -f a_{n}.txt && echo done > b_{n}.txt", deps=["a"], checks=[f"file:b_{n}.txt"])])
            PS.apply(p, y)
            SV.plan_run(p, "demo-plan", start_daemon=False)
        errs, done = [], {}

        def drive(n):
            try:
                with Project(ps[n].root) as q:                              # one handle per thread (in real life: one process per project)
                    done[n] = run_until(q, lambda: SV.status(q, "demo-plan")["state"] == "completed", 150)
            except Exception as ex:      # noqa: BLE001
                errs.append((n, ex))
        ts = [threading.Thread(target=drive, args=(n,)) for n in ps]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errs and done == {"alpha": True, "beta": True}
        for n, p in ps.items():
            other = "beta" if n == "alpha" else "alpha"
            st = PS.load_state(p, "demo-plan")
            rs = SV.load_run(p, st["plan_id"])
            assert all(e["phase"] == "verified" for e in rs["tasks"].values()) and rs["state"] == "completed"
            assert not os.path.exists(os.path.join(p.root, f"a_{other}.txt"))
            assert all(other not in (ev["summary"] or "") for ev in p.q("SELECT summary FROM events WHERE action LIKE 'supervisor%'"))
    finally:
        for p in ps.values():
            p.close()
