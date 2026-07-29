"""Privacy-safe diagnostics adapter for the Qt application.

``DiagnosticsExporter`` translates live Qt/controller objects into the small,
explicit allowlist accepted by :mod:`core.support_bundle`.  Clipboard text,
preview, structured JSON, and ZIP export all come from one cached artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import json
import logging
import platform
from pathlib import Path
from typing import Any

from core.redaction import (
    REDACTED_FIELDS as _REDACTED_FIELDS,  # noqa: F401 - compatibility re-export
    REDACTED_NAME_HINTS as _REDACTED_NAME_HINTS,  # noqa: F401 - compatibility re-export
    redact_mapping,
    redact_text,
)
from core.support_bundle import (
    SupportBundleArtifact,
    SupportBundlePreview,
    SupportFacts,
    build_support_bundle,
)

LOGGER = logging.getLogger("webjam.qt.diagnostics")

_LOG_TAIL_LINES = 30


class DiagnosticsExporter:
    """Build one privacy-safe support snapshot from live app state."""

    def __init__(
        self,
        settings: Any,
        bridge: Any,
        jamulus_controller: Any,
        window_version: str,
        *,
        build_id: str = "",
        jamulus_version: str = "",
        session_health: Any = None,
        session_lifecycle: Any = None,
        musician_guidance: Any = None,
        recording_coordinator: Any = None,
        metrics_service: Any = None,
        jamulus_update: Any = None,
        webex_app: Any = None,
    ) -> None:
        self.settings = settings
        self.bridge = bridge
        self.jamulus = jamulus_controller
        self.window_version = window_version
        self.build_id = build_id
        self.jamulus_version = jamulus_version
        self.session_health = session_health
        self.session_lifecycle = session_lifecycle
        self.musician_guidance = musician_guidance
        self.recording = recording_coordinator
        self.metrics = metrics_service
        self.jamulus_update = jamulus_update
        self.webex_app = webex_app
        self._artifact_cache: SupportBundleArtifact | None = None

    def build_summary(self) -> str:
        """Return the exact human report stored as ``README.txt`` in the ZIP."""

        return self.artifact().copy_text

    def build_preview(self) -> SupportBundlePreview:
        """Return a preview of the exact logical report and archive members."""

        return self.artifact().preview()

    def save_bundle(self, output_dir: Path, filename: str | None = None) -> Path:
        """Save the same snapshot used by ``build_summary`` and preview."""

        return self.artifact().save_zip(output_dir, filename)

    def save_structured_report(
        self, output_dir: Path, filename: str | None = None
    ) -> Path:
        return self.artifact().save_structured_json(output_dir, filename)

    def artifact(self) -> SupportBundleArtifact:
        if self._artifact_cache is None:
            self._artifact_cache = self._build_artifact()
        return self._artifact_cache

    def refresh(self) -> None:
        """Discard the snapshot so the next explicit action samples live state."""

        self._artifact_cache = None

    def _build_artifact(self) -> SupportBundleArtifact:
        diagnostics = self._audio_diagnostics()
        engine_capabilities: dict[str, Any] = {}
        for source_name, report_name in (
            ("backend", "backend"),
            ("active", "active"),
            ("latency_mode", "latency_mode"),
            ("blocksize", "block_size"),
        ):
            value = _plain_value(getattr(diagnostics, source_name, None))
            if value is not None:
                engine_capabilities[report_name] = value

        rpc = getattr(self.jamulus, "rpc_client", None)
        rpc_available = _plain_value(getattr(rpc, "available", None))
        if isinstance(rpc_available, bool):
            engine_capabilities["rpc_available"] = rpc_available
        try:
            age = rpc.last_activity_age() if rpc is not None else None
        except Exception:  # noqa: BLE001 - diagnostics must remain best-effort
            age = None
        if isinstance(age, (int, float)) and not isinstance(age, bool):
            engine_capabilities["rpc_last_activity_age_s"] = max(0.0, float(age))

        sample_rate = _plain_value(getattr(diagnostics, "samplerate", None))
        reconnect_attempts = _plain_value(
            getattr(self.bridge, "jamulus_reconnect_attempts", None)
        )
        metric_values = self._metric_values()
        reconnect_counts: dict[str, int] = {}
        if isinstance(reconnect_attempts, int) and not isinstance(
            reconnect_attempts, bool
        ):
            reconnect_counts["attempts"] = max(0, reconnect_attempts)
        for field, metric_name in (
            ("succeeded", "metric_jamulus_reconnect_success"),
            ("failed", "metric_jamulus_reconnect_failed"),
        ):
            value = _metric_count(metric_values, metric_name)
            if value:
                reconnect_counts[field] = value

        channels: dict[str, int] = {}
        local_capture_enabled = _plain_value(
            getattr(self.settings, "local_capture_enabled", None)
        )
        if isinstance(local_capture_enabled, bool):
            channels["recorded"] = 2 if local_capture_enabled else 0
        recorder_health = self._recorder_health()
        process_cleanup, port_cleanup = self._cleanup_snapshot()
        export_counts = {
            "succeeded": _metric_count(
                metric_values, "metric_diagnostics_bundle_exported"
            ),
            "failed": _metric_count(
                metric_values, "metric_diagnostics_bundle_failed"
            ),
        }
        export_counts = {key: value for key, value in export_counts.items() if value}

        facts = SupportFacts(
            webjam_version=self.window_version,
            build_id=self.build_id,
            os_name=f"{platform.system()} {platform.release()}".strip(),
            architecture=platform.machine(),
            jamulus_version=self.jamulus_version or self._jamulus_version(),
            jamulus_state=str(
                _plain_value(getattr(self.bridge, "jamulus_state", "")) or "unknown"
            ),
            jamulus_update=_public_mapping(self.jamulus_update),
            webex_app=_public_mapping(self.webex_app),
            musician_guidance=self._musician_guidance(),
            session_transitions=self._session_transitions(),
            engine_capabilities=engine_capabilities,
            sample_rate_hz=(
                sample_rate
                if isinstance(sample_rate, (int, float))
                and not isinstance(sample_rate, bool)
                else None
            ),
            channels=channels,
            recorder_health=recorder_health,
            reconnect_counts=reconnect_counts,
            export_counts=export_counts,
            process_cleanup=process_cleanup,
            port_cleanup=port_cleanup,
        )
        return build_support_bundle(facts, log_excerpts=self._log_excerpts())

    def _session_transitions(self) -> tuple[dict[str, str], ...]:
        """Return only the lifecycle's explicit, allowlisted timeline."""

        lifecycle = self.session_lifecycle
        timeline = getattr(lifecycle, "public_timeline", None)
        if not callable(timeline):
            return ()
        try:
            values = timeline()
        except Exception:  # noqa: BLE001 - diagnostics remains best effort
            return ()
        if not isinstance(values, tuple):
            return ()
        return tuple(value for value in values if isinstance(value, dict))

    def _musician_guidance(self) -> dict[str, Any]:
        public = getattr(self.musician_guidance, "to_public_dict", None)
        if not callable(public):
            return {}
        try:
            value = public()
        except Exception:  # noqa: BLE001 - diagnostics remains best effort
            return {}
        return dict(value) if isinstance(value, dict) else {}

    def _metric_values(self) -> dict[str, Any]:
        try:
            values = self.metrics.collect() if self.metrics is not None else {}
        except Exception:  # noqa: BLE001 - support export remains best-effort
            return {}
        return dict(values) if isinstance(values, dict) else {}

    def _recorder_health(self) -> dict[str, Any]:
        recorder = self.recording
        if recorder is None:
            return {}
        result: dict[str, Any] = {}
        phase = getattr(recorder, "phase", None)
        phase_value = _plain_value(getattr(phase, "value", phase))
        if isinstance(phase_value, str):
            result["state"] = phase_value
        snapshot = getattr(recorder, "snapshot", None)
        for name in ("armed", "recording"):
            value = _plain_value(getattr(snapshot, name, None))
            if isinstance(value, bool):
                result[name] = value

        validation = getattr(recorder, "last_validation", None)
        if validation is not None:
            take = getattr(validation, "take", None)
            errors = getattr(validation, "errors", ())
            result["finalized"] = True
            result["reopened"] = bool(take is not None)
            if take is not None:
                duration = _plain_value(getattr(take, "duration_s", None))
                if isinstance(duration, (int, float)) and not isinstance(
                    duration, bool
                ):
                    result["duration_seconds"] = max(0.0, float(duration))
                tracks = list(getattr(take, "tracks", ()) or ())
                rates = {
                    int(rate)
                    for track in tracks
                    if isinstance(
                        (rate := _plain_value(getattr(track, "samplerate", None))),
                        int,
                    )
                    and not isinstance(rate, bool)
                    and rate > 0
                }
                if len(rates) == 1:
                    result["sample_rate_hz"] = next(iter(rates))
                result["channels"] = sum(
                    max(
                        0,
                        int(
                            _plain_value(getattr(track, "channels", 0)) or 0
                        ),
                    )
                    for track in tracks
                    if isinstance(
                        _plain_value(getattr(track, "channels", 0)), int
                    )
                )
            if errors:
                result["last_error"] = "take_needs_attention"
        return result

    def _cleanup_snapshot(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        processes: list[dict[str, Any]] = []
        for component, attribute in (
            ("music_engine", "jamulus_process"),
            ("band_server", "hosted_server_process"),
        ):
            process = getattr(self.bridge, attribute, None)
            poll = getattr(process, "poll", None)
            live = bool(process is not None and callable(poll) and poll() is None)
            processes.append(
                {
                    "component": component,
                    "status": "running" if live else "released",
                    "owned": process is not None,
                }
            )

        ports: list[dict[str, Any]] = []
        port_free = getattr(self.bridge, "_port_free", None)
        if callable(port_free):
            for component, setting_name, protocol in (
                ("music_engine_control", "jamulus_rpc_port", "tcp"),
                ("band_server_control", "server_rpc_port", "tcp"),
                ("band_audio", "jamulus_port", "udp"),
            ):
                raw_port = _plain_value(getattr(self.settings, setting_name, None))
                if not isinstance(raw_port, int) or isinstance(raw_port, bool):
                    continue
                try:
                    released = bool(port_free(raw_port, udp=protocol == "udp"))
                except Exception:  # noqa: BLE001 - snapshot is optional evidence
                    continue
                ports.append(
                    {
                        "component": component,
                        "port": raw_port,
                        "protocol": protocol,
                        "status": "released" if released else "active",
                        "in_use": not released,
                        "released": released,
                    }
                )
        return processes, ports

    def _audio_diagnostics(self) -> Any:
        try:
            return self.jamulus.audio_engine.diagnostics()
        except Exception:  # noqa: BLE001 - report generation must never crash UI
            return None

    def _jamulus_version(self) -> str:
        value = _plain_value(getattr(self.bridge, "jamulus_version", ""))
        return value if isinstance(value, str) else ""

    def _log_excerpts(self) -> dict[str, str]:
        home = Path.home()
        candidates = {
            "webjam": home / ".webjam.log",
            "jamulus": home / ".webjam_jamulus.log",
            "jamulus_server": home
            / "Library"
            / "Logs"
            / "WebJam"
            / "jamulus-server.log",
        }
        excerpts: dict[str, str] = {}
        for name, path in candidates.items():
            lines = self._tail_log(path, unavailable_marker=False)
            if lines:
                excerpts[name] = "\n".join(lines)
        return excerpts

    def _tail_log(
        self, path: Path, *, unavailable_marker: bool = True
    ) -> list[str]:
        """Return a bounded, redacted tail without exposing the source path."""

        if path.is_symlink():
            return ["(log file unavailable)"] if unavailable_marker else []
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 128 * 1024))
                raw = handle.read()
        except OSError:
            return ["(log file unavailable)"] if unavailable_marker else []
        except Exception:  # noqa: BLE001
            LOGGER.debug("Unexpected log read failure", exc_info=True)
            return ["(log file unavailable)"] if unavailable_marker else []
        if _looks_binary(raw):
            return ["(log file unavailable)"] if unavailable_marker else []
        text = raw.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        tail = all_lines[-_LOG_TAIL_LINES:] if all_lines else ["(empty log)"]
        return [redact_text(line) for line in tail]

    def _sanitised_settings_json(self) -> str:
        """Compatibility helper; settings are never included in support output."""

        try:
            data = asdict(self.settings)
        except TypeError:
            data = {
                key: getattr(self.settings, key)
                for key in dir(self.settings)
                if not key.startswith("_")
                and not callable(getattr(self.settings, key))
            }
        try:
            return json.dumps(redact_mapping(data), indent=2, default=str, sort_keys=True)
        except Exception:  # noqa: BLE001
            return "(settings unavailable)"


def _plain_value(value: Any) -> str | int | float | bool | None:
    """Reject mocks, paths, and arbitrary objects at the controller boundary."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _public_mapping(value: Any) -> dict[str, Any]:
    """Copy only a mapping; the core support schema applies its own allowlist."""

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _metric_count(values: dict[str, Any], name: str) -> int:
    value = _plain_value(values.get(name))
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _looks_binary(payload: bytes) -> bool:
    audio_magic = (b"RIFF", b"FORM", b"fLaC", b"OggS", b"ID3", b"caff")
    if payload.startswith(audio_magic) or b"\x00" in payload[:4_096]:
        return True
    sample = payload[:4_096]
    if not sample:
        return False
    controls = sum(byte < 32 and byte not in (9, 10, 13) for byte in sample)
    return controls / len(sample) >= 0.02
