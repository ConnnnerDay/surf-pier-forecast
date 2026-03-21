// Service Worker for Surf & Pier Fishing Forecast
// v4: Push notifications + geolocation-driven condition alerts.
//     Navigate requests fall back to a branded offline page on network failure.
//     HTML pages are never cached — they embed session-specific CSRF tokens.
var CACHE_NAME = 'fishforecast-v4';
var OFFLINE_URL = '/static/offline.html';
var PRECACHE = [
  '/static/style.css',
  '/static/icons/icon-192.svg',
  OFFLINE_URL,
];

// ── Push notification handler ─────────────────────────────────────────────
self.addEventListener('push', function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  var title = data.title || 'Surf & Pier Forecast';
  var options = {
    body: data.body || 'Check your fishing conditions.',
    icon: '/static/icons/icon-192.svg',
    badge: '/static/icons/icon-192.svg',
    tag: data.tag || 'fish-forecast',
    data: { url: data.url || '/' },
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Open the app (or focus the existing tab) when a notification is tapped.
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if (c.url.indexOf(self.location.origin) === 0 && 'focus' in c) {
          return c.navigate(target).then(function (cl) { return cl.focus(); });
        }
      }
      return clients.openWindow(target);
    })
  );
});

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
