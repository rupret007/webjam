"""Choose a music activity before choosing its sound path."""

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from core.follow_along import PLAY_ALONG_CHOICES
from webjam_qt.theme.tokens import Space


class PlayAlongDialog(QDialog):
    choice_requested = Signal(str)

    def __init__(self, parent=None, *, hosting=True):
        super().__init__(parent)
        self.setWindowTitle("Play along")
        self.setModal(False)
        self.resize(500, 390)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll)
        content = QWidget()
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)
        title = QLabel("How do you want to play along?")
        title.setWordWrap(True)
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        self.buttons = {}
        for choice in PLAY_ALONG_CHOICES:
            section = QVBoxLayout()
            section.setSpacing(Space.SM)
            layout.addLayout(section)
            is_guest_track = choice.key == "ensemble" and not hosting
            description = (
                "Your host chooses the file. Use the mixer to balance the track with the musicians."
                if is_guest_track else choice.description
            )
            heading = QLabel(choice.title)
            heading.setWordWrap(True)
            heading_font = heading.font()
            heading_font.setBold(True)
            heading.setFont(heading_font)
            section.addWidget(heading)
            detail = QLabel(description)
            detail.setWordWrap(True)
            section.addWidget(detail)
            button = QPushButton("Open Jamulus mixer" if is_guest_track else choice.action)
            button.setObjectName("PrimaryButton" if choice.key == "ensemble" else "GhostButton")
            button.setAutoDefault(False)
            button.setAccessibleDescription(description)
            button.installEventFilter(self)
            button.clicked.connect(lambda _checked=False, key=choice.key: self.choice_requested.emit(key))
            self.buttons[choice.key] = button
            section.addWidget(button)
        hint = QLabel(
            "Shared Track needs a supported Mac audio route. Keep meeting video on and "
            "disconnect meeting audio while playing."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if not hosting:
            hint.setText(
                "Disconnect meeting audio while playing; keep its video for faces."
            )
        layout.addStretch(1)
        for label in content.findChildren(QLabel):
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in self.buttons.values():
            self.scroll.ensureWidgetVisible(watched, 0, Space.SM)
        return super().eventFilter(watched, event)
