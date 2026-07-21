# Branding: internal `Helmsman` vs. external `PocketADM`

The product was renamed **Helmsman → PocketADM** before the App Store launch.
The rename is deliberately **partial**: anything an end user can see is
`PocketADM`; the internal plumbing keeps the `helmsman` identifiers so that
existing installs are not disrupted (renaming a Docker volume or container is a
data-loss / downtime risk for no user-visible gain).

This file is the single source of truth for where the line sits. If you touch a
name, decide which side of the line it is on and keep it there.

## External — must read `PocketADM` (user-visible)

| Where | File |
| --- | --- |
| TOTP issuer shown in the user's authenticator app | [`server/auth.py`](../server/auth.py) → `provisioning_uri()` |
| First-run "admin password" log banner | [`server/auth.py`](../server/auth.py) → `bootstrap_password()` |
| App name / Store listing / bundle id `de.maxaufknax.pocketadm` | [`client/`](../client/), [`codemagic.yaml`](../codemagic.yaml) |
| Canonical install + catalog URLs (repo `maxaufknax/pocketadm`) | [`server/config.py`](../server/config.py) `DEFAULT_CATALOG_URL`, [`server/bootstrap.py`](../server/bootstrap.py) `INSTALLER_URL`, [`install.sh`](../install.sh) `REPO` |

### Why the URLs matter

The old defaults pointed at `github.com/maxaufknax/helmsman`. Those still
resolve **only** because GitHub keeps a permanent rename redirect
(`helmsman` → `pocketadm`). The moment a *new* repo named `helmsman` is created
under the same account, the redirect stops and every fresh install / catalog
fetch breaks silently. They now point directly at `pocketadm`. Do not revert.

## Internal — intentionally stays `helmsman` (pinned, not a bug)

These are **not** user-visible and are pinned on purpose. Changing them would
rename Docker resources and break dependents that address the stack by name.

- **Docker**: compose project name (`name: helmsman`), `container_name: helmsman`
  / `helmsman-demo`, image tag `helmsman:latest`, data volume
  `helmsman_helmsman_data`, deployed-app compose projects `helmsman-<id>`,
  snapshot repo `helmsman/snapshot`, self-detection heuristics.
  See [`docker-compose.yml`](../docker-compose.yml) (top comment explains the pin).
- **Env vars**: `HELMSMAN_DATA`, `HELMSMAN_DEMO`, `HELMSMAN_PORT`,
  `HELMSMAN_BIND`, `HELMSMAN_IMAGE`, `HELMSMAN_WORKDIR`, `HELMSMAN_HOSTNAME`,
  `HELMSMAN_CATALOG_URL`. Renaming these breaks existing `docker-compose.yml`
  and shell profiles in the wild.
- **FastAPI app title** (`FastAPI(title="Helmsman")`) — only appears in internal
  API metadata, never rendered to the user.
- **Installer internals**: [`install.sh`](../install.sh) install dir
  `/opt/helmsman`, `HELMSMAN_*` env vars, and its cosmetic "Helmsman is running!"
  banner. The banner is seen only by the admin running the one-liner (who is
  already dealing with the container internals), so it stays with the internal
  set; the *URL* it clones from is external and was moved to `pocketadm`.

## Grey area — indirect, left as-is for now

The AI system prompts still describe the assistant as belonging to "Helmsman"
([`server/ai.py`](../server/ai.py) `SYSTEM_PROMPT`,
[`server/agents.py`](../server/agents.py) `SENTINEL_SYSTEM`,
[`server/reports.py`](../server/reports.py) analyst prompt). These are not shown
directly, but the model *could* echo the name in a reply. They are left
untouched deliberately (out of the launch-critical scope) and noted here so the
decision is explicit — flip them to `PocketADM` when convenient.
