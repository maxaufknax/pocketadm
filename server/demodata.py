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
