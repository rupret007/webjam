# WebJam v0.18 unified-guidance pilot checklist

Status values: **PASS**, **FAIL**, or **NOT RUN**. Do not infer a physical
result from automation. Record the exact commit/package identity, Macs,
interfaces, Jamulus version, OS versions, date, operator, and evidence location
before changing a status.

## Candidate identity

| Field | Value |
| --- | --- |
| Source commit | |
| Package/build ID | |
| Host OS / Mac / interface | |
| Guest OS / Mac / interface | |
| Network/topology | |
| Jamulus version | 3.12.2 expected |
| Date / musicians | |
| Evidence folder | |

## Guidance walkthrough

At every row, compare the visible Session HUD, passive stage, Session Canvas
NOW card, recorder/Studio text when relevant, and assistive description. Record
the exact title, one next action, why text, and output states. A difference is a
failure even when the underlying operation works.

| Gate | Status | Evidence / notes |
| --- | --- | --- |
| Idle Host and Join each present one clear action | **NOT RUN** | |
| Native Jamulus setup identifies Jamulus as device owner | **NOT RUN** | |
| Band Check waiting and human confirmation remain distinct | **NOT RUN** | |
| Host invitation appears only after verified share readiness | **NOT RUN** | |
| Guest joining does not claim connection from process launch | **NOT RUN** | |
| Authenticated roster becomes connected/live without claiming audibility | **NOT RUN** | |
| Record request waits for recorder confirmation | **NOT RUN** | |
| Confirmed recording and stop/finalization are distinct | **NOT RUN** | |
| Take validation ends in ready or actionable attention | **NOT RUN** | |
| Guest Local Original capture/transfer/receipt is truthful | **NOT RUN** | |
| Studio identifies take, validation, non-destructive edits, and save state | **NOT RUN** | |
| Named Verse/Chorus move is clear before, during, and after the edit | **NOT RUN** | |
| Export in-progress, verified success, and failure are distinct | **NOT RUN** | |
| Selecting/editing another take clears the earlier export result | **NOT RUN** | |
| End/Leave shows finalization before completed cleanup | **NOT RUN** | |

## Recovery and privacy

| Gate | Status | Evidence / notes |
| --- | --- | --- |
| Jamulus interruption shows automatic reconnect without false success | **NOT RUN** | |
| Safe retry appears only after the prior attempt is stopped | **NOT RUN** | |
| Consumed invitation requests a fresh link and cannot replay | **NOT RUN** | |
| Wi-Fi/address change asks the host to copy a new invitation | **NOT RUN** | |
| Microphone denial gives fixed, actionable, path-free wording | **NOT RUN** | |
| Interface disconnect/reconnect keeps recording/take truth conservative | **NOT RUN** | |
| Sleep/wake rejects stale callbacks from the earlier attempt | **NOT RUN** | |
| Failed Studio save keeps the dirty take open and retries safely | **NOT RUN** | |
| Failed export publishes no partial package as success | **NOT RUN** | |
| Support preview contains finite guidance but no authored/private content | **NOT RUN** | |
| Opt-in Companion data contains anonymous slots and no private identifiers | **NOT RUN** | |

Use adversarial local text while checking privacy: musician names, a home path,
an invitation-shaped string, a server address, a device name, a token-like
value, and notes that say “recording complete.” None may appear in diagnostics
or Companion responses, and the note must not change operational output state.

## Layout, keyboard, and accessibility

Repeat the meaningful states at 760×600 and at the normal desktop size.

| Gate | Status | Evidence / notes |
| --- | --- | --- |
| One dominant action; no duplicate HUD/Studio action | **NOT RUN** | |
| Session Canvas notes and chat remain usable at 760×600 | **NOT RUN** | |
| Studio Arrange, mixer, hint, and controls do not overlap at 760×600 | **NOT RUN** | |
| Keyboard reaches HUD, notes/chat, take list, Arrange, and export in order | **NOT RUN** | |
| Focus moves to the first meaningful action without trapping the user | **NOT RUN** | |
| Screen reader announces meaningful state changes once | **NOT RUN** | |
| Playhead/meter/waveform motion causes no repeated announcement | **NOT RUN** | |
| Orange accent and text remain legible over real waveforms | **NOT RUN** | |

Capture screenshots for idle, setup, live, recording, take attention, Studio
dirty, export success/failure, and one recovery state. Capture a short screen-
reader log or video for the no-churn check.

## Musical and package gates

| Gate | Status | Evidence / notes |
| --- | --- | --- |
| Two musicians hear each other and explicitly confirm quality | **NOT RUN** | |
| Shared take contains synchronized separate musician tracks | **NOT RUN** | |
| Real Local Originals survive disconnect and reach the host | **NOT RUN** | |
| Long recording/recovery preserves source hashes | **NOT RUN** | |
| Arrange/comp/cycle playback is correct through real outputs | **NOT RUN** | |
| Export imports with correct alignment/markers in an external editor | **NOT RUN** | |
| Signed clean install passes quarantine/SmartScreen/trust checks | **NOT RUN** | |
| macOS notarization and platform entitlements are verified | **NOT RUN** | |

## Exit rule

An explicitly labeled unsigned private test candidate may be published while a
physical or credentialed row is **NOT RUN**, provided the release and filenames
state that boundary and no result is inferred. Do not promote a candidate as a
production-trusted release while any required row is **NOT RUN** or **FAIL**.
Automated evidence must never be rewritten as human audibility, hardware,
import, signing, or notarization proof.
