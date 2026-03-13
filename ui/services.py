from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable, Iterable


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
        for key in self.repository.list_settings().keys():
            if key.startswith(prefix):
                self.repository.delete_setting(key)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): MetricsService._json_safe(v) for k, v in value.items()}
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
        home_dir = Path(home_dir)
        try:
            home_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise NotADirectoryError(f"Snapshot output path is not a directory: {home_dir}") from exc
        if not home_dir.is_dir():
            raise NotADirectoryError(f"Snapshot output path is not a directory: {home_dir}")

        timestamp = datetime.now(UTC)
        snapshot = {
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "jamulus_state": jamulus_state,
            "webex_state": webex_state,
            "latency_ms": latency_ms,
            "server": server,
            "webex_url": webex_url,
            "audio_diagnostics": audio_diagnostics,
            "usage_metrics": self.collect(),
        }
        out_path = home_dir / f"webjam_diagnostics_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        content = json.dumps(self._json_safe(snapshot), indent=2)
        fd, temp_name = tempfile.mkstemp(
            prefix=out_path.stem + ".",
            suffix=".tmp",
            dir=str(home_dir),
        )
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path = Path(temp_name)
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(out_path)
            return out_path
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

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

        created_at = datetime.now(UTC)
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
        output_dir = Path(output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise NotADirectoryError(f"Bundle output path is not a directory: {output_dir}") from exc
        if not output_dir.is_dir():
            raise NotADirectoryError(f"Bundle output path is not a directory: {output_dir}")

        created_at = datetime.now(UTC)
        stamp = created_at.strftime("%Y%m%d_%H%M%S")
        bundle_path = output_dir / f"webjam_diagnostics_bundle_{stamp}.zip"
        workspace = Path(tempfile.mkdtemp(prefix=f"{bundle_path.stem}.", dir=str(output_dir)))

        try:
            logs_dir = workspace / "logs"
            files_dir = workspace / "files"
            logs_dir.mkdir(parents=True, exist_ok=True)
            files_dir.mkdir(parents=True, exist_ok=True)

            snapshot_payload = {
                "created_at": created_at.isoformat().replace("+00:00", "Z"),
                "jamulus_state": jamulus_state,
                "webex_state": webex_state,
                "latency_ms": latency_ms,
                "server": server,
                "webex_url": webex_url,
                "webex_last_error": webex_last_error or "none",
                "jamulus_path": jamulus_path or "",
                "audio_diagnostics": audio_diagnostics,
                "usage_metrics": self.collect(),
            }
            (workspace / "snapshot.json").write_text(
                json.dumps(self._json_safe(snapshot_payload), indent=2),
                encoding="utf-8",
            )

            if settings_payload is not None:
                (workspace / "settings.json").write_text(
                    json.dumps(self._json_safe(settings_payload), indent=2),
                    encoding="utf-8",
                )
            if room_context is not None:
                (workspace / "room_context.json").write_text(
                    json.dumps(self._json_safe(room_context), indent=2),
                    encoding="utf-8",
                )
            if extra_json_files:
                for raw_name, payload in extra_json_files.items():
                    candidate_name = os.path.basename(str(raw_name or "").strip()) or "extra.json"
                    if not candidate_name.lower().endswith(".json"):
                        candidate_name = f"{candidate_name}.json"
                    (workspace / candidate_name).write_text(
                        json.dumps(self._json_safe(payload), indent=2),
                        encoding="utf-8",
                    )

            environment_payload = {
                "platform": platform.platform(),
                "python": sys.version,
                "python_executable": sys.executable,
                "timestamp_utc": created_at.isoformat().replace("+00:00", "Z"),
            }
            (workspace / "environment.json").write_text(
                json.dumps(self._json_safe(environment_payload), indent=2),
                encoding="utf-8",
            )

            missing_files: list[str] = []
            copied_logs = self._copy_files(
                self._normalize_file_candidates(log_files),
                logs_dir,
                missing_files,
            )
            copied_support = self._copy_files(
                self._normalize_file_candidates(support_files),
                files_dir,
                missing_files,
            )

            readme_lines = [
                "WebJam Diagnostics Bundle",
                "",
                f"Created (UTC): {created_at.isoformat().replace('+00:00', 'Z')}",
                f"Jamulus state: {jamulus_state}",
                f"Webex state: {webex_state}",
                f"Server: {server}",
                "",
                "Included files:",
                f"- Snapshot JSON: snapshot.json",
                f"- Environment JSON: environment.json",
                f"- Logs copied: {len(copied_logs)}",
                f"- Support files copied: {len(copied_support)}",
            ]
            if settings_payload is not None:
                readme_lines.append("- Settings JSON: settings.json")
            if room_context is not None:
                readme_lines.append("- Room context JSON: room_context.json")
            if extra_json_files:
                for raw_name in extra_json_files.keys():
                    candidate_name = os.path.basename(str(raw_name or "").strip()) or "extra.json"
                    if not candidate_name.lower().endswith(".json"):
                        candidate_name = f"{candidate_name}.json"
                    readme_lines.append(f"- Extra JSON: {candidate_name}")
            if missing_files:
                readme_lines.append("")
                readme_lines.append("Missing/Skipped file paths:")
                readme_lines.extend(f"- {item}" for item in missing_files)
            (workspace / "README.txt").write_text("\n".join(readme_lines), encoding="utf-8")

            fd, temp_name = tempfile.mkstemp(
                prefix=bundle_path.stem + ".",
                suffix=".tmp",
                dir=str(output_dir),
            )
            try:
                os.close(fd)
            except OSError:
                pass
            temp_path = Path(temp_name)
            try:
                with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for item in workspace.rglob("*"):
                        if item.is_file():
                            archive.write(item, item.relative_to(workspace).as_posix())
                temp_path.replace(bundle_path)
                return bundle_path
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
