"""The design lock: a Music session works on a computer with no keys at all.

Jamulus carries the band, the Shared Track carries the song, the conductor and
the form carry the shape, a meeting sits beside it, and the mix has faders.
None of that is allowed to depend on a Music AI key, a model key, or a working
credential store. These tests run with the store disabled and every provider
environment variable removed (see ``tests/conftest.py``), which is exactly the
state a musician who never opens Settings is in.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key_or_default
from core.meeting_companion import build_invite_message, describe_mutes
from core.provider_credentials import ProviderCredentials
from core.song_workbench import SharedTrackView, SongWorkbench
from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator
from webjam_qt.widgets.song_overlay import PAGE_SONG, PAGE_TOOLS, SongOverlay

REPO_ROOT = Path(__file__).resolve().parent.parent

SHEET = """Key: G major
Tempo: 96
Time: 4/4
[Intro x4]
G D
[Verse x8]
G D Em C
Waiting on the last train home
[Chorus x8]
C G D D
"""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _workbench(monotonic=None) -> SongWorkbench:
    return SongWorkbench(
        title="Tuesday Jam", notes=SHEET, monotonic=monotonic or (lambda: 0.0)
    )


def _coordinator(app) -> SongToolsCoordinator:
    overlay = SongOverlay()
    window = SimpleNamespace(
        song_overlay=overlay,
        session_canvas=SimpleNamespace(
            current_notes=lambda: SHEET, set_notes=MagicMock()
        ),
        session_strip=SimpleNamespace(
            current_title=lambda: "Tuesday Jam",
            _elapsed_seconds=0,
            _shared_track_last_snapshot=None,
            set_song_line=MagicMock(),
        ),
        flash_message=MagicMock(),
    )
    controller = SimpleNamespace(
        window=window,
        settings=SimpleNamespace(
            music_ai_api_key="", webex_url="", takes_directory=""
        ),
        creator_profile=get_creator_profile_by_key_or_default("music"),
        jamulus=SimpleNamespace(send_chat=MagicMock(return_value=True)),
        _reference_track_is_host=lambda: True,
        _reference_track_load_pending=None,
        _snapshot_participants=lambda: [],
        _ui_invoker=None,
        _open_settings_wizard=MagicMock(),
    )
    coordinator = SongToolsCoordinator(controller)
    coordinator.connect()
    return coordinator


# ----------------------------------------------------------------------
# There are no keys
# ----------------------------------------------------------------------
def test_this_computer_has_no_keys_and_no_store():
    credentials = ProviderCredentials()

    assert credentials.configured_ids() == ()
    assert credentials.store.usable() is False


# ----------------------------------------------------------------------
# The song, the clock, the form
# ----------------------------------------------------------------------
def test_the_song_reads_off_the_notes_with_no_key():
    form = _workbench().form

    assert form.key.value == "G major"
    assert form.tempo.value == "96"
    assert [section.label for section in form.sections] == [
        "Intro",
        "Verse",
        "Chorus",
    ]


def test_the_shared_clock_runs_with_no_key():
    ticks = iter([0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    workbench = _workbench(monotonic=lambda: next(ticks))

    assert workbench.clock.start() is True
    snapshot = workbench.clock_snapshot()

    assert snapshot.running
    assert snapshot.section_label
    assert snapshot.bar >= 1


def test_the_form_overlay_and_conductor_line_render_with_no_key():
    workbench = _workbench()

    rows = workbench.form_overlay()

    assert [row.label for row in rows] == ["Intro", "Verse", "Chorus"]
    assert rows[1].chords == "G D Em C"
    assert "G major" in workbench.conductor_line()


def test_writing_help_and_chord_help_work_with_no_key():
    """This is the part a musician actually uses. It is theory, not a model."""

    workbench = _workbench()

    advice = workbench.writing_advice()
    chords = workbench.chord_advice(section_name="Chorus")
    following = workbench.next_chord_advice(section_name="Verse")

    assert advice.available
    assert chords.available
    assert chords.suggestions[0].chords
    assert following.available
    assert "Suggestions for" in chords.headline()


def test_the_catch_up_and_the_shareable_sheet_work_with_no_key():
    workbench = _workbench()

    catch_up = workbench.catch_up(
        shared_track=SharedTrackView(
            loaded=True, playing=True, source_name="demo", position_s=42.0
        ),
        elapsed_seconds=900,
        is_host=False,
    )

    assert catch_up.joined_late
    assert catch_up.has_content
    assert "Key G major" in workbench.shareable_sheet()


def test_the_two_mutes_and_the_one_invite_work_with_no_key():
    mutes = describe_mutes(
        webjam_muted_participants=1,
        participant_count=3,
        meeting_configured=True,
        meeting_service="Webex",
    )
    invite = build_invite_message(
        join_link="webjam://join/abc",
        session_name="Tuesday Jam",
        meeting_url="https://example.webex.com/meet/jeff",
        song_line="Key G major · 96 BPM",
    )

    assert len(mutes.controls) == 2
    assert "webjam://join/abc" in invite.text
    assert "Key G major" in invite.text


def test_the_studio_section_bridge_works_with_no_key():
    from core.song_sections import section_markers_from_form

    markers = section_markers_from_form(
        _workbench().form.sections, sample_rate=48000, tempo_bpm=96.0
    )

    assert [marker.label for marker in markers] == ["Intro", "Verse", "Chorus"]


# ----------------------------------------------------------------------
# The panel, with no keys
# ----------------------------------------------------------------------
def test_the_song_panel_opens_and_shows_the_song_with_no_key(app):
    coordinator = _coordinator(app)

    with patch.object(coordinator, "discover_workflows") as discover:
        coordinator.toggle_panel()

    overlay = coordinator.overlay
    assert overlay.isVisible()
    assert overlay.current_page() == PAGE_SONG
    assert "G major" in overlay._form_summary.text()
    assert "Verse: G D Em C" in overlay._form_rows.text()
    # Nothing was discovered, because there is nothing to discover it with.
    discover.assert_not_called()


def test_the_tools_page_says_what_is_missing_without_blocking_anything(app):
    coordinator = _coordinator(app)
    coordinator.toggle_panel()
    coordinator.overlay.show_page(PAGE_TOOLS)

    status = coordinator.overlay._tools_status.text()

    assert "Music AI key" in status
    assert coordinator.overlay._tool_buttons == []
    assert coordinator.overlay._key_button.isHidden() is False


def test_the_model_row_is_absent_and_one_line_stands_in_for_it(app):
    coordinator = _coordinator(app)
    coordinator.toggle_panel()

    overlay = coordinator.overlay
    assert overlay._model_button.isHidden()
    assert overlay._model_note.isHidden() is False
    assert "Settings" in overlay._model_note.text()
    assert overlay._write_button.isEnabled()


def test_opening_the_panel_contacts_nobody(app):
    """No key means no client, and no key also means no attempt to make one."""

    coordinator = _coordinator(app)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as music_ai, patch(
        "webjam_qt.controllers.song_tools_coordinator.ask_for_section"
    ) as model, patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread"
    ) as thread:
        coordinator.toggle_panel()
        coordinator.show_writing_help()
        coordinator.show_chords("Chorus")

    music_ai.assert_not_called()
    model.assert_not_called()
    thread.assert_not_called()
    assert coordinator.overlay._suggestion_rows


def test_running_a_song_tool_with_no_key_refuses_before_a_file_picker(app):
    coordinator = _coordinator(app)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.run_song_tool("stems")

    picker.assert_not_called()
    client.assert_not_called()


# ----------------------------------------------------------------------
# The jam's own modules do not know models exist
# ----------------------------------------------------------------------
KEYLESS_MODULES = (
    "core/song_form.py",
    "core/song_help.py",
    "core/song_clock.py",
    "core/song_sections.py",
    "core/stem_bench.py",
    "core/song_workbench.py",
    "core/meeting_companion.py",
    "core/music_companion.py",
)


@pytest.mark.parametrize("relative", KEYLESS_MODULES)
def test_no_jam_module_imports_a_credential_or_a_model(relative):
    tree = ast.parse((REPO_ROOT / relative).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    for forbidden in (
        "core.provider_credentials",
        "core.secret_store",
        "core.text_model_client",
        "core.song_model_help",
    ):
        assert forbidden not in imported, f"{relative}: {forbidden}"


def test_the_session_controller_never_reads_a_key():
    """Starting, joining, or ending a jam must not consult a credential."""

    source = (
        REPO_ROOT / "webjam_qt" / "controllers" / "application_controller.py"
    ).read_text()
    for forbidden in (
        "provider_credentials",
        "ProviderCredentials",
        "text_model_client",
        "song_model_help",
        "music_ai_api_key",
    ):
        assert forbidden not in source, forbidden
