# Worth building — restore the saved Music listening mix

Declared dependency: OPEN DRAFT #105 at
3811d3f0179bc03e90f7490ed8a9469fd8fec607, branch
codex/music-effective-listening-mix. Fresh branch:
codex/music-saved-mix-restore. Fetched master remains
2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5592992664

A musician saves a quiet, muted reference track and a balanced collaborator.
After changing the mix, Load Mix restores those numbers and Mute in memory,
but sends no native gain commands. The native mixer retains the previous loud
levels. The same defect affects Load Mix From and automatic restore after a
proved connection. Explicit Load also leaves existing cards unchanged until
another authenticated roster arrives. Production controller/MixManager/native
JSON serialization reproduces this with a memory socket; physical sound is
NOT RUN.

Before → after: matched saved listening levels and mute/Solo choices reach the
current native mixer, and existing cards immediately show the restored values.
Saving during Solo must preserve both its current effective mute and the
personal mute choices restored when Solo ends. The complete final mix must be
resolved before dispatch, including unmatched channels affected by Solo ending.
An invalid or unrelated saved file must offer recovery instead of claiming
that a mix loaded.

This directly improves rehearsals with or without a track. It outranks more
surface polish because the displayed listening mix currently disagrees with
commands sent to the native engine. It reuses #105's ordered, bounded dispatcher
and owner checks. It does not compose #103/#104 or certify their combined state.

Acceptance covers real default/named/automatic save/load, exclusive Solo,
legacy snapshots, changed IDs with unique names, ambiguous/unknown rows,
current participant/monitor ownership, immediate Qt cards and authenticated
reconnect. Preserve chosen faders, recording/source/device independence,
Art's two-card door and guest-never-seek. New optional saved mute metadata must
remain backward-compatible and contain no new identity or secret.

Keep OPEN DRAFT PRE_KAREN. Exact final local suite and both hosted workflows
including all four desktops are required. Automated commands do not establish
physical audibility, supported different-home joining or latency. Karen and
physical acceptance remain open. Parked #37/#49 and all previous drafts stay
untouched. No merge/tag/sign/release/Pages/Publish/deploy/spend/live Cisco,
public rendezvous, short codes, second engine, automatic capture or new Goal.
