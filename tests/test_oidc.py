"""Single sign-on (OpenID Connect).

SSO is a second door into a root-on-host app, so what it refuses matters more
than what it lets through. Pinned here against a fake provider:

  * it is off unless fully configured, never on in demo, and the client secret
    never leaves the server;
  * a sign-in only completes for the attempt it started, in the browser it
    started in, and only once;
  * the ID token must come from the configured issuer, for this client, for
    this attempt (nonce), and be current;
  * the user has to be on the allow-list by group or username, never by email;
  * PKCE: the verifier sent to the token endpoint matches the challenge;
  * the one-time code turns into a working token exactly once.
"""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from server import auth, config, main, oidc

ISSUER = "https://idp.example.com/application/o/pocketadm/"
REDIRECT = "https://pocket.example.com/api/auth/oidc/callback"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": "https://idp.example.com/application/o/authorize/",
    "token_endpoint": "https://idp.example.com/application/o/token/",
    "userinfo_endpoint": "https://idp.example.com/application/o/userinfo/",
    "scopes_supported": ["openid", "profile", "email"],
    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
}


def _jwt(claims: dict) -> str:
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{seg({'alg': 'RS256', 'typ': 'JWT'})}.{seg(claims)}.c2lnbmF0dXJl"


class FakeIdP:
    """Answers discovery, token and userinfo requests the way a provider does."""

    def __init__(self):
        self.discovery = dict(DISCOVERY)
        self.claims = {}            # overrides for the next ID token
        self.userinfo = None        # None = userinfo knows only the subject
        self.token_status = 200
        self.token_requests = []
        self.nonce = ""

    async def get_json(self, url, headers=None):
        if url == ISSUER.rstrip("/") + "/.well-known/openid-configuration":
            return self.discovery
        if url == DISCOVERY["userinfo_endpoint"]:
            assert headers == {"Authorization": "Bearer at-1"}
            return self.userinfo if self.userinfo is not None else {"sub": "u-1"}
        raise AssertionError("unexpected GET " + url)

    async def post_form(self, url, data, basic):
        self.token_requests.append({"url": url, "data": dict(data), "basic": basic})
        if self.token_status != 200:
            return self.token_status, {"error": "invalid_grant",
                                       "error_description": "code expired"}
        now = time.time()
        claims = {"iss": ISSUER, "aud": "client-1", "sub": "u-1", "exp": now + 300,
                  "iat": now, "nonce": self.nonce, "preferred_username": "max",
                  "email": "max@example.com", "groups": ["admins"]}
        claims.update(self.claims)
        claims = {k: v for k, v in claims.items() if v is not None}
        return 200, {"id_token": _jwt(claims), "access_token": "at-1",
                     "token_type": "Bearer"}


@pytest.fixture
def idp(monkeypatch, clean_settings, tmp_path):
    fake = FakeIdP()
    monkeypatch.setattr(oidc, "_get_json", fake.get_json)
    monkeypatch.setattr(oidc, "_post_form", fake.post_form)
    monkeypatch.setattr(config, "DEMO", False)
    monkeypatch.setattr(auth, "_failed", {})
    monkeypatch.setattr(auth, "_RL_FILE", tmp_path / "attempts.json")
    for store in (oidc._discovery, oidc._pending, oidc._codes):
        store.clear()
    return fake


@pytest.fixture
def client():
    return TestClient(main.app, base_url="https://pocket.example.com")


def _configure(**over):
    cfg = {"issuer": ISSUER, "client_id": "client-1", "client_secret": "s3cret-value",
           "allowed": ["admins"], "label": "Authentik", "redirect_uri": REDIRECT}
    cfg.update(over)
    config.set_oidc(cfg)


def _start(client, idp):
    """Begin a sign-in; returns the authorization request's query parameters."""
    r = client.get("/api/auth/oidc/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(DISCOVERY["authorization_endpoint"] + "?")
    params = {k: v[0] for k, v in parse_qs(urlsplit(loc).query).items()}
    idp.nonce = params["nonce"]
    return params


def _callback(client, **params):
    r = client.get("/api/auth/oidc/callback", params=params, follow_redirects=False)
    assert r.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}


def _authed():
    return {"Authorization": "Bearer " + auth.issue_token()}


# ------------------------------------------------------------ availability

def test_off_until_configured(idp, client):
    assert client.get("/api/info").json()["sso"] is None
    assert client.get("/api/auth/oidc/start", follow_redirects=False).status_code == 404


def test_half_configured_counts_as_off(idp, client):
    _configure(allowed=[])
    assert oidc.get_config() is None
    assert client.get("/api/info").json()["sso"] is None


def test_info_offers_only_the_label(idp, client):
    _configure()
    r = client.get("/api/info")
    assert r.json()["sso"] == {"label": "Authentik"}
    assert "s3cret-value" not in r.text and "client-1" not in r.text


def test_never_in_demo(idp, client, monkeypatch):
    _configure()
    monkeypatch.setattr(config, "DEMO", True)
    assert client.get("/api/info").json()["sso"] is None
    assert client.get("/api/auth/oidc/start", follow_redirects=False).status_code == 404


# --------------------------------------------------------------- the flow

def test_full_sign_in(idp, client):
    _configure()
    params = _start(client, idp)
    assert params["response_type"] == "code"
    assert params["client_id"] == "client-1"
    assert params["redirect_uri"] == REDIRECT
    assert params["scope"].split() == ["openid", "profile", "email"]
    assert params["code_challenge_method"] == "S256"

    back = _callback(client, state=params["state"], code="auth-code-1")
    assert "sso_error" not in back, back
    sent = idp.token_requests[-1]
    assert sent["data"]["code"] == "auth-code-1"
    assert sent["data"]["redirect_uri"] == REDIRECT
    verifier = sent["data"]["code_verifier"]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == params["code_challenge"]          # PKCE holds together

    r = client.post("/api/auth/oidc/claim", json={"code": back["sso"]})
    assert r.status_code == 200
    assert auth.check_token(r.json()["token"]) is True
    # one-time: the same code never works twice
    assert client.post("/api/auth/oidc/claim", json={"code": back["sso"]}).status_code == 401


def test_client_secret_basic_is_preferred(idp, client):
    _configure()
    params = _start(client, idp)
    _callback(client, state=params["state"], code="c")
    sent = idp.token_requests[-1]
    assert sent["basic"] == ("client-1", "s3cret-value")
    assert "client_secret" not in sent["data"]


def test_client_secret_post_when_basic_unsupported(idp, client):
    idp.discovery["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
    _configure()
    params = _start(client, idp)
    _callback(client, state=params["state"], code="c")
    sent = idp.token_requests[-1]
    assert sent["basic"] is None
    assert sent["data"]["client_secret"] == "s3cret-value"


def test_groups_scope_requested_when_offered(idp, client):
    idp.discovery["scopes_supported"] = ["openid", "profile", "email", "groups"]
    _configure()
    assert "groups" in _start(client, idp)["scope"].split()


def test_attempt_is_single_use(idp, client):
    _configure()
    params = _start(client, idp)
    assert "sso" in _callback(client, state=params["state"], code="c")
    replay = _callback(client, state=params["state"], code="c")
    assert "sso" not in replay and "expired" in replay["sso_error"]


def test_unknown_state_refused(idp, client):
    _configure()
    _start(client, idp)
    back = _callback(client, state="forged-state", code="c")
    assert "sso" not in back and "expired" in back["sso_error"]
    assert idp.token_requests == []        # never even asked the provider


def test_must_finish_in_the_same_browser(idp, client):
    _configure()
    params = _start(client, idp)
    other = TestClient(main.app, base_url="https://pocket.example.com")  # no cookie
    back = _callback(other, state=params["state"], code="c")
    assert "sso" not in back and "browser" in back["sso_error"]
    assert idp.token_requests == []


def test_provider_error_is_shown(idp, client):
    _configure()
    params = _start(client, idp)
    back = _callback(client, state=params["state"], error="access_denied",
                     error_description="User declined")
    assert "sso" not in back and "User declined" in back["sso_error"]


def test_token_endpoint_refusal(idp, client):
    _configure()
    idp.token_status = 400
    params = _start(client, idp)
    back = _callback(client, state=params["state"], code="c")
    assert "sso" not in back and "code expired" in back["sso_error"]


# --------------------------------------------------------- ID token checks

@pytest.mark.parametrize("claims, why", [
    ({"iss": "https://evil.example.com/"}, "issued by someone else"),
    ({"aud": "another-client"}, "another application"),
    ({"aud": ["client-1", "another-client"]}, "another application"),   # no azp
    ({"exp": time.time() - 3600}, "expired"),
    ({"iat": time.time() + 3600}, "future"),
    ({"nonce": "replayed-nonce"}, "does not belong"),
    ({"nonce": None}, "does not belong"),
    ({"sub": None}, "names no user"),
])
def test_bad_id_token_refused(idp, client, claims, why):
    _configure()
    params = _start(client, idp)
    idp.claims = claims
    back = _callback(client, state=params["state"], code="c")
    assert "sso" not in back
    assert why in back["sso_error"]


def test_multiple_audiences_with_matching_azp_accepted(idp, client):
    _configure()
    params = _start(client, idp)
    idp.claims = {"aud": ["client-1", "another-client"], "azp": "client-1"}
    assert "sso" in _callback(client, state=params["state"], code="c")


# ---------------------------------------------------------- the allow-list

def test_user_outside_allow_list_refused(idp, client):
    _configure(allowed=["admins"])
    params = _start(client, idp)
    idp.claims = {"groups": ["family"], "preferred_username": "eve"}
    back = _callback(client, state=params["state"], code="c")
    assert "sso" not in back and "eve is not allowed" in back["sso_error"]


def test_username_on_allow_list_accepted(idp, client):
    _configure(allowed=["MAX"])                     # case-insensitive
    params = _start(client, idp)
    idp.claims = {"groups": []}
    assert "sso" in _callback(client, state=params["state"], code="c")


def test_email_never_matches(idp, client):
    # users can often edit their own address at the provider
    _configure(allowed=["max@example.com"])
    params = _start(client, idp)
    idp.claims = {"groups": [], "preferred_username": "someone"}
    assert "sso" not in _callback(client, state=params["state"], code="c")


def test_groups_from_userinfo(idp, client):
    _configure(allowed=["admins"])
    params = _start(client, idp)
    idp.claims = {"groups": None}
    idp.userinfo = {"sub": "u-1", "groups": ["admins"]}
    assert "sso" in _callback(client, state=params["state"], code="c")


def test_userinfo_for_another_subject_ignored(idp, client):
    _configure(allowed=["admins"])
    params = _start(client, idp)
    idp.claims = {"groups": None}
    idp.userinfo = {"sub": "someone-else", "groups": ["admins"]}
    assert "sso" not in _callback(client, state=params["state"], code="c")


def test_id_token_beats_userinfo(idp, client):
    _configure(allowed=["admins"])
    params = _start(client, idp)
    idp.claims = {"groups": ["family"], "preferred_username": "eve"}
    idp.userinfo = {"sub": "u-1", "groups": ["admins"], "preferred_username": "max"}
    assert "sso" not in _callback(client, state=params["state"], code="c")


# --------------------------------------------------------------- settings

def _settings_body(**over):
    body = {"issuer": ISSUER, "client_id": "client-1", "client_secret": "s3cret-value",
            "allowed": "admins, ops", "label": "Authentik", "redirect_uri": REDIRECT}
    body.update(over)
    return body


def test_settings_need_auth(idp, client):
    assert client.get("/api/settings/sso").status_code == 401
    assert client.put("/api/settings/sso", json=_settings_body()).status_code == 401
    assert client.delete("/api/settings/sso").status_code == 401


def test_settings_round_trip_without_the_secret(idp, client):
    r = client.put("/api/settings/sso", json=_settings_body(), headers=_authed())
    assert r.status_code == 200, r.text
    got = client.get("/api/settings/sso", headers=_authed())
    data = got.json()
    assert data["configured"] is True and data["secret_set"] is True
    assert data["allowed"] == ["admins", "ops"]
    assert "s3cret-value" not in got.text


def test_settings_keep_secret_when_left_blank(idp, client):
    client.put("/api/settings/sso", json=_settings_body(), headers=_authed())
    r = client.put("/api/settings/sso", json=_settings_body(client_secret="", label="SSO"),
                   headers=_authed())
    assert r.status_code == 200
    assert config.get_oidc()["client_secret"] == "s3cret-value"


@pytest.mark.parametrize("over, why", [
    ({"issuer": "http://idp.example.com/application/o/pocketadm/"}, "https"),
    ({"redirect_uri": "https://pocket.example.com/elsewhere"}, "must end in"),
    ({"redirect_uri": "http://pocket.example.com/api/auth/oidc/callback"}, "https"),
    ({"allowed": " , "}, "at least one"),
    ({"client_id": " "}, "Client ID"),
])
def test_settings_validation(idp, client, over, why):
    r = client.put("/api/settings/sso", json=_settings_body(**over), headers=_authed())
    assert r.status_code == 400
    assert why in r.json()["detail"]
    assert config.get_oidc() == {}


def test_settings_accept_lan_http_redirect(idp, client):
    body = _settings_body(redirect_uri="http://192.168.1.20:8090/api/auth/oidc/callback")
    assert client.put("/api/settings/sso", json=body, headers=_authed()).status_code == 200


def test_settings_accept_discovery_url(idp, client):
    body = _settings_body(issuer=ISSUER + ".well-known/openid-configuration")
    assert client.put("/api/settings/sso", json=body, headers=_authed()).status_code == 200
    assert config.get_oidc()["issuer"] == ISSUER


def test_settings_refuse_issuer_mismatch(idp, client):
    idp.discovery["issuer"] = "https://other.example.com/"
    r = client.put("/api/settings/sso", json=_settings_body(), headers=_authed())
    assert r.status_code == 400 and "different issuer" in r.json()["detail"]


def test_settings_remove(idp, client):
    _configure()
    assert client.delete("/api/settings/sso", headers=_authed()).status_code == 200
    assert oidc.get_config() is None
    assert client.get("/api/info").json()["sso"] is None


# ------------------------------------------------------------ the iOS app

def test_app_sign_in_returns_to_the_app(idp, client):
    """The iOS app starts with ?client=app inside an ASWebAuthenticationSession;
    the result comes back at pocketadm://sso, where the session catches it."""
    _configure()
    r = client.get("/api/auth/oidc/start?client=app", follow_redirects=False)
    assert r.status_code == 302
    params = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    idp.nonce = params["nonce"]
    back = client.get("/api/auth/oidc/callback", params={"state": params["state"], "code": "c-1"},
                      follow_redirects=False)
    loc = back.headers["location"]
    assert loc.startswith("pocketadm://sso?code=")
    code = parse_qs(urlsplit(loc).query)["code"][0]
    token = client.post("/api/auth/oidc/claim", json={"code": code}).json()["token"]
    assert auth.check_token(token)


def test_app_refusal_also_returns_to_the_app(idp, client):
    _configure(allowed=["somebody-else"])
    r = client.get("/api/auth/oidc/start?client=app", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    idp.nonce = params["nonce"]
    loc = client.get("/api/auth/oidc/callback", params={"state": params["state"], "code": "c-1"},
                     follow_redirects=False).headers["location"]
    assert loc.startswith("pocketadm://sso?error=")
    assert "not allowed" in parse_qs(urlsplit(loc).query)["error"][0]


def test_browser_sign_in_never_goes_to_the_app_scheme(idp, client):
    _configure()
    params = _start(client, idp)
    back = _callback(client, state=params["state"], code="c-1")
    assert "sso" in back
