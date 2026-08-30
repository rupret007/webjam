"""
Band-server RPC session + the Record button.

Unit-tests JamulusServerRpc against a fake NDJSON server (auth, recorder
calls, error surfaces, timeouts) and the Conductor's Record button wiring
(unconfigured guidance, worker success/failure paths, button state).
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.jamulus_server_rpc import (  # noqa: E402
    JamulusServerRpc,
    ServerRpcError,
    read_secret_file,
)


pytestmark = pytest.mark.requires_local_socket


def _make_staged_server_take(
    root: Path,
    folder_name: str,
    *,
    session_id: str,
    take_id: str,
    client_name: str,
    port: int,
    start_frame: int = 0,
) -> tuple[Path, str]:
    """Create one valid v2 publication receipt with an unrenamed native WAV."""

    import hashlib
    import struct
    import wave

    from core.take_library import parse_jamulus_recording_filename

    take = root / folder_name
    take.mkdir()
    native = f"{client_name}-127_0_0_1_{port}-{start_frame}-1.wav"
    media = take / native
    frames = 2_400 + start_frame % 101
    with wave.open(str(media), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48_000)
        audio.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))
    parsed = parse_jamulus_recording_filename(native)
    if parsed is None:
        raise AssertionError("test recorder filename did not parse")
    (take / "take.lof").write_text(
        f'file "{native}" offset {start_frame / 48_000:.14f}\n',
        encoding="utf-8",
    )
    marker = {
        "schema": 2,
        "session_id": session_id,
        "take_id": take_id,
        "entries": [
            {
                "filename": "server-media-001.wav",
                "recorder_key_sha256": parsed.recorder_key_sha256,
                "start_frame": parsed.start_frame,
                "channels": parsed.channels,
                "collision_index": parsed.collision_index,
                "offset_s": start_frame / 48_000,
                "size_bytes": media.stat().st_size,
                "sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
            }
        ],
    }
    (take / ".webjam-recording-staging.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    return take, native


class _FakeJamulusServer:
    """Minimal jamulusserver/* JSON-RPC endpoint for tests."""

    def __init__(self, secret="server-secret-0123456789"):
        self.secret = secret
        self.recorder_enabled = False
        self.received: list[dict] = []
        self.fail_method: str | None = None  # answer this method with an error
        self.mute_method: str | None = None  # never answer this method
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._conn = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            self._srv.settimeout(5.0)
            self._conn, _ = self._srv.accept()
        except OSError:
            return
        f = self._conn.makefile("r", encoding="utf-8", newline="\n")
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            self.received.append(obj)
            self._handle(obj)

    def _reply(self, obj):
        try:
            self._conn.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

    def _handle(self, obj):
        method, rid = obj.get("method"), obj.get("id")
        if method == self.mute_method:
            return
        if method == self.fail_method:
            self._reply(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -1, "message": "nope"}}
            )
            return
        if method == "jamulus/apiAuth":
            ok = (obj.get("params") or {}).get("secret") == self.secret
            self._reply(
                {"jsonrpc": "2.0", "id": rid, "result": "ok" if ok else "not ok"}
            )
        elif method == "jamulusserver/startRecording":
            self.recorder_enabled = True
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "acknowledged"})
        elif method == "jamulusserver/stopRecording":
            self.recorder_enabled = False
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "acknowledged"})
        elif method == "jamulusserver/restartRecording":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "acknowledged"})
        elif method == "jamulusserver/getRecorderStatus":
            self._reply(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "initialised": True,
                        "enabled": self.recorder_enabled,
                        "recordingDirectory": "/recordings",
                        "errorMessage": "",
                    },
                }
            )
        elif method == "jamulusserver/getClients":
            self._reply(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "connections": 2,
                        "clients": [{"id": 0}, {"id": 1}],
                    },
                }
            )

    def stop(self):
        for s in (self._conn, self._srv):
            try:
                if s:
                    s.close()
            except OSError:
                pass


class TestJamulusServerRpc(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeJamulusServer()

    def tearDown(self):
        self.fake.stop()

    def _rpc(self, secret=None):
        return JamulusServerRpc(
            port=self.fake.port,
            secret=secret or self.fake.secret,
        )

    def test_full_record_cycle(self):
        with self._rpc() as rpc:
            self.assertTrue(rpc.start_recording())
            self.assertTrue(rpc.get_recorder_status()["enabled"])
            self.assertTrue(rpc.restart_recording())
            self.assertTrue(rpc.stop_recording())
            self.assertFalse(rpc.get_recorder_status()["enabled"])

    def test_get_clients(self):
        with self._rpc() as rpc:
            result = rpc.get_clients()
        self.assertEqual(result["connections"], 2)

    def test_wrong_secret_raises_actionable_error(self):
        with self.assertRaises(ServerRpcError) as ctx:
            self._rpc(secret="wrong").connect()
        self.assertIn("refused the RPC secret", str(ctx.exception))

    def test_unreachable_port_covers_same_mac_and_remote_server(self):
        dead = JamulusServerRpc(port=1, secret="x")
        with self.assertRaises(ServerRpcError) as ctx:
            dead.connect()
        message = str(ctx.exception)
        self.assertIn("same-Mac server", message)
        self.assertIn("SSH tunnel", message)

    def test_error_response_raises(self):
        self.fake.fail_method = "jamulusserver/startRecording"
        with self._rpc() as rpc:
            with self.assertRaises(ServerRpcError) as ctx:
                rpc.start_recording()
        message = str(ctx.exception)
        self.assertEqual(
            message,
            "jamulusserver/startRecording was rejected by the band server.",
        )
        self.assertNotIn("nope", message)

    def test_call_timeout_raises(self):
        self.fake.mute_method = "jamulusserver/getRecorderStatus"
        with self._rpc() as rpc:
            with patch.object(JamulusServerRpc, "CALL_TIMEOUT_S", 0.5):
                with self.assertRaises(ServerRpcError) as ctx:
                    rpc.get_recorder_status()
        self.assertIn("timed out", str(ctx.exception))

    def test_call_without_connect_raises(self):
        with self.assertRaises(ServerRpcError):
            self._rpc().start_recording()


class TestReadSecretFile(unittest.TestCase):
    def test_missing_file_is_actionable(self):
        with self.assertRaises(ServerRpcError) as ctx:
            read_secret_file("/nonexistent/jsonrpc.secret")
        self.assertIn("jsonrpc.secret", str(ctx.exception))

    def test_empty_file_rejected(self):
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".secret") as f:
            f.flush()
            with self.assertRaises(ServerRpcError):
                read_secret_file(f.name)

    def test_reads_and_strips(self):
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".secret", delete=False) as f:
            f.write("  the-secret \n")
        self.assertEqual(read_secret_file(f.name), "the-secret")


class TestRecordButtonWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls._runtime_home_directory = TemporaryDirectory(
            prefix="webjam-record-button-"
        )
        cls.addClassCleanup(cls._runtime_home_directory.cleanup)
        runtime_home = Path(cls._runtime_home_directory.name)
        cls._runtime_home_patch = patch(
            "pathlib.Path.home",
            return_value=runtime_home,
        )
        cls._runtime_home_patch.start()
        cls.addClassCleanup(cls._runtime_home_patch.stop)
        cls._app = QApplication.instance() or QApplication([])
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import (
            ApplicationController,
        )
        from webjam_qt.windows.conductor_window import ConductorWindow

        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        settings = AppSettings(
            config_file=str(runtime_home / "settings.json"),
            mix_file=str(runtime_home / "mix.json"),
            log_file=str(runtime_home / "webjam.log"),
        )
        cls.controller = ApplicationController(cls.window, settings=settings)

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def setUp(self):
        c = self.controller
        c.window.flash_message = MagicMock()
        c._recorder_armed = False
        c._server_recording = False
        c.recording.phase = c.recording.phase.__class__.IDLE
        c.recording._local_capture = None
        c.recording._take_id = ""
        c.recording._reset_session_evidence()
        with c.recording._evidence_lock:
            c.recording._recording_plan = None
            c.recording._recording_plan_take_id = ""
            c.recording._recording_plan_fingerprint = ""
        c.settings.local_capture_enabled = False
        c.settings.musician_name = "Test Musician"
        c.settings.takes_directory = ""
        c.settings.server_rpc_secret_file = ""
        # A prior test may have left the button disabled mid-toggle.
        self.window.session_strip.set_recording_phase("idle")
        # The strip signal is wired to the real handler; with no secret file
        # configured it would open a MODAL error dialog and hang the suite —
        # mock it (tests that assert on it use this mock).
        c._show_actionable_error = MagicMock()
        # Start-path tests exercise coordinator authority, not the modal UI.
        # The dialog/model have dedicated fail-closed tests; accept the exact
        # immutable snapshot here so recorder worker assertions stay focused.
        c._confirm_recording_readiness = MagicMock(return_value=True)

    def _install_bound_capture_plan(self, input_maps):
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.session_recording_plan import SessionRecordingPlan
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        session_id = new_project_id()
        participant_id = new_project_id()
        plan = SessionRecordingPlan(
            session_id=session_id,
            take_id=take_id,
            plan_generation=1,
            roster=((participant_id, "Host"),),
            expected_server_stems=(participant_id,),
            count_in_frames=0,
            pre_roll_frames=0,
            storage=RecordingStorageCheck(
                status=RecordingStorageStatus.READY,
                detail="Recording storage is ready.",
                free_bytes=10_000_000,
                required_bytes=1_000_000,
            ),
            expected_source_count=(
                1
                + sum(
                    bool(item.enabled and item.local_original_enabled)
                    for item in input_maps
                )
            ),
            created_at_utc="2026-08-16T12:00:00Z",
            input_maps=tuple(input_maps),
            server_channel_counts=(1,),
        )
        c.recording._session_id = session_id
        c.recording._take_id = take_id
        with c.recording._evidence_lock:
            c.recording._recording_plan = plan
            c.recording._recording_plan_take_id = take_id
            c.recording._recording_plan_fingerprint = plan.plan_fingerprint()
        return plan

    def _install_guest_arm_plan(self, *, guest_track_count=1):
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.session_recording_plan import (
            GuestLocalOriginalBinding,
            SessionRecordingPlan,
        )
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        session_id = new_project_id()
        host_id = new_project_id()
        guest_id = new_project_id()
        source_ids = tuple(new_project_id() for _ in range(guest_track_count))
        widths = tuple(1 for _ in range(guest_track_count))
        plan = SessionRecordingPlan(
            session_id=session_id,
            take_id=take_id,
            plan_generation=1,
            roster=((host_id, "Host"), (guest_id, "Guest")),
            expected_server_stems=(host_id, guest_id),
            count_in_frames=0,
            pre_roll_frames=0,
            storage=RecordingStorageCheck(
                status=RecordingStorageStatus.READY,
                detail="Recording storage is ready.",
                free_bytes=10_000_000,
                required_bytes=1_000_000,
            ),
            expected_source_count=2 + guest_track_count,
            created_at_utc="2026-08-16T12:00:00Z",
            guest_local_originals=(
                GuestLocalOriginalBinding(
                    participant_id=guest_id,
                    track_count=guest_track_count,
                    map_fingerprint_sha256="ab" * 32,
                    presence_generation=3,
                    channel_counts=widths,
                    logical_source_ids=source_ids,
                ),
            ),
            server_channel_counts=(1, 1),
        )
        peer = SimpleNamespace(
            active=True,
            publish_capture_arm=MagicMock(
                return_value=SimpleNamespace(arm_generation=7)
            ),
            wait_for_capture_arm_acknowledgements=MagicMock(return_value=True),
            capture_arm_ready=MagicMock(return_value=True),
            cancel_capture_arm=MagicMock(return_value=True),
            discard_prepared_local_original_obligations=MagicMock(return_value=True),
        )
        c.host_peer = peer
        c.recording._session_id = session_id
        c.recording._take_id = take_id
        c.recording.phase = c.recording.phase.__class__.PREFLIGHT
        with c.recording._evidence_lock:
            c.recording._recording_plan = plan
            c.recording._recording_plan_take_id = take_id
            c.recording._recording_plan_fingerprint = plan.plan_fingerprint()
        return plan, peer

    def test_strip_button_emits_signal(self):
        received = []
        self.window.session_strip.record_requested.connect(
            lambda: received.append(True)
        )
        self.window.session_strip._record_button.click()
        self.assertEqual(received, [True])

    def test_shared_track_participant_identity_is_stable_within_the_session(self):
        import uuid

        from core.take_project import new_project_id

        c = self.controller
        original_session_id = c.recording._session_id
        try:
            session_id = new_project_id()
            c.recording._session_id = session_id
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            first_take_identity = c.recording._reference_participant_id

            self.assertEqual(
                first_take_identity,
                str(
                    uuid.uuid5(
                        uuid.UUID(session_id),
                        "participant:shared-track",
                    )
                ),
            )

            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            self.assertEqual(
                c.recording._reference_participant_id,
                first_take_identity,
            )

            next_session_id = new_project_id()
            c.recording._session_id = next_session_id
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            self.assertNotEqual(
                c.recording._reference_participant_id,
                first_take_identity,
            )
            self.assertEqual(
                c.recording._reference_participant_id,
                str(
                    uuid.uuid5(
                        uuid.UUID(next_session_id),
                        "participant:shared-track",
                    )
                ),
            )
        finally:
            c.recording._session_id = original_session_id
            c.recording._take_id = ""
            c.recording._reset_session_evidence()

    def test_planned_shared_track_transaction_waits_for_playback_and_cleanup(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        prior_reference_track = c._reference_track
        try:
            c.recording._take_id = take_id
            c.recording.plan_shared_track_for_next_take(required=True)
            c.recording._begin_shared_track_transaction(take_id)

            # Planning the following take must not rewrite this take's frozen
            # requirement.
            c.recording.plan_shared_track_for_next_take(required=False)
            self.assertTrue(c.recording.shared_track_required_for_active_take)

            playing = SimpleNamespace(
                state=SimpleNamespace(value="playing"),
                active=True,
                cleanup_pending=False,
            )
            ready = SimpleNamespace(
                state=SimpleNamespace(value="ready"),
                active=False,
                cleanup_pending=False,
                underrun_frames=0,
            )
            c._reference_track = SimpleNamespace(snapshot=ready)
            c.recording._confirmed_recording_started()
            c.recording.observe_shared_track_snapshot(playing)
            c.recording._confirmed_recording_stopped()
            c.recording.note_shared_track_cleanup_requested()

            self.assertEqual(
                c.recording._await_shared_track_transaction_errors(take_id),
                (),
            )
        finally:
            c._reference_track = prior_reference_track
            c.recording._retire_active_take(take_id)
            c.recording.plan_shared_track_for_next_take(required=False)

    def test_shared_track_playing_before_recorder_confirmation_is_not_proof(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        prior_reference_track = c._reference_track
        try:
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            c.recording.plan_shared_track_for_next_take(required=True)
            c.recording._begin_shared_track_transaction(take_id)
            playing = SimpleNamespace(
                state=SimpleNamespace(value="playing"),
                active=True,
                cleanup_pending=False,
                underrun_frames=7,
            )
            ready = SimpleNamespace(
                state=SimpleNamespace(value="ready"),
                active=False,
                cleanup_pending=False,
                underrun_frames=7,
            )
            c._reference_track = SimpleNamespace(snapshot=playing)

            c.recording.observe_shared_track_snapshot(playing)
            c.recording._confirmed_recording_started()
            c._reference_track.snapshot = ready
            c.recording._confirmed_recording_stopped()
            c.recording.note_shared_track_cleanup_requested()
            with patch(
                "webjam_qt.controllers.recording_coordinator."
                "_SHARED_TRACK_FINALIZE_TIMEOUT_S",
                0.0,
            ):
                errors = c.recording._await_shared_track_transaction_errors(take_id)

            self.assertEqual(len(errors), 1)
            self.assertIn("never reached confirmed playback", errors[0])
        finally:
            c._reference_track = prior_reference_track
            c.recording._retire_active_take(take_id)
            c.recording.plan_shared_track_for_next_take(required=False)

    def test_shared_track_count_in_is_an_authoritative_recorder_phase(self):
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        prior_reference_track = c._reference_track
        prior_phase = c.recording.phase
        try:
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            c.recording.plan_shared_track_for_next_take(required=True)
            c.recording._begin_shared_track_transaction(take_id)
            c.recording._set_phase(RecorderPhase.RECORDING)
            c._reference_track = SimpleNamespace(
                snapshot=SimpleNamespace(underrun_frames=0)
            )
            c.recording._confirmed_recording_started()

            c.recording.observe_shared_track_snapshot(
                SimpleNamespace(
                    state=SimpleNamespace(value="playing"),
                    active=True,
                    cleanup_pending=False,
                    count_in_active=True,
                    underrun_frames=0,
                )
            )
            self.assertIs(c.recording.phase, RecorderPhase.COUNT_IN)
            self.assertEqual(c.recording.snapshot.phase.value, "count_in")

            c.recording.observe_shared_track_snapshot(
                SimpleNamespace(
                    state=SimpleNamespace(value="playing"),
                    active=True,
                    cleanup_pending=False,
                    count_in_active=False,
                    underrun_frames=0,
                )
            )
            self.assertIs(c.recording.phase, RecorderPhase.RECORDING)
        finally:
            c._reference_track = prior_reference_track
            c.recording._retire_active_take(take_id)
            c.recording.plan_shared_track_for_next_take(required=False)
            c.recording.phase = prior_phase

    def test_shared_track_underruns_are_scoped_to_each_recording_window(self):
        from core.take_project import RecoveryStatus, new_project_id

        c = self.controller
        prior_reference_track = c._reference_track
        first_take = new_project_id()
        second_take = new_project_id()
        starting_generation = c.recording.public_diagnostics()["generation"]
        try:
            baseline = SimpleNamespace(
                state=SimpleNamespace(value="ready"),
                active=False,
                cleanup_pending=False,
                underrun_frames=10,
            )
            dropped = SimpleNamespace(
                state=SimpleNamespace(value="playing"),
                active=True,
                cleanup_pending=False,
                underrun_frames=14,
            )
            stopped = SimpleNamespace(
                state=SimpleNamespace(value="ready"),
                active=False,
                cleanup_pending=False,
                underrun_frames=14,
            )
            c._reference_track = SimpleNamespace(snapshot=baseline)
            c.recording._take_id = first_take
            c.recording._begin_recording_diagnostics(first_take)
            c.recording._reset_session_evidence()
            c.recording.plan_shared_track_for_next_take(required=True)
            c.recording._begin_shared_track_transaction(first_take)
            c.recording._confirmed_recording_started()
            c._reference_track.snapshot = dropped
            c.recording.observe_shared_track_snapshot(dropped)
            c._reference_track.snapshot = stopped
            c.recording._confirmed_recording_stopped()

            self.assertEqual(
                c.recording._current_session_evidence().recovery_status,
                RecoveryStatus.NEEDS_ATTENTION,
            )
            first_diagnostics = c.recording.public_diagnostics()
            self.assertEqual(first_diagnostics["dropout_gap_count"], 1)
            self.assertEqual(
                first_diagnostics["failure_reason_code"],
                "shared_track_dropout",
            )
            c.recording._retire_active_take(first_take)

            # The stream's lifetime counter remains at 14. With no take-local
            # delta, the next Record Session must remain clean.
            c.recording._take_id = second_take
            c.recording._begin_recording_diagnostics(second_take)
            c.recording._reset_session_evidence()
            c.recording.plan_shared_track_for_next_take(required=True)
            c.recording._begin_shared_track_transaction(second_take)
            c.recording._confirmed_recording_started()
            c._reference_track.snapshot = SimpleNamespace(
                state=SimpleNamespace(value="playing"),
                active=True,
                cleanup_pending=False,
                underrun_frames=14,
            )
            c.recording.observe_shared_track_snapshot(c._reference_track.snapshot)
            c._reference_track.snapshot = stopped
            c.recording._confirmed_recording_stopped()

            self.assertEqual(
                c.recording._current_session_evidence().recovery_status,
                RecoveryStatus.NOT_NEEDED,
            )
            second_diagnostics = c.recording.public_diagnostics()
            self.assertEqual(
                second_diagnostics["generation"],
                starting_generation + 2,
            )
            self.assertEqual(second_diagnostics["current_take_id"], second_take)
            self.assertEqual(second_diagnostics["last_take_id"], first_take)
            self.assertEqual(second_diagnostics["dropout_gap_count"], 0)
            self.assertEqual(second_diagnostics["failure_reason_code"], "none")
            self.assertEqual(second_diagnostics["failure_category"], "none")
        finally:
            c._reference_track = prior_reference_track
            c.recording._retire_active_take(first_take)
            c.recording._retire_active_take(second_take)
            c.recording.plan_shared_track_for_next_take(required=False)

    def test_planned_shared_track_transaction_fails_closed_without_evidence(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        prior_reference_track = c._reference_track
        try:
            c.recording._take_id = take_id
            c.recording.plan_shared_track_for_next_take(required=True)
            c.recording._begin_shared_track_transaction(take_id)
            c._reference_track = SimpleNamespace(snapshot=None)

            with patch(
                "webjam_qt.controllers.recording_coordinator."
                "_SHARED_TRACK_FINALIZE_TIMEOUT_S",
                0.0,
            ):
                errors = c.recording._await_shared_track_transaction_errors(take_id)

            self.assertEqual(len(errors), 2)
            self.assertIn("never reached confirmed playback", errors[0])
            self.assertIn("cleanup was not confirmed", errors[1])
        finally:
            c._reference_track = prior_reference_track
            c.recording._retire_active_take(take_id)
            c.recording.plan_shared_track_for_next_take(required=False)

    def test_shared_track_loss_during_preflight_refuses_recording_start(self):
        c = self.controller
        prior_reference_track = c._reference_track
        prior_phase = c.recording.phase
        try:
            c.recording.phase = c.recording.phase.__class__.PREFLIGHT
            c.recording.plan_shared_track_for_next_take(required=True)
            c._reference_track = SimpleNamespace(
                snapshot=SimpleNamespace(
                    loaded=False,
                    state=SimpleNamespace(value="failed"),
                )
            )
            with patch.object(c, "_show_actionable_error") as show_error:
                c.recording._begin_recording_start(
                    [],
                    "",
                    hosted_readiness=None,
                )

            self.assertEqual(c.recording.phase.value, "error")
            self.assertEqual(c.recording._take_id, "")
            self.assertFalse(c.recording._pending_shared_track_required)
            guidance = show_error.call_args.kwargs
            self.assertIn("No recorder was started", guidance["what_failed"])
            self.assertIn("remove it before recording", guidance["next_action"])
            self.assertNotIn("retry_callback", guidance)
        finally:
            c._reference_track = prior_reference_track
            c.recording.phase = prior_phase
            c.recording.plan_shared_track_for_next_take(required=False)

    def test_shared_track_without_playback_route_refuses_recording_start(self):
        c = self.controller
        prior_reference_track = c._reference_track
        prior_phase = c.recording.phase
        try:
            c.recording.phase = c.recording.phase.__class__.PREFLIGHT
            c.recording.plan_shared_track_for_next_take(required=True)
            c._reference_track = SimpleNamespace(
                snapshot=SimpleNamespace(
                    loaded=True,
                    state=SimpleNamespace(value="ready"),
                    can_play=False,
                )
            )
            with patch.object(c, "_show_actionable_error") as show_error:
                c.recording._begin_recording_start(
                    [],
                    "",
                    hosted_readiness=None,
                )

            self.assertEqual(c.recording.phase.value, "error")
            self.assertEqual(c.recording._take_id, "")
            self.assertFalse(c.recording._pending_shared_track_required)
            guidance = show_error.call_args.kwargs
            self.assertIn("No recorder was started", guidance["what_failed"])
            self.assertIn("audio route", guidance["likely_cause"])
            self.assertNotIn("retry_callback", guidance)
        finally:
            c._reference_track = prior_reference_track
            c.recording.phase = prior_phase
            c.recording.plan_shared_track_for_next_take(required=False)

    def test_prestart_retry_returns_through_application_record_planning(self):
        c = self.controller
        prior_phase = c.recording.phase
        try:
            c._show_actionable_error.reset_mock()
            c.recording._fail_hosted_recording_readiness(None)

            guidance = c._show_actionable_error.call_args.kwargs
            self.assertEqual(guidance["retry_callback"], c._on_record_requested)
        finally:
            c.recording.phase = prior_phase

    def test_unproven_participant_identity_refuses_before_take_allocation(self):
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )

        c = self.controller
        prior_port = c.settings.server_rpc_port
        prior_secret = c.settings.server_rpc_secret_file
        prior_ids = dict(c.recording._participant_id_by_channel)
        prior_phase = c.recording.phase
        participant = SimpleNamespace(
            channel_id=17,
            name="Unproven guest",
            role="Guest",
            participant_id="",
        )
        storage = RecordingStorageCheck(
            status=RecordingStorageStatus.READY,
            detail="Recording storage is ready.",
            free_bytes=10_000_000,
            required_bytes=1_000_000,
        )
        try:
            c.settings.server_rpc_port = 22124
            c.settings.server_rpc_secret_file = "/private/recording.secret"
            c.recording._participant_id_by_channel = {}
            c.recording.phase = c.recording.phase.__class__.PREFLIGHT
            c._show_actionable_error.reset_mock()
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "_private_secret_file_identity",
                    return_value=(1, 2, 3, 4),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "check_recording_storage",
                    return_value=storage,
                ),
                patch.object(c, "peer_participant_id_for_channel", return_value=""),
            ):
                c.recording._begin_recording_start(
                    [participant],
                    "/private/recording.secret",
                    hosted_readiness=None,
                )

            self.assertEqual(c.recording.phase.value, "error")
            self.assertEqual(c.recording._take_id, "")
            self.assertIsNone(c.recording._recording_plan)
            guidance = c._show_actionable_error.call_args.kwargs
            self.assertIn("durable recording identity", guidance["what_failed"])
            self.assertIn("No recorder was started", guidance["what_failed"])
            self.assertEqual(guidance["retry_callback"], c._on_record_requested)
        finally:
            c.settings.server_rpc_port = prior_port
            c.settings.server_rpc_secret_file = prior_secret
            c.recording._participant_id_by_channel = prior_ids
            c.recording.phase = prior_phase

    def test_recording_plan_gate_requires_exact_sources_and_shared_generation(self):
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.session_recording_plan import (
            InputMapBinding,
            SessionRecordingPlan,
            SharedTrackBinding,
        )
        from core.take_library import RecorderClientReceipt
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        musician_id = new_project_id()
        reference_id = c.recording._reference_participant_id
        source_fingerprint = "ab" * 32
        plan = SessionRecordingPlan(
            session_id=new_project_id(),
            take_id=take_id,
            plan_generation=3,
            roster=((musician_id, "Alice"),),
            expected_server_stems=(musician_id, reference_id),
            count_in_frames=96_000,
            pre_roll_frames=96_000,
            storage=RecordingStorageCheck(
                status=RecordingStorageStatus.READY,
                detail="Recording storage is ready.",
                free_bytes=10_000_000,
                required_bytes=1_000_000,
            ),
            expected_source_count=3,
            created_at_utc="2026-08-15T12:00:00Z",
            shared_track=SharedTrackBinding(source_fingerprint, 8),
            shared_track_planned=True,
            input_maps=(InputMapBinding("Host Mic", 1, True, True),),
            server_channel_counts=(1, 2),
        )
        musician = RecorderClientReceipt(1, "Alice", musician_id, "11" * 32, 1)
        reference = RecorderClientReceipt(
            2,
            "WebJam Track",
            reference_id,
            "22" * 32,
            2,
            "reference_track",
            source_fingerprint,
            8,
        )
        with c.recording._evidence_lock:
            c.recording._recording_plan = plan
            c.recording._recording_plan_take_id = take_id
            c.recording._recording_plan_fingerprint = plan.plan_fingerprint()
        try:
            self.assertEqual(
                c.recording._recording_plan_validation_errors(
                    take_id,
                    (musician, reference),
                    required_local_count=1,
                ),
                (),
            )

            replacement = RecorderClientReceipt(
                2,
                "WebJam Track",
                reference_id,
                "22" * 32,
                2,
                "reference_track",
                source_fingerprint,
                9,
            )
            generation_errors = c.recording._recording_plan_validation_errors(
                take_id,
                (musician, replacement),
                required_local_count=1,
            )
            self.assertTrue(
                any("playback generation" in item for item in generation_errors)
            )

            substitute = RecorderClientReceipt(3, "Bob", new_project_id(), "33" * 32, 1)
            source_errors = c.recording._recording_plan_validation_errors(
                take_id,
                (musician, reference, substitute),
                required_local_count=1,
            )
            self.assertTrue(any("exactly match" in item for item in source_errors))
        finally:
            c.recording._retire_active_take(take_id)

    def test_recording_plan_gate_requires_exact_logical_local_track_topology(self):
        from core.local_capture import LocalCaptureTrack
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.session_recording_plan import InputMapBinding, SessionRecordingPlan
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        musician_id = new_project_id()
        plan = SessionRecordingPlan(
            session_id=new_project_id(),
            take_id=take_id,
            plan_generation=4,
            roster=((musician_id, "Host"),),
            expected_server_stems=(musician_id,),
            count_in_frames=0,
            pre_roll_frames=0,
            storage=RecordingStorageCheck(
                status=RecordingStorageStatus.READY,
                detail="Recording storage is ready.",
                free_bytes=10_000_000,
                required_bytes=1_000_000,
            ),
            expected_source_count=3,
            created_at_utc="2026-08-15T12:00:00Z",
            input_maps=(
                InputMapBinding("Host Mic", 1, True, True),
                InputMapBinding("Room Pair", 2, True, True),
            ),
            creator_profile_key="podcast_voice",
            server_channel_counts=(1,),
        )
        with c.recording._evidence_lock:
            c.recording._recording_plan = plan
            c.recording._recording_plan_take_id = take_id
            c.recording._recording_plan_fingerprint = plan.plan_fingerprint()
        try:
            observed = plan.resolved_capture_tracks()
            self.assertEqual(
                c.recording._local_capture_plan_validation_errors(
                    take_id,
                    observed,
                    required_local_count=2,
                ),
                (),
            )
            wrong = (
                LocalCaptureTrack(
                    "local-Host Mic",
                    (0,),
                    logical_source_id=observed[0].logical_source_id,
                ),
                LocalCaptureTrack(
                    "local-Room Pair",
                    (2, 3),
                    logical_source_id=observed[1].logical_source_id,
                ),
            )
            self.assertTrue(
                c.recording._local_capture_plan_validation_errors(
                    take_id,
                    wrong,
                    required_local_count=2,
                )
            )
            self.assertTrue(
                c.recording._local_capture_plan_validation_errors(
                    take_id,
                    (),
                    required_local_count=2,
                )
            )
        finally:
            c.recording._retire_active_take(take_id)

    def test_recording_plan_binds_and_rechecks_exact_guest_local_originals(self):
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.session_recording_plan import (
            InputMapBinding,
            SessionRecordingPlan,
        )
        from core.take_library import RecorderClientReceipt
        from core.take_project import new_project_id

        c = self.controller
        prior_host_peer = c.host_peer
        take_id = new_project_id()
        host_id = new_project_id()
        guest_id = new_project_id()
        fingerprint = "ab" * 32
        guest_logical_ids = (new_project_id(), new_project_id())
        obligation = SimpleNamespace(
            participant_id=guest_id,
            track_count=2,
            map_fingerprint=fingerprint,
            presence_generation=9,
            channel_counts=(1, 2),
            logical_source_ids=guest_logical_ids,
            exact_topology=True,
        )
        peer = SimpleNamespace(
            active=True,
            prepare_local_original_obligations=lambda _take_id: (
                (obligation,),
                (),
            ),
            local_original_obligations_for_take=lambda _take_id: (obligation,),
            discard_prepared_local_original_obligations=lambda _take_id: True,
        )
        c.host_peer = peer
        try:
            bindings, issues = c.recording._prepare_guest_local_original_bindings(
                take_id
            )
            self.assertEqual(issues, ())
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0].participant_id, guest_id)
            self.assertEqual(bindings[0].track_count, 2)

            plan = SessionRecordingPlan(
                session_id=new_project_id(),
                take_id=take_id,
                plan_generation=5,
                roster=((host_id, "Host"), (guest_id, "Guest")),
                expected_server_stems=(host_id, guest_id),
                count_in_frames=0,
                pre_roll_frames=0,
                storage=RecordingStorageCheck(
                    status=RecordingStorageStatus.READY,
                    detail="Recording storage is ready.",
                    free_bytes=10_000_000,
                    required_bytes=1_000_000,
                ),
                expected_source_count=5,
                created_at_utc="2026-08-15T12:00:00Z",
                input_maps=(InputMapBinding("Host Mic", 1, True, True),),
                guest_local_originals=bindings,
                creator_profile_key="podcast_voice",
                server_channel_counts=(1, 1),
            )
            receipts = (
                RecorderClientReceipt(1, "Host", host_id, "11" * 32, 1),
                RecorderClientReceipt(2, "Guest", guest_id, "22" * 32, 1),
            )
            with c.recording._evidence_lock:
                c.recording._recording_plan = plan
                c.recording._recording_plan_take_id = take_id
                c.recording._recording_plan_fingerprint = plan.plan_fingerprint()
            self.assertEqual(
                c.recording._recording_plan_validation_errors(
                    take_id,
                    receipts,
                    required_local_count=1,
                ),
                (),
            )

            changed = SimpleNamespace(
                participant_id=guest_id,
                track_count=1,
                map_fingerprint="cd" * 32,
                presence_generation=10,
                channel_counts=(1,),
                logical_source_ids=(guest_logical_ids[0],),
                exact_topology=True,
            )
            peer.local_original_obligations_for_take = lambda _take_id: (changed,)
            self.assertTrue(
                any(
                    "guest Local Original obligations" in error
                    for error in c.recording._recording_plan_validation_errors(
                        take_id,
                        receipts,
                        required_local_count=1,
                    )
                )
            )
        finally:
            c.recording._retire_active_take(take_id)
            c.host_peer = prior_host_peer

    def test_guest_capture_arm_acknowledges_before_server_start_path(self):
        c = self.controller
        prior_host_peer = c.host_peer
        plan, peer = self._install_guest_arm_plan()
        hosted_readiness = SimpleNamespace()
        try:
            with (
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch.object(
                    c.recording,
                    "_readiness_authority_still_matches",
                    return_value=True,
                ) as authority,
                patch.object(
                    c.recording, "_continue_recording_start"
                ) as continue_start,
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread"
                ) as worker,
            ):
                self.assertTrue(
                    c.recording._arm_guest_capture_before_server_start(
                        plan,
                        hosted_readiness,
                    )
                )
                continue_start.assert_not_called()
                self.assertEqual(worker.call_count, 1)
                arm_thread = worker.call_args
                self.assertEqual(arm_thread.kwargs["name"], "guest-capture-arm")
                arm_thread.kwargs["target"](*arm_thread.kwargs["args"])

            peer.publish_capture_arm.assert_called_once_with(
                plan.take_id,
                recording_plan_fingerprint=plan.plan_fingerprint(),
            )
            peer.wait_for_capture_arm_acknowledgements.assert_called_once_with(
                plan.take_id,
                arm_generation=7,
                timeout_s=8.0,
            )
            peer.capture_arm_ready.assert_called_once_with(
                plan.take_id,
                arm_generation=7,
            )
            authority.assert_called_once_with(
                plan,
                hosted_readiness=hosted_readiness,
                planned_shared_track=False,
            )
            continue_start.assert_called_once_with(plan)
        finally:
            c.recording._retire_active_take(plan.take_id)
            c.host_peer = prior_host_peer

    def test_guest_capture_arm_timeout_retires_take_without_server_start(self):
        c = self.controller
        prior_host_peer = c.host_peer
        plan, peer = self._install_guest_arm_plan()
        peer.wait_for_capture_arm_acknowledgements.return_value = False
        try:
            with (
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch.object(
                    c.recording, "_continue_recording_start"
                ) as continue_start,
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
            ):
                self.assertTrue(
                    c.recording._arm_guest_capture_before_server_start(
                        plan,
                        SimpleNamespace(),
                    )
                )

            continue_start.assert_not_called()
            self.assertEqual(c.recording.phase.value, "error")
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(c.recording._guest_capture_arm_take_id, "")
            self.assertEqual(c.recording._guest_capture_arm_generation, 0)
            self.assertTrue(
                any(
                    call.args == (plan.take_id,)
                    and call.kwargs.get("arm_generation") == 7
                    for call in peer.cancel_capture_arm.call_args_list
                )
            )
            self.assertEqual(
                c._show_actionable_error.call_args.args[0],
                "Guest Inputs Did Not Arm",
            )
        finally:
            c.host_peer = prior_host_peer

    def test_guest_capture_arm_rechecks_authority_after_ack(self):
        c = self.controller
        prior_host_peer = c.host_peer
        plan, peer = self._install_guest_arm_plan()
        try:
            with (
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch.object(
                    c.recording,
                    "_readiness_authority_still_matches",
                    return_value=False,
                ) as authority,
                patch.object(
                    c.recording, "_continue_recording_start"
                ) as continue_start,
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
            ):
                self.assertTrue(
                    c.recording._arm_guest_capture_before_server_start(
                        plan,
                        SimpleNamespace(),
                    )
                )

            authority.assert_called_once()
            continue_start.assert_not_called()
            self.assertEqual(c.recording.phase.value, "error")
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(
                c._show_actionable_error.call_args.args[0],
                "Recording Readiness Changed",
            )
        finally:
            c.host_peer = prior_host_peer

    def test_stale_guest_capture_arm_callback_cannot_resurrect_new_take(self):
        from core.take_project import new_project_id

        c = self.controller
        prior_host_peer = c.host_peer
        plan, peer = self._install_guest_arm_plan()
        try:
            with (
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch.object(
                    c.recording, "_continue_recording_start"
                ) as continue_start,
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread"
                ) as worker,
            ):
                self.assertTrue(
                    c.recording._arm_guest_capture_before_server_start(
                        plan,
                        SimpleNamespace(),
                    )
                )
                newer_take_id = new_project_id()
                c.recording._take_id = newer_take_id
                arm_thread = worker.call_args
                arm_thread.kwargs["target"](*arm_thread.kwargs["args"])

            continue_start.assert_not_called()
            self.assertEqual(c.recording._take_id, newer_take_id)
            peer.cancel_capture_arm.assert_any_call(
                plan.take_id,
                arm_generation=7,
            )
        finally:
            c.recording._take_id = plan.take_id
            c.recording._retire_active_take(plan.take_id)
            c.host_peer = prior_host_peer

    def test_live_source_projection_uses_one_exact_plan_for_every_route(self):
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.recording_sources import (
            RecordingSourceKind,
            RecordingSourceState,
        )
        from core.session_recording_plan import (
            GuestLocalOriginalBinding,
            InputMapBinding,
            SessionRecordingPlan,
            SharedTrackBinding,
        )
        from core.take_library import RecorderClientReceipt
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        host_id = new_project_id()
        guest_id = new_project_id()
        reference_id = c.recording._reference_participant_id
        guest_source_ids = (new_project_id(), new_project_id())
        source_fingerprint = "ab" * 32
        plan = SessionRecordingPlan(
            session_id=c.recording._session_id,
            take_id=take_id,
            plan_generation=7,
            roster=((host_id, "Host"), (guest_id, "Guest")),
            expected_server_stems=(host_id, guest_id, reference_id),
            count_in_frames=48_000,
            pre_roll_frames=48_000,
            storage=RecordingStorageCheck(
                status=RecordingStorageStatus.READY,
                detail="Recording storage is ready.",
                free_bytes=10_000_000,
                required_bytes=1_000_000,
            ),
            expected_source_count=6,
            created_at_utc="2026-08-16T12:00:00Z",
            shared_track=SharedTrackBinding(source_fingerprint, 4),
            shared_track_planned=True,
            input_maps=(InputMapBinding("Host Mic", 2, True, True),),
            guest_local_originals=(
                GuestLocalOriginalBinding(
                    participant_id=guest_id,
                    track_count=2,
                    map_fingerprint_sha256="cd" * 32,
                    presence_generation=9,
                    channel_counts=(1, 2),
                    logical_source_ids=guest_source_ids,
                ),
            ),
            creator_profile_key="podcast_voice",
            server_channel_counts=(1, 2, 2),
        )
        receipts = (
            RecorderClientReceipt(10, "Host", host_id, "11" * 32, 1),
            RecorderClientReceipt(11, "Guest", guest_id, "22" * 32, 2),
            RecorderClientReceipt(
                12,
                "Shared Track",
                reference_id,
                "33" * 32,
                2,
                "reference_track",
                source_fingerprint,
                4,
            ),
        )
        c.recording._take_id = take_id
        c.recording._local_participant_id = host_id
        c.recording._participant_ids = {10: host_id, 11: guest_id}
        c.recording._track_names = {10: "Host", 11: "Guest"}
        c.recording.phase = RecorderPhase.COUNT_IN
        with c.recording._evidence_lock:
            c.recording._recording_plan = plan
            c.recording._recording_plan_take_id = take_id
            c.recording._recording_plan_fingerprint = plan.plan_fingerprint()
        with c.recording._receipt_lock:
            c.recording._recording_receipts = {
                (receipt.recorder_key_sha256, index): receipt
                for index, receipt in enumerate(receipts)
            }
            c.recording._recording_conflicted_keys = set()
            c.recording._recording_receipts_frozen_take_id = ""
        try:
            rows = c.recording.recording_source_presentations()
            self.assertEqual(len(rows), 6)
            self.assertEqual(
                tuple(row.source_kind for row in rows),
                (
                    RecordingSourceKind.JAMULUS_SERVER,
                    RecordingSourceKind.JAMULUS_SERVER,
                    RecordingSourceKind.SHARED_TRACK,
                    RecordingSourceKind.LOCAL_ORIGINAL,
                    RecordingSourceKind.LOCAL_ORIGINAL,
                    RecordingSourceKind.LOCAL_ORIGINAL,
                ),
            )
            self.assertEqual(tuple(row.channels for row in rows), (1, 2, 2, 2, 1, 2))
            self.assertEqual(
                tuple(row.state for row in rows), (RecordingSourceState.RECORDING,) * 6
            )
            self.assertEqual(
                tuple(row.channel_id for row in rows), (10, 11, -1, -1, -1, -1)
            )
            self.assertEqual(
                tuple(row.logical_source_id for row in rows),
                (
                    *plan.server_logical_source_ids,
                    plan.resolved_capture_tracks()[0].logical_source_id,
                    *guest_source_ids,
                ),
            )
        finally:
            c.recording._retire_active_take(take_id)

    def test_application_publishes_or_clears_exact_studio_source_snapshot(self):
        from core.recording_sources import (
            RecordingSourceKind,
            RecordingSourcePresentation,
            RecordingSourceState,
        )
        from core.take_project import new_project_id

        c = self.controller
        participant_id = new_project_id()
        row = RecordingSourcePresentation(
            participant_id=participant_id,
            display_name="Host",
            kind="musician",
            state=RecordingSourceState.RECORDING,
            channels=1,
            logical_source_id=new_project_id(),
            source_kind=RecordingSourceKind.JAMULUS_SERVER,
            channel_id=7,
        )
        studio = c.window.recording_studio
        with (
            patch.object(
                c.recording,
                "recording_source_presentations",
                side_effect=((row,), ()),
            ),
            patch.object(studio, "set_recording_sources") as publish,
            patch.object(studio, "clear_recording_sources") as clear,
        ):
            c._apply_recording_states_to_participants()
            publish.assert_called_once_with((row,))
            clear.assert_not_called()

            c._apply_recording_states_to_participants()
            clear.assert_called_once_with()

    def test_plan_binding_checkpoints_exact_private_plan_before_recording(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )
        from core.take_project import new_project_id

        c = self.controller
        prior_root = c.settings.takes_directory
        prior_session_id = c.recording._session_id
        prior_reference = c._reference_track
        with TemporaryDirectory() as directory:
            take_id = new_project_id()
            musician_id = new_project_id()
            c.settings.takes_directory = directory
            c.recording._session_id = new_project_id()
            c.recording._take_id = take_id
            c.recording._begin_recording_diagnostics(take_id)
            c.recording._reset_session_evidence()
            c.recording._participant_ids = {4: musician_id}
            c.recording._track_names = {4: "Alice"}
            c._reference_track = SimpleNamespace(
                snapshot=SimpleNamespace(
                    state=SimpleNamespace(value="ready"),
                    playback_generation=5,
                    count_in_beats=4,
                    count_in_bpm=120.0,
                ),
                recording_source_fingerprint=lambda: "ab" * 32,
            )
            storage = RecordingStorageCheck(
                status=RecordingStorageStatus.READY,
                detail="Recording storage is ready.",
                free_bytes=10_000_000,
                required_bytes=1_000_000,
            )
            try:
                self.assertTrue(
                    c.recording._bind_session_recording_plan(
                        storage,
                        planned_shared_track=True,
                        server_channel_counts=(1, 2),
                    )
                )
                plan = c.recording._recording_plan
                self.assertIsNotNone(plan)
                self.assertEqual(plan.shared_track.playback_generation, 6)
                self.assertEqual(plan.count_in_frames, 96_000)
                self.assertEqual(plan.pre_roll_frames, 96_000)
                self.assertEqual(
                    set(plan.expected_server_stems),
                    {musician_id, c.recording._reference_participant_id},
                )
                self.assertTrue(c.recording._create_evidence_journal())
                loaded = RecordingManifestJournal(directory).load(take_id)
                self.assertTrue(loaded.trusted)
                self.assertEqual(loaded.plan, plan)
            finally:
                c.recording._retire_active_take(take_id)
                c.settings.takes_directory = prior_root
                c.recording._session_id = prior_session_id
                c._reference_track = prior_reference

    def test_peer_finalizing_signal_uses_the_public_host_peer_api(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        begin_finalization = MagicMock()
        prior_host_peer = c.host_peer
        c.host_peer = SimpleNamespace(
            active=True,
            begin_take_finalization=begin_finalization,
        )
        try:
            c.recording._signal_peer_recording_finalizing(
                take_id,
                stopped_utc="2026-08-03T12:00:00Z",
                message="  The host is finalizing the take.  ",
            )
        finally:
            c.host_peer = prior_host_peer

        begin_finalization.assert_called_once_with(
            take_id,
            stopped_utc="2026-08-03T12:00:00Z",
            message="The host is finalizing the take.",
        )

    def test_confirmed_stop_finalizes_the_exact_acknowledged_ambiguous_arm(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        begin_finalization = MagicMock(side_effect=RuntimeError("not recording"))
        begin_armed_finalization = MagicMock(return_value=object())
        prior_host_peer = c.host_peer
        c.host_peer = SimpleNamespace(
            active=True,
            begin_take_finalization=begin_finalization,
            begin_armed_take_finalization=begin_armed_finalization,
        )
        with c.recording._evidence_lock:
            c.recording._guest_capture_arm_take_id = take_id
            c.recording._guest_capture_arm_generation = 11
        try:
            c.recording._signal_peer_recording_finalizing(
                take_id,
                stopped_utc="2026-08-03T12:00:00Z",
                message="  The host is finalizing the take.  ",
            )
        finally:
            with c.recording._evidence_lock:
                c.recording._guest_capture_arm_take_id = ""
                c.recording._guest_capture_arm_generation = 0
            c.host_peer = prior_host_peer

        begin_finalization.assert_called_once()
        begin_armed_finalization.assert_called_once_with(
            take_id,
            arm_generation=11,
            stopped_utc="2026-08-03T12:00:00Z",
            message="The host is finalizing the take.",
        )

    def test_unconfigured_shows_setup_instructions(self):
        c = self.controller
        c._on_record_requested()
        c._show_actionable_error.assert_called_once()
        self.assertEqual(
            c._show_actionable_error.call_args.args[0],
            "Recording Is Available On The Host",
        )
        self.assertNotIn(
            "RPC",
            c._show_actionable_error.call_args.kwargs["next_action"],
        )
        kwargs = c._show_actionable_error.call_args.kwargs
        self.assertIn("host", kwargs["next_action"].lower())
        self.assertIn("Studio", kwargs["next_action"])

    def test_authenticated_receipt_binds_reference_only_to_stable_owned_claim(self):
        from core.reference_track import ReferenceTrackOwnershipClaim
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        musician_id = new_project_id()
        c.participants = {
            4: SimpleNamespace(
                channel_id=4,
                name="Alice",
                role="Guitar",
                participant_id=musician_id,
            )
        }
        source_fingerprint = "ab" * 32
        first_claim = ReferenceTrackOwnershipClaim(
            udp_port=51042,
            process_id=1234,
            generation="1" * 32,
            source_fingerprint_sha256=source_fingerprint,
        )
        claim_holder = [first_claim]
        old_reference = getattr(c, "_reference_track", None)
        c._reference_track = SimpleNamespace(
            recording_ownership_claim=lambda: claim_holder[0]
        )
        payload = {
            "connections": 2,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    # A row at the configured allocation base is still the
                    # musician; only the exact PID-owned offset can be the
                    # Reference Track.
                    "address": "127.0.0.1:51000",
                    "channels": 1,
                },
                {
                    "id": 9,
                    "name": "A musician may copy this name",
                    "address": "127.0.0.1:51042",
                    "channels": 1,
                },
            ],
        }
        try:
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                side_effect=lambda channel_id: musician_id if channel_id == 4 else "",
            ):
                context = c.recording._roster_observation_context()
                rendered_context = repr(context)
                self.assertNotIn("Alice", rendered_context)
                self.assertNotIn("51042", rendered_context)
                self.assertNotIn(musician_id, rendered_context)
                c.recording._consume_authenticated_roster(payload, context)
            receipts, errors = c.recording._recording_receipt_snapshot()

            self.assertEqual(errors, ())
            self.assertEqual(len(receipts), 2)
            by_kind = {item.source_kind: item for item in receipts}
            self.assertEqual(by_kind["musician"].participant_id, musician_id)
            self.assertEqual(by_kind["reference_track"].display_name, "WebJam Track")
            self.assertEqual(
                by_kind["reference_track"].source_fingerprint_sha256,
                source_fingerprint,
            )
            self.assertNotIn("51042", repr(receipts))

            # Replacing the exact process/generation between context and
            # receipt invalidates the special binding even though the port
            # and display row are otherwise unchanged.
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                side_effect=lambda channel_id: musician_id if channel_id == 4 else "",
            ):
                stale_context = c.recording._roster_observation_context()
                claim_holder[0] = ReferenceTrackOwnershipClaim(
                    udp_port=51042,
                    process_id=5678,
                    generation="2" * 32,
                    source_fingerprint_sha256=source_fingerprint,
                )
                c.recording._consume_authenticated_roster(payload, stale_context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].source_kind, "musician")
            self.assertTrue(any("conflicted" in item for item in errors))
        finally:
            c._reference_track = old_reference
            c.recording._take_id = ""
            c.participants = {}

    def test_unbound_presentation_id_never_becomes_recording_identity(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        c.participants = {
            4: SimpleNamespace(
                channel_id=4,
                name="Alice",
                role="Guitar",
                # Deliberately stale/generated UI metadata, not a peer binding.
                participant_id=new_project_id(),
            )
        }
        payload = {
            "connections": 1,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    "address": "127.0.0.1:50000",
                    "channels": 1,
                }
            ],
        }
        try:
            context = c.recording._roster_observation_context()
            c.recording._consume_authenticated_roster(payload, context)
            receipts, errors = c.recording._recording_receipt_snapshot()

            self.assertEqual(receipts, ())
            self.assertTrue(any("could not prove" in item for item in errors))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_same_name_channel_reuse_between_roster_snapshots_is_unproven(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        c.participants = {4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")}
        old_participant = new_project_id()
        replacement = new_project_id()
        payload = {
            "connections": 1,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    "address": "127.0.0.1:50000",
                    "channels": 1,
                }
            ],
        }
        try:
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                side_effect=[old_participant, replacement],
            ):
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, context)
            receipts, errors = c.recording._recording_receipt_snapshot()

            self.assertEqual(receipts, ())
            self.assertTrue(any("could not prove" in item for item in errors))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_legacy_client_local_presence_is_never_recorder_authority(self):
        from core.session_transfer import EnrollmentRegistry, SessionCredentials
        from core.take_project import new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            registry = EnrollmentRegistry(directory, SessionCredentials.create())
            first = registry.enroll(
                new_project_id(),
                "Alice",
                invite_token=registry.credentials.invite_token,
            )
            second = registry.enroll(
                new_project_id(),
                "Alice",
                invite_token=registry.credentials.invite_token,
            )
            registry.bind_presence(
                first.participant_id,
                4,
                "Alice",
                generation=10,
            )
            host_peer = SimpleNamespace(
                active=True,
                presence_for_channel=registry.presence_for_channel,
            )
            old_host_peer = c.host_peer
            c.host_peer = host_peer
            c.participants = {
                4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")
            }
            payload = {
                "connections": 1,
                "clients": [
                    {
                        "id": 4,
                        "name": "Alice",
                        "address": "127.0.0.1:50000",
                        "channels": 1,
                    }
                ],
            }
            try:
                c.recording._take_id = new_project_id()
                c.recording._reset_session_evidence()
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, context)
                receipts, errors = c.recording._recording_receipt_snapshot()
                self.assertEqual(receipts, ())
                self.assertEqual(errors, ())
                self.assertTrue(c.recording._recording_presence_retry_pending)

                registry.reconcile_presence_channels(())
                stale_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, stale_context)
                receipts, errors = c.recording._recording_receipt_snapshot()
                self.assertEqual(receipts, ())
                self.assertEqual(errors, ())
                self.assertTrue(c.recording._recording_presence_retry_pending)

                # A fresh take can prove the replacement only after that
                # enrolled participant publishes a newer signed generation.
                registry.bind_presence(
                    second.participant_id,
                    4,
                    "Alice",
                    generation=1,
                )
                c.recording._take_id = new_project_id()
                c.recording._reset_session_evidence()
                fresh_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, fresh_context)
                receipts, errors = c.recording._recording_receipt_snapshot()
                self.assertEqual(receipts, ())
                self.assertEqual(errors, ())
                self.assertTrue(c.recording._recording_presence_retry_pending)
            finally:
                c.recording._take_id = ""
                c.participants = {}
                c.host_peer = old_host_peer

    def test_v2_ordered_presence_maps_sparse_server_ids_by_ordinal(self):
        from core.jamulus_roster_identity import (
            JamulusCommonProfile,
            ordered_client_local_roster_fingerprint,
            ordered_common_roster_digest,
        )
        from core.jamulus_rpc_client import (
            JamulusOrderedRosterProof,
            JamulusOrderedRosterRow,
            JamulusRpcMonitorIdentity,
        )
        from core.take_project import new_project_id

        c = self.controller
        identity = JamulusRpcMonitorIdentity(8, 21, 3456)
        profiles = (
            JamulusCommonProfile("Host", 3, "Chicago", 2),
            JamulusCommonProfile("Alex", 5, "Chicago", 2),
            JamulusCommonProfile("Alex", 6, "Chicago", 2),
        )
        roster = JamulusOrderedRosterProof(
            identity=identity,
            rpc_connection_generation=2,
            audio_connection_generation=4,
            roster_revision=7,
            observed_at=123.0,
            rows=tuple(
                JamulusOrderedRosterRow(index, local_id, profiles[index])
                for index, local_id in enumerate((1, 0, 2))
            ),
            own_ordinal=1,
            common_digest=ordered_common_roster_digest(profiles),
            host_roster_fingerprint=ordered_client_local_roster_fingerprint(
                (1, 0, 2),
                own_ordinal=1,
            ),
        )
        participant_ids = tuple(new_project_id() for _ in profiles)
        proofs = tuple(
            SimpleNamespace(
                participant_id=participant_ids[index],
                display_name=profile.name,
                ordered_roster_digest=roster.common_digest,
                roster_count=roster.roster_size,
                self_ordinal=index,
                process_generation=(21 if index == 1 else index + 1),
                rpc_connection_generation=(2 if index == 1 else 1),
                audio_connection_generation=(4 if index == 1 else 1),
                topology_epoch=1,
                challenge="old-lease",
                challenge_epoch=1,
                presence_generation=index + 1,
                capture_enabled=True,
                recorder_eligible=True,
            )
            for index, profile in enumerate(profiles)
        )
        proof_holder = [proofs]
        host_peer = SimpleNamespace(
            active=True,
            host_enrollment=SimpleNamespace(participant_id=participant_ids[1]),
            recording_presence_snapshot=lambda **_kwargs: proof_holder[0],
        )
        payload = {
            "connections": 3,
            "clients": [
                {
                    "id": server_id,
                    "name": profile.name,
                    "instrumentCode": profile.instrument_code,
                    "city": profile.city,
                    "skillLevelCode": profile.skill_level_code,
                    "address": f"127.0.0.1:{50_000 + index}",
                    "channels": 1,
                }
                for index, (server_id, profile) in enumerate(zip((0, 4, 11), profiles))
            ],
        }
        old_host_peer = c.host_peer
        old_proof = getattr(c, "_primary_ordered_roster_proof", None)
        c.host_peer = host_peer
        c._primary_ordered_roster_proof = roster
        try:
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(errors, ())
            self.assertEqual(
                {item.server_channel_id: item.participant_id for item in receipts},
                dict(zip((0, 4, 11), participant_ids)),
            )

            # Join/reconnect timing may expose the native row before that
            # enrolled peer has renewed Presence v2. The provisional snapshot
            # keeps all prior receipts intact and a later complete retry clears
            # the pending state without creating conflicts.
            proof_holder[0] = proofs[:2]
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                provisional_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, provisional_context)
            provisional_receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(provisional_receipts, receipts)
            self.assertEqual(errors, ())
            self.assertTrue(c.recording._recording_presence_retry_pending)
            proof_holder[0] = proofs
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                retry_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, retry_context)
            self.assertFalse(c.recording._recording_presence_retry_pending)
            self.assertEqual(c.recording._recording_receipt_snapshot()[0], receipts)

            # The same enrolled guest reconnects with a new client/audio
            # lifecycle and recorder UDP key. The host-private layout rotates
            # topology, so both media segments retain the same durable owner.
            reconnect_roster = JamulusOrderedRosterProof(
                identity=identity,
                rpc_connection_generation=2,
                audio_connection_generation=4,
                roster_revision=8,
                observed_at=130.0,
                rows=tuple(
                    JamulusOrderedRosterRow(index, local_id, profiles[index])
                    for index, local_id in enumerate((2, 0, 1))
                ),
                own_ordinal=1,
                common_digest=roster.common_digest,
                host_roster_fingerprint=(
                    ordered_client_local_roster_fingerprint((2, 0, 1), own_ordinal=1)
                ),
            )
            reconnect_claims = []
            for index, old_claim in enumerate(proofs):
                claim = SimpleNamespace(**vars(old_claim))
                claim.topology_epoch = 2
                claim.challenge = "reconnect-topology"
                claim.challenge_epoch = 2
                claim.presence_generation += 10
                if index == 2:
                    claim.audio_connection_generation += 1
                reconnect_claims.append(claim)
            reconnect_payload = {
                "connections": 3,
                "clients": [dict(row) for row in payload["clients"]],
            }
            reconnect_payload["clients"][2]["address"] = "127.0.0.1:51002"
            c._primary_ordered_roster_proof = reconnect_roster
            proof_holder[0] = tuple(reconnect_claims)
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=reconnect_roster,
            ):
                reconnect_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(
                    reconnect_payload, reconnect_context
                )
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(errors, ())
            self.assertEqual(len(receipts), 4)
            self.assertEqual(
                sum(item.participant_id == participant_ids[2] for item in receipts),
                2,
            )
            c._primary_ordered_roster_proof = roster
            proof_holder[0] = proofs

            # An atomic lease rollover changes challenge/presence generation,
            # not semantic owner, ordinal, or native lifecycle generations.
            # A server RPC spanning that promotion remains attributable.
            promoted = []
            for old_claim in proofs:
                claim = SimpleNamespace(**vars(old_claim))
                claim.challenge = "new-lease"
                claim.challenge_epoch = 2
                claim.presence_generation += 100
                promoted.append(claim)
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            proof_holder[0] = proofs
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                rollover_context = c.recording._roster_observation_context()
                proof_holder[0] = tuple(promoted)
                c.recording._consume_authenticated_roster(payload, rollover_context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(errors, ())
            self.assertEqual(len(receipts), 3)
            proof_holder[0] = proofs

            # A periodic identical client-list refresh updates freshness only.
            # It may overtake an in-flight server RPC without becoming a
            # topology transition or invalidating the take.
            from dataclasses import replace

            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                refresh_context = c.recording._roster_observation_context()
            refreshed_roster = replace(roster, observed_at=789.0)
            c._primary_ordered_roster_proof = refreshed_roster
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=refreshed_roster,
            ):
                c.recording._consume_authenticated_roster(payload, refresh_context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(errors, ())
            self.assertEqual(len(receipts), 3)
            c._primary_ordered_roster_proof = roster

            # A guest/common-profile match cannot stand in for the exact host
            # process at the host's self ordinal.
            stale_host = SimpleNamespace(**vars(proofs[1]))
            stale_host.process_generation = 20
            proof_holder[0] = (proofs[0], stale_host, proofs[2])
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                stale_host_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, stale_host_context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertEqual(errors, ())
            self.assertTrue(c.recording._recording_presence_retry_pending)
            proof_holder[0] = proofs

            # Even an identical roster digest cannot authorize across a newer
            # exact observation while the server RPC is in flight.
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                stale_context = c.recording._roster_observation_context()
            newer = JamulusOrderedRosterProof(
                identity=identity,
                rpc_connection_generation=2,
                audio_connection_generation=4,
                roster_revision=8,
                observed_at=124.0,
                rows=roster.rows,
                own_ordinal=roster.own_ordinal,
                common_digest=roster.common_digest,
                host_roster_fingerprint=roster.host_roster_fingerprint,
            )
            c._primary_ordered_roster_proof = newer
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=newer,
            ):
                c.recording._consume_authenticated_roster(payload, stale_context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertEqual(errors, ())
            self.assertTrue(c.recording._recording_presence_retry_pending)
        finally:
            c.recording._take_id = ""
            c._primary_ordered_roster_proof = old_proof
            c.host_peer = old_host_peer

    def test_v2_reference_restart_keeps_both_proven_segments(self):
        from core.jamulus_roster_identity import (
            JamulusCommonProfile,
            ordered_client_local_roster_fingerprint,
            ordered_common_roster_digest,
        )
        from core.jamulus_rpc_client import (
            JamulusOrderedRosterProof,
            JamulusOrderedRosterRow,
            JamulusRpcMonitorIdentity,
        )
        from core.reference_track import ReferenceTrackOwnershipClaim
        from core.take_project import new_project_id

        c = self.controller
        identity = JamulusRpcMonitorIdentity(9, 31, 4567)
        profiles = (
            JamulusCommonProfile("Host", 3, "Chicago", 2),
            JamulusCommonProfile("WebJam Track", 0, "", 0),
        )
        roster = JamulusOrderedRosterProof(
            identity=identity,
            rpc_connection_generation=2,
            audio_connection_generation=4,
            roster_revision=7,
            observed_at=123.0,
            rows=tuple(
                JamulusOrderedRosterRow(index, index, profile)
                for index, profile in enumerate(profiles)
            ),
            own_ordinal=0,
            common_digest=ordered_common_roster_digest(profiles),
            host_roster_fingerprint=ordered_client_local_roster_fingerprint(
                (0, 1), own_ordinal=0
            ),
        )
        host_participant_id = new_project_id()
        host_presence = SimpleNamespace(
            participant_id=host_participant_id,
            display_name="Host",
            ordered_roster_digest=roster.common_digest,
            roster_count=roster.roster_size,
            self_ordinal=0,
            process_generation=identity.process_generation,
            rpc_connection_generation=roster.rpc_connection_generation,
            audio_connection_generation=roster.audio_connection_generation,
            topology_epoch=1,
            challenge="lease",
            challenge_epoch=1,
            presence_generation=1,
            capture_enabled=True,
            recorder_eligible=True,
        )
        host_peer = SimpleNamespace(
            active=True,
            host_enrollment=SimpleNamespace(participant_id=host_participant_id),
            recording_presence_snapshot=lambda **_kwargs: (host_presence,),
        )
        claims = [
            ReferenceTrackOwnershipClaim(
                udp_port=51042,
                process_id=1234,
                generation="1" * 32,
            )
        ]

        def payload(port: int) -> dict[str, object]:
            return {
                "connections": 2,
                "clients": [
                    {
                        "id": 0,
                        "name": "Host",
                        "instrumentCode": 3,
                        "city": "Chicago",
                        "skillLevelCode": 2,
                        "address": "127.0.0.1:50000",
                        "channels": 1,
                    },
                    {
                        "id": 7,
                        "name": "WebJam Track",
                        "instrumentCode": 0,
                        "city": "",
                        "skillLevelCode": 0,
                        "address": f"127.0.0.1:{port}",
                        "channels": 1,
                    },
                ],
            }

        old_host_peer = c.host_peer
        old_proof = getattr(c, "_primary_ordered_roster_proof", None)
        old_reference = getattr(c, "_reference_track", None)
        c.host_peer = host_peer
        c._primary_ordered_roster_proof = roster
        c._reference_track = SimpleNamespace(
            recording_ownership_claim=lambda: claims[0]
        )
        try:
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=roster,
            ):
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload(51042), context)
                claims[0] = ReferenceTrackOwnershipClaim(
                    udp_port=51043,
                    process_id=5678,
                    generation="2" * 32,
                )
                restart_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(
                    payload(51043), restart_context
                )
            receipts, errors = c.recording._recording_receipt_snapshot()

            self.assertEqual(errors, ())
            reference_receipts = tuple(
                receipt
                for receipt in receipts
                if receipt.source_kind == "reference_track"
            )
            self.assertEqual(len(receipts), 3)
            self.assertEqual(len(reference_receipts), 2)
            self.assertEqual(
                {receipt.participant_id for receipt in reference_receipts},
                {c.recording._reference_participant_id},
            )
            self.assertNotIn("51042", repr(receipts))
            self.assertNotIn("51043", repr(receipts))
            self.assertNotIn("5678", repr(receipts))
        finally:
            c.recording._take_id = ""
            c._reference_track = old_reference
            c._primary_ordered_roster_proof = old_proof
            c.host_peer = old_host_peer

    def test_exact_owned_reference_can_join_after_recording_started(self):
        from core.jamulus_roster_identity import JamulusCommonProfile
        from core.reference_track import ReferenceTrackOwnershipClaim
        from core.take_project import new_project_id

        c = self.controller
        host_profile = JamulusCommonProfile("Host", 3, "Chicago", 2)
        initial = _hosted_readiness_fixture((host_profile,))
        joined = _hosted_readiness_fixture(
            (
                host_profile,
                JamulusCommonProfile("WebJam Track", 0, "", 0),
            )
        )
        joined.claims[0].participant_id = initial.participant_ids[0]
        joined.host_peer.host_enrollment.participant_id = initial.participant_ids[0]
        joined.claims_holder[0] = (joined.claims[0],)
        joined.payload["clients"][1]["address"] = "127.0.0.1:51042"
        claim = ReferenceTrackOwnershipClaim(
            udp_port=51042,
            process_id=2468,
            generation="a" * 32,
        )
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        old_reference = getattr(c, "_reference_track", None)
        try:
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            c.host_peer = initial.host_peer
            c._primary_ordered_roster_proof = initial.proof
            c.participants = initial.participants
            c._reference_track = None
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                side_effect=lambda _identity: c._primary_ordered_roster_proof,
            ):
                first_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(
                    initial.payload, first_context
                )
                self.assertEqual(len(c.recording._recording_receipt_snapshot()[0]), 1)

                c.host_peer = joined.host_peer
                c._primary_ordered_roster_proof = joined.proof
                c.participants = joined.participants
                c._reference_track = SimpleNamespace(
                    recording_ownership_claim=lambda: claim
                )
                joined_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(
                    joined.payload, joined_context
                )

            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(errors, ())
            self.assertFalse(c.recording._recording_presence_retry_pending)
            self.assertEqual(len(receipts), 2)
            self.assertEqual(
                {receipt.source_kind for receipt in receipts},
                {"musician", "reference_track"},
            )
            self.assertNotIn("51042", repr(receipts))
            self.assertNotIn("2468", repr(receipts))
        finally:
            c.recording._take_id = ""
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c._reference_track = old_reference
            c.participants = {}

    def test_recorder_digest_change_on_same_channel_conflicts_both_sources(self):
        from core.take_project import new_project_id

        c = self.controller
        participant_id = new_project_id()
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        c.participants = {4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")}

        def payload(port: int) -> dict:
            return {
                "connections": 1,
                "clients": [
                    {
                        "id": 4,
                        "name": "Alice",
                        "address": f"127.0.0.1:{port}",
                        "channels": 1,
                    }
                ],
            }

        try:
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=participant_id,
            ):
                for port in (50000, 50001):
                    context = c.recording._roster_observation_context()
                    c.recording._consume_authenticated_roster(payload(port), context)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertEqual(len(c.recording._recording_conflicted_keys), 2)
            self.assertTrue(any("conflicted" in item for item in errors))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_recorder_digest_moving_channels_is_permanently_unproven(self):
        from core.take_project import new_project_id

        c = self.controller
        participant_id = new_project_id()
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()

        def observe(channel_id: int) -> None:
            c.participants = {
                channel_id: SimpleNamespace(
                    channel_id=channel_id, name="Alice", role="Guitar"
                )
            }
            payload = {
                "connections": 1,
                "clients": [
                    {
                        "id": channel_id,
                        "name": "Alice",
                        "address": "127.0.0.1:50000",
                        "channels": 1,
                    }
                ],
            }
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=participant_id,
            ):
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, context)

        try:
            observe(4)
            observe(7)
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertEqual(len(c.recording._recording_conflicted_keys), 1)
            self.assertTrue(any("conflicted" in item for item in errors))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_unproven_recorder_key_cannot_be_promoted_by_later_binding(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        c.participants = {4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")}
        payload = {
            "connections": 1,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    "address": "127.0.0.1:50000",
                    "channels": 1,
                }
            ],
        }
        try:
            unbound_context = c.recording._roster_observation_context()
            c.recording._consume_authenticated_roster(payload, unbound_context)
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=new_project_id(),
            ):
                proved_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, proved_context)
            receipts, errors = c.recording._recording_receipt_snapshot()

            self.assertEqual(receipts, ())
            self.assertTrue(any("conflicted" in item for item in errors))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_mono_stereo_change_keeps_two_receipts_for_one_durable_owner(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        participant_id = new_project_id()
        c.participants = {4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")}

        def payload(channels):
            return {
                "connections": 1,
                "clients": [
                    {
                        "id": 4,
                        "name": "Alice",
                        "address": "127.0.0.1:50000",
                        "channels": channels,
                    }
                ],
            }

        try:
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=participant_id,
            ):
                for channels in (1, 2):
                    context = c.recording._roster_observation_context()
                    c.recording._consume_authenticated_roster(
                        payload(channels), context
                    )
            receipts, errors = c.recording._recording_receipt_snapshot()

            self.assertEqual(errors, ())
            self.assertEqual({item.channels for item in receipts}, {1, 2})
            self.assertEqual(
                {item.participant_id for item in receipts}, {participant_id}
            )
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_final_roster_snapshot_is_authenticated_and_rejects_late_results(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        participant_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._reset_session_evidence()
        c.participants = {4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")}
        payload = {
            "connections": 1,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    "address": "127.0.0.1:50000",
                    "channels": 1,
                }
            ],
        }
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.return_value = payload
        try:
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=participant_id,
            ):
                initial_context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(
                    payload,
                    initial_context,
                )
            with (
                patch.object(
                    c,
                    "peer_participant_id_for_channel",
                    return_value=participant_id,
                ),
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
            ):
                receipts, errors = c.recording._final_recording_receipt_snapshot()

            self.assertEqual(errors, ())
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].participant_id, participant_id)
            fake_rpc.get_clients.assert_called_once()

            # A stale worker result arriving after the publication barrier is
            # ignored even if channel/name now point to another participant.
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=new_project_id(),
            ):
                c.recording._consume_authenticated_roster(
                    payload,
                    c.recording._roster_observation_context(),
                )
            frozen_receipts, frozen_errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(frozen_receipts, receipts)
            self.assertEqual(frozen_errors, errors)
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_final_roster_failure_clears_prior_attributions(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        participant_id = new_project_id()
        c.participants = {4: SimpleNamespace(channel_id=4, name="Alice", role="Guitar")}
        payload = {
            "connections": 1,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    "address": "127.0.0.1:50000",
                    "channels": 1,
                }
            ],
        }
        try:
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=participant_id,
            ):
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(payload, context)
            with patch(
                "core.jamulus_server_rpc.read_secret_file",
                side_effect=OSError("/private/tmp/secret-file"),
            ):
                receipts, errors = c.recording._final_recording_receipt_snapshot()

            self.assertEqual(receipts, ())
            self.assertTrue(any("final authenticated" in item for item in errors))
            self.assertNotIn("private", repr(errors))
            self.assertNotIn("secret-file", repr(errors))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_final_freeze_fails_closed_when_presence_retry_never_completes(self):
        from core.jamulus_roster_identity import JamulusCommonProfile
        from core.take_project import new_project_id

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (
                JamulusCommonProfile("Host", 3, "Chicago", 2),
                JamulusCommonProfile("Guest", 5, "Austin", 2),
            )
        )
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.return_value = fixture.payload
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        try:
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.recording._take_id = new_project_id()
            c.recording._reset_session_evidence()
            fixture.claims_holder[0] = (fixture.claims[0],)
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=fixture.proof,
            ):
                context = c.recording._roster_observation_context()
                c.recording._consume_authenticated_roster(fixture.payload, context)
            self.assertTrue(c.recording._recording_presence_retry_pending)
            self.assertEqual(c.recording._recording_receipt_snapshot(), ((), ()))

            with (
                patch.object(
                    c.jamulus,
                    "ordered_roster_proof_for",
                    return_value=fixture.proof,
                ),
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
            ):
                receipts, errors = c.recording._final_recording_receipt_snapshot()

            self.assertEqual(receipts, ())
            self.assertTrue(
                any(
                    "complete current WebJam musician roster" in error
                    for error in errors
                )
            )
            self.assertEqual(
                c.recording._recording_receipts_frozen_take_id,
                c.recording._take_id,
            )
        finally:
            c.recording._take_id = ""
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c.participants = {}

    def test_final_roster_drain_timeout_clears_and_freezes_receipts(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        with c.recording._receipt_lock:
            c.recording._roster_poll_inflight = True
        try:
            with patch(
                "webjam_qt.controllers.recording_coordinator."
                "_FINAL_RECEIPT_DRAIN_TIMEOUT_S",
                0.0,
            ):
                receipts, errors = c.recording._final_recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertTrue(any("in time" in item for item in errors))
            self.assertEqual(
                c.recording._recording_receipts_frozen_take_id,
                c.recording._take_id,
            )
        finally:
            with c.recording._receipt_condition:
                c.recording._roster_poll_inflight = False
                c.recording._receipt_condition.notify_all()
            c.recording._take_id = ""

    def test_overlapping_exact_roster_updates_fail_identity_closed(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        with c.recording._receipt_lock:
            c.recording._roster_poll_inflight = True
        try:
            c.recording.request_authenticated_roster_observation(
                exact_process_update=True
            )
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertTrue(any("every Jamulus roster" in item for item in errors))
        finally:
            with c.recording._receipt_condition:
                c.recording._roster_poll_pending = None
                c.recording._roster_poll_inflight = False
                c.recording._receipt_condition.notify_all()
            c.recording._take_id = ""

    def test_roster_request_defers_native_reference_socket_proof_off_ui(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        try:
            with (
                patch.object(
                    c.recording,
                    "_reference_recording_claim",
                ) as claim,
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread"
                ) as thread,
            ):
                c.recording.request_authenticated_roster_observation()

            claim.assert_not_called()
            thread.return_value.start.assert_called_once_with()
            pending = c.recording._roster_poll_pending
            self.assertIsNotNone(pending)
            self.assertIsNone(pending.reference_claim)
        finally:
            with c.recording._receipt_condition:
                c.recording._roster_poll_pending = None
                c.recording._roster_poll_inflight = False
                c.recording._receipt_condition.notify_all()
            c.recording._take_id = ""

    def test_post_stop_new_client_does_not_taint_or_gain_receipt(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        participant_id = new_project_id()
        c.participants = {5: SimpleNamespace(channel_id=5, name="Late", role="Bass")}
        payload = {
            "connections": 1,
            "clients": [
                {
                    "id": 5,
                    "name": "Late",
                    "address": "127.0.0.1:50001",
                    "channels": 1,
                }
            ],
        }
        try:
            # Unbound and then proved observations are both first seen only
            # after Stop; neither creates history nor a global error.
            c.recording._consume_authenticated_roster(
                payload,
                c.recording._roster_observation_context(),
                allow_new_receipts=False,
            )
            with patch.object(
                c,
                "peer_participant_id_for_channel",
                return_value=participant_id,
            ):
                c.recording._consume_authenticated_roster(
                    payload,
                    c.recording._roster_observation_context(),
                    allow_new_receipts=False,
                )
            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertEqual(errors, ())
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_stop_post_confirmation_roster_cannot_admit_new_digest(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._reset_session_evidence()
        participant_id = new_project_id()
        c.participants = {5: SimpleNamespace(channel_id=5, name="Late", role="Bass")}
        empty = {"connections": 0, "clients": []}
        late = {
            "connections": 1,
            "clients": [
                {
                    "id": 5,
                    "name": "Late",
                    "address": "127.0.0.1:50001",
                    "channels": 1,
                }
            ],
        }
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.side_effect = [empty, late]
        fake_rpc.stop_recording.return_value = True
        fake_rpc.get_recorder_status.return_value = {"enabled": False}
        try:
            with (
                patch.object(
                    c,
                    "peer_participant_id_for_channel",
                    return_value=participant_id,
                ),
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
                patch.object(c._ui_invoker, "invoke"),
            ):
                c._record_toggle_worker(False, "/tmp/secret")

            receipts, errors = c.recording._recording_receipt_snapshot()
            self.assertEqual(receipts, ())
            self.assertEqual(errors, ())
            self.assertEqual(fake_rpc.get_clients.call_count, 2)
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_configured_spawns_worker_toward_armed(self):
        from core.jamulus_roster_identity import JamulusCommonProfile
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (JamulusCommonProfile("Host", 3, "Chicago", 2),)
        )
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / "jsonrpc.secret"
            secret_path.write_text("s3cret\n", encoding="utf-8")
            c.settings.server_rpc_secret_file = str(secret_path)
            c._jamulus_connected = True
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            ready_storage = RecordingStorageCheck(
                RecordingStorageStatus.READY,
                "Recording storage is ready.",
                10_000_000,
                1_000_000,
            )
            try:
                with (
                    patch.object(
                        c.jamulus,
                        "ordered_roster_proof_for",
                        return_value=fixture.proof,
                    ),
                    patch(
                        "webjam_qt.controllers.recording_coordinator."
                        "_private_secret_file_identity",
                        return_value=(1, 2, 3, 4),
                    ),
                    patch(
                        "webjam_qt.controllers.recording_coordinator."
                        "check_recording_storage",
                        return_value=ready_storage,
                    ),
                    patch.object(c, "_record_toggle_worker") as worker,
                    patch.object(
                        c.recording, "request_authenticated_roster_observation"
                    ),
                    patch.object(
                        c.recording,
                        "_revalidate_hosted_recording_readiness",
                        return_value=True,
                    ),
                    patch.object(
                        c.recording, "_create_evidence_journal", return_value=True
                    ) as journal,
                    patch(
                        "webjam_qt.controllers.recording_coordinator.threading.Thread",
                        side_effect=lambda *a, **kw: _Immediate(*a, **kw),
                    ),
                ):
                    context = c.recording._hosted_recording_readiness_context(
                        list(fixture.participants.values())
                    )
                    self.assertIsNotNone(context)
                    readiness = c.recording._evaluate_hosted_recording_readiness(
                        fixture.payload,
                        context,
                        reference_before=None,
                        reference_after=None,
                    )
                    self.assertIsNotNone(readiness)
                    c.recording.phase = c.recording.phase.__class__.PREFLIGHT
                    c.recording._begin_recording_start(
                        list(fixture.participants.values()),
                        str(secret_path),
                        hosted_readiness=readiness,
                    )
                journal.assert_called_once()
                worker.assert_called_once_with(True, str(secret_path))
                self.assertEqual(fixture.capture_arm_calls, [])
            finally:
                c.host_peer = old_host
                c._primary_ordered_roster_proof = old_proof
                c._jamulus_connected = False
                c.participants = {}

    def test_readiness_acceptance_requeries_exact_server_topology(self):
        from copy import deepcopy

        from core.jamulus_roster_identity import JamulusCommonProfile

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (JamulusCommonProfile("Host", 3, "Chicago", 2),)
        )
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        old_participants = c.participants
        old_secret = c.settings.server_rpc_secret_file
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / "jsonrpc.secret"
            secret_path.write_text("s3cret\n", encoding="utf-8")
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.settings.server_rpc_secret_file = str(secret_path)
            fake_rpc = MagicMock()
            fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
            fake_rpc.__exit__ = MagicMock(return_value=None)
            fake_rpc.get_clients.return_value = fixture.payload
            try:
                with (
                    patch.object(
                        c.jamulus,
                        "ordered_roster_proof_for",
                        return_value=fixture.proof,
                    ),
                    patch(
                        "webjam_qt.controllers.recording_coordinator."
                        "_private_secret_file_identity",
                        return_value=(1, 2, 3, 4),
                    ),
                    patch(
                        "core.jamulus_server_rpc.JamulusServerRpc",
                        return_value=fake_rpc,
                    ),
                    patch(
                        "core.jamulus_server_rpc.read_secret_file",
                        return_value="s3cret",
                    ),
                ):
                    context = c.recording._hosted_recording_readiness_context(
                        list(fixture.participants.values())
                    )
                    self.assertIsNotNone(context)
                    expected = c.recording._evaluate_hosted_recording_readiness(
                        fixture.payload,
                        context,
                        reference_before=None,
                        reference_after=None,
                    )
                    self.assertIsNotNone(expected)
                    self.assertTrue(
                        c.recording._revalidate_hosted_recording_readiness(expected)
                    )

                    changed = deepcopy(fixture.payload)
                    changed["clients"][0]["channels"] = 2
                    fake_rpc.get_clients.return_value = changed
                    self.assertFalse(
                        c.recording._revalidate_hosted_recording_readiness(expected)
                    )
            finally:
                c.host_peer = old_host
                c._primary_ordered_roster_proof = old_proof
                c.participants = old_participants
                c.settings.server_rpc_secret_file = old_secret

    def test_hosted_preflight_waits_for_join_then_accepts_complete_retry(self):
        from core.local_capture import LocalCaptureTrack
        from core.jamulus_roster_identity import JamulusCommonProfile

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (
                JamulusCommonProfile("Host", 3, "Chicago", 2),
                JamulusCommonProfile("Guest", 5, "Austin", 2),
            )
        )
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.return_value = fixture.payload
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        secret_dir = TemporaryDirectory()
        secret_path = Path(secret_dir.name) / "jsonrpc.secret"
        secret_path.write_text("s3cret\n", encoding="utf-8")
        try:
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.settings.server_rpc_secret_file = str(secret_path)
            c._jamulus_connected = True
            # The native guest row arrived first; its enrolled Presence-v2
            # claim has not. This is both ordinary join timing and the safe
            # handling for an unsupported direct-Jamulus participant.
            fixture.claims_holder[0] = (fixture.claims[0],)
            with (
                patch.object(
                    c.jamulus,
                    "ordered_roster_proof_for",
                    return_value=fixture.proof,
                ),
                patch.object(
                    c.jamulus,
                    "request_ordered_roster_refresh",
                    return_value=True,
                ) as refresh,
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(
                    c.recording,
                    "_begin_recording_start",
                ) as begin,
            ):
                c._on_record_requested()
                self.assertEqual(c.recording.phase.value, "error")
                self.assertEqual(c.recording._take_id, "")
                self.assertIsNone(c.recording._local_capture)
                begin.assert_not_called()
                refresh.assert_called_with(fixture.proof.identity)
                guidance = c._show_actionable_error.call_args.kwargs
                self.assertIn("join through", guidance["next_action"])
                self.assertIn("full profiles", guidance["next_action"])

                fixture.claims_holder[0] = tuple(fixture.claims)
                c.recording.phase = c.recording.phase.__class__.IDLE
                c._show_actionable_error.reset_mock()
                c._on_record_requested()

            begin.assert_called_once()
            readiness = begin.call_args.kwargs["hosted_readiness"]
            self.assertEqual(
                dict(readiness.musician_ids_by_channel),
                dict(enumerate(fixture.participant_ids)),
            )
            self.assertEqual(readiness.reference_channels, ())
            rendered = repr(readiness) + repr(readiness.context)
            self.assertNotIn("Guest", rendered)
            self.assertNotIn("Chicago", rendered)
            self.assertNotIn(str(secret_path), rendered)
            self.assertNotIn(fixture.participant_ids[0], rendered)
            c._show_actionable_error.assert_not_called()

            ready_storage = SimpleNamespace(
                can_start=True,
                status="ready",
                required_bytes=0,
                detail="Recording storage is ready.",
            )
            capture_tracks = (
                LocalCaptureTrack("host-room", (0, 1)),
                LocalCaptureTrack("host-mic", (2,)),
            )
            with (
                patch.object(
                    c.jamulus,
                    "ordered_roster_proof_for",
                    return_value=fixture.proof,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "check_recording_storage",
                    return_value=ready_storage,
                ) as storage_check,
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "resolve_capture_tracks",
                    return_value=capture_tracks,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "check_local_capture_preflight",
                    return_value=SimpleNamespace(ready=True),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "snapshot_take_directories",
                    return_value={},
                ),
                patch.object(
                    c.recording,
                    "request_authenticated_roster_observation",
                ),
                patch.object(
                    c.recording,
                    "_revalidate_hosted_recording_readiness",
                    return_value=True,
                ),
                patch.object(
                    c.recording,
                    "_start_local_capture",
                    return_value=True,
                ),
                patch.object(
                    c.recording,
                    "_create_evidence_journal",
                    return_value=True,
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread"
                ) as toggle_thread,
            ):
                c.recording._begin_recording_start(
                    list(fixture.participants.values()),
                    str(secret_path),
                    hosted_readiness=readiness,
                )
                self.assertEqual(toggle_thread.call_count, 1)
                arm_thread = toggle_thread.call_args_list[0]
                self.assertEqual(arm_thread.kwargs["name"], "guest-capture-arm")
                arm_thread.kwargs["target"](*arm_thread.kwargs["args"])
            self.assertEqual(
                c.recording._participant_ids,
                dict(enumerate(fixture.participant_ids)),
            )
            self.assertTrue(c.recording._take_id)
            self.assertEqual(toggle_thread.call_count, 2)
            self.assertEqual(
                toggle_thread.call_args_list[1].kwargs["name"],
                "record-toggle",
            )
            self.assertEqual(len(fixture.capture_arm_calls), 1)
            self.assertEqual(
                [
                    call.kwargs["local_original_tracks"]
                    for call in storage_check.call_args_list
                ],
                [3, 4, 4, 4],
            )
        finally:
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c.participants = {}
            c.settings.server_rpc_secret_file = ""
            c._jamulus_connected = False
            c.recording._take_id = ""
            c.recording.phase = c.recording.phase.__class__.IDLE
            secret_dir.cleanup()

    def test_hosted_preflight_rejects_indistinguishable_remote_profiles(self):
        from core.jamulus_roster_identity import JamulusCommonProfile

        c = self.controller
        duplicate = JamulusCommonProfile("Alex", 5, "Austin", 2)
        fixture = _hosted_readiness_fixture(
            (
                JamulusCommonProfile("Host", 3, "Chicago", 2),
                duplicate,
                duplicate,
            )
        )
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.return_value = fixture.payload
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        secret_dir = TemporaryDirectory()
        secret_path = Path(secret_dir.name) / "jsonrpc.secret"
        secret_path.write_text("s3cret\n", encoding="utf-8")
        try:
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.settings.server_rpc_secret_file = str(secret_path)
            c._jamulus_connected = True
            with (
                patch.object(
                    c.jamulus,
                    "ordered_roster_proof_for",
                    return_value=fixture.proof,
                ),
                patch.object(
                    c.jamulus,
                    "request_ordered_roster_refresh",
                    return_value=True,
                ),
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(c.recording, "_begin_recording_start") as begin,
            ):
                c._on_record_requested()

            begin.assert_not_called()
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(c.recording.phase.value, "error")
            self.assertIn(
                "identical full Jamulus profiles",
                c._show_actionable_error.call_args.kwargs["likely_cause"],
            )
        finally:
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c.participants = {}
            c.settings.server_rpc_secret_file = ""
            c._jamulus_connected = False
            c.recording.phase = c.recording.phase.__class__.IDLE
            secret_dir.cleanup()

    def test_hosted_preflight_rejects_unsupported_direct_jamulus_peer(self):
        from core.jamulus_roster_identity import JamulusCommonProfile

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (
                JamulusCommonProfile("Host", 3, "Chicago", 2),
                JamulusCommonProfile("Direct Client", 8, "Denver", 1),
            )
        )
        fixture.claims_holder[0] = (fixture.claims[0],)
        secret_dir = TemporaryDirectory()
        secret_path = Path(secret_dir.name) / "jsonrpc.secret"
        secret_path.write_text("s3cret\n", encoding="utf-8")
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        try:
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.settings.server_rpc_secret_file = str(secret_path)
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=fixture.proof,
            ):
                context = c.recording._hosted_recording_readiness_context(
                    list(fixture.participants.values())
                )
                self.assertIsNotNone(context)
                readiness = c.recording._evaluate_hosted_recording_readiness(
                    fixture.payload,
                    context,
                    reference_before=None,
                    reference_after=None,
                )
            self.assertIsNone(readiness)
        finally:
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c.participants = {}
            c.settings.server_rpc_secret_file = ""
            secret_dir.cleanup()

    def test_hosted_preflight_allows_only_exact_owned_reference_without_presence(self):
        from core.jamulus_roster_identity import JamulusCommonProfile
        from core.reference_track import ReferenceTrackOwnershipClaim

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (
                JamulusCommonProfile("Host", 3, "Chicago", 2),
                JamulusCommonProfile("WebJam Track", 0, "", 0),
            )
        )
        fixture.claims_holder[0] = (fixture.claims[0],)
        fixture.payload["clients"][1]["address"] = "127.0.0.1:51042"
        claim = ReferenceTrackOwnershipClaim(51042, 2468, "b" * 32)
        secret_dir = TemporaryDirectory()
        secret_path = Path(secret_dir.name) / "jsonrpc.secret"
        secret_path.write_text("s3cret\n", encoding="utf-8")
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        try:
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.settings.server_rpc_secret_file = str(secret_path)
            with patch.object(
                c.jamulus,
                "ordered_roster_proof_for",
                return_value=fixture.proof,
            ):
                context = c.recording._hosted_recording_readiness_context(
                    list(fixture.participants.values())
                )
                self.assertIsNotNone(context)
                readiness = c.recording._evaluate_hosted_recording_readiness(
                    fixture.payload,
                    context,
                    reference_before=claim,
                    reference_after=claim,
                )
            self.assertIsNotNone(readiness)
            self.assertEqual(
                readiness.musician_ids_by_channel,
                ((0, fixture.participant_ids[0]),),
            )
            self.assertEqual(readiness.reference_channels, (1,))
            self.assertNotIn("51042", repr(readiness))
            self.assertNotIn("2468", repr(readiness))
        finally:
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c.participants = {}
            c.settings.server_rpc_secret_file = ""
            secret_dir.cleanup()

    def test_hosted_preflight_rejects_rpc_config_changed_before_apply(self):
        from core.jamulus_roster_identity import JamulusCommonProfile

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (JamulusCommonProfile("Host", 3, "Chicago", 2),)
        )
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.return_value = fixture.payload
        secret_dir = TemporaryDirectory()
        secret_path = Path(secret_dir.name) / "jsonrpc.secret"
        secret_path.write_text("s3cret\n", encoding="utf-8")
        callbacks = []
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        old_port = c.settings.server_rpc_port
        try:
            c.host_peer = fixture.host_peer
            c._primary_ordered_roster_proof = fixture.proof
            c.participants = fixture.participants
            c.settings.server_rpc_secret_file = str(secret_path)
            c._jamulus_connected = True
            with (
                patch.object(
                    c.jamulus,
                    "ordered_roster_proof_for",
                    return_value=fixture.proof,
                ),
                patch.object(
                    c.jamulus,
                    "request_ordered_roster_refresh",
                    return_value=True,
                ),
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=callbacks.append,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(c.recording, "_begin_recording_start") as begin,
            ):
                c._on_record_requested()
                self.assertEqual(len(callbacks), 1)
                c.settings.server_rpc_port = old_port + 1
                callbacks.pop()()

            begin.assert_not_called()
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(c.recording.phase.value, "error")
        finally:
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c.participants = {}
            c.settings.server_rpc_secret_file = ""
            c.settings.server_rpc_port = old_port
            c._jamulus_connected = False
            c.recording.phase = c.recording.phase.__class__.IDLE
            secret_dir.cleanup()

    def test_toggle_worker_uses_take_captured_rpc_port(self):
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _ToggleAttempt,
            _private_secret_file_identity,
        )

        c = self.controller
        old_port = c.settings.server_rpc_port
        old_secret_file = c.settings.server_rpc_secret_file
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / "jsonrpc.secret"
            secret_path.write_text("captured-secret\n", encoding="utf-8")
            take_id = new_project_id()
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            identity = _private_secret_file_identity(secret_path)
            c.recording._bind_recording_rpc_configuration(
                take_id,
                41_234,
                str(secret_path),
                identity,
            )
            attempt = _ToggleAttempt(
                take_id,
                target_armed=True,
                server_rpc_port=41_234,
                server_rpc_secret_file=str(secret_path),
                server_rpc_secret_identity=identity,
            )
            fake_rpc = MagicMock()
            fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
            fake_rpc.__exit__ = MagicMock(return_value=None)
            fake_rpc.get_clients.return_value = {"clients": []}
            fake_rpc.start_recording.return_value = True
            fake_rpc.get_recorder_status.return_value = {"enabled": True}
            c.settings.server_rpc_port = 49_999
            c.settings.server_rpc_secret_file = "/private/changed/secret"
            try:
                with (
                    patch(
                        "core.jamulus_server_rpc.JamulusServerRpc",
                        return_value=fake_rpc,
                    ) as rpc_factory,
                    patch.object(c.recording, "_consume_authenticated_roster"),
                    patch.object(c._ui_invoker, "invoke"),
                ):
                    c.recording._run_toggle_attempt(attempt)
                rpc_factory.assert_called_once_with(
                    port=41_234,
                    secret="captured-secret",
                )
                self.assertNotIn(str(secret_path), repr(attempt))
            finally:
                c.settings.server_rpc_port = old_port
                c.settings.server_rpc_secret_file = old_secret_file
                c.recording._retire_active_take(take_id)

    def test_toggle_worker_rejects_replaced_take_secret_without_path_leak(self):
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _ToggleAttempt,
            _private_secret_file_identity,
        )

        c = self.controller
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / "jsonrpc.secret"
            secret_path.write_text("original-secret\n", encoding="utf-8")
            take_id = new_project_id()
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            identity = _private_secret_file_identity(secret_path)
            c.recording._bind_recording_rpc_configuration(
                take_id,
                41_235,
                str(secret_path),
                identity,
            )
            attempt = _ToggleAttempt(
                take_id,
                target_armed=True,
                server_rpc_port=41_235,
                server_rpc_secret_file=str(secret_path),
                server_rpc_secret_identity=identity,
            )
            secret_path.write_text(
                "replacement-secret-with-different-size\n",
                encoding="utf-8",
            )
            try:
                with (
                    patch("core.jamulus_server_rpc.JamulusServerRpc") as rpc_factory,
                    patch.object(
                        c._ui_invoker,
                        "invoke",
                        side_effect=lambda callback: callback(),
                    ),
                    patch.object(c.recording, "apply_toggle_failure") as failure,
                ):
                    c.recording._run_toggle_attempt(attempt)
                rpc_factory.assert_not_called()
                message = failure.call_args.args[0]
                self.assertIn("captured recorder configuration", message)
                self.assertNotIn(str(secret_path), message)
            finally:
                c.recording._retire_active_take(take_id)

    def test_active_and_final_roster_reads_use_take_captured_rpc_binding(self):
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _private_secret_file_identity,
        )

        c = self.controller
        old_port = c.settings.server_rpc_port
        old_secret_file = c.settings.server_rpc_secret_file
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / "jsonrpc.secret"
            secret_path.write_text("captured-secret\n", encoding="utf-8")
            take_id = new_project_id()
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            c.recording._bind_recording_rpc_configuration(
                take_id,
                41_236,
                str(secret_path),
                _private_secret_file_identity(secret_path),
            )
            context = c.recording._roster_observation_context()
            self.assertIsNotNone(context)
            fake_rpc = MagicMock()
            fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
            fake_rpc.__exit__ = MagicMock(return_value=None)
            fake_rpc.get_clients.return_value = {"clients": []}
            c.settings.server_rpc_port = 49_999
            c.settings.server_rpc_secret_file = "/private/changed/secret"
            try:
                with (
                    patch(
                        "core.jamulus_server_rpc.JamulusServerRpc",
                        return_value=fake_rpc,
                    ) as rpc_factory,
                    patch.object(c.recording, "_consume_authenticated_roster"),
                    patch.object(
                        c.recording,
                        "_consume_authenticated_roster_serial",
                    ),
                ):
                    with c.recording._receipt_lock:
                        c.recording._roster_poll_pending = context
                        c.recording._roster_poll_inflight = True
                    c.recording._roster_observation_worker()
                    c.recording._final_recording_receipt_snapshot()
                self.assertEqual(rpc_factory.call_count, 2)
                self.assertTrue(
                    all(
                        call.kwargs
                        == {
                            "port": 41_236,
                            "secret": "captured-secret",
                        }
                        for call in rpc_factory.call_args_list
                    )
                )
                self.assertNotIn(str(secret_path), repr(context))
            finally:
                c.settings.server_rpc_port = old_port
                c.settings.server_rpc_secret_file = old_secret_file
                c.recording._retire_active_take(take_id)

    def test_private_evidence_journal_tracks_confirmed_start_stop_and_retires(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            c.settings.takes_directory = directory
            c.settings.musician_name = "Test Host"
            c.recording._take_id = new_project_id()
            c.recording._local_participant_id = new_project_id()
            c.recording._reset_session_evidence()

            self.assertTrue(c.recording._create_evidence_journal())
            journal = RecordingManifestJournal(directory)
            initial = journal.load(c.recording._take_id)
            self.assertIsNotNone(initial)
            self.assertTrue(initial.trusted)
            self.assertFalse(initial.evidence.started_utc)

            with (
                patch.object(c, "signal_peer_recording_started"),
                patch.object(c, "signal_peer_recording_stopped"),
                patch.object(c.recording, "_begin_take_validation"),
            ):
                c.recording.apply_toggle_result(True)
                c.recording.apply_toggle_result(False)

            completed = journal.load(c.recording._take_id)
            self.assertIsNotNone(completed)
            self.assertTrue(completed.trusted)
            self.assertTrue(completed.evidence.started_utc)
            self.assertTrue(completed.evidence.ended_utc)
            self.assertEqual(
                [item.event for item in completed.evidence.timeline],
                ["recording_requested", "recording_started", "recording_stopped"],
            )

            c.recording._remove_evidence_journal_after_manifest()
            self.assertIsNone(journal.load(c.recording._take_id))

    def test_evidence_journal_setup_failure_blocks_server_recording(self):
        from core.jamulus_roster_identity import JamulusCommonProfile
        from core.recording_readiness import (
            RecordingStorageCheck,
            RecordingStorageStatus,
        )

        c = self.controller
        fixture = _hosted_readiness_fixture(
            (JamulusCommonProfile("Host", 3, "Chicago", 2),)
        )
        old_host = c.host_peer
        old_proof = c._primary_ordered_roster_proof
        c.settings.server_rpc_secret_file = "/tmp/secret"
        c._jamulus_connected = True
        c.host_peer = fixture.host_peer
        c._primary_ordered_roster_proof = fixture.proof
        c.participants = fixture.participants
        ready = RecordingStorageCheck(
            RecordingStorageStatus.READY,
            "Recording storage is ready.",
            10_000_000,
            1_000_000,
        )
        try:
            with (
                patch.object(
                    c.jamulus,
                    "ordered_roster_proof_for",
                    return_value=fixture.proof,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "check_recording_storage",
                    return_value=ready,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator."
                    "_private_secret_file_identity",
                    return_value=(1, 2, 3, 4),
                ),
                patch.object(
                    c.recording, "_create_evidence_journal", return_value=False
                ),
                patch.object(
                    c.recording,
                    "_revalidate_hosted_recording_readiness",
                    return_value=True,
                ),
                patch.object(c, "_record_toggle_worker") as worker,
            ):
                context = c.recording._hosted_recording_readiness_context(
                    list(fixture.participants.values())
                )
                self.assertIsNotNone(context)
                readiness = c.recording._evaluate_hosted_recording_readiness(
                    fixture.payload,
                    context,
                    reference_before=None,
                    reference_after=None,
                )
                self.assertIsNotNone(readiness)
                c.recording.phase = c.recording.phase.__class__.PREFLIGHT
                c.recording._begin_recording_start(
                    list(fixture.participants.values()),
                    "/tmp/secret",
                    hosted_readiness=readiness,
                )

            worker.assert_not_called()
            self.assertEqual(c.recording.phase.value, "error")
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(
                c._show_actionable_error.call_args.args[0],
                "Recording Recovery Setup Failed",
            )
            self.assertIn(
                "No server recording was started",
                c._show_actionable_error.call_args.kwargs["next_action"],
            )
        finally:
            c.host_peer = old_host
            c._primary_ordered_roster_proof = old_proof
            c._jamulus_connected = False
            c.participants = {}

    def test_pending_evidence_journal_is_surfaced_without_private_identifier(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            c.settings.takes_directory = directory
            take_id = new_project_id()
            RecordingManifestJournal(directory).create(take_id, SessionEvidence())
            c.recording._stale_journal_scan_done = False
            c.recording._recover_stale_evidence_journals_once()

        message = c.window.flash_message.call_args.args[0]
        self.assertIn("interrupted recording", message)
        self.assertIn("Open Studio", message)
        self.assertNotIn(take_id, message)

    def test_active_take_journal_is_never_recovered_as_stale(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            c.settings.takes_directory = directory
            take_id = new_project_id()
            journal = RecordingManifestJournal(directory)
            journal.create(take_id, SessionEvidence())
            c.recording._take_id = take_id
            c.recording._validation_take_id = ""
            c.recording._stale_journal_scan_done = False

            c.recording._recover_stale_evidence_journals_once()

            pending = journal.load(take_id)
            false_project = (
                Path(directory) / f"Recovered-{take_id}" / "webjam-take.json"
            ).exists()

        self.assertIsNotNone(pending)
        self.assertTrue(pending.trusted)
        self.assertFalse(false_project)
        c.window.flash_message.assert_not_called()
        c.recording._take_id = ""

    def test_published_take_scan_skips_malformed_child_and_finds_valid_match(self):
        from core.take_project import ProjectStatus, TakeProject, new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _PublishedTakeStatus,
        )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "a-malformed"
            malformed.mkdir()
            (malformed / "webjam-take.json").write_text("{bad", encoding="utf-8")

            take_id = new_project_id()
            valid = root / "b-valid"
            valid.mkdir()
            project = TakeProject(
                session_id=new_project_id(),
                take_id=take_id,
                session_title="",
                take_name="Existing take",
                status=ProjectStatus.NEEDS_ATTENTION,
                project_sample_rate=48_000,
                participants=(),
                tracks=(),
            )
            (valid / "webjam-take.json").write_text(
                json.dumps(project.to_dict()), encoding="utf-8"
            )

            result = self.controller.recording._published_take_has_id(
                directory, take_id
            )
            (valid / "webjam-take.json").unlink()
            malformed_only = self.controller.recording._published_take_has_id(
                directory, take_id
            )

        self.assertIs(result, _PublishedTakeStatus.MATCH)
        self.assertIs(malformed_only, _PublishedTakeStatus.ABSENT)

    def test_published_take_scan_is_indeterminate_when_inventory_is_truncated(self):
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _PublishedTakeStatus,
        )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(513):
                (root / f"entry-{index:03d}").mkdir()
            result = self.controller.recording._published_take_has_id(
                directory, new_project_id()
            )

        self.assertIs(result, _PublishedTakeStatus.INDETERMINATE)

    def test_indeterminate_published_take_scan_retains_recovery_journal(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _PublishedTakeStatus,
        )

        c = self.controller
        with TemporaryDirectory() as directory:
            take_id = new_project_id()
            journal = RecordingManifestJournal(directory)
            journal.create(take_id, SessionEvidence())
            c.settings.takes_directory = directory
            c.recording._stale_journal_scan_done = False
            with patch.object(
                c.recording,
                "_published_take_has_id",
                return_value=_PublishedTakeStatus.INDETERMINATE,
            ):
                c.recording._recover_stale_evidence_journals_once()

            pending = journal.load(take_id)
            false_project = (
                Path(directory) / f"Recovered-{take_id}" / "webjam-take.json"
            ).exists()

        self.assertIsNotNone(pending)
        self.assertFalse(false_project)

    def test_published_take_scan_io_error_is_not_reported_as_absence(self):
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            _PublishedTakeStatus,
        )

        with patch.object(Path, "iterdir", side_effect=OSError("private path")):
            result = self.controller.recording._published_take_has_id(
                "/private/takes", new_project_id()
            )
        self.assertIs(result, _PublishedTakeStatus.INDETERMINATE)

    def test_stale_capture_recovery_log_never_includes_local_path(self):
        c = self.controller
        with TemporaryDirectory(prefix="Private Band Takes ") as directory:
            private_path = Path(directory) / "Secret Session" / "Recovered"
            item = SimpleNamespace(recovery_dir=private_path)
            c.settings.takes_directory = directory
            c.recording._stale_capture_scan_done = False
            with (
                patch(
                    "core.local_capture.recover_stale_local_captures",
                    return_value=(item,),
                ),
                patch.object(c.recording, "_publish_recovered_local_capture"),
                self.assertLogs("webjam.qt.recording", level="WARNING") as logs,
            ):
                c.recording._recover_stale_captures_once()

        rendered = "\n".join(logs.output)
        self.assertIn("Recovered 1 abandoned local capture", rendered)
        self.assertNotIn(str(private_path), rendered)
        self.assertNotIn("Secret Session", rendered)

    def test_recovered_evidence_journal_is_published_as_review_project(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import RecoveryStatus, SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            c.settings.takes_directory = directory
            take_id = new_project_id()
            journal = RecordingManifestJournal(directory)
            journal.create(
                take_id,
                SessionEvidence(
                    protocol_version="jamulus-3.12.2",
                    recovery_notes=("Interrupted server recording checkpoint",),
                ),
            )
            c.recording._stale_journal_scan_done = False
            c.recording._recover_stale_evidence_journals_once()
            recovery_dir = Path(directory) / f"Recovered-{take_id}"
            manifest = json.loads((recovery_dir / "webjam-take.json").read_text())
            recovery_files = {
                path.relative_to(recovery_dir).as_posix()
                for path in recovery_dir.rglob("*")
                if path.is_file()
            }
            recovered_journal = RecordingManifestJournal(directory)

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(
            manifest["session"]["recovery_status"],
            RecoveryStatus.NEEDS_ATTENTION.value,
        )
        self.assertEqual(manifest["status"], "needs_attention")
        self.assertEqual(manifest["tracks"], [])
        self.assertEqual(recovery_files, {"webjam-take.json"})
        self.assertIn("review-only", " ".join(manifest["errors"]))
        self.assertIsNone(recovered_journal.load(take_id))

    def test_relaunch_reconciles_partial_server_filename_staging_without_guessing(self):
        import hashlib
        import struct
        import wave

        from core.take_library import load_take, parse_jamulus_recording_filename
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id

        def write_wav(path: Path, seconds: float) -> None:
            frames = int(seconds * 48_000)
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(48_000)
                audio.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))

        c = self.controller
        with TemporaryDirectory() as directory:
            root = Path(directory)
            take = root / "interrupted"
            take.mkdir()
            alice = "Alice-127_0_0_1_52000-0-1.wav"
            bob = "Bob_-127_0_0_1_52001-100-1.wav"
            session_id = new_project_id()
            take_id = new_project_id()
            write_wav(take / alice, 0.1)
            write_wav(take / bob, 0.2)
            (take / "take.lof").write_text(
                f'file "{alice}" offset 0.00000000000000\n'
                f'file "{bob}" offset 1.00000000000000\n',
                encoding="utf-8",
            )

            entries = []
            for index, (filename, offset) in enumerate(
                ((alice, 0.0), (bob, 1.0)), start=1
            ):
                source = take / filename
                parsed = parse_jamulus_recording_filename(filename)
                self.assertIsNotNone(parsed)
                entries.append(
                    {
                        "filename": f"server-media-{index:03d}.wav",
                        "recorder_key_sha256": parsed.recorder_key_sha256,
                        "start_frame": parsed.start_frame,
                        "channels": parsed.channels,
                        "collision_index": parsed.collision_index,
                        "offset_s": offset,
                        "size_bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                )
            (take / ".webjam-recording-staging.json").write_text(
                json.dumps(
                    {
                        "schema": 2,
                        "session_id": session_id,
                        "take_id": take_id,
                        "entries": entries,
                    }
                ),
                encoding="utf-8",
            )
            (take / alice).replace(take / "server-media-001.wav")
            journal = RecordingManifestJournal(root)
            journal.create(
                take_id,
                SessionEvidence(
                    protocol_version="jamulus-3.12.2",
                    recovery_notes=("Original recording checkpoint",),
                ),
            )

            before_recovery = load_take(take)
            self.assertIsNotNone(before_recovery)
            self.assertNotIn(
                "Bob", " ".join(track.name for track in before_recovery.tracks)
            )
            self.assertNotIn(
                "52001", " ".join(track.name for track in before_recovery.tracks)
            )

            c.settings.takes_directory = directory
            c.recording._staged_take_scan_done = False
            c.recording._stale_capture_scan_done = True
            c.recording._stale_journal_scan_done = False
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
            ):
                c.recording.recover_interrupted_recordings()

            persisted = "\n".join(
                item.read_text(encoding="utf-8")
                for item in take.iterdir()
                if item.suffix in {".json", ".lof", ".rpp"}
            )
            payload = json.loads((take / "webjam-take.json").read_text())
            marker_exists = (take / ".webjam-recording-staging.json").exists()
            bob_exists = (take / bob).exists()
            first_opaque_exists = (take / "server-media-001.wav").is_file()
            second_opaque_exists = (take / "server-media-002.wav").is_file()
            pending_journal = journal.load(take_id)
            manifest_paths = tuple(root.glob("*/webjam-take.json"))

        self.assertFalse(marker_exists)
        self.assertFalse(bob_exists)
        self.assertTrue(first_opaque_exists)
        self.assertTrue(second_opaque_exists)
        self.assertEqual(payload["status"], "needs_attention")
        self.assertEqual(payload["session_id"], session_id)
        self.assertEqual(payload["take_id"], take_id)
        self.assertIn(
            "Original recording checkpoint",
            payload["session"]["recovery_notes"],
        )
        self.assertTrue(
            all(track["participant_id"] is None for track in payload["tracks"])
        )
        self.assertIsNone(pending_journal)
        self.assertEqual(manifest_paths, (take / "webjam-take.json",))
        for private in ("Alice", "Bob", "52000", "52001", "127_0_0_1"):
            self.assertNotIn(private, persisted)
        message = c.window.flash_message.call_args.args[0]
        self.assertIn("without guessing musician identity", message)

    def test_staged_media_keeps_untrusted_linked_journal_without_false_project(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session_id = new_project_id()
            take_id = new_project_id()
            take, native = _make_staged_server_take(
                root,
                "interrupted",
                session_id=session_id,
                take_id=take_id,
                client_name="Alice",
                port=52000,
            )
            journal = RecordingManifestJournal(root)
            journal.create(take_id, SessionEvidence())
            journal.path_for(take_id).write_text("{malformed", encoding="utf-8")

            c.settings.takes_directory = directory
            c.recording._staged_take_scan_done = False
            c.recording._staged_media_take_ids = set()
            c.recording._stale_capture_scan_done = True
            c.recording._stale_journal_scan_done = False
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
            ):
                c.recording.recover_interrupted_recordings()

            payload = json.loads((take / "webjam-take.json").read_text())
            pending = journal.load(take_id)
            false_project = root / f"Recovered-{take_id}" / "webjam-take.json"
            false_project_exists = false_project.exists()
            marker_exists = (take / ".webjam-recording-staging.json").exists()
            native_exists = (take / native).exists()

        self.assertEqual(payload["take_id"], take_id)
        self.assertEqual(payload["status"], "needs_attention")
        self.assertIn(
            "could not be safely read",
            " ".join(payload["session"]["recovery_notes"]),
        )
        self.assertIsNotNone(pending)
        self.assertFalse(pending.trusted)
        self.assertFalse(false_project_exists)
        self.assertFalse(marker_exists)
        self.assertFalse(native_exists)

    def test_duplicate_staging_take_ids_fail_closed_without_retiring_journal(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session_id = new_project_id()
            take_id = new_project_id()
            first, first_native = _make_staged_server_take(
                root,
                "first",
                session_id=session_id,
                take_id=take_id,
                client_name="Alice",
                port=52000,
            )
            second, second_native = _make_staged_server_take(
                root,
                "second",
                session_id=session_id,
                take_id=take_id,
                client_name="Bob",
                port=52001,
                start_frame=100,
            )
            journal = RecordingManifestJournal(root)
            journal.create(take_id, SessionEvidence())

            c.settings.takes_directory = directory
            c.recording._staged_take_scan_done = False
            c.recording._staged_media_take_ids = set()
            c.recording._stale_capture_scan_done = True
            c.recording._stale_journal_scan_done = False
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
            ):
                c.recording.recover_interrupted_recordings()

            pending = journal.load(take_id)
            false_project = root / f"Recovered-{take_id}" / "webjam-take.json"
            facts = (
                (first / ".webjam-recording-staging.json").is_file(),
                (second / ".webjam-recording-staging.json").is_file(),
                (first / first_native).is_file(),
                (second / second_native).is_file(),
                (first / "webjam-take.json").exists(),
                (second / "webjam-take.json").exists(),
                false_project.exists(),
            )

        self.assertIsNotNone(pending)
        self.assertTrue(pending.trusted)
        self.assertEqual(facts, (True, True, True, True, False, False, False))

    def test_wrong_checksum_staging_cannot_claim_or_retire_linked_journal(self):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session_id = new_project_id()
            take_id = new_project_id()
            take, native = _make_staged_server_take(
                root,
                "copied-marker",
                session_id=session_id,
                take_id=take_id,
                client_name="Mallory",
                port=52002,
            )
            marker_path = take / ".webjam-recording-staging.json"
            marker = json.loads(marker_path.read_text())
            marker["entries"][0]["sha256"] = "0" * 64
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            journal = RecordingManifestJournal(root)
            journal.create(take_id, SessionEvidence())

            c.settings.takes_directory = directory
            c.recording._staged_take_scan_done = False
            c.recording._staged_media_take_ids = set()
            c.recording._stale_capture_scan_done = True
            c.recording._stale_journal_scan_done = False
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.threading.Thread",
                    side_effect=lambda *args, **kwargs: _Immediate(*args, **kwargs),
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=lambda callback: callback(),
                ),
            ):
                c.recording.recover_interrupted_recordings()

            pending = journal.load(take_id)
            facts = (
                marker_path.is_file(),
                (take / native).is_file(),
                (take / "webjam-take.json").exists(),
                (root / f"Recovered-{take_id}" / "webjam-take.json").exists(),
            )

        self.assertIsNotNone(pending)
        self.assertTrue(pending.trusted)
        self.assertEqual(facts, (True, True, False, False))

    def test_recovered_local_capture_publishes_recovery_manifest_and_retires_trusted_journal(
        self,
    ):
        """Recovered PCM is bound to its original opaque take, not left orphaned."""
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import RecoveryStatus, SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            recovery_dir = Path(directory) / "Recovered-local-test"
            recovery_dir.mkdir()
            recovered_audio = recovery_dir / "host-guitar.recovered-partial.wav"
            recovered_audio.write_bytes(b"preserved media")
            take_id = new_project_id()
            session_id = new_project_id()
            journal = RecordingManifestJournal(directory)
            journal.create(take_id, SessionEvidence(protocol_version="jamulus-3.12.2"))
            item = SimpleNamespace(
                recovery_dir=recovery_dir,
                files=(recovered_audio,),
                take_id=take_id,
                session_id=session_id,
                started_utc="2026-07-14T00:00:00Z",
                total_frames=48_000,
                errors=("Local writer stopped unexpectedly.",),
                gaps=(),
                capture_device=None,
            )
            complete = SimpleNamespace(take=SimpleNamespace(path=recovery_dir))
            with patch(
                "webjam_qt.controllers.recording_coordinator.write_take_manifest",
                return_value=complete,
            ) as write_manifest:
                c.recording._publish_recovered_local_capture(item, directory)

            kwargs = write_manifest.call_args.kwargs
            self.assertEqual(kwargs["session_id"], session_id)
            self.assertEqual(kwargs["take_id"], take_id)
            self.assertIn(
                "recovered after an interrupted recording", kwargs["capture_errors"][0]
            )
            self.assertEqual(
                kwargs["session_evidence"].recovery_status,
                RecoveryStatus.NEEDS_ATTENTION,
            )
            self.assertIn(
                "local_capture_recovered",
                [event.event for event in kwargs["session_evidence"].timeline],
            )
            self.assertIsNone(journal.load(take_id))

    def test_recovered_local_capture_writes_a_partial_project_with_durable_boundary(
        self,
    ):
        """Recovered WAVs become review-only schema-v2 projects on startup."""
        import struct
        import wave

        from core.local_capture import RecoveredLocalCapture
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import RecoveryStatus, SessionEvidence, new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            recovery_dir = Path(directory) / "Recovered-local-test"
            recovery_dir.mkdir()
            recovered_audio = recovery_dir / "host-guitar.recovered-partial.wav"
            with wave.open(str(recovered_audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(struct.pack("<480h", *([800] * 480)))
            original_audio = recovered_audio.read_bytes()
            take_id = new_project_id()
            session_id = new_project_id()
            journal = RecordingManifestJournal(directory)
            journal.create(take_id, SessionEvidence(protocol_version="jamulus-3.12.2"))
            item = RecoveredLocalCapture(
                source_dir=recovery_dir,
                recovery_dir=recovery_dir,
                files=(recovered_audio,),
                take_id=take_id,
                session_id=session_id,
                started_utc="2026-07-14T00:00:00Z",
                total_frames=480,
                durable_frames=240,
                sample_rate=48_000,
            )

            c.recording._publish_recovered_local_capture(item, directory)
            payload = json.loads((recovery_dir / "webjam-take.json").read_text())
            self.assertEqual(recovered_audio.read_bytes(), original_audio)
            self.assertIsNone(journal.load(take_id))

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["take_id"], take_id)
        self.assertEqual(payload["session_id"], session_id)
        self.assertEqual(
            payload["session"]["recovery_status"], RecoveryStatus.NEEDS_ATTENTION.value
        )
        segment = payload["tracks"][0]["segments"][0]
        self.assertEqual(segment["media_status"], "partial")
        self.assertIn(
            {
                "start_frame": 240,
                "frame_count": 240,
                "reason": "unverified_after_crash_checkpoint",
                "channels": [0],
            },
            segment["gaps"],
        )

    def test_live_take_validation_marks_non_durable_local_media_partial(self):
        """The live finalizer must retain the capture durability boundary."""
        import struct
        import wave

        from core.take_project import new_project_id

        def write_wav(path: Path) -> None:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(struct.pack("<480h", *([800] * 480)))

        c = self.controller
        with TemporaryDirectory() as directory:
            root = Path(directory)
            take_dir = root / "Jamulus Take"
            take_dir.mkdir()
            write_wav(take_dir / "Band-0-1.wav")
            write_wav(take_dir / "host-guitar.wav")
            write_wav(take_dir / "host-vocal.wav")
            take_id = new_project_id()
            c.settings.takes_directory = directory
            c.recording._take_id = take_id
            c.recording._expected_tracks = 1
            c.recording._reset_session_evidence()
            capture = MagicMock()
            capture.stop_into.return_value = SimpleNamespace(
                errors=("Local capture durability checkpoint failed.",),
                started_utc="2026-07-16T00:00:00Z",
                duration_s=0.01,
                gaps=(),
                total_frames=480,
                durable_frames=240,
                capture_device=None,
            )
            c.recording._local_capture = capture
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.find_changed_take",
                    return_value=take_dir,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.wait_for_take_files_stable",
                    return_value=True,
                ),
            ):
                c.recording._build_take_validation(take_id=take_id)
            payload = json.loads((take_dir / "webjam-take.json").read_text())
        c.recording._expected_tracks = 0

        local_segments = [
            track["segments"][0]
            for track in payload["tracks"]
            if track["source"] == "local_isolated"
        ]
        self.assertEqual(len(local_segments), 2)
        for segment in local_segments:
            self.assertEqual(segment["media_status"], "partial")
            self.assertIn(
                {
                    "start_frame": 240,
                    "frame_count": 240,
                    "reason": "unverified_after_crash_checkpoint",
                    "channels": [0],
                },
                segment["gaps"],
            )

    def test_shutdown_validation_publishes_one_media_take_and_retires_journal(self):
        import struct
        import wave

        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import new_project_id

        c = self.controller
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw_take = root / "Jamulus Take"
            raw_take.mkdir()
            native = raw_take / "Alice-127_0_0_1_50000-0-1.wav"
            with wave.open(str(native), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(struct.pack("<480h", *([800] * 480)))
            (raw_take / "take.lof").write_text(
                f'file "{native.name}" offset 0.00000000000000\n',
                encoding="utf-8",
            )

            take_id = new_project_id()
            c.settings.takes_directory = directory
            c.recording._take_id = take_id
            c.recording._expected_tracks = 1
            c.recording._reset_session_evidence()
            self.assertTrue(c.recording._create_evidence_journal())

            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.find_changed_take",
                    return_value=raw_take,
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.wait_for_take_files_stable",
                    return_value=True,
                ),
                patch.object(
                    c.recording,
                    "_final_recording_receipt_snapshot",
                    return_value=((), ("Recorder identity remained unproven.",)),
                ),
            ):
                result = c.recording._build_take_validation(take_id=take_id)

            payload = json.loads((raw_take / "webjam-take.json").read_text())
            journal = RecordingManifestJournal(directory)
            c.recording._stale_journal_scan_done = False
            c.recording._recover_stale_evidence_journals_once()
            manifest_paths = tuple(root.glob("*/webjam-take.json"))
            pending_journal = journal.load(take_id)

        self.assertIsNotNone(result.take)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["take_id"], take_id)
        self.assertEqual(len(payload["tracks"]), 1)
        self.assertIsNone(pending_journal)
        self.assertEqual(manifest_paths, (raw_take / "webjam-take.json",))
        c.recording._expected_tracks = 0
        c.recording._take_id = ""

    def test_missing_server_take_forwards_durable_boundary_to_recovery_manifest(self):
        """The no-server recovery path keeps the same export-blocking boundary."""
        from core.session_recording_plan import InputMapBinding

        c = self.controller
        with TemporaryDirectory() as directory:
            c.settings.takes_directory = directory
            c.recording._reset_session_evidence()
            plan = self._install_bound_capture_plan(
                (InputMapBinding("Host Mic", 1, True, True),)
            )
            take_id = plan.take_id
            capture = MagicMock()

            def stop_into(destination):
                Path(destination).mkdir(parents=True, exist_ok=True)
                return SimpleNamespace(
                    errors=("Local capture durability checkpoint failed.",),
                    started_utc="2026-07-16T00:00:00Z",
                    duration_s=0.01,
                    gaps=(),
                    total_frames=480,
                    durable_frames=240,
                    capture_device=None,
                    recovery_dir=None,
                    # A broken writer supplied no finalized topology. The
                    # manifest must see this absence, not the intended plan.
                    tracks=(),
                )

            capture.stop_into.side_effect = stop_into
            c.recording._local_capture = capture
            fake_result = SimpleNamespace(take=None, errors=(), warnings=())
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.find_changed_take",
                    return_value=None,
                ),
                patch("webjam_qt.controllers.recording_coordinator.time.sleep"),
                patch(
                    "webjam_qt.controllers.recording_coordinator.write_take_manifest",
                    return_value=fake_result,
                ) as write_manifest,
            ):
                c.recording._build_take_validation(take_id=take_id)

        self.assertEqual(write_manifest.call_args.kwargs["local_durable_frames"], 240)
        self.assertEqual(write_manifest.call_args.kwargs["local_capture_tracks"], ())
        self.assertIs(write_manifest.call_args.kwargs["recording_plan"], plan)

    def test_salvage_reports_the_capture_recovery_folder_not_a_guess(self):
        c = self.controller
        with TemporaryDirectory() as directory:
            c.settings.takes_directory = directory
            actual = Path(directory) / "Recovered-local-actual"
            actual.mkdir()
            fake_capture = MagicMock()
            fake_capture.stop_into.return_value = SimpleNamespace(
                errors=(),
                recovery_dir=actual,
                files=(),
            )
            c.recording._local_capture = fake_capture

            recovered, errors = c.recording._salvage_capture()

        self.assertEqual(recovered, actual)
        self.assertEqual(errors, ())

    def test_salvage_logs_neither_recovery_path_nor_capture_error(self):
        c = self.controller
        private_path = "/Users/musician/Secret Sessions/Recovered-local"
        private_error = f"writer failed beside {private_path}"
        fake_capture = MagicMock()
        fake_capture.stop_into.return_value = SimpleNamespace(
            errors=(private_error,),
            recovery_dir=Path(private_path),
            files=(),
        )
        c.recording._local_capture = fake_capture

        with self.assertLogs("webjam.qt.recording", level="INFO") as captured:
            recovered, errors = c.recording._salvage_capture()

        rendered = "\n".join(captured.output)
        self.assertEqual(recovered, Path(private_path))
        self.assertEqual(errors, (private_error,))
        self.assertNotIn(private_path, rendered)
        self.assertNotIn(private_error, rendered)
        self.assertIn("1 capture issue", rendered)

    def test_salvage_exception_log_hides_private_path_and_traceback(self):
        c = self.controller
        private_error = "failed at /Users/musician/Secret Session/local.part"
        fake_capture = MagicMock()
        fake_capture.stop_into.side_effect = OSError(private_error)
        c.recording._local_capture = fake_capture

        with self.assertLogs("webjam.qt.recording", level="ERROR") as captured:
            recovered, errors = c.recording._salvage_capture()

        rendered = "\n".join(captured.output)
        self.assertIsNone(recovered)
        self.assertEqual(errors, ())
        self.assertNotIn(private_error, rendered)
        self.assertNotIn("/Users/", rendered)
        self.assertNotIn("Traceback", rendered)
        fake_capture.abort.assert_called_once()

    def test_unsafe_storage_blocks_before_the_record_worker_starts(self):
        c = self.controller
        c.settings.server_rpc_secret_file = "/tmp/secret"
        c._jamulus_connected = True
        c.participants = {1: SimpleNamespace(role="Guitar")}
        blocked = SimpleNamespace(
            can_start=False,
            detail="There isn't enough free storage to safely start this take.",
            status="action_needed",
        )
        with (
            patch(
                "webjam_qt.controllers.recording_coordinator.check_recording_storage",
                return_value=blocked,
            ),
            patch(
                "webjam_qt.controllers.recording_coordinator._private_secret_file_identity",
                return_value=(1, 2, 3, 4),
            ),
            patch.object(c, "_record_toggle_worker") as worker,
        ):
            c._on_record_requested()

        worker.assert_not_called()
        self.assertEqual(c.recording.phase.value, "error")
        c._show_actionable_error.assert_called_once()
        args, kwargs = c._show_actionable_error.call_args
        self.assertEqual(args[0], "Recording Storage Needs Attention")
        self.assertIn("No recording was started", kwargs["next_action"])
        self.assertIn("end this session", kwargs["next_action"].lower())
        c._jamulus_connected = False
        c.participants = {}

    def test_record_preflight_requires_confirmed_participant(self):
        c = self.controller
        c.settings.server_rpc_secret_file = "/tmp/secret"
        c._jamulus_connected = False
        c.participants = {}
        with patch.object(c, "_record_toggle_worker") as worker:
            c._on_record_requested()
        worker.assert_not_called()
        self.assertEqual(c.recording.phase.value, "error")
        c._show_actionable_error.assert_called_once()

    def test_local_capture_starts_independently_of_talkback_mode(self):
        from core.session_recording_plan import InputMapBinding

        c = self.controller
        c.settings.webex_audio_mode = "talkback"
        c.settings.local_capture_enabled = True
        c.settings.takes_directory = "/tmp/takes"
        plan = self._install_bound_capture_plan(
            (
                InputMapBinding("host-guitar", 1, True, True),
                InputMapBinding("host-vocal", 1, True, True),
            )
        )
        fake_capture = MagicMock()
        with patch(
            "core.local_capture.LocalInputCapture", return_value=fake_capture
        ) as capture_cls:
            self.assertTrue(c.recording._start_local_capture())
        fake_capture.start.assert_called_once()
        self.assertEqual(
            capture_cls.call_args.kwargs["tracks"], plan.resolved_capture_tracks()
        )
        self.assertIs(c.recording._local_capture, fake_capture)
        c.recording._local_capture = None
        c.settings.local_capture_enabled = False

    def test_opted_out_input_map_never_starts_legacy_capture(self):
        from core.session_recording_plan import InputMapBinding

        c = self.controller
        c.settings.local_capture_enabled = True
        c.settings.takes_directory = "/tmp/takes"
        c.settings.input_maps = [
            {
                "name": "Guide",
                "channels": 2,
                "enabled": True,
                "local_original_enabled": False,
            }
        ]
        self._install_bound_capture_plan((InputMapBinding("Guide", 2, True, False),))

        with patch("core.local_capture.LocalInputCapture") as capture_cls:
            self.assertTrue(c.recording._start_local_capture())

        capture_cls.assert_not_called()
        self.assertIsNone(c.recording._local_capture)
        self.assertEqual(c.recording._local_capture_track_count, 0)
        c.settings.input_maps = []
        c.settings.local_capture_enabled = False

    def test_local_capture_failure_blocks_server_start(self):
        from core.session_recording_plan import InputMapBinding

        c = self.controller
        c.settings.webex_audio_mode = "video_only"
        c.settings.local_capture_enabled = True
        c.settings.takes_directory = "/tmp/takes"
        self._install_bound_capture_plan(
            (
                InputMapBinding("host-guitar", 1, True, True),
                InputMapBinding("host-vocal", 1, True, True),
            )
        )
        private_error = "device busy at /Users/musician/Secret Interface"
        with (
            patch(
                "core.local_capture.LocalInputCapture",
                side_effect=RuntimeError(private_error),
            ),
            self.assertLogs("webjam.qt.recording", level="WARNING") as captured,
        ):
            self.assertFalse(c.recording._start_local_capture())
        self.assertNotIn(private_error, "\n".join(captured.output))
        self.assertEqual(c.recording.phase.value, "error")
        c._show_actionable_error.assert_called_once()
        c.settings.local_capture_enabled = False

    def test_audience_bridge_does_not_implicitly_enable_local_capture(self):
        c = self.controller
        c.settings.webex_audio_mode = "audience_bridge"
        c.settings.local_capture_enabled = False
        with patch("core.local_capture.LocalInputCapture") as capture_cls:
            self.assertTrue(c.recording._start_local_capture())
        capture_cls.assert_not_called()

    def test_shutdown_stop_requires_recorder_acknowledgement(self):
        c = self.controller
        c._server_recording = True
        c._recorder_armed = True
        c.settings.server_rpc_secret_file = "/tmp/server.secret"
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.stop_recording.return_value = False
        with (
            patch("core.jamulus_server_rpc.JamulusServerRpc", return_value=fake_rpc),
            patch("core.jamulus_server_rpc.read_secret_file", return_value="secret"),
        ):
            self.assertFalse(c.recording.stop_server_recording_for_shutdown())
        fake_rpc.get_recorder_status.assert_not_called()
        self.assertTrue(c._server_recording)
        self.assertTrue(c._recorder_armed)
        c._server_recording = False
        c._recorder_armed = False

    def test_shutdown_stop_requires_disabled_recorder_state(self):
        c = self.controller
        c._server_recording = True
        c._recorder_armed = True
        c.settings.server_rpc_secret_file = "/tmp/server.secret"
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.stop_recording.return_value = True
        fake_rpc.get_recorder_status.return_value = {"enabled": True}
        with (
            patch("core.jamulus_server_rpc.JamulusServerRpc", return_value=fake_rpc),
            patch("core.jamulus_server_rpc.read_secret_file", return_value="secret"),
            patch(
                "webjam_qt.controllers.recording_coordinator.time.monotonic",
                side_effect=[10.0, 10.0, 14.0],
            ),
            patch("webjam_qt.controllers.recording_coordinator.time.sleep"),
        ):
            self.assertFalse(c.recording.stop_server_recording_for_shutdown())
        fake_rpc.get_recorder_status.assert_called_once()
        self.assertTrue(c._server_recording)
        self.assertTrue(c._recorder_armed)
        c._server_recording = False
        c._recorder_armed = False

    def test_shutdown_stop_keeps_services_alive_until_normal_validation_retires_take(
        self,
    ):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._reset_session_evidence()
        c._server_recording = True
        c._recorder_armed = True
        c.settings.server_rpc_secret_file = "/tmp/server.secret"
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.stop_recording.return_value = True
        fake_rpc.get_recorder_status.return_value = {"enabled": False}
        callbacks: list[object] = []
        outcomes: list[bool] = []

        with (
            patch("core.jamulus_server_rpc.JamulusServerRpc", return_value=fake_rpc),
            patch("core.jamulus_server_rpc.read_secret_file", return_value="secret"),
            patch.object(
                c.recording,
                "_confirmed_recording_stopped",
                return_value=("2026-08-03T12:00:00Z", True),
            ),
            patch.object(
                c.recording,
                "_signal_peer_recording_finalizing",
            ) as signal_finalizing,
            patch.object(
                c._ui_invoker,
                "invoke",
                side_effect=callbacks.append,
            ),
            patch.object(
                c.window,
                "set_status_recording",
            ) as set_status,
            patch.object(
                c.recording,
                "_begin_take_validation",
            ) as begin_validation,
        ):
            worker = threading.Thread(
                target=lambda: outcomes.append(
                    c.recording.stop_server_recording_for_shutdown()
                )
            )
            worker.start()
            worker.join(timeout=5.0)

            self.assertFalse(worker.is_alive())
            self.assertEqual(outcomes, [False])
            self.assertEqual(len(callbacks), 1)
            set_status.assert_not_called()
            begin_validation.assert_not_called()
            signal_finalizing.assert_called_once_with(
                take_id,
                stopped_utc="2026-08-03T12:00:00Z",
                message="The host is finalizing the recorded take.",
            )
            self.assertFalse(c.recording.stop_server_recording_for_shutdown())

            callbacks[0]()
            set_status.assert_called_once_with(False)
            begin_validation.assert_called_once_with(take_id)
            self.assertEqual(c.recording._validation_take_id, take_id)
            self.assertFalse(c.recording.stop_server_recording_for_shutdown())

            c.recording._retire_active_take(take_id)
            self.assertTrue(c.recording.stop_server_recording_for_shutdown())

        c.recording._take_id = ""
        c.recording._validation_take_id = ""
        c.recording._shutdown_validation_pending_take_id = ""
        c._server_recording = False
        c._recorder_armed = False

    def test_shutdown_validation_exception_keeps_raw_take_and_retries_until_exact_manifest(
        self,
    ):
        import struct
        import wave

        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import ProjectStatus, TakeProject, new_project_id

        c = self.controller
        callbacks: list[object] = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw_take = root / "Jamulus Take"
            raw_take.mkdir()
            with wave.open(str(raw_take / "Band-0-1.wav"), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(struct.pack("<480h", *([400] * 480)))

            take_id = new_project_id()
            c.settings.takes_directory = directory
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            self.assertTrue(c.recording._create_evidence_journal())
            c.recording._shutdown_validation_pending_take_id = take_id
            c.recording._validation_take_id = take_id
            c._server_recording = False
            c._recorder_armed = False

            with (
                patch.object(
                    c.recording,
                    "_build_take_validation",
                    side_effect=OSError("private/raw/path"),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.find_changed_take",
                    return_value=raw_take,
                ),
                patch.object(
                    c.recording,
                    "_salvage_capture",
                    return_value=(None, ()),
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=callbacks.append,
                ),
            ):
                c.recording._validate_take_worker(take_id)

            self.assertEqual(len(callbacks), 1)
            callbacks.pop(0)()
            self.assertEqual(c.recording._take_id, take_id)
            self.assertEqual(c.recording._shutdown_validation_pending_take_id, take_id)
            self.assertEqual(c.recording._validation_take_id, "")
            self.assertIsNotNone(RecordingManifestJournal(directory).load(take_id))
            self.assertFalse((raw_take / "webjam-take.json").exists())
            with patch.object(
                c.recording, "_request_shutdown_take_validation"
            ) as retry_publication:
                c.recording.on_record_requested()
            retry_publication.assert_called_once_with(take_id)
            self.assertEqual(c.recording._take_id, take_id)

            # Retry is idempotent: it queues validation without another
            # recorder RPC because stop was already confirmed.
            with patch.object(
                c._ui_invoker,
                "invoke",
                side_effect=callbacks.append,
            ):
                self.assertFalse(c.recording.stop_server_recording_for_shutdown())
                self.assertFalse(c.recording.stop_server_recording_for_shutdown())
            self.assertEqual(len(callbacks), 1)
            with patch.object(c.recording, "_begin_take_validation") as begin:
                callbacks.pop(0)()
            begin.assert_called_once_with(take_id)
            self.assertEqual(c.recording._validation_take_id, take_id)

            project = TakeProject(
                session_id=new_project_id(),
                take_id=take_id,
                session_title="",
                take_name="Durable shutdown take",
                status=ProjectStatus.NEEDS_ATTENTION,
                project_sample_rate=48_000,
                participants=(),
                tracks=(),
            )
            (raw_take / "webjam-take.json").write_text(
                json.dumps(project.to_dict()), encoding="utf-8"
            )
            c.recording._show_validation_result(
                c.recording.last_validation,
                take_id=take_id,
            )
            self.assertIsNone(RecordingManifestJournal(directory).load(take_id))
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(c.recording._shutdown_validation_pending_take_id, "")
            self.assertTrue(c.recording.stop_server_recording_for_shutdown())

        c.recording._take_id = ""
        c.recording._validation_take_id = ""
        c.recording._shutdown_validation_pending_take_id = ""
        c.recording._shutdown_validation_dispatch_take_id = ""

    def test_shutdown_validation_exception_without_folder_retains_owner_and_journal(
        self,
    ):
        from core.recording_manifest_journal import RecordingManifestJournal
        from core.take_project import new_project_id

        c = self.controller
        callbacks: list[object] = []
        with TemporaryDirectory() as directory:
            take_id = new_project_id()
            c.settings.takes_directory = directory
            c.recording._take_id = take_id
            c.recording._reset_session_evidence()
            self.assertTrue(c.recording._create_evidence_journal())
            c.recording._shutdown_validation_pending_take_id = take_id
            c.recording._validation_take_id = take_id
            c._server_recording = False
            c._recorder_armed = False

            with (
                patch.object(
                    c.recording,
                    "_build_take_validation",
                    side_effect=OSError("private/missing/path"),
                ),
                patch(
                    "webjam_qt.controllers.recording_coordinator.find_changed_take",
                    return_value=None,
                ),
                patch.object(
                    c.recording,
                    "_salvage_capture",
                    return_value=(None, ()),
                ),
                patch.object(
                    c._ui_invoker,
                    "invoke",
                    side_effect=callbacks.append,
                ),
            ):
                c.recording._validate_take_worker(take_id)

            callbacks.pop(0)()
            self.assertIsNone(c.recording.last_validation.take)
            self.assertEqual(c.recording._take_id, take_id)
            self.assertEqual(c.recording._validation_take_id, "")
            self.assertEqual(c.recording._shutdown_validation_pending_take_id, take_id)
            self.assertIsNotNone(RecordingManifestJournal(directory).load(take_id))
            with patch.object(c._ui_invoker, "invoke", side_effect=callbacks.append):
                self.assertFalse(c.recording.stop_server_recording_for_shutdown())
            self.assertEqual(len(callbacks), 1)

        c.recording._take_id = ""
        c.recording._validation_take_id = ""
        c.recording._shutdown_validation_pending_take_id = ""
        c.recording._shutdown_validation_dispatch_take_id = ""

    def test_shutdown_validation_without_configured_takes_root_keeps_teardown_blocked(
        self,
    ):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        c.settings.takes_directory = ""
        c.recording._take_id = take_id
        c.recording._reset_session_evidence()
        c.recording._recording_ended_utc = "2026-08-03T12:03:00Z"
        c.recording._shutdown_validation_pending_take_id = take_id
        c.recording._validation_take_id = take_id
        c._server_recording = False
        c._recorder_armed = False
        callbacks: list[object] = []

        with (
            patch.object(
                c.recording,
                "_salvage_capture",
                return_value=(None, ()),
            ),
            patch.object(c, "signal_peer_recording_stopped") as peer_finished,
        ):
            c.recording._begin_take_validation(take_id)

        self.assertEqual(c.recording._take_id, take_id)
        self.assertEqual(c.recording._shutdown_validation_pending_take_id, take_id)
        self.assertEqual(c.recording._validation_take_id, "")
        peer_finished.assert_called_once_with(
            take_id,
            stopped_utc="2026-08-03T12:03:00Z",
            needs_attention=True,
            message="The take could not be finalized and needs host review.",
        )
        with patch.object(c._ui_invoker, "invoke", side_effect=callbacks.append):
            self.assertFalse(c.recording.stop_server_recording_for_shutdown())
        self.assertEqual(len(callbacks), 1)

        c.recording._take_id = ""
        c.recording._validation_take_id = ""
        c.recording._shutdown_validation_pending_take_id = ""
        c.recording._shutdown_validation_dispatch_take_id = ""

    def test_validation_exception_becomes_needs_attention_instead_of_sticking(self):
        c = self.controller
        c.settings.takes_directory = ""
        with (
            patch.object(
                c.recording,
                "_build_take_validation",
                side_effect=OSError("disk full / private/path"),
            ),
            patch.object(
                c._ui_invoker, "invoke", side_effect=lambda callback: callback()
            ),
        ):
            c.recording._validate_take_worker()
        self.assertEqual(c.recording.phase.value, "needs_attention")
        self.assertIn(
            "No completed take was found",
            self.window.recording_studio._hint.text(),
        )
        self.assertNotIn("private/path", self.window.recording_studio._hint.text())

    def test_worker_success_arms_button_and_flashes(self):
        c = self.controller
        self.window.workspace_stack.setCurrentWidget(self.window.center_splitter)
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_recorder_status.return_value = {"enabled": True}
        with (
            patch("core.jamulus_server_rpc.JamulusServerRpc", return_value=fake_rpc),
            patch("core.jamulus_server_rpc.read_secret_file", return_value="s3cret"),
            patch.object(c._ui_invoker, "invoke", side_effect=lambda fn: fn()),
        ):
            c._record_toggle_worker(True, "/tmp/secret")
        fake_rpc.start_recording.assert_called_once()
        self.assertTrue(c._recorder_armed)
        self.assertEqual(
            self.window.session_strip._record_button.text(), "■ Stop Recording"
        )
        self.assertIs(
            self.window.workspace_stack.currentWidget(),
            self.window.center_splitter,
        )
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("confirmed" in m for m in msgs), msgs)

    def test_worker_captures_authenticated_roster_before_and_after_start(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        participant_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._reset_session_evidence()
        c.participants = {
            4: SimpleNamespace(
                channel_id=4,
                name="Alice",
                role="Guitar",
                participant_id=participant_id,
            )
        }
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_clients.return_value = {
            "connections": 1,
            "clients": [
                {
                    "id": 4,
                    "name": "Alice",
                    "address": "127.0.0.1:50000",
                    "channels": 1,
                }
            ],
        }
        fake_rpc.get_recorder_status.return_value = {"enabled": True}
        try:
            with (
                patch.object(
                    c,
                    "peer_participant_id_for_channel",
                    return_value=participant_id,
                ),
                patch(
                    "core.jamulus_server_rpc.JamulusServerRpc",
                    return_value=fake_rpc,
                ),
                patch(
                    "core.jamulus_server_rpc.read_secret_file",
                    return_value="s3cret",
                ),
                patch.object(c._ui_invoker, "invoke", side_effect=lambda fn: fn()),
            ):
                c._record_toggle_worker(True, "/tmp/secret")

            receipts, errors = c.recording._recording_receipt_snapshot()
            calls = [item[0] for item in fake_rpc.method_calls]
            first_roster = calls.index("get_clients")
            start = calls.index("start_recording")
            confirmed = calls.index("get_recorder_status")
            final_roster = calls.index("get_clients", confirmed + 1)
            self.assertLess(first_roster, start)
            self.assertLess(start, confirmed)
            self.assertLess(confirmed, final_roster)
            self.assertGreaterEqual(fake_rpc.get_clients.call_count, 2)
            self.assertEqual(errors, ())
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].participant_id, participant_id)
            self.assertEqual(receipts[0].source_kind, "musician")
            self.assertNotIn("50000", repr(receipts))
        finally:
            c.recording._take_id = ""
            c.participants = {}

    def test_confirmed_server_times_are_shared_with_peer_and_take_evidence(self):
        from core.take_project import new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._local_participant_id = new_project_id()
        c.settings.musician_name = "Test Host"
        c.recording._reset_session_evidence()
        with (
            patch.object(c, "signal_peer_recording_started") as peer_started,
            patch.object(
                c.recording, "_signal_peer_recording_finalizing"
            ) as peer_finalizing,
            patch.object(c, "signal_peer_recording_stopped") as peer_stopped,
            patch.object(c.recording, "_begin_take_validation"),
        ):
            c.recording.apply_toggle_result(True)
            start_evidence = c.recording._current_session_evidence()
            c.recording.apply_toggle_result(False)
        evidence = c.recording._current_session_evidence()

        self.assertTrue(start_evidence.started_utc)
        self.assertTrue(evidence.ended_utc)
        self.assertEqual(evidence.host.display_name, "Test Host")
        self.assertIn("jamulus-3.12.2", evidence.protocol_version)
        self.assertEqual(
            peer_started.call_args.kwargs["started_utc"], evidence.started_utc
        )
        self.assertEqual(
            peer_finalizing.call_args.kwargs["stopped_utc"], evidence.ended_utc
        )
        peer_stopped.assert_not_called()
        self.assertEqual(
            [item.event for item in evidence.timeline],
            ["recording_requested", "recording_started", "recording_stopped"],
        )

    def test_recording_evidence_redacts_lifecycle_detail_and_marks_failed_recovery(
        self,
    ):
        from core.take_project import RecoveryStatus, new_project_id

        c = self.controller
        c.recording._take_id = new_project_id()
        c.recording._local_participant_id = new_project_id()
        c.recording._reset_session_evidence()
        with patch.object(c, "signal_peer_recording_started"):
            c.recording.apply_toggle_result(True)
        c.recording.record_lifecycle_event(
            "reconnecting",
            reason=(
                "Retry webjam://join?token=private-token at 192.168.10.9 "
                "with Bearer private-secret"
            ),
            recovery_attempt=2,
        )
        c.recording.record_lifecycle_event(
            "connected", reason="The connection returned."
        )
        recovered = c.recording._current_session_evidence()
        rendered = " ".join(item.detail for item in recovered.timeline)
        self.assertEqual(recovered.recovery_status, RecoveryStatus.RECOVERED)
        self.assertNotIn("webjam://", rendered)
        self.assertNotIn("192.168.10.9", rendered)
        self.assertNotIn("private-secret", rendered)

        c.recording.record_lifecycle_event(
            "failed_recoverable", reason="The reconnect did not finish."
        )
        self.assertEqual(
            c.recording._current_session_evidence().recovery_status,
            RecoveryStatus.NEEDS_ATTENTION,
        )

    def test_lifecycle_transition_forwards_redacted_event_to_active_recorder(self):
        from core.session_lifecycle import SessionLifecycle, SessionLifecyclePhase

        c = self.controller
        c.session_lifecycle = SessionLifecycle(role="host")
        with patch.object(c.recording, "record_lifecycle_event") as record_event:
            self.assertTrue(
                c._transition_lifecycle(
                    SessionLifecyclePhase.PREPARING,
                    "Preparing webjam://join?token=private-token",
                )
            )

        record_event.assert_called_once()
        self.assertEqual(
            record_event.call_args.args[0], SessionLifecyclePhase.PREPARING
        )
        self.assertNotIn("webjam://", record_event.call_args.kwargs["reason"])
        self.assertEqual(record_event.call_args.kwargs["recovery_attempt"], 0)

    def test_worker_failure_offers_actionable_retry(self):
        c = self.controller
        with (
            patch(
                "core.jamulus_server_rpc.read_secret_file",
                side_effect=ServerRpcError("Is the SSH tunnel up?"),
            ),
            patch.object(c._ui_invoker, "invoke", side_effect=lambda fn: fn()),
        ):
            c._record_toggle_worker(True, "/tmp/secret")
        self.assertFalse(c._recorder_armed)
        self.assertEqual(
            self.window.session_strip._record_button.text(), "Retry Record"
        )
        c._show_actionable_error.assert_called_once()
        args, kwargs = c._show_actionable_error.call_args
        self.assertEqual(args[0], "Recording Could Not Start")
        self.assertEqual(
            kwargs["what_failed"],
            "WebJam couldn't confirm that recording started.",
        )
        self.assertNotIn("SSH tunnel", kwargs["what_failed"])
        self.assertEqual(kwargs["retry_callback"], c.recording.on_record_requested)

    def test_stop_failure_stays_visibly_recording_and_retries_stop(self):
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        c._recorder_armed = True
        c._server_recording = True
        c.recording.phase = RecorderPhase.STOPPING
        self.window.session_strip.set_recording_phase("recording")
        c.recording.apply_toggle_failure("Recorder did not answer")

        self.assertEqual(c.recording.phase, RecorderPhase.STOP_FAILED)
        self.assertEqual(
            self.window.session_strip._record_button.text(), "■ Finish Stop"
        )
        self.assertTrue(self.window.session_strip._record_clock.isActive())
        self.assertIn("CLEANUP PENDING", self.window.recording_studio._phase.text())
        _args, kwargs = c._show_actionable_error.call_args
        self.assertEqual(kwargs["retry_callback"], c.recording.on_record_requested)

        c._recorder_armed = False
        c._server_recording = False
        c.recording.phase = RecorderPhase.IDLE
        self.window.session_strip.set_recording_phase("idle")

    def test_authoritative_stop_during_stopping_validates_once_despite_late_rpc_failure(
        self,
    ):
        """A real server stop wins over a later lost stop-RPC reply."""
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._reset_session_evidence()
        c._recorder_armed = True
        c._server_recording = True
        c.recording.phase = RecorderPhase.STOPPING
        with patch.object(c.recording, "_begin_take_validation") as begin:
            c.recording.on_server_state(False)
            c.recording.apply_toggle_failure("late stop timeout", take_id=take_id)
            c.recording.apply_toggle_result(False, take_id=take_id)

        begin.assert_called_once_with(take_id)
        self.assertFalse(c._recorder_armed)
        self.assertFalse(c._server_recording)
        self.assertEqual(c.recording.phase, RecorderPhase.VALIDATING)
        c._show_actionable_error.assert_not_called()

    def test_authoritative_stop_after_stop_failure_starts_validation_once(self):
        """A delayed false notification must not leave STOP_FAILED stuck."""
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._reset_session_evidence()
        c._recorder_armed = True
        c._server_recording = True
        c.recording.phase = RecorderPhase.STOP_FAILED
        with (
            patch.object(
                c.recording, "_signal_peer_recording_finalizing"
            ) as peer_finalizing,
            patch.object(c, "signal_peer_recording_stopped") as peer_finished,
            patch.object(c.recording, "_begin_take_validation") as begin,
        ):
            c.recording.on_server_state(False)
            c.recording.apply_toggle_result(False, take_id=take_id)

        begin.assert_called_once_with(take_id)
        peer_finalizing.assert_called_once()
        self.assertEqual(peer_finalizing.call_args.args, (take_id,))
        self.assertIn(
            "stopped unexpectedly",
            peer_finalizing.call_args.kwargs["message"],
        )
        peer_finished.assert_not_called()
        self.assertEqual(c.recording.phase, RecorderPhase.VALIDATING)

    def test_unexpected_stop_cannot_publish_a_complete_take(self):
        from core.take_library import TakeInfo, TakeValidationResult
        from core.take_project import RecoveryStatus, new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._validation_take_id = take_id
        c.recording._reset_session_evidence()
        c.recording._confirmed_recording_started()
        c.recording._confirmed_recording_stopped(
            unexpected=True,
            detail="The band server stopped before WebJam requested it.",
        )
        result = TakeValidationResult(
            TakeInfo(
                path=Path("/tmp/webjam-unexpected-stop"),
                name="Unexpected stop",
                validation_status="complete",
                take_id=take_id,
            )
        )

        with (
            patch.object(c.window.recording_studio, "on_take_completed"),
            patch.object(c, "signal_peer_recording_stopped") as peer_finished,
        ):
            c.recording._show_validation_result(result, take_id=take_id)

        self.assertEqual(
            c.recording._current_session_evidence().recovery_status,
            RecoveryStatus.NEEDS_ATTENTION,
        )
        self.assertEqual(c.recording.phase, RecorderPhase.NEEDS_ATTENTION)
        self.assertTrue(peer_finished.call_args.kwargs["needs_attention"])

    def test_late_worker_callback_stays_bound_to_its_originating_take(self):
        """A retired worker must not fall back to mutating the next take."""
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import (
            RecorderPhase,
            _ToggleAttempt,
        )

        c = self.controller
        retired_take_id = new_project_id()
        current_take_id = new_project_id()
        c.recording._take_id = current_take_id
        c.recording.phase = RecorderPhase.RECORDING
        c._recorder_armed = True
        c._server_recording = True
        attempt = _ToggleAttempt(
            retired_take_id,
            target_armed=False,
            server_rpc_port=22_122,
            server_rpc_secret_file="/tmp/secret",
            server_rpc_secret_identity=(1, 2, 3, 4),
        )
        with (
            patch("core.jamulus_server_rpc.JamulusServerRpc") as rpc_factory,
            patch.object(
                c._ui_invoker, "invoke", side_effect=lambda callback: callback()
            ),
        ):
            c.recording._run_toggle_attempt(attempt, "/tmp/secret")

        rpc_factory.assert_not_called()
        self.assertEqual(c.recording.phase, RecorderPhase.RECORDING)
        self.assertTrue(c._recorder_armed)
        c._show_actionable_error.assert_not_called()

    def test_validation_retires_take_before_unrelated_recorder_notifications(self):
        """A later manual recorder cycle cannot reuse completed-take ownership."""
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._validation_take_id = take_id
        c.recording._recording_ended_utc = "2026-08-03T12:01:00Z"
        result = SimpleNamespace(
            ok=True,
            take=SimpleNamespace(path=Path("/tmp/webjam-completed-take")),
            errors=(),
            warnings=(),
            summary="1 track · 0:01 · 48 kHz",
        )
        with (
            patch.object(c.window.recording_studio, "on_take_completed"),
            patch.object(c, "signal_peer_recording_stopped") as peer_finished,
            patch.object(c.recording, "_begin_take_validation") as begin,
        ):
            c.recording._show_validation_result(result, take_id=take_id)
            self.assertEqual(c.recording._take_id, "")
            self.assertEqual(c.recording._validation_take_id, "")
            peer_finished.assert_called_once_with(
                take_id,
                stopped_utc="2026-08-03T12:01:00Z",
                needs_attention=False,
                message="The take is finalized and ready.",
            )
            c.recording.on_server_state(True)
            c.recording.on_server_state(False)
            c.recording.apply_toggle_result(True, take_id=take_id)

        begin.assert_not_called()
        self.assertEqual(c.recording.phase.value, "idle")
        self.assertFalse(c._recorder_armed)

    def test_validation_failure_publishes_peer_needs_attention_before_retire(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._validation_take_id = take_id
        c.recording._recording_ended_utc = "2026-08-03T12:02:00Z"
        result = SimpleNamespace(
            ok=False,
            take=None,
            errors=("A required recorder track was missing.",),
            warnings=(),
            summary="",
        )

        with (
            patch.object(c.window.recording_studio, "on_take_completed"),
            patch.object(c, "signal_peer_recording_stopped") as peer_finished,
        ):
            c.recording._show_validation_result(result, take_id=take_id)

        peer_finished.assert_called_once_with(
            take_id,
            stopped_utc="2026-08-03T12:02:00Z",
            needs_attention=True,
            message="The take needs host review before it is ready.",
        )
        self.assertEqual(c.recording.phase.value, "needs_attention")
        self.assertEqual(c.recording._take_id, "")
        self.assertEqual(c.recording._validation_take_id, "")

    def test_durable_take_is_registered_before_peer_ready_is_published(self):
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        c.recording._take_id = take_id
        c.recording._validation_take_id = take_id
        c.recording._recording_ended_utc = "2026-08-03T12:04:00Z"
        take_path = Path("/tmp/webjam-durable-take")
        result = SimpleNamespace(
            ok=True,
            take=SimpleNamespace(path=take_path),
            errors=(),
            warnings=(),
            summary="1 track · 0:01 · 48 kHz",
        )
        events: list[str] = []
        prior_host_peer = c.host_peer
        c.host_peer = SimpleNamespace(
            active=True,
            register_take=lambda registered_id, registered_path: events.append(
                f"register:{registered_id}:{registered_path.name}"
            ),
        )
        try:
            with (
                patch.object(c.window.recording_studio, "on_take_completed"),
                patch.object(
                    c,
                    "signal_peer_recording_stopped",
                    side_effect=lambda *_args, **_kwargs: events.append("ready"),
                ),
            ):
                c.recording._show_validation_result(result, take_id=take_id)
        finally:
            c.host_peer = prior_host_peer

        self.assertEqual(
            events,
            [f"register:{take_id}:webjam-durable-take", "ready"],
        )

    def test_validation_waits_and_reconciles_initial_peer_inventory_before_complete(
        self,
    ):
        from core.take_library import TakeInfo, TakeValidationResult
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        events: list[str] = []
        with TemporaryDirectory() as directory:
            take_path = Path(directory)
            manifest_path = take_path / "webjam-take.json"
            manifest_path.write_text(
                json.dumps({"schema_version": 2, "take_id": take_id}),
                encoding="utf-8",
            )
            initial = TakeInfo(
                path=take_path,
                name="Initial peer inventory",
                validation_status="complete",
                manifest_path=manifest_path,
                take_id=take_id,
            )
            refreshed = TakeInfo(
                path=take_path,
                name="Initial peer inventory",
                validation_status="complete",
                manifest_path=manifest_path,
                take_id=take_id,
            )

            class _Peer:
                active = True

                @staticmethod
                def wait_for_initial_take_inventory(
                    waited_take_id: str, *, timeout_s: float
                ) -> bool:
                    self.assertEqual(waited_take_id, take_id)
                    self.assertGreater(timeout_s, 0.0)
                    events.append("wait")
                    return True

                @staticmethod
                def register_take(registered_take_id: str, path: Path) -> None:
                    self.assertEqual((registered_take_id, path), (take_id, take_path))
                    events.append("register")

                @staticmethod
                def reconcile_take(reconciled_take_id: str, path: Path) -> bool:
                    self.assertEqual((reconciled_take_id, path), (take_id, take_path))
                    events.append("reconcile")
                    manifest_path.write_text(
                        json.dumps(
                            {
                                "schema_version": 2,
                                "take_id": take_id,
                                "peer_transfers": {"status": "complete"},
                            }
                        ),
                        encoding="utf-8",
                    )
                    return True

            prior_host_peer = c.host_peer
            c.host_peer = _Peer()
            c.recording._take_id = take_id
            c.recording._validation_take_id = take_id
            c.recording._recording_ended_utc = "2026-08-03T12:06:00Z"
            c.recording.phase = RecorderPhase.VALIDATING
            try:
                with (
                    patch.object(
                        c.recording,
                        "_build_take_validation",
                        return_value=TakeValidationResult(initial),
                    ),
                    patch(
                        "webjam_qt.controllers.recording_coordinator.load_take",
                        return_value=refreshed,
                    ),
                    patch.object(
                        c._ui_invoker,
                        "invoke",
                        side_effect=lambda callback: callback(),
                    ),
                    patch.object(
                        c.window.recording_studio,
                        "on_take_completed",
                    ),
                    patch.object(
                        c,
                        "signal_peer_recording_stopped",
                        side_effect=lambda *_args, **_kwargs: events.append("complete"),
                    ),
                ):
                    c.recording._validate_take_worker(take_id)
            finally:
                c.host_peer = prior_host_peer
                c.recording._take_id = ""
                c.recording._validation_take_id = ""

        self.assertEqual(events, ["wait", "register", "reconcile", "complete"])
        self.assertEqual(c.recording._initial_peer_inventory_take_id, take_id)

    def test_late_peer_reconciliation_republishes_terminal_truth(self):
        from core.take_library import TakeInfo, TakeValidationResult
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        take_path = Path("/tmp/webjam-late-peer-reconciliation")
        transfer_error = "Peer transfer: Alex's local original has not arrived."
        prior = TakeInfo(
            path=take_path,
            name="Late peer reconciliation",
            validation_status="needs_attention",
            manifest_errors=(transfer_error,),
            take_id=take_id,
        )
        refreshed = TakeInfo(
            path=take_path,
            name="Late peer reconciliation",
            validation_status="complete",
            take_id=take_id,
        )
        previous_validation = c.recording.last_validation
        c.recording.last_validation = TakeValidationResult(
            prior,
            (transfer_error,),
        )
        c.recording.phase = RecorderPhase.NEEDS_ATTENTION
        try:
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.load_take",
                    return_value=refreshed,
                ),
                patch.object(
                    c,
                    "signal_peer_recording_stopped",
                ) as peer_finished,
            ):
                c.recording.on_peer_take_reconciled(take_id, take_path)
                # A later semantic manifest update with the same terminal state
                # is still republished; guests must not depend on a phase edge.
                c.recording.on_peer_take_reconciled(take_id, take_path)

            self.assertEqual(c.recording.phase, RecorderPhase.COMPLETE)
            self.assertEqual(peer_finished.call_count, 2)
            self.assertTrue(
                all(
                    call.kwargs["needs_attention"] is False
                    for call in peer_finished.call_args_list
                )
            )
        finally:
            c.recording.last_validation = previous_validation
            c.recording.phase = RecorderPhase.IDLE

    def test_peer_reconciliation_racing_validation_is_reloaded_before_complete(self):
        from core.take_library import TakeInfo, TakeValidationResult
        from core.take_project import new_project_id
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        take_id = new_project_id()
        take_path = Path("/tmp/webjam-validation-race")
        transfer_error = "Peer isolated recording: Local Original is still arriving."
        stale = TakeInfo(
            path=take_path,
            name="Stale validation",
            validation_status="needs_attention",
            manifest_errors=(transfer_error,),
            take_id=take_id,
        )
        refreshed = TakeInfo(
            path=take_path,
            name="Refreshed validation",
            validation_status="complete",
            take_id=take_id,
        )
        previous_validation = c.recording.last_validation
        c.recording._take_id = take_id
        c.recording._validation_take_id = take_id
        c.recording._recording_ended_utc = "2026-08-09T12:01:00Z"
        c.recording._initial_peer_inventory_take_id = take_id
        c.recording.phase = RecorderPhase.VALIDATING
        try:
            with (
                patch(
                    "webjam_qt.controllers.recording_coordinator.load_take",
                    return_value=refreshed,
                ) as reload_take,
                patch.object(
                    c.window.recording_studio,
                    "on_take_completed",
                ) as studio_complete,
                patch.object(
                    c,
                    "signal_peer_recording_stopped",
                ) as peer_finished,
            ):
                # The manifest commit callback reaches the UI before the queued
                # worker result. It must be latched, not discarded.
                c.recording.on_peer_take_reconciled(take_id, take_path)
                reload_take.assert_not_called()
                c.recording._show_validation_result(
                    TakeValidationResult(stale, (transfer_error,)),
                    take_id=take_id,
                )

            reload_take.assert_called_once_with(take_path)
            self.assertIs(c.recording.last_validation.take, refreshed)
            self.assertEqual(c.recording.phase, RecorderPhase.COMPLETE)
            self.assertFalse(peer_finished.call_args.kwargs["needs_attention"])
            self.assertIs(studio_complete.call_args.args[1].take, refreshed)
        finally:
            c.recording.last_validation = previous_validation
            c.recording._retire_active_take(take_id)
            c.recording.phase = RecorderPhase.IDLE

    def test_initial_peer_inventory_failure_uses_recoverable_error_class(self):
        from core.session_transfer_runtime import PEER_TRANSFER_ERROR_PREFIX
        from core.take_library import TakeInfo, TakeValidationResult
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        c.recording._begin_recording_diagnostics(take_id)
        with TemporaryDirectory() as directory:
            take_path = Path(directory)
            (take_path / "webjam-take.json").write_text(
                json.dumps({"schema_version": 2, "take_id": take_id}),
                encoding="utf-8",
            )
            loaded = TakeInfo(
                path=take_path,
                name="Peer inventory failure",
                validation_status="needs_attention",
                take_id=take_id,
            )
            peer = SimpleNamespace(
                active=True,
                wait_for_initial_take_inventory=MagicMock(return_value=False),
                register_take=MagicMock(),
                reconcile_take=MagicMock(return_value=False),
            )
            prior_host_peer = c.host_peer
            c.host_peer = peer
            try:
                with (
                    patch.object(
                        c.recording,
                        "_publish_take_attention",
                        return_value=True,
                    ) as publish_attention,
                    patch(
                        "webjam_qt.controllers.recording_coordinator.load_take",
                        return_value=loaded,
                    ),
                ):
                    result = c.recording._reconcile_initial_peer_inventory(
                        TakeValidationResult(loaded),
                        take_id=take_id,
                    )
            finally:
                c.host_peer = prior_host_peer

        attention_message = publish_attention.call_args.args[1]
        self.assertTrue(attention_message.startswith(PEER_TRANSFER_ERROR_PREFIX))
        self.assertIn(attention_message, result.errors)
        self.assertEqual(
            c.recording.public_diagnostics()["failure_reason_code"],
            "peer_inventory",
        )
        c.recording._retire_active_take(take_id)

    def test_guest_state_stays_finalizing_until_validation_is_ready(self):
        from core.session_transfer import RecordingSignal, SessionControlState
        from core.take_project import new_project_id

        c = self.controller
        take_id = new_project_id()
        with TemporaryDirectory() as directory:
            control = SessionControlState(directory, new_project_id())
            control.begin(take_id, started_utc="2026-08-03T12:00:00Z")
            prior_host_peer = c.host_peer
            c.host_peer = SimpleNamespace(
                active=True,
                begin_take_finalization=control.begin_finalizing,
                finish_take=control.finish,
                register_take=MagicMock(),
            )
            c.recording._take_id = take_id
            c.recording._validation_take_id = take_id
            c.recording._recording_ended_utc = "2026-08-03T12:05:00Z"
            result = SimpleNamespace(
                ok=True,
                take=SimpleNamespace(path=Path(directory)),
                errors=(),
                warnings=(),
                summary="1 track · 0:01 · 48 kHz",
            )
            try:
                c.recording._signal_peer_recording_finalizing(
                    take_id,
                    stopped_utc="2026-08-03T12:05:00Z",
                    message="The host is finalizing the recorded take.",
                )
                self.assertEqual(
                    control.snapshot().signal,
                    RecordingSignal.FINALIZING,
                )

                with patch.object(c.window.recording_studio, "on_take_completed"):
                    c.recording._show_validation_result(result, take_id=take_id)

                self.assertEqual(
                    control.snapshot().signal,
                    RecordingSignal.COMPLETE,
                )
            finally:
                c.host_peer = prior_host_peer
                c.recording._take_id = ""
                c.recording._validation_take_id = ""

    def test_ambiguous_start_failure_preserves_capture_and_retries_stop(self):
        """A lost start reply must not delete audio or send another start."""
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        capture = MagicMock()
        c.recording._local_capture = capture
        c.recording.phase = RecorderPhase.STARTING
        c.recording.apply_toggle_failure("Recorder confirmation timed out")

        self.assertTrue(c._recorder_armed)
        self.assertIs(c.recording._local_capture, capture)
        capture.abort.assert_not_called()
        capture.stop_into.assert_not_called()
        self.assertEqual(c.recording.phase, RecorderPhase.STOP_FAILED)
        self.assertEqual(
            self.window.session_strip._record_button.text(), "■ Finish Stop"
        )
        _args, kwargs = c._show_actionable_error.call_args
        self.assertIn("may still be recording", kwargs["next_action"])

        c.recording._local_capture = None
        c._recorder_armed = False
        c._server_recording = False
        c.recording.phase = RecorderPhase.IDLE
        self.window.session_strip.set_recording_phase("idle")

    def test_shutdown_salvages_active_capture_into_recovery_folder(self):
        """Quitting mid-recording must preserve the stems, never abort them."""
        import tempfile
        from pathlib import Path

        c = self.controller
        with tempfile.TemporaryDirectory() as d:
            c.settings.takes_directory = d
            fake_capture = MagicMock()
            fake_capture.stop_into.return_value = SimpleNamespace(errors=())
            c.recording._local_capture = fake_capture
            c.recording.salvage_on_shutdown()
            fake_capture.stop_into.assert_called_once()
            dest = Path(fake_capture.stop_into.call_args.args[0])
            self.assertEqual(dest.parent, Path(d))
            self.assertTrue(dest.name.startswith("Recovered-"))
            fake_capture.abort.assert_not_called()
            self.assertIsNone(c.recording._local_capture)
            # Idempotent: a second salvage (closeEvent + app.py both call
            # shutdown) finds nothing to do.
            c.recording.salvage_on_shutdown()
            fake_capture.stop_into.assert_called_once()

    def test_take_local_capture_hand_off_is_atomic(self):
        """Validation worker and shutdown race for the capture; exactly one
        side may finalize it."""
        c = self.controller
        for _ in range(50):
            c.recording._local_capture = object()
            barrier = threading.Barrier(2)
            claimed = []

            def worker():
                barrier.wait()
                claimed.append(c.recording._take_local_capture())

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            winners = [item for item in claimed if item is not None]
            self.assertEqual(len(winners), 1)
        self.assertIsNone(c.recording._local_capture)

    def test_confirm_quit_idle_skips_dialog(self):
        c = self.controller
        with patch("webjam_qt.controllers.recording_coordinator.QMessageBox") as mbox:
            self.assertTrue(c.recording.confirm_quit())
        mbox.assert_not_called()

    def test_confirm_quit_while_recording_defaults_to_cancel(self):
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        c.recording.phase = RecorderPhase.RECORDING
        with patch("webjam_qt.controllers.recording_coordinator.QMessageBox") as mbox:
            box = mbox.return_value
            box.clickedButton.return_value = object()  # anything but Quit
            self.assertFalse(c.recording.confirm_quit())
            box.exec.assert_called_once()
            box.setDefaultButton.assert_called_once()
            # Quit clicked → the close proceeds.
            box.clickedButton.return_value = box.addButton.return_value
            self.assertTrue(c.recording.confirm_quit())
        self.assertIn("keeps recording", mbox.return_value.setText.call_args.args[0])

    def test_confirm_close_is_wired_to_role_aware_close_guard(self):
        self.assertEqual(self.window.confirm_close, self.controller._confirm_close)

    def test_stop_audio_salvages_inflight_capture_and_resets_phase(self):
        import tempfile
        from pathlib import Path
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        with tempfile.TemporaryDirectory() as d:
            c.settings.takes_directory = d
            fake_capture = MagicMock()
            fake_capture.stop_into.return_value = SimpleNamespace(errors=())
            c.recording._local_capture = fake_capture
            c.recording.phase = RecorderPhase.RECORDING
            self.window.session_strip.set_recording_phase("recording")
            c._recorder_armed = True
            c._server_recording = True
            c.bridge.stop_jamulus = MagicMock()
            with patch(
                "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                c.audio.stop()
            for _ in range(100):
                QApplication.processEvents()
                if fake_capture.stop_into.called:
                    break
                time.sleep(0.01)
            fake_capture.stop_into.assert_called_once()
            dest = Path(fake_capture.stop_into.call_args.args[0])
            self.assertEqual(dest.parent, Path(d))
            self.assertTrue(dest.name.startswith("Recovered-"))
            self.assertEqual(c.recording.phase.value, "idle")
            self.assertFalse(c._recorder_armed)
            self.assertFalse(c._server_recording)
            strip = self.window.session_strip
            self.assertFalse(strip._record_elapsed.isHidden())
            self.assertEqual(strip._record_elapsed.text(), "RECORDING STOPPED")
            self.assertEqual(strip._record_button.text(), "● Record Session")
            msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
            self.assertTrue(any("tracks were saved" in m for m in msgs), msgs)

    def test_stop_audio_during_validation_does_not_steal_capture(self):
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        fake_capture = MagicMock()
        c.recording._local_capture = fake_capture
        c.recording.phase = RecorderPhase.VALIDATING
        c.bridge.stop_jamulus = MagicMock()
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            c.audio.stop()
        fake_capture.stop_into.assert_not_called()
        fake_capture.abort.assert_not_called()
        self.assertIs(c.recording._local_capture, fake_capture)
        self.assertEqual(c.recording.phase.value, "finalizing")

    def test_stop_audio_clears_stale_take_verified_chip(self):
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        c.recording.phase = RecorderPhase.COMPLETE
        self.window.session_strip.set_recording_phase("complete")
        self.assertEqual(
            self.window.session_strip._record_elapsed.text(), "READY · TAKE SAVED"
        )
        c.bridge.stop_jamulus = MagicMock()
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            c.audio.stop()
        for _ in range(100):
            QApplication.processEvents()
            if c.recording.phase is RecorderPhase.IDLE:
                break
            time.sleep(0.01)
        self.assertEqual(c.recording.phase.value, "idle")
        self.assertTrue(self.window.session_strip._record_elapsed.isHidden())

    def test_no_takes_dir_recovery_uses_recovered_prefix(self):
        from pathlib import Path

        c = self.controller
        fake_capture = MagicMock()
        fake_capture.stop_into.return_value = SimpleNamespace(errors=())
        c.recording._local_capture = fake_capture
        c.settings.takes_directory = ""
        try:
            c.recording._begin_take_validation()
            fake_capture.stop_into.assert_called_once()
            dest = Path(fake_capture.stop_into.call_args.args[0])
            self.assertTrue(dest.name.startswith("Recovered-"))
            self.assertEqual(dest.parent.name, "WebJam Recovered Takes")
            self.assertEqual(c.recording.phase.value, "needs_attention")
        finally:
            if c.recording._recovery_box is not None:
                c.recording._recovery_box.close()
                c.recording._recovery_box = None

    def test_outside_takes_dir_recovery_opens_persistent_box(self):
        c = self.controller
        fake_capture = MagicMock()
        fake_capture.stop_into.return_value = SimpleNamespace(errors=())
        c.recording._local_capture = fake_capture
        c.settings.takes_directory = ""
        try:
            c.recording._begin_take_validation()
            box = c.recording._recovery_box
            self.assertIsNotNone(box)
            self.assertTrue(box.isVisible())  # open(), not exec(): non-blocking
            self.assertIn("outside your Takes folder", box.text())
            self.assertIn("Studio", box.text())
            labels = [button.text() for button in box.buttons()]
            self.assertIn("Reveal in Finder", labels)
        finally:
            if c.recording._recovery_box is not None:
                c.recording._recovery_box.close()
                c.recording._recovery_box = None

    def test_recovery_box_hides_capture_errors_and_absolute_path(self):
        c = self.controller
        private_path = "/Users/jeff/private/takes/Recovered-01"
        secret = "Bearer capture-secret-123"
        try:
            with self.assertLogs("webjam.qt.recording", level="WARNING") as captured:
                c.recording._notify_recovered(
                    Path(private_path),
                    (f"capture failed at {private_path}: {secret}",),
                )
            box = c.recording._recovery_box
            self.assertIsNotNone(box)
            rendered = box.text()
            self.assertIn("Reveal in Finder", rendered)
            self.assertIn("need review", rendered)
            self.assertNotIn(private_path, rendered)
            self.assertNotIn(secret, rendered)
            self.assertNotIn("capture failed", rendered)
            log_text = "\n".join(captured.output)
            self.assertNotIn(private_path, log_text)
            self.assertNotIn(secret, log_text)
            self.assertNotIn("capture failed", log_text)
        finally:
            if c.recording._recovery_box is not None:
                c.recording._recovery_box.close()
                c.recording._recovery_box = None

    def test_completion_copy_failure_hides_findings_and_path(self):
        c = self.controller
        private_path = "/Users/jeff/private/takes/Take01"
        secret = "Bearer validation-secret-456"
        result = SimpleNamespace(
            ok=False,
            take=SimpleNamespace(path=private_path),
            errors=(f"Expected tracks at {private_path}; {secret}",),
            warnings=(f"private.wav appears silent: {secret}",),
            summary="",
        )
        title, body = c.recording._completion_text(result)
        self.assertEqual(title, "WebJam — Take needs attention")
        self.assertIn("preserved", body)
        self.assertIn("Studio", body)
        self.assertIn("Reveal in Finder", body)
        self.assertNotIn(private_path, body)
        self.assertNotIn(secret, body)
        self.assertNotIn("Expected tracks", body)
        self.assertNotIn("private.wav", body)

    def test_validation_flash_hides_raw_findings(self):
        c = self.controller
        private_path = Path("/Users/jeff/private/takes/Take02")
        secret = "rpc-secret-789"
        result = SimpleNamespace(
            ok=False,
            take=SimpleNamespace(path=private_path),
            errors=(f"failed at {private_path}: {secret}",),
            warnings=(f"warning at {private_path}",),
            summary="",
        )
        c.recording._take_id = ""
        with (
            patch.object(c.window.recording_studio, "on_take_completed"),
            self.assertLogs("webjam.qt.recording", level="WARNING") as captured,
        ):
            c.recording._show_validation_result(result)
        self.assertIs(c.recording.last_validation, result)
        rendered = c.window.flash_message.call_args.args[0]
        self.assertIn("needs review", rendered)
        self.assertNotIn(str(private_path), rendered)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("failed at", rendered)
        log_text = "\n".join(captured.output)
        self.assertNotIn(str(private_path), log_text)
        self.assertNotIn(secret, log_text)
        self.assertNotIn("failed at", log_text)

    def test_completion_copy_no_take_points_to_band_check(self):
        c = self.controller
        result = SimpleNamespace(
            ok=False,
            take=None,
            errors=("No new Jamulus take folder appeared after recording stopped.",),
            warnings=(),
            summary="",
        )
        title, body = c.recording._completion_text(result)
        self.assertEqual(title, "WebJam — Take needs attention")
        self.assertIn("Band Check", body)
        self.assertNotIn("Saved to:", body)

    def test_completion_copy_success_unchanged(self):
        c = self.controller
        result = SimpleNamespace(
            ok=True,
            take=SimpleNamespace(path="/takes/Take01"),
            errors=(),
            warnings=("secret warning at /Users/jeff/private.wav",),
            summary="2 tracks · 1:04 · 48 kHz",
        )
        title, body = c.recording._completion_text(result)
        self.assertEqual(title, "WebJam — Recording complete")
        self.assertIn("Take saved · 2 tracks · 1:04 · 48 kHz", body)
        self.assertIn("something to review", body)
        self.assertNotIn("secret warning", body)
        self.assertNotIn("/Users/jeff", body)

    def test_validation_worker_posts_staged_progress(self):
        import tempfile
        from pathlib import Path

        c = self.controller
        strip = self.window.session_strip
        details: list[str] = []
        original = strip.set_recording_phase

        def spy(phase, detail=""):
            if phase in {"finalizing", "validating"} and detail:
                details.append(detail)
            original(phase, detail)

        with tempfile.TemporaryDirectory() as d:
            c.settings.takes_directory = d
            fake_result = SimpleNamespace(
                ok=True,
                take=SimpleNamespace(path=Path(d) / "Take01"),
                errors=(),
                warnings=(),
                summary="1 track",
            )
            try:
                with (
                    patch.object(strip, "set_recording_phase", side_effect=spy),
                    patch(
                        "webjam_qt.controllers.recording_coordinator.find_changed_take",
                        return_value=Path(d) / "Take01",
                    ),
                    patch(
                        "webjam_qt.controllers.recording_coordinator."
                        "wait_for_take_files_stable",
                        return_value=True,
                    ),
                    patch(
                        "webjam_qt.controllers.recording_coordinator."
                        "write_take_manifest",
                        return_value=fake_result,
                    ),
                    patch.object(c._ui_invoker, "invoke", side_effect=lambda fn: fn()),
                ):
                    c.recording._validate_take_worker()
            finally:
                if c.recording._completion_box is not None:
                    c.recording._completion_box.close()
                    c.recording._completion_box = None
        self.assertEqual(
            details,
            ["WAITING FOR SERVER FILES…", "CHECKING TRACKS…", "ALIGNING HOST TRACKS…"],
        )

    def test_worker_stop_path(self):
        c = self.controller
        c._recorder_armed = True
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_recorder_status.return_value = {"enabled": False}
        with (
            patch("core.jamulus_server_rpc.JamulusServerRpc", return_value=fake_rpc),
            patch("core.jamulus_server_rpc.read_secret_file", return_value="s3cret"),
            patch.object(c._ui_invoker, "invoke", side_effect=lambda fn: fn()),
        ):
            c._record_toggle_worker(False, "/tmp/secret")
        fake_rpc.stop_recording.assert_called_once()
        self.assertFalse(c._recorder_armed)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("stopped" in m for m in msgs), msgs)


class _Immediate:
    def __init__(self, *positional, **kwargs):
        self._target = kwargs.get("target")
        self._args = kwargs.get("args", ())

    def start(self):
        if self._target is not None:
            self._target(*self._args)


def _hosted_readiness_fixture(profiles):
    """Build exact client/server/Presence-v2 facts for hosted preflight tests."""

    from core.jamulus_roster_identity import (
        ordered_client_local_roster_fingerprint,
        ordered_common_roster_digest,
    )
    from core.jamulus_rpc_client import (
        JamulusOrderedRosterProof,
        JamulusOrderedRosterRow,
        JamulusRpcMonitorIdentity,
    )
    from core.logical_sources import derive_logical_source_id
    from core.take_project import new_project_id

    identity = JamulusRpcMonitorIdentity(31, 41, 5432)
    local_ids = tuple(range(len(profiles)))
    proof = JamulusOrderedRosterProof(
        identity=identity,
        rpc_connection_generation=3,
        audio_connection_generation=5,
        roster_revision=7,
        observed_at=time.monotonic(),
        rows=tuple(
            JamulusOrderedRosterRow(index, local_ids[index], profile)
            for index, profile in enumerate(profiles)
        ),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest(tuple(profiles)),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            local_ids,
            own_ordinal=0,
        ),
    )
    participant_ids = tuple(new_project_id() for _profile in profiles)
    claims = [
        SimpleNamespace(
            participant_id=participant_ids[index],
            display_name=profile.name,
            ordered_roster_digest=proof.common_digest,
            roster_count=proof.roster_size,
            self_ordinal=index,
            process_generation=(
                identity.process_generation if index == 0 else index + 1
            ),
            rpc_connection_generation=(
                proof.rpc_connection_generation if index == 0 else 1
            ),
            audio_connection_generation=(
                proof.audio_connection_generation if index == 0 else 1
            ),
            topology_epoch=1,
            challenge="lease",
            challenge_epoch=1,
            presence_generation=index + 1,
            capture_enabled=True,
            recorder_eligible=True,
        )
        for index, profile in enumerate(profiles)
    ]
    claims_holder = [tuple(claims)]
    session_id = new_project_id()
    guest_obligations = tuple(
        SimpleNamespace(
            participant_id=participant_ids[index],
            track_count=1,
            map_fingerprint=(f"{index + 1:02x}" * 32),
            presence_generation=index + 1,
            channel_counts=(1,),
            logical_source_ids=(
                derive_logical_source_id(
                    session_id,
                    participant_ids[index],
                    "local_original",
                    0,
                ),
            ),
            exact_topology=True,
        )
        for index in range(1, len(participant_ids))
    )
    prepared_obligations = {}
    capture_arms = {}
    capture_arm_calls = []
    capture_arm_cancellations = []
    capture_arm_generation = [0]

    def _prepare_obligations(take_id):
        prepared_obligations[take_id] = guest_obligations
        return guest_obligations, ()

    def _discard_obligations(take_id):
        return prepared_obligations.pop(take_id, None) is not None

    def _publish_capture_arm(take_id, *, recording_plan_fingerprint):
        capture_arm_generation[0] += 1
        arm = SimpleNamespace(
            take_id=take_id,
            arm_generation=capture_arm_generation[0],
            recording_plan_fingerprint=recording_plan_fingerprint,
        )
        capture_arms[take_id] = arm
        capture_arm_calls.append(arm)
        return arm

    def _capture_arm_ready(take_id, *, arm_generation):
        current = capture_arms.get(take_id)
        return bool(
            current is not None and current.arm_generation == int(arm_generation)
        )

    def _wait_for_capture_arm_acknowledgements(
        take_id,
        *,
        arm_generation,
        timeout_s,
    ):
        del timeout_s
        return _capture_arm_ready(take_id, arm_generation=arm_generation)

    def _cancel_capture_arm(take_id, *, arm_generation=None):
        current = capture_arms.get(take_id)
        if current is None or (
            arm_generation is not None and current.arm_generation != int(arm_generation)
        ):
            return False
        capture_arm_cancellations.append((take_id, arm_generation))
        capture_arms.pop(take_id, None)
        return True

    host_peer = SimpleNamespace(
        active=True,
        session_id=session_id,
        host_enrollment=SimpleNamespace(participant_id=participant_ids[0]),
        recording_presence_snapshot=lambda **_kwargs: claims_holder[0],
        prepare_local_original_obligations=_prepare_obligations,
        local_original_obligations_for_take=lambda take_id: prepared_obligations.get(
            take_id, ()
        ),
        discard_prepared_local_original_obligations=_discard_obligations,
        publish_capture_arm=_publish_capture_arm,
        capture_arm_ready=_capture_arm_ready,
        wait_for_capture_arm_acknowledgements=(_wait_for_capture_arm_acknowledgements),
        cancel_capture_arm=_cancel_capture_arm,
    )
    participants = {
        local_id: SimpleNamespace(
            channel_id=local_id,
            name=profile.name,
            role="Guitar",
            is_local=index == 0,
            participant_id="",
        )
        for index, (local_id, profile) in enumerate(zip(local_ids, profiles))
    }
    payload = {
        "connections": len(profiles),
        "clients": [
            {
                "id": index * 4,
                "name": profile.name,
                "instrumentCode": profile.instrument_code,
                "city": profile.city,
                "skillLevelCode": profile.skill_level_code,
                "address": f"127.0.0.1:{50_000 + index}",
                "channels": 1,
            }
            for index, profile in enumerate(profiles)
        ],
    }
    return SimpleNamespace(
        proof=proof,
        participant_ids=participant_ids,
        claims=claims,
        claims_holder=claims_holder,
        host_peer=host_peer,
        participants=participants,
        payload=payload,
        capture_arms=capture_arms,
        capture_arm_calls=capture_arm_calls,
        capture_arm_cancellations=capture_arm_cancellations,
    )


if __name__ == "__main__":
    unittest.main()
