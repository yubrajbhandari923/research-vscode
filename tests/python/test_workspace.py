"""Shared research ledger and experiment workspaces (git worktrees)."""
import json
import os
import subprocess
import sys

import pytest

from conftest import git
from research import workspace as W
from research.store import Project, find_root
from research.util import ResearchError


def cli(cwd, *args):
    r = subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=cwd, capture_output=True, text=True)
    return r


@pytest.fixture
def shared(repo, tmp_path):
    """A classic project migrated to a shared ledger; returns (main worktree, ledger path)."""
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    ledger = tmp_path / "ledger"
    W.enable(str(repo), str(ledger))
    return repo, ledger


def test_enable_is_a_dry_run_first_and_blocks_tracked_research(repo, tmp_path):
    Project.init(str(repo), name="demo").close()
    plan = W.plan_enable(str(repo), str(tmp_path / "led"))
    assert plan["ok"] and not os.path.exists(tmp_path / "led") and os.path.isdir(repo / ".research")
    git(repo, "add", "-f", ".research/config.yaml")
    plan = W.plan_enable(str(repo), str(tmp_path / "led"))
    assert not plan["ok"] and "tracked" in " ".join(plan["problems"])
    with pytest.raises(ResearchError):
        W.enable(str(repo), str(tmp_path / "led"))
    assert os.path.isdir(repo / ".research") and not os.path.exists(tmp_path / "led")


def test_enable_keeps_a_backup_and_every_record(repo, tmp_path):
    with Project.init(str(repo), name="demo") as p:
        q = p.next_id("Q")
    r = W.enable(str(repo), str(tmp_path / "led"))
    assert os.path.isdir(r["backup"]) and not os.path.exists(repo / ".research")
    with Project(str(repo)) as p:
        assert p.location.linked and p.ledger_root == str(tmp_path / "led")
        assert p.rdir == str(tmp_path / "led" / ".research")
        assert p.rel(p.path("runs", "RUN-0001")) == ".research/runs/RUN-0001"
        assert p.abspath(".research/runs/RUN-0001") == p.path("runs", "RUN-0001")
    assert find_root(str(repo)) == str(repo)


def test_classic_projects_are_unchanged(proj, repo):
    assert not proj.location.linked and proj.ledger_root == proj.root == str(repo)
    assert W.info(proj)["mode"] == "classic"
    with pytest.raises(ResearchError):
        W.create(proj, "EXP-001")


def test_two_worktrees_share_one_history(shared):
    repo, ledger = shared
    with Project(str(repo)) as p:
        qid = p.next_id("Q")
        from research import services
        q = services.create_question(p, "Which alpha?")
        e = services.create_experiment(p, "alpha sweep", question=q["id"], hypothesis="h")
        ws = W.create(p, e["id"], name="alpha")
    assert ws["branch"] == f"exp/{e['id']}-alpha" and os.path.isdir(ws["path"])
    # .git is a file in a linked worktree; discovery must still work
    assert os.path.isfile(os.path.join(ws["path"], ".git"))
    assert find_root(ws["path"]) == ws["path"]
    with Project(ws["path"]) as pw:
        assert pw.rdir == str(ledger / ".research")
        assert pw.get("experiment", e["id"])["title"] == "alpha sweep"
        from research import services
        q2 = services.create_question(pw, "from the experiment worktree")
        assert W.info(pw)["workspace"] == ws["id"]
    with Project(str(repo)) as p:
        assert p.get("question", q2["id"])["title"] == "from the experiment worktree"
        assert q2["id"] != q["id"]


def test_ids_never_collide_across_concurrent_worktrees(shared):
    repo, ledger = shared
    with Project(str(repo)) as p:
        from research import services
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        wss = [W.create(p, e["id"], name=f"v{i}", path=str(repo.parent / f"wt{i}")) for i in range(3)]
    procs = []
    for ws in wss:
        code = ("from research.store import Project; from research import services\n"
                "p=Project.find()\n"
                "for i in range(6): print(services.create_question(p,'q%d'%i)['id'])\n")
        procs.append(subprocess.Popen([sys.executable, "-c", code], cwd=ws["path"], stdout=subprocess.PIPE, text=True))
    ids = [l for pr in procs for l in pr.communicate()[0].split()]
    assert len(ids) == 18 and len(set(ids)) == 18


def test_run_records_workspace_and_a_durable_snapshot(shared):
    repo, _ = shared
    with Project(str(repo)) as p:
        from research import services, runs
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
    wt = ws["path"]
    open(os.path.join(wt, "new_file.py"), "w").write("x = 1\n")      # untracked
    open(os.path.join(wt, "train.py"), "a").write("# edit\n")        # tracked, dirty
    with Project(wt) as pw:
        from research import runs
        r = runs.create_run(pw, e["id"], command="python train.py", backend="manual")
        env = json.loads(pw.q1("SELECT env FROM runs WHERE id=?", (r["id"],))["env"])
    info = env["workspace"]
    assert info["id"] == ws["id"] and info["branch"] == ws["branch"]
    snap = info["snapshot"]
    assert snap["differs_from_head"]
    shown = subprocess.run(["git", "show", f"{snap['commit']}:new_file.py"], cwd=wt, capture_output=True, text=True)
    assert shown.stdout == "x = 1\n"
    git(wt, "checkout", "--", "train.py")
    os.remove(os.path.join(wt, "new_file.py"))
    # the snapshot survives cleanup because a ref pins it
    assert subprocess.run(["git", "cat-file", "-e", snap["commit"]], cwd=wt).returncode == 0


def test_rebuild_after_deleting_the_index_and_branch_deletion(shared):
    repo, ledger = shared
    with Project(str(repo)) as p:
        from research import services
        q = services.create_question(p, "q")
        e = services.create_experiment(p, "e", hypothesis="h", question=q["id"])
        ws = W.create(p, e["id"])
    W.archive(Project(str(repo)), ws["id"])
    git(repo, "branch", "-D", ws["branch"])
    for f in ("research.db", "research.db-wal", "research.db-shm"):
        try:
            os.remove(ledger / ".research" / f)
        except FileNotFoundError:
            pass
    with Project(str(repo)) as p:
        assert p.get("experiment", e["id"])["title"] == "e"
        assert [w for w in W.list_workspaces(p, include_archived=True)][0]["status"] == "archived"


def test_archive_refuses_to_lose_work_and_keeps_the_branch(shared):
    repo, _ = shared
    with Project(str(repo)) as p:
        from research import services
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
        open(os.path.join(ws["path"], "scratch.txt"), "w").write("keep me")
        impact = W.archive_impact(p, ws["id"])
        assert impact["blockers"]
        with pytest.raises(ResearchError):
            W.archive(p, ws["id"])
        assert os.path.exists(os.path.join(ws["path"], "scratch.txt"))
        os.remove(os.path.join(ws["path"], "scratch.txt"))
        out = W.archive(p, ws["id"], reason="done")
        assert not os.path.exists(ws["path"])
    assert subprocess.run(["git", "rev-parse", "--verify", ws["branch"]], cwd=repo, capture_output=True).returncode == 0


def test_a_worktree_that_disappears_is_reported_not_fatal(shared):
    repo, _ = shared
    import shutil
    with Project(str(repo)) as p:
        from research import services
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
        shutil.rmtree(ws["path"])
        s = W.show(p, ws["id"])
        assert s["present"] is False and s["status"] == "active"


def test_cli_ledger_dry_run_then_apply(repo, tmp_path):
    Project.init(str(repo), name="demo").close()
    led = str(tmp_path / "cli-ledger")
    r = cli(repo, "ledger", "enable", led)
    assert r.returncode == 0 and "dry run" in r.stdout and not os.path.exists(led)
    r = cli(repo, "ledger", "enable", led, "--apply")
    assert r.returncode == 0, r.stderr
    r = cli(repo, "ws", "list")
    assert r.returncode == 0
    assert cli(repo, "ledger", "status", "--json").stdout.count("shared-ledger") == 1


def test_dispatch_runs_in_the_linked_workspace_and_says_so(shared):
    repo, _ = shared
    with Project(str(repo)) as p:
        from research import services, plans, agents
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
        pl = plans.create_plan(p, "plan", objective="o")
        t = plans.create_task(p, pl["id"], "do it", goal="g")
        W.link_task(p, ws["id"], t["id"])
        d = agents.dispatch(p, "implementer", t["id"])
        assert d["cwd"] == ws["path"] and d["env"]["RESEARCH_ROOT"] == ws["path"] and d["workspace"] == ws["id"]
        brief = open(p.abspath(d["brief"])).read()
        assert "## Workspace" in brief and ws["branch"] in brief
        other = plans.create_task(p, pl["id"], "unlinked", goal="g")
        assert agents.dispatch(p, "implementer", other["id"])["cwd"] == str(repo)


def test_hpc_manifest_pins_the_exact_source(shared):
    repo, _ = shared
    from research.hpc import bundle
    with Project(str(repo)) as p:
        from research import services
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
    open(os.path.join(ws["path"], "uncommitted.py"), "w").write("y = 2\n")
    with Project(ws["path"]) as pw:
        m = bundle.manifest(pw, e["id"], [])
    snap = m["provenance"]["workspace"]["snapshot"]
    shown = subprocess.run(["git", "show", f"{snap['commit']}:uncommitted.py"], cwd=ws["path"], capture_output=True, text=True)
    assert shown.stdout == "y = 2\n" and m["provenance"]["workspace"]["id"] == ws["id"]
