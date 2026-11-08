"""H.10: what the supervisor does when a provider refuses because of a usage/rate/overload limit. A fake provider binary
(tests/python/fakes/limit_agent.py) fails with each provider's message from tests/python/fixtures/limits/*.jsonl while a flag file exists, then does the
work once the test removes it; the clock is the fake one of providers.limits. Parser edge cases are test_provider_limits.py's."""
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from research import decisions as D, notify, planspec as PS, providers, supervisor as SV
from research.chat import alerts
from research.providers import limits as L
from research.store import Project
from test_supervisor import phases, proj, run_until, task_yaml, yaml_plan  # noqa: F401

HERE = os.path.dirname(__file__)
FAKE = os.path.join(HERE, "fakes", "limit_agent.py")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def fixtures(skip_kinds=("credit",)):
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "fixtures", "limits", "*.jsonl"))):
        prov = os.path.basename(f)[:-6]
        for r in map(json.loads, open(f, encoding="utf-8")):
            if r["expect"] and r["expect"]["kind"] not in skip_kinds:
                out.append(pytest.param(prov, r, id=f"{prov}-{r['id']}-{r['label']}"))
    return out


class Env:
    """A project whose agent profiles are the fake binary, a flag file that means 'the provider is limited', and the fake clock."""

    def __init__(self, p, tmp_path, monkeypatch):
        self.p, self.tmp, self.flag, self.log = p, tmp_path, str(tmp_path / "limited.flag"), str(tmp_path / "launches.log")
        self.mp = monkeypatch
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        self.clock(NOW)

    def clock(self, now):
        self.now = now
        L.set_clock(lambda: now)

    def profile(self, name, provider, case=None, **extra):
        args = [FAKE, "--flag", self.flag, "--log", self.log] + (["--case", case] if case else [])
        exe = self.tmp / "bin" / provider              # named like the real CLI: the supervisor derives the provider (session ids, resume) from the command
        if not exe.exists():
            exe.parent.mkdir(exist_ok=True)
            exe.symlink_to(FAKE)
        self.p.config["agents"]["profiles"][name] = {"command": str(exe), "args": args, "provider": provider, **extra}
        self.p.save_config()

    def aim(self, prov, cid):
        """Profile `lim` fails with fixture `prov:cid`, with the clock set so its reset is a few hours away."""
        row = next(r for r in map(json.loads, open(os.path.join(HERE, "fixtures", "limits", prov + ".jsonl"), encoding="utf-8")) if r["id"] == cid)
        self.clock(aim(row, prov)[0])
        self.profile("lim", prov, f"{prov}:{cid}")

    def usage_limit(self, **kw):
        self.p.config["supervisor"]["usage_limit"] = {"on_limit": "wait", **kw}
        self.p.save_config()

    def plan(self, tasks="a"):
        ys = [task_yaml(k, k.upper(), "true", checks=["file:done.txt"]).replace("profile: fake", f"profile: {prof}") for k, prof in
              (t.split(":") if ":" in t else (t, "lim") for t in tasks.split(","))]
        PS.apply(self.p, yaml_plan(ys))
        SV.plan_run(self.p, "demo-plan", start_daemon=False)

    def limit(self):
        open(self.flag, "w").write("x")

    def lift(self):
        os.path.exists(self.flag) and os.unlink(self.flag)

    def launches(self):
        return [json.loads(l) for l in open(self.log)] if os.path.exists(self.log) else []

    def rs(self):
        return SV.load_run(self.p, PS.load_state(self.p, "demo-plan")["plan_id"])

    def entry(self, key="a"):
        return self.rs()["tasks"][PS.load_state(self.p, "demo-plan")["tasks"][key]]

    def status(self):
        return SV.status(self.p, "demo-plan")

    def settle(self, phase, key="a", timeout=60):
        assert run_until(self.p, lambda: phases(self.p).get(key) == phase, timeout), (phases(self.p), self.entry(key).get("reason"))

    def advance_to_resume(self, key="a", extra=1):
        self.clock(datetime.fromtimestamp(self.entry(key)["resume_at"] + extra, timezone.utc))


@pytest.fixture
def env(proj, tmp_path, monkeypatch):
    e = Env(proj, tmp_path, monkeypatch)
    yield e
    L.set_clock(None)
    monkeypatch.undo()
    time.tzset()


def aim(row, prov):
    """Move the fake clock so the fixture's reset (absolute or relative) is a few hours away, as a real wait would be; return its LimitInfo."""
    now = datetime.strptime(row["now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if row.get("now") else NOW
    L.set_clock(lambda: now)
    info = providers.limit_info(prov, row["stdout"], row["stderr"], row["exit"])
    if info.reset_at and not (now < info.reset_at < now + timedelta(hours=6)):
        now = info.reset_at - timedelta(hours=2)
        L.set_clock(lambda: now)
        info = providers.limit_info(prov, row["stdout"], row["stderr"], row["exit"])
    return now, info


# ------------------------------------------------------------------------------------------------ wait and auto-resume, every fixture
@pytest.mark.parametrize("prov,row", fixtures())
def test_each_providers_limit_message_waits_then_resumes_by_itself(env, prov, row):
    now, info = aim(row, prov)
    env.clock(now)
    env.profile("lim", prov, f"{prov}:{row['id']}")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    e, st = env.entry(), env.status()
    assert len(env.launches()) == 1 and e["attempts"][0]["voided"].startswith("provider error") and env.rs()["budgets"]["attempts"] == 0
    until = float(e["resume_at"])
    if info.reset_at:            # known reset: exactly reset + the buffer
        assert until == pytest.approx(info.reset_at.timestamp() + 120)
    else:                        # unknown: the first backoff step (long for usage, short for rate/overload)
        assert until - now.timestamp() == pytest.approx(60 * (1 if info.kind in ("rate", "overload") else 15))
    assert st["waiting_provider"]["provider"] == prov and st["health"]["state"] == "waiting_for_provider"
    SV.tick(env.p)
    assert len(env.launches()) == 1                               # not launched before the reset, however often the supervisor looks
    env.lift()
    env.clock(datetime.fromtimestamp(until + 1, timezone.utc))
    env.settle("verified")
    assert len(env.launches()) == 2 and env.rs()["budgets"]["attempts"] == 1
    actions = [r["action"] for r in env.p.q("SELECT action FROM events WHERE action LIKE 'supervisor_%limit%' OR action='supervisor_waiting_provider'")]
    assert "supervisor_waiting_provider" in actions and "supervisor_resumed_after_limit" in actions


def test_halt_is_the_default_and_nothing_waits(env):
    env.profile("lim", "claude", "claude:c4")
    env.limit()
    env.plan()
    env.settle("attention")
    e = env.entry()
    assert "provider error" in e["attempts"][0]["voided"] and "waiting_provider" not in e and env.rs()["budgets"]["attempts"] == 0
    SV.tick(env.p)
    assert len(env.launches()) == 1 and env.status()["waiting_provider"] is None


def test_halt_mode_reads_stderr_now_too(env):
    env.profile("lim", "codex", "codex:x1")                        # Codex prints its limit on stderr only
    env.limit()
    env.plan()
    env.settle("attention")
    assert "voided" in env.entry()["attempts"][0] and len(env.entry()["attempts"]) == 1


# ------------------------------------------------------------------------------------------------ other providers, restart, pause
def test_tasks_on_other_providers_keep_running_while_one_waits(env):
    env.aim("claude", "c5")
    env.profile("fine", "codex")
    env.usage_limit()
    env.limit()
    env.plan("a:lim,b:fine")
    assert run_until(env.p, lambda: phases(env.p).get("b") == "verified" and phases(env.p).get("a") == "waiting_provider", 90), phases(env.p)
    assert env.status()["waiting_provider"]["tasks"] == 1


def test_restart_during_the_wait_keeps_the_state_and_resumes_on_time(env):
    env.aim("claude", "c5")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    root, until = env.p.root, env.entry()["resume_at"]
    env.p.close()
    with Project(root) as p2:                                     # a new process reading only what is on disk
        env.p = p2
        assert SV.limited_until(p2, "lim") == until and env.status()["waiting_provider"]["until_ts"] == until
        assert phases(p2)["a"] == "waiting_provider"
        env.lift()
        env.advance_to_resume()
        env.settle("verified")


def test_pause_wins_over_the_wait_and_resume_continues_it(env):
    env.aim("claude", "c5")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    SV.plan_pause(env.p, "demo-plan")
    env.lift()
    env.advance_to_resume()
    SV.tick(env.p)
    assert len(env.launches()) == 1                               # paused: nothing relaunches
    SV.plan_resume(env.p, "demo-plan")
    env.settle("verified")


def test_wait_time_is_not_plan_working_time(env):
    env.aim("claude", "c5")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    w = env.rs()["budgets"]["wall_seconds"]
    rs = env.rs()
    rs["last_tick"] -= 50
    SV.save_run(env.p, rs)
    SV.tick(env.p)
    assert env.rs()["budgets"]["wall_seconds"] == w


# ------------------------------------------------------------------------------------------------ unknown reset, backoff, long waits
def test_unknown_reset_backs_off_at_growing_intervals_and_the_last_repeats(env):
    env.profile("lim", "claude", "claude:c1")                     # usage limit, no reset time
    env.usage_limit(unknown_reset_backoff_minutes=[15, 30, 60])
    env.limit()
    env.plan()
    gaps = []
    for i in range(4):
        env.settle("waiting_provider" if i == 0 else "waiting_provider", timeout=60)
        gaps.append(round((env.entry()["resume_at"] - env.now.timestamp()) / 60))
        env.advance_to_resume()
        if i < 3:
            assert run_until(env.p, lambda: len(env.launches()) == i + 2 and phases(env.p)["a"] == "waiting_provider" and env.entry()["resume_at"] > env.now.timestamp(), 60)
    assert gaps == [15, 30, 60, 60]
    assert env.rs()["budgets"]["attempts"] == 0                   # four refused relaunches cost nothing


def test_rate_and_overload_use_the_short_backoff(env):
    env.profile("lim", "claude", "claude:c9")                     # 529 overloaded, no reset
    env.usage_limit(short_backoff_minutes=[1, 2, 5])
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    assert round((env.entry()["resume_at"] - env.now.timestamp()) / 60) == 1


def test_a_reset_beyond_max_wait_asks_instead_of_waiting_unattended(env):
    env.profile("lim", "claude", "claude:c4")                     # weekly limit, resets tomorrow 11am UTC: 23 hours away
    env.usage_limit(max_wait_hours=8)
    env.limit()
    env.plan()
    env.settle("attention")
    ds = D.list_all(env.p)
    assert len(ds) == 1 and {o["key"] for o in ds[0]["options"]} >= {"wait", "stop"} and "waiting_provider" not in env.entry()
    assert not any(o["protected"] for o in ds[0]["options"])      # nothing here needs the PIN
    D.answer(env.p, ds[0]["id"], key="wait")
    assert phases(env.p)["a"] == "waiting_provider" and env.status()["waiting_provider"]["tasks"] == 1
    env.lift()
    env.advance_to_resume()
    env.settle("verified")


def test_stop_option_pauses_the_plan_and_switch_is_offered_when_another_provider_is_installed(env):
    env.profile("lim", "claude", "claude:c4")
    env.profile("codex", "codex")
    env.usage_limit(max_wait_hours=8)
    env.limit()
    env.plan()
    env.settle("attention")
    d = D.list_all(env.p)[0]
    assert "switch" in {o["key"] for o in d["options"]}
    D.answer(env.p, d["id"], key="stop")
    assert SV.load_run(env.p, PS.load_state(env.p, "demo-plan")["plan_id"])["state"] == "paused"


def test_credit_balance_is_not_waited_out_blindly(env):
    env.profile("lim", "claude", "claude:c14")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("attention")
    assert D.list_all(env.p)


# ------------------------------------------------------------------------------------------------ fallback_then_wait
def _fallback_ready(env, **codex):
    env.aim("claude", "c5")
    env.profile("codex", "codex", **codex)
    env.p.config["agents"]["handoff"] = {"enabled": True, "on_provider_unavailable": "try_next", "fallback_order": ["codex"]}
    env.usage_limit(on_limit="fallback_then_wait")
    env.limit()


def test_fallback_then_wait_switches_first(env):
    _fallback_ready(env)
    env.plan()
    env.settle("verified")
    e = env.entry()
    assert e["handoffs"][-1]["to"] == "codex" and "waiting_provider" not in e and env.rs()["budgets"]["attempts"] == 1


def test_fallback_never_picks_a_model_orchestration_does_not_allow(env):
    _fallback_ready(env, model="not-allowed")
    env.p.config["orchestration"] = {**(env.p.config.get("orchestration") or {}), "available": ["something-else"]}
    env.p.save_config()
    env.plan()
    env.settle("waiting_provider")
    assert not env.entry().get("handoffs")


# ------------------------------------------------------------------------------------------------ detection details
def test_exit_zero_run_that_printed_a_limit_is_voided_not_verified(env):
    env.aim("agy", "a1")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    a = env.entry()["attempts"][0]
    assert a["outcome"] == "completed" and a["exit_code"] == 0 and a["voided"] and "verification" not in a


def test_claude_weekly_limit_with_empty_stdout_is_read_from_the_transcript(env):
    env.mp.setattr(SV, "_claude_projects_dir", lambda: str(env.tmp / "claude-projects"))
    env.profile("lim", "claude", "claude:empty")
    env.p.config["agents"]["profiles"]["lim"]["args"] += ["--transcript", "You've hit your weekly limit · resets 5pm (UTC)", "--projects", str(env.tmp / "claude-projects")]
    env.p.save_config()
    env.clock(datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc))
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    assert env.entry()["resume_at"] == pytest.approx(datetime(2026, 11, 7, 17, 2, tzinfo=timezone.utc).timestamp())


def test_empty_stdout_failure_without_a_transcript_is_an_ordinary_failure(env):
    env.mp.setattr(SV, "_claude_projects_dir", lambda: str(env.tmp / "nowhere"))
    env.profile("lim", "claude", "claude:empty")
    env.usage_limit()
    env.limit()
    env.plan()
    assert run_until(env.p, lambda: len(env.entry()["attempts"]) >= 1 and env.entry()["attempts"][0].get("category") == "agent_process_failure", 60)
    assert "waiting_provider" not in env.entry()


def test_the_relaunch_continues_the_session_where_the_adapter_supports_it(env):
    env.mp.setattr(providers, "native_resume_supported", lambda kind, cmd: True)
    env.aim("claude", "c2")                                        # carries session_id abc
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    env.lift()
    env.advance_to_resume()
    env.settle("verified")
    second = env.launches()[1]
    assert "--resume" in second and second[second.index("--resume") + 1] == "abc"


# ------------------------------------------------------------------------------------------------ alerts
def _outbox(p):
    path = alerts.outbox_path(p)
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def test_one_alert_when_waiting_starts_and_one_when_it_resumes(env):
    env.mp.setattr(notify, "chat_enabled", lambda p: True)
    env.aim("claude", "c5")
    env.usage_limit()
    env.limit()
    env.plan("a,b")                                                # two tasks hit the same limit
    env.settle("waiting_provider")
    SV.tick(env.p)
    sent = [r for r in _outbox(env.p) if r["kind"] == "provider_limit"]
    assert len(sent) == 1 and not sent[0]["clear"]
    assert sent[0]["text"].startswith("Claude limit reached; resuming about ") and "other tasks continue" in sent[0]["text"] and " (in " in sent[0]["text"]
    env.lift()
    env.advance_to_resume()
    env.settle("verified")
    sent = [r for r in _outbox(env.p) if r["kind"] == "provider_limit"]
    assert [r["clear"] for r in sent] == [False, True]


def test_no_alert_without_chat(env):
    env.aim("claude", "c5")
    env.usage_limit()
    env.limit()
    env.plan()
    env.settle("waiting_provider")
    assert not _outbox(env.p)
