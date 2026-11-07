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
import sys
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
    if not pin:   # nothing was typed (no terminal, Ctrl-D): refuse, but do not count it toward the lockout
        raise PolicyBlocked(no_pin_reason(), hint="Run this from a normal terminal (a prompt appears), or use the dialog in VS Code." + _env_hint())
    if hmac.compare_digest(_hash(pin, base64.b64decode(d["salt"])), d["hash"]):
        if d.get("fails"):
            _save({**d, "fails": 0, "locked_until": 0})
        return
    d["fails"] = d.get("fails", 0) + 1
    if d["fails"] >= MAX_FAILS:
        d.update(fails=0, locked_until=time.time() + LOCK_SECONDS)
    _save(d)
    left = MAX_FAILS - d["fails"]
    raise PolicyBlocked("Incorrect PIN" + (f" ({left} attempt(s) left before a {LOCK_SECONDS}s lock)" if left > 0 else f"; locked for {LOCK_SECONDS}s"))


def set_pin(new: str, old: Optional[str] = None) -> None:
    """Create the PIN, or change it (the current PIN is required once one exists). Forgotten PIN: delete approval.json by hand."""
    if not new or len(new) < 4:
        raise ResearchError("The PIN must be at least 4 characters")
    if is_set():
        verify(old)
    salt = secrets.token_bytes(16)
    _save({"salt": base64.b64encode(salt).decode(), "hash": _hash(new, salt), "fails": 0, "locked_until": 0})


def agent_env_vars() -> list:
    return [k for k in _AGENT_ENV if os.environ.get(k)]


def _env_hint() -> str:
    v = agent_env_vars()
    return (f" This shell looks like an agent environment ({', '.join(v)} is set), so PIN prompts are disabled. "
            "Run it in your own terminal.") if v else ""


def no_pin_reason() -> str:
    """Why no PIN was available, so the error says which of three different problems it is (never the PIN itself)."""
    if agent_environment():
        return "No PIN was asked for: this shell is an agent-controlled environment (" + ", ".join(agent_env_vars()) + ")"
    if not _has_tty():
        return "No PIN could be read: there is no interactive terminal (a pipe, CI, a service or the VS Code RPC process)"
    return "No PIN was entered"


def _tty_fd() -> Optional[int]:
    try:
        return os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None


def read_pin(prompt: str = "Approval PIN: ") -> Optional[str]:
    """Read a secret from the controlling terminal with echo off. None when there is none (agent shells, CI, no tty): the caller fails closed.
    Uses termios on /dev/tty directly: `getpass(stream=open('/dev/tty','r+'))` raises 'not seekable' on Python 3.14, which used to be swallowed
    and reported as 'no interactive terminal'."""
    if agent_environment():
        return None
    try:
        import termios
    except ImportError:   # Windows
        import getpass
        return (getpass.getpass(prompt) or None) if sys.stdin.isatty() else None
    fd = _tty_fd()
    if fd is None:
        return None
    try:
        old = termios.tcgetattr(fd)
        new = old[:]
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSAFLUSH, new)
        try:
            os.write(fd, prompt.encode())
            buf = b""
            while not buf.endswith(b"\n"):
                ch = os.read(fd, 1)
                if not ch:
                    break
                buf += ch
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, old)
            os.write(fd, b"\n")
        return buf.decode(errors="ignore").strip("\r\n") or None
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)


def interactive() -> bool:
    """A person is at a terminal (not an agent, not a pipe, not the VS Code RPC process, not a test run)."""
    return not agent_environment() and sys.stdin.isatty() and sys.stdout.isatty() and _has_tty()


def _has_tty() -> bool:
    fd = _tty_fd()
    if fd is None:
        return False
    os.close(fd)
    return True


def enroll() -> None:
    """First protected action on a machine with no PIN: create one now, at the prompt, instead of silently skipping the check."""
    print("No approval PIN is set on this machine. Protected actions (approving, promoting, authorizing) need one.", file=sys.stderr)
    new = read_pin("Choose a PIN (at least 4 characters): ")
    if not new or new != read_pin("Repeat PIN: "):
        raise PolicyBlocked("No PIN was created, so nothing was approved", hint="Run `research approval set-pin`, then repeat the action.")
    set_pin(new)
    print("PIN saved on this machine (not in the project, git or agent context).", file=sys.stderr)


def confirm(p, action: str, rationale: Optional[str] = None, pin: Optional[str] = None, rationale_required: Optional[bool] = None) -> Dict[str, Any]:
    """Gate a protected action. Agents are refused outright; a human needs the PIN (when one is set and the policy requires it).
    The PIN is never recorded, only who approved what and why."""
    from . import governance as G
    if (p.author or {}).get("author_type") not in (None, "human") or (agent_environment() and pin is None):
        raise PolicyBlocked(f"{action} needs human approval: agents cannot do it",
                            hint="Submit a proposal instead (`research proposal …`); the researcher approves it with a PIN." + _env_hint())
    ap = G.cfg(p)["approval"]
    need_why = ap["require_rationale"] if rationale_required is None else rationale_required
    if need_why and not (rationale or "").strip():
        raise ResearchError(f"{action} needs a rationale", hint="Pass --note \"why\".")
    if ap["require_pin"] and not is_set():
        if pin is not None or not interactive():    # fail closed: nothing to verify against
            raise PolicyBlocked("No approval PIN is set on this machine",
                                hint="Set one: `research approval set-pin` or VS Code Settings → Approval PIN. "
                                     "To run without a PIN, set approval.require_pin: false (machine config).")
        enroll()
    elif ap["require_pin"]:
        verify(pin if pin is not None else read_pin())
    p.event("governance", None, "approval", f"{action}" + (f": {rationale.strip()[:300]}" if rationale else ""), {"author_type": "human", "author_name": os.environ.get("RESEARCH_AUTHOR")})
    return {"approved": True, "action": action}
