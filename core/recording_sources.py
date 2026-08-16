"""Per-source recording truth projection for the live workspace.

One pure function turns the recording coordinator's existing take-scoped
evidence — roster identities, recorder receipts, conflict keys, and the
receipt-freeze fact — into bounded compatibility presentations. v0.26 exact
plan rows additionally keep Jamulus server stems, Local Originals, and Shared
Track distinct for Studio without exposing paths or capture fingerprints.

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
import math
from typing import Iterable, Mapping

from core.logical_sources import canonical_logical_source_id


class RecordingSourceState(str, Enum):
    """The finite per-source states the live workspace may claim."""

    ARMED = "armed"
    WAITING = "waiting"
    RECORDING = "recording"
    STOPPING = "stopping"
    CONFLICTED = "conflicted"
    MISSING = "missing"
    FINALIZED = "finalized"


class RecordingSourceKind(str, Enum):
    """The exact recorder/capture route represented by a live source row."""

    JAMULUS_SERVER = "jamulus_server"
    LOCAL_ORIGINAL = "local_original"
    SHARED_TRACK = "shared_track"


class RecordingSourcePresentationError(ValueError):
    """Raised when an exact live-source snapshot is incomplete or ambiguous."""


_PRE_ROLL_PHASES = frozenset({"preflight", "starting"})
# Count-in begins only after capture is active, so it is an explicit active
# phase; degrading it to pre-roll would misreport live sources as merely armed.
_ACTIVE_PHASES = frozenset({"recording", "count_in"})
_STOPPING_PHASES = frozenset({"stopping"})
_FROZEN_PHASES = frozenset(
    {"finalizing", "validating", "complete", "needs_attention"}
)
_MAX_SOURCES = 512
_MAX_NAME_CHARS = 128


@dataclass(frozen=True, repr=False)
class RecordingSourcePresentation:
    """One bounded, path-free source row for the recording workspace."""

    participant_id: str
    display_name: str
    # ``kind`` remains the card-level compatibility vocabulary consumed by the
    # participant grid. Only Jamulus stems are ``musician`` rows; Local
    # Originals remain distinct so one participant card cannot collapse several
    # input-map states. ``source_kind`` below is the exact capture route.
    kind: str  # "musician" | "local_original" | "shared_track"
    state: RecordingSourceState
    channels: int = 0
    logical_source_id: str = ""
    source_kind: RecordingSourceKind | str = ""
    # A Jamulus server source may retain its live channel so monitor faders and
    # meter pushes stay connected. Local Originals and Shared Track use -1;
    # Studio allocates presentation-only row keys from their proven logical IDs.
    channel_id: int = -1
    meter_level: float | None = None
    dropout_count: int | None = None
    overloaded: bool | None = None

    def __repr__(self) -> str:
        return "RecordingSourcePresentation(private=[redacted])"


def validate_exact_recording_sources(
    rows: Iterable[RecordingSourcePresentation],
) -> tuple[RecordingSourcePresentation, ...]:
    """Validate one complete, immutable live-source snapshot.

    The legacy receipt projection deliberately remains readable without stable
    IDs or exact topology. Studio calls this stricter boundary before rendering
    the v0.26 source view: one malformed, duplicate, or ambiguous row rejects
    the *whole* snapshot, so the UI cannot silently substitute roster order,
    display names, or guessed channel widths.
    """

    result: list[RecordingSourcePresentation] = []
    logical_source_ids: set[str] = set()
    server_channel_ids: set[int] = set()
    for index, row in enumerate(rows):
        if index >= _MAX_SOURCES:
            raise RecordingSourcePresentationError(
                "The live source snapshot exceeds the supported source limit."
            )
        if not isinstance(row, RecordingSourcePresentation):
            raise RecordingSourcePresentationError(
                "Every live source row must be a recording source presentation."
            )
        if not isinstance(row.state, RecordingSourceState):
            raise RecordingSourcePresentationError(
                "A live source row has an unsupported recording state."
            )
        try:
            source_kind = RecordingSourceKind(
                getattr(row.source_kind, "value", row.source_kind)
            )
        except ValueError as exc:
            raise RecordingSourcePresentationError(
                "A live source row has no exact capture route."
            ) from exc
        try:
            logical_source_id = canonical_logical_source_id(row.logical_source_id)
        except ValueError as exc:
            raise RecordingSourcePresentationError(
                "A live source row has no stable logical source identity."
            ) from exc
        if logical_source_id in logical_source_ids:
            raise RecordingSourcePresentationError(
                "The live source snapshot contains a duplicate logical source."
            )
        logical_source_ids.add(logical_source_id)

        display_name = _bounded_name(row.display_name)
        if display_name != row.display_name:
            raise RecordingSourcePresentationError(
                "A live source name is not bounded canonical text."
            )
        participant_id = str(row.participant_id or "").strip()
        if (
            len(participant_id) > _MAX_NAME_CHARS
            or participant_id != row.participant_id
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in participant_id)
        ):
            raise RecordingSourcePresentationError(
                "A live source participant identity is invalid."
            )
        if row.channels not in {1, 2} or isinstance(row.channels, bool):
            raise RecordingSourcePresentationError(
                "Every exact live source must declare mono or stereo topology."
            )
        if isinstance(row.channel_id, bool) or not isinstance(row.channel_id, int):
            raise RecordingSourcePresentationError(
                "A live source channel identity must be an integer."
            )

        if source_kind is RecordingSourceKind.SHARED_TRACK:
            if row.kind != "shared_track" or participant_id or row.channel_id != -1:
                raise RecordingSourcePresentationError(
                    "The Shared Track source row has conflicting ownership fields."
                )
        else:
            expected_kind = (
                "musician"
                if source_kind is RecordingSourceKind.JAMULUS_SERVER
                else "local_original"
            )
            if row.kind != expected_kind or not participant_id:
                raise RecordingSourcePresentationError(
                    "A performance source row requires one proven participant."
                )
            if source_kind is RecordingSourceKind.JAMULUS_SERVER:
                if row.channel_id < 0 or row.channel_id in server_channel_ids:
                    raise RecordingSourcePresentationError(
                        "Jamulus source channels must be unique and non-negative."
                    )
                server_channel_ids.add(row.channel_id)
            elif row.channel_id != -1:
                raise RecordingSourcePresentationError(
                    "Local Original rows do not own Jamulus monitor channels."
                )

        meter_level = row.meter_level
        if meter_level is not None and (
            isinstance(meter_level, bool)
            or not isinstance(meter_level, (int, float))
            or not math.isfinite(float(meter_level))
            or not 0.0 <= float(meter_level) <= 1.0
        ):
            raise RecordingSourcePresentationError(
                "A live source meter must be unavailable or between zero and one."
            )
        dropout_count = row.dropout_count
        if dropout_count is not None and (
            isinstance(dropout_count, bool)
            or not isinstance(dropout_count, int)
            or not 0 <= dropout_count <= 1_000_000
        ):
            raise RecordingSourcePresentationError(
                "A live source dropout count is outside the supported limits."
            )
        if row.overloaded is not None and type(row.overloaded) is not bool:
            raise RecordingSourcePresentationError(
                "A live source overload state must be true, false, or unavailable."
            )
        result.append(row)
    return tuple(result)


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
    stopping = phase_text in _STOPPING_PHASES
    armed = phase_text in _PRE_ROLL_PHASES
    if not (frozen or active or stopping or armed):
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
        if stopping:
            return (
                RecordingSourceState.STOPPING
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
                source_kind=RecordingSourceKind.JAMULUS_SERVER,
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
                source_kind=RecordingSourceKind.SHARED_TRACK,
            )
        )
    elif shared_track_planned and (active or stopping or armed or frozen):
        rows.append(
            RecordingSourcePresentation(
                participant_id="",
                display_name="Shared Track",
                kind="shared_track",
                state=(
                    RecordingSourceState.MISSING
                    if frozen
                    else RecordingSourceState.WAITING
                    if active or stopping
                    else RecordingSourceState.ARMED
                ),
                source_kind=RecordingSourceKind.SHARED_TRACK,
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
