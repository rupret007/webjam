# Reference Track macOS physical pilot

> **v0.22.3 published boundary:** the callback and source-load changes are
> included in the immutable published v0.22.3 private test candidate. This
> pilot remains physical evidence, not an audibility claim.

This runbook records the gates automation cannot prove. Do not convert a
connection, moving meter, decoded waveform, process state, or server roster
entry into an audibility result.

The immutable v0.22.3 packages keep playback fail-closed unless the production
factory proves the local BlackHole route on that Mac. Run this procedure only
against an exact published v0.22.3 package and record its filename and SHA-256
with the evidence. Do not use the constructor's test-only
`physical_route_certified` override, and never add an environment variable,
setting, command-line flag, or user-facing bypass to a downloadable package.

## Exact evidence header

Record before testing:

- WebJam version, full build ID, package filename, and SHA-256;
- Mac model, macOS version (14.2 or later), architecture, interface, and wired
  headphones;
- Jamulus version and exact hosted-server endpoint;
- BlackHole version and the exact route name selected by the backend;
- test-track filename without its folder path, format, sample rate, channel
  count, duration, and SHA-256;
- second endpoint hardware, OS, Jamulus version, and tester;
- start/end timestamps and tester names.

Use a short, rights-cleared test file containing distinct spoken numbers or
tones, silence, and repeatable transients. Never include a private song path,
Webex link, invitation, token, password, or RPC secret in evidence.

## Preconditions

1. Install the exact WebJam candidate and BlackHole on the host Mac.
2. Use wired headphones on both Jamulus endpoints. Disable speakers, direct
   hardware monitoring, and OS sound enhancements.
3. Start a private hosted jam and join from a physically separate second
   endpoint. Confirm ordinary two-way Jamulus playing first.
4. Keep Webex closed for the baseline. If Webex is tested later, its
   microphone must remain muted while musicians play.
5. Confirm no old `WebJam Track` participant, backing Jamulus process, client
   RPC listener, stale virtual connection, or session-unique Reference Track
   profile/secret/quarantine entry exists before starting. Inventory names by
   bounded pattern; do not assume the old fixed filenames.
6. Confirm no unrelated process has BlackHole selected for input or output.
   Keep a dated process/device inventory as physical evidence. The source pilot
   coordinates every WebJam 16ch/64ch owner, including an orphaned backing
   child, but does not establish system-wide ownership against unrelated audio
   applications.

## Core acceptance

For every step, record PASS, FAIL, or NOT RUN plus a timestamp and observation.

1. Open the direct **Reference Track** action as host, then confirm **More → Reference
   Track…** opens the same panel and a guest cannot open either route.
2. Verify the panel keeps source and route states separate and names BlackHole
   readiness without exposing a filesystem path. Whether or not route proof is
   currently available, load and inspect WAV/WAVE, AIFF, and FLAC one at a time.
   Exercise MP3 only when the packaged decoder advertises it. Reject malformed,
   renamed, symlinked, unsupported-channel, and oversized files safely.
3. Choose **Recheck Route**. Require a bounded status refresh with no playback,
   backing-client launch, or source loss.
4. Press Play. Require exactly one separately named `WebJam Track` participant
   on both mixers and one separately owned backing client. Record the primary
   and backing Jamulus PIDs and the input/output device identity WebJam reports
   for each live process; the backing process must prove the exact selected
   BlackHole device in both directions. Do not substitute saved profile text.
5. On both endpoints, listen through Jamulus. Require the same clean musical
   passage, no physical/direct-monitor duplicate, and no feedback.
6. Move the track participant's fader independently on each endpoint. Require
   only that musician's monitor level to change.
7. Mute the track on one endpoint. Require the other endpoint to remain
   audible.
8. Exercise pause, restart, paused seek, loop in/out, source trim, count-in,
   and rapid but valid controls. Require one bounded transition per command,
   no hang, and no duplicate client.
9. Start server recording before playback, during playback, and during a
   paused interval. Require one aligned Reference Track stem and no corruption
   of musician stems. Verify silence where the track was paused.
10. Confirm the host never hears a second local copy outside the primary
   Jamulus mix. Muting `WebJam Track` in the host's primary mixer must make it
   inaudible to the host.

## Failure and cleanup acceptance

1. While playing, remove or rename the virtual route. Require immediate safe
   stop and a truthful failure state.
2. Change the primary Jamulus input or output, route it to BlackHole, or stop
   either primary I/O direction. Require silence and safe stop on the next
   bounded live-route check without stopping the primary client.
   Repeat with a duplex physical interface. This specifically tests the
   reported CoreAudio wrong-input result. The filename-only primary profile is
   under WebJam's private Application Support launch directory; leave the
   musician's normal Jamulus profile untouched. Live PID-bound CoreAudio route
   evidence is the authoritative check.
3. Sleep/wake or otherwise delay route checking past its freshness window.
   Require silence rather than reuse of stale pre-sleep evidence.
4. Terminate the backing client RPC connection. Require playback refusal/stop;
   the primary musician client and hosted server must remain live.
5. Disconnect and reconnect the hosted Jamulus session. Require safe teardown,
   no automatic unproven resume, and an explicit fresh Play after health proof.
6. Attempt to activate the legacy Webex audience bridge on the same virtual
   device. Require mutual exclusion, not mixed ownership.
7. End the jam while playing. Require the stream and backing client to stop
   before the primary client/server; recording finalization must remain safe.
8. Quit WebJam while ready, playing, paused, routing, and failed. Require no
   owned backing process, RPC port, secret, session-unique profile, quarantine
   entry, virtual connection, lifecycle claim, or decoder worker after the
   bounded shutdown window. Force the parent pilot process to exit while its
   backing Jamulus child is alive: a second pilot must refuse ownership until
   the orphan child exits, then acquire it normally.
9. Repeat load/play/pause/stop for at least 25 cycles, then run a 60-minute
   rehearsal. Record CPU, memory trend, underruns, route stability, and process
   residue.
10. Instrument callback duration and allocation/lock behavior under CPU,
    storage, UI, and network load. The source pilot now uses a preallocated SPSC
    handoff; its callback performs no mutex acquisition, wait, source I/O, or new
    audio-buffer allocation and pulls into caller-provided output. Still require
    physical allocation and callback-timing evidence at the native boundary;
    source review and synthetic tests alone are not promotion evidence.

## Webex coexistence check

After the standalone audio gates pass, open a configured Webex meeting
externally and join from a second endpoint. Keep Webex muted while playing.
Require that Webex open/close/failure neither replaces nor ends Jamulus or the
Reference Track. Webex speech/camera/admission remain Webex-owned claims and
must be recorded separately.

## Promotion rule

The current macOS production factory may make Play available after
machine-derived certification of an official 48-kHz BlackHole 16ch/64ch route,
but that result must not be promoted or described as physical acceptance.
Physical promotion still requires every core and cleanup gate to pass on two
real Jamulus endpoints against the exact source-candidate hash, the reported
CoreAudio switch case to be independently closed, and BlackHole exclusive
ownership to be either proven in code or enforced by a reviewed setup. The
callback's reviewed non-blocking, preallocated handoff must also pass the
physical allocation and timing gate; a clean machine check or synthetic run
alone is insufficient.
Windows and Linux audibility remain **NOT RUN** until their own backends repeat
the same physical evidence. A failed isolation, feedback, wrong participant,
uncontrolled return, primary-client interruption, recording corruption, or
owned-process leak blocks promotion.
