const CACHE = 'tai-cache-v6';
const ASSETS = [
  './',
  'index.html',
  'manifest.json',
  'vendor/jsQR.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  // Navigation (index.html) ke liye NETWORK-FIRST: pehle network, na ho toh cache.
  // Isse nayi deploy hamesha fresh load hogi (purana page kabhi pin nahi hoga).
  e.respondWith(
    fetch(e.request)
      .then(async (res) => {
        const cache = await caches.open(CACHE);
        cache.put(e.request, res.clone());
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
