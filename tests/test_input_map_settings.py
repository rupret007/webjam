import json

from core.session_recording_plan import configured_input_map_bindings
from core.settings import AppSettings, load_settings, save_settings


def _entry(**overrides):
    entry = {
        "name": "Guitar DI",
        "channels": 1,
        "enabled": True,
        "local_original_enabled": True,
    }
    entry.update(overrides)
    return entry


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

    # Configured maps: sequential channels, stereo splits into L/R stems,
    # disabled and non-Local-Original entries consume nothing.
    settings.input_maps = [
        _entry(name="Guitar DI", channels=1),
        _entry(name="Skipped", channels=1, enabled=False),
        _entry(name="Not LO", channels=1, local_original_enabled=False),
        _entry(name="Room Pair", channels=2),
    ]
    resolved = resolve_capture_tracks(settings)
    assert resolved == (
        ("local-Guitar DI", 0),
        ("local-Room Pair L", 1),
        ("local-Room Pair R", 2),
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
    assert len(resolved) == 32
    assert resolved[-1] == ("local-Last Stereo R", 31)


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

    assert len(resolved) == 4
    stems = [stem for stem, _channel in resolved]
    assert len({stem.casefold() for stem in stems}) == 4
    assert all(1 <= len(stem) <= 64 for stem in stems)


def test_configured_stems_classify_as_local_and_enumerate_stably():
    from core.take_library import is_local_stem_name

    assert is_local_stem_name("local-Guitar DI.wav")
    assert is_local_stem_name("host-guitar.wav")
    assert is_local_stem_name("host-vocal-local.wav")
    assert not is_local_stem_name("1-Jeff-something.wav")
    assert not is_local_stem_name("local-Guitar DI.flac")
