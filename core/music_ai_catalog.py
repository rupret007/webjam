"""Which Song tools an account can actually run, decided at runtime.

Music AI workflow slugs belong to the account, not to the platform. The API
reference's own example shows a beat-and-BPM workflow whose slug is
``untitled-workflow-e78c2e``, so any hardcoded list of slugs would be a guess
dressed as a feature. This module therefore starts from ``GET /workflow`` — the
real list for the real key — and decides which product verb each entry can
serve by reading its name, slug, and description.

The consequence is deliberate: a verb with no matching workflow is reported
``unsupported`` with the reason why. It is never shown as a button that fails
later, and it is never quietly stubbed to look like it worked.

The one exception is stem separation, where the quick start documents
``music-ai/stems-vocals-accompaniment`` as living in the shared ``music-ai``
namespace "accessible to all users". That single slug is offered as a fallback,
and it is labelled as the shared template rather than as the account's own.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.music_ai_client import DOCUMENTED_STEMS_WORKFLOW, MusicAIWorkflow

# Result kinds decide what the session does with a finished job, not how it
# is run. Everything the API returns is a URL; this says what it means.
RESULT_AUDIO_SET = "audio_set"
RESULT_AUDIO_FILE = "audio_file"
RESULT_CHORDS = "chords"
RESULT_LYRICS = "lyrics"
RESULT_SECTIONS = "sections"


@dataclass(frozen=True, slots=True)
class SongToolVerb:
    """One thing a musician can ask for, and how to recognise it upstream."""

    key: str
    label: str
    summary: str
    result_kind: str
    keywords: tuple[str, ...]
    # Words that mean a workflow belongs to a *different* verb. Chord and key
    # detection reads a song; pitch shifting rewrites one. Both mention "key".
    excludes: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    primary: bool = True
    unsupported_hint: str = ""


SONG_TOOL_VERBS: tuple[SongToolVerb, ...] = (
    SongToolVerb(
        key="stems",
        label="Split stems",
        summary="Separate a file into vocals, drums, bass, and the rest.",
        result_kind=RESULT_AUDIO_SET,
        keywords=("stem", "separat", "isolat", "vocal remov", "karaoke"),
        unsupported_hint="No stem-separation workflow is on this account.",
    ),
    SongToolVerb(
        key="chords",
        label="Chords & key",
        summary="Read the chords, key, and tempo out of a file.",
        result_kind=RESULT_CHORDS,
        keywords=(
            "chord",
            "harmon",
            "beat",
            "bpm",
            "key detect",
            "tempo detect",
            "metadata",
        ),
        excludes=("shift", "stretch", "transpose", "change key", "change tempo"),
        unsupported_hint=(
            "No chord, key, or beat-detection workflow is on this account."
        ),
    ),
    SongToolVerb(
        key="lyrics",
        label="Lyrics",
        summary="Transcribe the words, aligned to the recording.",
        result_kind=RESULT_LYRICS,
        keywords=("lyric", "transcri", "align", "subtitle", "caption"),
        excludes=("chord", "beat", "drum"),
        unsupported_hint="No lyric-transcription workflow is on this account.",
    ),
    SongToolVerb(
        key="sections",
        label="Sections",
        summary="Find where the verse, chorus, and bridge start.",
        result_kind=RESULT_SECTIONS,
        keywords=("section", "structur", "segment", "arrangement", "song part"),
        unsupported_hint=(
            "No song-section workflow is on this account. Music AI does not "
            "offer section detection to every account."
        ),
    ),
    SongToolVerb(
        key="key_tempo",
        label="Change key/tempo",
        summary="Render the file at a new key or a new speed.",
        result_kind=RESULT_AUDIO_FILE,
        keywords=(
            "pitch",
            "transpose",
            "time stretch",
            "timestretch",
            "speed",
            "change key",
            "change tempo",
        ),
        requires=("shift", "stretch", "transpose", "change", "speed", "pitch"),
        excludes=("detect", "analy"),
        unsupported_hint=(
            "No pitch-shift or time-stretch workflow is on this account."
        ),
    ),
    SongToolVerb(
        key="master",
        label="Master",
        summary="Run a mastering pass over a mix.",
        result_kind=RESULT_AUDIO_FILE,
        keywords=("master",),
        excludes=("stem", "separat"),
        unsupported_hint="No mastering workflow is on this account.",
    ),
    SongToolVerb(
        key="enhance",
        label="Clean up audio",
        summary="Reduce noise or restore a rough recording.",
        result_kind=RESULT_AUDIO_FILE,
        keywords=("enhance", "denoise", "noise", "cleanup", "clean up", "restor"),
        excludes=("stem", "separat"),
        primary=False,
        unsupported_hint="No audio-enhancement workflow is on this account.",
    ),
)

_VERBS_BY_KEY = {verb.key: verb for verb in SONG_TOOL_VERBS}

# Moises features that are part of the consumer app rather than the developer
# API. The reference exposes exactly four resources — uploads, jobs, workflows,
# and the application behind the key — so there is nothing to call for these.
# They are listed so the answer to "why isn't that here?" is visible, not so
# they can be stubbed.
UNSUPPORTED_MOISES_FEATURES: tuple[tuple[str, str], ...] = (
    (
        "Your Moises song library",
        "The developer API has no library or account endpoints. WebJam works "
        "from a file you pick on this computer.",
    ),
    (
        "Live separation while the track plays",
        "Music AI runs asynchronous batch jobs over uploaded files. There is "
        "no streaming endpoint to drive a live player.",
    ),
    (
        "The app's practice tools (loop trainer, speed trainer, metronome)",
        "These run inside the Moises app. No API workflow performs them.",
    ),
    (
        "Signing in with a Moises account",
        "The API authenticates with a key created at music.ai/dash. WebJam "
        "never asks for a Moises password.",
    ),
)


@dataclass(frozen=True, slots=True)
class SongToolCapability:
    """Whether one verb can run here, and with which workflow."""

    verb: SongToolVerb
    workflow_slug: str = ""
    workflow_name: str = ""
    shared_template: bool = False
    reason: str = ""

    @property
    def key(self) -> str:
        return self.verb.key

    @property
    def label(self) -> str:
        return self.verb.label

    @property
    def supported(self) -> bool:
        return bool(self.workflow_slug)

    def describe(self) -> str:
        if not self.supported:
            return f"{self.label} — unavailable. {self.reason}"
        if self.shared_template:
            return f"{self.label} — {self.workflow_name} (shared Music AI template)"
        return f"{self.label} — {self.workflow_name}"


@dataclass(frozen=True, slots=True)
class SongToolCatalog:
    """The resolved verb list for one API key."""

    capabilities: tuple[SongToolCapability, ...] = ()
    workflow_count: int = 0
    discovered: bool = False
    error: str = ""
    # Whether looking again could plausibly succeed. A network blip yes; a key
    # the API rejected, no -- telling someone to retry a wrong key wastes
    # their evening.
    retryable: bool = False

    @property
    def available(self) -> tuple[SongToolCapability, ...]:
        return tuple(item for item in self.capabilities if item.supported)

    @property
    def unavailable(self) -> tuple[SongToolCapability, ...]:
        return tuple(item for item in self.capabilities if not item.supported)

    @property
    def usable(self) -> bool:
        return bool(self.available)

    def capability(self, verb_key: str) -> SongToolCapability | None:
        for item in self.capabilities:
            if item.key == verb_key:
                return item
        return None

    def summary_line(self) -> str:
        if self.error:
            return self.error
        if not self.discovered:
            return "Song tools have not checked this account's workflows yet."
        if not self.available:
            return (
                f"This Music AI account has {self.workflow_count} workflows, "
                "none of which match a Song tool. Add one in the Music AI "
                "dashboard."
            )
        names = ", ".join(item.label for item in self.available)
        return f"Available from your Music AI account: {names}."


def resolve_song_tools(
    workflows: tuple[MusicAIWorkflow, ...] | list[MusicAIWorkflow],
    *,
    allow_shared_templates: bool = True,
) -> SongToolCatalog:
    """Match an account's real workflow list onto WebJam's product verbs."""

    entries = [item for item in workflows if isinstance(item, MusicAIWorkflow)]
    capabilities: list[SongToolCapability] = []
    for verb in SONG_TOOL_VERBS:
        match = _best_match(verb, entries)
        if match is not None:
            capabilities.append(
                SongToolCapability(
                    verb=verb,
                    workflow_slug=match.slug,
                    workflow_name=match.name,
                )
            )
            continue
        if (
            allow_shared_templates
            and verb.key == "stems"
            # Documented in the quick start as available to every account.
            and not any(item.slug == DOCUMENTED_STEMS_WORKFLOW for item in entries)
        ):
            capabilities.append(
                SongToolCapability(
                    verb=verb,
                    workflow_slug=DOCUMENTED_STEMS_WORKFLOW,
                    workflow_name="Vocals and accompaniment",
                    shared_template=True,
                )
            )
            continue
        capabilities.append(
            SongToolCapability(verb=verb, reason=verb.unsupported_hint)
        )

    return SongToolCatalog(
        capabilities=tuple(capabilities),
        workflow_count=len(entries),
        discovered=True,
    )


def failed_catalog(reason: str, *, retryable: bool = True) -> SongToolCatalog:
    """Return a catalog that offers nothing, says why, and whether to retry."""

    return SongToolCatalog(
        capabilities=tuple(
            SongToolCapability(verb=verb, reason=str(reason))
            for verb in SONG_TOOL_VERBS
        ),
        discovered=False,
        error=str(reason),
        retryable=bool(retryable),
    )


def verb_for_key(verb_key: str) -> SongToolVerb | None:
    return _VERBS_BY_KEY.get(str(verb_key))


def _best_match(
    verb: SongToolVerb,
    workflows: list[MusicAIWorkflow],
) -> MusicAIWorkflow | None:
    best: MusicAIWorkflow | None = None
    best_score = 0
    for workflow in workflows:
        score = _score(verb, workflow)
        if score > best_score:
            best_score = score
            best = workflow
    return best


def _score(verb: SongToolVerb, workflow: MusicAIWorkflow) -> int:
    haystack = workflow.search_text
    if any(token in haystack for token in verb.excludes):
        return 0
    if verb.requires and not any(token in haystack for token in verb.requires):
        return 0
    hits = [token for token in verb.keywords if token in haystack]
    if not hits:
        return 0
    score = len(hits)
    # A workflow whose *name* says what it does is a better match than one that
    # only mentions the word somewhere in a long description.
    name_and_slug = f"{workflow.name} {workflow.slug}".lower()
    score += sum(2 for token in hits if token in name_and_slug)
    return score


__all__ = [
    "RESULT_AUDIO_FILE",
    "RESULT_AUDIO_SET",
    "RESULT_CHORDS",
    "RESULT_LYRICS",
    "RESULT_SECTIONS",
    "SONG_TOOL_VERBS",
    "UNSUPPORTED_MOISES_FEATURES",
    "SongToolCapability",
    "SongToolCatalog",
    "SongToolVerb",
    "failed_catalog",
    "resolve_song_tools",
    "verb_for_key",
]
