"""Integrations: credentials are injected server-side and never leak out.

The whole point of this module is that the agent can call a third-party API
*without ever seeing the token*: PocketADM stores the secret and attaches the
right auth header itself. These tests pin two things:

  1. the correct auth scheme/header is produced per provider (injection works);
  2. the secret never escapes — not into the public listing, the agent prompt,
     or an outbound request's URL/body.
"""
import pytest

from server import config, integrations


@pytest.fixture
def store(clean_settings):
    """A clean integrations store backed by the isolated settings dict."""
    return integrations._store()


# --------------------------------------------------- per-provider header injection

@pytest.mark.parametrize("type_,secret,expect_header,expect_value", [
    ("desec",      "tok_desec",  "Authorization", "Token tok_desec"),
    ("cloudflare", "cf_secret",  "Authorization", "Bearer cf_secret"),
    ("godaddy",    "k:s",        "Authorization", "sso-key k:s"),
    ("ionos",      "ion_key",    "X-API-Key",     "ion_key"),
])
def test_builtin_provider_injects_expected_header(store, type_, secret,
                                                  expect_header, expect_value):
    integrations.save(name="acct", type_=type_, secret=secret)
    _entry, base, headers = integrations._resolve("acct")
    assert base == integrations.TYPES[type_]["base_url"]
    assert headers.get(expect_header) == expect_value


def test_generic_integration_uses_custom_header(store):
    integrations.save(name="grafana", type_="generic", secret="glsa_xyz",
                      base_url="http://grafana.local/api",
                      auth_header_name="X-Grafana-Token")
    _entry, base, headers = integrations._resolve("grafana")
    assert base == "http://grafana.local/api"
    assert headers == {"X-Grafana-Token": "glsa_xyz"}


def test_generic_defaults_to_authorization_header(store):
    integrations.save(name="thing", type_="generic", secret="abc123",
                      base_url="https://api.thing.io")
    _entry, _base, headers = integrations._resolve("thing")
    assert headers == {"Authorization": "abc123"}


# ---------------------------------------------------------- no secret ever leaks

def test_list_public_masks_the_secret(store):
    integrations.save(name="cf", type_="cloudflare", secret="SUPERSECRET")
    pub = integrations.list_public()
    assert len(pub) == 1
    item = pub[0]
    assert item["has_secret"] is True
    assert item["secret_len"] == len("SUPERSECRET")
    # the raw secret must not appear anywhere in the public projection
    assert "secret" not in item
    assert "SUPERSECRET" not in repr(pub)


def test_prompt_section_lists_without_secret(store):
    integrations.save(name="cf", type_="cloudflare", secret="SUPERSECRET",
                      note="prod DNS")
    section = integrations.prompt_section()
    assert "cf" in section and "prod DNS" in section
    assert "SUPERSECRET" not in section


def test_prompt_section_empty_when_nothing_connected(store):
    assert integrations.prompt_section() == ""


def test_disabled_integration_is_hidden_from_prompt(store):
    integrations.save(name="cf", type_="cloudflare", secret="s", enabled=False)
    assert integrations.prompt_section() == ""


# --------------------------------------------------------------- save/resolve rules

def test_save_rejects_bad_name(store):
    with pytest.raises(ValueError):
        integrations.save(name="bad name!", type_="cloudflare", secret="s")


def test_save_rejects_unknown_type(store):
    with pytest.raises(ValueError):
        integrations.save(name="x", type_="nope", secret="s")


def test_generic_requires_http_base_url(store):
    with pytest.raises(ValueError):
        integrations.save(name="x", type_="generic", secret="s",
                          base_url="ftp://nope")


def test_save_requires_a_secret(store):
    with pytest.raises(ValueError):
        integrations.save(name="x", type_="cloudflare", secret="")


def test_secret_is_preserved_when_updated_without_one(store):
    integrations.save(name="cf", type_="cloudflare", secret="original")
    # editing the note without re-entering the secret keeps the stored one
    integrations.save(name="cf", type_="cloudflare", secret="", note="edited")
    _entry, _base, headers = integrations._resolve("cf")
    assert headers["Authorization"] == "Bearer original"


def test_resolve_unknown_raises(store):
    with pytest.raises(ValueError):
        integrations._resolve("ghost")


def test_resolve_disabled_raises(store):
    integrations.save(name="cf", type_="cloudflare", secret="s")
    integrations.set_enabled("cf", False)
    with pytest.raises(ValueError):
        integrations._resolve("cf")


def test_remove(store):
    integrations.save(name="cf", type_="cloudflare", secret="s")
    integrations.remove("cf")
    assert integrations.list_public() == []


# ------------------------------------------------ outbound request carries the header

class _FakeResponse:
    def __init__(self):
        self.status_code = 200
        self.text = "ok"


class _FakeClient:
    """Records the kwargs of the single request the module makes."""
    last = None

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def request(self, method, url, **kwargs):
        _FakeClient.last = {"method": method, "url": url, **kwargs}
        return _FakeResponse()


def test_request_injects_header_and_hides_secret_from_url(store, monkeypatch):
    import asyncio

    integrations.save(name="cf", type_="cloudflare", secret="SUPERSECRET")
    monkeypatch.setattr(integrations.httpx, "AsyncClient", _FakeClient)

    out = asyncio.run(integrations.request("cf", "GET", "zones", params={"page": 1}))

    sent = _FakeClient.last
    assert sent["method"] == "GET"
    assert sent["headers"]["Authorization"] == "Bearer SUPERSECRET"
    assert sent["headers"]["User-Agent"] == "Helmsman-Agent/1.0"
    # the secret is a header, never smuggled into the URL or query
    assert "SUPERSECRET" not in sent["url"]
    assert sent["url"].endswith("/zones")
    assert out.startswith("[200]")
