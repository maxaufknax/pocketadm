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

from . import agents, ai, config, dockerapi, permissions, sysinfo, updates

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
        status = "ok" if n == 0 else "warn" if high == 0 else "crit"
        summary = "everything up to date" if n == 0 else \
            f"{n} image update{'s' if n != 1 else ''} pending" + \
            (f" ({high} security-relevant)" if high else "")
        out.append(_check("docker-updates", "Docker image updates", "⬆️", status, summary,
                          recommendation="Apply them in the Health → Updates tab." if n else None))
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


async def check_backups() -> list[dict]:
    """Is any recognizable backup tooling in place? (Best-effort detection.)"""
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
    if found:
        return [_check("backups", "Backups", "💾", "ok",
                       "backup tooling detected: " + ", ".join(sorted(set(found))[:6]))]
    return [_check("backups", "Backups", "💾", "warn", "no backup tooling detected",
                   recommendation="Volumes and configs are one disk failure away from gone. "
                                  "Ask the Vibe agent to set up restic or borgmatic to an "
                                  "external target — and export a PocketADM settings backup "
                                  "under More → Backup.")]


async def check_agent_tasks() -> list[dict]:
    """Things the agent (or a Sentinel loop) flagged and that wait for the user:
    permission requests it ran into mid-session, and unresolved warn/crit
    findings from background agents. Shown here so they aren't forgotten."""
    out = []
    try:
        open_reqs = [p for p in permissions.list_all() if p.get("status") == "open"]
        for p in open_reqs[:6]:
            out.append(_check("perm-" + p["id"], p.get("title", "Permission needed"), "🔐",
                              "warn", p.get("explanation") or p.get("detail", ""),
                              recommendation=(p.get("fix") or "") +
                              " Resolve or dismiss it under More → Tasks & permissions."))
        if not open_reqs:
            out.append(_check("perms", "Agent permission requests", "🔐", "ok",
                              "nothing waiting for you"))
    except Exception:
        pass
    try:
        notifs = agents.notifications(limit=30).get("items", [])
        flagged = [n for n in notifs if n.get("status") in ("warn", "crit")][:5]
        for n in flagged:
            out.append(_check("sentinel-" + str(n.get("id", "")),
                              n.get("title", "Background agent finding"), "🤖",
                              n.get("status", "warn"),
                              (n.get("body") or "")[:300] +
                              (f" (seen {n['count']}×)" if n.get("count", 1) > 1 else ""),
                              recommendation="Reported by a background agent — open the bell "
                                             "in the top bar for details, or fix it with AI."))
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
    return report


def _prune_history(keep: int = 60) -> None:
    files = sorted(REPORTS_DIR.glob("*.json"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)


def list_reports(limit: int = 30) -> list[dict]:
    out = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            r = json.loads(f.read_text())
            out.append({"file": f.stem, "time": r["time"], "score": r["score"],
                        "counts": r["counts"], "trigger": r.get("trigger", "?")})
        except Exception:
            pass
    return out


def get_report(name: str) -> dict | None:
    if not re.fullmatch(r"[0-9-]+", name):
        return None
    path = REPORTS_DIR / (name + ".json")
    if not path.exists():
        return None
    return json.loads(path.read_text())


def latest_report() -> dict | None:
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)
    return json.loads(files[0].read_text()) if files else None


ANALYZE_SYSTEM = (
    "You are the security & operations analyst of Helmsman, a self-hosted server manager. "
    "You get a JSON health report of the user's server. Write a short, friendly analysis for "
    "a self-hoster who is not a sysadmin: 1) one-line overall verdict, 2) the issues that "
    "actually matter, ordered by importance, each with a concrete next step, 3) anything "
    "surprisingly good. Be honest, avoid alarmism, max ~250 words. Use markdown headings/lists.")


async def analyze_report(report: dict, lang: str = "") -> str:
    slim = {"score": report["score"], "counts": report["counts"],
            "checks": [{k: c.get(k) for k in ("group", "title", "status", "summary", "recommendation")}
                       for c in report["checks"]]}
    prompt = "Server health report:\n" + json.dumps(slim, indent=1)
    if lang:
        prompt += f"\n\nAnswer in language: {lang}"
    return await ai.one_shot(prompt, ANALYZE_SYSTEM)


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
