"""Push notifications to the phones signed in to this server.

The iPhone app registers its Apple push token with the PocketADM push relay
(pocketadm.com/push) and gets back a relay id: a random capability that lets
whoever holds it send a notification to that one phone, and nothing else. The
app hands the relay id to every server it is signed in to. This module keeps
those ids and sends through the relay. The relay holds the Apple key; the server
never sees the raw device token, and the relay never learns which server a
phone belongs to.

Delivery is best effort and never blocks the caller. An id the relay no longer
knows (the app was deleted, the token expired) is dropped when it says so.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time

import httpx

from . import config

DEVICES_FILE = config.DATA_DIR / "push_devices.json"
DEFAULT_RELAY = "https://pocketadm.com/push"
IMPORTANCE = ("info", "important", "critical")
RANK = {"info": 0, "important": 1, "critical": 2}
MAX_DEVICES = 20
_RELAY_ID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

# what the last attempt said, per device id (shown in the app's push settings)
_last: dict[str, dict] = {}


def relay_url() -> str:
    return (os.environ.get("POCKETADM_PUSH_RELAY") or config.settings.get("push_relay")
            or DEFAULT_RELAY).rstrip("/")


def _load() -> list[dict]:
    try:
        data = json.loads(DEVICES_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(devices: list[dict]) -> None:
    try:
        DEVICES_FILE.write_text(json.dumps(devices[-MAX_DEVICES:]))
        os.chmod(DEVICES_FILE, 0o600)
    except OSError:
        pass


def _public(device: dict) -> dict:
    out = {k: device.get(k) for k in ("id", "name", "platform", "added", "min", "assistant",
                                      "preview", "last_ok")}
    last = _last.get(device["id"]) or {}
    out["last_error"] = last.get("error", "")
    return out


def devices() -> list[dict]:
    return [_public(d) for d in _load()]


def status() -> dict:
    return {"relay": relay_url(), "devices": devices()}


def register(relay_id: str, name: str = "", platform: str = "ios", min_importance: str = "info",
             assistant: bool = True, preview: bool = True) -> dict:
    """Add a phone, or update it when it is already known (same relay id)."""
    relay_id = (relay_id or "").strip()
    if not _RELAY_ID.match(relay_id):
        raise ValueError("not a relay id")
    items = _load()
    device = next((d for d in items if d.get("relay_id") == relay_id), None)
    if device is None:
        device = {"id": secrets.token_hex(4), "relay_id": relay_id, "added": time.time()}
        items.append(device)
    device.update(name=(name or "iPhone").strip()[:60], platform=platform[:12] or "ios",
                  min=min_importance if min_importance in IMPORTANCE else "info",
                  assistant=bool(assistant), preview=bool(preview))
    _save(items)
    return _public(device)


def update(device_id: str, **changes) -> dict | None:
    items = _load()
    device = next((d for d in items if d["id"] == device_id), None)
    if device is None:
        return None
    if changes.get("min") in IMPORTANCE:
        device["min"] = changes["min"]
    for key in ("assistant", "preview"):
        if key in changes and changes[key] is not None:
            device[key] = bool(changes[key])
    if changes.get("name"):
        device["name"] = str(changes["name"]).strip()[:60]
    _save(items)
    return _public(device)


def remove(device_id: str = "", relay_id: str = "") -> bool:
    items = _load()
    kept = [d for d in items if d["id"] != device_id and (not relay_id or d.get("relay_id") != relay_id)]
    if len(kept) == len(items):
        return False
    _save(kept)
    return True


def wants(device: dict, kind: str, importance: str) -> bool:
    """Whether this phone asked for this kind of message."""
    if kind == "assistant":
        return bool(device.get("assistant", True))
    return RANK.get(importance, 0) >= RANK.get(device.get("min") or "info", 0)


def payload_for(device: dict, title: str, body: str, *, kind: str, importance: str,
                thread: str, data: dict | None) -> dict:
    name = config.get_server_name() or "PocketADM"
    if not device.get("preview", True):
        # the relay passes text through; with previews off it only ever sees this
        title, body = name, "New message from your server."
    return {
        "relay_id": device["relay_id"],
        "title": title[:120],
        "subtitle": name[:60] if device.get("preview", True) else "",
        "body": body[:900],
        "thread": thread[:64],
        "category": kind,
        "level": "time-sensitive" if importance == "critical" else "active",
        "sound": importance != "info" or kind == "assistant",
        "data": {**(data or {}), "kind": kind, "server": name},
    }


async def _post(client: httpx.AsyncClient, device: dict, payload: dict) -> int:
    try:
        r = await client.post(relay_url() + "/v1/send", json=payload)
        code = r.status_code
        detail = ""
        if code >= 400:
            try:
                detail = (r.json() or {}).get("detail", "")
            except ValueError:
                detail = r.text[:120]
    except Exception as e:  # noqa: BLE001 — a dead relay never breaks the caller
        code, detail = 0, type(e).__name__
    if code == 200:
        _last[device["id"]] = {"t": time.time(), "error": ""}
    else:
        _last[device["id"]] = {"t": time.time(), "error": detail or f"HTTP {code}"}
    return code


async def send(title: str, body: str, *, kind: str = "watch", importance: str = "info",
               thread: str = "watch", data: dict | None = None) -> dict:
    """Send to every phone that wants this message. Returns what happened."""
    if config.DEMO:
        return {"sent": 0, "failed": 0, "removed": 0}
    targets = [d for d in _load() if wants(d, kind, importance)]
    if not targets:
        return {"sent": 0, "failed": 0, "removed": 0}
    sent = failed = 0
    gone: list[str] = []
    async with httpx.AsyncClient(timeout=12) as client:
        for device in targets:
            code = await _post(client, device, payload_for(
                device, title, body, kind=kind, importance=importance, thread=thread, data=data))
            if code == 200:
                sent += 1
                device["last_ok"] = time.time()
            elif code == 410:
                gone.append(device["id"])
            else:
                failed += 1
    items = _load()
    by_id = {d["id"]: d for d in targets}
    for d in items:
        if d["id"] in by_id and by_id[d["id"]].get("last_ok"):
            d["last_ok"] = by_id[d["id"]]["last_ok"]
    _save([d for d in items if d["id"] not in gone])
    return {"sent": sent, "failed": failed, "removed": len(gone)}


def notify(title: str, body: str, **kwargs) -> None:
    """Fire and forget: schedule a send on the running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    asyncio.ensure_future(_quiet(send(title, body, **kwargs)))


async def _quiet(coro) -> None:
    try:
        await coro
    except Exception:
        pass
