"""Selective promotion: only chosen changes reach main, only through verified, reviewed, human-approved, fast-forward integration."""
import json
import os
import subprocess
import sys

import pytest

from conftest import git
from research import promotion as PR, services, workspace as W
from research.store import Project
from research.util import PolicyBlocked, ResearchError

ALICE = {"author_type": "human", "author_name": "alice"}
BOB = {"author_type": "human", "author_name": "bob"}
CLAUDE = {"author_type": "agent", "author_name": "claude"}


def commit(path, name, text, msg):
    with open(os.path.join(path, name), "w") as f:
        f.write(text)
    git(path, "add", name)
    git(path, "commit", "-qm", msg)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()


def head(path, ref="HEAD"):
    return subprocess.run(["git", "rev-parse", ref], cwd=path, capture_output=True, text=True).stdout.strip()


def files_in(path, ref):
    return set(subprocess.run(["git", "ls-tree", "-r", "--name-only", ref], cwd=path, capture_output=True, text=True).stdout.split())


@pytest.fixture
def world(repo, tmp_path):
    """Shared-ledger project with an experiment workspace holding three commits (A good, B experimental, C good)."""
    Project.init(str(repo), name="d").close()
    W.enable(str(repo), str(tmp_path / "ledger"))
    p = Project(str(repo))
    p.author = dict(ALICE)
    p.save_config({"governance.approval.require_pin": False, "promotion.test_command": "test -f solver.py && test ! -e scratch_notes.txt", "promotion.require_supported_finding": False})
    p.config = p.load_config()
    q = services.create_question(p, "q")
    e = services.create_experiment(p, "faster solver", question=q["id"], hypothesis="h")
    ws = W.create(p, e["id"], name="solver")
    wt = ws["path"]
    a = commit(wt, "solver.py", "def solve(): return 1\n", "add solver")
    b = commit(wt, "scratch_notes.txt", "dead end\n", "scratch experiment")
    c = commit(wt, "solver.py", "def solve(): return 2\n", "tune solver")
    yield {"p": p, "repo": str(repo), "ws": ws, "exp": e, "commits": (a, b, c), "wt": wt}
    p.close()


def candidate(w, commits=None, paths=None, **kw):
    a, b, c = w["commits"]
    return PR.prepare(w["p"], w["ws"]["id"], commits=commits if commits is not None else ([a, c] if not paths else None), paths=paths, **kw)


def test_changes_lists_what_could_be_promoted_and_flags_uncommitted_work(world):
    open(os.path.join(world["wt"], "wip.py"), "w").write("x")
    ch = PR.changes(world["p"], world["ws"]["id"])
    assert [c["subject"] for c in ch["commits"]] == ["add solver", "scratch experiment", "tune solver"]
    assert ch["commits"][1]["files"] == ["scratch_notes.txt"] and ch["uncommitted"] and "commit" in ch["note"].lower()


def test_prepare_takes_only_the_selected_commits_on_top_of_the_current_main(world):
    repo, p = world["repo"], world["p"]
    main_before = commit(repo, "unrelated.txt", "someone else's work\n", "unrelated main change")   # main moved after the experiment started
    c = candidate(world)
    assert c["state"] == "prepared" and c["classification"]["reusable"]
    assert files_in(repo, c["branch"]) == {"train.py", "unrelated.txt", "solver.py"}      # selected + current main; not the scratch file
    assert head(repo, "main") == main_before, "main is untouched by preparation"
    assert "scratch_notes.txt" in files_in(repo, world["ws"]["branch"]), "the experiment workspace is untouched"
    d = PR.diff(p, c["id"])
    assert "solver.py" in d["patch"] and "scratch_notes" not in d["patch"] and "unrelated" not in d["patch"]


def test_prepare_by_paths_and_unsafe_selections(world):
    p = world["p"]
    c = PR.prepare(p, world["ws"]["id"], paths=["solver.py"])
    assert files_in(world["repo"], c["branch"]) == {"train.py", "solver.py"}
    for bad in ("../outside", "/etc/passwd", ".research/config.yaml", "nonexistent.py"):
        with pytest.raises(ResearchError):
            PR.prepare(p, world["ws"]["id"], paths=[bad])
    with pytest.raises(ResearchError, match="exactly one"):
        PR.prepare(p, world["ws"]["id"], commits=list(world["commits"][:1]), paths=["solver.py"])
    with pytest.raises(ResearchError, match="not a commit"):
        PR.prepare(p, world["ws"]["id"], commits=[head(world["repo"])])


def test_failing_tests_block_review_and_approval(world):
    p = world["p"]
    c = PR.prepare(p, world["ws"]["id"], commits=[world["commits"][1]])        # only the scratch file: no solver.py → tests fail
    c = PR.verify(p, c["id"])
    assert c["gates"]["tests"]["state"] == "failed" and c["state"] == "prepared" and not c["classification"]["technically_correct"]
    p.author = dict(BOB)
    with pytest.raises(PolicyBlocked, match="passing tests"):
        PR.review(p, c["id"], "approve")
    p.author = dict(ALICE)
    with pytest.raises(PolicyBlocked, match="open gates"):
        PR.approve(p, c["id"])


def test_the_whole_path_to_main_needs_independent_review_and_a_human(world):
    p, repo = world["p"], world["repo"]
    c = candidate(world)
    assert PR.verify(p, c["id"])["state"] == "verified"
    with pytest.raises(PolicyBlocked, match="own promotion"):
        PR.review(p, c["id"], "approve")                                   # alice prepared it
    p.author = dict(CLAUDE)
    assert PR.review(p, c["id"], "approve", "reads correctly")["state"] == "reviewed"   # an agent may review
    with pytest.raises(PolicyBlocked, match="Only a human"):
        PR.approve(p, c["id"])
    p.author = dict(BOB)
    assert PR.approve(p, c["id"])["state"] == "approved"
    assert head(repo, "main") == c["base_main"], "approval does not move main"
    p.author = dict(CLAUDE)
    with pytest.raises(PolicyBlocked):
        PR.integrate(p, c["id"], confirm=True)                              # agents never integrate
    p.author = dict(BOB)
    with pytest.raises(PolicyBlocked, match="--confirm"):
        PR.integrate(p, c["id"])
    done = PR.integrate(p, c["id"], confirm=True)
    assert done["state"] == "promoted" and done["classification"]["promoted"]
    assert head(repo, "main") == done["integration"]["commit"] == head(repo, c["branch"])
    assert files_in(repo, "main") == {"train.py", "solver.py"}, "scratch_notes.txt never reached main"
    assert head(repo, world["ws"]["branch"]) and subprocess.run(["git", "rev-parse", "--verify", c["branch"]], cwd=repo, capture_output=True).returncode == 0
    ev = p.conn.execute("SELECT summary FROM events WHERE entity_type='promotion' AND action='promoted'").fetchone()[0]
    assert "merged into main" in ev


def test_a_candidate_that_changes_after_approval_must_be_re_approved(world):
    p = world["p"]
    c = candidate(world)
    PR.verify(p, c["id"])
    p.author = dict(BOB)
    PR.review(p, c["id"], "approve")
    PR.approve(p, c["id"])
    commit(c["path"], "extra.py", "sneaky\n", "late addition")
    s = PR.show(p, c["id"])
    assert s["gates"]["approval"]["state"] == "stale" and s["state"] != "approved" and not s["classification"]["integration_ready"]
    before = head(world["repo"], "main")
    with pytest.raises(PolicyBlocked):
        PR.integrate(p, c["id"], confirm=True)
    assert head(world["repo"], "main") == before


def test_integration_is_refused_when_main_moved_or_is_dirty_and_never_forces(world):
    p, repo = world["p"], world["repo"]
    c = candidate(world)
    PR.verify(p, c["id"])
    p.author = dict(BOB)
    PR.review(p, c["id"], "approve")
    PR.approve(p, c["id"])
    open(os.path.join(repo, "train.py"), "a").write("# uncommitted\n")
    with pytest.raises(ResearchError, match="uncommitted changes"):
        PR.integrate(p, c["id"], confirm=True)
    git(repo, "checkout", "--", "train.py")
    moved = commit(repo, "later.txt", "x\n", "main moved on")
    with pytest.raises(ResearchError, match="moved since"):
        PR.integrate(p, c["id"], confirm=True)
    assert head(repo, "main") == moved and PR.show(p, c["id"])["state"] == "approved"


def test_scientific_evidence_gate_blocks_approval_until_a_finding_is_supported(world):
    p = world["p"]
    p.save_config({"promotion.require_supported_finding": True})
    p.config = p.load_config()
    c = candidate(world)
    PR.verify(p, c["id"])
    p.author = dict(CLAUDE)
    PR.review(p, c["id"], "approve")
    p.author = dict(BOB)
    assert PR.show(p, c["id"])["gates"]["evidence"]["state"] == "blocked"
    with pytest.raises(PolicyBlocked, match="scientific evidence"):
        PR.approve(p, c["id"])


def test_an_unsuccessful_experiment_stays_documented_when_its_candidate_is_abandoned(world):
    p, e = world["p"], world["exp"]
    c = candidate(world)
    PR.abandon(p, c["id"], "approach did not generalize")
    assert PR.list_candidates(p) == [] and PR.list_candidates(p, include_closed=True)[0]["state"] == "abandoned"
    assert p.get("experiment", e["id"])["title"] == "faster solver"
    assert W.show(p, world["ws"]["id"])["status"] == "active"           # code and workspace are not deleted by abandoning
    assert subprocess.run(["git", "rev-parse", "--verify", c["branch"]], cwd=world["repo"], capture_output=True).returncode == 0


def test_the_five_questions_are_reported_separately(world):
    p = world["p"]
    c = candidate(world)
    k = c["classification"]
    assert k == {"evidence_supported": None, "scientifically_successful": None, "technically_correct": None, "reusable": True, "integration_ready": False, "promoted": False}


def test_a_supported_finding_is_supported_evidence_not_a_successful_experiment():
    """TrueCT F-001: an independently supported finding that the pilot FAILED its QC used to yield scientifically_successful=True."""
    from research import promotion as PR

    class P:
        status = "failed"

        def exists(self, kind, ident):
            return True

        def get(self, kind, ident):
            return {"status": self.status}
    gates = {"evidence": {"state": "passed"}, "tests": {"state": "passed"}, "review": {"state": "approved"}, "scientific_content": {"state": "not_applicable"}, "approval": {"state": "approved"}}
    c = {"experiment": "EXP-001", "selection": {"paths": ["src/x.py"]}}
    k = PR._classify(P(), c, gates)
    assert k["evidence_supported"] is True and k["scientifically_successful"] is False and k["integration_ready"] is True, "reusable, well-evidenced code from a failed experiment"
    P.status = "completed"
    assert PR._classify(P(), c, gates)["scientifically_successful"] is True
    P.status = "running"
    assert PR._classify(P(), c, gates)["scientifically_successful"] is None, "not known yet is different from no"


def test_cli_lists_and_agents_cannot_approve_through_it(world):
    repo = world["repo"]
    c = candidate(world)
    r = subprocess.run([sys.executable, "-m", "research_cli", "promote", "list", "--json"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)[0]["id"] == c["id"]
    r = subprocess.run([sys.executable, "-m", "research_cli", "promote", "approve", c["id"]], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "RESEARCH_AGENT": "claude"})
    assert r.returncode == 3


def test_tests_only_count_for_the_exact_clean_revision_they_ran_against(world):
    p = world["p"]
    c = candidate(world)
    assert PR.verify(p, c["id"])["gates"]["tests"]["state"] == "passed"
    open(os.path.join(c["path"], "solver.py"), "a").write("# edited after the tests, not committed\n")
    s = PR.show(p, c["id"])
    assert s["gates"]["tests"]["state"] == "stale" and s["state"] == "prepared" and not s["classification"]["technically_correct"]
    with pytest.raises(ResearchError, match="uncommitted"):
        PR.verify(p, c["id"])                       # cannot "verify" a tree that matches no commit
    git(c["path"], "commit", "-qam", "tweak")
    assert PR.show(p, c["id"])["gates"]["tests"]["state"] == "unverified"     # new revision: the old result does not carry over
    assert PR.verify(p, c["id"])["gates"]["tests"]["state"] == "passed"
    # the recorded result names the commit that was tested
    ver = [r for r in PR._records(p) if r["op"] == "verify"]
    assert ver[-1]["head"] == head(c["path"]) and ver[0]["head"] != ver[-1]["head"] and ver[-1]["command"] and ver[-1]["exit_code"] == 0
