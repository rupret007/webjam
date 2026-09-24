# Show wave: PR #149 on current master

Marker: `WEBJAM_SHOW_WAVE_FINAL_BUILD_20260924`.

Keep [#149](https://github.com/rupret007/webjam/pull/149) **OPEN DRAFT** for
Bob/Karen. Base: `da16749abc9f87cde127e01ea10a7712e5ce7800`. This is desktop
Studio and Art room polish; the native Art companion has its own required CI
job. The PR body is the final exact-tip ledger, including local results and
hosted run URLs. No ancestor result qualifies a changed tip.

## Rebase and retained failure

Replayed #149's commits after old #148 base
`844ea1080500fc937101b19b21168b5a5525600c` onto current master. #145–#148 and
the offline Logic handoff (#150) are already on master. The changelog conflict
keeps this draft's entry in Unreleased and preserves the earlier release history.
All work used `/Users/jeffstory/Documents/webjam`; no second checkout was created.

Original tip `bffd0cc61930b27cf2e6feec9bea9db9b8ecaffd` failed the same Arrange
geometry assertion in both hosted runs:
[push failure](https://github.com/rupret007/webjam/actions/runs/35817521311),
[PR failure](https://github.com/rupret007/webjam/actions/runs/35817524017).
The final Arrange action extended to x=773 in a 760-pixel Studio. Labels grow
after fades/crossfade are enabled. The original module reported 132 passed and
one failed; an empty retry did not fix it. Normal local font metrics passed,
so the regression also exercises wider glyphs. Failed evidence is retained.

Arrange now measures the available editor width and keeps marker/section
actions and region actions on separate rows when their labels need the room.
The same buttons retain their handlers and keyboard order. The original
edit/save/reload/source-byte assertions remain, with an additional 125%-glyph
case reproducing the overflow before the correction.

The [original PM3 evidence](../manager-demo-studio-art-pm3-20260922/README.md)
remains historical. It is not current-tip proof or physical-device evidence.

Pre-freeze self-QA passed 1,026 Art tests in 15 fresh processes, plus 32 bounded
source/action/privacy/cleanup probes. All 28 Art rendered fixtures fit 760×600
without scrolling. The nine Studio renderer cases also passed. Exact-tip
qualification remains the separate final gate recorded in the PR body.

Current synthetic review views: [compact Studio](studio-compact.png),
[save retry and retained Notes at 22px](studio-retry-22px.png), and
[guest Art activities at 125% glyph width](art-guest-125.png).
The [render manifest](render-manifest.json) binds these images to source hashes;
they are not observations of physical recording or a real remote participant.

## Verification and review boundary

Before pushing, run Ruff, dependency consistency/policy and vulnerability audits,
all seven CI native dependency-lock audits (with CI's existing macOS packaging
exception), compileall, UX smoke, diff check and every tracked application test
module in sorted fresh processes, without retries. Keep results and failures
under `out/show-wave/pr149/<full-tip>/`; report Python/Qt versions and skips.
Run the unchanged Studio/Art fixture renderers into that checkout's `out/`
directory. Their synthetic state does not establish audio, media, device or
two-computer success.

The exact pushed tip must pass all 13 normal hosted jobs in the same workflow:
`test`; both real Jamulus integrations; both Jamulus update-input checks;
Transport; Reference service; Pocket Stage; Art companion; and all four desktop
builds. Conditional signing, notarization, manual certification and publication
jobs remain outside this source-only round. Any red result must be preserved
and diagnosed, not rerun to manufacture green.

Karen should review layout/focus/keyboard reachability, Stop during recording
or export, retained save/retry identity, source-byte preservation, and Art's
own-tools guidance alongside current local-file and YouTube actions. Layout
does not acquire recording, playback, saving, export, meeting or guest-seek
authority. An agent's self-review is not independent Karen PASS.

## Ranked leftovers

1. Independent Karen leftover + security + UX review on the exact green tip;
   Bob's MATCH and Jeff's packaged feel remain pending.
2. Studio's existing external-document conflict recovery needs a workflow that
   preserves both versions. This draft does not change persistence authority.
3. Fine track-lane number/name/status typography can crowd at enlarged text;
   broader track typography remains a separate feel item.
4. Physical two-computer Music/Art, provider, recording/storage, installed
   companion and Logic listening/alignment rehearsal remain separate gates.
   Preserve every unobserved result as **NOT RUN**.
5. Next wave drafts: profile-correct Art Help and the under-ten-minute
   `SHOW_ONEPAGER.md` / `DEMO_SCRIPT.md`. Logic handoff is an explicit offline
   command, not a Studio button or live MIDI capture.

GitHub Latest was verified as v0.28.3 at the start of this round. No version,
tag, release, Latest, Pages, signing or notarization change is authorized here.
Parked #37/#49 stay untouched. After the exact tip is green, stop for Bob/Karen;
do not merge or leftover-squash. A later final build requires Bob's explicit
**FINAL BUILD** after all MATCHED and Bob/Karen QA.
