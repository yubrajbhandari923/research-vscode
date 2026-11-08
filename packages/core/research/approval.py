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

from .util import AGENT_ENV_VARS as _AGENT_ENV, PolicyBlocked, ResearchError

MAX_FAILS, LOCK_SECONDS = 5, 60


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


def chat_status() -> Dict[str, Any]:
    d = _load()
    return {"chat_enrolled": bool(d.get("totp")), "chat_disabled": bool(d.get("chat_disabled"))}   # never the secret


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
        if d.get("fails") or d.get("chat_disabled"):   # a local PIN login re-enables chat approval
            _save({**d, "fails": 0, "locked_until": 0, "chat_disabled": False})
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
    old_totp = _load().get("totp")   # changing the PIN keeps the enrolled authenticator
    _save({"salt": base64.b64encode(salt).decode(), "hash": _hash(new, salt), "fails": 0, "locked_until": 0, **({"totp": old_totp} if old_totp else {})})


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


# ----------------------------------------------------------------------------------------------- approving from chat (authenticator code)
# Computer-only forever. A code constant, not config: an edit by an agent cannot widen what chat may approve.
CHAT_FORBIDDEN = frozenset({"frozen_test_edit", "scientific_plan_edit", "hypothesis_edit", "governance_relax", "approval_settings",
                            "pin_change", "totp_change", "allow_list_change"})
CHAT_ACTIONS = ("experiment_proposal", "plan_add_tasks", "budget_increase", "retry", "role_change", "promotion", "resume", "decision_option")


def _totp_secret() -> Optional[str]:
    """The only reader of the authenticator secret; refuses agent environments."""
    if agent_environment():
        raise PolicyBlocked("The authenticator secret is not available in an agent environment")
    return (_load().get("totp") or {}).get("secret")


def proposal_hash(pr: Dict[str, Any]) -> str:
    """What the approval card shows: any change to the content or revision changes it, so a stale card cannot approve something new."""
    body = {k: pr.get(k) for k in ("id", "kind", "rev", "patch", "fields", "rationale", "task", "plan_key")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def chat_action(pr: Dict[str, Any]) -> str:
    """The action class of a proposal. Derived here, never taken from the proposal's own claim (a claim can only add restrictions)."""
    if pr.get("kind") == "experiment":
        tag = "experiment_proposal"
    elif pr.get("operations") and set(pr["operations"]) <= {"create_task"} and not pr.get("conflicts"):
        tag = "plan_add_tasks"
    else:
        tag = "scientific_plan_edit"
    return pr["action"] if pr.get("action") in CHAT_FORBIDDEN else tag


def chat_enroll(pin: Optional[str], ask_code, show, at: Optional[float] = None) -> None:
    """Enroll an authenticator. PIN required; shows the otpauth URI and base32 secret once; stores nothing until one valid code is entered."""
    from . import totp
    if agent_environment():
        raise PolicyBlocked("Only the researcher can enroll an authenticator", hint="Run it in your own terminal." + _env_hint())
    verify(pin)
    secret = totp.new_secret()
    show(totp.uri(secret, "research-os"), secret)
    if totp.verify(secret, ask_code() or "", at) is None:
        raise PolicyBlocked("That code did not match, so no authenticator was enrolled", hint="Check the phone clock and try again.")
    d = _load()
    d["totp"] = {"secret": secret, "last_step": -1, "enrolled": int(time.time())}
    d.update(chat_disabled=False)
    _save(d)


def chat_revoke(pin: Optional[str]) -> None:
    if agent_environment():
        raise PolicyBlocked("Only the researcher can revoke the authenticator", hint=_env_hint())
    verify(pin)
    d = _load()
    d.pop("totp", None)
    d["chat_disabled"] = False
    _save(d)


def confirm_chat(p, proposal: Dict[str, Any], content_hash: str, code: str, platform: str, user_id: str, role: str = "approver",
                 at: Optional[float] = None) -> Dict[str, Any]:
    """Gate one PIN-protected proposal approved from chat with an authenticator code (never the PIN). Raises PolicyBlocked unless every rule holds:
    chat approval enabled and enrolled, not disabled by failures, an `approver` user, an action chat may approve and `approval.chat_allow` permits,
    the content hash still equals the proposal's, and a fresh valid code. Failures share the PIN counter and its lock; the 5th disables chat
    approval until a local PIN login. The accepted time step is stored, so a code works once. Records the approval in the ledger as `totp-chat`."""
    from . import governance as G, totp
    if (p.author or {}).get("author_type") not in (None, "human") or agent_environment():
        raise PolicyBlocked("Chat approval is for humans on an allow-listed chat account: agents cannot use it")
    ap = G.cfg(p)["approval"]
    if not (ap.get("chat") or {}).get("enabled"):
        raise PolicyBlocked("Approving from chat is off", hint="Set governance.approval.chat.enabled: true, then enroll with `research approval chat-enroll`.")
    d = _load()
    if not d.get("totp"):
        raise PolicyBlocked("No authenticator is enrolled", hint="Run `research approval chat-enroll` at the computer.")
    if d.get("chat_disabled"):
        raise PolicyBlocked("Chat approval is disabled after repeated wrong codes", hint="Log in with your PIN at the computer (`research approval verify`) to re-enable it.")
    wait = int(d.get("locked_until", 0) - time.time())
    if wait > 0:
        raise PolicyBlocked(f"Too many wrong codes; try again in {wait}s")
    if role != "approver":
        raise PolicyBlocked("Approving needs the approver role", hint="Pair the user again with the approver role (research chat pair).")
    tag = chat_action(proposal)
    if tag in CHAT_FORBIDDEN or tag not in CHAT_ACTIONS:
        raise PolicyBlocked(f"{tag.replace('_', ' ')} needs your computer: it cannot be approved from chat")
    allowed = ap.get("chat_allow") or []
    if allowed and tag not in allowed:
        raise PolicyBlocked(f"{tag} is not in approval.chat_allow")
    if proposal.get("status", "pending") != "pending":
        raise PolicyBlocked(f"{proposal.get('id')} is already {proposal.get('status')}")
    if not content_hash or not hmac.compare_digest(str(content_hash), proposal_hash(proposal)):
        raise PolicyBlocked(f"{proposal.get('id')} changed since the card was sent: review it again")
    t = d["totp"]
    step = totp.verify(_totp_secret(), code or "", at, after_step=int(t.get("last_step", -1)))
    if step is None:
        d["fails"] = d.get("fails", 0) + 1
        if d["fails"] >= MAX_FAILS:
            d.update(fails=0, locked_until=time.time() + LOCK_SECONDS, chat_disabled=True)
        _save(d)
        left = MAX_FAILS - d.get("fails", 0)
        raise PolicyBlocked("Incorrect or already used code" + (f" ({left} attempt(s) left)" if not d.get("chat_disabled") else "; chat approval is now disabled until a PIN login at the computer"))
    t["last_step"] = step
    _save({**d, "totp": t, "fails": 0, "locked_until": 0})
    who = {"author_type": "human", "author_name": f"{platform}:{user_id}"}
    p.event("governance", proposal.get("id"), "approval", f"Approve {proposal.get('id')} method=totp-chat platform={platform} user={user_id} role={role} action={tag}", who)
    return {"approved": True, "method": "totp-chat", "platform": platform, "user_id": user_id, "role": role, "action": tag}


def approve_proposal_from_chat(p, pid: str, content_hash: str, code: str, platform: str, user_id: str, role: str = "approver",
                               note: Optional[str] = None, at: Optional[float] = None) -> Dict[str, Any]:
    """Approve plan/experiment proposal `pid` from chat (gateway process only). Same apply path as `decide_proposal`, exactly once."""
    from . import supervisor as SV
    with SV._lock(p):
        pr = SV._prop_load(p, pid)
        proof = confirm_chat(p, pr, content_hash, code, platform, user_id, role, at)
        if pr.get("kind") != "experiment" and SV.classify_change(p, pr["patch"])["conflicts"]:
            raise ResearchError(f"{pid} no longer applies cleanly", hint="Ask the agent to revise it.")
        pr["approved_via"] = {k: proof[k] for k in ("method", "platform", "user_id", "role")}
        SV._apply_proposal(p, pr, f"chat:{platform}:{user_id} (totp-chat)" + (f": {note}" if note else ""), status="approved")
    p.event("plan", None, "plan_proposal_approved", f"{pid} approved via chat")
    return pr
