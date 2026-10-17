"""Malformed private invites must leave the door ready for another paste."""

from __future__ import annotations

import traceback

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from core.network_invite import InviteLinkError, create_invite_link, parse_invite_link
from core.settings import AppSettings
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
    InvitationSource,
    parse_invitation_at_ingress,
)
from webjam_qt.windows.launch_dialog import LaunchDialog


TOKEN = "synthetic-private-invite-token-for-tests-only"
ERROR_COPY = (
    "That's not a WebJam invite. Ask your host to press Copy Invite "
    "and send you the whole message."
)


def private_link(peer_text: str = "43121") -> str:
    return create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token=TOKEN,
    ).replace("peer=43121", f"peer={peer_text}")


@pytest.mark.parametrize(
    "peer_text",
    ["9" * 5000, "0" * 5000 + "1", "65536", "0", "", "-1", "1.0", "１２３"],
    ids=["oversized", "oversized-leading-zeros", "above-range", "zero", "empty", "negative", "decimal", "unicode"],
)
def test_invalid_peer_port_raises_domain_error(peer_text):
    with pytest.raises(InviteLinkError, match="private WebJam invite link is not valid"):
        parse_invite_link(private_link(peer_text))


@pytest.mark.parametrize("peer_text", ["1", "43121", "65535", "00001"])
def test_valid_peer_port_boundaries_remain_accepted(peer_text):
    invite = parse_invite_link(private_link(peer_text))
    assert invite.peer_enabled
    assert invite.peer_port == int(peer_text)


def test_duplicate_peer_port_remains_rejected():
    with pytest.raises(InviteLinkError):
        parse_invite_link(private_link() + "&peer=43121")


@pytest.mark.parametrize("source", [InvitationSource.PASTE, InvitationSource.MAC_FILE_OPEN])
def test_oversized_peer_port_becomes_safe_ingress_error(source, caplog):
    raw = private_link("9" * 5000)
    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(raw, source=source, platform="darwin")
    assert caught.value.code is InvitationIngressErrorCode.INVALID
    assert str(caught.value) == ERROR_COPY
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    diagnostic = "".join(traceback.format_exception(caught.value)) + caplog.text
    assert TOKEN not in diagnostic
    assert raw not in diagnostic


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
@pytest.mark.parametrize("entry", ["paste-button", "accept-invite"])
def test_oversized_peer_port_restores_door_and_allows_valid_paste(
    qapp, tmp_path, caplog, profile, size, entry,
):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile,
    )
    dialog = LaunchDialog(settings)
    try:
        dialog.resize(*size)
        dialog.show_join()
        dialog.show()
        qapp.processEvents()
        # Music Host is macOS-only; Art LAN Host is available on Linux/Windows.
        # Recovery must restore that same availability, not leave submission lock.
        host_available = dialog._host_button.isEnabled()
        join_available = dialog._join_button.isEnabled()
        raw = private_link("9" * 5000)
        dialog._invite_input.setText(raw)
        if entry == "paste-button":
            dialog._join_button_primary.click()
        else:
            assert dialog.accept_invite(raw) is False
        qapp.processEvents()

        assert not dialog._submitting
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.selected_role == ""
        assert dialog.band_invite is None
        assert dialog.remote_invitation is None
        assert dialog._invite_input.isEnabled()
        assert dialog._invite_input.hasFocus()
        assert dialog._invite_input.text() == ""
        assert dialog._host_button.isEnabled() is host_available
        assert dialog._join_button.isEnabled() is join_available
        assert join_available
        assert not dialog._join_button_primary.submission_locked()
        assert dialog._join_status.text() == "That invite didn't work"
        assert dialog._join_error.isVisibleTo(dialog)
        assert dialog._join_error.text() == ERROR_COPY
        assert not (tmp_path / "settings.json").exists()

        # Back reaches the usable Host/Join door; returning permits a fresh paste.
        dialog._join_back_button.click()
        assert dialog.showing_choices
        assert dialog._host_button.isVisibleTo(dialog)
        assert dialog._host_button.isEnabled() is host_available
        dialog._join_button.click()
        assert dialog._invite_input.isVisibleTo(dialog)
        dialog._invite_input.setText(private_link())
        assert dialog._join_button_primary.isEnabled()
        dialog._join_button_primary.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.selected_role == "join"
        assert dialog.band_invite == parse_invite_link(private_link())
        assert dialog._invite_input.text() == ""
        persisted = (tmp_path / "settings.json").read_text()
        assert TOKEN not in persisted + caplog.text + dialog._join_error.text()
        assert raw not in persisted + caplog.text
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
