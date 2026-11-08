"""Allow-list by platform USER ID with roles, pairing codes approved at the local terminal, and lockout. Machine-level, 0600.

`gateway/pairing.json`: {platform: {users: [{user_id, role, paired_at, label}], pending: {CODE: {user_id, label, expires}},
last_code: {user_id: ts}, fails: [ts...], locked_until: ts}}. Codes are never logged or put in events.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Dict, List, Optional

from ..util import ResearchError
from . import ROLES, gdir, read_json, txn, write_json

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O/1/I
EXPIRY, MAX_PENDING, PER_USER_GAP, MAX_FAILS, LOCKOUT = 3600, 3, 600, 5, 3600
_now = time.time                                      # tests replace it


def _path() -> str:
    return gdir("pairing.json")


def _load() -> Dict[str, Any]:
    d = read_json(_path())
    return d if isinstance(d, dict) else {}


def _plat(d: Dict[str, Any], platform: str) -> Dict[str, Any]:
    x = d.setdefault(platform, {})
    for k, v in (("users", []), ("pending", {}), ("last_code", {}), ("fails", [])):
        x.setdefault(k, v)
    return x


def users(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    d = _load()
    return [dict(u, platform=pl) for pl, x in sorted(d.items()) if platform in (None, pl) for u in (x.get("users") or [])]


def role_of(platform: str, user_id: Any) -> Optional[str]:
    for u in (_load().get(platform) or {}).get("users") or []:
        if str(u["user_id"]) == str(user_id):
            return u["role"]
    return None


def at_least(role: Optional[str], need: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(need)


def add_user(platform: str, user_id: Any, role: Optional[str] = None, label: str = "") -> Dict[str, Any]:
    if role is not None and role not in ROLES:
        raise ResearchError(f"role must be one of {', '.join(ROLES)}")
    with txn("pairing"):
        d = _load()
        x = _plat(d, platform)
        for u in x["users"]:
            if str(u["user_id"]) == str(user_id):
                if role:
                    u["role"] = role
                write_json(_path(), d)
                return u
        u = {"user_id": str(user_id), "role": role or ("operator" if not x["users"] else "viewer"), "paired_at": _now(), "label": label[:60]}
        x["users"].append(u)        # the first paired user defaults to operator, later ones to viewer
        write_json(_path(), d)
        return u


def remove_user(platform: str, user_id: Any) -> bool:
    with txn("pairing"):
        d = _load()
        x = _plat(d, platform)
        n = len(x["users"])
        x["users"] = [u for u in x["users"] if str(u["user_id"]) != str(user_id)]
        write_json(_path(), d)
        return len(x["users"]) < n


def request_code(platform: str, user_id: Any, label: str = "") -> Optional[str]:
    """A NEW code for an unknown user, or None when rate limits say no (a live code already exists for them, 3 are pending, or 10 minutes
    have not passed). The code is shown once, in the reply that creates it."""
    with txn("pairing"):
        d = _load()
        x = _plat(d, platform)
        now = _now()
        x["pending"] = {c: v for c, v in x["pending"].items() if v["expires"] > now}
        if any(v["user_id"] == str(user_id) for v in x["pending"].values()):
            return None
        if len(x["pending"]) >= MAX_PENDING or now - x["last_code"].get(str(user_id), 0) < PER_USER_GAP:
            return None
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        x["pending"][code] = {"user_id": str(user_id), "label": label[:60], "expires": now + EXPIRY}
        x["last_code"][str(user_id)] = now
        write_json(_path(), d)
        return code


def approve(code: str, role: Optional[str] = None) -> Dict[str, Any]:
    """Run at the local terminal. 5 wrong attempts lock pairing for an hour (all platforms: a guess costs the same everywhere)."""
    code = (code or "").strip().upper().replace("-", "")
    with txn("pairing"):
        d = _load()
        g = d.setdefault("_lock", {"fails": [], "until": 0})
        now = _now()
        if g["until"] > now:
            raise ResearchError("Too many wrong pairing codes; try again later", hint=f"locked for {int((g['until'] - now) / 60) + 1} more minute(s)")
        for platform, x in d.items():
            if platform.startswith("_"):
                continue
            v = (x.get("pending") or {}).get(code)
            if v and v["expires"] > now:
                del x["pending"][code]
                write_json(_path(), d)
                break
        else:
            g["fails"] = [t for t in g["fails"] if now - t < LOCKOUT] + [now]
            if len(g["fails"]) >= MAX_FAILS:
                g["until"] = now + LOCKOUT
            write_json(_path(), d)
            raise ResearchError("That code is wrong or expired", hint="Ask the user to message the bot again for a new code.")
        g["fails"] = []
        write_json(_path(), d)
    return dict(add_user(platform, v["user_id"], role, v["label"]), platform=platform)


def migrate_telegram(chat_id: Any) -> Optional[Dict[str, Any]]:
    """Existing Telegram setups: a private chat id (= the user id) becomes an operator once; a group becomes alerts-only until a
    member pairs. Returns the new entry, or None."""
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return None
    with txn("pairing"):
        d = _load()
        x = _plat(d, "telegram")
        if cid <= 0 or x.get("migrated") or any(str(u["user_id"]) == str(cid) for u in x["users"]):
            return None
        x["migrated"] = True
        write_json(_path(), d)
    return add_user("telegram", cid, "operator", "migrated from chat id")
