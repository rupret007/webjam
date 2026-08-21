"""Canonical, fact-derived musician session conductor.

This module deliberately does not own a Jamulus process, an RPC client, a
recorder, or a Qt widget.  Those subsystems remain the authorities for their
own facts.  The conductor accepts a small immutable snapshot of those facts
and derives one musician-facing phase and one dominant next action.

That boundary is important: a running process is never treated as a connected
session, a recorder request is never treated as a saved take, and a meter is
never treated as proof that another musician heard anything.  Callers should
update :class:`SessionFacts` from their authoritative subsystem observations,
then render :class:`SessionConductorPresentation` without inventing a second
UI-owned state machine.

``SessionConductor`` adds a narrow generation/revision guard around the pure
derivation.  It makes duplicate callbacks idempotent and rejects callbacks
from an old attempt, older revision, or a completed attempt.  A persisted
checkpoint restores non-terminal live work as ``indeterminate`` until a fresh
authoritative observation arrives; saved take evidence can remain reviewable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from core.creative_modes import (
    CreatorProfile,
    get_creator_profile_by_key_or_default,
)
from core.meeting_link import (
    MEETING_DIRECT_CAPTURE_BOUNDARY,
    RECORD_SESSION_MEETING_CAPTURE_NOTICE,
)


class SessionRole(str, Enum):
    """The musician's role for one session attempt."""

    HOST = "host"
    GUEST = "guest"
    JOIN = "guest"
    PRACTICE = "practice"


class SessionConductorPhase(str, Enum):
    """The canonical musician-facing lifecycle vocabulary.

    These values intentionally describe a musician's current situation, not a
    particular worker, process, or UI screen.
    """

    IDLE = "idle"
    CONFIRMING_IDENTITY_AND_SOUND = "confirming_identity_and_sound"
    BAND_CHECK_REQUIRED = "band_check_required"
    BAND_CHECK_IN_PROGRESS = "band_check_in_progress"
    READY_TO_START = "ready_to_start"
    STARTING_HOST = "starting_host"
    WAITING_FOR_HOST_READINESS = "waiting_for_host_readiness"
    INVITE_READY = "invite_ready"
    JOINING = "joining"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    LIVE = "live"
    RECORDING_STARTING = "recording_starting"
    RECORDING = "recording"
    RECORDING_STOPPING = "recording_stopping"
    TAKE_VALIDATING = "take_validating"
    GUEST_MEDIA_TRANSFERRING = "guest_media_transferring"
    TAKE_READY = "take_ready"
    TAKE_NEEDS_ATTENTION = "take_needs_attention"
    REVIEWING = "reviewing"
    EXPORTING = "exporting"
    ENDING = "ending"
    ENDED = "ended"
    BLOCKED = "blocked"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class SessionPrimaryAction(str, Enum):
    """One intentionally small set of dominant musician actions."""

    NONE = "none"
    CONTINUE = "continue"
    CONFIRM_SOUND = "confirm_sound"
    RUN_BAND_CHECK = "run_band_check"
    START_SESSION = "start_session"
    COPY_INVITE = "copy_invite"
    RESET_INVITE = "reset_invite"
    OPEN_AUDIO_SETTINGS = "open_audio_settings"
    ADD_CONVERSATION = "add_conversation"
    SAVE_CONVERSATION = "save_conversation"
    ENTER_JAM = "enter_jam"
    RETRY_SETUP = "retry_setup"
    WAIT = "wait"
    TRY_RECONNECT = "try_reconnect"
    RECORD = "record"
    STOP_RECORDING = "stop_recording"
    REVIEW_TAKE = "review_take"
    SELECT_TAKE = "select_take"
    EXPORT_TRACKS = "export_tracks"
    END_SESSION = "end_session"
    OPEN_DETAILS = "open_details"
    CHECK_SESSION = "check_session"

    @property
    def label(self) -> str:
        return {
            SessionPrimaryAction.NONE: "",
            SessionPrimaryAction.CONTINUE: "Continue",
            SessionPrimaryAction.CONFIRM_SOUND: "Confirm your sound",
            SessionPrimaryAction.RUN_BAND_CHECK: "Run Band Check",
            SessionPrimaryAction.START_SESSION: "Start Session",
            SessionPrimaryAction.COPY_INVITE: "Copy Invite",
            SessionPrimaryAction.RESET_INVITE: "Reset Invite",
            SessionPrimaryAction.OPEN_AUDIO_SETTINGS: "Open Audio Setup",
            SessionPrimaryAction.ADD_CONVERSATION: "Add Conversation",
            SessionPrimaryAction.SAVE_CONVERSATION: "Save Conversation",
            SessionPrimaryAction.ENTER_JAM: "Enter Jam",
            SessionPrimaryAction.RETRY_SETUP: "Try Setup Again",
            SessionPrimaryAction.WAIT: "Please wait",
            SessionPrimaryAction.TRY_RECONNECT: "Try Reconnect",
            SessionPrimaryAction.RECORD: "Record",
            SessionPrimaryAction.STOP_RECORDING: "Stop Recording",
            SessionPrimaryAction.REVIEW_TAKE: "Review Take",
            SessionPrimaryAction.SELECT_TAKE: "Choose a Take",
            SessionPrimaryAction.EXPORT_TRACKS: "Export Tracks",
            SessionPrimaryAction.END_SESSION: "End Session",
            SessionPrimaryAction.OPEN_DETAILS: "Open Details",
            SessionPrimaryAction.CHECK_SESSION: "Check session",
        }[self]

    def label_for(self, profile: CreatorProfile | str) -> str:
        """Return profile-aware copy without changing the action contract.

        Action values remain stable for controllers, shortcuts, and protocol
        projections.  Only the user-facing label changes for creator profiles
        whose vocabulary is not music-specific.
        """

        resolved = (
            profile
            if isinstance(profile, CreatorProfile)
            else get_creator_profile_by_key_or_default(profile)
        )
        if resolved.key == "podcast_voice":
            return {
                SessionPrimaryAction.RUN_BAND_CHECK: "Run Sound Check",
                SessionPrimaryAction.ENTER_JAM: "Enter Session",
            }.get(self, self.label)
        if resolved.key == "review_rehearsal":
            return {
                SessionPrimaryAction.RUN_BAND_CHECK: "Run Session Check",
                SessionPrimaryAction.ENTER_JAM: "Enter Review",
            }.get(self, self.label)
        if resolved.key == "art":
            return {
                SessionPrimaryAction.RUN_BAND_CHECK: "Run Session Check",
                SessionPrimaryAction.ENTER_JAM: "Enter Studio",
            }.get(self, self.label)
        return self.label


class EvidenceState(str, Enum):
    """A source-specific piece of evidence, never a rendered UI state."""

    UNKNOWN = "unknown"
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_REQUIRED = "not_required"


class ProcessState(str, Enum):
    """Observed process state; ``RUNNING`` is deliberately weak evidence."""

    UNKNOWN = "unknown"
    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class MusicPathState(str, Enum):
    """Authenticated Jamulus client-path truth from its control boundary."""

    UNKNOWN = "unknown"
    NOT_STARTED = "not_started"
    STARTING = "starting"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class RecorderState(str, Enum):
    """Authoritative recorder state, not a Record button's visual state."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    REQUESTED = "requested"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TakeValidationState(str, Enum):
    """State of manifest/source validation for one authoritative take."""

    UNKNOWN = "unknown"
    NOT_STARTED = "not_started"
    VALIDATING = "validating"
    VALID = "valid"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"


class GuestMediaState(str, Enum):
    """State of optional authenticated guest-original delivery."""

    NOT_EXPECTED = "not_expected"
    UNKNOWN = "unknown"
    WAITING = "waiting"
    TRANSFERRING = "transferring"
    VERIFIED = "verified"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"


class ReviewState(str, Enum):
    """Studio playback/review state supplied by the review coordinator."""

    IDLE = "idle"
    REVIEWING = "reviewing"


class ExportState(str, Enum):
    """Track-export worker state supplied by the Studio coordinator."""

    IDLE = "idle"
    EXPORTING = "exporting"
    COMPLETE = "complete"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"


class CleanupState(str, Enum):
    """Owned-process cleanup truth supplied by the lifecycle coordinator."""

    NOT_REQUESTED = "not_requested"
    ENDING = "ending"
    COMPLETE = "complete"
    FAILED = "failed"
    UNKNOWN = "unknown"


class FailureDisposition(str, Enum):
    """An authority's safe conclusion about a session-level failure.

    This is purposefully not a raw exception.  A lower-level coordinator must
    make the conservative classification from its real provider facts.  An
    optional transfer or a review-only issue should use its own state above
    instead of promoting the entire session to ``FAILED``.
    """

    NONE = "none"
    RETRYABLE = "retryable"
    FINAL = "final"
    BLOCKED = "blocked"
    INDETERMINATE = "indeterminate"


def _coerce_role(value: SessionRole | str) -> SessionRole:
    if isinstance(value, SessionRole):
        return value
    text = str(value or "").strip().lower()
    if text == "join":
        return SessionRole.GUEST
    return SessionRole(text)


@dataclass(frozen=True, slots=True)
class SessionFacts:
    """Immutable, privacy-safe facts observed from the authoritative layers.

    No rendered value contains a server address, invitation, device identifier,
    name, audio, or raw exception.  ``take_path`` is an internal, optional
    filesystem handle for a controller that is opening Studio; it is never
    copied into a presentation and must not be included in a support bundle.
    The caller maps real subsystem state into these finite values before it
    crosses the musician-facing boundary.

    ``setup_requested`` is an explicit musician or deep-link intent, not a UI
    visual state.  It allows initial launch to remain ``idle`` until a person
    actually starts Host or Join.
    """

    role: SessionRole = SessionRole.HOST
    setup_requested: bool = False
    identity: EvidenceState = EvidenceState.NOT_STARTED
    sound: EvidenceState = EvidenceState.NOT_STARTED
    band_check: EvidenceState = EvidenceState.NOT_STARTED

    host_server_process: ProcessState = ProcessState.NOT_STARTED
    host_server_rpc: EvidenceState = EvidenceState.NOT_STARTED
    host_listener: EvidenceState = EvidenceState.NOT_STARTED
    invite: EvidenceState = EvidenceState.NOT_STARTED

    guest_enrollment: EvidenceState = EvidenceState.NOT_STARTED
    music_path: MusicPathState = MusicPathState.NOT_STARTED
    local_participant: EvidenceState = EvidenceState.NOT_STARTED
    remote_participant: EvidenceState = EvidenceState.NOT_STARTED
    participant_identity: EvidenceState = EvidenceState.NOT_STARTED
    had_authenticated_connection: bool = False

    recorder: RecorderState = RecorderState.IDLE
    local_capture: EvidenceState = EvidenceState.NOT_REQUIRED
    take_validation: TakeValidationState = TakeValidationState.NOT_STARTED
    guest_media: GuestMediaState = GuestMediaState.NOT_EXPECTED
    media_preservation: EvidenceState = EvidenceState.NOT_REQUIRED
    take_path: str = ""
    take_available: bool = False
    human_two_way_audibility: EvidenceState = EvidenceState.NOT_STARTED
    studio: ReviewState = ReviewState.IDLE
    studio_take: EvidenceState = EvidenceState.NOT_STARTED
    studio_edits: EvidenceState = EvidenceState.NOT_REQUIRED
    studio_export_available: bool = False
    export: ExportState = ExportState.IDLE
    cleanup: CleanupState = CleanupState.NOT_REQUESTED
    failure: FailureDisposition = FailureDisposition.NONE
    creator_profile_key: str = "music"

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _coerce_role(self.role))
        object.__setattr__(self, "setup_requested", bool(self.setup_requested))
        object.__setattr__(self, "identity", EvidenceState(self.identity))
        object.__setattr__(self, "sound", EvidenceState(self.sound))
        object.__setattr__(self, "band_check", EvidenceState(self.band_check))
        object.__setattr__(
            self,
            "host_server_process",
            ProcessState(self.host_server_process),
        )
        object.__setattr__(self, "host_server_rpc", EvidenceState(self.host_server_rpc))
        object.__setattr__(self, "host_listener", EvidenceState(self.host_listener))
        object.__setattr__(self, "invite", EvidenceState(self.invite))
        object.__setattr__(
            self,
            "guest_enrollment",
            EvidenceState(self.guest_enrollment),
        )
        object.__setattr__(self, "music_path", MusicPathState(self.music_path))
        object.__setattr__(
            self,
            "local_participant",
            EvidenceState(self.local_participant),
        )
        object.__setattr__(
            self,
            "remote_participant",
            EvidenceState(self.remote_participant),
        )
        object.__setattr__(
            self,
            "participant_identity",
            EvidenceState(self.participant_identity),
        )
        object.__setattr__(
            self,
            "had_authenticated_connection",
            bool(self.had_authenticated_connection),
        )
        object.__setattr__(self, "recorder", RecorderState(self.recorder))
        object.__setattr__(
            self,
            "local_capture",
            EvidenceState(self.local_capture),
        )
        object.__setattr__(
            self,
            "take_validation",
            TakeValidationState(self.take_validation),
        )
        object.__setattr__(self, "guest_media", GuestMediaState(self.guest_media))
        object.__setattr__(
            self,
            "media_preservation",
            EvidenceState(self.media_preservation),
        )
        object.__setattr__(self, "take_path", str(self.take_path or ""))
        object.__setattr__(
            self,
            "take_available",
            bool(self.take_available or self.take_path),
        )
        object.__setattr__(
            self,
            "human_two_way_audibility",
            EvidenceState(self.human_two_way_audibility),
        )
        object.__setattr__(self, "studio", ReviewState(self.studio))
        object.__setattr__(self, "studio_take", EvidenceState(self.studio_take))
        object.__setattr__(self, "studio_edits", EvidenceState(self.studio_edits))
        object.__setattr__(
            self,
            "studio_export_available",
            bool(self.studio_export_available),
        )
        object.__setattr__(self, "export", ExportState(self.export))
        object.__setattr__(self, "cleanup", CleanupState(self.cleanup))
        object.__setattr__(self, "failure", FailureDisposition(self.failure))
        object.__setattr__(
            self,
            "creator_profile_key",
            get_creator_profile_by_key_or_default(self.creator_profile_key).key,
        )

    @property
    def creator_profile(self) -> CreatorProfile:
        """Return the canonical presentation contract for these facts."""

        return get_creator_profile_by_key_or_default(self.creator_profile_key)

    @property
    def music_path_authenticated(self) -> bool:
        """Whether Jamulus control authentication, not process liveness, passed."""

        return self.music_path is MusicPathState.AUTHENTICATED

    @property
    def host_ready(self) -> bool:
        """Whether observed host facts jointly permit creating an invite.

        A host's own Jamulus client may still be joining while the hosted
        server is safely reachable.  That is not a live-band claim, so it
        must not block a truthful invitation.
        """

        return _host_ready(self)

    @property
    def band_check_pending(self) -> bool:
        return self.band_check is EvidenceState.IN_PROGRESS

    @property
    def band_check_required(self) -> bool:
        return self.setup_requested and self.band_check in {
            EvidenceState.NOT_STARTED,
            EvidenceState.UNKNOWN,
        }

    @property
    def invite_available(self) -> bool:
        return self.host_ready and self.invite is EvidenceState.VERIFIED

    @property
    def recorder_phase(self) -> RecorderState:
        return self.recorder

    @property
    def take_validated(self) -> bool:
        return self.take_validation is TakeValidationState.VALID and self.take_available

    @property
    def take_needs_attention(self) -> bool:
        return (
            self.take_validation
            in {
                TakeValidationState.NEEDS_ATTENTION,
                TakeValidationState.FAILED,
            }
            or self.guest_media
            in {
                GuestMediaState.NEEDS_ATTENTION,
                GuestMediaState.FAILED,
            }
            or self.export in {ExportState.NEEDS_ATTENTION, ExportState.FAILED}
        )

    @property
    def transfer_pending(self) -> bool:
        return self.guest_media in {
            GuestMediaState.UNKNOWN,
            GuestMediaState.WAITING,
            GuestMediaState.TRANSFERRING,
        }

    @property
    def studio_reviewing(self) -> bool:
        return self.studio is ReviewState.REVIEWING

    @property
    def exporting(self) -> bool:
        return self.export is ExportState.EXPORTING

    @property
    def stopping(self) -> bool:
        return (
            self.recorder is RecorderState.STOPPING
            or self.cleanup is CleanupState.ENDING
        )

    @property
    def ended(self) -> bool:
        return self.cleanup is CleanupState.COMPLETE

    @property
    def failed(self) -> bool:
        return (
            self.failure
            in {
                FailureDisposition.RETRYABLE,
                FailureDisposition.FINAL,
            }
            or self.music_path is MusicPathState.FAILED
        )


# This exact public name is intentionally explicit at integration call sites.
# ``SessionFacts`` remains as a compact spelling for tests and local helpers.
SessionConductorFacts = SessionFacts


@dataclass(frozen=True, slots=True)
class SessionConductorPresentation:
    """The complete, concise answer a musician-facing renderer needs."""

    phase: SessionConductorPhase
    role: SessionRole
    primary_action: SessionPrimaryAction
    title: str
    message: str
    evidence_limit: str
    preservation: str = ""
    retry_safe: bool = False
    creator_profile_key: str = "music"
    primary_label: str = ""

    @property
    def lifecycle_phase(self) -> SessionConductorPhase:
        """Explicit compatibility name for consumers that render a lifecycle."""

        return self.phase

    @property
    def headline(self) -> str:
        return self.title

    @property
    def detail(self) -> str:
        return self.message

    @property
    def limitation(self) -> str:
        return self.evidence_limit

    @property
    def primary_enabled(self) -> bool:
        """Whether the single primary action represents a safe user command."""

        return self.primary_action not in {
            SessionPrimaryAction.NONE,
            SessionPrimaryAction.WAIT,
        }

    @property
    def action_label(self) -> str:
        """Profile-aware primary label for every renderer."""

        return self.primary_label or self.primary_action.label_for(
            self.creator_profile_key
        )


_TERMINAL_PHASES = frozenset(
    {
        SessionConductorPhase.ENDED,
        SessionConductorPhase.FAILED,
    }
)

_DURABLE_RESTORE_PHASES = frozenset(
    {
        SessionConductorPhase.IDLE,
        SessionConductorPhase.READY_TO_START,
        SessionConductorPhase.TAKE_READY,
        SessionConductorPhase.TAKE_NEEDS_ATTENTION,
        SessionConductorPhase.REVIEWING,
        SessionConductorPhase.ENDED,
        SessionConductorPhase.BLOCKED,
        SessionConductorPhase.FAILED,
    }
)


def _presentation(
    phase: SessionConductorPhase,
    facts: SessionFacts,
) -> SessionConductorPresentation:
    """Render one profile-aware presentation for an already-derived phase."""

    role = facts.role
    profile = facts.creator_profile
    vocabulary = profile.vocabulary
    preservation = _preservation_line(facts.media_preservation)
    host = role is SessionRole.HOST

    if profile.key == "music":
        check_name = "Band Check"
        session_short = "jam"
        counterpart = "bandmate"
        waiting_counterpart = "your bandmate"
        group = "the band"
        live_path = "live music path"
        reachable_path = "music path"
        authenticated_path = "music path"
    elif profile.key == "podcast_voice":
        check_name = "Sound Check"
        session_short = vocabulary.session_noun
        counterpart = "speaker"
        waiting_counterpart = "your other speaker"
        group = "the speakers"
        live_path = "audio path"
        reachable_path = live_path
        authenticated_path = "WebJam audio path"
    else:
        check_name = "Session Check"
        session_short = vocabulary.session_noun
        # Profiles past Music and Podcast address people by their own noun so
        # a room of artists is never told to wait for another "musician".
        counterpart = vocabulary.participant_singular
        waiting_counterpart = f"another {counterpart}"
        group = f"the {vocabulary.participant_plural}"
        live_path = "audio path"
        reachable_path = live_path
        authenticated_path = "WebJam audio path"

    def present(
        primary_action: SessionPrimaryAction,
        title: str,
        message: str,
        evidence_limit: str,
        preservation_text: str = "",
        *,
        retry_safe: bool = False,
    ) -> SessionConductorPresentation:
        if profile.key == "art":
            title = f"{title} · Preview"
            # Art does synchronize one host-clocked video and does point the
            # room at one shared canvas, so it must not borrow Review's
            # "visual media is not synchronized" line. It states the narrower
            # truth instead: host transport only, a canvas WebJam brokers but
            # does not draw, and no take to review afterwards.
            policy_limit = (
                f"{MEETING_DIRECT_CAPTURE_BOUNDARY} Notes stay local and are "
                "not shared. An optional reference video follows the host's "
                "play, pause, stop, and position on each artist's own copy of "
                "the same file; that is not frame-accurate or timecoded "
                "review. An optional shared canvas is painted in Drawpile, "
                "not in WebJam, and WebJam cannot see it. This session is not "
                "recorded."
            )
            evidence_limit = f"{evidence_limit} {policy_limit}".strip()
        elif profile.is_preview:
            title = f"{title} · Preview"
            policy_limit = (
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE} Notes stay local and "
                "are not shared; visual media and timecode are not synchronized. "
                "Completed takes are playback-only: arrangement editing and track "
                "export are unavailable."
            )
            evidence_limit = f"{evidence_limit} {policy_limit}".strip()
        elif profile.key == "podcast_voice":
            evidence_limit = (
                f"{evidence_limit} {RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
            ).strip()
        return SessionConductorPresentation(
            phase=phase,
            role=role,
            primary_action=primary_action,
            title=title,
            message=message,
            evidence_limit=evidence_limit,
            preservation=preservation_text,
            retry_safe=retry_safe,
            creator_profile_key=profile.key,
            primary_label=primary_action.label_for(profile),
        )

    if phase is SessionConductorPhase.IDLE:
        message = (
            "Choose Host or Join to begin a rehearsal."
            if profile.key == "music"
            else f"Choose Host or Join to begin a {vocabulary.session_noun}."
        )
        evidence = (
            "WebJam has not checked a live music path or another musician yet."
            if profile.key == "music"
            else f"WebJam has not checked its audio path or another {counterpart} yet."
        )
        return present(
            SessionPrimaryAction.START_SESSION,
            "Ready when you are",
            message,
            evidence,
        )
    if phase is SessionConductorPhase.CONFIRMING_IDENTITY_AND_SOUND:
        return present(
            SessionPrimaryAction.CONFIRM_SOUND,
            "Confirm your sound",
            "Check your name and the sound you will send before continuing.",
            f"WebJam cannot yet confirm that {group} can hear you.",
        )
    if phase is SessionConductorPhase.BAND_CHECK_REQUIRED:
        return present(
            SessionPrimaryAction.RUN_BAND_CHECK,
            f"Complete {check_name}",
            f"{check_name} needs to verify this setup before the {session_short} starts.",
            "A saved setup is not proof that this route still works today.",
        )
    if phase is SessionConductorPhase.BAND_CHECK_IN_PROGRESS:
        return present(
            SessionPrimaryAction.WAIT,
            "Checking your setup",
            f"{check_name} is gathering evidence for this exact setup.",
            (
                "WebJam will not claim live audibility until "
                f"{vocabulary.participant_plural} confirm it."
            ),
        )
    if phase is SessionConductorPhase.READY_TO_START:
        message = (
            "Your setup is ready. Start the session when your band is ready."
            if profile.key == "music"
            else "Your setup is ready. Start when everyone is ready."
        )
        evidence = (
            "This confirms this Mac's setup, not a live band connection."
            if profile.key == "music"
            else (
                "This confirms this Mac's setup, not a live connection to the "
                f"{vocabulary.session_noun}."
            )
        )
        return present(
            SessionPrimaryAction.START_SESSION,
            "Ready to start",
            message,
            evidence,
        )
    if phase is SessionConductorPhase.STARTING_HOST:
        message = (
            "WebJam is starting the private band session."
            if profile.key == "music"
            else f"WebJam is starting the private {vocabulary.session_noun}."
        )
        return present(
            SessionPrimaryAction.WAIT,
            f"Starting your {session_short}",
            message,
            "A launched server is not yet proof that the session is reachable.",
        )
    if phase is SessionConductorPhase.WAITING_FOR_HOST_READINESS:
        return present(
            SessionPrimaryAction.WAIT,
            "Preparing the invite",
            "WebJam is verifying the host session before it can be shared.",
            f"WebJam has not yet confirmed an authenticated, reachable {reachable_path}.",
        )
    if phase is SessionConductorPhase.INVITE_READY:
        invite_message = (
            "Share the invite when your bandmate is ready to join."
            if profile.key == "music"
            else f"Share the invite when {waiting_counterpart} is ready to join."
        )
        invite_evidence = (
            "WebJam is ready to invite; it cannot confirm a bandmate is connected yet."
            if profile.key == "music"
            else (
                "WebJam is ready to invite; it cannot confirm that "
                f"{waiting_counterpart} is connected yet."
            )
        )
        return present(
            SessionPrimaryAction.COPY_INVITE,
            "Invite ready",
            invite_message,
            invite_evidence,
        )
    if phase is SessionConductorPhase.JOINING:
        return present(
            SessionPrimaryAction.WAIT,
            f"Joining the {session_short}",
            f"WebJam is verifying your private connection to {group}.",
            f"Opening an invite is not proof that the {authenticated_path} is authenticated.",
        )
    if phase is SessionConductorPhase.CONNECTED:
        return present(
            SessionPrimaryAction.NONE,
            f"Connected to the {session_short}",
            (
                f"Your {authenticated_path} is authenticated. Waiting for "
                f"{waiting_counterpart} to appear."
            ),
            (
                "A connection alone does not prove that either "
                f"{vocabulary.participant_singular} heard the other."
            ),
        )
    if phase is SessionConductorPhase.RECONNECTING:
        return present(
            SessionPrimaryAction.TRY_RECONNECT,
            "Reconnecting",
            f"WebJam lost the {live_path} and is checking it again.",
            "WebJam cannot confirm the current connection until authentication returns.",
            preservation,
            retry_safe=True,
        )
    if phase is SessionConductorPhase.LIVE:
        if profile.key == "music":
            title = "Band connected"
            message = (
                "Your band is connected. Play a note, then make sure you can hear "
                "each other before recording. Use Band Check (F2) if you need help."
            )
            evidence = (
                "Only musicians can confirm two-way audibility; meters do not prove it."
            )
        elif profile.key == "podcast_voice":
            title = "Speakers connected"
            message = (
                "Your speakers are connected through WebJam. Speak briefly, then "
                "make sure you can hear each other before recording. Use Sound "
                "Check (F2) if you need help."
            )
            evidence = (
                "Only speakers can confirm two-way audibility; meters do not prove it."
            )
        elif profile.key == "art":
            title = "Artists connected"
            message = (
                "The artists are connected through WebJam. Confirm everyone can "
                "hear the WebJam audio, then work as usual. Use Session Check "
                "(F2) if you need help."
            )
            evidence = (
                "Only artists can confirm two-way audibility; meters do not prove it."
            )
        else:
            title = "Participants connected"
            message = (
                "Participants are connected through WebJam. Confirm everyone can "
                "hear the WebJam audio before the review begins. Use Session Check "
                "(F2) if you need help."
            )
            evidence = "Only participants can confirm two-way audibility; meters do not prove it."
        return present(
            SessionPrimaryAction.RECORD
            if host and profile.capabilities.session_recording
            else SessionPrimaryAction.NONE,
            title,
            message,
            evidence,
        )
    if phase is SessionConductorPhase.RECORDING_STARTING:
        return present(
            SessionPrimaryAction.WAIT,
            "Starting recording",
            "WebJam is waiting for the recorder to confirm this take.",
            "A Record request is not proof that a take is safely being saved.",
        )
    if phase is SessionConductorPhase.RECORDING:
        evidence = (
            "WebJam cannot call the take saved until recording stops and "
            "validation finishes."
        )
        return present(
            SessionPrimaryAction.STOP_RECORDING if host else SessionPrimaryAction.NONE,
            "Recording take",
            "This WebJam-audio take is actively being recorded."
            if profile.is_preview
            else "This take is actively being recorded.",
            evidence,
        )
    if phase is SessionConductorPhase.RECORDING_STOPPING:
        return present(
            SessionPrimaryAction.WAIT,
            "Saving take",
            "WebJam is waiting for recording to stop safely.",
            "Media is not complete until validation confirms its sources and manifest.",
        )
    if phase is SessionConductorPhase.TAKE_VALIDATING:
        return present(
            SessionPrimaryAction.WAIT,
            "Validating take",
            "WebJam is checking the recorded sources before review.",
            "Files on disk are not proof that this take is complete.",
            preservation,
        )
    if phase is SessionConductorPhase.GUEST_MEDIA_TRANSFERRING:
        transfer_message = (
            "WebJam is verifying the guest’s original recording for this take."
            if profile.key == "music"
            else (
                f"WebJam is verifying the {vocabulary.participant_singular}’s "
                "original recording for this take."
            )
        )
        return present(
            SessionPrimaryAction.WAIT,
            "Waiting for the original",
            transfer_message,
            "The original is not complete until its transfer, hash, and PCM checks pass.",
            preservation,
        )
    if phase is SessionConductorPhase.TAKE_READY:
        if not profile.capabilities.take_review:
            return present(
                SessionPrimaryAction.NONE,
                "Audio take preserved",
                "The required WebJam-audio sources passed validation.",
                "This profile does not provide completed-take review.",
                preservation,
            )
        return present(
            SessionPrimaryAction.REVIEW_TAKE,
            "Take ready to review",
            (
                "The take’s required WebJam-audio sources are ready for "
                "playback and source review."
                if not profile.capabilities.take_editing
                else "The take’s required sources and validation evidence are ready for Studio."
            ),
            (
                "Review is read-only; arrangement editing and track export are unavailable."
                if not profile.capabilities.take_editing
                else "This does not confirm how the take will sound in another editor."
            ),
            preservation,
        )
    if phase is SessionConductorPhase.TAKE_NEEDS_ATTENTION:
        attention_message = (
            "WebJam preserved what it could. Open the source and recovery details."
            if not profile.capabilities.take_review
            else (
                "WebJam preserved what it could. Review the source and recovery details."
                if not profile.capabilities.track_export
                else (
                    "WebJam preserved what it could. Review the source and recovery "
                    "details before export."
                )
            )
        )
        attention_limit = (
            "WebJam is not calling this take complete. This profile does not "
            "provide completed-take review."
            if not profile.capabilities.take_review
            else (
                "WebJam is not calling this take complete. Review remains "
                "playback-only and cannot export tracks."
                if not profile.capabilities.track_export
                else "WebJam is not calling this take complete or ready for another editor."
            )
        )
        return present(
            SessionPrimaryAction.OPEN_DETAILS
            if not profile.capabilities.take_review
            else SessionPrimaryAction.REVIEW_TAKE,
            "Take needs attention",
            attention_message,
            attention_limit,
            preservation or "WebJam could not confirm whether all media was preserved.",
        )
    if phase is SessionConductorPhase.REVIEWING:
        if not profile.capabilities.take_review:
            return present(
                SessionPrimaryAction.NONE,
                "Review workspace",
                "Capture feedback in local notes and decide the next rehearsal action.",
                "This profile does not provide completed-take review.",
                preservation,
            )
        if not profile.capabilities.take_editing:
            if facts.studio_take is EvidenceState.NOT_STARTED:
                return present(
                    SessionPrimaryAction.NONE,
                    "No takes yet",
                    "Record WebJam audio in the live session, then return here to review it.",
                    "Opening review does not create or validate a recording.",
                    preservation,
                )
            if facts.studio_take is EvidenceState.UNKNOWN:
                return present(
                    SessionPrimaryAction.SELECT_TAKE,
                    "Choose a take to review",
                    "Select a completed take for playback and source inspection.",
                    "Review is read-only; arrangement editing and track export are unavailable.",
                    preservation,
                )
            if facts.studio_take in {EvidenceState.FAILED, EvidenceState.BLOCKED}:
                return present(
                    SessionPrimaryAction.OPEN_DETAILS,
                    "Review this take",
                    "The selected take has source or validation details that need attention.",
                    "Playback review cannot repair, edit, or export the take.",
                    preservation,
                )
            return present(
                SessionPrimaryAction.REVIEW_TAKE,
                "Take open for review",
                "Use playback and source details to review the completed WebJam-audio take.",
                "Review is read-only; arrangement editing and track export are unavailable.",
                preservation,
            )
        if facts.studio_edits in {EvidenceState.FAILED, EvidenceState.BLOCKED}:
            return present(
                SessionPrimaryAction.OPEN_DETAILS,
                "Studio choices need attention",
                "WebJam couldn't confirm that the latest non-destructive choices were saved.",
                "The recorded take is unchanged, but the latest Studio choices are not confirmed.",
                preservation,
            )
        if facts.studio_take is EvidenceState.NOT_STARTED:
            return present(
                SessionPrimaryAction.NONE,
                "No takes yet",
                "Record a take in the live session, then return to Studio to review it.",
                "Opening Studio does not create or validate a recording.",
                preservation,
            )
        if facts.studio_take is EvidenceState.UNKNOWN:
            return present(
                SessionPrimaryAction.SELECT_TAKE,
                "Choose a take to review",
                "Select a take in Studio to check its sources, arrangement, and export readiness.",
                "Opening Studio does not select or validate a take by itself.",
                preservation,
            )
        if facts.studio_take in {EvidenceState.FAILED, EvidenceState.BLOCKED}:
            return present(
                SessionPrimaryAction.OPEN_DETAILS,
                "Review this take",
                "The selected take has source or validation details that need attention.",
                "WebJam will not enable export until the selected take is safe to use.",
                preservation,
            )
        if facts.studio_edits is EvidenceState.IN_PROGRESS:
            return present(
                SessionPrimaryAction.WAIT,
                "Saving Studio choices",
                "WebJam is saving the latest non-destructive arrangement choices.",
                "Export waits until those choices reach the durable Studio sidecar.",
                preservation,
            )
        if not facts.studio_export_available:
            return present(
                SessionPrimaryAction.REVIEW_TAKE,
                "Review this take",
                "Check the selected take and its source details before export.",
                "The selected take is open, but Studio has not enabled a safe export yet.",
                preservation,
            )
        return present(
            SessionPrimaryAction.EXPORT_TRACKS,
            "Ready to export",
            "The selected take is verified and its current Studio choices are saved.",
            "Studio review is non-destructive and does not prove an external-editor import.",
            preservation,
        )
    if phase is SessionConductorPhase.EXPORTING:
        if not profile.capabilities.track_export:
            return present(
                SessionPrimaryAction.WAIT,
                "Checking existing export activity",
                "WebJam is waiting for the current operation to reach a safe boundary.",
                "This Preview does not offer local multitrack Studio or track export.",
                preservation,
            )
        return present(
            SessionPrimaryAction.WAIT,
            "Exporting tracks",
            "WebJam is publishing and checking the track package.",
            "An export is not ready until its selected sources and checksums verify.",
            preservation,
        )
    if phase is SessionConductorPhase.ENDING:
        return present(
            SessionPrimaryAction.WAIT,
            "Ending session",
            "WebJam is finishing owned work and cleaning up the session.",
            "WebJam will not call the session finished until owned-process cleanup is confirmed.",
            preservation,
        )
    if phase is SessionConductorPhase.ENDED:
        return present(
            SessionPrimaryAction.START_SESSION,
            "Safe to end session",
            "The session has finished its confirmed cleanup.",
            "Ending confirms cleanup, not a human review of every take or export.",
            preservation,
        )
    if phase is SessionConductorPhase.BLOCKED:
        return present(
            SessionPrimaryAction.OPEN_DETAILS,
            "Action needed",
            "WebJam found a required condition that needs attention before it can continue.",
            "WebJam will not guess past a blocked device, identity, or safety check.",
            preservation,
        )
    if phase is SessionConductorPhase.FAILED:
        retry_safe = facts.failure is FailureDisposition.RETRYABLE
        return present(
            SessionPrimaryAction.TRY_RECONNECT
            if retry_safe
            else SessionPrimaryAction.OPEN_DETAILS,
            "Session needs attention",
            (
                "WebJam stopped this attempt safely. Check the details before trying again."
                if retry_safe
                else "WebJam cannot safely continue this session attempt. Check the details."
            ),
            "WebJam is not treating an incomplete provider outcome as a successful session.",
            preservation,
            retry_safe=retry_safe,
        )
    # INDETERMINATE is the safe fallback for unknown provider outcomes.
    return present(
        SessionPrimaryAction.CHECK_SESSION,
        "Session status needs checking",
        "WebJam cannot confirm the current state after an interruption or incomplete provider result.",
        "WebJam will not retry or claim a take is safe until fresh evidence is observed.",
        preservation or "WebJam could not confirm whether media was preserved.",
    )


def _preservation_line(state: EvidenceState) -> str:
    if state is EvidenceState.VERIFIED:
        return "Recorded media was preserved."
    if state in {EvidenceState.FAILED, EvidenceState.BLOCKED}:
        return "WebJam could not confirm whether all media was preserved."
    if state is EvidenceState.UNKNOWN:
        return "WebJam has not confirmed whether media was preserved."
    return ""


def _is_failed(*states: object) -> bool:
    return any(
        state in {EvidenceState.FAILED, ProcessState.FAILED, MusicPathState.FAILED}
        for state in states
    )


def _is_blocked(*states: object) -> bool:
    return any(state is EvidenceState.BLOCKED for state in states)


def _host_ready(facts: SessionFacts) -> bool:
    """Require independently observed server, RPC, and listener truth.

    Host reachability and host-client authentication are deliberately
    separate.  The former can safely make an invite available; only
    ``_music_connected`` can promote the musician-facing session to connected
    or live.
    """

    return (
        facts.host_server_process is ProcessState.RUNNING
        and facts.host_server_rpc is EvidenceState.VERIFIED
        and facts.host_listener is EvidenceState.VERIFIED
    )


def _music_connected(facts: SessionFacts) -> bool:
    """A process or meter alone is deliberately insufficient here."""

    return (
        facts.music_path is MusicPathState.AUTHENTICATED
        and facts.local_participant is EvidenceState.VERIFIED
    )


def derive_session_presentation(
    facts: SessionFacts,
) -> SessionConductorPresentation:
    """Derive the canonical phase from immutable subsystem facts.

    Precedence intentionally favors safety: cleanup, invalid/incomplete takes,
    and explicitly indeterminate outcomes prevent a stale live or process fact
    from creating a more optimistic musician-facing state.
    """

    # Ending is a hard boundary.  Once cleanup is requested, no late record or
    # connection callback is allowed to make the user think the session resumed.
    if facts.cleanup is CleanupState.COMPLETE:
        return _presentation(SessionConductorPhase.ENDED, facts)
    if facts.cleanup is CleanupState.ENDING:
        return _presentation(SessionConductorPhase.ENDING, facts)
    if facts.cleanup in {CleanupState.FAILED, CleanupState.UNKNOWN}:
        return _presentation(SessionConductorPhase.INDETERMINATE, facts)

    # Export/review facts are local to a validated take and outrank a stale
    # connection callback from a session that has already moved into review.
    if facts.export is ExportState.EXPORTING:
        return _presentation(SessionConductorPhase.EXPORTING, facts)
    if facts.export in {ExportState.NEEDS_ATTENTION, ExportState.FAILED}:
        return _presentation(SessionConductorPhase.TAKE_NEEDS_ATTENTION, facts)
    if facts.studio is ReviewState.REVIEWING:
        return _presentation(SessionConductorPhase.REVIEWING, facts)

    if facts.take_validation in {
        TakeValidationState.NEEDS_ATTENTION,
        TakeValidationState.FAILED,
    } or facts.guest_media in {
        GuestMediaState.NEEDS_ATTENTION,
        GuestMediaState.FAILED,
    }:
        return _presentation(SessionConductorPhase.TAKE_NEEDS_ATTENTION, facts)
    if facts.take_validation is TakeValidationState.VALIDATING:
        return _presentation(SessionConductorPhase.TAKE_VALIDATING, facts)
    if facts.take_validation is TakeValidationState.VALID:
        if facts.guest_media in {
            GuestMediaState.WAITING,
            GuestMediaState.TRANSFERRING,
            GuestMediaState.UNKNOWN,
        }:
            return _presentation(SessionConductorPhase.GUEST_MEDIA_TRANSFERRING, facts)
        if facts.guest_media in {
            GuestMediaState.NOT_EXPECTED,
            GuestMediaState.VERIFIED,
        }:
            if not facts.take_available:
                return _presentation(SessionConductorPhase.INDETERMINATE, facts)
            return _presentation(SessionConductorPhase.TAKE_READY, facts)

    if facts.recorder is RecorderState.FAILED:
        if facts.media_preservation is EvidenceState.NOT_REQUIRED:
            return _presentation(SessionConductorPhase.FAILED, facts)
        return _presentation(SessionConductorPhase.TAKE_NEEDS_ATTENTION, facts)
    if facts.recorder is RecorderState.STOPPING:
        return _presentation(SessionConductorPhase.RECORDING_STOPPING, facts)
    if facts.recorder is RecorderState.RECORDING:
        return _presentation(SessionConductorPhase.RECORDING, facts)
    if facts.recorder in {RecorderState.REQUESTED, RecorderState.STARTING}:
        return _presentation(SessionConductorPhase.RECORDING_STARTING, facts)
    if facts.recorder is RecorderState.STOPPED and facts.take_validation in {
        TakeValidationState.NOT_STARTED,
        TakeValidationState.UNKNOWN,
    }:
        return _presentation(SessionConductorPhase.TAKE_VALIDATING, facts)

    # A completed/cancelled controller deliberately clears setup intent before
    # it refreshes the shell.  A late process/roster callback must not revive
    # that finished attempt as "joining" or "waiting".  Take/review facts
    # above remain visible because they are durable local work, not a live
    # music-path assertion.
    if not facts.setup_requested:
        return _presentation(SessionConductorPhase.IDLE, facts)

    # A subsystem's explicit conservative conclusion must win over a friendly
    # message.  Optional transfer and export failures were handled above as
    # take attention rather than escalating the whole session.
    if facts.failure is FailureDisposition.BLOCKED:
        return _presentation(SessionConductorPhase.BLOCKED, facts)
    if facts.failure is FailureDisposition.INDETERMINATE:
        return _presentation(SessionConductorPhase.INDETERMINATE, facts)
    if facts.failure in {FailureDisposition.RETRYABLE, FailureDisposition.FINAL}:
        return _presentation(SessionConductorPhase.FAILED, facts)

    if _is_blocked(
        facts.identity,
        facts.sound,
        facts.band_check,
        facts.participant_identity,
    ):
        return _presentation(SessionConductorPhase.BLOCKED, facts)
    if _is_failed(facts.identity, facts.sound, facts.band_check):
        return _presentation(SessionConductorPhase.BLOCKED, facts)
    if facts.participant_identity is EvidenceState.FAILED:
        # A duplicate or otherwise untrusted participant identity is never a
        # live participant.  The roster authority provides this fact.
        return _presentation(SessionConductorPhase.BLOCKED, facts)

    if facts.music_path is MusicPathState.RECONNECTING or (
        facts.music_path is MusicPathState.DISCONNECTED
        and facts.had_authenticated_connection
    ):
        return _presentation(SessionConductorPhase.RECONNECTING, facts)
    if facts.music_path is MusicPathState.FAILED:
        return _presentation(SessionConductorPhase.FAILED, facts)

    # A host must not look merely "connected" to itself while the private
    # session still lacks a verified invite.  Once a remote participant has
    # been authenticated, the ordinary connected/live branch below takes over.
    if (
        facts.role is SessionRole.HOST
        and facts.remote_participant is not EvidenceState.VERIFIED
    ):
        if _host_ready(facts) and facts.invite is EvidenceState.VERIFIED:
            return _presentation(SessionConductorPhase.INVITE_READY, facts)
        if facts.host_server_process is ProcessState.STARTING:
            return _presentation(SessionConductorPhase.STARTING_HOST, facts)
        if (
            facts.host_server_process is ProcessState.RUNNING
            or facts.host_server_rpc
            in {
                EvidenceState.IN_PROGRESS,
                EvidenceState.VERIFIED,
            }
        ):
            return _presentation(
                SessionConductorPhase.WAITING_FOR_HOST_READINESS, facts
            )

    if _music_connected(facts):
        if (
            facts.remote_participant is EvidenceState.VERIFIED
            and facts.participant_identity is EvidenceState.VERIFIED
        ):
            return _presentation(SessionConductorPhase.LIVE, facts)
        return _presentation(SessionConductorPhase.CONNECTED, facts)

    # A host can see a remote roster entry while its own client is still
    # authenticating.  That is neither invite-ready nor live; show the same
    # connection-in-progress state a guest sees until this Mac has fresh
    # authenticated local-roster evidence.
    if facts.music_path in {
        MusicPathState.STARTING,
        MusicPathState.AUTHENTICATING,
    }:
        return _presentation(SessionConductorPhase.JOINING, facts)

    if facts.role is SessionRole.HOST:
        if facts.host_server_process is ProcessState.FAILED:
            return _presentation(SessionConductorPhase.FAILED, facts)
    elif facts.role is SessionRole.GUEST:
        if facts.guest_enrollment in {
            EvidenceState.IN_PROGRESS,
            EvidenceState.VERIFIED,
        } or facts.music_path in {
            MusicPathState.STARTING,
            MusicPathState.AUTHENTICATING,
            MusicPathState.DISCONNECTED,
        }:
            return _presentation(SessionConductorPhase.JOINING, facts)
        if facts.guest_enrollment is EvidenceState.FAILED:
            return _presentation(SessionConductorPhase.FAILED, facts)
    elif facts.music_path in {
        MusicPathState.STARTING,
        MusicPathState.AUTHENTICATING,
    }:
        return _presentation(SessionConductorPhase.JOINING, facts)

    if facts.identity in {
        EvidenceState.NOT_STARTED,
        EvidenceState.IN_PROGRESS,
        EvidenceState.UNKNOWN,
    } or facts.sound in {
        EvidenceState.NOT_STARTED,
        EvidenceState.IN_PROGRESS,
        EvidenceState.UNKNOWN,
    }:
        return _presentation(SessionConductorPhase.CONFIRMING_IDENTITY_AND_SOUND, facts)
    if facts.band_check is EvidenceState.IN_PROGRESS:
        return _presentation(SessionConductorPhase.BAND_CHECK_IN_PROGRESS, facts)
    if facts.band_check in {EvidenceState.NOT_STARTED, EvidenceState.UNKNOWN}:
        return _presentation(SessionConductorPhase.BAND_CHECK_REQUIRED, facts)
    if facts.band_check in {EvidenceState.VERIFIED, EvidenceState.NOT_REQUIRED}:
        return _presentation(SessionConductorPhase.READY_TO_START, facts)

    # Unknown combinations never get a friendly invented state.
    return _presentation(SessionConductorPhase.INDETERMINATE, facts)


def derive_session_conductor(
    facts: SessionConductorFacts,
) -> SessionConductorPresentation:
    """Public ergonomic name for the pure canonical derivation."""

    return derive_session_presentation(facts)


@dataclass(frozen=True, slots=True)
class SessionConductorToken:
    """Generation token carried by callbacks for one attempt."""

    generation: int
    role: SessionRole

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or int(self.generation) < 0:
            raise ValueError("generation must be a non-negative integer")
        object.__setattr__(self, "generation", int(self.generation))
        object.__setattr__(self, "role", _coerce_role(self.role))


@dataclass(frozen=True, slots=True)
class SessionConductorCheckpoint:
    """Bounded data that can be persisted without a rendered phase."""

    token: SessionConductorToken
    revision: int
    facts: SessionFacts

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or int(self.revision) < 0:
            raise ValueError("revision must be a non-negative integer")
        object.__setattr__(self, "revision", int(self.revision))
        if self.facts.role is not self.token.role:
            raise ValueError("checkpoint facts must use the token role")


@dataclass(frozen=True, slots=True)
class SessionConductorSnapshot:
    """Current facts plus their derived presentation and callback guard."""

    token: SessionConductorToken
    revision: int
    facts: SessionFacts
    presentation: SessionConductorPresentation


class SessionConductor:
    """Generation- and revision-safe holder for fact-derived presentation.

    The conductor never invokes a provider.  A controller starts/retries the
    actual work, then calls :meth:`observe` with real observations.  Returning
    ``False`` means the observation was stale, contradictory, role-mismatched,
    or would resurrect a completed attempt; callers should discard it.
    """

    def __init__(self, facts: SessionFacts | None = None) -> None:
        self._facts = facts or SessionFacts()
        self._token = SessionConductorToken(0, self._facts.role)
        self._revision = 0
        self._needs_fresh_observation = False

    @property
    def snapshot(self) -> SessionConductorSnapshot:
        presentation = derive_session_presentation(self._facts)
        if self._needs_fresh_observation:
            presentation = _presentation(
                SessionConductorPhase.INDETERMINATE, self._facts
            )
        return SessionConductorSnapshot(
            token=self._token,
            revision=self._revision,
            facts=self._facts,
            presentation=presentation,
        )

    @property
    def token(self) -> SessionConductorToken:
        return self._token

    def start(self, role: SessionRole | str) -> SessionConductorToken:
        """Start one attempt, idempotently protecting rapid repeated presses.

        If an active attempt exists, its token wins even when a conflicting
        second role arrives.  The caller must finish or fail that attempt before
        deliberately starting another one, preventing two host/join attempts
        from being created by rapid UI input.
        """

        requested_role = _coerce_role(role)
        current = self.snapshot.presentation.phase
        if (
            current not in _TERMINAL_PHASES
            and current is not SessionConductorPhase.IDLE
        ):
            return self._token
        if current is SessionConductorPhase.IDLE and self._facts.setup_requested:
            return self._token

        generation = self._token.generation + 1
        self._token = SessionConductorToken(generation, requested_role)
        self._revision = 0
        self._facts = SessionFacts(
            role=requested_role,
            setup_requested=True,
            creator_profile_key=self._facts.creator_profile_key,
        )
        self._needs_fresh_observation = False
        return self._token

    def retry(self) -> SessionConductorToken | None:
        """Open a new guarded attempt only when the last result is retry-safe."""

        presentation = self.snapshot.presentation
        if not presentation.retry_safe:
            return None
        # A fresh generation makes late callbacks from the failed attempt stale.
        self._token = SessionConductorToken(
            self._token.generation + 1,
            self._token.role,
        )
        self._revision = 0
        self._facts = SessionFacts(
            role=self._token.role,
            setup_requested=True,
            creator_profile_key=self._facts.creator_profile_key,
        )
        self._needs_fresh_observation = False
        return self._token

    def reset_to_idle(
        self,
        role: SessionRole | str | None = None,
    ) -> SessionConductorToken:
        """Invalidate an ended/cancelled attempt after confirmed cleanup.

        Controllers must call this only once their owned cleanup has reached a
        known idle boundary.  It deliberately advances the token instead of
        merely replacing facts, so an old worker cannot redraw a session after
        the musician has safely left it.
        """

        reset_role = self._token.role if role is None else _coerce_role(role)
        self._token = SessionConductorToken(
            self._token.generation + 1,
            reset_role,
        )
        self._revision = 0
        self._facts = SessionFacts(
            role=reset_role,
            creator_profile_key=self._facts.creator_profile_key,
        )
        self._needs_fresh_observation = False
        return self._token

    def observe(
        self,
        token: SessionConductorToken,
        revision: int,
        facts: SessionFacts,
    ) -> bool:
        """Apply a newer authoritative observation if it is safe to do so.

        Repeating an exact callback is a no-op success.  A different payload at
        the same revision is rejected because an authority cannot safely claim
        that two different snapshots were the same observed fact.
        """

        if token != self._token:
            return False
        if isinstance(revision, bool) or int(revision) < 0:
            return False
        revision = int(revision)
        if facts.role is not token.role:
            return False
        if revision < self._revision:
            return False
        if revision == self._revision:
            return facts == self._facts

        current_phase = self.snapshot.presentation.phase
        incoming_phase = derive_session_presentation(facts).phase
        if current_phase in _TERMINAL_PHASES and incoming_phase is not current_phase:
            return False

        self._facts = facts
        self._revision = revision
        self._needs_fresh_observation = False
        return True

    def checkpoint(self) -> SessionConductorCheckpoint:
        """Return a path-free fact checkpoint; the phase is re-derived later."""

        return SessionConductorCheckpoint(
            self._token,
            self._revision,
            replace(self._facts, take_path=""),
        )

    @classmethod
    def restore(cls, checkpoint: SessionConductorCheckpoint) -> "SessionConductor":
        """Restore durable facts without trusting stale live-process state."""

        conductor = cls(checkpoint.facts)
        conductor._token = checkpoint.token
        conductor._revision = checkpoint.revision
        phase = derive_session_presentation(checkpoint.facts).phase
        conductor._needs_fresh_observation = phase not in _DURABLE_RESTORE_PHASES
        return conductor
