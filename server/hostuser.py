"""Server identity + system-user inspection and management.

Reads the host's account database (mounted read-only at /host) so the app can
show *who* the server is built on — the real hostname, OS, and the Linux users
with their rights (login, admin/sudo, service accounts) — in plain language.

For changes (set a password, lock an account, grant/revoke admin, add a user)
it runs the matching host tool inside a throwaway container that chroots the
real host root:  `docker run --rm -i -v /:/host helmsman chroot /host <tool>`.
Helmsman already controls the host through the mounted Docker socket, so this
opens no new door — it just exposes a few safe, audited actions to an admin who
would otherwise need the command line. Secrets are fed on stdin, never argv.
"""
import asyncio
import os
import re
import shutil
import time
from pathlib import Path

HOST = "/host" if os.path.isdir("/host") else ""
HELPER_IMAGE = os.environ.get("HELMSMAN_IMAGE", "helmsman:latest")

# Usernames: portable POSIX rule (lower-case start, optional trailing $ for machine accts)
NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\$?$")
ADMIN_GROUP_CANDIDATES = ("sudo", "wheel", "admin")
NOLOGIN_SHELLS = {"", "/usr/sbin/nologin", "/sbin/nologin", "/bin/false",
                  "/usr/bin/false", "/bin/true"}
UID_HUMAN_MIN = 1000
UID_NOBODY = 65534


# ------------------------------------------------------------- identity

def _os_release() -> dict:
    info = {}
    try:
        for line in Path(HOST + "/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v.strip().strip('"')
    except OSError:
        pass
    return info


def identity() -> dict:
    """The server's real identity — the technical facts, plainly labelled."""
    uname = os.uname()
    rel = _os_release()
    host_present = bool(HOST)
    admin_group = _admin_group()
    users = list_users()
    humans = [u for u in users if u["kind"] == "human"]
    return {
        "hostname": _hostname(),
        "os": rel.get("PRETTY_NAME") or rel.get("NAME") or uname.sysname,
        "os_id": rel.get("ID", ""),
        "kernel": uname.release,
        "arch": uname.machine,
        "in_container": os.path.exists("/.dockerenv"),
        "host_access": host_present and _can_manage(),
        "manage_reason": _manage_reason(),
        "admin_group": admin_group,
        "counts": {
            "total": len(users),
            "human": len(humans),
            "admins": sum(1 for u in users if u["is_admin"]),
            "system": sum(1 for u in users if u["kind"] == "system"),
        },
    }


def _hostname() -> str:
    for path in (HOST + "/etc/hostname",):
        try:
            return Path(path).read_text().strip() or os.uname().nodename
        except OSError:
            pass
    return os.environ.get("HELMSMAN_HOSTNAME") or os.uname().nodename


# ------------------------------------------------------------- users

def _read_passwd() -> list[dict]:
    out = []
    try:
        for line in Path(HOST + "/etc/passwd").read_text().splitlines():
            parts = line.split(":")
            if len(parts) < 7:
                continue
            out.append({"name": parts[0], "uid": _int(parts[2]), "gid": _int(parts[3]),
                        "gecos": parts[4].split(",")[0].strip(), "home": parts[5],
                        "shell": parts[6]})
    except OSError:
        pass
    return out


def _read_groups() -> tuple[dict[int, str], dict[str, list[str]]]:
    """-> (gid->groupname, username->[supplementary group names])."""
    gid_name: dict[int, str] = {}
    member_of: dict[str, list[str]] = {}
    try:
        for line in Path(HOST + "/etc/group").read_text().splitlines():
            parts = line.split(":")
            if len(parts) < 4:
                continue
            name, gid, members = parts[0], _int(parts[2]), parts[3]
            gid_name[gid] = name
            for m in filter(None, members.split(",")):
                member_of.setdefault(m, []).append(name)
    except OSError:
        pass
    return gid_name, member_of


def _locked_map() -> dict[str, bool | None]:
    """user -> is the password locked? (None if /etc/shadow is unreadable)."""
    out: dict[str, bool | None] = {}
    try:
        for line in Path(HOST + "/etc/shadow").read_text().splitlines():
            parts = line.split(":")
            if len(parts) < 2:
                continue
            h = parts[1]
            # '!'/'*' prefix or '!'/'*'/'' hash means no usable password
            out[parts[0]] = h.startswith(("!", "*")) or h in ("", "!", "*")
    except OSError:
        pass
    return out


def _admin_group() -> str:
    _, groups_by_name = _all_group_names()
    for g in ADMIN_GROUP_CANDIDATES:
        if g in groups_by_name:
            return g
    return "sudo"


def _all_group_names() -> tuple[dict[int, str], set[str]]:
    gid_name, _ = _read_groups()
    names = set(gid_name.values())
    return gid_name, names


def list_users() -> list[dict]:
    """Every account, classified and annotated for a non-technical admin."""
    gid_name, member_of = _read_groups()
    locked = _locked_map()
    admin_group = _admin_group()
    users = []
    for p in _read_passwd():
        name, uid, shell = p["name"], p["uid"], p["shell"]
        primary = gid_name.get(p["gid"], str(p["gid"]))
        groups = sorted({primary, *member_of.get(name, [])})
        is_admin = uid == 0 or admin_group in groups or bool(
            {"wheel", "admin", "sudo"} & set(groups))
        is_locked = locked.get(name)
        can_login = shell not in NOLOGIN_SHELLS and is_locked is not True
        kind = "human" if uid == 0 or (UID_HUMAN_MIN <= uid < UID_NOBODY) else "system"
        users.append({
            "name": name, "uid": uid, "shell": shell, "home": p["home"],
            "gecos": p["gecos"], "groups": groups, "primary_group": primary,
            "kind": kind, "is_admin": is_admin, "is_root": uid == 0,
            "locked": is_locked, "can_login": can_login,
            "in_docker": "docker" in groups,
            "role": _role_label(uid, is_admin, can_login, kind),
        })
    users.sort(key=lambda u: (u["kind"] != "human", not u["is_admin"], u["uid"]))
    return users


def _role_label(uid: int, is_admin: bool, can_login: bool, kind: str) -> str:
    if uid == 0:
        return "Administrator (root)"
    if kind == "system":
        return "Service account"
    if is_admin:
        return "Administrator" + ("" if can_login else " · login disabled")
    if can_login:
        return "Standard user"
    return "User · login disabled"


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return -1


# ------------------------------------------------- privileged host helper

def _can_manage() -> bool:
    return bool(HOST) and os.path.exists("/var/run/docker.sock") \
        and shutil.which("docker") is not None


def _manage_reason() -> str:
    if not HOST:
        return "Running without the host mount — inspection only."
    if not _can_manage():
        return "The Docker socket isn't available, so accounts are read-only here."
    return ""


def _validate_name(user: str) -> None:
    if not NAME_RE.match(user or ""):
        raise ValueError("Invalid username")


async def _host_exec(argv: list[str], stdin: str = "", timeout: int = 40) -> tuple[bool, str]:
    """Run a host binary inside a throwaway chroot-into-/host container."""
    if not _can_manage():
        raise RuntimeError(_manage_reason() or "Host management is not available.")
    cmd = ["docker", "run", "--rm", "-i", "-v", "/:/host", "-e", "LANG=C",
           HELPER_IMAGE, "chroot", "/host", *argv]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin else None), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "The operation timed out."
    return proc.returncode == 0, out.decode("utf-8", "replace").strip()


def _find(user: str) -> dict:
    for u in list_users():
        if u["name"] == user:
            return u
    raise ValueError(f"No account named '{user}'")


async def set_password(user: str, password: str) -> str:
    _validate_name(user)
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    _find(user)  # ensure it exists
    ok, out = await _host_exec(["/usr/sbin/chpasswd"], stdin=f"{user}:{password}\n")
    if not ok:
        raise RuntimeError(out or "chpasswd failed")
    return f"Password updated for {user}."


async def set_locked(user: str, locked: bool) -> str:
    _validate_name(user)
    u = _find(user)
    if u["is_root"] and locked:
        raise ValueError("Refusing to lock the root account")
    ok, out = await _host_exec(["/usr/sbin/usermod", "-L" if locked else "-U", user])
    if not ok:
        raise RuntimeError(out or "usermod failed")
    return f"{user} {'locked' if locked else 'unlocked'}."


async def set_admin(user: str, on: bool) -> str:
    _validate_name(user)
    u = _find(user)
    if u["is_root"]:
        raise ValueError("root is always an administrator")
    group = _admin_group()
    if on:
        ok, out = await _host_exec(["/usr/sbin/usermod", "-aG", group, user])
    else:
        ok, out = await _host_exec(["/usr/bin/gpasswd", "-d", user, group])
    if not ok:
        raise RuntimeError(out or "group change failed")
    return f"{user} is {'now an administrator' if on else 'no longer an administrator'}."


async def create_user(user: str, password: str, admin: bool = False) -> str:
    _validate_name(user)
    if any(u["name"] == user for u in list_users()):
        raise ValueError(f"A user named '{user}' already exists")
    if password and len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    ok, out = await _host_exec(["/usr/sbin/useradd", "-m", "-s", "/bin/bash", user])
    if not ok:
        raise RuntimeError(out or "useradd failed")
    if password:
        await set_password(user, password)
    if admin:
        await set_admin(user, True)
    return f"Created user {user}."
