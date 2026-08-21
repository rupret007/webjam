from __future__ import annotations

import os
import platform
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.redaction import redact_mapping, redact_text
from core.support_bundle import (
    SupportBundleArtifact,
    SupportFacts,
    build_support_bundle,
)


class RetryService:
    @staticmethod
    def retry_action(action: Callable[[], Any], attempts: int = 3, base_delay: float = 0.4) -> Any:
        """Retry an action on exception; callable should raise on failure."""
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        if base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return action()
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(base_delay * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        return None


class MetricsService:
    METRIC_KEYS = [
        "metric_setup_wizard_opened",
        "metric_setup_wizard_completed",
        "metric_jamulus_launch_attempt",
        "metric_jamulus_launch_success",
        "metric_jamulus_launch_failed",
        "metric_jamulus_reconnect_attempt",
        "metric_jamulus_reconnect_success",
        "metric_jamulus_reconnect_failed",
        "metric_webex_open_attempt",
        "metric_webex_open_success",
        "metric_webex_open_failed",
        "metric_webex_reconnect_attempt",
        "metric_webex_reconnect_success",
        "metric_webex_reconnect_failed",
        "metric_diagnostics_panel_opened",
        "metric_audio_diagnostics_opened",
        "metric_ready_check_run",
        "metric_diagnostics_bundle_exported",
        "metric_diagnostics_bundle_failed",
        "metric_session_brief_exported",
        "metric_session_brief_failed",
        "metric_listening_profile_save_success",
        "metric_listening_profile_save_failed",
        "metric_listening_profile_load_success",
        "metric_listening_profile_load_failed",
        "metric_listening_profile_delete_success",
        "metric_listening_profile_delete_failed",
        "metric_save_mix_attempt",
        "metric_save_mix_success",
        "metric_save_mix_failed",
        "metric_load_mix_attempt",
        "metric_load_mix_success",
        "metric_load_mix_failed",
        # v0.4.5 — incremented in code but were missing from this list,
        # so they didn't appear in usage-metrics dialogs / exports.
        "metric_jamulus_stop",
        "metric_jamulus_port_conflict",
        "metric_webex_leave",
        "metric_session_completed",
        # v0.4.7 — round-4 telemetry audit additions.
        "metric_jamulus_hang_detected",
        "metric_audio_device_blackhole_found",
        "metric_audio_device_missing",
        "metric_mix_corruption_recovered",
        "metric_session_started",
    ]

    def __init__(self, repository: Any):
        self.repository = repository

    def increment(self, key: str) -> int:
        return self.repository.increment_setting(key)

    def collect(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for key in self.METRIC_KEYS:
            values[key] = self.repository.get_setting(key, "0") or "0"
        for key, value in self.repository.list_settings().items():
            if key.startswith("metric_") and key not in values:
                values[key] = value
        return values

    def reset_with_prefix(self, prefix: str = "metric_") -> None:
        for key in self.repository.list_settings():
            if key.startswith(prefix):
                self.repository.delete_setting(key)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            safe = {
                str(k): MetricsService._json_safe(v) for k, v in value.items()
            }
            return redact_mapping(safe)
        if isinstance(value, (list, tuple, set)):
            return [MetricsService._json_safe(item) for item in value]
        return str(value)

    @staticmethod
    def _normalize_file_candidates(candidates: Iterable[os.PathLike[str] | str]) -> list[Path]:
        normalized: list[Path] = []
        for raw in candidates:
            if raw is None:
                continue
            try:
                candidate = Path(raw).expanduser()
            except (TypeError, ValueError):
                continue
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _copy_files(
        sources: Iterable[Path],
        destination: Path,
        missing_sink: list[str],
        *,
        redact: bool = False,
    ) -> list[str]:
        copied: list[str] = []
        name_counts: dict[str, int] = {}
        for source in sources:
            if not source.is_file():
                missing_sink.append(str(source))
                continue
            base_name = source.name or "attachment"
            duplicate_index = name_counts.get(base_name, 0)
            name_counts[base_name] = duplicate_index + 1
            if duplicate_index:
                stem = source.stem or "attachment"
                suffix = source.suffix
                target_name = f"{stem}_{duplicate_index}{suffix}"
            else:
                target_name = base_name
            target = destination / target_name
            if redact:
                try:
                    text = source.read_text(encoding="utf-8", errors="replace")
                    target.write_text(redact_text(text), encoding="utf-8")
                except OSError:
                    shutil.copy2(source, target)
            else:
                shutil.copy2(source, target)
            copied.append(str(source))
        return copied

    def export_snapshot(
        self,
        home_dir: Path,
        jamulus_state: str,
        webex_state: str,
        latency_ms: float | None,
        server: str,
        webex_url: str,
        audio_diagnostics: dict[str, Any],
    ) -> Path:
        # Webex destinations and the configured server are deliberately not
        # forwarded.  They are private meeting/session information, not facts
        # needed to diagnose the production audio engine.
        _ = (webex_state, server, webex_url)
        artifact = self._build_support_artifact(
            jamulus_state=jamulus_state,
            latency_ms=latency_ms,
            audio_diagnostics=audio_diagnostics,
        )
        return artifact.save_structured_json(Path(home_dir))

    def export_session_brief(
        self,
        output_dir: Path,
        room_context: dict[str, Any],
        artifacts: Iterable[dict[str, Any]],
        notes: str,
        participants: Iterable[Any],
        mode_label: str = "",
    ) -> Path:
        output_dir = Path(output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise NotADirectoryError(f"Session brief output path is not a directory: {output_dir}") from exc
        if not output_dir.is_dir():
            raise NotADirectoryError(f"Session brief output path is not a directory: {output_dir}")

        created_at = datetime.now(timezone.utc)
        stamp = created_at.strftime("%Y%m%d_%H%M%S")
        brief_path = output_dir / f"webjam_session_brief_{stamp}.md"

        context = room_context if isinstance(room_context, dict) else {}
        mode_key = str(context.get("mode_key", "") or "").strip() or "music_jam"
        template_name = str(context.get("template_name", "") or "").strip() or "Untitled session"
        session_goal = str(context.get("session_goal", "") or "").strip() or "(no goal recorded)"
        review_state = str(context.get("review_state", "") or "").strip().lower() or "draft"
        mode_text = mode_label.strip() if isinstance(mode_label, str) else ""
        if not mode_text:
            mode_text = mode_key.replace("_", " ").title()

        participant_list: list[str] = []
        seen_participant_ids: set[str] = set()
        for participant in participants:
            participant_id: str | None = None
            participant_name: object = participant
            if isinstance(participant, dict):
                participant_name = participant.get("name", participant)
                raw_id = participant.get("channel_id", participant.get("id"))
                if raw_id is not None:
                    participant_id = str(raw_id)
            else:
                participant_name = getattr(participant, "name", participant)
                raw_id = getattr(participant, "channel_id", getattr(participant, "id", None))
                if raw_id is not None:
                    participant_id = str(raw_id)
            if participant_id is not None:
                if participant_id in seen_participant_ids:
                    continue
                seen_participant_ids.add(participant_id)
            participant_text = str(participant_name).strip()
            if participant_text:
                participant_list.append(participant_text)

        artifact_lines: list[str] = []
        artifact_count = 0
        for raw in artifacts:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "") or "").strip() or "Untitled"
            artifact_type = str(raw.get("artifact_type", "") or "").strip().lower() or "note"
            reference = str(raw.get("reference", "") or "").strip()
            if reference:
                artifact_lines.append(f"- [{artifact_type}] {title}: {reference}")
            else:
                artifact_lines.append(f"- [{artifact_type}] {title}")
            artifact_count += 1

        notes_text = str(notes or "").rstrip()
        note_line_count = len([line for line in notes_text.splitlines() if line.strip()]) if notes_text else 0

        lines = [
            "# WebJam Session Brief",
            "",
            f"Created (UTC): {created_at.isoformat().replace('+00:00', 'Z')}",
            f"Mode: {mode_text}",
            f"Template: {template_name}",
            f"Goal: {session_goal}",
            f"Review state: {review_state}",
            f"Participants: {len(participant_list)}",
            f"Artifacts: {artifact_count}",
            f"Note lines: {note_line_count}",
            "",
            "## Participants",
        ]
        if participant_list:
            lines.extend(f"- {name}" for name in participant_list)
        else:
            lines.append("- (none)")

        lines.extend(["", "## Artifacts"])
        if artifact_lines:
            lines.extend(artifact_lines)
        else:
            lines.append("- (none)")

        lines.extend(["", "## Notes"])
        if notes_text:
            lines.append(notes_text)
        else:
            lines.append("(none)")

        lines.extend(["", "## Diagnostics", f"- Exported with local usage metrics count: {len(self.collect())}"])

        content = "\n".join(lines) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=brief_path.stem + ".",
            suffix=".tmp",
            dir=str(output_dir),
        )
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path = Path(temp_name)
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(brief_path)
            return brief_path
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def export_diagnostics_bundle(
        self,
        output_dir: Path,
        jamulus_state: str,
        webex_state: str,
        latency_ms: float | None,
        server: str,
        webex_url: str,
        audio_diagnostics: dict[str, Any],
        settings_payload: dict[str, Any] | None = None,
        room_context: dict[str, Any] | None = None,
        webex_last_error: str = "",
        jamulus_path: str = "",
        log_files: Iterable[os.PathLike[str] | str] = (),
        support_files: Iterable[os.PathLike[str] | str] = (),
        extra_json_files: dict[str, Any] | None = None,
    ) -> Path:
        # Compatibility parameters are intentionally ignored.  In particular,
        # settings, room context, databases, arbitrary support files, private
        # meeting URLs, executable paths, and ad-hoc JSON can no longer enter a
        # default support archive.
        _ = (
            webex_state,
            server,
            webex_url,
            settings_payload,
            room_context,
            webex_last_error,
            jamulus_path,
            support_files,
            extra_json_files,
        )
        artifact = self._build_support_artifact(
            jamulus_state=jamulus_state,
            latency_ms=latency_ms,
            audio_diagnostics=audio_diagnostics,
            log_excerpts=self._allowed_log_excerpts(log_files),
        )
        return artifact.save_zip(Path(output_dir))

    def _build_support_artifact(
        self,
        *,
        jamulus_state: str,
        latency_ms: float | None,
        audio_diagnostics: dict[str, Any],
        log_excerpts: dict[str, str] | None = None,
    ) -> SupportBundleArtifact:
        diagnostics = audio_diagnostics if isinstance(audio_diagnostics, dict) else {}
        engine_fields: dict[str, Any] = {}
        for source, destination in (
            ("backend", "backend"),
            ("active", "active"),
            ("responsive", "responsive"),
            ("latency_mode", "latency_mode"),
            ("blocksize", "block_size"),
            ("rpc_available", "rpc_available"),
        ):
            if source in diagnostics:
                engine_fields[destination] = diagnostics[source]
        if latency_ms is not None:
            engine_fields["latency_ms"] = latency_ms

        metrics = self.collect()

        def metric(name: str) -> int:
            try:
                return max(0, int(metrics.get(name, "0") or 0))
            except (TypeError, ValueError):
                return 0

        reconnect_counts = {
            "attempts": metric("metric_jamulus_reconnect_attempt"),
            "succeeded": metric("metric_jamulus_reconnect_success"),
            "failed": metric("metric_jamulus_reconnect_failed"),
        }
        export_counts = {
            "succeeded": metric("metric_diagnostics_bundle_exported"),
            "failed": metric("metric_diagnostics_bundle_failed"),
        }
        try:
            from webjam_qt import __version__
        except Exception:  # pragma: no cover - legacy-only fallback
            __version__ = "unknown"

        facts = SupportFacts(
            webjam_version=__version__,
            os_name=f"{platform.system()} {platform.release()}".strip(),
            architecture=platform.machine(),
            jamulus_state=jamulus_state,
            engine_capabilities=engine_fields,
            sample_rate_hz=diagnostics.get("samplerate"),
            reconnect_counts=reconnect_counts,
            export_counts=export_counts,
        )
        return build_support_bundle(facts, log_excerpts=log_excerpts or {})

    @staticmethod
    def _allowed_log_excerpts(
        candidates: Iterable[os.PathLike[str] | str],
    ) -> dict[str, str]:
        allowed_names = {
            ".webjam.log": "webjam",
            "webjam.log": "webjam",
            ".webjam_jamulus.log": "jamulus",
            "jamulus.log": "jamulus",
            "jamulus-server.log": "jamulus_server",
            "band-check.log": "band_check",
        }
        excerpts: dict[str, str] = {}
        for raw in candidates:
            try:
                source = Path(raw)
            except (TypeError, ValueError):
                continue
            key = allowed_names.get(source.name.lower())
            if not key or key in excerpts or source.is_symlink() or not source.is_file():
                continue
            try:
                with source.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - 128 * 1024))
                    raw_excerpt = handle.read()
            except OSError:
                continue
            if _looks_binary_log(raw_excerpt):
                continue
            excerpt = raw_excerpt.decode("utf-8", errors="replace")
            excerpts[key] = excerpt
        return excerpts


def _looks_binary_log(payload: bytes) -> bool:
    audio_magic = (b"RIFF", b"FORM", b"fLaC", b"OggS", b"ID3", b"caff")
    if payload.startswith(audio_magic) or b"\x00" in payload[:4_096]:
        return True
    sample = payload[:4_096]
    if not sample:
        return False
    controls = sum(byte < 32 and byte not in (9, 10, 13) for byte in sample)
    return controls / len(sample) >= 0.02
