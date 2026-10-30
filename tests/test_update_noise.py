"""Why the update list stopped crying wolf: a rebuilt image with the same
version waits until the installed one is a month old and never wears the
security badge, two images of the same software are told apart, an image
built on this server is not a failed registry check, Ubuntu's release number
is not the app's version, and the package updates are the host's — not those
of the container PocketADM runs in."""
import asyncio
import datetime

from server import config, dockerapi, hostrun, reports, updates


def test_ubuntus_release_label_is_not_the_apps_version():
    inherited = {"org.opencontainers.image.version": "24.04"}
    assert updates.image_version("ollama/ollama", inherited, []) == ""
    assert updates.image_version("ubuntu:24.04", inherited, []) == "24.04"
    assert updates.image_version("x/app", {"org.opencontainers.image.version": "2024.04.1"}, []) \
        == "2024.04.1"


def test_a_young_rebuild_waits_and_never_says_security():
    young = {"update_available": True, "security": True, "age_days": 12}
    updates._hold_rebuild(young, running_behind=False)
    assert young == {"update_available": False, "security": False, "age_days": 12,
                     "rebuild": True, "rebuild_pending": True}
    old = {"update_available": True, "security": True, "age_days": 45}
    updates._hold_rebuild(old, running_behind=False)
    assert old["update_available"] and not old["security"] and "rebuild_pending" not in old
    # pulled already, only the recreate is missing: that is a to-do right away
    pulled = {"update_available": True, "security": True, "age_days": 3}
    updates._hold_rebuild(pulled, running_behind=True)
    assert pulled["update_available"]


def test_the_same_software_twice_is_named_by_its_containers():
    results = [{"label": "Redis", "used_by": ["nextcloud-redis", "synapse-redis"]},
               {"label": "Redis", "used_by": ["authentik-redis"]},
               {"label": "Nginx", "used_by": ["helmsman-landing"]}]
    updates._distinct_labels(results)
    assert [r["label"] for r in results] == ["Redis (nextcloud-redis +1)",
                                             "Redis (authentik-redis)", "Nginx"]


def test_waiting_since_survives_new_builds_and_ends_with_the_update(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "SEEN_FILE", tmp_path / "seen.json")

    def since(now, available=True, error=""):
        rows = [{"image": "postgres:16-alpine", "update_available": available, "error": error}]
        updates._note_pending_since(rows, now)
        return rows[0].get("pending_since")

    assert since(1000.0) == 1000.0
    assert since(5000.0) == 1000.0                        # another rebuild, same wait
    since(6000.0, available=False, error="registry check failed")
    assert since(7000.0) == 1000.0                        # a failed check is not an update
    since(8000.0, available=False)                        # applied
    assert since(9000.0) == 9000.0


def test_local_builds_and_young_rebuilds_are_no_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "SEEN_FILE", tmp_path / "seen.json")
    created = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")

    async def containers(all_=False):
        return [{"name": "hub-landing", "image": "docker-hub-landing", "id": "a", "ports": []},
                {"name": "authentik-postgres", "image": "postgres:16-alpine", "id": "b",
                 "ports": []}]

    async def inspect_image(image):
        name = image.split(":")[0]
        # Docker's containerd store: a local build has a "digest" as well
        return {"Id": f"sha256:{name}", "Created": created,
                "RepoDigests": [f"{name}@sha256:local"],
                "Config": {"Labels": {}, "Env": ["PG_VERSION=16.15"] if name == "postgres" else []}}

    async def digest(client, registry, repo, tag):
        return "" if repo.endswith("docker-hub-landing") else "sha256:newer"

    async def remote_info(client, image):
        return {"version": "16.15", "created": created}

    monkeypatch.setattr(dockerapi, "list_containers", containers)
    monkeypatch.setattr(dockerapi, "inspect_image", inspect_image)
    monkeypatch.setattr(updates, "remote_digest", digest)
    monkeypatch.setattr(updates, "remote_image_info", remote_info)
    monkeypatch.setattr(config, "get_ignored_images", lambda: [])
    try:
        found = {u["image"]: u for u in asyncio.run(updates.check_docker_updates(force=True))}
    finally:
        updates._cache.update(time=0, result=None)
    local = found["docker-hub-landing"]
    assert local["local_build"] and local["error"] == "" and not local["update_available"]
    pg = found["postgres:16-alpine"]
    assert pg["rebuild"] and pg["rebuild_pending"] and not pg["update_available"]
    assert pg["priority"] == "low" and pg["security"] is False


def test_host_packages_and_which_of_them_are_security_updates():
    text = ("Listing...\n"
            "docker-compose-plugin/noble 5.6.1-1~ubuntu.24.04~noble amd64 "
            "[upgradable from: 5.6.0-1~ubuntu.24.04~noble]\n"
            "libnss3/noble-updates,noble-security 2:3.98-1ubuntu0.3 amd64 "
            "[upgradable from: 2:3.98-1ubuntu0.2]\n")
    assert updates.parse_apt_list(text) == [
        {"package": "docker-compose-plugin", "new": "5.6.1-1~ubuntu.24.04~noble",
         "current": "5.6.0-1~ubuntu.24.04~noble", "security": False},
        {"package": "libnss3", "new": "2:3.98-1ubuntu0.3", "current": "2:3.98-1ubuntu0.2",
         "security": True}]


def _fake_host(root):
    (root / "etc/apt/apt.conf.d").mkdir(parents=True)
    (root / "etc/apt/apt.conf.d/20auto-upgrades").write_text(
        'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n')
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/unattended-upgrade").write_text("")
    (root / "var/log/unattended-upgrades").mkdir(parents=True)
    (root / "var/log/unattended-upgrades/unattended-upgrades.log").write_text("All upgrades installed")
    (root / "run").mkdir()
    (root / "run/reboot-required").write_text("")
    (root / "run/reboot-required.pkgs").write_text("linux-image-6.8.0-148-generic\nlibc6\n")


def test_packages_are_read_on_the_host_once_per_half_hour(tmp_path, monkeypatch):
    _fake_host(tmp_path)
    monkeypatch.setattr(hostrun, "HOST", str(tmp_path))
    monkeypatch.setattr(hostrun, "available", lambda: True)
    ran = []

    async def on_host(command, timeout=60, cwd=None):
        ran.append(command)
        return 0, "Listing...\nlibnss3/noble-security 2 amd64 [upgradable from: 1]\n"
    monkeypatch.setattr(hostrun, "run", on_host)
    updates._apt_cache.update(time=0, result=None)
    try:
        apt = asyncio.run(updates.check_apt_updates())
        assert ran == ["apt list --upgradable 2>/dev/null"]
        assert apt["available"] and apt["security"] == 1 and apt["auto_security"]
        assert apt["last_auto_run"] and apt["reboot_required"]
        assert apt["reboot_packages"] == ["libc6", "linux-image-6.8.0-148-generic"]
        asyncio.run(updates.check_apt_updates())          # cached: no second helper container
        assert len(ran) == 1
    finally:
        updates._apt_cache.update(time=0, result=None)


def test_the_health_check_sees_a_pending_reboot_and_routine_packages(tmp_path, monkeypatch):
    _fake_host(tmp_path)
    monkeypatch.setattr(reports, "HOST", str(tmp_path))

    async def no_images(force=False):
        return []

    async def packages(force=False):
        return {"available": True, "security": 1, "auto_security": True,
                "packages": [{"package": "libnss3", "new": "2", "current": "1", "security": True}]}
    monkeypatch.setattr(updates, "check_docker_updates", no_images)
    monkeypatch.setattr(updates, "check_apt_updates", packages)
    checks = {c["id"]: c for c in asyncio.run(reports.check_updates_pending())}
    # unattended upgrades install it tomorrow morning: worth knowing, not a warning
    assert checks["apt"]["status"] == "info" and "automatically" in checks["apt"]["summary"]
    # /var/run on the host is a link to /run that the /host mount cannot follow
    assert "libc6" in checks["reboot"]["summary"]
