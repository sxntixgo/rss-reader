const CACHE = 'rss-v1';
const PRECACHE = [
  '/',
  '/static/style.css',
  '/static/manifest.json',
  'https://unpkg.com/htmx.org@1.9.12',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE))
  );
});

self.addEventListener('activate', e => {
  // Remove old caches
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
});

self.addEventListener('fetch', e => {
  const url = e.request.url;
  // Network-first for dynamic routes
  if (url.includes('/articles') || url.includes('/vote/') ||
      url.includes('/dismiss/') || url.includes('/feeds') ||
      url.includes('/poll')) {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request))
    );
  } else {
    // Cache-first for static assets
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request))
    );
  }
});
