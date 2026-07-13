"""Production ownership for WebJam's private recording transfer plane.

Jamulus remains the live-audio transport.  These two small runtimes own only
durable enrollment, recording-state observation, local isolated originals,
and resumable delivery.  A peer outage never stops an active local capture.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from core.network_invite import BandInvite, create_invite_link
from core.session_transfer import (
    EnrollmentRegistry,
    ParticipantEnrollment,
    PresenceBinding,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionStateSnapshot,
    SessionTransferError,
    TransferDescriptor,
    TransferIntegrityError,
    TransferStore,
    _sha256_file,
    _write_json_secure,
    derive_participant_id,
    load_or_create_installation_id,
)


LOGGER = logging.getLogger("webjam.session_transfer")
_POLL_SECONDS = 0.75
_TRANSFER_ERROR_PREFIX = "Peer isolated recording: "


def is_private_lan_host(value: str) -> bool:
    """True only for a routable RFC1918 IPv4 address on the local LAN."""

    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return False
    return bool(
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
        and not address.is_multicast
    )


def default_installation_identity_path(settings_file: str | Path) -> Path:
    """Place identity beside a custom config, or in WebJam app support."""

    configured = Path(settings_file).expanduser()
    if configured.name and configured.parent != Path("."):
        return configured.parent / ".webjam-installation.json"
    return Path.home() / ".webjam-installation.json"


@dataclass(frozen=True)
class PendingLocalSegment:
    descriptor: TransferDescriptor
    source: Path
    status: str = "pending"
    error: str = ""


class HostPeerSession:
    """Own one credential-rotated, private-LAN host peer service."""

    def __init__(
        self,
        *,
        on_take_updated: Callable[[str, Path, bool], None] | None = None,
    ) -> None:
        self._on_take_updated = on_take_updated
        self.credentials: SessionCredentials | None = None
        self.registry: EnrollmentRegistry | None = None
        self.control: SessionControlState | None = None
        self.transfers: TransferStore | None = None
        self.server: SessionPeerServer | None = None
        self.host_enrollment: ParticipantEnrollment | None = None
        self._root: Path | None = None
        self._registered_takes: dict[str, Path] = {}
        self._expected_by_take: dict[str, tuple[str, ...]] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def active(self) -> bool:
        return self.server is not None and self.credentials is not None

    @property
    def peer_port(self) -> int:
        return self.server.address[1] if self.server is not None else 0

    @property
    def session_id(self) -> str:
        return self.credentials.session_id if self.credentials else ""

    def start(
        self,
        bind_host: str,
        *,
        takes_root: str | Path,
        installation_path: str | Path,
        display_name: str,
    ) -> None:
        if self.active:
            return
        if not is_private_lan_host(bind_host):
            raise SessionTransferError(
                "The recording service needs this Mac's private Wi-Fi address."
            )
        credentials = SessionCredentials.create()
        root = (
            Path(takes_root).expanduser().resolve()
            / ".webjam-peer"
            / credentials.session_id
        )
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        registry = EnrollmentRegistry(root, credentials)
        control = SessionControlState(root, credentials.session_id)
        transfers = TransferStore(root, credentials.session_id)
        server = SessionPeerServer(
            bind_host,
            0,
            registry=registry,
            control=control,
            transfers=transfers,
        )
        installation_id = load_or_create_installation_id(installation_path)
        host_enrollment = registry.enroll(
            installation_id,
            display_name,
            invite_token=credentials.invite_token,
        )
        try:
            server.start()
        except BaseException:
            server.stop()
            raise
        with self._lock:
            self.credentials = credentials
            self.registry = registry
            self.control = control
            self.transfers = transfers
            self.server = server
            self.host_enrollment = host_enrollment
            self._root = root
            self._registered_takes.clear()
            self._expected_by_take.clear()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._maintenance_loop,
            name="webjam-host-transfer-maintenance",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None
        server = self.server
        if server is not None:
            server.stop()
        with self._lock:
            self.server = None
            self.registry = None
            self.control = None
            self.transfers = None
            self.credentials = None
            self.host_enrollment = None
            self._root = None
            self._registered_takes.clear()
            self._expected_by_take.clear()

    def invite_link(self, *, host: str, jamulus_port: int, session_name: str) -> str:
        if not self.active or self.credentials is None:
            return ""
        return create_invite_link(
            host,
            port=jamulus_port,
            session_name=session_name,
            session_id=self.credentials.session_id,
            peer_port=self.peer_port,
            invite_token=self.credentials.invite_token,
        )

    def bind_host_presence(
        self,
        channel_id: int,
        display_name: str,
        *,
        capture_enabled: bool,
    ) -> PresenceBinding | None:
        if self.registry is None or self.host_enrollment is None:
            return None
        prior = self.registry.presence_for_participant(
            self.host_enrollment.participant_id
        )
        if (
            prior is not None
            and prior.channel_id == int(channel_id)
            and prior.display_name == " ".join(str(display_name).split())
            and prior.capture_enabled == bool(capture_enabled)
        ):
            return prior
        generation = max(time.time_ns(), (prior.generation + 1) if prior else 1)
        return self.registry.bind_presence(
            self.host_enrollment.participant_id,
            channel_id,
            display_name,
            generation=generation,
            capture_enabled=capture_enabled,
        )

    def participant_id_for_channel(self, channel_id: int) -> str | None:
        if self.registry is None:
            return None
        return self.registry.participant_id_for_channel(channel_id)

    def begin_take(self, take_id: str, *, started_utc: str) -> SessionStateSnapshot | None:
        if self.control is None:
            return None
        snapshot = self.control.begin(take_id, started_utc=started_utc)
        if take_id in self._expected_by_take:
            return snapshot
        expected: list[str] = []
        if self.registry is not None:
            host_id = self.host_enrollment.participant_id if self.host_enrollment else ""
            for enrollment in self.registry.participants():
                if enrollment.participant_id == host_id:
                    continue
                binding = self.registry.presence_for_participant(
                    enrollment.participant_id
                )
                if binding is not None and binding.capture_enabled:
                    expected.append(enrollment.participant_id)
        self._expected_by_take[take_id] = tuple(sorted(expected))
        return snapshot

    def finish_take(
        self,
        take_id: str,
        *,
        stopped_utc: str,
        needs_attention: bool = False,
        message: str = "",
    ) -> SessionStateSnapshot | None:
        if self.control is None:
            return None
        return self.control.finish(
            take_id,
            stopped_utc=stopped_utc,
            needs_attention=needs_attention,
            message=message,
        )

    def register_take(self, take_id: str, take_dir: str | Path) -> None:
        try:
            canonical_take = str(uuid.UUID(str(take_id)))
        except (ValueError, TypeError, AttributeError):
            return
        with self._lock:
            self._registered_takes[canonical_take] = Path(take_dir).resolve()
        self.reconcile_take(canonical_take, take_dir)

    def _maintenance_loop(self) -> None:
        while not self._stop_event.wait(_POLL_SECONDS):
            with self._lock:
                registered = tuple(self._registered_takes.items())
            for take_id, take_dir in registered:
                try:
                    self.reconcile_take(take_id, take_dir)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not refresh peer transfer inventory")

    def reconcile_take(self, take_id: str, take_dir: str | Path) -> bool:
        """Attach verified media and disclose every expected missing transfer."""

        if self.transfers is None or self.registry is None:
            return False
        from core.file_io import atomic_write_text
        from core.take_project import (
            AlignmentState,
            MediaSegment,
            MediaStatus,
            Participant,
            ProjectStatus,
            ProjectTrack,
            SourceQuality,
            SourceType,
            load_take_project,
        )

        folder = Path(take_dir).resolve()
        manifest = folder / "webjam-take.json"
        if not manifest.is_file():
            return False
        project = load_take_project(folder)
        if project.take_id != take_id:
            return False
        inventory = self.transfers.inventory(take_id)
        display_by_id = {
            item.participant_id: item.display_name
            for item in self.registry.participants()
        }
        expected = set(self._expected_by_take.get(take_id, ()))
        expected.update(item.descriptor.participant_id for item in inventory)
        received_by_participant: dict[str, list] = {}
        for item in inventory:
            received_by_participant.setdefault(
                item.descriptor.participant_id, []
            ).append(item)

        participants = {item.participant_id: item for item in project.participants}
        tracks_by_id = {track.track_id: track for track in project.tracks}
        segment_ids = {
            segment.segment_id
            for track in project.tracks
            for segment in track.segments
        }
        attached_dir = folder / "transferred-isolated"
        attached_dir.mkdir(exist_ok=True)
        transfer_errors: list[str] = []
        transfer_summary: list[dict] = []
        next_order = max((item.order for item in project.tracks), default=-1) + 1
        attached_new_media = False

        for participant_id in sorted(expected):
            name = display_by_id.get(participant_id, "Musician")
            participant_items = received_by_participant.get(participant_id, [])
            participant_status = "missing"
            segment_summaries: list[dict] = []
            if participant_id not in participants:
                participants[participant_id] = Participant(participant_id, name)
            if not participant_items:
                transfer_errors.append(
                    f"{_TRANSFER_ERROR_PREFIX}{name}'s local original has not arrived."
                )
            for item in participant_items:
                descriptor = item.descriptor
                status = "verified" if item.complete else "receiving"
                if item.error:
                    status = "needs_attention"
                segment_summaries.append(
                    {
                        "segment_id": descriptor.segment_id,
                        "status": status,
                        "received_bytes": item.received_bytes,
                        "size_bytes": descriptor.size_bytes,
                        "sha256": descriptor.sha256,
                        "sample_rate": descriptor.sample_rate,
                        "channels": descriptor.channels,
                        "device_id": descriptor.device_id,
                        "source_channel": descriptor.source_channel,
                        "gap_frames": descriptor.gap_frames,
                        "errors": list(descriptor.capture_errors),
                    }
                )
                if not item.complete or item.path is None:
                    transfer_errors.append(
                        f"{_TRANSFER_ERROR_PREFIX}{name}'s local original is incomplete."
                    )
                    continue
                destination = attached_dir / (
                    f"{participant_id}-{descriptor.segment_id}.wav"
                )
                if not destination.is_file() or _sha256_file(destination) != descriptor.sha256:
                    temporary = destination.with_suffix(".wav.copying")
                    temporary.unlink(missing_ok=True)
                    try:
                        # Host peer storage and take folders normally share a
                        # volume. A hard link publishes the verified immutable
                        # bytes instantly without doubling long-take disk use.
                        os.link(item.path, temporary)
                    except OSError:
                        shutil.copyfile(item.path, temporary)
                    os.chmod(temporary, 0o600)
                    if _sha256_file(temporary) != descriptor.sha256:
                        temporary.unlink(missing_ok=True)
                        raise TransferIntegrityError(
                            "Attached peer media did not match its checksum."
                        )
                    os.replace(temporary, destination)
                if descriptor.segment_id not in segment_ids:
                    attached_new_media = True
                    media_status = (
                        MediaStatus.PARTIAL
                        if descriptor.capture_errors or descriptor.gap_frames
                        else MediaStatus.AVAILABLE
                    )
                    relative = destination.relative_to(folder).as_posix()
                    track = ProjectTrack(
                        track_id=str(
                            uuid.uuid5(
                                uuid.UUID(take_id),
                                f"peer-track:{descriptor.segment_id}",
                            )
                        ),
                        source_id=str(
                            uuid.uuid5(
                                uuid.UUID(take_id),
                                f"peer-source:{descriptor.segment_id}",
                            )
                        ),
                        participant_id=participant_id,
                        name=f"{name} Input {descriptor.source_channel + 1}",
                        instrument="",
                        source_type=SourceType.LOCAL_ISOLATED,
                        quality=SourceQuality.UNVERIFIED,
                        media_status=media_status,
                        order=next_order,
                        segments=(
                            MediaSegment(
                                segment_id=descriptor.segment_id,
                                path=relative,
                                project_start_frame=0,
                                frame_count=descriptor.frame_count,
                                sample_rate=descriptor.sample_rate,
                                channels=descriptor.channels,
                                sample_format=descriptor.subtype,
                                media_status=media_status,
                                sha256=descriptor.sha256,
                                device_id="",
                                gaps=(),
                                size_bytes=descriptor.size_bytes,
                                has_signal=None,
                            ),
                        ),
                        alignment=AlignmentState(
                            method="peer-local-original-unverified-alignment"
                        ),
                    )
                    tracks_by_id[track.track_id] = track
                    segment_ids.add(descriptor.segment_id)
                    next_order += 1
                if descriptor.capture_errors or descriptor.gap_frames:
                    transfer_errors.append(
                        f"{_TRANSFER_ERROR_PREFIX}{name}'s local original needs attention."
                    )
            if participant_items:
                if all(item.complete for item in participant_items):
                    participant_status = (
                        "needs_attention"
                        if any(
                            item.descriptor.capture_errors
                            or item.descriptor.gap_frames
                            for item in participant_items
                        )
                        else "verified"
                    )
                else:
                    participant_status = "receiving"
            transfer_summary.append(
                {
                    "participant_id": participant_id,
                    "display_name": name,
                    "status": participant_status,
                    "segments": segment_summaries,
                }
            )

        base_errors = tuple(
            item for item in project.errors if not item.startswith(_TRANSFER_ERROR_PREFIX)
        )
        errors = tuple(dict.fromkeys((*base_errors, *transfer_errors)))
        status = project.status
        if errors:
            status = ProjectStatus.NEEDS_ATTENTION
        elif not base_errors and status is ProjectStatus.NEEDS_ATTENTION:
            status = ProjectStatus.COMPLETE
        updated = replace(
            project,
            status=status,
            participants=tuple(participants.values()),
            tracks=tuple(tracks_by_id.values()),
            errors=errors,
            revision=project.revision + 1,
        )
        # Avoid rewriting the manifest every maintenance tick if truth did not change.
        prior_payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload = updated.to_dict()
        payload["peer_transfers"] = {
            "status": "needs_attention" if transfer_errors else "complete",
            "participants": transfer_summary,
        }
        prior_comparable = dict(prior_payload)
        prior_revision = prior_comparable.pop("revision", None)
        next_comparable = dict(payload)
        next_comparable.pop("revision", None)
        if prior_comparable == next_comparable:
            return False
        payload["revision"] = max(int(prior_revision or 0) + 1, updated.revision)
        atomic_write_text(
            manifest,
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            mode=0o600,
        )
        callback = self._on_take_updated
        if callback is not None:
            try:
                callback(take_id, folder, attached_new_media)
            except Exception:  # noqa: BLE001
                # UI notification is advisory. Never turn a successfully
                # verified/attached original back into a transfer failure.
                LOGGER.exception("Could not publish peer take update")
        return True


class GuestPeerSession:
    """Observe host recording truth and preserve/upload this Mac's originals."""

    def __init__(
        self,
        invite: BandInvite,
        *,
        display_name: str,
        takes_root: str | Path,
        installation_path: str | Path,
        capture_enabled: Callable[[], bool],
        capture_config: Callable[[], tuple[int, int, int]],
        capture_factory: Callable[..., object] | None = None,
        on_originals_changed: Callable[[Path], None] | None = None,
    ) -> None:
        if not invite.peer_enabled:
            raise SessionTransferError(
                "This legacy invitation cannot coordinate isolated local recording."
            )
        credentials = SessionCredentials(invite.session_id, invite.invite_token)
        self.invite = invite
        self.display_name = " ".join(str(display_name).split())[:80] or "Musician"
        self.takes_root = Path(takes_root).expanduser().resolve()
        self.installation_path = Path(installation_path).expanduser().resolve()
        self.installation_id = load_or_create_installation_id(self.installation_path)
        self.capture_enabled = capture_enabled
        self.capture_config = capture_config
        self.capture_factory = capture_factory
        self._on_originals_changed = on_originals_changed
        self.client = SessionPeerClient(
            invite.host,
            invite.peer_port,
            credentials=credentials,
            timeout_s=2.0,
        )
        self.enrollment: ParticipantEnrollment | None = None
        self.last_state: SessionStateSnapshot | None = None
        self.last_error = ""
        self._desired_presence: tuple[int, str, bool] | None = None
        self._bound_presence: tuple[int, str, bool] | None = None
        self._presence_generation = 0
        self._capture = None
        self._active_take_id = ""
        self._capture_started_config: tuple[int, int, int] | None = None
        self._pending: list[PendingLocalSegment] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.queue_path = (
            self.takes_root
            / "WebJam Local Originals"
            / invite.session_id
            / "webjam-transfer-queue.json"
        )
        self.queue_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.queue_path.parent, 0o700)
        from core.local_capture import recover_stale_local_captures

        self.recovered_captures = recover_stale_local_captures(
            self.queue_path.parent
        )
        for recovered in self.recovered_captures:
            LOGGER.warning(
                "Recovered abandoned guest local capture in %s",
                recovered.recovery_dir,
            )
        self._load_queue()
        if self.recovered_captures or any(
            item.source.is_file() for item in self.pending_segments
        ):
            self._notify_originals_changed()

    @property
    def participant_id(self) -> str:
        return self.enrollment.participant_id if self.enrollment else ""

    @property
    def active_take_id(self) -> str:
        return self._active_take_id

    @property
    def pending_segments(self) -> tuple[PendingLocalSegment, ...]:
        with self._lock:
            return tuple(self._pending)

    @property
    def originals_root(self) -> Path:
        """Visible root that owns preserved originals and recovery folders."""

        return self.queue_path.parent

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="webjam-guest-recording-transfer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None
        # A quit/network leave never deletes or aborts an active original.
        self._finalize_capture(needs_attention="Session ended before host stop was observed.")
        try:
            self._upload_pending()
        except SessionTransferError:
            pass

    def observe_presence(self, channel_id: int, display_name: str) -> None:
        desired = (
            int(channel_id),
            " ".join(str(display_name).split())[:80] or self.display_name,
            bool(self.capture_enabled()),
        )
        with self._lock:
            if desired != self._desired_presence:
                self._desired_presence = desired

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
                self.last_error = ""
            except SessionTransferError as exc:
                # Peer failure is control-plane only. Keep capture rolling and
                # retry from the confirmed host byte offset on the next poll.
                self.last_error = str(exc)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Guest recording transfer worker failed")
                self.last_error = "The host recording service needs attention."
            self._stop_event.wait(_POLL_SECONDS)

    def poll_once(self) -> SessionStateSnapshot:
        if self.enrollment is None:
            self.enrollment = self.client.enroll(
                self.installation_id, self.display_name
            )
        self._publish_presence_if_needed()
        state = self.client.state(self.enrollment)
        self.last_state = state
        if state.signal is RecordingSignal.RECORDING and state.take_id:
            if bool(self.capture_enabled()):
                self._start_capture(state.take_id)
        elif state.signal in {
            RecordingSignal.FINALIZING,
            RecordingSignal.COMPLETE,
            RecordingSignal.NEEDS_ATTENTION,
        }:
            if state.take_id and state.take_id == self._active_take_id:
                self._finalize_capture(
                    needs_attention=state.message
                    if state.signal is RecordingSignal.NEEDS_ATTENTION
                    else ""
                )
        self._upload_pending()
        return state

    def _publish_presence_if_needed(self) -> None:
        if self.enrollment is None:
            return
        with self._lock:
            desired = self._desired_presence
            bound = self._bound_presence
        if desired is None or desired == bound:
            return
        self._presence_generation = max(
            time.time_ns(), self._presence_generation + 1
        )
        self.client.bind_presence(
            self.enrollment,
            channel_id=desired[0],
            display_name=desired[1],
            generation=self._presence_generation,
            capture_enabled=desired[2],
        )
        with self._lock:
            self._bound_presence = desired

    def _new_capture(self, root: Path, device: int, rate: int, blocksize: int):
        if self.capture_factory is not None:
            return self.capture_factory(
                root,
                device=device,
                samplerate=rate,
                blocksize=blocksize,
            )
        from core.local_capture import LocalInputCapture

        return LocalInputCapture(
            root,
            device=device,
            samplerate=rate,
            blocksize=blocksize,
        )

    def _start_capture(self, take_id: str) -> None:
        if self._capture is not None:
            if self._active_take_id == take_id:
                return
            # A different take cannot overwrite an unfinalized local original.
            self._finalize_capture(needs_attention="A new take started before the prior stop.")
        device, rate, blocksize = self.capture_config()
        originals = self.queue_path.parent
        originals.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(originals, 0o700)
        capture = self._new_capture(originals, int(device), int(rate), int(blocksize))
        capture.start()
        self._capture = capture
        self._active_take_id = take_id
        self._capture_started_config = (int(device), int(rate), int(blocksize))

    def _finalize_capture(self, *, needs_attention: str = "") -> None:
        capture = self._capture
        take_id = self._active_take_id
        if capture is None or not take_id:
            return
        self._capture = None
        self._active_take_id = ""
        final_dir = self.queue_path.parent / take_id
        try:
            result = capture.stop_into(final_dir)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"The local original could not be finalized: {exc}"
            return
        errors = list(getattr(result, "errors", ()) or ())
        if needs_attention:
            errors.append(needs_attention)
        current_config = tuple(int(item) for item in self.capture_config())
        if self._capture_started_config and current_config[:2] != self._capture_started_config[:2]:
            errors.append(
                "The selected input device or sample rate changed during this take; "
                "the preserved segment uses the configuration captured at its start."
            )
        capture_device = getattr(result, "capture_device", None)
        device_id = str(getattr(capture_device, "device_id", "") or "")
        gaps = tuple(getattr(result, "gaps", ()) or ())
        for channel, source in enumerate(tuple(getattr(result, "files", ()) or ())):
            source_path = Path(source).resolve()
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(source_path))
            except (OSError, RuntimeError) as exc:
                errors.append(f"The preserved local WAV is unreadable: {exc}")
                continue
            channel_gap_frames = sum(
                int(getattr(gap, "frame_count", 0) or 0)
                for gap in gaps
                if not getattr(gap, "channels", ())
                or channel in tuple(getattr(gap, "channels", ()))
            )
            descriptor = TransferDescriptor(
                session_id=self.invite.session_id,
                take_id=take_id,
                participant_id=self.enrollment.participant_id
                if self.enrollment
                else derive_participant_id(
                    self.invite.session_id, self.installation_id
                ),
                segment_id=str(uuid.uuid4()),
                sha256=_sha256_file(source_path),
                size_bytes=source_path.stat().st_size,
                sample_rate=int(info.samplerate),
                channels=int(info.channels),
                frame_count=int(info.frames),
                subtype=str(info.subtype or "PCM_24"),
                started_utc=str(getattr(result, "started_utc", "") or ""),
                device_id=device_id,
                source_channel=channel,
                gap_frames=channel_gap_frames,
                capture_errors=tuple(dict.fromkeys(errors)),
            )
            with self._lock:
                self._pending.append(PendingLocalSegment(descriptor, source_path))
        self._capture_started_config = None
        self._save_queue()
        self._notify_originals_changed()

    def _notify_originals_changed(self) -> None:
        callback = self._on_originals_changed
        if callback is None:
            return
        try:
            callback(self.originals_root)
        except Exception:  # noqa: BLE001
            # The audio and durable queue are already safe; a reveal-action
            # refresh must never make capture finalization look unsuccessful.
            LOGGER.exception("Could not publish Local Originals update")

    def _upload_pending(self) -> None:
        enrollment = self.enrollment
        if enrollment is None:
            return
        changed = False
        with self._lock:
            pending = tuple(self._pending)
        for item in pending:
            if item.status == "verified":
                continue
            if not item.source.is_file():
                if item.status != "missing_local_original":
                    replacement = replace(
                        item,
                        status="missing_local_original",
                        error="The preserved local original is missing.",
                    )
                    with self._lock:
                        index = self._pending.index(item)
                        self._pending[index] = replacement
                    changed = True
                continue
            try:
                receipt = self.client.upload_file(
                    enrollment, item.descriptor, item.source
                )
                status = "verified" if receipt.complete else "pending"
                error = receipt.error
            except SessionTransferError as exc:
                status = "pending"
                error = str(exc)
            replacement = replace(item, status=status, error=error)
            if replacement != item:
                with self._lock:
                    index = self._pending.index(item)
                    self._pending[index] = replacement
                changed = True
            if status != "verified":
                # Keep ordering deterministic and avoid pounding a failed host.
                break
        if changed:
            self._save_queue()

    def _save_queue(self) -> None:
        with self._lock:
            items = tuple(self._pending)
        _write_json_secure(
            self.queue_path,
            {
                "schema": 1,
                "session_id": self.invite.session_id,
                "segments": [
                    {
                        "descriptor": asdict(item.descriptor),
                        "source": str(item.source),
                        "status": item.status,
                        "error": item.error[:240],
                    }
                    for item in items
                ],
            },
        )

    def _load_queue(self) -> None:
        if not self.queue_path.is_file():
            return
        try:
            payload = json.loads(self.queue_path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1 or payload.get("session_id") != self.invite.session_id:
                raise ValueError("session")
            records = payload.get("segments", ())
            if not isinstance(records, list):
                raise ValueError("segments")
            loaded: list[PendingLocalSegment] = []
            for record in records:
                descriptor = TransferDescriptor.from_mapping(record["descriptor"])
                source = Path(record["source"]).expanduser().resolve()
                # Never invent completion for a missing local original.
                status = str(record.get("status", "pending"))
                if not source.is_file():
                    status = "missing_local_original"
                loaded.append(
                    PendingLocalSegment(
                        descriptor,
                        source,
                        status=status,
                        error=str(record.get("error", ""))[:240],
                    )
                )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionTransferError("The local transfer queue is unreadable.") from exc
        with self._lock:
            self._pending = loaded
        try:
            os.chmod(self.queue_path, 0o600)
        except OSError:
            pass
