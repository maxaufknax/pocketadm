"""Persistent chat conversations for Vibe Code.

Each chat is one JSON file under DATA_DIR/chats/<id>.json holding the full
internal message history (so a conversation survives reloads/reconnects and
can be resumed on any device) plus display metadata.
"""
import json
import secrets
import time

from . import config

CHATS_DIR = config.DATA_DIR / "chats"
CHATS_DIR.mkdir(exist_ok=True)

MAX_CHATS = 200
DEFAULT_TITLE = "New chat"


def _path(chat_id: str):
    if not chat_id or not all(c.isalnum() for c in chat_id):
        raise ValueError("bad chat id")
    return CHATS_DIR / f"{chat_id}.json"


def create() -> dict:
    chat = {
        "id": secrets.token_hex(8),
        "title": DEFAULT_TITLE,
        "created": time.time(),
        "updated": time.time(),
        "archived": False,
        "messages": [],
        "usage": {"input": 0, "output": 0, "cost": 0.0, "turns": 0},
    }
    save(chat)
    _prune()
    return chat


def save(chat: dict) -> None:
    chat["updated"] = time.time()
    _path(chat["id"]).write_text(json.dumps(chat))


def load(chat_id: str) -> dict | None:
    try:
        p = _path(chat_id)
    except ValueError:
        return None
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def list_chats() -> list[dict]:
    out = []
    for f in CHATS_DIR.glob("*.json"):
        try:
            c = json.loads(f.read_text())
        except Exception:
            continue
        out.append({
            "id": c.get("id", f.stem),
            "title": c.get("title", DEFAULT_TITLE),
            "created": c.get("created", 0),
            "updated": c.get("updated", 0),
            "archived": bool(c.get("archived")),
            "message_count": sum(1 for m in c.get("messages", [])
                                 if m.get("role") in ("user", "assistant")),
        })
    return sorted(out, key=lambda c: -c["updated"])


def delete(chat_id: str) -> None:
    try:
        _path(chat_id).unlink(missing_ok=True)
    except ValueError:
        pass


def set_archived(chat_id: str, archived: bool) -> bool:
    c = load(chat_id)
    if not c:
        return False
    c["archived"] = archived
    save(c)
    return True


def rename(chat_id: str, title: str) -> bool:
    c = load(chat_id)
    if not c:
        return False
    c["title"] = title.strip()[:80] or DEFAULT_TITLE
    save(c)
    return True


def title_from(text: str) -> str:
    """Derive a chat title from the first user message."""
    t = " ".join(text.split())
    return (t[:56] + "…") if len(t) > 56 else (t or DEFAULT_TITLE)


def display_events(messages: list[dict]) -> list[dict]:
    """Flatten internal message history into render-ready events."""
    outputs = {m.get("tool_call_id"): m.get("content", "")
               for m in messages if m.get("role") == "tool"}
    events: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                events.append({"t": "user", "text": content})
        elif role == "assistant":
            if m.get("content"):
                events.append({"t": "assistant", "text": m["content"]})
            for tc in m.get("tool_calls", []) or []:
                events.append({"t": "tool", "name": tc.get("name", "?"),
                               "args": tc.get("args", {}),
                               "output": (outputs.get(tc.get("id"), "") or "")[:2000]})
    return events


def _prune() -> None:
    files = sorted(CHATS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for f in files[:-MAX_CHATS]:
        f.unlink(missing_ok=True)
