"""An explicit local lesson choice beside externally owned meeting controls."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

from core.youtube_lesson import parse_youtube_lesson_url
from webjam_qt.theme.tokens import Space


class LessonHandoffPanel(QFrame):
    choose_requested = Signal(int)
    open_requested = Signal(int)
    save_requested = Signal(int)
    restart_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.generation = 0
        self.hosting = None
        self.lesson_url = ""
        self.profile = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Space.SM, 0, 0)
        layout.setSpacing(Space.XS)
        self.source = QLabel()
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        self.source.setAccessibleName("Selected lesson")
        layout.addWidget(self.source)
        self.choose_button = QPushButton("Choose YouTube…")
        self.open_button = QPushButton("Open in browser")
        self.save_button = QPushButton("Remember lesson")
        self.restart_button = QPushButton("Restart pause requests")
        for button, signal in ((self.choose_button, self.choose_requested),
                               (self.open_button, self.open_requested),
                               (self.save_button, self.save_requested),
                               (self.restart_button, self.restart_requested)):
            button.setObjectName("GhostButton")
            button.setAutoDefault(False)
            button.setAccessibleName(button.text())
            button.clicked.connect(lambda _checked=False, b=button, s=signal: self._request(b, s))
            layout.addWidget(button)
        self.restart_button.setAccessibleDescription(
            "Start fresh requests for this room. Previous requests stay retired; the host controls playback."
        )
        self.restart_button.hide()
        self.save_button.setAccessibleDescription("Save the lesson link in the current Art project.")
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Lesson handoff status")
        layout.addWidget(self.status)
        self.set_context(hosting=None)

    def set_context(self, *, hosting, profile="", lesson_url=""):
        url = parse_youtube_lesson_url(lesson_url).playback_url if lesson_url else ""
        value = (hosting, profile, url)
        if value != (self.hosting, self.profile, self.lesson_url):
            self.generation += 1
            self.status.clear()
        self.hosting, self.profile, self.lesson_url = value
        if hosting is None or profile != "art":
            self.restart_button.hide()
        self.setVisible(hosting is not None)
        if url:
            prefix = "Your browser lesson:\n" if hosting else "Your reference link (the meeting may show another lesson):\n"
            self.source.setText(prefix + url)
        else:
            self.source.setText("Choose the lesson you want to share." if hosting else
                                "Watch the host's shared lesson in the meeting.")
        self.choose_button.setVisible(hosting is True)
        self.choose_button.setText("Change YouTube…" if url else "Choose YouTube…")
        self.choose_button.setAccessibleName("Change YouTube lesson" if url else "Choose YouTube lesson")
        self.open_button.setVisible(hosting is True and bool(url))
        self.save_button.setVisible(profile in {"art", "music"} and bool(url) and hosting is not None)
        self.save_button.setAccessibleDescription(
            "Save the lesson link with the current rehearsal song." if profile == "music"
            else "Save the lesson link in the current Art project."
        )
        self.save_button.setText("Remember lesson" if hosting else "Save reference")
        self.save_button.setAccessibleName("Remember browser lesson" if hosting else "Remember reference link")

    def _request(self, button, signal):
        if self.hosting is not None and self.isVisible() and button.isVisible() and button.isEnabled():
            signal.emit(self.generation)

    def set_status(self, text):
        self.status.setText(text)
