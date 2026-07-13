# Recording and Logic Pro workflow

WebJam records a rehearsal as a **take** on one shared project timeline.
JamulusServer supplies a post-network track for each connected musician. Any
Mac that explicitly opts in can also keep its interface inputs 1 and 2 as local
isolated originals.

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

On each Mac that should retain interface originals, open **More → Multitrack
Studio → Recording Setup** and:

1. Choose the wired output Studio should use for review.
2. Enable **Keep interface inputs 1 and 2 as isolated local originals**.
3. Choose a shareable two-channel input that supports 48 kHz.
4. Save Recording Setup before the host starts the take.

This is explicit per-Mac opt-in. WebJam writes two separate mono PCM24/48-kHz
files. If only one interface input carries a source, the other file may be
silent; label that expectation instead of calling it another musician.

Guest originals use WebJam's authenticated same-LAN transfer plane after a v2
invite. It is plain HTTP on private RFC1918 IPv4, not TLS or an Internet/VPN/
IPv6 service. The guest's original is never moved or deleted. Interrupted
delivery resumes from the verified byte offset, and the host publishes an
attached copy only after size, SHA-256, and PCM facts agree.

## Record and verify a take

1. Join the session and confirm the actual musicians appear once.
2. The host presses **Record** or **Record Take**.
3. Wait until recording is confirmed, play, then stop from the host.
4. Keep both apps open while server files finalize and any guest originals
   transfer.
5. Open the take in Studio and read its status before exporting.

The schema-v2 `webjam-take.json` records stable take/participant/track/segment
IDs, source type and quality, project placement, media rate/channels/format,
hashes, device facts, gap intervals, and alignment evidence. A reconnect or
dropped local block does not pull later audio earlier to hide time: missing
frames stay on the timeline as a disclosed gap/silence interval.

If a local writer cannot finish normally, WebJam preserves visible recovered
media and recovery metadata. Missing, receiving, partial, recovered, damaged,
or failed-transfer media remains visible in project truth. It is not silently
dropped while the take is called complete.

## Review in Studio

Studio is non-destructive. It supports:

- full-take composite waveform lanes and a shared time ruler;
- multi-segment and mixed-rate playback on the project clock;
- play, pause, stop, scrub, and seek;
- per-track gain, pan, mute, and multi-solo;
- a selectable wired playback output;
- explicit missing/partial/damaged/transfer findings;
- non-destructive automatic offset/drift evidence and separate manual nudge.

The original WAVs are not rewritten. Close/reopen the take and repeat a seek
and playback check before accepting it.

## Export for Logic Pro

Select a trustworthy take and press **Export for Logic**. WebJam publishes a
new `Logic Export`, `Logic Export 2`, and so on only after every required output
succeeds. A schema-v2 package contains:

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
  selected source identity, transform, and output facts;
- `CHECKSUMS.sha256`;
- `IMPORT INTO LOGIC PRO.md`.

Original source hashes are checked before rendering. Logic exports never rewrite the original recorder WAVs.
Missing, changed, damaged, or incomplete selected media blocks the atomic export
instead of producing a false Logic-ready folder.

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
Physical Logic Pro import for the v0.10.0 candidate is **NOT RUN** until the
two-Mac worksheet records it.

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
