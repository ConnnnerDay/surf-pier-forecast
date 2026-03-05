// Service Worker for Surf & Pier Fishing Forecast
// v2: HTML navigate requests are never cached — they embed session CSRF tokens
// which go stale when the session changes, causing 400 Bad Request on form submit.
var CACHE_NAME = 'fishforecast-v2';
var PRECACHE = [
  '/static/style.css',
  '/static/icons/icon-192.svg',
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(PRECACHE);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(n) { return n !== CACHE_NAME; })
             .map(function(n) { return caches.delete(n); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function(event) {
  // Never intercept HTML navigation or API requests — always hit the network.
  // HTML pages contain session-specific CSRF tokens; caching them causes
  // "Bad Request" errors when the session changes.
  if (event.request.mode === 'navigate') return;
  if (event.request.url.includes('/api/')) return;

  // Cache-first for static assets (CSS, JS, icons, fonts).
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      return cached || fetch(event.request).then(function(response) {
        var clone = response.clone();
        caches.open(CACHE_NAME).then(function(cache) {
          cache.put(event.request, clone);
        });
        return response;
      });
    })
  );
});
