"""Auth: token signing/expiry/revocation, password, TOTP, and rate limiting.

This module is the only thing standing between the open internet and a
root-on-host shell (see hostrun.py), so its guarantees are worth pinning:

  * a token verifies iff it was signed by *this* server's secret, is unexpired,
    and carries the current auth generation;
  * bumping the generation (password change / "sign out everywhere") invalidates
    every previously issued token;
  * TOTP follows RFC 6238;
  * the login limiter trips after too many failures (and stays loose in demo).
"""
import base64
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server import auth, config


# --------------------------------------------------------------------- tokens

def test_issued_token_verifies(clean_settings):
    token = auth.issue_token()
    assert auth.check_token(token) is True


@pytest.mark.parametrize("bad", ["", "garbage", "a.b.c", "not-a-token",
                                 "eyJ.tampered"])
def test_malformed_token_rejected(bad, clean_settings):
    assert auth.check_token(bad) is False


def test_tampered_payload_rejected(clean_settings):
    token = auth.issue_token()
    p64, s64 = token.split(".")
    # flip a character in the signature -> signature check must fail
    flipped = s64[:-1] + ("A" if s64[-1] != "A" else "B")
    assert auth.check_token(f"{p64}.{flipped}") is False


def test_expired_token_rejected(clean_settings, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_TTL", -10)  # already expired at issue time
    token = auth.issue_token()
    assert auth.check_token(token) is False


def test_token_signed_with_other_secret_rejected(clean_settings, monkeypatch):
    token = auth.issue_token()
    assert auth.check_token(token) is True
    monkeypatch.setattr(config, "get_secret", lambda: b"\x00" * 32)
    assert auth.check_token(token) is False


def test_generation_bump_invalidates_old_tokens(clean_settings):
    token = auth.issue_token()
    assert auth.check_token(token) is True
    config.bump_auth_generation()
    assert auth.check_token(token) is False


def test_revoke_all_sessions(clean_settings):
    old = auth.issue_token()
    fresh = auth.revoke_all_sessions()
    assert auth.check_token(old) is False   # everyone else is signed out
    assert auth.check_token(fresh) is True   # caller gets a working token back


# ------------------------------------------------------------------- password

def test_set_and_verify_password(clean_settings):
    auth.set_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple") is True
    assert auth.verify_password("wrong") is False


def test_password_change_signs_out_existing_sessions(clean_settings):
    token = auth.issue_token()
    auth.set_password("new-password")  # bumps generation as a side effect
    assert auth.check_token(token) is False


def test_bootstrap_honours_env_password(clean_settings, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "from-env-123")
    auth.bootstrap_password()
    assert auth.verify_password("from-env-123") is True
    assert auth.verify_password("nope") is False


# ----------------------------------------------------------------------- TOTP

def test_totp_matches_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, secret = ASCII "12345678901234567890".
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth._totp_code(secret, 59) == "287082"
    assert auth._totp_code(secret, 1111111109) == "081804"


def test_verify_totp_code_accepts_current_and_adjacent_window():
    secret = auth.new_totp_secret()
    now = time.time()
    assert auth.verify_totp_code(secret, auth._totp_code(secret, now)) is True
    # window=1 -> the previous 30s step is still accepted (clock skew tolerance)
    assert auth.verify_totp_code(secret, auth._totp_code(secret, now - 30)) is True
    # two steps out is rejected
    assert auth.verify_totp_code(secret, auth._totp_code(secret, now - 90)) is False


@pytest.mark.parametrize("code", ["", "abc", "12", "notanumber"])
def test_verify_totp_code_rejects_junk(code):
    secret = auth.new_totp_secret()
    assert auth.verify_totp_code(secret, code) is False


def test_enable_totp_requires_matching_code(clean_settings):
    secret = auth.new_totp_secret()
    assert auth.enable_totp(secret, "000000") is False   # wrong code -> not enabled
    assert auth.totp_enabled() is False
    good = auth._totp_code(secret, time.time())
    assert auth.enable_totp(secret, good) is True
    assert auth.totp_enabled() is True
    auth.disable_totp()
    assert auth.totp_enabled() is False


def test_provisioning_uri_uses_product_issuer():
    # End users see this string in their authenticator app; it must be the
    # external product name, never the internal "Helmsman". (See docs/BRANDING.md)
    uri = auth.provisioning_uri("SECRET", "my-server")
    assert "issuer=PocketADM" in uri
    assert uri.startswith("otpauth://totp/PocketADM:")
    assert "Helmsman" not in uri


# --------------------------------------------------------------- rate limiting

@pytest.fixture
def fresh_limiter(monkeypatch):
    """Isolate the module-global failure map and force non-demo mode."""
    monkeypatch.setattr(auth, "_failed", {})
    monkeypatch.setattr(config, "DEMO", False)
    return "203.0.113.7"


def test_rate_limit_trips_after_soft_max(fresh_limiter):
    ip = fresh_limiter
    for _ in range(auth.SOFT_MAX):
        auth.record_failure(ip)
    with pytest.raises(HTTPException) as exc:
        auth.rate_limit(ip)
    assert exc.value.status_code == 429


def test_rate_limit_allows_fresh_ip(fresh_limiter):
    auth.rate_limit("198.51.100.42")  # no failures recorded -> must not raise


def test_rate_limit_lenient_in_demo(monkeypatch):
    monkeypatch.setattr(auth, "_failed", {})
    monkeypatch.setattr(config, "DEMO", True)
    ip = "192.0.2.99"
    for _ in range(auth.SOFT_MAX + 3):   # would trip the non-demo soft limit
        auth.record_failure(ip)
    auth.rate_limit(ip)  # demo tolerates far more (DEMO_MAX) -> must not raise


# --------------------------------------------------------------- HTTP dependency

def _fake_request(*, header="", query=None):
    return SimpleNamespace(
        headers={"authorization": header} if header else {},
        query_params=query or {},
    )


def test_require_auth_accepts_valid_bearer(clean_settings):
    token = auth.issue_token()
    # headers.get is what require_auth calls; SimpleNamespace dict provides it
    req = _fake_request(header=f"Bearer {token}")
    assert auth.require_auth(req) is None  # no exception == authorised


def test_require_auth_accepts_token_query_param(clean_settings):
    token = auth.issue_token()
    req = _fake_request(query={"token": token})
    assert auth.require_auth(req) is None


def test_require_auth_rejects_missing_token(clean_settings):
    with pytest.raises(HTTPException) as exc:
        auth.require_auth(_fake_request())
    assert exc.value.status_code == 401
