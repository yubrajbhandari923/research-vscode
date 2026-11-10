"""Self-healing (supervisor_assistant.self_heal): the exact PRAVAH AP-002 failure, end to end. PROMO-002 contained an executable Quarto cell; every other gate passed; the
promotion paused and nothing repaired it. Now the debug flow's deterministic diagnosis acts: a bounded repair task fixes the SOURCE in the experiment's own worktree, the
supervisor verifies it, the candidate is rebuilt, re-tested and independently re-reviewed, and integration is retried. Real git, real supervisor ticks and scripted agents;
the independent reviewer is the stand-in test_autopromote uses. Nothing in the gates is stubbed."""
import json
import os
import subprocess

import pytest

from research import autopromote as AP, notify, planspec as PS, promotion as PR, registry, selfheal as SH, supervisor as SV
from research.chat import alerts
from research.store import Project
from conftest import git
from test_auto_approve import begin, enable, paused_or
from test_autopromote import ap, head, install_reviewer, items, out, run_to, task  # noqa: F401  (fixtures and helpers)
from test_supervisor import lproj, run_until  # noqa: F401

# AP-002: the task's own commit contains an executable Quarto cell, so the scientific-content gate fails after tests and review passed.
CELL = "echo V=1 > lib1.py && echo '```{python}' > chapter.qmd && echo 'print(1)' >> chapter.qmd && echo '```' >> chapter.qmd"
FIX = "MOCK_SH: sed -i.bak 's/^```{python}/```text/' chapter.qmd && rm -f chapter.qmd.bak"
EXP = [("e1", [task("a1", CELL, checks=["file:chapter.qmd"])])]
CASE = "promotion:AP-001"


def heal_on(p, fix=FIX, **extra):
    vals = {"supervisor_assistant.self_heal": True, **extra}
    if fix:
        vals["supervisor_assistant.repair_instructions"] = fix
    registry.apply(p, vals, reason="self-healing")


def case(p, pid):
    return ((SV.load_run(p, pid).get("selfheal") or {}).get("cases") or {}).get(CASE) or {}


def sources(p, pid):
    return items(p, pid)[0]["sources"]


# ------------------------------------------------------------------------------------------------ off by default: today's behaviour
def test_without_the_setting_the_failure_just_stops_as_before(ap, repo):
    enable(ap)
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 90)
    it = items(ap, pid)[0]
    assert "scientific content provenance" in it["error"] and not SH.enabled(ap)
    run_until(ap, lambda: False, 4)
    assert "selfheal" not in SV.load_run(ap, pid) and len(PS.load_state(ap, "ap")["tasks"]) == 1, "no repair task, no agent, nothing changed"
    assert SV.status(ap, "ap")["health"]["state"] == "halted" and not os.path.exists(os.path.join(repo, "chapter.qmd"))


# ------------------------------------------------------------------------------------------------ the AP-002 failure, healed
def test_ap002_an_executable_quarto_cell_is_diagnosed_repaired_retried_and_integrated(ap, repo, monkeypatch):
    enable(ap)
    heal_on(ap)
    monkeypatch.setattr(notify, "chat_enabled", lambda p: True)
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 300)
    it = items(ap, pid)[0]
    assert it["status"] == "integrated" and it["attempt"] == 2 and it["objections"][0]["why"], "the failed candidate stays on record; a new one was built"
    text = open(os.path.join(repo, "chapter.qmd")).read()
    assert "```text" in text and "{python}" not in text and open(os.path.join(repo, "lib1.py")).read().strip() == "V=1", "main has the repaired source"
    c = case(ap, pid)
    assert c["phase"] == "recovered" and c["history"][0]["class"] == "scientific_content" and c["task_key"] == "heal-ap-001-1" and c["attempt"] == 1
    # the repair is an ordinary supervised task: verified by the supervisor, its own attempt, its own commit
    st = PS.load_state(ap, "ap")
    tid = st["tasks"]["heal-ap-001-1"]
    rs = SV.load_run(ap, pid)
    e = rs["tasks"][tid]
    assert e["phase"] == "verified" and e["attempts"][-1]["state"] == "verified" and e["attempts"][-1]["verification"]["passed"] is True
    assert "heal check-source chapter.qmd" in json.dumps(st["spec_tasks"]["heal-ap-001-1"]["checks"]), "verified against the failing gate's own question"
    assert tid in {x["task"] for x in it["sources"]} and e["attempts"][-1].get("commit") in {x["sha"] for x in it["sources"]}, "provenance: the repair commit is attributed to the repair task"
    t = ap.get("task", tid)
    assert t.get("completed_by") == "supervisor" and tid in json.dumps([dict(r) for r in ap.q("SELECT * FROM events")]), "created and completed by the supervisor, not a person"
    # every gate really ran again on the NEW candidate: tests, an independent review, provenance, fast-forward
    cand = PR.show(ap, it["promotion"])
    g = cand["gates"]
    assert cand["state"] == "promoted" and g["tests"]["state"] == "passed" and g["review"]["state"] == "approved" and it["review"]["by"] == "reviewer"
    assert g["scientific_content"]["state"] == "passed", "the provenance gate passed on the repaired source (it was not stubbed or skipped)"
    old = [c_ for c_ in PR.list_candidates(ap, include_closed=True) if c_["id"] != it["promotion"]]
    assert old and old[0]["state"] == "abandoned", "the failed candidate was abandoned (kept on disk), never edited"
    recs = [json.loads(l) for l in open(ap.path("promotions.jsonl"))]
    for r in recs:
        if r["op"] in ("approve", "integrate"):
            assert r["author_type"] == "system" and r["mode"] == "supervisor_authorized" and r["standing"] is True, "no human authorization was impersonated"
    assert out(repo, "rev-list", "--merges", "main") == ""
    sent = [json.loads(l) for l in open(alerts.outbox_path(ap)) if '"recovery"' in l]
    assert len(sent) == 1 and "Recovered by itself" in sent[0]["text"] and "AP-001" in sent[0]["text"] and "No scientific claim was approved" in sent[0]["text"]
    assert SV.status(ap, "ap")["health"]["state"] == "completed"


def test_the_plan_is_not_halted_while_a_repair_is_in_progress(ap, repo):
    enable(ap)
    heal_on(ap)
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: case(ap, pid).get("phase") in ("repairing", "retrying"), 90)
    assert SV.status(ap, "ap")["health"]["state"] != "halted", "recovering: nothing needs you yet, so nothing interrupts you"
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 300)


# ------------------------------------------------------------------------------------------------ restarts, crash safety, idempotency
def test_restart_between_diagnosis_and_repair_resumes_without_duplicating_anything(ap, repo):
    enable(ap)
    heal_on(ap)
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: case(ap, pid).get("phase") == "repairing", 90)      # persisted BEFORE the task exists: the crash window
    st = PS.load_state(ap, "ap")
    p2 = Project(str(repo))                                                        # the daemon restarted
    try:
        rs = SV.load_run(p2, pid)
        for _ in range(3):                                                         # a step is idempotent: three of them add one task
            SH.step(p2, st, rs, SV.cfg(p2, st), {"events": []})
        SV.save_run(p2, rs)
        assert sorted(PS.load_state(p2, "ap")["tasks"]) == ["a1", "heal-ap-001-1"]
        run_to(p2, pid, lambda rs: rs["state"] == "completed", 300)
        assert case(p2, pid)["phase"] == "recovered" and items(p2, pid)[0]["status"] == "integrated"
        assert sum(1 for l in open(p2.path("promotions.jsonl")) if json.loads(l)["op"] == "integrate") == 1, "one promotion"
        assert SV.load_run(p2, pid)["selfheal"]["repairs"] == 1, "one repair, however many times it was resumed"
        SH.step(p2, PS.load_state(p2, "ap"), SV.load_run(p2, pid), {}, {"events": []})
        assert case(p2, pid)["phase"] == "recovered"
    finally:
        p2.close()


# ------------------------------------------------------------------------------------------------ failed repairs are bounded
def test_a_repair_that_never_fixes_it_is_bounded_and_then_asks_you(ap, repo):
    enable(ap)
    heal_on(ap, fix=None)                                                          # the repair agent does nothing, so its check keeps failing
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: case(ap, pid).get("phase") == "escalated", 300)
    c = case(ap, pid)
    assert c["attempt"] == 2 and [h.get("outcome") for h in c["history"]] == ["repair_failed", "repair_failed"] and "2 repair attempt(s) did not fix it" in c["reason"]
    assert sorted(PS.load_state(ap, "ap")["tasks"]) == ["a1", "heal-ap-001-1", "heal-ap-001-2"], "exactly two repair tasks, never a third"
    it = items(ap, pid)[0]
    assert it["status"] == "paused" and "self-healing stopped" in it["error"] and "scientific content provenance" in it["error"]
    run_until(ap, lambda: False, 5)
    assert len(PS.load_state(ap, "ap")["tasks"]) == 3 and not os.path.exists(os.path.join(repo, "chapter.qmd")), "stopped for good; nothing merged"
    h = SV.status(ap, "ap")["health"]
    assert h["state"] == "halted" and "self-healing stopped" in json.dumps(h), "now it interrupts, and says why"


def test_the_same_failure_after_a_verified_repair_stops_instead_of_looping(ap, repo, monkeypatch):
    enable(ap)
    heal_on(ap, fix=None)
    monkeypatch.setattr(SH, "_check_for", lambda p, dx: "true")                    # a repair whose check passes without fixing anything
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: case(ap, pid).get("phase") == "escalated", 300)
    c = case(ap, pid)
    assert "same failure came back after a repair" in c["reason"] and c["history"][0]["outcome"] == "failed_again"
    assert sorted(PS.load_state(ap, "ap")["tasks"]) == ["a1", "heal-ap-001-1"], "one repair, then it stopped: no infinite loop"
    assert not os.path.exists(os.path.join(repo, "chapter.qmd"))


def test_the_plan_wide_repair_budget_is_enforced(ap, repo):
    enable(ap)
    heal_on(ap, fix=None, **{"supervisor_assistant.max_repairs_per_plan": 1, "supervisor_assistant.max_repairs": 5})
    pid = begin(ap, EXP)
    run_to(ap, pid, lambda rs: case(ap, pid).get("phase") == "escalated", 300)
    assert "plan's repair budget (1) is used" in case(ap, pid)["reason"] and len(PS.load_state(ap, "ap")["tasks"]) == 2


# ------------------------------------------------------------------------------------------------ no gate bypass
def test_a_repair_that_weakens_a_test_is_stopped_by_the_gate_and_never_promoted(ap, repo):
    (repo / "test_core.py").write_text("def test_ok():\n    assert 1 == 1\n")
    git(repo, "add", "test_core.py")
    git(repo, "commit", "-qm", "an existing test")
    enable(ap)
    heal_on(ap, fix=FIX + " && echo 'assert True  # weakened' >> test_core.py")      # the repair agent also tampers with a frozen test
    pid = begin(ap, [("e1", [task("a1", CELL.replace("echo V=1 > lib1.py", "echo V=1 > lib1.py"), checks=["file:chapter.qmd"])])])
    main0 = head(repo, "main")
    run_to(ap, pid, lambda rs: case(ap, pid).get("phase") == "escalated", 300)
    it = items(ap, pid)[0]
    assert "may not modify or delete an existing test" in it["error"] and "self-healing stopped" in it["error"], it["error"]
    assert "protected" in case(ap, pid)["reason"] or "needs you" in case(ap, pid)["reason"] or "scientific or permission" in case(ap, pid)["reason"]
    assert head(repo, "main") == main0 and not os.path.exists(os.path.join(repo, "chapter.qmd")), "the frozen-test guard held; nothing reached main"
    assert open(repo / "test_core.py").read() == "def test_ok():\n    assert 1 == 1\n"


@pytest.mark.parametrize("error", [
    "test_core.py: promoted work may not modify or delete an existing test (M); a human must review that change",
    "numerical checks failed: oracle drifted",
    "ledger files are never promoted",
    "big.bin: 9 MB exceeds auto_promotion.max_file_mb; datasets and results stay out of main",
    "2 commit(s) on EXP-001's branch cannot be tied to one verified task",
    "verification policy changed outside Settings",
    "no independent review arrived in time",
])
def test_protected_scientific_and_permission_failures_are_never_repaired(ap, error):
    item = {"id": "AP-001", "experiment": "EXP-001", "status": "paused", "error": error, "promotion": None}
    dx = SH.diagnose_promotion(ap, {}, {}, item)
    assert dx["recoverable"] is False and dx["class"] in ("needs_you",), "these ask you: they are decisions or protected, not engineering faults"


# ------------------------------------------------------------------------------------------------ the debug flow
def test_the_automatic_diagnosis_follows_the_setting_and_task_retries_stay_within_budget(lproj):
    from test_intervention import WRONG, blocked, start, ticks
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    tid = SV.status(lproj, "fx")["tasks"][0]["id"]
    assert not notify._auto_investigator(lproj) and not SH.retry_allowed(lproj, tid), "off: today's behaviour"
    registry.apply(lproj, {"supervisor_assistant.self_heal": True})
    assert notify._auto_investigator(lproj), "turning self-healing on turns the automatic diagnosis on"
    assert SH.retry_allowed(lproj, tid) is False, "the task's attempt budget is exhausted: that asks you"
