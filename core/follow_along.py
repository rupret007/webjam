"""Creative intent and external media ownership for following a lesson."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FollowAlongChoice:
    key: str
    title: str
    description: str
    action: str


PLAY_ALONG_CHOICES = (
    FollowAlongChoice(
        "lesson", "Learn from a YouTube video",
        "Watch one shared video, talk and take turns. The meeting carries the song and voices.",
        "Set up video practice",
    ),
    FollowAlongChoice(
        "ensemble", "Play together with a backing track",
        "Hear one song and the musicians through Jamulus. Use Webex for faces; its video arrives later.",
        "Open Shared Track",
    ),
)


def lesson_sound_guidance(*, profile: str, hosting: bool, service: str = "") -> str:
    meeting = service or "your meeting"
    if hosting:
        share = (
            "Webex app: Share the lesson window with Include computer sound. "
            "Browser meeting: share the lesson tab with tab audio."
            if service == "Webex" else
            f"Share the lesson window or tab with computer sound in {meeting}."
        )
        steps = (
            f"Open the YouTube lesson in your browser, then join the meeting. {share} "
            "Keep faces beside the lesson. Pause and resume in the browser when asked. "
            "The YouTube player volume changes the shared lesson; your meeting's speaker volume "
            "changes what you hear and microphone mute controls your voice. Use headphones to avoid echo."
        )
    else:
        steps = (
            f"Join {meeting} to watch the host's lesson and see each other's faces. "
            "Listen to that shared copy; leave your own YouTube player closed. "
            "Ask the host to pause or resume; the host controls the browser. Your meeting speaker volume changes what you hear; "
            "its microphone mute controls your voice. Use headphones."
        )
    if profile == "music":
        steps += (
            " Take turns for video practice. Jamulus remains live: stop your instrument "
            "at its send control. WebJam mix mute changes only what you hear."
        )
    return steps
