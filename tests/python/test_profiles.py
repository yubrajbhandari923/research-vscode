"""Settings profiles: built-in default / interactive / overnight, custom ones at MACHINE level, applied per PROJECT, diffed, reverted, and the overnight readiness check."""
import argparse

import pytest

from research import autopromote as AP, profiles as PF, registry, supervisor as SV
from research.store import Project
from research.util import ResearchError
from test_supervisor import proj  # noqa: F401  (fixture)

KEY = "auto_promotion.auto_approve_verified_code"


def val(p, k):
    return registry.describe_value(p, k)


def test_builtins_and_diff(proj):
    assert PF.names()[:3] == ["default", "interactive", "overnight"]
    d = PF.diff(proj, "overnight")
    keys = {r["key"] for r in d["rows"]}
    assert "supervisor.usage_limit.on_limit" in keys and KEY in keys and d["differing"] == len(d["rows"])
    two = PF.diff(proj, "default", "overnight")
    assert two["left"] == "default" and two["right"] == "overnight" and any(r["key"] == KEY and r["profile"] is True and r["current"] is False for r in two["rows"])


def test_apply_is_per_project_audited_binds_the_standing_authorization_and_reverts(proj, tmp_path):
    pv = PF.preview(proj, "overnight")
    assert pv["needs_pin"] and KEY in pv["turns_on"] and pv["changes"]["supervisor.usage_limit.on_limit"] == "wait"
    assert val(proj, "supervisor.usage_limit.on_limit") == "halt", "a preview changes nothing"
    r = PF.apply(proj, "overnight", confirm=True)
    assert r["changed"] and val(proj, "supervisor.usage_limit.on_limit") == "wait" and val(proj, KEY) is True
    assert AP._standing_read(proj)["fingerprint"] == AP.fingerprint(proj), "the toggle went through Settings, so the human binding exists"
    assert PF.status(proj)["applied"] == "overnight" and PF.status(proj)["drift"] == []
    registry.apply(proj, {"supervisor.usage_limit.max_wait_hours": 3})
    assert [d["key"] for d in PF.status(proj)["drift"]] == ["supervisor.usage_limit.max_wait_hours"], "changed one by one afterwards: reported as drift"
    PF.revert(proj, confirm=True)
    assert val(proj, "supervisor.usage_limit.on_limit") == "halt" and val(proj, KEY) is False and PF.applied(proj) is None
    assert AP._standing_read(proj) is None, "reverting turned the authorization off"


def test_overrides_and_agents_cannot_apply(proj):
    PF.apply(proj, "overnight", {"supervisor.usage_limit.max_wait_hours": 14}, confirm=True)
    assert val(proj, "supervisor.usage_limit.max_wait_hours") == 14
    with pytest.raises(ResearchError, match="Invalid profile settings|positive|Expected"):
        PF.preview(proj, "overnight", {"supervisor.usage_limit.max_wait_hours": "soon"})
    proj.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    with pytest.raises(ResearchError, match="Agents cannot change project policy"):
        PF.apply(proj, "default", confirm=True)


def test_custom_profiles_live_on_the_machine_and_apply_per_project(proj, tmp_path):
    p = PF.create("mine", "long waits", base="overnight", settings={"supervisor.usage_limit.max_wait_hours": 14})
    assert not p["builtin"] and "mine" in PF.names() and PF.get("mine")["settings"][KEY] is True
    with pytest.raises(ResearchError, match="built-in"):
        PF.create("overnight", settings={KEY: True})
    with pytest.raises(ResearchError, match="not a setting|Invalid"):
        PF.create("bad", settings={"nope.nope": 1})
    second = Project.init(str(tmp_path / "second"), name="s", goal="g")
    try:
        assert "mine" in PF.names(), "visible from every project"
        PF.apply(proj, "mine", confirm=True)
        assert val(proj, "supervisor.usage_limit.max_wait_hours") == 14 and val(second, "supervisor.usage_limit.max_wait_hours") == 8, "applied to one project only"
    finally:
        second.close()
    snap = PF.create("snapshot", from_project=proj)
    assert snap["settings"]["supervisor.usage_limit.max_wait_hours"] == 14
    PF.delete("mine")
    assert "mine" not in PF.names()
    with pytest.raises(ResearchError, match="Built-in"):
        PF.delete("default")


def test_overnight_check_says_what_is_missing_then_ready(proj, monkeypatch):
    r = PF.check(proj, "overnight")
    ids = {c["id"]: c["status"] for c in r["checks"]}
    assert not r["ready"] and ids["supervisor.usage_limit.on_limit"] == "fail" and ids["supervisor"] == "fail" and r["verdict"].startswith("NOT ready")
    PF.apply(proj, "overnight", confirm=True)
    proj.config.setdefault("agents", {}).setdefault("profiles", {})["rev"] = {"command": "true"}
    proj.save_config()
    registry.apply(proj, {"promotion.test_command": "true", "auto_promotion.reviewer_profile": "rev"})
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": True})
    r = PF.check(proj, "overnight")
    assert r["ready"] and r["blockers"] == 0 and r["verdict"].startswith("READY"), [c for c in r["checks"] if c["status"] != "ok"]
    assert any(c["id"] == "chat" and c["status"] == "warn" for c in r["checks"]), "no chat: a warning, not a blocker"
    assert any(c["id"] == "standing_authorization" and c["status"] == "ok" for c in r["checks"])


def test_cli_list_diff_preview_and_apply(proj, capsys):
    from research_cli.commands import profile as C
    ns = lambda **k: argparse.Namespace(**{"action": "list", "name": None, "other": None, "set": None, "base": None, "from_project": False, "all": False, "yes": False, "note": None, "json": False, **k})
    C.cmd_profile(ns(), proj)
    assert "overnight" in capsys.readouterr().out
    C.cmd_profile(ns(action="apply", name="overnight"), proj)
    out = capsys.readouterr().out
    assert "preview only" in out and "turns ON" in out and val(proj, KEY) is False
    C.cmd_profile(ns(action="apply", name="overnight", yes=True), proj)
    assert val(proj, KEY) is True and "applied" in capsys.readouterr().out
    C.cmd_profile(ns(action="status"), proj)
    assert "overnight" in capsys.readouterr().out
