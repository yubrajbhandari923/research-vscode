"""Supervisor intervention, halt detection, the debug agent and the chat alerts. Real supervisor ticks, git and a scripted agent. The planning/debug
agent (`intervene.run_assistant`) is the only stand-in; Telegram is the shared fake Bot API (tests/python/fakes/fake_telegram.py): no model and no
network are used. Gateway-level behaviour lives in test_chat_*.py."""
import asyncio
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from research import approval as A, intervene as I, notify, planspec as PS, supervisor as SV
from research.util import PolicyBlocked, ResearchError
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fakes"))
from fake_telegram import FakeTelegram  # noqa: E402
from test_supervisor import lproj, run_until  # noqa: F401  (lproj is a fixture)
from test_supervisor import phase, plan, run_attempts, t

TOKEN = "123456789:AAH-fake_token_for_tests_0123456789abc"
HUMAN = {"author_type": "human", "author_name": "alice", "author_model": None}
AGENT = {"author_type": "agent", "author_name": "claude", "author_model": None}


@pytest.fixture
def stub(monkeypatch):
    """Scriptable planner/debug agent: `stub.reply = {...}` (or a raw string) is what the 'model' returns."""
    class S:
        reply = None
        calls = []
    s = S()
    s.calls = []

    def fake(p, objective, prompt):
        s.calls.append((objective, prompt))
        return s.reply if isinstance(s.reply, str) else json.dumps(s.reply)
    monkeypatch.setattr(I, "run_assistant", fake)
    return s


def start(p, exps, **kw):
    PS.apply(p, plan(exps, **kw))
    SV.plan_run(p, "fx", start_daemon=False)


def blocked(p, key):
    return phase(p, key)["phase"] == "blocked"


def tid_of(key):
    return None


def ticks(p, pred, timeout=90):
    return run_until(p, pred, timeout)


def iv_state(p, iid):
    return I.load(p, iid)["state"]


# ------------------------------------------------------------------------------------------------ halt detection
def test_status_says_halted_when_nothing_moves_and_waiting_for_approval_when_only_approval_is_pending(lproj):
    start(lproj, [("e1", [t("a", "implementer", "true", checks=["file:never.txt"], attempts=1), t("b", "implementer", "echo b > b.txt", deps=["a"], checks=["file:b.txt"])])])
    assert ticks(lproj, lambda: blocked(lproj, "a"))
    s = SV.status(lproj, "fx")
    assert s["state"] == "running", "the run state is unchanged"
    h = s["health"]
    assert h["state"] == "halted" and "HALTED" in h["label"] and [b["task"] for b in h["blocking"]] == [phase(lproj, "a")["id"]] and h["fingerprint"]
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "cli"))
    from research_cli.commands import supervisor as supervisor_cmds
    text = "\n".join(supervisor_cmds._status_lines(s))
    assert "HALTED" in text and "nothing is running" in text and "supervisor instruct" in text
    # a plan waiting only for a start approval is a different thing
    PS.apply(lproj, plan([("e1", [t("c", "implementer", "true")])], key="gate").replace("execution: {max_attempts: 3}", "execution: {max_attempts: 3, requires_approval: true}"))
    SV.plan_run(lproj, "gate", start_daemon=False)
    SV.tick(lproj)
    assert SV.status(lproj, "gate")["health"]["state"] == "waiting_for_approval"


def test_a_plan_that_is_moving_is_not_reported_as_halted(lproj):
    start(lproj, [("e1", [t("s", "implementer", "sleep 6; echo ok > s.txt", checks=["file:s.txt"])])])
    assert ticks(lproj, lambda: phase(lproj, "s")["phase"] == "running", 30)
    assert SV.status(lproj, "fx")["health"]["state"] == "progressing"
    assert ticks(lproj, lambda: SV.status(lproj, "fx")["state"] == "completed", 60)
    assert SV.status(lproj, "fx")["health"]["state"] == "completed"


# ------------------------------------------------------------------------------------------------ scientific amendment, one approval, evidence kept
WRONG = [("e1", [t("w", "implementer", "echo wrong > out.txt", checks=["file:out.txt", "cmd:grep -q right out.txt"], attempts=1)])]


def test_scientific_amendment_needs_one_pin_approval_then_resumes_and_keeps_the_evidence(lproj, stub):
    A.set_pin("4321")
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    old = run_attempts(lproj)["w"][0]["verification"]["diagnostics"]
    stub.reply = {"summary": "The 'right' text check is the wrong test for this output; require the file only.", "rationale": "Researcher states the output content is not the criterion.",
                  "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}, "why": "relax"}, {"type": "reverify"}]}
    it = I.request(lproj, phase(lproj, "w")["id"], "The output only has to exist; drop the content check.")
    assert it["state"] == "proposed" and it["protected"] == ["scientific"] and any("[scientific*]" in x for x in it["preview"])
    ticks(lproj, lambda: False, 4)
    assert "grep" in json.dumps(PS.export(lproj, "fx")), "nothing applied before approval"
    with pytest.raises(PolicyBlocked):
        I.approve(lproj, it["id"], "no", pin="0000")                    # wrong PIN
    with pytest.raises(ResearchError, match="rationale"):
        I.approve(lproj, it["id"], None, pin="4321")                    # a reason is required for a protected change
    assert iv_state(lproj, it["id"]) == "proposed"
    I.approve(lproj, it["id"], "agreed, the content check was wrong", pin="4321")           # ONE approval for the whole proposal
    assert ticks(lproj, lambda: iv_state(lproj, it["id"]) == "resolved", 90)
    done = I.load(lproj, it["id"])
    assert [h["state"] for h in done["history"]] == ["requested", "proposed", "approved", "implementing", "verifying", "resolved"]
    assert done["approval"]["via"].startswith("human approval with PIN") and done["approval"]["by"]
    assert "grep" in json.dumps(list(done["originals"].values())[0]["checks"]), "the original acceptance criteria are preserved verbatim"
    assert "grep" not in PS.export(lproj, "fx") and "file:out.txt" in PS.export(lproj, "fx")
    first = run_attempts(lproj)["w"][0]
    assert first["verification_history"][0]["diagnostics"] == old and first["verification_history"][0]["passed"] is False, "the failed result is kept as evidence, not overwritten"
    assert first["verification"]["passed"] is True and first["verification"]["ts"] > first["verification_history"][0]["ts"] or first["verification"]["passed"]
    d = [x for x in lproj.list("decision") if "amended" in x["statement"].lower()]
    assert d and it["id"] in d[0]["statement"] and "Instruction:" in d[0]["reason"] and "Before:" in d[0]["reason"] and "Approved by" in d[0]["reason"]
    assert phase(lproj, "w")["phase"] == "verified"
    assert ticks(lproj, lambda: SV.status(lproj, "fx")["state"] == "completed", 30), "downstream/plan resumed automatically"


def test_a_rejected_approval_changes_nothing(lproj, stub):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stub.reply = {"summary": "s", "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}, {"type": "unblock"}]}
    it = I.request(lproj, phase(lproj, "w")["id"], "loosen it")
    body = lambda: PS.export(lproj, "fx").split("\n", 1)[1]        # the first line carries the export time
    before = body()
    I.reject(lproj, it["id"], "no: the content check is the science")
    ticks(lproj, lambda: False, 5)
    assert iv_state(lproj, it["id"]) == "rejected" and body() == before and blocked(lproj, "w")
    assert I.load(lproj, it["id"])["rejection"]["reason"].startswith("no:")
    assert not I.load(lproj, it["id"])["applied"]
    again = I.request(lproj, phase(lproj, "w")["id"], "try something else")          # a rejected one does not block the next instruction
    assert again["id"] != it["id"]


# ------------------------------------------------------------------------------------------------ missing deliverable / role, engineering repair
def test_a_role_contradiction_is_fixed_by_one_protected_approval_and_the_task_resumes(lproj, stub):
    start(lproj, [("e1", [t("rep", "verifier", "echo x > gate.md", checks=["file:gate.md"], acceptance="gate.md")])])
    PS.apply(lproj, plan([("e1", [t("rep", "verifier", "echo x > gate.md", checks=["file:gate.md"], acceptance="gate.md")])]).replace("MOCK_SH:", "MOCK_SH@2:"))
    assert ticks(lproj, lambda: blocked(lproj, "rep"))
    assert run_attempts(lproj)["rep"][0]["category"] == "role_contradiction"
    stub.reply = {"summary": "Make it the report's author.", "actions": [{"type": "amend_task", "fields": {"agent": {"role": "analyst", "profile": "fake"}}}, {"type": "unblock"}]}
    it = I.request(lproj, phase(lproj, "rep")["id"], "This task has to write the gate report: make it an analyst.")
    assert it["protected"] == ["scientific"], "a role change can weaken independent review, so it is protected"
    A.set_pin("4321")
    I.approve(lproj, it["id"], "intended: an analyst authors the report", pin="4321")
    assert ticks(lproj, lambda: iv_state(lproj, it["id"]) == "resolved", 120)
    assert len(run_attempts(lproj)["rep"]) == 2 and phase(lproj, "rep")["role"] == "analyst"


def test_engineering_repair_grants_attempts_only_with_approval_and_guides_the_next_attempt(lproj, stub):
    start(lproj, [("e1", [t("e", "implementer", "echo ok > out.txt", checks=["file:out.txt"], attempts=1)])])
    PS.apply(lproj, plan([("e1", [t("e", "implementer", "echo ok > out.txt", checks=["file:out.txt"], attempts=1)])]).replace("MOCK_SH:", "MOCK_SH@2:"))
    assert ticks(lproj, lambda: blocked(lproj, "e"))
    stub.reply = {"summary": "Allow one more attempt and tell the agent to write the file.", "actions": [
        {"type": "guide", "text": "Write out.txt before finishing."}, {"type": "set_max_attempts", "value": 3}, {"type": "retry"}]}
    it = I.request(lproj, phase(lproj, "e")["id"], "It just needs another go; remind it to write the file.")
    assert it["protected"] == ["resource"] and it["state"] == "proposed", "retry budgets change only with authorization"
    A.set_pin("4321")
    I.approve(lproj, it["id"], "one more try is fine", pin="4321")
    assert ticks(lproj, lambda: iv_state(lproj, it["id"]) == "resolved", 120)
    st = PS.load_state(lproj, "fx")
    assert "Write out.txt before finishing." in I.guidance(lproj, st["tasks"]["e"])
    assert phase(lproj, "e")["phase"] == "verified"


def test_unprotected_instructions_apply_without_a_pin_and_the_agent_sees_the_instruction(lproj, stub):
    start(lproj, [("e1", [t("g", "implementer", "echo ok > out.txt", checks=["file:out.txt"], attempts=1)])])
    PS.apply(lproj, plan([("e1", [t("g", "implementer", "echo ok > out.txt", checks=["file:out.txt"], attempts=1)])]).replace("MOCK_SH:", "MOCK_SH@2:"))
    assert ticks(lproj, lambda: blocked(lproj, "g"))
    stub.reply = {"summary": "Guide and resume.", "actions": [{"type": "guide", "text": "Use the second approach."}, {"type": "unblock"}]}
    it = I.request(lproj, phase(lproj, "g")["id"], "Try the second approach.")
    assert it["state"] == "approved" and it["protected"] == [] and it["approval"]["via"].startswith("policy")
    assert ticks(lproj, lambda: iv_state(lproj, it["id"]) == "resolved", 120)
    from research import taskprompt
    text, _ = taskprompt.assemble(lproj, PS.load_state(lproj, "fx")["tasks"]["g"], PS.load_state(lproj, "fx"), {"attempts": []}, "repair", None, None, SV.cfg(lproj))
    assert "Instruction from the researcher" in text and "Try the second approach." in text


# ------------------------------------------------------------------------------------------------ running agents, queueing, interruption
def test_queued_instruction_waits_for_the_running_attempt_and_interrupt_stops_it_safely(lproj, stub):
    start(lproj, [("e1", [t("s", "implementer", "sleep 25; echo ok > s.txt", checks=["file:s.txt"], attempts=2)])])
    PS.apply(lproj, plan([("e1", [t("s", "implementer", "echo ok > s.txt", checks=["file:s.txt"], attempts=2)])]).replace("MOCK_SH:", "MOCK_SH@2:"))
    assert ticks(lproj, lambda: bool((run_attempts(lproj).get("s") or [{}])[-1].get("execution")), 30)
    first = run_attempts(lproj)["s"][0]
    stub.reply = {"summary": "Restart with guidance.", "actions": [{"type": "guide", "text": "Skip the slow step."}, {"type": "retry"}]}
    sid = PS.load_state(lproj, "fx")["tasks"]["s"]
    q = I.request(lproj, sid, "Skip the slow step.")                     # queued: the agent keeps running
    for _ in range(4):
        SV.tick(lproj)
    assert I.load(lproj, q["id"])["state"] == "approved" and I.load(lproj, q["id"])["queued"] and phase(lproj, "s")["phase"] == "running"
    I.cancel(lproj, q["id"])
    i2 = I.request(lproj, sid, "Skip the slow step.", interrupt=True)    # interrupt: stop it safely now
    assert ticks(lproj, lambda: iv_state(lproj, i2["id"]) == "resolved", 120)
    at = run_attempts(lproj)["s"]
    assert at[0]["voided"] and at[0]["execution"] == first["execution"], "the interrupted attempt is kept as evidence, not counted"
    from research import execution as E
    assert E.show(lproj, first["execution"])["state"] not in ("running", "launched"), "the old agent was really stopped"
    assert len([a for a in at if not a.get("voided")]) == 1 and phase(lproj, "s")["phase"] == "verified"
    assert len({a["execution"] for a in at}) == len(at), "no execution was launched twice"


def test_a_second_open_intervention_for_the_same_task_is_refused_and_agents_cannot_instruct(lproj, stub):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stub.reply = {"summary": "s", "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}]}
    tid = phase(lproj, "w")["id"]
    I.request(lproj, tid, "one")
    with pytest.raises(ResearchError, match="already has an open intervention"):
        I.request(lproj, tid, "two")
    lproj.author = dict(AGENT)
    with pytest.raises(PolicyBlocked):
        I.request(lproj, tid, "agent says so")
    lproj.author = dict(HUMAN)


# ------------------------------------------------------------------------------------------------ fail closed, restart safety
@pytest.mark.parametrize("reply", ["I think you should rerun it.", {"actions": []}, {"actions": [{"type": "rm_rf"}]},
                                   {"actions": [{"type": "set_max_attempts", "value": 99}]}, {"actions": [{"type": "amend_task", "fields": {"secret_field": 1}}]},
                                   {"actions": [{"type": "change_frozen_test", "path": "tests/x.py", "reason": "short"}]}])
def test_unusable_planner_output_fails_closed_and_changes_nothing(lproj, stub, reply):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    body = lambda: PS.export(lproj, "fx").split("\n", 1)[1]
    before = body()
    stub.reply = reply
    it = I.request(lproj, phase(lproj, "w")["id"], "do something")
    assert it["state"] == "failed" and "could not plan" in it["history"][-1]["note"] and not it["applied"]
    assert body() == before


def test_frozen_test_changes_are_always_protected_and_never_edit_files(lproj, stub):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    open(os.path.join(lproj.root, "frozen_test.py"), "w").write("assert True\n")
    stub.reply = {"summary": "s", "actions": [{"type": "change_frozen_test", "path": "frozen_test.py", "reason": "the oracle tolerance was mis-specified"}, {"type": "unblock"}]}
    it = I.request(lproj, phase(lproj, "w")["id"], "the oracle is wrong")
    assert it["protected"] == ["frozen_test"] and it["state"] == "proposed"
    A.set_pin("4321")
    I.approve(lproj, it["id"], "yes, the oracle was wrong", pin="4321")
    ticks(lproj, lambda: iv_state(lproj, it["id"]) in ("verifying", "resolved", "failed"), 60)
    assert open(os.path.join(lproj.root, "frozen_test.py")).read() == "assert True\n", "the intervention never rewrites a frozen test itself"
    assert "authorized: modify frozen test frozen_test.py" in I.guidance(lproj, PS.load_state(lproj, "fx")["tasks"]["w"])


def test_a_restart_in_the_middle_of_implementing_applies_each_action_exactly_once(lproj, stub, monkeypatch):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stub.reply = {"summary": "s", "actions": [{"type": "amend_task", "fields": {"prompt": "Do the work.\nMOCK_SH: echo right > out.txt"}}, {"type": "unblock"}]}
    it = I.request(lproj, phase(lproj, "w")["id"], "fix the prompt and go")
    assert it["state"] == "approved"
    applied = []
    real = I._apply

    def crash_after_first(p, st, rs, it_, i, a, sv):
        applied.append(i)
        real(p, st, rs, it_, i, a, sv)
        if i == 0 and len(applied) == 1:
            raise KeyboardInterrupt("supervisor killed after action 0")
    monkeypatch.setattr(I, "_apply", crash_after_first)
    with pytest.raises(KeyboardInterrupt):
        SV.tick(lproj)
    # (the flag for action 0 was not saved because the kill came before it: the action itself is idempotent)
    monkeypatch.setattr(I, "_apply", real)
    spy = []
    real_apply = PS.apply
    monkeypatch.setattr(PS, "apply", lambda *a, **k: (spy.append(1), real_apply(*a, **k))[1])
    assert ticks(lproj, lambda: iv_state(lproj, it["id"]) == "resolved", 120)
    assert len(spy) <= 1, "the plan patch is re-applied at most once and is a no-op the second time"
    assert len(run_attempts(lproj)["w"]) == 2 and phase(lproj, "w")["phase"] == "verified", "one resumed attempt, no duplicates"
    assert list(I.load(lproj, it["id"])["applied"]) == ["0", "1"]


# ------------------------------------------------------------------------------------------------ Telegram + debug agent
@pytest.fixture
def tg(monkeypatch, tmp_path, lproj):
    from research import machine
    from research.chat import gateway
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg2"))
    fake = FakeTelegram()
    monkeypatch.setenv("RESEARCH_TELEGRAM_API", fake.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(gateway, "ensure_running", lambda p=None: {"action": "none"})        # no real gateway process in these tests
    notify.set_credentials(TOKEN, 777)
    lproj.config["notifications"] = {"halt_grace_seconds": 0, "auto_debug": True, "telegram": {"enabled": True}}
    lproj.save_config()
    machine.remember(lproj.root)
    from research.chat.telegram import TelegramChannel
    fake.deliver = lambda: asyncio.run(gateway.Gateway({"telegram": TelegramChannel()}, discover=False).pump_once())   # what the gateway does each tick
    yield fake
    gateway.stop()          # a supervisor started by the test starts a real gateway process; do not leave it behind
    fake.stop()


def join_threads():
    for th in list(notify._threads.values()):
        th.join(30)


def test_credentials_are_machine_local_validated_and_never_leak(lproj, tg):
    path = notify._path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and TOKEN not in open(lproj.rdir + "/config.yaml").read()
    with pytest.raises(ResearchError, match="bot token"):
        notify.set_credentials("not-a-token", 1)
    s = notify.status(lproj)
    assert s["ready"] and TOKEN not in json.dumps(s)


def test_a_halt_sends_one_message_with_the_debug_summary_and_options_then_reports_recovery(lproj, stub, tg):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stub.reply = {"summary": "The content check expects the word right but the task prints wrong. Most likely the check is mistaken.",
                  "options": [{"label": "Relax the check", "instruction": "The output only has to exist.", "class": "scientific"},
                              {"label": "Fix the task", "instruction": "Make the task print right.", "class": "implementation"}], "recommended": 1}
    notify.step(lproj)
    join_threads()
    assert tg.sent == [], "the supervisor writes to the outbox; only the gateway talks to Telegram"
    tg.deliver()
    assert tg.sent[0] == "Plan fx started." and len(tg.sent) == 2 and {m["chat"] for m in tg.messages} == {"777"}, "the plan start (default level) and then the one halt notice"
    hi = next(i for i, m in enumerate(tg.sent) if "HALTED" in m)
    msg = tg.sent[hi]
    assert "2. Fix the task" in json.dumps(tg.messages[hi]["markup"]) and "Pause plan" in json.dumps(tg.messages[hi]["markup"])
    assert "HALTED" in msg and "most likely the check is mistaken" in msg.lower() and "1. Relax the check [scientific]" in msg and "2. Fix the task [implementation] (recommended)" in msg and "/choose N" in msg
    assert TOKEN not in msg
    notify.step(lproj)
    join_threads()
    tg.deliver()
    assert len(tg.sent) == 2 and sum("HALTED" in m for m in tg.sent) == 1, "one notice per halt, not one per pass"
    rec = notify.latest_debug(lproj)
    assert rec["state"] == "done" and len(rec["options"]) == 2 and "opus" in rec["model"] or "default" in rec["model"]
    assert "read files and logs" in stub.calls[0][1] and "(read only" in stub.calls[0][1]


def test_telegram_commands_report_status_and_route_instructions_but_never_approve_protected_changes(lproj, stub, tg):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    tid = phase(lproj, "w")["id"]
    tg.say("/status")
    tg.say("/blocked")
    tg.say("/status", chat=999)                              # a stranger gets a pairing code and nothing else
    notify.poll(lproj)
    mine = [m for m in tg.messages if m["chat"] == "777"]
    texts = [m["text"] for m in mine]
    assert len(texts) == 2 and [m["text"].startswith("You are not paired yet") for m in tg.messages if m["chat"] == "999"] == [True]
    assert "HALTED" in texts[0] and "fx" in texts[0] and tid in texts[1] and "wrong" in texts[1].lower() or "attempt" in texts[1].lower()
    tg.say("/help")
    tg.say(f"/say {tid} The output only has to exist, drop the content check.")
    stub.reply = {"summary": "Relax the check.", "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}, {"type": "reverify"}]}
    tg.sent.clear()
    notify.poll(lproj)
    assert len(tg.sent) == 2 and "/say" in tg.sent[0]
    reply = tg.sent[1]
    assert "needs your approval with the PIN" in reply and f"research intervention approve INT-001" in reply
    its = I.list_all(lproj)
    assert len(its) == 1 and its[0]["source"] == "telegram" and its[0]["state"] == "proposed" and not its[0]["applied"], "chat cannot approve a protected change"
    tg.say("/pause")
    tg.sent.clear()
    notify.poll(lproj)
    assert "paused" in tg.sent[0]
    assert SV.status(lproj, "fx")["state"] == "paused"
    tg.say("/resume")
    tg.say("/nonsense")
    tg.sent.clear()
    notify.poll(lproj)
    # resuming starts a real supervisor, whose notify step may queue an alert that a real gateway process delivers meanwhile: order-free
    assert any("resumed" in m for m in tg.sent) and any("Unknown command" in m for m in tg.sent)


def test_choose_turns_a_debug_option_into_an_intervention_and_plain_text_goes_to_the_only_blocked_task(lproj, stub, tg):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stub.reply = {"summary": "Check mistaken.", "options": [{"label": "Guide", "instruction": "Print right instead.", "class": "implementation"}]}
    notify.run_debug(lproj, "fx")
    stub.reply = {"summary": "Guide the agent and retry.", "actions": [{"type": "guide", "text": "Print right."}, {"type": "unblock"}]}
    tg.say("/options")
    tg.say("/choose 1")
    notify.poll(lproj)
    its = I.list_all(lproj)
    assert len(its) == 1 and its[0]["instruction"] == "Print right instead." and its[0]["state"] == "approved", "unprotected: applied under the researcher's instruction"
    assert "Applied under your instruction" in tg.sent[-1]
    assert ticks(lproj, lambda: iv_state(lproj, its[0]["id"]) in ("resolved", "failed"), 120)


def test_halt_notice_without_the_debug_agent_and_no_telegram_means_no_network(lproj, stub, monkeypatch, tmp_path):
    from research.chat import telegram
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg3"))
    calls = []
    monkeypatch.setattr(telegram, "_api", lambda *a, **k: calls.append(a) or {"ok": True, "result": []})
    lproj.config["notifications"] = {"halt_grace_seconds": 0, "auto_debug": False, "telegram": {"enabled": False}}
    lproj.save_config()
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    notify.step(lproj)
    join_threads()
    assert calls == [] and not stub.calls, "disabled by default: no network and no agent run"
    stub.reply = {"summary": "Check mistaken.", "options": [{"label": "Fix", "instruction": "Print right.", "class": "implementation"}]}
    lproj.config["notifications"]["auto_debug"] = True              # diagnosis for the UI/CLI only: still no network
    lproj.save_config()
    os.unlink(os.path.join(lproj.path("supervisor", "debug"), "halt-fx.json"))
    notify.step(lproj)
    join_threads()
    assert calls == [] and notify.latest_debug(lproj)["state"] == "done"
    from research.chat import alerts
    assert not os.path.exists(alerts.outbox_path(lproj)), "chat off: nothing is queued either"


# ------------------------------------------------------------------------------------------------ CLI (real launcher)
def test_cli_surfaces_exist_and_fail_clearly(lproj, tmp_path):
    from research import localbin
    localbin.install(lproj.root)
    bin_ = localbin.launcher(lproj.root)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "RESEARCH_"))}
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg4")

    def run(*args):
        r = subprocess.run([bin_, *args], cwd=lproj.root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
        return r.returncode, r.stdout + r.stderr
    assert run("intervention", "list")[1].strip().endswith("no interventions")
    code, out = run("supervisor", "instruct")
    assert code != 0 and "instruct needs a task" in out
    code, out = run("supervisor", "instruct", "T-999", "--message", "hello")
    assert code != 0 and "T-999" in out or "not part of a managed plan" in out
    code, out = run("supervisor", "debug")
    assert code != 0 and "Nothing is blocked" in out
    code, out = run("telegram", "status")
    assert code == 0 and "no token" in out
    code, out = run("telegram", "setup", "--token", "bad", "--chat-id", "1")
    assert code != 0 and "bot token" in out
    assert run("telegram", "--help")[0] == 0


def test_cli_intervention_approve_asks_for_the_pin_in_a_real_terminal(lproj, stub, tmp_path, monkeypatch):
    from research import localbin
    from conftest import env_for, pty_run
    xdg = tmp_path / "xdgp"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    A.set_pin("4321")
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    stub.reply = {"summary": "Relax.", "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}, {"type": "reverify"}]}
    it = I.request(lproj, phase(lproj, "w")["id"], "relax the check")
    localbin.install(lproj.root)
    bin_ = localbin.launcher(lproj.root)
    code, prompts, out = pty_run([bin_, "intervention", "approve", it["id"], "--note", "agreed"], ["0000"], env_for(xdg), lproj.root)
    assert code != 0 and len(prompts) == 1 and "Incorrect PIN" in out and iv_state(lproj, it["id"]) == "proposed"
    code, prompts, out = pty_run([bin_, "intervention", "approve", it["id"], "--note", "agreed"], ["4321"], env_for(xdg), lproj.root)
    assert code == 0 and len(prompts) == 1 and "4321" not in out and iv_state(lproj, it["id"]) == "approved"


def test_ensure_running_starts_the_supervisor_for_telegram_or_a_running_plan_and_replaces_an_old_one(lproj, tg):
    try:
        assert not SV.daemon_status(lproj)["running"]
        r = notify.ensure_running(lproj)
        assert r["action"] == "start" and r["running"], "Telegram is enabled and ready, so the bot's process is started without another switch"
        pid = SV.daemon_status(lproj)["pid"]
        assert notify.ensure_running(lproj)["action"] == "none", "already running and current: nothing to do"
        d = json.load(open(SV._daemon_path(lproj)))
        d["version"] = "0.0.1"                                             # started by an older Research OS
        open(SV._daemon_path(lproj), "w").write(json.dumps(d))
        r = notify.ensure_running(lproj)
        assert r["action"] == "restart" and r["restarted"] and SV.daemon_status(lproj)["pid"] != pid and not SV.daemon_status(lproj)["restart_required"]
    finally:
        SV.daemon_stop(lproj)


def test_ensure_running_does_nothing_when_nothing_needs_a_supervisor(lproj, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
    assert notify.ensure_running(lproj)["action"] == "none" and not SV.daemon_status(lproj)["running"]
