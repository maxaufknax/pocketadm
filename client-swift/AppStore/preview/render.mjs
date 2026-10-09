#!/usr/bin/env node
// Renders the App Store preview video from preview.html.
//
//   node render.mjs [--lang en|de] [--orient portrait|landscape] [--out FILE.mp4]
//   node render.mjs --stills 3,8.5,15 [--lang de]      # PNGs of single moments
//
// Every frame is a pure function of its time (window.renderFrame(t)), so the
// page is photographed 30 times per second of video and the PNGs are piped
// into ffmpeg together with the soundtrack from music.py. The output follows
// App Store Connect's preview spec: H.264 High@4.0, 30 fps, 10-12 Mbit/s,
// stereo AAC 256 kbit/s at 48 kHz, 15-30 seconds.
//
// Needs Node, Playwright with Chromium, ffmpeg, and Python 3 with numpy + scipy.
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");          // client-swift/
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); }
catch { ({ chromium } = require(path.join(spawnSync("npm", ["root", "-g"]).stdout.toString().trim(), "playwright"))); }

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
  return acc;
}, []));
const lang = args.lang || "en";
const orient = args.orient || "portrait";
const [W, H] = orient === "portrait" ? [886, 1920] : [1920, 886];
const outDir = path.join(here, "out");
await mkdir(outDir, { recursive: true });

const TYPES = { ".html": "text/html", ".png": "image/png", ".svg": "image/svg+xml", ".ttf": "font/ttf", ".js": "text/javascript" };
const server = createServer(async (req, res) => {
  try {
    const p = path.normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
    const file = path.join(root, p);
    if (!file.startsWith(root)) throw new Error("outside");
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
    res.end(await readFile(file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const url = `http://127.0.0.1:${server.address().port}/AppStore/preview/preview.html?lang=${lang}&orient=${orient}`;

const browser = await chromium.launch({ args: ["--font-render-hinting=none", "--disable-lcd-text"] });
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
page.on("pageerror", (e) => console.error("page error:", e.message));
await page.goto(url);
await page.waitForFunction(() => window.READY === true, null, { timeout: 60000 });
const timeline = await page.evaluate(() => window.TIMELINE);
const shoot = async (t) => { await page.evaluate((t) => window.renderFrame(t), t); return page.screenshot({ type: "png" }); };

if (args.stills) {
  for (const t of args.stills.split(",").map(Number)) {
    const file = path.join(outDir, `still-${lang}-${orient}-${t.toFixed(2)}.png`);
    await writeFile(file, await shoot(t));
    console.log(file);
  }
} else {
  // the soundtrack follows the cuts, taps and keystrokes of this timeline
  const tlFile = path.join(outDir, `timeline-${lang}-${orient}.json`);
  const wav = path.join(outDir, `soundtrack-${lang}-${orient}.wav`);
  await writeFile(tlFile, JSON.stringify(timeline, null, 1));
  if (!args["keep-audio"]) {
    const py = spawnSync("python3", [path.join(here, "music.py"), tlFile, wav], { stdio: "inherit" });
    if (py.status !== 0) throw new Error("music.py failed");
  }
  if (args["audio-only"]) { await browser.close(); server.close(); process.exit(0); }
  await mkdir(path.join(here, "video"), { recursive: true });
  const out = args.out || path.join(here, "video", `PocketADM-preview-${lang}-${W}x${H}.mp4`);
  const frames = Math.round(timeline.duration * timeline.fps);
  const ff = spawn("ffmpeg", [
    "-y", "-loglevel", "error",
    "-f", "image2pipe", "-framerate", String(timeline.fps), "-c:v", "png", "-i", "-",
    "-i", wav,
    "-map", "0:v", "-map", "1:a",
    "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0", "-pix_fmt", "yuv420p",
    "-preset", "slow", "-tune", "animation",
    "-b:v", "11M", "-minrate", "11M", "-maxrate", "11M", "-bufsize", "11M", "-x264-params", "nal-hrd=cbr",
    "-r", String(timeline.fps), "-g", "30",
    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
    "-shortest", "-movflags", "+faststart",
    out,
  ], { stdio: ["pipe", "inherit", "inherit"] });
  const started = Date.now();
  for (let i = 0; i < frames; i++) {
    const png = await shoot(i / timeline.fps);
    if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once("drain", r));
    if (i % 60 === 0) process.stdout.write(`\rframe ${i}/${frames}  ${((Date.now() - started) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end();
  await new Promise((r, j) => ff.on("close", (c) => (c === 0 ? r() : j(new Error("ffmpeg " + c)))));
  console.log(`\n${out}`);
}
await browser.close();
server.close();
