const CACHE_NAME = 'manganoka-v3';
const ASSETS = [
    '/fr/',
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

    // Ne pas intercepter : API, proxy d'images, routes d'administration
    if (
        url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/img-proxy') ||
        url.pathname.startsWith('/img-cdn') ||
        url.pathname.startsWith('/admin')
    ) {
        return;
    }

    e.respondWith(
        fetch(e.request)
            .then((response) => {
                // Ne mettre en cache que les réponses valides
                if (!response.ok) return response;

                const responseClone = response.clone();

                // Mettre en cache les pages manga/reader et les assets statiques
                if (
                    url.pathname.startsWith('/fr/read/') ||
                    url.pathname.startsWith('/fr/manga/') ||
                    url.pathname.includes('/static/') ||
                    url.pathname.match(/\.(webp|jpg|jpeg|png|svg|css|js)$/)
                ) {
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
                    const accept = e.request.headers.get('accept') || '';
                    if (accept.includes('text/html')) {
                        return caches.match('/fr/').then((r) =>
                            r || new Response('Hors ligne', { status: 503, headers: { 'Content-Type': 'text/plain' } })
                        );
                    }

                    // Fallback pour images
                    if (accept.includes('image')) {
                        return caches.match('/static/noka_lost.svg').then((r) =>
                            r || new Response('', { status: 503 })
                        );
                    }

                    // Fallback générique — ne jamais retourner undefined
                    return new Response('', { status: 503 });
                });
            })
    );
});
