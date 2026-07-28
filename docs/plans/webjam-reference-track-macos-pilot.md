# Reference Track macOS physical pilot

This runbook records the gates automation cannot prove. Do not convert a
connection, moving meter, decoded waveform, process state, or server roster
entry into an audibility result.

The v0.20.0 production factory intentionally keeps playback locked. Run this
procedure only from a separately identified, instrumented source-pilot build
whose internal test wiring explicitly constructs
`MacOSBlackHoleReferenceBackend(physical_route_certified=True)`. Record that
source diff and build hash with the evidence. Never add an environment
variable, setting, command-line flag, or user-facing bypass to a downloadable
package.

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
   RPC listener, or stale virtual connection exists before starting.
6. Confirm no unrelated process has BlackHole selected for input or output.
   Keep a dated process/device inventory as physical evidence; the source pilot
   does not yet establish system-wide exclusive ownership itself.

## Core acceptance

For every step, record PASS, FAIL, or NOT RUN plus a timestamp and observation.

1. Open **More → Reference Track…** as host. Confirm a guest cannot open the
   panel.
2. Verify the panel names BlackHole readiness without exposing a filesystem
   path. Load WAV, AIFF, FLAC, and MP3 samples one at a time; reject malformed
   and unsupported files safely.
3. Press Play. Require exactly one separately named `WebJam Track` participant
   on both mixers and one separately owned backing client. Record the primary
   Jamulus PID and the input/output device names WebJam reports as live proof;
   do not substitute saved profile text.
4. On both endpoints, listen through Jamulus. Require the same clean musical
   passage, no physical/direct-monitor duplicate, and no feedback.
5. Move the track participant's fader independently on each endpoint. Require
   only that musician's monitor level to change.
6. Mute the track on one endpoint. Require the other endpoint to remain
   audible.
7. Exercise pause, restart, paused seek, loop in/out, source trim, count-in,
   and rapid but valid controls. Require one bounded transition per command,
   no hang, and no duplicate client.
8. Start server recording before playback, during playback, and during a
   paused interval. Require one aligned Reference Track stem and no corruption
   of musician stems. Verify silence where the track was paused.
9. Confirm the host never hears a second local copy outside the primary
   Jamulus mix. Muting `WebJam Track` in the host's primary mixer must make it
   inaudible to the host.

## Failure and cleanup acceptance

1. While playing, remove or rename the virtual route. Require immediate safe
   stop and a truthful failure state.
2. Change the primary Jamulus input or output, route it to BlackHole, or stop
   either primary I/O direction. Require silence and safe stop on the next
   bounded live-route check without stopping the primary client.
   Repeat with a duplex physical interface and after making the Jamulus profile
   temporarily unwritable. This specifically tests the reported CoreAudio
   wrong-input result and stale-profile counterexample.
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
   owned backing process, RPC port, secret, virtual connection, or decoder
   worker after the bounded shutdown window.
9. Repeat load/play/pause/stop for at least 25 cycles, then run a 60-minute
   rehearsal. Record CPU, memory trend, underruns, route stability, and process
   residue.
10. Instrument callback duration and allocation/lock behavior under CPU,
    storage, UI, and network load. The present Python callback uses ordinary
    locks and per-call NumPy allocations, so it is a promotion blocker until
    replaced by a preallocated non-blocking handoff or proven equivalent at the
    native callback boundary.

## Webex coexistence check

After the standalone audio gates pass, open a configured Webex meeting
externally and join from a second endpoint. Keep Webex muted while playing.
Require that Webex open/close/failure neither replaces nor ends Jamulus or the
Reference Track. Webex speech/camera/admission remain Webex-owned claims and
must be recorded separately.

## Promotion rule

The macOS backend remains production-locked until every core and cleanup gate
passes on two real Jamulus endpoints against the exact controlled-pilot hash,
the reported CoreAudio switch case is independently closed, and BlackHole
exclusive ownership is either proven in code or enforced by a reviewed setup.
The callback must also have a reviewed non-blocking, preallocated real-time
handoff; a clean synthetic run alone is insufficient.
Windows and Linux audibility remain **NOT RUN** until their own backends repeat
the same physical evidence. A failed isolation, feedback, wrong participant,
uncontrolled return, primary-client interruption, recording corruption, or
owned-process leak blocks promotion.
