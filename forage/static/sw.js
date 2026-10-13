// App shell cache + stale-while-revalidate for read-only API calls, so recent articles open offline.
const SHELL = "forage-shell-v50";
const API = "forage-api-v38";
const THUMBS = "forage-thumbs";  // unversioned: thumbnails are immutable per URL, so they survive shell upgrades
const THUMBS_MAX = 2000;
const API_MAX = 300;  // cached API responses kept, oldest out
const ASSETS = ["/", "/static/style.css", "/static/app.js", "/static/manifest.webmanifest", "/static/icons/icon.svg",
  "/static/fonts/Libron-Regular.woff2", "/static/fonts/Libron-Italic.woff2", "/static/fonts/Libron-Bold.woff2", "/static/fonts/Libron-BoldItalic.woff2",
  "/static/fonts/SourceSans3-Regular.woff2", "/static/fonts/SourceSans3-Semibold.woff2", "/static/fonts/SourceSans3-Bold.woff2", "/static/fonts/SourceSans3-It.woff2",
  "/static/fonts/JetBrainsMonoNerd-Regular.woff2", "/static/fonts/JetBrainsMonoNerd-Bold.woff2"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, API, THUMBS].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/thumb/")) {  // cache-first: the ?v= hash makes each URL immutable
    e.respondWith(caches.open(THUMBS).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      try {
        const res = await fetch(req);
        if (res.ok) {
          await c.put(req, res.clone());
          c.keys().then((ks) => ks.slice(0, Math.max(0, ks.length - THUMBS_MAX)).forEach((k) => c.delete(k)));  // oldest out
        }
        return res;
      } catch { return new Response("", { status: 504 }); }
    }));
    return;
  }
  if (url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/opml")) {
    e.respondWith(
      fetch(req).then((res) => {
        if (res.ok && !/^\/api\/(settings|find)/.test(url.pathname)) {
          const copy = res.clone();
          caches.open(API).then(async (c) => {
            await c.put(req, copy);
            const ks = await c.keys();
            ks.slice(0, Math.max(0, ks.length - API_MAX)).forEach((k) => c.delete(k));
          });
        }
        return res;
      }).catch(() => caches.match(req).then((r) => r || new Response('{"detail":"Offline, and this isn\'t saved on the device"}', { status: 503, headers: { "content-type": "application/json" } })))
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
