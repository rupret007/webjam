from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import soundfile as sf

from core.network_invite import (
    BandInvite,
    InviteLinkError,
    create_invite_link,
    parse_invite_link,
)
from core.local_capture import LocalCaptureGap
from core.session_transfer import (
    EnrollmentRegistry,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionTransferError,
    TransferDescriptor,
    TransferGap,
    TransferStore,
    load_or_create_installation_id,
)
from core.session_transfer_runtime import (
    GuestPeerSession,
    HostPeerSession,
    is_private_lan_host,
)
from core.take_project import (
    AlignmentState,
    GapInterval,
    MediaSegment,
    MediaStatus,
    Participant,
    ProjectMarker,
    ProjectStatus,
    ProjectTrack,
    RecoveryStatus,
    SourceQuality,
    SourceType,
    TakeProject,
    load_take_project,
    new_project_id,
    write_take_project,
)


def _id() -> str:
    return str(uuid.uuid4())


def _wait_until(predicate, description: str, *, timeout_s: float = 5.0) -> None:
    """Wait for the host's owned maintenance worker without polling sleeps."""

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), f"Timed out waiting for {description}."


class _ControllableThread:
    """Small deterministic worker double for timeout/ownership tests."""

    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.join_timeouts: list[float] = []

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(float(timeout or 0.0))


def _wav(
    path: Path,
    *,
    rate: int = 48_000,
    channels: int = 1,
    frequency: float = 440.0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timeline = np.arange(2_503, dtype=np.float64) / rate
    mono = (0.2 * np.sin(2.0 * np.pi * frequency * timeline)).astype(np.float32)
    data = mono if channels == 1 else np.column_stack((mono, mono * 0.5))
    sf.write(path, data, rate, subtype="PCM_24")


def _click_wav(
    path: Path,
    *,
    times: tuple[float, ...],
    duration_s: float,
    rate: int = 48_000,
) -> None:
    """Write bounded shared-transient fixtures for peer timing tests."""

    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.zeros(int(round(duration_s * rate)), dtype=np.float32)
    shape = np.asarray((0.88, 0.54, 0.24, 0.08), dtype=np.float32)
    for time_s in times:
        frame = int(round(time_s * rate))
        if 0 <= frame <= len(audio) - len(shape):
            audio[frame : frame + len(shape)] = shape
    sf.write(path, audio, rate, subtype="PCM_24")


def _append_server_reference(
    take_dir: Path,
    *,
    participant_id: str,
    display_name: str,
    source: Path,
) -> str:
    """Add one checksum-backed server stem for a specific enrolled musician."""

    project = load_take_project(take_dir)
    info = sf.info(str(source))
    segment = MediaSegment(
        segment_id=new_project_id(),
        path=source.relative_to(take_dir).as_posix(),
        project_start_frame=0,
        frame_count=info.frames,
        sample_rate=info.samplerate,
        channels=info.channels,
        sample_format=info.subtype,
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
    )
    reference = ProjectTrack(
        track_id=new_project_id(),
        source_id=new_project_id(),
        participant_id=participant_id,
        name=f"{display_name} network reference",
        instrument="",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=max((track.order for track in project.tracks), default=-1) + 1,
        segments=(segment,),
        alignment=AlignmentState(
            confidence=1.0,
            method="recorder-origin",
        ),
    )
    participants = project.participants
    if participant_id not in {item.participant_id for item in participants}:
        participants = (*participants, Participant(participant_id, display_name))
    write_take_project(
        take_dir,
        replace(
            project,
            participants=participants,
            tracks=(*project.tracks, reference),
            revision=project.revision + 1,
        ),
    )
    return reference.track_id


def _descriptor(
    path: Path,
    credentials: SessionCredentials,
    participant_id: str,
    take_id: str,
    *,
    segment_id: str | None = None,
) -> TransferDescriptor:
    info = sf.info(str(path))
    return TransferDescriptor(
        session_id=credentials.session_id,
        take_id=take_id,
        participant_id=participant_id,
        segment_id=segment_id or _id(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
        sample_rate=info.samplerate,
        channels=info.channels,
        frame_count=info.frames,
        subtype=info.subtype,
        started_utc="2026-07-13T12:00:00Z",
    )


class _FakeCapture:
    instances: list["_FakeCapture"] = []

    def __init__(
        self,
        root,
        *,
        device,
        samplerate,
        blocksize,
        take_id,
        session_id,
    ) -> None:
        self.root = Path(root)
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.take_id = take_id
        self.session_id = session_id
        self.started = False
        self.stop_calls = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop_into(self, destination: Path):
        self.stop_calls += 1
        first = Path(destination) / "input-1.wav"
        second = Path(destination) / "input-2.wav"
        _wav(first)
        _wav(second)
        return SimpleNamespace(
            files=(first, second),
            started_utc="2026-07-13T12:00:00Z",
            errors=(),
            gaps=(),
            capture_device=SimpleNamespace(device_id="portaudio:test:7"),
        )


class _GappedFakeCapture(_FakeCapture):
    """A deterministic local-capture result with one gap per source stem."""

    instances: list["_GappedFakeCapture"] = []

    def stop_into(self, destination: Path):
        result = super().stop_into(destination)
        return SimpleNamespace(
            files=result.files,
            started_utc=result.started_utc,
            errors=result.errors,
            gaps=(
                LocalCaptureGap(240, 48, (0,), "queue_overflow"),
                LocalCaptureGap(720, 64, (1,), "write_failure"),
            ),
            capture_device=result.capture_device,
        )


@pytest.fixture
def peer(tmp_path: Path):
    credentials = SessionCredentials.create()
    root = tmp_path / "host"
    registry = EnrollmentRegistry(root, credentials)
    control = SessionControlState(root, credentials.session_id)
    transfers = TransferStore(root, credentials.session_id)
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    server.start()
    try:
        yield credentials, registry, control, transfers, server
    finally:
        server.stop()


def test_private_invite_v2_round_trips_while_legacy_v1_remains_valid() -> None:
    credentials = SessionCredentials.create()
    legacy = parse_invite_link(
        create_invite_link("192.168.1.10", session_name="Legacy")
    )
    assert not legacy.peer_enabled
    link = create_invite_link(
        "192.168.1.10",
        session_name="Private",
        session_id=credentials.session_id,
        peer_port=43121,
        invite_token=credentials.invite_token,
    )
    parsed = parse_invite_link(link)
    assert parsed.peer_enabled
    assert parsed.session_id == credentials.session_id
    assert parsed.peer_port == 43121
    assert parsed.invite_token == credentials.invite_token


@pytest.mark.parametrize("host", ["8.8.8.8", "100.64.0.1", "jam.example.com"])
def test_private_invite_v2_never_exposes_control_or_media_plane_publicly(
    host: str,
) -> None:
    credentials = SessionCredentials.create()
    with pytest.raises(InviteLinkError, match="same-network"):
        create_invite_link(
            host,
            session_id=credentials.session_id,
            peer_port=43121,
            invite_token=credentials.invite_token,
        )

    valid = create_invite_link(
        "192.168.1.10",
        session_id=credentials.session_id,
        peer_port=43121,
        invite_token=credentials.invite_token,
    )
    forged = valid.replace("192.168.1.10", host)
    with pytest.raises(InviteLinkError, match="same-network"):
        parse_invite_link(forged)


def test_installation_identity_is_private_stable_and_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity.json"
    first = load_or_create_installation_id(path)
    assert load_or_create_installation_id(path) == first
    assert os.stat(path).st_mode & 0o777 == 0o600
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(SessionTransferError, match="identity"):
        load_or_create_installation_id(path)


def test_authenticated_presence_keeps_duplicate_names_distinct_through_rename_and_reconnect(
    peer,
) -> None:
    credentials, registry, _control, _transfers, server = peer
    client = SessionPeerClient("127.0.0.1", server.address[1], credentials=credentials)
    first = client.enroll(_id(), "Alex")
    second = client.enroll(_id(), "Alex")
    client.bind_presence(
        first,
        channel_id=4,
        display_name="Alex",
        generation=10,
        capture_enabled=True,
    )
    client.bind_presence(
        second,
        channel_id=7,
        display_name="Alex",
        generation=10,
        capture_enabled=False,
    )
    assert registry.participant_id_for_channel(4) == first.participant_id
    assert registry.participant_id_for_channel(7) == second.participant_id

    changed = client.bind_presence(
        first,
        channel_id=11,
        display_name="Alex Guitar",
        generation=11,
        capture_enabled=True,
    )
    assert changed.participant_id == first.participant_id
    assert registry.participant_id_for_channel(4) is None
    assert registry.participant_id_for_channel(11) == first.participant_id
    assert (
        registry.presence_for_participant(first.participant_id).display_name
        == "Alex Guitar"
    )


def test_guest_capture_starts_only_after_confirmed_state_survives_peer_outage_and_uploads(
    tmp_path: Path,
    peer,
) -> None:
    credentials, _registry, control, transfers, server = peer
    _FakeCapture.instances.clear()
    invite = BandInvite(
        "127.0.0.1",
        22124,
        "Test",
        credentials.session_id,
        server.address[1],
        credentials.invite_token,
    )
    originals_updates: list[Path] = []
    guidance_updates: list[str] = []
    guest = GuestPeerSession(
        invite,
        display_name="Alex",
        takes_root=tmp_path / "guest",
        installation_path=tmp_path / "installation.json",
        capture_enabled=lambda: True,
        capture_config=lambda: (7, 48_000, 128),
        capture_factory=_FakeCapture,
        on_originals_changed=originals_updates.append,
        on_guidance_changed=lambda: guidance_updates.append("changed"),
    )
    assert os.stat(guest.originals_root).st_mode & 0o777 == 0o700
    guest.observe_presence(4, "Alex")
    idle = guest.poll_once()
    assert idle.signal is RecordingSignal.IDLE
    assert not _FakeCapture.instances

    take_id = _id()
    control.begin(take_id, started_utc="2026-07-13T12:00:00Z")
    guest.poll_once()
    guest.poll_once()  # duplicate start snapshot is exactly once
    assert len(_FakeCapture.instances) == 1
    capture = _FakeCapture.instances[0]
    assert capture.started
    assert guest.active_take_id == take_id
    assert guidance_updates == ["changed"]
    assert capture.take_id == take_id
    assert capture.session_id == credentials.session_id

    original_state = guest.client.state
    guest.client.state = lambda _enrollment: (_ for _ in ()).throw(
        SessionTransferError("peer interrupted")
    )
    with pytest.raises(SessionTransferError):
        guest.poll_once()
    assert guest.active_take_id == take_id
    assert capture.stop_calls == 0
    guest.client.state = original_state

    control.finish(take_id, stopped_utc="2026-07-13T12:01:00Z")
    guest.poll_once()
    guest.poll_once()  # duplicate stop snapshot cannot finalize twice
    assert capture.stop_calls == 1
    assert guest.active_take_id == ""
    assert len(guest.pending_segments) == 2
    assert all(item.status == "verified" for item in guest.pending_segments)
    assert all(item.source.is_file() for item in guest.pending_segments)
    assert all(
        transfers.status(item.descriptor).complete for item in guest.pending_segments
    )
    assert originals_updates == [guest.originals_root]
    assert guidance_updates == ["changed", "changed"]
    guest.stop()


def test_guest_capture_gaps_survive_queue_transfer_and_host_attachment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Precise local-capture loss must reach Studio's immutable source view."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    _GappedFakeCapture.instances.clear()
    host = HostPeerSession()
    guest: GuestPeerSession | None = None
    try:
        host.start(
            "127.0.0.1",
            takes_root=tmp_path / "host-takes",
            installation_path=tmp_path / "host-installation.json",
            display_name="Host",
        )
        credentials = host.credentials
        assert credentials is not None
        invite = BandInvite(
            "127.0.0.1",
            22124,
            "Test",
            credentials.session_id,
            host.peer_port,
            credentials.invite_token,
        )
        guest = GuestPeerSession(
            invite,
            display_name="Alex",
            takes_root=tmp_path / "guest-takes",
            installation_path=tmp_path / "guest-installation.json",
            capture_enabled=lambda: True,
            capture_config=lambda: (7, 48_000, 128),
            capture_factory=_GappedFakeCapture,
        )
        guest.observe_presence(8, "Alex")
        assert guest.poll_once().signal is RecordingSignal.IDLE

        take_id = _id()
        assert host.begin_take(take_id, started_utc="2026-07-13T12:00:00Z")
        guest.poll_once()
        assert len(_GappedFakeCapture.instances) == 1

        take_dir = tmp_path / "finished-take"
        take_dir.mkdir()
        assert host.host_enrollment is not None
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        assert host.finish_take(take_id, stopped_utc="2026-07-13T12:01:00Z")
        guest.poll_once()

        pending = sorted(
            guest.pending_segments, key=lambda item: item.descriptor.source_channel
        )
        assert [
            (item.descriptor.source_channel, item.descriptor.gaps) for item in pending
        ] == [
            (0, (TransferGap(240, 48, (0,), "queue_overflow"),)),
            (1, (TransferGap(720, 64, (0,), "write_failure"),)),
        ]
        assert all(item.status == "verified" for item in pending)

        # The durable queue is the guest's recovery boundary. Reopening it
        # must retain exact intervals, not only the legacy aggregate count.
        reopened = GuestPeerSession(
            invite,
            display_name="Alex",
            takes_root=tmp_path / "guest-takes",
            installation_path=tmp_path / "guest-installation.json",
            capture_enabled=lambda: True,
            capture_config=lambda: (7, 48_000, 128),
            capture_factory=_GappedFakeCapture,
        )
        assert [
            item.descriptor.gaps
            for item in sorted(
                reopened.pending_segments,
                key=lambda item: item.descriptor.source_channel,
            )
        ] == [
            (TransferGap(240, 48, (0,), "queue_overflow"),),
            (TransferGap(720, 64, (0,), "write_failure"),),
        ]

        host.register_take(take_id, take_dir)
        _wait_until(
            lambda: sum(
                track.source_type is SourceType.LOCAL_ISOLATED
                for track in load_take_project(take_dir).tracks
            )
            == 2,
            "both gapped peer tracks",
        )
        project = load_take_project(take_dir)
        peer_tracks = {
            track.name: track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        }
        assert peer_tracks["Alex Input 1"].primary_segment.gaps == (
            GapInterval(240, 48, "queue_overflow", (0,)),
        )
        assert peer_tracks["Alex Input 2"].primary_segment.gaps == (
            GapInterval(720, 64, "write_failure", (0,)),
        )
        assert all(
            track.media_status is MediaStatus.PARTIAL for track in peer_tracks.values()
        )
        manifest = json.loads((take_dir / "webjam-take.json").read_text())
        summaries = {
            item["source_channel"]: item
            for item in manifest["peer_transfers"]["participants"][0]["segments"]
        }
        assert summaries[0]["gaps"] == [
            {
                "start_frame": 240,
                "frame_count": 48,
                "channels": [0],
                "reason": "queue_overflow",
            }
        ]
        assert summaries[1]["gaps"] == [
            {
                "start_frame": 720,
                "frame_count": 64,
                "channels": [0],
                "reason": "write_failure",
            }
        ]
    finally:
        if guest is not None:
            guest.stop()
        host.stop()


def test_host_keeps_legacy_gap_totals_without_inventing_gap_positions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    host = HostPeerSession()
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        client = SessionPeerClient("127.0.0.1", host.peer_port, credentials=credentials)
        alex = client.enroll(_id(), "Alex")
        client.bind_presence(
            alex,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        assert host.begin_take(take_id, started_utc="2026-07-13T12:00:00Z")
        take_dir = tmp_path / "finished-take"
        take_dir.mkdir()
        assert host.host_enrollment is not None
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        assert host.finish_take(take_id, stopped_utc="2026-07-13T12:01:00Z")

        original = tmp_path / "alex-original.wav"
        _wav(original)
        descriptor = replace(
            _descriptor(original, credentials, alex.participant_id, take_id),
            gap_frames=60,
        )
        assert descriptor.gaps == ()
        assert client.upload_file(alex, descriptor, original).complete
        host.register_take(take_id, take_dir)
        _wait_until(
            lambda: any(
                track.participant_id == alex.participant_id
                and track.source_type is SourceType.LOCAL_ISOLATED
                for track in load_take_project(take_dir).tracks
            ),
            "legacy-gap peer track",
        )

        project = load_take_project(take_dir)
        peer_track = next(
            track
            for track in project.tracks
            if track.participant_id == alex.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert peer_track.media_status is MediaStatus.PARTIAL
        assert peer_track.primary_segment.gaps == ()
        manifest = json.loads((take_dir / "webjam-take.json").read_text())
        summary = manifest["peer_transfers"]["participants"][0]["segments"][0]
        assert summary["gap_frames"] == 60
        assert summary["gaps"] == []
    finally:
        host.stop()


def test_guest_opt_out_never_opens_capture(tmp_path: Path, peer) -> None:
    credentials, _registry, control, _transfers, server = peer
    _FakeCapture.instances.clear()
    invite = BandInvite(
        "127.0.0.1",
        22124,
        "Test",
        credentials.session_id,
        server.address[1],
        credentials.invite_token,
    )
    guest = GuestPeerSession(
        invite,
        display_name="Alex",
        takes_root=tmp_path / "guest",
        installation_path=tmp_path / "installation.json",
        capture_enabled=lambda: False,
        capture_config=lambda: (7, 48_000, 128),
        capture_factory=_FakeCapture,
    )
    guest.observe_presence(4, "Alex")
    guest.poll_once()
    control.begin(_id(), started_utc="2026-07-13T12:00:00Z")
    guest.poll_once()
    assert not _FakeCapture.instances
    guest.stop()


def _base_project(
    take_dir: Path, take_id: str, session_id: str, participant_id: str
) -> None:
    source = take_dir / "server.wav"
    _wav(source)
    info = sf.info(str(source))
    segment = MediaSegment(
        segment_id=new_project_id(),
        path=source.name,
        project_start_frame=0,
        frame_count=info.frames,
        sample_rate=info.samplerate,
        channels=info.channels,
        sample_format=info.subtype,
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
    )
    project = TakeProject(
        session_id=session_id,
        take_id=take_id,
        session_title="Test",
        take_name=take_dir.name,
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48_000,
        participants=(Participant(participant_id, "Host"),),
        tracks=(
            ProjectTrack(
                track_id=new_project_id(),
                source_id=new_project_id(),
                participant_id=participant_id,
                name="Host network",
                instrument="",
                source_type=SourceType.JAMULUS_SERVER,
                quality=SourceQuality.NETWORK_TRACK,
                media_status=MediaStatus.AVAILABLE,
                order=0,
                segments=(segment,),
                alignment=AlignmentState(),
            ),
        ),
    )
    write_take_project(take_dir, project)


def test_host_inventory_discloses_missing_then_attaches_only_verified_media(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    updates: list[tuple[str, Path, bool]] = []
    host = HostPeerSession(
        on_take_updated=lambda take_id, path, attached: updates.append(
            (take_id, path, attached)
        )
    )
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        client = SessionPeerClient("127.0.0.1", host.peer_port, credentials=credentials)
        guest = client.enroll(_id(), "Alex")
        client.bind_presence(
            guest,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        host.begin_take(take_id, started_utc="2026-07-13T12:00:00Z")
        take_dir = tmp_path / "finished-take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        host.finish_take(take_id, stopped_utc="2026-07-13T12:01:00Z")
        host.register_take(take_id, take_dir)
        _wait_until(lambda: bool(updates), "initial missing peer inventory")
        missing = load_take_project(take_dir)
        assert missing.status is ProjectStatus.NEEDS_ATTENTION
        assert any("has not arrived" in error for error in missing.errors)
        missing_payload = json.loads((take_dir / "webjam-take.json").read_text())
        assert (
            missing_payload["peer_transfers"]["participants"][0]["status"] == "missing"
        )
        assert updates == [(take_id, take_dir.resolve(), False)]
        updates.clear()

        local_original = tmp_path / "alex-original.wav"
        _wav(local_original)
        before = local_original.read_bytes()
        descriptor = _descriptor(
            local_original, credentials, guest.participant_id, take_id
        )
        receipt = client.upload_file(guest, descriptor, local_original, chunk_bytes=257)
        assert receipt.complete
        assert host.reconcile_take(take_id, take_dir) is True
        complete = load_take_project(take_dir)
        assert not complete.errors
        assert complete.status is ProjectStatus.COMPLETE
        assert complete.session_evidence.recovery_status is RecoveryStatus.RECOVERED
        assert any(
            event.event == "peer_original_recovered"
            for event in complete.session_evidence.timeline
        )
        attached = next(
            track
            for track in complete.tracks
            if track.participant_id == guest.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert attached.primary_segment.segment_id != descriptor.segment_id
        attached_path = take_dir / attached.primary_segment.path
        assert attached_path.read_bytes() == before
        assert local_original.read_bytes() == before
        # The only server stem belongs to the host, not Alex.  A matching
        # checksum proves delivery, not a shared timeline, so WebJam must not
        # use another participant's audio as an alignment reference.
        assert attached.quality is SourceQuality.UNVERIFIED
        assert attached.alignment.method.startswith(
            "peer-local-original-awaiting-reference/"
        )
        payload = json.loads((take_dir / "webjam-take.json").read_text())
        assert payload["peer_transfers"]["participants"][0]["status"] == "verified"
        alignment = payload["peer_transfers"]["participants"][0]["segments"][0][
            "alignment"
        ]
        assert alignment["status"] == "waiting_for_reference"
        assert alignment["timing_ready"] is False
        assert "same-participant" in alignment["reason"]
        assert updates == [(take_id, take_dir.resolve(), True)]
        assert host.reconcile_take(take_id, take_dir) is False
        assert updates == [(take_id, take_dir.resolve(), True)]
    finally:
        host.stop()


def test_host_registration_wakes_its_worker_without_blocking_the_caller(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Long peer reconciliation cannot run on the recording-completion thread."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    host = HostPeerSession()
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[threading.Thread] = []

    def slow_reconcile(*_args, **_kwargs) -> bool:
        worker_threads.append(threading.current_thread())
        started.set()
        assert release.wait(5.0)
        return False

    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    monkeypatch.setattr(host, "reconcile_take", slow_reconcile)
    try:
        began = time.monotonic()
        host.register_take(_id(), tmp_path / "long-take")
        assert time.monotonic() - began < 0.25
        assert started.wait(1.0)
        assert worker_threads == [host._thread]
    finally:
        release.set()
        host.stop()


def test_host_promotes_verified_guest_original_only_with_strong_same_participant_reference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A checksum-verified guest source gains a timeline only from its own stem."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    host = HostPeerSession()
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        assert host.host_enrollment is not None
        client = SessionPeerClient(
            "127.0.0.1", host.peer_port, credentials=credentials
        )
        guest = client.enroll(_id(), "Alex")
        client.bind_presence(
            guest,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        assert host.begin_take(take_id, started_utc="2026-07-13T12:00:00Z")
        take_dir = tmp_path / "finished-take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )

        source_times = (0.55, 1.8, 3.7, 5.9, 8.4, 10.7)
        offset_s = 0.17321
        guest_original = take_dir / "alex-original.wav"
        server_reference = take_dir / "alex-server-reference.wav"
        _click_wav(guest_original, times=source_times, duration_s=12.0)
        _click_wav(
            server_reference,
            times=tuple(time_s + offset_s for time_s in source_times),
            duration_s=12.0,
        )
        reference_track_id = _append_server_reference(
            take_dir,
            participant_id=guest.participant_id,
            display_name="Alex",
            source=server_reference,
        )
        assert host.finish_take(take_id, stopped_utc="2026-07-13T12:01:00Z")
        descriptor = _descriptor(
            guest_original, credentials, guest.participant_id, take_id
        )
        assert client.upload_file(guest, descriptor, guest_original).complete

        host.register_take(take_id, take_dir)
        _wait_until(
            lambda: any(
                track.participant_id == guest.participant_id
                and track.source_type is SourceType.LOCAL_ISOLATED
                for track in load_take_project(take_dir).tracks
            ),
            "verified peer alignment",
        )

        project = load_take_project(take_dir)
        attached = next(
            track
            for track in project.tracks
            if track.participant_id == guest.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert attached.quality is SourceQuality.VERIFIED_ISOLATED
        assert attached.alignment.method.startswith(
            "peer-local-original-verified-alignment/"
        )
        assert attached.alignment.confidence >= 0.85
        assert attached.alignment.residual_ms <= 2.0
        assert attached.alignment.automatic_offset_s == pytest.approx(offset_s, abs=0.002)
        assert len(attached.alignment.anchors) >= 3
        assert attached.alignment.reference_track_id == reference_track_id
        assert len(attached.alignment.reference_fingerprint_sha256) == 64

        payload = json.loads((take_dir / "webjam-take.json").read_text())
        alignment = payload["peer_transfers"]["participants"][0]["segments"][0][
            "alignment"
        ]
        assert alignment["status"] == "aligned"
        assert alignment["timing_ready"] is True
        assert host.reconcile_take(take_id, take_dir) is False
    finally:
        host.stop()


def test_host_retries_guest_alignment_when_its_server_reference_arrives_later(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A preserved original is not terminal merely because its stem is late."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    host = HostPeerSession()
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        assert host.host_enrollment is not None
        client = SessionPeerClient(
            "127.0.0.1", host.peer_port, credentials=credentials
        )
        guest = client.enroll(_id(), "Alex")
        client.bind_presence(
            guest,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        assert host.begin_take(take_id, started_utc="2026-07-15T12:00:00Z")
        take_dir = tmp_path / "late-reference-take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        assert host.finish_take(take_id, stopped_utc="2026-07-15T12:01:00Z")
        host.register_take(take_id, take_dir)

        source_times = (0.5, 1.7, 3.4, 5.6, 8.2, 10.5)
        offset_s = 0.12175
        guest_original = take_dir / "alex-original.wav"
        _click_wav(guest_original, times=source_times, duration_s=12.0)
        descriptor = _descriptor(
            guest_original, credentials, guest.participant_id, take_id
        )
        assert client.upload_file(guest, descriptor, guest_original).complete
        assert host.reconcile_take(take_id, take_dir)

        waiting = next(
            track
            for track in load_take_project(take_dir).tracks
            if track.participant_id == guest.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert waiting.quality is SourceQuality.UNVERIFIED
        assert waiting.alignment.method.startswith(
            "peer-local-original-awaiting-reference/"
        )

        server_reference = take_dir / "alex-server-reference.wav"
        _click_wav(
            server_reference,
            times=tuple(time_s + offset_s for time_s in source_times),
            duration_s=12.0,
        )
        _append_server_reference(
            take_dir,
            participant_id=guest.participant_id,
            display_name="Alex",
            source=server_reference,
        )

        assert host.reconcile_take(take_id, take_dir)
        promoted = next(
            track
            for track in load_take_project(take_dir).tracks
            if track.participant_id == guest.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert promoted.quality is SourceQuality.VERIFIED_ISOLATED
        assert promoted.alignment.method.startswith(
            "peer-local-original-verified-alignment/"
        )
    finally:
        host.stop()


def test_host_retries_after_manifest_changes_during_peer_alignment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A recorder-side manifest update cannot be overwritten by slow alignment."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    from core import take_alignment

    real_align = take_alignment.align_project_tracks
    alignment_started = threading.Event()
    release_alignment = threading.Event()

    def delayed_align(*args, **kwargs):
        alignment_started.set()
        assert release_alignment.wait(10.0)
        return real_align(*args, **kwargs)

    monkeypatch.setattr(take_alignment, "align_project_tracks", delayed_align)
    host = HostPeerSession()
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        assert host.host_enrollment is not None
        client = SessionPeerClient(
            "127.0.0.1", host.peer_port, credentials=credentials
        )
        guest = client.enroll(_id(), "Alex")
        client.bind_presence(
            guest,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        assert host.begin_take(take_id, started_utc="2026-07-15T12:00:00Z")
        take_dir = tmp_path / "concurrent-manifest-take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        source_times = (0.5, 1.7, 3.4, 5.6, 8.2, 10.5)
        offset_s = 0.12175
        guest_original = take_dir / "alex-original.wav"
        server_reference = take_dir / "alex-server-reference.wav"
        _click_wav(guest_original, times=source_times, duration_s=12.0)
        _click_wav(
            server_reference,
            times=tuple(time_s + offset_s for time_s in source_times),
            duration_s=12.0,
        )
        _append_server_reference(
            take_dir,
            participant_id=guest.participant_id,
            display_name="Alex",
            source=server_reference,
        )
        assert host.finish_take(take_id, stopped_utc="2026-07-15T12:01:00Z")
        host.register_take(take_id, take_dir)
        descriptor = _descriptor(
            guest_original, credentials, guest.participant_id, take_id
        )
        assert client.upload_file(guest, descriptor, guest_original).complete

        result: list[bool] = []
        worker = threading.Thread(
            target=lambda: result.append(host.reconcile_take(take_id, take_dir)),
        )
        worker.start()
        assert alignment_started.wait(10.0)
        before_external_write = load_take_project(take_dir)
        marker = ProjectMarker(new_project_id(), 0.75, "Recorder note")
        write_take_project(
            take_dir,
            replace(
                before_external_write,
                markers=(*before_external_write.markers, marker),
                revision=before_external_write.revision + 1,
            ),
        )
        release_alignment.set()
        worker.join(timeout=15.0)
        assert not worker.is_alive()
        assert result == [True]

        reconciled = load_take_project(take_dir)
        assert marker in reconciled.markers
        attached = next(
            track
            for track in reconciled.tracks
            if track.participant_id == guest.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert attached.quality is SourceQuality.VERIFIED_ISOLATED
    finally:
        release_alignment.set()
        host.stop()


def test_host_retries_when_manifest_changes_just_before_conditional_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The final compare-and-replace closes the post-analysis TOCTOU window."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    from core import take_project

    real_commit = take_project.replace_take_project_manifest_if_unchanged
    commit_entered = threading.Event()
    release_commit = threading.Event()
    commits = 0

    def delayed_first_commit(*args, **kwargs):
        nonlocal commits
        commits += 1
        if commits == 1:
            commit_entered.set()
            assert release_commit.wait(10.0)
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(
        take_project,
        "replace_take_project_manifest_if_unchanged",
        delayed_first_commit,
    )
    host = HostPeerSession()
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        assert host.host_enrollment is not None
        client = SessionPeerClient(
            "127.0.0.1", host.peer_port, credentials=credentials
        )
        guest = client.enroll(_id(), "Alex")
        client.bind_presence(
            guest,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        assert host.begin_take(take_id, started_utc="2026-07-15T12:00:00Z")
        take_dir = tmp_path / "commit-race-take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        assert host.finish_take(take_id, stopped_utc="2026-07-15T12:01:00Z")

        original = tmp_path / "alex-original.wav"
        _wav(original)
        descriptor = _descriptor(
            original, credentials, guest.participant_id, take_id
        )
        assert client.upload_file(guest, descriptor, original).complete

        result: list[bool] = []
        worker = threading.Thread(
            target=lambda: result.append(host.reconcile_take(take_id, take_dir)),
        )
        worker.start()
        assert commit_entered.wait(10.0)
        before_external_write = load_take_project(take_dir)
        marker = ProjectMarker(new_project_id(), 0.75, "Recorder note")
        write_take_project(
            take_dir,
            replace(
                before_external_write,
                markers=(*before_external_write.markers, marker),
                revision=before_external_write.revision + 1,
            ),
        )
        release_commit.set()
        worker.join(timeout=15.0)
        assert not worker.is_alive()
        assert result == [True]

        reconciled = load_take_project(take_dir)
        assert marker in reconciled.markers
        assert any(
            track.participant_id == guest.participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
            for track in reconciled.tracks
        )
    finally:
        release_commit.set()
        host.stop()


def test_host_inventory_keeps_same_peer_segment_id_for_two_participants_distinct(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    host = HostPeerSession()
    host.start(
        "127.0.0.1",
        takes_root=tmp_path / "host-takes",
        installation_path=tmp_path / "host-installation.json",
        display_name="Host",
    )
    try:
        credentials = host.credentials
        assert credentials is not None
        assert host.host_enrollment is not None
        client = SessionPeerClient("127.0.0.1", host.peer_port, credentials=credentials)
        alex = client.enroll(_id(), "Alex")
        blair = client.enroll(_id(), "Blair")
        client.bind_presence(
            alex,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        client.bind_presence(
            blair,
            channel_id=8,
            display_name="Blair",
            generation=1,
            capture_enabled=True,
        )
        take_id = _id()
        host.begin_take(take_id, started_utc="2026-07-13T12:00:00Z")
        take_dir = tmp_path / "finished-take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        host.finish_take(take_id, stopped_utc="2026-07-13T12:01:00Z")
        host.register_take(take_id, take_dir)

        shared_segment_id = _id()
        alex_original = tmp_path / "alex-original.wav"
        blair_original = tmp_path / "blair-original.wav"
        _wav(alex_original)
        _wav(blair_original, frequency=554.37)
        alex_descriptor = _descriptor(
            alex_original,
            credentials,
            alex.participant_id,
            take_id,
            segment_id=shared_segment_id,
        )
        blair_descriptor = _descriptor(
            blair_original,
            credentials,
            blair.participant_id,
            take_id,
            segment_id=shared_segment_id,
        )
        assert client.upload_file(alex, alex_descriptor, alex_original).complete
        assert client.upload_file(blair, blair_descriptor, blair_original).complete

        assert host.reconcile_take(take_id, take_dir) is True
        project = load_take_project(take_dir)
        peer_tracks = tuple(
            track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert project.status is ProjectStatus.COMPLETE
        assert not project.errors
        assert {track.participant_id for track in peer_tracks} == {
            alex.participant_id,
            blair.participant_id,
        }
        assert len(peer_tracks) == 2
        assert len({track.track_id for track in peer_tracks}) == 2
        assert len({track.source_id for track in peer_tracks}) == 2
        project_segment_ids = {
            track.primary_segment.segment_id for track in peer_tracks
        }
        assert len(project_segment_ids) == 2
        assert shared_segment_id not in project_segment_ids
        attached_paths = {track.primary_segment.path for track in peer_tracks}
        assert len(attached_paths) == 2
        assert {
            (take_dir / track.primary_segment.path).read_bytes()
            for track in peer_tracks
        } == {alex_original.read_bytes(), blair_original.read_bytes()}
        payload = json.loads((take_dir / "webjam-take.json").read_text())
        participants = payload["peer_transfers"]["participants"]
        assert {item["participant_id"] for item in participants} == {
            alex.participant_id,
            blair.participant_id,
        }
        assert all(item["status"] == "verified" for item in participants)
        assert all(
            item["segments"][0]["segment_id"] == shared_segment_id
            for item in participants
        )

        revision = project.revision
        assert host.reconcile_take(take_id, take_dir) is False
        unchanged = load_take_project(take_dir)
        assert unchanged.revision == revision
        assert {
            (track.track_id, track.source_id, track.primary_segment.segment_id)
            for track in unchanged.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        } == {
            (track.track_id, track.source_id, track.primary_segment.segment_id)
            for track in peer_tracks
        }
    finally:
        host.stop()


def test_host_service_rotates_credentials_and_releases_port(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    host = HostPeerSession()
    args = {
        "takes_root": tmp_path / "takes",
        "installation_path": tmp_path / "installation.json",
        "display_name": "Host",
    }
    host.start("127.0.0.1", **args)
    first_session = host.session_id
    first_token = host.credentials.invite_token
    first_port = host.peer_port
    host.stop()
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.25)
        assert probe.connect_ex(("127.0.0.1", first_port)) != 0
    finally:
        probe.close()
    host.start("127.0.0.1", **args)
    try:
        assert host.session_id != first_session
        assert host.credentials.invite_token != first_token
    finally:
        host.stop()


def test_host_stop_suppresses_late_reconcile_after_rapid_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A checksum already in flight must not publish after Leave/End.

    The maintenance worker is allowed a bounded join during shutdown so the
    application stays responsive for a very large attachment. Once that bound
    expires, the old owner remains retained and a restart is refused until a
    later stop proves the worker and peer server are gone.
    """

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 0.01)
    from core import session_transfer_runtime

    real_hash = session_transfer_runtime._sha256_file
    checksum_entered = threading.Event()
    release_checksum = threading.Event()

    def delayed_hash(path: str | Path) -> str:
        if (
            Path(path).name.endswith(".wav.copying")
            and not checksum_entered.is_set()
        ):
            checksum_entered.set()
            assert release_checksum.wait(10.0)
        return real_hash(path)

    monkeypatch.setattr(session_transfer_runtime, "_sha256_file", delayed_hash)
    updates: list[tuple[str, Path, bool]] = []
    host = HostPeerSession(
        on_take_updated=lambda take_id, path, attached: updates.append(
            (take_id, path, attached)
        )
    )
    args = {
        "takes_root": tmp_path / "host-takes",
        "installation_path": tmp_path / "installation.json",
        "display_name": "Host",
    }
    try:
        host.start("127.0.0.1", **args)
        first_credentials = host.credentials
        assert first_credentials is not None
        client = SessionPeerClient(
            "127.0.0.1",
            host.peer_port,
            credentials=first_credentials,
        )
        guest = client.enroll(_id(), "Alex")
        client.bind_presence(
            guest,
            channel_id=7,
            display_name="Alex",
            generation=1,
            capture_enabled=True,
        )
        first_take_id = _id()
        host.begin_take(first_take_id, started_utc="2026-07-15T00:00:00Z")
        first_take_dir = tmp_path / "first-take"
        first_take_dir.mkdir()
        _base_project(
            first_take_dir,
            first_take_id,
            first_credentials.session_id,
            host.host_enrollment.participant_id,
        )
        host.finish_take(first_take_id, stopped_utc="2026-07-15T00:01:00Z")
        host.register_take(first_take_id, first_take_dir)

        original = tmp_path / "alex-original.wav"
        _wav(original)
        descriptor = _descriptor(
            original,
            first_credentials,
            guest.participant_id,
            first_take_id,
        )
        assert client.upload_file(guest, descriptor, original, chunk_bytes=257).complete
        assert checksum_entered.wait(5.0)
        old_thread = host._thread
        assert old_thread is not None
        # A partial-upload maintenance pass may have emitted ordinary updates
        # before the verified attachment was deliberately held.  Only events
        # after this point are stale.
        updates.clear()

        assert host.stop() is False
        stopped_manifest = (first_take_dir / "webjam-take.json").read_bytes()

        # A rapid Start cannot replace or hide the retained old owner.
        host.start("127.0.0.1", **args)
        assert host.credentials is first_credentials

        release_checksum.set()
        old_thread.join(timeout=2.0)
        assert not old_thread.is_alive()
        assert host.stop() is True
        assert (first_take_dir / "webjam-take.json").read_bytes() == stopped_manifest
        assert updates == []
        assert not list((first_take_dir / "transferred-isolated").glob("*.copying"))

        # A fresh lifecycle is allowed only after the retained owner is gone.
        host.start("127.0.0.1", **args)
        second_credentials = host.credentials
        assert second_credentials is not None
        assert second_credentials.session_id != first_credentials.session_id
        second_take_id = _id()
        host.begin_take(second_take_id, started_utc="2026-07-15T00:02:00Z")
        second_take_dir = tmp_path / "second-take"
        second_take_dir.mkdir()
        _base_project(
            second_take_dir,
            second_take_id,
            second_credentials.session_id,
            host.host_enrollment.participant_id,
        )
        host.finish_take(second_take_id, stopped_utc="2026-07-15T00:03:00Z")
        host.register_take(second_take_id, second_take_dir)
        _wait_until(
            lambda: updates
            == [(second_take_id, second_take_dir.resolve(), False)],
            "current-session peer inventory after restart",
        )
        assert updates == [(second_take_id, second_take_dir.resolve(), False)]
    finally:
        release_checksum.set()
        host.stop()


def test_host_take_update_callback_can_stop_reentrantly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A non-blocking notification may synchronously stop its host safely."""

    monkeypatch.setattr(
        "core.session_transfer_runtime.is_private_lan_host", lambda _host: True
    )
    monkeypatch.setattr("core.session_transfer_runtime._POLL_SECONDS", 3600.0)
    updates: list[str] = []
    host: HostPeerSession

    def stop_from_callback(take_id: str, _path: Path, _attached: bool) -> None:
        updates.append(take_id)
        host.stop()

    host = HostPeerSession(on_take_updated=stop_from_callback)
    try:
        host.start(
            "127.0.0.1",
            takes_root=tmp_path / "host-takes",
            installation_path=tmp_path / "installation.json",
            display_name="Host",
        )
        credentials = host.credentials
        assert credentials is not None
        assert host.host_enrollment is not None
        take_id = _id()
        host.begin_take(take_id, started_utc="2026-07-15T00:00:00Z")
        take_dir = tmp_path / "take"
        take_dir.mkdir()
        _base_project(
            take_dir,
            take_id,
            credentials.session_id,
            host.host_enrollment.participant_id,
        )
        host.finish_take(take_id, stopped_utc="2026-07-15T00:01:00Z")
        host.register_take(take_id, take_dir)
        _wait_until(lambda: updates == [take_id], "reentrant take update")
        _wait_until(lambda: not host.active, "reentrant host shutdown")

        assert updates == [take_id]
        assert not host.active
    finally:
        host.stop()


def test_external_host_stop_and_reentrant_callback_stop_do_not_deadlock() -> None:
    """A callback must not queue behind a stopper waiting on its lease."""

    callback_entered = threading.Event()
    call_stop_from_callback = threading.Event()
    callback_stop_returned = threading.Event()
    external_stop_returned = threading.Event()
    results: dict[str, bool] = {}
    host: HostPeerSession

    def stop_from_callback(_take_id: str, _path: Path, _attached: bool) -> None:
        callback_entered.set()
        assert call_stop_from_callback.wait(2.0)
        results["callback"] = host.stop()
        callback_stop_returned.set()

    host = HostPeerSession(on_take_updated=stop_from_callback)
    server = MagicMock()
    host.server = server
    host.credentials = SessionCredentials.create()
    generation = host._lifecycle_generation
    stop_event = host._stop_event

    def run_callback() -> None:
        with host._callback_condition:
            callback = host._begin_callback_lease_locked(generation, stop_event)
        assert callback is not None
        try:
            callback(_id(), Path("/unused"), False)
        finally:
            host._end_callback_lease(generation)

    worker = threading.Thread(target=run_callback, daemon=True)
    host._thread = worker
    worker.start()
    assert callback_entered.wait(2.0)

    def stop_externally() -> None:
        results["external"] = host.stop()
        external_stop_returned.set()

    external = threading.Thread(target=stop_externally, daemon=True)
    external.start()
    # The external stopper has invalidated the lifecycle and is now waiting
    # for the callback lease while holding the stop serialization lock.
    assert stop_event.wait(2.0)
    call_stop_from_callback.set()

    assert callback_stop_returned.wait(2.0), (
        "reentrant callback stop blocked behind the external stopper"
    )
    assert external_stop_returned.wait(2.0)
    worker.join(timeout=2.0)
    external.join(timeout=2.0)
    _wait_until(
        lambda: host._stop_retry_thread is None,
        "deferred host stop retry cleanup",
    )

    assert results == {"callback": False, "external": True}
    server.stop.assert_called_once_with()
    assert host._thread is None
    assert host.server is None
    assert host.credentials is None
    assert host._stop_requested_generation is None


def test_host_stop_retains_owner_until_worker_exit_is_proven() -> None:
    host = HostPeerSession()
    worker = _ControllableThread()
    server = MagicMock()
    host._thread = worker
    host.server = server
    host.credentials = SessionCredentials.create()

    assert host.stop() is False

    assert host._thread is worker
    assert host.server is server
    assert host.credentials is not None
    assert worker.join_timeouts == [3.0]
    server.stop.assert_not_called()

    worker.alive = False
    assert host.stop() is True

    server.stop.assert_called_once_with()
    assert host._thread is None
    assert host.server is None
    assert host.credentials is None


def test_guest_stop_retains_owner_and_capture_until_worker_exit_is_proven() -> None:
    guest = object.__new__(GuestPeerSession)
    guest._stop_event = threading.Event()
    guest._stop_lock = threading.Lock()
    worker = _ControllableThread()
    guest._thread = worker
    guest._finalize_capture = MagicMock()
    guest._upload_pending = MagicMock()

    assert guest.stop() is False

    assert guest._thread is worker
    assert worker.join_timeouts == [5.0]
    guest._finalize_capture.assert_not_called()
    guest._upload_pending.assert_not_called()

    worker.alive = False
    guest.start()
    assert guest._thread is worker
    assert guest._stop_event.is_set()

    assert guest.stop() is True

    assert guest._thread is None
    guest._finalize_capture.assert_called_once_with(
        needs_attention="Session ended before host stop was observed."
    )
    guest._upload_pending.assert_called_once_with()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.1.4", True),
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("127.0.0.1", False),
        ("169.254.2.1", False),
        ("8.8.8.8", False),
    ],
)
def test_host_peer_binding_accepts_only_private_lan_ipv4(
    host: str, expected: bool
) -> None:
    assert is_private_lan_host(host) is expected
