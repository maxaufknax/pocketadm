"""Helmsman — self-hosted server command center. FastAPI app assembly."""
import asyncio
import json

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, appstore, auth, config, dockerapi, sysinfo, terminal, updates

app = FastAPI(title="Helmsman", docs_url=None, redoc_url=None)
auth.bootstrap_password()

authed = Depends(auth.require_auth)


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
    cfg = config.get_ai_config()
    return {
        "ok": True,
        "hostname": sysinfo.hostname(),
        "ai_configured": bool(cfg["api_key"]),
        "ai_provider": cfg["provider"],
        "ai_model": cfg["model"] or config.DEFAULT_MODELS.get(cfg["provider"], ""),
    }


# ------------------------------------------------------------ dashboard

@app.get("/api/system", dependencies=[authed])
async def system():
    data = await asyncio.to_thread(sysinfo.snapshot)
    data["docker"] = await dockerapi.engine_info() if await dockerapi.available() else None
    return data


@app.get("/api/containers", dependencies=[authed])
async def containers():
    return await dockerapi.list_containers()


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


# -------------------------------------------------------------- updates

@app.get("/api/updates", dependencies=[authed])
async def get_updates(force: bool = False):
    docker_updates, apt = await asyncio.gather(
        updates.check_docker_updates(force), updates.check_apt_updates())
    return {"docker": docker_updates, "apt": apt}


class PullBody(BaseModel):
    image: str


@app.post("/api/updates/pull", dependencies=[authed])
async def pull_update(body: PullBody):
    try:
        return {"output": await updates.pull_image(body.image)}
    except Exception as e:
        raise HTTPException(500, str(e))


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


# ------------------------------------------------------------- settings

class AIConfigBody(BaseModel):
    provider: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""


@app.post("/api/settings/ai", dependencies=[authed])
async def set_ai(body: AIConfigBody):
    if body.provider not in ("anthropic", "openrouter", "openai", ""):
        raise HTTPException(400, "unknown provider")
    config.set_ai_config(body.provider, body.api_key, body.model, body.base_url)
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
            if msg.get("type") in ("approve", "set_auto", "reset"):
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
