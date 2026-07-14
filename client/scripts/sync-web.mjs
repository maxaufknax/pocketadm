#!/usr/bin/env node
/* Copy the PWA in ../web into ./www so Capacitor can bundle it as the native
   client shell. The web app is server-agnostic already: with no backend behind
   the bundled files, /api/info fails, so it shows the cold-start Connect screen
   (add a server by URL/QR, or install onto a new server over SSH).

   We drop the service worker in the native build — the OS handles the app
   lifecycle and a SW caching a bundled shell only gets in the way. */
import { cp, rm, mkdir, readdir, unlink, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = join(here, "..", "..", "web");
const wwwDir = join(here, "..", "www");

if (!existsSync(webDir)) {
  console.error("web/ not found at", webDir);
  process.exit(1);
}

await rm(wwwDir, { recursive: true, force: true });
await mkdir(wwwDir, { recursive: true });
await cp(webDir, wwwDir, { recursive: true });

// no service worker in the native shell
for (const f of ["sw.js"]) {
  const p = join(wwwDir, f);
  if (existsSync(p)) await unlink(p);
}
// neutralise the SW registration line so the app never 404s trying to load it
const { readFile } = await import("node:fs/promises");
const appJs = join(wwwDir, "app.js");
if (existsSync(appJs)) {
  let src = await readFile(appJs, "utf8");
  src = src.replace(/if \("serviceWorker" in navigator\)[^\n]*\n/, "");
  await writeFile(appJs, src);
}

// native shell only: pin the zoom. Stops iOS from zoom-jumping into focused
// fields and disables pinch — the PWA served by the server keeps browser zoom.
const indexHtml = join(wwwDir, "index.html");
if (existsSync(indexHtml)) {
  let html = await readFile(indexHtml, "utf8");
  html = html.replace(
    /content="width=device-width, initial-scale=1, viewport-fit=cover/,
    'content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover',
  );
  await writeFile(indexHtml, html);
}

console.log("synced web/ -> www/ (service worker stripped, viewport pinned)");
