/* Home Hub service worker — offline app shell + fast static caching.
   Served from / (root scope) via a main.py route so it controls the whole app.
   Note: service workers only run in a SECURE CONTEXT (HTTPS or localhost) — over
   plain http://<lan-ip> the browser won't register this, which is expected. */
const VERSION = 'homehub-v1';
const SHELL = VERSION + '-shell';
const PRECACHE = [
  '/',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon-180.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (e) => { if (e.data === 'skipWaiting') self.skipWaiting(); });

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                         // never cache mutations
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;          // only same-origin
  if (url.pathname.startsWith('/api/') || url.pathname === '/healthz') return;  // dynamic/auth: network only

  if (req.mode === 'navigate') {                            // app shell: network-first, offline fallback
    e.respondWith(
      fetch(req)
        .then((res) => { const copy = res.clone(); caches.open(SHELL).then((c) => c.put('/', copy)); return res; })
        .catch(() => caches.match('/', { ignoreSearch: true }))
    );
    return;
  }

  // static assets (/static/*, generated/studio files): stale-while-revalidate
  e.respondWith(
    caches.open(SHELL).then((cache) =>
      cache.match(req).then((cached) => {
        const net = fetch(req).then((res) => { if (res && res.ok) cache.put(req, res.clone()); return res; }).catch(() => cached);
        return cached || net;
      })
    )
  );
});
