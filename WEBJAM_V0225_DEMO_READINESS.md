# WebJam v0.22.5 two-musician demo readiness

> **Human evidence worksheet:** automated tests cannot prove audibility,
> latency, feedback isolation, Webex behavior, or hardware recovery. Complete
> this worksheet only against exact published v0.22.5 assets after checksum
> verification. Until then every physical row is **NOT RUN**.

## Evidence identity

| Field | Host | Bandmate |
| --- | --- | --- |
| Date/time and tester |  |  |
| Asset filename |  |  |
| SHA-256 |  |  |
| About version/build ID |  |  |
| OS, architecture, and model |  |  |
| Interface/input/output |  |  |
| Wired headphones | ☐ | ☐ |
| Jamulus buffer/ping/jitter shown |  |  |

Reference files: one real MP3 and one WAV, with filename/path omitted from
shared evidence. Record only duration, sample rate, and SHA-256 when needed.

## Stop conditions

Stop the demo and mark the relevant row **FAIL** for a crash, data loss, secret
or private-path disclosure, wrong external action, feedback that cannot be
stopped immediately, duplicated/direct Reference Track monitoring, a false
Connected/Playing/Muted claim, or an owned process that survives End/Leave.

## Musician-order rehearsal

1. Verify both release checksums, install normally, open About, and record the
   exact version/build IDs. Do not use an adjacent source build.
2. Connect interfaces and wired headphones before Host/Join. If the Mac is
   clearly on built-in microphone plus speakers, verify **Go Back** is the safe
   default and audio stays stopped; then connect the intended route and retry.
3. Host on the Mac package; join from the second endpoint. Complete native
   Jamulus sound setup. Each musician plays a note and explicitly confirms
   clean two-way hearing before proceeding.
4. On the host, open **Reference Track**, load the WAV, and verify load state is
   separate from route readiness. Prove the isolated BlackHole route, then test
   Play, Pause, paused seek, Restart, and Stop. Repeat with the MP3, including
   drag-and-drop. Record any exact bounded error text.
5. Both musicians independently confirm that the Reference Track is heard
   through Jamulus, appears as the dedicated `WebJam Track` participant, has an
   independent mixer level, and is not doubled through direct host monitoring.
6. Play together to the song for at least five minutes. Record a short musical
   judgment from each person: timing feel, perceived latency, dropouts, drift,
   and whether the backing track stayed usable. Do not translate “felt good”
   into a latency-eliminated claim.
7. Open **Webex Controls** and confirm this alone does not join a meeting.
   Verify **Show Webex App** brings the verified native app forward on macOS.
   If using an approved test meeting, join explicitly and keep Webex muted while
   playing; confirm Jamulus remains the music path and no delayed duplicate is
   heard.
8. Record a short take, stop/finalize it, enter Studio, return with **Back to
   Live**, and confirm the session remains truthful. Open Reference Studio
   separately and verify it does not change the live Jamulus session.
9. Unplug/reconnect one approved interface, exercise the documented recovery,
   then End/Leave. Confirm WebJam stops only its owned processes and relaunches
   cleanly without claiming that it closed externally owned Webex.

## Scorecard

| Gate | Result | Evidence / exact observation |
| --- | --- | --- |
| Exact package/checksum/version/build identity | **NOT RUN** |  |
| Clean Host/Join and two-way musician audibility | **NOT RUN** |  |
| Feedback warning is default-safe and non-persistent | **NOT RUN** |  |
| WAV load and all transport controls | **NOT RUN** |  |
| MP3 picker + drag/drop load and playback | **NOT RUN** |  |
| Separate `WebJam Track` participant and independent fader | **NOT RUN** |  |
| No direct-monitor doubling or audible feedback | **NOT RUN** |  |
| Five-minute play-along latency/dropout/drift observation | **NOT RUN** |  |
| Webex Controls / Show / Join separation | **NOT RUN** |  |
| Webex muted while Jamulus music stays uninterrupted | **NOT RUN** |  |
| Record, Studio, Back to Live, and Reference Studio isolation | **NOT RUN** |  |
| Interface recovery and clean End/Leave teardown | **NOT RUN** |  |
| Gatekeeper / SmartScreen / managed-device policy | **NOT RUN** |  |

Allowed results are **PASS**, **FAIL**, or **NOT RUN**. Add the date and exact
asset SHA-256 to any external evidence location; never paste credentials,
meeting links, device UIDs, raw support bundles, or private filesystem paths.

## Verdict

- **READY FOR DEMO** requires every demo-path row through clean teardown to be
  PASS on the exact packages. Platform-trust rows may remain NOT RUN only while
  the release continues to be labeled an unsigned/ad-hoc private test candidate.
- Any stop-condition failure means **NOT READY** until a new versioned package
  contains the fix and the failed physical row is rerun.
