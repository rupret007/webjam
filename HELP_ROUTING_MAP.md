# WebJam help routing map (v0.10.0 Qt app)

Maps common musician questions to the current interface. Setup Wizard,
**Start Audio**, raw server fields, and color-named buttons are legacy paths.

| User need | Current path |
|---|---|
| Start a new band session | Launch WebJam → **Host a Jam** → complete Band Check if shown → **Start Session** |
| Join a band session | Open the invite (WebJam fills/accepts it), or launch WebJam → **Join a Jam** → paste invite → **Join Jam**; complete Band Check if shown → **Start Session** |
| Check input, headphones, scratch recording, or live readiness | `F2`, **More → Band Check**, or **Settings → Run Band Check** |
| Share the session | Live bottom bar → **Copy Invite** → send the complete link only to the intended bandmate; it is normally a reusable session-scoped v2 bearer credential, not a one-use token, while an **Automatic Local Originals are off** warning identifies a v1 guest with join/play plus a server track, but no WebJam local-original capture/delivery |
| Change this Mac's monitor mix | Participant cards → fader / **Mute Monitor** / **Solo** |
| Record the whole band | Live bottom bar → **Record** |
| Review tracks and takes | **More → Multitrack Studio** |
| Choose Studio output / keep this Mac's isolated inputs | **More → Multitrack Studio → Recording Setup** |
| Prepare aligned Logic stems | Select a Studio take → **Export for Logic** → **Show Logic Export** |
| Capture rehearsal notes | **More → Session Notes** |
| Add optional video/conversation | **More → Add Video or Conversation** |
| Pause music send for conversation | **More → Talk Break**, then hold Spacebar in Webex |
| Change display name or conversation link | **More → Settings** or **Ctrl+,** |
| Resolve a connection/device problem | Follow the one stage action; run live-observe **Band Check** with `F2` for details without restarting the session |
| Allow a denied microphone | Stage → **Open System Settings** → Privacy & Security → Microphone → return → **Try Again** |
| Retry after Wi-Fi interruption | Restore the same network → stage **Try Again** if automatic reconnect times out |
| Leave without ending the host's jam | Guest bottom bar → **Leave Jam**; active opted-in local capture is finalized, queued, and given a final upload attempt |
| End the jam for everyone | Host: **Stop Rec** if needed → wait for **Take saved** → bottom bar **End Session** |
| Save / load monitor mix | **Ctrl+S** / **Ctrl+O** |
| Copy a short redacted diagnostics summary | **Ctrl+Shift+D** |
| Preview and save a privacy-safe support bundle | **Band Check (`F2`) → Save Support Bundle** |
| Keyboard shortcut reference | **F1** |
| Exact two-Mac test-night run | [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) |
| Recording and Logic workflow | [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md) |
| Developer band-server detail | [`server/README.md`](server/README.md) |
| Companion API for external tools | [`COMPANION_API.md`](COMPANION_API.md) |
