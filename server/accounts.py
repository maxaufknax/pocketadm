"""AI accounts: one place to connect Claude, ChatGPT, Mistral and the rest,
and one answer to "which AI does what".

An account can be connected two ways: with an API key (billed per use by the
provider) or with a subscription the person already pays for, through the
provider's own coding CLI on the server (Claude Code, Codex, Mistral Vibe —
see signin.py). Either way the account is usable everywhere in PocketADM:
the assistant, the background watch and the explainers (container, update,
health) each pick a provider and model from the connected accounts
(config.get_ai_route).
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from . import clis, config, engines, localai, signin

ACCOUNTS = [
    {"id": "anthropic", "name": "Claude", "vendor": "Anthropic", "engine": "claude-code",
     "key_provider": "anthropic", "subscription": "Claude Pro or Max",
     "key_hint": "console.anthropic.com → API keys", "brand": "anthropic"},
    {"id": "openai", "name": "ChatGPT", "vendor": "OpenAI", "engine": "codex",
     "key_provider": "openai", "subscription": "ChatGPT Plus or Pro",
     "key_hint": "platform.openai.com → API keys", "brand": "openai"},
    {"id": "mistral", "name": "Mistral", "vendor": "Mistral AI", "engine": "mistral-vibe",
     "key_provider": "mistral", "subscription": "Le Chat Pro, with Mistral Vibe",
     "key_hint": "console.mistral.ai → API keys", "brand": "mistral"},
    {"id": "openrouter", "name": "OpenRouter", "vendor": "OpenRouter", "engine": "",
     "key_provider": "openrouter", "subscription": "",
     "key_hint": "openrouter.ai → Keys · one key for many models, with free ones",
     "brand": "openrouter"},
]

FEATURE_LABELS = {"assistant": "Assistant", "watch": "Watch", "insights": "Explanations"}

_status_cache: dict[str, tuple[float, dict]] = {}
_STATUS_TTL = 90


def forget_status(engine: str) -> None:
    _status_cache.pop(engine, None)


async def _run(argv: list[str], timeout: float = 15) -> tuple[int, str]:
    return await signin._run(argv, timeout)


async def engine_status(engine: str) -> dict:
    """Whether an engine's CLI is installed and signed in — asked of the CLI
    itself, cached for a minute and a half (each answer starts a process)."""
    hit = _status_cache.get(engine)
    if hit and time.time() - hit[0] < _STATUS_TTL:
        return hit[1]
    tool = signin.CLI_FOR[engine]
    installed = engines.installed(engine)
    out = {"installed": installed, "signed_in": False, "detail": "", "plan": "", "version": ""}
    if installed:
        out["version"] = await clis._version_of(clis.BIN_DIR / clis.CLIS[tool]["bin"])
    if engine == "claude-code":
        if config.get_engine_token("claude-code"):
            out.update(signed_in=True, detail="Signed in with your Claude subscription")
        elif installed:
            code, text = await _run([engines.binary(engine), "auth", "status", "--json"])
            try:
                data = json.loads(text[text.index("{"):])
            except ValueError:
                data = {}
            if data.get("loggedIn"):
                method = data.get("authMethod", "")
                out.update(signed_in=True,
                           detail="Signed in with an Anthropic API key" if "api" in method.lower()
                           else "Signed in with your Claude subscription")
                out["plan"] = data.get("subscriptionType", "") or ""
    elif engine == "codex" and installed:
        code, text = await _run([engines.binary(engine), "login", "status"])
        low = text.lower()
        # "Not logged in" contains "logged in" too
        if code == 0 and "logged in" in low and "not logged in" not in low:
            out.update(signed_in=True,
                       detail="Signed in with your ChatGPT plan" if "chatgpt" in low
                       else "Signed in with an OpenAI API key")
    elif engine == "mistral-vibe":
        key = signin.vibe_key()
        if key:
            out.update(signed_in=True, detail="Signed in with your Mistral account")
            out["plan"] = await _mistral_plan(key)
    _status_cache[engine] = (time.time(), out)
    return out


_plan_cache: dict[str, tuple[float, str]] = {}


async def _mistral_plan(key: str) -> str:
    """The plan behind a Vibe sign-in ("Le Chat Pro"), as Vibe itself asks."""
    fingerprint = key[-6:]
    hit = _plan_cache.get(fingerprint)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    plan = ""
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get("https://console.mistral.ai/api/vibe/whoami",
                                 headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 200:
                data = r.json()
                plan = str(data.get("plan_name") or data.get("plan_type") or "")[:40]
    except Exception:
        plan = ""
    _plan_cache[fingerprint] = (time.time(), plan)
    return plan


def _uses(provider_ids: set[str]) -> list[str]:
    """Which features run on an account right now."""
    out = []
    for feature, label in FEATURE_LABELS.items():
        route = config.get_ai_route(feature)
        if route.get("provider") in provider_ids:
            out.append(label)
    return out


async def overview() -> dict:
    if config.DEMO:
        from . import demodata
        return demodata.accounts()
    configured = set(config.configured_providers())
    statuses = await asyncio.gather(*(engine_status(a["engine"]) for a in ACCOUNTS if a["engine"]))
    by_engine = {a["engine"]: st for a, st in zip([a for a in ACCOUNTS if a["engine"]], statuses)}
    rows = []
    for a in ACCOUNTS:
        engine = a["engine"]
        st = by_engine.get(engine, {}) if engine else {}
        ids = {a["key_provider"]} | ({engine} if engine else set())
        rows.append({
            **a,
            "key_set": a["key_provider"] in configured,
            "key_from_env": config.key_from_env(a["key_provider"]),
            "can_subscribe": bool(engine),
            "cli_installed": st.get("installed", False),
            "cli_version": st.get("version", ""),
            "signed_in": st.get("signed_in", False),
            "detail": st.get("detail", ""),
            "plan": st.get("plan", ""),
            "connected": a["key_provider"] in configured or st.get("signed_in", False),
            "used_for": _uses(ids),
        })
    local = {"running": False, "base": "", "models": 0}
    try:
        if await localai.available():
            opts = await localai.model_options()
            local = {"running": True, "base": config.get_ollama_base(), "models": len(opts)}
    except Exception:
        pass
    local["used_for"] = _uses({"ollama"})
    return {"accounts": rows, "local": local, "routes": routes()}


def routes() -> dict:
    out = {}
    for feature, label in FEATURE_LABELS.items():
        route = config.get_ai_route(feature)
        custom = feature == "assistant" or bool(
            ((config.settings.get("ai_routes") or {}).get(feature) or {}).get("provider"))
        out[feature] = {"label": label, "provider": route.get("provider", ""),
                        "model": route.get("model", ""), "custom": custom,
                        "provider_label": provider_label(route.get("provider", ""))}
    return out


def provider_label(provider: str) -> str:
    if provider in engines.ENGINES:
        return engines.ENGINES[provider]["label"]
    return {"anthropic": "Anthropic API", "openai": "OpenAI API", "mistral": "Mistral API",
            "openrouter": "OpenRouter", "ollama": "Local model"}.get(provider, provider)


def usable(provider: str) -> bool:
    """Whether a provider can actually answer right now."""
    if not provider:
        return False
    if provider == "ollama":
        return bool(config.get_ollama_base())
    if provider in engines.ENGINES:
        return engines.installed(provider)
    return provider in config.configured_providers()
