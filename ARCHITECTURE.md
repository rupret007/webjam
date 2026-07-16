# WebJam architecture — v0.16

## Product boundary

WebJam is an orchestration layer around unmodified Jamulus and optional Webex.
The boundary is deliberate:

| Layer | Responsibility |
| --- | --- |
| `webjam_qt` | Host/Join launch, Session HUD, invitations, recording/Studio UI, recovery messages |
| `services/bridge_service.py` | Direct owned-process launch/stop, hosted-server supervision, authenticated Jamulus RPC, external Webex launch |
| `core/jamulus_profile.py` | Dedicated Jamulus profile launch contract and private, allowlisted restart records |
| Jamulus | Live devices, channels, buffer, jitter, quality, mix, and actual music connection |
| Webex | Conversation/video meeting state and device controls |

## Jamulus-native launch

On macOS `JamulusNativeProfileManager` safely selects the Jamulus configuration
directory and passes only:

```text
--inifile WebJam-native-v0.16.ini
```

Jamulus alone creates and writes that profile. WebJam does not write profile
content or any device/channel/buffer/jitter/quality value. The normal
`Jamulus.ini` is never overwritten. WebJam launches the client directly with
normal GUI visibility, `--connect`, and an authenticated localhost JSON-RPC
surface. It uses no coordinate automation, UI scraping, or undocumented audio
RPC calls.

## Startup state machine

`ApplicationController` projects a role-aware startup attempt into
`SessionHud`:

1. host server start (host only);
2. visible native Jamulus launch and sound setup;
3. process/RPC/connection/local-identity proof;
4. automatic handoff to the ordinary Session HUD and safe invite readiness.

Jamulus setup is not a WebJam approval gate: WebJam watches for fresh,
authenticated connection proof and moves into the session automatically. It
does not call that proof audibility; musicians play a note and verify each
other, with Band Check available if help is needed. Webex is optional under
**More** and never delays the session or invite.

The persisted attempt record holds only a digest ID, generation, role, safe
server/client phases, profile fingerprint, connection state, compatibility
confirmation/conversation state, and next-action enum. It stores no invite,
credential, URL, device data, path, or notes. A restart resumes only after an
exact profile match and new live proof.

## Recording and Studio

`RecordingCoordinator` owns host recorder state, storage readiness, take
validation, recovery journals, and Local Originals handoff. Its work begins at
Record time, not at music startup. `RecordingStudio` owns review playback,
non-destructive mix sidecars, waveform work, and Track Export. Studio playback
output is not part of Jamulus configuration.

## Truth and failure behavior

Jamulus RPC supplies process/authentication/roster/connection facts, never an
invented audio-device or Webex claim. A human confirmation supplies audibility.
End/Leave stops only WebJam-owned processes and hosts finalize recording before
server shutdown. Band Check is an optional live observer; it does not restart
or configure the music engine.
