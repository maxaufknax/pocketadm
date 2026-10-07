"""client-swift/tools/asc_release.py — the release workflow's last step.

It runs inside Codemagic against the real App Store Connect, where a mistake
is expensive: a version pulled out of review loses its queue position, and a
submission nobody meant to send is public. So the script is run here against a
simulated App Store Connect that records every call, and the tests pin what
must never happen (submitting, touching a version in review) as well as the
order that must hold (version string before build).
"""
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).parent.parent
SCRIPT = ROOT / "client-swift" / "tools" / "asc_release.py"
TEXTS = ROOT / "client-swift" / "AppStore"


@pytest.fixture
def mod(monkeypatch):
    # the script imports PyJWT; the simulated API never checks the token
    monkeypatch.setitem(sys.modules, "jwt", types.SimpleNamespace(encode=lambda *a, **k: "t"))
    spec = importlib.util.spec_from_file_location("asc_release", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    for k in ("APP_STORE_CONNECT_ISSUER_ID", "APP_STORE_CONNECT_KEY_IDENTIFIER",
              "APP_STORE_CONNECT_PRIVATE_KEY"):
        monkeypatch.setenv(k, "x")
    return m


class FakeASC:
    def __init__(self, versions, review_detail=True, build_states=("PROCESSING", "VALID")):
        self.versions = versions
        self.review_detail = review_detail
        self.build_states = list(build_states)
        self.calls = []
        self.locales = ["en-US", "de-DE", "fr-FR"]

    def call(self, method, path, body=None, params=None, ok404=False):
        self.calls.append((method, path, body))
        if path == "/v1/apps":
            return {"data": [{"id": "APP"}]}
        if path == "/v1/builds":
            state = self.build_states.pop(0) if len(self.build_states) > 1 else self.build_states[0]
            return {"data": [{"id": "BUILD", "attributes": {"processingState": state}}]}
        if path == "/v1/apps/APP/appStoreVersions":
            if params and params.get("filter[appStoreState]") == "READY_FOR_SALE":
                return {"data": [v for v in self.versions if v["attributes"]["appStoreState"] == "READY_FOR_SALE"]}
            return {"data": self.versions}
        if path == "/v1/appStoreVersions" and method == "POST":
            return {"data": {"id": "NEWVER", "attributes": body["data"]["attributes"]}}
        if path.endswith("/appStoreVersionLocalizations"):
            return {"data": [{"id": f"L-{l}", "attributes": {"locale": l}} for l in self.locales]}
        if path.endswith("/appStoreReviewDetail"):
            if "LIVE" in path:
                return {"data": {"id": "RD-LIVE", "attributes": {
                    "contactFirstName": "A", "contactLastName": "B",
                    "contactPhone": "+49", "contactEmail": "a@example.com"}}}
            return {"data": {"id": "RD"}} if self.review_detail else None
        return {}

    def paths(self, method=None):
        return [p for m, p, _ in self.calls if method is None or m == method]


def run(mod, fake, monkeypatch, *extra):
    monkeypatch.setattr(mod.ASC, "call", lambda self, *a, **k: fake.call(*a, **k))
    monkeypatch.setattr(sys, "argv", ["asc_release.py", "--bundle-id", "de.maxaufknax.pocketadm",
                                      "--version", "2.0.0", "--build-number", "202610071200",
                                      "--texts", str(TEXTS), *extra])
    mod.main()


LIVE = {"id": "LIVE", "attributes": {"versionString": "1.0.1", "appStoreState": "READY_FOR_SALE"}}


def test_creates_the_version_attaches_the_build_and_never_submits(mod, monkeypatch):
    fake = FakeASC([LIVE])
    run(mod, fake, monkeypatch)
    assert ("POST", "/v1/appStoreVersions") in [(m, p) for m, p, _ in fake.calls]
    assert "/v1/appStoreVersions/NEWVER/relationships/build" in fake.paths("PATCH")
    whats_new = {p.rsplit("/", 1)[1]: b["data"]["attributes"]["whatsNew"]
                 for m, p, b in fake.calls if "/appStoreVersionLocalizations/" in p}
    assert set(whats_new) == {"L-en-US", "L-de-DE", "L-fr-FR"}
    assert whats_new["L-de-DE"].startswith("PocketADM 2.0 ist")
    assert whats_new["L-fr-FR"] == whats_new["L-en-US"], "a locale without text gets the English one"
    assert not any("reviewSubmission" in p for p in fake.paths()), "the script must never submit"


def test_review_notes_carry_the_demo_account(mod, monkeypatch):
    fake = FakeASC([LIVE])
    run(mod, fake, monkeypatch)
    body = next(b for m, p, b in fake.calls if p == "/v1/appStoreReviewDetails/RD")
    attrs = body["data"]["attributes"]
    assert attrs["demoAccountName"] == "demo" and attrs["demoAccountPassword"] == "demo"
    assert "Try the live demo" in attrs["notes"]


def test_missing_review_details_copy_the_live_contact(mod, monkeypatch):
    fake = FakeASC([LIVE], review_detail=False)
    run(mod, fake, monkeypatch)
    post = next(b for m, p, b in fake.calls if m == "POST" and p == "/v1/appStoreReviewDetails")
    assert post["data"]["attributes"]["contactPhone"] == "+49"


@pytest.mark.parametrize("state", ["WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_DEVELOPER_RELEASE"])
def test_a_version_in_flight_is_never_touched(mod, monkeypatch, state):
    fake = FakeASC([LIVE, {"id": "V", "attributes": {"versionString": "1.0.2", "appStoreState": state}}])
    with pytest.raises(SystemExit):
        run(mod, fake, monkeypatch)
    assert not fake.paths("PATCH") and not fake.paths("POST")


def test_an_existing_draft_is_renamed_before_the_build_is_attached(mod, monkeypatch):
    draft = {"id": "DRAFT", "attributes": {"versionString": "1.0.2", "appStoreState": "PREPARE_FOR_SUBMISSION"}}
    fake = FakeASC([LIVE, draft])
    run(mod, fake, monkeypatch)
    patches = fake.paths("PATCH")
    assert patches.index("/v1/appStoreVersions/DRAFT") < patches.index(
        "/v1/appStoreVersions/DRAFT/relationships/build"), "version string first, then the build"
    assert not any(m == "POST" and p == "/v1/appStoreVersions" for m, p, _ in fake.calls)


def test_a_rejected_build_stops_with_a_reason(mod, monkeypatch):
    fake = FakeASC([LIVE], build_states=("INVALID",))
    with pytest.raises(SystemExit) as exc:
        run(mod, fake, monkeypatch)
    assert "INVALID" in str(exc.value)
    assert not fake.paths("PATCH")


def test_release_texts_exist_and_fit():
    for name in ("whats-new/en-US.txt", "whats-new/de-DE.txt", "review-notes.txt"):
        text = (TEXTS / name).read_text(encoding="utf-8")
        assert 100 < len(text) <= 4000, name
    notes = (TEXTS / "review-notes.txt").read_text()
    assert "demo.pocketadm.com" in notes


# ------------------------------------------------------------------ screenshots

class ShotASC(FakeASC):
    def __init__(self, *a, sets=("APP_IPHONE_65", "APP_IPHONE_67"), **k):
        super().__init__(*a, **k)
        self.sets = sets
        self.locales = ["en-US", "de-DE"]
        self.n = 0

    def call(self, method, path, body=None, params=None, ok404=False):
        if path.endswith("/appScreenshotSets") and method == "GET":
            self.calls.append((method, path, body))
            loc = path.split("/")[3]
            return {"data": [{"id": f"SET-{loc}-{t}", "attributes": {"screenshotDisplayType": t}}
                             for t in self.sets]}
        if path.endswith("/appScreenshots") and method == "GET":
            self.calls.append((method, path, body))
            return {"data": [{"id": "OLD1"}, {"id": "OLD2"}]}
        if path == "/v1/appScreenshots" and method == "POST":
            self.calls.append((method, path, body))
            self.n += 1
            return {"data": {"id": f"NEW{self.n}", "attributes": {"uploadOperations": [
                {"method": "PUT", "url": "https://upload.example/x", "offset": 0, "length": 3,
                 "requestHeaders": [{"name": "Content-Type", "value": "image/png"}]}]}}}
        if path.startswith("/v1/appScreenshots/NEW") and method == "GET":
            return {"data": {"attributes": {"assetDeliveryState": {"state": "COMPLETE"}}}}
        return super().call(method, path, body, params, ok404)


def test_screenshots_are_replaced_new_first_old_after(mod, monkeypatch, tmp_path):
    for locale in ("en-US", "de-DE"):
        (tmp_path / locale).mkdir()
        for n in (1, 2):
            (tmp_path / locale / f"0{n}-x.png").write_bytes(b"png")
    puts = []
    monkeypatch.setattr(mod, "put_bytes", lambda op, blob: puts.append((op["url"], blob[:op["length"]])))
    fake = ShotASC([LIVE])
    run(mod, fake, monkeypatch, "--screenshots", str(tmp_path))
    seq = [(m, p) for m, p, _ in fake.calls]
    # per set (each starts with listing its current screenshots): every upload
    # comes before the first delete, so a set is never empty
    starts = [i for i, (m, p) in enumerate(seq) if m == "GET" and p.endswith("/appScreenshots")]
    assert len(starts) == 2
    for begin, end in zip(starts, starts[1:] + [len(seq)]):
        block = seq[begin:end]
        uploads = [i for i, x in enumerate(block) if x == ("POST", "/v1/appScreenshots")]
        deletes = [i for i, x in enumerate(block) if x[0] == "DELETE" and x[1].startswith("/v1/appScreenshots/")]
        assert len(uploads) == 2 and len(deletes) == 2
        assert max(uploads) < min(deletes), "upload first, delete after"
    assert len(puts) == 4
    checks = [b for m, p, b in fake.calls if m == "PATCH" and p.startswith("/v1/appScreenshots/NEW")]
    assert all(c["data"]["attributes"]["uploaded"] for c in checks)
    # the other iPhone size would keep showing the old app on big phones
    assert ("DELETE", "/v1/appScreenshotSets/SET-L-en-US-APP_IPHONE_67") in seq
    assert not any("reviewSubmission" in p for _, p in seq)


def test_store_shots_compositor_writes_both_languages(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    raw = tmp_path / "raw"
    raw.mkdir()
    for tab in ("dashboard", "assistant", "containers", "terminal", "more"):
        Image.new("RGB", (1320, 2868), (20, 30, 40)).save(raw / f"raw-{tab}.png")
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "client-swift" / "tools" / "store-shots.py"),
                          str(raw), str(tmp_path / "out")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    for locale in ("en-US", "de-DE"):
        files = sorted((tmp_path / "out" / locale).glob("*.png"))
        assert [f.name for f in files][0] == "01-dashboard.png" and len(files) == 5
        assert Image.open(files[0]).size == (1284, 2778)
