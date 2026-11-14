/* Rada — lógica de presentación compartida por el widget de escritorio y la app. */
(function () {
  "use strict";
  var fFecha, fDia, fHora, PUERTOS = {}, CORTO = {};
  function formateadores(tz) {
    fFecha = new Intl.DateTimeFormat("es", { timeZone: tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
    fDia = new Intl.DateTimeFormat("es", { timeZone: tz, day: "numeric", month: "short" });
    fHora = new Intl.DateTimeFormat("es", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false });
  }
  formateadores("UTC");
  /* Flota, puertos y zona horaria llegan con los datos (descifrados), no viven en el código */
  function configurar(d) {
    if (!d) return;
    try { formateadores(d.zona_horaria || "UTC"); } catch (e) { formateadores("UTC"); }
    PUERTOS = {}; CORTO = {};
    Object.keys(d.puertos || {}).forEach(function (lc) {
      PUERTOS[lc] = d.puertos[lc].nombre;
      CORTO[lc] = d.puertos[lc].corto || d.puertos[lc].nombre;
    });
    window.Rada.PUERTOS = PUERTOS;
    window.Rada.CORTO = CORTO;
    window.Rada.FLOTA = d.flota || "";
  }
  var ROMANOS = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fecha(iso) { return iso ? fFecha.format(new Date(iso)).replace(".", "") : "—"; }
  function dia(iso) { return iso ? fDia.format(new Date(iso)).replace(".", "") : "—"; }
  function hora(iso) { return iso ? fHora.format(new Date(iso)) : "—"; }
  function horas(desde, hasta) { return (new Date(hasta || Date.now()) - new Date(desde)) / 36e5; }
  function hace(iso) {
    if (!iso) return "";
    var h = horas(iso);
    if (h < 1) { var m = Math.max(1, Math.round(h * 60)); return "hace " + m + " min"; }
    if (h < 48) return "hace " + Math.round(h) + " h";
    return "hace " + Math.round(h / 24) + " d";
  }
  function duracion(iso) {           // "8 d", "5 h" (sin "hace")
    var h = horas(iso);
    return h < 48 ? Math.max(1, Math.round(h)) + " h" : Math.round(h / 24) + " d";
  }
  function hasta(iso) {
    var h = -horas(iso);
    if (h < -1) return "ETA superado";
    if (h < 1) return "llega ya";
    return h < 48 ? "en " + Math.round(h) + " h" : "en " + Math.round(h / 24) + " d";
  }
  function nombre(b) {
    return (b && b.nombre || "").replace(/ (\d)$/, function (_, n) { return " " + ROMANOS[+n]; });
  }
  function etaDe(b) { return b.eta_ais_utc || b.eta_aprox_utc || null; }

  /* Estado visual de un buque dentro de un grupo */
  function chip(tipo, b) {
    if (tipo === "en_puerto") return b.en_rada ? ["c-rada", "En rada"] : ["c-puerto", "Atracado"];
    return tipo === "vienen" ? ["c-viene", "Viene"] : ["c-zarpo", "Zarpó"];
  }
  function franja(tipo, b) {
    if (tipo === "en_puerto") return b.en_rada ? "f-rada" : "f-puerto";
    return tipo === "vienen" ? "f-viene" : "f-zarpo";
  }
  function resumen(tipo, b) {        // una línea corta (widget)
    if (tipo === "vienen") {
      if (b.eta_ais_utc) return "ETA " + fecha(b.eta_ais_utc);
      if (b.eta_aprox_utc) return "ETA ≈ " + fecha(b.eta_aprox_utc);
      return "de " + (b.salio_de || "?");
    }
    if (tipo === "en_puerto") return (b.en_rada ? "en rada " : "atracado ") + (b.llegada_utc ? duracion(b.llegada_utc) : "");
    return "zarpó " + hace(b.salio_utc) + (b.destino ? " → " + b.destino : "");
  }

  /* Frase de un movimiento (evento) */
  function evento(e) {
    var p = PUERTOS[e.puerto] || e.puerto || "";
    var n = "<b>" + esc(nombre(e)) + "</b>";
    var t = e.hora_utc || e.utc;
    switch (e.tipo) {
      case "rumbo": return { clase: "p-viene", html: n + " puso rumbo a " + esc(p) + (e.origen ? " desde " + esc(e.origen) : ""), cuando: t };
      case "fondeo": return { clase: "p-rada", html: n + " fondeó en la rada de " + esc(p), cuando: t };
      case "atraque": return { clase: "p-puerto", html: n + " atracó en " + esc(p) + (e.aprox ? " (hora aprox.)" : ""), cuando: t };
      case "zarpe": return { clase: "p-zarpo", html: n + " zarpó de " + esc(p) + (e.destino ? " rumbo a " + esc(e.destino) : ""), cuando: t };
      case "eta": return { clase: "p-falta", html: n + ": nuevo ETA a " + esc(p) + " " + fecha(e.eta_utc) + " (antes " + fecha(e.eta_previa_utc) + ")", cuando: e.utc };
      default: return { clase: "p-zarpo", html: n + " " + esc(e.tipo), cuando: t };
    }
  }

  /* Antigüedad del conjunto de datos */
  function frescura(d) {
    if (!d || !d.generado_utc) return { texto: "sin datos", viejo: true };
    var h = horas(d.generado_utc);
    var limite = ((d.cada_min || 60) / 60) * 2.5;
    return { texto: "act. " + hace(d.generado_utc), viejo: h > limite, desde: hora(d.generado_utc) };
  }

  /* Carga de datos para la app: en GitHub Pages, desde la nube (nube.js); en la PC, estado.json por HTTP
     y, si no se puede (archivo local), estado.js */
  function cargar(cb) {
    if (window.RadaNube && window.RadaNube.activa) return window.RadaNube.cargar(cb);
    var hecho = false;
    function porScript() {
      var s = document.createElement("script");
      s.src = "estado.js?t=" + Date.now();
      s.onload = function () { s.remove(); cb(window.DATOS || null); };
      s.onerror = function () { s.remove(); cb(null); };
      document.head.appendChild(s);
    }
    if (location.protocol === "file:") return porScript();
    fetch("estado.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { hecho = true; cb(d); })
      .catch(function () { if (!hecho) porScript(); });
  }

  window.Rada = {
    PUERTOS: PUERTOS, CORTO: CORTO, FLOTA: "", configurar: configurar, esc: esc, fecha: fecha, dia: dia, hora: hora,
    hace: hace, hasta: hasta, duracion: duracion, nombre: nombre, etaDe: etaDe, chip: chip, franja: franja,
    resumen: resumen, evento: evento, frescura: frescura, cargar: cargar
  };
})();
