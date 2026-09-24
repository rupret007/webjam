# Record along: Logic handoff, phase two

Phase one delivered the core `HandoffSession` writer and command-line interface.
Phase two integrates the Logic handoff into the Qt Recording Studio UI.

## Adapter module: `core/logic_handoff_adapter.py`

The adapter bridges the recording infrastructure (`TakeProject`, `TakeInfo`) and
the Logic handoff writer (`HandoffSession`). It reads exact per-source stems with
their `start_frame` alignment from the take's manifest/timeline data, the project
sample rate, BPM and meter from the project when defined (otherwise documented
defaults), and markers where the project has them.

```python
from core.logic_handoff_adapter import build_handoff_session, can_export_to_logic

# Check if a take can be exported
can_export, reason = can_export_to_logic(take_path)

# Build the session
result = build_handoff_session(take_path)
session = result.session
bpm_is_default = result.bpm_is_default  # True if using 120 BPM default
```

### LogicHandoffAdapterResult

The adapter returns a result object containing:
- `session`: The `HandoffSession` ready for export
- `bpm_is_default`: True when the project uses the default 120 BPM (clearly labeled in UI)
- `time_signature_is_default`: True when using the default 4/4 time signature
- `markers_added`: Count of markers in the session (always includes "Start" at frame 0)

### Supported sample rates

Only Logic-supported sample rates are accepted: 44100, 48000, 88200, 96000, 176400,
or 192000 Hz. Takes at other rates are refused with a clear error message.

### Default BPM labeling

WebJam does not capture MIDI or tempo information from the jam session. The adapter
uses the project's `tempo_bpm` if present (non-default), otherwise 120 BPM. When
the default is used, `bpm_is_default=True` ensures the UI clearly communicates this
to the user so they know the tempo map is a placeholder.

## UI integration: Send to Logic button

A "Send to Logic" button appears in the Recording Studio's playback controls
(macOS only). The button:

- Is visible only on macOS (`sys.platform == "darwin"`)
- Is disabled while recording is in progress
- Is disabled while another export is running
- Is disabled when no completed take is selected
- Is disabled when track export is not available for the current take
- Shows "Sending…" during the export operation

### Export workflow

1. User selects a completed take and clicks "Send to Logic"
2. Playback stops
3. Button disables and shows "Sending…"
4. Adapter builds `HandoffSession` from take data (off-thread)
5. Logic handoff exports aligned 24-bit stems and MIDI file (off-thread)
6. On success:
   - If Logic Pro is installed: prompts to open `session.mid` in Logic
   - Otherwise: reveals the handoff folder in Finder
7. On failure: displays error and restores controls

### Logic Pro detection

The UI checks for Logic Pro by looking for app bundles at:
- `/Applications/Logic Pro.app`
- `/Applications/Logic Pro X.app`

When detected, the user is offered to open `session.mid` directly in Logic.

## Files changed

- `core/logic_handoff_adapter.py` (new): Adapter module bridging takes to HandoffSession
- `webjam_qt/widgets/recording_studio.py`: UI integration with Send to Logic button
- `tests/test_logic_handoff_adapter.py` (new): 20 unit tests for the adapter
- `tests/test_send_to_logic_ui.py` (new): Qt offscreen UI tests for button states

## Testing

### Adapter tests

```bash
python -m pytest tests/test_logic_handoff_adapter.py -v
```

Covers:
- Valid take builds session correctly
- Sample rate validation (all Logic rates supported, others rejected)
- BPM default labeling (120 flagged, custom not flagged)
- Time signature handling
- Marker handling (Start always added, existing preserved)
- No MIDI tracks (WebJam does not capture MIDI)
- Error cases (nonexistent path, empty take)
- `can_export_to_logic` validation function

### UI tests

```bash
python -m pytest tests/test_send_to_logic_ui.py -v
```

Covers (requires Qt display):
- Button visibility on macOS vs other platforms
- Button states (disabled during recording/export/no take)
- Button enabled with valid take
- Export workflow (button text changes to "Sending…")
- Logic Pro detection
- No overwrite of original take data

## Honest behavior

The adapter and UI explicitly do not:
- Invent timing from file names or recording timestamps
- Compensate for latency or network delay
- Resample audio or convert formats
- Capture or synthesize MIDI notes
- Modify or overwrite the original take

The handoff folder is always a new copy alongside (not replacing) the original
recorded take. Default values (120 BPM, 4/4 time) are clearly labeled so the user
knows they may need adjustment in Logic.

## Import into Logic

Follow the generated `README.md` in the handoff folder:

1. Open `session.mid` as a **new project** in Logic to load tempo, meter, and markers
2. Set the project sample rate to match the session (shown in the completion message)
3. Drag all WAV stems together to bar 1 / time zero as separate tracks
4. Keep original audio timing; disable automatic tempo matching/Flex stretching
5. Verify alignment in Logic, including the session end and any named markers
