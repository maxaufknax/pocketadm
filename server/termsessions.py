"""Persistent, server-side terminal sessions (tmux-style).

The old model tied one PTY to one WebSocket: closing the app killed the shell.
Here the PTY lives in a server-side session object instead — websockets only
*attach* to it. That means:

  - locking the phone / switching devices does NOT kill what's running
    (Claude Code, a compile, a migration keep going in the background),
  - the same session can be watched live from several devices at once
    (like the Vibe chats), and
  - re-attaching replays the recent scrollback so you see where things stand.

Sessions hold a bounded ring buffer of raw terminal output (~256 KB). Replaying
it into a fresh xterm reproduces the screen well enough in practice — the same
trade ttyd/tmux attach make. Sessions do not survive a PocketADM restart
(the processes are its children); that limitation is by design and documented
in the UI.
"""
import asyncio
import os
import pty
import secrets
import signal
import struct
import time

import fcntl
import termios

from fastapi import WebSocket

from . import terminal

BUFFER_CAP = 256 * 1024          # scrollback bytes kept per session
MAX_LIVE = 12                    # refuse to spawn more concurrent shells
DEAD_LINGER = 30 * 60            # ended sessions stay listed this long (s)


class TermSession:
    def __init__(self, context: str, title: str):
        self.id = secrets.token_hex(4)
        self.context = context
        self.title = title or context
        self.created = time.time()
        self.last_active = self.created
        self.ended_at: float | None = None
        self.alive = False
        self.pid = -1
        self.fd = -1
        self.cols = 80
        self.rows = 24
        self.buffer = bytearray()
        self.subscribers: set[WebSocket] = set()

    # ---------------------------------------------------------- lifecycle

    def spawn(self) -> None:
        cmd = terminal.build_command(self.context)   # raises ValueError on bad ctx
        pid, fd = pty.fork()
        if pid == 0:  # child
            env = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8")
            if self.context == "local":
                env = terminal._local_env(env)
            try:
                os.execvpe(cmd[0], cmd, env)
            except FileNotFoundError:
                os.write(2, f"{cmd[0]}: not found\n".encode())
            os._exit(1)
        self.pid, self.fd = pid, fd
        self.alive = True
        os.set_blocking(fd, False)
        asyncio.get_running_loop().add_reader(fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self.fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if data:
            self.last_active = time.time()
            self.buffer.extend(data)
            if len(self.buffer) > BUFFER_CAP:
                # trim from the front, preferably to a line break so the
                # replayed scrollback doesn't start mid-escape-sequence
                excess = len(self.buffer) - BUFFER_CAP
                nl = self.buffer.find(b"\n", excess, excess + 4096)
                del self.buffer[: (nl + 1) if nl != -1 else excess]
            self._broadcast(data.decode("utf-8", "replace"))
        else:
            self._mark_ended()

    def _mark_ended(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            loop.remove_reader(self.fd)
        except Exception:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.alive = False
        self.ended_at = time.time()
        self._broadcast("\r\n\x1b[90m[session ended]\x1b[0m\r\n")
        asyncio.ensure_future(self._reap_child())

    async def _reap_child(self) -> None:
        """Collect the exit status so the child doesn't linger as a zombie."""
        for _ in range(20):
            try:
                pid, _status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if pid:
                return
            await asyncio.sleep(0.5)
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError, OSError):
            pass

    def _broadcast(self, text: str) -> None:
        for ws in list(self.subscribers):
            asyncio.ensure_future(self._send(ws, text))

    async def _send(self, ws: WebSocket, text: str) -> None:
        try:
            await ws.send_text(text)
        except Exception:
            self.subscribers.discard(ws)

    # ------------------------------------------------------------- client

    async def attach(self, ws: WebSocket) -> None:
        """Replay scrollback, then pump client frames until it disconnects.
        Detaching leaves the session (and whatever runs in it) untouched."""
        if self.buffer:
            await self._send(ws, self.buffer.decode("utf-8", "replace"))
        if not self.alive:
            await self._send(ws, "\r\n\x1b[90m[this session has ended — scrollback only]\x1b[0m\r\n")
        self.subscribers.add(ws)
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "input" and self.alive:
                    self.last_active = time.time()
                    try:
                        os.write(self.fd, msg.get("data", "").encode())
                    except OSError:
                        pass
                elif msg.get("type") == "resize" and self.alive:
                    self.cols = int(msg.get("cols", 80))
                    self.rows = int(msg.get("rows", 24))
                    try:
                        winsz = struct.pack("HHHH", self.rows, self.cols, 0, 0)
                        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsz)
                    except OSError:
                        pass
        except Exception:
            pass
        finally:
            self.subscribers.discard(ws)

    def kill(self) -> None:
        """End the session for real: polite EOF first (lets `su -`/exec'd
        shells exit cleanly so their --rm helper containers get removed),
        then signals."""
        if self.alive:
            try:
                os.write(self.fd, b"\x03")    # ^C anything in the foreground
                os.write(self.fd, b"\x04")    # ^D → shell exits at the prompt
            except OSError:
                pass
            async def _finish(pid: int) -> None:
                await asyncio.sleep(1.5)
                for sig in (signal.SIGHUP, signal.SIGTERM):
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        return
            asyncio.ensure_future(_finish(self.pid))

    def meta(self) -> dict:
        return {
            "id": self.id, "title": self.title, "context": self.context,
            "created": self.created, "last_active": self.last_active,
            "alive": self.alive, "clients": len(self.subscribers),
        }


_sessions: dict[str, TermSession] = {}


def _reap() -> None:
    now = time.time()
    for sid, s in list(_sessions.items()):
        if not s.alive and not s.subscribers and now - (s.ended_at or 0) > DEAD_LINGER:
            del _sessions[sid]


def create(context: str, title: str = "") -> TermSession:
    _reap()
    if sum(1 for s in _sessions.values() if s.alive) >= MAX_LIVE:
        raise ValueError(f"too many open sessions (max {MAX_LIVE}) — close one first")
    s = TermSession(context, title)
    s.spawn()
    _sessions[s.id] = s
    return s


def get(sid: str) -> TermSession | None:
    return _sessions.get(sid)


def list_meta() -> list[dict]:
    _reap()
    return sorted((s.meta() for s in _sessions.values()),
                  key=lambda m: m["last_active"], reverse=True)


def close(sid: str) -> bool:
    s = _sessions.pop(sid, None)
    if not s:
        return False
    s.kill()
    return True
