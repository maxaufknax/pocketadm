"""Interactive PTY terminal over WebSocket.

Contexts:
  - "local":  a shell where PocketADM runs. In the container image this shell
    ships with the docker CLI + mounted socket, so it can manage the whole host.
  - "host:<user>": a *real login shell as one of the host's Linux users*
    (e.g. maxaufknax@stream). Runs `su - <user>` inside a throwaway container
    that chroots the mounted host root — the same door hostuser.py already uses.
    This is what lets a user actually run the commands the agent suggests
    (sudo, systemctl, files under their home) instead of only inside our box.
  - "container:<id>": docker exec -it into any running container.
  - "ssh": shell on the host via SSH, if HOST_SSH (user@host) is configured.

Protocol (JSON text frames from client): {"type":"input","data":...},
{"type":"resize","cols":N,"rows":N}. Server sends raw text frames (output).
"""
import asyncio
import fcntl
import os
import pty
import re
import shutil
import signal
import struct
import termios

from fastapi import WebSocket, WebSocketDisconnect

from . import config, hostuser

# The local shell gets a HOME on the data volume so anything installed there
# (Claude Code / Codex / Mistral Vibe CLI, their login sessions, dotfiles, shell
# history) survives container recreates and updates.
PERSIST_HOME = config.DATA_DIR / "home"
HELPER_IMAGE = os.environ.get("HELMSMAN_IMAGE", "helmsman:latest")

# /etc/profile in the base image *resets* PATH for every login shell, which drops
# ~/.local/bin — so `claude` / `codex` / `vibe` become "command not found" even
# though they are installed. A login shell reads ~/.bash_profile AFTER /etc/profile,
# so putting our PATH restore there wins. This was the exact bug users hit.
_BASH_PROFILE = """# Managed by PocketADM — restores user-installed tools onto PATH.
# (the base image's /etc/profile resets PATH for login shells and drops these)
for _d in "$HOME/.local/bin" "$HOME/bin"; do
  case ":$PATH:" in *":$_d:"*) : ;; *) [ -d "$_d" ] && PATH="$_d:$PATH" ;; esac
done
export PATH
[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
"""

# A clear, calm prompt so the built-in shell reads as "the PocketADM app box",
# not root@<random-hex> — part of making the many terminal identities legible.
_BASHRC = """# Managed by PocketADM.
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
if [ -z "$PS1" ]; then :; else
  PS1='\\[\\e[38;5;39m\\]pocketadm\\[\\e[0m\\]:\\[\\e[38;5;245m\\]\\w\\[\\e[0m\\]$ '
fi
alias ll='ls -alF'
"""


def _ensure_profile() -> None:
    """Idempotently drop the PATH-restoring login profile into the persistent HOME."""
    try:
        PERSIST_HOME.mkdir(exist_ok=True)
        for name, content in ((".bash_profile", _BASH_PROFILE), (".bashrc", _BASHRC)):
            f = PERSIST_HOME / name
            if not f.exists() or "Managed by PocketADM" not in f.read_text(errors="replace"):
                f.write_text(content)
    except OSError:
        pass


def _local_env(env: dict) -> dict:
    try:
        _ensure_profile()
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
    if context.startswith("host:"):
        return _host_user_command(context.split(":", 1)[1])
    if context == "ssh":
        target = os.environ.get("HOST_SSH", "")
        if not target:
            raise ValueError("HOST_SSH not configured")
        return ["ssh", "-tt", "-o", "StrictHostKeyChecking=accept-new", target]
    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    return [shell, "-l"]


def _host_user_command(user: str) -> list[str]:
    """A real login shell as a host Linux user, via chroot-into-/host.

    Opens no new door: PocketADM already controls the host through the mounted
    Docker socket (hostuser.py uses the identical throwaway-chroot pattern for
    account management). --hostname makes the prompt read e.g. maxaufknax@stream.
    """
    if not hostuser.NAME_RE.match(user or ""):
        raise ValueError("bad user name")
    if not hostuser._can_manage():
        raise ValueError(hostuser._manage_reason() or "host shell not available here")
    if not any(u["name"] == user for u in hostuser.list_users()):
        raise ValueError(f"no host account named '{user}'")
    hostname = hostuser._hostname() or "host"
    if not re.match(r"^[a-zA-Z0-9._-]{1,64}$", hostname):
        hostname = "host"
    # `su - <user>` = full login shell (their env, their home as cwd, no password
    # needed since we are uid 0). chroot /host makes the host filesystem the root,
    # so files, sudo group membership and the host's docker.sock are all real.
    # (No --net host: it conflicts with --hostname on some Docker versions, and
    #  the correct maxaufknax@stream prompt matters more for portability.)
    return ["docker", "run", "--rm", "-it", "--hostname", hostname,
            "-v", "/:/host", "-e", "LANG=C.UTF-8",
            HELPER_IMAGE, "chroot", "/host", "su", "-", user]


async def handle_terminal(ws: WebSocket, context: str = "local") -> None:
    try:
        cmd = build_command(context)
    except ValueError as e:
        await ws.send_text(f"\r\n[pocketadm] {e}\r\n")
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
