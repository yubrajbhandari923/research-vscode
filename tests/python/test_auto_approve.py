"""`auto_promotion.auto_approve_verified_code`: one persistent, human-bound setting that authorizes, approves and integrates eligible code promotions across plans
and restarts with no PIN prompt each time. It is standing AUTHORIZATION, not a bypass: tests, the independent reviewer and provenance still gate every promotion.
Also the AP-002 failure ("approved, not approved with every gate passing"): its three different causes are told apart, and an approval is never converted.
Real git, real supervisor ticks and a scripted agent; the independent reviewer is the same stand-in test_autopromote uses."""
import json
import os
import subprocess

import pytest

from research import autopromote as AP, notify, planspec as PS, promotion as PR, registry, supervisor as SV
from research.chat import alerts
from research.store import Project
from research.util import PolicyBlocked, ResearchError
from test_autopromote import CHAIN, ap, head, install_reviewer, item_status, items, out, plan, run_to, task  # noqa: F401  (fixtures and helpers)
from test_supervisor import run_until

KEY = "auto_promotion.auto_approve_verified_code"


def enable(p, **extra):
    """What the Settings toggle / `research config set` does: one human change, through the registry."""
    return registry.apply(p, {KEY: True, **extra}, reason="overnight runs")


def begin(p, exps, auto=False):
    PS.apply(p, plan(exps, auto=auto))
    SV.plan_run(p, "ap", start_daemon=False)
    return PS.load_state(p, "ap")["plan_id"]


def paused_or(p, pid, status):
    its = SV.load_run(p, pid)["autopromote"]["items"]
    return bool(its) and its[0]["status"] == status


# ------------------------------------------------------------------------------------------------ the setting: persistence and who may change it
def test_the_setting_is_persisted_listed_in_settings_and_bound_by_a_human(ap, repo):
    opt = next(o for o in registry.describe(ap, "git")["options"] if o["key"] == KEY)
    assert opt["type"] == "bool" and opt["risk"] == "policy" and opt["scopes"] == ["project"] and opt["value"] is False, "a toggle in Settings, off by default, a policy setting"
    assert not os.path.exists(AP._standing_path(ap))
    r = enable(ap)
    assert r["changed"] and "auto_approve_verified_code: true" in open(ap.config_path).read()
    b = json.load(open(AP._standing_path(ap)))
    assert b["fingerprint"] == AP.fingerprint(ap) and b["by"] and b["setting"] == KEY
    p2 = Project(str(repo))                                           # a new process
    try:
        assert AP.standing_enabled(p2) and AP.standing(p2)["fingerprint"] == b["fingerprint"]
    finally:
        p2.close()
    with pytest.raises(ResearchError, match="cannot be set at the machine level"):
        registry.apply(ap, {KEY: True}, scope="machine")               # standing authorization is per project, never machine-wide
    registry.apply(ap, {KEY: False})
    assert not AP.standing_enabled(ap) and not os.path.exists(AP._standing_path(ap)), "turned off: the authorization is gone, not idle"


def test_agents_cannot_turn_it_on_and_an_edit_around_settings_authorizes_nothing(ap, repo):
    human, ap.author = ap.author, {"author_type": "agent", "author_name": "claude", "author_model": None}
    with pytest.raises(ResearchError, match="Agents cannot change project policy"):
        registry.apply(ap, {KEY: True})
    ap.author = human
    text = open(ap.config_path).read()
    open(ap.config_path, "w").write(text + "\nauto_promotion:\n  auto_approve_verified_code: true\n" if "auto_promotion:" not in text else text.replace("auto_promotion:", "auto_promotion:\n  auto_approve_verified_code: true", 1))
    ap.config = ap.load_config()
    assert AP.standing_enabled(ap) and AP.standing(ap) is None, "the file says on, but no human bound it"
    pid = begin(ap, CHAIN[:1])
    st, rs = PS.load_state(ap, "ap"), SV.load_run(ap, pid)
    m = AP.mode(ap, st, rs)
    assert m["mode"] == "suspended" and "not given through Settings" in m["why"] and "config set" in m["why"], "fail closed, and say how to authorize"


# ------------------------------------------------------------------------------------------------ authorization without a PIN, gates intact
def test_with_it_on_every_plan_promotes_with_no_declaration_and_no_pin_prompt(ap, repo, monkeypatch):
    enable(ap)
    from research import approval
    monkeypatch.setattr(approval, "confirm", lambda *a, **k: pytest.fail("promotion must not ask for a PIN"))
    pid = begin(ap, CHAIN, auto=False)                                  # the plan does not declare execution.auto_promotion
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 150)
    assert set(item_status(ap, pid)) == {"integrated"} and open(os.path.join(repo, "lib3.py")).read().strip() == "V=3"
    recs = [json.loads(l) for l in open(ap.path("promotions.jsonl"))]
    for r in recs:
        if r["op"] in ("approve", "integrate"):
            assert r["author_type"] == "system" and r["mode"] == "supervisor_authorized" and r["standing"] is True and r["authorized_by"], "recorded as the supervisor, naming the human who authorized"
    it = items(ap, pid)[0]
    assert it["tests"]["state"] == "passed" and it["review"]["by"] == "reviewer", "the tests and the independent review still ran"
    assert out(repo, "rev-list", "--merges", "main") == "", "fast-forward only"


def test_the_gates_still_stop_it(ap, repo, monkeypatch):
    enable(ap)
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > data1.txt", checks=["file:data1.txt"])])])        # the promotion tests (`ls lib*.py`) find nothing: they fail
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 90)
    it = items(ap, pid)[0]
    assert "integration tests failed" in it["error"] and not it["transient"] and AP.retrying(ap, it) is False, "a failing test is a finding, never retried by itself"
    assert not os.path.exists(os.path.join(repo, "data1.txt"))
    assert SV.status(ap, "ap")["health"]["state"] == "halted", "and it interrupts: a real problem"


def test_a_reviewer_who_asks_for_changes_still_blocks_it(ap, repo, monkeypatch):
    enable(ap)
    install_reviewer(monkeypatch, ap, verdict="changes", note="rename the helper")
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 90)
    assert "rename the helper" in items(ap, pid)[0]["error"] and not os.path.exists(os.path.join(repo, "lib1.py"))


# ------------------------------------------------------------------------------------------------ restarts and policy changes
def test_it_survives_restarts_and_follows_a_policy_change_made_through_settings(ap, repo):
    enable(ap)
    pid = begin(ap, CHAIN[:1])
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 120)
    assert item_status(ap, pid) == ["integrated"]
    p2 = Project(str(repo))                                           # restart: a new process, same project
    try:
        st, rs = PS.load_state(p2, "ap"), SV.load_run(p2, pid)
        assert AP.mode(p2, st, rs)["mode"] == "active" and AP.mode(p2, st, rs)["standing"] is True and AP.mode(p2, st, rs)["by"]
        registry.apply(p2, {"auto_promotion.numerical_command": "true"})       # a human changes the policy in Settings: the authorization follows it
        assert AP.mode(p2, st, SV.load_run(p2, pid))["mode"] == "active"
        text = open(p2.config_path).read()
        open(p2.config_path, "w").write(text.replace("numerical_command: 'true'", "numerical_command: 'false'").replace("numerical_command: true", "numerical_command: 'false'"))
        p2.config = p2.load_config()                                  # the same change made around Settings (an agent editing the file)
        m = AP.mode(p2, st, SV.load_run(p2, pid))
        assert m["mode"] == "suspended" and "outside Settings" in m["why"] and "turn it off and on" in m["why"]
        registry.apply(p2, {KEY: False}, reason="re-authorize")        # turning it off and on again is the human act that re-binds (each change needs the PIN)
        registry.apply(p2, {KEY: True}, reason="re-authorize")
        assert AP.mode(p2, st, SV.load_run(p2, pid))["mode"] == "active"
    finally:
        p2.close()


def test_an_incomplete_setup_is_suspended_with_the_reason_not_silently_idle(ap, repo):
    enable(ap)
    registry.apply(ap, {"auto_promotion.reviewer_profile": "nobody-configured"})       # a human change through Settings: still active only if the setup is complete
    pid = begin(ap, CHAIN[:1])
    m = AP.mode(ap, PS.load_state(ap, "ap"), SV.load_run(ap, pid))
    assert m["mode"] == "suspended" and "not set up" in m["why"] and "reviewer_profile" in m["why"]


# ------------------------------------------------------------------------------------------------ AP-002: approved, but not integrable
def reach_approved(ap, pid):
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "approved"), 90)
    return items(ap, pid)[0]


def test_approved_but_a_gate_fails_is_named_as_the_gate_not_as_the_approval(ap, repo, monkeypatch):
    enable(ap)
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    state = {"fail": False}
    real = PR._scientific_content
    monkeypatch.setattr(PR, "_scientific_content", lambda p, c, h: {"state": "failed", "detail": "chapter.qmd: internal identifier RUN-0003 in scientific prose"} if state["fail"] else real(p, c, h))
    it = reach_approved(ap, pid)
    state["fail"] = True                                              # the provenance gate fails after the supervisor approved
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 60)
    err = items(ap, pid)[0]["error"]
    assert "is approved but cannot be integrated" in err and "scientific content provenance" in err and "RUN-0003" in err, err
    assert "not approved with every gate passing" not in err and not os.path.exists(os.path.join(repo, "lib1.py"))
    c = PR.show(ap, it["promotion"])
    r = PR.readiness(c)
    assert r["state"] == "approved" and r["approval_state"] == "approved" and r["approval_mode"] == "supervisor_authorized" and r["gate_problems"] and not r["ready"], \
        "state, approval mode and integration readiness are three different facts"


def test_the_supervisor_will_not_approve_what_integration_would_refuse(ap, repo, monkeypatch):
    enable(ap)
    monkeypatch.setattr(PR, "_scientific_content", lambda p, c, h: {"state": "failed", "detail": "identifier leak"})
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 90)
    it = items(ap, pid)[0]
    assert it["status"] == "paused" and "cannot be approved yet" in it["error"] and "scientific content provenance" in it["error"]
    assert PR.show(ap, it["promotion"])["state"] != "approved", "stopped before an approval record existed"


def test_a_human_approval_is_never_converted_into_the_supervisors_authorization(ap, repo, monkeypatch):
    enable(ap)
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    ran = {"n": 0}
    real = AP._integrate

    def hold(p, rs, item, auth, out):
        ran["n"] += 1
        if ran["n"] == 1 and item["status"] == "approved":
            raise ResearchError("held for the test")                    # not an approval of its own: only freezes the moment between the supervisor's approval and its merge
        return real(p, rs, item, auth, out)
    monkeypatch.setattr(AP, "_integrate", hold)
    it = reach_approved(ap, pid)
    PR.approve(ap, it["promotion"], "I read it myself")                  # a person's own approval replaces the supervisor's record
    ap.config["auto_promotion"]["auto_retry_attempts"] = 0
    main0 = head(repo, "main")
    monkeypatch.setattr(AP, "_integrate", real)
    c = PR.show(ap, it["promotion"])
    assert PR.readiness(c)["approval_mode"] == "human"
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 60)
    err = items(ap, pid)[0]["error"]
    assert "approval mode: human" in err and "never turns another approval into its own" in err and f"research promote integrate {it['promotion']} --confirm" in err, err
    assert head(repo, "main") == main0 and PR.show(ap, it["promotion"])["approval"].get("mode") != "supervisor_authorized", "nothing merged, nothing silently re-labelled"
    assert PR.show(ap, it["promotion"])["state"] == "approved"


# ------------------------------------------------------------------------------------------------ transient failures: bounded self-recovery
def dirty_main(repo):
    open(os.path.join(repo, "train.py"), "a").write("# someone is editing main\n")


def clean_main(repo):
    subprocess.run(["git", "checkout", "--", "train.py"], cwd=repo, check=True)


def test_a_transient_failure_retries_by_itself_and_does_not_interrupt(ap, repo):
    enable(ap, **{"auto_promotion.auto_retry_minutes": ["0.01"]})
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "approved"), 90)
    dirty_main(repo)                                                    # a person is editing main right now: nothing can be merged
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "paused"), 60)
    it = items(ap, pid)[0]
    assert it["transient"] and "uncommitted changes" in it["error"] and AP.retrying(ap, it), "recognized as transient"
    assert SV.status(ap, "ap")["health"]["state"] == "progressing", "it is recovering: nothing needs you yet, so nothing interrupts you"
    clean_main(repo)
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 120)
    it = items(ap, pid)[0]
    assert it["status"] == "integrated" and it["auto_retries"] == 1 and open(os.path.join(repo, "lib1.py")).read().strip() == "V=1"
    assert it["objections"][0]["why"], "the failure stays on record"
    assert sum(1 for l in open(ap.path("promotions.jsonl")) if json.loads(l)["op"] == "integrate") == 1, "one promotion, no duplicate"


def test_retries_are_bounded_then_it_asks_you(ap, repo):
    enable(ap, **{"auto_promotion.auto_retry_minutes": ["0.01"], "auto_promotion.auto_retry_attempts": 2})
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    run_to(ap, pid, lambda rs: paused_or(ap, pid, "approved"), 90)
    dirty_main(repo)
    run_to(ap, pid, lambda rs: (items(ap, pid)[0].get("auto_retries") or 0) >= 2 and items(ap, pid)[0]["status"] == "paused" and not AP.retrying(ap, items(ap, pid)[0]), 120)
    it = items(ap, pid)[0]
    assert it["auto_retries"] == 2 and "retried 2 time(s) automatically; this needs you" in it["error"] or "uncommitted changes" in it["error"]
    run_until(ap, lambda: False, 4)
    assert items(ap, pid)[0]["auto_retries"] == 2, "it stops at the limit"
    assert SV.status(ap, "ap")["health"]["state"] == "halted", "now it is a real problem and it interrupts"
    clean_main(repo)
    SV.autopromote(ap, "ap", "retry", item="AP-001")                    # the human way still works
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 120)


def test_a_success_is_announced_once(ap, repo, monkeypatch):
    enable(ap)
    monkeypatch.setattr(notify, "chat_enabled", lambda p: True)
    pid = begin(ap, [("e1", [task("a1", "echo V=1 > lib1.py", checks=["file:lib1.py"])])])
    run_to(ap, pid, lambda rs: rs["state"] == "completed", 120)
    sent = [json.loads(l) for l in open(alerts.outbox_path(ap)) if '"promotion_done"' in l]
    assert len(sent) == 1 and sent[0]["severity"] == "ok" and "AP-001" in sent[0]["text"] and "no scientific claim was approved" in sent[0]["text"]
    assert alerts.allowed(ap, "promotion_done"), "sent at the default (medium) level"
