"""Take library — discovery + .lof offset parsing."""

from __future__ import annotations

import struct
import json
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.local_capture import LocalCaptureTrack
from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus
from core.session_recording_plan import SessionRecordingPlan
from core.take_project import (
    CaptureDevice,
    HostIdentity,
    RecoveryStatus,
    SessionEvidence,
    SessionTimelineEvent,
    new_project_id,
)

from core.take_library import (
    RecorderClientReceipt,
    RecorderRosterError,
    discover_takes,
    find_changed_take,
    load_take,
    parse_lof_offsets,
    parse_jamulus_recording_filename,
    recording_staging_identity,
    recorder_client_observations,
    snapshot_take_directories,
    validate_take,
    write_take_manifest,
    estimate_local_alignment,
)


def _write_wav(
    path: Path,
    seconds: float = 1.0,
    rate: int = 48000,
    channels: int = 1,
):
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        samples = frames * channels
        w.writeframes(struct.pack("<%dh" % samples, *([0] * samples)))


def _receipt(
    name: str,
    port: int,
    *,
    channel_id: int = 0,
    participant_id: str | None = None,
    source_kind: str = "musician",
    channels: int = 1,
    source_fingerprint_sha256: str = "",
    playback_generation: int = 0,
) -> RecorderClientReceipt:
    observation = recorder_client_observations(
        {
            "connections": 1,
            "clients": [
                {
                    "id": channel_id,
                    "name": name,
                    "address": f"127.0.0.1:{port}",
                    "channels": channels,
                }
            ],
        }
    )[0]
    return RecorderClientReceipt(
        server_channel_id=channel_id,
        display_name=name,
        participant_id=participant_id or str(uuid.uuid4()),
        recorder_key_sha256=observation.recorder_key_sha256,
        channels=channels,
        source_kind=source_kind,
        source_fingerprint_sha256=source_fingerprint_sha256,
        playback_generation=playback_generation,
    )


def _write_lof(path: Path, *entries: tuple[str, float]) -> None:
    path.write_text(
        "".join(f'file "{name}" offset {offset:.14f}\n' for name, offset in entries),
        encoding="utf-8",
    )


def _write_complete_server_take(take: Path) -> Path:
    """Publish one valid schema-v2 take and return its opaque media path."""

    take.mkdir()
    filename = "Alice-127_0_0_1_52000-0-1.wav"
    _write_wav(take / filename, seconds=0.1)
    _write_lof(take / "take.lof", (filename, 0.0))
    result = write_take_manifest(
        take,
        expected_tracks=1,
        required_local_stems=0,
        recording_receipts=(_receipt("Alice", 52000),),
    )
    if not result.ok:
        raise AssertionError(result.errors)
    return take / "server-media-001.wav"


def _recording_plan(
    session_id: str,
    take_id: str,
    participant_id: str,
    *,
    channels: int,
) -> SessionRecordingPlan:
    return SessionRecordingPlan(
        session_id=session_id,
        take_id=take_id,
        plan_generation=1,
        roster=((participant_id, "Alice"),),
        expected_server_stems=(participant_id,),
        server_channel_counts=(channels,),
        count_in_frames=0,
        pre_roll_frames=0,
        storage=RecordingStorageCheck(
            RecordingStorageStatus.READY,
            "ok",
            10_000_000,
            1_000,
        ),
        expected_source_count=1,
        created_at_utc="2026-08-16T12:00:00Z",
    )


class TestLofParsing(unittest.TestCase):
    def test_parses_files_and_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            lof = Path(d) / "take.lof"
            lof.write_text(
                'file "guitar.wav" offset 0\n'
                'file "bass.wav" offset 3.5\n'
                'file "vocals.wav"\n',
                encoding="utf-8",
            )
            offsets = parse_lof_offsets(lof)
        self.assertEqual(offsets["guitar.wav"], 0.0)
        self.assertEqual(offsets["bass.wav"], 3.5)
        self.assertEqual(offsets["vocals.wav"], 0.0)

    def test_basename_keying_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            lof = Path(d) / "take.lof"
            lof.write_text(
                'file "/srv/recordings/x/drums.wav" offset 1.25\n'
                "garbage line\n"
                'file "keys.wav" offset notanumber\n',
                encoding="utf-8",
            )
            offsets = parse_lof_offsets(lof)
        self.assertEqual(offsets["drums.wav"], 1.25)
        self.assertEqual(offsets.get("keys.wav"), 0.0)  # bad offset -> 0
        self.assertNotIn("garbage", offsets)

    def test_missing_file_returns_empty(self):
        self.assertEqual(parse_lof_offsets(Path("/nonexistent.lof")), {})


class TestLoadTake(unittest.TestCase):
    def test_creator_profile_loads_from_session_and_legacy_missing_is_music(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            podcast = root / "podcast"
            _write_complete_server_take(podcast)
            manifest_path = podcast / "webjam-take.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["session"] = {"creator_profile_key": "podcast_voice"}
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            legacy = root / "legacy"
            legacy.mkdir()
            _write_wav(legacy / "voice.wav", seconds=0.1)

            podcast_info = load_take(podcast)
            legacy_info = load_take(legacy)

        self.assertIsNotNone(podcast_info)
        self.assertEqual(podcast_info.creator_profile_key, "podcast_voice")
        self.assertIsNotNone(legacy_info)
        self.assertEqual(legacy_info.creator_profile_key, "music")

    def test_explicit_unsupported_creator_profile_uses_generic_fail_closed_state(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            _write_complete_server_take(take)
            manifest_path = take / "webjam-take.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["session"] = {"creator_profile_key": "future_private_profile"}
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            info = load_take(take)

        self.assertIsNotNone(info)
        self.assertEqual(info.creator_profile_key, "")
        self.assertEqual(info.validation_status, "needs_attention")
        finding = " ".join(info.manifest_errors)
        self.assertIn("generic review labels", finding)
        self.assertNotIn("future_private_profile", finding)

    def test_staging_identity_rejects_non_regular_markers_without_opening(self):
        import os

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            marker = take / ".webjam-recording-staging.json"
            marker.mkdir()
            self.assertIsNone(recording_staging_identity(take))
            marker.rmdir()
            if hasattr(os, "mkfifo"):
                os.mkfifo(marker)
                self.assertIsNone(recording_staging_identity(take))

    def test_builds_tracks_with_offsets_and_durations(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "2026-07-05_take1"
            take.mkdir()
            _write_wav(take / "guitar.wav", seconds=2.0)
            _write_wav(take / "bass_guitar.wav", seconds=1.0)
            (take / "take.lof").write_text(
                'file "guitar.wav" offset 0\nfile "bass_guitar.wav" offset 1.5\n',
                encoding="utf-8",
            )
            (take / "take.rpp").write_text("<REAPER_PROJECT>\n", encoding="utf-8")
            info = load_take(take)

        self.assertIsNotNone(info)
        self.assertEqual(info.track_count, 2)
        self.assertIsNotNone(info.reaper_project)
        by_name = {t.name: t for t in info.tracks}
        self.assertIn("Guitar", by_name)
        self.assertIn("Bass Guitar", by_name)  # prettified from bass_guitar
        self.assertAlmostEqual(by_name["Bass Guitar"].offset_s, 1.5)
        self.assertAlmostEqual(by_name["Guitar"].duration_s, 2.0, places=1)
        # take duration = latest end: bass starts 1.5, lasts 1.0 -> 2.5;
        # guitar 0..2.0 -> 2.0; max = 2.5
        self.assertAlmostEqual(info.duration_s, 2.5, places=1)

    def test_folder_without_audio_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "notes_only"
            empty.mkdir()
            (empty / "readme.txt").write_text("hi", encoding="utf-8")
            self.assertIsNone(load_take(empty))

    def test_no_lof_defaults_offsets_zero(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "a.wav")
            info = load_take(take)
        self.assertEqual(info.tracks[0].offset_s, 0.0)

    def test_native_recorder_filename_is_never_used_as_display_name(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            private_name = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / private_name, seconds=0.1)

            info = load_take(take)

        self.assertIsNotNone(info)
        self.assertEqual(info.tracks[0].name, "Unverified Jamulus source")
        self.assertNotIn("Alice", info.tracks[0].name)
        self.assertNotIn("52000", info.tracks[0].name)

    def test_manifest_declared_missing_track_remains_visible_and_invalidates_take(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host.wav", seconds=0.1)
            (take / "webjam-take.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "complete",
                        "tracks": [
                            {
                                "filename": "host.wav",
                                "name": "Host",
                                "source": "jamulus_server",
                            },
                            {
                                "filename": "guest.wav",
                                "name": "Guest",
                                "source": "jamulus_server",
                                "duration_s": 2.0,
                                "sample_rate": 48000,
                                "offset_s": 0.5,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            info = load_take(take)

        self.assertIsNotNone(info)
        self.assertEqual([track.name for track in info.tracks], ["Host", "Guest"])
        guest = info.tracks[1]
        self.assertEqual(guest.path.name, "guest.wav")
        self.assertEqual(guest.media_status, "missing")
        self.assertEqual(guest.duration_s, 2.0)
        self.assertEqual(guest.samplerate, 48000)
        self.assertEqual(info.duration_s, 2.5)
        self.assertEqual(info.validation_status, "needs_attention")
        self.assertTrue(
            any("Guest is missing" in error for error in info.manifest_errors)
        )

    def test_schema_v2_rejects_same_content_external_media_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            take = root / "take"
            media = _write_complete_server_take(take)
            outside = root / "outside-private.wav"
            media.replace(outside)
            try:
                media.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            info = load_take(take)
            validation = validate_take(take, expected_tracks=1)

        self.assertIsNotNone(info)
        self.assertEqual(info.validation_status, "needs_attention")
        self.assertEqual(info.tracks[0].media_status, "damaged")
        self.assertEqual(info.tracks[0].segments[0].media_status, "damaged")
        self.assertFalse(validation.ok)
        findings = " ".join(info.manifest_errors)
        self.assertIn("not a regular file inside the take", findings)
        self.assertNotIn("outside-private", findings)

    def test_schema_v2_ignores_unlisted_audio_and_blocks_complete_status(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            _write_complete_server_take(take)
            _write_wav(take / "injected-private-name.wav", seconds=0.2)

            info = load_take(take)
            validation = validate_take(take, expected_tracks=1)

        self.assertIsNotNone(info)
        self.assertEqual(info.track_count, 1)
        self.assertEqual(info.validation_status, "needs_attention")
        self.assertFalse(validation.ok)
        findings = " ".join(info.manifest_errors)
        self.assertIn("outside its verified media inventory", findings)
        self.assertNotIn("injected-private-name", findings)

    def test_schema_v2_never_uses_legacy_fallback_for_invalid_segments(self):
        mutations = {
            "missing": lambda track: track.pop("segments"),
            "empty": lambda track: track.__setitem__("segments", []),
            "non_list": lambda track: track.__setitem__("segments", {}),
            "malformed": lambda track: track.__setitem__(
                "segments", [{"path": track["filename"]}]
            ),
        }
        for case, mutate in mutations.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                take = Path(d) / "take"
                media = _write_complete_server_take(take)
                manifest_path = take / "webjam-take.json"
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(payload["tracks"][0])
                manifest_path.write_text(json.dumps(payload), encoding="utf-8")

                # A same-shape media change used to pass when the segment list
                # was absent and load_take fell back to flat legacy fields.
                with wave.open(str(media), "wb") as changed:
                    changed.setnchannels(1)
                    changed.setsampwidth(2)
                    changed.setframerate(48_000)
                    changed.writeframes(struct.pack("<4800h", *([1] * 4800)))

                info = load_take(take)
                validation = validate_take(take, expected_tracks=1)

                self.assertIsNotNone(info)
                self.assertEqual(info.validation_status, "needs_attention")
                self.assertEqual(info.track_count, 1)
                self.assertEqual(info.tracks[0].media_status, "damaged")
                self.assertEqual(info.tracks[0].segments, ())
                self.assertFalse(validation.ok)
                self.assertIn("invalid media inventory", " ".join(info.manifest_errors))


class TestDiscoverTakes(unittest.TestCase):
    def test_discovers_multiple_and_sorts_newest_first(self):
        import os
        import time

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = root / "old_take"
            new = root / "new_take"
            old.mkdir()
            new.mkdir()
            _write_wav(old / "g.wav")
            _write_wav(new / "g.wav")
            past = time.time() - 1000
            os.utime(old, (past, past))
            takes = discover_takes(root)
        names = [t.name for t in takes]
        self.assertIn("old_take", names)
        self.assertIn("new_take", names)
        self.assertEqual(names[0], "new_take")  # newest first

    def test_missing_root_is_empty(self):
        self.assertEqual(discover_takes("/nonexistent/path/xyz"), [])

    def test_scan_failure_log_hides_private_root_and_exception(self):
        with tempfile.TemporaryDirectory() as d:
            private_error = f"permission denied while reading {d}/Secret Session"
            with (
                patch(
                    "core.take_library.load_take",
                    side_effect=OSError(private_error),
                ),
                self.assertLogs("webjam.take_library", level="WARNING") as captured,
            ):
                self.assertEqual(discover_takes(d), [])

        rendered = "\n".join(captured.output)
        self.assertNotIn(d, rendered)
        self.assertNotIn(private_error, rendered)
        self.assertIn("takes library could not be scanned", rendered)

    def test_root_itself_as_single_take(self):
        with tempfile.TemporaryDirectory() as d:
            _write_wav(Path(d) / "solo.wav")
            takes = discover_takes(d)
        self.assertEqual(len(takes), 1)
        self.assertEqual(takes[0].track_count, 1)

    def test_discovers_strict_evidence_only_project_without_media(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            take_dir = root / "Recovered interrupted take"
            take_id = new_project_id()
            result = write_take_manifest(
                take_dir,
                expected_tracks=0,
                required_local_stems=0,
                capture_errors=("Interrupted recording evidence recovered.",),
                session_title="Recovered recording evidence",
                session_id=new_project_id(),
                take_id=take_id,
                session_evidence=SessionEvidence(
                    recovery_status=RecoveryStatus.NEEDS_ATTENTION,
                    recovery_notes=("No media survived the interruption.",),
                    timeline=(SessionTimelineEvent("recording_evidence_recovered"),),
                ),
            )

            takes = discover_takes(root)
            validation = validate_take(take_dir)
            payload = json.loads((take_dir / "webjam-take.json").read_text())

        self.assertIsNotNone(result.take)
        self.assertEqual(len(takes), 1)
        recovered = takes[0]
        self.assertEqual(recovered.take_id, take_id)
        self.assertEqual(recovered.track_count, 0)
        self.assertTrue(recovered.review_only)
        self.assertFalse(recovered.is_exportable)
        self.assertIn("cannot be exported", recovered.export_block_reason)
        self.assertFalse(validation.ok)
        self.assertEqual(validation.summary, "Review only · no audio preserved")
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["tracks"], [])

    def test_empty_folder_and_empty_legacy_manifest_remain_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "empty-folder").mkdir()
            legacy = root / "empty-legacy"
            legacy.mkdir()
            (legacy / "webjam-take.json").write_text(
                json.dumps({"schema_version": 1, "tracks": []}),
                encoding="utf-8",
            )

            takes = discover_takes(root)

        self.assertEqual(takes, [])


class TestTakeValidation(unittest.TestCase):
    def test_pinned_filename_parser_never_calls_start_frame_a_channel_id(self):
        parsed = parse_jamulus_recording_filename("Alice-192_0_2_x_50000-96000-2_3.wav")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.start_frame, 96_000)
        self.assertEqual(parsed.channels, 2)
        self.assertEqual(parsed.collision_index, 3)
        self.assertNotIn("192_0_2", repr(parsed))
        self.assertIsNone(parse_jamulus_recording_filename("unsafe/path.wav"))

    def test_authenticated_roster_validation_erases_addresses_and_detects_collision(
        self,
    ):
        observations = recorder_client_observations(
            {
                "connections": 1,
                "clients": [
                    {
                        "id": 7,
                        "name": "Alice",
                        "address": "192.0.2.44:50000",
                        "channels": 2,
                    }
                ],
            }
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].server_channel_id, 7)
        self.assertEqual(observations[0].channels, 2)
        self.assertNotIn("192.0.2", repr(observations[0]))
        self.assertNotIn("50000", repr(observations[0]))

        with self.assertRaises(RecorderRosterError):
            recorder_client_observations({"connections": 2, "clients": []})
        with self.assertRaises(RecorderRosterError) as collision:
            recorder_client_observations(
                {
                    "connections": 2,
                    "clients": [
                        {
                            "id": 1,
                            "name": "Alice",
                            "address": "192.0.2.44:50000",
                            "channels": 1,
                        },
                        {
                            "id": 2,
                            "name": "Alice",
                            "address": "192.0.2.99:50000",
                            "channels": 1,
                        },
                    ],
                }
            )
        self.assertEqual(len(collision.exception.conflicted_keys), 1)
        self.assertNotIn("192.0.2", str(collision.exception))

    def test_owned_reference_match_requires_exact_loopback_udp_port(self):
        observations = recorder_client_observations(
            {
                "connections": 3,
                "clients": [
                    {
                        "id": 1,
                        "name": "WebJam Track",
                        "address": "127.0.0.1:51000",
                        "channels": 1,
                    },
                    {
                        "id": 2,
                        "name": "WebJam Track",
                        "address": "127.0.0.1:51001",
                        "channels": 1,
                    },
                    {
                        "id": 3,
                        "name": "WebJam Track",
                        "address": "192.0.2.44:51000",
                        "channels": 1,
                    },
                ],
            },
            owned_reference_udp_port=51000,
        )
        self.assertEqual(
            [item.matches_owned_reference for item in observations],
            [True, False, False],
        )

    def test_empty_jamulus_name_is_valid_and_keeps_exact_recorder_digest(self):
        observations = recorder_client_observations(
            {
                "connections": 1,
                "clients": [
                    {
                        "id": 9,
                        "name": "",
                        "address": "127.0.0.1:51002",
                        "channels": 1,
                    }
                ],
            }
        )
        parsed = parse_jamulus_recording_filename("____-127_0_0_1_51002-0-1.wav")

        self.assertIsNotNone(parsed)
        self.assertEqual(observations[0].display_name, "Musician")
        self.assertEqual(
            observations[0].recorder_key_sha256,
            parsed.recorder_key_sha256,
        )

    def test_ipv4_mapped_ipv6_uses_qt_dotted_recorder_key(self):
        observations = recorder_client_observations(
            {
                "connections": 1,
                "clients": [
                    {
                        "id": 10,
                        "name": "Alice",
                        "address": "::ffff:192.0.2.44:50000",
                        "channels": 1,
                    }
                ],
            }
        )
        parsed = parse_jamulus_recording_filename(
            "Alice-__ffff_192_0_2_x_50000-0-1.wav"
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(
            observations[0].recorder_key_sha256,
            parsed.recorder_key_sha256,
        )

    def test_astral_name_matches_qt_utf16_qchar_translation(self):
        observation = recorder_client_observations(
            {
                "connections": 1,
                "clients": [
                    {
                        "id": 10,
                        "name": "A😀B",
                        "address": "127.0.0.1:50000",
                        "channels": 1,
                    }
                ],
            }
        )[0]
        parsed = parse_jamulus_recording_filename("A__B-127_0_0_1_50000-0-1.wav")

        self.assertIsNotNone(parsed)
        self.assertEqual(observation.recorder_key_sha256, parsed.recorder_key_sha256)

    def test_pinned_roster_accepts_150_channels_and_rejects_id_150(self):
        clients = [
            {
                "id": channel_id,
                "name": f"Musician {channel_id}",
                "address": f"127.0.0.1:{20_000 + channel_id}",
                "channels": 1,
            }
            for channel_id in range(150)
        ]
        self.assertEqual(
            len(
                recorder_client_observations(
                    {
                        "connections": 150,
                        "clients": clients,
                    }
                )
            ),
            150,
        )
        invalid = dict(clients[-1])
        invalid["id"] = 150
        with self.assertRaises(RecorderRosterError):
            recorder_client_observations(
                {
                    "connections": 1,
                    "clients": [invalid],
                }
            )
        with self.assertRaises(RecorderRosterError):
            recorder_client_observations(
                {
                    "connections": 151,
                    "clients": [*clients, invalid],
                }
            )

    def test_proven_reference_reconnects_group_as_lof_timed_opaque_segments(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            first = "WebJam_Track-127_0_0_1_51000-0-1.wav"
            # Jamulus's startFrame counts server network frames, not 48-kHz
            # PCM samples. Only the LOF's converted 2.0-second value sets the
            # project position.
            second = "WebJam_Track-127_0_0_1_51000-1500-1.wav"
            _write_wav(take / first, seconds=0.1)
            _write_wav(take / second, seconds=0.1)
            _write_lof(take / "take.lof", (first, 0.0), (second, 2.0))
            (take / "take.rpp").write_text(
                f'NAME WebJam_Track-127_0_0_1_51000\nFILE "{take / first}"\n',
                encoding="utf-8",
            )
            participant_id = str(uuid.uuid4())
            receipt = _receipt(
                "WebJam Track",
                51000,
                participant_id=participant_id,
                source_kind="reference_track",
            )

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt,),
            )
            payload_text = (take / "webjam-take.json").read_text()
            payload = json.loads(payload_text)
            lof_text = (take / "take.lof").read_text()
            rpp_text = (take / "take.rpp").read_text()

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(len(payload["tracks"]), 1)
            track = payload["tracks"][0]
            self.assertEqual(track["participant_id"], participant_id)
            self.assertEqual(track["source"], "live_reference")
            self.assertEqual(
                [item["project_start_frame"] for item in track["segments"]],
                [0, 96_000],
            )
            self.assertEqual(
                [item["path"] for item in track["segments"]],
                ["server-media-001.wav", "server-media-002.wav"],
            )
            self.assertFalse((take / first).exists())
            self.assertFalse((take / second).exists())
            for persisted in (payload_text, lof_text, rpp_text):
                self.assertNotIn("51000", persisted)
                self.assertNotIn("127_0_0_1", persisted)

    def test_proven_musician_reconnect_keys_group_as_ordered_segments(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            first = "Alex-127_0_0_1_52000-0-1.wav"
            second = "Alex-127_0_0_1_52001-100-1.wav"
            _write_wav(take / first, seconds=0.1)
            _write_wav(take / second, seconds=0.1)
            _write_lof(take / "take.lof", (first, 0.0), (second, 1.0))
            participant_id = str(uuid.uuid4())

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(
                    _receipt("Alex", 52000, participant_id=participant_id),
                    _receipt("Alex", 52001, participant_id=participant_id),
                ),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(len(payload["tracks"]), 1)
            self.assertEqual(payload["tracks"][0]["participant_id"], participant_id)
            self.assertEqual(
                [
                    segment["project_start_frame"]
                    for segment in payload["tracks"][0]["segments"]
                ],
                [0, 48_000],
            )
            self.assertEqual(
                [segment["path"] for segment in payload["tracks"][0]["segments"]],
                ["server-media-001.wav", "server-media-002.wav"],
            )

    def test_missing_or_conflicting_receipt_preserves_audio_needs_attention(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-4800-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.1))
            receipt = _receipt("Alice", 52000)
            conflicting = RecorderClientReceipt(
                server_channel_id=receipt.server_channel_id,
                display_name=receipt.display_name,
                participant_id=str(uuid.uuid4()),
                recorder_key_sha256=receipt.recorder_key_sha256,
                channels=receipt.channels,
            )

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt, conflicting),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

            self.assertFalse(result.ok)
            self.assertIsNotNone(result.take)
            self.assertEqual(payload["status"], "needs_attention")
            self.assertEqual(len(payload["tracks"]), 1)
            self.assertIsNone(payload["tracks"][0]["participant_id"])
            self.assertEqual(payload["tracks"][0]["filename"], "server-media-001.wav")
            self.assertTrue((take / "server-media-001.wav").is_file())
            self.assertTrue(any("conflicted" in item for item in payload["errors"]))

    def test_native_filename_without_receipt_is_opaque_and_needs_attention(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
            )
            persisted = "\n".join(
                item.read_text(encoding="utf-8")
                for item in take.iterdir()
                if item.suffix in {".json", ".lof", ".rpp"}
            )

            self.assertFalse(result.ok)
            self.assertFalse((take / filename).exists())
            self.assertTrue((take / "server-media-001.wav").is_file())
            self.assertNotIn("52000", persisted)
            self.assertNotIn("127_0_0_1", persisted)

    def test_malformed_and_duplicate_lof_never_publish_complete(self):
        for lof_text in (
            'file "Alice-127_0_0_1_52000-0-1.wav" offset not-a-number\n',
            'file "Alice-127_0_0_1_52000-0-1.wav" offset 0.0\n'
            'file "Alice-127_0_0_1_52000-0-1.wav" offset 1.0\n',
        ):
            with self.subTest(lof_text=lof_text), tempfile.TemporaryDirectory() as d:
                take = Path(d) / "take"
                take.mkdir()
                filename = "Alice-127_0_0_1_52000-0-1.wav"
                _write_wav(take / filename, seconds=0.1)
                (take / "take.lof").write_text(lof_text, encoding="utf-8")

                result = write_take_manifest(
                    take,
                    expected_tracks=1,
                    required_local_stems=0,
                    recording_receipts=(_receipt("Alice", 52000),),
                )
                payload = json.loads((take / "webjam-take.json").read_text())

                self.assertFalse(result.ok)
                self.assertEqual(
                    payload["tracks"][0]["segments"][0]["project_start_frame"],
                    0,
                )
                self.assertTrue(
                    any("LOF timing evidence" in item for item in payload["errors"])
                )

    def test_rpp_absolute_path_and_changed_name_are_privacy_staged(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            (take / "take.rpp").write_text(
                "NAME Renamed_Musician-127_0_0_1_52000\n"
                f'FILE "/Volumes/Private Session/{filename}"\n',
                encoding="utf-8",
            )

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(_receipt("Alice", 52000),),
            )
            rpp = (take / "take.rpp").read_text()

            self.assertTrue(result.ok, result.errors)
            self.assertIn("NAME WebJam recorded source", rpp)
            self.assertIn('FILE "server-media-001.wav"', rpp)
            for private in ("Volumes", "Private Session", "52000", "127_0_0_1"):
                self.assertNotIn(private, rpp)

    def test_privacy_staging_rolls_back_lof_and_wav_after_rpp_write_failure(self):
        from core.file_io import atomic_write_text as real_atomic_write_text

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            (take / "take.rpp").write_text(
                f'NAME Alice-127_0_0_1_52000\nFILE "{take / filename}"\n',
                encoding="utf-8",
            )
            original_lof = (take / "take.lof").read_bytes()
            original_rpp = (take / "take.rpp").read_bytes()
            calls = 0

            def fail_second_sidecar(path, text, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("synthetic write failure")
                return real_atomic_write_text(path, text, **kwargs)

            with (
                patch(
                    "core.file_io.atomic_write_text",
                    side_effect=fail_second_sidecar,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "Server recording privacy staging failed",
                ),
            ):
                write_take_manifest(
                    take,
                    expected_tracks=1,
                    required_local_stems=0,
                    recording_receipts=(_receipt("Alice", 52000),),
                )

            self.assertTrue((take / filename).is_file())
            self.assertFalse((take / "server-media-001.wav").exists())
            self.assertEqual((take / "take.lof").read_bytes(), original_lof)
            self.assertEqual((take / "take.rpp").read_bytes(), original_rpp)

    def test_privacy_staging_rejects_oversized_or_excess_sidecars_before_read(self):
        for case in ("oversized", "too_many"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                take = Path(d) / "take"
                take.mkdir()
                filename = "Alice-127_0_0_1_52000-0-1.wav"
                _write_wav(take / filename, seconds=0.1)
                _write_lof(take / "take.lof", (filename, 0.0))
                if case == "oversized":
                    with (take / "take.rpp").open("wb") as sidecar:
                        sidecar.truncate(8 * 1024 * 1024 + 1)
                else:
                    for index in range(5):
                        (take / f"take-{index}.rpp").write_text(
                            "<REAPER_PROJECT>\n", encoding="utf-8"
                        )

                with self.assertRaisesRegex(OSError, "project evidence was unsafe"):
                    write_take_manifest(
                        take,
                        expected_tracks=1,
                        required_local_stems=0,
                        recording_receipts=(_receipt("Alice", 52000),),
                    )

                self.assertTrue((take / filename).is_file())
                self.assertFalse((take / "server-media-001.wav").exists())
                self.assertFalse((take / ".webjam-recording-staging.json").exists())

    def test_complete_manifest_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            receipt = _receipt("Alice", 52000)

            first = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt,),
            )
            first_manifest = (take / "webjam-take.json").read_bytes()
            second = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt,),
            )

            self.assertTrue(first.ok, first.errors)
            self.assertTrue(second.ok, second.errors)
            self.assertEqual((take / "webjam-take.json").read_bytes(), first_manifest)
            self.assertFalse((take / ".webjam-recording-staging.json").exists())

    def test_complete_manifest_retry_detects_same_shape_media_tampering(self):
        import hashlib

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            receipt = _receipt("Alice", 52000)

            first = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt,),
            )
            self.assertTrue(first.ok, first.errors)
            manifest_path = take / "webjam-take.json"
            original_manifest = manifest_path.read_bytes()
            payload = json.loads(original_manifest)
            segment = payload["tracks"][0]["segments"][0]
            media_path = take / segment["path"]
            original_size = media_path.stat().st_size
            original_hash = segment["sha256"]

            frames = int(segment["frame_count"])
            channels = int(segment["channels"])
            with wave.open(str(media_path), "wb") as changed:
                changed.setnchannels(channels)
                changed.setsampwidth(2)
                changed.setframerate(int(segment["sample_rate"]))
                samples = frames * channels
                changed.writeframes(struct.pack(f"<{samples}h", *([1] * samples)))

            self.assertEqual(media_path.stat().st_size, original_size)
            self.assertNotEqual(
                hashlib.sha256(media_path.read_bytes()).hexdigest(), original_hash
            )

            second = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt,),
            )

            self.assertFalse(second.ok)
            self.assertEqual(manifest_path.read_bytes(), original_manifest)
            self.assertIsNotNone(second.take)
            self.assertEqual(second.take.validation_status, "needs_attention")
            self.assertEqual(second.take.tracks[0].segments[0].media_status, "damaged")
            self.assertIn("A recorded segment changed after validation.", second.errors)

    def test_address_free_staging_receipt_recovers_interrupted_publication(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.5))
            receipt = _receipt("Alice", 52000)

            with (
                patch(
                    "core.take_library.validate_take",
                    side_effect=RuntimeError("synthetic publication interruption"),
                ),
                self.assertRaisesRegex(RuntimeError, "publication interruption"),
            ):
                write_take_manifest(
                    take,
                    expected_tracks=1,
                    required_local_stems=0,
                    recording_receipts=(receipt,),
                )

            staging_path = take / ".webjam-recording-staging.json"
            staging = staging_path.read_text()
            self.assertTrue((take / "server-media-001.wav").is_file())
            for private in (filename, "Alice", "52000", "127_0_0_1"):
                self.assertNotIn(private, staging)

            recovered = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(receipt,),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

            self.assertTrue(recovered.ok, recovered.errors)
            self.assertEqual(
                payload["tracks"][0]["participant_id"], receipt.participant_id
            )
            self.assertEqual(
                payload["tracks"][0]["segments"][0]["project_start_frame"],
                24_000,
            )
            self.assertFalse(staging_path.exists())

    def test_partial_wav_rename_crash_resumes_from_media_bound_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            alice = "Alice-127_0_0_1_52000-0-1.wav"
            bob = "Bob_-127_0_0_1_52001-100-1.wav"
            _write_wav(take / alice, seconds=0.1)
            _write_wav(take / bob, seconds=0.2)
            _write_lof(take / "take.lof", (alice, 0.0), (bob, 1.0))
            (take / "take.rpp").write_text(
                f'NAME Alice-127_0_0_1_52000\nFILE "{take / alice}"\n'
                f'NAME Bob_-127_0_0_1_52001\nFILE "{take / bob}"\n',
                encoding="utf-8",
            )
            alice_receipt = _receipt("Alice", 52000)
            bob_receipt = _receipt("Bob", 52001, channel_id=1)
            real_replace = Path.replace
            wav_renames = 0

            def interrupt_second_wav(source, target):
                nonlocal wav_renames
                if source.suffix == ".wav":
                    wav_renames += 1
                    if wav_renames == 2:
                        raise SystemExit("synthetic process loss")
                return real_replace(source, target)

            with (
                patch.object(
                    Path,
                    "replace",
                    autospec=True,
                    side_effect=interrupt_second_wav,
                ),
                self.assertRaisesRegex(SystemExit, "process loss"),
            ):
                write_take_manifest(
                    take,
                    expected_tracks=2,
                    required_local_stems=0,
                    recording_receipts=(alice_receipt, bob_receipt),
                )

            self.assertTrue((take / "server-media-001.wav").is_file())
            self.assertTrue((take / bob).is_file())
            self.assertTrue((take / ".webjam-recording-staging.json").is_file())

            recovered = write_take_manifest(
                take,
                expected_tracks=2,
                required_local_stems=0,
                recording_receipts=(alice_receipt, bob_receipt),
            )
            payload_text = (take / "webjam-take.json").read_text()
            payload = json.loads(payload_text)
            lof_text = (take / "take.lof").read_text()
            rpp_text = (take / "take.rpp").read_text()

            self.assertTrue(recovered.ok, recovered.errors)
            self.assertEqual(len(payload["tracks"]), 2)
            self.assertTrue((take / "server-media-002.wav").is_file())
            self.assertEqual(
                sorted(
                    track["segments"][0]["project_start_frame"]
                    for track in payload["tracks"]
                ),
                [0, 48_000],
            )
            for persisted in (payload_text, lof_text, rpp_text):
                for private in ("52000", "52001", "127_0_0_1"):
                    self.assertNotIn(private, persisted)

    def test_process_restart_without_live_receipts_is_honestly_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_52000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            receipt = _receipt("Alice", 52000)

            with (
                patch(
                    "core.take_library.validate_take",
                    side_effect=RuntimeError("synthetic process loss"),
                ),
                self.assertRaises(RuntimeError),
            ):
                write_take_manifest(
                    take,
                    expected_tracks=1,
                    required_local_stems=0,
                    recording_receipts=(receipt,),
                )

            interrupted = load_take(take)
            self.assertIsNotNone(interrupted)
            self.assertEqual(interrupted.validation_status, "needs_attention")
            self.assertTrue(
                any(
                    "publication was interrupted" in item
                    for item in interrupted.manifest_errors
                )
            )

            recovered = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                # A new process has no authenticated in-memory receipt.
                recording_receipts=(),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

            self.assertFalse(recovered.ok)
            self.assertIsNone(payload["tracks"][0]["participant_id"])
            self.assertEqual(payload["status"], "needs_attention")

    def test_manifest_capture_errors_never_persist_paths_or_private_filenames(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "guitar.wav", seconds=0.1)

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                capture_errors=(
                    "Recoverable parts remain in /Volumes/Band Archive/Secret.wav",
                    r"Could not attach C:\Users\Jeff\Private Mix.wav: denied",
                    r"Writer failed at \\studio-nas\private\Hidden Take.wav",
                ),
                recording_identity_errors=(
                    "identity failed at /private/tmp/Secret Song.wav",
                    "roster failed at 203.0.113.7:50000 with token=private",
                    "roster failed at [2001:db8::1]:50000 for user@example.com",
                ),
            )
            manifest = (take / "webjam-take.json").read_text()

            self.assertFalse(result.ok)
            for private in (
                "Volumes",
                "Band Archive",
                "Secret.wav",
                "Users",
                "Private Mix.wav",
                "studio-nas",
                "Hidden Take.wav",
                "private/tmp",
                "Secret Song.wav",
                "203.0.113.7",
                "2001:db8",
                "user@example.com",
                "token=private",
            ):
                self.assertNotIn(private, manifest)
            self.assertIn("local recording", manifest)
            self.assertIn("identity evidence needs attention", manifest)

    def test_channel_mode_change_groups_under_same_proven_participant(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            mono = "Alice-127_0_0_1_52000-0-1.wav"
            stereo = "Alice-127_0_0_1_52000-100-2.wav"
            _write_wav(take / mono, seconds=0.1, channels=1)
            _write_wav(take / stereo, seconds=0.1, channels=2)
            _write_lof(take / "take.lof", (mono, 0.0), (stereo, 1.0))
            participant_id = str(uuid.uuid4())

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(
                    _receipt("Alice", 52000, participant_id=participant_id),
                    _receipt(
                        "Alice",
                        52000,
                        participant_id=participant_id,
                        channels=2,
                    ),
                ),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

            self.assertFalse(result.ok)
            self.assertEqual(len(payload["tracks"]), 1)
            self.assertEqual(len(payload["tracks"][0]["segments"]), 2)
            self.assertEqual(payload["status"], "needs_attention")
            self.assertTrue(
                any("channel layout" in error for error in result.errors),
                result.errors,
            )

    def test_reconnect_segments_do_not_satisfy_two_expected_participants(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            first = "Alice-127_0_0_1_52000-0-1.wav"
            second = "Alice-127_0_0_1_52000-100-1.wav"
            _write_wav(take / first, seconds=0.1)
            _write_wav(take / second, seconds=0.1)
            _write_lof(take / "take.lof", (first, 0.0), (second, 1.0))
            participant_id = str(uuid.uuid4())

            result = write_take_manifest(
                take,
                expected_tracks=2,
                required_local_stems=0,
                recording_receipts=(
                    _receipt("Alice", 52000, participant_id=participant_id),
                ),
            )

            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    "Expected at least 2 tracks but found 1" in item
                    for item in result.errors
                )
            )

    def test_lof_is_authoritative_without_treating_start_frame_as_samples(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Alice-127_0_0_1_53000-4800-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.25))

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(_receipt("Alice", 53000),),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(
                payload["tracks"][0]["segments"][0]["project_start_frame"],
                12_000,
            )
            self.assertEqual(payload["errors"], [])

    def test_manifest_uses_live_musician_names_for_server_tracks(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Jeff-127_0_0_1_50000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                session_title="Sunday Rehearsal",
                recording_receipts=(_receipt("Jeff", 50000),),
            )
        self.assertIsNotNone(result.take)
        self.assertEqual(result.take.tracks[0].name, "Jeff")
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.take.session_title, "Sunday Rehearsal")
        self.assertEqual(result.take.display_name, "Sunday Rehearsal")

    def test_manifest_marks_the_backing_song_as_a_live_reference(self):
        """The host's reference song must not look like another musician.

        It reaches the server as an ordinary Jamulus client, so without an
        explicit source type the take treats the song as a performance and
        Studio comps and mixes it like one.
        """

        source_fingerprint = "ab" * 32
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            musician_file = "Jeff-127_0_0_1_50000-0-1.wav"
            reference_file = "WebJam_Track-127_0_0_1_50001-1-1.wav"
            _write_wav(take / musician_file, seconds=0.1)
            _write_wav(take / reference_file, seconds=0.1)
            _write_lof(
                take / "take.lof",
                (musician_file, 0.0),
                (reference_file, 1 / 48_000),
            )
            result = write_take_manifest(
                take,
                expected_tracks=2,
                required_local_stems=0,
                required_reference_track=True,
                session_title="Sunday Rehearsal",
                recording_receipts=(
                    _receipt("Jeff", 50000),
                    _receipt(
                        "WebJam Track",
                        50001,
                        channel_id=1,
                        source_kind="reference_track",
                        source_fingerprint_sha256=source_fingerprint,
                        playback_generation=7,
                    ),
                ),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

        by_name = {track["name"]: track for track in payload["tracks"]}

        self.assertEqual(by_name["Jeff"]["source"], "jamulus_server")
        self.assertEqual(by_name["WebJam Track"]["source"], "live_reference")
        self.assertEqual(by_name["WebJam Track"]["quality"], "reference")
        self.assertEqual(
            by_name["WebJam Track"]["alignment"]["reference_fingerprint_sha256"],
            source_fingerprint,
        )
        self.assertEqual(
            by_name["WebJam Track"]["alignment"]["reference_playback_generation"],
            7,
        )
        self.assertIsNotNone(result.take)
        self.assertTrue(result.ok, result.errors)

    def test_required_shared_track_stem_fails_closed_without_owned_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Jeff-127_0_0_1_50000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))

            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                required_reference_track=True,
                recording_receipts=(_receipt("Jeff", 50000),),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "needs_attention")
        self.assertTrue(
            any("exact band-server stem" in error for error in payload["errors"])
        )
        self.assertEqual(payload["tracks"][0]["source"], "jamulus_server")

    def test_required_shared_track_is_enforced_on_immutable_complete_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "Jeff-127_0_0_1_50000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            first = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(_receipt("Jeff", 50000),),
            )
            manifest_path = take / "webjam-take.json"
            published = manifest_path.read_bytes()

            revalidated = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                required_reference_track=True,
                recording_receipts=(_receipt("Jeff", 50000),),
            )

            self.assertTrue(first.ok, first.errors)
            self.assertFalse(revalidated.ok)
            self.assertTrue(
                any("exact band-server stem" in error for error in revalidated.errors)
            )
            self.assertEqual(manifest_path.read_bytes(), published)

    def test_a_musician_named_like_the_reference_is_still_a_musician(self):
        """Display text alone never grants Reference Track ownership."""

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            filename = "WebJam_Track-127_0_0_1_50000-0-1.wav"
            _write_wav(take / filename, seconds=0.1)
            _write_lof(take / "take.lof", (filename, 0.0))
            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                recording_receipts=(_receipt("WebJam Track", 50000),),
            )
            payload = json.loads((take / "webjam-take.json").read_text())

        self.assertEqual(payload["tracks"][0]["source"], "jamulus_server")
        self.assertTrue(result.ok, result.errors)

    def test_reports_expected_track_shortfall_and_silence(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "guitar.wav", seconds=0.1)
            result = validate_take(take, expected_tracks=2)
        self.assertFalse(result.ok)
        self.assertIn("Expected at least 2", result.errors[0])
        self.assertTrue(any("silent" in warning for warning in result.warnings))

    def test_reports_mixed_samplerates(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "one.wav", seconds=0.1, rate=48000)
            _write_wav(take / "two.wav", seconds=0.1, rate=44100)
            result = validate_take(take)
        self.assertFalse(result.ok)
        self.assertTrue(any("sample rates" in error for error in result.errors))

    def test_rejects_single_non_48k_track(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "one.wav", seconds=0.1, rate=44100)
            result = validate_take(take)
        self.assertFalse(result.ok)
        self.assertTrue(any("48 kHz" in error for error in result.errors))

    def test_manifest_classifies_local_stems_and_blocks_silent_alignment(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "server-host.wav", seconds=0.1)
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "host-vocal.wav", seconds=0.1)
            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=2,
                app_version="test",
            )
            manifest = json.loads((take / "webjam-take.json").read_text())
            loaded = load_take(take)
        self.assertFalse(result.ok)
        self.assertEqual(manifest["status"], "needs_attention")
        self.assertTrue(any("aligned confidently" in e for e in result.errors))
        self.assertEqual(
            len([track for track in loaded.tracks if track.source == "local_isolated"]),
            2,
        )
        self.assertEqual(loaded.validation_status, "needs_attention")

    def test_manifest_does_not_store_capture_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "server.wav", seconds=0.1)
            write_take_manifest(take, expected_tracks=1, required_local_stems=0)
            text = (take / "webjam-take.json").read_text()
        self.assertNotIn("secret", text.lower())

    def test_recovery_manifest_marks_audio_beyond_durable_checkpoint_partial(self):
        """Recovered PCM never claims frames after the last fsynced checkpoint."""
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "Recovered-local"
            take.mkdir()
            _write_wav(take / "host-guitar.recovered-partial.wav", seconds=0.01)
            result = write_take_manifest(
                take,
                expected_tracks=0,
                required_local_stems=0,
                local_total_frames=480,
                local_durable_frames=240,
            )
            data = json.loads((take / "webjam-take.json").read_text())

        self.assertFalse(result.ok)
        track = data["tracks"][0]
        self.assertEqual(track["media_status"], "partial")
        self.assertEqual(track["segments"][0]["media_status"], "partial")
        self.assertIn(
            {
                "start_frame": 240,
                "frame_count": 240,
                "reason": "unverified_after_crash_checkpoint",
                "channels": [0],
            },
            track["segments"][0]["gaps"],
        )
        self.assertTrue(any("durably checkpointed" in error for error in result.errors))

    def test_recovery_manifest_retains_stereo_track_and_gap_identity(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "Recovered-local"
            take.mkdir()
            _write_wav(
                take / "local-Rehearsal.recovered-partial.wav",
                seconds=0.01,
                channels=2,
            )
            track = LocalCaptureTrack("local-Rehearsal", (0, 1))
            gap = SimpleNamespace(
                start_frame=100,
                frame_count=25,
                channels=(0,),
                reason="queue_overflow",
            )

            result = write_take_manifest(
                take,
                expected_tracks=0,
                required_local_stems=1,
                capture_gaps=(gap,),
                local_capture_tracks=(track,),
                local_total_frames=480,
                local_durable_frames=240,
            )
            payload = json.loads((take / "webjam-take.json").read_text())

        self.assertFalse(result.ok)
        self.assertEqual(len(payload["tracks"]), 1)
        segment = payload["tracks"][0]["segments"][0]
        self.assertEqual(segment["channels"], 2)
        self.assertIn(
            {
                "start_frame": 100,
                "frame_count": 25,
                "reason": "queue_overflow",
                "channels": [0, 1],
            },
            segment["gaps"],
        )
        self.assertIn(
            {
                "start_frame": 240,
                "frame_count": 240,
                "reason": "unverified_after_crash_checkpoint",
                "channels": [0, 1],
            },
            segment["gaps"],
        )

    def test_final_manifest_v2_has_stable_ids_exact_media_hash_and_capture_gap(self):
        import hashlib
        import uuid

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "host-vocal.wav", seconds=0.1)
            server_filename = "Band-127_0_0_1_50000-0-1.wav"
            _write_wav(take / server_filename, seconds=0.1)
            _write_lof(take / "take.lof", (server_filename, 0.0))
            session_id = str(uuid.uuid4())
            take_id = str(uuid.uuid4())
            participant_id = str(uuid.uuid4())
            local_id = str(uuid.uuid4())
            gap = SimpleNamespace(
                start_frame=1200,
                frame_count=240,
                channels=(0,),
                reason="queue_overflow",
            )
            device = CaptureDevice(
                device_id="coreaudio:test-input",
                display_name="Test Input",
                backend="Core Audio",
                sample_rate=48000,
                channel_indices=(0, 1),
                channel_labels=("Guitar", "Vocal"),
            )

            write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=2,
                session_id=session_id,
                take_id=take_id,
                local_participant_id=local_id,
                local_participant_name="Jeff",
                capture_device=device,
                capture_gaps=(gap,),
                local_total_frames=4800,
                recording_receipts=(
                    _receipt(
                        "Band",
                        50000,
                        participant_id=participant_id,
                    ),
                ),
            )
            data = json.loads((take / "webjam-take.json").read_text())
            expected_guitar_size = (take / "host-guitar.wav").stat().st_size
            expected_guitar_hash = hashlib.sha256(
                (take / "host-guitar.wav").read_bytes()
            ).hexdigest()

        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["session_id"], session_id)
        self.assertEqual(data["take_id"], take_id)
        self.assertEqual(
            len({item["participant_id"] for item in data["participants"]}), 2
        )
        self.assertEqual(data["devices"][0]["device_id"], device.device_id)
        server = next(
            item for item in data["tracks"] if item["source"] == "jamulus_server"
        )
        guitar = next(
            item for item in data["tracks"] if item["filename"] == "host-guitar.wav"
        )
        vocal = next(
            item for item in data["tracks"] if item["filename"] == "host-vocal.wav"
        )
        self.assertEqual(server["participant_id"], participant_id)
        self.assertEqual(guitar["participant_id"], local_id)
        self.assertEqual(vocal["participant_id"], local_id)
        segment = guitar["segments"][0]
        self.assertEqual(segment["frame_count"], 4800)
        self.assertEqual(segment["sample_rate"], 48000)
        self.assertEqual(segment["channels"], 1)
        self.assertEqual(segment["sample_format"], "PCM_16")
        self.assertEqual(segment["size_bytes"], expected_guitar_size)
        self.assertEqual(segment["sha256"], expected_guitar_hash)
        self.assertEqual(
            segment["gaps"],
            [
                {
                    "start_frame": 1200,
                    "frame_count": 240,
                    "reason": "queue_overflow",
                    "channels": [0],
                }
            ],
        )
        self.assertEqual(vocal["segments"][0]["gaps"], [])

    def test_stereo_local_original_keeps_one_track_and_exact_gap_topology(self):
        """Typed input order, not filename order, owns logical gap identity."""

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "local-Zeta Bus.wav", seconds=0.1, channels=2)
            _write_wav(take / "local-Alpha Vocal.wav", seconds=0.1, channels=1)
            tracks = (
                LocalCaptureTrack("local-Zeta Bus", (0, 1)),
                LocalCaptureTrack("local-Alpha Vocal", (2,)),
            )
            gap = SimpleNamespace(
                start_frame=1200,
                frame_count=240,
                channels=(0,),
                reason="queue_overflow",
            )
            device = CaptureDevice(
                device_id="coreaudio:stereo-test",
                display_name="Stereo Test",
                backend="Core Audio",
                sample_rate=48000,
                channel_indices=(0, 1, 2),
                channel_labels=("Bus L", "Bus R", "Voice"),
            )

            result = write_take_manifest(
                take,
                expected_tracks=0,
                required_local_stems=2,
                local_participant_name="Jeff",
                capture_device=device,
                capture_gaps=(gap,),
                local_capture_tracks=tracks,
                local_total_frames=4800,
            )
            payload = json.loads((take / "webjam-take.json").read_text())
            loaded = load_take(take)

        zeta = next(item for item in payload["tracks"] if "Zeta Bus" in item["name"])
        alpha = next(
            item for item in payload["tracks"] if "Alpha Vocal" in item["name"]
        )
        self.assertEqual(len(payload["tracks"]), 2)
        self.assertEqual(len(zeta["segments"]), 1)
        self.assertEqual(zeta["segments"][0]["channels"], 2)
        self.assertEqual(
            zeta["segments"][0]["gaps"],
            [
                {
                    "start_frame": 1200,
                    "frame_count": 240,
                    "reason": "queue_overflow",
                    "channels": [0, 1],
                }
            ],
        )
        self.assertEqual(alpha["segments"][0]["channels"], 1)
        self.assertEqual(alpha["segments"][0]["gaps"], [])
        loaded_zeta = next(item for item in loaded.tracks if "Zeta Bus" in item.name)
        self.assertEqual(loaded_zeta.channel_count, 2)
        self.assertTrue(loaded_zeta.has_supported_channel_topology)
        # No server reference was present, so alignment still truthfully needs
        # attention; that must not erase or split the stereo source evidence.
        self.assertFalse(result.ok)

    def test_local_original_channel_mismatch_is_preserved_but_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "local-Synth.wav", seconds=0.1, channels=1)

            result = write_take_manifest(
                take,
                expected_tracks=0,
                required_local_stems=1,
                local_capture_tracks=(LocalCaptureTrack("local-Synth", (0, 1)),),
                local_total_frames=4800,
            )
            payload = json.loads((take / "webjam-take.json").read_text())

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "needs_attention")
        self.assertEqual(payload["tracks"][0]["segments"][0]["channels"], 1)
        self.assertTrue(
            any("bound mono/stereo" in error for error in result.errors),
            result.errors,
        )

    def test_manifest_preserves_session_evidence_adds_host_and_indexes_media_gap(self):
        import uuid

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "host-vocal.wav", seconds=0.1)
            _write_wav(take / "Band-127_0_0_1_50000-0-1.wav", seconds=0.1)
            session_id = str(uuid.uuid4())
            take_id = str(uuid.uuid4())
            local_id = str(uuid.uuid4())
            host_id = str(uuid.uuid4())
            evidence = SessionEvidence(
                protocol_version="jamulus-3.12.2 / webjam-v2",
                started_utc="2026-07-14T02:00:00Z",
                ended_utc="2026-07-14T02:05:00Z",
                host=HostIdentity(host_id, "Jeff Story"),
                recovery_status=RecoveryStatus.RECOVERED,
                recovery_notes=("Local capture resumed from a safe checkpoint.",),
                timeline=(
                    SessionTimelineEvent(
                        "recording_started",
                        occurred_utc="2026-07-14T02:00:00Z",
                        participant_id=host_id,
                    ),
                ),
            )
            gap = SimpleNamespace(
                start_frame=1200,
                frame_count=240,
                channels=(0,),
                reason="queue_overflow",
            )

            write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=2,
                session_id=session_id,
                take_id=take_id,
                local_participant_id=local_id,
                local_participant_name="Local input",
                capture_gaps=(gap,),
                local_total_frames=4800,
                session_evidence=evidence,
            )
            data = json.loads((take / "webjam-take.json").read_text())

        self.assertEqual(data["schema_version"], 2)
        self.assertIn(
            {
                "participant_id": host_id,
                "display_name": "Jeff Story",
                "instrument": "",
            },
            data["participants"],
        )
        session = data["session"]
        self.assertEqual(session["protocol_version"], evidence.protocol_version)
        self.assertEqual(session["started_utc"], evidence.started_utc)
        self.assertEqual(session["ended_utc"], evidence.ended_utc)
        self.assertEqual(
            session["host"],
            {
                "participant_id": host_id,
                "display_name": "Jeff Story",
            },
        )
        self.assertEqual(session["recovery_status"], "recovered")
        self.assertEqual(
            session["recovery_notes"],
            ["Local capture resumed from a safe checkpoint."],
        )
        self.assertIn(
            {
                "event": "recording_started",
                "occurred_utc": "2026-07-14T02:00:00Z",
                "participant_id": host_id,
            },
            session["timeline"],
        )
        media_gap = next(
            item for item in session["timeline"] if item["event"] == "media_gap"
        )
        self.assertEqual(media_gap["participant_id"], local_id)
        self.assertEqual(media_gap["at_s"], 0.025)
        self.assertIn(
            "240 source frames unavailable (queue_overflow)", media_gap["detail"]
        )

    def test_load_schema2_retains_nested_reconnect_segments_rates_and_gaps(self):
        import hashlib

        import numpy as np
        import soundfile as sf

        from core.take_project import (
            AlignmentState,
            GapInterval,
            MediaSegment,
            MediaStatus,
            Participant,
            ProjectStatus,
            ProjectTrack,
            SourceQuality,
            SourceType,
            TakeProject,
            new_project_id,
            write_take_project,
        )

        with tempfile.TemporaryDirectory() as d:
            take_dir = Path(d) / "take"
            nested = take_dir / "transferred-isolated"
            nested.mkdir(parents=True)
            first = nested / "first.wav"
            second = nested / "second.wav"
            sf.write(first, np.full(4410, 0.2, dtype="float32"), 44100)
            sf.write(second, np.full(4800, 0.3, dtype="float32"), 48000)
            participant_id = new_project_id()
            segments = (
                MediaSegment(
                    new_project_id(),
                    "transferred-isolated/first.wav",
                    4800,
                    4410,
                    44100,
                    1,
                    "PCM_16",
                    sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
                    size_bytes=first.stat().st_size,
                    gaps=(GapInterval(100, 50, "network interruption"),),
                ),
                MediaSegment(
                    new_project_id(),
                    "transferred-isolated/second.wav",
                    14400,
                    4800,
                    48000,
                    1,
                    "PCM_16",
                    sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
                    size_bytes=second.stat().st_size,
                ),
            )
            track = ProjectTrack(
                new_project_id(),
                new_project_id(),
                participant_id,
                "Guest Guitar",
                "Guitar",
                SourceType.LOCAL_ISOLATED,
                SourceQuality.VERIFIED_ISOLATED,
                MediaStatus.AVAILABLE,
                0,
                segments,
                AlignmentState(
                    automatic_offset_s=0.01,
                    drift_ppm=500.0,
                    confidence=0.9,
                    method="test-alignment",
                ),
            )
            project = TakeProject(
                new_project_id(),
                new_project_id(),
                "Session",
                "Take",
                ProjectStatus.COMPLETE,
                48000,
                (Participant(participant_id, "Guest", "Guitar"),),
                (track,),
            )
            write_take_project(take_dir, project)
            loaded = load_take(take_dir)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.project_samplerate, 48000)
        self.assertEqual(len(loaded.tracks), 1)
        loaded_track = loaded.tracks[0]
        self.assertEqual(len(loaded_track.segments), 2)
        self.assertEqual(
            [segment.samplerate for segment in loaded_track.segments],
            [44100, 48000],
        )
        self.assertEqual(loaded_track.segments[1].project_start_frame, 14400)
        self.assertEqual(
            loaded_track.segments[0].gaps,
            ((100, 50, (), "network interruption"),),
        )
        self.assertEqual(loaded_track.offset_s, 0.01)
        self.assertEqual(loaded_track.drift_ppm, 500.0)
        self.assertEqual(loaded_track.alignment_method, "test-alignment")
        self.assertAlmostEqual(loaded_track.duration_s, 0.40005, places=4)

    def test_alignment_recovers_known_server_delay(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(7)
            guitar = np.zeros(rate * 5, dtype="float32")
            guitar[rate : rate * 3] = rng.normal(0, 0.1, rate * 2)
            vocal = np.zeros_like(guitar)
            server = np.concatenate((np.zeros(rate // 2, dtype="float32"), guitar))
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", server, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, 0.5, delta=0.02)
        self.assertGreater(confidence, 0.9)

    def test_alignment_failure_log_hides_media_path_and_exception(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "Secret Session"
            take.mkdir()
            for name in ("host-guitar.wav", "host-vocal.wav", "server-host.wav"):
                (take / name).write_bytes(b"placeholder")
            private_error = f"decoder failed at {take / 'host-guitar.wav'}"

            with (
                patch("soundfile.read", side_effect=RuntimeError(private_error)),
                self.assertLogs("webjam.take_library", level="ERROR") as captured,
            ):
                offset, confidence = estimate_local_alignment(take)

        rendered = "\n".join(captured.output)
        self.assertEqual((offset, confidence), (0.0, 0.0))
        self.assertNotIn(str(take), rendered)
        self.assertNotIn(private_error, rendered)
        self.assertNotIn("Traceback", rendered)

    def test_alignment_recovers_negative_offset_when_local_leads(self):
        """Local capture arms before the server recorder starts, so the local
        stems normally lead the server take: the offset must come back
        negative, not clamped to zero."""
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(11)
            performance = rng.normal(0, 0.1, rate * 3).astype("float32")
            lead_in = int(rate * 0.75)
            guitar = np.concatenate((np.zeros(lead_in, dtype="float32"), performance))
            vocal = np.zeros_like(guitar)
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", performance, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, -0.75, delta=0.02)
        self.assertGreater(confidence, 0.9)

    def test_alignment_is_sample_accurate_off_the_envelope_grid(self):
        """An offset that is not a multiple of the 10 ms envelope block must
        still be recovered to within a millisecond by the refinement pass."""
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(3)
            guitar = np.zeros(rate * 5, dtype="float32")
            guitar[rate : rate * 3] = rng.normal(0, 0.1, rate * 2)
            vocal = np.zeros_like(guitar)
            delay = 24_137  # ≈0.5028 s: deliberately off the 480-sample grid
            server = np.concatenate((np.zeros(delay, dtype="float32"), guitar))
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", server, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, delay / rate, delta=0.001)
        self.assertGreater(confidence, 0.9)

    def test_alignment_reports_low_confidence_for_unrelated_audio(self):
        """Uncorrelated program material must not manufacture confidence."""
        import numpy as np
        import soundfile as sf

        from core.take_library import ALIGNMENT_CONFIDENCE_MIN

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(5)
            guitar = rng.normal(0, 0.1, rate * 5).astype("float32")
            vocal = np.zeros_like(guitar)
            unrelated = rng.normal(0, 0.1, rate * 5).astype("float32")
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", unrelated, rate)
            _offset, confidence = estimate_local_alignment(take)
        self.assertLess(confidence, ALIGNMENT_CONFIDENCE_MIN)

    def test_alignment_handles_stereo_server_track(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(9)
            guitar = np.zeros(rate * 5, dtype="float32")
            guitar[rate : rate * 3] = rng.normal(0, 0.1, rate * 2)
            vocal = np.zeros_like(guitar)
            delayed = np.concatenate((np.zeros(rate // 4, dtype="float32"), guitar))
            stereo = np.stack((delayed, delayed), axis=1)
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", stereo, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, 0.25, delta=0.02)
        self.assertGreater(confidence, 0.9)

    def test_load_take_round_trips_negative_manifest_offset(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "server-host.wav", seconds=0.1)
            (take / "webjam-take.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "complete",
                        "tracks": [
                            {
                                "filename": "host-guitar.wav",
                                "source": "local_ssl",
                                "offset_s": -1.25,
                            },
                            {
                                "filename": "server-host.wav",
                                "source": "jamulus_server",
                                "offset_s": None,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_take(take)
        stem = next(t for t in loaded.tracks if t.source == "local_ssl")
        self.assertAlmostEqual(stem.offset_s, -1.25)

    def test_snapshot_finds_new_take(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            before = snapshot_take_directories(root)
            new = root / "new"
            new.mkdir()
            _write_wav(new / "track.wav", seconds=0.1)
            self.assertEqual(find_changed_take(root, before), new)

    def test_manifest_binds_plan_source_id_and_rejects_server_width_substitution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            session_id = new_project_id()
            participant_id = new_project_id()

            def write(channels: int, planned_channels: int):
                take_id = new_project_id()
                take = root / take_id
                take.mkdir()
                filename = "Alice-127_0_0_1_52000-0-2.wav"
                _write_wav(take / filename, seconds=0.1, channels=channels)
                _write_lof(take / "take.lof", (filename, 0.0))
                receipt = _receipt(
                    "Alice",
                    52000,
                    participant_id=participant_id,
                    channels=channels,
                )
                plan = _recording_plan(
                    session_id,
                    take_id,
                    participant_id,
                    channels=planned_channels,
                )
                return write_take_manifest(
                    take,
                    expected_tracks=1,
                    required_local_stems=0,
                    session_id=session_id,
                    take_id=take_id,
                    recording_receipts=(receipt,),
                    recording_plan=plan,
                ), plan

            exact, exact_plan = write(2, 2)
            substituted, _wrong_plan = write(2, 1)

        self.assertTrue(exact.ok, exact.errors)
        self.assertEqual(
            exact.take.tracks[0].logical_source_id,
            exact_plan.logical_source_id_for_server(participant_id),
        )
        self.assertFalse(substituted.ok)
        self.assertIn("planned mono/stereo", " ".join(substituted.errors))

    def test_snapshot_rejects_multiple_changed_take_directories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            before = snapshot_take_directories(root)
            for name in ("recorder-take", "unrelated-take"):
                candidate = root / name
                candidate.mkdir()
                _write_wav(candidate / "track.wav", seconds=0.1)
            self.assertIsNone(find_changed_take(root, before))

    def test_snapshot_ignores_private_work_directories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            before = snapshot_take_directories(root)
            take = root / "Jamulus-2026-07-14"
            take.mkdir()
            _write_wav(take / "track.wav", seconds=0.1)
            # A final lifecycle/evidence checkpoint can be newer than the
            # server folder. It must never become the candidate take.
            journal = root / ".webjam-recording-evidence"
            journal.mkdir()
            _write_wav(journal / "would-be-audio.wav", seconds=0.1)

            after = snapshot_take_directories(root)
            self.assertNotIn(journal, after)
            self.assertEqual(find_changed_take(root, before), take)
            self.assertEqual([item.path for item in discover_takes(root)], [take])


if __name__ == "__main__":
    unittest.main()
