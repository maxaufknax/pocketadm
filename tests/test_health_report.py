"""The health report as the app shows it: a score, five areas, an explanation
and something to do for every finding, and findings you accepted on purpose."""
import asyncio
import json
import os
import time

from server import permissions, reports


def _report(*checks):
    return {"time": time.time(), "duration": 0.1, "trigger": "manual",
            "counts": {}, "score": "ok", "checks": [dict(c) for c in checks]}


DISK = {"id": "disk", "group": "Resources", "title": "Disk usage", "icon": "", "status": "warn",
        "summary": "84% used"}
SSH = {"id": "ssh-pw", "group": "SSH", "title": "SSH password auth", "icon": "", "status": "warn",
       "summary": "Password authentication enabled"}
OK = {"id": "memory", "group": "Resources", "title": "Memory", "icon": "", "status": "ok",
      "summary": "40%"}


def test_decorate_adds_category_explanation_actions_and_score(clean_settings):
    r = reports.decorate(_report(DISK, SSH, OK))
    disk = next(c for c in r["checks"] if c["id"] == "disk")
    assert disk["category"] == "Stability" and "disk" in disk["explain"].lower()
    kinds = [a["kind"] for a in disk["actions"]]
    assert kinds[:2] == ["open", "job"] and "assistant" in kinds and kinds[-1] == "mute"
    assistant = next(a for a in disk["actions"] if a["kind"] == "assistant")
    assert "84% used" in assistant["prompt"]
    ok = next(c for c in r["checks"] if c["id"] == "memory")
    assert ok["actions"] == []
    assert r["counts"] == {"ok": 1, "info": 0, "warn": 2, "crit": 0}
    assert r["score"] == "warn" and r["points"] == 84


def test_accepting_a_finding_takes_it_out_of_the_score(clean_settings):
    reports.set_muted("ssh-pw", True, "LAN only")
    r = reports.decorate(_report(DISK, SSH))
    ssh = next(c for c in r["checks"] if c["id"] == "ssh-pw")
    assert ssh["muted"] and ssh["status"] == "info" and ssh["original_status"] == "warn"
    assert ssh["muted_note"] == "LAN only"
    assert ssh["actions"] == [{"kind": "unmute", "label": "Watch this again"}]
    assert r["counts"]["warn"] == 1 and r["muted_count"] == 1
    # decorating a stored report twice must not double-apply
    again = reports.decorate(r)
    assert again["counts"]["warn"] == 1
    reports.set_muted("ssh-pw", False)
    r = reports.decorate(_report(DISK, SSH))
    assert r["counts"]["warn"] == 2


def test_stale_permission_requests_expire_and_duplicates_merge(clean_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "_FILE", tmp_path / "perm.json")
    old = permissions.add("fs", "Write access to the host filesystem", "/host ro")
    for detail in ("a", "b", "c"):
        permissions.add("root", "Elevated (root) privileges", detail)
    items = json.loads((tmp_path / "perm.json").read_text())
    for p in items:
        if p["id"] == old["id"]:
            p["last_seen"] = p["time"] = time.time() - 10 * 86400
    (tmp_path / "perm.json").write_text(json.dumps(items))
    rows = asyncio.run(reports.check_agent_tasks())
    assert len(rows) == 1
    assert rows[0]["title"] == "Elevated (root) privileges"
    assert len(rows[0]["permission_ids"]) == 3
    assert next(p for p in json.loads((tmp_path / "perm.json").read_text())
                if p["id"] == old["id"])["status"] == "expired"
    assert permissions.set_status_many(rows[0]["permission_ids"], "dismissed") == 3
    assert asyncio.run(reports.check_agent_tasks()) == []


def test_backup_timers_count_as_backups(tmp_path):
    units = tmp_path / "etc/systemd/system"
    (units / "timers.target.wants").mkdir(parents=True)
    (units / "backup.timer").write_text("[Timer]\n")
    (units / "timers.target.wants" / "backup.timer").write_text("")
    (units / "minecraft-backup.timer").write_text("[Timer]\n")   # not enabled
    (units / "certbot.timer").write_text("[Timer]\n")
    stamps = tmp_path / "var/lib/systemd/timers"
    stamps.mkdir(parents=True)
    stamp = stamps / "stamp-backup.timer"
    stamp.write_text("")
    os.utime(stamp, (time.time() - 3600, time.time() - 3600))
    timers = {t["name"]: t for t in reports._backup_timers(str(tmp_path))}
    assert set(timers) == {"backup", "minecraft-backup"}
    assert timers["backup"]["enabled"] and not timers["minecraft-backup"]["enabled"]
    assert time.time() - timers["backup"]["last_run"] < 4000
