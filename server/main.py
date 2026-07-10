"""Helmsman — self-hosted server command center. FastAPI app assembly."""
import asyncio
import json

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, appstore, auth, config, dockerapi, jobs, reports, sysinfo, terminal, updates

app = FastAPI(title="Helmsman", docs_url=None, redoc_url=None)
auth.bootstrap_password()

authed = Depends(auth.require_auth)


@app.on_event("startup")
async def _startup():
    reports.start_scheduler()


# ------------------------------------------------------------------ auth

class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    auth.rate_limit(ip)
    if not auth.verify_password(body.password):
        auth.record_failure(ip)
        raise HTTPException(401, "Wrong password")
    return {"token": auth.issue_token()}


@app.get("/api/me", dependencies=[authed])
async def me():
    default = config.get_ai_default()
    return {
        "ok": True,
        "hostname": sysinfo.hostname(),
        "ai_configured": bool(default["provider"]),
        "ai_default": default,
        "ai_providers": config.configured_providers(),
        "workspaces": config.get_workspaces(),
        "report_config": config.get_report_config(),
    }


# ------------------------------------------------------------ dashboard

@app.get("/api/system", dependencies=[authed])
async def system():
    data = await asyncio.to_thread(sysinfo.snapshot)
    data["docker"] = await dockerapi.engine_info() if await dockerapi.available() else None
    return data


@app.get("/api/containers", dependencies=[authed])
async def containers():
    result = await dockerapi.list_containers()
    for c in result:
        c["service"] = updates.service_meta(c["image"])
    return result


@app.post("/api/containers/{cid}/{action}", dependencies=[authed])
async def container_action(cid: str, action: str):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "bad action")
    await dockerapi.container_action(cid, action)
    return {"ok": True}


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


class UpdateBody(BaseModel):
    image: str
    recreate: bool = True


@app.post("/api/updates/apply", dependencies=[authed])
async def apply_update(body: UpdateBody):
    job = updates.start_update_job(body.image, body.recreate)
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


# ------------------------------------------------------------- appstore

@app.get("/api/apps", dependencies=[authed])
async def apps():
    return {"catalog": appstore.catalog(), "installed": await appstore.installed()}


class InstallBody(BaseModel):
    values: dict[str, str] = {}


@app.post("/api/apps/{app_id}/install", dependencies=[authed])
async def install_app(app_id: str, body: InstallBody):
    try:
        return {"output": await appstore.install(app_id, body.values)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/apps/{app_id}/uninstall", dependencies=[authed])
async def uninstall_app(app_id: str, remove_data: bool = False):
    try:
        return {"output": await appstore.uninstall(app_id, remove_data)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------- ai / settings

@app.get("/api/ai/models", dependencies=[authed])
async def ai_models():
    return {"providers": await ai.list_models(), "default": config.get_ai_default()}


@app.get("/api/ai/usage", dependencies=[authed])
async def ai_usage():
    return ai.usage_summary()


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


class WorkspacesBody(BaseModel):
    paths: list[str]


@app.post("/api/settings/workspaces", dependencies=[authed])
async def set_workspaces(body: WorkspacesBody):
    config.set_workspaces(body.paths[:12])
    return {"ok": True, "workspaces": config.get_workspaces()}


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


# ----------------------------------------------------------- websockets

@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket):
    await ws.accept()
    if not await auth.require_auth_ws(ws):
        return
    await terminal.handle_terminal(ws, ws.query_params.get("context", "local"))


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    if not await auth.require_auth_ws(ws):
        return
    session = ai.ChatSession(ws)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") in ("approve", "config", "reset"):
                await session.handle_client_message(msg)
            else:
                # run turns as a task so approvals can arrive while streaming
                asyncio.ensure_future(session.handle_client_message(msg))
    except (WebSocketDisconnect, RuntimeError):
        pass


# --------------------------------------------------------------- static

@app.exception_handler(404)
async def spa_fallback(request: Request, exc):
    if request.url.path.startswith(("/api/", "/ws/")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(config.WEB_DIR / "index.html")

app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
