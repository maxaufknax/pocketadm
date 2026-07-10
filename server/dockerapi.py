"""Async Docker Engine API client over the unix socket (no SDK dependency)."""
import json
from typing import Any

import httpx

SOCKET = "/var/run/docker.sock"
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=SOCKET),
            base_url="http://docker", timeout=60,
        )
    return _client


async def available() -> bool:
    try:
        r = await client().get("/_ping")
        return r.status_code == 200
    except Exception:
        return False


async def list_containers(all_: bool = True) -> list[dict]:
    r = await client().get("/containers/json", params={"all": "true" if all_ else "false"})
    r.raise_for_status()
    out = []
    for c in r.json():
        labels = c.get("Labels", {})
        ports = []
        seen = set()
        for p in c.get("Ports", []):
            if p.get("PublicPort") and p["PublicPort"] not in seen:
                seen.add(p["PublicPort"])
                ports.append({"private": p.get("PrivatePort"), "public": p.get("PublicPort"),
                              "type": p.get("Type"), "ip": p.get("IP", "")})
        out.append({
            "id": c["Id"][:12],
            "name": (c.get("Names") or ["?"])[0].lstrip("/"),
            "image": c.get("Image", ""),
            "state": c.get("State", ""),
            "status": c.get("Status", ""),
            "health": _health_from_status(c.get("Status", "")),
            "ports": sorted(ports, key=lambda p: p["public"]),
            "compose_project": labels.get("com.docker.compose.project", ""),
            "compose_service": labels.get("com.docker.compose.service", ""),
            "created": c.get("Created", 0),
            "mounts_docker_sock": any(
                m.get("Source") == "/var/run/docker.sock" for m in c.get("Mounts", [])),
        })
    return sorted(out, key=lambda x: (x["state"] != "running", x["name"]))


def _health_from_status(status: str) -> str:
    if "(healthy" in status:
        return "healthy"
    if "(unhealthy" in status:
        return "unhealthy"
    if "(health" in status:
        return "starting"
    return ""


async def inspect_container(cid: str) -> dict:
    r = await client().get(f"/containers/{cid}/json")
    r.raise_for_status()
    return r.json()


async def container_detail(cid: str) -> dict:
    """Human-friendly summary of a container's configuration."""
    d = await inspect_container(cid)
    cfg, host = d.get("Config", {}), d.get("HostConfig", {})
    state = d.get("State", {})
    mounts = [{"source": m.get("Source", ""), "dest": m.get("Destination", ""),
               "rw": m.get("RW", True), "type": m.get("Type", "")}
              for m in d.get("Mounts", [])]
    networks = list((d.get("NetworkSettings", {}).get("Networks") or {}).keys())
    return {
        "id": d["Id"][:12],
        "name": d.get("Name", "").lstrip("/"),
        "image": cfg.get("Image", ""),
        "created": d.get("Created", ""),
        "started_at": state.get("StartedAt", ""),
        "state": state.get("Status", ""),
        "health": (state.get("Health") or {}).get("Status", ""),
        "restart_count": d.get("RestartCount", 0),
        "restart_policy": (host.get("RestartPolicy") or {}).get("Name", ""),
        "privileged": host.get("Privileged", False),
        "env_count": len(cfg.get("Env") or []),
        "cmd": " ".join(cfg.get("Cmd") or [])[:200],
        "mounts": mounts,
        "networks": networks,
        "labels": {k: v for k, v in (cfg.get("Labels") or {}).items()
                   if k.startswith("com.docker.compose")},
    }


async def container_action(cid: str, action: str) -> None:
    assert action in ("start", "stop", "restart", "pause", "unpause", "kill")
    r = await client().post(f"/containers/{cid}/{action}")
    if r.status_code >= 400 and r.status_code != 304:
        raise RuntimeError(f"{action} failed: {r.text}")


async def container_logs(cid: str, tail: int = 200) -> str:
    r = await client().get(f"/containers/{cid}/logs",
                           params={"stdout": "true", "stderr": "true", "tail": str(tail)})
    r.raise_for_status()
    # Demultiplex the Docker stream format (8-byte header frames)
    raw, out, i = r.content, [], 0
    while i + 8 <= len(raw):
        size = int.from_bytes(raw[i + 4:i + 8], "big")
        out.append(raw[i + 8:i + 8 + size])
        i += 8 + size
    if not out:  # tty containers return a plain stream
        return raw.decode("utf-8", "replace")
    return b"".join(out).decode("utf-8", "replace")


async def container_stats(cid: str) -> dict:
    r = await client().get(f"/containers/{cid}/stats", params={"stream": "false", "one-shot": "false"})
    r.raise_for_status()
    s = r.json()
    cpu = 0.0
    try:
        cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = s["cpu_stats"]["system_cpu_usage"] - s["precpu_stats"]["system_cpu_usage"]
        if sys_delta > 0:
            cpu = round(cpu_delta / sys_delta * s["cpu_stats"].get("online_cpus", 1) * 100, 1)
    except KeyError:
        pass
    mem = s.get("memory_stats", {})
    return {"cpu_percent": cpu, "mem_usage": mem.get("usage", 0), "mem_limit": mem.get("limit", 0)}


async def inspect_image(name: str) -> dict | None:
    r = await client().get(f"/images/{name}/json")
    return r.json() if r.status_code == 200 else None


async def list_images() -> list[dict[str, Any]]:
    r = await client().get("/images/json")
    r.raise_for_status()
    return r.json()


async def pull_image_stream(image: str, on_progress) -> None:
    """Pull via engine API, reporting aggregated layer progress via callback."""
    ref = image if ":" in image.rsplit("/", 1)[-1] else image + ":latest"
    from_image, tag = ref.rsplit(":", 1)
    layers: dict[str, str] = {}
    last_emit = 0.0
    import time as _time

    async with client().stream("POST", "/images/create",
                               params={"fromImage": from_image, "tag": tag},
                               timeout=1800) as resp:
        if resp.status_code >= 400:
            raise RuntimeError((await resp.aread()).decode()[:300])
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                status, lid = ev.get("status", ""), ev.get("id", "")
                if lid:
                    detail = ev.get("progressDetail") or {}
                    if detail.get("total"):
                        pct = int(detail.get("current", 0) * 100 / detail["total"])
                        layers[lid] = f"{status} {pct}%"
                    else:
                        layers[lid] = status
                    now = _time.monotonic()
                    if now - last_emit > 0.7:  # throttle progress updates
                        last_emit = now
                        done = sum(1 for s in layers.values()
                                   if s in ("Pull complete", "Already exists"))
                        active = [f"{i[:6]} {s}" for i, s in layers.items()
                                  if s not in ("Pull complete", "Already exists", "Waiting")][:3]
                        on_progress(f"\rLayers {done}/{len(layers)} · " + " | ".join(active))
                elif status:
                    on_progress(status)


async def recreate_container(cid: str, on_progress) -> str:
    """Recreate a container with its current config (after an image pull).

    Watchtower-style: stop + rename old, create + start new, roll back on error.
    """
    d = await inspect_container(cid)
    name = d.get("Name", "").lstrip("/")
    cfg = d.get("Config", {})
    body = {
        **{k: cfg.get(k) for k in ("Hostname", "User", "Env", "Cmd", "Entrypoint",
                                   "WorkingDir", "Labels", "ExposedPorts", "Volumes",
                                   "Healthcheck", "Tty", "OpenStdin") if cfg.get(k) is not None},
        "Image": cfg.get("Image", ""),
        "HostConfig": d.get("HostConfig", {}),
    }
    networks = (d.get("NetworkSettings", {}).get("Networks") or {})
    if networks:
        first = next(iter(networks))
        body["NetworkingConfig"] = {"EndpointsConfig": {first: {
            "Aliases": [a for a in (networks[first].get("Aliases") or []) if a != d["Id"][:12]],
        }}}

    backup = f"{name}-old-helmsman"
    on_progress(f"Stopping {name} …")
    await client().post(f"/containers/{cid}/stop", params={"t": 15})
    await client().post(f"/containers/{cid}/rename", params={"name": backup})
    try:
        on_progress(f"Creating new {name} …")
        r = await client().post("/containers/create", params={"name": name}, json=body)
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("message", r.text)[:300])
        new_id = r.json()["Id"]
        # attach remaining networks before start
        for net, netcfg in list(networks.items())[1:]:
            await client().post(f"/networks/{net}/connect", json={
                "Container": new_id,
                "EndpointConfig": {"Aliases": [a for a in (netcfg.get("Aliases") or [])
                                               if a != d["Id"][:12]]}})
        r = await client().post(f"/containers/{new_id}/start")
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("message", r.text)[:300])
        on_progress(f"Removing old container …")
        await client().delete(f"/containers/{backup}", params={"force": "true"})
        return new_id[:12]
    except Exception:
        on_progress("⚠ failed — rolling back to previous container")
        try:
            r = await client().get(f"/containers/{name}/json")
            if r.status_code == 200:
                await client().delete(f"/containers/{name}", params={"force": "true"})
        except Exception:
            pass
        await client().post(f"/containers/{backup}/rename", params={"name": name})
        await client().post(f"/containers/{name}/start")
        raise


async def engine_info() -> dict:
    r = await client().get("/info")
    r.raise_for_status()
    d = r.json()
    return {"containers": d.get("Containers"), "running": d.get("ContainersRunning"),
            "images": d.get("Images"), "version": d.get("ServerVersion"), "os": d.get("OperatingSystem")}
