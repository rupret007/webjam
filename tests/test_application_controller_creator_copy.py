from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QMessageBox

from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.recording_coordinator import (
    RecorderPhase,
    RecordingCoordinator,
)


def _close_controller(
    profile_key: str,
    *,
    hosting: bool,
    recording_active: bool = False,
    take_in_progress: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        _active_creator_profile_key=profile_key,
        recording=SimpleNamespace(
            is_recording_active=recording_active,
            take_in_progress=take_in_progress,
            confirm_quit=MagicMock(return_value=True),
        ),
        bridge=SimpleNamespace(
            hosted_server_alive=MagicMock(return_value=False),
            hosted_server_owned=MagicMock(return_value=hosting),
        ),
        settings=SimpleNamespace(
            host_server_enabled=hosting,
            last_creator_profile_key=profile_key,
        ),
        window=SimpleNamespace(
            recording_studio=SimpleNamespace(export_in_progress=False)
        ),
        _is_jamulus_running=MagicMock(return_value=True),
    )


def _invite_controller(profile_key: str, *, webex_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        _active_creator_profile_key=profile_key,
        _remote_invite_owner=SimpleNamespace(
            copy_for_clipboard=MagicMock(return_value="webjam://join?v=3")
        ),
        _shutdown_cleanup_blocks_action=lambda: False,
        _host_peer_warning="",
        settings=SimpleNamespace(webex_url=webex_url),
        window=SimpleNamespace(
            flash_message=MagicMock(),
            session_strip=SimpleNamespace(
                current_title=MagicMock(return_value="Tuesday Jam")
            ),
        ),
    )


@pytest.mark.parametrize(
    ("profile_key", "expected"),
    [
        ("music", "Invite link copied — send it to another musician."),
        ("podcast_voice", "Invite link copied — send it to another speaker."),
        (
            "review_rehearsal",
            "Invite link copied — send it to another participant.",
        ),
    ],
)
def test_copy_invite_success_uses_creator_participant_vocabulary(
    profile_key: str,
    expected: str,
) -> None:
    clipboard = MagicMock()
    controller = _invite_controller(profile_key)

    with patch("PySide6.QtWidgets.QApplication") as application:
        application.clipboard.return_value = clipboard
        ApplicationController._copy_band_invite(controller)

    copied = clipboard.setText.call_args.args[0]
    assert "webjam://join?v=3" in copied
    assert controller.window.flash_message.call_args.args[0] == expected


def test_copy_invite_carries_the_meeting_link_in_the_same_message() -> None:
    """One paste, not two: a bandmate should not have to chase a second link."""

    clipboard = MagicMock()
    controller = _invite_controller(
        "music", webex_url="https://band.webex.com/meet/jeff"
    )

    with patch("PySide6.QtWidgets.QApplication") as application:
        application.clipboard.return_value = clipboard
        ApplicationController._copy_band_invite(controller)

    copied = clipboard.setText.call_args.args[0]
    assert "webjam://join?v=3" in copied
    assert "https://band.webex.com/meet/jeff" in copied
    assert "does not run it" in copied
    assert controller.window.flash_message.call_args.args[0] == (
        "One invite copied — jam link and meeting link. Send it to another "
        "musician."
    )


def test_copy_invite_drops_a_meeting_link_that_fails_validation() -> None:
    """A malformed or non-Webex link is never pasted into a bandmate's chat."""

    clipboard = MagicMock()
    controller = _invite_controller("music", webex_url="https://evil.example.com/meet")

    with patch("PySide6.QtWidgets.QApplication") as application:
        application.clipboard.return_value = clipboard
        ApplicationController._copy_band_invite(controller)

    copied = clipboard.setText.call_args.args[0]
    assert "evil.example.com" not in copied
    assert "webjam://join?v=3" in copied


@pytest.mark.parametrize(
    ("profile_key", "expected"),
    [
        (
            "music",
            "Message not sent. Reconnect to your band, then press Enter to try again.",
        ),
        (
            "podcast_voice",
            "Message not sent. Reconnect to the recording session with your "
            "speakers, then press Enter to try again.",
        ),
        (
            "review_rehearsal",
            "Message not sent. Reconnect to the Preview review session with your "
            "participants, then press Enter to try again.",
        ),
    ],
)
def test_chat_rejection_uses_creator_session_vocabulary(
    profile_key: str,
    expected: str,
) -> None:
    canvas = SimpleNamespace(
        restore_unsent_chat=MagicMock(),
        append_line=MagicMock(),
    )
    controller = SimpleNamespace(
        _active_creator_profile_key=profile_key,
        jamulus=SimpleNamespace(send_chat=MagicMock(return_value=False)),
        window=SimpleNamespace(
            session_canvas=canvas,
            flash_message=MagicMock(),
        ),
    )

    ApplicationController._on_chat_submitted(controller, "keep this")

    canvas.restore_unsent_chat.assert_called_once_with("keep this")
    canvas.append_line.assert_not_called()
    assert controller.window.flash_message.call_args.args[0] == expected


@pytest.mark.parametrize(
    ("profile_key", "hosting", "expected_title", "expected_body"),
    [
        (
            "music",
            True,
            "End jam and quit?",
            "Quitting WebJam ends this jam for every connected musician.",
        ),
        (
            "music",
            False,
            "Leave jam and quit?",
            "Quitting WebJam disconnects you; the band can keep playing.",
        ),
        (
            "podcast_voice",
            True,
            "End recording session and quit?",
            "Quitting WebJam ends this recording session for every connected speaker.",
        ),
        (
            "podcast_voice",
            False,
            "Leave recording session and quit?",
            "Quitting WebJam disconnects you; the other speakers can continue "
            "the recording session.",
        ),
        (
            "review_rehearsal",
            True,
            "End review session and quit? (Preview)",
            "Quitting WebJam ends this Preview review session for every connected "
            "participant.",
        ),
        (
            "review_rehearsal",
            False,
            "Leave review session and quit? (Preview)",
            "Quitting WebJam disconnects you; the other participants can continue "
            "the Preview review session.",
        ),
    ],
)
def test_live_quit_confirmation_uses_creator_and_role_copy(
    profile_key: str,
    hosting: bool,
    expected_title: str,
    expected_body: str,
) -> None:
    controller = _close_controller(profile_key, hosting=hosting)

    with patch.object(
        QMessageBox,
        "question",
        return_value=QMessageBox.StandardButton.No,
    ) as question:
        assert ApplicationController._confirm_close(controller) is False

    assert question.call_args.args[1] == expected_title
    assert question.call_args.args[2] == expected_body
    assert question.call_args.args[4] == QMessageBox.StandardButton.No


@pytest.mark.parametrize(
    ("profile_key", "expected_title", "expected_preservation"),
    [
        (
            "music",
            "Finish the take first",
            "This keeps every musician's track complete and verified.",
        ),
        (
            "podcast_voice",
            "Finish the recording first",
            "This keeps every speaker's recorded track complete and verified.",
        ),
        (
            "review_rehearsal",
            "Finish the review recording first (Preview)",
            "This preserves every participant's captured source and verification "
            "evidence.",
        ),
    ],
)
def test_in_progress_hosted_recording_close_uses_truthful_preservation_copy(
    profile_key: str,
    expected_title: str,
    expected_preservation: str,
) -> None:
    controller = _close_controller(
        profile_key,
        hosting=True,
        recording_active=True,
        take_in_progress=True,
    )

    with patch.object(QMessageBox, "information") as information:
        assert ApplicationController._confirm_close(controller) is False

    title, body = information.call_args.args[1:3]
    assert title == expected_title
    assert body.endswith(expected_preservation)
    if profile_key == "review_rehearsal":
        assert "editing" not in body.lower()
        assert "export" not in body.lower()
    controller.recording.confirm_quit.assert_not_called()


def test_review_studio_close_failure_does_not_claim_editing_or_export() -> None:
    controller = SimpleNamespace(
        _active_creator_profile_key="review_rehearsal",
        window=SimpleNamespace(
            recording_studio=SimpleNamespace(
                prepare_close=MagicMock(return_value=False)
            )
        ),
    )

    with patch.object(QMessageBox, "information") as information:
        assert ApplicationController._prepare_studio_close(controller) is False

    title, body = information.call_args.args[1:3]
    assert title == "Review state not saved (Preview)"
    assert "captured sources remain protected" in body
    assert "edit" not in body.lower()
    assert "export" not in body.lower()


def test_successful_guest_peer_cleanup_restores_saved_local_creator_profile() -> None:
    guest = SimpleNamespace(stop=MagicMock(return_value=True))
    clear_projection = MagicMock()
    visible_title = {"value": "Borrowed Host Review"}
    set_session_title = MagicMock(
        side_effect=lambda title: visible_title.__setitem__("value", title)
    )
    persistence = SimpleNamespace(
        switch_profile_key=MagicMock(),
        clear_borrowed_title=MagicMock(),
        _load_session_metadata=MagicMock(
            side_effect=lambda: set_session_title("My Local Podcast")
        ),
    )
    controller = SimpleNamespace(
        guest_peer=guest,
        host_peer=None,
        _creator_profile_host_owned=True,
        _active_creator_profile_key="review_rehearsal",
        _host_peer_warning="host-owned",
        _persistence=persistence,
        settings=SimpleNamespace(last_creator_profile_key="podcast_voice"),
        window=SimpleNamespace(
            session_strip=SimpleNamespace(
                clear_shared_track_projection=clear_projection,
                set_session_title=set_session_title,
            ),
        ),
    )

    assert ApplicationController._stop_session_peer(controller) is True

    guest.stop.assert_called_once_with()
    assert controller.guest_peer is None
    assert controller._active_creator_profile_key == "podcast_voice"
    assert controller._creator_profile_host_owned is False
    clear_projection.assert_called_once_with()
    persistence.switch_profile_key.assert_called_once_with("podcast_voice")
    persistence.clear_borrowed_title.assert_called_once_with()
    assert set_session_title.call_args_list[0].args == ("Host + Guest Recording",)
    assert set_session_title.call_args_list[-1].args == ("My Local Podcast",)
    persistence._load_session_metadata.assert_called_once_with()
    assert visible_title["value"] == "My Local Podcast"


def test_failed_guest_peer_cleanup_retains_authenticated_host_profile() -> None:
    guest = SimpleNamespace(stop=MagicMock(return_value=False))
    clear_projection = MagicMock()
    visible_title = {"value": "Borrowed Host Review"}
    set_session_title = MagicMock(
        side_effect=lambda title: visible_title.__setitem__("value", title)
    )
    persistence = SimpleNamespace(
        switch_profile_key=MagicMock(),
        clear_borrowed_title=MagicMock(),
        _load_session_metadata=MagicMock(),
    )
    controller = SimpleNamespace(
        guest_peer=guest,
        host_peer=None,
        _creator_profile_host_owned=True,
        _active_creator_profile_key="review_rehearsal",
        _host_peer_warning="host-owned",
        _persistence=persistence,
        settings=SimpleNamespace(last_creator_profile_key="podcast_voice"),
        window=SimpleNamespace(
            session_strip=SimpleNamespace(
                clear_shared_track_projection=clear_projection,
                set_session_title=set_session_title,
            ),
        ),
    )

    assert ApplicationController._stop_session_peer(controller) is False

    guest.stop.assert_called_once_with()
    assert controller.guest_peer is guest
    assert controller._active_creator_profile_key == "review_rehearsal"
    assert controller._creator_profile_host_owned is True
    assert controller._host_peer_warning == "host-owned"
    clear_projection.assert_not_called()
    persistence.switch_profile_key.assert_not_called()
    persistence.clear_borrowed_title.assert_not_called()
    persistence._load_session_metadata.assert_not_called()
    set_session_title.assert_not_called()
    assert visible_title["value"] == "Borrowed Host Review"


@pytest.mark.parametrize(
    ("profile_key", "hosting", "required_fragments", "forbidden_fragments"),
    [
        (
            "podcast_voice",
            True,
            ("recording session", "connected speaker", "completed source files"),
            ("band", "musician", "edit", "export"),
        ),
        (
            "podcast_voice",
            False,
            ("recording session", "local source files", "Recovered folder"),
            ("band", "musician", "edit", "export"),
        ),
        (
            "review_rehearsal",
            True,
            ("Preview review session", "participant", "completed source files"),
            ("band", "musician", "edit", "export"),
        ),
        (
            "review_rehearsal",
            False,
            ("Preview review session", "local source files", "Recovered folder"),
            ("band", "musician", "edit", "export"),
        ),
    ],
)
def test_mid_recording_quit_dialog_uses_frozen_creator_profile(
    profile_key: str,
    hosting: bool,
    required_fragments: tuple[str, ...],
    forbidden_fragments: tuple[str, ...],
) -> None:
    coordinator = RecordingCoordinator.__new__(RecordingCoordinator)
    coordinator.phase = RecorderPhase.RECORDING
    coordinator._recording_creator_profile_key = profile_key
    coordinator._c = SimpleNamespace(
        _recorder_armed=True,
        _server_recording=True,
        settings=SimpleNamespace(host_server_enabled=hosting),
        bridge=SimpleNamespace(hosted_server_owned=MagicMock(return_value=hosting)),
        window=object(),
    )

    with patch(
        "webjam_qt.controllers.recording_coordinator.QMessageBox"
    ) as message_box:
        box = message_box.return_value
        quit_button = object()
        cancel_button = object()
        box.addButton.side_effect = (quit_button, cancel_button)
        box.clickedButton.return_value = cancel_button
        assert coordinator.confirm_quit() is False

    body = box.setText.call_args.args[0]
    for fragment in required_fragments:
        assert fragment in body
    for fragment in forbidden_fragments:
        assert fragment not in body.lower()


@pytest.mark.parametrize(
    ("hosting", "expected"),
    [
        (
            True,
            "A recording is still running, and this Mac is hosting the band "
            "server.\n\nQuitting stops the recording AND ends the session for every "
            "connected musician. WebJam will stop the recording cleanly and save "
            "your isolated local tracks before it quits.\n\nQuit WebJam?",
        ),
        (
            False,
            "A recording is still running.\n\nQuitting disconnects this computer, "
            "but the band server keeps recording until someone presses ■ Stop Rec. "
            "Your isolated local tracks will be saved to a Recovered folder before "
            "WebJam quits.\n\nQuit WebJam?",
        ),
    ],
)
def test_mid_recording_music_quit_copy_remains_exact(
    hosting: bool,
    expected: str,
) -> None:
    coordinator = RecordingCoordinator.__new__(RecordingCoordinator)
    coordinator.phase = RecorderPhase.RECORDING
    coordinator._recording_creator_profile_key = "music"
    coordinator._c = SimpleNamespace(
        _recorder_armed=True,
        _server_recording=True,
        settings=SimpleNamespace(host_server_enabled=hosting),
        bridge=SimpleNamespace(hosted_server_owned=MagicMock(return_value=hosting)),
        window=object(),
    )

    with patch(
        "webjam_qt.controllers.recording_coordinator.QMessageBox"
    ) as message_box:
        box = message_box.return_value
        box.addButton.side_effect = (object(), object())
        box.clickedButton.return_value = None
        assert coordinator.confirm_quit() is False

    assert box.setText.call_args.args[0] == expected
