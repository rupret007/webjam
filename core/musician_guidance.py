"""One immutable musician-facing projection of WebJam's existing truth.

The conductor remains the operational authority.  This module copies its
guarded phase and action, adds bounded output/recovery explanations, and keeps
the local creative pulse visibly separate.  It never invokes a provider,
advances a lifecycle, reads a path, or persists derived state.

``to_public_dict`` is deliberately smaller than the local UI snapshot.  It
contains only finite operational values and reason-free lifecycle transitions;
musician-authored notes and identifiers never cross that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Mapping

from core.session_conductor import (
    EvidenceState,
    ExportState,
    GuestMediaState,
    RecorderState,
    ReviewState,
    SessionConductorPhase,
    SessionConductorSnapshot,
    SessionPrimaryAction,
    SessionRole,
    TakeValidationState,
)
from core.session_intelligence import SessionPulse
from core.session_lifecycle import SessionLifecyclePhase


_MAX_TRANSITIONS = 5
_SAFE_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class GuidanceEvidence(str, Enum):
    """The bounded authority category behind the current guidance."""

    NONE = "none"
    SETUP = "setup"
    HOST_READINESS = "host_readiness"
    CONNECTION = "connection"
    HUMAN_CONFIRMATION = "human_confirmation"
    RECORDER = "recorder"
    TAKE_VALIDATION = "take_validation"
    GUEST_MEDIA = "guest_media"
    STUDIO = "studio"
    EXPORT = "export"
    CLEANUP = "cleanup"
    RECOVERY = "recovery"


class GuidanceRecovery(str, Enum):
    """Finite recovery category safe for public diagnostics."""

    NONE = "none"
    WAIT = "wait"
    RETRY_CONNECTION = "retry_connection"
    REPLACE_INVITE = "replace_invite"
    RETRY_SETUP = "retry_setup"
    REVIEW_TAKE = "review_take"
    OPEN_DETAILS = "open_details"
    CHECK_SESSION = "check_session"


class GuidanceState(str, Enum):
    """A common vocabulary for recording, take, Studio, and export outputs."""

    NOT_STARTED = "not_started"
    WORKING = "working"
    ACTIVE = "active"
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True, slots=True)
class StudioGuidanceFacts:
    """Bounded semantic facts published by the Studio review surface.

    No take name, identifier, path, section label, or worker error crosses this
    adapter.  The conductor uses these values only while Studio is the active
    review surface.
    """

    take_revision: int = 0
    take_available: bool = False
    take_selected: bool = False
    take_validated: bool = False
    take_needs_attention: bool = False
    arrangement_available: bool = False
    dirty: bool = False
    save_failed: bool = False
    can_export: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.take_revision, bool) or int(self.take_revision) < 0:
            raise ValueError("take_revision must be a non-negative integer")
        object.__setattr__(self, "take_revision", int(self.take_revision))

    @property
    def take_evidence(self) -> EvidenceState:
        if not self.take_selected:
            return (
                EvidenceState.UNKNOWN
                if self.take_available
                else EvidenceState.NOT_STARTED
            )
        if self.take_needs_attention:
            return EvidenceState.FAILED
        if self.take_validated:
            return EvidenceState.VERIFIED
        return EvidenceState.UNKNOWN

    @property
    def edit_evidence(self) -> EvidenceState:
        if self.save_failed:
            return EvidenceState.FAILED
        if self.dirty:
            return EvidenceState.IN_PROGRESS
        if self.arrangement_available:
            return EvidenceState.VERIFIED
        return EvidenceState.NOT_REQUIRED


@dataclass(frozen=True, slots=True)
class GuidanceDisplayOverride:
    """Bounded local copy for an observed topology-specific recovery.

    The conductor still owns phase, evidence, outputs, and generation.  This
    adapter lets a controller supply more specific fixed copy and a semantic
    action for facts the generic conductor intentionally does not encode.
    """

    title: str
    message: str
    primary_action: SessionPrimaryAction
    action_label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "primary_action", SessionPrimaryAction(self.primary_action)
        )


@dataclass(frozen=True, slots=True)
class GuidanceOutput:
    """One local, fixed-copy output status."""

    key: str
    label: str
    state: GuidanceState
    detail: str

    def __post_init__(self) -> None:
        if self.key not in {"recording", "take", "guest_media", "studio", "export"}:
            raise ValueError("unsupported guidance output key")
        object.__setattr__(self, "state", GuidanceState(self.state))

    def to_public_dict(self) -> dict[str, str]:
        """Return only finite values; local explanatory copy stays private."""

        return {"key": self.key, "state": self.state.value}


@dataclass(frozen=True, slots=True)
class GuidanceTransition:
    """One reason-free lifecycle transition suitable for UI and diagnostics."""

    at: str
    from_phase: SessionLifecyclePhase
    to_phase: SessionLifecyclePhase
    label: str

    def to_public_dict(self) -> dict[str, str]:
        return {
            "at": self.at,
            "from": self.from_phase.value,
            "to": self.to_phase.value,
        }


@dataclass(frozen=True, slots=True)
class MusicianGuidanceSnapshot:
    """Complete local renderer contract for one accepted conductor revision."""

    generation: int
    revision: int
    role: SessionRole
    phase: SessionConductorPhase
    primary_action: SessionPrimaryAction
    primary_enabled: bool
    primary_label: str
    title: str
    message: str
    why: str
    evidence: GuidanceEvidence
    preservation: str
    recovery: GuidanceRecovery
    recovery_text: str
    outputs: tuple[GuidanceOutput, ...]
    transitions: tuple[GuidanceTransition, ...]
    creative: SessionPulse | None = None

    @property
    def action_label(self) -> str:
        return self.primary_label or self.primary_action.label

    @property
    def next_step(self) -> str:
        if self.primary_action is SessionPrimaryAction.WAIT:
            return "Wait while WebJam verifies this step."
        if self.primary_action is SessionPrimaryAction.NONE:
            return self.message
        if self.primary_enabled:
            return self.action_label
        return "Review the current status before continuing."

    @property
    def output_line(self) -> str:
        visible = tuple(
            output
            for output in self.outputs
            if output.state
            not in {
                GuidanceState.NOT_STARTED,
                GuidanceState.NOT_REQUIRED,
            }
        )
        if not visible:
            return "No recording or export is confirmed yet."
        return " · ".join(f"{item.label}: {item.detail}" for item in visible)

    @property
    def accessible_description(self) -> str:
        values = [self.title, self.message, f"Next: {self.next_step}", self.why]
        if self.preservation:
            values.append(self.preservation)
        return " ".join(value.strip() for value in values if value.strip())

    def output(self, key: str) -> GuidanceOutput:
        for item in self.outputs:
            if item.key == key:
                return item
        raise KeyError(key)

    def to_public_dict(self) -> dict[str, object]:
        """Serialize an allowlist with no creative text or identifiers."""

        return {
            "schema": 1,
            "generation": self.generation,
            "revision": self.revision,
            "role": self.role.value,
            "phase": self.phase.value,
            "primary_action": self.primary_action.value,
            "primary_enabled": self.primary_enabled,
            "evidence": self.evidence.value,
            "recovery": self.recovery.value,
            "outputs": [item.to_public_dict() for item in self.outputs],
            "transitions": [item.to_public_dict() for item in self.transitions],
        }

    def to_markdown(self) -> str:
        """Render the local session brief, including authorized creative text."""

        creative = self.creative
        title = creative.title if creative is not None else "WebJam session"
        lines = [
            f"# {title}",
            "",
            "## Session status",
            f"Status: {self.title}",
            f"Next: {self.next_step}",
            f"Why: {self.why}",
        ]
        if self.preservation:
            lines.append(f"Media: {self.preservation}")
        lines.extend(["", "## Outputs"])
        for output in self.outputs:
            lines.append(f"- {output.label}: {output.detail}")
        if self.transitions:
            lines.extend(["", "## Session record"])
            lines.extend(f"- {item.at}: {item.label}" for item in self.transitions)
        if creative is not None:
            lines.extend(
                [
                    "",
                    "## Creative pulse",
                    f"Mode: {creative.mode_label}",
                    f"Stage: {creative.stage}",
                    f"Summary: {creative.summary}",
                    f"Next creative step: {creative.next_step}",
                ]
            )
            _append_section(lines, "Decisions", creative.decisions)
            if creative.actions:
                lines.extend(["", "### Actions"])
                for action in creative.actions:
                    owner = f"@{action.owner} " if action.owner else ""
                    lines.append(f"- {owner}{action.text}".rstrip())
            _append_section(lines, "Blockers", creative.blockers)
            _append_section(lines, "Questions", creative.questions)
            _append_section(lines, "References", creative.references)
        return "\n".join(lines)


def build_musician_guidance(
    conductor: SessionConductorSnapshot,
    *,
    creative: SessionPulse | None = None,
    lifecycle_events: Iterable[Mapping[str, object]] = (),
    display_override: GuidanceDisplayOverride | None = None,
) -> MusicianGuidanceSnapshot:
    """Project one already-accepted conductor snapshot for all renderers."""

    presentation = conductor.presentation
    primary_action = (
        display_override.primary_action
        if display_override is not None
        else presentation.primary_action
    )
    outputs = _outputs(conductor)
    recovery, recovery_text = _recovery(
        presentation.phase,
        presentation.retry_safe,
        primary_action,
    )
    return MusicianGuidanceSnapshot(
        generation=conductor.token.generation,
        revision=conductor.revision,
        role=presentation.role,
        phase=presentation.phase,
        primary_action=primary_action,
        primary_enabled=primary_action
        not in {
            SessionPrimaryAction.NONE,
            SessionPrimaryAction.WAIT,
        },
        primary_label=(
            (
                display_override.action_label
                or primary_action.label_for(presentation.creator_profile_key)
            )
            if display_override is not None
            else presentation.action_label
        ),
        title=(display_override.title if display_override else presentation.title),
        message=(
            display_override.message if display_override else presentation.message
        ),
        why=presentation.evidence_limit,
        evidence=_evidence(presentation.phase),
        preservation=presentation.preservation,
        recovery=recovery,
        recovery_text=recovery_text,
        outputs=outputs,
        transitions=_safe_transitions(lifecycle_events),
        creative=creative,
    )


def _outputs(conductor: SessionConductorSnapshot) -> tuple[GuidanceOutput, ...]:
    facts = conductor.facts
    return (
        _recording_output(facts.recorder),
        _take_output(facts.recorder, facts.take_validation, facts.take_available),
        _guest_media_output(facts.guest_media),
        _studio_output(
            facts.studio,
            facts.studio_take,
            facts.studio_edits,
            facts.studio_export_available,
        ),
        _export_output(facts.export),
    )


def _recording_output(state: RecorderState) -> GuidanceOutput:
    values = {
        RecorderState.IDLE: (GuidanceState.NOT_STARTED, "Not recording"),
        RecorderState.UNKNOWN: (GuidanceState.NEEDS_ATTENTION, "Status unknown"),
        RecorderState.REQUESTED: (GuidanceState.WORKING, "Waiting for confirmation"),
        RecorderState.STARTING: (GuidanceState.WORKING, "Waiting for confirmation"),
        RecorderState.RECORDING: (GuidanceState.ACTIVE, "Recorder confirmed"),
        RecorderState.STOPPING: (GuidanceState.WORKING, "Stopping safely"),
        RecorderState.STOPPED: (GuidanceState.WORKING, "Stopped; validating"),
        RecorderState.FAILED: (GuidanceState.NEEDS_ATTENTION, "Needs attention"),
    }
    status, detail = values[state]
    return GuidanceOutput("recording", "Recording", status, detail)


def _take_output(
    recorder: RecorderState,
    validation: TakeValidationState,
    available: bool,
) -> GuidanceOutput:
    if validation is TakeValidationState.VALID and available:
        value = (GuidanceState.READY, "Validated and available")
    elif validation is TakeValidationState.VALIDATING or (
        recorder is RecorderState.STOPPED
        and validation in {TakeValidationState.NOT_STARTED, TakeValidationState.UNKNOWN}
    ):
        value = (GuidanceState.WORKING, "Validation in progress")
    elif (
        validation
        in {
            TakeValidationState.NEEDS_ATTENTION,
            TakeValidationState.FAILED,
        }
        or recorder is RecorderState.FAILED
    ):
        value = (GuidanceState.NEEDS_ATTENTION, "Review required")
    elif validation is TakeValidationState.UNKNOWN:
        value = (GuidanceState.NEEDS_ATTENTION, "Status unknown")
    else:
        value = (GuidanceState.NOT_STARTED, "No validated take")
    return GuidanceOutput("take", "Take", value[0], value[1])


def _guest_media_output(state: GuestMediaState) -> GuidanceOutput:
    values = {
        GuestMediaState.NOT_EXPECTED: (GuidanceState.NOT_REQUIRED, "Not expected"),
        GuestMediaState.UNKNOWN: (GuidanceState.WORKING, "Awaiting verification"),
        GuestMediaState.WAITING: (GuidanceState.WORKING, "Waiting for original"),
        GuestMediaState.TRANSFERRING: (GuidanceState.WORKING, "Transfer in progress"),
        GuestMediaState.VERIFIED: (GuidanceState.READY, "Original verified"),
        GuestMediaState.NEEDS_ATTENTION: (
            GuidanceState.NEEDS_ATTENTION,
            "Review required",
        ),
        GuestMediaState.FAILED: (GuidanceState.NEEDS_ATTENTION, "Transfer failed"),
    }
    status, detail = values[state]
    return GuidanceOutput("guest_media", "Guest original", status, detail)


def _studio_output(
    state: ReviewState,
    take: EvidenceState,
    edits: EvidenceState,
    can_export: bool,
) -> GuidanceOutput:
    if edits in {EvidenceState.FAILED, EvidenceState.BLOCKED}:
        value = (GuidanceState.NEEDS_ATTENTION, "Choices could not be saved")
    elif edits is EvidenceState.IN_PROGRESS:
        value = (GuidanceState.WORKING, "Saving non-destructive choices")
    elif state is ReviewState.REVIEWING and take is EvidenceState.VERIFIED:
        detail = (
            "Verified take; export available"
            if can_export
            else "Verified take open for review"
        )
        value = (GuidanceState.ACTIVE, detail)
    elif state is ReviewState.REVIEWING and take in {
        EvidenceState.FAILED,
        EvidenceState.BLOCKED,
    }:
        value = (GuidanceState.NEEDS_ATTENTION, "Selected take needs review")
    elif state is ReviewState.REVIEWING and take is EvidenceState.NOT_STARTED:
        value = (GuidanceState.NOT_STARTED, "No recorded take yet")
    elif state is ReviewState.REVIEWING:
        value = (GuidanceState.WORKING, "Choose a take to review")
    else:
        value = (GuidanceState.NOT_STARTED, "Studio not open")
    return GuidanceOutput("studio", "Studio", value[0], value[1])


def _export_output(state: ExportState) -> GuidanceOutput:
    values = {
        ExportState.IDLE: (GuidanceState.NOT_STARTED, "Not exported"),
        ExportState.EXPORTING: (GuidanceState.WORKING, "Verification in progress"),
        ExportState.COMPLETE: (GuidanceState.READY, "Export verified"),
        ExportState.NEEDS_ATTENTION: (
            GuidanceState.NEEDS_ATTENTION,
            "Review and retry",
        ),
        ExportState.FAILED: (GuidanceState.NEEDS_ATTENTION, "Export failed"),
    }
    status, detail = values[state]
    return GuidanceOutput("export", "Export", status, detail)


def _evidence(phase: SessionConductorPhase) -> GuidanceEvidence:
    if phase is SessionConductorPhase.IDLE:
        return GuidanceEvidence.NONE
    if phase in {
        SessionConductorPhase.CONFIRMING_IDENTITY_AND_SOUND,
        SessionConductorPhase.BAND_CHECK_REQUIRED,
        SessionConductorPhase.BAND_CHECK_IN_PROGRESS,
        SessionConductorPhase.READY_TO_START,
    }:
        return GuidanceEvidence.SETUP
    if phase in {
        SessionConductorPhase.STARTING_HOST,
        SessionConductorPhase.WAITING_FOR_HOST_READINESS,
        SessionConductorPhase.INVITE_READY,
    }:
        return GuidanceEvidence.HOST_READINESS
    if phase in {
        SessionConductorPhase.JOINING,
        SessionConductorPhase.CONNECTED,
        SessionConductorPhase.RECONNECTING,
    }:
        return GuidanceEvidence.CONNECTION
    if phase is SessionConductorPhase.LIVE:
        return GuidanceEvidence.HUMAN_CONFIRMATION
    if phase in {
        SessionConductorPhase.RECORDING_STARTING,
        SessionConductorPhase.RECORDING,
        SessionConductorPhase.RECORDING_STOPPING,
    }:
        return GuidanceEvidence.RECORDER
    if phase in {
        SessionConductorPhase.TAKE_VALIDATING,
        SessionConductorPhase.TAKE_READY,
        SessionConductorPhase.TAKE_NEEDS_ATTENTION,
    }:
        return GuidanceEvidence.TAKE_VALIDATION
    if phase is SessionConductorPhase.GUEST_MEDIA_TRANSFERRING:
        return GuidanceEvidence.GUEST_MEDIA
    if phase is SessionConductorPhase.REVIEWING:
        return GuidanceEvidence.STUDIO
    if phase is SessionConductorPhase.EXPORTING:
        return GuidanceEvidence.EXPORT
    if phase in {SessionConductorPhase.ENDING, SessionConductorPhase.ENDED}:
        return GuidanceEvidence.CLEANUP
    return GuidanceEvidence.RECOVERY


def _recovery(
    phase: SessionConductorPhase,
    retry_safe: bool,
    primary_action: SessionPrimaryAction,
) -> tuple[GuidanceRecovery, str]:
    if primary_action is SessionPrimaryAction.RESET_INVITE:
        return (
            GuidanceRecovery.REPLACE_INVITE,
            "Replace the old invitation before sharing another private link.",
        )
    if primary_action is SessionPrimaryAction.RETRY_SETUP:
        return (
            GuidanceRecovery.RETRY_SETUP,
            "Retry only after WebJam confirms the prior setup attempt stopped safely.",
        )
    if phase in {
        SessionConductorPhase.STARTING_HOST,
        SessionConductorPhase.WAITING_FOR_HOST_READINESS,
        SessionConductorPhase.JOINING,
        SessionConductorPhase.RECORDING_STARTING,
        SessionConductorPhase.RECORDING_STOPPING,
        SessionConductorPhase.TAKE_VALIDATING,
        SessionConductorPhase.GUEST_MEDIA_TRANSFERRING,
        SessionConductorPhase.EXPORTING,
        SessionConductorPhase.ENDING,
    }:
        return GuidanceRecovery.WAIT, "WebJam is still verifying this step."
    if phase is SessionConductorPhase.RECONNECTING or (
        phase is SessionConductorPhase.FAILED and retry_safe
    ):
        return (
            GuidanceRecovery.RETRY_CONNECTION,
            "Retry only after WebJam confirms the prior attempt stopped safely.",
        )
    if phase is SessionConductorPhase.TAKE_NEEDS_ATTENTION:
        return (
            GuidanceRecovery.REVIEW_TAKE,
            "Open Studio and review the preserved sources before exporting.",
        )
    if phase in {SessionConductorPhase.BLOCKED, SessionConductorPhase.FAILED}:
        return (
            GuidanceRecovery.OPEN_DETAILS,
            "Open the details and resolve the required condition before continuing.",
        )
    if phase is SessionConductorPhase.INDETERMINATE:
        return (
            GuidanceRecovery.CHECK_SESSION,
            "Check the session so WebJam can gather fresh authoritative evidence.",
        )
    return GuidanceRecovery.NONE, ""


def _safe_transitions(
    values: Iterable[Mapping[str, object]],
) -> tuple[GuidanceTransition, ...]:
    transitions: list[GuidanceTransition] = []
    for value in values:
        if str(value.get("event", "")) != "transition":
            continue
        at = str(value.get("at", ""))
        if not _SAFE_TIMESTAMP_RE.fullmatch(at):
            continue
        try:
            previous = SessionLifecyclePhase(str(value.get("from_state", "")))
            current = SessionLifecyclePhase(str(value.get("to_state", "")))
        except ValueError:
            continue
        transitions.append(
            GuidanceTransition(
                at=at,
                from_phase=previous,
                to_phase=current,
                label=_transition_label(current),
            )
        )
    return tuple(transitions[-_MAX_TRANSITIONS:])


def _transition_label(phase: SessionLifecyclePhase) -> str:
    return {
        SessionLifecyclePhase.IDLE: "Ready for a new session",
        SessionLifecyclePhase.PREPARING: "Session preparation started",
        SessionLifecyclePhase.CHECKING_PERMISSIONS: "Checking permissions",
        SessionLifecyclePhase.RUNNING_PREFLIGHT: "Running setup checks",
        SessionLifecyclePhase.STARTING_HOST: "Host session started",
        SessionLifecyclePhase.WAITING_FOR_REACHABILITY: "Verifying invite readiness",
        SessionLifecyclePhase.READY_TO_SHARE: "Invite ready to share",
        SessionLifecyclePhase.JOINING: "Joining the session",
        SessionLifecyclePhase.CONNECTED: "Music path connected",
        SessionLifecyclePhase.DEGRADED: "Session needs attention",
        SessionLifecyclePhase.RECONNECTING: "Reconnection started",
        SessionLifecyclePhase.ENDING: "Session cleanup started",
        SessionLifecyclePhase.FINALIZING_RECORDINGS: "Finalizing recordings",
        SessionLifecyclePhase.COMPLETED: "Session cleanup completed",
        SessionLifecyclePhase.FAILED_RECOVERABLE: "Safe retry available",
        SessionLifecyclePhase.FAILED_FINAL: "Session stopped safely",
    }[phase]


def _append_section(lines: list[str], title: str, values: Iterable[str]) -> None:
    items = tuple(values)
    if not items:
        return
    lines.extend(["", f"### {title}"])
    lines.extend(f"- {item}" for item in items)
