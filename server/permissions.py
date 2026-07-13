"""Permission requests — when the agent hits a missing-right wall (host FS
read-only, needs root, no docker access…) we don't just hand the raw error back
to the model. We surface a plain-language, actionable request the user can see
under a Tasks/Tips section, dismiss, or resolve in a focused agent session.

Persisted append-style to /data/permission-requests.json. Identical requests
(same kind + target) are de-duplicated: the count grows instead of piling up.
"""
import hashlib
import json
import secrets
import time

from . import config

_FILE = config.DATA_DIR / "permission-requests.json"
_MAX = 100
_DEDUPE_WINDOW = 7 * 86400


def _load() -> list[dict]:
    try:
        return json.loads(_FILE.read_text()) if _FILE.exists() else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    try:
        _FILE.write_text(json.dumps(items[:_MAX]))
    except Exception:
        pass


def _fingerprint(kind: str, detail: str) -> str:
    return hashlib.sha1(f"{kind}|{detail.strip().lower()}".encode()).hexdigest()[:16]


def add(kind: str, title: str, detail: str, explanation: str = "", risk: str = "",
        fix: str = "", chat_id: str = "") -> dict:
    """Record (or bump) a permission request. Returns the stored entry with a
    transient `_new` flag = True the first time it appears (callers push only
    then, so repeats stay quiet)."""
    items = _load()
    fp = _fingerprint(kind, detail)
    prev = next((p for p in items if p.get("fp") == fp and p.get("status") == "open"), None)
    now = time.time()
    if prev and now - prev.get("last_seen", prev["time"]) < _DEDUPE_WINDOW:
        prev["count"] = int(prev.get("count", 1)) + 1
        prev["last_seen"] = now
        if chat_id:
            prev["chat_id"] = chat_id
        items.remove(prev)
        items.insert(0, prev)
        _save(items)
        return {**prev, "_new": False}
    entry = {
        "id": secrets.token_hex(5), "time": now, "last_seen": now, "count": 1,
        "status": "open", "fp": fp, "kind": kind, "title": title[:120],
        "detail": detail[:200], "explanation": explanation[:600], "risk": risk[:400],
        "fix": fix[:400], "chat_id": chat_id,
    }
    _save([entry] + items)
    return {**entry, "_new": True}


def list_all(include_closed: bool = False) -> list[dict]:
    items = _load()
    if not include_closed:
        items = [p for p in items if p.get("status") == "open"]
    return items


def open_count() -> int:
    return sum(1 for p in _load() if p.get("status") == "open")


def set_status(req_id: str, status: str) -> bool:
    items = _load()
    hit = False
    for p in items:
        if p["id"] == req_id:
            p["status"] = status
            hit = True
    if hit:
        _save(items)
    return hit
