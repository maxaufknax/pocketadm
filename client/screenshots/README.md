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

The files are numbered in the order the store should show them: `01-home`,
`02-vibe`, `03-apps`, `04-health`, `05-terminal`. **Delete the old PNGs when the
numbering changes** — a rename leaves the previous set behind, and stale
screenshots of an old layout sitting next to fresh ones is how the wrong one
gets uploaded.

Two tabs need help to photograph well, and `shots.js` fails loudly rather than
shipping either:

- **Terminal** opens on its launcher, not on a shell, so the script taps the
  first target and runs `docker ps` — otherwise the shot is an empty black
  rectangle. It then drops focus, because typing raises `body.kb-open` and
  slides the tab bar away for the keyboard.
- **Vibe** needs the demo's seeded chat, which only opens because boot falls
  back to the first non-empty conversation when there's no `localStorage` to
  resume from (a first-time visitor, which is what a capture run is).

## pocketadm-showcase.mp4 + showcase-poster.png — the README banner

The landscape screen recording at the top of the repo README (29.5 s, 1920x886).
GitHub never plays a repo-hosted video inline in a README, so the README links
the mp4 behind `showcase-poster.png`, a frame with a play overlay — the click
lands on the blob page, which plays it. (True inline autoplay is only possible
through GitHub's own attachment CDN: drag the mp4 into any issue comment and
paste the `user-attachments.githubusercontent.com` URL it produces into the
README instead of the `<img>`.)

Kept under 10 MB — the attachment-CDN limit, for the day of that swap — by
re-encoding without audio, the source being a 29 MB H.264+AAC take from the
Phone's screen recording (`26-10-10 01-06-52 1920.mp4`, landscape oriented):

    ffmpeg -y -i source.mp4 -an -c:v libx264 -pass 1 -b:v 2300k \
      -preset medium -movflags +faststart -pix_fmt yuv420p -f mp4 /dev/null
    ffmpeg -y -i source.mp4 -an -c:v libx264 -pass 2 -b:v 2300k \
      -preset medium -movflags +faststart -pix_fmt yuv420p pocketadm-showcase.mp4

Poster: a frame at 2 s (`ffmpeg -ss 2 -i … -frames:v 1`) with a play circle
drawn on top (Pillow); regenerate both together when a new recording replaces
the video.
