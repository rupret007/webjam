"""Keep wrapped guidance readable and reveal keyboard-focused actions."""

from PySide6.QtCore import QEvent, QSize, Signal
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea
from PySide6.QtCore import Qt


class ScrollableContent(QScrollArea):
    viewport_resized = Signal()
    content_height_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().installEventFilter(self)
        self._fitting = False
        self._content_height = 0
        QApplication.instance().focusChanged.connect(self._reveal_focus)

    def setWidget(self, widget):
        super().setWidget(widget)
        widget.installEventFilter(self)
        self.fit_content()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self.viewport_resized.emit()
            self.fit_content()
        elif watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.fit_content()
        return super().eventFilter(watched, event)

    def fit_content(self):
        content = self.widget()
        if self._fitting or content is None or content.layout() is None:
            return
        self._fitting = True
        try:
            layout = content.layout()
            height = max(layout.minimumSize().height(),
                         layout.totalHeightForWidth(max(1, self.viewport().width())))
            if content.minimumHeight() != height:
                content.setMinimumHeight(height)
            if height != self._content_height:
                self._content_height = height
                self.content_height_changed.emit(height)
        finally:
            self._fitting = False

    def _reveal_focus(self, _old, current):
        content = self.widget()
        if (current is not None and content is not None and self.isVisible()
                and content.isAncestorOf(current)):
            self.ensureWidgetVisible(current, 8, 8)
