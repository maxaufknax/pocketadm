"""Vibe Code — an AI agent that works on the server via tool use.

Provider-agnostic (Anthropic native API or any OpenAI-compatible endpoint
such as OpenRouter). Streams over WebSocket with a JSON event protocol:

  server -> client:
    {"type":"text","delta":str}                  assistant text chunk
    {"type":"thinking","delta":str}              reasoning chunk (if enabled)
    {"type":"tool_request","id","name","args"}   needs approval
    {"type":"tool_start","id","name","args"}     executing now
    {"type":"tool_result","id","output"}
    {"type":"usage","turn":{...},"session":{...}}
    {"type":"stopped"}                            turn cancelled by user
    {"type":"done"} | {"type":"error","message"}
  client -> server:
    {"type":"user","text":str}
    {"type":"config","mode","provider","model","workdir","thinking"}  (any subset)
    {"type":"approve","id":str,"approved":bool}
    {"type":"stop"}
    {"type":"reset"}

Modes:
  chat  — conversation only, no tools
  plan  — read-only investigation (safe tools auto), proposes a plan
  agent — full tools, destructive/write actions need approval   (default)
  auto  — full tools, everything auto-approved

The agent has a persistent memory file (like Claude Code's CLAUDE.md):
its content is injected into every system prompt and the agent can update
it with the update_memory tool — so it learns about this server over time.
"""
import asyncio
import difflib
import json
import os
import re
import time
from pathlib import Path

import httpx

from . import audit, chats, config, integrations, localai

MAX_TOOL_OUTPUT = 12000
MAX_TURNS = 25
# Absolute ceiling on tool iterations for a single user prompt. The agent no
# longer stops silently at MAX_TURNS (that is a soft checkpoint now); this is the
# runaway/cost safety net that always forces a pause even in autonomous mode.
HARD_MAX_ITERATIONS = 300
DEFAULT_WORKDIR = os.environ.get(
    "HELMSMAN_WORKDIR", "/host" if os.path.isdir("/host") else os.path.expanduser("~"))

MEMORY_FILE = config.DATA_DIR / "agent-memory.md"
MEMORY_MAX_CHARS = 6000

SYSTEM_PROMPT = """You are Vibe Code, the built-in AI engineer of Helmsman, a self-hosted \
server management app. You work directly on the user's server through tools.

Environment: you run {where}. Your working directory is {workdir}. \
The docker CLI is available and controls the host's Docker engine{host_note}.

Guidelines:
- Be concise; the user is often on a phone. Prefer short answers and small steps.
- Inspect before you change: read files / list dirs / check state first.
- For any task with more than ~2 steps, call update_plan first to show the user a short \
plan, and keep it updated (one step in_progress at a time) so they can follow along.
- For destructive actions (rm, docker rm, overwriting configs), state what you are about to do first.
- When you finish a task, summarize in 1-3 sentences what you changed.
- Answer in the language the user writes in.
- When you learn something durable about this server (layout, conventions, the user's \
preferences, recurring problems and their fixes), save it with update_memory so future \
sessions know it. Keep memory short and factual; update or remove stale entries.
{memory_section}{mode_note}"""

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
        "name": "edit_file",
        "description": "Replace an exact text snippet in a file. old_text must match exactly "
                       "once (or set replace_all). Safer than rewriting whole files.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "list_dir",
        "description": "List a directory on the server (name, type, size).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search file contents recursively (grep -rn). Returns matching "
                       "lines with file:line prefixes.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "regex / text to search"},
                "path": {"type": "string", "description": "directory, default workdir"},
                "glob": {"type": "string", "description": "filename filter like *.yml"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "fetch_url",
        "description": "HTTP GET a URL (docs, changelogs, APIs). Returns text, truncated.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "integration_request",
        "description": "Call an API the user connected under Settings → Integrations "
                       "(DNS providers like deSEC/IONOS/GoDaddy/Cloudflare, or any generic "
                       "API). Auth headers are injected server-side; give the path relative "
                       "to the integration's base URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "integration": {"type": "string", "description": "name of the configured integration"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "path": {"type": "string", "description": "e.g. /domains/ or /zones"},
                "body": {"type": "string", "description": "JSON body for write requests"},
            },
            "required": ["integration", "path"],
        },
    },
    {
        "name": "update_plan",
        "description": "Publish or update a short, visible to-do plan for the current task, "
                       "shown to the user beside the chat so they can follow along. Call it "
                       "once you start any multi-step task, and again whenever a step's status "
                       "changes (mark exactly one step in_progress at a time). Keep 2-8 concise "
                       "steps. Display-only: it performs no work itself.",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered plan steps.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "update_memory",
        "description": "Update your persistent memory about this server (shown to you in "
                       "every future session). mode 'append' adds a line/section, "
                       "'replace' rewrites the whole memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["append", "replace"]},
            },
            "required": ["content"],
        },
    },
]

SAFE_TOOLS = {"read_file", "list_dir", "search_files", "fetch_url",
              "update_memory", "update_plan"}
MODE_TOOLS = {
    "chat": [],
    "plan": ["run_command", "read_file", "list_dir", "search_files", "fetch_url", "update_plan"],
    "agent": [t["name"] for t in TOOLS],
    "auto": [t["name"] for t in TOOLS],
}

# providers selectable in a chat: the key-based cloud ones plus local Ollama
CHAT_PROVIDERS = config.PROVIDERS + ("ollama",)


def tool_docs() -> list[dict]:
    """Tool overview for the settings UI, incl. on/off state."""
    disabled = set(config.get_disabled_tools())
    return [{"name": t["name"], "description": t["description"],
             "safe": t["name"] in SAFE_TOOLS,
             "mutates": t["name"] not in SAFE_TOOLS,
             "enabled": t["name"] not in disabled} for t in TOOLS]


def allowed_tools(mode: str) -> list[str]:
    """Tools available in a mode after the user's on/off choices are applied."""
    disabled = set(config.get_disabled_tools())
    return [t for t in MODE_TOOLS.get(mode, []) if t not in disabled]


# ------------------------------------------------------------- memory

def read_memory() -> str:
    try:
        return MEMORY_FILE.read_text() if MEMORY_FILE.exists() else ""
    except OSError:
        return ""


def save_memory(content: str) -> None:
    MEMORY_FILE.write_text(content[:MEMORY_MAX_CHARS * 4])


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
    instructions = config.get_custom_instructions().strip()
    instr_section = ""
    if instructions:
        instr_section = ("\nStanding instructions from the server owner — follow them "
                         "unless they conflict with safety:\n<instructions>\n"
                         + instructions[:config.CUSTOM_INSTRUCTIONS_MAX] + "\n</instructions>\n")
    memory = read_memory().strip()
    memory_section = ""
    if memory:
        memory_section = ("\nYour memory about this server (from previous sessions):\n"
                          "<memory>\n" + memory[:MEMORY_MAX_CHARS] + "\n</memory>\n")
    return SYSTEM_PROMPT.format(
        where="inside the Helmsman container" if in_container else "directly on the host",
        workdir=workdir,
        host_note=(". The host filesystem is mounted read-only at /host"
                   if os.path.isdir("/host") else ""),
        memory_section=instr_section + memory_section + integrations.prompt_section(),
        mode_note=MODE_NOTES.get(mode, ""),
    )


# ---------------------------------------------------------------- tool exec

async def execute_tool(name: str, args: dict, workdir: str) -> str:
    try:
        if name == "run_command":
            # Self-preservation guard: refuse to stop our own infrastructure
            cmd = (args.get("command") or "").strip()
            if _SELF_HOSTNAME and _SELF_DOCKER_BLACKLIST.search(cmd):
                return (
                    "[blocked: this command targets the Helmsman container itself "
                    "or the nginx-proxy-manager that routes to it. "
                    "Stopping these would take the UI offline. "
                    "Ask the user to run this manually instead.]"
                )
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
            return _truncate(_resolve(args["path"], workdir).read_text(errors="replace"))
        if name == "write_file":
            p = _resolve(args["path"], workdir)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"Wrote {len(args['content'])} bytes to {p}"
        if name == "edit_file":
            p = _resolve(args["path"], workdir)
            text = p.read_text(errors="replace")
            old, new = args["old_text"], args["new_text"]
            n = text.count(old)
            if n == 0:
                return "Error: old_text not found in file (must match exactly, incl. whitespace)"
            if n > 1 and not args.get("replace_all"):
                return f"Error: old_text occurs {n} times — provide more context or set replace_all"
            p.write_text(text.replace(old, new) if args.get("replace_all")
                         else text.replace(old, new, 1))
            return f"Replaced {n if args.get('replace_all') else 1} occurrence(s) in {p}"
        if name == "list_dir":
            p = Path(args.get("path") or workdir)
            if not p.is_absolute():
                p = Path(workdir) / p
            lines = []
            for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
                try:
                    size = "" if e.is_dir() else f"  {e.stat().st_size}"
                except OSError:
                    size = ""
                lines.append((e.name + "/" if e.is_dir() else e.name) + size)
            return _truncate("\n".join(lines) or "[empty]")
        if name == "search_files":
            path = args.get("path") or workdir
            cmd = ["grep", "-rn", "-I", "--max-count=200", "-e", args["pattern"]]
            if args.get("glob"):
                cmd.append("--include=" + args["glob"])
            cmd += ["--exclude-dir=.git", "--exclude-dir=node_modules",
                    "--exclude-dir=.venv", path]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), 30)
            except asyncio.TimeoutError:
                proc.kill()
                return "[search timed out]"
            return _truncate(out.decode("utf-8", "replace")) or "[no matches]"
        if name == "fetch_url":
            url = args["url"]
            if not url.startswith(("http://", "https://")):
                return "Error: only http(s) URLs"
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "Helmsman-Agent/1.0"})
            text = r.text
            if "text/html" in r.headers.get("content-type", ""):
                text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", text)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s{3,}", "\n", text)
            return _truncate(f"[{r.status_code}] " + text.strip())
        if name == "integration_request":
            return await integrations.request(
                args["integration"], args.get("method", "GET"),
                args.get("path", ""), args.get("body", ""))
        if name == "update_memory":
            mode = args.get("mode", "append")
            if mode == "replace":
                save_memory(args["content"])
            else:
                current = read_memory()
                save_memory((current.rstrip() + "\n" if current.strip() else "") +
                            args["content"].strip() + "\n")
            return f"Memory updated ({mode}), now {len(read_memory())} chars."
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def _tool_audit_detail(name: str, args: dict) -> str:
    if name == "run_command":
        return (args.get("command") or "")[:200]
    if name == "integration_request":
        return f"{args.get('method', 'GET')} {args.get('integration', '')}{args.get('path', '')}"
    if name in ("write_file", "edit_file"):
        return args.get("path", "")
    if name == "update_memory":
        return args.get("mode", "append") + " memory"
    return (args.get("path") or "")[:200]


def _sensitive_call(tc: dict) -> bool:
    """Credential-touching writes always need a human tap, even in Auto mode."""
    if tc.get("name") != "integration_request":
        return False
    method = (tc.get("args", {}).get("method") or "GET").upper()
    return method not in ("GET", "HEAD", "OPTIONS")


# Signatures that mean "the action failed because the agent lacks a permission",
# not because the command itself was wrong. Surfaced to the user as an
# actionable request instead of a dead-end tool error.
_PERM_SIGNS = ("permission denied", "read-only file system", "operation not permitted",
               "permissionerror", "eacces", "must be root", "are you root",
               "not permitted", "sudo: a password is required", "sudo: no tty",
               "got permission denied while trying to connect to the docker")

# Self-preservation: the agent must never stop/kill/remove the Helmsman
# container itself (or the nginx-proxy-manager it routes through), since
# it runs inside it and has the Docker socket mounted.
_SELF_HOSTNAME = os.environ.get("HOSTNAME", "")
_SELF_DOCKER_BLACKLIST = re.compile(
    rf"\b(?:docker\s+(?:stop|kill|rm|restart|pause|compose\s+down)\s+.*?"
    rf"(?:{'|'.join(
        re.escape(n) for n in [
            _SELF_HOSTNAME, "helmsman",
            "nginx-proxy-manager", "npm-app",
            "openresty",
        ]
        if n
    )})"
    rf"|docker-compose\s+(?:down|stop)\b)",
    re.IGNORECASE,
)


def detect_permission_issue(name: str, args: dict, output: str) -> dict | None:
    """Classify a failed tool result as a missing-permission situation and
    return a plain-language request (kind/title/detail/explanation/risk/fix),
    or None if it does not look permission-related."""
    low = (output or "").lower()
    if not any(s in low for s in _PERM_SIGNS):
        return None
    detail = _tool_audit_detail(name, args) or (args.get("path") or args.get("command") or "")
    detail = detail[:200]
    if "read-only file system" in low:
        return {
            "kind": "host-fs-readonly",
            "title": "Write access to the host filesystem",
            "detail": detail,
            "explanation": "The host filesystem is mounted read-only at /host, so the agent "
                           "can inspect host files but not change them. To let it edit host "
                           "files, the /host mount in Helmsman's docker-compose must be changed "
                           "from read-only (:ro) to read-write and the container recreated.",
            "risk": "The agent would then be able to modify (or delete) any file on the host, "
                    "not just this one. Only grant it if you trust the tasks you hand it.",
            "fix": "Edit helmsman/docker-compose.yml: change the `/:/host:ro` volume to `/:/host` "
                   "and run `docker compose up -d`.",
        }
    if "docker" in low and "permission denied" in low:
        return {
            "kind": "docker-permission",
            "title": "Access to the Docker daemon",
            "detail": detail,
            "explanation": "The command needs to talk to the Docker daemon but the current user "
                           "isn't allowed to (not in the docker group / no socket access).",
            "risk": "Docker access is effectively root on the host. Grant deliberately.",
            "fix": "Add the user to the docker group, or run the action from a context that has "
                   "socket access.",
        }
    if any(s in low for s in ("must be root", "are you root", "operation not permitted",
                              "sudo: a password", "sudo: no tty", "not permitted")):
        return {
            "kind": "needs-root",
            "title": "Elevated (root) privileges",
            "detail": detail,
            "explanation": "This action needs root/administrator privileges that the agent "
                           "doesn't currently have in this context.",
            "risk": "Root can change anything on the system. Review the exact command before "
                    "granting.",
            "fix": "Run the command with the necessary privileges, or open a focused session "
                   "to set up the access properly.",
        }
    return {
        "kind": "permission",
        "title": "A missing permission",
        "detail": detail,
        "explanation": "The action failed because the agent lacks the permission it needs.",
        "risk": "Review what the action would do before granting access.",
        "fix": "Grant the needed access, or open a focused session to resolve it.",
    }


def _resolve(path: str, workdir: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(workdir) / p


# ------------------------------------------------- file-edit diffs (for the UI)
# So the user can *see* what the agent changed — how many lines added/removed and
# a colored patch — not just "wrote N bytes". Computed around the actual write.

FILE_WRITE_TOOLS = {"write_file", "edit_file"}
_DIFF_MAX_LINES = 500


def capture_before(name: str, args: dict, workdir: str) -> str | None:
    """Snapshot a file's current text right before a write/edit tool runs.
    Returns "" for a not-yet-existing file, None when the tool isn't a writer."""
    if name not in FILE_WRITE_TOOLS:
        return None
    try:
        return _resolve(args.get("path", ""), workdir).read_text(errors="replace")
    except (OSError, KeyError):
        return ""


def build_file_diff(name: str, args: dict, workdir: str, before: str | None) -> dict | None:
    """After the write happened, produce {path, added, removed, patch, truncated}
    for the UI. Cheap and best-effort — never raises into the tool flow."""
    if before is None or name not in FILE_WRITE_TOOLS:
        return None
    try:
        after = _resolve(args.get("path", ""), workdir).read_text(errors="replace")
    except OSError:
        return None
    if after == before:
        return None
    b_lines, a_lines = before.splitlines(), after.splitlines()
    added = removed = 0
    patch: list[str] = []
    for line in difflib.unified_diff(b_lines, a_lines, lineterm="", n=2):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if len(patch) < _DIFF_MAX_LINES:
            patch.append(line)
    return {
        "path": args.get("path", ""),
        "added": added, "removed": removed,
        "patch": "\n".join(patch),
        "truncated": len(patch) >= _DIFF_MAX_LINES,
    }


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + f"\n… [truncated, {len(text)} chars total]"
    return text


# ------------------------------------------------------------- providers
# Internal message format:
#   {"role":"user"|"assistant","content":str,"tool_calls":[{"id","name","args"}]?,
#    "thinking_blocks":[{"thinking","signature"}]?}
#   {"role":"tool","tool_call_id":str,"content":str}
# Adapters yield: ("text", delta) | ("thinking", delta) | ("thinking_block", {...})
#                 | ("tool_call", {...}) | ("usage", {...}) | ("stop", reason)

THINKING_TIERS = ("off", "low", "medium", "high")
# Anthropic extended thinking: tier -> budget_tokens (max_tokens must exceed it)
_ANTHROPIC_BUDGETS = {"low": 2048, "medium": 8000, "high": 16000}


def normalize_thinking(value) -> str:
    """Accept the new tier strings and the legacy on/off boolean."""
    if isinstance(value, str) and value in THINKING_TIERS:
        return value
    return "medium" if value is True else "off"


def _cfg_for(provider: str, model: str) -> dict:
    if provider == "ollama":
        base = localai.openai_base_sync()
        if not base:
            raise RuntimeError("No local model is running. Set one up under More → Local AI.")
        return {"provider": "ollama", "api_key": "ollama",
                "model": model or "", "base_url": base}
    key = config.get_key(provider)
    if not key:
        raise RuntimeError(f"No API key configured for {provider}. Add one under Settings → AI.")
    return {"provider": provider, "api_key": key,
            "model": model or config.DEFAULT_MODELS.get(provider, ""),
            "base_url": config.get_base_url(provider)}


def _filter_tools(tool_names: list[str]) -> list[dict]:
    return [t for t in TOOLS if t["name"] in tool_names]


async def stream_anthropic(cfg: dict, messages: list, sysprompt: str,
                           tool_names: list[str], thinking: str = "off"):
    tools = [{"name": t["name"], "description": t["description"],
              "input_schema": t["parameters"]} for t in _filter_tools(tool_names)]
    api_messages = []
    for m in messages:
        if m["role"] == "user":
            api_messages.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = []
            for tb in m.get("thinking_blocks", []):
                blocks.append({"type": "thinking", "thinking": tb["thinking"],
                               "signature": tb["signature"]})
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
    thinking = normalize_thinking(thinking)
    budget = _ANTHROPIC_BUDGETS.get(thinking, 0)
    body = {
        "model": cfg["model"], "max_tokens": budget + 8192 if budget else 4096,
        "system": sysprompt,
        "messages": _merge_consecutive(api_messages), "stream": True,
    }
    if budget:
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    url = (cfg["base_url"] or "https://api.anthropic.com") + "/v1/messages"

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"API error {resp.status_code}: {(await resp.aread()).decode()[:500]}")
            current_tool, tool_json, stop = None, "", "end"
            think_text, think_sig, in_thinking = "", "", False
            usage = {"input": 0, "output": 0}
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                t = ev.get("type")
                if t == "message_start":
                    usage["input"] = ev["message"].get("usage", {}).get("input_tokens", 0)
                elif t == "content_block_start":
                    btype = ev["content_block"]["type"]
                    if btype == "tool_use":
                        current_tool = {"id": ev["content_block"]["id"],
                                        "name": ev["content_block"]["name"]}
                        tool_json = ""
                    elif btype == "thinking":
                        in_thinking, think_text, think_sig = True, "", ""
                elif t == "content_block_delta":
                    d = ev["delta"]
                    if d.get("type") == "text_delta":
                        yield ("text", d["text"])
                    elif d.get("type") == "input_json_delta":
                        tool_json += d["partial_json"]
                    elif d.get("type") == "thinking_delta":
                        think_text += d.get("thinking", "")
                        yield ("thinking", d.get("thinking", ""))
                    elif d.get("type") == "signature_delta":
                        think_sig += d.get("signature", "")
                elif t == "content_block_stop":
                    if current_tool:
                        current_tool["args"] = json.loads(tool_json or "{}")
                        yield ("tool_call", current_tool)
                        current_tool = None
                    elif in_thinking:
                        yield ("thinking_block", {"thinking": think_text, "signature": think_sig})
                        in_thinking = False
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
    if cfg["provider"] == "ollama":
        return cfg["base_url"]     # already the …:11434/v1 endpoint
    return cfg["base_url"] or (
        "https://openrouter.ai/api/v1" if cfg["provider"] == "openrouter"
        else "https://api.openai.com/v1")


def _openai_reasoning_model(model: str) -> bool:
    """OpenAI models that accept the reasoning_effort parameter."""
    base = model.lower().split("/")[-1]
    return bool(re.match(r"(o\d|gpt-5)", base))


async def stream_openai(cfg: dict, messages: list, sysprompt: str,
                        tool_names: list[str], thinking: str = "off"):
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
    thinking = normalize_thinking(thinking)
    if thinking != "off":
        if cfg["provider"] == "openrouter":
            # OpenRouter normalizes reasoning effort across providers/models
            body["reasoning"] = {"effort": thinking}
        elif cfg["provider"] == "openai" and _openai_reasoning_model(cfg["model"]):
            body["reasoning_effort"] = thinking
        # ollama/local: no portable reasoning control — run without
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
                reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                if reasoning:
                    yield ("thinking", reasoning)
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


def get_stream(cfg: dict, messages: list, sysprompt: str, tool_names: list[str],
               thinking: str = "off"):
    if cfg["provider"] == "anthropic":
        return stream_anthropic(cfg, messages, sysprompt, tool_names, thinking)
    return stream_openai(cfg, messages, sysprompt, tool_names, thinking)


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
    try:
        if await localai.available():
            local = await localai.model_options()
            out.append({"provider": "ollama", "models": local, "local": True})
    except Exception:
        pass
    _model_cache.update(time=time.time(), result=out)
    return out


# The live agent loop (multi-device streaming, reconnect, steering) lives in
# sessions.py; it uses the engine helpers above (get_stream, execute_tool,
# system_prompt, allowed_tools, estimate_cost, _persist_usage, _cfg_for …).


# ------------------------------------------------------------ usage log

_USAGE_FILE = config.DATA_DIR / "usage.json"


def _blank_slot() -> dict:
    return {"input": 0, "output": 0, "cost": 0.0, "requests": 0}


def _persist_usage(cfg: dict, usage: dict, cost: float | None) -> None:
    try:
        data = json.loads(_USAGE_FILE.read_text()) if _USAGE_FILE.exists() else {}
        day = time.strftime("%Y-%m-%d")
        slot = data.setdefault(day, _blank_slot())
        slot["input"] += usage["input"]
        slot["output"] += usage["output"]
        slot["requests"] += 1
        if cost:
            slot["cost"] = round(slot["cost"] + cost, 6)
        # per-model breakdown for the usage view
        mkey = f"{cfg['provider']}/{cfg['model']}"
        models = slot.setdefault("models", {})
        m = models.setdefault(mkey, _blank_slot())
        m["input"] += usage["input"]
        m["output"] += usage["output"]
        m["requests"] += 1
        if cost:
            m["cost"] = round(m["cost"] + cost, 6)
        # keep last 90 days
        for old in sorted(data)[:-90]:
            del data[old]
        _USAGE_FILE.write_text(json.dumps(data, indent=1))
    except Exception:
        pass


def _load_usage() -> dict:
    try:
        return json.loads(_USAGE_FILE.read_text()) if _USAGE_FILE.exists() else {}
    except Exception:
        return {}


def _add(dst: dict, src: dict) -> None:
    for k in ("input", "output", "cost", "requests"):
        dst[k] = round(dst.get(k, 0) + src.get(k, 0), 6) if k == "cost" \
            else dst.get(k, 0) + src.get(k, 0)


def usage_summary() -> dict:
    data = _load_usage()
    today = time.strftime("%Y-%m-%d")
    month = today[:7]
    month_slot = _blank_slot()
    for day, slot in data.items():
        if day.startswith(month):
            _add(month_slot, slot)
    return {"today": data.get(today, _blank_slot()), "month": month_slot}


def usage_series(days: int = 30) -> dict:
    """Daily series + per-model breakdown for the last `days` days (for charts)."""
    days = max(1, min(days, 90))
    data = _load_usage()
    series, total, models = [], _blank_slot(), {}
    now = time.time()
    for i in range(days - 1, -1, -1):
        date = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
        slot = data.get(date, {})
        series.append({"date": date,
                       "input": slot.get("input", 0), "output": slot.get("output", 0),
                       "cost": round(slot.get("cost", 0.0), 6),
                       "requests": slot.get("requests", 0)})
        _add(total, slot)
        for mkey, m in (slot.get("models") or {}).items():
            _add(models.setdefault(mkey, _blank_slot()), m)
    model_list = [{"model": k, **v} for k, v in models.items()]
    model_list.sort(key=lambda m: (m["cost"], m["input"] + m["output"]), reverse=True)
    summ = usage_summary()
    return {"days": series, "models": model_list, "total": total,
            "range_days": days, "today": summ["today"], "month": summ["month"],
            "priced": any(m["cost"] for m in model_list) or total["cost"] > 0}


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


async def one_shot_stream(prompt: str, system: str = ""):
    """Streaming, tool-less completion — yields text deltas so callers (e.g. the
    "What is this?" explainer) can show the answer appearing live instead of a
    silent spinner. Falls back to a single yield if the provider can't stream."""
    default = config.get_ai_default()
    if not default["provider"]:
        raise RuntimeError("No AI API key configured")
    cfg = _cfg_for(default["provider"], default["model"])
    if cfg["provider"] == "anthropic":
        url = (cfg["base_url"] or "https://api.anthropic.com") + "/v1/messages"
        body = {"model": cfg["model"], "max_tokens": 2000, "stream": True,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"API error {resp.status_code}: "
                                       f"{(await resp.aread()).decode()[:300]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except ValueError:
                        continue
                    if ev.get("type") == "content_block_delta":
                        piece = ev.get("delta", {}).get("text", "")
                        if piece:
                            yield piece
        return
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}]
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", _openai_base(cfg) + "/chat/completions",
                                 json={"model": cfg["model"], "messages": msgs, "stream": True},
                                 headers={"Authorization": f"Bearer {cfg['api_key']}"}) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"API error {resp.status_code}: "
                                   f"{(await resp.aread()).decode()[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                    continue
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                piece = (ev.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                if piece:
                    yield piece
