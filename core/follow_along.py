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


def lesson_setup_steps(*, profile: str, hosting: bool, service: str = "") -> str:
    meeting = service or "your meeting"
    if hosting:
        steps = (f"1. Choose YouTube and open it in your browser.\n"
                 f"2. Join {meeting}; share that lesson with computer sound.\n"
                 "3. Keep faces beside the lesson. You control pause and resume.")
    else:
        steps = (f"1. Join {meeting} to see the host's shared YouTube lesson and faces.\n"
                 "2. Listen to that one copy; keep your own player closed.\n"
                 "3. Ask the host to pause or resume.")
    if profile == "music":
        steps += ("\nJamulus is not required for video practice. "
                  "Take turns; meeting playback is delayed. If connected to Jamulus, "
                  "stop your instrument at its send control. Listening mute does not stop your send.")
    return steps


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
            " Take turns for video practice. If connected to Jamulus, stop your instrument "
            "at its send control. WebJam mix mute changes only what you hear."
        )
    return steps
