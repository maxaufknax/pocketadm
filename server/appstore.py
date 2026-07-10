"""One-click app deployments: catalog templates -> docker compose projects.

Each installed app lives in DATA_DIR/apps/<id>/docker-compose.yml and runs as
compose project 'helmsman-<id>', so Helmsman can cleanly manage its lifecycle.
"""
import asyncio
import json
import re
from pathlib import Path

from . import config, dockerapi

CATALOG_FILE = Path(__file__).parent / "catalog.json"
APPS_DIR = config.DATA_DIR / "apps"


def catalog() -> list[dict]:
    return json.loads(CATALOG_FILE.read_text())["apps"]


def get_app(app_id: str) -> dict | None:
    return next((a for a in catalog() if a["id"] == app_id), None)


def _validate_value(key: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{key} must not be empty")
    # No newlines/quotes — values are substituted into YAML
    if re.search(r"[\n\r\"'`$]", value):
        raise ValueError(f"{key} contains invalid characters")
    if key.endswith("PORT") and not value.isdigit():
        raise ValueError(f"{key} must be a number")
    return value


async def _compose(app_id: str, *args: str, timeout: int = 600) -> str:
    app_dir = APPS_DIR / app_id
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "-p", f"helmsman-{app_id}", *args,
        cwd=app_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    text = out.decode("utf-8", "replace")[-4000:]
    if proc.returncode != 0:
        raise RuntimeError(text)
    return text


async def install(app_id: str, values: dict[str, str]) -> str:
    app = get_app(app_id)
    if not app:
        raise ValueError(f"Unknown app: {app_id}")
    compose_text = app["compose"]
    for field in app.get("fields", []):
        val = _validate_value(field["key"], str(values.get(field["key"], field["default"])))
        compose_text = compose_text.replace("{{" + field["key"] + "}}", val)
    app_dir = APPS_DIR / app_id
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "docker-compose.yml").write_text(compose_text)
    return await _compose(app_id, "up", "-d", "--pull", "always")


async def uninstall(app_id: str, remove_data: bool = False) -> str:
    if not (APPS_DIR / app_id / "docker-compose.yml").exists():
        raise ValueError("App is not installed")
    args = ["down"] + (["-v"] if remove_data else [])
    out = await _compose(app_id, *args)
    if remove_data:
        for f in (APPS_DIR / app_id).iterdir():
            f.unlink()
        (APPS_DIR / app_id).rmdir()
    return out


async def installed() -> dict[str, dict]:
    """Map app_id -> {running, containers[]} for catalog apps we deployed."""
    result: dict[str, dict] = {}
    deployed = {d.name for d in APPS_DIR.iterdir() if (d / "docker-compose.yml").exists()} \
        if APPS_DIR.exists() else set()
    if not deployed:
        return {}
    try:
        containers = await dockerapi.list_containers(all_=True)
    except Exception:
        containers = []
    for app_id in deployed:
        project = f"helmsman-{app_id}"
        app_containers = [c for c in containers if c["compose_project"] == project]
        result[app_id] = {
            "running": any(c["state"] == "running" for c in app_containers),
            "containers": [c["name"] for c in app_containers],
            "ports": sorted({p["public"] for c in app_containers for p in c["ports"]}),
        }
    return result
