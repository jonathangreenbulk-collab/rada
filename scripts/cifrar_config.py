"""Cifra claves/config.json → config.enc (lo único de la configuración que se publica).

Uso (tras editar claves/config.json): python -I scripts/cifrar_config.py
"""
import json
import pathlib
import sys

AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import cifrado  # noqa: E402

RAIZ = AQUI.parent

texto = (RAIZ / "claves" / "config.json").read_text(encoding="utf-8")
json.loads(texto)                                   # que sea JSON válido antes de cifrarlo
clave = cifrado.cargar_clave()
blob = cifrado.cifrar(texto.encode(), clave, "config")
assert json.loads(cifrado.descifrar(blob, clave, "config")) == json.loads(texto)
(RAIZ / "config.enc").write_bytes(blob)
print(f"config.enc listo ({len(blob)} bytes, verificado)")
