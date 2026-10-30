"""The Alertmanager webhook: token auth, and payload → activity events."""
import pytest
from starlette.testclient import TestClient

from server import alerts, auth, config, main


@pytest.fixture
def client():
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + auth.issue_token()
    return c


def test_token_generated_once_and_persists(clean_settings):
    first = alerts.token()
    assert first and first == alerts.token()
    assert config.settings["alerts_token"] == first
    rotated = alerts.rotate()
    assert rotated != first and alerts.verify(rotated)


def test_verify_requires_known_token(clean_settings):
    assert not alerts.verify("nope")            # no token stored yet
    tok = alerts.token()
    assert alerts.verify(tok)
    assert not alerts.verify(tok + "x")
    assert not alerts.verify("")


def test_ingest_maps_alerts_to_activity(clean_settings):
    from server import activity
    payload = {
        "alerts": [
            {"status": "firing", "labels": {"alertname": "HostDiskCritical", "severity": "critical"},
             "annotations": {"summary": "Disk is 92% full", "description": "on /"}},
            {"status": "firing", "labels": {"alertname": "ContainerDown", "severity": "warning",
                                            "instance": "jellyfin:8096"},
             "annotations": {}},
            {"status": "resolved", "labels": {"alertname": "HostHighLoad", "severity": "warning"},
             "annotations": {"summary": "load back to normal"}},
            "not-an-alert",
        ]
    }
    result = alerts.ingest(payload)
    assert result == {"fired": 2, "resolved": 1}
    events = list(activity.EVENTS)[-3:]
    assert [e["kind"] for e in events] == ["alert", "alert", "alert-resolved"]
    assert events[0]["title"] == "HostDiskCritical"
    assert events[0]["severity"] == "crit" and events[0]["source"] == "prometheus"
    assert events[0]["detail"] == "on /"
    assert events[1]["severity"] == "warn" and events[1]["target"] == "jellyfin:8096"
    assert events[2]["title"] == "HostHighLoad resolved" and events[2]["severity"] == "ok"
    assert alerts.ingest({}) == {"fired": 0, "resolved": 0}
    assert alerts.ingest("garbage") == {"fired": 0, "resolved": 0}


def test_webhook_route_auth_and_ingest(client, clean_settings):
    tok = alerts.token()
    payload = {"alerts": [{"status": "firing",
                           "labels": {"alertname": "BackupSSDStale", "severity": "warning"},
                           "annotations": {"summary": "last SSD backup 40h ago"}}]}
    assert client.post("/api/alerts/webhook", json=payload).status_code == 401
    assert client.post("/api/alerts/webhook", json=payload,
                       headers={"X-Alert-Token": "wrong"}).status_code == 401
    assert client.post("/api/alerts/webhook", json=payload,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/api/alerts/webhook", json=payload,
                     headers={"Authorization": f"Bearer {tok}"})
    assert ok.status_code == 200 and ok.json() == {"fired": 1, "resolved": 0}
    ok = client.post("/api/alerts/webhook", json=payload, headers={"X-Alert-Token": tok})
    assert ok.status_code == 200 and ok.json() == {"fired": 1, "resolved": 0}
    bad = client.post("/api/alerts/webhook", content="not json",
                      headers={"X-Alert-Token": tok})
    assert bad.status_code == 400


def test_token_endpoints(client, clean_settings):
    shown = client.get("/api/alerts/token")
    assert shown.status_code == 200 and shown.json()["token"] == alerts.token()
    rotated = client.post("/api/alerts/token/rotate").json()["token"]
    assert rotated and alerts.verify(rotated)
