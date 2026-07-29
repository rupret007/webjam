from __future__ import annotations

import base64
import json
from xml.etree import ElementTree

import pytest

from core.audio_route_profile import (
    AudioRoutePlatform,
    AudioRouteProfile,
    Jamulus3122AudioRouteAdapter,
)
from core.jamulus_name import (
    DEFAULT_JAMULUS_NAME,
    JAMULUS_NAME_HELP,
    JamulusNameError,
    grapheme_clusters,
    jamulus_name_contract,
    recover_jamulus_name,
    utf16_units,
    validate_jamulus_name,
)
from core.settings import AppSettings, load_settings, save_settings


def _linux_profile() -> AudioRouteProfile:
    return AudioRouteProfile(
        platform=AudioRoutePlatform.LINUX_JACK,
        input_device_id="system:capture",
        output_device_id="system:playback",
        input_device_name="system",
        output_device_name="system",
        requested_buffer_frames=128,
        adapter_version="jamulus-3.12.2-route-v1",
        jack_server="webjam-name-test",
        jack_input_ports=("source:left", "source:right"),
        jack_output_ports=("sink:left", "sink:right"),
    )


@pytest.mark.parametrize("version", ("3.12.2", "3.12.3", "r3_12_3"))
def test_contract_is_version_scoped(version):
    contract = jamulus_name_contract(version)
    assert contract.max_utf16_units == 16
    assert contract.mixer_wrap_graphemes == 8


def test_unverified_version_fails_closed():
    with pytest.raises(JamulusNameError, match="not verified"):
        validate_jamulus_name("Jeff", version="3.13.0")


@pytest.mark.parametrize(
    ("value", "first", "second"),
    (
        ("12345678", "12345678", ""),
        ("123456789", "12345678", "9"),
        ("1234567890123456", "12345678", "90123456"),
        ("Jeff Story", "Jeff Sto", "ry"),
    ),
)
def test_native_mixer_preview_wraps_at_eight_graphemes(value, first, second):
    result = validate_jamulus_name(value)
    assert result.first_line == first
    assert result.second_line == second
    assert result.preview == first + (f"\n{second}" if second else "")


def test_seventeen_ascii_utf16_units_are_rejected_without_truncation():
    with pytest.raises(JamulusNameError, match="too long"):
        validate_jamulus_name("12345678901234567")


def test_utf16_limit_counts_supplementary_emoji_as_two_units():
    accepted = validate_jamulus_name("12345678901234🎸")
    assert accepted.utf16_units == 16
    assert accepted.value == "12345678901234🎸"
    with pytest.raises(JamulusNameError, match="emoji can use two"):
        validate_jamulus_name("123456789012345🎸")


def test_combining_and_emoji_zwj_sequences_are_never_split_in_preview():
    combining = "Cafe\u0301BandX"
    combined_result = validate_jamulus_name(combining)
    assert grapheme_clusters(combining)[3] == "e\u0301"
    assert combined_result.first_line == "Cafe\u0301Band"
    assert combined_result.second_line == "X"

    family = "A👩\u200d👩\u200d👧B"
    assert grapheme_clusters(family) == ("A", "👩\u200d👩\u200d👧", "B")
    assert validate_jamulus_name(family).value == family


@pytest.mark.parametrize(
    "value",
    (
        "Jeff\nStory",
        "Jeff\rStory",
        "Jeff\tStory",
        "Jeff\x00Story",
        "Jeff\u2028Story",
        "Jeff\u202eStory",
    ),
)
def test_controls_line_breaks_and_direction_overrides_are_rejected(value):
    with pytest.raises(JamulusNameError, match="control characters"):
        validate_jamulus_name(value)


def test_accepted_unicode_is_preserved_exactly():
    value = "Zoë 🎸"
    result = validate_jamulus_name(f"  {value}  ")
    assert result.value == value
    assert utf16_units(result.value) == 6
    assert JAMULUS_NAME_HELP.startswith("Jamulus displays up to 16")


def test_legacy_recovery_uses_safe_default_without_abbreviation():
    assert recover_jamulus_name("x" * 17) == DEFAULT_JAMULUS_NAME
    assert recover_jamulus_name("Good Name") == "Good Name"


def test_invalid_file_and_environment_names_recover_safely(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"config_file": str(path), "musician_name": "x" * 17}),
        encoding="utf-8",
    )
    assert load_settings(str(path)).musician_name == DEFAULT_JAMULUS_NAME

    monkeypatch.setenv("WEBJAM_MUSICIAN_NAME", "bad\nname")
    assert load_settings(str(path)).musician_name == DEFAULT_JAMULUS_NAME


def test_new_settings_write_rejects_invalid_name(tmp_path):
    path = tmp_path / "settings.json"
    with pytest.raises(JamulusNameError):
        save_settings(
            AppSettings(config_file=str(path), musician_name="x" * 17)
        )
    assert not path.exists()


def test_profile_boundary_preserves_exact_valid_name_and_rejects_overlength():
    adapter = Jamulus3122AudioRouteAdapter()
    payload = adapter.render_inifile(
        _linux_profile(),
        musician_name="Zoë 🎸",
    )
    settings = {
        child.tag: child.text
        for child in ElementTree.fromstring(payload)
    }
    assert (
        base64.b64decode(settings["name_base64"]).decode("utf-8")
        == "Zoë 🎸"
    )
    with pytest.raises(JamulusNameError):
        adapter.render_inifile(
            _linux_profile(),
            musician_name="x" * 17,
        )
