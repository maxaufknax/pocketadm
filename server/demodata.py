"""Sample data for demo mode — a believable little homeserver.

Used by dockerapi when HELMSMAN_DEMO is set and no Docker socket is mounted,
so a public demo instance can show the full UI without touching a real host.
"""
import random
import time

_NOW = time.time()

CONTAINERS = [
    # name, image, state, ports, project, service, hours_up, health
    ("nextcloud",        "nextcloud:29-apache",                "running", [(80, 8081)],  "cloud", "nextcloud", 720, "healthy"),
    ("nextcloud-db",     "mariadb:11",                         "running", [],            "cloud", "db",        720, ""),
    ("nextcloud-redis",  "redis:7-alpine",                     "running", [],            "cloud", "redis",     720, ""),
    ("jellyfin",         "jellyfin/jellyfin:latest",           "running", [(8096, 8096)],"media", "jellyfin",  312, ""),
    ("navidrome",        "deluan/navidrome:latest",            "running", [(4533, 4533)],"media", "navidrome", 312, ""),
    ("vaultwarden",      "vaultwarden/server:latest",          "running", [(80, 8222)],  "vault", "vaultwarden", 96, "healthy"),
    ("pihole",           "pihole/pihole:latest",               "running", [(53, 53), (80, 8053)], "dns", "pihole", 1400, "healthy"),
    ("grafana",          "grafana/grafana:11.1.0",             "running", [(3000, 3000)],"monitoring", "grafana", 96, ""),
    ("prometheus",       "prom/prometheus:latest",             "running", [(9090, 9090)],"monitoring", "prometheus", 96, ""),
    ("uptime-kuma",      "louislam/uptime-kuma:1",             "running", [(3001, 3001)],"monitoring", "uptime-kuma", 96, "healthy"),
    ("gitea",            "gitea/gitea:1.22",                   "running", [(3000, 3002), (22, 2222)], "git", "server", 480, ""),
    ("helmsman",         "ghcr.io/maxaufknax/helmsman:latest", "running", [(8080, 8090)],"helmsman", "helmsman", 24, ""),
    ("backup-runner",    "offen/docker-volume-backup:v2",      "exited",  [],            "backup", "backup", 0, ""),
]

_LOG_LINES = [
    "INFO  ready — listening on 0.0.0.0",
    "INFO  request completed in 12ms",
    "INFO  scheduled task finished ok",
    "WARN  slow query took 1.2s",
    "INFO  health check passed",
    "INFO  cache hit ratio 94%",
]


def list_containers(all_: bool = True) -> list[dict]:
    out = []
    for i, (name, image, state, ports, project, service, hours, health) in enumerate(CONTAINERS):
        if not all_ and state != "running":
            continue
        out.append({
            "id": f"{i:012x}"[:12],
            "name": name,
            "image": image,
            "state": state,
            "status": (f"Up {hours // 24} days" if hours >= 48 else f"Up {hours} hours")
                      + (f" ({health})" if health else "") if state == "running"
                      else "Exited (0) 2 hours ago",
            "health": health,
            "ports": [{"private": pr, "public": pu, "type": "tcp", "ip": "0.0.0.0"}
                      for pr, pu in ports],
            "compose_project": project,
            "compose_service": service,
            "created": int(_NOW - hours * 3600),
            "mounts_docker_sock": name == "helmsman",
        })
    return sorted(out, key=lambda x: (x["state"] != "running", x["name"]))


def _by_id(cid: str) -> dict | None:
    return next((c for c in list_containers()
                 if c["id"].startswith(cid) or c["name"] == cid), None)


def inspect_container(cid: str) -> dict:
    c = _by_id(cid) or list_containers()[0]
    return {"Id": c["id"] * 5, "Name": "/" + c["name"],
            "Config": {"Image": c["image"], "Env": [], "Labels": {
                "com.docker.compose.project": c["compose_project"],
                "com.docker.compose.service": c["compose_service"]}},
            "State": {"Status": c["state"], "StartedAt": "2026-07-01T00:00:00Z",
                      "Health": {"Status": c["health"]} if c["health"] else None},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
            "RestartCount": 0, "Created": "2026-06-01T00:00:00Z",
            "Mounts": [], "NetworkSettings": {"Networks": {"bridge": {}}}}


def container_detail(cid: str) -> dict:
    c = _by_id(cid) or list_containers()[0]
    return {"id": c["id"], "name": c["name"], "image": c["image"],
            "created": "2026-06-01T00:00:00Z", "started_at": "2026-07-01T00:00:00Z",
            "state": c["state"], "health": c["health"], "restart_count": 0,
            "restart_policy": "unless-stopped", "privileged": False,
            "env_count": 6, "cmd": "",
            "mounts": [{"source": f"/srv/{c['compose_project']}", "dest": "/data",
                        "rw": True, "type": "bind"}],
            "networks": ["bridge"],
            "labels": {"com.docker.compose.project": c["compose_project"],
                       "com.docker.compose.service": c["compose_service"]}}


def container_logs(cid: str, tail: int = 200) -> str:
    rng = random.Random(cid)
    lines = [f"2026-07-11T0{i % 10}:00:00Z {rng.choice(_LOG_LINES)}"
             for i in range(min(tail, 40))]
    return "\n".join(lines) + "\n"


def container_stats(cid: str) -> dict:
    rng = random.Random(cid + str(int(_NOW // 30)))
    return {"cpu_percent": round(rng.uniform(0.1, 8.0), 1),
            "mem_usage": rng.randint(40, 900) * 1024 * 1024,
            "mem_limit": 8 * 1024 ** 3}


def inspect_image(name: str) -> dict:
    # a fake local digest that never matches the registry -> demo shows updates
    return {"Id": "sha256:" + "d" * 64, "Created": "2026-03-01T00:00:00.0Z",
            "RepoDigests": [name.split("@")[0].rsplit(":", 1)[0] + "@sha256:" + "0" * 64]}


def engine_info() -> dict:
    cs = list_containers()
    return {"containers": len(cs), "running": sum(c["state"] == "running" for c in cs),
            "images": len(cs) + 4, "version": "27.0 (demo)", "os": "Demo Linux"}


# ============================================================ seed content
# A public demo looks dead if Vibe, Health and the bell are empty. Seed one
# believable finished agent session, a health report and a few notifications so
# a reviewer sees the whole product alive — without any live model or host.

import json  # noqa: E402
import time  # noqa: E402

from . import config  # noqa: E402

_SEED_CHAT_ID = "de300000c0ffee01"


def _seed_chat() -> dict:
    t0 = _NOW - 3 * 3600
    messages = [
        {"role": "user",
         "content": "Jellyfin keeps buffering in the evenings. Can you find out why and fix it?"},
        {"role": "assistant",
         "content": "Let me look at how hard Jellyfin is working and check its recent logs.",
         "tool_calls": [{"id": "c1", "name": "run_command",
                         "args": {"command": "docker stats --no-stream jellyfin"}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": "CONTAINER   CPU %     MEM USAGE / LIMIT\n"
                    "jellyfin    182.4%    1.83GiB / 8GiB"},
        {"role": "assistant",
         "content": "182% CPU means it is transcoding video on the CPU. Let me confirm from the logs.",
         "tool_calls": [{"id": "c2", "name": "run_command",
                         "args": {"command": "docker logs --tail 5 jellyfin"}}]},
        {"role": "tool", "tool_call_id": "c2",
         "content": "[INFO] Playback: DirectPlay not possible, falling back to transcode\n"
                    "[INFO] Transcoding H265 -> H264 (software, libx264)\n"
                    "[WARN] Encoder falling behind real-time"},
        {"role": "assistant",
         "content": "Found it. Jellyfin is **software-transcoding** every evening stream, so "
                    "the CPU maxes out and playback buffers. Your box has an Intel iGPU that can "
                    "do this in hardware (QSV) almost for free.\n\n"
                    "**Fix:** pass the iGPU into the container and enable QSV hardware "
                    "acceleration. I can add `devices: [/dev/dri:/dev/dri]` to the Jellyfin "
                    "compose and turn on QSV in its config — want me to apply it?\n\n"
                    "_(In this demo nothing is actually changed — this is a saved sample "
                    "conversation.)_"},
    ]
    return {"id": _SEED_CHAT_ID, "title": "Jellyfin keeps buffering in the evenings",
            "created": t0, "updated": t0 + 240, "archived": False,
            "messages": messages,
            "usage": {"input": 2140, "output": 386, "cost": 0.0, "turns": 1}}


def _seed_report() -> dict:
    now = _NOW - 1800
    checks = [
        {"id": "res-cpu", "group": "Resources", "title": "CPU & memory", "icon": "📊",
         "status": "ok", "summary": "Load is healthy (0.2), 2.0 GiB RAM free of 7.7 GiB."},
        {"id": "disk", "group": "Resources", "title": "Disk space", "icon": "💾",
         "status": "warn", "summary": "Root filesystem 78% full (137 GB free).",
         "recommendation": "Prune old Docker images: `docker image prune -a` frees ~6 GB."},
        {"id": "ssh-root", "group": "SSH", "title": "SSH root login", "icon": "🔐",
         "status": "ok", "summary": "PermitRootLogin is disabled and password auth is off."},
        {"id": "fail2ban", "group": "SSH", "title": "fail2ban", "icon": "🛡️",
         "status": "ok", "summary": "Active — 3 IPs currently banned on the sshd jail."},
        {"id": "updates", "group": "Updates", "title": "Container image updates", "icon": "⬆️",
         "status": "warn", "summary": "2 images have newer versions (nextcloud, vaultwarden).",
         "recommendation": "Review and apply from the Updates tab; snapshots let you roll back."},
        {"id": "backups", "group": "Backups", "title": "Volume backups", "icon": "🗄️",
         "status": "crit", "summary": "No backup ran in the last 7 days for 'cloud' and 'vault'.",
         "recommendation": "Set up a scheduled volume backup — this is your biggest risk."},
        {"id": "ports", "group": "Network", "title": "Exposed ports", "icon": "🌐",
         "status": "ok", "summary": "Only 80/443 are public; everything else is bound to localhost."},
    ]
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "info", "warn", "crit")}
    return {"time": now, "duration": 1.9, "trigger": "scheduled", "counts": counts,
            "score": "crit", "checks": checks}


def _seed_notifications() -> list[dict]:
    base = _NOW
    raw = [
        ("update", "warn", "2 container updates available",
         "nextcloud and vaultwarden have newer images. Review them in the Updates tab.", 3600),
        ("security", "crit", "No recent backups",
         "The 'cloud' and 'vault' stacks have not been backed up in 7 days.", 7200),
        ("health", "ok", "Nightly health check passed",
         "6 of 7 checks are green. One warning: disk at 78%.", 10800),
    ]
    out = []
    for src, status, title, body, ago in raw:
        import hashlib
        import re
        import secrets
        fp = hashlib.sha1(f"{src}|{status}|{re.sub(r'[\\W\\d]+', ' ', title.lower()).strip()}"
                          .encode()).hexdigest()[:16]
        out.append({"id": secrets.token_hex(5), "time": base - ago, "source": src,
                    "status": status, "title": title, "body": body, "fp": fp,
                    "count": 1, "last_seen": base - ago})
    return out


def seed() -> None:
    """Idempotently plant sample content so the demo isn't empty. Safe to call
    on every startup — each store is only seeded when it is still empty."""
    try:
        # a stable identity + skip the first-run wizard so the demo lands
        # straight on the dashboard (both survive a wiped volume — re-seeded here)
        if not config.get_server_name():
            config.set_server_name("PocketADM Demo")
        if not config.get_onboarded():
            config.set_onboarded()

        chats_dir = config.DATA_DIR / "chats"
        chats_dir.mkdir(exist_ok=True)
        seed_chat_file = chats_dir / f"{_SEED_CHAT_ID}.json"
        if not seed_chat_file.exists():
            seed_chat_file.write_text(json.dumps(_seed_chat()))

        reports_dir = config.DATA_DIR / "reports"
        reports_dir.mkdir(exist_ok=True)
        if not any(reports_dir.glob("*.json")):
            rep = _seed_report()
            fname = time.strftime("%Y%m%d-%H%M%S", time.localtime(rep["time"])) + ".json"
            (reports_dir / fname).write_text(json.dumps(rep))

        notif_file = config.DATA_DIR / "notifications.json"
        if not notif_file.exists():
            notif_file.write_text(json.dumps(_seed_notifications()))
    except Exception:
        pass
