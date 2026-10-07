"""What the update sheet tells you before you pull: versions, the releases in
between, where the source lives, and what the restart does to people."""
import asyncio

import pytest

from server import config, dockerapi, updates


def test_version_comparison():
    assert updates._version_tuple("v2.13.1") == (2, 13, 1)
    assert updates._version_tuple("release-1.2") == (1, 2)
    assert updates._version_tuple("latest") == ()
    assert updates.is_newer("v2.13.1", "2.12.9") is True
    assert updates.is_newer("2.12", "2.12.0") is False
    assert updates.is_newer("latest", "2.0") is None
    assert updates.major_jump("1.9.2", "2.0.0") is True
    assert updates.major_jump("2025.10.1", "2025.12.0") is False


def test_pinned_tags_only_follow_their_own_line():
    assert updates.reachable_release("7.4.6", "7-alpine")
    assert not updates.reachable_release("8.2.1", "7-alpine")
    assert updates.reachable_release("version-2025.10.3", "2025.10")
    assert not updates.reachable_release("version-2025.12.0", "2025.10")
    assert updates.reachable_release("v9.9.9", "latest")


@pytest.mark.parametrize("image,source,repo", [
    ("jc21/nginx-proxy-manager:latest", "", "NginxProxyManager/nginx-proxy-manager"),
    ("nextcloud:latest", "", "nextcloud/server"),
    ("ghcr.io/goauthentik/server:2025.10", "", "goauthentik/authentik"),
    ("gcr.io/cadvisor/cadvisor:latest", "", "google/cadvisor"),
    ("ghcr.io/foo/bar:1", "", "foo/bar"),
    ("lscr.io/linuxserver/sonarr:latest", "", "linuxserver/docker-sonarr"),
    ("someone/thing:1", "https://github.com/real/home.git", "real/home"),
    ("postgres:16-alpine", "", ""),
])
def test_repo_mapping(image, source, repo):
    assert updates._github_repo_for(image, source) == repo


def test_release_details_marks_what_is_coming(monkeypatch):
    monkeypatch.setattr(config, "DEMO", True)
    monkeypatch.setattr(dockerapi, "demo", lambda: True)

    async def fake_inspect(name):
        return {"Created": "2026-06-03T04:32:00.123Z", "RepoDigests": [name + "@sha256:" + "a" * 64],
                "Config": {"Labels": {"org.opencontainers.image.version": "2.12.3"}}}

    async def fake_remote(client, image):
        return {"version": "2.13.1", "created": "2026-09-28T10:00:00", "digest": "sha256:b",
                "_t": 0}

    async def fake_releases(repo, limit=12):
        return [{"tag": "v2.14.0-beta", "name": "", "date": "2026-10-01", "prerelease": True,
                 "notes": "beta", "url": ""},
                {"tag": "v2.13.1", "name": "2.13.1", "date": "2026-09-28", "prerelease": False,
                 "notes": "Fixes certificate renewal.", "url": ""},
                {"tag": "v2.13.0", "name": "2.13.0", "date": "2026-09-01", "prerelease": False,
                 "notes": "New access lists.", "url": ""},
                {"tag": "v2.12.3", "name": "2.12.3", "date": "2026-06-01", "prerelease": False,
                 "notes": "old", "url": ""}]

    monkeypatch.setattr(dockerapi, "inspect_image", fake_inspect)
    monkeypatch.setattr(updates, "remote_image_info", fake_remote)
    monkeypatch.setattr(updates, "github_releases", fake_releases)
    d = asyncio.run(updates.release_details("jc21/nginx-proxy-manager:latest"))
    assert d["label"] == "Nginx Proxy Manager"
    assert "reverse proxy" in d["description"].lower()
    assert "unreachable" in d["impact"]
    assert d["local"]["version"] == "2.12.3" and d["remote"]["version"] == "2.13.1"
    coming = [r["tag"] for r in d["releases"] if r["newer"]]
    assert coming == ["v2.13.1", "v2.13.0"]      # not the beta beyond it, not the old one
    assert d["newer_count"] == 2 and d["major"] is False
    assert d["links"]["changelog"].endswith("/NginxProxyManager/nginx-proxy-manager/releases")
