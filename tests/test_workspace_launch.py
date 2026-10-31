"""Pure launch-boundary cases: no Qt, settings writes, hardware or providers."""
from types import SimpleNamespace

import pytest

from core.network_invite import BandInvite
from core.remote_invitation import issue_remote_invitation
from core.workspace_launch import InvalidWorkspaceLaunch, validate_workspace_launch


@pytest.fixture
def remote():
    return issue_remote_invitation(
        "reference-local", allowed_profiles=frozenset({"reference-local"}),
        host_spki_sha256=bytes.fromhex("44" * 32), issued_at_unix=1_800_000_000,
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    ).invitation


@pytest.mark.parametrize("role", ["host", "studio", "library"])
def test_explicit_non_join_choice_needs_no_invitation(role):
    validate_workspace_launch(SimpleNamespace(selected_role=role))


def test_join_accepts_one_typed_lan_invitation():
    validate_workspace_launch(SimpleNamespace(
        selected_role="join", band_invite=BandInvite("192.168.1.42"),
        remote_invitation=None,
    ))


def test_join_accepts_one_typed_remote_invitation_without_consuming_it(remote):
    launch = SimpleNamespace(selected_role="join", band_invite=None, remote_invitation=remote)
    validate_workspace_launch(launch)
    assert launch.remote_invitation is remote


@pytest.mark.parametrize("role", [None, "", "unexpected", "JOIN", 1, ["host"]])
def test_dialog_acceptance_without_an_explicit_role_is_not_authority(role):
    with pytest.raises(InvalidWorkspaceLaunch):
        validate_workspace_launch(SimpleNamespace(selected_role=role))


@pytest.mark.parametrize("band,remote", [(None, None), ("PRIVATE-SENTINEL", None), (None, "PRIVATE-SENTINEL")])
def test_join_requires_a_typed_invitation_and_errors_never_echo_input(band, remote):
    with pytest.raises(InvalidWorkspaceLaunch) as caught:
        validate_workspace_launch(SimpleNamespace(selected_role="join", band_invite=band, remote_invitation=remote))
    assert str(caught.value) == "Choose Host, a valid Join invitation, or saved work before continuing."
    assert "PRIVATE-SENTINEL" not in str(caught.value)


def test_join_refuses_two_invitations_or_wrong_slots(remote):
    band = BandInvite("192.168.1.42")
    for first, second in [(band, remote), (remote, None), (None, band)]:
        with pytest.raises(InvalidWorkspaceLaunch):
            validate_workspace_launch(SimpleNamespace(selected_role="join", band_invite=first, remote_invitation=second))
