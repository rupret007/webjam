"""A complete invitation carries a session-only, explicit conversation handoff."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from core.meeting_companion import build_invite_message
from core.network_invite import create_invite_link
from core.remote_invitation import issue_remote_invitation
from core.settings import AppSettings
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
    InvitationSource,
    conversation_url_from_pasted_invitation,
    parse_invitation_at_ingress,
)
from webjam_qt.windows.launch_dialog import LaunchDialog


HOST_MEETING = "https://host.webex.com/meet/artist"
PERSONAL_MEETING = "https://guest.webex.com/meet/personal"


def invitation_message(*, profile="art", meeting=HOST_MEETING, native=False):
    if native:
        link = issue_remote_invitation(
            "reference-local", allowed_profiles={"reference-local"},
            host_spki_sha256=b"p" * 32,
        ).private_link.reveal_for_clipboard()
    else:
        link = create_invite_link(
            "192.168.1.42", session_name="Saturday Making",
            session_id="11111111-1111-4111-8111-111111111111",
            peer_port=43121, invite_token="t" * 43,
        )
    return build_invite_message(
        join_link=link, creator_profile_key=profile, meeting_url=meeting,
    ).text


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("profile", ("art", "music"))
@pytest.mark.parametrize("native", (False, True))
@pytest.mark.parametrize("meeting", (
    HOST_MEETING, "https://zoom.us/j/123", "https://meet.jit.si/artists",
))
def test_generated_complete_invitation_retains_exact_optional_conversation(
    profile, native, meeting,
):
    message = invitation_message(profile=profile, native=native, meeting=meeting)
    transport = parse_invitation_at_ingress(message, source=InvitationSource.PASTE)
    assert transport is not None
    assert conversation_url_from_pasted_invitation(message) == meeting
    # Supplemental context never adds fields to transport representations.
    assert meeting not in repr(transport)


@pytest.mark.parametrize("quote", ("", "> ", ">> ", "> > "))
@pytest.mark.parametrize("wrapped", (
    HOST_MEETING, f"<{HOST_MEETING}>", f'"{HOST_MEETING}"',
    f"`{HOST_MEETING}`", f"({HOST_MEETING}).",
))
def test_forwarded_invitation_preserves_labeled_link_and_ignores_signature(
    quote, wrapped,
):
    message = invitation_message().replace(HOST_MEETING, wrapped)
    forwarded = "\r\n".join(quote + line for line in message.splitlines())
    forwarded += "\n-- My site\nhttps://meet.jit.si/unrelated-signature"
    assert conversation_url_from_pasted_invitation(forwarded) == HOST_MEETING


def test_repeated_identical_forwarded_message_has_one_conversation():
    message = invitation_message()
    assert conversation_url_from_pasted_invitation(
        message + "\n" + "\n".join("> " + line for line in message.splitlines())
    ) == HOST_MEETING


@pytest.mark.parametrize("suffix", (
    "", "\n" + HOST_MEETING, "\nSee my signature: " + HOST_MEETING,
    "\nhttps://meet.jit.si/unrelated",
))
def test_no_labeled_block_does_not_guess_from_unrelated_urls(suffix):
    message = invitation_message(meeting="") + suffix
    assert conversation_url_from_pasted_invitation(message) == ""


@pytest.mark.parametrize("url", (
    "http://host.webex.com/meet/artist",
    "https://user:password@host.webex.com/meet/artist",
    "https://host.webex.com:8443/meet/artist",
    "https://host.webex.com.evil.tld/meet/artist",
    "https://127.0.0.1/private",
    "https://192.168.1.1/private",
    "https://localhost/private",
    "https://zoom.us/j/123",
    HOST_MEETING + " https://zoom.us/j/123",
    "", "not a link",
))
def test_invalid_or_mislabeled_conversation_fails_without_echoing_url(url):
    message = invitation_message().replace(HOST_MEETING, url)
    with pytest.raises(InvitationIngressError) as caught:
        conversation_url_from_pasted_invitation(message)
    assert caught.value.code is InvitationIngressErrorCode.INVALID
    assert "copy the full invitation" in str(caught.value)
    assert "https://" not in str(caught.value) + repr(caught.value)
    assert "password" not in str(caught.value) + repr(caught.value)


def test_conflicting_labeled_blocks_are_not_resolved_by_order():
    first = invitation_message()
    second = invitation_message(meeting="https://zoom.us/j/123")
    with pytest.raises(InvitationIngressError):
        conversation_url_from_pasted_invitation(first + "\n" + second)


def test_missing_block_url_does_not_borrow_later_signature_link():
    message = invitation_message().replace(HOST_MEETING, "")
    with pytest.raises(InvitationIngressError):
        conversation_url_from_pasted_invitation(message + "\n" + HOST_MEETING)


def test_companion_extraction_is_bounded_and_requires_webjam_context():
    with pytest.raises(InvitationIngressError):
        conversation_url_from_pasted_invitation(invitation_message() + "x" * 8192)
    assert conversation_url_from_pasted_invitation(
        "Optional Webex conversation and work sharing (host.webex.com):\n" + HOST_MEETING
    ) == ""


@pytest.mark.parametrize("personal", ("", PERSONAL_MEETING))
@pytest.mark.parametrize("profile", ("art", "music"))
@pytest.mark.parametrize("native", (False, True))
def test_actual_join_retains_companion_without_saving_or_opening_it(
    qapp, monkeypatch, tmp_path, personal, profile, native,
):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"), musician_name="Artist",
        webex_url=personal,
    )
    save = Mock()
    open_url = Mock(side_effect=AssertionError("Joining must not open a meeting"))
    monkeypatch.setattr("webjam_qt.windows.launch_dialog.save_settings", save)
    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    door = LaunchDialog(settings)
    try:
        door.show_join()
        assert door.accept_invite(invitation_message(profile=profile, native=native))
        assert door.selected_role == "join"
        assert door.invitation_meeting_url == HOST_MEETING
        assert door._invite_input.text() == ""
        saved = save.call_args.args[0]
        assert saved.webex_url == personal
        assert HOST_MEETING not in repr(saved)
        open_url.assert_not_called()
        # Back/new entry and cancellation must not leak the previous host's
        # companion to a later direct invitation or another role.
        door.show_choices()
        assert door.invitation_meeting_url == ""
        assert door.accept_invitation(parse_invitation_at_ingress(
            invitation_message(meeting=""), source=InvitationSource.PASTE,
        ))
        assert door.invitation_meeting_url == ""
        door.show_choices()
        assert door.accept_invite(invitation_message())
        door.reject()
        assert door.invitation_meeting_url == ""
    finally:
        door.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("failure", ("invalid_companion", "save_error"))
def test_failed_join_has_no_retained_companion_or_private_input(
    qapp, monkeypatch, tmp_path, failure,
):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"), musician_name="Artist",
        webex_url=PERSONAL_MEETING,
    )
    save = Mock(side_effect=OSError("no disk") if failure == "save_error" else None)
    monkeypatch.setattr("webjam_qt.windows.launch_dialog.save_settings", save)
    door = LaunchDialog(settings)
    message = invitation_message()
    if failure == "invalid_companion":
        message = message.replace(HOST_MEETING, "https://localhost/private")
    try:
        assert not door.accept_invite(message)
        assert door.invitation_meeting_url == ""
        assert door._invite_input.text() == ""
        assert door._settings.webex_url == PERSONAL_MEETING
        if failure == "invalid_companion":
            save.assert_not_called()
    finally:
        door.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("native", (False, True))
def test_bootstrap_passes_accepted_conversation_separately_from_saved_settings(
    qapp, monkeypatch, tmp_path, native,
):
    from webjam_qt import app as app_module

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"), musician_name="Artist",
        webex_url=PERSONAL_MEETING,
    )
    door = LaunchDialog(settings)
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    controller = MagicMock()
    monkeypatch.setenv("WEBJAM_SMOKE_AUTOSTART_AUDIO", "0")
    monkeypatch.setattr("webjam_qt.windows.launch_dialog.save_settings", Mock())

    def accept_paste():
        assert door.accept_invite(invitation_message(native=native))
        return door.result()

    try:
        with (
            patch.object(sys, "argv", ["WebJam"]),
            patch.object(app_module, "configure_logging"),
            patch.object(app_module, "load_settings", return_value=settings),
            patch.object(door, "exec", side_effect=accept_paste),
            patch.object(app_module, "LaunchDialog", return_value=door),
            patch.object(app_module.QApplication, "instance", return_value=qt_app),
            patch.object(app_module, "load_stylesheet", return_value=""),
            patch.object(app_module, "ConductorWindow", return_value=MagicMock()),
            patch.object(app_module, "ApplicationController", return_value=controller) as constructor,
            patch.object(app_module.QTimer, "singleShot"),
        ):
            assert app_module._run_app() == 0
        kwargs = constructor.call_args.kwargs
        assert kwargs["session_meeting_url"] == HOST_MEETING
        assert kwargs["settings"].webex_url == PERSONAL_MEETING
        assert (kwargs["remote_invitation"] is not None) is native
        assert (kwargs["session_invite"] is not None) is (not native)
        assert door.invitation_meeting_url == ""
    finally:
        door.deleteLater()
        qapp.processEvents()
