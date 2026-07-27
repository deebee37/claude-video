/* Cache-first service worker. The engine files are PRE-cached during
   install (the first page visit isn't controlled by the SW yet, so
   runtime caching alone would miss them and offline reloads would
   break). Bump CACHE to force everyone onto new files. */
"use strict";

const CACHE = "phone-editor-v2";
const PRECACHE = [
  "./",
  "./index.html",
  "./app.js",
  "./vendor/ffmpeg.js",
  "./vendor/814.ffmpeg.js",
  "./vendor/ffmpeg-core.js",
  "./vendor/ffmpeg-core.wasm",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  // CacheStorage is origin-wide, but *.github.io hosts every one of the
  // user's project sites on one origin. Only delete THIS app's own older
  // caches (the "phone-editor-" prefix) so we never wipe another site's.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("phone-editor-") && k !== CACHE)
            .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;

  // ONE shared operation promise, passed synchronously to BOTH
  // respondWith() and waitUntil(). It awaits the cache write before
  // resolving, so the browser can never terminate this worker with a
  // 31MB write still in flight. It settles on every path -- cache hit,
  // network ok, network fail, put ok, put fail, or unexpected throw --
  // so the worker is never left waiting on an unresolved promise. A
  // cache.put failure is caught so it can never break the Response.
  const op = (async () => {
    const hit = await caches.match(event.request);
    if (hit) return hit;
    const resp = await fetch(event.request);
    if (resp.ok && (resp.type === "basic" || resp.type === "default")) {
      const cache = await caches.open(CACHE);
      try { await cache.put(event.request, resp.clone()); }
      catch (_) { /* cache write failed; the Response is still valid */ }
    }
    return resp;
  })();

  event.respondWith(op);
  event.waitUntil(op);
});
