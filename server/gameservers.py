"""Game servers on this host — Minecraft (Java edition) for now — watched the way
a player's launcher sees them: a server list ping every 30 seconds.

The ping answers whether the server is up, its version and message of the day,
and who is online (the list the server chooses to share, at most a dozen
names). From the difference between two looks this module tells:

  someone joined   → activity feed (games), a notification, and a push
  someone left     → activity feed only
  server up/down   → activity feed only (stopping a game server is usually
                     on purpose, so it is not worth waking a phone for)

Configured in settings["gameservers"]: a list of {name, host, port, notify}.
Nothing is configured by default — a server without game servers pings no one.
"""
from __future__ import annotations

import asyncio
import json
import re
import struct
import time

from . import config

INTERVAL = 30            # seconds between looks
FAILS_FOR_DOWN = 3       # a missed ping is a GC pause, three in a row is down
MAX_SERVERS = 6
PING_TIMEOUT = 4.0
MAX_RESPONSE = 256 * 1024
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$")

# per server id: what the last looks said
_state: dict[str, dict] = {}
_task: asyncio.Task | None = None


# ------------------------------------------------------------------ settings

def _sid(entry: dict) -> str:
    return f"{entry['host']}:{entry['port']}"


def validate(entries: list) -> list[dict]:
    """Clean a list from the API; raises ValueError with a readable reason."""
    if not isinstance(entries, list):
        raise ValueError("servers must be a list")
    if len(entries) > MAX_SERVERS:
        raise ValueError(f"at most {MAX_SERVERS} game servers")
    out: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("each server is an object")
        host = str(raw.get("host", "")).strip()
        if not _HOST.match(host):
            raise ValueError(f"not a host name or address: {host!r}")
        try:
            port = int(raw.get("port", 25565))
        except (TypeError, ValueError):
            raise ValueError("port must be a number")
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        name = re.sub(r"\s+", " ", str(raw.get("name") or host)).strip()[:40] or host
        entry = {"name": name, "host": host, "port": port, "notify": raw.get("notify", True) is not False}
        if any(_sid(e) == _sid(entry) for e in out):
            raise ValueError(f"{host}:{port} is listed twice")
        out.append(entry)
    return out


def servers() -> list[dict]:
    try:
        return validate(config.settings.get("gameservers") or [])
    except ValueError:
        return []


def set_servers(entries: list) -> list[dict]:
    clean = validate(entries)
    config.settings["gameservers"] = clean
    config.save_settings(config.settings)
    keep = {_sid(e) for e in clean}
    for sid in list(_state):
        if sid not in keep:
            del _state[sid]
    return clean


# ------------------------------------------------------------------ the ping

def _varint(n: int) -> bytes:
    out = b""
    n &= 0xFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


async def _read_varint(reader: asyncio.StreamReader) -> int:
    n = 0
    for i in range(5):
        b = (await reader.readexactly(1))[0]
        n |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return n
    raise ValueError("varint too long")


def _text(desc) -> str:
    """A chat component (or plain string) as plain text, colour codes removed."""
    if isinstance(desc, str):
        s = desc
    elif isinstance(desc, dict):
        s = str(desc.get("text", "")) + "".join(_text(x) for x in desc.get("extra") or [])
    elif isinstance(desc, list):
        s = "".join(_text(x) for x in desc)
    else:
        s = ""
    return re.sub(r"§.", "", s)


def parse_status(data: dict) -> dict:
    players = data.get("players") if isinstance(data.get("players"), dict) else {}
    sample = players.get("sample") if isinstance(players.get("sample"), list) else []
    names = [str(p.get("name"))[:32] for p in sample if isinstance(p, dict) and p.get("name")]
    version = data.get("version") if isinstance(data.get("version"), dict) else {}
    return {
        "version": str(version.get("name", ""))[:60],
        "motd": _text(data.get("description")).strip()[:200],
        "online": int(players.get("online") or 0),
        "max": int(players.get("max") or 0),
        "names": sorted(set(names), key=str.lower),
    }


async def ping(host: str, port: int, timeout: float = PING_TIMEOUT) -> dict:
    """Server list ping (the launcher's), modern protocol. Raises on failure."""
    async def run() -> dict:
        start = time.monotonic()
        reader, writer = await asyncio.open_connection(host.strip("[]"), port)
        try:
            addr = host.encode()[:255]
            hs = _varint(0) + _varint(767) + _varint(len(addr)) + addr + struct.pack(">H", port) + _varint(1)
            writer.write(_varint(len(hs)) + hs + _varint(1) + _varint(0))
            await writer.drain()
            length = await _read_varint(reader)
            if not 0 < length <= MAX_RESPONSE:
                raise ValueError("bad length")
            body = await reader.readexactly(length)
            latency = round((time.monotonic() - start) * 1000)
        finally:
            writer.close()
        pos = 0
        def varint_at():
            nonlocal pos
            n = 0
            for i in range(5):
                b = body[pos]; pos += 1
                n |= (b & 0x7F) << (7 * i)
                if not b & 0x80:
                    return n
            raise ValueError("varint too long")
        if varint_at() != 0:
            raise ValueError("not a status response")
        size = varint_at()
        status = parse_status(json.loads(body[pos:pos + size].decode("utf-8", "replace")))
        status["latency_ms"] = latency
        return status
    return await asyncio.wait_for(run(), timeout)


# ------------------------------------------------------------------ events

def _event(kind: str, title: str, detail: str = "", severity: str = "info", target: str = "") -> None:
    try:
        from . import activity
        activity.push("games", kind, title, detail, severity=severity, target=target, source="gameservers")
    except Exception:
        pass


def _joined(server: dict, names: list[str]) -> None:
    who = ", ".join(names)
    title = f"{who} {'ist' if len(names) == 1 else 'sind'} auf {server['name']}"
    _event("game.join", title, target=server["name"])
    if not server.get("notify", True):
        return
    try:
        from . import agents, push
        st = _state.get(_sid(server), {})
        body = f"{st.get('online', len(names))} von {st.get('max', '?')} online"
        if st.get("names"):
            body += ": " + ", ".join(st["names"])
        notif = agents.add_notification("gameservers", "info", title, body)
        agents.annotate_notification(notif["id"], kind="game", importance="info", topic="gameserver")
        push.notify(title, body, kind="game", importance="info", thread="gameserver",
                    data={"server": server["name"]})
    except Exception:
        pass


def observe(server: dict, result: dict | None, now: float | None = None) -> None:
    """Fold one ping result (None = failed) into the state and emit events."""
    now = now or time.time()
    sid = _sid(server)
    st = _state.setdefault(sid, {"up": None, "fails": 0, "names": [], "online": 0, "max": 0,
                                 "since": None, "seen": {}})
    st["checked"] = now
    if result is None:
        st["fails"] += 1
        if st["up"] and st["fails"] >= FAILS_FOR_DOWN:
            st.update(up=False, since=now, names=[], online=0, latency_ms=None)
            _event("game.down", f"{server['name']} ist offline", target=server["name"])
        elif st["up"] is None and st["fails"] >= FAILS_FOR_DOWN:
            st.update(up=False, since=now)
        return
    first = st["up"] is None
    was_up = st["up"]
    before = set(st["names"])
    st.update(up=True, fails=0, version=result["version"], motd=result["motd"], online=result["online"],
              max=result["max"], names=result["names"], latency_ms=result.get("latency_ms"))
    if not was_up:
        st["since"] = now
        if was_up is False:
            _event("game.up", f"{server['name']} ist online", result["version"], target=server["name"])
    for n in result["names"]:
        st["seen"][n] = now
    if first:
        return  # what was going on before we looked is not news
    joined = sorted(set(result["names"]) - before, key=str.lower)
    left = sorted(before - set(result["names"]), key=str.lower)
    if joined:
        _joined(server, joined)
    for n in left:
        _event("game.leave", f"{n} hat {server['name']} verlassen", target=server["name"])


def overview() -> dict:
    out = []
    for s in servers():
        st = _state.get(_sid(s), {})
        out.append({
            **s,
            "id": _sid(s),
            "up": st.get("up"),
            "since": st.get("since"),
            "checked": st.get("checked"),
            "version": st.get("version", ""),
            "motd": st.get("motd", ""),
            "online": st.get("online", 0),
            "max": st.get("max", 0),
            "players": st.get("names", []),
            "latency_ms": st.get("latency_ms"),
            "last_seen": dict(sorted(st.get("seen", {}).items(), key=lambda kv: -kv[1])[:30]),
        })
    return {"servers": out, "interval": INTERVAL}


# ------------------------------------------------------------------ loop

async def tick() -> None:
    for s in servers():
        try:
            result = await ping(s["host"], s["port"])
        except Exception:
            result = None
        observe(s, result)


async def _loop() -> None:
    while True:
        try:
            await tick()
        except Exception:
            pass
        await asyncio.sleep(INTERVAL)


def start() -> None:
    global _task
    if config.DEMO or (_task and not _task.done()):
        return
    _task = asyncio.ensure_future(_loop())
