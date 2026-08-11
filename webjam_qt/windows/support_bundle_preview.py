"""Musician-facing privacy preview for the canonical support artifact."""
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.support_bundle import SupportBundlePreview
from webjam_qt.theme.tokens import Space


class SupportBundlePreviewDialog(QDialog):
    """Show the exact safe archive snapshot before asking where to save it."""

    def __init__(
        self,
        preview: SupportBundlePreview,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.preview = preview
        self.setObjectName("SupportBundlePreviewDialog")
        self.setWindowTitle("WebJam — Save Support Bundle")
        self.setModal(True)
        self.resize(620, 560)
        self.setMinimumSize(520, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        eyebrow = QLabel("SUPPORT BUNDLE")
        eyebrow.setObjectName("SupportPreviewEyebrow")
        root.addWidget(eyebrow)
        title = QLabel("Review before saving")
        title.setObjectName("SupportPreviewTitle")
        root.addWidget(title)

        privacy = QLabel(
            "No recordings, session notes, meeting-service content, meeting links, "
            "authentication secrets, or arbitrary personal files are included."
        )
        privacy.setObjectName("SupportPreviewPrivacy")
        privacy.setWordWrap(True)
        privacy.setAccessibleName("Support bundle privacy summary")
        root.addWidget(privacy)

        summary = QLabel(
            "Included facts: WebJam and music-engine versions, operating system, "
            "sanitized engine and recorder health, reconnects, errors, tests, "
            "and cleanup results when available."
        )
        summary.setObjectName("SupportPreviewSummary")
        summary.setWordWrap(True)
        root.addWidget(summary)

        files_title = QLabel(
            f"Exact files ({len(preview.archive_files)})"
        )
        files_title.setObjectName("SupportPreviewSectionTitle")
        root.addWidget(files_title)

        files_scroll = QScrollArea()
        files_scroll.setObjectName("SupportPreviewFiles")
        files_scroll.setWidgetResizable(True)
        files_scroll.setMaximumHeight(150)
        files_content = QWidget()
        files_layout = QVBoxLayout(files_content)
        files_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        files_layout.setSpacing(Space.XS)
        for name in preview.archive_files:
            item = QLabel(f"• {name}")
            item.setObjectName("SupportPreviewFile")
            item.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            files_layout.addWidget(item)
        files_layout.addStretch(1)
        files_scroll.setWidget(files_content)
        root.addWidget(files_scroll)

        report_title = QLabel("Sanitized report preview")
        report_title.setObjectName("SupportPreviewSectionTitle")
        root.addWidget(report_title)
        self._report = QPlainTextEdit()
        self._report.setObjectName("SupportPreviewReport")
        self._report.setReadOnly(True)
        self._report.setPlainText(preview.copy_text)
        self._report.setAccessibleName("Sanitized support report preview")
        root.addWidget(self._report, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Choose Where to Save")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.setAccessibleName("Continue to choose where to save the support bundle")
        save.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

    @property
    def exact_archive_files(self) -> tuple[str, ...]:
        return self.preview.archive_files

    @property
    def logical_fields(self) -> tuple[str, ...]:
        fields = self.preview.manifest.get("logical_fields", ())
        if isinstance(fields, list):
            return tuple(str(item) for item in fields)
        return ()

    @property
    def privacy_facts(self) -> Mapping[str, object]:
        value = self.preview.manifest.get("privacy", {})
        return value if isinstance(value, Mapping) else {}
