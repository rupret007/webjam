# Worth-Building — Art Notes to talk and share

2026-09-05 CT. Branch `codex/webjam-finish-product-art-next-action`; exact base
`c143185193d2722027a58a621e83d955ce7b977a` (post-#76 squash).
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556911937),
23:32:43 CT through 03:32:43 CT. Read #3 body and latest BEFORE/AFTER first;
Bob's latest AFTER released the earlier Grok lease. Verified merged #76 and
GitHub master SHA, fetched that exact master, preserved the clean #76 branch,
and created this fresh branch in `/Users/jeffstory/Documents/WebJam` only.

**Source/product Worth-Building: PASS.** An artist taking notes has an enabled
“Message artists… (Enter to send)” composer. Its only implementation is Jamulus
chat, which Art rooms do not start. Enter fails and instructs the artist to
“Reconnect to your band,” so the advertised task has no successful Art retry.
A sculptor or talk-only guest needs the existing Conversation controls to talk
and share work while using their own tools.

The baseline uses the real ApplicationController and an authenticated native
Art guest with isolated settings/notes/credentials, synthetic room receipts,
and mocked outgoing message/meeting calls. Two passing defect assertions prove:

- A saved-Music guest adopted into Art sees the enabled composer; Enter calls
  Jamulus, restores the draft on rejection, and displays the band retry text.
- Direct stale Music send/receive dispatch can append its text into the Art
  personal Notes even though Art has no Jamulus chat transport.

Probe: `/private/tmp/test_webjam_post76_chat_before.py`; log:
`/private/tmp/webjam-post76-chat-before.log` (2 passed, 1.82s). An initial pytest
console entry could not import the external probe's tests plugin and collected
nothing; using python -m pytest resolved that harness path. No live sends,
external app launches or media decoding occurred.

Build one communication slice: Art Notes replaces the unsupported composer
with a clear **Talk & share** action revealing the existing Conversation panel.
Explain that notes stay on this computer and talking/sharing happens in the
meeting. Navigation must preserve notes, selection, undo, pending saves and
hidden Music drafts. Opening the panel does not open a meeting or claim the
artist joined it. Current-profile guards must reject delayed outgoing and
incoming Jamulus chat work during Art, including dispatch after a profile
change; non-Art chat keeps its existing accepted-send behavior. Reuse existing
Conversation ownership, current cleanup guards and stage navigation.

Proof: actual host and LAN/native guest routes, borrowed Music preferences and
restoration, stale dispatch, configured/unconfigured Conversation, compact and
normal keyboard/focus, local note preservation, no external launches, and no
private payloads in diagnostics. Focused Art/door/session/invite and chat tests,
full raw pytest, UX smoke, Ruff, compileall, pip check, independent PRE_KAREN
leftover/privacy review and exact-tip hosted SUCCESS with four desktop builds
remain required.

Actual-controller Cocoa QA also exposed two layout failures on this same
communication journey. With full room guidance, Notes can shrink below its
editor minimum at 720×560 and beside the room at 1040×720. After opening
Conversation and returning to Notes, the retained meeting card's fixed
152-pixel height lets its title, guidance and status overlap in the narrow
stage pane. The prior chat-row probe shows the compact Notes shortage is
inherited; the real screenshot confirms the meeting-card issue is reachable
on the new route. Preserve both panels' content/state while adapting spacing
and Art Conversation layout; add actual-controller geometry assertions rather
than relying only on shorter widget fixtures. Root captures are
`/private/tmp/webjam-post76-communication-{notes,conversation}-{720,1040}.png`.

The compact layout decision is explicit: below 900 pixels, selecting Art
Notes gives the draft the full workspace, including after a window resize.
It hides only the internal Conversation card, preserving its identity, link,
status and any externally owned meeting. Talk & share shows those same
controls again. Wider Notes can retain the readable Conversation card beside
the draft. This keeps Notes usable with full room guidance and larger text;
it does not change the existing Back to room action or launch a meeting.

Deferred independently: stale Music Creative Pulse after Art profile adoption;
Suggestion's Notes promise versus its existing Krita image handoff; optional
canvas presentation pressure for own-tools guests. Missing-copy Paint along
remains useful local preparation during reconnect and correctly pauses an aged
playing receipt; that is not a demonstrated transport defect. #74 publication,
#75 dual Open routes and #76 return-to-room are baseline and are not repeated.

Holds: Art Preview, own tools, silent local-file Paint along, Webex first-class
beside the room and never on the Art door. No automatic meeting/canvas launch,
second video stack, short-code/public rendezvous, merge/tag/sign/release/Pages/
Release Trust. Unsigned 0.27.2 Jeff-only. Physical/public/live-provider/installed
owner-package gates NOT RUN. #37/#49 parked; #67 and completed PR branches
untouched; preserve all four stashes. Canonical WebJam only. One OPEN DRAFT for
Karen, then coord AFTER and agent:none / lease cleared.
