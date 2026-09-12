# Show WebJam in two minutes

A script for showing someone the door, not a replacement for
[First Session](FIRST_JAM.md) or the [simple-language guide](README_SIMPLE.md).
Use this when you just want to show what WebJam looks like; use those guides
to actually run a session.

> **Boundary:** this script runs a source checkout, not a signed package.
> Every v0.27 physical and platform-trust gate remains **NOT RUN** — see
> [README](README.md) for the exact published release and its checksums. This
> script stops before Host or Join, so it starts no Jamulus process and opens
> no meeting.

## Run it

```bash
# Headless door check; exits when the tests finish.
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_art_start_ux.py

# Interactive source demo; close the window when finished.
.venv/bin/python webjam_qt_main.py
```

(`DEVELOPMENT.md` covers setting up `.venv` if you don't have one yet.)

## The script

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
   **Host** and **Join** as the role choices. Close the window to finish.
   To actually host or join, continue with [First Session](FIRST_JAM.md).

## What this does and doesn't prove

- **Automated door checks cover:** workspace and Art-card order, labels,
  exactly two Art activities, and no vendor/tool name or Preview/Ready/API
  chrome on the door —
  `tests/test_art_start_ux.py`. Run the headless check above for this checkout;
  use its exact-commit CI result for hosted evidence.
- **Doesn't prove:** that the *packaged, signed* build feels obvious to a
  first-time user. That's the
  [owner click gate](UX_ACCEPTANCE_CHECKLIST.md#owner-click-gate-current-two-card-door)
  — Jeff-only, against a checksum-verified release, and currently **NOT RUN**.
  This script is a faster, honest stand-in for showing a teammate the door
  today; it is not a substitute for that gate.

## If something looks different from this doc

The door is guarded by `tests/test_art_start_ux.py` — if the app disagrees
with this file, trust the app and the test, and open an issue noting the
mismatch. This file describes the source walkthrough and can drift.
