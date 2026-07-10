"""Single-admin auth: password login -> HMAC-signed expiring token.

The admin password comes from ADMIN_PASSWORD env, or is auto-generated on
first run and printed to the log (like Portainer/Odysseus do).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request, WebSocket

from . import config

_PW_FILE = config.DATA_DIR / "admin.pw"
TOKEN_TTL = 30 * 24 * 3600  # 30 days; mobile apps shouldn't log you out constantly

_failed: dict[str, list[float]] = {}  # ip -> timestamps, simple rate limit


def _hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def bootstrap_password() -> None:
    """Ensure an admin password exists; generate + log one on first run."""
    env_pw = os.environ.get("ADMIN_PASSWORD")
    if env_pw:
        salt = secrets.token_bytes(16)
        _PW_FILE.write_bytes(salt + _hash_pw(env_pw, salt))
        os.chmod(_PW_FILE, 0o600)
        return
    if _PW_FILE.exists():
        return
    pw = secrets.token_urlsafe(12)
    salt = secrets.token_bytes(16)
    _PW_FILE.write_bytes(salt + _hash_pw(pw, salt))
    os.chmod(_PW_FILE, 0o600)
    print(f"\n{'='*56}\n  Helmsman first run — admin password: {pw}\n"
          f"  (set ADMIN_PASSWORD env to choose your own)\n{'='*56}\n", flush=True)


def verify_password(password: str) -> bool:
    if not _PW_FILE.exists():
        return False
    blob = _PW_FILE.read_bytes()
    salt, expected = blob[:16], blob[16:]
    return hmac.compare_digest(_hash_pw(password, salt), expected)


def _sign(payload: bytes) -> str:
    sig = hmac.new(config.get_secret(), payload, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." +
            base64.urlsafe_b64encode(sig).decode().rstrip("="))


def issue_token() -> str:
    payload = json.dumps({"exp": int(time.time()) + TOKEN_TTL, "n": secrets.token_hex(8)}).encode()
    return _sign(payload)


def check_token(token: str) -> bool:
    try:
        p64, s64 = token.split(".")
        payload = base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4))
        sig = base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4))
        expected = hmac.new(config.get_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return False
        return json.loads(payload)["exp"] > time.time()
    except Exception:
        return False


def rate_limit(ip: str) -> None:
    now = time.time()
    attempts = [t for t in _failed.get(ip, []) if now - t < 300]
    _failed[ip] = attempts
    if len(attempts) >= 8:
        raise HTTPException(429, "Too many attempts, try again later")


def record_failure(ip: str) -> None:
    _failed.setdefault(ip, []).append(time.time())


def require_auth(request: Request) -> None:
    """FastAPI dependency for HTTP routes."""
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth else request.query_params.get("token", "")
    if not check_token(token):
        raise HTTPException(401, "Not authenticated")


async def require_auth_ws(ws: WebSocket) -> bool:
    """WebSocket auth: token passed as query param. Closes socket if invalid."""
    token = ws.query_params.get("token", "")
    if not check_token(token):
        await ws.close(code=4401)
        return False
    return True
