from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from core.network_invite import (
    BandInvite,
    InviteLinkError,
    create_invite_link,
    parse_invite_link,
)
from core.session_transfer import (
    EnrollmentRegistry,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionTransferError,
    TransferDescriptor,
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
    MediaSegment,
    MediaStatus,
    Participant,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    load_take_project,
    new_project_id,
    write_take_project,
)


def _id() -> str:
    return str(uuid.uuid4())


def _wav(path: Path, *, rate: int = 48_000, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timeline = np.arange(2_503, dtype=np.float64) / rate
    mono = (0.2 * np.sin(2.0 * np.pi * 440.0 * timeline)).astype(np.float32)
    data = mono if channels == 1 else np.column_stack((mono, mono * 0.5))
    sf.write(path, data, rate, subtype="PCM_24")


def _descriptor(
    path: Path,
    credentials: SessionCredentials,
    participant_id: str,
    take_id: str,
) -> TransferDescriptor:
    info = sf.info(str(path))
    return TransferDescriptor(
        session_id=credentials.session_id,
        take_id=take_id,
        participant_id=participant_id,
        segment_id=_id(),
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

    def __init__(self, root, *, device, samplerate, blocksize) -> None:
        self.root = Path(root)
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
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


def test_installation_identity_is_private_stable_and_fails_closed(tmp_path: Path) -> None:
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
    client = SessionPeerClient(
        "127.0.0.1", server.address[1], credentials=credentials
    )
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
    assert registry.presence_for_participant(first.participant_id).display_name == "Alex Guitar"


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
    guest = GuestPeerSession(
        invite,
        display_name="Alex",
        takes_root=tmp_path / "guest",
        installation_path=tmp_path / "installation.json",
        capture_enabled=lambda: True,
        capture_config=lambda: (7, 48_000, 128),
        capture_factory=_FakeCapture,
        on_originals_changed=originals_updates.append,
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
        transfers.status(item.descriptor).complete
        for item in guest.pending_segments
    )
    assert originals_updates == [guest.originals_root]
    guest.stop()


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


def _base_project(take_dir: Path, take_id: str, session_id: str, participant_id: str) -> None:
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
        missing = load_take_project(take_dir)
        assert missing.status is ProjectStatus.NEEDS_ATTENTION
        assert any("has not arrived" in error for error in missing.errors)
        missing_payload = json.loads((take_dir / "webjam-take.json").read_text())
        assert missing_payload["peer_transfers"]["participants"][0]["status"] == "missing"
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
        attached = next(
            track for track in complete.tracks
            if track.primary_segment.segment_id == descriptor.segment_id
        )
        attached_path = take_dir / attached.primary_segment.path
        assert attached_path.read_bytes() == before
        assert local_original.read_bytes() == before
        payload = json.loads((take_dir / "webjam-take.json").read_text())
        assert payload["peer_transfers"]["participants"][0]["status"] == "verified"
        assert updates == [(take_id, take_dir.resolve(), True)]
        assert host.reconcile_take(take_id, take_dir) is False
        assert updates == [(take_id, take_dir.resolve(), True)]
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
def test_host_peer_binding_accepts_only_private_lan_ipv4(host: str, expected: bool) -> None:
    assert is_private_lan_host(host) is expected
