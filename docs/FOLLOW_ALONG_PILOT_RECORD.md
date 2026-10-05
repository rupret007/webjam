# Two-person pilot record

Copy this file for each attempt. Every physical result starts **NOT RUN**.
Replace a status with **PASS** or **FAIL** only after observing it, and describe
the evidence. Automated or simulated checks do not establish hearing, cameras,
sharing or feel. Current preparation: participants are not available yet.

## Setup

- Date/time and people/roles (use initials): NOT RUN
- Draft PR, full commit and package SHA-256 from `SOURCE-AND-HASHES.json`: NOT RUN
- Host: computer, OS, display resolution/text scaling: NOT RUN
- Guest: computer, OS, display resolution/text scaling: NOT RUN
- Webex app/browser and versions on both computers: NOT RUN
- Network: same Wi-Fi/LAN or separate homes; room type/reachability: NOT RUN
- Headphones, microphones, instruments, interfaces and routing on each side: NOT RUN
- Lesson and sharing choice: window/computer sound or tab/tab audio: NOT RUN
- Permission for any captured evidence (recording is optional): NOT RUN

## First attempt without coaching

Try Host/Join before reading the click guide. Keep assisted attempts separate.

| Observation | Status | Time / evidence / assistance needed |
| --- | --- | --- |
| Open the packaged app and reach the launch screen | NOT RUN | |
| Host finds Art / Paint along and starts a room | NOT RUN | |
| Guest finds Join and connects to the intended room | NOT RUN | |
| Both reach the same meeting and first shared lesson | NOT RUN | Start time: ; elapsed: |
| Labels, controls and next steps are understandable | NOT RUN | First confusing click: |
| Small window, larger text and keyboard navigation remain usable | NOT RUN | Sizes and text scaling: |

## Paint Along

| Observation | Status | Evidence / failure / help needed |
| --- | --- | --- |
| Host opens the real YouTube lesson deliberately | NOT RUN | |
| Both hear the shared lesson and each other's voices | NOT RUN | Ask both people separately: |
| Both see faces while following the shared lesson | NOT RUN | |
| One audible lesson; no duplicate player or echo | NOT RUN | |
| Physical painting is comfortable alongside lesson and faces | NOT RUN | |
| Painting in another app, screen sharing and return to lesson | NOT RUN | |
| Pause request reaches host; actual browser pauses and resumes | NOT RUN | Request and playback are separate observations: |
| Notes → Conversation retains the chosen lesson | NOT RUN | |
| Meeting added after room invite reaches guest via Copy Link / Add Link | NOT RUN | Room stays connected: |
| Changed meeting link uses Change Link without leaving room | NOT RUN | |
| Late guest receives usable setup and hears one shared lesson | NOT RUN | |
| Three-person session (host + 2 guests) | NOT RUN | Automated: `test_follow_along_multi_participant.py` (offscreen LAN) |
| Late guest joins after host chose lesson | NOT RUN | Automated: `test_late_guest_gets_current_lesson_without_host_rechoosing` |
| Guest drop/rejoin mid-lesson | NOT RUN | Automated: `test_follow_along_reconnect.py` |
| Second session same day (End then Start, same app) | NOT RUN | Automated: `test_follow_along_repeated_session.py` (host path) |
| App restart mid-lesson | NOT RUN | |
| Connection loss/reconnect preserves work and gives useful recovery | NOT RUN | |
| Rejected invitation can be replaced through Paste new invite | NOT RUN | |
| Failed embedding can use Open in browser deliberately | NOT RUN | |
| Unavailable video gives usable recovery with another lesson | NOT RUN | |
| Remember lesson persists after restart | NOT RUN | |
| Continued host: Start Session → Library → Use saved lesson | NOT RUN | Same workspace and saved reference: |
| Continued guest: Start Session → Join → Library → Use saved lesson | NOT RUN | Save a guest reference first via Art project → Add link…; same workspace: |
| Saved-lesson reuse opens setup without automatic media | NOT RUN | |
| End/Leave and app close clean up WebJam; external sharing is ended deliberately | NOT RUN | |

## Music

| Observation | Status | Evidence / failure / help needed |
| --- | --- | --- |
| Play along → video practice is easy to find | NOT RUN | |
| Practice works through one Webex presentation without Jamulus setup | NOT RUN | |
| After failed audio setup, meeting link still opens in browser/meeting app | NOT RUN | |
| Both understand turn-taking and hear the shared song/voices | NOT RUN | |
| Listening mute and outgoing instrument send are understood and verified | NOT RUN | |
| Supported ensemble route has one backing source and both instruments | NOT RUN | |
| Webex audio is disconnected during ensemble; cameras remain useful | NOT RUN | |
| Each musician verifies their actual Jamulus return and level controls | NOT RUN | |
| Shared Track pause/seek/reconnect and cleanup work | NOT RUN | |
| Sustained routing and physical latency measurement | NOT RUN | Method, duration and measured result: |
| Jeff's assessment of timing/feel | NOT RUN | |

## Findings and follow-up

For each problem record: severity (prevents participation / lost work / audio
confusion / repeated setup), role, exact steps, expected and actual result,
reproduction rate, and consented evidence location. Remove private room tokens,
meeting links, credentials and personal notes from public reports.

- Problems: NOT RUN
- Assisted retry results (keep the original failed attempt): NOT RUN
- Checks omitted and why: participants unavailable until scheduled
- Next highest-value repair based on observations: NOT RUN

Keep the PR draft. Jeff owns Latest, feel, tags, merges and releases.
