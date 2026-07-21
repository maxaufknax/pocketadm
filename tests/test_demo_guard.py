"""Demo-guard middleware: a demo instance is a read-only public playground.

In demo mode every state-changing HTTP method must be blocked with 403, except
a tiny allow-list (login, marking notifications seen). Non-demo instances must
let those same requests through to the normal auth/handler path. The guard reads
``config.DEMO`` per-request, so we flip it with monkeypatch rather than
re-importing the whole app.
"""
import pytest
from starlette.testclient import TestClient

from server import config, main


@pytest.fixture
def client():
    return TestClient(main.app)


# --------------------------------------------------------------- demo mode on

def test_demo_blocks_post(monkeypatch, client):
    monkeypatch.setattr(config, "DEMO", True)
    r = client.post("/api/settings/ai", json={})
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_demo_blocks_all_mutating_methods(monkeypatch, client, method):
    monkeypatch.setattr(config, "DEMO", True)
    r = client.request(method, "/api/settings/ai")
    assert r.status_code == 403


def test_demo_allows_get(monkeypatch, client):
    monkeypatch.setattr(config, "DEMO", True)
    # GET is never blocked by the guard; /api/info is public and always 200.
    r = client.get("/api/info")
    assert r.status_code == 200
    assert r.json().get("demo") is True


def test_demo_allowlist_lets_login_through(monkeypatch, client):
    monkeypatch.setattr(config, "DEMO", True)
    # /api/login is on the allow-list: the guard must NOT 403 it. It still runs
    # the real handler (which rejects a bad password), so anything-but-403 proves
    # the guard let it pass.
    r = client.post("/api/login", json={"password": "definitely-wrong"})
    assert r.status_code != 403


# -------------------------------------------------------------- demo mode off

def test_non_demo_does_not_block_mutations(monkeypatch, client):
    monkeypatch.setattr(config, "DEMO", False)
    # A normal instance must not 403 on method alone; without a token the request
    # reaches auth and comes back 401 — never a demo-403.
    r = client.post("/api/settings/ai", json={})
    assert r.status_code == 401
    assert r.status_code != 403
