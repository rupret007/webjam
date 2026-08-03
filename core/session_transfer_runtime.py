"""Production ownership for WebJam's private recording transfer plane.

Jamulus remains the live-audio transport.  These two small runtimes own only
durable enrollment, recording-state observation, local isolated originals,
and resumable delivery.  A peer outage never stops an active local capture.
"""

from __future__ import annotations

import ipaddress
import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

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
    TransferGap,
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

# A generic transient match is useful evidence, but a remotely delivered local
# original must clear a higher bar before Studio may treat it as a timing-ready
# performance stem.  These are intentionally stricter than the aligner's
# ordinary automatic-result floor: peer media was captured by another clock
# and reaches the host after the take has ended.
_PEER_ALIGNMENT_INITIAL_METHOD = "peer-local-original-unverified-alignment"
_PEER_ALIGNMENT_WAITING_PREFIX = "peer-local-original-awaiting-reference/"
_PEER_ALIGNMENT_UNCERTAIN_PREFIX = "peer-local-original-alignment-uncertain/"
_PEER_ALIGNMENT_VERIFIED_PREFIX = "peer-local-original-verified-alignment/"
_PEER_ALIGNMENT_MIN_CONFIDENCE = 0.85
_PEER_ALIGNMENT_MAX_RESIDUAL_MS = 2.0
_PEER_ALIGNMENT_MIN_ANCHORS = 3
_MANIFEST_RECONCILE_MAX_ATTEMPTS = 3
_RECONCILE_RETRY = object()


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


def _peer_project_media_ids(
    take_id: str,
    participant_id: str,
    transfer_segment_id: str,
) -> tuple[str, str, str]:
    """Return durable project IDs for one transferred peer segment.

    ``TransferDescriptor.segment_id`` belongs to a guest's local capture.  It
    is not a project-wide identity: two independently installed clients can
    legitimately produce the same UUID.  Project entities must instead be
    scoped by both the enrolled participant and that local capture ID.
    """

    namespace = uuid.UUID(take_id)
    identity = f"{participant_id}:{transfer_segment_id}"
    return (
        str(uuid.uuid5(namespace, f"peer-track:{identity}")),
        str(uuid.uuid5(namespace, f"peer-source:{identity}")),
        str(uuid.uuid5(namespace, f"peer-segment:{identity}")),
    )


def _is_legacy_peer_attachment(
    track: object,
    *,
    take_id: str,
    participant_id: str,
    transfer_segment_id: str,
    source_type: object,
) -> bool:
    """Recognize the pre-scoped attachment shape without trusting lookalikes.

    Older manifests used the transfer segment UUID directly as the project
    segment ID.  Keep an existing attachment for the same enrolled participant
    idempotent during upgrade, while allowing a second participant with that
    UUID to be attached with the new scoped project IDs.
    """

    namespace = uuid.UUID(take_id)
    legacy_track_id = str(uuid.uuid5(namespace, f"peer-track:{transfer_segment_id}"))
    legacy_source_id = str(uuid.uuid5(namespace, f"peer-source:{transfer_segment_id}"))
    return bool(
        getattr(track, "participant_id", None) == participant_id
        and getattr(track, "source_type", None) == source_type
        and getattr(track, "track_id", None) == legacy_track_id
        and getattr(track, "source_id", None) == legacy_source_id
        and any(
            getattr(segment, "segment_id", None) == transfer_segment_id
            for segment in getattr(track, "segments", ())
        )
    )


def _enum_text(value: object) -> str:
    """Return an enum-like value without importing project types at startup."""

    return str(getattr(value, "value", value) or "").strip().lower()


def _track_media_is_verified(track: object, take_root: Path) -> bool:
    """Require file-backed checksum proof before timing a peer original.

    ``TransferStore`` validates the uploaded blob and the attachment path is
    hashed again before publication.  This second helper also verifies a
    candidate Jamulus reference from the immutable project manifest so an
    alignment result can never promote media that has changed on disk.
    """

    if _enum_text(getattr(track, "media_status", "")) != "available":
        return False
    segments = tuple(getattr(track, "segments", ()) or ())
    if not segments:
        return False
    for segment in segments:
        if _enum_text(getattr(segment, "media_status", "")) != "available":
            return False
        checksum = str(getattr(segment, "sha256", "") or "").strip().lower()
        relative = str(getattr(segment, "path", "") or "").strip()
        if not checksum or not relative:
            return False
        path = take_root / relative
        try:
            if not path.is_file() or _sha256_file(path) != checksum:
                return False
        except OSError:
            return False
    return True


def _same_participant_reference_track(
    tracks: tuple[object, ...],
    *,
    participant_id: str,
    take_root: Path,
) -> object | None:
    """Return exactly one strong Jamulus reference for a guest source.

    A server stem from another musician is not a timing reference for a guest
    original.  Likewise, two same-participant candidates are ambiguous: this
    bounded first integration chooses neither rather than guessing a channel.
    """

    candidates = tuple(
        track
        for track in tracks
        if getattr(track, "participant_id", "") == participant_id
        and _enum_text(getattr(track, "source_type", "")) == "jamulus_server"
    )
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    if _enum_text(getattr(candidate, "quality", "")) != "network_track":
        return None
    if not _track_media_is_verified(candidate, take_root):
        return None
    return candidate


def _reference_fingerprint(track: object) -> str:
    """Return a stable fingerprint for the exact server media used to align.

    Each source segment's declared digest is already checked against disk
    before this value is stored. Including its immutable segment ID and media
    facts prevents a later same-track rewrite from silently inheriting the
    timing transform for a different reference recording.
    """

    digest = hashlib.sha256()
    for segment in sorted(
        tuple(getattr(track, "segments", ()) or ()),
        key=lambda item: str(getattr(item, "segment_id", "")),
    ):
        digest.update(str(getattr(segment, "segment_id", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(getattr(segment, "sha256", "")).lower().encode("ascii"))
        digest.update(b"\0")
        digest.update(str(getattr(segment, "frame_count", 0)).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(getattr(segment, "sample_rate", 0)).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(getattr(segment, "channels", 0)).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _peer_alignment_summary(
    track: object,
    *,
    status: str,
    timing_ready: bool,
    reason: str,
) -> dict[str, object]:
    """Keep transport success separate from timing-readiness evidence."""

    alignment = getattr(track, "alignment", None)
    payload: dict[str, object] = {
        "status": status,
        "timing_ready": timing_ready,
        "confidence": round(float(getattr(alignment, "confidence", 0.0)), 6),
        "residual_ms": round(float(getattr(alignment, "residual_ms", 0.0)), 6),
        "method": str(getattr(alignment, "method", "") or ""),
        "reason": reason,
    }
    return payload


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
        # This is an advisory, non-blocking notification hook. It may call
        # ``stop`` reentrantly, but must not wait for UI work that in turn
        # waits for this maintenance worker.
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
        # One take can be registered directly while the maintenance worker is
        # also reconciling it.  Keep their slow checksum/copy/alignment work
        # serial without holding the host lifecycle lock or blocking another
        # take.  RLock preserves the documented reentrant update callback.
        self._take_reconcile_locks: dict[str, threading.RLock] = {}
        self._stop_event = threading.Event()
        # Registration happens on the recording completion path. Wake the
        # owned maintenance worker immediately instead of running hashing,
        # copying, or timing analysis on Qt's event loop.
        self._maintenance_wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._callback_condition = threading.Condition(self._lock)
        self._callback_leases: dict[tuple[int, int], int] = {}
        self._stop_lock = threading.Lock()
        self._stop_retry_thread: threading.Thread | None = None
        self._stop_retry_generation: int | None = None
        self._stop_requested_generation: int | None = None
        # A maintenance pass may be in a long checksum/copy when the user
        # leaves.  Give each start its own identity so that old work cannot
        # publish a manifest or UI update after a stop (or a rapid restart).
        self._lifecycle_generation = 0

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
        with self._lock:
            if (
                self.active
                or self._thread is not None
                or self._stop_requested_generation is not None
            ):
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
            self._lifecycle_generation += 1
            generation = self._lifecycle_generation
            stop_event = threading.Event()
            wake_event = threading.Event()
            self._stop_event = stop_event
            self._maintenance_wake = wake_event
            self.credentials = credentials
            self.registry = registry
            self.control = control
            self.transfers = transfers
            self.server = server
            self.host_enrollment = host_enrollment
            self._root = root
            self._registered_takes.clear()
            self._expected_by_take.clear()
            self._take_reconcile_locks.clear()
            self._thread = threading.Thread(
                target=self._maintenance_loop,
                args=(generation, stop_event, wake_event),
                name="webjam-host-transfer-maintenance",
                daemon=True,
            )
            thread = self._thread
        thread.start()

    def stop(self) -> bool:
        """Stop only after both worker and peer server are proven gone."""

        with self._callback_condition:
            if self._caller_holds_callback_lease_locked():
                # Never queue behind an external stopper while it is waiting
                # for this callback lease. Request the same stop idempotently,
                # then let the callback unwind so either that stopper or the
                # bounded deferred retry can prove cleanup.
                generation = self._request_stop_locked()
                self._ensure_stop_retry_locked(generation)
                return False
        with self._stop_lock:
            return self._stop_once()

    def _stop_once(self, *, expected_generation: int | None = None) -> bool:
        with self._callback_condition:
            if (
                expected_generation is not None
                and self._stop_requested_generation != expected_generation
            ):
                # The requested owner was already cleared. A newer lifecycle,
                # if any, belongs to its own explicit stop request.
                return True
            # Invalidate first.  A maintenance pass holds the same lock while
            # it publishes a manifest. A callback lease lets the callback run
            # outside that lock without allowing it to begin after stop.
            generation = self._request_stop_locked()
            thread = self._thread
            server = self.server
            self._wait_for_callback_leases_locked(generation)
        if thread is threading.current_thread():
            # A documented advisory callback may stop reentrantly from the
            # maintenance worker. It cannot join itself, so retain every owner
            # and hand cleanup to one bounded retry thread after the callback
            # returns.
            with self._callback_condition:
                self._ensure_stop_retry_locked(generation)
            return False
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if thread is not None and thread.is_alive():
            return False
        if server is not None:
            try:
                server_stopped = server.stop()
            except Exception:  # noqa: BLE001 - retain owner for explicit retry
                LOGGER.exception("Host peer server stop could not be confirmed")
                return False
            if server_stopped is False:
                return False
        with self._callback_condition:
            # Do not let a late stop of an old lifecycle erase a new one.
            if self.server is server and self._thread is thread:
                self._thread = None
                self.server = None
                self.registry = None
                self.control = None
                self.transfers = None
                self.credentials = None
                self.host_enrollment = None
                self._root = None
                self._registered_takes.clear()
                self._expected_by_take.clear()
                self._take_reconcile_locks.clear()
                if self._stop_requested_generation == generation:
                    self._stop_requested_generation = None
        return True

    def _caller_holds_callback_lease_locked(self) -> bool:
        """Return whether this thread must unwind before stop can finish."""

        caller = threading.get_ident()
        return any(
            thread_id == caller and count > 0
            for (_generation, thread_id), count in self._callback_leases.items()
        )

    def _request_stop_locked(self) -> int:
        """Invalidate the owned lifecycle once and wake its worker."""

        generation = self._stop_requested_generation
        if generation is None:
            generation = self._lifecycle_generation
            self._stop_requested_generation = generation
            self._lifecycle_generation += 1
        self._stop_event.set()
        self._maintenance_wake.set()
        return generation

    def _ensure_stop_retry_locked(self, generation: int) -> None:
        """Start at most one bounded deferred attempt for this lifecycle."""

        retry = self._stop_retry_thread
        if (
            retry is not None
            and retry.is_alive()
            and self._stop_retry_generation == generation
        ):
            return
        retry = threading.Thread(
            target=self._retry_stop_after_worker,
            args=(generation,),
            name="webjam-host-transfer-stop-retry",
            daemon=True,
        )
        self._stop_retry_thread = retry
        self._stop_retry_generation = generation
        retry.start()

    def _retry_stop_after_worker(self, generation: int) -> None:
        """Finish a reentrant stop after its maintenance callback unwinds."""

        try:
            with self._stop_lock:
                self._stop_once(expected_generation=generation)
        except Exception:  # noqa: BLE001 - explicit callers can still retry
            LOGGER.exception("Deferred host peer stop could not be confirmed")
        finally:
            with self._lock:
                if self._stop_retry_thread is threading.current_thread():
                    self._stop_retry_thread = None
                    self._stop_retry_generation = None

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

    def presence_for_channel(self, channel_id: int) -> PresenceBinding | None:
        """Return authenticated live-channel evidence, including generation."""

        if self.registry is None:
            return None
        return self.registry.presence_for_channel(channel_id)

    def reconcile_presence_channels(self, active_channel_ids: Iterable[int]) -> int:
        """Retire peer bindings absent from the current primary roster."""

        if self.registry is None:
            return 0
        return self.registry.reconcile_presence_channels(active_channel_ids)

    def begin_take(
        self, take_id: str, *, started_utc: str
    ) -> SessionStateSnapshot | None:
        if self.control is None:
            return None
        snapshot = self.control.begin(take_id, started_utc=started_utc)
        if take_id in self._expected_by_take:
            return snapshot
        expected: list[str] = []
        if self.registry is not None:
            host_id = (
                self.host_enrollment.participant_id if self.host_enrollment else ""
            )
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
        """Queue immediate inventory maintenance without blocking the caller.

        A finished take can contain long Local Originals.  Their checksum,
        copy, and timing analysis belong to the owned maintenance lifecycle,
        not the recorder completion/UI path.  Callers that deliberately need
        synchronous maintenance can still call :meth:`reconcile_take`.
        """

        try:
            canonical_take = str(uuid.UUID(str(take_id)))
        except (ValueError, TypeError, AttributeError):
            return
        with self._lock:
            self._registered_takes[canonical_take] = Path(take_dir).resolve()
            wake_event = self._maintenance_wake
        wake_event.set()

    def _lifecycle_is_current_locked(
        self,
        generation: int,
        stop_event: threading.Event,
    ) -> bool:
        return bool(
            self._lifecycle_generation == generation
            and self._stop_event is stop_event
            and not stop_event.is_set()
        )

    def _lifecycle_is_current(
        self,
        generation: int,
        stop_event: threading.Event,
    ) -> bool:
        with self._lock:
            return self._lifecycle_is_current_locked(generation, stop_event)

    def _begin_callback_lease_locked(
        self,
        generation: int,
        stop_event: threading.Event,
    ) -> Callable[[str, Path, bool], None] | None:
        """Claim a current-lifecycle callback before releasing ``_lock``."""

        if not self._lifecycle_is_current_locked(generation, stop_event):
            return None
        callback = self._on_take_updated
        if callback is None:
            return None
        key = (generation, threading.get_ident())
        self._callback_leases[key] = self._callback_leases.get(key, 0) + 1
        return callback

    def _end_callback_lease(self, generation: int) -> None:
        with self._callback_condition:
            key = (generation, threading.get_ident())
            count = self._callback_leases.get(key, 0)
            if count <= 1:
                self._callback_leases.pop(key, None)
            else:
                self._callback_leases[key] = count - 1
            self._callback_condition.notify_all()

    def _wait_for_callback_leases_locked(self, generation: int) -> None:
        """Wait for other in-flight current callbacks without self-deadlock."""

        own_key = (generation, threading.get_ident())
        own_leases = self._callback_leases.get(own_key, 0)
        while (
            sum(
                count
                for (lease_generation, _thread_id), count in self._callback_leases.items()
                if lease_generation == generation
            )
            > own_leases
        ):
            self._callback_condition.wait()

    def _maintenance_loop(
        self,
        generation: int,
        stop_event: threading.Event,
        wake_event: threading.Event,
    ) -> None:
        while True:
            wake_event.wait(_POLL_SECONDS)
            with self._lock:
                # Clear while holding the same lock used by register_take so
                # a registration racing this pass cannot lose its wake-up.
                wake_event.clear()
                if not self._lifecycle_is_current_locked(generation, stop_event):
                    return
                registered = tuple(self._registered_takes.items())
            for take_id, take_dir in registered:
                if not self._lifecycle_is_current(generation, stop_event):
                    return
                try:
                    self.reconcile_take(
                        take_id,
                        take_dir,
                        _generation=generation,
                        _stop_event=stop_event,
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not refresh peer transfer inventory")

    def reconcile_take(
        self,
        take_id: str,
        take_dir: str | Path,
        *,
        _generation: int | None = None,
        _stop_event: threading.Event | None = None,
    ) -> bool:
        """Attach verified media without losing a competing take update.

        Reconciliation is deliberately allowed to take time while it verifies
        blobs and analyzes audio. A per-take lock serializes this peer service's
        own passes; an optimistic manifest revision/byte check detects a
        recorder or other writer that updated the project meanwhile and retries
        from fresh immutable facts instead of publishing a stale replacement.
        """

        with self._lock:
            take_lock = self._take_reconcile_locks.setdefault(
                str(take_id), threading.RLock()
            )
        with take_lock:
            for attempt in range(_MANIFEST_RECONCILE_MAX_ATTEMPTS):
                result = self._reconcile_take_once(
                    take_id,
                    take_dir,
                    _generation=_generation,
                    _stop_event=_stop_event,
                )
                if result is not _RECONCILE_RETRY:
                    return bool(result)
                LOGGER.info(
                    "Take manifest changed during peer reconciliation; retrying "
                    "attempt %s/%s",
                    attempt + 1,
                    _MANIFEST_RECONCILE_MAX_ATTEMPTS,
                )
        LOGGER.warning(
            "Take manifest kept changing during peer reconciliation; leaving "
            "the next maintenance pass to retry safely"
        )
        return False

    def _reconcile_take_once(
        self,
        take_id: str,
        take_dir: str | Path,
        *,
        _generation: int | None = None,
        _stop_event: threading.Event | None = None,
    ) -> bool | object:
        """Run one optimistic reconciliation pass; return retry sentinel on drift."""

        with self._lock:
            generation = (
                self._lifecycle_generation if _generation is None else _generation
            )
            stop_event = self._stop_event if _stop_event is None else _stop_event
            if (
                not self._lifecycle_is_current_locked(generation, stop_event)
                or self.transfers is None
                or self.registry is None
            ):
                return False
            transfers = self.transfers
            registry = self.registry
            expected_ids = self._expected_by_take.get(take_id, ())
        from core.take_project import (
            AlignmentState,
            MediaSegment,
            MediaStatus,
            Participant,
            GapInterval,
            ProjectStatus,
            ProjectTrack,
            RecoveryStatus,
            SessionTimelineEvent,
            SourceQuality,
            SourceType,
            load_take_project,
            replace_take_project_manifest_if_unchanged,
        )

        folder = Path(take_dir).resolve()
        manifest = folder / "webjam-take.json"
        if not manifest.is_file():
            return False
        try:
            manifest_before = manifest.read_bytes()
            manifest_before_payload = json.loads(manifest_before)
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(manifest_before_payload, dict):
            return False
        base_manifest_revision = int(manifest_before_payload.get("revision", 0) or 0)
        project = load_take_project(folder)
        if project.take_id != take_id:
            return False
        inventory = transfers.inventory(take_id)
        display_by_id = {
            item.participant_id: item.display_name
            for item in registry.participants()
        }
        expected = set(expected_ids)
        expected.update(item.descriptor.participant_id for item in inventory)
        received_by_participant: dict[str, list] = {}
        for item in inventory:
            received_by_participant.setdefault(
                item.descriptor.participant_id, []
            ).append(item)

        participants = {item.participant_id: item for item in project.participants}
        tracks_by_id = {track.track_id: track for track in project.tracks}
        segment_ids = {
            segment.segment_id for track in project.tracks for segment in track.segments
        }
        if not self._lifecycle_is_current(generation, stop_event):
            return False
        attached_dir = folder / "transferred-isolated"
        attached_dir.mkdir(exist_ok=True)
        transfer_errors: list[str] = []
        transfer_summary: list[dict] = []
        next_order = max((item.order for item in project.tracks), default=-1) + 1
        attached_new_media = False

        for participant_id in sorted(expected):
            if not self._lifecycle_is_current(generation, stop_event):
                return False
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
                if not self._lifecycle_is_current(generation, stop_event):
                    return False
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
                        "gaps": [gap.to_mapping() for gap in descriptor.gaps],
                        "errors": list(descriptor.capture_errors),
                        "alignment": {
                            "status": "waiting_for_verified_attachment",
                            "timing_ready": False,
                        },
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
                if (
                    not destination.is_file()
                    or _sha256_file(destination) != descriptor.sha256
                ):
                    if not self._lifecycle_is_current(generation, stop_event):
                        return False
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
                    with self._lock:
                        if not self._lifecycle_is_current_locked(
                            generation, stop_event
                        ):
                            temporary.unlink(missing_ok=True)
                            return False
                        os.replace(temporary, destination)
                track_id, source_id, project_segment_id = _peer_project_media_ids(
                    take_id,
                    participant_id,
                    descriptor.segment_id,
                )
                legacy_track = next(
                    (
                        track
                        for track in tracks_by_id.values()
                        if _is_legacy_peer_attachment(
                            track,
                            take_id=take_id,
                            participant_id=participant_id,
                            transfer_segment_id=descriptor.segment_id,
                            source_type=SourceType.LOCAL_ISOLATED,
                        )
                    ),
                    None,
                )
                legacy_attached = legacy_track is not None
                attached_track = tracks_by_id.get(track_id) or legacy_track
                if project_segment_id not in segment_ids and not legacy_attached:
                    attached_new_media = True
                    media_status = (
                        MediaStatus.PARTIAL
                        if descriptor.capture_errors or descriptor.gap_frames
                        else MediaStatus.AVAILABLE
                    )
                    relative = destination.relative_to(folder).as_posix()
                    track = ProjectTrack(
                        track_id=track_id,
                        source_id=source_id,
                        participant_id=participant_id,
                        name=f"{name} Input {descriptor.source_channel + 1}",
                        instrument="",
                        source_type=SourceType.LOCAL_ISOLATED,
                        quality=SourceQuality.UNVERIFIED,
                        media_status=media_status,
                        order=next_order,
                        segments=(
                            MediaSegment(
                                segment_id=project_segment_id,
                                path=relative,
                                project_start_frame=0,
                                frame_count=descriptor.frame_count,
                                sample_rate=descriptor.sample_rate,
                                channels=descriptor.channels,
                                sample_format=descriptor.subtype,
                                media_status=media_status,
                                sha256=descriptor.sha256,
                                device_id="",
                                # Only structured records carry a position.
                                # Older descriptors may disclose aggregate
                                # ``gap_frames``; retain their partial status
                                # without guessing where audio was absent.
                                gaps=tuple(
                                    GapInterval(
                                        gap.start_frame,
                                        gap.frame_count,
                                        gap.reason,
                                        gap.channels,
                                    )
                                    for gap in descriptor.gaps
                                ),
                                size_bytes=descriptor.size_bytes,
                                has_signal=None,
                            ),
                        ),
                        alignment=AlignmentState(
                            method=_PEER_ALIGNMENT_INITIAL_METHOD
                        ),
                    )
                    tracks_by_id[track.track_id] = track
                    segment_ids.add(project_segment_id)
                    next_order += 1
                    attached_track = track

                # Transport verification does not prove that a guest's clock
                # shares the server take timeline.  Only attempt automatic
                # timing after the immutable attachment exists and its hash has
                # been checked above.  The reference must be the one verified
                # Jamulus server track assigned to this same enrolled musician;
                # never borrow another participant's audio or choose among
                # ambiguous candidates.
                if attached_track is None:
                    segment_summaries[-1]["alignment"] = {
                        "status": "uncertain",
                        "timing_ready": False,
                        "reason": "The attached local original could not be identified in the take project.",
                    }
                else:
                    alignment_method = str(
                        attached_track.alignment.method or ""
                    ).strip().lower()
                    manual_nudge = float(attached_track.alignment.manual_nudge_s)
                    retrying_reference = alignment_method.startswith(
                        _PEER_ALIGNMENT_WAITING_PREFIX
                    )
                    if (
                        (
                            alignment_method == _PEER_ALIGNMENT_INITIAL_METHOD
                            or retrying_reference
                        )
                        and not manual_nudge
                    ):
                        alignment_reason = ""
                        uncertainty_code = ""
                        reference_track = None
                        if descriptor.capture_errors or descriptor.gap_frames:
                            alignment_reason = (
                                "The local original has declared capture gaps or errors."
                            )
                            uncertainty_code = "incomplete-source"
                        elif not _track_media_is_verified(attached_track, folder):
                            alignment_reason = (
                                "The attached local original no longer matches its recorded checksum."
                            )
                            uncertainty_code = "attachment-checksum-mismatch"
                        else:
                            reference_track = _same_participant_reference_track(
                                tuple(tracks_by_id.values()),
                                participant_id=participant_id,
                                take_root=folder,
                            )
                            if reference_track is None:
                                alignment_reason = (
                                    "No verified same-participant Jamulus server reference is available."
                                )
                                uncertainty_code = (
                                    "no-verified-same-participant-reference"
                                )

                        if reference_track is None:
                            waiting_for_reference = (
                                uncertainty_code
                                == "no-verified-same-participant-reference"
                            )
                            alignment = AlignmentState(
                                method=(
                                    (
                                        _PEER_ALIGNMENT_WAITING_PREFIX
                                        if waiting_for_reference
                                        else _PEER_ALIGNMENT_UNCERTAIN_PREFIX
                                    )
                                    + uncertainty_code
                                )
                            )
                            attached_track = replace(
                                attached_track,
                                quality=SourceQuality.UNVERIFIED,
                                alignment=alignment,
                            )
                            tracks_by_id[attached_track.track_id] = attached_track
                            segment_summaries[-1]["alignment"] = _peer_alignment_summary(
                                attached_track,
                                status=(
                                    "waiting_for_reference"
                                    if waiting_for_reference
                                    else "uncertain"
                                ),
                                timing_ready=False,
                                reason=alignment_reason,
                            )
                        else:
                            try:
                                # Importing the analysis path here is
                                # intentional: no decode/scan work occurs for
                                # incomplete or unverified transfer media.
                                from core.take_alignment import (
                                    AlignmentOutcome,
                                    align_project_tracks,
                                )

                                result = align_project_tracks(
                                    folder,
                                    reference_track,
                                    attached_track,
                                    project_sample_rate=project.project_sample_rate,
                                )
                            except Exception:  # noqa: BLE001
                                LOGGER.exception(
                                    "Could not analyze verified guest original alignment"
                                )
                                result = None

                            if result is not None and (
                                result.outcome is AlignmentOutcome.ALIGNED
                                and result.state.confidence
                                >= _PEER_ALIGNMENT_MIN_CONFIDENCE
                                and result.state.residual_ms
                                <= _PEER_ALIGNMENT_MAX_RESIDUAL_MS
                                and len(result.state.anchors)
                                >= _PEER_ALIGNMENT_MIN_ANCHORS
                                and not result.issues
                            ):
                                alignment = replace(
                                    result.state,
                                    method=(
                                        _PEER_ALIGNMENT_VERIFIED_PREFIX
                                        + result.state.method
                                    ),
                                    reference_track_id=reference_track.track_id,
                                    reference_fingerprint_sha256=_reference_fingerprint(
                                        reference_track
                                    ),
                                )
                                attached_track = replace(
                                    attached_track,
                                    quality=SourceQuality.VERIFIED_ISOLATED,
                                    alignment=alignment,
                                )
                                tracks_by_id[attached_track.track_id] = attached_track
                                segment_summaries[-1]["alignment"] = _peer_alignment_summary(
                                    attached_track,
                                    status="aligned",
                                    timing_ready=True,
                                    reason="Strong shared transient evidence verified this local original against its server track.",
                                )
                            else:
                                if result is None:
                                    alignment = AlignmentState(
                                        method=(
                                            _PEER_ALIGNMENT_UNCERTAIN_PREFIX
                                            + "analysis-unavailable"
                                        )
                                    )
                                    alignment_reason = (
                                        "WebJam could not read enough timing evidence to verify this local original."
                                    )
                                else:
                                    alignment = replace(
                                        result.state,
                                        method=(
                                            _PEER_ALIGNMENT_UNCERTAIN_PREFIX
                                            + result.state.method
                                        ),
                                    )
                                    alignment_reason = (
                                        "The shared timing evidence did not pass "
                                        "WebJam's strong verification gate."
                                    )
                                attached_track = replace(
                                    attached_track,
                                    quality=SourceQuality.UNVERIFIED,
                                    alignment=alignment,
                                )
                                tracks_by_id[attached_track.track_id] = attached_track
                                segment_summaries[-1]["alignment"] = _peer_alignment_summary(
                                    attached_track,
                                    status="uncertain",
                                    timing_ready=False,
                                    reason=alignment_reason,
                                )
                    elif alignment_method.startswith(_PEER_ALIGNMENT_VERIFIED_PREFIX):
                        segment_summaries[-1]["alignment"] = _peer_alignment_summary(
                            attached_track,
                            status="aligned",
                            timing_ready=(
                                attached_track.quality
                                is SourceQuality.VERIFIED_ISOLATED
                            ),
                            reason="Strong shared transient evidence verified this local original against its server track.",
                        )
                    elif manual_nudge:
                        segment_summaries[-1]["alignment"] = _peer_alignment_summary(
                            attached_track,
                            status="manual_review",
                            timing_ready=False,
                            reason="A manual timing adjustment is preserved for Studio review.",
                        )
                    else:
                        if alignment_method.startswith(_PEER_ALIGNMENT_WAITING_PREFIX):
                            alignment_reason = (
                                "No verified same-participant Jamulus server reference is available."
                            )
                            alignment_status = "waiting_for_reference"
                        elif alignment_method.endswith("incomplete-source"):
                            alignment_reason = (
                                "The local original has declared capture gaps or errors."
                            )
                            alignment_status = "uncertain"
                        elif alignment_method.endswith("attachment-checksum-mismatch"):
                            alignment_reason = (
                                "The attached local original no longer matches its recorded checksum."
                            )
                            alignment_status = "uncertain"
                        elif alignment_method.endswith(
                            "no-verified-same-participant-reference"
                        ):
                            alignment_reason = (
                                "No verified same-participant Jamulus server reference is available."
                            )
                            alignment_status = "waiting_for_reference"
                        elif alignment_method.endswith("analysis-unavailable"):
                            alignment_reason = (
                                "WebJam could not read enough timing evidence to verify this local original."
                            )
                            alignment_status = "uncertain"
                        else:
                            alignment_reason = (
                                "The shared timing evidence did not pass WebJam's "
                                "strong verification gate."
                            )
                            alignment_status = "uncertain"
                        segment_summaries[-1]["alignment"] = _peer_alignment_summary(
                            attached_track,
                            status=alignment_status,
                            timing_ready=False,
                            reason=alignment_reason,
                        )
                if descriptor.capture_errors or descriptor.gap_frames:
                    transfer_errors.append(
                        f"{_TRANSFER_ERROR_PREFIX}{name}'s local original needs attention."
                    )
            if participant_items:
                if all(item.complete for item in participant_items):
                    participant_status = (
                        "needs_attention"
                        if any(
                            item.descriptor.capture_errors or item.descriptor.gap_frames
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
            item
            for item in project.errors
            if not item.startswith(_TRANSFER_ERROR_PREFIX)
        )
        had_transfer_attention = any(
            item.startswith(_TRANSFER_ERROR_PREFIX) for item in project.errors
        )
        errors = tuple(dict.fromkeys((*base_errors, *transfer_errors)))
        status = project.status
        if errors:
            status = ProjectStatus.NEEDS_ATTENTION
        elif not base_errors and status is ProjectStatus.NEEDS_ATTENTION:
            status = ProjectStatus.COMPLETE
        evidence = project.session_evidence
        if had_transfer_attention and not transfer_errors:
            timeline = list(evidence.timeline)
            recovered_event = SessionTimelineEvent(
                "peer_original_recovered",
                occurred_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                detail="A verified guest local original arrived after take validation.",
            )
            if recovered_event not in timeline:
                timeline.append(recovered_event)
            recovery_status = evidence.recovery_status
            if recovery_status is not RecoveryStatus.NEEDS_ATTENTION:
                recovery_status = RecoveryStatus.RECOVERED
            recovery_notes = tuple(
                dict.fromkeys(
                    (
                        *evidence.recovery_notes,
                        "A verified guest local original arrived after take validation.",
                    )
                )
            )
            evidence = replace(
                evidence,
                recovery_status=recovery_status,
                recovery_notes=recovery_notes,
                timeline=tuple(timeline),
            )
        updated = replace(
            project,
            status=status,
            participants=tuple(participants.values()),
            tracks=tuple(tracks_by_id.values()),
            errors=errors,
            session_evidence=evidence,
            revision=project.revision + 1,
        )
        if not self._lifecycle_is_current(generation, stop_event):
            return False
        # Avoid rewriting the manifest every maintenance tick if truth did not change.
        try:
            prior_bytes = manifest.read_bytes()
            prior_payload = json.loads(prior_bytes)
        except (OSError, ValueError, TypeError):
            return _RECONCILE_RETRY
        if (
            prior_bytes != manifest_before
            or not isinstance(prior_payload, dict)
            or int(prior_payload.get("revision", 0) or 0)
            != base_manifest_revision
        ):
            return _RECONCILE_RETRY
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
        with self._callback_condition:
            if not self._lifecycle_is_current_locked(generation, stop_event):
                return False
            if not replace_take_project_manifest_if_unchanged(
                folder,
                expected_bytes=prior_bytes,
                payload=payload,
            ):
                return _RECONCILE_RETRY
            callback = self._begin_callback_lease_locked(generation, stop_event)
        if callback is not None:
            try:
                callback(take_id, folder, attached_new_media)
            except Exception:  # noqa: BLE001
                # UI notification is advisory. Never turn a successfully
                # verified/attached original back into a transfer failure.
                LOGGER.exception("Could not publish peer take update")
            finally:
                self._end_callback_lease(generation)
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
        on_guidance_changed: Callable[[], None] | None = None,
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
        self._on_guidance_changed = on_guidance_changed
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
        self._presence_observation_epoch = 0
        self._capture = None
        self._active_take_id = ""
        self._capture_started_config: tuple[int, int, int] | None = None
        self._pending: list[PendingLocalSegment] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self.queue_path = (
            self.takes_root
            / "WebJam Local Originals"
            / invite.session_id
            / "webjam-transfer-queue.json"
        )
        self.queue_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.queue_path.parent, 0o700)
        from core.local_capture import recover_stale_local_captures

        self.recovered_captures = recover_stale_local_captures(self.queue_path.parent)
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
        # A retained dead worker can mean a prior bounded stop timed out and
        # still owes capture finalization/upload. Only stop() may clear that
        # owner; never replace it with a new transfer lifecycle.
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="webjam-guest-recording-transfer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> bool:
        """Stop only after the transfer worker is proven gone."""

        with self._stop_lock:
            self._stop_event.set()
            thread = self._thread
            if thread is threading.current_thread():
                return False
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
            if thread is not None and thread.is_alive():
                return False
            self._thread = None
            # A quit/network leave never deletes or aborts an active original.
            self._finalize_capture(
                needs_attention="Session ended before host stop was observed."
            )
            try:
                self._upload_pending()
            except SessionTransferError:
                pass
            return True

    def observe_presence(self, channel_id: int, display_name: str) -> None:
        desired = (
            int(channel_id),
            " ".join(str(display_name).split())[:80] or self.display_name,
            bool(self.capture_enabled()),
        )
        with self._lock:
            self._desired_presence = desired
            self._presence_observation_epoch += 1
            # The host retires a binding as soon as this channel disappears
            # from its process-authenticated Jamulus roster.  A subsequent
            # roster appearance must therefore publish a fresh signed
            # generation even when Jamulus happens to reuse the same channel,
            # name, and capture preference.
            self._bound_presence = None

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
            observation_epoch = self._presence_observation_epoch
        if desired is None or desired == bound:
            return
        self._presence_generation = max(time.time_ns(), self._presence_generation + 1)
        self.client.bind_presence(
            self.enrollment,
            channel_id=desired[0],
            display_name=desired[1],
            generation=self._presence_generation,
            capture_enabled=desired[2],
        )
        with self._lock:
            # A newer process-authenticated roster observation may have
            # arrived while the signed request was in flight. In that case the
            # completed request cannot satisfy the newer proof obligation;
            # leave it pending so the next poll publishes another generation.
            if (
                self._presence_observation_epoch == observation_epoch
                and self._desired_presence == desired
            ):
                self._bound_presence = desired

    def _new_capture(
        self,
        root: Path,
        device: int,
        rate: int,
        blocksize: int,
        *,
        take_id: str,
    ):
        if self.capture_factory is not None:
            return self.capture_factory(
                root,
                device=device,
                samplerate=rate,
                blocksize=blocksize,
                take_id=take_id,
                session_id=self.invite.session_id,
            )
        from core.local_capture import LocalInputCapture

        return LocalInputCapture(
            root,
            device=device,
            samplerate=rate,
            blocksize=blocksize,
            take_id=take_id,
            session_id=self.invite.session_id,
        )

    def _start_capture(self, take_id: str) -> None:
        if self._capture is not None:
            if self._active_take_id == take_id:
                return
            # A different take cannot overwrite an unfinalized local original.
            self._finalize_capture(
                needs_attention="A new take started before the prior stop."
            )
        device, rate, blocksize = self.capture_config()
        originals = self.queue_path.parent
        originals.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(originals, 0o700)
        capture = self._new_capture(
            originals,
            int(device),
            int(rate),
            int(blocksize),
            take_id=take_id,
        )
        capture.start()
        self._capture = capture
        self._active_take_id = take_id
        self._capture_started_config = (int(device), int(rate), int(blocksize))
        self._notify_guidance_changed()

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
        if (
            self._capture_started_config
            and current_config[:2] != self._capture_started_config[:2]
        ):
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
            channel_gaps: list[TransferGap] = []
            for gap in gaps:
                raw_channels = getattr(gap, "channels", ())
                try:
                    source_channels = tuple(int(item) for item in raw_channels)
                except (TypeError, ValueError):
                    errors.append(
                        "A local capture gap had an invalid channel map and "
                        "could not be represented safely."
                    )
                    continue
                if source_channels and channel not in source_channels:
                    continue
                try:
                    # Each local-original file is a self-contained source
                    # segment. Preserve the exact source-frame interval, but
                    # map it to that file's media channels (normally mono),
                    # rather than leaking the capture device's channel index.
                    channel_gaps.append(
                        TransferGap(
                            start_frame=getattr(gap, "start_frame", -1),
                            frame_count=getattr(gap, "frame_count", 0),
                            channels=tuple(range(int(info.channels))),
                            reason=str(getattr(gap, "reason", "")),
                        )
                    )
                except (TypeError, ValueError):
                    errors.append(
                        "A local capture gap could not be represented safely."
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
                capture_errors=tuple(dict.fromkeys(errors)),
                gaps=tuple(channel_gaps),
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

    def _notify_guidance_changed(self) -> None:
        callback = self._on_guidance_changed
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - guidance cannot affect transfer
            LOGGER.exception("Could not publish Local Originals guidance")

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
            self._notify_guidance_changed()

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
            if (
                payload.get("schema") != 1
                or payload.get("session_id") != self.invite.session_id
            ):
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
            raise SessionTransferError(
                "The local transfer queue is unreadable."
            ) from exc
        with self._lock:
            self._pending = loaded
        try:
            os.chmod(self.queue_path, 0o600)
        except OSError:
            pass
