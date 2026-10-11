// App shell cache + stale-while-revalidate for read-only API calls, so recent articles open offline.
const SHELL = "forage-shell-v4";
const API = "forage-api-v4";
const ASSETS = ["/", "/static/style.css", "/static/app.js", "/static/manifest.webmanifest", "/static/icons/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, API].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/opml")) {
    e.respondWith(
      fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(API).then((c) => c.put(req, copy)); }
        return res;
      }).catch(() => caches.match(req).then((r) => r || new Response("[]", { status: 503, headers: { "content-type": "application/json" } })))
    );
    return;
  }
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then((r) => r || caches.match("/")))
  );
});
