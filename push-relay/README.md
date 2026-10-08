# PocketADM push relay

Apple push notifications for self-hosted PocketADM servers. The iPhone app
registers its device token here and gets a random relay id; it hands that id to
the servers it is signed in to, and they send through `POST /v1/send`. The
relay holds the Apple key, the servers never see a device token, and nothing
about a message is stored. See the docstring in `relay.py` for the API.

Runs next to the website (see `../landing/docker-compose.yml`), reachable as
`https://pocketadm.com/push/`. Configuration:

| Variable | |
| --- | --- |
| `APNS_KEY_FILE` | the `.p8` key from developer.apple.com → Keys (Apple Push Notifications service) |
| `APNS_KEY_ID` | its Key ID |
| `APNS_TEAM_ID` | the developer team id |
| `RELAY_BUNDLES` | bundle ids that may register, comma-separated |
| `RELAY_PER_HOUR`, `RELAY_PER_DAY` | notifications per phone (120 / 1000) |

Without a key the relay still registers phones and answers `503` to sends, so
servers and the app can tell "not set up yet" from "broken".
