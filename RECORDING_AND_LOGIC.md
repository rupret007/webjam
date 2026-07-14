# Recording and Logic Pro workflow

WebJam records a rehearsal as a **take** on one shared project timeline.
JamulusServer supplies a post-network track for each connected musician. The
host, and a guest connected through an active v2 private invite, can explicitly
opt in to keeping interface inputs 1 and 2 as local isolated originals.

**Test status:** Current source includes the recording-storage guard,
recording-provenance journal, durable local-capture checkpoints, and
recovery-only take publication. Automated checks do not replace the physical
recording, interruption-recovery, two-Mac, or Logic Pro import checks; those
remain **NOT RUN** until the worksheet records them.

## Know the sources

| Source | What it proves | Where it lives |
| --- | --- | --- |
| Jamulus server track | What that participant delivered through the network/server recorder | Host take |
| Local isolated input 1/2 | What the selected two-channel PortAudio/Core Audio stream captured on that Mac | Original stays on that Mac; a verified copy can attach to the host take |
| Server reference | Offline unity mix of exported Jamulus server tracks | Logic export |
| Studio reference | Offline rough mix using Studio gain/pan/mute/solo state | Logic export |

The local capture and WebJam input meter use a separate PortAudio stream from
Jamulus. They do not alter the live Jamulus mix, but they also cannot prove
Jamulus selected the same hardware. Confirm the live route with both
musicians' ears and record the choices in
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).

WebJam does not deliberately record Webex or system output. A selected virtual
or loopback device can still contain those sources, so the physical test must
prove the actual interface route.

## Choose local originals

On the host and each active-v2 guest that should retain interface originals,
open **More → Multitrack Studio → Recording Setup** and:

1. Choose the wired output Studio should use for review.
2. Enable **Keep interface inputs 1 and 2 as isolated local originals**.
3. Choose a shareable two-channel input that supports 48 kHz.
4. If the recording drive needs changing, use **Choose Folder** on the same
   screen and save Recording Setup before the host starts the session.

This is explicit per-Mac opt-in. WebJam writes two separate mono PCM24/48-kHz
files. If only one interface input carries a source, the other file may be
silent; label that expectation instead of calling it another musician.

Guest originals use WebJam's authenticated same-LAN transfer plane after a v2
invite. It is plain HTTP on private RFC1918 IPv4, not TLS or an Internet/VPN/
IPv6 service, and it has no upload quota or rate limiting. Use it only with a
trusted bandmate on a trusted LAN. The complete link is a reusable
session-scoped bearer credential, not a one-use token; anyone holding it on
that LAN can enroll until the host peer restarts. The guest's original is never
moved or deleted. A normal interrupted delivery resumes from the verified byte
offset, and the host publishes an attached copy only after size, SHA-256, and
PCM facts agree. If WebJam later recovers a guest's interrupted local capture,
it preserves that media on the guest Mac for manual review; recovery does not
automatically upload or reconcile it with the host take.
If the host warns **Automatic Local Originals are off**, the v1 fallback still
joins/plays and receives a host-side server track, but has no WebJam-orchestrated
guest local-original capture or delivery.

## Record and verify a take

1. Join the session and confirm the actual musicians appear once.
2. Current source runs a pre-arm writable-folder/free-space check for
   the actual band: an unsafe result starts nothing, while a low-storage result
   is a warning to make room before a long rehearsal. If a running session is
   unsafe, end it before choosing another Takes folder and restarting.
3. Wait until recording is confirmed, play, then stop from the host.
4. Keep both apps open while server files finalize and any guest originals
   transfer.
5. Open the take in Studio and read its status before exporting.

Before the host ends, press **Stop Rec** and wait for **Take saved**; End Session
is blocked while a take is recording or validating. **Leave Jam** finalizes any
active opted-in guest original, persists its resumable queue, and attempts one
final upload. An unavailable host leaves that media and queue on the guest Mac.

The schema-v2 `webjam-take.json` records stable opaque take/participant/track/
segment IDs, source type and quality, project placement, media rate/channels/
format, hashes, device facts, gap intervals, and alignment evidence. Optional
session evidence includes **WebJam-observed UTC timestamps recorded after
server confirmation**, the host identity and protocol label, and a bounded,
redacted lifecycle/recovery timeline. Those timestamps describe when WebJam
observed recorder state; they are not a claim about the server clock. That
session-evidence portion
intentionally excludes invitation links, network addresses, credentials, and
raw device identifiers. A reconnect
or dropped local block does not pull later audio earlier to hide time: missing
frames stay on the timeline as a disclosed gap/silence interval.

While a take is in progress, WebJam atomically checkpoints that same bounded
session evidence in a private, crash-safe journal below the selected **Takes**
folder. Each local-input writer also periodically flushes and fsyncs both WAV
stems before advancing an opaque-ID recovery checkpoint. The checkpoint records
only the durable frame boundary; it never claims that later buffered frames
survived a crash. Neither checkpoint is a completed-take claim: a malformed,
unfinished, or interrupted checkpoint is treated as needing attention and is
retired only after a final manifest is published.

If a local writer cannot finish normally, WebJam preserves visible recovered
media and recovery metadata. On a host recovery scan, readable local audio is
published as a recovery-only project with **NEEDS ATTENTION** status, any known
gaps and durable boundary, and its opaque take/session IDs. It is not a
completed multitrack take or a timing-ready Logic export. Missing, receiving,
partial, recovered, damaged, or failed-transfer media remains visible in
project truth; it is never silently dropped while the take is called complete.

## Review in Studio

Studio is a focused, non-destructive review workspace. It borrows the useful
shape of a DAW—one shared timeline, compact track headers, a selected-track
inspector, and a transport—without claiming to be a music editor. It supports:

- full-take composite waveform lanes on one shared **elapsed-time-only** ruler;
- multi-segment and mixed-rate playback on the project clock;
- play, pause, stop, scrub, and seek;
- a selected-track inspector with source, media status, timeline placement,
  alignment evidence, recorded-gap count, and Logic-inclusion status;
- per-track observed input/playback meter, gain, pan, mute, and multi-solo;
- a selectable wired playback output;
- explicit missing/partial/damaged/transfer findings;
- non-destructive automatic offset/drift evidence and separate manual nudge.

The ruler deliberately shows seconds only. WebJam does not infer tempo,
bars, beats, automation, plug-ins, or audio edits from a rehearsal; clicking
or dragging the ruler only seeks review playback. Known recorded gaps stay
visible on the shared timeline and in the selected-track inspector.

For a schema-v2 take, Studio saves review choices in
`.webjam-studio-state.json` beside the take. That small sidecar holds only
gain, pan, mute, solo, and Logic-export inclusion, keyed by the take's stable
opaque `track_id` values and bound to its `session_id` and `take_id`. It is
atomically replaced, so a slider change never rewrites source WAVs or
`webjam-take.json`. A malformed or wrong-take sidecar is ignored rather than
applied; Studio shows safe default review choices. New or reordered tracks get
defaults instead of inheriting another musician's settings.

Close/reopen the take and repeat a seek and playback check before accepting it.

## Export for Logic Pro

Select a trustworthy take and press **Export for Logic**. In schema-v2 Studio,
the per-track **Logic export** choices are saved as non-destructive review
state and control the next handoff until changed; the recorded take remains
unchanged. WebJam resolves that selection and the Studio rough-mix state by
durable `track_id`, never by the temporary order of visible or exported rows.
That keeps a selected subset or a reordered project from borrowing another
musician's gain, pan, mute, or solo setting. WebJam publishes a new `Logic
Export`, `Logic Export 2`, and so on only after every required output succeeds.
A schema-v2 package contains:

- numbered, musician/source-named **PCM24 WAV stems** rendered from the same
  project origin and to the same project length;
- `WebJam Server Reference.wav`, an offline unity mix of the exported
  post-network Jamulus server tracks;
- `WebJam Studio Reference.wav`, a rough mix reflecting Studio controls;
- `MARKERS.csv`;
- `ALIGNMENT REPORT.md`;
- `RECORDING REPORT.md`;
- `AUDIO ANALYSIS.json`, produced by independently reopening every exported
  WAV and measuring its rate, channels, frames, duration, RMS/peak, and clips;
- `webjam-project-source.json`, preserving the source project evidence;
- `webjam-logic-export.json`, including project rate, tempo, time signature,
  selected source identity, transform, and output facts; it carries nonempty
  bounded/redacted session evidence when the source take has it;
- `CHECKSUMS.sha256`;
- `IMPORT INTO LOGIC PRO.md`.

Original source hashes are checked before rendering. Logic exports never
rewrite the original recorder WAVs. Missing, changed, damaged, or incomplete
selected media blocks the atomic export instead of producing a false
Logic-ready folder. An explicitly silent segment in a selected performance
track also pauses export until you review it or intentionally deselect that
track. A selected local original with no verified timeline alignment (including
an unaligned or unverified guest original) pauses a timing-ready export: keep
the Jamulus server track for that take, or align and verify the local original
before exporting. Studio shows these as fixed, actionable messages and never
exposes local paths or worker diagnostics in the musician-facing error.

Mixed-rate/drift conversion uses the disclosed deterministic linear affine
method `deterministic-linear-affine-v1`. It keeps the same project clock and is
repeatable, but WebJam does not claim it is a sample-perfect or mastering-grade
resampler.

## Import into Logic Pro

1. Open `IMPORT INTO LOGIC PRO.md` and create an empty Logic project with the
   named sample rate, tempo, and time signature.
2. Verify `CHECKSUMS.sha256` before moving the package.
3. Select all numbered stem WAVs and drag them together into the empty Tracks
   area at `0:00`, one file per new audio track.
4. Do not import either WebJam reference WAV as another performance stem. Use
   them only for comparison.
5. Recreate named markers from `MARKERS.csv` if needed.
6. Play from the beginning and through every reconnect/gap boundary. Confirm
   track names, musician/input identity, duration, audibility, and alignment.
7. Keep both JSON manifests, both Markdown reports, the analysis, and checksum
   file with the Logic project.

WebJam does not generate or automate Logic's proprietary `.logicx` format.
Physical Logic Pro import remains **NOT RUN** until the two-Mac worksheet
records it.

## Acceptance gate

The recording/Logic path passes only when:

- every expected server/local source is present or truthfully disclosed;
- the actual hardware routes and two-way audibility are proven on two Macs;
- an outage leaves continued local capture, explicit timeline truth, and a
  resumable verified guest copy;
- Studio playback/seek/pan/mute/multi-solo/output/reopen checks pass;
- the exported numbered stems have matching project length and valid checksums;
- Logic Pro actually imports the exact package at `0:00` and the musicians
  confirm identity and alignment;
- End/Leave leaves no WebJam-owned recorder, transfer, Jamulus, server, or
  `caffeinate` process.

Until those physical steps are performed, the result remains **NOT RUN** even
when automated project, Studio, export, and real-Jamulus/JACK tests pass.
