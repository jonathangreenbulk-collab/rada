"""Reglas de negocio de Rada (una sola fuente de verdad para la nube, el widget y la app).

Nada específico vive en el código: puertos, flota, región cercana, distancias y traducciones
llegan con configurar(cfg), desde la configuración cifrada.
"""
from __future__ import annotations

import datetime as dt
import re

DIAS_ZARPE = 10                 # cuánto tiempo sigue un buque en "Zarparon"

PUERTOS: dict[str, str] = {}    # código de puerto -> nombre
PREFIJO_FLOTA = ""
MILLAS: dict[str, dict[str, int]] = {}
NUDOS = 14
_PAISES_REGION: set[str] = set()
_CLAVES_REGION: tuple[str, ...] = ()
_ZONAS: list[tuple[str, str]] = []
_ZEE: dict[str, str] = {}

ESTADOS = {
    "moored": "Atracado",
    "at anchor": "Fondeado",
    "under way using engine": "Navegando",
    "under way sailing": "Navegando",
    "restricted manoeuvrability": "Maniobra restringida",
    "restricted maneuverability": "Maniobra restringida",
    "constrained by her draught": "Restringido por calado",
    "not under command": "Sin gobierno",
    "aground": "Varado",
}


def configurar(cfg: dict):
    global PREFIJO_FLOTA, NUDOS, _PAISES_REGION, _CLAVES_REGION
    PUERTOS.clear()
    PUERTOS.update({lc: p["nombre"] for lc, p in cfg["puertos"].items()})
    PREFIJO_FLOTA = cfg["flota"]["prefijo"]
    MILLAS.clear()
    MILLAS.update(cfg.get("millas", {}))
    NUDOS = cfg.get("nudos", 14)
    region = cfg.get("region_cercana", {})
    _PAISES_REGION = set(region.get("paises", []))
    _CLAVES_REGION = tuple(region.get("claves", []))
    _ZONAS[:] = [tuple(par) for par in cfg.get("traducciones_zona", [])]
    _ZEE.clear()
    _ZEE.update(cfg.get("zee", {}))


def de_la_flota(nombre: str | None) -> bool:
    return bool(PREFIJO_FLOTA) and (nombre or "").startswith(PREFIJO_FLOTA)


def puerto_de(texto: str | None) -> str | None:
    t = texto or ""
    for lc, nombre in PUERTOS.items():
        if nombre in t:
            return lc
    return None


def en_rada(mo: dict) -> bool:
    return (mo.get("zona_actual") or "").startswith("an anchorage") or \
        (mo.get("estado_ais") or "").lower() == "at anchor"


def donde(mo: dict) -> str | None:
    z = mo.get("zona_actual") or ""
    m = re.match(r"(.+?) Exclusive Economic Zone(?: \((.+)\))?$", z)
    if m:
        z = f"ZEE de {_ZEE.get(m[1], m[1])}" + (f" ({m[2]})" if m[2] else "")
    for en, es in _ZONAS:
        z = z.replace(en, es)
    return z or None


def estado_es(mo: dict) -> str | None:
    e = mo.get("estado_ais")
    return ESTADOS.get((e or "").lower(), e)


def relacion(mo: dict, ahora: dt.datetime) -> tuple[str | None, str | None]:
    """(tipo, código de puerto): tipo en rada | atracado | viene | zarpo | None."""
    aqui = puerto_de(mo.get("zona_actual"))
    if aqui:
        return ("rada" if en_rada(mo) else "atracado", aqui)
    va = puerto_de(mo.get("destino"))
    if va:
        return ("viene", va)
    salio = puerto_de(mo.get("salio_de"))
    if salio and mo.get("salio_utc"):
        if ahora - dt.datetime.fromisoformat(mo["salio_utc"]) <= dt.timedelta(days=DIAS_ZARPE):
            return ("zarpo", salio)
    return (None, None)


def region(mo: dict) -> str:
    """'cercana' si el buque navega o escala en la región de interés (se revisa más a menudo)."""
    textos = [mo.get("zona_actual") or "", mo.get("destino_pais") or "", mo.get("destino") or ""]
    if any(k in t for t in textos for k in _CLAVES_REGION):
        return "cercana"
    if any((e.get("pais") or "") in _PAISES_REGION for e in mo.get("ultimas_escalas") or []):
        return "cercana"
    return "resto"


def eta_aprox(mo: dict, destino: str) -> dict:
    mn = MILLAS.get(destino, {}).get(mo.get("salio_de") or "")
    if not mn or not mo.get("salio_utc"):
        return {}
    horas = mn / NUDOS
    eta = (dt.datetime.fromisoformat(mo["salio_utc"]) + dt.timedelta(hours=horas)).replace(second=0, microsecond=0)
    return {"eta_aprox_utc": eta.isoformat(), "eta_calculo": f"{mn} mn a {NUDOS} nudos ≈ {horas:.0f} h"}
