# Recording and Studio — v0.16

## Recording is separate from live music

Jamulus owns the live interface and mix. WebJam’s optional Local Originals are
a separate capture path for this Mac’s first two interface inputs. They are not
a prerequisite for a jam and they do not change Jamulus settings.

When the host presses **Record** for the first time, WebJam asks:

- **Record Shared Jam Only** — start the synchronized host take now.
- **Also Keep This Mac’s Inputs** — open Recording Setup and explicitly choose
  a valid two-channel Local Originals input before recording.

The host keeps shared recording authority. Guests can opt into Local Originals
only when the active private session supports them; they are never interrupted
while joining music.

## Recording readiness

WebJam checks takes storage, recorder control, the known roster, and any
explicit local-capture setting when Record is requested. It does not perform
those checks during Host or Join. A failed record preflight preserves the live
jam and explains the next safe action.

## Studio

Studio opens from **More → Studio**. It is a Logic-like review workspace, not
a Logic integration:

- review verified takes and per-track truth;
- choose playback output only while reviewing a take;
- use non-destructive level, pan, mute, and solo choices;
- inspect waveforms, gaps, and source state;
- export aligned 24-bit stems and a rough mix for another editor.

Studio does not alter source recordings. Export never rewrites the original take.
Local Originals remain separate, preserved media and appear only when real
files are available.
