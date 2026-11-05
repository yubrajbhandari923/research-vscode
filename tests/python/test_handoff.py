"""Cross-provider continuation: same task, same worktree, portable context, never while the old process may still run."""
import sys

import pytest

from research import execution as E, planspec as PS, providers as pv, supervisor as SV
from research.util import ResearchError

from test_supervisor import phases, proj, run_until, task_yaml, yaml_plan   # noqa: F401  (fixtures)


def start(proj):
    proj.config["agents"]["profiles"]["fake2"] = dict(proj.config["agents"]["profiles"]["fake"])
    proj.save_config()
    PS.apply(proj, yaml_plan([task_yaml("a", "A", "exit 1", attempt="1")], extra_exec=", on_failure: pause"))
    SV.plan_run(proj, "demo-plan", start_daemon=False)
    assert run_until(proj, lambda: phases(proj)["a"] == "attention", 60)
    return PS.load_state(proj, "demo-plan")["tasks"]["a"]


def test_continue_with_another_provider_keeps_the_task_and_hands_over_context(proj):
    tid = start(proj)
    out = SV.continue_with(proj, tid, "fake2", "claude hit its limit")
    assert out["handoffs"][0]["from"] == "fake" and out["handoffs"][0]["to"] == "fake2" and out["plan_state"] == "paused"
    SV._set_state(proj, "demo-plan", "running")      # on_failure=pause paused the plan; a user pause is never overridden by a handoff
    r = SV.tick(proj)
    st = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])["tasks"][tid]
    assert r["launched"], (r, st.get("phase"), st.get("reason"))
    assert r["launched"][0]["task"] == tid
    rs = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])
    a = rs["tasks"][tid]["attempts"][-1]
    assert a["n"] == 2 and a["profile"] == "fake2" and a["session_mode"] == "handoff" and "resumed_session" not in a   # no provider session id crosses over
    text = open(proj.abspath(a["prompt_path"]), encoding="utf-8").read()
    assert "Handoff from another agent" in text and "claude hit its limit" in text
    assert SV.provider_status(proj, tid)["handoffs"][0]["to"] == "fake2"


def test_continue_is_refused_while_the_previous_process_may_still_run(proj):
    tid = start(proj)
    rs = SV.load_run(proj, PS.load_state(proj, "demo-plan")["plan_id"])
    ex = rs["tasks"][tid]["attempts"][-1]["execution"]
    import json
    rec = json.load(open(E.path(proj, ex)))
    rec.update(state="unknown", pid=999999, launcher_pid=999999)
    open(E.path(proj, ex), "w").write(json.dumps(rec))
    with pytest.raises(ResearchError, match="may still be running"):
        SV.continue_with(proj, tid, "fake2")
    with pytest.raises(ResearchError):
        SV.continue_with(proj, tid, "nope")


def test_automatic_fallback_is_opt_in_ordered_and_bounded(proj, monkeypatch):
    e = {"attempts": [{"n": 1, "profile": "claude", "state": "failed"}], "phase": "attention"}
    rs = {"state": "running"}
    monkeypatch.setattr(SV, "_provider_profiles", lambda p: {"claude": "claude", "codex": "codex", "agy": "agy"})
    assert not SV._fallback(proj, rs, "T-1", e, e["attempts"][0], "limit", {})            # off by default
    proj.config["agents"]["handoff"].update(enabled=True, on_provider_unavailable="try_next")
    assert SV._fallback(proj, rs, "T-1", e, e["attempts"][0], "limit", {}) and e["provider_override"] == "codex"
    e["attempts"].append({"n": 2, "profile": "codex", "state": "failed"})
    assert SV._fallback(proj, rs, "T-1", e, e["attempts"][-1], "limit", {}) and e["provider_override"] == "agy"
    e["attempts"].append({"n": 3, "profile": "agy", "state": "failed"})
    assert not SV._fallback(proj, rs, "T-1", e, e["attempts"][-1], "limit", {})            # every provider tried once: stop
    e2 = {"attempts": [{"n": 1, "profile": "claude", "state": "unknown"}], "phase": "attention"}
    assert not SV._fallback(proj, rs, "T-2", e2, e2["attempts"][0], "limit", {})           # unknown state: never relaunch


def test_agy_resume_and_session_id_shapes():
    argv = pv.build_argv({"command": "agy"}, "agy", None, "P", "/w", [], True)
    assert argv[-1] == "P" and "--mode" in argv and "--dangerously-skip-permissions" not in argv
    assert "--conversation" in pv.resume_argv("agy", argv, "abc")
    assert pv.session_id_of("agy", '{"conversation_id":"abc","status":"SUCCESS"}') == "abc"
