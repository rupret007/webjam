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
from core.song_clock import SongClockSnapshot
from core.song_help import ChordAdvice, NextChordAdvice, WritingAdvice
from core.song_workbench import CatchUp, FormRow
from core.stem_bench import StemMix, StemTarget
from webjam_qt.theme.tokens import Color, Font, Space

PAGE_SONG = "song"
PAGE_STEMS = "stems"
PAGE_TOOLS = "tools"
PAGE_MEETING = "meeting"

_PAGES = (PAGE_SONG, PAGE_STEMS, PAGE_TOOLS, PAGE_MEETING)
_PAGE_LABELS = {
    PAGE_SONG: "Song",
    PAGE_STEMS: "Stems",
    PAGE_TOOLS: "Tools",
    PAGE_MEETING: "Meeting",
}

# Narrow enough to sit beside a meeting on a laptop, wide enough to read a
# four-chord line without wrapping it mid-progression.
OVERLAY_WIDTH = 340
# At the supported 720px window floor a fixed 340px panel would take nearly
# half the workspace, so it gives ground before the mixer does.
OVERLAY_WIDTH_COMPACT = 264
COMPACT_WINDOW_WIDTH = 1000
_MAX_LIST_ROWS = 4


class SongOverlay(QFrame):
    """Song, Song tools, and meeting truth in one compact non-modal panel."""

    closed = Signal()
    song_tool_requested = Signal(str)     # Music AI verb key
    write_help_requested = Signal()
    chords_requested = Signal(str)        # section name; "" means next missing
    share_sheet_requested = Signal()
    api_key_requested = Signal()
    clock_toggled = Signal()
    section_located = Signal(str)
    suggestion_kept = Signal(str, str)   # section label, chord line
    suggestions_dismissed = Signal()
    stem_mute_toggled = Signal(str)
    stem_solo_toggled = Signal(str)
    sing_this_one_requested = Signal()
    send_stems_to_jam_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SongOverlay")
        self.setAccessibleName("Song tools and meeting panel")
        self.setFixedWidth(OVERLAY_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        # Clicks must not fall through to the mixer underneath.
        self.setAutoFillBackground(True)
        self.setVisible(False)

        self._compact = False
        self._page_buttons: dict[str, QPushButton] = {}
        self._tool_buttons: list[QPushButton] = []
        self._tool_rows: list[QWidget] = []
        self._busy_verb = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        root.setSpacing(Space.SM)

        root.addLayout(self._build_header())
        root.addLayout(self._build_tabs())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_song_page())
        self._stack.addWidget(self._build_stems_page())
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

        # Read at jam distance: the chord the room is on, large and plain,
        # above everything else. A musician glancing up mid-take gets one
        # answer, not a paragraph.
        self._now_chord = QLabel("")
        self._now_chord.setObjectName("SongOverlayNowChord")
        self._now_chord.setTextFormat(Qt.TextFormat.PlainText)
        self._now_chord.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._now_chord.setStyleSheet(
            f"color: {Color.TEXT_PRIMARY};"
            f" font-size: {Font.SIZE_DISPLAY}px;"
            f" font-weight: {Font.WEIGHT_SEMIBOLD};"
        )
        self._now_chord.setVisible(False)
        self._now_next = QLabel("")
        self._now_next.setObjectName("SongOverlayNowNext")
        self._now_next.setTextFormat(Qt.TextFormat.PlainText)
        self._now_next.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._now_next.setStyleSheet(
            f"color: {Color.TEXT_SECONDARY}; font-size: {Font.SIZE_MD}px;"
        )
        self._now_next.setVisible(False)

        self._catch_up_headline = _body_label("", object_name="SongOverlayHeadline")
        self._catch_up_lines = _body_label("")
        self._form_summary = _body_label("No key, tempo, or sections captured yet.")
        # The song's shape with its changes written against it, the way a
        # musician reads a chart while playing.
        self._form_rows = _body_label("")
        self._form_rows.setObjectName("SongOverlayForm")

        # One shared position for the whole room. The clock is a reference the
        # host runs; it does not listen to the band, and the copy says so.
        self._clock_line = _body_label("")
        self._clock_line.setObjectName("SongOverlayClock")
        self._clock_button = QPushButton("▶")
        self._clock_button.setObjectName("GhostButton")
        self._clock_button.setFixedSize(28, 24)
        self._clock_button.setAccessibleName("Start the shared song clock")
        self._clock_button.setToolTip(
            "Start the room's shared bar and section count. It runs from your "
            "tempo; it does not follow what the band plays."
        )
        self._clock_button.clicked.connect(self.clock_toggled.emit)
        self._locate_button = QPushButton("⤒")
        self._locate_button.setObjectName("GhostButton")
        self._locate_button.setFixedSize(28, 24)
        self._locate_button.setAccessibleName("Move the clock to the chosen part")
        self._locate_button.setToolTip("Move the clock to the part chosen below.")
        self._locate_button.clicked.connect(
            lambda: self.section_located.emit(self.selected_section())
        )

        clock_row = QHBoxLayout()
        clock_row.setSpacing(Space.XS)
        clock_row.addWidget(self._clock_button)
        clock_row.addWidget(self._locate_button)
        clock_row.addWidget(self._clock_line, 1)

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
        self._suggestion_headline = _body_label("")
        # Suggestions are never written anywhere until a musician says so, so
        # each one carries its own Keep. Dismiss clears the panel; it changes
        # nothing, because nothing was changed.
        self._suggestion_container = QWidget()
        self._suggestion_layout = QVBoxLayout(self._suggestion_container)
        self._suggestion_layout.setContentsMargins(0, 0, 0, 0)
        self._suggestion_layout.setSpacing(Space.XS)
        self._suggestion_rows: list[QWidget] = []

        self._dismiss_button = QPushButton("Dismiss")
        self._dismiss_button.setObjectName("GhostButton")
        self._dismiss_button.setAccessibleName("Dismiss these suggestions")
        self._dismiss_button.setToolTip("Clear these suggestions. Nothing changes.")
        self._dismiss_button.clicked.connect(self.suggestions_dismissed.emit)
        self._dismiss_button.setVisible(False)

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

        layout.addWidget(self._now_chord)
        layout.addWidget(self._now_next)
        layout.addWidget(self._catch_up_headline)
        layout.addWidget(self._catch_up_lines)
        layout.addWidget(self._form_summary)
        layout.addWidget(self._form_rows)
        layout.addLayout(clock_row)
        layout.addWidget(self._section_picker)
        layout.addLayout(buttons)
        layout.addWidget(self._advice)
        layout.addWidget(self._suggestion_headline)
        layout.addWidget(self._suggestion_container)
        layout.addWidget(self._dismiss_button)
        layout.addWidget(self._results)
        layout.addStretch(1)
        layout.addWidget(self._share_button)
        return page

    def _build_stems_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)

        self._stem_status = _body_label(
            "No stems yet. Run Split stems on a file you own."
        )
        # These chips sit beside the reference; the musicians' faders are a
        # different mix and nothing here moves them.
        self._stem_boundary = _body_label(
            "These are the reference, not the band. Musician faders are unchanged."
        )
        self._stem_boundary.setObjectName("SongOverlayMuted")
        self._stem_container = QWidget()
        self._stem_layout = QVBoxLayout(self._stem_container)
        self._stem_layout.setContentsMargins(0, 0, 0, 0)
        self._stem_layout.setSpacing(Space.XS)
        self._stem_rows: list[QWidget] = []

        self._sing_button = QPushButton("Sing this one")
        self._sing_button.setObjectName("GhostButton")
        self._sing_button.setAccessibleName("Mute the vocal stems")
        self._sing_button.setToolTip(
            "Mutes the record's vocal so the room sings it."
        )
        self._sing_button.clicked.connect(self.sing_this_one_requested.emit)
        self._sing_button.setEnabled(False)

        self._send_stems_button = QPushButton("Send to jam")
        self._send_stems_button.setObjectName("GhostButton")
        self._send_stems_button.setAccessibleName(
            "Send what you can hear into the jam as the Shared Track"
        )
        self._send_stems_button.clicked.connect(
            self.send_stems_to_jam_requested.emit
        )
        self._send_stems_button.setEnabled(False)

        self._stem_note = _body_label("")
        self._stem_note.setObjectName("SongOverlayMuted")

        layout.addWidget(self._stem_status)
        layout.addWidget(self._stem_boundary)
        layout.addWidget(self._stem_container)
        layout.addStretch(1)
        layout.addWidget(self._sing_button)
        layout.addWidget(self._send_stems_button)
        layout.addWidget(self._stem_note)
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

        # ADR 0002: this page explains; it never adds a second button for an
        # action something else already owns. Copy Invite is a HUD primary
        # action and the meeting mute handoff belongs to Conversation, so
        # neither is duplicated here.
        self._mute_lines = _body_label("")
        self._mute_caution = _body_label("")
        self._mute_caution.setObjectName("SongOverlayMuted")
        self._meeting_owner = _body_label("")
        self._meeting_owner.setObjectName("SongOverlayMuted")
        self._recording_note = _body_label("")
        self._recording_note.setObjectName("SongOverlayMuted")
        self._end_note = _body_label("")
        self._end_note.setObjectName("SongOverlayMuted")

        layout.addWidget(self._mute_lines)
        layout.addWidget(self._mute_caution)
        layout.addWidget(self._meeting_owner)
        layout.addWidget(self._recording_note)
        layout.addStretch(1)
        layout.addWidget(self._end_note)
        return page

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_available_width(self, window_width: int) -> None:
        """Give ground to the mixer before the mixer gives ground to us.

        The panel is a companion to the jam, not a competitor for it, so on a
        narrow window it narrows first. Below the threshold the large current
        chord also steps down, because a 32px glyph in a 264px column pushes
        everything useful off the bottom.
        """

        compact = int(window_width or 0) < COMPACT_WINDOW_WIDTH
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedWidth(OVERLAY_WIDTH_COMPACT if compact else OVERLAY_WIDTH)
        size = Font.SIZE_XL if compact else Font.SIZE_DISPLAY
        self._now_chord.setStyleSheet(
            f"color: {Color.TEXT_PRIMARY};"
            f" font-size: {size}px;"
            f" font-weight: {Font.WEIGHT_SEMIBOLD};"
        )

    @property
    def compact(self) -> bool:
        return self._compact

    def showEvent(self, event) -> None:  # noqa: N802
        """Size to the window the moment the panel appears.

        Opening on an already-narrow window must not draw one wide frame and
        then snap; the window's resize event may have happened long before.
        """

        window = self.window()
        if window is not None:
            self.set_available_width(window.width())
        super().showEvent(event)

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

    def set_sections(
        self,
        names: tuple[str, ...],
        *,
        current: str = "",
    ) -> None:
        """Offer the song's own parts, keeping the current choice if it lives.

        ``current`` is the part the clock is on. While the room is playing,
        "chords for the bridge" almost always means the part they are in or
        heading into, so that becomes the default instead of a generic guess.
        """

        previous = self.selected_section()
        self._section_picker.blockSignals(True)
        self._section_picker.clear()
        self._section_picker.addItem("Next part", "")
        for name in names:
            self._section_picker.addItem(name, name)
        wanted = previous or str(current or "")
        index = self._section_picker.findData(wanted)
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
        clock: SongClockSnapshot | None = None,
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

        if clock is not None:
            self.set_clock(clock)
        self._render_now_chord(clock)
        summary = form_summary or "No key, tempo, or sections captured yet."
        self._form_summary.setText(summary)
        # The catch-up already leads with the song line for a late arrival;
        # repeating it directly underneath just costs height in a narrow pane.
        self._form_summary.setVisible(summary not in catch_up_lines)
        here = clock.section_label if clock is not None else ""
        self._form_rows.setText(
            "\n".join(
                # The playhead marks where the room is, so the form reads like
                # a chart being followed rather than a list.
                f"{'▸ ' if row.label == here and here else '  '}{row.describe()}"
                for row in form_rows
            )
        )
        self._form_rows.setVisible(bool(form_rows))
        self._form_rows.setAccessibleName(
            "Song form: "
            + "; ".join(row.describe().replace("\n", ", ") for row in form_rows)
            if form_rows
            else "No song form written yet"
        )
        self._advice.setText(_advice_text(advice, next_chords))
        self._advice.setVisible(bool(self._advice.text()))
        self._render_suggestions(chords)
        self._results.setText("\n".join(results[:_MAX_LIST_ROWS]))
        self._results.setVisible(bool(results))
        self._share_button.setEnabled(bool(sheet_shareable))
        self._share_button.setToolTip(
            "Posts the key, tempo, and sections into band chat so a late "
            "arrival can catch up. Notes stay on this computer."
            if sheet_shareable
            else "Write a key, tempo, or section in the notes first."
        )

    def _render_now_chord(self, clock: SongClockSnapshot | None) -> None:
        """Show the chord the room is on, or nothing.

        Only shown while the position is actually known. A large chord that is
        a guess would be the most confident wrong thing on the screen.
        """

        if (
            clock is None
            or not clock.running
            or not clock.section_label
            or not clock.chords_now
        ):
            self._now_chord.setVisible(False)
            self._now_next.setVisible(False)
            return

        chords = clock.chords_now
        # Which chord of the part, from the bar. The room writes one chord per
        # bar in the common case; anything else falls back to the first.
        index = (max(1, clock.bar_in_section) - 1) % len(chords)
        current = chords[index]
        following = chords[(index + 1) % len(chords)] if len(chords) > 1 else ""

        self._now_chord.setText(current)
        self._now_chord.setAccessibleName(f"Current chord {current}")
        self._now_chord.setAccessibleDescription(
            f"{clock.section_label}, bar {clock.bar_in_section}"
            + (f", next chord {following}" if following else "")
            + ". Position is a reference the host runs, not audio-followed."
        )
        self._now_chord.setVisible(True)
        self._now_next.setText(
            f"{clock.section_label} · next {following}"
            if following
            else clock.section_label
        )
        self._now_next.setVisible(True)

    def _render_suggestions(self, chords: ChordAdvice | None) -> None:
        """Draw each suggestion with the one tap that would keep it."""

        for row in self._suggestion_rows:
            self._suggestion_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._suggestion_rows = []

        if chords is None:
            self._suggestion_headline.setText("")
            self._suggestion_headline.setVisible(False)
            self._dismiss_button.setVisible(False)
            return

        if not chords.available:
            # The refusal is the answer: say what is missing and offer nothing.
            self._suggestion_headline.setText(chords.headline())
            self._suggestion_headline.setVisible(True)
            self._dismiss_button.setVisible(True)
            return

        headline = chords.headline()
        if chords.rewrites_existing:
            headline = (
                f"{chords.section_label} already plays "
                f"{' '.join(chords.existing_chords)}. Instead:\n{headline}"
            )
        self._suggestion_headline.setText(headline)
        self._suggestion_headline.setVisible(True)

        for suggestion in chords.suggestions[:_MAX_LIST_ROWS]:
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(1)

            headline = QHBoxLayout()
            headline.setSpacing(Space.XS)
            text = _body_label(f"Suggestion · {suggestion.chord_line}")
            headline.addWidget(text, 1)
            keep = QPushButton("Keep")
            keep.setObjectName("GhostButton")
            keep.setFixedHeight(22)
            keep.setAccessibleName(
                f"Keep {suggestion.chord_line} for {chords.section_label}"
            )
            keep.setToolTip(
                f"Write {suggestion.chord_line} under {chords.section_label} in "
                "your notes. The Studio arrangement is not touched."
            )
            keep.clicked.connect(
                lambda _checked=False,
                label=chords.section_label,
                line=suggestion.chord_line: self.suggestion_kept.emit(label, line)
            )
            headline.addWidget(keep)
            row_layout.addLayout(headline)

            reason = _body_label(
                f"{suggestion.numeral_line} · "
                f"{suggestion.reason} {suggestion.context}".strip()
            )
            reason.setObjectName("SongOverlayMuted")
            row_layout.addWidget(reason)

            self._suggestion_layout.addWidget(row)
            self._suggestion_rows.append(row)

        self._dismiss_button.setVisible(True)

    def clear_suggestions(self) -> None:
        """Drop the suggestions on screen. Nothing else changes."""

        self._render_suggestions(None)
        self._advice.setText("")
        self._advice.setVisible(False)
        self._suggestion_headline.setText("")
        self._suggestion_headline.setVisible(False)

    def set_clock(self, snapshot: SongClockSnapshot) -> None:
        """Render the shared position, and never imply it follows the band."""

        running = snapshot.running
        follows_track = snapshot.follows_shared_track
        self._clock_button.setText("■" if running else "▶")
        self._clock_button.setAccessibleName(
            "Stop the shared song clock" if running else "Start the shared song clock"
        )
        # While a Shared Track holds a song it owns the transport, so the
        # panel does not offer a second, competing start button.
        self._clock_button.setEnabled(
            snapshot.has_form and snapshot.tempo_bpm > 0 and not follows_track
        )
        self._locate_button.setEnabled(snapshot.has_form and not follows_track)

        if not snapshot.has_form:
            self._clock_line.setText("Write a section header to start the clock.")
            return
        if snapshot.tempo_bpm <= 0:
            self._clock_line.setText("Write a tempo to start the clock.")
            return
        line = snapshot.position_label or "Not started"
        if follows_track:
            line = f"{line} · with Shared Track"
        if snapshot.section_lengths_assumed:
            line = f"{line} · lengths assumed"
        self._clock_line.setText(line)
        # Described, never announced. A screen reader interrupting every bar
        # would make the panel unusable in the room it was built for; a
        # musician reads this when they ask for it.
        self._clock_line.setAccessibleName(
            f"Song position: {line}. Read on request; not announced."
        )
        self._clock_line.setToolTip(
            (
                "Counting against the Shared Track, which the host controls. "
                "Bars assume the file starts at bar one at this tempo."
                if follows_track
                else "A shared reference the host runs. WebJam does not follow "
                "the live audio, so this will not correct if the band drifts."
            )
            + " Write \"[Verse x8]\" to state a part's length."
        )

    def set_stems(
        self,
        *,
        stems: tuple[StemTarget, ...],
        mix: StemMix | None,
        note: str = "",
        can_send: bool = False,
    ) -> None:
        """Render separated stems as faders sitting beside the live mix."""

        for row in self._stem_rows:
            self._stem_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._stem_rows = []

        audible = {item.name for item in (mix.audible if mix is not None else ())}
        for stem in stems:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(Space.XS)
            name = QLabel(stem.label)
            name.setObjectName("SongOverlayBody")
            name.setToolTip(stem.hint or stem.label)
            row_layout.addWidget(name, 1)
            for text, signal, active, description in (
                ("M", self.stem_mute_toggled, stem.muted, "Mute"),
                ("S", self.stem_solo_toggled, stem.solo, "Solo"),
            ):
                button = QPushButton(text)
                button.setObjectName("GhostButton")
                button.setFixedSize(26, 22)
                button.setCheckable(True)
                button.setChecked(bool(active))
                # Named so nobody can mistake this for a band fader or a
                # meeting mute. It is one stem of a reference file.
                button.setAccessibleName(
                    f"{description} the {stem.label} stem of the reference file"
                )
                button.setToolTip(
                    f"{description} {stem.label} in the reference. "
                    "Musicians and the meeting are unaffected."
                )
                button.clicked.connect(
                    lambda _checked=False, key=stem.name, emit=signal: emit.emit(key)
                )
                row_layout.addWidget(button)
            heard = QLabel("·" if stem.name in audible else " ")
            heard.setObjectName("SongOverlayMuted")
            heard.setAccessibleName(
                f"{stem.label} is audible"
                if stem.name in audible
                else f"{stem.label} is silent"
            )
            row_layout.addWidget(heard)
            self._stem_layout.addWidget(row)
            self._stem_rows.append(row)

        self._stem_status.setText(
            mix.describe()
            if mix is not None
            else "No stems yet. Run Split stems on a file you own."
        )
        self._sing_button.setEnabled(bool(stems))
        self._send_stems_button.setEnabled(bool(can_send))
        self._stem_note.setText(note)
        self._stem_note.setVisible(bool(note))

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
            failed = catalog is not None and bool(catalog.error)
            if not failed:
                message = "Checking which Song tools this account can run…"
            elif catalog.retryable:
                message = f"{catalog.error} Reopening Song tools tries again."
            else:
                # Nothing to retry: the answer will be the same until the key
                # changes, so do not send anyone round the loop.
                message = catalog.error
            self._tools_status.setText(message)
            self._tools_unsupported.setText("")
            return

        self._tools_status.setText(catalog.summary_line())
        for capability in catalog.available:
            # A songwriter should not have to hover to learn what a producer's
            # word means, so the plain purpose is on screen under the button.
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(1)

            button = QPushButton(capability.label)
            button.setObjectName("GhostButton")
            button.setAccessibleName(f"Run {capability.label} on a file you pick")
            hint = " ".join(capability.verb.summary.split())
            if capability.shared_template:
                hint = f"{hint} Uses the shared Music AI template."
            if not is_host:
                hint = (
                    f"{hint} Only the host can send a file to Music AI."
                )
            button.setToolTip(hint)
            button.setAccessibleDescription(hint)
            button.setEnabled(is_host and not self._busy_verb)
            button.clicked.connect(
                lambda _checked=False, key=capability.key: (
                    self.song_tool_requested.emit(key)
                )
            )
            row_layout.addWidget(button)

            purpose = _body_label(" ".join(capability.verb.summary.split()))
            purpose.setObjectName("SongOverlayMuted")
            row_layout.addWidget(purpose)

            self._tools_layout.addWidget(row)
            self._tool_rows.append(row)
            self._tool_buttons.append(button)

        self._tools_unsupported.setText(_unsupported_text(catalog))

    def set_meeting_state(
        self,
        *,
        mutes: MuteSurface | None,
        end_note: str = "",
        recording_note: str = "",
        meeting_configured: bool = False,
    ) -> None:
        """Render both mutes and what ending the jam will not end."""

        if mutes is None:
            self._mute_lines.setText("")
            self._mute_caution.setText("")
            self._meeting_owner.setText("")
        else:
            self._mute_lines.setText(
                "\n".join(control.describe() for control in mutes.controls)
            )
            self._mute_caution.setText(mutes.caution())
            meeting = mutes.meeting
            self._meeting_owner.setText(
                f"{meeting.action_label} from Conversation. {meeting.hint}"
                if meeting is not None
                else ""
            )
        self._meeting_owner.setVisible(bool(self._meeting_owner.text()))
        self._recording_note.setText(recording_note)
        self._recording_note.setVisible(bool(recording_note))
        # Two separate facts, both worth saying, neither of them a button:
        # ending the jam does not end the meeting, and the invite lives on the
        # session bar.
        invite_note = "Copy Invite on the session bar sends the jam link" + (
            " and the meeting link." if meeting_configured else "."
        )
        self._end_note.setText(
            f"{end_note}\n{invite_note}" if end_note else invite_note
        )
        self._end_note.setVisible(True)

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
        for row in self._tool_rows:
            self._tools_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._tool_rows = []
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
    next_chords: NextChordAdvice | None = None,
) -> str:
    lines: list[str] = []
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


__all__ = [
    "COMPACT_WINDOW_WIDTH",
    "OVERLAY_WIDTH",
    "OVERLAY_WIDTH_COMPACT",
    "PAGE_MEETING",
    "PAGE_SONG",
    "PAGE_STEMS",
    "PAGE_TOOLS",
    "SongOverlay",
]
