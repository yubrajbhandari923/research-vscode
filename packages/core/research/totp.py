"""TOTP (RFC 6238) with the standard library only: 6 digits, 30 s step, HMAC-SHA1, so any authenticator app works.

Pure functions; the secret's storage and the failure counter live in approval.py. `at` (seconds since the epoch) is injectable for tests.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP, DIGITS = 30, 6


def new_secret() -> str:
    """160 random bits, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS).zfill(DIGITS)


def step_of(at: Optional[float] = None) -> int:
    return int((time.time() if at is None else at) // STEP)


def code(secret: str, at: Optional[float] = None) -> str:
    return _hotp(secret, step_of(at))


def verify(secret: str, given: str, at: Optional[float] = None, window: int = 1, after_step: int = -1) -> Optional[int]:
    """The time step `given` matches within [-window, +window] of now, or None. Steps <= `after_step` (already used) never match.
    Every candidate is compared in constant time and none short-circuits."""
    given = "".join(str(given or "").split())
    now, hit = step_of(at), None
    for s in range(now - window, now + window + 1):
        if hmac.compare_digest(_hotp(secret, s), given) and s > after_step:
            hit = s
    return hit


def uri(secret: str, account: str, issuer: str = "Research OS") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"
