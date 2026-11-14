"""Actualización de Rada: decide qué consultar, consulta con educación y publica el estado (cifrado).

Corre cada 30 min (GitHub Actions) con un tope de tiempo de consultas (RADA_TIEMPO_MAX, 40 s por defecto).
Cada buque tiene un nivel según lo que está haciendo; el nivel decide cada cuánto se revisa:

  rada 1 h · viene (ETA < 24 h) 1 h · atracado 2 h · viene 3 h · zarpó 12 h · vigilado 24 h
  flota en la región cercana 48 h · resto de la flota 28 días · páginas de puerto 2 h

Configuración: claves/config.json (PC) o config.enc (repositorio, cifrado).
Memoria entre ejecuciones: memoria.enc (en la nube vive en la rama 'datos').
Con RADA_PUBLICO=1 (nube, registros públicos) solo se imprimen totales: nunca nombres ni puertos.

Uso: python -I scripts/actualizar.py --memoria memoria.enc --salida salida [--tambien-json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import cifrado  # noqa: E402
import maritime_optima as mo  # noqa: E402
import red as redmod  # noqa: E402
import reglas  # noqa: E402
import vesselfinder as vf  # noqa: E402

RAIZ = AQUI.parent
MO_HOST, VF_HOST = "maritimeoptima.com", "www.vesselfinder.com"
PUBLICO = os.environ.get("RADA_PUBLICO") == "1"

INTERVALO_MIN = {
    "puerto": 120, "rada": 60, "viene_cerca": 60, "atracado": 120, "viene": 180,
    "zarpo": 720, "vigilado": 1440, "cercana": 2880, "resto": 40320, "indice": 43200, "sitemap": 43200,
}
PRIORIDAD = ["puerto", "rada", "viene_cerca", "atracado", "viene", "zarpo", "vigilado", "cercana", "resto",
             "indice", "sitemap"]
VF_INTERVALO_MIN, VF_CERCA_MIN = 180, 60      # ETA del buque: cada 3 h; cada 1 h si llega en < 8 h
DIAS_VIGILANCIA = 60                          # tras tocar un puerto vigilado, el buque sigue vigilado 60 días
MAX_EVENTOS, MAX_HISTORIAL, MAX_EJECUCIONES = 60, 8, 48


def detalle(msg: str):
    """Mensajes con nombres: solo en la PC, nunca en los registros públicos de la nube."""
    if not PUBLICO:
        print(msg, flush=True)


def ahora_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(d: dt.datetime | None) -> str | None:
    return d.isoformat() if d else None


def de_iso(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


def desfase(clave: str, minutos: int) -> dt.timedelta:
    """Desfase estable por buque, para repartir las revisiones en el tiempo y no hacerlas todas juntas."""
    h = int(hashlib.sha1(clave.encode()).hexdigest()[:8], 16)
    return dt.timedelta(minutes=h % max(1, minutos))


# ---------------------------------------------------------------- configuración y archivos cifrados
def cargar_config(clave: bytes | None) -> dict:
    local = RAIZ / "claves" / "config.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    return json.loads(cifrado.descifrar((RAIZ / "config.enc").read_bytes(), clave, "config"))


def leer_json(ruta: pathlib.Path, clave: bytes | None, etiqueta: str):
    datos = ruta.read_bytes()
    if ruta.suffix == ".enc":
        datos = cifrado.descifrar(datos, clave, etiqueta)
    return json.loads(datos)


# ---------------------------------------------------------------- memoria
def memoria_inicial(ahora: dt.datetime) -> dict:
    """Primera vez: flota semilla (claves/flota.json, solo en la PC) con revisiones repartidas en el tiempo.
    Sin semilla, la flota se descubre leyendo el índice público de buques (una parte por ejecución)."""
    mem = {"version": 1, "creada_utc": iso(ahora), "sitios": {}, "puertos": {}, "buques": {},
           "eventos": [], "ejecuciones": [], "sitemaps": {}}
    semilla = RAIZ / "claves" / "flota.json"
    if semilla.exists():
        for f in json.loads(semilla.read_text(encoding="utf-8"))["buques"]:
            nivel_base = "cercana" if f.get("region") == "cercana" else "resto"
            b = {k: f.get(k) for k in ("nombre", "imo", "mmsi", "ficha")}
            b["region"] = nivel_base
            b["mo_utc"] = iso(ahora - desfase(f["imo"], INTERVALO_MIN[nivel_base]))
            if f.get("relacion_ve"):                    # los que ya tocan los puertos vigilados: revisar ya
                b["mo_utc"], b["visto_ve_utc"] = None, iso(ahora)
            mem["buques"][f["imo"]] = b
    return mem


def cargar_vigilados_manuales(mem: dict, cfg: dict):
    """Buques que se quieren seguir siempre (p. ej. fletados sin el prefijo de la flota): van en la configuración."""
    for v in cfg.get("vigilados", []):
        if not v.get("imo") or not v.get("ficha"):
            continue
        b = mem["buques"].setdefault(str(v["imo"]), {k: v.get(k) for k in ("nombre", "imo", "mmsi", "ficha")})
        b["manual"] = True


# ---------------------------------------------------------------- niveles y tareas
def eta_de(b: dict) -> dt.datetime | None:
    v = b.get("vf") or {}
    if v.get("eta_ais_utc"):
        return de_iso(v["eta_ais_utc"])
    rel = b.get("rel") or {}
    if rel.get("tipo") == "viene" and b.get("mo"):
        return de_iso(reglas.eta_aprox(b["mo"], rel["puerto"]).get("eta_aprox_utc"))
    return None


def nivel(b: dict, ahora: dt.datetime) -> str | None:
    if b.get("inactivo"):
        return None
    if b.get("prioridad_extra"):
        return "rada"
    tipo = (b.get("rel") or {}).get("tipo")
    if tipo in ("rada", "atracado", "zarpo"):
        return tipo
    if tipo == "viene":
        eta = eta_de(b)
        return "viene_cerca" if eta and eta - ahora < dt.timedelta(hours=24) else "viene"
    visto = de_iso(b.get("visto_ve_utc"))
    if b.get("manual") or (visto and ahora - visto < dt.timedelta(days=DIAS_VIGILANCIA)):
        return "vigilado"
    return b.get("region") or "resto"


def atraso(ultimo: str | None, intervalo_min: int, ahora: dt.datetime) -> float | None:
    """>= 1 si toca revisar (1 = justo a tiempo, 2 = el doble de tarde...). None si no toca."""
    if not ultimo:
        return 1e9
    r = (ahora - de_iso(ultimo)).total_seconds() / 60 / intervalo_min
    return r if r >= 1 else None


def planificar(mem: dict, ahora: dt.datetime) -> tuple[list, list]:
    t_mo, t_vf = [], []
    for lc in reglas.PUERTOS:
        a = atraso((mem["puertos"].get(lc) or {}).get("leido_utc"), INTERVALO_MIN["puerto"], ahora)
        if a:
            t_mo.append((PRIORIDAD.index("puerto"), -a, "puerto", lc))
    for imo, b in mem["buques"].items():
        n = nivel(b, ahora)
        if not n:
            continue
        a = atraso(b.get("mo_utc"), INTERVALO_MIN[n], ahora)
        if a:
            t_mo.append((PRIORIDAD.index(n), -a, "buque", imo))
        if n in ("viene", "viene_cerca"):
            eta = eta_de(b)
            cada = VF_CERCA_MIN if eta and eta - ahora < dt.timedelta(hours=8) else VF_INTERVALO_MIN
            a = atraso(b.get("vf_utc"), cada, ahora)
            if a:
                t_vf.append((PRIORIDAD.index(n), -a, "eta", imo))
    if reglas.PREFIJO_FLOTA:
        a = atraso(mem.get("indice_utc"), INTERVALO_MIN["indice"], ahora)
        if a:
            t_mo.append((PRIORIDAD.index("indice"), -a, "indice", ""))
        for url, s in (mem.get("sitemaps") or {}).items():
            if not s.get("leido_utc") or s["leido_utc"] < (mem.get("indice_utc") or ""):
                t_mo.append((PRIORIDAD.index("sitemap"), 0, "sitemap", url))
                break                                  # como mucho un fichero grande por ejecución
    t_mo.sort()
    t_vf.sort()
    return t_mo, t_vf


# ---------------------------------------------------------------- eventos
def evento(mem: dict, ahora: dt.datetime, tipo: str, b: dict, puerto: str | None, **extra):
    ev = {"utc": iso(ahora), "tipo": tipo, "nombre": b.get("nombre"), "imo": b.get("imo"), "puerto": puerto}
    ev.update({k: v for k, v in extra.items() if v is not None})
    mem["eventos"].insert(0, ev)
    del mem["eventos"][MAX_EVENTOS:]
    mem.setdefault("_cambios", 0)
    mem["_cambios"] += 1
    detalle(f"  EVENTO {tipo}: {b.get('nombre')} {puerto or ''} {extra or ''}")


def registrar_cambio(mem: dict, b: dict, nueva: tuple, ahora: dt.datetime, arranque: bool):
    previa = b.get("rel") or {}
    p = (previa.get("tipo"), previa.get("puerto"))
    if p == nueva:
        return
    tipo, puerto = nueva
    datos = b.get("mo") or {}

    # Cuándo empezó el nuevo estado: hora de la fuente si la hay; si no, ahora (aproximada)
    desde, aprox = ahora, True
    if tipo in ("rada", "atracado"):
        esc = next((e for e in datos.get("ultimas_escalas", []) if reglas.puerto_de(e["puerto"]) == puerto), {})
        if esc.get("llegada_utc") and not (p[0] == "rada" and tipo == "atracado"):
            desde, aprox = de_iso(esc["llegada_utc"]), False
    elif tipo in ("viene", "zarpo") and datos.get("salio_utc"):
        desde, aprox = de_iso(datos["salio_utc"]), False

    b["rel"] = {"tipo": tipo, "puerto": puerto, "desde_utc": iso(desde), "aprox": aprox}
    if tipo:
        b.setdefault("historial", []).insert(0, dict(b["rel"]))
        del b["historial"][MAX_HISTORIAL:]
    if arranque:                                       # primera ejecución: se anota el estado, sin avisos
        return

    if p[0] in ("rada", "atracado") and tipo not in ("rada", "atracado"):
        evento(mem, ahora, "zarpe", b, p[1], destino=datos.get("destino"), hora_utc=datos.get("salio_utc"))
    if tipo == "viene":
        evento(mem, ahora, "rumbo", b, puerto, origen=datos.get("salio_de"), hora_utc=datos.get("salio_utc"))
    elif tipo == "rada":
        evento(mem, ahora, "fondeo", b, puerto, hora_utc=iso(desde))
    elif tipo == "atracado":
        evento(mem, ahora, "atraque", b, puerto, hora_utc=iso(desde), aprox=aprox or None)


# ---------------------------------------------------------------- ejecución de tareas
def leer_puerto(red: redmod.Red, mem: dict, lc: str, ahora: dt.datetime):
    raw = red.obtener(mo.url_puerto(lc), marcador=mo.MARCA_PUERTO)
    if raw is None:
        return
    p = mo.parsear_puerto(raw, lc)
    previo = (mem["puertos"].get(lc) or {}).get("datos") or {}
    if (p.get("total_en_puerto") or 0) > 0 and not p["buques_en_puerto_muestra"] and previo:
        print("  ! una página de puerto vino sin tabla; se conserva la anterior (¿cambió el formato?)")
        mem.setdefault("avisos_formato", {})[lc] = iso(ahora)
        return
    mem["puertos"][lc] = {"leido_utc": iso(ahora), "datos": p}
    for x in p["buques_en_puerto_muestra"]:
        if not reglas.de_la_flota(x.get("nombre")) or not x.get("imo"):
            continue
        b = mem["buques"].setdefault(x["imo"], {"nombre": x["nombre"], "imo": x["imo"], "mmsi": x["mmsi"],
                                                 "ficha": x["ficha"], "region": "cercana", "mo_utc": None})
        rel = b.get("rel") or {}
        if rel.get("tipo") not in ("rada", "atracado") or rel.get("puerto") != lc:
            b["prioridad_extra"] = True               # un buque de la flota en puerto que no teníamos así: revisarlo ya
    detalle(f"  puerto {lc}: {p.get('total_en_puerto')} en puerto, muestra {len(p['buques_en_puerto_muestra'])}")


def leer_buque(red: redmod.Red, mem: dict, imo: str, ahora: dt.datetime, arranque: bool):
    b = mem["buques"][imo]
    raw = red.obtener(b["ficha"], marcador=mo.MARCA_BUQUE)
    b["mo_utc"] = iso(ahora)
    b.pop("prioridad_extra", None)
    if raw is None:
        b["inactivo"] = True
        detalle(f"  {b.get('nombre')}: ficha ya no existe (404)")
        return
    datos = mo.parsear_buque(raw, b["ficha"])
    if not datos.get("zona_actual"):
        print("  ! no se pudo leer una ficha; se conserva lo anterior")
        return
    b["nombre"] = datos.get("nombre") or b.get("nombre")
    b["region"] = reglas.region(datos)
    nueva = reglas.relacion(datos, ahora)
    if nueva[0]:
        b["visto_ve_utc"] = iso(ahora)
    relevante = nueva[0] or b.get("manual") or b.get("visto_ve_utc")
    if relevante:
        b["mo"] = datos
    else:
        b.pop("mo", None)
        b["zona"] = datos.get("zona_actual")
    registrar_cambio(mem, b, nueva, ahora, arranque)
    detalle(f"  {b['nombre']}: {reglas.estado_es(datos)} · {reglas.donde(datos)} · rel={nueva[0] or '-'} {nueva[1] or ''}")


def leer_eta(red: redmod.Red, mem: dict, imo: str, ahora: dt.datetime, arranque: bool):
    b = mem["buques"][imo]
    raw = red.obtener(vf.url_buque(imo), marcador=vf.MARCA)
    b["vf_utc"] = iso(ahora)
    if raw is None:
        return
    nuevo = vf.parsear(raw, vf.url_buque(imo), ahora)
    previo = b.get("vf") or {}
    b["vf"] = nuevo
    rel = b.get("rel") or {}
    viejo_eta, nuevo_eta = de_iso(previo.get("eta_ais_utc")), de_iso(nuevo.get("eta_ais_utc"))
    if not arranque and viejo_eta and nuevo_eta and abs((nuevo_eta - viejo_eta).total_seconds()) >= 3 * 3600:
        evento(mem, ahora, "eta", b, rel.get("puerto"), eta_utc=iso(nuevo_eta), eta_previa_utc=iso(viejo_eta))
    detalle(f"  ETA {b.get('nombre')}: {nuevo.get('eta_ais_utc')} → {nuevo.get('vf_destino_locode')}")


def leer_indice(red: redmod.Red, mem: dict, ahora: dt.datetime):
    raw = red.obtener(f"{mo.BASE}/vessels/pages/sitemap.xml", marcador="<sitemapindex")
    if raw is None:
        return
    urls = re.findall(r"<loc>([^<]+)</loc>", raw)
    mem["sitemaps"] = {u: (mem.get("sitemaps") or {}).get(u, {}) for u in urls}
    mem["indice_utc"] = iso(ahora)
    detalle(f"  índice de buques: {len(urls)} ficheros")


def leer_sitemap(red: redmod.Red, mem: dict, url: str, ahora: dt.datetime):
    raw = red.obtener(url, marcador="<urlset")
    mem["sitemaps"][url] = {"leido_utc": iso(ahora)}
    if raw is None:
        return
    pref = re.escape(reglas.PREFIJO_FLOTA.strip().replace(" ", "_"))
    nuevos = 0
    for imo, mmsi, nombre in re.findall(rf"/vessels/pages/imo:(\d+)/mmsi:(\d+)/({pref}_[^<\"]+?)\.html", raw):
        ficha = f"{mo.BASE}/vessels/pages/imo:{imo}/mmsi:{mmsi}/{nombre}.html"
        b = mem["buques"].get(imo)
        if b is None:
            mem["buques"][imo] = {"nombre": nombre.replace("_", " "), "imo": imo, "mmsi": mmsi, "ficha": ficha,
                                  "region": "cercana", "mo_utc": None}
            nuevos += 1
        elif b.get("ficha") != ficha:                  # renombrado o cambio de MMSI
            b.update({"mmsi": mmsi, "ficha": ficha, "inactivo": False})
    detalle(f"  flota: {nuevos} buques nuevos en {url.rsplit('/', 1)[-1]}")


# ---------------------------------------------------------------- salida para la app y el widget
def vista(b: dict) -> dict:
    datos = b.get("mo") or {}
    d = {k: datos.get(k) for k in ("nombre", "imo", "mmsi", "ficha", "tipo", "eslora_m", "bandera", "construido",
                                   "estado_ais", "dato_ais_hace", "zona_actual", "salio_de", "salio_utc",
                                   "destino", "destino_pais", "ultimas_escalas")}
    d["imo"] = d["imo"] or b.get("imo")
    d["estado_es"], d["donde_es"], d["en_rada"] = reglas.estado_es(datos), reglas.donde(datos), reglas.en_rada(datos)
    d["leido_utc"] = b.get("mo_utc")
    d["historial"] = b.get("historial", [])
    return d


def construir_estado(mem: dict, cfg: dict, ahora: dt.datetime) -> dict:
    puertos = {}
    for lc, pc in cfg["puertos"].items():
        p = (mem["puertos"].get(lc) or {})
        pd_ = p.get("datos") or {}
        puertos[lc] = {
            "nombre": pc["nombre"], "corto": pc.get("corto") or pc["nombre"], "leido_utc": p.get("leido_utc"),
            "total_en_puerto": pd_.get("total_en_puerto"), "zarpados_recientes": pd_.get("zarpados_recientes"),
            "vienen": [], "en_puerto": [], "zarparon": [],
            "otros_en_puerto": [x for x in pd_.get("buques_en_puerto_muestra", [])
                                if not reglas.de_la_flota(x.get("nombre"))],
        }
    for b in mem["buques"].values():
        datos = b.get("mo")
        if not datos or b.get("inactivo"):
            continue
        aqui = reglas.puerto_de(datos.get("zona_actual"))
        va = reglas.puerto_de(datos.get("destino"))
        salio = reglas.puerto_de(datos.get("salio_de"))
        if aqui:
            d = vista(b)
            esc = next((e for e in datos.get("ultimas_escalas", []) if reglas.puerto_de(e["puerto"]) == aqui), {})
            d["llegada_utc"] = esc.get("llegada_utc") or (b.get("rel") or {}).get("desde_utc")
            puertos[aqui]["en_puerto"].append(d)
            continue
        if va:
            d = vista(b)
            d.update(reglas.eta_aprox(datos, va))
            v = b.get("vf") or {}
            if v.get("vf_destino_locode") == va:      # ETA del buque solo si apunta al mismo puerto
                d.update({k: v.get(k) for k in ("eta_ais_utc", "calado_actual_m", "vf_posicion_hace")})
                d["vf_leido_utc"] = b.get("vf_utc")
            puertos[va]["vienen"].append(d)
        if salio and datos.get("salio_utc") and \
                ahora - de_iso(datos["salio_utc"]) <= dt.timedelta(days=reglas.DIAS_ZARPE):
            puertos[salio]["zarparon"].append(vista(b))

    avisos = []
    nombres = {MO_HOST: "Maritime Optima", VF_HOST: "VesselFinder"}
    for host, e in mem["sitios"].items():
        if e.get("pausa_hasta") and de_iso(e["pausa_hasta"]) > ahora:
            avisos.append({"nivel": "alerta", "sitio": nombres.get(host, host), "hasta_utc": e["pausa_hasta"],
                           "motivo": e.get("motivo")})
    return {
        "version": 3,
        "generado_utc": iso(ahora),
        "cada_min": 30,
        "titulo": cfg.get("titulo", "Rada"),
        "flota": cfg["flota"]["nombre"],
        "zona_horaria": cfg.get("zona_horaria", "UTC"),
        "zona_horaria_nombre": cfg.get("zona_horaria_nombre", ""),
        "fuente": "Maritime Optima y VesselFinder (páginas públicas, AIS)",
        "puertos": puertos,
        "eventos": mem["eventos"][:25],
        "avisos": avisos,
        "consultas_hoy": {nombres.get(h, h): e.get("peticiones_hoy", 0) for h, e in mem["sitios"].items()},
        "flota_vigilada": sum(1 for b in mem["buques"].values() if not b.get("inactivo")),
    }


# ---------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memoria", default=str(RAIZ / "memoria.enc"))
    ap.add_argument("--salida", default=str(RAIZ / "salida"))
    ap.add_argument("--tambien-json", action="store_true", help="además del cifrado, deja copias legibles (solo PC)")
    args = ap.parse_args()
    tiempo_max = float(os.environ.get("RADA_TIEMPO_MAX", "40"))

    clave = cifrado.cargar_clave()
    cfg = cargar_config(clave)
    reglas.configurar(cfg)

    ahora = ahora_utc()
    pmem = pathlib.Path(args.memoria)
    arranque = not pmem.exists()
    mem = memoria_inicial(ahora) if arranque else leer_json(pmem, clave, "memoria")
    cargar_vigilados_manuales(mem, cfg)
    red = redmod.Red(mem["sitios"], tiempo_max_s=tiempo_max)
    t0 = time.monotonic()

    t_mo, t_vf = planificar(mem, ahora)
    print(f"{iso(ahora)} · {'arranque · ' if arranque else ''}pendientes: {len(t_mo)} + {len(t_vf)} · "
          f"tope {tiempo_max:g} s", flush=True)
    colas = {MO_HOST: t_mo, VF_HOST: t_vf}
    fallos = 0
    while True:
        listas = [(red.segundos_hasta_listo(h), h) for h, q in colas.items() if q and red.disponible(h)]
        if not listas:
            break
        _, host = min(listas)
        _, _, clase, clave_t = colas[host].pop(0)
        try:
            if clase == "puerto":
                leer_puerto(red, mem, clave_t, ahora)
            elif clase == "buque":
                leer_buque(red, mem, clave_t, ahora, arranque)
                b = mem["buques"][clave_t]
                n = nivel(b, ahora)
                if n in ("viene", "viene_cerca") and atraso(b.get("vf_utc"), VF_CERCA_MIN, ahora) \
                        and not any(t[3] == clave_t for t in colas[VF_HOST]):
                    colas[VF_HOST].insert(0, (PRIORIDAD.index(n), 0, "eta", clave_t))   # ETA recién descubierto
            elif clase == "eta":
                leer_eta(red, mem, clave_t, ahora, arranque)
            elif clase == "indice":
                leer_indice(red, mem, ahora)
            elif clase == "sitemap":
                leer_sitemap(red, mem, clave_t, ahora)
        except redmod.SitioNoDisponible as ex:
            print(f"  un sitio no está disponible ({ex}); {len(colas[host])} tareas quedan para la próxima vez")
            colas[host] = []
        except redmod.RespuestaRara as ex:
            fallos += 1
            detalle(f"  ! {clase} {clave_t}: {ex}")

    pendientes = sum(len(q) for q in colas.values())
    cambios = mem.pop("_cambios", 0)
    estado = construir_estado(mem, cfg, ahora)
    mem["ejecuciones"].insert(0, {"utc": iso(ahora), "segundos": round(time.monotonic() - t0, 1),
                                  "peticiones": red.en_ejecucion, "notas": red.registro[-8:]})
    del mem["ejecuciones"][MAX_EJECUCIONES:]

    salida = pathlib.Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    t_estado = json.dumps(estado, ensure_ascii=False).encode()
    t_mem = json.dumps(mem, ensure_ascii=False, separators=(",", ":")).encode()
    (salida / "estado.enc").write_bytes(cifrado.cifrar(t_estado, clave, "estado"))
    (salida / "memoria.enc").write_bytes(cifrado.cifrar(t_mem, clave, "memoria"))
    if args.tambien_json:
        (salida / "estado.json").write_bytes(t_estado)
        (salida / "memoria.json").write_bytes(t_mem)
    for lc, p in estado["puertos"].items():
        detalle(f"{p['nombre']}: vienen {len(p['vienen'])} · en puerto {len(p['en_puerto'])} · "
                f"zarparon {len(p['zarparon'])}")
    print(f"consultas: {sum(red.en_ejecucion.values())} · fallos: {fallos} · cambios: {cambios} · "
          f"quedan: {pendientes} · {round(time.monotonic() - t0, 1)} s")


if __name__ == "__main__":
    main()
