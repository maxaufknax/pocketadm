"""One-click app deployments: catalog templates -> docker compose projects.

Each installed app lives in DATA_DIR/apps/<id>/docker-compose.yml and runs as
compose project 'helmsman-<id>', so Helmsman can cleanly manage its lifecycle.
"""
import asyncio
import json
import re
import time
from pathlib import Path

import httpx

from . import config, dockerapi

CATALOG_FILE = Path(__file__).parent / "catalog.json"
APPS_DIR = config.DATA_DIR / "apps"
REMOTE_CACHE = config.DATA_DIR / "catalog-remote.json"
REMOTE_TTL = 24 * 3600


def catalog() -> list[dict]:
    """Built-in catalog merged with the remote one (remote wins on id clashes,
    new remote apps are appended) — so the store grows without image updates."""
    apps = {a["id"]: a for a in json.loads(CATALOG_FILE.read_text())["apps"]}
    rc = _load_remote_cache()
    if rc and rc.get("url") == config.get_catalog_url():
        for a in rc.get("apps", []):
            apps[a["id"]] = {**a, "remote": True}
    return list(apps.values())


# ------------------------------------------------------------ remote catalog

def _load_remote_cache() -> dict | None:
    try:
        return json.loads(REMOTE_CACHE.read_text()) if REMOTE_CACHE.exists() else None
    except Exception:
        return None


def _valid_app(a) -> bool:
    """Only accept remote entries that look like real catalog apps."""
    return (isinstance(a, dict)
            and isinstance(a.get("id"), str) and re.fullmatch(r"[a-z0-9-]{1,40}", a["id"])
            and isinstance(a.get("name"), str) and a["name"].strip()
            and isinstance(a.get("compose"), str) and "services:" in a["compose"]
            and isinstance(a.get("fields", []), list)
            and all(isinstance(f, dict) and isinstance(f.get("key"), str)
                    for f in a.get("fields", [])))


def catalog_info() -> dict:
    url = config.get_catalog_url()
    rc = _load_remote_cache()
    fresh = bool(rc and rc.get("url") == url)
    return {"url": url, "enabled": bool(url),
            "remote_count": len(rc.get("apps", [])) if fresh else 0,
            "fetched": rc.get("time") if fresh else None,
            "error": rc.get("error", "") if fresh else ""}


async def refresh_remote(force: bool = False) -> dict:
    """Fetch the remote catalog (if configured and stale). Never raises —
    a broken remote just leaves the built-in catalog in place."""
    url = config.get_catalog_url()
    if not url:
        return catalog_info()
    rc = _load_remote_cache()
    if not force and rc and rc.get("url") == url and \
            time.time() - rc.get("time", 0) < REMOTE_TTL:
        return catalog_info()
    entry = {"time": time.time(), "url": url, "apps": [], "error": ""}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
        entry["apps"] = [a for a in data.get("apps", []) if _valid_app(a)][:200]
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"[:200]
        if rc and rc.get("url") == url:      # keep last good apps on failure
            entry["apps"] = rc.get("apps", [])
    REMOTE_CACHE.write_text(json.dumps(entry))
    return catalog_info()


async def remote_refresher() -> None:
    """Background task: keep the remote catalog reasonably fresh."""
    await asyncio.sleep(20)
    while True:
        try:
            await refresh_remote()
        except Exception:
            pass
        await asyncio.sleep(6 * 3600)


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


def _detect_patterns(app: dict) -> list[str]:
    """Image substrings that identify an app, e.g. 'portainer/portainer-ce'.
    Catalog entries can override with a 'detect' list; default = the image
    repos from the compose template (without registry host and tag)."""
    if app.get("detect"):
        return [d.lower() for d in app["detect"]]
    pats = []
    for m in re.finditer(r"^\s*image:\s*(\S+)", app.get("compose", ""), re.M):
        ref = m.group(1).split("@")[0]
        if ":" in ref.rsplit("/", 1)[-1]:
            ref = ref.rsplit(":", 1)[0]
        parts = ref.split("/")
        # drop registry host (docker.n8n.io/…, lscr.io/…) but keep org/name
        if len(parts) > 1 and ("." in parts[0] or parts[0] == "localhost"):
            parts = parts[1:]
        pats.append("/".join(parts).lower())
    return pats


def _normalize_image(ref: str) -> str:
    """'lscr.io/linuxserver/code-server:4.9' -> 'linuxserver/code-server'."""
    ref = ref.split("@")[0].lower()
    if ":" in ref.rsplit("/", 1)[-1]:
        ref = ref.rsplit(":", 1)[0]
    parts = ref.split("/")
    if len(parts) > 1 and ("." in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]
    return "/".join(parts)


def _image_matches(pattern: str, image: str) -> bool:
    """Precise match: org/name patterns need exact equality; bare names match
    the image basename (also with -variant suffixes like grafana-oss)."""
    norm = _normalize_image(image)
    if "/" in pattern:
        return norm == pattern
    base = norm.rsplit("/", 1)[-1]
    return base == pattern or base.startswith(pattern + "-")


def _is_image_id(image: str) -> bool:
    """True for untagged references like 'd8a7bc55027e' or 'sha256:…' — docker
    shows these when the tag moved to a newer image after a pull."""
    ref = image.removeprefix("sha256:")
    return bool(re.fullmatch(r"[0-9a-f]{12,64}", ref))


GENERIC_NAME_PARTS = {"server", "app", "web", "api", "service", "docker", "ce"}


def _name_matches(pattern: str, container: dict) -> bool:
    """Fallback when the image reference is unusable (bare image ID):
    match the app by container name / compose service, e.g. app 'nextcloud'
    ↔ containers 'nextcloud', 'nextcloud-cron', 'nextcloud-db'. Generic parts
    ('server' in vaultwarden/server) never identify an app by themselves."""
    keys = {p for p in (pattern.rsplit("/", 1)[-1], pattern.split("/", 1)[0])
            if p and p not in GENERIC_NAME_PARTS}
    for cand in (container.get("name", ""), container.get("compose_service", "")):
        cand = cand.lower()
        for key in keys:
            if cand == key or cand.startswith(key + "-") or cand.endswith("-" + key):
                return True
    return False


# helper images that appear in many stacks and must not identify an app
GENERIC_IMAGES = ("mariadb", "mysql", "postgres", "redis", "mongo", "alpine",
                  "busybox", "debian", "ubuntu", "python", "node", "nginx")


async def installed() -> dict[str, dict]:
    """Map app_id -> install status for every catalog app.

    source 'helmsman'  — deployed by us as compose project helmsman-<id>
    source 'external'  — matching containers exist on the server but were
                         installed some other way (docker run, own compose …)
    """
    result: dict[str, dict] = {}
    deployed = {d.name for d in APPS_DIR.iterdir() if (d / "docker-compose.yml").exists()} \
        if APPS_DIR.exists() else set()
    try:
        containers = await dockerapi.list_containers(all_=True)
    except Exception:
        containers = []

    for app in catalog():
        app_id = app["id"]
        if app_id in deployed:
            project = f"helmsman-{app_id}"
            app_containers = [c for c in containers if c["compose_project"] == project]
            result[app_id] = {
                "source": "helmsman",
                "running": any(c["state"] == "running" for c in app_containers),
                "containers": [c["name"] for c in app_containers],
                "ports": sorted({p["public"] for c in app_containers for p in c["ports"]}),
            }
            continue
        # external detection by image match (skip helper images like mariadb)
        pats = [p for p in _detect_patterns(app)
                if p.rsplit("/", 1)[-1] not in GENERIC_IMAGES]
        matches = []
        for c in containers:
            if c["compose_project"].startswith("helmsman-"):
                continue
            if any(_image_matches(p, c["image"]) for p in pats):
                matches.append(c)
            elif _is_image_id(c["image"]) and any(_name_matches(p, c) for p in pats):
                matches.append(c)
        if matches:
            result[app_id] = {
                "source": "external",
                "running": any(c["state"] == "running" for c in matches),
                "containers": [c["name"] for c in matches],
                "ports": sorted({p["public"] for c in matches for p in c["ports"]}),
            }
    return result
