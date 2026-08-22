"""Separated stems as faders beside the jam, and the one honest route back in."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.stem_bench import (
    MAX_STEMS,
    StemBench,
    StemBenchError,
    StemTarget,
    bounce_stems,
)

soundfile = pytest.importorskip("soundfile")
numpy = pytest.importorskip("numpy")

STEM_NAMES = ("vocals", "drums", "bass", "other")


@pytest.fixture
def stem_files(tmp_path):
    paths = {}
    for index, name in enumerate(STEM_NAMES, start=1):
        path = tmp_path / f"{name}.wav"
        tone = (
            numpy.sin(
                numpy.linspace(0, index * 40 * numpy.pi, 24000, dtype="float32")
            )
            * 0.2
        )
        soundfile.write(str(path), numpy.column_stack([tone, tone]), 48000)
        paths[name] = str(path)
    return paths


@pytest.fixture
def bench(stem_files):
    instance = StemBench()
    instance.load(
        [(name, stem_files[name]) for name in STEM_NAMES],
        source_name="demo_mix.wav",
    )
    return instance


# ----------------------------------------------------------------------
# Loading and naming
# ----------------------------------------------------------------------
def test_stems_arrive_with_names_a_musician_recognises(bench):
    assert [stem.label for stem in bench.stems] == [
        "Vocals",
        "Drums",
        "Bass",
        "Other",
    ]
    assert bench.source_name == "demo_mix.wav"
    assert bench.loaded


def test_each_stem_says_what_muting_it_is_for(bench):
    assert "sing it yourself" in bench.stem("vocals").hint
    assert "play the kit" in bench.stem("drums").hint


def test_an_unknown_stem_name_still_loads_readably():
    bench = StemBench()
    bench.load([("cinematic", "/tmp/x.wav")])
    assert bench.stems[0].label == "Cinematic"


def test_stems_without_a_path_are_dropped():
    bench = StemBench()
    bench.load([("vocals", ""), ("drums", "/tmp/d.wav")])
    assert [stem.name for stem in bench.stems] == ["drums"]


def test_the_bench_is_bounded():
    bench = StemBench()
    bench.load([(f"stem{index}", f"/tmp/{index}.wav") for index in range(50)])
    assert len(bench.stems) == MAX_STEMS


# ----------------------------------------------------------------------
# Mixing, the way the room's faders already work
# ----------------------------------------------------------------------
def test_everything_is_audible_until_someone_touches_a_fader(bench):
    mix = bench.mix()
    assert len(mix.audible) == 4
    assert not mix.silent
    assert mix.describe().startswith("All stems:")


def test_muting_removes_one_stem_and_says_which(bench):
    bench.set_muted("vocals", True)
    mix = bench.mix()

    assert [stem.name for stem in mix.silent] == ["vocals"]
    assert "without Vocals" in mix.describe()


def test_solo_silences_everything_not_soloed(bench):
    """The same rule the participant grid uses, so nothing surprises anyone."""

    bench.set_muted("bass", True)
    bench.set_solo("drums", True)
    mix = bench.mix()

    assert [stem.name for stem in mix.audible] == ["drums"]
    assert mix.soloing
    assert "Soloing Drums" in mix.describe()


def test_toggles_flip_state(bench):
    assert bench.toggle_mute("vocals")
    assert bench.stem("vocals").muted
    assert bench.toggle_mute("vocals")
    assert not bench.stem("vocals").muted

    assert bench.toggle_solo("drums")
    assert bench.stem("drums").solo


def test_toggling_a_stem_that_is_not_there_reports_failure(bench):
    assert bench.toggle_mute("tuba") is False
    assert bench.set_solo("tuba", True) is False


def test_reset_clears_every_fader(bench):
    bench.set_muted("vocals", True)
    bench.set_solo("drums", True)
    bench.reset()

    assert all(not stem.muted and not stem.solo for stem in bench.stems)


def test_an_empty_bench_says_what_to_do(bench):
    empty = StemBench()
    assert "Run Split stems" in empty.mix().describe()
    assert empty.mix().is_empty


# ----------------------------------------------------------------------
# The move people actually run separation for
# ----------------------------------------------------------------------
def test_sing_this_one_mutes_the_vocal_in_a_single_call(bench):
    assert bench.sing_this_one()
    mix = bench.mix()

    assert [stem.name for stem in mix.silent] == ["vocals"]
    assert {stem.name for stem in mix.audible} == {"drums", "bass", "other"}


def test_sing_this_one_clears_any_solo_that_would_fight_it(bench):
    bench.set_solo("vocals", True)
    bench.sing_this_one()

    assert not any(stem.solo for stem in bench.stems)
    assert bench.stem("vocals").muted


def test_sing_this_one_reports_failure_when_there_is_no_vocal_stem():
    bench = StemBench()
    bench.load([("drums", "/tmp/d.wav"), ("bass", "/tmp/b.wav")])
    assert bench.sing_this_one() is False


def test_a_two_stem_separation_is_handled(stem_files):
    """The documented shared workflow returns vocals and accompaniment."""

    bench = StemBench()
    bench.load(
        [
            ("vocals", stem_files["vocals"]),
            ("accompaniments", stem_files["drums"]),
        ]
    )
    assert bench.sing_this_one()
    path, note = bench.shared_track_plan()

    assert path == stem_files["drums"]
    assert "Backing" in note


# ----------------------------------------------------------------------
# Getting back into the jam
# ----------------------------------------------------------------------
def test_one_audible_stem_needs_no_mixing(bench):
    bench.set_solo("drums", True)
    path, note = bench.shared_track_plan()

    assert path == bench.stem("drums").path
    assert "Sending Drums" in note


def test_several_audible_stems_ask_to_be_mixed_first(bench):
    bench.set_muted("vocals", True)
    path, note = bench.shared_track_plan()

    assert path == ""
    assert "Mix 3 stems into one file first." == note


def test_everything_muted_is_refused_with_a_reason(bench):
    for name in STEM_NAMES:
        bench.set_muted(name, True)
    path, note = bench.shared_track_plan()

    assert path == ""
    assert "Every stem is muted" in note


def test_an_empty_bench_is_refused_with_what_to_do():
    path, note = StemBench().shared_track_plan()
    assert path == ""
    assert "Run Split stems on a file you own first." == note


def test_a_stem_that_vanished_from_disk_is_reported_not_sent(bench, tmp_path):
    Path(bench.stem("drums").path).unlink()
    bench.set_solo("drums", True)
    path, note = bench.shared_track_plan()

    assert path == ""
    assert "cannot read Drums" in note


def test_the_bounce_name_is_stable_for_the_same_mix(bench):
    bench.set_muted("vocals", True)
    first = bench.bounce_name()
    bench.set_muted("vocals", False)
    bench.set_muted("vocals", True)

    assert bench.bounce_name() == first
    assert first.endswith(".wav")
    assert "/" not in first


def test_the_bounce_name_changes_when_the_mix_does(bench):
    bench.set_muted("vocals", True)
    muted = bench.bounce_name()
    bench.set_muted("drums", True)

    assert bench.bounce_name() != muted


# ----------------------------------------------------------------------
# The bounce itself
# ----------------------------------------------------------------------
def test_audible_stems_mix_down_to_one_playable_file(bench, tmp_path):
    bench.sing_this_one()
    destination = tmp_path / "out" / bench.bounce_name()

    mixed = bounce_stems(list(bench.mix().audible), destination)

    info = soundfile.info(mixed)
    assert Path(mixed).is_file()
    assert info.samplerate == 48000
    assert info.channels == 2
    assert info.frames == 24000


def test_the_bounce_cannot_clip_by_summing(bench, tmp_path):
    """Four stems summed raw would exceed full scale; the mix is scaled."""

    mixed = bounce_stems(list(bench.stems), tmp_path / "sum.wav")
    data, _rate = soundfile.read(mixed, dtype="float32")

    assert float(numpy.max(numpy.abs(data))) <= 1.0


def test_a_bounce_of_nothing_is_refused(tmp_path):
    with pytest.raises(StemBenchError, match="no audible stems"):
        bounce_stems([], tmp_path / "empty.wav")


def test_an_unreadable_stem_is_reported_by_name(tmp_path):
    with pytest.raises(StemBenchError, match="Vocals"):
        bounce_stems(
            [StemTarget(name="vocals", path=str(tmp_path / "missing.wav"))],
            tmp_path / "out.wav",
        )


def test_stems_at_different_sample_rates_are_refused_not_resampled(tmp_path):
    tone = numpy.zeros((4800, 2), dtype="float32")
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    soundfile.write(str(first), tone, 48000)
    soundfile.write(str(second), tone, 44100)

    with pytest.raises(StemBenchError, match="sample rate"):
        bounce_stems(
            [
                StemTarget(name="drums", path=str(first)),
                StemTarget(name="bass", path=str(second)),
            ],
            tmp_path / "out.wav",
        )


def test_a_mono_stem_is_carried_into_both_channels(tmp_path):
    mono = tmp_path / "mono.wav"
    stereo = tmp_path / "stereo.wav"
    soundfile.write(str(mono), numpy.full(4800, 0.5, dtype="float32"), 48000)
    soundfile.write(str(stereo), numpy.zeros((4800, 2), dtype="float32"), 48000)

    mixed = bounce_stems(
        [
            StemTarget(name="bass", path=str(mono)),
            StemTarget(name="drums", path=str(stereo)),
        ],
        tmp_path / "out.wav",
    )
    data, _rate = soundfile.read(mixed, dtype="float32")

    assert data.shape[1] == 2
    assert float(data[0][0]) == pytest.approx(float(data[0][1]), abs=1e-6)
    assert float(data[0][0]) > 0


def test_stems_of_different_lengths_are_padded_not_truncated(tmp_path):
    short = tmp_path / "short.wav"
    long = tmp_path / "long.wav"
    soundfile.write(str(short), numpy.zeros((1000, 2), dtype="float32"), 48000)
    soundfile.write(str(long), numpy.zeros((5000, 2), dtype="float32"), 48000)

    mixed = bounce_stems(
        [
            StemTarget(name="drums", path=str(short)),
            StemTarget(name="bass", path=str(long)),
        ],
        tmp_path / "out.wav",
    )
    assert soundfile.info(mixed).frames == 5000


def test_a_bounce_into_an_impossible_place_is_reported(bench, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")

    with pytest.raises(StemBenchError):
        bounce_stems(list(bench.stems), blocker / "nested" / "out.wav")
