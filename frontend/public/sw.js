// Service Worker: App-Hülle zwischenspeichern und „Teilen mit SmartArchive“ annehmen.
// Dokumente und API-Antworten werden bewusst NICHT zwischengespeichert.
const SHELL = "sa-shell-v2";
const SHARE = "sa-share";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/icon.svg"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== SHARE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;

  if (event.request.method === "POST" && url.pathname === "/share-target") {
    event.respondWith((async () => {
      const form = await event.request.formData();
      const cache = await caches.open(SHARE);
      let i = 0;
      for (const file of form.getAll("files")) {
        if (typeof file === "string") continue;
        await cache.put(`/shared/${Date.now()}-${i++}`, new Response(file, {
          headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name || "geteilt") },
        }));
      }
      return Response.redirect("/#/teilen", 303);
    })());
    return;
  }

  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;

  // Netz zuerst, damit Updates sofort ankommen; ohne Netz die gespeicherte Hülle.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok && (url.pathname === "/" || url.pathname.startsWith("/assets/"))) {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(event.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(event.request).then((r) => r || caches.match("/")))
  );
});
