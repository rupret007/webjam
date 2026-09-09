# Worth building — artists can Host on the desktop they have

Fresh independent branch: `codex/art-lan-host-platforms`, from fetched
`origin/master` `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
[BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5595493289),
2026-09-08 22:49 CT; codex lease through 2026-09-09 02:49 CT.

## The observed failure

Both Make together and Paint along disable Host on Windows and Linux, with
“Hosting is available in the macOS app.” This applies Music's platform gate to
ordinary Art even though Art already starts the authenticated Python LAN room
before the Music engine. A sculptor or painter cannot take the first action.
The policy baseline reproduces the barrier and direct-call bypasses:
**23 failed / 10 passed** before production changes.

This beats another copy-only slice: it enables an existing room on the user's
platform. It adds neither a creative tool requirement nor a network service.

## Before and after

Before: Windows/Linux artists can only Join from the Art door. A disabled Host
button also fails to protect a direct or stale programmatic Host submission.
After: supported Windows/Linux desktop Art users can choose either activity
and Host an ordinary LAN room. Current profile, platform and native opt-in are
checked again at submission. Disabled choices explain their restriction in
both visible and accessible text, including after switching Art activities.

Mac hosting remains as before. Non-Mac Music, other profiles and an explicit
native reference-local request remain unavailable; an opt-in never silently
falls back to ordinary LAN. Runtime dispatch and signature checks are unchanged.

## Acceptance and evidence

- Both Art starts accept Host without installing or starting a Music component.
- Profile/start/back changes and failed saves retain the correct next action;
  stale/direct calls cannot grant unavailable hosting or replace a submission.
- Real host settings, room listener, credentials, guest invitation ingestion,
  authenticated state and named-reader presence work together on loopback.
- Missing address or failed bind prevents sharing; Retry can open the real
  listener. Guest Leave releases its worker; End Room closes the owned listener.
- Actual desktop CI runtimes execute the same bounded stdlib unittest before
  packaging, using their existing locked dependencies. OS-selection mocks
  alone are not evidence for Windows or Linux runtime behavior.

Focused policy is **33 passed**. Art's existing UX suite is **43 passed** after
updating two obsolete Windows-host assumptions and retaining denied controls.
Portable, full local and exact-tip hosted results belong in the OPEN DRAFT
PRE_KAREN body and coord AFTER; do not infer final green from this document.

## Scope and remaining work

This is independent master-based work. It does not include unmerged #110
`686aa2b639aea2183ef53d327c3940782d6d21f3` or replace that verified combined
Art/Music candidate. Later composition must preserve its newer behavior.

Real installed-app LAN/firewall use, separate-home joining, lesson/voice levels,
physical Music timing and Karen review remain open. Windows chmod is not proof
of an owner-only ACL. Source-runtime tests do not certify frozen packages or
media. The Art door remains two cards with the squirrel mark; guests never seek.
OPEN DRAFT only; no merge/squash/tag/sign/release/Pages/Publish/deploy/spend/live
Cisco, automatic media/capture, unsolicited send, short codes/public rendezvous,
second engine, other repo or second goal. Parked #37/#49 untouched.
