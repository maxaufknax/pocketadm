"""Single sign-on through an OpenID Connect provider — Authentik, Authelia,
Keycloak, Pocket ID, Zitadel … — as an *additional* way in. The admin password
(and its 2FA) keeps working; SSO never replaces it, so a broken or unreachable
provider can't lock anyone out.

Flow (authorization code + PKCE, confidential client):

  /api/auth/oidc/start     302 to the provider, carrying state, nonce and a
                           PKCE challenge; a cookie ties the attempt to this
                           browser
  /api/auth/oidc/callback  code -> tokens over the back channel, ID-token
                           claims checked, allow-list checked, then 302 to
                           /?sso=<one-time code>
  /api/auth/oidc/claim     the app trades that code for a regular token

The ID token comes straight from the token endpoint over TLS, in a request
authenticated with the client secret, so per OIDC Core 3.1.3.7 (6) TLS server
validation stands in for checking its signature. iss, aud, exp and nonce are
still verified. That is why the issuer has to be https.

Who may sign in is decided twice: by the provider's own access policy and by
the allow-list here (groups or usernames). The allow-list is mandatory. This
app is root on the host, and a provider application without an access policy
lets every account in the directory through. Email is deliberately not
matched: many providers let users edit their own address.
"""
import base64
import hashlib
import ipaddress
import json
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

import httpx

from . import config

CALLBACK_PATH = "/api/auth/oidc/callback"
COOKIE = "pocketadm_oidc"
FLOW_TTL = 600          # seconds to finish signing in at the provider
CODE_TTL = 60           # seconds the app has to claim the one-time code
MAX_PENDING = 32
DISCOVERY_TTL = 3600
HTTP_TIMEOUT = 10.0
CLOCK_SKEW = 120        # leeway for exp / iat against the provider's clock


class SSOError(Exception):
    """A sign-in failure whose message is safe to show the user."""


# ----------------------------------------------------------------- config

def get_config() -> dict | None:
    """The stored provider config, or None when SSO is off (always off in demo)."""
    if config.DEMO:
        return None
    cfg = config.get_oidc()
    if not all(cfg.get(k) for k in ("issuer", "client_id", "client_secret",
                                    "redirect_uri", "allowed")):
        return None
    return cfg


def public_info() -> dict | None:
    """What the sign-in screen may know: just the button label."""
    cfg = get_config()
    return {"label": cfg.get("label") or "SSO"} if cfg else None


def normalize_issuer(value: str) -> str:
    """Accept an issuer URL or its discovery URL; return the issuer."""
    value = (value or "").strip()
    suffix = "/.well-known/openid-configuration"
    if value.endswith(suffix):
        value = value[: -len(suffix)]
    return value


def parse_allowed(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\n", ",").split(",")
    seen, out = set(), []
    for item in value or []:
        item = str(item).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def check_redirect_uri(uri: str) -> str:
    """The callback URL the app will register with the provider. It has to point
    at this app's callback route, over https (plain http only for LAN hosts)."""
    uri = (uri or "").strip()
    parts = urlsplit(uri)
    if parts.path != CALLBACK_PATH or parts.query or parts.fragment or not parts.hostname:
        raise SSOError(f"Redirect URI must end in {CALLBACK_PATH}")
    if parts.scheme == "https":
        return uri
    if parts.scheme == "http" and _is_private(parts.hostname):
        return uri
    raise SSOError("Redirect URI must use https")


def _is_private(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith((".local", ".lan", ".internal", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


# ------------------------------------------------------------- discovery

_discovery: dict[str, tuple[float, dict]] = {}


async def _get_json(url: str, headers: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False) as c:
        r = await c.get(url, headers=headers or {})
    if r.status_code != 200:
        raise SSOError(f"{urlsplit(url).netloc} answered HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise SSOError(f"{urlsplit(url).netloc} did not return JSON") from None


async def _post_form(url: str, data: dict, auth: tuple[str, str] | None) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False) as c:
        r = await c.post(url, data=data, auth=auth,
                         headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body


async def discover(issuer: str, fresh: bool = False) -> dict:
    """Fetch (and cache) the provider's discovery document. The document's own
    issuer must be the one we asked for (OIDC Discovery 4.3), otherwise a
    compromised or misconfigured endpoint could vouch for another provider."""
    issuer = normalize_issuer(issuer)
    if not issuer.startswith("https://"):
        raise SSOError("The issuer URL must start with https://")
    hit = _discovery.get(issuer)
    if hit and not fresh and time.time() - hit[0] < DISCOVERY_TTL:
        return hit[1]
    try:
        doc = await _get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
    except httpx.HTTPError as e:
        raise SSOError(f"Could not reach the provider ({type(e).__name__})") from None
    if str(doc.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
        raise SSOError("The provider reports a different issuer than the one entered")
    for key in ("authorization_endpoint", "token_endpoint"):
        if not str(doc.get(key, "")).startswith("https://"):
            raise SSOError(f"The provider's {key} is missing or not https")
    _discovery[issuer] = (time.time(), doc)
    return doc


# --------------------------------------------------------- pending flows

_pending: dict[str, dict] = {}      # state -> {nonce, verifier, browser, exp}
_codes: dict[str, dict] = {}        # one-time code -> {exp, user}


def _purge() -> None:
    now = time.time()
    for store in (_pending, _codes):
        for key in [k for k, v in store.items() if v["exp"] <= now]:
            store.pop(key, None)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def begin(app: bool = False) -> tuple[str, str]:
    """Start a sign-in. Returns (provider URL to redirect to, browser cookie).

    `app`: started by the iOS app in an ASWebAuthenticationSession, which
    expects the result at pocketadm://sso instead of the web app's /?sso=."""
    cfg = get_config()
    if not cfg:
        raise SSOError("Single sign-on is not set up")
    doc = await discover(cfg["issuer"])
    _purge()
    while len(_pending) >= MAX_PENDING:          # drop the oldest attempt
        _pending.pop(min(_pending, key=lambda k: _pending[k]["exp"]), None)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, browser = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    _pending[state] = {"nonce": nonce, "verifier": verifier, "app": bool(app),
                       "browser": _digest(browser), "exp": time.time() + FLOW_TTL}
    scopes = ["openid", "profile", "email"]
    if "groups" in (doc.get("scopes_supported") or []):
        scopes.append("groups")
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": " ".join(scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    endpoint = doc["authorization_endpoint"]
    sep = "&" if "?" in endpoint else "?"
    return endpoint + sep + urlencode(params), browser


def _decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        raise SSOError("The provider returned an unreadable ID token") from None


def check_id_token(claims: dict, *, issuer: str, client_id: str, nonce: str) -> None:
    now = time.time()
    if claims.get("iss") != issuer:
        raise SSOError("ID token was issued by someone else")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if client_id not in auds:
        raise SSOError("ID token was issued for another application")
    if len(auds) > 1 and claims.get("azp") != client_id:
        raise SSOError("ID token was issued for another application")
    try:
        exp = float(claims["exp"])
    except (KeyError, TypeError, ValueError):
        raise SSOError("ID token has no expiry") from None
    if exp < now - CLOCK_SKEW:
        raise SSOError("ID token has expired — check the server clocks")
    if float(claims.get("iat") or 0) > now + CLOCK_SKEW:
        raise SSOError("ID token is from the future — check the server clocks")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")).encode(),
                                               nonce.encode()):
        raise SSOError("ID token does not belong to this sign-in")
    if not claims.get("sub"):
        raise SSOError("ID token names no user")


def allowed_match(claims: dict, allowed: list[str]) -> str:
    """Return what matched (a group or the username), or "" when nothing did."""
    wanted = {a.lower(): a for a in allowed}
    groups = claims.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    for g in groups:
        if str(g).lower() in wanted:
            return f"group {g}"
    user = str(claims.get("preferred_username") or "")
    if user and user.lower() in wanted:
        return f"user {user}"
    return ""


async def complete(state: str, code: str, browser: str) -> dict:
    """Finish a sign-in from the provider's redirect. Returns the identity."""
    _purge()
    flow = _pending.pop(state or "", None)
    if not flow:
        raise SSOError("This sign-in link has expired — please start again")
    if not browser or not secrets.compare_digest(_digest(browser), flow["browser"]):
        raise SSOError("Sign-in has to finish in the browser it started in")
    if not code:
        raise SSOError("The provider sent no authorization code")
    cfg = get_config()
    if not cfg:
        raise SSOError("Single sign-on is not set up")
    doc = await discover(cfg["issuer"])

    data = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": cfg["redirect_uri"], "code_verifier": flow["verifier"]}
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    basic = None
    if "client_secret_basic" in methods:
        # RFC 6749 2.3.1: form-encode both parts before Basic encoding
        basic = (quote(cfg["client_id"], safe=""), quote(cfg["client_secret"], safe=""))
    else:
        data.update(client_id=cfg["client_id"], client_secret=cfg["client_secret"])
    try:
        status, tokens = await _post_form(doc["token_endpoint"], data, basic)
    except httpx.HTTPError as e:
        raise SSOError(f"Could not reach the provider ({type(e).__name__})") from None
    if status != 200 or "id_token" not in tokens:
        reason = tokens.get("error_description") or tokens.get("error") or f"HTTP {status}"
        raise SSOError(f"The provider refused the sign-in: {str(reason)[:160]}")

    claims = _decode_jwt_payload(tokens["id_token"])
    check_id_token(claims, issuer=doc["issuer"], client_id=cfg["client_id"],
                   nonce=flow["nonce"])

    # Groups often only come from userinfo. Its subject must be the same user.
    if doc.get("userinfo_endpoint", "").startswith("https://") and tokens.get("access_token"):
        try:
            info = await _get_json(doc["userinfo_endpoint"],
                                   {"Authorization": f"Bearer {tokens['access_token']}"})
        except (SSOError, httpx.HTTPError):
            info = {}
        if info.get("sub") == claims["sub"]:
            # the ID token wins wherever it says something; userinfo fills gaps
            claims = {**info, **{k: v for k, v in claims.items() if v not in (None, "", [])}}

    matched = allowed_match(claims, cfg["allowed"])
    who = str(claims.get("preferred_username") or claims.get("email") or claims["sub"])
    if not matched:
        raise SSOError(f"{who} is not allowed to sign in here — "
                       "add the user or one of their groups to the allow-list")
    return {"user": who, "matched": matched}


def flow_is_app(state: str) -> bool:
    """Whether a pending sign-in was started by the iOS app — looked up before
    complete() consumes the attempt, so even a refusal goes back to the app."""
    flow = _pending.get(state or "")
    return bool(flow and flow.get("app"))


def new_login_code(identity: dict) -> str:
    _purge()
    code = secrets.token_urlsafe(24)
    _codes[code] = {**identity, "exp": time.time() + CODE_TTL}
    return code


def claim_login_code(code: str) -> dict | None:
    """The identity the code was minted for — exactly once — else None."""
    _purge()
    return _codes.pop(code or "", None)
