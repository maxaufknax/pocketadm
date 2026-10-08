"""PocketADM's own records, for the assistant.

PocketADM already knows most answers people ask the assistant for: which
updates ran and how they ended, what happened on the server today, how full
the disks are and how that changed, what the last health check found. Real
chats showed the assistant hunting for that with twenty shell commands
(`find / -name "*update*"`, grepping container logs) and then guessing —
"the update returned HTTP 200, so it worked". This module answers from the
records themselves, compactly, so a model can read them in one step:

  * the built-in agent calls it as the `pocketadm` tool;
  * Claude Code, Codex and Vibe run `pocketadm <topic>` (server/cli.py).

Every topic returns plain text meant for a model: dates in local time, sizes
in GB, newest first, nothing that is a secret.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

TOPICS = {
    "overview": "containers, disks, pending updates, last health check and notable events",
    "updates": "pending image updates and every update run with its result",
    "activity": "what happened on the server (containers, logins, packages, alerts)",
    "audit": "what was done through PocketADM, by whom",
    "health": "the last health check: findings and recommendations",
    "metrics": "CPU, memory, disk and load over time, with the trend",
    "storage": "drives and mounts with size, used and free",
    "jobs": "long operations (updates, installs) with their log",
    "watch": "the watch's recent messages",
}

JOB_HISTORY = config.DATA_DIR / "jobs-history.jsonl"
METRICS_LONG = config.DATA_DIR / "metrics-long.json"

# the assistant's own command runners and terminals, not the user's services
HELPER_PREFIXES = ("pocketadm-exec-", "pocketadm-write-", "pocketadm-host-", "pocketadm-term-")


def is_helper_name(name: str) -> bool:
    return (name or "").startswith(HELPER_PREFIXES)


def _when(t: float) -> str:
    return time.strftime("%a %d.%m %H:%M", time.localtime(t)) if t else "?"


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _since(days: float) -> float:
    return time.time() - max(0.05, days) * 86400


# ------------------------------------------------------------------ topics

def _audit_rows(days: float, limit: int, actions: tuple = ()) -> list[dict]:
    from . import audit
    out = []
    for e in audit.recent(limit=2000)["events"]:
        if e.get("t", 0) < _since(days):
            break
        if actions and e.get("action") not in actions:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def job_history(days: float = 30, kinds: tuple = ()) -> list[dict]:
    """Finished jobs (persisted, newest first) plus the ones still in memory."""
    from . import jobs
    rows: dict[str, dict] = {}
    try:
        for line in JOB_HISTORY.read_text(errors="replace").splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            rows[j.get("id", "")] = j
    except OSError:
        pass
    for j in list(getattr(jobs, "_jobs", {}).values()):
        rows[j.id] = j.as_dict(tail=30)
    out = [j for j in rows.values() if j.get("created", 0) >= _since(days)
           and (not kinds or j.get("kind") in kinds)]
    return sorted(out, key=lambda j: -j.get("created", 0))


def _job_text(j: dict, tail: int = 8) -> str:
    took = ""
    if j.get("finished") and j.get("created"):
        took = f", took {int(j['finished'] - j['created'])} s"
    head = f"- {_when(j.get('created', 0))} {j.get('title', '?')}: {j.get('status', '?')}{took}"
    lines = [ln for ln in (j.get("log_tail") or []) if ln.strip()][-tail:]
    if lines:
        head += "\n" + "\n".join("    " + ln[:200] for ln in lines)
    return head


async def _updates(days: float, limit: int) -> str:
    from . import dockerapi, updates
    parts = []
    pending = updates._cache.get("result") or []
    ready = [u for u in pending if u.get("update_available") and not u.get("ignored")]
    if ready:
        parts.append(f"Pending image updates ({len(ready)}): " +
                     ", ".join(u.get("image", "?") for u in ready[:20]))
    elif pending:
        parts.append("No image updates pending (last check "
                     f"{_when(updates._cache.get('time', 0))}).")
    else:
        parts.append("Image updates have not been checked since PocketADM started.")
    runs = _audit_rows(days, limit, ("update_apply", "snapshot_rollback"))
    if runs:
        parts.append(f"Update runs in the last {days:g} days (newest first):")
        for e in runs:
            what = e.get("detail") or e.get("target", "")
            parts.append(f"- {_when(e['t'])} {e['action'].replace('_', ' ')}: {what[:300]} "
                         f"(started by {e.get('actor') or e.get('source', '?')})")
    else:
        parts.append(f"No updates were applied through PocketADM in the last {days:g} days.")
    done = job_history(days, ("update", "update-all", "update_all"))
    if done:
        parts.append("Their jobs and how they ended:")
        parts += [_job_text(j) for j in done[:limit]]
    # how the updated services are doing now
    try:
        containers = await dockerapi.list_containers(all_=True)
    except Exception:
        containers = []
    if containers and runs:
        touched = set()
        for e in runs:
            for ref in (e.get("detail") or e.get("target") or "").split(","):
                touched.add(ref.strip().split(":")[0])
        rows = [c for c in containers if c["image"].split(":")[0] in touched]
        if rows:
            parts.append("Those services now:")
            for c in rows[:30]:
                health = f", {c['health']}" if c.get("health") else ""
                parts.append(f"- {c['name']} ({c['image']}): {c['state']}{health} — {c['status']}")
    return "\n".join(parts)


def _activity(days: float, limit: int, search: str) -> str:
    from . import activity
    out = []
    needle = search.lower()
    for e in activity.recent(limit=1500)["events"]:
        if e.get("t", 0) < _since(days):
            break
        if is_helper_name(e.get("target", "")):
            continue
        text = f"{e.get('title', '')} {e.get('detail', '')}"
        if needle and needle not in text.lower() and needle not in e.get("category", ""):
            continue
        sev = e.get("severity", "")
        mark = {"crit": "!! ", "warn": "! "}.get(sev, "")
        out.append(f"- {_when(e['t'])} [{e.get('category', '')}] {mark}{e.get('title', '')}"
                   + (f" — {e['detail'][:160]}" if e.get("detail") else ""))
        if len(out) >= limit:
            break
    return "\n".join(out) or f"Nothing recorded in the last {days:g} days" + \
        (f" matching '{search}'." if search else ".")


def _audit(days: float, limit: int, search: str) -> str:
    rows = _audit_rows(days, 2000)
    needle = search.lower()
    out = []
    for e in rows:
        line = f"{e.get('action', '')} {e.get('target', '')} {e.get('detail', '')}"
        if needle and needle not in line.lower():
            continue
        out.append(f"- {_when(e['t'])} {e.get('actor') or ''} via {e.get('source', '?')}: "
                   f"{e.get('action', '').replace('_', ' ')} {e.get('target', '')}"
                   + (f" — {e['detail'][:160]}" if e.get("detail") else "")
                   + ("" if e.get("status", "ok") == "ok" else f" [{e['status']}]"))
        if len(out) >= limit:
            break
    return "\n".join(out) or "No actions recorded."


def _health() -> str:
    from . import reports
    rep = reports.latest_report()
    if not rep:
        return "No health check has run yet."
    counts = rep.get("counts") or {}
    out = [f"Last health check {_when(rep.get('time', 0))}: " +
           ", ".join(f"{v} {k}" for k, v in counts.items() if v)]
    for c in rep.get("checks", []):
        if c.get("status") in ("ok", "info"):
            continue
        line = f"- [{c.get('status')}] {c.get('title')}: {c.get('summary', '')}"
        if c.get("recommendation"):
            line += f" → {c['recommendation']}"
        out.append(line[:500])
    if len(out) == 1:
        out.append("Everything checked out.")
    return "\n".join(out)


def _metric_points(days: float) -> list[dict]:
    from . import metrics
    try:
        long = json.loads(Path(METRICS_LONG).read_text())
    except (OSError, ValueError):
        long = []
    pts = [p for p in long if p.get("t", 0) >= _since(days)]
    if days <= 1:
        pts += metrics.history(int(days * 1440))
    return sorted(pts, key=lambda p: p.get("t", 0))


def _metrics(days: float) -> str:
    pts = _metric_points(days)
    if len(pts) < 2:
        return "Not enough history yet (PocketADM keeps about a week of 5-minute samples)."
    out = [f"{len(pts)} samples from {_when(pts[0]['t'])} to {_when(pts[-1]['t'])}:"]
    for key, label, unit in (("disk", "System disk used", "%"), ("mem", "Memory used", "%"),
                             ("cpu", "CPU", "%"), ("load", "Load (1 min)", "")):
        vals = [(p["t"], p[key]) for p in pts if isinstance(p.get(key), (int, float))]
        if not vals:
            continue
        first, last = vals[0][1], vals[-1][1]
        peak = max(vals, key=lambda v: v[1])
        avg = sum(v for _, v in vals) / len(vals)
        line = (f"- {label}: now {last:.1f}{unit}, average {avg:.1f}{unit}, "
                f"peak {peak[1]:.1f}{unit} at {_when(peak[0])}")
        if key in ("disk", "mem"):
            span = max(1e-6, (vals[-1][0] - vals[0][0]) / 86400)
            line += f", change {last - first:+.1f}{unit} ({(last - first) / span:+.2f}{unit}/day)"
        out.append(line)
    # the biggest jumps in disk use, where a cause can be looked for
    jumps = []
    for a, b in zip(pts, pts[1:]):
        if isinstance(a.get("disk"), (int, float)) and isinstance(b.get("disk"), (int, float)):
            d = b["disk"] - a["disk"]
            if abs(d) >= 0.5:
                jumps.append((abs(d), d, b["t"]))
    if jumps:
        out.append("Largest disk jumps: " + ", ".join(
            f"{d:+.1f}% at {_when(t)}" for _, d, t in sorted(jumps, reverse=True)[:5]))
    return "\n".join(out)


def _storage() -> str:
    from . import files
    try:
        data = files.storage()
    except Exception as e:  # noqa: BLE001
        return f"Drives could not be read: {e}"
    rows = data.get("filesystems") if isinstance(data, dict) else data
    out = []
    for d in rows or []:
        name = d.get("label") or d.get("model") or d.get("device", "")
        out.append(f"- {d.get('mount')} ({d.get('kind')}, {d.get('fstype')}"
                   + (f", {name}" if name else "") + f"): {_gb(d.get('used', 0))} of "
                   f"{_gb(d.get('total', 0))} used ({d.get('percent', 0)}%), {_gb(d.get('free', 0))} free")
    return "\n".join(out) or "No drives found."


def _jobs(days: float, limit: int) -> str:
    rows = job_history(days)
    return "\n".join(_job_text(j, tail=12) for j in rows[:limit]) or "No jobs recorded."


def _watch(limit: int) -> str:
    from . import channel
    msgs = channel.page(limit=limit).get("messages", [])
    out = []
    for m in msgs[-limit:]:
        who = "watch" if m.get("role") != "user" else "you"
        out.append(f"- {_when(m.get('t', 0))} {who}: {' '.join(str(m.get('text', '')).split())[:300]}")
    return "\n".join(out) or "The watch has not written anything yet."


async def _overview() -> str:
    from . import dockerapi, sysinfo
    out = []
    try:
        containers = [c for c in await dockerapi.list_containers(all_=True)
                      if not is_helper_name(c["name"])]
        running = [c for c in containers if c["state"] == "running"]
        bad = [c for c in containers if c["state"] != "running" or c.get("health") == "unhealthy"]
        out.append(f"Containers: {len(running)} of {len(containers)} running." +
                   (" Not fine: " + ", ".join(f"{c['name']} ({c['health'] or c['state']})"
                                               for c in bad[:12]) if bad else ""))
    except Exception:
        pass
    try:
        snap = sysinfo.snapshot_light()
        out.append(f"Now: CPU {snap.get('cpu_percent', 0):.0f}%, memory "
                   f"{snap.get('memory_percent', 0):.0f}%, load {snap.get('load1', 0):.2f}")
    except Exception:
        pass
    out.append("Drives:\n" + _storage())
    out.append(_metrics(7).split("\n", 1)[-1])
    out.append(_health())
    up = await _updates(3, 5)
    out.append(up.split("\n", 1)[0])
    notable = []
    from . import activity
    for e in activity.recent(limit=400)["events"]:
        if e.get("t", 0) < _since(2):
            break
        if e.get("severity") in ("warn", "crit") and not is_helper_name(e.get("target", "")):
            notable.append(f"- {_when(e['t'])} {e.get('title', '')}")
        if len(notable) >= 8:
            break
    if notable:
        out.append("Notable events (2 days):\n" + "\n".join(notable))
    return "\n\n".join(p for p in out if p)


async def query(topic: str, days: float = 0, limit: int = 0, search: str = "") -> str:
    """One topic of PocketADM's records as text for a model."""
    topic = (topic or "overview").strip().lower()
    if topic not in TOPICS:
        return "Unknown topic. Topics: " + ", ".join(f"{k} ({v})" for k, v in TOPICS.items())
    days = float(days or (1 if topic in ("activity", "audit") else 7))
    limit = int(limit or 40)
    if config.DEMO:
        from . import demodata
        return demodata.records(topic)
    if topic == "overview":
        return await _overview()
    if topic == "updates":
        return await _updates(days, limit)
    if topic == "activity":
        return _activity(days, limit, search)
    if topic == "audit":
        return _audit(days, limit, search)
    if topic == "health":
        return _health()
    if topic == "metrics":
        return _metrics(days)
    if topic == "storage":
        return _storage()
    if topic == "jobs":
        return _jobs(days, limit)
    return _watch(limit)
