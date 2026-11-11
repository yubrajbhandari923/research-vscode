"""Ordinary task failures recover by themselves (PRAVAH PLAN-002 T-023): a task that did not write a file it owns is an incomplete deliverable, not `environment`.
The agent said `environment`; the verifier's own output said a file was missing. Real supervisor ticks, real worktrees, scripted agents. Each test pins one of the five
situations the classifier must tell apart: own output, upstream output, delayed evidence, a genuine permission/environment error, and a verifier asked to author."""
import sys

import pytest

from research import planspec as PS, registry, runs as R, selfheal as SH, supervisor as SV
from research.hpc import remote as HR
from test_supervisor import lproj, phase, plan, run_attempts, run_until, t  # noqa: F401  (fixtures and helpers of the supervisor suite)

CLI = f"{sys.executable} -m research_cli"
SAY_ENV = f"{CLI} agent problem $RESEARCH_TASK --category environment -m 'the sandbox has no write access to docs/'"


def go(p, exps, until, timeout=90, **kw):
    PS.apply(p, plan(exps, **kw))
    SV.plan_run(p, "fx", start_daemon=False)
    ok = run_until(p, until, timeout)
    run_until(p, lambda: False, 3)
    return ok


def cats(p, key):
    return [a.get("category") for a in run_attempts(p)[key] if a.get("state") == "failed"]


# ------------------------------------------------------------------------------------------------ T-023: its own file, reported as `environment`
def test_t023_a_missing_own_file_reported_as_environment_is_repaired_with_the_original_diagnostics(lproj):
    # attempt 1 claims an environment problem and writes nothing; attempt 2 writes the file
    shell = f"MOCK_SH@1: {SAY_ENV}\\nMOCK_SH@2: mkdir -p docs && echo ok > docs/f.md"
    x = t("t23", "analyst", "true", checks=["file:docs/f.md"], acceptance="docs/f.md").replace('MOCK_SH: true', shell)
    assert go(lproj, [("e1", [x])], lambda: phase(lproj, "t23")["phase"] == "verified", 150)
    a1, a2 = run_attempts(lproj)["t23"][:2]
    assert a1["category"] == "incomplete_deliverable" and a1["category_override"]["reported"] == "environment", "the agent's word is overridden and the reason is recorded"
    assert "no environment error" in a1["category_override"]["why"] and a2["kind"] == "repair" and a2["verification"]["passed"] is True
    assert "docs/f.md" in a1["verification"]["diagnostics"][0], "the repair carries the exact missing path"
    assert len([a for a in run_attempts(lproj)["t23"] if a["state"] == "verified"]) == 1, "nothing verified was rerun"


def test_the_repair_attempt_is_told_exactly_which_files_are_missing(lproj):
    from research import taskprompt
    PS.apply(lproj, plan([("e1", [t("w", "analyst", "true", checks=["file:docs/f.md"])])]))
    SV.plan_run(lproj, "fx", start_daemon=False)
    st = PS.load_state(lproj, "fx")
    text, _ = taskprompt.assemble(lproj, st["tasks"]["w"], st, {"attempts": []}, "repair", ["check file: docs/f.md failed (missing)"])
    assert "still missing" in text and "`docs/f.md`" in text


def test_an_identical_failure_after_the_repair_stops_instead_of_looping(lproj):
    x = t("w", "analyst", "true", checks=["file:docs/f.md"], attempts=5)
    assert go(lproj, [("e1", [x])], lambda: phase(lproj, "w")["phase"] == "blocked", 120)
    at = run_attempts(lproj)["w"]
    assert len(at) == 2, "one repair, then it stops although the budget allowed five"
    assert "stopping instead of repeating" in phase(lproj, "w")["reason"] and "docs/f.md" in phase(lproj, "w")["reason"]


# ------------------------------------------------------------------------------------------------ what must NOT be called an incomplete deliverable
def test_an_upstream_file_is_still_a_missing_prerequisite_whatever_the_agent_says(lproj):
    exps = [("e1", [t("a1", "implementer", "echo a > docs-a.md", checks=["file:docs-a.md"], acceptance="docs-a.md")]),
            ("e2", [t("v1", "analyst", SAY_ENV, deps=["a1"], checks=["file:docs-a.md"])])]
    assert go(lproj, exps, lambda: phase(lproj, "v1")["phase"] == "blocked", 120)
    at = run_attempts(lproj)["v1"]
    assert len(at) == 1 and at[0]["category"] == "missing_prerequisite" and at[0]["category_override"]["reported"] == "environment"


def test_a_verifier_asked_to_author_a_file_is_a_role_contradiction_whatever_the_agent_says(lproj):
    assert go(lproj, [("e1", [t("rep", "verifier", SAY_ENV, checks=["file:docs-gate.md"], acceptance="docs-gate.md")])], lambda: phase(lproj, "rep")["phase"] == "blocked")
    at = run_attempts(lproj)["rep"]
    assert len(at) == 1 and at[0]["category"] == "role_contradiction" and at[0]["category_override"]["reported"] == "environment"


def test_a_genuine_environment_error_keeps_the_agents_category(lproj):
    x = t("w", "analyst", f"{CLI} agent problem $RESEARCH_TASK --category environment -m x; echo 'ModuleNotFoundError: numpy' >&2; false", checks=["file:docs/f.md"])
    # the verifier's diagnostics have no environment text here, so the failure that matters is the process exit: an agent that crashed on a missing module stays `environment`
    assert go(lproj, [("e1", [x])], lambda: phase(lproj, "w")["phase"] == "blocked")
    at = run_attempts(lproj)["w"]
    assert len(at) == 1 and at[0]["category"] == "environment" and "category_override" not in at[0], "a real environment failure is not repaired by repeating the task"


def test_a_permission_denial_is_never_overridden(lproj, monkeypatch):
    monkeypatch.setattr(SV, "_permission_denials", lambda p, a: ["rm -rf /"])
    assert go(lproj, [("e1", [t("w", "analyst", "true", checks=["file:docs/f.md"])])], lambda: phase(lproj, "w")["phase"] == "blocked")
    at = run_attempts(lproj)["w"]
    assert len(at) == 1 and at[0]["category"] == "permission_denied" and "category_override" not in at[0]


def test_diagnostics_with_an_environment_error_are_never_contradicted(lproj):
    p = lproj
    for text in ("check file: docs/f.md failed (missing)", ):
        assert SV._contradicted(p, "T-x", {}, "environment", [text, "ModuleNotFoundError: yaml"]) is False
        assert SV._contradicted(p, "T-x", {}, "permission_denied", [text]) is False, "only the listed categories can be overridden"
        assert SV._contradicted(p, "T-x", {}, "scientific_assumption", [text]) is False
        assert SV._contradicted(p, "T-x", {}, "environment", ["check metric:loss failed (loss not found"]) is False, "a missing metric is not a missing file"


# ------------------------------------------------------------------------------------------------ delayed evidence is the only thing that waits
def test_evidence_that_can_still_arrive_waits_and_is_not_repaired(lproj, monkeypatch):
    monkeypatch.setattr(HR, "sync", lambda *a, **k: {})
    run_py = ("import os; from research.store import Project; from research import runs; p=Project(os.environ['RESEARCH_ROOT']); "
              "e=p.get('task',os.environ['RESEARCH_TASK'])['related_experiment_id']; "
              "r=runs.create_run(p,e,command='job',backend='slurm',status='running',slurm_job_id='9001')")
    x = t("job", "analyst", f'{sys.executable} -c \\"{run_py}\\"', checks=["file:docs/f.md"])
    PS.apply(lproj, plan([("e1", [x])]))
    SV.plan_run(lproj, "fx", start_daemon=False)
    run_until(lproj, lambda: False, 20)
    tid = PS.load_state(lproj, "fx")["tasks"]["job"]
    a = {"verification": {}}
    assert SV.evidence_can_arrive(lproj, tid, a) is True, "an outstanding computational run can still deliver the file"
    assert SV._contradicted(lproj, tid, a, "environment", ["check file: docs/f.md failed (missing)"]) is False
    assert len(run_attempts(lproj)["job"]) == 1, "no second attempt was started while the run is outstanding"


# ------------------------------------------------------------------------------------------------ self-healing a task that is already blocked (the state PRAVAH was left in)
def test_self_healing_reclassifies_and_repairs_a_task_already_blocked_as_environment(lproj, monkeypatch):
    """Reproduce the stored T-023 state: blocked, category `environment`, the missing-file diagnostics, a budget left. The self-healing pass must turn it into ONE
    repair attempt on the same task (history kept), exactly once, within its bounds."""
    shell = f"MOCK_SH@1: {SAY_ENV}\\nMOCK_SH@2: mkdir -p docs && echo ok > docs/f.md"
    x = t("w", "analyst", "true", checks=["file:docs/f.md"]).replace("MOCK_SH: true", shell)
    # first run without the fix in place: classify as the old code did, by forcing the agent's category through the categorizer
    registry.apply(lproj, {"supervisor_assistant.self_heal": True}, reason="test")
    monkeypatch.setattr(SV, "_contradicted", lambda *a, **k: False)
    assert go(lproj, [("e1", [x])], lambda: phase(lproj, "w")["phase"] in ("blocked", "attention", "repair_pending"), 60)
    monkeypatch.undo()
    st = PS.load_state(lproj, "fx")
    rs = SV.load_run(lproj, st["plan_id"])
    tid = st["tasks"]["w"]
    e = rs["tasks"][tid]
    a = [x for x in e["attempts"] if not x.get("voided")][-1]
    a["category"] = "environment"
    e.update(phase="blocked", reason="environment: check file: docs/f.md failed (missing)")
    rs["state"] = "running"
    SV.save_run(lproj, rs)
    assert run_until(lproj, lambda: phase(lproj, "w")["phase"] == "verified", 120)
    at = run_attempts(lproj)["w"]
    assert at[0]["category_override"]["reported"] == "environment" and at[0].get("reclassified")
    assert (SV.load_run(lproj, st["plan_id"])["selfheal"]["task_repairs"] or {}).get(tid) == 1, "counted once, against the repair caps"
    assert len([a for a in at if a["state"] == "verified"]) == 1
