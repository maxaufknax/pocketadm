"""Update detection: docker images (registry digest comparison, no pull needed)
and host apt packages (when accessible). Optional AI explanations.

Updates are enriched with service metadata (friendly name, icon, category),
a priority classification, image age and changelog links, and can be applied
as a background job: pull with live progress, then recreate the containers.
"""
import asyncio
import datetime
import json
import re
import time

import httpx

from . import ai, config, dockerapi, jobs

_cache: dict = {"time": 0, "result": None}
CACHE_TTL = 1800

# Known services: substring of image name -> friendly metadata.
# security=True marks software whose updates are typically security-relevant
# (auth, proxies, password managers, databases, anything internet-facing).
SERVICE_META = {
    "traefik":      ("Traefik", "🚦", "Reverse Proxy", True),
    "nginx":        ("Nginx", "🌐", "Web Server", True),
    "caddy":        ("Caddy", "🌐", "Web Server", True),
    "authentik":    ("Authentik", "🔑", "Authentication", True),
    "authelia":     ("Authelia", "🔑", "Authentication", True),
    "vaultwarden":  ("Vaultwarden", "🔐", "Passwords", True),
    "postgres":     ("PostgreSQL", "🐘", "Database", True),
    "mariadb":      ("MariaDB", "🗄️", "Database", True),
    "mysql":        ("MySQL", "🗄️", "Database", True),
    "redis":        ("Redis", "⚡", "Cache", True),
    "mongo":        ("MongoDB", "🍃", "Database", True),
    "wireguard":    ("WireGuard", "🕳️", "VPN", True),
    "openssh":      ("OpenSSH", "🔒", "Remote Access", True),
    "grafana":      ("Grafana", "📊", "Monitoring", False),
    "prometheus":   ("Prometheus", "🔥", "Monitoring", False),
    "uptime-kuma":  ("Uptime Kuma", "📈", "Monitoring", False),
    "dozzle":       ("Dozzle", "📜", "Monitoring", False),
    "portainer":    ("Portainer", "🐳", "Management", False),
    "jellyfin":     ("Jellyfin", "🎬", "Media", False),
    "navidrome":    ("Navidrome", "🎵", "Media", False),
    "plex":         ("Plex", "🎬", "Media", False),
    "nextcloud":    ("Nextcloud", "☁️", "Files & Sync", True),
    "gitea":        ("Gitea", "🍵", "Development", False),
    "code-server":  ("code-server", "💻", "Development", False),
    "n8n":          ("n8n", "🔗", "Automation", False),
    "home-assistant": ("Home Assistant", "🏠", "Smart Home", False),
    "adguard":      ("AdGuard Home", "🛡️", "DNS / Adblock", True),
    "pihole":       ("Pi-hole", "🛡️", "DNS / Adblock", True),
    "open-webui":   ("Open WebUI", "🤖", "AI", False),
    "ollama":       ("Ollama", "🤖", "AI", False),
    "synapse":      ("Matrix Synapse", "💬", "Communication", True),
    "mautrix":      ("Matrix Bridge", "💬", "Communication", False),
    "element":      ("Element", "💬", "Communication", False),
    "immich":       ("Immich", "📸", "Photos", False),
    "paperless":    ("Paperless-ngx", "📄", "Documents", False),
    "syncthing":    ("Syncthing", "🔄", "Files & Sync", False),
    "minecraft":    ("Minecraft Server", "⛏️", "Games", False),
    "watchtower":   ("Watchtower", "🗼", "Management", False),
    "alpine":       ("Alpine Linux", "🏔️", "Base Image", False),
    "debian":       ("Debian", "🌀", "Base Image", False),
    "ubuntu":       ("Ubuntu", "🟠", "Base Image", False),
    "python":       ("Python", "🐍", "Base Image", False),
    "node":         ("Node.js", "🟢", "Base Image", False),
}


def service_meta(image: str) -> dict:
    """Friendly name/icon/category for an image reference."""
    base = image.split("@")[0].rsplit(":", 1)[0].lower()
    for key, (label, icon, category, security) in SERVICE_META.items():
        if key in base:
            return {"label": label, "icon": icon, "category": category, "security": security}
    name = base.rsplit("/", 1)[-1]
    return {"label": name.replace("-", " ").replace("_", " ").title(),
            "icon": "📦", "category": "Service", "security": False}

ACCEPT = ("application/vnd.docker.distribution.manifest.list.v2+json, "
          "application/vnd.oci.image.index.v1+json, "
          "application/vnd.docker.distribution.manifest.v2+json, "
          "application/vnd.oci.image.manifest.v1+json")


def parse_image_ref(ref: str) -> tuple[str, str, str]:
    """'grafana/grafana:10.2' -> (registry, repository, tag)."""
    ref = ref.split("@")[0]
    tag = "latest"
    if ":" in ref.rsplit("/", 1)[-1]:
        ref, tag = ref.rsplit(":", 1)
    parts = ref.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repo = parts[0], "/".join(parts[1:])
    else:
        registry, repo = "registry-1.docker.io", ref if "/" in ref else f"library/{ref}"
    return registry, repo, tag


async def remote_digest(client: httpx.AsyncClient, registry: str, repo: str, tag: str) -> str | None:
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    r = await client.head(url, headers=headers)
    if r.status_code == 401:
        # Token dance (works for docker hub, ghcr, lscr, quay …)
        www = r.headers.get("www-authenticate", "")
        m = dict(re.findall(r'(\w+)="([^"]*)"', www))
        if "realm" not in m:
            return None
        tr = await client.get(m["realm"], params={k: v for k, v in
                                                  [("service", m.get("service", "")),
                                                   ("scope", m.get("scope", f"repository:{repo}:pull"))] if v})
        if tr.status_code != 200:
            return None
        headers["Authorization"] = f"Bearer {tr.json().get('token', tr.json().get('access_token', ''))}"
        r = await client.head(url, headers=headers)
    if r.status_code != 200:
        return None
    return r.headers.get("docker-content-digest")


def _classify_priority(meta: dict, exposed_publicly: bool, age_days: int | None) -> str:
    if meta["security"]:
        return "high"
    if exposed_publicly or (age_days or 0) > 180:
        return "medium"
    return "low"


def _links_for(image: str) -> dict:
    registry, repo, tag = parse_image_ref(image)
    links = {}
    if registry == "registry-1.docker.io":
        links["hub"] = (f"https://hub.docker.com/_/{repo[8:]}" if repo.startswith("library/")
                        else f"https://hub.docker.com/r/{repo}")
        if not repo.startswith("library/") and repo.count("/") == 1:
            links["changelog"] = f"https://github.com/{repo}/releases"
    elif registry == "ghcr.io":
        links["hub"] = f"https://github.com/{repo}"
        links["changelog"] = f"https://github.com/{'/'.join(repo.split('/')[:2])}/releases"
    elif registry == "lscr.io":
        links["changelog"] = f"https://github.com/linuxserver/docker-{repo.split('/')[-1]}/releases"
    return links


async def check_docker_updates(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["result"] is not None and now - _cache["time"] < CACHE_TTL:
        return _cache["result"]

    containers = await dockerapi.list_containers(all_=False)
    images: dict[str, dict] = {}
    for c in containers:
        if c["image"].startswith("sha256:"):
            continue
        slot = images.setdefault(c["image"], {"used_by": [], "public": False})
        slot["used_by"].append(c["name"])
        if any(p.get("ip") in ("", "0.0.0.0", "::") for p in c["ports"]):
            slot["public"] = True

    ignored = set(config.get_ignored_images())
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        async def check_one(image: str, info_c: dict) -> None:
            meta = service_meta(image)
            entry = {"image": image, "used_by": info_c["used_by"], "update_available": False,
                     "current_digest": "", "remote_digest": "", "error": "",
                     "ignored": image in ignored, "age_days": None,
                     "tag": parse_image_ref(image)[2], **meta, "links": _links_for(image)}
            try:
                info = await dockerapi.inspect_image(image)
                if not info:
                    entry["error"] = "image not found locally"
                    results.append(entry)
                    return
                created = info.get("Created", "")
                if created:
                    try:
                        dt = datetime.datetime.fromisoformat(created.split(".")[0] + "+00:00")
                        entry["age_days"] = max(0, int((now - dt.timestamp()) / 86400))
                    except ValueError:
                        pass
                local_digests = {d.split("@")[1] for d in info.get("RepoDigests", []) if "@" in d}
                if not local_digests:
                    entry["error"] = "locally built image"
                    entry["local_build"] = True
                    results.append(entry)
                    return
                registry, repo, tag = parse_image_ref(image)
                remote = await remote_digest(client, registry, repo, tag)
                if remote is None:
                    entry["error"] = "registry check failed"
                else:
                    entry["current_digest"] = sorted(local_digests)[0][:19]
                    entry["remote_digest"] = remote[:19]
                    entry["update_available"] = remote not in local_digests
            except Exception as e:
                entry["error"] = str(e)[:200]
            entry["priority"] = _classify_priority(meta, info_c["public"], entry["age_days"])
            results.append(entry)

        await asyncio.gather(*(check_one(img, inf) for img, inf in images.items()))

    prio_rank = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: (x["ignored"], not x["update_available"],
                                prio_rank.get(x.get("priority", "low"), 2),
                                -(x["age_days"] or 0), x["image"]))
    _cache.update(time=now, result=results)
    return results


async def check_apt_updates() -> dict:
    """Host package updates — only meaningful when running natively on the host."""
    try:
        proc = await asyncio.create_subprocess_shell(
            "apt list --upgradable 2>/dev/null | tail -n +2",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        pkgs = []
        for line in out.decode().splitlines():
            m = re.match(r"([^/]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from:\s*([^\]]+)\]", line)
            if m:
                pkgs.append({"package": m.group(1), "new": m.group(2), "current": m.group(3)})
        return {"available": True, "packages": pkgs}
    except Exception:
        return {"available": False, "packages": []}


def start_update_job(image: str, recreate: bool = True) -> jobs.Job:
    """Pull the newer image as a background job with live progress, then
    (optionally) recreate all containers running on it."""
    meta = service_meta(image)

    async def work(job: jobs.Job) -> None:
        job.log(f"⬇ Pulling {image} …")
        await dockerapi.pull_image_stream(image, job.log)
        job.log("✓ Pull complete")
        _cache["time"] = 0  # invalidate check cache
        if not recreate:
            job.finish(True, "Image updated. Containers keep running on the old "
                             "image until they are recreated.")
            return
        containers = [c for c in await dockerapi.list_containers(all_=False)
                      if c["image"] == image]
        if not containers:
            job.finish(True, "No running containers use this image — nothing to recreate.")
            return
        for c in containers:
            job.log(f"♻ Recreating {c['name']} with the new image …")
            new_id = await dockerapi.recreate_container(c["id"], job.log)
            job.log(f"✓ {c['name']} is running again ({new_id})")
        job.finish(True, f"✓ Update finished: {meta['label']} "
                         f"({len(containers)} container{'s' if len(containers) > 1 else ''} recreated)")

    return jobs.start(f"Update {meta['label']} ({image})", "update", work)


EXPLAIN_SYSTEM = ("You explain software updates to self-hosting users who are not sysadmins. "
                  "Be concise (max ~150 words), friendly, and concrete: what is this software, "
                  "what does an update typically bring, is there any risk, and what should the "
                  "user do. Answer in the user's language if specified.")


async def explain_update(subject: str, kind: str, lang: str = "") -> str:
    changelog = ""
    if kind == "docker":
        _, repo, tag = parse_image_ref(subject)
        changelog = await _try_github_release_notes(repo)
    prompt = (f"A new update is available for {'docker image' if kind == 'docker' else 'package'} "
              f"'{subject}'.\n")
    if changelog:
        prompt += f"\nRecent release notes:\n{changelog[:4000]}\n"
    if lang:
        prompt += f"\nAnswer in language: {lang}"
    prompt += "\nExplain this update to the user."
    return await ai.one_shot(prompt, EXPLAIN_SYSTEM)


async def _try_github_release_notes(repo: str) -> str:
    """Many images map 1:1 to a github repo (grafana/grafana …) — best effort."""
    if repo.startswith("library/") or repo.count("/") != 1:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                d = r.json()
                return f"{d.get('name', d.get('tag_name', ''))}\n{d.get('body', '')}"
    except Exception:
        pass
    return ""
