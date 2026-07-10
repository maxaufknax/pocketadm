"""Vibe Code — an AI agent that works on the server via tool use.

Provider-agnostic (Anthropic native API or any OpenAI-compatible endpoint
such as OpenRouter). Streams over WebSocket with a JSON event protocol:

  server -> client:
    {"type":"text","delta":str}           assistant text chunk
    {"type":"tool_request","id","name","args"}   needs approval (manual mode)
    {"type":"tool_start","id","name","args"}     executing now
    {"type":"tool_result","id","output"}
    {"type":"done"} | {"type":"error","message"}
  client -> server:
    {"type":"user","text":str}
    {"type":"approve","id":str,"approved":bool}
    {"type":"set_auto","auto":bool}
"""
import asyncio
import json
import os
from pathlib import Path

import httpx

from . import config

MAX_TOOL_OUTPUT = 12000
MAX_TURNS = 25
WORKDIR = os.environ.get("HELMSMAN_WORKDIR", "/host" if os.path.isdir("/host") else os.path.expanduser("~"))

SYSTEM_PROMPT = """You are Vibe Code, the built-in AI engineer of Helmsman, a self-hosted \
server management app. You work directly on the user's server through tools.

Environment: you run {where}. Your working directory is {workdir}. \
The docker CLI is available and controls the host's Docker engine{host_note}.

Guidelines:
- Be concise; the user is often on a phone. Prefer short answers and small steps.
- Inspect before you change: read files / list dirs / check state first.
- For destructive actions (rm, docker rm, overwriting configs), state what you are about to do first.
- When you finish a task, summarize in 1-3 sentences what you changed.
- Answer in the language the user writes in."""

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

SAFE_TOOLS = {"read_file", "list_dir"}  # auto-approved even in manual mode


def system_prompt() -> str:
    in_container = os.path.exists("/.dockerenv")
    return SYSTEM_PROMPT.format(
        where="inside the Helmsman container" if in_container else "directly on the host",
        workdir=WORKDIR,
        host_note=(". The host filesystem is mounted read-only at /host"
                   if os.path.isdir("/host") else ""),
    )


# ---------------------------------------------------------------- tool exec

async def execute_tool(name: str, args: dict) -> str:
    try:
        if name == "run_command":
            timeout = min(int(args.get("timeout") or 60), 300)
            proc = await asyncio.create_subprocess_shell(
                args["command"], stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, cwd=WORKDIR,
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
                p = Path(WORKDIR) / p
            return _truncate(p.read_text(errors="replace"))
        if name == "write_file":
            p = Path(args["path"])
            if not p.is_absolute():
                p = Path(WORKDIR) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"Wrote {len(args['content'])} bytes to {p}"
        if name == "list_dir":
            p = Path(args.get("path") or WORKDIR)
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
# Each provider adapter is an async generator yielding:
#   ("text", delta) | ("tool_call", {"id","name","args"}) | ("stop", reason)

async def stream_anthropic(cfg: dict, messages: list) -> "asyncio.AsyncIterator":
    tools = [{"name": t["name"], "description": t["description"],
              "input_schema": t["parameters"]} for t in TOOLS]
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
            api_messages.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            api_messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                 "content": m["content"]}]})
    body = {
        "model": cfg["model"] or config.DEFAULT_MODELS["anthropic"],
        "max_tokens": 4096, "system": system_prompt(),
        "messages": _merge_consecutive(api_messages), "tools": tools, "stream": True,
    }
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    url = (cfg["base_url"] or "https://api.anthropic.com") + "/v1/messages"

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"API error {resp.status_code}: {(await resp.aread()).decode()[:500]}")
            current_tool, tool_json, stop = None, "", "end"
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                t = ev.get("type")
                if t == "content_block_start" and ev["content_block"]["type"] == "tool_use":
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


async def stream_openai(cfg: dict, messages: list) -> "asyncio.AsyncIterator":
    tools = [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in TOOLS]
    api_messages = [{"role": "system", "content": system_prompt()}]
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
    base = cfg["base_url"] or (
        "https://openrouter.ai/api/v1" if cfg["provider"] == "openrouter"
        else "https://api.openai.com/v1")
    body = {"model": cfg["model"] or config.DEFAULT_MODELS.get(cfg["provider"], ""),
            "messages": api_messages, "tools": tools, "stream": True}
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", base.rstrip("/") + "/chat/completions",
                                 json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"API error {resp.status_code}: {(await resp.aread()).decode()[:500]}")
            tool_calls: dict[int, dict] = {}
            stop = "stop"
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                ev = json.loads(line[6:])
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
            yield ("stop", "tool_use" if tool_calls else stop)


def get_stream(cfg: dict, messages: list):
    if cfg["provider"] == "anthropic":
        return stream_anthropic(cfg, messages)
    return stream_openai(cfg, messages)


# ------------------------------------------------------------- agent loop

class ChatSession:
    """One WebSocket = one conversation. Runs the agent loop with approvals."""

    def __init__(self, ws):
        self.ws = ws
        self.messages: list = []
        self.auto_approve = False
        self.pending: dict[str, asyncio.Future] = {}

    async def send(self, **event) -> None:
        await self.ws.send_text(json.dumps(event))

    async def handle_client_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "user":
            await self.run_turn(msg.get("text", ""))
        elif t == "set_auto":
            self.auto_approve = bool(msg.get("auto"))
        elif t == "approve":
            fut = self.pending.pop(msg.get("id", ""), None)
            if fut and not fut.done():
                fut.set_result(bool(msg.get("approved")))
        elif t == "reset":
            self.messages = []

    async def run_turn(self, user_text: str) -> None:
        cfg = config.get_ai_config()
        if not cfg["api_key"]:
            await self.send(type="error",
                            message="No AI API key configured. Add one under Settings → AI.")
            return
        self.messages.append({"role": "user", "content": user_text})
        try:
            for _ in range(MAX_TURNS):
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                async for kind, payload in get_stream(cfg, self.messages):
                    if kind == "text":
                        text_parts.append(payload)
                        await self.send(type="text", delta=payload)
                    elif kind == "tool_call":
                        tool_calls.append(payload)
                self.messages.append({"role": "assistant",
                                      "content": "".join(text_parts),
                                      "tool_calls": tool_calls})
                if not tool_calls:
                    break
                for tc in tool_calls:
                    output = await self._run_tool_with_approval(tc)
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                          "content": output})
            await self.send(type="done")
        except Exception as e:
            await self.send(type="error", message=str(e))

    async def _run_tool_with_approval(self, tc: dict) -> str:
        needs_approval = not self.auto_approve and tc["name"] not in SAFE_TOOLS
        if needs_approval:
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
        output = await execute_tool(tc["name"], tc["args"])
        await self.send(type="tool_result", id=tc["id"], output=output)
        return output


async def one_shot(prompt: str, system: str = "") -> str:
    """Single non-streaming completion without tools (used by update explainer)."""
    cfg = config.get_ai_config()
    if not cfg["api_key"]:
        raise RuntimeError("No AI API key configured")
    if cfg["provider"] == "anthropic":
        url = (cfg["base_url"] or "https://api.anthropic.com") + "/v1/messages"
        body = {"model": cfg["model"] or config.DEFAULT_MODELS["anthropic"],
                "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
    base = cfg["base_url"] or (
        "https://openrouter.ai/api/v1" if cfg["provider"] == "openrouter"
        else "https://api.openai.com/v1")
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}]
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(base.rstrip("/") + "/chat/completions",
                              json={"model": cfg["model"] or config.DEFAULT_MODELS.get(cfg["provider"], ""),
                                    "messages": msgs},
                              headers={"Authorization": f"Bearer {cfg['api_key']}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
