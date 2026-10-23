/* ChorePilot service worker: caches the app shell only, so the dashboard opens instantly and
   shows a friendly "can't reach home" page when offline. It NEVER caches /api/ responses:
   device state, chores and anything personal always come live from your ChorePilot. */
"use strict";
const CACHE = "chorepilot-shell-{{VERSION}}";
const SHELL = ["/", "/app.css", "/app.js", "/theme.js", "/manifest.webmanifest", "/icon.svg", "/icon-192.png",
  "/apple-touch-icon.png", "/fonts/ibm-plex-sans.woff2"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return;   // always live
  if (!SHELL.includes(url.pathname) && url.pathname !== "/index.html") return;
  // network first (so an update shows up at once), the cached shell when home is unreachable
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then(r => r || caches.match("/"))));
});
