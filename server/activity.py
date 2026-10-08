"""Everything that happens on the server, as one live feed.

The audit log answers "what did PocketADM do". People also want to see what
the *server* did: a container that died at 3am, who logged in over SSH, which
packages apt upgraded, the USB drive that was plugged in, the backup unit that
failed, the moment the internet dropped. This module collects those moments
from the sources that already record them — the Docker event stream and the
host's own logs (auth, syslog, kern, dpkg, fail2ban) — plus PocketADM's own
audit trail, normalises them into one shape and serves them as a paginated
history and a live Server-Sent Events stream.

  {"id", "t", "category", "kind", "title", "detail", "severity", "target", "source"}

categories: containers · security · system · network · updates · app
Collectors only read; nothing here changes the host.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import re
import secrets
import time
from datetime import datetime

from . import config

FILE = config.DATA_DIR / "activity.jsonl"
MAX_LINES = 6000
_TRIM_AT = 7000
EVENTS: collections.deque = collections.deque(maxlen=3000)
_subscribers: set[asyncio.Queue] = set()
_tasks: list[asyncio.Task] = []
HOST = "/host" if os.path.isdir("/host/var/log") else ""

CATEGORIES = {
    "containers": "Containers",
    "security": "Logins & security",
    "system": "System",
    "network": "Network",
    "updates": "Updates & packages",
    "app": "PocketADM & AI",
}


# ------------------------------------------------------------------ store

def push(category: str, kind: str, title: str, detail: str = "", severity: str = "ok",
         target: str = "", source: str = "", t: float | None = None) -> dict:
    """Record one event and hand it to every live listener. Never raises."""
    event = {"id": secrets.token_hex(5), "t": round(t or time.time(), 3),
             "category": category if category in CATEGORIES else "system",
             "kind": kind, "title": title[:200], "detail": (detail or "")[:600],
             "severity": severity if severity in ("ok", "info", "warn", "crit") else "info",
             "target": (target or "")[:200], "source": source}
    try:
        EVENTS.append(event)
        with FILE.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        _maybe_trim()
    except Exception:
        pass
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    if event["severity"] in ("warn", "crit"):
        try:
            from . import watch
            watch.note_event(event)
        except Exception:
            pass
    return event


def _maybe_trim() -> None:
    try:
        if FILE.stat().st_size < 260 * _TRIM_AT:
            return
        lines = FILE.read_text(errors="replace").splitlines()
        if len(lines) > _TRIM_AT:
            FILE.write_text("\n".join(lines[-MAX_LINES:]) + "\n")
    except Exception:
        pass


def load() -> None:
    """Fill the in-memory buffer from disk after a restart."""
    try:
        lines = FILE.read_text(errors="replace").splitlines()[-EVENTS.maxlen:]
    except OSError:
        return
    for line in lines:
        try:
            EVENTS.append(json.loads(line))
        except ValueError:
            continue


def recent(limit: int = 100, before: float = 0, categories: list[str] | None = None) -> dict:
    wanted = set(categories or [])
    out = []
    for e in reversed(EVENTS):
        if before and e["t"] >= before:
            continue
        if wanted and e["category"] not in wanted:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    if len(out) < limit and FILE.exists() and len(EVENTS) == EVENTS.maxlen:
        # older than the buffer: read the file backwards
        try:
            oldest = out[-1]["t"] if out else (before or time.time())
            for line in reversed(FILE.read_text(errors="replace").splitlines()):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e["t"] >= oldest or (wanted and e["category"] not in wanted):
                    continue
                out.append(e)
                if len(out) >= limit:
                    break
        except OSError:
            pass
    cursor = out[-1]["t"] if len(out) >= limit else None
    return {"events": out, "cursor": cursor, "categories": CATEGORIES, "stats": stats()}


def stats(hours: int = 24) -> dict:
    """Counts of the last day, for the header of the Activity screen."""
    cutoff = time.time() - hours * 3600
    counts = {c: 0 for c in CATEGORIES}
    problems = 0
    for e in EVENTS:
        if e["t"] >= cutoff:
            counts[e["category"]] = counts.get(e["category"], 0) + 1
            if e["severity"] in ("warn", "crit"):
                problems += 1
    return {"hours": hours, "counts": counts, "problems": problems}


async def stream(categories: list[str] | None = None):
    """Server-Sent Events: every new event, and a heartbeat comment every 15s
    so proxies keep the connection open."""
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    wanted = set(categories or [])
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                e = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if wanted and e["category"] not in wanted:
                continue
            yield "data: " + json.dumps(e, ensure_ascii=False) + "\n\n"
    finally:
        _subscribers.discard(q)


# ------------------------------------------------------------- PocketADM itself

_AUDIT_CATEGORY = {
    "login": "security", "login_failed": "security", "logout_all": "security",
    "password_change": "security", "2fa_enable": "security", "2fa_disable": "security",
    "exposure_ack": "security", "pair_new": "security", "pair_claim": "security",
    "user_password": "security", "user_lock": "security", "user_admin": "security",
    "user_create": "security",
    "container_action": "containers", "container_remove": "containers",
    "update_apply": "updates", "snapshot_rollback": "updates", "snapshot_delete": "updates",
    "app_install": "containers", "app_uninstall": "containers",
}
_AUDIT_TITLES = {
    "login": "Signed in to PocketADM", "login_failed": "Failed PocketADM sign-in",
    "logout_all": "Signed out every other device", "password_change": "PocketADM password changed",
    "2fa_enable": "Two-factor turned on", "2fa_disable": "Two-factor turned off",
    "pair_new": "Pairing code created", "pair_claim": "A new device was paired",
    "container_action": "Container control", "container_remove": "Container removed",
    "update_apply": "Update started", "snapshot_rollback": "Rolled back an update",
    "app_install": "App installed", "app_uninstall": "App removed",
    "agent_tool": "The assistant acted", "terminal": "Terminal opened",
    "terminal_kill": "Terminal session ended", "cli_install": "Coding agent installed",
    "file_download": "File opened", "maintenance": "Maintenance",
    "loop_run": "Background check ran", "watch_message": "Watch message",
    "ai_signin": "AI account connected", "ai_signout": "AI account disconnected",
    "file_edit": "File saved", "file_create": "File created", "file_restore": "File restored",
    "file_mkdir": "Folder created", "file_upload": "File uploaded", "file_rename": "Renamed",
    "file_move": "Moved", "file_copy": "Copied", "file_delete": "Deleted",
    "file_chmod": "Permissions changed", "file_extract": "Archive unpacked",
    "watch_save": "Watch settings changed", "watch_run": "Watch asked to look",
}


def from_audit(entry: dict) -> None:
    """audit.record calls this for every logged action."""
    action = entry.get("action", "")
    if action in ("file_download",) and entry.get("detail") == "preview":
        return
    category = _AUDIT_CATEGORY.get(action, "app")
    title = _AUDIT_TITLES.get(action, action.replace("_", " ").capitalize())
    target = entry.get("target", "")
    detail = entry.get("detail", "")
    if action == "container_action" and detail:
        title = f"Container {detail}" if " " not in detail else detail[:1].upper() + detail[1:]
    status = entry.get("status", "ok")
    severity = {"ok": "ok", "warn": "warn", "error": "crit", "crit": "crit",
                "info": "info"}.get(status, "info")
    who = entry.get("source", "ui")
    via = {"ui": "", "agent": "by the assistant", "auto": "by the assistant (auto mode)",
           "sentinel": "by the background watch", "watch": "by the background watch"}.get(who, "")
    push(category, "pocketadm." + action, title,
         detail=" · ".join(x for x in (target if target != detail else "", detail, via) if x),
         severity=severity, target=target, source="pocketadm", t=entry.get("t"))


# ------------------------------------------------------------- Docker events

def docker_event(e: dict) -> dict | None:
    """One raw Docker event as an activity row, or None for noise (exec_*
    from health checks, network attach/detach, image tags)."""
    typ = e.get("Type", "")
    action = (e.get("Action") or "").split(":")[0].strip()
    attrs = (e.get("Actor") or {}).get("Attributes") or {}
    name = attrs.get("name") or (e.get("Actor") or {}).get("ID", "")[:12]
    t = e.get("time") or time.time()
    if typ == "container":
        if action == "health_status":
            status = (e.get("Action") or "").split(":")[-1].strip()
            if status == "healthy":
                return {"category": "containers", "kind": "docker.health", "t": t,
                        "title": f"{name} is healthy again", "severity": "ok", "target": name}
            if status == "unhealthy":
                return {"category": "containers", "kind": "docker.health", "t": t,
                        "title": f"{name} turned unhealthy", "severity": "warn", "target": name}
            return None
        words = {"start": ("started", "ok"), "stop": ("stopped", "info"),
                 "restart": ("restarted", "info"), "kill": ("was killed", "info"),
                 "oom": ("ran out of memory", "crit"), "create": ("was created", "info"),
                 "destroy": ("was removed", "info"), "pause": ("was paused", "info"),
                 "unpause": ("was resumed", "info"), "rename": ("was renamed", "info")}
        if action == "die":
            code = attrs.get("exitCode", "")
            bad = code not in ("", "0", "143", "137")  # 143/137: stopped on request
            return {"category": "containers", "kind": "docker.die", "t": t,
                    "title": f"{name} exited" + (f" with code {code}" if code else ""),
                    "severity": "warn" if bad else "info", "target": name}
        if action in words:
            text, sev = words[action]
            return {"category": "containers", "kind": "docker." + action, "t": t,
                    "title": f"{name} {text}", "severity": sev, "target": name,
                    "detail": attrs.get("image", "") if action in ("create", "start") else ""}
        return None
    if typ == "image" and action in ("pull", "delete"):
        ref = (e.get("Actor") or {}).get("ID", "") or attrs.get("name", "")
        return {"category": "updates", "kind": "docker.image." + action, "t": t,
                "title": ("Image pulled: " if action == "pull" else "Image removed: ") + ref[:120],
                "severity": "ok", "target": ref[:120]}
    if typ == "volume" and action in ("create", "destroy"):
        vol = (e.get("Actor") or {}).get("ID", "")[:40]
        return {"category": "containers", "kind": "docker.volume." + action, "t": t,
                "title": f"Volume {vol} " + ("created" if action == "create" else "removed"),
                "severity": "info", "target": vol}
    return None


async def _docker_loop() -> None:
    from . import dockerapi
    backoff = 2
    while True:
        try:
            params = {"since": str(int(time.time())),
                      "filters": json.dumps({"type": ["container", "image", "volume"]})}
            async with dockerapi.client().stream("GET", "/events", params=params,
                                                  timeout=None) as resp:
                backoff = 2
                buf = ""
                async for chunk in resp.aiter_text():
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        try:
                            raw = json.loads(line)
                        except ValueError:
                            continue
                        row = docker_event(raw)
                        if row:
                            push(source="docker", **row)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


# ------------------------------------------------------------- host logs

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?)\s+\S+\s+")
_TS_CLASSIC = re.compile(r"^([A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+\S+\s+")


def _stamp(line: str) -> tuple[float, str]:
    """(epoch, rest of the line) for an RFC 3339 or classic syslog line."""
    m = _TS.match(line)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp(), line[m.end():]
        except ValueError:
            return time.time(), line[m.end():]
    m = _TS_CLASSIC.match(line)
    if m:
        try:
            year = datetime.now().year
            return datetime.strptime(f"{year} {m.group(1)}", "%Y %b %d %H:%M:%S").timestamp(), line[m.end():]
        except ValueError:
            return time.time(), line[m.end():]
    return time.time(), line


class FailedLogins:
    """Failed SSH logins arrive by the hundred from bots; one row per address
    per ten minutes says the same without burying everything else."""

    def __init__(self):
        self.pending: dict[str, list] = {}
        self.last_emit: dict[str, float] = {}

    def add(self, ip: str, user: str, t: float) -> None:
        slot = self.pending.setdefault(ip, [0, set(), t])
        slot[0] += 1
        if user:
            slot[1].add(user)
        slot[2] = t

    def flush(self, now: float | None = None, quiet: float = 600) -> list[dict]:
        now = now or time.time()
        out = []
        for ip, (count, users, t) in list(self.pending.items()):
            if now - self.last_emit.get(ip, 0) < quiet:
                continue
            names = ", ".join(sorted(users)[:4]) + ("…" if len(users) > 4 else "")
            out.append({"category": "security", "kind": "ssh.failed", "t": t,
                        "title": f"{count} failed SSH login{'s' if count != 1 else ''} from {ip}",
                        "detail": f"tried: {names}" if names else "",
                        "severity": "warn" if count >= 20 else "info", "target": ip})
            self.last_emit[ip] = now
            del self.pending[ip]
        return out


_failed = FailedLogins()


def parse_auth(line: str) -> dict | None:
    t, rest = _stamp(line)
    m = re.search(r"sshd\[\d+\]: Accepted (\w+) for (\S+) from (\S+)", rest)
    if m:
        method, user, ip = m.groups()
        return {"category": "security", "kind": "ssh.login", "t": t,
                "title": f"{user} logged in over SSH", "detail": f"from {ip} ({method})",
                "severity": "info", "target": user}
    m = re.search(r"sshd\[\d+\]: (?:Failed \w+ for (?:invalid user )?(\S+)|Invalid user (\S+)) from (\S+)", rest)
    if m:
        user = m.group(1) or m.group(2) or ""
        _failed.add(m.group(3), user, t)
        return None
    m = re.search(r"sudo:\s+(\S+) : .*?COMMAND=(.+)$", rest)
    if m:
        user, command = m.groups()
        command = command.strip()
        short = command.split("/")[-1] if command.startswith("/") else command
        return {"category": "security", "kind": "sudo", "t": t,
                "title": f"{user} ran a command as root", "detail": short[:300],
                "severity": "info", "target": user}
    m = re.search(r"(?:useradd|adduser)\[\d+\]: new user: name=([^,\s]+)", rest)
    if m:
        return {"category": "security", "kind": "user.add", "t": t,
                "title": f"New user account: {m.group(1)}", "severity": "warn", "target": m.group(1)}
    m = re.search(r"passwd\[\d+\]: pam_unix\(passwd:chauthtok\): password changed for (\S+)", rest)
    if m:
        return {"category": "security", "kind": "user.password", "t": t,
                "title": f"Password changed for {m.group(1)}", "severity": "info",
                "target": m.group(1)}
    return None


def parse_syslog(line: str) -> dict | None:
    t, rest = _stamp(line)
    m = re.search(r"systemd\[1\]: (\S+?)\.(service|timer|mount): Failed with result '([^']+)'", rest)
    if m:
        unit, kind, why = m.groups()
        return {"category": "system", "kind": "systemd.failed", "t": t,
                "title": f"{unit} failed", "detail": f"{unit}.{kind}: {why}",
                "severity": "warn", "target": unit}
    m = re.search(r"systemd\[1\]: (?:Finished|Started) (.+?)\.?$", rest)
    if m and re.search(r"backup|snapshot|restic|borg", m.group(1), re.I) \
            and "Finished" in rest:
        return {"category": "system", "kind": "systemd.finished", "t": t,
                "title": f"Finished: {m.group(1)}", "severity": "ok", "target": m.group(1)}
    if re.search(r"systemd\[1\]: (Reached target .*(Shutdown|Reboot)|Shutting down)", rest):
        return {"category": "system", "kind": "power.shutdown", "t": t,
                "title": "The server is shutting down", "severity": "warn"}
    return None


def parse_kern(line: str) -> dict | None:
    t, rest = _stamp(line)
    m = re.search(r"Out of memory: Killed process (\d+) \(([^)]+)\)", rest)
    if m:
        return {"category": "system", "kind": "kernel.oom", "t": t,
                "title": f"Out of memory — the kernel killed {m.group(2)}",
                "detail": f"pid {m.group(1)}", "severity": "crit", "target": m.group(2)}
    m = re.search(r"sd \S+: \[(sd[a-z]+)\] Attached SCSI (removable )?disk", rest)
    if m:
        return {"category": "system", "kind": "disk.attached", "t": t,
                "title": f"Drive connected ({m.group(1)})", "severity": "info", "target": m.group(1)}
    m = re.search(r"usb \S+: USB disconnect", rest)
    if m:
        return {"category": "system", "kind": "usb.disconnect", "t": t,
                "title": "A USB device was disconnected", "severity": "info"}
    m = re.search(r"(EXT4-fs error|XFS .*error|Buffer I/O error|I/O error, dev (\S+))", rest)
    if m:
        return {"category": "system", "kind": "disk.error", "t": t,
                "title": "Disk error reported by the kernel", "detail": rest.strip()[:300],
                "severity": "crit"}
    return None


def parse_dpkg(line: str) -> dict | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (install|upgrade|remove|purge) (\S+?)(?::\S+)? (\S+) (\S+)", line)
    if not m:
        return None
    when, action, pkg, old, new = m.groups()
    try:
        t = datetime.strptime(when, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        t = time.time()
    words = {"install": f"installed {new}", "upgrade": f"{old} → {new}",
             "remove": "removed", "purge": "purged"}
    return {"category": "updates", "kind": "apt." + action, "t": t,
            "title": f"Package {pkg} " + ("upgraded" if action == "upgrade" else
                                           "installed" if action == "install" else "removed"),
            "detail": words[action], "severity": "info", "target": pkg}


def parse_fail2ban(line: str) -> dict | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*?\[(\S+)\] (Ban|Unban) (\S+)", line)
    if not m:
        return None
    when, jail, action, ip = m.groups()
    try:
        t = datetime.strptime(when, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        t = time.time()
    return {"category": "security", "kind": "fail2ban." + action.lower(), "t": t,
            "title": f"fail2ban {'banned' if action == 'Ban' else 'released'} {ip}",
            "detail": f"jail {jail}", "severity": "info" if action == "Ban" else "ok",
            "target": ip}


class Tail:
    """Follows a log file across rotations, from its end at start-up."""

    def __init__(self, path: str):
        self.path = path
        self.inode = None
        self.offset = 0

    def read_new(self, max_bytes: int = 512 * 1024) -> list[str]:
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if self.inode is None:
            self.inode, self.offset = st.st_ino, st.st_size   # start at the end
            return []
        if st.st_ino != self.inode or st.st_size < self.offset:
            self.inode, self.offset = st.st_ino, 0             # rotated
        if st.st_size == self.offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read(max_bytes)
        except OSError:
            return []
        cut = data.rfind(b"\n")
        if cut < 0:
            return []
        self.offset += cut + 1
        return data[:cut].decode("utf-8", "replace").splitlines()


_DPKG_BURST: list[dict] = []


async def _logs_loop() -> None:
    sources = [
        (Tail(HOST + "/var/log/auth.log"), parse_auth),
        (Tail(HOST + "/var/log/syslog"), parse_syslog),
        (Tail(HOST + "/var/log/kern.log"), parse_kern),
        (Tail(HOST + "/var/log/dpkg.log"), parse_dpkg),
        (Tail(HOST + "/var/log/fail2ban.log"), parse_fail2ban),
    ]
    for tail, _ in sources:
        tail.read_new()          # remember where the files end now
    last_flush = time.time()
    while True:
        await asyncio.sleep(3)
        for tail, parse in sources:
            try:
                lines = await asyncio.to_thread(tail.read_new)
            except Exception:
                continue
            for line in lines:
                try:
                    row = parse(line)
                except Exception:
                    row = None
                if not row:
                    continue
                if row["kind"].startswith("apt."):
                    _DPKG_BURST.append(row)
                    continue
                push(source="host", **row)
        now = time.time()
        if _DPKG_BURST and now - max(r["t"] for r in _DPKG_BURST) > 20:
            _flush_dpkg()
        if now - last_flush > 30:
            for row in _failed.flush(now):
                push(source="host", **row)
            last_flush = now


def _flush_dpkg() -> None:
    """One apt run upgrades dozens of packages; that is one event."""
    rows = list(_DPKG_BURST)
    _DPKG_BURST.clear()
    if len(rows) <= 3:
        for row in rows:
            push(source="host", **row)
        return
    names = ", ".join(r["target"] for r in rows[:8]) + ("…" if len(rows) > 8 else "")
    push("updates", "apt.batch", f"{len(rows)} packages changed by apt", detail=names,
         severity="info", source="host", t=rows[-1]["t"])


# ------------------------------------------------------------- network

class Connectivity:
    """Internet up/down transitions from the metrics probe: three failed
    probes in a row (30 s) is "down", the first success after is "back"."""

    def __init__(self):
        self.misses = 0
        self.down_since: float | None = None

    def sample(self, ping: float | None, t: float) -> dict | None:
        if ping is None:
            self.misses += 1
            if self.misses == 3 and self.down_since is None:
                self.down_since = t
                return {"category": "network", "kind": "net.down", "t": t,
                        "title": "Internet connection lost", "severity": "crit",
                        "detail": "1.1.1.1, 8.8.8.8 and 9.9.9.9 stopped answering"}
            return None
        self.misses = 0
        if self.down_since is not None:
            minutes = max(1, round((t - self.down_since) / 60))
            self.down_since = None
            return {"category": "network", "kind": "net.up", "t": t,
                    "title": f"Internet is back after about {minutes} min", "severity": "ok"}
        return None


connectivity = Connectivity()


def on_metrics(point: dict) -> None:
    """metrics.py calls this with every sample."""
    row = connectivity.sample(point.get("ping"), point.get("t") or time.time())
    if row:
        push(source="metrics", **row)


# ------------------------------------------------------------- lifecycle

def start() -> None:
    load()
    try:
        with open((HOST or "") + "/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        if uptime < 900 and not any(e["kind"] == "power.boot" and time.time() - e["t"] < 1800
                                    for e in EVENTS):
            push("system", "power.boot", "The server started",
                 detail=f"up for {int(uptime // 60)} min", severity="info", source="host",
                 t=time.time() - uptime)
    except (OSError, ValueError):
        pass
    if not _tasks:
        _tasks.append(asyncio.ensure_future(_logs_loop()))
        _tasks.append(asyncio.ensure_future(_docker_loop()))
