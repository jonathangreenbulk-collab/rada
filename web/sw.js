/* Rada — service worker: primero la red; si no hay señal (o tarda más de 5 s), lo último guardado.
   Guarda la app y el archivo de datos tal como llega (cifrado). */
var CACHE = "rada-v1";
var BASE = ["./", "rada.css", "rada.js", "nube.js"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(BASE); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function redPrimero(req, clave) {
  var guardado = function () { return caches.match(clave); };
  return new Promise(function (ok, mal) {
    var listo = false;
    var t = setTimeout(function () {
      guardado().then(function (r) { if (r && !listo) { listo = true; ok(r); } });
    }, 5000);
    fetch(req).then(function (r) {
      if (r.ok) { var copia = r.clone(); caches.open(CACHE).then(function (c) { return c.put(clave, copia); }); }
      if (!listo) { listo = true; clearTimeout(t); ok(r); }
    }).catch(function () {
      clearTimeout(t);
      guardado().then(function (r) {
        if (listo) return;
        listo = true;
        if (r) ok(r); else mal(new Error("sin red"));
      });
    });
  });
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin && url.hostname !== "raw.githubusercontent.com") return;
  e.respondWith(redPrimero(req, url.origin + url.pathname));   // sin "?t=…": una sola copia por archivo
});
