/* ============================================================================
   Opteia Kanban — service worker
   ----------------------------------------------------------------------------
   Strategy:
     - Navigations (HTML pages): network-first  -> fresh server-rendered pages,
       fall back to cache when offline, then to offline.html.
     - Static assets (css/js/img/fonts): stale-while-revalidate.
     - NEVER cache /jsonrpc.php (the Kanboard API) or any non-GET request
       (mutations: task moves, comments, edits) — these must always hit origin.
     - Cross-origin requests pass through untouched (Cloudflare, fonts).
   Version-bump CACHE on every meaningful deploy; skipWaiting() + clients.claim()
   make the new SW take over immediately.
   ========================================================================== */

const CACHE = 'opteia-kb-v1';
const OFFLINE_URL = '/plugins/OpteiaSkin/Asset/pwa/offline.html';
const PRECACHE = [OFFLINE_URL];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE)
            .then((c) => c.addAll(PRECACHE))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const req = event.request;

    // Only GET is cacheable. Non-GET (POST/PUT/DELETE = Kanboard mutations)
    // and the JSON-RPC API must always go straight to the network.
    if (req.method !== 'GET') return;

    const url = new URL(req.url);

    // Cross-origin (Cloudflare, external fonts) — don't intercept.
    if (url.origin !== self.location.origin) return;

    // Never cache the API.
    if (url.pathname === '/jsonrpc.php') return;

    // Don't cache the service worker itself or the manifest.
    if (url.pathname.endsWith('/sw.js') || url.pathname.endsWith('/manifest.json')) return;

    // ---- Navigations: network-first ----
    if (req.mode === 'navigate') {
        event.respondWith(
            fetch(req)
                .then((res) => {
                    if (res && res.status === 200 && res.type === 'basic') {
                        const copy = res.clone();
                        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
                    }
                    return res;
                })
                .catch(() =>
                    caches.match(req).then((cached) => cached || caches.match(OFFLINE_URL))
                )
        );
        return;
    }

    // ---- Static assets: stale-while-revalidate ----
    event.respondWith(
        caches.match(req).then((cached) => {
            const network = fetch(req)
                .then((res) => {
                    if (res && res.status === 200 && res.type === 'basic') {
                        const copy = res.clone();
                        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
                    }
                    return res;
                })
                .catch(() => cached);
            return cached || network;
        })
    );
});
