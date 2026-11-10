"""A halt diagnosis (and the automatic investigator) that is refused by a provider's usage limit is a wait with a time, not a failure: it runs again by itself
once the reset has passed, on the same clock and with the same limit message parsing the supervisor's own task waits use. The agent CLI is the only stand-in."""
import json
import os
import time
from datetime import datetime, timezone

import pytest

from research import agents, intervene as I, notify, supervisor as SV
from research.providers import limits as L
from test_intervention import WRONG, blocked, start, ticks  # noqa: F401
from test_supervisor import lproj, phase  # noqa: F401  (lproj is a fixture)
from test_usage_limit import aim

HERE = os.path.dirname(__file__)


def claude_row(cid):
    return next(r for r in map(json.loads, open(os.path.join(HERE, "fixtures", "limits", "claude.jsonl"), encoding="utf-8")) if r["id"] == cid)


class Agent:
    """Stands in for `agents.dispatch` + `agents.run_headless`: refuses with a real Claude limit message while `limited`, else answers with a diagnosis."""

    def __init__(self, row):
        self.row, self.limited, self.calls = row, True, 0

    def dispatch(self, p, **kw):
        return {"available": True, "headless": True, "argv": ["x"], "env": {}, "profile": "claude", "model": None, "role": "planner"}

    def run_headless(self, p, d, timeout=None):
        self.calls += 1
        if self.limited:
            return {"exit_code": self.row["exit"], "timed_out": False, "result": "", "stderr": self.row["stderr"], "raw_stdout": self.row["stdout"],
                    "raw_stderr": self.row["stderr"], "usage": None}
        text = json.dumps({"summary": "The content check expects 'right' but the task prints 'wrong'.", "options": []})
        return {"exit_code": 0, "timed_out": False, "result": text, "stderr": "", "raw_stdout": text, "raw_stderr": "", "usage": None}


@pytest.fixture
def halted(lproj, monkeypatch):
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    lproj.config["notifications"] = {"halt_grace_seconds": 0, "auto_debug": True}
    lproj.save_config()
    row = claude_row("c4")
    now, info = aim(row, "claude")
    ag = Agent(row)
    monkeypatch.setattr(agents, "dispatch", ag.dispatch)
    monkeypatch.setattr(agents, "run_headless", ag.run_headless)
    yield lproj, ag, info
    L.set_clock(None)


def join():
    for th in list(notify._threads.values()):
        th.join(30)


def test_a_diagnosis_refused_by_a_usage_limit_waits_and_runs_again_after_the_reset(halted):
    p, ag, info = halted
    notify.step(p)
    join()
    rec = notify.latest_debug(p)
    assert rec["state"] == "waiting_provider" and ag.calls == 1, "a limit is not a failed diagnosis"
    assert rec["resume_at"] == pytest.approx(info.reset_at.timestamp() + 120) and "usage limit" in rec["error"]
    h = SV.status(p, "fx")["health"]
    msg = notify.message_for(rec, {**h, "plan": "fx"})
    assert "diagnosis is waiting" in msg and "runs again by itself" in msg and "debug agent failed" not in msg
    notify.step(p)
    join()
    assert ag.calls == 1, "not before the reset, however often the supervisor looks"
    with pytest.raises(I.ProviderLimited):
        I.run_assistant(p, "x", "y")
    assert ag.calls == 1, "a known limit is not probed with another call"
    ag.limited = False
    L.set_clock(lambda: datetime.fromtimestamp(rec["resume_at"] + 1, timezone.utc))
    notify.step(p)
    join()
    done = notify.latest_debug(p)
    assert done["state"] == "done" and "expects 'right'" in done["summary"] and ag.calls == 2, "resumed by itself, once"
    notify.step(p)
    join()
    assert ag.calls == 2, "and not repeated"


def test_a_limit_that_cannot_be_waited_out_is_reported_as_a_failure(halted, monkeypatch):
    p, ag, _ = halted
    err = I.ProviderLimited("claude refused (credit limit); add credit", time.time() + 3600, "claude", waitable=False)
    monkeypatch.setattr(I, "run_assistant", lambda *a, **k: (_ for _ in ()).throw(err))
    rec = notify.run_debug(p, "fx")
    assert rec["state"] == "failed" and "credit" in rec["error"]


def test_the_automatic_investigator_is_re_armed_after_the_reset(halted, monkeypatch):
    p, ag, info = halted
    from research import investigator
    calls = []

    def limited(*a, **k):
        calls.append(1)
        raise I.ProviderLimited("claude is at its usage limit", info.reset_at.timestamp() + 120, "claude")
    monkeypatch.setattr(investigator, "maybe_auto", limited)
    monkeypatch.setattr(notify, "_auto_investigator", lambda p: True)
    p.config["notifications"]["auto_debug"] = False
    p.save_config()
    notify.step(p)
    time.sleep(1.5)
    marker = notify._read(notify._dir(p, "halt-fx.json"))
    assert marker["investigate_after"] == pytest.approx(info.reset_at.timestamp() + 120) and len(calls) == 1
    notify.step(p)
    time.sleep(0.5)
    assert len(calls) == 1, "waits for the reset"
    L.set_clock(lambda: datetime.fromtimestamp(marker["investigate_after"] + 1, timezone.utc))
    notify.step(p)
    time.sleep(1.5)
    assert len(calls) == 2, "armed again after the reset"
