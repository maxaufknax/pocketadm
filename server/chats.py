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
    # In the public demo, visitor chats stay in memory only: the list keeps
    # showing just the curated sample, and one visitor's messages never persist
    # or leak to the next. The seeded sample is written directly by demodata.
    if config.DEMO:
        return
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


def _summary(c: dict, fallback_id: str = "") -> dict:
    messages = c.get("messages", [])
    first = next((m["content"] for m in messages
                  if m.get("role") == "user" and isinstance(m.get("content"), str)
                  and m["content"].strip()), "")
    last = next((m["content"] for m in reversed(messages)
                 if m.get("role") == "assistant" and m.get("content")), "")
    return {
        "id": c.get("id", fallback_id),
        "title": c.get("title", DEFAULT_TITLE),
        "created": c.get("created", 0),
        "updated": c.get("updated", 0),
        "archived": bool(c.get("archived")),
        "pinned": bool(c.get("pinned")),
        "message_count": sum(1 for m in messages if m.get("role") in ("user", "assistant")),
        "tool_count": sum(len(m.get("tool_calls") or []) for m in messages
                          if m.get("role") == "assistant"),
        "preview": " ".join((last or first).split())[:140],
    }


def list_chats(query: str = "") -> list[dict]:
    """Every chat, pinned first, then newest. With a query, only chats whose
    title or messages contain it, each with the matching passage."""
    needle = query.strip().lower()
    out = []
    for f in CHATS_DIR.glob("*.json"):
        try:
            c = json.loads(f.read_text())
        except Exception:
            continue
        row = _summary(c, f.stem)
        if needle:
            hit = _find(c, needle)
            if hit is None:
                continue
            row["snippet"] = hit
        out.append(row)
    return sorted(out, key=lambda c: (not c["pinned"], -c["updated"]))


def _find(chat: dict, needle: str) -> str | None:
    if needle in (chat.get("title") or "").lower():
        return ""
    for m in chat.get("messages", []):
        text = m.get("content")
        if m.get("role") not in ("user", "assistant") or not isinstance(text, str):
            continue
        i = text.lower().find(needle)
        if i >= 0:
            start = max(0, i - 50)
            return ("…" if start else "") + " ".join(text[start:i + len(needle) + 70].split())
    return None


def set_pinned(chat_id: str, pinned: bool) -> bool:
    c = load(chat_id)
    if not c:
        return False
    c["pinned"] = pinned
    _save_quiet(c)
    return True


def _save_quiet(chat: dict) -> None:
    """Save without moving the chat to the top: pinning is not a new message."""
    if config.DEMO:
        return
    _path(chat["id"]).write_text(json.dumps(chat))


def export_markdown(chat_id: str) -> str | None:
    """The conversation as Markdown, for the share sheet."""
    c = load(chat_id)
    if not c:
        return None
    lines = [f"# {c.get('title') or DEFAULT_TITLE}", ""]
    outputs = {m.get("tool_call_id"): m.get("content", "")
               for m in c.get("messages", []) if m.get("role") == "tool"}
    for m in c.get("messages", []):
        role = m.get("role")
        if role == "user" and isinstance(m.get("content"), str) and m["content"].strip():
            lines += ["**You:**", "", m["content"].strip(), ""]
        elif role == "assistant":
            if m.get("content"):
                lines += ["**Assistant:**", "", m["content"].strip(), ""]
            for tc in m.get("tool_calls") or []:
                args = tc.get("args") or {}
                what = args.get("command") or args.get("path") or args.get("url") or ""
                lines.append(f"> `{tc.get('name', 'tool')}` {what}".rstrip())
                out = (outputs.get(tc.get("id"), "") or "").strip()
                if out:
                    lines += ["", "```", out[:1500], "```"]
                lines.append("")
    return "\n".join(lines).strip() + "\n"


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
    _save_quiet(c)
    return True


def title_from(text: str) -> str:
    """Derive a chat title from the first user message."""
    t = " ".join(text.split())
    return (t[:56] + "…") if len(t) > 56 else (t or DEFAULT_TITLE)


CONTEXT_MARK = "[Attached context"


def _without_context(content: str, m: dict) -> str:
    """A message as the person wrote it: the context the app attached in
    front of it (files, services …) shows as chips, not as text."""
    if m.get("attachments") and content.startswith(CONTEXT_MARK):
        cut = content.rfind("\n\n[/Attached context]\n\n")
        if cut >= 0:
            return content[cut + len("\n\n[/Attached context]\n\n"):]
    return content


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
                event = {"t": "user", "text": _without_context(content, m)}
                if m.get("attachments"):
                    event["attachments"] = m["attachments"]
                events.append(event)
        elif role == "assistant":
            if m.get("content"):
                events.append({"t": "assistant", "text": m["content"], "by": m.get("by", "")})
            for tc in m.get("tool_calls", []) or []:
                events.append({"t": "tool", "name": tc.get("name", "?"),
                               "args": tc.get("args", {}),
                               "output": (outputs.get(tc.get("id"), "") or "")[:2000]})
    return events


def _prune() -> None:
    files = sorted(CHATS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for f in files[:-MAX_CHATS]:
        f.unlink(missing_ok=True)
