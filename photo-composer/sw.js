// sw.js — Scene Composer サービスワーカー
// 役割:
//  1. アプリ本体のキャッシュ（オフラインでも開ける。ネットワーク優先で常に最新を反映）
//  2. Web Share Target: スマホの共有シートから渡された画像を一時保存してアプリへ渡す

const CACHE = 'scene-composer-v1';
const SHARED_CACHE = 'scene-composer-shared';
const SHELL = [
  './', 'index.html', 'style.css', 'app.js', 'compositor.js',
  'providers.js', 'gemini.js', 'openai.js', 'pollinations.js',
  'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE && k !== SHARED_CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // 共有シートからの画像（POST /share-target）→ 一時保存してアプリを開く
  if (e.request.method === 'POST' && url.pathname.endsWith('/share-target')) {
    e.respondWith((async () => {
      try {
        const form = await e.request.formData();
        const file = form.get('image');
        if (file) {
          const cache = await caches.open(SHARED_CACHE);
          await cache.put('shared-image',
            new Response(file, { headers: { 'Content-Type': file.type || 'image/png' } }));
        }
      } catch { /* 保存に失敗してもアプリは開く */ }
      return Response.redirect('./?shared=1', 303);
    })());
    return;
  }

  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  // ネットワーク優先（更新を確実に反映）、オフライン時はキャッシュ
  e.respondWith((async () => {
    try {
      const res = await fetch(e.request);
      const cache = await caches.open(CACHE);
      cache.put(e.request, res.clone());
      return res;
    } catch {
      const hit = await caches.match(e.request);
      return hit || Response.error();
    }
  })());
});
