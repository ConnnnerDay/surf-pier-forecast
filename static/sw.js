// Service Worker for Surf & Pier Fishing Forecast
// v3: Navigate requests fall back to a branded offline page on network failure.
//     HTML pages are never cached — they embed session-specific CSRF tokens.
var CACHE_NAME = 'fishforecast-v3';
var OFFLINE_URL = '/static/offline.html';
var PRECACHE = [
  '/static/style.css',
  '/static/icons/icon-192.svg',
  OFFLINE_URL,
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
  // For HTML page navigations: always try the network.
  // If the network fails (offline), serve the pre-cached offline page.
  // HTML pages contain session-specific CSRF tokens so they must never be
  // served from the SW cache — only the dedicated offline.html is cached.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(function() {
        return caches.match(OFFLINE_URL);
      })
    );
    return;
  }

  // Never intercept API requests — always hit the network.
  if (event.request.url.includes('/api/')) return;

  // Cache-first for static assets (CSS, JS, icons, fonts).
  // Only successful responses (2xx) are written to the cache so that error
  // pages are never served from cache on subsequent offline visits.
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      return cached || fetch(event.request).then(function(response) {
        if (response.ok) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      }).catch(function() {
        // Network failure and no cache hit — return an empty offline response
        // for sub-resources so the page degrades gracefully instead of throwing.
        return new Response('', { status: 503, statusText: 'Offline' });
      });
    })
  );
});
