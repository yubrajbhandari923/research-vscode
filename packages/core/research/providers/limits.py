"""Shared parsing for provider usage-limit messages (design H.10; message formats: docs/PHASE_CLEANUP_R_LIMITS.md).

A reset time is returned as a tz-aware UTC datetime or None (unknown: the supervisor then uses its backoff schedule). Clock times
without a date resolve to the next occurrence; times without a zone are host-local (the supervisor and the CLI share a host).
`now()` is the one clock, so tests set a fake one with `limits.set_clock`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

from .base import LimitInfo

_clock: Optional[Callable[[], datetime]] = None
PAST_GRACE = timedelta(minutes=5)   # a reset a moment ago is "now", not "tomorrow" (parsing and launch delays)


def set_clock(fn: Optional[Callable[[], datetime]]) -> None:
    global _clock
    _clock = fn


def now() -> datetime:
    return _clock() if _clock else datetime.now(timezone.utc)


def from_epoch(v: Any) -> Optional[datetime]:
    """Epoch seconds, or milliseconds when the number is too large to be seconds."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)


_UNIT = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_REL_KEY = re.compile(r"(?:try again in|try again after|resets? in|retry after|retry in|retry-after:?)\s*", re.I)
_REL_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])", re.I)


def parse_relative(text: str) -> Optional[datetime]:
    """'try again in 2h 14m', 'Resets in 1h1m35s', 'after 60.6 seconds', 'retry-after: 56482' -> now + that. None when absent."""
    for m in _REL_KEY.finditer(text or ""):
        tail = text[m.end():m.end() + 40]
        secs = sum(float(n) * _UNIT[u[0].lower()] for n, u in _contiguous(tail))
        bare = re.match(r"(\d+(?:\.\d+)?)\b(?!\s*[a-z])", tail)   # a bare number after retry-after is seconds
        if not secs and bare and "retry-after" in m.group(0).lower():
            secs = float(bare.group(1))
        if secs:
            return now() + timedelta(seconds=secs)
    return None


def _contiguous(tail: str) -> List[Any]:
    """Duration pieces that follow each other ('2h 14m', '1h1m35s'); stops at the first non-duration text."""
    out, pos = [], 0
    for m in _REL_PART.finditer(tail):
        if tail[pos:m.start()].strip(" ,and"):
            break
        out.append((m.group(1), m.group(2)))
        pos = m.end()
    return out


def _zone(name: Optional[str]):
    """None = host-local. 'UTC' and IANA names via zoneinfo; an unknown name or a Python without zoneinfo raises KeyError."""
    if not name:
        return None
    if name.strip().upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name.strip())
    except Exception as e:   # ImportError (py3.8), ZoneInfoNotFoundError, ValueError
        raise KeyError(name) from e


def _at(day: datetime, h: int, mi: int, zone) -> datetime:
    """Wall-clock h:mi on the calendar date of `day` in `zone` (None = host-local), as UTC. DST-correct (mktime/zoneinfo resolve the offset)."""
    naive = datetime(day.year, day.month, day.day, h, mi)
    return (naive.replace(tzinfo=zone) if zone else naive.astimezone()).astimezone(timezone.utc)


_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b(?:\s*\(([^)]+)\))?", re.I)


def parse_clock(text: str) -> Optional[datetime]:
    """First '3pm', '3:45pm', '11am (UTC)', '2pm (America/New_York)' in `text`: the next occurrence of that wall-clock time. None when
    absent or the zone is unknown (an unknown zone must not be guessed)."""
    m = _CLOCK.search(text or "")
    if not m:
        return None
    h, mi = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0), int(m.group(2) or 0)
    if int(m.group(1)) > 12 or mi > 59:
        return None
    try:
        zone = _zone(m.group(4))
    except KeyError:
        return None
    n = now()
    local_today = n.astimezone(zone) if zone else n.astimezone()
    cand = _at(local_today, h, mi, zone)
    if cand < n - PAST_GRACE:
        cand = _at(local_today + timedelta(days=1), h, mi, zone)   # calendar day after, so DST shifts are honoured
    return cand


_FULL = re.compile(r"([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th)?, (\d{4}) (\d{1,2}):(\d{2}) ([AP]M)")
_MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def parse_codex_time(text: str) -> Optional[datetime]:
    """Codex 'Try again at 3:41 PM.' (host-local, today) or 'Try again at Feb 23rd, 2026 9:01 PM.' (host-local date). 'later' -> None."""
    m = re.search(r"[Tt]ry again at (.+?)\.?\s*$", (text or "").strip())
    if not m:
        return None
    s = m.group(1).strip().rstrip(".")
    f = _FULL.search(s)
    if f and f.group(1) in _MONTHS:
        h = int(f.group(4)) % 12 + (12 if f.group(6) == "PM" else 0)
        return _at(datetime(int(f.group(3)), _MONTHS[f.group(1)], int(f.group(2))), h, int(f.group(5)), None)
    return parse_clock(s)


def parse_iso_utc(text: str) -> Optional[datetime]:
    """'You will regain access on 2026-09-01 at 00:00 UTC' (Anthropic spend cap)."""
    m = re.search(r"regain access on (\d{4})-(\d\d)-(\d\d) at (\d\d):(\d\d) UTC", text or "")
    return datetime(*(int(x) for x in m.groups()), tzinfo=timezone.utc) if m else None


# ------------------------------------------------------------------------------------------------ classification
_KINDS = [("credit", re.compile(r"credit balance|insufficient_quota|billing_error|exceeded your current quota", re.I)),
          ("overload", re.compile(r"overloaded|\b529\b|MODEL_CAPACITY_EXHAUSTED|at capacity", re.I)),
          ("usage", re.compile(r"hit your .{0,40}limit|usage.limit|usage_limit_reached|quota reached|RESOURCE_EXHAUSTED|Free usage exceeded|"
                               r"QuotaExceeded|spend limit|session limit|weekly limit", re.I)),
          ("rate", re.compile(r"rate.?limit|\b429\b|temporarily limiting|too many requests|slow down", re.I))]


def classify(text: str) -> Optional[str]:
    for kind, rx in _KINDS:
        if rx.search(text or ""):
            return kind
    return None


def reset_in(text: str) -> Optional[datetime]:
    """Whatever reset form the text carries; absolute forms first (they are exact), relative last."""
    return _header_epoch(text) or parse_iso_utc(text) or parse_codex_time(text) or _after_resets(text) or parse_relative(text)


def _header_epoch(text: str) -> Optional[datetime]:
    m = re.search(r"ratelimit-unified-reset:?\s*(\d{9,13})\b", text or "", re.I)
    return from_epoch(m.group(1)) if m else None


def _after_resets(text: str) -> Optional[datetime]:
    """Clock time after 'resets' / 'will reset at' (never a bare clock time elsewhere in the text)."""
    m = re.search(r"resets?(?: at)?\s+(?=\d)", text or "", re.I)
    return parse_clock(text[m.end():]) if m else None


def info(text: str, kind: Optional[str] = None, reset: Optional[datetime] = None) -> Optional[LimitInfo]:
    k = kind or classify(text)
    return LimitInfo(k, reset if reset else reset_in(text), text.strip()[:300]) if k else None


def json_lines(*streams: str) -> Iterator[Dict[str, Any]]:
    for s in streams:
        for line in (s or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d


def plain_lines(*streams: str) -> Iterator[str]:
    for s in streams:
        for line in (s or "").splitlines():
            if line.strip() and not line.strip().startswith("{"):
                yield line.strip()


def best(cands: List[Optional[LimitInfo]]) -> Optional[LimitInfo]:
    """The first candidate that knows its reset time, else the first candidate."""
    got = [c for c in cands if c]
    return next((c for c in got if c.reset_at), got[0] if got else None)


def failing(exit_code: Optional[int]) -> bool:
    """Plain-text matching only counts for a failed run: an agent that merely quotes 'rate limit' in a finished answer is not limited."""
    return exit_code != 0
