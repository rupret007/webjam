# WebJam v0.17 two-Mac source-candidate worksheet

> Blank operator record for one exact v0.17.0 candidate. Fill identity fields
> only after the final commit and package exist. Every observation below starts
> as **NOT RUN** and stays that way until a musician performs it on the named
> artifact. Automated, simulated, loopback, container, and headless-Qt results
> do not fill physical or external-editor rows.

## Evidence classes and pre-run decisions

This worksheet supports two distinct decisions:

- A **functional two-Mac pilot** may use exact `ADHOC-TEST-ONLY` branch
  artifacts. It can validate musicians, real interfaces, recording, Studio,
  export, and recovery, but signing/notarization and platform trust remain
  **NOT RUN**.
- **Release promotion** requires exact Developer ID signed/notarized artifacts,
  all required trust rows marked **PASS**, and the other physical rows below
  completed against those same hashes. A functional pilot cannot promote an
  ad-hoc artifact.

Fill every decision before installing either candidate:

| Decision | Record |
| --- | --- |
| Pilot class | _functional ad-hoc / release-trust_ |
| Host musician / instrument / operator | _blank_ |
| Guest musician / instrument / operator | _blank_ |
| Observer, timekeeper, and worksheet owner | _blank_ |
| Mac selected for interface disruption | _blank_ |
| Mac selected for sleep/wake | _blank_ |
| Network topology | _same LAN / routed private network; describe_ |
| Pilot song and fixed form | _blank; recommended Intro / Verse / Chorus / Verse / Chorus / End_ |
| Representative long-take duration | _blank; choose before recording_ |
| External editor and exact version | _blank; name Logic Pro if Logic is the acceptance target_ |
| Evidence directory | _blank_ |
| Intended release channel | _private pilot / later public candidate_ |

## Automated prerequisite evidence

These rows prove source and package prerequisites only. They never satisfy a
physical listening, interface, external-editor, signing, or trust row.

| Evidence | Record |
| --- | --- |
| Source suite command / result / warning count | _blank_ |
| PR CI workflow run and check conclusion | _blank_ |
| Native build artifact class | _blank_ |
| Host architecture artifact / Actions digest | _blank_ |
| Guest architecture artifact / Actions digest | _blank_ |
| Known skipped/manual jobs | _blank_ |

## Candidate identity

| Field | Record |
| --- | --- |
| App version | `0.17.0` |
| Source commit | _blank — final candidate not selected_ |
| Source branch / PR | _blank_ |
| Host artifact filename | _blank_ |
| Host artifact SHA-256 | _blank_ |
| Guest artifact filename | _blank_ |
| Guest artifact SHA-256 | _blank_ |
| Build workflow / run | _blank_ |
| Candidate artifact class | _blank — ADHOC-TEST-ONLY or signed/notarized_ |
| Signing identity | `NOT RUN` |
| Notarization / platform trust | `NOT RUN` |
| Preserved rollback artifact | _blank_ |

Do not begin if the Macs report different app versions, source commits, or
workflow runs. Intel and Apple Silicon artifacts normally have different
filenames and hashes: each Mac's computed hash must match the expected hash
recorded for its own architecture; the two hashes do not need to equal each
other. Preserve the v0.16.3 published package separately and never overwrite
an installed app in place.

## Machines and audio route

| Field | Host Mac | Guest Mac |
| --- | --- | --- |
| Model / architecture | _blank_ | _blank_ |
| macOS version | _blank_ | _blank_ |
| Interface / driver | _blank_ | _blank_ |
| Input channels | _blank_ | _blank_ |
| Headphone/output route | _blank_ | _blank_ |
| Jamulus sample rate / buffer | _blank_ | _blank_ |
| Operator | _blank_ | _blank_ |

## Preflight on each Mac

1. Download the architecture-matching artifact from the one recorded workflow
   run. Record the GitHub Actions artifact digest, then compute and record the
   downloaded DMG or ZIP hash with `shasum -a 256 <filename>`.
2. Confirm at least 20 GB free on the app, Takes, export, and evidence volumes.
   Create the evidence directory named
   `WebJam-v0.17.0-pilot-YYYYMMDD-<source-short-sha>` before launch.
3. Preserve the hash-verified v0.16.3 rollback installer separately. Quit any
   installed WebJam, move only the prior app bundle aside, mount the candidate
   DMG, drag WebJam to `/Applications`, eject the DMG, and launch that installed
   copy. Do not run the app from the mounted image.
4. Verify the candidate reports version `0.17.0`, the recorded source commit,
   and the expected build identity. Confirm the bundled Jamulus and
   JamulusServer version is 3.12.2.
5. Launch operator mode on both Macs:

   ```bash
   "/Applications/WebJam.app/Contents/MacOS/WebJam" --test-night
   ```

6. Connect the named interfaces and headphones. Use headphone-only monitoring
   or otherwise control acoustic feedback. In Jamulus—not WebJam—select and
   record the exact input channels, output, sample rate, and buffer shown in the
   machine table. Keep Webex audio muted while playing if Webex is used.
7. Confirm both clocks show the same date/time, the worksheet owner is ready,
   and each operator knows where to save the Test Night report and sanitized
   support bundle. Press F2 and use **Save Support Bundle** once before the run.

## Fixed fixture and run order

Use one agreed song so observations are comparable rather than improvised:

1. **Audibility calibration:** alternate 10 seconds per musician, then play
   together for 30 seconds. Confirm each person hears the other through the
   named Jamulus/headphone route; a roster alone is not a pass.
2. **Take A — base:** record the fixed 2–3 minute song form. Include clear
   transients at section boundaries and record the expected track count/names.
   Exercise both Shared Jam and one explicitly configured Local Original if
   that path is part of this pilot.
3. **Take B — repeated take:** record the same form with one documented Chorus
   variation. Add it to the matching track, audition without saving a comp,
   then comp only the intended variation.
4. **Arrange:** on a copy/reference plan recorded in the notes, move the named
   Chorus, split and trim one region, add fades, disable/enable one region, and
   Undo/Redo. Record expected before/after section order and frame boundaries.
5. **Cycle:** loop an ordinary musical range of at least four project frames
   that crosses a clear transient. Listen through the named physical output.
   One- through three-frame pathological loops are sample-exact transport tests,
   not de-click acceptance fixtures, and may retain a raw seam.
6. **Export/import:** publish one evidence-rich edited package on the Mac,
   verify checksums independently, and import it into the preselected editor at
   the project sample rate with every stem starting at 0:00.
7. **Long take:** record/play the duration chosen above; do not decide what
   counts as representative after seeing the result.
8. **Disruption:** one variable at a time, disconnect/reconnect the selected
   interface, then perform sleep/wake on the other selected Mac. WebJam must
   drop stale readiness, make no false audibility claim, preserve evidence, and
   require fresh facts before declaring recovery.
9. **Cleanup:** End/Leave, save both Test Night reports and final support
   bundles, and confirm owned WebJam/Jamulus/fabric processes are gone.

## Host, join, and live music

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Host starts its private server before Jamulus opens | `NOT RUN` | _blank_ |
| Invitation is copied once and guest joins without connection details in WebJam UI | `NOT RUN` | _blank_ |
| Both musicians hear each other through the real Jamulus route | `NOT RUN` | _blank_ |
| Webex remains optional and muted while playing, if used | `NOT RUN` | _blank_ |
| Record choice clearly separates Shared Jam from Local Originals | `NOT RUN` | _blank_ |
| Stop/finalize preserves a reviewable multitrack take | `NOT RUN` | _blank_ |

## Studio Arrange, sections, and comping

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Completed take opens in Studio with correct tracks and progressive waveforms | `NOT RUN` | _blank_ |
| Arrange remains usable at 760×600, 1024×768, and 1440×900 | `NOT RUN` | _blank_ |
| Keyboard focus, track/region selection, nudge, trim, Undo, and Redo are usable | `NOT RUN` | _blank_ |
| Move, trim, split, duplicate, disable/delete, and snap edits sound as shown | `NOT RUN` | _blank_ |
| A named Verse/Chorus section moves as one all-track ripple edit and Undo restores it | `NOT RUN` | _blank_ |
| A compatible repeated take can be added and auditioned without changing the saved comp | `NOT RUN` | _blank_ |
| A quick-swipe or keyboard comp selection plays only the intended lane/range | `NOT RUN` | _blank_ |
| Closing and reopening Studio restores the exact saved arrangement and mix | `NOT RUN` | _blank_ |

## Real-output playback and cycle

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Studio output is explicitly chosen without changing Jamulus live output | `NOT RUN` | _blank_ |
| Arrange and comp playback is heard through the named physical output | `NOT RUN` | _blank_ |
| Scrub and seek land on the intended edit boundaries | `NOT RUN` | _blank_ |
| Ordinary cycle (at least four frames) wraps at selected frames without an audible click; 1–3-frame de-click is excluded | `NOT RUN` | _blank_ |
| Playback remains responsive for the preselected long-take duration | `NOT RUN` | _blank_ |

## Evidence-rich export and external editor

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Export creates one complete new package and no partial success folder | `NOT RUN` | _blank_ |
| Edited PCM24 stems, aligned-unity originals, and rough mix have the recorded expected frame count/duration | `NOT RUN` | _blank_ |
| Studio document, source manifests, markers/sections CSV, provenance, and SHA256SUMS are present | `NOT RUN` | _blank_ |
| Independent checksum verification matches every listed output | `NOT RUN` | _blank_ |
| External editor and version | `NOT RUN` | _blank_ |
| Import preserves project rate, track identity, alignment, edits, gaps, and markers | `NOT RUN` | _blank_ |
| Imported playback matches Studio through a named physical output | `NOT RUN` | _blank_ |

## Disruption, recovery, and trust

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Interface disconnect/reconnect recovers truthfully through Jamulus | `NOT RUN` | _blank_ |
| Sleep/wake does not reuse stale readiness and recovery is truthful | `NOT RUN` | _blank_ |
| Interrupted/failed recording remains preserved for review | `NOT RUN` | _blank_ |
| Failed Studio save blocks destructive close until retry succeeds | `NOT RUN` | _blank_ |
| Clean installation passes the platform trust prompts recorded above | `NOT RUN` | _blank_ |
| End/Leave stops only owned processes after recording finalization | `NOT RUN` | _blank_ |

## Evidence capture

Keep one immutable evidence directory for the exact candidate. It must contain:

- this filled worksheet;
- both `WebJam-private-pilot-report.json` Test Night reports;
- a sanitized support ZIP from each Mac before the run and after cleanup;
- expected and locally computed candidate artifact hashes;
- screenshots at 760×600, 1024×768, and 1440×900;
- the completed take directory, export package, editor project/report, and
  import/playback screenshots;
- source-evidence hashes captured before the first Studio edit and after
  comp/export; and
- any failure support bundle captured before changing another variable.

For the selected take, set `PILOT_TAKE_DIR` to that exact directory and capture
the manifest/source hashes before editing, then repeat the same command after
export. The two sorted files must match byte-for-byte:

```bash
PILOT_TAKE_DIR="/exact/path/to/selected-take"
cd "$PILOT_TAKE_DIR"
find . -type f \( -name 'webjam-take.json' -o -name '*.wav' \) -exec shasum -a 256 '{}' + | LC_ALL=C sort > "/exact/evidence/path/source-hashes-before.txt"
```

Verify the exported package from inside its root and save the terminal output:

```bash
shasum -a 256 -c SHA256SUMS.txt | tee "/exact/evidence/path/export-checksums.txt"
```

Do not put invitations, secrets, raw device identifiers, or unsanitized logs in
shared evidence. Use WebJam's support-bundle flow for logs.

## Final disposition

| Field | Record |
| --- | --- |
| Physical two-Mac result | `NOT RUN` |
| Real-output Arrange/comp result | `NOT RUN` |
| Real-output cycle/de-click result | `NOT RUN` |
| External-editor import result | `NOT RUN` |
| Signing/notarization result | `NOT RUN` |
| Candidate decision | _blank — do not promote while required rows are NOT RUN_ |
| Evidence attachment location | _blank_ |

## Acceptance, stop, rerun, and rollback rules

- **PASS** requires the named human observation and its listed evidence.
  **FAIL** means observed behavior contradicted it. **NOT RUN** includes an
  unavailable, blocked, indeterminate, or unchecked result; never infer a pass.
- Stop immediately on candidate identity/hash mismatch, source manifest or WAV
  mutation, recording loss, a false ready/audible claim, partial export shown
  as success, unsafe close, crash, or unexplained owned-process residue.
- On failure, save the support bundle, Test Night report, take, export, and
  worksheet before changing one variable. A source or package change creates a
  new candidate identity and requires a fresh worksheet. A targeted rerun is
  allowed only for an operator/setup mistake with unchanged artifact hashes and
  preserved evidence; product failures require the affected scenario and every
  dependent scenario to be rerun on the new candidate.
- A functional pilot may pass while signing/notarization is **NOT RUN**, but it
  authorizes only further private testing. Release promotion requires the exact
  signed/notarized artifacts and every required trust row to pass.

Rollback procedure:

1. Stop recording and use End/Leave if those actions remain safe.
2. Save both support bundles, Test Night reports, take/export folders, hashes,
   and this worksheet. Quit WebJam and Jamulus and confirm owned processes end.
3. Move only `/Applications/WebJam.app` for the candidate to Trash. Do not
   delete Takes, application-support data, logs, or evidence.
4. Reinstall the preserved hash-verified v0.16.3 artifact without overwriting
   either candidate evidence or the candidate app in place.
5. Record the rollback artifact filename, hash, install result, and whether the
   historical app launches. A rollback launch is recovery evidence, not a pass
   for the v0.17 candidate.
