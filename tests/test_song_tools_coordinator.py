"""The controller glue: nothing uploads without a chosen file and a host yes.

These drive the coordinator against a fake controller and a fake transport, so
the whole path from button press to attached result is covered without a
socket, a real window, or a real session.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.creative_modes import get_creator_profile_by_key_or_default
from core.music_ai_catalog import failed_catalog, resolve_song_tools
from core.music_ai_client import API_BASE_URL, MusicAIResponse, MusicAIWorkflow
from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator
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
        session_canvas=SimpleNamespace(current_notes=lambda: notes),
        session_strip=SimpleNamespace(
            current_title=lambda: "Tuesday Jam", _elapsed_seconds=1320
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

    text = coordinator.overlay._advice.text()
    assert "Suggestions for" in text
    assert "A minor" in text


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
def test_the_panel_copies_one_invite_with_both_links(app):
    coordinator = _coordinator(app, webex_url="https://band.webex.com/meet/jeff")
    coordinator._c._host_share_readiness = MagicMock(return_value=SimpleNamespace())
    coordinator._c._current_invite_url = MagicMock(
        return_value="webjam://join?v=2&host=10.0.0.2&port=22124"
    )
    clipboard = MagicMock()

    with patch("PySide6.QtWidgets.QApplication") as application:
        application.clipboard.return_value = clipboard
        coordinator._copy_invite()

    copied = clipboard.setText.call_args.args[0]
    assert "webjam://join?v=2" in copied
    assert "https://band.webex.com/meet/jeff" in copied


def test_the_mute_action_hands_off_to_the_meeting_app(app):
    coordinator = _coordinator(app)
    coordinator._open_meeting_mute()
    coordinator._c._focus_webex_mute.assert_called_once()


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

    text = coordinator.overlay._advice.text()
    assert "Verse already plays Am F C G. Instead:" in text
    assert "After Am F C G in Verse" in text


def test_chord_help_with_no_selection_answers_the_next_missing_part(app):
    coordinator = _coordinator(app)
    coordinator.show_chords("")

    text = coordinator.overlay._advice.text()
    assert "Suggestions for Chorus" in text
    # Nothing exists to continue from, so no next-chord block is offered.
    assert "After " not in text


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
