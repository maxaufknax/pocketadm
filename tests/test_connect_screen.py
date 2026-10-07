"""The cold-start Connect screen must always offer a route that works.

PocketADM is a client for a server the user runs. That makes the first screen
unusually load-bearing: someone who has not installed anything yet — an App
Store reviewer, or anyone evaluating the app — can otherwise reach a screen
whose every button demands a server they do not have. The demo entry is the
one path on that screen that needs nothing but the internet, so it is pinned
here, together with the thing that goes stale silently: the address printed in
the App Review notes has to be the address the button actually dials.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
INDEX = (ROOT / "web" / "index.html").read_text()
APP_JS = (ROOT / "web" / "app.js").read_text()
APPSTORE = (ROOT / "client" / "APPSTORE.md").read_text()

DEMO_SERVER = re.search(r'const DEMO_SERVER = "([^"]+)"', APP_JS)


def test_connect_screen_offers_the_demo():
    assert 'id="connect-demo"' in INDEX, "the Connect screen lost its one-tap demo entry"
    assert re.search(r'\$\("#connect-demo"\)\??\.addEventListener', APP_JS), (
        "#connect-demo exists in the markup but nothing listens to it"
    )


def test_demo_server_is_configured():
    assert DEMO_SERVER, "app.js no longer defines DEMO_SERVER"
    assert DEMO_SERVER.group(1).startswith("https://"), "the demo must be reached over https"


def test_review_notes_name_the_same_demo_server():
    """A renamed demo host would silently strand every future App Review."""
    host = DEMO_SERVER.group(1)
    assert host in APPSTORE, (
        f"client/APPSTORE.md does not mention {host}. The App Review notes tell "
        f"the reviewer where the demo lives; if the app dials somewhere else, "
        f"the reviewer follows instructions into a dead end."
    )


def test_demo_failure_leaves_the_button_usable():
    """A dead demo must not strand the user on a spinner."""
    fn = re.search(r"async function connectDemo\(.*?\n\}", APP_JS, re.S)
    assert fn, "connectDemo() is gone"
    body = fn.group(0)
    assert "btn.disabled = false" in body, (
        "connectDemo() must re-enable its button when the demo is unreachable"
    )


@pytest.mark.parametrize("entry", ["connect-add", "connect-scan", "connect-ssh", "connect-demo"])
def test_every_connect_button_is_wired(entry):
    assert f'id="{entry}"' in INDEX
    assert f'#{entry}"' in APP_JS, f"#{entry} has no handler — it would be a dead button"


def test_install_command_is_copyable():
    """Selecting a wrapped shell command by hand on a phone is the friction
    that stops someone standing up their first server."""
    assert "wireCopyableCommands" in APP_JS
    assert ".install-hint pre" in APP_JS, "the copy helper no longer matches the installer blocks"


# ------------------------------------------------------------------ iOS app (2.0+)

NATIVE_STATE = (ROOT / "client-swift" / "App" / "Core" / "AppState.swift").read_text()
NATIVE_CONNECT = (ROOT / "client-swift" / "App" / "Features" / "ConnectView.swift").read_text()
REVIEW_NOTES = (ROOT / "client-swift" / "AppStore" / "review-notes.txt").read_text()


def test_native_app_offers_the_demo_on_its_connect_screen():
    assert 'Label("Try the live demo"' in NATIVE_CONNECT
    assert "app.openDemo()" in NATIVE_CONNECT


def test_native_demo_server_is_the_one_in_the_review_notes():
    """The release workflow writes review-notes.txt into App Store Connect; the
    button has to dial exactly that server, or App Review meets a dead end."""
    m = re.search(r'static let demoServer = URL\(string: "([^"]+)"\)', NATIVE_STATE)
    assert m, "AppState.swift no longer defines demoServer"
    host = m.group(1).removeprefix("https://")
    assert m.group(1).startswith("https://")
    assert host in REVIEW_NOTES
    assert DEMO_SERVER.group(1) == m.group(1), "web and iOS app must use the same demo server"
