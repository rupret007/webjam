"""Bring-your-own-key writing help, from the panel down to the request.

The design lock these hold: the jam works with no keys, WebJam's own writing
help works with no keys, a model key only ever adds a labelled suggestion, and
nothing is sent to a provider without a musician seeing the text first.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.creative_modes import get_creator_profile_by_key_or_default
from core.secret_store import set_default_secret_store
from core.song_form import parse_song_form
from core.song_model_help import ModelChordSuggestion, ModelHelpResult
from tests.support.fake_secret_store import FakeSecretStore
from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator
from webjam_qt.widgets.song_overlay import SongOverlay

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET = "Key: A minor\nTempo: 104\n[Verse]\nAm F C G\nDriving through town\n"

COORDINATOR = "webjam_qt.controllers.song_tools_coordinator"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store():
    fake = FakeSecretStore()
    set_default_secret_store(fake)
    yield fake
    set_default_secret_store(None)


@pytest.fixture
def overlay(app):
    widget = SongOverlay()
    yield widget
    widget.deleteLater()


class InlineThread:
    """Runs the worker body where the test can see it fail."""

    started = 0

    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        type(self).started += 1
        if self._target is not None:
            self._target()


def _controller(app, *, notes=SHEET, is_host=True):
    overlay = SongOverlay()
    window = SimpleNamespace(
        song_overlay=overlay,
        session_canvas=SimpleNamespace(
            current_notes=lambda: notes, set_notes=MagicMock()
        ),
        session_strip=SimpleNamespace(
            current_title=lambda: "Tuesday Jam",
            _elapsed_seconds=0,
            _shared_track_last_snapshot=None,
            set_song_line=MagicMock(),
        ),
        flash_message=MagicMock(),
    )
    return SimpleNamespace(
        window=window,
        settings=SimpleNamespace(
            music_ai_api_key="", webex_url="", takes_directory=""
        ),
        creator_profile=get_creator_profile_by_key_or_default("music"),
        jamulus=SimpleNamespace(send_chat=MagicMock(return_value=True)),
        _reference_track_is_host=lambda: is_host,
        _reference_track_load_pending=None,
        _snapshot_participants=lambda: [],
        _ui_invoker=None,
        _open_settings_wizard=MagicMock(),
    )


def _coordinator(app, **kwargs) -> SongToolsCoordinator:
    coordinator = SongToolsCoordinator(_controller(app, **kwargs))
    coordinator.connect()
    return coordinator


def _flashes(coordinator) -> list[str]:
    return [
        call.args[0]
        for call in coordinator._c.window.flash_message.call_args_list
    ]


def _result(section="Bridge", provider="OpenAI"):
    return ModelHelpResult(
        provider_id="openai",
        provider_label=provider,
        model="gpt-5.6-luna",
        section_label=section,
        key="A minor",
        suggestions=(
            ModelChordSuggestion(
                chords=("F", "C", "G", "Am"),
                reason="Leaves the tonic before the last chorus.",
                provider_label=provider,
            ),
        ),
    )


# ----------------------------------------------------------------------
# The panel, with and without a key
# ----------------------------------------------------------------------
def test_with_no_key_the_ask_button_is_absent_and_one_line_explains(overlay):
    overlay.set_model_help_state(
        providers=(), note="Add a model key in Settings to ask a model."
    )

    assert overlay._model_button.isHidden()
    assert overlay._model_provider.isHidden()
    assert overlay._model_note.isHidden() is False
    assert overlay._model_note.text().count(".") <= 2


def test_webjam_s_own_write_help_is_offered_whether_or_not_a_key_exists(overlay):
    """The jam is not broken by a missing key; it is missing an extra."""

    overlay.set_model_help_state(providers=(), note="Add a model key in Settings.")

    assert overlay._write_button.isHidden() is False
    assert overlay._chords_button.isHidden() is False
    assert overlay._write_button.isEnabled()


def test_one_key_names_that_provider_and_shows_no_picker(overlay):
    overlay.set_model_help_state(providers=(("openai", "OpenAI"),))

    assert overlay._model_button.isHidden() is False
    assert overlay._model_button.text() == "Ask OpenAI"
    assert overlay._model_provider.isHidden()
    assert overlay._model_note.isHidden()


def test_two_keys_are_chosen_between_here_not_at_launch(overlay):
    overlay.set_model_help_state(
        providers=(("openai", "OpenAI"), ("anthropic", "Anthropic"))
    )

    assert overlay._model_provider.isHidden() is False
    assert [
        overlay._model_provider.itemText(index)
        for index in range(overlay._model_provider.count())
    ] == ["OpenAI", "Anthropic"]
    assert overlay.selected_model_provider() == "openai"


def test_the_ask_button_says_what_leaves_the_computer(overlay):
    overlay.set_model_help_state(providers=(("openai", "OpenAI"),))

    tooltip = overlay._model_button.toolTip()
    assert "your own key" in tooltip
    assert "No audio, no lyrics, no file." in tooltip


def test_asking_is_refused_while_the_song_is_empty(overlay):
    overlay.set_model_help_state(
        providers=(("openai", "OpenAI"),), enabled=False
    )

    assert overlay._model_button.isEnabled() is False


def test_a_model_answer_is_drawn_as_a_suggestion_that_names_its_provider(overlay):
    overlay.set_model_suggestions(_result())

    from PySide6.QtWidgets import QLabel

    text = "\n".join(
        label.text()
        for row in overlay._suggestion_rows
        for label in row.findChildren(QLabel)
    )
    assert "OpenAI suggestion · F C G Am" in text
    assert "Suggestions, not what the song is." in (
        overlay._suggestion_headline.text()
    )
    assert overlay._dismiss_button.isHidden() is False


def test_a_model_answer_can_be_kept_with_one_tap(overlay):
    kept: list[tuple[str, str]] = []
    overlay.suggestion_kept.connect(lambda label, line: kept.append((label, line)))

    overlay.set_model_suggestions(_result())
    from PySide6.QtWidgets import QPushButton

    keep = [
        button
        for row in overlay._suggestion_rows
        for button in row.findChildren(QPushButton)
    ][0]
    keep.click()

    assert kept == [("Bridge", "F C G Am")]


def test_dismissing_a_model_answer_clears_it_and_changes_nothing(overlay):
    overlay.set_model_suggestions(_result())

    overlay.clear_suggestions()

    assert overlay._suggestion_rows == []
    assert overlay._suggestion_headline.isHidden()


def test_asking_shows_quiet_progress_rather_than_a_blocking_wait(overlay):
    overlay.set_model_help_state(providers=(("openai", "OpenAI"),))

    overlay.set_model_busy("OpenAI")

    assert overlay._suggestion_headline.text() == "Asking OpenAI…"
    assert overlay._model_button.isEnabled() is False
    assert overlay.isModal() is False


# ----------------------------------------------------------------------
# The coordinator
# ----------------------------------------------------------------------
def test_with_no_key_nothing_is_sent_and_the_line_points_at_settings(app, store):
    coordinator = _coordinator(app)

    with patch(f"{COORDINATOR}.ask_for_section") as asked, patch(
        f"{COORDINATOR}.threading.Thread"
    ) as thread:
        coordinator.ask_model_for_chords()

    asked.assert_not_called()
    thread.assert_not_called()
    assert any("Settings" in message for message in _flashes(coordinator))


def test_the_panel_offers_the_keys_this_computer_actually_has(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    store.items["minimax"] = "mm-1"

    coordinator._render_model_help_state()

    assert coordinator.overlay._model_provider.isHidden() is False
    assert [
        coordinator.overlay._model_provider.itemData(index)
        for index in range(coordinator.overlay._model_provider.count())
    ] == ["openai", "minimax"]


def test_the_first_ask_of_a_session_shows_the_exact_text_first(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"

    with patch(
        f"{COORDINATOR}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ) as confirm, patch(f"{COORDINATOR}.ask_for_section") as asked, patch(
        f"{COORDINATOR}.threading.Thread"
    ) as thread:
        coordinator.ask_model_for_chords("openai")

    asked.assert_not_called()
    thread.assert_not_called()
    body = confirm.call_args.args[2]
    assert "Key: A minor" in body
    assert "Verse: Am F C G" in body
    assert "Driving through town" not in body
    assert "does not send audio" in body


def test_saying_yes_sends_and_renders_the_answer(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    InlineThread.started = 0

    with patch(
        f"{COORDINATOR}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        f"{COORDINATOR}.ask_for_section", return_value=_result()
    ) as asked, patch(f"{COORDINATOR}.threading.Thread", InlineThread):
        coordinator.ask_model_for_chords("openai")

    assert InlineThread.started == 1, "a model call must not block the jam"
    assert asked.call_args.kwargs["api_key"] == "sk-1"
    assert asked.call_args.kwargs["provider_id"] == "openai"
    assert coordinator.overlay._suggestion_rows


def test_consent_is_asked_once_per_session_not_once_per_suggestion(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"

    with patch(
        f"{COORDINATOR}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as confirm, patch(
        f"{COORDINATOR}.ask_for_section", return_value=_result()
    ), patch(f"{COORDINATOR}.threading.Thread", InlineThread):
        coordinator.ask_model_for_chords("openai")
        coordinator.ask_model_for_chords("openai")

    assert confirm.call_count == 1


def test_a_second_provider_asks_for_its_own_consent(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    store.items["anthropic"] = "sk-ant-1"

    with patch(
        f"{COORDINATOR}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as confirm, patch(
        f"{COORDINATOR}.ask_for_section", return_value=_result()
    ), patch(f"{COORDINATOR}.threading.Thread", InlineThread):
        coordinator.ask_model_for_chords("openai")
        coordinator.ask_model_for_chords("anthropic")

    assert confirm.call_count == 2


def test_a_refusal_from_the_provider_is_reported_and_draws_nothing(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    coordinator._model_consent.add("openai")
    blocked = ModelHelpResult(
        provider_id="openai",
        provider_label="OpenAI",
        blocked_reason="OpenAI rejected this key. Check it in Settings.",
    )

    with patch(
        f"{COORDINATOR}.ask_for_section", return_value=blocked
    ), patch(f"{COORDINATOR}.threading.Thread", InlineThread):
        coordinator.ask_model_for_chords("openai")

    assert coordinator.overlay._suggestion_rows == []
    assert "OpenAI rejected this key. Check it in Settings." in _flashes(coordinator)


def test_asking_about_an_empty_song_never_leaves_the_computer(app, store):
    coordinator = _coordinator(app, notes="")
    store.items["openai"] = "sk-1"

    with patch(f"{COORDINATOR}.ask_for_section") as asked, patch(
        f"{COORDINATOR}.QMessageBox.question"
    ) as confirm:
        coordinator.ask_model_for_chords("openai")

    asked.assert_not_called()
    confirm.assert_not_called()
    assert any("nothing" in message.lower() for message in _flashes(coordinator))


def test_only_one_model_request_runs_at_a_time(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    coordinator._model_consent.add("openai")
    coordinator._asking_model = "openai"

    with patch(f"{COORDINATOR}.ask_for_section") as asked:
        coordinator.ask_model_for_chords("openai")

    asked.assert_not_called()


def test_a_model_suggestion_reaches_a_companion_labelled_and_attributed(app, store):
    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    coordinator._model_consent.add("openai")

    with patch(
        f"{COORDINATOR}.ask_for_section", return_value=_result()
    ), patch(f"{COORDINATOR}.threading.Thread", InlineThread):
        coordinator.ask_model_for_chords("openai")

    published = coordinator.companion_snapshot().to_public_dict()["suggestion"]

    assert published["label"] == "suggestion"
    assert published["chords"] == ["F", "C", "G", "Am"]
    assert published["reason"].startswith("OpenAI:")


def test_the_music_ai_key_now_resolves_from_the_credential_store_too(app, store):
    coordinator = _coordinator(app)
    store.items["music_ai"] = "stored-music-ai-key"

    assert coordinator._api_key() == "stored-music-ai-key"


def test_song_tools_still_read_an_older_music_ai_key_from_settings(app, store):
    coordinator = _coordinator(app)
    coordinator._c.settings.music_ai_api_key = "from-the-old-file"

    assert coordinator._api_key() == "from-the-old-file"


# ----------------------------------------------------------------------
# ADR 0002: a suggestion is not a measurement
# ----------------------------------------------------------------------
def test_a_model_answer_never_becomes_a_detected_chord_or_lyric(app, store):
    """Music AI measures. A model proposes. The form only records the first."""

    coordinator = _coordinator(app)
    store.items["openai"] = "sk-1"
    coordinator._model_consent.add("openai")

    with patch(
        f"{COORDINATOR}.ask_for_section", return_value=_result()
    ), patch(f"{COORDINATOR}.threading.Thread", InlineThread):
        coordinator.ask_model_for_chords("openai")

    assert coordinator.workbench.detected_chords() == ()
    assert coordinator.workbench.lyrics() == ""
    assert coordinator.workbench.runs == ()
    assert coordinator.workbench.form == parse_song_form(SHEET, title="Tuesday Jam")
    coordinator._c.window.session_canvas.set_notes.assert_not_called()


def test_the_model_path_never_touches_the_conductor_or_recording():
    ask = ast.parse(
        inspect.getsource(SongToolsCoordinator.ask_model_for_chords).lstrip()
    )
    touched = {
        node.attr for node in ast.walk(ask) if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "_update_session_hud",
        "_publish_musician_guidance",
        "_refresh_session_pulse",
        "session_conductor",
        "_on_record_requested",
        "attach_run",
        "set_notes",
    ):
        assert forbidden not in touched, forbidden


def test_no_model_sdk_is_imported_anywhere_in_the_model_path():
    """ADR 0002: no generative runtime ships. This is HTTP and a parser."""

    for relative in ("core/text_model_client.py", "core/song_model_help.py"):
        tree = ast.parse((REPO_ROOT / relative).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        for sdk in ("openai", "anthropic", "torch", "transformers", "langchain"):
            assert sdk not in imported, f"{relative}: {sdk}"


def test_the_model_client_never_reaches_audio_or_the_jam():
    for relative in ("core/text_model_client.py", "core/song_model_help.py"):
        source = (REPO_ROOT / relative).read_text()
        tree = ast.parse(source)
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "jamulus",
            "sounddevice",
            "soundfile",
            "audio_engine",
            "stem_bench",
            "reference_track",
        ):
            assert not any(
                forbidden in name.lower() for name in names
            ), f"{relative}: {forbidden}"
