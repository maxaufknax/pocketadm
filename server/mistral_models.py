"""The models a Mistral key can reach — for the Mistral API provider and for
Mistral Vibe, which can run any of them.

Mistral's /v1/models lists every alias as its own entry (mistral-medium,
mistral-medium-3.5, mistral-vibe-cli-latest … are one model) plus embedding,
OCR, moderation and audio models. Menus want one row per model that can chat
and call tools, with a name a person recognises. The list depends on the
account: a Le Chat login reaches other models than a paid API workspace (the
GLM models, Mistral Large), so it is always asked live and cached per key.
"""
from __future__ import annotations

import hashlib
import re
import time

import httpx

API = "https://api.mistral.ai/v1/models"
TTL = 3600

# not useful as a chat or coding model, whatever the capability flags say
_SKIP = ("labs-", "voxtral", "ocr", "embed", "moderation", "mistral-code-fim")

# names people know these by; everything else is derived from the id
_NAMES = {
    "mistral-medium-latest": "Mistral Medium 3.5",
    "mistral-large-2512": "Mistral Large 3",
    "mistral-large-latest": "Mistral Large 3",
    "mistral-small-2603": "Mistral Small 4",
    "mistral-small-latest": "Mistral Small 4",
    "devstral-2512": "Devstral 2",
    "devstral-latest": "Devstral 2",
    "devstral-small-2512": "Devstral Small 2",
    "codestral-2508": "Codestral",
    "codestral-latest": "Codestral",
    "zai-glm-latest": "GLM 5.3",
}

# what a model is good for, in a few words, by id prefix (first match wins)
_HINTS = (
    ("zai-glm", "strong reasoning, careful with tools"),
    ("glm", "strong reasoning, careful with tools"),
    ("devstral", "built for coding agents"),
    ("mistral-large", "Mistral's largest model"),
    ("mistral-medium", "balanced, Vibe's default"),
    ("magistral", "thinks before it answers"),
    ("codestral", "code completion and edits"),
    ("mistral-small", "fast and cheap"),
    ("ministral", "tiny and very fast"),
)

# order in menus: the strong general models first, the small ones last
_ORDER = ("mistral-medium", "zai-glm", "glm", "devstral", "mistral-large", "magistral",
          "codestral", "mistral-small", "ministral")

_cache: dict[str, tuple[float, list[dict]]] = {}


def display_name(model_id: str) -> str:
    """mistral-large-4 → Mistral Large 4, zai-glm-5-3 → GLM 5.3,
    ministral-14b-2512 → Ministral 14B (25.12)."""
    if model_id in _NAMES:
        return _NAMES[model_id]
    parts = [p for p in model_id.split("-") if p and p not in ("latest", "zai")]
    words: list[str] = []
    version: list[str] = []
    date = ""
    for p in parts:
        if re.fullmatch(r"\d{4}", p):
            date = f"({p[:2]}.{p[2:]})"
        elif re.fullmatch(r"\d{1,2}", p):
            version.append(p)
        elif re.fullmatch(r"\d+b", p):
            words.append(p.upper())
        elif p in ("glm",):
            words.append("GLM")
        else:
            words.append(p.capitalize())
    name = " ".join(words + ([".".join(version)] if version else []))
    return f"{name} {date}".strip() if date else name


def hint(model_id: str) -> str:
    for prefix, text in _HINTS:
        if model_id.startswith(prefix):
            return text
    return ""


# families that take a reasoning effort (Mistral's /v1/models says so)
_REASONING = ("zai-glm", "glm-", "magistral", "mistral-medium", "mistral-small-26",
              "mistral-small-latest", "mistral-large-4", "mistral-vibe-cli")


def reasons(model_id: str) -> bool:
    return model_id.startswith(_REASONING)


def _rank(model_id: str) -> tuple:
    for i, prefix in enumerate(_ORDER):
        if model_id.startswith(prefix):
            return (i, model_id)
    return (len(_ORDER), model_id)


def chat_models(data: list[dict]) -> list[dict]:
    """One row per model that can chat and call tools, in menu order:
    [{"id", "name", "hint", "aliases", "reasoning"}]. The id is the model's own
    name in the API (the alias every other entry points to)."""
    seen: dict[str, dict] = {}
    for m in data or []:
        caps = m.get("capabilities") or {}
        mid = str(m.get("id") or "")
        canonical = str(m.get("name") or mid)
        if not mid or any(s in canonical or s in mid for s in _SKIP):
            continue
        if not (caps.get("completion_chat") and caps.get("function_calling")):
            continue
        if m.get("deprecation"):
            continue
        row = seen.setdefault(canonical, {
            "id": canonical, "name": display_name(canonical), "hint": hint(canonical),
            "aliases": set(), "reasoning": bool(caps.get("reasoning"))})
        row["aliases"].add(mid)
        row["aliases"].update(a for a in m.get("aliases") or [] if isinstance(a, str))
    rows = sorted(seen.values(), key=lambda r: _rank(r["id"]))
    for r in rows:
        r["aliases"] = sorted(r["aliases"] - {r["id"]})
    return rows


async def fetch(key: str, force: bool = False) -> list[dict]:
    """chat_models() for the account behind `key`, cached for an hour. An
    unreachable API gives an empty list, never an exception."""
    if not key:
        return []
    fp = hashlib.sha256(key.encode()).hexdigest()[:16]
    hit = _cache.get(fp)
    if hit and not force and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(API, headers={"Authorization": f"Bearer {key}"})
        rows = chat_models(r.json().get("data", [])) if r.status_code == 200 else []
    except Exception:
        rows = []
    if rows or not hit:
        _cache[fp] = (time.time(), rows)
    return rows or (hit[1] if hit else [])


def covers(rows: list[dict], model_id: str) -> bool:
    """Whether a model (by id or any alias) is among `rows`."""
    return any(model_id == r["id"] or model_id in r["aliases"] for r in rows)
