"""Cifrado de lo que se publica: AES-256-GCM sobre los datos comprimidos.

El repositorio es público; la configuración y los datos no: solo se suben cifrados.
La clave nunca está en el repositorio: en la nube es el secreto RADA_CLAVE y en la PC, claves/datos.key.

Formato de archivo: b"RADA1" + nonce (12 bytes) + AES-GCM(zlib(datos)), con la etiqueta
("config", "estado", "memoria") como dato asociado. Compatible con WebCrypto + DecompressionStream("deflate").
"""
from __future__ import annotations

import base64
import os
import pathlib
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIA = b"RADA1"
RAIZ = pathlib.Path(__file__).resolve().parent.parent
CLAVE_LOCAL = RAIZ / "claves" / "datos.key"


def cargar_clave(ruta: str | os.PathLike | None = None) -> bytes:
    texto = os.environ.get("RADA_CLAVE") or pathlib.Path(ruta or CLAVE_LOCAL).read_text(encoding="utf-8")
    texto = texto.strip()
    clave = base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))
    if len(clave) != 32:
        raise ValueError("la clave debe tener 256 bits")
    return clave


def cifrar(datos: bytes, clave: bytes, etiqueta: str) -> bytes:
    nonce = os.urandom(12)
    return MAGIA + nonce + AESGCM(clave).encrypt(nonce, zlib.compress(datos, 9), etiqueta.encode())


def descifrar(blob: bytes, clave: bytes, etiqueta: str) -> bytes:
    if blob[:len(MAGIA)] != MAGIA:
        raise ValueError("no es un archivo cifrado de Rada")
    n = len(MAGIA)
    return zlib.decompress(AESGCM(clave).decrypt(blob[n:n + 12], blob[n + 12:], etiqueta.encode()))
