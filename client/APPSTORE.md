# Shipping PocketADM to the App Store — no Mac needed

This is the end-to-end runbook. The build, signing and upload all happen on
Codemagic's macOS cloud VMs; you drive it from a browser (your iPhone is enough).
Everything an agent can prepare is already in this repo:

- `codemagic.yaml` — the cloud build pipeline (build → sign → TestFlight).
- `client/scripts/ios-configure.sh` — native Info.plist settings, applied at build time.
- `client/assets/{icon,splash,splash-dark}.png` — source art; all iOS sizes are generated in CI.
- Privacy + Support pages live at `https://pocketadm.com/privacy/` and `/support/`.

Legend: 👤 = you do it in a browser · 🤖 = already done in the repo.

---

## 0. Prerequisite: the code must be on GitHub

Codemagic builds from a Git repo, so push this project (including `client/` and
`codemagic.yaml`) to GitHub. If you have no push credential on the server, create
a Personal Access Token and:

```bash
cd /srv/cloud-server/pocketadm
git add -A && git commit -m "iOS release pipeline"
git push https://<TOKEN>@github.com/maxaufknax/pocketadm.git main
```

(Ask the agent to run this once you have a token — it never stores it.)

---

## 1. 👤 One-time Apple setup (App Store Connect, ~20 min)

1. **Accept the agreements.** App Store Connect → *Business* → accept the
   **Free Apps** agreement. (No tax/banking forms are needed for a free app.)
2. **Register the App ID.** [developer.apple.com](https://developer.apple.com) →
   *Certificates, IDs & Profiles* → *Identifiers* → **+** → App ID → Bundle ID
   **explicit** `de.maxaufknax.pocketadm`. Capabilities: leave defaults (add
   Push Notifications later if/when we wire APNs).
3. **Create the app record.** App Store Connect → *Apps* → **+** → New App:
   - Platform **iOS**, Name **PocketADM**, Primary language **English (U.S.)**,
     Bundle ID `de.maxaufknax.pocketadm`, SKU `pocketadm-ios`.
4. **Create an App Store Connect API key** (this is the no-Mac magic key).
   *Users and Access* → *Integrations* → *App Store Connect API* → **+**:
   - Name `Codemagic`, Access **App Manager**.
   - Download the `.p8` file (once only!), and note the **Key ID** and **Issuer ID**.

## 2. 👤 Connect Codemagic (~10 min)

1. Sign in at [codemagic.io](https://codemagic.io) with GitHub, add this repo.
2. *Team settings → Integrations → App Store Connect* → add the API key from 1.4
   (`.p8` + Key ID + Issuer ID). **Name it exactly `PocketADM ASC key`** — that
   string is referenced in `codemagic.yaml`.
3. Open the app in Codemagic → it detects `codemagic.yaml` → run the
   **`ios-release`** workflow.

### 2.1 👤 Signing: the certificate private key (one-time, required)

Apple's API can *create* your distribution certificate but can **never** hand out
its private key — so you supply one. The pipeline reuses it every build (Apple
caps distribution certs, so we must not generate a new one each time).

1. Generate an RSA private key and base64-encode it to a single line:
   ```bash
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out cert_key.pem
   base64 -w0 cert_key.pem   # copy the whole one-line output
   ```
   (On the server, ask the agent — it can generate this for you.)
2. In Codemagic → your app → **Environment variables**: add
   - **Name** `CERTIFICATE_PRIVATE_KEY_B64`
   - **Value** the one-line base64 from step 1
   - **Group** `ios-release` · **Secure** ✅ (must be checked)
3. Keep `cert_key.pem` safe (or just regenerate — the first build creates a fresh
   cert from whatever key it sees). Never commit it.

That's it. The pipeline installs deps, bundles the latest `../web` UI, generates
the iOS project + icons, creates the cert + profile from your key, signs, and
uploads to **TestFlight**.

## 3. Test on your iPhone

- Install **TestFlight** from the App Store.
- After the build finishes processing (~15–60 min), it appears under
  *TestFlight → Internal Testing*. Add yourself as an internal tester → install.

## 4. 👤 Submit for review

`codemagic.yaml` already has `submit_to_app_store: true`, so **every green build
submits itself**. There is nothing left to flip — but the submission only goes
through once the listing below is complete.

### The listing is the whole job (⚠️ read this first)

The build succeeding tells you nothing about the submission succeeding. They are
separate: the 2026-07-16 build signed, uploaded and reached TestFlight fine, then
the App Store step failed with a 409 because the listing was empty. Codemagic
marks the *whole build* red when that happens, even though the IPA is already in
App Store Connect and TestFlight already has it. **A red build with
`UPLOAD SUCCEEDED` in the log means "not submitted", not "not built".**

Everything below is manual work in App Store Connect — no API, no CI step does
it for you. This is the exact list Apple returned, so nothing here is guesswork:

**App information**
- [ ] **Primary category** (`primaryCategory` — Developer Tools)
- [ ] **Content rights declaration** (`contentRightsDeclaration` — contains no
      third-party content)
- [ ] **Age rating questionnaire** — all of it. Apple named every attribute:
      medical/treatment info, gambling + simulated gambling, sexual content
      (both attributes), nudity, violence (cartoon, realistic, prolonged),
      horror/fear, mature/suggestive, profanity, alcohol/tobacco/drugs, guns,
      contests, loot box, **unrestricted web access**, **messaging and chat**,
      **user generated content**, advertising, parental controls, health or
      wellness topics, age assurance. Answer *No* to all of them **except**
      the ones flagged in § *Age rating* below — read that section, the honest
      answers there are not all "No".

**Pricing and availability**
- [ ] **Price: Free** (`App is missing required pricing` — this alone blocks the
      submission). Requires the **Free Apps agreement** to be accepted first
      (§ 1). No tax/banking forms needed for a free app.

**Version (1.0.0) → App Store**
- [ ] **Description**, **Keywords**, **Support URL**, **Privacy Policy URL**,
      **Copyright** — all copy-paste ready in § *Store listing* below. The URLs
      are live: https://pocketadm.com/support/ and https://pocketadm.com/privacy/
- [ ] **Screenshots — `APP_IPHONE_65` (1242 x 2688)**. Ready to upload from
      `client/screenshots/iphone-65/` (see the README there for what they show).
      `APP_IPAD_PRO_3GEN_129` is **no longer required**: the app now ships
      iPhone-only (`TARGETED_DEVICE_FAMILY = 1`, set in `scripts/ios-configure.sh`).
- [ ] **App Review Information** (`appStoreReviewDetail was not found`) — the
      demo account and notes from § *Review notes*.

**App privacy** (separate section, must be *published*)
- [ ] Answer and **publish** the data-usage questions — § *App privacy*.
      "Answers to what data your app collects are needed" means published, not
      just saved.

Once all of it is filled, re-run the `ios-release` workflow (or hit *Submit for
Review* on the already-processed build — the binary is there already).

Apple review is usually **under 24 h** today. Budget one possible rejection
round (see § *Review notes* — the demo account is your safety net).

---

## Store listing (copy-paste ready)

| Field | Value |
| --- | --- |
| **Name** (≤30) | `PocketADM: Server Console` |
| **Subtitle** (≤30) | `Your server, in your pocket` |
| **Category** | Primary **Developer Tools**, Secondary **Utilities** |
| **Price** | Free |
| **Support URL** | `https://pocketadm.com/support/` |
| **Marketing URL** | `https://pocketadm.com` |
| **Privacy Policy URL** | `https://pocketadm.com/privacy/` |
| **Copyright** | `2026 Maximilian Paasch` |

**Keywords** (≤100 chars):
```
server,ssh,terminal,docker,self-host,homelab,admin,monitoring,vps,nas,devops,sysadmin,ai
```

**Promotional text** (≤170):
```
Run and troubleshoot your self-hosted server from your phone: live dashboard, a real terminal, one-tap app installs, and an AI agent that works directly on your box.
```

**Description** (English):
```
PocketADM is a mobile-first command center for the servers you host yourself.
Connect to your own Linux box and run the whole thing from your phone.

• Live dashboard — CPU, memory, disk, uptime and network at a glance, with every
  container grouped and one tap from its logs.
• A real terminal — a full terminal with a mobile key bar and copy & paste. Open
  a shell as your own host user, the app, or exec into any container.
• Vibe Code — an optional AI agent that runs commands, reads and edits files and
  fixes things directly on your server. It streams live, asks before anything
  destructive, and you bring your own API key (or run a local model).
• One-tap app store — deploy popular self-hosted apps as clean Docker/compose
  projects with friendly config forms.
• Smart updates & health — spot outdated images, roll back safely, and get plain
  language security and health checks.

PocketADM is open source (MIT). There is no account with us and no tracking: the
app talks only to the servers you add, over a direct connection. You run the
server software yourself with a single install command from pocketadm.com.

Just looking? Add the demo server demo.pocketadm.com with username and password
"demo" to explore a read-only sandbox.
```

**Beschreibung** (German — optional localization):
```
PocketADM ist die mobile Kommandozentrale für deine selbst gehosteten Server.
Verbinde dich mit deinem eigenen Linux-Server und steuere alles vom Handy.

• Live-Dashboard — CPU, RAM, Speicher, Uptime und Netzwerk auf einen Blick, jeder
  Container gruppiert und einen Tipp von seinen Logs entfernt.
• Ein echtes Terminal — mit mobiler Tastenleiste und Copy & Paste. Öffne eine
  Shell als dein Host-Benutzer, als App, oder springe in jeden Container.
• Vibe Code — ein optionaler KI-Agent, der Befehle ausführt, Dateien liest und
  bearbeitet und direkt auf deinem Server Dinge repariert. Er streamt live, fragt
  vor riskanten Aktionen nach, und du nutzt deinen eigenen API-Key (oder ein
  lokales Modell).
• App-Store mit einem Tipp — installiere beliebte Self-Hosting-Apps als saubere
  Docker-/Compose-Projekte.
• Updates & Health — erkenne veraltete Images, rolle sicher zurück und erhalte
  verständliche Sicherheits- und Health-Checks.

PocketADM ist Open Source (MIT). Es gibt kein Konto bei uns und kein Tracking:
Die App spricht nur mit den Servern, die du hinzufügst. Die Serversoftware
installierst du selbst mit einem Befehl von pocketadm.com.

Nur schauen? Füge den Demo-Server demo.pocketadm.com mit Benutzername und
Passwort „demo" hinzu.
```

---

## App privacy (the questionnaire)

Answer **"Data Not Collected"** — it's the honest answer. The app has no backend
of ours, no analytics and no tracking; server addresses/tokens and preferences
live only on the device. (See `/privacy/`.)

## Age rating

Answer *No* to everything except the three below, which sound like they apply to
this app but mostly don't. Apple names all three explicitly in its rejection, so
decide them deliberately rather than clicking through:

| Attribute | Answer | Why |
| --- | --- | --- |
| `unrestrictedWebAccess` | **Yes** | The terminal and the agent can fetch arbitrary URLs (`fetch_url`, `curl`). That is honest and it likely makes the rating **17+**. Fine — the app is a root shell for grown-ups. |
| `messagingAndChat` | **No** | The chat is the user talking to an AI about *their own* server. The attribute is about communicating with *other people*; PocketADM has no user-to-user messaging. |
| `userGeneratedContent` | **No** | Nothing a user writes is shared with, or visible to, anyone else. There is no shared surface at all — every install is a private server. |

Do not answer *Yes* to the last two just because the word "chat" appears in the
product. That would gratuitously raise the rating and invite questions about
moderation tooling you have no need for.

## Review notes (paste into "App Review Information → Notes")

```
PocketADM is a client for self-hosted server software the user runs themselves;
it has no backend of ours. To review it end to end, add the demo server:

  Server address:  https://demo.pocketadm.com
  Username / password:  demo / demo

(In the app: Add a server → enter the address and password.)

App Transport Security allows arbitrary loads because PocketADM connects only to
servers the user explicitly adds — these are often local IPs (192.168.x.x), .local
hosts, or self-hosted domains that may use self-signed certificates. No traffic
goes to any server other than the one the user configures.

Camera is used solely to scan pairing QR codes. The app collects no personal data.
```

Also set **App Review Information → Sign-In required: Yes**, and provide the same
demo/demo (the app itself has no account, but the reviewer needs a server to sign
into — the demo server provides one).

---

## Guideline 4.2 ("minimum functionality") — status

Thin website-wrappers get rejected. PocketADM is already well-positioned:

- ✅ The UI is **bundled locally** (Capacitor serves `www/` from the app, it does
  not load a remote website), and there is a native **cold-start Connect screen**,
  multi-server switching and QR pairing — real client behavior, not a webview.
- ✅ Native camera QR scanning via `@capacitor/barcode-scanner` (the web
  `BarcodeDetector` doesn't exist in WKWebView — `PocketNative.scanQR` bridges to
  the native scanner, the browser/PWA path keeps the web fallback). Plus native
  status bar, splash, keyboard and haptics.
- 🔜 Nice-to-have hardening for a smoother review / better app: native push
  (APNs) for Sentinel alerts and a native share sheet. Not blockers for a
  first submission, but on the roadmap.

## Final-release checklist (work through top to bottom)

1. **demo.pocketadm.com reachable** with `demo` / `demo` — reviewers depend on it
   (see *Review notes*). `curl -s https://demo.pocketadm.com/api/info` must
   answer with `"demo": true`.
2. **TestFlight build verified on a real iPhone** — especially: pairing QR scan
   (native scanner), keyboard behavior in Vibe, terminal sessions.
3. Store listing + screenshots + App Privacy answered in App Store Connect.
4. Flip **`submit_to_app_store: true`** in `codemagic.yaml` (root) and start
   `ios-release` — or leave it `false` and press *Submit for Review* manually on
   the processed build in App Store Connect. Until then it stays `false` on
   purpose: every CI run only ships to TestFlight.
5. After approval: switch the release toggle in ASC to *manual release* if you
   want to control launch day.

## Android (later)

The same repo builds Android: `npm run add:android`, then a Codemagic
`android` workflow signs an `.aab` and ships to the Play Console. Not wired yet.
