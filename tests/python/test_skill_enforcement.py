"""Skill levels: recommended (optional), required (blocks managed dispatch, injected + hashed), protocol (checks)."""
import os

import pytest

from research import agents, skills as SK
from research import services as S
from research.util import PolicyBlocked, ResearchError


def _skill(p, name, body="Do the thing carefully."):
    S.create_skill(p, name, description=f"{name} skill", body=body)
    return name


def _brief(p, d):
    with open(p.abspath(d["brief"]), encoding="utf-8") as f:
        return f.read()


def test_default_level_is_recommended_and_never_blocks(proj):
    _skill(proj, "optional-one")
    SK.set_skill(proj, "optional-one", applies_to=["analyst"])
    d = agents.dispatch(proj, "analyst", None, "summarize")
    assert d["skills"]["required"] == [] and "Required skills" not in _brief(proj, d)
    assert SK.list_skills(proj)[0]["level"] == "recommended"


def test_required_skill_is_injected_hashed_and_recorded(proj):
    _skill(proj, "careful", body="RULE-ALPHA: never guess a unit.")
    SK.set_skill(proj, "careful", applies_to=["analyst"], level="required")
    d = agents.dispatch(proj, "analyst", None, "summarize")
    [r] = d["skills"]["required"]
    assert r["name"] == "careful" and r["level"] == "required" and r["sha256"] == SK.digest(proj, "careful")
    text = _brief(proj, d)
    assert "RULE-ALPHA: never guess a unit." in text and r["sha256"][:12] in text
    side = proj.abspath(d["brief"])[:-3] + ".skills.json"
    assert os.path.exists(side)
    # the role it does not apply to is unaffected
    assert agents.dispatch(proj, "planner", None, "plan something")["skills"]["required"] == []


def test_required_skill_absent_blocks_dispatch_and_writes_nothing(proj):
    dd = proj.path("cache", "dispatch")
    n0 = len(os.listdir(dd)) if os.path.isdir(dd) else 0
    with pytest.raises(PolicyBlocked) as e:
        agents.dispatch(proj, "analyst", None, "x", require_skills=["no-such-skill"])
    assert "no-such-skill" in str(e.value)
    assert (len(os.listdir(dd)) if os.path.isdir(dd) else 0) == n0


def test_gated_skill_deleted_from_disk_still_blocks(proj):
    _skill(proj, "vanishing")
    SK.set_skill(proj, "vanishing", applies_to=["analyst"], level="required")
    SK.remove_skill(proj, "vanishing")  # folder gone, manifest entry removed too → no longer gated
    agents.dispatch(proj, "analyst", None, "x")
    _skill(proj, "vanishing2")
    SK.set_skill(proj, "vanishing2", applies_to=["analyst"], level="required")
    import shutil
    shutil.rmtree(proj.path("skills", "vanishing2"))  # manifest still says required
    with pytest.raises(PolicyBlocked):
        agents.dispatch(proj, "analyst", None, "x")


def test_changed_skill_is_detected_as_drift(proj):
    _skill(proj, "careful")
    SK.set_skill(proj, "careful", applies_to=["analyst"], level="required")
    d = agents.dispatch(proj, "analyst", None, "x")
    rec = d["skills"]["required"]
    assert SK.drift(proj, rec) == []
    with open(proj.path("skills", "careful", "SKILL.md"), "a", encoding="utf-8") as f:
        f.write("\nNew rule.\n")
    [msg] = SK.drift(proj, rec)
    assert "changed since it was dispatched" in msg
    SK.remove_skill(proj, "careful")
    assert "no longer installed" in SK.drift(proj, rec)[0]


def test_a_script_inside_the_skill_changes_the_digest(proj):
    _skill(proj, "careful")
    d0 = SK.digest(proj, "careful")
    os.makedirs(proj.path("skills", "careful", "scripts"))
    open(proj.path("skills", "careful", "scripts", "check.py"), "w").write("print(1)\n")
    assert SK.digest(proj, "careful") != d0


def test_protocol_requires_checks_and_runs_them(proj):
    _skill(proj, "strict")
    with pytest.raises(ResearchError):
        SK.set_skill(proj, "strict", level="protocol")
    with pytest.raises(ResearchError):
        SK.set_skill(proj, "strict", level="protocol", checks=["rm -rf /"])
    SK.set_skill(proj, "strict", level="protocol", checks=["research --version"], applies_to=["analyst"])
    assert SK.run_checks(proj, "strict")["verdict"] == "pass"
    SK.set_skill(proj, "strict", checks=["research --version", "research no-such-command-xyz"])
    r = SK.run_checks(proj, "strict")
    assert r["verdict"] == "fail" and [c["ok"] for c in r["checks"]] == [True, False]
    d = agents.dispatch(proj, "analyst", None, "x")
    assert d["skills"]["required"][0]["level"] == "protocol" and "research --version" in _brief(proj, d)


def test_skill_without_checks_is_never_reported_as_verified(proj):
    _skill(proj, "plain")
    assert SK.run_checks(proj, "plain")["verdict"] == "no_checks"


def test_unknown_level_in_manifest_never_becomes_a_gate(proj):
    _skill(proj, "odd")
    m = SK.load_manifest(proj)
    m["odd"] = {"level": "mandatory!!", "applies_to": ["analyst"]}
    SK.save_manifest(proj, m)
    assert SK.list_skills(proj)[0]["level"] == "recommended"
    agents.dispatch(proj, "analyst", None, "x")


def test_task_dispatch_records_skill_hashes_in_orchestration_ledger(proj):
    from research import plans as P
    from research import orchestrate as O
    _skill(proj, "careful")
    SK.set_skill(proj, "careful", level="required")
    pl = P.create_plan(proj, "pl", objective="o")
    t = P.create_task(proj, pl["id"], "t", goal="g", skills=["careful"])
    d = agents.dispatch(proj, "implementer", t["id"])
    assert d["skills"]["required"][0]["name"] == "careful"
    assert O.verify_work(proj, t["id"])["passed"] is False  # nothing verifiable here; and no crash with skill state present
    with open(proj.path("skills", "careful", "SKILL.md"), "a", encoding="utf-8") as f:
        f.write("\nchanged\n")


def test_audit_is_honest_about_direct_sessions(proj):
    _skill(proj, "careful")
    SK.set_skill(proj, "careful", level="required")
    a = SK.audit(proj)
    assert "NOT gated" in a["note"]
    assert any(r["name"] == "careful" and r["level"] == "required" for r in a["skills"])
    SK.link_skills(proj, ["claude", "agents"])
    assert SK.audit(proj)["problems"] == []


def test_cli_dispatch_require_skill_flag_injects_and_blocks(proj, capsys):
    import subprocess, sys, json
    _skill(proj, "needed-skill", body="NEEDED-RULE-XYZ")
    root = proj.root
    proj.close()
    base = [sys.executable, "-m", "research_cli", "--root", root, "--json", "dispatch", "analyst", "--print"]
    ok = subprocess.run(base + ["--require-skill", "needed-skill"], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    d = json.loads(ok.stdout)
    assert [r["name"] for r in d["skills"]["required"]] == ["needed-skill"]
    assert "NEEDED-RULE-XYZ" in open(os.path.join(root, d["brief"]), encoding="utf-8").read()
    bad = subprocess.run(base + ["--require-skill", "no-such-skill"], capture_output=True, text=True)
    assert bad.returncode != 0 and "no-such-skill" in (bad.stdout + bad.stderr)
