"""WebJam beside a free Webex meeting, told truthfully.

ADR 0004 settled the shape: Webex is an independent application in its own
window. WebJam stores a meeting link and nothing else — no account, no token,
and no embedded runtime here.

Whether an Embedded App companion ships is a separate track's decision, and it
does not change anything in this module. A custom add-on needs a licensed
organization and a Control Hub administrator to approve it, which the musician
this product is for often does not have, so **no music feature may depend on
one either way**. :func:`music_features_require_meeting` exists to be asserted
against. What Music publishes to a companion when one does exist is ADR 0012.

What the second-window arrangement actually costs a musician is confusion, and
this module exists to remove three specific kinds of it:

* **Two mutes.** WebJam's mute and Webex's mute do different things, and one of
  them WebJam cannot see or set. Guessing which one is live is how a musician
  ends up playing over someone.
* **End is not end.** Leaving the jam does not close the meeting, and closing
  the meeting does not end the jam. Each app only ends itself.
* **One invite.** A bandmate should paste one thing, not chase two links and
  guess which is the music.

Everything here is pure text and state; no Qt, no network, no platform calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.meeting_link import (
    GENERIC_MEETING_SERVICE_KEY,
    identify_meeting_service,
    is_allowed_meeting_link,
    meeting_link_hostname,
    meeting_service_label,
)

# Webex is the primary meeting platform, so it is the name musician-facing
# copy uses when no link says otherwise. Zoom, Teams, Google Meet, FaceTime,
# and a neutral generic HTTPS host all remain valid through
# :mod:`core.meeting_link`, and a configured link names itself.
DEFAULT_MEETING_SERVICE = "Webex"

# WebJam's own mute sets the local monitor mix. Jamulus owns the signal your
# instrument sends to the band, and ``JamulusController.set_self_muted`` is
# unsupported, so WebJam must never present its mute as "the band stops
# hearing me".
WEBJAM_MUTE_SCOPE = "what you hear"
MEETING_MUTE_SCOPE = "your microphone in the meeting"


@dataclass(frozen=True, slots=True)
class MuteControl:
    """One mute control, who owns it, and whether WebJam can prove its state."""

    name: str
    scope: str
    owner: str
    verifiable: bool
    state_text: str
    action_label: str
    hint: str

    def describe(self) -> str:
        return f"{self.name} mute — {self.scope} ({self.state_text})"


@dataclass(frozen=True, slots=True)
class MuteSurface:
    """Both mutes side by side, so neither can be mistaken for the other."""

    webjam: MuteControl
    meeting: MuteControl | None = None

    @property
    def controls(self) -> tuple[MuteControl, ...]:
        return tuple(item for item in (self.webjam, self.meeting) if item is not None)

    def caution(self) -> str:
        """Return the sentence that prevents the expensive misunderstanding."""

        if self.meeting is None:
            return (
                "Muting here changes your monitor mix only. To stop your "
                "instrument reaching the band, use your instrument or interface."
            )
        return (
            "These are two different mutes. WebJam's changes what you hear; "
            f"{self.meeting.owner}'s changes what the meeting hears. Neither "
            "stops your instrument reaching the band."
        )


@dataclass(frozen=True, slots=True)
class EndSessionPrompt:
    """The End/Leave confirmation, including what it will *not* end."""

    title: str
    question: str
    meeting_note: str = ""

    def full_text(self) -> str:
        if not self.meeting_note:
            return self.question
        return f"{self.question}\n\n{self.meeting_note}"


@dataclass(frozen=True, slots=True)
class InviteMessage:
    """One clipboard block carrying everything a bandmate needs."""

    text: str
    includes_meeting: bool

    @property
    def line_count(self) -> int:
        return len(self.text.splitlines())


def service_name_for_link(meeting_url: str = "") -> str:
    """Return what to call the meeting in copy: its own name, or Webex.

    A configured link identifies itself, so a Zoom user reads "Zoom". With no
    link, or an unusable one, copy falls back to the primary platform rather
    than inventing a neutral phrase nobody says out loud.
    """

    candidate = str(meeting_url or "").strip()
    if not candidate or not is_allowed_meeting_link(candidate):
        return DEFAULT_MEETING_SERVICE
    service = identify_meeting_service(candidate)
    if service and service != GENERIC_MEETING_SERVICE_KEY:
        return meeting_service_label(service)
    # An unbranded host has no name to use, and "Meeting service mute" reads
    # like a placeholder. The host the musician typed is what they recognise.
    return meeting_link_hostname(candidate) or DEFAULT_MEETING_SERVICE


def music_features_require_meeting() -> bool:
    """Music never depends on a meeting, an add-on, or a licensed organization.

    This is asserted by the test suite rather than merely documented, so a
    future change that gates Song tools, songwriting help, Shared Track, or
    recording behind Webex fails a test instead of shipping.
    """

    return False


def describe_mutes(
    *,
    webjam_muted_participants: int = 0,
    participant_count: int = 0,
    meeting_configured: bool = False,
    meeting_service: str = DEFAULT_MEETING_SERVICE,
) -> MuteSurface:
    """Return both mute controls, each labelled with what it really does."""

    service = str(meeting_service or DEFAULT_MEETING_SERVICE).strip() or (
        DEFAULT_MEETING_SERVICE
    )
    muted = max(0, int(webjam_muted_participants))
    total = max(0, int(participant_count))
    if muted <= 0:
        state = "nobody muted in your mix"
    elif total and muted >= total:
        state = "everyone muted in your mix"
    else:
        state = f"{muted} muted in your mix"

    webjam = MuteControl(
        name="WebJam",
        scope=WEBJAM_MUTE_SCOPE,
        owner="WebJam",
        verifiable=True,
        state_text=state,
        action_label="Mute in your mix",
        hint=(
            "Silences a musician in your monitor mix on this computer. It does "
            "not change what anyone else hears."
        ),
    )
    if not meeting_configured:
        return MuteSurface(webjam=webjam)

    meeting = MuteControl(
        name=service,
        scope=MEETING_MUTE_SCOPE,
        owner=service,
        # ADR 0004: the external app exposes no verifiable mute state to this
        # integration, so WebJam reports that it does not know rather than
        # showing a control that would be a guess.
        verifiable=False,
        state_text=f"WebJam cannot read {service}'s mute",
        action_label=f"Open {service} to mute",
        hint=(
            f"Brings {service} forward so you can use its own Mute control. "
            "WebJam does not change or verify it."
        ),
    )
    return MuteSurface(webjam=webjam, meeting=meeting)


def end_session_prompt(
    *,
    hosting: bool,
    recording_active: bool = False,
    meeting_configured: bool = False,
    meeting_service: str = DEFAULT_MEETING_SERVICE,
) -> EndSessionPrompt:
    """Return the End/Leave confirmation, stating that the meeting survives."""

    service = str(meeting_service or DEFAULT_MEETING_SERVICE).strip() or (
        DEFAULT_MEETING_SERVICE
    )
    if hosting:
        title = "End Jam?"
        question = (
            "End this jam for everyone?\n\n"
            "WebJam will safely finish any recording and stop the hosted session."
        )
    elif recording_active:
        title = "Leave Jam?"
        question = (
            "Leave this jam?\n\nThe host's recording will keep running. "
            "Only this Mac will disconnect."
        )
    else:
        title = "Leave Jam?"
        question = (
            "Leave this jam?\n\n"
            "The host and other musicians will stay connected."
        )

    note = ""
    if meeting_configured:
        note = (
            f"Your {service} meeting is separate and stays open. "
            f"Leave it in {service} when you're done."
        )
    return EndSessionPrompt(title=title, question=question, meeting_note=note)


def meeting_recording_note(
    *,
    meeting_service: str = DEFAULT_MEETING_SERVICE,
) -> str:
    """State that a meeting recording is not a WebJam take.

    They are different files, made by different applications, from different
    audio. Someone who assumes otherwise finds out after the session, when the
    take they wanted does not exist.
    """

    service = str(meeting_service or DEFAULT_MEETING_SERVICE).strip() or (
        DEFAULT_MEETING_SERVICE
    )
    return (
        f"A {service} recording is not a WebJam take. WebJam records the jam "
        f"here; {service} records its own call. Neither becomes the other."
    )


def meeting_departure_note(
    *,
    meeting_service: str = DEFAULT_MEETING_SERVICE,
) -> str:
    """Return the other half of "end is not end", for the meeting side."""

    service = str(meeting_service or DEFAULT_MEETING_SERVICE).strip() or (
        DEFAULT_MEETING_SERVICE
    )
    return (
        f"Leaving or closing the {service} meeting does not end the jam. "
        "Return to WebJam to leave or end the session here."
    )


def build_invite_message(
    *,
    join_link: str,
    session_name: str = "",
    meeting_url: str = "",
    participant_noun: str = "musician",
    song_line: str = "",
    creator_profile_key: str = "music",
) -> InviteMessage:
    """Return one paste that carries the jam link and, if set, the meeting.

    The ``webjam://`` link is passed through untouched — this changes what the
    clipboard holds, not the invitation protocol. A meeting link is included
    only when it passes the same validation the rest of WebJam applies, so a
    malformed or unsupported link is dropped. Art copy describes the making
    room and optional work sharing; the default preserves existing Music
    callers. V3 invitations name the supported manual-paste route.
    """

    link = str(join_link or "").strip()
    if not link:
        raise ValueError("an invite needs a join link")

    name = " ".join(str(session_name or "").split())[:80]
    noun = str(participant_noun or "musician").strip() or "musician"
    art = str(creator_profile_key or "").strip().casefold() == "art"
    headline = f"Join {name} on WebJam:" if name else (
        "Join this art room on WebJam:" if art else "Join this jam on WebJam:"
    )
    # This is copy around an already-created link, not a second parser or a
    # rewrite of its opaque capability. Canonical v3 links use manual paste.
    manual_paste = link.startswith("webjam://join?") and (
        "v=3" in link.partition("?")[2].split("&")
    )
    lines = [headline]
    if manual_paste:
        lines.append("Open WebJam, choose Join, then paste this full invitation.")
    lines.append(link)
    # When the room has already chosen a song, say so. A joiner arrives
    # knowing what they are playing instead of being asked to pick something.
    song = " ".join(str(song_line or "").split())[:120]
    if song and not art:
        lines.extend(["", f"Song: {song}"])
    candidate = str(meeting_url or "").strip()
    # Any meeting link the rest of WebJam accepts is carried, not just Webex.
    includes_meeting = bool(candidate) and is_allowed_meeting_link(candidate)
    if includes_meeting:
        site = meeting_link_hostname(candidate) or "the meeting"
        if art:
            service = service_name_for_link(candidate)
            lines.extend(
                [
                    "",
                    f"Optional {service} conversation and work sharing ({site}):",
                    candidate,
                    "",
                    "WebJam opens the art room. Bring your own tools, paper, or usual app. "
                    "The meeting is separate and optional; WebJam does not run it.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    f"Optional video chat ({site}):",
                    candidate,
                    "",
                    "The WebJam link carries the music. The meeting link is a "
                    "separate app for talking between takes — WebJam does not run "
                    "it, and you do not need it to play.",
                ]
            )
    elif art:
        lines.extend(
            [
                "",
                "WebJam opens the art room. Bring your own tools, paper, or usual app. "
                "You can make together without a meeting.",
            ]
        )
    elif not manual_paste:
        lines.extend(
            [
                "",
                f"Open the link in WebJam to join as a {noun}.",
            ]
        )
    return InviteMessage(text="\n".join(lines), includes_meeting=includes_meeting)


__all__ = [
    "DEFAULT_MEETING_SERVICE",
    "EndSessionPrompt",
    "InviteMessage",
    "MEETING_MUTE_SCOPE",
    "MuteControl",
    "MuteSurface",
    "WEBJAM_MUTE_SCOPE",
    "build_invite_message",
    "service_name_for_link",
    "describe_mutes",
    "end_session_prompt",
    "meeting_departure_note",
    "meeting_recording_note",
    "music_features_require_meeting",
]
