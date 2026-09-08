# PRE_KAREN — saved Music listening mix restoration

Declared stack: this branch codex/music-saved-mix-restore depends on OPEN DRAFT
#105 exact 3811d3f0179bc03e90f7490ed8a9469fd8fec607. PR base is
codex/music-effective-listening-mix. Fetched origin/master is
2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2. This does not compose #103/#104.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5592992664

## Leftover and product result

Actual default Load, named Load and connection auto-restore updated model
faders/mutes but sent no native gains. The dormant UDP adapter cannot apply
native listening gain. Explicit Load also left visible cards unchanged until
another authenticated roster. A parsed invalid/unrelated file could falsely
report success. These are ordinary saved-rehearsal journeys, not cosmetic
wording gaps.

Resolve matches and the complete final Solo/mute state under the participant
lock before applying any gain. Restore every matched row and any other row
affected by a Solo transition. Apply current effective gain through #105's
bounded ordered worker with exact participant ownership. Explicit successful
Load refreshes existing listening cards through the existing guarded helper;
auto-restore retains its authenticated local-connection proof gate.

Serialize a detached value snapshot under the same lock. During Solo, optional
pre_solo_muted stores each personal post-Solo choice while the existing muted
field retains the current effective mute. This distinguishes a previously
muted channel made audible by Solo from an explicitly muted Solo channel.
Both the restored immediate state and subsequent unsolo must agree. Legacy
files without the optional field honor the mute values they actually contain;
missing historical intent cannot be reconstructed. Non-Solo snapshots retain
the previous field shape. An older reader can read the file but cannot use
new metadata to restore post-Solo choices.

## Security and ownership

No new endpoint, credential, media engine, device access, playback, capture or
connection claim. Files still use the existing explicit/default mix paths and
atomic write machinery. Optional metadata contains only a boolean listening
choice. No participant identity, file path or credential is added to commands.
Saved names are matching hints, not authenticated identity. Preserve exact-ID
legacy matching and unique normalized-name fallback; reject ambiguous matches.
Unknown rows cannot mutate or dispatch to an unrelated participant.

Stage rows before mutation so a callback cannot observe partial restoration.
Bounds and existing value coercions remain; nonfinite integer conversions
retain prior values rather than raising midway through a load. Resolve duplicate
rows by their final values and exclusive Solo by the last final true candidate.
An unmatched current Solo survives a partial load; clearing a matched Solo
restores other affected participants' personal choices.

Carry exact restored participant objects into native enqueue, then retain
#105's participant/client/monitor-epoch checks at dispatch and socket send.
A replacement row cannot inherit a saved command by reusing the same channel.
Native I/O remains outside the participant and dispatch locks. Already-entered
native writes are not cancellable bytes; commands are sequential, not an atomic
multi-channel transaction. Pan retains its existing path and is not certified.

## Ten-second UX

- Save a quiet reference track and a comfortable collaborator level. Load
  restores those choices in existing cards and native gain commands together.
- Save/load during Solo, then end Solo: prepared levels and personal mute
  choices survive. A selected Solo channel explicitly muted stays muted.
- Named and default loads show a useful message if nothing matches or the
  file has no valid mix. Missing/corrupt/cancelled paths retain their recovery.
- Automatic restore remains silent and runs only after existing local native
  connection proof. A remote-only roster does not establish that proof.
- Card projection creates no feedback gestures, new participant or connection
  evidence. A queued older roster cannot overwrite the newer restored mix.
- No new Art door control, configuration tour, guest playback authority or
  musician-only restriction on Art. Notes and external Conversation stay owned
  by their existing paths.

## Evidence and limits

Baseline native tests used exact saved #105 production sources: initial suite
15 failures / 7 passes; revised schema/feedback suite 20 failures / 9 passes.
The first version's personal-mute-in-existing-key expectation was replaced
before implementation because it could not represent both selected-Solo
histories. Both logs and baseline sources remain available. The revised suite
passes 40 cases through real MixManager/controller/native JSON framing with
synthetic readiness, device construction, worker scheduling and memory socket.

Actual Qt shortcut and registered identity-callback baseline: 5 failures /
3 passes, no setup errors. Ten final cases also cover both saved-Solo histories,
actual empty-roster recovery, changed channel IDs, remote-only rejection,
current local proof, stale queued roster and cancel/missing/corrupt/no-match.
Existing mix-manager test doubles now return a real positive matched-row count
instead of an unconstrained mock. This corrects the fixture contract rather
than accepting arbitrary objects as successful product restoration.

Ten ownership/atomicity cases cover row replacement before helper/enqueue,
retired rows/epochs/Stop before dispatch, later explicit Mute winning, and
complete final state at every native/legacy callback. Review also reproduced
invalid mute values overwriting a suppressed row's personal choice: an explicit
in-memory reconstruction of the reviewed one-line defect failed 4 of 8 cases;
the corrected fallback passes all 8. Three real JSON nonfinite-value cases
retain prior valid fields, skip an invalid identity and still restore finite
rows without partial callback state. No frozen-tip or hosted failure has been
observed at this pre-verification point.

Full frozen-tip local verification and both hosted workflows including four
desktops are required. Their exact tip, counts, outcomes and any failure/retry
history belong in the PR body and coord AFTER; this source document alone
claims no final hosted result. Automated command framing and offscreen cards
do not prove physical sound, routing support, different-home joining, latency,
endurance or installed-laptop feel. These remain NOT RUN. Karen is pending;
Codex self-QA is not independent PASS.

Keep OPEN DRAFT; preserve previous component heads and parked #37/#49. Release
the lease at handoff. No merge/squash/tag/sign/release/Pages/Release Trust/Publish/
deploy/spend/live Cisco/public rendezvous/short codes/automatic capture/other
repo/new Goal. Unsigned 0.27.2 remains Jeff-only. The complete Art/Music goal
remains incomplete; continue the next safe work under its sustained scope.
