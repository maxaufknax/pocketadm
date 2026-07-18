"""A live map of the server, injected into the agent's system prompt.

Real sessions showed the agent spending 20+ exploration commands (and hundreds
of thousands of tokens) rediscovering facts the app already knows: which
compose stack a container belongs to, where that stack lives on disk, what
else is running. This module renders those facts into a compact text block so
the agent starts every task already knowing the terrain, the way a human admin
does. It is generated from the Docker API + host mounts, cached briefly, and
shown verbatim to the user under Settings → AI so there is no hidden context.
"""

from __future__ import annotations

import os
import shutil
import time

from . import dockerapi, hostuser, hostrun

TTL = 600          # seconds; docker topology rarely changes faster
MAX_CHARS = 5000

_cache: dict = {"text": "", "ts": 0.0}


def cached_text() -> str:
    return _cache["text"]


async def get(force: bool = False) -> str:
    if not force and _cache["text"] and time.time() - _cache["ts"] < TTL:
        return _cache["text"]
    try:
        text = await _build()
    except Exception as e:
        text = _cache["text"] or f"(server map unavailable: {type(e).__name__})"
    _cache.update(text=text, ts=time.time())
    return text


async def _build() -> str:
    lines: list[str] = []
    ident = {}
    try:
        ident = hostuser.identity()
    except Exception:
        pass
    host_bits = []
    if ident:
        host_bits.append(f"{ident.get('hostname', '?')} — {ident.get('os', '?')}, "
                         f"kernel {ident.get('kernel', '?')}, {ident.get('arch', '')}")
    disk = _disk_line()
    if disk:
        host_bits.append(disk)
    mem = _mem_line()
    if mem:
        host_bits.append(mem)
    if host_bits:
        lines.append("Host: " + " · ".join(host_bits))

    try:
        containers = await dockerapi.list_containers(all_=True)
    except Exception:
        containers = []
    if containers:
        stacks: dict[str, dict] = {}
        loose: list[dict] = []
        for c in containers:
            proj = c.get("compose_project")
            if proj:
                s = stacks.setdefault(proj, {"dir": c.get("compose_dir", ""), "svcs": []})
                if not s["dir"] and c.get("compose_dir"):
                    s["dir"] = c["compose_dir"]
                s["svcs"].append(c)
            else:
                loose.append(c)
        lines.append(f"Docker: {sum(1 for c in containers if c['state'] == 'running')} of "
                     f"{len(containers)} containers running.")
        lines.append("Compose stacks (project @ host dir: services — work in that dir "
                     "with `docker compose`; never replace these with raw `docker run`):")
        for proj in sorted(stacks):
            s = stacks[proj]
            svcs = ", ".join(_svc(c) for c in sorted(s["svcs"], key=lambda x: x["name"]))
            where = f" @ {s['dir']}" if s["dir"] else ""
            lines.append(f"- {proj}{where}: {svcs}")
        if loose:
            lines.append("Standalone containers (no compose project): "
                         + ", ".join(_svc(c, image=True)
                                     for c in sorted(loose, key=lambda x: x["name"])))

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n… (truncated)"
    return text


def _svc(c: dict, image: bool = False) -> str:
    name = c.get("compose_service") or c["name"]
    bits = []
    if image:
        bits.append(c.get("image", "").split("@")[0][:40])
    ports = c.get("ports") or []
    if ports:
        bits.append(":" + "/".join(str(p["public"]) for p in ports[:3]))
    tag = "" if c.get("state") == "running" else "✗"
    extra = f"({', '.join(bits)})" if bits else ""
    return f"{name}{tag}{extra}"


def _disk_line() -> str:
    root = "/host" if os.path.isdir("/host") else "/"
    try:
        du = shutil.disk_usage(root)
        pct = round(du.used / du.total * 100)
        return f"disk / {du.total // (1024 ** 3)}G, {pct}% used"
    except OSError:
        return ""


def _mem_line() -> str:
    path = "/host/proc/meminfo" if os.path.exists("/host/proc/meminfo") else "/proc/meminfo"
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return f"RAM {round(kb / 1024 / 1024)}G"
    except OSError:
        pass
    return ""
