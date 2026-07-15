"""Mac Jamulus route preparation is safe before a real client is spawned."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from core.coreaudio_devices import CoreAudioDevice, CoreAudioScan
from core.macos_audio_route import (
    WEBJAM_ROUTE_INIFILE,
    JamulusAudioRouteError,
    MacOSJamulusRouteManager,
    _default_version_probe,
    jamulus_macos_config_directory,
)
from core.settings import AppSettings


def _device(
    uid: str,
    name: str,
    object_id: int,
    *,
    inputs: int = 2,
    outputs: int = 2,
    rate: float | None = 48_000.0,
) -> CoreAudioDevice:
    return CoreAudioDevice(
        uid=uid,
        name=name,
        object_id=object_id,
        input_channels=inputs,
        output_channels=outputs,
        nominal_rate=rate,
    )


def _manager(
    scan: CoreAudioScan,
    tmp_path: Path,
) -> MacOSJamulusRouteManager:
    return MacOSJamulusRouteManager(
        scanner=lambda: scan,
        home=tmp_path,
        version_probe=lambda _binary: "3.12.2",
    )


def _xml_settings(path: Path) -> dict[str, str]:
    root = ElementTree.fromstring(path.read_bytes())
    return {element.tag: element.text or "" for element in root}


def test_default_version_probe_parses_official_jamulus_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundled 3.12.2 client prints this banner for ``--version``."""

    monkeypatch.setattr(
        "core.macos_audio_route.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=" *** Jamulus, Version 3.12.2\n",
            stderr="",
        ),
    )

    assert _default_version_probe("/bundle/Jamulus") == "3.12.2"


def test_prepare_uses_defaults_writes_owned_config_and_keeps_name_neutral(
    tmp_path: Path,
) -> None:
    interface = _device("input-uid", "Band Interface", 10, outputs=0)
    speakers = _device("output-uid", "Band Speakers", 11, inputs=0)
    scan = CoreAudioScan(
        devices=(interface, speakers),
        default_input_uid=interface.uid,
        default_output_uid=speakers.uid,
    )

    plan = _manager(scan, tmp_path).prepare(
        AppSettings(musician_name="Private Musician Name"), "/Applications/Jamulus"
    )

    expected_dir = jamulus_macos_config_directory(tmp_path)
    assert plan.working_directory == expected_dir
    assert plan.provisioned.path == expected_dir / WEBJAM_ROUTE_INIFILE
    assert plan.arguments == ("--inifile", WEBJAM_ROUTE_INIFILE)
    assert plan.profile.input_device_id == "input-uid"
    assert plan.profile.output_device_id == "output-uid"
    assert plan.profile.confirmation_level.value == "preflighted"
    settings = _xml_settings(plan.provisioned.path)
    assert base64.b64decode(settings["name_base64"]).decode("utf-8") == "WebJam Musician"
    assert "Private Musician Name" not in plan.provisioned.path.read_text("utf-8")
    assert plan.provisioned.path.stat().st_mode & 0o777 == 0o600


def test_prepare_honors_explicit_stable_uids_instead_of_enumeration_order(
    tmp_path: Path,
) -> None:
    first = _device("first", "First Interface", 10)
    selected = _device("selected", "Selected Interface", 99)
    scan = CoreAudioScan(
        devices=(selected, first),
        default_input_uid=first.uid,
        default_output_uid=first.uid,
    )
    settings = AppSettings(
        jamulus_audio_input_uid=selected.uid,
        jamulus_audio_output_uid=selected.uid,
    )

    plan = _manager(scan, tmp_path).prepare(settings, "/Applications/Jamulus")

    assert plan.profile.input_device_id == selected.uid
    assert plan.profile.output_device_id == selected.uid


def test_prepare_rejects_unplugged_explicit_device_without_default_fallback(
    tmp_path: Path,
) -> None:
    present = _device("present", "Present Interface", 10)
    scan = CoreAudioScan(
        devices=(present,),
        default_input_uid=present.uid,
        default_output_uid=present.uid,
    )
    settings = AppSettings(
        jamulus_audio_input_uid="unplugged",
        jamulus_audio_output_uid=present.uid,
    )

    with pytest.raises(JamulusAudioRouteError, match="no longer connected"):
        _manager(scan, tmp_path).prepare(settings, "/Applications/Jamulus")
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_prepare_rejects_ambiguous_jamulus_display_name_before_writing(
    tmp_path: Path,
) -> None:
    selected = _device("one", "USB Audio", 10)
    duplicate = _device("two", "USB Audio", 11)
    scan = CoreAudioScan(
        devices=(selected, duplicate),
        default_input_uid=selected.uid,
        default_output_uid=selected.uid,
    )

    with pytest.raises(JamulusAudioRouteError, match="same name"):
        _manager(scan, tmp_path).prepare(AppSettings(), "/Applications/Jamulus")
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_prepare_rejects_wrong_pinned_music_engine_before_writing(
    tmp_path: Path,
) -> None:
    device = _device("interface", "Band Interface", 10)
    scan = CoreAudioScan(
        devices=(device,),
        default_input_uid=device.uid,
        default_output_uid=device.uid,
    )
    manager = MacOSJamulusRouteManager(
        scanner=lambda: scan,
        home=tmp_path,
        version_probe=lambda _binary: "3.12.3",
    )

    with pytest.raises(JamulusAudioRouteError, match="3.12.2"):
        manager.prepare(AppSettings(), "/Applications/Jamulus")
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_prepare_fails_closed_when_coreaudio_reports_an_untrusted_scan(
    tmp_path: Path,
) -> None:
    device = _device("interface", "Band Interface", 10)
    scan = CoreAudioScan(
        devices=(device,),
        default_input_uid=device.uid,
        default_output_uid=device.uid,
        error="temporary CoreAudio failure",
    )

    with pytest.raises(JamulusAudioRouteError, match="couldn't inspect"):
        _manager(scan, tmp_path).prepare(AppSettings(), "/Applications/Jamulus")
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_reconnect_rejects_changed_device_instead_of_silently_switching(
    tmp_path: Path,
) -> None:
    original = _device("interface", "Band Interface", 10)
    first_scan = CoreAudioScan(
        devices=(original,),
        default_input_uid=original.uid,
        default_output_uid=original.uid,
    )
    current = {"scan": first_scan}
    manager = MacOSJamulusRouteManager(
        scanner=lambda: current["scan"],
        home=tmp_path,
        version_probe=lambda _binary: "3.12.2",
    )
    plan = manager.prepare(AppSettings(), "/Applications/Jamulus")
    current["scan"] = CoreAudioScan(
        devices=(_device("interface", "Renamed Interface", 42),),
        default_input_uid="interface",
        default_output_uid="interface",
    )

    with pytest.raises(JamulusAudioRouteError, match="device changed"):
        manager.validate_active(plan)
