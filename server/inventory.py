"""The whole server at a glance: what runs on it, how it is reached, what runs
on a schedule — found on any server, not configured by hand.

Containers were always visible. What was missing is everything around them
that an admin keeps in their head: which domain leads to which service, the
systemd units someone wrote for backups or a game server, the timers and cron
jobs, the drives. This module discovers them from the host itself:

  * domains — from the reverse proxy that is actually running: Nginx Proxy
    Manager (its database), Caddy (its Caddyfile), Traefik (container labels),
    or nginx on the host (sites-enabled);
  * services and timers — systemd units an admin added (/etc/systemd/system),
    well-known infrastructure (ssh, docker, wireguard, fail2ban …) and anything
    that failed; with their state and next run;
  * cron jobs, drives, and the compose stacks with their directories.

The app shows it under More → Server, the assistant gets the short version in
its server map. Results are cached briefly: reading the host costs a helper
container per systemctl call.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time

from . import config, dockerapi, hostrun, records

HOST = "/host" if os.path.isdir("/host") else ""
TTL = 120

_cache: dict = {"t": 0.0, "data": None}

# infrastructure people expect to see when it is installed and enabled
_KNOWN = re.compile(
    r"^(ssh|sshd|docker|containerd|fail2ban|ufw|firewalld|nftables|wg-quick@.+|tailscaled|"
    r"cloudflared.*|nginx|apache2|httpd|caddy|traefik|haproxy|postfix|exim4|cron|crond|"
    r"unattended-upgrades|smartd|zfs-.+|mdmonitor|nfs-server|smbd|nmbd|syncthing@.+|"
    r"code-server@.+|cockpit|netdata|prometheus-node-exporter|node_exporter|"
    r"systemd-resolved|systemd-timesyncd|chrony|ntp|libvirtd|lxd|snapd|"
    r"mariadb|mysql|postgresql.*|redis-server|mongod|minecraft.*)\.(service|timer)$")

_UNIT_NAME = re.compile(r"^[A-Za-z0-9@_.:\-\\]+\.(service|timer|socket|mount|target|path)$")


def valid_unit(name: str) -> bool:
    return bool(_UNIT_NAME.match(name or "")) and ".." not in name


# ------------------------------------------------------------------ domains

def _host_path(path: str) -> str:
    return (HOST + path) if HOST and not path.startswith(HOST) else path


async def _mount_source(container: str, dest: str) -> str:
    """Where a container's directory (e.g. NPM's /data) lives on the host."""
    try:
        info = await dockerapi.inspect_container(container)
    except Exception:
        return ""
    for m in info.get("Mounts") or []:
        if m.get("Destination") == dest:
            return m.get("Source", "")
    return ""


def _npm_hosts(db_path: str) -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    out = []
    try:
        rows = con.execute(
            "SELECT domain_names, forward_scheme, forward_host, forward_port, enabled, "
            "certificate_id, is_deleted FROM proxy_host").fetchall()
        for names, scheme, fhost, fport, enabled, cert, deleted in rows:
            if deleted:
                continue
            try:
                domains = json.loads(names or "[]")
            except ValueError:
                domains = []
            for d in domains:
                out.append({"domain": d, "target": f"{scheme or 'http'}://{fhost}:{fport}",
                            "host": str(fhost or ""), "port": int(fport or 0),
                            "tls": bool(cert), "enabled": bool(enabled), "source": "Nginx Proxy Manager"})
        for names, fhost, enabled, deleted in con.execute(
                "SELECT domain_names, forward_domain_name, enabled, is_deleted FROM redirection_host"):
            if deleted:
                continue
            for d in json.loads(names or "[]"):
                out.append({"domain": d, "target": f"redirect → {fhost}", "host": "", "port": 0,
                            "tls": False, "enabled": bool(enabled), "source": "Nginx Proxy Manager",
                            "redirect": True})
    except (sqlite3.Error, ValueError, TypeError):
        pass
    finally:
        con.close()
    return out


_CADDY_SITE = re.compile(r"^\s*([^\s{#][^{]*?)\s*\{\s*$")
_CADDY_PROXY = re.compile(r"reverse_proxy\s+(\S+)")


def parse_caddyfile(text: str) -> list[dict]:
    out, sites, depth = [], [], 0
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if depth == 0:
            m = _CADDY_SITE.match(stripped)
            if m:
                sites = [s.strip().rstrip(",") for s in re.split(r"[,\s]+", m.group(1)) if s.strip()]
        if depth == 1 and sites:
            p = _CADDY_PROXY.search(stripped)
            if p:
                target = p.group(1)
                hostport = target.split("://")[-1]
                host, _, port = hostport.partition(":")
                for site in sites:
                    if site.startswith(("(", "{", ":")) or "." not in site:
                        continue
                    domain = site.split("://")[-1]
                    out.append({"domain": domain, "target": target, "host": host,
                                "port": int(port) if port.isdigit() else 0,
                                "tls": not site.startswith("http://"), "enabled": True,
                                "source": "Caddy"})
        depth += stripped.count("{") - stripped.count("}")
        depth = max(depth, 0)
        if depth == 0:
            sites = sites if "{" in stripped else []
    return out


_NGINX_SERVER_NAME = re.compile(r"server_name\s+([^;]+);")
_NGINX_PROXY = re.compile(r"proxy_pass\s+([^;]+);")


def parse_nginx(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\bserver\s*\{", text)[1:]:
        names = _NGINX_SERVER_NAME.search(block)
        proxy = _NGINX_PROXY.search(block)
        if not names:
            continue
        target = proxy.group(1).strip() if proxy else "static files"
        hostport = target.split("://")[-1].split("/")[0]
        host, _, port = hostport.partition(":")
        tls = "ssl_certificate" in block or "listen 443" in block or "443 ssl" in block
        for d in names.group(1).split():
            if d in ("_", "localhost") or "." not in d:
                continue
            out.append({"domain": d, "target": target, "host": host if proxy else "",
                        "port": int(port) if port.isdigit() else 0, "tls": tls,
                        "enabled": True, "source": "nginx"})
    return out


_TRAEFIK_RULE = re.compile(r"Host\(([^)]*)\)")


def traefik_domains(containers: list[dict], labels_by_name: dict[str, dict]) -> list[dict]:
    out = []
    for c in containers:
        labels = labels_by_name.get(c["name"]) or {}
        if labels.get("traefik.enable", "true").lower() == "false":
            continue
        for key, value in labels.items():
            if key.startswith("traefik.http.routers.") and key.endswith(".rule"):
                for hosts in _TRAEFIK_RULE.findall(value):
                    for d in re.findall(r"[`'\"]([^`'\"]+)[`'\"]", hosts):
                        router = key.split(".")[3]
                        tls = labels.get(f"traefik.http.routers.{router}.tls", "") == "true" or \
                            bool(labels.get(f"traefik.http.routers.{router}.tls.certresolver"))
                        out.append({"domain": d, "target": c["name"], "host": c["name"], "port": 0,
                                    "tls": tls, "enabled": True, "source": "Traefik",
                                    "service": c["name"]})
    return out


def _attach_services(domains: list[dict], containers: list[dict]) -> None:
    """Which container a domain leads to: by name, or by a published port."""
    names = {c["name"] for c in containers}
    by_port: dict[int, str] = {}
    for c in containers:
        for p in c.get("ports") or []:
            if p.get("public"):
                by_port.setdefault(int(p["public"]), c["name"])
    for d in domains:
        if d.get("service"):
            continue
        host = d.get("host", "")
        if host in names:
            d["service"] = host
        elif d.get("port") and (host in ("localhost", "127.0.0.1", "host.docker.internal", "")
                                or re.match(r"^(10|172|192\.168)\.", host)):
            d["service"] = by_port.get(d["port"], "")
        else:
            d["service"] = ""


async def _domains(containers: list[dict]) -> list[dict]:
    found: list[dict] = []
    labels: dict[str, dict] = {}
    for c in containers:
        image = c.get("image", "").lower()
        if "nginx-proxy-manager" in image and c["state"] == "running":
            src = await _mount_source(c["name"], "/data")
            if src:
                found += await asyncio.to_thread(_npm_hosts, _host_path(src + "/database.sqlite"))
        elif image.split("/")[-1].startswith("caddy") and c["state"] == "running":
            for dest in ("/etc/caddy/Caddyfile", "/etc/caddy"):
                src = await _mount_source(c["name"], dest)
                if src:
                    path = _host_path(src if dest.endswith("Caddyfile") else src + "/Caddyfile")
                    try:
                        found += parse_caddyfile(open(path, errors="replace").read())
                    except OSError:
                        pass
                    break
    # Traefik reads labels off the containers
    if any("traefik" in c.get("image", "").lower() for c in containers):
        for c in containers:
            try:
                info = await dockerapi.inspect_container(c["name"])
                labels[c["name"]] = (info.get("Config") or {}).get("Labels") or {}
            except Exception:
                continue
        found += traefik_domains(containers, labels)
    # nginx installed on the host
    sites = _host_path("/etc/nginx/sites-enabled")
    if os.path.isdir(sites):
        for name in sorted(os.listdir(sites))[:50]:
            try:
                found += parse_nginx(open(os.path.join(sites, name), errors="replace").read())
            except OSError:
                continue
    _attach_services(found, containers)
    seen, out = set(), []
    for d in sorted(found, key=lambda d: (not d["enabled"], d["domain"])):
        if d["domain"] in seen:
            continue
        seen.add(d["domain"])
        out.append(d)
    return out


# ------------------------------------------------------------------ systemd

def _unit_files() -> tuple[set[str], set[str]]:
    """(custom, enabled): units an admin added under /etc/systemd/system, and
    units enabled through a *.wants directory."""
    base = _host_path("/etc/systemd/system")
    custom, enabled = set(), set()
    try:
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if not name.endswith((".service", ".timer")):
                if name.endswith(".wants") and os.path.isdir(path):
                    enabled.update(n for n in os.listdir(path) if n.endswith((".service", ".timer")))
                continue
            if os.path.islink(path):
                target = os.readlink(path)
                if target == "/dev/null" or target.startswith(("/lib/", "/usr/lib/")):
                    continue
            if "@." in name:
                continue        # a template; its instances show up as enabled units
            custom.add(name)
    except OSError:
        pass
    return custom, enabled


def _description_from_file(unit: str) -> str:
    for base in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
        path = _host_path(f"{base}/{unit}")
        try:
            for line in open(path, errors="replace"):
                if line.startswith("Description="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def parse_list_units(text: str) -> dict[str, dict]:
    """`systemctl list-units --all --plain --no-legend` → {unit: {load, active, sub, description}}."""
    out = {}
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].endswith((".service", ".timer")):
            continue
        out[parts[0]] = {"load": parts[1], "active": parts[2], "sub": parts[3],
                         "description": parts[4].strip() if len(parts) > 4 else ""}
    return out


def parse_show(text: str) -> dict[str, dict]:
    """`systemctl show a b -p …` → {Id: {prop: value}} (blocks split by blank lines)."""
    out, cur = {}, {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                out[cur["Id"]] = cur
            cur = {}
            continue
        key, _, value = line.partition("=")
        cur[key] = value
    return out


_SHOW_PROPS = ("Id,Description,ActiveState,SubState,UnitFileState,Result,ActiveEnterTimestamp,"
               "NextElapseUSecRealtime,LastTriggerUSec,Triggers,TriggeredBy,MemoryCurrent,"
               "FragmentPath,ExecMainStartTimestamp,ExecMainStatus")


def _when_text(value: str) -> str:
    value = (value or "").strip()
    if not value or value in ("n/a", "0") or value.startswith("[not set]"):
        return ""
    return value


_STAMP = r"\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+"
_TIMER_LINE = re.compile(rf"^(?P<next>{_STAMP}|-|n/a)\s+(?P<left>.*?)\s*(?P<last>{_STAMP}|-|n/a)\s+"
                         rf"(?P<passed>.*?)\s*(?P<unit>\S+\.timer)\s+(?P<activates>\S+)\s*$")


def parse_list_timers(text: str) -> dict[str, dict]:
    """`systemctl list-timers --all --no-legend` → {timer: {next, last}} — the
    only place monotonic timers (every 5 minutes …) show their next run."""
    out = {}
    for line in text.splitlines():
        m = _TIMER_LINE.match(line.strip())
        if m:
            out[m["unit"]] = {"next": "" if m["next"] in ("-", "n/a") else m["next"],
                              "last": "" if m["last"] in ("-", "n/a") else m["last"]}
    return out


async def _systemd() -> list[dict]:
    if not hostrun.available():
        return []
    rc, text = await hostrun.run(
        "systemctl list-units --type=service --type=timer --all --plain --no-legend --no-pager; "
        "echo @@TIMERS@@; systemctl list-timers --all --no-legend --no-pager", timeout=40)
    if rc != 0 or "Failed to connect to bus" in text:
        return []
    units_text, _, timers_text = text.partition("@@TIMERS@@")
    loaded = parse_list_units(units_text)
    timers = parse_list_timers(timers_text)
    custom, enabled = _unit_files()
    wanted = set()
    for unit, st in loaded.items():
        if unit in custom or st["active"] == "failed" or (_KNOWN.match(unit) and
                                                          (unit in enabled or st["active"] == "active")):
            wanted.add(unit)
    wanted |= {u for u in custom if u in loaded or u.endswith(".timer")}
    if not wanted:
        return []
    names = " ".join(sorted(wanted))
    rc, shown = await hostrun.run(f"systemctl show {names} -p {_SHOW_PROPS} --no-pager", timeout=40)
    props = parse_show(shown) if rc == 0 else {}
    out = []
    for unit in sorted(wanted):
        p = props.get(unit, {})
        st = loaded.get(unit, {})
        kind = "timer" if unit.endswith(".timer") else "service"
        mem = p.get("MemoryCurrent", "")
        out.append({
            "unit": unit, "kind": kind,
            "description": p.get("Description") or st.get("description") or _description_from_file(unit),
            "active": p.get("ActiveState") or st.get("active", "inactive"),
            "sub": p.get("SubState") or st.get("sub", ""),
            "enabled": p.get("UnitFileState", "enabled" if unit in enabled else ""),
            "result": p.get("Result", ""),
            "since": _when_text(p.get("ActiveEnterTimestamp", "")),
            "next_run": _when_text(p.get("NextElapseUSecRealtime", "")) or
                        (timers.get(unit) or {}).get("next", ""),
            "last_run": _when_text(p.get("LastTriggerUSec", "")) or
                        (timers.get(unit) or {}).get("last", ""),
            "triggers": p.get("Triggers", ""),
            "triggered_by": p.get("TriggeredBy", ""),
            "memory": int(mem) if mem.isdigit() and int(mem) < 1 << 60 else 0,
            "custom": unit in custom,
            "path": p.get("FragmentPath", ""),
        })
    return out


# ------------------------------------------------------------------ cron

_CRON_SECRET = re.compile(r"((?:token|key|pass(?:word)?|secret)=)\S+", re.I)


def _cron() -> list[dict]:
    out = []

    def read(path: str, user: str = "") -> None:
        try:
            lines = open(_host_path(path), errors="replace").read().splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or re.match(r"^[A-Z_]+=", line):
                continue
            parts = line.split()
            if parts[0].startswith("@"):
                sched, rest = parts[0], parts[1:]
            elif len(parts) >= 6:
                sched, rest = " ".join(parts[:5]), parts[5:]
            else:
                continue
            who = user
            if not user and rest:
                who, rest = rest[0], rest[1:]
            command = _CRON_SECRET.sub(r"\1•••", " ".join(rest))
            out.append({"schedule": sched, "user": who, "command": command[:240],
                        "file": path})

    read("/etc/crontab")
    for base, user_from_name in (("/etc/cron.d", False), ("/var/spool/cron/crontabs", True)):
        try:
            names = sorted(os.listdir(_host_path(base)))
        except OSError:
            continue
        for name in names[:60]:
            if name.startswith("."):
                continue
            read(f"{base}/{name}", user=name if user_from_name else "")
    return out


# ------------------------------------------------------------------ build

async def build(force: bool = False) -> dict:
    if config.DEMO:
        from . import demodata
        return demodata.inventory()
    if not force and _cache["data"] and time.time() - _cache["t"] < TTL:
        return _cache["data"]
    from . import files, hostuser, sysinfo
    try:
        containers = [c for c in await dockerapi.list_containers(all_=True)
                      if not records.is_helper_name(c["name"])]
    except Exception:
        containers = []
    stacks: dict[str, dict] = {}
    loose = []
    for c in containers:
        if c.get("compose_project"):
            s = stacks.setdefault(c["compose_project"], {"project": c["compose_project"],
                                                         "dir": c.get("compose_dir", ""),
                                                         "services": []})
            s["dir"] = s["dir"] or c.get("compose_dir", "")
            s["services"].append({"name": c["name"], "state": c["state"], "health": c.get("health", ""),
                                  "image": c["image"], "ports": c.get("ports", [])})
        else:
            loose.append({"name": c["name"], "state": c["state"], "health": c.get("health", ""),
                          "image": c["image"], "ports": c.get("ports", [])})
    domains, units = await asyncio.gather(_domains(containers), _systemd(),
                                          return_exceptions=True)
    domains = domains if isinstance(domains, list) else []
    units = units if isinstance(units, list) else []
    try:
        ident = hostuser.identity()
    except Exception:
        ident = {}
    try:
        drives = files.storage().get("filesystems", [])
    except Exception:
        drives = []
    snap = {}
    try:
        snap = {"uptime": sysinfo.uptime_seconds(), "memory": sysinfo.memory(),
                "cores": os.cpu_count() or 0}
    except Exception:
        pass
    data = {
        "time": time.time(),
        "host": {"hostname": ident.get("hostname", ""), "os": ident.get("os", ""),
                 "kernel": ident.get("kernel", ""), "arch": ident.get("arch", ""),
                 "cores": snap.get("cores", 0),
                 "memory": (snap.get("memory") or {}).get("total", 0),
                 "uptime": snap.get("uptime", 0)},
        "stacks": sorted(stacks.values(), key=lambda s: s["project"]),
        "containers": sorted(loose, key=lambda c: c["name"]),
        "domains": domains,
        "services": [u for u in units if u["kind"] == "service"],
        "timers": [u for u in units if u["kind"] == "timer"],
        "cron": await asyncio.to_thread(_cron),
        "drives": [{k: d.get(k) for k in ("mount", "kind", "fstype", "label", "model", "total",
                                          "used", "free", "percent", "browsable")} for d in drives],
    }
    _cache.update(t=time.time(), data=data)
    return data


def cached() -> dict | None:
    return _cache["data"]


def map_lines(data: dict | None) -> list[str]:
    """The short version for the assistant's server map."""
    if not data:
        return []
    out = []
    doms = [d for d in data.get("domains", []) if d.get("enabled")]
    if doms:
        out.append("Domains (reverse proxy → service):")
        for d in doms[:60]:
            to = d.get("service") or d.get("target", "")
            out.append(f"  {d['domain']} → {to}" + ("" if d.get("tls") else " (no TLS)")
                       + f" [{d.get('source', '')}]")
    svc = data.get("services", []) + data.get("timers", [])
    if svc:
        failed = [u["unit"] for u in svc if u["active"] == "failed"]
        custom = [u["unit"] for u in svc if u.get("custom")]
        known = [u["unit"] for u in svc if not u.get("custom") and u["active"] == "active"]
        if custom:
            out.append("Host systemd units added by the admin: " + ", ".join(custom[:60]))
        if known:
            out.append("Host infrastructure running: " + ", ".join(known[:40]))
        if failed:
            out.append("FAILED units: " + ", ".join(failed))
    if data.get("cron"):
        out.append(f"Cron jobs: {len(data['cron'])} (e.g. " + "; ".join(
            f"{c['schedule']} {c['command'][:60]}" for c in data["cron"][:4]) + ")")
    drives = [d for d in data.get("drives", []) if d.get("kind") in ("system", "data", "external", "network")]
    if drives:
        out.append("Drives: " + ", ".join(
            f"{d['mount']} {d.get('percent', 0)}% of {round((d.get('total') or 0) / 1e9)} GB"
            + (f" ({d['kind']})" if d.get("kind") != "system" else "") for d in drives[:10]))
    return out


# ------------------------------------------------------------------ one unit

async def unit_detail(unit: str, lines: int = 120) -> dict:
    if not valid_unit(unit):
        raise ValueError("not a unit name")
    if config.DEMO:
        from . import demodata
        return demodata.unit_detail(unit)
    rc, shown = await hostrun.run(f"systemctl show {unit} -p {_SHOW_PROPS} --no-pager", timeout=30)
    p = parse_show(shown).get(unit, {}) if rc == 0 else {}
    lines = max(20, min(int(lines or 120), 1000))
    rc, logs = await hostrun.run(
        f"journalctl -u {unit} -n {lines} --no-pager -o short-iso 2>&1 | tail -n {lines}", timeout=30)
    return {
        "unit": unit, "kind": "timer" if unit.endswith(".timer") else "service",
        "description": p.get("Description", ""), "active": p.get("ActiveState", ""),
        "sub": p.get("SubState", ""), "enabled": p.get("UnitFileState", ""),
        "result": p.get("Result", ""), "since": _when_text(p.get("ActiveEnterTimestamp", "")),
        "next_run": _when_text(p.get("NextElapseUSecRealtime", "")),
        "last_run": _when_text(p.get("LastTriggerUSec", "")),
        "triggers": p.get("Triggers", ""), "triggered_by": p.get("TriggeredBy", ""),
        "path": p.get("FragmentPath", ""),
        "logs": logs if rc == 0 else "",
    }


UNIT_ACTIONS = ("start", "stop", "restart", "enable", "disable")


async def unit_action(unit: str, action: str) -> dict:
    if not valid_unit(unit) or action not in UNIT_ACTIONS:
        raise ValueError("invalid unit or action")
    rc, out = await hostrun.run(f"systemctl {action} {unit} 2>&1", timeout=90)
    _cache["t"] = 0
    return {"ok": rc == 0, "output": out.strip()[-800:]}
