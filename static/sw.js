const CACHE_NAME = 'manganoka-v1';
const ASSETS = [
    '/',
    '/static/style.css',
    '/static/theme.js',
    '/static/noka_logo.svg'
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
    
    e.respondWith(
        fetch(e.request)
            .then((response) => {
                // Mettre en cache la réponse réussie de nos fichiers statiques
                if (response.status === 200 && e.request.url.includes('/static/')) {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(e.request, copy));
                }
                return response;
            })
            .catch(() => {
                // Si réseau indisponible, chercher dans le cache
                return caches.match(e.request);
            })
    );
});
