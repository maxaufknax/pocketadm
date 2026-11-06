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

## showcase.gif — the README banner

`showcase.gif` is the animated banner at the top of the repo README: the five
`iphone-65` shots cross-fading, 300 px wide, ~8 s, built with ffmpeg
(LD_LIBRARY_PATH may need clearing on machines with a bundled
libstdc++, e.g. from editor extensions):

    ffmpeg -y \
      $(for f in iphone-65/0*.png; do echo -loop 1 -t 2 -i $f; done) \
      -filter_complex "\
[0:v]scale=300:-2:flags=lanczos,setsar=1[a];[1:v]scale=300:-2:flags=lanczos,setsar=1[b];\
[2:v]scale=300:-2:flags=lanczos,setsar=1[c];[3:v]scale=300:-2:flags=lanczos,setsar=1[d];\
[4:v]scale=300:-2:flags=lanczos,setsar=1[e];\
[a][b]xfade=transition=fade:duration=0.6:offset=1.4[x1];\
[x1][c]xfade=transition=fade:duration=0.6:offset=2.8[x2];\
[x2][d]xfade=transition=fade:duration=0.6:offset=4.2[x3];\
[x3][e]xfade=transition=fade:duration=0.6:offset=5.6,fps=10,split[s0][s1];\
[s0]palettegen=stats_mode=single[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[o]" \
      -map "[o]" -loop 0 showcase.gif

Regenerate together with the PNGs whenever the screens change; the crossfade
offsets assume five inputs of 2 s each.
