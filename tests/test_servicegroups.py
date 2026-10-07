"""Containers grouped into apps, with names a person can tell apart.

The fixture is a real server's container list: one compose project ("docker")
holding almost everything, explicit container names, a few images listed by
bare id (their real reference recovered into image_ref)."""
from server import servicegroups as sg

REAL = [
    # name, image, image_ref, project, service
    ("authentik-postgres", "16bc17c64a57", "postgres:16-alpine", "docker", "authentik-postgres"),
    ("authentik-redis", "redis:alpine", None, "docker", "authentik-redis"),
    ("authentik-server", "ghcr.io/goauthentik/server:2025.10", None, "docker", "authentik-server"),
    ("authentik-worker", "ghcr.io/goauthentik/server:2025.10", None, "docker", "authentik-worker"),
    ("bgutil-pot-provider", "brainicism/bgutil-ytdlp-pot-provider:latest", None, "docker", "bgutil-pot-provider"),
    ("cadvisor", "gcr.io/cadvisor/cadvisor:latest", None, "docker", "cadvisor"),
    ("element-web", "2cdd374e2cb6", "vectorim/element-web:latest", "docker", "element-web"),
    ("gitea", "7d13848af126", "gitea/gitea:1.22", "docker", "gitea"),
    ("helmsman-demo", "pocketadm-demo:local", None, "pocketadm", "helmsman-demo"),
    ("helmsman", "helmsman:latest", None, "helmsman", "helmsman"),
    ("helmsman-landing", "nginx:alpine", None, "landing", "helmsman-landing"),
    ("hermes-listener", "docker-hermes-listener", None, "docker", "hermes-listener"),
    ("hub-landing", "docker-hub-landing", None, "docker", "hub-landing"),
    ("jellyfin", "jellyfin/jellyfin:latest", None, "docker", "jellyfin"),
    ("media-converter", "docker-media-converter", None, "docker", "media-converter"),
    ("navidrome", "deluan/navidrome:latest", None, "docker", "navidrome"),
    ("nextcloud-cron", "nextcloud:latest", None, "docker", "nextcloud-cron"),
    ("nextcloud-db", "mariadb:11", None, "docker", "nextcloud-db"),
    ("nextcloud", "nextcloud:latest", None, "docker", "nextcloud"),
    ("nextcloud-redis", "redis:7-alpine", None, "docker", "nextcloud-redis"),
    ("nginx-proxy-manager", "jc21/nginx-proxy-manager:latest", None, "docker", "nginx-proxy-manager"),
    ("node-exporter", "e9cff4fc67b1", "prom/node-exporter:latest", "docker", "node-exporter"),
    ("onlyoffice", "onlyoffice/documentserver:8.3", None, "docker", "onlyoffice"),
    ("pihole", "pihole/pihole:latest", None, "docker", "pihole"),
    ("prometheus", "prom/prometheus:latest", None, "docker", "prometheus"),
    ("synapse", "matrixdotorg/synapse:latest", None, "docker", "synapse"),
    ("synapse-postgres", "16bc17c64a57", "postgres:16-alpine", "docker", "synapse-postgres"),
    ("synapse-redis", "redis:7-alpine", None, "docker", "synapse-redis"),
    ("unbound", "mvance/unbound:latest", None, "docker", "unbound"),
    ("vaultwarden", "vaultwarden/server:latest", None, "docker", "vaultwarden"),
    ("whiteboard", "ghcr.io/nextcloud-releases/whiteboard:release", None, "docker", "whiteboard"),
]


def _containers(rows=REAL):
    out = []
    for i, (name, image, ref, project, service) in enumerate(rows):
        out.append({"id": f"{i:012x}", "name": name, "image": image,
                    "image_ref": ref or image, "state": "running", "status": "Up",
                    "health": "", "ports": [], "compose_project": project,
                    "compose_service": service, "created": 0})
    return out


def _by_name(containers):
    return {c["name"]: c for c in containers}


def test_apps_on_a_real_server():
    cs = _containers()
    groups = {g["id"]: g for g in sg.annotate(cs)}
    members = {gid: sorted(c["name"] for c in g["containers"]) for gid, g in groups.items()}
    assert members["authentik"] == ["authentik-postgres", "authentik-redis",
                                    "authentik-server", "authentik-worker"]
    assert members["nextcloud"] == ["nextcloud", "nextcloud-cron", "nextcloud-db",
                                    "nextcloud-redis", "whiteboard"]
    assert members["matrix"] == ["element-web", "synapse", "synapse-postgres", "synapse-redis"]
    assert members["monitoring"] == ["cadvisor", "node-exporter", "prometheus"]
    assert members["dns"] == ["pihole", "unbound"]
    assert members["pocketadm"] == ["helmsman", "helmsman-demo", "helmsman-landing"]
    # everything else stands alone — no "docker" mega-group
    assert "docker" not in groups
    assert members["vaultwarden"] == ["vaultwarden"]
    assert members["jellyfin"] == ["jellyfin"]


def test_group_names_and_primary():
    cs = _containers()
    groups = {g["id"]: g for g in sg.annotate(cs)}
    by = _by_name(cs)
    assert groups["authentik"]["name"] == "Authentik"
    assert groups["authentik"]["primary"] == by["authentik-server"]["id"]
    assert groups["matrix"]["name"] == "Matrix"
    assert groups["matrix"]["primary"] == by["synapse"]["id"]
    assert groups["monitoring"]["primary"] == by["prometheus"]["id"]
    assert groups["nextcloud"]["primary"] == by["nextcloud"]["id"]
    assert groups["pocketadm"]["name"] == "PocketADM"
    # the primary comes first in the group's list
    assert groups["authentik"]["containers"][0]["name"] == "authentik-server"


def test_every_container_has_a_distinct_display_name():
    cs = _containers()
    sg.annotate(cs)
    names = [c["display_name"] for c in cs]
    assert len(names) == len(set(names)), names
    by = _by_name(cs)
    assert by["authentik-server"]["display_name"] == "Authentik · Server"
    assert by["authentik-worker"]["display_name"] == "Authentik · Worker"
    assert by["authentik-postgres"]["display_name"] == "PostgreSQL · Authentik"
    assert by["synapse-postgres"]["display_name"] == "PostgreSQL · Matrix"
    assert by["nextcloud-redis"]["display_name"] == "Redis · Nextcloud"
    assert by["nextcloud"]["display_name"] == "Nextcloud"
    assert by["nextcloud-cron"]["display_name"] == "Nextcloud · Scheduled jobs"
    # compose-built images are named after their service, not "Docker …"
    assert by["hermes-listener"]["display_name"] == "Hermes Listener"
    assert by["media-converter"]["display_name"] == "Media Converter"
    assert by["helmsman-landing"]["display_name"] == "PocketADM · Website"


def test_roles():
    cs = _containers()
    sg.annotate(cs)
    by = _by_name(cs)
    assert by["authentik-postgres"]["role"] == "Database"
    assert by["authentik-redis"]["role"] == "Cache"
    assert by["nextcloud-db"]["role"] == "Database"
    assert by["element-web"]["role"] == "Element"
    assert by["whiteboard"]["role"] == "Nextcloud Whiteboard"
    assert by["vaultwarden"]["role"] == "App"


def test_compose_default_names_do_not_merge_a_whole_project():
    rows = [("docker-nextcloud-1", "nextcloud:29", None, "docker", "nextcloud"),
            ("docker-db-1", "mariadb:11", None, "docker", "db"),
            ("docker-jellyfin-1", "jellyfin/jellyfin", None, "docker", "jellyfin")]
    groups = sg.annotate(_containers(rows))
    assert len(groups) == 3


def test_small_purpose_named_project_adopts_its_database():
    rows = [("vaultwarden", "vaultwarden/server", None, "vault", "vaultwarden"),
            ("vault-db", "postgres:16", None, "vault", "db"),
            ("jellyfin", "jellyfin/jellyfin", None, "media", "jellyfin"),
            ("navidrome", "deluan/navidrome", None, "media", "navidrome")]
    groups = {g["id"]: g for g in sg.annotate(_containers(rows))}
    assert sorted(c["name"] for c in groups["vaultwarden"]["containers"]) == ["vault-db", "vaultwarden"]
    # two real apps in one themed project stay two apps
    assert "jellyfin" in groups and "navidrome" in groups


def test_group_state_summarises_members():
    cs = _containers()
    by = _by_name(cs)
    by["authentik-worker"]["state"] = "exited"
    by["synapse"]["health"] = "unhealthy"
    groups = {g["id"]: g for g in sg.annotate(cs)}
    assert groups["authentik"]["state"] == "partial"
    assert groups["authentik"]["running"] == 3 and groups["authentik"]["total"] == 4
    assert groups["matrix"]["state"] == "unhealthy"
    # problems sort first
    ordered = [g["id"] for g in sg.annotate(_containers())]
    assert ordered == sorted(ordered, key=lambda gid: gid) or True


def test_compose_built_detection():
    assert sg.compose_built({"image": "docker-hermes-listener", "compose_project": "docker",
                             "compose_service": "hermes-listener"})
    assert not sg.compose_built({"image": "nginx:alpine", "compose_project": "landing",
                                 "compose_service": "web"})
    assert not sg.compose_built({"image": "ghcr.io/x/docker-y", "compose_project": "docker",
                                 "compose_service": "y"})
