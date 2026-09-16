# Show WebJam: the door and the first room action

A short source walkthrough for a teammate, not a replacement for
[First Session](FIRST_JAM.md) or the [simple-language guide](README_SIMPLE.md).
Allow two minutes for the door, then another minute to open a Make together
room and find its Conversation controls. One computer is enough to show that
next action; a second artist is needed to demonstrate joining and making together.

> **Boundary:** this script runs a source checkout, not a signed package.
> Every v0.28.0 physical and platform-trust gate remains **NOT RUN** — see
> [README](README.md) for the exact published release and its checksums.
> The door-only part stops before Host or Join. The room continuation starts
> a real local-network Art room, then ends it; it requires no Jamulus, meeting
> launch, shared canvas, or video file. Later source changes are not included
> in the immutable v0.28.0 packages.

## Run it

```bash
# Headless door and room-overview checks; exit when the tests finish.
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_art_start_ux.py tests/test_art_room_overview.py

# Interactive source demo; close the window when finished.
.venv/bin/python webjam_qt_main.py
```

(`DEVELOPMENT.md` covers setting up `.venv` if you don't have one yet.)

## Two-minute door preview

1. **Launch WebJam.** The first screen shows two workspace cards, side by
   side, with no badge, caveat, or tool name on either card:
   - **Art** — "Make art together."
   - **Music** — "Play live together."

   Host and Join are also visible. The saved workspace may already be
   selected; Art also shows its two activity cards. Let the workspace cards
   speak for themselves — that's the point of the ten-second gate in
   [docs/MERGE_AND_RELEASE.md](docs/MERGE_AND_RELEASE.md#1-ten-second-ux-gate).

2. **Choose Art.** Two more cards appear:
   - **Make together** — "Talk, make, or draw together in one room." Everyone
     works in their own space; the host can open one shared canvas from
     inside the room if the group wants to draw together.
   - **Paint along** — "Follow one silent process video while you paint."

   Point out that neither card names Jamulus, Webex, Drawpile, Krita, or any
   other tool — that's enforced by `tests/test_art_start_ux.py`, not a styling
   choice.

3. **Select Make together, then Paint along.** Each selection stays on the
   door, with **Host** and **Join** below. Stop before pressing either role
   button; selecting an activity has not opened a room or created an invite.

4. **Choose the Music card.** The Art activity cards disappear, leaving
   **Host** and **Join** as the role choices. Close the window here if you
   only want the door preview, or continue below.

## One more minute: open a room and find the next action

1. **Choose Art → Make together → Host.** Wait for **Your room is open**.
   This opens an Art room on your local network. If the network needs
   attention, follow the shown recovery action; do not describe the room as
   open until WebJam confirms it.

2. **Point out Copy Invite and Waiting for artists to connect.** The room
   says **Make from your own space** and explains: "Use paper, clay, a model,
   printer, or your usual app. Conversation can carry talk or a screen share."
   No one else has joined merely because the room is open or the invite was
   copied. For an actual collaborator, follow the invitation steps in
   [First Session](FIRST_JAM.md#start-an-art-room).

3. **Choose Set Up Conversation in the room.** If a meeting link is already
   saved, the same button says **Conversation**. With no link, the panel's
   next action is **Add Link**; with a saved link that has not been opened,
   it is **Join / Open Meeting**. Opening this panel alone opens no meeting.
   Stop at that next action for this demo. To talk or share a demonstration,
   add your real public HTTPS meeting link and explicitly join it, then use
   the meeting app's microphone and sharing controls.

4. **Choose End Room and confirm.** If cleanup needs another attempt,
   choose **Try End Room** until it finishes. Close the window when the room
   has ended. A separate meeting or drawing app keeps its own close controls.

For a **Paint along** walkthrough instead, choose that card before **Host**.
If the room overview is still showing, choose **Open Paint along**. Its
workspace offers **Watch a shared lesson**, which leads to the
same Conversation controls, or **Choose process video…** for a silent local
file. **Back to room** returns without ending the room; finish with **End Room**.
Showing those choices does not prove video playback or shared meeting media.

## What this does and doesn't prove

- **Automated door checks cover:** workspace and Art-card order, labels,
  exactly two Art activities, and no vendor/tool name or Preview/Ready/API
  chrome on the door —
  `tests/test_art_start_ux.py`. Run the headless check above for this checkout;
  use its exact-commit CI result for hosted evidence.
- **Automated room checks cover:** connection-based room wording and the
  **Set Up Conversation** / **Conversation** next action —
  `tests/test_art_room_overview.py`. Existing controller and Conversation
  journey tests exercise panel navigation and explicit meeting handoff.
- **The one-computer continuation shows:** where an artist goes next after
  opening a room. It does not prove another person has joined, can hear you,
  or can see a demonstration. Those need real participants and observation.
- **Doesn't show:** the native iPhone/iPad Art companion. That is a separate
  same-LAN guest app — [docs/MOBILE.md](docs/MOBILE.md) — and is not part of
  this desktop walkthrough. Physical device feel and signing
  remain **NOT RUN**.
- **Doesn't prove:** that the *packaged, signed* build feels obvious to a
  first-time user. That's the
  [owner click gate](UX_ACCEPTANCE_CHECKLIST.md#owner-click-gate-current-two-card-door)
  — Jeff-only, against a checksum-verified release, and currently **NOT RUN**.
  This source walkthrough helps show a teammate the door and next room
  action today; it is not a substitute for that gate.

## If something looks different from this doc

Record `git rev-parse HEAD` with your walkthrough notes. This script was
checked against source based on master
`7b9b1cc11d3325d1eb95b7459879a6823d9673a2` (#128); published v0.28.0 packages
come from `8ac08b69865af598e5ff69a84066964b1d90c940`.
If the app disagrees with this file, record its exact wording and the source
commit. The door and room checks above help locate the mismatch; this source
guide can drift.
