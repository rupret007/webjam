# Show WebJam: the 8:45 presenter script

**Jamulus live band → Play along → Paint along → Record along into Logic.**

**Timed physical rehearsal: PENDING.** The schedule below is a target, with a
9:30 presentation stop. Source checks establish the clicks, not elapsed time,
audibility, shared viewing, or installed-build feel. Fill in the rehearsal
receipt after running the complete sequence. Preparation is outside the timer.

## Prepare once, before the audience arrives

- [ ] Select one app/build for the entire show. Record its version and source
  commit or installed package hash in the receipt. Published baseline is
  [v0.28.3](https://github.com/rupret007/webjam/releases/tag/v0.28.3); draft source
  changes are a separate candidate. Finish installation and documented
  platform-trust steps beforehand. No new build publication is part of this run.
- [ ] Use the sole checkout, `/Users/jeffstory/Documents/webjam`. Verify its
  `.venv` and the handoff command below. For a source presentation, launch with
  `./.venv/bin/python webjam_qt_main.py` from that directory. Each later
  “reopen” means the same command after WebJam exits, or the same installed app.
- [ ] Have a second musician ready with a rehearsed private invitation and
  Jamulus interface, channels, headphones, and buffer. Hear a short phrase in
  both directions. A connection label or moving meter alone is insufficient.
  Keep invitation contents out of screenshots and recordings.
- [ ] Prepare a Music project: **File → New Music Project… → Play Along /
  Record**. Choose permitted backing audio, then a new project destination.
  Wait for preparation; **Save**, close, reopen, and check local playback.
  Record the project folder location. During the show, **Open Project…** selects
  that folder, not an individual audio file.
- [ ] Prepare a short permitted local process video. A second desktop follower
  needs its own matching copy; WebJam does not transfer the file. Rehearse Art
  joining and playback if demonstrating a follower. Keep a tested YouTube link
  as an optional alternate; the main script uses the local file.
- [ ] Open Logic beforehand and close unrelated projects. Rehearse importing
  the synthetic pack below as a new project. Keep that prepared import as a
  labeled fallback. Assign and audition an instrument beforehand if MIDI
  playback is part of the presentation; MIDI carries notes, not instrument sounds.
- [ ] Use a comfortable window, approximately 1000×740 or larger, and rehearse
  both app relaunches. In a compact window, **End**, **Leave**, and **Talk** are
  shortened labels for the full session/room and Conversation actions.
- [ ] Optional: make and verify a 60-second earlier-session recording for
  fallback, labeled with its date and build. Do not assume such a recording exists.

Keep [the one-pager](SHOW_ONEPAGER.md) open. Notes appear in live Music; the
standalone Studio hides the live rail and Notes. Live **Studio** reviews completed
takes. The File-menu route above opens the separate local Play along workspace.

## Presenter cues

### 0:00–2:30 — Live band and Notes

**Say:** “Play together, play along, paint along, then take the work into Logic.”

Show the **Art / Music** door. Choose **Music → Host** or **Join** for the
prepared room. A guest pastes the complete invitation. Wait for the authenticated
Jamulus connection and exchange a short musical phrase with the second musician.

**Say:** “Jamulus carries the live band.”

Open **Notes**, show one short local note, then return to **Live**. Choose
**End Session** as host or **Leave Jam** as guest, confirm if asked, and wait for
cleanup. Close WebJam. Notes are private to this computer.

### 2:30–4:00 — Play along

Reopen the same app. Choose **File → New Music Project…** on the launch screen.
Point out **Play Along / Record**, then choose **Open Project…** and select the
prepared project folder. Wait until ready; use **▶** (Play) for 15 seconds, then
**■** (Stop). Choose **Save**, then **File → Close Project**, and close WebJam.

**Say:** “Play along gives me a local backing track and a place to develop an
idea.” This local Studio audio path does not feed Jamulus.

### 4:00–6:20 — Paint along

Reopen the same app. Choose **Art → Paint along → Host** and wait for **Your room
is open**. If the overview remains visible, choose **Open Paint along**. Choose
**Choose process video…**, select the prepared file, wait until ready, then
**Play** and **Pause**. Point out **YouTube link…** as the alternate source in
this workspace; no online load is needed for the main run.

**Say:** “Each artist paints in their own tools while following the host.”

A local-file guest chooses **Open my copy…**; a YouTube guest explicitly chooses
**Open lesson**. Video is silent and timing approximate, not frame-accurate.
An open host room does not establish that anyone else joined or watched.

Choose **Back to room → End Room**, confirm, and wait for cleanup. Close WebJam
so a source-launch Terminal returns to its prompt before the next command.

**Say:** “Conversation hands talking and screen sharing to Webex or the group's
meeting service.” Opening Conversation alone opens no meeting; an external
meeting has its own end controls. This short run describes that handoff rather
than claiming a meeting was joined or shared.

### 6:20–8:20 — Record along: MIDI and stems into Logic

**Say before running:** “This four-second synthetic example shows the handoff
format. It is not the band performance we just heard.”

```bash
cd /Users/jeffstory/Documents/webjam
./.venv/bin/python -m tools.logic_handoff --stub
```

The JSON receipt names the new `folder`, `midi`, `stems`, `tempo`, and `readme`.
Copy its exact folder path into Finder **Go → Go to Folder…**. Each run creates
a separate folder under `~/Music/WebJam/LogicHandoff`.

1. Follow the generated README. Open `session.mid` as a **new Logic project**
   to load its tempo, meter, and markers.
2. Set/check **48 kHz** before importing audio. Add both WAVs together at
   **bar 1 / time zero, as separate tracks**. Keep original audio timing and
   disable automatic tempo matching/Flex stretching.
3. Show the MIDI and audio tracks, **120 BPM**, **4/4**, and **Start / Middle**
   markers. Verify that both audio and MIDI end at **bar 3**. Playback is a
   separate observation; do not claim listening if you only inspect the screen.

**Say:** “Editable Type 1 MIDI and aligned 24-bit stems carry the work into
Logic.” The current exporter is CLI/API only, not Studio **Export Tracks**, and
does not automatically collect the earlier live session or record MIDI.

### 8:20–8:45 — Close

Show [the one-pager](SHOW_ONEPAGER.md).

**Say:** “WebJam conducts the creative room and hands the work to the tools
people trust.” Note any step shown from prepared evidence instead of live.

## If a step needs recovery

Spend at most 20 seconds on one displayed recovery action, then take the
fallback. At **9:00**, stop new setup and show the prepared handoff evidence;
finish by **9:30**. A fallback keeps the presentation moving but does not count
as proof of the failed live step.

| Problem | Next action |
| --- | --- |
| Music connection or sound is not ready | Use the displayed recovery action, then a verified earlier recording if prepared. Say today's live audio was not demonstrated. |
| Invitation may have been consumed | Obtain a fresh invitation and use **Paste New Invite**. Use **Try Again** only when WebJam offers it. |
| Local Studio playback fails | Show the prepared project and controls, name playback as unverified, and move on; avoid device setup during the show. |
| Video stays opening | Use **Cancel opening**, then the prepared local file. If playback still fails, show the workspace and report that limit. |
| An optional YouTube lesson fails | Use **Choose process video…** with the prepared local file. |
| Logic import is slow/unavailable | Show the generated files/README and labeled prepared import. Do not claim a fresh import or playback. |
| Room cleanup remains incomplete | Use the shown **Try End Session / Try Leave Jam / Try End Room / Try Leave Room**. Start no new room over unresolved cleanup; finish with prepared evidence. |

For actual stopped material, supply aligned audio and explicit note data using
the manifest in [Logic handoff phase one](docs/LOGIC_HANDOFF_PHASE1.md), then run
`./.venv/bin/python -m tools.logic_handoff --session /path/to/session.json`.
The exporter does not infer timing, render Studio edits, resample, transcribe
audio, or create a `.logicx` project. No new UI integration is assumed here.

## Rehearsal receipt — fill in actual observations

Leave unperformed items **NOT RUN**. A source check, fallback, or previous run
does not make the selected build's live observation pass. Do not include private
invitations, credentials, or participant contact details in the receipt.

```text
Date / presenter:
App version + exact source SHA OR installed package name + SHA-256:
CLI source SHA (if different):
Machine / OS / window size:
Prepared project folder / process-video file / fallback location (local only):
Start time / finish time / total elapsed:
Under 10 minutes: NOT RUN / YES / NO
Music: both directions heard? NOT RUN / YES / NO; observation:
Notes: visible and readable? NOT RUN / YES / NO; observation:
Play along: local backing heard? NOT RUN / YES / NO; observation:
Paint along: host play/pause observed? NOT RUN / YES / NO; observation:
Art follower (if demonstrated): NOT RUN / observation:
Logic: fresh import or prepared fallback? NOT RUN / observation:
Logic: separate tracks, 48 kHz, 120 BPM, 4/4, markers, bar-3 ends:
Logic listening: NOT RUN / observation:
Cleanup completed between workflows? NOT RUN / YES / NO; observation:
Fallbacks used / elapsed time at each transition / remaining issues:
```

## Source and review boundary

Navigation was checked against master base
`da16749abc9f87cde127e01ea10a7712e5ce7800`, using only
`/Users/jeffstory/Documents/webjam`. No fresh physical rehearsal is claimed.
The docs PR body owns the final tip SHA, complete local gate, exact-tip hosted
required-job results, and Karen leftover/security/UX review handoff. This script
does not certify a different open PR, native companion device feel, or packaging.

Keep the docs PR **OPEN DRAFT**; after its exact tip reaches hosted green, stop
for Bob/Karen. **No new publish/tag/Latest until Bob says FINAL BUILD after all
MATCHED and Bob + Karen QA.** No signing/notarization or Pages; leave #37/#49
parked. No second WebJam checkout, clone, worktree, or Codex task.

Marker: `WEBJAM_SHOW_WAVE_FINAL_BUILD_20260924`.
