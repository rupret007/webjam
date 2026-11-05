"""Bridge-side adaptive latency: Jamulus ini defaults + measured policy loop."""

from __future__ import annotations

import logging
import copy
import math
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from core.jamulus_adaptive_latency import (
    DEFAULT_FRAME_FACTOR,
    AdaptiveLatencyPolicy,
    LatencyObservation,
    ParticipantDelayPresentation,
    apply_frame_factor_to_profile,
    apply_latency_defaults_to_profile,
    classify_delay,
    estimate_overall_delay_ms,
    latency_defaults_for_settings,
    read_client_ini_values,
)
from core.jamulus_adaptive_latency import DelayStatus
from core.jamulus_rpc_client import JamulusRpcMonitorIdentity

if TYPE_CHECKING:
    from services.bridge_service import BridgeService

_logger = logging.getLogger("webjam.adaptive_latency.controller")
LATENCY_OBSERVATION_MAX_AGE_S = 8.0


class AdaptiveLatencyController:
    def __init__(self, bridge: BridgeService) -> None:
        self._bridge = bridge
        self._lock = threading.RLock()
        self._policy = AdaptiveLatencyPolicy()
        self._ping_ms: float | None = None
        self._ping_at: float | None = None
        self._remote_jitter_blocks: dict[int, int] = {}
        self._local_channel_id: int | None = None
        self._factor_known = False
        self._launch_frame_factor = DEFAULT_FRAME_FACTOR
        self._profile_path: Path | None = None
        self._rpc = None
        self._rpc_source = None
        self._binding_generation = 0
        self._tuning_failed = False

    def _retire_binding_locked(self, *, clear_launch: bool) -> None:
        self._binding_generation += 1
        if self._rpc is not None:
            self._rpc.set_server_info_listener(None)
        self._rpc = None
        self._rpc_source = None
        self._ping_ms = None
        self._ping_at = None
        self._remote_jitter_blocks = {}
        self._local_channel_id = None
        if clear_launch:
            self._factor_known = False
            self._launch_frame_factor = DEFAULT_FRAME_FACTOR
            self._profile_path = None
            self._tuning_failed = False
        self._policy = AdaptiveLatencyPolicy(
            enabled=self._policy.enabled,
            player_override=self._policy.player_override,
            current_factor=(self._launch_frame_factor if clear_launch else self._policy.current_factor),
        )

    def stop(self) -> None:
        """Retire measurements before the owned Jamulus monitor stops."""
        with self._lock:
            self._retire_binding_locked(clear_launch=True)

    def configure_from_settings(self) -> None:
        settings = self._bridge.settings
        enabled = bool(getattr(settings, "adaptive_latency_enabled", True))
        override = bool(getattr(settings, "adaptive_latency_player_override", False))
        with self._lock:
            self._policy.enabled = enabled
            self._policy.player_override = override

    def apply_launch_defaults(self, profile_path: Path) -> list[str]:
        """Fill unset Jamulus latency ini keys before the client starts."""

        with self._lock:
            self._retire_binding_locked(clear_launch=True)
            return self._apply_launch_defaults_locked(profile_path)

    def _apply_launch_defaults_locked(self, profile_path: Path) -> list[str]:

        self.configure_from_settings()
        with self._lock:
            if not self._policy.enabled or self._policy.player_override:
                return []
        defaults = latency_defaults_for_settings(self._bridge.settings)
        try:
            written = apply_latency_defaults_to_profile(Path(profile_path), defaults=defaults)
            values = read_client_ini_values(Path(profile_path).read_bytes())
            factor = int(values.get("prefsndcrdbufidx", ""))
        except (ValueError, ElementTree.ParseError):
            # Optional tuning cannot repair or replace another profile format.
            with self._lock:
                self._factor_known = False
                self._policy.enabled = False
            return []
        if written:
            _logger.info(
                "Applied Jamulus latency defaults to unset keys: %s",
                ", ".join(sorted(written)),
            )
        with self._lock:
            self._factor_known = factor in (1, 2, 4)
            if self._factor_known:
                self._policy.current_factor = factor
                self._launch_frame_factor = factor
                self._profile_path = Path(profile_path)
            else:
                self._policy.enabled = False
        return written

    def note_server_ping(self, ping_ms: float | None) -> None:
        try:
            value = float(ping_ms)
        except (TypeError, ValueError):
            value = None
        if value is not None and (not math.isfinite(value) or value <= 0):
            value = None
        with self._lock:
            self._ping_ms = value
            self._ping_at = time.monotonic() if value is not None else None
            self._run_policy_tick()

    def note_local_channel_id(self, channel_id: int | None) -> None:
        with self._lock:
            self._local_channel_id = channel_id if channel_id is not None else None

    def note_server_clients(self, clients: list[dict] | None) -> None:
        if not isinstance(clients, list):
            return
        mapping: dict[int, int] = {}
        for row in clients:
            if not isinstance(row, dict):
                continue
            try:
                cid = int(row.get("id"))
                jitter = int(row.get("jitterBufferSize"))
            except (TypeError, ValueError):
                continue
            if cid >= 0 and jitter >= 0:
                mapping[cid] = jitter
        with self._lock:
            self._remote_jitter_blocks = mapping

    def bind_jamulus_controller(self, controller, *, source=None, server_address=None) -> None:
        rpc = getattr(controller, "rpc_client", None)
        if rpc is None:
            return
        with self._lock:
            snapshot = rpc.monitor_snapshot()
            expected = source if source is not None else snapshot.identity
            if (not isinstance(expected, JamulusRpcMonitorIdentity)
                    or not expected.is_process_bound or expected.monitor_epoch <= 0
                    or not snapshot.running or snapshot.identity != expected):
                return
            self._retire_binding_locked(clear_launch=False)
            self._rpc = rpc
            self._rpc_source = expected
            generation = self._binding_generation

            def receive_ping(ping_ms, observed_source):
                with self._lock:
                    if generation != self._binding_generation or observed_source != expected:
                        return
                    current = rpc.monitor_snapshot()
                    if (not current.running or not current.available or not current.authenticated
                            or current.identity != expected or not rpc.server_ping_active):
                        return
                    self.note_server_ping(ping_ms)

            rpc.set_server_info_listener(receive_ping, server_address=server_address)

    def participant_delays(self) -> dict[int, ParticipantDelayPresentation]:
        with self._lock:
            ping = self._ping_ms
            factor = self._launch_frame_factor
            jitter_map = dict(self._remote_jitter_blocks)
            local_id = self._local_channel_id
            factor_known = self._factor_known
            if (self._ping_at is None
                    or time.monotonic() - self._ping_at > LATENCY_OBSERVATION_MAX_AGE_S):
                ping = None
            if self._rpc is not None:
                snapshot = self._rpc.monitor_snapshot()
                if (not snapshot.running or not snapshot.available or not snapshot.authenticated
                        or snapshot.identity != self._rpc_source or not self._rpc.server_ping_active):
                    ping = None

        results: dict[int, ParticipantDelayPresentation] = {}
        if local_id is not None:
            jitter = jitter_map.get(local_id)
            overall = estimate_overall_delay_ms(
                ping_ms=ping if factor_known else None,
                frame_factor=factor,
                jitter_buffer_blocks=jitter,
            )
            results[local_id] = ParticipantDelayPresentation(
                channel_id=local_id,
                overall_delay_ms=overall,
                status=classify_delay(overall),
                detail=_delay_detail(overall, ping, jitter),
            )
        for channel_id, jitter in jitter_map.items():
            if channel_id in results:
                continue
            overall = estimate_overall_delay_ms(
                ping_ms=None,
                frame_factor=DEFAULT_FRAME_FACTOR,
                jitter_buffer_blocks=jitter,
            )
            results[channel_id] = ParticipantDelayPresentation(
                channel_id=channel_id,
                overall_delay_ms=overall,
                status=classify_delay(overall) if overall is not None else DelayStatus.UNKNOWN,
                detail=f"Server jitter buffer {jitter} block(s)",
            )
        return results

    def _run_policy_tick(self) -> None:
        with self._lock:
            if self._tuning_failed:
                return
            self.configure_from_settings()
            plan = self._bridge.native_profile_plan
            profile_path = getattr(plan, "profile_path", None) if plan is not None else None
            if (not self._factor_known or profile_path is None
                    or Path(profile_path) != self._profile_path):
                return
            observation = LatencyObservation(ping_ms=self._ping_ms)
            previous = copy.copy(self._policy)
            new_factor = self._policy.decide(observation)
            if new_factor is None:
                return
            try:
                written = apply_frame_factor_to_profile(Path(profile_path), new_factor)
            except (OSError, ValueError, ElementTree.ParseError) as error:
                self._policy = previous
                self._policy.enabled = False
                self._tuning_failed = True
                _logger.warning("Optional latency tuning stopped (%s).", type(error).__name__)
                return
            if written:
                _logger.info(
                    "Adaptive latency stepped prefsndcrdbufidx to %s (%s samples). "
                    "Jamulus 3.12.2 has no JSON-RPC buffer setter; reconnect or adjust "
                    "in Jamulus Settings to apply live.",
                    new_factor,
                    {1: 64, 2: 128, 4: 256}.get(new_factor, "?"),
                )
            else:
                self._policy = previous


def _delay_detail(
    overall: float | None,
    ping: float | None,
    jitter_blocks: int | None,
) -> str:
    parts: list[str] = []
    if ping is not None:
        parts.append(f"ping {ping:.0f} ms")
    if jitter_blocks is not None:
        parts.append(f"jitter buffer {jitter_blocks} block(s)")
    if overall is not None:
        parts.append(f"≈ {overall:.0f} ms overall")
    return " · ".join(parts)
