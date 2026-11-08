"""H.5: `supervisor.investigator.auto` -- the halt watcher (notify.step) runs the investigator once for a task that exhausted its retries,
on a worker thread, under max_per_plan / budget_usd; protected parts still wait for the PIN. Scripted fake investigator binary, no model."""
import threading

import pytest

from research import intervene as I, investigator as INV, notify, supervisor as SV
from research.store import Project
from test_investigator import ADD, inv  # noqa: F401
from test_supervisor import lproj  # noqa: F401


def watch(h, **inv_cfg):
    h.p.config["supervisor"] = {**(h.p.config.get("supervisor") or {}), "investigator": {"auto": True, "max_per_plan": 3, "budget_usd": 5, **inv_cfg}}
    h.p.config["notifications"] = {**(h.p.config.get("notifications") or {}), "halt_grace_seconds": 0}
    h.p.save_config()


def step(h):
    with Project(h.p.root, sync=False) as p:      # as the daemon does: a fresh handle per pass
        notify.step(p)
    for t in list(notify._threads.values()) + [t for t in threading.enumerate() if t.name.startswith("Thread")]:
        if t is not threading.current_thread():
            t.join(30)


def test_off_by_default_nothing_runs(inv):
    inv.reply({"understood": "u", "summary": "s", "actions": [{"type": "note", "text": "n"}]})
    inv.p.config["notifications"] = {"halt_grace_seconds": 0}
    inv.p.save_config()
    step(inv)
    assert inv.runs() == 0


def test_a_halt_triggers_one_investigation_once(inv):
    inv.reply({"understood": "the check is wrong", "summary": "add a diagnostic", "actions": [ADD]})
    watch(inv)
    step(inv)
    assert inv.runs() == 1
    step(inv)
    step(inv)
    assert inv.runs() == 1                                                  # the halt marker and (task, fingerprint) both hold
    log = INV._load(inv.p)["runs"]
    assert len(log) == 1 and log[0]["task"] == inv.tid and log[0]["fingerprint"] and log[0]["status"] == "done"
    acts = [r["action"] for r in inv.p.q("SELECT action FROM events WHERE entity_id=?", (inv.tid,))]
    assert {"investigator_auto", "investigator_started", "investigator_outcome"} <= set(acts)


def test_max_per_plan_zero_calls_nothing(inv):
    inv.reply({"understood": "u", "summary": "s", "actions": [{"type": "note", "text": "n"}]})
    watch(inv, max_per_plan=0)
    step(inv)
    assert inv.runs() == 0
    assert any(r["action"] == "investigator_skipped" for r in inv.p.q("SELECT action FROM events WHERE entity_id=?", (inv.tid,)))


def test_budget_used_up_calls_nothing_more(inv):
    inv.reply({"understood": "u", "summary": "s", "actions": [{"type": "note", "text": "n"}]}, cost=9)
    watch(inv, budget_usd=1.0)
    INV.maybe_auto(inv.p, inv.tid, "other-fingerprint")                      # spends 9 of 1
    assert inv.runs() == 1
    step(inv)
    assert inv.runs() == 1


def test_protected_actions_wait_for_the_pin_and_nothing_is_approved(inv):
    inv.reply({"understood": "needs more attempts", "summary": "raise the retry budget", "actions": [{"type": "set_max_attempts", "value": 9}]})
    watch(inv)
    step(inv)
    its = I.list_all(inv.p)
    assert len(its) == 1 and its[0]["state"] == "proposed" and its[0]["protected"] and not its[0].get("approval")


def test_the_supervisor_tick_never_calls_the_investigator(inv, monkeypatch):
    watch(inv)
    monkeypatch.setattr(INV, "maybe_auto", lambda *a, **k: pytest.fail("called from the tick"))
    monkeypatch.setattr(INV, "investigate", lambda *a, **k: pytest.fail("called from the tick"))
    SV.tick(inv.p)
