"""The container endpoints the iOS app's Containers tab and detail screen use,
against the demo's canned Docker data (never the real socket)."""
import pytest
from starlette.testclient import TestClient

from server import auth, config, dockerapi, main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "DEMO", True)
    monkeypatch.setattr(dockerapi, "demo", lambda: True)
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + auth.issue_token()
    return c


def test_containers_carry_their_app_role_and_unique_name(client):
    rows = client.get("/api/containers").json()
    assert rows and all({"group_id", "group_name", "role", "display_name", "service"} <= set(r)
                        for r in rows)
    names = [r["display_name"] for r in rows]
    assert len(names) == len(set(names))
    nextcloud = {r["name"]: r for r in rows if r["group_id"] == "nextcloud"}
    assert set(nextcloud) == {"nextcloud", "nextcloud-db", "nextcloud-redis"}
    assert nextcloud["nextcloud-db"]["role"] == "Database"


def test_services_group_the_containers(client):
    groups = client.get("/api/services").json()["groups"]
    by_id = {g["id"]: g for g in groups}
    assert by_id["nextcloud"]["total"] == 3
    assert by_id["nextcloud"]["containers"][0]["name"] == "nextcloud"
    # the exited backup container makes its app "stopped", and problems sort first
    states = [g["state"] for g in groups]
    assert states.index("stopped") < states.index("running")


def test_detail_masks_secrets(client):
    cid = client.get("/api/containers").json()[0]["id"]
    detail = client.get(f"/api/containers/{cid}/detail").json()
    env = {e["key"]: e for e in detail["env"]}
    secret = next(e for k, e in env.items() if k.endswith("_PASSWORD"))
    assert secret["secret"] and secret["value"] != "never-shown"
    assert env["TZ"]["value"] == "Europe/Berlin" and not env["TZ"]["secret"]
    assert detail["network_details"] and "ports" in detail and "resources" in detail


def test_top_events_and_live_stats(client):
    cid = client.get("/api/containers").json()[0]["id"]
    top = client.get(f"/api/containers/{cid}/top").json()
    assert top["titles"][0] == "PID" and top["processes"]
    events = client.get(f"/api/containers/{cid}/events").json()["events"]
    assert events and events[0]["t"] >= events[-1]["t"]
    stats = client.get(f"/api/containers/{cid}/stats?live=1").json()
    assert {"cpu_percent", "mem_usage", "net_rx_rate", "pids"} <= set(stats)


def test_demo_refuses_group_actions(client):
    r = client.post("/api/services/nextcloud/action", json={"action": "restart"})
    assert r.status_code == 403


def test_mask_env_rules():
    rows = {e["key"]: e for e in dockerapi.mask_env([
        "POSTGRES_PASSWORD=x", "DATABASE_URL=postgres://u:p@db/x", "LOG_LEVEL=info",
        "API_TOKEN=", "SOME_URL=https://example.com/a"])}
    assert rows["POSTGRES_PASSWORD"]["value"] == dockerapi.MASK
    assert rows["DATABASE_URL"]["secret"]
    assert rows["LOG_LEVEL"]["value"] == "info"
    assert rows["API_TOKEN"]["value"] == ""            # empty stays empty, still marked
    assert not rows["SOME_URL"]["secret"]


def test_stats_summary_uses_previous_sample_for_one_shot():
    prev = {"cpu_stats": {"cpu_usage": {"total_usage": 1000}, "system_cpu_usage": 10000,
                          "online_cpus": 2},
            "memory_stats": {"usage": 100, "limit": 1000, "stats": {"inactive_file": 20}},
            "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 500}}}
    cur = {"cpu_stats": {"cpu_usage": {"total_usage": 1500}, "system_cpu_usage": 20000,
                         "online_cpus": 2},
           "precpu_stats": {},
           "memory_stats": {"usage": 300, "limit": 1000, "stats": {"inactive_file": 50}},
           "networks": {"eth0": {"rx_bytes": 3000, "tx_bytes": 900}},
           "pids_stats": {"current": 7}}
    out = dockerapi.stats_summary(cur, prev, elapsed=2.0)
    assert out["cpu_known"] and out["cpu_percent"] == 10.0
    assert out["mem_usage"] == 250
    assert out["net_rx_rate"] == 1000.0 and out["net_tx_rate"] == 200.0
    assert out["pids"] == 7


def test_describe_event_words():
    die = dockerapi.describe_event({"t": 1, "action": "die", "exit_code": "137"})
    assert die["summary"] == "exited (exit code 137)" and die["severity"] == "warn"
    oom = dockerapi.describe_event({"t": 1, "action": "oom"})
    assert oom["severity"] == "crit"
    health = dockerapi.describe_event({"t": 1, "action": "health_status: unhealthy"})
    assert health["summary"] == "health turned unhealthy"
    assert dockerapi.describe_event({"t": 1, "action": "exec_start: sh"}) is None


def test_demux_handles_frames_and_tty():
    frame = b"\x01\x00\x00\x00\x00\x00\x00\x05hello" + b"\x02\x00\x00\x00\x00\x00\x00\x03err"
    frames, rest = dockerapi._demux(frame + b"\x01\x00\x00")
    assert frames == [b"hello", b"err"] and rest == b"\x01\x00\x00"
    frames, rest = dockerapi._demux(b"plain tty output\n")
    assert frames == [] and rest == b"plain tty output\n"
