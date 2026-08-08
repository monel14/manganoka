const CACHE_NAME = 'manganoka-v2';
const ASSETS = [
    '/',
    '/static/style.css',
    '/static/theme.js',
    '/static/noka_logo.svg',
    '/static/noka_loader.svg',
    '/static/noka_lost.svg'
];

// Installation du service worker et mise en cache des actifs essentiels
self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS);
        }).then(() => self.skipWaiting())
    );
});

// Activation et nettoyage des anciens caches
self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.map((key) => {
                    if (key !== CACHE_NAME) {
                        return caches.delete(key);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Stratégie de mise en cache : Réseau d'abord, puis Cache si hors ligne
self.addEventListener('fetch', (e) => {
    // Ne pas intercepter les requêtes non-GET ou externes
    if (e.request.method !== 'GET' || !e.request.url.startsWith(self.location.origin)) {
        return;
    }
    
    const url = new URL(e.request.url);
    
    // Skip API calls and image proxy
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/img-proxy')) {
        return;
    }
    
    e.respondWith(
        fetch(e.request)
            .then((response) => {
                // Clone pour mise en cache
                const responseClone = response.clone();
                
                // Mettre en cache les pages visitées et images
                if (response.ok && (
                    url.pathname.startsWith('/read/') ||
                    url.pathname.startsWith('/manga/') ||
                    url.pathname.includes('/static/') ||
                    url.pathname.match(/\.(webp|jpg|jpeg|png|svg)$/)
                )) {
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(e.request, responseClone);
                    });
                }
                
                return response;
            })
            .catch(() => {
                // Si réseau indisponible, chercher dans le cache
                return caches.match(e.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    
                    // Fallback pour pages HTML
                    if (e.request.headers.get('accept').includes('text/html')) {
                        return caches.match('/');
                    }
                    
                    // Fallback pour images
                    if (e.request.headers.get('accept').includes('image')) {
                        return caches.match('/static/noka_lost.svg');
                    }
                });
            })
    );
});
