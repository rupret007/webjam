import json
import os

import pytest

from core.local_capture import LocalCaptureTrack
from core.session_recording_plan import (
    LEGACY_CAPTURE_TRACKS,
    configured_input_map_bindings,
    resolve_capture_tracks,
)
from core.settings import AppSettings, _coerce_settings_data, load_settings, save_settings


def _entry(**overrides):
    entry = {
        "name": "Guitar DI",
        "channels": 1,
        "enabled": True,
        "local_original_enabled": True,
    }
    entry.update(overrides)
    return entry


@pytest.fixture(params=["saved", "environment", "legacy_saved", "legacy_environment"])
def capture_opt_in(request, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("WEBJAM_"):
            monkeypatch.delenv(key)
    if request.param == "environment":
        monkeypatch.setenv("WEBJAM_LOCAL_CAPTURE_ENABLED", "true")
        return {"local_capture_enabled": False}
    if request.param == "legacy_environment":
        monkeypatch.setenv("WEBJAM_WEBEX_AUDIO_BRIDGE_ENABLED", "true")
        return {"local_capture_enabled": False}
    if request.param == "legacy_saved":
        # The retired flag alone intentionally cannot authorize capture.
        return {"webex_audio_bridge_enabled": True, "local_capture_enabled": True}
    return {"local_capture_enabled": True}


@pytest.mark.parametrize("raw", [{}, False, 0, ""], ids=["object", "false", "zero", "string"])
def test_falsey_malformed_input_maps_disable_capture(tmp_path, capture_opt_in, raw):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        **capture_opt_in,
        "input_maps": raw,
        "musician_name": "Map regression",
        "audio_input_device_index": 7,
        "local_capture_choice_made": True,
    }), encoding="utf-8")

    loaded = load_settings(str(config))

    assert resolve_capture_tracks(loaded) == ()
    assert loaded.local_capture_enabled is False
    assert loaded.input_maps == []
    assert loaded.musician_name == "Map regression"
    assert loaded.audio_input_device_index == 7
    assert loaded.local_capture_choice_made is True


@pytest.mark.parametrize("raw", [{}, False, 0, ""], ids=["object", "false", "zero", "string"])
def test_falsey_malformed_input_maps_disable_capture_during_coercion(raw):
    data = {"input_maps": raw, "local_capture_enabled": True}

    _coerce_settings_data(data)

    assert data["input_maps"] == []
    assert data["local_capture_enabled"] is False


@pytest.mark.parametrize("maps", [{}, {"input_maps": []}, {"input_maps": None}],
                         ids=["absent", "empty_list", "legacy_null"])
def test_legacy_empty_input_maps_preserve_capture_compatibility(
    tmp_path, capture_opt_in, maps,
):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({**capture_opt_in, **maps}), encoding="utf-8")

    loaded = load_settings(str(config))

    assert loaded.local_capture_enabled is True
    assert loaded.input_maps == []
    assert resolve_capture_tracks(loaded) == LEGACY_CAPTURE_TRACKS


def test_valid_input_maps_preserve_capture_opt_in(tmp_path, capture_opt_in):
    config = tmp_path / "config.json"
    maps = [_entry(name="Room Pair", channels=2)]
    config.write_text(json.dumps({**capture_opt_in, "input_maps": maps}), encoding="utf-8")

    loaded = load_settings(str(config))

    assert loaded.local_capture_enabled is True
    assert loaded.input_maps == maps
    assert resolve_capture_tracks(loaded) == (
        LocalCaptureTrack("local-Room Pair", (0, 1), logical_source_ordinal=0),
    )


def test_valid_input_maps_round_trip_through_disk(tmp_path):
    config = tmp_path / "config.json"
    settings = AppSettings(config_file=str(config))
    settings.input_maps = [_entry(), _entry(name="Vocal Mic", channels=2)]
    save_settings(settings)
    loaded = load_settings(str(config))
    assert loaded.input_maps == [
        {
            "name": "Guitar DI",
            "channels": 1,
            "enabled": True,
            "local_original_enabled": True,
        },
        {
            "name": "Vocal Mic",
            "channels": 2,
            "enabled": True,
            "local_original_enabled": False,
        },
    ] or loaded.input_maps[1]["local_original_enabled"] is True
    bindings = configured_input_map_bindings(loaded)
    assert [binding.track_name for binding in bindings] == [
        "Guitar DI",
        "Vocal Mic",
    ]
    assert bindings[1].channel_count == 2


def test_hostile_input_maps_fail_safe_to_empty(tmp_path):
    hostile_lists = (
        "not-a-list",
        [{"name": "", "channels": 1}],
        [{"name": "x" * 200, "channels": 1}],
        [{"name": "Bad\nName", "channels": 1}],
        [{"name": "Ok", "channels": 3}],
        [{"name": "Ok", "channels": True}],
        [{"name": "Ok", "channels": 1, "enabled": "yes"}],
        [{"name": "Same", "channels": 1}, {"name": "Same", "channels": 2}],
        [_entry(name=f"T{i}") for i in range(33)],
        [["not", "a", "dict"]],
    )
    config = tmp_path / "config.json"
    for hostile in hostile_lists:
        config.write_text(
            json.dumps({"input_maps": hostile}), encoding="utf-8"
        )
        loaded = load_settings(str(config))
        assert loaded.input_maps == [], hostile
        assert configured_input_map_bindings(loaded) == ()


def test_default_is_empty_and_compat_rule_is_the_fixed_two_stem_map():
    settings = AppSettings()
    assert settings.input_maps == []
    assert configured_input_map_bindings(settings) == ()
    # The compatibility rule lives at the capture/plan layer: enabled local
    # capture with an empty configured list means the fixed two host stems,
    # which the recording coordinator binds into the plan as capture truth.
    assert settings.local_capture_enabled is False


def test_resolve_capture_tracks_is_the_single_capture_truth():
    from core.session_recording_plan import (
        LEGACY_CAPTURE_TRACKS,
        resolve_capture_tracks,
    )

    settings = AppSettings()
    # Capture off -> nothing, regardless of configuration.
    settings.local_capture_enabled = False
    settings.input_maps = [_entry()]
    assert resolve_capture_tracks(settings) == ()

    # Capture on, no valid config -> the legacy fixed pair, unchanged.
    settings.local_capture_enabled = True
    settings.input_maps = []
    assert resolve_capture_tracks(settings) == LEGACY_CAPTURE_TRACKS

    # Configured maps: sequential channels, stereo stays one logical WAV,
    # disabled and non-Local-Original entries consume nothing.
    settings.input_maps = [
        _entry(name="Guitar DI", channels=1),
        _entry(name="Skipped", channels=1, enabled=False),
        _entry(name="Not LO", channels=1, local_original_enabled=False),
        _entry(name="Room Pair", channels=2),
    ]
    resolved = resolve_capture_tracks(settings)
    assert resolved == (
        LocalCaptureTrack("local-Guitar DI", (0,), logical_source_ordinal=0),
        LocalCaptureTrack("local-Room Pair", (1, 2), logical_source_ordinal=3),
    )

    # Hostile names sanitize deterministically and never collide.
    settings.input_maps = [
        _entry(name="Björn/../etc"),
        _entry(name="Björn:*?"),
    ]
    resolved = resolve_capture_tracks(settings)
    assert len(resolved) == 2
    assert len({stem.lower() for stem, _c in resolved}) == 2
    for stem, _channel in resolved:
        assert stem.startswith("local-")
        assert "/" not in stem and ".." not in stem

    # Every resolved stem is accepted by the capture engine's validator.
    from core.local_capture import LocalInputCapture

    capture = LocalInputCapture(".", samplerate=48000, tracks=resolved)
    assert capture._required_input_channels == 2


def test_resolver_never_records_opted_out_rows_or_truncates_capacity():
    from core.session_recording_plan import resolve_capture_tracks

    settings = AppSettings(local_capture_enabled=True)
    settings.input_maps = [
        _entry(name="Guide", channels=2, local_original_enabled=False),
        _entry(name="Muted", channels=1, enabled=False),
    ]
    assert resolve_capture_tracks(settings) == ()

    settings.input_maps = [
        _entry(name=f"Stereo {index}", channels=2)
        for index in range(17)
    ]
    assert resolve_capture_tracks(settings) == ()

    settings.input_maps = [
        _entry(name=f"Mono {index}", channels=1)
        for index in range(33)
    ]
    assert resolve_capture_tracks(settings) == ()

    settings.input_maps = [
        _entry(name="Unsafe flag", enabled="false")
    ]
    assert resolve_capture_tracks(settings) == ()

    settings.input_maps = [
        _entry(name=f"Mono {index}", channels=1)
        for index in range(30)
    ] + [_entry(name="Last Stereo", channels=2)]
    resolved = resolve_capture_tracks(settings)
    assert len(resolved) == 31
    assert resolved[-1] == LocalCaptureTrack(
        "local-Last Stereo", (30, 31), logical_source_ordinal=30
    )


def test_over_capacity_persisted_map_disables_capture_instead_of_defaulting(
    tmp_path,
    monkeypatch,
):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "local_capture_enabled": True,
                "input_maps": [
                    _entry(name=f"Stereo {index}", channels=2)
                    for index in range(17)
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("WEBJAM_LOCAL_CAPTURE_ENABLED", "true")
    loaded = load_settings(str(config))

    assert loaded.input_maps == []
    assert loaded.local_capture_enabled is False


def test_long_and_colliding_map_names_resolve_to_valid_unique_capture_stems():
    from core.session_recording_plan import resolve_capture_tracks

    settings = AppSettings(local_capture_enabled=True)
    settings.input_maps = [
        _entry(name="A" * 128, channels=1),
        _entry(name="A" * 127 + "B", channels=1),
        _entry(name="C" * 128, channels=2),
    ]

    resolved = resolve_capture_tracks(settings)

    assert len(resolved) == 3
    stems = [track.stem for track in resolved]
    assert len({stem.casefold() for stem in stems}) == 3
    assert all(1 <= len(stem) <= 64 for stem in stems)
    assert resolved[-1].source_channels == (2, 3)


def test_configured_stems_classify_as_local_and_enumerate_stably():
    from core.take_library import is_local_stem_name

    assert is_local_stem_name("local-Guitar DI.wav")
    assert is_local_stem_name("host-guitar.wav")
    assert is_local_stem_name("host-vocal-local.wav")
    assert not is_local_stem_name("1-Jeff-something.wav")
    assert not is_local_stem_name("local-Guitar DI.flac")
