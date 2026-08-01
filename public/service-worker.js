const CACHE_NAME = 'khadamati-app-shell-v1.1.1-transformation-r1';
const SHELL = [
  './',
  './index.html',
  './assets/styles/khadamati-v1.css',
  './assets/scripts/khadamati-visuals.js',
  './app-icon-192.png',
  './app-icon-512.png',
  './assets/providers/omani-electrician.webp',
  './assets/providers/omani-cleaning-team.webp',
  './assets/providers/omani-ac-technician.webp',
  './assets/providers/omani-moving-team.webp',
  './assets/providers/omani-tech-technician.webp',
  './assets/providers/omani-events-team.webp',
  './assets/providers/omani-construction-team.webp',
  './assets/providers/omani-car-technician.webp',
  './assets/providers/omani-private-tutor.webp',
  './assets/providers/omani-home-care.webp',
  './assets/providers/omani-tailor.webp',
  './assets/providers/omani-tech-company.webp',
  './assets/onboarding/core/user-service.webp',
  './assets/onboarding/core/user-direct-request.webp',
  './assets/onboarding/core/user-matching.webp',
  './assets/onboarding/core/user-track.webp',
  './assets/onboarding/core/guest-browse.webp',
  './assets/onboarding/core/guest-compare.webp',
  './assets/onboarding/core/guest-signin.webp',
  './assets/onboarding/core/guest-privacy.webp',
  './assets/onboarding/core/provider-profile.webp',
  './assets/onboarding/core/provider-opportunity.webp',
  './assets/onboarding/core/provider-availability.webp',
  './assets/onboarding/core/provider-offer.webp',
  './assets/onboarding/core/company-profile.webp',
  './assets/onboarding/core/company-dispatch.webp',
  './assets/onboarding/core/company-analytics.webp',
  './assets/onboarding/core/company-team.webp',
  './assets/ads/campaigns/home-services.webp',
  './assets/ads/campaigns/nearby-services.webp',
  './assets/ads/campaigns/business-services.webp',
  './vendor/leaflet.css',
  './vendor/leaflet.js'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  const privatePath = /\/(api|media|uploads)\//.test(url.pathname);
  if (url.origin !== self.location.origin || privatePath) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
    return;
  }
  const acceptsHtml = event.request.headers.get('accept')?.includes('text/html');
  if (event.request.mode === 'navigate' || acceptsHtml) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .then(response => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put('./index.html', copy)).catch(() => {});
          return response;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok && response.type === 'basic') {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const route = event.notification.data?.route || './';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      const open = clients.find(client => 'focus' in client);
      if (open) {
        open.postMessage({ type: 'KHADAMATI_NOTIFICATION', route });
        return open.focus();
      }
      return self.clients.openWindow(route);
    })
  );
});

self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch (_) { payload = { body: event.data?.text() || '' }; }
  event.waitUntil(
    self.registration.showNotification(payload.title || 'خدماتي', {
      body: payload.body || payload.message || '',
      icon: './app-icon-192.png',
      badge: './app-icon-192.png',
      tag: payload.tag || payload.id || 'khadamati',
      data: { route: payload.route || './', notificationId: payload.id || '' }
    })
  );
});
