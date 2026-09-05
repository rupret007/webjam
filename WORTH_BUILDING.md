# Worth-Building — Art invitation entry and same-network recovery

2026-09-05 CT. Branch `codex/webjam-art-entry-clarity`, exact base
`origin/master cf311470fadcee1a688f3b675eb6d2ca4094926d`.
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5554784296).
The source audit began on c18e0b9a; #71 landed while this slice was in progress.
Only this new branch was rebased onto its verified squash on master. This slice
keeps its room overview, personal-context fixes, and native retry generation
as baseline; the new initial-LAN-failure and Leave checks extend only the
invitation/retry path. The #71 branch remains untouched.

**Source/product Worth-Building: PASS.** An artist should be able to follow the
invitation and recover from joining on the wrong network without inventing a
new room. Three connected failures are present in the original master audit and remain
outside #71:

| Guest task | Current source evidence | Required improvement |
| --- | --- | --- |
| Follow the host's ordinary Art invitation | `build_invite_message` omits the local-network prerequisite and only adds explicit Join/paste instructions for v3. The ingress rejects both v2 and v3 bearer URLs from process arguments; a Windows/Linux click is not the supported route. A direct parser probe confirms the same generated message succeeds when explicitly pasted. | Compose accurate whole-message paste instructions from the host's actual route. State same-network requirements only when observed; do not imply native/reference-local invitations provide public rendezvous. |
| Know what to do before Join | The Join page has a masked field but no network guidance. Guests can arrive with default Music preferences and paste an Art invite, so an Art-only hint would miss them. | Reuse the existing Join subtitle/accessible description for conditional network guidance and whole-message paste. Keep one field, one primary action, no automatic parsing or extra profile choice. |
| Recover after the host/network was unavailable | `LanRoomGuest._run` stops its observer after 30 seconds; `RoomParticipantController.lose_lan` retains it, while `start_lan_guest` treats any stored observer as active. Both initial and connected failures demand a fresh invite. Cancelling that dialog paints native one-use failure copy even for LAN. | Offer a guarded retry using the same typed LAN invitation only after confirmed old-worker cleanup. Fresh callback/conductor generations, no overlapping observers, no audio startup before authenticated host profile, and topology-correct cancellation. |

The suspected native missing-profile WAIT override was disproved by the
existing replacement-invitation precedence. Keyboard Enter-on-card behavior is
deferred to keep one coherent invitation/recovery slice.

Required proof: focused invitation/door/controller/observer regressions,
full raw local pytest, Ruff, compileall, pip check, UX smoke, native compact
Join/recovery renders, PRE_KAREN, and exact-tip hosted SUCCESS including all
four desktop builds. Initial source audit is not completion evidence.

Holds: Art Preview; own tools and existing silent local-file Paint along;
optional external conversation/canvas only by explicit action; no Webex on
the Art door, public rendezvous, short-code, default Session Help claim, or
new video stack. Private payloads stay out of diagnostics. #37/#49 remain
parked and #67 untouched. No merge/tag/sign/release/Pages/Release Trust.
Unsigned 0.27.2 Jeff-only; physical/public/installed-package click-feel NOT RUN.
