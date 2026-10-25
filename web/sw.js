// オフラインで動くためのキャッシュ。
// - 画面と Python のファイル: Mac につながれば新しいものを取り、つながらなければ (外出先など) キャッシュを使う
// - Pyodide (CDN): 版が固定なので、一度取ったらキャッシュを使う
// - 同期の API (/api/): キャッシュしない
"use strict";

const CACHE = "lexicon-engine-v4";
const NETWORK_TIMEOUT = 3000;
// Mac につながらなかったら、しばらくは待たずにキャッシュを使う (起動時に何度も待たないように)
const OFFLINE_GRACE = 30000;
let offlineUntil = 0;
const APP_FILES = [
  "./", "index.html", "app.js", "editor.js", "style.css", "manifest.webmanifest", "icon-180.png", "icon-512.png", "py/modules.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

async function precache() {
  // Python ファイルはサーバーの一覧 (py/modules.json) にあるものをすべてキャッシュする
  const cache = await caches.open(CACHE);
  await cache.addAll(APP_FILES);
  const modules = await (await cache.match("py/modules.json")).json();
  await cache.addAll(modules.map((name) => `py/${name}`));
}

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith("/api/")) return;
    event.respondWith(networkFirst(request));
  } else if (url.hostname === "cdn.jsdelivr.net") {
    event.respondWith(cacheFirst(request));
  }
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  if (!self.navigator.onLine || Date.now() < offlineUntil) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
  }
  try {
    const response = await withTimeout(fetch(request, { cache: "no-store" }), NETWORK_TIMEOUT);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (error) {
    offlineUntil = Date.now() + OFFLINE_GRACE;
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok || response.type === "opaque") await cache.put(request, response.clone());
  return response;
}

function withTimeout(promise, timeout) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), timeout);
    promise.then((value) => { clearTimeout(timer); resolve(value); }, (error) => { clearTimeout(timer); reject(error); });
  });
}
