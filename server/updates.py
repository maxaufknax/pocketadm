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

from . import ai, config, dockerapi, jobs, snapshots

_cache: dict = {"time": 0, "result": None}
CACHE_TTL = 1800

# Known services: substring of image name -> friendly metadata.
# security=True marks software whose updates are typically security-relevant
# (auth, proxies, password managers, databases, anything internet-facing).
SERVICE_META = {
    "traefik":      ("Traefik", "🚦", "Reverse Proxy", True),
    "nginx-proxy-manager": ("Nginx Proxy Manager", "🚦", "Reverse Proxy", True),
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
    "headscale":    ("Headscale", "🕳️", "VPN", True),
    "openssh":      ("OpenSSH", "🔒", "Remote Access", True),
    "loki":         ("Grafana Loki", "🪵", "Monitoring", False),
    "promtail":     ("Promtail", "🪵", "Monitoring", False),
    "grafana":      ("Grafana", "📊", "Monitoring", False),
    "node-exporter": ("Node Exporter", "📊", "Monitoring", False),
    "cadvisor":     ("cAdvisor", "📊", "Monitoring", False),
    "prometheus":   ("Prometheus", "🔥", "Monitoring", False),
    "uptime-kuma":  ("Uptime Kuma", "📈", "Monitoring", False),
    "dozzle":       ("Dozzle", "📜", "Monitoring", False),
    "portainer":    ("Portainer", "🐳", "Management", False),
    "jellyfin":     ("Jellyfin", "🎬", "Media", False),
    "navidrome":    ("Navidrome", "🎵", "Media", False),
    "plex":         ("Plex", "🎬", "Media", False),
    "nextcloud":    ("Nextcloud", "☁️", "Files & Sync", True),
    "whiteboard":   ("Nextcloud Whiteboard", "🖊️", "Files & Sync", False),
    "collabora":    ("Collabora Online", "📝", "Office", False),
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
    "unbound":      ("Unbound", "🛡️", "DNS", True),
    "ntfy":         ("ntfy", "🔔", "Notifications", False),
    "searxng":      ("SearXNG", "🔎", "Search", False),
    "onlyoffice":   ("OnlyOffice", "📝", "Office", False),
    "documentserver": ("OnlyOffice", "📝", "Office", False),
    "webtop":       ("Webtop", "🖥️", "Remote Desktop", False),
    "chroma":       ("ChromaDB", "🧠", "AI", False),
    "freshrss":     ("FreshRSS", "📰", "Productivity", False),
    "stirling":     ("Stirling PDF", "🪄", "Utilities", False),
    "homepage":     ("Homepage", "🗂️", "Utilities", False),
    "memos":        ("Memos", "📝", "Productivity", False),
    "vectorim":     ("Element", "💬", "Communication", False),
    "alpine":       ("Alpine Linux", "🏔️", "Base Image", False),
    "debian":       ("Debian", "🌀", "Base Image", False),
    "ubuntu":       ("Ubuntu", "🟠", "Base Image", False),
    "python":       ("Python", "🐍", "Base Image", False),
    "node":         ("Node.js", "🟢", "Base Image", False),
}


def service_meta(image: str) -> dict:
    """Friendly name/icon/category for an image reference.

    Matches the image *basename* first so that e.g. grafana/loki is Loki and
    not Grafana; only falls back to the full path (for org-level families
    like mautrix/*) if no basename entry fits."""
    base = image.split("@")[0].rsplit(":", 1)[0].lower()
    name = base.rsplit("/", 1)[-1]
    for candidates in (name, base):
        for key, (label, icon, category, security) in SERVICE_META.items():
            if key in candidates:
                return {"label": label, "icon": icon, "category": category, "security": security}
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


def _is_image_id(ref: str) -> bool:
    """True for a bare image ID ('sha256:…' or a 12–64 hex digest). Docker's
    container list reports these instead of a name when the tag has moved to a
    newer image after a pull — such refs can't be checked against a registry,
    so we recover the real reference from the container's Config.Image instead.
    (Twin of appstore._is_image_id.)"""
    return bool(re.fullmatch(r"[0-9a-f]{12,64}", ref.removeprefix("sha256:")))


async def _registry_request(client: httpx.AsyncClient, method: str, url: str,
                            accept: str, repo: str) -> httpx.Response:
    """A registry request with the anonymous token dance (Docker Hub, GHCR,
    lscr, quay … all answer 401 with where to fetch a pull token)."""
    headers = {"Accept": accept}
    r = await client.request(method, url, headers=headers)
    if r.status_code == 401:
        www = r.headers.get("www-authenticate", "")
        m = dict(re.findall(r'(\w+)="([^"]*)"', www))
        if "realm" not in m:
            return r
        tr = await client.get(m["realm"], params={k: v for k, v in
                                                  [("service", m.get("service", "")),
                                                   ("scope", m.get("scope", f"repository:{repo}:pull"))] if v})
        if tr.status_code != 200:
            return r
        headers["Authorization"] = f"Bearer {tr.json().get('token', tr.json().get('access_token', ''))}"
        r = await client.request(method, url, headers=headers)
    return r


async def remote_digest(client: httpx.AsyncClient, registry: str, repo: str, tag: str) -> str | None:
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    r = await _registry_request(client, "HEAD", url, ACCEPT, repo)
    if r.status_code != 200:
        return None
    return r.headers.get("docker-content-digest")


_remote_info_cache: dict[str, dict] = {}


def _host_arch() -> str:
    import platform
    m = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm"}.get(m, m or "amd64")


async def remote_image_info(client: httpx.AsyncClient, image: str) -> dict:
    """The version and build date of what the registry would pull now — read
    from the image's config (OCI labels), the same place `docker inspect`
    reads them locally. Cached per image for an hour; best effort."""
    cached = _remote_info_cache.get(image)
    if cached and time.time() - cached["_t"] < 3600:
        return cached
    info = {"version": "", "created": "", "digest": "", "_t": time.time()}
    try:
        registry, repo, tag = parse_image_ref(image)
        base = f"https://{registry}/v2/{repo}"
        r = await _registry_request(client, "GET", f"{base}/manifests/{tag}", ACCEPT, repo)
        if r.status_code != 200:
            return info
        info["digest"] = (r.headers.get("docker-content-digest") or "")[:19]
        manifest = r.json()
        if manifest.get("manifests"):            # an index: pick this machine's platform
            arch = _host_arch()
            pick = next((m for m in manifest["manifests"]
                         if (m.get("platform") or {}).get("architecture") == arch
                         and (m.get("platform") or {}).get("os", "linux") == "linux"),
                        manifest["manifests"][0])
            r = await _registry_request(client, "GET", f"{base}/manifests/{pick['digest']}",
                                        ACCEPT, repo)
            if r.status_code != 200:
                return info
            manifest = r.json()
        config_digest = (manifest.get("config") or {}).get("digest")
        if not config_digest:
            return info
        r = await _registry_request(client, "GET", f"{base}/blobs/{config_digest}",
                                    "application/json", repo)
        if r.status_code != 200:
            return info
        blob = r.json()
        labels = (blob.get("config") or {}).get("Labels") or {}
        info["version"] = (labels.get("org.opencontainers.image.version")
                           or labels.get("version") or "")[:40]
        info["created"] = (blob.get("created") or "").split(".")[0]
        info["source"] = labels.get("org.opencontainers.image.source", "")
    except Exception:
        pass
    _remote_info_cache[image] = info
    return info


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
        ref, image_id = c["image"], ""
        if _is_image_id(ref):
            # Tag moved/pruned: recover the real reference (e.g.
            # ghcr.io/open-webui/open-webui:main) from the container config, and
            # keep the running image's ID so the local digest reflects what's
            # actually deployed — not wherever the tag points now.
            image_id = ref
            det = await dockerapi.inspect_container(c["id"])
            cfg_image = (det.get("Config") or {}).get("Image", "")
            if cfg_image and not _is_image_id(cfg_image):
                ref = cfg_image
            else:
                continue  # genuinely untaggable (locally built without a repo tag)
        slot = images.setdefault(ref, {"used_by": [], "public": False, "image_id": image_id})
        slot["used_by"].append(c["name"])
        if image_id and not slot["image_id"]:
            slot["image_id"] = image_id
        if any(p.get("ip") in ("", "0.0.0.0", "::") for p in c["ports"]):
            slot["public"] = True

    ignored = set(config.get_ignored_images())
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        async def check_one(image: str, info_c: dict) -> None:
            meta = service_meta(image)
            registry_, repo_, tag_ = parse_image_ref(image)
            entry = {"image": image, "used_by": info_c["used_by"], "update_available": False,
                     "current_digest": "", "remote_digest": "", "error": "",
                     "ignored": image in ignored, "age_days": None,
                     "tag": tag_, "repo": f"{registry_}/{repo_}",
                     **meta, "links": _links_for(image)}
            try:
                # Inspect the tag's *currently pulled* image (not the running
                # container's, whose tag may have moved away — see below).
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
                        entry["created"] = created.split(".")[0] + "Z"
                    except ValueError:
                        pass
                # the app's own version, when the image declares it (OCI label)
                labels = (info.get("Config") or {}).get("Labels") or {}
                entry["version"] = (labels.get("org.opencontainers.image.version")
                                    or labels.get("version") or "")[:40]
                entry["source"] = labels.get("org.opencontainers.image.source", "")[:200]
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
                    # Two independent reasons to update: the registry moved past
                    # the image we've pulled, OR a newer image is already pulled
                    # but the container still runs the old one (its tag wandered,
                    # which is exactly why image_id was a bare id) — a recreate
                    # would change it. Either way there's an update to apply.
                    registry_newer = remote not in local_digests
                    running_id = info_c.get("image_id") or ""
                    running_behind = bool(running_id and running_id != info.get("Id", ""))
                    entry["update_available"] = registry_newer or running_behind
                    if registry_newer:
                        # what the update brings: the new image's own version and date
                        latest = await remote_image_info(client, image)
                        entry["latest_version"] = latest.get("version", "")
                        entry["latest_created"] = latest.get("created", "")
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
        await _update_one(job, image, meta, recreate)
        if job.status == "running":
            job.finish(True)

    return jobs.start(f"Update {meta['label']} ({image})", "update", work)


async def _update_one(job: jobs.Job, image: str, meta: dict, recreate: bool = True) -> None:
    # Find containers linked to this image, even when the tag has moved
    # and docker ps reports a bare image ID (same logic as check_docker_updates).
    containers_all = await dockerapi.list_containers(all_=False)
    used_by: list[str] = []
    for c in containers_all:
        ref = c["image"]
        if ref == image:
            used_by.append(c["name"])
        elif _is_image_id(ref):
            det = await dockerapi.inspect_container(c["id"])
            if (det.get("Config") or {}).get("Image", "") == image:
                used_by.append(c["name"])
    try:
        snap = await snapshots.create_snapshot(image, used_by)
        if snap:
            job.log(f"📸 Snapshot saved ({snap['image_id']}) — roll back anytime "
                    f"from Health → Updates → Snapshots")
    except Exception as e:
        job.log(f"◌ Could not snapshot the current image ({type(e).__name__}) — "
                f"continuing without a rollback point")
    async with job.step(f"⬇ Pulling {image} …"):
        await dockerapi.pull_image_stream(image, job.log)
    job.log("✓ Pull complete")
    _cache["time"] = 0  # invalidate check cache
    if not recreate:
        job.log("Image updated. Containers keep running on the old "
                "image until they are recreated.")
        return
    # Re-lookup containers linked to this image (by tag, bare ID, or Config.Image).
    containers_all = await dockerapi.list_containers(all_=False)
    containers: list[dict] = []
    for c in containers_all:
        ref = c["image"]
        if ref == image:
            containers.append(c)
        elif _is_image_id(ref):
            det = await dockerapi.inspect_container(c["id"])
            if (det.get("Config") or {}).get("Image", "") == image:
                containers.append(c)
    if not containers:
        job.log("No running containers use this image — nothing to recreate.")
        return
    for c in containers:
        async with job.step(f"♻ Recreating {c['name']} with the new image …"):
            new_id = await dockerapi.recreate_container(c["id"], job.log)
        job.log(f"✓ {c['name']} started ({new_id}) — waiting for it to settle …")
        await _wait_healthy(job, new_id, c["name"])
    job.log(f"✓ Update finished: {meta['label']} "
            f"({len(containers)} container{'s' if len(containers) > 1 else ''} recreated)")


async def _wait_healthy(job: jobs.Job, cid: str, name: str, timeout: int = 45) -> None:
    """Post-recreate sanity: report state/health so the user knows it came back."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            d = await dockerapi.inspect_container(cid)
        except Exception:
            return
        state = d.get("State", {})
        status = state.get("Status", "?")
        health = (state.get("Health") or {}).get("Status", "")
        cur = f"{status}{' / ' + health if health else ''}"
        if cur != last:
            job.log(f"  {name}: {cur}")
            last = cur
        if status == "running" and health in ("", "healthy"):
            return
        if status in ("exited", "dead"):
            job.log(f"⚠ {name} exited right after the update — check its logs "
                    f"(the old container config was preserved).")
            return
        await asyncio.sleep(3)
    job.log(f"  {name}: still starting after {timeout}s — that can be normal for big apps.")


def start_update_all_job(images: list[str]) -> jobs.Job:
    """Update several images sequentially in one job."""
    async def work(job: jobs.Job) -> None:
        ok, failed = 0, []
        for i, image in enumerate(images, 1):
            meta = service_meta(image)
            job.log(f"—— [{i}/{len(images)}] {meta['label']} ——")
            try:
                await _update_one(job, image, meta, recreate=True)
                ok += 1
            except Exception as e:
                failed.append(meta["label"])
                job.log(f"✗ {meta['label']} failed: {type(e).__name__}: {e}")
        _cache["time"] = 0
        if failed:
            job.finish(False, f"Finished: {ok} updated, {len(failed)} failed "
                              f"({', '.join(failed)})")
        else:
            job.finish(True, f"✓ All {ok} updates applied")

    return jobs.start(f"Update all ({len(images)} images)", "update", work)


EXPLAIN_SYSTEM = ("You explain a pending software update to a self-hoster who is not a "
                  "sysadmin. Be concrete and short (max ~140 words), plain words, no "
                  "headings. Say: 1) in one sentence what the update changes for them, "
                  "2) the risk — low, medium or high — and why (breaking changes, migrations, "
                  "major versions, known issues in the notes), 3) what to do (update now, wait, "
                  "back up first). Use the release notes given; never invent changes. "
                  "Answer in the user's language if specified.")


async def explain_update(subject: str, kind: str, lang: str = "") -> str:
    if kind == "docker":
        details = await release_details(subject)
        newer = [r for r in details["releases"] if r["newer"]] or details["releases"][:2]
        notes = "\n\n".join(f"## {r['name'] or r['tag']} ({r['date']})\n{r['notes'][:1500]}"
                             for r in newer[:5])
        prompt = (f"Service: {details['label']} — {details['description'] or details['category']}\n"
                  f"Image: {subject}\n"
                  f"Installed: {details['local'].get('version') or details['local'].get('tag', '?')}"
                  f" (built {details['local'].get('created', '?')})\n"
                  f"New: {details['remote'].get('version') or 'same tag, newer build'}"
                  f" (built {details['remote'].get('created', '?')})\n"
                  f"Containers recreated: {', '.join(u['name'] for u in details['used_by']) or 'none'}\n"
                  f"While updating: {details['impact']}\n")
        if notes:
            prompt += f"\nRelease notes since the installed version:\n{notes[:6000]}\n"
        else:
            prompt += "\nNo release notes were found upstream.\n"
    else:
        prompt = f"A new update is available for package '{subject}'.\n"
    if lang:
        prompt += f"\nAnswer in language: {lang}"
    prompt += "\nExplain this update to the user."
    return await ai.one_shot(prompt, EXPLAIN_SYSTEM, feature="insights")


# Images whose name does not say where their source lives.
KNOWN_REPOS = {
    "jc21/nginx-proxy-manager": "NginxProxyManager/nginx-proxy-manager",
    "vaultwarden/server": "dani-garcia/vaultwarden",
    "library/nextcloud": "nextcloud/server",
    "jellyfin/jellyfin": "jellyfin/jellyfin",
    "deluan/navidrome": "navidrome/navidrome",
    "matrixdotorg/synapse": "element-hq/synapse",
    "vectorim/element-web": "element-hq/element-web",
    "pihole/pihole": "pi-hole/docker-pi-hole",
    "portainer/portainer-ce": "portainer/portainer",
    "prom/prometheus": "prometheus/prometheus",
    "prom/node-exporter": "prometheus/node_exporter",
    "prom/alertmanager": "prometheus/alertmanager",
    "cadvisor/cadvisor": "google/cadvisor",
    "grafana/grafana": "grafana/grafana",
    "grafana/loki": "grafana/loki",
    "gitea/gitea": "go-gitea/gitea",
    "onlyoffice/documentserver": "ONLYOFFICE/DocumentServer",
    "mvance/unbound": "MatthewVance/unbound-docker",
    "library/redis": "redis/redis",
    "library/nginx": "nginx/nginx",
    "library/caddy": "caddyserver/caddy",
    "library/traefik": "traefik/traefik",
    "louislam/uptime-kuma": "louislam/uptime-kuma",
    "goauthentik/server": "goauthentik/authentik",
    "home-assistant/home-assistant": "home-assistant/core",
    "homeassistant/home-assistant": "home-assistant/core",
    "ollama/ollama": "ollama/ollama",
    "open-webui/open-webui": "open-webui/open-webui",
    "n8nio/n8n": "n8n-io/n8n",
    "immich-app/immich-server": "immich-app/immich",
    "paperless-ngx/paperless-ngx": "paperless-ngx/paperless-ngx",
    "syncthing/syncthing": "syncthing/syncthing",
    "nextcloud-releases/whiteboard": "nextcloud/whiteboard",
    "brainicism/bgutil-ytdlp-pot-provider": "Brainicism/bgutil-ytdlp-pot-provider",
    "adguard/adguardhome": "AdguardTeam/AdGuardHome",
    "codercom/code-server": "coder/code-server",
    "containrrr/watchtower": "containrrr/watchtower",
    "amir20/dozzle": "amir20/dozzle",
    "binwiederhier/ntfy": "binwiederhier/ntfy",
    "searxng/searxng": "searxng/searxng",
    "stirlingtools/stirling-pdf": "Stirling-Tools/Stirling-PDF",
    "freshrss/freshrss": "FreshRSS/FreshRSS",
    "neosmemo/memos": "usememos/memos",
}

# What a service is, for the sentence at the top of the update sheet.
DESCRIPTIONS = {
    "Nginx Proxy Manager": "The reverse proxy in front of your web apps: it routes each domain to its container and manages the HTTPS certificates.",
    "Traefik": "The reverse proxy in front of your web apps, with automatic HTTPS.",
    "Caddy": "A web server and reverse proxy with automatic HTTPS.",
    "Nginx": "A web server, often serving a website or sitting in front of another app.",
    "Authentik": "Your single sign-on: one login for all your apps, with 2FA.",
    "Authelia": "Single sign-on and 2FA in front of your apps.",
    "Vaultwarden": "Your password manager (a Bitwarden-compatible server).",
    "Nextcloud": "Your own cloud: files, calendar, contacts and office documents.",
    "Nextcloud Whiteboard": "The collaborative whiteboard inside Nextcloud.",
    "PostgreSQL": "A database other apps store their data in.",
    "MariaDB": "A database other apps store their data in.",
    "MySQL": "A database other apps store their data in.",
    "Redis": "A fast in-memory cache other apps use to stay quick.",
    "Jellyfin": "Your media server for movies, shows and music.",
    "Navidrome": "Your music streaming server.",
    "Matrix Synapse": "Your Matrix chat server (Element connects to it).",
    "Element": "The web app for chatting on your Matrix server.",
    "Pi-hole": "Network-wide ad and tracker blocking, and the DNS server of your network.",
    "Unbound": "A private DNS resolver that answers lookups without a third party.",
    "Portainer": "A web UI for managing Docker.",
    "Prometheus": "Collects the metrics of your server and services.",
    "Grafana": "Dashboards for your metrics.",
    "cAdvisor": "Measures the resource use of every container for Prometheus.",
    "Node Exporter": "Reports the host's CPU, memory and disk to Prometheus.",
    "Gitea": "Your own Git hosting, like a small GitHub.",
    "OnlyOffice": "Edits Word, Excel and PowerPoint documents in the browser.",
    "Uptime Kuma": "Watches whether your sites and services are up.",
    "Home Assistant": "Your smart home hub.",
    "Immich": "Your photo and video backup, like Google Photos.",
    "Paperless-ngx": "Scans, OCRs and files your paper documents.",
    "Syncthing": "Keeps folders in sync between your devices.",
    "Ollama": "Runs AI models locally on this server.",
    "Open WebUI": "A chat interface for AI models.",
    "n8n": "Workflow automation, like Zapier on your own server.",
    "AdGuard Home": "Network-wide ad blocking and DNS.",
}

# What happens while an update recreates the container, by category.
IMPACT = {
    "Reverse Proxy": "Every site behind this proxy is unreachable for the seconds it takes to restart.",
    "Web Server": "The site it serves is unreachable for the seconds it takes to restart.",
    "Authentication": "Sign-ins pause briefly; apps that check sessions with it may ask you to sign in again.",
    "Database": "Apps that keep their data in it stop working while it restarts — best done when nobody uses them.",
    "Cache": "Apps that use it slow down or reconnect for a moment while it restarts.",
    "DNS / Adblock": "Name lookups in your network fail while it restarts — devices may briefly show no internet.",
    "DNS": "Name lookups fail while it restarts — devices may briefly show no internet.",
    "Passwords": "Your password manager is offline for a moment; apps keep their cached vault.",
    "Communication": "Chats pause for a moment and catch up when it is back.",
    "Management": "The management UI is unavailable while it restarts.",
}


def _version_tuple(text: str) -> tuple:
    """"v2.13.1" -> (2, 13, 1); "release-1.2" -> (1, 2); no digits -> ()."""
    m = re.search(r"(\d+(?:\.\d+)*)", text or "")
    if not m:
        return ()
    return tuple(int(p) for p in m.group(1).split(".")[:4])


def is_newer(tag: str, installed: str) -> bool | None:
    """Whether a release tag is newer than the installed version; None when
    either has no comparable number (a "latest" tag, a date-less name)."""
    a, b = _version_tuple(tag), _version_tuple(installed)
    if not a or not b:
        return None
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def major_jump(installed: str, latest: str) -> bool:
    a, b = _version_tuple(installed), _version_tuple(latest)
    return bool(a and b and b[0] > a[0] and a[0] < 1000)   # 2025.10 -> 2025.12 is not "major"


def _repo_from_source(url: str) -> str:
    m = re.match(r"https?://github\.com/([^/\s]+/[^/\s#?]+)", url or "")
    return m.group(1).removesuffix(".git") if m else ""


def _github_repo_for(image: str, source: str = "") -> str:
    """owner/name of the GitHub repo behind an image, best effort: the image's
    own source label first, then a list of well-known images, then its name."""
    repo = _repo_from_source(source)
    if repo:
        return repo
    registry, path, _ = parse_image_ref(image)
    for candidate in (path, "/".join(path.split("/")[-2:])):
        if candidate in KNOWN_REPOS:
            return KNOWN_REPOS[candidate]
    if registry == "ghcr.io":
        return "/".join(path.split("/")[:2])
    if registry == "lscr.io" or path.startswith("linuxserver/"):
        return f"linuxserver/docker-{path.split('/')[-1]}"
    if registry == "registry-1.docker.io" and not path.startswith("library/") \
            and path.count("/") == 1:
        return path
    return ""


def reachable_release(tag: str, image_tag: str) -> bool:
    """Whether a release can arrive through this image tag at all: "7-alpine"
    follows 7.x, "2025.10" follows 2025.10.x, "latest" follows everything."""
    track = _version_tuple(image_tag)
    if not track:
        return True
    version = _version_tuple(tag)
    return bool(version) and version[:len(track)] == track


_release_cache: dict[str, tuple[float, list]] = {}


async def github_releases(repo: str, limit: int = 12) -> list[dict]:
    """Recent releases of a GitHub repo (cached for an hour: the API allows 60
    anonymous calls an hour). Follows renames — jc21/nginx-proxy-manager
    answers with a redirect to its new home."""
    hit = _release_cache.get(repo)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases",
                                 params={"per_page": limit},
                                 headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                for rel in r.json():
                    if rel.get("draft"):
                        continue
                    out.append({
                        "tag": rel.get("tag_name", ""),
                        "name": rel.get("name") or rel.get("tag_name", ""),
                        "date": (rel.get("published_at") or "")[:10],
                        "prerelease": bool(rel.get("prerelease")),
                        "notes": (rel.get("body") or "")[:4000],
                        "url": rel.get("html_url", ""),
                    })
    except Exception:
        return hit[1] if hit else []
    _release_cache[repo] = (time.time(), out)
    return out


async def release_details(image: str) -> dict:
    """Everything worth knowing before pulling an update onto a live service:
    what the service is, the installed and the new version with their dates,
    the upstream releases in between, which containers get recreated and what
    that does to the people using it."""
    meta = service_meta(image)
    out: dict = {"image": image, "local": {}, "remote": {}, "releases": [],
                 "links": _links_for(image), **meta,
                 "description": DESCRIPTIONS.get(meta["label"], ""),
                 "impact": IMPACT.get(meta["category"],
                                      "The service is unavailable while its container is "
                                      "recreated — usually well under a minute."),
                 "used_by": [], "repo": "", "newer_count": 0, "major": False}
    try:
        info = await dockerapi.inspect_image(image)
    except Exception:
        info = None
    source = ""
    if info:
        labels = (info.get("Config") or {}).get("Labels") or {}
        source = labels.get("org.opencontainers.image.source", "")
        out["local"] = {
            "version": (labels.get("org.opencontainers.image.version")
                        or labels.get("version") or "")[:40],
            "created": (info.get("Created") or "").split(".")[0],
            "tag": parse_image_ref(image)[2],
            "digest": next((d.split("@")[1][:19] for d in info.get("RepoDigests", [])
                            if "@" in d), ""),
        }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            remote = await remote_image_info(client, image)
        out["remote"] = {k: v for k, v in remote.items() if not k.startswith("_")}
        source = source or remote.get("source", "")
    except Exception:
        pass
    try:
        for c in await dockerapi.list_containers(all_=True):
            if c["image"] == image:
                out["used_by"].append({"name": c["name"], "state": c["state"]})
    except Exception:
        pass
    repo = _github_repo_for(image, source)
    out["repo"] = repo
    if repo:
        out["links"]["changelog"] = f"https://github.com/{repo}/releases"
        out["links"]["source"] = f"https://github.com/{repo}"
        image_tag = parse_image_ref(image)[2]
        installed = out["local"].get("version") or image_tag
        latest = out["remote"].get("version", "")
        for rel in await github_releases(repo):
            newer = is_newer(rel["tag"], installed)
            # nothing newer than what the registry would pull is "coming", and a
            # pinned tag (redis:7-alpine) never brings the next major
            beyond = is_newer(rel["tag"], latest) if latest else False
            coming = bool(newer) and not beyond and reachable_release(rel["tag"], image_tag)
            out["releases"].append({**rel, "newer": coming, "notes": rel["notes"][:2500]})
        out["newer_count"] = sum(1 for r in out["releases"] if r["newer"])
        out["major"] = major_jump(installed, latest or next(
            (r["tag"] for r in out["releases"] if r["newer"] and not r["prerelease"]), ""))
    return out
