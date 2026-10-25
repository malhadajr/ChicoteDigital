"""Gera chicote.ico a partir do mesmo desenho usado na bandeja (usado no build)."""

import io
import os
import sys

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtWidgets import QApplication

import chicote_digital

TAMANHOS = (16, 24, 32, 48, 64, 128, 256)


def main():
    app = QApplication(sys.argv[:1])
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    chicote_digital.pixmap_icone(256).save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data())))
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chicote.ico")
    img.save(destino, sizes=[(t, t) for t in TAMANHOS])
    print("ícone gerado:", destino)
    del app


if __name__ == "__main__":
    main()
