"""Privacy and integrity tests for the canonical support artifact."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
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
    def test_allowlist_excludes_devices_environment_personal_files_and_content(self):
        artifact = _artifact()
        report = artifact.structured_report
        encoded = json.dumps(report)

        self.assertEqual(report["versions"]["webjam"], "0.9.0")
        self.assertEqual(report["audio"]["sample_rate_hz"], 48_000)
        self.assertEqual(report["audio"]["channels"], {"input": 2, "output": 2})
        self.assertNotIn("device_name", encoded)
        self.assertNotIn("environment", encoded)
        self.assertNotIn("recording_path", encoded)
        self.assertNotIn("private_notes", encoded)
        self.assertNotIn("traceback", encoded)
        self.assertNotIn("PRIVATE-UID", encoded)
        self.assertNotIn("very-secret", encoded)
        self.assertNotIn("alice@example.com", encoded)
        self.assertNotIn("SERIAL-42", encoded)
        self.assertIn("$HOME/Music", encoded)

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
