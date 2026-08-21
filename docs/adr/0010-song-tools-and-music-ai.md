# ADR 0010: Song tools in the jam, and the Music AI developer API

- Status: Accepted
- Date: 2026-08-21
- Scope: Songwriting help and Music AI jobs on the live Music session surface

## Context

Musicians already write their song into the session notes — the canvas has
invited "chord progressions / lyrics" there since the notes surface shipped.
Nothing read that text, so WebJam knew the band was working on a song and could
not say anything about it.

Separately, Moises is a tool this product's users already pay for. Its developer
platform is Music AI (`https://api.music.ai/v1`), which is a different account
system from the consumer Moises app: the credential is an API key created at
`music.ai/dash` and sent verbatim in the `Authorization` header, with no
`Bearer` prefix. A Moises app password is not a credential here.

The wrong shape for both of these is a side tool. A musician mid-jam who wants
chords for the bridge, or wants to hear the drums on their own, should not leave
the session to get it.

## What already exists, and what to take from it

The patterns below are borrowed deliberately. None of these products is cloned,
and none is a dependency.

| Product | What it proves | What WebJam takes |
|---|---|---|
| **Moises** (consumer) / **Music AI** (developer) | Stems, chords, lyrics, sections, pitch/tempo on the song you're learning | The verb set, and the actual API. `api.music.ai` is the developer platform; the consumer app login is not a key |
| **Chordify** | Detected chords read well when overlaid on the track you're playing | Chords displayed against the session's own form. No public API exists and none is scraped — the chords come from a Music AI job on a file the user chose |
| **Hookpad Aria** (Hooktheory) | The strongest write-help operates on a *selected region of a song you already have*, in context | `suggest_chords(section_name=...)` scores against the parts on either side and explains the seam. Aria is powered by a symbolic model; WebJam's version is deterministic theory, which is a real difference in capability and is labelled as suggestion either way |
| **Tonaly**, **Song Cage** | Theory-aware next-chord suggestions land better when each one is explained, and can run on-device | `suggest_next_chords` ships the reasoning with every candidate, and runs locally |
| **BandLab SongStarter** | Generating a fresh sketch from genre/mood is a different product | **Deliberately not copied.** There is no genre or mood input anywhere, and no cold-start generator. Help requires existing material, asserted by test |
| **Endlesss** | People do jam together live | Confirms the jam, not the AI model |

Nobody ships Moises-class song tools plus section-aware write help *inside a
live Jamulus jam*. That integration is the product, which is why every result
lands on the session surface rather than in a separate window.

## Decision

### Song tools live on the live session surface

One entry (More → Song Tools) opens a compact panel that is a fixed column in
the conductor body — not a window, not a dialog. WebJam is expected to sit in
the narrow pane beside a free Webex window (ADR 0004,
`webjam_qt.controllers.window_layout`), so the panel never calls `raise_`,
`activateWindow`, `exec`, or `setFocus`, and only ever appears from a
musician's own click. Music launch is unchanged: Host / Join / New Music
Project, with no fourth start card.

Song tools are Music-only. Podcast & Voice, Review & Rehearsal, and Studio
Visit have no song form, so the entry is absent there rather than present and
inert.

### The panel explains; the HUD acts (ADR 0002)

ADR 0002 makes the HUD the only actionable primary control, and Canvas and
Studio explain the same next action without adding a competing button. The
Song panel obeys that literally:

- It carries **no button whose label is a HUD primary action**, asserted by
  parsing the widget's `QPushButton` labels against that set. An earlier
  revision had a "Copy one invite" button; `Copy Invite` is a HUD primary
  action, so it is gone and the panel now says where the action lives.
- The meeting mute handoff belongs to Conversation, so the Meeting page has
  **no buttons at all** — it states both mutes, says which app owns which, and
  points at Conversation.
- Nothing in the coordinator touches conductor phase, the session HUD,
  musician guidance, the session pulse, or recording, asserted by test.
- The 250 ms repaint tick updates the position line only. It never rebuilds
  guidance, asserted against the parsed call graph of `_on_tick`.

Song tools are creative and opt-in throughout: the panel never opens itself,
and a suggestion is text until a musician acts on it.

### Coexisting with the meeting window (ADR 0004)

Jamulus carries performance audio, Shared Track is the song clock, and the
meeting is optional faces in its own application. Song tools sit entirely on
the WebJam side of that line:

- Nothing embeds a meeting, taps its output, or captures it. No WebEngine, no
  new OAuth, no screen share — asserted across every song module.
- No stem, chord, lyric, or clock value is ever sent into a meeting.
  `core/music_ai_results.py` contains no meeting concept at all.
- Opening the panel, running a job, and asking for write-help touch none of
  the meeting handoff. Join / Open Meeting, Show Webex App, and the
  Conversation panel keep working exactly as before, asserted at runtime by
  driving each path against a mocked meeting surface and checking it was never
  called.
- A job in flight is one word on the session strip, a `QLabel` that can never
  cover or disable Conversation. The missing-key line lives in the panel.
- The only upload sources that exist are a file the user picked and the Shared
  Track they loaded. A meeting recording or system capture is not one of them,
  and the live Jamulus mix is a named permanent refusal.

**Webex is primary in copy**, and other services stay valid. The service name
comes from the configured link through `core.meeting_link`, so a Zoom user
reads "Zoom" and a Teams user reads "Microsoft Teams"; with no link, or an
unusable one, copy says Webex. An unbranded public host is named by its own
hostname, because "Meeting service mute" reads like a placeholder. The invite
carries any link `core.meeting_link` accepts rather than re-implementing a
Webex-only policy — an earlier revision used the Webex-only validator and would
have silently dropped a valid Zoom or Meet link.

No copy anywhere claims WebJam joined, muted, or verified a meeting.

### The rules that are easy to leave out

Each of these is a place where a plausible implementation would have been
wrong, so each is pinned by a test rather than by intent.

- **Two mutes stay two.** A stem chip is one stem of a reference file. It never
  reaches a Jamulus channel or a meeting, it is named
  "Mute the Vocals stem of the reference file", and the page says musicians and
  the meeting are unaffected.
- **Ending the meeting does not end the jam**, and a Music AI job that finishes
  afterwards still lands locally, because a result is a file on disk and a
  meeting has nothing to do with it.
- **One invite carries the binding.** When the room has chosen a song, the
  invite names it — key, tempo, and shape — so a joiner arrives knowing what
  they are playing instead of meeting a second picker. No path travels in an
  invite, and a guest is never shown a file dialog at all.
- **Late join starts where the room is.** A playing Shared Track starts the
  repaint by itself, because nobody pressed start on the joiner's machine and
  an overlay frozen at bar one is worse than none. A stopped clock announces
  nothing rather than claiming 0:00.
- **Refusals come before dialogs.** Missing key, wrong role, or an unavailable
  verb are answered before any picker opens. Being shown a file chooser and
  then told no is worse than being told no.
- **The confirmation names the file.** "This uploads the Shared Track file you
  already chose to Music AI", then the file, size, and tool, then the
  boundaries: never the live jam, never a meeting or its recording.
- **A meeting recording is not a WebJam take.** Different applications,
  different audio, stated on the meeting page.
- **No chat mirror, no rename.** The song sheet goes to Jamulus band chat, the
  path a session already has. Nothing is posted to a meeting and no musician
  name is ever set.
- **No device is re-routed.** Picking a file or running a job touches no audio
  device, so neither Jamulus nor the meeting moves.
- **Readable at jam distance.** The chord the room is on is drawn large and
  alone above everything else, with the next chord beside it, and only while
  the position is actually known — a big chord that is a guess would be the
  most confident wrong thing on the screen.
- **Sleep does not restart work.** When WebJam stops waiting for a job it says
  so ("Split stems — still at Music AI") and never runs it again, because a
  blind retry spends the account's credits twice for one answer.

### Suggestions are kept, not applied

Every suggested progression is drawn with the word **Suggestion**, its
reasoning, and one **Keep**. Keep writes that progression under its part in the
musician's own notes — never into the Studio arrangement, and never
automatically. **Dismiss** clears the panel and undoes nothing, because nothing
was done. A refusal (no key yet) is shown in the same place with nothing to
keep.

### Quiet by construction

While a Song tools job runs, the session strip shows one word — "Chords & key…"
— and nothing blocks or covers the jam. That same line shows the current part
and its chords while the clock runs, and it **survives closing the panel**, so
an overlay a musician turned on stays on when they go back to the jam. It is a
label, never a control, so it cannot compete with the HUD.

Running a tool takes exactly one dialog: the host confirmation, which names the
file. When the session already holds a Shared Track that is the subject, and no
second "which file?" box appears.

A missing key is one sentence naming `music.ai/dash` and `MUSIC_AI_API_KEY`,
shown inside the panel. It never reaches the HUD: an absent optional credential
is not the session's next action.

### One section vocabulary, two honest representations

Master already describes song parts twice, and the two are genuinely
different moments rather than a duplication to be collapsed:

* **In the jam** no `StudioDocument` exists at all — `RecordingStudio` runs
  with `_studio_state = None` while the band plays — so the only form that can
  exist is the one musicians typed into the notes.
* **In Studio** a part is a `StudioMarker` with `MarkerKind.SECTION`: a frame
  range on a take, which is what `core.studio_sections.reorder_section`
  permutes.

Two representations is unavoidable. Two *vocabularies* is not.
`core.song_sections` is the single list of role names and aliases; the live
parser imports `normalize_role` from it rather than keeping a copy, and
`section_markers_from_form` carries the form the room played onto a take as
real, contiguous `MarkerKind.SECTION` markers — clipped to the take rather than
running past its end. `form_labels_from_markers` reads the other direction. So
a band jams, writes the form, and finds that arrangement already marked up in
take review instead of retyping it.

Note that no fixed Intro/Verse/Chorus vocabulary existed on master to adopt:
Studio labels are free text, with `"Section N"` and a flat `"Verse"` as dialog
defaults. `next_section_label` gives Studio a better default drawn from the
same list.

`core/song_sections.py` imports `core.studio_project` lazily inside the one
function that needs it, so a live session never loads the Studio document model
to describe its own form.

### Facts and suggestions are distinguishable

`core.song_form` reads the notes into a key, tempo, sections, chords, and lines.
Every fact carries its origin — stated by a musician, or detected by a named
Music AI job — and a field WebJam does not know stays empty rather than
becoming a plausible default. A stated key wins over a detected one, because a
musician writing `Key: G major` is deciding, not predicting.

`core.song_help` produces suggestions, labelled as such. Chord progressions are
stored as scale degrees and rendered into the song's key; when no key is stated
or detected, one is read off the written chords and reported as an assumption.
A progression the song already uses elsewhere is demoted, so "a progression for
a different part" actually differs.

Help is always asked for **one part** of the song. A named part is a region to
rewrite; with no selection, the next part the song is missing is answered
instead. Either way the suggestion is scored against the parts on both sides —
a progression that opens on the chord the previous part ends on is ranked down,
and one that ends on the dominant before a part that starts on the tonic is
ranked up — and the reason for that placement is shown. `suggest_next_chords`
answers the narrower "we're on this, what comes next" from the chord a part
currently ends on, using ordinary functional harmony with the explanation
attached.

The song's form, with each part's chords and a separate row for any chord run a
Music AI job heard on a file, is rendered over the session. Detected chords are
never spread across the written parts: the API returns a chord list for a whole
file, and claiming which chords belong to which part would be an alignment
WebJam has not been given.

### Songwriting help never leaves the computer

Structure, next-section, arrangement, rhyme, and chord help are computed
locally. No audio and no text is sent anywhere to produce them. This is a
boundary, not a gap: asking "what could the bridge do?" must not stream the band
to a third party. If a cloud text completion is ever added, it must be an
explicit action on text the musician typed, never on audio, and never implicit.

### Music AI capabilities are discovered, never assumed

Workflow slugs belong to the account. The API reference's own example shows a
beat-and-BPM workflow under the slug `untitled-workflow-e78c2e`, so a hardcoded
slug list would be a guess presented as a feature. `GET /workflow` is read at
runtime and matched onto product verbs (Split stems, Chords & key, Lyrics,
Sections, Change key/tempo, Master, Clean up audio).

A verb with no matching workflow is **unsupported**: it is not drawn as a
button, and it is named in the panel with the reason. It is never stubbed to
report success. The single slug named without asking is
`music-ai/stems-vocals-accompaniment`, which the quick start documents as
available to every account; it is labelled as the shared template.

Moises features with no developer-API module — the song library, live
separation during playback, the app's practice tools, Moises account sign-in —
are listed as unsupported with why. The API exposes uploads, jobs, workflows,
and the application behind the key, and nothing else.

### Uploads are chosen, host-confirmed, and never the live jam

`core.song_workbench.evaluate_upload` owns the rules so they hold regardless of
which UI is wired to them:

- no API key, no attempt, with copy naming `music.ai/dash`;
- only the host may send a file, matching the Shared Track rule;
- the file must be one the user chose — there is no code path that discovers
  one;
- the live Jamulus mix is a named, always-rejected source;
- an allowed upload still returns a confirmation naming the file, its size, and
  the tool before anything leaves the machine.

Jobs run on a worker thread; a five-minute stem separation cannot freeze a jam.
Results are downloaded beside the session so the panel shows stems, chords,
lyrics, and detected key/tempo in place rather than behind a URL that expires.

### Secrets

`AppSettings.music_ai_api_key`, overridable by `WEBJAM_MUSIC_AI_API_KEY`. The
config file is already written `0o600`, and the field name matches redaction's
existing `api_key` hint, so it stays out of logs and support bundles. No key is
committed; every test uses fakes and no test opens a socket.

## Consequences

A musician can ask for the next section, chords for a part they have not
written, stems, lyrics, or a detected key without leaving the jam. What WebJam
does not know, it says it does not know. What the account cannot run does not
appear as a button.

The cost is that writing help is deterministic music theory rather than a
generative model, and that detection quality is whatever the account's chosen
workflows produce. Both are visible to the musician by design.

## References

- [Music AI API reference](https://music.ai/docs/api/reference/)
- [Music AI file upload](https://music.ai/docs/api/file-upload/)
- [Music AI quick start](https://music.ai/docs/getting-started/quick-start/)
- ADR 0004 — external Webex launch (the second-window boundary this builds on)
- ADR 0007 — Webex Embedded App companion, owned by the companion track. Music
  gates on none of it either way; see ADR 0012 for what Music publishes to a
  companion when one exists
