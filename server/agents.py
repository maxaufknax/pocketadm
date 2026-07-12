"""Sentinel — background agent loops.

The user configures loops (security watch, update watch, health digest, or a
custom prompt) with an interval. Each run executes a small autonomous agent
with read-only tools, produces a status + summary, stores it as a
notification (bell icon in the app) and can push it to an ntfy topic so the
phone buzzes even when the app is closed.
"""
import asyncio
import hashlib
import json
import re
import secrets
import time

import httpx

from . import ai, audit, config

NOTIF_FILE = config.DATA_DIR / "notifications.json"
MAX_NOTIFICATIONS = 120
LOOP_TOOLS = ["run_command", "read_file", "list_dir", "search_files", "fetch_url"]
MAX_LOOP_TURNS = 10

SENTINEL_SYSTEM = """You are Sentinel, the background monitoring agent of Helmsman on the \
user's server. You run unattended on a schedule — nobody is watching, so never ask \
questions. Investigate using your tools with READ-ONLY intent: inspect logs, states and \
configs, but never modify anything (no writes, no restarts, no installs).

{focus}

Compare against what is normal for a small self-hosted server. Be concise and concrete: \
name the affected service/file and what the user should do. If everything is fine, say so \
in 1-2 sentences — do not invent problems.{lang_note}

End your reply with EXACTLY these two lines (machine-parsed):
STATUS: ok|info|warn|crit
TITLE: <max 8 words summarizing the single most important finding>"""

PRESETS = {
    "security": {
        "name": "Security watch", "icon": "🛡", "interval_min": 360,
        "desc": "Watches for break-in attempts: failed SSH logins, fail2ban bans, "
                "newly exposed ports and containers restarting out of nowhere.",
        "focus": ("Focus: security. Check failed/successful SSH logins (last hours of "
                  "auth logs), fail2ban bans, unusual listening ports, containers that "
                  "restarted unexpectedly, and anything that looks like scanning or "
                  "brute-force noise from the internet."),
    },
    "updates": {
        "name": "Update watch", "icon": "⬆️", "interval_min": 1440,
        "desc": "Reviews pending image and system updates and flags which ones are "
                "security-relevant and worth applying soon.",
        "focus": ("Focus: updates. Review the pending updates listed in the context. "
                  "Which are security-relevant and should be applied soon? Anything "
                  "known-breaking? Keep it short — a prioritized mini-list."),
    },
    "health": {
        "name": "Health digest", "icon": "💚", "interval_min": 720,
        "desc": "A regular check-up: disk space, memory pressure, unhealthy or "
                "restarting containers and error bursts in your core services.",
        "focus": ("Focus: general health. Check disk space trends, memory pressure, "
                  "containers that are unhealthy/restarting, error bursts in logs of "
                  "core services. Summarize the server's condition like a daily digest."),
    },
    "custom": {
        "name": "Custom loop", "icon": "🔁", "interval_min": 720,
        "desc": "You decide what it watches — describe the job in plain words and the "
                "agent checks it on your schedule.",
        "focus": "",
    },
}

_scheduler_task: asyncio.Task | None = None
_running: set[str] = set()


# ------------------------------------------------------------- loop config

def get_loops() -> list[dict]:
    return config.settings.get("agent_loops", [])


def save_loops(loops: list[dict]) -> list[dict]:
    clean = []
    for lp in loops[:10]:
        preset = lp.get("preset") if lp.get("preset") in PRESETS else "custom"
        clean.append({
            "id": lp.get("id") or secrets.token_hex(4),
            "preset": preset,
            "name": (lp.get("name") or PRESETS[preset]["name"]).strip()[:40],
            "prompt": (lp.get("prompt") or "").strip()[:2000],
            "interval_min": max(30, int(lp.get("interval_min") or 360)),
            "enabled": bool(lp.get("enabled")),
            "ntfy_url": (lp.get("ntfy_url") or "").strip()[:200],
            "notify_min": lp.get("notify_min") if lp.get("notify_min")
            in ("all", "info", "warn", "crit") else "warn",
            "last_run": float(lp.get("last_run") or 0),
            "last_status": lp.get("last_status") or "",
            "last_title": (lp.get("last_title") or "")[:120],
        })
    config.settings["agent_loops"] = clean
    config.save_settings(config.settings)
    return clean


def _update_loop(loop_id: str, **fields) -> None:
    loops = get_loops()
    for lp in loops:
        if lp["id"] == loop_id:
            lp.update(fields)
    config.settings["agent_loops"] = loops
    config.save_settings(config.settings)


# ---------------------------------------------------------- notifications

def _load_notifications() -> list[dict]:
    try:
        return json.loads(NOTIF_FILE.read_text()) if NOTIF_FILE.exists() else []
    except Exception:
        return []


DEDUPE_WINDOW = 7 * 86400


def _fingerprint(source: str, status: str, title: str) -> str:
    norm = re.sub(r"[\W\d]+", " ", title.lower()).strip()
    return hashlib.sha1(f"{source}|{status}|{norm}".encode()).hexdigest()[:16]


def add_notification(source: str, status: str, title: str, body: str) -> dict:
    """Store a notification. If the newest notification from the same source
    reports the same finding (status + title), it is bumped instead of
    duplicated: `count` grows, `last_seen` updates, `time` (first seen — used
    for the unread badge) stays. The returned dict carries a transient
    `_repeat` flag so callers can mute pushes for repeats."""
    items = _load_notifications()
    fp = _fingerprint(source, status, title)
    prev = next((n for n in items if n["source"] == source), None)
    if prev and prev.get("fp") == fp and \
            time.time() - prev.get("last_seen", prev["time"]) < DEDUPE_WINDOW:
        prev["count"] = int(prev.get("count", 1)) + 1
        prev["last_seen"] = time.time()
        prev["body"] = body[:6000]
        items.remove(prev)
        items.insert(0, prev)
        NOTIF_FILE.write_text(json.dumps(items[:MAX_NOTIFICATIONS]))
        return {**prev, "_repeat": True}
    notif = {"id": secrets.token_hex(5), "time": time.time(), "source": source,
             "status": status, "title": title[:120], "body": body[:6000],
             "fp": fp, "count": 1, "last_seen": time.time()}
    NOTIF_FILE.write_text(json.dumps(([notif] + items)[:MAX_NOTIFICATIONS]))
    return notif


def _mark_pushed(notif_id: str) -> None:
    items = _load_notifications()
    for n in items:
        if n["id"] == notif_id:
            n["last_push"] = time.time()
    NOTIF_FILE.write_text(json.dumps(items[:MAX_NOTIFICATIONS]))


def notifications(limit: int = 50) -> dict:
    items = _load_notifications()[:limit]
    seen = float(config.settings.get("notifications_seen", 0))
    unseen = sum(1 for n in items if n["time"] > seen)
    return {"items": items, "unseen": unseen}


def mark_seen() -> None:
    config.settings["notifications_seen"] = time.time()
    config.save_settings(config.settings)


async def _push_ntfy(url: str, status: str, title: str, body: str) -> None:
    prio = {"crit": "urgent", "warn": "high", "info": "default"}.get(status, "min")
    tags = {"crit": "rotating_light", "warn": "warning", "info": "information_source",
            "ok": "white_check_mark"}.get(status, "bell")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(url, content=body[:3800].encode(),
                              headers={"Title": title.encode("ascii", "replace").decode(),
                                       "Priority": prio, "Tags": tags})
    except Exception:
        pass


# ------------------------------------------------------------- agent runs

async def _run_mini_agent(prompt: str, sysprompt: str) -> str:
    """Autonomous non-streaming agent loop with read-only tools."""
    default = config.get_ai_default()
    if not default["provider"]:
        raise RuntimeError("No AI key configured")
    cfg = ai._cfg_for(default["provider"], default["model"])
    messages: list[dict] = [{"role": "user", "content": prompt}]
    usage = {"input": 0, "output": 0}
    final = ""
    for _ in range(MAX_LOOP_TURNS):
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        thinking_blocks: list[dict] = []
        async for kind, payload in ai.get_stream(cfg, messages, sysprompt, LOOP_TOOLS):
            if kind == "text":
                text_parts.append(payload)
            elif kind == "tool_call":
                tool_calls.append(payload)
            elif kind == "thinking_block":
                thinking_blocks.append(payload)
            elif kind == "usage":
                usage["input"] += payload["input"]
                usage["output"] += payload["output"]
        msg: dict = {"role": "assistant", "content": "".join(text_parts),
                     "tool_calls": tool_calls}
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        if "".join(text_parts).strip():
            final = "".join(text_parts)
        if not tool_calls:
            break
        for tc in tool_calls:
            out = await ai.execute_tool(tc["name"], tc["args"], ai.DEFAULT_WORKDIR)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    cost = ai.estimate_cost(cfg["provider"], cfg["model"], usage["input"], usage["output"])
    ai._persist_usage(cfg, usage, cost)
    return final


def _parse_result(text: str) -> tuple[str, str, str]:
    """-> (status, title, body without the marker lines)"""
    status, title, body_lines = "info", "", []
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith("STATUS:"):
            val = s.split(":", 1)[1].strip().lower()
            if val in ("ok", "info", "warn", "crit"):
                status = val
        elif s.upper().startswith("TITLE:"):
            title = s.split(":", 1)[1].strip()
        else:
            body_lines.append(line)
    return status, title or "Sentinel report", "\n".join(body_lines).strip()


async def _loop_context() -> str:
    """Cheap context so the agent doesn't have to rediscover the basics."""
    parts = []
    try:
        from . import dockerapi, updates
        containers = await dockerapi.list_containers(all_=True)
        bad = [f"{c['name']} ({c['state']}{'/' + c['health'] if c['health'] else ''})"
               for c in containers
               if c["state"] != "running" or c["health"] == "unhealthy"]
        parts.append(f"Containers: {sum(c['state'] == 'running' for c in containers)}"
                     f"/{len(containers)} running"
                     + (f"; not healthy: {', '.join(bad[:12])}" if bad else ""))
        upd = [u for u in (await updates.check_docker_updates())
               if u["update_available"] and not u["ignored"]]
        if upd:
            parts.append("Pending image updates: " +
                         ", ".join(f"{u['label']} ({u['priority']})" for u in upd[:20]))
    except Exception:
        pass
    try:
        from . import reports
        r = reports.latest_report()
        if r:
            issues = [c["title"] + f" [{c['status']}]" for c in r.get("checks", [])
                      if c["status"] in ("warn", "crit")]
            parts.append(f"Last health report score: {r['score']}"
                         + (f"; issues: {'; '.join(issues[:10])}" if issues else ""))
    except Exception:
        pass
    return "\n".join(parts)


LANG_HINT = {"de": " Answer in German.", "en": ""}


async def run_loop(loop: dict, trigger: str = "schedule") -> dict | None:
    if loop["id"] in _running:
        return None
    _running.add(loop["id"])
    try:
        preset = PRESETS.get(loop.get("preset", "custom"), PRESETS["custom"])
        focus = loop.get("prompt") or preset["focus"] or \
            "Focus: whatever seems most important for this server right now."
        lang = config.settings.get("sentinel_lang", "")
        sysprompt = SENTINEL_SYSTEM.format(
            focus=focus, lang_note=LANG_HINT.get(lang, f" Answer in language: {lang}." if lang else ""))
        ctx = await _loop_context()
        prompt = (f"Scheduled run of loop \"{loop['name']}\" ({trigger}). "
                  f"Current server context:\n{ctx or '(no context available)'}\n\n"
                  "Investigate now and report.")
        try:
            text = await _run_mini_agent(prompt, sysprompt)
            status, title, body = _parse_result(text)
        except Exception as e:
            status, title, body = "warn", f"{loop['name']} failed", \
                f"The background loop could not run: {type(e).__name__}: {e}"
        notif = add_notification(loop["name"], status, title, body)
        repeat = notif.pop("_repeat", False)
        _update_loop(loop["id"], last_run=time.time(), last_status=status, last_title=title)
        audit.record("loop_run", target=loop["name"], source="sentinel",
                     status=status, detail=title + (" (repeat)" if repeat else ""))
        rank = {"all": 0, "ok": 0, "info": 1, "warn": 2, "crit": 3}
        wants_push = loop.get("ntfy_url") and \
            rank.get(status, 1) >= rank.get(loop.get("notify_min", "warn"), 2)
        # dedupe: a repeat of the same finding doesn't buzz the phone again —
        # except crit, which re-reminds at most once a day while it persists
        if wants_push and repeat:
            wants_push = status == "crit" and \
                time.time() - notif.get("last_push", 0) > 86400
        if wants_push:
            await _push_ntfy(loop["ntfy_url"], status, f"{loop['name']}: {title}", body)
            _mark_pushed(notif["id"])
        return notif
    finally:
        _running.discard(loop["id"])


# -------------------------------------------------------------- scheduler

def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        _scheduler_task = asyncio.ensure_future(_scheduler())


async def _scheduler() -> None:
    await asyncio.sleep(90)  # let the app settle after boot
    while True:
        try:
            for loop in get_loops():
                if not loop.get("enabled"):
                    continue
                due = loop.get("last_run", 0) + loop["interval_min"] * 60
                if time.time() >= due and loop["id"] not in _running:
                    asyncio.ensure_future(run_loop(loop))
        except Exception:
            pass
        await asyncio.sleep(60)
