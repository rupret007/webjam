"""Per-source recording truth projection for the live workspace.

One pure function turns the recording coordinator's existing take-scoped
evidence — roster identities, recorder receipts, conflict keys, and the
receipt-freeze fact — into one bounded presentation per source (musician
or Shared Track) for UI cards and guest-safe summaries.

Truth rules, deliberately conservative:

- Receipts are *identity evidence*, not liveness. While recording is
  active, an unproven participant renders as WAITING (identity not yet
  proven this take), never MISSING.
- MISSING exists only after the take's receipt set is frozen: at that
  point an absent proof is a real absence the finalization gate will also
  see.
- Only an explicit conflict key may render CONFLICTED.
- The projection never carries fingerprints, digests, channel ids, or
  paths; participant ids and bounded display names only, with a redacted
  repr.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class RecordingSourceState(str, Enum):
    """The finite per-source states the live workspace may claim."""

    ARMED = "armed"
    WAITING = "waiting"
    RECORDING = "recording"
    CONFLICTED = "conflicted"
    MISSING = "missing"
    FINALIZED = "finalized"


_PRE_ROLL_PHASES = frozenset({"preflight", "starting"})
# ``count_in`` is a UI-synthesized phase string, not a RecorderPhase value;
# it is explicitly an active-capture phase here (scout risk: silently
# degrading it would misreport the count-in as idle).
_ACTIVE_PHASES = frozenset({"recording", "stopping", "count_in"})
_FROZEN_PHASES = frozenset({"validating", "complete", "needs_attention"})
_MAX_SOURCES = 512
_MAX_NAME_CHARS = 128


@dataclass(frozen=True, repr=False)
class RecordingSourcePresentation:
    """One bounded, path-free source row for the recording workspace."""

    participant_id: str
    display_name: str
    kind: str  # "musician" | "shared_track"
    state: RecordingSourceState
    channels: int = 0

    def __repr__(self) -> str:
        return "RecordingSourcePresentation(private=[redacted])"


def _bounded_name(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text[:_MAX_NAME_CHARS] or "Musician"


def project_recording_sources(
    *,
    phase: str,
    roster: Iterable[tuple[str, str]],
    receipts: Iterable[object],
    conflicted_keys: Iterable[str],
    receipts_frozen: bool,
    shared_track_planned: bool = False,
) -> tuple[RecordingSourcePresentation, ...]:
    """Project per-source recording truth from existing take evidence.

    ``roster`` is (participant_id, display_name) pairs; ``receipts`` are
    RecorderClientReceipt-shaped objects (participant_id, display_name,
    channels, source_kind, recorder_key_sha256). Unknown phases and an
    idle/error take project to an empty tuple rather than inventing rows.
    """

    phase_text = str(phase or "").strip().lower()
    frozen = bool(receipts_frozen) or phase_text in _FROZEN_PHASES
    active = phase_text in _ACTIVE_PHASES
    armed = phase_text in _PRE_ROLL_PHASES
    if not (frozen or active or armed):
        return ()

    conflicted = {str(key) for key in conflicted_keys if str(key)}
    proven: dict[str, tuple[int, bool, str]] = {}
    shared_track_receipt: tuple[str, int, bool] | None = None
    for receipt in tuple(receipts)[:_MAX_SOURCES]:
        participant_id = str(getattr(receipt, "participant_id", "") or "")
        digest = str(getattr(receipt, "recorder_key_sha256", "") or "")
        channels_value = getattr(receipt, "channels", 0)
        channels = (
            channels_value
            if isinstance(channels_value, int)
            and not isinstance(channels_value, bool)
            else 0
        )
        in_conflict = digest in conflicted
        kind = str(getattr(receipt, "source_kind", "") or "musician")
        if kind == "reference_track":
            name = _bounded_name(
                getattr(receipt, "display_name", "") or "Shared Track"
            )
            shared_track_receipt = (name, channels, in_conflict)
            continue
        if participant_id:
            proven[participant_id] = (
                channels,
                in_conflict,
                _bounded_name(getattr(receipt, "display_name", "")),
            )

    def _state_for(proof: tuple[int, bool, str] | None) -> RecordingSourceState:
        if proof is not None and proof[1]:
            return RecordingSourceState.CONFLICTED
        if frozen:
            return (
                RecordingSourceState.FINALIZED
                if proof is not None
                else RecordingSourceState.MISSING
            )
        if active:
            return (
                RecordingSourceState.RECORDING
                if proof is not None
                else RecordingSourceState.WAITING
            )
        return RecordingSourceState.ARMED

    rows: list[RecordingSourcePresentation] = []
    seen: set[str] = set()
    for participant_id, display_name in tuple(roster)[:_MAX_SOURCES]:
        identity = str(participant_id or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        proof = proven.get(identity)
        rows.append(
            RecordingSourcePresentation(
                participant_id=identity,
                display_name=_bounded_name(display_name),
                kind="musician",
                state=_state_for(proof),
                channels=proof[0] if proof is not None else 0,
            )
        )

    if shared_track_receipt is not None:
        name, channels, in_conflict = shared_track_receipt
        rows.append(
            RecordingSourcePresentation(
                participant_id="",
                display_name=name,
                kind="shared_track",
                state=_state_for((channels, in_conflict, name)),
                channels=channels,
            )
        )
    elif shared_track_planned and (active or armed or frozen):
        rows.append(
            RecordingSourcePresentation(
                participant_id="",
                display_name="Shared Track",
                kind="shared_track",
                state=(
                    RecordingSourceState.MISSING
                    if frozen
                    else RecordingSourceState.WAITING
                    if active
                    else RecordingSourceState.ARMED
                ),
            )
        )
    return tuple(rows)


def summarize_recording_sources(
    rows: Iterable[RecordingSourcePresentation],
) -> Mapping[str, int]:
    """Bounded state counts for diagnostics — no identities, ever."""

    counts: dict[str, int] = {}
    for row in tuple(rows)[:_MAX_SOURCES]:
        counts[row.state.value] = counts.get(row.state.value, 0) + 1
    return counts
