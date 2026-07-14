# PocketADM native client (Capacitor)

This wraps the PocketADM PWA (`../web`) as a native iOS / Android app for the
App Store and Play Store. The web app is already **server-agnostic** (multi-server
store, bearer-token auth, permissive CORS, QR pairing), so the native shell adds
no product logic — it just bundles the UI and boots into the **Connect screen**
when there is no server behind the bundled files.

## How it behaves as a client

On launch the app calls `/api/info` against its own origin. In the native shell
there is no backend there, so that fails and PocketADM shows the cold-start
**Connect screen**:

- **Add a server** — enter a PocketADM server's address + admin password.
- **Scan pairing QR** — pair instantly from a server you're signed into elsewhere.
- **Set up a new server over SSH** — install PocketADM onto a fresh Linux box
  from a server you're already connected to (server-to-server bootstrap), or copy
  the one-line installer.

Once a server is added its bearer token is stored on-device and the app boots
straight into it; the top-bar hostname switches between multiple servers.

## Build

**No Mac? Ship it from the cloud.** The full App Store path — build, code
signing and TestFlight upload on Codemagic's macOS VMs — is documented step by
step in **[APPSTORE.md](APPSTORE.md)**. That is the recommended route; your
iPhone (plus a browser for App Store Connect) is all you need.

Local build (only if you *do* have a Mac / Android Studio):

```bash
cd client
npm install
npm run sync            # copies ../web into ./www and runs `cap sync`
npm run add:ios         # once, to create the ios/ project
npm run assets          # generate iOS icons + splash from ./assets
npm run open:ios        # opens Xcode  -> Product > Archive -> upload to TestFlight
npm run add:android     # once, to create the android/ project
npm run open:android    # opens Android Studio -> Build > Generate Signed Bundle
```

Re-run `npm run sync` whenever `../web` changes to re-bundle the latest UI.

## Notes

- **App icon / splash**: use `../web/icons/icon-512.png`. Generate platform assets
  with `@capacitor/assets` (`npx @capacitor/assets generate`) or set them in
  Xcode / Android Studio.
- **Service worker** is stripped from the native bundle by `scripts/sync-web.mjs`
  (the OS owns the app lifecycle; a SW caching a bundled shell only causes stale
  assets). It stays intact for the server-hosted PWA.
- **Deep links / pairing**: `iosScheme` is `pocketadm://` — pairing handoff links
  (`/?pair=CODE&c=CHAT`) also work when opened in the in-app browser.
- **Native SSH from a cold device** (installing onto a brand-new server before any
  server exists) needs a native SSH plugin and is not wired yet; today the SSH
  installer runs from a server you're already connected to. Until then, the
  Connect screen's one-line installer covers the very first server.
- **App icon / splash**: source art lives in `./assets` (`icon.png` 1024²,
  `splash.png` / `splash-dark.png` 2732²). CI runs `@capacitor/assets` to produce
  every platform size, so you never touch Xcode's asset catalog by hand.
- **Apple**: the Developer account is active. Follow [APPSTORE.md](APPSTORE.md)
  end to end — one-time App Store Connect setup, an API key, connect Codemagic,
  run the `ios-release` workflow. First build lands in TestFlight; review is
  usually under 24 h.
