"""One-time pairing codes for the "add this server to another device" flow.

An authenticated session mints a short-lived code (shown as a QR); the new
device claims it once and receives a regular auth token. Codes live only in
memory — a restart simply voids any unclaimed codes.
"""
import secrets
import time

TTL = 600            # seconds a code stays claimable
MAX_ACTIVE = 8

_codes: dict[str, float] = {}   # code -> expiry


def _purge() -> None:
    now = time.time()
    for code in [c for c, exp in _codes.items() if exp <= now]:
        _codes.pop(code, None)


def new_code() -> tuple[str, int]:
    _purge()
    while len(_codes) >= MAX_ACTIVE:   # drop the oldest outstanding code
        _codes.pop(min(_codes, key=_codes.get), None)
    code = secrets.token_urlsafe(18)
    _codes[code] = time.time() + TTL
    return code, TTL


def claim(code: str) -> bool:
    """True exactly once per valid code."""
    _purge()
    return _codes.pop(code or "", None) is not None
