from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.session_transfer import (
    EnrollmentRegistry,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionTransferError,
    TransferAuthenticationError,
    TransferConflictError,
    TransferDescriptor,
    TransferIntegrityError,
    TransferStore,
)


def _id() -> str:
    return str(uuid.uuid4())


def _wav(path: Path, *, rate: int = 48_000, channels: int = 1) -> None:
    frames = 2_503
    timeline = np.arange(frames, dtype=np.float64) / rate
    mono = (0.2 * np.sin(2.0 * np.pi * 440.0 * timeline)).astype(np.float32)
    data = mono if channels == 1 else np.column_stack([mono, mono * 0.5])
    sf.write(path, data, rate, subtype="PCM_24")


def _descriptor(
    source: Path,
    credentials: SessionCredentials,
    participant_id: str,
    *,
    take_id: str | None = None,
    segment_id: str | None = None,
) -> TransferDescriptor:
    info = sf.info(source)
    return TransferDescriptor(
        session_id=credentials.session_id,
        take_id=take_id or _id(),
        participant_id=participant_id,
        segment_id=segment_id or _id(),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
        sample_rate=info.samplerate,
        channels=info.channels,
        frame_count=info.frames,
        subtype=info.subtype,
        started_utc="2026-07-13T12:00:00Z",
    )


def test_credentials_derive_stable_scoped_participant_tokens() -> None:
    credentials = SessionCredentials.create()
    participant = _id()
    assert credentials.participant_token(participant) == credentials.participant_token(
        participant
    )
    assert credentials.participant_token(participant) != credentials.participant_token(_id())
    assert credentials.participant_token(participant) != SessionCredentials.create().participant_token(
        participant
    )


def test_registry_keeps_identity_through_rename_reload_and_duplicate_names(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    first_installation = _id()
    first = registry.enroll(
        first_installation, "Alex", invite_token=credentials.invite_token
    )
    renamed = registry.enroll(
        first_installation, "Alex Guitar", invite_token=credentials.invite_token
    )
    other = registry.enroll(_id(), "Alex Guitar", invite_token=credentials.invite_token)

    assert renamed.participant_id == first.participant_id
    assert renamed.participant_token == first.participant_token
    assert renamed.display_name == "Alex Guitar"
    assert other.participant_id != first.participant_id

    reopened = EnrollmentRegistry(tmp_path, credentials)
    assert reopened.participant_id_for_installation(first_installation) == first.participant_id
    assert reopened.authenticate(first.participant_id, first.participant_token)
    assert not reopened.authenticate(first.participant_id, other.participant_token)
    assert not reopened.authenticate(_id(), first.participant_token)
    assert os.stat(reopened.path).st_mode & 0o777 == 0o600
    assert os.stat(reopened.root).st_mode & 0o777 == 0o700
    saved = reopened.path.read_text(encoding="utf-8")
    assert credentials.invite_token not in saved
    assert first.participant_token not in saved


def test_registry_rejects_bad_invitation_and_wrong_session_file(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    with pytest.raises(TransferAuthenticationError):
        registry.enroll(_id(), "Musician", invite_token="x" * 43)

    registry.enroll(_id(), "Musician", invite_token=credentials.invite_token)
    with pytest.raises(SessionTransferError, match="another session"):
        EnrollmentRegistry(tmp_path, SessionCredentials.create())


def test_control_state_is_monotonic_idempotent_and_durable(tmp_path: Path) -> None:
    session_id = _id()
    take_id = _id()
    state = SessionControlState(tmp_path, session_id)
    initial = state.snapshot()
    started = state.begin(take_id, started_utc="2026-07-13T12:00:00Z")
    duplicate = state.begin(take_id, started_utc="ignored")
    assert started is duplicate
    assert started.generation == initial.generation + 1
    assert started.signal is RecordingSignal.RECORDING
    with pytest.raises(TransferConflictError):
        state.begin(_id(), started_utc="2026-07-13T12:00:01Z")

    finished = state.finish(take_id, stopped_utc="2026-07-13T12:01:00Z")
    repeated = state.finish(take_id, stopped_utc="2026-07-13T12:02:00Z")
    assert finished is repeated
    assert finished.signal is RecordingSignal.COMPLETE
    assert state.begin(take_id, started_utc="delayed duplicate") is finished
    reopened = SessionControlState(tmp_path, session_id)
    assert reopened.snapshot() == finished
    assert os.stat(reopened.path).st_mode & 0o777 == 0o600


def test_transfer_store_resumes_replays_chunks_and_publishes_exact_pcm(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    participant_id = _id()
    source = tmp_path / "local.wav"
    _wav(source, channels=2)
    original = source.read_bytes()
    descriptor = _descriptor(source, credentials, participant_id)
    store = TransferStore(tmp_path / "host", credentials.session_id)
    first = original[:713]
    receipt = store.append(descriptor, offset=0, data=first)
    assert receipt.received_bytes == len(first)
    assert not receipt.complete

    replay = store.append(descriptor, offset=0, data=first)
    assert replay.received_bytes == len(first)
    with pytest.raises(TransferConflictError) as out_of_order:
        store.append(descriptor, offset=len(first) + 1, data=b"x")
    assert out_of_order.value.expected_offset == len(first)
    with pytest.raises(TransferConflictError) as changed_retry:
        store.append(descriptor, offset=0, data=b"z" * len(first))
    assert changed_retry.value.expected_offset == len(first)

    receipt = store.append(descriptor, offset=len(first), data=original[len(first) :])
    assert receipt.complete
    assert receipt.path is not None
    assert receipt.path.read_bytes() == original
    assert source.read_bytes() == original
    assert not Path(str(receipt.path) + ".part").exists()
    assert os.stat(receipt.path).st_mode & 0o777 == 0o600

    # A duplicate final chunk or complete-file retry is exactly once.
    duplicate = store.append(descriptor, offset=0, data=original[:100])
    assert duplicate.complete
    assert duplicate.path == receipt.path


def test_transfer_store_preserves_bad_checksum_partial_for_recovery(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    source = tmp_path / "local.wav"
    _wav(source)
    descriptor = replace(
        _descriptor(source, credentials, _id()), sha256="0" * 64
    )
    store = TransferStore(tmp_path / "host", credentials.session_id)
    with pytest.raises(TransferIntegrityError, match="SHA-256"):
        store.append(descriptor, offset=0, data=source.read_bytes())
    status = store.status(descriptor)
    assert not status.complete
    assert status.received_bytes == source.stat().st_size
    assert "SHA-256" in status.error
    parts = list((tmp_path / "host").rglob("*.part"))
    assert len(parts) == 1
    assert parts[0].read_bytes() == source.read_bytes()


def test_transfer_store_rejects_pcm_metadata_mismatch_without_publication(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    source = tmp_path / "local.wav"
    _wav(source)
    descriptor = replace(
        _descriptor(source, credentials, _id()), sample_rate=44_100
    )
    store = TransferStore(tmp_path / "host", credentials.session_id)
    with pytest.raises(TransferIntegrityError, match="WAV facts"):
        store.append(descriptor, offset=0, data=source.read_bytes())
    assert not list((tmp_path / "host").rglob("*.wav"))
    assert list((tmp_path / "host").rglob("*.wav.part"))


def test_transfer_identity_cannot_escape_or_collide(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    source = tmp_path / "local.wav"
    _wav(source)
    participant_id = _id()
    descriptor = _descriptor(source, credentials, participant_id)
    with pytest.raises(ValueError, match="UUID"):
        replace(descriptor, segment_id="../../escape")
    with pytest.raises(TransferConflictError, match="another session"):
        TransferStore(tmp_path / "other", _id()).status(descriptor)


def test_transfer_descriptor_never_sends_private_capture_error_content(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    source = tmp_path / "local.wav"
    _wav(source)
    descriptor = replace(
        _descriptor(source, credentials, _id()),
        capture_errors=(
            "Recovery stayed in /Users/alice/Music and token=invite-private",
            "Jamulus --jsonrpcsecret command-private",
        ),
    )
    encoded = json.dumps(descriptor.capture_errors)
    assert "/Users/alice" not in encoded
    assert "invite-private" not in encoded
    assert "command-private" not in encoded
    assert "$HOME/Music" in encoded
    assert "[redacted]" in encoded


@pytest.fixture
def peer(tmp_path: Path):
    credentials = SessionCredentials.create()
    host_root = tmp_path / "host"
    registry = EnrollmentRegistry(host_root, credentials)
    control = SessionControlState(host_root, credentials.session_id)
    transfers = TransferStore(host_root, credentials.session_id)
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    server.start()
    client = SessionPeerClient(
        "127.0.0.1", server.address[1], credentials=credentials
    )
    try:
        yield credentials, registry, control, transfers, server, client
    finally:
        server.stop()


def test_peer_protocol_enrolls_observes_and_transfers_without_changing_original(
    tmp_path: Path,
    peer,
) -> None:
    credentials, registry, control, transfers, _server, client = peer
    installation_id = _id()
    enrolled = client.enroll(installation_id, "Sam")
    again = client.enroll(installation_id, "Sam Bass")
    assert again.participant_id == enrolled.participant_id
    assert registry.authenticate(again.participant_id, again.participant_token)

    take_id = _id()
    control.begin(take_id, started_utc="2026-07-13T12:00:00Z")
    state = client.state(again)
    assert state.take_id == take_id
    assert state.signal is RecordingSignal.RECORDING

    source = tmp_path / "guest-original.wav"
    _wav(source, channels=2)
    before = source.read_bytes()
    descriptor = _descriptor(
        source, credentials, again.participant_id, take_id=take_id
    )
    receipt = client.upload_file(again, descriptor, source, chunk_bytes=311)
    assert receipt.complete
    published = transfers.status(descriptor)
    assert published.complete
    assert published.path is not None
    assert published.path.read_bytes() == before
    assert source.read_bytes() == before


def test_peer_protocol_resumes_after_server_restart(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    host_root = tmp_path / "host"
    registry = EnrollmentRegistry(host_root, credentials)
    control = SessionControlState(host_root, credentials.session_id)
    transfers = TransferStore(host_root, credentials.session_id)
    first_server = SessionPeerServer(
        "127.0.0.1", 0, registry=registry, control=control, transfers=transfers
    )
    first_server.start()
    first_client = SessionPeerClient(
        "127.0.0.1", first_server.address[1], credentials=credentials
    )
    enrolled = first_client.enroll(_id(), "Guest")
    source = tmp_path / "guest.wav"
    _wav(source)
    descriptor = _descriptor(source, credentials, enrolled.participant_id)
    raw = source.read_bytes()
    transfers.append(descriptor, offset=0, data=raw[:401])
    first_server.stop()

    # The service can move to another safe port; on-disk identity and offset
    # remain authoritative after process restart.
    reopened_registry = EnrollmentRegistry(host_root, credentials)
    reopened_control = SessionControlState(host_root, credentials.session_id)
    reopened_transfers = TransferStore(host_root, credentials.session_id)
    second_server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=reopened_registry,
        control=reopened_control,
        transfers=reopened_transfers,
    )
    second_server.start()
    try:
        second_client = SessionPeerClient(
            "127.0.0.1", second_server.address[1], credentials=credentials
        )
        receipt = second_client.upload_file(
            enrolled, descriptor, source, chunk_bytes=199
        )
        assert receipt.complete
        assert reopened_transfers.status(descriptor).received_bytes == len(raw)
    finally:
        second_server.stop()


def test_peer_protocol_rejects_wrong_invite_participant_and_descriptor(
    peer, tmp_path: Path
) -> None:
    credentials, _registry, _control, _transfers, server, client = peer
    with pytest.raises(TransferAuthenticationError):
        SessionPeerClient(
            "127.0.0.1",
            server.address[1],
            credentials=SessionCredentials(credentials.session_id, "x" * 43),
        ).enroll(_id(), "Intruder")

    enrollment = client.enroll(_id(), "Member")
    forged = replace(enrollment, participant_token="y" * 43)
    with pytest.raises(TransferAuthenticationError):
        client.state(forged)

    other = client.enroll(_id(), "Other member")
    # The status route is metadata-only, but it must enforce the same owner
    # binding as the upload route. Use a real WAV descriptor so the request
    # reaches that authorization boundary.
    wav = tmp_path / "member.wav"
    _wav(wav)
    descriptor = _descriptor(wav, credentials, enrollment.participant_id)
    with pytest.raises(TransferAuthenticationError):
        client.transfer_status(other, descriptor)


def test_unreadable_or_cross_session_checkpoints_fail_closed(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    host = tmp_path / "host"
    host.mkdir()
    (host / "webjam-session-state.json").write_text("{", encoding="utf-8")
    with pytest.raises(SessionTransferError, match="unreadable"):
        SessionControlState(host, credentials.session_id)

    (host / "webjam-session-state.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "session_id": _id(),
                "generation": 1,
                "signal": "idle",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SessionTransferError, match="another session"):
        SessionControlState(host, credentials.session_id)
