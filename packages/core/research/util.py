"""Small shared helpers (no project state here)."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import socket
from typing import Any, Dict, Iterable, List, Optional


class ResearchError(Exception):
    """User-facing error. CLI prints message and exits with `exit_code`."""

    exit_code = 1

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.hint = hint


class NotFound(ResearchError):
    exit_code = 4


class BudgetExceeded(ResearchError):
    """Raised by agent-policy checks. Exit code 3 so agents can detect it."""

    exit_code = 3


class PolicyBlocked(BudgetExceeded):
    """A verification / coordination gate refused the action (same exit code 3 as the run budget)."""


class NotInitialized(ResearchError):
    exit_code = 2


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def parse_iso(s: Optional[str]) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover
        return "unknown"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str, limit: Optional[int] = None) -> Optional[str]:
    """Hash a file. If `limit` is given and the file is larger, return None (too big)."""
    try:
        size = os.path.getsize(path)
        if limit is not None and size > limit:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def jdump(v: Any) -> Optional[str]:
    if v is None:
        return None
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def jload(s: Optional[str], default: Any = None) -> Any:
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def as_list(v: Any) -> List[Any]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        return [x for x in v if x is not None and x != ""]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


def slugify(s: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.strip().lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "untitled"


def coerce_scalar(v: str) -> Any:
    """Parse CLI `key=value` values: int, float, bool, null, JSON lists, else string."""
    t = v.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    if t.lower() in ("null", "none"):
        return None
    try:
        if re.fullmatch(r"[-+]?\d+", t):
            return int(t)
        return float(t)
    except ValueError:
        pass
    if t[:1] in "[{":
        try:
            return json.loads(t)
        except ValueError:
            pass
    return v


def parse_kv(items: Optional[Iterable[str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for it in items or []:
        if "=" not in it:
            raise ResearchError(f"Expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = coerce_scalar(v)
    return out


def human_size(n: Optional[int]) -> str:
    if n is None:
        return "—"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return str(n)


# --------------------------------------------------------------------------- authorship

def detect_author(agent: Optional[str] = None, model: Optional[str] = None,
                  author_type: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Resolve who is acting.

    Priority: explicit args > RESEARCH_AUTHOR_TYPE / RESEARCH_AGENT / RESEARCH_AGENT_MODEL env >
    auto-detection of known agents (Claude Code sets CLAUDECODE=1) > human.
    """
    env = os.environ
    agent = agent or env.get("RESEARCH_AGENT") or None
    model = model or env.get("RESEARCH_AGENT_MODEL") or None
    at = author_type or env.get("RESEARCH_AUTHOR_TYPE") or None
    if not agent and not at:
        if env.get("CLAUDECODE") == "1" or env.get("CLAUDE_CODE_ENTRYPOINT"):
            agent = "claude-code"
        elif env.get("CODEX_SANDBOX") or env.get("CODEX_SANDBOX_NETWORK_DISABLED"):
            agent = "codex"
    if at is None:
        at = "agent" if agent else "human"
    if at not in ("human", "agent", "system"):
        raise ResearchError(f"author_type must be human|agent|system, got {at!r}")
    name = agent if at == "agent" else (env.get("RESEARCH_AUTHOR") or None)
    role = env.get("RESEARCH_AGENT_ROLE")
    if at == "agent" and name and role and "/" not in name:
        name = f"{name}/{role}"  # e.g. claude/verifier: roles of one CLI count as different reviewers
    return {"author_type": at, "author_name": name, "author_model": model if at == "agent" else None}
