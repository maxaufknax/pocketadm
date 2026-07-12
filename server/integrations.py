"""Third-party service integrations (DNS providers, self-hosted APIs …).

The user stores API credentials once (Settings → Integrations); the Vibe
agent can then call the service through the integration_request tool without
ever seeing the raw secret — Helmsman injects the auth header server-side.

Built-in types know their base URL and auth scheme; "generic" lets the user
wire up anything (a local Grafana/Portainer/Nextcloud API, some cloud
service …) with a base URL and a custom auth header.
"""
import json
import time

import httpx

from . import config

MAX_RESPONSE = 10000

TYPES: dict[str, dict] = {
    "desec": {
        "label": "deSEC DNS",
        "base_url": "https://desec.io/api/v1",
        "auth_header": lambda secret: {"Authorization": f"Token {secret}"},
        "test_path": "/domains/",
        "hint": "Token from desec.io → Token management",
        "docs": "https://desec.readthedocs.io/en/latest/",
    },
    "ionos": {
        "label": "IONOS DNS",
        "base_url": "https://api.hosting.ionos.com/dns/v1",
        "auth_header": lambda secret: {"X-API-Key": secret},
        "test_path": "/zones",
        "hint": "publicprefix.secret from developer.hosting.ionos.de",
        "docs": "https://developer.hosting.ionos.de/docs/dns",
    },
    "godaddy": {
        "label": "GoDaddy",
        "base_url": "https://api.godaddy.com/v1",
        "auth_header": lambda secret: {"Authorization": f"sso-key {secret}"},
        "test_path": "/domains?limit=1",
        "hint": "key:secret from developer.godaddy.com/keys",
        "docs": "https://developer.godaddy.com/doc",
    },
    "cloudflare": {
        "label": "Cloudflare",
        "base_url": "https://api.cloudflare.com/client/v4",
        "auth_header": lambda secret: {"Authorization": f"Bearer {secret}"},
        "test_path": "/user/tokens/verify",
        "hint": "API token from dash.cloudflare.com → My Profile → API Tokens",
        "docs": "https://developers.cloudflare.com/api/",
    },
    "generic": {
        "label": "Generic API",
        "base_url": "",           # user-provided
        "auth_header": None,      # user-provided header name
        "test_path": "",
        "hint": "Any HTTP API: base URL + auth header (e.g. a local Grafana or Portainer)",
        "docs": "",
    },
}


def _store() -> dict:
    return config.settings.setdefault("integrations", {})


def list_public() -> list[dict]:
    """Integrations without secrets, for the UI and the agent prompt.

    Secrets never leave this module — only *metadata* about them (that one is
    set and how long it is) is exposed, so the UI can show a masked hint."""
    out = []
    for name, item in _store().items():
        t = TYPES.get(item.get("type", "generic"), TYPES["generic"])
        secret = item.get("secret", "")
        out.append({
            "name": name,
            "type": item.get("type", "generic"),
            "type_label": t["label"],
            "base_url": item.get("base_url") or t["base_url"],
            "auth_header_name": item.get("auth_header_name", ""),
            "note": item.get("note", ""),
            "enabled": item.get("enabled", True),
            "has_secret": bool(secret),
            "secret_len": len(secret),
            "last_used": item.get("last_used", 0),
        })
    return sorted(out, key=lambda x: x["name"])


def save(name: str, type_: str, secret: str, base_url: str = "",
         auth_header_name: str = "", note: str = "", enabled: bool = True) -> None:
    name = name.strip()[:40]
    if not name or not all(c.isalnum() or c in "-_." for c in name):
        raise ValueError("Name: letters/digits/-_. only")
    if type_ not in TYPES:
        raise ValueError(f"Unknown type {type_}")
    if type_ == "generic" and not base_url.startswith(("http://", "https://")):
        raise ValueError("Generic integrations need a http(s) base URL")
    store = _store()
    entry = store.get(name, {})
    store[name] = {
        "type": type_,
        "secret": secret.strip() or entry.get("secret", ""),
        "base_url": base_url.strip().rstrip("/"),
        "auth_header_name": auth_header_name.strip(),
        "note": note.strip()[:120],
        "enabled": enabled,
        "last_used": entry.get("last_used", 0),
    }
    if not store[name]["secret"]:
        raise ValueError("An API token/secret is required")
    config.save_settings(config.settings)


def set_enabled(name: str, enabled: bool) -> bool:
    entry = _store().get(name)
    if not entry:
        return False
    entry["enabled"] = enabled
    config.save_settings(config.settings)
    return True


def remove(name: str) -> None:
    _store().pop(name, None)
    config.save_settings(config.settings)


def _resolve(name: str) -> tuple[dict, str, dict]:
    """-> (entry, base_url, auth_headers)"""
    entry = _store().get(name)
    if not entry:
        raise ValueError(f"No integration named '{name}'. Configured: "
                         + (", ".join(_store()) or "none"))
    if not entry.get("enabled", True):
        raise ValueError(f"The integration '{name}' is turned off. The owner must "
                         "re-enable it under Settings → Integrations before it can be used.")
    t = TYPES.get(entry.get("type", "generic"), TYPES["generic"])
    base = entry.get("base_url") or t["base_url"]
    if t["auth_header"]:
        headers = t["auth_header"](entry["secret"])
    else:
        hname = entry.get("auth_header_name") or "Authorization"
        headers = {hname: entry["secret"]}
    return entry, base, headers


async def request(name: str, method: str, path: str,
                  body: str = "", params: dict | None = None) -> str:
    """Perform an authenticated request on behalf of the agent."""
    entry, base, headers = _resolve(name)
    entry["last_used"] = int(time.time())
    config.save_settings(config.settings)
    method = (method or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return f"Error: unsupported method {method}"
    url = base + ("/" + path.lstrip("/") if path else "")
    kwargs: dict = {"headers": {**headers, "User-Agent": "Helmsman-Agent/1.0"}}
    if params:
        kwargs["params"] = params
    if body:
        try:
            kwargs["json"] = json.loads(body)
        except json.JSONDecodeError:
            kwargs["content"] = body
            kwargs["headers"]["Content-Type"] = "text/plain"
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            r = await client.request(method, url, **kwargs)
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"
    text = r.text or "[empty response]"
    if len(text) > MAX_RESPONSE:
        text = text[:MAX_RESPONSE] + f"\n… [truncated, {len(text)} chars total]"
    return f"[{r.status_code}] {text}"


async def test(name: str) -> dict:
    entry, base, headers = _resolve(name)
    t = TYPES.get(entry.get("type", "generic"), TYPES["generic"])
    path = t["test_path"] or "/"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(base + path, headers=headers)
        return {"ok": r.status_code < 400, "status": r.status_code,
                "detail": r.text[:300]}
    except Exception as e:
        return {"ok": False, "status": 0, "detail": f"{type(e).__name__}: {e}"}


def prompt_section() -> str:
    """Text block for the agent system prompt, so it knows what's connected."""
    items = [i for i in list_public() if i["enabled"] and i["has_secret"]]
    if not items:
        return ""
    lines = [f"- \"{i['name']}\" ({i['type_label']}, base {i['base_url']})"
             + (f" — {i['note']}" if i["note"] else "") for i in items]
    return ("\nConnected integrations — call them with the integration_request tool "
            "(paths are relative to each base URL):\n" + "\n".join(lines) + "\n"
            "Handle these credentials with care: the tokens are injected server-side and "
            "you never see them — never ask the user for them, never try to read them from "
            "disk, and don't echo secrets or full API responses that may contain them. "
            "Anything that changes state (POST/PUT/PATCH/DELETE) is confirmed with the user "
            "first, even in Auto mode. Prefer read-only (GET) calls when exploring.\n")
