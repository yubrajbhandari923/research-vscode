"""An overnight run must not spam chat: the supervisor's liveness, flap damping, the hourly cap, send backoff, and two projects side by side.

The incident: a supervisor sleeping out a provider limit wrote its heartbeat once a minute; the watchdog called that "not running", then "running again"
on the next pass, and `clear` forgot the cooldown. One pair of messages a minute, all night, until Discord revoked the bot token."""
import asyncio
import json
import os
import time

from research import supervisor as SV
from research.chat import alerts, gateway
from research.chat.gateway import Gateway
from research.store import Project
from test_chat_core import Clock, ch, clock, two  # noqa: F401  (fixtures)


def run(coro):
    return asyncio.run(coro)


def gw(ch, c):
    return Gateway({"fake": ch}, now=c, discover=False)


def test_a_sleeping_or_busy_supervisor_is_not_reported_down(ch, two, monkeypatch):
    """The exact overnight pattern: process alive, heartbeat age swinging between fresh and ~60 s (5 s poll). Nothing is sent."""
    state = {"age": 1.0}
    monkeypatch.setattr(SV, "all_runs", lambda p: [{"state": "running", "plan_key": "x"}])
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": True, "heartbeat_age_seconds": state["age"]})
    c = Clock(time.time())
    g = gw(ch, c)
    for i in range(40):                       # twenty minutes of 30-second checks
        state["age"] = 58.0 if i % 2 else 2.0
        c.t += 30
        run(g.watch_once())
        run(g.pump_once())
    assert not ch.delivered


def test_a_dead_supervisor_is_restarted_before_anyone_is_told(ch, two, monkeypatch):
    calls, alive = [], {"v": False}
    monkeypatch.setattr(SV, "all_runs", lambda p: [{"state": "running", "plan_key": "x"}])
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": alive["v"], "heartbeat_age_seconds": 500.0 if not alive["v"] else 1.0})

    def start(p, **kw):
        calls.append(p.root)
        return {"running": True, "pid": 1}
    monkeypatch.setattr(SV, "daemon_start", start)
    c = Clock(time.time())
    g = gw(ch, c)
    run(g.watch_once())
    run(g.pump_once())
    assert sorted(calls) == sorted(two) and not ch.delivered, "restarted silently, one start per project"
    for _ in range(5):                        # it keeps dying: three restarts an hour per project, then the researcher is told once
        c.t += 30
        run(g.watch_once())
        run(g.pump_once())
    assert len(calls) == 6
    down = [t for t in ch.texts() if "not working" in t]
    assert len(down) == 2 and all("could not be started again" in t for t in down), "one message per project, not one per check"
    alive["v"] = True
    c.t += 30
    run(g.watch_once())
    run(g.pump_once())
    assert len([t for t in ch.texts() if "running again" in t]) == 2


def test_auto_restart_can_be_switched_off(ch, two, monkeypatch):
    calls = []
    with Project(two[0], sync=False) as p:
        p.save_config({"supervisor.auto_restart": False})
    monkeypatch.setattr(SV, "all_runs", lambda p: [{"state": "running", "plan_key": "x"}])
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": False, "heartbeat_age_seconds": 500.0})
    monkeypatch.setattr(SV, "daemon_start", lambda p, **kw: calls.append(p.root) or {"running": True})
    g = gw(ch, Clock(time.time()))
    run(g.watch_once())
    assert calls == [two[1]]


def test_a_flapping_alert_is_muted_after_three_rounds(two, clock):
    with Project(two[0], sync=False) as p:
        sent = []
        for _ in range(60):                   # one raise/clear pair a minute for an hour
            sent.append(alerts.raise_(p, "supervisor_down", "supervisor_down", "error", "down"))
            sent.append(alerts.clear(p, "supervisor_down", "up"))
            clock.t += 60
        real = [r for r in sent if r]
        assert len(real) == 6, "three raises and three clears, then silence"
        assert "muted for an hour" in real[4]["text"]
        clock.t += 3700                       # quiet for an hour: it may speak again
        assert alerts.raise_(p, "supervisor_down", "supervisor_down", "error", "down")
        assert alerts.clear(p, "supervisor_down", "up")


def test_a_muted_alert_never_sends_its_clear_and_other_keys_are_untouched(two, clock):
    with Project(two[0], sync=False) as p:
        for _ in range(3):
            assert alerts.raise_(p, "k", "halt", "error", "halted")
            assert alerts.clear(p, "k", "moving")
        assert alerts.raise_(p, "k", "halt", "error", "halted") is None
        assert alerts.clear(p, "k", "moving") is None
        assert alerts.raise_(p, "other", "halt", "error", "another task halted", task="T-2")
        assert alerts.raise_(p, "k", "halt", "error", "you asked", force=True), "an answer the person asked for is never muted"


def test_hourly_cap_holds_back_noise_but_never_a_decision(ch, two):
    c = Clock(time.time())
    g = gw(ch, c)
    with Project(two[0], sync=False) as p:
        p.save_config({"notifications.alerts.max_per_hour": 5, "notifications.level": "high"})
    for i in range(40):                       # one message every 30 s: slower than storm control, far more than anyone wants
        with Project(two[0], sync=False) as p:
            alerts.raise_(p, f"k{i}", "task_started", "info", f"task {i} started", task=f"T-{i}")
        c.t += 30
        run(g.pump_once())
    texts = ch.texts()
    assert len([t for t in texts if t.endswith("started")]) == 5
    assert len([t for t in texts if "held back" in t]) == 1, "one notice, not one per dropped message"
    with Project(two[0], sync=False) as p:
        alerts.raise_(p, "d", "decision", "warn", "DR-1 needs an answer", decision="DR-1")
    run(g.pump_once())
    assert "DR-1 needs an answer" in ch.texts()[-1]
    with Project(two[1], sync=False) as p:    # the other project has its own allowance
        alerts.raise_(p, "x", "halt", "error", "beta halted")
    run(g.pump_once())
    assert "beta halted" in ch.texts()[-1]
    c.t += 3700
    with Project(two[0], sync=False) as p:
        alerts.raise_(p, "later", "halt", "error", "an hour later")
    run(g.pump_once())
    assert "an hour later" in ch.texts()[-1]


def test_a_failing_platform_is_backed_off_not_hammered(ch, two):
    with Project(two[0], sync=False) as p:
        alerts.raise_(p, "k", "halt", "error", "halted")
    c = Clock(time.time())
    g = gw(ch, c)
    ch.fail = 1000
    tries = []
    orig = ch.deliver

    async def counting(*a, **kw):
        tries.append(c.t)
        return await orig(*a, **kw)
    ch.deliver = counting
    for _ in range(300):                      # ten minutes of 2-second ticks
        run(g.pump_once())
        c.t += 2
    assert len(tries) <= 7, f"{len(tries)} attempts in ten minutes: a revoked or rate-limited bot must not be retried every tick"


def test_two_projects_keep_separate_alert_state_and_cursors(ch, two, clock):
    a, b = two
    with Project(a, sync=False) as p:
        for _ in range(4):
            alerts.raise_(p, "supervisor_down", "supervisor_down", "error", "down")
            alerts.clear(p, "supervisor_down", "up")
    with Project(b, sync=False) as p:
        assert alerts.raise_(p, "supervisor_down", "supervisor_down", "error", "down"), "alpha's flapping does not mute beta"
    g = gw(ch, Clock(time.time()))
    run(g.pump_once())
    texts = ch.texts()
    assert len([t for t in texts if t.startswith("[alpha]")]) + len([t for t in texts if "alerts in the last minute" in t and "[alpha]" in t]) >= 1
    assert any(t.startswith("[beta]") and "down" in t for t in texts)
    n = len(ch.delivered)
    assert run(gw(ch, Clock(time.time())).pump_once()) == 0 and len(ch.delivered) == n, "a restarted gateway re-sends nothing for either project"


def test_heartbeat_follows_the_beat_file_not_only_the_pass(tmp_path):
    Project.init(str(tmp_path), name="x", goal="g").close()
    with Project(str(tmp_path), sync=False) as p:
        os.makedirs(SV.sdir(p), exist_ok=True)
        with open(SV._daemon_path(p), "w") as f:
            json.dump({"pid": os.getpid(), "heartbeat": time.time() - 400, "pass_started": time.time() - 400}, f)
        assert SV.daemon_status(p)["heartbeat_age_seconds"] > 300
        with open(SV.sdir(p, "daemon.beat"), "w") as f:
            f.write("x")
        d = SV.daemon_status(p)
        assert d["running"] and d["heartbeat_age_seconds"] < 5 and d["pass_seconds"] > 300, "alive and in one long pass: not down"
