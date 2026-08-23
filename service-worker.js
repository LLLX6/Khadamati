const CACHE_PREFIX = 'khadamati-app-shell-v';
const CACHE_NAME = 'khadamati-app-shell-v1.2.0-r1';
const INDEX_CACHE_KEY = './index.html';
const PRIVATE_PATH = /\/(?:api|media|uploads)(?:\/|$)/i;
const DOWNLOAD_PATH = /\/(?:downloads?|exports?)(?:\/|$)|\.(?:pdf|zip|csv|xlsx?|docx?|pptx?)$/i;
const STATIC_ASSET_PATH = /\.(?:css|m?js|png|jpe?g|webp|svg|ico|woff2?|ttf)$/i;
const SHELL = [
  './',
  './index.html',
  './assets/styles/khadamati-v1.css',
  './assets/scripts/khadamati-visuals.js',
  './assets/scripts/khadamati-ui-state.js',
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
  './assets/onboarding/core/guest-privacy.webp',
  './assets/onboarding/core/provider-account-v2.webp',
  './assets/onboarding/core/provider-community-v2.webp',
  './assets/onboarding/core/provider-tasks-v2.webp',
  './assets/onboarding/core/provider-today-v2.webp',
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
  // A failed pre-cache must fail this installation so the last complete worker
  // remains active. The page may explicitly promote a fully installed worker.
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys
          .filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
          .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

function isBlockedPath(url) {
  return PRIVATE_PATH.test(url.pathname) || DOWNLOAD_PATH.test(url.pathname);
}

function isHtmlResponse(response) {
  const contentType = (response.headers.get('content-type') || '').toLowerCase();
  const disposition = (response.headers.get('content-disposition') || '').toLowerCase();
  return response.ok
    && contentType.includes('text/html')
    && !disposition.includes('attachment');
}

function isAppNavigation(request, url) {
  if (request.mode !== 'navigate') return false;
  if (!(request.headers.get('accept') || '').toLowerCase().includes('text/html')) return false;
  if (isBlockedPath(url)) return false;
  const lastSegment = url.pathname.split('/').pop() || '';
  return !lastSegment.includes('.') || /\.html?$/i.test(lastSegment);
}

function isCanonicalNavigation(url) {
  const scopePath = new URL(self.registration.scope).pathname;
  const indexPath = new URL(INDEX_CACHE_KEY, self.registration.scope).pathname;
  return url.pathname === scopePath || url.pathname === indexPath;
}

function isCacheableStaticResponse(response) {
  const cacheControl = (response.headers.get('cache-control') || '').toLowerCase();
  return response.ok
    && response.type === 'basic'
    && !/(?:^|,)\s*(?:no-store|private)\b/.test(cacheControl);
}

function staticCacheKey(url) {
  const keys = [...url.searchParams.keys()];
  if (keys.some(key => key !== 'v')) return '';
  if (keys.length && !/^[A-Za-z0-9._-]{1,80}$/.test(url.searchParams.get('v') || '')) return '';
  const canonical = new URL(url.href);
  canonical.search = '';
  canonical.hash = '';
  return canonical.href;
}

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || isBlockedPath(url)) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
    return;
  }
  if (isAppNavigation(event.request, url)) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .then(response => {
          if (!isHtmlResponse(response) || !isCanonicalNavigation(url)) return response;
          const copy = response.clone();
          return caches.open(CACHE_NAME)
            .then(cache => cache.put(INDEX_CACHE_KEY, copy))
            .catch(() => {})
            .then(() => response);
        })
        .catch(() => caches.match(INDEX_CACHE_KEY).then(response => response || Response.error()))
    );
    return;
  }
  if (!STATIC_ASSET_PATH.test(url.pathname)) return;
  const cacheKey = staticCacheKey(url);
  if (!cacheKey) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (!isCacheableStaticResponse(response)) return response;
        const copy = response.clone();
        return caches.open(CACHE_NAME)
          .then(cache => cache.put(cacheKey, copy))
          .catch(() => {})
          .then(() => response);
      })
      .catch(() => caches.match(cacheKey))
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const notificationId = event.notification.data?.notificationId || '';
  const route = notificationId
    ? `./#notification=${encodeURIComponent(notificationId)}`
    : (event.notification.data?.route || './');
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      const open = clients.find(client => 'focus' in client);
      if (open) {
        open.postMessage({ type: 'KHADAMATI_NOTIFICATION', route, notificationId });
        return open.focus();
      }
      return self.clients.openWindow(route);
    })
  );
});

self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch (_) { payload = { body: event.data?.text() || '' }; }
  const notificationId = payload.id || '';
  const route = notificationId
    ? `./#notification=${encodeURIComponent(notificationId)}`
    : (payload.route || './');
  event.waitUntil(
    self.registration.showNotification(payload.title || 'خدماتي', {
      body: payload.body || payload.message || '',
      icon: './app-icon-192.png',
      badge: './app-icon-192.png',
      tag: payload.tag || notificationId || 'khadamati',
      renotify: Boolean(payload.renotify),
      requireInteraction: Boolean(payload.requiresAction),
      data: { route, notificationId }
    })
  );
});
