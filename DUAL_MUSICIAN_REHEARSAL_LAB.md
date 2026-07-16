# Dual-Musician Rehearsal Lab

This is a bounded, repeatable source-level rehearsal of WebJam's private
host/guest recording path. It is designed to prove the production control,
transfer, project, Studio, export, and cleanup code without touching a
musician's normal WebJam data, audio devices, or Jamulus profile.

It is evidence for code behavior—not a substitute for a two-Mac rehearsal.

## Run the source-level gate

Run this from the repository root. It needs no audio hardware, Jamulus binary,
private LAN, or special environment variable.

```bash
.venv/bin/python -m pytest -q tests/test_dual_musician_rehearsal_lab.py
```

The source-level lab uses real `HostPeerSession`, `GuestPeerSession`,
`SessionPeerServer`, `SessionPeerClient`, and loopback HTTP transfer. Its only
test-scoped policy injection is loopback admission; production still rejects
loopback as a private-session endpoint.

## Optional real-Jamulus transport companion

The companion is Linux-only and opt-in. It requires the checksum-pinned
official Jamulus 3.12.2 server/client packages, JACK dummy support, and the
test dependencies installed by the `integration-jamulus` CI job.

```bash
WEBJAM_RUN_JACK_AUDIO_INTEGRATION=1 \
WEBJAM_JAMULUS_BINARY="$(command -v jamulus-headless)" \
WEBJAM_JAMULUS_CLIENT_BINARY="$(command -v jamulus)" \
.venv/bin/python -m pytest -q -s \
  tests/test_dual_musician_rehearsal_lab_real_jamulus.py
```

CI runs this alongside the existing real two-client JACK harness. It proves
one real JamulusServer, two real Jamulus clients, synthetic marker audio
through the codecs, recorder start/stop, a guest disconnect/reconnect, and
owned process/port/JACK cleanup. It does not turn the source-level capture
fixture into a physical-audio claim.

## What the source-level lab exercises

- Starts a host peer service, issues/parses a v2 invitation, and enrolls one
  guest with a stable installation-derived identity.
- Records a bounded gapped take and a bounded clean take. The gap is injected
  only at the synthetic local-capture boundary; real peer control and transfer
  use loopback HTTP.
- Forces a real local connection-refused control interruption, resumes the
  active guest capture, checks checksum/PCM-verified upload and idempotent host
  reconciliation, then verifies guest relaunch keeps one participant identity.
- Opens the clean take through the Studio playback core, verifies seek and
  track-keyed non-destructive state, then exports equal-length 48 kHz PCM24
  stems without changing source WAV bytes.
- Stops lab-owned host/guest resources, verifies peer-port release and no new
  WebJam runtime threads, preserves Local Originals, then launches/stops a
  fresh host session with rotated credentials.

## Isolation, artifacts, and cleanup

Every source-level run stays below pytest's temporary root:

```text
<pytest tmp>/dual-musician-rehearsal-lab/<run-id>/
```

The lab creates its host/guest takes, installation identities, transfer queue,
take projects, Studio sidecar, and export package only under that root. It does
not alter `HOME`, normal WebJam settings, normal takes, native Jamulus profiles,
or the installed application.

It writes these safe, mode-`0600` evidence files:

- `lab-report.json` — bounded step timings, classifications, outcomes, and
  explicit limitations.
- `cleanup-manifest.json` — port/thread release, fresh-session relaunch, and
  Local Originals preservation checks.

The report contains no invitation bearer, participant bearer, peer port, or
session ID. Lab artifacts are scanned for invitation and participant bearers.
The source test fails if cleanup/relaunch or the privacy checks fail.

## Evidence boundaries

| Classification | What it means here |
| --- | --- |
| Source-level deterministic | Host/guest peer runtime, identity, transfer, project, Studio state, export, privacy report, and cleanup were exercised in one isolated process. |
| Synthetic capture | The WAVs, declared capture gap, and zero-origin alignment are deterministic fixtures. |
| Real-Jamulus transport | Only the opt-in Linux/JACK companion: real Jamulus processes and synthetic marker audio crossed their codecs. |
| Artifact verified | Take/project/export/report bytes and declared PCM24 facts were checked. |

The following remain **NOT RUN** unless separately performed and recorded on
the named hardware:

- Two physical Macs, real musicians, headphones, and bidirectional audibility.
- CoreAudio/interface selection, permissions, hardware removal, sleep/wake,
  and proof that Jamulus and Local Originals use the intended physical route.
- Physical LAN/Wi-Fi interruption, firewall behavior, or Internet traversal.
- Webex behavior during a rehearsal.
- Import/playback in an external editor, including Logic Pro.

Do not describe a passing lab run as a physical rehearsal, hardware test, or
external-editor verification.
