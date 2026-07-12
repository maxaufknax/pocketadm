"""Pre-update snapshots — the safety net under one-tap updates.

Before an update pulls a new image, the currently running image is pinned
under a `helmsman/snapshot:<slug>-<ts>` tag (so `docker image prune` and the
moving upstream tag can't take it away) together with the names of the
containers that ran on it. Rolling back recreates those containers on the
snapshot image — same config, previous version.
"""
import asyncio
import json
import re
import secrets
import time

from . import config, dockerapi, jobs

SNAP_FILE = config.DATA_DIR / "snapshots.json"
SNAP_REPO = "helmsman/snapshot"
KEEP_PER_IMAGE = 3


def _load() -> list[dict]:
    try:
        return json.loads(SNAP_FILE.read_text()) if SNAP_FILE.exists() else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    SNAP_FILE.write_text(json.dumps(items, indent=1))


def list_snapshots() -> list[dict]:
    return sorted(_load(), key=lambda s: -s["time"])


def get(snap_id: str) -> dict | None:
    return next((s for s in _load() if s["id"] == snap_id), None)


def _slug(image: str) -> str:
    base = image.split("@")[0].lower()
    return re.sub(r"[^a-z0-9_.-]+", "-", base).strip("-.")[:48] or "image"


async def create_snapshot(image: str, containers: list[str]) -> dict | None:
    """Pin the current image under a snapshot tag. Returns the record,
    or None when there is nothing to snapshot (image missing locally)."""
    info = await dockerapi.inspect_image(image)
    if not info or not info.get("Id"):
        return None
    ts = int(time.time())
    tag = f"{_slug(image)}-{ts}"
    await dockerapi.tag_image(info["Id"], SNAP_REPO, tag)
    snap = {"id": secrets.token_hex(5), "time": ts, "image": image,
            "image_id": info["Id"].removeprefix("sha256:")[:12],
            "ref": f"{SNAP_REPO}:{tag}", "containers": containers[:20]}
    items = _load()
    items.append(snap)
    # retention: keep the newest KEEP_PER_IMAGE snapshots per image ref
    same = sorted((s for s in items if s["image"] == image), key=lambda s: -s["time"])
    for old in same[KEEP_PER_IMAGE:]:
        items.remove(old)
        try:
            await dockerapi.remove_image(old["ref"])
        except Exception:
            pass
    _save(items)
    return snap


async def delete(snap_id: str) -> bool:
    items = _load()
    snap = next((s for s in items if s["id"] == snap_id), None)
    if not snap:
        return False
    items.remove(snap)
    _save(items)
    try:
        await dockerapi.remove_image(snap["ref"])
    except Exception:
        pass
    return True


def start_rollback_job(snap: dict) -> jobs.Job:
    """Recreate the snapshot's containers on the pinned previous image."""

    async def work(job: jobs.Job) -> None:
        from . import updates  # runtime import (updates imports us)
        info = await dockerapi.inspect_image(snap["ref"])
        if not info:
            job.finish(False, f"✗ Snapshot image {snap['ref']} no longer exists")
            return
        containers = {c["name"]: c for c in await dockerapi.list_containers(all_=True)}
        job.log(f"↩ Rolling back to {snap['image']} as of "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(snap['time']))} "
                f"(image {snap['image_id']})")
        done, missing = 0, []
        for name in snap["containers"]:
            c = containers.get(name)
            if not c:
                missing.append(name)
                job.log(f"◌ {name}: container not found anymore — skipped")
                continue
            async with job.step(f"♻ Recreating {name} on the snapshot image …"):
                new_id = await dockerapi.recreate_container(c["id"], job.log,
                                                            image=snap["ref"])
            job.log(f"✓ {name} started ({new_id}) — waiting for it to settle …")
            await updates._wait_healthy(job, new_id, name)
            done += 1
        updates._cache["time"] = 0
        if done:
            job.finish(True, f"✓ Rollback finished — {done} container"
                             f"{'s' if done != 1 else ''} back on the previous version"
                             + (f" ({len(missing)} skipped)" if missing else ""))
        else:
            job.finish(False, "✗ Rollback found none of the snapshot's containers")

    return jobs.start(f"Roll back {snap['image']}", "rollback", work)
