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

Song tools are Music-only. Podcast & Voice and Review & Rehearsal sessions have
no song form, so the entry is absent there rather than present and inert.

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
- ADR 0007 — Webex Embedded App companion, rejected for this product
