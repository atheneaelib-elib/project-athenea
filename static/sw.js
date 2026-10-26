const CACHE_NAME = 'ebook-library-v2';
const STATIC_ASSETS = [
  '/',
  '/static/placeholder.svg',
  '/static/manifest.json',
  '/static/js/jszip.min.js',
  '/static/js/epub.min.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('PWA Precache failed for some assets:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // DO NOT intercept raw book files, downloads, or API calls — let browser handle them directly
  if (url.pathname.startsWith('/raw/') || 
      url.pathname.startsWith('/download/') || 
      url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/opds')) {
    return;
  }

  // Cache static assets with cache-first strategy
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Network-first with cache fallback for HTML pages
  event.respondWith(
    fetch(event.request).catch(() => {
      return caches.match(event.request);
    })
  );
});
