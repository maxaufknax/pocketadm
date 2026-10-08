"""Files people attach in the assistant chat — a screenshot of an error, a
config from their laptop, a log someone sent them.

They are stored on the server, where every assistant can open them: the
built-in one works on the host (/var/lib/pocketadm/uploads/…), the coding
agents see the host at /host. Pictures also go to the model itself when it
can see images (ai.py); text files are quoted into the message. Uploads are
kept for 30 days.
"""
from __future__ import annotations

import mimetypes
import os
import re
import secrets
import shutil
import time

from . import config

MAX_BYTES = 25 * 1024 * 1024
IMAGE_MAX_BYTES = 8 * 1024 * 1024
KEEP_DAYS = 30
TEXT_PREVIEW = 64 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

HOST = "/host"
HOST_DIR = "/var/lib/pocketadm/uploads"


def roots() -> tuple[str, str]:
    """(the folder as written from this container, the same folder as the
    assistant names it). On a server PocketADM sees whole, that is a host
    folder; otherwise PocketADM's own data folder."""
    if os.path.isdir(HOST + "/var/lib") and os.access(HOST + "/var/lib", os.W_OK):
        return HOST + HOST_DIR, HOST_DIR
    own = str(config.DATA_DIR / "uploads")
    return own, own


def safe_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()+@,]", "_", name).strip(" .")[:120]
    return name or "file"


def looks_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:TEXT_PREVIEW].decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        # a multi-byte character cut at the preview's end is still text
        return e.start >= min(len(data), TEXT_PREVIEW) - 4


def save(name: str, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError(f"Files can be up to {MAX_BYTES // (1024 * 1024)} MB.")
    write_root, shown_root = roots()
    day = time.strftime("%Y-%m-%d")
    folder = os.path.join(write_root, day)
    os.makedirs(folder, mode=0o755, exist_ok=True)
    name = safe_name(name)
    if os.path.exists(os.path.join(folder, name)):
        stem, ext = os.path.splitext(name)
        name = f"{stem}-{secrets.token_hex(3)}{ext}"
    target = os.path.join(folder, name)
    with open(target, "wb") as fh:
        fh.write(data)
    os.chmod(target, 0o644)
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if media in IMAGE_TYPES:
        kind = "image"
    elif looks_text(data):
        kind = "text"
    else:
        kind = "file"
    _prune(write_root)
    return {"name": name, "path": os.path.join(shown_root, day, name), "size": len(data),
            "media_type": media, "kind": kind,
            "text": data[:TEXT_PREVIEW].decode("utf-8", "replace") if kind == "text" else "",
            "truncated": kind == "text" and len(data) > TEXT_PREVIEW}


def local_path(path: str) -> str:
    """Where an upload the assistant names lives inside this container."""
    write_root, shown_root = roots()
    real = os.path.normpath(path or "")
    if real == shown_root or real.startswith(shown_root.rstrip("/") + "/"):
        return write_root + real[len(shown_root):]
    return ""


def load_image(path: str) -> tuple[str, bytes] | None:
    """(media type, bytes) of an uploaded picture — only from the uploads
    folder, whatever path a message names."""
    local = local_path(path)
    media = mimetypes.guess_type(local)[0] or ""
    if not local or media not in IMAGE_TYPES:
        return None
    write_root, _ = roots()
    if not os.path.realpath(local).startswith(os.path.realpath(write_root) + os.sep):
        return None
    try:
        if os.path.getsize(local) > IMAGE_MAX_BYTES:
            return None
        with open(local, "rb") as fh:
            return media, fh.read()
    except OSError:
        return None


def agent_path(path: str) -> str:
    """The path as a coding agent in this container opens it (/host/…)."""
    write_root, shown_root = roots()
    if write_root.startswith(HOST + "/") and path.startswith(shown_root):
        return HOST + path
    return path


def _prune(write_root: str) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for day in os.listdir(write_root):
            folder = os.path.join(write_root, day)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and os.path.isdir(folder) \
                    and os.path.getmtime(folder) < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass
