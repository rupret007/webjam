"""Deterministic bounded mixer and built-in DSP for schema-3 Studio projects.

The mixer consumes already rendered stereo source tracks.  It owns no media,
device, plug-in, or worker-thread policy, and its state grows only with the
validated document and configured reverb delay.  Playback and bounce therefore
share the exact same channel-strip, automation, routing, and master path.

All effects are small WebJam-owned implementations.  There is deliberately no
third-party plug-in hosting.  Reverb is restricted by the project model to a
bus so one stateful processor can be shared by any number of sends.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np

from core.studio_project import (
    MAX_PROJECT_FRAMES,
    StudioAutomationInterpolation,
    StudioAutomationLane,
    StudioAutomationParameter,
    StudioDocument,
    StudioEffect,
    StudioEffectKind,
    StudioMaster,
    StudioTrack,
    StudioTrackKind,
)

MIXER_CAPABILITY_ID = "webjam.schema3-mixer.v1"
MIXER_NOISE_FLOOR_DB = -100.0
MAX_REVERB_TAIL_SECONDS = 10.0
MAX_MIXER_BLOCK_FRAMES = 1_048_576
MAX_MIXER_TRACK_BLOCK_FRAMES = 8_388_608
MAX_REALTIME_EFFECT_UNITS_48K = 12


class StudioMixerError(RuntimeError):
    """Raised when schema-3 signal flow cannot be rendered exactly."""


@dataclass(frozen=True)
class StudioMixerCapability:
    """Truthful execution contract for the built-in schema-3 mixer."""

    capability_id: str = MIXER_CAPABILITY_ID
    available: bool = True
    deterministic: bool = True
    bounded_state: bool = True
    producer_thread_safe: bool = True
    device_callback_safe: bool = False
    realtime_playback_supported: bool = True
    realtime_effect_units_48k: int = 0
    realtime_effect_unit_limit_48k: int = MAX_REALTIME_EFFECT_UNITS_48K
    effects: tuple[str, ...] = tuple(item.value for item in StudioEffectKind)
    detail: str = (
        "Run on WebJam's bounded render/producer worker; processing allocates "
        "block-sized arrays and must not run in an audio-device callback."
    )


def studio_mixer_capability(
    document: StudioDocument | None = None,
) -> StudioMixerCapability:
    """Return the tested capability contract, optionally for one document.

    Dense effect graphs remain deterministic and available for offline bounce,
    but are capability-gated from interactive playback when their conservative
    sample-rate-adjusted work estimate exceeds the tested producer budget.
    """

    if document is None:
        return StudioMixerCapability()
    if not isinstance(document, StudioDocument) or document.schema_version != 3:
        raise StudioMixerError(
            "Mixer capability requires a schema-3 document or null."
        )
    enabled_effects = sum(
        int(effect.enabled)
        for track in document.tracks
        for effect in track.effects
    )
    units = math.ceil(
        enabled_effects * document.project_sample_rate / 48_000
    )
    supported = units <= MAX_REALTIME_EFFECT_UNITS_48K
    return StudioMixerCapability(
        realtime_playback_supported=supported,
        realtime_effect_units_48k=units,
        detail=(
            "Run on WebJam's bounded render/producer worker; processing allocates "
            "block-sized arrays and must not run in an audio-device callback."
            if supported
            else (
                "This dense effect graph is available for deterministic offline "
                "bounce but exceeds the tested interactive producer budget."
            )
        ),
    )


def _coefficient(milliseconds: float, sample_rate: int) -> float:
    seconds = max(float(milliseconds) / 1_000.0, 1.0 / sample_rate)
    return math.exp(-1.0 / (seconds * sample_rate))


def _db_to_gain(value: float) -> float:
    return math.pow(10.0, float(value) / 20.0)


def _finite_stereo(block: np.ndarray, frame_count: int, field_name: str) -> None:
    if (
        not isinstance(block, np.ndarray)
        or block.dtype != np.float32
        or block.shape != (frame_count, 2)
        or not np.all(np.isfinite(block))
    ):
        raise StudioMixerError(f"{field_name} must be finite stereo float32 audio.")


def automation_values(
    lane: StudioAutomationLane,
    start_frame: int,
    frame_count: int,
) -> np.ndarray:
    """Return deterministic float32 values for an absolute frame interval.

    The first and last breakpoint values extend outward.  LINEAR uses the
    exact integer-frame distance between neighboring points; HOLD changes on
    the breakpoint frame itself.  Results therefore do not depend on how a
    caller partitions the same interval into render blocks.
    """

    if not isinstance(lane, StudioAutomationLane):
        raise StudioMixerError("automation lane must be a StudioAutomationLane.")
    if (
        isinstance(start_frame, bool)
        or not isinstance(start_frame, int)
        or not -MAX_PROJECT_FRAMES <= start_frame <= MAX_PROJECT_FRAMES
    ):
        raise StudioMixerError("automation start_frame must be an integer.")
    if (
        isinstance(frame_count, bool)
        or not isinstance(frame_count, int)
        or frame_count < 0
        or frame_count > MAX_MIXER_BLOCK_FRAMES
        or start_frame + frame_count > MAX_PROJECT_FRAMES
    ):
        raise StudioMixerError("automation frame_count must be non-negative.")
    if frame_count == 0:
        return np.zeros(0, dtype=np.float32)

    point_frames = np.fromiter(
        (item.frame for item in lane.points),
        dtype=np.int64,
        count=len(lane.points),
    )
    point_values = np.fromiter(
        (item.value for item in lane.points),
        dtype=np.float64,
        count=len(lane.points),
    )
    return _automation_values(
        lane,
        point_frames,
        point_values,
        start_frame,
        frame_count,
    )


def _automation_values(
    lane: StudioAutomationLane,
    point_frames: np.ndarray,
    point_values: np.ndarray,
    start_frame: int,
    frame_count: int,
) -> np.ndarray:
    frames = np.arange(
        start_frame,
        start_frame + frame_count,
        dtype=np.int64,
    )
    right = np.searchsorted(point_frames, frames, side="right")
    left = np.maximum(right - 1, 0)
    result = point_values[left].copy()
    before = right == 0
    result[before] = point_values[0]
    if lane.interpolation is StudioAutomationInterpolation.LINEAR:
        between = (right > 0) & (right < len(point_frames))
        if np.any(between):
            left_indices = left[between]
            right_indices = right[between]
            left_frames = point_frames[left_indices]
            spans = point_frames[right_indices] - left_frames
            offsets = frames[between] - left_frames
            progress = offsets.astype(np.float64) / spans.astype(np.float64)
            left_values = point_values[left_indices]
            result[between] = left_values + (
                point_values[right_indices] - left_values
            ) * progress
    return result.astype(np.float32)


class _EffectProcessor:
    def reset(self) -> None:
        raise NotImplementedError

    def process_inplace(self, block: np.ndarray) -> None:
        raise NotImplementedError


class _HighPassProcessor(_EffectProcessor):
    def __init__(self, effect: StudioEffect, sample_rate: int) -> None:
        rc = 1.0 / (2.0 * math.pi * effect.hpf_frequency_hz)
        dt = 1.0 / sample_rate
        self._alpha = rc / (rc + dt)
        self._previous_input = np.zeros(2, dtype=np.float64)
        self._previous_output = np.zeros(2, dtype=np.float64)

    def reset(self) -> None:
        self._previous_input.fill(0.0)
        self._previous_output.fill(0.0)

    def process_inplace(self, block: np.ndarray) -> None:
        for frame in range(len(block)):
            for channel in range(2):
                source = float(block[frame, channel])
                output = self._alpha * (
                    self._previous_output[channel]
                    + source
                    - self._previous_input[channel]
                )
                self._previous_input[channel] = source
                self._previous_output[channel] = output
                block[frame, channel] = np.float32(output)


class _EqualizerProcessor(_EffectProcessor):
    def __init__(self, effect: StudioEffect, sample_rate: int) -> None:
        amplitude = math.pow(10.0, effect.eq_gain_db / 40.0)
        omega = 2.0 * math.pi * effect.eq_frequency_hz / sample_rate
        alpha = math.sin(omega) / (2.0 * effect.eq_q)
        cosine = math.cos(omega)
        a0 = 1.0 + alpha / amplitude
        self._b0 = (1.0 + alpha * amplitude) / a0
        self._b1 = (-2.0 * cosine) / a0
        self._b2 = (1.0 - alpha * amplitude) / a0
        self._a1 = (-2.0 * cosine) / a0
        self._a2 = (1.0 - alpha / amplitude) / a0
        self._z1 = np.zeros(2, dtype=np.float64)
        self._z2 = np.zeros(2, dtype=np.float64)

    def reset(self) -> None:
        self._z1.fill(0.0)
        self._z2.fill(0.0)

    def process_inplace(self, block: np.ndarray) -> None:
        for frame in range(len(block)):
            for channel in range(2):
                source = float(block[frame, channel])
                output = self._b0 * source + self._z1[channel]
                self._z1[channel] = (
                    self._b1 * source - self._a1 * output + self._z2[channel]
                )
                self._z2[channel] = self._b2 * source - self._a2 * output
                block[frame, channel] = np.float32(output)


class _CompressorProcessor(_EffectProcessor):
    def __init__(self, effect: StudioEffect, sample_rate: int) -> None:
        self._threshold_db = effect.compressor_threshold_db
        self._ratio = effect.compressor_ratio
        self._makeup_db = effect.compressor_makeup_db
        self._attack = _coefficient(effect.compressor_attack_ms, sample_rate)
        self._release = _coefficient(effect.compressor_release_ms, sample_rate)
        self._envelope = 0.0

    def reset(self) -> None:
        self._envelope = 0.0

    def process_inplace(self, block: np.ndarray) -> None:
        for frame in range(len(block)):
            level = max(
                abs(float(block[frame, 0])),
                abs(float(block[frame, 1])),
            )
            coefficient = self._attack if level > self._envelope else self._release
            self._envelope = (
                coefficient * self._envelope + (1.0 - coefficient) * level
            )
            level_db = 20.0 * math.log10(max(self._envelope, 1.0e-12))
            reduction_db = 0.0
            if level_db > self._threshold_db:
                reduction_db = (level_db - self._threshold_db) * (
                    1.0 - 1.0 / self._ratio
                )
            gain = _db_to_gain(self._makeup_db - reduction_db)
            block[frame, 0] = np.float32(float(block[frame, 0]) * gain)
            block[frame, 1] = np.float32(float(block[frame, 1]) * gain)


class _GateProcessor(_EffectProcessor):
    def __init__(self, effect: StudioEffect, sample_rate: int) -> None:
        self._threshold = _db_to_gain(effect.gate_threshold_db)
        self._attack = _coefficient(effect.gate_attack_ms, sample_rate)
        self._release = _coefficient(effect.gate_release_ms, sample_rate)
        self._gain = 0.0

    def reset(self) -> None:
        self._gain = 0.0

    def process_inplace(self, block: np.ndarray) -> None:
        for frame in range(len(block)):
            level = max(
                abs(float(block[frame, 0])),
                abs(float(block[frame, 1])),
            )
            target = 1.0 if level >= self._threshold else 0.0
            coefficient = self._attack if target > self._gain else self._release
            self._gain = coefficient * self._gain + (1.0 - coefficient) * target
            block[frame, 0] = np.float32(float(block[frame, 0]) * self._gain)
            block[frame, 1] = np.float32(float(block[frame, 1]) * self._gain)


class _ReverbProcessor(_EffectProcessor):
    def __init__(self, effect: StudioEffect, sample_rate: int) -> None:
        delay_frames = max(
            1,
            round(effect.reverb_delay_ms * sample_rate / 1_000.0),
        )
        self._delay = np.zeros((delay_frames, 2), dtype=np.float64)
        self._index = 0
        self._mix = effect.reverb_mix
        self._decay = effect.reverb_decay
        self._damping = effect.reverb_damping
        self._lowpass = np.zeros(2, dtype=np.float64)

    def reset(self) -> None:
        self._delay.fill(0.0)
        self._lowpass.fill(0.0)
        self._index = 0

    def process_inplace(self, block: np.ndarray) -> None:
        dry_gain = 1.0 - self._mix
        for frame in range(len(block)):
            for channel in range(2):
                delayed = float(self._delay[self._index, channel])
                lowpass = (
                    (1.0 - self._damping) * delayed
                    + self._damping * float(self._lowpass[channel])
                )
                self._lowpass[channel] = lowpass
                source = float(block[frame, channel])
                self._delay[self._index, channel] = (
                    source + lowpass * self._decay
                )
                block[frame, channel] = np.float32(
                    source * dry_gain + lowpass * self._mix
                )
            self._index += 1
            if self._index == len(self._delay):
                self._index = 0


def _processor(effect: StudioEffect, sample_rate: int) -> _EffectProcessor:
    if effect.kind is StudioEffectKind.HPF:
        return _HighPassProcessor(effect, sample_rate)
    if effect.kind is StudioEffectKind.EQ:
        return _EqualizerProcessor(effect, sample_rate)
    if effect.kind is StudioEffectKind.COMPRESSOR:
        return _CompressorProcessor(effect, sample_rate)
    if effect.kind is StudioEffectKind.GATE:
        return _GateProcessor(effect, sample_rate)
    if effect.kind is StudioEffectKind.REVERB:
        return _ReverbProcessor(effect, sample_rate)
    raise StudioMixerError("Studio effect kind is not supported.")


def studio_effect_tail_frames(document: StudioDocument) -> int:
    """Return the bounded shared-reverb tail represented by ``document``."""

    longest = 0
    for track in document.tracks:
        for effect in track.effects:
            if (
                not effect.enabled
                or effect.kind is not StudioEffectKind.REVERB
                or effect.reverb_mix <= 0.0
            ):
                continue
            delay = max(
                1,
                round(
                        effect.reverb_delay_ms
                        * document.project_sample_rate
                        / 1_000.0
                    ),
            )
            if effect.reverb_decay <= 0.0:
                repeats = 1
            else:
                repeats = max(
                    1,
                    math.ceil(
                        (MIXER_NOISE_FLOOR_DB / 20.0)
                        / math.log10(effect.reverb_decay)
                    ),
                )
            longest = max(longest, delay * repeats)
    return min(
        longest,
        int(MAX_REVERB_TAIL_SECONDS * document.project_sample_rate),
    )


@dataclass(frozen=True)
class StudioMixResult:
    """One final bus plus deterministic post-fader channel-strip outputs."""

    master: np.ndarray
    tracks: Mapping[str, np.ndarray]


class StudioMixEngine:
    """Stateful, contiguous-block schema-3 mixer."""

    def __init__(self, document: StudioDocument) -> None:
        if not isinstance(document, StudioDocument) or document.schema_version != 3:
            raise StudioMixerError("StudioMixEngine requires a schema-3 document.")
        self.document = document
        self.sample_rate = document.project_sample_rate
        self._tracks = {item.track_id: item for item in document.tracks}
        masters = tuple(
            item
            for item in document.tracks
            if item.kind is StudioTrackKind.MASTER
        )
        self._master_track_id = masters[0].track_id if masters else ""
        self._order = self._topological_order()
        self._processors = {
            track.track_id: tuple(
                _processor(effect, self.sample_rate)
                for effect in track.effects
                if effect.enabled
            )
            for track in document.tracks
        }
        self._automation: dict[
            str,
            dict[
                StudioAutomationParameter,
                tuple[StudioAutomationLane, np.ndarray, np.ndarray],
            ],
        ] = {}
        for track in document.tracks:
            compiled = {}
            for lane in track.automation:
                if not lane.enabled:
                    continue
                frames = np.fromiter(
                    (item.frame for item in lane.points),
                    dtype=np.int64,
                    count=len(lane.points),
                )
                values = np.fromiter(
                    (item.value for item in lane.points),
                    dtype=np.float64,
                    count=len(lane.points),
                )
                frames.flags.writeable = False
                values.flags.writeable = False
                compiled[lane.parameter] = (lane, frames, values)
            self._automation[track.track_id] = compiled
        self._expected_frame: int | None = None

    @property
    def expected_frame(self) -> int | None:
        return self._expected_frame

    @property
    def capability_id(self) -> str:
        return MIXER_CAPABILITY_ID

    def _topological_order(self) -> tuple[str, ...]:
        edges = {track_id: set() for track_id in self._tracks}
        indegree = {track_id: 0 for track_id in self._tracks}
        for track in self._tracks.values():
            targets = set()
            if track.output_bus_id:
                targets.add(track.output_bus_id)
            elif self._master_track_id and track.track_id != self._master_track_id:
                targets.add(self._master_track_id)
            targets.update(item.target_bus_id for item in track.sends)
            for target in targets:
                if target not in edges[track.track_id]:
                    edges[track.track_id].add(target)
                    indegree[target] += 1
        ready = sorted(
            (track_id for track_id, count in indegree.items() if count == 0),
            key=lambda track_id: (
                self._tracks[track_id].order,
                track_id,
            ),
        )
        ordered: list[str] = []
        while ready:
            track_id = ready.pop(0)
            ordered.append(track_id)
            for target in sorted(
                edges[track_id],
                key=lambda item: (self._tracks[item].order, item),
            ):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    ready.sort(
                        key=lambda item: (self._tracks[item].order, item)
                    )
        if len(ordered) != len(self._tracks):
            raise StudioMixerError("Studio routing graph contains a cycle.")
        return tuple(ordered)

    def reset(self) -> None:
        for processors in self._processors.values():
            for processor in processors:
                processor.reset()
        self._expected_frame = None

    @staticmethod
    def _cancel(cancel_check: Callable[[], None] | None) -> None:
        if cancel_check is not None:
            cancel_check()

    def _lane_values(
        self,
        track_id: str,
        parameter: StudioAutomationParameter,
        start_frame: int,
        frame_count: int,
    ) -> np.ndarray | None:
        compiled = self._automation[track_id].get(parameter)
        if compiled is None:
            return None
        lane, frames, values = compiled
        return _automation_values(
            lane,
            frames,
            values,
            start_frame,
            frame_count,
        )

    def _channel_strip(
        self,
        track: StudioTrack,
        state: StudioTrack,
        source: np.ndarray,
        start_frame: int,
        cancel_check: Callable[[], None] | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        pre_fader = source
        pre_fader *= np.float32(state.trim_gain)
        for processor in self._processors[track.track_id]:
            self._cancel(cancel_check)
            processor.process_inplace(pre_fader)
        post_fader = pre_fader.copy()
        count = len(post_fader)

        volume = self._lane_values(
            track.track_id,
            StudioAutomationParameter.VOLUME,
            start_frame,
            count,
        )
        if volume is None:
            post_fader *= np.float32(state.fader_gain)
        else:
            post_fader *= volume[:, np.newaxis]

        pan = self._lane_values(
            track.track_id,
            StudioAutomationParameter.PAN,
            start_frame,
            count,
        )
        if pan is None:
            if state.pan < 0.0:
                post_fader[:, 1] *= np.float32(1.0 + state.pan)
            elif state.pan > 0.0:
                post_fader[:, 0] *= np.float32(1.0 - state.pan)
        else:
            left = np.where(pan > 0.0, 1.0 - pan, 1.0).astype(np.float32)
            right = np.where(pan < 0.0, 1.0 + pan, 1.0).astype(np.float32)
            post_fader[:, 0] *= left
            post_fader[:, 1] *= right

        mute = self._lane_values(
            track.track_id,
            StudioAutomationParameter.MUTE,
            start_frame,
            count,
        )
        if mute is None:
            if state.muted:
                post_fader.fill(0.0)
        else:
            post_fader[mute >= np.float32(0.5)] = np.float32(0.0)
        return pre_fader, post_fader

    def process_block(
        self,
        *,
        start_frame: int,
        frame_count: int,
        raw_tracks: Mapping[str, np.ndarray],
        track_states: Mapping[str, StudioTrack],
        master: StudioMaster,
        apply_master: bool = True,
        cancel_check: Callable[[], None] | None = None,
    ) -> StudioMixResult:
        """Process one contiguous block without song-length allocation.

        A caller that receives any exception must call :meth:`reset` before
        retrying; :class:`StudioRenderStream` enforces that rollback contract.
        """

        if (
            isinstance(start_frame, bool)
            or not isinstance(start_frame, int)
            or not -MAX_PROJECT_FRAMES <= start_frame <= MAX_PROJECT_FRAMES
        ):
            raise StudioMixerError("Mixer start_frame must be an integer.")
        if (
            isinstance(frame_count, bool)
            or not isinstance(frame_count, int)
            or frame_count <= 0
            or frame_count > MAX_MIXER_BLOCK_FRAMES
            or start_frame + frame_count > MAX_PROJECT_FRAMES
        ):
            raise StudioMixerError("Mixer frame_count must be a positive integer.")
        if self._expected_frame is not None and start_frame != self._expected_frame:
            raise StudioMixerError(
                "Mixer blocks must be contiguous; reset before a seek."
            )
        if not isinstance(master, StudioMaster):
            raise StudioMixerError("Mixer master must be a StudioMaster.")
        if not isinstance(apply_master, bool):
            raise StudioMixerError("apply_master must be true or false.")
        if cancel_check is not None and not callable(cancel_check):
            raise StudioMixerError("cancel_check must be callable or null.")
        if set(track_states) != set(self._tracks):
            raise StudioMixerError("Mixer track states do not match the document.")
        if frame_count * max(1, len(self._tracks)) > MAX_MIXER_TRACK_BLOCK_FRAMES:
            raise StudioMixerError(
                "Mixer block is too large for this track count."
            )
        unknown_raw = set(raw_tracks).difference(self._tracks)
        if unknown_raw:
            raise StudioMixerError("Mixer received audio for an unknown track.")
        for track_id, block in raw_tracks.items():
            _finite_stereo(block, frame_count, f"raw track {track_id}")

        inputs = {
            track_id: np.zeros((frame_count, 2), dtype=np.float32)
            for track_id in self._tracks
        }
        for track_id, block in raw_tracks.items():
            inputs[track_id] += block
        output = np.zeros((frame_count, 2), dtype=np.float32)
        processed: dict[str, np.ndarray] = {}
        any_solo = any(
            state.solo
            for track_id, state in track_states.items()
            if self._tracks[track_id].kind
            in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING}
        )

        for track_id in self._order:
            self._cancel(cancel_check)
            track = self._tracks[track_id]
            state = track_states[track_id]
            if state.track_id != track_id:
                raise StudioMixerError("Mixer track state identity changed.")
            source = inputs[track_id]
            if (
                track.kind in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING}
                and any_solo
                and not state.solo
            ):
                source.fill(0.0)
            pre_fader, post_fader = self._channel_strip(
                track,
                state,
                source,
                start_frame,
                cancel_check,
            )
            if not np.all(np.isfinite(post_fader)):
                raise StudioMixerError(
                    "Studio channel strip produced non-finite audio."
                )
            processed[track_id] = post_fader

            if track.track_id == self._master_track_id:
                output += post_fader
            elif track.output_bus_id:
                inputs[track.output_bus_id] += post_fader
            elif self._master_track_id:
                inputs[self._master_track_id] += post_fader
            else:
                output += post_fader
            for send in track.sends:
                if not send.enabled or send.gain <= 0.0:
                    continue
                tap = pre_fader if send.pre_fader else post_fader
                inputs[send.target_bus_id] += tap * np.float32(send.gain)

        if apply_master:
            output *= np.float32(master.gain)
            if master.limiter_enabled:
                np.clip(output, -1.0, 1.0, out=output)
        if not np.all(np.isfinite(output)):
            raise StudioMixerError("Studio master produced non-finite audio.")
        self._expected_frame = start_frame + frame_count
        return StudioMixResult(master=output, tracks=processed)


__all__ = [
    "MAX_MIXER_BLOCK_FRAMES",
    "MAX_MIXER_TRACK_BLOCK_FRAMES",
    "MAX_REALTIME_EFFECT_UNITS_48K",
    "MAX_REVERB_TAIL_SECONDS",
    "MIXER_CAPABILITY_ID",
    "MIXER_NOISE_FLOOR_DB",
    "StudioMixEngine",
    "StudioMixResult",
    "StudioMixerCapability",
    "StudioMixerError",
    "automation_values",
    "studio_effect_tail_frames",
    "studio_mixer_capability",
]
