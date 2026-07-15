"""Regenerate packaged ``.ico`` and ``.icns`` from the native brand renderer.

Run from the repository root with::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m webjam_qt.theme.generate_brand_icons

The matching SVG is a portable documentation companion.  The live renderer is
native QPainter geometry, and these binary containers exist because Windows
Explorer and macOS Finder do not consume that geometry from a PyInstaller app.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtWidgets import QApplication

from webjam_qt.theme.brand import BRAND_MARK_PATH, render_application_icon_pixmap


OUTPUT_DIR = BRAND_MARK_PATH.parent


def _png(size: int) -> bytes:
    pixmap = render_application_icon_pixmap(size)
    if pixmap.isNull():
        raise RuntimeError(f"Could not render WebJam brand icon at {size}px")
    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Could not allocate icon output buffer")
    if not pixmap.save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode WebJam brand icon at {size}px")
    buffer.close()
    return bytes(payload)


def _write_ico(path: Path) -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [(size, _png(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + (16 * len(images))
    directory = bytearray()
    payload = bytearray()
    for size, data in images:
        encoded_size = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        payload.extend(data)
        offset += len(data)
    path.write_bytes(header + directory + payload)


def _write_icns(path: Path) -> None:
    chunks = (
        (b"icp4", 16),
        (b"icp5", 32),
        (b"icp6", 64),
        (b"ic07", 128),
        (b"ic08", 256),
        (b"ic09", 512),
        (b"ic10", 1024),
    )
    body = bytearray()
    for kind, size in chunks:
        data = _png(size)
        body.extend(kind)
        body.extend(struct.pack(">I", len(data) + 8))
        body.extend(data)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_ico(OUTPUT_DIR / "webjam.ico")
    _write_icns(OUTPUT_DIR / "webjam.icns")
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
