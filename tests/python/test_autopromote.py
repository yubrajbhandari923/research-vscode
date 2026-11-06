"""Opt-in unattended code promotion: real git, real supervisor ticks and a scripted agent. The independent reviewer is a stand-in that
posts through the real `promotion.review` (its dispatch of a live provider is not exercised here)."""
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from conftest import git
from research import autopromote as AP, planspec as PS, promotion as PR, services, supervisor as SV, workspace as W
from research.store import Project
from research.util import PolicyBlocked, ResearchError, now_iso
from test_supervisor import MOCK, run_until

AGENT = {"author_type": "agent", "author_name": "claude", "author_model": None}


def out(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def ap(repo, tmp_path, monkeypatch):
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["agents"]["profiles"]["rev"] = {"command": sys.executable}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.config["promotion"] = {"require_supported_finding": True}          # science is NOT required for code promotion
    p.config["auto_promotion"] = {"test_command": "ls lib*.py", "numerical_command": "true", "reviewer_profile": "rev"}
    p.save_config()
    install_reviewer(monkeypatch, p)
    yield p
    p.close()


def install_reviewer(monkeypatch, p, verdict="approve", name="reviewer", before=None):
    def fake(p_, item, pol):
        if before:
            before()
        human, p_.author = p_.author, {"author_type": "agent", "author_name": name, "author_model": None}
        try:
            PR.review(p_, item["promotion"], verdict, "scripted independent review")
        finally:
            p_.author = human
        return {"at": now_iso(), "epoch": time.time(), "profile": name, "pid": 0}
    monkeypatch.setattr(AP, "_launch_reviewer", fake)


def task(key, shell, deps=(), checks=(), extra=""):
    d = f"depends_on: [{', '.join(deps)}], " if deps else ""
    c = f"checks: [{', '.join(repr(x) for x in checks)}], " if checks else ""
    return f'{{key: {key}, title: T {key}, {d}{c}{extra}agent: {{role: implementer, profile: fake}}, prompt: "Work.\\nMOCK_SH: {shell}"}}'


def plan(exps, auto=True, key="ap"):
    body = ""
    for ek, tasks in exps:
        body += f"      - key: {ek}\n        title: Exp {ek}\n        hypothesis: h {ek}\n        tasks:\n" + "".join(f"          - {t}\n" for t in tasks)
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: {key}
            title: Auto
            objective: o
            execution: {{max_parallel_agents: 1, max_attempts_per_task: 1{', auto_promotion: true' if auto else ''}}}
            experiments:
        """) + body


CHAIN = [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])]),
         ("e2", [task("b1", "test -f lib1.py && echo V=2 > lib2.py", deps=["a1"], checks=["file:lib2.py"])]),
         ("e3", [task("c1", "test -f lib1.py && test -f lib2.py && echo V=3 > lib3.py", deps=["b1"], checks=["file:lib3.py"])])]


def start(p, exps, authorize=True):
    PS.apply(p, plan(exps))
    SV.plan_run(p, "ap", start_daemon=False)
    if authorize:
        SV.autopromote(p, "ap", "authorize", note="overnight, reviewed YAML")
    return PS.load_state(p, "ap")["plan_id"]


def items(p, pid):
    return SV.load_run(p, pid)["autopromote"]["items"]


def item_status(p, pid):
    return [i["status"] for i in items(p, pid)]


def run_to(p, pid, pred, timeout=90):
    ok = run_until(p, lambda: pred(SV.load_run(p, pid)), timeout)
    assert ok, json.dumps(SV.load_run(p, pid).get("autopromote"), indent=1, default=str)


def head(path, ref="HEAD"):
    return out(path, "rev-parse", ref)


# ------------------------------------------------------------------------------------------------ success
def test_three_dependent_experiments_promote_in_order_and_each_starts_from_the_integrated_main(ap, repo):
    pid = start(ap, CHAIN)
    run_to(ap, pid, lambda rs: rs["state"] == "completed")
    its = items(ap, pid)
    assert [i["experiment"] for i in its] == [PS.load_state(ap, "ap")["experiments"][k] for k in ("e1", "e2", "e3")] and set(item_status(ap, pid)) == {"integrated"}
    for f, v in (("lib1.py", "V=1"), ("lib2.py", "V=2"), ("lib3.py", "V=3")):
        assert open(os.path.join(repo, f)).read().strip() == v
    assert out(repo, "rev-list", "--merges", "main") == "", "history stays linear: fast-forward only"
    # each downstream worktree was created from the main that already contained the upstream code
    wss = {w["experiment"]: w for w in W.list_workspaces(ap)}
    for prev, nxt in zip(its, its[1:]):
        assert out(repo, "merge-base", "--is-ancestor", prev["main_after"], wss[nxt["experiment"]]["head"]) == "" and \
            subprocess.run(["git", "merge-base", "--is-ancestor", prev["main_after"], wss[nxt["experiment"]]["head"]], cwd=repo).returncode == 0
        assert wss[nxt["experiment"]]["base_rev"] == prev["main_after"], "worktree starts from the integrated main SHA"
    # provenance: system/supervisor acted, naming the authorizing human; a human approval is never impersonated
    recs = [json.loads(l) for l in open(ap.path("promotions.jsonl"))]
    for r in recs:
        if r["op"] in ("approve", "integrate"):
            assert r["author_type"] == "system" and r["author_name"] == "supervisor" and r["mode"] == "supervisor_authorized" and r["authorized_by"]
    assert os.path.exists(ap.path("supervisor", "promotions", "AP-001.json"))
    i = its[0]
    assert i["tests"]["state"] == "passed" and i["numerical"]["state"] == "passed" and i["review"]["by"] == "reviewer" and i["base_main"] and i["candidate_head"] and i["main_after"]


def test_default_is_manual_declared_but_unauthorized_does_nothing(ap, repo):
    main0 = head(repo, "main")
    pid = start(ap, [CHAIN[0], ("e2", [task("b1", "echo V=2 > lib2.py", deps=["a1"], checks=["file:lib2.py"])])], authorize=False)
    run_to(ap, pid, lambda rs: rs["state"] == "completed")
    assert items(ap, pid) == [] and head(repo, "main") == main0 and not os.path.exists(os.path.join(repo, "lib1.py"))
    assert SV.status(ap, "ap")["auto_promotion"]["mode"] == "manual"
    # and a plan that never declares it cannot be authorized
    PS.apply(ap, plan(CHAIN[:1], auto=False, key="plain"))
    SV.plan_run(ap, "plain", start_daemon=False)
    with pytest.raises(ResearchError, match="does not declare"):
        SV.autopromote(ap, "plain", "authorize", note="x")


# ------------------------------------------------------------------------------------------------ governance
def test_agents_cannot_authorize_approve_integrate_or_change_the_policy_silently(ap, repo):
    pid = start(ap, CHAIN[:1], authorize=False)
    ap.author = dict(AGENT)
    with pytest.raises(PolicyBlocked):
        SV.autopromote(ap, "ap", "authorize", note="I want it")
    ap.author = {"author_type": "human", "author_name": "alice", "author_model": None}
    SV.autopromote(ap, "ap", "authorize", note="ok")
    for who in ({"author_type": "agent", "author_name": "supervisor"}, {"author_type": "human", "author_name": "alice"}, {"author_type": "system", "author_name": "supervisor"}):
        ap.author = dict(who, author_model=None)
        with pytest.raises(PolicyBlocked):                      # no in-process grant -> nobody outside the supervisor tick can use the path
            PR._require_supervisor(ap, {"id": "x", "auto": {}})
    ap.author = {"author_type": "human", "author_name": "alice", "author_model": None}
    # changing the verification command suspends the authorization instead of silently running the new command
    ap.config["auto_promotion"]["test_command"] = "true"
    assert SV.status(ap, "ap")["auto_promotion"]["mode"] == "suspended"
    main0 = head(repo, "main")
    run_until(ap, lambda: False, 6)
    assert items(ap, pid) == [] and head(repo, "main") == main0
    SV.autopromote(ap, "ap", "revoke")
    assert SV.status(ap, "ap")["auto_promotion"]["mode"] == "manual"


def test_the_preparer_and_the_implementer_cannot_review_their_own_work(ap, repo):
    pid = start(ap, CHAIN[:1])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "prepared")
    promo = items(ap, pid)[0]["promotion"]
    for who in ({"author_type": "system", "author_name": "supervisor"}, {"author_type": "agent", "author_name": "fake"}):   # preparer; implementing profile
        ap.author = dict(who, author_model=None)
        with pytest.raises(PolicyBlocked):
            PR.review(ap, promo, "approve")
    ap.author = {"author_type": "human", "author_name": "alice", "author_model": None}


# ------------------------------------------------------------------------------------------------ gates fail closed
def test_failing_integration_tests_pause_the_item_and_block_downstream(ap, repo):
    ap.config["auto_promotion"]["test_command"] = "false"
    ap.save_config()
    pid = start(ap, CHAIN[:2])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused")
    run_until(ap, lambda: False, 4)
    it = items(ap, pid)[0]
    assert "integration tests failed" in it["error"] and head(repo, "main") == it["base_main"]
    assert not os.path.exists(os.path.join(repo, "lib1.py"))
    assert {t["key"]: t["phase"] for t in SV.status(ap, "ap")["tasks"]}["b1"] == "waiting", "downstream never launched"
    assert it["promotion"] and PR.show(ap, it["promotion"])["present"], "the failed candidate is kept for inspection"


def test_failing_numerical_checks_pause_after_tests_pass(ap, repo):
    ap.config["auto_promotion"]["numerical_command"] = "false"
    ap.save_config()
    pid = start(ap, CHAIN[:1])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused")
    it = items(ap, pid)[0]
    assert "numerical checks failed" in it["error"] and it["tests"]["state"] == "passed" and head(repo, "main") == it["base_main"]


def test_review_requesting_changes_pauses(ap, repo, monkeypatch):
    install_reviewer(monkeypatch, ap, verdict="changes")
    pid = start(ap, CHAIN[:1])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused")
    it = items(ap, pid)[0]
    assert "requested changes" in it["error"] and not os.path.exists(os.path.join(repo, "lib1.py"))


def test_promoted_work_may_not_edit_existing_tests_or_add_bulky_files(ap, repo):
    os.makedirs(os.path.join(repo, "tests"), exist_ok=True)
    open(os.path.join(repo, "tests", "test_core.py"), "w").write("def test_a():\n    assert True\n")
    git(repo, "add", "tests")
    git(repo, "commit", "-qm", "existing test")
    pid = start(ap, [("e1", [task("a1", "echo V=1 > lib1.py; echo weakened > tests/test_core.py", checks=["file:lib1.py"])])])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused")
    assert "may not modify or delete an existing test" in items(ap, pid)[0]["error"]
    assert open(os.path.join(repo, "tests", "test_core.py")).read().startswith("def test_a")
    ap.config["auto_promotion"]["max_file_mb"] = 0.00001
    ap.save_config()
    assert "exceeds" in _guard_msg(ap, repo)


def _guard_msg(p, repo):
    d = os.path.join(os.path.dirname(repo), "guard")
    git(repo, "worktree", "add", "-q", "-b", "guard", d)
    open(os.path.join(d, "big.bin"), "w").write("x" * 100)
    git(d, "add", "big.bin")
    git(d, "commit", "-qm", "big")
    try:
        AP._guard_diff(d, "main", "HEAD", AP.policy(p))
    except PolicyBlocked as e:
        return str(e)
    return ""


# ------------------------------------------------------------------------------------------------ main protection
def test_dirty_main_is_never_overwritten_and_untracked_files_survive(ap, repo, monkeypatch):
    def dirty():
        open(os.path.join(repo, "train.py"), "a").write("# my unsaved edit\n")
        open(os.path.join(repo, "notes.txt"), "w").write("untracked\n")
    install_reviewer(monkeypatch, ap, before=dirty)
    pid = start(ap, CHAIN[:1])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused")
    it = items(ap, pid)[0]
    assert "uncommitted changes" in it["error"] and head(repo, "main") == it["base_main"]
    assert "my unsaved edit" in open(os.path.join(repo, "train.py")).read() and open(os.path.join(repo, "notes.txt")).read() == "untracked\n"


def test_main_moving_after_the_candidate_was_tested_pauses_without_merging(ap, repo, monkeypatch):
    moved = {}

    def move():
        open(os.path.join(repo, "other.txt"), "w").write("someone else\n")
        git(repo, "add", "other.txt")
        git(repo, "commit", "-qm", "human commit on main")
        moved["sha"] = head(repo, "main")
    install_reviewer(monkeypatch, ap, before=move)
    pid = start(ap, CHAIN[:1])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused")
    assert "moved" in items(ap, pid)[0]["error"] and head(repo, "main") == moved["sha"] and not os.path.exists(os.path.join(repo, "lib1.py"))
    # a human decides: retry prepares a NEW candidate from the current main and re-tests it
    install_reviewer(monkeypatch, ap)
    SV.autopromote(ap, "ap", "retry", item="AP-001")
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"][0]["status"] == "integrated")
    assert os.path.exists(os.path.join(repo, "lib1.py")) and os.path.exists(os.path.join(repo, "other.txt"))
    assert out(repo, "rev-list", "--merges", "main") == ""


# ------------------------------------------------------------------------------------------------ tracking
def test_no_op_tasks_create_nothing_to_promote(ap, repo):
    main0 = head(repo, "main")
    pid = start(ap, [("e1", [task("a1", "true", checks=["file:train.py"])])])
    run_to(ap, pid, lambda rs: rs["state"] == "completed")
    assert items(ap, pid) == [] and head(repo, "main") == main0


def test_later_commits_in_the_same_experiment_promote_only_what_is_new(ap, repo):
    exps = [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"]),
                    task("a2", "echo V=4 > lib4.py", deps=["b1"], checks=["file:lib4.py"])]),
            ("e2", [task("b1", "test -f lib1.py && echo V=2 > lib2.py", deps=["a1"], checks=["file:lib2.py"])])]
    pid = start(ap, exps)
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 120)
    its = items(ap, pid)
    e1 = [i for i in its if i["experiment"] == PS.load_state(ap, "ap")["experiments"]["e1"]]
    assert len(e1) == 2 and [len(i["sources"]) for i in e1] == [1, 1], "second promotion carries only the new commit"
    assert e1[0]["sources"][0]["sha"] != e1[1]["sources"][0]["sha"] and set(item_status(ap, pid)) == {"integrated"}
    assert all(os.path.exists(os.path.join(repo, f)) for f in ("lib1.py", "lib2.py", "lib4.py"))
    assert len(_syncs(ap, pid)) >= 1, "the diverged experiment branch received main by an explicit, recorded merge"


def _syncs(p, pid):
    return SV.load_run(p, pid)["autopromote"].get("syncs", [])


# ------------------------------------------------------------------------------------------------ science vs code
def test_a_negative_hypothesis_with_valid_code_still_promotes_and_no_claim_is_approved(ap, repo):
    pid = start(ap, CHAIN[:1])
    e1 = PS.load_state(ap, "ap")["experiments"]["e1"]
    f = services.create_finding(ap, "alpha has no effect", statement="negative", status="contradicted", supports=[e1])
    before = ap.list("finding")
    run_to(ap, pid, lambda rs: rs["state"] == "completed")
    assert os.path.exists(os.path.join(repo, "lib1.py"))
    after = ap.list("finding")
    assert [(x["id"], x["status"]) for x in after] == [(x["id"], x["status"]) for x in before], "findings untouched"
    c = PR.show(ap, items(ap, pid)[0]["promotion"])
    assert c["gates"]["evidence"]["state"] == "info" and "not implied" in c["gates"]["evidence"]["detail"]
    assert c["classification"]["scientifically_successful"] is not True


# ------------------------------------------------------------------------------------------------ restart / idempotency
def test_restart_mid_promotion_is_idempotent(ap, repo):
    pid = start(ap, CHAIN[:2])
    run_to(ap, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "verified")
    ap.close()
    p2 = Project(str(repo))                                      # a new process: only the persisted run state and ledger survive
    try:
        # simulate a crash after the candidate was prepared but before the item recorded it
        rs = SV.load_run(p2, pid)
        rs["autopromote"]["items"][0].update(status="queued", promotion=None, tests=None, numerical=None, candidate_head=None)
        SV.save_run(p2, rs)
        run_to(p2, pid, lambda r: r["state"] == "completed")
        cands = [c for c in PR.list_candidates(p2, include_closed=True) if (c.get("reason") or "").startswith("autopromote:ap:AP-001")]
        assert len(cands) == 1, "the open candidate is adopted, not duplicated"
        integ = [json.loads(l) for l in open(p2.path("promotions.jsonl")) if '"op": "integrate"' in l]
        assert len({r["promotion"] for r in integ}) == len(integ), "no candidate integrated twice"
        assert set(item_status(p2, pid)) == {"integrated"}
        before = head(repo, "main")
        SV.tick(p2)
        SV.tick(p2)
        assert head(repo, "main") == before
    finally:
        p2.close()
