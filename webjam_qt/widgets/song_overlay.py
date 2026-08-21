"""Compact in-session chrome for the song, its tools, and the meeting beside it.

This is a child widget of the conductor workspace, not a window and not a
dialog. That is the whole point. WebJam is expected to sit in a narrow pane
next to a free Webex window (see ``webjam_qt.controllers.window_layout``), so
anything that raised itself, took the foreground, or blocked on ``exec()``
would pull a musician out of the meeting mid-take. Nothing here calls
``raise_``, ``activateWindow``, ``exec``, or ``setFocus``; the overlay appears
in place, and the musician keeps whatever they were typing in.

The three pages answer the three questions that actually come up in a live
room: what is this song, what can I run on it, and which mute am I touching.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.creative_modes import CreatorProfile
from core.meeting_companion import MuteSurface
from core.music_ai_catalog import UNSUPPORTED_MOISES_FEATURES, SongToolCatalog
from core.song_help import ChordAdvice, NextChordAdvice, WritingAdvice
from core.song_workbench import CatchUp, FormRow
from webjam_qt.theme.tokens import Space

PAGE_SONG = "song"
PAGE_TOOLS = "tools"
PAGE_MEETING = "meeting"

_PAGES = (PAGE_SONG, PAGE_TOOLS, PAGE_MEETING)
_PAGE_LABELS = {PAGE_SONG: "Song", PAGE_TOOLS: "Tools", PAGE_MEETING: "Meeting"}

# Narrow enough to sit beside a meeting on a laptop, wide enough to read a
# four-chord line without wrapping it mid-progression.
OVERLAY_WIDTH = 340
_MAX_LIST_ROWS = 4


class SongOverlay(QFrame):
    """Song, Song tools, and meeting truth in one compact non-modal panel."""

    closed = Signal()
    song_tool_requested = Signal(str)     # Music AI verb key
    write_help_requested = Signal()
    chords_requested = Signal(str)        # section role; "" means next missing
    share_sheet_requested = Signal()
    api_key_requested = Signal()
    mute_help_requested = Signal()
    invite_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SongOverlay")
        self.setAccessibleName("Song tools and meeting panel")
        self.setFixedWidth(OVERLAY_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        # Clicks must not fall through to the mixer underneath.
        self.setAutoFillBackground(True)
        self.setVisible(False)

        self._page_buttons: dict[str, QPushButton] = {}
        self._tool_buttons: list[QPushButton] = []
        self._busy_verb = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        root.setSpacing(Space.SM)

        root.addLayout(self._build_header())
        root.addLayout(self._build_tabs())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_song_page())
        self._stack.addWidget(self._build_tools_page())
        self._stack.addWidget(self._build_meeting_page())
        root.addWidget(self._stack, 1)

        self.show_page(PAGE_SONG)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.XS)
        self._title = QLabel("Song")
        self._title.setObjectName("SongOverlayTitle")
        row.addWidget(self._title, 1)
        close = QPushButton("✕")
        close.setObjectName("GhostButton")
        close.setFixedSize(24, 24)
        close.setAccessibleName("Close song panel")
        close.setToolTip("Close")
        close.clicked.connect(self._on_close)
        row.addWidget(close)
        return row

    def _build_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.XS)
        for key in _PAGES:
            button = QPushButton(_PAGE_LABELS[key])
            button.setObjectName("GhostButton")
            button.setCheckable(True)
            button.setAccessibleName(f"Show {_PAGE_LABELS[key].lower()}")
            button.clicked.connect(lambda _checked, page=key: self.show_page(page))
            self._page_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_song_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)

        self._catch_up_headline = _body_label("", object_name="SongOverlayHeadline")
        self._catch_up_lines = _body_label("")
        self._form_summary = _body_label("No key, tempo, or sections captured yet.")
        # The song's shape with its changes written against it, the way a
        # musician reads a chart while playing.
        self._form_rows = _body_label("")
        self._form_rows.setObjectName("SongOverlayForm")

        # Help is asked for one part of the song, not for a song in general.
        self._section_picker = QComboBox()
        self._section_picker.setObjectName("SongOverlaySection")
        self._section_picker.setAccessibleName("Part of the song to work on")
        self._section_picker.setToolTip(
            "Which part to write. Suggestions are made against the parts "
            "either side of it."
        )
        self._section_picker.addItem("Next part", "")

        self._write_button = QPushButton("Help write")
        self._write_button.setObjectName("GhostButton")
        self._write_button.setAccessibleName("Suggest what to write next")
        self._write_button.setToolTip(
            "Suggests the next section and lyric moves from the song on this "
            "computer. Nothing is uploaded."
        )
        self._write_button.clicked.connect(self.write_help_requested.emit)

        self._chords_button = QPushButton("Suggest chords")
        self._chords_button.setObjectName("GhostButton")
        self._chords_button.setAccessibleName("Suggest chords for another part")
        self._chords_button.setToolTip(
            "Suggests changes for a part the song does not have yet, in its "
            "key. Nothing is uploaded."
        )
        self._chords_button.clicked.connect(
            lambda: self.chords_requested.emit(self.selected_section())
        )

        buttons = QHBoxLayout()
        buttons.setSpacing(Space.XS)
        buttons.addWidget(self._write_button)
        buttons.addWidget(self._chords_button)

        self._advice = _body_label("")
        self._results = _body_label("")

        self._share_button = QPushButton("Share sheet to chat")
        self._share_button.setObjectName("GhostButton")
        self._share_button.setAccessibleName("Share the song sheet to band chat")
        self._share_button.setToolTip(
            "Posts the key, tempo, and sections into band chat so a late "
            "arrival can catch up. Notes stay on this computer."
        )
        self._share_button.clicked.connect(self.share_sheet_requested.emit)
        self._share_button.setEnabled(False)

        layout.addWidget(self._catch_up_headline)
        layout.addWidget(self._catch_up_lines)
        layout.addWidget(self._form_summary)
        layout.addWidget(self._form_rows)
        layout.addWidget(self._section_picker)
        layout.addLayout(buttons)
        layout.addWidget(self._advice)
        layout.addWidget(self._results)
        layout.addStretch(1)
        layout.addWidget(self._share_button)
        return page

    def _build_tools_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)

        self._tools_status = _body_label("")
        self._tools_container = QWidget()
        self._tools_layout = QVBoxLayout(self._tools_container)
        self._tools_layout.setContentsMargins(0, 0, 0, 0)
        self._tools_layout.setSpacing(Space.XS)

        self._key_button = QPushButton("Add API key…")
        self._key_button.setObjectName("GhostButton")
        self._key_button.setAccessibleName("Add a Music AI API key")
        self._key_button.clicked.connect(self.api_key_requested.emit)
        self._key_button.setVisible(False)

        self._tools_unsupported = _body_label("")
        self._tools_unsupported.setObjectName("SongOverlayMuted")

        layout.addWidget(self._tools_status)
        layout.addWidget(self._tools_container)
        layout.addWidget(self._key_button)
        layout.addStretch(1)
        layout.addWidget(self._tools_unsupported)
        return page

    def _build_meeting_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)

        self._mute_lines = _body_label("")
        self._mute_caution = _body_label("")
        self._mute_caution.setObjectName("SongOverlayMuted")

        self._mute_button = QPushButton("Open meeting to mute")
        self._mute_button.setObjectName("GhostButton")
        self._mute_button.setAccessibleName("Open the meeting app to mute")
        self._mute_button.clicked.connect(self.mute_help_requested.emit)
        self._mute_button.setVisible(False)

        self._invite_button = QPushButton("Copy one invite")
        self._invite_button.setObjectName("GhostButton")
        self._invite_button.setAccessibleName("Copy one invite for jam and meeting")
        self._invite_button.setToolTip(
            "Copies one message with the jam link and, if set, the meeting link."
        )
        self._invite_button.clicked.connect(self.invite_requested.emit)

        self._end_note = _body_label("")
        self._end_note.setObjectName("SongOverlayMuted")

        layout.addWidget(self._mute_lines)
        layout.addWidget(self._mute_caution)
        layout.addWidget(self._mute_button)
        layout.addWidget(self._invite_button)
        layout.addStretch(1)
        layout.addWidget(self._end_note)
        return page

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def show_page(self, page: str) -> None:
        key = page if page in _PAGES else PAGE_SONG
        self._stack.setCurrentIndex(_PAGES.index(key))
        self._title.setText(_PAGE_LABELS[key])
        for name, button in self._page_buttons.items():
            button.setChecked(name == key)

    def current_page(self) -> str:
        return _PAGES[self._stack.currentIndex()]

    def set_creator_profile(self, profile: CreatorProfile) -> None:
        """Apply profile vocabulary. Only Music ever shows this panel."""

        if not isinstance(profile, CreatorProfile):
            raise TypeError("profile must be a CreatorProfile")
        preview = " · Preview" if profile.is_preview else ""
        self.setAccessibleDescription(
            f"{profile.label} song tools and meeting panel{preview}. "
            "Suggestions are generated on this computer."
        )

    def selected_section(self) -> str:
        """Return the part help was asked for; ``""`` means the next one."""

        return str(self._section_picker.currentData() or "")

    def set_sections(self, names: tuple[str, ...]) -> None:
        """Offer the song's own parts, keeping the current choice if it lives."""

        previous = self.selected_section()
        self._section_picker.blockSignals(True)
        self._section_picker.clear()
        self._section_picker.addItem("Next part", "")
        for name in names:
            self._section_picker.addItem(name, name)
        index = self._section_picker.findData(previous)
        self._section_picker.setCurrentIndex(max(0, index))
        self._section_picker.blockSignals(False)
        self._section_picker.setEnabled(bool(names))

    def set_song_state(
        self,
        *,
        catch_up: CatchUp | None = None,
        form_summary: str = "",
        form_rows: tuple[FormRow, ...] = (),
        advice: WritingAdvice | None = None,
        chords: ChordAdvice | None = None,
        next_chords: NextChordAdvice | None = None,
        results: tuple[str, ...] = (),
        sheet_shareable: bool = False,
    ) -> None:
        """Render the song page from the room's own material."""

        catch_up_lines = tuple(catch_up.lines) if catch_up is not None else ()
        if catch_up is not None and catch_up.has_content:
            self._catch_up_headline.setText(catch_up.headline)
            self._catch_up_headline.setVisible(True)
            self._catch_up_lines.setText("\n".join(catch_up_lines))
            self._catch_up_lines.setVisible(True)
        else:
            self._catch_up_headline.setVisible(False)
            self._catch_up_lines.setVisible(False)

        summary = form_summary or "No key, tempo, or sections captured yet."
        self._form_summary.setText(summary)
        # The catch-up already leads with the song line for a late arrival;
        # repeating it directly underneath just costs height in a narrow pane.
        self._form_summary.setVisible(summary not in catch_up_lines)
        self._form_rows.setText("\n".join(row.describe() for row in form_rows))
        self._form_rows.setVisible(bool(form_rows))
        self._advice.setText(_advice_text(advice, chords, next_chords))
        self._advice.setVisible(bool(self._advice.text()))
        self._results.setText("\n".join(results[:_MAX_LIST_ROWS]))
        self._results.setVisible(bool(results))
        self._share_button.setEnabled(bool(sheet_shareable))
        self._share_button.setToolTip(
            "Posts the key, tempo, and sections into band chat so a late "
            "arrival can catch up. Notes stay on this computer."
            if sheet_shareable
            else "Write a key, tempo, or section in the notes first."
        )

    def set_tools_state(
        self,
        *,
        catalog: SongToolCatalog | None,
        has_api_key: bool,
        is_host: bool,
        missing_key_text: str = "",
    ) -> None:
        """Render only what this account can actually run.

        Verbs the account's workflow list does not include are never drawn as
        buttons. They are named underneath instead, so an absent feature reads
        as a fact about the account rather than as a bug.
        """

        self._clear_tool_buttons()
        self._key_button.setVisible(not has_api_key)

        if not has_api_key:
            self._tools_status.setText(missing_key_text or "Add a Music AI API key.")
            self._tools_unsupported.setText(_unsupported_text(None))
            return
        if catalog is None or not catalog.discovered:
            self._tools_status.setText(
                catalog.error if catalog is not None and catalog.error
                else "Checking which Song tools this account can run…"
            )
            self._tools_unsupported.setText("")
            return

        self._tools_status.setText(catalog.summary_line())
        for capability in catalog.available:
            button = QPushButton(capability.label)
            button.setObjectName("GhostButton")
            button.setAccessibleName(f"Run {capability.label} on a file you pick")
            hint = capability.verb.summary
            if capability.shared_template:
                hint = f"{hint} Uses the shared Music AI template."
            if not is_host:
                hint = (
                    f"{hint} Only the host can send a file to Music AI."
                )
            button.setToolTip(hint)
            button.setEnabled(is_host and not self._busy_verb)
            button.clicked.connect(
                lambda _checked=False, key=capability.key: (
                    self.song_tool_requested.emit(key)
                )
            )
            self._tools_layout.addWidget(button)
            self._tool_buttons.append(button)

        self._tools_unsupported.setText(_unsupported_text(catalog))

    def set_meeting_state(
        self,
        *,
        mutes: MuteSurface | None,
        end_note: str = "",
        meeting_configured: bool = False,
    ) -> None:
        """Render both mutes and what ending the jam will not end."""

        if mutes is None:
            self._mute_lines.setText("")
            self._mute_caution.setText("")
            self._mute_button.setVisible(False)
        else:
            self._mute_lines.setText(
                "\n".join(control.describe() for control in mutes.controls)
            )
            self._mute_caution.setText(mutes.caution())
            meeting = mutes.meeting
            self._mute_button.setVisible(meeting is not None)
            if meeting is not None:
                self._mute_button.setText(meeting.action_label)
                self._mute_button.setToolTip(meeting.hint)
        self._end_note.setText(end_note)
        self._end_note.setVisible(bool(end_note))
        self._invite_button.setToolTip(
            "Copies one message with the jam link and the meeting link."
            if meeting_configured
            else "Copies the jam link. Add a meeting link in Settings to "
            "include it in the same invite."
        )

    def set_busy(self, verb_key: str, message: str = "") -> None:
        """Show that one tool is running without blocking the session."""

        self._busy_verb = str(verb_key or "")
        if self._busy_verb:
            self._tools_status.setText(message or "Working…")
        for button in self._tool_buttons:
            button.setEnabled(not self._busy_verb)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _clear_tool_buttons(self) -> None:
        for button in self._tool_buttons:
            self._tools_layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self._tool_buttons = []

    def _on_close(self) -> None:
        self.setVisible(False)
        self.closed.emit()


def _body_label(text: str, *, object_name: str = "SongOverlayBody") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _advice_text(
    advice: WritingAdvice | None,
    chords: ChordAdvice | None,
    next_chords: NextChordAdvice | None = None,
) -> str:
    lines: list[str] = []
    if chords is not None:
        if chords.rewrites_existing:
            lines.append(
                f"{chords.section_label} already plays "
                f"{' '.join(chords.existing_chords)}. Instead:"
            )
        lines.append(chords.headline())
        for suggestion in chords.suggestions[:_MAX_LIST_ROWS]:
            lines.append(f"  {suggestion.chord_line}   {suggestion.numeral_line}")
            detail = f"{suggestion.reason} {suggestion.context}".strip()
            lines.append(f"  {detail}")
    if next_chords is not None:
        lines.append(next_chords.headline())
        for candidate in next_chords.candidates[:_MAX_LIST_ROWS]:
            lines.append(f"  {candidate.describe()}")
    if advice is not None:
        for idea in advice.ideas[:_MAX_LIST_ROWS]:
            lines.append(f"{idea.headline}: {idea.detail}")
        if advice.rhymes:
            lines.append(f"Rhymes: {', '.join(advice.rhymes)}")
    return "\n".join(lines)


def _unsupported_text(catalog: SongToolCatalog | None) -> str:
    """Name what is missing, so an absent feature is never a mystery."""

    lines: list[str] = []
    if catalog is not None:
        missing = [item.label for item in catalog.unavailable]
        if missing:
            lines.append(f"Not on this account: {', '.join(missing)}.")
    lines.append(
        "Not in the Music AI API: "
        + "; ".join(name for name, _reason in UNSUPPORTED_MOISES_FEATURES)
        + "."
    )
    return "\n".join(lines)


__all__ = ["OVERLAY_WIDTH", "PAGE_MEETING", "PAGE_SONG", "PAGE_TOOLS", "SongOverlay"]
