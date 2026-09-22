"""Recording repair uses effective settings and the next owned meter start."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from core import audio_engine, settings


@pytest.mark.parametrize("rate,buffer,expected", [
    (None, None, {}),
    ("44100", "256", {"audio_samplerate": 44100, "audio_blocksize": 256}),
    ("48000", "0", {"audio_samplerate": 48000, "audio_blocksize": 0}),
    ("0", "-1", {}),
    ("-48000", "invalid", {}),
    ("PRIVATE_BAD_VALUE", " 512 ", {"audio_blocksize": 512}),
])
def test_override_projection_matches_settings_loader(tmp_path, monkeypatch, rate, buffer, expected):
    original = settings.AppSettings(
        config_file=str(tmp_path / "settings.json"),
        audio_samplerate=48000, audio_blocksize=0,
    )
    settings.save_settings(original)
    for key, value in (("WEBJAM_AUDIO_SAMPLERATE", rate), ("WEBJAM_AUDIO_BLOCKSIZE", buffer)):
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)
    effective = settings.load_settings(original.config_file)
    assert settings.audio_format_environment_overrides() == expected
    assert effective.audio_samplerate == expected.get("audio_samplerate", 48000)
    assert effective.audio_blocksize == expected.get("audio_blocksize", 0)


@pytest.mark.parametrize("available", [True, False])
def test_next_owned_meter_start_reports_repaired_format_without_reopening_live_stream(monkeypatch, available):
    original = settings.AppSettings(audio_samplerate=44100, audio_blocksize=256)
    engine = audio_engine.RealAudioEngine(original)
    backend = Mock()
    monkeypatch.setattr(audio_engine, "sd", backend if available else None)
    monkeypatch.setattr(engine, "_resolve_device", lambda: 7)
    try:
        engine.start()
        assert engine.diagnostics().samplerate == 44100
        engine.settings = replace(original, audio_samplerate=48000, audio_blocksize=0)
        engine.start()
        assert engine.diagnostics().samplerate == 44100
        assert backend.InputStream.call_count == int(available)
        engine.stop()
        engine.start()
        assert engine.diagnostics().samplerate == 48000
        assert engine.diagnostics().blocksize == 0
        assert engine.diagnostics().active is available
        if available:
            assert backend.InputStream.call_args.kwargs["samplerate"] == 48000
            assert backend.InputStream.call_args.kwargs["blocksize"] == 0
        else:
            assert engine.get_level(-1) == 0.0
    finally:
        engine.stop()
