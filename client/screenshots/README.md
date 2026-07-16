# App Store screenshots

Regenerated, not hand-made. `iphone-65/` holds the **APP_IPHONE_65** slot
(1242 x 2688) that App Store Connect requires — the only screenshot slot left
now that the app ships iPhone-only (see `scripts/ios-configure.sh`,
`TARGETED_DEVICE_FAMILY = 1`; a universal build also demands
`APP_IPAD_PRO_3GEN_129` at 2048 x 2732).

## What they show

The **demo build** (`HELMSMAN_DEMO=1`) — the same application code as the
release, rendered against `demodata.py`'s sample server instead of a real one.
That is deliberate: it keeps real hostnames, containers and infrastructure out
of a public store listing.

Two things to know before uploading:

- The `Demo mode` banner is hidden at capture time. It is an artifact of the
  capture environment; a real user never sees it. Nothing else is altered — the
  UI and the data below it are exactly what the product renders.
- The Vibe conversation is the demo's seeded sample session and ends with its
  own line saying nothing was really changed. That line is honest and can stay;
  remove the screenshot instead of editing the content if you'd rather not
  ship it.

## Regenerate

Needs the demo running locally (`docker compose -f docker-compose.demo.yml up -d --build`,
port 8091):

    docker run --rm --network host \
      -v "$PWD/client/screenshots/iphone-65:/out" \
      -e NODE_PATH=/usr/src/app/node_modules \
      --entrypoint node zenika/alpine-chrome:with-puppeteer /out/shots.js

`shots.js` logs in with the demo password, hides the demo banner, walks the five
tabs and writes 414x896 @3x. Note Node resolves modules from the *script's*
directory, hence `NODE_PATH`.
