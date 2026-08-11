# WebJam v0.23.0 Shared Track and recording physical test checklist

> **Current status:** every physical row in this document is **NOT RUN**.
> The immutable v0.23.0 private test candidate is now a historical release; it
> was GitHub Latest when published. Supersession does not change this ledger's
> status. Record the selected package names, checksums, and physical test
> environment below before changing any physical result.

Use this ledger for physical multi-machine, Jamulus, BlackHole, JACK, hardware,
recording, and Studio validation. Automated tests are prerequisite evidence,
not substitutes for these observations.

## Result rules

Use only these statuses:

- **NOT RUN** — nobody performed the exact step against the recorded artifact.
- **PASS** — the expected result was directly observed and its evidence is
  linked.
- **FAIL** — the observed result violated the expectation; preserve the
  recoverable media and attach a sanitized issue reference.
- **BLOCKED** — the exact test was attempted but an external prerequisite was
  unavailable. A missing machine, interface, account, route, or signed package
  is **BLOCKED**, never PASS. A step that was not attempted stays **NOT RUN**.

Never infer PASS from CI, a simulated device, a moving meter, a roster row, a
waveform, a successful RPC call, a produced WAV, or an older release result.
Do not include invitations, meeting links, credentials, local paths, device
UIDs, raw exceptions, or private participant names in public evidence.

## Exact candidate identity

Complete this before changing any row from **NOT RUN**:

| Evidence field | Value |
| --- | --- |
| Candidate version | `0.23.0` |
| Git commit | `416186a3ea9cddc1ff01a2b0d61f5e1d5dfc70c8` |
| Build/CI run ID | Tag CI `31368570400`; protected promotion `31371289158` |
| GitHub release | Immutable historical release `367773776`, tag `v0.23.0`; it was Latest when published |
| Host asset filename | **NOT RUN — exact v0.23.0 asset not selected for physical test** |
| Host asset SHA-256 | **NOT RUN — not recorded** |
| Guest A asset filename / SHA-256 | **NOT RUN — not recorded** |
| Guest B asset filename / SHA-256 | **NOT RUN — not recorded** |
| Jamulus client/server versions and provenance | **NOT RUN — not recorded** |
| Shared Track component identity | **NOT RUN — not recorded** |
| Test date/time zone | **NOT RUN — not recorded** |
| Test lead and musician aliases | **NOT RUN — not recorded** |
| Sanitized evidence folder / issue | **NOT RUN — not recorded** |

If any binary, commit, dependency lock, component catalog, or package hash
changes, start a new ledger. Do not carry PASS forward.

## Required lab topology

### Primary two- or three-musician topology

- Host Mac: supported macOS, physical low-latency interface, wired headphones,
  official unambiguous BlackHole 16ch or 64ch at 48 kHz, Ethernet where
  practical, and a locally stored test song.
- Guest A: a second physical Mac with its own interface and wired headphones.
- Guest B, recommended: Ubuntu 22.04 x64 with the exact candidate ZIP, native
  Jamulus, JACK, a separate interface, and wired headphones.
- Optional observer/recorder: a third musician or measurement system used only
  for listening and timing evidence; it must not become an unproved music path.
- Every physical interface's direct-monitor path must be documented as on/off.
  Shared Track isolation tests require direct monitoring to be off or otherwise
  conclusively separated.
- Use a purpose-made non-copyright test source containing a leading sync pulse,
  spoken section IDs, sustained stereo material, silence, transients, and a
  trailing pulse. Retain its SHA-256 privately with the evidence.

| Topology prerequisite | Status |
| --- | --- |
| Two physical machines and two musicians available | **NOT RUN** |
| Third Linux/JACK guest available | **NOT RUN** |
| Exact candidate hashes verified on every machine | **NOT RUN** |
| Wired headphones connected on every endpoint | **NOT RUN** |
| Physical interface and channel mapping checked in Jamulus | **NOT RUN** |
| Host BlackHole 16ch/64ch identity and 48-kHz format checked | **NOT RUN** |
| Direct-monitor state recorded and safe | **NOT RUN** |
| Test source identity, channel count, rate, duration, and hash recorded | **NOT RUN** |
| Free disk space and private takes folder checked | **NOT RUN** |
| Rollback package retained before installation | **NOT RUN** |

## A. Installation and clean-start boundary

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| A01 | Verify each downloaded asset against its v0.23.0 checksum manifest before launch. | **NOT RUN** |
| A02 | Install or extract on a clean account. Record SmartScreen/Gatekeeper/quarantine behavior without bypassing policy silently. | **NOT RUN** |
| A03 | Launch with no stored WebJam session. Host and Join remain the only initial live-session choices. | **NOT RUN** |
| A04 | On each Mac, complete native Jamulus device/channel/headphone/buffer setup without an Other Application Data permission prompt. | **NOT RUN** |
| A05 | Quit and relaunch. The dedicated Jamulus profile returns without changing the musician's ordinary `Jamulus.ini`. | **NOT RUN** |
| A06 | Confirm no v0.23.0 UI or About text claims that the candidate is published, signed, notarized, or physically certified. | **NOT RUN** |

## B. Live multi-machine music baseline

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| B01 | Host starts the private server; Guest A joins from one invitation; Guest B joins when available. No bearer remains visible after ingress. | **NOT RUN** |
| B02 | Each musician plays alone. Every other musician hears the correct source with acceptable low-latency Jamulus behavior and no Webex duplication. | **NOT RUN** |
| B03 | Each musician adjusts the other participants in native Jamulus. Mix changes remain local to that musician. | **NOT RUN** |
| B04 | Run Band Check after the session is already live. It observes without restarting or reconfiguring Jamulus. | **NOT RUN** |
| B05 | Verify WebJam never calls authenticated connection, roster presence, or meter motion proof of audibility. | **NOT RUN** |

## C. Shared Track source and live-session UX

Run each supported format using files with known hashes. Picker and drop results
must match.

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| C01 | Use **Add Shared Track** with a valid WAV; loading does not start playback. Name, duration, progressive waveform, and stopped state are correct and reveal no folder path. | **NOT RUN** |
| C02 | Repeat C01 by dropping the same WAV on the live-session surface. State and validation match the picker path. | **NOT RUN** |
| C03 | Repeat with AIFF and FLAC. | **NOT RUN** |
| C04 | If the exact package advertises MP3, load real LAME and ffmpeg/Lavc/Lavf files plus a permitted trailing APE tag; confirm duration and bounded waveform. If MP3 is not advertised, record **BLOCKED**, not PASS. | **NOT RUN** |
| C05 | Try unsupported, renamed, malformed, truncated, oversized, symlinked, changing-during-load, multi-file, and remote-URL payloads. Each refuses safely with a path-free reason and no playback. | **NOT RUN** |
| C06 | Resize at 720×560, 760×600, 1024×768, and 1440×900. Compact waveform, source/readiness state, Record Session, invite, and End/Leave remain legible and reachable. | **NOT RUN** |
| C07 | Keyboard and screen-reader navigation reaches Add/Open, transport, route recheck, loop/count-in, Replace, Remove, Record Session, Studio, and End/Leave in logical order. | **NOT RUN** |
| C08 | While stopped, **Replace…** loads a second source without exposing the first path. **Remove** returns to the empty state without deleting either imported file. | **NOT RUN** |
| C09 | During playing, paused route ownership, stopping, or cleanup pending, attempted replacement/removal is visibly refused. The active generation is not silently torn down. | **NOT RUN** |

## D. Shared Track route, listening, and control

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| D01 | With BlackHole absent or wrong, Play is unavailable and Recheck Route does not start audio. | **NOT RUN** |
| D02 | With one official unambiguous BlackHole 16ch/64ch route at 48 kHz, verify readiness then press Play. Exact primary/backing ownership is proved before sound starts. | **NOT RUN** |
| D03 | Host hears the Shared Track only through the normal Jamulus return, not a local direct-monitor duplicate. Muting the `WebJam Track` participant removes it from the host's headphones. | **NOT RUN** |
| D04 | Guest A hears the same Shared Track through Jamulus. Muting or changing `WebJam Track` in Guest A's Jamulus mix affects Guest A only. | **NOT RUN** |
| D05 | Guest B repeats D04 through the Linux/JACK endpoint. | **NOT RUN** |
| D06 | Guest WebJam receives authenticated, bounded, path-free Shared Track state with no transport controls or audibility field. A legacy peer may show channel presence only; neither view claims sample synchronization, audibility, isolation, or health. | **NOT RUN** |
| D07 | Exercise Play, Pause, paused seek, Resume, Stop, Restart, loop in/out, source trim, and a visible/audible count-in. Waveform playhead and text remain consistent without rapid accessibility chatter. | **NOT RUN** |
| D08 | Set a short loop around transient material and a long loop across silence. All endpoints hear the expected musical range with no extra local copy. | **NOT RUN** |
| D09 | Force decoder starvation or equivalent controlled underrun. Emitted silence is accompanied by an honest dropout warning and bounded counter. | **NOT RUN** |
| D10 | Remove or invalidate route proof during playback. Audio silences promptly, stale state cannot authorize restart, the primary musician session survives, and recovery is explicit. | **NOT RUN** |
| D11 | Restore the valid route, use Recheck Route, and restart without restarting WebJam or the primary jam. | **NOT RUN** |
| D12 | Force owned backing-process teardown failure. Cleanup pending stays visible; Stop retries; source replacement, End, and quit do not report clean completion early. | **NOT RUN** |
| D13 | Race Play/Stop, Replace/Stop, End Jam/Play, reconnect/play, and quit/cleanup. No orphan `WebJam Track`, stale audio, leaked route lease, or externally owned process termination remains. | **NOT RUN** |

## E. Record Session lifecycle and count-in

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| E01 | With no Shared Track, choose **Record Session** and **Record Shared Jam Only**. Observe Preparing → Recording; no Local Original appears. | **NOT RUN** |
| E02 | With a ready Shared Track and count-in enabled, choose Record Session. Observe Preparing → Count-in → Recording; track playback begins only for the confirmed recording generation. | **NOT RUN** |
| E03 | During Recording, both guests see truthful bounded recording state but cannot control the host recorder. Stop moves guest state to Finalizing before the later Ready/attention result. | **NOT RUN** |
| E04 | Press Record Session twice during Preparing/Recording. Only one recorder generation and take exist. | **NOT RUN** |
| E05 | Press **Stop Recording** once. Observe Stopping → Finalizing → Ready; Studio is not presented as ready during stopping/finalization. | **NOT RUN** |
| E06 | Press Stop repeatedly and race Stop with End Jam. Cleanup/finalization remain idempotent and the server is not stopped before required recording work settles. | **NOT RUN** |
| E07 | Start another recording only after Ready. Take folders, manifests, IDs, and Shared Track source identities do not collide. | **NOT RUN** |

## F. Authoritative multitrack identity and alignment

Use at least two musicians plus the Shared Track. Create spoken musician IDs
and claps against the test source's sync pulses; do not put private real names
in public evidence.

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| F01 | Final take contains one authoritative isolated server stem per eligible musician and one distinct Shared Track stem—never several copies of a stereo mix. | **NOT RUN** |
| F02 | Each Studio row has the correct bounded musician label/type exactly once; Shared Track is typed/named distinctly. | **NOT RUN** |
| F03 | Compare leading/trailing pulses and claps across raw stems, manifest timing, and Studio playhead. Record measured offsets; no media is silently shifted without evidence. | **NOT RUN** |
| F04 | Join a musician during recording. The new segment/identity is explicit; existing stems are not renamed or reordered into another musician. | **NOT RUN** |
| F05 | Leave and reconnect one musician. Stable identity and segment boundaries survive roster refresh/reorder without guessing. | **NOT RUN** |
| F06 | Use duplicate or ambiguous Jamulus profiles/recorder filenames. Matching fails closed or marks the source ineligible; it never assigns audio to the wrong musician. | **NOT RUN** |
| F07 | Record a second take with the same Shared Track. Its stable source identity supports take-lane matching without confusing it with a musician or take-local filename. | **NOT RUN** |
| F08 | Change the Shared Track between stopped takes. The new source receives a distinct identity; Studio does not silently comp it as the old song. | **NOT RUN** |

## G. Local Originals and guest delivery

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| G01 | Choose shared-only recording. No local-capture device opens and no Local Original track is invented. | **NOT RUN** |
| G02 | Choose **Also Keep This Mac’s Inputs**, select an eligible two-channel host interface, and record. Host Local Original is preserved with explicit identity and timing evidence. | **NOT RUN** |
| G03 | Opt Guest A into Local Originals after valid setup. Verify durable transfer, checksum, and receipt without delaying initial join. | **NOT RUN** |
| G04 | Leave Guest B opted out. No Guest B Local Original obligation or empty track appears. | **NOT RUN** |
| G05 | Interrupt guest transfer after capture. Audio remains preserved/recoverable; host Studio shows waiting or missing state and cannot claim complete aligned export. | **NOT RUN** |
| G06 | Resume transfer and verify the exact bytes. Alignment becomes eligible only when the intact matching server reference and strict timing evidence both pass. | **NOT RUN** |
| G07 | Remove/change the matching server reference or introduce capture gaps. Local Original remains reviewable but unverified; manual nudge alone cannot create verified alignment. | **NOT RUN** |

## H. Recording faults, gaps, and recovery

Use a disposable, bounded test volume for destructive fault injection. Never
target a home directory, production takes folder, or unrelated media.

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| H01 | Inject dropped capture blocks. Manifest and Studio expose explicit silence gaps/dropout evidence; duration is not shortened silently. | **NOT RUN** |
| H02 | Exhaust the disposable volume during capture/finalization. Existing audio is retained in private staging/recovery, Ready is refused, and the user gets one safe next action. | **NOT RUN** |
| H03 | Inject a write, fsync, checksum, rename, or manifest-publication failure. No partial folder is presented as a complete take; last-known-good data stays recoverable. | **NOT RUN** |
| H04 | Retry stop/finalization after the recoverable cause is removed. One take publishes atomically with checksums, or Needs attention remains truthful. | **NOT RUN** |
| H05 | Terminate WebJam during active capture, stopping, and finalizing in separate runs. Relaunch recovery preserves available audio and never manufactures completion. | **NOT RUN** |
| H06 | Corrupt or remove one required staged/server stem before publication. Validation refuses Ready and identifies the bounded media problem without a private path. | **NOT RUN** |
| H07 | Force Shared Track cleanup failure after recorder stop. Recording success remains blocked/qualified until route cleanup is resolved; no backing process is orphaned. | **NOT RUN** |
| H08 | Force recorder stop failure while Shared Track cleans up. The route can retire without claiming a completed take; retry remains available. | **NOT RUN** |

## I. Studio continuation and musician UX

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| I01 | From Ready, open the completed take naturally in Studio. Selection, playhead, and live-return context are preserved. | **NOT RUN** |
| I02 | Track list contains every authoritative musician once, one distinct Shared Track, and only evidenced Local Originals. Type/color/name controls are legible and accessible. | **NOT RUN** |
| I03 | Choose a physical Studio output. Aligned waveform playback matches raw-stem sync measurements; Shared Track and musician faders/mute/solo/pan act independently. | **NOT RUN** |
| I04 | In completed-take Studio, exercise play/stop, cycle, time/position, zoom/scroll, time ruler, playhead, snap, loop range, and persistent selection. In a separate Reference Studio song project, also exercise count-in, metronome, tempo, and bar/beat ruler; do not report those controls as completed-take Studio features. | **NOT RUN** |
| I05 | In completed-take Studio, exercise its selected-region delete, duplicate, move, trim, fade, and crossfade actions. In a separate Reference Studio song project, multi-select regions and exercise one-step Select All/Cut/Copy/Paste/Delete. Undo/Redo restores exact choices without changing source hashes. | **NOT RUN** |
| I06 | Add markers and named sections; move a section across all tracks as one undoable ripple edit. Unsafe seam crossings fail atomically. | **NOT RUN** |
| I07 | Add a safely matching repeated take, audition its lane, comp a range, and verify non-overlapping deterministic comp boundaries. | **NOT RUN** |
| I08 | Exercise completed-take Studio's track/mixer controls, master output, and limiter. Exercise sends/effects only in the Reference Studio song-project surface that exposes them. Disabled controls are visually distinct from enabled controls. | **NOT RUN** |
| I09 | In completed-take Studio, exercise autosave, close/reopen, conflict, failed-save retry, and last-known-good recovery. Exercise explicit Save and Save As in a separate Reference Studio song project. Both preserve immutable source media and pending edit truth. | **NOT RUN** |
| I10 | Resize at all supported sizes and use keyboard-only navigation plus VoiceOver/NVDA/Orca as applicable. No clipping, overlapping labels, low-contrast state, inaccessible hit target, or keyboard trap appears. | **NOT RUN** |
| I11 | Confirm the UI uses WebJam's black, neutral-gray, white, and burnt-orange identity and does not copy Apple artwork, icons, exact layout, assets, or trade dress. | **NOT RUN** |

## J. Export and external-editor proof

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| J01 | On macOS/Linux with secure directory APIs, export the edited package. Require equal-length edited stems, aligned originals, rough mix, markers, instructions, arrangement, source manifests, provenance, and checksums. | **NOT RUN** |
| J02 | On Windows/unsupported runtime, verify the distinct **Export Aligned Originals** path and its explicit exclusion of edits, fades, comps, sections, master processing, and attached lanes. | **NOT RUN** |
| J03 | Change a source, manifest, saved Studio state, or cross-take identity during export. Publication fails closed and no partial folder is called success. | **NOT RUN** |
| J04 | Import the complete package into at least one external editor. Verify equal start, duration, source names/types, markers, Shared Track identity, and audible alignment. | **NOT RUN** |
| J05 | Independently hash original take media before and after editing/export. Every source byte remains unchanged. | **NOT RUN** |

## K. Linux/JACK endpoint and real-Jamulus laboratory

The Linux desktop package is a Join target, not a replacement macOS host.
JACK remains owned by Jamulus. Keep physical endpoint listening distinct from
the automated real-Jamulus/JACK harness.

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| K01 | On Ubuntu 22.04 x64, start JACK with the recorded interface/rate/block settings, launch the exact WebJam/Jamulus candidate, and join the Mac host. | **NOT RUN** |
| K02 | Hear both Mac musician and `WebJam Track` through the Linux guest's Jamulus return; adjust their Jamulus levels independently. | **NOT RUN** |
| K03 | Change JACK block size or restart JACK. WebJam keeps ownership claims conservative and directs native audio repair to Jamulus without inventing connection/audibility success. | **NOT RUN** |
| K04 | Record while the Linux musician plays and reconnects. Final Studio mapping retains the correct participant identity/segments or fails closed. | **NOT RUN** |
| K05 | Run the repository's Linux/JACK real-Jamulus integration against the exact source commit. Record this as automated integration evidence, not physical audibility. | **NOT RUN** |
| K06 | Compare the harness's expected server stem inventory with the physical take without treating filename similarity as identity proof. | **NOT RUN** |

## L. Hardware, interruption, and long-session matrix

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| L01 | Disconnect/reconnect the host physical interface while idle, playing Shared Track, and recording in separate runs. State remains truthful; no direct speaker feedback path is selected silently. | **NOT RUN** |
| L02 | Disconnect/reconnect Guest A's interface during recording. Segment/gap evidence is explicit and identity remains stable or fails closed. | **NOT RUN** |
| L03 | Sleep/wake the host while idle and during an expendable recording. Stale processes/routes cannot authorize a new generation; recoverable media is preserved. | **NOT RUN** |
| L04 | Sleep/wake Guest A, then reconnect. Roster reorder does not relabel another stem as that guest. | **NOT RUN** |
| L05 | Change network interface/address and exercise bounded Jamulus reconnect while Shared Track is stopped, then playing. Route and recording owners remain generation-safe. | **NOT RUN** |
| L06 | Run one continuous five-minute recorded rehearsal with at least two musicians and the Shared Track. Verify stem identity/alignment, Studio playback, export, and clean teardown. | **NOT RUN** |
| L07 | Run one continuous sixty-minute rehearsal with at least two musicians, Shared Track loops/seeks, multiple recordings, Studio reviews, and repeated clean teardown. | **NOT RUN** |
| L08 | During both duration runs, record CPU, memory, handle/thread, disk, xrun/underrun, and dropout summaries at bounded intervals without collecting private names, paths, invitations, or device UIDs. | **NOT RUN** |
| L09 | End/Leave after the sixty-minute session. Only WebJam-owned processes stop; no server, recorder, backing client, route lease, staging journal, or unsaved Studio edit is abandoned. | **NOT RUN** |

## M. Privacy and supportability observation

| ID | Physical action and expected result | Status |
| --- | --- | --- |
| M01 | Create a Support Bundle after successful Shared Track playback/recording. It contains bounded route/source class, count-in, dropout, cleanup, generation, take, and component facts only. | **NOT RUN** |
| M02 | Repeat after malformed source, route loss, recorder failure, transfer wait, and export failure. No source/recording path, invite, meeting link, credential, device UID, private name, authored note, or raw exception appears. | **NOT RUN** |
| M03 | Inspect ordinary logs and public exported metadata for the same exclusions. Musician-facing local UI may show the source filename and intended participant labels only where needed. | **NOT RUN** |

## Release decision summary

| Gate family | Status | Evidence |
| --- | --- | --- |
| Exact v0.23.0 package identity and checksums | **NOT RUN** | None |
| Clean install and platform trust | **NOT RUN** | None |
| Two-/three-machine Jamulus baseline | **NOT RUN** | None |
| Shared Track source/UX | **NOT RUN** | None |
| BlackHole route, isolation, and independent mix | **NOT RUN** | None |
| Guest state without authority | **NOT RUN** | None |
| Record Session lifecycle/count-in | **NOT RUN** | None |
| Authoritative musician/Shared Track stems and alignment | **NOT RUN** | None |
| Local Originals transfer/alignment | **NOT RUN** | None |
| Failure injection and crash recovery | **NOT RUN** | None |
| Studio playback/edit/comp/mix/save/recovery | **NOT RUN** | None |
| Export and external-editor import | **NOT RUN** | None |
| Linux/JACK physical endpoint | **NOT RUN** | None |
| Long-session and hardware interruption | **NOT RUN** | None |
| Packaged accessibility and responsive UX | **NOT RUN** | None |
| Privacy/support bundle inspection | **NOT RUN** | None |
| Signing, notarization, SmartScreen, and Gatekeeper | **NOT RUN** | None |

Release recommendation: **NOT RUN — no v0.23.0 physical release decision is
authorized by this ledger.**
