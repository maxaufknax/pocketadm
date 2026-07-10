"""Vibe Code — an AI agent that works on the server via tool use.

Provider-agnostic (Anthropic native API or any OpenAI-compatible endpoint
such as OpenRouter). Streams over WebSocket with a JSON event protocol:

  server -> client:
    {"type":"text","delta":str}                  assistant text chunk
    {"type":"tool_request","id","name","args"}   needs approval
    {"type":"tool_start","id","name","args"}     executing now
    {"type":"tool_result","id","output"}
    {"type":"usage","turn":{...},"session":{...}}
    {"type":"done"} | {"type":"error","message"}
  client -> server:
    {"type":"user","text":str}
    {"type":"config","mode","provider","model","workdir"}   (any subset)
    {"type":"approve","id":str,"approved":bool}
    {"type":"reset"}

Modes:
  chat  — conversation only, no tools
  plan  — read-only investigation (safe tools auto), proposes a plan
  agent — full tools, destructive/write actions need approval   (default)
  auto  — full tools, everything auto-approved
"""
import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from . import config

MAX_TOOL_OUTPUT = 12000
MAX_TURNS = 25
DEFAULT_WORKDIR = os.environ.get(
    "HELMSMAN_WORKDIR", "/host" if os.path.isdir("/host") else os.path.expanduser("~"))

SYSTEM_PROMPT = """You are Vibe Code, the built-in AI engineer of Helmsman, a self-hosted \
server management app. You work directly on the user's server through tools.

Environment: you run {where}. Your working directory is {workdir}. \
The docker CLI is available and controls the host's Docker engine{host_note}.

Guidelines:
- Be concise; the user is often on a phone. Prefer short answers and small steps.
- Inspect before you change: read files / list dirs / check state first.
- For destructive actions (rm, docker rm, overwriting configs), state what you are about to do first.
- When you finish a task, summarize in 1-3 sentences what you changed.
- Answer in the language the user writes in.
{mode_note}"""

MODE_NOTES = {
    "chat": "\nMode: CHAT — you have no tools in this mode. Answer from knowledge; "
            "if the user wants server actions, suggest switching to Agent mode.",
    "plan": "\nMode: PLAN — investigate read-only and produce a concrete step-by-step "
            "plan. Do NOT modify anything (no writes, no state-changing commands). "
            "End with a numbered plan the user can approve in Agent mode.",
    "agent": "",
    "auto": "\nMode: AUTO — your tool calls run without per-action approval. "
            "Be extra careful with destructive commands.",
}

TOOLS = [
    {
        "name": "run_command",
        "description": "Run a shell command on the server (bash). Returns stdout+stderr (truncated).",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run"},
                "timeout": {"type": "integer", "description": "Seconds, default 60, max 300"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from the server.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write/overwrite a text file on the server. Creates parent dirs.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List a directory on the server.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]

SAFE_TOOLS = {"read_file", "list_dir"}
MODE_TOOLS = {
    "chat": [],
    "plan": ["run_command", "read_file", "list_dir"],
    "agent": [t["name"] for t in TOOLS],
    "auto": [t["name"] for t in TOOLS],
}

# Approximate USD per 1M tokens (input, output) — for display only.
STATIC_PRICING = {
    "claude-sonnet-5": (3, 15), "claude-sonnet-4": (3, 15),
    "claude-opus": (15, 75), "claude-haiku-4.5": (1, 5), "claude-haiku": (0.8, 4),
    "gpt-5.2": (1.75, 14), "gpt-5-mini": (0.25, 2), "gpt-5": (1.25, 10),
    "gemini-3.1-pro": (2, 12), "gemini-3-flash": (0.3, 2.5),
    "deepseek": (0.5, 1.5), "mistral-large": (2, 6),
}
_openrouter_pricing: dict[str, tuple[float, float]] = {}


def estimate_cost(provider: str, model: str, input_tok: int, output_tok: int) -> float | None:
    pricing = None
    if provider == "openrouter" and model in _openrouter_pricing:
        pricing = _openrouter_pricing[model]
    else:
        key = model.split("/")[-1].lower()
        for sub, p in STATIC_PRICING.items():
            if sub in key:
                pricing = p
                break
    if not pricing:
        return None
    return (input_tok * pricing[0] + output_tok * pricing[1]) / 1_000_000


def system_prompt(workdir: str, mode: str) -> str:
    in_container = os.path.exists("/.dockerenv")
    return SYSTEM_PROMPT.format(
        where="inside the Helmsman container" if in_container else "directly on the host",
        workdir=workdir,
        host_note=(". The host filesystem is mounted read-only at /host"
                   if os.path.isdir("/host") else ""),
        mode_note=MODE_NOTES.get(mode, ""),
    )


# ---------------------------------------------------------------- tool exec

async def execute_tool(name: str, args: dict, workdir: str) -> str:
    try:
        if name == "run_command":
            timeout = min(int(args.get("timeout") or 60), 300)
            proc = await asyncio.create_subprocess_shell(
                args["command"], stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, cwd=workdir,
                executable="/bin/bash")
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return f"[timed out after {timeout}s]"
            text = out.decode("utf-8", "replace")
            if proc.returncode != 0:
                text += f"\n[exit code: {proc.returncode}]"
            return _truncate(text) or "[no output]"
        if name == "read_file":
            p = Path(args["path"])
            if not p.is_absolute():
                p = Path(workdir) / p
            return _truncate(p.read_text(errors="replace"))
        if name == "write_file":
            p = Path(args["path"])
            if not p.is_absolute():
                p = Path(workdir) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"Wrote {len(args['content'])} bytes to {p}"
        if name == "list_dir":
            p = Path(args.get("path") or workdir)
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name))
            return _truncate("\n".join(
                (e.name + "/" if e.is_dir() else e.name) for e in entries) or "[empty]")
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + f"\n… [truncated, {len(text)} chars total]"
    return text


# ------------------------------------------------------------- providers
# Internal message format:
#   {"role":"user"|"assistant","content":str,"tool_calls":[{"id","name","args"}]?}
#   {"role":"tool","tool_call_id":str,"content":str}
# Adapters yield: ("text", delta) | ("tool_call", {...}) | ("usage", {...}) | ("stop", reason)

def _cfg_for(provider: str, model: str) -> dict:
    key = config.get_key(provider)
    if not key:
        raise RuntimeError(f"No API key configured for {provider}. Add one under Settings → AI.")
    return {"provider": provider, "api_key": key,
            "model": model or config.DEFAULT_MODELS.get(provider, ""),
            "base_url": config.get_base_url(provider)}


def _filter_tools(tool_names: list[str]) -> list[dict]:
    return [t for t in TOOLS if t["name"] in tool_names]


async def stream_anthropic(cfg: dict, messages: list, sysprompt: str, tool_names: list[str]):
    tools = [{"name": t["name"], "description": t["description"],
              "input_schema": t["parameters"]} for t in _filter_tools(tool_names)]
    api_messages = []
    for m in messages:
        if m["role"] == "user":
            api_messages.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc["id"],
                               "name": tc["name"], "input": tc["args"]})
            if blocks:
                api_messages.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            api_messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                 "content": m["content"]}]})
    body = {
        "model": cfg["model"], "max_tokens": 4096, "system": sysprompt,
        "messages": _merge_consecutive(api_messages), "stream": True,
    }
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    url = (cfg["base_url"] or "https://api.anthropic.com") + "/v1/messages"

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"API error {resp.status_code}: {(await resp.aread()).decode()[:500]}")
            current_tool, tool_json, stop = None, "", "end"
            usage = {"input": 0, "output": 0}
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                t = ev.get("type")
                if t == "message_start":
                    usage["input"] = ev["message"].get("usage", {}).get("input_tokens", 0)
                elif t == "content_block_start" and ev["content_block"]["type"] == "tool_use":
                    current_tool = {"id": ev["content_block"]["id"],
                                    "name": ev["content_block"]["name"]}
                    tool_json = ""
                elif t == "content_block_delta":
                    d = ev["delta"]
                    if d.get("type") == "text_delta":
                        yield ("text", d["text"])
                    elif d.get("type") == "input_json_delta":
                        tool_json += d["partial_json"]
                elif t == "content_block_stop" and current_tool:
                    current_tool["args"] = json.loads(tool_json or "{}")
                    yield ("tool_call", current_tool)
                    current_tool = None
                elif t == "message_delta":
                    stop = ev["delta"].get("stop_reason") or stop
                    usage["output"] = ev.get("usage", {}).get("output_tokens", usage["output"])
            yield ("usage", usage)
            yield ("stop", stop)


def _merge_consecutive(msgs: list) -> list:
    """Anthropic requires alternating roles; merge consecutive same-role messages."""
    out: list = []
    for m in msgs:
        if out and out[-1]["role"] == m["role"]:
            a, b = out[-1]["content"], m["content"]
            if isinstance(a, str):
                a = [{"type": "text", "text": a}]
            if isinstance(b, str):
                b = [{"type": "text", "text": b}]
            out[-1]["content"] = a + b
        else:
            out.append(dict(m))
    return out


def _openai_base(cfg: dict) -> str:
    return cfg["base_url"] or (
        "https://openrouter.ai/api/v1" if cfg["provider"] == "openrouter"
        else "https://api.openai.com/v1")


async def stream_openai(cfg: dict, messages: list, sysprompt: str, tool_names: list[str]):
    tools = [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in _filter_tools(tool_names)]
    api_messages = [{"role": "system", "content": sysprompt}]
    for m in messages:
        if m["role"] == "assistant":
            entry: dict = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                entry["tool_calls"] = [{"id": tc["id"], "type": "function", "function": {
                    "name": tc["name"], "arguments": json.dumps(tc["args"])}}
                    for tc in m["tool_calls"]]
            api_messages.append(entry)
        elif m["role"] == "tool":
            api_messages.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                                 "content": m["content"]})
        else:
            api_messages.append({"role": "user", "content": m["content"]})
    body = {"model": cfg["model"], "messages": api_messages, "stream": True,
            "stream_options": {"include_usage": True}}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", _openai_base(cfg) + "/chat/completions",
                                 json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"API error {resp.status_code}: {(await resp.aread()).decode()[:500]}")
            tool_calls: dict[int, dict] = {}
            stop = "stop"
            usage = {"input": 0, "output": 0}
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                ev = json.loads(line[6:])
                if ev.get("usage"):
                    usage["input"] = ev["usage"].get("prompt_tokens", 0)
                    usage["output"] = ev["usage"].get("completion_tokens", 0)
                if not ev.get("choices"):
                    continue
                choice = ev["choices"][0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield ("text", delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = tool_calls.setdefault(tc["index"], {"id": "", "name": "", "args_raw": ""})
                    fn = tc.get("function") or {}
                    slot["id"] = tc.get("id") or slot["id"]
                    slot["name"] = fn.get("name") or slot["name"]
                    slot["args_raw"] += fn.get("arguments") or ""
                if choice.get("finish_reason"):
                    stop = choice["finish_reason"]
            for slot in tool_calls.values():
                try:
                    args = json.loads(slot["args_raw"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield ("tool_call", {"id": slot["id"], "name": slot["name"], "args": args})
            yield ("usage", usage)
            yield ("stop", "tool_use" if tool_calls else stop)


def get_stream(cfg: dict, messages: list, sysprompt: str, tool_names: list[str]):
    if cfg["provider"] == "anthropic":
        return stream_anthropic(cfg, messages, sysprompt, tool_names)
    return stream_openai(cfg, messages, sysprompt, tool_names)


# ------------------------------------------------------------ model list

CURATED = {
    "anthropic": ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-5.2", "gpt-5.2-mini", "gpt-5-mini"],
    "openrouter": ["anthropic/claude-sonnet-4.5", "anthropic/claude-haiku-4.5",
                   "openai/gpt-5.2", "google/gemini-3.1-pro-preview",
                   "google/gemini-3-flash", "deepseek/deepseek-chat-v3.1",
                   "mistralai/mistral-large-2512", "qwen/qwen3-coder"],
}
_model_cache: dict = {"time": 0, "result": None}


async def list_models() -> list[dict]:
    """Available models per configured provider (live where possible)."""
    if _model_cache["result"] and time.time() - _model_cache["time"] < 3600:
        return _model_cache["result"]
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for provider in config.configured_providers():
            key = config.get_key(provider)
            models: list[dict] = []
            try:
                if provider == "anthropic":
                    r = await client.get(
                        (config.get_base_url(provider) or "https://api.anthropic.com") + "/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
                    if r.status_code == 200:
                        models = [{"id": m["id"], "name": m.get("display_name", m["id"])}
                                  for m in r.json().get("data", [])][:20]
                elif provider == "openrouter":
                    r = await client.get("https://openrouter.ai/api/v1/models")
                    if r.status_code == 200:
                        allow = ("anthropic/", "openai/", "google/", "deepseek/",
                                 "mistralai/", "qwen/", "meta-llama/", "x-ai/")
                        for m in r.json().get("data", []):
                            if not m["id"].startswith(allow) or ":free" in m["id"]:
                                continue
                            pr = m.get("pricing", {})
                            try:
                                _openrouter_pricing[m["id"]] = (
                                    float(pr.get("prompt", 0)) * 1e6,
                                    float(pr.get("completion", 0)) * 1e6)
                            except (TypeError, ValueError):
                                pass
                            models.append({"id": m["id"], "name": m.get("name", m["id"])})
                        models = models[:60]
            except Exception:
                models = []
            if not models:
                models = [{"id": m, "name": m} for m in CURATED.get(provider, [])]
            out.append({"provider": provider, "models": models})
    _model_cache.update(time=time.time(), result=out)
    return out


# ------------------------------------------------------------- agent loop

class ChatSession:
    """One WebSocket = one conversation. Runs the agent loop with approvals."""

    def __init__(self, ws):
        self.ws = ws
        self.messages: list = []
        self.mode = "agent"
        default = config.get_ai_default()
        self.provider = default["provider"]
        self.model = default["model"]
        self.workdir = DEFAULT_WORKDIR
        self.pending: dict[str, asyncio.Future] = {}
        self.session_usage = {"input": 0, "output": 0, "cost": 0.0, "turns": 0}

    async def send(self, **event) -> None:
        await self.ws.send_text(json.dumps(event))

    async def handle_client_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "user":
            await self.run_turn(msg.get("text", ""))
        elif t == "config":
            if msg.get("mode") in MODE_TOOLS:
                self.mode = msg["mode"]
            if msg.get("provider") in config.PROVIDERS:
                self.provider = msg["provider"]
            if "model" in msg:
                self.model = msg["model"] or config.DEFAULT_MODELS.get(self.provider, "")
            if msg.get("workdir"):
                self.workdir = self._safe_workdir(msg["workdir"])
        elif t == "approve":
            fut = self.pending.pop(msg.get("id", ""), None)
            if fut and not fut.done():
                fut.set_result(bool(msg.get("approved")))
        elif t == "reset":
            self.messages = []
            self.session_usage = {"input": 0, "output": 0, "cost": 0.0, "turns": 0}

    def _safe_workdir(self, path: str) -> str:
        allowed = config.get_workspaces() + [DEFAULT_WORKDIR]
        resolved = os.path.realpath(path)
        for root in allowed:
            if resolved == os.path.realpath(root) or \
               resolved.startswith(os.path.realpath(root) + os.sep):
                return resolved if os.path.isdir(resolved) else self.workdir
        return self.workdir

    async def run_turn(self, user_text: str) -> None:
        if not self.provider:
            await self.send(type="error",
                            message="No AI API key configured. Add one under Settings → AI.")
            return
        try:
            cfg = _cfg_for(self.provider, self.model)
        except RuntimeError as e:
            await self.send(type="error", message=str(e))
            return
        self.messages.append({"role": "user", "content": user_text})
        sysprompt = system_prompt(self.workdir, self.mode)
        tool_names = MODE_TOOLS[self.mode]
        turn_usage = {"input": 0, "output": 0}
        try:
            for _ in range(MAX_TURNS):
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                async for kind, payload in get_stream(cfg, self.messages, sysprompt, tool_names):
                    if kind == "text":
                        text_parts.append(payload)
                        await self.send(type="text", delta=payload)
                    elif kind == "tool_call":
                        tool_calls.append(payload)
                    elif kind == "usage":
                        turn_usage["input"] += payload["input"]
                        turn_usage["output"] += payload["output"]
                self.messages.append({"role": "assistant",
                                      "content": "".join(text_parts),
                                      "tool_calls": tool_calls})
                if not tool_calls:
                    break
                for tc in tool_calls:
                    output = await self._run_tool_with_approval(tc)
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                          "content": output})
            cost = estimate_cost(cfg["provider"], cfg["model"],
                                 turn_usage["input"], turn_usage["output"])
            self.session_usage["input"] += turn_usage["input"]
            self.session_usage["output"] += turn_usage["output"]
            self.session_usage["turns"] += 1
            if cost is not None:
                self.session_usage["cost"] += cost
            _persist_usage(cfg, turn_usage, cost)
            await self.send(type="usage",
                            turn={**turn_usage, "cost": cost, "model": cfg["model"]},
                            session=self.session_usage)
            await self.send(type="done")
        except Exception as e:
            await self.send(type="error", message=str(e))

    async def _run_tool_with_approval(self, tc: dict) -> str:
        auto = self.mode == "auto" or (self.mode in ("agent", "plan") and tc["name"] in SAFE_TOOLS)
        if not auto:
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self.pending[tc["id"]] = fut
            await self.send(type="tool_request", id=tc["id"], name=tc["name"], args=tc["args"])
            try:
                approved = await asyncio.wait_for(fut, timeout=600)
            except asyncio.TimeoutError:
                approved = False
            if not approved:
                await self.send(type="tool_result", id=tc["id"], output="[denied by user]")
                return "The user declined this action. Ask how they want to proceed."
        await self.send(type="tool_start", id=tc["id"], name=tc["name"], args=tc["args"])
        output = await execute_tool(tc["name"], tc["args"], self.workdir)
        await self.send(type="tool_result", id=tc["id"], output=output)
        return output


# ------------------------------------------------------------ usage log

_USAGE_FILE = config.DATA_DIR / "usage.json"


def _persist_usage(cfg: dict, usage: dict, cost: float | None) -> None:
    try:
        data = json.loads(_USAGE_FILE.read_text()) if _USAGE_FILE.exists() else {}
        day = time.strftime("%Y-%m-%d")
        slot = data.setdefault(day, {"input": 0, "output": 0, "cost": 0.0, "requests": 0})
        slot["input"] += usage["input"]
        slot["output"] += usage["output"]
        slot["requests"] += 1
        if cost:
            slot["cost"] = round(slot["cost"] + cost, 6)
        # keep last 60 days
        for old in sorted(data)[:-60]:
            del data[old]
        _USAGE_FILE.write_text(json.dumps(data, indent=1))
    except Exception:
        pass


def usage_summary() -> dict:
    try:
        data = json.loads(_USAGE_FILE.read_text()) if _USAGE_FILE.exists() else {}
    except Exception:
        data = {}
    today = time.strftime("%Y-%m-%d")
    month = today[:7]
    month_slot = {"input": 0, "output": 0, "cost": 0.0, "requests": 0}
    for day, slot in data.items():
        if day.startswith(month):
            for k in month_slot:
                month_slot[k] += slot.get(k, 0)
    return {"today": data.get(today, {"input": 0, "output": 0, "cost": 0.0, "requests": 0}),
            "month": month_slot}


async def one_shot(prompt: str, system: str = "") -> str:
    """Single non-streaming completion without tools (updates/reports helpers)."""
    default = config.get_ai_default()
    if not default["provider"]:
        raise RuntimeError("No AI API key configured")
    cfg = _cfg_for(default["provider"], default["model"])
    if cfg["provider"] == "anthropic":
        url = (cfg["base_url"] or "https://api.anthropic.com") + "/v1/messages"
        body = {"model": cfg["model"], "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}]
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(_openai_base(cfg) + "/chat/completions",
                              json={"model": cfg["model"], "messages": msgs},
                              headers={"Authorization": f"Bearer {cfg['api_key']}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
