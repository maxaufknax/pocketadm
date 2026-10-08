"""Signing the coding CLIs in to the user's own subscriptions — from the phone.

Until now the only way was a terminal on the server: run `claude`, `codex
login` or `vibe` and follow the prompts. On a phone that is a struggle, and the
prompts expect a browser on the same machine. Each CLI has a way that works
across devices; this module drives it and hands the phone the one step only a
person can do:

  * Claude Code — `claude auth login --claudeai`, through plain pipes: the CLI
    prints a claude.com link, the person signs in and copies the code the page
    shows, PocketADM hands it to the CLI on stdin and the CLI stores the login
    itself (exit code 0). A code that does not work is answered with the CLI's
    own reason and a fresh link, because the code was bound to the old one.
    A token made elsewhere with `claude setup-token` can be pasted instead;
    PocketADM keeps that one (settings, never sent back out) and gives it to
    every Claude Code run as CLAUDE_CODE_OAUTH_TOKEN.
  * Codex — `codex login --device-auth`: a link and a one-time code to type
    there; the CLI notices on its own when the sign-in is done.
  * Mistral Vibe — the Agent Client Protocol's delegated browser sign-in
    (`vibe-acp`, auth method "browser-auth-delegated"): a console.mistral.ai
    link; Vibe stores the key it receives in its own config.

A missing CLI is installed first, inside the same flow. Flows live in memory
and expire; the app polls `get()` while its sheet is open.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import pty
import re
import secrets
import struct
import termios
import time
import urllib.parse

from . import audit, clis, config, engines

FLOW_TTL = 15 * 60
FLOWS: dict[str, "Flow"] = {}

# engine -> the CLI that has to be installed for it (clis.CLIS key)
CLI_FOR = {"claude-code": "claude", "codex": "codex", "mistral-vibe": "mistral"}

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
# terminal UIs (Ink) move the cursor instead of printing spaces
_CURSOR_RIGHT = re.compile(r"\x1b\[(\d*)C")
_OSC8 = re.compile(r"\x1b\]8;[^;]*;([^\x07\x1b]+)(?:\x07|\x1b\\)")
_URL = re.compile(r"https://[^\s\"'<>\x1b\x07]+")
_CLAUDE_TOKEN = re.compile(r"sk-ant-oat\d*-[A-Za-z0-9_\-]{20,}")
_DEVICE_CODE = re.compile(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,6})\b")


class Flow:
    def __init__(self, engine: str):
        self.id = secrets.token_hex(8)
        self.engine = engine
        self.state = "starting"     # starting | installing | waiting_browser | waiting_code
        #                             | verifying | done | failed | cancelled
        self.url = ""
        self.user_code = ""
        self.message = ""
        self.error = ""
        self.started = time.time()
        self.expires = self.started + FLOW_TTL
        self.task: asyncio.Task | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.master: int | None = None
        self.output = ""
        self.seen = 0               # characters read in total (output keeps the tail)
        self.attempt = 1
        self.code_event = asyncio.Event()
        self.code = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "engine": self.engine, "state": self.state, "url": self.url,
                "user_code": self.user_code, "message": self.message, "error": self.error,
                "needs_code": self.state == "waiting_code", "attempt": self.attempt,
                "expires": self.expires, "label": engines.ENGINES[self.engine]["label"]}


def clean(text: str) -> str:
    text = _CURSOR_RIGHT.sub(lambda m: " " * int(m.group(1) or 1), text)
    return _ANSI.sub("", text).replace("\r", "")


def find_url(raw: str, prefer: tuple = ()) -> str:
    """The sign-in link in a CLI's output. Terminal UIs may wrap it in an OSC 8
    hyperlink or colour it; both are taken apart here."""
    urls = _OSC8.findall(raw) + _URL.findall(clean(raw))
    urls = [u.rstrip(".,);]") for u in urls]
    for word in prefer:
        for u in urls:
            if word in u:
                return u
    return urls[0] if urls else ""


# ------------------------------------------------------------------ lifecycle

def get(flow_id: str) -> Flow | None:
    _reap()
    return FLOWS.get(flow_id)


def _reap() -> None:
    now = time.time()
    for fid, flow in list(FLOWS.items()):
        if now > flow.expires + 600:
            FLOWS.pop(fid, None)
        elif now > flow.expires and flow.state not in ("done", "failed", "cancelled"):
            flow.state, flow.error = "failed", "The sign-in took too long. Start it again."
            _kill(flow)


def _kill(flow: Flow) -> None:
    if flow.proc and flow.proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            flow.proc.kill()
    if flow.master is not None:
        with contextlib.suppress(OSError):
            os.close(flow.master)
        flow.master = None


async def start(engine: str) -> Flow:
    if engine not in CLI_FOR:
        raise ValueError("unknown engine")
    for other in FLOWS.values():            # one sign-in per engine at a time
        if other.engine == engine and other.state not in ("done", "failed", "cancelled"):
            await cancel(other.id)
    flow = Flow(engine)
    FLOWS[flow.id] = flow
    runner = {"claude-code": _claude, "codex": _codex, "mistral-vibe": _vibe}[engine]

    async def run():
        try:
            if not engines.installed(engine) or (engine == "mistral-vibe" and not
                                                  os.access(clis.BIN_DIR / "vibe", os.X_OK)):
                await _install(flow)
            await runner(flow)
            if flow.state == "done":
                audit.record("ai_signin", target=engines.ENGINES[engine]["label"],
                             detail="subscription connected")
                from . import accounts
                accounts.forget_status(engine)
        except asyncio.CancelledError:
            flow.state = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001 — shown to the person, not swallowed
            if flow.state != "cancelled":
                flow.state, flow.error = "failed", str(e)[:400] or type(e).__name__
        finally:
            _kill(flow)

    flow.task = asyncio.ensure_future(run())
    return flow


async def submit_code(flow_id: str, code: str) -> Flow:
    flow = get(flow_id)
    if not flow:
        raise KeyError(flow_id)
    if flow.state != "waiting_code":
        raise ValueError("this sign-in is not waiting for a code")
    flow.code = code.strip()
    flow.state = "verifying"
    flow.message = "Checking the code …"
    flow.code_event.set()
    return flow


async def cancel(flow_id: str) -> None:
    flow = FLOWS.get(flow_id)
    if not flow:
        return
    flow.state = "cancelled"
    _kill(flow)
    if flow.task and not flow.task.done():
        flow.task.cancel()


async def _install(flow: Flow) -> None:
    tool = CLI_FOR[flow.engine]
    flow.state = "installing"
    flow.message = f"Installing {clis.CLIS[tool]['name']} on the server …"
    job = clis.start_install_job(tool)
    while job.status == "running":
        await asyncio.sleep(1)
        if job.lines:
            flow.message = f"Installing {clis.CLIS[tool]['name']} … " + job.lines[-1][:80]
    if job.status != "done":
        raise RuntimeError("Installing failed: " + (job.lines[-1] if job.lines else "see the job log"))


# ------------------------------------------------------------------ PTY helper

async def _spawn_pty(argv: list[str], cols: int = 1000, rows: int = 50):
    """A CLI with a real terminal (Claude Code's UI insists on one), wide
    enough that a 400-character link is never wrapped."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    env = engines._env()
    env.update({"TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows),
                "BROWSER": "/bin/false", "NO_COLOR": "1", "CI": ""})
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=slave, stdout=slave, stderr=slave, env=env,
        cwd=str(clis.terminal.PERSIST_HOME), start_new_session=True)
    os.close(slave)
    os.set_blocking(master, False)
    return proc, master


async def _read_pty(flow: Flow, wait: float = 0.3) -> str:
    """Whatever the CLI printed since the last read."""
    chunks = []
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            data = os.read(flow.master, 65536)
            if not data:
                break
            chunks.append(data.decode("utf-8", "replace"))
        except BlockingIOError:
            await asyncio.sleep(0.05)
        except OSError:
            break
    text = "".join(chunks)
    flow.seen += len(text)
    flow.output = (flow.output + text)[-20000:]
    return text


def _write_pty(flow: Flow, text: str) -> None:
    with contextlib.suppress(OSError):
        os.write(flow.master, text.encode())


_CLAUDE_KEY = re.compile(r"sk-ant-api\d*-[A-Za-z0-9_\-]{20,}")
_LOGIN_FAILED = re.compile(r"(?:login failed|oauth error|error)\s*:\s*(.+)", re.I)


def claude_code_problem(code: str, url: str) -> str:
    """Why a pasted Claude code cannot work, before the CLI spends it — or "".

    The page shows `CODE#STATE`. Claude Code needs both halves, and the state
    names the sign-in page the code came from: a code from an older page fails
    on Anthropic's side with nothing but "status code 400"."""
    if _CLAUDE_KEY.search(code):
        return ("That is an API key, not a sign-in code. Paste it under API key on the "
                "previous screen instead.")
    if "#" not in code:
        return ("That is only part of the code. Tap Copy Code on Claude's page — the "
                "whole code has a # in the middle.")
    state = code.rsplit("#", 1)[1].strip()
    wanted = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("state") or [""])[0]
    if wanted and state and state != wanted:
        return ("That code belongs to an older sign-in page. Open the sign-in page again "
                "and copy the code it shows now.")
    return ""


def normalize_claude_code(text: str) -> str:
    """What people paste: the code, the code in quotes, or the whole callback
    address (…/oauth/code/callback?code=X&state=Y)."""
    text = (text or "").strip().strip("'\"` ")
    if text.startswith("http"):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        code, state = (query.get("code") or [""])[0], (query.get("state") or [""])[0]
        if code and state:
            return f"{code}#{state}"
    return "".join(text.split())


async def _claude_login(flow: Flow) -> None:
    """Start `claude auth login --claudeai` and wait for its sign-in link."""
    if flow.proc and flow.proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            flow.proc.kill()
    env = engines._env()
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)       # a stale token would shadow the new login
    env.update({"BROWSER": "/bin/false", "NO_COLOR": "1"})
    flow.proc = await asyncio.create_subprocess_exec(
        engines.binary("claude-code"), "auth", "login", "--claudeai",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(clis.terminal.PERSIST_HOME))
    said: list[str] = []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            line = await asyncio.wait_for(flow.proc.stdout.readline(),
                                          max(0.5, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
        if not line:
            break
        text = clean(line.decode("utf-8", "replace"))
        said.append(text.strip())
        url = find_url(text, ("oauth/authorize",))
        if url and "authorize" in url:
            flow.url = url
            return
    tail = " ".join(" ".join(said).split())[-300:]
    raise RuntimeError("Claude Code did not hand out a sign-in link." + (f" It said: {tail}" if tail else ""))


async def _claude_logged_in() -> dict:
    """`claude auth status --json`, as the CLI sees its stored login."""
    code, text = await _run([engines.binary("claude-code"), "auth", "status", "--json"])
    try:
        return json.loads(text[text.index("{"):])
    except ValueError:
        return {}


async def _claude_token(flow: Flow, token: str) -> bool:
    """A long-lived token made with `claude setup-token` somewhere else: check it
    with a one-line request, keep it if Claude answers."""
    flow.state, flow.message = "verifying", "Checking the token with Claude …"
    env = engines._env()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    try:
        proc = await asyncio.create_subprocess_exec(
            engines.binary("claude-code"), "-p", "Reply with the single word OK.",
            "--max-turns", "1", "--output-format", "json",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(clis.terminal.PERSIST_HOME))
        out, _ = await asyncio.wait_for(proc.communicate(), 120)
    except (OSError, asyncio.TimeoutError):
        flow.state, flow.message = "waiting_code", "Claude did not answer in time. Try the token again."
        return False
    text = out.decode("utf-8", "replace")
    try:
        result = json.loads(text[text.index("{"):])
    except ValueError:
        result = {}
    if proc.returncode == 0 and not result.get("is_error"):
        config.set_engine_token("claude-code", token)
        flow.state, flow.message = "done", "Claude is connected."
        return True
    reason = str(result.get("result") or " ".join(clean(text).split()))[-200:]
    flow.state = "waiting_code"
    flow.message = "Claude did not accept this token" + (f": {reason}" if reason else ".")
    return False


async def _claude(flow: Flow) -> None:
    flow.state = "starting"
    flow.message = "Asking Claude Code for a sign-in link …"
    await _claude_login(flow)
    flow.state = "waiting_code"
    flow.message = "Sign in on the page that opens, then copy the code it shows and paste it here."
    while time.time() < flow.expires:
        try:
            await asyncio.wait_for(flow.code_event.wait(), 5)
        except asyncio.TimeoutError:
            if flow.proc and flow.proc.returncode is not None and flow.state == "waiting_code":
                # the CLI gave up waiting (it has its own timeout): a fresh link
                await _claude_login(flow)
                flow.attempt += 1
            continue
        flow.code_event.clear()
        code, flow.code = normalize_claude_code(flow.code), ""
        if _CLAUDE_TOKEN.fullmatch(code):
            if await _claude_token(flow, code):
                return
            continue
        problem = claude_code_problem(code, flow.url)
        if problem:
            flow.state, flow.message = "waiting_code", problem
            continue
        try:
            flow.proc.stdin.write((code + "\n").encode())
            await flow.proc.stdin.drain()
            out = await asyncio.wait_for(flow.proc.stdout.read(), 120)
            await asyncio.wait_for(flow.proc.wait(), 10)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            out = b""
        said = " ".join(clean(out.decode("utf-8", "replace")).replace("Paste code here if prompted >", "").split())
        if flow.proc.returncode == 0:
            # the CLI stores its own login now; an older pasted token must not shadow it
            config.set_engine_token("claude-code", "")
            status = await _claude_logged_in()
            if status and not status.get("loggedIn"):
                raise RuntimeError("Claude Code reported success but is not signed in. " + said[-200:])
            plan = str(status.get("subscriptionType") or "")
            flow.state = "done"
            flow.message = "Claude is connected" + (f" ({plan.capitalize()})." if plan else ".")
            return
        found = _LOGIN_FAILED.search(said)
        reason = (found.group(1) if found else said or "no reason given").strip()[:200]
        # The code was spent on, and bound to, that sign-in page: start a fresh one.
        await _claude_login(flow)
        flow.attempt += 1
        flow.state = "waiting_code"
        flow.message = (f"Claude did not accept that code ({reason}). The sign-in page is new now: "
                        "open it again, sign in, and paste the code it shows.")
    raise RuntimeError("The sign-in took too long. Start it again.")


async def _codex(flow: Flow) -> None:
    flow.message = "Asking Codex for a sign-in code …"
    flow.proc, flow.master = await _spawn_pty([engines.binary("codex"), "login", "--device-auth"])
    while flow.proc.returncode is None and time.time() < flow.expires:
        await _read_pty(flow, 0.5)
        plain = clean(flow.output)
        if not flow.url:
            flow.url = find_url(flow.output, ("device", "auth.openai.com", "openai.com"))
        if not flow.user_code:
            m = _DEVICE_CODE.search(plain)
            if m:
                flow.user_code = m.group(1)
        if flow.url and flow.state not in ("waiting_browser",):
            flow.state = "waiting_browser"
            flow.message = ("Open the page, sign in with your ChatGPT account and enter the code "
                            "shown here. This screen updates by itself.")
        await asyncio.sleep(0.5)
    await _read_pty(flow)
    if flow.proc.returncode == 0:
        flow.state, flow.message = "done", "ChatGPT is connected."
        return
    tail = " ".join(clean(flow.output).split())[-300:]
    raise RuntimeError("Codex did not finish the sign-in." + (f" It said: {tail}" if tail else ""))


async def _vibe(flow: Flow) -> None:
    flow.message = "Asking Mistral for a sign-in link …"
    proc = await asyncio.create_subprocess_exec(
        engines.binary("mistral-vibe"), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=engines._env(), cwd=str(clis.terminal.PERSIST_HOME), limit=16 * 1024 * 1024)
    flow.proc = proc
    rpc = engines._RPC(proc, jsonrpc=True)

    async def reader():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            with contextlib.suppress(ValueError):
                msg = json.loads(line)
                if not rpc.resolve(msg) and "id" in msg and msg.get("method"):
                    await rpc.send({"jsonrpc": "2.0", "id": msg["id"], "error": {
                        "code": -32601, "message": "not supported"}})

    read_task = asyncio.ensure_future(reader())
    try:
        fut = await rpc.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False, "_meta": {"browser-auth-delegated": True}},
            "clientInfo": {"name": "pocketadm", "title": "PocketADM", "version": config.VERSION}})
        init = await asyncio.wait_for(fut, 60) or {}
        methods = {m.get("id") for m in init.get("authMethods") or [] if isinstance(m, dict)}
        if "browser-auth-delegated" not in methods:
            raise RuntimeError("This version of Mistral Vibe cannot sign in from another device. "
                               "Update it under More → Coding agents and try again.")
        fut = await rpc.request("authenticate", {"methodId": "browser-auth-delegated",
                                                 "_meta": {"action": "start"}})
        started = await asyncio.wait_for(fut, 60) or {}
        info = (started.get("_meta") or {}).get("browser-auth-delegated") or {}
        flow.url = info.get("signInUrl", "")
        attempt = info.get("attemptId", "")
        if not flow.url or not attempt:
            raise RuntimeError("Mistral did not hand out a sign-in link.")
        flow.state = "waiting_browser"
        flow.message = ("Sign in with your Mistral account on the page that opens. "
                        "This screen updates by itself.")
        fut = await rpc.request("authenticate", {"methodId": "browser-auth-delegated",
                                                 "_meta": {"action": "complete",
                                                           "attemptId": attempt}})
        remaining = max(30, flow.expires - time.time())
        done = await asyncio.wait_for(fut, remaining) or {}
        result = (done.get("_meta") or {}).get("browser-auth-delegated") or {}
        if result.get("status") not in (None, "completed"):
            raise RuntimeError("Mistral did not confirm the sign-in.")
        flow.state, flow.message = "done", "Mistral is connected."
    except asyncio.TimeoutError:
        raise RuntimeError("The sign-in took too long. Start it again.") from None
    finally:
        read_task.cancel()
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)


# ------------------------------------------------------------------ sign out

def _vibe_env_path():
    return clis.terminal.PERSIST_HOME / ".vibe" / ".env"


def vibe_key() -> str:
    """The key Vibe stored at sign-in (read only to tell whether it is signed
    in and which plan it is on — never sent to a client)."""
    path = _vibe_env_path()
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "MISTRAL_API_KEY":
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


async def sign_out(engine: str) -> None:
    if engine == "claude-code":
        config.set_engine_token("claude-code", "")
        if engines.installed("claude-code"):
            await _run([engines.binary("claude-code"), "auth", "logout"])
    elif engine == "codex":
        if engines.installed("codex"):
            await _run([engines.binary("codex"), "logout"])
    elif engine == "mistral-vibe":
        path = _vibe_env_path()
        try:
            lines = [ln for ln in path.read_text().splitlines()
                     if ln.partition("=")[0].strip() != "MISTRAL_API_KEY"]
            path.write_text("\n".join(lines) + ("\n" if lines else ""))
        except OSError:
            pass
    else:
        raise ValueError("unknown engine")
    audit.record("ai_signout", target=engines.ENGINES[engine]["label"])
    from . import accounts
    accounts.forget_status(engine)


async def _run(argv: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=engines._env(), cwd=str(clis.terminal.PERSIST_HOME))
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode("utf-8", "replace")
    except (OSError, asyncio.TimeoutError):
        return 1, ""
