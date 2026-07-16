/* PocketADM service worker — cache the app shell, never cache API/WS. */
const CACHE = "pocketadm-v22";
const SHELL = ["/", "/style.css", "/app.js", "/native.js", "/icons.js", "/manifest.webmanifest",
  "/vendor/xterm.js", "/vendor/xterm.css", "/vendor/xterm-addon-fit.js",
  "/icons/icon.svg", "/icons/logo.svg", "/icons/wordmark.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return;
  // network-first so updates land immediately; cache is the offline fallback
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok && e.request.method === "GET") {
        const clone = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, clone));
      }
      return res;
    }).catch(() => caches.match(e.request, { ignoreSearch: true }))
  );
});
