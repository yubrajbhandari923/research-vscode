"""H.10 parsers: `providers.limit_info` against the fixtures of docs/PHASE_CLEANUP_R_LIMITS.md §7 (label REAL = quoted from an issue/doc/source,
REALISTIC = built from verified field names), with a fake clock and a fixed host timezone. The supervisor does not use it yet (WP18)."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from research import providers
from research.providers import limits as L

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "limits")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ" if "." in s else "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    def set_(now=NOW, tz="America/Los_Angeles"):
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        L.set_clock(lambda: now)
    set_()
    yield set_
    L.set_clock(None)
    monkeypatch.undo()
    time.tzset()


def _rows():
    out = []
    for prov in ("claude", "codex", "opencode", "agy"):
        for line in open(os.path.join(FIX, prov + ".jsonl"), encoding="utf-8"):
            r = json.loads(line)
            out.append(pytest.param(prov, r, id=f"{prov}-{r['id']}-{r['label']}"))
    return out


@pytest.mark.parametrize("prov,r", _rows())
def test_fixture(clock, prov, r):
    clock(_iso(r["now"]) if r.get("now") else NOW)
    got = providers.limit_info(prov, r["stdout"], r["stderr"], r["exit"])
    if r["expect"] is None:
        assert got is None, got
        return
    assert got is not None and got.kind == r["expect"]["kind"], got
    assert got.reset_at == (_iso(r["expect"]["reset"]) if r["expect"]["reset"] else None), got
    assert got.reset_at is None or got.reset_at.tzinfo is not None and got.reset_at.utcoffset() == timedelta(0)


def test_every_provider_with_a_parser_has_fixtures():
    assert {n for n, a in providers.ADAPTERS.items() if a.limit_info} == {"claude", "codex", "opencode", "agy"} == {f[:-6] for f in os.listdir(FIX)}
    assert providers.limit_info("custom", "rate limit", "", 1) is None and providers.limit_info("nope", "", "", 1) is None


@pytest.mark.parametrize("tz,now,text,want", [
    # DST starts 2026-03-08 in New York: "tomorrow 3pm" is EDT (19:00Z), not EST (20:00Z)
    ("America/New_York", "2026-03-07T21:00:00Z", "You've hit your session limit · resets 3pm (America/New_York)", "2026-03-08T19:00:00Z"),
    # DST ends 2026-11-01: next 3pm is EST (20:00Z), not EDT (19:00Z)
    ("America/New_York", "2026-10-31T20:00:00Z", "You've hit your session limit · resets 3pm (America/New_York)", "2026-11-01T20:00:00Z"),
    # the same through the host zone (no zone named in the message)
    ("America/New_York", "2026-03-07T21:00:00Z", "You've hit your session limit · resets 3pm", "2026-03-08T19:00:00Z"),
    ("America/New_York", "2026-10-31T20:00:00Z", "You've hit your session limit · resets 3pm", "2026-11-01T20:00:00Z"),
    # still ahead today: no roll-over; a minute ago: still today (launch delay), an hour ago: tomorrow
    ("UTC", "2026-11-07T12:00:00Z", "resets 3pm", "2026-11-07T15:00:00Z"),
    ("UTC", "2026-11-07T15:01:00Z", "resets 3pm", "2026-11-07T15:00:00Z"),
    ("UTC", "2026-11-07T16:00:00Z", "resets 3pm", "2026-11-08T15:00:00Z"),
    # 12am / 12pm
    ("UTC", "2026-11-07T12:00:00Z", "resets 12am (UTC)", "2026-11-08T00:00:00Z"),
    ("UTC", "2026-11-07T10:00:00Z", "resets 12pm (UTC)", "2026-11-07T12:00:00Z"),
    # a zone far from the host: Tokyo wall clock
    ("America/Los_Angeles", "2026-11-07T12:00:00Z", "resets 11am (Asia/Tokyo)", "2026-11-08T02:00:00Z"),
    # unknown zone: never guessed
    ("UTC", "2026-11-07T12:00:00Z", "resets 3pm (Mars/Olympus)", None),
])
def test_claude_clock_times_dst_and_zones(clock, tz, now, text, want):
    clock(_iso(now), tz)
    got = providers.limit_info("claude", "You've hit your session limit · " + text, "", 1)
    assert got.kind == "usage" and got.reset_at == (_iso(want) if want else None)


def test_codex_dates_follow_the_host_dst(clock):
    clock(NOW, "America/New_York")   # Mar 8 3:41 AM is EDT; Feb 23 9:01 PM is EST
    assert providers.limit_info("codex", "", "You’ve hit your usage limit. Try again at Mar 8th, 2026 3:41 AM.", 1).reset_at == _iso("2026-03-08T07:41:00Z")
    assert providers.limit_info("codex", "", "You’ve hit your usage limit. Try again at Feb 23rd, 2026 9:01 PM.", 1).reset_at == _iso("2026-02-24T02:01:00Z")


def test_plain_text_only_counts_for_a_failed_run(clock):
    assert providers.limit_info("claude", "You've hit your weekly limit · resets 11am (UTC)", "", 0) is None
    assert providers.limit_info("claude", "You've hit your weekly limit · resets 11am (UTC)", "", None).kind == "usage"


def test_epoch_seconds_and_milliseconds():
    assert L.from_epoch(1790578200) == L.from_epoch(1790578200000) == datetime(2026, 9, 28, 6, 50, tzinfo=timezone.utc)
    assert L.from_epoch("x") is None and L.from_epoch(0) is None
