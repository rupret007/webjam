"""Session-scoped song state: what the room knows, and what may leave it.

This is the sidecar the live Music surface renders. It holds the song form the
room has written, whatever a Music AI job has genuinely detected, and the rules
that decide whether a file is allowed to leave this computer at all.

Two boundaries are enforced here rather than in the UI, so they hold no matter
which button is wired to them later:

* **Nothing is uploaded that the user did not choose.** There is no code path
  that discovers a file. The live Jamulus mix is a named, always-rejected
  source, so a future caller that reaches for it fails closed against a test.
* **The host confirms uploads.** A guest can ask; only the host can send. This
  matches the Shared Track rule already in the product — the host owns what
  enters and leaves the room.

Late joiners are the other job. A musician who arrives twenty minutes in cannot
see what the room decided, because session notes are local to each computer.
This builds the catch-up they can actually be shown, and the compact sheet the
host can paste into band chat, without inventing a sync protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from core.music_ai_catalog import SongToolCapability, SongToolCatalog
from core.music_ai_client import missing_key_message
from core.music_ai_results import SongToolRun
from core.song_form import SongForm, merge_sections, parse_song_form
from core.song_help import ChordAdvice, WritingAdvice, detected_sections
from core.song_help import suggest_chords, suggest_writing

# The live jam is never an upload candidate. Naming it makes the refusal
# testable instead of relying on nobody ever wiring it up.
LIVE_MIX_SOURCE = "<live-jamulus-mix>"

SOURCE_PICKED_FILE = "picked_file"
SOURCE_SHARED_TRACK = "shared_track"

_ALLOWED_SOURCES = frozenset({SOURCE_PICKED_FILE, SOURCE_SHARED_TRACK})
_MAX_RUNS = 12
_MAX_SHEET_CHARS = 900


@dataclass(frozen=True, slots=True)
class UploadDecision:
    """Whether one specific file may be sent to Music AI, and why."""

    allowed: bool
    reason: str = ""
    confirmation_title: str = ""
    confirmation_body: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed


@dataclass(frozen=True, slots=True)
class SharedTrackView:
    """The read-only Shared Track facts an overlay is allowed to state."""

    loaded: bool = False
    playing: bool = False
    source_name: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    host_controlled: bool = True

    def status_line(self) -> str:
        if not self.loaded:
            return "No Shared Track loaded."
        name = self.source_name or "Shared Track"
        if self.playing:
            return f"{name} — playing {_clock(self.position_s)}"
        return f"{name} — paused {_clock(self.position_s)}"


@dataclass(frozen=True, slots=True)
class CatchUp:
    """What a musician who just arrived needs in order to play the next take."""

    joined_late: bool
    headline: str
    lines: tuple[str, ...] = ()
    sheet_available: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.lines)


def evaluate_upload(
    *,
    capability: SongToolCapability | None,
    source_kind: str,
    path: str,
    is_host: bool,
    has_api_key: bool,
) -> UploadDecision:
    """Decide whether this exact file may be sent, failing closed by default.

    Every refusal names the thing the musician can do about it. The order
    matters: the cheapest, most likely problem is reported first so a guest
    without a key is not told to go find the host.
    """

    if not has_api_key:
        return UploadDecision(allowed=False, reason=missing_key_message())
    if capability is None:
        return UploadDecision(
            allowed=False, reason="That Song tool is not available."
        )
    if not capability.supported:
        return UploadDecision(
            allowed=False,
            reason=capability.reason or f"{capability.label} is unavailable.",
        )
    if not is_host:
        return UploadDecision(
            allowed=False,
            reason=(
                "Only the host can send a file to Music AI. Ask the host to "
                f"run {capability.label} for the room."
            ),
        )
    if source_kind == LIVE_MIX_SOURCE or path == LIVE_MIX_SOURCE:
        return UploadDecision(
            allowed=False,
            reason=(
                "WebJam never uploads the live jam. Pick a file on this "
                "computer instead."
            ),
        )
    if source_kind not in _ALLOWED_SOURCES:
        return UploadDecision(
            allowed=False,
            reason="Pick a file on this computer first.",
        )

    candidate = str(path or "").strip()
    if not candidate:
        return UploadDecision(
            allowed=False, reason="Pick a file on this computer first."
        )
    resolved = Path(candidate)
    try:
        readable = resolved.is_file()
        size = resolved.stat().st_size if readable else 0
    except OSError:
        readable = False
        size = 0
    if not readable:
        return UploadDecision(
            allowed=False,
            reason="WebJam cannot read that file. Pick another one.",
        )
    if size <= 0:
        return UploadDecision(allowed=False, reason="That file is empty.")

    origin = (
        "the session's Shared Track"
        if source_kind == SOURCE_SHARED_TRACK
        else "a file you picked"
    )
    return UploadDecision(
        allowed=True,
        confirmation_title="Send this file to Music AI?",
        confirmation_body=(
            f"{resolved.name} ({_megabytes(size)}) will be uploaded to Music AI "
            f"to run {capability.label}.\n\n"
            f"Source: {origin}. The live jam is never uploaded.\n"
            "The file leaves this computer. Only send music you have the "
            "rights to."
        ),
    )


class SongWorkbench:
    """The song this session is working on, plus anything it has detected."""

    def __init__(self, *, title: str = "", notes: str = "") -> None:
        self._title = str(title or "")
        self._notes = str(notes or "")
        self._runs: list[SongToolRun] = []
        self._catalog: SongToolCatalog | None = None

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def set_notes(self, notes: str) -> None:
        self._notes = str(notes or "")

    def set_title(self, title: str) -> None:
        self._title = str(title or "")

    def set_catalog(self, catalog: SongToolCatalog | None) -> None:
        self._catalog = catalog

    def attach_run(self, run: SongToolRun) -> None:
        """Record a finished Song tools run so the session can show it."""

        if not isinstance(run, SongToolRun):
            raise TypeError("run must be a SongToolRun")
        self._runs = [item for item in self._runs if item.job_id != run.job_id]
        self._runs.append(run)
        del self._runs[:-_MAX_RUNS]

    def clear_runs(self) -> None:
        self._runs = []

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------
    @property
    def catalog(self) -> SongToolCatalog | None:
        return self._catalog

    @property
    def runs(self) -> tuple[SongToolRun, ...]:
        return tuple(self._runs)

    @property
    def form(self) -> SongForm:
        """Return the song sheet, with detected facts folded in behind it."""

        form = parse_song_form(self._notes, title=self._title)
        for run in self._runs:
            detail = run.label
            form = form.with_detected(
                key=run.detected_key,
                tempo=run.detected_tempo,
                detail=detail,
            )
            if run.detected_sections:
                form = replace(
                    form,
                    sections=merge_sections(
                        form.sections,
                        detected_sections(run.detected_sections, detail),
                    ),
                )
        return form

    def writing_advice(self) -> WritingAdvice:
        return suggest_writing(self.form)

    def chord_advice(self, role: str = "") -> ChordAdvice:
        return suggest_chords(self.form, role=role)

    def stems(self) -> tuple[str, ...]:
        """Return local paths of separated stems, newest run first."""

        paths: list[str] = []
        for run in reversed(self._runs):
            if run.verb_key != "stems":
                continue
            for artifact in run.audio_artifacts:
                if artifact.local_path and artifact.local_path not in paths:
                    paths.append(artifact.local_path)
        return tuple(paths)

    def lyrics(self) -> str:
        for run in reversed(self._runs):
            if run.lyrics_text:
                return run.lyrics_text
        return ""

    def detected_chords(self) -> tuple[str, ...]:
        for run in reversed(self._runs):
            if run.chord_symbols:
                return run.chord_symbols
        return ()

    def conductor_line(self) -> str:
        """Return one line of song truth for the conductor, or ``""``."""

        form = self.form
        if not form.has_content:
            return ""
        return form.summary_line()

    # ------------------------------------------------------------------
    # Late join
    # ------------------------------------------------------------------
    def catch_up(
        self,
        *,
        shared_track: SharedTrackView | None = None,
        elapsed_seconds: float = 0.0,
        joined_late_after_seconds: float = 120.0,
        is_host: bool = False,
    ) -> CatchUp:
        """Return what someone who just arrived should be shown, if anything."""

        elapsed = max(0.0, float(elapsed_seconds))
        joined_late = elapsed >= max(0.0, float(joined_late_after_seconds))
        form = self.form
        lines: list[str] = []

        if shared_track is not None and shared_track.loaded:
            lines.append(shared_track.status_line())
        if form.has_content:
            lines.append(form.summary_line())
        elif not is_host:
            # Notes never leave the computer that typed them, so a guest with
            # an empty canvas genuinely has no sheet. Say that rather than
            # implying the room has nothing written down.
            lines.append(
                "No song sheet on this computer. Session notes stay local — "
                "ask the host to share theirs to chat."
            )
        for run in reversed(self._runs[-2:]):
            lines.append(run.summary_line())

        headline = (
            f"You joined {_minutes(elapsed)} in"
            if joined_late
            else "Where the session is"
        )
        return CatchUp(
            joined_late=joined_late,
            headline=headline,
            lines=tuple(lines[:4]),
            sheet_available=form.has_content,
        )

    def shareable_sheet(self) -> str:
        """Return a compact song sheet the host can paste into band chat.

        Chat is the only shared-text path a live session already has, so this
        reuses it instead of adding a sync protocol. It is bounded because it
        travels over Jamulus chat, and it carries no file paths.
        """

        form = self.form
        if not form.has_content:
            return ""
        parts: list[str] = []
        if form.title:
            parts.append(form.title)
        if form.key is not None:
            parts.append(f"Key {form.key.value}")
        if form.tempo is not None:
            parts.append(f"{form.tempo.value} BPM")
        header = " · ".join(parts)

        lines = [header] if header else []
        for section in form.sections[:6]:
            if section.chords:
                lines.append(f"{section.label}: {section.chord_line}")
            else:
                lines.append(f"{section.label}:")
        return "\n".join(lines)[:_MAX_SHEET_CHARS]


def _clock(seconds: float) -> str:
    total = max(0, int(float(seconds or 0.0)))
    return f"{total // 60}:{total % 60:02d}"


def _minutes(seconds: float) -> str:
    total = max(0, int(float(seconds or 0.0)))
    if total < 90:
        return f"{total} seconds"
    return f"{round(total / 60)} minutes"


def _megabytes(size: int) -> str:
    megabytes = max(0.0, float(size)) / (1024 * 1024)
    if megabytes < 0.1:
        return "under 0.1 MB"
    return f"{megabytes:.1f} MB"


__all__ = [
    "LIVE_MIX_SOURCE",
    "SOURCE_PICKED_FILE",
    "SOURCE_SHARED_TRACK",
    "CatchUp",
    "SharedTrackView",
    "SongWorkbench",
    "UploadDecision",
    "evaluate_upload",
]
