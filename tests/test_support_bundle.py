"""Privacy and integrity tests for the canonical support artifact."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from core.redaction import REDACTED, redact_mapping, redact_text
from core.support_bundle import SupportFacts, build_support_bundle


CREATED_AT = datetime(2026, 7, 13, 12, 34, 56, tzinfo=timezone.utc)


def _artifact():
    return build_support_bundle(
        SupportFacts(
            webjam_version="0.9.0",
            build_id="abc123",
            os_name="macOS 15.5",
            architecture="arm64",
            jamulus_version="3.12.2",
            jamulus_state="Running",
            musician_guidance={
                "schema": 1,
                "generation": 3,
                "revision": 7,
                "role": "host",
                "phase": "recording",
                "primary_action": "stop_recording",
                "primary_enabled": True,
                "evidence": "recorder",
                "recovery": "none",
                "title": "Alice's private unreleased song",
                "outputs": [
                    {"key": "recording", "state": "active", "path": "/private"},
                    {"key": "evil", "state": "private-token"},
                ],
                "transitions": [
                    {
                        "at": "2026-07-13T12:00:00Z",
                        "from": "preparing",
                        "to": "connected",
                        "reason": "invitation=private-value",
                    },
                    {
                        "at": "/Users/alice/private",
                        "from": "connected",
                        "to": "completed",
                    },
                ],
            },
            session_transitions=(
                {
                    "at": "2026-07-13T12:00:00Z",
                    "component": "jamulus",
                    "from_state": "starting",
                    "to_state": "running",
                    "status": "ok",
                    "private_notes": "must never enter the report",
                },
            ),
            engine_capabilities={
                "backend": "sounddevice",
                "active": True,
                "rpc_available": True,
                "supported_sample_rates": [48_000],
                "device_name": "Alice's interface SN-PRIVATE",
                "environment": dict(os.environ),
            },
            sample_rate_hz=48_000,
            channels={"input": 2, "output": 2, "device_uid": "PRIVATE-UID"},
            recorder_health={
                "state": "recording",
                "writable": True,
                "format": "PCM_24",
                "sample_count": 240_000,
                "dropped_blocks": 0,
                "recording_path": "/Users/alice/secret/take.wav",
            },
            reconnect_counts={"attempts": 2, "succeeded": 1, "private": 999},
            export_counts={"succeeded": 1},
            errors=(
                {
                    "component": "recorder",
                    "code": "WRITE_RETRY",
                    "message": (
                        "retry at /Users/alice/Music token=very-secret "
                        "email=alice@example.com device_uid=SERIAL-42"
                    ),
                    "traceback": "not allowlisted",
                },
            ),
            test_results=(
                {"name": "record/reopen", "status": "pass", "detail": "240000 samples"},
            ),
            process_cleanup=(
                {"component": "Jamulus", "status": "stopped", "owned": True},
            ),
            port_cleanup=(
                {"component": "RPC", "port": 22125, "status": "released"},
            ),
        ),
        log_excerpts={
            "webjam": (
                "Authorization: Bearer bearer-private\n"
                "Cookie: session=private-cookie\n"
                "invite webjam://join?host=10.0.0.5&token=invite-private\n"
                "meeting https://example.webex.com/meet/private-room?token=meeting-private\n"
                "path /Users/alice/Library/Logs and alice@example.com\n"
                "device_uid=DEVICE-SERIAL-999\n"
            ),
            "jamulus": "engine ready at 48000 Hz",
            "../../private.wav": "audio payload",
            "notes": "personal diary",
            "webjam.db": "database payload",
            "band_check": "RIFF\x00\x00\x00\x00WAVE disguised audio",
        },
        created_at=CREATED_AT,
    )


class TestRecursiveRedaction(unittest.TestCase):
    def test_nested_mapping_and_free_text_remove_secret_and_personal_values(self):
        home = "/Users/alice"
        payload = {
            "safe": "visible",
            "nested": {
                "access_token": "nested-token",
                "items": [
                    {"rpc_secret": "rpc-private"},
                    {"email_address": "alice@example.com"},
                    {"device_uid": "SERIAL-123"},
                    {"path": f"{home}/Music/take.wav"},
                ],
            },
        }

        redacted = redact_mapping(payload)
        encoded = json.dumps(redacted)

        self.assertEqual(redacted["safe"], "visible")
        for private in (
            "nested-token",
            "rpc-private",
            "alice@example.com",
            "SERIAL-123",
            home,
        ):
            self.assertNotIn(private, encoded)
        self.assertIn(REDACTED, encoded)
        self.assertIn("$HOME/Music/take.wav", encoded)

    def test_redact_text_covers_invites_auth_headers_queries_and_identities(self):
        raw = (
            "Authorization: Basic Ym9iOnBhc3M=\n"
            "Proxy-Authorization: Digest private-value\n"
            "Set-Cookie: sid=cookie-private; Secure\n"
            "WEBJAM_API_KEY=api-private rpc_secret=rpc-private\n"
            '"access_token": "secret value with spaces"\n'
            "token=another secret value with spaces\n"
            "UNRELATED_ENV=private environment value\n"
            'device: "Alice USB Interface Serial 123"\n'
            "device=Jeff USB Interface Serial 456\n"
            "device serial: HUMAN-SERIAL-789\n"
            "Session title set: Alice's private unreleased song\n"
            "webjam://join?v=1&host=192.168.1.5&token=invite-private\n"
            "https://example.webex.com/meet/private-room?token=meeting-private\n"
            "https://host.invalid/path?signature=query-private\n"
            "owner alice@example.com device_serial=SERIAL-PRIVATE\n"
            "jamulus --jsonrpcsecret rpc-command-private "
            "--token='quoted-command-private'\n"
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0."
            "dGVzdHNpZ25hdHVyZXByaXZhdGU\n"
            "-----BEGIN PRIVATE KEY-----\n"
            "cGVtLWVuY29kZWQtcHJpdmF0ZS1rZXk=\n"
            "-----END PRIVATE KEY-----\n"
            "/Users/alice/Documents/private.txt C:\\Users\\Alice\\private.txt"
        )

        safe = redact_text(raw)

        for private in (
            "Ym9iOnBhc3M",
            "private-value",
            "cookie-private",
            "api-private",
            "rpc-private",
            "secret value with spaces",
            "another secret value with spaces",
            "private environment value",
            "Alice USB Interface Serial 123",
            "Jeff USB Interface Serial 456",
            "HUMAN-SERIAL-789",
            "Alice's private unreleased song",
            "invite-private",
            "private-room",
            "meeting-private",
            "query-private",
            "alice@example.com",
            "SERIAL-PRIVATE",
            "rpc-command-private",
            "quoted-command-private",
            "eyJhbGciOiJIUzI1NiJ9",
            "cGVtLWVuY29kZWQtcHJpdmF0ZS1rZXk",
            "/Users/alice",
            "C:\\Users\\Alice",
        ):
            self.assertNotIn(private, safe)
        self.assertIn("webjam://[redacted]", safe)
        self.assertIn("https://example.webex.com/[redacted]", safe)
        self.assertIn('"access_token": "[redacted]"', safe)
        self.assertIn("token=[redacted]", safe)
        self.assertGreaterEqual(safe.count("$HOME"), 2)


class TestSupportArtifact(unittest.TestCase):
    def test_free_text_removes_absolute_paths_and_private_filenames(self):
        volume_path = "/Volumes/Band Archive/Private Unreleased Song.wav"
        apostrophe_path = "/Volumes/Jeff's Band/Secret Rehearsal.wav"
        private_path = "/private/tmp/WebJam Audit/Secret Session.aiff"
        temporary_path = "/tmp/WebJam Test/Hidden Take.flac"
        config_path = "/etc/webjam/private.ini"
        application_path = "/Applications/WebJam Private.app/Contents/MacOS/WebJam"
        windows_path = r"D:\Band Sessions\Private Mix.wav"
        windows_forward_path = "E:/Band Sessions/Hidden Stem.flac"
        unc_path = r"\\studio-nas\Jeff Private\Final Song.mp3"
        home_path = "/Users/alice/Music/Home Secret.wav"
        file_uri = "file:///Volumes/Band%20Archive/URI%20Secret.wav"

        artifact = build_support_bundle(
            SupportFacts(
                engine_capabilities={
                    "last_error": (
                        f"route failed at {application_path}: error code 17"
                    ),
                },
                recorder_health={
                    "last_error": f"could not read {windows_path}",
                },
                errors=(
                    {
                        "component": "reference-track",
                        "code": "DECODE_FAILED",
                        "message": (
                            "Failed to open Jeff's Private Demo Song.flac because "
                            "the decoder refused it"
                        ),
                    },
                ),
                test_results=(
                    {
                        "name": "path-boundary",
                        "status": "failed",
                        "detail": f"retry {windows_forward_path}: unavailable",
                    },
                ),
            ),
            log_excerpts={
                "webjam": (
                    f"failed to read {volume_path}; next "
                    f"\"{private_path}\", then '{temporary_path}'.\n"
                    f"apostrophe {apostrophe_path}\n"
                    f"config {config_path}: permission denied\n"
                    f"network share {unc_path}!\n"
                    f"home source {home_path}\n"
                    f"file URI {file_uri}\n"
                    "directories /tmp/private-directory and /etc/hidden-config\n"
                    "source 'Jeff's Quoted Private Master.wav' was rejected\n"
                    "project named Secret Arrangement.logicx was rejected\n"
                    "source_name=Assigned Private Master.als; decode failed\n"
                    "Standalone Secret Master.cpr\n"
                ),
            },
            created_at=CREATED_AT,
        )

        archive_text = "\n".join(
            artifact.read_archive_file(name).decode("utf-8")
            for name in artifact.archive_files
            if name.endswith((".txt", ".json", ".log"))
        )
        structured_text = json.dumps(artifact.structured_report)
        combined = archive_text + structured_text
        for private in (
            volume_path,
            "Band Archive",
            "Private Unreleased Song.wav",
            apostrophe_path,
            "Jeff's Band",
            "Secret Rehearsal.wav",
            private_path,
            "Secret Session.aiff",
            temporary_path,
            "Hidden Take.flac",
            config_path,
            application_path,
            "WebJam Private.app",
            windows_path,
            "Private Mix.wav",
            windows_forward_path,
            "Hidden Stem.flac",
            unc_path,
            "Jeff Private",
            home_path,
            "Home Secret.wav",
            file_uri,
            "URI%20Secret.wav",
            "Jeff's Private Demo Song.flac",
            "Jeff's Quoted Private Master.wav",
            "Secret Arrangement.logicx",
            "Assigned Private Master.als",
            "Standalone Secret Master.cpr",
            "private-directory",
            "hidden-config",
        ):
            self.assertNotIn(private, combined)

        self.assertGreaterEqual(combined.count("[redacted-path]"), 12)
        for useful in (
            "error code 17",
            "the decoder refused it",
            "permission denied",
            "unavailable",
        ):
            self.assertIn(useful, combined)

    def test_path_scrubbing_preserves_safe_url_origins_only(self):
        raw = (
            "meeting "
            "https://example.webex.com/meet/private-room?token=meeting-private\n"
            "catalog "
            "https://updates.example.invalid/releases/catalog.json"
            "?signature=catalog-private\n"
            "local http://192.168.1.7/assets/private-song.wav?token=private\n"
            "invite webjam://join?v=1&host=10.0.0.5&token=invite-private\n"
        )
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": raw},
            created_at=CREATED_AT,
        )

        safe_log = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        for private in (
            "private-room",
            "meeting-private",
            "releases/catalog.json",
            "catalog-private",
            "192.168.1.7",
            "private-song.wav",
            "10.0.0.5",
            "invite-private",
        ):
            self.assertNotIn(private, safe_log)
        self.assertIn("https://example.webex.com/[redacted]", safe_log)
        self.assertIn("https://updates.example.invalid/[redacted]", safe_log)
        self.assertIn("http://[redacted-ip]/[redacted]", safe_log)
        self.assertIn("webjam://[redacted]", safe_log)

    def test_path_scrubbing_covers_adversarial_names_uris_and_windows_forms(self):
        raw = (
            "decoder rejected My Private Song.wav during probe\n"
            "ffmpeg: My Other Secret Mix.flac: Invalid data\n"
            r"windows $USERPROFILE\Music\Third Secret Song.wav failed"
            "\n"
            r"relative ..\Band\Fourth Private Song.aiff failed"
            "\n"
            r"extended \\?\C:\Band\Fourth-B Private Song.wav failed"
            "\n"
            "remote smb://studio-nas/Band%20Archive/Fifth%20Private.mp3\n"
            "remote afp://studio-nas/Band%20Archive/Sixth%20Private.wav\n"
            "remote sftp://studio-nas/Band%20Archive/Sixth-B%20Private.wav\n"
            "url https://host.invalid/open?path="
            "%2FVolumes%2FBand%20Archive%2FSeventh%20Private.wav\n"
            "secret https://host.invalid/token/path-only-secret-value\n"
            "GET https://host.invalid/ok,/Volumes/Eighth-Private.wav\n"
            "curly \u201c/Volumes/Band Archive/Ninth Private.wav\u201d failed\n"
            "unicode /Volumes/R\u00e9p\u00e9tition/\u79d8\u5bc6 Song.wav failed\n"
            "source_name=Tenth Private Master.als; decode failed\n"
            "project named Eleventh Private Arrangement.logicx was rejected\n"
            "Standalone Twelfth Private Master.cpr\n"
            "Unsupported Thirteenth Master.uncommonfmt was rejected\n"
            "colon source:/tmp/Fourteenth Private Folder\n"
            r"drive label:D:\Band\Fifteenth Private Folder"
            "\n"
            "dotted /Volumes/Project.v1 Masters/Sixteenth Private Folder\n"
            "angle Seventeenth Private Song.wav>decode failed\n"
            "pipe Eighteenth Private Song.wav|decode failed\n"
            "dash Nineteenth Private Song.wav\u2014decode failed\n"
        )
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": raw},
            created_at=CREATED_AT,
        )

        safe_log = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        for private in (
            "My Private Song.wav",
            "My Other Secret Mix.flac",
            "Third Secret Song.wav",
            "Fourth Private Song.aiff",
            "Fourth-B Private Song.wav",
            "studio-nas",
            "Fifth%20Private.mp3",
            "Sixth%20Private.wav",
            "Sixth-B%20Private.wav",
            "Seventh%20Private.wav",
            "path-only-secret-value",
            "Eighth-Private.wav",
            "Ninth Private.wav",
            "R\u00e9p\u00e9tition",
            "\u79d8\u5bc6 Song.wav",
            "Tenth Private Master.als",
            "Eleventh Private Arrangement.logicx",
            "Twelfth Private Master.cpr",
            "Thirteenth Master.uncommonfmt",
            "Fourteenth Private Folder",
            "Fifteenth Private Folder",
            "Project.v1 Masters",
            "Sixteenth Private Folder",
            "Seventeenth Private Song.wav",
            "Eighteenth Private Song.wav",
            "Nineteenth Private Song.wav",
        ):
            self.assertNotIn(private, safe_log)
        self.assertIn("during probe", safe_log)
        self.assertIn("Invalid data", safe_log)
        self.assertIn("was rejected", safe_log)
        self.assertGreaterEqual(safe_log.count("decode failed"), 3)
        self.assertGreaterEqual(safe_log.count("[redacted-path]"), 10)

    def test_path_scrubbing_does_not_corrupt_safe_diagnostics(self):
        raw = (
            "It's loading 'Secret Song.wav' and that's okay\n"
            "Connecting to jamulus.io\n"
            "read more at docs.example.com for details\n"
            "GET /api/health returned HTTP 503\n"
            "endpoint /v1/status: unavailable\n"
            "ratio /foo/bar is invalid\n"
            "Jamulus version 3.12.2; rate 48.0\n"
        )
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": raw},
            created_at=CREATED_AT,
        )

        safe_log = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        self.assertNotIn("Secret Song.wav", safe_log)
        for expected in (
            "It's loading '[redacted-path]' and that's okay",
            "Connecting to jamulus.io",
            "read more at docs.example.com for details",
            "GET /api/health returned HTTP 503",
            "endpoint /v1/status: unavailable",
            "ratio /foo/bar is invalid",
            "Jamulus version 3.12.2; rate 48.0",
        ):
            self.assertIn(expected, safe_log)

    def test_readme_names_the_bundle_and_identifies_the_build(self):
        """README.txt opens with a quotable Bundle ID and an identity block.

        A musician reads the Bundle ID over the phone; a technician matches
        it against ``manifest.json`` without comparing whole archives.  The
        at-a-glance block renders only facts the report actually contains.
        """

        artifact = build_support_bundle(
            SupportFacts(
                webjam_version="0.22.5",
                build_id="abc1234",
                jamulus_version="3.12.2",
                jamulus_state="connected",
                os_name="Darwin 24.5.0",
                architecture="arm64",
            ),
            created_at=CREATED_AT,
        )
        summary = artifact.read_archive_file("README.txt").decode("utf-8")
        manifest = json.loads(
            artifact.read_archive_file("manifest.json").decode("utf-8")
        )

        bundle_id = manifest["bundle_id"]
        self.assertRegex(bundle_id, r"^[0-9a-f]{10}$")
        self.assertIn(f"Bundle ID: {bundle_id}", summary)
        expected_id = hashlib.sha256(
            artifact.read_archive_file("support.json")
        ).hexdigest()[:10]
        self.assertEqual(bundle_id, expected_id)
        self.assertIn("## At a glance", summary)
        self.assertIn("- WebJam: 0.22.5 (build abc1234)", summary)
        self.assertIn("- Jamulus: 3.12.2 — state: connected", summary)
        self.assertIn("- System: Darwin 24.5.0 (arm64)", summary)

        # An empty report renders no fabricated glance facts.
        empty = build_support_bundle(SupportFacts(), created_at=CREATED_AT)
        empty_summary = empty.read_archive_file("README.txt").decode("utf-8")
        self.assertNotIn("- WebJam:", empty_summary)
        self.assertNotIn("- System:", empty_summary)
        self.assertIn("Bundle ID: ", empty_summary)

    def test_log_lines_keep_timestamp_severity_and_component(self):
        """The app's own formatter prefix must survive bundling.

        ``%(asctime)s %(levelname)s %(name)s`` with a dotted logger such as
        ``webjam.qt.diagnostics`` must not be mistaken for a filename with an
        unknown extension; losing it strips time ordering, severity, and
        origin from every bundled log line.  Message bodies keep the full
        redaction treatment.
        """

        raw = (
            "2026-08-07T10:00:00 ERROR webjam.qt.diagnostics Export failed\n"
            "2026-08-07T10:00:01 INFO webjam Session started\n"
            "2026-08-07T10:00:02 WARNING webjam.core.take_library "
            "Take /Users/jeff/Music/My Band Take 3.wav missing\n"
            "2026-08-07 10:00:03,123 CRITICAL webjam.services.bridge_service "
            "Jamulus exited with code 0\n"
            "2026-08-07T10:00:04 ERROR /Users/jeff/Music/Loud Song.wav boom\n"
        )
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": raw},
            created_at=CREATED_AT,
        )

        safe_log = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        for private in ("jeff", "My Band Take 3.wav", "Loud Song.wav"):
            self.assertNotIn(private, safe_log)
        for expected in (
            "2026-08-07T10:00:00 ERROR webjam.qt.diagnostics Export failed",
            "2026-08-07T10:00:01 INFO webjam Session started",
            "2026-08-07T10:00:02 WARNING webjam.core.take_library "
            "Take [redacted-path]",
            "2026-08-07 10:00:03,123 CRITICAL webjam.services.bridge_service "
            "Jamulus exited with code 0",
        ):
            self.assertIn(expected, safe_log)
        # A prefix whose component slot is a rooted path is not a structural
        # prefix; the rooted-path rule still redacts it and its conservative
        # end-of-path scan may swallow the trailing message text.
        self.assertIn("2026-08-07T10:00:04 ERROR [redacted-path]", safe_log)
        self.assertNotIn("10:00:04 ERROR /Users", safe_log)

    def test_path_scrubbing_bounds_adversarial_log_work(self):
        bounded_slash_heavy = "/a " * 2_700
        slash_heavy = "/a " * ((128 * 1024) // 3)
        oversized = "private My Never Visible Song.wav " + ("x" * 1024 * 1024)
        bounded_safe_text = "x" * (16 * 1024)
        bounded_sensitive_text = ("x" * (15 * 1024)) + " alice@example.com"
        sensitive_heavy = "\n".join(
            ("x" * 590) + " alice@example.com" for _ in range(200)
        )

        started = time.perf_counter()
        artifact = build_support_bundle(
            SupportFacts(
                engine_capabilities={
                    "backend": bounded_safe_text,
                    "last_error": bounded_sensitive_text,
                },
                recorder_health={"last_error": oversized},
            ),
            log_excerpts={
                "webjam": bounded_slash_heavy,
                "jamulus": slash_heavy,
                "jamulus_server": oversized,
                "band_check": sensitive_heavy,
            },
            created_at=CREATED_AT,
        )
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 2.0)
        self.assertEqual(
            artifact.read_archive_file("logs/webjam.log"),
            b"[redacted-path]\n",
        )
        self.assertEqual(
            artifact.read_archive_file("logs/jamulus.log"),
            b"[redacted-oversize-log-line]\n",
        )
        self.assertEqual(
            artifact.read_archive_file("logs/jamulus-server.log"),
            b"[redacted-oversize-log]\n",
        )
        safe_band_check = artifact.read_archive_file("logs/band-check.log")
        self.assertNotIn(b"alice@example.com", safe_band_check)
        self.assertIn(b"[redacted-oversize-log-line]", safe_band_check)
        archive_text = b"\n".join(
            artifact.read_archive_file(name)
            for name in artifact.archive_files
            if name.endswith((".txt", ".json", ".log"))
        ).decode("utf-8")
        self.assertNotIn("My Never Visible Song.wav", archive_text)
        self.assertEqual(
            artifact.structured_report["audio"]["engine"]["last_error"],
            "[redacted-oversize-text]",
        )
        self.assertEqual(
            artifact.structured_report["audio"]["engine"]["backend"],
            "x" * 2_000,
        )
        self.assertEqual(
            artifact.structured_report["recorder"]["last_error"],
            "[redacted-oversize-text]",
        )

    def test_multiline_credential_values_are_redacted_before_log_tail(self):
        raw = (
            'password="\nMULTILINE-PASSWORD-VALUE\n"\n'
            "engine recovered after password rejection\n"
            "rpc_secret=\nMULTILINE-RPC-VALUE\n"
            "engine ready after RPC retry\n"
            "access_token: '\nMULTILINE-TOKEN-VALUE\n'\n"
            "diagnostics complete\n"
            'api_key="\nUNCLOSED-KEY-VALUE'
        )
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": raw},
            created_at=CREATED_AT,
        )

        safe_log = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        for private in (
            "MULTILINE-PASSWORD-VALUE",
            "MULTILINE-RPC-VALUE",
            "MULTILINE-TOKEN-VALUE",
            "UNCLOSED-KEY-VALUE",
        ):
            self.assertNotIn(private, safe_log)
        for useful in (
            "engine recovered after password rejection",
            "engine ready after RPC retry",
            "diagnostics complete",
        ):
            self.assertIn(useful, safe_log)
        self.assertGreaterEqual(safe_log.count("[redacted]"), 4)

    def test_private_key_crossing_tail_boundary_and_orphan_end_are_redacted(self):
        key_lines = [f"SYNTHETIC-PRIVATE-KEY-LINE-{index:02d}" for index in range(27)]
        lines = (
            ["-----BEGIN PRIVATE KEY-----"]
            + key_lines
            + ["-----END PRIVATE KEY-----"]
            + [f"ordinary-line-{index}" for index in range(472)]
        )
        self.assertEqual(len(lines), 501)

        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={
                "webjam": lines,
                "jamulus": "\n".join(
                    key_lines
                    + ["-----END PRIVATE KEY-----", "engine still healthy"]
                ),
            },
            created_at=CREATED_AT,
        )

        safe_webjam = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        safe_jamulus = artifact.read_archive_file("logs/jamulus.log").decode("utf-8")
        for safe_log in (safe_webjam, safe_jamulus):
            self.assertNotIn("SYNTHETIC-PRIVATE-KEY-LINE", safe_log)
            self.assertNotIn("PRIVATE KEY-----", safe_log)
        self.assertEqual(len(safe_webjam.splitlines()), 500)
        self.assertIn("ordinary-line-471", safe_webjam)
        self.assertIn("engine still healthy", safe_jamulus)

    def test_unclosed_private_key_is_fail_closed_with_bounded_runtime(self):
        malformed = "\n".join(
            "-----BEGIN PRIVATE KEY-----" + ("A" * 220) for _ in range(500)
        )
        orphaned = "\n".join(
            ("ORPHAN-KEY-MATERIAL-" + ("B" * 190) + "-----END PRIVATE KEY-----")
            for _ in range(500)
        )
        self.assertLessEqual(len(malformed), 128 * 1024)
        self.assertLessEqual(len(orphaned), 128 * 1024)

        started = time.perf_counter()
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": malformed, "jamulus": orphaned},
            created_at=CREATED_AT,
        )
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.0)
        self.assertEqual(
            artifact.read_archive_file("logs/webjam.log"),
            b"[redacted]\n",
        )
        safe_jamulus = artifact.read_archive_file("logs/jamulus.log").decode("utf-8")
        self.assertNotIn("ORPHAN-KEY-MATERIAL", safe_jamulus)
        self.assertNotIn("PRIVATE KEY-----", safe_jamulus)
        self.assertLessEqual(len(safe_jamulus.encode("utf-8")), 64 * 1024)

    def test_log_tail_and_oversized_lines_are_bounded_as_whole_units(self):
        lines = [f"bounded-line-{index}" for index in range(501)]
        lines.insert(
            250,
            "Private Oversized Song.wav " + ("x" * (9 * 1024)),
        )
        lines[300:303] = [
            "-----BEGIN PRIVATE KEY-----",
            "cGVtLXByaXZhdGUtc3VwcG9ydC1idW5kbGU=",
            "-----END PRIVATE KEY-----",
        ]
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": lines},
            created_at=CREATED_AT,
        )

        safe_log = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        self.assertNotIn("bounded-line-0\n", safe_log)
        self.assertNotIn("Private Oversized Song.wav", safe_log)
        self.assertNotIn("cGVtLXByaXZhdGUtc3VwcG9ydC1idW5kbGU", safe_log)
        self.assertIn("[redacted]", safe_log)
        self.assertIn("[redacted-oversize-log-line]", safe_log)
        self.assertIn("bounded-line-500", safe_log)
        self.assertLessEqual(len(safe_log.splitlines()), 500)

    def test_log_byte_cap_retains_only_complete_tail_lines(self):
        lines = [f"bounded-{index:03d}-" + ("x" * 230) for index in range(500)]
        artifact = build_support_bundle(
            SupportFacts(),
            log_excerpts={"webjam": lines},
            created_at=CREATED_AT,
        )

        payload = artifact.read_archive_file("logs/webjam.log")
        retained = payload.decode("utf-8").splitlines()
        self.assertLessEqual(len(payload), 64 * 1024)
        self.assertLessEqual(len(retained), 500)
        self.assertNotIn(lines[0], retained)
        self.assertEqual(retained[-1], lines[-1])
        self.assertTrue(all(line in lines for line in retained))
        self.assertTrue(payload.endswith(b"\n"))

    def test_jamulus_recovery_is_complete_bounded_and_path_free(self):
        recovery = {
            "generation": 12,
            "recovery_generation": 3,
            "launch_intended": True,
            "pending": False,
            "active": True,
            "attempts_started": 2,
            "max_attempts": 5,
            "inflight": True,
            "exhausted": False,
            "process_id": 456,
            "process_alive": True,
            "rpc_freshness": "fresh",
            "rpc_age_seconds": 0.125,
            "launch_request_generation": 17,
            "native_setup_grace_configured": True,
            "native_setup_grace_active": True,
            "next_attempt_at": float("inf"),
            "native_setup_deadline": 987654.0,
            "bundle_path": "/Applications/Private Pilot/Jamulus.app",
            "profile_path": "/Users/alice/private/WebJam.ini",
            "rpc_secret": "private-secret",
        }
        artifact = build_support_bundle(
            SupportFacts(jamulus_recovery=recovery),
            created_at=CREATED_AT,
        )

        safe = artifact.structured_report["jamulus"]["recovery"]
        self.assertEqual(
            safe,
            {
                "active": True,
                "attempts_started": 2,
                "exhausted": False,
                "generation": 12,
                "inflight": True,
                "launch_intended": True,
                "launch_request_generation": 17,
                "max_attempts": 5,
                "native_setup_grace_active": True,
                "native_setup_grace_configured": True,
                "pending": False,
                "process_alive": True,
                "process_id": 456,
                "recovery_generation": 3,
                "rpc_age_seconds": 0.125,
                "rpc_freshness": "fresh",
            },
        )
        encoded = json.dumps(artifact.structured_report, allow_nan=False)
        for forbidden in (
            "next_attempt_at",
            "native_setup_deadline",
            "bundle_path",
            "Private Pilot",
            "profile_path",
            "/Users/alice",
            "rpc_secret",
            "private-secret",
            "Infinity",
            "NaN",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_incomplete_or_impossible_recovery_is_not_reported(self):
        base = {
            "generation": 12,
            "recovery_generation": 3,
            "launch_intended": True,
            "pending": False,
            "active": True,
            "attempts_started": 2,
            "max_attempts": 5,
            "inflight": False,
            "exhausted": False,
            "process_id": 456,
            "process_alive": True,
            "rpc_freshness": "fresh",
            "launch_request_generation": 17,
            "native_setup_grace_configured": True,
            "native_setup_grace_active": True,
        }
        invalid_values = (
            {key: value for key, value in base.items() if key != "process_id"},
            {**base, "attempts_started": 6},
            {**base, "rpc_freshness": "/Users/alice/private"},
            {**base, "launch_request_generation": -1},
            {**base, "launch_request_generation": True},
            {**base, "launch_request_generation": 2**63},
            {**base, "native_setup_grace_configured": "yes"},
            {**base, "native_setup_grace_active": 1},
        )

        for recovery in invalid_values:
            with self.subTest(recovery=recovery):
                report = build_support_bundle(
                    SupportFacts(jamulus_recovery=recovery),
                    created_at=CREATED_AT,
                ).structured_report
                self.assertNotIn("recovery", report.get("jamulus", {}))

    def test_jamulus_foreground_keeps_only_bounded_reason_code(self):
        artifact = build_support_bundle(
            SupportFacts(
                jamulus_foreground={
                    "reason_code": "activation-refused",
                    "process_id": 4321,
                    "bundle_path": "/Users/alice/private/Jamulus.app",
                    "exception": "token=private",
                    "native_handle": 9876,
                }
            ),
            created_at=CREATED_AT,
        )

        safe = artifact.structured_report["jamulus"]["foreground"]
        self.assertEqual(safe, {"reason_code": "activation-refused"})
        encoded = json.dumps(artifact.structured_report)
        for forbidden in (
            "process_id",
            "bundle_path",
            "/Users/alice",
            "exception",
            "native_handle",
            "token=private",
        ):
            self.assertNotIn(forbidden, encoded)

        invalid = build_support_bundle(
            SupportFacts(
                jamulus_foreground={
                    "reason_code": "/Users/alice/private",
                }
            ),
            created_at=CREATED_AT,
        ).structured_report
        self.assertNotIn("foreground", invalid.get("jamulus", {}))

    def test_component_sections_keep_only_bounded_path_free_trust_facts(self):
        artifact = build_support_bundle(
            SupportFacts(
                jamulus_update={
                    "state": "ready",
                    "active_version": "3.12.2",
                    "available_version": "3.12.3",
                    "previous_version": "3.12.2",
                    "fallback_version": "3.12.2",
                    "target": "macos-arm64",
                    "progress_percent": 100,
                    "reason_code": "license-approval-required",
                    "restart_when_idle": True,
                    "checked_at_utc": "2026-07-28T12:34:56Z",
                    "catalog_verified": True,
                    "catalog_sequence": 7,
                    "catalog_expires_at_utc": "2026-08-15T12:34:56Z",
                    "signer_fingerprint_sha256": "a" * 64,
                    "catalog_url": (
                        "https://host.invalid/private/catalog?token=secret"
                    ),
                    "artifact_path": "/Users/alice/private/Jamulus.dmg",
                    "message": "failure in /Users/alice/private",
                    "raw_exception": "token=secret",
                },
                webex_app={
                    "state": "installed",
                    "installed": True,
                    "version": "46.7.0.35472",
                    "publisher_verified": True,
                    "reason_code": "publisher-check-deferred",
                    "events": [
                        {
                            "action": "conversation-panel",
                            "result": "shown",
                        },
                        {
                            "action": "show-webex-app",
                            "result": "activated-running",
                        },
                        {
                            "action": "show-webex-app",
                            "result": "launched-app",
                        },
                        {
                            "action": "show-webex-app",
                            "result": "failed",
                            "reason_code": "native-launch-unconfirmed",
                        },
                        {
                            "action": "show-webex-app",
                            "result": "refused",
                            "reason_code": "application-reference-unverified",
                        },
                        {
                            "action": "meeting-handoff",
                            "result": "open-failed",
                            "reason_code": "native-activation-failed",
                            "url": (
                                "https://example.webex.com/meet/private-room"
                            ),
                        },
                        {
                            "action": "private-room",
                            "result": "secret",
                        },
                    ],
                    "path": "/Applications/Webex.app",
                    "meeting_url": (
                        "https://example.webex.com/meet/private-room"
                    ),
                    "username": "alice@example.com",
                },
                reference_track={
                    "playback_state": "ready",
                    "source_state": "loaded",
                    "source_format": "FLAC",
                    "source_sample_rate_hz": 96_000,
                    "source_channels": 2,
                    "source_duration_s": 321.125,
                    "route_available": False,
                    "route_platform": "macos",
                    "route_backend": "blackhole",
                    "route_reason": "cleanup_pending",
                    "route_active": False,
                    "cleanup_pending": True,
                    "count_in_active": False,
                    "audio_callback_calls": 17,
                    "audio_requested_frames": 17_408,
                    "audio_delivered_frames": 16_384,
                    "audio_underrun_frames": 1_024,
                    "audio_callback_faults": 1,
                    "source_name": "Private Demo Song.flac",
                    "source_path": "/Users/alice/private/Private Demo Song.flac",
                    "detail": "token=secret",
                },
            ),
            created_at=CREATED_AT,
        )

        report = artifact.structured_report
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(
            report["jamulus_update"],
            {
                "active_version": "3.12.2",
                "available_version": "3.12.3",
                "catalog_expires_at_utc": "2026-08-15T12:34:56Z",
                "catalog_sequence": 7,
                "catalog_verified": True,
                "checked_at_utc": "2026-07-28T12:34:56Z",
                "fallback_version": "3.12.2",
                "previous_version": "3.12.2",
                "progress_percent": 100,
                "reason_code": "license-approval-required",
                "restart_when_idle": True,
                "signer_fingerprint_sha256": "a" * 64,
                "state": "ready",
                "target": "macos-arm64",
            },
        )
        self.assertEqual(
            report["webex_app"],
            {
                "installed": True,
                "publisher_verified": True,
                "reason_code": "publisher-check-deferred",
                "state": "installed",
                "version": "46.7.0.35472",
                "events": [
                    {
                        "action": "conversation-panel",
                        "result": "shown",
                    },
                    {
                        "action": "show-webex-app",
                        "result": "activated-running",
                    },
                    {
                        "action": "show-webex-app",
                        "result": "launched-app",
                    },
                    {
                        "action": "show-webex-app",
                        "reason_code": "native-launch-unconfirmed",
                        "result": "failed",
                    },
                    {
                        "action": "show-webex-app",
                        "reason_code": "application-reference-unverified",
                        "result": "refused",
                    },
                    {
                        "action": "meeting-handoff",
                        "reason_code": "native-activation-failed",
                        "result": "open-failed",
                    },
                ],
            },
        )
        self.assertEqual(
            report["reference_track"],
            {
                "audio_callback_calls": 17,
                "audio_callback_faults": 1,
                "audio_delivered_frames": 16_384,
                "audio_requested_frames": 17_408,
                "audio_underrun_frames": 1_024,
                "playback_state": "ready",
                "cleanup_pending": True,
                "count_in_active": False,
                "route_active": False,
                "route_available": False,
                "route_backend": "blackhole",
                "route_platform": "macos",
                "route_reason": "cleanup_pending",
                "source_channels": 2,
                "source_duration_s": 321.125,
                "source_format": "FLAC",
                "source_sample_rate_hz": 96_000,
                "source_state": "loaded",
            },
        )
        encoded = json.dumps(report)
        for forbidden in (
            "catalog_url",
            "artifact_path",
            "raw_exception",
            "/Users/alice",
            "token=secret",
            "meeting_url",
            "private-room",
            "username",
            "alice@example.com",
            "/Applications/Webex.app",
            "source_name",
            "source_path",
            "Private Demo Song",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_reference_track_counters_are_bounded_and_private_fields_are_dropped(
        self,
    ):
        maximum = 2**63 - 1
        artifact = build_support_bundle(
            SupportFacts(
                reference_track={
                    "audio_callback_calls": maximum,
                    "audio_requested_frames": -1,
                    "audio_delivered_frames": 12.5,
                    "audio_underrun_frames": True,
                    "audio_callback_faults": 2**63,
                    "cleanup_pending": False,
                    "count_in_active": True,
                    "source_name": "Private Count-In Demo.wav",
                    "source_path": "/Users/alice/Music/Private Count-In Demo.wav",
                    "audio_callback_detail": (
                        "decoder failed at /Users/alice/Music/Private Count-In Demo.wav"
                    ),
                }
            ),
            created_at=CREATED_AT,
        )

        self.assertEqual(
            artifact.structured_report["reference_track"],
            {
                "audio_callback_calls": maximum,
                "cleanup_pending": False,
                "count_in_active": True,
            },
        )
        encoded = json.dumps(artifact.structured_report)
        for forbidden in (
            "audio_requested_frames",
            "audio_delivered_frames",
            "audio_underrun_frames",
            "audio_callback_faults",
            "source_name",
            "source_path",
            "audio_callback_detail",
            "/Users/alice",
            "Private Count-In Demo",
        ):
            self.assertNotIn(forbidden, encoded)

        injected = build_support_bundle(
            SupportFacts(
                reference_track={
                    "audio_requested_frames": (
                        "/Users/alice/Music/Injected Counter.wav"
                    ),
                    "count_in_active": "/Users/alice/Music/Injected State.wav",
                }
            ),
            created_at=CREATED_AT,
        ).structured_report
        self.assertNotIn("reference_track", injected)
        self.assertNotIn("/Users/alice", json.dumps(injected))
        self.assertNotIn("Injected Counter", json.dumps(injected))
        self.assertNotIn("Injected State", json.dumps(injected))

    def test_recorder_identity_generation_and_failure_facts_are_bounded(self):
        take_id = "5c394840-f544-4c11-a63f-20b76d8a7be8"
        report = build_support_bundle(
            SupportFacts(
                recorder_health={
                    "state": "validating",
                    "generation": 23,
                    "take_id": take_id,
                    "dropout_count": 4,
                    "gap_count": 2,
                    "cleanup_pending": True,
                    "reason_code": "local_original_inventory_pending",
                    "failure_category": "peer_transfer",
                    "recording_path": "/Users/alice/Music/Private Take.wav",
                    "participant_name": "Alice",
                }
            ),
            created_at=CREATED_AT,
        ).structured_report

        self.assertEqual(
            report["recorder"],
            {
                "cleanup_pending": True,
                "dropout_count": 4,
                "failure_category": "peer_transfer",
                "gap_count": 2,
                "generation": 23,
                "reason_code": "local_original_inventory_pending",
                "state": "validating",
                "take_id": take_id,
            },
        )
        encoded = json.dumps(report)
        self.assertNotIn("/Users/alice", encoded)
        self.assertNotIn("Private Take", encoded)
        self.assertNotIn("participant_name", encoded)
        self.assertNotIn("Alice", encoded)

        rejected = build_support_bundle(
            SupportFacts(
                recorder_health={
                    "state": "recording /Users/alice/private.wav",
                    "generation": True,
                    "take_id": "/Users/alice/private.wav",
                    "dropout_count": -1,
                    "gap_count": 2**63,
                    "cleanup_pending": "/Users/alice/private.wav",
                    "reason_code": "../../private.wav",
                    "failure_category": "/Users/alice/private.wav",
                }
            ),
            created_at=CREATED_AT,
        ).structured_report
        self.assertNotIn("recorder", rejected)
        self.assertNotIn("/Users/alice", json.dumps(rejected))

    def test_catalog_fetch_diagnostics_use_exact_finite_allowlists(self):
        accepted = build_support_bundle(
            SupportFacts(
                jamulus_update={
                    "catalog_fetch_status": "failed",
                    "catalog_fetch_reason_code": (
                        "catalog-secure-connection-failed"
                    ),
                    "tls_trust_source": "packaged-certifi",
                    "tls_trust_status": "ready",
                    "tls_environment_ca_overrides": "ignored",
                    "catalog_redirect_policy": "explicit-allowlist",
                }
            ),
            created_at=CREATED_AT,
        ).structured_report["jamulus_update"]
        self.assertEqual(
            accepted,
            {
                "catalog_fetch_status": "failed",
                "catalog_fetch_reason_code": (
                    "catalog-secure-connection-failed"
                ),
                "tls_trust_source": "packaged-certifi",
                "tls_trust_status": "ready",
                "tls_environment_ca_overrides": "ignored",
                "catalog_redirect_policy": "explicit-allowlist",
            },
        )

        rejected_report = build_support_bundle(
            SupportFacts(
                jamulus_update={
                    "catalog_fetch_status": "private-state",
                    "catalog_fetch_reason_code": "private-api-token-123",
                    "tls_trust_source": "/Users/private/cacert.pem",
                    "tls_trust_status": "private",
                    "tls_environment_ca_overrides": "private",
                    "catalog_redirect_policy": "private",
                }
            ),
            created_at=CREATED_AT,
        ).structured_report
        self.assertNotIn("jamulus_update", rejected_report)

    def test_allowlist_excludes_devices_environment_personal_files_and_content(self):
        artifact = _artifact()
        report = artifact.structured_report
        encoded = json.dumps(report)

        self.assertEqual(report["versions"]["webjam"], "0.9.0")
        self.assertEqual(report["audio"]["sample_rate_hz"], 48_000)
        self.assertEqual(report["audio"]["channels"], {"input": 2, "output": 2})
        self.assertEqual(report["session"]["guidance"]["phase"], "recording")
        self.assertEqual(
            report["session"]["guidance"]["outputs"],
            [{"key": "recording", "state": "active"}],
        )
        self.assertEqual(
            report["session"]["guidance"]["transitions"],
            [
                {
                    "at": "2026-07-13T12:00:00Z",
                    "from": "preparing",
                    "to": "connected",
                }
            ],
        )
        self.assertNotIn("device_name", encoded)
        self.assertNotIn("environment", encoded)
        self.assertNotIn("recording_path", encoded)
        self.assertNotIn("private_notes", encoded)
        self.assertNotIn("Alice's private unreleased song", encoded)
        self.assertNotIn("private-value", encoded)
        self.assertNotIn("traceback", encoded)
        self.assertNotIn("PRIVATE-UID", encoded)
        self.assertNotIn("very-secret", encoded)
        self.assertNotIn("alice@example.com", encoded)
        self.assertNotIn("SERIAL-42", encoded)
        self.assertIn("[redacted-path]", encoded)
        self.assertNotIn("$HOME/Music", encoded)

        names = artifact.archive_files
        self.assertEqual(
            set(names),
            {
                "README.txt",
                "support.json",
                "manifest.json",
                "logs/webjam.log",
                "logs/jamulus.log",
            },
        )
        joined_names = " ".join(names).lower()
        for forbidden in (".wav", ".aiff", ".db", "notes", "transcript", ".."):
            self.assertNotIn(forbidden, joined_names)
        for name in names:
            path = PurePosixPath(name)
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)

        log_text = artifact.read_archive_file("logs/webjam.log").decode("utf-8")
        for private in (
            "bearer-private",
            "private-cookie",
            "invite-private",
            "private-room",
            "meeting-private",
            "alice@example.com",
            "DEVICE-SERIAL-999",
            "/Users/alice",
        ):
            self.assertNotIn(private, log_text)

    def test_preview_copy_and_saved_zip_are_the_same_artifact(self):
        artifact = _artifact()
        preview = artifact.preview()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = artifact.save_zip(Path(temp_dir), "../../Alice's bundle.zip")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(output.parent, Path(temp_dir))
            self.assertNotIn("..", output.name)
            self.assertNotIn("'", output.name)

            with zipfile.ZipFile(output, "r") as archive:
                names = tuple(sorted(archive.namelist()))
                self.assertEqual(names, preview.archive_files)
                self.assertEqual(
                    json.loads(archive.read("support.json")), preview.report
                )
                self.assertEqual(
                    json.loads(archive.read("manifest.json")), preview.manifest
                )
                self.assertEqual(
                    archive.read("README.txt").decode("utf-8"), preview.copy_text
                )
                for entry in preview.manifest["files"]:
                    payload = archive.read(entry["path"])
                    self.assertEqual(len(payload), entry["size_bytes"])
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(), entry["sha256"]
                    )

    def test_structured_json_is_private_and_matches_preview(self):
        artifact = _artifact()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = artifact.save_structured_json(Path(temp_dir))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text()), artifact.preview().report)

    def test_zip_failure_leaves_no_partial_or_temporary_file(self):
        artifact = _artifact()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "support"
            with patch(
                "core.support_bundle.zipfile.ZipFile",
                side_effect=RuntimeError("simulated archive failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "archive failure"):
                    artifact.save_zip(output_dir)
            self.assertTrue(output_dir.is_dir())
            self.assertEqual(list(output_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
