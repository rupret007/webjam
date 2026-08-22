"""Production ownership for WebJam's private recording transfer plane.

Jamulus remains the live-audio transport.  These two small runtimes own only
durable enrollment, recording-state observation, local isolated originals,
and resumable delivery.  A peer outage never stops an active local capture.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.network_invite import BandInvite, create_invite_link
from core.session_transfer import (
    CaptureArmAcknowledgement,
    CaptureArmSnapshot,
    EnrollmentRegistry,
    LocalOriginalObligation,
    ParticipantEnrollment,
    PresenceBinding,
    PresenceV2Challenge,
    PresenceV2Proof,
    RecordingSignal,
    ReferenceVideoPlaybackState,
    ReferenceVideoSessionSnapshot,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionStateSnapshot,
    RoomClockSessionSnapshot,
    RoomClockSourceValue,
    SessionTransferError,
    SharedCanvasSessionSnapshot,
    SharedTrackPlaybackState,
    SharedTrackSessionSnapshot,
    TransferConflictError,
    TransferDescriptor,
    TransferGap,
    TransferIntegrityError,
    TransferStore,
    _presence_digest_text,
    _presence_fingerprint_text,
    _presence_int,
    _presence_ordinal_tuple,
    _sha256_file,
    _write_json_secure,
    derive_participant_id,
    load_or_create_installation_id,
)

LOGGER = logging.getLogger("webjam.session_transfer")
_POLL_SECONDS = 0.75
PEER_TRANSFER_ERROR_PREFIX = "Peer isolated recording: "

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
_ZERO_LOCAL_ORIGINAL_MAP_FINGERPRINT = hashlib.sha256(
    b"webjam-local-original-map-v1:disabled"
).hexdigest()


@dataclass(frozen=True, slots=True)
class _ParticipantInventoryDisposition:
    """Bounded truth about one guest's declared take inventory."""

    status: str
    input_count: int = 0
    segment_count: int = 0
    issue: str = ""

    @property
    def settled(self) -> bool:
        return self.status in {"complete", "needs_attention"}


def _participant_inventory_disposition(
    items: Iterable[object],
    obligation: LocalOriginalObligation | None = None,
) -> _ParticipantInventoryDisposition:
    """Validate one participant's immutable input/segment declaration.

    A descriptor from an older peer has zero counts. It remains wire-readable
    and its verified media can still be preserved, but it cannot prove that no
    additional local input is still waiting to be declared.
    """

    records = tuple(items)
    if obligation is not None and obligation.exact:
        expected_count = int(obligation.track_count or 0)
        if expected_count == 0:
            if records:
                return _ParticipantInventoryDisposition(
                    "needs_attention",
                    issue=(
                        "local original uploaded media despite its pre-take "
                        "zero-track obligation."
                    ),
                )
            return _ParticipantInventoryDisposition("complete")
        if not records:
            return _ParticipantInventoryDisposition(
                "missing",
                input_count=expected_count,
                segment_count=expected_count,
                issue="declared local-original inventory has not arrived.",
            )
    if not records:
        return _ParticipantInventoryDisposition("missing")
    declarations = {
        (
            int(getattr(item.descriptor, "inventory_input_count", 0) or 0),
            int(getattr(item.descriptor, "inventory_segment_count", 0) or 0),
        )
        for item in records
    }
    if declarations == {(0, 0)}:
        return _ParticipantInventoryDisposition(
            "needs_attention",
            issue=(
                "local original did not declare its complete input and segment "
                "inventory."
            ),
        )
    if (0, 0) in declarations or len(declarations) != 1:
        return _ParticipantInventoryDisposition(
            "needs_attention",
            issue="local original declared contradictory take inventories.",
        )
    input_count, segment_count = next(iter(declarations))
    if obligation is not None and obligation.exact:
        expected_count = int(obligation.track_count or 0)
        if input_count != expected_count or segment_count != expected_count:
            return _ParticipantInventoryDisposition(
                "needs_attention",
                input_count=input_count,
                segment_count=segment_count,
                issue=(
                    "local original did not match its pre-take logical-track "
                    "obligation."
                ),
            )
        fingerprints = {
            str(
                getattr(
                    item.descriptor,
                    "inventory_map_fingerprint",
                    "",
                )
                or ""
            )
            for item in records
        }
        if fingerprints != {obligation.map_fingerprint}:
            return _ParticipantInventoryDisposition(
                "needs_attention",
                input_count=input_count,
                segment_count=segment_count,
                issue=(
                    "local original did not match its pre-take input-map fingerprint."
                ),
            )
    if len(records) < segment_count:
        return _ParticipantInventoryDisposition(
            "receiving",
            input_count=input_count,
            segment_count=segment_count,
            issue="declared local-original inventory has not fully arrived.",
        )
    if len(records) > segment_count:
        return _ParticipantInventoryDisposition(
            "needs_attention",
            input_count=input_count,
            segment_count=segment_count,
            issue="local original exceeded its declared segment inventory.",
        )
    source_channels = {
        int(getattr(item.descriptor, "source_channel", -1)) for item in records
    }
    if source_channels != set(range(input_count)):
        return _ParticipantInventoryDisposition(
            "needs_attention",
            input_count=input_count,
            segment_count=segment_count,
            issue="local original did not represent every declared input.",
        )
    if any(
        (width := getattr(item.descriptor, "channels", None)) is not None
        and int(width) not in {1, 2}
        for item in records
    ):
        return _ParticipantInventoryDisposition(
            "needs_attention",
            input_count=input_count,
            segment_count=segment_count,
            issue="local original contained an unsupported channel layout.",
        )
    if obligation is not None and obligation.exact_topology:
        by_ordinal = {
            int(item.descriptor.source_channel): item.descriptor for item in records
        }
        for ordinal, (channel_count, logical_source_id) in enumerate(
            zip(obligation.channel_counts, obligation.logical_source_ids, strict=True)
        ):
            descriptor = by_ordinal[ordinal]
            if (
                int(descriptor.channels) != channel_count
                or str(getattr(descriptor, "logical_source_id", ""))
                != logical_source_id
            ):
                return _ParticipantInventoryDisposition(
                    "needs_attention",
                    input_count=input_count,
                    segment_count=segment_count,
                    issue=(
                        "local original did not match its pre-take ordered "
                        "mono/stereo source topology."
                    ),
                )
    return _ParticipantInventoryDisposition(
        "complete",
        input_count=input_count,
        segment_count=segment_count,
    )


def _obligation_contract_key(
    obligations: Iterable[LocalOriginalObligation],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                item.participant_id,
                item.track_count,
                item.map_fingerprint,
                item.capture_requested,
                item.channel_counts,
                item.logical_source_ids,
            )
            for item in obligations
        )
    )


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
        getattr(track, "segments", ()) or (),
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


@dataclass(frozen=True, repr=False)
class _DesiredPresenceV2:
    display_name: str
    ordered_roster_digest: str
    roster_count: int
    self_ordinal: int
    process_generation: int
    rpc_connection_generation: int
    audio_connection_generation: int
    capture_enabled: bool
    local_original_track_count: int | None = None
    local_original_map_fingerprint: str = ""
    local_original_channel_counts: tuple[int, ...] = ()
    local_original_source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        digest = _presence_digest_text(self.ordered_roster_digest)
        count = _presence_int(self.roster_count, "roster_count", positive=True)
        if count > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("roster_count exceeds the supported limit.")
        ordinal = _presence_int(self.self_ordinal, "self_ordinal")
        if ordinal >= count:
            raise ValueError("self_ordinal must identify a roster row.")
        object.__setattr__(
            self,
            "display_name",
            " ".join(str(self.display_name).split())[:80] or "Musician",
        )
        object.__setattr__(self, "ordered_roster_digest", digest)
        object.__setattr__(self, "roster_count", count)
        object.__setattr__(self, "self_ordinal", ordinal)
        object.__setattr__(
            self,
            "process_generation",
            _presence_int(self.process_generation, "process_generation", positive=True),
        )
        object.__setattr__(
            self,
            "rpc_connection_generation",
            _presence_int(
                self.rpc_connection_generation,
                "rpc_connection_generation",
                positive=True,
            ),
        )
        object.__setattr__(
            self,
            "audio_connection_generation",
            _presence_int(
                self.audio_connection_generation,
                "audio_connection_generation",
                positive=True,
            ),
        )
        if type(self.capture_enabled) is not bool:
            raise ValueError("capture_enabled must be a boolean.")
        contract = LocalOriginalObligation(
            participant_id="00000000-0000-0000-0000-000000000000",
            track_count=self.local_original_track_count,
            map_fingerprint=self.local_original_map_fingerprint,
            capture_requested=self.capture_enabled,
            channel_counts=self.local_original_channel_counts,
            logical_source_ids=self.local_original_source_ids,
        )
        object.__setattr__(self, "local_original_track_count", contract.track_count)
        object.__setattr__(
            self,
            "local_original_map_fingerprint",
            contract.map_fingerprint,
        )
        object.__setattr__(
            self, "local_original_channel_counts", contract.channel_counts
        )
        object.__setattr__(
            self, "local_original_source_ids", contract.logical_source_ids
        )

    def __repr__(self) -> str:
        return "_DesiredPresenceV2(private=[redacted])"


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
        self._local_original_obligations_by_take: dict[
            str, tuple[LocalOriginalObligation, ...]
        ] = {}
        self._prepared_local_original_obligation_takes: set[str] = set()
        self._capture_cursor_by_take: dict[str, int] = {}
        self._presence_readiness_issue_by_take: dict[str, str] = {}
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
        self._host_presence_v2_desired: _DesiredPresenceV2 | None = None
        self._host_presence_v2_desired_fingerprint = ""
        self._host_presence_v2_desired_ambiguous_ordinals: tuple[int, ...] = ()
        self._host_presence_v2_generation = 0
        self._recording_roster_key: tuple[object, ...] | None = None
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
        creator_profile_key: str = "music",
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
        control = SessionControlState(
            root,
            credentials.session_id,
            creator_profile_key=creator_profile_key,
        )
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
            self._local_original_obligations_by_take.clear()
            self._prepared_local_original_obligation_takes.clear()
            self._capture_cursor_by_take.clear()
            self._presence_readiness_issue_by_take.clear()
            self._take_reconcile_locks.clear()
            self._host_presence_v2_desired = None
            self._host_presence_v2_desired_fingerprint = ""
            self._host_presence_v2_desired_ambiguous_ordinals = ()
            self._host_presence_v2_generation = 0
            self._recording_roster_key = None
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
            except Exception:
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
                self._local_original_obligations_by_take.clear()
                self._prepared_local_original_obligation_takes.clear()
                self._capture_cursor_by_take.clear()
                self._presence_readiness_issue_by_take.clear()
                self._take_reconcile_locks.clear()
                self._host_presence_v2_desired = None
                self._host_presence_v2_desired_fingerprint = ""
                self._host_presence_v2_desired_ambiguous_ordinals = ()
                self._host_presence_v2_generation = 0
                self._recording_roster_key = None
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
            if self.registry is not None:
                self.registry.invalidate_presence_v2()
            self._host_presence_v2_desired = None
            self._host_presence_v2_desired_fingerprint = ""
            self._host_presence_v2_desired_ambiguous_ordinals = ()
            self._recording_roster_key = None
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
        except Exception:
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

    def install_recording_presence_roster(
        self,
        ordered_roster_digest: str,
        roster_count: int,
        *,
        self_ordinal: int,
        host_roster_fingerprint: str,
        ambiguous_ordinals: tuple[int, ...],
        process_generation: int,
        rpc_connection_generation: int,
        audio_connection_generation: int,
        force_rotate: bool = False,
    ) -> PresenceV2Challenge | None:
        """Bind the v2 challenge to one exact primary-server roster proof."""

        if self.registry is None:
            return None
        ordinal = _presence_int(self_ordinal, "self_ordinal")
        count = _presence_int(roster_count, "roster_count", positive=True)
        if ordinal >= count:
            raise ValueError("self_ordinal must identify a roster row.")
        fingerprint = _presence_fingerprint_text(host_roster_fingerprint)
        ambiguous = _presence_ordinal_tuple(ambiguous_ordinals, roster_count=count)
        challenge = self.registry.install_presence_v2_roster(
            ordered_roster_digest,
            roster_count,
            host_roster_fingerprint=fingerprint,
            ambiguous_ordinals=ambiguous,
            process_generation=process_generation,
            rpc_connection_generation=rpc_connection_generation,
            audio_connection_generation=audio_connection_generation,
            force_rotate=force_rotate,
        )
        key = (
            challenge.ordered_roster_digest,
            challenge.roster_count,
            ordinal,
            _presence_int(process_generation, "process_generation", positive=True),
            _presence_int(
                rpc_connection_generation,
                "rpc_connection_generation",
                positive=True,
            ),
            _presence_int(
                audio_connection_generation,
                "audio_connection_generation",
                positive=True,
            ),
            fingerprint,
            ambiguous,
        )
        with self._lock:
            self._recording_roster_key = key
        self._refresh_host_recording_presence()
        return challenge

    def bind_host_recording_presence(
        self,
        display_name: str,
        *,
        ordered_roster_digest: str,
        roster_count: int,
        self_ordinal: int,
        host_roster_fingerprint: str,
        ambiguous_ordinals: tuple[int, ...],
        process_generation: int,
        rpc_connection_generation: int,
        audio_connection_generation: int,
        challenge: str,
        challenge_epoch: int,
        topology_epoch: int,
        presence_generation: int,
        capture_enabled: bool,
        local_original_track_count: int | None = None,
        local_original_map_fingerprint: str = "",
        local_original_channel_counts: tuple[int, ...] = (),
        local_original_source_ids: tuple[str, ...] = (),
    ) -> PresenceV2Proof | None:
        """Publish the enrolled host's challenge-scoped roster ordinal."""

        if self.registry is None or self.host_enrollment is None:
            return None
        desired = _DesiredPresenceV2(
            display_name=display_name,
            ordered_roster_digest=ordered_roster_digest,
            roster_count=roster_count,
            self_ordinal=self_ordinal,
            process_generation=process_generation,
            rpc_connection_generation=rpc_connection_generation,
            audio_connection_generation=audio_connection_generation,
            capture_enabled=capture_enabled,
            local_original_track_count=local_original_track_count,
            local_original_map_fingerprint=local_original_map_fingerprint,
            local_original_channel_counts=local_original_channel_counts,
            local_original_source_ids=local_original_source_ids,
        )
        fingerprint = _presence_fingerprint_text(host_roster_fingerprint)
        ambiguous = _presence_ordinal_tuple(
            ambiguous_ordinals, roster_count=desired.roster_count
        )
        supplied_generation = _presence_int(
            presence_generation, "presence_generation", positive=True
        )
        expected_key = (
            desired.ordered_roster_digest,
            desired.roster_count,
            desired.self_ordinal,
            desired.process_generation,
            desired.rpc_connection_generation,
            desired.audio_connection_generation,
            fingerprint,
            ambiguous,
        )
        with self._lock:
            if expected_key != self._recording_roster_key:
                raise TransferConflictError(
                    "The host recorder presence does not match its proven roster."
                )
            generation = max(
                time.time_ns(),
                self._host_presence_v2_generation + 1,
                supplied_generation,
            )
            proof = self.registry.bind_presence_v2(
                self.host_enrollment.participant_id,
                desired.display_name,
                ordered_roster_digest=desired.ordered_roster_digest,
                roster_count=desired.roster_count,
                self_ordinal=desired.self_ordinal,
                process_generation=desired.process_generation,
                rpc_connection_generation=desired.rpc_connection_generation,
                audio_connection_generation=desired.audio_connection_generation,
                challenge=challenge,
                challenge_epoch=challenge_epoch,
                topology_epoch=topology_epoch,
                presence_generation=generation,
                capture_enabled=desired.capture_enabled,
                local_original_track_count=desired.local_original_track_count,
                local_original_map_fingerprint=(desired.local_original_map_fingerprint),
                local_original_channel_counts=desired.local_original_channel_counts,
                local_original_source_ids=desired.local_original_source_ids,
                _allow_ambiguous_ordinal=(desired.self_ordinal in ambiguous),
            )
            self._host_presence_v2_generation = generation
            self._host_presence_v2_desired = desired
            self._host_presence_v2_desired_fingerprint = fingerprint
            self._host_presence_v2_desired_ambiguous_ordinals = ambiguous
            return proof

    def _refresh_host_recording_presence(self) -> PresenceV2Proof | None:
        """Renew the host claim after a lease rotation with a fresh generation."""

        with self._lock:
            registry = self.registry
            enrollment = self.host_enrollment
            desired = self._host_presence_v2_desired
            desired_fingerprint = self._host_presence_v2_desired_fingerprint
            desired_ambiguous = self._host_presence_v2_desired_ambiguous_ordinals
            key = self._recording_roster_key
            if registry is None or enrollment is None or desired is None:
                return None
            desired_key = (
                desired.ordered_roster_digest,
                desired.roster_count,
                desired.self_ordinal,
                desired.process_generation,
                desired.rpc_connection_generation,
                desired.audio_connection_generation,
                desired_fingerprint,
                desired_ambiguous,
            )
            if key != desired_key:
                return None
            try:
                challenge = registry.current_presence_v2_challenge()
            except TransferConflictError:
                return None
            current = registry.recording_presence_snapshot(
                ordered_roster_digest=challenge.ordered_roster_digest,
                roster_count=challenge.roster_count,
                challenge=challenge.challenge,
                challenge_epoch=challenge.challenge_epoch,
            )
            for proof in current:
                if (
                    proof.participant_id == enrollment.participant_id
                    and proof.display_name == desired.display_name
                    and proof.self_ordinal == desired.self_ordinal
                    and proof.process_generation == desired.process_generation
                    and proof.rpc_connection_generation
                    == desired.rpc_connection_generation
                    and proof.audio_connection_generation
                    == desired.audio_connection_generation
                    and proof.capture_enabled == desired.capture_enabled
                    and proof.local_original_track_count
                    == desired.local_original_track_count
                    and proof.local_original_map_fingerprint
                    == desired.local_original_map_fingerprint
                    and proof.local_original_channel_counts
                    == desired.local_original_channel_counts
                    and proof.local_original_source_ids
                    == desired.local_original_source_ids
                ):
                    return proof
            generation = max(time.time_ns(), self._host_presence_v2_generation + 1)
            try:
                proof = registry.bind_presence_v2(
                    enrollment.participant_id,
                    desired.display_name,
                    ordered_roster_digest=desired.ordered_roster_digest,
                    roster_count=desired.roster_count,
                    self_ordinal=desired.self_ordinal,
                    process_generation=desired.process_generation,
                    rpc_connection_generation=desired.rpc_connection_generation,
                    audio_connection_generation=desired.audio_connection_generation,
                    challenge=challenge.challenge,
                    challenge_epoch=challenge.challenge_epoch,
                    topology_epoch=challenge.topology_epoch,
                    presence_generation=generation,
                    capture_enabled=desired.capture_enabled,
                    local_original_track_count=(desired.local_original_track_count),
                    local_original_map_fingerprint=(
                        desired.local_original_map_fingerprint
                    ),
                    local_original_channel_counts=(
                        desired.local_original_channel_counts
                    ),
                    local_original_source_ids=desired.local_original_source_ids,
                    _allow_ambiguous_ordinal=(
                        desired.self_ordinal in desired_ambiguous
                    ),
                )
            except TransferConflictError:
                # A concurrent peer challenge fetch can rotate the lease
                # between the read and bind. The next bounded maintenance pass
                # retries against the new exact epoch.
                return None
            self._host_presence_v2_generation = generation
            return proof

    def recording_presence_snapshot(
        self,
        *,
        ordered_roster_digest: str | None = None,
        roster_count: int | None = None,
        challenge: str | None = None,
        challenge_epoch: int | None = None,
    ) -> tuple[PresenceV2Proof, ...]:
        """Return fresh v2 recorder proofs; legacy bindings never appear."""

        if self.registry is None:
            return ()
        self._refresh_host_recording_presence()
        return self.registry.recording_presence_snapshot(
            ordered_roster_digest=ordered_roster_digest,
            roster_count=roster_count,
            challenge=challenge,
            challenge_epoch=challenge_epoch,
        )

    def invalidate_recording_presence(self) -> None:
        with self._lock:
            registry = self.registry
            self._host_presence_v2_desired = None
            self._host_presence_v2_desired_fingerprint = ""
            self._host_presence_v2_desired_ambiguous_ordinals = ()
            self._recording_roster_key = None
        if registry is not None:
            registry.invalidate_presence_v2()

    def recording_local_original_obligations(
        self,
    ) -> tuple[LocalOriginalObligation, ...]:
        """Return current authenticated, path-free guest capture contracts.

        Older v2 peers remain represented with ``track_count=None`` so a
        caller can surface a readiness problem instead of treating an
        imprecise capture opt-in as an exact zero-track promise.
        """

        registry = self.registry
        if registry is None:
            return ()
        self._refresh_host_recording_presence()
        host_id = self.host_enrollment.participant_id if self.host_enrollment else ""
        return tuple(
            obligation
            for obligation in registry.current_local_original_obligations()
            if obligation.participant_id != host_id
        )

    def recording_local_original_obligation_issues(self) -> tuple[str, ...]:
        """Explain why current guest obligations cannot safely gate a take."""

        registry = self.registry
        if registry is None:
            return ("The authenticated peer registry is unavailable.",)
        host_id = self.host_enrollment.participant_id if self.host_enrollment else ""
        proofs = self.recording_presence_snapshot()
        fresh_ids = {proof.participant_id for proof in proofs}
        # Enrollment is a durable reconnect identity, not evidence that a peer
        # is in the current Jamulus roster. Only an enrollment that still owns
        # a reconciled live channel may create a legacy/missing-proof issue;
        # otherwise a departed guest would block every future take forever.
        live_enrolled_guest_ids = {
            enrollment.participant_id
            for enrollment in registry.participants()
            if enrollment.participant_id != host_id
            and registry.presence_for_participant(enrollment.participant_id) is not None
        }
        issues: list[str] = []
        if not registry.presence_v2_configured():
            if live_enrolled_guest_ids:
                issues.append(
                    "An enrolled guest cannot declare an exact Local Original inventory."
                )
        else:
            missing = tuple(
                participant_id
                for participant_id in registry.recording_presence_missing_participant_ids()
                if participant_id != host_id
            )
            if missing:
                issues.append(
                    "A connected guest has not renewed its exact Local Original inventory."
                )
            if live_enrolled_guest_ids - fresh_ids:
                issues.append(
                    "An enrolled guest has no exact Local Original inventory proof."
                )
        if any(
            proof.participant_id != host_id and proof.local_original_track_count is None
            for proof in proofs
        ):
            issues.append(
                "A guest's Local Original opt-in does not include an exact logical-track inventory."
            )
        if any(
            proof.participant_id != host_id and not proof.local_original_topology_exact
            for proof in proofs
        ):
            issues.append(
                "A guest's Local Original inventory does not include its exact "
                "ordered mono/stereo source topology."
            )
        return tuple(dict.fromkeys(issues))

    def prepare_local_original_obligations(
        self, take_id: str
    ) -> tuple[tuple[LocalOriginalObligation, ...], tuple[str, ...]]:
        """Freeze an exact, authenticated guest plan before recording starts.

        The two snapshots close the small gap between reading the contract and
        its readiness state. Any authenticated change during that interval is
        reported as an issue, never accepted as a silently different plan.
        """

        canonical_take = str(uuid.UUID(str(take_id)))
        before = self.recording_local_original_obligations()
        issues = list(self.recording_local_original_obligation_issues())
        obligations = self.recording_local_original_obligations()
        if _obligation_contract_key(before) != _obligation_contract_key(obligations):
            issues.append(
                "A guest's Local Original inventory changed during pre-take validation."
            )
        with self._lock:
            prior = self._local_original_obligations_by_take.get(canonical_take)
            if prior is not None and _obligation_contract_key(
                prior
            ) != _obligation_contract_key(obligations):
                issues.append(
                    "That take already has a different Local Original inventory plan."
                )
            elif not issues:
                self._local_original_obligations_by_take[canonical_take] = obligations
                self._prepared_local_original_obligation_takes.add(canonical_take)
        return obligations, tuple(dict.fromkeys(issues))

    def discard_prepared_local_original_obligations(self, take_id: str) -> bool:
        """Retire an unused preflight snapshot without touching take evidence.

        The operation is idempotent and succeeds only for a snapshot still
        marked as prepared. Once recording state has published that take, the
        immutable contract remains owned by its validation/recovery lifecycle.
        """

        try:
            canonical_take = str(uuid.UUID(str(take_id)))
        except (TypeError, ValueError, AttributeError):
            return False
        control = self.control
        if control is not None:
            snapshot = control.snapshot()
            if (
                snapshot.take_id == canonical_take
                and snapshot.signal is not RecordingSignal.IDLE
            ):
                return False
        with self._lock:
            if canonical_take not in self._prepared_local_original_obligation_takes:
                return False
            if (
                canonical_take in self._expected_by_take
                or canonical_take in self._capture_cursor_by_take
                or canonical_take in self._presence_readiness_issue_by_take
                or canonical_take in self._registered_takes
            ):
                return False
            self._prepared_local_original_obligation_takes.discard(canonical_take)
            self._local_original_obligations_by_take.pop(canonical_take, None)
            return True

    def local_original_obligations_for_take(
        self, take_id: str
    ) -> tuple[LocalOriginalObligation, ...]:
        """Return the immutable pre-take guest contracts frozen for ``take_id``."""

        try:
            canonical_take = str(uuid.UUID(str(take_id)))
        except (TypeError, ValueError, AttributeError):
            return ()
        with self._lock:
            return self._local_original_obligations_by_take.get(canonical_take, ())

    @staticmethod
    def _capture_arm_obligation_key(
        obligation: LocalOriginalObligation,
    ) -> tuple[object, ...]:
        return (
            obligation.participant_id,
            obligation.track_count,
            obligation.map_fingerprint,
            obligation.presence_generation,
            obligation.capture_requested,
            obligation.channel_counts,
            obligation.logical_source_ids,
        )

    def publish_capture_arm(
        self,
        take_id: str,
        *,
        recording_plan_fingerprint: str,
    ) -> CaptureArmSnapshot:
        """Ask every exact, opted-in guest to open capture before server start."""

        canonical_take = str(uuid.UUID(str(take_id)))
        with self._lock:
            if canonical_take not in self._prepared_local_original_obligation_takes:
                raise TransferConflictError(
                    "Guest Local Original obligations were not prepared for this take."
                )
            obligations = self._local_original_obligations_by_take.get(canonical_take)
            control = self.control
        if obligations is None or control is None:
            raise SessionTransferError("The guest capture-arm service is unavailable.")
        required = tuple(
            item
            for item in obligations
            if item.capture_requested and bool(item.track_count)
        )
        if any(not item.exact_topology for item in required):
            raise TransferConflictError(
                "A guest Local Original obligation has no exact source topology."
            )
        return control.publish_capture_arm(
            canonical_take,
            recording_plan_fingerprint=recording_plan_fingerprint,
            requirements=required,
        )

    def capture_arm_pending_participant_ids(
        self,
        take_id: str,
        *,
        arm_generation: int,
    ) -> tuple[str, ...]:
        """Return every required guest lacking a current, exact start ACK."""

        canonical_take = str(uuid.UUID(str(take_id)))
        generation = int(arm_generation)
        with self._lock:
            control = self.control
            registry = self.registry
            planned = self._local_original_obligations_by_take.get(canonical_take, ())
        planned_required_ids = tuple(
            sorted(
                item.participant_id
                for item in planned
                if item.capture_requested and bool(item.track_count)
            )
        )
        if control is None or registry is None:
            return planned_required_ids
        arm, requirements, acknowledgements = control.capture_arm_state()
        if (
            arm is None
            or arm.take_id != canonical_take
            or arm.arm_generation != generation
        ):
            return planned_required_ids
        expected = {item.participant_id: item for item in requirements}
        acknowledged = {item.participant_id: item for item in acknowledgements}
        current = {
            item.participant_id: item
            for item in registry.current_local_original_obligations()
        }
        pending: list[str] = []
        for participant_id, obligation in expected.items():
            acknowledgement = acknowledged.get(participant_id)
            current_obligation = current.get(participant_id)
            if (
                acknowledgement is None
                or current_obligation is None
                or self._capture_arm_obligation_key(current_obligation)
                != self._capture_arm_obligation_key(obligation)
            ):
                pending.append(participant_id)
        return tuple(sorted(pending))

    def capture_arm_ready(
        self,
        take_id: str,
        *,
        arm_generation: int,
    ) -> bool:
        try:
            canonical_take = str(uuid.UUID(str(take_id)))
        except (TypeError, ValueError, AttributeError):
            return False
        with self._lock:
            control = self.control
            registry = self.registry
        if control is None or registry is None:
            return False
        arm, _requirements, _acknowledgements = control.capture_arm_state()
        if (
            arm is None
            or arm.take_id != canonical_take
            or arm.arm_generation != int(arm_generation)
        ):
            return False
        return not self.capture_arm_pending_participant_ids(
            canonical_take,
            arm_generation=arm_generation,
        )

    def wait_for_capture_arm_acknowledgements(
        self,
        take_id: str,
        *,
        arm_generation: int,
        timeout_s: float,
    ) -> bool:
        """Wait boundedly without retaining host or HTTP-server locks."""

        if isinstance(timeout_s, bool):
            raise ValueError("timeout_s must be a finite non-negative number.")
        timeout = float(timeout_s)
        if not math.isfinite(timeout) or timeout < 0.0:
            raise ValueError("timeout_s must be a finite non-negative number.")
        deadline = time.monotonic() + timeout
        while True:
            if self.capture_arm_ready(
                take_id,
                arm_generation=arm_generation,
            ):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            with self._lock:
                control = self.control
            if control is None:
                return False
            _arm, _requirements, acknowledgements = control.capture_arm_state()
            control.wait_for_capture_arm_change(
                acknowledgement_count=len(acknowledgements),
                timeout_s=min(remaining, 0.1),
            )

    def cancel_capture_arm(
        self,
        take_id: str,
        *,
        arm_generation: int | None = None,
    ) -> bool:
        """Cancel one exact pending request; delayed callbacks cannot cancel newer work."""

        with self._lock:
            control = self.control
        if control is None:
            return False
        return control.cancel_capture_arm(
            take_id,
            arm_generation=arm_generation,
        )

    def _update_expected_capture_participants(self, take_id: str) -> None:
        """Union enrolled-peer Local Original obligations for one take."""

        registry = self.registry
        if registry is None:
            return
        host_id = self.host_enrollment.participant_id if self.host_enrollment else ""
        expected = set(self._expected_by_take.get(take_id, ()))
        current_obligations = self.recording_local_original_obligations()
        with self._lock:
            # ``control.begin`` has already published this take, so its
            # prepared snapshot is now immutable take evidence.
            self._prepared_local_original_obligation_takes.discard(take_id)
            frozen_obligations = self._local_original_obligations_by_take.get(take_id)
            if frozen_obligations is None:
                frozen_obligations = current_obligations
                self._local_original_obligations_by_take[take_id] = frozen_obligations
            elif _obligation_contract_key(
                current_obligations
            ) != _obligation_contract_key(frozen_obligations):
                self._presence_readiness_issue_by_take.setdefault(
                    take_id,
                    "A guest's Local Original inventory changed after the take began.",
                )
        expected.update(
            obligation.participant_id
            for obligation in current_obligations
            if obligation.capture_requested
        )
        if registry.presence_v2_configured():
            cursor = self._capture_cursor_by_take.setdefault(
                take_id, registry.presence_v2_capture_cursor()
            )
            proofs = self.recording_presence_snapshot()
            fresh_ids = {proof.participant_id for proof in proofs}
            expected.update(
                proof.participant_id
                for proof in proofs
                if proof.participant_id != host_id and proof.capture_enabled
            )
            expected.update(
                participant_id
                for participant_id in (
                    registry.current_capture_enabled_participant_ids()
                )
                if participant_id != host_id
            )
            expected.update(
                participant_id
                for participant_id in (
                    registry.recording_presence_missing_participant_ids(
                        capture_enabled_only=True
                    )
                )
                if participant_id != host_id
            )
            expected.update(
                participant_id
                for participant_id in registry.capture_enabled_participant_ids_since(
                    cursor
                )
                if participant_id != host_id
            )
            unproven_legacy_capture = {
                participant_id
                for participant_id in registry.legacy_capture_enabled_participant_ids()
                if participant_id != host_id and participant_id not in fresh_ids
            }
            expected.update(unproven_legacy_capture)
            missing = registry.recording_presence_missing_participant_ids()
            if (
                (host_id and host_id not in fresh_ids)
                or missing
                or unproven_legacy_capture
            ):
                self._presence_readiness_issue_by_take.setdefault(
                    take_id,
                    "Recorder participant readiness was incomplete.",
                )
        else:
            # Legacy presence remains valid only for Local Original delivery;
            # it never enters the recorder-ownership snapshot.
            for enrollment in registry.participants():
                if enrollment.participant_id == host_id:
                    continue
                binding = registry.presence_for_participant(enrollment.participant_id)
                if binding is not None and binding.capture_enabled:
                    expected.add(enrollment.participant_id)
        self._expected_by_take[take_id] = tuple(sorted(expected))

    def begin_take(
        self, take_id: str, *, started_utc: str
    ) -> SessionStateSnapshot | None:
        if self.control is None:
            return None
        registry = self.registry
        if registry is not None:
            self._capture_cursor_by_take.setdefault(
                take_id, registry.presence_v2_capture_cursor()
            )
        snapshot = self.control.begin(take_id, started_utc=started_utc)
        self._update_expected_capture_participants(take_id)
        return snapshot

    def begin_take_finalization(
        self,
        take_id: str,
        *,
        stopped_utc: str,
        message: str = "",
    ) -> SessionStateSnapshot | None:
        """Tell enrolled peers to finalize originals before host validation."""

        if self.control is None:
            return None
        snapshot = self.control.begin_finalizing(
            take_id,
            stopped_utc=stopped_utc,
            message=message,
        )
        # Stop truth must reach guests before registry reads that exist only to
        # classify the later Local Original inventory. If this refresh fails,
        # the caller sees the failure and final reconciliation remains
        # fail-closed, but guests no longer remain falsely Recording.
        self._update_expected_capture_participants(take_id)
        return snapshot

    def begin_armed_take_finalization(
        self,
        take_id: str,
        *,
        arm_generation: int,
        stopped_utc: str,
        message: str = "",
    ) -> SessionStateSnapshot | None:
        """Publish stop truth when server start confirmation was ambiguous.

        The control layer accepts this only for the exact, fully acknowledged
        arm.  A stale callback or partially armed guest set therefore cannot
        promote speculative media into a take.
        """

        if self.control is None:
            return None
        snapshot = self.control.begin_armed_finalizing(
            take_id,
            arm_generation=arm_generation,
            stopped_utc=stopped_utc,
            message=message,
        )
        self._update_expected_capture_participants(take_id)
        return snapshot

    def publish_shared_track_state(
        self,
        *,
        state: SharedTrackPlaybackState | str,
        loaded: bool,
        source_display_name: str = "",
        position_s: float = 0.0,
        duration_s: float = 0.0,
        loop_start_s: float = 0.0,
        loop_end_s: float | None = None,
        count_in_active: bool = False,
        cleanup_pending: bool = False,
        needs_attention: bool = False,
        playback_generation: int | None = None,
    ) -> SharedTrackSessionSnapshot | None:
        """Publish bounded Shared Track truth for authenticated guests.

        The returned projection grants neither transport control nor evidence
        of remote audibility. It is safe for a guest to render as host state.
        """

        if self.control is None:
            return None
        return self.control.publish_shared_track(
            state=state,
            loaded=loaded,
            source_display_name=source_display_name,
            position_s=position_s,
            duration_s=duration_s,
            loop_start_s=loop_start_s,
            loop_end_s=loop_end_s,
            count_in_active=count_in_active,
            cleanup_pending=cleanup_pending,
            needs_attention=needs_attention,
            playback_generation=playback_generation,
        )

    def publish_reference_video_state(
        self,
        *,
        state: ReferenceVideoPlaybackState | str,
        shared: bool,
        source_display_name: str = "",
        identity_digest: str = "",
        position_s: float = 0.0,
        duration_s: float = 0.0,
        needs_attention: bool = False,
        playback_generation: int | None = None,
    ) -> ReferenceVideoSessionSnapshot | None:
        """Publish bounded reference video transport for authenticated peers.

        The projection grants no transport authority and is not evidence that
        any other computer is showing the same frame.  A follower may mirror
        it only after proving it opened the same file.
        """

        if self.control is None:
            return None
        return self.control.publish_reference_video(
            state=state,
            shared=shared,
            source_display_name=source_display_name,
            identity_digest=identity_digest,
            position_s=position_s,
            duration_s=duration_s,
            needs_attention=needs_attention,
            playback_generation=playback_generation,
        )

    def publish_shared_canvas_state(
        self,
        *,
        shared: bool,
        join_url: str = "",
        server_label: str = "",
        session_label: str = "",
    ) -> SharedCanvasSessionSnapshot | None:
        """Offer authenticated peers the host's Drawpile invitation.

        The projection is an address, not authority: WebJam cannot see the
        canvas and never reports that anyone else opened it.  Drawpile still
        applies its own session password and account rules on join.
        """

        if self.control is None:
            return None
        return self.control.publish_shared_canvas(
            shared=shared,
            join_url=join_url,
            server_label=server_label,
            session_label=session_label,
        )

    def publish_room_clock_state(
        self,
        *,
        source: RoomClockSourceValue | str,
        running: bool = False,
        position_s: float = 0.0,
        duration_s: float = 0.0,
        bar: int = 0,
        beat: int = 0,
        section_label: str = "",
        tempo_bpm: float = 0.0,
        meter_numerator: int = 0,
        meter_denominator: int = 0,
    ) -> RoomClockSessionSnapshot | None:
        """Offer authenticated peers one pulse for the whole room.

        This is the published seam. Art calls it with a reference video
        position; a music surface calls it with a bar, a beat, a section, and
        optionally a tempo and meter. Neither has to know the other exists,
        and a room with no owner simply has no clock.

        The projection grants no authority: it says where the room is, not who
        may move it.
        """

        if self.control is None:
            return None
        return self.control.publish_room_clock(
            source=source,
            running=running,
            position_s=position_s,
            duration_s=duration_s,
            bar=bar,
            beat=beat,
            section_label=section_label,
            tempo_bpm=tempo_bpm,
            meter_numerator=meter_numerator,
            meter_denominator=meter_denominator,
        )

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
        self._update_expected_capture_participants(take_id)
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

    def wait_for_initial_take_inventory(
        self,
        take_id: str,
        *,
        timeout_s: float = 5.0,
    ) -> bool:
        """Wait off the UI thread for expected guest upload dispositions.

        A current peer declares both its total input count and segment count on
        every immutable descriptor. A participant is successful only after the
        complete declared set exists and every segment is checksum-complete or
        has a terminal transfer error. Legacy or contradictory declarations are
        wire-readable but immediately fail closed. Timeout is classification,
        never evidence that an incomplete inventory succeeded.
        """

        try:
            canonical_take = str(uuid.UUID(str(take_id)))
            timeout = float(timeout_s)
        except (TypeError, ValueError, AttributeError):
            return False
        if isinstance(timeout_s, bool) or not 0.0 <= timeout <= 60.0:
            return False
        self._update_expected_capture_participants(canonical_take)
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if not self.active or self.transfers is None:
                    return False
                transfers = self.transfers
                expected = set(self._expected_by_take.get(canonical_take, ()))
                obligations = {
                    item.participant_id: item
                    for item in self._local_original_obligations_by_take.get(
                        canonical_take, ()
                    )
                }
                readiness_issue = self._presence_readiness_issue_by_take.get(
                    canonical_take, ""
                )
            if readiness_issue:
                return False
            participants_to_check = expected | set(obligations)
            if not participants_to_check:
                return True
            try:
                inventory = transfers.inventory(canonical_take)
            except Exception:  # noqa: BLE001 - inventory truth fails absent
                inventory = ()
            by_participant: dict[str, list[object]] = {}
            for item in inventory:
                participant_id = str(
                    getattr(getattr(item, "descriptor", None), "participant_id", "")
                    or ""
                )
                if participant_id in participants_to_check:
                    by_participant.setdefault(participant_id, []).append(item)
            dispositions = {
                participant_id: _participant_inventory_disposition(
                    by_participant.get(participant_id, ()),
                    obligations.get(participant_id),
                )
                for participant_id in participants_to_check
            }
            if any(
                disposition.status == "needs_attention"
                for disposition in dispositions.values()
            ):
                return False
            settled = all(
                disposition.status == "complete"
                and all(
                    bool(getattr(item, "complete", False))
                    or bool(getattr(item, "error", ""))
                    for item in by_participant.get(participant_id, ())
                )
                for participant_id, disposition in dispositions.items()
            )
            now = time.monotonic()
            if settled:
                return True
            if now >= deadline:
                return False
            time.sleep(min(0.05, max(0.0, deadline - now)))

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
                for (
                    lease_generation,
                    _thread_id,
                ), count in self._callback_leases.items()
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
            try:
                self._refresh_host_recording_presence()
            except Exception:
                # Recorder proof renewal is fail-closed and independent of
                # media-transfer maintenance. Never let it end the worker.
                LOGGER.exception("Could not renew recorder-correlation presence")
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
                except Exception:
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
            obligation_by_id = {
                item.participant_id: item
                for item in self._local_original_obligations_by_take.get(take_id, ())
            }
            readiness_issue = self._presence_readiness_issue_by_take.get(take_id, "")
        from core.take_project import (
            AlignmentState,
            GapInterval,
            MediaSegment,
            MediaStatus,
            Participant,
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
            item.participant_id: item.display_name for item in registry.participants()
        }
        expected = set(expected_ids)
        expected.update(obligation_by_id)
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
        if readiness_issue:
            transfer_errors.append(f"{PEER_TRANSFER_ERROR_PREFIX}{readiness_issue}")
        transfer_summary: list[dict] = []
        next_order = max((item.order for item in project.tracks), default=-1) + 1
        attached_new_media = False

        for participant_id in sorted(expected):
            if not self._lifecycle_is_current(generation, stop_event):
                return False
            name = display_by_id.get(participant_id, "Musician")
            participant_items = received_by_participant.get(participant_id, [])
            inventory_disposition = _participant_inventory_disposition(
                participant_items,
                obligation_by_id.get(participant_id),
            )
            participant_status = "missing"
            segment_summaries: list[dict] = []
            if participant_id not in participants:
                participants[participant_id] = Participant(participant_id, name)
            exact_zero = bool(
                (obligation := obligation_by_id.get(participant_id)) is not None
                and obligation.exact
                and obligation.track_count == 0
            )
            if not participant_items and not exact_zero:
                transfer_errors.append(
                    f"{PEER_TRANSFER_ERROR_PREFIX}{name}'s local original has not arrived."
                )
            elif inventory_disposition.issue:
                transfer_errors.append(
                    f"{PEER_TRANSFER_ERROR_PREFIX}{name}'s "
                    f"{inventory_disposition.issue}"
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
                        f"{PEER_TRANSFER_ERROR_PREFIX}{name}'s local original is incomplete."
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
                        alignment=AlignmentState(method=_PEER_ALIGNMENT_INITIAL_METHOD),
                        logical_source_id=descriptor.logical_source_id,
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
                    alignment_method = (
                        str(attached_track.alignment.method or "").strip().lower()
                    )
                    manual_nudge = float(attached_track.alignment.manual_nudge_s)
                    retrying_reference = alignment_method.startswith(
                        _PEER_ALIGNMENT_WAITING_PREFIX
                    )
                    if (
                        alignment_method == _PEER_ALIGNMENT_INITIAL_METHOD
                        or retrying_reference
                    ) and not manual_nudge:
                        alignment_reason = ""
                        uncertainty_code = ""
                        reference_track = None
                        if descriptor.capture_errors or descriptor.gap_frames:
                            alignment_reason = "The local original has declared capture gaps or errors."
                            uncertainty_code = "incomplete-source"
                        elif not _track_media_is_verified(attached_track, folder):
                            alignment_reason = "The attached local original no longer matches its recorded checksum."
                            uncertainty_code = "attachment-checksum-mismatch"
                        else:
                            reference_track = _same_participant_reference_track(
                                tuple(tracks_by_id.values()),
                                participant_id=participant_id,
                                take_root=folder,
                            )
                            if reference_track is None:
                                alignment_reason = "No verified same-participant Jamulus server reference is available."
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
                            segment_summaries[-1]["alignment"] = (
                                _peer_alignment_summary(
                                    attached_track,
                                    status=(
                                        "waiting_for_reference"
                                        if waiting_for_reference
                                        else "uncertain"
                                    ),
                                    timing_ready=False,
                                    reason=alignment_reason,
                                )
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
                            except Exception:
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
                                segment_summaries[-1]["alignment"] = (
                                    _peer_alignment_summary(
                                        attached_track,
                                        status="aligned",
                                        timing_ready=True,
                                        reason="Strong shared transient evidence verified this local original against its server track.",
                                    )
                                )
                            else:
                                if result is None:
                                    alignment = AlignmentState(
                                        method=(
                                            _PEER_ALIGNMENT_UNCERTAIN_PREFIX
                                            + "analysis-unavailable"
                                        )
                                    )
                                    alignment_reason = "WebJam could not read enough timing evidence to verify this local original."
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
                                segment_summaries[-1]["alignment"] = (
                                    _peer_alignment_summary(
                                        attached_track,
                                        status="uncertain",
                                        timing_ready=False,
                                        reason=alignment_reason,
                                    )
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
                            alignment_reason = "No verified same-participant Jamulus server reference is available."
                            alignment_status = "waiting_for_reference"
                        elif alignment_method.endswith("incomplete-source"):
                            alignment_reason = "The local original has declared capture gaps or errors."
                            alignment_status = "uncertain"
                        elif alignment_method.endswith("attachment-checksum-mismatch"):
                            alignment_reason = "The attached local original no longer matches its recorded checksum."
                            alignment_status = "uncertain"
                        elif alignment_method.endswith(
                            "no-verified-same-participant-reference"
                        ):
                            alignment_reason = "No verified same-participant Jamulus server reference is available."
                            alignment_status = "waiting_for_reference"
                        elif alignment_method.endswith("analysis-unavailable"):
                            alignment_reason = "WebJam could not read enough timing evidence to verify this local original."
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
                        f"{PEER_TRANSFER_ERROR_PREFIX}{name}'s local original needs attention."
                    )
            if exact_zero and not participant_items:
                participant_status = "verified"
            elif participant_items:
                if inventory_disposition.status == "needs_attention":
                    participant_status = "needs_attention"
                elif inventory_disposition.status != "complete":
                    participant_status = "receiving"
                elif any(item.error for item in participant_items):
                    participant_status = "needs_attention"
                elif all(item.complete for item in participant_items):
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
                    "inventory": {
                        "status": inventory_disposition.status,
                        "input_count": inventory_disposition.input_count,
                        "segment_count": inventory_disposition.segment_count,
                        "received_segments": len(participant_items),
                        **(
                            {
                                "planned_track_count": obligation_by_id[
                                    participant_id
                                ].track_count,
                                "planned_map_fingerprint": obligation_by_id[
                                    participant_id
                                ].map_fingerprint,
                            }
                            if participant_id in obligation_by_id
                            else {}
                        ),
                    },
                    "segments": segment_summaries,
                }
            )

        base_errors = tuple(
            item
            for item in project.errors
            if not item.startswith(PEER_TRANSFER_ERROR_PREFIX)
        )
        had_transfer_attention = any(
            item.startswith(PEER_TRANSFER_ERROR_PREFIX) for item in project.errors
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
            or int(prior_payload.get("revision", 0) or 0) != base_manifest_revision
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
            except Exception:
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
        capture_tracks: Callable[[], tuple[object, ...]] | None = None,
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
        self.capture_tracks = capture_tracks
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
        self.last_presence_v2_error = ""
        self._desired_presence: tuple[int, str, bool] | None = None
        self._bound_presence: tuple[int, str, bool] | None = None
        self._presence_generation = 0
        self._presence_observation_epoch = 0
        self._desired_presence_v2: _DesiredPresenceV2 | None = None
        self._desired_presence_v2_capture_override: bool | None = None
        self._bound_presence_v2: tuple[_DesiredPresenceV2, str, int, int] | None = None
        self._presence_v2_generation = 0
        self._presence_v2_observation_epoch = 0
        self._presence_v2_topology_epoch = 0
        self._desired_presence_v2_topology_epoch = 0
        self._capture = None
        self._active_take_id = ""
        self._active_capture_arm: CaptureArmSnapshot | None = None
        self._active_capture_arm_state_generation: int | None = None
        self._bound_capture_arm_ack: CaptureArmAcknowledgement | None = None
        self._capture_started_config: tuple[int, int, int] | None = None
        self._capture_started_tracks: tuple[object, ...] | None = None
        self._capture_started_obligation: (
            tuple[int, str, tuple[int, ...], tuple[str, ...]] | None
        ) = None
        self._capture_finalization_needs_attention = False
        self._guidance_notification_generation = 0
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
    def capture_finalization_needs_attention(self) -> bool:
        """Whether capture finalization has an indeterminate durable outcome."""

        return bool(getattr(self, "_capture_finalization_needs_attention", False))

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
            if getattr(self, "_active_capture_arm", None) is not None:
                # An arm still visible here was never positively observed as
                # canceled.  The ACK response or host commit may have crossed
                # this shutdown, so preserve it as recovery media rather than
                # destructively guessing that recording never started.
                self._finalize_capture(
                    needs_attention=(
                        "Session ended before capture-arm commit or cancellation "
                        "was confirmed."
                    ),
                    upload_allowed=False,
                )
            else:
                # A quit/network leave never deletes or aborts an active take.
                self._finalize_capture(
                    needs_attention="Session ended before host stop was observed."
                )
            try:
                self._upload_pending()
            except SessionTransferError:
                pass
            self.invalidate_recording_presence()
            return not self.capture_finalization_needs_attention

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

    def _current_local_original_contract(
        self, *, capture_enabled: bool | None = None
    ) -> tuple[
        bool,
        int | None,
        str,
        tuple[object, ...] | None,
        tuple[int, ...],
        tuple[str, ...],
    ]:
        """Resolve a name-free logical-track contract without exposing config.

        A malformed map deliberately returns the legacy/unknown shape. The
        host can still authenticate the peer, but exact-take readiness then
        fails closed instead of silently treating a bad map as zero tracks.
        """

        requested = (
            bool(self.capture_enabled()) if capture_enabled is None else capture_enabled
        )
        if type(requested) is not bool:
            raise ValueError("capture_enabled must be a boolean.")
        if not requested:
            return False, 0, _ZERO_LOCAL_ORIGINAL_MAP_FINGERPRINT, (), (), ()
        try:
            tracks = (
                tuple(self.capture_tracks())
                if self.capture_tracks is not None
                else None
            )
            if tracks == ():
                return False, 0, _ZERO_LOCAL_ORIGINAL_MAP_FINGERPRINT, tracks, (), ()
            from core.local_capture import (
                bind_local_capture_logical_sources,
                local_capture_track_map_fingerprint,
            )

            participant_id = self.participant_id or derive_participant_id(
                self.invite.session_id, self.installation_id
            )
            tracks = bind_local_capture_logical_sources(
                tracks,
                session_id=self.invite.session_id,
                participant_id=participant_id,
            )
            fingerprint = local_capture_track_map_fingerprint(tracks)
            channel_counts = tuple(int(track.channel_count) for track in tracks)
            source_ids = tuple(str(track.logical_source_id) for track in tracks)
            return (
                True,
                len(tracks),
                fingerprint,
                tracks,
                channel_counts,
                source_ids,
            )
        except Exception:  # noqa: BLE001 - local names/paths stay private
            return True, None, "", None, (), ()

    def observe_presence_v2(
        self,
        display_name: str,
        *,
        ordered_roster_digest: str,
        roster_count: int,
        self_ordinal: int,
        process_generation: int,
        rpc_connection_generation: int,
        audio_connection_generation: int,
        capture_enabled: bool | None = None,
    ) -> None:
        """Observe this owned client in one ordered process-bound RPC roster.

        No client-local channel number is accepted.  Calling the legacy
        :meth:`observe_presence` does not synthesize or upgrade this proof.
        The host authenticates this enrolled WebJam peer, but the ordinal is a
        cooperative claim and invitations are intended for trusted bandmates.
        """

        (
            enabled,
            track_count,
            map_fingerprint,
            _tracks,
            channel_counts,
            logical_source_ids,
        ) = self._current_local_original_contract(capture_enabled=capture_enabled)
        desired = _DesiredPresenceV2(
            display_name=display_name,
            ordered_roster_digest=ordered_roster_digest,
            roster_count=roster_count,
            self_ordinal=self_ordinal,
            process_generation=process_generation,
            rpc_connection_generation=rpc_connection_generation,
            audio_connection_generation=audio_connection_generation,
            capture_enabled=enabled,
            local_original_track_count=track_count,
            local_original_map_fingerprint=map_fingerprint,
            local_original_channel_counts=channel_counts,
            local_original_source_ids=logical_source_ids,
        )
        with self._lock:
            if (
                self._desired_presence_v2 == desired
                and self._desired_presence_v2_topology_epoch
                == self._presence_v2_topology_epoch
            ):
                return
            self._desired_presence_v2 = desired
            self._desired_presence_v2_capture_override = capture_enabled
            self._desired_presence_v2_topology_epoch = self._presence_v2_topology_epoch
            self._presence_v2_observation_epoch += 1
            self._bound_presence_v2 = None

    def invalidate_recording_presence(self) -> None:
        """Retire local v2 proof material after RPC/audio/process proof loss."""

        lock = getattr(self, "_lock", None)
        if lock is None:
            # A constructor/teardown failure can leave a partial lifecycle
            # owner with no v2 state to retire. Cleanup must remain idempotent.
            return
        with lock:
            self._desired_presence_v2 = None
            self._desired_presence_v2_capture_override = None
            self._desired_presence_v2_topology_epoch = 0
            self._bound_presence_v2 = None
            self._presence_v2_observation_epoch += 1
            self.last_presence_v2_error = ""

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
                self.last_error = ""
            except SessionTransferError as exc:
                # Peer failure is control-plane only. Keep capture rolling and
                # retry from the confirmed host byte offset on the next poll.
                self.last_error = str(exc)
            except Exception:
                LOGGER.exception("Guest recording transfer worker failed")
                self.last_error = "The host recording service needs attention."
            self._stop_event.wait(_POLL_SECONDS)

    def poll_once(self) -> SessionStateSnapshot:
        if self.enrollment is None:
            self.enrollment = self.client.enroll(
                self.installation_id, self.display_name
            )
        self._publish_presence_if_needed()
        previous_state = self.last_state
        state = self.client.state(self.enrollment)
        self.last_state = state
        state_changed = bool(
            (
                previous_state is None
                and (
                    state.signal is not RecordingSignal.IDLE
                    or state.shared_track.generation > 0
                    # A first poll into a room where the host is already
                    # playing or already painting must notify, or a late
                    # joiner would sit on the default projection until the
                    # host next touched anything.
                    or state.reference_video.generation > 0
                    or state.shared_canvas.generation > 0
                    or state.room_clock.generation > 0
                    or state.capture_arm is not None
                )
            )
            or (previous_state is not None and state != previous_state)
        )
        published_terminal_early = bool(
            state_changed
            and state.signal
            in {
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }
        )
        if published_terminal_early:
            # Publish host stop truth before local WAV finalization or a long
            # synchronous upload. The UI must not remain visibly Recording
            # while those independent guest-owned operations finish.
            self._notify_guidance_changed()
        guidance_before_work = self._guidance_notification_generation
        self._apply_capture_arm_state(state)
        if state.signal is RecordingSignal.RECORDING and state.take_id:
            if bool(self.capture_enabled()) and (
                not state.capture_arm_supported or self._active_take_id == state.take_id
            ):
                # Legacy hosts have no capture-arm capability and retain their
                # historical session-wide start behavior.  On a current host,
                # the scoped arm/ACK is the participant's only authority to
                # capture; an unrelated enrolled peer must not open a device.
                self._start_capture(state.take_id)
        elif state.signal in {
            RecordingSignal.FINALIZING,
            RecordingSignal.COMPLETE,
            RecordingSignal.NEEDS_ATTENTION,
        } and state.take_id and state.take_id == self._active_take_id:
            self._finalize_capture(
                needs_attention=state.message
                if state.signal is RecordingSignal.NEEDS_ATTENTION
                else ""
            )
        self._upload_pending()
        if (
            state_changed
            and not published_terminal_early
            and self._guidance_notification_generation == guidance_before_work
        ):
            # Recording and Shared Track presentation share this bounded poll.
            # Capture/upload transitions can issue the same semantic
            # notification first; the generation check avoids duplicates.
            self._notify_guidance_changed()
        return state

    def _apply_capture_arm_state(self, state: SessionStateSnapshot) -> None:
        """Open/ACK or cancel one additive pre-start capture instruction."""

        arm = state.capture_arm
        active_arm = self._active_capture_arm
        cancellation = state.capture_arm_cancellation
        exact_cancellation = bool(
            active_arm is not None
            and cancellation is not None
            and cancellation.take_id == active_arm.take_id
            and cancellation.arm_generation == active_arm.arm_generation
        )
        if arm is None:
            if active_arm is None:
                return
            if state.take_id == active_arm.take_id and state.signal in {
                RecordingSignal.RECORDING,
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }:
                # The host observed every exact ACK and committed this take.
                # A very short take may already be terminal by the next poll;
                # terminal truth must finalize, never discard, that real audio.
                self._active_capture_arm = None
                self._active_capture_arm_state_generation = None
                self._bound_capture_arm_ack = None
                return
            if exact_cancellation:
                # Abort is destructive, so a newer session generation alone
                # is insufficient.  Require the host's exact take/generation
                # cancellation proof; unrelated Shared Track or take state
                # after a restart must preserve possibly recorded media.
                self._cancel_armed_capture()
            else:
                self._finalize_capture(
                    needs_attention=(
                        "The host restarted or became uncertain before the "
                        "capture-arm outcome was confirmed."
                    ),
                    upload_allowed=False,
                )
            return

        if active_arm is not None and active_arm != arm:
            if state.take_id == active_arm.take_id and state.signal in {
                RecordingSignal.RECORDING,
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }:
                self._finalize_capture(
                    needs_attention=(
                        state.message
                        if state.signal is RecordingSignal.NEEDS_ATTENTION
                        else ""
                    )
                )
            elif exact_cancellation:
                self._cancel_armed_capture()
            else:
                self._finalize_capture(
                    needs_attention=(
                        "The capture-arm authority changed without a confirmed "
                        "cancellation."
                    ),
                    upload_allowed=False,
                )
        if not bool(self.capture_enabled()):
            return
        if not self._start_capture(arm.take_id):
            return
        self._active_capture_arm = arm
        self._active_capture_arm_state_generation = state.generation
        acknowledgement = self._capture_arm_acknowledgement(arm)
        if self._bound_capture_arm_ack == acknowledgement:
            return
        if self.enrollment is None:
            raise SessionTransferError(
                "Capture opened before participant enrollment completed."
            )
        accepted = self.client.acknowledge_capture_arm(
            self.enrollment,
            acknowledgement,
        )
        if accepted != acknowledgement:
            raise SessionTransferError(
                "The host acknowledged a different guest capture contract."
            )
        self._bound_capture_arm_ack = accepted

    def _capture_arm_acknowledgement(
        self,
        arm: CaptureArmSnapshot,
    ) -> CaptureArmAcknowledgement:
        with self._lock:
            desired = self._desired_presence_v2
            bound = self._bound_presence_v2
            presence_generation = self._presence_v2_generation
        if desired is None or bound is None or bound[0] != desired:
            raise TransferConflictError(
                "A fresh exact Local Original presence proof is required."
            )
        started = self._capture_started_obligation
        if started is None:
            raise TransferConflictError(
                "The Local Original capture did not bind an exact start contract."
            )
        track_count, map_fingerprint, channel_counts, logical_source_ids = started
        if (
            not track_count
            or desired.local_original_track_count != track_count
            or desired.local_original_map_fingerprint != map_fingerprint
            or desired.local_original_channel_counts != channel_counts
            or desired.local_original_source_ids != logical_source_ids
        ):
            raise TransferConflictError(
                "The Local Original contract changed during capture arming."
            )
        return CaptureArmAcknowledgement(
            participant_id=self.participant_id,
            take_id=arm.take_id,
            arm_generation=arm.arm_generation,
            recording_plan_fingerprint=arm.recording_plan_fingerprint,
            presence_generation=presence_generation,
            local_original_map_fingerprint=map_fingerprint,
            local_original_channel_counts=channel_counts,
            local_original_source_ids=logical_source_ids,
        )

    def _publish_presence_if_needed(self) -> None:
        if self.enrollment is None:
            return
        with self._lock:
            desired = self._desired_presence
            bound = self._bound_presence
            observation_epoch = self._presence_observation_epoch
        if desired is not None and desired != bound:
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
                # A newer process-authenticated roster observation may have
                # arrived while the signed request was in flight. In that case the
                # completed request cannot satisfy the newer proof obligation;
                # leave it pending so the next poll publishes another generation.
                if (
                    self._presence_observation_epoch == observation_epoch
                    and self._desired_presence == desired
                ):
                    self._bound_presence = desired
        try:
            self._publish_presence_v2_if_needed()
        except SessionTransferError as exc:
            # Recorder attribution is optional evidence around the independent
            # recording-control and media-transfer plane. Keep it fail-closed
            # without starving state polling, capture finalization, or upload.
            self.last_presence_v2_error = str(exc)
        else:
            self.last_presence_v2_error = ""

    def _publish_presence_v2_if_needed(self) -> None:
        if self.enrollment is None:
            return
        with self._lock:
            observed = self._desired_presence_v2
            capture_override = self._desired_presence_v2_capture_override
        if observed is not None:
            (
                enabled,
                track_count,
                map_fingerprint,
                _tracks,
                channel_counts,
                logical_source_ids,
            ) = self._current_local_original_contract(capture_enabled=capture_override)
            refreshed = replace(
                observed,
                capture_enabled=enabled,
                local_original_track_count=track_count,
                local_original_map_fingerprint=map_fingerprint,
                local_original_channel_counts=channel_counts,
                local_original_source_ids=logical_source_ids,
            )
            if refreshed != observed:
                with self._lock:
                    if self._desired_presence_v2 == observed:
                        self._desired_presence_v2 = refreshed
                        self._presence_v2_observation_epoch += 1
                        self._bound_presence_v2 = None
        challenge = self.client.presence_v2_challenge(self.enrollment)
        with self._lock:
            known_topology = self._presence_v2_topology_epoch
            if known_topology == 0:
                self._presence_v2_topology_epoch = challenge.topology_epoch
                if self._desired_presence_v2 is not None:
                    # The first session challenge follows the first local RPC
                    # observation; there is no older topology to replay.
                    self._desired_presence_v2_topology_epoch = challenge.topology_epoch
            elif known_topology != challenge.topology_epoch:
                had_desired = self._desired_presence_v2 is not None
                self._presence_v2_topology_epoch = challenge.topology_epoch
                self._desired_presence_v2 = None
                self._desired_presence_v2_topology_epoch = 0
                self._bound_presence_v2 = None
                self._presence_v2_observation_epoch += 1
                if had_desired:
                    raise TransferConflictError(
                        "A fresh local recorder-roster observation is required."
                    )
                return
            desired = self._desired_presence_v2
            bound = self._bound_presence_v2
            observation_epoch = self._presence_v2_observation_epoch
            desired_topology = self._desired_presence_v2_topology_epoch
        if desired is None:
            return
        if desired_topology != challenge.topology_epoch:
            raise TransferConflictError(
                "A fresh local recorder-roster observation is required."
            )
        if (
            challenge.ordered_roster_digest != desired.ordered_roster_digest
            or challenge.roster_count != desired.roster_count
        ):
            raise TransferConflictError(
                "The guest and host recorder rosters do not match."
            )
        expected_bound = (
            desired,
            challenge.challenge,
            challenge.challenge_epoch,
            challenge.topology_epoch,
        )
        if bound == expected_bound:
            return
        self._presence_v2_generation = max(
            time.time_ns(), self._presence_v2_generation + 1
        )
        self.client.bind_presence_v2(
            self.enrollment,
            display_name=desired.display_name,
            ordered_roster_digest=desired.ordered_roster_digest,
            roster_count=desired.roster_count,
            self_ordinal=desired.self_ordinal,
            process_generation=desired.process_generation,
            rpc_connection_generation=desired.rpc_connection_generation,
            audio_connection_generation=desired.audio_connection_generation,
            challenge=challenge.challenge,
            challenge_epoch=challenge.challenge_epoch,
            topology_epoch=challenge.topology_epoch,
            presence_generation=self._presence_v2_generation,
            capture_enabled=desired.capture_enabled,
            local_original_track_count=desired.local_original_track_count,
            local_original_map_fingerprint=(desired.local_original_map_fingerprint),
            local_original_channel_counts=desired.local_original_channel_counts,
            local_original_source_ids=desired.local_original_source_ids,
        )
        with self._lock:
            if (
                self._presence_v2_observation_epoch == observation_epoch
                and self._desired_presence_v2 == desired
                and self._desired_presence_v2_topology_epoch == challenge.topology_epoch
            ):
                self._bound_presence_v2 = expected_bound

    def _new_capture(
        self,
        root: Path,
        device: int,
        rate: int,
        blocksize: int,
        *,
        take_id: str,
        tracks: tuple[object, ...] | None,
    ):
        if self.capture_factory is not None:
            factory_kwargs = {
                "device": device,
                "samplerate": rate,
                "blocksize": blocksize,
                "take_id": take_id,
                "session_id": self.invite.session_id,
            }
            if tracks is not None:
                factory_kwargs["tracks"] = tracks
            return self.capture_factory(root, **factory_kwargs)
        from core.local_capture import LocalInputCapture

        return LocalInputCapture(
            root,
            device=device,
            samplerate=rate,
            blocksize=blocksize,
            take_id=take_id,
            session_id=self.invite.session_id,
            tracks=tracks,
        )

    def _start_capture(self, take_id: str) -> bool:
        if self._capture is not None:
            if self._active_take_id == take_id:
                return True
            # A different take cannot overwrite an unfinalized local original.
            self._finalize_capture(
                needs_attention="A new take started before the prior stop."
            )
            if self._capture is not None:
                return False
        (
            enabled,
            track_count,
            map_fingerprint,
            tracks,
            channel_counts,
            logical_source_ids,
        ) = self._current_local_original_contract()
        if not enabled or track_count == 0:
            # The musician opted every configured Local Original out (or the
            # map failed closed) between presence publication and take start.
            # Never reinterpret that as LocalInputCapture's legacy pair.
            return False
        with self._lock:
            desired = self._desired_presence_v2
        if track_count is None or (
            desired is not None
            and (
                desired.local_original_track_count != track_count
                or desired.local_original_map_fingerprint != map_fingerprint
                or desired.local_original_channel_counts != channel_counts
                or desired.local_original_source_ids != logical_source_ids
            )
        ):
            self.last_error = (
                "The Local Original input map needs a fresh pre-take proof."
            )
            return False
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
            tracks=tracks,
        )
        capture.start()
        self._capture = capture
        self._active_take_id = take_id
        self._capture_started_config = (int(device), int(rate), int(blocksize))
        self._capture_started_tracks = tracks
        self._capture_started_obligation = (
            track_count,
            map_fingerprint,
            channel_counts,
            logical_source_ids,
        )
        self._capture_finalization_needs_attention = False
        self._notify_guidance_changed()
        return True

    def _cancel_armed_capture(self) -> None:
        """Discard only pre-start audio after the exact arm was canceled."""

        if self.capture_finalization_needs_attention:
            # stop_into() may already have partially moved durable files. An
            # automatic abort cannot distinguish that outcome from an intact
            # armed stream, so preserve the exact owner for recovery review.
            return

        capture = self._capture
        self._capture = None
        self._active_take_id = ""
        self._active_capture_arm = None
        self._active_capture_arm_state_generation = None
        self._bound_capture_arm_ack = None
        self._capture_started_config = None
        self._capture_started_tracks = None
        self._capture_started_obligation = None
        if capture is None:
            return
        abort = getattr(capture, "abort", None)
        if not callable(abort):
            self.last_error = (
                "The canceled pre-start Local Original could not be released."
            )
            return
        try:
            abort()
        except Exception:  # noqa: BLE001 - native errors can expose local paths
            self.last_error = (
                "The canceled pre-start Local Original needs local recovery review."
            )
        self._notify_guidance_changed()

    def _finalize_capture(
        self,
        *,
        needs_attention: str = "",
        upload_allowed: bool = True,
    ) -> None:
        if self.capture_finalization_needs_attention:
            # A prior stop_into() failed after an unknown amount of durable
            # work. Retrying could split, duplicate, or overwrite the take.
            return
        capture = self._capture
        take_id = self._active_take_id
        if capture is None or not take_id:
            return
        final_dir = self.queue_path.parent / take_id
        try:
            result = capture.stop_into(final_dir)
        except Exception:  # noqa: BLE001 - capture errors may contain local paths
            self._capture_finalization_needs_attention = True
            self.last_error = (
                "The local original could not be finalized and needs local "
                "recovery review."
            )
            self._notify_guidance_changed()
            return
        source_files = tuple(getattr(result, "files", ()) or ())
        if not source_files:
            self._capture_finalization_needs_attention = True
            self.last_error = (
                "The local original could not be finalized and needs local "
                "recovery review."
            )
            self._notify_guidance_changed()
            return
        self._capture = None
        self._active_take_id = ""
        self._active_capture_arm = None
        self._active_capture_arm_state_generation = None
        self._bound_capture_arm_ack = None
        self._capture_finalization_needs_attention = False
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
        (
            current_enabled,
            current_count,
            current_fingerprint,
            _tracks,
            current_channel_counts,
            current_source_ids,
        ) = self._current_local_original_contract()
        if (
            self._capture_started_tracks is not None
            and _tracks != self._capture_started_tracks
        ):
            errors.append(
                "The Local Original input map changed during this take; the "
                "preserved segment uses the map captured at its start."
            )
        if self._capture_started_obligation is not None and (
            not current_enabled
            or (
                current_count,
                current_fingerprint,
                current_channel_counts,
                current_source_ids,
            )
            != self._capture_started_obligation
        ):
            errors.append("The Local Original obligation changed during this take.")
        capture_device = getattr(result, "capture_device", None)
        device_id = str(getattr(capture_device, "device_id", "") or "")
        gaps = tuple(getattr(result, "gaps", ()) or ())
        inventory_input_count = len(source_files)
        inventory_segment_count = len(source_files)
        (
            planned_count,
            inventory_map_fingerprint,
            planned_channel_counts,
            planned_source_ids,
        ) = self._capture_started_obligation or (0, "", (), ())
        result_tracks = getattr(result, "tracks", None)
        if result_tracks is not None:
            try:
                from core.local_capture import local_capture_track_map_fingerprint

                actual_tracks = tuple(result_tracks)
                actual_fingerprint = local_capture_track_map_fingerprint(actual_tracks)
                if (
                    len(actual_tracks) != planned_count
                    or actual_fingerprint != inventory_map_fingerprint
                ):
                    errors.append(
                        "The finalized Local Original input map did not match "
                        "its pre-take obligation."
                    )
                inventory_map_fingerprint = actual_fingerprint
            except Exception:  # noqa: BLE001 - result names stay private
                errors.append(
                    "The finalized Local Original input map could not be verified."
                )
                inventory_map_fingerprint = ""
        if inventory_input_count != planned_count:
            errors.append(
                "The finalized Local Original inventory did not match its "
                "pre-take logical-track count."
            )
        for channel, source in enumerate(source_files):
            source_path = Path(source).resolve()
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(source_path))
            except (OSError, RuntimeError):
                errors.append("The preserved local WAV is unreadable.")
                continue
            if (
                channel >= len(planned_channel_counts)
                or int(info.channels) != planned_channel_counts[channel]
            ):
                errors.append(
                    "The preserved local WAV did not match its planned mono/stereo layout."
                )
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
                inventory_input_count=inventory_input_count,
                inventory_segment_count=inventory_segment_count,
                inventory_map_fingerprint=inventory_map_fingerprint,
                logical_source_id=(
                    planned_source_ids[channel]
                    if channel < len(planned_source_ids)
                    else ""
                ),
                capture_errors=tuple(dict.fromkeys(errors)),
                gaps=tuple(channel_gaps),
            )
            with self._lock:
                self._pending.append(
                    PendingLocalSegment(
                        descriptor,
                        source_path,
                        status="pending" if upload_allowed else "recovery_only",
                    )
                )
        self._capture_started_config = None
        self._capture_started_tracks = None
        self._capture_started_obligation = None
        self._save_queue()
        self._notify_originals_changed()

    def _notify_originals_changed(self) -> None:
        callback = self._on_originals_changed
        if callback is None:
            return
        try:
            callback(self.originals_root)
        except Exception:
            # The audio and durable queue are already safe; a reveal-action
            # refresh must never make capture finalization look unsuccessful.
            LOGGER.exception("Could not publish Local Originals update")

    def _notify_guidance_changed(self) -> None:
        self._guidance_notification_generation += 1
        callback = self._on_guidance_changed
        if callback is None:
            return
        try:
            callback()
        except Exception:
            LOGGER.exception("Could not publish Local Originals guidance")

    def _upload_pending(self) -> None:
        enrollment = self.enrollment
        if enrollment is None:
            return
        changed = False
        with self._lock:
            pending = tuple(self._pending)
        for item in pending:
            if item.status in {"verified", "recovery_only"}:
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
