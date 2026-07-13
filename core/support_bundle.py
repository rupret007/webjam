"""Privacy-safe WebJam support reports and bundles.

The bundle is allowlist-first: callers provide typed diagnostic facts and log
*text*, never arbitrary files or an environment/settings dump.  A single
immutable artifact supplies the clipboard report, preview, structured JSON,
and ZIP so those surfaces cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import zipfile

from core.redaction import REDACTED, redact_text


SCHEMA_VERSION = 1
_MAX_TEXT_LENGTH = 2_000
_MAX_RECORDS = 200
_MAX_LOG_LINES = 500
_MAX_LOG_BYTES = 64 * 1024
_AUDIO_MAGIC = ("RIFF", "FORM", "fLaC", "OggS", "ID3", "caff")

_ENGINE_FIELDS = frozenset(
    {
        "backend",
        "active",
        "responsive",
        "rpc_available",
        "rpc_last_activity_age_s",
        "latency_mode",
        "latency_ms",
        "block_size",
        "sample_format",
        "input_channels",
        "output_channels",
        "supported_sample_rates",
        "last_error",
    }
)
_CHANNEL_FIELDS = frozenset({"input", "output", "recorded", "meter_input"})
_RECORDER_FIELDS = frozenset(
    {
        "state",
        "armed",
        "recording",
        "writable",
        "finalized",
        "reopened",
        "format",
        "sample_rate_hz",
        "channels",
        "duration_seconds",
        "sample_count",
        "disk_free_bytes",
        "recovered_files",
        "dropped_blocks",
        "last_error",
    }
)
_TRANSITION_FIELDS = frozenset(
    {"at", "component", "event", "from_state", "to_state", "status", "reason"}
)
_EXPORT_FIELDS = frozenset({"at", "kind", "status", "duration_ms", "error"})
_ERROR_FIELDS = frozenset({"at", "component", "code", "message", "recoverable"})
_TEST_FIELDS = frozenset({"at", "name", "status", "detail"})
_PROCESS_FIELDS = frozenset(
    {"component", "status", "owned", "exit_code", "forced", "orphaned"}
)
_PORT_FIELDS = frozenset(
    {"component", "port", "protocol", "status", "in_use", "released"}
)
_RECONNECT_FIELDS = frozenset({"attempts", "succeeded", "failed"})
_EXPORT_COUNT_FIELDS = frozenset({"attempts", "succeeded", "failed"})

_LOG_ARCHIVE_NAMES = {
    "webjam": "logs/webjam.log",
    "jamulus": "logs/jamulus.log",
    "jamulus_server": "logs/jamulus-server.log",
    "server": "logs/jamulus-server.log",
    "band_check": "logs/band-check.log",
}


@dataclass(frozen=True)
class SupportFacts:
    """The complete set of facts permitted in a default support report."""

    webjam_version: str = ""
    build_id: str = ""
    os_name: str = ""
    architecture: str = ""
    jamulus_version: str = ""
    jamulus_state: str = ""
    session_transitions: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    engine_capabilities: Mapping[str, Any] = field(default_factory=dict)
    sample_rate_hz: int | float | None = None
    channels: Mapping[str, Any] = field(default_factory=dict)
    recorder_health: Mapping[str, Any] = field(default_factory=dict)
    dropped_blocks: int | None = None
    reconnect_counts: Mapping[str, Any] = field(default_factory=dict)
    export_counts: Mapping[str, Any] = field(default_factory=dict)
    export_events: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    errors: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    test_results: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    process_cleanup: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    port_cleanup: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True)
class SupportBundlePreview:
    """Safe preview derived from the exact bytes destined for the archive."""

    report: Mapping[str, Any]
    archive_files: tuple[str, ...]
    manifest: Mapping[str, Any]
    copy_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": _json_clone(self.report),
            "archive_files": list(self.archive_files),
            "manifest": _json_clone(self.manifest),
            "copy_text": self.copy_text,
        }


class SupportBundleArtifact:
    """One immutable snapshot shared by preview, copy, JSON, and ZIP exports."""

    def __init__(
        self,
        *,
        report: Mapping[str, Any],
        archive_files: Mapping[str, bytes],
        created_at: datetime,
    ) -> None:
        self._report = _json_clone(report)
        self._archive_files = dict(archive_files)
        self.created_at = created_at

    @property
    def copy_text(self) -> str:
        return self._archive_files["README.txt"].decode("utf-8")

    @property
    def archive_files(self) -> tuple[str, ...]:
        return tuple(sorted(self._archive_files))

    @property
    def structured_report(self) -> dict[str, Any]:
        return _json_clone(self._report)

    def read_archive_file(self, name: str) -> bytes:
        """Return one canonical archive member; primarily useful for preview UI."""

        return bytes(self._archive_files[name])

    def preview(self) -> SupportBundlePreview:
        manifest = json.loads(self._archive_files["manifest.json"].decode("utf-8"))
        return SupportBundlePreview(
            report=self.structured_report,
            archive_files=self.archive_files,
            manifest=manifest,
            copy_text=self.copy_text,
        )

    def save_zip(self, output_dir: Path, filename: str | None = None) -> Path:
        """Atomically save this exact artifact as a mode-0600 ZIP."""

        output_dir = _prepare_output_dir(output_dir)
        default_name = f"webjam_support_{self.created_at.strftime('%Y%m%d_%H%M%S')}.zip"
        safe_name = _safe_output_name(filename or default_name, suffix=".zip")
        destination = _available_destination(output_dir / safe_name)

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}.", suffix=".tmp", dir=str(output_dir)
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o600)
            with zipfile.ZipFile(
                temporary, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name in self.archive_files:
                    info = _zip_info(name, self.created_at)
                    archive.writestr(info, self._archive_files[name])
            _sync_file(temporary)
            os.replace(temporary, destination)
            return destination
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def save_structured_json(
        self, output_dir: Path, filename: str | None = None
    ) -> Path:
        """Atomically save the same structured report used by preview and ZIP."""

        output_dir = _prepare_output_dir(output_dir)
        default_name = (
            f"webjam_diagnostics_{self.created_at.strftime('%Y%m%d_%H%M%S')}.json"
        )
        safe_name = _safe_output_name(filename or default_name, suffix=".json")
        destination = _available_destination(output_dir / safe_name)
        payload = self._archive_files["support.json"]
        _atomic_private_write(destination, payload)
        return destination


def build_support_bundle(
    facts: SupportFacts,
    *,
    log_excerpts: Mapping[str, str | Sequence[str]] | None = None,
    created_at: datetime | None = None,
) -> SupportBundleArtifact:
    """Create one sanitized artifact from allowlisted facts and log excerpts."""

    timestamp = _utc_timestamp(created_at)
    report = _compact_mapping(
        {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
            "versions": {
                "webjam": _safe_text(facts.webjam_version),
                "build": _safe_text(facts.build_id),
                "jamulus": _safe_text(facts.jamulus_version),
            },
            "system": {
                "os": _safe_text(facts.os_name),
                "architecture": _safe_text(facts.architecture),
            },
            "jamulus": {"state": _safe_text(facts.jamulus_state)},
            "session": {
                "transitions": _sanitize_records(
                    facts.session_transitions, _TRANSITION_FIELDS
                ),
                "reconnects": _sanitize_mapping(
                    facts.reconnect_counts, _RECONNECT_FIELDS
                ),
            },
            "audio": {
                "engine": _sanitize_mapping(
                    facts.engine_capabilities, _ENGINE_FIELDS
                ),
                "sample_rate_hz": _safe_number(facts.sample_rate_hz),
                "channels": _sanitize_mapping(facts.channels, _CHANNEL_FIELDS),
            },
            "recorder": _recorder_report(facts),
            "exports": {
                "counts": _sanitize_mapping(
                    facts.export_counts, _EXPORT_COUNT_FIELDS
                ),
                "events": _sanitize_records(facts.export_events, _EXPORT_FIELDS),
            },
            "errors": _sanitize_records(facts.errors, _ERROR_FIELDS),
            "tests": _sanitize_records(facts.test_results, _TEST_FIELDS),
            "cleanup": {
                "processes": _sanitize_records(
                    facts.process_cleanup, _PROCESS_FIELDS
                ),
                "ports": _sanitize_records(facts.port_cleanup, _PORT_FIELDS),
            },
        },
        preserve_keys={"schema_version", "created_at_utc"},
    )

    sanitized_logs = _sanitize_logs(log_excerpts or {})
    summary = _render_summary(report, tuple(sorted(sanitized_logs)))
    files: dict[str, bytes] = {
        "README.txt": summary.encode("utf-8"),
        "support.json": _json_bytes(report),
    }
    files.update(sanitized_logs)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": report["created_at_utc"],
        "privacy": {
            "collection": "allowlist-only",
            "audio_included": False,
            "arbitrary_files_included": False,
        },
        "logical_fields": sorted(_leaf_paths(report)),
        "files": [
            {
                "path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in sorted(files.items())
        ],
        "manifest_file": "manifest.json",
    }
    files["manifest.json"] = _json_bytes(manifest)
    return SupportBundleArtifact(
        report=report, archive_files=files, created_at=timestamp
    )


def _recorder_report(facts: SupportFacts) -> dict[str, Any]:
    report = _sanitize_mapping(facts.recorder_health, _RECORDER_FIELDS)
    if facts.dropped_blocks is not None:
        report["dropped_blocks"] = _safe_nonnegative_int(facts.dropped_blocks)
    return report


def _sanitize_mapping(
    value: Mapping[str, Any] | Any, allowed_fields: frozenset[str]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(allowed_fields):
        if key not in value:
            continue
        safe = _safe_value(value[key])
        if safe not in (None, "", [], {}):
            result[key] = safe
        elif safe in (False, 0):
            result[key] = safe
    return result


def _sanitize_records(
    records: Sequence[Mapping[str, Any]] | Any,
    allowed_fields: frozenset[str],
) -> list[dict[str, Any]]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        return []
    result: list[dict[str, Any]] = []
    for record in records[:_MAX_RECORDS]:
        safe = _sanitize_mapping(record, allowed_fields)
        if safe:
            result.append(safe)
    return result


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _safe_number(value)
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        safe_items = [_safe_value(item) for item in value[:_MAX_RECORDS]]
        return [item for item in safe_items if item not in (None, "", [], {})]
    # Mappings and arbitrary objects are intentionally excluded here.  Every
    # nested mapping needs its own explicit field allowlist.
    return None


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return redact_text(str(value))[:_MAX_TEXT_LENGTH]


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 6)


def _safe_nonnegative_int(value: Any) -> int:
    number = _safe_number(value)
    if number is None:
        return 0
    return max(0, int(number))


def _compact_mapping(
    value: Mapping[str, Any], *, preserve_keys: set[str] | None = None
) -> dict[str, Any]:
    preserve = preserve_keys or set()
    result: dict[str, Any] = {}
    for key, raw in value.items():
        if isinstance(raw, Mapping):
            safe: Any = _compact_mapping(raw)
        elif isinstance(raw, list):
            safe = [item for item in raw if item not in (None, "", [], {})]
        else:
            safe = raw
        if key in preserve or safe not in (None, "", [], {}):
            result[str(key)] = safe
    return result


def _sanitize_logs(
    excerpts: Mapping[str, str | Sequence[str]],
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for raw_name, raw_content in excerpts.items():
        key = re.sub(r"[^a-z0-9]+", "_", str(raw_name).lower()).strip("_")
        archive_name = _LOG_ARCHIVE_NAMES.get(key)
        if not archive_name or archive_name in files:
            continue
        if isinstance(raw_content, str):
            text = raw_content
        elif isinstance(raw_content, Sequence):
            text = "\n".join(str(line) for line in raw_content)
        else:
            continue
        if not _looks_like_log_text(text):
            continue
        lines = redact_text(text).splitlines()[-_MAX_LOG_LINES:]
        payload = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        if len(payload) > _MAX_LOG_BYTES:
            payload = payload[-_MAX_LOG_BYTES:].decode(
                "utf-8", errors="ignore"
            ).encode("utf-8")
            newline = payload.find(b"\n")
            if newline >= 0:
                payload = payload[newline + 1 :]
        files[archive_name] = payload
    return files


def _looks_like_log_text(text: str) -> bool:
    prefix = text[:16]
    if any(prefix.startswith(magic) for magic in _AUDIO_MAGIC):
        return False
    if "\x00" in text:
        return False
    sample = text[:4_096]
    if not sample:
        return True
    control_count = sum(
        1 for character in sample if ord(character) < 32 and character not in "\n\r\t"
    )
    return control_count / len(sample) < 0.02


def _render_summary(report: Mapping[str, Any], log_names: tuple[str, ...]) -> str:
    lines = [
        "# WebJam Diagnostics",
        "Privacy-safe support report",
        "",
        f"Created (UTC): {report['created_at_utc']}",
        f"Sensitive values are {REDACTED}. Audio, databases, notes, transcripts,",
        "settings, environment variables, private invites, and arbitrary files are excluded.",
        "",
        "## Diagnostic facts",
    ]
    fact_lines = []
    for path, value in _iter_leaves(report):
        if path in {"schema_version", "created_at_utc"}:
            continue
        label = "Jamulus state" if path == "jamulus.state" else path
        fact_lines.append(
            f"- {label}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        )
    lines.extend(fact_lines or ["- No diagnostic facts were available."])
    lines.extend(["", "## Included sanitized logs"])
    lines.extend([f"- {name}" for name in log_names] or ["- None"])
    lines.extend(
        [
            "",
            "The ZIP includes this report, the identical structured report, sanitized",
            "allowlisted log excerpts, and an integrity manifest.",
            "",
        ]
    )
    return "\n".join(lines)


def _iter_leaves(value: Any, prefix: str = ""):
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_leaves(value[key], child)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_leaves(item, f"{prefix}[{index}]")
        return
    yield prefix, value


def _leaf_paths(value: Any) -> list[str]:
    return [path for path, _value in _iter_leaves(value)]


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _utc_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _prepare_output_dir(path: Path) -> Path:
    output_dir = Path(path)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise NotADirectoryError(
            f"Bundle output path is not a directory: {output_dir}"
        ) from exc
    if not output_dir.is_dir():
        raise NotADirectoryError(
            f"Bundle output path is not a directory: {output_dir}"
        )
    return output_dir


def _safe_output_name(name: str, *, suffix: str) -> str:
    basename = Path(str(name or "")).name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    if not safe:
        safe = f"webjam_support{suffix}"
    if not safe.lower().endswith(suffix):
        safe += suffix
    return safe


def _available_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"No available support report name in {path.parent}")


def _zip_info(name: str, created_at: datetime) -> zipfile.ZipInfo:
    local = created_at.astimezone()
    year = max(1980, local.year)
    info = zipfile.ZipInfo(
        filename=name,
        date_time=(year, local.month, local.day, local.hour, local.minute, local.second),
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    return info


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _atomic_private_write(destination: Path, payload: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp", dir=str(destination.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
