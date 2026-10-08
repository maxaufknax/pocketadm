"""What to ask the assistant right now — from the server's own state.

The empty chat used to offer the same three questions on every server ("Why
is disk usage climbing?" even when it was not). These come from what
PocketADM sees: a full disk, updates that just ran, a container that keeps
restarting, a failed systemd unit, findings of the last health check. No
model is asked; it has to be instant.
"""
from __future__ import annotations

import time

from . import config, records

MAX = 4

_FALLBACK = [
    "Give me a short health check of the whole server.",
    "Which of my services can be reached from the internet, and are they protected?",
    "What runs on a schedule on this server, and did the last runs work?",
    "Which containers have no restart policy or no healthcheck?",
]


async def build() -> list[str]:
    if config.DEMO:
        return ["Did the updates I ran today go well?",
                "Why did disk use grow this week?",
                "Is anything on the server unhealthy right now?",
                "What runs on a schedule on this server?"]
    out: list[str] = []

    def add(text: str) -> None:
        if text and text not in out and len(out) < MAX:
            out.append(text)

    # something is broken right now
    try:
        from . import dockerapi
        bad = [c for c in await dockerapi.list_containers(all_=True)
               if not c.get("helper") and not records.is_helper_name(c["name"])
               and (c["state"] in ("restarting", "dead") or c.get("health") == "unhealthy"
                    or (c["state"] == "exited" and "Exited (0)" not in c.get("status", "")))]
        if bad:
            c = bad[0]
            what = "unhealthy" if c.get("health") == "unhealthy" else c["state"]
            add(f"Why is {c['name']} {what}, and how do I fix it?")
    except Exception:
        pass
    try:
        from . import inventory
        inv = inventory.cached()
        failed = [u["unit"] for u in (inv or {}).get("services", []) + (inv or {}).get("timers", [])
                  if u.get("active") == "failed"]
        if failed:
            add(f"Why did {failed[0]} fail?")
    except Exception:
        pass
    # updates that just ran
    try:
        runs = records._audit_rows(1, 5, ("update_apply",))
        if runs:
            n = sum(int((r.get("target") or "x").split()[0]) if (r.get("target") or "x").split()[0].isdigit() else 1
                    for r in runs)
            add(f"I updated {n} service{'s' if n != 1 else ''} in the last day — did everything come back fine?")
    except Exception:
        pass
    # the disk
    try:
        pts = records._metric_points(7)
        disk = [p["disk"] for p in pts if isinstance(p.get("disk"), (int, float))]
        if disk:
            if disk[-1] >= 85:
                add(f"The system disk is {disk[-1]:.0f}% full — what takes the space and what can go?")
            elif len(disk) > 10 and disk[-1] - disk[0] >= 2:
                add(f"Disk use grew by {disk[-1] - disk[0]:.0f}% this week — what grew?")
    except Exception:
        pass
    # pending updates and health findings
    try:
        from . import updates
        pending = [u for u in (updates._cache.get("result") or [])
                   if u.get("update_available") and not u.get("ignored")]
        if pending:
            add(f"Which of the {len(pending)} pending updates are safe to apply now?")
    except Exception:
        pass
    try:
        from . import reports
        rep = reports.latest_report()
        if rep and rep.get("score") in ("warn", "crit") and time.time() - rep.get("time", 0) < 3 * 86400:
            add("Go through the health check findings and fix what is safe to fix.")
    except Exception:
        pass
    for text in _FALLBACK:
        add(text)
    return out
