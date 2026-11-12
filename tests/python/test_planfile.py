"""The plan file is the plan: edit it and the supervisor follows; budgets raised anywhere release what they blocked; nothing is half-applied."""
import os
import textwrap

import pytest

from research import archive as AR, board as BD, planfile as PF, planspec as PS, supervisor as SV
from research.util import ResearchError
from test_supervisor import MOCK, phases, proj, run_until, task_yaml, yaml_plan  # noqa: F401  (fixture and helpers)

pytestmark = pytest.mark.slow


def plan_file(p, tasks, extra_exec=""):
    path = os.path.join(p.root, "plans", "demo-plan.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(yaml_plan(tasks, extra_exec).replace("scope: partial\n", ""))
    return path


def edit(path, old, new):
    text = open(path).read()
    assert old in text, text
    open(path, "w").write(text.replace(old, new))


FLAKY = task_yaml("build", "Build it", "echo good > r.txt", checks=["cmd:grep -q good r.txt"], attempt="3")      # only its third attempt produces a passing result


def start(p, tasks, extra_exec=""):
    path = plan_file(p, tasks, extra_exec)
    r = PF.add(p, path)
    assert r["action"] == "created" and r["file"]["status"] == "in_sync"
    SV.plan_run(p, "demo-plan", start_daemon=False)
    return path


def blocked_on_budget(p):
    t = SV.status(p, "demo-plan")["tasks"][0]
    return t["phase"] == "blocked" and t["block_kind"] == "task_budget"


def test_editing_the_attempt_budget_in_the_file_is_all_it_takes(proj):
    p = proj
    path = start(p, [FLAKY])
    assert run_until(p, lambda: blocked_on_budget(p))
    t = SV.status(p, "demo-plan")["tasks"][0]
    assert t["attempts"] == 2 and "research bump " + t["id"] in t["reason"], "the stop itself says how to continue"
    edit(path, "max_attempts_per_task: 2", "max_attempts_per_task: 4")
    assert PF.info(p, "demo-plan")["status"] == "edited"
    assert run_until(p, lambda: phases(p)["build"] == "verified"), "no export, no apply, no resolve: saving the file was enough"
    assert PF.info(p, "demo-plan")["status"] == "in_sync"
    assert open(path).read().count("max_attempts_per_task: 4") == 1, "a file that applied cleanly is left exactly as written"


def test_bump_allows_more_attempts_and_retries_now(proj):
    p = proj
    path = start(p, [FLAKY])
    assert run_until(p, lambda: blocked_on_budget(p))
    tid = SV.status(p, "demo-plan")["tasks"][0]["id"]
    r = PF.bump(p, tid)
    assert (r["before"], r["after"], r["used"], r["released"]) == (2, 5, 2, [tid])
    assert "max_attempts: 5" in open(path).read(), "the file never lies about the plan"
    assert PF.info(p, "demo-plan")["status"] == "in_sync"
    assert run_until(p, lambda: phases(p)["build"] == "verified")
    with pytest.raises(ResearchError, match="already used"):
        PF.bump(p, tid, attempts=1)


def test_bump_widens_the_plan_budget_when_that_is_what_stops_it(proj):
    p = proj
    start(p, [FLAKY], extra_exec=", max_total_attempts: 2")
    assert run_until(p, lambda: blocked_on_budget(p))
    tid = SV.status(p, "demo-plan")["tasks"][0]["id"]
    r = PF.bump(p, tid)
    assert r["plan_budget"] == {"spent": 2, "before": 2, "after": 5}
    assert run_until(p, lambda: phases(p)["build"] == "verified")


def test_plan_set_changes_values_without_an_editor(proj):
    p = proj
    path = start(p, [FLAKY])
    assert run_until(p, lambda: blocked_on_budget(p))
    r = PF.set_values(p, "demo-plan", ["build.max_attempts=6", "execution.max_parallel_agents=2", "build.title=Build it properly"])
    assert r["operations"] >= 1
    text = open(path).read()
    assert "max_attempts: 6" in text and "max_parallel_agents: 2" in text
    assert SV.release_budget_blocks(p, "demo-plan")
    with pytest.raises(ResearchError, match="not a task or experiment"):
        PF.set_values(p, "demo-plan", ["nope.max_attempts=3"])
    with pytest.raises(ResearchError):
        PF.set_values(p, "demo-plan", ["build.agent.role=wizard"])
    assert "wizard" not in open(path).read(), "an invalid value changes neither the plan nor the file"


def test_a_new_task_in_the_file_waits_for_one_sync_and_an_invalid_file_changes_nothing(proj):
    p = proj
    path = start(p, [task_yaml("build", "Build it", "echo hi > out.txt", checks=["file:out.txt"])])
    more = textwrap.indent(task_yaml("extra", "A second task", "echo hi > two.txt", deps=["build"], checks=["file:two.txt"]), " " * 8)
    open(path, "a").write(more)
    SV.tick(p)
    i = PF.info(p, "demo-plan")
    assert i["status"] == "edited" and any("task extra" in h for h in i["held"]), "a new task is a scientific change: held, with the reason"
    assert "extra" not in phases(p)
    assert [n["next"][0]["cmd"] for n in BD.plan(p, "demo-plan")["needs_you"]] == ["research plan sync demo-plan"]
    r = PF.sync(p, "demo-plan")[0]
    assert r["action"] == "applied" and "extra" in phases(p)
    p.save_config({"supervisor.plan_files.auto_sync": "all"})
    edit(path, "depends_on: [build]", "depends_on: [nowhere]")
    rev = PS.load_state(p, "demo-plan")["revision"]
    res = PF.step(p)
    i = PF.info(p, "demo-plan")
    assert i["problem"], res
    assert PS.load_state(p, "demo-plan")["revision"] == rev, "an invalid file is reported and nothing is applied"
    assert run_until(p, lambda: phases(p)["build"] == "verified"), "the plan keeps running on its last good version"
    with pytest.raises(ResearchError):
        PF.sync(p, "demo-plan")
    edit(path, "depends_on: [nowhere]", "depends_on: [build]")
    assert run_until(p, lambda: phases(p).get("extra") == "verified")
    assert not PF.info(p, "demo-plan")["problem"]


def test_an_edit_made_while_the_supervisor_changed_the_plan_is_merged(proj):
    p = proj
    path = start(p, [FLAKY, task_yaml("later", "Later", "echo hi > l.txt", deps=["build"], checks=["file:l.txt"])])
    PF.set_values(p, "demo-plan", ["later.max_attempts=7"])              # the ledger moves (as a repair task or an intervention would)
    text = open(path).read()
    open(path, "w").write(text.replace("max_attempts: 7", "max_attempts: 3").replace("max_attempts_per_task: 2", "max_attempts_per_task: 5"))
    st = PS.load_state(p, "demo-plan")
    st["file"]["revision"] -= 1          # the race: the ledger changed again after the person opened the file
    PS.save_state(p, st)
    assert PF.info(p, "demo-plan")["status"] == "merge"
    r = PF.sync(p, "demo-plan")[0]
    assert r["action"] == "merged"
    out = open(path).read()
    assert "max_attempts_per_task: 5" in out and "max_attempts: 3" in out and PF.info(p, "demo-plan")["status"] == "in_sync"
    assert set(phases(p)) == {"build", "later"}, "a merge never retires anything"


def test_the_file_follows_when_the_plan_changes_elsewhere_and_a_deleted_file_deletes_nothing(proj):
    p = proj
    path = start(p, [FLAKY])
    os.unlink(path)
    SV.tick(p)
    assert phases(p) == {"build": "running"} or "build" in phases(p)
    assert PF.sync(p, "demo-plan")[0]["action"] == "written" and os.path.exists(path)


def test_wall_time_pause_lifts_when_the_budget_is_raised(proj):
    p = proj
    start(p, [task_yaml("build", "Build it", "sleep 1; echo hi > out.txt", checks=["file:out.txt"])], extra_exec=", max_wall_minutes: 1")
    SV.tick(p)
    rs = SV.load_run(p, PS.load_state(p, "demo-plan")["plan_id"])
    rs["budgets"]["wall_seconds"] = 3600.0
    SV.save_run(p, rs)
    SV.tick(p)
    assert SV.status(p, "demo-plan")["state"] == "paused"
    PF.set_values(p, "demo-plan", ["execution.max_wall_minutes=600"])
    assert run_until(p, lambda: phases(p)["build"] == "verified")
    SV.plan_pause(p, "demo-plan") if SV.status(p, "demo-plan")["state"] == "running" else None


def test_archive_hides_without_deleting_and_refuses_a_running_plan(proj):
    p = proj
    start(p, [task_yaml("build", "Build it", "echo hi > out.txt", checks=["file:out.txt"])])
    st = PS.load_state(p, "demo-plan")
    with pytest.raises(ResearchError, match="archiving never stops work"):
        AR.archive(p, "demo-plan")
    assert run_until(p, lambda: SV.status(p, "demo-plan")["state"] == "completed")
    r = AR.archive(p, "demo-plan", "superseded")
    exp = list(st["experiments"].values())
    assert r["also"] == exp
    assert BD.data(p)["plans"] == [] and BD.data(p)["archived_hidden"] == 1
    assert [x["plan"] for x in BD.data(p, include_archived=True)["plans"]] == ["demo-plan"]
    assert AR.visible(p, p.list("experiment")) == [] and len(AR.visible(p, p.list("experiment"), include=True)) == 1
    assert p.exists("experiment", exp[0]) and p.exists("plan", st["plan_id"]), "nothing is deleted"
    assert PF.sync(p) == [], "an archived plan's file is no longer followed"
    back = AR.unarchive(p, st["plan_id"])
    assert back["also"] == exp and len(BD.data(p)["plans"]) == 1 and not AR.listing(p)


def test_board_names_the_next_command_for_everything_that_waits(proj):
    p = proj
    start(p, [FLAKY, task_yaml("later", "Later", "echo hi > l.txt", deps=["build"], checks=["file:l.txt"])])
    assert run_until(p, lambda: blocked_on_budget(p))
    b = BD.data(p)
    pl = b["plans"][0]
    build = pl["experiments"][0]["tasks"][0]
    assert build["mark"] == "you" and build["next"][0]["cmd"] == f"research bump {build['id']}"
    assert pl["experiments"][0]["tasks"][1]["mark"] == "wait"
    assert [n["ref"] for n in b["needs_you"]] == [build["id"]] and pl["file"]["status"] == "in_sync"


# ----------------------------------------------------------------------------------------------- one agent session per session group
def _attempt(**kw):
    return {"n": 1, "session_id": "S-1", "session_group": "main", "role": "implementer", "profile": "fake", "cwd": "/w/exp1", "verification": {"passed": True},
            "ended": "2026-01-01T00:00:00+00:00", "execution": "x1", **kw}


def _group(monkeypatch, rs, spec=None, d=None, role="implementer", cap=4, supported=True):
    class PV:
        provider_of = staticmethod(lambda prof, name: "claude")
        native_resume_supported = staticmethod(lambda kind, cmd: supported)
    monkeypatch.setattr(SV, "_providers", lambda: PV)
    attempt = {}
    sid = SV._group_session(None, rs, "T-2", spec or {"agent": {"session_group": "main"}}, role, d or {"argv": ["claude"], "profile": "fake", "cwd": "/w/exp1"}, attempt, cap)
    return sid, attempt


def test_tasks_of_one_session_group_continue_one_agent_session(monkeypatch):
    rs = {"tasks": {"T-1": {"phase": "verified", "attempts": [_attempt()]}, "T-2": {"phase": "waiting", "attempts": []}}}
    sid, a = _group(monkeypatch, rs)
    assert sid == "S-1" and a == {"session_chain": 2, "session_from": "x1"}


@pytest.mark.parametrize("why,change", [
    ("no group declared", {"spec": {"agent": {}}}),
    ("a verifier never inherits the implementer's context", {"role": "verifier"}),
    ("another profile is another model", {"d": {"argv": ["claude"], "profile": "other", "cwd": "/w/exp1"}}),
    ("another worktree is another provider session store", {"d": {"argv": ["claude"], "profile": "fake", "cwd": "/w/exp2"}}),
    ("the installed CLI cannot resume", {"supported": False}),
    ("switched off", {"cap": 0}),
])
def test_a_session_is_not_shared_when_it_would_not_be_the_same_conversation(monkeypatch, why, change):
    rs = {"tasks": {"T-1": {"phase": "verified", "attempts": [_attempt()]}}}
    assert _group(monkeypatch, rs, **change)[0] is None, why


def test_a_session_is_shared_only_from_verified_work_and_only_so_many_times(monkeypatch):
    assert _group(monkeypatch, {"tasks": {"T-1": {"phase": "blocked", "attempts": [_attempt(verification={"passed": False})]}}})[0] is None
    assert _group(monkeypatch, {"tasks": {"T-1": {"phase": "verified", "attempts": [_attempt(voided=True)]}}})[0] is None
    assert _group(monkeypatch, {"tasks": {"T-1": {"phase": "verified", "attempts": [_attempt(session_chain=4)]}}})[0] is None, "the fifth task starts a fresh session"
    rs = {"tasks": {"T-0": {"phase": "verified", "attempts": [_attempt(session_id="S-0", ended="2025-12-31T00:00:00+00:00")]},
                    "T-1": {"phase": "verified", "attempts": [_attempt(session_id="S-1", session_chain=2)]}}}
    sid, a = _group(monkeypatch, rs)
    assert sid == "S-1" and a["session_chain"] == 3, "the most recent session of the group is the one continued"


# ----------------------------------------------------------------------------------------------- the reviewer is observable
def test_a_reviewer_that_exits_without_a_verdict_is_noticed_at_once_and_replaced_once(monkeypatch):
    from research import autopromote as AP, execution as E, promotion as PR
    import time as _t
    launched = []
    monkeypatch.setattr(PR, "show", lambda p, pid: {"gates": {"head": "h1", "review": {"state": "none"}}})
    monkeypatch.setattr(AP, "_launch_reviewer", lambda p, item, pol: launched.append(1) or {"at": "now", "epoch": _t.time() - 60, "profile": "rev", "execution": f"e{len(launched)}"})
    state = {"state": "running", "alive": True}
    monkeypatch.setattr(E, "show", lambda p, ident: dict(state, exit_code=1))
    item = {"id": "AP-001", "promotion": "PROMO-1", "candidate_head": "h1", "status": "verified", "history": [], "experiment": "EXP-001"}
    pol = {"review_timeout_minutes": 120, "reviewer_profile": "rev", "require_distinct_reviewer": True}
    out = {"events": [], "blocked": []}
    AP._review(None, item, pol, out)
    assert item["status"] == "awaiting_review" and len(launched) == 1
    AP._review(None, item, pol, out)
    assert len(launched) == 1 and AP.reviewer_status(None, item)["state"] == "running", "a live reviewer is left alone"
    state.update(state="failed", alive=False)
    AP._review(None, item, pol, out)
    assert len(launched) == 2 and item["review_attempts"] == 2 and "second reviewer" in out["events"][0], "not after review_timeout_minutes: now"
    AP._review(None, item, pol, out)
    assert item["status"] == "paused" and "exited twice without recording a verdict" in item["error"] and "research logs AP-001" in item["error"]
