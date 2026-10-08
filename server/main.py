"""Helmsman — self-hosted server command center. FastAPI app assembly."""
import asyncio
import json
import os
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agents, ai, appstore, audit, auth, backups, bootstrap, chats,
               clis, config, demodata, dockerapi, engines, hostuser, integrations, jobs,
               localai, metrics, oidc, pairing, permissions, reports, servermap,
               servicegroups, sessions, skills, snapshots, sysinfo, terminal, termsessions,
               tls, updates)
from . import (accounts, activity, channel, files, inventory, memory, push, signin,
               suggestions, uploads, watch)

app = FastAPI(title="Helmsman", docs_url=None, redoc_url=None)
auth.bootstrap_password()
auth.install_log_redaction()

authed = Depends(auth.require_auth)

# Demo instances are a public read-only playground: every mutation is blocked.
# A WebSocket ticket changes nothing — without it the demo's chat and terminal
# could not even connect.
DEMO_ALLOW = {"/api/login", "/api/notifications/seen", "/api/ws/ticket", "/api/watch/channel/read"}


@app.middleware("http")
async def _demo_guard(request: Request, call_next):
    if config.DEMO and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.url.path not in DEMO_ALLOW:
        return JSONResponse({"detail": "Demo mode — this instance is read-only"},
                            status_code=403)
    return await call_next(request)


# The web UI renders model output, so a script-injection bug there would run
# with a root-on-host session. The policy allows only scripts served by this
# server (no inline script, no eval); styles may be inline because the UI and
# xterm.js set them from code. connect-src stays open: the multi-server client
# talks to other PocketADM boxes. frame-ancestors stops clickjacking, and
# no-referrer keeps one-time codes in the URL from leaking to external links.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; "
       "connect-src 'self' https: wss: http: ws:; worker-src 'self'; "
       "manifest-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=()",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# The client app may be served from a different Helmsman instance (multi-server
# mode) or packaged natively. Auth is a bearer token in a header — no cookies —
# so a permissive CORS policy does not open a CSRF hole.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def _startup():
    metrics.start()
    if config.DEMO:
        demodata.seed()
        demodata.start_activity()
    else:
        activity.start()
        skills.seed_defaults()
        reports.start_scheduler()
        agents.start_scheduler()
        watch.start()
        asyncio.ensure_future(appstore.remote_refresher())
        asyncio.ensure_future(localai.reconnect_on_startup())


# ------------------------------------------------------------------ auth

import ipaddress


def _is_private_host(host: str) -> bool:
    """True if `host` is a loopback / LAN / mDNS name — i.e. NOT internet-facing.

    Used to decide whether this instance looks publicly exposed. Conservative:
    anything we can't positively classify as local is treated as public, so the
    security nudge errs toward warning rather than staying silent."""
    host = (host or "").strip().lower()
    if host.startswith("["):              # bracketed IPv6, maybe with :port
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:            # host:port (IPv4 or hostname)
        host = host.rsplit(":", 1)[0]
    host = host.strip()
    if not host or host == "localhost":
        return True
    if host.endswith((".local", ".lan", ".internal", ".home.arpa", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False  # a real DNS name (e.g. dev.example.com) -> public


def is_public_exposure(request: Request) -> bool:
    """Whether the client reached this instance over an internet-facing host.

    Heuristic, not a guarantee (a public FQDN can still sit behind a VPN), so
    the UI treats it as an advisory the user can acknowledge — but for the many
    self-hosters who put this on a domain without a second factor, it's the
    signal that turns 'root gateway wide open' into a visible warning."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    # x-forwarded-host may carry a comma-separated chain; the first is the client's
    host = host.split(",")[0].strip()
    return not _is_private_host(host)


class LoginBody(BaseModel):
    password: str
    totp: str = ""


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    auth.rate_limit(ip)
    if config.DEMO:
        # public playground: fixed demo credentials, no lockout surprises
        if body.password == "demo":
            return {"token": auth.issue_token(), "demo": True}
        auth.record_failure(ip)
        raise HTTPException(401, "Demo password is “demo”")
    if not auth.verify_password(body.password):
        auth.record_failure(ip)
        audit.record("login_failed", target=ip, detail="wrong password", status="warn")
        raise HTTPException(401, "Wrong password")
    if auth.totp_enabled():
        if not body.totp:
            # password ok, but a second factor is required
            return JSONResponse({"detail": "2FA code required", "totp": True},
                                status_code=401)
        if not auth.verify_totp(body.totp):
            auth.record_failure(ip)
            audit.record("login_failed", target=ip, detail="wrong 2FA code", status="warn")
            return JSONResponse({"detail": "Wrong 2FA code", "totp": True},
                                status_code=401)
    audit.record("login", target=ip, detail="2FA" if auth.totp_enabled() else "password")
    return {"token": auth.issue_token()}


@app.get("/api/info")
async def server_info():
    """Unauthenticated, minimal server identity for the client Connect screen —
    lets a new device confirm it reached a real Helmsman before signing in."""
    return {
        "helmsman": True,
        "version": config.VERSION,
        "server_name": config.get_server_name() or sysinfo.hostname(),
        "demo": config.DEMO,
        "totp_required": auth.totp_enabled(),
        # label for a "Sign in with …" button, or None when SSO is off
        "sso": oidc.public_info(),
    }


# --------------------------------------------------------- device pairing

@app.post("/api/pair/new", dependencies=[authed])
async def pair_new():
    """Mint a one-time pairing code (shown as a QR on the signed-in device).
    Another device scans it and calls /api/pair/claim to get its own token."""
    code, ttl = pairing.new_code()
    audit.record("pair_new", detail="pairing code issued")
    return {"code": code, "ttl": ttl,
            "server_name": config.get_server_name() or sysinfo.hostname(),
            # fingerprint of the self-signed HTTPS key; a client puts it in the
            # QR when it encodes the https://…:8443 address (see tls.py)
            "tls_fingerprint": tls.fingerprint()}


class QRBody(BaseModel):
    text: str


@app.post("/api/qr", dependencies=[authed])
async def make_qr(body: QRBody):
    """Render arbitrary text as a QR SVG (segno) — used for the pairing screen,
    which builds the payload from the browser's own origin + a pairing code."""
    svg = auth.qr_svg(body.text[:800])
    if not svg:
        raise HTTPException(501, "QR rendering unavailable (segno not installed)")
    return {"svg": svg}


class PairClaimBody(BaseModel):
    code: str


@app.post("/api/pair/claim")
async def pair_claim(body: PairClaimBody, request: Request):
    ip = request.client.host if request.client else "?"
    auth.rate_limit(ip)
    if not pairing.claim(body.code):
        auth.record_failure(ip)
        audit.record("pair_claim", target=ip, status="warn", detail="invalid/expired code")
        raise HTTPException(401, "Pairing code is invalid or expired")
    audit.record("pair_claim", target=ip, detail="new device paired")
    return {"token": auth.issue_token(),
            "server_name": config.get_server_name() or sysinfo.hostname()}


@app.post("/api/ws/ticket", dependencies=[authed])
async def ws_ticket():
    """Trade the bearer token (sent as a header) for a single-use WebSocket
    ticket, so the long-lived token never appears in a URL or an access log."""
    return {"ticket": auth.issue_ws_ticket(), "ttl": auth.WS_TICKET_TTL}


FEATURES = ["services", "container_live", "activity", "storage", "files_v2", "watch",
            "accounts", "routes", "chat_manage", "health_v2", "update_details_v2",
            "watch_channel", "push", "files_manage", "chat_presence", "update_explain_v2",
            # 0.26: notes instead of one memory text, the server inventory with
            # systemd units, suggestions from the server's state, Vibe models
            "notes", "inventory", "units", "suggestions", "vibe_models", "chat_attachments"]


@app.get("/api/me", dependencies=[authed])
async def me(request: Request):
    default = config.get_ai_default()
    # Root gateway on a public host without 2FA -> flag it so the UI can warn.
    exposed = not config.DEMO and is_public_exposure(request)
    return {
        "ok": True,
        "version": config.VERSION,
        "demo": config.DEMO,
        "hostname": sysinfo.hostname(),
        "server_name": config.get_server_name() or sysinfo.hostname(),
        "onboarded": config.get_onboarded(),
        "ai_configured": bool(default["provider"]) or bool(engines.installed_engines()),
        "ai_default": default,
        "ai_providers": config.configured_providers(),
        "workspaces": config.get_workspaces(),
        "default_workspace": config.get_default_workspace(),
        "report_config": config.get_report_config(),
        "totp_enabled": auth.totp_enabled(),
        "can_pair": not config.DEMO,
        "public_exposure": exposed,
        # once the user has explicitly chosen to run exposed without 2FA, stop nagging
        "exposure_ack": config.get_exposure_ack(),
        # what this server can do, so a newer app can fall back on an older server
        "features": FEATURES,
        "watch_enabled": bool((config.settings.get("watch") or {}).get("enabled"))
                         or config.DEMO,
    }


# ------------------------------------------------------------ dashboard

@app.get("/api/system", dependencies=[authed])
async def system():
    data = await asyncio.to_thread(sysinfo.snapshot)
    data["docker"] = await dockerapi.engine_info() if await dockerapi.available() else None
    last = metrics.latest()
    data["net"] = {"rx": last["rx"], "tx": last["tx"], "ping": last["ping"]} if last else None
    return data


_image_refs: dict = {}   # container id -> the image it was created from (servicegroups)


async def _annotated_containers() -> tuple[list[dict], list[dict]]:
    # PocketADM's own command runners and terminal shells are not services
    result = [c for c in await dockerapi.list_containers() if not c.get("helper")]
    await servicegroups.resolve_images(result, _image_refs)
    groups = servicegroups.annotate(result)
    return result, groups


@app.get("/api/containers", dependencies=[authed])
async def containers():
    # each container carries its app (group_id/group_name), its role in it and
    # a display name that is unique on this server — see servicegroups.py
    result, _ = await _annotated_containers()
    return result


@app.get("/api/services", dependencies=[authed])
async def services():
    """The containers grouped into apps (Nextcloud = app + database + cache +
    cron), problems first. The iOS app's Containers tab shows these."""
    _, groups = await _annotated_containers()
    return {"groups": groups}


class GroupActionBody(BaseModel):
    action: str


@app.post("/api/services/{group_id}/action", dependencies=[authed])
async def service_action(group_id: str, body: GroupActionBody):
    """Start, stop or restart every container of one app, in an order that
    works: databases and caches first on the way up, last on the way down."""
    if body.action not in ("start", "stop", "restart"):
        raise HTTPException(400, "bad action")
    _, groups = await _annotated_containers()
    group = next((g for g in groups if g["id"] == group_id), None)
    if not group:
        raise HTTPException(404, "no such app")
    own = localai.own_container_name()
    members = [c for c in group["containers"]
               if c["name"] != "helmsman" and not c["id"].startswith(own or "\0")]
    support_first = sorted(members, key=lambda c: not servicegroups.is_support(c))
    order = support_first if body.action == "start" else list(reversed(support_first))
    done, failed = [], []
    for c in order:
        if body.action == "start" and c["state"] == "running":
            continue
        if body.action == "stop" and c["state"] != "running":
            continue
        try:
            await dockerapi.container_action(c["id"], body.action)
            done.append(c["name"])
        except Exception as e:  # keep going: one stuck container must not strand the rest
            failed.append(f"{c['name']}: {str(e)[:120]}")
    audit.record("container_action", target=group["name"],
                 detail=f"{body.action} app ({len(done)} containers)"
                        + (f", {len(failed)} failed" if failed else ""),
                 status="warn" if failed else "ok")
    return {"ok": not failed, "done": done, "failed": failed}


@app.get("/api/containers/{cid}/logs", dependencies=[authed])
async def container_logs(cid: str, tail: int = 200, since: int = 0, timestamps: bool = False):
    return {"logs": await dockerapi.container_logs(cid, min(tail, 5000), max(0, since), timestamps)}


@app.get("/api/containers/{cid}/logs/stream", dependencies=[authed])
async def container_logs_stream(cid: str, tail: int = 50):
    """The live tail: new log lines as plain text while the connection lasts."""
    return StreamingResponse(dockerapi.follow_logs(cid, min(max(tail, 0), 500)),
                             media_type="text/plain",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/containers/{cid}/stats", dependencies=[authed])
async def container_stats(cid: str, live: bool = False):
    try:
        return await dockerapi.container_stats(cid, live)
    except Exception as e:
        raise HTTPException(404, f"stats unavailable: {str(e)[:120]}")


@app.get("/api/containers/{cid}/top", dependencies=[authed])
async def container_top(cid: str):
    try:
        return await dockerapi.container_top(cid)
    except Exception as e:
        raise HTTPException(404, f"top failed: {str(e)[:120]}")


@app.get("/api/containers/{cid}/events", dependencies=[authed])
async def container_events(cid: str, hours: int = 72):
    try:
        return {"events": await dockerapi.container_events(cid, hours)}
    except Exception:
        return {"events": []}


@app.get("/api/containers/{cid}/detail", dependencies=[authed])
async def container_detail(cid: str):
    try:
        detail = await dockerapi.container_detail(cid)
    except Exception as e:
        raise HTTPException(404, f"inspect failed: {e}")
    detail["service"] = updates.service_meta(detail["image"])
    return detail


DESCRIBE_SYSTEM = (
    "You explain a Docker container to a self-hoster who is not a sysadmin. "
    "Given inspect data and recent logs, answer briefly with markdown: "
    "1) What this service is and what it does for the user (2-3 sentences, plain words). "
    "2) Current state: does it look healthy? Anything notable in the logs? "
    "3) One concrete tip if something should be improved. Max ~160 words.")


class DescribeBody(BaseModel):
    lang: str = ""


@app.post("/api/containers/{cid}/describe", dependencies=[authed])
async def container_describe(cid: str, body: DescribeBody):
    try:
        detail = await dockerapi.container_detail(cid)
        logs = await dockerapi.container_logs(cid, 60)
    except Exception as e:
        raise HTTPException(404, f"inspect failed: {e}")
    detail["service"] = updates.service_meta(detail["image"])
    prompt = ("Container inspect summary:\n" + json.dumps(detail, default=str)[:4000] +
              "\n\nRecent logs:\n" + logs[-3000:])
    if body.lang:
        prompt += f"\n\nAnswer in language: {body.lang}"
    try:
        return {"description": await ai.one_shot(prompt, DESCRIBE_SYSTEM)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/containers/{cid}/describe/stream", dependencies=[authed])
async def container_describe_stream(cid: str, lang: str = ""):
    """Same explainer as /describe, but streamed as Server-Sent Events so the UI
    shows the answer forming live (and can tell it's actually working)."""
    try:
        detail = await dockerapi.container_detail(cid)
        logs = await dockerapi.container_logs(cid, 60)
    except Exception as e:
        raise HTTPException(404, f"inspect failed: {e}")
    detail["service"] = updates.service_meta(detail["image"])
    prompt = ("Container inspect summary:\n" + json.dumps(detail, default=str)[:4000] +
              "\n\nRecent logs:\n" + logs[-3000:])
    if lang:
        prompt += f"\n\nAnswer in language: {lang}"

    async def gen():
        try:
            async for piece in ai.one_shot_stream(prompt, DESCRIBE_SYSTEM):
                yield "data: " + json.dumps({"delta": piece}) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        except Exception as e:  # surface the failure into the stream, not a 500
            yield "data: " + json.dumps({"error": str(e)}) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class RestartPolicyBody(BaseModel):
    policy: str


@app.post("/api/containers/{cid}/restart-policy", dependencies=[authed])
async def container_restart_policy(cid: str, body: RestartPolicyBody):
    try:
        await dockerapi.set_restart_policy(cid, body.policy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    audit.record("container_action", target=cid, detail=f"restart policy → {body.policy}")
    return {"ok": True, "policy": body.policy}


# NOTE: registered AFTER the specific POST routes above — FastAPI matches routes
# in registration order, so this catch-all must not shadow e.g. …/describe.
@app.post("/api/containers/{cid}/{action}", dependencies=[authed])
async def container_action(cid: str, action: str):
    if action not in ("start", "stop", "restart", "pause", "unpause", "kill"):
        raise HTTPException(400, "bad action")
    if action in ("stop", "kill", "pause"):
        own = localai.own_container_name()
        try:
            info = await dockerapi.inspect_container(cid)
            name = (info.get("Name") or "").lstrip("/")
        except Exception:
            name = ""
        if name == "helmsman" or (own and (cid.startswith(own) or name == own)):
            raise HTTPException(400, "PocketADM will not stop itself — use the host's shell for that")
    try:
        await dockerapi.container_action(cid, action)
    except RuntimeError as e:
        raise HTTPException(400, str(e)[:300])
    audit.record("container_action", target=cid, detail=action)
    return {"ok": True}


@app.delete("/api/containers/{cid}", dependencies=[authed])
async def container_remove(cid: str, force: bool = False):
    """Remove a container. Named volumes stay on disk, so app data survives —
    the UI says so. PocketADM refuses to remove itself."""
    own = localai.own_container_name()
    try:
        info = await dockerapi.inspect_container(cid)
        name = (info.get("Name") or "").lstrip("/")
    except Exception:
        raise HTTPException(404, "no such container")
    if cid.startswith(own) or (info.get("Id") or "").startswith(own) or name == "helmsman":
        raise HTTPException(400, "refusing to remove the PocketADM container from inside itself")
    await dockerapi.remove_container(cid, force=force)
    audit.record("container_remove", target=name or cid, detail="forced" if force else "")
    return {"ok": True, "name": name}


# ------------------------------------------------------------ metrics

@app.get("/api/metrics/history", dependencies=[authed])
async def metrics_history(minutes: int = 60):
    return {"points": metrics.history(min(minutes, 10080)), "interval": metrics.INTERVAL}


@app.get("/api/metrics/context", dependencies=[authed])
async def metrics_context(t: float, window: int = 240):
    """What happened around a moment in time — used to explain anomaly markers
    on the metric graphs: docker events, PocketADM actions (audit log) and jobs
    in a ±window/2 slice around t."""
    window = max(60, min(window, 3600))
    lo, hi = t - window / 2, t + window / 2
    try:
        ev = await dockerapi.events(lo, hi)
    except Exception:
        ev = []
    entries = []
    for e in ev:
        label = {
            "start": "started", "die": "exited", "stop": "stopped",
            "kill": "was killed", "restart": "restarted", "oom": "ran OUT OF MEMORY",
            "destroy": "was removed", "create": "was created",
        }.get((e["action"] or "").split(":")[0])
        if e["type"] == "image":
            label = "image pulled" if e["action"] == "pull" else None
        if (e["action"] or "").startswith("health_status"):
            label = "health turned " + e["action"].split(":")[-1].strip()
        if not label:
            continue
        summary = f'{e["name"]} {label}'
        if e.get("exit_code") not in (None, "", "0"):
            summary += f' (exit {e["exit_code"]})'
        entries.append({"t": e["t"], "kind": "docker", "summary": summary})
    for a in audit.recent(limit=400)["events"]:
        ts = a.get("t") or 0
        if lo <= ts <= hi:
            entries.append({"t": ts, "kind": "action",
                            "summary": f'{a.get("action", "?")} {a.get("target", "")}'.strip()
                                       + (f' · {a.get("detail")}' if a.get("detail") else "")
                                       + f' — via {a.get("source", "ui")}'})
    entries.sort(key=lambda x: x["t"])
    return {"events": entries[:40], "window": window}


# ---------------------------------------------------------- fs browser
# Browse, preview, download, measure — and manage, with the guardrails in
# files.py (system folders, credentials, versions for an undo).


@app.get("/api/fs/start", dependencies=[authed])
async def fs_start():
    """Where the file browser opens: "/" when PocketADM sees the whole server."""
    return await asyncio.to_thread(files.start)


def _fs_error(e: Exception) -> HTTPException:
    if isinstance(e, PermissionError):
        return HTTPException(403, str(e) or "permission denied")
    if isinstance(e, FileNotFoundError):
        return HTTPException(404, str(e) or "not found")
    if isinstance(e, FileExistsError):
        return HTTPException(409, str(e) or "already exists")
    if isinstance(e, files.Conflict):
        return HTTPException(409, str(e))
    if isinstance(e, ValueError):
        return HTTPException(400, str(e))
    if isinstance(e, OSError):
        return HTTPException(500, e.strerror or str(e))
    return HTTPException(500, str(e))


class FsWriteBody(BaseModel):
    path: str
    content: str
    expected_modified: float | None = None
    create: bool = False


@app.post("/api/fs/write", dependencies=[authed])
async def fs_write(body: FsWriteBody):
    """Save a text file (or create one with create=true). The previous version
    is kept: POST /api/fs/restore with the returned `version` undoes the save."""
    try:
        result = await asyncio.to_thread(files.write_text, body.path, body.content,
                                         body.expected_modified, body.create)
    except Exception as e:  # noqa: BLE001 — mapped to a status code
        raise _fs_error(e)
    audit.record("file_create" if body.create else "file_edit", target=result["display"],
                 detail=f"{result['size']} bytes")
    return result


class FsRestoreBody(BaseModel):
    version: str


@app.post("/api/fs/restore", dependencies=[authed])
async def fs_restore(body: FsRestoreBody):
    try:
        result = await asyncio.to_thread(files.restore_version, body.version)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_restore", target=result["display"])
    return result


class FsMkdirBody(BaseModel):
    path: str
    name: str


@app.post("/api/fs/mkdir", dependencies=[authed])
async def fs_mkdir(body: FsMkdirBody):
    try:
        result = await asyncio.to_thread(files.make_dir, body.path, body.name)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_mkdir", target=result["display"])
    return result


@app.post("/api/fs/upload", dependencies=[authed])
async def fs_upload(request: Request, path: str, name: str, overwrite: bool = False):
    """The request body is the file itself (any type, streamed to disk)."""
    import queue
    import threading
    chunks: "queue.Queue[bytes | None]" = queue.Queue(maxsize=64)

    def feed():
        while True:
            item = chunks.get()
            if item is None:
                return
            yield item

    holder: dict = {}

    def worker():
        try:
            holder["result"] = files.save_upload(path, name, feed(), overwrite)
        except BaseException as e:  # noqa: BLE001
            holder["error"] = e
            while chunks.get() is not None:      # drain so the reader never blocks
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        async for chunk in request.stream():
            if chunk:
                await asyncio.to_thread(chunks.put, chunk)
    finally:
        await asyncio.to_thread(chunks.put, None)
        await asyncio.to_thread(thread.join)
    if "error" in holder:
        raise _fs_error(holder["error"])
    result = holder["result"]
    audit.record("file_upload", target=result["display"], detail=f"{result['size']} bytes")
    return result


class FsRenameBody(BaseModel):
    path: str
    name: str


@app.post("/api/fs/rename", dependencies=[authed])
async def fs_rename(body: FsRenameBody):
    try:
        result = await asyncio.to_thread(files.rename, body.path, body.name)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_rename", target=files.display(body.path), detail=f"→ {body.name}")
    return result


class FsMoveBody(BaseModel):
    paths: list[str]
    dest: str
    action: str = "move"            # "move" | "copy"
    overwrite: bool = False


@app.post("/api/fs/move", dependencies=[authed])
async def fs_move(body: FsMoveBody):
    if not body.paths:
        raise HTTPException(400, "nothing to move")
    try:
        result = await asyncio.to_thread(files.move, body.paths, body.dest,
                                         body.action == "copy", body.overwrite)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_copy" if body.action == "copy" else "file_move", target=files.display(body.dest),
                 detail=", ".join(result["done"])[:300])
    return result


class FsPathsBody(BaseModel):
    paths: list[str]


@app.post("/api/fs/delete", dependencies=[authed])
async def fs_delete(body: FsPathsBody):
    if not body.paths:
        raise HTTPException(400, "nothing to delete")
    try:
        result = await asyncio.to_thread(files.delete, body.paths)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_delete", target=", ".join(result["removed"])[:300])
    return result


class FsChmodBody(BaseModel):
    path: str
    mode: str
    recursive: bool = False


@app.post("/api/fs/chmod", dependencies=[authed])
async def fs_chmod(body: FsChmodBody):
    try:
        result = await asyncio.to_thread(files.chmod, body.path, body.mode, body.recursive)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_chmod", target=result["display"], detail=body.mode)
    return result


class FsPathBody(BaseModel):
    path: str


@app.post("/api/fs/extract", dependencies=[authed])
async def fs_extract(body: FsPathBody):
    try:
        result = await asyncio.to_thread(files.extract, body.path)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_extract", target=result["display"])
    return result


@app.get("/api/fs/archive", dependencies=[authed])
async def fs_archive(path: str):
    """A folder as a zip file, to save or share on the phone."""
    from starlette.background import BackgroundTask
    try:
        out = await asyncio.to_thread(files.make_archive, path)
    except Exception as e:  # noqa: BLE001
        raise _fs_error(e)
    audit.record("file_download", target=files.display(files.resolve(path)), detail="folder as zip")

    def cleanup():
        import shutil
        shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    return FileResponse(out, filename=os.path.basename(out), media_type="application/zip",
                        content_disposition_type="attachment", background=BackgroundTask(cleanup))

@app.get("/api/fs", dependencies=[authed])
async def fs_list(path: str = "", want_files: int = Query(0, alias="files"), hidden: int = 0):
    """Directory browser over the configured workspaces (by default the whole
    host). ?files=1 also returns file entries, ?hidden=1 includes dotfiles."""
    try:
        return await asyncio.to_thread(files.listing, path, bool(want_files), bool(hidden))
    except PermissionError as e:
        raise HTTPException(403, str(e) or "permission denied")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.get("/api/fs/read", dependencies=[authed])
async def fs_read(path: str):
    """Preview a text file inside the allowed roots (size-capped)."""
    try:
        return await asyncio.to_thread(files.read_text, path)
    except PermissionError as e:
        raise HTTPException(403, str(e) or "permission denied")
    except (FileNotFoundError, OSError):
        raise HTTPException(404, "not readable")


@app.get("/api/fs/raw", dependencies=[authed])
async def fs_raw(path: str, download: bool = False):
    """The file itself, for a preview (images, PDFs, video) or to save it on
    the phone. Range requests work, so large media can be scrubbed."""
    try:
        resolved = await asyncio.to_thread(files.raw_path, path)
    except PermissionError as e:
        raise HTTPException(403, str(e) or "permission denied")
    except FileNotFoundError:
        raise HTTPException(404, "not a file")
    audit.record("file_download", target=files.display(resolved),
                 detail="download" if download else "preview")
    return FileResponse(resolved, filename=os.path.basename(resolved),
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/fs/usage", dependencies=[authed])
async def fs_usage(path: str):
    """What fills a folder: the size of everything directly inside it."""
    try:
        return await files.usage(path)
    except PermissionError as e:
        raise HTTPException(403, str(e) or "permission denied")


@app.get("/api/fs/search", dependencies=[authed])
async def fs_search(path: str, q: str):
    try:
        return await asyncio.to_thread(files.search, path, q)
    except PermissionError as e:
        raise HTTPException(403, str(e) or "permission denied")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/storage", dependencies=[authed])
async def storage():
    """The server's drives — system disk, data disks, USB drives, network
    shares — with how full each one is."""
    if config.DEMO:
        return demodata.storage()
    return await asyncio.to_thread(files.storage)


# ----------------------------------------------------- SSH bootstrap (fleet)

class BootstrapBody(BaseModel):
    host: str
    user: str = "root"
    password: str = ""
    key: str = ""
    port: int = 22
    install_port: int = 8443          # the new server's HTTPS port


@app.post("/api/bootstrap/ssh", dependencies=[authed])
async def bootstrap_ssh(body: BootstrapBody):
    """Install PocketADM onto another machine over SSH, streamed as a job.
    Credentials are used transiently and never stored."""
    if not bootstrap.available():
        raise HTTPException(501, "SSH support unavailable (paramiko not installed)")
    host = (body.host or "").strip()
    if not host:
        raise HTTPException(400, "host is required")
    job = bootstrap.start_job(host, (body.user or "root").strip(),
                              password=body.password, key=body.key,
                              port=body.port or 22, install_port=body.install_port or 8443)
    audit.record("bootstrap_ssh", target=f"{body.user}@{host}",
                 detail="remote install started")
    return {"job_id": job.id}


# ----------------------------------------------------------------- jobs

@app.get("/api/jobs", dependencies=[authed])
async def list_jobs(kind: str | None = None):
    return jobs.recent(kind)


@app.get("/api/jobs/{job_id}", dependencies=[authed])
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job.as_dict(tail=500)


@app.get("/api/jobs/{job_id}/stream", dependencies=[authed])
async def stream_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return StreamingResponse(job.follow(), media_type="text/plain")


# -------------------------------------------------------------- updates

@app.get("/api/updates", dependencies=[authed])
async def get_updates(force: bool = False):
    docker_updates, apt = await asyncio.gather(
        updates.check_docker_updates(force), updates.check_apt_updates())
    return {"docker": docker_updates, "apt": apt}


@app.get("/api/updates/detail", dependencies=[authed])
async def update_detail(image: str, lang: str = ""):
    """Installed version/build date + latest upstream releases for one image,
    and the summary of what changes when one was already written."""
    details = await updates.release_details(image)
    details["explanation"] = updates.cached_explanation(
        image, details["remote"].get("digest", ""), lang)
    return details


class UpdateBody(BaseModel):
    image: str
    recreate: bool = True


@app.post("/api/updates/apply", dependencies=[authed])
async def apply_update(body: UpdateBody):
    job = updates.start_update_job(body.image, body.recreate)
    audit.record("update_apply", target=body.image)
    return {"job_id": job.id}


class UpdateAllBody(BaseModel):
    images: list[str]


@app.post("/api/updates/apply-all", dependencies=[authed])
async def apply_all_updates(body: UpdateAllBody):
    if not body.images:
        raise HTTPException(400, "no images given")
    job = updates.start_update_all_job(body.images[:30])
    audit.record("update_apply", target=f"{len(body.images)} images",
                 detail=", ".join(body.images[:8]))
    return {"job_id": job.id}


class IgnoreBody(BaseModel):
    image: str
    ignored: bool


@app.post("/api/updates/ignore", dependencies=[authed])
async def ignore_update(body: IgnoreBody):
    config.set_ignored_image(body.image, body.ignored)
    updates._cache["time"] = 0
    return {"ok": True}


class ExplainBody(BaseModel):
    subject: str
    kind: str = "docker"
    lang: str = ""


@app.post("/api/updates/explain", dependencies=[authed])
async def explain(body: ExplainBody):
    try:
        return {"explanation": await updates.explain_update(body.subject, body.kind, body.lang)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ------------------------------------------------------------- snapshots

@app.get("/api/snapshots", dependencies=[authed])
async def snapshots_index():
    return {"snapshots": snapshots.list_snapshots()}


@app.post("/api/snapshots/{snap_id}/rollback", dependencies=[authed])
async def snapshot_rollback(snap_id: str):
    snap = snapshots.get(snap_id)
    if not snap:
        raise HTTPException(404, "no such snapshot")
    job = snapshots.start_rollback_job(snap)
    audit.record("snapshot_rollback", target=snap["image"],
                 detail=f"to {snap['image_id']}", status="warn")
    return {"job_id": job.id}


@app.delete("/api/snapshots/{snap_id}", dependencies=[authed])
async def snapshot_delete(snap_id: str):
    if not await snapshots.delete(snap_id):
        raise HTTPException(404, "no such snapshot")
    audit.record("snapshot_delete", target=snap_id)
    return {"ok": True}


# ------------------------------------------------------------- appstore

@app.get("/api/apps", dependencies=[authed])
async def apps():
    return {"catalog": appstore.catalog(), "installed": await appstore.installed(),
            "catalog_info": appstore.catalog_info()}


@app.post("/api/apps/catalog/refresh", dependencies=[authed])
async def apps_catalog_refresh():
    return await appstore.refresh_remote(force=True)


class CatalogUrlBody(BaseModel):
    url: str


@app.post("/api/apps/catalog/url", dependencies=[authed])
async def apps_catalog_url(body: CatalogUrlBody):
    config.set_catalog_url(body.url)
    info = await appstore.refresh_remote(force=True)
    audit.record("catalog_url", detail=body.url[:120])
    return info


class InstallBody(BaseModel):
    values: dict[str, str] = {}


@app.post("/api/apps/{app_id}/install", dependencies=[authed])
async def install_app(app_id: str, body: InstallBody):
    try:
        out = await appstore.install(app_id, body.values)
        audit.record("app_install", target=app_id)
        return {"output": out}
    except (ValueError, RuntimeError) as e:
        audit.record("app_install", target=app_id, status="error", detail=str(e)[:200])
        raise HTTPException(400, str(e))


@app.post("/api/apps/{app_id}/uninstall", dependencies=[authed])
async def uninstall_app(app_id: str, remove_data: bool = False):
    try:
        out = await appstore.uninstall(app_id, remove_data)
        audit.record("app_uninstall", target=app_id,
                     detail="with data" if remove_data else "")
        return {"output": out}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------- ai / settings

@app.get("/api/ai/accounts", dependencies=[authed])
async def ai_accounts():
    """Every AI account — Claude, ChatGPT, Mistral, OpenRouter, local — with
    how it is connected (subscription and/or API key) and what uses it."""
    return await accounts.overview()


@app.post("/api/ai/accounts/{engine}/signin", dependencies=[authed])
async def ai_signin_start(engine: str):
    """Connect a subscription through its coding CLI, from the phone: installs
    the CLI when it is missing and returns the sign-in flow to poll."""
    if engine not in signin.CLI_FOR:
        raise HTTPException(404, "unknown account")
    flow = await signin.start(engine)
    return {"flow": flow.as_dict()}


@app.get("/api/ai/signin/{flow_id}", dependencies=[authed])
async def ai_signin_poll(flow_id: str):
    flow = signin.get(flow_id)
    if not flow:
        raise HTTPException(404, "this sign-in is over — start it again")
    return {"flow": flow.as_dict()}


class SignInCodeBody(BaseModel):
    code: str


@app.post("/api/ai/signin/{flow_id}/code", dependencies=[authed])
async def ai_signin_code(flow_id: str, body: SignInCodeBody):
    if not body.code.strip():
        raise HTTPException(400, "paste the code from the sign-in page")
    try:
        flow = await signin.submit_code(flow_id, body.code[:500])
    except KeyError:
        raise HTTPException(404, "this sign-in is over — start it again")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"flow": flow.as_dict()}


@app.delete("/api/ai/signin/{flow_id}", dependencies=[authed])
async def ai_signin_cancel(flow_id: str):
    await signin.cancel(flow_id)
    return {"ok": True}


@app.post("/api/ai/accounts/{engine}/signout", dependencies=[authed])
async def ai_signout(engine: str):
    if engine not in signin.CLI_FOR:
        raise HTTPException(404, "unknown account")
    await signin.sign_out(engine)
    return await accounts.overview()


@app.get("/api/ai/routes", dependencies=[authed])
async def ai_routes():
    return {"routes": accounts.routes()}


class RouteBody(BaseModel):
    feature: str
    provider: str = ""        # "" = same as the assistant (not for the assistant itself)
    model: str = ""


@app.post("/api/ai/routes", dependencies=[authed])
async def ai_set_route(body: RouteBody):
    if body.feature not in config.AI_FEATURES:
        raise HTTPException(400, "unknown feature")
    if body.provider and not accounts.usable(body.provider):
        raise HTTPException(400, f"{accounts.provider_label(body.provider)} is not connected")
    if body.feature == "assistant" and not body.provider:
        raise HTTPException(400, "the assistant needs a provider")
    model = body.model or ("default" if body.provider in engines.ENGINES
                           else config.DEFAULT_MODELS.get(body.provider, ""))
    config.set_ai_route(body.feature, body.provider, model if body.provider else "")
    ai._model_cache["time"] = 0
    audit.record("settings", target="AI for " + accounts.FEATURE_LABELS[body.feature],
                 detail=accounts.provider_label(body.provider) or "same as the assistant")
    return {"routes": accounts.routes()}


@app.get("/api/ai/models", dependencies=[authed])
async def ai_models():
    # API providers, then the coding-agent CLIs installed on this server
    # (Claude Code, Codex), which run on their own login — see engines.py
    return {"providers": await ai.list_models() + await engines.providers_live(),
            "default": config.get_ai_default()}


# --------------------------------------------------------------- local AI

@app.get("/api/localai/status", dependencies=[authed])
async def localai_status():
    return await localai.status()


@app.post("/api/localai/install", dependencies=[authed])
async def localai_install():
    if not localai.can_install():
        raise HTTPException(400, "Docker is not available to install Ollama here")
    job = localai.start_install_job()
    audit.record("localai_install", detail="Ollama")
    return {"job_id": job.id}


@app.post("/api/localai/connect", dependencies=[authed])
async def localai_connect():
    try:
        base = await localai.connect_existing()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    ai._model_cache["time"] = 0
    audit.record("localai_connect", detail=base or "")
    return await localai.status()


class LocalPullBody(BaseModel):
    model: str


@app.post("/api/localai/pull", dependencies=[authed])
async def localai_pull(body: LocalPullBody):
    if not body.model.strip():
        raise HTTPException(400, "no model given")
    job = localai.start_pull_job(body.model.strip())
    audit.record("localai_pull", target=body.model.strip())
    ai._model_cache["time"] = 0
    return {"job_id": job.id}


@app.post("/api/localai/delete", dependencies=[authed])
async def localai_delete(body: LocalPullBody):
    try:
        ok = await localai.delete_model(body.model.strip())
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(400, "delete failed")
    audit.record("localai_delete", target=body.model.strip(), status="warn")
    ai._model_cache["time"] = 0
    return {"ok": True}


class LocalBaseBody(BaseModel):
    base: str = ""


@app.post("/api/localai/base", dependencies=[authed])
async def localai_base(body: LocalBaseBody):
    config.set_ollama_base(body.base)
    localai._cache["time"] = 0
    ai._model_cache["time"] = 0
    return await localai.status()


@app.get("/api/ai/usage", dependencies=[authed])
async def ai_usage():
    return ai.usage_summary()


@app.get("/api/ai/usage/series", dependencies=[authed])
async def ai_usage_series(days: int = 30):
    return ai.usage_series(days)


class AIConfigBody(BaseModel):
    keys: dict[str, str] = {}          # provider -> key ("" keep, "-" clear)
    default_provider: str = ""
    default_model: str = ""


@app.post("/api/settings/ai", dependencies=[authed])
async def set_ai(body: AIConfigBody):
    for prov in body.keys:
        if prov not in config.PROVIDERS:
            raise HTTPException(400, f"unknown provider {prov}")
    config.set_keys(body.keys)
    if body.default_provider:
        # an installed coding-agent CLI can be the default for new chats too
        if body.default_provider not in config.PROVIDERS \
                and not engines.installed(body.default_provider):
            raise HTTPException(400, "unknown provider")
        config.set_ai_default(body.default_provider, body.default_model)
    ai._model_cache["time"] = 0
    return {"ok": True, "configured": config.configured_providers()}


class PasswordBody(BaseModel):
    current: str
    new: str


@app.post("/api/settings/password", dependencies=[authed])
async def change_password(body: PasswordBody):
    if not auth.verify_password(body.current):
        raise HTTPException(403, "Current password is wrong")
    if len(body.new) < 8:
        raise HTTPException(400, "New password must have at least 8 characters")
    auth.set_password(body.new)     # also bumps generation -> other sessions out
    audit.record("password_change", detail="other sessions revoked")
    # hand the caller a fresh token so they stay signed in
    return {"ok": True, "token": auth.issue_token()}


# ------------------------------------------------------- 2FA & sessions

@app.get("/api/settings/2fa/setup", dependencies=[authed])
async def totp_setup():
    secret = auth.new_totp_secret()
    uri = auth.provisioning_uri(secret)
    return {"secret": secret, "uri": uri, "svg": auth.qr_svg(uri)}


class TotpEnableBody(BaseModel):
    secret: str
    code: str


@app.post("/api/settings/2fa/enable", dependencies=[authed])
async def totp_enable(body: TotpEnableBody):
    if auth.totp_enabled():
        raise HTTPException(400, "2FA is already enabled")
    if not auth.enable_totp(body.secret.strip(), body.code):
        raise HTTPException(400, "That code didn't match — check your authenticator and try again")
    config.set_exposure_ack(False)  # risk resolved: stop the exposure warning
    audit.record("2fa_enable")
    return {"ok": True}


@app.post("/api/settings/exposure/ack", dependencies=[authed])
async def exposure_ack():
    """Record that the admin has knowingly chosen to run this root gateway on a
    public host without 2FA. Dismisses the warning; enabling 2FA re-arms it."""
    config.set_exposure_ack(True)
    audit.record("exposure_ack", status="warn",
                 detail="running publicly exposed without 2FA, acknowledged")
    return {"ok": True}


class TotpDisableBody(BaseModel):
    password: str
    code: str = ""


@app.post("/api/settings/2fa/disable", dependencies=[authed])
async def totp_disable(body: TotpDisableBody):
    if not auth.verify_password(body.password):
        raise HTTPException(403, "Password is wrong")
    if not auth.verify_totp(body.code):
        raise HTTPException(403, "Enter a valid current 2FA code to disable it")
    auth.disable_totp()
    audit.record("2fa_disable", status="warn")
    return {"ok": True}


@app.post("/api/settings/sessions/revoke", dependencies=[authed])
async def revoke_sessions():
    token = auth.revoke_all_sessions()
    audit.record("logout_all", detail="all other devices signed out")
    return {"ok": True, "token": token}


# ------------------------------------------------------- single sign-on

APP_SSO_CALLBACK = "pocketadm://sso"


def _sso_back(code: str = "", error: str = "", app: bool = False) -> RedirectResponse:
    """Back to the app after a sign-in attempt. The browser-binding cookie is
    spent either way. The iOS app signs in through ASWebAuthenticationSession,
    which catches the pocketadm:// redirect itself — no other app can receive
    it, and the one-time code still has to be claimed within 60 seconds."""
    if app:
        query = urlencode({"code": code} if code else {"error": error[:300]})
        resp = RedirectResponse(APP_SSO_CALLBACK + "?" + query, status_code=302)
        resp.delete_cookie(oidc.COOKIE, path="/api/auth/oidc")
        return resp
    query = urlencode({"sso": code} if code else {"sso_error": error[:300]})
    resp = RedirectResponse("/?" + query, status_code=302)
    resp.delete_cookie(oidc.COOKIE, path="/api/auth/oidc")
    return resp


@app.get("/api/auth/oidc/start")
async def oidc_start(client: str = ""):
    cfg = oidc.get_config()
    if not cfg:
        raise HTTPException(404, "Single sign-on is not set up")
    app_flow = client == "app"
    try:
        url, browser = await oidc.begin(app=app_flow)
    except oidc.SSOError as e:
        return _sso_back(error=str(e), app=app_flow)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(oidc.COOKIE, browser, max_age=oidc.FLOW_TTL, path="/api/auth/oidc",
                    httponly=True, samesite="lax",
                    secure=cfg["redirect_uri"].startswith("https://"))
    return resp


@app.get("/api/auth/oidc/callback")
async def oidc_callback(request: Request, state: str = "", code: str = "",
                        error: str = "", error_description: str = ""):
    # No failure counting here: state and code are unguessable, and behind a
    # reverse proxy every client shares one address, so a few aborted SSO
    # attempts would otherwise lock the password login too.
    ip = request.client.host if request.client else "?"
    app_flow = oidc.flow_is_app(state)
    if error:
        msg = (error_description or error)[:160]
        audit.record("login_failed", target=ip, status="warn", detail=f"SSO: {msg}")
        return _sso_back(error=f"The provider stopped the sign-in: {msg}", app=app_flow)
    try:
        who = await oidc.complete(state, code, request.cookies.get(oidc.COOKIE, ""))
    except oidc.SSOError as e:
        audit.record("login_failed", target=ip, status="warn", detail=f"SSO: {e}")
        return _sso_back(error=str(e), app=app_flow)
    return _sso_back(code=oidc.new_login_code(who), app=app_flow)


class SSOClaimBody(BaseModel):
    code: str


@app.post("/api/auth/oidc/claim")
async def oidc_claim(body: SSOClaimBody, request: Request):
    ip = request.client.host if request.client else "?"
    auth.rate_limit(ip)
    who = oidc.claim_login_code(body.code)
    if not who:
        auth.record_failure(ip)
        raise HTTPException(401, "Sign-in code is invalid or expired")
    audit.record("login", target=ip, detail=f"SSO as {who['user']} ({who['matched']})")
    return {"token": auth.issue_token()}


@app.get("/api/settings/sso", dependencies=[authed])
async def sso_settings():
    cfg = config.get_oidc()
    return {
        "configured": oidc.get_config() is not None,
        "issuer": cfg.get("issuer", ""),
        "client_id": cfg.get("client_id", ""),
        "secret_set": bool(cfg.get("client_secret")),   # the secret never leaves
        "allowed": cfg.get("allowed", []),
        "label": cfg.get("label", ""),
        "redirect_uri": cfg.get("redirect_uri", ""),
        "callback_path": oidc.CALLBACK_PATH,
    }


class SSOSettingsBody(BaseModel):
    issuer: str
    client_id: str
    client_secret: str = ""          # empty = keep the stored one
    allowed: list[str] | str
    label: str = ""
    redirect_uri: str


@app.put("/api/settings/sso", dependencies=[authed])
async def sso_settings_save(body: SSOSettingsBody):
    secret = body.client_secret.strip() or config.get_oidc().get("client_secret", "")
    try:
        redirect_uri = oidc.check_redirect_uri(body.redirect_uri)
        allowed = oidc.parse_allowed(body.allowed)
        if not body.client_id.strip():
            raise oidc.SSOError("Client ID is required")
        if not secret:
            raise oidc.SSOError("Client secret is required")
        if not allowed:
            raise oidc.SSOError("Name at least one group or user that may sign in")
        doc = await oidc.discover(body.issuer, fresh=True)
    except oidc.SSOError as e:
        raise HTTPException(400, str(e))
    config.set_oidc({
        "issuer": doc["issuer"],
        "client_id": body.client_id.strip(),
        "client_secret": secret,
        "allowed": allowed,
        "label": body.label.strip()[:40] or "SSO",
        "redirect_uri": redirect_uri,
    })
    audit.record("sso_configure", target=doc["issuer"], detail="allowed: " + ", ".join(allowed))
    return {"ok": True}


@app.delete("/api/settings/sso", dependencies=[authed])
async def sso_settings_remove():
    config.set_oidc(None)
    audit.record("sso_remove", status="warn")
    return {"ok": True}


@app.get("/api/audit", dependencies=[authed])
async def audit_log(limit: int = 80, action: str = "", source: str = "", before: float = 0):
    return audit.recent(min(limit, 300), action, source, before)


@app.get("/api/activity", dependencies=[authed])
async def activity_feed(limit: int = 100, before: float = 0, category: str = ""):
    """Everything that happened on the server — Docker, SSH, sudo, apt, the
    kernel, systemd, the internet connection and PocketADM itself — newest
    first, paginated with `before`."""
    cats = [c for c in category.split(",") if c]
    return activity.recent(min(max(limit, 1), 300), before, cats)


@app.get("/api/activity/stream", dependencies=[authed])
async def activity_stream(category: str = ""):
    """The same feed, live (Server-Sent Events)."""
    cats = [c for c in category.split(",") if c]
    return StreamingResponse(activity.stream(cats), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ServerBody(BaseModel):
    name: str = ""


@app.post("/api/settings/server", dependencies=[authed])
async def set_server(body: ServerBody):
    config.set_server_name(body.name)
    return {"ok": True, "server_name": config.get_server_name() or sysinfo.hostname()}


# ------------------------------------------------ server identity & users

@app.get("/api/server/identity", dependencies=[authed])
async def server_identity():
    ident = await asyncio.to_thread(hostuser.identity)
    ident["display_name"] = config.get_server_name()
    return ident


@app.get("/api/server/users", dependencies=[authed])
async def server_users():
    ident = await asyncio.to_thread(hostuser.identity)
    users = await asyncio.to_thread(hostuser.list_users)
    return {"users": users, "identity": ident,
            "can_manage": ident["host_access"], "reason": ident["manage_reason"]}


class UserPasswordBody(BaseModel):
    password: str


@app.post("/api/server/users/{name}/password", dependencies=[authed])
async def user_set_password(name: str, body: UserPasswordBody):
    try:
        msg = await hostuser.set_password(name, body.password)
    except (ValueError, RuntimeError) as e:
        audit.record("user_password", target=name, status="error", detail=str(e)[:160])
        raise HTTPException(400, str(e))
    audit.record("user_password", target=name, detail="password changed")
    return {"ok": True, "message": msg}


class UserFlagBody(BaseModel):
    value: bool


@app.post("/api/server/users/{name}/lock", dependencies=[authed])
async def user_set_lock(name: str, body: UserFlagBody):
    try:
        msg = await hostuser.set_locked(name, body.value)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    audit.record("user_lock", target=name,
                 detail="locked" if body.value else "unlocked",
                 status="warn" if body.value else "ok")
    return {"ok": True, "message": msg}


@app.post("/api/server/users/{name}/admin", dependencies=[authed])
async def user_set_admin(name: str, body: UserFlagBody):
    try:
        msg = await hostuser.set_admin(name, body.value)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    audit.record("user_admin", target=name,
                 detail="granted admin" if body.value else "revoked admin",
                 status="warn")
    return {"ok": True, "message": msg}


class UserCreateBody(BaseModel):
    name: str
    password: str = ""
    admin: bool = False


@app.post("/api/server/users", dependencies=[authed])
async def user_create(body: UserCreateBody):
    try:
        msg = await hostuser.create_user(body.name, body.password, body.admin)
    except (ValueError, RuntimeError) as e:
        audit.record("user_create", target=body.name, status="error", detail=str(e)[:160])
        raise HTTPException(400, str(e))
    audit.record("user_create", target=body.name,
                 detail="admin" if body.admin else "standard user")
    return {"ok": True, "message": msg}


@app.post("/api/settings/onboarded", dependencies=[authed])
async def set_onboarded():
    config.set_onboarded()
    return {"ok": True}


# ---------------------------------------------------------------- chats

@app.get("/api/chats", dependencies=[authed])
async def chats_index(q: str = ""):
    rows = chats.list_chats(q)
    # a chat the agent is still working on, or waiting in for an OK
    for row in rows:
        live = sessions.manager.get(row["id"])
        row["running"] = bool(live and live.running)
        row["waiting"] = bool(live and (live.pending or live.paused))
    return {"chats": rows}


class PinBody(BaseModel):
    pinned: bool = True


@app.post("/api/chats/{chat_id}/pin", dependencies=[authed])
async def chat_pin(chat_id: str, body: PinBody):
    if not chats.set_pinned(chat_id, body.pinned):
        raise HTTPException(404, "no such chat")
    return {"ok": True}


class ChatIdsBody(BaseModel):
    ids: list[str]


@app.post("/api/chats/delete", dependencies=[authed])
async def chats_delete_many(body: ChatIdsBody):
    for chat_id in body.ids[:200]:
        if sessions.manager.get(chat_id) and sessions.manager.get(chat_id).running:
            continue            # a chat that is working right now is not deleted under it
        chats.delete(chat_id)
    return {"ok": True}


@app.get("/api/chats/{chat_id}/export", dependencies=[authed])
async def chat_export(chat_id: str):
    text = chats.export_markdown(chat_id)
    if text is None:
        raise HTTPException(404, "no such chat")
    return JSONResponse({"markdown": text})


class ArchiveBody(BaseModel):
    archived: bool = True


@app.post("/api/chats/{chat_id}/archive", dependencies=[authed])
async def chat_archive(chat_id: str, body: ArchiveBody):
    if not chats.set_archived(chat_id, body.archived):
        raise HTTPException(404, "no such chat")
    return {"ok": True}


class RenameBody(BaseModel):
    title: str


@app.post("/api/chats/{chat_id}/rename", dependencies=[authed])
async def chat_rename(chat_id: str, body: RenameBody):
    if not chats.rename(chat_id, body.title):
        raise HTTPException(404, "no such chat")
    # keep a live session (and every device watching it) in sync
    live = sessions.manager.get(chat_id)
    if live:
        live.chat["title"] = body.title.strip()[:80] or chats.DEFAULT_TITLE
        await live.broadcast(type="chat_meta", id=chat_id,
                             title=live.chat["title"], live=False)
    return {"ok": True, "title": body.title.strip()[:80]}


@app.delete("/api/chats/{chat_id}", dependencies=[authed])
async def chat_delete(chat_id: str):
    chats.delete(chat_id)
    return {"ok": True}


# ------------------------------------------------- sentinel / notifications

@app.get("/api/notifications", dependencies=[authed])
async def notifications_index():
    return agents.notifications()


@app.post("/api/notifications/seen", dependencies=[authed])
async def notifications_seen():
    agents.mark_seen()
    return {"ok": True}


class FeedbackBody(BaseModel):
    helpful: bool


@app.post("/api/notifications/{notif_id}/feedback", dependencies=[authed])
async def notification_feedback(notif_id: str, body: FeedbackBody):
    """"Helpful" / "not helpful" on a watch message: the watch reads it next time."""
    watch.feedback(notif_id, body.helpful)
    return {"ok": True}


@app.delete("/api/notifications/{notif_id}", dependencies=[authed])
async def notification_delete(notif_id: str):
    if not agents.delete_notification(notif_id):
        raise HTTPException(404, "no such alert")
    return {"ok": True}


# ------------------------------------------------------------------ the watch

@app.get("/api/watch", dependencies=[authed])
async def watch_status():
    """The background watch: its settings, which AI it runs on, what it did."""
    if config.DEMO:
        return demodata.watch_status()
    return watch.status()


class WatchBody(BaseModel):
    changes: dict


@app.post("/api/watch", dependencies=[authed])
async def watch_save(body: WatchBody):
    before = watch.settings()["enabled"]
    watch.update_settings(body.changes)
    after = watch.settings()["enabled"]
    audit.record("watch_save", detail=("turned on" if after and not before else
                                       "turned off" if before and not after else "settings"))
    return watch.status()


class WatchRunBody(BaseModel):
    kind: str = "test"


@app.post("/api/watch/run", dependencies=[authed])
async def watch_run(body: WatchRunBody):
    """Look now. Runs in the background; the app polls /api/watch and the
    message arrives in the alerts."""
    if body.kind not in ("test", "observe"):
        raise HTTPException(400, "bad kind")
    route = config.get_ai_route("watch")
    if not route.get("provider") or not accounts.usable(route["provider"]):
        raise HTTPException(400, "Connect an AI for the watch first (More → AI accounts).")
    asyncio.ensure_future(watch.run(body.kind))
    audit.record("watch_run", detail=body.kind)
    return {"started": True}


class WatchPauseBody(BaseModel):
    minutes: int = 0


@app.post("/api/watch/pause", dependencies=[authed])
async def watch_pause(body: WatchPauseBody):
    return watch.pause(min(max(body.minutes, 0), 60 * 24 * 14))


class WatchMuteBody(BaseModel):
    topic: str
    hours: float = 24 * 7
    note: str = ""


@app.post("/api/watch/mute", dependencies=[authed])
async def watch_mute(body: WatchMuteBody):
    return watch.mute(body.topic, body.hours, body.note)


@app.post("/api/watch/test-delivery", dependencies=[authed])
async def watch_test_delivery():
    """A test message to ntfy and Matrix, to see that the setup works."""
    sent = await watch.push_external(watch.settings(), "Test message",
                                     "This is a test from PocketADM's watch. If you can read this, "
                                     "messages will reach you here.", "critical")
    if not sent:
        raise HTTPException(400, "Nothing is set up to receive messages yet, or it did not accept them.")
    return {"sent": sent}


@app.post("/api/watch/memory/clear", dependencies=[authed])
async def watch_forget():
    return watch.forget_memory()


# ------------------------------------------------------------------ the watch's channel

def _channel_status() -> dict:
    st = demodata.watch_status() if config.DEMO else watch.status()
    return {"enabled": st["settings"]["enabled"], "paused": st["paused"],
            "running": st["running"], "quiet_now": st["quiet_now"],
            "route": st["route"], "next_round": st["next_round"],
            "last_round": st["last_round"]}


@app.get("/api/watch/channel", dependencies=[authed])
async def watch_channel(after: float = 0, before: float = 0, limit: int = 60):
    """The conversation with the watch, oldest first: what it wrote on its own,
    what you asked it, and its answers."""
    if config.DEMO:
        demodata.seed_channel()
    page = channel.page(after, before, min(max(limit, 1), 200))
    page["status"] = _channel_status()
    return page


class ChannelChatBody(BaseModel):
    text: str


@app.post("/api/watch/chat", dependencies=[authed])
async def watch_chat(body: ChannelChatBody):
    """Write to the watch. The answer arrives in the channel (and as a push
    notification when the phone is locked)."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty message")
    msg = channel.add("user", text[:2000], kind="chat")
    asyncio.ensure_future(watch.answer())
    return {"message": msg, "replying": True}


class ChannelReadBody(BaseModel):
    t: float = 0


@app.post("/api/watch/channel/read", dependencies=[authed])
async def watch_channel_read(body: ChannelReadBody):
    channel.mark_read(body.t or None)
    return {"unread": channel.unread()}


class ChannelFeedbackBody(BaseModel):
    helpful: bool


@app.post("/api/watch/channel/{msg_id}/feedback", dependencies=[authed])
async def watch_channel_feedback(msg_id: str, body: ChannelFeedbackBody):
    msg = channel.get(msg_id)
    if not msg:
        raise HTTPException(404, "no such message")
    channel.update(msg_id, feedback="helpful" if body.helpful else "not_helpful")
    watch.feedback(msg.get("notification") or msg_id, body.helpful, topic=msg.get("topic", ""))
    return {"ok": True}


@app.delete("/api/watch/channel/{msg_id}", dependencies=[authed])
async def watch_channel_delete(msg_id: str):
    msg = channel.get(msg_id)
    if not channel.delete(msg_id):
        raise HTTPException(404, "no such message")
    if msg and msg.get("notification"):
        agents.delete_notification(msg["notification"])
    return {"ok": True}


# ------------------------------------------------------------------ push to phones

@app.get("/api/push", dependencies=[authed])
async def push_status():
    return push.status()


class PushDeviceBody(BaseModel):
    relay_id: str
    name: str = ""
    platform: str = "ios"
    min: str = "info"
    assistant: bool = True
    preview: bool = True


@app.post("/api/push/devices", dependencies=[authed])
async def push_register(body: PushDeviceBody):
    """A phone that wants push notifications from this server (its relay id,
    see push.py)."""
    try:
        device = push.register(body.relay_id, body.name, body.platform, body.min,
                               body.assistant, body.preview)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return device


class PushDeviceUpdateBody(BaseModel):
    min: str | None = None
    assistant: bool | None = None
    preview: bool | None = None
    name: str | None = None


@app.patch("/api/push/devices/{device_id}", dependencies=[authed])
async def push_update(device_id: str, body: PushDeviceUpdateBody):
    device = push.update(device_id, **body.model_dump())
    if device is None:
        raise HTTPException(404, "no such device")
    return device


@app.delete("/api/push/devices/{device_id}", dependencies=[authed])
async def push_remove(device_id: str):
    if not push.remove(device_id):
        raise HTTPException(404, "no such device")
    return {"ok": True}


@app.post("/api/push/test", dependencies=[authed])
async def push_test():
    result = await push.send("Test notification",
                             "Push works: messages from the watch and the assistant reach this phone.",
                             kind="watch", importance="important", thread="watch")
    if not result["sent"]:
        errors = sorted({d["last_error"] for d in push.devices() if d["last_error"]})
        raise HTTPException(502, "Nothing was delivered" + (f": {', '.join(errors)}" if errors else
                                                            " — no phone is registered."))
    return result


@app.get("/api/agents/loops", dependencies=[authed])
async def loops_index():
    return {"loops": agents.get_loops(),
            "presets": {k: {kk: v[kk] for kk in ("name", "icon", "interval_min", "desc")}
                        for k, v in agents.PRESETS.items()}}


class LoopsBody(BaseModel):
    loops: list[dict]


@app.post("/api/agents/loops", dependencies=[authed])
async def loops_save(body: LoopsBody):
    loops = agents.save_loops(body.loops)
    audit.record("loop_save", detail=f"{sum(l['enabled'] for l in loops)} enabled")
    return {"loops": loops}


@app.post("/api/agents/loops/{loop_id}/run", dependencies=[authed])
async def loop_run(loop_id: str):
    loop = next((lp for lp in agents.get_loops() if lp["id"] == loop_id), None)
    if not loop:
        raise HTTPException(404, "no such loop")
    asyncio.ensure_future(agents.run_loop(loop, trigger="manual"))
    return {"started": True}


# --------------------------------------------------------- integrations

@app.get("/api/integrations", dependencies=[authed])
async def integrations_index():
    return {"integrations": integrations.list_public(),
            "types": {k: {kk: v[kk] for kk in ("label", "base_url", "hint", "docs")}
                      for k, v in integrations.TYPES.items()}}


class IntegrationBody(BaseModel):
    name: str
    type: str = "generic"
    secret: str = ""
    base_url: str = ""
    auth_header_name: str = ""
    note: str = ""
    enabled: bool = True


@app.post("/api/integrations", dependencies=[authed])
async def integration_save(body: IntegrationBody):
    try:
        integrations.save(body.name, body.type, body.secret, body.base_url,
                          body.auth_header_name, body.note, body.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("integration_save", target=body.name, detail=body.type)
    return {"ok": True, "integrations": integrations.list_public()}


class IntegrationEnableBody(BaseModel):
    enabled: bool


@app.post("/api/integrations/{name}/enabled", dependencies=[authed])
async def integration_set_enabled(name: str, body: IntegrationEnableBody):
    if not integrations.set_enabled(name, body.enabled):
        raise HTTPException(404, "no such integration")
    audit.record("integration_save", target=name,
                 detail="enabled" if body.enabled else "disabled")
    return {"ok": True, "integrations": integrations.list_public()}


@app.delete("/api/integrations/{name}", dependencies=[authed])
async def integration_delete(name: str):
    integrations.remove(name)
    audit.record("integration_delete", target=name)
    return {"ok": True}


@app.post("/api/integrations/{name}/test", dependencies=[authed])
async def integration_test(name: str):
    try:
        return await integrations.test(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


# --------------------------------------------------------------- agent

@app.get("/api/agent/memory", dependencies=[authed])
async def get_agent_memory():
    return {"memory": ai.read_memory()}


class MemoryBody(BaseModel):
    memory: str


@app.post("/api/agent/memory", dependencies=[authed])
async def set_agent_memory(body: MemoryBody):
    ai.save_memory(body.memory)
    return {"ok": True}


# ------------------------------------------------------- notes (0.26)

@app.get("/api/agent/notes", dependencies=[authed])
async def agent_notes():
    return memory.overview()


class NoteBody(BaseModel):
    text: str = ""
    topic: str = ""
    subject: str | None = None
    pinned: bool | None = None


@app.post("/api/agent/notes", dependencies=[authed])
async def add_agent_note(body: NoteBody):
    try:
        note, what = memory.add(body.text, topic=body.topic, subject=body.subject or "",
                                source="you", pinned=bool(body.pinned))
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("agent_note", target=note["id"], detail=what)
    return {**memory.overview(), "note": note, "result": what}


@app.patch("/api/agent/notes/{note_id}", dependencies=[authed])
async def edit_agent_note(note_id: str, body: NoteBody):
    try:
        note = memory.edit(note_id, text=body.text or None, topic=body.topic or None,
                           pinned=body.pinned, subject=body.subject)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not note:
        raise HTTPException(404, "No such note")
    return {**memory.overview(), "note": note}


@app.delete("/api/agent/notes/{note_id}", dependencies=[authed])
async def delete_agent_note(note_id: str):
    if not memory.forget(note_id):
        raise HTTPException(404, "No such note")
    audit.record("agent_note_delete", target=note_id)
    return memory.overview()


@app.post("/api/agent/notes/tidy", dependencies=[authed])
async def tidy_agent_notes():
    result = await memory.tidy()
    audit.record("agent_notes_tidy", detail=f"{result['before']} → {result['after']} notes")
    return {**memory.overview(), "tidy": result}


@app.post("/api/agent/notes/undo", dependencies=[authed])
async def undo_agent_notes():
    if not memory.undo():
        raise HTTPException(409, "Nothing to undo")
    return memory.overview()


@app.post("/api/agent/notes/clear", dependencies=[authed])
async def clear_agent_notes():
    memory.clear()
    audit.record("agent_notes_clear")
    return memory.overview()


# ------------------------------------------------------- server inventory (0.26)

@app.get("/api/inventory", dependencies=[authed])
async def server_inventory(refresh: bool = False):
    """Everything on the server at a glance: stacks, domains, systemd units and
    timers, cron jobs, drives — discovered, not configured."""
    return await inventory.build(force=refresh)


@app.get("/api/system/units/{unit}", dependencies=[authed])
async def system_unit(unit: str, lines: int = 120):
    try:
        return await inventory.unit_detail(unit, lines)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/system/units/{unit}/{action}", dependencies=[authed])
async def system_unit_action(unit: str, action: str):
    if not inventory.valid_unit(unit) or action not in inventory.UNIT_ACTIONS:
        raise HTTPException(400, "Unknown unit or action")
    result = await inventory.unit_action(unit, action)
    audit.record("unit_" + action, target=unit, status="ok" if result["ok"] else "error",
                 detail=result["output"][:200])
    if not result["ok"]:
        raise HTTPException(500, result["output"] or f"systemctl {action} failed")
    return {**result, **(await inventory.unit_detail(unit, 60))}


@app.post("/api/chat/upload", dependencies=[authed])
async def chat_upload(request: Request, name: str):
    """A file attached in the assistant chat (the body is the file). Stored
    where every assistant can open it; see uploads.py."""
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > uploads.MAX_BYTES:
            raise HTTPException(413, f"Files can be up to {uploads.MAX_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise HTTPException(400, "The file is empty.")
    try:
        result = await asyncio.to_thread(uploads.save, name, bytes(data))
    except (OSError, ValueError) as e:
        raise HTTPException(400, str(e))
    audit.record("chat_upload", target=result["path"], detail=f"{result['size']} bytes")
    return result


@app.get("/api/ai/suggestions", dependencies=[authed])
async def ai_suggestions():
    """What to ask the assistant now, from the server's state (no AI call)."""
    return {"suggestions": await suggestions.build()}


@app.get("/api/agent/instructions", dependencies=[authed])
async def get_agent_instructions():
    return {"instructions": config.get_custom_instructions()}


class InstructionsBody(BaseModel):
    instructions: str


@app.post("/api/agent/instructions", dependencies=[authed])
async def set_agent_instructions(body: InstructionsBody):
    config.set_custom_instructions(body.instructions)
    return {"ok": True, "instructions": config.get_custom_instructions()}


@app.get("/api/agent/tools", dependencies=[authed])
async def agent_tools():
    return {"tools": ai.tool_docs()}


@app.get("/api/agent/autonomy", dependencies=[authed])
async def get_autonomy():
    return {**config.get_autonomy(), "ntfy_url": config.get_ntfy_url(),
            "autoread": config.get_autoread()}


class AutonomyBody(BaseModel):
    pause_mode: str | None = None
    steps: int | None = None
    minutes: int | None = None
    push: bool | None = None
    ntfy_url: str | None = None
    autoread: bool | None = None


@app.post("/api/agent/autonomy", dependencies=[authed])
async def set_autonomy(body: AutonomyBody):
    if body.ntfy_url is not None:
        config.set_ntfy_url(body.ntfy_url)
    if body.autoread is not None:
        config.set_autoread(body.autoread)
        audit.record("agent_autoread", detail="on" if body.autoread else "off")
    au = config.set_autonomy(pause_mode=body.pause_mode or "", steps=body.steps,
                             minutes=body.minutes, push=body.push)
    return {**au, "ntfy_url": config.get_ntfy_url(), "autoread": config.get_autoread()}


# ---- server knowledge: live map + skills ----

@app.get("/api/agent/servermap", dependencies=[authed])
async def get_servermap(refresh: bool = False):
    text = await servermap.get(force=refresh)
    return {"text": text, "enabled": config.get_servermap_enabled()}


class ServermapBody(BaseModel):
    enabled: bool


@app.post("/api/agent/servermap", dependencies=[authed])
async def set_servermap(body: ServermapBody):
    config.set_servermap_enabled(body.enabled)
    return {"enabled": config.get_servermap_enabled()}


@app.get("/api/skills", dependencies=[authed])
async def list_skills():
    return {"skills": skills.list_skills()}


@app.get("/api/skills/{name}", dependencies=[authed])
async def read_skill(name: str):
    try:
        return {"name": name, "content": skills.read(name)}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


class SkillBody(BaseModel):
    content: str


@app.put("/api/skills/{name}", dependencies=[authed])
async def save_skill(name: str, body: SkillBody):
    try:
        slug = skills.save(name, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("skill_save", target=slug)
    return {"ok": True, "name": slug}


@app.delete("/api/skills/{name}", dependencies=[authed])
async def delete_skill(name: str):
    try:
        skills.delete(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("skill_delete", target=name)
    return {"ok": True}


@app.get("/api/permissions", dependencies=[authed])
async def list_permissions():
    return {"items": permissions.list_all(), "open": permissions.open_count()}


class PermActionBody(BaseModel):
    action: str   # "dismiss" | "resolve"


class PermBulkBody(BaseModel):
    ids: list[str]
    action: str = "dismiss"


@app.post("/api/permissions/bulk", dependencies=[authed])
async def act_permissions_bulk(body: PermBulkBody):
    status = "dismissed" if body.action == "dismiss" else "resolved"
    n = permissions.set_status_many(body.ids[:50], status)
    audit.record("permission_" + status, target=f"{n} requests")
    return {"ok": True, "changed": n, "open": permissions.open_count()}


@app.post("/api/permissions/{req_id}", dependencies=[authed])
async def act_permission(req_id: str, body: PermActionBody):
    status = "dismissed" if body.action == "dismiss" else "resolved"
    if not permissions.set_status(req_id, status):
        raise HTTPException(404, "no such request")
    audit.record("permission_" + status, target=req_id)
    return {"ok": True, "open": permissions.open_count()}


class ToolToggleBody(BaseModel):
    enabled: bool


@app.post("/api/agent/tools/{name}", dependencies=[authed])
async def toggle_agent_tool(name: str, body: ToolToggleBody):
    if name not in {t["name"] for t in ai.TOOLS}:
        raise HTTPException(404, "no such tool")
    config.set_tool_enabled(name, body.enabled)
    return {"ok": True, "tools": ai.tool_docs()}


class WorkspacesBody(BaseModel):
    paths: list[str]


@app.post("/api/settings/workspaces", dependencies=[authed])
async def set_workspaces(body: WorkspacesBody):
    config.set_workspaces(body.paths[:12])
    return {"ok": True, "workspaces": config.get_workspaces()}


class DefaultWorkspaceBody(BaseModel):
    path: str


@app.post("/api/settings/default-workspace", dependencies=[authed])
async def set_default_workspace(body: DefaultWorkspaceBody):
    config.set_default_workspace(body.path)
    return {"ok": True, "default_workspace": config.get_default_workspace()}


# -------------------------------------------------------------- reports

@app.get("/api/reports", dependencies=[authed])
async def reports_index():
    return {"reports": reports.list_reports(), "config": config.get_report_config()}


@app.get("/api/reports/latest", dependencies=[authed])
async def reports_latest():
    r = reports.latest_report()
    if not r:
        raise HTTPException(404, "no reports yet")
    return r


@app.get("/api/reports/{name}", dependencies=[authed])
async def reports_get(name: str):
    r = reports.get_report(name)
    if not r:
        raise HTTPException(404, "no such report")
    return r


@app.post("/api/reports/run", dependencies=[authed])
async def reports_run():
    return await reports.run_report(trigger="manual")


class AnalyzeBody(BaseModel):
    name: str = ""      # report file stem; empty = latest
    lang: str = ""


@app.post("/api/reports/analyze", dependencies=[authed])
async def reports_analyze(body: AnalyzeBody):
    report = reports.get_report(body.name) if body.name else reports.latest_report()
    if not report:
        raise HTTPException(404, "no report to analyze")
    try:
        return {"analysis": await reports.analyze_report(report, body.lang)}
    except Exception as e:
        raise HTTPException(500, str(e))


class MuteBody(BaseModel):
    muted: bool = True
    note: str = ""


@app.post("/api/reports/checks/{check_id}/mute", dependencies=[authed])
async def reports_mute(check_id: str, body: MuteBody):
    """Accept a finding on purpose ("password SSH is fine, it is LAN only"):
    it stops counting against the score and moves to Accepted."""
    if not check_id or len(check_id) > 80:
        raise HTTPException(400, "bad check id")
    reports.set_muted(check_id, body.muted, body.note)
    audit.record("settings", target="health check " + check_id,
                 detail="accepted" if body.muted else "watched again")
    return reports.latest_report() or {"ok": True}


@app.post("/api/maintenance/prune-images", dependencies=[authed])
async def maintenance_prune_images():
    """Remove images no container uses, as a job with a log. Images of
    stopped containers stay: their container still refers to them."""
    async def work(job: jobs.Job) -> None:
        job.log("Looking for images no container uses …")
        r = await dockerapi.client().post("/images/prune",
                                          params={"filters": json.dumps({"dangling": ["false"]})},
                                          timeout=600)
        if r.status_code >= 400:
            raise RuntimeError(r.text[:300])
        data = r.json()
        removed = len(data.get("ImagesDeleted") or [])
        freed = data.get("SpaceReclaimed") or 0
        job.log(f"Removed {removed} image layers, freed {freed / 1e9:.2f} GB.")
        audit.record("maintenance", target="images", detail=f"freed {freed / 1e9:.2f} GB")
        job.finish(True, "✓ Done")

    job = jobs.start("Remove unused images", "maintenance", work)
    return {"job_id": job.id}


class ReportConfigBody(BaseModel):
    interval_min: int = 360
    auto: bool = True


@app.post("/api/reports/config", dependencies=[authed])
async def reports_config(body: ReportConfigBody):
    config.set_report_config(body.interval_min, body.auto)
    return {"ok": True}


# -------------------------------------------------------------- backups

@app.get("/api/backup/export", dependencies=[authed])
async def backup_export():
    """Download PocketADM's state (settings, keys, chats, memory, app compose
    files, audit log) as a tar.gz. Sensitive — contains keys and secrets."""
    name, data = await asyncio.to_thread(backups.export_archive)
    audit.record("backup_export", detail=f"{len(data)} bytes")
    return StreamingResponse(iter([data]), media_type="application/gzip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/backup/restore", dependencies=[authed])
async def backup_restore(request: Request):
    """Restore a previously exported backup (raw tar.gz body). Overwrites
    current settings — a restart afterwards is recommended."""
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "backup too large")
    try:
        restored = await asyncio.to_thread(backups.restore_archive, data)
    except Exception as e:
        raise HTTPException(400, f"restore failed: {e}")
    audit.record("backup_restore", detail=f"{len(restored)} files")
    return {"ok": True, "files": len(restored),
            "note": "Restored. Restart the PocketADM container to apply everything cleanly."}


# ------------------------------------------------- coding-agent CLIs

@app.get("/api/clis", dependencies=[authed])
async def clis_index():
    return {"clis": await clis.status()}


@app.post("/api/clis/{tool}/install", dependencies=[authed])
async def clis_install(tool: str):
    try:
        job = clis.start_install_job(tool)
    except ValueError:
        raise HTTPException(404, "unknown tool")
    audit.record("cli_install", target=tool)
    return {"job_id": job.id}


# ----------------------------------------------------------- websockets

@app.get("/api/terminal/targets", dependencies=[authed])
async def terminal_targets():
    """Human-readable, grouped list of who/where the terminal can open a shell:
    the PocketADM app box, real host logins (maxaufknax@stream), and each running
    service container. Replaces the flat, confusing dropdown of many identities."""
    groups: list[dict] = []

    server: list[dict] = [{
        "id": "local", "label": "PocketADM app",
        "sub": "the app's own container · docker + host control", "icon": "box",
    }]
    host_ok = hostuser._can_manage()
    if host_ok:
        try:
            ident = await asyncio.to_thread(hostuser.identity)
            host = ident.get("hostname") or "host"
            users = await asyncio.to_thread(hostuser.list_users)
        except Exception:
            host, users = "host", []
        for u in users:
            if u["kind"] != "human" or not u["can_login"]:
                continue
            server.append({
                "id": "host:" + u["name"],
                "label": f'{u["name"]}@{host}',
                "sub": u["role"] + (" · you" if u["is_admin"] and u["name"] != "root" else ""),
                "icon": "shield" if u["is_root"] else ("user-cog" if u["is_admin"] else "user"),
                "host": True,
            })
    groups.append({"label": "This server", "targets": server})

    svc: list[dict] = []
    try:
        result = await dockerapi.list_containers()
        for c in result:
            if c.get("state") != "running":
                continue
            ref = c["name"] if appstore._is_image_id(c["image"]) else c["image"]
            meta = updates.service_meta(ref)
            svc.append({
                "id": "container:" + c["id"],
                "label": meta.get("label") or c["name"],
                "sub": c["name"] + " · " + (c["image"][:40]),
                "icon": meta.get("icon") or "box",
                "container": True,
            })
    except Exception:
        pass
    svc.sort(key=lambda t: t["label"].lower())
    groups.append({"label": "Service containers", "targets": svc})

    return {"groups": groups, "host_shell": host_ok}


@app.get("/api/terminal/sessions", dependencies=[authed])
async def terminal_sessions():
    """Server-side terminal sessions — they keep running when the app closes,
    stream to any number of devices, and replay scrollback on attach."""
    return {"sessions": termsessions.list_meta(), "max_live": termsessions.MAX_LIVE}


class TermSessionBody(BaseModel):
    context: str = "local"
    title: str = ""


@app.post("/api/terminal/sessions", dependencies=[authed])
async def terminal_session_create(body: TermSessionBody):
    try:
        s = termsessions.create(body.context, body.title.strip()[:60])
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("terminal", target=body.context, detail=f"session {s.id}")
    return {"session": s.meta()}


@app.delete("/api/terminal/sessions/{sid}", dependencies=[authed])
async def terminal_session_close(sid: str):
    if not termsessions.close(sid):
        raise HTTPException(404, "no such session")
    audit.record("terminal_kill", target=sid)
    return {"ok": True}


@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket):
    await ws.accept()
    if not await auth.require_auth_ws(ws):
        return
    if config.DEMO:
        # public playground: never spawn a real shell — serve a safe simulation
        await terminal.demo_terminal(ws)
        return
    sid = ws.query_params.get("session", "")
    if sid:
        s = termsessions.get(sid)
        if not s:
            await ws.send_text("\r\n[pocketadm] session-gone\r\n")
            await ws.close()
            return
        await s.attach(ws)
        return
    # legacy path: an ephemeral PTY bound to this one websocket
    ctx = ws.query_params.get("context", "local")
    audit.record("terminal", target=ctx)
    await terminal.handle_terminal(ws, ctx)


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    if not await auth.require_auth_ws(ws):
        return
    # the session (and its agent run) lives independently of this socket, so
    # the work survives a disconnect and streams to every device on the chat
    await sessions.ws_chat(ws)


# --------------------------------------------------------------- static

@app.exception_handler(404)
async def spa_fallback(request: Request, exc):
    if request.url.path.startswith(("/api/", "/ws/")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(config.WEB_DIR / "index.html")

app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
