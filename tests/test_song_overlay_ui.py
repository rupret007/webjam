"""The in-jam song panel: Music only, honest states, and never in the way.

WebJam is expected to sit in the narrow pane beside a free Webex window, so a
panel that raised itself or blocked on a dialog would pull a musician out of
the meeting mid-take. That is asserted here against the source, not just the
behaviour, because a single stray ``activateWindow`` would be enough.
"""

from __future__ import annotations

import inspect

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from core.creative_modes import get_creator_profile_by_key_or_default
from core.meeting_companion import describe_mutes, end_session_prompt
from core.music_ai_catalog import failed_catalog, resolve_song_tools
from core.music_ai_client import MusicAIWorkflow, missing_key_message
from core.music_ai_results import SongArtifact, SongToolRun
from core.song_workbench import SharedTrackView, SongWorkbench
from webjam_qt.widgets import song_overlay as song_overlay_module
from webjam_qt.widgets.song_overlay import (
    PAGE_MEETING,
    PAGE_SONG,
    PAGE_STEMS,
    PAGE_TOOLS,
    SongOverlay,
)
from webjam_qt.windows.conductor_window import ConductorWindow

SHEET = """Key: A minor
Tempo: 104
[Verse]
Am F C G
Driving through the same town twice
"""

ACCOUNT = [
    MusicAIWorkflow("1", "Stem Separation", "stems", "isolate vocals"),
    MusicAIWorkflow("2", "Lyric transcription", "lyrics", "transcribe lyrics"),
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(app):
    widget = SongOverlay()
    yield widget
    widget.deleteLater()


def _tool_labels(widget: SongOverlay) -> list[str]:
    return [button.text() for button in widget._tool_buttons]


# ----------------------------------------------------------------------
# Placement and focus
# ----------------------------------------------------------------------
def test_the_panel_never_steals_focus_from_the_meeting():
    """One stray activateWindow would pull a musician out of their meeting.

    The check is on the parsed call graph rather than the raw text, so the
    module docstring naming these methods does not itself trip it.
    """

    import ast

    forbidden = {
        "activateWindow",
        "raise_",
        "exec",
        "exec_",
        "setFocus",
        "setWindowFlags",
        "showNormal",
    }
    tree = ast.parse(inspect.getsource(song_overlay_module))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & forbidden), sorted(called & forbidden)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "QMessageBox" not in imported
    assert "QDialog" not in imported


def test_the_panel_is_a_child_widget_not_a_window(app):
    """Parented into the conductor body, so showing it opens no new window."""

    window = ConductorWindow(
        mode_entries=[("music", "Music")],
        initial_mode_key="music",
        initial_title="Tuesday",
    )
    try:
        overlay = window.song_overlay
        assert not overlay.isWindow()
        assert overlay.window() is window
    finally:
        window.deleteLater()


def test_the_panel_is_hidden_until_a_musician_asks_for_it(overlay):
    assert not overlay.isVisible()


def test_the_panel_fits_beside_a_meeting(overlay):
    from webjam_qt.controllers.window_layout import MINIMUM_WEBJAM_WIDTH

    assert overlay.width() < MINIMUM_WEBJAM_WIDTH / 2


def test_the_conductor_window_hosts_the_panel_without_showing_it(app):
    window = ConductorWindow(
        mode_entries=[("music", "Music")],
        initial_mode_key="music",
        initial_title="Tuesday",
    )
    try:
        assert window.song_overlay is not None
        assert not window.song_overlay.isVisible()
    finally:
        window.deleteLater()


# ----------------------------------------------------------------------
# Music only
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("profile_key", "expected"),
    [("music", True), ("podcast_voice", False), ("review_rehearsal", False)],
)
def test_song_tools_are_offered_only_in_a_music_session(app, profile_key, expected):
    window = ConductorWindow(
        mode_entries=[("music", "Music")],
        initial_mode_key="music",
        initial_title="Tuesday",
    )
    try:
        window.session_strip.set_creator_profile(
            get_creator_profile_by_key_or_default(profile_key)
        )
        action = window.session_strip._song_tools_action
        assert action.isVisible() is expected
        assert action.isEnabled() is expected
    finally:
        window.deleteLater()


def test_the_music_launch_flow_is_unchanged(app, tmp_path):
    """No fourth start card: Host / Join / Studio, exactly as before."""

    from core.settings import AppSettings
    from webjam_qt.windows.launch_dialog import LaunchDialog

    dialog = LaunchDialog(AppSettings(config_file=str(tmp_path / "settings.json")))
    try:
        role_buttons = [
            dialog._host_button,
            dialog._join_button,
            dialog._studio_button,
        ]
        assert all(isinstance(button, QPushButton) for button in role_buttons)
        assert len({id(button) for button in role_buttons}) == 3
        for attribute in dir(dialog):
            assert "song_tool" not in attribute.lower()
            assert "music_ai" not in attribute.lower()
    finally:
        dialog.deleteLater()


# ----------------------------------------------------------------------
# Fail-closed copy
# ----------------------------------------------------------------------
def test_without_an_api_key_no_tool_is_offered_and_the_copy_is_honest(overlay):
    overlay.set_tools_state(
        catalog=None,
        has_api_key=False,
        is_host=True,
        missing_key_text=missing_key_message(),
    )

    assert _tool_labels(overlay) == []
    status = overlay._tools_status.text()
    assert "music.ai/dash" in status
    assert "Moises app login" in status
    # isHidden() reflects this widget's own state; isVisible() would also be
    # False simply because the unshown panel has no visible ancestor.
    assert not overlay._key_button.isHidden()


def test_a_discovery_failure_offers_nothing_and_shows_why(overlay):
    overlay.set_tools_state(
        catalog=failed_catalog("Music AI rejected this API key."),
        has_api_key=True,
        is_host=True,
    )
    assert _tool_labels(overlay) == []
    assert overlay._tools_status.text() == "Music AI rejected this API key."


def test_only_verbs_the_account_can_run_become_buttons(overlay):
    overlay.set_tools_state(
        catalog=resolve_song_tools(ACCOUNT), has_api_key=True, is_host=True
    )

    assert _tool_labels(overlay) == ["Split stems", "Lyrics"]
    unsupported = overlay._tools_unsupported.text()
    assert "Not on this account" in unsupported
    assert "Sections" in unsupported
    assert "Master" in unsupported


def test_app_only_moises_features_are_named_rather_than_missing(overlay):
    overlay.set_tools_state(
        catalog=resolve_song_tools(ACCOUNT), has_api_key=True, is_host=True
    )
    unsupported = overlay._tools_unsupported.text()
    assert "Not in the Music AI API" in unsupported
    assert "library" in unsupported.lower()


def test_a_guest_sees_the_tools_disabled_with_the_reason(overlay):
    overlay.set_tools_state(
        catalog=resolve_song_tools(ACCOUNT), has_api_key=True, is_host=False
    )

    assert _tool_labels(overlay) == ["Split stems", "Lyrics"]
    assert not any(button.isEnabled() for button in overlay._tool_buttons)
    assert "Only the host" in overlay._tool_buttons[0].toolTip()


def test_a_running_tool_disables_the_others_without_blocking_the_session(overlay):
    overlay.set_tools_state(
        catalog=resolve_song_tools(ACCOUNT), has_api_key=True, is_host=True
    )
    overlay.set_busy("stems", "Running Split stems…")

    assert not any(button.isEnabled() for button in overlay._tool_buttons)
    assert "Running Split stems" in overlay._tools_status.text()

    overlay.set_busy("", "")
    assert all(button.isEnabled() for button in overlay._tool_buttons)


def test_the_tool_list_stays_short(overlay):
    """Six product verbs, not a DAW-full assistant panel."""

    overlay.set_tools_state(
        catalog=resolve_song_tools(
            ACCOUNT
            + [
                MusicAIWorkflow("3", "Chord detection", "c", "detect chords"),
                MusicAIWorkflow("4", "Section finder", "s", "song structure"),
                MusicAIWorkflow("5", "Pitch shift", "p", "change key"),
                MusicAIWorkflow("6", "Master", "m", "mastering"),
                MusicAIWorkflow("7", "Denoise", "d", "noise removal"),
            ]
        ),
        has_api_key=True,
        is_host=True,
    )
    assert len(overlay._tool_buttons) <= 7


# ----------------------------------------------------------------------
# Results attach to the session
# ----------------------------------------------------------------------
def test_results_appear_on_the_session_surface_not_in_a_dialog(overlay):
    workbench = SongWorkbench(title="Tuesday", notes=SHEET)
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j1",
            source_name="mix.wav",
            artifacts=(
                SongArtifact("vocals", "audio", local_path="/tmp/vocals.wav"),
                SongArtifact("drums", "audio", local_path="/tmp/drums.wav"),
            ),
        )
    )
    overlay.set_song_state(
        form_summary=workbench.conductor_line(),
        results=tuple(run.summary_line() for run in workbench.runs),
        sheet_shareable=True,
    )

    assert overlay._results.isVisible() or overlay._results.text()
    assert "Split stems" in overlay._results.text()
    assert "2 audio files" in overlay._results.text()


def test_detected_facts_are_labelled_as_detected_on_the_surface(overlay):
    workbench = SongWorkbench(notes="[Verse]\nAm F\n")
    workbench.attach_run(
        SongToolRun(
            verb_key="chords",
            label="Chords & key",
            workflow_slug="chords",
            job_id="j2",
            source_name="mix.wav",
            detected_key="A minor",
            detected_tempo="104",
        )
    )
    overlay.set_song_state(form_summary=workbench.conductor_line())

    assert "detected by Chords & key" in overlay._form_summary.text()


def test_a_late_arrival_sees_the_catch_up_in_place(overlay):
    workbench = SongWorkbench(title="Tuesday", notes=SHEET)
    overlay.set_song_state(
        catch_up=workbench.catch_up(
            shared_track=SharedTrackView(
                loaded=True, playing=True, source_name="demo.wav", position_s=95
            ),
            elapsed_seconds=1320,
        ),
        form_summary=workbench.conductor_line(),
    )

    assert overlay._catch_up_headline.text() == "You joined 22 minutes in"
    assert "demo.wav — playing 1:35" in overlay._catch_up_lines.text()


def test_the_song_line_is_not_repeated_underneath_the_catch_up(overlay):
    workbench = SongWorkbench(notes=SHEET)
    overlay.set_song_state(
        catch_up=workbench.catch_up(elapsed_seconds=1320),
        form_summary=workbench.conductor_line(),
    )
    assert not overlay._form_summary.isVisible()


def test_sharing_the_sheet_is_disabled_until_there_is_one(overlay):
    overlay.set_song_state(form_summary="", sheet_shareable=False)
    assert not overlay._share_button.isEnabled()
    assert "Write a key" in overlay._share_button.toolTip()

    overlay.set_song_state(form_summary="Key A minor", sheet_shareable=True)
    assert overlay._share_button.isEnabled()


def test_writing_help_and_chords_render_with_their_reasoning(overlay):
    workbench = SongWorkbench(notes=SHEET)
    overlay.set_song_state(
        form_summary=workbench.conductor_line(),
        advice=workbench.writing_advice(),
        chords=workbench.chord_advice(),
    )

    text = overlay._advice.text()
    assert "Suggestions for" in text
    assert "from your notes" in text
    assert any(numeral in text for numeral in ("i", "VI", "VII"))


def test_help_buttons_say_nothing_is_uploaded(overlay):
    for button in (overlay._write_button, overlay._chords_button):
        assert "Nothing is uploaded" in button.toolTip()


# ----------------------------------------------------------------------
# Meeting page
# ----------------------------------------------------------------------
def test_both_mutes_are_shown_on_the_meeting_page(overlay):
    overlay.set_meeting_state(
        mutes=describe_mutes(
            webjam_muted_participants=1,
            participant_count=3,
            meeting_configured=True,
        ),
        end_note=end_session_prompt(
            hosting=True, meeting_configured=True
        ).meeting_note,
        meeting_configured=True,
    )

    lines = overlay._mute_lines.text()
    assert "WebJam mute — what you hear" in lines
    assert "Webex mute — your microphone in the meeting" in lines
    assert "Neither stops your instrument" in overlay._mute_caution.text()
    assert not overlay._mute_button.isHidden()
    assert "stays open" in overlay._end_note.text()


def test_with_no_meeting_the_page_makes_no_claim_about_one(overlay):
    overlay.set_meeting_state(
        mutes=describe_mutes(meeting_configured=False),
        end_note="",
        meeting_configured=False,
    )

    assert not overlay._mute_button.isVisible()
    assert not overlay._end_note.isVisible()
    assert "monitor mix only" in overlay._mute_caution.text()
    assert "Add a meeting link" in overlay._invite_button.toolTip()


# ----------------------------------------------------------------------
# Navigation
# ----------------------------------------------------------------------
@pytest.mark.parametrize("page", [PAGE_SONG, PAGE_STEMS, PAGE_TOOLS, PAGE_MEETING])
def test_each_page_can_be_shown(overlay, page):
    overlay.show_page(page)
    assert overlay.current_page() == page


def test_an_unknown_page_falls_back_to_the_song(overlay):
    overlay.show_page("nope")
    assert overlay.current_page() == PAGE_SONG


def test_closing_the_panel_hides_it_and_reports_once(overlay):
    seen = []
    overlay.closed.connect(lambda: seen.append(True))
    overlay.setVisible(True)
    overlay._on_close()

    assert not overlay.isVisible()
    assert seen == [True]


def test_a_profile_must_be_a_creator_profile(overlay):
    with pytest.raises(TypeError):
        overlay.set_creator_profile("music")


# ----------------------------------------------------------------------
# Form overlay and the section picker
# ----------------------------------------------------------------------
def test_the_form_is_shown_over_the_jam_with_its_chords(overlay):
    workbench = SongWorkbench(title="Tuesday", notes=SHEET)
    overlay.set_song_state(
        form_summary=workbench.conductor_line(),
        form_rows=workbench.form_overlay(),
    )

    text = overlay._form_rows.text()
    assert "Verse: Am F C G" in text
    assert not overlay._form_rows.isHidden()


def test_detected_chords_are_marked_apart_from_written_ones(overlay):
    workbench = SongWorkbench(notes=SHEET)
    workbench.attach_run(
        SongToolRun(
            verb_key="chords",
            label="Chords & key",
            workflow_slug="chords",
            job_id="j7",
            source_name="mix.wav",
            chord_symbols=("Am", "F", "C", "G"),
        )
    )
    overlay.set_song_state(form_rows=workbench.form_overlay())

    text = overlay._form_rows.text()
    assert "Heard on the file: Am F C G ·detected" in text
    assert "Verse: Am F C G\n" in text


def test_an_empty_song_shows_no_form_rows(overlay):
    overlay.set_song_state(form_rows=())
    assert overlay._form_rows.isHidden()


def test_the_picker_offers_the_songs_own_parts(overlay):
    overlay.set_sections(("Verse", "Chorus"))

    labels = [
        overlay._section_picker.itemText(index)
        for index in range(overlay._section_picker.count())
    ]
    assert labels == ["Next part", "Verse", "Chorus"]
    assert overlay.selected_section() == ""


def test_choosing_a_part_is_what_the_chords_button_asks_about(overlay):
    overlay.set_sections(("Verse", "Chorus"))
    overlay._section_picker.setCurrentIndex(2)

    seen: list[str] = []
    overlay.chords_requested.connect(seen.append)
    overlay._chords_button.click()

    assert seen == ["Chorus"]


def test_a_chosen_part_survives_the_song_being_re_read(overlay):
    overlay.set_sections(("Verse", "Chorus"))
    overlay._section_picker.setCurrentIndex(2)
    overlay.set_sections(("Verse", "Chorus", "Bridge"))

    assert overlay.selected_section() == "Chorus"


def test_a_chosen_part_that_disappears_falls_back_to_the_next_one(overlay):
    overlay.set_sections(("Verse", "Chorus"))
    overlay._section_picker.setCurrentIndex(2)
    overlay.set_sections(("Verse",))

    assert overlay.selected_section() == ""


def test_the_picker_is_disabled_until_the_song_has_parts(overlay):
    overlay.set_sections(())
    assert not overlay._section_picker.isEnabled()
    overlay.set_sections(("Verse",))
    assert overlay._section_picker.isEnabled()


def test_rewriting_a_part_says_what_it_already_plays(overlay):
    workbench = SongWorkbench(notes=SHEET)
    overlay.set_song_state(chords=workbench.chord_advice(section_name="Verse"))

    text = overlay._advice.text()
    assert "Verse already plays Am F C G. Instead:" in text


def test_the_seam_reasoning_reaches_the_musician(overlay):
    workbench = SongWorkbench(notes=SHEET)
    overlay.set_song_state(chords=workbench.chord_advice(section_name="Chorus"))
    assert "Verse ends on" in overlay._advice.text()


def test_next_chord_candidates_render_with_their_reasons(overlay):
    workbench = SongWorkbench(notes=SHEET)
    overlay.set_song_state(
        next_chords=workbench.next_chord_advice(section_name="Verse")
    )

    text = overlay._advice.text()
    assert "After Am F C G in Verse" in text
    assert "—" in text


def test_the_song_page_keeps_to_its_control_budget(overlay):
    """Few words: three worded actions, two transport glyphs, one picker.

    The budget is split because a 28px transport glyph does not cost a
    musician the same attention as another labelled button. Anything that adds
    a fourth worded action to this page should have to change this test.
    """

    from PySide6.QtWidgets import QComboBox

    page = overlay._stack.widget(0)
    buttons = [
        child for child in page.findChildren(QPushButton) if not child.isHidden()
    ]
    worded = [button for button in buttons if len(button.text()) > 2]
    glyphs = [button for button in buttons if len(button.text()) <= 2]

    assert sorted(button.text() for button in worded) == [
        "Help write",
        "Share sheet to chat",
        "Suggest chords",
    ]
    assert len(glyphs) == 2
    assert all(button.width() <= 32 for button in glyphs)
    assert len(page.findChildren(QComboBox)) == 1


# ----------------------------------------------------------------------
# The shared clock on the session surface
# ----------------------------------------------------------------------
CLOCKED_SHEET = """Key: G major
Tempo: 120
[Intro x4]
G D
[Verse x8]
G D Em C
"""


def _clocked(monotonic):
    return SongWorkbench(
        title="Tuesday", notes=CLOCKED_SHEET, monotonic=monotonic
    )


def test_the_position_reads_as_bars_within_a_part(overlay):
    now = {"value": 0.0}
    workbench = _clocked(lambda: now["value"])
    workbench.clock.start()
    now["value"] = 10.0

    overlay.set_song_state(clock=workbench.clock_snapshot())
    assert overlay._clock_line.text() == "Verse · bar 2 of 8"
    assert overlay._clock_button.text() == "■"


def test_a_stopped_clock_offers_to_start(overlay):
    workbench = _clocked(lambda: 0.0)
    overlay.set_song_state(clock=workbench.clock_snapshot())

    assert overlay._clock_button.text() == "▶"
    assert overlay._clock_button.isEnabled()


def test_the_clock_says_what_it_needs_before_it_can_run(overlay):
    overlay.set_song_state(clock=SongWorkbench(notes="").clock_snapshot())
    assert "Write a section header" in overlay._clock_line.text()
    assert not overlay._clock_button.isEnabled()

    overlay.set_song_state(
        clock=SongWorkbench(notes="[Verse]\nG D\n").clock_snapshot()
    )
    assert "Write a tempo" in overlay._clock_line.text()
    assert not overlay._clock_button.isEnabled()


def test_assumed_section_lengths_are_admitted_on_the_surface(overlay):
    workbench = SongWorkbench(notes="Tempo: 100\n[Verse]\nG D\n")
    overlay.set_song_state(clock=workbench.clock_snapshot())
    assert "lengths assumed" in overlay._clock_line.text()


def test_the_clock_never_claims_to_follow_the_band_in_its_copy(overlay):
    workbench = _clocked(lambda: 0.0)
    overlay.set_song_state(clock=workbench.clock_snapshot())

    tip = overlay._clock_line.toolTip() + overlay._clock_button.toolTip()
    assert "does not follow" in tip or "does not follow the band" in tip
    assert "will not correct if the band drifts" in tip


def test_the_playhead_marks_where_the_room_is_on_the_form(overlay):
    now = {"value": 0.0}
    workbench = _clocked(lambda: now["value"])
    workbench.clock.start()
    now["value"] = 10.0

    overlay.set_song_state(
        form_rows=workbench.form_overlay(), clock=workbench.clock_snapshot()
    )
    lines = overlay._form_rows.text().splitlines()

    assert lines[0].startswith("  Intro")
    assert lines[1].startswith("▸ Verse")


def test_starting_the_clock_is_one_click(overlay):
    seen: list[bool] = []
    overlay.clock_toggled.connect(lambda: seen.append(True))
    overlay._clock_button.click()
    assert seen == [True]


def test_locating_asks_for_the_chosen_part(overlay):
    overlay.set_sections(("Intro", "Verse"))
    overlay._section_picker.setCurrentIndex(2)
    seen: list[str] = []
    overlay.section_located.connect(seen.append)
    overlay._locate_button.click()

    assert seen == ["Verse"]


# ----------------------------------------------------------------------
# Stems beside the jam
# ----------------------------------------------------------------------
def _bench_workbench():
    workbench = SongWorkbench(notes=SHEET)
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j20",
            source_name="demo_mix.wav",
            artifacts=(
                SongArtifact("vocals", "audio", local_path="/tmp/v.wav"),
                SongArtifact("drums", "audio", local_path="/tmp/d.wav"),
                SongArtifact("bass", "audio", local_path="/tmp/b.wav"),
            ),
        )
    )
    return workbench


def test_stems_render_as_one_row_each_with_mute_and_solo(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    overlay.set_stems(stems=bench.stems, mix=bench.mix())

    assert len(overlay._stem_rows) == 3
    buttons = overlay._stem_rows[0].findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["M", "S"]
    assert all(button.isCheckable() for button in buttons)


def test_the_stem_page_says_what_you_would_hear(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    bench.sing_this_one()
    overlay.set_stems(stems=bench.stems, mix=bench.mix())

    assert overlay._stem_status.text() == (
        "Playing Drums, Bass · without Vocals"
    )


def test_muting_a_stem_asks_the_coordinator_rather_than_deciding(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    overlay.set_stems(stems=bench.stems, mix=bench.mix())

    seen: list[str] = []
    overlay.stem_mute_toggled.connect(seen.append)
    overlay._stem_rows[0].findChildren(QPushButton)[0].click()

    assert seen == ["vocals"]


def test_soloing_a_stem_is_a_separate_signal(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    overlay.set_stems(stems=bench.stems, mix=bench.mix())

    seen: list[str] = []
    overlay.stem_solo_toggled.connect(seen.append)
    overlay._stem_rows[1].findChildren(QPushButton)[1].click()

    assert seen == ["drums"]


def test_sing_this_one_is_offered_once_stems_exist(overlay):
    overlay.set_stems(stems=(), mix=None)
    assert not overlay._sing_button.isEnabled()
    assert "Run Split stems" in overlay._stem_status.text()

    workbench = _bench_workbench()
    overlay.set_stems(
        stems=workbench.stem_bench.stems, mix=workbench.stem_bench.mix()
    )
    assert overlay._sing_button.isEnabled()
    assert "sings it" in overlay._sing_button.toolTip()


def test_sending_stems_to_the_jam_is_gated(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    overlay.set_stems(stems=bench.stems, mix=bench.mix(), can_send=False)
    assert not overlay._send_stems_button.isEnabled()

    overlay.set_stems(stems=bench.stems, mix=bench.mix(), can_send=True)
    assert overlay._send_stems_button.isEnabled()


def test_the_reason_a_send_is_unavailable_is_shown(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    _path, note = bench.shared_track_plan()
    overlay.set_stems(stems=bench.stems, mix=bench.mix(), note=note)

    assert overlay._stem_note.text() == note
    assert not overlay._stem_note.isHidden()


def test_the_stems_page_keeps_to_two_worded_actions(overlay):
    workbench = _bench_workbench()
    bench = workbench.stem_bench
    overlay.set_stems(stems=bench.stems, mix=bench.mix())

    page = overlay._stack.widget(1)
    worded = [
        button
        for button in page.findChildren(QPushButton)
        if len(button.text()) > 2
    ]
    assert sorted(button.text() for button in worded) == [
        "Send to jam",
        "Sing this one",
    ]
