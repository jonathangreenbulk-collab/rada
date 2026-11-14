/* Rada — modo nube. Publicada en GitHub Pages, la app baja los datos cifrados de la rama 'datos'
   y los descifra en el propio teléfono (AES-256-GCM sobre zlib, igual que scripts/cifrado.py).
   La clave vive solo en este navegador: se pasa una vez escaneando el QR que muestra la PC. */
(function () {
  "use strict";
  var activa = /\.github\.io$/i.test(location.hostname);
  var CLAVE = "rada.clave", MAGIA = "RADA1";
  var caja = null, flujo = null;

  function leer() { try { return localStorage.getItem(CLAVE) || ""; } catch (e) { return ""; } }
  function guardar(k) { try { localStorage.setItem(CLAVE, k); return true; } catch (e) { return false; } }
  function olvidar() { try { localStorage.removeItem(CLAVE); } catch (e) {} }

  function bytes(k) {                      // base64 "urlsafe" (como la genera Python) → 32 bytes
    var s = String(k).trim().replace(/=+$/, "").replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function valida(k) {
    if (!/^[A-Za-z0-9_-]{43}=?$/.test(String(k || "").trim())) return false;
    try { return bytes(k).length === 32; } catch (e) { return false; }
  }

  /* https://<usuario>.github.io/<repo>/ → rama 'datos' del mismo repositorio; no hay nombres escritos aquí */
  function urlDatos() {
    return "https://raw.githubusercontent.com/" + location.hostname.split(".")[0] + "/" +
      location.pathname.split("/")[1] + "/datos/estado.enc";
  }

  function descifrar(buf, clave, etiqueta) {
    var b = new Uint8Array(buf), n = MAGIA.length;
    for (var i = 0; i < n; i++) if (b[i] !== MAGIA.charCodeAt(i)) return Promise.reject(new Error("formato desconocido"));
    return crypto.subtle.importKey("raw", bytes(clave), "AES-GCM", false, ["decrypt"])
      .then(function (k) {
        return crypto.subtle.decrypt({ name: "AES-GCM", iv: b.slice(n, n + 12),
          additionalData: new TextEncoder().encode(etiqueta) }, k, b.slice(n + 12));
      })
      .then(function (plano) {
        return new Response(new Blob([plano]).stream().pipeThrough(new DecompressionStream("deflate"))).text();
      })
      .then(JSON.parse);
  }

  function cargar(cb) {
    var k = leer();
    if (!valida(k)) { pedir(""); cb(null); return; }
    fetch(urlDatos() + "?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); })
      .then(function (buf) { return descifrar(buf, k, "estado"); })
      .then(function (d) { cerrar(); cb(d); })
      .catch(function (e) {
        if (e && e.name === "OperationError") {   // la etiqueta GCM no cuadra: clave equivocada
          olvidar();
          pedir("Esa clave no abre los datos. Vuelve a escanear el código de la PC.");
        }
        cb(null);
      });
  }

  /* ---------------------------------------------------------------- pantalla para vincular */
  var CSS =
    ".llave{position:fixed;inset:0;z-index:50;background:var(--papel);overflow-y:auto;display:grid;place-items:center;" +
      "padding:max(16px,env(safe-area-inset-top)) max(16px,env(safe-area-inset-right)) max(16px,env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left))}" +
    ".llave[hidden]{display:none}" +
    ".llave-caja{width:100%;max-width:420px;display:grid;gap:14px}" +
    ".llave-caja h2{font:700 1.8rem/1.05 var(--f-display);letter-spacing:.02em;margin:0}" +
    ".llave-caja p{margin:0;color:var(--tinta-2);line-height:1.45}" +
    ".llave-caja .llave-error{color:var(--magenta);font-weight:600}" +
    ".llave-video{width:100%;aspect-ratio:1;object-fit:cover;border-radius:14px;background:#000}" +
    ".llave-principal{justify-content:center;padding:12px 16px;font-size:.95rem;background:var(--tinta);color:var(--papel);border-color:var(--tinta)}" +
    ".llave-mano summary{cursor:pointer;color:var(--tinta-2);font-size:.85rem}" +
    ".llave-fila{display:flex;gap:8px;margin-top:10px}" +
    ".llave-fila input{flex:1;min-width:0;border:1px solid var(--linea);border-radius:10px;padding:9px 11px;" +
      "font:400 .9rem var(--f-dato);background:var(--superficie);color:var(--tinta)}";

  function pedir(msg) {
    if (!caja) {
      var st = document.createElement("style");
      st.textContent = CSS;
      document.head.appendChild(st);
      caja = document.createElement("div");
      caja.className = "llave";
      caja.setAttribute("role", "dialog");
      caja.setAttribute("aria-modal", "true");
      caja.setAttribute("aria-labelledby", "llave-t");
      caja.innerHTML =
        '<div class="llave-caja">' +
          '<h2 id="llave-t">Vincular este teléfono</h2>' +
          '<p>Los datos viajan cifrados. Para abrirlos, este teléfono necesita la clave una sola vez: ' +
          'escanea desde aquí el código que te muestra la PC.</p>' +
          '<p class="llave-error" hidden></p>' +
          '<video class="llave-video" playsinline muted hidden></video>' +
          '<button class="boton llave-principal" type="button" data-llave="escanear">Escanear código</button>' +
          '<details class="llave-mano"><summary>Pegar la clave a mano</summary>' +
            '<div class="llave-fila"><input type="password" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="Clave">' +
            '<button class="boton" type="button" data-llave="guardar">Guardar</button></div>' +
          '</details>' +
        '</div>';
      document.body.appendChild(caja);
      caja.addEventListener("click", function (ev) {
        var a = ev.target.closest("[data-llave]");
        if (!a) return;
        if (a.getAttribute("data-llave") === "escanear") escanear();
        else aceptar(caja.querySelector("input").value);
      });
    }
    var err = caja.querySelector(".llave-error");
    err.textContent = msg || "";
    err.hidden = !msg;
    caja.hidden = false;
  }
  function cerrar() { parar(); if (caja) caja.hidden = true; }

  function aceptar(k) {
    k = String(k || "").trim();
    if (!valida(k)) { pedir("Eso no parece una clave de Rada."); return; }
    parar();
    if (!guardar(k)) { pedir("Este navegador no deja guardar la clave (¿modo incógnito?)."); return; }
    location.reload();
  }

  function parar() {
    if (flujo) { flujo.getTracks().forEach(function (t) { t.stop(); }); flujo = null; }
    if (caja) caja.querySelector("video").hidden = true;
  }

  function escanear() {
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices) {
      pedir("Este navegador no lee códigos QR. Abre la página en Chrome o pega la clave a mano.");
      return;
    }
    var video = caja.querySelector("video"), det;
    try { det = new window.BarcodeDetector({ formats: ["qr_code"] }); }
    catch (e) { pedir("Este navegador no lee códigos QR. Abre la página en Chrome o pega la clave a mano."); return; }
    function buscar() {
      if (!flujo) return;
      det.detect(video).then(function (cs) {
        var k = cs.map(function (c) { return String(c.rawValue || "").trim(); }).filter(valida)[0];
        if (k) aceptar(k); else setTimeout(buscar, 250);
      }).catch(function () { setTimeout(buscar, 400); });
    }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(function (s) { parar(); flujo = s; video.srcObject = s; video.hidden = false; return video.play(); })
      .then(buscar)
      .catch(function () {
        parar();
        pedir("No se pudo abrir la cámara. Dale permiso de cámara a esta página e inténtalo otra vez.");
      });
  }

  /* Sin señal: la app y los últimos datos descargados quedan guardados en el teléfono */
  if (activa && "serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register("sw.js").catch(function () {}); });
  }

  window.RadaNube = { activa: activa, cargar: cargar };
})();
