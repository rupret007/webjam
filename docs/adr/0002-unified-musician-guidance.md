# ADR 0002: Unified musician guidance projection

Status: Accepted for WebJam v0.18 implementation

## Context

WebJam already has authoritative state owners, but its musician-facing answers
are split across several projections:

- `SessionConductor` derives the operational phase and one primary action from
  bounded subsystem facts.
- `SessionLifecycle` retains a redacted transition journal for support.
- `SessionPulse` derives creative structure from musician-authored notes.
- Recording and Studio widgets render their own validation, dirty, and export
  messages.
- Diagnostics and the optional localhost companion API expose separate subsets
  of lifecycle and provider state.

This creates three product risks. Surfaces can disagree, creative text can sit
beside operational state without a visible truth boundary, and every new
consumer is tempted to derive another state machine. It also makes privacy
review difficult because the companion response currently includes a server
address and participant names instead of consuming the same public projection
as the UI.

## Decision

WebJam will add one immutable `MusicianGuidanceSnapshot` as a **projection**, not
an authority or lifecycle. `SessionConductor` remains the sole operational
phase and primary-action derivation. Studio facts are mapped into conductor
facts before acceptance. The new builder accepts the guarded snapshot plus
optional creative content, lifecycle transitions, and a bounded local display
override, then provides a single renderer contract.

The snapshot carries the conductor generation and revision unchanged. It never
advances a phase, invokes a provider, executes an action, reads a path, or
persists state. A stale or contradictory provider observation must therefore be
rejected by `SessionConductor` before guidance is built.

### Fact ownership and consumption

| Fact | Authority | Guidance use | Consumers |
| --- | --- | --- | --- |
| Operational phase and primary action | `SessionConductor` | Copied; a bounded native-setup/topology override may specialize the action ID and fixed local copy without changing phase/evidence | HUD, stage, Canvas, Studio |
| Connection and participant presence | Audio/roster adapters through conductor facts | Evidence category and output summary | HUD, Canvas, public diagnostics |
| Recorder phase | Recording coordinator through conductor facts | Recording status | Canvas, Studio, public diagnostics |
| Take validation and preservation | Recording/take validators through conductor facts | Take/output status | Canvas, Studio, public diagnostics |
| Guest-media transfer | Transfer coordinator through conductor facts | Transfer status | Canvas, Studio, public diagnostics |
| Studio selection, dirty state, arrangement support | Studio controller/widget adapter | Review status only; no phase authority | Canvas, Studio |
| Export worker result | Studio coordinator through conductor facts | Export status | HUD, Canvas, Studio, public diagnostics |
| Recent transitions | `SessionLifecycle.public_timeline()` | Allowlisted state labels; reasons discarded | Canvas, diagnostics |
| Decisions, actions, blockers, questions | `SessionPulse` from deliberate notes | Creative guidance only | Canvas, exported brief |

### Truth boundary

Operational and creative guidance are separate fields. Notes may influence the
creative checkpoint and brief, but cannot affect connection, recording, take,
transfer, Studio, export, cleanup, evidence, recovery, or the primary action.
The snapshot's public form omits the creative projection entirely.

### Rendering and refresh

The application controller builds guidance only after accepting conductor
facts. It distributes that same value to musician-facing renderers. Note edits
rebuild only the debounced creative projection. Studio emits semantic state
changes when selection, dirty state, validation, or export eligibility changes;
the export worker separately publishes its confirmed result.
Guidance is not rebuilt from meter, waveform, playhead, animation, or audio
callback ticks.

The HUD remains the only actionable primary control. Canvas and Studio explain
the same next action but do not add a competing button.

### Public and private representations

`to_public_dict()` is an explicit allowlist containing finite enum values,
booleans, bounded counts, generation/revision numbers, and reason-free lifecycle
transitions. It excludes:

- notes, decisions, actions, blockers, questions, links, and session titles;
- participant names and identifiers;
- invitations, addresses, ports, device names, and paths;
- raw provider state, exceptions, and error strings;
- take names, take IDs, filenames, and export folders.

The Session Canvas and exported session brief may contain musician-authored
creative content because they are direct, local user surfaces. Diagnostics and
the companion API may consume only the public form.

### Future model assistance

No model SDK or generative runtime is introduced in v0.18. A future assistant
may summarize explicitly authorized content only if it is opt-in, read-only,
off the real-time path, unable to execute actions or alter operational truth,
and visibly labeled as a suggestion. No provider abstraction is added until a
measured use case requires it.

## Consequences

- All musician-facing surfaces gain one stable semantic contract.
- Existing authoritative services remain unchanged and independently testable.
- Public diagnostics become more useful while exposing less private data.
- Studio must publish bounded semantic facts when its UI-owned review state
  changes.
- The lifecycle journal remains a support record, not a replacement conductor.
- Some older companion response fields containing names or addresses are
  intentionally removed to satisfy the privacy boundary.

## Verification

The implementation must prove phase/action preservation across every conductor
phase, stale-generation retention, notes isolation, public serialization
allowlisting, cross-surface consistency, Studio dirty/export transitions,
reason-free lifecycle rendering, compact 760x600 layout, keyboard and screen
reader behavior, and absence of guidance work in high-frequency tick paths.
