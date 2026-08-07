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

from core.musician_guidance import GuidanceEvidence, GuidanceRecovery, GuidanceState
from core.redaction import REDACTED, redact_text, should_redact_name
from core.session_conductor import (
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
)
from core.session_lifecycle import SessionLifecyclePhase


SCHEMA_VERSION = 1
_MAX_TEXT_LENGTH = 2_000
_MAX_RECORDS = 200
_MAX_LOG_LINES = 500
_MAX_LOG_BYTES = 64 * 1024
_MAX_SUPPORT_TEXT_INPUT_CHARS = 16 * 1024
_MAX_SUPPORT_LOG_INPUT_CHARS = 128 * 1024
_MAX_SUPPORT_LOG_LINE_CHARS = 8 * 1024
_MAX_SUPPORT_LOG_SEQUENCE_LINES = 1_000
_MAX_SUPPORT_GENERAL_REDACTION_LINE_CHARS = 512
_MAX_SUPPORT_GENERAL_REDACTION_TOTAL_CHARS = 4 * 1024
_AUDIO_MAGIC = ("RIFF", "FORM", "fLaC", "OggS", "ID3", "caff")
_REDACTED_PATH = "[redacted-path]"
_REDACTED_OVERSIZE_TEXT = "[redacted-oversize-text]"
_REDACTED_OVERSIZE_LOG = "[redacted-oversize-log]"
_REDACTED_OVERSIZE_LOG_LINE = "[redacted-oversize-log-line]"

# General diagnostics intentionally keep useful URL origins and paths after
# applying their own scheme-specific privacy policy.  A support bundle has a
# stricter contract: filesystem identities and musician-selected filenames do
# not leave the machine.  Keep these expressions local to the bundle boundary
# so ordinary user-facing errors are not made needlessly vague.
_SUPPORT_URI_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://[^\s'\"<>]+"
)
_SUPPORT_ROOTED_PATH_RE = re.compile(
    r"(?ix)(?<![A-Za-z0-9_/\\])(?:"
    r"(?:file|smb|afp|nfs|ftp|sftp):(?://)?[\\/]"
    r"|[A-Z]:[\\/]"
    r"|\\\\"
    r"|\\\?\?\\"
    r"|//"
    r"|/(?!/)"
    r"|\$(?:[A-Z_][A-Z0-9_]*|\{[A-Z_][A-Z0-9_]*\})[\\/]"
    r"|%[A-Z_][A-Z0-9_]*%[\\/]"
    r"|~(?:[^/\\\s]+)?[\\/]"
    r"|\.\.?[\\/]"
    r")"
)
_SUPPORT_EXTENSION_RE = re.compile(
    r"(?i)\.(?P<extension>7z|[A-Za-z][A-Za-z0-9_-]{0,31})"
    r"(?=$|[\\/\s,;:!?)}\]<>|\"'`\u2013\u2014\u201d\u2019\u2026\u3002]"
    r"|\.(?=$|[\s,;:!?)}\]<>|\"'`\u2013\u2014\u201d\u2019\u2026\u3002]))"
)
_SUPPORT_PRIVATE_KEY_MARKER_RE = re.compile(
    r"(?i)-----(?P<kind>BEGIN|END) [^-\r\n]*PRIVATE KEY-----"
)
# The app's own log formatter is ``%(asctime)s %(levelname)s %(name)s
# %(message)s`` with dotted logger names such as ``webjam.qt.diagnostics``.
# That dotted name looks like an unknown file extension to the conservative
# filename scanner below, which would redact from the start of the line and
# strip the timestamp, severity, and component from every bundled log line.
# The prefix matched here cannot carry private data by construction: a strict
# timestamp, a fixed severity word, and a dotted identifier chain with no
# spaces, separators, or userinfo. Only this structural prefix is preserved;
# the message body keeps the full redaction treatment.
_SUPPORT_LOG_LINE_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?"
    r"[ \t]+(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)"
    r"[ \t]+[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r"(?=[ \t]|$)"
)
_SUPPORT_ASSIGNMENT_PREFIX_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])"
    r"(?P<key>[\"']?[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}[\"']?)"
    r"[ \t]*[:=][ \t]*"
)
_SUPPORT_POSSIBLE_IPV4_RE = re.compile(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}")
_SUPPORT_ENV_ASSIGNMENT_RE = re.compile(
    r"(?i)^\s*(?:export\s+)?[A-Z][A-Z0-9_]{1,}\s*="
)
_SUPPORT_QUERY_SECRET_RE = re.compile(
    r"(?i)[?&](?:secret|token|password|passphrase|credential|api[_-]?key|"
    r"apikey|auth|authorization|code|invite|key|session|signature|sig|jwt|"
    r"rpc[_-]?key)="
)
_SUPPORT_GENERAL_REDACTION_MARKERS = (
    "api-key",
    "api_key",
    "apikey",
    "auth-key",
    "auth_key",
    "authkey",
    "authorization",
    "basic ",
    "bearer ",
    "chat-message",
    "chat_message",
    "chatmessage",
    "cookie",
    "credential",
    "device",
    "digest ",
    "display-name",
    "display_name",
    "displayname",
    "dsn",
    "email",
    "eyj",
    "full-name",
    "full_name",
    "fullname",
    "invite",
    "lyrics",
    "musician",
    "participant",
    "passphrase",
    "passwd",
    "password",
    "private key",
    "private-key",
    "private_key",
    "private-notes",
    "private_notes",
    "privatekey",
    "privatenotes",
    "private_notes",
    "rpc-",
    "rpc_",
    "rpcsecret",
    "secret",
    "serial",
    "session title",
    "session-notes",
    "session-title",
    "session_notes",
    "session_title",
    "sessionnotes",
    "sessiontitle",
    "token",
    "transcript",
    "uid",
    "webex.com",
    "webjam:",
)
_SUPPORT_FILE_EXTENSIONS = frozenset(
    {
        "7z",
        "aac",
        "aif",
        "aifc",
        "aiff",
        "alac",
        "als",
        "app",
        "band",
        "bandproj",
        "caf",
        "caff",
        "cpr",
        "csv",
        "db",
        "dmg",
        "exe",
        "flac",
        "flp",
        "gz",
        "ini",
        "json",
        "log",
        "logic",
        "logicx",
        "m3u",
        "m3u8",
        "m4a",
        "mid",
        "midi",
        "mp3",
        "mp4",
        "oga",
        "ogg",
        "opus",
        "ptf",
        "ptx",
        "py",
        "reaper",
        "rpp",
        "sqlite",
        "studioone",
        "tar",
        "toml",
        "txt",
        "wav",
        "wave",
        "wma",
        "xml",
        "yaml",
        "yml",
        "zip",
    }
)
_SUPPORT_SAFE_BARE_DOMAIN_SUFFIXES = frozenset(
    {"ai", "app", "co", "com", "dev", "edu", "gov", "invalid", "io", "net", "org"}
)
_SUPPORT_NON_FILE_SLASH_PREFIXES = frozenset(
    {"delete", "endpoint", "get", "head", "options", "patch", "post", "put", "ratio"}
)
_SUPPORT_FILESYSTEM_ROOT_NAMES = frozenset(
    {
        "applications",
        "data",
        "etc",
        "home",
        "library",
        "media",
        "mnt",
        "opt",
        "private",
        "root",
        "run",
        "srv",
        "tmp",
        "users",
        "usr",
        "var",
        "volumes",
    }
)
_SUPPORT_DIAGNOSTIC_SUFFIX_PREFIXES = (
    "decode ",
    "denied",
    "error ",
    "failed",
    "failure",
    "invalid",
    "missing",
    "not ",
    "permission ",
    "refused",
    "unavailable",
)

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
_JAMULUS_RPC_FRESHNESS = frozenset(
    {"no_process", "starting", "fresh", "stale"}
)
_JAMULUS_FOREGROUND_REASON_CODES = frozenset(
    {
        "not-requested",
        "foregrounded",
        "not-running",
        "identity-unverified",
        "native-activation-unavailable",
        "activation-refused",
        "frontmost-unconfirmed",
        "process-changed",
        "platform-not-managed",
    }
)
_GUIDANCE_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)
_COMPONENT_VERSION_RE = re.compile(r"^[0-9]{1,5}(?:\.[0-9]{1,5}){2,3}$")
_REASON_CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JAMULUS_UPDATE_STATES = frozenset(
    {
        "idle",
        "checking",
        "up-to-date",
        "available",
        "downloading",
        "ready",
        "deferred",
        "fallback",
        "failed",
        "cancelled",
        "not-checked",
        "unavailable",
    }
)
_COMPONENT_TARGETS = frozenset(
    {"windows-x64", "linux-x64", "macos-arm64", "macos-x64"}
)
_CATALOG_FETCH_STATES = frozenset({"not-checked", "online", "failed"})
_CATALOG_FETCH_REASON_CODES = frozenset(
    {
        "catalog-offline",
        "catalog-secure-connection-failed",
        "catalog-service-unavailable",
        "catalog-trust-unavailable",
    }
)
_TLS_TRUST_SOURCES = frozenset(
    {"packaged-certifi", "injected", "unavailable"}
)
_TLS_TRUST_STATES = frozenset({"not-checked", "ready", "unavailable", "unknown"})
_TLS_ENVIRONMENT_OVERRIDE_STATES = frozenset({"ignored", "unknown"})
_CATALOG_REDIRECT_POLICIES = frozenset({"explicit-allowlist", "unknown"})
_WEBEX_APP_STATES = frozenset(
    {
        "installed",
        "not-installed",
        "invalid",
        "unsupported",
        "not-checked",
        "unavailable",
    }
)
_WEBEX_EVENT_ACTIONS = frozenset(
    {
        "conversation-panel",
        "show-webex-app",
        "mute-guidance",
        "meeting-handoff",
    }
)
_WEBEX_EVENT_RESULTS = frozenset(
    {
        "shown",
        "check-pending",
        "unavailable",
        "busy",
        "activated-running",
        "launched-app",
        "refused",
        "failed",
        "missing-link",
        "invalid-link",
        "accepted",
        "opened-externally",
        "open-failed",
        "cancelled",
    }
)
_WEBEX_EVENT_REASON_CODES = frozenset(
    {
        "activation-cancelled",
        "activation-exception",
        "ambiguous-running-instances",
        "app-not-running",
        "application-reference-unverified",
        "application-path-unverified",
        "invalid-activation-result",
        "native-activation-failed",
        "native-activation-unavailable",
        "native-launch-failed",
        "native-launch-unconfirmed",
        "process-publisher-unverified",
        "reverification-failed",
        "reverification-refused",
        "running-target-changed",
        "running-target-mismatch",
        "target-invalid",
        "verified-app-unavailable",
    }
)
_REFERENCE_TRACK_PLAYBACK_STATES = frozenset(
    {
        "unavailable",
        "idle",
        "loading",
        "ready",
        "routing",
        "playing",
        "paused",
        "stopping",
        "failed",
        "closed",
    }
)
_REFERENCE_TRACK_SOURCE_STATES = frozenset(
    {"not_loaded", "loading", "loaded", "failed"}
)
_REFERENCE_TRACK_SOURCE_FORMATS = frozenset(
    {"unknown", "WAV", "WAVEX", "RF64", "AIFF", "FLAC", "MP3"}
)
_REFERENCE_TRACK_ROUTE_PLATFORMS = frozenset(
    {"unknown", "macos", "windows", "linux"}
)
_REFERENCE_TRACK_ROUTE_BACKENDS = frozenset(
    {"blackhole", "vb-cable-jack", "jack", "unavailable"}
)
_REFERENCE_TRACK_ROUTE_REASONS = frozenset(
    {
        "ready",
        "unavailable",
        "audience_bridge_conflict",
        "physical_certification_required",
        "cleanup_pending",
        "blackhole_unavailable",
        "windows_backend_unavailable",
        "linux_backend_unavailable",
        "live_route_unavailable",
        "unsupported_platform",
    }
)

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
    jamulus_recovery: Mapping[str, Any] = field(default_factory=dict)
    jamulus_foreground: Mapping[str, Any] = field(default_factory=dict)
    jamulus_update: Mapping[str, Any] = field(default_factory=dict)
    webex_app: Mapping[str, Any] = field(default_factory=dict)
    reference_track: Mapping[str, Any] = field(default_factory=dict)
    session_transitions: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    musician_guidance: Mapping[str, Any] = field(default_factory=dict)
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
            "jamulus": {
                "state": _safe_text(facts.jamulus_state),
                "recovery": _sanitize_jamulus_recovery(
                    facts.jamulus_recovery
                ),
                "foreground": _sanitize_jamulus_foreground(
                    facts.jamulus_foreground
                ),
            },
            "jamulus_update": _sanitize_jamulus_update(facts.jamulus_update),
            "webex_app": _sanitize_webex_app(facts.webex_app),
            "reference_track": _sanitize_reference_track(facts.reference_track),
            "session": {
                "guidance": _sanitize_guidance(facts.musician_guidance),
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
    report_payload = _json_bytes(report)
    # A short, stable name for this exact artifact.  A musician can read it
    # from README.txt over the phone and a technician can match it against
    # the manifest without comparing whole archives.
    bundle_id = hashlib.sha256(report_payload).hexdigest()[:10]
    summary = _render_summary(
        report, tuple(sorted(sanitized_logs)), bundle_id=bundle_id
    )
    files: dict[str, bytes] = {
        "README.txt": summary.encode("utf-8"),
        "support.json": report_payload,
    }
    files.update(sanitized_logs)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": bundle_id,
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


def _sanitize_jamulus_recovery(
    value: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Accept one complete, bounded, path-free recovery snapshot."""

    if not isinstance(value, Mapping):
        return {}

    result: dict[str, Any] = {}
    integer_fields = (
        "generation",
        "recovery_generation",
        "attempts_started",
        "max_attempts",
        "process_id",
        "launch_request_generation",
    )
    for key in integer_fields:
        item = value.get(key)
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 0 <= item <= 2**63 - 1
        ):
            return {}
        result[key] = item

    if result["max_attempts"] <= 0:
        return {}
    if result["attempts_started"] > result["max_attempts"]:
        return {}

    for key in (
        "launch_intended",
        "pending",
        "active",
        "inflight",
        "exhausted",
        "process_alive",
        "native_setup_grace_configured",
        "native_setup_grace_active",
    ):
        item = value.get(key)
        if not isinstance(item, bool):
            return {}
        result[key] = item

    freshness = value.get("rpc_freshness")
    if not isinstance(freshness, str) or freshness not in _JAMULUS_RPC_FRESHNESS:
        return {}
    result["rpc_freshness"] = freshness

    age = _safe_number(value.get("rpc_age_seconds"))
    if age is not None and age >= 0:
        result["rpc_age_seconds"] = age
    return result


def _sanitize_jamulus_foreground(
    value: Mapping[str, Any] | Any,
) -> dict[str, str]:
    """Accept only one bounded outcome code, never native identity."""

    if not isinstance(value, Mapping):
        return {}
    reason = value.get("reason_code")
    if (
        not isinstance(reason, str)
        or reason not in _JAMULUS_FOREGROUND_REASON_CODES
    ):
        return {}
    return {"reason_code": reason}


def _sanitize_jamulus_update(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Accept only finite updater state and cryptographic trust facts."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    state = value.get("state")
    if isinstance(state, str) and state in _JAMULUS_UPDATE_STATES:
        result["state"] = state
    for key in (
        "active_version",
        "available_version",
        "previous_version",
        "fallback_version",
    ):
        item = value.get(key)
        if isinstance(item, str) and _COMPONENT_VERSION_RE.fullmatch(item):
            result[key] = item
    target = value.get("target")
    if isinstance(target, str) and target in _COMPONENT_TARGETS:
        result["target"] = target
    progress = value.get("progress_percent")
    if (
        isinstance(progress, int)
        and not isinstance(progress, bool)
        and 0 <= progress <= 100
    ):
        result["progress_percent"] = progress
    reason = value.get("reason_code")
    if isinstance(reason, str) and _REASON_CODE_RE.fullmatch(reason):
        result["reason_code"] = reason
    restart_when_idle = value.get("restart_when_idle")
    if isinstance(restart_when_idle, bool):
        result["restart_when_idle"] = restart_when_idle
    checked_at = value.get("checked_at_utc")
    if isinstance(checked_at, str) and _GUIDANCE_TIMESTAMP_RE.fullmatch(
        checked_at
    ):
        result["checked_at_utc"] = checked_at
    catalog_verified = value.get("catalog_verified")
    if isinstance(catalog_verified, bool):
        result["catalog_verified"] = catalog_verified
    sequence = value.get("catalog_sequence")
    if (
        isinstance(sequence, int)
        and not isinstance(sequence, bool)
        and 0 <= sequence <= 2**63 - 1
    ):
        result["catalog_sequence"] = sequence
    expires_at = value.get("catalog_expires_at_utc")
    if isinstance(expires_at, str) and _GUIDANCE_TIMESTAMP_RE.fullmatch(
        expires_at
    ):
        result["catalog_expires_at_utc"] = expires_at
    fingerprint = value.get("signer_fingerprint_sha256")
    if isinstance(fingerprint, str) and _SHA256_RE.fullmatch(fingerprint):
        result["signer_fingerprint_sha256"] = fingerprint
    for key, allowed in (
        ("catalog_fetch_status", _CATALOG_FETCH_STATES),
        ("tls_trust_source", _TLS_TRUST_SOURCES),
        ("tls_trust_status", _TLS_TRUST_STATES),
        (
            "tls_environment_ca_overrides",
            _TLS_ENVIRONMENT_OVERRIDE_STATES,
        ),
        ("catalog_redirect_policy", _CATALOG_REDIRECT_POLICIES),
    ):
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            result[key] = item
    fetch_reason = value.get("catalog_fetch_reason_code")
    if isinstance(fetch_reason, str) and fetch_reason in _CATALOG_FETCH_REASON_CODES:
        result["catalog_fetch_reason_code"] = fetch_reason
    return result


def _sanitize_webex_app(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Accept only native-app trust facts and finite, identity-free actions."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    state = value.get("state")
    if isinstance(state, str) and state in _WEBEX_APP_STATES:
        result["state"] = state
    installed = value.get("installed")
    if isinstance(installed, bool):
        result["installed"] = installed
    version = value.get("version")
    if isinstance(version, str) and _COMPONENT_VERSION_RE.fullmatch(version):
        result["version"] = version
    publisher_verified = value.get("publisher_verified")
    if isinstance(publisher_verified, bool):
        result["publisher_verified"] = publisher_verified
    reason = value.get("reason_code")
    if isinstance(reason, str) and _REASON_CODE_RE.fullmatch(reason):
        result["reason_code"] = reason
    raw_events = value.get("events")
    if (
        isinstance(raw_events, Sequence)
        and not isinstance(raw_events, (str, bytes, bytearray))
    ):
        events: list[dict[str, str]] = []
        for raw_event in list(raw_events)[-12:]:
            if not isinstance(raw_event, Mapping):
                continue
            action = raw_event.get("action")
            event_result = raw_event.get("result")
            if (
                not isinstance(action, str)
                or action not in _WEBEX_EVENT_ACTIONS
                or not isinstance(event_result, str)
                or event_result not in _WEBEX_EVENT_RESULTS
            ):
                continue
            event = {"action": action, "result": event_result}
            event_reason = raw_event.get("reason_code")
            if (
                isinstance(event_reason, str)
                and event_reason in _WEBEX_EVENT_REASON_CODES
            ):
                event["reason_code"] = event_reason
            events.append(event)
        if events:
            result["events"] = events
    return result


def _sanitize_reference_track(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Accept only bounded route and source-shape facts, never source identity."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    enum_fields = (
        ("playback_state", _REFERENCE_TRACK_PLAYBACK_STATES),
        ("source_state", _REFERENCE_TRACK_SOURCE_STATES),
        ("source_format", _REFERENCE_TRACK_SOURCE_FORMATS),
        ("route_platform", _REFERENCE_TRACK_ROUTE_PLATFORMS),
        ("route_backend", _REFERENCE_TRACK_ROUTE_BACKENDS),
        ("route_reason", _REFERENCE_TRACK_ROUTE_REASONS),
    )
    for key, allowed in enum_fields:
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            result[key] = item

    numeric_bounds = (
        ("source_sample_rate_hz", 0.0, 384_000.0),
        ("source_channels", 0.0, 2.0),
        ("source_duration_s", 0.0, 24.0 * 60.0 * 60.0),
    )
    for key, minimum, maximum in numeric_bounds:
        item = _safe_number(value.get(key))
        if item is not None and minimum <= item <= maximum:
            result[key] = item

    for key in ("route_available", "route_active", "cleanup_pending"):
        item = value.get(key)
        if isinstance(item, bool):
            result[key] = item
    return result


def _sanitize_guidance(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Accept only the guidance model's finite public representation."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {"schema": 1}
    for key in ("generation", "revision"):
        raw = value.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            result[key] = raw
    enum_fields = {
        "role": {item.value for item in SessionRole},
        "phase": {item.value for item in SessionConductorPhase},
        "primary_action": {item.value for item in SessionPrimaryAction},
        "evidence": {item.value for item in GuidanceEvidence},
        "recovery": {item.value for item in GuidanceRecovery},
    }
    for key, allowed in enum_fields.items():
        raw = value.get(key)
        if isinstance(raw, str) and raw in allowed:
            result[key] = raw
    if isinstance(value.get("primary_enabled"), bool):
        result["primary_enabled"] = value["primary_enabled"]

    outputs: list[dict[str, str]] = []
    raw_outputs = value.get("outputs", ())
    if isinstance(raw_outputs, Sequence) and not isinstance(
        raw_outputs, (str, bytes)
    ):
        for raw in raw_outputs[:_MAX_RECORDS]:
            if not isinstance(raw, Mapping):
                continue
            key = raw.get("key")
            state = raw.get("state")
            if key in {"recording", "take", "guest_media", "studio", "export"} and (
                isinstance(state, str)
                and state in {item.value for item in GuidanceState}
            ):
                outputs.append({"key": key, "state": state})
    result["outputs"] = outputs

    transitions: list[dict[str, str]] = []
    raw_transitions = value.get("transitions", ())
    lifecycle_values = {item.value for item in SessionLifecyclePhase}
    if isinstance(raw_transitions, Sequence) and not isinstance(
        raw_transitions, (str, bytes)
    ):
        for raw in raw_transitions[:_MAX_RECORDS]:
            if not isinstance(raw, Mapping):
                continue
            at = raw.get("at")
            previous = raw.get("from")
            current = raw.get("to")
            if (
                isinstance(at, str)
                and _GUIDANCE_TIMESTAMP_RE.fullmatch(at)
                and previous in lifecycle_values
                and current in lifecycle_values
            ):
                transitions.append({"at": at, "from": previous, "to": current})
    result["transitions"] = transitions
    return _compact_mapping(result)


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


def _redact_support_multiline_secrets(text: str) -> str:
    """Redact bounded multi-line secrets before selecting the log tail.

    Tail selection must happen after this pass.  Otherwise a private-key or
    quoted credential that starts just before the retained 500 lines loses its
    opening marker and its body becomes indistinguishable from ordinary text.
    These scanners preserve line breaks, advance monotonically, and fail closed
    at end-of-input, so malformed blocks cannot trigger the lazy-dot searches
    used by the general-purpose diagnostics redactor.
    """

    return _redact_support_multiline_assignments(
        _redact_support_private_key_blocks(text)
    )


def _redact_support_private_key_blocks(text: str) -> str:
    parts: list[str] = []
    safe_start = 0
    block_start: int | None = None
    for marker in _SUPPORT_PRIVATE_KEY_MARKER_RE.finditer(text):
        kind = marker.group("kind").upper()
        if block_start is None and kind == "BEGIN":
            block_start = marker.start()
            continue
        if block_start is None:
            # The excerpt may itself begin in the middle of a key block.  An
            # orphan END marker makes everything before it untrusted.
            stop = marker.end()
            parts.append(_redacted_secret_preserving_line_breaks(text[safe_start:stop]))
            safe_start = stop
            continue
        if kind == "BEGIN":
            # A nested/malformed BEGIN remains inside the fail-closed range.
            continue
        parts.append(text[safe_start:block_start])
        stop = marker.end()
        parts.append(
            _redacted_secret_preserving_line_breaks(text[block_start:stop])
        )
        safe_start = stop
        block_start = None

    if block_start is not None:
        parts.append(text[safe_start:block_start])
        parts.append(_redacted_secret_preserving_line_breaks(text[block_start:]))
        safe_start = len(text)
    parts.append(text[safe_start:])
    return "".join(parts)


def _redact_support_multiline_assignments(text: str) -> str:
    parts: list[str] = []
    safe_start = 0
    cursor = 0
    while cursor < len(text):
        match = _SUPPORT_ASSIGNMENT_PREFIX_RE.search(text, cursor)
        if match is None:
            break
        key = match.group("key").strip("\"'")
        if not should_redact_name(key):
            cursor = match.end()
            continue

        value_start = match.end()
        if value_start >= len(text):
            parts.append(text[safe_start : match.start()])
            parts.append(
                _redacted_secret_preserving_line_breaks(
                    text[match.start() : value_start]
                )
            )
            safe_start = value_start
            cursor = value_start
            continue

        quote = text[value_start] if text[value_start] in {"\"", "'"} else None
        if quote is not None:
            content_start = value_start + 1
            closing = _find_unescaped_quote(text, content_start, quote)
            stop = closing + 1 if closing is not None else len(text)
            parts.append(text[safe_start : match.start()])
            parts.append(
                _redacted_secret_preserving_line_breaks(text[match.start() : stop])
            )
            safe_start = stop
            cursor = stop
            continue

        if text[value_start] in "\r\n":
            content_start = _next_nonempty_line_start(text, value_start)
            if content_start is not None:
                stop = _line_end(text, content_start)
                parts.append(text[safe_start : match.start()])
                parts.append(
                    _redacted_secret_preserving_line_breaks(
                        text[match.start() : stop]
                    )
                )
                safe_start = stop
                cursor = stop
                continue

        stop = value_start
        while stop < len(text) and text[stop] not in "\r\n,;}]":
            stop += 1
        parts.append(text[safe_start : match.start()])
        parts.append(
            _redacted_secret_preserving_line_breaks(text[match.start() : stop])
        )
        safe_start = stop
        cursor = stop

    parts.append(text[safe_start:])
    return "".join(parts)


def _find_unescaped_quote(text: str, start: int, quote: str) -> int | None:
    cursor = start
    while cursor < len(text):
        candidate = text.find(quote, cursor)
        if candidate < 0:
            return None
        backslashes = 0
        previous = candidate - 1
        while previous >= start and text[previous] == "\\":
            backslashes += 1
            previous -= 1
        if backslashes % 2 == 0:
            return candidate
        cursor = candidate + 1
    return None


def _next_nonempty_line_start(text: str, start: int) -> int | None:
    cursor = start
    while cursor < len(text):
        if text.startswith("\r\n", cursor):
            cursor += 2
        elif text[cursor] in "\r\n":
            cursor += 1
        else:
            break
        line_end = _line_end(text, cursor)
        first_content = cursor
        while first_content < line_end and text[first_content] in " \t":
            first_content += 1
        if first_content < line_end:
            return first_content
        cursor = line_end
    return None


def _line_end(text: str, start: int) -> int:
    cursor = start
    while cursor < len(text) and text[cursor] not in "\r\n":
        cursor += 1
    return cursor


def _redacted_secret_preserving_line_breaks(text: str) -> str:
    return REDACTED + "".join(character for character in text if character in "\r\n")


def _support_line_needs_general_redaction(text: str) -> bool:
    lowered = text.lower()
    return (
        "@" in text
        or "://" in text
        or "::" in text
        or text.count(":") >= 4
        or _SUPPORT_POSSIBLE_IPV4_RE.search(text) is not None
        or _SUPPORT_QUERY_SECRET_RE.search(text) is not None
        or any(marker in lowered for marker in _SUPPORT_GENERAL_REDACTION_MARKERS)
    )


def _redact_general_support_lines(
    lines: Sequence[str],
    *,
    overflow_marker: str = _REDACTED_OVERSIZE_LOG_LINE,
) -> list[str]:
    """Bound calls into the broad legacy redactor without skipping secrets."""

    result: list[str] = []
    redaction_chars = 0
    for line in lines:
        if _SUPPORT_ENV_ASSIGNMENT_RE.match(line):
            result.append(REDACTED)
            continue
        if not _support_line_needs_general_redaction(line):
            result.append(line)
            continue
        if (
            len(line) > _MAX_SUPPORT_GENERAL_REDACTION_LINE_CHARS
            or redaction_chars + len(line)
            > _MAX_SUPPORT_GENERAL_REDACTION_TOTAL_CHARS
        ):
            result.append(overflow_marker)
            continue
        redaction_chars += len(line)
        result.append(redact_text(line))
    return result


def _redact_support_fragment(text: str) -> str:
    """Remove filesystem identities from one URL-free, single-line fragment.

    The scanner advances past every range it recognizes.  It deliberately
    avoids the repeated lazy-wildcard substitutions previously used here:
    those became quadratic on an otherwise ordinary slash-heavy log line.
    Ambiguous rooted paths consume the rest of the fragment rather than risk
    guessing where a musician-selected directory name ends.
    """

    rooted_ranges = _rooted_path_ranges(text)
    filename_ranges = _filename_ranges(text, excluded=rooted_ranges)
    return _replace_support_ranges(
        text, _merge_support_range_lists(rooted_ranges, filename_ranges)
    )


def _rooted_path_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        match = _SUPPORT_ROOTED_PATH_RE.search(text, cursor)
        if match is None:
            break
        start = match.start()
        if _is_non_file_slash(text, start):
            cursor = _slash_token_end(text, match.end())
            continue
        end = _rooted_path_end(text, start, match.end())
        ranges.append((start, end))
        cursor = max(end, match.end())
    return ranges


def _is_non_file_slash(text: str, start: int) -> bool:
    if start >= len(text) or text[start] != "/":
        return False
    prefix = text[max(0, start - 32) : start].rstrip().lower()
    word = prefix.rsplit(None, 1)[-1] if prefix else ""
    if word not in _SUPPORT_NON_FILE_SLASH_PREFIXES:
        return False
    end = _slash_token_end(text, start + 1)
    first_segment = text[start + 1 : end].split("/", 1)[0].lower()
    if first_segment in _SUPPORT_FILESYSTEM_ROOT_NAMES:
        return False
    return not any(
        match.group("extension").lower() in _SUPPORT_FILE_EXTENSIONS
        for match in _SUPPORT_EXTENSION_RE.finditer(text, start, end)
    )


def _slash_token_end(text: str, start: int) -> int:
    cursor = start
    while cursor < len(text) and not text[cursor].isspace():
        cursor += 1
    return max(cursor, start)


def _rooted_path_end(text: str, start: int, content_start: int) -> int:
    quote = _opening_quote_before(text, start)
    cursor = content_start
    while cursor < len(text):
        character = text[cursor]
        if quote is not None:
            if _is_closing_quote(text, cursor, quote):
                return cursor
            cursor += 1
            continue
        if character == ".":
            extension = _SUPPORT_EXTENSION_RE.match(text, cursor)
            if extension is not None:
                # A bundle suffix such as ``WebJam.app/Contents`` is not the
                # end of the path.  Continue until another extension, a clear
                # diagnostic separator, a closing quote, or end-of-line.
                if extension.end() >= len(text):
                    return extension.end()
                following = text[extension.end()]
                if following in "/\\":
                    cursor = extension.end()
                    continue
                if following.isspace():
                    # Dots are legal inside a directory name, and paths in
                    # diagnostics are commonly unquoted despite containing
                    # spaces.  In that ambiguous case, keep scanning rather
                    # than expose the remainder of a private directory.
                    cursor = extension.end()
                    continue
                return extension.end()
        if character == ":" and cursor + 1 < len(text):
            suffix = text[cursor + 1 : cursor + 65].lstrip().lower()
            if text[cursor + 1].isspace() and suffix.startswith(
                _SUPPORT_DIAGNOSTIC_SUFFIX_PREFIXES
            ):
                return cursor
        cursor += 1
    return len(text)


def _opening_quote_before(text: str, start: int) -> str | None:
    if start <= 0:
        return None
    opener = text[start - 1]
    pairs = {'"': '"', "'": "'", "`": "`", "\u201c": "\u201d", "\u2018": "\u2019"}
    if opener not in pairs:
        return None
    if opener == "'" and start >= 2 and _is_word_character(text[start - 2]):
        return None
    return pairs[opener]


def _is_closing_quote(text: str, cursor: int, quote: str) -> bool:
    if text[cursor] != quote:
        return False
    if quote != "'":
        return True
    return cursor + 1 >= len(text) or not _is_word_character(text[cursor + 1])


def _is_word_character(value: str) -> bool:
    return value.isalnum() or value == "_"


def _filename_ranges(
    text: str, *, excluded: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    quoted = _quoted_content_ranges(text)
    quote_index = 0
    excluded_index = 0

    for match in _SUPPORT_EXTENSION_RE.finditer(text):
        position = match.start()
        while (
            excluded_index < len(excluded) and excluded[excluded_index][1] <= position
        ):
            excluded_index += 1
        if excluded_index < len(excluded):
            excluded_start, excluded_end = excluded[excluded_index]
            if excluded_start <= position < excluded_end:
                continue

        while quote_index < len(quoted) and quoted[quote_index][1] <= position:
            quote_index += 1
        quote_range: tuple[int, int] | None = None
        if quote_index < len(quoted):
            candidate = quoted[quote_index]
            if candidate[0] <= position < candidate[1]:
                quote_range = candidate

        extension = match.group("extension").lower()
        if (
            quote_range is None
            and extension not in _SUPPORT_FILE_EXTENSIONS
            and _looks_like_safe_bare_hostname(text, match)
        ):
            continue

        # Without a rooted path or explicit quote, the left edge of a filename
        # containing spaces is inherently ambiguous.  Redacting from the start
        # of this URL-free fragment is conservative and still preserves useful
        # diagnostic text following the extension.
        start = quote_range[0] if quote_range is not None else 0
        end = match.end()
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (min(ranges[-1][0], start), max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    return ranges


def _quoted_content_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    pairs = {'"': '"', "'": "'", "`": "`", "\u201c": "\u201d", "\u2018": "\u2019"}
    active_close: str | None = None
    content_start = 0
    for cursor, character in enumerate(text):
        if active_close is not None:
            if _is_closing_quote(text, cursor, active_close):
                ranges.append((content_start, cursor))
                active_close = None
            continue
        if character not in pairs:
            continue
        if character == "'" and cursor > 0 and _is_word_character(text[cursor - 1]):
            continue
        if cursor + 1 >= len(text) or text[cursor + 1].isspace():
            continue
        active_close = pairs[character]
        content_start = cursor + 1
    if active_close is not None:
        ranges.append((content_start, len(text)))
    return ranges


def _looks_like_safe_bare_hostname(text: str, extension_match: re.Match[str]) -> bool:
    extension = extension_match.group("extension").lower()
    if extension not in _SUPPORT_SAFE_BARE_DOMAIN_SUFFIXES:
        return False
    start = extension_match.start()
    scanned = 0
    while start > 0 and text[start - 1] in (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    ):
        start -= 1
        scanned += 1
        if scanned > 253:
            return False
    hostname = text[start : extension_match.end()].rstrip(".")
    labels = hostname.split(".")
    return len(labels) >= 2 and all(
        label
        and len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _merge_support_range_lists(
    first: Sequence[tuple[int, int]], second: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    first_index = 0
    second_index = 0
    while first_index < len(first) or second_index < len(second):
        if second_index >= len(second) or (
            first_index < len(first) and first[first_index] <= second[second_index]
        ):
            merged.append(first[first_index])
            first_index += 1
        else:
            merged.append(second[second_index])
            second_index += 1
    return merged


def _replace_support_ranges(text: str, ranges: Sequence[tuple[int, int]]) -> str:
    if not ranges:
        return text
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        start = max(0, start)
        end = min(len(text), end)
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(text[cursor:start])
        parts.append(_REDACTED_PATH)
        cursor = end
    parts.append(text[cursor:])
    safe = "".join(parts)
    while _REDACTED_PATH + _REDACTED_PATH in safe:
        safe = safe.replace(_REDACTED_PATH + _REDACTED_PATH, _REDACTED_PATH)
    return safe


def _sanitize_support_uri(value: str, scheme: str) -> str:
    trailing = ""
    token = value
    while token and token[-1] in ".,;!?)}":
        trailing = token[-1] + trailing
        token = token[:-1]
    normalized_scheme = scheme.lower()
    if normalized_scheme == "webjam":
        return f"webjam://{REDACTED}{trailing}"
    if normalized_scheme not in {"http", "https"}:
        return f"{_REDACTED_PATH}{trailing}"

    separator = token.find("://")
    remainder = token[separator + 3 :] if separator >= 0 else ""
    authority_end = len(remainder)
    for delimiter in "/?#":
        candidate = remainder.find(delimiter)
        if candidate >= 0:
            authority_end = min(authority_end, candidate)
    authority = remainder[:authority_end]
    detail = remainder[authority_end:]
    if "@" in authority:
        authority = authority.rsplit("@", 1)[-1]
    if not authority:
        authority = REDACTED
    origin = f"{normalized_scheme}://{authority}"
    if detail not in {"", "/"}:
        origin = f"{origin}/{REDACTED}"
    return origin + trailing


def _redact_support_line(text: str) -> str:
    prefix = ""
    prefix_match = _SUPPORT_LOG_LINE_PREFIX_RE.match(text)
    if prefix_match is not None:
        prefix = text[: prefix_match.end()]
        text = text[prefix_match.end() :]
    parts: list[str] = []
    previous_end = 0
    for match in _SUPPORT_URI_RE.finditer(text):
        parts.append(_redact_support_fragment(text[previous_end : match.start()]))
        parts.append(_sanitize_support_uri(match.group(0), match.group("scheme")))
        previous_end = match.end()
    parts.append(_redact_support_fragment(text[previous_end:]))
    safe = "".join(parts)
    while _REDACTED_PATH + _REDACTED_PATH in safe:
        safe = safe.replace(_REDACTED_PATH + _REDACTED_PATH, _REDACTED_PATH)
    return prefix + safe


def _redact_support_paths(text: str) -> str:
    parts: list[str] = []
    for line in text.splitlines(keepends=True):
        body, ending = _support_line_parts(line)
        parts.append(_redact_support_line(body))
        parts.append(ending)
    if not parts:
        return _redact_support_line(text)
    return "".join(parts)


def _support_line_parts(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1]
    return line, ""


def _redact_support_text(value: Any) -> str:
    """Apply shared secret redaction plus bundle-only path redaction.

    URLs are segmented after :func:`redact_text` has applied the existing
    Webex, invite, query-secret, and IP policies.  HTTP diagnostics retain only
    their sanitized origin; path, query, and fragment components remain private.
    Filesystem and other resource schemes are removed entirely.
    """

    raw = str(value if value is not None else "")
    if len(raw) > _MAX_SUPPORT_TEXT_INPUT_CHARS:
        return _REDACTED_OVERSIZE_TEXT
    bounded = _redact_support_multiline_secrets(raw)
    chunks = bounded.splitlines(keepends=True)
    if not chunks:
        chunks = [bounded]
    bodies: list[str] = []
    endings: list[str] = []
    for chunk in chunks:
        body, ending = _support_line_parts(chunk)
        bodies.append(body)
        endings.append(ending)
    secret_safe = _redact_general_support_lines(
        bodies,
        overflow_marker=_REDACTED_OVERSIZE_TEXT,
    )
    return "".join(
        _redact_support_line(body) + ending
        for body, ending in zip(secret_safe, endings, strict=True)
    )


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    if len(raw) > _MAX_SUPPORT_TEXT_INPUT_CHARS:
        return _REDACTED_OVERSIZE_TEXT
    return _redact_support_text(raw)[:_MAX_TEXT_LENGTH]


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
            text = ""
            try:
                item_count = len(raw_content)
            except (OverflowError, TypeError):
                continue
            if item_count > _MAX_SUPPORT_LOG_SEQUENCE_LINES:
                text = _REDACTED_OVERSIZE_LOG
                candidates: Sequence[Any] = ()
            else:
                candidates = raw_content
            values: list[str] = []
            total_chars = 0
            for line in candidates:
                value = str(line)
                total_chars += len(value) + (1 if values else 0)
                if total_chars > _MAX_SUPPORT_LOG_INPUT_CHARS:
                    break
                values.append(value)
            if total_chars > _MAX_SUPPORT_LOG_INPUT_CHARS:
                text = _REDACTED_OVERSIZE_LOG
            elif candidates:
                text = "\n".join(values)
        else:
            continue
        if not _looks_like_log_text(text):
            continue
        if len(text) > _MAX_SUPPORT_LOG_INPUT_CHARS:
            text = _REDACTED_OVERSIZE_LOG
        text = _redact_support_multiline_secrets(text)
        raw_lines = text.splitlines()[-_MAX_LOG_LINES:]
        bounded_lines = [
            (
                _REDACTED_OVERSIZE_LOG_LINE
                if len(line) > _MAX_SUPPORT_LOG_LINE_CHARS
                else line
            )
            for line in raw_lines
        ]
        # Multi-line secret state was resolved before tail selection.  Apply
        # the general redactor to each already-bounded line so none of its
        # broad compatibility expressions can rescan a 128-KiB payload.  Path
        # scanning remains line-local for the same reason.
        lines = [
            _redact_support_line(line)
            for line in _redact_general_support_lines(bounded_lines)
        ]
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


def _summary_glance_lines(report: Mapping[str, Any]) -> list[str]:
    """Render only facts the report actually contains, in plain language."""

    def _lookup(*path: str) -> Any:
        value: Any = report
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                return None
            value = value[key]
        return value

    lines: list[str] = []
    webjam = _lookup("versions", "webjam")
    build = _lookup("versions", "build")
    if webjam:
        suffix = f" (build {build})" if build else ""
        lines.append(f"- WebJam: {webjam}{suffix}")
    jamulus_version = _lookup("versions", "jamulus")
    jamulus_state = _lookup("jamulus", "state")
    if jamulus_version or jamulus_state:
        parts = []
        if jamulus_version:
            parts.append(str(jamulus_version))
        if jamulus_state:
            parts.append(f"state: {jamulus_state}")
        lines.append(f"- Jamulus: {' — '.join(parts)}")
    os_name = _lookup("system", "os")
    architecture = _lookup("system", "architecture")
    if os_name:
        suffix = f" ({architecture})" if architecture else ""
        lines.append(f"- System: {os_name}{suffix}")
    update_status = _lookup("jamulus_update", "status")
    if update_status:
        lines.append(f"- Jamulus component update: {update_status}")
    return lines


def _render_summary(
    report: Mapping[str, Any],
    log_names: tuple[str, ...],
    *,
    bundle_id: str,
) -> str:
    lines = [
        "# WebJam Diagnostics",
        "Privacy-safe support report",
        "",
        f"Bundle ID: {bundle_id}",
        f"Created (UTC): {report['created_at_utc']}",
    ]
    glance = _summary_glance_lines(report)
    if glance:
        lines.extend(["", "## At a glance"])
        lines.extend(glance)
    lines.extend(
        [
            "",
            "Quote the Bundle ID when reporting a problem so support can",
            "match this exact bundle.",
            "",
            f"Sensitive values are {REDACTED}. Audio, databases, notes, transcripts,",
            "settings, environment variables, private invites, and arbitrary files are excluded.",
            "",
            "## Diagnostic facts",
        ]
    )
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
