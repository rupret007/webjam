"""The controller glue: nothing uploads without a chosen file and a host yes.

These drive the coordinator against a fake controller and a fake transport, so
the whole path from button press to attached result is covered without a
socket, a real window, or a real session.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.creative_modes import get_creator_profile_by_key_or_default
from core.music_ai_catalog import failed_catalog, resolve_song_tools
from core.music_ai_client import API_BASE_URL, MusicAIResponse, MusicAIWorkflow
from core.music_ai_results import SongArtifact, SongToolRun
from core.song_workbench import JobBudget, SongWorkbench
from core.stem_bench import StemBenchError
from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.widgets.song_overlay import SongOverlay

SHEET = "Key: A minor\nTempo: 104\n[Verse]\nAm F C G\nDriving through town\n"
ACCOUNT = [MusicAIWorkflow("1", "Stem Separation", "my-stems", "isolate vocals")]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeTransport:
    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def request(self, method, url, *, headers, body=None, timeout=30.0):
        self.calls.append((method.upper(), url))
        for (route_method, route_url), response in self.routes.items():
            if route_method == method.upper() and url.startswith(route_url):
                return response
        return MusicAIResponse(404, b"{}")


def _controller(app, *, is_host=True, api_key="k", webex_url="", notes=SHEET):
    overlay = SongOverlay()
    window = SimpleNamespace(
        song_overlay=overlay,
        session_canvas=SimpleNamespace(
            current_notes=lambda: notes, set_notes=MagicMock()
        ),
        session_strip=SimpleNamespace(
            current_title=lambda: "Tuesday Jam",
            _elapsed_seconds=1320,
            _shared_track_last_snapshot=None,
            set_song_line=MagicMock(),
        ),
        flash_message=MagicMock(),
    )
    return SimpleNamespace(
        window=window,
        settings=SimpleNamespace(
            music_ai_api_key=api_key,
            webex_url=webex_url,
            takes_directory="",
        ),
        creator_profile=get_creator_profile_by_key_or_default("music"),
        jamulus=SimpleNamespace(send_chat=MagicMock(return_value=True)),
        _reference_track_is_host=lambda: is_host,
        _reference_track_load_pending=None,
        _last_shared_track_snapshot=None,
        _snapshot_participants=lambda: [],
        _ui_invoker=None,
        _open_settings_wizard=MagicMock(),
        _focus_webex_mute=MagicMock(),
    )


def _coordinator(app, **kwargs) -> SongToolsCoordinator:
    controller = _controller(app, **kwargs)
    coordinator = SongToolsCoordinator(controller)
    coordinator.connect()
    return coordinator


def _flashes(coordinator) -> list[str]:
    return [
        call.args[0]
        for call in coordinator._c.window.flash_message.call_args_list
    ]


# ----------------------------------------------------------------------
# Availability
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("profile_key", "expected"),
    [("music", True), ("podcast_voice", False), ("review_rehearsal", False)],
)
def test_song_tools_exist_only_in_a_music_session(app, profile_key, expected):
    coordinator = _coordinator(app)
    coordinator._c.creator_profile = get_creator_profile_by_key_or_default(
        profile_key
    )
    assert coordinator.is_available() is expected


def test_the_panel_only_opens_when_asked_and_toggles_shut(app):
    coordinator = _coordinator(app)
    assert not coordinator.overlay.isVisible()

    with patch.object(coordinator, "discover_workflows"):
        coordinator.toggle_panel()
        assert coordinator.overlay.isVisible()
        coordinator.toggle_panel()
        assert not coordinator.overlay.isVisible()


def test_rendering_is_skipped_while_the_panel_is_closed(app):
    """Background work must not repaint a panel nobody opened."""

    coordinator = _coordinator(app)
    coordinator.refresh()
    assert coordinator.overlay._form_summary.text() == (
        "No key, tempo, or sections captured yet."
    )


# ----------------------------------------------------------------------
# Local help
# ----------------------------------------------------------------------
def test_writing_help_reads_the_live_notes_without_any_network(app):
    coordinator = _coordinator(app)
    coordinator.show_writing_help()

    text = coordinator.overlay._advice.text()
    assert "Chorus" in text
    assert coordinator.workbench.form.key.value == "A minor"


def test_chord_help_answers_for_a_part_the_song_lacks(app):
    coordinator = _coordinator(app)
    coordinator.show_chords("")

    headline = coordinator.overlay._suggestion_headline.text()
    assert "Suggestions for" in headline
    assert "A minor" in headline


def test_the_song_sheet_goes_to_band_chat_not_back_into_the_notes(app):
    """The notes are where the sheet came from; echoing it would double it."""

    coordinator = _coordinator(app)
    coordinator.share_sheet_to_chat()

    coordinator._c.jamulus.send_chat.assert_called_once()
    sheet = coordinator._c.jamulus.send_chat.call_args.args[0]
    assert "Key A minor" in sheet
    assert "Verse: Am F C G" in sheet
    assert "Song sheet posted to band chat." in _flashes(coordinator)


def test_an_empty_session_has_no_sheet_to_share(app):
    coordinator = _coordinator(app, notes="")
    coordinator.share_sheet_to_chat()

    coordinator._c.jamulus.send_chat.assert_not_called()
    assert any("Write a key" in message for message in _flashes(coordinator))


def test_a_rejected_chat_message_is_reported_honestly(app):
    coordinator = _coordinator(app)
    coordinator._c.jamulus.send_chat = MagicMock(return_value=False)
    coordinator.share_sheet_to_chat()

    assert any("could not post" in message for message in _flashes(coordinator))


# ----------------------------------------------------------------------
# No silent upload
# ----------------------------------------------------------------------
def test_cancelling_the_file_picker_uploads_nothing(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=("", ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.run_song_tool("stems")

    confirm.assert_not_called()
    client.assert_not_called()


def test_declining_the_confirmation_uploads_nothing(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.run_song_tool("stems")

    client.assert_not_called()


def test_a_guest_is_refused_before_any_confirmation_is_shown(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app, is_host=False)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.run_song_tool("stems")

    confirm.assert_not_called()
    client.assert_not_called()
    assert any("Only the host" in message for message in _flashes(coordinator))


def test_without_an_api_key_nothing_is_attempted(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app, api_key="")
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.run_song_tool("stems")

    client.assert_not_called()
    assert any("music.ai/dash" in message for message in _flashes(coordinator))


def test_a_verb_the_account_cannot_run_is_refused(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.run_song_tool("sections")

    picker.assert_not_called()
    client.assert_not_called()
    assert any("section" in message.lower() for message in _flashes(coordinator))


def test_discovery_is_not_attempted_without_a_key(app):
    coordinator = _coordinator(app, api_key="")
    with patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        coordinator.discover_workflows()
    client.assert_not_called()


# ----------------------------------------------------------------------
# A full run
# ----------------------------------------------------------------------
def test_a_confirmed_run_attaches_its_result_to_the_session(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._c.settings.takes_directory = str(tmp_path / "takes")

    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): MusicAIResponse(
                200,
                json.dumps(
                    {
                        "uploadUrl": "https://storage.googleapis.com/upload/a",
                        "downloadUrl": "https://storage.googleapis.com/download/a",
                    }
                ).encode(),
            ),
            ("PUT", "https://storage.googleapis.com/upload/a"): MusicAIResponse(
                200, b""
            ),
            ("POST", f"{API_BASE_URL}/job"): MusicAIResponse(
                200, json.dumps({"id": "job-1"}).encode()
            ),
            ("GET", f"{API_BASE_URL}/job/job-1"): MusicAIResponse(
                200,
                json.dumps(
                    {
                        "id": "job-1",
                        "status": "SUCCEEDED",
                        "result": {
                            "vocals": "https://cdn.music.ai/a/vocals.wav",
                            "drums": "https://cdn.music.ai/a/drums.wav",
                        },
                    }
                ).encode(),
            ),
            ("GET", "https://cdn.music.ai/a/"): MusicAIResponse(200, b"RIFFaudio"),
        }
    )

    def build_client(api_key, **_kwargs):
        from core.music_ai_client import MusicAIClient

        return MusicAIClient(api_key, transport=transport, sleep=lambda _s: None)

    started: list = []

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            started.append(self._target)
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=build_client,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread",
        InlineThread,
    ):
        coordinator.run_song_tool("stems")

    assert started, "the job must run off the UI thread"
    assert len(coordinator.workbench.runs) == 1
    run = coordinator.workbench.runs[0]
    assert run.verb_key == "stems"
    assert len(run.audio_artifacts) == 2
    assert coordinator.workbench.stems()
    assert (tmp_path / "takes" / "WebJam Song Tools").is_dir()
    assert coordinator._running_verb == ""


def test_a_failed_job_reports_the_reason_and_leaves_the_session_usable(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): MusicAIResponse(
                200,
                json.dumps(
                    {
                        "uploadUrl": "https://storage.googleapis.com/upload/a",
                        "downloadUrl": "https://storage.googleapis.com/download/a",
                    }
                ).encode(),
            ),
            ("PUT", "https://storage.googleapis.com/upload/a"): MusicAIResponse(
                200, b""
            ),
            ("POST", f"{API_BASE_URL}/job"): MusicAIResponse(
                200, json.dumps({"id": "job-2"}).encode()
            ),
            ("GET", f"{API_BASE_URL}/job/job-2"): MusicAIResponse(
                200,
                json.dumps(
                    {
                        "id": "job-2",
                        "status": "FAILED",
                        "error": {
                            "code": "BAD_INPUT",
                            "title": "Invalid input",
                            "message": "File is corrupted.",
                        },
                    }
                ).encode(),
            ),
        }
    )

    def build_client(api_key, **_kwargs):
        from core.music_ai_client import MusicAIClient

        return MusicAIClient(api_key, transport=transport, sleep=lambda _s: None)

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=build_client,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread",
        InlineThread,
    ):
        coordinator.run_song_tool("stems")

    assert coordinator.workbench.runs == ()
    assert coordinator._running_verb == ""
    assert any("Invalid input" in message for message in _flashes(coordinator))


def test_only_one_tool_runs_at_a_time(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._running_verb = "stems"

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker:
        coordinator.run_song_tool("stems")

    picker.assert_not_called()
    assert any("already running" in message for message in _flashes(coordinator))


def test_discovery_failure_leaves_an_honest_catalog(app):
    coordinator = _coordinator(app)

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=RuntimeError("boom"),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.discover_workflows()

    assert coordinator._catalog is not None
    assert not coordinator._catalog.usable
    assert not coordinator._discovering


def test_a_failed_catalog_offers_no_buttons_when_rendered(app):
    coordinator = _coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator._apply_catalog(failed_catalog("Music AI rejected this API key."))

    assert coordinator.overlay._tool_buttons == []


# ----------------------------------------------------------------------
# Meeting actions
# ----------------------------------------------------------------------
def test_the_panel_never_duplicates_the_huds_primary_action(app):
    """ADR 0002: Copy Invite is a HUD primary action; the panel explains only."""

    coordinator = _coordinator(app, webex_url="https://band.webex.com/meet/jeff")
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    assert not hasattr(coordinator.overlay, "_invite_button")
    assert not hasattr(coordinator, "_copy_invite")
    assert (
        "Copy Invite on the session bar"
        in coordinator.overlay._end_note.text()
    )


def test_the_panel_points_at_conversation_for_the_meeting_mute(app):
    """The meeting mute handoff already has an owner; do not add a second."""

    coordinator = _coordinator(app, webex_url="https://band.webex.com/meet/jeff")
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    assert not hasattr(coordinator.overlay, "_mute_button")
    assert not hasattr(coordinator, "_open_meeting_mute")
    assert "from Conversation" in coordinator.overlay._meeting_owner.text()


def test_the_key_action_opens_settings(app):
    coordinator = _coordinator(app)
    coordinator._open_settings()
    coordinator._c._open_settings_wizard.assert_called_once()


# ----------------------------------------------------------------------
# Section-scoped help
# ----------------------------------------------------------------------
def test_chord_help_for_a_named_part_also_offers_what_comes_next(app):
    coordinator = _coordinator(app)
    coordinator.show_chords("Verse")

    assert (
        "Verse already plays Am F C G. Instead:"
        in coordinator.overlay._suggestion_headline.text()
    )
    assert "After Am F C G in Verse" in coordinator.overlay._advice.text()


def test_chord_help_with_no_selection_answers_the_next_missing_part(app):
    coordinator = _coordinator(app)
    coordinator.show_chords("")

    assert "Suggestions for Chorus" in coordinator.overlay._suggestion_headline.text()
    # Nothing exists to continue from, so no next-chord block is offered.
    assert "After " not in coordinator.overlay._advice.text()


def test_the_picker_is_filled_from_the_live_notes(app):
    coordinator = _coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    labels = [
        coordinator.overlay._section_picker.itemText(index)
        for index in range(coordinator.overlay._section_picker.count())
    ]
    assert labels == ["Next part", "Verse"]


def test_the_form_overlay_reaches_the_panel_on_refresh(app):
    coordinator = _coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    assert "Verse: Am F C G" in coordinator.overlay._form_rows.text()


# ----------------------------------------------------------------------
# The shared clock
# ----------------------------------------------------------------------
CLOCK_NOTES = "Key: G major\nTempo: 120\n[Intro x4]\nG D\n[Verse x8]\nG D Em C\n"


def _clock_coordinator(app):
    now = {"value": 0.0}
    coordinator = _coordinator(app, notes=CLOCK_NOTES)
    coordinator.workbench = SongWorkbench(
        title="Tuesday", notes=CLOCK_NOTES, monotonic=lambda: now["value"]
    )
    return coordinator, now


def test_starting_and_stopping_the_clock_is_one_control(app):
    coordinator, now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)

    coordinator.toggle_clock()
    assert coordinator.workbench.clock_snapshot().running

    now["value"] = 10.0
    coordinator._on_tick()  # what the repaint timer does in a live session
    assert coordinator.overlay._clock_line.text() == "Verse · bar 2 of 8"

    coordinator.toggle_clock()
    assert not coordinator.workbench.clock_snapshot().running


def test_starting_the_clock_says_it_does_not_follow_the_band(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.toggle_clock()

    assert any(
        "does not follow the band" in message for message in _flashes(coordinator)
    )


def test_a_song_with_no_tempo_is_told_what_the_clock_needs(app):
    coordinator = _coordinator(app, notes="[Verse]\nG D\n")
    coordinator.toggle_clock()

    assert not coordinator.workbench.clock_snapshot().running
    assert any("Write a tempo" in message for message in _flashes(coordinator))


def test_the_clock_can_be_moved_to_the_chosen_part(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.locate_section("Verse")

    assert coordinator.workbench.clock_snapshot().section_label == "Verse"


def test_locating_a_part_that_is_not_there_says_so(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.locate_section("Bridge")

    assert any("not in this song's form" in m for m in _flashes(coordinator))


def test_the_clock_belongs_to_the_room_not_the_panel(app):
    """Closing the panel must not stop the count the band is following."""

    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.toggle_clock()

    coordinator.close_panel()
    coordinator._on_panel_closed()

    assert coordinator.workbench.clock_snapshot().running


def test_a_repaint_tick_never_shows_the_panel_on_its_own(app):
    coordinator, now = _clock_coordinator(app)
    coordinator.toggle_clock()
    now["value"] = 4.0

    coordinator._on_tick()

    assert not coordinator.overlay.isVisible()


# ----------------------------------------------------------------------
# Stems beside the jam
# ----------------------------------------------------------------------
def _with_stems(app, tmp_path, *, is_host=True):
    import numpy
    import soundfile

    coordinator = _coordinator(app, is_host=is_host)
    paths = {}
    for name in ("vocals", "drums", "bass"):
        path = tmp_path / f"{name}.wav"
        soundfile.write(
            str(path), numpy.zeros((4800, 2), dtype="float32"), 48000
        )
        paths[name] = str(path)
    coordinator.workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="my-stems",
            job_id="j30",
            source_name="demo_mix.wav",
            artifacts=tuple(
                SongArtifact(name, "audio", local_path=path)
                for name, path in paths.items()
            ),
        )
    )
    coordinator._c.settings.takes_directory = str(tmp_path / "takes")
    return coordinator, paths


def test_a_finished_separation_arrives_as_faders(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path)
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    assert len(coordinator.overlay._stem_rows) == 3
    assert "All stems" in coordinator.overlay._stem_status.text()


def test_muting_a_stem_updates_what_the_room_would_hear(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path)
    coordinator.toggle_stem_mute("vocals")

    assert coordinator.workbench.stem_bench.stem("vocals").muted
    assert "without Vocals" in coordinator.overlay._stem_status.text()


def test_soloing_uses_the_same_rule_as_the_participant_grid(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path)
    coordinator.toggle_stem_solo("drums")

    mix = coordinator.workbench.stem_bench.mix()
    assert [stem.name for stem in mix.audible] == ["drums"]


def test_sing_this_one_mutes_the_vocal_and_says_so(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path)
    coordinator.sing_this_one()

    assert coordinator.workbench.stem_bench.stem("vocals").muted
    assert any("Sing it" in message for message in _flashes(coordinator))


def test_sing_this_one_without_a_vocal_stem_is_honest(app):
    coordinator = _coordinator(app)
    coordinator.workbench.stem_bench.load([("drums", "/tmp/d.wav")])
    coordinator.sing_this_one()

    assert any("no separate vocal" in message for message in _flashes(coordinator))


def test_one_audible_stem_goes_straight_into_the_jam(app, tmp_path):
    coordinator, paths = _with_stems(app, tmp_path)
    loaded: list[str] = []
    coordinator._c._load_reference_track = loaded.append

    coordinator.toggle_stem_solo("drums")
    coordinator.send_stems_to_jam()

    assert loaded == [paths["drums"]]
    assert any("host control" in message for message in _flashes(coordinator))


def test_several_audible_stems_are_mixed_before_they_reach_the_jam(app, tmp_path):
    import soundfile

    coordinator, _paths = _with_stems(app, tmp_path)
    loaded: list[str] = []
    coordinator._c._load_reference_track = loaded.append

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    coordinator.sing_this_one()
    with patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.send_stems_to_jam()

    assert len(loaded) == 1
    assert soundfile.info(loaded[0]).samplerate == 48000
    assert "WebJam Song Tools" in loaded[0]


def test_a_guest_cannot_send_stems_into_the_jam(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path, is_host=False)
    loaded: list[str] = []
    coordinator._c._load_reference_track = loaded.append

    coordinator.toggle_stem_solo("drums")
    coordinator.send_stems_to_jam()

    assert loaded == []
    assert any("Only the host" in message for message in _flashes(coordinator))


def test_everything_muted_reports_the_reason_and_sends_nothing(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path)
    loaded: list[str] = []
    coordinator._c._load_reference_track = loaded.append

    for name in ("vocals", "drums", "bass"):
        coordinator.toggle_stem_mute(name)
    coordinator.send_stems_to_jam()

    assert loaded == []
    assert any("Every stem is muted" in message for message in _flashes(coordinator))


def test_a_failed_mix_reports_the_reason_and_leaves_the_jam_alone(app, tmp_path):
    coordinator, _paths = _with_stems(app, tmp_path)
    loaded: list[str] = []
    coordinator._c._load_reference_track = loaded.append

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    coordinator.sing_this_one()
    with patch(
        "webjam_qt.controllers.song_tools_coordinator.bounce_stems",
        side_effect=StemBenchError("These stems do not share a sample rate."),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.send_stems_to_jam()

    assert loaded == []
    assert any("sample rate" in message for message in _flashes(coordinator))


# ----------------------------------------------------------------------
# Shared Track owns the clock while it holds a song
# ----------------------------------------------------------------------
class _TrackSnapshot(SimpleNamespace):
    pass


def _with_shared_track(coordinator, *, name="demo.wav", position=20.0, state="playing"):
    coordinator._c.window.session_strip._shared_track_last_snapshot = _TrackSnapshot(
        state=state, source_name=name, position_s=position, duration_s=180.0
    )


def test_the_clock_follows_the_shared_track_when_one_is_loaded(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    _with_shared_track(coordinator)

    coordinator.refresh()
    snapshot = coordinator.workbench.clock_snapshot()

    assert snapshot.follows_shared_track
    assert snapshot.section_label == "Verse"
    assert "with Shared Track" in coordinator.overlay._clock_line.text()


def test_the_panel_will_not_start_a_competing_count(app):
    coordinator, _now = _clock_coordinator(app)
    _with_shared_track(coordinator)

    coordinator.toggle_clock()

    snapshot = coordinator.workbench.clock_snapshot()
    assert snapshot.follows_shared_track
    assert snapshot.position_s == 20.0  # untouched by the panel's button
    assert any(
        "Shared Track is the clock" in message for message in _flashes(coordinator)
    )


def test_the_transport_controls_defer_to_the_shared_track(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    _with_shared_track(coordinator)
    coordinator.refresh()

    assert not coordinator.overlay._clock_button.isEnabled()
    assert not coordinator.overlay._locate_button.isEnabled()


def test_a_guest_projection_drives_the_clock_too(app):
    """Guests read the host's published Shared Track position, so they agree."""

    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator._c.window.session_strip._shared_track_last_snapshot = SimpleNamespace(
        state=SimpleNamespace(value="playing"),
        source_display_name="demo.wav",
        position_s=34.0,
        duration_s=180.0,
    )
    coordinator.refresh()

    assert coordinator.workbench.clock_snapshot().follows_shared_track
    assert coordinator.workbench.clock_snapshot().section_label == "Verse"


def test_a_shared_track_without_a_form_still_marks_the_strip(app):
    """Painters and musicians can ride a track even when nobody wrote bars."""

    coordinator = _coordinator(app, notes="")
    _with_shared_track(coordinator, position=34.0)
    coordinator.refresh()

    snapshot = coordinator.workbench.clock_snapshot()
    assert snapshot.follows_shared_track
    assert not snapshot.has_form
    assert "0:34" in _song_line(coordinator)
    description = coordinator._c.window.session_strip.set_song_line.call_args.kwargs[
        "description"
    ]
    assert "Shared Track" in description


def test_removing_the_track_returns_the_count_to_the_panel(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    _with_shared_track(coordinator)
    coordinator.refresh()

    coordinator._c.window.session_strip._shared_track_last_snapshot = None
    coordinator.refresh()

    assert not coordinator.workbench.clock_snapshot().follows_shared_track
    assert coordinator.overlay._clock_button.isEnabled()


# ----------------------------------------------------------------------
# Suggestions: labelled, one tap to keep, one to dismiss, never auto-written
# ----------------------------------------------------------------------
def test_a_suggestion_writes_nothing_until_it_is_kept(app):
    coordinator = _coordinator(app)
    written: list[str] = []
    coordinator._c.window.session_canvas.set_notes = written.append

    coordinator.show_chords("Verse")

    assert written == []
    assert coordinator.overlay._suggestion_rows


def test_keeping_a_suggestion_writes_it_under_that_part(app):
    coordinator = _coordinator(app)
    written: list[str] = []
    coordinator._c.window.session_canvas.set_notes = written.append

    coordinator.keep_suggestion("Verse", "F G Am Am")

    assert len(written) == 1
    assert "F G Am Am" in written[0]
    lines = written[0].splitlines()
    assert lines[lines.index("[Verse]") + 1] == "F G Am Am"


def test_keeping_a_part_the_sheet_lacks_appends_it(app):
    coordinator = _coordinator(app)
    written: list[str] = []
    coordinator._c.window.session_canvas.set_notes = written.append

    coordinator.keep_suggestion("Bridge", "Dm Am Em Am")

    assert "[Bridge]" in written[0]
    assert written[0].rstrip().endswith("Dm Am Em Am")


def test_keeping_clears_the_suggestions_and_says_what_happened(app):
    coordinator = _coordinator(app)
    coordinator._c.window.session_canvas.set_notes = lambda _text: None
    coordinator.show_chords("Verse")

    coordinator.keep_suggestion("Verse", "F G Am Am")

    assert coordinator.overlay._suggestion_rows == []
    assert any("Kept F G Am Am under Verse" in m for m in _flashes(coordinator))


def test_keeping_nothing_changes_nothing(app):
    coordinator = _coordinator(app)
    written: list[str] = []
    coordinator._c.window.session_canvas.set_notes = written.append

    coordinator.keep_suggestion("Verse", "   ")

    assert written == []
    assert any("Nothing to keep" in m for m in _flashes(coordinator))


def test_dismissing_clears_the_panel_and_writes_nothing(app):
    coordinator = _coordinator(app)
    written: list[str] = []
    coordinator._c.window.session_canvas.set_notes = written.append
    coordinator.show_chords("Verse")

    coordinator.dismiss_suggestions()

    assert coordinator.overlay._suggestion_rows == []
    assert written == []


def test_the_selected_part_defaults_to_where_the_clock_is(app):
    coordinator, now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.workbench.clock.start()
    now["value"] = 10.0

    coordinator.refresh()

    assert coordinator.overlay.selected_section() == "Verse"


# ----------------------------------------------------------------------
# The quiet line on the strip
# ----------------------------------------------------------------------
def _song_line(coordinator) -> str:
    return coordinator._c.window.session_strip.set_song_line.call_args.args[0]


def test_a_running_clock_puts_the_part_on_the_strip(app):
    coordinator, now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.workbench.clock.start()
    now["value"] = 10.0

    coordinator._on_tick()

    assert "Verse · bar 2 of 8" in _song_line(coordinator)


def test_the_strip_line_survives_closing_the_panel(app):
    """Overlays you turned on stay on when you go back to the jam."""

    coordinator, now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.workbench.clock.start()
    now["value"] = 10.0
    coordinator._on_tick()

    coordinator.close_panel()
    coordinator._on_panel_closed()

    assert not coordinator.overlay.isVisible()
    assert "Verse" in _song_line(coordinator)


def test_a_job_in_flight_reads_as_one_quiet_word(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(
        [MusicAIWorkflow("1", "Chord detection", "chords", "detect chords")]
    )
    coordinator._running_verb = "chords"

    coordinator._render_song_line()

    assert _song_line(coordinator) == "Chords & key…"


def test_the_strip_stays_empty_in_a_quiet_room(app):
    coordinator = _coordinator(app)
    coordinator._render_song_line()
    assert _song_line(coordinator) == ""


def test_a_non_music_session_never_gets_a_song_line(app):
    coordinator = _coordinator(app)
    coordinator._c.creator_profile = get_creator_profile_by_key_or_default(
        "podcast_voice"
    )
    coordinator._render_song_line()
    assert _song_line(coordinator) == ""


# ----------------------------------------------------------------------
# One dialog, not two
# ----------------------------------------------------------------------
def test_a_loaded_shared_track_is_the_subject_without_an_extra_dialog(app, tmp_path):
    source = tmp_path / "backing.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._c._reference_track_load_pending = str(source)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ) as confirm:
        coordinator.run_song_tool("stems")

    picker.assert_not_called()
    # Exactly one dialog: the host confirmation, which names the file.
    assert confirm.call_count == 1
    assert "backing.wav" in confirm.call_args.args[2]


# ----------------------------------------------------------------------
# Webex coexistence: a second window that Song tools never touch
# ----------------------------------------------------------------------
def _meeting_controller(app, **kwargs):
    coordinator = _coordinator(app, **kwargs)
    embed = SimpleNamespace(
        set_launch_status=MagicMock(),
        set_app_status=MagicMock(),
        focus_primary_action=MagicMock(),
        setVisible=MagicMock(),
    )
    coordinator._c.window.webex_embed = embed
    coordinator._c._show_webex_app = MagicMock()
    coordinator._c._on_join_video = MagicMock()
    coordinator._c._focus_webex_mute = MagicMock()
    return coordinator, embed


def test_opening_song_tools_leaves_the_meeting_handoff_alone(app):
    coordinator, embed = _meeting_controller(app)

    with patch.object(coordinator, "discover_workflows"):
        coordinator.toggle_panel()

    embed.setVisible.assert_not_called()
    embed.focus_primary_action.assert_not_called()
    embed.set_launch_status.assert_not_called()
    coordinator._c._show_webex_app.assert_not_called()
    coordinator._c._on_join_video.assert_not_called()
    coordinator._c._focus_webex_mute.assert_not_called()


def test_write_help_leaves_the_meeting_handoff_alone(app):
    coordinator, embed = _meeting_controller(app)

    coordinator.show_writing_help()
    coordinator.show_chords("Verse")
    coordinator.keep_suggestion("Verse", "F G Am Am")

    embed.setVisible.assert_not_called()
    coordinator._c._show_webex_app.assert_not_called()
    coordinator._c._on_join_video.assert_not_called()


def test_a_running_job_leaves_the_meeting_handoff_alone(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator, embed = _meeting_controller(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=RuntimeError("offline"),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.run_song_tool("stems")

    embed.setVisible.assert_not_called()
    embed.set_launch_status.assert_not_called()
    coordinator._c._show_webex_app.assert_not_called()


def test_the_in_flight_line_is_a_label_not_a_control(app):
    """It reports; it never grows into a button that could cover Conversation."""

    from PySide6.QtWidgets import QLabel, QPushButton

    strip_widget = ConductorWindow(
        mode_entries=[("music", "Music")],
        initial_mode_key="music",
        initial_title="Tuesday",
    )
    try:
        strip = strip_widget.session_strip
        strip.set_song_line("Chords & key…")

        assert isinstance(strip._song_line, QLabel)
        assert not isinstance(strip._song_line, QPushButton)
        assert strip.current_song_line() == "Chords & key…"
        # Conversation and Studio remain exactly as reachable as before.
        assert strip._video_button.isEnabled()
        assert strip._tools_button.isEnabled()
    finally:
        strip_widget.deleteLater()


def test_the_missing_key_line_lives_in_the_panel_not_over_conversation(app):
    coordinator, embed = _meeting_controller(app, api_key="")
    coordinator.overlay.setVisible(True)

    coordinator.refresh()

    assert "MUSIC_AI_API_KEY" in coordinator.overlay._tools_status.text()
    embed.setVisible.assert_not_called()
    embed.set_launch_status.assert_not_called()


def test_the_panel_copy_follows_whichever_meeting_service_is_configured(app):
    coordinator, _embed = _meeting_controller(
        app, webex_url="https://zoom.us/j/123456"
    )
    coordinator.overlay.setVisible(True)

    coordinator.refresh()

    assert "Zoom mute" in coordinator.overlay._mute_lines.text()
    assert "Zoom" in coordinator.overlay._end_note.text()


# ----------------------------------------------------------------------
# The companion surface: publishes the song, accepts only requests
# ----------------------------------------------------------------------
def test_the_companion_snapshot_carries_the_live_song(app):
    coordinator, now = _clock_coordinator(app)
    coordinator.workbench.clock.start()
    now["value"] = 10.0

    snapshot = coordinator.companion_snapshot()

    assert snapshot.is_music_session
    assert snapshot.section == "Verse"
    assert snapshot.key == "G major"
    assert snapshot.bpm == 120.0
    assert snapshot.position_known


def test_a_non_music_session_publishes_an_empty_projection(app):
    coordinator = _coordinator(app)
    coordinator._c.creator_profile = get_creator_profile_by_key_or_default(
        "podcast_voice"
    )

    snapshot = coordinator.companion_snapshot()

    assert not snapshot.is_music_session
    assert snapshot.chord_overlay == ()


def test_the_projection_never_carries_a_path_or_a_key(app, tmp_path):
    import json

    source = tmp_path / "private_mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app, api_key="sk-live-secret-value")
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._c._reference_track_load_pending = str(source)

    published = json.dumps(coordinator.companion_snapshot().to_public_dict())

    assert "private_mix" not in published
    assert str(tmp_path) not in published
    assert "sk-live-secret-value" not in published
    assert "music.ai" not in published


def test_the_projection_shows_the_current_suggestion(app):
    coordinator = _coordinator(app)
    coordinator.show_chords("Verse")

    snapshot = coordinator.companion_snapshot()

    assert snapshot.suggestion is not None
    assert snapshot.suggestion.section == "Verse"
    assert snapshot.suggestion.chords


def test_dismissing_clears_what_the_companion_would_show(app):
    coordinator = _coordinator(app)
    coordinator.show_chords("Verse")
    coordinator.dismiss_suggestions()

    assert coordinator.companion_snapshot().suggestion is None


def test_a_companion_can_ask_for_write_help(app):
    coordinator = _coordinator(app)

    decision = coordinator.handle_companion_command({"command": "write_help"})

    assert decision.accepted
    assert coordinator.overlay._advice.text()


def test_a_companion_can_ask_for_chords_for_a_named_part(app):
    coordinator = _coordinator(app)

    decision = coordinator.handle_companion_command(
        {"command": "suggest_chords", "section": "Verse"}
    )

    assert decision.accepted
    assert "Verse already plays" in coordinator.overlay._suggestion_headline.text()


def test_a_companion_request_never_opens_a_file_picker(app, tmp_path):
    """A dialog nobody asked for, in front of someone looking elsewhere."""

    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        decision = coordinator.handle_companion_command(
            {"command": "run_song_tool", "verb": "stems"}
        )

    picker.assert_not_called()
    client.assert_not_called()
    assert not decision.accepted
    assert "Load a Shared Track on the desktop first" in decision.reason


def test_a_companion_tool_request_runs_on_the_track_the_host_chose(app, tmp_path):
    source = tmp_path / "backing.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._c._reference_track_load_pending = str(source)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ) as confirm:
        decision = coordinator.handle_companion_command(
            {"command": "run_song_tool", "verb": "stems"}
        )

    assert decision.accepted
    picker.assert_not_called()
    # The host still confirms on the desktop; the companion only asked.
    assert confirm.call_count == 1
    assert "backing.wav" in confirm.call_args.args[2]


def test_a_guest_companion_request_is_refused_before_any_dialog(app, tmp_path):
    source = tmp_path / "backing.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app, is_host=False)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._c._reference_track_load_pending = str(source)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm:
        decision = coordinator.handle_companion_command(
            {"command": "run_song_tool", "verb": "stems"}
        )

    confirm.assert_not_called()
    assert not decision.accepted
    assert "Only the host" in decision.reason


def test_a_companion_cannot_smuggle_a_path_into_a_request(app, tmp_path):
    victim = tmp_path / "not_mine.wav"
    victim.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        decision = coordinator.handle_companion_command(
            {
                "command": "run_song_tool",
                "verb": "stems",
                "path": str(victim),
                "inputUrl": "https://storage.googleapis.com/upload/x",
            }
        )

    confirm.assert_not_called()
    client.assert_not_called()
    assert not decision.accepted


def test_the_native_strip_still_shows_the_song_with_no_companion(app):
    """No add-on anywhere; the overlay renders where it always did."""

    coordinator, now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.workbench.clock.start()
    now["value"] = 10.0

    coordinator._on_tick()

    assert "Verse · bar 2 of 8" in _song_line(coordinator)
    assert coordinator.overlay._clock_line.text() == "Verse · bar 2 of 8"


# ----------------------------------------------------------------------
# Late join: overlays start where the room is, not at 0:00
# ----------------------------------------------------------------------
def test_a_late_joiner_sees_the_current_section_not_the_top(app):
    """Nobody pressed start here; the Shared Track is already 34 seconds in."""

    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    _with_shared_track(coordinator, position=34.0)

    coordinator.refresh()

    snapshot = coordinator.workbench.clock_snapshot()
    assert snapshot.section_label == "Verse"
    assert snapshot.bar_in_section > 1
    assert "Verse · bar" in coordinator.overlay._clock_line.text()


def test_a_playing_shared_track_starts_the_repaint_on_its_own(app):
    """Otherwise a guest's overlay would freeze at whatever it first drew."""

    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    assert coordinator._tick_timer is None

    _with_shared_track(coordinator, position=10.0)
    coordinator.refresh()

    assert coordinator._tick_timer is not None
    assert coordinator._tick_timer.isActive()


def test_a_paused_shared_track_does_not_keep_repainting(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    _with_shared_track(coordinator, position=10.0, state="paused")

    coordinator.refresh()
    coordinator._on_tick()

    assert coordinator._tick_timer is None or not coordinator._tick_timer.isActive()


def test_with_no_track_and_no_clock_nothing_claims_a_position(app):
    """A stopped clock sits at the top of the form but announces nothing."""

    coordinator = _coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    snapshot = coordinator.workbench.clock_snapshot()
    assert not snapshot.running
    assert not snapshot.follows_shared_track
    # Nothing reaches the jam surface, and no large chord is drawn.
    assert _song_line(coordinator) == ""
    assert coordinator.overlay._now_chord.isHidden()


# ----------------------------------------------------------------------
# End meeting is not end jam
# ----------------------------------------------------------------------
def test_a_job_finishing_after_someone_left_the_meeting_still_lands(app, tmp_path):
    """Music AI results are local files; a meeting has nothing to do with them."""

    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app, webex_url="")   # meeting gone or never set
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._c.settings.takes_directory = str(tmp_path / "takes")

    transport = FakeTransport(
        {
            ("GET", f"{API_BASE_URL}/upload"): MusicAIResponse(
                200,
                json.dumps(
                    {
                        "uploadUrl": "https://storage.googleapis.com/upload/a",
                        "downloadUrl": "https://storage.googleapis.com/download/a",
                    }
                ).encode(),
            ),
            ("PUT", "https://storage.googleapis.com/upload/a"): MusicAIResponse(
                200, b""
            ),
            ("POST", f"{API_BASE_URL}/job"): MusicAIResponse(
                200, json.dumps({"id": "job-9"}).encode()
            ),
            ("GET", f"{API_BASE_URL}/job/job-9"): MusicAIResponse(
                200,
                json.dumps(
                    {
                        "id": "job-9",
                        "status": "SUCCEEDED",
                        "result": {"vocals": "https://cdn.music.ai/a/vocals.wav"},
                    }
                ).encode(),
            ),
            ("GET", "https://cdn.music.ai/a/"): MusicAIResponse(200, b"RIFFaudio"),
        }
    )

    def build_client(api_key, **_kwargs):
        from core.music_ai_client import MusicAIClient

        return MusicAIClient(api_key, transport=transport, sleep=lambda _s: None)

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=build_client,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.run_song_tool("stems")

    assert len(coordinator.workbench.runs) == 1
    assert coordinator.workbench.stems()


# ----------------------------------------------------------------------
# Sleep and reconnect: report, never restart
# ----------------------------------------------------------------------
def test_a_job_wehjam_stopped_waiting_for_is_reported_not_restarted(app):
    from core.music_ai_client import MusicAIJobError

    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._running_verb = "stems"

    coordinator._finish_tool(
        None,
        str(MusicAIJobError("still working", code="TIMEOUT")),
        unfinished="Split stems",
    )

    assert coordinator._running_verb == ""
    assert coordinator._unfinished_job == "Split stems"
    assert "still at Music AI" in _song_line(coordinator)


def test_starting_a_new_job_clears_the_stale_note(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._unfinished_job = "Split stems"

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=RuntimeError("offline"),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread"
    ):
        coordinator.run_song_tool("stems")

    assert coordinator._unfinished_job == ""


# ----------------------------------------------------------------------
# One invite carries the song the room already chose
# ----------------------------------------------------------------------
def test_the_invite_song_line_names_what_a_joiner_is_joining(app):
    coordinator, _now = _clock_coordinator(app)

    line = coordinator.invite_song_line()

    assert "Key G major" in line
    assert "120 BPM" in line
    assert "Intro → Verse" in line
    assert "/" not in line


def test_a_session_with_no_song_adds_nothing_to_the_invite(app):
    coordinator = _coordinator(app, notes="")
    assert coordinator.invite_song_line() == ""


def test_a_non_music_session_adds_nothing_to_the_invite(app):
    coordinator = _coordinator(app)
    coordinator._c.creator_profile = get_creator_profile_by_key_or_default(
        "podcast_voice"
    )
    assert coordinator.invite_song_line() == ""


def test_a_guest_is_never_offered_a_file_picker(app):
    """Joiners get the room's binding, not a second picker."""

    coordinator = _coordinator(app, is_host=False)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker:
        coordinator.run_song_tool("stems")
        coordinator.handle_companion_command(
            {"command": "run_song_tool", "verb": "stems"}
        )

    picker.assert_not_called()


# ----------------------------------------------------------------------
# Fail closed: every refusal, proven to upload nothing and open nothing
# ----------------------------------------------------------------------
class _RefusalCase(SimpleNamespace):
    pass


REFUSALS = (
    _RefusalCase(
        name="no api key",
        setup=dict(api_key=""),
        catalog=ACCOUNT,
        verb="stems",
        fragment="MUSIC_AI_API_KEY",
    ),
    _RefusalCase(
        name="guest",
        setup=dict(is_host=False),
        catalog=ACCOUNT,
        verb="stems",
        fragment="Only the host",
    ),
    _RefusalCase(
        name="workflows never discovered",
        setup={},
        catalog=None,
        verb="stems",
        fragment="not available",
    ),
    _RefusalCase(
        name="account cannot run the verb",
        setup={},
        catalog=ACCOUNT,
        verb="sections",
        fragment="section",
    ),
    _RefusalCase(
        name="unknown verb",
        setup={},
        catalog=ACCOUNT,
        verb="teleport",
        fragment="not available",
    ),
    _RefusalCase(
        name="not a music session",
        setup={},
        catalog=ACCOUNT,
        verb="stems",
        profile="podcast_voice",
        fragment="part of a Music session",
    ),
)


@pytest.mark.parametrize("case", REFUSALS, ids=lambda case: case.name)
def test_every_refusal_uploads_nothing_and_opens_nothing(app, case):
    """The fail-closed matrix: no client, no picker, no confirmation, no job."""

    coordinator = _coordinator(app, **case.setup)
    coordinator._catalog = (
        resolve_song_tools(case.catalog) if case.catalog is not None else None
    )
    if getattr(case, "profile", ""):
        coordinator._c.creator_profile = get_creator_profile_by_key_or_default(
            case.profile
        )

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client, patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread"
    ) as thread:
        coordinator.run_song_tool(case.verb)

    picker.assert_not_called()
    confirm.assert_not_called()
    client.assert_not_called()
    thread.assert_not_called()
    assert coordinator.workbench.runs == ()
    assert coordinator._running_verb == ""
    if case.fragment:
        assert any(case.fragment in message for message in _flashes(coordinator))


@pytest.mark.parametrize("case", REFUSALS, ids=lambda case: case.name)
def test_every_refusal_holds_for_a_companion_request_too(app, case):
    """The same matrix, arriving from outside the desktop window."""

    coordinator = _coordinator(app, **case.setup)
    coordinator._catalog = (
        resolve_song_tools(case.catalog) if case.catalog is not None else None
    )
    if getattr(case, "profile", ""):
        coordinator._c.creator_profile = get_creator_profile_by_key_or_default(
            case.profile
        )

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm, patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client:
        decision = coordinator.handle_companion_command(
            {"command": "run_song_tool", "verb": case.verb}
        )

    assert not decision.accepted
    picker.assert_not_called()
    confirm.assert_not_called()
    client.assert_not_called()
    assert coordinator.workbench.runs == ()


def test_declining_the_confirmation_uploads_nothing_even_with_everything_ready(
    app, tmp_path
):
    """The last gate: everything is in order and the host still says no."""

    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName",
        return_value=(str(source), ""),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
    ) as client, patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread"
    ) as thread:
        coordinator.run_song_tool("stems")

    client.assert_not_called()
    thread.assert_not_called()
    assert coordinator.workbench.runs == ()


def test_a_refusal_before_the_picker_is_not_an_accident_of_wording(app):
    """The precondition gate is structural, not a string match on a reason."""

    import inspect

    from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator

    source = inspect.getsource(SongToolsCoordinator.run_song_tool)
    assert "evaluate_upload_preconditions" in source
    assert "startswith" not in source
    # The precondition check must come before any file is chosen.
    assert source.index("evaluate_upload_preconditions") < source.index(
        "_choose_source"
    )


# ----------------------------------------------------------------------
# Quota, the click, and a host who left
# ----------------------------------------------------------------------
def test_a_jam_cannot_fire_twenty_stem_jobs(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._budget = JobBudget(limit=2, window_s=3600.0)

    started = 0
    for _attempt in range(5):
        coordinator._running_verb = ""
        with patch(
            "webjam_qt.controllers.song_tools_coordinator.QFileDialog"
            ".getOpenFileName",
            return_value=(str(source), ""),
        ), patch(
            "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "webjam_qt.controllers.song_tools_coordinator.MusicAIClient"
        ), patch(
            "webjam_qt.controllers.song_tools_coordinator.threading.Thread"
        ) as thread:
            coordinator.run_song_tool("stems")
            started += thread.call_count

    assert started == 2
    assert any("Music AI jobs this hour" in m for m in _flashes(coordinator))


def test_the_limit_refuses_before_the_picker_opens(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator._budget = JobBudget(limit=1, window_s=3600.0)
    coordinator._budget.record(time.monotonic())

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker, patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm:
        coordinator.run_song_tool("stems")

    picker.assert_not_called()
    confirm.assert_not_called()


def test_song_tools_never_start_a_second_player(app):
    """Count-in, metronome, and Shared Track transport stay the clock."""

    import inspect

    from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator

    source = inspect.getsource(SongToolsCoordinator)
    # The only route into the room is the host-owned Shared Track loader.
    assert "_load_reference_track" in source
    for forbidden in (
        "QMediaPlayer",
        "QSoundEffect",
        "sounddevice",
        "_play_reference_track",
        "start_playback",
        "metronome",
    ):
        assert forbidden not in source, forbidden


def test_the_clock_holds_through_a_count_in(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator._c.window.session_strip._shared_track_last_snapshot = SimpleNamespace(
        state="playing",
        source_name="demo.wav",
        position_s=30.0,
        duration_s=180.0,
        count_in_active=True,
    )

    coordinator.refresh()

    snapshot = coordinator.workbench.clock_snapshot()
    assert snapshot.follows_shared_track
    assert not snapshot.running          # the click is not the song
    assert coordinator.overlay._now_chord.isHidden()


def test_the_clock_starts_when_the_count_in_ends(app):
    coordinator, _now = _clock_coordinator(app)
    coordinator.overlay.setVisible(True)
    strip = coordinator._c.window.session_strip
    strip._shared_track_last_snapshot = SimpleNamespace(
        state="playing",
        source_name="demo.wav",
        position_s=30.0,
        duration_s=180.0,
        count_in_active=True,
    )
    coordinator.refresh()

    strip._shared_track_last_snapshot = SimpleNamespace(
        state="playing",
        source_name="demo.wav",
        position_s=30.0,
        duration_s=180.0,
        count_in_active=False,
    )
    coordinator.refresh()

    assert coordinator.workbench.clock_snapshot().running


def test_a_host_who_left_keeps_the_overlays_and_defers_the_upload(app, tmp_path):
    source = tmp_path / "mix.wav"
    source.write_bytes(b"RIFF" + b"0" * 4096)
    coordinator = _coordinator(app, is_host=False)
    coordinator._catalog = resolve_song_tools(ACCOUNT)
    coordinator.overlay.setVisible(True)
    coordinator.refresh()

    # Local facts are still on screen.
    assert "Verse: Am F C G" in coordinator.overlay._form_rows.text()
    assert coordinator.workbench.conductor_line()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QFileDialog.getOpenFileName"
    ) as picker:
        coordinator.run_song_tool("stems")

    picker.assert_not_called()
    assert any("waits for a host" in m for m in _flashes(coordinator))


def test_a_host_who_left_does_not_pretend_a_confirmation_is_pending(app):
    coordinator = _coordinator(app, is_host=False)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.QMessageBox.question"
    ) as confirm:
        coordinator.run_song_tool("stems")

    confirm.assert_not_called()
    assert coordinator._running_verb == ""


# ----------------------------------------------------------------------
# A companion can tell that something changed
# ----------------------------------------------------------------------
def test_the_revision_moves_when_the_song_position_does(app):
    """A counter that only moved on suggestions would show bar one all night."""

    coordinator, now = _clock_coordinator(app)
    coordinator.workbench.clock.start()

    first = coordinator.companion_snapshot()
    now["value"] = 10.0
    second = coordinator.companion_snapshot()

    assert second.revision > first.revision
    assert second.bar != first.bar


def test_the_revision_holds_still_when_nothing_changed(app):
    coordinator, _now = _clock_coordinator(app)

    first = coordinator.companion_snapshot()
    second = coordinator.companion_snapshot()

    assert second.revision == first.revision


def test_the_revision_moves_for_a_suggestion_and_for_its_dismissal(app):
    coordinator = _coordinator(app)

    quiet = coordinator.companion_snapshot().revision
    coordinator.show_chords("Verse")
    suggested = coordinator.companion_snapshot().revision
    coordinator.dismiss_suggestions()
    dismissed = coordinator.companion_snapshot().revision

    assert suggested > quiet
    assert dismissed > suggested


def test_the_revision_moves_when_a_job_starts_and_finishes(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    idle = coordinator.companion_snapshot().revision
    coordinator._running_verb = "stems"
    running = coordinator.companion_snapshot().revision
    coordinator._running_verb = ""
    finished = coordinator.companion_snapshot().revision

    assert running > idle
    assert finished > running


# ----------------------------------------------------------------------
# A failed discovery is retried, not permanent
# ----------------------------------------------------------------------
def test_reopening_the_panel_retries_a_failed_discovery(app):
    """A network blip must not disable Song tools for the rest of the night."""

    coordinator = _coordinator(app)
    coordinator._catalog = failed_catalog("WebJam could not reach Music AI.")

    with patch.object(coordinator, "discover_workflows") as discover:
        coordinator.toggle_panel()

    discover.assert_called_once()


def test_reopening_does_not_re_fetch_a_list_it_already_has(app):
    coordinator = _coordinator(app)
    coordinator._catalog = resolve_song_tools(ACCOUNT)

    with patch.object(coordinator, "discover_workflows") as discover:
        coordinator.toggle_panel()

    discover.assert_not_called()


def test_a_failed_discovery_says_how_to_try_again(app):
    coordinator = _coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator._apply_catalog(failed_catalog("WebJam could not reach Music AI."))

    status = coordinator.overlay._tools_status.text()
    assert "could not reach Music AI" in status
    assert "Reopening Song tools tries again" in status


def test_a_rejected_key_is_not_offered_as_something_to_retry(app):
    """Reopening will not fix a wrong key; do not send anyone round the loop."""

    coordinator = _coordinator(app)
    coordinator.overlay.setVisible(True)
    coordinator._apply_catalog(
        failed_catalog("Music AI rejected this API key.", retryable=False)
    )

    status = coordinator.overlay._tools_status.text()
    assert status == "Music AI rejected this API key."
    assert "tries again" not in status

    with patch.object(coordinator, "discover_workflows") as discover:
        coordinator.toggle_panel()
    discover.assert_not_called()


def test_a_rejected_key_is_recorded_as_not_retryable(app):
    from core.music_ai_client import MusicAIAuthError

    coordinator = _coordinator(app)

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=MusicAIAuthError("Music AI rejected this API key."),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.discover_workflows()

    assert coordinator._catalog is not None
    assert not coordinator._catalog.retryable


def test_a_network_failure_is_recorded_as_retryable(app):
    from core.music_ai_client import MusicAITransportError

    coordinator = _coordinator(app)

    class InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with patch(
        "webjam_qt.controllers.song_tools_coordinator.MusicAIClient",
        side_effect=MusicAITransportError("WebJam could not reach Music AI."),
    ), patch(
        "webjam_qt.controllers.song_tools_coordinator.threading.Thread", InlineThread
    ):
        coordinator.discover_workflows()

    assert coordinator._catalog.retryable


def test_discovery_is_still_never_attempted_without_a_key(app):
    coordinator = _coordinator(app, api_key="")
    coordinator._catalog = failed_catalog("WebJam could not reach Music AI.")

    with patch.object(coordinator, "discover_workflows") as discover:
        coordinator.toggle_panel()

    discover.assert_not_called()
