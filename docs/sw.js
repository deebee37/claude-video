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
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;

  // waitUntil is registered SYNCHRONOUSLY so the browser cannot kill
  // this worker while a large cache write (the 31MB wasm) is in flight.
  let settleWrite;
  const writeDone = new Promise((resolve) => { settleWrite = resolve; });
  event.waitUntil(writeDone);

  event.respondWith((async () => {
    try {
      const hit = await caches.match(event.request);
      if (hit) { settleWrite(); return hit; }
      const resp = await fetch(event.request);
      if (resp.ok && (resp.type === "basic" || resp.type === "default")) {
        const copy = resp.clone();
        caches.open(CACHE)
          .then((c) => c.put(event.request, copy))
          .catch(() => {})
          .then(settleWrite);
      } else {
        settleWrite();
      }
      return resp;
    } catch (err) {
      settleWrite();
      throw err;
    }
  })());
});
