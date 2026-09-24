# Show-wave docs: review packet

Marker: `WEBJAM_SHOW_WAVE_FINAL_BUILD_20260924`.

Keep this PR **OPEN DRAFT**. Base is master
`da16749abc9f87cde127e01ea10a7712e5ce7800`, in the sole checkout
`/Users/jeffstory/Documents/webjam`. The PR body records its final exact tip,
local results, and hosted run URLs. A changed tip requires new qualification.

## What Jeff can review

- [Show one-pager](../../../SHOW_ONEPAGER.md): the four-part product story in
  approximately 300 words, including the current CLI handoff boundary.
- [Presenter script](../../../DEMO_SCRIPT.md): an 8:45 target, preparation,
  actual clicks, explicit room/project transitions, bounded fallbacks, and a
  blank receipt for the timed physical rehearsal.
- Entry guides link to those documents and correct the relevant stale routes:
  live Studio reviews completed takes; standalone Music starts from the launch
  File menu. Paint along supports silent local and embedded YouTube sources;
  Conversation is a separate explicit meeting handoff.
- Audience-facing release notes in these entry guides now identify published
  baseline v0.28.3. Superseded v0.28.1 records remain labeled historical.
  No release object, tag, asset, workflow, or runtime behavior is changed.

## Evidence and self-review

Source navigation was checked against the base's launch, conductor, Studio home,
project controller, Paint along, and Logic handoff implementations. Notes are
shown only in the live Music step. The standalone playback controls are
**▶ / ■**; compact room labels and folder selection are explained. Closing
WebJam after Art returns a source-launch Terminal to its prompt before export.

Pre-freeze checks: 166 existing documentation/export tests passed. The real
`tools.logic_handoff --stub` command successfully wrote a fresh pack under the
checkout's ignored `out/show-wave/docs/handoff-smoke/`: four seconds at 48 kHz,
120 BPM, 4/4, a mono and a stereo 24-bit WAV, Type 1 MIDI, and Start/Middle
markers. This is synthetic file-generation evidence, not a fresh Logic import
or a recording of the preceding live band. Final exact-tip results belong in
the PR body, not in an inferred result from these preliminary checks.

Security/ownership review covers commands, local-only receipt data, explicit
meeting and YouTube handoffs, permitted demo media, and avoiding invitations,
credentials, or participant contacts in screenshots and committed evidence.
The script neither requests signing/notarization nor bypasses platform trust.
The empty rehearsal receipt stays empty until actual observations are supplied.

## Honest leftovers for Karen and Bob

- **NOT RUN:** a complete timed rehearsal on the selected app/build. The
  8:45 schedule and 9:30 stop are targets, not measured performance.
- **NOT RUN in this wave:** two-person physical audibility, local device
  playback, a real Art follower watching, external meeting media, fresh Logic
  import/listening/alignment, installed-build feel, and native companion touch
  or Split View. Historical Logic import evidence is linked by the canonical
  handoff guide and is not promoted into a new result.
- The CLI's synthetic example does not export the earlier jam automatically.
  Actual material needs a stopped-source manifest with explicit aligned audio
  and MIDI notes. No new handoff UI, live MIDI capture, or transcription exists.
- Art F1 still falls through to Music help; Music help has stale launch labels
  and describes live Studio too broadly. These are the next bounded Cisco
  polish candidates, outside this documentation draft.
- Older technical/runbook documents outside these entry guides retain dated
  release or local-only Paint along wording. This draft reconciles the show
  entry points and preserves historical evidence; it does not certify the
  entire documentation corpus as current.
- #149 remains a separate open draft at `3af81dd`. Bob's coordination record
  reports MATCH_PASS and Karen MATCH_READY YES for that tip; its automated
  evidence and completed-take layout are not this draft's standalone demo proof.

After the exact tip passes the complete local gate and all 13 normal hosted
jobs, stop for Bob/Karen leftover + security (+UX) review. No merge or
leftover-squash. No publish/tag/Latest until Bob says **FINAL BUILD** after all
MATCHED and Bob + Karen QA. No Pages, signing/notarization, second checkout,
product clone, second WebJam Codex task, or changes to parked #37/#49.
