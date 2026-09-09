# Worth building — a Music invitation names the working way in

Fresh branch `codex/music-lan-invite-guidance` from fetched `origin/master`
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
[BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5594685090)
posted 2026-09-08 21:07 CT. This is independent of pending drafts #96–#108.

Music's ordinary LAN host already builds one complete invitation and supplies
its same-network requirement. The message builder ignores that fact for Music.
Without a meeting it says to open the link; with a meeting it gives no Join
instruction. Protected bearer invitations are intentionally rejected through
process arguments, while explicitly pasting the full message works.

This is a concrete first-minute joining defect. It comes before more room
polish because the guest needs to get into the rehearsal at all. Different-home
joining still needs the separate network decision; this copy does not solve it.

Before: an incomplete or misleading joining instruction, with no LAN scope.
After: **Open WebJam, choose Join, then paste this full invitation.** The
message states **same Wi-Fi or local network** when the host supplies that fact.
The host's copy confirmation says to send the whole message and keep the room
open. Room/song text, the unchanged invitation and optional meeting stay together.

Use the existing message builder and host-copy action. Do not change invitation
parsing, credentials, transport, audio, browser handoff or permissions. Preserve
Art copy, supported v1 entry and native v3 paste guidance without adding a
public or same-network claim to the native owner path.

Acceptance follows real Music host copy into the real LaunchDialog, with and
without an optional meeting. The typed invite must survive, personal meeting
settings must remain, and no meeting or audio starts. Readiness failures must
still prevent copying an unusable invitation. Existing platform/ingress tests
continue to reject bearer process arguments.

This master-based slice preserves the meeting in the copied text; #97's
room-scoped Conversation adoption remains in its existing draft and needs
later declared composition. The combined #108 candidate remains unchanged.
Local/hosted results and exact final source belong in the OPEN DRAFT PRE_KAREN
body and coord AFTER. Physical joining, audio and usability remain NOT RUN;
Karen pending. No merge, signing, release, deploy, spend or live provider action.
