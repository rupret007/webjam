"""Art's AI image panel: two verbs, a status line, and nothing to configure.

The panel renders immutable snapshots and emits semantic intent. It decides
nothing -- whether Krita and its plugin are installed, and whether a local
backend answered, all belong to :mod:`core.ai_image`.

There is no prompt box here, no model list, no sampler, no step count, and no
mask tool. Not because those are hard, but because Krita AI Diffusion already
has them and a second, worse copy inside WebJam would be a lie about where the
image is actually made. So the panel is two buttons of the same family:
**Make** a new image, or **Edit** one you already own.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.ai_image import (
    RESULTS_ARE_YOURS_MESSAGE,
    AiImageSnapshot,
    AiImageState,
)
from core.krita_ai import AI_IMAGE_SUFFIXES
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.status_chip import QuietAction, StatusChip

_HEADLINE = "AI image"
_HINT = (
    "WebJam does not generate anything. Krita's AI Image Generation plugin "
    "does, on this computer, using a local backend. You describe the image "
    "there, because WebJam cannot type into another program. Put a result on "
    "the shared canvas yourself if you want the room to see it."
)


def image_name_filter() -> str:
    """Build the file dialog filter from the suffixes the domain accepts."""

    patterns = " ".join(f"*{suffix}" for suffix in sorted(AI_IMAGE_SUFFIXES))
    return f"Images ({patterns})"


class AiImageDialog(QDialog):
    """One compact panel offering Make and Edit, and honest recovery."""

    make_requested = Signal()
    edit_requested = Signal(str)
    install_krita_requested = Signal()
    install_plugin_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Image")
        self.setModal(False)
        # Narrow on purpose: an artist needs Krita and the faces of the people
        # they are working with on screen at once, so WebJam's own chrome
        # stays out of the way.
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.SM)

        self._headline = QLabel(_HEADLINE)
        self._headline.setObjectName("AiImageHeadline")
        self._headline.setAccessibleName("AI image")
        layout.addWidget(self._headline)

        self._status = QLabel("")
        self._status.setObjectName("AiImageStatus")
        self._status.setWordWrap(True)
        self._status.setAccessibleName("AI image status")
        layout.addWidget(self._status)

        self._chip = StatusChip()
        self._chip.clicked.connect(self._chip_pressed)
        layout.addWidget(self._chip)

        # Editing an image you already have is a real need and not the point
        # of the panel, so it is available and visibly lesser rather than a
        # second equal call to action.
        self._edit = QuietAction()
        self._edit.clicked.connect(self._choose_image)
        layout.addWidget(self._edit)

        self._activity = QLabel("")
        self._activity.setObjectName("AiImageActivity")
        self._activity.setWordWrap(True)
        self._activity.setVisible(False)
        layout.addWidget(self._activity)

        ownership = QLabel(RESULTS_ARE_YOURS_MESSAGE)
        ownership.setObjectName("AiImageOwnership")
        ownership.setWordWrap(True)
        layout.addWidget(ownership)

        hint = QLabel(_HINT)
        hint.setObjectName("AiImageHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    # -- intent --------------------------------------------------------

    def _chip_pressed(self) -> None:
        action = self._chip.property("action")
        if action == "make":
            self.make_requested.emit()
        elif action == "install_krita":
            self.install_krita_requested.emit()
        elif action == "install_plugin":
            self.install_plugin_requested.emit()

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an image you already have",
            "",
            image_name_filter(),
        )
        if path:
            self.edit_requested.emit(path)

    # -- rendering -----------------------------------------------------

    def set_snapshot(self, snapshot: AiImageSnapshot) -> None:
        ready = bool(snapshot.can_generate)
        self._status.setText(snapshot.message)
        self._status.setAccessibleDescription(snapshot.message)

        if snapshot.state is AiImageState.READY and snapshot.backend_label:
            self._headline.setText(f"AI image · {snapshot.backend_label}")
        else:
            self._headline.setText(_HEADLINE)

        if ready:
            self._chip.setProperty("action", "make")
            self._chip.offer(
                "Make an image",
                "Open Krita on a new canvas with AI Image Generation, where "
                "you describe the image you want.",
            )
            self._edit.offer(
                "Edit an image you have…",
                "Choose a local image you own and open it in Krita, where AI "
                "Image Generation can fill, extend, or remove part of it.",
            )
        elif snapshot.state is AiImageState.NEEDS_KRITA:
            self._chip.setProperty("action", "install_krita")
            self._chip.offer(
                "Install Krita",
                "Open Krita's download page. WebJam does not install anything "
                "for you.",
                tone=StatusChip.RECOVERY,
            )
            self._edit.withdraw()
        elif snapshot.state is AiImageState.NEEDS_PLUGIN:
            self._chip.setProperty("action", "install_plugin")
            self._chip.offer(
                "Install the AI plugin",
                "Open the Krita AI Diffusion download page.",
                tone=StatusChip.RECOVERY,
            )
            self._edit.withdraw()
        else:
            # Outside a room there is nothing to press and nothing missing.
            self._chip.withdraw()
            self._edit.withdraw()

        self._activity.setText(snapshot.activity)
        self._activity.setVisible(bool(snapshot.activity))


__all__ = ["AiImageDialog", "image_name_filter"]
