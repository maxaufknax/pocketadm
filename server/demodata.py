"""Sample data for demo mode — a believable little homeserver.

Used by dockerapi when HELMSMAN_DEMO is set and no Docker socket is mounted,
so a public demo instance can show the full UI without touching a real host.
"""
import random
import time

from . import config

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
    env = [f"TZ=Europe/Berlin", "PUID=1000", "PGID=1000",
           f"{c['compose_service'].upper()}_PASSWORD=never-shown"]
    from . import dockerapi  # noqa: E402 — masking lives with the real data
    healthy = c["health"] == "healthy"
    return {"id": c["id"], "name": c["name"], "image": c["image"], "image_id": "d" * 12,
            "created": "2026-06-01T00:00:00Z", "started_at": "2026-07-01T00:00:00Z",
            "finished_at": "", "state": c["state"], "exit_code": 0, "oom_killed": False,
            "error": "", "health": c["health"], "health_failing_streak": 0,
            "health_log": [{"start": "2026-07-11T09:00:00", "end": "2026-07-11T09:00:01",
                            "exit_code": 0, "output": "ok"}] if healthy else [],
            "restart_count": 0, "restart_policy": "unless-stopped", "restart_max": 0,
            "privileged": False, "env_count": len(env), "env": dockerapi.mask_env(env),
            "cmd": "", "entrypoint": "/init", "working_dir": "/app", "user": "",
            "hostname": c["name"],
            "mounts": [{"source": f"/srv/{c['compose_project']}/data", "dest": "/data",
                        "rw": True, "type": "bind", "name": ""}],
            "networks": [c["compose_project"] + "_default"],
            "network_details": [{"name": c["compose_project"] + "_default",
                                 "ip": "172.18.0." + str(int(c["id"], 16) % 200 + 2),
                                 "gateway": "172.18.0.1", "aliases": [c["compose_service"]]}],
            "network_mode": "bridge",
            "ports": [{"private": p["private"], "proto": "tcp", "public": p["public"],
                       "ip": p["ip"]} for p in c["ports"]],
            "resources": {"memory_limit": 0, "cpus": 0, "cpu_shares": 0, "pids_limit": 0},
            "log_driver": "json-file",
            "compose_dir": f"/srv/{c['compose_project']}",
            "compose_files": f"/srv/{c['compose_project']}/docker-compose.yml",
            "labels": {"com.docker.compose.project": c["compose_project"],
                       "com.docker.compose.service": c["compose_service"]}}


def container_top(cid: str) -> dict:
    c = _by_id(cid) or list_containers()[0]
    if c["state"] != "running":
        return {"titles": [], "processes": [], "note": "The container is not running."}
    rng = random.Random(cid)
    procs = [["1", "root", "0.0", "0.1", "12-03:11:09", "/init"],
             [str(rng.randint(80, 200)), "abc", f"{rng.uniform(0.2, 6):.1f}",
              f"{rng.uniform(1, 9):.1f}", "12-03:10:58", c["compose_service"] + " --serve"]]
    return {"titles": ["PID", "USER", "%CPU", "%MEM", "ELAPSED", "COMMAND"], "processes": procs}


def container_events(cid: str) -> list[dict]:
    base = _NOW
    return [
        {"t": base - 3600 * 5, "action": "start", "summary": "started", "severity": "ok"},
        {"t": base - 3600 * 5 - 40, "action": "die", "summary": "exited (exit code 0)",
         "severity": "ok"},
        {"t": base - 86400 * 2, "action": "create", "summary": "was created", "severity": "ok"},
    ]


async def follow_logs(cid: str):
    import asyncio
    rng = random.Random(cid)
    for i in range(30):
        await asyncio.sleep(2)
        yield f"2026-07-11T10:{i:02d}:00Z {rng.choice(_LOG_LINES)}\n"


def container_logs(cid: str, tail: int = 200) -> str:
    rng = random.Random(cid)
    lines = [f"2026-07-11T0{i % 10}:00:00Z {rng.choice(_LOG_LINES)}"
             for i in range(min(tail, 40))]
    return "\n".join(lines) + "\n"


def container_stats(cid: str) -> dict:
    rng = random.Random(cid + str(int(time.time() // 3)))
    return {"cpu_percent": round(rng.uniform(0.1, 8.0), 1), "cpu_known": True,
            "mem_usage": rng.randint(40, 900) * 1024 * 1024,
            "mem_limit": 8 * 1024 ** 3,
            "net_rx": rng.randint(10, 900) * 1024 ** 2, "net_tx": rng.randint(5, 300) * 1024 ** 2,
            "blk_read": rng.randint(1, 900) * 1024 ** 2, "blk_write": rng.randint(1, 400) * 1024 ** 2,
            "pids": rng.randint(4, 40),
            "net_rx_rate": rng.uniform(200, 40000), "net_tx_rate": rng.uniform(100, 9000)}


def inspect_image(name: str) -> dict:
    # a fake local digest that never matches the registry -> demo shows updates
    return {"Id": "sha256:" + "d" * 64, "Created": "2026-03-01T00:00:00.0Z",
            "RepoDigests": [name.split("@")[0].rsplit(":", 1)[0] + "@sha256:" + "0" * 64]}


def engine_info() -> dict:
    cs = list_containers()
    return {"containers": len(cs), "running": sum(c["state"] == "running" for c in cs),
            "images": len(cs) + 4, "version": "27.0 (demo)", "os": "Demo Linux"}


DEMO_FS = config.DATA_DIR / "demo-fs"


def _png(width: int, height: int) -> bytes:
    """A small sunset gradient as a PNG — no imaging library needed."""
    import struct
    import zlib
    rows = b""
    for y in range(height):
        t = y / max(height - 1, 1)
        r, g, b = int(255 - 70 * t), int(140 - 90 * t), int(60 + 110 * t)
        rows += b"\x00" + bytes((r, g, b)) * width
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


_DEMO_TREE = {
    "etc/hostname": "homeserver\n",
    "etc/hosts": "127.0.0.1 localhost\n127.0.1.1 homeserver\n192.168.1.10 homeserver.lan\n",
    "etc/fstab": ("UUID=8c1e-root  /            ext4  defaults        0 1\n"
                  "UUID=3F2A-EFI   /boot/efi    vfat  umask=0077      0 1\n"
                  "UUID=b7d1-usb   /mnt/backup  ext4  defaults,nofail 0 2\n"),
    "etc/docker/daemon.json": '{\n  "log-driver": "json-file",\n  "log-opts": {"max-size": "10m", "max-file": "3"}\n}\n',
    "etc/systemd/system/backup.service": ("[Unit]\nDescription=Nightly volume backup\n\n[Service]\nType=oneshot\n"
                                          "ExecStart=/srv/backup/backup.sh\n"),
    "etc/systemd/system/backup.timer": "[Timer]\nOnCalendar=*-*-* 02:30\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n",
    "home/admin/.bashrc": "alias dps='docker ps --format \"table {{.Names}}\\t{{.Status}}\"'\nexport EDITOR=nano\n",
    "home/admin/notes.md": ("# Homeserver notes\n\n| Service | URL | Notes |\n| --- | --- | --- |\n"
                            "| Nextcloud | cloud.example.com | files, calendar |\n"
                            "| Jellyfin | media.example.com | transcodes on the iGPU |\n"
                            "| Vaultwarden | vault.example.com | VPN only |\n\n"
                            "## To do\n\n- [x] Move backups to the USB drive\n- [ ] Raise MariaDB max_connections\n"),
    "home/admin/scripts/cleanup.sh": "#!/bin/sh\n# frees the Jellyfin transcode cache\nrm -rf /srv/jellyfin/cache/transcodes/*\n",
    "srv/README.md": "Every service lives in its own folder with a docker-compose.yml.\n",
    "srv/nextcloud/docker-compose.yml": ("services:\n  nextcloud:\n    image: nextcloud:31-apache\n    restart: unless-stopped\n"
                                         "    volumes:\n      - ./data:/var/www/html\n    env_file: .env\n"
                                         "  db:\n    image: mariadb:11\n    restart: unless-stopped\n"),
    "srv/nextcloud/.env": "MYSQL_DATABASE=nextcloud\nMYSQL_USER=nextcloud\nMYSQL_PASSWORD=change-me\n",
    "srv/jellyfin/docker-compose.yml": ("services:\n  jellyfin:\n    image: jellyfin/jellyfin:latest\n"
                                        "    devices:\n      - /dev/dri:/dev/dri\n    volumes:\n      - ./config:/config\n"
                                        "      - /mnt/backup/media:/media:ro\n"),
    "srv/vaultwarden/docker-compose.yml": ("services:\n  vaultwarden:\n    image: vaultwarden/server:latest\n"
                                           "    ports:\n      - 127.0.0.1:8081:80\n"),
    "srv/backup/backup.sh": "#!/bin/bash\nset -e\ntar --zstd -cf /mnt/backup/nightly/$(date +%F).tar.zst /srv\n",
    "var/log/syslog": "".join(f"Oct  8 0{h}:1{h} homeserver systemd[1]: Started backup.service - Nightly volume backup.\n"
                              for h in range(2, 8)),
    "var/log/auth.log": ("Oct  8 07:58:12 homeserver sshd[2210]: Accepted publickey for admin from 192.168.1.20\n"
                         "Oct  8 08:03:41 homeserver sshd[2299]: Failed password for root from 203.0.113.9\n"),
    "mnt/backup/nightly/README.txt": "Nightly archives of /srv, kept for 14 days.\n",
    "mnt/backup/media/README.txt": "Movies and music for Jellyfin.\n",
}


def seed_files() -> None:
    """A believable little server to browse in the demo: /etc, /home, /srv,
    logs and a USB backup drive — instead of the demo container's insides."""
    marker = DEMO_FS / ".seeded-v1"
    if marker.exists():
        return
    for rel, content in _DEMO_TREE.items():
        path = DEMO_FS / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    pictures = DEMO_FS / "home/admin/Pictures"
    pictures.mkdir(parents=True, exist_ok=True)
    (pictures / "sunset.png").write_bytes(_png(240, 160))
    for rel in ("opt", "root", "tmp", "mnt/backup/nightly", "srv/jellyfin/config", "srv/nextcloud/data"):
        (DEMO_FS / rel).mkdir(parents=True, exist_ok=True)
    for day in range(1, 8):
        (DEMO_FS / f"mnt/backup/nightly/2026-10-0{day}.tar.zst").write_bytes(b"\x28\xb5\x2f\xfd" + b"\0" * 2048)
    marker.write_text("1")


def storage() -> dict:
    gb = 1024 ** 3
    rows = [
        ("/", "/dev/nvme0n1p2", "ext4", "", "Samsung SSD 980", "nvme0n1", "nvme", False,
         "system", 476 * gb, 296 * gb),
        ("/mnt/backup", "/dev/sda1", "ext4", "Backup", "Portable SSD T7", "sda", "usb", False,
         "external", 931 * gb, 402 * gb),
        ("/boot/efi", "/dev/nvme0n1p1", "vfat", "", "Samsung SSD 980", "nvme0n1", "nvme", False,
         "boot", 1 * gb, int(0.04 * gb)),
    ]
    root = str(DEMO_FS)
    return {"filesystems": [
        {"mount": m, "path": root if m == "/" else root + m, "device": dev, "fstype": fs,
         "label": label, "model": model,
         "disk": disk, "transport": tr, "removable": rem, "external": kind == "external",
         "kind": kind, "total": total, "used": used, "free": total - used,
         "percent": round(100 * used / total, 1), "browsable": kind != "boot"}
        for m, dev, fs, label, model, disk, tr, rem, kind, total, used in rows]}


_DEMO_FEED = [
    ("containers", "docker.restart", "nextcloud restarted", "", "info", "nextcloud"),
    ("security", "ssh.login", "admin logged in over SSH", "from 192.168.1.20 (publickey)", "info", "admin"),
    ("containers", "docker.health", "vaultwarden is healthy again", "", "ok", "vaultwarden"),
    ("updates", "docker.image.pull", "Image pulled: grafana/grafana:11.1.0", "", "ok", "grafana"),
    ("security", "ssh.failed", "37 failed SSH logins from 203.0.113.9", "tried: root, admin, ubnt", "warn", "203.0.113.9"),
    ("system", "systemd.finished", "Finished: Nightly volume backup", "", "ok", "backup"),
    ("app", "pocketadm.agent_tool", "The assistant acted", "run_command · docker compose pull jellyfin", "info", ""),
    ("network", "net.up", "Internet is back after about 2 min", "", "ok", ""),
    ("security", "fail2ban.ban", "fail2ban banned 198.51.100.4", "jail sshd", "info", "198.51.100.4"),
    ("updates", "apt.batch", "14 packages changed by apt", "openssl, libssl3, curl, git…", "info", ""),
]


def start_activity() -> None:
    """A believable history, and now and then a new event — so the live feed
    in the demo shows what it does."""
    import asyncio
    from . import activity
    if not activity.EVENTS:
        activity.load()
    if not activity.EVENTS:
        now = time.time()
        for i, (cat, kind, title, detail, sev, target) in enumerate(_DEMO_FEED):
            activity.push(cat, kind, title, detail=detail, severity=sev, target=target,
                          source="demo", t=now - (len(_DEMO_FEED) - i) * 1900)

    async def ticker():
        rng = random.Random()
        while True:
            await asyncio.sleep(rng.uniform(25, 45))
            cat, kind, title, detail, sev, target = rng.choice(_DEMO_FEED)
            activity.push(cat, kind, title, detail=detail, severity=sev, target=target,
                          source="demo")

    asyncio.ensure_future(ticker())


def accounts() -> dict:
    def row(id_, name, vendor, engine, key_provider, subscription, brand, *, key=False,
            signed=False, detail="", plan="", used=()):
        return {"id": id_, "name": name, "vendor": vendor, "engine": engine,
                "key_provider": key_provider, "subscription": subscription,
                "key_hint": "", "brand": brand, "key_set": key, "can_subscribe": bool(engine),
                "cli_installed": signed, "cli_version": "2.1.210" if signed else "",
                "signed_in": signed, "detail": detail, "plan": plan,
                "connected": key or signed, "used_for": list(used)}
    return {"accounts": [
        row("anthropic", "Claude", "Anthropic", "claude-code", "anthropic", "Claude Pro or Max",
            "anthropic", signed=True, detail="Signed in with your Claude subscription",
            plan="max", used=("Assistant",)),
        row("openai", "ChatGPT", "OpenAI", "codex", "openai", "ChatGPT Plus or Pro", "openai"),
        row("mistral", "Mistral", "Mistral AI", "mistral-vibe", "mistral",
            "Le Chat Pro, with Mistral Vibe", "mistral", key=True, used=("Watch", "Explanations")),
        row("openrouter", "OpenRouter", "OpenRouter", "", "openrouter", "", "openrouter"),
    ], "local": {"running": False, "base": "", "models": 0, "used_for": []},
        "routes": {
            "assistant": {"label": "Assistant", "provider": "claude-code", "model": "default",
                          "custom": True, "provider_label": "Claude Code"},
            "watch": {"label": "Watch", "provider": "mistral", "model": "mistral-medium-latest",
                      "custom": True, "provider_label": "Mistral API"},
            "insights": {"label": "Explanations", "provider": "mistral",
                         "model": "mistral-small-latest", "custom": True,
                         "provider_label": "Mistral API"}}}


# ============================================================ seed content
# A public demo looks dead if Vibe, Health and the bell are empty. Seed one
# believable finished agent session, a health report and a few notifications so
# a reviewer sees the whole product alive — without any live model or host.

import json  # noqa: E402
import secrets  # noqa: E402
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
    """Watch messages as the real watch writes them: prose, with links."""
    base = _NOW
    raw = [
        ("important", "warn", "backup", "No backup ran for the vault in a week",
         "The nightly volume backup has not finished since last Tuesday — the backup container "
         "exits right after starting because its target disk is not mounted. Plug the USB drive "
         "back in and run `docker start backup-runner`, or ask the assistant to check the mount.",
         2 * 3600, [{"kind": "container", "label": "Open backup-runner", "target": "backup-runner"},
                    {"kind": "open", "label": "Health", "target": "checks"}]),
        ("info", "info", "disk-trend", "The disk fills up in about six weeks",
         "Root is at 78% and grew by 2.1 GB a day this week, mostly Jellyfin's transcode cache. "
         "At this pace it is full in roughly six weeks; clearing the cache frees about 9 GB.",
         20 * 3600, [{"kind": "open", "label": "Storage", "target": "storage"}]),
        ("critical", "crit", "nextcloud-down", "Nextcloud was down for 4 minutes",
         "Nextcloud stopped answering at 14:02 after its database ran out of connections; it "
         "recovered on its own at 14:06 when the cron job finished. If it happens again, raise "
         "max_connections in MariaDB.",
         2 * 86400, [{"kind": "container", "label": "Open nextcloud", "target": "nextcloud"}]),
    ]
    out = []
    import secrets
    for importance, status, topic, title, body, ago, actions in raw:
        out.append({"id": secrets.token_hex(5), "time": base - ago, "source": "watch",
                    "status": status, "title": title, "body": body, "fp": topic,
                    "count": 1, "last_seen": base - ago, "kind": "watch",
                    "importance": importance, "topic": topic, "actions": actions + [
                        {"kind": "assistant", "label": "Ask the assistant",
                         "prompt": f"The watch wrote: \"{body}\" — what should I do?"}],
                    "steps": [{"tool": "run_command", "detail": "docker ps -a --filter name=backup",
                               "output": "backup-runner  Exited (1) 2 hours ago", "ms": 140}]})
    return out


def seed_channel() -> None:
    """The watch's channel in the demo: what it wrote on its own, and a short
    exchange with the user — refreshed when it has grown stale."""
    from . import channel
    try:
        data = json.loads(channel.CHANNEL_FILE.read_text())
        newest = max((m.get("t", 0) for m in data.get("messages", [])), default=0)
        if data.get("messages") and time.time() - newest < 3 * 86400:
            return
    except (OSError, ValueError):
        pass
    now = time.time()

    def msg(ago, role, text, **fields):
        base = {"id": "m" + secrets.token_hex(5), "t": now - ago, "role": role, "text": text,
                "detail": "", "title": "", "importance": "info", "topic": "", "kind": "observe",
                "actions": [], "feedback": "", "reply_to": ""}
        base.update(fields)
        return base

    messages = [
        msg(3 * 86400, "watch", "I am keeping an eye on this server now. I write when something is "
            "worth knowing — usually rarely, and only critical things at night. Ask me anything here.",
            kind="system", title="The watch is on"),
        msg(2 * 86400, "watch", "Nextcloud was down for four minutes at 14:02 — its database ran "
            "out of connections. It recovered on its own when the cron job finished.",
            detail="MariaDB logged \"Too many connections\" 312 times between 14:02 and 14:06. "
                   "If it happens again, raise max_connections from 100 to 200 in the MariaDB config.",
            title="Nextcloud was down briefly", importance="critical", topic="nextcloud-down",
            kind="incident", actions=[{"kind": "container", "label": "Open nextcloud", "target": "nextcloud"}]),
        msg(20 * 3600, "watch", "The disk fills up in about six weeks at this pace — mostly "
            "Jellyfin's transcode cache.",
            detail="Root is at 78% and grew by 2.1 GB a day this week. Clearing "
                   "/var/lib/jellyfin/transcodes frees about 9 GB.",
            title="Disk full in about six weeks", topic="disk-trend",
            actions=[{"kind": "open", "label": "Storage", "target": "storage"}]),
        msg(19 * 3600, "user", "Is it safe to delete the transcode cache?", kind="chat"),
        msg(19 * 3600 - 40, "watch", "Yes — Jellyfin rebuilds it when someone watches something. "
            "Nothing is playing right now, so now is a good time.", kind="chat",
            actions=[{"kind": "container", "label": "Open jellyfin", "target": "jellyfin"}]),
        msg(2 * 3600, "watch", "The vault backup has not run for a week: the backup container "
            "exits right away because its target drive is not mounted.",
            detail="backup-runner exited with code 1 every night since Tuesday (\"/mnt/backup: no "
                   "such device\"). Plug the USB drive back in, then run `docker start backup-runner`.",
            title="No vault backup for a week", importance="important", topic="backup",
            kind="incident", actions=[{"kind": "container", "label": "Open backup-runner",
                                       "target": "backup-runner"},
                                      {"kind": "open", "label": "Health", "target": "checks"}]),
    ]
    try:
        channel.CHANNEL_FILE.write_text(json.dumps({"messages": messages, "read": now - 3 * 3600}))
    except OSError:
        pass


def watch_status() -> dict:
    return {
        "settings": {"enabled": True, "lang": "en", "timezone": "Europe/Berlin",
                     "interval_min": 180, "quiet_start": "23:00", "quiet_end": "07:30",
                     "info_per_day": 3, "important_per_day": 8, "weekly": True,
                     "knowledge": "Jellyfin is only used in the evenings.", "budget_usd": 5.0,
                     "push_min": "important", "ntfy_url": "", "matrix_homeserver": "",
                     "matrix_room": "", "matrix_token_set": False, "paused_until": 0, "mutes": []},
        "route": {"provider": "mistral", "model": "mistral-medium-latest",
                  "label": "Mistral API", "usable": True},
        "running": False, "paused": False, "quiet_now": False,
        "last_round": _NOW - 3600, "next_round": _NOW + 7200,
        "sent_today": {"critical": 0, "important": 1, "info": 0},
        "spent_30d": 0.84, "budget_left": 4.16, "pending_events": 0,
        "runs": [{"t": _NOW - 3600, "kind": "observe", "decision": "silent",
                  "reason": "all quiet", "provider": "mistral", "model": "mistral-medium-latest",
                  "cost": 0.03},
                 {"t": _NOW - 2 * 3600, "kind": "incident", "decision": "notify",
                  "importance": "important", "topic": "backup", "provider": "mistral",
                  "model": "mistral-medium-latest", "cost": 0.05}],
        "held": [], "memory": ["Disk / 78% on Monday", "Minecraft is stopped on purpose"],
    }


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

        # the file browser shows the sample server, as if it were the host
        seed_files()
        from . import files
        files.HOST = str(DEMO_FS)
        config.settings["workspaces"] = [str(DEMO_FS)]

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

        # the watch's sample messages, refreshed when they have grown stale so a
        # visitor never meets "3 weeks ago" on the first screen
        notif_file = config.DATA_DIR / "notifications.json"
        try:
            existing = json.loads(notif_file.read_text()) if notif_file.exists() else []
        except ValueError:
            existing = []
        newest = max((n.get("time", 0) for n in existing), default=0)
        if not any(n.get("kind") == "watch" for n in existing) or time.time() - newest > 3 * 86400:
            notif_file.write_text(json.dumps(_seed_notifications()))
    except Exception:
        pass
