"""Helmsman configuration — env-driven with sane defaults and first-run bootstrap."""
import json
import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("HELMSMAN_DATA", "/data")).resolve()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

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


def get_report_config() -> dict:
    return {"interval_min": settings.get("report_interval_min", 360),
            "auto": settings.get("report_auto", True)}


def set_report_config(interval_min: int, auto: bool) -> None:
    settings["report_interval_min"] = max(15, interval_min)
    settings["report_auto"] = auto
    save_settings(settings)


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openrouter": "anthropic/claude-sonnet-4.5",
    "openai": "gpt-5.2",
}
