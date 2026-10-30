"""The server's own public sites, looked at from the outside every few minutes
— the way a visitor reaches them: DNS, the internet path, the reverse proxy and
TLS — and the certificate each of them actually serves.

This catches what nothing inside the server sees: a VPS or tunnel in front of
it that went down, a proxy host that lost its certificate, a certificate that
runs out. The served certificate also settles what a reverse proxy's renewal
log cannot: errors about certificates no site serves anymore are noise; a site
whose served certificate runs out is not.

A site that fails twice in a row becomes a warning in the activity feed, which
the watch investigates. When it answers again, that is recorded too, and if
the watch had told the user about the outage, the all-clear follows on its own
(watch.tick sends it).
"""
from __future__ import annotations

import asyncio
import re
import ssl
import time

import httpx

from . import config

INTERVAL = 300           # seconds between looks
CERT_EVERY = 3600        # served certificates change rarely
FAILS_FOR_DOWN = 2       # one timeout is the internet, two in a row is an outage
CERT_WARN_DAYS = 14
CERT_CRIT_DAYS = 3
MAX_SITES = 8
USER_AGENT = "PocketADM-Watch/1.0"


def parse_hosts(raw: str) -> list[str]:
    """"studgo.de, https://pocketadm.com/ x" → ["studgo.de", "pocketadm.com"]."""
    out: list[str] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        host = re.sub(r"^https?://", "", part.strip().lower()).split("/")[0].split(":")[0]
        if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", host) and host not in out:
            out.append(host)
    return out[:MAX_SITES]


async def sites() -> list[str]:
    """The sites to check: the ones set in the watch's settings, else the
    proxy's TLS sites (shortest names first, so the main domains come first)."""
    chosen = parse_hosts((config.settings.get("watch") or {}).get("sites", ""))
    if chosen:
        return chosen
    from . import inventory
    try:
        data = await inventory.build()
    except Exception:
        return []
    domains = sorted({d["domain"].lower() for d in data.get("domains", [])
                      if d.get("enabled") and d.get("tls") and not d.get("redirect")
                      and "*" not in d["domain"]}, key=lambda d: (d.count("."), d))
    return parse_hosts(" ".join(domains))


def _reason(e: Exception) -> str:
    """An exception as a visitor would describe it."""
    text = f"{type(e).__name__}: {e}".lower()
    if "certificate has expired" in text:
        return "the certificate has expired"
    if "hostname mismatch" in text or "doesn't match" in text or "not valid for" in text:
        return "the certificate is for a different name"
    if "certificate_verify_failed" in text or "certificate verify failed" in text:
        return "the certificate is not trusted"
    if "name or service not known" in text or "nodename nor servname" in text \
            or "temporary failure in name resolution" in text or "no address associated" in text:
        return "the name does not resolve"
    if "timeout" in text or "timed out" in text:
        return "no answer within 10 seconds"
    if "refused" in text:
        return "connection refused"
    if "unreachable" in text:
        return "network unreachable"
    return f"{type(e).__name__}"


async def check_site(client: httpx.AsyncClient, host: str) -> dict:
    """One visit: anything below 500 counts as up (a login page, a 403 from a
    VPN gate, a redirect are all the site answering)."""
    t0 = time.monotonic()
    try:
        async with client.stream("GET", f"https://{host}/", headers={"User-Agent": USER_AGENT}) as r:
            stream = r.extensions.get("network_stream")
            addr = stream.get_extra_info("server_addr") if stream else None
            ok = r.status_code < 500
            return {"ok": ok, "status": r.status_code, "ms": int((time.monotonic() - t0) * 1000),
                    "via": "IPv6" if addr and ":" in str(addr[0]) else "IPv4" if addr else "",
                    "error": "" if ok else f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001 — every failure is an answer here
        return {"ok": False, "status": 0, "ms": int((time.monotonic() - t0) * 1000), "via": "",
                "error": _reason(e)}


async def served_cert(host: str) -> dict:
    """The certificate the site hands a visitor: when it runs out, which names."""
    ctx = ssl.create_default_context()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 443, ssl=ctx, server_hostname=host), 10)
    except Exception as e:  # noqa: BLE001
        return {"error": _reason(e)}
    try:
        cert = writer.get_extra_info("peercert") or {}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
    not_after = cert.get("notAfter")
    return {"expires": ssl.cert_time_to_seconds(not_after) if not_after else None,
            "names": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"][:6]}


async def tick(state: dict, now: float | None = None) -> dict:
    """One look, when it is due. Updates state["sites"] in place and returns
    {"events": activity events to record, "back": sites that answer again after
    an outage the watch told the user about}. The caller records the events
    after saving the state — recording one hands it to the watch, which writes
    the same state file."""
    now = now or time.time()
    out: dict = {"events": [], "back": []}
    if now - state.get("probe_at", 0) < INTERVAL:
        return out
    state["probe_at"] = now
    hosts = await sites()
    known = state.setdefault("sites", {})
    for gone in [h for h in known if h not in hosts]:
        known.pop(gone)
    if not hosts:
        return out
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        checks = await asyncio.gather(*(check_site(client, h) for h in hosts))
    cert_due = now - state.get("cert_at", 0) >= CERT_EVERY
    certs = await asyncio.gather(*(served_cert(h) for h in hosts)) if cert_due else []
    if cert_due:
        state["cert_at"] = now
    for i, host in enumerate(hosts):
        res, site = checks[i], known.setdefault(host, {"fails": 0})
        site.update(status=res["status"], ms=res["ms"], via=res["via"] or site.get("via", ""),
                    checked=now)
        if res["ok"]:
            if site.get("down_since"):
                lasted = now - site["down_since"]
                out["events"].append({"category": "network", "kind": "site.up", "severity": "ok",
                                      "title": f"{host} answers again",
                                      "detail": f"after {int(lasted // 60)} minutes", "target": host})
                if site.get("announced"):
                    out["back"].append({"host": host, "since": site["down_since"], "lasted": lasted})
            for key in ("down_since", "announced", "first_fail", "error"):
                site.pop(key, None)
            site["fails"] = 0
        else:
            site["fails"] = site.get("fails", 0) + 1
            site["error"] = res["error"]
            site.setdefault("first_fail", now)
            if site["fails"] == FAILS_FOR_DOWN:
                site["down_since"] = site["first_fail"]
                out["events"].append({
                    "category": "network", "kind": "site.down", "severity": "warn",
                    "title": f"{host} is not reachable",
                    "detail": f"{res['error']} — checked from this server through the internet, "
                              f"{FAILS_FOR_DOWN} times in a row", "target": host})
        if cert_due and certs[i].get("expires"):
            days = int((certs[i]["expires"] - now) // 86400)
            site.update(cert_days=days, cert_names=certs[i]["names"])
            if days <= CERT_WARN_DAYS and now - site.get("cert_warned", 0) > 86400:
                site["cert_warned"] = now
                out["events"].append({
                    "category": "network", "kind": "cert.expiring",
                    "severity": "crit" if days <= CERT_CRIT_DAYS else "warn",
                    "title": f"The certificate {host} serves runs out in {days} days",
                    "detail": "names: " + ", ".join(certs[i]["names"]), "target": host})
    return out


def context_lines(state: dict, clock) -> list[str]:
    """What the watch should know without looking: which sites answer, and how
    long the certificates they serve still last."""
    known = state.get("sites") or {}
    if not known:
        return []
    parts = []
    for host, s in known.items():
        if s.get("down_since"):
            parts.append(f"{host} DOWN since {clock(s['down_since'])} ({s.get('error', '')})")
        elif s.get("fails"):
            parts.append(f"{host} failed the last check ({s.get('error', '')})")
        else:
            parts.append(f"{host} ok (HTTP {s.get('status')}, {s.get('ms')} ms)")
    via = {s.get("via") for s in known.values() if s.get("via")}
    lines = ["Public sites, checked every 5 minutes from this server through the internet"
             + (f" over {via.pop()}" if len(via) == 1 else "") + ", the way visitors reach them: "
             + "; ".join(parts)]
    certs: dict[str, int] = {}
    for host, s in known.items():
        if s.get("cert_days") is not None:
            certs.setdefault(", ".join(s.get("cert_names") or [host]), s["cert_days"])
    if certs:
        lines.append("Certificates the sites actually serve: "
                     + "; ".join(f"{names}: {days} days left" for names, days in certs.items())
                     + ". These days left decide whether a certificate problem matters; renewal "
                       "errors in the reverse proxy's log about certificates no site serves are noise.")
    return lines
