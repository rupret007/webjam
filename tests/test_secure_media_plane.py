from __future__ import annotations

import hashlib
import inspect
import struct
import uuid
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from core.secure_media_plane import (
    MAX_RETRY_AFTER_SECONDS,
    MediaAuthorizationError,
    MediaBackpressureError,
    MediaCallbackError,
    MediaCancelledError,
    MediaCapacityError,
    MediaConflictError,
    MediaGenerationError,
    MediaIntegrityError,
    MediaStorageError,
    MediaTransferPhase,
    SecureMediaGrant,
    SecureMediaPlane,
    VerifiedMedia,
)
from core.session_transfer import TransferDescriptor, TransferStore


def _id() -> str:
    return str(uuid.uuid4())


def _wav(path: Path, *, frames: int = 1_024, rate: int = 48_000) -> bytes:
    samples = [int(12_000 * ((index % 32) / 31.0 - 0.5)) for index in range(frames)]
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack(f"<{frames}h", *samples))
    return path.read_bytes()


def _descriptor(
    source: Path,
    *,
    session_id: str,
    participant_id: str,
    take_id: str,
    segment_id: str | None = None,
) -> TransferDescriptor:
    with wave.open(str(source), "rb") as uploaded:
        frames = uploaded.getnframes()
        rate = uploaded.getframerate()
    return TransferDescriptor(
        session_id=session_id,
        take_id=take_id,
        participant_id=participant_id,
        segment_id=segment_id or _id(),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
        sample_rate=rate,
        channels=1,
        frame_count=frames,
        subtype="PCM_16",
        started_utc="2026-07-13T12:00:00Z",
    )


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _grant(
    plane: SecureMediaPlane,
    clock: _Clock,
    *,
    participant_id: str,
    take_ids: tuple[str, ...],
    pin: str,
    connection_generation: int = 3,
):
    return plane.issue_grant(
        session_id=plane.session_id,
        participant_id=participant_id,
        peer_spki_sha256=pin,
        connection_generation=connection_generation,
        expires_at=clock.now + 120.0,
        allowed_take_ids=take_ids,
    )


def _connection(pin: str, generation: int = 3) -> dict[str, object]:
    return {
        "peer_spki_sha256": pin,
        "connection_generation": generation,
    }


def test_verified_upload_replays_exact_chunks_and_notifies_once(tmp_path: Path) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = hashlib.sha256(b"authenticated peer").hexdigest()
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    callbacks: list[VerifiedMedia] = []
    store = TransferStore(tmp_path / "host", session_id)
    plane = SecureMediaPlane(
        store,
        session_id,
        min_free_bytes=0,
        clock=clock,
        on_verified=callbacks.append,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    connection = _connection(pin)

    started = plane.begin_transfer(
        grant, descriptor, transfer_generation=8, **connection
    )
    assert started.phase is MediaTransferPhase.ACTIVE
    split = len(raw) // 2
    first = plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=8,
        offset=0,
        data=raw[:split],
        **connection,
    )
    replay = plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=8,
        offset=0,
        data=raw[:split],
        **connection,
    )
    assert replay.received_bytes == first.received_bytes == split
    with pytest.raises(MediaConflictError) as conflict:
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=8,
            offset=0,
            data=b"x" * split,
            **connection,
        )
    assert conflict.value.expected_offset == split

    complete = plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=8,
        offset=split,
        data=raw[split:],
        **connection,
    )
    assert complete.complete
    assert len(callbacks) == 1
    assert callbacks[0].path.read_bytes() == raw
    assert repr(callbacks[0]) == "VerifiedMedia(private=[redacted])"
    assert str(tmp_path) not in repr(callbacks[0])

    final_replay = plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=8,
        offset=0,
        data=raw[:split],
        **connection,
    )
    assert final_replay.complete
    assert len(callbacks) == 1
    with pytest.raises(MediaConflictError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=8,
            offset=0,
            data=b"z" * split,
            **connection,
        )
    assert len(callbacks) == 1


def test_grant_rejects_cross_session_participant_take_peer_and_connection(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = hashlib.sha256(b"expected peer").hexdigest()
    clock = _Clock()
    source = tmp_path / "source.wav"
    _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        min_free_bytes=0,
        clock=clock,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    rejected = (
        (replace(descriptor, session_id=_id()), pin, 3),
        (replace(descriptor, participant_id=_id()), pin, 3),
        (replace(descriptor, take_id=_id()), pin, 3),
        (descriptor, hashlib.sha256(b"other peer").hexdigest(), 3),
        (descriptor, pin, 4),
    )
    for candidate, observed_pin, generation in rejected:
        with pytest.raises(MediaAuthorizationError):
            plane.begin_transfer(
                grant,
                candidate,
                peer_spki_sha256=observed_pin,
                connection_generation=generation,
                transfer_generation=1,
            )


def test_peer_api_cannot_supply_paths_compression_or_noncanonical_ids(
    tmp_path: Path,
) -> None:
    begin_parameters = inspect.signature(SecureMediaPlane.begin_transfer).parameters
    chunk_parameters = inspect.signature(SecureMediaPlane.receive_chunk).parameters
    for forbidden in ("path", "destination", "filename", "compression", "encoding"):
        assert forbidden not in begin_parameters
        assert forbidden not in chunk_parameters

    session_id = _id()
    participant_id = _id()
    take_id = _id()
    source = tmp_path / "peer-chosen-name.wav"
    _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    with pytest.raises(ValueError, match="UUID"):
        replace(descriptor, segment_id="../../escape")
    with pytest.raises(ValueError, match="UUID"):
        SecureMediaGrant(
            grant_token="x" * 43,
            session_id=session_id,
            participant_id=participant_id,
            peer_spki_sha256="a" * 64,
            connection_generation=1,
            expires_at=2_000.0,
            allowed_take_ids=("../../escape",),
        )


def test_declared_file_session_quota_and_disk_preflight_are_fail_closed(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    first_take = _id()
    second_take = _id()
    pin = "a" * 64
    clock = _Clock()
    first_source = tmp_path / "first.wav"
    second_source = tmp_path / "second.wav"
    _wav(first_source)
    _wav(second_source, frames=1_025)
    first = _descriptor(
        first_source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=first_take,
    )
    second = _descriptor(
        second_source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=second_take,
    )
    connection = _connection(pin)

    file_plane = SecureMediaPlane(
        TransferStore(tmp_path / "file-host", session_id),
        session_id,
        max_file_bytes=first.size_bytes - 1,
        max_session_bytes=first.size_bytes,
        min_free_bytes=0,
        clock=clock,
    )
    file_grant = _grant(
        file_plane,
        clock,
        participant_id=participant_id,
        take_ids=(first_take,),
        pin=pin,
    )
    with pytest.raises(MediaCapacityError, match="too large"):
        file_plane.begin_transfer(
            file_grant, first, transfer_generation=1, **connection
        )

    quota = first.size_bytes + second.size_bytes - 1
    quota_plane = SecureMediaPlane(
        TransferStore(tmp_path / "quota-host", session_id),
        session_id,
        max_file_bytes=max(first.size_bytes, second.size_bytes),
        max_session_bytes=quota,
        min_free_bytes=0,
        clock=clock,
    )
    quota_grant = _grant(
        quota_plane,
        clock,
        participant_id=participant_id,
        take_ids=(first_take, second_take),
        pin=pin,
    )
    quota_plane.begin_transfer(
        quota_grant, first, transfer_generation=1, **connection
    )
    with pytest.raises(MediaCapacityError, match="quota"):
        quota_plane.begin_transfer(
            quota_grant, second, transfer_generation=1, **connection
        )

    probed: list[Path] = []

    def almost_enough(root: Path) -> int:
        probed.append(root)
        return first.size_bytes + 99

    disk_plane = SecureMediaPlane(
        TransferStore(tmp_path / "disk-host", session_id),
        session_id,
        max_file_bytes=first.size_bytes,
        max_session_bytes=first.size_bytes,
        min_free_bytes=100,
        disk_free_probe=almost_enough,
        clock=clock,
    )
    disk_grant = _grant(
        disk_plane,
        clock,
        participant_id=participant_id,
        take_ids=(first_take,),
        pin=pin,
    )
    with pytest.raises(MediaCapacityError, match="free space") as disk_error:
        disk_plane.begin_transfer(
            disk_grant, first, transfer_generation=1, **connection
        )
    assert probed == [disk_plane.store.root]
    assert str(tmp_path) not in str(disk_error.value)


def test_disk_probe_exception_does_not_expose_its_path_or_message(tmp_path: Path) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "b" * 64
    clock = _Clock()
    source = tmp_path / "private-musician-name.wav"
    _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )

    def broken_probe(_root: Path) -> int:
        raise OSError("/Users/private/Music/private-musician-name.wav")

    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        min_free_bytes=0,
        disk_free_probe=broken_probe,
        clock=clock,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    with pytest.raises(MediaStorageError) as failure:
        plane.begin_transfer(
            grant, descriptor, transfer_generation=1, **_connection(pin)
        )
    assert "/Users/private" not in str(failure.value)
    assert "private-musician" not in repr(failure.value)


def test_token_bucket_raises_bounded_retry_without_advancing_clock(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "c" * 64
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        max_raw_chunk_bytes=4,
        rate_bytes_per_second=2,
        burst_bytes=4,
        min_free_bytes=0,
        clock=clock,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    connection = _connection(pin)
    plane.begin_transfer(grant, descriptor, transfer_generation=1, **connection)
    plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=1,
        offset=0,
        data=raw[:4],
        **connection,
    )
    before = clock.now
    with pytest.raises(MediaBackpressureError) as limited:
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=1,
            offset=4,
            data=raw[4:8],
            **connection,
        )
    assert clock.now == before
    assert limited.value.retry_after_seconds == pytest.approx(2.0)
    assert 0 < limited.value.retry_after_seconds <= MAX_RETRY_AFTER_SECONDS
    clock.advance(limited.value.retry_after_seconds)
    resumed = plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=1,
        offset=4,
        data=raw[4:8],
        **connection,
    )
    assert resumed.received_bytes == 8
    with pytest.raises(MediaConflictError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=1,
            offset=8,
            data=raw[8:13],
            **connection,
        )


def test_cancel_preserves_partial_and_requires_new_transfer_generation(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "d" * 64
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    store = TransferStore(tmp_path / "host", session_id)
    plane = SecureMediaPlane(store, session_id, min_free_bytes=0, clock=clock)
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    connection = _connection(pin)
    plane.begin_transfer(grant, descriptor, transfer_generation=7, **connection)
    plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=7,
        offset=0,
        data=raw[:101],
        **connection,
    )
    cancelled = plane.cancel_transfer(
        grant, descriptor, transfer_generation=7, **connection
    )
    assert cancelled.cancelled
    assert store.status(descriptor).received_bytes == 101
    assert list(store.root.rglob("*.wav.part"))
    with pytest.raises(MediaCancelledError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=7,
            offset=101,
            data=raw[101:120],
            **connection,
        )
    with pytest.raises(MediaGenerationError):
        plane.begin_transfer(
            grant, descriptor, transfer_generation=7, **connection
        )
    resumed = plane.begin_transfer(
        grant, descriptor, transfer_generation=8, **connection
    )
    assert resumed.received_bytes == 101
    assert resumed.phase is MediaTransferPhase.ACTIVE


def test_revocation_and_expiry_stop_chunks_without_deleting_partials(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "e" * 64
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    store = TransferStore(tmp_path / "host", session_id)
    plane = SecureMediaPlane(store, session_id, min_free_bytes=0, clock=clock)
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    connection = _connection(pin)
    plane.begin_transfer(grant, descriptor, transfer_generation=1, **connection)
    plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=1,
        offset=0,
        data=raw[:71],
        **connection,
    )
    plane.revoke_grant(grant)
    plane.revoke_grant(grant)
    with pytest.raises(MediaAuthorizationError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=1,
            offset=71,
            data=raw[71:90],
            **connection,
        )
    assert store.status(descriptor).received_bytes == 71

    expiring_take = _id()
    expiring_descriptor = replace(
        descriptor, take_id=expiring_take, segment_id=_id()
    )
    expiring = plane.issue_grant(
        session_id=session_id,
        participant_id=participant_id,
        peer_spki_sha256=pin,
        connection_generation=3,
        expires_at=clock.now + 1.0,
        allowed_take_ids=(expiring_take,),
    )
    plane.begin_transfer(
        expiring, expiring_descriptor, transfer_generation=1, **connection
    )
    clock.advance(1.0)
    with pytest.raises(MediaAuthorizationError, match="no longer active"):
        plane.receive_chunk(
            expiring,
            expiring_descriptor,
            transfer_generation=1,
            offset=0,
            data=raw[:32],
            **connection,
        )
    assert store.status(expiring_descriptor).received_bytes == 0


def test_transfer_and_connection_generations_fail_closed(tmp_path: Path) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "f" * 64
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        min_free_bytes=0,
        clock=clock,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
        connection_generation=11,
    )
    connection = _connection(pin, 11)
    plane.begin_transfer(grant, descriptor, transfer_generation=4, **connection)
    with pytest.raises(MediaGenerationError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=3,
            offset=0,
            data=raw[:10],
            **connection,
        )
    with pytest.raises(MediaGenerationError):
        plane.begin_transfer(
            grant, descriptor, transfer_generation=5, **connection
        )
    with pytest.raises(MediaAuthorizationError):
        plane.receive_chunk(
            grant,
            descriptor,
            peer_spki_sha256=pin,
            connection_generation=10,
            transfer_generation=4,
            offset=0,
            data=raw[:10],
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda descriptor: replace(descriptor, sha256="0" * 64),
        lambda descriptor: replace(descriptor, sample_rate=44_100),
    ],
)
def test_forged_hash_or_pcm_facts_preserve_partial_and_never_notify(
    tmp_path: Path, mutate
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "1" * 64
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    descriptor = mutate(
        _descriptor(
            source,
            session_id=session_id,
            participant_id=participant_id,
            take_id=take_id,
        )
    )
    callbacks: list[VerifiedMedia] = []
    store = TransferStore(tmp_path / "host", session_id)
    plane = SecureMediaPlane(
        store,
        session_id,
        min_free_bytes=0,
        clock=clock,
        on_verified=callbacks.append,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    connection = _connection(pin)
    plane.begin_transfer(grant, descriptor, transfer_generation=1, **connection)
    with pytest.raises(MediaIntegrityError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=1,
            offset=0,
            data=raw,
            **connection,
        )
    receipt = store.status(descriptor)
    assert receipt.received_bytes == len(raw)
    assert not receipt.complete
    assert list(store.root.rglob("*.wav.part"))
    assert callbacks == []
    with pytest.raises(MediaCancelledError):
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=1,
            offset=0,
            data=raw[:16],
            **connection,
        )


def test_quota_reconstructs_from_transferstore_checkpoint_after_restart(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    first_take = _id()
    second_take = _id()
    pin = "2" * 64
    clock = _Clock()
    source = tmp_path / "source.wav"
    raw = _wav(source)
    first = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=first_take,
    )
    second = replace(first, take_id=second_take, segment_id=_id())
    quota = first.size_bytes + second.size_bytes - 1
    store = TransferStore(tmp_path / "host", session_id)
    first_plane = SecureMediaPlane(
        store,
        session_id,
        max_file_bytes=first.size_bytes,
        max_session_bytes=quota,
        min_free_bytes=0,
        clock=clock,
    )
    first_grant = _grant(
        first_plane,
        clock,
        participant_id=participant_id,
        take_ids=(first_take,),
        pin=pin,
    )
    connection = _connection(pin)
    first_plane.begin_transfer(
        first_grant, first, transfer_generation=1, **connection
    )
    first_plane.receive_chunk(
        first_grant,
        first,
        transfer_generation=1,
        offset=0,
        data=raw[:32],
        **connection,
    )

    reopened = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        max_file_bytes=first.size_bytes,
        max_session_bytes=quota,
        min_free_bytes=0,
        clock=clock,
    )
    reopened_grant = _grant(
        reopened,
        clock,
        participant_id=participant_id,
        take_ids=(second_take,),
        pin=pin,
    )
    with pytest.raises(MediaCapacityError, match="quota"):
        reopened.begin_transfer(
            reopened_grant, second, transfer_generation=1, **connection
        )


def test_grant_status_and_callback_errors_never_leak_secrets_or_paths(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "3" * 64
    clock = _Clock()
    source = tmp_path / "Alice Private.wav"
    raw = _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )

    def broken_callback(_media: VerifiedMedia) -> None:
        raise RuntimeError("Alice /Users/alice/Music/private.wav")

    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        min_free_bytes=0,
        clock=clock,
        on_verified=broken_callback,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
    )
    representation = repr(grant)
    assert grant.grant_token not in representation
    assert pin not in representation
    assert session_id not in representation
    assert participant_id not in representation
    assert str(tmp_path) not in repr(plane)

    connection = _connection(pin)
    status = plane.begin_transfer(
        grant, descriptor, transfer_generation=1, **connection
    )
    assert session_id not in repr(status)
    with pytest.raises(MediaCallbackError) as callback_failure:
        plane.receive_chunk(
            grant,
            descriptor,
            transfer_generation=1,
            offset=0,
            data=raw,
            **connection,
        )
    exposed = repr(callback_failure.value)
    assert "Alice" not in exposed
    assert "/Users/alice" not in exposed
    assert pin not in exposed
    # Publication succeeded and the failing callback is not invoked twice.
    duplicate = plane.receive_chunk(
        grant,
        descriptor,
        transfer_generation=1,
        offset=0,
        data=raw[:16],
        **connection,
    )
    assert duplicate.complete


def test_grants_are_short_lived_and_rotate_per_participant(
    tmp_path: Path,
) -> None:
    session_id = _id()
    participant_id = _id()
    take_id = _id()
    pin = "9" * 64
    clock = _Clock()
    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        min_free_bytes=0,
        clock=clock,
    )
    with pytest.raises(MediaAuthorizationError, match="lifetime"):
        plane.issue_grant(
            session_id=session_id,
            participant_id=participant_id,
            peer_spki_sha256=pin,
            connection_generation=1,
            expires_at=clock.now + 24 * 60 * 60 + 1,
            allowed_take_ids=(take_id,),
        )
    first = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=(take_id,),
        pin=pin,
        connection_generation=1,
    )
    replacement = plane.issue_grant(
        session_id=session_id,
        participant_id=participant_id,
        peer_spki_sha256=pin,
        connection_generation=2,
        expires_at=clock.now + 120,
        allowed_take_ids=(take_id,),
    )
    assert replacement != first
    assert len(plane._grants) == 1
    source = tmp_path / "source.wav"
    _wav(source)
    descriptor = _descriptor(
        source,
        session_id=session_id,
        participant_id=participant_id,
        take_id=take_id,
    )
    with pytest.raises(MediaAuthorizationError):
        plane.begin_transfer(
            first,
            descriptor,
            peer_spki_sha256=pin,
            connection_generation=1,
            transfer_generation=1,
        )


def test_new_transfer_inventory_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.secure_media_plane as media_module

    monkeypatch.setattr(media_module, "MAX_STORED_TRANSFERS", 1)
    session_id = _id()
    participant_id = _id()
    take_ids = (_id(), _id())
    pin = "8" * 64
    clock = _Clock()
    plane = SecureMediaPlane(
        TransferStore(tmp_path / "host", session_id),
        session_id,
        min_free_bytes=0,
        clock=clock,
    )
    grant = _grant(
        plane,
        clock,
        participant_id=participant_id,
        take_ids=take_ids,
        pin=pin,
    )
    for index, take_id in enumerate(take_ids):
        source = tmp_path / f"source-{index}.wav"
        _wav(source)
        descriptor = _descriptor(
            source,
            session_id=session_id,
            participant_id=participant_id,
            take_id=take_id,
        )
        if index == 0:
            plane.begin_transfer(
                grant,
                descriptor,
                transfer_generation=1,
                **_connection(pin),
            )
        else:
            with pytest.raises(MediaCapacityError, match="too many"):
                plane.begin_transfer(
                    grant,
                    descriptor,
                    transfer_generation=1,
                    **_connection(pin),
                )
