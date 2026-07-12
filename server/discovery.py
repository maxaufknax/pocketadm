"""Detect services the agent just brought up.

When the Vibe agent installs or builds something (docker run, compose up,
a Dockerfile it wrote …), new containers appear on the host. We snapshot the
running set before a turn and diff afterwards, so Helmsman can proactively
surface a freshly-created service and offer to finish wiring it up (open it,
reverse-proxy + HTTPS, keep it running, back it up) instead of leaving the
user to discover it themselves.
"""
from . import appstore, dockerapi, updates

# our own containers / rename churn must never look like a "new service"
_IGNORE_EXACT = {"helmsman"}


async def snapshot_ids() -> set[str]:
    """IDs of currently running containers (cheap; called before a turn)."""
    try:
        return {c["id"] for c in await dockerapi.list_containers(all_=False)}
    except Exception:
        return set()


async def new_services(before_ids: set[str]) -> list[dict]:
    """Containers that appeared since `before_ids`, grouped into services."""
    try:
        current = await dockerapi.list_containers(all_=False)
    except Exception:
        return []
    fresh = [c for c in current
             if c["id"] not in before_ids
             and c["name"] not in _IGNORE_EXACT
             and not c["name"].endswith("-old-helmsman")
             and not c["compose_project"].startswith("helmsman-")]
    if not fresh:
        return []

    groups: dict[str, list[dict]] = {}
    for c in fresh:
        key = c["compose_project"] or ("solo:" + c["name"])
        groups.setdefault(key, []).append(c)

    out: list[dict] = []
    for members in groups.values():
        # the container exposing ports (or just the first) represents the group
        main = max(members, key=lambda c: (len(c["ports"]), c["state"] == "running"))
        ports = sorted({p["public"] for m in members for p in m["ports"]})
        ref = main["name"] if appstore._is_image_id(main["image"]) else main["image"]
        meta = updates.service_meta(ref)
        out.append({
            "id": main["id"],
            "name": main["compose_service"] or main["name"],
            "container": main["name"],
            "project": main["compose_project"],
            "image": main["image"],
            "ports": ports,
            "primary_port": ports[0] if ports else None,
            "count": len(members),
            "icon": meta["icon"],
            "label": meta["label"],
            "running": any(m["state"] == "running" for m in members),
        })
    # most interesting first (has a port), cap so a big stack doesn't spam chat
    out.sort(key=lambda s: (s["primary_port"] is None, s["name"]))
    return out[:6]
