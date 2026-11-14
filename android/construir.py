"""Compila la app Android de Rada sin Android Studio ni Gradle: solo el kit de línea de comandos.

Pasos: recursos (aapt2) → Java (javac) → dex (d8) → APK alineado (zipalign) → firma (apksigner).
- La interfaz es la de web/, y solo se copian archivos versionados: nunca datos locales descifrados.
- El repositorio de datos sale de 'git remote origin'; no queda escrito en el código.
- La firma vive en claves/ (no se sube). Sin ella no se puede actualizar la app ya instalada.

Uso:  python android/construir.py      →  android/build/rada.apk
"""
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
AND = RAIZ / "android"
OUT = AND / "build"
CLAVES = RAIZ / "claves"
SDK = pathlib.Path(os.environ.get("ANDROID_HOME") or pathlib.Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")
API, MIN_API, BUILD_TOOLS = 36, 33, "36.1.0"
NO_EMPAQUETAR = {"web/widget.html", "web/sw.js", "web/manifest.webmanifest"}   # solo sirven en la PC / en Pages


def herramienta(nombre: str) -> str:
    carpeta = SDK / "build-tools" / BUILD_TOOLS
    for ext in (".exe", ".bat", ""):
        p = carpeta / (nombre + ext)
        if p.exists():
            return str(p)
    sys.exit(f"falta {nombre} en {carpeta}")


def jdk(nombre: str) -> str:
    jh = os.environ.get("JAVA_HOME")
    if jh and (pathlib.Path(jh) / "bin" / (nombre + ".exe")).exists():
        return str(pathlib.Path(jh) / "bin" / (nombre + ".exe"))
    w = shutil.which(nombre)
    if not w:
        sys.exit(f"falta {nombre} (JDK)")
    return w


def correr(*args) -> str:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    salida = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print(salida)
        sys.exit(f"falló: {pathlib.Path(str(args[0])).name}")
    return salida


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(RAIZ), *args], capture_output=True, text=True, check=True).stdout.strip()


def repositorio() -> str:
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", git("remote", "get-url", "origin"))
    if not m:
        sys.exit("no se pudo leer el repositorio de 'origin'")
    return m.group(1)


def firma():
    ks, pw = CLAVES / "android-firma.p12", CLAVES / "android-firma.txt"
    if not ks.exists():
        CLAVES.mkdir(exist_ok=True)
        pw.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        correr(jdk("keytool"), "-genkeypair", "-keystore", ks, "-storetype", "PKCS12", "-alias", "rada",
               "-keyalg", "RSA", "-keysize", "3072", "-validity", "36500", "-dname", "CN=Rada",
               "-storepass:file", pw, "-keypass:file", pw)
        print("· firma nueva en claves/ (guárdala: sin ella no se puede actualizar la app instalada)")
    return ks, pw


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    jar =SDK / "platforms" / f"android-{API}" / "android.jar"
    if not jar.exists():
        sys.exit(f"falta {jar}: instala platforms/android-{API}")
    if OUT.exists():
        shutil.rmtree(OUT)
    for d in ("gen/app/rada", "clases", "dex", "assets"):
        (OUT / d).mkdir(parents=True)

    # 1) interfaz web: solo lo versionado en git
    copiados = 0
    for f in git("ls-files", "web").splitlines():
        if f in NO_EMPAQUETAR or pathlib.PurePosixPath(f).name.startswith("estado."):
            continue
        destino = OUT / "assets" / f
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RAIZ / f, destino)
        copiados += 1

    # 2) de dónde bajar los datos (generado; no se versiona)
    (OUT / "gen/app/rada/Config.java").write_text(
        "package app.rada;\n\n/** Generado por android/construir.py desde git remote origin. */\n"
        "final class Config {\n    private Config() {}\n\n"
        f'    static final String REPO = "{repositorio()}";\n}}\n', encoding="utf-8")

    # 3) recursos + manifiesto
    aapt2 = herramienta("aapt2")
    correr(aapt2, "compile", "--dir", AND / "res", "-o", OUT / "res.zip")
    version = int((time.time() - 1767225600) // 60)                 # minutos desde 2026-01-01: siempre sube
    correr(aapt2, "link", "-I", jar, "--manifest", AND / "AndroidManifest.xml",
           "--min-sdk-version", MIN_API, "--target-sdk-version", API,
           "--version-code", version, "--version-name", time.strftime("%Y.%m.%d.%H%M"),
           "-A", OUT / "assets", "--java", OUT / "gen", "-o", OUT / "base.apk", OUT / "res.zip")

    # 4) Java → .class → .dex
    fuentes = sorted(str(p) for p in (AND / "src").rglob("*.java")) + sorted(str(p) for p in (OUT / "gen").rglob("*.java"))
    avisos = correr(jdk("javac"), "--release", "17", "-encoding", "UTF-8", "-Xlint:all,-options,-serial",
                    "-cp", jar, "-d", OUT / "clases", *fuentes)
    if avisos:
        print(avisos)
    with zipfile.ZipFile(OUT / "clases.jar", "w") as z:
        for p in (OUT / "clases").rglob("*.class"):
            z.write(p, p.relative_to(OUT / "clases").as_posix())
    correr(herramienta("d8"), "--release", "--min-api", MIN_API, "--lib", jar, "--output", OUT / "dex", OUT / "clases.jar")

    # 5) APK: recursos + dex → alinear → firmar → verificar
    shutil.copy(OUT / "base.apk", OUT / "sin_firmar.apk")
    with zipfile.ZipFile(OUT / "sin_firmar.apk", "a", zipfile.ZIP_DEFLATED) as z:
        z.write(OUT / "dex" / "classes.dex", "classes.dex")
    correr(herramienta("zipalign"), "-p", "-f", "4", OUT / "sin_firmar.apk", OUT / "alineado.apk")
    ks, pw = firma()
    # PKCS12: la clave privada usa la misma contraseña que el almacén (apksigner la reutiliza si no se da --key-pass)
    correr(herramienta("apksigner"), "sign", "--ks", ks, "--ks-key-alias", "rada",
           "--ks-pass", f"file:{pw}", "--out", OUT / "rada.apk", OUT / "alineado.apk")
    correr(herramienta("apksigner"), "verify", "--min-sdk-version", MIN_API, OUT / "rada.apk")

    # 6) control: nada de datos dentro del APK
    with zipfile.ZipFile(OUT / "rada.apk") as z:
        if any(pathlib.PurePosixPath(n).name.startswith("estado.") for n in z.namelist()):
            sys.exit("¡hay datos dentro del APK! revisa web/")
    tam = (OUT / "rada.apk").stat().st_size // 1024
    print(f"listo: {OUT / 'rada.apk'} · versión {version} · {copiados} archivos de interfaz · {tam} KB")


if __name__ == "__main__":
    main()
