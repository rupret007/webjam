# Send to Logic / Export for DAW Evidence

Date: 2026-09-24
PR: #155
Commit: 2d1cf07

## Button States

The "Send to Logic" (macOS) / "Export for DAW" (other platforms) button appears
in the Recording Studio's playback controls area.

### State 1: Disabled (no take selected)
- Button text: "Send to Logic" (macOS) or "Export for DAW" (other)
- Enabled: No
- Tooltip: "Select a completed take to send to Logic."

### State 2: Disabled (during recording)
- Button text: "Send to Logic" / "Export for DAW"
- Enabled: No
- Tooltip: "Stop recording before sending to Logic."

### State 3: Disabled (during export)
- Button text: "Send to Logic" / "Export for DAW"
- Enabled: No
- Tooltip: "Wait for the current export to finish."

### State 4: Ready (valid take selected)
- Button text: "Send to Logic" / "Export for DAW"
- Enabled: Yes
- Tooltip: "Create a Logic-ready handoff folder with aligned 24-bit stems,
  MIDI tempo/markers, and import instructions. Works with any DAW that imports
  WAV and Standard MIDI files."

### State 5: Exporting
- Button text: "Sending…"
- Enabled: No
- Hint area: "Creating Logic handoff folder…"

### State 6: Success (macOS with Logic installed)
- Dialog: "Open in Logic Pro?" with Yes/No buttons
- Details: Stem count, sample rate, import instructions
- On Yes: Opens session.mid in Logic Pro
- On No: Reveals folder in Finder

### State 7: Success (other platforms or Logic not installed)
- Folder revealed in file manager (Finder/Explorer/xdg-open)
- Hint area: "Logic handoff ready · N stems · folder name · Open session.mid
  as a new Logic project, set sample rate to X Hz, then drag all WAVs to bar 1."

## Gate Verification

### Ruff (exact as CI)
```
$ ruff check webjam_qt/ core/ ui/ services/ api/
All checks passed!
```

### Pytest (relevant tests)
```
$ QT_QPA_PLATFORM=offscreen pytest tests/test_logic_handoff_adapter.py \
    tests/test_send_to_logic_ui.py -v
36 passed in 0.32s
```

Test breakdown:
- test_logic_handoff_adapter.py: 30 tests
  - Build session: 4 tests
  - BPM default labeling: 3 tests
  - Time signature: 2 tests
  - Markers: 3 tests
  - No MIDI: 1 test
  - Refusal cases: 2 tests
  - Can export validation: 3 tests
  - Stem alignment: 2 tests
  - Multi-segment: 3 tests
  - Gapped segments: 1 test
  - Overlapping segments: 2 tests
  - Path safety: 4 tests

- test_send_to_logic_ui.py: 6 tests
  - Full export integration: 4 tests
  - Cross-platform compatibility: 2 tests

## Platform Behavior

| Platform | Button Label      | Folder Reveal Method |
|----------|-------------------|---------------------|
| macOS    | "Send to Logic"   | `open` command      |
| Windows  | "Export for DAW"  | `os.startfile()` or `explorer` |
| Linux    | "Export for DAW"  | `xdg-open`          |
| Fallback | varies            | `QDesktopServices.openUrl()` |

## Multi-Segment Export Behavior

Tracks with multiple segments (punch-ins, retakes, gaps) export each segment
as a separate stem:
- First segment: "Track Name"
- Subsequent segments: "Track Name (part 2)", "Track Name (part 3)", etc.

Each stem retains its exact `project_start_frame` for timeline alignment.

Overlapping segments are rejected with a clear error message.

## Path Safety

The adapter validates all segment paths:
1. Paths must resolve to inside the take directory (rejects `../escape.wav`)
2. Symlinks are rejected (prevents indirection attacks)
3. Missing files are reported with track name and segment index

## Synthetic Evidence Notice

All tests in this PR are synthetic automated checks. No physical audio testing,
Logic Pro GUI import verification, or human QA was performed. The export
produces standard WAV and SMF files that should work in any DAW supporting
these formats, but actual import behavior has not been verified.
