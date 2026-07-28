"""Schema-3 mixer, automation, routing, DSP, and compatibility contracts."""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace

import numpy as np
import pytest

from core.studio_history import StudioHistory
from core.studio_mixer import (
    MAX_MIXER_BLOCK_FRAMES,
    MIXER_CAPABILITY_ID,
    StudioMixEngine,
    StudioMixerError,
    automation_values,
    studio_effect_tail_frames,
    studio_mixer_capability,
)
from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioAutomationInterpolation,
    StudioAutomationLane,
    StudioAutomationParameter,
    StudioAutomationPoint,
    StudioDocument,
    StudioEffect,
    StudioEffectKind,
    StudioMaster,
    StudioProjectError,
    StudioSend,
    StudioTrack,
    StudioTrackKind,
    studio_document_from_dict,
)


def _id(number: int) -> str:
    return str(uuid.UUID(int=number))


def _point(frame: int, value: float) -> StudioAutomationPoint:
    return StudioAutomationPoint(frame=frame, value=value)


def _lane(
    number: int,
    parameter: StudioAutomationParameter,
    points: tuple[StudioAutomationPoint, ...],
    *,
    interpolation: StudioAutomationInterpolation = (
        StudioAutomationInterpolation.LINEAR
    ),
) -> StudioAutomationLane:
    return StudioAutomationLane(
        lane_id=_id(number),
        parameter=parameter,
        points=points,
        interpolation=interpolation,
    )


def _effect(number: int, kind: StudioEffectKind, **changes: object) -> StudioEffect:
    return StudioEffect(effect_id=_id(number), kind=kind, **changes)


def _document(*tracks: StudioTrack) -> StudioDocument:
    return StudioDocument(
        project_id=_id(1),
        project_sample_rate=48_000,
        tracks=tracks,
        schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    )


def _states(document: StudioDocument) -> dict[str, StudioTrack]:
    return {track.track_id: track for track in document.tracks}


def test_schema3_mixer_round_trip_and_legacy_v3_defaults() -> None:
    bus = StudioTrack(
        _id(20),
        order=1,
        name="Shared Space",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        effects=(
            _effect(70, StudioEffectKind.HPF),
            _effect(71, StudioEffectKind.REVERB),
        ),
    )
    track = StudioTrack(
        _id(10),
        name="Vocal",
        channel_count=2,
        output_bus_id=bus.track_id,
        sends=(
            StudioSend(
                send_id=_id(40),
                target_bus_id=bus.track_id,
                gain=0.25,
            ),
        ),
        automation=(
            _lane(
                50,
                StudioAutomationParameter.VOLUME,
                (_point(0, 0.5), _point(48_000, 1.0)),
            ),
            _lane(
                51,
                StudioAutomationParameter.MUTE,
                (_point(96_000, 1.0),),
                interpolation=StudioAutomationInterpolation.HOLD,
            ),
        ),
    )
    document = _document(track, bus)
    payload = document.to_dict()
    restored = studio_document_from_dict(copy.deepcopy(payload))

    assert restored == document
    assert restored.to_dict() == payload
    assert payload["tracks"][0]["output_bus_id"] == bus.track_id
    assert payload["tracks"][0]["automation"][0]["points"][1] == {
        "frame": 48_000,
        "value": 1.0,
    }

    legacy = copy.deepcopy(payload)
    for item in legacy["tracks"]:
        item.pop("output_bus_id")
        item.pop("sends")
        item.pop("automation")
        item.pop("effects")
    migrated = studio_document_from_dict(legacy)
    assert all(
        not track.output_bus_id
        and not track.sends
        and not track.automation
        and not track.effects
        for track in migrated.tracks
    )
    assert "automation" in migrated.to_dict()["tracks"][0]


def test_schema2_rejects_hidden_schema3_mixer_state() -> None:
    with pytest.raises(StudioProjectError, match="song-track fields"):
        StudioDocument(
            session_id=_id(1),
            take_id=_id(2),
            tracks=(
                StudioTrack(
                    _id(10),
                    automation=(
                        _lane(
                            50,
                            StudioAutomationParameter.VOLUME,
                            (_point(0, 1.0),),
                        ),
                    ),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("lane", "message"),
    (
        (
            lambda: _lane(
                1,
                StudioAutomationParameter.PAN,
                (_point(10, 0.0), _point(9, 1.0)),
            ),
            "ascending",
        ),
        (
            lambda: _lane(
                1,
                StudioAutomationParameter.MUTE,
                (_point(0, 1.0),),
            ),
            "hold",
        ),
        (
            lambda: _lane(
                1,
                StudioAutomationParameter.VOLUME,
                (_point(0, -0.1),),
            ),
            "between 0 and 4",
        ),
    ),
)
def test_automation_validation_is_strict(lane, message: str) -> None:
    with pytest.raises(StudioProjectError, match=message):
        lane()


def test_automation_exact_breakpoints_and_partition_parity() -> None:
    lane = _lane(
        1,
        StudioAutomationParameter.VOLUME,
        (_point(10, 0.0), _point(20, 1.0), _point(30, 0.25)),
    )
    complete = automation_values(lane, 5, 31)
    partitioned = np.concatenate(
        (
            automation_values(lane, 5, 7),
            automation_values(lane, 12, 9),
            automation_values(lane, 21, 15),
        )
    )
    np.testing.assert_array_equal(complete, partitioned)
    assert complete[0] == np.float32(0.0)
    assert complete[10 - 5] == np.float32(0.0)
    assert complete[15 - 5] == np.float32(0.5)
    assert complete[20 - 5] == np.float32(1.0)
    assert complete[30 - 5] == np.float32(0.25)
    assert complete[-1] == np.float32(0.25)


def test_routing_requires_buses_is_acyclic_and_reverb_is_shared_only() -> None:
    audio = StudioTrack(
        _id(10),
        name="Audio",
        output_bus_id=_id(99),
    )
    with pytest.raises(StudioProjectError, match="target a bus"):
        _document(audio)

    first_bus = StudioTrack(
        _id(20),
        order=0,
        name="A",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        output_bus_id=_id(21),
    )
    second_bus = StudioTrack(
        _id(21),
        order=1,
        name="B",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        output_bus_id=_id(20),
    )
    with pytest.raises(StudioProjectError, match="cycle"):
        _document(first_bus, second_bus)

    with pytest.raises(StudioProjectError, match="only on a Studio bus"):
        _document(
            StudioTrack(
                _id(10),
                name="Audio",
                effects=(_effect(70, StudioEffectKind.REVERB),),
            )
        )
    with pytest.raises(StudioProjectError, match="below Nyquist"):
        _document(
            StudioTrack(
                _id(20),
                name="Bus",
                kind=StudioTrackKind.BUS,
                channel_count=2,
                effects=(
                    _effect(
                        70,
                        StudioEffectKind.EQ,
                        eq_frequency_hz=24_000.0,
                    ),
                ),
            )
        )
    with pytest.raises(StudioProjectError, match="bus solo is not available"):
        _document(
            StudioTrack(
                _id(20),
                name="Bus",
                kind=StudioTrackKind.BUS,
                channel_count=2,
                solo=True,
            )
        )


def test_volume_pan_mute_and_post_send_follow_absolute_frames() -> None:
    bus = StudioTrack(
        _id(20),
        order=1,
        name="Parallel",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        fader_gain=0.5,
    )
    source = StudioTrack(
        _id(10),
        name="Source",
        channel_count=2,
        fader_gain=0.5,
        pan=1.0,
        sends=(
            StudioSend(
                send_id=_id(30),
                target_bus_id=bus.track_id,
                gain=0.5,
            ),
        ),
        automation=(
            _lane(
                40,
                StudioAutomationParameter.MUTE,
                (_point(0, 0.0), _point(2, 1.0), _point(3, 0.0)),
                interpolation=StudioAutomationInterpolation.HOLD,
            ),
        ),
    )
    document = _document(source, bus)
    raw = np.ones((4, 2), dtype=np.float32)
    result = StudioMixEngine(document).process_block(
        start_frame=0,
        frame_count=4,
        raw_tracks={source.track_id: raw},
        track_states=_states(document),
        master=StudioMaster(limiter_enabled=False),
    )

    # Source right = .5; its post send is .25 into a .5 bus = .125.
    np.testing.assert_allclose(result.master[:2, 0], 0.0, atol=1e-7)
    np.testing.assert_allclose(result.master[:2, 1], 0.625, atol=1e-7)
    np.testing.assert_array_equal(result.master[2], np.zeros(2, dtype=np.float32))
    np.testing.assert_allclose(result.master[3], (0.0, 0.625), atol=1e-7)


def test_pre_fader_send_remains_independent_of_fader_and_mute() -> None:
    bus = StudioTrack(
        _id(20),
        order=1,
        name="Cue",
        kind=StudioTrackKind.BUS,
        channel_count=2,
    )
    source = StudioTrack(
        _id(10),
        name="Source",
        muted=True,
        fader_gain=0.0,
        sends=(
            StudioSend(
                send_id=_id(30),
                target_bus_id=bus.track_id,
                pre_fader=True,
                gain=0.25,
            ),
        ),
    )
    document = _document(source, bus)
    result = StudioMixEngine(document).process_block(
        start_frame=0,
        frame_count=8,
        raw_tracks={
            source.track_id: np.ones((8, 2), dtype=np.float32),
        },
        track_states=_states(document),
        master=StudioMaster(limiter_enabled=False),
    )
    np.testing.assert_array_equal(
        result.master,
        np.full((8, 2), 0.25, dtype=np.float32),
    )


def _dsp_document() -> tuple[StudioDocument, StudioTrack]:
    bus = StudioTrack(
        _id(20),
        order=1,
        name="Shared FX",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        effects=(
            _effect(70, StudioEffectKind.HPF, hpf_frequency_hz=120.0),
            _effect(
                71,
                StudioEffectKind.EQ,
                eq_frequency_hz=1_500.0,
                eq_gain_db=3.0,
                eq_q=1.2,
            ),
            _effect(
                72,
                StudioEffectKind.COMPRESSOR,
                compressor_threshold_db=-24.0,
                compressor_ratio=4.0,
            ),
            _effect(
                73,
                StudioEffectKind.GATE,
                gate_threshold_db=-70.0,
            ),
            _effect(
                74,
                StudioEffectKind.REVERB,
                reverb_mix=0.3,
                reverb_decay=0.5,
                reverb_delay_ms=5.0,
            ),
        ),
    )
    source = StudioTrack(
        _id(10),
        name="Source",
        channel_count=2,
        output_bus_id=bus.track_id,
    )
    return _document(source, bus), source


def test_all_builtin_dsp_has_exact_block_partition_parity() -> None:
    document, source = _dsp_document()
    phase = np.arange(4_096, dtype=np.float32)
    raw = np.column_stack(
        (
            np.sin(phase * np.float32(0.017)) * np.float32(0.7),
            np.cos(phase * np.float32(0.013)) * np.float32(0.5),
        )
    ).astype(np.float32)

    complete_engine = StudioMixEngine(document)
    complete = complete_engine.process_block(
        start_frame=0,
        frame_count=len(raw),
        raw_tracks={source.track_id: raw.copy()},
        track_states=_states(document),
        master=StudioMaster(limiter_enabled=False),
    ).master

    partitioned_engine = StudioMixEngine(document)
    blocks = []
    cursor = 0
    for count in (1, 17, 255, 1_023, 2_800):
        block = raw[cursor : cursor + count].copy()
        blocks.append(
            partitioned_engine.process_block(
                start_frame=cursor,
                frame_count=len(block),
                raw_tracks={source.track_id: block},
                track_states=_states(document),
                master=StudioMaster(limiter_enabled=False),
            ).master
        )
        cursor += count
    assert cursor == len(raw)
    np.testing.assert_array_equal(complete, np.concatenate(blocks))
    assert np.all(np.isfinite(complete))
    assert float(np.max(np.abs(complete))) > 0.01


def test_reverb_allocation_is_fixed_and_tail_is_bounded() -> None:
    document, source = _dsp_document()
    engine = StudioMixEngine(document)
    reverb = engine._processors[_id(20)][-1]
    delay = reverb._delay
    assert delay.shape == (240, 2)
    assert 0 < studio_effect_tail_frames(document) <= 480_000

    for start in range(0, 2_560, 256):
        engine.process_block(
            start_frame=start,
            frame_count=256,
            raw_tracks={
                source.track_id: np.zeros((256, 2), dtype=np.float32),
            },
            track_states=_states(document),
            master=StudioMaster(),
        )
        assert reverb._delay is delay
        assert reverb._delay.shape == (240, 2)


def test_limiter_clipping_contiguity_and_cancellation_are_explicit() -> None:
    source = StudioTrack(_id(10), name="Source", fader_gain=4.0)
    document = _document(source)
    raw = np.ones((16, 2), dtype=np.float32)
    clipped = StudioMixEngine(document).process_block(
        start_frame=0,
        frame_count=16,
        raw_tracks={source.track_id: raw},
        track_states=_states(document),
        master=StudioMaster(gain=4.0, limiter_enabled=True),
    )
    np.testing.assert_array_equal(
        clipped.master,
        np.ones((16, 2), dtype=np.float32),
    )
    open_bus = StudioMixEngine(document).process_block(
        start_frame=0,
        frame_count=16,
        raw_tracks={source.track_id: raw},
        track_states=_states(document),
        master=StudioMaster(gain=4.0, limiter_enabled=False),
    )
    assert float(open_bus.master.max()) == pytest.approx(16.0)

    engine = StudioMixEngine(document)
    engine.process_block(
        start_frame=0,
        frame_count=1,
        raw_tracks={source.track_id: raw[:1]},
        track_states=_states(document),
        master=StudioMaster(),
    )
    with pytest.raises(StudioMixerError, match="contiguous"):
        engine.process_block(
            start_frame=2,
            frame_count=1,
            raw_tracks={source.track_id: raw[:1]},
            track_states=_states(document),
            master=StudioMaster(),
        )

    class Cancelled(RuntimeError):
        pass

    with pytest.raises(Cancelled, match="stop DSP"):
        StudioMixEngine(document).process_block(
            start_frame=0,
            frame_count=16,
            raw_tracks={source.track_id: raw},
            track_states=_states(document),
            master=StudioMaster(),
            cancel_check=lambda: (_ for _ in ()).throw(Cancelled("stop DSP")),
        )
    assert engine.capability_id == MIXER_CAPABILITY_ID
    capability = studio_mixer_capability()
    assert capability.available
    assert capability.deterministic
    assert capability.bounded_state
    assert capability.producer_thread_safe
    assert not capability.device_callback_safe
    assert set(capability.effects) == {item.value for item in StudioEffectKind}


def test_track_count_block_budget_rejects_before_audio_allocation() -> None:
    tracks = tuple(
        StudioTrack(_id(100 + index), order=index, name=f"Track {index}")
        for index in range(9)
    )
    document = _document(*tracks)
    with pytest.raises(StudioMixerError, match="too large for this track count"):
        StudioMixEngine(document).process_block(
            start_frame=0,
            frame_count=MAX_MIXER_BLOCK_FRAMES,
            raw_tracks={},
            track_states=_states(document),
            master=StudioMaster(),
        )


def test_dense_effect_graph_is_truthfully_gated_to_offline_rendering() -> None:
    effect_kinds = (
        StudioEffectKind.HPF,
        StudioEffectKind.EQ,
        StudioEffectKind.COMPRESSOR,
        StudioEffectKind.GATE,
    )
    tracks = tuple(
        StudioTrack(
            _id(200 + track_index),
            order=track_index,
            name=f"Processed {track_index}",
            effects=tuple(
                _effect(
                    300 + track_index * len(effect_kinds) + effect_index,
                    kind,
                )
                for effect_index, kind in enumerate(effect_kinds)
            ),
        )
        for track_index in range(4)
    )
    capability = studio_mixer_capability(_document(*tracks))
    assert capability.available
    assert capability.deterministic
    assert not capability.realtime_playback_supported
    assert capability.realtime_effect_units_48k == 16
    assert "offline bounce" in capability.detail


def test_history_rejects_cross_project_schema3_transition() -> None:
    document = _document(StudioTrack(_id(10), name="Audio"))
    history = StudioHistory(document)
    with pytest.raises(StudioProjectError, match="project and sample-rate"):
        history.perform(
            "Wrong project",
            lambda value: replace(
                value,
                project_id=_id(99),
                revision=value.revision + 1,
            ),
        )
