# Contributing

Issues and PRs are welcome. This is a small project with strong opinions, so the
shorter and the more in-character a change is, the faster it lands.

## The stack, on purpose

- One FastAPI app (`server/`), vanilla HTML/JS/CSS (`web/`, xterm.js vendored):
  no build step, no frontend framework, no node_modules for the web app.
- The iPhone client (`client-swift/`, SwiftUI). The Capacitor shell (`client/`)
  shipped the 1.x App Store version, which it still supports.
- Dependencies are a cost, not a feature. A PR that adds one should say why the
  stdlib (`server/`) or a `<script src>` vendored file (`web/`) cannot do it.
- Text in the UI is written for a person in a hurry: no exclamation marks, no
  marketing words, no emoji.

## Run it before you PR

```bash
docker compose up -d --build          # the whole thing
docker compose logs helmsman          # the generated admin password
```

Without Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
ADMIN_PASSWORD=dev .venv/bin/uvicorn server.main:app --reload --port 8090
.venv/bin/pytest                      # the suite, conftest spins up fixtures
```

PR expectation: the tests that touch your change run green, and you say in one
short paragraph how you checked the rest by hand (a screenshot for UI, the
exact curl for an API). Small and honest beats big and fluent.

## Where things are

| Path | |
| --- | --- |
| `server/` | everything the container runs |
| `web/` | the PWA the iPhone container and any browser render |
| `client-swift/` | the native SwiftUI app |
| `install.sh` | the one-line installer, kept POSIX-bash and idempotent |
| `docs/BRANDING.md` | naming, colors and voice |
| `CHANGELOG.md` | one entry per release, user-visible |

Security concerns go to [SECURITY.md](SECURITY.md), not into issues.
