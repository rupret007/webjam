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


def lesson_setup_steps(*, profile: str, hosting: bool, service: str = "",
                       meeting_configured: bool = False) -> str:
    meeting = service or "your meeting"
    if hosting:
        invitation = ("Use Copy Link to send guests this meeting link." if meeting_configured else
                      "Use Add Link for your meeting, then Copy Link to send it to guests.")
        steps = (f"1. Choose YouTube and open it in your browser.\n"
                 f"2. {invitation}\n"
                 f"3. Join {meeting}, share the lesson with computer sound, and keep faces visible. "
                 "You control pause and resume.")
    elif not meeting_configured:
        steps = ("1. Ask the host for the meeting link, then use Add Link.\n"
                 "2. Join the meeting and listen to the host's shared copy; keep your own player closed.\n"
                 "3. Keep faces visible and ask the host to pause or resume.")
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
            "A room invitation includes only the meeting link available when it was copied. "
            "If you add or change it later, send it with Copy Link; guests use Add Link or Change Link. "
            "Keep faces beside the lesson. Pause and resume in the browser when asked. "
            "The YouTube player volume changes the shared lesson; your meeting's speaker volume "
            "changes what you hear and microphone mute controls your voice. Use headphones to avoid echo."
        )
    else:
        steps = (
            f"Join {meeting} to watch the host's lesson and see each other's faces. "
            "If the host sends a meeting link later, use Add Link or Change Link here; "
            "you do not need to leave the WebJam room. "
            "Listen to that shared copy; leave your own YouTube player closed. "
            "Ask the host to pause or resume; the host controls the browser. Your meeting speaker volume changes what you hear; "
            "its microphone mute controls your voice. Use headphones."
        )
    if profile == "music":
        steps += (
            " Copy Link shares the meeting, not a WebJam room invitation. Guests can open it "
            "in their browser or meeting app; video practice does not require a WebJam room."
            " Take turns for video practice. If connected to Jamulus, stop your instrument "
            "at its send control. WebJam mix mute changes only what you hear."
        )
    return steps
