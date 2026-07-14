"""Contract tests for WebJam's pinned Jamulus 3.12.2 audio adapter."""

from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
from xml.etree import ElementTree

import pytest

from core.audio_route_profile import (
    AudioRoutePlatform,
    AudioRouteProfile,
    Jamulus3122AudioRouteAdapter,
    JamulusChannelMode,
    RouteConfirmationLevel,
)


def _mac_profile(**changes: object) -> AudioRouteProfile:
    values: dict[str, object] = {
        "platform": AudioRoutePlatform.MACOS_COREAUDIO,
        "input_device_id": "AppleUSBAudioEngine:Focusrite:Scarlett:input-uid",
        "output_device_id": "AppleUSBAudioEngine:Focusrite:Scarlett:output-uid",
        "input_device_name": "Scarlett 4i4 USB",
        "output_device_name": "Scarlett 4i4 USB",
        "input_channels": (2, 3),
        "output_channels": (0, 1),
        "channel_mode": JamulusChannelMode.MONO_IN_STEREO_OUT,
        "device_generation": "coreaudio-registry-17",
        "app_version": "0.11.0",
        "jamulus_binary_sha256": "a" * 64,
    }
    values.update(changes)
    return AudioRouteProfile(**values)  # type: ignore[arg-type]


def _windows_profile(**changes: object) -> AudioRouteProfile:
    values: dict[str, object] = {
        "platform": AudioRoutePlatform.WINDOWS_ASIO,
        "input_device_id": "HKLM\\ASIO\\Focusrite USB ASIO",
        "output_device_id": "HKLM\\ASIO\\Focusrite USB ASIO",
        "input_device_name": "Focusrite USB ASIO",
        "output_device_name": "Focusrite USB ASIO",
        "device_generation": "asio-registry-4",
        "app_version": "0.11.0",
    }
    values.update(changes)
    return AudioRouteProfile(**values)  # type: ignore[arg-type]


def _linux_profile(**changes: object) -> AudioRouteProfile:
    values: dict[str, object] = {
        "platform": AudioRoutePlatform.LINUX_JACK,
        "input_device_id": "pipewire:alsa_card.usb-focusrite",
        "output_device_id": "pipewire:alsa_card.usb-focusrite",
        "input_device_name": "Focusrite capture",
        "output_device_name": "Focusrite playback",
        "device_generation": "pipewire-graph-28",
        "app_version": "0.11.0",
        "requested_buffer_frames": 128,
        "jack_server": "webjam-session-73",
        "jack_input_ports": ("webjam_source:out_1", "webjam_source:out_2"),
        "jack_output_ports": ("webjam_sink:in_1", "webjam_sink:in_2"),
    }
    values.update(changes)
    return AudioRouteProfile(**values)  # type: ignore[arg-type]


def _settings(payload: bytes) -> dict[str, str]:
    root = ElementTree.fromstring(payload)
    return {child.tag: child.text or "" for child in root}


def test_profile_is_frozen_and_fingerprint_tracks_only_route_truth() -> None:
    profile = _mac_profile()
    with pytest.raises(FrozenInstanceError):
        profile.input_device_id = "changed"  # type: ignore[misc]

    fingerprint = profile.invalidation_fingerprint()
    confirmed = profile.with_confirmation(
        RouteConfirmationLevel.PREFLIGHTED,
        method="coreaudio_uid_and_format_probe",
        verified_at="2026-07-13T01:02:03Z",
    )
    invalidated = confirmed.invalidate("device registry changed")

    assert confirmed.invalidation_fingerprint() == fingerprint
    assert invalidated.invalidation_fingerprint() == fingerprint
    assert replace(profile, input_channels=(0, 1)).invalidation_fingerprint() != fingerprint
    assert invalidated.is_valid is False
    assert invalidated.confirmation_level is RouteConfirmationLevel.CONFIGURED


def test_confirmation_is_monotonic_and_never_fakes_coreaudio_graph_evidence() -> None:
    profile = _mac_profile().with_confirmation(
        RouteConfirmationLevel.PREFLIGHTED,
        method="coreaudio_uid_and_format_probe",
        verified_at="2026-07-13T01:02:03Z",
    )

    with pytest.raises(ValueError, match="downgraded"):
        profile.with_confirmation(
            RouteConfirmationLevel.CONFIGURED,
            method="settings",
            verified_at="2026-07-13T01:03:00Z",
        )
    with pytest.raises(ValueError, match="cannot graph-confirm"):
        _mac_profile(
            confirmation_level=RouteConfirmationLevel.GRAPH_CONFIRMED,
            last_verified_at="2026-07-13T01:02:03Z",
        )

    heard = profile.with_confirmation(
        RouteConfirmationLevel.MUSICIAN_CONFIRMED,
        method="musician_heard_round_trip",
        verified_at="2026-07-13T01:04:00Z",
    )
    assert heard.confirmation_level is RouteConfirmationLevel.MUSICIAN_CONFIRMED


def test_pinned_version_sample_rate_and_hash_are_strict() -> None:
    with pytest.raises(ValueError, match="3.12.2 exactly"):
        _mac_profile(jamulus_version="3.12.3")
    with pytest.raises(ValueError, match="48000 Hz"):
        _mac_profile(sample_rate=44_100)
    with pytest.raises(ValueError, match="64-character"):
        _mac_profile(jamulus_binary_sha256="not-a-digest")


def test_channel_mode_reports_distinct_send_and_return_widths() -> None:
    mono = _mac_profile(channel_mode=JamulusChannelMode.MONO)
    split = _mac_profile(channel_mode=JamulusChannelMode.MONO_IN_STEREO_OUT)
    stereo = _mac_profile(channel_mode=JamulusChannelMode.STEREO)

    assert (mono.send_channel_count, mono.return_channel_count) == (1, 1)
    assert (split.send_channel_count, split.return_channel_count) == (1, 2)
    assert (stereo.send_channel_count, stereo.return_channel_count) == (2, 2)


def test_coreaudio_uses_display_pair_only_as_selector_and_writes_exact_maps() -> None:
    adapter = Jamulus3122AudioRouteAdapter()
    profile = _mac_profile(requested_buffer_frames=128)
    settings = _settings(adapter.render_inifile(profile, musician_name="Jeff Story"))

    assert base64.b64decode(settings["auddev_base64"]).decode() == (
        "in: Scarlett 4i4 USB/out: Scarlett 4i4 USB"
    )
    assert settings["sndcrdinlch"] == "2"
    assert settings["sndcrdinrch"] == "3"
    assert settings["sndcrdoutlch"] == "0"
    assert settings["sndcrdoutrch"] == "1"
    assert settings["prefsndcrdbufidx"] == "2"
    assert settings["audiochannels"] == "1"
    assert "samplerate" not in settings
    assert profile.input_device_id not in adapter.render_inifile(profile).decode()

    with pytest.raises(ValueError, match="cannot form a Jamulus selector"):
        _mac_profile(input_device_name="Interface/Input")


def test_macos_launch_uses_only_the_owned_inifile_name() -> None:
    adapter = Jamulus3122AudioRouteAdapter()

    assert adapter.launch_arguments(
        _mac_profile(),
        Path("/Users/tester/Library/Containers/app.jamulussoftware.Jamulus/Data/.config/Jamulus/WebJam-route-v1.ini"),
    ) == ("--inifile", "WebJam-route-v1.ini")


def test_windows_requires_one_asio_identity_and_uses_its_driver_name() -> None:
    adapter = Jamulus3122AudioRouteAdapter()
    profile = _windows_profile()

    assert adapter.jamulus_device_selector(profile) == "Focusrite USB ASIO"
    with pytest.raises(ValueError, match="one driver"):
        _windows_profile(output_device_id="HKLM\\ASIO\\Other")
    with pytest.raises(ValueError, match="names must match"):
        _windows_profile(output_device_name="Other ASIO")


def test_linux_owns_launch_environment_and_exact_jack_graph() -> None:
    adapter = Jamulus3122AudioRouteAdapter()
    profile = _linux_profile(
        confirmation_level=RouteConfirmationLevel.GRAPH_CONFIRMED,
        last_verified_at="2026-07-13T01:02:03Z",
        verification_method="jack_graph_enumeration",
    )

    assert adapter.launch_arguments(profile, Path("/tmp/client.xml")) == (
        "--inifile",
        "/tmp/client.xml",
        "--nojackconnect",
    )
    assert adapter.environment_overrides(profile) == {
        "JACK_DEFAULT_SERVER": "webjam-session-73"
    }
    assert adapter.jack_connections(profile, client_name="WebJamJeff") == (
        ("webjam_source:out_1", "Jamulus WebJamJeff:input left"),
        ("webjam_source:out_2", "Jamulus WebJamJeff:input right"),
        ("Jamulus WebJamJeff:output left", "webjam_sink:in_1"),
        ("Jamulus WebJamJeff:output right", "webjam_sink:in_2"),
    )
    settings = _settings(adapter.render_inifile(profile))
    assert "auddev_base64" not in settings
    assert "prefsndcrdbufidx" not in settings


def test_adapter_declares_live_send_mute_unsupported() -> None:
    assert Jamulus3122AudioRouteAdapter.live_send_mute is False


def test_provision_is_atomic_protected_and_restorable(tmp_path: Path) -> None:
    adapter = Jamulus3122AudioRouteAdapter()
    target = tmp_path / "owned" / "jamulus-client.xml"
    target.parent.mkdir()
    target.write_bytes(b"old config\n")
    target.chmod(0o644)

    provisioned = adapter.provision(_mac_profile(), target, musician_name="Jeff")

    assert provisioned.previous_existed is True
    assert provisioned.backup_path == target.with_name(target.name + ".bak")
    assert provisioned.backup_path.read_bytes() == b"old config\n"
    assert target.read_bytes().startswith(b"<?xml")
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert os.stat(provisioned.backup_path).st_mode & 0o777 == 0o600
    adapter.restore_backup(provisioned)
    assert target.read_bytes() == b"old config\n"
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_provision_rolls_back_if_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = Jamulus3122AudioRouteAdapter()
    target = tmp_path / "client.xml"
    target.write_bytes(b"known-good\n")
    original_atomic_write = adapter._atomic_write
    rendered = adapter.render_inifile(_mac_profile())

    def fail_after_replace(path: Path, payload: bytes) -> None:
        original_atomic_write(path, payload)
        if path == target and payload == rendered:
            raise OSError("simulated fsync failure")

    monkeypatch.setattr(adapter, "_atomic_write", fail_after_replace)
    with pytest.raises(OSError, match="simulated"):
        adapter.provision(_mac_profile(), target)

    assert target.read_bytes() == b"known-good\n"


def test_provision_rejects_symlink_target(tmp_path: Path) -> None:
    adapter = Jamulus3122AudioRouteAdapter()
    real = tmp_path / "outside.xml"
    real.write_text("leave me alone", encoding="utf-8")
    target = tmp_path / "client.xml"
    target.symlink_to(real)

    with pytest.raises(ValueError, match="symlink"):
        adapter.provision(_mac_profile(), target)

    assert real.read_text(encoding="utf-8") == "leave me alone"
