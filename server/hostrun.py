"""Run agent commands and file writes directly on the HOST.

The app container mounts the host filesystem read-only at /host, so the agent
could inspect the server but not act on it like an admin would: host paths
needed the /host prefix, edits failed on the ro mount, and compose builds from
/host resolved relative bind mounts wrongly. This module gives the agent a real
host execution context using the same throwaway chroot-into-/host helper that
hostuser.py and terminal.py already use (no new trust boundary — the app holds
the Docker socket and is root-on-host either way):

    docker run --rm -i --network host -v /:/host <image> chroot /host bash -c …

Inside the chroot, paths are the host's real paths, the filesystem is
writable, and /var/run/docker.sock is the host's own socket, so docker compose
run from a project's working dir behaves exactly as if typed on the host.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

HELPER_IMAGE = os.environ.get("HELMSMAN_IMAGE", "helmsman:latest")
HOST = "/host"
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def available() -> bool:
    return (os.path.isdir(HOST)
            and os.path.exists("/var/run/docker.sock")
            and shutil.which("docker") is not None)


def to_host_path(path: str) -> str:
    """Translate an in-container view of a host path to the real host path."""
    if path == HOST:
        return "/"
    if path.startswith(HOST + "/"):
        return path[len(HOST):]
    return path


def host_cwd_for(workdir: str) -> str | None:
    """The host-side working directory for a session workdir, or None if the
    workdir only exists inside the app container (e.g. /data)."""
    cand = to_host_path(workdir or "/")
    return cand if os.path.isdir(HOST + cand) else None


async def run(command: str, timeout: int = 60, cwd: str | None = None) -> tuple[int, str]:
    """Execute a shell command on the host as root. Returns (exit_code, output)."""
    name = f"pocketadm-exec-{uuid.uuid4().hex[:10]}"
    script = command if not cwd else f"cd {_sq(cwd)} && {command}"
    argv = ["docker", "run", "--rm", "-i", "--name", name, "--network", "host",
            "-v", "/:/host", "-e", "LANG=C.UTF-8", "-e", f"PATH={_PATH}",
            HELPER_IMAGE, "chroot", HOST, "/bin/bash", "-c", script]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        # killing the docker CLI client does not stop the helper container
        asyncio.get_running_loop().create_task(_reap(name))
        return 124, f"[timed out after {timeout}s]"
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def write_file(path: str, content: str) -> None:
    """Write a file on the host. Overwrites keep the file's owner and mode;
    new files inherit the parent directory's owner instead of staying root's."""
    script = ('d="$(dirname "$1")"; mkdir -p "$d" || exit 1; '
              'if [ ! -e "$1" ]; then : > "$1" && '
              'chown --reference="$d" "$1" 2>/dev/null; fi; '
              'cat > "$1"')
    argv = ["docker", "run", "--rm", "-i", "-v", "/:/host",
            HELPER_IMAGE, "chroot", HOST, "/bin/sh", "-c", script, "sh", path]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(content.encode()), 60)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("host write timed out")
    if proc.returncode != 0:
        raise RuntimeError(out.decode("utf-8", "replace").strip() or "host write failed")


async def _reap(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker", "rm", "-f", name,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()


def _sq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
