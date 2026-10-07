"""The file browser: host paths, hidden files, drives, sizes, search, and the
files it must never hand out."""
import asyncio
import os

import pytest

from server import ai, config, files

MOUNTINFO = """22 1 252:0 / / rw,relatime shared:1 - ext4 /dev/mapper/ubuntu--vg-ubuntu--lv rw
25 22 259:2 / /boot rw,relatime shared:3 - ext4 /dev/nvme0n1p2 rw
26 25 259:1 / /boot/efi rw,relatime shared:4 - vfat /dev/nvme0n1p1 rw
120 22 8:1 / /mnt/cloudserver-ssd rw,relatime shared:60 - exfat /dev/sda1 rw
121 22 252:0 /srv/data /srv/mirror rw,relatime shared:1 - ext4 /dev/mapper/ubuntu--vg-ubuntu--lv rw
130 22 7:3 / /snap/firefox/8995 ro,nodev shared:70 - squashfs /dev/loop3 ro
131 22 0:58 / /var/lib/docker/rootfs/overlayfs/abc rw - overlay overlay rw
140 22 0:60 / /mnt/nas rw - nfs4 nas:/export rw
150 22 0:61 / /run/user/1000 rw - tmpfs tmpfs rw
151 22 8:17 / /run/media/max/STICK rw - vfat /dev/sdb1 rw
"""


def test_mountinfo_keeps_real_filesystems_once():
    mounts = {m["mount"]: m for m in files.parse_mountinfo(MOUNTINFO)}
    assert set(mounts) == {"/", "/boot", "/boot/efi", "/mnt/cloudserver-ssd", "/mnt/nas",
                           "/run/media/max/STICK"}
    # a bind mount of a folder on the root disk is not a second drive
    assert mounts["/"]["device"] == "/dev/mapper/ubuntu--vg-ubuntu--lv"
    assert mounts["/mnt/cloudserver-ssd"]["fstype"] == "exfat"


def test_display_paths(monkeypatch):
    monkeypatch.setattr(files, "HOST", "/host")
    assert files.display("/host") == "/"
    assert files.display("/host/srv/x") == "/srv/x"
    assert files.display("/data/home") == "/data/home"


@pytest.fixture
def tree(tmp_path, monkeypatch, clean_settings):
    root = tmp_path / "root"
    (root / "srv" / "app").mkdir(parents=True)
    (root / "srv" / "app" / "compose.yml").write_text("services: {}\n")
    (root / "srv" / ".hidden").write_text("x")
    (root / "srv" / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    (root / "data").mkdir()
    for name in ("settings.json", "secret.key", "admin.pw"):
        (root / "data" / name).write_text("secret")
    monkeypatch.setattr(files, "HOST", "")
    monkeypatch.setattr(config, "get_workspaces", lambda: [str(root)])
    monkeypatch.setattr(ai, "DEFAULT_WORKDIR", str(root))
    return root


def test_listing_hides_dotfiles_unless_asked(tree):
    out = files.listing(str(tree / "srv"), files=True)
    assert [d["name"] for d in out["dirs"]] == ["app"]
    assert [f["name"] for f in out["file_entries"]] == ["photo.jpg"]
    assert out["hidden"] == 1
    out = files.listing(str(tree / "srv"), files=True, hidden=True)
    assert {f["name"] for f in out["file_entries"]} == {".hidden", "photo.jpg"}
    photo = next(f for f in out["file_entries"] if f["name"] == "photo.jpg")
    assert photo["size"] == 103 and not photo["text"] and photo["mode"].startswith("-")
    assert photo["modified"] > 0 and photo["owner"]


def test_outside_the_roots_is_refused(tree, tmp_path):
    with pytest.raises(PermissionError):
        files.listing(str(tmp_path))
    with pytest.raises(PermissionError):
        files.listing(str(tree / "srv" / ".." / ".."))


def test_own_credentials_are_never_served(tree):
    # a directory holding secret.key + admin.pw next to settings.json is a
    # PocketADM data dir seen from the host side
    with pytest.raises(PermissionError):
        files.read_text(str(tree / "data" / "settings.json"))
    with pytest.raises(PermissionError):
        files.raw_path(str(tree / "data" / "secret.key"))
    assert files.raw_path(str(tree / "srv" / "photo.jpg")).endswith("photo.jpg")
    assert files.read_text(str(tree / "srv" / "app" / "compose.yml"))["content"] == "services: {}\n"


def test_search_and_usage(tree):
    hits = files.search(str(tree), "compose")["hits"]
    assert [h["name"] for h in hits] == ["compose.yml"]
    with pytest.raises(ValueError):
        files.search(str(tree), "c")
    usage = asyncio.run(files.usage(str(tree)))
    names = [c["name"] for c in usage["children"]]
    assert set(names) == {"srv", "data"} and usage["total"] > 0
    assert usage["children"][0]["bytes"] >= usage["children"][-1]["bytes"]


def test_vibe_login_is_protected(tmp_path):
    vibe = tmp_path / ".vibe"
    vibe.mkdir()
    (vibe / ".env").write_text("MISTRAL_API_KEY=x")
    assert ai.is_protected_path(vibe / ".env")
    assert not ai.is_protected_path(tmp_path / ".env")
