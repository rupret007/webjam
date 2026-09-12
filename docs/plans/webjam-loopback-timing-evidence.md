# Measure audio round-trip delay from one capture clock

## Worth building

WebJam can align recorded takes and observe transport RTT, but neither value
establishes the delay through a musician's complete audio path. Two independently
started recordings include their start-time difference. A person playing late
also includes human timing. Neither is a sound basis for diagnosing latency.

This independent slice adds two explicit, offline tools: make a standard
synthetic probe file, then analyze a separately authorized interleaved capture
of its direct and once-returned signals. They do not open audio devices, play,
record, discover rooms, contact a network or change the music engine.

The slice starts from master `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2` and does
not depend on pending #96–#99. It supplies a missing measurement step; the
two-person rehearsal, actual routing and physical timing evidence remain NOT RUN.

## Prepare the standard signal

From the repository's Python environment:

```sh
python -m tools.make_loopback_probe new-probe.wav
```

This creates one new private mono PCM16 WAV at 48 kHz. It contains 100 short
3.1-kHz bursts, each 20 ms long, with a smooth envelope and maximum amplitude
0.2. Onset gaps vary between 1.25 and 1.65 seconds; both ends have 1.2 seconds
of silence. The file is under three minutes. Existing files are refused.
Creation never plays the sound or starts a recorder.

The nonuniform gaps are deliberate: regular repeated clicks can be confused
with the next click after a missing or delayed return. This detector is for
isolated calibration bursts, not arbitrary music, speech or performances.

## Physical capture contract — requires a separate explicit decision

Have both testers agree to a synthetic-only test and document the wiring before
opening a recorder. Use wired headphones and an isolated once-returned path;
start at a low listening level. Do not form a loudspeaker/microphone feedback
loop, route the returning signal back again, or capture conversation by default.

Record two selected channels in **one interleaved hardware capture**:

1. A direct reference tap of the actual launched probe.
2. The once-returned copy of that same probe after the route under test.

Both channels must be sampled by one ADC clock. Do not combine two separate
recordings, shift channels, automatically align a take, edit away gaps, resample
one side or substitute an unproven aggregate-device clock. Use a fresh capture
for each route and leave sufficient quiet lead-in and tail to observe the
maximum declared delay. Record the direct signal too; intended launch times
are not observed reference timestamps.

Start with a local cable/interface calibration, then a favorable music route,
then the separate-home-network route. Keep each capture and its result separate.
Use the existing routing pilot to verify the intended devices, isolation,
headphones, participant identity and independent faders before interpreting
timing. These tools cannot validate the wiring from a WAV file.

## Analyze an explicit capture

```sh
python -m tools.analyze_loopback_timing capture.wav \
  --reference-channel 1 \
  --return-channel 2 \
  --max-delay-ms 1000 \
  --assert-same-clock \
  --output new-timing-report.json
```

Channels are numbered from 1. The assertion records the operator's claim about
the capture contract; the report marks it **not independently verified**.
Choose the maximum expected round-trip delay before analysis. The limit is
1,000 ms; it is a search bound, not a musical acceptance threshold. The quiet
gap between reference bursts must exceed that bound **plus 50 ms**. Bursts must
be 2–50 ms long; the detector also requires at least 200 ms of quiet at both
ends and a complete final search window. The standard probe exceeds these
minimums, including for the maximum 1,000 ms search.

Input limits are PCM16/24/32 WAV, 44.1/48/96 kHz, 2–8 channels, at most 180
seconds, 256 MiB and 512 detected events per selected channel. Floating-point,
compressed, incomplete and unsupported-precision files are refused. Work is
checked against a cooperative 30-second analysis budget; this is not a promise
to interrupt a stalled native decoder.

The extension-size and precision checks distinguish container width from valid
sample bits, following the fields documented by
[Microsoft's WAVEFORMATEXTENSIBLE reference](https://learn.microsoft.com/en-us/windows/win32/api/mmreg/ns-mmreg-waveformatextensible)
and [WAVEFORMATEX reference](https://learn.microsoft.com/en-us/windows/win32/api/mmreg/ns-mmreg-waveformatex).
This first analyzer deliberately supports equal container/valid precision only;
that is its narrower input policy, not a claim that other precision is invalid
audio. A declared format extension must fit within its actual RIFF chunk.

Without `--output`, the tool writes one JSON object to standard output. With
an output path, it creates exactly that new report and refuses existing files,
including the input, hardlinks and symlinks. It never replaces evidence or
creates missing directories. Errors use fixed codes and omit private paths,
filenames, arbitrary arguments and decoder details.

## What the report means

The detector measures the lag between observed reference and return events on
the common sample timeline. A qualified result reports raw round-trip delay
statistics and event counts. At least 20 unambiguous pairs are required; the
standard probe supplies 100. Missing or extra returns and ambiguous matches
remain explicit. Insufficient or unsuitable signals withhold the summary.

RMS analysis bins are approximately one millisecond at the supported sample
rates; the report gives their actual sample-derived resolution. That resolution
is not a promise of physical accuracy. Codec filtering, burst distortion,
buffering, resampling and the capture arrangement affect interpretation.
Conservative shape and spread checks withhold a summary for detected distortion
or overlapping returns. Some overlaps cannot be distinguished from one altered
burst, so the once-returned wiring assertion still matters. A working room can
produce an indeterminate capture; inspect the signal and route rather than
relaxing a threshold to force a number. The report identifies the detector policy.

The result includes the capture digest and bounded audio facts, detector
configuration, counts and provenance status. It excludes filenames, filesystem
paths, WAV metadata, waveform samples, meeting links and device identities.
Keep a separate privacy-safe wiring/build record with the report.

These are **measurements**, not an audibility or playable-latency PASS:

- Preserve the raw audio RTT and local calibration separately. Do not silently
  subtract calibration and call the remainder network delay.
- Do not divide RTT by two to claim one-way delay. The outbound and return
  paths need not be symmetric.
- A changing measured lag is not remote clock-drift certification. It can
  include buffering, scheduling and resampling effects.
- A Shared Track and a player's sound can take different capture/routing paths.
  Measure each intended relationship; one loopback is not proof of alignment
  between all musicians or of correct local monitoring.
- Do not infer dropouts solely from late human playing. Missing detected probe
  events are evidence under this signal/capture contract, not a recording of
  the musician's performance.

## Owner acceptance record

For each separate test, record candidate version/full SHA and package digest,
platform/interface/headphones, the selected path, local versus remote network
conditions, capture digest, start/end time and operator. Keep private addresses,
invitations, secrets and personal recording paths out of shared evidence.

Retain the full report even when it is indeterminate. Document missing events
and observed echo, duplicate monitoring, channel loss or cleanup failure. Those
functional failures must be resolved before interpreting a pleasant latency
number as a useful rehearsal. Compare repeated starts/stops and a sustained
session using the existing physical pilot; a three-minute probe is not the
pilot's 60-minute stability evidence.

Choose practical latency criteria only after comparing the measured route with
the testers' actual ability to rehearse. There is no universal zero-latency
claim or invented threshold in this tool. Physical audibility, timing, recovery
and independent review remain separate acceptance rows.

## PRE_KAREN self-QA

- **Leftover honesty:** this adds explicit-file measurement, not an automatic
  latency indicator or a change to Studio's take alignment. Synthetic fixture
  correctness does not certify a live route.
- **Input and privacy:** one stable regular PCM WAV, two distinct selected
  channels, bounded file size/duration/rate/channel/event counts and fixed
  errors. No capture, network, device enumeration or freeform report metadata.
- **Failure behavior:** silence, noise, clipping, continuous or ambiguous
  signals and malformed/changing inputs must never become plausible false
  timing success. Reports do not award physical PASS.
- **Workflow:** the probe generator creates a file only on an explicit command.
  Actual playback/capture requires its own authorization. No first-screen
  controls, invitation authority or participant transport behavior changes.

Exact automated counts, mathematical fixture evidence and hosted checks belong
in the OPEN DRAFT PR body. Codex self-QA is not Karen PASS.

All holds remain: no merge/squash/tag/sign/release/Pages/Release Trust/Publish,
deploy/send/spend/live Cisco, public rendezvous or automatic media capture.
Unsigned 0.27.2 stays Jeff-only; parked #37/#49 and pending drafts stay intact.
