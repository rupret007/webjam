"""WebJam beside a free Webex meeting, told truthfully.

ADR 0004 settled the shape: Webex is an independent application in its own
window. WebJam stores a meeting link and nothing else — no account, no token,
no embedded runtime, and no Webex Embedded App. That last point is a product
decision as much as a technical one. A custom Webex add-on requires a licensed
organization and a Control Hub administrator to approve it, which the musician
this product is for does not have. So no WebJam music feature may depend on
one, and :func:`music_features_require_meeting` exists to be asserted against.

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

from core.webex_url import is_allowed_webex_url, webex_site_hostname

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


def music_features_require_meeting() -> bool:
    """Music never depends on a meeting, a Webex add-on, or a licensed org.

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


def meeting_departure_note(
    *,
    meeting_service: str = DEFAULT_MEETING_SERVICE,
) -> str:
    """Return the other half of "end is not end", for the meeting side."""

    service = str(meeting_service or DEFAULT_MEETING_SERVICE).strip() or (
        DEFAULT_MEETING_SERVICE
    )
    return (
        f"Closing {service} does not end the jam. WebJam keeps playing until "
        "you end the session here."
    )


def build_invite_message(
    *,
    join_link: str,
    session_name: str = "",
    meeting_url: str = "",
    participant_noun: str = "musician",
) -> InviteMessage:
    """Return one paste that carries the jam link and, if set, the meeting.

    The ``webjam://`` link is passed through untouched — this changes what the
    clipboard holds, not the invitation protocol. A meeting link is included
    only when it passes the same validation the rest of WebJam applies, so a
    malformed or non-Webex link is dropped rather than pasted into a bandmate's
    chat window.
    """

    link = str(join_link or "").strip()
    if not link:
        raise ValueError("an invite needs a join link")

    name = " ".join(str(session_name or "").split())[:80]
    noun = str(participant_noun or "musician").strip() or "musician"
    headline = f"Join {name} on WebJam:" if name else "Join this jam on WebJam:"

    lines = [headline, link]
    candidate = str(meeting_url or "").strip()
    includes_meeting = bool(candidate) and is_allowed_webex_url(candidate)
    if includes_meeting:
        site = webex_site_hostname(candidate) or "the meeting"
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
    else:
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
    "describe_mutes",
    "end_session_prompt",
    "meeting_departure_note",
    "music_features_require_meeting",
]
