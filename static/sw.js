// Service Worker for Surf & Pier Fishing Forecast
// v5: Static assets now served with mtime-versioned URLs (?v=<mtime>).
//     Templates use surl() helper so browsers cache CSS/JS for 1 year.
//     Unversioned style.css removed from PRECACHE — versioned URLs are
//     cached dynamically on first use via the fetch handler below.
//     Navigate requests fall back to a branded offline page on network failure.
//     HTML pages are never cached — they embed session-specific CSRF tokens.
var CACHE_NAME = 'fishforecast-v8';
var OFFLINE_URL = '/static/offline.html';
var PRECACHE = [
  '/static/icons/icon-192.svg',
  OFFLINE_URL,
];

// ── Push notification handler ─────────────────────────────────────────────
self.addEventListener('push', function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}

  // Sanitise all string fields — truncate to safe lengths and strip anything
  // non-printable.  This limits the impact of a compromised push payload.
  function safeStr(val, maxLen, fallback) {
    if (typeof val !== 'string') return fallback;
    return val.replace(/[^\x20-\x7E\u00A0-\uFFFF]/g, '').slice(0, maxLen) || fallback;
  }

  // Validate the URL: only allow same-origin paths.
  var safeUrl = '/';
  try {
    var parsed = new URL(data.url || '/', self.location.origin);
    if (parsed.origin === self.location.origin) {
      safeUrl = parsed.pathname + parsed.search + parsed.hash;
    }
  } catch (e) {}

  var title = safeStr(data.title, 80, 'Surf & Pier Forecast');
  var options = {
    body: safeStr(data.body, 200, 'Check your fishing conditions.'),
    icon: '/static/icons/icon-192.svg',
    badge: '/static/icons/icon-192.svg',
    tag: safeStr(data.tag, 40, 'fish-forecast'),
    data: { url: safeUrl },
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Open the app (or focus the existing tab) when a notification is tapped.
self.addEventListener('notificationclick', function (event) {
  event.notification.close();

  // Validate the target URL so a crafted push payload cannot redirect the
  // user to an external site or trigger a javascript: navigation.
  var rawUrl = (event.notification.data && event.notification.data.url) || '/';
  var target = '/'; // safe default
  try {
    var parsed = new URL(rawUrl, self.location.origin);
    // Only allow navigation within our own origin.
    if (parsed.origin === self.location.origin) {
      target = parsed.pathname + parsed.search + parsed.hash;
    }
  } catch (e) { /* malformed URL — fall back to '/' */ }

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

  // Stale-while-revalidate for expensive or preloaded API calls.
  // On repeat visits these are served from cache immediately; the background
  // fetch keeps the SW entry fresh so the next load gets updated data.
  var _swrApis = [
    '/api/weather/env-context',
    '/api/weather/combined-forecast',
    '/api/map/stat-cards',
  ];
  if (event.request.method === 'GET' &&
      _swrApis.some(function(p) { return event.request.url.includes(p); })) {
    // Keep the SW alive until the background cache write completes so mobile
    // browsers don't terminate the worker before the update lands.
    var bgRefresh = caches.open(CACHE_NAME).then(function(cache) {
      return fetch(event.request).then(function(response) {
        if (response.ok) cache.put(event.request, response.clone());
        return response;
      }).catch(function() { return null; });
    });
    event.waitUntil(bgRefresh);
    event.respondWith(
      caches.open(CACHE_NAME).then(function(cache) {
        return cache.match(event.request).then(function(cached) {
          if (cached) return cached;
          // No cached entry — must wait for the network response.
          return bgRefresh.then(function(r) {
            return r || new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } });
          });
        });
      })
    );
    return;
  }

  // Never intercept other API requests — always hit the network.
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
