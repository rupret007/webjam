"""Opt-in real Swift URLSession ↔ Python LAN Art room interoperability.

The listener is loopback-only. A Swift test-only initializer supplies that
endpoint after proving the production invitation parser refuses it. Empty
checkpoint files coordinate observations without retaining private payloads.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from core.session_transfer import (
    EnrollmentRegistry,
    SessionControlState,
    SessionCredentials,
    SessionPeerServer,
    TransferStore,
)

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.requires_local_socket,
    pytest.mark.skipif(
        sys.platform != "darwin"
        or os.environ.get("WEBJAM_RUN_SWIFT_ART_COMPANION_INTEGRATION") != "1",
        reason="requires explicit macOS Swift ↔ LAN Art companion integration gate",
    ),
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        pytest.fail(message, pytrace=False)


def _wait_checkpoint(process: subprocess.Popen, directory: Path, name: str) -> None:
    # The first checkpoint permits a cold Swift build. Later checkpoints only
    # wait for bounded URLSession work and the explicit Python acknowledgment.
    deadline = time.monotonic() + (120 if name == "enrolled" else 15)
    while not (directory / name).is_file():
        result = process.poll()
        _require(
            result is None,
            f"Swift Art companion probe exited before {name}; exit code {result}. "
            "Captured output is withheld to keep private transport details out of test logs.",
        )
        _require(
            time.monotonic() < deadline,
            f"Swift Art companion probe timed out before {name}.",
        )
        time.sleep(0.01)


def _release_checkpoint(directory: Path, name: str) -> None:
    (directory / name).touch()


def _stop_probe(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    # The Swift runner and its test executable share this probe's new process
    # group. A failed test cannot leave its child retrying a retired host.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


@pytest.mark.parametrize(
    ("profile", "start"),
    [("art", "talk_and_make"), ("art", "paint_along"), ("music", "")],
    ids=["make-together", "paint-along", "music-follow-only"],
)
def test_swift_urlsession_joins_observes_and_loses_a_real_lan_room(
    tmp_path: Path, profile: str, start: str,
) -> None:
    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path / "host", credentials.session_id,
        creator_profile_key=profile, art_start_key=start,
    )
    if profile == "music":
        # A real recording signal is still only observed room context: this
        # native companion has no recording, capture, mixer or Music Host path.
        control.begin(str(uuid.uuid4()), started_utc="2026-09-12T12:00:00Z")
    server = SessionPeerServer(
        "127.0.0.1", 0,
        registry=EnrollmentRegistry(tmp_path / "host", credentials),
        control=control,
        transfers=TransferStore(tmp_path / "host", credentials.session_id),
    )
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    process = None
    server_stopped = False
    try:
        server.start()
        environment = os.environ.copy()
        environment.update({
            "WEBJAM_ART_COMPANION_TEST_ENDPOINT": f"http://127.0.0.1:{server.address[1]}/",
            "WEBJAM_ART_COMPANION_TEST_SESSION": credentials.session_id,
            "WEBJAM_ART_COMPANION_TEST_TOKEN": credentials.invite_token,
            "WEBJAM_ART_COMPANION_TEST_CHECKPOINTS": str(checkpoints),
            "WEBJAM_ART_COMPANION_TEST_PROFILE": profile,
            "WEBJAM_ART_COMPANION_TEST_START": start,
        })
        # Never append raw Swift/URLSession output to an assertion: error
        # diagnostics from a networking framework may contain private data.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                ["swift", "test", "--package-path", str(ROOT / "ios"),
                 "--filter", "liveLANArtCompanion"],
                env=environment, cwd=ROOT, stdout=stdout, stderr=stderr,
                start_new_session=True,
            )
            _wait_checkpoint(process, checkpoints, "enrolled")
            _require(not server.room_participants(), "Enrollment alone became room presence.")
            _release_checkpoint(checkpoints, "may-read-state")

            _wait_checkpoint(process, checkpoints, "state-read")
            participants = server.room_participants()
            _require(len(participants) == 1, "An authenticated state read did not establish one guest.")
            names = server.room_connection_names()
            _require(
                names is not None and names.names == ("Mobile probe",),
                "The real host could not identify its authenticated mobile reader.",
            )
            _require(
                control.snapshot().art_start_key == start,
                "Guest observation changed the host's selected Art start.",
            )
            _release_checkpoint(checkpoints, "may-reject-auth")

            _wait_checkpoint(process, checkpoints, "auth-rejected")
            _require(
                server.room_participants() == participants,
                "A rejected participant credential changed room presence.",
            )
            server.stop()
            server_stopped = True
            _require(
                not server.room_participants() and server.active_handler_count == 0,
                "The real host did not finish stopping before the loss probe.",
            )
            _release_checkpoint(checkpoints, "host-stopped")
            _wait_checkpoint(process, checkpoints, "host-loss-confirmed")
            try:
                result = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pytest.fail("Swift Art companion probe did not exit after confirming host loss.", pytrace=False)
            _require(result == 0, "Swift Art companion probe reported a failed assertion.")
    finally:
        _stop_probe(process)
        if not server_stopped:
            server.stop()
