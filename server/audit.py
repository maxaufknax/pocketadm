"""Audit log — an append-only record of who did what on the server.

Every state-changing action (logins, container control, updates, config
changes, AI agent tool calls, Sentinel runs, terminal sessions) is written
as one JSON line to DATA_DIR/audit.jsonl. This answers the question that
matters most for a root-level tool people run unattended: *what happened,
and did the AI touch anything overnight?*
"""
import json
import time

from . import config

LOG_FILE = config.DATA_DIR / "audit.jsonl"
MAX_LINES = 5000          # rotate: keep the newest N events
_TRIM_AT = 6000

# Friendly labels + icons per action, for the UI.
ACTIONS = {
    "login":            ("🔓", "Signed in"),
    "login_failed":     ("⛔", "Failed sign-in"),
    "logout_all":       ("🚪", "Signed out all sessions"),
    "password_change":  ("🔑", "Password changed"),
    "user_password":    ("🔑", "User password changed"),
    "user_lock":        ("🔒", "User account locked"),
    "user_admin":       ("👑", "Admin rights changed"),
    "user_create":      ("👤", "User account created"),
    "2fa_enable":       ("🛡", "2FA enabled"),
    "2fa_disable":      ("🛡", "2FA disabled"),
    "container_action": ("🐳", "Container control"),
    "container_remove": ("🗑", "Container removed"),
    "cli_install":      ("❯_", "Coding agent installed"),
    "terminal_kill":    ("❯_", "Terminal session ended"),
    "update_apply":     ("⬆️", "Update applied"),
    "app_install":      ("◲", "App installed"),
    "app_uninstall":    ("◲", "App removed"),
    "integration_save": ("🔌", "Integration saved"),
    "integration_delete": ("🔌", "Integration removed"),
    "loop_save":        ("🛰", "Agent loops changed"),
    "loop_run":         ("🛰", "Agent loop ran"),
    "agent_tool":       ("🤖", "AI action"),
    "terminal":         ("❯_", "Terminal session"),
    "settings":         ("⚙️", "Settings changed"),
}


def record(action: str, target: str = "", source: str = "ui",
           detail: str = "", status: str = "ok", actor: str = "admin") -> None:
    """Append one event. Never raises — auditing must not break a request."""
    try:
        entry = {"t": round(time.time(), 3), "action": action, "target": target[:200],
                 "source": source, "detail": detail[:500], "status": status, "actor": actor}
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _maybe_trim()
    except Exception:
        pass


def _maybe_trim() -> None:
    try:
        # cheap check: only rewrite when clearly over budget
        with LOG_FILE.open("rb") as f:
            f.seek(0, 2)
            if f.tell() < 400 * _TRIM_AT:   # ~<400 bytes/line heuristic
                return
        lines = LOG_FILE.read_text(errors="replace").splitlines()
        if len(lines) > _TRIM_AT:
            LOG_FILE.write_text("\n".join(lines[-MAX_LINES:]) + "\n")
    except Exception:
        pass


def recent(limit: int = 100, action: str = "", source: str = "",
           before: float = 0) -> dict:
    """Newest-first events, optionally filtered by action/source and paginated
    with a `before` timestamp cursor."""
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return {"events": [], "cursor": None, "meta": _meta()}
    out = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if action and e.get("action") != action:
            continue
        if source and e.get("source") != source:
            continue
        if before and e.get("t", 0) >= before:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    cursor = out[-1]["t"] if len(out) >= limit else None
    return {"events": out, "cursor": cursor, "meta": _meta()}


def _meta() -> dict:
    return {"actions": {k: {"icon": v[0], "label": v[1]} for k, v in ACTIONS.items()}}
