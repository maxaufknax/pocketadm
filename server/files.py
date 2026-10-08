"""The file browser: the server's folders, its drives and what fills them —
and managing them: edit, upload, create, rename, move, copy, delete.

The browser shows the host's filesystem through the /host mount, so paths
travel with the /host prefix on the wire and carry a `display` form ("/srv/x")
for people. Browsing is limited to the configured workspaces (by default the
whole host); PocketADM's own credential files stay unreadable here, exactly as
they are for the agent. Changes have their own guardrails (see "managing files").
"""
from __future__ import annotations

import asyncio
import grp
import json
import os
import pwd
import re
import secrets
import shutil
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


KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff",
              ".svg", ".ico", ".avif"},
    "video": {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts"},
    "audio": {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aiff"},
    "pdf": {".pdf"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".7z", ".rar", ".zst"},
    "document": {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
                 ".pages", ".numbers", ".key", ".rtf", ".epub"},
}


def file_kind(name: str) -> str:
    """What a file is, for its icon and how the app opens it."""
    ext = os.path.splitext(name)[1].lower()
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind
    return "text" if looks_text(name) else "file"


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
    mounts = mount_points()
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
                drive = mounts.get(info["display"])
                if drive:
                    info["drive"] = drive
                dirs.append(info)
        else:
            file_count += 1
            if files and len(entries) < LIST_CAP:
                entries.append({**info, "size": st.st_size, "text": looks_text(e.name),
                                "kind": file_kind(e.name)})
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
    st = os.stat(resolved)
    meta = {"path": resolved, "display": display(resolved), "size": size,
            "modified": st.st_mtime, "mode": stat.filemode(st.st_mode), "owner": _owner(st.st_uid),
            "writable": not ai.is_protected_path(resolved) and not any(
                display(resolved).startswith(p + "/") for p in ("/proc", "/sys", "/dev"))}
    if b"\x00" in raw[:4096]:
        return {**meta, "binary": True, "content": "", "truncated": truncated}
    return {**meta, "binary": False, "truncated": truncated, "content": raw.decode("utf-8", "replace")}


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


_mounts_cache: dict = {"t": 0.0, "value": {}}


def mount_points() -> dict[str, dict]:
    """Folders that are drives of their own ("/mnt/t5" -> an external disk),
    so the browser can mark them. Cached for a minute."""
    if time.time() - _mounts_cache["t"] < 60:
        return _mounts_cache["value"]
    value = {}
    try:
        for fs in storage()["filesystems"]:
            if fs["mount"] != "/":
                value[fs["mount"]] = {"kind": fs["kind"], "percent": fs["percent"],
                                      "label": fs["label"] or fs["model"], "free": fs["free"]}
    except Exception:
        pass
    _mounts_cache.update(t=time.time(), value=value)
    return value


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
                                     "dir": is_dir, "size": size, "text": looks_text(e.name),
                                     "kind": "folder" if is_dir else file_kind(e.name)})
                        if len(hits) >= limit:
                            break
                    if is_dir and e.name not in ("proc", "sys", "dev") \
                            and not e.name.startswith("overlay"):
                        queue.append(e.path)
        except OSError:
            continue
    return {"path": resolved, "display": display(resolved), "query": query, "hits": hits,
            "scanned": scanned, "complete": complete and len(hits) < limit}


# ------------------------------------------------------------- managing files
#
# Root-on-host either way (the agent can do all of this after a tap), so the
# browser may change files too — with guardrails that make a slip on a phone
# survivable: the system's top-level folders and the roots themselves cannot be
# deleted or moved, /proc, /sys and /dev are never written, PocketADM's own
# credentials stay untouchable, every overwrite keeps the previous version for
# an undo, and new files belong to whoever owns the folder they land in.

BACKUP_DIR = config.DATA_DIR / "file_versions"
BACKUP_KEEP = 60
BACKUP_MAX = 8 * 1024 * 1024          # bigger files are not versioned
EDIT_MAX = 4 * 1024 * 1024
SYSTEM_DIRS = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64",
               "/libx32", "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/snap",
               "/srv", "/sys", "/tmp", "/usr", "/var", "/var/lib", "/var/log", "/usr/local",
               "/var/lib/docker", "/etc/systemd", "/lost+found"}
NO_WRITE = ("/proc", "/sys", "/dev")


class Conflict(Exception):
    """The file changed on the server since it was opened."""


def _host_form(resolved: str) -> str:
    return display(resolved)


def _check_writable(resolved: str, removing: bool = False) -> None:
    shown = _host_form(resolved)
    if any(shown == p or shown.startswith(p + "/") for p in NO_WRITE):
        raise PermissionError(f"{shown} is part of the running system and cannot be changed here")
    if ai.is_protected_path(resolved):
        raise PermissionError("this file holds PocketADM's own credentials")
    if removing:
        if shown in SYSTEM_DIRS or os.path.realpath(resolved) in [os.path.realpath(r) for r in roots()]:
            raise PermissionError(f"{shown} is a system folder — it cannot be deleted or moved")
        if shown.startswith("/home/") and shown.count("/") == 2:
            raise PermissionError(f"{shown} is a user's home folder — it cannot be deleted or moved")


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\x00" in name or len(name) > 255:
        raise ValueError("not a valid name")
    return name


def _inherit_owner(path: str, parent: str) -> None:
    """A new file or folder belongs to whoever owns the folder it is in."""
    try:
        st = os.stat(parent)
        os.chown(path, st.st_uid, st.st_gid, follow_symlinks=False)
    except OSError:
        pass


def _keep_version(resolved: str) -> str:
    """A copy of the file as it was, for an undo. Returns its id ("" if none)."""
    try:
        if not os.path.isfile(resolved) or os.path.getsize(resolved) > BACKUP_MAX:
            return ""
        BACKUP_DIR.mkdir(exist_ok=True)
        os.chmod(BACKUP_DIR, 0o700)
        vid = f"{int(time.time())}-{secrets.token_hex(4)}"
        shutil.copy2(resolved, BACKUP_DIR / vid)
        (BACKUP_DIR / f"{vid}.json").write_text(json.dumps({"path": resolved, "t": time.time()}))
        versions = sorted(p for p in BACKUP_DIR.iterdir() if not p.name.endswith(".json"))
        for old in versions[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
            (BACKUP_DIR / f"{old.name}.json").unlink(missing_ok=True)
        return vid
    except OSError:
        return ""


def _stat_info(resolved: str) -> dict:
    st = os.stat(resolved)
    return {"path": resolved, "display": display(resolved), "size": st.st_size,
            "modified": st.st_mtime, "mode": stat.filemode(st.st_mode), "owner": _owner(st.st_uid)}


def write_text(path: str, content: str, expected_modified: float | None = None,
               create: bool = False) -> dict:
    """Save a text file in place. An existing file keeps its owner and mode,
    and the previous version is kept for an undo; `expected_modified` refuses
    to overwrite a file that changed on the server since it was opened."""
    data = content.encode("utf-8")
    if len(data) > EDIT_MAX:
        raise ValueError("too large to edit here (4 MB at most)")
    if create:
        parent = resolve(os.path.dirname(path.rstrip("/")) or "/")
        resolved = os.path.join(parent, _safe_name(os.path.basename(path.rstrip("/"))))
        if os.path.lexists(resolved):
            raise FileExistsError("a file with this name already exists")
    else:
        resolved = resolve(path)
        if not os.path.isfile(resolved):
            raise FileNotFoundError("not a file")
        parent = os.path.dirname(resolved)
    _check_writable(resolved)
    existing = os.path.isfile(resolved)
    version = ""
    if existing:
        st = os.stat(resolved)
        if expected_modified and abs(st.st_mtime - expected_modified) > 0.001:
            raise Conflict("the file changed on the server since you opened it")
        version = _keep_version(resolved)
    tmp = os.path.join(parent, f".{os.path.basename(resolved)}.pocketadm-{secrets.token_hex(4)}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if existing:
            os.chmod(tmp, stat.S_IMODE(st.st_mode))
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass
        else:
            os.chmod(tmp, 0o644)
            _inherit_owner(tmp, parent)
        os.replace(tmp, resolved)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if e.errno == 30:
            raise PermissionError("the server's files are mounted read-only for PocketADM")
        raise
    return {**_stat_info(resolved), "version": version}


def restore_version(version: str) -> dict:
    """Put back the file as it was before a save (the undo after an edit)."""
    if not version or "/" in version or version.startswith("."):
        raise ValueError("bad version")
    meta_file = BACKUP_DIR / f"{version}.json"
    try:
        meta = json.loads(meta_file.read_text())
    except (OSError, ValueError):
        raise FileNotFoundError("that version is gone")
    target = meta["path"]
    _check_writable(target)
    if not within(os.path.realpath(os.path.dirname(target)), roots()):
        raise PermissionError("outside the folders PocketADM may change")
    st = os.stat(target) if os.path.exists(target) else None
    shutil.copyfile(BACKUP_DIR / version, target)
    if st:
        os.chmod(target, stat.S_IMODE(st.st_mode))
        try:
            os.chown(target, st.st_uid, st.st_gid)
        except OSError:
            pass
    return _stat_info(target)


def make_dir(parent: str, name: str) -> dict:
    base = resolve(parent)
    if not os.path.isdir(base):
        raise FileNotFoundError("not a folder")
    target = os.path.join(base, _safe_name(name))
    _check_writable(target)
    os.mkdir(target, 0o755)
    _inherit_owner(target, base)
    return {"path": target, "display": display(target)}


def save_upload(parent: str, name: str, chunks, overwrite: bool = False) -> dict:
    """Store an uploaded file (an iterable of byte chunks) in a folder."""
    base = resolve(parent)
    if not os.path.isdir(base):
        raise FileNotFoundError("not a folder")
    target = os.path.join(base, _safe_name(name))
    _check_writable(target)
    if os.path.lexists(target) and not overwrite:
        raise FileExistsError("a file with this name already exists")
    tmp = os.path.join(base, f".{os.path.basename(target)}.upload-{secrets.token_hex(4)}")
    size = 0
    try:
        with open(tmp, "wb") as fh:
            for chunk in chunks:
                fh.write(chunk)
                size += len(chunk)
        os.chmod(tmp, 0o644)
        _inherit_owner(tmp, base)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {**_stat_info(target)}


def rename(path: str, new_name: str) -> dict:
    src = resolve(path)
    _check_writable(src, removing=True)
    dst = os.path.join(os.path.dirname(src), _safe_name(new_name))
    _check_writable(dst)
    if os.path.lexists(dst):
        raise FileExistsError("something with this name already exists")
    os.rename(src, dst)
    return {"path": dst, "display": display(dst)}


def move(paths: list[str], dest: str, copy: bool = False, overwrite: bool = False) -> dict:
    """Move or copy files and folders into another folder."""
    target_dir = resolve(dest)
    if not os.path.isdir(target_dir):
        raise FileNotFoundError("the destination is not a folder")
    done = []
    for path in paths:
        src = resolve(path)
        _check_writable(src, removing=not copy)
        dst = os.path.join(target_dir, os.path.basename(src))
        _check_writable(dst)
        if os.path.realpath(dst) == os.path.realpath(src):
            raise ValueError("it is already there")
        if os.path.isdir(src) and (os.path.realpath(target_dir) + os.sep).startswith(
                os.path.realpath(src) + os.sep):
            raise ValueError("a folder cannot go inside itself")
        if os.path.lexists(dst):
            if not overwrite:
                raise FileExistsError(f"{os.path.basename(src)} already exists there")
            _check_writable(dst, removing=True)
            if os.path.isdir(dst) and not os.path.islink(dst):
                shutil.rmtree(dst)
            else:
                os.unlink(dst)
        if copy:
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=True)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
        else:
            shutil.move(src, dst)
        done.append(display(dst))
    return {"done": done}


def delete(paths: list[str]) -> dict:
    removed = []
    for path in paths:
        target = resolve(path)
        _check_writable(target, removing=True)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.unlink(target)
        removed.append(display(target))
    return {"removed": removed}


def chmod(path: str, mode: str, recursive: bool = False) -> dict:
    target = resolve(path)
    _check_writable(target)
    if not re.fullmatch(r"[0-7]{3,4}", mode or ""):
        raise ValueError("give the mode as three octal digits, e.g. 644")
    value = int(mode, 8)
    os.chmod(target, value)
    if recursive and os.path.isdir(target):
        for dirpath, dirnames, filenames in os.walk(target):
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                if not os.path.islink(full):
                    os.chmod(full, value)
    return _stat_info(target)


ARCHIVE_MAX = 4 * 1024 ** 3


def make_archive(path: str) -> str:
    """A folder as one zip file (in a temp dir), to share or save on the phone."""
    import tempfile
    import zipfile

    src = resolve(path)
    if not os.path.isdir(src):
        raise FileNotFoundError("not a folder")
    if ai.is_protected_path(src):
        raise PermissionError("this holds PocketADM's own credentials")
    out = os.path.join(tempfile.mkdtemp(prefix="pocketadm-zip-"),
                       (os.path.basename(src) or "server") + ".zip")
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            for name in filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full) or ai.is_protected_path(full) or not os.path.isfile(full):
                    continue
                try:
                    total += os.path.getsize(full)
                    if total > ARCHIVE_MAX:
                        raise ValueError("the folder is too large to download at once (4 GB)")
                    zf.write(full, os.path.relpath(full, os.path.dirname(src)))
                except OSError:
                    continue
    return out


def extract(path: str) -> dict:
    """Unpack a zip or tar archive next to itself, into a folder of its name."""
    import tarfile
    import zipfile
    src = resolve(path)
    if not os.path.isfile(src):
        raise FileNotFoundError("not a file")
    base = os.path.dirname(src)
    name = os.path.basename(src)
    for suffix in (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar", ".zip"):
        if name.lower().endswith(suffix):
            stem = name[:-len(suffix)] or "archive"
            break
    else:
        raise ValueError("only zip and tar archives can be unpacked here")
    target = os.path.join(base, stem)
    n = 1
    while os.path.lexists(target):
        n += 1
        target = os.path.join(base, f"{stem} {n}")
    _check_writable(target)
    os.mkdir(target, 0o755)
    try:
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(src) as zf:
                for member in zf.namelist():
                    dest = os.path.realpath(os.path.join(target, member))
                    if not dest.startswith(os.path.realpath(target) + os.sep):
                        raise ValueError("the archive points outside its folder")
                zf.extractall(target)
        else:
            with tarfile.open(src) as tf:
                tf.extractall(target, filter="data")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    for dirpath, dirnames, filenames in os.walk(target):
        for entry in [dirpath] + [os.path.join(dirpath, f) for f in filenames]:
            _inherit_owner(entry, base)
    return {"path": target, "display": display(target)}


def start() -> dict:
    """Where the browser opens: the whole server when PocketADM may see it
    (like an editor opened on /), otherwise the folders it may show."""
    allowed = roots()
    whole = HOST or "/"
    if any(os.path.realpath(r) == os.path.realpath(whole) for r in allowed):
        return {"path": whole, "display": "/", "roots": allowed, "whole_server": True}
    if len(allowed) == 1:
        return {"path": allowed[0], "display": display(allowed[0]), "roots": allowed,
                "whole_server": False}
    return {"path": "", "display": "", "roots": allowed, "whole_server": False}
