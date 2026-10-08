"""The file browser: the server's folders, its drives and what fills them.

Everything is read-only. The browser shows the host's filesystem through the
/host mount, so paths travel with the /host prefix on the wire and carry a
`display` form ("/srv/x") for people. Browsing is limited to the configured
workspaces (by default the whole host); PocketADM's own credential files stay
unreadable here, exactly as they are for the agent.
"""
from __future__ import annotations

import asyncio
import grp
import os
import pwd
import stat
import time
from pathlib import Path

from . import ai, config

HOST = "/host" if os.path.isdir("/host") else ""

# file kinds we can safely preview as text
TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".conf", ".cfg", ".ini", ".env",
            ".yml", ".yaml", ".json", ".toml", ".xml", ".html", ".htm", ".css",
            ".js", ".ts", ".jsx", ".tsx", ".py", ".sh", ".bash", ".zsh", ".rb",
            ".go", ".rs", ".c", ".h", ".cpp", ".java", ".php", ".sql", ".csv",
            ".service", ".timer", ".gitignore", ".dockerignore", ".properties", ".swift",
            ".kt", ".lua", ".pl", ".vue", ".svelte", ".scss", ".less", ".nginx", ".rules",
            ".list", ".lock", ".tf", ".hcl", ".mod", ".sum", ".gradle", ".j2", ".tpl"}
TEXT_NAMES = {"Dockerfile", "docker-compose.yml", "Makefile", "LICENSE", "README",
              ".env", ".gitignore", "requirements.txt", "Caddyfile", "Procfile",
              "crontab", "hosts", "fstab", "passwd", "group", "hostname"}
LIST_CAP = 2000


def looks_text(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_EXT or name in TEXT_NAMES or ("." not in name and not name.startswith("."))


def display(path: str) -> str:
    """The host's own name for a path seen through the /host mount."""
    if HOST and (path == HOST or path.startswith(HOST + "/")):
        return path[len(HOST):] or "/"
    return path


def roots() -> list[str]:
    out: list[str] = []
    for r in config.get_workspaces() + [ai.DEFAULT_WORKDIR]:
        rp = os.path.realpath(r)
        if os.path.isdir(rp) and rp not in out:
            out.append(rp)
    return out


def within(resolved: str, allowed: list[str]) -> bool:
    return any(resolved == r or resolved.startswith(r.rstrip("/") + os.sep) for r in allowed)


def resolve(path: str) -> str:
    """A requested path, made real and checked against the allowed roots.
    Accepts the host's own form ("/srv/x") as well as the /host form; the
    host's form wins when both exist."""
    allowed = roots()
    candidates = [path]
    if HOST and path.startswith("/") and not (path == HOST or path.startswith(HOST + "/")):
        candidates.insert(0, HOST + path)
    for candidate in candidates:
        resolved = os.path.realpath(candidate)
        if within(resolved, allowed) and os.path.exists(resolved):
            return resolved
    resolved = os.path.realpath(candidates[-1])
    if not within(resolved, allowed):
        raise PermissionError("outside the folders PocketADM may browse")
    return resolved


_owner_cache: dict[tuple[str, int], str] = {}


def _owner(uid: int, kind: str = "user") -> str:
    """User names come from the host's passwd, not the container's."""
    key = (kind, uid)
    if key in _owner_cache:
        return _owner_cache[key]
    name = str(uid)
    path = (HOST or "") + ("/etc/passwd" if kind == "user" else "/etc/group")
    try:
        with open(path) as f:
            for line in f:
                parts = line.split(":")
                if len(parts) > 2 and parts[2] == str(uid):
                    name = parts[0]
                    break
    except OSError:
        try:
            name = pwd.getpwuid(uid).pw_name if kind == "user" else grp.getgrgid(uid).gr_name
        except (KeyError, OSError):
            pass
    _owner_cache[key] = name
    return name


def listing(path: str, files: bool = True, hidden: bool = False) -> dict:
    allowed = roots()
    if not path:
        return {"path": "", "display": "", "parent": "", "dirs": [
                    {"name": display(r), "path": r, "display": display(r)} for r in allowed],
                "file_entries": [], "files": 0, "roots": allowed, "hidden": 0,
                "truncated": False}
    resolved = resolve(path)
    if not os.path.isdir(resolved):
        raise FileNotFoundError("not a directory")
    dirs, entries, file_count, hidden_count = [], [], 0, 0
    with os.scandir(resolved) as it:
        items = sorted(it, key=lambda e: e.name.lower())
    for e in items:
        if e.name.startswith("."):
            hidden_count += 1
            if not hidden:
                continue
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        is_link = stat.S_ISLNK(st.st_mode)
        try:
            is_dir = e.is_dir(follow_symlinks=True)
        except OSError:
            is_dir = False
        info = {"name": e.name, "path": os.path.join(resolved, e.name),
                "display": display(os.path.join(resolved, e.name)),
                "modified": st.st_mtime, "mode": stat.filemode(st.st_mode),
                "owner": _owner(st.st_uid), "link": is_link}
        if is_dir:
            if len(dirs) < LIST_CAP:
                dirs.append(info)
        else:
            file_count += 1
            if files and len(entries) < LIST_CAP:
                entries.append({**info, "size": st.st_size, "text": looks_text(e.name)})
    parent = os.path.dirname(resolved)
    if not within(parent, allowed) or parent == resolved:
        parent = ""
    try:
        here = os.statvfs(resolved)
        free = here.f_bavail * here.f_frsize
    except OSError:
        free = 0
    return {"path": resolved, "display": display(resolved), "parent": parent,
            "dirs": dirs, "file_entries": entries, "files": file_count,
            "hidden": hidden_count, "roots": allowed, "free": free,
            "truncated": len(dirs) >= LIST_CAP or (files and len(entries) >= LIST_CAP)}


def read_text(path: str, cap: int = 512 * 1024) -> dict:
    resolved = resolve(path)
    if ai.is_protected_path(resolved):
        raise PermissionError("this file holds PocketADM's own credentials")
    if not os.path.isfile(resolved):
        raise FileNotFoundError("not a file")
    size = os.path.getsize(resolved)
    with open(resolved, "rb") as fh:
        raw = fh.read(cap + 1)
    truncated = len(raw) > cap
    raw = raw[:cap]
    if b"\x00" in raw[:4096]:
        return {"path": resolved, "display": display(resolved), "size": size, "binary": True,
                "content": "", "truncated": truncated}
    return {"path": resolved, "display": display(resolved), "size": size, "binary": False,
            "truncated": truncated, "content": raw.decode("utf-8", "replace")}


def raw_path(path: str) -> str:
    """A file the app may download (QuickLook preview, share sheet)."""
    resolved = resolve(path)
    if ai.is_protected_path(resolved):
        raise PermissionError("this file holds PocketADM's own credentials")
    if not os.path.isfile(resolved):
        raise FileNotFoundError("not a file")
    return resolved


# ------------------------------------------------------------- drives

REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "exfat", "vfat", "msdos", "ntfs",
           "ntfs3", "fuseblk", "f2fs", "jfs", "reiserfs", "hfsplus", "apfs", "bcachefs",
           "nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "fuse.rclone", "9p",
           "fuse.mergerfs", "fuse.glusterfs", "ceph", "ext4dev"}
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "fuse.rclone", "9p",
              "fuse.glusterfs", "ceph"}


def parse_mountinfo(text: str) -> list[dict]:
    """Real filesystems from /proc/<pid>/mountinfo, one per device: bind mounts
    and container overlays are views of something already listed."""
    seen: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split()
        if " - " not in line or len(parts) < 10:
            continue
        left, right = line.split(" - ", 1)
        lp, rp = left.split(), right.split()
        if len(lp) < 5 or len(rp) < 2:
            continue
        dev, root, mount = lp[2], lp[3], lp[4].replace("\\040", " ")
        fstype, source = rp[0], rp[1]
        if fstype not in REAL_FS:
            continue
        if mount.startswith(("/snap/", "/var/lib/docker/", "/proc", "/sys", "/dev")) or \
                (mount.startswith("/run/") and not mount.startswith("/run/media/")):
            continue
        entry = {"mount": mount, "device": source, "fstype": fstype, "dev": dev, "root": root}
        prev = seen.get(dev)
        # keep the mount of the whole filesystem, then the shortest path
        if prev is None or (root == "/" and prev["root"] != "/") or \
                (root == prev["root"] and len(mount) < len(prev["mount"])):
            seen[dev] = entry
    return sorted(seen.values(), key=lambda e: (e["mount"] != "/", e["mount"]))


def _block_name(source: str, host: str) -> str:
    """/dev/sda1 -> sda1, /dev/mapper/vg-lv -> dm-0."""
    if not source.startswith("/dev/"):
        return ""
    real = os.path.realpath(host + source) if host else os.path.realpath(source)
    return os.path.basename(real)


def _disk_of(block: str, host: str) -> str:
    """The physical disk a partition or LVM volume lives on."""
    sys_block = f"{host}/sys/class/block/{block}"
    if not os.path.exists(sys_block):
        return block
    slaves = f"{host}/sys/block/{block}/slaves"
    if os.path.isdir(slaves):
        under = sorted(os.listdir(slaves))
        if under:
            return _disk_of(under[0], host)
    if os.path.exists(f"{host}/sys/block/{block}"):
        return block
    parent = os.path.basename(os.path.dirname(os.path.realpath(sys_block)))
    return parent or block


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _labels(host: str) -> dict[str, str]:
    out = {}
    base = f"{host}/dev/disk/by-label"
    try:
        for name in os.listdir(base):
            target = os.path.basename(os.readlink(os.path.join(base, name)))
            out[target] = name.replace("\\x20", " ")
    except OSError:
        pass
    return out


def storage() -> dict:
    """The server's drives as people think of them: the system disk, data
    disks, and the USB drive plugged in for backups — with how full each is."""
    host = HOST
    info_path = f"{host}/proc/1/mountinfo" if host else "/proc/self/mountinfo"
    text = _read(info_path)
    labels = _labels(host)
    out = []
    for fs in parse_mountinfo(text):
        path = (host + fs["mount"]) if host and fs["mount"] != "/" else (host or "/")
        try:
            st = os.statvfs(path)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        if total <= 0:
            continue
        block = _block_name(fs["device"], host)
        disk = _disk_of(block, host) if block else ""
        transport = ""
        if disk:
            link = os.path.realpath(f"{host}/sys/block/{disk}")
            transport = "usb" if "/usb" in link else "nvme" if disk.startswith("nvme") else \
                "sata" if disk.startswith("sd") else ""
        removable = _read(f"{host}/sys/block/{disk}/removable") == "1" if disk else False
        network = fs["fstype"] in NETWORK_FS
        external = transport == "usb" or removable
        kind = "network" if network else "external" if external else \
            "system" if fs["mount"] == "/" else \
            "boot" if fs["mount"].startswith("/boot") else "data"
        out.append({
            "mount": fs["mount"], "path": path, "device": fs["device"], "fstype": fs["fstype"],
            "label": labels.get(block, ""), "model": _read(f"{host}/sys/block/{disk}/device/model") if disk else "",
            "disk": disk, "transport": transport, "removable": removable, "external": external,
            "kind": kind, "total": total, "used": max(0, used), "free": free,
            "percent": round(100.0 * max(0, used) / total, 1) if total else 0.0,
            "browsable": within(os.path.realpath(path), roots()),
        })
    order = {"system": 0, "data": 1, "external": 2, "network": 3, "boot": 4}
    out.sort(key=lambda f: (order.get(f["kind"], 9), f["mount"]))
    return {"filesystems": out}


# ------------------------------------------------------------- sizes and search

_usage_cache: dict[str, tuple[float, dict]] = {}


async def usage(path: str, timeout: int = 45) -> dict:
    """How much each folder directly inside `path` holds (du, one filesystem),
    biggest first — the answer to "what is filling my disk"."""
    resolved = resolve(path)
    hit = _usage_cache.get(resolved)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    proc = await asyncio.create_subprocess_exec(
        "du", "-b", "--max-depth=1", "--one-file-system", resolved,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    partial = False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, _ = await proc.communicate()
        partial = True
    children, total = [], 0
    for line in out.decode("utf-8", "replace").splitlines():
        size, _, name = line.partition("\t")
        if not size.isdigit():
            continue
        if name.rstrip("/") == resolved.rstrip("/"):
            total = int(size)
            continue
        children.append({"name": os.path.basename(name), "path": name,
                         "display": display(name), "bytes": int(size)})
    children.sort(key=lambda c: -c["bytes"])
    result = {"path": resolved, "display": display(resolved), "total": total,
              "children": children[:60], "partial": partial or not total}
    if not partial:
        _usage_cache[resolved] = (time.time(), result)
    return result


def search(path: str, query: str, limit: int = 200, budget: float = 6.0) -> dict:
    """Files and folders under `path` whose name contains `query` — breadth
    first, within a time budget, never following links off the tree."""
    resolved = resolve(path)
    needle = query.strip().lower()
    if len(needle) < 2:
        raise ValueError("search for at least two characters")
    deadline = time.monotonic() + budget
    queue, hits, scanned = [resolved], [], 0
    complete = True
    while queue:
        if time.monotonic() > deadline or len(hits) >= limit:
            complete = False
            break
        current = queue.pop(0)
        try:
            with os.scandir(current) as it:
                for e in it:
                    scanned += 1
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if needle in e.name.lower():
                        try:
                            size = 0 if is_dir else e.stat(follow_symlinks=False).st_size
                        except OSError:
                            size = 0
                        hits.append({"name": e.name, "path": e.path, "display": display(e.path),
                                     "dir": is_dir, "size": size, "text": looks_text(e.name)})
                        if len(hits) >= limit:
                            break
                    if is_dir and e.name not in ("proc", "sys", "dev") \
                            and not e.name.startswith("overlay"):
                        queue.append(e.path)
        except OSError:
            continue
    return {"path": resolved, "display": display(resolved), "query": query, "hits": hits,
            "scanned": scanned, "complete": complete and len(hits) < limit}
