# WebJam help routing map

Maps common musician questions to the v0.13.0 test-night interface. Host and
Join both use the short sound confirmation before Band Check. Earlier packages
are rollback history only. Setup Wizard, **Start Audio**, raw server fields,
and color-named buttons are legacy paths. Physical two-Mac audio and Logic
results remain **NOT RUN** until recorded in the pilot worksheet.

| User need | Current path |
|---|---|
| Start a new band session | Launch WebJam → **Host a Jam** → confirm name and band sound → complete Band Check → **Start Session**. |
| Join a band session | Open or paste the invite → confirm name and band sound → Band Check → **Start Session**. |
| Check input, headphones, scratch recording, or live readiness | `F2`, **More → Band Check**, or **Settings → Run Band Check** |
| Share the session | Live bottom bar → **Copy Invite** → send the complete same-LAN v2 link only to the intended bandmate; it is normally a reusable session-scoped bearer credential, while an **Automatic Local Originals are off** warning identifies a v1 guest with join/play plus a server track, but no WebJam local-original capture/delivery |
| Change this Mac's monitor mix | Participant cards → fader / **Mute Monitor** / **Solo** |
| Record the whole band | Live bottom bar → **Record** |
| Review tracks and takes | **More → Multitrack Studio** |
| Choose Studio output / keep this Mac's isolated inputs | **More → Multitrack Studio → Recording Setup** |
| Review a take before export | **More → Multitrack Studio** → open the take → read the shared elapsed-time ruler → select a lane → inspect source, timing, and known gaps → review its non-destructive gain/pan/mute/solo mix |
| Prepare aligned Logic stems | In the open Studio take, review each track's saved **Logic export** choice → **Export for Logic** → **Show Logic Export**; the choice is used for future exports until changed and never changes WAVs or `webjam-take.json`; a selected silent track or unaligned local original pauses export until reviewed |
| Capture rehearsal notes | **More → Session Notes** |
| Add optional video/conversation | **More → Add Video or Conversation** |
| Speak during rehearsal | Mute the audio interface before unmuting Webex; if that is unavailable, end the WebJam session first |
| Change display name or conversation link | **More → Settings** or **Ctrl+,**; it also exposes Band input and Band output & review |
| Resolve a connection/device problem | Follow the one stage action; run live-observe **Band Check** with `F2` for details without restarting the session |
| Allow a denied microphone | Stage → **Open System Settings** → Privacy & Security → Microphone → return → **Try Again** |
| Retry after Wi-Fi interruption | Restore the same network → stage **Try Again** if automatic reconnect times out |
| Retry a lab-only v3 private link | Use **Try Again** only when WebJam says the sidecar failed before guest enrollment. If it says **Fresh invitation required**, ask the host for a new link; do not retry the old link or fall back to a local/legacy session. v3 is not public or Internet hosting. |
| Recover an interrupted local recording | Relaunch WebJam → open the recovered **NEEDS ATTENTION** project in Multitrack Studio → manually review its checkpoint/gap evidence. Do not treat recovered guest media as automatically uploaded or export-ready. |
| Leave without ending the host's jam | Guest bottom bar → **Leave Jam**; active opted-in local capture is finalized, queued, and given a final upload attempt |
| End the jam for everyone | Host: **Stop Rec** if needed → wait for **Take saved** → bottom bar **End Session** |
| Save / load monitor mix | **Ctrl+S** / **Ctrl+O** |
| Copy a short redacted diagnostics summary | **Ctrl+Shift+D** |
| Preview and save a privacy-safe support bundle | **Band Check (`F2`) → Save Support Bundle** |
| Keyboard shortcut reference | **F1** |
| Exact two-Mac test-night run | [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) |
| Recording and Logic workflow | [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md) |
| Why Studio has no beat grid | Studio is a recording-review workspace, not a Logic clone; its ruler is elapsed time only and it does not invent tempo, bars, beats, or beat editing. |
| Developer band-server detail | [`server/README.md`](server/README.md) |
| Companion API for external tools | [`COMPANION_API.md`](COMPANION_API.md) |
