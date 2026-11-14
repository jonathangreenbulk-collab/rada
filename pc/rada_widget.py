"""Rada — widget de escritorio (Windows).

Ventana pequeña sin bordes con el resumen de la flota vigilada en sus puertos.
Este programa NO consulta webs de terceros: los datos los produce la nube (GitHub Actions)
y aquí solo se descarga estado.enc de la rama 'datos' con la sesión de GitHub CLI (gh), usando ETag
(si no cambió, GitHub responde 304 y no cuenta para el límite). El archivo viene cifrado y se descifra
con la clave local (claves/datos.key); la copia legible queda solo en esta PC.

Uso:  pythonw pc/rada_widget.py        (sin consola; el acceso directo de Inicio hace esto)
"""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import winreg
from ctypes import wintypes

AQUI = pathlib.Path(__file__).resolve().parent
RAIZ = AQUI.parent
WEB = RAIZ / "web"
CACHE = AQUI / "cache"
CONFIG = AQUI / "config.json"
TITULO = "Rada · widget"
ANCHO, ALTO, MARGEN = 380, 272, 16
DEFECTO = {"repo": "", "rama": "datos", "archivo": "estado.enc", "encima": False, "pos": None}

sys.path.insert(0, str(RAIZ / "scripts"))
import cifrado  # noqa: E402  (AES-256-GCM; la clave vive en claves/datos.key)

CACHE.mkdir(exist_ok=True)
if sys.stdout is None or sys.stderr is None:          # pythonw: sin consola → la salida va al registro
    _salida = open(AQUI / "rada_widget.salida.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _salida
    sys.stderr = sys.stderr or _salida
log = logging.getLogger("rada")
_h = logging.handlers.RotatingFileHandler(AQUI / "rada_widget.log", maxBytes=200_000, backupCount=1, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)
log.setLevel(logging.INFO)

u32 = ctypes.windll.user32
u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
u32.FindWindowW.restype = wintypes.HWND
u32.MonitorFromPoint.restype = wintypes.HANDLE
u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]


# ------------------------------------------------------------------ configuración
def cargar_config() -> dict:
    cfg = dict(DEFECTO)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def guardar_config(cfg: dict):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CONFIG)


def tema_oscuro() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


# ------------------------------------------------------------------ datos
def _gh() -> str:
    return shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def _escribir_atomico(ruta: pathlib.Path, contenido: str):
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(contenido, encoding="utf-8")
    os.replace(tmp, ruta)


class Sincronizador:
    """Descarga estado.json de la rama 'datos' del repo privado. Nunca más de 1 vez por minuto."""

    def __init__(self, cfg: dict):
        self._cfg, self._token, self._ultimo, self._lock = cfg, None, 0.0, threading.Lock()
        self._publicado = None

    def _obtener_token(self, renovar=False) -> str:
        if self._token and not renovar:
            return self._token
        r = subprocess.run([_gh(), "auth", "token"], capture_output=True, text=True, timeout=20,
                           creationflags=0x08000000)  # sin ventana de consola
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError("Sin sesión de GitHub en esta PC (gh auth login)")
        self._token = r.stdout.strip()
        return self._token

    def _descargar(self):
        c = self._cfg
        url = f"https://api.github.com/repos/{c['repo']}/contents/{c['archivo']}?ref={c['rama']}"
        f_datos, f_etag = CACHE / "estado.json", CACHE / "etag.txt"
        for intento in (1, 2):
            cab = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "rada-widget", "Authorization": f"Bearer {self._obtener_token(intento == 2)}"}
            if f_datos.exists() and f_etag.exists():
                cab["If-None-Match"] = f_etag.read_text().strip()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=20) as r:
                    cuerpo, etag = r.read(), r.headers.get("ETag")
                if c["archivo"].endswith(".enc"):
                    cuerpo = cifrado.descifrar(cuerpo, cifrado.cargar_clave(), "estado")
                json.loads(cuerpo)                       # que sea JSON válido antes de guardarlo
                f_datos.write_bytes(cuerpo)
                if etag:
                    f_etag.write_text(etag)
                return
            except urllib.error.HTTPError as e:
                if e.code == 304:
                    return
                if e.code == 401 and intento == 1:
                    continue
                raise RuntimeError(f"GitHub respondió {e.code}") from e

    def _publicar_para_app(self, estado: dict):
        """Copia los datos junto a la app (web/) para que se abra sin internet ni servidor."""
        if self._publicado == estado.get("generado_utc"):
            return
        texto = json.dumps(estado, ensure_ascii=False)
        _escribir_atomico(WEB / "estado.json", texto)
        _escribir_atomico(WEB / "estado.js", "window.DATOS = " + texto + ";\n")
        self._publicado = estado.get("generado_utc")

    def estado(self, forzar=False) -> dict:
        with self._lock:
            error, nube = None, False
            if self._cfg.get("repo") and (forzar or time.time() - self._ultimo > 60):
                self._ultimo = time.time()
                try:
                    self._descargar()
                    nube = True
                except Exception as e:                  # sin red, sin sesión...: se usa lo guardado
                    error = str(e)
                    log.warning("sincronización: %s", e)
            elif self._cfg.get("repo"):
                nube = True
            for f, origen in ((CACHE / "estado.json", "nube" if nube else "guardado"),
                              (RAIZ / "datos" / "estado.json", "local")):
                try:
                    estado = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                self._publicar_para_app(estado)
                return {"estado": estado, "origen": origen, "error": error}
            return {"estado": None, "origen": None, "error": error or "Aún no hay datos"}


# ------------------------------------------------------------------ ventana
def abrir_url(url: str):
    """Abre en el navegador predeterminado conservando el #ancla (os.startfile la pierde en archivos locales)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                                                      r"\UrlAssociations\https\UserChoice") as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split()[0]
        subprocess.Popen([exe, url], creationflags=0x08000000)
    except Exception:
        os.startfile(url)


class Api:
    """Lo que el HTML del widget puede pedir a Python (atributos con _ no se exponen)."""

    def __init__(self, sinc: Sincronizador, cfg: dict):
        self._sinc, self._cfg, self._ventana = sinc, cfg, None

    def config(self):
        return {"encima": bool(self._cfg.get("encima"))}

    def estado(self):
        return self._sinc.estado()

    def refrescar(self):
        return self._sinc.estado(forzar=True)

    def abrir_app(self, destino=""):
        url = (WEB / "index.html").as_uri() + (f"#{destino}" if destino else "")
        abrir_url(url)

    def fijar(self, valor):
        self._cfg["encima"] = bool(valor)
        self._ventana.on_top = bool(valor)
        guardar_config(self._cfg)
        return bool(valor)

    def cerrar(self):
        self._ventana.destroy()


def _area_trabajo() -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    u32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)       # SPI_GETWORKAREA (sin barra de tareas)
    return r.left, r.top, r.right, r.bottom


def _rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def preparar_y_mostrar(ventana, cfg: dict):
    """Con la ventana aún oculta: la ubica, la saca de la barra de tareas, redondea esquinas y la muestra.
    Pase lo que pase, la ventana termina visible."""
    try:
        _preparar(ventana, cfg)
    except Exception:
        log.exception("ajustes de ventana")
        try:
            ventana.show()
        except Exception:
            log.exception("mostrar ventana")


def _preparar(ventana, cfg: dict):
    ventana.events.loaded.wait(15)
    log.info("contenido cargado; preparando ventana")
    hwnd = None
    for _ in range(50):
        hwnd = u32.FindWindowW(None, TITULO)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        log.warning("no encontré la ventana; se muestra sin ajustes")
        ventana.show()
        return
    escala = u32.GetDpiForWindow(hwnd) / 96 if hasattr(u32, "GetDpiForWindow") else 1
    w, h = round(ANCHO * escala), round(ALTO * escala)            # tamaño exacto (sin marcos invisibles)
    pos = cfg.get("pos")
    punto = wintypes.POINT(pos[0] + w // 2, pos[1] + 10) if pos else None
    if not pos or not u32.MonitorFromPoint(punto, 0):              # pantalla ya no existe: esquina por defecto
        _, _, ar, ab = _area_trabajo()
        m = round(MARGEN * escala)
        pos = [ar - w - m, ab - h - m]
    SWP_NOZORDER, SWP_NOACTIVATE = 0x4, 0x10
    u32.SetWindowPos(hwnd, None, pos[0], pos[1], w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
    estilo = u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (estilo | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    try:
        redondo = ctypes.c_int(2)                                   # DWMWCP_ROUND (Windows 11)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(redondo), 4)
    except Exception:
        pass
    ventana.show()

    def al_mover(*_):
        l2, t2, _, _ = _rect(hwnd)
        if [l2, t2] != cfg.get("pos"):
            cfg["pos"] = [l2, t2]
            guardar_config(cfg)
    ventana.events.moved += al_mover


def main():
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "Local\\RadaWidget")
    if k32.GetLastError() == 183:                                  # ya hay uno abierto
        hwnd = u32.FindWindowW(None, TITULO)
        if hwnd:
            u32.ShowWindow(hwnd, 5)
            u32.SetForegroundWindow(hwnd)
        return

    import webview  # importar tarde: arranque más rápido si ya había uno abierto

    cfg = cargar_config()
    api = Api(Sincronizador(cfg), cfg)
    ventana = webview.create_window(
        TITULO, url=str(WEB / "widget.html"), js_api=api, width=ANCHO, height=ALTO,
        frameless=True, easy_drag=False, shadow=True, resizable=False, hidden=True, focus=False,
        on_top=bool(cfg.get("encima")), background_color="#14212B" if tema_oscuro() else "#FFFFFF")
    api._ventana = ventana
    log.info("inicio · repo=%s", cfg.get("repo") or "(sin configurar: datos locales)")
    webview.start(preparar_y_mostrar, (ventana, cfg), gui="edgechromium",
                  storage_path=str(CACHE / "webview"), private_mode=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("el widget se cerró por un error")
        raise
