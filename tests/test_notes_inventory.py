"""0.26: the assistant's notes, PocketADM's records for the assistant, the
server inventory (domains, systemd units, cron) and the `pocketadm` command.

Each of these replaces guessing: notes instead of one ever-growing memory
text, records instead of twenty `find`/`grep` probes, a discovered inventory
instead of "which domain was that again"."""
import asyncio
import json
import sqlite3
import time

import pytest

from server import ai, cli, cmdpolicy, config, inventory, memory, records, suggestions


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "FILE", tmp_path / "agent-memory.json")
    monkeypatch.setattr(memory, "PREVIOUS", tmp_path / "agent-memory.prev.json")
    monkeypatch.setattr(memory, "LEGACY", tmp_path / "agent-memory.md")
    monkeypatch.setattr(memory, "LOCK", tmp_path / ".lock")
    return tmp_path


# ------------------------------------------------------------------ notes

def test_old_memory_becomes_notes_once(notes_dir):
    (notes_dir / "agent-memory.md").write_text(
        "Backups go to /mnt/backup every night.\n"
        "\n"
        "## Projekt \"StudGo\" — iOS-App (Stand 18.09.2026)\n"
        "- Code: /srv/cloud-server/studgo (SwiftUI)\n"
        "- Build via Codemagic\n"
        "  - workflow ios-release\n"
        "- password=hunter22 for the router\n")
    items = memory.notes()
    texts = [n["text"] for n in items]
    assert "Code: /srv/cloud-server/studgo (SwiftUI)" in texts
    assert any("workflow ios-release" in t for t in texts)            # sub-item joined
    assert all("hunter22" not in t for t in texts)                      # never a secret
    studgo = next(n for n in items if n["text"].startswith("Code:"))
    assert studgo["subject"] == "Projekt StudGo — iOS-App" and studgo["topic"] == "projects"
    assert next(n for n in items if "Backups" in n["text"])["topic"] == "storage"
    assert not (notes_dir / "agent-memory.md").exists()                 # kept as .md.bak
    assert (notes_dir / "agent-memory.md.bak").exists()
    assert len(memory.notes()) == len(items)                            # only once


def test_remember_dedupes_corrects_and_forgets(notes_dir):
    a, what = memory.add("Nextcloud data lives on /srv/nextcloud/data", source="assistant")
    assert what == "added" and a["topic"] == "services"
    b, what = memory.add("Nextcloud data lives on /srv/nextcloud/data.")
    assert what == "updated" and b["id"] == a["id"] and len(memory.notes()) == 1
    # alike, but another fact
    other, what = memory.add("Nextcloud data lives on /srv/nextcloud/data2")
    assert what == "added" and memory.forget(other["id"])
    c, what = memory.add("Nextcloud data moved to /mnt/data/nextcloud", replaces=a["id"])
    assert what == "replaced" and c["id"] == a["id"]
    assert memory.notes()[0]["text"] == "Nextcloud data moved to /mnt/data/nextcloud"
    with pytest.raises(ValueError):
        memory.add("the api_key=sk-abcdefghijklmnopqrstuvwx for OpenAI")
    assert memory.forget(a["id"]) and memory.notes() == []
    assert not memory.forget("nope")


def test_prompt_block_groups_pins_and_budgets(notes_dir):
    memory.add("Always answer in German", topic="preferences", pinned=True)
    for i in range(40):
        memory.add(f"Service number {i} runs on port {9000 + i} behind the proxy", topic="services")
    block = memory.prompt_block(budget=600)
    assert block.startswith("Apps & services:") or "Preferences:" in block
    assert "Always answer in German" in block                           # pinned survives the budget
    assert "older notes are not shown" in block
    assert len(block) < 900
    st = memory.stats()
    assert st["count"] == 41 and st["pinned"] == 1 and st["by_topic"]["services"] == 40


def test_tidy_falls_back_to_dedupe_and_can_be_undone(notes_dir, monkeypatch):
    now = time.time()
    memory.replace_all([
        {"id": "m1", "text": "Backups run at 03:00", "topic": "storage", "source": "assistant",
         "created": now, "updated": now, "pinned": False},
        {"id": "m2", "text": "Backups run at 03:00.", "topic": "storage", "source": "assistant",
         "created": now, "updated": now - 10, "pinned": False},
        {"id": "m3", "text": "Gitea is at git.example.org", "topic": "services", "source": "you",
         "created": now, "updated": now, "pinned": False}])
    if memory.PREVIOUS.exists():
        memory.PREVIOUS.unlink()

    async def no_model(*a, **k):
        raise RuntimeError("No AI is connected yet")
    monkeypatch.setattr(ai, "one_shot", no_model)
    result = asyncio.run(memory.tidy())
    assert result == {"before": 3, "after": 2, "used_ai": False, "removed": 1}
    assert memory.can_undo()
    assert memory.undo() and len(memory.notes()) == 3 and not memory.can_undo()


def test_tidy_with_a_model_keeps_pins_and_sources(notes_dir, monkeypatch):
    now = time.time()
    memory.replace_all([
        {"id": "m1", "text": "Backups at 03:00", "topic": "storage", "source": "you",
         "created": now - 50, "updated": now - 5, "pinned": False},
        {"id": "m2", "text": "Nightly backup is at 3am", "topic": "storage", "source": "assistant",
         "created": now - 90, "updated": now - 9, "pinned": False},
        {"id": "m3", "text": "Answer briefly", "topic": "preferences", "source": "you",
         "created": now, "updated": now, "pinned": True}])

    async def model(prompt, system="", feature=""):
        assert '"m1"' in prompt and feature == "insights"
        return json.dumps({"notes": [{"text": "Nightly backup at 03:00", "topic": "storage",
                                      "from": ["m1", "m2"]}]})
    monkeypatch.setattr(ai, "one_shot", model)
    result = asyncio.run(memory.tidy())
    assert result["used_ai"] and result["after"] == 2
    merged = next(n for n in memory.notes() if n["topic"] == "storage")
    assert merged["id"] == "m1" and merged["source"] == "you" and merged["created"] == now - 90
    assert any(n["pinned"] and n["text"] == "Answer briefly" for n in memory.notes())


def test_remember_and_forget_tools(notes_dir):
    out = asyncio.run(ai.execute_tool("remember", {"fact": "Minecraft runs as minecraft.service",
                                                   "topic": "services"}, "/tmp"))
    note_id = out.split("note ")[1].rstrip(".")
    assert memory.notes()[0]["id"] == note_id
    assert "Not saved" in asyncio.run(ai.execute_tool(
        "remember", {"fact": "token=abcdef1234567 for the API"}, "/tmp"))
    assert asyncio.run(ai.execute_tool("forget", {"id": note_id}, "/tmp")) == "Note removed."
    assert {"remember", "forget", "pocketadm"} <= ai.SAFE_TOOLS
    assert "update_memory" not in [t["name"] for t in ai.TOOLS]
    prompt = ai.system_prompt("/tmp", "agent")
    assert "PocketADM" in prompt and "Helmsman" not in prompt and "Vibe Code" not in prompt


def test_old_apps_still_read_and_write_memory_text(notes_dir):
    ai.save_memory("## Network\n- DNS is at deSEC\n- The VPS forwards IPv4\n")
    assert len(memory.notes()) == 2 and all(n["source"] == "you" for n in memory.notes())
    assert "## Network & domains" in ai.read_memory() and "- DNS is at deSEC" in ai.read_memory()


# ------------------------------------------------------------------ records

def test_records_answer_did_the_updates_go_well(tmp_path, monkeypatch):
    from server import activity, audit, dockerapi, jobs, updates
    audit_file = tmp_path / "audit.jsonl"
    now = time.time()
    audit_file.write_text("\n".join(json.dumps(e) for e in [
        {"t": now - 3600, "action": "update_apply", "target": "2 images",
         "detail": "nextcloud:latest, redis:7-alpine", "source": "ui", "status": "ok", "actor": "admin"},
        {"t": now - 60, "action": "terminal_kill", "target": "x", "source": "ui", "status": "ok"},
    ]) + "\n")
    monkeypatch.setattr(audit, "LOG_FILE", audit_file)
    history = tmp_path / "jobs-history.jsonl"
    history.write_text(json.dumps({"id": "j1", "title": "Update 2 images", "kind": "update",
                                   "status": "error", "created": now - 3590, "finished": now - 3500,
                                   "log_tail": ["✓ nextcloud updated", "✗ Redis failed: timeout"]}) + "\n")
    monkeypatch.setattr(records, "JOB_HISTORY", history)
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(updates, "_cache", {"time": now - 100, "result": [
        {"image": "jellyfin/jellyfin:latest", "update_available": True, "ignored": False}]})

    async def containers(all_=True):
        return [{"name": "nextcloud", "image": "nextcloud:latest", "state": "running",
                 "health": "healthy", "status": "Up 1 hour (healthy)", "ports": []},
                {"name": "redis", "image": "redis:7-alpine", "state": "restarting", "health": "",
                 "status": "Restarting (1) 5 seconds ago", "ports": []},
                {"name": "pocketadm-exec-1", "image": "helmsman:latest", "state": "running",
                 "health": "", "status": "Up", "ports": []}]
    monkeypatch.setattr(dockerapi, "list_containers", containers)
    text = asyncio.run(records.query("updates", days=2))
    assert "Pending image updates (1): jellyfin/jellyfin:latest" in text
    assert "nextcloud:latest, redis:7-alpine" in text
    assert "Update 2 images: error" in text and "Redis failed: timeout" in text
    assert "redis (redis:7-alpine): restarting" in text
    assert "terminal" not in text
    assert "Unknown topic" in asyncio.run(records.query("nope"))


def test_records_metrics_trend(tmp_path, monkeypatch):
    now = time.time()
    pts = [{"t": now - 6 * 86400 + i * 3600, "disk": 60 + i * 0.05, "mem": 50, "cpu": 10, "load": 1}
           for i in range(140)]
    pts[100]["disk"] += 3
    (tmp_path / "m.json").write_text(json.dumps(pts))
    monkeypatch.setattr(records, "METRICS_LONG", tmp_path / "m.json")
    text = asyncio.run(records.query("metrics", days=7))
    assert "System disk used: now 67.0%" in text and "/day" in text
    assert "Largest disk jumps: +3.0%" in text


def test_jobs_are_remembered_after_they_finish(tmp_path, monkeypatch):
    from server import jobs
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    async def go():
        async def work(job):
            job.log("pulling")
        job = jobs.start("Update x", "update", work)
        for _ in range(50):
            if job.status != "running":
                break
            await asyncio.sleep(0.02)
        if job.status == "running":
            job.finish(True)
    asyncio.run(go())
    line = json.loads((tmp_path / "jobs-history.jsonl").read_text().splitlines()[-1])
    assert line["title"] == "Update x" and line["status"] in ("done", "error")


# ------------------------------------------------------------------ inventory

def test_npm_domains_lead_to_containers(tmp_path):
    db = tmp_path / "database.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE proxy_host (domain_names TEXT, forward_scheme TEXT, forward_host TEXT,"
                " forward_port INT, enabled INT, certificate_id INT, is_deleted INT)")
    con.execute("CREATE TABLE redirection_host (domain_names TEXT, forward_domain_name TEXT,"
                " enabled INT, is_deleted INT)")
    con.executemany("INSERT INTO proxy_host VALUES (?,?,?,?,?,?,?)", [
        ('["cloud.example.org"]', "http", "nextcloud", 80, 1, 3, 0),
        ('["mc.example.org"]', "http", "172.18.0.1", 8710, 1, 3, 0),
        ('["old.example.org"]', "http", "gone", 80, 1, 0, 1),
        ('["tv.example.org"]', "http", "192.168.1.5", 8096, 0, 0, 0)])
    con.execute("INSERT INTO redirection_host VALUES (?,?,?,?)", ('["www.example.org"]', "example.org", 1, 0))
    con.commit()
    con.close()
    rows = inventory._npm_hosts(str(db))
    assert {r["domain"] for r in rows} == {"cloud.example.org", "mc.example.org", "tv.example.org",
                                           "www.example.org"}
    containers = [{"name": "nextcloud", "ports": []},
                  {"name": "jellyfin", "ports": [{"public": 8096}]}]
    inventory._attach_services(rows, containers)
    by = {r["domain"]: r for r in rows}
    assert by["cloud.example.org"]["service"] == "nextcloud" and by["cloud.example.org"]["tls"]
    assert by["tv.example.org"]["service"] == "jellyfin" and not by["tv.example.org"]["enabled"]
    assert by["mc.example.org"]["service"] == ""        # a host process, not a container


def test_caddy_nginx_traefik_and_cron_parsing(tmp_path, monkeypatch):
    caddy = inventory.parse_caddyfile("a.example.com, b.example.com {\n  reverse_proxy app:8080\n}\n"
                                      ":80 {\n respond hi\n}\n")
    assert [d["domain"] for d in caddy] == ["a.example.com", "b.example.com"]
    nginx = inventory.parse_nginx("server { server_name x.example.net; listen 443 ssl; "
                                  "location / { proxy_pass http://127.0.0.1:81; } }")
    assert nginx[0]["domain"] == "x.example.net" and nginx[0]["tls"] and nginx[0]["port"] == 81
    traefik = inventory.traefik_domains(
        [{"name": "blog"}], {"blog": {"traefik.http.routers.blog.rule": "Host(`blog.example.io`)",
                                     "traefik.http.routers.blog.tls.certresolver": "le"}})
    assert traefik == [{"domain": "blog.example.io", "target": "blog", "host": "blog", "port": 0,
                        "tls": True, "enabled": True, "source": "Traefik", "service": "blog"}]
    (tmp_path / "etc" / "cron.d").mkdir(parents=True)
    (tmp_path / "etc" / "crontab").write_text("SHELL=/bin/sh\n17 * * * * root run-parts /etc/cron.hourly\n")
    (tmp_path / "etc" / "cron.d" / "certs").write_text("*/5 * * * * root curl -s https://x?token=abc123\n")
    monkeypatch.setattr(inventory, "HOST", str(tmp_path))
    jobs = inventory._cron()
    assert jobs[0] == {"schedule": "17 * * * *", "user": "root",
                       "command": "run-parts /etc/cron.hourly", "file": "/etc/crontab"}
    assert "abc123" not in jobs[1]["command"] and "token=•••" in jobs[1]["command"]


def test_systemd_units_are_found_and_described(tmp_path, monkeypatch):
    base = tmp_path / "etc" / "systemd" / "system"
    (base / "multi-user.target.wants").mkdir(parents=True)
    (base / "backup.service").write_text("[Unit]\nDescription=Nightly backup\n")
    (base / "backup.timer").write_text("[Unit]\nDescription=Backup timer\n")
    (base / "display-manager.service").symlink_to("/lib/systemd/system/gdm3.service")
    (base / "multi-user.target.wants" / "ssh.service").symlink_to("/lib/systemd/system/ssh.service")
    monkeypatch.setattr(inventory, "HOST", str(tmp_path))
    monkeypatch.setattr(inventory.hostrun, "available", lambda: True)
    calls = []

    async def fake_run(command, timeout=60, cwd=None):
        calls.append(command)
        if command.startswith("systemctl list-units"):
            return 0, ("backup.service loaded inactive dead Nightly backup\n"
                       "backup.timer loaded active waiting Backup timer\n"
                       "ssh.service loaded active running OpenBSD Secure Shell server\n"
                       "gdm3.service loaded active running GNOME Display Manager\n"
                       "broken.service loaded failed failed Something broken\n"
                       "@@TIMERS@@\n"
                       "Fri 2026-10-09 02:30:00 UTC 8h left Thu 2026-10-08 02:30:00 UTC 15h ago "
                       "backup.timer backup.service\n")
        return 0, ("Id=backup.timer\nActiveState=active\nSubState=waiting\nUnitFileState=enabled\n"
                   "Triggers=backup.service\n\nId=ssh.service\nActiveState=active\n"
                   "UnitFileState=enabled\nMemoryCurrent=3645440\n")
    monkeypatch.setattr(inventory.hostrun, "run", fake_run)
    units = {u["unit"]: u for u in asyncio.run(inventory._systemd())}
    assert set(units) == {"backup.service", "backup.timer", "ssh.service", "broken.service"}
    assert units["backup.timer"]["next_run"] == "Fri 2026-10-09 02:30:00 UTC"
    assert units["backup.timer"]["custom"] and not units["ssh.service"]["custom"]
    assert units["ssh.service"]["memory"] == 3645440
    assert units["broken.service"]["active"] == "failed"
    assert "gdm3" not in " ".join(calls[1:])                     # desktop noise stays out
    lines = inventory.map_lines({"services": [u for u in units.values() if u["kind"] == "service"],
                                 "timers": [units["backup.timer"]], "domains": [], "drives": []})
    assert any(l.startswith("FAILED units: broken.service") for l in lines)


def test_unit_names_cannot_inject():
    assert inventory.valid_unit("wg-quick@wg0.service")
    for bad in ("x.service; rm -rf /", "$(id).service", "../x.service", "x", "a b.service"):
        assert not inventory.valid_unit(bad)
    with pytest.raises(ValueError):
        asyncio.run(inventory.unit_action("x.service;reboot", "restart"))
    with pytest.raises(ValueError):
        asyncio.run(inventory.unit_action("x.service", "mask"))


# ------------------------------------------------------------------ pocketadm command

def test_pocketadm_command_policy():
    read = ["pocketadm updates --days 3", "pocketadm overview", "pocketadm remember 'x' --topic server",
            "pocketadm forget m12ab3", "pocketadm host systemctl status minecraft",
            "pocketadm host 'journalctl -u backup -n 50'", "pocketadm host --timeout 30 df -h"]
    write = ["pocketadm host systemctl restart minecraft", "pocketadm host 'ls; rm -rf /'",
             "pocketadm host", "pocketadm pair", "pocketadm host 'cat /etc/hosts > /tmp/x'"]
    assert all(cmdpolicy.is_read_only(c) for c in read)
    assert not any(cmdpolicy.is_read_only(c) for c in write)


def test_pocketadm_cli_notes(notes_dir, capsys):
    assert cli.main(["remember", "Jellyfin", "uses", "Intel", "QSV", "--topic", "services"]) == 0
    assert "Saved as note" in capsys.readouterr().out
    note = memory.notes()[0]
    assert note["text"] == "Jellyfin uses Intel QSV" and note["topic"] == "services"
    assert cli.main(["notes"]) == 0 and note["id"] in capsys.readouterr().out
    assert cli.main(["forget", note["id"]]) == 0 and memory.notes() == []
    assert cli.main(["remember", "password=supersecret1"]) == 1


def test_suggestions_follow_the_server(monkeypatch):
    from server import dockerapi, updates

    async def containers(all_=True):
        return [{"name": "gitea", "image": "gitea", "state": "running", "health": "unhealthy",
                 "status": "Up (unhealthy)", "ports": []}]
    monkeypatch.setattr(dockerapi, "list_containers", containers)
    monkeypatch.setattr(updates, "_cache", {"time": time.time(), "result": [
        {"image": "a", "update_available": True}, {"image": "b", "update_available": True}]})
    monkeypatch.setattr(records, "_audit_rows", lambda *a, **k: [{"target": "3 images"}])
    monkeypatch.setattr(records, "_metric_points", lambda days: [{"disk": 91.0}])
    out = asyncio.run(suggestions.build())
    assert out[0] == "Why is gitea unhealthy, and how do I fix it?"
    assert "I updated 3 services in the last day — did everything come back fine?" in out
    assert any("91% full" in s for s in out) and len(out) == 4


# ------------------------------------------------------------------ the HTTP API

@pytest.fixture
def api(notes_dir, monkeypatch):
    from starlette.testclient import TestClient
    from server import auth, main
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + auth.issue_token()
    return c


def test_notes_api_round_trip(api):
    r = api.post("/api/agent/notes", json={"text": "Gitea backs up to /mnt/backup/gitea",
                                           "topic": "storage"})
    assert r.status_code == 200 and r.json()["result"] == "added"
    note = r.json()["note"]
    assert note["source"] == "you" and r.json()["stats"]["count"] == 1
    r = api.patch(f"/api/agent/notes/{note['id']}", json={"pinned": True, "subject": "Gitea"})
    assert r.json()["note"]["pinned"] and r.json()["note"]["subject"] == "Gitea"
    assert api.post("/api/agent/notes", json={"text": "secret=abcdefgh123"}).status_code == 400
    overview = api.get("/api/agent/notes").json()
    assert [t["id"] for t in overview["topics"]][0] == "server"
    assert api.get("/api/agent/memory").json()["memory"].startswith("## Storage & backups")
    assert api.delete(f"/api/agent/notes/{note['id']}").json()["stats"]["count"] == 0
    assert api.delete("/api/agent/notes/nope").status_code == 404
    assert api.post("/api/agent/notes/undo").status_code == 409


def test_units_api_validates_names(api):
    assert api.post("/api/system/units/x.service;reboot/restart").status_code in (400, 404)
    assert api.post("/api/system/units/ssh.service/mask").status_code == 400
    assert api.get("/api/system/units/not-a-unit").status_code == 400


def test_demo_serves_inventory_notes_and_suggestions(api, monkeypatch):
    from server import demodata, dockerapi
    monkeypatch.setattr(config, "DEMO", True)
    monkeypatch.setattr(dockerapi, "demo", lambda: True)
    inv = api.get("/api/inventory").json()
    assert inv["domains"] and inv["services"] and inv["timers"] and inv["drives"]
    unit = api.get("/api/system/units/minecraft.service").json()
    assert unit["active"] == "active" and "Started minecraft.service" in unit["logs"]
    assert api.post("/api/system/units/minecraft.service/restart").status_code == 403
    demodata.seed_notes()
    assert api.get("/api/agent/notes").json()["stats"]["count"] == 5
    assert len(api.get("/api/ai/suggestions").json()["suggestions"]) == 4
    assert "inventory" in api.get("/api/me").json()["features"]


def test_remove_key_through_the_api(api, monkeypatch, clean_settings):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    api.post("/api/settings/ai", json={"keys": {"openai": "sk-x-123"}})
    assert "openai" in config.configured_providers()
    r = api.post("/api/settings/ai", json={"keys": {"openai": "-"}})
    assert "openai" not in r.json()["configured"]
