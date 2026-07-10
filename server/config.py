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


def get_secret() -> bytes:
    """Signing secret for session tokens, generated on first run."""
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    key = secrets.token_bytes(32)
    _SECRET_FILE.write_bytes(key)
    os.chmod(_SECRET_FILE, 0o600)
    return key


def get_ai_config() -> dict:
    """AI provider config. Env vars win; settings.json (set via UI) is fallback."""
    return {
        "provider": os.environ.get("AI_PROVIDER") or settings.get("ai_provider", ""),
        "api_key": os.environ.get("AI_API_KEY") or settings.get("ai_api_key", ""),
        "model": os.environ.get("AI_MODEL") or settings.get("ai_model", ""),
        "base_url": os.environ.get("AI_BASE_URL") or settings.get("ai_base_url", ""),
    }


def set_ai_config(provider: str, api_key: str, model: str, base_url: str = "") -> None:
    settings["ai_provider"] = provider
    if api_key:  # empty means "keep existing"
        settings["ai_api_key"] = api_key
    settings["ai_model"] = model
    settings["ai_base_url"] = base_url
    save_settings(settings)


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openrouter": "anthropic/claude-sonnet-4.5",
    "openai": "gpt-5.2",
}
