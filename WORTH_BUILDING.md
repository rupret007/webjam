# Worth-Building — Music Host/Join network recovery

A Music host can copy an invite containing a newly selected Wi-Fi address while
its retained room listener still serves the old address. Guests cannot use that
invite. A running Music guest's Try Again also takes the host invite-refresh
shortcut instead of asking the existing audio supervisor to recover. Both
failures reproduced through the actual controller before implementation.

This slice reuses Art #79's explicit listener recovery pattern. Address loss
hides Copy while retaining the listener; return to the same route restores it.
A changed address offers Try Again and replaces only an idle listener after
confirmed cleanup, then offers Copy New Invite. Audio and the conductor attempt
keep their existing owners. Recording and outstanding take/transfer obligations
must finish through their ordered owner; retry cannot discard them. New takes
and Shared Track playback cannot begin during listener replacement or unresolved
cleanup. Existing Stop Recording remains available.

Guest retries use the existing bounded Bridge supervision. Authenticated profile
discovery must stop its observer before handing off to Music, and late End,
Quit, or a new invitation wins over stale cleanup callbacks. This adds no new
transport, public rendezvous, or cross-network capability claim.

The real rotating log receives fixed network transition and explicit recovery
outcome messages. Repeated polling stays quiet. Invitations, addresses, artist
content, take identities, and private exceptions stay out of diagnostics.

Worth-Building: PASS. Regressions demonstrated the invalid invite, dead guest
action, unconfirmed/reentrant handoff, recording action loss and unsafe new-take
dispatch. Native controller tests cover recovery plus 760×600 Conversation-open
and closed views. Full automated and independent review evidence is recorded in
PRE_KAREN_QA.md; exact-tip hosted checks and artifacts belong in the draft/AFTER.

Canonical checkout `/Users/jeffstory/Documents/WebJam`; new branch
`codex/webjam-finish-product-music-recovery` from exact master
`8d708d568ff20d43c4850b44729f5957226c8e6d` after Bob's #79 squash.
BEFORE [5558440677](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5558440677)
and [addendum 5558444758](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5558444758)
cover this same task, marker `OVERNIGHT_NEXT_MUSIC_RECOVER_20260906_0449`,
through 08:52 CT on 2026-09-06. Local master was fast-forwarded as authorized;
old #79, other existing branches and all four stashes are preserved.

One OPEN DRAFT for Karen, exact-tip hosted green on four desktops, AFTER and
agent:none, then stop for Bob. Parked #37/#49 untouched; stay off #67. Unsigned
0.27.2 is Jeff-only. No merge/tag/sign/Pages/Release Trust/publish/GitHub Latest,
short-code, live providers, or other repos. Art remains Preview; own tools,
silent local Paint along and Webex beside WebJam keep their existing boundaries.
Physical/two-device/installed-package gates remain NOT RUN.
