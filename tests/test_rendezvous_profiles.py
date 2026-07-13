from __future__ import annotations

import pytest

from core.rendezvous_profiles import (
    DEFAULT_RENDEZVOUS_PROFILES,
    REFERENCE_LOCAL_PROFILE,
    RendezvousProfile,
    RendezvousProfileError,
    RendezvousProfileRegistry,
)


def test_default_registry_contains_only_the_loopback_reference_profile() -> None:
    assert DEFAULT_RENDEZVOUS_PROFILES.profile_ids == {"reference-local"}
    assert DEFAULT_RENDEZVOUS_PROFILES.resolve("reference-local") is (
        REFERENCE_LOCAL_PROFILE
    )
    assert REFERENCE_LOCAL_PROFILE.lab_only is True
    assert "127.0.0.1" not in repr(REFERENCE_LOCAL_PROFILE)


def test_public_profile_requires_fixed_tls_control_and_dns_service_names() -> None:
    profile = RendezvousProfile(
        profile_id="pilot-us",
        control_host="rendezvous.example.test",
        control_port=443,
        control_tls=True,
        relay_host="relay.example.test",
        relay_port=47132,
    )

    assert profile.profile_id == "pilot-us"
    assert profile.control_host == "rendezvous.example.test"
    assert profile.control_tls is True


@pytest.mark.parametrize(
    "changes",
    [
        {"control_tls": False},
        {"control_host": "https://service.example.test"},
        {"control_host": "user@service.example.test"},
        {"control_host": "127.0.0.1"},
        {"control_port": 0},
        {"control_port": True},
        {"relay_host": "localhost"},
        {"relay_host": "192.0.2.2"},
        {"relay_port": 99999},
        {"profile_id": "Pilot-US"},
        {"profile_id": "pіlot-us"},  # Cyrillic i
        {"profile_id": "https://service.example.test"},
    ],
)
def test_public_profile_rejects_insecure_or_ambiguous_endpoints(changes) -> None:
    values = {
        "profile_id": "pilot-us",
        "control_host": "service.example.test",
        "control_port": 443,
        "control_tls": True,
        "relay_host": "relay.example.test",
        "relay_port": 47132,
        **changes,
    }
    with pytest.raises(RendezvousProfileError):
        RendezvousProfile(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"control_host": "192.168.1.2"},
        {"control_host": "localhost"},
        {"relay_host": "10.0.0.1"},
        {"relay_host": "relay.example.test"},
    ],
)
def test_lab_profile_cannot_escape_loopback(changes) -> None:
    values = {
        "profile_id": "lab-test",
        "control_host": "127.0.0.1",
        "control_port": 47131,
        "control_tls": False,
        "relay_host": "127.0.0.1",
        "relay_port": 47132,
        "lab_only": True,
        **changes,
    }
    with pytest.raises(RendezvousProfileError):
        RendezvousProfile(**values)


def test_registry_uses_exact_ids_and_rejects_duplicates() -> None:
    registry = RendezvousProfileRegistry((REFERENCE_LOCAL_PROFILE,))
    for candidate in (
        "REFERENCE-LOCAL",
        "reference_local",
        " reference-local",
        "référence-local",
        "https://127.0.0.1:47131",
    ):
        with pytest.raises(RendezvousProfileError):
            registry.resolve(candidate)
    with pytest.raises(RendezvousProfileError, match="unique"):
        RendezvousProfileRegistry(
            (REFERENCE_LOCAL_PROFILE, REFERENCE_LOCAL_PROFILE)
        )
