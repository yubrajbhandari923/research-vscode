"""Repair-attempt escalation to the stronger model (supervisor, real ticks)."""
import os

import pytest
from research import planspec as PS
from research import supervisor as SV
from test_supervisor import lproj
from test_supervisor import run_until
from test_supervisor import task_yaml
from test_supervisor import yaml_plan


# ------------------------------------------------------------------------------------------------ escalation
def attempts(p, ref="demo-plan"):
    st = PS.load_state(p, ref)
    return [a for e in SV.load_run(p, st["plan_id"])["tasks"].values() for a in e["attempts"]]


ESC = ", escalation: {model: big-model, effort: high}"


def incomplete_plan(extra):
    # attempt 1 does nothing (deliverable never written); attempt 2 writes it
    return yaml_plan([task_yaml("a", "A", "echo done > out.txt", checks=["file:out.txt"], attempt="2")], extra_exec=extra)


pytestmark = pytest.mark.skipif(os.name == "nt", reason="pty and symlinks are POSIX")


def test_incomplete_deliverable_escalates_the_repair_attempt_to_the_stronger_model(lproj):
    PS.apply(lproj, incomplete_plan(ESC))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: {t["key"]: t["phase"] for t in SV.status(lproj, "demo-plan")["tasks"]}["a"] == "verified", 90)
    a1, a2 = attempts(lproj)
    assert a1["category"] == "incomplete_deliverable" and not a1.get("escalated") and a1.get("model") != "big-model"
    assert a2["escalated"] == {"model": "big-model", "effort": "high"} and a2["model"] == "big-model" and a2["state"] == "verified"
    from research import execution as E
    assert E.show(lproj, a2["execution"])["request"]["effort"] == "high" and E.show(lproj, a1["execution"])["request"].get("effort") is None
    assert "escalated" in " ".join(e["summary"] for e in lproj.q("SELECT summary FROM events WHERE action='supervisor_escalated'"))


def test_no_escalation_unless_configured_or_for_other_failures(lproj):
    PS.apply(lproj, incomplete_plan(""))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: {t["key"]: t["phase"] for t in SV.status(lproj, "demo-plan")["tasks"]}["a"] == "verified", 90)
    assert not any(a.get("escalated") for a in attempts(lproj)), "default: never escalates"


def test_a_wrong_result_is_not_an_incomplete_deliverable_and_escalation_is_bounded(lproj):
    # the file exists but a command check fails on attempt 1: incorrect_implementation, so no escalation
    PS.apply(lproj, yaml_plan([task_yaml("a", "A", "echo wrong > out.txt", checks=["file:out.txt", "cmd:grep -q right out.txt"], attempt="1")], extra_exec=ESC))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    run_until(lproj, lambda: len(attempts(lproj)) >= 2 and attempts(lproj)[-1]["state"] in ("failed", "verified", "done"), 90)
    a1 = attempts(lproj)[0]
    assert a1["category"] == "incorrect_implementation" and not any(a.get("escalated") for a in attempts(lproj))


def test_plan_validation_rejects_bad_escalation():
    for bad in (", escalation: {color: red}", ", escalation: {on: [incomplete_deliverable]}", ", escalation: {model: x, on: [scientific_assumption]}"):
        with pytest.raises(PS.PlanSpecError, match="escalation"):
            PS.validate(incomplete_plan(bad))
    assert PS.validate(incomplete_plan(ESC))["ok"]
