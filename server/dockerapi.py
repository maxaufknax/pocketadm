"""Async Docker Engine API client over the unix socket (no SDK dependency)."""
import json
import os
import re
import time
from typing import Any

import httpx

from . import config, demodata

SOCKET = "/var/run/docker.sock"
_client: httpx.AsyncClient | None = None


def demo() -> bool:
    """Serve canned data: demo mode without a real Docker socket."""
    return config.DEMO and not os.path.exists(SOCKET)


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=SOCKET),
            base_url="http://docker", timeout=60,
        )
    return _client


async def available() -> bool:
    if demo():
        return True
    try:
        r = await client().get("/_ping")
        return r.status_code == 200
    except Exception:
        return False


async def list_containers(all_: bool = True) -> list[dict]:
    if demo():
        return demodata.list_containers(all_)
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
            # Where this stack is defined on the *host*. The UI uses it to offer
            # a real "do it yourself in the terminal" command for updates. Note
            # it is a host path: compose has to run in a host shell, not in our
            # container, or relative bind mounts would resolve under /host.
            "compose_dir": labels.get("com.docker.compose.project.working_dir", ""),
            "created": c.get("Created", 0),
            # PocketADM's own command runners and terminal shells (hostrun,
            # terminal.py): plumbing, not a service of the server
            "helper": bool(labels.get("pocketadm.helper"))
                      or str(c.get("Command", "")).startswith("chroot /host"),
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
    if demo():
        return demodata.inspect_container(cid)
    r = await client().get(f"/containers/{cid}/json")
    r.raise_for_status()
    return r.json()


_SECRET_KEY = re.compile(r"(PASS|SECRET|TOKEN|KEY|PRIVATE|CREDENTIAL|AUTH|SALT|COOKIE|"
                         r"SESSION|DSN|DATABASE_URL|CONN|SIGNING|ENCRYPT)", re.I)
_URL_CREDENTIALS = re.compile(r"://[^/@\s]+:[^/@\s]+@")
MASK = "••••••••"


def mask_env(env: list[str]) -> list[dict]:
    """Environment variables for display: names always, values only where they
    cannot be a credential. A phone screen gets shared, screenshotted and read
    over a shoulder; a database password does not need to be on it."""
    out = []
    for item in env or []:
        key, _, value = item.partition("=")
        secret = bool(_SECRET_KEY.search(key)) or bool(_URL_CREDENTIALS.search(value))
        out.append({"key": key, "value": MASK if secret and value else value[:500],
                    "secret": secret})
    return sorted(out, key=lambda e: e["key"])


def _health_log(state: dict) -> list[dict]:
    log = ((state.get("Health") or {}).get("Log") or [])[-5:]
    return [{"start": (h.get("Start") or "")[:19], "end": (h.get("End") or "")[:19],
             "exit_code": h.get("ExitCode"),
             "output": (h.get("Output") or "").strip()[-400:]} for h in reversed(log)]


async def container_detail(cid: str) -> dict:
    """Human-friendly summary of a container's configuration."""
    if demo():
        return demodata.container_detail(cid)
    d = await inspect_container(cid)
    cfg, host = d.get("Config", {}), d.get("HostConfig", {})
    state = d.get("State", {})
    mounts = [{"source": m.get("Source", ""), "dest": m.get("Destination", ""),
               "rw": m.get("RW", True), "type": m.get("Type", ""),
               "name": m.get("Name", "")}
              for m in d.get("Mounts", [])]
    net_settings = d.get("NetworkSettings", {}) or {}
    nets = net_settings.get("Networks") or {}
    networks = list(nets.keys())
    network_details = [{"name": name, "ip": (n or {}).get("IPAddress", ""),
                        "gateway": (n or {}).get("Gateway", ""),
                        "aliases": [a for a in ((n or {}).get("Aliases") or [])
                                    if a != d.get("Id", "")[:12]][:6]}
                       for name, n in nets.items()]
    ports = []
    for spec, bindings in (net_settings.get("Ports") or {}).items():
        private, _, proto = spec.partition("/")
        for b in bindings or [{}]:
            ports.append({"private": int(private) if private.isdigit() else 0,
                          "proto": proto or "tcp",
                          "public": int(b["HostPort"]) if (b or {}).get("HostPort", "").isdigit() else None,
                          "ip": (b or {}).get("HostIp", "")})
    labels = cfg.get("Labels") or {}
    restart = host.get("RestartPolicy") or {}
    return {
        "id": d["Id"][:12],
        "name": d.get("Name", "").lstrip("/"),
        "image": cfg.get("Image", ""),
        "image_id": (d.get("Image") or "").removeprefix("sha256:")[:12],
        "created": d.get("Created", ""),
        "started_at": state.get("StartedAt", ""),
        "finished_at": state.get("FinishedAt", ""),
        "state": state.get("Status", ""),
        "exit_code": state.get("ExitCode"),
        "oom_killed": bool(state.get("OOMKilled")),
        "error": state.get("Error", ""),
        "health": (state.get("Health") or {}).get("Status", ""),
        "health_failing_streak": (state.get("Health") or {}).get("FailingStreak", 0),
        "health_log": _health_log(state),
        "restart_count": d.get("RestartCount", 0),
        "restart_policy": restart.get("Name", ""),
        "restart_max": restart.get("MaximumRetryCount", 0),
        "privileged": host.get("Privileged", False),
        "env_count": len(cfg.get("Env") or []),
        "env": mask_env(cfg.get("Env") or []),
        "cmd": " ".join(cfg.get("Cmd") or [])[:200],
        "entrypoint": " ".join(cfg.get("Entrypoint") or [])[:200],
        "working_dir": cfg.get("WorkingDir", ""),
        "user": cfg.get("User", ""),
        "hostname": cfg.get("Hostname", ""),
        "mounts": mounts,
        "networks": networks,
        "network_details": network_details,
        "network_mode": host.get("NetworkMode", ""),
        "ports": ports,
        "resources": {"memory_limit": host.get("Memory") or 0,
                      "cpus": round((host.get("NanoCpus") or 0) / 1e9, 2),
                      "cpu_shares": host.get("CpuShares") or 0,
                      "pids_limit": host.get("PidsLimit") or 0},
        "log_driver": (host.get("LogConfig") or {}).get("Type", ""),
        "compose_dir": labels.get("com.docker.compose.project.working_dir", ""),
        "compose_files": labels.get("com.docker.compose.project.config_files", ""),
        "labels": {k: v for k, v in labels.items()
                   if k.startswith(("com.docker.compose", "org.opencontainers.image"))},
    }


RESTART_POLICIES = ("no", "always", "unless-stopped", "on-failure")


async def set_restart_policy(cid: str, policy: str) -> None:
    """Change what Docker does when the container stops — live, no recreate."""
    if policy not in RESTART_POLICIES:
        raise ValueError("unknown restart policy")
    body = {"RestartPolicy": {"Name": policy}}
    if policy == "on-failure":
        body["RestartPolicy"]["MaximumRetryCount"] = 5
    r = await client().post(f"/containers/{cid}/update", json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"update failed: {r.text[:200]}")


async def container_top(cid: str) -> dict:
    """The processes running inside a container (`docker top`)."""
    if demo():
        return demodata.container_top(cid)
    r = await client().get(f"/containers/{cid}/top", params={"ps_args": "-eo pid,user,pcpu,pmem,etime,args"})
    if r.status_code == 409:
        return {"titles": [], "processes": [], "note": "The container is not running."}
    r.raise_for_status()
    data = r.json()
    return {"titles": data.get("Titles") or [], "processes": (data.get("Processes") or [])[:200]}


_EVENT_WORDS = {
    "start": "started", "die": "exited", "stop": "stopped", "kill": "was killed",
    "restart": "restarted", "oom": "ran out of memory", "destroy": "was removed",
    "create": "was created", "pause": "was paused", "unpause": "was resumed",
    "rename": "was renamed", "update": "was reconfigured",
}


def describe_event(e: dict) -> dict | None:
    """One docker event as a line a person reads ("exited (code 137)")."""
    action = (e.get("action") or "").split(":")[0].strip()
    if action == "health_status":
        status = (e.get("action") or "").split(":")[-1].strip()
        return {"t": e.get("t", 0), "action": "health", "status": status,
                "summary": f"health turned {status}",
                "severity": "warn" if status == "unhealthy" else "ok"}
    words = _EVENT_WORDS.get(action)
    if not words:
        return None
    summary = words
    code = e.get("exit_code")
    if action == "die" and code not in (None, "", "0"):
        summary += f" (exit code {code})"
    severity = "crit" if action == "oom" else \
        "warn" if action in ("die", "kill") and code not in (None, "", "0") else "ok"
    return {"t": e.get("t", 0), "action": action, "summary": summary, "severity": severity}


async def container_events(cid: str, hours: int = 24) -> list[dict]:
    """What happened to one container lately, newest first."""
    if demo():
        return demodata.container_events(cid)
    since = time.time() - max(1, min(hours, 24 * 14)) * 3600
    until = time.time() - 1
    r = await client().get("/events", params={
        "since": str(int(since)), "until": str(int(until)),
        "filters": json.dumps({"type": ["container"], "container": [cid]}),
    }, timeout=15)
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        attrs = (e.get("Actor") or {}).get("Attributes") or {}
        item = describe_event({"t": e.get("time", 0), "action": e.get("Action", ""),
                               "exit_code": attrs.get("exitCode")})
        if item:
            out.append(item)
    out.reverse()
    return out[:100]


async def container_action(cid: str, action: str) -> None:
    assert action in ("start", "stop", "restart", "pause", "unpause", "kill")
    r = await client().post(f"/containers/{cid}/{action}",
                            timeout=90 if action in ("stop", "restart") else 60)
    if r.status_code >= 400 and r.status_code != 304:
        raise RuntimeError(f"{action} failed: {r.text}")


async def remove_container(cid: str, force: bool = False) -> None:
    """Remove a container (its named volumes are left in place on purpose —
    data survives; anonymous volumes go with the container as usual)."""
    r = await client().delete(f"/containers/{cid}",
                              params={"force": "true" if force else "false"})
    if r.status_code >= 400:
        raise RuntimeError(f"remove failed: {r.text[:300]}")


async def events(since: float, until: float) -> list[dict]:
    """Docker engine events in a time window (bounded → the stream terminates).
    Used to explain metric anomalies: what started/died/was pulled around then.
    `until` must lie in the past: with a future bound the engine keeps the
    stream open until that wall-clock time (= a hanging request)."""
    if demo():
        return []
    until = min(until, time.time() - 1)
    if until <= since:
        return []
    r = await client().get("/events", params={
        "since": str(int(since)), "until": str(int(until)),
        "filters": json.dumps({"type": ["container", "image"]}),
    }, timeout=15)
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        action = (e.get("Action") or "").split(":")[0]
        if action not in ("start", "die", "stop", "kill", "restart", "oom",
                          "destroy", "create", "pull", "health_status"):
            continue
        attrs = (e.get("Actor") or {}).get("Attributes") or {}
        name = attrs.get("name") or (e.get("Actor") or {}).get("ID", "")[:12]
        out.append({
            "t": e.get("time", 0),
            "type": e.get("Type", ""),
            "action": e.get("Action", ""),
            "name": name,
            "exit_code": attrs.get("exitCode"),
        })
    return out


def _demux(raw: bytes) -> tuple[list[bytes], bytes]:
    """Split Docker's multiplexed log stream (8-byte headers) into payloads;
    returns the frames and whatever incomplete tail is left over."""
    out, i = [], 0
    while i + 8 <= len(raw):
        if raw[i] not in (0, 1, 2) or raw[i + 1:i + 4] != b"\x00\x00\x00":
            return [], raw          # not multiplexed (a tty container)
        size = int.from_bytes(raw[i + 4:i + 8], "big")
        if i + 8 + size > len(raw):
            break
        out.append(raw[i + 8:i + 8 + size])
        i += 8 + size
    return out, raw[i:]


async def container_logs(cid: str, tail: int = 200, since: int = 0,
                         timestamps: bool = False) -> str:
    if demo():
        return demodata.container_logs(cid, tail)
    params = {"stdout": "true", "stderr": "true", "tail": str(tail),
              "timestamps": "true" if timestamps else "false"}
    if since:
        params["since"] = str(int(time.time() - since))
    r = await client().get(f"/containers/{cid}/logs", params=params)
    r.raise_for_status()
    frames, rest = _demux(r.content)
    if not frames:  # tty containers return a plain stream
        return r.content.decode("utf-8", "replace")
    return b"".join(frames).decode("utf-8", "replace")


async def follow_logs(cid: str, tail: int = 50, max_seconds: int = 900):
    """New log lines as they are written — the live tail of the detail screen.
    Ends after `max_seconds`; the app simply reconnects."""
    if demo():
        async for line in demodata.follow_logs(cid):
            yield line
        return
    params = {"stdout": "true", "stderr": "true", "tail": str(tail), "follow": "true"}
    deadline = time.monotonic() + max_seconds
    buf, tty = b"", None
    async with client().stream("GET", f"/containers/{cid}/logs", params=params,
                               timeout=httpx.Timeout(None, connect=10)) as resp:
        if resp.status_code >= 400:
            yield f"[logs unavailable: {resp.status_code}]\n"
            return
        async for chunk in resp.aiter_bytes():
            buf += chunk
            if tty is None and len(buf) >= 8:
                tty = not (buf[0] in (0, 1, 2) and buf[1:4] == b"\x00\x00\x00")
            if tty:
                text, buf = buf.decode("utf-8", "replace"), b""
            else:
                frames, buf = _demux(buf)
                text = b"".join(frames).decode("utf-8", "replace")
            if text:
                yield text
            if time.monotonic() > deadline:
                return


_last_stats: dict[str, dict] = {}


def stats_summary(s: dict, previous: dict | None = None, elapsed: float = 0) -> dict:
    """CPU, memory, network and disk numbers from one Docker stats sample.

    CPU needs two readings. A full sample carries its own predecessor
    (precpu_stats); a one-shot sample does not, so the previous one-shot
    sample of the same container stands in for it."""
    cpu = None
    try:
        cur = s["cpu_stats"]
        pre = s.get("precpu_stats") or {}
        if not (pre.get("system_cpu_usage") and pre.get("cpu_usage", {}).get("total_usage")) \
                and previous:
            pre = previous.get("cpu_stats") or {}
        cpu_delta = cur["cpu_usage"]["total_usage"] - (pre.get("cpu_usage") or {}).get("total_usage", 0)
        sys_delta = cur.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        if sys_delta > 0 and pre.get("system_cpu_usage"):
            cpus = cur.get("online_cpus") or len(cur["cpu_usage"].get("percpu_usage") or []) or 1
            cpu = round(cpu_delta / sys_delta * cpus * 100, 1)
    except (KeyError, TypeError):
        cpu = None
    mem = s.get("memory_stats") or {}
    usage = mem.get("usage", 0) or 0
    # like `docker stats`: page cache that can be dropped is not "used"
    inactive = (mem.get("stats") or {}).get("inactive_file") \
        or (mem.get("stats") or {}).get("total_inactive_file") or 0
    used = max(0, usage - inactive)
    rx = sum((n or {}).get("rx_bytes", 0) for n in (s.get("networks") or {}).values())
    tx = sum((n or {}).get("tx_bytes", 0) for n in (s.get("networks") or {}).values())
    blk_read = blk_write = 0
    for entry in ((s.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []):
        op = (entry.get("op") or "").lower()
        if op == "read":
            blk_read += entry.get("value", 0)
        elif op == "write":
            blk_write += entry.get("value", 0)
    out = {"cpu_percent": cpu if cpu is not None else 0.0, "cpu_known": cpu is not None,
           "mem_usage": used, "mem_limit": mem.get("limit", 0) or 0,
           "net_rx": rx, "net_tx": tx, "blk_read": blk_read, "blk_write": blk_write,
           "pids": (s.get("pids_stats") or {}).get("current", 0) or 0,
           "net_rx_rate": None, "net_tx_rate": None}
    if previous and elapsed > 0:
        prev = stats_summary(previous)
        out["net_rx_rate"] = max(0.0, (rx - prev["net_rx"]) / elapsed)
        out["net_tx_rate"] = max(0.0, (tx - prev["net_tx"]) / elapsed)
    return out


async def container_stats(cid: str, live: bool = False) -> dict:
    """One reading of a container's resource use. `live` is the cheap form
    for a screen that polls: Docker answers at once instead of sampling for a
    second, and CPU and network rates come from the previous poll."""
    if demo():
        return demodata.container_stats(cid)
    params = {"stream": "false", "one-shot": "true" if live else "false"}
    r = await client().get(f"/containers/{cid}/stats", params=params)
    r.raise_for_status()
    sample = r.json()
    now = time.monotonic()
    prev = _last_stats.get(cid) if live else None
    elapsed = now - prev["_at"] if prev else 0
    result = stats_summary(sample, prev["sample"] if prev else None, elapsed)
    if live:
        _last_stats[cid] = {"sample": sample, "_at": now}
        if len(_last_stats) > 64:
            _last_stats.pop(next(iter(_last_stats)))
        if not result["cpu_known"] and not prev:
            # first poll of this container: take one full sample for a real number
            return await container_stats(cid, live=False) | {
                k: v for k, v in result.items() if k.startswith("net_") and v is not None}
    return result


async def inspect_image(name: str) -> dict | None:
    if demo():
        return demodata.inspect_image(name)
    r = await client().get(f"/images/{name}/json")
    return r.json() if r.status_code == 200 else None


async def tag_image(image_id: str, repo: str, tag: str) -> None:
    r = await client().post(f"/images/{image_id}/tag", params={"repo": repo, "tag": tag})
    if r.status_code >= 400:
        raise RuntimeError(f"tag failed: {r.text[:200]}")


async def remove_image(ref: str) -> bool:
    """Untag/remove an image reference; best-effort (in-use images stay)."""
    r = await client().delete(f"/images/{ref}")
    return r.status_code < 400


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


async def recreate_container(cid: str, on_progress, image: str | None = None) -> str:
    """Recreate a container with its current config (after an image pull).

    Watchtower-style: stop + rename old, create + start new, roll back on error.
    `image` overrides the image reference (used for snapshot rollbacks).
    """
    d = await inspect_container(cid)
    name = d.get("Name", "").lstrip("/")
    cfg = d.get("Config", {})
    body = {
        **{k: cfg.get(k) for k in ("Hostname", "User", "Env", "Cmd", "Entrypoint",
                                   "WorkingDir", "Labels", "ExposedPorts", "Volumes",
                                   "Healthcheck", "Tty", "OpenStdin") if cfg.get(k) is not None},
        "Image": image or cfg.get("Image", ""),
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


async def system_df() -> dict:
    """Disk usage of images/containers/volumes (docker system df)."""
    if demo():
        return {}
    r = await client().get("/system/df")
    r.raise_for_status()
    return r.json()


async def engine_info() -> dict:
    if demo():
        return demodata.engine_info()
    r = await client().get("/info")
    r.raise_for_status()
    d = r.json()
    return {"containers": d.get("Containers"), "running": d.get("ContainersRunning"),
            "images": d.get("Images"), "version": d.get("ServerVersion"), "os": d.get("OperatingSystem")}
