"""The pairing QR scanner must never be able to become a dead button again.

App Review rejected 1.0.0 twice on Guideline 2.1(a) with the same sentence:
"There was no response when we tapped on scan again. No purpose string
permission modal was prompted." Both halves have a mechanical cause, and both
are pinned here because neither can be caught by running the Python server:

1. **The plugin's argument contract.** ``@capacitor/barcode-scanner`` decodes
   the call on iOS into ``OSBarcodeScanArgumentsModel`` with plain
   ``container.decode(...)`` — *not* ``decodeIfPresent`` — for scanInstructions,
   scanButton, cameraDirection and scanOrientation. Its npm wrapper class fills
   those defaults in before they reach the bridge; this app has no bundler and
   calls the bridge proxy directly, so every key has to be sent by hand. Omit
   one and JSONDecoder throws, the call rejects with "Error decoding scan
   arguments" in microseconds, and neither the camera nor its permission prompt
   ever appear.

2. **The purpose strings.** ``ios-configure.sh`` writes them into the
   Capacitor-generated Info.plist on the build VM. Every PlistBuddy call there
   is error-swallowing by necessity (Add fails when a key exists, Set when it
   does not), so the script has to *read back* what it wrote and fail the build
   otherwise — a silently missing NSCameraUsageDescription is a camera that
   never opens.

3. **A second route to the camera.** The native plugin is third-party code we
   cannot test from CI, so app.js must also be able to reach the camera through
   getUserMedia + the vendored decoder. That path is what makes a broken plugin
   a degraded experience rather than a rejected binary.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent
NATIVE_JS = (ROOT / "web" / "native.js").read_text()
APP_JS = (ROOT / "web" / "app.js").read_text()
IOS_CONFIGURE = (ROOT / "client" / "scripts" / "ios-configure.sh").read_text()

# The scanBarcode({...}) object literal that goes over the bridge.
_SCAN_CALL = re.search(
    r"scanBarcode\(\{(.*?)\}\)", NATIVE_JS, re.S
)


def test_scan_call_exists():
    assert _SCAN_CALL, "native.js no longer calls CapacitorBarcodeScanner.scanBarcode"


# key -> regex the *value* must match, mirroring the Swift decoder's types.
REQUIRED_SCAN_ARGS = {
    "scanInstructions": r'".+"',      # String
    "scanButton": r"(true|false)",    # Bool
    "scanText": r'".+"',              # String — decoded whenever scanButton is true
    "cameraDirection": r"\d+",        # Int  (1 = back)
    "scanOrientation": r"\d+",        # Int  (1 = portrait)
}


@pytest.mark.parametrize("key,value_re", sorted(REQUIRED_SCAN_ARGS.items()))
def test_every_decoded_scanner_argument_is_sent(key, value_re):
    body = _SCAN_CALL.group(1)
    m = re.search(rf"\b{key}\s*:\s*({value_re})\s*[,}}]", body)
    assert m, (
        f"native.js does not send {key!r} to scanBarcode with a {value_re} value. "
        f"The iOS plugin decodes it unconditionally — leaving it out makes the "
        f"call reject with 'Error decoding scan arguments' before the camera "
        f"or its permission prompt appear."
    )


def test_scanner_errors_are_reported_not_swallowed():
    """app.js can only fall back if native.js tells it *why* a scan failed."""
    for kind in ("denied", "cancelled", "unavailable"):
        assert f'"{kind}"' in NATIVE_JS, f"native.js no longer classifies {kind!r} scan failures"


# --------------------------------------------------------------- Info.plist

REQUIRED_PLIST_KEYS = [
    "NSCameraUsageDescription",
    "NSLocalNetworkUsageDescription",
    "NSPhotoLibraryAddUsageDescription",
]


@pytest.mark.parametrize("key", REQUIRED_PLIST_KEYS)
def test_purpose_string_is_written_and_verified(key):
    assert f"str {key} " in IOS_CONFIGURE, f"ios-configure.sh no longer writes {key}"
    assert re.search(rf"^expect {key} ", IOS_CONFIGURE, re.M), (
        f"ios-configure.sh writes {key} but never reads it back. PlistBuddy "
        f"failures are swallowed by design, so an unverified write can ship a "
        f"binary whose camera is dead on arrival."
    )


def test_plist_verification_failure_fails_the_build():
    assert re.search(r'FAILED.*-ne 0', IOS_CONFIGURE), (
        "ios-configure.sh must exit non-zero when a key it wrote isn't there"
    )


# ------------------------------------------------------ the fallback route

def test_pairing_has_a_camera_route_that_does_not_need_the_plugin():
    assert "getUserMedia" in APP_JS
    assert "loadJsQR" in APP_JS, "app.js no longer loads the bundled QR decoder"
    assert (ROOT / "web" / "vendor" / "jsqr.js").exists(), "web/vendor/jsqr.js is missing"


def test_plugin_failure_falls_through_to_that_route():
    assert "nativeDead" in APP_JS, (
        "app.js must stop retrying a plugin that failed at the plugin level and "
        "use the in-page camera instead"
    )


def test_vendored_decoder_registers_itself():
    """The UMD wrapper must fall through to a global when loaded as <script>."""
    src = (ROOT / "web" / "vendor" / "jsqr.js").read_text()
    assert re.search(r"\.jsQR\s*=\s*\w+\(\)", src), (
        "vendor/jsqr.js must expose window.jsQR when loaded as a plain <script>"
    )
    assert "sourceMappingURL" not in src, (
        "the vendored copy must not point at a source map that isn't shipped"
    )


def test_vendored_decoder_actually_decodes_a_pairing_qr():
    """Render the real pairing payload with the real generator, then decode it.

    segno is what the server renders pairing QRs with, so this exercises the
    exact round trip the app performs — minus the camera, which no CI machine
    has. Skipped where node is unavailable; GitHub's runners have it.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    segno = pytest.importorskip("segno")

    payload = json.dumps({"h": "pair", "u": "https://demo.pocketadm.com", "c": "TEST-CODE-1234"})
    matrix = [list(row) for row in segno.make(payload, error="m").matrix]
    scale, border, n = 6, 4, len(matrix)
    size = (n + 2 * border) * scale
    pixels = []
    for y in range(size):
        my = y // scale - border
        for x in range(size):
            mx = x // scale - border
            dark = 0 <= my < n and 0 <= mx < n and matrix[my][mx]
            v = 0 if dark else 255
            pixels += [v, v, v, 255]

    with tempfile.TemporaryDirectory() as tmp:
        data = pathlib.Path(tmp) / "frame.json"
        data.write_text(json.dumps({"w": size, "h": size, "d": pixels}))
        script = pathlib.Path(tmp) / "decode.js"
        script.write_text(
            "const jsQR = require(process.argv[2]);\n"
            "const f = require(process.argv[3]);\n"
            "const r = jsQR(new Uint8ClampedArray(f.d), f.w, f.h);\n"
            "process.stdout.write(r ? r.data : '');\n"
        )
        out = subprocess.run(
            [node, str(script), str(ROOT / "web" / "vendor" / "jsqr.js"), str(data)],
            capture_output=True, text=True, timeout=60,
        )
    assert out.returncode == 0, out.stderr
    assert out.stdout == payload, f"decoded {out.stdout!r}, expected {payload!r}"
