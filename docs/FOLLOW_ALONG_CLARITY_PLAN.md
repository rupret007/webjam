# Follow-along clarity and continuity

This round builds on verified `4a48ed5` and draft #174. The initial inspection
on 2026-10-03 found a clean worktree, unchanged draft, two completed successful
CI runs and no remaining build/test process. Work continues in one coding lane
on `codex/follow-along-clarity-20261003`. Jeff retains Latest, feel, tags,
merges and releases. No Barker/Wildflower work is included.

## Assessment and priorities

1. **Make the audible lesson easy to find.** The empty Paint Along surface
   currently emphasizes its silent player and puts Watch a shared lesson below
   the player controls. Promote that existing route at first entry for hosts
   and guests, while clearly retaining the silent-reference alternative.
   Replace dense setup prose with ordered steps and accessible sound/sharing
   details. Correct the unconditional "Jamulus remains live" instruction.
2. **Preserve useful intent without retaining stale authority.** Isolated
   controller probes reproduced inert Music lesson buttons after the first
   Session Library creates a workspace and after initial host credentials
   become available. Ordinary Conversation navigation also discards practice
   guidance and restores the generic mute prompt. Preserve the chosen activity
   through ordinary navigation, adopt the first workspace through its owner,
   and keep actual room/profile/role replacement and stale actions rejected.
3. **Save and explicitly reuse lessons.** Art already saves references, but
   reopening one only opens its browser URL. Add a deliberate route back into
   shared-lesson setup. Music currently retains its link only in memory; add
   an optional canonical YouTube reference to an existing rehearsal song,
   using the existing editor/save authority. Preserve absent fields in old
   plans, templates, backups, drafts and conflict handling. Loading or
   selecting saved work must never launch external media.
4. **Recover without a navigation puzzle.** Changing a meeting link retires
   Art pause-request authority correctly but requires leaving/re-entering to
   restore it. Offer an explicit restart through the existing request owner.
   Keep native-room voice/chat fallback and old-context rejection. Distinguish
   unavailable YouTube videos from embedding refusal and show the existing
   browser route without automatically opening it.
5. **Keep instructions and evidence consistent.** Update the default Help
   overview as well as searchable topics, README, actual-click guides and the
   changelog. Verify real composed compact/enlarged-text views, rather than
   relying only on a tall isolated card fixture.

These are changes to existing workflows and local reference metadata. There is
no new conferencing engine, audio capture/routing path, service or YouTube
extraction. Webex owns cameras, voices and shared browser sound. Guided Music
practice uses turn-taking; ensemble playing retains Jamulus and the supported
Shared Track route. Listening mute never means outgoing-instrument mute.

## Verification and delivery

Retain baseline reproductions under `build/follow-along-clarity`. Add behavioral
regressions, test stale callbacks and real navigation, inspect native layouts
and review independently. Run affected modules first, then repository-required
final source, integration and desktop gates at an unchanged commit, without
retries that conceal failures. Deliver a verified Mac test build with its exact
source and package identity, instructions and a draft PR. Cleanup follows
verified replacement and preserves source, environments, media, useful evidence
and a recoverable fallback.

The existing [two-person pilot](PAINT_PLAY_ALONG_PLAN.md#two-person-acceptance)
remains the physical acceptance procedure. Check sound, both voices, faces,
pause/resume, reconnects, one audible lesson and ensemble routing/latency when
people and devices are available. These observations start **NOT RUN**;
controlled fixtures, screenshots and packaged smoke tests do not prove them.

## Implementation checkpoint

The draft now promotes the audible entry, separates three setup steps from
expandable sound tips, and scrolls compact/enlarged content to reveal focused
actions. It preserves navigation and first-save intent, including a meeting
edit while visiting Notes, while replacing action generations. Music song
references and Art saved references/bookmarks explicitly return to setup
through existing Library draft/save owners. Supported LAN Art rooms offer an
explicit request restart; provider errors explain the browser fallback.

Independent reviews found and led to repairs for a suspended meeting edit,
an initial enlarged role badge, modal saved-link actions and a chooser result
aimed at a replaced plan with the same song ID. Launch-time Library actions
now explain Continue first when no live lesson setup exists. Regression coverage includes
these cases, disk-failure recovery, older backup shape, retained song drafts,
bookmark ownership, cold-joined Music and real composed keyboard navigation.
Old recovery-layout assertions now measure scroll content and focused actions
inside the viewport while retaining the no-playback/transport-change checks.

Focused source checks and native layout/recovery checks precede the final
commit gates. Final full-source, desktop CI, package verification, build
handoff and replacement cleanup remain pending at this checkpoint. The
identified build handoff will record those results without upgrading the
physical observations above.
