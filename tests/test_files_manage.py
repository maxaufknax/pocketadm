"""Managing files from the phone: saving keeps owner, mode and an undo; system
folders, the roots and PocketADM's credentials cannot be destroyed; archives
cannot write outside their folder."""
import io
import os
import stat
import tarfile
import zipfile

import pytest

from server import ai, config, files


@pytest.fixture
def tree(tmp_path, monkeypatch, clean_settings):
    root = tmp_path / "root"
    (root / "srv" / "app").mkdir(parents=True)
    (root / "srv" / "app" / "compose.yml").write_text("services: {}\n")
    (root / "etc").mkdir()
    (root / "proc").mkdir()
    (root / "home" / "max").mkdir(parents=True)
    data = root / "data"
    data.mkdir()
    for name in ("settings.json", "secret.key", "admin.pw"):
        (data / name).write_text("secret")
    monkeypatch.setattr(files, "HOST", str(root))
    monkeypatch.setattr(config, "get_workspaces", lambda: [str(root)])
    monkeypatch.setattr(ai, "DEFAULT_WORKDIR", str(root))
    monkeypatch.setattr(files, "BACKUP_DIR", tmp_path / "versions")
    return root


def test_start_opens_the_whole_server(tree):
    start = files.start()
    assert start["path"] == str(tree) and start["display"] == "/" and start["whole_server"]


def test_save_keeps_mode_and_an_undo(tree):
    f = tree / "srv" / "app" / "compose.yml"
    os.chmod(f, 0o640)
    before = f.stat().st_mtime
    out = files.write_text("/srv/app/compose.yml", "services:\n  web: {}\n", expected_modified=before)
    assert f.read_text() == "services:\n  web: {}\n"
    assert stat.S_IMODE(f.stat().st_mode) == 0o640
    assert out["display"] == "/srv/app/compose.yml" and out["version"]
    files.restore_version(out["version"])
    assert f.read_text() == "services: {}\n"


def test_save_refuses_a_file_that_changed_meanwhile(tree):
    with pytest.raises(files.Conflict):
        files.write_text("/srv/app/compose.yml", "x", expected_modified=1.0)


def test_create_new_file_and_folder(tree):
    out = files.write_text("/srv/app/.env", "A=1\n", create=True)
    assert (tree / "srv" / "app" / ".env").read_text() == "A=1\n" and out["size"] == 4
    with pytest.raises(FileExistsError):
        files.write_text("/srv/app/.env", "again", create=True)
    files.make_dir("/srv", "backups")
    assert (tree / "srv" / "backups").is_dir()
    for bad in ("", "..", "a/b"):
        with pytest.raises(ValueError):
            files.make_dir("/srv", bad)


def test_upload_streams_to_disk(tree):
    out = files.save_upload("/srv", "photo.jpg", [b"\xff\xd8", b"rest"])
    assert (tree / "srv" / "photo.jpg").read_bytes() == b"\xff\xd8rest" and out["size"] == 6
    with pytest.raises(FileExistsError):
        files.save_upload("/srv", "photo.jpg", [b"x"])
    files.save_upload("/srv", "photo.jpg", [b"x"], overwrite=True)
    assert (tree / "srv" / "photo.jpg").read_bytes() == b"x"
    assert not [p for p in (tree / "srv").iterdir() if ".upload-" in p.name]


def test_rename_move_copy_delete(tree):
    files.rename("/srv/app/compose.yml", "docker-compose.yml")
    assert (tree / "srv" / "app" / "docker-compose.yml").exists()
    files.make_dir("/srv", "old")
    files.move(["/srv/app/docker-compose.yml"], "/srv/old", copy=True)
    assert (tree / "srv" / "old" / "docker-compose.yml").exists()
    assert (tree / "srv" / "app" / "docker-compose.yml").exists()
    with pytest.raises(FileExistsError):
        files.move(["/srv/app/docker-compose.yml"], "/srv/old")
    with pytest.raises(ValueError):
        files.move(["/srv/app"], "/srv/app")                  # into itself
    files.delete(["/srv/old"])
    assert not (tree / "srv" / "old").exists()


def test_system_folders_roots_and_credentials_are_safe(tree):
    for path in ("/", "/etc", "/srv", "/home/max"):
        with pytest.raises(PermissionError):
            files.delete([path])
    with pytest.raises(PermissionError):
        files.rename("/etc", "etc2")
    with pytest.raises(PermissionError):
        files.write_text("/proc/x", "1", create=True)
    with pytest.raises(PermissionError):
        files.write_text("/data/secret.key", "stolen")
    with pytest.raises(PermissionError):
        files.delete(["/data/admin.pw"])
    # inside a system folder is fine
    files.write_text("/etc/app.conf", "a=1", create=True)
    files.delete(["/etc/app.conf"])


def test_chmod(tree):
    files.chmod("/srv/app/compose.yml", "600")
    assert stat.S_IMODE((tree / "srv" / "app" / "compose.yml").stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        files.chmod("/srv/app/compose.yml", "rwx")


def test_extract_zip_and_tar_but_not_outside(tree):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("site/index.html", "<h1>hi</h1>")
    (tree / "srv" / "site.zip").write_bytes(buf.getvalue())
    out = files.extract("/srv/site.zip")
    assert out["display"] == "/srv/site" and (tree / "srv" / "site" / "site" / "index.html").exists()

    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    (tree / "srv" / "evil.zip").write_bytes(evil.getvalue())
    with pytest.raises(ValueError):
        files.extract("/srv/evil.zip")
    assert not (tree / "escape.txt").exists() and not (tree / "srv" / "evil").exists()

    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w:gz") as tf:
        info = tarfile.TarInfo("notes.txt")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"ok"))
    (tree / "srv" / "n.tar.gz").write_bytes(tbuf.getvalue())
    out = files.extract("/srv/n.tar.gz")
    assert (tree / "srv" / "n" / "notes.txt").read_text() == "ok"


def test_folder_as_zip_leaves_credentials_out(tree):
    out = files.make_archive("/")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert any(n.endswith("srv/app/compose.yml") for n in names)
    assert not any(n.endswith(("secret.key", "admin.pw", "settings.json")) for n in names)


def test_listing_marks_kinds(tree):
    (tree / "srv" / "movie.mkv").write_bytes(b"0")
    (tree / "srv" / "doc.pdf").write_bytes(b"0")
    out = files.listing("/srv", files=True)
    kinds = {f["name"]: f["kind"] for f in out["file_entries"]}
    assert kinds == {"movie.mkv": "video", "doc.pdf": "pdf"}
