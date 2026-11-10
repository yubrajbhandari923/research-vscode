"""Automatic promotion must carry every verified commit of an experiment, whoever made it, and nothing unverified.

The failure (PRAVAH, EXP-001): T-001 and T-002 were committed by the supervisor and recorded on their attempts; T-003 and T-004 were committed by the
AGENT before verification, so the tree was clean, the supervisor made no commit, the attempt recorded none, and `pending()` selected only two commits.
The candidate had no tests, the integration test command found none, and the promotion paused. Real git, real supervisor ticks, scripted agents."""
import json
import os
import subprocess

import pytest

from conftest import git
from research import autopromote as AP, commitprov as CP, intervene as I, notify, planspec as PS, supervisor as SV, workspace as W
from research.chat import alerts
from research.util import ResearchError
from test_autopromote import ap, head, install_reviewer, item_status, items, out, plan, run_to, start, task  # noqa: F401  (fixtures and helpers)
from test_supervisor import run_until

COMMIT = "GIT_AUTHOR_NAME=Agent GIT_COMMITTER_NAME=Agent git commit -qm"
# T-001, T-002: the supervisor commits their files. T-003, T-004: the agent commits its own work first, so there is nothing left for the supervisor to commit.
FOUR = [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"]),
                task("a2", "echo V=2 > lib2.py", deps=["a1"], checks=["file:lib2.py"]),
                task("a3", f"mkdir -p tests && echo ok > tests/test_cfg.py && git add -A && {COMMIT} cfg-tests", deps=["a2"], checks=["file:tests/test_cfg.py"]),
                task("a4", f"echo V=4 > lib4.py && git add -A && {COMMIT} lib4", deps=["a3"], checks=["file:lib4.py"])])]


@pytest.fixture
def tests_required(ap):
    """The promotion's integration test needs the test file that only the agent-committed T-003 adds (what pytest's exit code 5 stood for)."""
    ap.config["auto_promotion"].pop("test_command", None)
    ap.config["promotion"]["test_command"] = "ls tests/test_cfg.py"
    ap.save_config()
    return ap


def attempts(p, pid):
    return {t: [dict(a) for a in e["attempts"]] for t, e in SV.load_run(p, pid)["tasks"].items()}


def branch(p):
    ws = W.list_workspaces(p)[0]
    return ws, out(ws["path"], "log", "--reverse", "--no-merges", "--format=%H|%an|%s", f"{ws['base_rev']}..HEAD").splitlines()


def forget_provenance(p, pid):
    """What a ledger written by 3.5.2 looks like: no attributions, and no branch heads on the attempts."""
    rs = SV.load_run(p, pid)
    rs.pop("commit_provenance", None)
    for e in rs["tasks"].values():
        for a in e["attempts"]:
            a.pop("head_before", None)
            a.pop("head_after", None)
    SV.save_run(p, rs)


# ------------------------------------------------------------------------------------------------ the reproduced failure, fixed
def test_agent_and_supervisor_commits_are_all_promoted_in_branch_order(tests_required, repo):
    pid = start(tests_required, FOUR)
    run_to(tests_required, pid, lambda rs: rs["state"] == "completed", 150)
    rs = SV.load_run(tests_required, pid)
    by = {k: rs["tasks"][t]["attempts"][-1] for k, t in PS.load_state(tests_required, "ap")["tasks"].items()}
    assert by["a1"].get("commit") and by["a2"].get("commit") and not by["a3"].get("commit") and not by["a4"].get("commit"), "the root cause: agent-committed work has no attempt.commit"
    _, log = branch(tests_required)
    assert [l.split("|")[1] for l in log] == ["Research OS supervisor", "Research OS supervisor", "Agent", "Agent"], "mixed supervisor-made and agent-made commits"
    it = items(tests_required, pid)[0]
    assert it["status"] == "integrated" and [x["sha"] for x in it["sources"]] == [l.split("|")[0] for l in log], "all four, in branch (cherry-pick) order"
    assert {x["basis"] for x in it["sources"]} == {"recorded", "head_range"}
    assert os.path.exists(os.path.join(repo, "tests", "test_cfg.py")) and os.path.exists(os.path.join(repo, "lib4.py"))
    assert out(repo, "rev-list", "--merges", "main") == "", "fast-forward only, linear"
    prov = SV.load_run(tests_required, pid)["commit_provenance"]["attributed"]
    assert len(prov) == 2 and all(v["basis"] == "head_range" for v in prov.values()), "the agent's commits are attributed with their evidence; no attempt was edited"
    assert all(a.get("head_before") and a.get("head_after") for a in by.values())


def test_a_candidate_that_omits_agent_commits_is_never_prepared(tests_required, repo):
    """Before the fix the candidate silently held two commits. Now the coverage of the branch is part of preparing it."""
    pid = start(tests_required, FOUR, authorize=False)
    run_to(tests_required, pid, lambda rs: rs["state"] == "completed", 150)
    st, rs = PS.load_state(tests_required, "ap"), SV.load_run(tests_required, pid)
    pend = AP.pending(tests_required, st, rs)
    (exp, ws), = pend
    assert len(pend[(exp, ws)]) == 4, "pending() now lists the agent's two commits too"


# ------------------------------------------------------------------------------------------------ an already-paused AP-001
def test_an_older_ledger_is_attributed_from_attempt_windows_and_recovered_by_retry(tests_required, repo, monkeypatch):
    pid = start(tests_required, FOUR, authorize=False)
    run_to(tests_required, pid, lambda rs: rs["state"] == "completed", 150)
    forget_provenance(tests_required, pid)                                  # the ledger an older version left
    before, runs_before = attempts(tests_required, pid), [r["id"] for r in tests_required.q("SELECT id FROM runs")]
    st, rs = PS.load_state(tests_required, "ap"), SV.load_run(tests_required, pid)
    assert sum(len(v) for v in AP.pending(tests_required, st, rs).values()) == 2, "an older ledger still shows only the supervisor's commits"
    cov = SV.autopromote(tests_required, "ap", "coverage")["coverage"][0]
    assert [x["basis"] for x in cov["included"]] == ["recorded", "recorded", "attempt_window", "attempt_window"] and not cov["orphans"], "read-only: nothing is written"
    assert "commit_provenance" not in SV.load_run(tests_required, pid)
    # the older supervisor: it prepared only what was recorded, so the promotion tests found no tests and AP-001 paused
    real = CP.reconcile
    monkeypatch.setattr(CP, "reconcile", lambda *a, **k: {"blocking": False, "orphans": [], "ambiguous": [], "included": [], "in_progress": [], "excluded": []})
    install_reviewer(monkeypatch, tests_required)
    SV.autopromote(tests_required, "ap", "authorize", note="overnight")
    run_to(tests_required, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused", 90)
    it = items(tests_required, pid)[0]
    assert len(it["sources"]) == 2 and "integration tests failed" in it["error"], "the candidate held only the supervisor's two commits and its tests found no test file"
    # install the fixed version: the next tick records the attributions and says the paused candidate omits them; it does not retry on its own
    monkeypatch.setattr(CP, "reconcile", real)
    SV.tick(tests_required)
    SV.tick(tests_required)
    rs = SV.load_run(tests_required, pid)
    it = items(tests_required, pid)[0]
    assert it["status"] == "paused" and len(it["omits"]) == 2 and len(rs["commit_provenance"]["attributed"]) == 2
    from research_cli.commands import supervisor as CLS
    assert "omits 2 verified commit" in "\n".join(CLS._status_lines(SV.status(tests_required, "ap")))
    # recovery: retry prepares a NEW candidate from the same verified work; nothing re-runs, nothing is relaxed, no duplicate promotion
    SV.autopromote(tests_required, "ap", "retry", item="AP-001")
    run_to(tests_required, pid, lambda rs: rs["autopromote"]["items"][0]["status"] == "integrated", 90)
    it = items(tests_required, pid)[0]
    assert len(it["sources"]) == 4 and it["objections"][0]["why"] and os.path.exists(os.path.join(repo, "tests", "test_cfg.py"))
    assert attempts(tests_required, pid) == before, "no task restarted, no attempt added, no commit rewritten"
    assert [r["id"] for r in tests_required.q("SELECT id FROM runs")] == runs_before
    run_until(tests_required, lambda: False, 3)
    assert len(items(tests_required, pid)) == 1 and item_status(tests_required, pid) == ["integrated"], "restarts and further ticks create nothing"
    promos = [json.loads(l) for l in open(tests_required.path("promotions.jsonl"))]
    assert len({r["promotion"] for r in promos if r["op"] == "prepare"}) == 2 and sum(r["op"] == "integrate" for r in promos) == 1


# ------------------------------------------------------------------------------------------------ nothing unverified, nothing silently omitted
def test_a_commit_nobody_can_vouch_for_stops_the_candidate_and_is_never_promoted(tests_required, repo, monkeypatch):
    pid = start(tests_required, FOUR, authorize=False)
    run_to(tests_required, pid, lambda rs: rs["state"] == "completed", 150)
    ws, _ = branch(tests_required)
    open(os.path.join(ws["path"], "notes.txt"), "w").write("a person's scratch work\n")
    env = {**os.environ, "GIT_COMMITTER_DATE": "2001-01-01T00:00:00", "GIT_AUTHOR_DATE": "2001-01-01T00:00:00", "GIT_AUTHOR_NAME": "Human", "GIT_COMMITTER_NAME": "Human"}
    subprocess.run(["git", "add", "notes.txt"], cwd=ws["path"], check=True)
    subprocess.run(["git", "commit", "-qm", "scratch"], cwd=ws["path"], check=True, env=env)
    orphan = out(ws["path"], "rev-parse", "HEAD")
    cov = SV.autopromote(tests_required, "ap", "coverage")["coverage"][0]
    assert [x["sha"] for x in cov["orphans"]] == [orphan] and cov["orphans"][0]["author"] == "Human"
    main0 = head(repo, "main")
    install_reviewer(monkeypatch, tests_required)
    SV.autopromote(tests_required, "ap", "authorize", note="reviewed")
    run_to(tests_required, pid, lambda rs: rs["autopromote"]["items"] and rs["autopromote"]["items"][0]["status"] == "paused", 90)
    it = items(tests_required, pid)[0]
    assert orphan[:10] in it["error"] and "cannot be tied to one verified task" in it["error"] and "exclude --commit" in it["error"]
    assert it["promotion"] is None and head(repo, "main") == main0, "no candidate was prepared and nothing merged"
    assert SV.status(tests_required, "ap")["health"]["state"] == "halted"
    with pytest.raises(ResearchError, match="not verified|must name a task|not a commit"):
        SV.autopromote(tests_required, "ap", "attribute", commit=orphan, task="T-999")
    SV.autopromote(tests_required, "ap", "exclude", commit=orphan, note="scratch, not part of any task")
    SV.autopromote(tests_required, "ap", "retry", item="AP-001")
    run_to(tests_required, pid, lambda rs: rs["autopromote"]["items"][0]["status"] == "integrated", 90)
    assert orphan not in [x["sha"] for x in items(tests_required, pid)[0]["sources"]] and not os.path.exists(os.path.join(repo, "notes.txt"))
    assert os.path.exists(os.path.join(repo, "tests", "test_cfg.py"))
    assert SV.load_run(tests_required, pid)["commit_provenance"]["decisions"][orphan]["action"] == "exclude"


def test_two_tasks_claiming_one_commit_is_ambiguous_not_guessed(tests_required, repo, monkeypatch):
    pid = start(tests_required, FOUR, authorize=False)
    run_to(tests_required, pid, lambda rs: rs["state"] == "completed", 150)
    forget_provenance(tests_required, pid)
    rs = SV.load_run(tests_required, pid)
    t3, t4 = [t for t, _ in sorted(rs["tasks"].items())][2:4]
    a3, a4 = rs["tasks"][t3]["attempts"][-1], rs["tasks"][t4]["attempts"][-1]
    a4["started"], a4["ended"] = a3["started"], a4["ended"]                 # parallel agents in one worktree: T-004's window now contains T-003's commit
    SV.save_run(tests_required, rs)
    cov = SV.autopromote(tests_required, "ap", "coverage")["coverage"][0]
    amb = [x for x in cov["ambiguous"]]
    assert amb and set(amb[0]["tasks"]) == {t3, t4} and all(x["sha"] != amb[0]["sha"] for x in cov["included"])
    # a human settles it, naming the task; the decision is recorded with who and why
    SV.autopromote(tests_required, "ap", "attribute", commit=amb[0]["sha"], task=t3, note="T-003 wrote the tests")
    d = SV.load_run(tests_required, pid)["commit_provenance"]["decisions"][amb[0]["sha"]]
    assert d["action"] == "attribute" and d["task"] == t3 and d["note"] and d["by"]


def test_work_still_in_progress_is_left_out_without_blocking(tests_required, repo):
    pid = start(tests_required, FOUR, authorize=False)
    run_to(tests_required, pid, lambda rs: rs["state"] == "completed", 150)
    rs = SV.load_run(tests_required, pid)
    t4 = sorted(rs["tasks"])[3]
    rs["tasks"][t4]["attempts"][-1]["state"] = "launched"                  # T-004's attempt has not verified (yet)
    SV.save_run(tests_required, rs)
    cov = SV.autopromote(tests_required, "ap", "coverage")["coverage"][0]
    assert [x["task"] for x in cov["in_progress"]] == [t4] and not cov["orphans"] and not cov["ambiguous"]


# ------------------------------------------------------------------------------------------------ the Discord "Diagnose" button
def paused_promotion(p, pid):
    rs = SV.load_run(p, pid)
    a = rs["autopromote"]
    a["authorized"] = a["authorized"] or {"by": "me", "fingerprint": AP.fingerprint(p)}
    a["items"] = [{"id": "AP-001", "experiment": "EXP-001", "workspace": "WS-001", "status": "paused", "error": "integration tests failed: no tests ran in 0.01s",
                   "history": [], "sources": [], "objections": [{"attempt": 1, "why": "earlier"}]}]
    SV.save_run(p, rs)


def test_a_halt_caused_by_a_paused_promotion_can_be_diagnosed_without_an_agent(ap, monkeypatch):
    pid = start(ap, FOUR[:1][:1] and [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])], authorize=False)
    run_to(ap, pid, lambda rs: rs["state"] == "completed")
    paused_promotion(ap, pid)
    monkeypatch.setattr(I, "run_assistant", lambda *a, **k: pytest.fail("a promotion is diagnosed from its record, not by an agent"))
    st, rs = PS.load_state(ap, "ap"), SV.load_run(ap, pid)
    h = {"blocking": [{"task": "AP-001", "key": "AP-001", "title": "promotion of EXP-001", "phase": "blocked", "reason": "no tests ran", "promotion": True}], "fingerprint": "f"}
    rec = notify.run_debug(ap, "ap", h)
    assert rec["state"] == "done" and "integration tests failed" in rec["summary"] and "retry --item AP-001" in rec["summary"] and "re-run" in rec["summary"]
    assert "no tests ran" in notify.message_for(rec, {**h, "plan": "ap"}) and "Tasks are not re-run" in notify.message_for(rec, {**h, "plan": "ap"})


def test_the_answer_to_diagnose_is_delivered_even_while_the_halt_card_is_in_its_cooldown(ap):
    first = alerts.raise_(ap, "halt:ap", "halt", "error", "Plan ap is HALTED")
    assert first and alerts.raise_(ap, "halt:ap", "halt", "error", "diagnosis") is None, "a repeat inside the cooldown is held back"
    got = alerts.raise_(ap, "halt:ap", "halt", "error", "diagnosis", force=True)
    assert got and got["text"] == "diagnosis", "a requested diagnosis is not"


def test_a_diagnosis_that_crashes_is_reported_not_swallowed(ap, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "run_debug", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("agent binary missing")))
    monkeypatch.setattr(notify, "chat_enabled", lambda p: True)
    monkeypatch.setattr(notify, "_alert_halt", lambda p, plan, fp, text, rec, force=False: sent.append((text, force)))
    notify._debug_and_alert(ap.root, "ap", {"blocking": [{"task": "T-001", "title": "t", "phase": "blocked", "reason": "r"}], "fingerprint": "f"})
    assert sent and "debug agent failed" in sent[0][0] and "agent binary missing" in sent[0][0] and sent[0][1] is True
