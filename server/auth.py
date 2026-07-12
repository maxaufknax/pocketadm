"""Single-admin auth: password login -> HMAC-signed expiring token.

The admin password comes from ADMIN_PASSWORD env, or is auto-generated on
first run and printed to the log (like Portainer/Odysseus do).

Tokens carry a *generation* number; bumping it (on password change or an
explicit "sign out everywhere") invalidates every previously issued token.
Optional TOTP two-factor adds a second step at login.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time

from fastapi import HTTPException, Request, WebSocket

from . import config

try:                       # optional: pretty QR for the 2FA setup screen
    import segno
except Exception:          # pragma: no cover - QR just degrades to manual entry
    segno = None

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


def set_password(new_password: str) -> None:
    """Change the admin password (used by Settings → Server).

    Also bumps the token generation so any other logged-in sessions are
    signed out — a password change should revoke access everywhere."""
    salt = secrets.token_bytes(16)
    _PW_FILE.write_bytes(salt + _hash_pw(new_password, salt))
    os.chmod(_PW_FILE, 0o600)
    config.bump_auth_generation()


def verify_password(password: str) -> bool:
    if not _PW_FILE.exists():
        return False
    blob = _PW_FILE.read_bytes()
    salt, expected = blob[:16], blob[16:]
    return hmac.compare_digest(_hash_pw(password, salt), expected)


# ------------------------------------------------------------- TOTP (2FA)

def totp_enabled() -> bool:
    return bool(config.get_totp_secret())


def new_totp_secret() -> str:
    """A fresh base32 secret (not yet activated)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_code(secret_b32: str, at: float, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    counter = int(at // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    num = struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF
    return str(num % (10 ** digits)).zfill(digits)


def verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or not secret:
        return False
    now = time.time()
    return any(hmac.compare_digest(_totp_code(secret, now + d * 30), code)
               for d in range(-window, window + 1))


def verify_totp(code: str) -> bool:
    return verify_totp_code(config.get_totp_secret(), code)


def provisioning_uri(secret: str, label: str = "") -> str:
    issuer = "Helmsman"
    acct = label or (config.get_server_name() or "admin")
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(issuer)}:{quote(acct)}"
            f"?secret={secret}&issuer={quote(issuer)}&digits=6&period=30")


def qr_svg(uri: str) -> str | None:
    if not segno:
        return None
    try:
        import io
        buf = io.BytesIO()
        segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2,
                                        dark="#0d1117", light="#ffffff")
        return buf.getvalue().decode("utf-8")
    except Exception:
        return None


def enable_totp(secret: str, code: str) -> bool:
    """Confirm the user's authenticator works before turning 2FA on."""
    if not verify_totp_code(secret, code):
        return False
    config.set_totp_secret(secret)
    return True


def disable_totp() -> None:
    config.set_totp_secret("")


# ----------------------------------------------------------------- tokens

def _sign(payload: bytes) -> str:
    sig = hmac.new(config.get_secret(), payload, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." +
            base64.urlsafe_b64encode(sig).decode().rstrip("="))


def issue_token() -> str:
    payload = json.dumps({"exp": int(time.time()) + TOKEN_TTL,
                          "gen": config.get_auth_generation(),
                          "n": secrets.token_hex(8)}).encode()
    return _sign(payload)


def revoke_all_sessions() -> str:
    """Invalidate every existing token and return a fresh one for the caller."""
    config.bump_auth_generation()
    return issue_token()


def check_token(token: str) -> bool:
    try:
        p64, s64 = token.split(".")
        payload = base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4))
        sig = base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4))
        expected = hmac.new(config.get_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(payload)
        if data["exp"] <= time.time():
            return False
        return data.get("gen", 0) == config.get_auth_generation()
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
