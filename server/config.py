"""Helmsman configuration — env-driven with sane defaults and first-run bootstrap."""
import json
import os
import secrets
from pathlib import Path

VERSION = "0.14.0"

DATA_DIR = Path(os.environ.get("HELMSMAN_DATA", "/data")).resolve()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Demo mode: read-only public playground — any mutation is rejected, the
# password is "demo", and Docker data is faked when no socket is mounted.
DEMO = os.environ.get("HELMSMAN_DEMO", "").lower() in ("1", "true", "yes")

# When running from a checkout (dev mode), fall back to ./data
if not DATA_DIR.exists() and not os.environ.get("HELMSMAN_DATA"):
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "apps").mkdir(exist_ok=True)

_SETTINGS_FILE = DATA_DIR / "settings.json"
_SECRET_FILE = DATA_DIR / "secret.key"


def _load_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_settings(settings: dict) -> None:
    _SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    os.chmod(_SETTINGS_FILE, 0o600)


settings = _load_settings()

# migrate v1 single-provider settings to per-provider keys
if "ai_api_key" in settings and "ai_keys" not in settings:
    prov = settings.get("ai_provider") or "openrouter"
    settings["ai_keys"] = {prov: settings.pop("ai_api_key")}
    settings["ai_default"] = {"provider": prov, "model": settings.pop("ai_model", "")}
    settings.pop("ai_provider", None)
    settings.pop("ai_base_url", None)
    save_settings(settings)


def get_secret() -> bytes:
    """Signing secret for session tokens, generated on first run."""
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    key = secrets.token_bytes(32)
    _SECRET_FILE.write_bytes(key)
    os.chmod(_SECRET_FILE, 0o600)
    return key


PROVIDERS = ("anthropic", "openrouter", "openai")


def get_key(provider: str) -> str:
    """API key for a provider. Env var wins over UI-stored settings."""
    if os.environ.get("AI_PROVIDER") == provider and os.environ.get("AI_API_KEY"):
        return os.environ["AI_API_KEY"]
    env = os.environ.get(f"{provider.upper()}_API_KEY", "")
    return env or settings.get("ai_keys", {}).get(provider, "")


def set_keys(keys: dict[str, str]) -> None:
    stored = settings.setdefault("ai_keys", {})
    for prov, key in keys.items():
        if prov in PROVIDERS and key:
            stored[prov] = key
        elif prov in PROVIDERS and key == "-":  # "-" clears a key
            stored.pop(prov, None)
    save_settings(settings)


def configured_providers() -> list[str]:
    return [p for p in PROVIDERS if get_key(p)]


def get_ai_default() -> dict:
    d = settings.get("ai_default") or {}
    provider = os.environ.get("AI_PROVIDER") or d.get("provider", "")
    if not provider or not get_key(provider):
        avail = configured_providers()
        provider = avail[0] if avail else ""
    model = os.environ.get("AI_MODEL") or d.get("model", "") or DEFAULT_MODELS.get(provider, "")
    return {"provider": provider, "model": model}


def set_ai_default(provider: str, model: str) -> None:
    settings["ai_default"] = {"provider": provider, "model": model}
    save_settings(settings)


def get_base_url(provider: str) -> str:
    if os.environ.get("AI_PROVIDER") == provider and os.environ.get("AI_BASE_URL"):
        return os.environ["AI_BASE_URL"]
    return settings.get("ai_base_urls", {}).get(provider, "")


# ---- misc user settings with defaults ----

def get_server_name() -> str:
    return settings.get("server_name", "")


def set_server_name(name: str) -> None:
    settings["server_name"] = name.strip()[:40]
    save_settings(settings)


def get_onboarded() -> bool:
    return bool(settings.get("onboarded"))


def set_onboarded() -> None:
    settings["onboarded"] = True
    save_settings(settings)


# ---- security: token generation + 2FA secret ----

def get_auth_generation() -> int:
    return int(settings.get("auth_generation", 0))


def bump_auth_generation() -> int:
    settings["auth_generation"] = get_auth_generation() + 1
    save_settings(settings)
    return settings["auth_generation"]


def get_totp_secret() -> str:
    return settings.get("totp_secret", "")


def set_totp_secret(secret: str) -> None:
    if secret:
        settings["totp_secret"] = secret
    else:
        settings.pop("totp_secret", None)
    save_settings(settings)



def get_ignored_images() -> list[str]:
    return settings.get("ignored_images", [])


def set_ignored_image(image: str, ignored: bool) -> None:
    lst = set(settings.get("ignored_images", []))
    (lst.add if ignored else lst.discard)(image)
    settings["ignored_images"] = sorted(lst)
    save_settings(settings)


def get_workspaces() -> list[str]:
    ws = settings.get("workspaces")
    if ws:
        return ws
    host = os.path.isdir("/host")
    defaults = ["/host/srv", "/host/home", "/host"] if host else ["/srv", os.path.expanduser("~")]
    return [w for w in defaults if os.path.isdir(w)]


def set_workspaces(paths: list[str]) -> None:
    settings["workspaces"] = [p for p in paths if p.strip()]
    save_settings(settings)


def get_default_workspace() -> str:
    """The folder new agent chats start in. Falls back to the first workspace."""
    dw = settings.get("default_workspace", "")
    if dw and os.path.isdir(dw):
        return dw
    ws = get_workspaces()
    return ws[0] if ws else ""


def set_default_workspace(path: str) -> None:
    settings["default_workspace"] = path.strip()
    save_settings(settings)


# ---- agent: custom instructions + disabled tools ----

CUSTOM_INSTRUCTIONS_MAX = 4000


def get_custom_instructions() -> str:
    return settings.get("custom_instructions", "")


def set_custom_instructions(text: str) -> None:
    settings["custom_instructions"] = (text or "").strip()[:CUSTOM_INSTRUCTIONS_MAX]
    save_settings(settings)


def get_disabled_tools() -> list[str]:
    return settings.get("disabled_tools", [])


def set_tool_enabled(name: str, enabled: bool) -> None:
    disabled = set(settings.get("disabled_tools", []))
    (disabled.discard if enabled else disabled.add)(name)
    settings["disabled_tools"] = sorted(disabled)
    save_settings(settings)


# ---- app catalog ----

DEFAULT_CATALOG_URL = ("https://raw.githubusercontent.com/maxaufknax/helmsman/"
                       "main/server/catalog.json")


def get_catalog_url() -> str:
    """Remote catalog source; '' disables remote fetching entirely."""
    env = os.environ.get("HELMSMAN_CATALOG_URL")
    if env is not None:
        return env.strip()
    return settings.get("catalog_url", DEFAULT_CATALOG_URL).strip()


def set_catalog_url(url: str) -> None:
    url = (url or "").strip()
    settings["catalog_url"] = url
    save_settings(settings)


# ---- local AI (Ollama) ----

def get_ollama_base() -> str:
    """Last-known / user-set Ollama endpoint (host or URL). '' = auto-detect."""
    return os.environ.get("OLLAMA_HOST") or settings.get("ollama_base", "")


def set_ollama_base(base: str) -> None:
    settings["ollama_base"] = (base or "").strip()
    save_settings(settings)


def get_ollama_network() -> str:
    """Docker network Helmsman was joined to so it can reach an Ollama container
    (reconnected on startup so it survives redeploys)."""
    return settings.get("ollama_network", "")


def set_ollama_network(network: str) -> None:
    settings["ollama_network"] = (network or "").strip()
    save_settings(settings)


def get_report_config() -> dict:
    return {"interval_min": settings.get("report_interval_min", 360),
            "auto": settings.get("report_auto", True)}


def set_report_config(interval_min: int, auto: bool) -> None:
    settings["report_interval_min"] = max(15, interval_min)
    settings["report_auto"] = auto
    save_settings(settings)


# ---------------------------------------------------------- agent autonomy

# How the long-running agent behaves at a checkpoint (every N steps / minutes):
#   "checkpoint" — pause and wait for the user to tap Continue (push sent)
#   "autonomous" — keep going on its own; only pause at the hard safety cap
def get_autonomy() -> dict:
    a = settings.get("agent_autonomy", {})
    return {
        "pause_mode": a.get("pause_mode", "checkpoint"),
        "steps": int(a.get("steps", 25)),        # tool runs per checkpoint (0 = off)
        "minutes": int(a.get("minutes", 0)),     # wall-clock per checkpoint (0 = off)
        "push": bool(a.get("push", True)),        # notify on pause / error
    }


def set_autonomy(pause_mode: str = "", steps: int | None = None,
                 minutes: int | None = None, push: bool | None = None) -> dict:
    a = settings.setdefault("agent_autonomy", {})
    if pause_mode in ("checkpoint", "autonomous"):
        a["pause_mode"] = pause_mode
    if steps is not None:
        a["steps"] = max(0, min(500, int(steps)))
    if minutes is not None:
        a["minutes"] = max(0, min(1440, int(minutes)))
    if push is not None:
        a["push"] = bool(push)
    save_settings(settings)
    return get_autonomy()


def get_ntfy_url() -> str:
    """Global ntfy topic URL used for agent push (pause / stop / done).
    Per-loop URLs (Sentinel) are separate and take precedence for those."""
    return os.environ.get("NTFY_URL", "") or settings.get("ntfy_url", "")


def set_ntfy_url(url: str) -> None:
    settings["ntfy_url"] = (url or "").strip()[:300]
    save_settings(settings)


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openrouter": "anthropic/claude-sonnet-4.5",
    "openai": "gpt-5.2",
}
