"""WebSocket credentials, log redaction, and the web UI's script hardening.

A WebSocket cannot send an Authorization header, so its credential rides in
the URL, and URLs end up in reverse-proxy access logs. Before 0.23 that was
the 30-day admin token, which also skips the password and the second factor.
Now a client trades the token for a single-use ticket that expires in seconds,
and the server masks credentials in its own log lines.

The web UI renders model output as markdown. A quote inside a link must not
close the href attribute, and the page runs under a policy that allows no
inline script, so one missed escape cannot become code execution.
"""
import json
import logging
import pathlib
import re
import shutil
import subprocess
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import auth, config, main

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def client(clean_settings, monkeypatch):
    monkeypatch.setattr(config, "DEMO", False)
    return TestClient(main.app)


def _bearer():
    return {"Authorization": "Bearer " + auth.issue_token()}


# ----------------------------------------------------------------- tickets

def test_ticket_is_single_use(clean_settings):
    t = auth.issue_ws_ticket()
    assert auth.consume_ws_ticket(t) is True
    assert auth.consume_ws_ticket(t) is False


def test_ticket_expires(clean_settings, monkeypatch):
    t = auth.issue_ws_ticket()
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + auth.WS_TICKET_TTL + 1)
    assert auth.consume_ws_ticket(t) is False


def test_sign_out_everywhere_voids_open_tickets(clean_settings):
    t = auth.issue_ws_ticket()
    config.bump_auth_generation()
    assert auth.consume_ws_ticket(t) is False


@pytest.mark.parametrize("bad", ["", "nope", "a" * 40])
def test_unknown_tickets_fail(bad, clean_settings):
    assert auth.consume_ws_ticket(bad) is False


def test_ticket_store_is_bounded(clean_settings):
    for _ in range(auth._WS_TICKET_MAX + 50):
        auth.issue_ws_ticket()
    assert len(auth._ws_tickets) <= auth._WS_TICKET_MAX


def test_ticket_endpoint_needs_a_token(client):
    assert client.post("/api/ws/ticket").status_code == 401
    r = client.post("/api/ws/ticket", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["ttl"] == auth.WS_TICKET_TTL and len(body["ticket"]) >= 24


def test_demo_hands_out_tickets(clean_settings, monkeypatch):
    """The demo blocks every POST it does not list. Without this entry its chat
    and terminal could not connect at all."""
    monkeypatch.setattr(config, "DEMO", True)
    r = TestClient(main.app).post("/api/ws/ticket", headers=_bearer())
    assert r.status_code == 200


def test_websocket_accepts_a_ticket_once(client):
    ticket = client.post("/api/ws/ticket", headers=_bearer()).json()["ticket"]
    with client.websocket_connect(f"/ws/chat?ticket={ticket}") as ws:
        ws.send_text(json.dumps({"type": "reset"}))
        assert json.loads(ws.receive_text())["type"] == "chat"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/chat?ticket={ticket}") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_websocket_refuses_garbage(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat?ticket=forged") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_legacy_token_still_works_but_can_be_switched_off(client, monkeypatch):
    token = auth.issue_token()
    with client.websocket_connect(f"/ws/chat?token={token}") as ws:
        ws.send_text(json.dumps({"type": "reset"}))
        assert json.loads(ws.receive_text())["type"] == "chat"
    monkeypatch.setattr(auth, "LEGACY_WS_TOKEN", False)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/chat?token={token}") as ws:
            ws.receive_text()


# ----------------------------------------------------------- log redaction

@pytest.mark.parametrize("line,secret", [
    ('172.19.0.3 - "WebSocket /ws/chat?token=eyJleHAi.abc" [accepted]', "eyJleHAi.abc"),
    ("GET /ws/terminal?session=s1&ticket=T0P-s3cret HTTP/1.1", "T0P-s3cret"),
    ("GET /?sso=one-time-code HTTP/1.1", "one-time-code"),
    ("GET /api/auth/oidc/callback?state=x&code=provider-code HTTP/1.1", "provider-code"),
])
def test_redact_url_masks_credentials(line, secret):
    out = auth.redact_url(line)
    assert secret not in out and "***" in out


def test_uvicorn_log_lines_are_masked():
    auth.install_log_redaction()
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                               '%s - "WebSocket %s" [accepted]',
                               ("172.19.0.3:5555", "/ws/chat?token=SECRET.TOKEN"), None)
    for f in logging.getLogger("uvicorn.error").filters:
        f.filter(record)
    assert "SECRET.TOKEN" not in record.getMessage()


def test_redaction_is_installed_once():
    auth.install_log_redaction()
    auth.install_log_redaction()
    filters = [f for f in logging.getLogger("uvicorn.access").filters
               if isinstance(f, auth.RedactCredentials)]
    assert len(filters) == 1


# --------------------------------------------------------- headers + script

def test_security_headers_on_the_page_and_the_api(client):
    for path in ("/", "/api/info"):
        r = client.get(path)
        csp = r.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert "frame-ancestors 'none'" in csp
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("referrer-policy") == "no-referrer"


def test_index_has_no_inline_script():
    """The CSP forbids inline script, so any that sneaks back in simply stops working."""
    html = (ROOT / "web" / "index.html").read_text()
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, f"inline <script> would be blocked by the CSP: {tag}"
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handler attribute"


def test_theme_boot_is_cached_by_the_service_worker():
    sw = (ROOT / "web" / "sw.js").read_text()
    assert "/theme-boot.js" in sw


def _render(markdown: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    src = (ROOT / "web" / "app.js").read_text()
    start = src.index("function renderMarkdown(")
    end = src.index("\nfunction mdDiv(")
    script = src[start:end] + "\nprocess.stdout.write(renderMarkdown(" + json.dumps(markdown) + "));"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.parametrize("payload", [
    '[click](https://a.example/"onmouseover="alert(1))',
    "[x](https://a.example/'onfocus='alert(1)'autofocus=')",
    '[x](https://a.example/"><img src=x onerror=alert(1)>)',
])
def test_markdown_links_cannot_break_out_of_href(payload):
    html = _render(payload)
    # browsers accept an attribute right after a closing quote: href="x"onclick=…
    assert not re.search(r"<a [^>]*[\s\"']on[a-z]+=", html), html
    assert "<img" not in html


def test_markdown_still_renders_links_and_quotes():
    html = _render('He said "hi" — see [docs](https://docs.example.com/a?b=1)')
    assert 'href="https://docs.example.com/a?b=1"' in html
    assert "&quot;hi&quot;" in html
