"""Cliente HTTP educado: la única puerta de salida hacia webs de terceros.

Reglas para no molestar (y que no nos bloqueen):
  - Una petición a la vez por sitio, con pausa aleatoria entre peticiones al mismo sitio.
  - Tope de peticiones por ejecución y por día para cada sitio.
  - robots.txt revisado una vez al día: si deja de permitir la ruta, no se consulta.
  - Si el sitio responde 403/429/503 o devuelve una página que no es la esperada,
    se pausa ese sitio (1 h, 2 h, 4 h... hasta 24 h) y se siguen usando los últimos datos buenos.
  - Una sola identidad (User-Agent fijo), sin proxies ni rotaciones: si un sitio no nos quiere, se respeta.

El estado (contadores, pausas, robots) vive en un dict que el llamador guarda en memoria.json.
"""
from __future__ import annotations

import datetime as dt
import gzip
import random
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# pausa = segundos aleatorios entre dos peticiones al mismo sitio
LIMITES = {
    "maritimeoptima.com": {"por_ejecucion": 13, "por_dia": 320, "pausa": (2.5, 5.0)},
    "www.vesselfinder.com": {"por_ejecucion": 3, "por_dia": 60, "pausa": (4.0, 8.0)},
}
PAUSA_MAX_H = 24


class SitioNoDisponible(Exception):
    """El sitio está en pausa, sin presupuesto o sin tiempo en esta ejecución."""


class RespuestaRara(Exception):
    """Respuesta inesperada (formato cambiado, página de verificación, error de red)."""


def _ahora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Red:
    def __init__(self, memoria_sitios: dict, tiempo_max_s: float | None = None):
        self.m = memoria_sitios
        self.tiempo_max_s = tiempo_max_s
        self.inicio = time.monotonic()
        self.listo_en: dict[str, float] = {}      # host -> momento (monotonic) en que se puede volver a pedir
        self.en_ejecucion: dict[str, int] = {}    # host -> peticiones en esta ejecución
        self.registro: list[str] = []

    # ------------------------------------------------------------ estado por sitio
    def _sitio(self, host: str) -> dict:
        e = self.m.setdefault(host, {})
        hoy = _ahora().date().isoformat()
        if e.get("dia") != hoy:
            e["dia"], e["peticiones_hoy"] = hoy, 0
        return e

    def motivo_no_disponible(self, host: str) -> str | None:
        e, lim = self._sitio(host), LIMITES[host]
        if e.get("pausa_hasta") and dt.datetime.fromisoformat(e["pausa_hasta"]) > _ahora():
            return f"en pausa hasta {e['pausa_hasta']} ({e.get('motivo', '?')})"
        if e["peticiones_hoy"] >= lim["por_dia"]:
            return "tope diario alcanzado"
        if self.en_ejecucion.get(host, 0) >= lim["por_ejecucion"]:
            return "tope de esta ejecución alcanzado"
        if self.tiempo_max_s and time.monotonic() - self.inicio > self.tiempo_max_s:
            return "tiempo de esta ejecución agotado"
        return None

    def disponible(self, host: str) -> bool:
        return self.motivo_no_disponible(host) is None

    def segundos_hasta_listo(self, host: str) -> float:
        return max(0.0, self.listo_en.get(host, 0.0) - time.monotonic())

    def _pausar(self, host: str, motivo: str, horas: float | None = None):
        e = self._sitio(host)
        if horas is None:
            horas = min(PAUSA_MAX_H, 2 ** e.get("pausas_seguidas", 0))
            e["pausas_seguidas"] = e.get("pausas_seguidas", 0) + 1
        hasta = (_ahora() + dt.timedelta(hours=horas)).replace(microsecond=0)
        e["pausa_hasta"], e["motivo"] = hasta.isoformat(), motivo
        self.registro.append(f"PAUSA {host} {horas:g} h: {motivo}")

    def _fallo_suave(self, host: str, motivo: str):
        e = self._sitio(host)
        e["fallos_seguidos"] = e.get("fallos_seguidos", 0) + 1
        self.registro.append(f"fallo {host}: {motivo} ({e['fallos_seguidos']} seguidos)")
        if e["fallos_seguidos"] >= 3:
            self._pausar(host, f"3 fallos seguidos: {motivo}", horas=1)
            e["fallos_seguidos"] = 0

    # ------------------------------------------------------------ HTTP crudo
    def _pedir(self, url: str, timeout: float = 20) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en",
            "Accept-Encoding": "gzip",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                cuerpo, cab = r.read(), dict(r.headers)
                codigo = r.status
        except urllib.error.HTTPError as e:
            cuerpo, cab, codigo = e.read(), dict(e.headers or {}), e.code
        if (cab.get("Content-Encoding") or "").lower() == "gzip":
            cuerpo = gzip.decompress(cuerpo)
        return codigo, cab, cuerpo

    def _esperar_turno(self, host: str):
        espera = self.segundos_hasta_listo(host)
        if espera:
            time.sleep(espera)

    def _contar(self, host: str):
        e = self._sitio(host)
        e["peticiones_hoy"] += 1
        self.en_ejecucion[host] = self.en_ejecucion.get(host, 0) + 1
        a, b = LIMITES[host]["pausa"]
        self.listo_en[host] = time.monotonic() + random.uniform(a, b)

    # ------------------------------------------------------------ robots.txt
    def _robots_permite(self, host: str, url: str) -> bool:
        e = self._sitio(host)
        rob = e.get("robots") or {}
        revisado = rob.get("revisado")
        if not revisado or _ahora() - dt.datetime.fromisoformat(revisado) > dt.timedelta(hours=24):
            self._esperar_turno(host)
            codigo, _, cuerpo = self._pedir(f"https://{host}/robots.txt")
            self._contar(host)
            texto = cuerpo.decode("utf-8", "replace") if codigo == 200 else ""
            rob = {"revisado": _ahora().replace(microsecond=0).isoformat(), "texto": texto[:20000]}
            e["robots"] = rob
        rp = urllib.robotparser.RobotFileParser()
        rp.parse((rob.get("texto") or "").splitlines())
        return rp.can_fetch("*", url)

    # ------------------------------------------------------------ API pública
    def obtener(self, url: str, marcador: str | None = None) -> str | None:
        """Devuelve el HTML, o None si la página no existe (404).
        Lanza SitioNoDisponible o RespuestaRara si no se pudo (el llamador conserva lo anterior)."""
        host = urlsplit(url).hostname
        motivo = self.motivo_no_disponible(host)
        if motivo:
            raise SitioNoDisponible(motivo)
        try:
            if not self._robots_permite(host, url):
                self._pausar(host, "robots.txt ya no permite esta ruta", horas=PAUSA_MAX_H)
                raise SitioNoDisponible("robots.txt no lo permite")
            self._esperar_turno(host)
            codigo, cab, cuerpo = self._pedir(url)
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            self._contar(host)
            self._fallo_suave(host, f"red: {ex}")
            raise RespuestaRara(f"error de red: {ex}") from ex
        self._contar(host)
        e = self._sitio(host)

        if codigo == 404:
            return None
        if codigo in (401, 403, 429, 451, 503):
            retry = cab.get("Retry-After", "")
            horas = None
            if retry.isdigit():
                horas = max(1.0, min(PAUSA_MAX_H, int(retry) / 3600))
            self._pausar(host, f"HTTP {codigo}", horas)
            raise SitioNoDisponible(f"HTTP {codigo}")
        if codigo != 200:
            self._fallo_suave(host, f"HTTP {codigo}")
            raise RespuestaRara(f"HTTP {codigo}")

        texto = cuerpo.decode("utf-8", errors="replace")
        if marcador and marcador not in texto:
            self._fallo_suave(host, "página distinta a la esperada (¿verificación o rediseño?)")
            raise RespuestaRara("contenido inesperado")
        e["fallos_seguidos"] = 0
        e["pausas_seguidas"] = 0
        e["ultimo_ok"] = _ahora().replace(microsecond=0).isoformat()
        return texto
