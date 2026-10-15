"""Capture the empty Paint along host with production styling, entirely offline.

Run from the worktree root: .venv/bin/python docs/evidence/W03-headline/capture.py DEST
"""

from pathlib import Path
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.windows.reference_video import ReferenceVideoDialog  # noqa: E402


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication(["w03-headline-capture"])
    fonts = Path("webjam_qt/theme/fonts")
    for path in sorted(fonts.glob("Inter-*.ttf")):
        QFontDatabase.addApplicationFont(str(path))
    font = QFont("Inter" if "Inter" in QFontDatabase.families() else app.font().family())
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(load_stylesheet())
    for width, height in ((800, 600), (1280, 800)):
        panel = ReferenceVideoDialog(hosting=True)
        panel.resize(width, height)
        panel.show()
        for _ in range(6):
            app.processEvents()
        assert (panel.width(), panel.height()) == (width, height)
        output = destination / f"paint-along-host-headline-{width}x{height}.png"
        assert panel.grab().save(str(output), "PNG")
        print(f"{output}: {panel._headline.text()}")
        panel.close()
        panel.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
