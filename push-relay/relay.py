"""PocketADM push relay — Apple push notifications for self-hosted servers.

A self-hosted PocketADM server cannot talk to Apple's push service itself: that
takes the app developer's signing key, which must never sit on thousands of
other people's machines. This tiny service holds the key and does only this:

  POST /v1/register   {token, bundle, env}    -> {relay_id}
      The iPhone app, with its Apple device token. The relay id is a random
      capability (256 bits): whoever holds it can send a notification to that
      one phone, nothing else. The same token always gets the same id back.
  POST /v1/send       {relay_id, title, subtitle, body, thread, category,
                       level, sound, data}
      A PocketADM server, with a relay id one of its phones gave it. Rate
      limited per phone. 410 means the phone is gone for good.
  POST /v1/unregister {relay_id}
  GET  /v1/health

The relay never learns which server a phone belongs to, the servers never see
the device token, and nothing about a message is stored: it is forwarded and
forgotten. Configure with APNS_KEY_FILE (the .p8), APNS_KEY_ID, APNS_TEAM_ID
and RELAY_BUNDLES (comma-separated bundle ids that may register).
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

DB_PATH = os.environ.get("RELAY_DB", "/data/relay.db")
KEY_FILE = os.environ.get("APNS_KEY_FILE", "/secrets/apns.p8")
KEY_ID = os.environ.get("APNS_KEY_ID", "")
TEAM_ID = os.environ.get("APNS_TEAM_ID", "")
BUNDLES = {b.strip() for b in os.environ.get("RELAY_BUNDLES", "de.maxaufknax.pocketadm").split(",")
           if b.strip()}
HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
PER_HOUR = int(os.environ.get("RELAY_PER_HOUR", "120"))
PER_DAY = int(os.environ.get("RELAY_PER_DAY", "1000"))
REGISTER_PER_HOUR = int(os.environ.get("RELAY_REGISTER_PER_HOUR", "60"))

_TOKEN = re.compile(r"^[0-9a-fA-F]{64,200}$")
_RELAY_ID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

app = FastAPI(title="PocketADM push relay", docs_url=None, redoc_url=None)
_lock = threading.Lock()


# ------------------------------------------------------------------ storage

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS devices (relay_id TEXT PRIMARY KEY, token TEXT UNIQUE, "
                "bundle TEXT, env TEXT, created REAL, last_used REAL)")
    return con


def register_token(token: str, bundle: str, env: str) -> str:
    with _lock, _db() as con:
        row = con.execute("SELECT relay_id FROM devices WHERE token = ?", (token,)).fetchone()
        if row:
            con.execute("UPDATE devices SET bundle = ?, env = ? WHERE relay_id = ?", (bundle, env, row[0]))
            return row[0]
        relay_id = secrets.token_urlsafe(32)
        con.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?)",
                    (relay_id, token, bundle, env, time.time(), 0))
        return relay_id


def lookup(relay_id: str) -> tuple[str, str, str] | None:
    with _lock, _db() as con:
        row = con.execute("SELECT token, bundle, env FROM devices WHERE relay_id = ?", (relay_id,)).fetchone()
        if row:
            con.execute("UPDATE devices SET last_used = ? WHERE relay_id = ?", (time.time(), relay_id))
        return tuple(row) if row else None


def forget(relay_id: str) -> None:
    with _lock, _db() as con:
        con.execute("DELETE FROM devices WHERE relay_id = ?", (relay_id,))


def count() -> int:
    with _lock, _db() as con:
        return con.execute("SELECT COUNT(*) FROM devices").fetchone()[0]


# ------------------------------------------------------------------ rate limits

_sends: dict[str, deque] = defaultdict(deque)
_registers: dict[str, deque] = defaultdict(deque)


def _allow(bucket: dict[str, deque], key: str, limits: list[tuple[int, float]]) -> bool:
    now = time.time()
    q = bucket[key]
    horizon = max(window for _, window in limits)
    while q and now - q[0] > horizon:
        q.popleft()
    for limit, window in limits:
        if sum(1 for t in q if now - t <= window) >= limit:
            return False
    q.append(now)
    return True


# ------------------------------------------------------------------ Apple

_jwt_cache: dict = {"token": "", "t": 0.0}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def configured() -> bool:
    return bool(KEY_ID and TEAM_ID and os.path.isfile(KEY_FILE))


def provider_token() -> str:
    """The ES256 token Apple wants, renewed every 50 minutes (it allows 60)."""
    if _jwt_cache["token"] and time.time() - _jwt_cache["t"] < 3000:
        return _jwt_cache["token"]
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    with open(KEY_FILE, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    header = _b64(json.dumps({"alg": "ES256", "kid": KEY_ID}).encode())
    claims = _b64(json.dumps({"iss": TEAM_ID, "iat": int(time.time())}).encode())
    signing_input = f"{header}.{claims}".encode()
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    token = f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    _jwt_cache.update(token=token, t=time.time())
    return token


def apns_payload(msg: "SendBody") -> dict:
    alert = {"title": msg.title[:120], "body": msg.body[:900]}
    if msg.subtitle:
        alert["subtitle"] = msg.subtitle[:60]
    aps: dict = {"alert": alert, "thread-id": msg.thread[:64] or "pocketadm",
                 "interruption-level": msg.level if msg.level in ("passive", "active", "time-sensitive")
                 else "active"}
    if msg.sound:
        aps["sound"] = "default"
    if msg.category:
        aps["category"] = msg.category[:32]
    data = {k: v for k, v in (msg.data or {}).items() if isinstance(v, (str, int, float, bool))}
    return {"aps": aps, "pocketadm": dict(list(data.items())[:12])}


async def deliver(token: str, bundle: str, env: str, payload: dict) -> tuple[int, str]:
    """POST one notification to Apple. Returns (status, reason)."""
    async with httpx.AsyncClient(http2=True, timeout=15) as client:
        r = await client.post(
            f"{HOSTS.get(env, HOSTS['production'])}/3/device/{token}",
            content=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"authorization": f"bearer {provider_token()}", "apns-topic": bundle,
                     "apns-push-type": "alert", "apns-priority": "10",
                     "apns-expiration": str(int(time.time()) + 86400)})
    reason = ""
    if r.status_code != 200:
        try:
            reason = r.json().get("reason", "")
        except ValueError:
            reason = r.text[:80]
    return r.status_code, reason


# ------------------------------------------------------------------ API

class RegisterBody(BaseModel):
    token: str
    bundle: str
    env: str = "production"


class SendBody(BaseModel):
    relay_id: str
    title: str = ""
    subtitle: str = ""
    body: str = ""
    thread: str = ""
    category: str = ""
    level: str = "active"
    sound: bool = True
    data: dict | None = None


class RelayIdBody(BaseModel):
    relay_id: str


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() if forwarded else "") or \
        (request.client.host if request.client else "?")


@app.get("/v1/health")
async def health():
    return {"ok": True, "apns": configured(), "devices": count()}


@app.post("/v1/register")
async def register(body: RegisterBody, request: Request):
    if body.bundle not in BUNDLES:
        raise HTTPException(400, "unknown app")
    if not _TOKEN.match(body.token or ""):
        raise HTTPException(400, "not an Apple device token")
    if body.env not in HOSTS:
        raise HTTPException(400, "env is production or sandbox")
    if not _allow(_registers, _client_ip(request), [(REGISTER_PER_HOUR, 3600)]):
        raise HTTPException(429, "too many registrations")
    return {"relay_id": register_token(body.token.lower(), body.bundle, body.env),
            "apns": configured()}


@app.post("/v1/unregister")
async def unregister(body: RelayIdBody):
    if _RELAY_ID.match(body.relay_id or ""):
        forget(body.relay_id)
    return {"ok": True}


@app.post("/v1/send")
async def send(body: SendBody):
    if not _RELAY_ID.match(body.relay_id or ""):
        raise HTTPException(400, "not a relay id")
    device = lookup(body.relay_id)
    if device is None:
        raise HTTPException(410, "unknown phone")
    if not configured():
        raise HTTPException(503, "the push relay has no Apple key yet")
    if not _allow(_sends, body.relay_id, [(PER_HOUR, 3600), (PER_DAY, 86400)]):
        raise HTTPException(429, "too many notifications for this phone")
    token, bundle, env = device
    try:
        status, reason = await deliver(token, bundle, env, apns_payload(body))
    except Exception as e:  # noqa: BLE001 — Apple unreachable is a 502, not a crash
        raise HTTPException(502, f"Apple did not answer ({type(e).__name__})")
    if status == 200:
        return {"ok": True}
    if status == 410 or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
        forget(body.relay_id)
        raise HTTPException(410, "the phone is no longer registered")
    if status == 429:
        raise HTTPException(429, "Apple asked to slow down")
    if status in (403,) or reason in ("InvalidProviderToken", "ExpiredProviderToken"):
        _jwt_cache["token"] = ""
        raise HTTPException(503, f"the relay's Apple key was refused ({reason})")
    raise HTTPException(502, f"Apple refused it ({status} {reason})")
