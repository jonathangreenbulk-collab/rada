"""Vincular el teléfono: muestra en pantalla el código QR con la clave de los datos.

En el teléfono: abrir Rada → «Escanear código». La app lee el QR con su propia cámara,
así la clave no pasa por otras apps ni queda en el historial del navegador.
No se guarda ningún archivo: el código existe solo mientras la ventana está abierta.

Uso:  python pc\\qr_clave.py
"""
import base64
import io
import pathlib
import sys
import tkinter as tk

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import cifrado  # noqa: E402
import segno    # noqa: E402


def main():
    cifrado.cargar_clave()                                   # valida que sea una clave de 256 bits
    texto = cifrado.CLAVE_LOCAL.read_text(encoding="utf-8").strip()
    png = io.BytesIO()
    segno.make(texto, error="m", micro=False).save(png, kind="png", scale=9, border=3)

    raiz = tk.Tk()
    raiz.title("Rada · vincular teléfono")
    raiz.configure(bg="white")
    raiz.resizable(False, False)
    img = tk.PhotoImage(data=base64.b64encode(png.getvalue()))
    tk.Label(raiz, image=img, bg="white").pack(padx=20, pady=(20, 8))
    tk.Label(raiz, bg="white", font=("Segoe UI", 11), justify="center",
             text="En el teléfono abre Rada y toca «Escanear código».\nCierra esta ventana cuando termines.").pack(padx=20, pady=(0, 20))
    raiz.attributes("-topmost", True)
    raiz.mainloop()


if __name__ == "__main__":
    main()
