"""Coding agents as chat engines: Claude Code, Codex and Mistral Vibe, on your
own login.

The built-in agent talks to model APIs with a key. Many people already pay for
a Claude or ChatGPT subscription and use Claude Code or Codex in a terminal;
PocketADM installs those CLIs (clis.py) and, with this module, drives them from
the Assistant tab the way Happy/Happier do on a laptop: the CLI does the work
with its own login, its own tools and its own context handling, and PocketADM
speaks the CLI's machine protocol, turns its events into the chat protocol both
apps already render, and puts every permission question on the phone.

  * Claude Code — `claude -p --input-format stream-json --output-format
    stream-json --permission-prompt-tool stdio`. Permission questions arrive as
    `control_request {subtype: can_use_tool}` and are answered with a
    `control_response` that allows or denies.
  * Codex — `codex app-server`, JSON-RPC over stdio: initialize → thread/start
    (or thread/resume) → turn/start. Approvals arrive as server requests
    (item/commandExecution/requestApproval, item/fileChange/requestApproval)
    and are answered with {decision: accept | decline}.
  * Mistral Vibe — `vibe-acp`, the Agent Client Protocol (JSON-RPC over stdio,
    as editors such as Zed speak it): initialize → session/new (or
    session/load) → session/prompt. Work streams in as session/update
    notifications; permission questions arrive as session/request_permission
    and are answered with the option the user's tap chose.

PocketADM's own rules still apply on top of the CLI's: in Agent mode a shell
command runs without a tap only if cmdpolicy calls it read-only (which means it
stays on this server), fetching from the internet asks first, anything that
names PocketADM's own credentials asks first, and Plan/Chat refuse every
change. Auto runs everything, exactly like the built-in agent.

Each turn is one CLI process. The CLI keeps the conversation itself; the chat
stores its session id (`chat["engine_sessions"]`) and resumes it next turn.
Switching engines mid-chat starts the new one with a short transcript of what
was said so far, so the conversation carries over.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import os
import secrets
import time

from . import audit, clis, cmdpolicy, config

ENGINES = {
    "claude-code": {
        "label": "Claude Code", "cli": "claude", "vendor": "Anthropic",
        "models": [
            {"id": "default", "name": "Default (your Claude Code setting)"},
            {"id": "opus", "name": "Opus"},
            {"id": "sonnet", "name": "Sonnet"},
            {"id": "haiku", "name": "Haiku"},
        ],
        "login_hint": ("Claude Code is not signed in on this server yet. Connect your Claude "
                       "subscription in the app under More → AI accounts, or run `claude` once "
                       "in the Terminal."),
    },
    "codex": {
        "label": "Codex", "cli": "codex", "vendor": "OpenAI",
        "models": [{"id": "default", "name": "Default (your Codex setting)"}],
        "login_hint": ("Codex is not signed in on this server yet. Connect your ChatGPT plan "
                       "in the app under More → AI accounts, or run `codex login` in the "
                       "Terminal."),
    },
    "mistral-vibe": {
        "label": "Mistral Vibe", "cli": "vibe-acp", "vendor": "Mistral AI",
        "models": [{"id": "default", "name": "Default (your Vibe setting)"}],
        "login_hint": ("Mistral Vibe is not signed in on this server yet. Connect your Mistral "
                       "account in the app under More → AI accounts, or run `vibe` once in the "
                       "Terminal."),
    },
}

TURN_TIMEOUT = 6 * 3600        # a turn may wait hours for an approval, never forever
APPROVAL_TIMEOUT = 1800
_LINE_LIMIT = 64 * 1024 * 1024  # one JSON line can carry a whole file
_HISTORY_CHARS = 6000

SERVER_CONTEXT = (
    "You are running inside PocketADM, the user's self-hosted server manager, on their "
    "server. You are in PocketADM's app container: the host's filesystem is mounted at "
    "/host, and the docker CLI controls the host's Docker engine. The user is watching "
    "on their phone and approves risky steps there, so say briefly what you are about "
    "to do before you do it, prefer reversible changes, and never stop or remove the "
    "helmsman container or the reverse proxy in front of it.")


# ------------------------------------------------------------------ catalog

def binary(engine: str) -> str:
    return str(clis.BIN_DIR / ENGINES[engine]["cli"])


def installed(engine: str) -> bool:
    return engine in ENGINES and os.access(binary(engine), os.X_OK)


def installed_engines() -> list[str]:
    return [e for e in ENGINES if installed(e)]


def signed_in(engine: str) -> bool:
    """A cheap guess (files, no process) whether an engine has a login —
    the model menu shows it; accounts.engine_status asks the CLI itself."""
    home = clis.terminal.PERSIST_HOME
    if engine == "claude-code":
        return bool(config.get_engine_token(engine)) or (home / ".claude" / ".credentials.json").exists()
    if engine == "codex":
        return (home / ".codex" / "auth.json").exists()
    if engine == "mistral-vibe":
        try:
            return any(line.startswith("MISTRAL_API_KEY=") and len(line) > 17
                       for line in (home / ".vibe" / ".env").read_text().splitlines())
        except OSError:
            return False
    return False


def providers() -> list[dict]:
    """Entries for /api/ai/models, next to the API providers. `agent` tells the
    apps this is a CLI with its own login rather than a model API."""
    return [{"provider": e, "label": ENGINES[e]["label"], "models": ENGINES[e]["models"],
             "local": False, "agent": True, "signed_in": signed_in(e)}
            for e in installed_engines()]


# ------------------------------------------------------------------ display

def _display(name: str, args: dict) -> tuple[str, dict]:
    """Claude Code's tool names, as the cards the apps already know."""
    a = args or {}
    if name == "Bash":
        return "run_command", {"command": a.get("command", ""), "description": a.get("description", "")}
    if name == "Read":
        return "read_file", {"path": a.get("file_path", "")}
    if name == "Write":
        return "write_file", {"path": a.get("file_path", ""), "content": a.get("content", "")}
    if name in ("Edit", "MultiEdit"):
        return "edit_file", {"path": a.get("file_path", ""),
                             "old_text": a.get("old_string", ""), "new_text": a.get("new_string", "")}
    if name == "Grep":
        return "search_files", {"pattern": a.get("pattern", ""), "path": a.get("path", ""),
                                "glob": a.get("glob", "")}
    if name == "Glob":
        return "search_files", {"pattern": a.get("pattern", ""), "path": a.get("path", "")}
    if name == "LS":
        return "list_dir", {"path": a.get("path", "")}
    if name == "WebFetch":
        return "fetch_url", {"url": a.get("url", "")}
    return name, a


def _snippet_diff(path: str, old: str, new: str) -> dict | None:
    """The diff card for an edit: what the agent replaced with what."""
    if old == new:
        return None
    added = removed = 0
    patch: list[str] = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=2):
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if len(patch) < 500:
            patch.append(line)
    return {"path": path, "added": added, "removed": removed,
            "patch": "\n".join(patch), "truncated": len(patch) >= 500}


def _patch_stats(path: str, diff: str) -> dict:
    lines = [l for l in (diff or "").splitlines() if not l.startswith(("+++", "---"))]
    return {"path": path, "added": sum(l.startswith("+") for l in lines),
            "removed": sum(l.startswith("-") for l in lines),
            "patch": "\n".join(lines[:500]), "truncated": len(lines) > 500}


def _text_of(content) -> str:
    """tool_result content: a string, or a list of {type:text,text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


def _transcript(messages: list[dict]) -> str:
    """The conversation so far, for an engine that has not seen it."""
    lines = []
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            lines.append("User: " + m["content"].strip())
        elif m.get("role") == "assistant" and m.get("content"):
            lines.append("Assistant: " + m["content"].strip())
    text = "\n\n".join(lines)
    return text[-_HISTORY_CHARS:]


# ------------------------------------------------------------------ policy

def needs_approval(mode: str, name: str, args: dict) -> bool | None:
    """PocketADM's rules for a tool an engine wants to run, in display terms.
    True = ask the user, False = run, None = refuse (Plan/Chat changing things)."""
    if mode == "auto":
        return False
    command = (args or {}).get("command") or ""
    path = (args or {}).get("path") or ""
    read_only = (name in ("read_file", "list_dir", "search_files") and not cmdpolicy.touches_protected(path)) \
        or (name == "run_command" and config.get_autoread() and cmdpolicy.is_read_only(command)
            and not cmdpolicy.touches_protected(command)) \
        or (name == "fetch_url" and cmdpolicy.url_is_local((args or {}).get("url", ""))) \
        or name in ("TodoWrite", "update_plan", "ExitPlanMode", "Task", "TodoRead")
    if read_only:
        return False
    if mode in ("chat", "plan"):
        return None
    return True


async def _ask(session, call_id: str, name: str, args: dict) -> bool:
    """Show the approval card and wait for the tap (shared with the built-in
    agent: Session.resolve_approval answers it)."""
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    session.pending[call_id] = fut
    await session.broadcast(type="tool_request", id=call_id, name=name, args=args)
    try:
        return await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        return False
    finally:
        session.pending.pop(call_id, None)


def _env() -> dict:
    env = clis._env()
    env.setdefault("LANG", "C.UTF-8")
    # never inherit a nested agent's session markers
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID"):
        env.pop(k, None)
    # the long-lived login made under More → AI accounts (claude setup-token)
    token = config.get_engine_token("claude-code")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


async def _spawn(argv: list[str], cwd: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=cwd if os.path.isdir(cwd) else "/",
        env=_env(), limit=_LINE_LIMIT)


async def _stop(proc: asyncio.subprocess.Process) -> str:
    """End the process and return what it said on stderr (for error messages)."""
    with contextlib.suppress(Exception):
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
    err = b""
    with contextlib.suppress(Exception):
        err = await asyncio.wait_for(proc.stderr.read(), 2)
    return err.decode("utf-8", "replace")[-2000:]


def _looks_logged_out(text: str) -> bool:
    t = (text or "").lower()
    return any(s in t for s in ("not logged in", "/login", "please log in", "invalid api key",
                                "authentication", "failed to authenticate", "oauth",
                                "session expired", "token expired", "unauthorized", "401",
                                "auth.json", "not signed in", "login required", "codex login"))


# ------------------------------------------------------------------ turn bookkeeping

class _Turn:
    """What one engine turn produced, kept the way the built-in agent keeps it so
    the chat replays identically on every device."""

    def __init__(self, session):
        self.session = session
        self.text: list[str] = []          # the assistant text being streamed
        self.calls: list[dict] = []        # tool calls of the current assistant message
        self.announced: set[str] = set()   # tool ids that already have a card
        self.settled: set[str] = set()     # tool ids refused here: the CLI's own error
                                           # result for them is not shown a second time
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    async def text_delta(self, delta: str) -> None:
        if delta:
            self.text.append(delta)
            await self.session.broadcast(type="text", delta=delta)

    async def thinking_delta(self, delta: str) -> None:
        if delta:
            await self.session.broadcast(type="thinking", delta=delta)

    def flush_assistant(self) -> None:
        """Close the current assistant message (text + its tool calls)."""
        text = "".join(self.text)
        if text or self.calls:
            self.session.messages.append({"role": "assistant", "content": text,
                                          "tool_calls": list(self.calls)})
        self.text, self.calls = [], []

    async def tool_started(self, call_id: str, name: str, args: dict, auto: str = "") -> None:
        if call_id in self.announced:
            return
        self.announced.add(call_id)
        self.calls.append({"id": call_id, "name": name, "args": args})
        await self.session.broadcast(type="tool_start", id=call_id, name=name, args=args, auto=auto)

    async def tool_refused(self, call_id: str, name: str, args: dict, shown: str = "[denied]") -> None:
        """A call the user (or Plan mode) refused: one card that says so, and the
        same record in the history the built-in agent keeps, for replay."""
        if call_id not in self.announced:
            self.announced.add(call_id)
            self.calls.append({"id": call_id, "name": name, "args": args})
        self.settled.add(call_id)
        self.flush_assistant()
        self.session.messages.append({"role": "tool", "tool_call_id": call_id,
                                      "content": shown + " — not run"})
        await self.session.broadcast(type="tool_result", id=call_id, output=shown)

    async def tool_finished(self, call_id: str, output: str, diff: dict | None = None) -> None:
        self.flush_assistant()
        self.session.messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
        self.session._mutated = True
        await self.session.broadcast(type="tool_result", id=call_id, output=output[:20000], diff=diff)
        self.session._persist()


# ------------------------------------------------------------------ Claude Code

async def _claude_turn(session, prompt: str, turn: _Turn) -> None:
    engine = "claude-code"
    resume = (session.chat.get("engine_sessions") or {}).get(engine, "")
    argv = [binary(engine), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", "--permission-prompt-tool", "stdio",
            "--append-system-prompt", SERVER_CONTEXT]
    if session.mode in ("chat", "plan"):
        argv += ["--permission-mode", "plan"]
    elif session.mode == "auto":
        argv += ["--permission-mode", "acceptEdits"]
    if session.model and session.model != "default":
        argv += ["--model", session.model]
    if resume:
        argv += ["--resume", resume]
    if getattr(session, "ephemeral", False):
        argv += ["--no-session-persistence"]
    proc = await _spawn(argv, session.workdir)
    pending_tools: dict[str, dict] = {}     # tool_use id -> {name, args} from the assistant message
    streamed_text = False                   # partial deltas already showed this message's text
    approvals: set[asyncio.Task] = set()
    finished = False

    async def send(obj: dict) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def answer(req: dict) -> None:
        request = req.get("request") or {}
        rid = req.get("request_id")
        if request.get("subtype") != "can_use_tool":
            await send({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": "unsupported request"}})
            return
        tool_id = request.get("tool_use_id") or ("perm-" + secrets.token_hex(6))
        raw_input = request.get("input") or {}
        name, args = _display(request.get("tool_name", "?"), raw_input)
        if request.get("tool_name") == "ExitPlanMode" and session.mode in ("chat", "plan"):
            # Claude wants to start carrying out its plan. In PocketADM that is
            # the user's switch to Agent mode, not something to wave through.
            plan = raw_input.get("plan") or ""
            if plan:
                await turn.text_delta(("\n\n" if turn.text else "") + plan)
            await send({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": {
                    "behavior": "deny", "interrupt": True,
                    "message": "This chat is in Plan mode: stop here. The user switches to "
                               "Agent mode when they want the plan carried out."}}})
            return
        if request.get("tool_name") == "TodoWrite":
            decision = False
        else:
            decision = needs_approval(session.mode, name, args)
        if decision is None:
            allowed = False
            message = f"{session.mode.capitalize()} mode is read-only in PocketADM — describe the change instead."
            shown = f"[not in {session.mode} mode]"
        elif decision:
            allowed = await _ask(session, tool_id, name, args)
            message = "The user declined this action. Ask how they want to proceed."
            shown = "[denied]"
        else:
            allowed, message, shown = True, "", ""
        if allowed:
            auto = "" if decision else ("read-only" if session.mode != "auto" else "")
            if request.get("tool_name") != "TodoWrite":
                await turn.tool_started(tool_id, name, args, auto=auto)
                audit.record("agent_tool", target=name, source="auto" if session.mode == "auto" else "agent",
                             detail=f"{ENGINES[engine]['label']}: " + (args.get("command") or args.get("path") or "")[:180])
            response = {"behavior": "allow", "updatedInput": raw_input}
        else:
            await turn.tool_refused(tool_id, name, args, shown)
            response = {"behavior": "deny", "message": message}
        await send({"type": "control_response",
                    "response": {"subtype": "success", "request_id": rid, "response": response}})

    async def pump() -> None:
        nonlocal streamed_text, finished
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "control_request":
                task = asyncio.ensure_future(answer(msg))
                approvals.add(task)
                task.add_done_callback(approvals.discard)
                continue
            if kind == "control_cancel_request":
                fut = session.pending.get((msg.get("request") or {}).get("tool_use_id", ""))
                if fut and not fut.done():
                    fut.set_result(False)
                continue
            if msg.get("parent_tool_use_id"):
                continue                       # a sub-agent's inner steps
            if kind == "system" and msg.get("subtype") == "init" and msg.get("session_id"):
                session.chat.setdefault("engine_sessions", {})[engine] = msg["session_id"]
            elif kind == "stream_event":
                event = msg.get("event") or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        streamed_text = True
                        await turn.text_delta(delta.get("text", ""))
                    elif delta.get("type") == "thinking_delta":
                        await turn.thinking_delta(delta.get("thinking", ""))
            elif kind == "assistant":
                for block in (msg.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and not streamed_text:
                        await turn.text_delta(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        name, args = _display(block.get("name", "?"), block.get("input") or {})
                        pending_tools[block.get("id", "")] = {"name": name, "args": args,
                                                             "raw": block.get("name")}
                        if block.get("name") == "TodoWrite":
                            await session.apply_engine_plan([
                                {"title": t.get("content", ""), "status": t.get("status", "pending")}
                                for t in (block.get("input") or {}).get("todos") or []])
                streamed_text = False
            elif kind == "user":
                for block in (msg.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tool_id = block.get("tool_use_id", "")
                    info = pending_tools.pop(tool_id, None) or {"name": "tool", "args": {}, "raw": ""}
                    if info["raw"] == "TodoWrite" or tool_id in turn.settled:
                        continue
                    # a tool Claude ran without asking (its own allow rules) gets its card now
                    await turn.tool_started(tool_id, info["name"], info["args"])
                    diff = None
                    if info["name"] == "edit_file":
                        diff = _snippet_diff(info["args"].get("path", ""), info["args"].get("old_text", ""),
                                             info["args"].get("new_text", ""))
                    elif info["name"] == "write_file":
                        diff = _snippet_diff(info["args"].get("path", ""), "", info["args"].get("content", ""))
                    output = _text_of(block.get("content"))
                    if block.get("is_error") and not output.startswith("Error"):
                        output = "Error: " + output
                    await turn.tool_finished(tool_id, output, diff)
            elif kind == "result":
                usage = msg.get("usage") or {}
                turn.usage["input"] += int(usage.get("input_tokens") or 0)
                turn.usage["output"] += int(usage.get("output_tokens") or 0)
                turn.usage["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
                turn.usage["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
                if msg.get("session_id"):
                    session.chat.setdefault("engine_sessions", {})[engine] = msg["session_id"]
                finished = True
                if msg.get("is_error") or str(msg.get("subtype", "")).startswith("error"):
                    reason = msg.get("result") or msg.get("subtype") or "the run failed"
                    raise RuntimeError(ENGINES[engine]["login_hint"] if _looks_logged_out(str(reason))
                                       else f"Claude Code stopped: {str(reason)[:400]}")
                break

    try:
        await send({"type": "user", "message": {"role": "user", "content": prompt},
                    "parent_tool_use_id": None, "session_id": resume or "default"})
        try:
            await asyncio.wait_for(pump(), TURN_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError("Claude Code ran for longer than six hours and was stopped.") from None
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await send({"type": "control_request", "request_id": secrets.token_hex(6),
                        "request": {"subtype": "interrupt"}})
        raise
    finally:
        for task in list(approvals):
            task.cancel()
        stderr = await _stop(proc)
        if not finished and not stderr.strip() and proc.returncode not in (0, None, -9, -15):
            stderr = f"exit status {proc.returncode}"
    if not finished:
        if resume and "no conversation found" in stderr.lower():
            # the CLI lost the session (other working directory, pruned history):
            # start fresh with the transcript instead of failing the chat
            session.chat.get("engine_sessions", {}).pop(engine, None)
            raise _Restart()
        raise RuntimeError(ENGINES[engine]["login_hint"] if _looks_logged_out(stderr)
                           else "Claude Code ended unexpectedly" + (f": {stderr.strip()[-400:]}" if stderr.strip() else "."))


class _Restart(Exception):
    """The engine's saved session is gone; run the turn again from a transcript."""


# ------------------------------------------------------------------ Codex

class _RPC:
    """Just enough JSON-RPC for codex app-server: requests we send, responses
    we await, notifications and server requests we hand to a callback."""

    def __init__(self, proc, jsonrpc: bool = False):
        self.proc = proc
        self.next_id = 0
        self.waiting: dict[int, asyncio.Future] = {}
        self.jsonrpc = jsonrpc          # ACP wants the "jsonrpc": "2.0" member, Codex does not care

    async def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict) -> asyncio.Future:
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiting[self.next_id] = fut
        msg = {"id": self.next_id, "method": method, "params": params}
        if self.jsonrpc:
            msg["jsonrpc"] = "2.0"
        await self.send(msg)
        return fut

    def resolve(self, msg: dict) -> bool:
        fut = self.waiting.pop(msg.get("id"), None) if "method" not in msg else None
        if fut is None:
            return False
        if not fut.done():
            if "error" in msg:
                fut.set_exception(RuntimeError((msg["error"] or {}).get("message", "request failed")))
            else:
                fut.set_result(msg.get("result"))
        return True


async def _codex_turn(session, prompt: str, turn: _Turn) -> None:
    engine = "codex"
    thread_id = (session.chat.get("engine_sessions") or {}).get(engine, "")
    proc = await _spawn([binary(engine), "app-server"], session.workdir)
    rpc = _RPC(proc)
    approvals: set[asyncio.Task] = set()
    items: dict[str, dict] = {}            # item id -> {name, args}
    totals: dict[str, dict] = {}           # first/last thread token totals seen
    done = asyncio.get_running_loop().create_future()
    state = {"turn_id": "", "error": ""}

    policy = "never" if session.mode == "auto" else "untrusted"
    thread_params = {"cwd": session.workdir, "approvalPolicy": policy,
                     # the container is the sandbox: Codex's own Linux sandbox needs
                     # kernel features a container usually does not grant
                     "sandbox": "danger-full-access",
                     "developerInstructions": SERVER_CONTEXT}
    if session.model and session.model != "default":
        thread_params["model"] = session.model

    async def approve(msg: dict) -> None:
        params = msg.get("params") or {}
        method = msg.get("method", "")
        item_id = params.get("itemId") or ("perm-" + secrets.token_hex(6))
        if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
            command = params.get("command")
            if isinstance(command, list):
                command = " ".join(command)
            name, args = "run_command", {"command": command or "", "reason": params.get("reason") or ""}
        elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            known = items.get(item_id) or {}
            name, args = "edit_file", {"path": (known.get("args") or {}).get("path", ""),
                                       "reason": params.get("reason") or ""}
        else:
            # permissions, user input, MCP elicitations: not something to wave through
            await rpc.send({"id": msg.get("id"), "error": {"code": -32601,
                                                            "message": "not supported by PocketADM"}})
            return
        decision = needs_approval(session.mode, name, args)
        if decision is None:
            allowed, shown = False, f"[not in {session.mode} mode]"
        elif decision:
            allowed, shown = await _ask(session, item_id, name, args), "[denied]"
        else:
            allowed, shown = True, ""
        legacy = method in ("execCommandApproval", "applyPatchApproval")
        if allowed:
            await turn.tool_started(item_id, name, args, auto="" if decision else "read-only")
            audit.record("agent_tool", target=name, source="auto" if session.mode == "auto" else "agent",
                         detail="Codex: " + (args.get("command") or args.get("path") or "")[:180])
            value = "approved" if legacy else "accept"
        else:
            await turn.tool_refused(item_id, name, args, shown)
            value = "denied" if legacy else "decline"
        await rpc.send({"id": msg.get("id"), "result": {"decision": value}})

    async def on_item(item: dict, completed: bool) -> None:
        kind, item_id = item.get("type"), item.get("id", "")
        if item.get("status") == "declined" or item_id in turn.settled:
            return                              # the refusal card already says so
        if kind == "commandExecution":
            args = {"command": item.get("command", "")}
            items[item_id] = {"name": "run_command", "args": args}
            if item_id not in session.pending:  # an open approval shows its own card
                await turn.tool_started(item_id, "run_command", args)
            if completed:
                output = item.get("aggregatedOutput") or ""
                if item.get("exitCode") not in (None, 0):
                    output += f"\n[exit code: {item.get('exitCode')}]"
                await turn.tool_finished(item_id, output or "[no output]")
        elif kind == "fileChange":
            changes = item.get("changes") or []
            path = changes[0].get("path", "") if changes else ""
            args = {"path": path, "files": len(changes)}
            items[item_id] = {"name": "edit_file", "args": args}
            if item_id not in session.pending:
                await turn.tool_started(item_id, "edit_file", args)
            if completed:
                diff = _patch_stats(path, changes[0].get("diff", "")) if changes else None
                status = item.get("status", "")
                await turn.tool_finished(item_id, f"{len(changes)} file(s) {status or 'changed'}", diff)
        elif kind in ("mcpToolCall", "dynamicToolCall", "webSearch"):
            name = item.get("tool") or ("web_search" if kind == "webSearch" else kind)
            args = {"query": item.get("query")} if kind == "webSearch" else (item.get("arguments") or {})
            await turn.tool_started(item_id, name, args if isinstance(args, dict) else {"arguments": args})
            if completed:
                result = item.get("result") or item.get("error") or item.get("results") or ""
                await turn.tool_finished(item_id, result if isinstance(result, str) else json.dumps(result)[:20000])
        elif kind == "agentMessage" and completed:
            # deltas streamed the text already; keep the message boundary
            turn.flush_assistant()

    async def reader() -> None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if rpc.resolve(msg):
                continue
            method, params = msg.get("method", ""), msg.get("params") or {}
            if "id" in msg:                    # a server request: an approval
                task = asyncio.ensure_future(approve(msg))
                approvals.add(task)
                task.add_done_callback(approvals.discard)
            elif method == "item/agentMessage/delta":
                await turn.text_delta(params.get("delta", ""))
            elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                await turn.thinking_delta(params.get("delta", ""))
            elif method in ("item/started", "item/completed"):
                await on_item(params.get("item") or {}, method == "item/completed")
            elif method == "turn/started":
                state["turn_id"] = (params.get("turn") or {}).get("id", "") or state["turn_id"]
            elif method == "turn/plan/updated":
                status = {"inProgress": "in_progress", "completed": "done"}
                await session.apply_engine_plan([
                    {"title": p.get("step", ""), "status": status.get(p.get("status"), "pending")}
                    for p in params.get("plan") or []])
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage") or {}
                total, last = usage.get("total") or {}, usage.get("last") or {}
                if "first" not in totals:      # the thread's total before this turn
                    totals["first"] = {k: int(total.get(k) or 0) - int(last.get(k) or 0)
                                       for k in ("inputTokens", "outputTokens", "cachedInputTokens")}
                totals["last"] = {k: int(total.get(k) or 0)
                                  for k in ("inputTokens", "outputTokens", "cachedInputTokens")}
            elif method == "error" and not params.get("willRetry"):
                state["error"] = ((params.get("error") or {}).get("message") or "Codex reported an error")
            elif method == "turn/completed":
                t = params.get("turn") or {}
                if t.get("status") == "failed":
                    state["error"] = ((t.get("error") or {}).get("message")
                                      or state["error"] or "the turn failed")
                if not done.done():
                    done.set_result(t.get("status", "completed"))
        if not done.done():
            done.set_result("closed")

    read_task = asyncio.ensure_future(reader())
    try:
        init = await rpc.request("initialize", {"clientInfo": {
            "name": "pocketadm", "title": "PocketADM", "version": config.VERSION}})
        await asyncio.wait_for(init, 30)
        await rpc.send({"method": "initialized"})
        if thread_id:
            fut = await rpc.request("thread/resume", {"threadId": thread_id, **thread_params})
        else:
            fut = await rpc.request("thread/start", thread_params)
        try:
            started = await asyncio.wait_for(fut, 60)
        except RuntimeError:
            if not thread_id:
                raise
            session.chat.get("engine_sessions", {}).pop(engine, None)
            raise _Restart()
        thread_id = ((started or {}).get("thread") or {}).get("id", thread_id)
        session.chat.setdefault("engine_sessions", {})[engine] = thread_id
        turn_fut = await rpc.request("turn/start", {"threadId": thread_id,
                                                    "input": [{"type": "text", "text": prompt}]})
        started_turn = await asyncio.wait_for(turn_fut, 60)
        state["turn_id"] = ((started_turn or {}).get("turn") or {}).get("id", "") or state["turn_id"]
        await asyncio.wait_for(done, TURN_TIMEOUT)
    except asyncio.CancelledError:
        if state["turn_id"]:
            with contextlib.suppress(Exception):
                await rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": state["turn_id"]})
        raise
    except (RuntimeError, asyncio.TimeoutError) as e:
        state["error"] = state["error"] or str(e) or "Codex did not answer"
    finally:
        for task in list(approvals):
            task.cancel()
        stderr = await _stop(proc)
        read_task.cancel()
    if "last" in totals:
        delta = {k: totals["last"][k] - totals["first"][k] for k in totals["last"]}
        turn.usage["input"] += max(delta["inputTokens"], 0)
        turn.usage["output"] += max(delta["outputTokens"], 0)
        turn.usage["cache_read"] += max(delta["cachedInputTokens"], 0)
    if state["error"] or done.result() == "closed":
        reason = state["error"] or stderr.strip()[-400:] or "Codex ended unexpectedly"
        raise RuntimeError(ENGINES[engine]["login_hint"] if _looks_logged_out(reason + stderr)
                           else f"Codex stopped: {reason}")



# ------------------------------------------------------------------ Mistral Vibe

def _acp_display(update: dict) -> tuple[str, dict]:
    """An ACP tool call as the cards the apps already know."""
    kind = update.get("kind") or ""
    raw = update.get("rawInput")
    raw = raw if isinstance(raw, dict) else ({"input": raw} if raw else {})
    title = update.get("title") or ""
    locations = update.get("locations") or []
    path = raw.get("path") or raw.get("file_path") or raw.get("filePath") \
        or (locations[0].get("path", "") if locations and isinstance(locations[0], dict) else "")
    if kind == "execute":
        return "run_command", {"command": raw.get("command") or raw.get("cmd") or title}
    if kind == "read":
        return "read_file", {"path": path or title}
    if kind in ("edit", "delete", "move"):
        return "edit_file", {"path": path or title}
    if kind == "search":
        return "search_files", {"pattern": raw.get("pattern") or raw.get("query") or title,
                                "path": path}
    if kind == "fetch":
        return "fetch_url", {"url": raw.get("url") or title}
    return (title or kind or "tool")[:60], raw


def _acp_option(options: list, wanted: str) -> str:
    """The optionId of the first option of a kind (allow_once / reject_once),
    falling back to its "always" variant."""
    for kind in (wanted, wanted.replace("_once", "_always")):
        for opt in options or []:
            if isinstance(opt, dict) and opt.get("kind") == kind:
                return opt.get("optionId", "")
    return ""


def _acp_text(content) -> str:
    """Text out of ACP tool-call content blocks."""
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "content":
            inner = block.get("content") or {}
            if isinstance(inner, dict) and inner.get("type") == "text":
                parts.append(inner.get("text", ""))
        elif block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _acp_diff(content) -> dict | None:
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "diff":
            return _snippet_diff(block.get("path", ""), block.get("oldText") or "",
                                 block.get("newText") or "")
    return None


VIBE_MODES = {"chat": "plan", "plan": "plan", "agent": "ask", "auto": "auto-approve"}


async def _vibe_turn(session, prompt: str, turn: _Turn) -> None:
    engine = "mistral-vibe"
    saved = (session.chat.get("engine_sessions") or {}).get(engine, "")
    proc = await _spawn([binary(engine)], session.workdir)
    rpc = _RPC(proc, jsonrpc=True)
    approvals: set[asyncio.Task] = set()
    calls: dict[str, dict] = {}            # toolCallId -> {name, args, out: [], hidden}
    state = {"loading": False, "session": saved}

    async def permission(msg: dict) -> None:
        params = msg.get("params") or {}
        tc = params.get("toolCall") or {}
        call_id = tc.get("toolCallId") or ("perm-" + secrets.token_hex(6))
        known = calls.get(call_id)
        name, args = (known["name"], known["args"]) if known and known.get("name") \
            else _acp_display(tc)
        decision = needs_approval(session.mode, name, args)
        if decision is None:
            allowed, shown = False, f"[not in {session.mode} mode]"
        elif decision:
            allowed, shown = await _ask(session, call_id, name, args), "[denied]"
        else:
            allowed, shown = True, ""
        if allowed:
            await turn.tool_started(call_id, name, args, auto="" if decision else "read-only")
            audit.record("agent_tool", target=name, source="auto" if session.mode == "auto" else "agent",
                         detail="Mistral Vibe: " + (args.get("command") or args.get("path") or "")[:180])
            option = _acp_option(params.get("options"), "allow_once")
        else:
            await turn.tool_refused(call_id, name, args, shown)
            option = _acp_option(params.get("options"), "reject_once")
        outcome = {"outcome": "selected", "optionId": option} if option else {"outcome": "cancelled"}
        await rpc.send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"outcome": outcome}})

    async def on_update(update: dict) -> None:
        if state["loading"]:
            return                          # history replay of a resumed session
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                await turn.text_delta(content.get("text", ""))
        elif kind == "agent_thought_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                await turn.thinking_delta(content.get("text", ""))
        elif kind == "plan":
            await session.apply_engine_plan([
                {"title": e.get("content", ""), "status": e.get("status", "pending")}
                for e in update.get("entries") or [] if isinstance(e, dict)])
        elif kind in ("tool_call", "tool_call_update"):
            call_id = update.get("toolCallId", "")
            call = calls.setdefault(call_id, {"name": "", "args": {}, "out": [], "hidden": False})
            if update.get("rawInput") is not None or kind == "tool_call":
                name, args = _acp_display(update)
                if isinstance(update.get("rawInput"), dict) and "todos" in update["rawInput"]:
                    call["hidden"] = True       # the to-do list shows as the plan instead
                if name:
                    call["name"], call["args"] = name, args
            text = _acp_text(update.get("content"))
            if text:
                call["out"].append(text)
            status = update.get("status") or ""
            if call["hidden"] or call_id in turn.settled or call_id in session.pending:
                return
            if status in ("in_progress", "completed", "failed"):
                await turn.tool_started(call_id, call["name"] or "tool", call["args"])
            if status in ("completed", "failed"):
                output = "".join(call["out"])
                raw_out = update.get("rawOutput")
                if not output and raw_out is not None:
                    output = raw_out if isinstance(raw_out, str) else json.dumps(raw_out)[:20000]
                if status == "failed" and not output.startswith("Error"):
                    output = "Error: " + (output or "the tool failed")
                await turn.tool_finished(call_id, output or "[no output]",
                                         _acp_diff(update.get("content")))

    async def reader() -> None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if rpc.resolve(msg):
                continue
            method, params = msg.get("method", ""), msg.get("params") or {}
            if "id" in msg and method:
                if method == "session/request_permission":
                    task = asyncio.ensure_future(permission(msg))
                    approvals.add(task)
                    task.add_done_callback(approvals.discard)
                else:                       # files, terminals, questions: not offered
                    await rpc.send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                        "code": -32601, "message": "not supported by PocketADM"}})
            elif method == "session/update":
                await on_update(params.get("update") or {})

    read_task = asyncio.ensure_future(reader())
    usage: dict = {}
    try:
        init = await rpc.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "pocketadm", "title": "PocketADM", "version": config.VERSION}})
        await asyncio.wait_for(init, 60)
        modes: dict = {}
        if saved:
            state["loading"] = True
            try:
                fut = await rpc.request("session/load", {"sessionId": saved, "cwd": session.workdir,
                                                         "mcpServers": []})
                loaded = await asyncio.wait_for(fut, 120)
                modes = (loaded or {}).get("modes") or {}
            except RuntimeError:
                session.chat.get("engine_sessions", {}).pop(engine, None)
                raise _Restart()
            finally:
                state["loading"] = False
        else:
            fut = await rpc.request("session/new", {"cwd": session.workdir, "mcpServers": []})
            created = await asyncio.wait_for(fut, 120)
            state["session"] = (created or {}).get("sessionId", "")
            modes = (created or {}).get("modes") or {}
            session.chat.setdefault("engine_sessions", {})[engine] = state["session"]
        wanted = VIBE_MODES.get(session.mode, "")
        available = {m.get("id") for m in (modes.get("availableModes") or []) if isinstance(m, dict)}
        if wanted in available and wanted != modes.get("currentModeId"):
            fut = await rpc.request("session/set_mode", {"sessionId": state["session"],
                                                         "modeId": wanted})
            with contextlib.suppress(Exception):
                await asyncio.wait_for(fut, 30)
        text = prompt if saved else f"{SERVER_CONTEXT}\n\n{prompt}"
        fut = await rpc.request("session/prompt", {"sessionId": state["session"],
                                                   "prompt": [{"type": "text", "text": text}]})
        result = await asyncio.wait_for(fut, TURN_TIMEOUT)
        usage = (result or {}).get("usage") or {}
    except asyncio.CancelledError:
        if state["session"]:
            with contextlib.suppress(Exception):
                await rpc.send({"jsonrpc": "2.0", "method": "session/cancel",
                                "params": {"sessionId": state["session"]}})
                await asyncio.sleep(0.3)
        raise
    except asyncio.TimeoutError:
        raise RuntimeError("Mistral Vibe did not answer in time.") from None
    except RuntimeError as e:
        stderr = await _stop(proc)
        read_task.cancel()
        reason = str(e) + " " + stderr
        if _looks_logged_out(reason) or "api key" in reason.lower() or "mistral_api_key" in reason.lower():
            raise RuntimeError(ENGINES[engine]["login_hint"]) from None
        raise RuntimeError(f"Mistral Vibe stopped: {str(e)[:400]}") from None
    finally:
        for task in list(approvals):
            task.cancel()
        if not read_task.done():
            await _stop(proc)
            read_task.cancel()
    for key, field in (("input", ("inputTokens", "input_tokens", "promptTokens")),
                       ("output", ("outputTokens", "output_tokens", "completionTokens"))):
        for name in field:
            if isinstance(usage.get(name), (int, float)):
                turn.usage[key] += int(usage[name])
                break


# ------------------------------------------------------------------ headless runs

class _Headless:
    """Just enough of a chat session for an engine to run one prompt in the
    background — the watch, the explainers — with no device attached. Plan
    mode: the engine may look, never change (needs_approval refuses writes
    before anyone could be asked)."""

    def __init__(self, engine: str, model: str, workdir: str, mode: str = "plan"):
        self.provider = engine
        self.model = model or "default"
        self.mode = mode
        self.workdir = workdir
        self.chat = {"id": "headless", "title": "", "engine_sessions": {}}
        self.messages: list[dict] = []
        self.pending: dict = {}
        self.plan: list = []
        self.ephemeral = True
        self._mutated = False
        self.text: list[str] = []
        self.steps: list[dict] = []

    async def broadcast(self, live: bool = True, **event) -> None:
        if event.get("type") == "text":
            self.text.append(event.get("delta", ""))
        elif event.get("type") == "tool_start":
            args = event.get("args") or {}
            self.steps.append({"tool": event.get("name", ""),
                               "detail": str(args.get("command") or args.get("path")
                                             or args.get("url") or "")[:200], "output": ""})
        elif event.get("type") == "tool_result" and self.steps:
            self.steps[-1]["output"] = str(event.get("output", ""))[:400]

    async def apply_engine_plan(self, steps: list[dict]) -> None:
        self.plan = steps

    def _persist(self) -> None:
        pass


async def run_headless(engine: str, prompt: str, model: str = "", workdir: str = "",
                       mode: str = "plan", timeout: float = 600) -> dict:
    """One prompt through an engine without a chat: {"text", "steps", "usage"}."""
    if not installed(engine):
        raise RuntimeError(f"{ENGINES[engine]['label']} is not installed on this server.")
    from . import ai as _ai
    session = _Headless(engine, model, workdir or _ai.DEFAULT_WORKDIR, mode)
    session.messages.append({"role": "user", "content": prompt})
    usage = await asyncio.wait_for(run_turn(session), timeout)
    # everything the engine said in this turn — a tool call in between splits
    # it into several assistant messages, the answer is all of them
    text = "".join(session.text).strip() or "\n\n".join(
        m["content"] for m in session.messages if m.get("role") == "assistant" and m.get("content"))
    return {"text": text, "steps": session.steps, "usage": usage}

# ------------------------------------------------------------------ entry point

def _prompt_for(session, engine: str) -> str:
    """The new user message(s) of this turn — and, when this engine has not
    seen the conversation yet, what came before."""
    tail: list[str] = []
    for m in reversed(session.messages):
        if m.get("role") != "user":
            break
        if isinstance(m.get("content"), str):
            tail.append(m["content"])
    prompt = "\n\n".join(reversed(tail)).strip()
    if not (session.chat.get("engine_sessions") or {}).get(engine):
        earlier = _transcript(session.messages[:len(session.messages) - len(tail)])
        if earlier:
            prompt = ("Earlier in this conversation (with another assistant):\n\n"
                      f"{earlier}\n\n---\n\n{prompt}")
    return prompt


async def run_turn(session) -> dict:
    """One user turn through an engine. Returns the turn's token usage."""
    engine = session.provider
    if not installed(engine):
        raise RuntimeError(f"{ENGINES[engine]['label']} is not installed on this server. "
                           "Connect it under More → AI accounts.")
    run = {"claude-code": _claude_turn, "codex": _codex_turn,
           "mistral-vibe": _vibe_turn}[engine]
    turn = _Turn(session)
    started = time.time()
    try:
        await run(session, _prompt_for(session, engine), turn)
    except _Restart:
        turn = _Turn(session)
        await run(session, _prompt_for(session, engine), turn)
    finally:
        turn.flush_assistant()
        session._persist()
    turn.usage["seconds"] = round(time.time() - started, 1)
    return turn.usage
