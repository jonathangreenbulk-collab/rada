"""Lectura de las páginas públicas de Maritime Optima (robots.txt: Allow /).

- Página de puerto  -> buques en puerto (solo una selección de ~10) con hora de llegada.
- Ficha de buque     -> zona/puerto actual, estado AIS, de dónde salió y cuándo,
                        hacia dónde va (sin ETA: está bloqueado), últimas escalas.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time
import urllib.error
import urllib.request

BASE = "https://maritimeoptima.com/public"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_MESES = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def descargar(url: str, reintentos: int = 2, pausa: float = 3.0) -> str | None:
    for intento in range(reintentos + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if intento == reintentos:
                raise
        except Exception:
            if intento == reintentos:
                raise
        time.sleep(pausa * (intento + 1))
    return None


def _texto(raw: str) -> str:
    """Texto visible en una sola línea (espacios normalizados)."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", " ", raw, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def _fecha_corta(s: str, ahora: dt.datetime | None = None) -> str | None:
    """'10th Nov 04:26 UTC' (sin año) -> ISO UTC. Si cae en el futuro lejano, es del año pasado."""
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)? (\w{3}) (\d{2}):(\d{2})", s.strip())
    if not m:
        return None
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    d, mes, h, mi = int(m[1]), _MESES.get(m[2]), int(m[3]), int(m[4])
    if not mes:
        return None
    f = dt.datetime(ahora.year, mes, d, h, mi, tzinfo=dt.timezone.utc)
    if f > ahora + dt.timedelta(days=60):
        f = f.replace(year=f.year - 1)
    return f.isoformat()


def _fecha_larga(s: str) -> str | None:
    """'Thu, 05 Nov 2026 22:38:10 UTC' -> ISO UTC."""
    try:
        f = dt.datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S UTC")
        return f.replace(tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------- puerto
def leer_puerto(locode: str) -> dict:
    raw = descargar(url_puerto(locode))
    if raw is None:
        return {"locode": locode, "error": "no encontrado"}
    return parsear_puerto(raw, locode)


MARCA_PUERTO = "Vessels in port"        # si no aparece, la página no es la esperada
MARCA_BUQUE = "Where is the vessel?"


def url_puerto(locode: str) -> str:
    return f"{BASE}/ports/pages/{locode}"


def parsear_puerto(raw: str, locode: str) -> dict:
    txt = _texto(raw)
    en_puerto = re.search(r"Vessels in port: (\d+)", txt)
    salidos = re.search(r"Recently departed: (\d+)", txt)

    buques = []
    # Cada fila: <a href=".../vessels/pages/imo:X/mmsi:Y/NOMBRE.html">NOMBRE</a> ... segmento ... fecha
    tabla = raw[raw.find("Vessels in port</"):] if "Vessels in port</" in raw else raw
    filas = re.findall(r"<tr.*?</tr>", tabla, flags=re.S)
    for fila in filas:
        celdas = [_texto(c) for c in re.findall(r"<td.*?</td>", fila, flags=re.S)]
        if len(celdas) < 3:
            continue
        link = re.search(r"vessels/pages/imo:(\d+)/mmsi:(\d+)/([^\"]+)\.html", fila)
        buques.append({
            "nombre": celdas[0],
            "tipo": celdas[1],
            "llegada_utc": _fecha_larga(celdas[2]),
            "imo": link[1] if link else None,
            "mmsi": link[2] if link else None,
            "ficha": f"{BASE}/vessels/pages/imo:{link[1]}/mmsi:{link[2]}/{link[3]}.html" if link else None,
        })
    return {
        "locode": locode,
        "total_en_puerto": int(en_puerto[1]) if en_puerto else None,
        "zarpados_recientes": int(salidos[1]) if salidos else None,
        "buques_en_puerto_muestra": buques,
    }


# ---------------------------------------------------------------- buque
def leer_buque(url_ficha: str) -> dict | None:
    raw = descargar(url_ficha)
    if raw is None:
        return None
    return parsear_buque(raw, url_ficha)


def parsear_buque(raw: str, url_ficha: str) -> dict:
    t = _texto(raw)
    b: dict = {"ficha": url_ficha}
    m = re.search(r"imo:(\d+)/mmsi:(\d+)", url_ficha)
    if m:
        b["imo"], b["mmsi"] = m[1], m[2]

    m = re.search(r"Where is the vessel\? (.+?) is currently in (?:the )?(.+?) and reports its navigational "
                  r"status as (.+?) , based on AIS data received (.+?) \.", t)
    if m:
        b["nombre"], b["zona_actual"], b["estado_ais"], b["dato_ais_hace"] = m[1], m[2], m[3], m[4]

    m = re.search(r"Current voyage information .+? departed from (.+?) on (\d{1,2}\w{2} \w{3} \d{2}:\d{2}) UTC "
                  r"and is heading to (.+?) , (.+?)\.", t)
    if m:
        b["salio_de"], b["salio_utc"] = m[1], _fecha_corta(m[2])
        b["destino"], b["destino_pais"] = m[3], m[4]
    elif re.search(r"has no reported destination", t):
        b["destino"] = None

    m = re.search(r"Last port calls Port name Arrival time Departure time (.+?) View vessel", t)
    escalas = []
    if m:
        sin_horas = re.sub(r"\d{1,2}(?:st|nd|rd|th) \w{3} \d{2}:\d{2} UTC|--", " ", m[1])
        for p, resto in re.findall(r"([^()]+?) \((\w{2})\)", sin_horas):
            escalas.append({"puerto": p.strip(), "pais": resto})
        # Solo la primera escala trae horas en la versión gratis
        h = re.search(r"\(\w{2}\) (\d{1,2}\w{2} \w{3} \d{2}:\d{2}) UTC (?:(\d{1,2}\w{2} \w{3} \d{2}:\d{2}) UTC|--)", m[1])
        if h and escalas:
            escalas[0]["llegada_utc"] = _fecha_corta(h[1])
            escalas[0]["salida_utc"] = _fecha_corta(h[2]) if h[2] else None
    b["ultimas_escalas"] = escalas

    m = re.search(r"\(IMO \d+\) is an? (.+?) vessel built in (\d{4}) and sailing under the flag of (.+?) \. "
                  r"The vessel has an overall length \(LOA\) of (\d+) meters", t)
    if m:
        b["tipo"], b["construido"], b["bandera"], b["eslora_m"] = m[1], int(m[2]), m[3], int(m[4])
    return b
