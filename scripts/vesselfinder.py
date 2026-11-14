"""Ficha pública de buque en VesselFinder: destino y ETA declarados por el buque (AIS).

robots.txt permite /vessels/details/. Se usa solo para los pocos buques que vienen
(1 petición por buque cada pocas horas), nunca para barridos.
"""
from __future__ import annotations

import datetime as dt
import re

from maritime_optima import _texto

BASE = "https://www.vesselfinder.com/vessels/details/"
MARCA = "IMO / MMSI"   # si no aparece, la página no es la esperada
_MESES = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def url_buque(imo: str | int) -> str:
    return f"{BASE}{imo}"


def _eta_utc(s: str, ahora: dt.datetime) -> str | None:
    """'Nov 16, 11:00' (UTC, sin año) -> ISO. El ETA mira al futuro: si cae >30 días atrás, es del año siguiente."""
    m = re.match(r"(\w{3}) (\d{1,2}), (\d{2}):(\d{2})", s)
    if not m or m[1] not in _MESES:
        return None
    f = dt.datetime(ahora.year, _MESES[m[1]], int(m[2]), int(m[3]), int(m[4]), tzinfo=dt.timezone.utc)
    if f < ahora - dt.timedelta(days=30):
        f = f.replace(year=f.year + 1)
    return f.isoformat()


def parsear(raw: str, url: str, ahora: dt.datetime | None = None) -> dict:
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    r: dict = {"vf_ficha": url}
    m = re.search(r'vilabel">Destination</div>.*?(?:href="/ports/([A-Z]{5})\d*">([^<]+)</a>|_value">\s*([^<]+?)\s*<)',
                  raw, flags=re.S)
    if m:
        r["vf_destino_locode"] = m[1]
        r["vf_destino"] = (m[2] or m[3] or "").strip()
    t = _texto(raw)
    m = re.search(r"ETA: (\w{3} \d{1,2}, \d{2}:\d{2})", t)
    r["eta_ais_utc"] = _eta_utc(m[1], ahora) if m else None
    m = re.search(r"Position Received (.+? ago)", t)
    if m:
        r["vf_posicion_hace"] = m[1]
    m = re.search(r"Current draught ([\d.]+) m", t)
    if m:
        r["calado_actual_m"] = float(m[1])
    return r
