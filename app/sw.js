/*
 * Predictive Maintenance - Service Worker
 * Adapted from the Landsafe service worker, which is already deployed and
 * working on Android Chrome.
 *
 * Required for two reasons:
 *  1. Browser push notifications on Android Chrome MUST be shown by the
 *     service worker - Notification() from the page is ignored there.
 *  2. Installable PWA (add-to-home-screen) so the dashboard behaves like an
 *     app on a workshop tablet.
 */

const CACHE = 'pdm-v1';

self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  // Never cache the telemetry endpoints - stale health data is worse than none.
  if (event.request.url.includes('/api/')) return;
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAIT_WAITING') {
    self.skipWaiting();
  }
  // Show notification on behalf of the page (required on Android Chrome)
  if (event.data && event.data.type === 'notify') {
    self.registration.showNotification(event.data.title, {
      body: event.data.body,
      tag: event.data.tag || 'pdm-alert',
      renotify: true,
      icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect fill='%230d1117' width='100' height='100' rx='20'/><text y='.9em' font-size='64' x='14'>⚙️</text></svg>",
      vibrate: event.data.vibrate || [300, 120, 300, 120, 300],
      requireInteraction: !!event.data.urgent
    });
  }
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(cs => { for (const c of cs) if ('focus' in c) return c.focus(); })
      .then(f => f || self.clients.openWindow('/'))
  );
});