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
        out.append({
            "id": c["Id"][:12],
            "name": (c.get("Names") or ["?"])[0].lstrip("/"),
            "image": c.get("Image", ""),
            "state": c.get("State", ""),
            "status": c.get("Status", ""),
            "ports": [
                {"private": p.get("PrivatePort"), "public": p.get("PublicPort"), "type": p.get("Type")}
                for p in c.get("Ports", []) if p.get("PublicPort")
            ],
            "compose_project": c.get("Labels", {}).get("com.docker.compose.project", ""),
            "created": c.get("Created", 0),
        })
    return sorted(out, key=lambda x: (x["state"] != "running", x["name"]))


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


async def engine_info() -> dict:
    r = await client().get("/info")
    r.raise_for_status()
    d = r.json()
    return {"containers": d.get("Containers"), "running": d.get("ContainersRunning"),
            "images": d.get("Images"), "version": d.get("ServerVersion"), "os": d.get("OperatingSystem")}
