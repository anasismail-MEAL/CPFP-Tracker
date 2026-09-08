// Caches the shell only. Pages and API calls always go to the network, because stale
// child records are worse than no record. ponytail: no offline write queue.
const CACHE = "cpfp-shell-v1";
const SHELL = ["/static/app.css", "/static/manifest.json", "/static/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || !url.pathname.startsWith("/static/")) return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
