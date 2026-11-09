"""Keep an unattended malformed-invite check from starting a valid legacy join."""
import pytest

from core.network_invite import BandInvite
from tools.check_native_join_recovery import (
    CheckStopped,
    LaunchControls,
    MALFORMED_INVITATION,
)
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationSource,
    parse_invitation_at_ingress,
)


def test_gui_fixture_is_rejected_by_the_real_invitation_ingress():
    with pytest.raises(InvitationIngressError):
        parse_invitation_at_ingress(MALFORMED_INVITATION, source=InvitationSource.PASTE)


def test_dns_shaped_dummy_is_a_valid_legacy_join_not_an_invalid_fixture():
    invitation = parse_invitation_at_ingress(
        "webjam-private-qa-invalid-invitation", source=InvitationSource.PASTE,
    )
    assert isinstance(invitation, BandInvite)


@pytest.mark.parametrize("action", ["Host", "Start Session", "Try Again", "Play"])
def test_gui_check_refuses_media_actions_before_using_accessibility(action, monkeypatch):
    controls = LaunchControls(123)

    def unexpected_accessibility(_):
        pytest.fail("forbidden action reached accessibility")

    monkeypatch.setattr(controls, "_execute", unexpected_accessibility)
    with pytest.raises(CheckStopped, match="media-or-unrelated-action-forbidden"):
        controls.press(action)
