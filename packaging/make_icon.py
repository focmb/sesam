#!/usr/bin/env python3
"""Erzeugt packaging/windows/sesam.ico aus dem Programm-Icon (src/sesam/gui/style.py: APP_ICON_SVG).

Nur nötig, wenn sich das Icon ändert; die erzeugte .ico liegt im Repository:
    python packaging/make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SIZES = (16, 24, 32, 48, 64, 128, 256)
TARGET = ROOT / "packaging" / "windows" / "sesam.ico"


def build() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from sesam.gui.style import APP_ICON_SVG

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (hält Qt am Leben)
    renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode()))
    images: list[bytes] = []
    for size in SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        images.append(bytes(data))
    # ICO-Container mit PNG-Bildern (ab Windows Vista üblich)
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, png in zip(SIZES, images):
        dim = 0 if size >= 256 else size  # 0 bedeutet 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(payload))
        payload += png
    return out + entries + payload


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(build())
    print(f"Geschrieben: {TARGET} ({TARGET.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
