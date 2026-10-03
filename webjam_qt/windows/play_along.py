"""Choose a music activity before choosing its sound path."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from core.follow_along import PLAY_ALONG_CHOICES
from webjam_qt.theme.tokens import Space


class PlayAlongDialog(QDialog):
    choice_requested = Signal(str)

    def __init__(self, parent=None, *, hosting=True):
        super().__init__(parent)
        self.setWindowTitle("Play along")
        self.setModal(False)
        self.resize(500, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)
        title = QLabel("How do you want to play along?")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.buttons = {}
        for choice in PLAY_ALONG_CHOICES:
            heading = QLabel(choice.title)
            heading.setWordWrap(True)
            layout.addWidget(heading)
            detail = QLabel(choice.description)
            detail.setWordWrap(True)
            layout.addWidget(detail)
            button = QPushButton("Open Jamulus mixer" if choice.key == "ensemble" and not hosting else choice.action)
            button.setObjectName("GhostButton")
            button.setAutoDefault(False)
            button.setAccessibleDescription(choice.description)
            button.clicked.connect(lambda _checked=False, key=choice.key: self.choice_requested.emit(key))
            self.buttons[choice.key] = button
            layout.addWidget(button)
        hint = QLabel(
            "Video practice uses the meeting's sound. For live playing, listen through Jamulus "
            "and disconnect meeting audio. Shared Track needs a local audio file and a supported host route."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if not hosting:
            hint.setText(
                "The host chooses the backing track. In your Jamulus mixer, balance the WebJam Track "
                "channel and the other musicians. Disconnect meeting audio while playing; keep its video for faces."
            )
