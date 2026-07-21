# WebJam architecture — v0.17.0

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
Record time, not at music startup.

The Studio boundary is layered:

| Layer | Responsibility |
| --- | --- |
| `core.studio_project` | Immutable, path-free schema-v2 arrangement in durable IDs and project frames |
| `core.studio_history` | Thread-safe, entry/byte-bounded exact undo/redo snapshots and gesture coalescing |
| `core.studio_store` | Exact-token, locked, atomic sidecar persistence, migration, backup, and recovery |
| `core.studio_controller` | Selection, dirty state, autosave coalescing, conflicts, and async generation cancellation |
| `core.studio_comping` / `core.studio_source_catalog` | Same-session repeated-take lanes, quick-swipe selections, and full take/track/segment trust binding |
| `core.studio_renderer` | Shared gap-aware frame/rate/DSP truth for arranged playback and export |
| `core.take_player` | Exact transport, output-device lifecycle, cycle delivery, and typed playback failures |
| `core.studio_waveform` | Cancellable visible-range tiles, bounded cache, and descriptor-bound source verification |
| `core.studio_export` | Fail-closed source snapshots and descriptor-relative evidence-rich 24-bit publication on supported macOS/Linux runtimes; unsupported platforms use the separate aligned-originals UI path |
| `webjam_qt.widgets.studio_arrange` | Fixed-header Arrange viewport, frame-domain selection/gestures, keyboard editing, and bounded painted waveform bindings |
| `webjam_qt.widgets.studio_editing` | Arrange, comp, section, snap, fade, and crossfade command surfaces |
| `webjam_qt.widgets.studio_review` | Track review lanes, mixer controls, meters, timeline ruler, and waveform overview widgets |
| `webjam_qt.widgets.studio_waveforms` | Generation-bound visible-tile scheduling and UI-thread delivery |
| `webjam_qt.widgets.studio_arrangement_workflow` | Arrangement/controller synchronization, take lanes, autosave, arranged playback reloads, and Studio export coordination |
| `RecordingStudio` | Take library, live-recording shell, transport/output UI, responsive composition, and worker shutdown ownership |

Studio state is a sidecar beside the take, never an update to `webjam-take.json`
or recorder WAVs. Region removal and source-inventory reconciliation use durable
tombstones so a later rescan cannot silently recreate a user deletion. The same
renderer drives playback and export; a cross-take comp must resolve through the
trusted source catalog before either path opens its media.

Autosave is requested by the framework-neutral controller and scheduled on the
Qt event loop. Save conflicts and disk failures leave the document dirty and
retryable. Switching takes cancels generation-bound waveform work and rejects
late waveform or export results that no longer belong to the selected take;
starting another export or shutting down cancels export work. Studio playback
output remains a review-only choice and is not part of Jamulus configuration.
The extracted arrangement-workflow mixin coordinates those services without
owning widget construction; the shell remains responsible for live capture,
take switching, worker lifetime, and the responsive library/editor/inspector
composition.

## Truth and failure behavior

Jamulus RPC supplies process/authentication/roster/connection facts, never an
invented audio-device or Webex claim. A human confirmation supplies audibility.
End/Leave stops only WebJam-owned processes and hosts finalize recording before
server shutdown. Band Check is an optional live observer; it does not restart
or configure the music engine.
