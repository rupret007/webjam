# WebJam: prove two creative sessions

Evidence baseline: 2026-09-08, America/Chicago. This goal is **INCOMPLETE**.
Draft readiness, green CI, and independent review are distinct from two-person
proof. Karen's absence holds review and merging; independent implementation can
continue in OPEN DRAFT slices. Nothing here authorizes deployment or live meetings.

## Target journeys and evidence status

1. Two people in different homes join one Art room, see and hear each other,
   and follow a Bob Ross mountain-painting lesson on YouTube. Either can ask to
   pause and resume; ownership and listening controls remain understandable.
2. Two musicians join, hear each other, and rehearse with or without Shared
   Track. Routing, independent listening levels, timing, and recovery are proven.

Use four separate statuses for every acceptance item: **Code**, **Automated**,
**Physical**, and **Review**. `PRESENT` describes implemented code, `PASS` needs
named evidence at a full tip SHA, and `NOT RUN` is not a failure or a pass.

| Acceptance item | Code / remaining gap | Automated evidence | Physical | Review |
| --- | --- | --- | --- | --- |
| One complete invitation | Implemented in this slice: full Art/Music paste retains a validated, temporary conversation link through joining. Personal settings stay separate; no auto-open. | New ingress, bootstrap, actual controller/handoff, modal and lifecycle regression tests added; final full-suite/tip evidence belongs in the draft. | NOT RUN | PENDING |
| Different-home Art joining | Ordinary Art room peer endpoint is private/LAN; easy Internet reachability is unresolved. | Local transport tests cannot prove different-home reachability. | NOT RUN | PENDING architecture decision |
| Any-artist room | Make together supports own-space work; #96 improves its first action. | #96 handoff records local/hosted proof; verify current PR tip before relying on it. | NOT RUN | PENDING Karen |
| Local Paint along | PRESENT: matching local files, silent playback, host-only transport, guest-never-seek. This is not the YouTube journey. | File identity, host/guest, seek, loading, stale-state, and navigation suites exist. | NOT RUN | New work PENDING |
| YouTube lesson + faces | External Conversation handoff PRESENT; concrete lesson/audio/guest pause guidance missing. | Handoff/layout tests pass; they do not exercise a live provider. | NOT RUN | PENDING |
| Guest pause/resume | No peer pause-request route. External-source host must operate the browser; guest request path needs explicit guidance. | Existing tests preserve guest transport refusal. | NOT RUN | PENDING |
| Art listening controls | Local video is muted; meeting owns microphones and speakers. Independent guest narration/voice mixing is unproven. | No live audio proof. | NOT RUN | PENDING |
| Music reference track | PRESENT: capability-gated macOS backend and separate track participant; supported source inspection and transport. | Reference-track backend, route, integration, and transport suites exist; record current-tip results. | NOT RUN | PENDING |
| Music routing/latency/cleanup | Physical isolation, two-endpoint audibility, device-switch safety, sustained timing and teardown remain open. | Simulated state/route tests are supporting evidence only. | NOT RUN | PENDING |

On baseline `41a26652387c855059402ae8986c4b0f91a0531f`, 168 tests passed across
`test_art_conversation_next_action.py`, `test_art_conversation_layout.py`,
`test_art_meeting_coexistence.py`, and `test_art_notes_communication.py`.
This is limited automated evidence, not the full-suite or current-draft verdict.

## Authoritative implementation and provider sources

- [Invitation ingress](../../webjam_qt/invitation_ingress.py) and
  [invitation copy](../../core/meeting_companion.py): preserve validated room and
  optional meeting context through the same user-selected invitation.
- [Session peer protocol](../../core/session_transfer.py): private peer service;
  current guest POST routes do not include a Paint along pause request.
- [Reference video](../../core/reference_video.py): identical local-file proof,
  approximately 0.75-second polling tolerance, five-second stale-state guard.
- [Video adapter](../../webjam_qt/widgets/reference_video_player.py): muted before
  first playback. [Conversation card](../../webjam_qt/widgets/webex_embed.py):
  opening a link is not proof of meeting admission, camera, or microphone state.
- [Music backend](../../services/reference_track_backend.py): current production
  Shared Track host backend is macOS-only. Live CoreAudio route proof requires
  macOS 14.2+, official BlackHole 16ch/64ch at 48 kHz, and primary/backing route
  isolation. BlackHole 2ch and WebJam Bridge are not substitutes. Other host
  platforms return unavailable; four desktop builds do not prove four audio backends.
- [Historical macOS physical pilot](webjam-reference-track-macos-pilot.md): retain
  its route, source, failure, 25-cycle and 60-minute checks. Its v0.22.4 package
  instructions are historical; this goal records the exact current candidate
  build/hash instead of requiring an old published release.
- [Webex content sharing](https://help.webex.com/article/i62jfl): video sharing and
  computer sound are supported; macOS may need an administrator-installed audio
  extension. Browser and native controls differ. Consult the applicable tab.
- [Webex shared-content/video layout](https://help.webex.com/en-us/article/4q9lsy):
  shared content can remain beside participant video; floating panels can move
  and resize. Actual small-laptop usability still needs observation.
- [Webex audio settings](https://help.webex.com/en-us/article/n139bv9): microphone
  and speaker levels belong to Webex. This does not establish independent
  guest-side narration-versus-voice faders or control from WebJam.

## Work order and architecture decision

1. Preserve the optional meeting in a full pasted invitation. Validate it,
   reject ambiguous/malformed context safely, avoid accidental persistence or
   launch, and bind application to the accepted current room attempt. Test
   cancellation, retry, profile changes, and saved unrelated meeting state.
2. Resolve remote joining: **what approved private reachability mechanism will
   connect two home networks without manual guest addressing, short codes,
   public discovery, or a public rendezvous service?** Inspect existing secure
   transport support before proposing new infrastructure. A private overlay or
   explicitly provisioned endpoint is a candidate to assess, not an authorized
   deployment. Document setup burden, authentication, lifecycle, costs, and OS
   coverage for Jeff's decision. Do not expose the LAN HTTP service publicly or
   relabel LAN success as remote success.
3. Make the YouTube path obvious through existing Conversation. Keep the Art
   door Make together + Paint along → Host/Join and the squirrel mark. Guide
   host browser sharing with sound, guest asking to pause, and returning to work.
   Any later local-file pause request needs authenticated host validation,
   current-room/source binding, bounded retries, and preserved guest-never-seek.
4. Fix concrete Music rehearsal failures against the route and timing scripts
   below; preserve fail-closed checks. Prepare independent drafts where possible.

Each slice records observed failure, before/after, acceptance tests, dependencies,
full tip SHA, PRE_KAREN self-QA, local results and hosted desktop CI. Use coord
#3 BEFORE/AFTER with a current four-hour codex lease before push; release at
handoff. A stacked draft names its prerequisite. No merge, sign, release, Pages,
spend, live Cisco, parked #37/#49 changes, or automatic media capture.

## Two-person script: invitation and return

Run only after Jeff explicitly schedules the human test and approves its network
setup. Use tester aliases A/B, synthetic room names, and the exact candidate hash.

1. A selects Art → Paint along → Host, saves a meeting link, and copies the
   complete invitation. B pastes that one message. Record actions and elapsed
   time until authenticated room connection; verify activity and meeting are
   carried without requesting another message. Do not record the invitation.
2. Repeat on separate home networks. If the endpoint cannot be reached, record
   FAIL and the safe next action; a separately joined meeting is not room proof.
3. Exercise an installed-app guest and record the missing-app instructions
   separately. Try malformed, expired/replaced and unsupported-version invites;
   require clear recovery, preserved unsent work, and no launch on rejected input.
4. B cancels once, retries, leaves, and returns. A ends the room during another
   join. Verify no stale joining success, stale meeting adoption, or auto-play.

## Two-person script: Bob Ross lesson

1. With both room and meeting connections separately confirmed, A opens a
   chosen Bob Ross mountain-painting YouTube lesson in the browser. B watches
   the meeting share rather than starting another audible browser copy.
2. A shares the intended browser window/tab, enables the provider's computer
   sound option, and checks the preview. Both enable cameras/mics explicitly and
   use headphones. Confirm lesson narration and both voices by listening.
3. B asks to pause, A pauses in the browser, both confirm the picture stopped;
   B asks to resume, A resumes. Repeat during narration and after buffering.
   Record confusion or missed requests. Do not claim WebJam controls the browser.
4. Each changes their own speaker level and microphone mute in the meeting;
   confirm effects with the other tester. A adjusts lesson source volume and
   checks its shared effect. Record whether B can separately balance narration
   and voices; keep that item open if unavailable.
5. On an ordinary laptop, keep faces and lesson visible; resize, visit Notes,
   return, and reconnect after a brief interruption. Verify no lost notes,
   unexpected playback, duplicate sound, or false connected/synchronized state.

## Two-person script: Music and timing

1. Use a supported macOS host, verified BlackHole route and wired headphones.
   Keep conferencing audio disconnected for the baseline. Confirm ordinary
   two-way instrument sound before loading a rights-cleared reference file.
2. Load, inspect, and Recheck Route without playback. Play with count-in;
   confirm exactly one WebJam Track on each mixer and no direct-monitor copy.
   Independently adjust/mute track and collaborator levels on each endpoint.
3. Pause, restart, seek while paused, loop, stop/remove track, then continue
   live playing. Test track failure, device switch, network loss and explicit
   recovery. End/quit and inspect owned resource cleanup without collecting secrets.
4. Measure a favorable network and two-home setup with a known click signal.
   **Recording is separately opt-in**: obtain both testers' permission for a
   synthetic-signal-only loopback capture; otherwise mark numeric timing NOT RUN.
5. Capture click launch on channel 1 and its remote loopback return on channel 2
   of the same multichannel recording device, using one ADC sample clock. Return
   audio once through an isolated remote route with headphones; no speaker/mic
   feedback loop and no routing returned audio into another network send.
   First measure an equivalent local cable/interface loopback calibration.
6. At known sample rate Fs, compute each matched click's round-trip delay as
   `1000 * (return_sample - launch_sample) / Fs` ms. Use at least 100 distinct
   clicks; retain raw round-trip and separately label the calibration estimate.
   Report median, p95, range, missing/duplicate clicks, and change over time.
   Never divide by two and call it measured one-way latency. Remote device
   latency remains included; human playing is a separate subjective observation.
   This measures round-trip transport, not alignment between reference-track
   mixes on different computers. Record track timing within each received mix
   separately; unsynchronized recordings cannot prove cross-site phase alignment.
7. Repeat 25 transport cycles and a 60-minute rehearsal, checking periodic
   timing samples, drift, dropouts, audibility, device stability and cleanup.
   Agree a practical usability target before the trial; record its rationale.
   Until that target and evidence exist, latency acceptance remains OPEN.

## Privacy-safe evidence record

```text
Candidate: version / full commit / package name + SHA-256
When: start/end America/Chicago; testers: A/B; network: LAN or separate homes
Setup: OS/architecture, interface, wired headphones, provider/client versions
Music only: sample rate, official route name, calibration wiring description
Item: expected behavior / PASS | FAIL | NOT RUN / actual observation
Timing: click count, median/p95/range ms, missing clicks, drift, capture clock
Capture: NOT AUTHORIZED | explicitly authorized synthetic-only; retention choice
Evidence: sanitized local artifact reference; no invite, token, URL, private path
Code: PRESENT | IN PROGRESS | MISSING; Automated: result + suite + exact tip
Physical: PASS | FAIL | NOT RUN; Review: PENDING | PASS + reviewed exact tip
Next action / unresolved dependency:
```

Do not attach conversations, performances, credentials, public IPs, or full
diagnostic dumps by default. Keep goal status incomplete while required physical
acceptance or independent review remains missing; a draft is an intermediate step.
