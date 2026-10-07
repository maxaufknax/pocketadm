"""Containers as apps: which containers belong together, what each one does,
and what to call it.

A server with thirty-odd containers in one compose project ("docker") read as
thirty-odd rows under one heading — three of them called "Authentik", one
"Docker Hermes Listener". People think in services: *Nextcloud* is the app,
its database, cache and cron container are parts of it. This module groups
containers into those apps and names every container so it can be told apart:

  * containers that share a name prefix belong together (authentik-server,
    authentik-worker, authentik-postgres, authentik-redis);
  * a few well-known families belong together across prefixes (Synapse and
    Element are Matrix; Prometheus, cAdvisor and node-exporter are monitoring);
  * in a small, purpose-named compose project, a lone database or cache joins
    the project's one real app;
  * every container gets a role inside its app ("Server", "Database") and a
    display name that is unique on the server ("Authentik Server",
    "PostgreSQL · Synapse").

Pure functions over the container dicts dockerapi.list_containers returns, so
the grouping is testable without Docker. The only I/O is recovering the image
name of containers Docker lists by bare image id (resolve_images).
"""
from __future__ import annotations

import re
from collections import Counter

from . import appstore, dockerapi, updates

# Compose projects named like this hold a whole server, not one app.
GENERIC_PROJECTS = {"docker", "compose", "default", "stack", "stacks", "server", "servers",
                    "homeserver", "home", "homelab", "apps", "app", "services", "service",
                    "main", "prod", "production", "root", "srv", "infra", "containers"}
# A purpose-named project bigger than this is a theme ("media"), not one app.
_SMALL_PROJECT = 8

# The word after the app's prefix says what a container is for.
ROLE_WORDS = {
    "db": "Database", "database": "Database", "postgres": "Database", "postgresql": "Database",
    "pg": "Database", "mariadb": "Database", "mysql": "Database", "mongo": "Database",
    "mongodb": "Database", "sql": "Database",
    "redis": "Cache", "valkey": "Cache", "memcached": "Cache", "cache": "Cache", "keydb": "Cache",
    "cron": "Scheduled jobs", "scheduler": "Scheduled jobs", "beat": "Scheduled jobs",
    "worker": "Worker", "workers": "Worker", "celery": "Worker", "jobs": "Worker",
    "queue": "Queue", "rabbitmq": "Queue", "broker": "Queue", "kafka": "Queue",
    "server": "Server", "web": "Web", "app": "App", "api": "API", "frontend": "Frontend",
    "backend": "Backend", "proxy": "Proxy", "nginx": "Web server", "reports": "Reports",
    "landing": "Website", "site": "Website", "demo": "Demo", "listener": "Listener",
    "bot": "Bot", "ldap": "LDAP", "outpost": "Outpost", "exporter": "Exporter",
    "migrate": "Migration", "migrations": "Migration", "init": "Setup", "setup": "Setup",
    "ui": "Interface", "admin": "Admin", "backup": "Backup", "search": "Search",
    "storage": "Storage", "minio": "Storage", "ml": "Machine learning", "agent": "Agent",
    "gateway": "Gateway", "dashboard": "Dashboard", "sync": "Sync", "media": "Media",
}
SUPPORT_ROLES = {"Database", "Cache", "Queue", "Scheduled jobs"}
SUPPORT_CATEGORIES = {"Database", "Cache"}

# Families that belong together even with different name prefixes:
# (tokens, family id, label shown when the group has several members, category)
FAMILIES = (
    (("synapse", "element", "mautrix", "dendrite", "conduit", "conduwuit", "matrix"),
     "matrix", "Matrix", "Communication"),
    (("prometheus", "cadvisor", "node-exporter", "nodeexporter", "grafana", "loki",
      "promtail", "alertmanager", "alloy"),
     "monitoring", "Monitoring", "Monitoring"),
    (("pihole", "pi-hole", "unbound"), "dns", "Pi-hole", "DNS / Adblock"),
    (("helmsman", "pocketadm"), "pocketadm", "PocketADM", "Management"),
    (("nextcloud",), "nextcloud", "Nextcloud", "Files & Sync"),
    (("immich",), "immich", "Immich", "Photos"),
    (("paperless",), "paperless", "Paperless-ngx", "Documents"),
)
_FAMILY_BY_TOKEN = {tok: fam for fam in FAMILIES for tok in fam[0]}

_SPLIT = re.compile(r"[-_.]")


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[-_./:@]", (text or "").lower()) if t]


def prefix_of(name: str) -> str:
    return _SPLIT.split((name or "").lower(), 1)[0] or (name or "").lower()


def pretty(text: str) -> str:
    """"hermes-listener" -> "Hermes Listener"."""
    words = [w for w in re.split(r"[-_. ]+", text or "") if w]
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words) or text


def compose_built(container: dict) -> bool:
    """An image compose built locally: named <project>-<service>, no registry,
    no namespace. Its name says nothing about what runs in it."""
    image = (container.get("image") or "").split("@")[0]
    if "/" in image:
        return False
    repo = image.rsplit(":", 1)[0] if ":" in image else image
    project = (container.get("compose_project") or "").lower()
    service = (container.get("compose_service") or "").lower()
    if not project:
        return False
    return repo.lower() in (f"{project}-{service}", f"{project}_{service}") \
        or (repo.lower().startswith(project + "-") and bool(service))


def base_name(container: dict) -> str:
    """The container name without what compose added on its own: the
    project prefix and the replica number ("docker-nextcloud-1" -> "nextcloud").
    Explicit container names stay as they are."""
    name = (container.get("name") or "").lower()
    project = (container.get("compose_project") or "").lower()
    if project and (name.startswith(project + "-") or name.startswith(project + "_")):
        rest = re.sub(r"[-_]\d+$", "", name[len(project) + 1:])
        if rest:
            return rest
    return name


def family_of(container: dict) -> tuple | None:
    """The family a container belongs to, from its name or its image path."""
    name = base_name(container)
    for token in _tokens(name):
        if token in _FAMILY_BY_TOKEN:
            return _FAMILY_BY_TOKEN[token]
    # two-word tokens ("node-exporter", "pi-hole") never survive the split
    for fam in FAMILIES:
        if any("-" in t and t in name for t in fam[0]):
            return fam
    image = container.get("image_ref") or container.get("image") or ""
    for token in _tokens(image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image):
        if token in _FAMILY_BY_TOKEN:
            return _FAMILY_BY_TOKEN[token]
    return None


def service_meta_for(container: dict) -> dict:
    """Friendly label/icon/category for one container."""
    name = container.get("name", "")
    lower = name.lower()
    if "helmsman" in lower or "pocketadm" in lower:
        return {"label": "PocketADM", "icon": "🧭", "category": "Management", "security": True}
    if compose_built(container):
        label = pretty(container.get("compose_service") or name)
        return {"label": label, "icon": "📦", "category": "Service", "security": False}
    ref = container.get("image_ref") or container.get("image") or ""
    if not ref or appstore._is_image_id(ref):
        ref = name
    return updates.service_meta(ref)


def role_of(container: dict, prefix: str, meta: dict) -> str:
    """What a container does inside its app."""
    name = base_name(container)
    rest = name[len(prefix):].strip("-_.") if name.startswith(prefix) else ""
    words = [w for w in _SPLIT.split(rest) if w and not w.isdigit()]
    for word in words:
        if word in ROLE_WORDS:
            return ROLE_WORDS[word]
    if meta.get("category") in SUPPORT_CATEGORIES:
        return meta["category"]
    if words:
        return pretty(" ".join(words))
    return "App"


def is_support(container: dict) -> bool:
    return container.get("role") in SUPPORT_ROLES \
        or (container.get("service") or {}).get("category") in SUPPORT_CATEGORIES


async def resolve_images(containers: list[dict], cache: dict | None = None) -> None:
    """Docker lists a container by bare image id once its tag moved on (a
    newer pull, a rebuilt image). The container's own config still names the
    image it was created from — recover it, so a PostgreSQL started from an
    id is still recognised as PostgreSQL. Sets `image_ref` on each container."""
    cache = cache if cache is not None else {}
    for c in containers:
        image = c.get("image") or ""
        if not appstore._is_image_id(image):
            c["image_ref"] = image
            continue
        key = c.get("id", "") + str(c.get("created", ""))
        if key not in cache:
            ref = ""
            try:
                detail = await dockerapi.inspect_container(c["id"])
                ref = (detail.get("Config") or {}).get("Image", "") or ""
            except Exception:
                ref = ""
            cache[key] = "" if appstore._is_image_id(ref) else ref
        c["image_ref"] = cache[key] or image


def annotate(containers: list[dict]) -> list[dict]:
    """Group containers into apps and name every container.

    Adds to each container: `service` (label/icon/category/security),
    `role`, `group_id`, `group_name`, `display_name`. Returns the groups,
    each with its containers (the same dicts), primary first."""
    project_size = Counter(c.get("compose_project") for c in containers
                           if c.get("compose_project"))
    keys: dict[str, str] = {}
    families: dict[str, tuple] = {}
    for c in containers:
        c["service"] = service_meta_for(c)
        fam = family_of(c)
        if fam:
            key = fam[1]
            families[key] = fam
        else:
            key = prefix_of(base_name(c))
        keys[c["id"]] = key

    # A lone database or cache in a small, purpose-named project joins the
    # project's one real app (vault + vault-db; but not "media", which holds two).
    by_project: dict[str, list[dict]] = {}
    for c in containers:
        proj = (c.get("compose_project") or "")
        if proj and proj.lower() not in GENERIC_PROJECTS and project_size[proj] <= _SMALL_PROJECT:
            by_project.setdefault(proj, []).append(c)
    for members in by_project.values():
        mains = {keys[c["id"]] for c in members
                 if c["service"].get("category") not in SUPPORT_CATEGORIES}
        if len(mains) != 1:
            continue
        main_key = next(iter(mains))
        for c in members:
            if c["service"].get("category") in SUPPORT_CATEGORIES:
                keys[c["id"]] = main_key

    grouped: dict[str, list[dict]] = {}
    for c in containers:
        grouped.setdefault(keys[c["id"]], []).append(c)

    groups: list[dict] = []
    for key, members in grouped.items():
        fam = families.get(key)
        for c in members:
            meta = c["service"]
            if len(members) == 1:
                c["role"] = "App"
            elif fam and meta.get("category") not in SUPPORT_CATEGORIES \
                    and meta["label"] not in (fam[2], "PocketADM"):
                # Synapse and Element in "Matrix": named by what they are
                c["role"] = meta["label"]
            else:
                c["role"] = role_of(c, prefix_of(base_name(c)), meta)
        primary = _primary(members, fam)
        if fam and (len(members) > 1 or fam[1] == "pocketadm"):
            name, category = fam[2], fam[3]
        else:
            name, category = primary["service"]["label"], primary["service"]["category"]
        members.sort(key=lambda c: (c is not primary, is_support(c), c.get("name", "")))
        running = sum(1 for c in members if c.get("state") == "running")
        unhealthy = sum(1 for c in members if c.get("health") == "unhealthy")
        restarting = sum(1 for c in members if c.get("state") == "restarting")
        if restarting:
            state = "restarting"
        elif unhealthy:
            state = "unhealthy"
        elif running == len(members):
            state = "running"
        elif running == 0:
            state = "stopped"
        else:
            state = "partial"
        ports = sorted({p["public"] for c in members for p in c.get("ports") or []
                        if p.get("public")})
        reachable = sorted({p["public"] for c in members for p in c.get("ports") or []
                            if p.get("public") and p.get("ip") in ("", "0.0.0.0", "::")})
        group = {
            "id": key,
            "name": name,
            "category": category,
            "icon": primary["service"].get("icon", ""),
            "icon_names": [name, primary["service"]["label"], primary.get("name", ""),
                           primary.get("image_ref") or primary.get("image", "")],
            "primary": primary["id"],
            "containers": members,
            "running": running,
            "total": len(members),
            "unhealthy": unhealthy,
            "state": state,
            "ports": ports,
            "reachable_ports": reachable,
            "compose_project": primary.get("compose_project", ""),
            "compose_dir": primary.get("compose_dir", ""),
            "security": any((c["service"] or {}).get("security") for c in members),
        }
        for c in members:
            c["group_id"] = key
            c["group_name"] = name
        groups.append(group)

    # Display names that tell containers apart across the whole server.
    labels = Counter(c["service"]["label"] for c in containers)
    for c in containers:
        label = c["service"]["label"]
        role = c.get("role") or "App"
        if labels[label] <= 1:
            c["display_name"] = label
        elif is_support(c) and c.get("group_name") and c["group_name"] != label:
            c["display_name"] = f"{label} · {c['group_name']}"
        elif role not in ("App", label):
            c["display_name"] = f"{label} · {role}"
        else:
            c["display_name"] = label
    # still ambiguous (two independent "Nginx"): fall back to the container name
    names = Counter(c["display_name"] for c in containers)
    for c in containers:
        if names[c["display_name"]] > 1 and c["role"] != "App":
            c["display_name"] = pretty(base_name(c))
    names = Counter(c["display_name"] for c in containers)
    for c in containers:
        if names[c["display_name"]] > 1:
            c["display_name"] = pretty(base_name(c))

    state_rank = {"restarting": 0, "unhealthy": 1, "partial": 2, "stopped": 3, "running": 4}
    groups.sort(key=lambda g: (state_rank.get(g["state"], 5), g["name"].lower()))
    return groups


def _primary(members: list[dict], fam: tuple | None = None) -> dict:
    """The container that stands for the app: not a database or cache, the
    family's namesake (Synapse in Matrix), the one with published ports, the
    one named exactly like the app."""
    lead = fam[0][0] if fam else ""

    def score(c: dict) -> tuple:
        role = c.get("role", "")
        return (
            c["service"].get("category") not in SUPPORT_CATEGORIES and role not in SUPPORT_ROLES,
            bool(lead) and lead in base_name(c),
            role in ("App", "Server", "Web"),
            len(c.get("ports") or []),
            c.get("state") == "running",
            -len(c.get("name", "")),
        )
    return max(members, key=score)
