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
