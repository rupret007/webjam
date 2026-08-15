from __future__ import annotations

import os
import re
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
)

from core.creative_modes import get_creator_profile_by_key_or_default  # noqa: E402
from core.session_recording_plan import (  # noqa: E402
    LEGACY_CAPTURE_TRACKS,
    resolve_capture_tracks,
)
from core.settings import AppSettings, load_settings  # noqa: E402
from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.input_map_editor import InputMapEditorDialog  # noqa: E402
from webjam_qt.windows.recording_setup import (  # noqa: E402
    LocalOriginalsChoiceDialog,
    RecordingSetupDialog,
)


APP = QApplication.instance() or QApplication([])


def _dialog_copy(dialog: LocalOriginalsChoiceDialog) -> tuple[list[str], list[str]]:
    labels = [item.text() for item in dialog.findChildren(QLabel)]
    buttons = [item.text() for item in dialog.findChildren(QPushButton)]
    return labels, buttons


def _dialog_accessible_copy(dialog: LocalOriginalsChoiceDialog) -> str:
    button_names = [
        item.accessibleName() for item in dialog.findChildren(QPushButton)
    ]
    return " ".join(
        [
            dialog.accessibleName(),
            dialog.accessibleDescription(),
            *button_names,
        ]
    )


def test_local_originals_choice_keeps_exact_music_copy_by_default() -> None:
    dialog = LocalOriginalsChoiceDialog()
    labels, buttons = _dialog_copy(dialog)

    detail = (
        "WebJam will record the shared Jamulus take either way. You can "
        "also keep configured inputs from this Mac as separate Local "
        "Originals for Studio later. This does not change Jamulus "
        "audio settings."
    )
    assert dialog.windowTitle() == "Keep a local original?"
    assert labels == ["Keep a local original?", detail]
    assert buttons == [
        "Record Shared Jam Only",
        "Also Keep This Mac’s Inputs",
        "Cancel",
    ]
    primary = dialog.findChild(QPushButton, "PrimaryButton")
    assert primary is not None
    assert primary.accessibleName() == "Record shared Jamulus take only"
    assert dialog.accessibleName() == "Keep a local original?"
    assert dialog.accessibleDescription() == detail


def test_podcast_choice_describes_webjam_voice_and_studio_boundaries() -> None:
    dialog = LocalOriginalsChoiceDialog(creator_profile="podcast_voice")
    labels, buttons = _dialog_copy(dialog)
    visible_copy = " ".join([dialog.windowTitle(), *labels, *buttons])
    complete_copy = f"{visible_copy} {_dialog_accessible_copy(dialog)}"

    assert dialog.windowTitle() == "Keep a local voice original?"
    assert "WebJam records the synchronized voice take" in visible_copy
    assert "Podcast & Voice Studio" in visible_copy
    assert "does not directly tap meeting apps" in visible_copy
    assert "do not route meeting/system audio into selected inputs" in visible_copy
    assert "Record Session captures Jamulus server stems" in complete_copy
    assert "Record Shared Voice Take Only" in visible_copy
    for leaked_term in ("shared jam", "band", "musician", "instrument", "song"):
        assert leaked_term not in complete_copy.casefold()


def test_review_choice_is_local_optional_and_playback_only() -> None:
    profile = get_creator_profile_by_key_or_default("review_rehearsal")
    dialog = LocalOriginalsChoiceDialog(creator_profile=profile)
    labels, buttons = _dialog_copy(dialog)
    visible_copy = " ".join([dialog.windowTitle(), *labels, *buttons])
    complete_copy = f"{visible_copy} {_dialog_accessible_copy(dialog)}"

    assert dialog.windowTitle() == "Keep optional Local Originals?"
    assert "shared synchronized WebJam audio take" in complete_copy
    assert "Local Originals for playback and source review" in complete_copy
    assert "playback-only" in complete_copy
    assert "take editing and track export are unavailable" in complete_copy
    assert "does not directly tap meeting apps" in visible_copy
    assert "do not route meeting/system audio into selected inputs" in visible_copy
    assert "Record Session captures Jamulus server stems" in complete_copy
    assert "Record Shared WebJam Audio Only" in visible_copy
    assert "Studio later" not in complete_copy


@pytest.mark.parametrize("profile_key", ("podcast_voice", "review_rehearsal"))
def test_creator_choice_truth_fits_the_compact_dialog_floor(profile_key: str) -> None:
    dialog = LocalOriginalsChoiceDialog(creator_profile=profile_key)
    dialog.setStyleSheet(load_stylesheet())
    dialog.resize(600, 310)
    dialog.show()
    APP.processEvents()
    APP.processEvents()
    try:
        detail = max(dialog.findChildren(QLabel), key=lambda item: len(item.text()))
        required = detail.fontMetrics().boundingRect(
            QRect(0, 0, detail.contentsRect().width(), 10_000),
            Qt.TextFlag.TextWordWrap,
            detail.text(),
        )
        assert dialog.size().width() == 600
        # 600x310 is the requested compact floor. Qt may expand the dialog a
        # few pixels when a platform font wraps to an additional line; that
        # is the correct accessible behavior and must never become clipping.
        assert 310 <= dialog.size().height() <= 360
        assert required.height() <= detail.contentsRect().height()
        buttons = [
            button
            for button in dialog.findChildren(QPushButton)
            if button.isVisibleTo(dialog)
        ]
        assert buttons
        assert max(button.geometry().bottom() for button in buttons) <= (
            dialog.contentsRect().bottom()
        )
    finally:
        dialog.close()


@pytest.mark.parametrize(
    ("creator_profile", "expected_error"),
    [("unknown", ValueError), (7, TypeError)],
)
def test_local_originals_choice_rejects_unsupported_profile_values(
    creator_profile: object,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        LocalOriginalsChoiceDialog(creator_profile=creator_profile)


def test_first_record_choice_receives_the_active_creator_profile(tmp_path) -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.bridge = SimpleNamespace(
        jamulus_state="Not launched",
        hosted_server_alive=mock.Mock(return_value=False),
    )
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        last_creator_profile_key="review_rehearsal",
    )
    controller.window = SimpleNamespace(flash_message=mock.Mock())
    controller.recording = SimpleNamespace(on_record_requested=mock.Mock())

    with mock.patch(
        "webjam_qt.windows.recording_setup.LocalOriginalsChoiceDialog"
    ) as choice_type:
        choice = choice_type.return_value
        choice.exec.return_value = choice_type.DialogCode.Accepted
        choice.choice = "shared"

        controller._on_record_requested()

    choice_type.assert_called_once()
    assert choice_type.call_args.kwargs["parent"] is controller.window
    passed_profile = choice_type.call_args.kwargs["creator_profile"]
    assert passed_profile.key == "review_rehearsal"
    controller.recording.on_record_requested.assert_called_once_with()


def _button(dialog: RecordingSetupDialog, text: str) -> QPushButton:
    return next(
        item for item in dialog.findChildren(QPushButton) if item.text() == text
    )


def _recording_setup_surface_copy(dialog: RecordingSetupDialog) -> str:
    parts = [
        dialog.windowTitle(),
        dialog.accessibleName(),
        dialog.accessibleDescription(),
    ]
    for item in [
        *dialog.findChildren(QLabel),
        *dialog.findChildren(QPushButton),
    ]:
        parts.extend(
            [
                item.text(),
                item.accessibleName(),
                item.accessibleDescription(),
                item.toolTip(),
            ]
        )
    return " ".join(" ".join(str(value or "").split()) for value in parts)


def test_recording_setup_preserves_exact_music_presentation_by_default() -> None:
    settings = AppSettings(local_capture_enabled=True)
    with mock.patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Interface", "channels": 2, "index": 7}],
    ):
        dialog = RecordingSetupDialog(settings, takes_folder_editable=False)

    labels = [item.text() for item in dialog.findChildren(QLabel)]
    assert (
        "The host records the synchronized Jamulus take. Choose whether this Mac "
        "also keeps its configured interface inputs as Local Originals. Studio "
        "chooses its own playback output when you review a take."
    ) in labels
    assert dialog._capture_unavailable.text() == (
        "Local originals are unavailable for this session. You can still play "
        "normally, and the host's synchronized server track is kept."
    )
    assert dialog._capture_help.text() == (
        "Name mono or stereo tracks with Edit Input Tracks. Enabled Local "
        "Originals use device inputs sequentially, up to 32 channels total. An "
        "empty track list keeps the compatible input 1–2 default. The device must "
        "run at 48 kHz and be shareable with Jamulus. WebJam records only after "
        "the host confirms a take."
    )
    assert dialog._edit_tracks_btn.text() == "Edit Input Tracks…"
    assert dialog._edit_tracks_btn.accessibleName() == (
        "Edit the named local input tracks Record Session captures"
    )
    assert _button(dialog, "Choose Folder").toolTip() == (
        "End or restart the current jam before changing its Takes folder."
    )
    assert dialog._tracks_summary.text() == (
        "Using the default two isolated stems (host-guitar, host-vocal)."
    )


def test_recording_setup_podcast_copy_names_voice_studio_and_real_fallback() -> None:
    settings = AppSettings(local_capture_enabled=True)
    with mock.patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Interface", "channels": 2, "index": 7}],
    ):
        dialog = RecordingSetupDialog(
            settings,
            takes_folder_editable=False,
            creator_profile="podcast_voice",
        )

    copy = _recording_setup_surface_copy(dialog)
    assert "synchronized WebJam voice take" in copy
    assert "Podcast & Voice Studio" in copy
    assert "WebJam never directly or automatically taps a meeting app" in copy
    assert "Record Session captures Jamulus server stems" in copy
    assert "Do not route meeting or system audio into those inputs" in copy
    assert dialog._edit_tracks_btn.text() == "Edit Voice Tracks…"
    assert dialog._tracks_summary.text() == (
        "No input map is configured. WebJam will keep the legacy two-input "
        "Local Original fallback (inputs 1–2) until you name voice tracks."
    )
    assert _button(dialog, "Choose Folder").toolTip() == (
        "End or restart the current recording session before changing its Takes "
        "folder."
    )
    assert "host-guitar" not in copy.casefold()
    assert "host-vocal" not in copy.casefold()
    assert re.search(r"\b(?:jam|band|musician|instrument|song|guitar)\b", copy, re.I) is None


def test_recording_setup_review_copy_is_source_based_and_playback_only() -> None:
    settings = AppSettings(local_capture_enabled=True)
    with mock.patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Interface", "channels": 2, "index": 7}],
    ):
        dialog = RecordingSetupDialog(
            settings,
            takes_folder_editable=False,
            creator_profile=get_creator_profile_by_key_or_default(
                "review_rehearsal"
            ),
        )

    copy = _recording_setup_surface_copy(dialog)
    assert "synchronized WebJam audio take" in copy
    assert "Completed-take review is playback-only" in copy
    assert "take editing and track export are unavailable" in copy
    assert "WebJam never directly or automatically taps a meeting app" in copy
    assert "Record Session captures Jamulus server stems" in copy
    assert "Do not route meeting or system audio into those inputs" in copy
    assert dialog._edit_tracks_btn.text() == "Configure Input Sources…"
    assert dialog._edit_tracks_btn.accessibleName() == (
        "Configure the named local input sources Record Session captures"
    )
    assert dialog._tracks_summary.text() == (
        "No input map is configured. WebJam will keep the legacy two-input "
        "Local Original fallback (inputs 1–2) until you name the sources to keep."
    )
    assert _button(dialog, "Choose Folder").toolTip() == (
        "End or restart the current review session before changing its Takes folder."
    )
    assert "Studio" not in copy
    assert "host-guitar" not in copy.casefold()
    assert "host-vocal" not in copy.casefold()
    assert re.search(r"\b(?:jam|band|musician|instrument|song|guitar)\b", copy, re.I) is None


@pytest.mark.parametrize(
    ("profile_key", "expected_take", "expected_session"),
    [
        ("podcast_voice", "WebJam voice take", "recording session"),
        ("review_rehearsal", "WebJam audio take", "review session"),
    ],
)
def test_profile_setup_without_local_originals_keeps_truthful_server_copy(
    profile_key: str,
    expected_take: str,
    expected_session: str,
) -> None:
    with mock.patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[],
    ):
        dialog = RecordingSetupDialog(
            AppSettings(local_capture_enabled=True),
            local_originals_available=False,
            creator_profile=profile_key,
        )

    copy = _recording_setup_surface_copy(dialog)
    assert expected_take in dialog.accessibleDescription()
    assert expected_session in dialog._capture_unavailable.text()
    assert "host's synchronized WebJam server track is kept" in copy
    assert dialog._capture.isEnabled() is False
    assert dialog._capture.isChecked() is False


def test_profile_copy_does_not_change_empty_map_legacy_capture_behavior(
    tmp_path,
) -> None:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        audio_input_device_index=7,
        input_maps=[],
    )
    with mock.patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Interface", "channels": 2, "index": 7}],
    ):
        dialog = RecordingSetupDialog(
            settings,
            creator_profile="podcast_voice",
        )

    dialog._capture.setChecked(True)
    dialog._save()

    saved = load_settings(settings.config_file)
    assert saved.input_maps == []
    assert resolve_capture_tracks(saved) == LEGACY_CAPTURE_TRACKS


def test_recording_setup_receives_the_active_creator_profile() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.bridge = SimpleNamespace(
        jamulus_state="Not launched",
        hosted_server_alive=mock.Mock(return_value=False),
    )
    controller.settings = AppSettings(
        host_server_enabled=True,
        last_creator_profile_key="podcast_voice",
    )
    controller.host_peer = SimpleNamespace(active=False)
    controller.window = SimpleNamespace()
    controller._is_jamulus_running = mock.Mock(return_value=False)

    with mock.patch(
        "webjam_qt.windows.recording_setup.RecordingSetupDialog"
    ) as dialog_type:
        dialog_type.return_value.exec.return_value = dialog_type.DialogCode.Rejected
        controller._open_recording_setup()

    dialog_type.assert_called_once()
    assert dialog_type.call_args.kwargs["parent"] is controller.window
    assert dialog_type.call_args.kwargs["local_originals_available"] is True
    assert dialog_type.call_args.kwargs["takes_folder_editable"] is True
    passed_profile = dialog_type.call_args.kwargs["creator_profile"]
    assert passed_profile.key == "podcast_voice"


def _input_editor_surface_copy(editor: InputMapEditorDialog) -> str:
    parts = [
        editor.windowTitle(),
        editor.accessibleName(),
        editor.accessibleDescription(),
    ]
    for item in [
        *editor.findChildren(QLabel),
        *editor.findChildren(QPushButton),
        *editor.findChildren(QCheckBox),
    ]:
        parts.extend(
            [
                item.text(),
                item.accessibleName(),
                item.accessibleDescription(),
                item.toolTip(),
            ]
        )
    for item in editor.findChildren(QLineEdit):
        parts.extend(
            [
                item.text(),
                item.placeholderText(),
                item.accessibleName(),
                item.accessibleDescription(),
            ]
        )
    return " ".join(" ".join(str(value or "").split()) for value in parts)


def test_input_map_editor_preserves_exact_music_presentation_by_default() -> None:
    editor = InputMapEditorDialog()
    editor._add_row()
    labels = [item.text() for item in editor.findChildren(QLabel)]
    buttons = [item.text() for item in editor.findChildren(QPushButton)]
    row = editor._rows[0]

    assert editor.windowTitle() == "Input Tracks"
    assert (
        "Name the local inputs Record Session captures as isolated Local "
        "Originals. Tracks record on your interface's inputs in this order; a "
        "stereo track uses two inputs. Up to 32 enabled Local Original input "
        "channels are supported. Leave this empty to keep the default two "
        "isolated stems."
    ) in labels
    assert "Add Track" in buttons
    assert "Save Input Tracks" in buttons
    assert editor._rows_scroll.accessibleName() == "Configured input tracks"
    assert row._name.placeholderText() == "Track name (e.g. Guitar DI)"
    assert row._name.accessibleName() == "Input track name"
    assert row._enabled.accessibleName() == "Enable this input track"
    assert row._local_original.accessibleName() == (
        "Keep this track as an isolated Local Original"
    )
    assert row._remove.accessibleName() == "Remove this input track"


@pytest.mark.parametrize(
    (
        "profile_key",
        "title",
        "placeholder",
        "name_accessible",
        "add_text",
        "save_text",
    ),
    [
        (
            "podcast_voice",
            "Voice Input Tracks",
            "Voice track name (e.g. Host Mic)",
            "Voice input track name",
            "Add Voice Track",
            "Save Voice Tracks",
        ),
        (
            "review_rehearsal",
            "Input Sources",
            "Source name (e.g. Room Mic)",
            "Input source name",
            "Add Source",
            "Save Sources",
        ),
    ],
)
def test_input_map_editor_profiles_have_truthful_visible_and_accessible_copy(
    profile_key: str,
    title: str,
    placeholder: str,
    name_accessible: str,
    add_text: str,
    save_text: str,
) -> None:
    editor = InputMapEditorDialog(creator_profile=profile_key)
    editor._add_row()
    row = editor._rows[0]
    copy = _input_editor_surface_copy(editor)

    assert editor.windowTitle() == title
    assert editor.accessibleName() == title
    assert "legacy two-input Local Original fallback (inputs 1–2)" in copy
    assert row._name.placeholderText() == placeholder
    assert row._name.accessibleName() == name_accessible
    assert add_text in copy
    assert save_text in copy
    assert "Guitar DI" not in copy
    assert re.search(r"\b(?:jam|band|musician|instrument|song|guitar)\b", copy, re.I) is None
    if profile_key == "review_rehearsal":
        assert "Studio" not in copy
        assert "input source" in copy.casefold()
    else:
        assert "voice input track" in copy.casefold()


def test_profile_input_map_editor_preserves_round_trip_semantics() -> None:
    maps = [
        {
            "name": "Room Mic",
            "channels": 2,
            "enabled": True,
            "local_original_enabled": True,
        }
    ]
    editor = InputMapEditorDialog(
        maps,
        creator_profile="review_rehearsal",
    )

    ok, error, collected = editor.collect()

    assert ok is True
    assert error == ""
    assert collected == maps


def test_recording_setup_threads_profile_to_input_map_editor() -> None:
    with mock.patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[],
    ):
        setup = RecordingSetupDialog(
            AppSettings(),
            creator_profile="review_rehearsal",
        )

    with mock.patch(
        "webjam_qt.windows.input_map_editor.InputMapEditorDialog"
    ) as editor_type:
        editor_type.return_value.exec.return_value = editor_type.DialogCode.Rejected
        setup._edit_input_tracks()

    editor_type.assert_called_once()
    assert editor_type.call_args.args == ([],)
    assert editor_type.call_args.kwargs["parent"] is setup
    passed_profile = editor_type.call_args.kwargs["creator_profile"]
    assert passed_profile.key == "review_rehearsal"
