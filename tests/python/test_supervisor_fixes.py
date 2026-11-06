"""Regression tests for the PRAVAH overnight-run failures: role/task contradictions, missing prerequisites, safe reload/restart,
configuration consistency and the effective-concurrency report. Real git, real supervisor ticks, a scripted agent."""
import json
import os
import textwrap
import time

import pytest

from research import autopromote as AP, execution as E, planspec as PS, supervisor as SV, workspace as W
from research.util import PolicyBlocked, ResearchError
from test_supervisor import lproj, run_until  # noqa: F401  (lproj is a fixture)


def t(key, role, shell, deps=(), checks=(), acceptance=None, attempts=3):
    d = f"depends_on: [{', '.join(deps)}], " if deps else ""
    c = f"checks: [{', '.join(repr(x) for x in checks)}], " if checks else ""
    a = f"acceptance: 'Deliver it. Outputs: {acceptance}.', " if acceptance else ""
    return (f'{{key: {key}, title: T {key}, {d}{c}{a}execution: {{max_attempts: {attempts}}}, agent: {{role: {role}, profile: fake}}, '
            f'prompt: "Work.\\nMOCK_SH: {shell}"}}')


def plan(exps, exec_extra="", key="fx"):
    body = "".join(f"      - key: {ek}\n        title: Exp {ek}\n        hypothesis: h\n        tasks:\n" + "".join(f"          - {x}\n" for x in tasks) for ek, tasks in exps)
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: {key}
            title: Fixes
            objective: o
            execution: {{max_parallel_agents: 1, max_attempts_per_task: 3{exec_extra}}}
            experiments:
        """) + body


def phase(p, key, ref="fx"):
    return {x["key"]: x for x in SV.status(p, ref)["tasks"]}[key]


def run_attempts(p, ref="fx"):
    st = PS.load_state(p, ref)
    return {k: SV.load_run(p, st["plan_id"])["tasks"].get(tid, {}).get("attempts", []) for k, tid in st["tasks"].items()}


# ------------------------------------------------------------------------------------------------ 1. role / task compatibility
VERIFIER_AUTHORS = [("e1", [t("rep", "verifier", "true", checks=["file:docs-gate.md"], acceptance="docs-gate.md")])]


def test_validate_and_diff_flag_a_verifier_that_must_author_its_own_deliverable(lproj):
    text = plan(VERIFIER_AUTHORS)
    v = PS.validate(text, lproj)
    assert v["ok"] and len(v["warnings"]) == 1 and "fx/rep is a verifier" in v["warnings"][0] and "docs-gate.md" in v["warnings"][0] and "analyst" in v["warnings"][0]
    d = PS.diff(lproj, text)
    assert d["ok"] and any("fx/rep is a verifier" in w for w in d["warnings"]) and d["plans"][0]["warnings"] == d["warnings"]
    assert len(d["warnings"]) == 1, "no duplicates"
    # advice only: the plan applies unchanged and the verifier keeps its role
    PS.apply(lproj, text)
    assert "role: verifier" in PS.export(lproj, "fx")


def test_a_verifier_that_checks_an_upstream_authors_report_is_fine(lproj):
    ok = [("e1", [t("author", "analyst", "echo r > rep.md", checks=["file:rep.md"], acceptance="rep.md"),
                  t("review", "verifier", "true", deps=["author"], checks=["file:rep.md"])])]
    assert PS.validate(plan(ok), lproj)["warnings"] == []


# ------------------------------------------------------------------------------------------------ 2. failure handling
def test_a_verifier_asked_to_author_its_report_is_blocked_after_one_attempt_not_redispatched(lproj):
    PS.apply(lproj, plan(VERIFIER_AUTHORS))
    SV.plan_run(lproj, "fx", start_daemon=False)
    assert run_until(lproj, lambda: phase(lproj, "rep")["phase"] == "blocked", 90)
    run_until(lproj, lambda: False, 5)
    at = run_attempts(lproj)["rep"]
    assert len(at) == 1, "no second dispatch of a verifier who is forbidden to create the file"
    assert at[0]["category"] == "role_contradiction" and "analyst" in at[0]["blocker"] and at[0]["verification"]["passed"] is False
    r = phase(lproj, "rep")["reason"]
    assert "role_contradiction" in r and "nothing was rewritten" in r and "review requirement is unchanged" in r
    assert "role: verifier" in PS.export(lproj, "fx"), "the plan was not silently rewritten"


def test_a_verifier_whose_upstream_file_is_not_in_its_worktree_is_a_missing_prerequisite(lproj):
    # the PRAVAH handoff failure: an implementer wrote docs-a.md on e1's own branch; the verifier (which runs in the main checkout) does not have it
    exps = [("e1", [t("a1", "implementer", "echo a > docs-a.md", checks=["file:docs-a.md"], acceptance="docs-a.md")]),
            ("e2", [t("v1", "verifier", "true", deps=["a1"], checks=["file:docs-a.md"])])]
    PS.apply(lproj, plan(exps))
    SV.plan_run(lproj, "fx", start_daemon=False)
    assert run_until(lproj, lambda: phase(lproj, "v1")["phase"] == "blocked", 120)
    run_until(lproj, lambda: False, 4)
    at = run_attempts(lproj)
    assert len(at["v1"]) == 1 and at["v1"][0]["category"] == "missing_prerequisite"
    assert len(at["a1"]) == 1 and at["a1"][0]["state"] == "verified", "the author's history is untouched"
    r = phase(lproj, "v1")["reason"]
    assert "docs-a.md" in r and "a1" in r and "promote" in r.lower() and "Not retried" in r


def test_an_ordinary_missing_file_by_the_owning_task_is_still_repaired_within_its_budget(lproj):
    PS.apply(lproj, plan([("e1", [t("w", "implementer", "echo ok > out.txt", checks=["file:out.txt"])])]).replace("MOCK_SH:", "MOCK_SH@2:"))
    SV.plan_run(lproj, "fx", start_daemon=False)
    assert run_until(lproj, lambda: phase(lproj, "w")["phase"] == "verified", 90)
    at = run_attempts(lproj)["w"]
    assert [a["category"] for a in at[:-1]] == ["incomplete_deliverable"] and len(at) == 2, "repairable failures keep their bounded retries"


# ------------------------------------------------------------------------------------------------ 3. configuration consistency
def test_one_canonical_test_command_and_a_conflict_is_an_error(lproj):
    lproj.config["promotion"] = {"test_command": "pytest -q"}
    lproj.config["auto_promotion"] = {"test_command": "pytest -q"}
    pol = AP.policy(lproj)
    assert pol["test_command"] == "pytest -q" and pol["test_command_source"] == "promotion.test_command" and pol["conflicts"] == []
    lproj.config["promotion"] = {}
    pol = AP.policy(lproj)
    assert pol["test_command_source"].startswith("auto_promotion.test_command") and "legacy" in pol["test_command_source"]
    lproj.config["promotion"] = {"test_command": "pytest -q tests"}
    lproj.config["auto_promotion"] = {"test_command": "make test"}
    assert AP.policy(lproj)["test_command"] == "pytest -q tests" and AP.policy(lproj)["conflicts"]
    from research.registry import OPTIONS
    keys = {o.key for o in OPTIONS}
    assert {"promotion.test_command", "auto_promotion.numerical_command", "auto_promotion.reviewer_profile", "auto_promotion.require_distinct_reviewer"} <= keys
    assert "auto_promotion.test_command" not in keys, "the Settings UI shows only the canonical command"
    assert SV.validate_settings(lproj) and "differ" in SV.validate_settings(lproj)[0]


def test_pin_errors_distinguish_wrong_pin_no_terminal_and_agent_shell(monkeypatch, tmp_path):
    from research import approval as A
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    A.set_pin("4321")
    with pytest.raises(PolicyBlocked, match="Incorrect PIN .*attempt"):
        A.verify("0000")
    monkeypatch.setattr(A, "_has_tty", lambda: False)
    with pytest.raises(PolicyBlocked, match="no interactive terminal"):
        A.verify(None)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(PolicyBlocked, match="agent-controlled environment .*CLAUDECODE"):
        A.verify(None)
    d = json.load(open(A._path()))
    assert d["fails"] == 1, "only the wrong PIN counted"
    for m in ("4321", "0000"):
        try:
            A.verify(None)
        except PolicyBlocked as e:
            assert m not in str(e) and m not in (e.hint or "")


# ------------------------------------------------------------------------------------------------ 4. reload / restart
def slow_plan(sleep=8, par=1):
    return plan([("e1", [t("s", "implementer", f"sleep {sleep}; echo done > s.txt", checks=["file:s.txt"], attempts=1)])]).replace("max_parallel_agents: 1", f"max_parallel_agents: {par}")


def test_reload_validates_applies_and_classifies_without_touching_the_running_agent(lproj):
    PS.apply(lproj, slow_plan(8))
    SV.plan_run(lproj, "fx", start_daemon=False)
    d = SV.daemon_start(lproj, detach=True)
    try:
        assert d["running"]
        assert run_until_phase(lproj, "s", "running", 60)
        ex = run_attempts(lproj)["s"][0]["execution"]
        pid0 = d["pid"]
        lproj.config["supervisor"]["poll_seconds"] = 2
        lproj.config["supervisor"]["max_total_attempts"] = 50
        lproj.config["hpc"] = {**(lproj.config.get("hpc") or {}), "default_cluster": "duke"}
        lproj.save_config()
        r = SV.daemon_reload(lproj)
        assert r["valid"] and r["applied"], r
        assert "supervisor.poll_seconds" in r["changed"]["live"] and "supervisor.max_total_attempts" in r["changed"]["live"]
        assert any(k.startswith("hpc") for k in r["changed"]["next_launch"]) and not r["changed"]["restart"]
        s = SV.daemon_status(lproj)
        assert s["pid"] == pid0 and s["running"] and s["last_reload"]["errors"] == [], "same process, no restart"
        assert E.show(lproj, ex)["state"] in ("launched", "running"), "the agent was not interrupted"
        assert run_until(lproj, lambda: phase(lproj, "s")["phase"] == "verified", 90)
        assert len(run_attempts(lproj)["s"]) == 1
        # an invalid configuration is refused before anything is applied
        lproj.config["supervisor"]["max_attempts_per_task"] = 0
        lproj.save_config()
        bad = SV.daemon_reload(lproj)
        assert not bad["valid"] and any("max_attempts_per_task" in e for e in bad["errors"]) and not bad["applied"]
        assert not os.path.exists(SV.sdir(lproj, "reload.request")), "nothing was requested"
    finally:
        SV.daemon_stop(lproj)


def run_until_phase(p, key, ph, timeout):
    """Phase reached AND, for a running task, its agent process is recorded (the attempt is reserved a moment before)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if phase(p, key)["phase"] == ph and (ph != "running" or (run_attempts(p).get(key) or [{}])[-1].get("execution")):
            return True
        time.sleep(0.3)
    return False


def test_reload_reports_when_a_restart_is_needed_and_when_nothing_runs(lproj):
    r = SV.daemon_reload(lproj)
    assert r["valid"] and not r["applied"] and "not running" in r["note"]
    PS.apply(lproj, slow_plan(1))
    SV.plan_run(lproj, "fx", start_daemon=False)
    SV.daemon_start(lproj, detach=True)
    try:
        d = json.load(open(SV._daemon_path(lproj)))
        d["version"] = "0.0.1"                                # the supervisor was started from an older Research OS
        open(SV._daemon_path(lproj), "w").write(json.dumps(d))
        s = SV.daemon_status(lproj)
        assert s["restart_required"] and "0.0.1" in s["restart_reason"]
        r = SV.daemon_reload(lproj)
        assert r["restart_required"] and any("0.0.1" in x for x in r["restart_reasons"])
    finally:
        SV.daemon_stop(lproj)


def test_restart_replaces_the_process_and_recovers_the_running_task_without_duplicates(lproj):
    PS.apply(lproj, slow_plan(10))
    SV.plan_run(lproj, "fx", start_daemon=False)
    d0 = SV.daemon_start(lproj, detach=True)
    try:
        assert run_until_phase(lproj, "s", "running", 60)
        a0 = run_attempts(lproj)["s"][0]
        out = SV.daemon_restart(lproj, timeout=60)
        assert out["restarted"] and out["pid"] != d0["pid"] and out["previous_pid"] == d0["pid"], out
        assert SV.daemon_status(lproj)["running"]
        assert E.show(lproj, a0["execution"])["state"] in ("launched", "running", "completed"), "the agent survived the restart"
        assert run_until(lproj, lambda: phase(lproj, "s")["phase"] == "verified", 120)
        at = run_attempts(lproj)["s"]
        assert len(at) == 1 and at[0]["execution"] == a0["execution"], "the same attempt finished; nothing was launched twice"
        assert SV.load_run(lproj, PS.load_state(lproj, "fx")["plan_id"])["budgets"]["attempts"] == 1
        assert run_until(lproj, lambda: SV.load_run(lproj, PS.load_state(lproj, "fx")["plan_id"])["state"] == "completed", 60)
    finally:
        SV.daemon_stop(lproj)


def test_restart_when_not_running_just_starts_and_agents_cannot_reload_or_restart(lproj):
    lproj.author = {"author_type": "agent", "author_name": "claude", "author_model": None}
    for fn in (SV.daemon_reload, SV.daemon_restart):
        with pytest.raises(PolicyBlocked):
            fn(lproj)
    lproj.author = {"author_type": "human", "author_name": "alice", "author_model": None}
    try:
        r = SV.daemon_restart(lproj)
        assert r["running"] and r["restarted"] is False and "was not running" in r["note"]
    finally:
        SV.daemon_stop(lproj)


# ------------------------------------------------------------------------------------------------ 5. parallelism
PAR = [("e1", [t("a1", "implementer", "sleep 3; echo 1 > a1.txt", checks=["file:a1.txt"]), t("a2", "implementer", "sleep 3; echo 2 > a2.txt", checks=["file:a2.txt"])]),
       ("e2", [t("b1", "implementer", "sleep 3; echo 3 > b1.txt", checks=["file:b1.txt"])]),
       ("e3", [t("c1", "implementer", "sleep 3; echo 4 > c1.txt", checks=["file:c1.txt"])])]


def watch_running(p, seconds):
    peak, same_tree = 0, False
    t0 = time.time()
    while time.time() - t0 < seconds:
        SV.tick(p)
        live = [x for x in SV.status(p, "fx")["tasks"] if x["phase"] in ("running", "verifying")]
        peak = max(peak, len(live))
        exps = [x["experiment"] for x in live]
        same_tree = same_tree or len(exps) != len(set(exps))
        if SV.status(p, "fx")["state"] == "completed":
            break
        time.sleep(0.3)
    return peak, same_tree


def test_independent_experiments_run_concurrently_but_one_worktree_never_runs_two_agents(lproj):
    PS.apply(lproj, plan(PAR).replace("max_parallel_agents: 1", "max_parallel_agents: 3"))
    SV.plan_run(lproj, "fx", start_daemon=False)
    peak, same_tree = watch_running(lproj, 120)
    assert peak >= 2, "dependency-ready tasks of different experiments overlapped"
    assert not same_tree, "a1 and a2 share the e1 worktree and were serialized"
    assert SV.status(lproj, "fx")["state"] == "completed"


def test_effective_concurrency_says_where_the_number_comes_from(lproj):
    PS.apply(lproj, plan(PAR[:1]).replace("max_parallel_agents: 1, ", ""))        # nothing set anywhere: the built-in default
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 1 and c["source"] == "built-in default" and c["plan"] is None and c["project"] is None
    lproj.config["supervisor"]["max_parallel_agents"] = 4                            # set in the project's own file
    lproj.save_config()
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 4 and c["source"].startswith("project settings") and c["plan"] is None and c["project"] == 4
    PS.apply(lproj, plan(PAR[:1]).replace("max_parallel_agents: 1", "max_parallel_agents: 2"))     # the plan overrides it
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 2 and c["source"].startswith("plan YAML") and c["plan"] == 2 and c["project"] == 4 and c["running"] == 0
    full = plan(PAR[:1]).replace("max_parallel_agents: 1, ", "").replace("objective: o", "objective: o\n    scope: full")
    PS.apply(lproj, full)                                                            # a full document that no longer lists the key removes it
    c = SV.status(lproj, "fx")["concurrency"]
    assert c["effective"] == 4 and c["source"].startswith("project settings")
