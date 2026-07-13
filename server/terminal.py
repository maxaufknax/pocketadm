"""Interactive PTY terminal over WebSocket.

Contexts:
  - "local":  a shell where Helmsman runs. In the container image this shell
    ships with the docker CLI + mounted socket, so it can manage the whole host.
  - "container:<id>": docker exec -it into any running container.
  - "ssh": shell on the host via SSH, if HOST_SSH (user@host) is configured.

Protocol (JSON text frames from client): {"type":"input","data":...},
{"type":"resize","cols":N,"rows":N}. Server sends raw text frames (output).
"""
import asyncio
import fcntl
import os
import pty
import shutil
import signal
import struct
import termios

from fastapi import WebSocket, WebSocketDisconnect

from . import config

# The local shell gets a HOME on the data volume so anything installed there
# (Claude Code / Codex CLI, their login sessions, dotfiles, shell history)
# survives container recreates and updates.
PERSIST_HOME = config.DATA_DIR / "home"


def _local_env(env: dict) -> dict:
    try:
        PERSIST_HOME.mkdir(exist_ok=True)
        env["HOME"] = str(PERSIST_HOME)
        env["PATH"] = f"{PERSIST_HOME}/.local/bin:{PERSIST_HOME}/bin:" + env.get("PATH", "")
    except OSError:
        pass
    return env


def build_command(context: str) -> list[str]:
    if context.startswith("container:"):
        cid = context.split(":", 1)[1]
        if not all(c.isalnum() or c in "-_." for c in cid):
            raise ValueError("bad container id")
        return ["docker", "exec", "-it", cid, "sh", "-c",
                "command -v bash >/dev/null && exec bash || exec sh"]
    if context == "ssh":
        target = os.environ.get("HOST_SSH", "")
        if not target:
            raise ValueError("HOST_SSH not configured")
        return ["ssh", "-tt", "-o", "StrictHostKeyChecking=accept-new", target]
    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    return [shell, "-l"]


async def handle_terminal(ws: WebSocket, context: str = "local") -> None:
    try:
        cmd = build_command(context)
    except ValueError as e:
        await ws.send_text(f"\r\n[helmsman] {e}\r\n")
        await ws.close()
        return

    pid, fd = pty.fork()
    if pid == 0:  # child
        env = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8")
        if context == "local":
            env = _local_env(env)
        try:
            os.execvpe(cmd[0], cmd, env)
        except FileNotFoundError:
            os.write(2, f"{cmd[0]}: not found\n".encode())
        os._exit(1)

    loop = asyncio.get_running_loop()
    os.set_blocking(fd, False)
    closed = asyncio.Event()

    def on_readable() -> None:
        try:
            data = os.read(fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if data:
            asyncio.ensure_future(_safe_send(ws, data))
        else:
            loop.remove_reader(fd)
            closed.set()

    loop.add_reader(fd, on_readable)

    try:
        while not closed.is_set():
            recv = asyncio.create_task(ws.receive_json())
            done, _ = await asyncio.wait(
                {recv, asyncio.create_task(closed.wait())},
                return_when=asyncio.FIRST_COMPLETED)
            if recv not in done:
                recv.cancel()
                break
            msg = recv.result()
            if msg.get("type") == "input":
                os.write(fd, msg.get("data", "").encode())
            elif msg.get("type") == "resize":
                winsz = struct.pack("HHHH", int(msg.get("rows", 24)), int(msg.get("cols", 80)), 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsz)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            await ws.close()
        except Exception:
            pass


async def _safe_send(ws: WebSocket, data: bytes) -> None:
    try:
        await ws.send_text(data.decode("utf-8", "replace"))
    except Exception:
        pass
