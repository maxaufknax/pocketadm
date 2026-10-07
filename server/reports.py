"""Reports & analyses: script-based server checks (no AI required),
run on demand or on a schedule, with optional AI narrative analysis.

Every check returns:
  {id, title, icon, status: ok|warn|crit|info, summary, details?, recommendation?}
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path

from . import ai, config, dockerapi, permissions, sysinfo, updates

REPORTS_DIR = config.DATA_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
HOST = "/host" if os.path.isdir("/host") else ""

_scheduler_task: asyncio.Task | None = None


def _check(id_, title, icon, status, summary, details=None, recommendation=None) -> dict:
    out = {"id": id_, "title": title, "icon": icon, "status": status, "summary": summary}
    if details:
        out["details"] = details[:2000]
    if recommendation:
        out["recommendation"] = recommendation
    return out


# ------------------------------------------------------------- checks

async def check_resources() -> list[dict]:
    snap = await asyncio.to_thread(sysinfo.snapshot)
    out = []
    d = snap["disk"]["percent"]
    out.append(_check(
        "disk", "Disk usage", "💾",
        "crit" if d > 90 else "warn" if d > 80 else "ok",
        f"{d}% used ({round(snap['disk']['free']/1e9)} GB free)",
        recommendation="Clean up old images/volumes: `docker system prune` — or check big "
                       "directories with the Vibe agent." if d > 80 else None))
    m = snap["memory"]["percent"]
    out.append(_check(
        "memory", "Memory", "🧠",
        "crit" if m > 92 else "warn" if m > 85 else "ok",
        f"{m}% used of {round(snap['memory']['total']/1e9)} GB"))
    cores = snap["cpu_count"] or 1
    load = snap["load"][0]
    out.append(_check(
        "load", "CPU load", "⚙️",
        "warn" if load > cores * 1.5 else "ok",
        f"load {load:.2f} on {cores} cores"))
    return out


async def check_containers() -> list[dict]:
    out = []
    try:
        containers = await dockerapi.list_containers(all_=True)
    except Exception as e:
        return [_check("docker", "Docker engine", "🐳", "crit", f"not reachable: {e}")]
    running = [c for c in containers if c["state"] == "running"]
    stopped = [c for c in containers if c["state"] in ("exited", "dead")]
    unhealthy = [c for c in running if c["health"] == "unhealthy"]
    restarting = [c for c in containers if c["state"] == "restarting"]
    out.append(_check("containers", "Containers", "🐳",
                      "ok", f"{len(running)} running, {len(stopped)} stopped"))
    if unhealthy:
        out.append(_check("unhealthy", "Unhealthy containers", "🤒", "crit",
                          ", ".join(c["name"] for c in unhealthy),
                          recommendation="Check the logs of these containers — their "
                                         "healthcheck is failing."))
    if restarting:
        out.append(_check("restart-loop", "Restart loops", "🔁", "crit",
                          ", ".join(c["name"] for c in restarting),
                          recommendation="These containers keep crashing. Check logs."))
    # deep inspect a sample for restart counts (cheap enough for <100)
    high_restarts = []
    for c in running[:80]:
        try:
            d = await dockerapi.inspect_container(c["id"])
            if d.get("RestartCount", 0) >= 3:
                high_restarts.append(f"{c['name']} ({d['RestartCount']}x)")
        except Exception:
            pass
    if high_restarts:
        out.append(_check("restarts", "Frequent restarts", "🔁", "warn",
                          ", ".join(high_restarts[:8])))
    privileged = []
    for c in running:
        if c.get("mounts_docker_sock") and c["name"] != "helmsman":
            privileged.append(c["name"] + " (docker.sock)")
    if privileged:
        out.append(_check("privileged", "Elevated privileges", "🔓", "info",
                          ", ".join(privileged[:8]),
                          recommendation="These containers can control Docker (root-equivalent). "
                                         "Make sure you trust them."))
    return out


async def check_network() -> list[dict]:
    try:
        containers = await dockerapi.list_containers(all_=False)
    except Exception:
        return []
    public_ports = []
    for c in containers:
        for p in c["ports"]:
            if p.get("ip") in ("", "0.0.0.0", "::"):
                public_ports.append(f"{p['public']}→{c['name']}")
    n = len(public_ports)
    return [_check("ports", "Published ports", "🌐",
                   "info" if n < 15 else "warn",
                   f"{n} container ports exposed on all interfaces",
                   details=", ".join(sorted(public_ports, key=lambda s: int(s.split('→')[0]))),
                   recommendation=None if n < 15 else
                   "Consider binding internal services to 127.0.0.1 and routing "
                   "through your reverse proxy.")]


async def check_ssh() -> list[dict]:
    path = Path(HOST + "/etc/ssh/sshd_config")
    if not path.exists():
        return [_check("ssh", "SSH hardening", "🔒", "info", "sshd_config not readable")]
    out = []
    try:
        text = path.read_text()
        extra = Path(HOST + "/etc/ssh/sshd_config.d")
        if extra.is_dir():
            for f in sorted(extra.glob("*.conf")):
                try:
                    text += "\n" + f.read_text()
                except OSError:
                    pass

        def effective(directive: str) -> str:
            vals = re.findall(rf"^\s*{directive}\s+(\S+)", text, re.M | re.I)
            return vals[-1].lower() if vals else ""

        root = effective("PermitRootLogin")
        pw = effective("PasswordAuthentication")
        if root in ("yes", ""):
            out.append(_check("ssh-root", "SSH root login", "🔒",
                              "crit" if root == "yes" else "warn",
                              f"PermitRootLogin is {'yes' if root == 'yes' else 'not set (defaults may allow it)'}",
                              recommendation="Set `PermitRootLogin no` (or `prohibit-password`) "
                                             "in /etc/ssh/sshd_config."))
        else:
            out.append(_check("ssh-root", "SSH root login", "🔒", "ok", f"PermitRootLogin {root}"))
        if pw == "yes" or pw == "":
            out.append(_check("ssh-pw", "SSH password auth", "🔑",
                              "warn",
                              "Password authentication " + ("enabled" if pw == "yes" else "not explicitly disabled"),
                              recommendation="Use SSH keys and set `PasswordAuthentication no`."))
        else:
            out.append(_check("ssh-pw", "SSH password auth", "🔑", "ok", "disabled (keys only)"))
    except OSError as e:
        out.append(_check("ssh", "SSH hardening", "🔒", "info", f"not readable: {e}"))
    return out


async def check_auth_log() -> list[dict]:
    path = Path(HOST + "/var/log/auth.log")
    if not path.exists():
        return []
    try:
        # read the last ~2MB, count failed ssh logins of the last 24h roughly
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 2_000_000))
            tail = f.read().decode("utf-8", "replace")
        failed = len(re.findall(r"Failed password|Invalid user", tail))
        accepted = len(re.findall(r"Accepted (?:publickey|password)", tail))
        status = "ok" if failed < 50 else "warn" if failed < 500 else "crit"
        return [_check("authlog", "SSH login attempts", "🚪", status,
                       f"~{failed} failed, {accepted} successful (recent log window)",
                       recommendation=None if failed < 50 else
                       "Lots of failed logins. fail2ban and/or a non-standard SSH port "
                       "reduce noise; keys-only auth keeps it safe.")]
    except OSError:
        return [_check("authlog", "SSH login attempts", "🚪", "info", "auth.log not readable")]


async def check_fail2ban() -> list[dict]:
    try:
        containers = await dockerapi.list_containers(all_=False)
        if any("fail2ban" in c["image"].lower() or "fail2ban" in c["name"].lower()
               for c in containers):
            return [_check("fail2ban", "fail2ban", "🛡️", "ok", "running (container)")]
    except Exception:
        pass
    if Path(HOST + "/etc/fail2ban").is_dir():
        return [_check("fail2ban", "fail2ban", "🛡️", "ok", "installed on host")]
    return [_check("fail2ban", "fail2ban", "🛡️", "info", "not detected",
                   recommendation="fail2ban blocks brute-force attackers automatically — "
                                  "worth installing if SSH is exposed.")]


async def check_updates_pending() -> list[dict]:
    out = []
    try:
        docker_ups = await updates.check_docker_updates()
        n = sum(1 for u in docker_ups if u["update_available"] and not u["ignored"])
        high = sum(1 for u in docker_ups
                   if u["update_available"] and not u["ignored"] and u.get("priority") == "high")
        # a pending update is a to-do; it becomes critical when a security-relevant
        # image has been left behind for two months
        stale = sum(1 for u in docker_ups
                    if u["update_available"] and not u["ignored"] and u.get("priority") == "high"
                    and (u.get("age_days") or 0) > 60)
        status = "ok" if n == 0 else "crit" if stale else "warn"
        summary = "everything up to date" if n == 0 else \
            f"{n} image update{'s' if n != 1 else ''} pending" + \
            (f" ({high} security-relevant)" if high else "")
        out.append(_check("docker-updates", "Docker image updates", "⬆️", status, summary,
                          recommendation="Review them under Updates — each one shows what "
                                         "changes, and a snapshot makes it undoable." if n else None))
    except Exception as e:
        out.append(_check("docker-updates", "Docker image updates", "⬆️", "info", str(e)[:100]))
    try:
        apt = await updates.check_apt_updates()
        if apt["available"]:
            n = len(apt["packages"])
            out.append(_check("apt", "Host packages", "📦",
                              "ok" if n == 0 else "warn" if n < 20 else "crit",
                              "up to date" if n == 0 else f"{n} upgradable packages"))
    except Exception:
        pass
    if Path(HOST + "/var/run/reboot-required").exists():
        out.append(_check("reboot", "Reboot required", "🔄", "warn",
                          "the host wants a reboot (kernel/libc update)",
                          recommendation="Schedule a reboot when convenient."))
    return out


async def check_docker_disk() -> list[dict]:
    """Reclaimable space: unused images and orphaned volumes."""
    try:
        df = await dockerapi.system_df()
    except Exception:
        return []
    out = []
    unused_img = sum(i.get("Size", 0) for i in df.get("Images") or []
                     if i.get("Containers", 0) == 0)
    if unused_img > 500e6:
        gb = unused_img / 1e9
        out.append(_check("docker-images", "Unused Docker images", "🧹",
                          "warn" if gb > 5 else "info",
                          f"~{gb:.1f} GB in images no container uses",
                          recommendation="Reclaim the space with `docker image prune -a` "
                                         "(keeps everything that's in use) — or let the "
                                         "Vibe agent clean up safely."))
    orphan_vols = [v.get("Name", "?") for v in df.get("Volumes") or []
                   if (v.get("UsageData") or {}).get("RefCount", 1) == 0]
    if len(orphan_vols) >= 3:
        out.append(_check("docker-volumes", "Orphaned volumes", "🧹", "info",
                          f"{len(orphan_vols)} volumes are attached to nothing",
                          details=", ".join(orphan_vols[:12]),
                          recommendation="If none of these hold data you need, "
                                         "`docker volume prune` frees them. Careful: "
                                         "volumes can contain app data — check first."))
    return out


async def check_app_security() -> list[dict]:
    """PocketADM's own security posture: 2FA, recent failed app logins."""
    out = []
    if config.get_totp_secret():
        out.append(_check("app-2fa", "PocketADM two-factor auth", "🔑", "ok", "enabled"))
    else:
        out.append(_check("app-2fa", "PocketADM two-factor auth", "🔑", "info",
                          "not enabled",
                          recommendation="This app can start/stop anything on your server — "
                                         "add a second factor under More → Security."))
    try:
        audit_file = config.DATA_DIR / "audit.jsonl"
        if audit_file.exists():
            cutoff = time.time() - 24 * 3600
            failed = 0
            for line in audit_file.read_text().splitlines()[-2000:]:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("action") == "login_failed" and e.get("time", 0) > cutoff:
                    failed += 1
            if failed:
                out.append(_check("app-logins", "Failed PocketADM logins", "🚪",
                                  "warn" if failed >= 10 else "info",
                                  f"{failed} failed sign-in attempt{'s' if failed != 1 else ''} "
                                  f"in the last 24 h",
                                  recommendation="If that wasn't you, make sure the app is not "
                                                 "exposed to the internet without protection, and "
                                                 "consider 2FA." if failed >= 10 else None))
    except Exception:
        pass
    return out


def _backup_timers(host: str = HOST) -> list[dict]:
    """Backup jobs scheduled as systemd timers: name, enabled, last run.

    Only units the admin wrote (/etc/systemd/system) count — the distro's own
    dpkg-db-backup lives in /usr/lib and is not a backup of anything you own.
    A persistent timer leaves a stamp file whose age is its last run."""
    tools = ("restic", "borg", "borgmatic", "duplicati", "duplicacy", "kopia",
             "rsnapshot", "backrest", "urbackup", "rclone", "backup")
    units = Path(host + "/etc/systemd/system")
    out = []
    try:
        timers = sorted(p for p in units.glob("*.timer") if any(t in p.name.lower() for t in tools))
    except OSError:
        return out
    for timer in timers:
        stamp = Path(host + "/var/lib/systemd/timers/stamp-" + timer.name)
        try:
            last = stamp.stat().st_mtime if stamp.exists() else None
        except OSError:
            last = None
        out.append({"name": timer.name.removesuffix(".timer"),
                    "enabled": (units / "timers.target.wants" / timer.name).exists(),
                    "last_run": last})
    return out


async def check_backups() -> list[dict]:
    """Is any recognizable backup tooling in place, and has it run lately?"""
    tools = ("restic", "borg", "borgmatic", "duplicati", "duplicacy", "kopia",
             "rsnapshot", "backrest", "urbackup", "velero")
    found = []
    try:
        for c in await dockerapi.list_containers(all_=True):
            hay = (c["image"] + " " + c["name"]).lower()
            for t in tools:
                if t in hay:
                    found.append(f"{c['name']} ({t})")
                    break
    except Exception:
        pass
    for d in ("/etc/cron.d", "/etc/cron.daily"):
        p = Path(HOST + d)
        if p.is_dir():
            try:
                for f in p.iterdir():
                    if any(t in f.name.lower() for t in tools + ("backup",)):
                        found.append(f"{d}/{f.name}")
            except OSError:
                pass
    timers = [t for t in _backup_timers() if t["enabled"]]
    found += [f"{t['name']} (systemd timer)" for t in timers]
    stamps = [t["last_run"] for t in timers if t["last_run"]]
    if found:
        summary = "backups set up: " + ", ".join(sorted(set(found))[:6])
        status = "ok"
        recommendation = None
        if stamps:
            age_h = (time.time() - max(stamps)) / 3600
            summary += f" · last run {_ago(age_h)}"
            if age_h > 72:
                status = "warn"
                recommendation = ("The newest scheduled backup ran more than three days ago. "
                                  "Check that the timer still fires and the target disk is attached.")
        return [_check("backups", "Backups", "💾", status, summary,
                       recommendation=recommendation)]
    return [_check("backups", "Backups", "💾", "warn", "no backup tooling detected",
                   recommendation="Volumes and configs are one disk failure away from gone. "
                                  "Ask the assistant to set up restic or borgmatic to an "
                                  "external target — and export a PocketADM settings backup "
                                  "under More → Backup.")]


def _ago(hours: float) -> str:
    if hours < 1:
        return "less than an hour ago"
    if hours < 48:
        return f"{int(hours)} h ago"
    return f"{int(hours // 24)} days ago"


async def check_agent_tasks() -> list[dict]:
    """Permission requests the assistant ran into and that wait for the user.

    Only recent ones, one row per kind of request: five identical "needs root"
    rows from last month's chats are noise, not tasks. Alerts from the
    background watch live on the Alerts screen, not here."""
    out = []
    try:
        permissions.expire_stale()
        cutoff = time.time() - 3 * 86400
        fresh = [p for p in permissions.list_all()
                 if p.get("status") == "open" and p.get("last_seen", p.get("time", 0)) >= cutoff]
        by_title: dict[str, dict] = {}
        for p in fresh:
            key = p.get("title", "")
            if key in by_title:
                by_title[key]["count"] = by_title[key].get("count", 1) + p.get("count", 1)
                by_title[key].setdefault("ids", []).append(p["id"])
            else:
                by_title[key] = {**p, "ids": [p["id"]]}
        for p in list(by_title.values())[:4]:
            c = _check("perm-" + p["id"], p.get("title", "Permission needed"), "🔐",
                       "warn", (p.get("explanation") or p.get("detail", ""))
                       + (f" (asked {p['count']}×)" if p.get("count", 1) > 1 else ""),
                       recommendation=(p.get("fix") or "") or None)
            c["permission_ids"] = p["ids"]
            out.append(c)
    except Exception:
        pass
    return out


CHECK_GROUPS = [
    ("Agent tasks", check_agent_tasks),
    ("Resources", check_resources),
    ("Storage", check_docker_disk),
    ("Containers", check_containers),
    ("Network", check_network),
    ("SSH", check_ssh),
    ("Logins", check_auth_log),
    ("Protection", check_fail2ban),
    ("App security", check_app_security),
    ("Backups", check_backups),
    ("Updates", check_updates_pending),
]

# The five areas the app groups findings in.
CATEGORY = {
    "Agent tasks": "Assistant", "Resources": "Stability", "Containers": "Stability",
    "Storage": "Storage", "Network": "Security", "SSH": "Security", "Logins": "Security",
    "Protection": "Security", "App security": "Security", "Backups": "Backups",
    "Updates": "Updates",
}

# What a finding means, in words for someone who is not a sysadmin.
EXPLAIN = {
    "disk": "How full the server's main disk is. Above ~85 % databases and logs start failing in "
            "odd ways, and a full disk can stop every service at once.",
    "memory": "How much RAM is in use. When it runs out, Linux kills the biggest process — often a "
              "database — without asking.",
    "load": "How busy the processor is compared to its cores. Sustained overload makes everything slow.",
    "containers": "How many of your services are running.",
    "unhealthy": "These containers report that their own health check fails: they run, but do not "
                 "work as they should.",
    "restart-loop": "These containers crash and restart over and over — usually a config error or a "
                    "missing dependency.",
    "restarts": "These containers restarted several times recently; something makes them crash.",
    "privileged": "These containers can control Docker itself, which is as powerful as root on the "
                  "server. That is normal for management tools — make sure you trust each one.",
    "ports": "Ports published on all interfaces are reachable from your network, and from the "
             "internet if your router forwards them. Internal services are safer behind the reverse proxy.",
    "ssh-root": "Whether someone can log in as root over SSH. Attackers try root first; turning it "
                "off removes the most guessed account.",
    "ssh-pw": "Whether SSH accepts passwords. Passwords can be guessed; keys cannot. Bots try "
              "thousands of passwords a day on every public server.",
    "authlog": "Failed SSH logins in the recent log. Thousands mean bots are knocking — harmless "
               "with keys-only login, dangerous with weak passwords.",
    "fail2ban": "fail2ban blocks addresses that keep failing to log in, which turns brute-force "
                "attempts into a few tries.",
    "app-2fa": "Whether PocketADM itself asks for a second factor. It can control the whole server, "
               "so its login deserves the strongest protection.",
    "app-logins": "Failed sign-ins to PocketADM in the last day.",
    "docker-images": "Old image versions that no container uses any more. They only take disk "
                     "space and can be removed safely.",
    "docker-volumes": "Docker volumes no container uses. They may hold old data — look before "
                      "deleting.",
    "backups": "Whether this server's data is copied somewhere else on a schedule. Without "
               "backups, one failed disk loses everything.",
    "docker-updates": "Newer versions of the images your services run. Updates fix bugs and "
                      "security holes; a snapshot is taken first so each can be rolled back.",
    "apt": "Updates for the server's operating system packages.",
    "reboot": "The operating system installed an update (kernel or core libraries) that only takes "
              "effect after a restart.",
}


def _actions(check: dict) -> list[dict]:
    """What the user can do about a finding, in the app, in one tap."""
    cid, status = check["id"], check["status"]
    if status == "ok":
        return []
    acts: list[dict] = []
    summary = check.get("summary", "")
    if cid == "disk":
        acts += [{"kind": "open", "target": "storage", "label": "See what uses the space"},
                 {"kind": "job", "job": "prune_images", "label": "Remove unused images"}]
    elif cid in ("unhealthy", "restart-loop", "restarts", "load"):
        acts.append({"kind": "open", "target": "containers", "label": "Open containers"})
    elif cid == "ssh-root":
        acts.append({"kind": "copy", "label": "Copy the fix",
                     "command": "sudo sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/' "
                                "/etc/ssh/sshd_config && sudo systemctl reload ssh"})
    elif cid == "ssh-pw":
        acts.append({"kind": "copy", "label": "Copy the fix (check your SSH key first)",
                     "command": "sudo sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' "
                                "/etc/ssh/sshd_config && sudo systemctl reload ssh"})
    elif cid == "docker-images":
        acts.append({"kind": "job", "job": "prune_images", "label": "Remove unused images"})
    elif cid in ("app-2fa",):
        acts.append({"kind": "open", "target": "security", "label": "Turn on 2FA"})
    elif cid == "app-logins":
        acts.append({"kind": "open", "target": "activity", "label": "See the sign-ins"})
    elif cid == "docker-updates":
        acts.append({"kind": "open", "target": "updates", "label": "Review updates"})
    elif cid == "apt":
        acts.append({"kind": "copy", "label": "Copy the command",
                     "command": "sudo apt update && sudo apt upgrade"})
    elif cid.startswith("perm-"):
        acts.append({"kind": "dismiss", "label": "Dismiss",
                     "ids": check.get("permission_ids") or [cid[5:]]})
    prompts = {
        "disk": "The server disk is getting full ({s}). Find what takes the most space and suggest "
                "safe cleanups — do not delete anything without asking.",
        "memory": "Memory use is high ({s}). Which containers use the most memory, and what can I do?",
        "unhealthy": "These containers are unhealthy: {s}. Check their logs and health checks and "
                     "tell me what is wrong.",
        "restart-loop": "These containers keep restarting: {s}. Find out why from their logs.",
        "restarts": "These containers restarted several times: {s}. Find out why.",
        "ports": "Review the ports published on all interfaces ({s}) and tell me which should be "
                 "bound to 127.0.0.1 behind the reverse proxy.",
        "ssh-root": "Help me turn off SSH root login safely on this server.",
        "ssh-pw": "Help me switch SSH to keys only without locking myself out.",
        "authlog": "Look at the failed SSH logins ({s}) and tell me whether anything got in.",
        "fail2ban": "Set up fail2ban for SSH on this server.",
        "backups": "This server has no backup set up. Suggest a simple, reliable backup for its "
                   "Docker volumes and configs to an external disk or another server.",
        "docker-volumes": "List the orphaned Docker volumes, what they likely contained and which "
                          "are safe to remove. Do not remove anything.",
        "reboot": "The host wants a reboot. What will be unavailable while it restarts, and does "
                  "everything come back on its own?",
        "privileged": "Review the containers with Docker socket access ({s}) — is each one expected?",
    }
    if cid in prompts:
        acts.append({"kind": "assistant", "label": "Ask the assistant",
                     "prompt": prompts[cid].format(s=summary[:300])})
    if not cid.startswith("perm-"):
        acts.append({"kind": "mute", "label": "Accept this"})
    return acts


def _score(counts: dict) -> int:
    return max(0, 100 - 20 * counts.get("crit", 0) - 8 * counts.get("warn", 0)
               - 1 * counts.get("info", 0))


def decorate(report: dict) -> dict:
    """The report as the app shows it: categories, explanations, actions, the
    user's accepted findings, and a 0–100 score. Applied when a report is
    read, so accepting a finding takes effect without a new run."""
    muted = config.settings.get("report_muted") or {}
    for c in report.get("checks", []):
        c["category"] = CATEGORY.get(c.get("group", ""), "Other")
        c["explain"] = EXPLAIN.get(c["id"], "")
        status = c.get("original_status") or c["status"]
        if c["id"] in muted and status in ("warn", "crit", "info"):
            c["original_status"] = status
            c["status"] = "info"
            c["muted"] = True
            c["muted_note"] = (muted[c["id"]] or {}).get("note", "")
        else:
            c.pop("muted", None)
        c["actions"] = _actions({**c, "status": status if c.get("muted") else c["status"]})
        if c.get("muted"):
            c["actions"] = [{"kind": "unmute", "label": "Watch this again"}]
    counts = {s: sum(1 for c in report.get("checks", []) if c["status"] == s)
              for s in ("ok", "info", "warn", "crit")}
    report["counts"] = counts
    report["score"] = "crit" if counts["crit"] else "warn" if counts["warn"] else "ok"
    report["points"] = _score(counts)
    report["muted_count"] = sum(1 for c in report.get("checks", []) if c.get("muted"))
    return report


def set_muted(check_id: str, muted: bool, note: str = "") -> None:
    entries = dict(config.settings.get("report_muted") or {})
    if muted:
        entries[check_id] = {"note": note.strip()[:200], "time": time.time()}
    else:
        entries.pop(check_id, None)
    config.settings["report_muted"] = entries
    config.save_settings(config.settings)


async def run_report(trigger: str = "manual") -> dict:
    started = time.time()
    checks: list[dict] = []
    for group, fn in CHECK_GROUPS:
        try:
            for c in await fn():
                c["group"] = group
                checks.append(c)
        except Exception as e:
            checks.append({"id": f"err-{group.lower()}", "group": group, "title": group,
                           "icon": "❓", "status": "info", "summary": f"check failed: {e}"})
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "info", "warn", "crit")}
    report = {
        "time": started,
        "duration": round(time.time() - started, 2),
        "trigger": trigger,
        "counts": counts,
        "score": "crit" if counts["crit"] else "warn" if counts["warn"] else "ok",
        "checks": checks,
    }
    fname = time.strftime("%Y%m%d-%H%M%S", time.localtime(started)) + ".json"
    (REPORTS_DIR / fname).write_text(json.dumps(report))
    _prune_history()
    return decorate(report)


def _prune_history(keep: int = 60) -> None:
    files = sorted(REPORTS_DIR.glob("*.json"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)


def list_reports(limit: int = 30) -> list[dict]:
    out = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            r = decorate(json.loads(f.read_text()))
            out.append({"file": f.stem, "time": r["time"], "score": r["score"],
                        "counts": r["counts"], "points": r["points"],
                        "trigger": r.get("trigger", "?")})
        except Exception:
            pass
    return out


def get_report(name: str) -> dict | None:
    if not re.fullmatch(r"[0-9-]+", name):
        return None
    path = REPORTS_DIR / (name + ".json")
    if not path.exists():
        return None
    return decorate(json.loads(path.read_text()))


def latest_report() -> dict | None:
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)
    return decorate(json.loads(files[0].read_text())) if files else None


ANALYZE_SYSTEM = (
    "You are the security & operations analyst of PocketADM, a self-hosted server manager. "
    "You get a JSON health report of the user's server. Write a short, friendly analysis for "
    "a self-hoster who is not a sysadmin: 1) one-line overall verdict, 2) the issues that "
    "actually matter, ordered by importance, each with a concrete next step, 3) anything "
    "surprisingly good. Be honest, avoid alarmism, max ~200 words. Use short paragraphs or a "
    "simple list, no headings.")


async def analyze_report(report: dict, lang: str = "") -> str:
    slim = {"score": report["score"], "counts": report["counts"],
            "checks": [{k: c.get(k) for k in ("group", "title", "status", "summary",
                                              "recommendation", "muted", "muted_note")}
                       for c in report["checks"]]}
    prompt = ("Server health report (findings marked muted were accepted by the user on "
              "purpose — mention them only if they are dangerous):\n" + json.dumps(slim, indent=1))
    if lang:
        prompt += f"\n\nAnswer in language: {lang}"
    return await ai.one_shot(prompt, ANALYZE_SYSTEM, feature="insights")


# ----------------------------------------------------------- scheduler

async def _scheduler_loop() -> None:
    while True:
        cfg = config.get_report_config()
        if not cfg["auto"]:
            await asyncio.sleep(300)
            continue
        latest = latest_report()
        due = (time.time() - latest["time"]) > cfg["interval_min"] * 60 if latest else True
        if due:
            try:
                await run_report(trigger="scheduled")
            except Exception:
                pass
        await asyncio.sleep(60)


def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.ensure_future(_scheduler_loop())
