# Worth building — one invitation carries Conversation

Base: fetched `origin/master` at `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
Branch: `codex/invitation-conversation-context`.
Canonical WebJam checkout only. Independent of pending #96; parked #37/#49 untouched.

A host already copies one invitation containing the WebJam room and optional
meeting. Join previously discarded the labeled meeting block. A clean guest had
to paste it again; a guest with a saved meeting could open that unrelated meeting.
This directly broke the one-invitation promise before either Art or Music could
be useful together. It takes priority over more Paint along copy or Music controls.

After this change, an accepted complete paste retains one validated conversation
link for the joined room only. Conversation display, Copy, Open, error Retry and
Band Check use that same context. Joining does not launch a meeting. An invitation
without a meeting does not select the guest's saved personal meeting.

Add Link / Change Link in a joined room updates temporary room context; personal
settings remain untouched. Successful Leave restores the personal meeting. Failed
cleanup retains the current room's link until retry succeeds. Replacing a room
invalidates old meeting handoffs and retries even when both use the same URL.

The transport invite models and wire formats are unchanged. Optional clipboard
context is untrusted input, never authenticated host identity. Existing HTTPS
validation, explicit external handoff, private-input limits and ambiguity checks
remain the trust boundary. No new network service, automatic opening, recording,
media capture, download or playback control is introduced.

The two-session goal remains incomplete. Different-home Art reachability still
needs an approved private connectivity design and physical proof. YouTube lesson
sharing/voices, independent listening levels and Music latency also need their
own work and two-person evidence. Track these in
[the two-session proof plan](docs/plans/webjam-two-session-proof.md).
