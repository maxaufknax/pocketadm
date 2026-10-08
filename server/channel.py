"""The watch's channel: one conversation between the user and the watch.

What the watch writes on its own (a problem it found, a trend, the Sunday look
back) and what the user asks it ("why is the disk so full?", "stop telling me
about the backups for a week") live in one timeline, newest at the bottom — a
chat with someone who looks after the server, like a bot in a Matrix room.

    {"id", "t", "role": "watch" | "user" | "system", "text", "detail", "title",
     "importance", "topic", "kind", "actions", "feedback", "reply_to"}

The store is a small JSON file next to the other state. The first time it is
read it takes over the watch's earlier messages from the notifications, so an
upgrade does not start with an empty channel.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from . import config

CHANNEL_FILE = config.DATA_DIR / "watch_channel.json"
CAP = 500
ROLES = ("watch", "user", "system")

# while the watch is writing an answer: {"since": t, "to": message id}
replying: dict = {}


def _seed_from_notifications() -> list[dict]:
    try:
        from . import agents
        items = agents._load_notifications()
    except Exception:
        return []
    out = []
    for n in reversed(items):
        if n.get("source") != "watch":
            continue
        out.append({"id": "m" + n["id"], "t": n.get("time", time.time()), "role": "watch",
                    "text": n.get("body", ""), "detail": "", "title": n.get("title", ""),
                    "importance": n.get("importance") or {"crit": "critical", "warn": "important"}
                    .get(n.get("status"), "info"),
                    "topic": n.get("topic", ""), "kind": n.get("run", "observe"),
                    "actions": n.get("actions") or [], "feedback": n.get("feedback", ""),
                    "notification": n["id"], "reply_to": ""})
    return out


def _load() -> dict:
    try:
        data = json.loads(CHANNEL_FILE.read_text())
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data
    except FileNotFoundError:
        data = {"messages": _seed_from_notifications(), "read": 0}
        _save(data)
        return data
    except (OSError, ValueError):
        pass
    return {"messages": [], "read": 0}


def _save(data: dict) -> None:
    data["messages"] = data.get("messages", [])[-CAP:]
    try:
        tmp = CHANNEL_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, CHANNEL_FILE)
    except OSError:
        pass


def add(role: str, text: str, **fields) -> dict:
    data = _load()
    msg = {"id": "m" + secrets.token_hex(5), "t": round(time.time(), 3),
           "role": role if role in ROLES else "system", "text": (text or "").strip()[:6000],
           "detail": "", "title": "", "importance": "info", "topic": "", "kind": "",
           "actions": [], "feedback": "", "reply_to": ""}
    for key, value in fields.items():
        if key in msg and key not in ("id", "t", "role") and value is not None:
            msg[key] = value
        elif key == "notification":
            msg["notification"] = value
    # strictly increasing times: the app polls with ?after=<newest it has>,
    # and two messages in the same millisecond would hide the second one
    if data["messages"] and msg["t"] <= data["messages"][-1]["t"]:
        msg["t"] = round(data["messages"][-1]["t"] + 0.001, 3)
    data["messages"].append(msg)
    if role == "user":
        data["read"] = msg["t"]          # what you wrote yourself is never unread
    _save(data)
    return msg


def get(msg_id: str) -> dict | None:
    return next((m for m in _load()["messages"] if m["id"] == msg_id), None)


def update(msg_id: str, **fields) -> dict | None:
    data = _load()
    for m in data["messages"]:
        if m["id"] == msg_id:
            m.update({k: v for k, v in fields.items() if k not in ("id", "t", "role")})
            _save(data)
            return m
    return None


def delete(msg_id: str) -> bool:
    data = _load()
    kept = [m for m in data["messages"] if m["id"] != msg_id]
    if len(kept) == len(data["messages"]):
        return False
    data["messages"] = kept
    _save(data)
    return True


def clear() -> None:
    _save({"messages": [], "read": time.time()})


def mark_read(t: float | None = None) -> None:
    """Everything up to `t` (default: all there is) has been seen."""
    data = _load()
    newest = max((m["t"] for m in data["messages"]), default=0.0)
    data["read"] = max(float(data.get("read") or 0), t or max(time.time(), newest))
    _save(data)


def unread(data: dict | None = None) -> int:
    data = data or _load()
    seen = float(data.get("read") or 0)
    return sum(1 for m in data["messages"] if m["role"] != "user" and m["t"] > seen)


def page(after: float = 0.0, before: float = 0.0, limit: int = 60) -> dict:
    """Messages oldest first. `after` fetches what is new since the last look,
    `before` scrolls back; `more` says whether older messages exist."""
    data = _load()
    msgs = data["messages"]
    if after:
        chosen = [m for m in msgs if m["t"] > after][-limit:]
        more = False
    else:
        pool = [m for m in msgs if not before or m["t"] < before]
        chosen = pool[-limit:]
        more = len(pool) > len(chosen)
    return {"messages": chosen, "more": more, "unread": unread(data),
            "read": float(data.get("read") or 0),
            "replying": bool(replying) and time.time() - replying.get("since", 0) < 1800}


def conversation(limit: int = 16) -> list[dict]:
    """The recent exchange, for the watch to answer in context."""
    return [m for m in _load()["messages"] if m["role"] in ("watch", "user")][-limit:]


def unanswered() -> list[dict]:
    """User messages after the watch's last word."""
    out: list[dict] = []
    for m in reversed(_load()["messages"]):
        if m["role"] == "watch":
            break
        if m["role"] == "user":
            out.insert(0, m)
    return out
