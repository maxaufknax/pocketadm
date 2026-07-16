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


# ============================================================ demo terminal
# In the public demo there is no host, no docker socket and a shared password,
# so a real PTY would hand every visitor a root shell. Instead we serve a
# *simulated* shell: it renders the same xterm UI (proving the native terminal,
# key bar and resize all work — the Guideline 4.2 argument) while only ever
# replaying canned output for the fake demo homeserver. Nothing is executed.

_DEMO_PROMPT = "\x1b[38;5;39mdemo\x1b[0m:\x1b[38;5;245m~\x1b[0m$ "
_DEMO_BANNER = (
    "\x1b[38;5;245mPocketADM demo shell — a sandbox, not a real host.\r\n"
    "Try: \x1b[0mls  ps  docker ps  free  df  uptime  cat readme.txt\x1b[38;5;245m  ·  "
    "install PocketADM on your own server for a real terminal.\x1b[0m\r\n\r\n")


def _demo_exec(line: str) -> str:
    """Canned output for one command line against the fake demo homeserver."""
    from . import demodata
    parts = line.split()
    if not parts:
        return ""
    cmd, args = parts[0], parts[1:]
    if cmd in ("help", "?"):
        return ("Available in the demo: ls, pwd, whoami, id, hostname, uname, "
                "uptime, date, free, df, ps, docker ps|images, cat, echo, clear.\r\n"
                "This is a read-only sandbox — nothing is really executed.")
    if cmd == "ls":
        return ("compose  data  media  \x1b[38;5;39mreadme.txt\x1b[0m"
                if not args else "readme.txt")
    if cmd == "pwd":
        return "/home/demo"
    if cmd in ("whoami",):
        return "demo"
    if cmd == "id":
        return "uid=1000(demo) gid=1000(demo) groups=1000(demo)"
    if cmd == "hostname":
        return "demo"
    if cmd == "uname":
        return ("Linux demo 6.8.0-demo x86_64 GNU/Linux"
                if "-a" in args else "Linux")
    if cmd == "uptime":
        return " 12:00:00 up 30 days,  4:12,  1 user,  load average: 0.18, 0.22, 0.19"
    if cmd == "date":
        return "Sat Jul 11 12:00:00 UTC 2026"
    if cmd == "free":
        return ("               total        used        free      shared\r\n"
                "Mem:            7.7Gi       3.1Gi       2.0Gi       0.3Gi\r\n"
                "Swap:           2.0Gi          0B       2.0Gi")
    if cmd == "df":
        return ("Filesystem      Size  Used Avail Use% Mounted on\r\n"
                "/dev/sda1       226G   78G  137G  37% /")
    if cmd == "echo":
        return " ".join(args)
    if cmd == "cat":
        if args and args[0] in ("readme.txt", "./readme.txt", "~/readme.txt"):
            return ("This is the PocketADM public demo. The data is sample data and\r\n"
                    "this shell is simulated — install PocketADM on your own server\r\n"
                    "for a real terminal, dashboard and AI agent. https://pocketadm.com")
        return f"cat: {args[0] if args else ''}: No such file or directory"
    if cmd == "clear":
        return "\x1b[2J\x1b[H"
    if cmd == "docker":
        if args[:1] == ["ps"]:
            rows = ["CONTAINER ID   IMAGE                       STATUS          NAMES"]
            for c in demodata.list_containers(all_=False):
                rows.append(f"{c['id']:<14} {c['image'][:26]:<26} "
                            f"{'Up':<15} {c['name']}")
            return "\r\n".join(rows)
        if args[:1] == ["images"]:
            return ("REPOSITORY                 TAG       SIZE\r\n"
                    "nextcloud                  29        1.1GB\r\n"
                    "jellyfin/jellyfin          latest    412MB\r\n"
                    "vaultwarden/server         latest    198MB")
        return "docker: only 'ps' and 'images' are available in the demo."
    if cmd == "ps":
        return ("  PID TTY          TIME CMD\r\n"
                "    1 ?        00:00:01 pocketadm\r\n"
                "   42 pts/0    00:00:00 bash\r\n"
                "   57 pts/0    00:00:00 ps")
    if cmd in ("sudo", "su", "rm", "apt", "apt-get", "curl", "wget", "ssh", "nc",
               "kill", "chmod", "chown", "mkfs", "dd"):
        return f"{cmd}: disabled in the read-only demo."
    return f"demo: {cmd}: command not found (this is a simulated demo shell)"


async def demo_terminal(ws: WebSocket) -> None:
    """A safe, simulated shell for demo mode — never spawns a process."""
    buf = ""
    try:
        await ws.send_text(_DEMO_BANNER + _DEMO_PROMPT)
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "input":
                continue
            for ch in msg.get("data", ""):
                if ch in ("\r", "\n"):
                    await ws.send_text("\r\n")
                    out = _demo_exec(buf.strip())
                    buf = ""
                    if out:
                        await ws.send_text(out.replace("\n", "\r\n")
                                           if "\r" not in out else out)
                        await ws.send_text("\r\n")
                    await ws.send_text(_DEMO_PROMPT)
                elif ch in ("\x7f", "\b"):
                    if buf:
                        buf = buf[:-1]
                        await ws.send_text("\b \b")
                elif ch == "\x03":            # Ctrl-C
                    buf = ""
                    await ws.send_text("^C\r\n" + _DEMO_PROMPT)
                elif ch == "\x0c":            # Ctrl-L
                    buf = ""
                    await ws.send_text("\x1b[2J\x1b[H" + _DEMO_PROMPT)
                elif ch >= " ":
                    buf += ch
                    await ws.send_text(ch)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
