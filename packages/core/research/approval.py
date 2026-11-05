"""Lightweight human-approval guardrail: a local PIN that stops *accidental* autonomous changes.

It is not authentication infrastructure and not a defence against a hostile agent: an agent with the same OS user can read this
machine-local file or edit the code. What it does: protected actions (approving an agent's proposal, overriding governance,
promoting to main) need a PIN typed at an interactive terminal or in the VS Code dialog. Without one the action fails closed.

The verifier (scrypt, random salt) lives in the machine config directory, never in the project, git, events, prompts or MCP output.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

from .util import PolicyBlocked, ResearchError

MAX_FAILS, LOCK_SECONDS = 5, 60
_AGENT_ENV = ("RESEARCH_AGENT", "RESEARCH_EXECUTION", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")


def _path() -> str:
    from .configfile import machine_path
    return os.path.join(os.path.dirname(machine_path() or os.path.expanduser("~/.config/research-os/config.yaml")), "approval.json")


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d: Dict[str, Any]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def _hash(pin: str, salt: bytes) -> str:
    return base64.b64encode(hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)).decode()


def is_set() -> bool:
    return bool(_load().get("hash"))


def status() -> Dict[str, Any]:
    d = _load()
    return {"set": bool(d.get("hash")), "locked_for": max(0, int(d.get("locked_until", 0) - time.time()))}


def agent_environment() -> bool:
    """True when this process looks like it runs inside an agent, whatever `--as` claims."""
    return any(os.environ.get(k) for k in _AGENT_ENV)


def verify(pin: Optional[str]) -> None:
    """Raise unless `pin` matches. Counts failures and locks briefly after MAX_FAILS."""
    d = _load()
    if not d.get("hash"):
        raise PolicyBlocked("No approval PIN is set", hint="Set one with `research approval set-pin` (or in Settings → Approval).")
    wait = int(d.get("locked_until", 0) - time.time())
    if wait > 0:
        raise PolicyBlocked(f"Too many wrong PINs; try again in {wait}s")
    if pin and hmac.compare_digest(_hash(pin, base64.b64decode(d["salt"])), d["hash"]):
        if d.get("fails"):
            _save({**d, "fails": 0, "locked_until": 0})
        return
    d["fails"] = d.get("fails", 0) + 1
    if d["fails"] >= MAX_FAILS:
        d.update(fails=0, locked_until=time.time() + LOCK_SECONDS)
    _save(d)
    raise PolicyBlocked("Wrong or missing PIN")


def set_pin(new: str, old: Optional[str] = None) -> None:
    """Create the PIN, or change it (the current PIN is required once one exists). Forgotten PIN: delete approval.json by hand."""
    if not new or len(new) < 4:
        raise ResearchError("The PIN must be at least 4 characters")
    if is_set():
        verify(old)
    salt = secrets.token_bytes(16)
    _save({"salt": base64.b64encode(salt).decode(), "hash": _hash(new, salt), "fails": 0, "locked_until": 0})


def read_pin(prompt: str = "Approval PIN: ") -> Optional[str]:
    """Read the PIN from the controlling terminal. None when there is none (agent shells, CI): the caller then fails closed."""
    import getpass
    if agent_environment():
        return None
    try:
        with open("/dev/tty", "r+") as tty:
            return getpass.getpass(prompt, stream=tty)
    except OSError:
        return None


def confirm(p, action: str, rationale: Optional[str] = None, pin: Optional[str] = None, rationale_required: Optional[bool] = None) -> Dict[str, Any]:
    """Gate a protected action. Agents are refused outright; a human needs the PIN (when one is set and the policy requires it).
    The PIN is never recorded, only who approved what and why."""
    from . import governance as G
    if (p.author or {}).get("author_type") not in (None, "human") or (agent_environment() and pin is None):
        raise PolicyBlocked(f"{action} needs human approval: agents cannot do it",
                            hint="Submit a proposal instead (`research proposal …`); the researcher approves it with a PIN.")
    ap = G.cfg(p)["approval"]
    need_why = ap["require_rationale"] if rationale_required is None else rationale_required
    if need_why and not (rationale or "").strip():
        raise ResearchError(f"{action} needs a rationale", hint="Pass --note \"why\".")
    if ap["require_pin"] and is_set():
        verify(pin if pin is not None else read_pin())
    p.event("governance", None, "approval", f"{action}" + (f": {rationale.strip()[:300]}" if rationale else ""), {"author_type": "human", "author_name": os.environ.get("RESEARCH_AUTHOR")})
    return {"approved": True, "action": action}
