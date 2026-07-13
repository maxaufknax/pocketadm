"""Backup & restore of PocketADM's own state (everything under DATA_DIR):
settings + keys, agent memory, chats, installed-app compose files, audit log,
snapshots metadata, reports. NOT the apps' data volumes — those need a real
backup tool (restic/borg), which the Checks tab nags about.
"""
import io
import tarfile
import time

from . import config

# regenerable caches that would only bloat the archive
_EXCLUDE_FILES = {"catalog-remote.json"}
_EXCLUDE_DIRS = {"tmp", "home"}          # /data/home = CLI tool installs (large)
_MAX_RESTORE_BYTES = 200 * 1024 * 1024   # decompressed safety cap


def export_archive() -> tuple[str, bytes]:
    """Everything worth keeping, as an in-memory tar.gz. Contains secret.key
    and API keys — the download is as sensitive as the server itself."""
    buf = io.BytesIO()
    root = config.DATA_DIR
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if rel.parts[0] in _EXCLUDE_DIRS or rel.name in _EXCLUDE_FILES:
                continue
            if path.is_file() and not path.is_symlink():
                tar.add(path, arcname=str(rel))
    name = time.strftime("pocketadm-backup-%Y%m%d-%H%M%S.tar.gz")
    return name, buf.getvalue()


def restore_archive(data: bytes) -> list[str]:
    """Extract a backup over DATA_DIR (existing files are overwritten).
    Member paths are strictly validated; nothing can escape DATA_DIR."""
    restored: list[str] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        markers = {"settings.json", "secret.key", "admin.pw"}
        if not any(m.name.lstrip("./") in markers for m in members):
            raise ValueError("This does not look like a PocketADM backup.")
        for m in members:
            if not m.isfile():
                continue
            name = m.name.lstrip("./")
            parts = name.split("/")
            if not name or name.startswith("/") or ".." in parts:
                raise ValueError(f"Unsafe path in archive: {m.name}")
            total += m.size
            if total > _MAX_RESTORE_BYTES:
                raise ValueError("Archive too large to restore.")
            target = config.DATA_DIR / name
            target.parent.mkdir(parents=True, exist_ok=True)
            f = tar.extractfile(m)
            if f is None:
                continue
            target.write_bytes(f.read())
            restored.append(name)
    # the restored settings.json must become live immediately
    config.settings.clear()
    config.settings.update(config._load_settings())
    return restored
