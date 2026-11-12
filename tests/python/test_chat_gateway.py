"""WP14a: the gateway with FakeChannel: outbox delivery (at-least-once, no duplicates across restart), storm folding, watchdog, digest,
button expiry, single instance."""
import asyncio
import os
import threading
import time

import pytest

from research import daemonlib, supervisor as SV
from research.chat import actions, alerts, commands, gateway, gdir, read_json
from research.chat.gateway import Gateway
from research.store import Project
from test_chat_core import Clock, ch, clock, two  # noqa: F401  (fixtures)


def run(coro):
    return asyncio.run(coro)


def gw_for(ch, clock=None):
    return Gateway({"fake": ch}, now=clock or time.time, discover=False)


def test_outbox_delivery_with_buttons_prefix_and_no_duplicates_across_restart(ch, two):
    a, b = two
    with Project(a, sync=False) as p:
        alerts.raise_(p, "k", "approval_needed", "warn", "Proposal PROP-1 needs you",
                      actions=[{"label": "Reject", "op": "chat_cmd_reject", "params": {"kind": "proposal", "id": "PROP-1"}, "style": "danger"},
                               {"label": "Approve on my computer", "text": "research proposal approve PROP-1"}])
    g = gw_for(ch)
    assert run(g.pump_once()) == 1
    m = ch.delivered[0]
    assert m.text.startswith("[alpha] ") and "PROP-1" in m.text and [x.label for x in m.buttons] == ["Reject", "Approve on my computer"]
    assert len(m.buttons[0].token) == 12 and actions.peek(m.buttons[0].token)["params"]["id"] == "PROP-1"
    assert run(g.pump_once()) == 0 and run(gw_for(ch).pump_once()) == 0, "a restarted gateway does not re-send"
    refs = read_json(gdir("refs.json"))
    assert refs["c:1"]["expires"] and refs["c:1"]["project"] == a, "the card is remembered so its buttons can expire and replies can be routed"


def test_a_failed_delivery_is_retried_not_lost_and_sent_once(ch, two):
    with Project(two[0], sync=False) as p:
        alerts.raise_(p, "k", "halt", "error", "halted")
        alerts.raise_(p, "k2", "halt", "error", "halted again", task="T-2")
    ch.fail = 1
    c = Clock(time.time())
    g = gw_for(ch, c)
    assert run(g.pump_once()) == 0 and not ch.delivered
    assert run(g.pump_once()) == 0 and not ch.delivered, "a failed send backs off: it is not retried on the very next tick"
    c.t += 11
    assert run(g.pump_once()) == 2 and [m.text.split("] ")[-1][:6] for m in ch.delivered] == ["halted", "halted"]
    assert run(g.pump_once()) == 0


def test_storm_control_folds_a_burst_into_one_summary(ch, two):
    with Project(two[0], sync=False) as p:
        for i in range(8):
            alerts.raise_(p, f"k{i}", "task_failed", "error", f"task {i} failed", task=f"T-{i}")
    assert run(gw_for(ch).pump_once()) == 1
    assert "8 alerts in the last minute" in ch.delivered[0].text and "task_failed" in ch.delivered[0].text


def test_supervisor_down_watchdog_raises_from_a_stale_heartbeat_and_clears(ch, two, monkeypatch):
    state = {"age": 999.0, "running": True}
    monkeypatch.setattr(SV, "all_runs", lambda p: [{"state": "running", "plan_key": "x"}])
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": state["running"], "heartbeat_age_seconds": state["age"]})
    g = gw_for(ch)
    run(g.watch_once())
    run(g.pump_once())
    assert any("supervisor is not working" in t and "silent" in t for t in ch.texts())
    n = len(ch.delivered)
    run(g.watch_once())
    run(g.pump_once())
    assert len(ch.delivered) == n, "the same condition is not announced twice"
    state["age"] = 1.0
    run(g.watch_once())
    run(g.pump_once())
    assert any("running again" in t for t in ch.texts())


def test_digest_is_sent_once_a_day_and_silent_when_nothing_changed(ch, two, monkeypatch):
    t0 = time.time()
    c = Clock(t0)
    with Project(two[0], sync=False) as p:
        p.save_config({"notifications.digest.at": "00:00"})
    g = gw_for(ch, c)
    run(g.watch_once())
    assert any("Since the last digest" in t for t in ch.texts()), "first digest reports the recent events"
    n = len(ch.delivered)
    run(g.watch_once())
    assert len(ch.delivered) == n, "once a day"
    c.t += 86400 + 5
    run(g.watch_once())
    assert len(ch.delivered) == n, "nothing happened since the last digest: no message"
    with Project(two[0], sync=False) as p:
        p.save_config({"notifications.digest.silent_if_unchanged": False})
    c.t += 86400
    run(g.watch_once())
    assert len(ch.delivered) == n + 1 and "Nothing changed" in ch.delivered[-1].text


def test_approval_buttons_expire_but_nothing_is_decided(ch, two):
    with Project(two[0], sync=False) as p:
        alerts.raise_(p, "k", "approval_needed", "warn", "needs you", actions=[{"label": "Reject", "op": "chat_cmd_reject", "params": {"kind": "proposal", "id": "P"}}])
    c = Clock(time.time())
    g = gw_for(ch, c)
    run(g.pump_once())
    run(g.watch_once())
    assert not ch.edits
    c.t += 25 * 3600
    run(g.watch_once())
    assert [r for r, m in ch.edits] == ["c:1"] and not ch.edits[0][1].buttons and not ch.edits[0][1].text, "buttons removed, text kept"
    n = len(ch.edits)
    run(g.watch_once())
    assert len(ch.edits) == n


def test_only_one_gateway_runs_per_machine_and_ensure_never_raises(ch, two, monkeypatch):
    res = []
    with daemonlib.lock(gdir("lock")):
        t = threading.Thread(target=lambda: res.append(run(Gateway({}, discover=False).run())))
        t.start()
        t.join(10)
    assert res == [3], "a second gateway exits at once"
    monkeypatch.setattr(gateway, "start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    assert gateway.ensure_running()["action"] == "none"


def test_gateway_loop_heartbeat_and_stop(ch, two):
    async def go():
        g = gw_for(ch)
        t = asyncio.ensure_future(g.run(tick=0.05))
        await asyncio.sleep(0.4)
        hb = daemonlib.read_heartbeat(gdir("daemon.json"))
        t.cancel()
        return hb
    hb = run(go())
    assert hb["alive"] and hb["pid"] == os.getpid() and not hb["stale"]
