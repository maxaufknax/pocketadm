"""Helmsman — self-hosted server command center. FastAPI app assembly."""
import asyncio
import json
import os

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agents, ai, appstore, audit, auth, backups, bootstrap, chats,
               clis, config, demodata, dockerapi, hostuser, integrations, jobs,
               localai, metrics, pairing, permissions, reports, servermap,
               sessions, skills, snapshots, sysinfo, terminal, termsessions,
               updates)

app = FastAPI(title="Helmsman", docs_url=None, redoc_url=None)
auth.bootstrap_password()

authed = Depends(auth.require_auth)

# Demo instances are a public read-only playground: every mutation is blocked.
DEMO_ALLOW = {"/api/login", "/api/notifications/seen"}


@app.middleware("http")
async def _demo_guard(request: Request, call_next):
    if config.DEMO and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.url.path not in DEMO_ALLOW:
        return JSONResponse({"detail": "Demo mode — this instance is read-only"},
                            status_code=403)
    return await call_next(request)


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
    else:
        skills.seed_defaults()
        reports.start_scheduler()
        agents.start_scheduler()
        asyncio.ensure_future(appstore.remote_refresher())
        asyncio.ensure_future(localai.reconnect_on_startup())


# ------------------------------------------------------------------ auth

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
    }


# --------------------------------------------------------- device pairing

@app.post("/api/pair/new", dependencies=[authed])
async def pair_new():
    """Mint a one-time pairing code (shown as a QR on the signed-in device).
    Another device scans it and calls /api/pair/claim to get its own token."""
    code, ttl = pairing.new_code()
    audit.record("pair_new", detail="pairing code issued")
    return {"code": code, "ttl": ttl,
            "server_name": config.get_server_name() or sysinfo.hostname()}


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


@app.get("/api/me", dependencies=[authed])
async def me():
    default = config.get_ai_default()
    return {
        "ok": True,
        "version": config.VERSION,
        "demo": config.DEMO,
        "hostname": sysinfo.hostname(),
        "server_name": config.get_server_name() or sysinfo.hostname(),
        "onboarded": config.get_onboarded(),
        "ai_configured": bool(default["provider"]),
        "ai_default": default,
        "ai_providers": config.configured_providers(),
        "workspaces": config.get_workspaces(),
        "default_workspace": config.get_default_workspace(),
        "report_config": config.get_report_config(),
        "totp_enabled": auth.totp_enabled(),
        "can_pair": not config.DEMO,
    }


# ------------------------------------------------------------ dashboard

@app.get("/api/system", dependencies=[authed])
async def system():
    data = await asyncio.to_thread(sysinfo.snapshot)
    data["docker"] = await dockerapi.engine_info() if await dockerapi.available() else None
    last = metrics.latest()
    data["net"] = {"rx": last["rx"], "tx": last["tx"], "ping": last["ping"]} if last else None
    return data


@app.get("/api/containers", dependencies=[authed])
async def containers():
    result = await dockerapi.list_containers()
    for c in result:
        # untagged image IDs carry no service info — fall back to the name
        ref = c["name"] if appstore._is_image_id(c["image"]) else c["image"]
        c["service"] = updates.service_meta(ref)
    return result


@app.get("/api/containers/{cid}/logs", dependencies=[authed])
async def container_logs(cid: str, tail: int = 200):
    return {"logs": await dockerapi.container_logs(cid, min(tail, 2000))}


@app.get("/api/containers/{cid}/stats", dependencies=[authed])
async def container_stats(cid: str):
    return await dockerapi.container_stats(cid)


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


# NOTE: registered AFTER the specific POST routes above — FastAPI matches routes
# in registration order, so this catch-all must not shadow e.g. …/describe.
@app.post("/api/containers/{cid}/{action}", dependencies=[authed])
async def container_action(cid: str, action: str):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "bad action")
    await dockerapi.container_action(cid, action)
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

def _fs_roots() -> list[str]:
    roots: list[str] = []
    for r in config.get_workspaces() + [ai.DEFAULT_WORKDIR]:
        rp = os.path.realpath(r)
        if os.path.isdir(rp) and rp not in roots:
            roots.append(rp)
    return roots


def _within_roots(resolved: str, roots: list[str]) -> bool:
    return any(resolved == r or resolved.startswith(r + os.sep) for r in roots)


# file kinds we can safely preview as text in the Explorer
_TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".conf", ".cfg", ".ini", ".env",
             ".yml", ".yaml", ".json", ".toml", ".xml", ".html", ".htm", ".css",
             ".js", ".ts", ".jsx", ".tsx", ".py", ".sh", ".bash", ".zsh", ".rb",
             ".go", ".rs", ".c", ".h", ".cpp", ".java", ".php", ".sql", ".csv",
             ".service", ".gitignore", ".dockerignore", ".properties"}
_TEXT_NAMES = {"Dockerfile", "docker-compose.yml", "Makefile", "LICENSE",
               "README", ".env", ".gitignore", "requirements.txt"}


def _looks_text(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in _TEXT_EXT or name in _TEXT_NAMES or "." not in name


@app.get("/api/fs", dependencies=[authed])
async def fs_list(path: str = "", files: int = 0):
    """Directory browser — restricted to the configured workspace roots (plus
    the default workdir). With ?files=1 it also returns file entries (name,
    path, size, whether previewable as text) so it can back the Explorer."""
    roots = _fs_roots()
    if not path:
        return {"path": "", "parent": None,
                "dirs": [{"name": r, "path": r} for r in roots],
                "file_entries": [], "files": 0, "roots": roots}
    resolved = os.path.realpath(path)
    if not _within_roots(resolved, roots):
        raise HTTPException(403, "outside allowed workspaces")
    if not os.path.isdir(resolved):
        raise HTTPException(404, "not a directory")
    dirs, file_entries, file_count = [], [], 0
    try:
        with os.scandir(resolved) as it:
            for e in sorted(it, key=lambda e: e.name.lower()):
                if e.name.startswith(".") and e.name not in (".config", ".env"):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append({"name": e.name, "path": os.path.join(resolved, e.name)})
                    else:
                        file_count += 1
                        if files:
                            try:
                                size = e.stat(follow_symlinks=False).st_size
                            except OSError:
                                size = 0
                            file_entries.append({
                                "name": e.name, "path": os.path.join(resolved, e.name),
                                "size": size, "text": _looks_text(e.name)})
                except OSError:
                    pass
    except PermissionError:
        raise HTTPException(403, "permission denied")
    parent = os.path.dirname(resolved)
    if not _within_roots(parent, roots):
        parent = ""
    return {"path": resolved, "parent": parent, "dirs": dirs[:600],
            "file_entries": file_entries[:600], "files": file_count, "roots": roots}


@app.get("/api/fs/read", dependencies=[authed])
async def fs_read(path: str):
    """Preview a text file inside the allowed roots (size-capped)."""
    roots = _fs_roots()
    resolved = os.path.realpath(path)
    if not _within_roots(resolved, roots):
        raise HTTPException(403, "outside allowed workspaces")
    if not os.path.isfile(resolved):
        raise HTTPException(404, "not a file")
    try:
        size = os.path.getsize(resolved)
    except OSError:
        raise HTTPException(404, "not readable")
    cap = 512 * 1024
    try:
        with open(resolved, "rb") as fh:
            raw = fh.read(cap + 1)
    except PermissionError:
        raise HTTPException(403, "permission denied")
    except OSError:
        raise HTTPException(404, "not readable")
    truncated = len(raw) > cap
    raw = raw[:cap]
    if b"\x00" in raw[:4096]:
        return {"path": resolved, "size": size, "binary": True, "content": "",
                "truncated": truncated}
    return {"path": resolved, "size": size, "binary": False, "truncated": truncated,
            "content": raw.decode("utf-8", "replace")}


# ----------------------------------------------------- SSH bootstrap (fleet)

class BootstrapBody(BaseModel):
    host: str
    user: str = "root"
    password: str = ""
    key: str = ""
    port: int = 22
    install_port: int = 8090


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
                              port=body.port or 22, install_port=body.install_port or 8090)
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
async def update_detail(image: str):
    """Installed version/build date + latest upstream releases for one image."""
    return await updates.release_details(image)


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

@app.get("/api/ai/models", dependencies=[authed])
async def ai_models():
    return {"providers": await ai.list_models(), "default": config.get_ai_default()}


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
        if body.default_provider not in config.PROVIDERS:
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
    audit.record("2fa_enable")
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


@app.get("/api/audit", dependencies=[authed])
async def audit_log(limit: int = 80, action: str = "", source: str = "", before: float = 0):
    return audit.recent(min(limit, 300), action, source, before)


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
async def chats_index():
    return {"chats": chats.list_chats()}


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
