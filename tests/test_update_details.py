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


# ------------------------------------------------------------------ 0.25: precise versions

def test_version_from_official_image_env():
    env = ["PATH=/usr/bin", "REDIS_VERSION=8.8.1", "GOSU_VERSION=1.17"]
    assert updates.image_version("redis:alpine", {}, env) == "8.8.1"
    assert updates.image_version("nginx:alpine", {}, ["NGINX_VERSION=1.31.6", "NJS_VERSION=0.9.1"]) == "1.31.6"
    assert updates.image_version("postgres:16", {}, ["PG_MAJOR=16", "PG_VERSION=16.10-1.pgdg"]) == "16.10-1.pgdg"
    # a runtime's version is not the app's
    assert updates.image_version("someone/webapp:latest", {}, ["NODE_VERSION=22.1.0"]) == ""
    # a label wins, a branch name in the label does not
    assert updates.image_version("x/y:1", {"org.opencontainers.image.version": "2.3.4"}, []) == "2.3.4"
    assert updates.image_version("x/y:1", {"org.opencontainers.image.version": "master-node"},
                                 ["Y_VERSION=1.2.0"]) == "1.2.0"


def _rel(tag, date, pre=False):
    return {"tag": tag, "name": tag, "date": date, "prerelease": pre, "notes": "n", "url": ""}


def test_coming_releases_by_version_skips_release_candidates():
    rels = [_rel("v1.163.0rc1", "2026-10-06"), _rel("v1.162.0", "2026-09-29"),
            _rel("v1.162.0rc1", "2026-09-23"), _rel("v1.161.0", "2026-09-15"),
            _rel("v1.160.0", "2026-09-01")]
    marks = updates.coming_releases(rels, "1.160.0", "1.162.0", "latest")
    assert [r["tag"] for r, m in zip(rels, marks) if m] == ["v1.162.0", "v1.161.0"]


def test_coming_releases_by_date_when_the_image_has_no_version():
    rels = [_rel("v3.15.0", "2026-09-24"), _rel("v3.14.0", "2026-08-20"),
            _rel("v3.13.0", "2026-07-01"), _rel("v3.12.0", "2026-05-30"),
            _rel("v3.16.0", "2026-10-01")]
    marks = updates.coming_releases(rels, "", "", "latest", "2026-06-03T04:32:00Z",
                                    "2026-09-25T08:06:23")
    assert [r["tag"] for r, m in zip(rels, marks) if m] == ["v3.15.0", "v3.14.0", "v3.13.0"]
    # nothing to compare against at all: claim nothing
    assert not any(updates.coming_releases(rels, "", "", "latest"))


def test_a_rebuild_of_the_same_version_brings_no_releases(monkeypatch):
    monkeypatch.setattr(config, "DEMO", True)
    monkeypatch.setattr(dockerapi, "demo", lambda: True)

    async def fake_inspect(name):
        return {"Created": "2026-09-16T03:26:24Z", "RepoDigests": [name + "@sha256:" + "a" * 64],
                "Config": {"Labels": {}, "Env": ["MARIADB_VERSION=1:11.8.9+maria~ubu2404"]}}

    async def fake_remote(client, image):
        return {"version": "11.8.9", "created": "2026-10-02T01:31:10", "digest": "sha256:c", "_t": 0}

    async def fake_releases(repo, limit=12):
        return [_rel("mariadb-11.8.9", "2026-08-01")]

    monkeypatch.setattr(dockerapi, "inspect_image", fake_inspect)
    monkeypatch.setattr(updates, "remote_image_info", fake_remote)
    monkeypatch.setattr(updates, "github_releases", fake_releases)
    monkeypatch.setattr(updates, "_github_repo_for", lambda image, source="": "MariaDB/server")
    # MARIADB_VERSION carries the packaging epoch: not a clean version, so the
    # label path decides — here: no label, so the version stays unknown locally
    d = asyncio.run(updates.release_details("mariadb:11"))
    assert d["remote"]["version"] == "11.8.9"


def test_rebuild_is_detected_and_explained_without_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "EXPLAIN_FILE", tmp_path / "x.json")

    async def fake_details(image):
        return {"label": "nginx", "local": {"version": "1.31.6", "tag": "alpine", "created": "2026-09-17"},
                "remote": {"version": "1.31.6", "created": "2026-09-22", "digest": "sha256:d"},
                "releases": [], "rebuild": True, "major": False}

    async def no_ai(*a, **k):
        raise AssertionError("a rebuild needs no model")

    monkeypatch.setattr(updates, "release_details", fake_details)
    monkeypatch.setattr(updates.ai, "one_shot", no_ai)
    text = asyncio.run(updates.explain_update("nginx:alpine", "docker", "de"))
    assert "Gleiche nginx-Version (1.31.6)" in text and "Risiko: gering" in text
    assert asyncio.run(updates.explain_update("nginx:alpine", "docker", "en")).startswith("- Same nginx")


def test_explain_sends_only_the_coming_notes_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "EXPLAIN_FILE", tmp_path / "x.json")
    calls = []

    async def fake_details(image):
        return {"label": "Matrix Synapse", "local": {"version": "1.160.0", "created": "2026-09-02"},
                "remote": {"version": "1.162.0", "created": "2026-09-29", "digest": "sha256:e"},
                "releases": [{"tag": "v1.162.0", "date": "2026-09-29", "newer": True,
                              "notes": "Adds MSC4140 delayed events.\n\n**Full Changelog**: https://x"},
                             {"tag": "v1.161.0", "date": "2026-09-15", "newer": True,
                              "notes": "Fixes a crash.\n* @someone made their first contribution in #1"},
                             {"tag": "v1.160.0", "date": "2026-09-01", "newer": False,
                              "notes": "THE OLD ONE"}],
                "rebuild": False, "major": False}

    async def fake_one_shot(prompt, system, feature=""):
        calls.append(prompt)
        return "- Delayed events\n- Crash fix\nRisk: low — patch releases\nRecommendation: update now"

    monkeypatch.setattr(updates, "release_details", fake_details)
    monkeypatch.setattr(updates.ai, "one_shot", fake_one_shot)
    first = asyncio.run(updates.explain_update("matrixdotorg/synapse:latest", "docker", "en"))
    again = asyncio.run(updates.explain_update("matrixdotorg/synapse:latest", "docker", "en"))
    assert first == again and len(calls) == 1             # the second answer came from the cache
    prompt = calls[0]
    assert "MSC4140" in prompt and "Fixes a crash" in prompt and "THE OLD ONE" not in prompt
    assert "Full Changelog" not in prompt and "first contribution" not in prompt
    assert "Installed: 1.160.0" in prompt and "New: 1.162.0" in prompt
    assert updates.cached_explanation("matrixdotorg/synapse:latest", "sha256:e", "en") == first
    assert updates.cached_explanation("matrixdotorg/synapse:latest", "sha256:e", "de") == ""
